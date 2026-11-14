"""Plan 2026-11-14 T2 item 1 (direction 2026-11-13 第 2 件 1): exact copies, widened.

Until now only pages and attachments of one kind, one title (an attachment's file name) and one text were one
document, and a notice of a detail path never merged. The text rule adds: documents of one kind_class (events aside)
whose text, all whitespace removed, is the same and at least 200 characters long are one document, across sites and
inside one, notices too, whatever their titles. The earliest by its identity date is kept (an undated one after every
dated one; on a tie the address's own site, then today's order); the others go to doc_alias and to the kept one's
copies, and their titles stay findable in its link texts. build_sites(copy_rule="title") / --copy-rule title builds as
before. The sentences are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from nknu.content import build, ids
from nknu.content.corpus import Event, PageMeta, Record
from nknu.content.dates import LIST_ROW, OWN_FIELD
from nknu.content.documents import LINK_SEP, build_documents
from nknu.retrieval import respond
from nknu.retrieval.respond import build_response, read_response
from nknu.retrieval.search import search_docs
from tests.content import test_build as tb
from tests.content import test_multisite_1108 as tm

TODAY = date(2026, 11, 14)
FETCHED = "2026-11-01T04:41:00+08:00"
ACA, TG = "https://c.nknu.edu.tw/aca/", "https://c.nknu.edu.tw/tg/"
ACA_SITE, TG_SITE = "c.nknu.edu.tw/aca", "c.nknu.edu.tw/tg"
WEB = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=595&selpage=MoreNews&newsidx="  # 生活輔導組's notices
WEB_SITE = "sso.nknu.edu.tw/web/595"
NEWS = "https://sso.nknu.edu.tw/news/lastnews.aspx?unid="  # the announcement platform: a shared one (site_units.yaml)
NEWS_SITE = "sso.nknu.edu.tw/news"
CHAIR = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=207016&page="  # 軟體系's 品保網站 pages
CHAIR_SITE = "sso.nknu.edu.tw/site/207016"
PDF = "附件:pdf:有文字层"
SCAN = "附件:pdf:文字很少(多半是扫描+少量字)"  # a scan with a few words: the words are not the document

# 61 characters, four times: 244 without whitespace
LONG = ("圖書館暑假期間調整開放時間，週一至週五上午九時至下午五時開放，週六及週日休館；"
        "借書期限順延至開學後第一週，逾期不另計罰款。") * 4
SPACED = LONG.replace("，", "，\n").replace("；", "； \u3000").replace("。", "。\n\n")  # other whitespace, the same words
OTHER = ("學生宿舍寒假期間停止供應熱水的時段為每日上午九時至下午四時，請住宿同學提前安排盥洗時間；"
         "如有設備故障請至宿舍服務台登記報修。") * 4
SHORT = "社團博覽會於操場舉行，歡迎參加。"
# the owner, 11-14 23:55 (tests/content/test_twins_1115.py): by_rule counts the groups a platform twin made too
NO_TWIN = {"groups": 0, "removed": 0}
# 11-15 (tests/content/test_file_titles_1115.py): the copies' link names the kept documents took over
NO_NAMES = {"on": True, "documents": 0, "names": 0}


def rec(rid: str, kind: str, url: str, title: str, text: str, site: str = "", day: str = "",
        source: str = OWN_FIELD, **more) -> Record:
    """A record with a serial id; day: the date the export read on its page, by the rule source names."""
    dated = {"page_date": day, "page_date_raw": day, "date_source": source} if day else {}
    fields = {"linktext": "", "linked": (), "site": site, "fetched": FETCHED, **dated, **more}
    return Record(rid, kind, url, f"pages/{rid}.html", title, text, **fields)


def merged(records: list[Record], metas: dict | None = None, events: tuple[Event, ...] = (),
           rule: str = build.COPY_TEXT) -> tuple[list[str], dict[str, list[str]], dict[str, int], dict]:
    """The ids kept after build._merge_copies, the copies under each, the excluded counts and the documents by id."""
    docs, excluded = build_documents(records, metas or {}, list(events))
    rows = build._rows(docs, {"e" + ev.id: ev for ev in events}, {})
    kept, copies = build._merge_copies(rows, lambda _: None, excluded, rule)
    return ([r.doc.doc_id for r in kept], {k: [c.doc.doc_id for c in v] for k, v in copies.items()}, excluded,
            {r.doc.doc_id: r.doc for r in kept})


# ---------------------------------------------------------------- which documents are one

def test_the_rules_and_the_least_length():
    assert build.COPY_RULES == (build.COPY_TEXT, build.COPY_TITLE) == ("text", "title")
    assert build.COPY_MIN_CHARS == 200
    assert build._bare("甲 乙\n\t丙\u3000丁\xa0戊\r\n己") == "甲乙丙丁戊己"
    assert len(build._bare(LONG)) == 244 and build._bare(SPACED) == build._bare(LONG) and SPACED != LONG


def test_notices_of_two_sites_with_one_text_are_one_document_whatever_their_titles():
    records = [rec("p1", "detail:sso_webidx", WEB + "1", "暑假開館時間", LONG, WEB_SITE, "2026-03-01"),
               rec("p2", "detail:sso_news", NEWS + "AB12", "【轉知】暑假開館時間", SPACED, NEWS_SITE, "2026-03-03"),
               rec("p3", "page:sso_clv2", CHAIR + "11", "開館時間", LONG, CHAIR_SITE),  # a page: another kind_class
               rec("p4", "detail:sso_webidx", WEB + "2", "暑假開館時間", LONG + "另訂之。", WEB_SITE, "2026-03-01")]
    kept, copies, excluded, docs = merged(records)
    assert (kept, copies) == (["dp1", "dp3", "dp4"], {"dp1": ["dp2"]})
    assert excluded == {"detail:sso_news" + build.COPY: 1}
    assert docs["dp1"].linktext == "【轉知】暑假開館時間"  # the copy's title is still found by search
    # today's rule: a notice of a detail path never merges, nor do two titles
    kept, copies, excluded, _ = merged(records, rule=build.COPY_TITLE)
    assert (kept, copies, excluded) == (["dp1", "dp2", "dp3", "dp4"], {}, {})


def test_under_two_hundred_characters_the_titles_must_still_be_one():
    brief = LONG[:199]
    pages = [rec("p1", "page:sso_clv2", CHAIR + "1", "開館時間", brief, CHAIR_SITE),
             rec("p2", "page:sso_clv2", CHAIR + "2", "暑假開館", brief, CHAIR_SITE),
             rec("p3", "page:sso_clv2", CHAIR + "3", "開館時間", brief, CHAIR_SITE)]
    for rule in build.COPY_RULES:
        kept, copies, excluded, _ = merged(pages, rule=rule)
        assert (kept, copies) == (["dp1", "dp2"], {"dp1": ["dp3"]}), rule  # one title and text: today's rule
        assert excluded == {"page:sso_clv2" + build.COPY: 1}
    # one character more and the text rule takes all three
    longer = [rec(p.id, p.kind, p.url, p.title, LONG[:200], CHAIR_SITE) for p in pages]
    assert merged(longer)[:2] == (["dp1"], {"dp1": ["dp2", "dp3"]})
    assert merged(longer, rule=build.COPY_TITLE)[:2] == (["dp1", "dp2"], {"dp1": ["dp3"]})


def test_events_and_files_whose_words_are_not_the_document_are_no_part_of_the_text_rule():
    title = LONG[:61] * 4  # an event's whole text is its title
    events = (Event("ev1", title, date(2026, 9, 1), date(2026, 9, 1), "https://x.invalid/1"),
              Event("ev2", title, date(2027, 9, 1), date(2027, 9, 1), "https://x.invalid/2"))
    scans = [rec("a1", SCAN, ACA + "f/1/甲.pdf", "甲.pdf", LONG, ACA_SITE),
             rec("a2", SCAN, ACA + "f/2/乙.pdf", "乙.pdf", LONG, ACA_SITE),  # another scan with the same few words
             rec("a3", SCAN, ACA + "f/3/甲.pdf", "甲.pdf", LONG, ACA_SITE)]  # one file name too: today's rule
    kept, copies, excluded, docs = merged(scans, events=events)
    assert not docs["da1"].has_text
    assert (kept, copies) == (["da1", "da2", "eev1", "eev2"], {"da1": ["da3"]})
    assert excluded == {SCAN + build.COPY: 1}


def test_a_group_of_each_rule_sharing_a_document_is_one_group():
    # the scan has the file name and the words of the first file (today's rule), the third file its text alone (the
    # text rule): one document, the text rule only widens
    files = [rec("a1", PDF, ACA + "f/1/甲.pdf", "甲.pdf", LONG, ACA_SITE),
             rec("a2", SCAN, ACA + "f/2/甲.pdf", "甲.pdf", LONG, ACA_SITE),
             rec("a3", PDF, ACA + "f/3/丙.pdf", "丙.pdf", SPACED, ACA_SITE),
             rec("a4", PDF, ACA + "f/4/丁.pdf", "丁.pdf", OTHER, ACA_SITE)]
    kept, copies, excluded, docs = merged(files)
    assert (kept, copies) == (["da1", "da4"], {"da1": ["da2", "da3"]})
    assert excluded == {SCAN + build.COPY: 1, PDF + build.COPY: 1} and docs["da1"].linktext == "丙.pdf"
    assert merged(files, rule=build.COPY_TITLE)[:2] == (["da1", "da3", "da4"], {"da1": ["da2"]})


# ---------------------------------------------------------------- which one is kept

def page(rid: str, n: int, title: str, day: str = "", text: str = LONG, site: str = CHAIR_SITE) -> Record:
    return rec(rid, "page:sso_clv2", CHAIR + str(n), title, text, site, day, LIST_ROW)


def test_the_earliest_dated_one_is_kept_and_an_undated_one_comes_after_every_dated_one():
    pages = [page("p1", 1, "甲"), page("p2", 2, "乙", "2026-05-01"), page("p3", 3, "丙", "2026-02-01"),
             page("p4", 4, "丁")]
    kept, copies, _, docs = merged(pages)
    assert (kept, copies) == (["dp3"], {"dp3": ["dp2", "dp1", "dp4"]})  # the copies newest first, the undated last
    assert docs["dp3"].linktext == LINK_SEP.join(["乙", "甲", "丁"])
    # no date anywhere: the first in corpus order, as before
    assert merged([page("p1", 1, "甲"), page("p2", 2, "乙")])[:2] == (["dp1"], {"dp1": ["dp2"]})


def test_groups_only_todays_rule_makes_keep_todays_choice():
    # one title and a text under 200 characters: the latest dated one stays, as before, under either rule 【Claude】
    pages = [page("p1", 1, "社團博覽會", "2026-02-01", SHORT), page("p2", 2, "社團博覽會", "2026-05-01", SHORT)]
    for rule in build.COPY_RULES:
        assert merged(pages, rule=rule)[:2] == (["dp2"], {"dp2": ["dp1"]}), rule
    # the same two with a text the text rule takes: the earliest
    long_pages = [page("p1", 1, "社團博覽會", "2026-02-01"), page("p2", 2, "社團博覽會", "2026-05-01")]
    assert merged(long_pages)[:2] == (["dp1"], {"dp1": ["dp2"]})
    assert merged(long_pages, rule=build.COPY_TITLE)[:2] == (["dp2"], {"dp2": ["dp1"]})


def test_on_one_day_the_address_own_site_comes_before_a_shared_platform_then_todays_order():
    day = "2026-03-01"
    posted = rec("p1", "detail:sso_news", NEWS + "AB12", "轉知", LONG, NEWS_SITE, day)  # the platform's own export
    own = rec("p2", "detail:sso_webidx", WEB + "1", "公告", LONG, WEB_SITE, day)  # its unit's own site
    brought = rec("p3", "detail:sso_news", NEWS + "CD34", "轉知二", LONG, WEB_SITE, day)  # the platform's, brought
    assert merged([posted, brought, own])[:2] == (["dp2"], {"dp2": ["dp1", "dp3"]})
    assert merged([posted, brought])[:2] == (["dp3"], {"dp3": ["dp1"]})  # the export that brought it, then its own
    # one day, one rank: the first in corpus order
    twins = [rec("p1", "detail:sso_webidx", WEB + "1", "甲", LONG, WEB_SITE, day),
             rec("p2", "detail:sso_webidx", WEB + "2", "乙", LONG, WEB_SITE, day)]
    assert merged(twins)[:2] == (["dp1"], {"dp1": ["dp2"]})


def test_on_one_day_a_file_on_a_notice_comes_before_one_dated_by_the_server_alone():
    notice = rec("p1", "newsdetail", ACA + "newsdetail.aspx?nid=1&gi=2", "開館公告", "請見附件", ACA_SITE)
    metas = {"p1": PageMeta("和平教務組", "開館公告", "2026/3/1 上午 09:00:00", date(2026, 3, 1), ())}
    loose = rec("a1", PDF, ACA + "f/1/甲.pdf", "甲.pdf", LONG, ACA_SITE, modified="2026-03-01T01:00:00+08:00")
    hung = rec("a2", PDF, ACA + "f/2/乙.pdf", "乙.pdf", LONG, ACA_SITE, linked=(notice.url,))
    kept, copies, _, docs = merged([notice, loose, hung], metas)
    assert (kept, copies) == (["dp1", "da2"], {"da2": ["da1"]})  # both 2026-03-01: today's order, the one on a notice
    assert docs["da2"].linktext == "甲.pdf"  # the copy's file name, its only title


def test_a_primary_document_is_kept_before_an_earlier_background_one():
    # the same file in an older archive (an archive member: background) and under a later notice: kept under the
    # member it would leave the primary documents 【Claude】
    notice, meta = cnknu_notice("p1", ACA, 1, "申請公告", "請見附件", date(2026, 3, 1))
    member = rec("m1", PDF, ACA + "f/forms.zip#表/申請表.pdf", "申請表.pdf", LONG, ACA_SITE,
                 modified="2026-01-05T01:00:00+08:00")
    loose = rec("a2", PDF, ACA + "f/2/申請表單.pdf", "申請表單.pdf", LONG, ACA_SITE, linked=(notice.url,))
    kept, copies, _, docs = merged([notice, member, loose], {"p1": meta})
    assert (kept, copies) == (["dp1", "da2"], {"da2": ["dm1"]})
    assert build.tier_of("da2", "", docs["da2"].kind_raw) == "primary"
    # two background ones: the earliest, as in any group
    older = rec("m2", PDF, ACA + "f/old.zip#申請表.pdf", "申請表.pdf", LONG, ACA_SITE,
                modified="2025-06-05T01:00:00+08:00")
    assert merged([member, older])[:2] == (["dm2"], {"dm2": ["dm1"]})


# ---------------------------------------------------------------- what the kept one takes over

def cnknu_notice(rid: str, base: str, nid: int, title: str, text: str, day: date) -> tuple[Record, PageMeta]:
    site = base.split("//", 1)[1].rstrip("/")
    raw = f"{day.year}/{day.month}/{day.day} 上午 09:00:00"
    return (rec(rid, "newsdetail", f"{base}newsdetail.aspx?nid={nid}&gi=2", title, text, site),
            PageMeta("", title, raw, day, ()))


def test_a_copys_title_and_file_name_stay_findable_and_what_hung_on_a_dropped_notice_hangs_on_the_kept_one():
    n1, m1 = cnknu_notice("p1", ACA, 1, "暑假開館公告", OTHER, date(2026, 3, 1))
    n2, m2 = cnknu_notice("p2", TG, 9, "轉知：暑假開館", OTHER, date(2026, 3, 5))  # the same notice on another site
    f1 = rec("a1", PDF, ACA + "uploadfile/news/1/附件1.pdf", "附件1.pdf", LONG, ACA_SITE, linked=(n1.url,),
             zones={n1.url: "attachments"}, contexts={n1.url: "暑假開館時間表"}, linktext="暑假開館時間表")
    f2 = rec("a2", PDF, TG + "uploadfile/news/9/library_summer.pdf", "library_summer.pdf", SPACED, TG_SITE,
             linked=(n2.url,), zones={n2.url: "attachments"}, contexts={n2.url: "圖書館暑假開館表"},
             linktext="圖書館暑假開館表")
    f3 = rec("a3", PDF, TG + "uploadfile/news/9/map.pdf", "map.pdf", "校區平面圖", TG_SITE, linked=(n2.url,),
             zones={n2.url: "content"}, contexts={n2.url: "平面圖"}, linktext="平面圖")
    kept, copies, excluded, docs = merged([n1, n2, f1, f2, f3], {"p1": m1, "p2": m2})
    assert (kept, copies) == (["dp1", "da1", "da3"], {"dp1": ["dp2"], "da1": ["da2"]})
    assert excluded == {"newsdetail" + build.COPY: 1, PDF + build.COPY: 1}
    assert docs["dp1"].linktext == "轉知：暑假開館"
    # the kept file: the copy's title (what its page calls it) and its file name, after its own
    assert docs["da1"].title == "暑假開館時間表"
    assert docs["da1"].linktext.split(LINK_SEP) == ["暑假開館時間表", "附件1.pdf", "圖書館暑假開館表", "library_summer.pdf"]
    assert docs["da1"].refs == (("dp1", "attachments", "暑假開館時間表"),)  # the dropped notice's reference is dp1's
    # a file only the dropped notice links hangs on the kept notice, and keeps that notice's title for the index
    assert (docs["da3"].parent_id, docs["da3"].parent_title) == ("dp1", "轉知：暑假開館")
    assert docs["da3"].refs == (("dp1", "content", "平面圖"),)


def test_under_todays_rule_a_file_name_is_never_added_twice():
    # one file name at two addresses, one titled by its page: nothing but the other's title joins the link texts
    n1, m1 = cnknu_notice("p1", ACA, 1, "甲公告", "請見附件一", date(2026, 3, 1))
    n2, m2 = cnknu_notice("p2", ACA, 2, "乙公告", "請見附件二", date(2026, 3, 5))
    f1 = rec("a1", PDF, ACA + "uploadfile/news/1/表.pdf", "表.pdf", SHORT, ACA_SITE, linked=(n1.url,),
             contexts={n1.url: "申請表"}, linktext="申請表")
    f2 = rec("a2", PDF, ACA + "uploadfile/news/2/表.pdf", "表.pdf", SHORT, ACA_SITE, linked=(n2.url,), linktext="表.pdf")
    for rule in build.COPY_RULES:
        kept, copies, _, docs = merged([n1, n2, f1, f2], {"p1": m1, "p2": m2}, rule=rule)
        assert (kept, copies) == (["dp1", "dp2", "da2"], {"da2": ["da1"]}), rule  # the latest notice's: today's choice
        assert docs["da2"].linktext.split(LINK_SEP) == ["表.pdf", "申請表"], rule


# ---------------------------------------------------------------- the build

def site_input(root: Path, site: str, name: str, rows: list[dict]) -> tuple[Path, Path]:
    """A corpus written by hand with stable ids and an export of its manifest only; rows: a record's fields (kind, url,
    title, text and more)."""
    export = root / "export"
    export.mkdir(parents=True)
    items, records = [], []
    for n, row in enumerate(rows, start=1):
        key = ids.location_key(row["url"])
        file = f"pages/{n:04d}.html"
        items.append({"序号": str(n), "类型": "附件" if row["kind"].startswith("附件") else "网页", "网址": row["url"],
                      "最后落地网址": "", "本地档案": file, "抓取时间": FETCHED})
        records.append({"id": ids.record_id(key), "key": key, "file": file, "site": site, "fetched": FETCHED, **row})
    (export / "manifest.json").write_text(json.dumps({"网站": site, "名称": name, "项目": items}, ensure_ascii=False),
                                          encoding="utf-8")
    (root / "corpus.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return root / "corpus.json", export


def doc_id(url: str) -> str:
    return "d" + ids.record_id(ids.location_key(url))


def dated(day: str) -> dict:
    return {"page_date": day, "page_date_raw": day, "date_source": OWN_FIELD}


A_NOTICE, T_NOTICE, N_NOTICE = ACA + "newsdetail.aspx?nid=1&gi=2", TG + "newsdetail.aspx?nid=9", NEWS + "AB12"
A_FILE, T_FILE = ACA + "uploadfile/news/1/附件1.pdf", TG + "uploadfile/news/9/library_summer.pdf"
A_PAGE, A_TWIN = ACA + "page.aspx?pn=50", ACA + "page.aspx?pn=51"


def copy_inputs(tmp_path: Path) -> list[tuple[Path, Path]]:
    """教务处, 成教所 and the announcement platform: one notice on all three (教务处's the earliest), its file on two
    (教务处's the earliest), and two addresses of one short page on 教务处's site (today's rule)."""
    aca = site_input(tmp_path / "aca", ACA_SITE, "教务处", [
        {"kind": "newsdetail", "url": A_NOTICE, "title": "暑假宿舍熱水公告", "text": OTHER, **dated("2026-03-01")},
        {"kind": PDF, "url": A_FILE, "title": "附件1.pdf", "text": LONG, "linked": A_NOTICE,
         "linktext": "暑假開館時間表", "contexts": {A_NOTICE: "暑假開館時間表"}, "zones": {A_NOTICE: "attachments"}},
        {"kind": "page", "url": A_PAGE, "title": "社團博覽會", "text": SHORT},
        {"kind": "page", "url": A_TWIN, "title": "社團博覽會", "text": SHORT}])
    tg = site_input(tmp_path / "tg", TG_SITE, "成人教育研究所", [
        {"kind": "newsdetail", "url": T_NOTICE, "title": "轉知：宿舍熱水", "text": OTHER, **dated("2026-03-05")},
        {"kind": PDF, "url": T_FILE, "title": "library_summer.pdf", "text": SPACED, "linked": T_NOTICE,
         "linktext": "圖書館暑假開館表", "contexts": {T_NOTICE: "圖書館暑假開館表"}, "zones": {T_NOTICE: "content"}},
        {"kind": "page", "url": TG + "page.aspx?pn=3", "title": "所務公告", "text": "本所辦公時間為上午八時至下午五時。"}])
    news = site_input(tmp_path / "news", NEWS_SITE, "全校公告中心", [
        {"kind": "detail:sso_news", "url": N_NOTICE, "title": "【公告】宿舍熱水", "text": OTHER, **dated("2026-03-02")}])
    return [aca, tg, news]


IDS = {name: doc_id(url) for name, url in (
    ("a_notice", A_NOTICE), ("t_notice", T_NOTICE), ("n_notice", N_NOTICE), ("a_file", A_FILE), ("t_file", T_FILE),
    ("a_page", A_PAGE), ("a_twin", A_TWIN))}


@pytest.fixture
def copied(tmp_path):
    out = tmp_path / "db" / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=copy_inputs(tmp_path))
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield report, conn
    conn.close()


def test_the_build_keeps_the_earliest_and_lists_the_others_as_its_copies(copied):
    report, conn = copied
    d = IDS
    kept = {r[0]: r[1:] for r in conn.execute("SELECT doc_id, kind_class, site, ident_date, linktext FROM docs")}
    assert set(kept) == {d["a_notice"], d["a_file"], d["a_page"], doc_id(TG + "page.aspx?pn=3")}
    # the copies' titles join its link texts, in the copies' order (the newest first)
    assert kept[d["a_notice"]] == ("notice", ACA_SITE, "2026-03-01", "轉知：宿舍熱水 | 【公告】宿舍熱水")
    assert kept[d["a_file"]][:3] == ("attachment", ACA_SITE, "2026-03-01")
    assert sorted(conn.execute("SELECT alias, doc_id, why FROM doc_alias")) == sorted([
        (d["t_notice"], d["a_notice"], "copy"), (d["n_notice"], d["a_notice"], "copy"),
        (d["t_file"], d["a_file"], "copy"), (d["a_twin"], d["a_page"], "copy")])
    # each copy with its own address and date, the newest first
    listed = json.loads(conn.execute("SELECT value FROM meta WHERE key = ?", ("copies:" + d["a_notice"],)).fetchone()[0])
    assert listed == [{"id": d["t_notice"], "url": T_NOTICE, "date": "2026-03-05", "date_kind": "published"},
                      {"id": d["n_notice"], "url": N_NOTICE, "date": "2026-03-02", "date_kind": "published"}]
    [file_copy] = json.loads(conn.execute("SELECT value FROM meta WHERE key = ?", ("copies:" + d["a_file"],)).fetchone()[0])
    assert (file_copy["id"], file_copy["url"], file_copy["date"]) == (d["t_file"], T_FILE, "2026-03-05")
    assert file_copy["parent_id"] == d["a_notice"]  # its notice is the kept notice's copy
    assert report.excluded == {"newsdetail" + build.COPY: 1, "detail:sso_news" + build.COPY: 1, PDF + build.COPY: 1,
                               "page" + build.COPY: 1}
    assert report.counts["doc_alias"] == {"copy": 4}


def test_the_report_counts_the_groups_and_each_sites_share(copied):
    report, conn = copied
    counted = dict(report.dedupe["copies"])
    largest = counted.pop("largest")
    assert counted == {
        "rule": "text", "min_chars": 200, "groups": 3, "removed": 4, "cross_site": 2,
        "by_rule": {"text": {"groups": 2, "removed": 3}, "title": {"groups": 1, "removed": 1}, "twin": NO_TWIN},
        "by_kind": {"notice": 2, "page": 1, "attachment": 1},
        "by_site": {ACA_SITE: {"kept": 3, "merged": 1}, TG_SITE: {"kept": 0, "merged": 2},
                    NEWS_SITE: {"kept": 0, "merged": 1}}, "names_carried": NO_NAMES}
    assert list(counted["by_site"]) == [ACA_SITE, TG_SITE, NEWS_SITE]  # the inputs' order
    # the groups with the most copies first, to look at after a build
    assert largest[0] == {"kept": IDS["a_notice"], "site": ACA_SITE, "kind": "notice", "title": "暑假宿舍熱水公告",
                          "copies": 2, "sites": 3, "titles": 3}
    assert [(g["kind"], g["copies"], g["titles"]) for g in largest[1:]] in (
        [("attachment", 1, 2), ("page", 1, 1)], [("page", 1, 1), ("attachment", 1, 2)])
    meta = dict(conn.execute("SELECT key, value FROM meta WHERE key NOT LIKE 'copies:%'"))
    assert meta["copy_rule"] == "text" and json.loads(meta["dedupe_json"]) == report.dedupe


def test_a_copys_title_finds_the_kept_document_and_read_resolves_its_id(copied):
    _, conn = copied
    d = IDS
    result = build_response(conn, search_docs(conn, "library_summer", today=TODAY, aliases=[]), today=TODAY)
    hit = next(r for r in result["results"] if r["id"] == d["a_file"])
    assert [c["id"] for c in hit["copies"]] == [d["t_file"]] and respond.NOTE_COPIES in result["notes"]
    assert d["a_notice"] in [h.doc_id for h in search_docs(conn, "轉知 宿舍熱水", today=TODAY, aliases=[]).hits]
    read = read_response(conn, d["n_notice"], "", today=TODAY)
    assert read["id"] == d["a_notice"] and read["notes"][0] == f"【系統】這個編號是 {d['a_notice']} 的副本，以下是保留的那份。"


def test_rule_title_builds_as_before_and_says_so(tmp_path, capsys):
    paths = tm.tree_files(tmp_path / "tree")
    inputs = copy_inputs(tmp_path)
    out = tmp_path / "db" / "old.db"
    report = tm.run(paths, out, inputs=inputs, copy_rule=build.COPY_TITLE)
    assert report.excluded == {"page" + build.COPY: 1}  # the two addresses of one short page, and nothing else
    assert {k: v for k, v in report.dedupe["copies"].items() if k != "largest"} == {
        "rule": "title", "min_chars": 200, "groups": 1, "removed": 1, "cross_site": 0,
        "by_rule": {"text": {"groups": 0, "removed": 0}, "title": {"groups": 1, "removed": 1}, "twin": NO_TWIN},
        "by_kind": {"page": 1}, "by_site": {ACA_SITE: {"kept": 1, "merged": 1}}, "names_carried": NO_NAMES}
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM docs").fetchone() == (7,)
        assert conn.execute("SELECT value FROM meta WHERE key = 'copy_rule'").fetchone() == ("title",)
    finally:
        conn.close()
    with pytest.raises(ValueError, match="copy_rule"):
        tm.run(paths, tmp_path / "db" / "bad.db", inputs=inputs, copy_rule="loose")
    # the command line: the default is the text rule, --copy-rule title the rule of before
    pairs = [arg for corpus, export in inputs for arg in ("--input", f"{corpus}::{export}")]
    rest = ["--aliases", str(paths["aliases"]), "--server-dates", str(paths["server"]), "--site-list",
            str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main([*pairs, "--out", str(tmp_path / "cli" / "new.db"), *rest]) == 0
    printed = capsys.readouterr().out
    assert "副本合并 3 组、少 4 份（跨网站 2 组" in printed
    assert f"{ACA_SITE} 留 3、并掉 1" in printed and f"{TG_SITE} 留 0、并掉 2" in printed
    saved = json.loads((tmp_path / "cli" / "build_report.json").read_text(encoding="utf-8"))
    assert saved["dedupe"]["copies"]["groups"] == 3 and saved["dedupe"]["copies"]["by_site"][NEWS_SITE] == {
        "kept": 0, "merged": 1}
    assert build.main([*pairs, "--out", str(tmp_path / "cli2" / "old.db"), *rest, "--copy-rule", "title"]) == 0
    printed = capsys.readouterr().out
    assert "副本合并 1 组、少 1 份" in printed and "--copy-rule title" in printed
    with pytest.raises(SystemExit) as stop:  # argparse refuses another rule
        build.main([*pairs, "--out", str(tmp_path / "cli3" / "x.db"), *rest, "--copy-rule", "loose"])
    assert stop.value.code == 2


def test_inputs_without_long_copies_build_the_same_under_both_rules(tmp_path):
    # test_build's export has copies of today's rule only (a short file under two notices, a scan at two addresses):
    # the text rule changes nothing there, the documents, their copies and the aliases are those of the rule of before
    def built(rule: str) -> tuple:
        paths = tb.make_inputs(tmp_path / rule)
        report = tb.run(paths, tmp_path / rule / "out" / "aca.db", copy_rule=rule)
        conn = sqlite3.connect(f"file:{tmp_path / rule / 'out' / 'aca.db'}?mode=ro", uri=True)
        try:
            return (conn.execute("SELECT doc_id, title, linktext, parent_id, ident_date, tier FROM docs ORDER BY doc_id"
                                 ).fetchall(),
                    conn.execute("SELECT * FROM doc_alias ORDER BY alias").fetchall(),
                    conn.execute("SELECT key, value FROM meta WHERE key LIKE 'copies:%' ORDER BY key").fetchall(),
                    conn.execute("SELECT * FROM doc_refs ORDER BY doc_id, ref_id").fetchall(), report.excluded,
                    {k: v for k, v in report.dedupe["copies"].items() if k != "rule"})
        finally:
            conn.close()

    new, old = built(build.COPY_TEXT), built(build.COPY_TITLE)
    assert new == old and new[4] == tb.COPIES_EXCLUDED
    assert new[5]["by_rule"] == {"text": {"groups": 0, "removed": 0}, "title": {"groups": 2, "removed": 2},
                                 "twin": NO_TWIN}
