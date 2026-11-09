"""Blueprint item 22, parts 2 and 3 (the owner 11-09 「照这个做」 to the content session's proposal, relayed by it; the
read-only survey research/crawler_lab_2026-11-04/item22_1109/lists.md; the crawler session decided the rest, how each
is done is Claude's). Part 2: 列表上的日期 for every page a list row links, not only a platform's detail pages
(c.nknu's activity and forum pages), and from other sites' lists too (an SSO unit site's 书表 rows link the activity
pages of sso.nknu.edu.tw/services; the 公告中心 mirror's lists, and the pages their pager brought, link
sso.nknu.edu.tw/news's notices), whose change exports the site again; a new last column 列表上的类别, the categories
the rows state, all of them with a note when they differ; a page row sharing its file with an earlier row takes that
row's dates (lists.md §4), a list's row sharing one never. Part 3: a column 页别 before it, 「列表」 for a list's first
page, 「列表翻页」 for what its pager brought and for a GET-paged list's later pages, blank for everything else (a table
of content with a pager, a page whose platform reads no list grid, a detail page). Made-up pages of each platform's
template on the small archive of test_export_1027, the activity pages at a made-up address; the one name is O-masked.
The three reviews of 11-09 added the same-site categories, the other sites' pages with no readable answer counted, the
crawler's item entries alone, the categories' order, a list's row writing a shared file and a content table's pager
rows with no file of their own or of their list's."""

from __future__ import annotations

import dataclasses
import json
import time

import pytest

from nknu.archive import export, postback, profiles, scope
from tests.archive.test_export_1027 import BASE, HTML, LIST1, SITE, _action, _res, by_url, make_archive, rows_of
from tests.archive.test_export_1102 import NOTICE, NOTICE_URL
from tests.archive.test_export_dates_1108 import MORE, SSO_NEWS_PAGE, WEB, _redirect, _webidx_site

WEBIDX = "https://sso.nknu.edu.tw/services/web/default.aspx?webidx="
SERVICES = "sso.nknu.edu.tw/services"
ACT = "https://sso.nknu.edu.tw/services/activity.aspx?aid="  # a made-up address: no platform describes the page
SSO_NEWS = "sso.nknu.edu.tw/news"
LASTNEWS = "https://sso.nknu.edu.tw/news/lastnews.aspx?unid="
MIRROR = "news.nknu.edu.tw"
MIRROR_LIST = "https://news.nknu.edu.tw/nknu_news/lastnewslist_dept.aspx?catid=ACA"
NEW_COLUMNS = ("页别", "列表上的类别")


def _export(arch, tmp_path, site: str = SITE, name: str = "out", **kw):
    out = tmp_path / name
    got = export.export_site(arch.root, site, out, **kw)
    return got, out, by_url(rows_of(out / "manifest.csv")), (out / "README.md").read_text(encoding="utf-8")


def _basis(out) -> dict:
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))["导出依据"]


def _grid(rows: list[tuple[str, str, str]], heading: str = "最新消息", pager: str = "") -> str:
    """A c.nknu MasterGrid page: (date, link, title) each row, no date selector (a row's first date is its date), no
    category; pager: the grid's last row."""
    trs = "".join(f'<tr><td>{d}</td><td><a href="{href}">{t}</a></td></tr>' for d, href, t in rows)
    return (f'<html><head><title>國立高雄師範大學教務處</title></head><body><h2>{heading}</h2><form id="form1" '
            f'method="post"><table id="MasterGrid"><tr><th>日期</th><th>主題</th></tr>{trs}{pager}</table></form>'
            "</body></html>")


def _page_n(shown: int, other: int) -> str:
    """A GridView pager row: the page shown, a link to another page (Page$N)."""
    return (f"<tr><td colspan=\"2\"><span>{shown}</span> <a href=\"javascript:__doPostBack('MasterGrid','Page${other}')"
            f"\">{other}</a></td></tr>")


def _pager_press(arch, list_id: int, url: str, body: str, step: int = 2) -> int:
    """The answer to a press of a list's pager (its page step), archived at the list's address."""
    data = body.encode()
    off, sha = arch.put(f"pager{list_id}-{step}", url, HTML, data)
    aid = _action(arch.f, list_id, kind="pager", step=step, row_key=f"p{step}", row_text=str(step), target="MasterGrid",
                  argument=f"Page${step}", sniff="html", ctype=HTML, bytes=len(data), sha256=sha, warc=arch.rel,
                  warc_offset=off)
    _res(arch.f, list_id, f"pb:pager:p{step}", "pager", how="postback", action_id=aid, text=str(step))
    return aid


def _items(arch, list_id: int, prof: profiles.Profile, url: str, html: str, site: str, *,
           list_url: str | None = None, step: int | None = None) -> list[postback.Row]:
    """The item entries the crawler writes on a list page for the rows of one of its pages (postback.Chains._plan):
    item:<the row's key, as read_page keys it> naming the row its link is."""
    pr = postback.read_page(prof, url, html, site=site, list_url=list_url or url, step=step)
    for row in pr.rows:
        hit = arch.f.row(scope.canonical(row.href, url)) if row.href else None
        _res(arch.f, list_id, f"item:{row.key}", "item", need="no", why="item",
             res_id=hit["id"] if hit is not None else None, text=row.title)
    return list(pr.rows)


# ---------------------------------------------------------------- the columns


