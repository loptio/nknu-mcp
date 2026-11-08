"""Plan 2026-11-08 T4 (spec 2026-11-08 §二 5): one database from many exports, and the unit patch (owner 11-08 04:08:
「请把文件的单位名、组别（以及单位简称）加进索引和 passage，免得搜『某系 + 某事』时搜不到对应系所的网页」).

Records of one id from several inputs are one document: the record of the newest 抓取时间 stays (an &amp; spelling
still gives way first), with the pages, zones and contexts of all; events of one id are one; each export is a row of
the sites table with its own 资料截至 and missing counts, and each result shows its own site's. Each document's unit
line (單位：<unit>（<short names>） <組別>) is the second line of every passage and its words are in title_tokens. The
pages are made up in the shape of each template (tests/totext/test_pages_1108.py's); the people in them are O masks.
The unit tree is test_build's made-up one, with the student affairs office added. No model loads."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from nknu.content import build, ids, sites
from nknu.content.build import UNIT_LABEL, BuildError, UnitWords, build_db, build_sites, unit_words
from nknu.content.corpus import Event, Record, load_calendar
from nknu.content.documents import Document, one_event_per_id, one_per_id
from nknu.content.embed import PASSAGE_VERSION, VecCache, chunk_passages, embed_chunks, passage
from nknu.content.units import load_units, resolve
from nknu.retrieval import respond
from nknu.retrieval.respond import build_response, read_response
from nknu.retrieval.search import search_docs
from tests.content import test_assemble as ta
from tests.content import test_build as tb
from tests.totext import test_pages_1108 as tp

TODAY = date(2026, 11, 8)
SCHOOL = "國立高雄師範大學"
ACA, WEB = "https://c.nknu.edu.tw/aca/", tp.WEB
NOTICE = ACA + "newsdetail.aspx?nid=578&gi=2"
FORM = ACA + "uploadfile/news/578/form.pdf"  # 教务处's file, which 生活輔導組's notice links too
DETAIL = WEB + "&selpage=MoreNews&newsidx=1326"
CAL_FILE = "data/0001-Google 行事历 nknubf@gmail.com 的事件.json"
CAL_URL = "https://www.googleapis.com/calendar/v3/calendars/nknubf%40gmail.com/events"
OLD, NEW = "2026-10-27T07:00:00+08:00", "2026-11-07T07:00:00+08:00"  # the two exports' 抓取时间
UNIT_SITES = [*tb.UNIT_SITES, tb.unit_site("學生事務處", "行政单位"), tb.unit_site("生活輔導組", "行政组别", "學生事務處")]
PAGE = {"page": {"title": "", "album": "", "images": []}}
PDF = {"pages": 1, "page_starts": [0]}


def event(eid: str, title: str, start: str, end: str) -> dict:
    return {"id": eid, "status": "confirmed", "summary": title, "start": {"date": start}, "end": {"date": end},
            "htmlLink": f"https://www.google.com/calendar/event?eid={eid}"}


def calendar_row(events: list[dict], fetched: str) -> dict:
    return {"type": "资料", "url": CAL_URL, "file": CAL_FILE, "fmt": "json", "fetched": fetched,
            "data": json.dumps({"kind": "calendar#events", "items": events}, ensure_ascii=False).encode(),
            "title": "Google 行事历 nknubf@gmail.com 的事件"}


EV1 = event("ev1", "申請停修", "2026-11-03", "2026-11-17")
EV2 = event("ev2", "115學年度第1學期期末考試", "2026-12-21", "2026-12-25")
EV3 = event("ev3", "宿舍開放", "2026-09-01", "2026-09-02")
DETAIL_HTML = ('<div class="news-detail-view"><div class="detail-header"><h3>學分學程申請表下載</h3></div>'
               f'<div class="detail-body"><p>請下載<a href="{FORM}">學分學程申請表</a>。承辦人O先生。</p></div></div>')


def aca_rows() -> list[dict]:
    """教务处's export: a notice linking FORM, FORM (fetched before the other export fetched it) and the calendar."""
    return [{"type": "网页", "url": NOTICE, "file": "pages/0001.html", "title": "學分學程申請", "fetched": OLD,
             "data": ta.notice_html(body='<p>請下載<a href="uploadfile/news/578/form.pdf">申請表</a></p>',
                                    title="學分學程申請", added="2026/10/1 上午 09:00:00")},
            {"type": "附件", "url": FORM, "file": "files/0002-form.pdf", "data": ta.PDF + b"old", "title": "form.pdf",
             "fmt": "pdf", "linked": [NOTICE], "linktext": "申請表", "fetched": OLD},
            calendar_row([EV1, EV2], OLD)]


def web_rows() -> list[dict]:
    """生活輔導組's export (sso.nknu.edu.tw/web/595): a notice's detail page linking FORM, FORM fetched again later
    (another version of it) and the same calendar, fetched with the same time as 教务处's (one row of one archive)."""
    return [{"type": "网页", "url": DETAIL, "file": "pages/0001.html", "title": "學分學程申請表下載", "fetched": NEW,
             "data": tp.webidx_page(DETAIL_HTML), "date": "2026-09-11", "date_raw": "發布日期：2026-09-11"},
            {"type": "附件", "url": FORM, "file": "files/0002-form.pdf", "data": ta.PDF + b"new", "title": "form.pdf",
             "fmt": "pdf", "linked": [DETAIL], "linktext": "學分學程申請表", "fetched": NEW},
            calendar_row([{**EV1, "summary": "申請停修（延長）"}, EV3], OLD)]


ACA_RESULTS = {"1": (PAGE, "學分學程申請\n請下載申請表"), "2": (PDF, "學分學程申請表 舊版\n" + ta.BODY), "3": ta.DATA_SKIPPED}
WEB_RESULTS = {"1": (PAGE, "學分學程申請表下載\n請下載學分學程申請表。承辦人O先生。"),
               "2": (PDF, "學分學程申請表 新版\n" + ta.BODY), "3": ta.DATA_SKIPPED}
MISSING = "\ufeff序号,类型,网址,本地档案,分类\n"


def tree_files(root: Path, sites: list[dict] = UNIT_SITES, units_yaml: str = tb.UNITS_YAML) -> dict[str, Path]:
    """The made-up site list and unit config (never the real ones), an alias table and empty server dates."""
    root.mkdir(parents=True, exist_ok=True)
    paths = {"sites": root / "sites.json", "units": root / "units.yaml", "aliases": root / "aliases.yaml",
             "server": root / "server_dates.json"}
    paths["sites"].write_text(json.dumps({"sites": sites}, ensure_ascii=False), encoding="utf-8")
    paths["units"].write_text(units_yaml, encoding="utf-8")
    paths["aliases"].write_text("groups: []\n", encoding="utf-8")
    paths["server"].write_text("{}", encoding="utf-8")
    return paths


# the T4 review's tree: the student affairs office and its section with short names (made up, in the shipped
# config's way), and the site list's way of naming a section with its office first (「總務處 出納組」)
OFFICE_SITES = [s if s["n"] != "出納組" else {**s, "a": ["總務處 出納組"]} for s in UNIT_SITES]
OFFICE_YAML = tb.UNITS_YAML + ("  學生事務處: {aliases: [學務處], evidence: 測試資料（虛構）}\n"
                               "  生活輔導組: {aliases: [生輔組], evidence: 測試資料（虛構）}\n")


