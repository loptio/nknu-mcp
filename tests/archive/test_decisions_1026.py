"""The owner's decisions of 10-26 (round-1 design §10.2.1, §10.5, §14.1) that outlived the judgment-layer cut of
11-19, each first written here as a test that failed (O4's rows that land nowhere lock what the program already does):

O3 the 10-24 choice page is gone: /choose, /choose/save, `nknu archive choices`, choices.py;
O4 a round-1 row no page uses that was to be a file and is redirected to a web page is not_file (连接失败), as a page's
   attachment is; a person may mark it the school's the same way, and one whose redirects land nowhere for what that
   comes to when option A (record 五·16) lists it: bad_link, server_error (review 10-26, O review 【Claude 的执行办法】).

O1 (a menu item that is the school's broken link, marked for R1), O2 (R5 and verify's G item gone) and O5 (a gate that
compared nothing) were about the reconciliation batch and verify, which that cut removed: their tests went with them.

Offline: the school is the tests' fake network, the viewer listens on 127.0.0.1 only. Every text and address is made
up; no person's name."""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import monitor, outcome, progress, reconcile, seeds, viewer
from nknu.archive.crawler import Crawler
from nknu.archive.frontier import Frontier
from tests.archive.test_archive import Clock, snap
from tests.archive.test_crawler_round1 import settle
from tests.archive.test_reconcile import BODY, HOME, PDF, PLAN, SITE, Q, W, WarcNet, _ok_types, _page

# ---------------------------------------------------------------- O3: the 10-24 choice page is gone


