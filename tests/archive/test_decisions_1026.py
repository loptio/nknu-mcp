"""The owner's decisions of 10-26 (round-1 design §10.2.1, §10.5, §13, §14.1, §18.2b), each first written here as a
test that failed (O5 and O4's rows that land nowhere lock what the program already does):

O1 a menu item that is the school's broken link (R1 bad_link) may be marked as the school's (人看过) the way a page's
   address is: only bad_link, only with a note, void once the page it was seen on is fetched again, listed all the
   same; once marked it no longer holds R1 back, and the dashboard and the reports say 学校那边的问题（人看过）; some
   of them marked, those count as the school's at once and the rest still hold R1 back (review 10-26, O review);
O2 R5 (the 25 questions' answer pages) and verify's G item are gone, and an old reconciliation's R5 holds nothing back;
O3 the 10-24 choice page is gone: /choose, /choose/save, `nknu archive choices`, choices.py;
O4 a round-1 row no page uses that was to be a file and is redirected to a web page is not_file (连接失败), as a page's
   attachment is; a person may mark it the school's the same way, and one whose redirects land nowhere for what that
   comes to when option A (record 五·16) lists it: bad_link, server_error (review 10-26, O review 【Claude 的执行办法】);
O5 a gate that compared nothing (没比到) does not keep a site from 爬完, and is said beside its state.

Offline: the school is the tests' fake network or httpx.MockTransport's, the viewer listens on 127.0.0.1 only. Every
text and address is made up; no person's name."""

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import crawler as crawl_mod
from nknu.archive import monitor, outcome, progress, reconcile, rehearsal, viewer
from nknu.archive.frontier import Frontier, utc_now
from nknu.archive.reconcile import FAIL, PASS, SKIP, WARN, Sources
from tests.archive import test_round1_after_crawl as e2e
from tests.archive.test_reconcile import BODY, PLAN, SITE, Arch, W, _old
from tests.archive.test_review_phase4 import cc_also

ERR = f"{W}/default.aspx?aspxerrorpath=/old-menu.aspx"  # a school error page's address (outcome.ERROR_SIGNATURES)
NOTE = "學校選單連到錯誤頁，人看過"


@pytest.fixture
def arch(tmp_path):
    a = Arch(tmp_path)
    yield a
    a.close()


def _menu_page(arch: Arch, path: str = "/", target: str = ERR) -> int:
    """A stored page whose menu (nav) links the school's error page, and the link row the crawler writes of it."""
    uid = arch.page(path, f"<html><body><nav><a href='{target}'>舊系統</a></nav><main><p>{BODY}</p></main>"
                          "</body></html>")
    arch.f.link(uid, target, "舊系統", outcome.ERROR_OUT)
    return uid


def _rec(arch: Arch) -> reconcile.Reconciled:
    return reconcile.reconcile_sites(arch.f, arch.root, [SITE], batch="t", plan=PLAN, sources=Sources(cc_dir=None))


def _r1(arch: Arch) -> reconcile.Check:
    return _rec(arch).sites[0].checks["R1"]


# ---------------------------------------------------------------- O1: a menu item that is the school's broken link


def test_o1_a_marked_menu_bad_link_no_longer_holds_r1_back_and_is_listed_all_the_same(arch):
    home = _menu_page(arch)
    r1 = _r1(arch)
    assert r1.status == WARN and r1.counts["bad_link"] == 1  # 要人看 until a person looked
    got = reconcile.review_menu_mark(arch.root, ERR, note=NOTE)
    assert [(m.site, m.target, m.page_id) for m in got] == [(SITE, ERR, home)]
    rec = _rec(arch)
    r1 = rec.sites[0].checks["R1"]
    assert r1.status == PASS and r1.counts["bad_link"] == 0 and r1.counts["school_side"] == 1
    assert [(i["target"], i["status"], i["note"]) for i in r1.items] == [(ERR, "school_side", NOTE)]
    assert "学校那边的问题（人看过）" in rec.text() and ERR in rec.text()
    stored = json.loads(arch.f.q("SELECT checks FROM reconcile_site WHERE site=?", (SITE,))[0][0])["R1"]
    assert stored["status"] == "pass" and stored["items"][0]["status"] == "school_side"


def test_o1_the_mark_is_void_once_the_page_it_was_seen_on_is_fetched_again(arch):
    home = _menu_page(arch)
    reconcile.review_menu_mark(arch.root, ERR, note=NOTE)
    assert _r1(arch).status == PASS
    arch.f.x("UPDATE url SET fetched_at=? WHERE id=?", ("2099-01-01T00:00:00+08:00", home))  # the same answer again
    r1 = _r1(arch)
    assert r1.status == WARN and r1.counts["bad_link"] == 1 and r1.counts["school_side"] == 0
    assert monitor.menu_marks(arch.f, SITE) == {}


