"""网站（资料总览）, live, inside 检视台 since 2026-11-21 (nknu.ops.overview, overview_data; a service of its own in
deploy/lab-overview/ until then): the data read from a small library, and the rules of keeping it current.

The fixture is a made-up library in the repo layout the lab PC has under /work: three sites (a department, an office, and
a 品保 site whose only page was merged into the department's), an export manifest, the round-1 list and a build report.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

import pytest

from nknu.ops import overview as server
from nknu.ops import overview_data as data_mod

TODAY = datetime.date(2026, 11, 15)
EPOCH = datetime.date(1970, 1, 1)



def day(text: str) -> int:
    return (datetime.date.fromisoformat(text) - EPOCH).days


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    lib_dir = tmp_path / "research" / "pipeline_2026-11-02"
    lib_dir.mkdir(parents=True)
    db = sqlite3.connect(lib_dir / "aca.db")
    db.executescript("""
        create table meta (key text, value text);
        create table sites (site text, name text, unit text, data_as_of text, missing_pages int, missing_files int,
                            docs int, export text, deleted_notices int, under_read int);
        create table docs (doc_id text, site text, kind_class text, kind_raw text, has_text int, chars int,
                           ident_date text, ident_kind text, tier text, unit_line text, grp text, url text);
        create table dates (doc_id text, kind text, date text, raw text, source text);
        create table units (unit_id text, name text, kind text, parent_id text, campus text);
    """)
    db.executemany("insert into meta values (?, ?)", [
        ("built_at", "2026-11-15T00:54:29+08:00"), ("schema_version", "pipeline-8"),
        ("copies:dA1", json.dumps([{"id": "dZ9", "url": "https://sso.nknu.edu.tw/site/9/page.aspx?x=1"}]))])
    db.executemany("insert into sites values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        ("c.nknu.edu.tw/aaa", "甲系", "甲學系", "2026-11-08", 0, 0, 4, "archive-export/sites/a", 2, 1),
        ("c.nknu.edu.tw/bbb", "總務處", "總務處", "2026-11-08", 0, 0, 1, None, 0, 0),
        ("sso.nknu.edu.tw/site/9", "甲系品保", "甲學系", "2026-11-08", 0, 0, 0, None, 0, 0),
        ("sso.nknu.edu.tw/site/8", "總務處品保", "總務處", "2026-11-08", 0, 0, 0, "archive-export/sites/r", 0, 0)])
    db.executemany("insert into docs values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        ("dA1", "c.nknu.edu.tw/aaa", "notice", "newsdetail", 1, 100, "2026-11-01", "published", "main",
         "單位：甲學系 類別：活動、演講", "一組", "https://c.nknu.edu.tw/aaa/n1"),
        ("dA2", "c.nknu.edu.tw/aaa", "attachment", "附件:pdf:含OCR", 0, 0, None, None, "main", "", "", "u2"),
        ("dA3", "c.nknu.edu.tw/aaa", "page", "teacher", 1, 50, "2026-12-25", "published", "main", "", "", "u3"),
        ("dA4", "c.nknu.edu.tw/aaa", "event", "calendar", 1, 20, None, None, "main", "", "", "u4"),
        ("dB1", "c.nknu.edu.tw/bbb", "page", "page", 1, 10, "2020-01-02", "server", "background", "", "", "u5")])
    db.executemany("insert into dates values (?, ?, ?, ?, ?)", [
        ("dA4", "event_start", "2026-11-10", "", ""), ("dA4", "event_start", "2030-01-01", "", "")])
    db.executemany("insert into units values (?, ?, ?, ?, ?)", [
        ("U0", "國立高雄師範大學", "学校", None, ""), ("C1", "文學院", "学院", "U0", ""),
        ("D1", "甲學系", "系所", "C1", ""), ("A1", "總務處", "行政单位", "U0", "")])
    db.commit()
    db.close()
    export = tmp_path / "archive-export" / "sites" / "a"
    export.mkdir(parents=True)
    (export / "manifest.csv").write_text(
        "类型,结果,分类,页别,本地档案\n"
        "网页,抓到了,,列表翻页,\n网页,下一轮再抓,,,\n网页,跳到别的网址：x,,,\n按钮,要登录才看得到,,,\n"
        "附件,学校那边已经不存在,,,\n附件,连不上,连接失败,,\n图片,抓到了,,,images/1.png\n"
        # 11-19: one Google Sites picture two pages show is two rows of one file: one picture
        "图片,抓到了,,,images/1.png\n", encoding="utf-8-sig")
    redirects = tmp_path / "archive-export" / "sites" / "r"
    redirects.mkdir(parents=True)
    (redirects / "manifest.csv").write_text("类型,结果,分类,页别\n网页,跳到别的网址（目标另算）,,\n", encoding="utf-8-sig")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "archive_round1.json").write_text(json.dumps({"sites": [
        {"site": "c.nknu.edu.tw/aaa", "name": "甲系（舊）網站", "home": "https://c.nknu.edu.tw/aaa/", "imp": "本系重要",
         "round": 1},
        {"site": "c.nknu.edu.tw/bbb", "name": "總務處", "round": 1},
        {"site": "x.example.edu.tw", "name": "乙網站", "home": "https://x.example.edu.tw", "why": "js_site",
         "imp": "偶尔有用", "platform": "自建", "round": 2}]}, ensure_ascii=False), encoding="utf-8")
    (lib_dir / "build_report.json").write_text(json.dumps({
        "excluded": {"newslist#翻页/按钮": 3, "album": 2, "newsdetail(学校删掉)": 1, "notes": "not a count"},
        "dedupe": {"copies": {"removed": 4}, "recurring": {"removed": 5}}}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_sites_are_ordered_by_size_and_grouped_by_the_unit_tree(root: Path) -> None:
    d = data_mod.collect(root, today=TODAY)
    assert [s["k"] for s in d["sites"]] == ["c.nknu.edu.tw/aaa", "c.nknu.edu.tw/bbb", "sso.nknu.edu.tw/site/8",
                                            "sso.nknu.edu.tw/site/9"]
    groups = [d["groups"][s["g"]] for s in d["sites"]]
    assert groups == ["文學院", "行政處室", "行政處室", "文學院"]
    assert d["built"] == "2026-11-15 00:54" and d["today"] == "2026-11-15"
    assert d["sites"][0]["u"] == "甲學系"


def test_a_site_record_carries_kinds_quality_and_sections(root: Path) -> None:
    a = data_mod.collect(root, today=TODAY)["sites"][0]
    assert a["t"] == [1, 1, 1, 1, 0] and a["b"] == 3
    assert a["ud"] == 1 and a["u3"] == [0, 0, 1] and a["nt"] == 1
    assert a["c"] == [100, 50, 0, 20, 0]
    assert a["fm"] == [0, 1, 0, 0, 0] and a["fmc"] == [0, 0, 0, 0, 0]  # a scanned PDF read by OCR, no text found
    # [name, documents, characters]: the page's 文件數 / 字數 switch picks one (owner 11-15)
    assert a["pk"] == [["師資陣容", 1, 50]]
    assert a["gr"] == [["一組", 1, 100]]
    assert a["ds"] == [2, 0, 0, 0, 0] and a["dsc"] == [150, 0, 0, 0, 0]
    assert a["ck"] is None  # no checks file
    assert a["uc"] == [0, 0, 0]
    assert a["n"] == "甲系" and a["f"] == "甲系（舊）網站" and a["imp"] == "本系重要"
    assert a["o"] == "甲系 甲系（旧）网站"  # for the finder only: the library's own name, and the name in Simplified
    assert a["del"] == 2 and a["ur"] == 1


def test_crawl_outcomes_come_from_the_export_the_library_was_built_from(root: Path) -> None:
    a = data_mod.collect(root, today=TODAY)["sites"][0]
    # got, next, gone, fail, login, todo; a redirect counts in none of them; one file of two rows is one picture
    assert a["cr"] == {"p": [1, 1, 0, 0, 1, 0], "f": [0, 0, 1, 1, 0, 0], "i": [1, 0, 0, 0, 0, 0]}
    assert a["pg"] == 1


def test_days_stop_at_the_end_of_next_year_and_the_latest_date_is_not_in_the_future(root: Path) -> None:
    d = data_mod.collect(root, today=TODAY)
    a = d["sites"][0]
    assert d["dayMax"] == day("2027-12-31")
    # a day: notice, page, attachment, event counts, then the same four in characters; the event's 20 characters are
    # shared out over its two days, and the half on 2030-01-01 is beyond the chart like the day itself
    assert d["daily"]["0"] == [[day("2026-11-01"), 1, 0, 0, 0, 100, 0, 0, 0], [day("2026-11-10"), 0, 0, 0, 1, 0, 0, 0, 10],
                               [day("2026-12-25"), 0, 1, 0, 0, 0, 50, 0, 0]]
    assert a["fut"] == 1  # the event on 2030-01-01 is counted, not drawn
    assert a["last"] == "2026-11-01" and a["first"] == "2026"
    assert d["sites"][1]["last"] == "2020-01-02" and d["sites"][1]["bg"] == 1


def test_a_site_left_without_documents_says_where_its_pages_went(root: Path) -> None:
    sites = {s["k"]: s for s in data_mod.collect(root, today=TODAY)["sites"]}
    merged, moved = sites["sso.nknu.edu.tw/site/9"], sites["sso.nknu.edu.tw/site/8"]
    assert merged["t"] == moved["t"] == [0, 0, 0, 0, 0]
    assert merged["note"] == "這個網站的網頁和「甲系」網站的重複，已併到那邊"
    assert moved["note"] == "這個網站的網址都會跳到別的網站，內容算在那邊"
    assert sites["c.nknu.edu.tw/aaa"]["note"] == ""


def test_the_sites_left_for_round_two_and_what_the_build_left_out(root: Path) -> None:
    d = data_mod.collect(root, today=TODAY)
    assert d["later"] == [{"n": "乙網站", "o": "乙网站", "h": "https://x.example.edu.tw", "w": "整站靠 JavaScript",
                           "m": "偶爾有用", "p": "自建"}]
    assert d["listed"] == 5
    assert dict(d["excluded"]) == {"列表頁和翻頁頁": 3, "相簿照片": 2, "學校已刪除、只剩模板的公告": 1,
                                   "重複或副本（同樣的內容只留一份）": 4, "每週重複的行事曆（合成一個系列）": 5}


def test_what_the_build_counts_itself_is_not_counted_twice(root: Path) -> None:
    # 11-16: a build since 11-14 counts its merged copies and folded series in excluded itself; the series' key holds
    # 「重复」 too and was read as a copy, and dedupe's counts were added on top (33,745 shown for 14,980)
    report = root / "research" / "pipeline_2026-11-02" / "build_report.json"
    report.write_text(json.dumps({
        "excluded": {"newsdetail(重复)": 2, "附件:pdf(副本)": 4, "行事历(重复系列)": 5},
        "dedupe": {"copies": {"removed": 4}, "recurring": {"removed": 5}}}, ensure_ascii=False), encoding="utf-8")
    d = data_mod.collect(root, today=TODAY)
    assert dict(d["excluded"]) == {"重複或副本（同樣的內容只留一份）": 6, "每週重複的行事曆（合成一個系列）": 5}


def test_the_owner_s_simplified_list_is_shown_in_traditional_and_the_school_s_own_text_as_written(root: Path) -> None:
    # the owner's site list writes most names, importance, platform and why in Simplified, some names as the school does
    (root / "config" / "archive_round1.json").write_text(json.dumps({"sites": [
        {"site": "c.nknu.edu.tw/aaa", "name": "甲系（旧）网站", "imp": "全校重要", "round": 1},
        {"site": "c.nknu.edu.tw/bbb", "name": "总务处", "imp": "偶尔有用", "round": 1},
        {"site": "y.example.edu.tw", "name": "台灣歷史文化及語言研究所（短网址）", "imp": "偶尔有用",
         "platform": "自建（厂商写的 PHP 系统）", "why": "附件接口，不是网站", "round": 2},
        {"site": "z.example.edu.tw", "name": "乙网站", "imp": "不是资讯网站", "platform": "其他（转址）",
         "why": "other_site", "round": 2},
        {"site": "w.example.edu.tw", "name": "超现代台湾学研究中心", "imp": "全校重要", "platform": "Google 协作平台",
         "why": "offsite_unit", "round": 2},
        {"site": "v.example.edu.tw", "name": "台风停课公告", "round": 2}]}, ensure_ascii=False), encoding="utf-8")
    d = data_mod.collect(root, today=TODAY)
    a, b = d["sites"][0], d["sites"][1]
    assert a["f"] == "甲系（舊）網站" and a["n"] == "甲系" and a["imp"] == "全校重要"
    assert b["n"] == b["u"] == "總務處" and b["imp"] == "偶爾有用"  # named like its unit, which the page relies on
    assert "总务处" in b["o"] and d["later"][0]["o"] == "超现代台湾学研究中心"  # a query typed in Simplified still finds it
    # sorted on the list's own importance words (全校重要, 偶尔有用, 不是资讯网站, none), shown converted; 台 stays as the
    # school writes it, also in the school's own name (台灣) next to the owner's note; a mainland word of the owner's
    # note takes Taiwan's (接口 -> 介面)
    assert [(x["n"], x["m"], x["p"], x["w"]) for x in d["later"]] == [
        ("超現代台灣學研究中心", "全校重要", "Google 協作平台", "校外的本校單位網站"),
        ("台灣歷史文化及語言研究所（短網址）", "偶爾有用", "自建（廠商寫的 PHP 系統）", "附件介面，不是網站"),
        ("乙網站", "不是資訊網站", "其他（轉址）", "其他網站"),
        ("颱風停課公告", "", "", "")]


def test_a_change_is_used_once_it_has_held_for_one_check() -> None:
    w = server.Watch()
    assert w.due(("a",))  # the first time: at once
    w.finished(("a",))
    assert not w.due(("a",))
    assert not w.due(("b",))  # a build may still be writing
    assert not w.due(("c",))
    assert w.due(("c",))
    w.finished(("c",))
    assert not w.due(("c",))


def test_the_page_carries_its_data_safely_and_the_service_keeps_one_computation(root: Path, monkeypatch) -> None:
    page = server.render("<title>x</title><script id='data'>__DATA__</script>", {"t": "</script><b>"})
    assert page.startswith(b"<!doctype html>") and b"</script><b>" not in page and b"<\\/script>" in page
    assert b'<html lang="zh-Hant-TW">' in page and "lang='zh-Hant-TW'" in server.WAITING.decode()
    monkeypatch.setattr(server, "today", lambda: TODAY)
    state = server.State(root, "__DATA__")
    state.tick()
    first = state.data
    assert first["live"] is True and len(first["stamp"]) == 16 and state.error == ""
    state.tick()  # nothing changed: the same data stays
    assert state.data is first
    said = state.said()
    assert said == {"stamp": first["stamp"], "built": first["built"], "state": "ok", "error": ""}
    linked = server.render("<script id='links'>__LINKS__</script>", {}).decode("utf-8")
    assert '"stamp": "/sites/stamp"' in linked  # where the page asks whether it is still current


def test_a_failed_computation_tells_the_page_why(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "today", lambda: TODAY)
    state = server.State(tmp_path, "__DATA__")  # no library there
    state.tick()
    assert state.data is None and state.error.startswith("統計失敗：") and state.said()["state"] == "error"


def test_the_build_s_checks_are_shown_only_for_the_library_they_were_made_of(root: Path) -> None:
    # spec 2026-11-16-data-fixes §五: site_checks.json, as research/pipeline_2026-11-02/lab_pc/site_report.py writes it
    sites = [s["k"] for s in data_mod.collect(root, today=TODAY)["sites"]]
    checks = {"db": "x", "built_at": "2026-11-15T00:54:29+08:00", "today": "2026-11-15", "new": [[sites[0], "fragments"]],
              "sites": {sites[0]: {"flags": ["fragments", "no_date", "same_title"], "docs": 3, "same_title": 16,
                                   "same_opening": [1, 3], "fragments": 7, "spike_dates": 4, "no_date": [1, 3],
                                   "empty_pages": [0, 1], "self": [3, 3]},
                        "nowhere": {"flags": [], "spike_dates": 0}}}
    path = root / data_mod.CHECKS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checks), encoding="utf-8")
    d = data_mod.collect(root, today=TODAY)
    a = d["sites"][0]
    assert d["checked"] == "2026-11-15 00:54"
    # no_date is the page's own 無日期 meter; the kinds in the page's order; what is new against the last build
    assert a["ck"] == {"f": ["same_title", "fragments"], "nw": ["fragments"], "st": 16, "so": [1, 3], "fr": 7,
                       "em": [0, 1], "se": [3, 3], "sp": 4}
    assert all(s["ck"] is None for s in d["sites"][1:])
    checks["built_at"] = "2026-11-14T00:00:00+08:00"  # the last library's checks, before this one's have run
    path.write_text(json.dumps(checks), encoding="utf-8")
    d = data_mod.collect(root, today=TODAY)
    assert d["checked"] == "" and all(s["ck"] is None for s in d["sites"])
    path.write_text("{not json", encoding="utf-8")
    assert data_mod.collect(root, today=TODAY)["checked"] == ""


def test_a_file_dated_by_its_own_table_row_is_a_date_source_of_its_own(root: Path) -> None:
    # the library of 11-16 (spec 2026-11-16-data-fixes §二): a file's own row on a file table dates it, ident_kind
    # 「published」 like a notice's own date; the page names it apart (DSRC's fifth)
    db = sqlite3.connect(root / data_mod.DB)
    db.execute("insert into docs values ('dA5', 'c.nknu.edu.tw/aaa', 'attachment', '附件:office', 1, 30, '2026-10-01',"
               " 'published', 'main', '', '', 'https://c.nknu.edu.tw/aaa/f#按钮=表一')")
    db.commit()
    db.close()
    a = data_mod.collect(root, today=TODAY)["sites"][0]
    assert a["ds"] == [2, 0, 0, 0, 1] and a["dsc"] == [150, 0, 0, 0, 30]


# ---------------------------------------------------------------- redesign 2026-11-17 (research/dashboard_redesign_2026-11-17)

PAGE = server.PAGE.read_text(encoding="utf-8")


def _line(prefix: str) -> str:
    """The page's one JS statement that starts with `prefix` (up to its closing semicolon)."""
    start = PAGE.index(prefix)
    return PAGE[start:PAGE.index(";\n", start) + 1]