def test_the_two_new_columns_are_the_last_two_with_their_help(tmp_path):
    arch = make_archive(tmp_path / "archive")
    _got, out, _rows, readme = _export(arch, tmp_path)
    assert export.COLUMNS[-2:] == NEW_COLUMNS and export.COLUMNS[-3] == "来源"  # appended: the old ones stay put
    assert list(export.COLUMN_HELP)[-2:] == list(NEW_COLUMNS)
    assert "列表翻页" in export.COLUMN_HELP["页别"] and "内容表格" in export.COLUMN_HELP["页别"]
    assert "|" in export.COLUMN_HELP["列表上的类别"]
    # 列表上的日期 is any row's that links the page, of this site's lists or another's (the review of 11-09)
    assert "公告在列表上" not in export.COLUMN_HELP["日期来源"] and "别的网站" in export.COLUMN_HELP["日期来源"]
    with open(out / "manifest.csv", encoding="utf-8-sig") as fh:
        assert tuple(fh.readline().rstrip("\r\n").split(","))[-2:] == NEW_COLUMNS
    data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert data["栏位"][-2:] == list(NEW_COLUMNS) and all(list(r)[-2:] == list(NEW_COLUMNS) for r in data["项目"])
    assert "**页别**" in readme and "**列表上的类别**" in readme
    # the site's own list, whose item entries name its own notices, is no other site's (_foreign): no line of
    # _foreign_words (its text, not the README's general words on 列表上的日期)
    assert "读了那些列表" not in readme
    conn = export.connect(arch.root / "frontier.db")
    try:
        snap = export.read_snapshot(conn, SITE, time.time())
    finally:
        conn.close()
    assert any(r["role"] == "item" for r in snap.raw_res)
    assert (snap.foreign_items, snap.foreign_lists, snap.foreign_presses) == ([], {}, {})


# ---------------------------------------------------------------- part 2: every page a list row links


CASE = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>活動花絮</h2><div id="lbl_CaseShow">
<h3>期末成果展</h3><p>活動日期：2025/03/05</p><p>王O明同學分享心得。</p></div></body></html>"""
FORUM = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>意見交流</h2>
<span id="lbl_PoTitle">停車證申請問題</span><span id="lbl_PoName">王O明</span>
<span id="lbl_PoTime">2025/4/1 上午 10:20:00</span><span id="lbl_PoText">請問如何申請？</span>
<table id="MasterGrid"><tr><td>回覆：請至承辦單位辦理。</td></tr></table></body></html>"""
ACTIVE = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>研習報名</h2>
<p>發佈日期：2025/02/20</p><p>名額有限。</p></body></html>"""


def test_activity_and_forum_pages_take_their_list_rows_dates(tmp_path):
    """lists.md §2.2: c.nknu's case_detail (成教所活动花絮, 385 pages with no date) and forumsview (总务处讨论区, 654)
    are no platform's detail pages (profiles.detail_of): their rows' dates were never taken."""
    arch = make_archive(tmp_path / "archive").open()
    arch.page("case", BASE + "case_detail.aspx?cid=5", CASE)
    arch.page("forum", BASE + "forumsview.aspx?fid=7", FORUM)
    assert profiles.detail_of(BASE + "case_detail.aspx?cid=5") is None
    html = _grid([("2025/03/01", "case_detail.aspx?cid=5", "期末成果展")], "活動花絮")
    cases = arch.page("cases", BASE + "case.aspx", html, page_role="list")
    _items(arch, cases, profiles.CNKNU, BASE + "case.aspx", html, SITE)  # its item entry names the page
    # the forum's row is matched by its link alone (no item entry)
    arch.page("forums", BASE + "forums.aspx", _grid([("2025/04/01 10:20", "forumsview.aspx?fid=7", "停車證申請問題")],
                                                    "意見交流"), page_role="list")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    got_case, got_forum = rows[BASE + "case_detail.aspx?cid=5"], rows[BASE + "forumsview.aspx?fid=7"]
    # the case page's words state 活動日期 (a bare 日期 label): the row's date comes first now
    assert (got_case["网页上写的日期"], got_case["日期原文"], got_case["日期来源"]) == \
        ("2025-03-01", "2025/03/01", export.LIST_ROW)
    # the forum's time stands with no label: no date of its words; its row's
    assert (got_forum["网页上写的日期"], got_forum["日期原文"], got_forum["日期来源"]) == \
        ("2025-04-01", "2025/04/01", export.LIST_ROW)
    # c.nknu's lists name no category cell (lists.md §2.4, open question 1): none
    assert got_case["列表上的类别"] == got_forum["列表上的类别"] == ""
    assert got_case["页别"] == got_forum["页别"] == ""


def test_a_list_rows_date_replaces_a_date_in_the_pages_words_never_its_own_field(tmp_path):
    """The order stays (Writer._dates): the platform's date field, the list row, the page's words."""
    arch = make_archive(tmp_path / "archive").open()
    arch.page("active", BASE + "activeview.aspx?aid=3", ACTIVE)
    arch.page("actives", BASE + "activelist.aspx", _grid([("2025/02/21", "activeview.aspx?aid=3", "研習報名"),
                                                           ("2025/01/01", "newsdetail.aspx?nid=663&gi=4", "放榜")]),
              page_role="list")
    arch.page("notice", NOTICE_URL, NOTICE)  # its 公告日期 field: 2026-10-22
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    active = rows[BASE + "activeview.aspx?aid=3"]
    assert (active["网页上写的日期"], active["日期来源"]) == ("2025-02-21", export.LIST_ROW)  # its words: 2025-02-20
    assert (rows[NOTICE_URL]["网页上写的日期"], rows[NOTICE_URL]["日期来源"]) == ("2026-10-22", export.OWN_FIELD)


# ---------------------------------------------------------------- part 2 across sites


def _forms_page(rows: list[tuple[str, str, str, str]]) -> str:
    """A WebIdx 书表 page (menuid=N, one GET page of a get walk): (date, category, title, link) each row."""
    out = "".join(f'<div class="items-row"><div class="col-date">{d}</div><div class="col-cat">{c}</div>'
                  f'<div class="col-title"><a href="{h}">{t}</a></div></div>' for d, c, t, h in rows)
    return ('<html><body><form id="form1" method="post"><div class="items-list-wrapper">' + out
            + "</div></form></body></html>")


def _forms(arch, unit: str, rows: list[tuple[str, str, str, str]], menu: int = 214) -> int:
    """Unit site web/<unit>'s 书表 list page and the item entries the crawler wrote for its rows."""
    site = f"sso.nknu.edu.tw/web/{unit}"
    url = f"{WEBIDX}{unit}&menuid={menu}"
    html = _forms_page(rows)
    lid = arch.page(f"forms{unit}", url, html, page_role="list", host="sso.nknu.edu.tw", site=site, owner_site=site)
    _items(arch, lid, profiles.SSO_WEBIDX, url, html, site)
    return lid


