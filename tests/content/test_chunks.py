from itertools import pairwise

from nknu.content import chunks as chunks_module
from nknu.content import numbering
from nknu.content.chunks import (
    MAX_CHARS,
    SECTION_MAX,
    TARGET_CHARS,
    Chunk,
    Section,
    make_chunks,
    split_sections,
    table_header,
)
from nknu.retrieval import respond

REG = ("國立高雄師範大學學生轉系辦法\n96.6.13 教務會議通過\n第一條 本辦法依大學法訂定。\n第二條 學生轉系以一次為限。\n"
       "一、申請時間：每學年第二學期。\n二、申請資格：修業滿一學年。\nQ1：可以轉兩次嗎？\n答：不可以。\n問：何時公告？\n答：五月。")


def _check(text, sections, chunks):
    """Chunks stay inside their section, never exceed MAX_CHARS, keep order and miss no visible character."""
    by_id = {s.sec_id: s for s in sections}
    assert all(c.end - c.start <= MAX_CHARS for c in chunks)
    assert all(by_id[c.sec_id].start <= c.start < c.end <= by_id[c.sec_id].end for c in chunks)
    assert all(a.end <= b.start for a, b in pairwise(chunks))
    covered = {i for c in chunks for i in range(c.start, c.end)}
    assert all(i in covered for i, ch in enumerate(text) if not ch.isspace())


def test_constants():
    assert (TARGET_CHARS, MAX_CHARS) == (400, 800)
    assert SECTION_MAX == respond.FULL_CAP_CHARS == 3_000  # a longer section cannot be shown whole


def test_sections_cover_text():
    secs = split_sections(REG, max_chars=0)  # T6: every mark, as before
    assert [s.heading for s in secs] == [
        "國立高雄師範大學學生轉系辦法", "第一條 本辦法依大學法訂定。", "第二條 學生轉系以一次為限。",
        "一、申請時間：每學年第二學期。", "二、申請資格：修業滿一學年。", "Q1：可以轉兩次嗎？", "問：何時公告？"]
    assert [s.sec_id for s in secs] == list(range(len(secs)))
    assert (secs[0].start, secs[-1].end) == (0, len(REG))
    assert all(a.end == b.start for a, b in pairwise(secs))
    assert REG[secs[1].start:secs[1].end] == "第一條 本辦法依大學法訂定。\n"
    assert REG[secs[0].start:secs[0].end] == "國立高雄師範大學學生轉系辦法\n96.6.13 教務會議通過\n"  # text before the first heading


def test_heading_patterns():
    # T6: （一） is a heading now (the 目 level of Taiwan's law numbering)
    text = ("前言\n第一章 總則\n（一）細目\n第1學期不是標題\n依第三條辦理\n壹、目的\n第 3 節 細則\n"
            "QR code 不是標題\n問題不是標題\nQ. 是標題")
    assert [s.heading for s in split_sections(text, max_chars=0)] == [
        "前言", "第一章 總則", "（一）細目", "壹、目的", "第 3 節 細則", "Q. 是標題"]


def test_contents_lines_are_not_headings():
    # review 11-01: each line of a table of contents became a one-line section, and a hit there showed only that line
    text = ("招生簡章\n目錄\n壹、報考資格 ........................ 1\n貳、修業年限 ...................... 3\n"
            "參、報名方式……………………………5\n肆、面試 ··········· 7\n第一章 總則．．．．．．12\n"
            "壹、報考資格\n大學畢業。\n貳、修業年限\n二至四年。\n一、自傳(含申請動機、志趣...)。\n二、讀書計畫")
    assert [s.heading for s in split_sections(text, max_chars=0)] == [
        "招生簡章", "壹、報考資格", "貳、修業年限", "一、自傳(含申請動機、志趣...)。", "二、讀書計畫"]


def test_heading_cut_to_40_chars():
    line = "第一條 " + "字" * 60
    assert split_sections(line + "\n內文")[0].heading == line[:40]


def test_text_starting_with_heading():
    text = "\n第一條 總則\n內容\n第二條 附則"
    assert [(s.sec_id, s.heading, s.start, s.end) for s in split_sections(text, max_chars=0)] == [
        (0, "第一條 總則", 0, text.index("第二條")), (1, "第二條 附則", text.index("第二條"), len(text))]


def test_sections_blank_and_plain():
    assert split_sections("") == []
    assert split_sections(" \n\t") == []
    text = "  沒有標題的一段文字\n第二行"
    assert split_sections(text) == [Section(0, "沒有標題的一段文字", 0, len(text))]