def two_exports(tmp_path: Path, web: list[dict] | None = None) -> dict:
    """The two exports assembled (each its own to-text cache), with missing.csv: 教务处 misses a page and two files,
    生活輔導組 a file. web: 生活輔導組's rows, web_rows() unless given."""
    ta.run_assemble(tmp_path / "aca", aca_rows(), ACA_RESULTS, old=None)
    ta.run_assemble(tmp_path / "web", web_rows() if web is None else web, WEB_RESULTS, old=None,
                    site="sso.nknu.edu.tw/web/595", name="学务处生活辅导组")
    (tmp_path / "aca" / "export" / "missing.csv").write_text(
        MISSING + "9,网页,https://x.invalid/1,,连接失败\n10,附件,https://x.invalid/2.pdf,,要人看\n"
        "11,附件,https://x.invalid/3.pdf,,连接失败\n", encoding="utf-8")
    (tmp_path / "web" / "export" / "missing.csv").write_text(MISSING + "5,附件,https://x.invalid/4.pdf,,连接失败\n",
                                                             encoding="utf-8")
    aca, web = ta.row_ids(tmp_path / "aca" / "export"), ta.row_ids(tmp_path / "web" / "export")
    return {"inputs": [(tmp_path / s / "out" / "corpus.json", tmp_path / s / "export") for s in ("aca", "web")],
            "notice": "d" + aca["p1"], "detail": "d" + web["p1"], "form": "d" + aca["a2"], "form_web": "d" + web["a2"],
            **tree_files(tmp_path / "tree")}


def run(paths: dict, out: Path, inputs=None, **kw) -> build.BuildReport:
    kw.setdefault("server_dates", None)
    return build_sites(paths["inputs"] if inputs is None else inputs, out, aliases=paths["aliases"], today=TODAY,
                       site_list=paths["sites"], units_config=paths["units"], **kw)


@pytest.fixture
def two(tmp_path):
    paths = two_exports(tmp_path)
    out = tmp_path / "db" / "aca.db"
    report = run(paths, out)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield paths, report, conn
    conn.close()


# ---------------------------------------------------------------- one record per id, across inputs

def rec(rid: str, url: str, fetched: str = "", site: str = "", **more) -> Record:
    fields = {"linktext": "", "linked": (), "key": "url:" + url, "fetched": fetched, "site": site, **more}
    return Record(rid, "附件:pdf:有文字层", url, f"files/{rid}.pdf", "a.pdf", "x", **fields)


def test_one_per_id_keeps_the_newest_fetch_with_what_every_record_says():
    page_a, page_b = ACA + "page.aspx?pn=1", WEB + "&menuid=518"
    a = rec("u1", FORM, OLD, "c.nknu.edu.tw/aca", linked=(page_a,), zones={page_a: "content"},
            contexts={page_a: "申請表"}, linktext="申請表")
    b = rec("u1", FORM, NEW, "sso.nknu.edu.tw/web/595", linked=(page_b,), zones={page_b: "attachments"},
            contexts={page_b: "學分學程申請表"}, linktext="學分學程申請表")
    kept, excluded = one_per_id([a, rec("u2", ACA + "x.pdf"), b])
    assert [r.id for r in kept] == ["u2", "u1"] and excluded == {"附件:pdf:有文字层(重复)": 1}
    one = kept[1]
    assert (one.fetched, one.site, one.file) == (NEW, "sso.nknu.edu.tw/web/595", "files/u1.pdf")
    assert (one.linked, one.zones) == ((page_b, page_a), {page_b: "attachments", page_a: "content"})
    assert one.contexts == {page_b: "學分學程申請表", page_a: "申請表"} and one.linktext == "學分學程申請表 | 申請表"


def test_one_per_id_still_lets_an_amp_spelling_give_way_first_then_the_owners_export_then_order():
    plain, amp = NOTICE, NOTICE.replace("&", "&amp;")
    newer_amp = rec("u1", amp, NEW)
    older_plain = rec("u1", plain, OLD)
    assert one_per_id([newer_amp, older_plain])[0][0].url == plain  # 教务处's 4 such pairs keep their plain spelling
    # a tie (one archive's row in two exports): the export of the site the address belongs to, then the first
    owner = rec("u3", FORM, OLD, "c.nknu.edu.tw/aca")
    other = rec("u3", FORM, OLD, "sso.nknu.edu.tw/web/595")
    assert one_per_id([other, owner])[0][0].site == "c.nknu.edu.tw/aca"
    drive = "https://www.googleapis.com/drive/v3/files/O1?alt=media"
    first, second = rec("u4", drive, OLD, "sso.nknu.edu.tw/web/595"), rec("u4", drive, OLD, "c.nknu.edu.tw/aca")
    assert one_per_id([first, second])[0][0].site == "sso.nknu.edu.tw/web/595"
    # an unreadable or missing 抓取时间 is the oldest
    assert one_per_id([rec("u5", FORM, "", "a"), rec("u5", FORM, "not a time", "b"), rec("u5", FORM, OLD, "c")]
                      )[0][0].site == "c"


NEWS_FILE = "https://news.nknu.edu.tw/nknu_news/o/uploadfile/form.pdf"  # the announcement platform: shared, 秘書室
NEWS_PAGE = "https://news.nknu.edu.tw/nknu_news/o/news.aspx?id=1"
COMMON = "https://sso.nknu.edu.tw/services/o/page.aspx?id=1"  # SSO's common parts: shared, the school


def test_one_per_id_on_a_tie_puts_a_shared_platforms_own_export_last():
    # review of T4 (11-08): on a tie (one archive's row in two exports, the lab PC's normal case) the export of the
    # address's own site won even when that site is a shared platform, so once the platform's export was in the build
    # a file 教务处 brought from it took the platform's placeholder unit (秘書室, or the school), docs.site and 资料截至,
    # and lost its 組別 (sites.py names the case: 教务处's 35 files on news.nknu.edu.tw). The export that brought it
    # stays now, in either input order 【Claude】
    for url, platform, brought in ((NEWS_FILE, "news.nknu.edu.tw", "c.nknu.edu.tw/aca"),
                                   (COMMON, "sso.nknu.edu.tw/services", "sso.nknu.edu.tw/site/207016")):
        own, other = rec("u6", url, OLD, platform), rec("u6", url, OLD, brought)
        for order in ([own, other], [other, own]):
            assert one_per_id(order)[0][0].site == brought, (url, [r.site for r in order])
    # two exports that brought it, the platform's between them: the first that brought it
    first, second = rec("u7", NEWS_FILE, OLD, "sso.nknu.edu.tw/web/595"), rec("u7", NEWS_FILE, OLD, "c.nknu.edu.tw/aca")
    assert one_per_id([rec("u7", NEWS_FILE, OLD, "news.nknu.edu.tw"), first, second])[0][0].site == first.site
    # the newest fetch still comes first: a newer row of the platform's own export stays, with the platform's unit
    # (exports of one archive at one time never differ; exports of different days come with the deferred daily updates)
    newer = rec("u8", NEWS_FILE, NEW, "news.nknu.edu.tw")
    assert one_per_id([rec("u8", NEWS_FILE, OLD, "c.nknu.edu.tw/aca"), newer])[0][0].site == "news.nknu.edu.tw"


def test_events_of_one_id_are_one_the_newest_calendar_fetch_first_ties_the_first_input():
    def ev(title: str, fetched: str, site: str) -> Event:
        return Event("ev1", title, date(2026, 11, 3), date(2026, 11, 16), "https://www.google.com/calendar/event?eid=1",
                     site=site, fetched=fetched)

    kept, excluded = one_event_per_id([ev("申請停修", OLD, "c.nknu.edu.tw/aca"), ev("申請停修（延長）", NEW, "x"),
                                       Event("ev2", "期末考", date(2026, 12, 21), date(2026, 12, 25), "")])
    assert [(e.id, e.title, e.site) for e in kept] == [("ev1", "申請停修（延長）", "x"), ("ev2", "期末考", "")]
    assert excluded == {"行事历(重复)": 1}
    tie, _ = one_event_per_id([ev("甲", OLD, "c.nknu.edu.tw/aca"), ev("乙", OLD, "x")])
    assert [(e.title, e.site) for e in tie] == [("甲", "c.nknu.edu.tw/aca")]
    assert one_event_per_id([]) == ([], {})


