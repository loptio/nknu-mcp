"""`nknu archive run --only PATH` (the owner 11-02: 「同意抓」 exactly the rows listed, none of the other rows of their
sites): a run that fetches the listed url rows and nothing else. Every choice of due work keeps to them (the
frontier's due queues, its next row, its waiting, next-due and pending counts, and the chains' due queues, next
press and waiting), a press runs only when its own row (its list page, action.list_id) is listed, and the run ends
once no listed row is left to fetch. A row not listed is never requested, even on the same host, and keeps its
state, attempts and next_at: the run's own start (rows left active, chains left pressing, names looked up again,
hosts nobody looked up) and the judgments it makes of a host or a directory (refusals in a row, a server that never
connects, robots.txt unreachable, the undoing of an outage) touch the listed rows only.

Everything runs on tests/archive/test_crawler_round1.py's fake network and fake clock; no address leaves this
machine."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import crawler as mod
from nknu.archive import google_api, links, monitor
from nknu.archive.frontier import Frontier
from nknu.archive.warc import WarcSink
from tests.archive import test_gsites_images_1027 as gs
from tests.archive.plans import OpenPlan
from tests.archive.test_archive import Clock, seed, snap
from tests.archive.test_crawler_round1 import QC, QUEUES, crawl, events, page, rows, timeout_err

VQ = "ip:vd.nknu.edu.tw"  # vd.nknu.edu.tw's queue (crawl()'s resolver: ip:<host> for a host QUEUES does not name)
A1 = "https://c.nknu.edu.tw/aca/a1.aspx"
A2 = "https://c.nknu.edu.tw/ccee/a2.aspx"
V3 = "https://vd.nknu.edu.tw/v3.html"
V4 = "https://vd.nknu.edu.tw/v4.html"
ROUTES = {"/aca/a1.aspx": page("<p>一</p>"), "/ccee/a2.aspx": page("<p>二</p>"), "/v3.html": page("<p>三</p>"),
          "/v4.html": page("<p>四</p>")}
T0 = 1_000_000.0  # the fake clock's start (tests/archive/test_archive.Clock)


@pytest.fixture(autouse=True)
def _workers_leave_when_idle(monkeypatch):
    """The fake clock moves only with requests and gaps: a worker with nothing due leaves at once (run() starts it
    again when something comes due), so that run() ends without waiting in real time."""
    monkeypatch.setattr(mod, "IDLE_EXIT_S", -1.0)


def _known(f: Frontier, *hosts: str) -> None:
    """robots.txt and the missing page of these hosts are known already: the only requests a run makes are rows'."""
    for host in hosts:
        f.set_host(host, robots_state="none", robots_at=T0, probe_status=404)


def four(tmp_path) -> dict[str, int]:
    """Two hosts, four pending rows: A1, A2 on c.nknu.edu.tw, V3, V4 on vd.nknu.edu.tw (ids 1–4, in that order)."""
    seeder, _, _ = crawl(tmp_path, {})
    for url in (A1, A2, V3, V4):
        seeder.seed_url(url)
    _known(seeder.frontier, "c.nknu.edu.tw", "vd.nknu.edu.tw")
    ids = {url: rows(seeder)[url]["id"] for url in (A1, A2, V3, V4)}
    seeder.frontier.close()
    assert sorted(ids.values()) == [1, 2, 3, 4]
    return ids


def frontier(tmp_path) -> Frontier:
    return Frontier(tmp_path / "frontier.db")


def add(f: Frontier, url: str, **kw) -> int:
    host = urlsplit(url).hostname
    cols = {"site": host, "unit": None, "tier": 1, "kind": "file", "depth": 1, "found_via": "link",
            "found_from": None, "owner_site": "c.nknu.edu.tw/aca", "kind0": "file", "prio": 2, **kw}
    f.add(url, host=host, **cols)
    return f.row(url)["id"]


def only_crawler(tmp_path, only, routes=None, **kw):
    """The run's crawler on the fake network, given --only (only: url ids)."""
    return crawl(tmp_path, {**ROUTES, **(routes or {})}, only=frozenset(only), **kw)


def run(crawler) -> None:
    t = threading.Thread(target=crawler.run, kwargs={"poll_s": 0.01}, daemon=True)
    t.start()
    t.join(30)
    if t.is_alive():
        crawler.halt("stop_file")
        t.join(10)
        pytest.fail("the run did not end by itself")


def run_only(tmp_path, only, routes=None, **kw):
    crawler, net, clock = only_crawler(tmp_path, only, routes, **kw)
    run(crawler)
    return crawler, net, clock


def kept(row) -> tuple:
    return row["state"], row["attempts"], row["next_at"], row["reason"]