def _status(port: int, path: str, method: str = "GET") -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=b"{}" if method == "POST" else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_o3_the_choice_page_its_saving_and_its_command_are_gone(tmp_path):
    assert importlib.util.find_spec("nknu.archive.choices") is None
    assert not hasattr(viewer.Viewer, "choose") and not hasattr(viewer.Viewer, "save_choices")
    assert not hasattr(viewer, "CHOOSE_PAGE")
    Frontier(tmp_path / "frontier.db").close()
    (tmp_path / "choices-data.json").write_text(json.dumps({"sections": []}), encoding="utf-8")  # an old file
    server = viewer.serve(tmp_path, 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert _status(port, "/") == 200 and _status(port, "/progress") == 200
        assert _status(port, "/choose") == 404
        assert _status(port, "/choose/save", "POST") == 405  # nothing on the viewer takes a POST
        assert "选择" not in urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
    finally:
        server.shutdown()
    assert not (tmp_path / "choices.json").exists()
    got = CliRunner().invoke(app, ["archive", "choices", "--dir", str(tmp_path)])
    assert got.exit_code == 2  # no such command


# ---------------------------------------------------------------- O4: a row no page uses, redirected to a web page


def _row(f: Frontier, path: str, **cols) -> int:
    row = {"url": f"https://{SITE}{path}", "path": path, "host": SITE, "site": SITE, "owner_site": SITE, "tier": 0,
           "kind": "file", "kind0": "file", "depth": 1, "found_via": "commoncrawl", "state": "done", "status": 200,
           "sniff": "pdf", "fetched_at": "2026-10-26T00:00:00+00:00"}
    row.update(cols)
    with f.lock:
        return f.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


def _orphan_world(tmp_path: Path) -> tuple[Frontier, dict[str, int]]:
    f = Frontier(tmp_path / "frontier.db")
    f.x("INSERT INTO site_plan(site, name, imp, round, home) VALUES (?,?,?,?,?)",
        (SITE, "测试单位", "全校重要", "1", f"https://{SITE}/"))
    home = _row(f, "/", kind="page", kind0="page", sniff="html", found_via="seed")
    ids = {"home": home}
    for name, kind0 in (("old.pdf", "file"), ("old.jpg", "image"), ("sitemap.xml", "sitemap")):
        ids[name] = _row(f, f"/{name}", kind0=kind0, kind="image" if kind0 == "image" else "file", status=302,
                         final_url=f"https://{SITE}/", sniff=None)
    ids["moved.pdf"] = _row(f, "/moved.pdf", status=302, final_url=f"https://{SITE}/new.pdf", sniff=None)
    ids["new.pdf"] = _row(f, "/new.pdf", found_via="redirect")
    return f, ids


def test_o4_a_file_no_page_uses_redirected_to_a_web_page_is_not_file(tmp_path):
    f, _ids = _orphan_world(tmp_path)
    try:
        got = {o.url.rsplit("/", 1)[1]: o for o in monitor.orphans(f).get(SITE, [])}
    finally:
        f.close()
    for name in ("old.pdf", "old.jpg", "sitemap.xml"):  # to be a file (its kind0), it came back a web page
        assert got[name].result == "not_file", (name, got[name])
        assert got[name].tone == monitor.RED and outcome.user_bucket(got[name].result) == "failed"
        assert f"https://{SITE}/" in got[name].text()  # where it went
    assert "moved.pdf" not in got  # it lands on a row of its own, judged by itself
    assert got["new.pdf"].result == "ok"


def test_o4_a_person_may_mark_it_the_schools_until_it_is_fetched_again(tmp_path):
    f, ids = _orphan_world(tmp_path)
    try:
        assert f.mark_school_side(ids["old.pdf"], "學校把附件連結指到首頁，人看過") == "not_file"
        got = {o.url.rsplit("/", 1)[1]: o for o in monitor.orphans(f)[SITE]}
        assert got["old.pdf"].result == outcome.SCHOOL_SIDE and got["old.pdf"].tone == monitor.GOOD
        assert got["old.jpg"].result == "not_file"  # only the row marked
        assert any(r["id"] == ids["old.jpg"] for r in reconcile.markable_rows(f, SITE))  # what review lists
        f.x("UPDATE url SET fetched_at=? WHERE id=?", ("2026-10-27T00:00:00+00:00", ids["old.pdf"]))
        got = {o.url.rsplit("/", 1)[1]: o for o in monitor.orphans(f)[SITE]}
        assert got["old.pdf"].result == "not_file"  # fetched again: the mark is void
    finally:
        f.close()


def _nowhere_world(tmp_path: Path) -> tuple[Frontier, dict[str, int]]:
    """Rows no page uses whose redirects land on no row: one sent to a school error page's address (the link table
    says out:error_page), two that send each other round (a loop), one sent to an address with no row and no verdict."""
    f, ids = _orphan_world(tmp_path)
    err = f"https://{SITE}/default.aspx?aspxerrorpath=/err.pdf"
    ids["err.pdf"] = _row(f, "/err.pdf", status=302, final_url=err, sniff=None)
    f.link(ids["err.pdf"], err, "", outcome.ERROR_OUT)
    ids["loop1.pdf"] = _row(f, "/loop1.pdf", status=302, final_url=f"https://{SITE}/loop2.pdf", sniff=None)
    ids["loop2.pdf"] = _row(f, "/loop2.pdf", status=302, final_url=f"https://{SITE}/loop1.pdf", sniff=None)
    ids["lost.pdf"] = _row(f, "/lost.pdf", status=302, final_url=f"https://{SITE}/nowhere.pdf", sniff=None)
    return f, ids


def test_o4_a_row_no_page_uses_that_lands_nowhere_may_be_marked_for_what_record_5_16_option_a_lists(tmp_path):
    """Review 10-26 (O review, medium), kept as written 【Claude 的执行办法】: what it comes to is outcome.markable's to
    judge, the same as for a page's resource (a page's attachment in a loop is server_error there and could be marked
    at f6c8fa6 already). Option A (record 五·16) lists bad_link (学校的链接坏了) and server_error (学校服务器出错); a
    loop is server_error by the orphan rule of design §10.5 【Claude 的执行办法】, for the owner to overturn. What is
    ours (not_discovered) is refused; a new fetch ends the mark, the same answer included."""
    f, ids = _nowhere_world(tmp_path)
    try:
        got = {o.url.rsplit("/", 1)[1]: o.result for o in monitor.orphans(f)[SITE]}
        assert {k: got[k] for k in ("err.pdf", "loop1.pdf", "loop2.pdf", "lost.pdf")} == {
            "err.pdf": "bad_link", "loop1.pdf": "server_error", "loop2.pdf": "server_error",
            "lost.pdf": "not_discovered"}
        listed = {r["id"]: r["result"] for r in reconcile.markable_rows(f, SITE)}
        assert listed[ids["err.pdf"]] == "bad_link" and listed[ids["loop1.pdf"]] == "server_error"
        assert ids["lost.pdf"] not in listed
        assert f.mark_school_side(ids["err.pdf"], "學校把附件連到錯誤頁，人看過") == "bad_link"
        assert f.mark_school_side(ids["loop1.pdf"], "學校伺服器跳轉繞圈，人看過") == "server_error"
        with pytest.raises(ValueError, match="not one result"):
            f.mark_school_side(ids["lost.pdf"], "人看過")
        got = {o.url.rsplit("/", 1)[1]: o.result for o in monitor.orphans(f)[SITE]}
        assert (got["err.pdf"], got["loop1.pdf"], got["loop2.pdf"]) == (outcome.SCHOOL_SIDE, outcome.SCHOOL_SIDE,
                                                                         "server_error")  # only the rows marked
        for name in ("err.pdf", "loop1.pdf"):  # fetched again, the same answer: the mark is void
            f.x("UPDATE url SET fetched_at=? WHERE id=?", ("2026-10-27T00:00:00+00:00", ids[name]))
        got = {o.url.rsplit("/", 1)[1]: o.result for o in monitor.orphans(f)[SITE]}
        assert (got["err.pdf"], got["loop1.pdf"]) == ("bad_link", "server_error")
    finally:
        f.close()


def _dashboard_state(root: Path) -> str:
    """The site's state as the dashboard shows it."""
    d = progress.Dashboard(root, background=False, cache_s=0)
    try:
        d.refresh_slow()
        return d.site(SITE)["state"]
    finally:
        d.close()


def test_o4_end_to_end_it_keeps_the_site_from_done_and_is_listed_as_a_failed_connection(tmp_path):
    """Seeded from the list and a made-up Common Crawl index, then crawled on the tests' fake network into a real WARC.
    (Before the cut of 11-19 this went on through the reconciliation and verify, on test_round1_after_crawl's world;
    the site's state is monitor.site_status's, which the dashboard shows, and its problem list is what verify's item
    17 printed.)"""
    old_pdf = f"{W}/files/old.pdf"
    cc = tmp_path / "cc"
    cc.mkdir()
    (cc / "CC-TEST.cdx").write_text(  # only Common Crawl remembers it: no page links to it
        f"tw,edu,nknu,w)/x 20260101000000 {json.dumps({'url': old_pdf, 'status': '200'})}\n", encoding="utf-8")
    routes = {"robots.txt": snap("x", 404),
              "/files/old.pdf": lambda u: snap(u, 302, headers={"location": HOME}),  # the school sends it home now
              "/files/a.pdf": lambda u: snap(u, body=PDF, headers={"content-type": "application/pdf"}),
              "/about.html": lambda u: snap(u, html=_page("關於", f"<p>關於我們的內文：{BODY}</p>")),
              "/news.html": lambda u: snap(u, html=_page("消息", f"<p>最新消息列表。{BODY}</p>")),
              "/contact.html": lambda u: snap(u, html=_page("聯絡", f"<p>聯絡方式。{BODY}</p>")),
              f"{SITE}/": lambda u: snap(u, html=_page("首頁", f"<p>{BODY}</p><a href='/files/a.pdf'>簡章.pdf</a>"))}
    root = tmp_path / "archive"
    clock = Clock()
    net = WarcNet(clock, routes, root)
    c = Crawler(root, plan=PLAN, clock=clock, sleep=clock.sleep, client_factory=net,
                resolver=lambda h: (f"ip:{h}", None, h), netcheck=lambda: True, disk_free=lambda p: 10**12,
                campus_url=None)
    seeds.seed_round1(c, old=None, facts_db=None, cc_dir=cc, probe=None)
    settle(c, net, [Q])
    f = c.frontier
    try:
        _ok_types(f)  # what the browser check (or a person) says of each page type: the pages themselves are fine
        row = f.row(old_pdf)
        assert (row["found_via"], row["status"], row["final_url"]) == ("commoncrawl", 302, HOME)
        st = monitor.site_status(f)[SITE]
    finally:
        net.sink.close()
        f.close()
    assert st.state == monitor.ERROR and any("连接失败" in x for x in st.why), st.why
    lines = [i.line() for i in st.issues]
    assert any(old_pdf in x and outcome.RESULT_LABEL["not_file"] in x for x in lines), lines
    assert _dashboard_state(root) == monitor.ERROR  # the dashboard shows monitor.site_status's state, none of its own
    # nothing else holds the site back: once a person marked that row as the school's, the site is 爬完
    assert reconcile.review_mark(root, row["id"], note="學校把附件連結指到首頁，人看過") == "not_file"
    assert _dashboard_state(root) == monitor.DONE