def test_a_file_of_another_unit_takes_no_group_from_the_pages_of_this_one():
    # found by the T4 pilot check (11-08): 體育室's PDF that a 教務處 notice links hung on that notice and took its 組別
    # (課務組), so its unit line said 「單位：體育室 課務組」. A file takes its parent's 組別, or the gi group of the pages
    # linking it, only from pages of its own unit 【Claude】 (教务处's 11-02 export: all 4,020 files and images are
    # 教務處's, so nothing there changes)
    from nknu.content.corpus import PageMeta
    from nknu.content.documents import build_documents

    page = ACA + "page.aspx?pn=7&gi=2"
    records = [Record("n1", "newsdetail", NOTICE, "pages/n1.html", "共同體育選課", "x", "", (), site="c.nknu.edu.tw/aca"),
               Record("p1", "page", page, "pages/p1.html", "表單", "x", "", (), site="c.nknu.edu.tw/aca"),
               Record("a1", "附件:pdf:有文字层", "https://c.nknu.edu.tw/reality/uploadfile/news/1/a.pdf", "files/a1.pdf",
                      "a.pdf", "總表", "", (NOTICE,), site="c.nknu.edu.tw/aca"),
               Record("a2", "附件:pdf:有文字层", ACA + "uploadfile/news/1/b.pdf", "files/a2.pdf", "b.pdf", "表", "",
                      (NOTICE,), site="c.nknu.edu.tw/aca"),
               Record("a3", "附件:pdf:有文字层", "https://c.nknu.edu.tw/affair/uploadfile/c.pdf", "files/a3.pdf", "c.pdf",
                      "表", "", (), nav_linked=(page,), site="c.nknu.edu.tw/aca"),
               Record("a4", "附件:pdf:有文字层", ACA + "uploadfile/d.pdf", "files/a4.pdf", "d.pdf", "表", "", (),
                      nav_linked=(page,), site="c.nknu.edu.tw/aca")]
    meta = PageMeta("和平教務組", "共同體育選課", "2026/10/20 上午 09:00:00", date(2026, 10, 20), ())
    docs = {d.doc_id: d for d in build_documents(records, {"n1": meta}, [])[0]}
    assert {k: (d.unit, d.grp, d.parent_id) for k, d in docs.items() if k.startswith("da")} == {
        "da1": ("體育室", "", "dn1"), "da2": ("教務處", "和平教務組", "dn1"),  # the parent is kept, its 組別 is not
        "da3": ("總務處", "", None), "da4": ("教務處", "和平教務組", None)}  # a menu's gi group: its own unit's only


def test_a_file_whose_parent_is_another_units_later_page_keeps_its_own_units_group():
    # review of T4 (11-08): 教務處's file that 生活輔導組 re-posts later (a common 轉知) hangs on that re-post, the
    # latest-dated page (11-01's rule, decided for one site: whether its own unit's page should be the parent is the
    # owner's call), and took no 組別 from it, so its unit line lost 和平教務組. Now, as a file without a parent: the
    # 組別 of the latest-dated page of its own unit linking it that has one, else the gi group of its own unit's pages
    # linking it, menus too 【Claude】
    from nknu.content.corpus import PageMeta
    from nknu.content.documents import build_documents

    aca = "c.nknu.edu.tw/aca"
    later = ACA + "newsdetail.aspx?nid=579&gi=3"
    menu = ACA + "page.aspx?pn=7&gi=2"
    repost = {"site": "sso.nknu.edu.tw/web/595", "fetched": NEW, "page_date": "2026-10-20",
              "page_date_raw": "發布日期：2026-10-20"}
    records = [Record("n1", "newsdetail", NOTICE, "pages/n1.html", "申請", "x", "", (), site=aca),
               Record("n2", "newsdetail", later, "pages/n2.html", "申請更新", "x", "", (), site=aca),
               Record("p1", "page", menu, "pages/p1.html", "表單", "x", "", (), site=aca),
               Record("r1", "detail:sso_webidx", DETAIL, "pages/r1.html", "轉知", "x", "", (), **repost),
               Record("a1", "附件:pdf:有文字层", FORM, "files/a1.pdf", "form.pdf", "表", "", (NOTICE, later, DETAIL),
                      site=aca),
               Record("a2", "附件:pdf:有文字层", ACA + "uploadfile/b.pdf", "files/a2.pdf", "b.pdf", "表", "", (DETAIL,),
                      nav_linked=(menu,), site=aca),
               Record("a3", "附件:pdf:有文字层", ACA + "uploadfile/c.pdf", "files/a3.pdf", "c.pdf", "表", "", (DETAIL,),
                      site=aca)]
    metas = {"n1": PageMeta("和平教務組", "申請", "2026/10/1 上午 09:00:00", date(2026, 10, 1), ()),
             "n2": PageMeta("招生企劃組", "申請更新", "2026/10/10 上午 09:00:00", date(2026, 10, 10), ())}
    docs = {d.doc_id: d for d in build_documents(records, metas, [])[0]}
    assert (docs["dr1"].unit, docs["dr1"].added) == ("生活輔導組", date(2026, 10, 20))
    assert {k: (d.unit, d.grp, d.parent_id) for k, d in docs.items() if k.startswith("da")} == {
        "da1": ("教務處", "招生企劃組", "dr1"),  # the latest-dated of its own unit's notices (n2, 10-10), not n1's
        "da2": ("教務處", "和平教務組", "dr1"),  # no page of its own unit links it: its menu page's gi 2
        "da3": ("教務處", "", "dr1")}  # nothing of its own unit links it


def test_calendar_events_carry_the_fetch_time_of_their_file(tmp_path):
    ta.run_assemble(tmp_path, aca_rows(), ACA_RESULTS, old=None)
    events = load_calendar(tmp_path / "export")
    assert {(e.id, e.site, e.fetched) for e in events} == {("ev1", "c.nknu.edu.tw/aca", OLD),
                                                           ("ev2", "c.nknu.edu.tw/aca", OLD)}


# ---------------------------------------------------------------- two exports, one database

def test_two_exports_sharing_a_file_and_a_calendar_build_one_database(two):
    paths, report, conn = two
    n, d, f = paths["notice"], paths["detail"], paths["form"]
    assert f == paths["form_web"]  # one identity, one id, whichever export holds it
    docs = {r[0]: r[1:] for r in conn.execute(
        "SELECT doc_id, kind_class, unit, grp, site, parent_id, ident_date FROM docs")}
    assert docs == {
        n: ("notice", "教務處", "和平教務組", "c.nknu.edu.tw/aca", None, "2026-10-01"),
        d: ("notice", "生活輔導組", "", "sso.nknu.edu.tw/web/595", None, "2026-09-11"),
        # the newest fetch is kept (生活輔導組's export), its unit is its own address's site's, its parent the latest
        # dated notice of either export
        f: ("attachment", "教務處", "和平教務組", "sso.nknu.edu.tw/web/595", n, "2026-10-01"),
        # events once: a tie on the calendar's fetch time goes to the first input
        "eev1": ("event", "教務處", "", "c.nknu.edu.tw/aca", None, None),
        "eev2": ("event", "教務處", "", "c.nknu.edu.tw/aca", None, None),
        "eev3": ("event", "生活輔導組", "", "sso.nknu.edu.tw/web/595", None, None)}
    text, title = conn.execute("SELECT text, title FROM docs WHERE doc_id = ?", (f,)).fetchone()
    assert "新版" in text and "舊版" not in text and title == "申請表"  # the latest dated notice's name for it
    assert conn.execute("SELECT title FROM docs WHERE doc_id = 'eev1'").fetchone() == ("申請停修",)
    refs = conn.execute("SELECT ref_id, zone, context FROM doc_refs WHERE doc_id = ?", (f,)).fetchall()
    assert sorted(refs) == sorted([(n, "content", "申請表"), (d, "content", "學分學程申請表")])  # from both sites
    assert report.excluded == {"附件:pdf:有文字层(重复)": 1, "行事历(重复)": 1}
    assert report.counts["docs"] == {"notice": 2, "attachment": 1, "event": 3, "total": 6}
    assert report.counts["shared_ids"] == 1  # ids found in more than one input
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    (a_corpus, a_export), (w_corpus, w_export) = paths["inputs"]
    # the first input stays in the keys readers before T4 read (gold's default corpus, check_realdata)
    assert (meta["corpus"], meta["export_dir"], meta["site"]) == (str(a_corpus), str(a_export), "c.nknu.edu.tw/aca")
    assert [(i["corpus"], i["export"], i["site"]) for i in json.loads(meta["inputs_json"])] == [
        (str(a_corpus), str(a_export), "c.nknu.edu.tw/aca"), (str(w_corpus), str(w_export), "sso.nknu.edu.tw/web/595")]
    assert meta["id_scheme"] == ids.ID_SCHEME