def _activity(arch, aid: int, words: str = "請於期限內報名。") -> int:
    """An activity page of sso.nknu.edu.tw/services (at a made-up address): no platform describes it."""
    return arch.page(f"act{aid}", ACT + str(aid), f"<html><head><title>單位活動</title></head><body><h2>單位活動 {aid}"
                     f"</h2><p>{words}</p></body></html>", host="sso.nknu.edu.tw", site=SERVICES,
                     owner_site=SERVICES)


def test_another_sites_forms_list_dates_and_categorises_this_sites_activity_pages(tmp_path):
    """lists.md §2.3: sso.nknu.edu.tw/services's 11,186 activity pages are linked from 25 unit sites' 书表 lists only;
    the export read its own site's lists alone."""
    arch = make_archive(tmp_path / "archive").open()
    _activity(arch, 77)
    _activity(arch, 78, "發佈日期：2026-01-01")
    forms = _forms(arch, "592", [("2026-03-02", "講座", "研習活動", ACT + "77"),
                                 ("2026-03-05", "競賽", "程式競賽", ACT + "78")])
    arch.close()
    got, out, rows, readme = _export(arch, tmp_path, SERVICES)
    a77, a78 = rows[ACT + "77"], rows[ACT + "78"]
    assert (a77["网页上写的日期"], a77["日期原文"], a77["日期来源"], a77["列表上的类别"]) == \
        ("2026-03-02", "2026-03-02", export.LIST_ROW, "講座")
    assert (a78["网页上写的日期"], a78["日期来源"], a78["列表上的类别"]) == ("2026-03-05", export.LIST_ROW, "競賽")
    # nothing of the other site's list is a row of this site's export
    assert not any(url.startswith(WEBIDX) for url in rows) and len(list((out / "pages").iterdir())) == 2
    assert got.counts["网页"]["got"] == 2
    assert "读了那些列表 1 页（列表页 1）" in readme and "页读不出" not in readme
    assert any("读了那些列表 1 页" in line for line in got.lines)
    # the unit site's own export: its list dates nothing of its own, and is 列表
    _got, _out, unit, _readme = _export(arch, tmp_path, "sso.nknu.edu.tw/web/592", "unit")
    assert unit[f"{WEBIDX}592&menuid=214"]["页别"] == export.LIST_FIRST
    assert unit[f"{WEBIDX}592&menuid=214"]["存档编号"] == f"u{forms}"


def _refetch(arch, uid: int, url: str, html: str) -> None:
    """A list page fetched again (a new answer in the WARC, its row pointing there), its item entries written anew."""
    data = html.encode()
    off, sha = arch.put(f"again{uid}", url, HTML, data)
    arch.f.x("UPDATE url SET warc=?, warc_offset=?, sha256=?, bytes=?, fetched_at=? WHERE id=?",
             (arch.rel, off, sha, len(data), "2026-11-09T01:02:03+00:00", uid))
    arch.f.x("DELETE FROM page_res WHERE page_id=? AND role='item'", (uid,))


def test_a_change_of_the_other_sites_list_exports_this_site_again(tmp_path):
    """snapshot_digest reads the other sites' lists that link the site (存档内容): the export service (--skip-same)
    exports it again when one changes, and only then."""
    arch = make_archive(tmp_path / "archive").open()
    _activity(arch, 77)
    forms = _forms(arch, "592", [("2026-03-02", "講座", "研習活動", ACT + "77")])
    arch.close()
    _got, first, rows, _readme = _export(arch, tmp_path, SERVICES, "first")
    assert rows[ACT + "77"]["网页上写的日期"] == "2026-03-02"
    with pytest.raises(export.Unchanged):
        export.export_site(arch.root, SERVICES, tmp_path / "same", skip_same=first)
    # 教务处's list fetched again: no row of it links this site
    arch.open()
    _refetch(arch, arch.ids["list"], BASE + "newslist.aspx", LIST1)
    arch.close()
    with pytest.raises(export.Unchanged):
        export.export_site(arch.root, SERVICES, tmp_path / "same2", skip_same=first)
    # the unit's 书表 fetched again: the school corrected the row's date
    arch.open()
    url = f"{WEBIDX}592&menuid=214"
    html = _forms_page([("2026-03-03", "講座", "研習活動", ACT + "77")])
    _refetch(arch, forms, url, html)
    _items(arch, forms, profiles.SSO_WEBIDX, url, html, "sso.nknu.edu.tw/web/592")
    arch.close()
    _got, second, rows2, _readme = _export(arch, tmp_path, SERVICES, "second", skip_same=first)
    assert _basis(second)["存档内容"] != _basis(first)["存档内容"]
    assert (rows2[ACT + "77"]["网页上写的日期"], rows2[ACT + "77"]["日期来源"]) == ("2026-03-03", export.LIST_ROW)


def _gv(rows: list[tuple[str, str, str, str]], shown: int, other: int) -> str:
    """An SSO 公告中心 list page (gvFull): (start date, category, title, the notice's unid) each row, and its pager."""
    trs = "".join(f'<tr><td><span id="gvFull_ctl0{i}_StartDate">{d}</span></td><td><span id="gvFull_ctl0{i}_Category">'
                  f'{c}</span></td><td><a id="gvFull_ctl0{i}_linkSubject_short" href="{LASTNEWS}{u}">{t}</a></td></tr>'
                  for i, (d, c, t, u) in enumerate(rows, 2))
    pager = (f"<tr><td colspan=\"3\"><span>{shown}</span> <a href=\"javascript:__doPostBack('gvFull','Page${other}')\">"
             f"{other}</a></td></tr>")
    return ('<html><body><form id="uForm" method="post"><table id="gvFull"><tr><th>日期</th><th>類別</th><th>主旨</th>'
            f"</tr>{trs}{pager}</table></form></body></html>")


DELETED = SSO_NEWS_PAGE.replace("2026-09-08 ~ 2026-12-31", "").replace("寒假宿舍申請", "").replace(
    "日期： 民國115年12月19日起受理。", "")


NEWS = {"host": "sso.nknu.edu.tw", "site": SSO_NEWS, "owner_site": SSO_NEWS}
MIRROR_COLS = {"host": MIRROR, "site": MIRROR, "owner_site": MIRROR}