def press(f: Frontier, list_id: int, *, kind: str = "detail", key: str = "k1", state: str = "pending") -> int:
    """A press planned on the page list_id (its target never matters: these presses must not be sent)."""
    cur = f.conn.execute(
        "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset, "
        "target, argument, row_key, state, created) VALUES (?,1,1,?,?,?,?,'test/-','none.warc.gz',0,?,'',?,?,?)",
        (list_id, kind, QC, "c.nknu.edu.tw", "c.nknu.edu.tw/ccee", "https://c.nknu.edu.tw/ccee/post.aspx", key,
         state, T0))
    return cur.lastrowid


def press_state(crawler, aid: int) -> str:
    return crawler.frontier.q("SELECT state FROM action WHERE id=?", (aid,))[0][0]


# ---------------------------------------------------------------- the listed rows, and only those


def test_only_the_listed_rows_are_requested_and_the_others_keep_their_state_attempts_and_due_time(tmp_path):
    ids = four(tmp_path)
    crawler, net, _ = run_only(tmp_path, {ids[A1], ids[V3]})
    assert sorted(net.seen) == sorted([A1, V3])  # rows 2 and 4 share a host with a listed row, and are not asked
    got = rows(crawler)
    assert got[A1]["state"] == got[V3]["state"] == "done"
    assert kept(got[A2]) == kept(got[V4]) == ("pending", 0, 0.0, None)


def test_the_run_ends_finished_once_the_listed_rows_are_done_and_says_how_many_were_listed(tmp_path):
    ids = four(tmp_path)
    crawler, _, _ = run_only(tmp_path, {ids[A1], ids[V3]})
    assert crawler.end_reason == "finished"
    start, end = (json.loads(events(crawler, code)[-1]) for code in ("run_start", "run_end"))
    assert (start["only"], start["only_pending"]) == (2, 2)
    assert (end["reason"], end["pending"], end["presses"], end["only"]) == ("finished", 0, 0, 2)
    assert crawler.frontier.waiting()[0] == 2  # the rows not listed still wait, for a later run