def test_the_sites_table_holds_each_exports_date_and_missing_counts(two):
    paths, report, conn = two
    rows = conn.execute("SELECT site, name, unit, data_as_of, missing_pages, missing_files, docs, export FROM sites"
                        " ORDER BY site").fetchall()
    (_, a_export), (_, w_export) = paths["inputs"]
    assert rows == [("c.nknu.edu.tw/aca", "教務處", "教務處", "2026-10-27", 1, 2, 3, str(a_export)),
                    ("sso.nknu.edu.tw/web/595", "學務處生活輔導組", "生活輔導組", "2026-11-07", 0, 1, 3, str(w_export))]
    assert [s["site"] for s in report.sites] == ["c.nknu.edu.tw/aca", "sso.nknu.edu.tw/web/595"]
    assert report.sites[1] == {"site": "sso.nknu.edu.tw/web/595", "name": "學務處生活輔導組", "unit": "生活輔導組",
                               "data_as_of": "2026-11-07", "missing_pages": 0, "missing_files": 1, "docs": 3,
                               "export": str(w_export), "deleted_notices": 0}  # owner 11-09, blueprint item 15


def test_each_result_shows_its_own_sites_date_and_the_texts_name_no_unit(two):
    paths, _, conn = two
    resp = build_response(conn, search_docs(conn, "學分學程", today=TODAY, aliases=[]), today=TODAY)
    dates = {r["id"]: r["data_as_of"] for r in resp["results"]}
    assert dates == {paths["notice"]: "2026-10-27", paths["detail"]: "2026-11-07", paths["form"]: "2026-11-07"}
    assert list(resp) == ["today", "coverage", "query", "found", "results", "notes"]  # no site list in the head
    assert resp["coverage"] == respond.COVERAGE_SITES_OCR.format(
        sites=2, calendar=respond.CALENDAR_PART, span="2026-10-27～2026-11-07", missing_pages=1, missing_files=3)
    assert "2 個網站" in resp["coverage"] and "每筆結果附所屬網站的資料截至日期" in resp["coverage"]
    assert resp["notes"][0] == respond.NOTE_RANKING_SITES and "教務處" not in resp["notes"][0]
    assert "已收錄的網站" in respond.NOTE_RANKING_SITES and "已收錄的網站" in respond.NOTE_RANKING_HYBRID_SITES
    hybrid = dataclasses.replace(search_docs(conn, "學分學程", today=TODAY, aliases=[]), ranking="hybrid")
    assert build_response(conn, hybrid, today=TODAY)["notes"][0] == respond.NOTE_RANKING_HYBRID_SITES
    none = build_response(conn, search_docs(conn, "!!!", today=TODAY, aliases=[]), today=TODAY)
    assert none == {"today": respond.today_block(TODAY), "coverage": resp["coverage"], "query": "!!!", "found": False,
                    "notes": [respond.NOTE_NOT_FOUND_SITES]}
    assert "教務處" not in respond.NOTE_NOT_FOUND_SITES and "已收錄的網站" in respond.NOTE_NOT_FOUND_SITES
    read = read_response(conn, paths["form"], "", today=TODAY)
    assert read["data_as_of"] == "2026-11-07" and list(read)[:3] == ["today", "found", "id"]
    unknown = read_response(conn, "dNOPE", "", today=TODAY)
    assert unknown == {"today": respond.today_block(TODAY), "found": False, "notes": [respond.NOTE_UNKNOWN_ID]}


def test_a_build_without_a_calendar_does_not_promise_one(tmp_path):
    ta.run_assemble(tmp_path / "web", web_rows()[:2], {k: WEB_RESULTS[k] for k in ("1", "2")}, old=None,
                    site="sso.nknu.edu.tw/web/595", name="学务处生活辅导组")
    paths = tree_files(tmp_path / "tree")
    out = tmp_path / "x.db"
    run(paths, out, inputs=[(tmp_path / "web" / "out" / "corpus.json", tmp_path / "web" / "export")])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        coverage = build_response(conn, search_docs(conn, "!!!", today=TODAY, aliases=[]), today=TODAY)["coverage"]
    finally:
        conn.close()
    assert "行事曆" not in coverage and "1 個網站" in coverage and "資料截至 2026-11-07，" in coverage


def test_a_database_without_sites_answers_with_the_texts_of_before(tmp_path):
    from tests.retrieval import test_respond as tr

    for drop in (False, True):  # an empty sites table (a hand-made db), none at all (the skeleton, the 11-04 build)
        t = tr.Tiny(tmp_path / f"old{drop}.db")
        try:
            if drop:
                t.conn.execute("DROP TABLE sites")
            c = t.doc("dp1", tr.prose(300), title="停修公告")
            resp = build_response(t.conn, tr.result(tr.hit("dp1", c)), today=tr.TODAY)
            assert (resp["coverage"], resp["data_as_of"], resp["notes"][0]) == (
                tr.COVERAGE, {"教務處": "2026-10-27"}, tr.NOTE_RANKING)
            assert "data_as_of" not in resp["results"][0]
            assert read_response(t.conn, "dp1", "", today=tr.TODAY)["data_as_of"] == {"教務處": "2026-10-27"}
            none = build_response(t.conn, tr.result(query="!!!"), today=tr.TODAY)
            assert none["notes"] == [tr.NOTE_NOT_FOUND]
        finally:
            t.conn.close()


def test_two_exports_of_one_site_are_one_row_with_the_newer_date_and_a_warning(tmp_path):
    ta.run_assemble(tmp_path / "old", aca_rows(), ACA_RESULTS, old=None)
    newer = [dict(r, fetched=NEW) for r in aca_rows()]
    ta.run_assemble(tmp_path / "new", newer, ACA_RESULTS, old=None)
    paths = tree_files(tmp_path / "tree")
    inputs = [(tmp_path / s / "out" / "corpus.json", tmp_path / s / "export") for s in ("old", "new")]
    out = tmp_path / "x.db"
    report = run(paths, out, inputs=inputs)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT site, data_as_of, export FROM sites").fetchall()
        sites = {s for (s,) in conn.execute("SELECT DISTINCT site FROM docs")}
    finally:
        conn.close()
    assert rows == [("c.nknu.edu.tw/aca", "2026-11-07", str(inputs[1][1]))] and sites == {"c.nknu.edu.tw/aca"}
    want = (f"2 份导出是同一个网站 c.nknu.edu.tw/aca：{inputs[0][1]}、{inputs[1][1]}；资料截至和缺漏数用资料截至较新的 "
            f"{inputs[1][1]}")
    assert [w for w in report.warnings if "同一个网站" in w] == [want]


def test_serial_ids_build_alone_only(tmp_path):
    # a corpus with ids of its own (the 10-29 one, a corpus assembled before plan 2026-11-08 T1): its p1 and another
    # export's p1 name other things, so it is never merged with another input
    one = tb.make_inputs(tmp_path / "one")
    two = tb.make_inputs(tmp_path / "two")
    with pytest.raises(BuildError, match="流水号"):
        build_sites([(one["corpus"], one["export"]), (two["corpus"], two["export"])], tmp_path / "x.db",
                    aliases=one["aliases"], today=TODAY)
    assert not (tmp_path / "x.db").exists()