def test_chunks_within_max():
    text = ("第一條 " + "甲" * 2000 + "\n\n"  # one line, no punctuation: hard cuts
            + "第二條 " + "".join(f"第{i}句話說明規定。" for i in range(300)) + "\n"  # one long line: sentence ends
            + "第三條\n" + "\n".join("短行" * 10 for _ in range(100)) + "\n\n"  # one paragraph of many short lines
            + "第四條 短")
    secs = split_sections(text)
    chunks = make_chunks("d1", text, secs)
    _check(text, secs, chunks)
    assert {c.doc_id for c in chunks} == {"d1"}
    assert {c.sec_id for c in chunks} == {0, 1, 2, 3}
    sentences = [c for c in chunks if c.sec_id == 1]
    assert len(sentences) > 1 and all(text[c.end - 1] == "。" for c in sentences)  # cut at sentence ends
    assert all(TARGET_CHARS // 2 <= c.end - c.start <= TARGET_CHARS for c in sentences[:-1])


def test_small_paragraphs_packed():
    text = "\n\n".join(["第一段" * 20, "第二段" * 20, "第三段" * 20])
    assert make_chunks("d1", text, split_sections(text)) == [Chunk("d1", 0, 0, len(text))]


def test_section_end_crumb_joins_the_chunk_before():
    # review 11-01: a one-character tail ('備') stayed a chunk of its own, won bm25 and became the whole summary
    for n in (395, 398, 420):
        text = "一、說明\n" + "甲" * n + "\n\n備"
        assert [(c.start, c.end) for c in make_chunks("d1", text, split_sections(text))] == [(0, len(text))], n
    # a crumb before a paragraph too long to join it goes back as well
    text = "第一條\n" + "甲" * 398 + "\n\n乙乙\n\n" + "丙" * MAX_CHARS
    assert [(c.start, c.end) for c in make_chunks("d1", text, split_sections(text))] == [(0, 406), (408, 1208)]


def test_paragraphs_near_target_stay_apart():
    text = "\n\n".join("段" * 300 for _ in range(4))
    chunks = make_chunks("d1", text, split_sections(text))
    assert [(c.end - c.start) for c in chunks] == [300] * 4


def test_chunks_never_cross_sections():
    text = "第一條 短\n第二條 也短"
    secs = split_sections(text, max_chars=0)
    assert [(c.sec_id, text[c.start:c.end]) for c in make_chunks("d1", text, secs)] == [
        (0, "第一條 短"), (1, "第二條 也短")]


def test_no_chunks_for_blank_text():
    assert make_chunks("d1", "", split_sections("")) == []


# ---------------------------------------------------------------- T6: refined sections, table headers

def test_short_document_is_one_section():
    assert split_sections("第一條 短\n第二條 也短") == [Section(0, "第一條 短", 0, len("第一條 短\n第二條 也短"))]


TWO_LEVELS = ("一、甲\n" + "子" * 1600 + "\n二、乙\n（一）丙\n" + "丑" * 1600 + "\n（二）丁\n" + "寅" * 1600)


def test_refine_splits_only_long_sections():
    assert [s.heading for s in split_sections(TWO_LEVELS)] == ["一、甲", "二、乙 （一）丙", "（二）丁"]


def test_no_section_is_only_its_heading():  # 「二、乙」 directly above （一）: it joins its first child
    assert all("\n" in TWO_LEVELS[s.start:s.end].strip() for s in split_sections(TWO_LEVELS))
    assert [s.heading for s in split_sections(TWO_LEVELS, max_chars=0)][:3] == ["一、甲", "二、乙", "（一）丙"]


def test_handbook_section_is_split_at_its_subitems():
    body = "".join(f"（{c}）主題\n" + "說" * 980 + "\n" for c in "一二三四五六七八九十")
    secs = split_sections("一、前言\n" + "序" * 400 + "\n二、註冊須知\n" + body)
    assert len(secs) == 11 and all(s.end - s.start <= SECTION_MAX for s in secs)
    assert secs[1].heading == "二、註冊須知 （一）主題"


def test_refined_sections_cover_text():
    text = "\n\n## 第一章 總則\n" + "說明" * 900 + "\n第一條 範圍\n" + "甲" * 2000 + "\n第二條 對象\n" + "乙" * 2000
    secs = split_sections(text)
    assert [s.heading for s in secs] == ["第一章 總則", "第一條 範圍", "第二條 對象"]  # leading blanks: the first
    assert (secs[0].start, secs[-1].end) == (0, len(text)) and all(a.end == b.start for a, b in pairwise(secs))
    assert [s.sec_id for s in secs] == [0, 1, 2]
    chunks = make_chunks("d1", text, secs)
    _check(text, secs, chunks)


def test_long_preamble_keeps_its_own_heading():
    # a part before the first cut of TARGET_CHARS // 2 characters or more is a section of its own
    text = "招生簡章\n" + "序" * 300 + "\n一、甲\n" + "子" * 1500 + "\n二、乙\n" + "丑" * 1500
    assert [s.heading for s in split_sections(text)] == ["招生簡章", "一、甲", "二、乙"]
    text = "招生簡章\n" + "序" * 100 + "\n一、甲\n" + "子" * 1500 + "\n二、乙\n" + "丑" * 1500
    assert [s.heading for s in split_sections(text)] == ["招生簡章 一、甲", "二、乙"]


def test_a_long_heading_leaves_room_for_its_first_childs():
    # review 11-02: f"{heading} {child}"[:40] dropped the child's heading after a long first line (a document title)
    title = "一一四學年度" + "招生簡章" * 10
    text = title + "\n一、報名資格\n" + "子" * 1600 + "\n二、報名方式\n" + "丑" * 1600
    assert [s.heading for s in split_sections(text)] == [f"{title[:33]} 一、報名資格", "二、報名方式"]
    child = "一、" + "報名資格及應繳證件" * 4  # a long child heading still leaves the first half to the title
    text = title + f"\n{child}\n" + "子" * 1600 + "\n二、報名方式\n" + "丑" * 1600
    assert split_sections(text)[0].heading == f"{title[:20]} {child}"[:40]
    text = "招生簡章" * 6 + "\n一、報名資格\n" + "子" * 1600 + "\n二、報名方式\n" + "丑" * 1600  # both fit: none is cut
    assert split_sections(text)[0].heading == "招生簡章" * 6 + " 一、報名資格"


def test_long_section_without_deeper_marks_stays_whole():
    text = "第一條 " + "甲" * 4000
    assert split_sections(text) == [Section(0, "第一條 " + "甲" * 36, 0, len(text))]


def test_table_header_span():
    text = "說明\n| 項目 | 金額 |\n| --- | --- |\n| 學費 | 1 |\n| 雜費 | 2 |"
    head = (text.index("| 項目"), text.index("| --- |") + len("| --- | --- |"))
    assert table_header(text, text.index("| 雜費")) == head
    assert table_header(text, text.index("| 項目")) is None and table_header(text, 0) is None


def test_table_header_without_or_on_its_separator():
    # Docling's compact tables write the separator | - | -: | (T3a's compact_tables=True)
    text = "| 項目 | 金額 |\n| - | -: |\n| 學費 | 1 |\n| 雜費 | 2 |"
    assert table_header(text, text.index("| 雜費")) == (0, text.index("| 學費") - 1)
    assert table_header(text, text.index("| - |")) == (0, text.index("\n"))  # a chunk on the separator: the header row
    text = "| 姓名 | 系所 |\n| 王O明 | 國文系 |\n| 林O華 | 英語系 |"  # no separator: the first row only
    assert table_header(text, text.index("| 林O華")) == (0, text.index("\n"))
    text = "| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |\n\n| 丙 | 丁 |\n| 3 | 4 |"  # a blank line ends a table
    assert table_header(text, text.index("| 3 |")) == (text.index("| 丙"), text.index("| 3 |") - 1)


def test_chunks_carry_table_headers():
    rows = "\n".join(f"| 第{i}項 | {i * 100} 元 | 說明文字說明文字 |" for i in range(80))
    text = "| 項目 | 金額 | 備註 |\n| --- | --- | --- |\n" + rows
    chunks = make_chunks("d1", text, split_sections(text))
    assert len(chunks) > 2 and chunks[0].head_start is None
    assert all((c.head_start, c.head_end) == (0, text.index(rows) - 1) for c in chunks[1:])


def test_chunks_outside_tables_have_no_header():
    text = "第一段" * 200 + "\n\n" + "第二段" * 200
    assert all((c.head_start, c.head_end) == (None, None) for c in make_chunks("d1", text, split_sections(text)))


def test_old_patterns_kept_for_the_attribution_scripts():
    # research/content_explain_2026-11-02/chunking_index/ imports _HEADING and _CONTENTS
    assert chunks_module._CONTENTS is numbering.CONTENTS_LINE
    assert chunks_module._HEADING.match("第一條 總則") and not chunks_module._HEADING.match("（一）細目")
    assert chunks_module.HEADING_CHARS == 40


def test_chunk_headers_match_table_header():
    # make_chunks cuts table_header's walk back short at the last chunk's row; the result must not change
    def table(name, n, cell):
        rows = "\n".join(f"| {name}{i} | {cell * (i % 7 + 1)} |" for i in range(n))
        return f"| {name} | 金額 |\n| --- | --- |\n{rows}"
    parts = [table("甲", 120, "說明"), "一段說明文字。" * 80, table("乙", 3, "短"), table("丙", 90, "長" * 300)]
    text = "\n\n".join(parts)
    chunks = make_chunks("d1", text, split_sections(text))
    assert sum(c.head_start is not None for c in chunks) > 10  # long rows (丙) make chunks start mid-row too
    assert all((c.head_start, c.head_end) == (table_header(text, c.start) or (None, None)) for c in chunks)
