"""spec 2026-11-17-ingest-guard-design §二: before a rebuild takes the place of the library in use, the build gives
every document that would leave it a reason, and holds the swap when too many have none (the owner's intent through
session 爬虫judge, 11-17: 「上游不管哪一环漏了，都不会悄悄进到学生用的正式库，进库这一步自己就能拦住，并让人看得到被拦的
原因」). What explains a leaving: a merge into another document, the school's side as the export marks it, a list's
pager, a few calendar events. What does not: an export row that lost its file for another cause, a row that is gone,
a row with a file that was not read, a record with no words, a site the build was not given, a page emptied.

The night of 11-16 is the case the half-a-site guard missed: 317 attachments of one site lost their files in the
export, a fifth of that site. The sentences are made up; no model loads."""
from __future__ import annotations

import json
import sqlite3

import pytest

from nknu.content import build
from nknu.content import documents as marks
from nknu.content.corpus import GONE, MOVED
from nknu.content.documents import build_documents
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm

ACA, ACA_SITE, PDF = tc.ACA, tc.ACA_SITE, tc.PDF
LONG = "本頁說明選課流程與注意事項，請同學依年級辦理。" * 12  # well over EMPTIED_FROM characters


def forms(n: int, start: int = 0) -> list[dict]:
    return [{"kind": PDF, "url": f"{ACA}f/{i}/表{i}.pdf", "title": f"第{i}號表.pdf", "text": f"第 {i} 號表的內容。"}
            for i in range(start, start + n)]


def pages(n: int, text: str = LONG) -> list[dict]:
    return [{"kind": "page", "url": f"{ACA}page.aspx?pn={i}", "title": f"第{i}頁", "text": f"第 {i} 頁。{text}" if text else ""}
            for i in range(n)]