def _mirror_press(arch, lid: int, step: int, **cols) -> int:
    """A press of the mirror list's pager (its page step)."""
    return _action(arch.f, lid, kind="pager", step=step, row_key=f"p{step}", row_text=str(step), host=MIRROR,
                   site=MIRROR, profile="sso_news/gvFull", target="gvFull", argument=f"Page${step}", **cols)


def _mirror(arch) -> int:
    """The 公告中心 mirror's list (news.nknu.edu.tw) in the archive: its first page links a notice of its own, the page
    its pager brought two of sso.nknu.edu.tw/news's (AAA111 dated by its field, BBB222 deleted by the school)."""
    arch.page("dated", LASTNEWS + "AAA111", SSO_NEWS_PAGE, **NEWS)  # its 公告時間: 2026-09-08
    arch.page("deleted", LASTNEWS + "BBB222", DELETED, **NEWS)  # the school's deleted notice: its fields empty
    page1 = _gv([("2026-08-01", "公告", "鏡像公告", "CCC333")], 1, 2)
    lid = arch.page("mirror", MIRROR_LIST, page1, page_role="list", **MIRROR_COLS)
    page2 = _gv([("2026-09-01", "招生", "寒假宿舍申請", "AAA111"), ("2025-12-01", "活動", "已刪除的公告", "BBB222")], 2, 1)
    off, sha = arch.put("mirror2", MIRROR_LIST, HTML, page2.encode())
    _mirror_press(arch, lid, 2, sniff="html", ctype=HTML, bytes=len(page2.encode()), sha256=sha, warc=arch.rel,
                  warc_offset=off)
    _items(arch, lid, profiles.SSO_NEWS, MIRROR_LIST, page1, MIRROR)
    rows2 = _items(arch, lid, profiles.SSO_NEWS, MIRROR_LIST, page2, MIRROR, step=2)
    assert {r.category for r in rows2} == {"招生", "活動"}
    return lid


def test_another_sites_pager_pages_date_and_categorise_this_sites_notices(tmp_path):
    """lists.md §2.3: sso.nknu.edu.tw/news's notices linked from the 公告中心 mirror's lists (news.nknu.edu.tw) and the
    pages their pager brought: a notice's own date field first, its row's category whatever its date came from."""
    arch = make_archive(tmp_path / "archive").open()
    _mirror(arch)
    arch.close()
    got, _out, rows, readme = _export(arch, tmp_path, SSO_NEWS)
    dated, deleted = rows[LASTNEWS + "AAA111"], rows[LASTNEWS + "BBB222"]
    assert (dated["网页上写的日期"], dated["日期来源"], dated["列表上的类别"]) == ("2026-09-08", export.OWN_FIELD, "招生")
    assert (deleted["网页上写的日期"], deleted["日期来源"], deleted["列表上的类别"]) == \
        ("2025-12-01", export.LIST_ROW, "活動")
    assert MIRROR_LIST not in rows and MIRROR_LIST + "#第2页" not in rows
    assert "2 页" in readme and "翻页 1" in readme and got.counts["网页"]["got"] == 2
    assert "页读不出" not in readme


def test_another_sites_pager_press_with_no_page_is_counted(tmp_path):
    """The contract review of 11-09: a press of another site's pager that brought no page this round (failed, still
    queued) is counted in the README, as its own site's export lists it with no file; one a newer press stands for, or
    one answered with a redirect, is none of its pages there either (Builder._presses) and is not."""
    arch = make_archive(tmp_path / "archive").open()
    lid = _mirror(arch)
    _mirror_press(arch, lid, 3, state="failed", reason="connect", status=None)
    _mirror_press(arch, lid, 4, state="pending", status=None, done=None)
    _mirror_press(arch, lid, 5, state="superseded", reason="new_epoch", status=None)
    _mirror_press(arch, lid, 6, status=302, location=MIRROR_LIST)
    arch.close()
    got, _out, rows, readme = _export(arch, tmp_path, SSO_NEWS)
    assert (rows[LASTNEWS + "AAA111"]["列表上的类别"], rows[LASTNEWS + "BBB222"]["网页上写的日期"]) == ("招生", "2025-12-01")
    assert "读了那些列表 2 页（列表页 1、翻页 1）" in readme and "另有 2 页读不出" in readme
    assert "这一轮的结果是「试了 3 次还是失败」 1" in readme and "这一轮的结果是「还没抓」 1" in readme
    assert any("另有 2 页读不出" in line for line in got.lines)


def test_rows_that_give_different_categories_are_all_listed_with_a_note(tmp_path):
    """lists.md §2.5: each category once, in the first spelling seen (one compared as postback.norm reads it: the
    full-width ＡＩ is AI), joined by 「 | 」, 备注 saying so (on round one's archive 6 notices of sso.nknu.edu.tw/news,
    a category from their own site's list and one from another site's: the replay review of 11-09)."""
    arch = make_archive(tmp_path / "archive").open()
    _activity(arch, 77)
    _forms(arch, "592", [("2026-03-02", "AI講座", "研習活動", ACT + "77")])
    _forms(arch, "593", [("2026-03-02", "研習", "研習活動", ACT + "77")])
    _forms(arch, "594", [("2026-03-02", "ＡＩ講座", "研習活動", ACT + "77")])
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path, SERVICES)
    row = rows[ACT + "77"]
    assert row["列表上的类别"] == "AI講座 | 研習"
    assert "列表上有 3 行指到它，写的类别不一样：AI講座、研習，都列出" in row["备注"]
    assert (row["网页上写的日期"], row["日期来源"]) == ("2026-03-02", export.LIST_ROW)
    assert "写的日期不一样" not in row["备注"]  # the rows agree on its date


