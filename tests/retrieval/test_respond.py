"""Task D: the response an agent reads (build_response), read() and the byte budget, on tiny hand-made databases.
Plan 2026-11-02 T9 and its addendum (D, E, H2, H3, H6, I5): the hybrid ranking note, the OCR coverage line,
summaries that lead to the hit, marked sections, table headers above a cut, referencing pages and format copies.
Owner 11-03 (「可以，这样砍」, addendum L): no series and no file-name dates in any response."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pytest

from nknu.content.schema import create_schema
from nknu.core.textnorm import tokenize
from nknu.retrieval import respond
from nknu.retrieval.respond import read_response, to_json, today_block
from nknu.retrieval.types import DocHit, SearchResult, Understood
from tests.retrieval.test_search import OLD_SERIES_DDL

TODAY = date(2026, 11, 1)


def build_response(conn, result, *, today, detail="full", **kw):
    """Most of these tests are of the layout that is the full level's since design 2026-11-16 §二 (第四版, 11-17):
    the first places 全文 or 章節, the rest 片段. The other two levels: tests/retrieval/test_detail_1117.py."""
    return respond.build_response(conn, result, today=today, detail=detail, **kw)


@pytest.fixture(autouse=True)
def _the_values_these_tests_were_written_for(monkeypatch):
    """Three full places, a 400-character 片段 (摘要 then) and a 36,000-byte search budget, as before design
    2026-11-16 §二: the logic is the same at the new values, which test_detail_1117.py checks."""
    monkeypatch.setattr(respond, "FULL_PLACES", 3)
    monkeypatch.setattr(respond, "SNIPPET_CHARS", 400)
    monkeypatch.setattr(respond, "RESPONSE_BUDGET_BYTES", 36_000)


BUDGET = 36_000
ALIASES = [["停修", "棄選", "退選"]]
META = {"site": "aca", "name": "教務處", "data_as_of": "2026-10-27", "missing_pages": "3", "missing_files": "43",
        "aliases_json": json.dumps(ALIASES, ensure_ascii=False)}
SOURCES = {"published": "公告頁日期欄", "issued": "發文日期", "revised": "沿革", "form_version": "表單改版",
           "filename": "檔名", "inherited": "所屬公告", "server": "伺服器檔案時間", "period_start": "正文期間",
           "period_end": "正文期間", "event_start": "行事曆", "event_end": "行事曆"}
SENTENCE = "本校學生應於規定期間內完成選課手續。"  # 18 chars
LINE = SENTENCE * 4 + "\n"  # 73 chars

COVERAGE = ("目前只收高師大教務處網站（c.nknu.edu.tw/aca）的公開網頁、公告附件與教務處行事曆（2023 年起），資料截至 2026-10-27；"
            "另有 3 個網頁、43 個附件沒抓到；掃描檔還沒轉成文字，只能依標題找到。其他單位與系所網站尚未收錄。")
# addendum M2 (11-08, deliberate): journals and exams are no longer 「稍微排後面」 but background (NOTE_BACKGROUND)
NOTE_RANKING = ("【系統】結果依關鍵字比對排序；問題沒有指定學期時，日期較近的文件稍微排前面。只含教務處公開資料。若下列原文都不能回答問題，請告訴使用者："
                "目前收錄的資料裡查無相關內容，不要推測。")
# T9, deliberate: 章節 content now carries 〔第 N 節〕 lines (addendum E), which are ours, not the school's text
NOTE_SECTIONS = ("【系統】長文件只給命中的章節和目錄；〔第 N 節〕是程式加的節號標記，N 就是 toc 的 part；"
                 "用 fetch 讀其他章節或全文。")
NOTE_DATES = ("【系統】每個日期後面括號是它的來源。附件的日期是所屬網頁的日期或伺服器檔案時間，"
              "不一定是文件本身的修訂日；文件自己寫的日期請看原文（開頭或沿革）。")
NOTE_NOT_FOUND = "【系統】查無相關資料：目前收錄的教務處資料裡沒有符合的內容，請勿推測。"
NOTE_DOWNGRADED = "【系統】回應太長，後面幾筆只給片段；需要時照各筆 more 用 fetch 讀。"  # addendum E (deliberate)
NO_TEXT = "這份是掃描檔或無法轉成文字的檔案，目前只能依標題找到；內容請開原檔網址查看。"
# plan 2026-11-02 T9: the copy the spec fixes
NOTE_RANKING_HYBRID = ("【系統】結果依關鍵字比對和語意相近程度排序（排序模型只計分、不寫字）；"
                       "問題沒有指定學期時，日期較近的文件稍微排前面。只含教務處公開資料。"
                       "若下列原文都不能回答問題，請換幾個說法再查；仍然沒有，請告訴使用者：目前收錄的資料裡查無相關內容，不要推測。")
COVERAGE_OCR = ("目前只收高師大教務處網站（c.nknu.edu.tw/aca）的公開網頁、公告附件（含壓縮檔裡的文件）、網頁上的圖片與"
                "教務處行事曆（2023 年起），資料截至 2026-10-27；另有 3 個網頁、43 個附件沒抓到；"
                "掃描檔和圖片的文字是程式辨識（OCR）的，可能有錯字；無法轉成文字的檔案只能依標題找到。其他單位與系所網站尚未收錄。")
# addendum M2; minutes named first since 11-08 10:16 (M9); spec 2026-11-16 §四 took out where they stand
NOTE_BACKGROUND = "【系統】background 為 true 的是背景資料（會議紀錄、壓縮檔裡的文件、學報、舊校刊和校訊、歷屆考題）。"
# the copies here list no id, as a build before plan 2026-11-08 T1 wrote them: they keep the notes of then
# (respond.NOTE_*_NO_ID); a build since lists ids and says so (tests/content/test_ids_1108.py)
NOTE_FORMATS = ("【系統】copies 中有 format 的是同一份文件的其他格式：各有自己的網址和格式，沒有自己的日期；"
                "內容以該筆為準。")
NOTE_REFS = ("【系統】referenced_by 是引用此檔的頁面（在正文或附件區連到它的網頁和公告），由新到舊；"
             "只出現在選單的連結不列。")
TABLE_HEAD = "| 系所 | 學費 | 雜費 | 合計 |\n| --- | --- | --- | --- |"
SERIES_WORDS = ("series", "folded", "folded_total", "promoted", "members")  # keys a series answer had (removed 11-03)


class Tiny:
    """A content database file in tmp_path: create_schema, then rows inserted by hand."""

    def __init__(self, path: Path, meta: dict[str, str] | None = None):
        self.path = path
        self.conn = sqlite3.connect(path)
        create_schema(self.conn)
        self.conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", (META if meta is None else meta).items())

    def doc(self, doc_id: str, text: str = "", *, title: str = "", kind_class: str = "notice",
            kind_raw: str = "newsdetail", url: str = "", unit: str = "教務處", grp: str = "註冊組",
            parent_id: str | None = None, has_text: bool | None = None, ay: int | None = None, sem: int | None = None,
            ident: tuple[str, str] | None = None, sections: list[tuple[str, int, int]] | None = None,
            chunks: list[tuple[int, int, int]] | None = None) -> list[int]:
        """ident: (iso date, kind). sections: (heading, start, end), default the whole text as section 0; chunks:
        (sec_id, start, end), default one per section (a blank text gets one chunk 0..0). Returns the chunk ids."""
        has = bool(text.strip()) if has_text is None else has_text
        ident_date, ident_kind = ident or (None, None)
        self.conn.execute(
            "INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, grp, parent_id, has_text, text, chars,"
            " period_ay, period_sem, ident_date, ident_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, kind_class, kind_raw, title or doc_id, url or f"https://c.nknu.edu.tw/aca/{doc_id}", unit, grp,
             parent_id, int(has), text, len(text), ay, sem, ident_date, ident_kind))
        if sections is None:
            sections = [("", 0, len(text))] if text.strip() else []
        self.conn.executemany("INSERT INTO sections VALUES (?, ?, ?, ?, ?)",
                              [(doc_id, i, h, s, e) for i, (h, s, e) in enumerate(sections)])
        if chunks is None:
            chunks = [(i, s, e) for i, (_, s, e) in enumerate(sections)] or [(0, 0, 0)]
        return [self.conn.execute("INSERT INTO chunks(doc_id, sec_id, start, end) VALUES (?, ?, ?, ?)",
                                  (doc_id, *c)).lastrowid for c in chunks]

    def date(self, doc_id: str, kind: str, value: str, raw: str = "", source: str | None = None) -> None:
        self.conn.execute("INSERT INTO dates VALUES (?, ?, ?, ?, ?)",
                          (doc_id, kind, value, raw, source or SOURCES[kind]))

    def old_series(self, series_id: str, members: list[str]) -> None:
        """What a build before 11-03 wrote (members newest first): docs.series_id and the series tables. Nothing reads
        them now (owner 11-03): they stand for the skeleton's database, which still has them."""
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(docs)")}
        if "series_id" not in columns:
            self.conn.executescript(OLD_SERIES_DDL)
        self.conn.execute("INSERT INTO series VALUES (?, ?, 'periodic', ?, ?)",
                          (series_id, f"key-{series_id}", members[0], len(members)))
        for i, m in enumerate(members):
            self.conn.execute("INSERT INTO series_members VALUES (?, ?, ?)", (series_id, m, i))
            self.conn.execute("UPDATE docs SET series_id=? WHERE doc_id=?", (series_id, m))

    def applies(self, doc_id: str, *, unit: str = "教務處", cohort_from: int | None = None,
                cohort_exact: int | None = None, raw: str = "") -> None:
        self.conn.execute("INSERT INTO applies_to VALUES (?, ?, ?, ?, ?)",
                          (doc_id, unit, cohort_from, cohort_exact, raw))

    def ref(self, doc_id: str, ref_id: str, zone: str, context: str = "") -> None:
        """doc_refs: page ref_id links the file doc_id from zone (content | attachments | unknown | nav)."""
        self.conn.execute("INSERT INTO doc_refs VALUES (?, ?, ?, ?)", (doc_id, ref_id, zone, context))