def test_o1_only_a_menu_bad_link_with_a_note_may_be_marked(arch):
    _menu_page(arch)
    with pytest.raises(ValueError, match="note"):
        reconcile.review_menu_mark(arch.root, ERR, note="  ")
    with pytest.raises(ValueError, match="bad_link"):  # the home page itself: it has a row, nothing is broken
        reconcile.review_menu_mark(arch.root, f"{W}/", note=NOTE)
    other = f"{W}/default.aspx?aspxerrorpath=/in-the-text.aspx"
    uid = arch.page("/text.html", f"<html><body><main><p>{BODY}</p><a href='{other}'>舊連結</a></main></body></html>")
    arch.f.link(uid, other, "舊連結", outcome.ERROR_OUT)
    with pytest.raises(ValueError, match="menu"):  # a broken link of the school's, but in no menu: R1 never counts it
        reconcile.review_menu_mark(arch.root, other, note=NOTE)
    with pytest.raises(ValueError, match="mark"):
        reconcile.review_menu_mark(arch.root, ERR, note=NOTE, mark="ok")
    lock = crawl_mod.hold_run_lock(arch.root)
    try:
        with pytest.raises(crawl_mod.AlreadyRunning):  # never while a run goes on (design §6.9)
            reconcile.review_menu_mark(arch.root, ERR, note=NOTE)
    finally:
        lock.close()
    assert not arch.f.q("SELECT 1 FROM event WHERE code=?", (reconcile.MENU_MARK_EVENT,))


def test_o1_a_mark_counts_at_once_for_the_dashboard_and_verify_without_another_reconciliation(arch):
    _menu_page(arch)
    _rec(arch)  # R1 ⚠ written
    rec = monitor.reconciled(arch.f, monitor.Rounds(arch.f))[SITE]
    assert rec.gates["R1"] == WARN and rec.menu_school == 0
    assert any("R1" in x and "学校的坏链接" in x for x in rec.not_passed())
    reconcile.review_menu_mark(arch.root, ERR, note=NOTE)
    rec = monitor.reconciled(arch.f, monitor.Rounds(arch.f))[SITE]
    assert rec.gates["R1"] == PASS and rec.menu_school == 1
    assert "学校那边的问题（人看过）" in rec.menu_words()


def test_o1_review_lists_the_menu_bad_links_and_marks_one(arch):
    _menu_page(arch)
    runner = CliRunner()
    got = runner.invoke(app, ["archive", "review", "--dir", str(arch.root)])
    assert got.exit_code == 0, got.output
    assert "选单上学校的坏链接" in got.output and ERR in got.output
    got = runner.invoke(app, ["archive", "review", "--dir", str(arch.root), "--mark", "school_side", "--menu", ERR])
    assert got.exit_code == 1 and "没有标记" in got.output  # a note is a must
    got = runner.invoke(app, ["archive", "review", "--dir", str(arch.root), "--mark", "school_side", "--menu", ERR,
                              "--note", NOTE])
    assert got.exit_code == 0, got.output
    assert "学校那边的问题（人看过）" in got.output
    got = runner.invoke(app, ["archive", "review", "--dir", str(arch.root)])
    assert "学校那边的问题（人看过）" in got.output  # listed all the same, as marked


def test_o1_a_mark_on_some_of_the_menu_bad_links_moves_their_count_and_r1_still_waits_for_the_rest(arch):
    """Review 10-26 (O review, low): marked since the reconciliation but not all of them, the marked ones leave
    「还没人看过」 and count as the school's (人看过) at once; R1 passes only once every one is marked."""
    err2 = f"{W}/default.aspx?aspxerrorpath=/older-menu.aspx"
    uid = arch.page("/", f"<html><body><nav><a href='{ERR}'>舊系統</a><a href='{err2}'>舊表單</a></nav>"
                         f"<main><p>{BODY}</p></main></body></html>")
    arch.f.link(uid, ERR, "舊系統", outcome.ERROR_OUT)
    arch.f.link(uid, err2, "舊表單", outcome.ERROR_OUT)
    assert _r1(arch).counts["bad_link"] == 2  # R1 ⚠ written, two of them
    rec = monitor.reconciled(arch.f, monitor.Rounds(arch.f))[SITE]
    assert (rec.gates["R1"], rec.menu_school, rec.menu_bad) == (WARN, 0, 2)
    reconcile.review_menu_mark(arch.root, ERR, note=NOTE)
    rec = monitor.reconciled(arch.f, monitor.Rounds(arch.f))[SITE]
    assert rec.gates["R1"] == WARN  # one is still nobody's
    assert (rec.menu_school, rec.menu_bad) == (1, 1)
    assert any("R1" in x and "选单上 1 个学校的坏链接还没人看过" in x for x in rec.not_passed()), rec.not_passed()
    assert rec.menu_words() == f"选单上 1 个学校的坏链接：{outcome.RESULT_LABEL[outcome.SCHOOL_SIDE]}"
    reconcile.review_menu_mark(arch.root, err2, note=NOTE)
    rec = monitor.reconciled(arch.f, monitor.Rounds(arch.f))[SITE]
    assert (rec.gates["R1"], rec.menu_school, rec.menu_bad) == (PASS, 2, 0)
    assert not any("R1" in x for x in rec.not_passed())