def test_another_sites_list_that_cannot_be_read_is_counted_and_stops_nothing(tmp_path):
    """A list page of another site whose record cannot be read back, one whose record is not what the archive recorded
    (its rows never read: they might be anything), and one that came to no page this round while its item entries stay
    (the contract review of 11-09: it was passed over uncounted): each counted, none of their rows used."""
    arch = make_archive(tmp_path / "archive").open()
    _activity(arch, 77)
    _forms(arch, "592", [("2026-03-02", "講座", "研習活動", ACT + "77")])
    broken = _forms(arch, "593", [("2026-03-04", "研習", "研習活動", ACT + "77")])
    arch.f.x("UPDATE url SET warc='warc/gone.warc.gz' WHERE id=?", (broken,))  # its record cannot be read back
    damaged = _forms(arch, "594", [("2026-03-05", "競賽", "研習活動", ACT + "77")])
    arch.f.x("UPDATE url SET sha256=? WHERE id=?", ("0" * 64, damaged))
    failed = _forms(arch, "595", [("2026-03-06", "徵才", "研習活動", ACT + "77")])
    arch.f.x("UPDATE url SET state='failed', reason='connect', attempts=3, status=NULL WHERE id=?", (failed,))
    arch.close()
    got, _out, rows, readme = _export(arch, tmp_path, SERVICES)
    row = rows[ACT + "77"]
    assert (row["网页上写的日期"], row["列表上的类别"]) == ("2026-03-02", "講座")  # the readable list's row only
    assert "写的日期不一样" not in row["备注"] and "写的类别不一样" not in row["备注"]
    assert "读了那些列表 1 页（列表页 1）" in readme and "另有 3 页读不出" in readme
    assert "存档里读不回来 1" in readme and "存档内容和纪录的 sha256 对不上 1" in readme
    assert "这一轮的结果是「试了 3 次还是失败」 1" in readme
    assert any("另有 3 页读不出" in line for line in got.lines)


def test_a_page_with_no_file_keeps_its_rows_category_and_no_date(tmp_path):
    """The category is the list row's whatever became of the page; a date only a page written has (Writer._list_dates,
    _dates; services's 20 rows with no file, the replay review of 11-09)."""
    arch = make_archive(tmp_path / "archive").open()
    gone = _activity(arch, 79)
    arch.f.x("UPDATE url SET state='failed', reason='connect', attempts=3, status=NULL WHERE id=?", (gone,))
    _forms(arch, "592", [("2026-03-07", "講座", "研習活動", ACT + "79")])
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path, SERVICES)
    row = rows[ACT + "79"]
    assert (row["本地档案"], row["网页上写的日期"], row["日期来源"], row["列表上的类别"]) == ("", "", "", "講座")


def test_a_row_the_crawlers_item_entry_names_the_page_dates_it_whatever_its_link_reads(tmp_path):
    """The crawler's item entry of a row (Chains._plan: its res_id, the page the crawler took the row's link for) is the
    other way a row is matched to a page, beside the row's link as the export reads it (the rules review of 11-09:
    every case above was matched both ways). Here the link reads as another address: the entry alone dates and
    categorises the page (Snap.foreign_items)."""
    arch = make_archive(tmp_path / "archive").open()
    act = _activity(arch, 77)
    site, url = "sso.nknu.edu.tw/web/592", f"{WEBIDX}592&menuid=214"
    html = _forms_page([("2026-03-02", "講座", "研習活動", ACT + "77&from=menu")])
    lid = arch.page("forms592", url, html, page_role="list", host="sso.nknu.edu.tw", site=site, owner_site=site)
    (row,) = postback.read_page(profiles.SSO_WEBIDX, url, html, site=site, list_url=url).rows
    _res(arch.f, lid, f"item:{row.key}", "item", need="no", why="item", res_id=act, text=row.title)
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path, SERVICES)
    got = rows[ACT + "77"]
    assert (got["网页上写的日期"], got["日期来源"], got["列表上的类别"]) == ("2026-03-02", export.LIST_ROW, "講座")


def test_the_sites_own_lists_categorise_its_notices(tmp_path):
    """lists.md §2.4 (the contract review of 11-09: no test said it): 1,823 pages on 18 sites take a category from
    their own site's lists: the notices a WebIdx row's press brought and one got by GET (lis 625, the unit sites), and
    sso.nknu.edu.tw/news's notices its own 公告中心 lists link (701)."""
    arch = make_archive(tmp_path / "archive").open()
    _webidx_site(arch)  # every row of its MoreNews list states 公告
    arch.page("dated", LASTNEWS + "AAA111", SSO_NEWS_PAGE, **NEWS)
    own = "https://sso.nknu.edu.tw/news/lastnewslist.aspx"
    page = _gv([("2026-09-01", "招生", "寒假宿舍申請", "AAA111")], 1, 2)
    _items(arch, arch.page("own", own, page, page_role="list", **NEWS), profiles.SSO_NEWS, own, page, SSO_NEWS)
    arch.close()
    _got, _out, rows, readme = _export(arch, tmp_path, WEB, "web")
    for url in (MORE + "#按钮=宿舍申請說明", MORE + "#按钮=獎學金公告", MORE + "&newsidx=1300"):
        assert rows[url]["列表上的类别"] == "公告", url
    assert rows[MORE]["列表上的类别"] == ""  # the list itself
    assert "读了那些列表" not in readme
    _got, _out, rows, readme = _export(arch, tmp_path, SSO_NEWS, "news")
    assert rows[LASTNEWS + "AAA111"]["列表上的类别"] == "招生" and "读了那些列表" not in readme


def test_the_categories_come_in_their_lists_order_whatever_order_the_archive_reads(tmp_path, monkeypatch):
    """Two of the site's own lists state different categories for one notice: in their lists' order (their url rows'
    ids), whatever order page_res hands their item entries in (it is read with no ORDER BY: the contract review of
    11-09)."""
    arch = make_archive(tmp_path / "archive").open()
    arch.page("dated", LASTNEWS + "AAA111", SSO_NEWS_PAGE, **NEWS)
    for i, cat in enumerate(("招生", "活動")):
        url = f"https://sso.nknu.edu.tw/news/subjectlist.aspx?cat={i}"
        page = _gv([("2026-09-01", cat, "寒假宿舍申請", "AAA111")], 1, 2)
        _items(arch, arch.page(f"own{i}", url, page, page_role="list", **NEWS), profiles.SSO_NEWS, url, page, SSO_NEWS)
    arch.close()
    read = export.read_snapshot

    def backwards(*args, **kw):
        snap = read(*args, **kw)
        snap.raw_res.reverse()
        return snap

    for name in ("asis", "backwards"):
        if name == "backwards":
            monkeypatch.setattr(export, "read_snapshot", backwards)
        _got, _out, rows, _readme = _export(arch, tmp_path, SSO_NEWS, name)
        row = rows[LASTNEWS + "AAA111"]
        assert row["列表上的类别"] == "招生 | 活動", name
        assert "写的类别不一样：招生、活動，都列出" in row["备注"], name