def hit(doc_id: str, chunk_ids=()) -> DocHit:
    return DocHit(doc_id=doc_id, score=1.0, coverage=1.0, bm25=-1.0 if chunk_ids else 0.0, chunk_ids=list(chunk_ids))


def keys_of(obj: object) -> set[str]:
    """Every dict key of a JSON-like object, nested."""
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in keys_of(v)}
    if isinstance(obj, list | tuple):
        return {k for v in obj for k in keys_of(v)}
    return set()


def result(*hits: DocHit, query: str = "停修", terms=(), cohorts=(), aliases=(), tokens=(),
           ranking: str = "keyword") -> SearchResult:
    """tokens: the query's own tokens as search writes them (textnorm.tokenize); ranking as hybrid_search sets it."""
    understood = Understood(tokens=list(tokens), canonical=[], aliases=list(aliases), terms=list(terms),
                            cohorts=list(cohorts))
    return SearchResult(query=query, understood=understood, hits=list(hits), candidates=len(hits), ranking=ranking)


def tops(db: Tiny) -> list[DocHit]:
    """Three short notices that take the three 全文 places, so the next hits are summaries."""
    return [hit(f"top{i}", db.doc(f"top{i}", prose(100), title=f"短公告{i}")) for i in range(3)]


def fee_table(rows: int = 80, repeat_at: int | None = None) -> tuple[str, list[int]]:
    """An opening line, a Markdown table (header row, separator, rows) and a closing line, as to-text writes a table;
    and the offset of each row. repeat_at: the header row written again before that row (a table that repeats it on
    each printed page)."""
    out = ["115學年度學雜費收費標準如下：\n", TABLE_HEAD + "\n"]
    offsets, pos = [], sum(map(len, out))
    for i in range(rows):
        if i == repeat_at:
            out.append(TABLE_HEAD.split("\n")[0] + "\n")
            pos += len(out[-1])
        row = f"| 第{i + 1:02d}學系碩士在職專班 | {16000 + 10 * i:,} | {10000 + i:,} | {26000 + 11 * i:,} |\n"
        offsets.append(pos)
        out.append(row)
        pos += len(row)
    out.append("以上金額單位為新臺幣元。\n")
    return "".join(out), offsets