def _bad_menu_world(tmp_path: Path) -> e2e.World:
    """The made-up school, its menu linking one of its own error pages on every page."""
    w = e2e.make_world(tmp_path)
    for url, html in list(w.school.html.items()):
        w.school.html[url] = w.shown[url] = html.replace("</nav>", f"<a href='{ERR}'>舊系統</a></nav>")
    return w


def test_o1_end_to_end_the_site_is_done_once_a_person_marked_its_broken_menu_link(tmp_path):
    w = _bad_menu_world(tmp_path)
    e2e._all_steps(w)
    got = w.not_done(monitor.UNRECONCILED)  # R1 ⚠: the school's broken menu link, nobody looked yet
    assert any("R1" in x for x in got.sites[e2e.SITE].why), got.sites[e2e.SITE].why
    reconcile.review_menu_mark(w.root, ERR, note=NOTE)
    dash, ver, got = w.state()
    assert (dash, ver) == ("done", monitor.DONE), got.sites[e2e.SITE].why
    assert any("学校那边的问题（人看过）" in x for x in got.sites[e2e.SITE].warnings)
    assert any("学校那边的问题（人看过）" in x for x in e2e._by_no(got.checks)["18"].lines)
    _d, html = w.dashboard()
    assert "学校那边的问题（人看过）" in html
    d = progress.Dashboard(w.root, background=False, cache_s=0)
    try:
        d.refresh_slow()
        page = viewer.progress_site_html(d.site(e2e.SITE))
    finally:
        d.close()
    assert "学校那边的问题（人看过）" in page
    res = w.reconcile()  # reconciled again: R1 passes by itself, the item listed as the school's
    r1 = res.sites[0].checks["R1"]
    assert r1.status == PASS and r1.items[0]["status"] == "school_side"
    assert "学校那边的问题（人看过）" in reconcile.report_text(res)
    assert w.state()[:2] == ("done", monitor.DONE)


# ---------------------------------------------------------------- O2: R5 and verify's G item are gone


def test_o2_r5_is_neither_a_check_nor_a_gate():
    assert "R5" not in reconcile.CHECKS and "R5" not in reconcile.GATES and "R5" not in monitor.GATES
    assert "R5" not in reconcile.CHECK_LABEL
    assert "golden" not in {f.name for f in dataclasses.fields(Sources)}
    assert not hasattr(reconcile, "GOLDEN") and not hasattr(reconcile, "load_golden")


def test_o2_a_reconciliation_writes_no_r5_and_its_report_names_none(arch):
    _menu_page(arch)
    rec = _rec(arch)
    assert "R5" not in rec.sites[0].checks
    assert "R5" not in json.loads(arch.f.q("SELECT checks FROM reconcile_site WHERE site=?", (SITE,))[0][0])
    assert "R5" not in rec.text() and "25 题" not in rec.text()


def _old_row(f: Frontier, checks: dict, gates_ok: int) -> None:
    f.x("INSERT OR REPLACE INTO reconcile_site(site, batch, at, checks, cover_cc, cover_old, gates_ok) "
        "VALUES (?,?,?,?,NULL,NULL,?)", (SITE, "old", utc_now(), json.dumps(checks), gates_ok))
    f.event("info", "reconcile", json.dumps({"batch": "old", "sites": [SITE]}))