def test_the_pages_own_sites_category_comes_first(tmp_path):
    """The replay review of 11-09: 6 notices of sso.nknu.edu.tw/news have a category from their own site's list and one
    from another site's, each in its own site's words: the own site's first, however each row was matched (here the
    own list's row by its link alone, the other site's by the crawler's item entry too)."""
    arch = make_archive(tmp_path / "archive").open()
    _mirror(arch)  # the mirror's page 2 states 招生 for AAA111
    own = "https://sso.nknu.edu.tw/news/lastnewslist.aspx"
    arch.page("own", own, _gv([("2026-09-01", "行政資訊", "寒假宿舍申請", "AAA111")], 1, 2), page_role="list", **NEWS)
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path, SSO_NEWS)
    row = rows[LASTNEWS + "AAA111"]
    assert row["列表上的类别"] == "行政資訊 | 招生"
    assert "列表上有 2 行指到它，写的类别不一样：行政資訊、招生，都列出" in row["备注"]


def test_list_category():
    def row(key: str, category: str) -> postback.Row:
        return postback.Row(key, "標題", "", category, None)

    assert export.list_category([]) is None
    assert export.list_category([(1, row("k1", "")), (2, row("k2", " "))]) is None
    assert export.list_category([(1, row("k1", "公告")), (1, row("k1", "公告")), (2, row("k2", "公告"))]) == ("公告", "")
    # a row a list shows on each of its pages (a pinned notice) is one row
    assert export.list_category([(1, row("k1", "公告")), (1, row("k1", "公告")), (2, row("k2", "活動"))]) == \
        ("公告 | 活動", "列表上有 2 行指到它，写的类别不一样：公告、活動，都列出")


# ---------------------------------------------------------------- lists.md §4: a page row sharing its file


def test_a_page_row_sharing_its_file_takes_the_dates_of_the_row_that_wrote_it(tmp_path):
    """Two of a site's link items redirect to one page of another site: the second shared the first's file and got no
    date at all (Writer._write returned before _page; uk's 4 newsdetail rows, 105 rows on 18 sites)."""
    arch = make_archive(tmp_path / "archive").open()
    landing = LASTNEWS + "AB12CD"
    arch.page("landing", landing, SSO_NEWS_PAGE, host="sso.nknu.edu.tw", site=SSO_NEWS, owner_site=SSO_NEWS)
    _redirect(arch, BASE + "newsdetail.aspx?nid=30", landing)
    _redirect(arch, BASE + "newsdetail.aspx?nid=31", landing)
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    one, two = rows[BASE + "newsdetail.aspx?nid=30"], rows[BASE + "newsdetail.aspx?nid=31"]
    assert one["本地档案"] == two["本地档案"] != "" and "和清单里另一行是同一个档" in two["备注"]
    for row in (one, two):
        assert (row["网页上写的日期"], row["日期原文"], row["日期来源"]) == \
            ("2026-09-08", "公告時間： 2026-09-08", export.OWN_FIELD)


RULES = ("<html><head><title>國立高雄師範大學總務處</title></head><body><h2>場地借用</h2><p>更新日期：2025/08/01</p>"
         "</body></html>")


