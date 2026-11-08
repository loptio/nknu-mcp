"""Task E: nknu.content.build on a tiny export (corpus.json, notice pages, calendar, manifest, missing.csv), then the
built file through search and respond (plan 2026-11-01, with the 11-01 later change on attachment dates). The unit
tree (T7d, addendum 2026-11-02 F) comes from a made-up site list and unit config, never the real ones. Owner 11-03
(「可以，这样砍」, addendum L): no series, no correction table and no file-name dates."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import itertools
import json
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pytest

from nknu.content import build
from nknu.content.build import BuildReport, build_db
from nknu.content.embed import chunk_passages
from nknu.content.schema import SCHEMA_VERSION
from nknu.content.units import Unit, load_units
from nknu.core import models
from nknu.retrieval.respond import build_response
from nknu.retrieval.search import search_docs
from nknu.settings import REPO_ROOT

TODAY = date(2026, 11, 1)
ACA = "https://c.nknu.edu.tw/aca/"

NOTICE_HTML = """<!DOCTYPE html><html><head>
<meta http-equiv="Content-Type" content="text/html; charset=big5" /><title>教務處</title></head><body>
<span class="border_title_h3">[<span id="lbl_GroupName">{group}</span>]</span>
<span id="lbl_Title">{title}</span>
<h5>公告日期：<span id="lbl_AddTime">{added}</span></h5>
<span id="lbl_Contents"><p>{title}</p></span>
</body></html>"""

STOP_TEXT = "115學年度第一學期線上申請停修\n受理期間：115年11月3日上午8時起至11月16日下午5時止\n請上網申請。"
# an attachment's own 發文日期 and 沿革: never read (owner 11-01, later change); the agent reads them in the text
RULE_TEXT = ("國立高雄師範大學學生停修辦法\n發文日期：中華民國115年6月10日\n96.6.13 行政會議修正通過\n"
             "111.12.21 教務會議修正通過\n第一條 本辦法依據本校學則訂定之。\n第二條 停修以一科為限，不予退費。")

NOTICES = [  # id, nid, gi, title, 公告日期, 組別, text
    ("p32", 662, 2, "115學年度第一學期線上申請停修時間及相關事宜", "2026/10/21 上午 08:56:00", "和平教務組", STOP_TEXT),
    ("p40", 600, 2, "114學年度第二學期線上申請停修時間及相關事宜", "2026/3/30 下午 02:00:00", "和平教務組",
     "受理期間：115年4月7日起至4月20日止"),
    ("p50", 701, 4, "116學年度研究所甄試招生簡章", "2026/9/1 上午 09:00:00", "招生企劃組", "簡章請見附件"),
    ("p51", 501, 4, "115學年度研究所甄試招生簡章", "2025/9/1 上午 09:00:00", "招生企劃組", "簡章請見附件"),
    ("p60", 610, 4, "115學年度學士班寒假轉學招生簡章公告", "2026/10/1 上午 09:00:00", "招生企劃組", "寒假轉學"),
    ("p61", 606, 4, "115學年度學士班暑假轉學招生簡章", "2026/5/1 上午 09:00:00", "招生企劃組", "暑假轉學"),
]
PAGES = [  # id, kind, url, title, text
    ("p15", "filesdownload", f"{ACA}filesdownload.aspx?pn=111&gi=2", "表單下載", "表單下載\n停修申請表"),
    ("p19", "page_single", f"{ACA}page_single.aspx?tid=4", "聯絡我們", "教務長室 分機 2105"),
    ("p70", "page", f"{ACA}page.aspx?pn=70&gi=2", "115學年度教室借用", "教室借用"),
    ("p71", "page", f"{ACA}page.aspx?pn=71&gi=2", "114學年度教室借用", "教室借用"),
    ("p1", "newslist", f"{ACA}newslist.aspx?gi=2", "最新消息", "列表"),
    ("p57", "外站:reurl.cc", "https://reurl.cc/52Lly6", "交通及住宿", "和平與燕巢校區交通住宿建議說明"),
]
ATTACHMENTS = [  # id, kind, file name, text, linking record ids
    ("a1", "附件:pdf:有文字层", "115-1停修公告1151020.pdf", RULE_TEXT, ["p32"]),
    ("a2", "附件:pdf:有文字层", "停修申請表.pdf", "停修申請表\n學號 科目", ["p15"]),
    ("a3", "附件:pdf:无文字层", "115-1註冊須知掃描.pdf", "", ["p15"]),
    ("a4", "附件:pdf:有文字层", "附件1.pdf", "寒假轉學考試科目", ["p60"]),
    ("a5", "附件:pdf:有文字层", "附件1.pdf", "暑假轉學考試科目", ["p61"]),
    ("a6", "附件:pdf:有文字层", "教室借用辦法.pdf", "教室借用辦法\n借用教室請上網申請", ["p70"]),
    ("a7", "附件:pdf:有文字层", "教室借用辦法.pdf", "教室借用辦法\n借用教室請至教務處申請", ["p71"]),  # not a copy
    ("a8", "附件:pdf:有文字层", "113學年度起入學學生必修科目表.pdf", "必修科目", ["p15"]),
    ("a9", "附件:office", "必修科目表.docx", "112學年度入學新生適用\n必修科目", ["p15"]),
    ("a10", "附件:office", "Teams連結.docx", "會議連結", ["p19"]),  # on a page and no Last-Modified: no date
    ("a11", "附件:pdf:文字很少(多半是扫描+少量字)", "線上停修操作手冊.pdf", "學生線上停修操作手冊\n勾選課程後送出", ["p32"]),
    # copies (review 11-01): one PDF under two notices; scanned PDFs told apart by their bytes only
    ("a12", "附件:pdf:有文字层", "選課操作說明.pdf", "選課操作說明\n登入選課系統後點選加選", ["p40"]),
    ("a13", "附件:pdf:有文字层", "選課操作說明.pdf", "選課操作說明\n登入選課系統後點選加選", ["p32"]),
    ("a14", "附件:pdf:无文字层", "轉系同意名單.pdf", "", ["p15"]),
    ("a15", "附件:pdf:无文字层", "轉系同意名單.pdf", "", ["p60"]),
    ("a16", "附件:pdf:无文字层", "轉系同意名單.pdf", "", ["p61"]),
]
FILES = {"a14": b"%PDF-1.4 the same scan", "a15": b"%PDF-1.4 the same scan", "a16": b"%PDF-1.4 another scan"}
EVENTS = [
    {"id": "ev1", "status": "confirmed", "summary": "申請停修", "htmlLink": "https://www.google.com/calendar/event?eid=1",
     "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-17"}},
    {"id": "ev2", "status": "confirmed", "summary": "115學年度第1學期期末考試",
     "htmlLink": "https://www.google.com/calendar/event?eid=2", "start": {"date": "2026-12-21"},
     "end": {"date": "2026-12-25"}},
]


def notice_url(nid: int, gi: int) -> str:
    return f"{ACA}newsdetail.aspx?nid={nid}&gi={gi}"


def attachment_url(aid: str, name: str) -> str:
    return f"{ACA}uploadfile/files/{aid}/{name}"


URLS = {n[0]: notice_url(n[1], n[2]) for n in NOTICES} | {p[0]: p[2] for p in PAGES} | {
    a[0]: attachment_url(a[0], a[2]) for a in ATTACHMENTS}
SERVER = {"a1": "2026-10-20", "a2": "2025-07-09", "a3": "2021-09-14", "a4": "2099-01-01", "a6": "2021-09-14",
          "a7": "2021-09-14", "a8": "2023-11-20", "a9": "2024-08-28", "p19": "2011-01-20"}

SCHOOL = "國立高雄師範大學"
SOFT = "軟體工程與管理學系"


def unit_site(n: str, k: str, p: str = "") -> dict:
    """One entry as the site probe writes it (research/site_probe_2026-10-23/data.json); units reads n, a, p, k, s."""
    return {"n": n, "a": [], "p": p, "k": k, "u": "https://example.invalid/", "s": "第一轮"}


UNIT_SITES = [  # made up in the site list's shape: the school, 教務處 and three of its sections, another office with a
    #             section, three colleges and five departments
    unit_site(f"{SCHOOL}（校首頁）", "学校"),
    unit_site("教務處", "行政单位"),
    unit_site("和平教務組", "行政组别", "教務處"),
    unit_site("燕巢教務組", "行政组别", "教務處"),
    unit_site("招生企劃組", "行政组别", "教務處"),
    unit_site("總務處", "行政单位"),
    unit_site("出納組", "行政组别", "總務處"),
    unit_site("文學院", "学院"),
    unit_site("理學院", "学院"),
    unit_site("科技學院", "学院"),
    unit_site("國文學系", "系所", "文學院"),
    unit_site("英語學系", "系所", "文學院"),
    unit_site("化學系", "系所", "理學院"),
    unit_site("數學系", "系所", "理學院"),
    unit_site(SOFT, "系所", "科技學院"),
]
UNITS_YAML = """\
units:
  文學院: {campus: 和平, evidence: 測試資料（虛構）}
  理學院: {campus: 燕巢, evidence: 測試資料（虛構）}
  科技學院: {campus: 燕巢, evidence: 測試資料（虛構）}
  和平教務組: {campus: 和平, aliases: [和平校區教務組], evidence: 測試資料（虛構）}
  燕巢教務組: {campus: 燕巢, evidence: 測試資料（虛構）}
  軟體工程與管理學系: {aliases: [軟體系, 軟工系], evidence: 測試資料（虛構）}