def test_a_tile_names_its_site_only_where_the_name_reads_whole() -> None:
    """diagnosis.md 資料總覽 ②: small tiles printed their names one character a line (性別教/育研究/所) or cut to
    「原住民…」. A name now gets at most two lines (three where a line holds 8 characters), at least 3 characters a
    line, or no label at all (the hover card names it)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    cases = [  # per (characters a line), room (lines that fit), need (lines the name needs) -> lines drawn
        (6, 3, 1, 1), (2, 3, 1, 1), (1, 1, 1, 1), (4, 1, 1, 1), (4, 0, 1, 0),
        (4, 3, 2, 2), (3, 3, 2, 2), (2, 4, 3, 0), (2, 4, 2, 0), (3, 3, 3, 0), (8, 3, 3, 3), (8, 2, 3, 0),
        (5, 1, 2, 0), (0, 3, 99, 0)]
    script = (_line("const labelLines =") + "\nconsole.log(JSON.stringify("
              + json.dumps([c[:3] for c in cases]) + ".map(([p, r, n]) => labelLines(p, r, n))));")
    got = json.loads(subprocess.run([node, "-e", script], check=True, capture_output=True, text=True).stdout)
    assert got == [c[3] for c in cases]
    assert "lines = labelLines(per, room, need)" in PAGE and "Math.min(room, 2) : 0" not in PAGE  # no clipped name
    assert "text-wrap: balance" in _line(".tlab .tn {").replace("}", ";")  # two lines share the name evenly


def test_the_build_s_reminders_say_what_is_wrong_in_plain_words() -> None:
    """diagnosis.md 資料總覽 ③ and the owner's rule of 11-16 for new names (tried on a cold reader first)."""
    names = {"none": "沒有文件進庫", "self": "用標題找不回文件", "empty": "網頁沒文字", "same_title": "多份文件同一標題",
             "same_opening": "選單混進正文", "fragments": "附件一字一行"}
    for key, name in names.items():
        assert f"{key}: ['{name}', " in PAGE, key
    assert "const CK_SPIKE = ['檔案時間整批相同', " in PAGE
    assert set(names) == set(data_mod.CHECK_KINDS)  # every reminder the data carries has its name
    code = PAGE.split("<script", 1)[1]
    for old in ("'用標題搜不到'", "'同名標題'", "'開頭相同'", "'碎字附件'", "'整批檔案時間'"):
        assert old not in code, old
    assert 'class="tag" data-tip="這次建庫才出現的提醒，上一次建庫時沒有">新</span>' in PAGE  # 新 says since when
    assert "新出現 ' + n + ' 項</span>" in PAGE and "'新出現 ' + c.nw.length" not in PAGE


def test_every_site_leads_to_its_page_in_the_inspection_panel() -> None:
    """Structure design 2026-11-17 section 二: 網站 keeps its charts and its list, and every site in it leads to the
    one page of that site (檢視台 /site): its name in the list is a link, and the inspector has one clear way there.
    The address comes from panels.json through nknu.ops.overview (__LINKS__), not written in the page twice."""
    assert '<script id="links" type="application/json">__LINKS__</script>' in PAGE
    assert "const SITE_PAGE = LINKS.site ||" in PAGE and "const TABLES = LINKS.tables ||" in PAGE
    assert '<a class="nm-a" href="\' + esc(SITE_PAGE + encodeURIComponent(s.k)) + \'"' in PAGE
    assert 'id="iGo" hidden' in PAGE and "這個網站的詳情 →" in PAGE
    assert "go.hidden = !one; if (one) go.href = SITE_PAGE + encodeURIComponent(one.k);" in PAGE
    assert "if (ev.target.closest('a[href]')) return;" in PAGE  # a name's link goes, it does not only pick the row