def test_a_page_row_sharing_its_file_takes_its_own_list_rows_date_before_the_pages_words(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    landing = "https://c.nknu.edu.tw/affair/rules.aspx"
    arch.page("landing", landing, RULES, site="c.nknu.edu.tw/affair", owner_site="c.nknu.edu.tw/affair")
    _redirect(arch, BASE + "newsdetail.aspx?nid=30", landing)
    _redirect(arch, BASE + "newsdetail.aspx?nid=31", landing)
    arch.page("links", BASE + "newslist.aspx?pclass=40", _grid([("2025/09/09", "newsdetail.aspx?nid=31", "借用規則")]),
              page_role="list")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    one, two = rows[BASE + "newsdetail.aspx?nid=30"], rows[BASE + "newsdetail.aspx?nid=31"]
    assert (one["网页上写的日期"], one["日期来源"]) == ("2025-08-01", export.PAGE_WORDS)
    assert (two["网页上写的日期"], two["日期来源"]) == ("2025-09-09", export.LIST_ROW)
    assert "和清单里另一行是同一个档" in two["备注"]


def _moved_list(arch, url: str, to: str) -> int:
    """A list of the site whose address now redirects (the crawler called it a list on an earlier answer)."""
    uid = _redirect(arch, url, to)
    arch.f.x("UPDATE url SET page_role='list' WHERE id=?", (uid,))
    return uid


def test_a_page_row_sharing_a_lists_file_still_takes_its_own_list_rows_date(tmp_path):
    """The rules review of 11-09: a list's row and a page row redirect to one page elsewhere, the list's row first (its
    file written for it). A list states no date of its own (none was kept for it), so the page row has none to share
    and took no date at all; it takes its own list row's, never the words of a page written for a list."""
    arch = make_archive(tmp_path / "archive").open()
    landing = "https://c.nknu.edu.tw/affair/rules.aspx"
    arch.page("landing", landing, RULES, site="c.nknu.edu.tw/affair", owner_site="c.nknu.edu.tw/affair")
    _moved_list(arch, BASE + "newslist.aspx?pclass=99", landing)
    _redirect(arch, BASE + "newsdetail.aspx?nid=31", landing)
    arch.page("links", BASE + "newslist.aspx?pclass=40", _grid([("2025/09/09", "newsdetail.aspx?nid=31", "借用規則")]),
              page_role="list")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    moved, page = rows[BASE + "newslist.aspx?pclass=99"], rows[BASE + "newsdetail.aspx?nid=31"]
    assert moved["本地档案"] == page["本地档案"] != "" and "和清单里另一行是同一个档" in page["备注"]
    assert (page["网页上写的日期"], page["日期来源"]) == ("2025-09-09", export.LIST_ROW)
    assert (moved["网页上写的日期"], moved["日期来源"], moved["页别"]) == ("", "", "")


def test_a_lists_row_sharing_a_pages_file_takes_no_date(tmp_path):
    """The other order (the rules review of 11-09: no test said it): a page row writes the file, the list's row shares
    it and takes nothing of its dates (列表页不填)."""
    arch = make_archive(tmp_path / "archive").open()
    landing = "https://c.nknu.edu.tw/affair/rules.aspx"
    arch.page("landing", landing, RULES, site="c.nknu.edu.tw/affair", owner_site="c.nknu.edu.tw/affair")
    _redirect(arch, BASE + "newsdetail.aspx?nid=30", landing)
    _moved_list(arch, BASE + "newslist.aspx?pclass=99", landing)
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    page, moved = rows[BASE + "newsdetail.aspx?nid=30"], rows[BASE + "newslist.aspx?pclass=99"]
    assert moved["本地档案"] == page["本地档案"] != "" and "和清单里另一行是同一个档" in moved["备注"]
    assert (page["网页上写的日期"], page["日期来源"]) == ("2025-08-01", export.PAGE_WORDS)
    assert (moved["网页上写的日期"], moved["日期来源"]) == ("", "")


# ---------------------------------------------------------------- part 3: 页别


def test_a_list_its_pager_pages_and_a_root_address_list(tmp_path):
    """The fixture's list and its page 2 (a press), and c.nknu's root address listing too (总务处's 111 and tecs's 12
    pager pages, which the content stage reads as other pages today: lists.md §3.3 item 4)."""
    arch = make_archive(tmp_path / "archive").open()
    root = arch.page("root", BASE, _grid([("2025/06/01", "newsview.aspx?nid=91", "校園公告")], pager=_page_n(1, 2)),
                     page_role="list")
    _pager_press(arch, root, BASE, _grid([("2025/05/01", "newsview.aspx?nid=92", "舊公告")], pager=_page_n(2, 1)))
    arch.close()
    _got, _out, rows, readme = _export(arch, tmp_path)
    assert rows[BASE + "newslist.aspx"]["页别"] == rows[BASE]["页别"] == export.LIST_FIRST == "列表"
    assert rows[BASE + "newslist.aspx#第2页"]["页别"] == rows[BASE + "#第2页"]["页别"] == export.LIST_PAGER == "列表翻页"
    for url in (BASE + "newslist.aspx#按钮=寒假宿舍申請", BASE + "newsview.aspx?nid=1", BASE + "about.aspx",
                BASE + "uploadfile/rule.pdf", BASE + "newslist.aspx#按钮=成績單申請表"):
        assert rows[url]["页别"] == "", url
    assert "「列表」2 行、「列表翻页」2 行" in readme


HONOR = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>榮譽榜</h2><form id="form1" method="post">
<table id="MasterGrid"><tr><th>年度</th><th>事蹟</th></tr><tr><td>113</td><td>王O明 榮獲優良教師</td></tr>{pager}
</table></form></body></html>"""


def test_a_table_of_content_with_a_pager_is_no_list(tmp_path):
    """成教所's honor.aspx (lists.md §3.3 item 3): a grid with no link, paged by Page$N, its words only there."""
    arch = make_archive(tmp_path / "archive").open()
    honor = arch.page("honor", BASE + "honor.aspx", HONOR.format(pager=_page_n(1, 2)), page_role="list")
    _pager_press(arch, honor, BASE + "honor.aspx", HONOR.format(pager=_page_n(2, 1)))
    arch.close()
    pr = postback.read_page(profiles.CNKNU, BASE + "honor.aspx", HONOR.format(pager=_page_n(1, 2)), site=SITE)
    assert pr.rowset is not None and pr.content and pr.has_pager  # the reading the export goes by
    _got, _out, rows, _readme = _export(arch, tmp_path)
    assert rows[BASE + "honor.aspx"]["页别"] == rows[BASE + "honor.aspx#第2页"]["页别"] == ""
    assert rows[BASE + "honor.aspx#第2页"]["本地档案"]


def test_a_content_tables_pager_rows_go_by_their_own_reading_else_their_lists(tmp_path):
    """The rules review of 11-09 (no test said it): two honor rolls, one whose page 2 press brought no file (its list's
    reading says it is a table of content), one whose first page brought none this round (its page 2's own reading
    says so): no 页别 for either's rows (Writer._list_kinds)."""
    arch = make_archive(tmp_path / "archive").open()
    honor = arch.page("honor", BASE + "honor.aspx", HONOR.format(pager=_page_n(1, 2)), page_role="list")
    aid = _action(arch.f, honor, kind="pager", step=2, row_key="p2", row_text="2", target="MasterGrid",
                  argument="Page$2", state="failed", reason="connect", status=None)
    _res(arch.f, honor, "pb:pager:p2", "pager", how="postback", action_id=aid, text="2")
    other = BASE + "honor.aspx?kind=2"
    lid = arch.page("honor2", other, HONOR.format(pager=_page_n(1, 2)), page_role="list")
    arch.f.x("UPDATE url SET state='failed', reason='connect', attempts=3, status=NULL WHERE id=?", (lid,))
    _pager_press(arch, lid, other, HONOR.format(pager=_page_n(2, 1)))
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    assert (rows[BASE + "honor.aspx#第2页"]["本地档案"], rows[BASE + "honor.aspx#第2页"]["页别"]) == ("", "")
    assert rows[BASE + "honor.aspx"]["页别"] == ""
    assert (rows[other]["本地档案"], rows[other]["页别"]) == ("", "")
    assert rows[other + "#第2页"]["本地档案"] and rows[other + "#第2页"]["页别"] == ""


def _more(row_title: str, pager: list[tuple[str, str]]) -> str:
    """A WebIdx MoreNews page: one notice row, and its pager box's links (text, address)."""
    links = "".join(f'<a href="{href}">{text}</a>' for text, href in pager)
    return ('<html><body><form id="form1" method="post"><div class="news-list-wrapper"><div class="news-row">'
            '<div class="col-date">2026-05-21</div><div class="col-cat">公告</div><div class="col-title">'
            f'<a href="default.aspx?webidx=592&selpage=MoreNews&newsidx=1300">{row_title}</a></div></div></div>'
            f'<div class="news-pagination">{links}</div></form></body></html>')


def test_a_get_paged_lists_later_page_and_its_entry_pages(tmp_path):
    """lists.md §3.3 item 2: MoreNews's pagenum=2 is 列表翻页; the entry address and the one numbered 1 are both the
    list's first page (列表)."""
    web = "sso.nknu.edu.tw/web/592"
    more = WEBIDX + "592&selpage=MoreNews"
    p1, p2 = more + "&pagenum=1", more + "&pagenum=2"
    arch = make_archive(tmp_path / "archive").open()
    sso = {"host": "sso.nknu.edu.tw", "site": web, "owner_site": web}
    entry = arch.page("entry", more, _more("宿舍申請說明", [("1", p1), ("2", p2)]), page_role="list", **sso)
    one = arch.page("p1", p1, _more("宿舍申請說明", [("1", p1), ("2", p2)]), page_role="list", **sso)
    two = arch.page("p2", p2, _more("獎學金公告", [("1", p1), ("2", p2)]), page_role="list", **sso)
    for src in (entry, one, two):
        for text, dst in (("1", one), ("2", two)):
            if src != dst:
                _res(arch.f, src, p1 if dst == one else p2, "pager", res_id=dst, text=text)
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path, web)
    assert (rows[more]["页别"], rows[p1]["页别"], rows[p2]["页别"]) == (export.LIST_FIRST, export.LIST_FIRST,
                                                                       export.LIST_PAGER)


