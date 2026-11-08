"""Sub-step 4 (spec 2026-11-08 §四): the step-3 export's four new manifest columns reach the content build.

- 按钮编号 (§一): a press row is keyed by the crawler's row_key, so two buttons of one list with the same words are two
  identities without an archive number, and their ids no longer may change between exports;
- 日期来源 (§二 2): a date the platform's own field gave (详细页的日期栏) or a notice's row on its list (列表上的日期)
  is trusted without the label rule, and a page the export found such a field on is a notice; any other date (网页上的
  日期字眼, or '' from an older export) keeps the label rule;
- 最后修改时间 (§二 3): an attachment's server date, unless it is within CLOCK_SECONDS of the fetch (the server's clock);
- 来源: carried as written.
An export made before 3c33a79 has none of them, and everything stays as it was. The names in the fixtures are made up
(O masks); no model loads."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from nknu.content import build, ids
from nknu.content.assemble import assemble
from nknu.content.corpus import PageMeta, Record, load_corpus
from nknu.content.dates import (
    FIELD_SOURCE,
    INHERITED_SOURCE,
    LIST_ROW,
    LIST_SOURCE,
    OWN_FIELD,
    PAGE_SOURCE,
    PAGE_WORDS,
    DateFact,
)
from nknu.content.documents import CLOCK_SECONDS, _stated, build_documents
from tests.content import test_assemble as ta

ACA = "https://c.nknu.edu.tw/aca/"
LIST = "https://sso.nknu.edu.tw/services/site/index.aspx?cLv2=100061"
GEO = "https://geo.nknu.edu.tw/news/detail/1326"  # a detail page whose address no detail path names
WEB_DETAIL = "https://sso.nknu.edu.tw/web/595/index.aspx?selpage=MoreNews&newsidx=1326"
FILE = "https://geo.nknu.edu.tw/files/form.pdf"
FETCHED = "2026-10-27T04:41:00+08:00"
SERVER = "伺服器檔案時間"


def rec(rid: str, kind: str, url: str, title: str = "x", linked=(), **more) -> Record:
    more.setdefault("fetched", FETCHED)
    return Record(rid, kind, url, f"pages/{rid}.html", title, title, "", tuple(linked), **more)


def by_id(docs) -> dict:
    return {d.doc_id: d for d in docs}


def press(n: int, words: str, row_key: str = "", archived: str = "") -> dict:
    return {"序号": str(n), "类型": "附件", "网址": f"{LIST}#按钮={words}", "链接文字": words, "按钮编号": row_key,
            "存档编号": archived}


# ---------------------------------------------------------------- 按钮编号 (spec 2026-11-08 §一)

def test_a_press_row_is_keyed_by_its_button_id():
    rows = [{"序号": "1", "类型": "网页", "网址": LIST},
            press(2, "申請表", " 3f2a9c ", "a5"), press(3, "申請表", "77b0e1", "a6"), press(4, "停修", ""),
            {"序号": "5", "类型": "网页", "网址": LIST + "#第2页", "按钮编号": "p2"}]
    keys = ids.manifest_keys(rows)
    assert keys[2] == f"press:{ids.location_key(LIST)}#3f2a9c"  # stripped; the words no part of it
    assert keys[3] == f"press:{ids.location_key(LIST)}#77b0e1"
    assert keys[4] == ids.press_key(LIST, "停修")  # no button id: the words, as before
    assert keys[5] == ids.location_key(LIST + "#第2页")  # a pager row has an address of its own
    assert ids.dup_rows(rows) == set()


def test_rows_sharing_a_button_id_still_get_their_archive_numbers_but_are_not_unstable():
    rows = [press(1, "申請表", "3f2a9c", "a5"), press(2, "申請表", "3f2a9c", "a6"),
            press(3, "停修", "", "a7"), press(4, "停修", "", "a8")]
    keys = ids.manifest_keys(rows)
    base = f"press:{ids.location_key(LIST)}#"
    assert (keys[1], keys[2]) == (base + "3f2a9c#a5", base + "3f2a9c#a6")
    assert (keys[3], keys[4]) == (base + "停修#a7", base + "停修#a8")
    assert ids.dup_rows(rows) == {3, 4}  # only the rows keyed by their words


def test_assemble_counts_no_unstable_ids_when_the_buttons_have_ids(tmp_path):
    pdf = ta.PDF + b"press"
    rows = [{"type": "网页", "url": LIST, "file": "pages/0001.html", "title": "公告",
             "data": ta.notice_html(body="<p>公告</p>")}]
    for n, archived in ((2, "a5"), (3, "a6")):
        rows.append({"type": "附件", "url": f"{LIST}#按钮=申請表（{archived}）", "file": f"files/{n:04d}.pdf",
                     "title": "申請表", "data": pdf + archived.encode(), "linked": [LIST], "linktext": "申請表"})
    extra = {2: {"存档编号": "a5", "按钮编号": "k2"}, 3: {"存档编号": "a6", "按钮编号": "k3"}}
    page = ({"page": {"title": "公告", "album": "", "images": []}}, "公告")
    results = {"1": page} | {str(n): ({"pages": 1, "page_starts": [0]}, ta.BODY) for n in (2, 3)}
    records, report = assemble_with(tmp_path, rows, results, extra)
    files = [r for r in records if r["url"].startswith(LIST + "#")]
    assert [r["key"].rsplit("#", 1)[-1] for r in files] == ["k2", "k3"]
    assert report.unstable_ids == 0


# ---------------------------------------------------------------- the columns reach the corpus

def assemble_with(tmp_path: Path, rows: list[dict], results: dict, extra: dict[int, dict]) -> tuple[list[dict], object]:
    """ta.write_export's export with the step-3 columns of extra (序号 -> {column: value}) added to its rows,
    assembled without the 10-29 fallback: (corpus.json's records, the report)."""
    export = ta.write_export(tmp_path / "export", rows)
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["项目"]:
        item.update(extra.get(int(item["序号"]), {}))
    (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    totext = ta.fake_totext(tmp_path, export, results)
    out = tmp_path / "out" / "corpus.json"
    report = assemble(export, totext, out, fallback=None)
    return json.loads(out.read_text(encoding="utf-8")), report


def test_the_new_columns_reach_the_corpus_and_come_back(tmp_path):
    extra = {1: {"日期来源": OWN_FIELD, "最后修改时间": "2026-10-01T09:00:00+08:00"},
             6: {"最后修改时间": "2023-05-01T10:00:00+08:00", "来源": "10-24 旧存档"},
             7: {"最后修改时间": "2024-02-02T08:00:00+08:00"}}  # the archive: its members carry its row's
    records, _ = assemble_with(tmp_path, ta.base_rows(), ta.base_results(), extra)
    got = {r["url"]: (r["date_source"], r["modified"], r["origin"]) for r in records}
    assert got[ta.NOTICE] == (OWN_FIELD, "2026-10-01T09:00:00+08:00", "")
    assert got[ACA + "uploadfile/news/1/a6.pdf"] == ("", "2023-05-01T10:00:00+08:00", "10-24 旧存档")
    members = [v for u, v in got.items() if u.startswith(ta.ZIP_URL + "#")]
    assert len(members) == 2 and set(members) == {("", "2024-02-02T08:00:00+08:00", "")}
    assert got[ACA + "uploadfile/news/1/a8.pdf"] == ("", "", "")  # no column value: ''
    loaded = {r.url: r for r in load_corpus(tmp_path / "out" / "corpus.json")}
    assert (loaded[ta.NOTICE].date_source, loaded[ta.NOTICE].modified) == (OWN_FIELD, "2026-10-01T09:00:00+08:00")
    assert loaded[ACA + "uploadfile/news/1/a6.pdf"].origin == "10-24 旧存档"


def test_an_older_corpus_reads_with_blank_columns(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([{"id": "a1", "kind": "附件:pdf:有文字层", "url": FILE}]), encoding="utf-8")
    r = load_corpus(path)[0]
    assert (r.date_source, r.modified, r.origin) == ("", "", "")


# ---------------------------------------------------------------- 日期来源 (spec 2026-11-08 §二 2)

def test_a_date_the_platform_gave_needs_no_label():
    def stated(source: str, raw: str, day: str = "2024-05-31", **more):
        return _stated(rec("n1", "detail:sso_webidx", WEB_DETAIL, page_date=day, page_date_raw=raw,
                           date_source=source, **more))

    assert stated(OWN_FIELD, "2024-05-31") == (date(2024, 5, 31), "2024-05-31", FIELD_SOURCE)
    assert stated(LIST_ROW, "2024-05-31") == (date(2024, 5, 31), "2024-05-31", LIST_SOURCE)
    assert stated(PAGE_WORDS, "日期：2024-05-31") is None  # words on the page: the label rule, as before
    assert stated("", "發布日期：2026-10-20", "2026-10-20") == (date(2026, 10, 20), "發布日期：2026-10-20", PAGE_SOURCE)
    assert stated("", "2024-05-31") is None  # an older export: no label, no date
    # every other check stays: after the fetch day, implausible, a c.nknu list page
    assert stated(OWN_FIELD, "2026-12-30", "2026-12-30") is None
    assert stated(OWN_FIELD, "1980-01-01", "1980-01-01") is None
    assert _stated(rec("p1", "default", ACA + "default.aspx?gi=4", page_date="2026-10-01",
                       page_date_raw="2026-10-01", date_source=OWN_FIELD)) is None


def test_a_page_with_the_platforms_date_field_is_a_notice_its_files_inherit():
    page = rec("p1", "page:geo", GEO, "招生說明", page_date="2026-09-11", page_date_raw="2026-09-11",
               date_source=OWN_FIELD)
    file = rec("a1", "附件:pdf:有文字层", FILE, "form.pdf", linked=(GEO,))
    docs = by_id(build_documents([page, file], {}, [])[0])
    p = docs["dp1"]
    assert (p.kind_class, p.kind_raw, p.added, p.added_raw, p.added_source, p.page_date) == (
        "notice", "page:geo", date(2026, 9, 11), "2026-09-11", FIELD_SOURCE, None)
    a = docs["da1"]
    assert a.parent_id == "dp1"
    assert build._facts(a, p, None, {}) == [DateFact("inherited", date(2026, 9, 11), "2026-09-11", INHERITED_SOURCE)]
    # two records of one address, both pages the field dates: one notice, the other (重复) of it
    twin = rec("p2", "page:geo", GEO.replace("https://", "http://"), "招生說明", page_date="2026-09-11",
               page_date_raw="2026-09-11", date_source=OWN_FIELD)
    docs, excluded = build_documents([page, twin], {}, [])
    assert [d.doc_id for d in docs] == ["dp1"] and docs[0].duplicates == ("dp2",)
    assert excluded == {"page:geo(重复)": 1}


def test_a_plain_page_with_the_date_field_is_a_notice_too():
    page = rec("p1", "page", "https://www.nknu.edu.tw/x", page_date="2026-09-11", page_date_raw="2026-09-11",
               date_source=OWN_FIELD)
    d = build_documents([page], {}, [])[0][0]
    assert (d.kind_class, d.added_source) == ("notice", FIELD_SOURCE)


def test_a_page_dated_by_its_words_stays_a_page():
    page = rec("p1", "page:geo", GEO, page_date="2026-09-11", page_date_raw="發布日期：2026-09-11",
               date_source=PAGE_WORDS)
    file = rec("a1", "附件:pdf:有文字层", FILE, "form.pdf", linked=(GEO,))
    docs = by_id(build_documents([page, file], {}, [])[0])
    p = docs["dp1"]
    assert (p.kind_class, p.page_date, p.page_date_source, p.added) == ("page", date(2026, 9, 11), PAGE_SOURCE, None)
    assert build._facts(p, None, None, {}) == [
        DateFact("published", date(2026, 9, 11), "發布日期：2026-09-11", PAGE_SOURCE)]
    assert build._facts(docs["da1"], p, None, {}) == []  # a page's date is never passed on


def test_a_notice_dated_by_its_list_row():
    notice = rec("n1", "detail:sso_webidx", WEB_DETAIL, page_date="2026-09-11", page_date_raw="2026-09-11",
                 date_source=LIST_ROW)
    file = rec("a1", "附件:pdf:有文字层", FILE, "form.pdf", linked=(WEB_DETAIL,))
    docs = by_id(build_documents([notice, file], {}, [])[0])
    n = docs["dn1"]
    assert (n.added, n.added_source) == (date(2026, 9, 11), LIST_SOURCE)
    assert build._facts(n, None, None, {})[0] == DateFact("published", date(2026, 9, 11), "2026-09-11", LIST_SOURCE)
    assert build._facts(docs["da1"], n, None, {}) == [
        DateFact("inherited", date(2026, 9, 11), "2026-09-11", INHERITED_SOURCE)]


def test_a_cnknu_notice_keeps_its_header_field():
    notice = rec("p32", "newsdetail", ACA + "newsdetail.aspx?nid=662&gi=2", page_date="2026-10-20",
                 page_date_raw="2026-10-20", date_source=OWN_FIELD)
    meta = PageMeta("和平教務組", "停修", "2026/10/21 上午 08:56:00", date(2026, 10, 21), ())
    n = build_documents([notice], {"p32": meta}, [])[0][0]
    assert (n.kind_class, n.added, n.added_raw, n.added_source) == (
        "notice", date(2026, 10, 21), "2026/10/21 上午 08:56:00", FIELD_SOURCE)


def merged(records: list[Record]) -> tuple[list[str], dict[str, list[str]], dict[str, int]]:
    """The documents kept after build._merge_copies, the copies under each and the excluded counts."""
    docs, excluded = build_documents(records, {}, [])
    kept, copies = build._merge_copies(build._rows(docs, {}, {}), lambda _: None, excluded)
    return [r.doc.doc_id for r in kept], {k: [c.doc.doc_id for c in v] for k, v in copies.items()}, excluded


def test_a_page_made_a_notice_still_folds_with_its_copies():
    # GEO shows one notice at every index= of its id (review 11-09): one notice, the other address its copy
    at = "https://geo.nknu.edu.tw/index.php?option=module&lang=cht&task=pageinfo&id=15&index={}"
    pages = [rec(f"p{n}", "page:geo", at.format(n), "招生說明", page_date="2026-09-11", page_date_raw="2026-09-11",
                 date_source=OWN_FIELD) for n in (1, 2)]
    kept, copies, excluded = merged(pages)
    assert (kept, copies) == (["dp1"], {"dp1": ["dp2"]})
    assert excluded == {"page:geo(副本)": 1}
    # the same with no 日期来源 (an older export): two pages, folded as before
    plain = [replace(p, date_source="") for p in pages]
    assert merged(plain)[:2] == (["dp1"], {"dp1": ["dp2"]})


def test_notices_of_a_detail_path_never_fold():
    at = "https://sso.nknu.edu.tw/web/595/index.aspx?selpage=MoreNews&newsidx={}"
    notices = [rec(f"n{n}", "detail:sso_webidx", at.format(n), "招生說明", page_date="2026-09-11",
                   page_date_raw="2026-09-11", date_source=LIST_ROW) for n in (1, 2)]
    assert merged(notices)[:2] == (["dn1", "dn2"], {})


def test_an_older_exports_unlabelled_date_still_dates_nothing():
    page = rec("p1", "page:geo", GEO, page_date="2024-05-31", page_date_raw="2024-05-31")
    d = build_documents([page], {}, [])[0][0]
    assert (d.kind_class, d.page_date, d.page_date_source, d.added) == ("page", None, "", None)
    assert build._facts(d, None, None, {}) == []


# ---------------------------------------------------------------- 最后修改时间 (spec 2026-11-08 §二 3)

def attachment(modified: str = "", fetched: str = "2026-11-07T07:00:00+08:00") -> Record:
    return rec("a1", "附件:pdf:有文字层", FILE, "form.pdf", modified=modified, fetched=fetched)


def server_facts(r: Record, server: dict[str, date]) -> tuple[date | None, list[DateFact]]:
    d = build_documents([r], {}, [])[0][0]
    return d.server_date, build._facts(d, None, None, server)


def test_the_files_last_modified_is_its_server_date():
    day, facts = server_facts(attachment("2023-05-01T10:00:00+08:00"), {FILE: date(2024, 1, 1)})
    assert day == date(2023, 5, 1)
    assert facts == [DateFact("server", date(2023, 5, 1), "2023-05-01", SERVER)]  # over server_dates.json's day
    # the Asia/Taipei day of a time written in another offset
    assert server_facts(attachment("2023-04-30T23:30:00+00:00"), {})[0] == date(2023, 5, 1)


def test_a_time_within_the_fetch_is_the_servers_clock():
    assert CLOCK_SECONDS == 120
    near = attachment("2026-11-07T06:58:30+08:00")  # 90 s before the fetch
    day, facts = server_facts(near, {FILE: date(2024, 1, 1)})
    assert day is None and facts == [DateFact("server", date(2024, 1, 1), "2024-01-01", SERVER)]
    assert server_facts(near, {}) == (None, [])
    assert server_facts(attachment("2026-11-07T06:57:59+08:00"), {})[0] == date(2026, 11, 7)  # 121 s: a file time
    assert server_facts(attachment("2026-11-07T06:58:00+08:00"), {})[0] is None  # 120 s: still the clock


def test_a_time_after_the_fetch_is_the_servers_clock():
    # a file is never changed after the answer that carried it: a server clock running fast (review 11-09)
    for later in ("2026-11-07T07:05:00+08:00", "2027-06-01T07:00:00+08:00"):
        assert server_facts(attachment(later), {})[0] is None
    # nothing to compare with when the record has no 抓取时间 (a corpus assembled before plan 2026-11-08 T3)
    assert server_facts(attachment("2023-05-01T10:00:00+08:00", fetched=""), {})[0] == date(2023, 5, 1)


def test_no_or_a_bad_last_modified_falls_back_to_server_dates():
    for modified in ("", "not a time", "1980-01-01T00:00:00+08:00"):
        day, facts = server_facts(attachment(modified), {FILE: date(2024, 1, 1)})
        assert day is None and facts == [DateFact("server", date(2024, 1, 1), "2024-01-01", SERVER)]
    assert server_facts(attachment(), {}) == (None, [])


def test_a_page_carries_its_server_date_but_is_never_dated_by_it():
    page = rec("p1", "page:geo", GEO, modified="2023-05-01T10:00:00+08:00")
    d = build_documents([page], {}, [])[0][0]
    assert (d.kind_class, d.server_date) == ("page", date(2023, 5, 1))
    assert build._facts(d, None, None, {}) == []