def test_a_collision_across_inputs_stops_the_build(tmp_path):
    paths = two_exports(tmp_path)
    corpus = paths["inputs"][1][0]
    records = json.loads(corpus.read_text(encoding="utf-8"))
    form = next(r for r in records if r["url"] == FORM)
    form["key"] = "url:c.nknu.edu.tw/aca/uploadfile/news/578/other.pdf"  # another key with FORM's id
    corpus.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(BuildError, match="编号撞了"):
        run(paths, tmp_path / "x.db")
    assert not (tmp_path / "x.db").exists()


def test_no_export_is_written_and_one_input_is_needed(tmp_path):
    paths = two_exports(tmp_path)
    with pytest.raises(ValueError, match="refusing"):
        run(paths, paths["inputs"][1][1] / "aca.db")  # inside the second export
    with pytest.raises(ValueError, match="至少"):
        run(paths, tmp_path / "x.db", inputs=[])


def test_a_corpus_paired_with_another_sites_export_stops_the_build(tmp_path, capsys):
    # review of T4 (11-08): the guard of _read_input had no test. 教务处's corpus with 生輔組's export would read its
    # files and notice fields in the wrong folder: BuildError, nothing written, and the command line exits 2
    paths = two_exports(tmp_path)
    (aca_corpus, _), (_, web_export) = paths["inputs"]
    out = tmp_path / "o" / "aca.db"
    with pytest.raises(BuildError, match="要配成对"):
        run(paths, out, inputs=[(aca_corpus, web_export)])
    assert not out.exists() and not out.with_name("aca.db.tmp").exists()
    rest = ["--out", str(out), "--aliases", str(paths["aliases"]), "--server-dates", str(paths["server"]),
            "--site-list", str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main(["--input", f"{aca_corpus}::{web_export}", *rest]) == 2
    assert "要配成对" in capsys.readouterr().err and not out.exists()


SAME = "2026-11-05T10:00:00+08:00"  # one archive's row in both exports: the 抓取时间 they share on the lab PC


def news_exports(tmp_path: Path) -> dict[str, tuple[Path, Path]]:
    """教务处's export (a notice of 和平教務組 linking a file on the announcement platform) and the platform's own
    export (a page linking the same file), both holding the file's one archive row, assembled (the T4 review's
    scratch build)."""
    notice = ACA + "newsdetail.aspx?nid=900&gi=2"
    form = {"type": "附件", "url": NEWS_FILE, "file": "files/0002-form.pdf", "data": ta.PDF + b"same",
            "title": "form.pdf", "fmt": "pdf", "linktext": "申請表", "fetched": SAME}
    aca = [{"type": "网页", "url": notice, "file": "pages/0001.html", "title": "申請表下載", "fetched": SAME,
            "data": ta.notice_html(body=f'<p>請下載<a href="{NEWS_FILE}">申請表</a></p>', title="申請表下載",
                                   added="2026/10/1 上午 09:00:00")},
           {**form, "linked": [notice]}]
    news = [{"type": "网页", "url": NEWS_PAGE, "file": "pages/0001.html", "title": "公告", "fetched": SAME,
             "data": b"<html><body><p>O</p></body></html>"},
            {**form, "linked": [NEWS_PAGE]}]
    ta.run_assemble(tmp_path / "aca", aca, {"1": (PAGE, "申請表下載\n請下載申請表"), "2": (PDF, "申請表\n" + ta.BODY)},
                    old=None)
    ta.run_assemble(tmp_path / "news", news, {"1": (PAGE, "公告"), "2": (PDF, "申請表\n" + ta.BODY)}, old=None,
                    site="news.nknu.edu.tw", name="全校公告")
    return {s: (tmp_path / s / "out" / "corpus.json", tmp_path / s / "export") for s in ("aca", "news")}


def test_a_file_on_the_announcement_platform_stays_the_units_that_brought_it(tmp_path):
    # review of T4 (11-08): 教务处's export alone gave ('教務處', '和平教務組'); with the platform's export too, in
    # either order, it gave ('秘書室', '', news.nknu.edu.tw). Now 教务处's record stays: its unit, 組別 and site
    exports = news_exports(tmp_path)
    paths = tree_files(tmp_path / "tree")
    for n, order in enumerate((("aca",), ("aca", "news"), ("news", "aca"))):
        out = tmp_path / f"x{n}.db"
        run(paths, out, inputs=[exports[s] for s in order])
        conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT unit, grp, site, unit_line FROM docs WHERE url = ?", (NEWS_FILE,)).fetchone()
            page = conn.execute("SELECT unit, site FROM docs WHERE url = ?", (NEWS_PAGE,)).fetchone()
        finally:
            conn.close()
        assert row == ("教務處", "和平教務組", "c.nknu.edu.tw/aca", "單位：教務處 和平教務組（和平校區教務組）"), order
        # the platform's own page, only in its own export: the platform's row of the site table
        assert page == (("秘書室", "news.nknu.edu.tw") if "news" in order else None), order


def test_a_page_of_sso_common_parts_stays_the_departments_that_brought_it(tmp_path):
    # review of T4 (11-08): a page of sso.nknu.edu.tw/services (shared: the school) in a department's export and in
    # the services export: the department's unit, its words and its site, in either order
    soft = hand_input(tmp_path / "soft", "sso.nknu.edu.tw/site/207016", "软体工程与管理学系品保网站",
                      [(CHAIR + "&page=11", "師資介紹", STAFF_TEXT), (COMMON, "系學會活動", "活動報名")])
    common = hand_input(tmp_path / "common", "sso.nknu.edu.tw/services", "SSO 共用模块",
                        [(COMMON, "系學會活動", "活動報名")])
    paths = tree_files(tmp_path / "tree")
    for n, inputs in enumerate(([soft, common], [common, soft])):
        out = tmp_path / f"x{n}.db"
        run(paths, out, inputs=inputs)
        conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT unit, site, unit_line FROM docs WHERE url = ?", (COMMON,)).fetchone()
        finally:
            conn.close()
        assert row == (tb.SOFT, "sso.nknu.edu.tw/site/207016", "單位：軟體工程與管理學系（軟體系、軟工系）"), n


def test_a_later_re_post_on_another_site_keeps_the_files_group(tmp_path):
    # review of T4 (11-08): the 生輔組 detail dated after 教務處's notice became the file's parent (the latest-dated
    # page) and the file lost 和平教務組: ('教務處', '') with 教务处's export and 生輔組's, ('教務處', '和平教務組')
    # with 教务处's alone. Its 組別 stays now; its parent and inherited date are the re-post's (the owner's call)
    web = web_rows()
    web[0] = {**web[0], "date": "2026-10-20", "date_raw": "發布日期：2026-10-20"}
    paths = two_exports(tmp_path, web=web)
    out = tmp_path / "x.db"
    run(paths, out)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT unit, grp, parent_id, ident_date, unit_line FROM docs WHERE doc_id = ?",
                           (paths["form"],)).fetchone()
    finally:
        conn.close()
    assert row == ("教務處", "和平教務組", paths["detail"], "2026-10-20", "單位：教務處 和平教務組（和平校區教務組）")


# ---------------------------------------------------------------- the command line