def test_a_site_whose_platform_reads_no_list_grid_marks_none(tmp_path):
    """电机系 (lists.md §3.3 item 8): the crawler calls its pages lists by their GET pager links alone; no platform
    reads a list grid there, and many pages hold their words only there."""
    elec = "elec.nknu.edu.tw"
    first, later = "https://elec.nknu.edu.tw/news.html", "https://elec.nknu.edu.tw/news.html?page=2"
    arch = make_archive(tmp_path / "archive").open()
    own = {"host": elec, "site": elec, "owner_site": elec}
    body = '<html><body><h2>系所公告</h2><p>系友名單</p><a href="news.html?page=2">2</a></body></html>'
    a = arch.page("e1", first, body, page_role="list", **own)
    b = arch.page("e2", later, body.replace("page=2", "page=1").replace(">2<", ">1<"), page_role="list", **own)
    _res(arch.f, a, later, "pager", res_id=b, text="2")
    _res(arch.f, b, first, "pager", res_id=a, text="1")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path, elec)
    assert rows[first]["页别"] == rows[later]["页别"] == ""


TEACHER = """<html><head><title>國立高雄師範大學教務處</title></head><body><div class="FocusMain">
<span id="lbl_TeaName">王O明</span><span id="lbl_TeaTitle">副教授</span><table id="MasterGrid"><tr><th>檔案</th></tr>
<tr><td><a href="uploadfile/plan.pdf">教學計畫</a></td></tr></table></div></body></html>"""


def test_a_detail_pages_file_grid_is_no_list_once_its_platform_calls_it_a_detail(tmp_path, monkeypatch):
    """lists.md §3.3 item 6: a teacher's page (teachercorner) holds a grid of its files; no platform calls it a detail
    today, so it is 列表 (51 pages); a Detail of it (open question for part 1) makes it blank."""
    arch = make_archive(tmp_path / "archive").open()
    arch.page("teacher", BASE + "teachercorner.aspx?tid=3", TEACHER, page_role="list")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    assert rows[BASE + "teachercorner.aspx?tid=3"]["页别"] == export.LIST_FIRST
    teacher = profiles.Detail(r"/teachercorner\.aspx", text=".FocusMain", title="#lbl_TeaName")
    cnknu = dataclasses.replace(profiles.CNKNU, details=(*profiles.CNKNU.details, teacher))
    monkeypatch.setattr(profiles, "PROFILES", tuple(cnknu if p is profiles.CNKNU else p for p in profiles.PROFILES))
    _got, _out, rows, _readme = _export(arch, tmp_path, name="with_detail")
    assert rows[BASE + "teachercorner.aspx?tid=3"]["页别"] == ""


def test_a_detail_address_that_shows_a_list_is_a_list(tmp_path):
    """tecs's newsview address written with &amp; (lists.md §3.3 item 5): neither its title area nor its text area is
    on the page (NO_AREA), which shows the list; its pager pages are 列表翻页."""
    shown = BASE + "newsview.aspx?kind=1&amp;nid=60"
    arch = make_archive(tmp_path / "archive").open()
    lid = arch.page("shown", shown, _grid([("2025/06/01", "newsview.aspx?nid=93", "研習公告")], pager=_page_n(1, 2)),
                    page_role="list")
    _pager_press(arch, lid, shown, _grid([("2025/05/01", "newsview.aspx?nid=94", "舊公告")], pager=_page_n(2, 1)))
    arch.close()
    assert profiles.detail_of(shown) is not None
    _got, _out, rows, _readme = _export(arch, tmp_path)
    assert export.NO_AREA in rows[shown]["缺什么"]
    assert (rows[shown]["页别"], rows[shown + "#第2页"]["页别"]) == (export.LIST_FIRST, export.LIST_PAGER)


def test_later_get_pages():
    """A GET-paged list's later pages (lists.md §3.4 item 1): some pager link to the page shows a number of 2 or more,
    or a next page's word, and none shows 1 (postback.Chains.get_walk's numbers of 2 or more, on every list page of
    11-09)."""
    def pager(res_id, text, *, how="get", role="pager"):
        return {"page_id": 1, "res_key": f"u{res_id}-{text}", "role": role, "how": how, "res_id": res_id,
                "text": text}

    entries = [pager(2, "2"), pager(3, "[3]"), pager(4, "下一頁"), pager(5, "1"), pager(6, "1"), pager(6, "2"),
               pager(7, "Next »"), pager(8, "2", how="postback"), pager(9, "2", role="item"), pager(None, "2"),
               pager(10, "首頁"), pager(11, "１２")]
    assert postback.later_get_pages(entries) == {2, 3, 4, 7, 11}
    assert postback.later_get_pages([]) == set()