def site(tmp_path, name: str, rows: list[dict], without_file: dict[str, str] | None = None, key: str = ACA_SITE,
         with_file: tuple[str, ...] = ()):
    """An export and its corpus of rows; without_file: {address: 分类} of rows the export lists but gives no file for
    (so the corpus has no record of them); with_file: addresses the export gives a file for that the corpus lacks."""
    corpus, export = tc.site_input(tmp_path / name, key, "教务处" if key == ACA_SITE else "通識中心", rows)
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    for url, said in (without_file or {}).items():
        manifest["项目"].append({"序号": str(len(manifest["项目"]) + 1), "类型": "附件", "网址": url, "最后落地网址": "",
                               "本地档案": "", "分类": said, "结果": "读取逾时"})
    for url in with_file:
        manifest["项目"].append({"序号": str(len(manifest["项目"]) + 1), "类型": "网页", "网址": url, "最后落地网址": "",
                               "本地档案": "pages/9999.html", "分类": "要人看", "结果": "页面几乎是空的"})
    (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return corpus, export


def urls(path) -> set[str]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return {r[0] for r in conn.execute("SELECT url FROM docs")}


def swap_json(out) -> dict:
    return json.loads(build.swap_file(out).read_text(encoding="utf-8"))


@pytest.fixture
def lib(tmp_path):
    """The library in use: forty forms that stay, twenty that will go, of one site."""
    paths, out = tm.tree_files(tmp_path / "tree"), tmp_path / "db" / "x.db"
    report = tm.run(paths, out, inputs=[site(tmp_path, "full", forms(60))])
    assert len(urls(out)) == 60 and report.departures == {"checked": False}  # nothing was there to compare with
    assert not build.swap_file(out).exists() and build.swap_file(out).name == "x.swap.json"
    assert (build.LEFT_TOTAL, build.LEFT_SITE) == (30, 15)
    return paths, out


# ---------------------------------------------------------------- held

def test_files_the_export_stopped_giving_hold_the_swap(tmp_path, lib):
    paths, out = lib
    lost = {r["url"]: "连接失败" for r in forms(20, start=40)}
    with pytest.raises(build.SwapHeld) as held:
        tm.run(paths, out, inputs=[site(tmp_path, "bad", forms(40), without_file=lost)])
    left = held.value.left
    assert held.value.sites == [] and left["unexplained"] == {"no_file": 20} and left["by_site"] == {ACA_SITE: 20}
    assert left["left"] == 20 and left["explained"] == {} and left["limits"] == {"total": 30, "site": 15, "wave": [50, 0.1]}
    assert len(urls(out)) == 60 and len(urls(held.value.held)) == 40 and held.value.held.name == "x.held.db"
    said = str(held.value)
    assert "没有换上" in said and "说不出原因" in said and "导出那一行没有档了 20" in said and "x.swap.json" in said
    told = swap_json(out)
    assert (told["swapped"], told["held"], told["too_many_left"], told["collapsed"]) == (False, "x.held.db", True, [])
    item = told["departures"]["items"][0]
    assert item["why"] == "no_file" and item["site"] == ACA_SITE and item["url"] in lost and "连接失败" in item["detail"]
    assert build.swap_file(held.value.held) == build.swap_file(out)  # one file for the library and the held one


def test_the_switch_and_the_force_let_it_in_and_still_say_what_left(tmp_path, lib):
    paths, out = lib
    lost = {r["url"]: "连接失败" for r in forms(20, start=40)}
    report = tm.run(paths, out, inputs=[site(tmp_path, "bad", forms(40), without_file=lost)], departure_guard=False)
    assert len(urls(out)) == 40 and report.departures["unexplained_total"] == 20
    assert (swap_json(out)["swapped"], swap_json(out)["too_many_left"], swap_json(out)["forced"]) == (True, False, False)
    tm.run(paths, out, inputs=[site(tmp_path, "full2", forms(60))])
    report = tm.run(paths, out, inputs=[site(tmp_path, "bad2", forms(40), without_file=lost)], force_swap=True)
    assert len(urls(out)) == 40 and report.departures["unexplained"] == {"no_file": 20} and swap_json(out)["forced"]


def test_the_command_holds_with_3_and_takes_the_switch(tmp_path, lib, capsys):
    paths, out = lib
    corpus, export = site(tmp_path, "bad", forms(40), without_file={r["url"]: "还在排队" for r in forms(20, start=40)})
    args = ["--input", f"{corpus}::{export}", "--out", str(out), "--aliases", str(paths["aliases"]),
            "--site-list", str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main(args) == 3
    assert "说不出原因" in capsys.readouterr().err and len(urls(out)) == 60
    assert build.main([*args, "--no-departure-guard"]) == 0 and len(urls(out)) == 40


def test_a_site_the_build_was_not_given_holds_the_swap(tmp_path, lib):
    paths, out = lib  # a line lost from the list of exports: the half-a-site guard does not see a site not built
    with pytest.raises(build.SwapHeld) as held:
        tm.run(paths, out, inputs=[site(tmp_path, "tg", [{**r, "url": r["url"].replace(ACA, tc.TG)} for r in forms(4)],
                                        key=tc.TG_SITE)])
    assert held.value.sites == [] and held.value.left["unexplained"] == {"site_not_built": 60}
    assert len(urls(out)) == 60


def test_pages_emptied_hold_the_swap_and_fifteen_do_not(tmp_path):
    paths, out = tm.tree_files(tmp_path / "tree"), tmp_path / "db" / "x.db"
    tm.run(paths, out, inputs=[site(tmp_path, "full", pages(16))])
    report = tm.run(paths, out, inputs=[site(tmp_path, "one-less", [*pages(15, text="載入中"), *pages(16)[15:]])])
    assert report.departures["unexplained"] == {"emptied": 15} and report.departures["left"] == 0
    assert "字" in report.departures["items"][0]["detail"]
    tm.run(paths, out, inputs=[site(tmp_path, "full2", pages(16))])
    with pytest.raises(build.SwapHeld) as held:
        tm.run(paths, out, inputs=[site(tmp_path, "empty", pages(16, text="載入中"))])
    assert held.value.left["unexplained"] == {"emptied": 16} and "内容被掏空 16" in str(held.value)


# ---------------------------------------------------------------- let in, and told

def test_what_the_school_took_away_is_explained_and_swapped(tmp_path):
    paths, out = tm.tree_files(tmp_path / "tree"), tmp_path / "db" / "x.db"
    notice = {"kind": "detail:c_nknu", "url": ACA + "newsdetail.aspx?nid=7&gi=2", "title": "獎學金公告",
              "text": "獎學金自即日起受理。", **tc.dated("2026-11-01")}
    old = [*pages(20), notice, *forms(60)]
    tm.run(paths, out, inputs=[site(tmp_path, "old", old)])
    gone = [{**r, "area_mark": GONE} for r in pages(20)[:17]]  # more than LEFT_SITE of them, each with its reason
    new = [*gone, *pages(20)[17:], {**notice, "area_mark": MOVED}, *forms(43)]
    report = tm.run(paths, out, inputs=[site(tmp_path, "new", new,
                                             without_file={r["url"]: build.SCHOOL_GONE for r in forms(17, start=43)})])
    left = report.departures
    assert left["checked"] and left["left"] == 35 and left["unexplained_total"] == 0 and left["items"] == []
    assert left["explained"] == {"school_deleted": 17, "school_moved": 1, "school_gone": 17}
    assert swap_json(out)["swapped"] and len(urls(out)) == 46  # more than the 30 a night may lose without a reason


def test_a_wave_of_school_deletions_in_one_site_is_held_for_a_look(tmp_path):
    paths, out = tm.tree_files(tmp_path / "tree"), tmp_path / "db" / "x.db"
    assert (build.WAVE_MIN, build.WAVE_SHARE) == (50, 0.1)
    tm.run(paths, out, inputs=[site(tmp_path, "old", forms(600))])
    some = [{**r, "area_mark": GONE} if i < 55 else r for i, r in enumerate(forms(600))]  # 55: under a tenth of 600
    report = tm.run(paths, out, inputs=[site(tmp_path, "some", some)])
    assert report.departures["explained"] == {"school_deleted": 55} and report.departures["waves"] == {}
    tm.run(paths, out, inputs=[site(tmp_path, "old2", forms(600))])
    many = [{**r, "area_mark": GONE} if i < 70 else r for i, r in enumerate(forms(600))]
    with pytest.raises(build.SwapHeld) as held:
        tm.run(paths, out, inputs=[site(tmp_path, "many", many)])
    left = held.value.left
    assert left["waves"] == {ACA_SITE: [70, 600]} and left["unexplained_total"] == 0 and len(urls(out)) == 600
    assert "超过一成" in str(held.value) and "70/600" in str(held.value) and "说不出原因" not in str(held.value)
    told = swap_json(out)
    assert (told["school_wave"], told["too_many_left"], told["swapped"]) == (True, False, False)
    report = tm.run(paths, out, inputs=[site(tmp_path, "many2", many)], force_swap=True)  # looked at, and true
    assert len(urls(out)) == 530 and report.departures["waves"] == {ACA_SITE: [70, 600]}


def test_a_few_without_a_reason_are_let_in_and_listed(tmp_path, lib):
    paths, out = lib
    kept = forms(57)  # the three last ones go three ways
    lost, unread, blank = (r["url"] for r in forms(3, start=57))
    new = [*kept, {**forms(1, start=59)[0], "title": "", "text": ""}]
    report = tm.run(paths, out, inputs=[site(tmp_path, "new", new, with_file=(unread,))])
    left = report.departures
    assert left["unexplained"] == {"no_row": 1, "row_not_read": 1, "no_text": 1} and left["unexplained_total"] == 3
    assert {i["url"]: i["why"] for i in left["items"]} == {lost: "no_row", unread: "row_not_read", blank: "no_text"}
    assert len(urls(out)) == 57 and swap_json(out)["swapped"]


# ---------------------------------------------------------------- the parts

def test_every_record_left_out_says_under_which_key():
    recs = [tc.rec("p1", "page", ACA + "1", "課程地圖", "修課順序說明。", ACA_SITE),
            tc.rec("p2", "page", ACA + "2", "舊辦法", "舊的辦法。", ACA_SITE, area_mark=GONE),
            tc.rec("p3", "page", ACA + "3", "", "", ACA_SITE),
            tc.rec("p4", "detail:c_nknu", ACA + "newsdetail.aspx?nid=4", "公告", "公告內容。", ACA_SITE, "2026-11-01",
                   area_mark=MOVED)]
    left: dict[str, str] = {}
    docs, excluded = build_documents(recs, {}, [], left=left)
    assert [d.doc_id for d in docs] == ["dp1"]
    assert left == {"dp2": "page" + marks.GONE_SUFFIX, "dp3": "page" + marks.BLANK,
                    "dp4": "detail:c_nknu" + marks.MOVED_SUFFIX}
    assert sum(excluded.values()) == len(left) and build_documents(recs, {}, [])[1] == excluded  # asked or not


def test_which_keys_explain_a_leaving():
    yes = {marks.GONE_SUFFIX: "school_deleted", marks.GONE_FILE_SUFFIX: "school_deleted",
           marks.MOVED_SUFFIX: "school_moved", marks.NO_DATA_SUFFIX: "school_no_data",
           marks.DELETED: "school_emptied_notice", marks.PAGER_SUFFIX: "list_pager", marks.DUPLICATE: "merged"}
    for suffix, why in yes.items():
        assert build._why_left("page" + suffix) == (why, True), suffix
    assert build._why_left("page" + marks.BLANK) == ("no_text", False)
    assert build._why_left("相簿") == ("kind_not_kept", False)
    for why in (*yes.values(), "no_text", "kind_not_kept", "no_file", "no_row", "row_not_read", "emptied",
                "calendar", "calendar_many", "site_not_built", "school_gone"):
        assert why in build.LEFT_WORDS, why


def _library(path, docs: list[tuple], alias: tuple[str, ...] = (), sites: tuple[str, ...] = (ACA_SITE,)):
    """A library of the columns _departures reads; docs: (doc_id, kind_class, site, text)."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE docs (doc_id TEXT, kind_class TEXT, site TEXT, title TEXT, url TEXT, text TEXT)")
        conn.execute("CREATE TABLE doc_alias (alias TEXT, doc_id TEXT, why TEXT)")
        conn.execute("CREATE TABLE sites (site TEXT)")
        conn.executemany("INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?)",
                         [(i, cls, s, "標題" + i, "https://example.invalid/" + i, text) for i, cls, s, text in docs])
        conn.executemany("INSERT INTO doc_alias VALUES (?, 'dkept', 'copy')", [(a,) for a in alias])
        conn.executemany("INSERT INTO sites VALUES (?)", [(s,) for s in sites])
    return path


def test_merged_copies_and_a_few_calendar_events_are_explained(tmp_path):
    events = [(f"e{i}", "event", ACA_SITE, "期中考") for i in range(100)]
    old = _library(tmp_path / "old.db", [("dkept", "page", ACA_SITE, "內容"), ("dcopy", "page", ACA_SITE, "內容"), *events])
    new = _library(tmp_path / "new.db", [("dkept", "page", ACA_SITE, "內容"), *events[:95]], alias=("dcopy",))
    left = build._departures(old, new, {}, [])
    assert left["left"] == 6 and left["explained"] == {"merged": 1, "calendar": 5} and left["unexplained_total"] == 0
    fewer = _library(tmp_path / "fewer.db", [("dkept", "page", ACA_SITE, "內容"), *events[:94]], alias=("dcopy",))
    left = build._departures(old, fewer, {}, [])
    assert left["explained"] == {"merged": 1} and left["unexplained"] == {"calendar_many": 6}
    assert not build._too_many_left(left) and build._too_many_left({**left, "unexplained_total": 31})
    assert build._too_many_left({**left, "by_site": {ACA_SITE: 16}}) and not build._too_many_left({"checked": False})


def test_nothing_to_compare_with_is_said_so(tmp_path):
    new = _library(tmp_path / "new.db", [("d1", "page", ACA_SITE, "內容")])
    assert build._departures(tmp_path / "none.db", new, {}, []) == {"checked": False}
    (tmp_path / "junk.db").write_bytes(b"not a database")
    assert build._departures(tmp_path / "junk.db", new, {}, []) == {"checked": False}
    with sqlite3.connect(tmp_path / "old-shape.db") as conn:
        conn.execute("CREATE TABLE docs (doc_id TEXT)")
    assert build._departures(tmp_path / "old-shape.db", new, {}, []) == {"checked": False}


def test_the_list_of_the_unexplained_stops_at_fifty_and_the_counts_do_not(tmp_path):
    old = _library(tmp_path / "old.db", [(f"d{i}", "page", ACA_SITE, "內容") for i in range(80)])
    new = _library(tmp_path / "new.db", [("d0", "page", ACA_SITE, "內容")])
    left = build._departures(old, new, {}, [])
    assert left["unexplained"] == {"no_row": 79} and len(left["items"]) == build.LEFT_SHOWN == 50