"""


def make_inputs(root: Path, events: list[dict] = EVENTS, pages: list[tuple] = (),
                attachments: list[tuple] = (), extra: dict[str, dict] | None = None,
                notices: list[tuple] = ()) -> dict[str, Path]:
    """corpus.json, an export folder, server_dates.json, an alias table, a made-up site list (sites.json) and unit
    config (units.yaml) under root. notices (id, nid, gi, title, 公告日期, 組別, text), pages
    (id, kind, url, title, text) and attachments (id, kind, url, file name, text, linking ids) are added to the fixed
    ones; extra: more fields of a record by its id (the assembled corpus's source, zones, contexts ...)."""
    export = root / "export"
    (export / "pages").mkdir(parents=True)
    (export / "data").mkdir()
    urls = (URLS | {n[0]: notice_url(n[1], n[2]) for n in notices} | {p[0]: p[2] for p in pages}
            | {a[0]: a[2] for a in attachments})
    records, items = [], []
    for rid, _nid, _gi, title, added, group, text in [*NOTICES, *notices]:
        file = f"pages/{rid}.html"
        (export / file).write_bytes(NOTICE_HTML.format(group=group, title=title, added=added).encode("utf-8"))
        records.append({"id": rid, "kind": "newsdetail", "url": urls[rid], "file": file, "title": title, "text": text})
        items.append({"类型": "网页", "网址": urls[rid], "最后落地网址": "", "本地档案": file})
    for rid, kind, url, title, text in [*PAGES, *pages]:
        records.append({"id": rid, "kind": kind, "url": url, "file": f"pages/{rid}.html", "title": title, "text": text})
    (export / "files").mkdir()
    for aid, kind, url, name, text, linked in [(a[0], a[1], URLS[a[0]], *a[2:]) for a in ATTACHMENTS] + list(attachments):
        file = f"files/{aid}-{name}"
        if aid in FILES:
            (export / file).write_bytes(FILES[aid])
        records.append({"id": aid, "kind": kind, "url": url, "file": file, "title": name, "text": text,
                        "linktext": f"{name}\xa0(618445 Kb)", "linked": " | ".join(urls[x] for x in linked)})
    manifest = {"网站": "c.nknu.edu.tw/aca", "名称": "教务处", "导出时间": "2026-10-27T09:19:03+08:00", "项目": items}
    (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (export / "missing.csv").write_text("﻿序号,类型,网址,本地档案,分类\n1,网页,https://a/1,,连接失败\n"
                                        "2,附件,https://a/2.pdf,,要人看\n3,附件,https://a/3.pdf,,连接失败\n",
                                        encoding="utf-8")
    calendar = {"kind": "calendar#events", "items": events}
    (export / "data" / "0001-Google 行事历 nknubf@gmail.com 的事件.json").write_text(
        json.dumps(calendar, ensure_ascii=False), encoding="utf-8")
    paths = {"corpus": root / "corpus.json", "export": export, "server": root / "server_dates.json",
             "aliases": root / "aliases.yaml", "sites": root / "sites.json", "units": root / "units.yaml"}
    records = [r | (extra or {}).get(r["id"], {}) for r in records]
    paths["corpus"].write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    paths["server"].write_text(json.dumps({URLS[k]: v for k, v in SERVER.items()}), encoding="utf-8")
    paths["aliases"].write_text("groups:\n  - [停修, 棄選, 退選]\n", encoding="utf-8")
    paths["sites"].write_text(json.dumps({"sites": UNIT_SITES, "nourl": [], "probed": "2026-10-23"},
                                         ensure_ascii=False), encoding="utf-8")
    paths["units"].write_text(UNITS_YAML, encoding="utf-8")
    return paths


def run(paths: dict[str, Path], out: Path, **kw):
    """build_db on make_inputs' files. The output is under the inputs' own folder (a pytest tmp folder), or a place the
    build refuses: checked before the build runs (a phase-2a test once wrote into the skeleton's folder)."""
    out = Path(out)
    assert out.resolve().is_relative_to(paths["corpus"].parent.resolve()) or build._protected(out, paths["export"])
    kw.setdefault("server_dates", paths["server"])
    kw.setdefault("site_list", paths["sites"])
    kw.setdefault("units_config", paths["units"])
    return build_db(paths["corpus"], paths["export"], out, aliases=paths["aliases"], today=TODAY, **kw)


def cli(paths: dict[str, Path], out: Path) -> list[str]:
    """build.main's arguments for make_inputs' files: never the real site list or unit config."""
    return ["--corpus", str(paths["corpus"]), "--export", str(paths["export"]), "--out", str(out),
            "--server-dates", str(paths["server"]), "--aliases", str(paths["aliases"]),
            "--site-list", str(paths["sites"]), "--units", str(paths["units"])]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("build")
    paths = make_inputs(root)
    out = root / "out" / "aca.db"
    report = run(paths, out)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    yield paths, report, conn
    conn.close()


def doc(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute("SELECT * FROM docs WHERE doc_id=?", (doc_id,)).fetchone()


def dates_of(conn: sqlite3.Connection, doc_id: str) -> list[tuple]:
    return conn.execute("SELECT kind, date, raw, source FROM dates WHERE doc_id=? ORDER BY rowid", (doc_id,)).fetchall()


def tables_of(conn: sqlite3.Connection) -> set[str]:
    return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# ---------------------------------------------------------------- meta and report

def test_meta(built):
    paths, report, conn = built
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert meta["schema_version"] == SCHEMA_VERSION  # was "skeleton-2"; pipeline-1 since T7a, pipeline-2 since 11-03
    assert (meta["site"], meta["name"], meta["data_as_of"]) == ("c.nknu.edu.tw/aca", "教務處", "2026-10-27")
    assert (meta["missing_pages"], meta["missing_files"]) == ("1", "2")
    assert json.loads(meta["aliases_json"]) == [["停修", "棄選", "退選"]]  # the groups search will use
    assert meta["corpus"] == str(paths["corpus"]) and meta["export_dir"] == str(paths["export"])
    assert meta["corpus_sha256"] == hashlib.sha256(paths["corpus"].read_bytes()).hexdigest()
    assert json.loads(meta["counts_json"]) == report.counts
    assert json.loads(meta["excluded_json"]) == COPIES_EXCLUDED
    assert meta["built_at"].startswith("20")


COPIES_EXCLUDED = {"newslist": 1, "附件:pdf:有文字层(副本)": 1, "附件:pdf:无文字层(副本)": 1}


def test_meta_pipeline(built):
    _, report, conn = built
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    # changed 11-03 on purpose: the series tables went (addendum L), so the schema is pipeline-2 (was pipeline-1); and
    # on 11-08 (plan 2026-11-08 T1): doc_alias and docs.tier came, so it is pipeline-3; and (plan 2026-11-08 T4) the
    # sites table, docs.site and docs.unit_line, so it is pipeline-4; and (owner 11-08 10:16, N11) chunk_fts.unit_tokens,
    # so it is pipeline-5; and (owner 11-09, blueprint item 15) sites.deleted_notices, so it is pipeline-6
    assert meta["schema_version"] == SCHEMA_VERSION == "pipeline-6"
    assert meta["id_scheme"] == "serial"  # this corpus's ids are its own (no keys): not the stable ids of assemble
    assert meta["text_pipeline"] == ""  # the 10-29 corpus: no record says where its text came from
    tables = tables_of(conn)
    assert {"doc_refs", "chunk_vecs"} <= tables and conn.execute("SELECT count(*) FROM chunk_vecs").fetchone() == (0,)
    assert {"units", "unit_aliases", "doc_scope"} <= tables  # T7d
    assert report.text_sources == {"corpus": report.counts["docs"]["total"]}


def test_no_series_tables_columns_or_stage(built):
    # owner 11-03 (「可以，这样砍」): the build groups no series. No series tables, indexes or docs.series_id; no series
    # numbers in the report or meta; no correction table or candidate list to pass, on the command line either
    paths, report, conn = built
    assert not {"series", "series_members"} & tables_of(conn)
    assert "series_id" not in {r[1] for r in conn.execute("PRAGMA table_info(docs)")}
    indexes = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert not {"members_doc", "docs_series"} & indexes
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert "series_embed_json" not in meta and not [k for k in meta if "series" in k]
    fields = {f.name for f in dataclasses.fields(BuildReport)}
    assert not {"series", "series_members", "series_embed"} & fields
    assert not {"series", "series_members", "series_embed"} & set(dataclasses.asdict(report))
    assert not {"corrections", "candidates"} & set(inspect.signature(build_db).parameters)
    for flag in ("--corrections", "--candidates"):
        with pytest.raises(SystemExit) as stop:  # argparse refuses an option it does not have
            build.main([*cli(paths, paths["corpus"].parent / "never.db"), flag, str(paths["corpus"])])
        assert stop.value.code == 2, flag
    assert not (paths["corpus"].parent / "never.db").exists()


def test_report(built):
    _, report, _ = built
    assert report.counts["docs"] == {"notice": 6, "page": 4, "attachment": 14, "event": 2, "link": 1, "total": 27}
    assert report.excluded == COPIES_EXCLUDED  # the two copies, counted like any left-out record
    assert report.date_sources == {"notice": {"published": 6}, "page": {"none": 4},
                                   "attachment": {"inherited": 7, "server": 6, "none": 1}, "event": {"行事曆": 2},
                                   "link": {"none": 1}}
    assert report.no_date == 6 and report.no_text == 4
    # changed 11-03 on purpose: no series count, and no file-name date (da1's 「1151020」 gave one before)
    assert report.counts["chunks"] > 0 and "filename" not in report.counts["dates"]
    assert report.counts["doc_types"] == {"journal": 0, "exam": 0, "minutes": 0}  # no paper, exam or minutes
    assert report.warnings == []


# ---------------------------------------------------------------- dates

def test_notice_published_date_and_periods(built):
    _, _, conn = built
    raw = "115年11月3日上午8時起至11月16日下午5時止"
    assert dates_of(conn, "dp32") == [("published", "2026-10-21", "2026/10/21 上午 08:56:00", "公告頁日期欄"),
                                      ("period_start", "2026-11-03", raw, "正文期間"),
                                      ("period_end", "2026-11-16", raw, "正文期間")]
    d = doc(conn, "dp32")
    assert (d["ident_date"], d["ident_kind"], d["period_ay"], d["period_sem"]) == ("2026-10-21", "published", 115, 1)
    assert (d["grp"], d["unit"], d["has_text"], d["chars"]) == ("和平教務組", "教務處", 1, len(d["text"]))


def test_attachment_inherits_and_never_reads_its_text(built):
    # no 發文日期, no 沿革; and since 11-03 (changed on purpose) no date from its file name 「115-1停修公告1151020.pdf」
    _, _, conn = built
    assert dates_of(conn, "da1") == [("inherited", "2026-10-21", "2026/10/21 上午 08:56:00", "所屬公告"),
                                     ("server", "2026-10-20", "2026-10-20", "伺服器檔案時間")]
    assert conn.execute("SELECT count(*) FROM dates WHERE kind = 'filename' OR source = '檔名'").fetchone() == (0,)
    d = doc(conn, "da1")
    assert (d["ident_date"], d["ident_kind"], d["parent_id"], d["period_ay"], d["period_sem"]) == (
        "2026-10-21", "inherited", "dp32", 115, 1)
    assert [k for k, *_ in dates_of(conn, "da4")] == ["inherited"]  # a Last-Modified past today + 400 days is dropped


def test_attachment_on_undated_page_uses_server_time(built):
    _, _, conn = built
    assert dates_of(conn, "da2") == [("server", "2025-07-09", "2025-07-09", "伺服器檔案時間")]
    assert (doc(conn, "da2")["ident_kind"], doc(conn, "da2")["parent_id"]) == ("server", "dp15")
    assert dates_of(conn, "da10") == [] and doc(conn, "da10")["ident_date"] is None
    assert dates_of(conn, "dp19") == []  # pages get no server time (plan: attachments only)
    result = search_docs(conn, "停修申請表", today=TODAY, aliases=[])
    r = next(r for r in build_response(conn, result, today=TODAY)["results"] if r["id"] == "da2")
    assert "｜伺服器檔案時間 2025-07-09（伺服器檔案時間）" in r["identity"]


def test_event_dates(built):
    _, _, conn = built
    assert dates_of(conn, "eev1") == [("event_start", "2026-11-03", "2026-11-03", "行事曆"),
                                      ("event_end", "2026-11-16", "2026-11-16", "行事曆")]  # end.date is exclusive
    assert doc(conn, "eev1")["ident_date"] is None and doc(conn, "eev1")["kind_class"] == "event"
    assert (doc(conn, "eev2")["period_ay"], doc(conn, "eev2")["period_sem"]) == (115, 1)  # its title names it
    assert (doc(conn, "eev1")["period_ay"], doc(conn, "eev1")["period_sem"]) == (115, 1)  # 申請停修: its first day's


# ---------------------------------------------------------------- applies_to

def test_applies_to(built):
    _, _, conn = built

    def rows(doc_id):
        return conn.execute("SELECT unit, cohort_from, cohort_exact, raw FROM applies_to WHERE doc_id=? ORDER BY rowid",
                            (doc_id,)).fetchall()

    assert rows("da8") == [("教務處", 113, None, "113學年度起入學")]  # from the title
    assert rows("da9") == [("教務處", None, 112, "112學年度入學")]  # from the first 300 characters
    assert rows("dp19") == [("教務處", None, None, "")]  # the relation without a qualifier


def test_two_files_of_one_name_are_two_results(built):
    # was test_tied_series_not_folded (da6 and da7, one series that could not tell its newest, both shown and each
    # called 「同系列最新」). Owner 11-03: no series; the two 教室借用辦法.pdf of the 115 and 114 pages are two results,
    # and no identity line names a series
    _, _, conn = built
    result = search_docs(conn, "教室借用辦法", today=TODAY, aliases=[])
    assert {"da6", "da7"} <= {h.doc_id for h in result.hits}
    rs = build_response(conn, result, today=TODAY)["results"]
    assert all("系列" not in r["identity"] and "series" not in r for r in rs)
    assert [r["identity"] for r in rs if r["id"] in ("da6", "da7")] == [
        "教務處｜和平教務組｜附件（PDF）｜伺服器檔案時間 2021-09-14（伺服器檔案時間）"] * 2


def copies_of(conn: sqlite3.Connection, doc_id: str) -> list[dict] | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (f"copies:{doc_id}",)).fetchone()
    return json.loads(row[0]) if row else None


def test_copies_kept_once(built):
    # review 11-01: the same file at two addresses took two of the ten places; it is kept once, under the latest
    # notice, and the other address is listed with its own notice and date
    # changed on purpose (plan 2026-11-08 T1): each copy lists its own id, which read resolves to the kept one
    _, _, conn = built
    assert doc(conn, "da12") is None and doc(conn, "da13")["parent_id"] == "dp32"
    assert copies_of(conn, "da13") == [{"id": "da12", "url": URLS["a12"], "date": "2026-03-30",
                                        "date_kind": "inherited", "parent_id": "dp40", "parent_title": NOTICES[1][3]}]
    # without text, only the same bytes make a copy: the one under a notice is kept, another scan stays apart
    assert doc(conn, "da14") is None and doc(conn, "da15")["parent_id"] == "dp60" and doc(conn, "da16") is not None
    assert copies_of(conn, "da15") == [{"id": "da14", "url": URLS["a14"], "parent_id": "dp15",
                                        "parent_title": "表單下載"}]
    assert copies_of(conn, "da16") is None and copies_of(conn, "da1") is None
    rs = build_response(conn, search_docs(conn, "選課操作說明", today=TODAY, aliases=[]), today=TODAY)
    ids = [r["id"] for r in rs["results"]]
    assert "da13" in ids and "da12" not in ids
    r = rs["results"][ids.index("da13")]
    assert r["copies"] == [{"id": "da12", "url": URLS["a12"], "date": "2026-03-30", "date_type": "所屬公告發布",
                            "parent": {"id": "dp40", "title": NOTICES[1][3]}}]
    assert any(n.startswith("【系統】copies") for n in rs["notes"])


def test_parent_title_and_text_source(tmp_path):
    paths = make_inputs(tmp_path, extra={"a1": {"source": "totext"}})
    report = run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        d = doc(conn, "da1")
        assert (d["parent_id"], d["parent_title"], d["text_source"]) == ("dp32", NOTICES[0][3], "totext")
        assert (doc(conn, "da2")["parent_title"], doc(conn, "da2")["text_source"]) == ("表單下載", "")
        assert doc(conn, "dp32")["parent_title"] == ""
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        # the 10-29 corpus names no zone: each linking page is a reference of unknown zone
        refs = conn.execute("SELECT ref_id, zone, context FROM doc_refs WHERE doc_id='da1'").fetchall()
    finally:
        conn.close()
    assert report.text_sources == {"totext": 1, "corpus": report.counts["docs"]["total"] - 1}
    assert meta["text_pipeline"] == "totext" and refs == [("dp32", "unknown", "")]


def test_table_header_offsets_and_tokens(tmp_path):
    # T6's table header of a chunk that starts inside a Markdown table: its offsets in chunks, its words in the index
    header = "| 項目 | 金額 |\n| --- | --- |"
    text = header + "\n" + "\n".join(f"| 學費第{i}期 | {i},000 元 |" for i in range(1, 80))
    table = ("a61", "附件:pdf:有文字层", attachment_url("a61", "收費表.pdf"), "收費表.pdf", text, ["p32"])
    paths = make_inputs(tmp_path, attachments=[table])
    run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        stored = doc(conn, "da61")["text"]
        chunks = conn.execute("SELECT c.head_start, c.head_end, f.body_tokens FROM chunks c JOIN chunk_fts f"
                              " ON f.rowid = c.chunk_id WHERE c.doc_id = 'da61' ORDER BY c.chunk_id").fetchall()
    finally:
        conn.close()
    assert len(chunks) > 1 and chunks[0][:2] == (None, None)  # the first chunk holds the header itself
    for head_start, head_end, body in chunks[1:]:
        assert stored[head_start:head_end] == header
        assert {"项目", "金额"} <= set(body.split())


def test_member_server_date_ignores_the_fragment(tmp_path):
    zip_url = attachment_url("a62", "m.zip")
    member = ("a62m1", "附件:pdf:有文字层", zip_url + "#%E6%9C%83%E8%AD%B0.pdf", "會議.pdf", "會議紀錄", ["p15"])
    paths = make_inputs(tmp_path, attachments=[member])
    server = json.loads(paths["server"].read_text(encoding="utf-8")) | {zip_url: "2026-01-02"}
    paths["server"].write_text(json.dumps(server), encoding="utf-8")
    run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        assert dates_of(conn, "da62m1") == [("server", "2026-01-02", "2026-01-02", "伺服器檔案時間")]
    finally:
        conn.close()


def test_skeleton_folder_is_protected(tmp_path):
    target = REPO_ROOT / "research" / "skeleton_2026-11-01" / "x.db"
    paths = make_inputs(tmp_path)
    assert build._protected(target, paths["export"])  # checked first: a build without the guard would write there
    with pytest.raises(ValueError):
        run(paths, target)
    assert not target.exists() and not target.with_name("x.db.tmp").exists()


def test_no_server_dates(tmp_path):
    # was test_corrections_and_no_server_dates: the correction table (and its warning about an unknown URL) went with
    # the series on 11-03; a build without server_dates.json still says so
    paths = make_inputs(tmp_path)
    report = run(paths, tmp_path / "x.db", server_dates=None)
    assert report.date_sources["attachment"] == {"inherited": 7, "none": 7}
    assert len(report.warnings) == 1 and "伺服器檔案時間" in report.warnings[0]


# ---------------------------------------------------------------- doc types (ranking v2)

UPLOAD = f"{ACA}uploadfile/userfiles/"
PAPERS = f"{UPLOAD}files/nknupaper/"  # the site's 學報 folder


def upload(path: str) -> str:
    return UPLOAD + quote(path)  # the site writes its upload paths percent-encoded


DOC_TYPES = [  # title, parent title, address, type: the real site's writing, no person names
    # journal: anything in the 學報 folder (issue contents, abstracts, papers) ...
    ("26", "", PAPERS + "26.htm", "journal"),
    ("230202", "", PAPERS + "excerpt/23/230202.htm", "journal"),
    ("a03.pdf", "第五十期", PAPERS + "excerpt/50/a03.pdf", "journal"),
    # ... and a whole issue: named so in its title, or a 總檔 / 全上傳 file on the 學報 page
    ("55期學報總檔.pdf", "高雄師大學報", upload("5/files/55期學報總檔.pdf"), "journal"),
    ("上網-52期學報-目錄_merged (2)(1).pdf", "高雄師大學報", upload("5/files/上網-52期學報-目錄_merged (2)(1).pdf"), "journal"),
    ("1111228-53期-總檔.pdf", "高雄師大學報", upload("5/files/1111228-53期-總檔.pdf"), "journal"),
    ("51全上傳.pdf", "高雄師大學報", upload("5/files/51全上傳.pdf"), "journal"),
    # ... and, changed 11-09 on purpose (owner, blueprint item 17, 「照建议定」), the old 校刊 (spaper) and 校訊 (sinfo)
    ("傳統公有市場華麗轉身", "", f"{UPLOAD}files/spaper/100/04.html", "journal"),
    # not journal: the journal's page and notices, its forms and board lists (lists are never demoted), 入學報考 /
    # 入學報到 (學報 inside other words), 總檔 off the 學報 page, other schools' calls for papers
    ("高雄師大學報", "", f"{ACA}page.aspx?pn=71&pclass=A5005&gi=5", ""),
    ("即日起，高雄師大學報投稿專用信箱更改為新信箱", "", notice_url(203, 5), ""),
    ("人文與藝術類-徵稿啟事(1).pdf", "高雄師大學報", upload("5/files/人文與藝術類-徵稿啟事(1).pdf"), ""),
    ("51期-撰稿須知.pdf", "高雄師大學報", upload("5/files/51期-撰稿須知.pdf"), ""),
    ("54期自然類-稿件審查意見書_2023_05_18.doc", "高雄師大學報", upload("5/files/54期自然類-稿件審查意見書.doc"), ""),
    ("第55期學報徵稿啟事.pdf", "高雄師大學報", upload("5/files/第55期學報徵稿啟事.pdf"), ""),
    ("a類：教育與社會類-59_60委員名單.pdf", "高雄師大學報", upload("5/files/a類：教育與社會類-59_60委員名單.pdf"), ""),
    ("111學年度碩博士班甄試入學報考人數統計.pdf", "111學年度碩博士班甄試入學考試報名人數統計",
     upload("4/files/招生目錄/01最新消息/111學年度碩博士班甄試入學報考人數統計.pdf"), ""),
    ("115學年度大學部新生入學報到須知", "", notice_url(641, 2), ""),
    ("報到資料總檔.pdf", "115學年度大學部新生入學報到須知", f"{ACA}uploadfile/news/20260706/報到資料總檔.pdf", ""),
    ("會議紀錄總檔.pdf", "會議紀錄", f"{ACA}uploadfile/files/file1_113153424.pdf", ""),
    ("各校期刊徵稿資訊(115年10月08日更新)", "", notice_url(85, 5), ""),
    # exam: past papers (the 歷屆考題 folder), 考古題 / 歷屆試題 in the title or the parent's title
    ("104b.pdf", "特殊教育學系", upload("4/files/招生目錄/09歷屆考題/01轉學考/05特教/02教育心理學/104b.pdf"), "exam"),
    ("114a.pdf", "軟體工程與管理學系", upload("4/files/招生目錄/09歷屆考題/02碩博士班/20軟體碩班/01計算機概論/114a.pdf"),
     "exam"),
    ("轉學考考古題.pdf", "", upload("4/files/轉學考考古題.pdf"), "exam"),
    ("108a.pdf", "歷屆試題下載", upload("4/files/108a.pdf"), "exam"),
    # not exam: past brochures (簡章 are never demoted), exam-room tables, admission lists, the department pages that
    # list the papers, a 試題 form
    ("u115b.pdf", "115學年度學士班寒假轉學招生簡章公告", upload("4/files/招生目錄/08歷屆簡章/01轉學考/u115b.pdf"), ""),
    ("115日碩試場分配表.pdf", "115學年度碩士班招生考試筆試試場分配表及試場位置圖公告",
     upload("4/files/招生目錄/01最新消息/115日碩試場分配表.pdf"), ""),
    ("115博錄取名單.pdf", "公告115學年度博士班招生考試錄取名單", upload("4/files/招生目錄/06博士班/02博士班考試/115博錄取名單.pdf"),
     ""),
    ("國文學系", "", f"{ACA}page_single.aspx?gi=4&tid=15", ""),
    ("試題疑義申請表.pdf", "115學年度碩士班招生考試簡章公告", upload("4/files/試題疑義申請表.pdf"), ""),
]


@pytest.mark.parametrize(("title", "parent", "url", "want"), DOC_TYPES)
def test_doc_type(title, parent, url, want):
    assert build.doc_type(title, parent, url) == want


TYPED_PAGES = [
    ("p104", "page", f"{ACA}page.aspx?pn=71&pclass=A5005&gi=5", "高雄師大學報", "高雄師大學報\n學報投稿專用信箱"),
    ("p271", "静态:nknupaper", PAPERS + "excerpt/50/500103.htm", "500103", "摘 要\n本研究探討大學生的學習策略。"),
    ("p370", "page_single", f"{ACA}page_single.aspx?gi=4&tid=15", "國文學系", "國文學系\n古籍導讀\n109\n108"),
]
TYPED_ATTACHMENTS = [
    ("a50", "附件:pdf:有文字层", upload("5/files/55期學報總檔.pdf"), "55期學報總檔.pdf", "高雄師大學報 第55期", ["p104"]),
    ("a51", "附件:pdf:有文字层", upload("5/files/人文與藝術類-徵稿啟事.pdf"), "人文與藝術類-徵稿啟事.pdf", "徵稿啟事", ["p104"]),
    ("a52", "附件:pdf:有文字层", upload("4/files/招生目錄/09歷屆考題/01轉學考/01國文/011古籍導讀/104a.pdf"), "104a.pdf",
     "國立高雄師範大學104學年度學士班轉學招生考試試題", ["p370"]),
    ("a53", "附件:pdf:有文字层", upload("4/files/招生目錄/08歷屆簡章/01轉學考/u115b.pdf"), "u115b.pdf", "寒假轉學招生簡章",
     ["p60"]),
]


def test_doc_types_written_and_counted(tmp_path):
    paths = make_inputs(tmp_path, pages=TYPED_PAGES, attachments=TYPED_ATTACHMENTS)
    report = run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        types = dict(conn.execute("SELECT doc_id, doc_type FROM docs WHERE doc_type != ''").fetchall())
        assert doc(conn, "da52")["parent_id"] == "dp370" and doc(conn, "da50")["parent_id"] == "dp104"
    finally:
        conn.close()
    assert types == {"dp271": "journal", "da50": "journal", "da52": "exam"}  # every other document: ''
    assert report.counts["doc_types"] == {"journal": 2, "exam": 1, "minutes": 0}


# ---------------------------------------------------------------- sections, chunks, index

def test_no_text_document_found_by_title(built):
    _, _, conn = built
    assert doc(conn, "da3")["has_text"] == 0
    assert conn.execute("SELECT sec_id, start, end FROM chunks WHERE doc_id='da3'").fetchall() == [(0, 0, 0)]
    assert conn.execute("SELECT count(*) FROM sections WHERE doc_id='da3'").fetchone()[0] == 0
    result = search_docs(conn, "註冊須知掃描", today=TODAY, aliases=[])
    r = next(r for r in build_response(conn, result, today=TODAY)["results"] if r["id"] == "da3")
    assert r["content_type"] == "無文字" and r["title"] == "115-1註冊須知掃描.pdf"


def test_few_words_scan_indexed_but_shown_as_no_text(built):
    # a mostly scanned PDF: no 全文, but its few words still find it (review 11-01)
    _, _, conn = built
    assert doc(conn, "da11")["has_text"] == 0
    result = search_docs(conn, "勾選課程", today=TODAY, aliases=[])  # words only its text has
    r = next(r for r in build_response(conn, result, today=TODAY)["results"] if r["id"] == "da11")
    assert r["content_type"] == "無文字" and r["content"].endswith("（只轉出少量文字：學生線上停修操作手冊\n勾選課程後送出）")


def test_sections_and_chunks_cover_text(built):
    _, _, conn = built
    for doc_id, text in conn.execute("SELECT doc_id, text FROM docs WHERE has_text=1").fetchall():
        secs = conn.execute("SELECT start, end FROM sections WHERE doc_id=? ORDER BY sec_id", (doc_id,)).fetchall()
        assert secs[0][0] == 0 and secs[-1][1] == len(text), doc_id
        assert all(a[1] == b[0] for a, b in itertools.pairwise(secs)), doc_id
        chunks = conn.execute("SELECT start, end FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()
        assert chunks and all(0 <= a < b <= len(text) for a, b in chunks), doc_id
    n = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert n == conn.execute("SELECT count(*) FROM chunk_fts").fetchone()[0]
    assert conn.execute("SELECT count(*) FROM chunks c JOIN chunk_fts f ON f.rowid = c.chunk_id").fetchone()[0] == n


def test_index_tokens(built):
    _, _, conn = built
    sql = ("SELECT f.title_tokens, f.body_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid"
           " WHERE c.doc_id = ? ORDER BY c.chunk_id DESC LIMIT 1")
    title, body = conn.execute(sql, ("da1",)).fetchone()
    # its own name, its notice's title and its link text, with their term tokens (T6: da1 is one section now, so
    # 第二條 is no heading here; test_long_section_heading_in_title_tokens has a section heading)
    assert {"停修", "线上", "term115s1", "ay115"} <= set(title.split())
    assert "退费" in body.split()
    _, notice_body = conn.execute(sql, ("dp32",)).fetchone()
    assert {"term115s1", "ay115", "受理"} <= set(notice_body.split())
    for event in ("eev1", "eev2"):  # an event's term tokens are in its title column once, named in the title or not
        title = conn.execute(sql, (event,)).fetchone()[0].split()
        assert title.count("term115s1") == 1 and title.count("ay115") == 1, event
    sized = conn.execute("SELECT c.doc_id, f.title_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid"
                         " JOIN docs d ON d.doc_id = c.doc_id WHERE d.kind_class = 'attachment'").fetchall()
    assert sized and [d for d, t in sized if {"kb", "618445"} & set(t.split())] == []  # link text 「… (618445 Kb)」


def test_long_section_heading_in_title_tokens(tmp_path):
    # T6: a document splits into sections only above SECTION_MAX; then each section's heading is in its chunks'
    # title column, as before
    text = "第一條 " + "甲" * 3000 + "\n第二條 停修以一科為限"
    rule = ("a60", "附件:pdf:有文字层", attachment_url("a60", "停修規定.pdf"), "停修規定.pdf", text, ["p32"])
    paths = make_inputs(tmp_path, attachments=[rule])
    run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        headings = [h for (h,) in conn.execute("SELECT heading FROM sections WHERE doc_id='da60' ORDER BY sec_id")]
        sql = ("SELECT f.title_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid"
               " WHERE c.doc_id = ? ORDER BY c.chunk_id DESC LIMIT 1")
        title = conn.execute(sql, ("da60",)).fetchone()[0].split()
    finally:
        conn.close()
    assert headings == [("第一條 " + "甲" * 3000)[:40], "第二條 停修以一科為限"]
    assert {"第二", "二条"} <= set(title)


NEXT_TERM_EVENT = {"id": "ev3", "status": "confirmed", "summary": "大學部網路選課Online course registration",
                   "htmlLink": "https://www.google.com/calendar/event?eid=3", "start": {"date": "2027-02-15"},
                   "end": {"date": "2027-02-19"}}


def test_next_term_event_found_by_its_term(tmp_path):
    # fix round 2 (11-01): next term's 選課 event names no term; it gets 115-2 from its first day, so a query that
    # names 115-2 (下學期 on 2026-11-01) finds it first, and the term alone finds it at all
    paths = make_inputs(tmp_path, events=[*EVENTS, NEXT_TERM_EVENT])
    run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        assert (doc(conn, "eev3")["period_ay"], doc(conn, "eev3")["period_sem"]) == (115, 2)
        for q in ("115-2", "下學期 選課", "115-2 選課", "115學年度第2學期 選課"):
            assert search_docs(conn, q, today=TODAY, aliases=[]).hits[0].doc_id == "eev3", q
    finally:
        conn.close()


OLD_EVENTS = [  # owner 11-01: the calendar from 2023-01-01 on, a fixed day; a first day is its Asia/Taipei day
    {"id": "ev4", "status": "confirmed", "summary": "學測", "htmlLink": "https://www.google.com/calendar/event?eid=4",
     "start": {"date": "2017-11-10"}, "end": {"date": "2017-11-11"}},
    {"id": "ev5", "status": "confirmed", "summary": "跨年晚會", "htmlLink": "https://www.google.com/calendar/event?eid=5",
     "start": {"dateTime": "2022-12-31T15:30:00Z"}, "end": {"dateTime": "2022-12-31T17:00:00Z"}},  # 23:30 in Taipei
]
NEW_EVENTS = [
    {"id": "ev6", "status": "confirmed", "summary": "元旦升旗", "htmlLink": "https://www.google.com/calendar/event?eid=6",
     "start": {"dateTime": "2022-12-31T16:30:00Z"}, "end": {"dateTime": "2022-12-31T17:30:00Z"}},  # 00:30, 1/1 in Taipei
    # no staff-schedule filter (owner 11-01): a staff member's leave from 2023 on is a document like any other event
    {"id": "ev7", "status": "confirmed", "summary": "盈O，休假", "htmlLink": "https://www.google.com/calendar/event?eid=7",
     "start": {"date": "2024-05-10"}, "end": {"date": "2024-05-11"}},
]


def test_calendar_from_2023_on(tmp_path):
    # owner 11-01: an event that starts before 2023-01-01 is left out and counted (the export keeps it), so a query
    # naming its term no longer finds it
    paths = make_inputs(tmp_path, events=[*EVENTS, *OLD_EVENTS, *NEW_EVENTS])
    report = run(paths, tmp_path / "x.db")
    assert report.excluded == {**COPIES_EXCLUDED, "行事历(2023年以前)": 2} and report.counts["docs"]["event"] == 4
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        assert doc(conn, "eev4") is None and doc(conn, "eev5") is None
        assert dates_of(conn, "eev6")[0] == ("event_start", "2023-01-01", "2023-01-01", "行事曆")
        assert doc(conn, "eev7")["title"] == "盈O，休假"
        assert search_docs(conn, "106-1", today=TODAY, aliases=[]).hits == []
    finally:
        conn.close()


def test_search_end_to_end(built):
    _, _, conn = built
    aliases = json.loads(conn.execute("SELECT value FROM meta WHERE key='aliases_json'").fetchone()[0])
    first = search_docs(conn, "115-1 停修", today=TODAY, aliases=aliases)
    ids = [h.doc_id for h in first.hits]
    # changed 11-03 on purpose (no series): the 114-2 notice is a result of its own now, below the 115-1 one the query
    # names (it folded under it before); the 115-1 calendar event 申請停修 stays first, as it was
    assert ids[0] == "eev1" and ids.index("dp32") < ids.index("dp40")
    same = search_docs(conn, "115學年度第1學期 停修", today=TODAY, aliases=aliases)
    assert [h.doc_id for h in same.hits[:3]] == [h.doc_id for h in first.hits[:3]]
    assert "dp32" in [h.doc_id for h in search_docs(conn, "退選", today=TODAY, aliases=aliases).hits]  # via 停修
    assert "dp32" not in [h.doc_id for h in search_docs(conn, "退選", today=TODAY, aliases=[]).hits]
    assert build_response(conn, search_docs(conn, "!!!", today=TODAY, aliases=aliases), today=TODAY)["found"] is False


# ---------------------------------------------------------------- writing the file, the command line

def test_output_replaced_whole(tmp_path, monkeypatch):
    paths = make_inputs(tmp_path)
    out = tmp_path / "aca.db"
    out.write_bytes(b"an older build")
    run(paths, out)
    conn = sqlite3.connect(out)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == (SCHEMA_VERSION,)
    finally:
        conn.close()
    assert not (tmp_path / "aca.db.tmp").exists()
    good = out.read_bytes()

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(build, "make_chunks", broken)
    with pytest.raises(RuntimeError):
        run(paths, out)
    assert out.read_bytes() == good and not (tmp_path / "aca.db.tmp").exists()  # a failed build leaves the old file


def test_main(tmp_path, capsys):
    paths = make_inputs(tmp_path)
    out = tmp_path / "o" / "aca.db"
    args = cli(paths, out)
    assert build.main(args) == 0
    report = json.loads((tmp_path / "o" / "build_report.json").read_text(encoding="utf-8"))
    assert report["counts"]["docs"]["total"] == 27 and report["no_text"] == 4 and out.is_file()
    assert report["text_sources"] == {"corpus": 27}
    assert report["scope"]["narrowed"] == {"total": 6, "title": 5, "header": 1}  # T7d: the unit stage ran
    printed = capsys.readouterr().out
    assert str(out) in printed and "文字来源 corpus 27" in printed
    assert "单位范围：缩小 6 份（标题 5、开头 1）、提及单位 0 份" in printed
    assert "系列" not in printed and "series" not in report  # 11-03: no series count in the line or the report
    assert build.main(["--corpus", str(tmp_path / "none.json"), *args[2:]]) == 2  # corpus missing
    inside = ["--out", str(paths["export"] / "aca.db")]
    assert build.main([*args[:4], *inside, *args[6:]]) == 2  # never writes into the export
    assert not (paths["export"] / "aca.db").exists()
    capsys.readouterr()
    for flag, missing in (("--site-list", "单位名单"), ("--units", "单位设定")):  # the unit tree's inputs are required
        i = args.index(flag)
        assert build.main([*args[:i], flag, str(tmp_path / "none.file"), *args[i + 2:]]) == 2
        assert f"找不到{missing}" in capsys.readouterr().err


# ---------------------------------------------------------------- vectors (T7b); the fakes live in test_embed, which
# imports this module, so they are imported inside the tests

def test_no_vectors_without_embed(built):
    _, report, conn = built
    assert report.vectors == {} and conn.execute("SELECT count(*) FROM chunk_vecs").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'vec%'").fetchone() == (0,)


def test_build_embed_flag(tmp_path):
    from tests.content.test_embed import FakeEmbedder

    paths = make_inputs(tmp_path)
    out, cache = tmp_path / "aca.db", tmp_path / "cache" / "vec_cache.sqlite"
    report = run(paths, out, embed=True, embedder=FakeEmbedder(), vec_cache=cache)
    v = report.vectors
    assert v["chunks"] > 0 and v["chunks"] == report.counts["chunks"]
    assert (v["model"], v["revision"], v["cached"]) == ("fake/embed", "0" * 40, 0) and v["encoded"] > 0
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM chunk_vecs").fetchone() == (v["chunks"],)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        # 16 KB pages: a 2 KB float16 vector row fits once in SQLite's default 4 KB page, 7 times in 16 KB
        assert conn.execute("PRAGMA page_size").fetchone() == (16_384,)
    finally:
        conn.close()
    assert (meta["vec_model"], meta["vec_count"], meta["vec_dim"]) == ("fake/embed", str(v["chunks"]), "16")
    again = run(paths, out, embed=True, embedder=FakeEmbedder(), vec_cache=cache)  # a rebuild: all from the cache
    assert (again.vectors["encoded"], again.vectors["cached"]) == (0, v["chunks"])
    assert not (tmp_path / "aca.db.tmp").exists()


def test_failed_embedding_leaves_the_old_db(tmp_path):
    from tests.content.test_embed import FailsOnCall

    paths = make_inputs(tmp_path)
    out = tmp_path / "aca.db"
    run(paths, out)
    good = out.read_bytes()
    with pytest.raises(RuntimeError, match="out of memory"):
        run(paths, out, embed=True, embedder=FailsOnCall(1))
    assert out.read_bytes() == good and not (tmp_path / "aca.db.tmp").exists()
    with pytest.raises(models.ModelUnavailable):  # no embedder given: models.load_embedder, which the guard stops
        run(paths, out, embed=True)
    assert out.read_bytes() == good and not (tmp_path / "aca.db.tmp").exists()


def test_vector_cache_is_never_written_where_the_build_may_not(tmp_path):
    from tests.content.test_embed import FakeEmbedder

    paths = make_inputs(tmp_path)
    out = tmp_path / "aca.db"
    for bad in (paths["export"] / "vec_cache.sqlite", out):
        with pytest.raises(ValueError):
            run(paths, out, embed=True, embedder=FakeEmbedder(), vec_cache=bad)
    assert not (paths["export"] / "vec_cache.sqlite").exists() and not out.exists()


def test_main_embed(tmp_path, capsys, monkeypatch):
    from tests.content.test_embed import FakeEmbedder

    paths = make_inputs(tmp_path)
    out = tmp_path / "o" / "aca.db"
    args = [*cli(paths, out), "--embed"]
    assert build.main(args) == 2  # the guard: no model in unit tests, and nothing is written before the model loads
    assert "unit tests never load real models" in capsys.readouterr().err and not out.parent.exists()
    monkeypatch.setattr(models, "load_embedder", lambda device=None: FakeEmbedder())
    assert build.main(args) == 0
    report = json.loads((tmp_path / "o" / "build_report.json").read_text(encoding="utf-8"))
    assert report["vectors"]["chunks"] == report["counts"]["chunks"] and (tmp_path / "o" / build.VEC_CACHE).is_file()
    assert f"向量 {report['vectors']['chunks']} 小段" in capsys.readouterr().out
    elsewhere = tmp_path / "elsewhere.sqlite"
    assert build.main([*args, "--vec-cache", str(elsewhere)]) == 0 and elsewhere.is_file()


def test_embed_encodes_chunk_passages_only(tmp_path):
    # owner 11-03: the series stage with vectors (T7c) went. A build with embed encodes each chunk's passage, in the
    # chunks' one round, and no title-and-opening-words vector of any document; meta and report say nothing of series
    from tests.content.test_embed import FakeEmbedder

    paths = make_inputs(tmp_path, pages=[("p80", "page", f"{ACA}page.aspx?pn=80&gi=2", "114學年度網路選課注意事項",
                                          "網路選課注意事項\n請於期限內上網選課"),
                                         ("p81", "page", f"{ACA}page.aspx?pn=81&gi=2", "115學年度網路選課相關注意事項",
                                          "網路選課相關注意事項\n請於期限內上網選課")])
    emb = FakeEmbedder()
    out = tmp_path / "aca.db"
    report = run(paths, out, embed=True, embedder=emb, vec_cache=tmp_path / "vec_cache.sqlite")
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        ids = [c for (c,) in conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")]
        passages = chunk_passages(conn, ids)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        series_tables = {"series", "series_members"} & tables_of(conn)
    finally:
        conn.close()
    # 11-08 (main session): the vectors embed each passage without its unit line (embed_chunks' unit, off by default);
    # the reranker's passages (chunk_passages) keep it
    lines = {c: line for c, line in sqlite3.connect(f"file:{out}?mode=ro", uri=True).execute(
        "SELECT c.chunk_id, d.unit_line FROM chunks c JOIN docs d USING (doc_id)")}
    plain = {p.replace("\n" + lines[c], "", 1) if lines[c] else p for c, p in passages.items()}
    assert emb.calls == 1 and set(emb.texts) == plain and len(emb.texts) == len(set(emb.texts))
    assert report.vectors["chunks"] == len(ids) == report.counts["chunks"] and meta["vec_count"] == str(len(ids))
    assert not series_tables and "series_embed_json" not in meta and not (tmp_path / "series_candidates.md").exists()
    assert not {"series", "series_members", "series_embed"} & set(dataclasses.asdict(report))



# ---------------------------------------------------------------- the unit tree and each document's scope (T7d,
# addendum 2026-11-02 F; I2: a document's campus comes only from campus words it writes, never from its publisher;
# I3: doc_scope reads the text's first 3,000 characters to tell a list its first 150 cut off)

def scope_rows(conn: sqlite3.Connection, doc_id: str) -> list[tuple]:
    return conn.execute("SELECT relation, unit_id, campus, level, cohort_from FROM doc_scope WHERE doc_id=?"
                        " ORDER BY rowid", (doc_id,)).fetchall()


def applies_row(unit: str = SCHOOL, campus: str | None = None, level: str | None = None,
                cohort: int | None = None) -> tuple:
    return ("applies_to", unit, campus, level, cohort)


def publisher_row(unit: str) -> tuple:
    return ("publisher", unit, None, None, None)


def mention_row(unit: str) -> tuple:
    return ("mentions", unit, None, None, None)


def test_unit_tree_stored(built):
    # units and unit_aliases hold the tree units.load_units gives (the school first, then the list's order), and each
    # unit's name before its aliases: read back the way search reads it, it is the same tree
    paths, _, conn = built
    tree = load_units(paths["sites"], paths["units"])
    assert len(tree) == 15 and tree[SOFT].aliases == ("軟體系", "軟工系") and tree[SOFT].campus == "燕巢"
    stored = conn.execute("SELECT unit_id, name, kind, parent_id, campus FROM units ORDER BY rowid").fetchall()
    assert stored == [(u.unit_id, u.name, u.kind, u.parent_id, u.campus) for u in tree.values()]
    names = conn.execute("SELECT alias, unit_id FROM unit_aliases ORDER BY rowid").fetchall()
    assert names == [(a, u.unit_id) for u in tree.values() for a in (u.name, *u.aliases)]
    # a plain scan (search's query has no ORDER BY) keeps that order: no index that reads them back sorted
    assert conn.execute("SELECT alias, unit_id FROM unit_aliases").fetchall() == names
    assert conn.execute("SELECT unit_id, name, kind, parent_id, campus FROM units").fetchall() == stored
    aliases: dict[str, list[str]] = {}
    for alias, unit_id in names:
        aliases.setdefault(unit_id, []).append(alias)
    assert {unit_id: Unit(unit_id, name, kind, parent_id, campus, tuple(a for a in aliases[unit_id] if a != name))
            for unit_id, name, kind, parent_id, campus in stored} == tree


def test_each_document_has_its_scope(built):
    # every document: one publisher row (the section its 組別 names, else 教務處), applies_to rows (one per unit x
    # campus x level, NULL where the title and the text's opening name none, each with the cohort) and mentions
    _, report, conn = built
    assert scope_rows(conn, "dp32") == [applies_row(), publisher_row("和平教務組")]  # I2: no campus from its section
    assert scope_rows(conn, "dp50") == [applies_row(level="碩士"), publisher_row("招生企劃組")]  # 研究所 in its title
    assert scope_rows(conn, "dp60") == [applies_row(level="學士"), publisher_row("招生企劃組")]  # 學士班
    assert scope_rows(conn, "dp57") == [applies_row(campus="和平"), applies_row(campus="燕巢"),
                                        publisher_row("教務處")]  # its text: 和平與燕巢校區; no 組別
    assert scope_rows(conn, "da8") == [applies_row(cohort=113), publisher_row("和平教務組")]  # 113學年度起入學
    assert scope_rows(conn, "eev1") == [applies_row(), publisher_row("教務處")]
    assert scope_rows(conn, "da12") == []  # a copy left out: no document, no scope
    total = report.counts["docs"]["total"]
    per = dict(conn.execute("SELECT relation, count(DISTINCT doc_id) FROM doc_scope GROUP BY relation").fetchall())
    assert per == {"applies_to": total, "publisher": total}  # nothing here mentions another unit
    assert conn.execute("SELECT count(*) FROM doc_scope WHERE doc_id NOT IN (SELECT doc_id FROM docs)").fetchone() == (
        0,)


def test_scope_report(built):
    paths, report, conn = built
    s = report.scope
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert json.loads(meta["scope_json"]) == s
    assert (s["site_list"], s["units_config"]) == (str(paths["sites"]), str(paths["units"]))
    assert s["site_list_sha256"] == hashlib.sha256(paths["sites"].read_bytes()).hexdigest()
    assert s["units_config_sha256"] == hashlib.sha256(paths["units"].read_bytes()).hexdigest()
    assert (s["units"], s["aliases"]) == (15, 3)
    assert s["kinds"] == {"学校": 1, "行政单位": 2, "行政组别": 4, "学院": 3, "系所": 5}
    total = report.counts["docs"]["total"]
    assert s["docs"] == {"applies_to": total, "publisher": total, "mentions": 0}
    assert s["rows"] == {"applies_to": total + 1, "publisher": total, "mentions": 0}  # dp57 names both campuses
    # the titles: 研究所 twice, 學士班 twice, 113學年度起入學; only the link's text names its campuses
    assert s["narrowed"] == {"total": 6, "title": 5, "header": 1}
    assert s["named"] == {"units": 0, "campus": {"和平": 1, "燕巢": 1}, "levels": {"碩士": 2, "學士": 2}, "cohort": 1}
    assert s["publishers"] == {"和平教務組": 14, "招生企劃組": 8, "教務處": 5}
    assert s["grp_unplaced"] == {} and s["seconds"] >= 0


SCOPE_NOTICES = [  # id, nid, gi, title, 公告日期, 組別, text: made up, on the export's patterns
    ("p90", 801, 3, "各學系學生申請轉系說明", "2026/5/1 上午 09:00:00", "燕巢教務組", "申請方式見附件。"),
    ("p91", 802, 3, "燕巢校區補辦學生證須知", "2026/5/2 上午 09:00:00", "燕巢教務組", "遺失者請攜帶證件辦理。"),
    ("p92", 803, 7, "校史展覽開幕", "2026/5/3 上午 09:00:00", "校史室", "歡迎全校師生參觀。"),  # a 組別 no unit has
    ("p93", 804, 8, "繳費期限延長", "2026/5/4 上午 09:00:00", "出納組", "逾期請洽出納組。"),  # a section, not 教務處's
]
FEE_HEAD = "碩士班收費一覽\n系別 收費\n國文學系 20,000\n"  # 26 characters: the first row of a fee table
FEE_NOTE = "備註：本表依規定收費。\n"  # 12 characters
FEE_TAIL = "英語學系 20,000\n化學系 21,000\n數學系 21,000"


def scope_attachment(aid: str, name: str, text: str, linked: str = "p50") -> tuple:
    return (aid, "附件:pdf:有文字层", attachment_url(aid, name), name, text, [linked])


SCOPE_ATTACHMENTS = [  # all under 招生企劃組's p50 but the last
    scope_attachment("a70", "軟體系碩士班試場分配.pdf", f"{SCHOOL}\n{SOFT}碩士班招生考試\n試場分配表"),
    scope_attachment("a71", "錄取名單.pdf", "碩士班招生考試錄取名單\n化學系 正取1名\n數學系 正取2名\n國文學系 正取1名"),
    scope_attachment("a72", "碩士班收費表.pdf", FEE_HEAD + FEE_NOTE * 12 + FEE_TAIL),  # the list goes on at 170
    scope_attachment("a73", "碩士班收費表舊版.pdf", FEE_HEAD + FEE_NOTE * 300 + FEE_TAIL),  # ... at 3,626
    scope_attachment("a74", "報名說明甲.pdf", "甲" * 147 + "化學系"),  # characters 147-149
    scope_attachment("a75", "報名說明乙.pdf", "甲" * 150 + "化學系"),  # characters 150-152
    scope_attachment("a76", "停修退費說明.pdf", "停修退費說明\n退費請洽出納組", linked="p32"),
]


def test_scope_from_the_title_the_opening_and_the_body(tmp_path):
    paths = make_inputs(tmp_path, notices=SCOPE_NOTICES, attachments=SCOPE_ATTACHMENTS)
    out = tmp_path / "x.db"
    report = run(paths, out)
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        rows = {d: scope_rows(conn, d) for d in ("dp90", "dp91", "dp92", "dp93", "da70", "da71", "da72", "da73",
                                                 "da74", "da75", "da76")}
    finally:
        conn.close()
    # I2: 燕巢教務組 publishes school-wide rules; a campus comes only from the document's own words
    assert rows["dp90"] == [applies_row(), publisher_row("燕巢教務組")]
    assert rows["dp91"] == [applies_row(campus="燕巢"), publisher_row("燕巢教務組")]
    # a 組別 that is no unit, or a unit outside 教務處: 教務處 published it (counted); another office named is a mention
    assert rows["dp92"] == [applies_row(), publisher_row("教務處")]
    assert rows["dp93"] == [applies_row(), publisher_row("教務處"), mention_row("出納組")]
    assert rows["da76"] == [applies_row(), publisher_row("和平教務組"), mention_row("出納組")]
    # a department and a level in the title narrow; three departments in the opening are a list: mentions
    assert rows["da70"] == [applies_row(SOFT, level="碩士"), publisher_row("招生企劃組")]
    assert rows["da71"] == [applies_row(level="碩士"), publisher_row("招生企劃組"), mention_row("國文學系"),
                            mention_row("化學系"), mention_row("數學系")]
    # I3: the first 3,000 characters tell a list the first 150 cut off (without them: 國文學系 alone), not one that
    # starts later
    assert rows["da72"] == [applies_row(level="碩士"), publisher_row("招生企劃組"), mention_row("國文學系")]
    assert rows["da73"] == [applies_row("國文學系", level="碩士"), publisher_row("招生企劃組")]
    # the header is the first 150 characters
    assert rows["da74"] == [applies_row("化學系"), publisher_row("招生企劃組")]
    assert rows["da75"] == [applies_row(), publisher_row("招生企劃組")]
    s = report.scope
    assert s["grp_unplaced"] == {"校史室": 1, "出納組": 1} and s["docs"]["mentions"] == 4
    assert [w for w in report.warnings if "組別" in w] == [
        "2 份文件的組別不是所属单位的组、按所属单位算发布者：校史室 1、出納組 1"]
    assert s["narrowed"] == {"total": 12, "title": 9, "header": 3}  # the fixed 6; dp91, da70, da72, da73 by title,
    #                                                                   da71 and da74 by their opening
    assert s["named"] == {"units": 3, "campus": {"和平": 1, "燕巢": 2}, "levels": {"學士": 2, "碩士": 6}, "cohort": 1}
    assert s["publishers"] == {"和平教務組": 15, "招生企劃組": 14, "燕巢教務組": 2, "教務處": 7}


def test_publisher_is_the_section_its_group_names_else_its_unit(tmp_path):
    paths = make_inputs(tmp_path)
    tree = load_units(paths["sites"], paths["units"])
    assert build.publisher_of("教務處", "燕巢教務組", tree) == "燕巢教務組"
    assert build.publisher_of("教務處", "和平校區教務組", tree) == "和平教務組"  # an alias
    assert build.publisher_of("教務處", "", tree) == build.publisher_of("教務處", "教務處", tree) == "教務處"
    assert build.publisher_of("教務處", "出納組", tree) == "教務處"  # a section of another office
    assert build.publisher_of("教務處", "校史室", tree) == "教務處"  # no unit
    assert build.publisher_of("不在樹上的單位", "燕巢教務組", tree) is None  # whose part it is cannot be told


def test_documents_whose_unit_is_not_in_the_tree(tmp_path):
    # a site list without 教務處 (its sections then hang under the school): no document can be placed under a
    # publisher, so none gets a publisher row, each applies to the school, and the build warns
    paths = make_inputs(tmp_path)
    paths["sites"].write_text(json.dumps({"sites": [s for s in UNIT_SITES if s["n"] != "教務處"]}, ensure_ascii=False),
                              encoding="utf-8")
    report = run(paths, tmp_path / "x.db")
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        assert scope_rows(conn, "dp32") == [applies_row()]
        per = dict(conn.execute("SELECT relation, count(*) FROM doc_scope GROUP BY relation").fetchall())
    finally:
        conn.close()
    total = report.counts["docs"]["total"]
    assert per == {"applies_to": total + 1} and report.scope["docs"]["publisher"] == 0
    assert report.scope["grp_unplaced"] == {}  # whose part a 組別 is cannot be told: not counted as unplaced
    assert report.warnings == [f"{total} 份文件的所属单位不在单位树上：没有发布者，适用范围按全校算"]


def test_no_site_list_no_unit_stage(tmp_path):
    # build_db without a site list (its default; the command line always passes one): the unit tables stay empty,
    # which search treats as neutral, and the report and meta say so
    paths = make_inputs(tmp_path)
    report = run(paths, tmp_path / "x.db", site_list=None)
    conn = sqlite3.connect(f"file:{tmp_path / 'x.db'}?mode=ro", uri=True)
    try:
        counts = [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("units", "unit_aliases", "doc_scope")]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()
    assert counts == [0, 0, 0] and report.scope == build.SCOPE_SKIPPED and "--site-list" in report.scope["skipped"]
    assert json.loads(meta["scope_json"]) == build.SCOPE_SKIPPED


def test_a_broken_unit_config_stops_the_build(tmp_path):
    paths = make_inputs(tmp_path)
    out = tmp_path / "aca.db"
    run(paths, out)
    good = out.read_bytes()
    paths["units"].write_text(UNITS_YAML + "  化學系: {campuss: 燕巢, evidence: 欄名打錯}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        run(paths, out)
    assert out.read_bytes() == good and not (tmp_path / "aca.db.tmp").exists()


def test_doc_scope_takes_the_three_relations_only(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        build.create_schema(conn)
        conn.execute("INSERT INTO doc_scope(doc_id, relation, unit_id) VALUES ('d1', 'mentions', '出納組')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO doc_scope(doc_id, relation, unit_id) VALUES ('d1', 'audience', '出納組')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO doc_scope(doc_id, relation, unit_id) VALUES ('d1', 'publisher', NULL)")
    finally:
        conn.close()