def test_main_takes_input_pairs(tmp_path, capsys):
    paths = two_exports(tmp_path)
    out = tmp_path / "o" / "aca.db"
    pairs = [arg for corpus, export in paths["inputs"] for arg in ("--input", f"{corpus}::{export}")]
    rest = ["--out", str(out), "--aliases", str(paths["aliases"]), "--server-dates", str(paths["server"]),
            "--site-list", str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main([*pairs, *rest]) == 0
    report = json.loads((tmp_path / "o" / "build_report.json").read_text(encoding="utf-8"))
    assert [s["site"] for s in report["sites"]] == ["c.nknu.edu.tw/aca", "sso.nknu.edu.tw/web/595"]
    assert report["passage"]["version"] == PASSAGE_VERSION == "2"
    printed = capsys.readouterr().out
    assert "网站 2 个" in printed
    (corpus, export), _ = paths["inputs"]
    for bad in (["--input", str(corpus)],  # no ::
                ["--input", f"{corpus}::{tmp_path / 'none'}"],  # no export there
                ["--input", f"{tmp_path / 'none.json'}::{export}"],  # no corpus there
                ["--input", f"{corpus}::{export}", "--corpus", str(corpus)]):  # one form or the other
        assert build.main([*bad, *rest]) == 2, bad
    err = capsys.readouterr().err
    assert "CORPUS::EXPORT" in err and "--corpus" in err


# ---------------------------------------------------------------- the unit patch (owner 11-08 04:08)

def doc(unit: str, grp: str = "", **more) -> Document:
    return Document("dp1", "page", "page", "師資介紹", "https://x.invalid/", unit, grp, None, "", "", True, "x", **more)


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    paths = tree_files(tmp_path_factory.mktemp("tree"))
    return load_units(paths["sites"], paths["units"])


def test_unit_words(tree):
    soft = unit_words(doc(tb.SOFT, "系辦公室"), tree)
    assert soft == UnitWords(tb.SOFT, ("軟體系", "軟工系"), "系辦公室", ())
    assert soft.line == "單位：軟體工程與管理學系（軟體系、軟工系） 系辦公室"  # the plan's example, with the made-up aliases
    assert soft.words == (tb.SOFT, "軟體系", "軟工系", "系辦公室")
    # a 組別 naming a section of the unit: its other names too (the publisher, build.publisher_of)
    section = unit_words(doc("教務處", "和平教務組"), tree)
    assert section.line == "單位：教務處 和平教務組（和平校區教務組）"
    assert unit_words(doc("教務處", "和平校區教務組"), tree).line == "單位：教務處 和平校區教務組（和平教務組）"
    # empty parts left out; a 組別 that is the unit's own name or alias is said once
    assert unit_words(doc("教務處"), tree).line == "單位：教務處"
    assert unit_words(doc(tb.SOFT, "軟體系"), tree).line == "單位：軟體工程與管理學系（軟體系、軟工系）"
    assert unit_words(doc(tb.SOFT, "软体系"), tree).line == "單位：軟體工程與管理學系（軟體系、軟工系）"  # any spelling
    assert unit_words(doc("教務處", SCHOOL), tree).line == "單位：教務處"  # a 組別 naming the school says nothing
    # the school is every unit's root: its name and short names on its own documents would draw every query naming
    # the school to them (4 of 293 agent queries of 11-01 named 高師大) 【Claude】; its 組別 stays
    assert unit_words(doc(SCHOOL), tree) == UnitWords() and UnitWords().line == "" and UnitWords().words == ()
    assert unit_words(doc(SCHOOL, "秘書室"), tree).line == "單位：秘書室"
    # without a unit tree (no site list): the unit as written, no short names; the school still left out
    assert unit_words(doc("教務處", "和平教務組"), None).line == "單位：教務處 和平教務組"
    assert unit_words(doc(SCHOOL), None) == UnitWords()
    # a unit the tree does not have: as written
    assert unit_words(doc("不在樹上的單位"), tree).line == "單位：不在樹上的單位"


@pytest.fixture(scope="module")
def office_tree(tmp_path_factory):
    paths = tree_files(tmp_path_factory.mktemp("office"), OFFICE_SITES, OFFICE_YAML)
    return load_units(paths["sites"], paths["units"])


def test_a_sections_unit_words_put_its_office_first(office_tree):
    # review of T4 (11-08): a section runs its own site (生活輔導組's web/595), so its documents said only
    # 「單位：生活輔導組」 and 「學務處 獎學金」 found nothing by their words, while 教務處's say 「單位：教務處 和平教務組」.
    # A unit that is a section of an office (a 行政组别 under a 行政单位) comes after the office's names 【Claude】
    life = unit_words(doc("生活輔導組"), office_tree)
    assert life == UnitWords("生活輔導組", ("生輔組",), office="學生事務處", office_aliases=("學務處",))
    assert life.line == "單位：學生事務處（學務處） 生活輔導組（生輔組）"  # the review's example
    assert life.words == ("學生事務處", "學務處", "生活輔導組", "生輔組")
    assert build.office_of("生活輔導組", office_tree) == "學生事務處"
    assert build.office_of("學生事務處", office_tree) is build.office_of(tb.SOFT, office_tree) is None
    # a 組別 naming the office, in any spelling, is said once; another one follows the unit
    assert unit_words(doc("生活輔導組", "學務處"), office_tree).line == life.line
    assert unit_words(doc("生活輔導組", "學生事务处"), office_tree).line == life.line
    assert unit_words(doc("生活輔導組", "宿舍服務"), office_tree).line == life.line + " 宿舍服務"
    # an office and a department: nothing above them, as before
    assert unit_words(doc("學生事務處"), office_tree).line == "單位：學生事務處（學務處）"
    assert unit_words(doc(tb.SOFT), office_tree).line == "單位：軟體工程與管理學系（軟體系、軟工系）"
    # 教務處's documents: the unit is the office, the section its 組別 (unchanged)
    assert unit_words(doc("教務處", "和平教務組"), office_tree).line == "單位：教務處 和平教務組（和平校區教務組）"
    # a short name that only writes the office before the name says nothing the line does not (the site list's
    # 「總務處 出納組」), as the unit and as the 組別 【Claude】
    assert office_tree["出納組"].aliases == ("總務處 出納組",)
    assert unit_words(doc("出納組"), office_tree) == UnitWords("出納組", (), office="總務處")
    assert unit_words(doc("出納組"), office_tree).line == "單位：總務處 出納組"
    assert unit_words(doc("總務處", "出納組"), office_tree).line == "單位：總務處 出納組"


def test_passage_puts_the_unit_line_second():
    unit = "單位：軟體工程與管理學系（軟體系） 系辦公室"
    assert passage("師資介紹", "教師", "師資 | 教師介紹", "專任", "", "本系專任教師", unit=unit) == \
        f"師資介紹\n{unit}\n教師\n師資 | 教師介紹\n專任\n本系專任教師"
    assert passage("t", "", "", "", "", "x") == "t\nx"  # no unit line: as before (a database built before T4)


def test_a_database_without_unit_lines_keeps_its_passages(tmp_path):
    # the 11-04 and 11-08 builds (pipeline-3 and before): no docs.unit_line, so the reranker reads their passages as
    # bge-m3 embedded them, and a vector run on one says passage version 1
    from tests.content.test_embed import FakeEmbedder, tiny_db

    conn = tiny_db(tmp_path / "old.db", [("dp1", "停修公告", "停修申請")])
    try:
        conn.execute("ALTER TABLE docs DROP COLUMN unit_line")
        assert chunk_passages(conn, [1]) == {1: "停修公告\n停修申請"}
        embed_chunks(conn, FakeEmbedder())
        assert conn.execute("SELECT value FROM meta WHERE key = 'vec_passage'").fetchone() == ("1",)
    finally:
        conn.close()
    new = tiny_db(tmp_path / "new.db", [("dp1", "停修公告", "停修申請")])
    try:
        new.execute("UPDATE docs SET unit_line = '單位：教務處 和平教務組'")
        assert chunk_passages(new, [1]) == {1: "停修公告\n單位：教務處 和平教務組\n停修申請"}
        embed_chunks(new, FakeEmbedder())  # 11-08: the vectors hold the text without the unit line by default
        assert new.execute("SELECT value FROM meta WHERE key = 'vec_passage'").fetchone() == ("1",)
        embed_chunks(new, FakeEmbedder(), unit=True)
        assert new.execute("SELECT value FROM meta WHERE key = 'vec_passage'").fetchone() == (PASSAGE_VERSION,)
    finally:
        new.close()


CHAIR = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=207016"  # site/207016: 軟體工程與管理學系
CHEM = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=203012"  # site/203012: 化學系
STAFF_TEXT = "本系專任教師\nO老師 研究室分機 1234\nO老師 研究室分機 1235"


def hand_input(root: Path, site: str, name: str, pages: list[tuple[str, str, str]]) -> tuple[Path, Path]:
    """A corpus written by hand with stable ids and an export of its manifest only: pages (url, title, text)."""
    export = root / "export"
    export.mkdir(parents=True)
    items, records = [], []
    for n, (url, title, text) in enumerate(pages, start=1):
        key = ids.location_key(url)
        items.append({"序号": str(n), "类型": "网页", "网址": url, "最后落地网址": "", "本地档案": f"pages/{n:04d}.html",
                      "抓取时间": NEW})
        records.append({"id": ids.record_id(key), "key": key, "kind": "page:sso_clv2", "url": url,
                        "file": f"pages/{n:04d}.html", "title": title, "text": text, "site": site, "fetched": NEW})
    (export / "manifest.json").write_text(json.dumps({"网站": site, "名称": name, "项目": items}, ensure_ascii=False),
                                          encoding="utf-8")
    (root / "corpus.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return root / "corpus.json", export


def department_inputs(tmp_path: Path) -> list[tuple[Path, Path]]:
    """Two departments' exports (品保網站, cLv2): 軟體系's 師資介紹 and 交通資訊, 化學系's 師資介紹 and six more pages."""
    soft = hand_input(tmp_path / "soft", "sso.nknu.edu.tw/site/207016", "软体工程与管理学系品保网站",
                      [(CHAIR + "&page=11", "師資介紹", STAFF_TEXT), (CHAIR + "&page=12", "交通資訊", "搭乘公車")])
    # more pages, so a word on two of them is a rare one for bm25 (in three rows its idf is nil)
    chem = hand_input(tmp_path / "chem", "sso.nknu.edu.tw/site/203012", "化学系品保网站",
                      [(CHEM + "&page=11", "師資介紹", STAFF_TEXT + "\n本系師資陣容堅強"),
                       *[(CHEM + f"&page={20 + i}", f"公告{i}", "實驗課程安排") for i in range(6)]])
    return [soft, chem]


DEPARTMENT_IDS = {name: "d" + ids.record_id(ids.location_key(url)) for name, url in (
    ("soft", CHAIR + "&page=11"), ("bus", CHAIR + "&page=12"), ("chem", CHEM + "&page=11"))}


@pytest.fixture
def departments(tmp_path):
    out = tmp_path / "x.db"
    report = run(tree_files(tmp_path / "tree"), out, inputs=department_inputs(tmp_path))
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield report, conn, DEPARTMENT_IDS
    conn.close()


def test_a_department_page_is_found_by_its_short_name_though_its_text_never_says_it(departments):
    _, conn, d = departments
    assert conn.execute("SELECT count(*) FROM docs WHERE text LIKE '%軟體%'").fetchone() == (0,)
    hits = {h.doc_id: h for h in search_docs(conn, "軟體系 師資", today=TODAY, aliases=[]).hits}
    # 化學系's page says 師資 twice and leads by its own words without the patch; the unit line puts 軟體系's first,
    # then 軟體系's other page (its unit words alone), then 化學系's (師資 alone). Changed 11-08 10:16 on purpose (owner,
    # N11): a word only the unit words have counts half (RankConfig.unit_cover), so 1.0, 0.67 are 0.67, 0.33 now; and
    # 軟體系 brings 軟體工程與管理學系's words (search.unit_names), whose 學系 finds 化學系's other pages, none covered
    assert list(hits)[:3] == [d["soft"], d["bus"], d["chem"]]
    assert [round(h.coverage, 2) for h in hits.values()] == [0.67, 0.33, 0.33] + [0.0] * 6
    assert search_docs(conn, "化學系 師資", today=TODAY, aliases=[]).hits[0].doc_id == d["chem"]
    title, unit = (t.split() for t in conn.execute(
        "SELECT f.title_tokens, f.unit_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid WHERE c.doc_id = ?",
        (d["soft"],)).fetchone())
    assert {"软体", "体系", "软工", "工系", "管理", "学系"} <= set(unit) and "单位" not in unit  # words, not the label
    assert not {"软体", "软工"} & set(title)  # a column of their own since 11-08 10:16
    line = conn.execute("SELECT unit_line FROM docs WHERE doc_id = ?", (d["soft"],)).fetchone()[0]
    assert line == "單位：軟體工程與管理學系（軟體系、軟工系）"
    chunk = conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?", (d["soft"],)).fetchone()[0]
    assert chunk_passages(conn, [chunk])[chunk].split("\n")[:2] == ["師資介紹", line]  # as the reranker reads it


def test_the_school_is_no_unit_word(tmp_path):
    stray = hand_input(tmp_path / "x", "example.invalid", "", [("https://example.invalid/a", "交通資訊", "搭乘公車")])
    out = tmp_path / "x.db"
    run(tree_files(tmp_path / "tree"), out, inputs=[stray])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        unit, line = conn.execute("SELECT unit, unit_line FROM docs").fetchone()
        title, words = conn.execute("SELECT title_tokens, unit_tokens FROM chunk_fts").fetchone()
    finally:
        conn.close()
    assert (unit, line, words) == (SCHOOL, "", "") and not {"高雄", "师范", "大学"} & set(title.split())


def test_the_report_says_every_passage_changed(departments):
    report, _, _ = departments
    assert report.passage == {"version": PASSAGE_VERSION, "unit_words": True, "unit_in_vectors": False,
                              "note": build.PASSAGE_NOTE}
    # 11-08 (main session): the note says the vectors leave the unit line out unless asked, and what asking costs
    assert PASSAGE_VERSION == "2" and "單位" in report.passage["note"] and "--unit-words-in-vectors" in report.passage[
        "note"] and "都要重算" in report.passage["note"]


def test_a_sections_page_is_found_by_its_offices_short_name(tmp_path):
    # review of T4 (11-08): 「學務處 宿舍」. Without the office in 生活輔導組's unit line, 教務處's page of the same title
    # led: 务处 is a word of every 處's name (教務處's too), and 學務處's own 学务 was nowhere in 生活輔導組's words
    life = hand_input(tmp_path / "life", "sso.nknu.edu.tw/web/595", "学务处生活辅导组",
                      [(tp.WEB + "&menuid=505", "宿舍申請", "申請時程與床位")])
    aca = hand_input(tmp_path / "aca", "c.nknu.edu.tw/aca", "教务处",
                     [(ACA + "page.aspx?pn=1", "宿舍申請", "申請時程請見附件"),
                      *[(ACA + f"page.aspx?pn={10 + i}", f"公告{i}", "選課時程") for i in range(6)]])
    out = tmp_path / "x.db"
    run(tree_files(tmp_path / "tree", OFFICE_SITES, OFFICE_YAML), out, inputs=[life, aca])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        d = {name: "d" + ids.record_id(ids.location_key(url))
             for name, url in (("life", tp.WEB + "&menuid=505"), ("aca", ACA + "page.aspx?pn=1"))}
        hits = search_docs(conn, "學務處 宿舍", today=TODAY, aliases=[]).hits
        line = conn.execute("SELECT unit_line FROM docs WHERE doc_id = ?", (d["life"],)).fetchone()[0]
    finally:
        conn.close()
    assert [h.doc_id for h in hits[:2]] == [d["life"], d["aca"]]
    # 学务 务处 宿舍; 务处 宿舍: changed 11-08 10:16 on purpose (owner, N11), 学务 务处 are only the unit words', which
    # count half (RankConfig.unit_cover): 1.0, 0.67 are 0.67, 0.5 now
    assert [round(h.coverage, 2) for h in hits[:2]] == [0.67, 0.5]
    assert line == "單位：學生事務處（學務處） 生活輔導組（生輔組）"


def test_the_report_lists_the_site_tables_units_without_a_short_name(two, office_tree):
    # review of T4 (11-08): 37 of the 68 units of the site table had no short name, so their unit words lacked one
    # silently. The build report lists them (scope.site_units_no_alias, and meta scope_json), in the table's order
    paths, report, conn = two
    tree = load_units(paths["sites"], paths["units"])
    listed = report.scope["site_units_no_alias"]
    assert listed == build.site_units_without_alias(tree)
    assert json.loads(dict(conn.execute("SELECT key, value FROM meta").fetchall())["scope_json"])[
        "site_units_no_alias"] == listed
    order = list(dict.fromkeys(row.unit for row in sites.table().rows.values()))
    assert listed == [u for u in order if u in listed]  # each once, in the table's order
    assert {"教務處", "生活輔導組", "體育室"} <= set(listed)  # in this made-up tree without one; not in it at all
    assert tb.SOFT not in listed and SCHOOL not in listed  # a short name; the school gives no unit words
    with_short = build.site_units_without_alias(office_tree)  # the same table, the office and its section named
    assert set(listed) - set(with_short) == {"學生事務處", "生活輔導組"}


@pytest.mark.skipif(not build.SITE_LIST.is_file(), reason="no site probe list here (research/ is not in git)")
def test_the_shipped_short_names_of_the_site_tables_units():
    # review of T4 (11-08): the short names config/units.yaml gained for units of the site table, each with the
    # school's own words as evidence 【Claude】 (課指組 is not among them: the school writes 課外組)
    tree = load_units(build.SITE_LIST, build.UNITS)
    shorts = {"學務處": "學生事務處", "生輔組": "生活輔導組", "課外組": "課外活動指導組", "衛保組": "衛生保健組",
              "圖資處": "圖書資訊處", "國際處": "國際事務處", "通識中心": "通識教育中心", "原資中心": "原住民族學生資源中心",
              "特教中心": "特殊教育中心", "學輔中心": "學生輔導中心", "軍訓室": "校安中心"}
    assert {short: resolve(short, tree) for short in shorts} == shorts
    assert unit_words(doc("生活輔導組"), tree).line == "單位：學生事務處（學務處） 生活輔導組（生輔組）"
    # the site list's 「師就處 檢定與實習組」 says nothing the line does not
    assert unit_words(doc("檢定與實習組"), tree).line == "單位：師資培育與就業輔導處（師就處） 檢定與實習組"
    left = build.site_units_without_alias(tree)
    assert left and not set(shorts.values()) & set(left) and all(not tree[u].aliases for u in left)


def test_without_unit_words_the_build_leaves_the_whole_patch_out(tmp_path, capsys):
    # review of T4 (11-08), Review Focus 5: the patch was always on, so a move of 教务处's offline_eval on the lab PC
    # could not be told from a regression. build_sites(unit_words=False) / --no-unit-words: no unit line, no unit
    # tokens, passages as version 1's; two builds of one set of inputs measure the patch alone
    inputs = department_inputs(tmp_path)
    paths = tree_files(tmp_path / "tree")
    on, off = tmp_path / "on.db", tmp_path / "off.db"
    run(paths, on, inputs=inputs)
    report = run(paths, off, inputs=inputs, unit_words=False)
    assert report.passage == {"version": PASSAGE_VERSION, "unit_words": False, "unit_in_vectors": False,
                              "note": build.PASSAGE_NOTE_OFF}
    with_words = sqlite3.connect(f"file:{on}?mode=ro", uri=True)
    without = sqlite3.connect(f"file:{off}?mode=ro", uri=True)
    try:
        assert [dict(c.execute("SELECT key, value FROM meta").fetchall())["unit_words"]
                for c in (with_words, without)] == ["on", "off"]
        assert without.execute("SELECT DISTINCT unit_line FROM docs").fetchall() == [("",)]
        chunks = [c for (c,) in with_words.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")]
        assert chunks == [c for (c,) in without.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")]
        lines = dict(with_words.execute("SELECT c.chunk_id, d.unit_line FROM chunks c JOIN docs d USING (doc_id)"))
        a, b = chunk_passages(with_words, chunks), chunk_passages(without, chunks)
        # each passage is the one with words less its unit line: version 1's
        assert all(lines[c] and b[c] == a[c].replace("\n" + lines[c], "", 1) for c in chunks)
        assert not any(UNIT_LABEL in p for p in b.values())
        title, unit = (t.split() for t in without.execute(
            "SELECT f.title_tokens, f.unit_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid"
            " WHERE c.doc_id = ?", (DEPARTMENT_IDS["soft"],)).fetchone())
        assert not {"软体", "体系", "软工", "工系"} & set(title) and unit == []  # unit_tokens since 11-08 10:16
        # without the patch 化學系's page leads 「軟體系 師資」 by its own words (with it, 軟體系's: the test above)
        assert search_docs(without, "軟體系 師資", today=TODAY, aliases=[]).hits[0].doc_id == DEPARTMENT_IDS["chem"]
    finally:
        with_words.close()
        without.close()
    # the command line
    out = tmp_path / "cli" / "aca.db"
    pairs = [arg for corpus, export in inputs for arg in ("--input", f"{corpus}::{export}")]
    assert build.main([*pairs, "--out", str(out), "--aliases", str(paths["aliases"]), "--server-dates",
                       str(paths["server"]), "--site-list", str(paths["sites"]), "--units", str(paths["units"]),
                       "--no-unit-words"]) == 0
    assert "没加单位词（--no-unit-words）" in capsys.readouterr().out
    assert json.loads(out.with_name("build_report.json").read_text(encoding="utf-8"))["passage"]["unit_words"] is False


def test_without_unit_words_the_vectors_of_a_build_before_serve_every_passage(tmp_path):
    # review of T4 (11-08): with no unit lines the passages are version 1's, so the lab PC's vector cache of the 11-08
    # build (before T4) serves a build with --no-unit-words whole, and only the build with the patch encodes again
    from tests.content.test_embed import FakeEmbedder

    inputs = department_inputs(tmp_path)
    paths = tree_files(tmp_path / "tree")
    cache = tmp_path / "vec_cache.sqlite"
    before = tmp_path / "before.db"  # as a build before T4: no unit_line column, so passage version 1
    run(paths, before, inputs=inputs)
    conn = sqlite3.connect(before)
    try:
        conn.execute("ALTER TABLE docs DROP COLUMN unit_line")
        with VecCache(cache) as vc:
            first = embed_chunks(conn, FakeEmbedder(), cache=vc)
    finally:
        conn.close()
    off = run(paths, tmp_path / "off.db", inputs=inputs, unit_words=False, embed=True, embedder=FakeEmbedder(),
              vec_cache=cache)
    assert (off.vectors["chunks"], off.vectors["cached"], off.vectors["encoded"]) == (first.chunks, first.chunks, 0)
    # 11-08 (main session): with the unit words in the keyword index and the reranker only, the vectors are version
    # 1's too, so the same cache serves them; only --unit-words-in-vectors misses once in full
    on = run(paths, tmp_path / "on.db", inputs=inputs, embed=True, embedder=FakeEmbedder(), vec_cache=cache)
    assert (on.vectors["cached"], on.vectors["encoded"]) == (first.chunks, 0)
    inside = run(paths, tmp_path / "in.db", inputs=inputs, embed=True, embedder=FakeEmbedder(), vec_cache=cache,
                 unit_in_vectors=True)
    assert (inside.vectors["cached"], inside.vectors["encoded"]) == (0, first.encoded)


def test_one_pair_form_still_builds(tmp_path):
    paths = tb.make_inputs(tmp_path)
    report = build_db(paths["corpus"], paths["export"], tmp_path / "x.db", aliases=paths["aliases"], today=TODAY,
                      site_list=paths["sites"], units_config=paths["units"], server_dates=paths["server"])
    assert [s["site"] for s in report.sites] == ["c.nknu.edu.tw/aca"] and report.sites[0]["missing_files"] == 2