def test_o2_an_old_reconciliations_r5_holds_nothing_back(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        passing = {g: {"status": "pass"} for g in monitor.GATES}
        _old_row(f, {**passing, "R5": {"status": "skip", "note": "引文没有不连网核对"}}, 0)  # written before 10-26
        rec = monitor.reconciled(f, monitor.Rounds(f))[SITE]
        assert rec.fresh and rec.passed and "R5" not in rec.not_passed()
        assert reconcile.fresh(f, SITE)
        _old_row(f, {**passing, "R2": {"status": "fail"}, "R5": {"status": "skip"}}, 0)  # another gate: still held
        assert not monitor.reconciled(f, monitor.Rounds(f))[SITE].passed
        _old_row(f, passing, 0)  # says no for no reason it gives: the two disagree, no pass
        assert not monitor.reconciled(f, monitor.Rounds(f))[SITE].passed
    finally:
        f.close()


def test_o2_verify_has_no_golden_item_nor_option(tmp_path):
    assert "golden" not in inspect.signature(monitor.verify).parameters
    assert "golden" not in inspect.signature(monitor.checks).parameters
    assert "golden" not in inspect.signature(rehearsal.rehearse).parameters
    assert not hasattr(monitor, "golden_check")
    assert "--golden" not in progress.COMMANDS["after"] and "verify" in progress.COMMANDS["after"]
    f = Frontier(tmp_path / "frontier.db")
    f.close()
    got = CliRunner().invoke(app, ["archive", "verify", "--dir", str(tmp_path), "--golden", "q.json"])
    assert got.exit_code == 2  # no such option


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


class _Redirecting(e2e.School):
    """The made-up school, sending some addresses to its home page (a 302)."""

    def __init__(self, html, to_home=()):
        super().__init__(html)
        self.to_home = set(to_home)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) in self.to_home:
            self.asked.append((request.method, str(request.url)))
            return httpx.Response(302, headers={"location": e2e.HOME, **self.HTML}, content=b"")
        return super().__call__(request)


def test_o4_end_to_end_it_keeps_the_site_from_done_and_is_listed_as_a_failed_connection(tmp_path):
    old_pdf = f"{e2e.W}/files/old.pdf"
    w = e2e.make_world(tmp_path)
    cc_also(tmp_path, old_pdf)  # only Common Crawl remembers it: no page links to it
    w.school = _Redirecting(w.school.html, to_home=(old_pdf,))
    e2e._all_steps(w)
    got = w.not_done(monitor.ERROR, reconciled=True)
    assert any("连接失败" in x for x in got.sites[e2e.SITE].why), got.sites[e2e.SITE].why
    c17 = e2e._by_no(got.checks)["17"]
    assert any(old_pdf in x and outcome.RESULT_LABEL["not_file"] in x for x in c17.lines), c17.lines


# ---------------------------------------------------------------- O5: a gate that compared nothing (locks today's)


def test_o5_gates_that_compared_nothing_pass_and_are_said(tmp_path):
    res = reconcile.SiteResult(SITE, "t", {g: reconcile.Check(PASS) for g in reconcile.GATES})
    for g in ("R1", "R2", "R3a", "R4a"):
        res.checks[g].counts["compared"] = False
    assert res.gates_ok and res.uncompared() == ["R1", "R2", "R3a", "R4a"]
    f = Frontier(tmp_path / "frontier.db")
    try:
        checks = {g: {"status": "pass", **({"compared": False} if g in res.uncompared() else {})}
                  for g in monitor.GATES}
        _old_row(f, checks, 1)
        rec = monitor.reconciled(f, monitor.Rounds(f))[SITE]
        assert rec.fresh and rec.passed and rec.uncompared == ("R1", "R2", "R3a", "R4a")
        assert all(monitor.UNCOMPARED_ZH[g] in rec.blind_words() for g in rec.uncompared)
    finally:
        f.close()


def test_o5_end_to_end_a_site_whose_lists_know_nothing_of_it_is_done_and_says_so(tmp_path):
    w = e2e.make_world(tmp_path)
    w.seed_and_run()
    assert w.jscheck().reason == "finished"
    other = "https://v.nknu.edu.tw/"
    (tmp_path / "cc" / "CC-TEST.cdx").write_text(  # Common Crawl and the old archive know only another site
        f"tw,edu,nknu,v)/ 20260101000000 {json.dumps({'url': other, 'status': '200'})}\n", encoding="utf-8")
    old = _old(tmp_path / "old-other", [(other, "v.nknu.edu.tw", "done", 200)])
    res = w.reconcile(sources=Sources(cc_dir=tmp_path / "cc", old=old))
    assert {"R2", "R3a", "R4a"} <= set(res.sites[0].uncompared()) and res.sites[0].gates_ok
    dash, ver, got = w.state()
    assert (dash, ver) == ("done", monitor.DONE), got.sites[e2e.SITE].why
    st = got.sites[e2e.SITE]
    assert any("没比到" in x and "R3a" in x and "R4a" in x for x in st.warnings), st.warnings
    cs = e2e._by_no(got.checks)
    assert cs["18"].status == monitor.PASS and any("没比到" in x for x in cs["18"].lines)
    assert cs["19"].status == monitor.PASS
    assert "没比到" in w.dashboard()[1]
    assert not {g for g in reconcile.GATES if res.sites[0].checks[g].status in (FAIL, WARN, SKIP)}
