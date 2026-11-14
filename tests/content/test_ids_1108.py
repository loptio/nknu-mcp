"""Plan 2026-11-08 T1 (spec 2026-11-08 §一): stable ids, the alias table and the tier column. An id is a pure function
of a manifest row (nknu.content.ids), so it survives a renumbered manifest; a c.nknu notice is one per site and nid;
the build checks one id per key, builds the rows of one identity as one document (documents.one_per_id), keeps the ids
of what it leaves out under another document in doc_alias (read resolves them) and writes each document's tier, which
search reads. No model loads; the names are made up."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

import pytest

from nknu.content import ids
from nknu.content.assemble import assemble
from nknu.content.build import BuildError, build_db
from nknu.content.corpus import Record
from nknu.content.documents import DUPLICATE, EVENT_KIND, FORMAT, build_documents, one_per_id
from nknu.content.schema import BACKGROUND, PRIMARY
from nknu.retrieval.respond import build_response, read_response
from nknu.retrieval.search import RankConfig, background_ids, search_docs
from tests.content import test_assemble as ta
from tests.content import test_build as tb

ACA = "https://c.nknu.edu.tw/aca/"
CCEE = "https://c.nknu.edu.tw/ccee/"
LIST = "https://sso.nknu.edu.tw/services/site/index.aspx?cLv2=100061"
UNID = "8CCBFA26E3B04D2B9A4F0C1D2E3F405162738495"
ID_SHAPE = re.compile(r"[um][a-z2-7]{12}")  # 13 characters: the letter, then 60 bits of lowercase base32


def row(n: int, url: str, **more) -> dict:
    return {"序号": str(n), "类型": "网页", "网址": url, **more}


# ---------------------------------------------------------------- keys and ids

def test_ids_survive_a_renumbered_manifest():
    rows = [row(1, ACA + "newsdetail.aspx?nid=662&gi=2"), row(2, ACA + "uploadfile/a.pdf", 类型="附件")]
    before = {rows[i]["网址"]: ids.record_id(key) for i, key in enumerate(ids.manifest_keys(rows).values())}
    moved = [row(1, ACA + "page.aspx?pn=1"), row(2, rows[0]["网址"]), row(3, ACA + "x.pdf", 类型="附件"),
             row(4, rows[1]["网址"], 类型="附件")]  # rows added before them: every 序号 moves
    keys = ids.manifest_keys(moved)
    assert {r["网址"]: ids.record_id(keys[int(r["序号"])]) for r in moved[1::2]} == before


def test_a_notice_is_one_per_site_and_nid():
    aca = ids.location_key(ACA + "newsdetail.aspx?nid=578&gi=2")
    ccee = ids.location_key(CCEE + "newsdetail.aspx?nid=578")
    assert (aca, ccee) == ("cnknu:c.nknu.edu.tw/aca:578", "cnknu:c.nknu.edu.tw/ccee:578")
    assert ids.record_id(aca) != ids.record_id(ccee)  # numbered per site: two notices (review 11-08, /3/7)
    assert ids.location_key("https://tecs.nknu.edu.tw/newsdetail.aspx?nid=578") == "cnknu:tecs.nknu.edu.tw:578"
    assert ids.location_key("https://sea.nknu.edu.tw/newsdetail.aspx?nid=578") == "cnknu:sea.nknu.edu.tw:578"


def test_gi_and_amp_spellings_of_a_notice_are_one_key():
    spellings = [ACA + "newsdetail.aspx?nid=606&gi=4", ACA + "newsdetail.aspx?nid=606",
                 "http://c.nknu.edu.tw/aca/newsdetail.aspx?nid=606&amp;gi=4", ACA + "NewsDetail.aspx?GI=2&Nid=606",
                 ACA + "newsview.aspx?nid=606"]
    assert {ids.location_key(u) for u in spellings} == {"cnknu:c.nknu.edu.tw/aca:606"}


def test_sso_news_mirrors_are_one_key():
    news = f"https://news.nknu.edu.tw/nknu_news/lastnews.aspx?catid=CAMPUS&unid={UNID.lower()}"
    sso = f"https://sso.nknu.edu.tw/news/lastnews.aspx?unid={UNID}"
    assert ids.location_key(news) == ids.location_key(sso) == "sso-news:" + UNID


def test_drive_item_media_and_export_urls_are_one_key():
    api = "https://www.googleapis.com/drive/v3/files/1Kh3gZTTq-x_9"
    urls = [api + "?fields=id,name,mimeType&supportsAllDrives=true", api + "?alt=media&supportsAllDrives=true",
            api + "/export?mimeType=application%2Fpdf"]
    assert {ids.location_key(u) for u in urls} == {"drive:1Kh3gZTTq-x_9"}


def test_any_other_address_is_its_url_without_the_scheme():
    assert ids.location_key("http://c.nknu.edu.tw/aca/f/a.pdf") == ids.location_key(ACA + "f/a.pdf") == (
        "url:c.nknu.edu.tw/aca/f/a.pdf")
    assert ids.location_key(ACA + "album.aspx?pn=1&amp;gi=5#第2页") == "url:c.nknu.edu.tw/aca/album.aspx?pn=1&gi=5#第2页"
    # a notice page of a host no c.nknu profile covers keeps its whole address (no nid reduction there)
    assert ids.location_key("https://x.test/newsdetail.aspx?nid=1&gi=2") == "url:x.test/newsdetail.aspx?nid=1&gi=2"


def test_press_rows_with_the_same_words_get_their_archive_numbers():
    rows = [row(1, LIST), row(2, f"{LIST}#按钮=115學年度第1學期學生事務會議", 链接文字="115學年度第1學期學生事務會議 紀錄",
                              存档编号="a12"),
            row(3, f"{LIST}#按钮=115學年度第1學期學生事務會議（a13）", 链接文字="115學年度第1學期學生事務會議  紀錄",
                存档编号="a13"),
            row(4, f"{LIST}#按钮=停修申請表", 标题="停修申請表", 存档编号="a14")]
    keys = ids.manifest_keys(rows)
    words = "115學年度第1學期學生事務會議 紀錄"  # NFKC, whitespace squashed: the two rows' words are the same
    assert keys[2] == f"press:url:{LIST.removeprefix('https://')}#{words}#a12" and keys[3] == keys[2][:-3] + "a13"
    assert keys[4] == ids.press_key(LIST, "停修申請表") and ids.dup_rows(rows) == {2, 3}
    assert keys[1] == ids.location_key(LIST)
    assert ids.press_key(LIST + "#第2页", "x", row_key="r7") == f"press:{ids.location_key(LIST)}#r7"


def test_member_keys():
    archive = ids.location_key(ACA + "uploadfile/m.zip")
    assert ids.member_key(archive, "會議/議程.pdf") == archive + "#會議/議程.pdf"
    assert ids.member_key(archive, "a\u0301.pdf") == ids.member_key(archive, "\u00e1.pdf")  # NFC: one path
    assert ids.member_key(archive, "x.pdf", 1) == archive + "#x.pdf#1"  # the second x.pdf in one archive
    assert ids.record_id(ids.member_key(archive, "x.pdf"), member=True).startswith("m")


def test_ids_are_13_lowercase_base32_characters():
    for key, member in (("url:c.nknu.edu.tw/aca/a.pdf", False), ("cnknu:c.nknu.edu.tw/aca:1", False),
                        ("url:c.nknu.edu.tw/aca/m.zip#a.pdf", True)):
        rid = ids.record_id(key, member=member)
        assert ID_SHAPE.fullmatch(rid) and rid[0] == ("m" if member else "u") and rid == ids.record_id(key, member)
    assert ids.ID_SCHEME == "url-sha256-60:v1"


# ---------------------------------------------------------------- assemble

def test_assemble_writes_stable_ids_and_their_keys(tmp_path):
    records, _ = ta.run_assemble(tmp_path / "a")
    rows = ta.base_rows()
    extra = {"type": "网页", "url": ACA + "page.aspx?pn=9", "file": "pages/0000-new.html", "title": "新頁",
             "data": ta.notice_html(body="<p>新頁</p>", title="新頁")}
    results = {str(int(k.split("!")[0]) + 1) + (("!" + k.split("!")[1]) if "!" in k else ""): v
               for k, v in ta.base_results().items()} | {"1": ({"page": {"title": "新頁", "album": "", "images": []}},
                                                              "新頁")}
    again, _ = ta.run_assemble(tmp_path / "b", [extra, *rows], results)  # one page more before every row
    same = {r["url"]: (r["id"], r["key"]) for r in records}
    assert {r["url"]: (r["id"], r["key"]) for r in again if r["url"] in same} == same
    assert all(ID_SHAPE.fullmatch(r["id"]) and r["id"] == ids.record_id(r["key"], r["id"][0] == "m") for r in records)
    notice = next(r for r in records if r["url"] == ta.NOTICE)
    assert notice["key"] == "cnknu:c.nknu.edu.tw/aca:1"
    members = [r for r in records if r["id"].startswith("m")]
    assert [m["key"] for m in members] == [ids.member_key(ids.location_key(ta.ZIP_URL), p) for p in ta.MEMBERS]


def test_assemble_counts_the_press_rows_whose_ids_may_change(tmp_path):
    pdf = ta.PDF + b"press"
    rows = [{"type": "网页", "url": LIST, "file": "pages/0001.html", "title": "公告",
             "data": ta.notice_html(body="<p>公告</p>")}]
    for n, archived in ((2, "a5"), (3, "a6")):  # the export adds （存档编号） to 网址 when two buttons share words
        rows.append({"type": "附件", "url": f"{LIST}#按钮=申請表（{archived}）", "file": f"files/{n:04d}.pdf",
                     "title": "申請表", "data": pdf + archived.encode(), "linked": [LIST], "linktext": "申請表",
                     "archived": archived})
    rows.append({"type": "附件", "url": f"{LIST}#按钮=停修", "file": "files/0004.pdf", "title": "停修",
                 "data": pdf + b"4", "linked": [LIST], "linktext": "停修"})
    export = ta.write_export(tmp_path / "export", rows)
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    for item, r in zip(manifest["项目"], rows, strict=True):
        item["存档编号"] = r.get("archived", "")
    (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    page = ({"page": {"title": "公告", "album": "", "images": []}}, "公告")
    totext = ta.fake_totext(tmp_path, export, {"1": page} | {str(n): ({"pages": 1, "page_starts": [0]}, ta.BODY)
                                                             for n in (2, 3, 4)})
    report = assemble(export, totext, tmp_path / "out" / "corpus.json", fallback=None)
    records = json.loads((tmp_path / "out" / "corpus.json").read_text(encoding="utf-8"))
    files = [r for r in records if r["url"].startswith(LIST + "#")]
    assert [r["key"].rsplit("#", 1)[-1] for r in files[:2]] == ["a5", "a6"] and len({r["id"] for r in files}) == 3
    assert report.unstable_ids == 2


def test_assembles_page_index_tells_two_sites_notices_apart():
    # the pages a file's row lists are found by this key (_Pages.at): 教務處's notice 578 is never ccee's (it was nid:578)
    from nknu.content.assemble import _page_key

    assert _page_key(ACA + "newsdetail.aspx?nid=578&gi=2") != _page_key(CCEE + "newsdetail.aspx?nid=578&gi=2")
    assert _page_key(ACA + "newsdetail.aspx?nid=578&amp;gi=4") == _page_key("http://c.nknu.edu.tw/aca/newsdetail.aspx"
                                                                           "?nid=578")


def test_a_path_twice_in_one_archive_gives_two_members(tmp_path):
    from nknu.totext.cache import Cache

    rows, results = ta.base_rows(), ta.base_results()
    twice = [ta.member_entry(1, "會議.pdf", ta.PDF + b"one"), ta.member_entry(2, "會議.pdf", ta.PDF + b"two")]
    results["7"] = ({"reason": "解开 2 份成员", "members": twice}, "")
    del results["7!會議.pdf"], results["7!議程.pdf"]
    export = ta.write_export(tmp_path / "export", rows)
    totext = ta.fake_totext(tmp_path, export, results)
    for entry, text in zip(twice, ("會議紀錄", "第二份會議紀錄"), strict=True):  # each its own entry, as the run writes
        Cache(totext / "cache").save(entry["sha256"], {"route": "pdf", "kind": "pdf", "pdf_offset": 0, "page_kind": "",
                                                       "status": "ok", "reason": "", "pages": 1, "page_starts": [0]},
                                     text + "\n" + ta.BODY, {})
    assemble(export, totext, tmp_path / "out" / "corpus.json", fallback=None)
    records = json.loads((tmp_path / "out" / "corpus.json").read_text(encoding="utf-8"))
    archive = ids.location_key(ta.ZIP_URL)
    members = [r for r in records if r["id"].startswith("m")]
    assert [m["key"] for m in members] == [archive + "#會議.pdf", archive + "#會議.pdf#1"]
    assert len({m["id"] for m in members}) == 2


# ---------------------------------------------------------------- documents: one site's notice is not another's

def rec(rid: str, kind: str, url: str, title: str = "", text: str = "", linked=(), linktext: str = "",
        **more) -> Record:
    return Record(rid, kind, url, f"pages/{rid}.html", title, text or title, linktext, tuple(linked), **more)


def test_two_sites_with_the_same_nid_are_two_notices_with_their_own_files_and_groups():
    from tests.content.test_documents import meta

    aca, ccee = ACA + "newsdetail.aspx?nid=578&gi=2", CCEE + "newsdetail.aspx?nid=578&gi=2"
    records = [rec("n1", "newsdetail", aca, "學分學程申請"), rec("n2", "newsdetail", ccee, "專題演講"),
               rec("p1", "page", ACA + "page.aspx?pn=1&gi=2", "教務處頁"), rec("p2", "page", CCEE + "page.aspx?gi=2", "系頁"),
               rec("a1", "附件:pdf:有文字层", ACA + "uploadfile/a.pdf", "申請表.pdf", linked=(aca,)),
               rec("a2", "附件:pdf:有文字层", CCEE + "uploadfile/b.pdf", "演講海報.pdf", linked=(ccee,))]
    metas = {"n1": meta("和平教務組", "學分學程申請"), "n2": meta("系辦公室", "專題演講")}
    docs, excluded = build_documents(records, metas, [])
    by = {d.doc_id: d for d in docs}
    assert excluded == {} and {by["dn1"].kind_class, by["dn2"].kind_class} == {"notice"}
    assert (by["da1"].parent_id, by["da2"].parent_id) == ("dn1", "dn2")  # each file on its own site's notice
    assert (by["dp1"].grp, by["dp2"].grp) == ("和平教務組", "系辦公室")  # gi 2 is voted per site


def test_a_notice_at_two_spellings_is_deduped_by_its_key_and_listed_as_a_duplicate():
    plain, escaped = ACA + "newsdetail.aspx?nid=606&gi=4", ACA + "newsdetail.aspx?nid=606&amp;gi=4"
    docs, excluded = build_documents([rec("p330", "newsdetail", escaped, "轉學招生"),
                                      rec("p64", "newsdetail", plain, "轉學招生")], {}, [])
    assert [d.doc_id for d in docs] == ["dp64"] and excluded == {"newsdetail(重复)": 1}
    assert docs[0].duplicates == ("dp330",)  # the build keeps its id in doc_alias


def test_one_record_per_id_joins_what_the_others_say():
    # two rows of one identity (http and https here) have one id: one record stays, the others' pages join it
    a, b = ACA + "newsdetail.aspx?nid=1", ACA + "newsdetail.aspx?nid=2"
    first = rec("u1", "附件:pdf:有文字层", "http://c.nknu.edu.tw/aca/f/x.pdf", "x.pdf", linked=(a,), zones={a: "content"},
                contexts={a: "申請表"}, nav_linked=(b,), linktext="申請表")
    second = rec("u1", "附件:pdf:有文字层", ACA + "f/x.pdf", "x.pdf", linked=(b,), zones={b: "attachments"},
                 contexts={b: "停修申請表"}, linktext="停修申請表")
    other = rec("u2", "page", ACA + "page.aspx", "頁")
    kept, counted = one_per_id([first, other, second])
    assert [r.id for r in kept] == ["u1", "u2"] and counted == {"附件:pdf:有文字层(重复)": 1}
    assert (kept[0].url, kept[0].linked, kept[0].nav_linked) == (first.url, (a, b), ())  # b: a text reference there
    assert kept[0].zones == {a: "content", b: "attachments"} and kept[0].contexts == {a: "申請表", b: "停修申請表"}
    assert kept[0].linktext == "申請表 | 停修申請表"
    amp = rec("u3", "newsdetail", ACA + "newsdetail.aspx?nid=3&amp;gi=2", "公告")
    plain = rec("u3", "newsdetail", ACA + "newsdetail.aspx?nid=3&gi=2", "公告")
    assert one_per_id([amp, plain])[0] == [plain]  # the one written without &amp; stays (the notice rule)
    assert one_per_id([other]) == ([other], {})


# ---------------------------------------------------------------- the build: aliases, tier, the id check

ALIAS_EXTRA = {  # a format sibling assemble folded into a2, and an &amp; spelling of p61 (another id: the 10-29 shape)
    "a2": {"formats": [{"id": "a17", "url": ACA + "uploadfile/files/a17/停修申請表.doc", "file": "files/a17.doc",
                        "format": "doc", "kind": "附件:office"}]},
    "p62": {"url": ACA + "newsdetail.aspx?nid=606&amp;gi=4"},
}
DUP_NOTICE = ("p62", 606, 4, tb.NOTICES[5][3], "2026/5/1 上午 09:00:00", "招生企劃組", "暑假轉學")


@pytest.fixture
def aliased(tmp_path):
    paths = tb.make_inputs(tmp_path, notices=[DUP_NOTICE], extra=ALIAS_EXTRA)
    report = tb.run(paths, tmp_path / "out" / "aca.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'out' / 'aca.db'}?mode=ro", uri=True)
    yield report, conn
    conn.close()


def test_the_alias_table_keeps_copies_formats_and_duplicates(aliased):
    report, conn = aliased
    assert sorted(conn.execute("SELECT alias, doc_id, why FROM doc_alias")) == [
        ("da12", "da13", "copy"), ("da14", "da15", "copy"), ("da17", "da2", "format"), ("dp62", "dp61", "duplicate")]
    assert report.counts["doc_alias"] == {"copy": 2, "format": 1, "duplicate": 1}
    assert report.excluded["newsdetail(重复)"] == 1


@pytest.mark.parametrize(("alias", "kept", "label"), [("da12", "da13", "副本"), ("da17", "da2", "其他格式"),
                                                     ("dp62", "dp61", "重複")])
def test_read_resolves_an_alias_to_the_kept_document(aliased, alias, kept, label):
    _, conn = aliased
    resp = read_response(conn, f" {alias} ", "", today=tb.TODAY)
    assert resp["found"] is True and resp["id"] == kept
    assert resp["notes"][0] == f"【系統】這個編號是 {kept} 的{label}，以下是保留的那份。"
    plain = read_response(conn, kept, "", today=tb.TODAY)
    assert {k: v for k, v in resp.items() if k != "notes"} == {k: v for k, v in plain.items() if k != "notes"}
    assert resp["notes"][1:] == plain["notes"]


def test_a_database_without_doc_alias_answers_an_unknown_id_as_before(tmp_path):
    from tests.retrieval import test_respond

    t = test_respond.Tiny(tmp_path / "old.db")  # the skeleton's and the 11-04 builds have no doc_alias
    try:
        t.doc("dp1", "停修申請受理期間至十一月中旬。", title="停修公告")
        t.conn.execute("DROP TABLE doc_alias")
        assert read_response(t.conn, "da12", "", today=tb.TODAY)["notes"] == ["【系統】查無此 id"]
        assert read_response(t.conn, "dp1", "", today=tb.TODAY)["found"] is True
    finally:
        t.conn.close()


# the copies notes say so (review of T1); a build whose copies list no id keeps the old ones (test_respond.py).
# Changed 11-14 on purpose (plan 2026-11-14 T2): copies need not share a title any more, only their content
NOTE_COPIES = ("【系統】copies 是同一份文件（內容相同，標題可能不同）在別的網址的副本，列出各自的 id、網址、所屬網頁和日期；"
               "用 read 讀副本的 id 會得到這一筆。內容和該筆相同。")
NOTE_FORMATS = "【系統】copies 中有 format 的是同一份文件的其他格式：各有自己的 id、網址和格式，沒有自己的日期；內容以該筆為準。"


def test_search_copies_carry_their_ids(aliased):
    _, conn = aliased
    rs = build_response(conn, search_docs(conn, "選課操作說明", today=tb.TODAY, aliases=[]), today=tb.TODAY)
    r = next(r for r in rs["results"] if r["id"] == "da13")
    assert [c["id"] for c in r["copies"]] == ["da12"]
    stop = build_response(conn, search_docs(conn, "停修申請表", today=tb.TODAY, aliases=[]), today=tb.TODAY)
    form = next(r for r in stop["results"] if r["id"] == "da2")
    assert form["copies"] == [{"id": "da17", "url": ALIAS_EXTRA["a2"]["formats"][0]["url"], "format": "DOC",
                               "parent": {"id": "dp15", "title": "表單下載"}}]
    assert NOTE_COPIES in rs["notes"] and NOTE_FORMATS in stop["notes"]
    read = read_response(conn, "da2", "", today=tb.TODAY)
    assert NOTE_FORMATS in read["notes"] and read["copies"][0]["id"] == "da17"


TIERED = [  # id, kind, url, file name, text, linking ids: an archive member (m…, and the a<序号>m<k> of an older
    # assemble), a journal and an exam by their titles (build.doc_type), and a primary file
    ("mzz1", "附件:pdf:有文字层", tb.attachment_url("a70", "m.zip") + "#a.pdf", "會議紀錄.pdf", "停修會議紀錄", ["p15"]),
    ("a71m2", "附件:pdf:有文字层", tb.attachment_url("a71", "n.zip") + "#b.pdf", "議程.pdf", "停修議程", ["p15"]),
    ("a72", "附件:pdf:有文字层", tb.attachment_url("a72", "55期學報.pdf"), "55期學報.pdf", "停修研究", ["p15"]),
    ("a73", "附件:pdf:有文字层", tb.attachment_url("a73", "歷屆考題.pdf"), "歷屆考題.pdf", "停修試題", ["p15"]),
    ("a74", "附件:pdf:有文字层", tb.attachment_url("a74", "停修須知.pdf"), "停修須知.pdf", "停修須知", ["p15"]),
]


def test_docs_tier_marks_members_journals_and_exams(tmp_path):
    paths = tb.make_inputs(tmp_path, attachments=TIERED)
    report = tb.run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        tiers = dict(conn.execute("SELECT doc_id, tier FROM docs WHERE doc_id IN ('dmzz1', 'da71m2', 'da72', 'da73',"
                                  " 'da74', 'dp32', 'eev1')"))
        meta = dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()
    assert tiers == {"dmzz1": BACKGROUND, "da71m2": BACKGROUND, "da72": BACKGROUND, "da73": BACKGROUND,
                     "da74": PRIMARY, "dp32": PRIMARY, "eev1": PRIMARY}
    assert report.counts["tiers"] == {PRIMARY: report.counts["docs"]["total"] - 4, BACKGROUND: 4}
    assert meta["id_scheme"] == ids.SERIAL_SCHEME  # this corpus's ids are its own, with no keys


def test_search_reads_the_tier_column(tmp_path):
    paths = tb.make_inputs(tmp_path, attachments=TIERED)
    tb.run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(tmp_path / "x.db")
    try:
        found = ["dmzz1", "da71m2", "da72", "da73", "da74"]
        assert background_ids(conn, found) == {"dmzz1", "da71m2", "da72", "da73"}
        conn.execute("UPDATE docs SET tier = ? WHERE doc_id = 'da72'", (PRIMARY,))  # the column decides, not doc_type
        conn.execute("UPDATE docs SET tier = ? WHERE doc_id = 'da74'", (BACKGROUND,))
        assert background_ids(conn, found) == {"dmzz1", "da71m2", "da73", "da74"}
        hits = search_docs(conn, "停修", today=tb.TODAY, aliases=[], rank=RankConfig(recency=False), limit=50).hits
        assert {h.doc_id for h in hits if h.background} == {"dmzz1", "da71m2", "da73", "da74"}
    finally:
        conn.close()


def _corpus(paths) -> list[dict]:
    return json.loads(paths["corpus"].read_text(encoding="utf-8"))


def test_two_keys_with_one_id_stop_the_build(tmp_path):
    paths = tb.make_inputs(tmp_path)
    records = _corpus(paths)
    for r, key in zip(records[:2], ("url:c.nknu.edu.tw/aca/a", "url:c.nknu.edu.tw/aca/b"), strict=True):
        r["id"], r["key"] = "uaaaaaaaaaaaa", key  # a forced collision: two identities, one id
    paths["corpus"].write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(BuildError, match="编号撞了") as stop:
        tb.run(paths, tmp_path / "x.db")
    assert "url:c.nknu.edu.tw/aca/a" in str(stop.value) and "url:c.nknu.edu.tw/aca/b" in str(stop.value)
    assert not (tmp_path / "x.db").exists() and isinstance(stop.value, ValueError)  # main prints it, exit code 2


def test_a_key_that_changes_kind_stops_the_build(tmp_path):
    paths = tb.make_inputs(tmp_path)
    records = _corpus(paths)
    key = "url:c.nknu.edu.tw/aca/m.zip#a.pdf"
    records[0] |= {"id": ids.record_id(key), "key": key}
    records[1] |= {"id": ids.record_id(key, member=True), "key": key}
    paths["corpus"].write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(BuildError, match="编号撞了") as stop:
        tb.run(paths, tmp_path / "x.db")
    assert key in str(stop.value)


def test_an_assembled_corpus_is_built_with_its_stable_ids(tmp_path):
    records, _ = ta.run_assemble(tmp_path)
    (tmp_path / "aliases.yaml").write_text("groups: []\n", encoding="utf-8")
    build_db(tmp_path / "out" / "corpus.json", tmp_path / "export", tmp_path / "db" / "aca.db", server_dates=None,
             aliases=tmp_path / "aliases.yaml")
    conn = sqlite3.connect(f"file:{tmp_path / 'db' / 'aca.db'}?mode=ro", uri=True)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        tiers = dict(conn.execute("SELECT doc_id, tier FROM docs"))
    finally:
        conn.close()
    assert meta["id_scheme"] == ids.ID_SCHEME
    kept = {"d" + r["id"] for r in records if r["kind"] != "album_images"}
    assert set(tiers) == kept and all(re.fullmatch(r"d[um][a-z2-7]{12}", d) for d in tiers)
    assert {d for d, t in tiers.items() if t == BACKGROUND} == {d for d in kept if d.startswith("dm")}  # 2 members


# ---------------------------------------------------------------- the build: rows of one identity (review of T1)

NID606, NID606_AMP = ACA + "newsdetail.aspx?nid=606&gi=4", ACA + "newsdetail.aspx?nid=606&amp;gi=4"
SINGLE = "c.nknu.edu.tw/aca/page_single.aspx?gi=4&tid=57"  # without its scheme: the export has it at http and https
TRANSFER_PDF = ACA + "uploadfile/news/606/a.pdf"


def test_rows_of_one_identity_build_one_document(tmp_path):
    # Both 教務處 manifests (10-27, 11-02) hold such rows: nid 606, 549 and 617 at their plain and &amp; spellings and
    # page_single tid=57 at http and https, all exported. A row's id is its identity's, so their records share an id:
    # without documents.one_per_id the build stops on docs' PRIMARY KEY (review of T1; the one-export form of the
    # plan's Review Focus 3). The &amp; spelling and http come first, so corpus order alone would keep them.
    notice = ta.notice_html(body="<p>本學期轉學招生簡章公告</p>", files='<a href="uploadfile/news/606/a.pdf">轉學招生簡章.pdf</a>',
                            title="轉學招生", added="2026/5/1 上午 09:00:00", group="招生企劃組")
    single = ta.notice_html(body="<p>招生企劃組業務職掌</p>", title="業務職掌", group="")
    rows = [{"type": "网页", "url": NID606_AMP, "file": "pages/0001.html", "title": "轉學招生", "data": notice},
            {"type": "网页", "url": NID606, "file": "pages/0002.html", "title": "轉學招生", "data": notice},
            {"type": "网页", "url": "http://" + SINGLE, "file": "pages/0003.html", "title": "業務職掌", "data": single},
            {"type": "网页", "url": "https://" + SINGLE, "file": "pages/0004.html", "title": "業務職掌", "data": single},
            {"type": "附件", "url": TRANSFER_PDF, "file": "files/0005-a.pdf", "data": ta.PDF + b"606", "title": "a.pdf",
             "fmt": "pdf", "linked": [NID606_AMP], "linktext": "轉學招生簡章.pdf"}]
    page = {"1": ("轉學招生", "本學期轉學招生簡章公告"), "2": ("轉學招生", "本學期轉學招生簡章公告"),
            "3": ("業務職掌", "招生企劃組業務職掌"), "4": ("業務職掌", "招生企劃組業務職掌")}
    results = {n: ({"page": {"title": title, "album": "", "images": []}}, f"{title}\n{body}")
               for n, (title, body) in page.items()} | {"5": ({"pages": 1, "page_starts": [0]}, ta.BODY)}
    export = ta.write_export(tmp_path / "export", rows)
    totext = ta.fake_totext(tmp_path, export, results)
    corpus = tmp_path / "out" / "corpus.json"
    assemble(export, totext, corpus, fallback=None)
    records = json.loads(corpus.read_text(encoding="utf-8"))
    named = ta.row_ids(export)  # p1 … a5: the stable id of each row's record
    assert Counter(r["id"] for r in records) == {named["p1"]: 2, named["p3"]: 2, named["a5"]: 1}  # two ids twice
    (tmp_path / "aliases.yaml").write_text("groups: []\n", encoding="utf-8")
    out = tmp_path / "db" / "aca.db"
    report = build_db(corpus, export, out, server_dates=None, aliases=tmp_path / "aliases.yaml")
    notice_id, single_id, pdf_id = ("d" + named[k] for k in ("p1", "p3", "a5"))
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        docs = {r[0]: r[1:] for r in conn.execute(
            "SELECT doc_id, kind_class, url, grp, parent_id, ident_date FROM docs")}
        refs = conn.execute("SELECT ref_id FROM doc_refs WHERE doc_id = ?", (pdf_id,)).fetchall()
        aliases = conn.execute("SELECT count(*) FROM doc_alias").fetchone()[0]
        excluded = json.loads(conn.execute("SELECT value FROM meta WHERE key = 'excluded_json'").fetchone()[0])
    finally:
        conn.close()
    # one document per identity: the notice at its plain spelling (the first written without &amp;), the page at the
    # first of its two addresses (corpus order among equals 【Claude】; Task 4: the newest 抓取时间, the same here)
    assert docs == {notice_id: ("notice", NID606, "招生企劃組", None, "2026-05-01"),
                    single_id: ("page", "http://" + SINGLE, "招生企劃組", None, None),  # gi 4: the notice's group
                    pdf_id: ("attachment", TRANSFER_PDF, "招生企劃組", notice_id, "2026-05-01")}
    assert refs == [(notice_id,)] and aliases == 0  # the &amp; page is the kept notice; one id needs no alias
    assert report.excluded == excluded == {"newsdetail" + DUPLICATE: 1, "page_single" + DUPLICATE: 1}
    # check_realdata's check_counts: the non-event documents are the corpus records less the left-out ones (not the
    # calendar's, which are no records, nor the format siblings folded into a record)
    left_out = sum(n for k, n in excluded.items() if not k.startswith(EVENT_KIND) and not k.endswith(FORMAT))
    assert sum(kind != "event" for kind, *_ in docs.values()) == len(records) - left_out == 3
