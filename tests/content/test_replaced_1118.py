"""11-18: 特教中心 keeps sections under one host (resource, ebd-asd, the root), each numbering its notices from 1.
An id was one per host and number, so once the other sections were crawled 197 notices of the library in use came out
of a build as another section's notice under their own id, and the departure guard saw only the one that also shrank.
Two things follow: a notice in a folder is that folder's (ids.NOTICE_FOLDERS), and the guard says "replaced" for a
document both libraries hold that is another thing now, "renumbered" for one whose address moved to another id
(spec 2026-11-17-ingest-guard-design §二, 11-18). The sentences are made up; no model loads."""
from __future__ import annotations

import json
import sqlite3

import pytest

from nknu.content import build, ids

KSPED = "https://ksped.nknu.edu.tw/"
OLD_TEXT = "職場人際的幾個練習：先聽完對方的話，再說自己的想法。" * 8
NEW_TEXT = "到校行動列車本週到三所學校，研習時間與教室如下表所列。" * 8


# ---------------------------------------------------------------- the key

def test_a_notice_in_a_folder_is_that_folders():
    resource = ids.location_key(KSPED + "resource/newsdetail.aspx?nid=172")
    other = ids.location_key(KSPED + "ebd-asd/newsdetail.aspx?nid=172")
    root = ids.location_key(KSPED + "newsdetail.aspx?nid=172")
    assert (resource, other) == ("cnknu:ksped.nknu.edu.tw/resource:172", "cnknu:ksped.nknu.edu.tw/ebd-asd:172")
    assert root == "cnknu:ksped.nknu.edu.tw:172" and len({resource, other, root}) == 3  # the root keeps the host's key
    spellings = [KSPED + "Resource/NewsDetail.aspx?Nid=172&gi=2", KSPED + "resource/newsview.aspx?nid=172",
                 "http://ksped.nknu.edu.tw/resource/newsdetail.aspx?nid=172&amp;gi=4"]
    assert {ids.location_key(u) for u in spellings} == {resource}  # one notice whatever its spelling, as before


def test_the_keys_of_before_do_not_move():
    assert ids.location_key("https://tecs.nknu.edu.tw/newsdetail.aspx?nid=578") == "cnknu:tecs.nknu.edu.tw:578"
    assert ids.location_key("https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=578&gi=2") == "cnknu:c.nknu.edu.tw/aca:578"
    assert ids.location_key(KSPED + "resource/page.aspx?sn=3") == "url:ksped.nknu.edu.tw/resource/page.aspx?sn=3"


def test_the_switch_gives_the_key_of_before(monkeypatch):
    monkeypatch.setattr(ids, "NOTICE_FOLDERS", False)
    keys = {ids.location_key(KSPED + f"{folder}newsdetail.aspx?nid=172") for folder in ("resource/", "ebd-asd/", "")}
    assert keys == {"cnknu:ksped.nknu.edu.tw:172"}
    assert ids.location_key("https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=578") == "cnknu:c.nknu.edu.tw/aca:578"


# ---------------------------------------------------------------- the guard