def prose(n: int) -> str:
    """n characters of plain text: sentences ending in 。, a line break after every fourth."""
    return (LINE * (n // len(LINE) + 1))[:n]


def articles(count: int, size: int) -> tuple[str, list[tuple[str, int, int]]]:
    """count sections of exactly size characters, each 「第N條 規定事項N」 + body + line break."""
    parts, secs, pos = [], [], 0
    for i in range(count):
        heading = f"第{i + 1}條 規定事項{i + 1}"
        body = heading + "\n" + prose(size - len(heading) - 2) + "\n"
        parts.append(body)
        secs.append((heading, pos, pos + len(body)))
        pos += len(body)
    return "".join(parts), secs


def size(resp: dict) -> int:
    return len(to_json(resp).encode())


@pytest.fixture
def db(tmp_path):
    t = Tiny(tmp_path / "aca.db")
    yield t
    t.conn.close()


# ---------------------------------------------------------------- header, not found

def test_today_block():
    # 11-03: the ROC date too, written as the school writes it, so the agent reads 「115年11月3日」 in a notice against
    # today without converting (changed on purpose: the dicts gain roc_date)
    assert today_block(TODAY) == {"date": "2026-11-01", "roc_date": "115年11月1日", "weekday": "週日", "term": "115-1"}
    assert today_block(date(2027, 1, 10)) == {"date": "2027-01-10", "roc_date": "116年1月10日", "weekday": "週日",
                                              "term": "115-1"}  # January: ROC year 116, still semester 115-1
    assert today_block(date(2026, 2, 15))["term"] == "114-2"
    assert today_block(date(2026, 2, 15))["roc_date"] == "115年2月15日"  # semester 114-2 runs in ROC year 115
    assert today_block(date(2026, 7, 31))["term"] == "114-2"
    assert today_block(date(2026, 8, 1)) == {"date": "2026-08-01", "roc_date": "115年8月1日", "weekday": "週六",
                                             "term": "115-1"}


def test_to_json_compact():
    assert to_json({"a": "停修", "b": [1, None]}) == '{"a":"停修","b":[1,null]}'


def test_not_found_shape(db):
    resp = build_response(db.conn, result(query="!!!"), today=TODAY)
    assert resp == {"today": {"date": "2026-11-01", "roc_date": "115年11月1日", "weekday": "週日", "term": "115-1"},
                    "data_as_of": {"教務處": "2026-10-27"}, "coverage": COVERAGE, "query": "!!!", "found": False,
                    "notes": [NOTE_NOT_FOUND]}


def test_found_header_and_notes(db):
    db.doc("dp1", prose(500), title="停修公告")
    resp = build_response(db.conn, result(hit("dp1", [1]), query="115-1 退選", terms=["115-1"], aliases=["退選"]),
                          today=TODAY)
    assert list(resp) == ["today", "data_as_of", "coverage", "query", "understood", "found", "results", "notes"]
    assert resp["found"] is True and resp["coverage"] == COVERAGE and resp["query"] == "115-1 退選"
    assert resp["understood"] == {"terms": ["115-1"], "aliases": ["退選"]}  # empty cohorts left out
    assert resp["notes"] == [NOTE_RANKING, NOTE_DATES]  # no 章節, no copies, no downgrade
    assert resp["results"][0]["rank"] == 1 and resp["results"][0]["id"] == "dp1"


def test_understood_left_out_when_empty(db):
    db.doc("dp1", prose(500))
    assert "understood" not in build_response(db.conn, result(hit("dp1", [1])), today=TODAY)
    resp = build_response(db.conn, result(hit("dp1", [1]), cohorts=[113]), today=TODAY)
    assert resp["understood"] == {"cohorts": [113]}
    tuples = SearchResult(query="停修", understood=Understood((), (), (), ("115-1",), ()), hits=[hit("dp1", [1])],
                          candidates=1)
    assert build_response(db.conn, tuples, today=TODAY)["understood"] == {"terms": ["115-1"]}


def test_missing_doc_is_skipped(db):
    db.doc("dp1", prose(500))
    resp = build_response(db.conn, result(hit("dGONE", [99]), hit("dp1", [1])), today=TODAY)
    assert [r["id"] for r in resp["results"]] == ["dp1"] and resp["results"][0]["rank"] == 1
    assert build_response(db.conn, result(hit("dGONE")), today=TODAY)["found"] is False


# ---------------------------------------------------------------- content: 全文, 章節, 摘要, 無文字

def test_full_vs_section(db):
    short = prose(2000)
    db.doc("dS", short, title="短公告")
    long_text, secs = articles(100, 500)
    cids = db.doc("dL", long_text, title="學則", kind_class="attachment", kind_raw="附件:pdf:有文字层",
                  url="https://c.nknu.edu.tw/aca/uploadfile/files/rules.pdf", sections=secs)
    resp = build_response(db.conn, result(hit("dS", [1]), hit("dL", [cids[37]])), today=TODAY)
    r1, r2 = resp["results"]
    assert r1["content_type"] == "全文" and r1["content"] == short
    assert "toc" not in r1 and "more" not in r1
    assert r2["content_type"] == "章節"
    # the hit's section whole; at 500 characters (under CONTEXT_CHARS) with the whole sections before and after it;
    # T9 (addendum E, deliberate): the block opens with a line naming its first section, parts lists all three
    assert r2["content"] == "〔第 36 節〕\n" + long_text[secs[36][1]:secs[38][2]].strip()
    assert r2["parts"] == ["36", "37", "38"]
    assert 1 <= len(r2["toc"]) <= 60 and {"part": "37", "heading": "第38條 規定事項38"} in r2["toc"]
    assert r2["more"] == '全文共 50000 字；用 fetch 讀其他章節：fetch(id="dL", part="節號")'
    assert NOTE_SECTIONS in resp["notes"]


def test_sections_rank_order_while_they_fit(db):
    text, secs = articles(10, 1000)
    cids = db.doc("dL", text, sections=secs)
    # hit chunks best first: sections 5, 6, 2, 8; 5+6+2 = 3,000 chars fit, 8 does not
    r = build_response(db.conn, result(hit("dL", [cids[5], cids[6], cids[2], cids[8]])), today=TODAY)["results"][0]
    assert r["content_type"] == "章節"
    s = [text[a:b].strip() for _, a, b in secs]
    # T9 (deliberate): each block opens with its 〔第 N 節〕 line, which the length allowance now covers
    assert r["content"].startswith("〔第 2 節〕\n" + s[2]) and s[5] in r["content"] and s[6] in r["content"]
    assert s[8] not in r["content"] and len(r["content"]) <= 3000 + 30
    assert r["content"].index(s[5]) < r["content"].index(s[6])  # shown in document order


def test_section_window_around_best_chunk(db):
    text = prose(20000)
    cids = db.doc("dW", text, sections=[("", 0, 20000)], chunks=[(0, 10001, 10400)])
    r = build_response(db.conn, result(hit("dW", cids)), today=TODAY)["results"][0]
    assert r["content_type"] == "章節"
    # T9 (addendum E, deliberate): the window opens with a line naming its section; it starts inside the section, so
    # the line carries the heading the toc gives (a section without one: its first line, at most 40 characters)
    mark, rest = r["content"].split("\n", 1)
    assert mark == "〔第 0 節：" + prose(40) + "〕" and r["parts"] == ["0"]
    # review 11-01: a part of a section, cut at both ends, says so: 「…」 where it is cut, and how to read it all
    assert rest.startswith("…\n本校") and rest.endswith("。\n…")  # snapped to sentence ends
    inner = rest[2:-2]
    assert len(inner) <= 3000 and text[10001:10400].strip() in inner  # the best chunk sits inside the window
    assert r["more"] == ('第 0 節共 20000 字，這裡只給其中命中的一段（「…」處有截斷）；讀整節：fetch(id="dW", part="0")；'
                         '全文共 20000 字；用 fetch 讀其他章節：fetch(id="dW", part="節號")')


def test_window_stays_inside_a_long_section(db):
    text, secs = articles(10, 1000)
    long_body = "第11條 長條文\n" + prose(6000) + "\n"
    secs.append(("第11條 長條文", len(text), len(text) + len(long_body)))
    text += long_body
    cids = db.doc("dL", text, sections=secs, chunks=[*((i, a, b) for i, (_, a, b) in enumerate(secs[:-1])),
                                                     (10, secs[10][1] + 100, secs[10][1] + 500)])
    r = build_response(db.conn, result(hit("dL", [cids[10]])), today=TODAY)["results"][0]
    # T9 (deliberate): the window starts on its section's heading line, so the 〔第 10 節〕 line names no heading
    assert r["content"].startswith("〔第 10 節〕\n第11條 長條文\n")  # the section's own start
    assert r["content"].endswith("\n…")
    body = r["content"].split("\n", 1)[1]
    assert body[:-2] in text[secs[10][1]:secs[10][2]] and r["more"].startswith(f"第 10 節共 {len(long_body)} 字")
    assert r["parts"] == ["10"]


def test_tiny_hit_section_gets_whole_neighbours(db):
    # review 11-01: 「一、畢業學分」 came alone (35 characters), without the program it belongs to
    parts = ["國文學系碩士班修業規定\n" + prose(800) + "\n", "一、畢業學分\n畢業學分至少修習滿 32 學分。\n",
             "二、修業年限\n" + prose(700) + "\n", "三、學位考試\n" + prose(5000) + "\n", "四、附則\n" + prose(300)]
    secs, pos = [], 0
    for p in parts:
        secs.append((p.split("\n")[0], pos, pos + len(p)))
        pos += len(p)
    text = "".join(parts)
    cids = db.doc("dT", text, sections=secs)
    r = build_response(db.conn, result(hit("dT", [cids[1]])), today=TODAY)["results"][0]
    # previous first, then next; T9 (deliberate): one block, opened by a line naming its first section
    assert r["content_type"] == "章節" and r["content"] == "〔第 0 節〕\n" + text[:secs[2][2]].strip()
    assert r["parts"] == ["0", "1", "2"]
    assert r["more"] == f'全文共 {len(text)} 字；用 fetch 讀其他章節：fetch(id="dT", part="節號")'


def test_tiny_hit_section_between_long_ones_gets_a_window(db):
    parts = [prose(5000) + "\n", "一、畢業學分\n畢業學分至少修習滿 32 學分。\n", prose(5000)]
    secs, pos = [], 0
    for p in parts:
        secs.append((p.split("\n")[0][:40], pos, pos + len(p)))
        pos += len(p)
    text = "".join(parts)
    cids = db.doc("dT", text, sections=secs)
    r = build_response(db.conn, result(hit("dT", [cids[1]])), today=TODAY)["results"][0]
    # T9 (addendum E, deliberate): the window starts inside section 0, which its first line names with the heading;
    # the best section is named where it starts inside the window; parts lists the three sections it reaches
    assert r["content"].startswith("〔第 0 節：" + secs[0][0] + "〕\n…\n") and r["content"].endswith("\n…")
    assert "\n〔第 1 節〕\n一、畢業學分\n畢業學分至少修習滿 32 學分。\n" in r["content"]
    assert len(r["content"]) <= 3004 + 2 * 60 and r["parts"] == ["0", "1", "2"]
    assert r["more"].startswith("這裡只給命中處前後約 3000 字（「…」處有截斷）；全文共")


def test_hit_sections_mark_each_block_and_list_parts(db):
    # addendum E: each block of whole sections opens with a line naming its first section; parts lists every
    # section shown, in order, so the agent can tell them from the toc
    text, secs = articles(10, 1000)
    cids = db.doc("dL", text, sections=secs)
    r = build_response(db.conn, result(hit("dL", [cids[5], cids[6], cids[2], cids[8]])), today=TODAY)["results"][0]
    s = [text[a:b].strip() for _, a, b in secs]
    assert r["content"] == "〔第 2 節〕\n" + s[2] + "\n…\n〔第 5 節〕\n" + text[secs[5][1]:secs[6][2]].strip()
    assert r["parts"] == ["2", "5", "6"]
    # a block whose first line is not its section's heading (an attachment's title block before 第1條) names it
    _, cids2, _ = attachment_with_title_block(db, "dA")
    a = build_response(db.conn, result(hit("dA", [cids2[0]])), today=TODAY)["results"][0]
    assert a["content"].startswith("〔第 0 節：第1條 規定事項1〕\n國立高雄師範大學學生轉系辦法\n")
    assert a["parts"] == ["0", "1"] and "header" not in a  # the content still starts with the title block


def test_summary_for_ranks_4_to_10(db):
    hits = []
    for i in range(6):
        hits.append(hit(f"d{i}", db.doc(f"d{i}", prose(1000), title=f"公告{i}")))
    resp = build_response(db.conn, result(*hits), today=TODAY)
    assert [r["content_type"] for r in resp["results"]] == ["全文"] * 3 + ["片段"] * 3
    s = resp["results"][3]
    # T9 (addendum E, deliberate; was prose(164) and no more): 400 characters from the chunk's start (no query word
    # in it), cut after the last 。 in the window's last quarter (index 382), 「…」 where the text goes on, and more
    # says how to read the rest
    assert s["content"] == prose(383) + "\n…"
    assert s["more"] == '全文共 1000 字；用 fetch 讀全文：fetch(id="d3")' and "part" not in s and "heading" not in s
    assert all("toc" not in r for r in resp["results"])
    assert all("more" not in r for r in resp["results"][:3])  # 全文 has nothing more


def test_summary_from_best_chunk_hard_cut_and_text_start(db):
    hits = tops(db)
    mid = db.doc("dMid", prose(2000), sections=[("", 0, 2000)], chunks=[(0, 0, 100), (0, 730, 1100)])
    plain = db.doc("dPlain", "甲" * 1000)
    db.doc("dStart", prose(2000))
    hits += [hit("dMid", [mid[1], mid[0]]), hit("dPlain", plain), hit("dStart", [])]
    rs = build_response(db.conn, result(*hits), today=TODAY)["results"]
    # T9 (addendum E, deliberate): the best chunk (730..1100, 370 characters) fits whole; the text goes on before and
    # after it, so both ends are marked (was: its first 180 characters, cut after a 。)
    assert rs[3]["content"] == "…\n" + prose(2000)[730:1100] + "\n…"
    assert rs[4]["content"] == "甲" * 400 + "\n…"  # no break in the window's last quarter: a hard cut (was 180)
    assert rs[5]["content"] == prose(383) + "\n…"  # no matched chunk: the text's start
    assert rs[5]["more"] == '全文共 2000 字；用 fetch 讀全文：fetch(id="dStart")'


# ---------------------------------------------------------------- summaries that lead to the hit (addendum E)

def test_summary_part_and_more_on_long_text(db):
    hits = tops(db)
    text, secs = articles(30, 500)  # 15,000 characters: more than one read page
    cids = db.doc("dL", text, title="學則", sections=secs)
    db.doc("dN", text, title="學則（沒有命中段落）", sections=secs)
    hits += [hit("dL", [cids[17]]), hit("dN", [])]
    rs = build_response(db.conn, result(*hits), today=TODAY)["results"]
    s = rs[3]
    assert s["content_type"] == "片段" and s["part"] == "17" and s["heading"] == "第18條 規定事項18"
    assert s["more"] == ('這段在第 17 節（全文共 15000 字）；讀這一節：fetch(id="dL", part="17")；'
                         '看目錄：fetch(id="dL", part="toc")')
    assert s["content"].startswith("…\n第18條 規定事項18\n") and s["content"].endswith("\n…")
    assert read_response(db.conn, "dL", s["part"], today=TODAY)["content"].startswith("第18條 規定事項18\n")
    n = rs[4]  # nothing matched: the text's start, read from the start
    assert n["more"] == '全文共 15000 字；用 fetch 讀：fetch(id="dN")' and "part" not in n and "heading" not in n
    assert n["content"].startswith("第1條 規定事項1\n")


def test_summary_part_with_offset_in_a_long_section(db):
    hits = tops(db)
    text = "第一章 總則\n" + prose(40_000)  # one section of 40,007 characters: read pages it
    a = 7 + 73 * 200  # a line start deep inside it
    secs = [("第一章 總則", 0, len(text))]
    p = db.doc("dP", text, sections=secs, chunks=[(0, a, a + 365)])
    q = db.doc("dQ", text, sections=secs, chunks=[(0, a + 18, a + 383)])  # starts inside that line
    hits += [hit("dP", p), hit("dQ", q)]
    sp, sq = build_response(db.conn, result(*hits), today=TODAY)["results"][3:5]
    assert sp["part"] == f"0:{a}" and sp["heading"] == "第一章 總則"
    assert sp["more"] == (f'這段在第 0 節（全文共 {len(text)} 字）；讀這一節：fetch(id="dP", part="0:{a}")；'
                          '看目錄：fetch(id="dP", part="toc")')
    first = sp["content"].split("\n")[1]  # the summary's first line, after the 「…」 line
    page = read_response(db.conn, "dP", sp["part"], today=TODAY)
    assert page["content"].split("\n")[0] == first and page["next"].startswith("0:")  # opens at the hit, pages on
    # a summary that starts inside a line: the part opens at that line's start
    assert sq["part"] == f"0:{a}"
    line = read_response(db.conn, "dQ", sq["part"], today=TODAY)["content"].split("\n")[0]
    assert line != sq["content"].split("\n")[1] and line.endswith(sq["content"].split("\n")[1])


def test_summary_on_short_text_says_read_whole(db):
    hits = tops(db)
    text, secs = articles(10, 900)  # 9,000 characters: one read page
    cids = db.doc("dS", text, sections=secs)
    tiny = db.doc("dTiny", "停修申請請於期限內至教務處網站辦理。")
    hits += [hit("dS", [cids[6]]), hit("dTiny", tiny)]
    rs = build_response(db.conn, result(*hits), today=TODAY)["results"]
    assert rs[3]["content_type"] == "片段" and "part" not in rs[3] and "heading" not in rs[3]
    assert rs[3]["more"] == '全文共 9000 字；用 fetch 讀全文：fetch(id="dS")'
    # the summary is the whole text: nothing is cut, so there is nothing more to read
    assert rs[4]["content"] == "停修申請請於期限內至教務處網站辦理。" and "more" not in rs[4]


def test_summary_centres_on_query_tokens(db):
    hits = tops(db)
    head = "開頭句：本段先說明選課期間。\n" + prose(465)  # 480 characters without a query word
    chunk = head + "線上停修申請自11月3日起受理。\n" + prose(120) + "停修申請以一科為限。\n" + prose(60)  # 688
    start = 146
    text = prose(start) + chunk + prose(300)
    c = db.doc("dC", text, sections=[("", 0, len(text))], chunks=[(0, start, start + len(chunk))])
    hits.append(hit("dC", c))
    q = result(*hits, query="停修申請", tokens=tokenize("停修申請"))  # 停修 修申 申请, as search writes them
    s = build_response(db.conn, q, today=TODAY)["results"][3]
    assert s["content"].startswith("…\n") and s["content"].endswith("\n…")
    inner = s["content"][2:-2]
    assert "線上停修申請" in inner and "停修申請以一科為限" in inner  # both places the words are, in one window
    assert "開頭句" not in inner and len(inner) <= 400  # not the chunk's start


def test_summary_tokens_fold_like_search(db):
    # search writes its tokens simplified (学分); the school's text is traditional (學分): the text is folded the same
    # way before the tokens are looked for, or the summary would miss them and start at the chunk's start
    hits = tops(db)
    chunk = "開頭句：本段先說明選課期間。\n" + prose(465) + "輔系應修學分數為二十學分。\n" + prose(150)
    text = prose(146) + chunk + prose(300)
    c = db.doc("dC", text, sections=[("", 0, len(text))], chunks=[(0, 146, 146 + len(chunk))])
    hits.append(hit("dC", c))
    assert tokenize("學分") == ["学分"]
    s = build_response(db.conn, result(*hits, query="學分", tokens=tokenize("學分")), today=TODAY)["results"][3]
    assert "輔系應修學分數為二十學分" in s["content"] and "開頭句" not in s["content"]


def test_summary_without_tokens_starts_at_chunk(db):
    # a meaning-only match (the dense path: no query word in the chunk) starts at the chunk's start, as before
    hits = tops(db)
    chunk = "開頭句：本段先說明選課期間。\n" + prose(673)
    text = prose(146) + chunk + prose(300)
    c = db.doc("dC", text, sections=[("", 0, len(text))], chunks=[(0, 146, 146 + len(chunk))])
    hits.append(DocHit(doc_id="dC", score=0.5, coverage=0.0, bm25=0.0, chunk_ids=c))
    s = build_response(db.conn, result(*hits, query="多益", tokens=tokenize("多益"), ranking="hybrid"),
                       today=TODAY)["results"][3]
    assert s["content"].startswith("…\n開頭句：本段先說明選課期間。\n") and s["content"].endswith("\n…")
    assert len(s["content"]) <= 400 + 4


def test_full_places_go_to_results_with_text(db):
    # review 11-01: a scanned file ranked first used up a 全文 place and the notice with the answer got 180 characters
    db.doc("dN", "", title="115-1停修公告.pdf", kind_class="attachment", kind_raw="附件:pdf:无文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/scan.pdf")
    hits = [hit("dN")] + [hit(f"d{i}", db.doc(f"d{i}", prose(1000), title=f"公告{i}")) for i in range(4)]
    rs = build_response(db.conn, result(*hits), today=TODAY)["results"]
    assert [r["content_type"] for r in rs] == ["無文字", "全文", "全文", "全文", "片段"]


def test_budget_gives_up_the_last_full_place_first(db):
    db.doc("dN", "", title="掃描.pdf", kind_class="attachment", kind_raw="附件:pdf:无文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/scan.pdf")
    hits = [hit("dN")]
    for i in range(3):
        text, secs = articles(100, 1000)
        cids = db.doc(f"dL{i}", text, title=f"長文件{i}", sections=secs)
        hits.append(hit(f"dL{i}", [cids[k] for k in range(10, 13)]))
    resp = build_response(db.conn, result(*hits), today=TODAY)
    assert size(resp) <= BUDGET and NOTE_DOWNGRADED in resp["notes"]
    assert [r["content_type"] for r in resp["results"]] == ["無文字", "章節", "章節", "片段"]


def long_hits(db: Tiny, names: list[str]) -> list[DocHit]:
    """Documents of 100 sections of 1,000 characters, each hit on sections 10-12 (a 章節 of 3,000 characters)."""
    out = []
    for name in names:
        text, secs = articles(100, 1000)
        cids = db.doc(name, text, title=f"長文件{name}", sections=secs)
        out.append(hit(name, [cids[k] for k in range(10, 13)]))
    return out


def test_budget_takes_places_back_from_the_last_place(db):
    # H6: a 無文字 result at rank 2 holds no place, so the places are ranks 1, 3 and 4; over budget, the last place
    # (rank 4) gives up its sections first, never rank 3 by its number
    db.doc("dN", "", title="掃描.pdf", kind_class="attachment", kind_raw="附件:pdf:无文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/scan.pdf")
    a, b, c = long_hits(db, ["dA", "dB", "dC"])
    resp = build_response(db.conn, result(a, hit("dN"), b, c), today=TODAY)
    assert size(resp) <= BUDGET and NOTE_DOWNGRADED in resp["notes"]
    assert [r["content_type"] for r in resp["results"]] == ["章節", "無文字", "章節", "片段"]
    given_up = resp["results"][3]
    assert len(given_up["content"]) > 300  # a place given up shows the full summary, not the shrunk one
    # ... with the way to its hit (addendum E: these fields also appear on a 摘要 the budget made)
    assert given_up["part"] == "10" and given_up["heading"] == "第11條 規定事項11"
    assert given_up["more"].startswith('這段在第 10 節（全文共 100000 字）；讀這一節：fetch(id="dC", part="10")')


def fit_hits(db: Tiny) -> list[DocHit]:
    """Three top documents whose 章節 (3,000 characters each) fill most of the budget, then seven 1,000-character
    notices shown as 片段."""
    hits = []
    for i in range(3):
        text, secs = articles(4, 1000)
        cids = db.doc(f"dL{i}", text, title=f"長文件{i}", sections=secs)
        hits.append(hit(f"dL{i}", cids[:3]))
    return hits + [hit(f"dt{i}", db.doc(f"dt{i}", prose(1000), title=f"公告{i}")) for i in range(7)]


def test_fit_makes_片段_rows_from_the_last_up(db, monkeypatch):
    # design 2026-11-16 §二 (第四版): within the budget nothing changes; over it, the last result that is not yet a
    # 片段 row becomes one (its 片段 and the short header), then the one before it (addendum E's shrinking of the tail
    # summaries to 180 characters went with the 摘要)
    hits = fit_hits(db)
    monkeypatch.setattr(respond, "RESPONSE_BUDGET_BYTES", 10**9)
    rs = build_response(db.conn, result(*hits), today=TODAY)["results"]
    assert [r["content_type"] for r in rs] == ["章節"] * 3 + ["片段"] * 7
    assert all("dates" not in r and "copies" not in r for r in rs[3:])  # 片段 rows carry the short header
    assert all(r["more"] == f'全文共 1000 字；用 fetch 讀全文：fetch(id="dt{i}")' for i, r in enumerate(rs[3:]))
    monkeypatch.setattr(respond, "RESPONSE_BUDGET_BYTES", size(build_response(db.conn, result(*hits), today=TODAY)) - 1)
    resp = build_response(db.conn, result(*hits), today=TODAY)
    assert NOTE_DOWNGRADED in resp["notes"]
    assert [r["content_type"] for r in resp["results"]] == ["章節", "章節"] + ["片段"] * 8


def test_hybrid_ranking_note(db):
    c = db.doc("d1", prose(500))
    hybrid = dataclasses.replace(result(hit("d1", c)), ranking="hybrid")
    assert build_response(db.conn, hybrid, today=TODAY)["notes"][0] == NOTE_RANKING_HYBRID
    assert build_response(db.conn, result(hit("d1", c)), today=TODAY)["notes"][0] == NOTE_RANKING


def test_hybrid_note_survives_the_budget(db):
    hits = long_hits(db, [f"dL{i}" for i in range(10)])
    resp = build_response(db.conn, result(*hits, ranking="hybrid"), today=TODAY)
    assert size(resp) <= BUDGET and NOTE_DOWNGRADED in resp["notes"]
    assert resp["notes"][0] == NOTE_RANKING_HYBRID and NOTE_RANKING not in resp["notes"]


def test_coverage_names_ocr_for_a_totext_build(tmp_path):
    ocr = Tiny(tmp_path / "ocr.db", META | {"text_pipeline": "totext"})
    old = Tiny(tmp_path / "old.db", META | {"text_pipeline": ""})
    try:
        for t in (ocr, old):
            t.doc("d1", prose(500))
        assert build_response(ocr.conn, result(hit("d1", [1])), today=TODAY)["coverage"] == COVERAGE_OCR
        assert build_response(ocr.conn, result(query="!!!"), today=TODAY)["coverage"] == COVERAGE_OCR
        # a build without to-text (the skeleton's) keeps the old line, which stays true for it
        assert build_response(old.conn, result(hit("d1", [1])), today=TODAY)["coverage"] == COVERAGE
    finally:
        ocr.conn.close()
        old.conn.close()


def test_no_text_content(db):
    db.doc("dN", "", title="掃描公告.pdf", kind_class="attachment", kind_raw="附件:pdf:无文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/scan.pdf")
    db.doc("dZ", "PK\x03\x04 zip bytes", title="表單.zip", kind_class="attachment", kind_raw="附件:无文字(zip)",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/forms.zip", has_text=False)
    db.doc("dB", "\n\t \n", title="空白頁", has_text=True)  # has_text says yes, the text is blank
    db.doc("dF", "", title="報名表單", kind_class="link", kind_raw="外站:forms.gle", url="https://forms.gle/x")
    resp = build_response(db.conn, result(hit("dN"), hit("dZ"), hit("dB"), hit("dF")), today=TODAY)
    rs = resp["results"]
    assert [r["content_type"] for r in rs] == ["無文字"] * 4
    assert rs[0]["content"] == rs[1]["content"] == rs[2]["content"] == NO_TEXT
    assert rs[3]["content"] == "這是外部網站的連結，內容沒有收錄；請開網址查看。"
    assert rs[0]["title"] == "掃描公告.pdf" and rs[0]["url"].endswith("scan.pdf")
    assert all("toc" not in r for r in rs)


FEW = "附件:pdf:文字很少(多半是扫描+少量字)"


def test_few_words_scan_is_not_full_text(db):
    # a mostly scanned PDF: its few words come after the 無文字 sentence, never as 全文 (review 11-01)
    manual = "學生線上停修操作手冊\n-學生端-\n\n1. 勾選欲停修之課程。"
    a = db.doc("dF", manual, title="[手冊]學生線上停修操作手冊.pdf", kind_class="attachment", kind_raw=FEW,
               url="https://c.nknu.edu.tw/aca/uploadfile/files/manual.pdf", has_text=False)
    junk = "Powered by TCPDF (www.tcpdf.org)\n" * 40
    b = db.doc("dT", junk, title="p27.pdf", kind_class="attachment", kind_raw=FEW,
               url="https://c.nknu.edu.tw/aca/uploadfile/files/p27.pdf", has_text=False)
    rs = build_response(db.conn, result(hit("dF", a), hit("dT", b)), today=TODAY)["results"]
    assert [r["content_type"] for r in rs] == ["無文字", "無文字"]
    assert rs[0]["content"] == NO_TEXT + "（只轉出少量文字：" + manual + "）" and "more" not in rs[0]
    assert rs[1]["content"].startswith(NO_TEXT + "（只轉出少量文字：Powered by TCPDF")
    assert rs[1]["content"].endswith("…）") and len(rs[1]["content"]) < len(NO_TEXT) + 620
    assert rs[1]["more"] == f'轉出的文字共 {len(junk.strip())} 字；用 fetch 讀全部：fetch(id="dT")'
    whole = read_response(db.conn, "dT", "", today=TODAY)
    assert whole["content_type"] == "無文字" and whole["content"] == NO_TEXT + "（只轉出少量文字：" + junk.strip() + "）"


def attachment_with_title_block(db, doc_id: str) -> tuple[str, list[int], list[tuple[str, int, int]]]:
    """An attachment whose text opens with its own title block (name, 沿革 lines) and a long line, then 100 articles."""
    block = "國立高雄師範大學學生轉系辦法\n96.6.13 行政會議修正通過\n111.12.21 教務會議修正通過\n" + "本辦法依學則訂定，" * 20 + "\n"
    body, secs = articles(100, 500)
    text = block + body
    secs = [(h, a + len(block), b + len(block)) for h, a, b in secs]
    secs[0] = (secs[0][0], 0, secs[0][2])
    cids = db.doc(doc_id, text, title="學生轉系辦法.pdf", kind_class="attachment", kind_raw="附件:pdf:有文字层",
                  url=f"https://c.nknu.edu.tw/aca/uploadfile/files/{doc_id}.pdf", sections=secs)
    return text, cids, secs


HEADER = "國立高雄師範大學學生轉系辦法\n96.6.13 行政會議修正通過\n111.12.21 教務會議修正通過"


def test_attachment_header(db):
    _, cids, _ = attachment_with_title_block(db, "dA")
    short = db.doc("dShort", prose(1000), title="短附件.pdf", kind_class="attachment", kind_raw="附件:pdf:有文字层",
                   url="https://c.nknu.edu.tw/aca/uploadfile/files/short.pdf")
    hits = [hit("dA", [cids[37]]), hit("dShort", short)]
    hits += [hit(f"dn{i}", db.doc(f"dn{i}", prose(1000))) for i in range(2)]
    _, cids2, _ = attachment_with_title_block(db, "dA2")
    hits.append(hit("dA2", [cids2[50]]))
    rs = build_response(db.conn, result(*hits), today=TODAY)["results"]
    assert rs[0]["content_type"] == "章節" and rs[0]["header"] == HEADER
    assert list(rs[0])[list(rs[0]).index("content_type") + 1] == "header"  # right before the content
    assert rs[1]["content_type"] == "全文" and "header" not in rs[1]  # the whole text has its start
    assert rs[3]["content_type"] == "片段" and "header" not in rs[3]  # a notice: no header
    assert rs[4]["content_type"] == "片段" and "header" not in rs[4]  # a 片段 is the short form (design §二)
    passage = build_response(db.conn, result(*hits), today=TODAY, detail="passage")["results"]
    assert passage[4]["content_type"] == "段落" and passage[4]["header"] == HEADER
    first = build_response(db.conn, result(hit("dA2", [cids2[0]])), today=TODAY)["results"][0]
    assert first["content_type"] == "章節" and "header" not in first  # the content already starts with it
    assert read_response(db.conn, "dA", "37", today=TODAY)["header"] == HEADER
    assert "header" not in read_response(db.conn, "dA", "", today=TODAY)


def test_attachment_header_cut():
    from nknu.retrieval.respond import attachment_header

    assert attachment_header("甲" * 100 + "\n" + "乙" * 100) == "甲" * 100  # after the last line end within 150
    assert attachment_header("短文件\n第二行") == "短文件\n第二行"  # the whole text when it is short
    assert attachment_header("甲" * 120 + "。" + "乙" * 100) == "甲" * 120 + "。"  # no line end: a sentence end
    assert attachment_header("甲" * 400) == "甲" * 150 + "…"  # neither: a hard cut


def test_content_whitespace_tidied(db):
    db.doc("dT", "第一行  \t\n\n\n\n\t\n第二行\n\n第三行\n\n\n", title="空白很多")
    r = build_response(db.conn, result(hit("dT", [1])), today=TODAY)["results"][0]
    assert r["content"] == "第一行\n\n第二行\n\n第三行"


def test_event_content_identity_and_status(db):
    db.doc("e1", "", title="申辦停修", kind_class="event", kind_raw="行事曆", grp="", has_text=False)
    db.date("e1", "event_start", "2026-11-03", "2026-11-03")
    db.date("e1", "event_end", "2026-11-16", "2026-11-16")
    r = build_response(db.conn, result(hit("e1")), today=TODAY)["results"][0]
    assert r["identity"] == "教務處｜行事曆｜2026-11-03～2026-11-16（行事曆）"
    assert r["status"] == "行事曆寫的日期 2026-11-03～2026-11-16：尚未開始"
    assert r["content_type"] == "全文" and r["content"] == "申辦停修"


# ---------------------------------------------------------------- identity, dates, status, applies_to, parent

def test_identity_line(db):
    db.doc("dp10", prose(300), title="轉系辦法修正公告", ident=("2026-03-02", "published"),
           url="https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=10&gi=1")
    db.date("dp10", "published", "2026-03-02", "2026/3/2 上午 09:00:00")
    db.doc("da20", prose(300), title="轉系辦法1150210.pdf", kind_class="attachment", kind_raw="附件:pdf:有文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/rule.pdf", parent_id="dp10", ident=("2026-03-02", "inherited"))
    db.date("da20", "server", "2026-03-01", "2026-03-01")
    db.date("da20", "inherited", "2026-03-02", "2026/3/2 上午 09:00:00")
    r = build_response(db.conn, result(hit("da20", [2])), today=TODAY)["results"][0]
    assert r["identity"] == "教務處｜註冊組｜附件（PDF）｜所屬公告發布 2026-03-02（所屬公告）"
    # identity kinds first (published, inherited, server). Changed 11-03 on purpose: the build no longer reads a date
    # from the file name, so the 檔名日期 entry this test had is gone (test_identity_card_shows_no_file_name_date)
    assert r["dates"] == [
        {"type": "所屬公告發布", "date": "2026-03-02", "source": "所屬公告", "raw": "2026/3/2 上午 09:00:00"},
        {"type": "伺服器檔案時間", "date": "2026-03-01", "source": "伺服器檔案時間", "raw": "2026-03-01"}]
    assert r["parent"] == {"id": "dp10", "title": "轉系辦法修正公告",
                           "url": "https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=10&gi=1"}
    assert list(r)[:6] == ["rank", "id", "identity", "title", "url", "dates"]


def test_identity_server_date_is_not_published(db):
    db.doc("da30", prose(300), title="表單.pdf", kind_class="attachment", kind_raw="附件:pdf:有文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/form.pdf", parent_id="dp1", ident=("2025-07-09", "server"))
    db.date("da30", "server", "2025-07-09", "2025-07-09")
    r = build_response(db.conn, result(hit("da30", [1])), today=TODAY)["results"][0]
    assert r["identity"] == "教務處｜註冊組｜附件（PDF）｜伺服器檔案時間 2025-07-09（伺服器檔案時間）"
    assert "發布" not in r["identity"] and r["dates"][0]["type"] == "伺服器檔案時間"


def test_identity_without_date_or_group(db):
    db.doc("dp1", prose(300), kind_class="page", kind_raw="page", grp="")
    db.doc("da1", prose(300), kind_class="attachment", kind_raw="附件:office",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/form.docx", grp="課務組")
    rs = build_response(db.conn, result(hit("dp1", [1]), hit("da1", [2])), today=TODAY)["results"]
    assert rs[0]["identity"] == "教務處｜網頁｜無日期"
    assert rs[1]["identity"] == "教務處｜課務組｜附件（DOCX）｜無日期"
    assert "dates" not in rs[0] and "status" not in rs[0] and "parent" not in rs[0]


def test_identity_card_shows_no_file_name_date(db):
    # owner 11-03: file-name dates went with the series. A build before then wrote them (kind filename, source 檔名;
    # the skeleton's database holds 119): neither search nor read shows one, and the identity line is as before
    db.doc("da20", prose(300), title="轉系辦法1150210.pdf", kind_class="attachment", kind_raw="附件:pdf:有文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/rule.pdf", ident=("2026-03-01", "server"))
    db.date("da20", "filename", "2026-02-10", "1150210")
    db.date("da20", "server", "2026-03-01", "2026-03-01")
    want = [{"type": "伺服器檔案時間", "date": "2026-03-01", "source": "伺服器檔案時間", "raw": "2026-03-01"}]
    r = build_response(db.conn, result(hit("da20", [1])), today=TODAY)["results"][0]
    whole = read_response(db.conn, "da20", "", today=TODAY)
    for shown in (r, whole):
        assert shown["dates"] == want
        assert shown["identity"] == "教務處｜註冊組｜附件（PDF）｜伺服器檔案時間 2026-03-01（伺服器檔案時間）"
        assert "檔名" not in to_json({k: v for k, v in shown.items() if k != "title"})  # the title is the school's


@pytest.mark.parametrize(("today", "phase"), [(date(2026, 11, 2), "尚未開始"), (date(2026, 11, 3), "進行中"),
                                              (date(2026, 11, 16), "進行中"), (date(2026, 11, 17), "已結束")])
def test_status_from_period(db, today, phase):
    db.doc("dp1", prose(300), ident=("2026-10-21", "published"))
    db.date("dp1", "published", "2026-10-21")
    db.date("dp1", "period_start", "2026-11-03", "115年11月3日")
    db.date("dp1", "period_end", "2026-11-16", "11月16日")
    r = build_response(db.conn, result(hit("dp1", [1])), today=today)["results"][0]
    assert r["status"] == f"正文寫的期間 2026-11-03～2026-11-16：{phase}"
    assert {"type": "期間開始", "date": "2026-11-03", "source": "正文期間", "raw": "115年11月3日"} in r["dates"]


def test_status_lone_deadline_and_two_periods(db):
    db.doc("dp1", prose(300))
    db.date("dp1", "period_end", "2026-11-16", "11月16日截止")
    db.doc("dp2", prose(300))
    for kind, value in (("period_start", "2026-12-01"), ("period_end", "2026-12-10"),
                        ("period_start", "2026-10-01"), ("period_end", "2026-10-15")):
        db.date("dp2", kind, value)
    db.doc("dp3", prose(300), ident=("2026-10-21", "published"))
    db.date("dp3", "published", "2026-10-21")
    rs = build_response(db.conn, result(hit("dp1", [1]), hit("dp2", [2]), hit("dp3", [3])), today=TODAY)["results"]
    assert rs[0]["status"] == "正文寫的期間 ～2026-11-16：尚未截止"
    assert rs[1]["status"] == ("正文寫的期間 2026-10-01～2026-10-15：已結束；"
                               "正文寫的期間 2026-12-01～2026-12-10：尚未開始")
    assert "status" not in rs[2]  # a publication date is not a period
    late = build_response(db.conn, result(hit("dp1", [1])), today=date(2026, 11, 17))["results"][0]
    assert late["status"] == "正文寫的期間 ～2026-11-16：已結束"


def test_applies_to_cohort_only(db):
    db.doc("dp1", prose(300))
    db.applies("dp1", cohort_from=113, raw="113學年度起入學")
    db.applies("dp1")  # the unit alone says nothing new
    db.applies("dp1", unit="軟體工程與管理學系", cohort_exact=112, raw="112學年度入學")
    r = build_response(db.conn, result(hit("dp1", [1])), today=TODAY)["results"][0]
    assert r["applies_to"] == [{"unit": "教務處", "cohort_from": 113, "raw": "113學年度起入學"},
                               {"unit": "軟體工程與管理學系", "cohort_exact": 112, "raw": "112學年度入學"}]


# ---------------------------------------------------------------- no series (owner 11-03, addendum L)

def old_build(db: Tiny) -> None:
    """Three yearly notices that a build before 11-03 grouped into series s1a2b3c4d5 (newest first), as the skeleton's
    database still holds them."""
    for ay, ident in ((115, "2026-10-21"), (114, "2025-10-20"), (113, "2024-10-21")):
        db.doc(f"d{ay}", prose(300), title=f"{ay}學年度第1學期停修公告", ay=ay, sem=1, ident=(ident, "published"))
        db.date(f"d{ay}", "published", ident)
    db.old_series("s1a2b3c4d5", ["d115", "d114", "d113"])


def test_no_response_carries_a_series_field_or_note(db):
    # owner 11-03: each document is its own result. Even on a database that still has the series tables, no search,
    # read or not-found answer has a series field, a folded list, a series part in an identity line or a series note
    old_build(db)
    found = build_response(db.conn, result(hit("d115", [1]), hit("d114", [2]), hit("d113", [3])), today=TODAY)
    assert [r["id"] for r in found["results"]] == ["d115", "d114", "d113"]
    assert [r["identity"] for r in found["results"]] == [
        f"教務處｜註冊組｜公告｜發布 {day}（公告頁日期欄）" for day in ("2026-10-21", "2025-10-20", "2024-10-21")]
    assert found["notes"] == [NOTE_RANKING, NOTE_DATES]
    answers = [found, read_response(db.conn, "d114", "", today=TODAY), read_response(db.conn, "d114", "toc", today=TODAY),
               build_response(db.conn, result(query="!!!"), today=TODAY),
               read_response(db.conn, "s1a2b3c4d5", "", today=TODAY)]
    for resp in answers:
        assert not keys_of(resp) & set(SERIES_WORDS), resp
        assert "系列" not in to_json(resp) and "收起" not in to_json(resp)


def test_read_of_an_s_id_is_unknown(db):
    # owner 11-03: read no longer lists a series. An id starting with s (a series id of a build before 11-03, here one
    # its tables still hold) gets the not-found answer of any unknown id, word for word
    old_build(db)
    unknown = read_response(db.conn, "dNOPE", "", today=TODAY)
    assert unknown == {"today": today_block(TODAY), "data_as_of": {"教務處": "2026-10-27"}, "found": False,
                       "notes": ["【系統】查無此 id"]}
    for sid in ("s1a2b3c4d5", " s1a2b3c4d5 ", "s0000000000", "s"):
        for part in ("", "toc", "1"):
            assert read_response(db.conn, sid, part, today=TODAY) == unknown, (sid, part)


# copies without ids, as NOTE_FORMATS above: the note of a build before plan 2026-11-08 T1
NOTE_COPIES = "【系統】copies 是同一份文件（標題和內容都相同）在別的網址的副本，只列網址、所屬網頁和日期；內容和該筆相同。"


def test_copies_listed(db):
    # the build keeps one document per copy group and lists the others in meta (review 11-01)
    db.doc("dA", prose(300), title="選課操作說明.pdf", kind_class="attachment", kind_raw="附件:pdf:有文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/news/9/a.pdf")
    entries = [{"url": f"https://c.nknu.edu.tw/aca/uploadfile/news/{i}/a.pdf", "date": f"2025-0{9 - i}-01",
                "date_kind": "inherited", "parent_id": f"dp{i}", "parent_title": f"公告{i}"} for i in range(6)]
    entries.append({"url": "https://c.nknu.edu.tw/aca/uploadfile/albumfiles/1/a.pdf", "date": "2021-09-14",
                    "date_kind": "server"})
    db.conn.execute("INSERT INTO meta VALUES ('copies:dA', ?)", (json.dumps(entries, ensure_ascii=False),))
    resp = build_response(db.conn, result(hit("dA", [1])), today=TODAY)
    r = resp["results"][0]
    assert r["copies"][0] == {"url": entries[0]["url"], "date": "2025-09-01", "date_type": "所屬公告發布",
                              "parent": {"id": "dp0", "title": "公告0"}}
    assert len(r["copies"]) == 5 and r["copies_total"] == 7  # COPIES_MAX (was FOLDED_MAX, shared with the series)
    assert NOTE_COPIES in resp["notes"] and resp["data_as_of"] == {"教務處": "2026-10-27"}
    whole = read_response(db.conn, "dA", "", today=TODAY)
    assert len(whole["copies"]) == 7 and "copies_total" not in whole and NOTE_COPIES in whole["notes"]
    assert whole["copies"][6] == {"url": entries[6]["url"], "date": "2021-09-14", "date_type": "伺服器檔案時間"}
    db.doc("dB", prose(300))
    assert "copies" not in build_response(db.conn, result(hit("dB", [2])), today=TODAY)["results"][0]


def test_format_copies_say_other_formats(db):
    # I5: a format sibling (the doc beside the pdf on one row) has its own url and format and no date of its own;
    # its note says so, and the old note stays for the same file at another address
    a = db.doc("dA", prose(300), title="學生退學申請書", kind_class="attachment", kind_raw="附件:pdf:有文字层",
               url="https://c.nknu.edu.tw/aca/uploadfile/files/quit.pdf")
    b = db.doc("dB", prose(300), title="學生休學申請書", kind_class="attachment", kind_raw="附件:pdf:有文字层",
               url="https://c.nknu.edu.tw/aca/uploadfile/files/rest.pdf")
    both = [{"url": "https://c.nknu.edu.tw/aca/uploadfile/news/1/quit.pdf", "date": "2025-09-01",
             "date_kind": "inherited", "parent_id": "dp1", "parent_title": "公告1"},
            {"url": "https://c.nknu.edu.tw/aca/uploadfile/files/quit.doc", "format": "doc", "parent_id": "dp9",
             "parent_title": "表單下載"}]
    formats = [{"url": "https://c.nknu.edu.tw/aca/uploadfile/files/rest.odt", "format": "odt", "parent_id": "dp9",
                "parent_title": "表單下載"}]
    for doc_id, entries in (("dA", both), ("dB", formats)):
        db.conn.execute("INSERT INTO meta VALUES (?, ?)", (f"copies:{doc_id}", json.dumps(entries, ensure_ascii=False)))
    resp = build_response(db.conn, result(hit("dA", a), hit("dB", b)), today=TODAY)
    assert resp["results"][0]["copies"] == [
        {"url": both[0]["url"], "date": "2025-09-01", "date_type": "所屬公告發布",
         "parent": {"id": "dp1", "title": "公告1"}},
        {"url": both[1]["url"], "format": "DOC", "parent": {"id": "dp9", "title": "表單下載"}}]
    assert NOTE_COPIES in resp["notes"] and NOTE_FORMATS in resp["notes"]
    only = build_response(db.conn, result(hit("dB", b)), today=TODAY)
    assert only["results"][0]["copies"] == [{"url": formats[0]["url"], "format": "ODT",
                                             "parent": {"id": "dp9", "title": "表單下載"}}]
    assert NOTE_FORMATS in only["notes"] and NOTE_COPIES not in only["notes"]
    read = read_response(db.conn, "dB", "", today=TODAY)
    assert NOTE_FORMATS in read["notes"] and NOTE_COPIES not in read["notes"] and read["copies"][0]["format"] == "ODT"


def test_copies_with_ids_are_said_to_share_their_content_not_their_title(db):
    # plan 2026-11-14 T2: the build merges documents of one text whatever their titles (a notice two sites post), so
    # the note of copies that carry ids says the content is the same and the title may differ; the note of a build
    # before plan 2026-11-08 T1 (no ids: one title and text then) stays as it was
    db.doc("dA", prose(300), title="暑假開館公告", url="https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=1&gi=2")
    copy = {"id": "dB", "url": "https://c.nknu.edu.tw/tg/newsdetail.aspx?nid=9", "date": "2026-03-05",
            "date_kind": "published"}
    db.conn.execute("INSERT INTO meta VALUES ('copies:dA', ?)", (json.dumps([copy], ensure_ascii=False),))
    note = ("【系統】copies 是同一份文件（內容相同，標題可能不同）在別的網址的副本，列出各自的 id、網址、所屬網頁和日期；"
            "用 fetch 讀副本的 id 會得到這一筆。內容和該筆相同。")
    assert respond.NOTE_COPIES == note and respond.NOTE_COPIES_NO_ID == NOTE_COPIES
    resp = build_response(db.conn, result(hit("dA", [1])), today=TODAY)
    assert resp["results"][0]["copies"] == [{"id": "dB", "url": copy["url"], "date": "2026-03-05", "date_type": "發布"}]
    assert note in resp["notes"] and NOTE_COPIES not in resp["notes"]
    read = read_response(db.conn, "dA", "", today=TODAY)
    assert note in read["notes"] and NOTE_COPIES not in read["notes"]
    assert not [n for n in (*resp["notes"], *read["notes"]) if "標題和內容都相同" in n]


def test_identity_of_an_archive_member(db):
    # an archive member's address is <archive url>#<its path> (T7a): the label names the member's format and the
    # archive it sits in, not the archive's format alone
    url = "https://c.nknu.edu.tw/aca/uploadfile/files/forms.zip#" + quote("會議紀錄/115-1.pdf")
    c = db.doc("da1m2", prose(300), title="115-1.pdf", kind_class="attachment", kind_raw="附件:pdf:有文字层", url=url)
    r = build_response(db.conn, result(hit("da1m2", c)), today=TODAY)["results"][0]
    assert r["identity"] == "教務處｜註冊組｜附件（ZIP 壓縮檔內的 PDF）｜無日期"


# ---------------------------------------------------------------- byte budget

def test_budget_cap(db):
    # changed 11-03 on purpose: the 40 older versions folded under each result (a series' list) are 40 copies at other
    # addresses now, the one list a result still carries; the budget steps are the same
    hits = []
    for i in range(10):
        text, secs = articles(100, 500)
        cids = db.doc(f"dL{i}", text, title=f"長文件{i}", sections=secs, ident=("2026-10-01", "published"))
        copies = [{"url": f"https://c.nknu.edu.tw/aca/uploadfile/news/{j}/長文件{i}的舊版第{j}次修正公告.pdf",
                   "date": "2020-01-01", "date_kind": "inherited", "parent_id": f"dp{i}o{j}",
                   "parent_title": f"長文件{i}的舊版第{j}次修正公告" * 2} for j in range(40)]
        db.conn.execute("INSERT INTO meta VALUES (?, ?)", (f"copies:dL{i}", json.dumps(copies, ensure_ascii=False)))
        hits.append(hit(f"dL{i}", [cids[k] for k in range(10, 16)]))
    resp = build_response(db.conn, result(*hits, query="長文件"), today=TODAY)
    assert size(resp) <= BUDGET
    assert NOTE_DOWNGRADED in resp["notes"]
    assert resp["results"][0]["content_type"] == "章節" and len(resp["results"][0]["content"]) > 2500
    assert resp["results"][2]["content_type"] == "片段"  # rank 3 is the first to give up its sections
    full_rows = [r for r in resp["results"] if r["content_type"] == "章節"]
    assert full_rows and all(r["copies_total"] == 40 for r in full_rows)
    assert all("copies" not in r for r in resp["results"] if r["content_type"] == "片段")  # 片段 rows list none


def test_budget_drops_results_from_the_end(db):
    # rows that are 片段 rows already, too many of them: results go from the end
    hits = []
    for i in range(40):
        db.doc(f"d{i}", prose(1000), title=f"公告{i}" + "很長的標題" * 120)
        hits.append(hit(f"d{i}", [i + 1]))
    resp = build_response(db.conn, result(*hits), today=TODAY, detail="snippet")
    assert size(resp) <= BUDGET and 1 <= len(resp["results"]) < 40
    assert [r["rank"] for r in resp["results"]] == list(range(1, len(resp["results"]) + 1))
    assert NOTE_DOWNGRADED in resp["notes"]


def test_budget_trims_rank1_content(db):
    db.doc("d1", prose(2900), title="公告")
    for k in range(400):
        db.date("d1", "revised", f"20{k % 25 + 1:02d}-0{k % 9 + 1}-1{k % 10}", f"沿革第{k}條修正")
    resp = build_response(db.conn, result(hit("d1", [1])), today=TODAY)
    r = resp["results"][0]
    assert size(resp) <= BUDGET and NOTE_DOWNGRADED in resp["notes"]
    assert r["content"].endswith("…") and 0 < len(r["content"]) < 2900


def test_budget_trim_of_rank1_sections_drops_parts(db):
    # rank 1's 章節 cut to a prefix may leave out some of the sections it listed: parts goes, toc and more stay
    text, secs = articles(100, 500)
    cids = db.doc("d1", text, title="學則", sections=secs)
    for k in range(400):
        db.date("d1", "revised", f"20{k % 25 + 1:02d}-0{k % 9 + 1}-1{k % 10}", f"沿革第{k}條修正")
    r = build_response(db.conn, result(hit("d1", [cids[k] for k in range(10, 16)])), today=TODAY)["results"][0]
    assert r["content_type"] == "章節" and r["content"].endswith("…") and "parts" not in r
    assert r["more"].endswith('fetch(id="d1", part="節號")')


# ---------------------------------------------------------------- read

def test_read_unknown_id(db):
    for unknown in ("dNOPE", "sNOPE", ""):
        resp = read_response(db.conn, unknown, "", today=TODAY)
        assert resp["found"] is False and resp["notes"] == ["【系統】查無此 id"]
        assert resp["today"]["date"] == "2026-11-01"


def test_read_full_and_long_doc(db):
    db.doc("dS", prose(2000), title="短公告", ident=("2026-10-21", "published"))
    text, secs = articles(100, 500)
    db.doc("dL", text, title="學則", sections=secs)
    short = read_response(db.conn, "dS", "", today=TODAY)
    assert short["found"] is True and short["content_type"] == "全文" and short["content"] == prose(2000)
    assert read_response(db.conn, " dS\n", " ", today=TODAY) == short  # copied with spaces around: still found
    assert short["identity"].startswith("教務處｜註冊組｜公告｜發布 2026-10-21") and "toc" not in short
    long = read_response(db.conn, "dL", "", today=TODAY)
    assert long["content_type"] == "章節" and long["content"].startswith("第1條 規定事項1\n")
    assert len(long["content"]) <= 10_000
    assert long["toc"][:2] == [{"part": "0", "heading": "第1條 規定事項1"}, {"part": "1", "heading": "第2條 規定事項2"}]
    assert len(long["toc"]) == 60
    assert long["more"] == '全文共 50000 字；用 fetch 讀其他章節：fetch(id="dL", part="節號")'
    assert size(long) <= BUDGET


def test_read_toc_and_section(db):
    text, secs = articles(100, 500)
    secs[0] = ("", 0, secs[0][2])  # section 0 without a heading of its own
    db.doc("dL", text, sections=secs)
    toc = read_response(db.conn, "dL", "toc", today=TODAY)
    assert len(toc["toc"]) == 100 and "content" not in toc
    assert toc["toc"][0] == {"part": "0", "heading": "第1條 規定事項1"}  # falls back to the section's first line
    sec = read_response(db.conn, "dL", "37", today=TODAY)
    assert sec["content_type"] == "章節" and sec["content"] == text[secs[37][1]:secs[37][2]].strip()
    assert "next" not in sec


def test_read_section_paging(db):
    text = prose(25_000)
    db.doc("dP", text, sections=[("", 0, 25_000)])
    pages, part = [], "0"
    for _ in range(10):
        resp = read_response(db.conn, "dP", part, today=TODAY)
        assert resp["content_type"] == "章節" and len(resp["content"]) <= 10_000 and size(resp) <= BUDGET
        pages.append(resp["content"])
        if "next" not in resp:
            break
        assert resp["next"].startswith("0:")
        assert resp["more"].endswith(f'fetch(id="dP", part="{resp["next"]}")')
        part = resp["next"]
    assert len(pages) == 3
    assert "".join(pages).replace("\n", "") == text.replace("\n", "")  # nothing lost, nothing repeated


def test_read_bad_part(db):
    text, secs = articles(5, 300)
    db.doc("dL", text, sections=secs)
    for part in ("abc", "99", "1:5000", "-1"):
        resp = read_response(db.conn, "dL", part, today=TODAY)
        assert resp["found"] is True and "content" not in resp, part
        assert resp["notes"][0] == "【系統】沒有這一節；請從 toc 選節號。" and len(resp["toc"]) == 5


def test_read_no_text(db):
    db.doc("dN", "", title="掃描.pdf", kind_class="attachment", kind_raw="附件:pdf:无文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/scan.pdf")
    for part in ("", "0", "toc"):
        resp = read_response(db.conn, "dN", part, today=TODAY)
        assert resp["content_type"] == "無文字" and resp["content"] == NO_TEXT


def test_read_budget_long_toc(db):
    secs, parts, pos = [], [], 0
    for i in range(1000):
        body = f"第{i + 1}條 " + "學生修讀學分與成績考核相關規定事項說明" * 2 + "\n" + prose(60) + "\n"
        heading = body.split("\n")[0][:40]
        parts.append(body)
        secs.append((heading, pos, pos + len(body)))
        pos += len(body)
    db.doc("dHuge", "".join(parts), sections=secs)
    toc = read_response(db.conn, "dHuge", "toc", today=TODAY)
    assert size(toc) <= BUDGET and 0 < len(toc["toc"]) < 1000
    assert any(n.startswith("【系統】目錄只列前") for n in toc["notes"])
    first = read_response(db.conn, "dHuge", "", today=TODAY)
    assert size(first) <= BUDGET and len(first["toc"]) == 60


# ---------------------------------------------------------------- table header above a cut (H2)

def test_table_header_above_a_summary_inside_a_table(db):
    # a 摘要 that starts on a row far down an 80-row table shows the header row once above it, then the cut marker
    text, rows = fee_table()
    c = db.doc("dT", text, title="學雜費收費標準", sections=[("", 0, len(text))], chunks=[(0, rows[40], rows[48] - 1)])
    s = build_response(db.conn, result(*tops(db), hit("dT", c)), today=TODAY)["results"][3]
    assert s["content_type"] == "片段"
    assert s["content"].startswith(TABLE_HEAD + "\n…\n| 第41學系碩士在職專班 | 16,400 | 10,040 | 26,440 |\n")
    assert s["content"].count("| 系所 | 學費 |") == 1 and s["content"].endswith("\n…")


def test_table_header_above_a_window_inside_a_table(db):
    # a 章節 window cut out of a long table section (3,000 of its about 3,500 characters) starts on a row
    text, rows = fee_table()
    c = db.doc("dT", text, title="學雜費收費標準", sections=[("", 0, len(text))], chunks=[(0, rows[60], rows[68] - 1)])
    r = build_response(db.conn, result(hit("dT", c)), today=TODAY)["results"][0]
    mark, body = r["content"].split("\n", 1)
    assert r["content_type"] == "章節" and mark == "〔第 0 節：115學年度學雜費收費標準如下：〕"
    assert body.startswith(TABLE_HEAD + "\n…\n| 第") and r["content"].count("| 系所 | 學費 |") == 1
    assert "| 第61學系碩士在職專班 | 16,600 |" in body


def test_table_header_above_a_read_page_inside_a_table(db):
    text, rows = fee_table()
    db.doc("dT", text, sections=[("", 0, len(text))])
    page = read_response(db.conn, "dT", f"0:{rows[50]}", today=TODAY)
    assert page["content"].startswith(TABLE_HEAD + "\n…\n| 第51學系碩士在職專班 | 16,500 |")
    assert page["content"].count("| 系所 | 學費 |") == 1
    start = read_response(db.conn, "dT", "0", today=TODAY)  # from the section's start the text holds the header
    assert start["content"].startswith("115學年度學雜費收費標準如下：\n" + TABLE_HEAD + "\n")
    assert start["content"].count("| 系所 | 學費 |") == 1
    # the row right after the separator: nothing is left out between the header and the page
    assert read_response(db.conn, "dT", f"0:{rows[0]}", today=TODAY)["content"].startswith(
        TABLE_HEAD + "\n| 第01學系碩士在職專班 |")


def test_table_header_not_added_when_the_text_holds_it(db):
    # a table that repeats its header row (each printed page): a page holding the repeat gets no second one
    text, rows = fee_table(repeat_at=40)
    db.doc("dT", text, sections=[("", 0, len(text))])
    page = read_response(db.conn, "dT", f"0:{rows[35]}", today=TODAY)
    assert page["content"].startswith("| 第36學系碩士在職專班 |") and page["content"].count("| 系所 | 學費 |") == 1


# ---------------------------------------------------------------- pages that reference a file (D, H3)

def referencing_pages(db: Tiny) -> None:
    """Two notices, a form page without a date and a unit's menu page, newest the menu page."""
    db.doc("dp1", prose(200), title="110學年度輔系公告", ident=("2021-01-08", "published"))
    db.doc("dp2", prose(200), title="115學年度停修公告", ident=("2025-10-20", "published"))
    db.doc("dp3", prose(200), title="表單下載", kind_class="page", kind_raw="page")
    for i in range(1, 4):
        db.doc(f"dm{i}", prose(200), title=f"燕巢教務組選單頁{i}", kind_class="page", kind_raw="page",
               ident=(f"2026-0{i}-01", "published"))


def attachment(db: Tiny, doc_id: str, parent_id: str | None = None) -> list[int]:
    return db.doc(doc_id, prose(300), title="教室借用要點", kind_class="attachment", kind_raw="附件:pdf:有文字层",
                  url=f"https://c.nknu.edu.tw/aca/uploadfile/files/{doc_id}.pdf", parent_id=parent_id,
                  ident=("2025-10-20", "inherited") if parent_id else ("2024-06-07", "server"))


def test_read_lists_referencing_pages_newest_first(db):
    # addendum D: read lists 引用此檔的頁面 (title, date, url) from doc_refs; H3: from the text, the attachment area
    # or a button (unknown), newest first (no date last); a menu link is kept in doc_refs but never listed
    referencing_pages(db)
    c = attachment(db, "da1", parent_id="dp2")
    db.ref("da1", "dp1", "content", "教室借用要點")
    db.ref("da1", "dp2", "attachments")
    db.ref("da1", "dp3", "unknown")
    db.ref("da1", "dm3", "nav", "教室借用要點")
    resp = read_response(db.conn, "da1", "", today=TODAY)
    assert resp["referenced_by"] == [
        {"id": "dp2", "title": "115學年度停修公告", "url": "https://c.nknu.edu.tw/aca/dp2", "date": "2025-10-20",
         "date_type": "發布"},
        {"id": "dp1", "title": "110學年度輔系公告", "url": "https://c.nknu.edu.tw/aca/dp1", "date": "2021-01-08",
         "date_type": "發布"},
        {"id": "dp3", "title": "表單下載", "url": "https://c.nknu.edu.tw/aca/dp3"}]
    assert NOTE_REFS in resp["notes"] and resp["parent"]["id"] == "dp2"
    r = build_response(db.conn, result(hit("da1", c)), today=TODAY)["results"][0]  # search: only the parent line
    assert "referenced_by" not in r and r["parent"]["id"] == "dp2" and NOTE_REFS not in r


def test_read_menu_only_attachment_lists_no_page(db):
    referencing_pages(db)
    attachment(db, "da2")
    db.ref("da2", "dm1", "nav")
    db.ref("da2", "dm2", "nav")
    resp = read_response(db.conn, "da2", "", today=TODAY)
    assert resp["found"] is True and "referenced_by" not in resp and NOTE_REFS not in resp["notes"]


def test_read_notice_body_and_menus_lists_only_that_notice(db):
    referencing_pages(db)
    attachment(db, "da3", parent_id="dp1")
    db.ref("da3", "dp1", "content")
    for i in range(1, 4):
        db.ref("da3", f"dm{i}", "nav")
    assert [p["id"] for p in read_response(db.conn, "da3", "", today=TODAY)["referenced_by"]] == ["dp1"]


def test_read_without_doc_refs(db):
    # a database built before addendum B (the skeleton's) has no doc_refs: read works and lists nothing
    attachment(db, "da1")
    db.conn.execute("DROP TABLE doc_refs")
    resp = read_response(db.conn, "da1", "", today=TODAY)
    assert resp["found"] is True and resp["content_type"] == "全文" and "referenced_by" not in resp


def test_read_budget_cuts_referencing_pages_last(db):
    # a file linked from very many notices: the page and the toc shrink first, then the list, whose total stays
    long_title = "關於本校學生申請教室借用及場地使用相關規定之公告" * 3
    for i in range(300):
        db.doc(f"dp{i}", prose(100), title=f"{long_title}{i}", ident=(f"2025-{i % 12 + 1:02d}-01", "published"))
    db.doc("dA", prose(9000), title="教室借用要點", kind_class="attachment", kind_raw="附件:pdf:有文字层",
           url="https://c.nknu.edu.tw/aca/uploadfile/files/room.pdf")
    for i in range(300):
        db.ref("dA", f"dp{i}", "content")
    resp = read_response(db.conn, "dA", "", today=TODAY)
    assert size(resp) <= BUDGET and resp["referenced_by_total"] == 300
    assert 0 < len(resp["referenced_by"]) < 300 and resp["referenced_by"][0]["date"] == "2025-12-01"  # newest kept
    assert len(resp["content"]) > 250 and resp["next"].startswith("0:")  # the page shrank to its floor, not away