def test_a_listed_row_waiting_for_a_retry_follows_end_wait_and_unlisted_rows_due_now_are_left(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.x("UPDATE url SET attempts=1, reason='timeout', next_at=? WHERE id=?", (T0 + 1800, ids[A1]))
    f.close()
    crawler, net, _ = run_only(tmp_path, {ids[A1]})  # end_wait_s 600 (the class's default)
    assert net.seen == [] and crawler.end_reason == "waiting"
    end = json.loads(events(crawler, "run_end")[-1])
    assert end["pending"] == 1 and end["next_due"] == pytest.approx(T0 + 1800)


def test_links_found_on_a_listed_row_are_enqueued_but_not_fetched(tmp_path):
    ids = four(tmp_path)
    new = "https://c.nknu.edu.tw/aca/new.aspx"
    crawler, net, _ = run_only(tmp_path, {ids[A1]}, {"/aca/a1.aspx": page(f'<p><a href="{new}">新的</a></p>')})
    assert rows(crawler)[new]["state"] == "pending" and new not in net.seen
    assert net.seen == [A1] and crawler.end_reason == "finished"


def test_only_with_sites_takes_the_rows_that_are_in_both(tmp_path):
    ids = four(tmp_path)
    crawler, net, _ = run_only(tmp_path, {ids[A1], ids[V3]}, sites=frozenset({"c.nknu.edu.tw/aca"}))
    assert net.seen == [A1] and crawler.end_reason == "finished"
    assert rows(crawler)[V3]["state"] == "pending"  # listed, but of a site the run was not given
    end = json.loads(events(crawler, "run_end")[-1])
    assert end["sites"] == ["c.nknu.edu.tw/aca"] and end["only"] == 2


PDF = "https://vd.nknu.edu.tw/files/f.pdf"
PDF_ROUTE = {"/files/f.pdf": lambda u: snap(u, body=b"%PDF-1.4 made up", headers={"content-type": "application/pdf"})}


def test_run_start_and_run_end_name_the_sites_the_listed_rows_count_for(tmp_path):
    """Not every site (null): the listed rows' own sites and those of the pages that use them, the only sites whose
    records the run can change (review 11-02: monitor reads a run_end over every site as newer than every site's
    browser check)."""
    ids = four(tmp_path)
    f = frontier(tmp_path)
    listed = add(f, PDF, owner_site="vd.nknu.edu.tw")
    f.x("UPDATE url SET state='done', status=200 WHERE id=?", (ids[A2],))  # a page of c.nknu.edu.tw/ccee
    f.set_page_res(ids[A2], [{"res_key": PDF, "how": "get", "role": "file", "need": "must", "res_id": listed}])
    f.close()
    crawler, net, _ = run_only(tmp_path, {listed}, PDF_ROUTE)
    assert net.seen == [PDF] and crawler.end_reason == "finished"
    start, end = (json.loads(events(crawler, code)[-1]) for code in ("run_start", "run_end"))
    assert start["sites"] == end["sites"] == ["c.nknu.edu.tw/ccee", "vd.nknu.edu.tw"]


def test_a_listed_rows_address_in_its_other_scheme_is_fetched_even_when_it_had_a_row_of_its_own(tmp_path):
    """The same resource (design §6.6, review 11-02: taken as it is): the run fetches that row as the listed one, and
    its run_end names that row's site too."""
    four(tmp_path)
    f = frontier(tmp_path)
    f.set_host("c.nknu.edu.tw", scheme_only="https")
    listed = add(f, "http://c.nknu.edu.tw/aca/t.pdf")
    add(f, "https://c.nknu.edu.tw/aca/t.pdf", owner_site="c.nknu.edu.tw/ccee")  # a row of its own, of another site
    f.close()
    crawler, net, _ = run_only(tmp_path, {listed}, {"/aca/t.pdf": PDF_ROUTE["/files/f.pdf"]})
    got = rows(crawler)
    assert net.seen == ["https://c.nknu.edu.tw/aca/t.pdf"] and crawler.end_reason == "finished"
    assert (got["http://c.nknu.edu.tw/aca/t.pdf"]["state"], got["https://c.nknu.edu.tw/aca/t.pdf"]["state"]) == (
        "skipped", "done")
    start, end = (json.loads(events(crawler, code)[-1]) for code in ("run_start", "run_end"))
    assert start["sites"] == ["c.nknu.edu.tw/aca"] and end["sites"] == ["c.nknu.edu.tw/aca", "c.nknu.edu.tw/ccee"]


def test_after_a_run_given_only_a_site_it_never_touched_keeps_its_finished_browser_check(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.event("info", "jscheck_end", json.dumps({"reason": "finished", "sites": None}))
    f.close()
    crawler, _, _ = run_only(tmp_path, {ids[A1]})
    rounds = monitor.Rounds(crawler.frontier)
    assert rounds.jscheck_done("vd.nknu.edu.tw") and rounds.jscheck_done("c.nknu.edu.tw/ccee")
    assert not rounds.jscheck_done("c.nknu.edu.tw/aca")  # A1's site: its check is older than this run's fetch


def test_the_frontiers_choices_of_due_work_keep_to_the_listed_rows(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.x("UPDATE url SET next_at=? WHERE id=?", (T0 + 50, ids[A1]))
    f.x("UPDATE url SET next_at=? WHERE id=?", (T0 + 10, ids[A2]))
    listed = {ids[A1], ids[V3]}
    assert f.queues(T0, ids=listed) == {VQ: ["vd.nknu.edu.tw"]}  # A1 is not due yet; V4 is due but not listed
    assert f.queues(T0) == {VQ: ["vd.nknu.edu.tw"]} and f.queues(T0 + 20) == {QC: ["c.nknu.edu.tw"],
                                                                               VQ: ["vd.nknu.edu.tw"]}
    assert f.queues(T0 + 20, ids=listed) == {VQ: ["vd.nknu.edu.tw"]}  # A2 came due, and is not listed
    assert f.next_due(["c.nknu.edu.tw"], ids=listed) == T0 + 50 and f.next_due(["c.nknu.edu.tw"]) == T0 + 10
    assert f.next_for(["vd.nknu.edu.tw"], T0, ids={ids[A1]}) is None
    assert f.next_for(["vd.nknu.edu.tw"], T0, ids={ids[V4]})["url"] == V4  # V3 goes first without --only
    assert f.waiting(ids=listed) == (2, 0.0) and f.waiting()[0] == 4
    assert f.pending_left(ids=listed)[0] == 2 and f.pending_left()[0] == 4
    assert f.waiting(ids=set()) == (0, None)  # an empty list is no row at all, never every row
    f.close()


# ---------------------------------------------------------------- the main loop: workers for the listed rows' queues


def _workers_started(crawler) -> list[str]:
    """The queues the run's main loop starts a worker for, one entry each time it starts one."""
    started, work = [], crawler._work

    def spy(queue: str) -> None:
        started.append(queue)
        work(queue)

    crawler._work = spy
    return started


def test_no_worker_starts_for_a_queue_whose_due_rows_are_none_of_them_listed(tmp_path):
    """A worker started for a queue with no listed row due finds nothing and idles, IDLE_EXIT_S (30 s) long; the main
    loop starts it again before it looks whether any worker is alive, so the run would never end (review 11-02: this
    file's IDLE_EXIT_S of -1 hides that, the worker mostly gone again before the look)."""
    ids = four(tmp_path)
    crawler, net, _ = only_crawler(tmp_path, {ids[A1]})
    started = _workers_started(crawler)
    run(crawler)
    assert set(started) == {QC}  # V3 and V4 are due on vd.nknu.edu.tw's queue, and not listed
    assert net.seen == [A1] and crawler.end_reason == "finished"


def test_no_worker_starts_for_a_queue_whose_due_presses_are_none_of_a_listed_row(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    press(f, ids[A2])  # due on c.nknu.edu.tw's queue, which has no listed row
    f.close()
    crawler, net, _ = only_crawler(tmp_path, {ids[V3]})
    started = _workers_started(crawler)
    run(crawler)
    assert set(started) == {VQ}
    assert net.seen == [V3] and crawler.end_reason == "finished"


# ---------------------------------------------------------------- presses


def test_a_due_press_on_a_listed_host_does_not_run_when_its_row_is_not_listed(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    aid = press(f, ids[A2])  # on c.nknu.edu.tw, whose A1 is listed
    f.close()
    crawler, net, _ = run_only(tmp_path, {ids[A1]})
    assert net.seen == [A1] and crawler.end_reason == "finished"
    assert press_state(crawler, aid) == "pending"  # never sent, nor started again from its page's GET
    end = json.loads(events(crawler, "run_end")[-1])
    assert end["presses"] == 0  # the presses of rows not listed do not keep the run going


def test_the_chains_choices_of_due_presses_keep_to_the_presses_of_listed_rows(tmp_path):
    ids = four(tmp_path)
    crawler, _, _ = only_crawler(tmp_path, {ids[A1]})
    press(crawler.frontier, ids[A2])
    ch = crawler.chains
    assert ch.due_queues(T0, ids={ids[A1]}) == [] and ch.due_queues(T0, ids={ids[A2]}) == [QC]
    assert ch.waiting(ids={ids[A1]})[0] == 0 and ch.waiting(ids={ids[A2]})[0] == 1
    assert ch.next_action(QC, T0, ids={ids[A1]}) is None
    assert ch.next_action(QC, T0, ids={ids[A2]})["list_id"] == ids[A2]  # its own row listed: it runs


def test_a_chain_left_pressing_on_a_page_not_listed_is_not_started_again(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.x("UPDATE url SET state='done' WHERE id=?", (ids[A2],))
    aid = press(f, ids[A2], state="active")  # left by a run that was killed
    f.close()
    crawler, net, _ = run_only(tmp_path, {ids[A1]})
    assert net.seen == [A1]
    assert rows(crawler)[A2]["state"] == "done" and press_state(crawler, aid) == "active"


def test_a_listed_row_already_done_is_skipped_and_its_presses_wait(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.x("UPDATE url SET state='done' WHERE id=?", (ids[A1],))
    aid = press(f, ids[A1])  # a press of a page fetched already: its chain would start again from a new GET
    f.close()
    crawler, net, _ = run_only(tmp_path, {ids[A1], ids[V3]})
    assert net.seen == [V3] and crawler.end_reason == "finished"
    assert rows(crawler)[A1]["state"] == "done" and press_state(crawler, aid) == "pending"
    start = json.loads(events(crawler, "run_start")[-1])
    assert (start["only"], start["only_pending"]) == (2, 1)


# ---------------------------------------------------------------- the run's own start touches the listed rows only


def test_rows_left_active_by_a_killed_run_go_back_to_pending_only_when_listed(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.x("UPDATE url SET state='active' WHERE id IN (?, ?)", (ids[A1], ids[A2]))
    f.close()
    crawler, net, _ = run_only(tmp_path, {ids[A1]})
    assert net.seen == [A1] and rows(crawler)[A2]["state"] == "active"


DEAD = "dead.nknu.edu.tw"


def _dead_row(f: Frontier, url: str, next_at: float) -> int:
    return add(f, url, kind="page", kind0="page", prio=1, site=DEAD, owner_site=DEAD, reason="dns", next_at=next_at)


def test_names_are_looked_up_again_only_for_the_listed_rows(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.set_host(DEAD, queue="dns-fail", dns_fail=5, dns_at=0.0)
    _dead_row(f, f"https://{DEAD}/x.html", T0 + 3600)
    f.close()
    looked = []

    def resolver(host):
        looked.append(host)
        return QUEUES.get(host, f"ip:{host}"), None, host  # every name resolves now

    crawler, _, _ = run_only(tmp_path, {ids[A1]}, resolver=resolver)
    assert DEAD not in looked
    assert kept(rows(crawler)[f"https://{DEAD}/x.html"]) == ("pending", 0, T0 + 3600, "dns")
    assert crawler.frontier.host(DEAD)["dns_fail"] == 5


def test_a_run_given_only_never_gives_a_name_up_and_leaves_its_other_rows_as_they_were(tmp_path):
    """A name given up fails every row of its host (_reresolve): a run given --only leaves that to a full run."""
    four(tmp_path)
    f = frontier(tmp_path)
    f.set_host(DEAD, queue="dns-fail", dns_fail=mod.DNS_GIVE_UP - 1, dns_at=0.0)
    listed = _dead_row(f, f"https://{DEAD}/listed.html", T0 - 1)
    _dead_row(f, f"https://{DEAD}/other.html", T0 - 1)
    f.close()

    def resolver(host):
        return ("dns-fail", None, None) if host == DEAD else (QUEUES.get(host, f"ip:{host}"), None, host)

    crawler, net, _ = run_only(tmp_path, {listed}, resolver=resolver)
    got = rows(crawler)
    assert net.seen == [] and got[f"https://{DEAD}/listed.html"]["state"] == "pending"
    assert crawler.frontier.host(DEAD)["dns_fail"] == mod.DNS_GIVE_UP - 1
    assert kept(got[f"https://{DEAD}/other.html"]) == ("pending", 0, T0 - 1, "dns")
    assert crawler.end_reason == "waiting"


def test_a_name_that_resolves_again_makes_only_its_listed_rows_due(tmp_path):
    four(tmp_path)
    f = frontier(tmp_path)
    f.set_host(DEAD, queue="dns-fail", dns_fail=2, dns_at=0.0)
    _known(f, DEAD)
    listed = _dead_row(f, f"https://{DEAD}/listed.html", T0 + 3600)
    _dead_row(f, f"https://{DEAD}/other.html", T0 + 3600)
    f.close()

    def resolver(host):
        return QUEUES.get(host, f"ip:{host}"), None, host  # every name resolves now

    crawler, net, _ = run_only(tmp_path, {listed}, {"/listed.html": page("<p>列出的</p>")}, resolver=resolver)
    got = rows(crawler)
    assert net.seen == [f"https://{DEAD}/listed.html"] and got[f"https://{DEAD}/listed.html"]["state"] == "done"
    assert kept(got[f"https://{DEAD}/other.html"]) == ("pending", 0, T0 + 3600, "dns")  # its wait, for a later run
    assert crawler.end_reason == "finished"


def test_hosts_nobody_looked_up_are_looked_up_only_for_the_listed_rows(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    add(f, "https://new.nknu.edu.tw/x.pdf")  # no host row: nobody looked its name up
    f.close()
    looked = []

    def resolver(host):
        looked.append(host)
        return QUEUES.get(host, f"ip:{host}"), None, host

    crawler, _, _ = run_only(tmp_path, {ids[A1]}, resolver=resolver)
    assert "new.nknu.edu.tw" not in looked and crawler.frontier.host("new.nknu.edu.tw") is None


def test_a_host_nobody_looked_up_that_does_not_resolve_holds_back_only_its_listed_rows(tmp_path):
    four(tmp_path)
    f = frontier(tmp_path)
    listed = add(f, "https://new.nknu.edu.tw/listed.pdf")  # no host row: nobody looked its name up
    add(f, "https://new.nknu.edu.tw/other.pdf")
    f.close()

    def resolver(host):
        return ("dns-fail", None, None) if host == "new.nknu.edu.tw" else (QUEUES.get(host, f"ip:{host}"), None, host)

    crawler, _, _ = only_crawler(tmp_path, {listed}, resolver=resolver)
    assert crawler._place_hosts() == 1
    h, got = crawler.frontier.host("new.nknu.edu.tw"), rows(crawler)
    mine = got["https://new.nknu.edu.tw/listed.pdf"]
    assert (mine["reason"], mine["next_at"]) == ("dns", h["dns_at"] + mod._dns_wait(h["dns_fail"]))
    assert kept(got["https://new.nknu.edu.tw/other.pdf"]) == ("pending", 0, 0.0, None)


# ---------------------------------------------------------------- a host's or a directory's judgment: listed rows only


def test_refusals_in_a_row_never_hold_back_an_unlisted_row_of_their_directory(tmp_path):
    four(tmp_path)
    f = frontier(tmp_path)
    listed = {add(f, f"https://c.nknu.edu.tw/aca/files/f{i}.pdf") for i in range(mod.DENY_STREAK)}
    other = "https://c.nknu.edu.tw/aca/files/g.pdf"
    add(f, other)
    f.close()
    crawler, net, _ = run_only(tmp_path, listed, {"/aca/files/": snap("x", 403)})
    assert len(net.seen) == mod.DENY_STREAK and other not in net.seen
    assert events(crawler, "host_denies")  # the streak was reached
    assert kept(rows(crawler)[other]) == ("pending", 0, 0.0, None)


def test_a_server_that_never_connects_fails_no_unlisted_row_as_host_unreachable(tmp_path):
    ids = four(tmp_path)
    crawler, _, _ = only_crawler(tmp_path, {ids[A1]})
    crawler._server_down(QC, mod.CONN_GIVE_UP)  # c.nknu.edu.tw never answered this crawl off campus
    got = rows(crawler)
    assert (got[A1]["state"], got[A1]["reason"]) == ("failed", "host_unreachable")
    assert kept(got[A2]) == ("pending", 0, 0.0, None)


def test_a_host_given_up_for_its_robots_txt_fails_no_unlisted_row(tmp_path):
    ids = four(tmp_path)
    crawler, _, _ = only_crawler(tmp_path, {ids[A1], ids[V3]})
    crawler.frontier.set_host("vd.nknu.edu.tw", answered_off=1)  # down for now: each row loses an attempt
    crawler._give_up_hosts(["c.nknu.edu.tw", "vd.nknu.edu.tw"], "timeout")
    got = rows(crawler)
    assert (got[A1]["state"], got[A1]["reason"]) == ("failed", "host_unreachable")
    assert (got[V3]["attempts"], got[V3]["reason"]) == (1, "timeout")
    assert kept(got[A2]) == kept(got[V4]) == ("pending", 0, 0.0, None)


def test_undoing_an_outage_puts_back_only_the_listed_rows_a_host_wait_failed(tmp_path):
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.x("UPDATE url SET state='failed', reason='host_unreachable' WHERE id IN (?, ?)", (ids[A1], ids[A2]))
    f.close()
    crawler, _, _ = only_crawler(tmp_path, {ids[A1]})
    crawler._undo([("host", "c.nknu.edu.tw", {"robots_fail": 0}, True)])
    got = rows(crawler)
    assert (got[A1]["state"], got[A1]["reason"]) == ("pending", "net_down")
    assert (got[A2]["state"], got[A2]["reason"]) == ("failed", "host_unreachable")


DRIVE = "https://www.googleapis.com/drive/v3/files/"


def _drive_row(f: Frontier, file_id: str, **kw) -> int:
    return add(f, f"{DRIVE}{file_id}?alt=media", site="www.googleapis.com", **kw)


def test_with_no_key_only_the_listed_drive_rows_say_they_wait_for_it(tmp_path, monkeypatch):
    monkeypatch.delenv(google_api.KEY_ENV, raising=False)
    four(tmp_path)
    f = frontier(tmp_path)
    listed = _drive_row(f, "listed")
    _drive_row(f, "other")
    f.close()
    crawler, net, _ = run_only(tmp_path, {listed})
    got = rows(crawler)
    assert net.seen == [] and crawler.end_reason == "waiting"  # the googleapis queue rests until a run with the key
    assert (got[f"{DRIVE}listed?alt=media"]["state"], got[f"{DRIVE}listed?alt=media"]["reason"]) == ("pending",
                                                                                                    "no_api_key")
    assert kept(got[f"{DRIVE}other?alt=media"]) == ("pending", 0, 0.0, None)


def test_with_the_key_a_drive_row_a_key_problem_held_is_put_back_only_when_listed(tmp_path, monkeypatch):
    monkeypatch.setenv(google_api.KEY_ENV, "made-up-for-a-test")
    ids = four(tmp_path)
    f = frontier(tmp_path)
    _drive_row(f, "other", reason="api_key", next_at=T0 + 999)  # left by an earlier run's key problem
    f.close()
    crawler, net, _ = run_only(tmp_path, {ids[A1]})
    assert net.seen == [A1] and crawler.end_reason == "finished"
    assert kept(rows(crawler)[f"{DRIVE}other?alt=media"]) == ("pending", 0, T0 + 999, "api_key")


def _gsites_page_read(tmp_path) -> None:
    """The counselling centre's fake Google Site (tests/archive/test_gsites_images_1027): its home read once by a full
    run, its four runtime pictures (url ids 2–5) pending right after it."""
    clock = Clock()
    net = gs.Site(clock)
    crawler = gs.make(tmp_path, net)
    seed(crawler, gs.HOME)
    sink = WarcSink(tmp_path / "warc", "q", 1, datetime(2026, 10, 27, tzinfo=UTC))
    net.sink = sink
    crawler._handle(gs.GQ, net, sink, crawler._next(gs.GQ))
    crawler.frontier.close()


@pytest.mark.parametrize("site_state, page_state", [
    (None, None),  # the site's first refusal: its page would be read again for new addresses
    ({"ok": True}, {"n": mod.GSIMG_REREADS}),  # pictures came before, the page read again twice: its unsent ones too
    ({"ok": False, "reread": True, "tries": [["https://sites.google.com/sitesv-images-rt/x", 403]]}, None),  # last try
], ids=["page_read_again", "unsent_pictures", "site_given_up"])
def test_a_listed_runtime_picture_refused_waits_for_round_two_alone(tmp_path, site_state, page_state):
    """What a full run does then (§23 B2, B3) touches rows not listed: the page goes back to pending to be read again,
    the page's other pictures or the whole site's wait for round two, the site is given up (review 11-02)."""
    _gsites_page_read(tmp_path)
    site = "sites.google.com/mail.nknu.edu.tw/counseling-center"
    f = frontier(tmp_path)
    if site_state is not None:
        f.meta(mod.GSIMG_SITE_KEY + site, json.dumps(site_state))
    if page_state is not None:
        f.meta(f"{mod.GSIMG_PAGE_KEY}1", json.dumps(page_state))
    before = {r["id"]: tuple(r) for r in f.q("SELECT * FROM url WHERE id <> 2")}
    f.close()
    net = gs.Site(Clock(), deny_all=True)  # every picture answers 403
    queues = {"sites.google.com": gs.GQ}
    crawler = mod.Crawler(tmp_path, plan=OpenPlan(), clock=net.clock, sleep=net.clock.sleep, client_factory=net,
                          resolver=lambda h: (queues.get(h, f"ip:{h}"), None, h), netcheck=lambda: True,
                          disk_free=lambda path: 10**12, campus_url=None, only=frozenset({2}))
    gs.work(crawler, net)
    f = crawler.frontier
    assert net.asked(gs.PIC) == ["https://sites.google.com/sitesv-images-rt/AMxu72h1p1=w1280"]
    assert {r["id"]: tuple(r) for r in f.q("SELECT * FROM url WHERE id <> 2")} == before  # the page, the others
    assert (f.row_by_id(2)["state"], f.row_by_id(2)["reason"]) == ("later", links.GOOGLE_SITES_ROUND2)
    assert f.meta(mod.GSIMG_GIVEUP_KEY + site) is None and not events(crawler, "gsimg_reread")
    assert f.meta(mod.GSIMG_SITE_KEY + site) == (json.dumps(site_state) if site_state is not None else None)


def test_rows_and_presses_left_active_with_no_worker_fail_only_when_listed(tmp_path):
    ids = four(tmp_path)
    crawler, _, _ = only_crawler(tmp_path, {ids[A1]})
    crawler.frontier.x("UPDATE url SET state='active' WHERE id IN (?, ?)", (ids[A1], ids[A2]))
    assert crawler._should_end() is False  # the crash's rows are failed, and the run looks again
    got = rows(crawler)
    assert (got[A1]["state"], got[A1]["reason"]) == ("failed", "crash:left_active")
    assert got[A2]["state"] == "active"
    mine, other = (press(crawler.frontier, ids[u], kind="click", key=f"click:{u}", state="active") for u in (A1, A2))
    crawler._should_end()
    assert (press_state(crawler, mine), press_state(crawler, other)) == ("failed", "active")


def test_listed_rows_due_that_no_queue_takes_end_the_run_as_stuck_whatever_else_is_due(tmp_path):
    """The stuck check asks whether a queue takes the listed rows' due work: rows and presses not listed, due on
    queues that could send, never make it look so (the run would wait for ever with both locks held)."""
    ids = four(tmp_path)
    f = frontier(tmp_path)
    f.set_host(DEAD, queue="dns-fail", dns_fail=1, dns_at=T0)
    listed = _dead_row(f, f"https://{DEAD}/x.html", 0.0)  # due now, on a name no queue sends to
    press(f, ids[A2])  # due on c.nknu.edu.tw's queue
    f.close()
    crawler, _, _ = only_crawler(tmp_path, {listed})  # A1, A2, V3 and V4 are due too, none of them listed
    assert crawler._should_end() is False  # the hosts are looked up once more first
    assert crawler._should_end() is True and crawler.end_reason == "stuck" and events(crawler, "stuck")


def test_a_listed_row_moved_to_its_other_scheme_is_waited_for_as_the_listed_row(tmp_path):
    four(tmp_path)
    f = frontier(tmp_path)
    f.set_host("c.nknu.edu.tw", scheme_only="https")
    listed = add(f, "http://c.nknu.edu.tw/aca/h.pdf")  # written before the host was known to answer on https only
    f.close()
    crawler, net, _ = run_only(tmp_path, {listed}, {"/aca/h.pdf": timeout_err()})
    moved = rows(crawler)["https://c.nknu.edu.tw/aca/h.pdf"]
    assert net.seen == ["https://c.nknu.edu.tw/aca/h.pdf"] and (moved["state"], moved["attempts"]) == ("pending", 1)
    assert crawler.end_reason == "waiting" and json.loads(events(crawler, "run_end")[-1])["pending"] == 1


# ---------------------------------------------------------------- the command


def _archive(tmp_path, n: int = 6, states: dict[int, tuple[str, str | None]] | None = None):
    """An archive directory whose frontier.db holds n pending rows, ids 1..n; states: id -> (state, reason)."""
    root = tmp_path / "archive"
    f = Frontier(root / "frontier.db")
    for i in range(1, n + 1):
        add(f, f"https://c.nknu.edu.tw/aca/p{i}.pdf")
    for i, (state, reason) in (states or {}).items():
        f.x("UPDATE url SET state=?, reason=? WHERE id=?", (state, reason, i))
    f.close()
    return root


def _fake_crawler(made: list, ends: str = "finished", effect=None):
    class FakeCrawler:
        """Records what the command gave it; its run does effect(frontier) (what a run changed), and ends at once, for
        the reason given."""

        def __init__(self, root, **kw):
            made.append(kw)
            self.stop, self.end_reason = threading.Event(), None
            self.sites, self.only = kw.get("sites"), kw.get("only")
            self.frontier = Frontier(root / "frontier.db")

        def halt(self, reason):
            self.end_reason = self.end_reason or reason
            self.stop.set()

        def run(self, poll_s: float = 5.0):
            if effect is not None:
                effect(self.frontier)
            self.end_reason = ends
            self.stop.set()

    return FakeCrawler


def _invoke(root, ids_file, *more):
    return CliRunner().invoke(app, ["archive", "run", "--dir", str(root), "--only", str(ids_file), *more])


def test_the_command_reads_u_ids_bare_ids_blank_lines_and_comments(tmp_path, monkeypatch):
    root = _archive(tmp_path)
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("u5\n6\n\n# note\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(mod, "Crawler", _fake_crawler(made))
    out = _invoke(root, ids_file, "--gap", "1-2")
    assert out.exit_code == 0, out.output
    assert made[0]["only"] == frozenset({5, 6})
    assert made[0]["gap"] == (1.0, 2.0) and made[0]["sites"] is None
    assert "--only 列出的 2 个网址现在：pending 2 个" in out.output  # what the listed rows came to


def test_after_the_run_the_rows_the_listed_rows_brought_that_still_wait_are_named(tmp_path, monkeypatch):
    """A Drive item's content, a redirect's target, a page's links: enqueued, never fetched by the run (review 11-02:
    a listed Drive row can be the item's metadata only). The command names them, so that a listed row's done is not
    read as its file in hand."""
    root = _archive(tmp_path)

    def fetched(f: Frontier) -> None:
        f.x("UPDATE url SET state='done' WHERE id IN (5, 6)")
        f.x("UPDATE url SET found_from=5 WHERE id=6")  # listed itself: fetched, not named
        add(f, "https://www.googleapis.com/drive/v3/files/abc?alt=media", found_from=5, found_via="drive")  # id 7
        add(f, "https://c.nknu.edu.tw/aca/fetched.pdf", found_from=6, state="done")  # id 8: in hand already

    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("u5\nu6\n", encoding="utf-8")
    monkeypatch.setattr(mod, "Crawler", _fake_crawler([], effect=fetched))
    out = _invoke(root, ids_file)
    assert out.exit_code == 0, out.output
    assert "列出的网址带出的 1 个网址还在待抓" in out.output and "u7" in out.output and "u8" not in out.output


def test_a_mistyped_dir_is_refused_and_nothing_is_made_there(tmp_path, monkeypatch):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("u5\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(mod, "Crawler", _fake_crawler(made))
    out = _invoke(tmp_path / "no-such-archive", ids_file)
    assert out.exit_code == 1 and made == [] and "找不到待抓清单" in out.output
    assert not (tmp_path / "no-such-archive").exists()


def test_a_run_given_only_that_ends_waiting_says_to_run_again_with_the_same_list(tmp_path, monkeypatch):
    """A plain run would fetch the other rows of those sites too: the hint names --only and its file."""
    root = _archive(tmp_path)
    f = Frontier(root / "frontier.db")
    f.x("UPDATE url SET attempts=1, reason='timeout', next_at=? WHERE id=5", (4_000_000_000.0,))
    f.close()
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("u5\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(mod, "Crawler", _fake_crawler(made, ends="waiting"))
    out = _invoke(root, ids_file)
    assert out.exit_code == 0, out.output
    assert f"--only {ids_file}" in out.output and "列出的网址还有 1 个网址在等" in out.output


def test_an_id_the_archive_does_not_have_refuses_the_run_with_exit_code_2(tmp_path, monkeypatch):
    root = _archive(tmp_path)
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("u5\nu999\n12345\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(mod, "Crawler", _fake_crawler(made))
    out = _invoke(root, ids_file)
    assert out.exit_code == 2 and made == []
    assert "u999" in out.output and "u12345" in out.output and "没有开始抓" in out.output


def test_a_line_that_is_no_id_refuses_the_run_with_exit_code_2(tmp_path, monkeypatch):
    root = _archive(tmp_path)
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("u5\nhttps://c.nknu.edu.tw/aca/p6.pdf\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(mod, "Crawler", _fake_crawler(made))
    out = _invoke(root, ids_file)
    assert out.exit_code == 2 and made == [] and "第 2 行" in out.output


def test_listed_rows_already_done_or_failed_are_reported_and_skipped(tmp_path, monkeypatch):
    root = _archive(tmp_path, states={5: ("done", None), 6: ("failed", "http_500")})
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("u4\nu5\nu6\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(mod, "Crawler", _fake_crawler(made))
    out = _invoke(root, ids_file, "--sites", "c.nknu.edu.tw/aca")
    assert out.exit_code == 0, out.output
    assert "跳过" in out.output and "u5" in out.output and "u6" in out.output
    assert made[0]["only"] == frozenset({4, 5, 6}) and made[0]["sites"] == frozenset({"c.nknu.edu.tw/aca"})


def test_nothing_left_to_fetch_starts_no_run(tmp_path, monkeypatch):
    root = _archive(tmp_path, states={5: ("done", None)})
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("u5\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(mod, "Crawler", _fake_crawler(made))
    out = _invoke(root, ids_file)
    assert out.exit_code == 0 and made == [] and "没有开始抓" in out.output