def library(path, docs: list[tuple], aliases: tuple[str, ...] = ()):
    """A library of the columns the guard reads; docs: (doc_id, kind_class, title, url, text)."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE docs (doc_id TEXT, kind_class TEXT, site TEXT, title TEXT, url TEXT, text TEXT)")
        conn.execute("CREATE TABLE doc_alias (alias TEXT)")
        conn.execute("CREATE TABLE sites (site TEXT)")
        conn.execute("INSERT INTO sites VALUES ('ksped.nknu.edu.tw')")
        conn.executemany("INSERT INTO docs VALUES (?, ?, 'ksped.nknu.edu.tw', ?, ?, ?)", docs)
        conn.executemany("INSERT INTO doc_alias VALUES (?)", [(a,) for a in aliases])
    return path


def notice(i: int, folder: str, title: str, text: str, doc_id: str | None = None) -> tuple:
    return (doc_id or f"d{i}", "notice", f"{title}（{i}）", f"{KSPED}{folder}newsdetail.aspx?nid={i}", f"第 {i} 則。{text}")


def test_another_notice_under_the_same_id_is_replaced_and_holds_the_swap(tmp_path):
    old = library(tmp_path / "old.db", [notice(i, "resource/", "人際攻略", OLD_TEXT) for i in range(20)])
    new = library(tmp_path / "new.db", [notice(i, "ebd-asd/", "行動列車", NEW_TEXT) for i in range(20)])
    left = build._departures(old, new, {}, [])
    assert left["left"] == 0 and left["unexplained"] == {"replaced": 20} and left["unexplained_total"] == 20
    assert left["by_site"] == {"ksped.nknu.edu.tw": 20} and build._too_many_left(left)
    item = left["items"][0]
    assert item["why"] == "replaced" and "resource/newsdetail.aspx" in item["url"] and "行動列車" in item["detail"]
    assert "ebd-asd/newsdetail.aspx" in item["detail"] and left["words"]["replaced"] == "同一个编号换成了另一份"


@pytest.mark.parametrize("folder, title, text", [
    ("resource/", "行動列車", NEW_TEXT),  # the school rewrote the notice where it is: its address stays
    ("ebd-asd/", "人際攻略", NEW_TEXT),  # its title stays
    ("ebd-asd/", "行動列車", OLD_TEXT),  # shown at another address and re-titled, the words are the same
])
def test_one_thing_changed_is_no_replacement(tmp_path, folder, title, text):
    old = library(tmp_path / "old.db", [notice(i, "resource/", "人際攻略", OLD_TEXT) for i in range(20)])
    new = library(tmp_path / "new.db", [notice(i, folder, title, text) for i in range(20)])
    left = build._departures(old, new, {}, [])
    assert left["unexplained"] == {} and left["left"] == 0 and not build._too_many_left(left)


def test_an_address_that_moved_to_another_id_is_renumbered(tmp_path):
    # the folder became part of the key: the resource notices have new ids, and the root's notices took the old ones
    old = library(tmp_path / "old.db", [notice(i, "resource/", "人際攻略", OLD_TEXT) for i in range(20)])
    moved = [notice(i, "resource/", "人際攻略", OLD_TEXT, doc_id=f"n{i}") for i in range(20)]
    root = [notice(i, "", "行動列車", NEW_TEXT) for i in range(8)]  # d0..d7 are the root's notices now
    new = library(tmp_path / "new.db", moved + root)
    left = build._departures(old, new, {}, [])
    assert left["left"] == 12 and left["explained"] == {"renumbered": 12} and left["unexplained"] == {}
    assert left["words"]["renumbered"] == "编号改了、网址还在库里" and not build._too_many_left(left)


def test_a_merge_is_still_a_merge_and_a_scheme_is_no_other_address(tmp_path):
    old = library(tmp_path / "old.db", [notice(1, "resource/", "人際攻略", OLD_TEXT),
                                         ("d2", "page", "簡介", "http://ksped.nknu.edu.tw/resource/page.aspx?sn=1", OLD_TEXT)])
    new = library(tmp_path / "new.db", [("d2", "page", "中心簡介", KSPED + "resource/page.aspx?sn=1", NEW_TEXT)],
                  aliases=("d1",))
    left = build._departures(old, new, {}, [])
    assert left["explained"] == {"merged": 1} and left["unexplained"] == {}  # d2: the same address, https now


# ---------------------------------------------------------------- a picture kept at one size

WIX = "https://static.wixstatic.com/media/"


def picture(doc_id: str, name: str, size: str) -> tuple:
    return (doc_id, "file", f"{name}.webp", f"{WIX}{name}~mv2.png/v1/fill/{size},q_95,enc_avif/{name}~mv2.webp",
            "海報：講座時間與地點，歡迎報名參加。")


def test_a_size_the_export_dropped_is_explained_only_when_the_picture_is_still_held(tmp_path):
    # the export keeps one size of a Wix picture; the documents of the other sizes leave. aaa is kept at another
    # size; of bbb the size kept reads as nothing (the blurred preview of 11-18), so the library holds none of it
    old = library(tmp_path / "old.db", [picture("a1", "aaa", "w_1088,h_612"), picture("a2", "aaa", "w_544,h_306"),
                                         picture("a3", "aaa", "w_1917,h_1080"), picture("b1", "bbb", "w_1088,h_612")])
    new = library(tmp_path / "new.db", [picture("a3", "aaa", "w_1917,h_1080")])
    export = tmp_path / "export"
    export.mkdir()
    gone = [picture("a1", "aaa", "w_1088,h_612"), picture("a2", "aaa", "w_544,h_306"), picture("b1", "bbb", "w_1088,h_612")]
    rows = [{"序号": str(i), "类型": "图片", "网址": doc[3], "最后落地网址": "", "本地档案": "", "分类": "规则不抓",
             "结果": "按规则不抓", "原因代码": "wix_size"} for i, doc in enumerate(gone, 1)]
    (export / "manifest.json").write_text(json.dumps({"网站": "ksped.nknu.edu.tw", "项目": rows}, ensure_ascii=False),
                                          encoding="utf-8")
    left = build._departures(old, new, {}, [export])
    assert left["left"] == 3 and left["explained"] == {"same_image": 2} and left["unexplained"] == {"no_file": 1}
    assert left["items"][0]["url"].startswith(WIX + "bbb") and left["words"]["same_image"] == "同一张图留了另一个尺寸"
    for row in rows:  # dropped for another cause (a fetch that failed): no reason, whatever else the library holds
        row["分类"] = "连接失败"
    (export / "manifest.json").write_text(json.dumps({"网站": "ksped.nknu.edu.tw", "项目": rows}, ensure_ascii=False),
                                          encoding="utf-8")
    assert build._departures(old, new, {}, [export])["unexplained"] == {"no_file": 3}


# ---------------------------------------------------------------- a kind decided out

def test_only_the_kind_decided_out_leaves_for_a_reason(tmp_path):
    # the owner decided teachers' publications in full are not collected; the export's row of that one rule carries
    # the rule's own name. Any other row a path rule dropped still explains nothing.
    papers = [(f"p{i}", "file", f"論文{i}.pdf", f"{KSPED}files/papers/{i}.pdf", "論文全文的第一頁。") for i in range(20)]
    forms = [(f"f{i}", "file", f"表{i}.pdf", f"{KSPED}files/forms/{i}.pdf", "申請表的說明。") for i in range(3)]
    old = library(tmp_path / "old.db", papers + forms)
    new = library(tmp_path / "new.db", [])
    export = tmp_path / "export"
    export.mkdir()
    rows = [{"序号": str(i), "类型": "附件", "网址": doc[3], "最后落地网址": "", "本地档案": "", "分类": "规则不抓",
             "结果": "按规则不抓", "原因代码": "out:skip_path:teacher_fulltext" if doc[0].startswith("p") else "out:skip_path"}
            for i, doc in enumerate(papers + forms, 1)]
    (export / "manifest.json").write_text(json.dumps({"网站": "ksped.nknu.edu.tw", "项目": rows}, ensure_ascii=False),
                                          encoding="utf-8")
    left = build._departures(old, new, {}, [export])
    assert left["left"] == 23 and left["explained"] == {"decided_teacher_fulltext": 20}
    assert left["unexplained"] == {"no_file": 3} and left["words"]["decided_teacher_fulltext"] == "定了不收：教师著作全文"
    assert not build._too_many_left(left)
    # 11-19: the proceedings of a conference are the second kind; each code has its own words
    assert set(build.DECIDED_OUT) == {"out:skip_path:teacher_fulltext", "out:skip_path:proceedings",
                                      "out:skip_path:teaching_materials"}
    assert all(why in build.LEFT_WORDS for why in build.DECIDED_OUT.values())
    for row in rows[:20]:
        row["原因代码"] = "out:skip_path:proceedings"
    (export / "manifest.json").write_text(json.dumps({"网站": "ksped.nknu.edu.tw", "项目": rows}, ensure_ascii=False),
                                          encoding="utf-8")
    again = build._departures(old, new, {}, [export])
    assert again["explained"] == {"decided_proceedings": 20} and again["unexplained"] == {"no_file": 3}
    for row in rows[:20]:
        row["原因代码"] = "out:skip_path:teacher_fulltext"
    rows[0]["分类"] = "连接失败"  # the name alone, on a row that is not the rule's, explains nothing
    (export / "manifest.json").write_text(json.dumps({"网站": "ksped.nknu.edu.tw", "项目": rows}, ensure_ascii=False),
                                          encoding="utf-8")
    assert build._departures(old, new, {}, [export])["unexplained"] == {"no_file": 4}


# ---------------------------------------------------------------- words that moved (11-19)

def test_a_picture_at_another_address_or_read_into_its_page_is_explained(tmp_path):
    # Google Sites gives a picture another address each time its page is read: the export now writes the page's. A
    # document whose row is gone, while every word of it is in the new library (same site, a document or inside a
    # page), explains its leaving; one whose words are nowhere does not
    poster = "增能班海報：時間一月二十一日至二月二日，地點國立高中藝能館，報名請洽總召計畫。"
    olds = [("p1", "file", "unnamed.jpg", f"{KSPED}img/old-a=w1280", poster),
            ("p2", "file", "unnamed.jpg", f"{KSPED}img/old-b=w1280", "另一張海報：研習日期與地點，請見計畫網頁說明。" * 2),
            ("p3", "file", "unnamed.jpg", f"{KSPED}img/old-c=w1280", "這一張的字新庫裡哪裡都沒有，是真的不見了。" * 2),
            ("p4", "file", "unnamed.jpg", f"{KSPED}img/old-d=w1280", "短字")]
    news = [("q1", "file", "unnamed.jpg", f"{KSPED}img/new-a=w1280", poster),  # the same picture, its new address
            ("q2", "page", "增能班海報", f"{KSPED}poster/05", "海報\n" + olds[1][4] + "\nGoogle Sites")]  # read into its page
    old, new = library(tmp_path / "old.db", olds), library(tmp_path / "new.db", news)
    left = build._departures(old, new, {}, [])
    assert left["left"] == 4 and left["explained"] == {"moved": 2} and left["unexplained"] == {"no_row": 2}
    assert {i["url"].rsplit("/", 1)[-1] for i in left["items"]} == {"old-c=w1280", "old-d=w1280"}  # p4: too few words to prove it
    assert left["words"]["moved"] == "字都还在库里（换了网址，或并进了网页）"


def test_a_picture_read_into_the_page_that_shows_it_is_explained_with_its_file(tmp_path):
    # 11-19: the export writes a Google Sites picture at the page's address now, so the page shows it and the build
    # reads its words into that page and makes no document of it: its row has a file all the same. A picture's words
    # count down to the least a picture is a document with; a file of another kind, or a picture whose words are
    # nowhere, still explains nothing
    # Words under MOVED_MIN only in the pages the row says link to it: a picture of the words every page carries (the
    # unit's name in the header) proves nothing by them, and a row that names no page finds nothing; from MOVED_MIN
    # on, the same picture kept at another address counts as anywhere else (counseling-center's banner, 11-19)
    page = "計畫說明\n第二階段\n初步構想計畫申請\n資助領域\nFUNDING AREAS\n表單下載"
    header = "國立高雄師範大學特殊教育中心"
    banner = "國立高雄師範大學\n特殊教育中心\nSpecial Education Center"
    olds = [("p1", "file", "unnamed.jpg", f"{KSPED}img/a=w1280", "第二階段\n初步構想計畫申請"),  # 12 characters
            ("p2", "file", "unnamed.jpg", f"{KSPED}img/b=w1280", "資助領域 FUNDING AREAS"),
            ("p3", "file", "unnamed.jpg", f"{KSPED}img/c=w1280", "這張圖的字新庫裡哪裡都沒有"),
            ("f1", "file", "說明.pdf", f"{KSPED}files/1.pdf", "第二階段\n初步構想計畫申請"),
            ("p4", "file", "unnamed.png", f"{KSPED}img/d=w1280", header),  # its words are only the other page's header
            ("p5", "file", "unnamed.jpg", f"{KSPED}img/e=w1280", "資助領域 FUNDING AREAS"),  # no page named
            ("p6", "file", "unnamed.png", f"{KSPED}img/f=w1280", banner)]  # kept at another address, 20 and more
    old = library(tmp_path / "old.db", olds)
    new = library(tmp_path / "new.db", [("q1", "page", "計畫說明", f"{KSPED}%E8%A8%88%E7%95%AB/", page),
                                         ("q2", "page", "最新消息", f"{KSPED}news", header + "\n最新消息"),
                                         ("q3", "file", "unnamed.png", f"{KSPED}img/g=w1280", banner)])
    export = tmp_path / "export"
    export.mkdir()
    linked = {"p1": "https://ksped.nknu.edu.tw/計畫 | https://ksped.nknu.edu.tw/other", "p2": "http://ksped.nknu.edu.tw/計畫",
              "p3": KSPED + "計畫", "f1": KSPED + "計畫", "p4": KSPED + "計畫", "p5": "",
              "p6": KSPED + "計畫"}
    rows = [{"序号": str(i), "类型": "附件" if doc[0] == "f1" else "图片", "网址": doc[3], "最后落地网址": "",
             "本地档案": f"files/{i}", "分类": "抓到了", "结果": "抓到了", "原因代码": "", "链到它的网页": linked[doc[0]]}
            for i, doc in enumerate(olds, 1)]
    (export / "manifest.json").write_text(json.dumps({"网站": "ksped.nknu.edu.tw", "项目": rows}, ensure_ascii=False),
                                          encoding="utf-8")
    left = build._departures(old, new, {}, [export])
    assert left["left"] == 7 and left["explained"] == {"moved": 3} and left["unexplained"] == {"row_not_read": 4}
    assert {i["url"].rsplit("/", 1)[-1] for i in left["items"]} == {"c=w1280", "1.pdf", "d=w1280", "e=w1280"}
    # without a row, a picture's few words prove nothing, as before; the banner kept at another address does
    assert build._departures(old, new, {}, [])["explained"] == {"moved": 1}


def test_a_picture_is_a_document_from_the_least_the_guard_looks_for():
    from nknu.content import assemble
    assert build.PICTURE_MOVED_MIN == assemble.IMAGE_DOC_CHARS and build.PICTURE_MOVED_MIN < build.MOVED_MIN


def test_a_page_whose_words_are_only_in_a_picture_now_is_no_move(tmp_path):
    # the words belong on the page: a page that left (or was emptied) while its words are only in a picture of the
    # new library explains nothing; a picture whose words are now in a page does
    words = "增能班海報：時間一月二十一日至二月二日，地點國立高中藝能館。"
    old = library(tmp_path / "old.db", [("p1", "page", "增能班海報", f"{KSPED}poster/05", "海報\n" + words)])
    new = library(tmp_path / "new.db", [("q1", "file", "unnamed.jpg", f"{KSPED}img/new-a=w1280", "海報" + words)])
    left = build._departures(old, new, {}, [])
    assert left["explained"] == {} and left["unexplained"] == {"no_row": 1}
