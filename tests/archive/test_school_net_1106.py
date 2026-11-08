"""The school's network taken for down by servers that were down on their own (owner 11-06: 「现在修」).

On the lab machine the first batch's last school rows sat on a dozen servers that had not connected for days. Whenever
three of them failed with no school server answering in the last two minutes, the crawler took the school's network
for down: 105 school-wide pauses of 15 minutes in 33 hours, the live school servers paused with them, and the dead
ones' failures undone each time. Only a server that was working is a sign of its network going down: it answered at
some time (a new server down from the start says nothing of its network), at most one of its connection failures in a
row is held against it (an outage's first failures are held against the servers when another school server answered
moments before), and no school answer refuted a pause it told. Two of them tell it (the first batch's school work is on
two servers, SSO's and c.nknu's), also when their first failures are minutes apart with no school answer between (one
was resting). A school answer that came after a teller's failure says that one failed on its own; with fewer than two
left the pause is lifted, and its tellers keep the rest of their own failures. The second review (11-06) found the rest:
what an answer that came before the drop but was noted after its gap refuted, the campus check asked when a school
pause ends, and server_down warned once.
"""

from __future__ import annotations

import contextlib

from nknu.archive import crawler as mod
from nknu.archive.frontier import net_key
from tests.archive.test_archive import Clock, Sink, make, seed, snap
from tests.archive.test_crawler_round1 import conn_err, drive, events, rows

SSO = {"sso.nknu.edu.tw": "ip:140.127.40.173"}
SSO_Q = SSO["sso.nknu.edu.tw"]


def dead_servers(n: int = 4) -> dict[str, str]:
    return {f"d{i}.nknu.edu.tw": f"ip:140.127.9.{i}" for i in range(1, n + 1)}


def live_servers(n: int = 3) -> dict[str, str]:
    return {f"s{i}.nknu.edu.tw": f"ip:140.127.1.{i}" for i in range(1, n + 1)}


def switch(up: dict):
    """A school server's route that answers while up["on"], and does not connect while the school's network is down."""
    def route(u):
        if up["on"]:
            return snap(u, html="<p>x</p>")
        raise conn_err()
    return route


@contextlib.contextmanager
def on_the_wire(crawler):
    """A request already sent when a pause began: _send's pause check never stops it."""
    real = crawler.frontier.paused_until
    crawler.frontier.paused_until = lambda queue: 0.0
    try:
        yield
    finally:
        crawler.frontier.paused_until = real


def offline(crawler) -> list[str]:
    return [m for m in events(crawler, "offline") if "school" in m]


def test_servers_down_on_their_own_are_no_sign_of_the_school_network_going_down():
    clock = Clock()
    streaks = {"ip:140.127.9.1": 9, "ip:140.127.9.2": 2, "ip:140.127.9.3": 9, "ip:140.127.1.2": 1}
    watch = mod._NetWatch(clock, lambda: True, streak=lambda q: streaks.get(q, 0))
    for q in ("ip:140.127.9.1", "ip:140.127.9.2", "ip:140.127.9.3"):
        assert watch.down_after(q) == (frozenset(), False)  # failing on their own, none answered: theirs, not the net's
    assert watch.down_after("ip:140.127.1.1") == (frozenset(), False)
    # one failure in a row held against it is no proof yet: an outage's first failures are held when another school
    # server answered moments before
    assert watch.down_after("ip:140.127.1.2") == (frozenset({"school"}), False)  # two that were working: the net's


def test_a_school_answer_while_the_pause_holds_refutes_it():
    clock = Clock()
    watch = mod._NetWatch(clock, lambda: True)
    tellers = ("ip:140.127.9.1", "ip:140.127.9.2")
    watch.down_after(tellers[0])
    assert watch.down_after(tellers[1]) == (frozenset({"school"}), False)
    assert not watch.answered(SSO_Q, http=False)  # a timeout: the connection was made, nothing answered
    assert watch.answered(SSO_Q)  # SSO's page, on the wire when the pause began: the school's network works
    assert not watch.answered(SSO_Q)  # refuted once
    clock.t += mod.NET_WINDOW_S + 1
    for q in tellers:
        assert watch.down_after(q) == (frozenset(), False)  # no sign until they answer
    watch.answered(tellers[0])
    clock.t += mod.NET_WINDOW_S + 1
    watch.down_after(tellers[0])
    assert watch.down_after("ip:140.127.1.1") == (frozenset({"school"}), False)  # its own answer made it a sign again


def test_a_pause_that_ran_its_course_is_not_refuted_by_the_answers_after_it():
    clock = Clock()
    watch = mod._NetWatch(clock, lambda: True)
    for q in ("ip:140.127.1.1", "ip:140.127.1.2"):
        watch.down_after(q)
    watch.grace("school")  # the pause is over with no school answer: the network was down
    assert not watch.answered(SSO_Q)  # the network is back, nothing refuted
    clock.t += mod.NET_WINDOW_S + 1
    watch.down_after("ip:140.127.1.1")
    assert watch.down_after("ip:140.127.1.2") == (frozenset({"school"}), False)  # still signs


def test_a_school_answer_while_the_check_is_asked_is_no_outage():
    clock = Clock()
    asked = []

    def check():
        asked.append(1)
        if len(asked) == 2:
            watch.answered(SSO_Q)  # SSO's page came back while the check looked up www.google.com
        return True

    watch = mod._NetWatch(clock, check)
    watch.down_after("ip:140.127.9.1")
    assert watch.down_after("ip:140.127.9.2") == (frozenset(), False)
    assert len(asked) == 2


def test_dead_school_servers_alone_never_pause_the_school_network(tmp_path):
    """The lab's tail: four school servers down for hours (their streaks counted while other servers answered) are
    tried every 15 minutes, and SSO answers a page now and then, never within two minutes of them."""
    dead = dead_servers()
    routes = {"sso.nknu.edu.tw": lambda u: snap(u, html="<p>x</p>"), **{h: conn_err() for h in dead}}
    crawler, net, clock = make(tmp_path, routes, queues={**dead, **SSO})
    crawler.campus = "on"  # the lab machine: a school host down is down, never handed to the lab (11-04)
    for h, q in dead.items():
        crawler.frontier.server_set(q, mod.CONN_GIVE_UP)
        for j in range(3):
            seed(crawler, f"https://{h}/p{j}")
    queues = [*dead.values(), SSO_Q]
    for step in range(240):  # four hours, a minute a step
        if step % 20 == 7:
            seed(crawler, f"https://sso.nknu.edu.tw/p{step}")
        for q in queues:
            drive(crawler, net, q)
        clock.t += 60
    assert not offline(crawler) and crawler.frontier.pause_of(net_key("school")) == 0
    assert all(crawler.frontier.server_fails(q) > mod.CONN_GIVE_UP for q in dead.values())  # they go on counting
    sso = [r for u, r in rows(crawler).items() if "sso.nknu.edu.tw/p" in u]
    assert len(sso) == 12 and {r["state"] for r in sso} == {"done"}
    dead_rows = [r for u, r in rows(crawler).items() if u.startswith("https://d")]
    # B2: no URL loses an attempt; down, they fail as server_down instead of waiting (the owner 11-08)
    assert {(r["state"], r["attempts"], r["reason"]) for r in dead_rows} == {("failed", 0, "server_down")}


def test_dead_servers_that_never_answered_never_pause_the_school_network(tmp_path):
    """Dead school servers that never answered (a new batch's, or down since before this archive knew them) say nothing
    of their network: their failures are their own from the first (red at CONN_GIVE_UP, one warning each), and the
    school is never paused for them, though no school server answered in the two minutes before."""
    dead = dead_servers(3)
    routes = {"sso.nknu.edu.tw": lambda u: snap(u, html="<p>x</p>"), **{h: conn_err() for h in dead}}
    crawler, net, clock = make(tmp_path, routes, queues={**dead, **SSO})
    crawler.campus = "on"
    for h in dead:
        seed(crawler, f"https://{h}/p")
    for step in range(180):  # three hours, a minute a step: the dead servers first each time
        if step % 20 == 10:
            seed(crawler, f"https://sso.nknu.edu.tw/p{step}")  # SSO answers a page now and then
        for q in [*dead.values(), SSO_Q]:
            drive(crawler, net, q)
        clock.t += 60
    assert not offline(crawler) and crawler.frontier.pause_of(net_key("school")) == 0
    assert all(crawler.frontier.server_fails(q) >= mod.CONN_GIVE_UP for q in dead.values())
    assert len(events(crawler, "server_down")) == len(dead)
    sso = [r for u, r in rows(crawler).items() if "sso.nknu.edu.tw/p" in u]
    assert len(sso) == 9 and {r["state"] for r in sso} == {"done"}
    dead_rows = [r for u, r in rows(crawler).items() if u.startswith("https://d")]
    assert {(r["state"], r["attempts"], r["reason"]) for r in dead_rows} == {("failed", 0, "server_down")}


def test_servers_that_died_together_tell_one_pause_and_keep_their_rest_when_it_is_lifted(tmp_path):
    """Two school servers that answered stop connecting together while SSO's page is on the wire: they tell the
    school's pause, and SSO's answer lifts it. Each keeps the 15-minute rest of its own failure, the one whose failure
    told it too (R3: none is asked again before it is over), and from then on they count their failures."""
    pair, up = dead_servers(2), {"on": True}
    routes = {"sso.nknu.edu.tw": lambda u: snap(u, html="<p>x</p>"), **{h: switch(up) for h in pair}}
    crawler, net, clock = make(tmp_path, routes, queues={**pair, **SSO})
    crawler.campus = "on"
    sink = Sink(crawler.root)
    for h in pair:
        seed(crawler, f"https://{h}/a")
    for q in pair.values():
        drive(crawler, net, q)  # they work: a page each
    clock.t += 300
    up["on"] = False  # both go down at once, on their own
    for h in pair:
        seed(crawler, f"https://{h}/b")
    for j in range(40):
        seed(crawler, f"https://sso.nknu.edu.tw/p{j}")
    row = crawler._next(SSO_Q)  # SSO's request goes out ...
    for q in pair.values():  # ... and the refusals come back before its page
        drive(crawler, net, q)
    assert len(offline(crawler)) == 1 and crawler.frontier.pause_of(net_key("school")) > clock()
    crawler._prefer[SSO_Q] = row["host"]
    with on_the_wire(crawler):
        crawler._handle(SSO_Q, net, sink, row)
    assert crawler.frontier.pause_of(net_key("school")) == 0  # lifted: the school's network works
    assert any("answered while the school network's pause held" in m for m in events(crawler, "online"))
    for q in pair.values():
        assert crawler.frontier.pause_of(q) > clock() + mod.CONN_PAUSE_S - 60
    for _ in range(180):
        for q in [*pair.values(), SSO_Q]:
            drive(crawler, net, q)
        clock.t += 60
    assert len(offline(crawler)) == 1
    assert all(crawler.frontier.server_fails(q) >= mod.CONN_GIVE_UP for q in pair.values())
    assert len(events(crawler, "server_down")) == len(pair)
    sso = [r for u, r in rows(crawler).items() if "sso.nknu.edu.tw/p" in u]
    assert {r["state"] for r in sso} == {"done"}
    left = [r for u, r in rows(crawler).items() if u.endswith("/b")]
    # they answered before: down for now, their rows keep waiting with every attempt (B2; 11-08 gives up only a
    # server that never answered)
    assert {(r["state"], r["attempts"]) for r in left} == {("pending", 0)}


def test_an_outage_that_begins_while_the_school_servers_answer_is_told(tmp_path):
    """The usual shape: the crawl is busy on three school servers when the school's network drops. Their first
    failures come seconds after the last answers and are held against them (one each); 15 minutes later they fail
    again and tell it. For three hours each is tried every 15 minutes, none goes red, no URL loses an attempt; when
    the network is back, everything is fetched."""
    live, up = live_servers(), {"on": True}
    crawler, net, clock = make(tmp_path, {h: switch(up) for h in live}, queues=live)
    crawler.campus = "on"
    for h in live:
        for j in range(30):
            seed(crawler, f"https://{h}/p{j}")
    for q in live.values():  # one page each, answered
        row = crawler._next(q)
        crawler._prefer[q] = row["host"]
        crawler._handle(q, net, Sink(crawler.root), row)
    clock.t += 3
    up["on"] = False
    for _ in range(180):
        for q in live.values():
            drive(crawler, net, q)
        clock.t += 60
    assert offline(crawler)
    assert all(crawler.frontier.server_fails(q) <= 1 for q in live.values())
    assert not events(crawler, "server_down")
    left = [r for u, r in rows(crawler).items() if r["state"] != "done"]
    assert left and {(r["state"], r["attempts"]) for r in left} == {("pending", 0)}
    up["on"] = True
    for _ in range(60):
        for q in live.values():
            drive(crawler, net, q)
        clock.t += 60
    assert {r["state"] for r in rows(crawler).values()} == {"done"}
    assert all(crawler.frontier.server_fails(q) == 0 for q in live.values())


def test_two_working_school_servers_tell_the_outage(tmp_path):
    """The first batch: its school work is on SSO's server and c.nknu's, beside servers down for days. The school's
    network drops: those two failing together tell it (the dead ones never do), and neither goes red."""
    two = {**SSO, "c.nknu.edu.tw": "ip:140.127.40.30"}
    dead, up = dead_servers(3), {"on": True}
    routes = {**{h: switch(up) for h in two}, **{h: conn_err() for h in dead}}
    crawler, net, clock = make(tmp_path, routes, queues={**two, **dead})
    crawler.campus = "on"
    for h, q in dead.items():
        crawler.frontier.server_set(q, 70)
        seed(crawler, f"https://{h}/p")
    queues = [*dead.values(), *two.values()]
    for step in range(200):
        if step == 20:
            up["on"] = False
        for h in two:  # a page each a minute, busy when the network drops
            seed(crawler, f"https://{h}/p{step}")
        for q in queues:
            drive(crawler, net, q)
        clock.t += 60
    assert offline(crawler)
    assert all(crawler.frontier.server_fails(q) <= 1 for q in two.values())
    assert not [m for m in events(crawler, "server_down") if "140.127.40." in m]


def test_working_school_servers_failing_together_still_pause_the_school_network(tmp_path):
    """School servers that answered a few minutes ago stop connecting: their failures are the school network's, even
    the one already held against the first before the second failed (undone with the pause)."""
    live, up = live_servers(), {"on": True}
    crawler, net, clock = make(tmp_path, {h: switch(up) for h in live}, queues=live)
    for h in live:
        seed(crawler, f"https://{h}/a")
    for q in live.values():
        drive(crawler, net, q)
    clock.t += 300  # past the two minutes: no school answer is fresh any more
    up["on"] = False
    for h in live:
        seed(crawler, f"https://{h}/b")
    for q in live.values():
        drive(crawler, net, q)
    assert len(offline(crawler)) == 1 and crawler.frontier.pause_of(net_key("school")) > clock()
    assert all(crawler.frontier.server_fails(q) == 0 for q in live.values())


def test_a_long_school_outage_holds_nothing_new_against_a_server_failing_on_its_own(tmp_path):
    """A server one failure short of CONN_GIVE_UP when the school's network drops for six hours, tried first after
    every school pause: while the school's network is unsure its failures count against no server, so no warning
    (server_down) for it, once or every 15 minutes, and none of its rows loses an attempt."""
    live, up = live_servers(), {"on": True}
    k = {"k.nknu.edu.tw": "ip:140.127.9.9"}
    crawler, net, clock = make(tmp_path, {**{h: switch(up) for h in live}, "k.nknu.edu.tw": conn_err()},
                               queues={**live, **k})
    crawler.campus = "on"
    crawler.frontier.server_set("ip:140.127.9.9", mod.CONN_GIVE_UP - 1)
    for h in live:
        seed(crawler, f"https://{h}/a")
    for q in live.values():
        drive(crawler, net, q)
    clock.t += 300
    up["on"] = False
    for h in (*live, *k):
        for j in range(3):
            seed(crawler, f"https://{h}/p{j}")
    for q in live.values():  # the working servers tell the outage ...
        drive(crawler, net, q)
    assert offline(crawler)
    for _ in range(360):  # ... and the one failing on its own is tried first after every school pause
        for q in ["ip:140.127.9.9", *live.values()]:
            drive(crawler, net, q)
        clock.t += 60
    assert len(offline(crawler)) > 1 and not events(crawler, "server_down")
    assert crawler.frontier.server_fails("ip:140.127.9.9") == mod.CONN_GIVE_UP - 1
    k_rows = [r for u, r in rows(crawler).items() if "k.nknu.edu.tw" in u]
    assert {(r["state"], r["attempts"]) for r in k_rows} == {("pending", 0)}


def test_a_school_server_that_never_answered_is_no_sign():
    clock = Clock()
    logged = {"ip:140.127.1.1"}  # a request row with a status, this run's or an earlier one's
    watch = mod._NetWatch(clock, lambda: True, answered_ever=lambda q: q in logged)
    watch.answered("ip:140.127.1.2")  # this run
    clock.t += mod.NET_WINDOW_S + 1
    for q in ("ip:140.127.9.1", "ip:140.127.9.2"):  # new, or down since before this archive knew them
        assert watch.down_after(q) == (frozenset(), False)
    assert watch.down_after("ip:140.127.1.1") == (frozenset(), False)
    assert watch.down_after("ip:140.127.1.2") == (frozenset({"school"}), False)


def test_working_servers_failing_minutes_apart_tell_the_outage():
    clock = Clock()
    watch = mod._NetWatch(clock, lambda: True)
    clock.t += mod.NET_WINDOW_S + 1
    assert watch.down_after("ip:140.127.1.1") == (frozenset(), False)
    clock.t += 6 * 60  # the other's first try comes minutes later (it was resting), nothing answered between
    assert watch.down_after("ip:140.127.1.2") == (frozenset({"school"}), False)


def test_a_school_answer_between_two_failures_or_a_long_time_keeps_them_apart():
    clock = Clock()
    watch = mod._NetWatch(clock, lambda: True)
    clock.t += mod.NET_WINDOW_S + 1
    watch.down_after("ip:140.127.1.1")
    clock.t += 60
    watch.answered(SSO_Q)  # the school's network worked after that failure: it was the server's own
    clock.t += mod.NET_WINDOW_S + 1
    assert watch.down_after("ip:140.127.1.2") == (frozenset(), False)
    clock.t += mod.SIGN_KEEP_S + 1  # longer than a server's rest and the window: forgotten
    assert watch.down_after("ip:140.127.1.3") == (frozenset(), False)


def test_an_answer_that_came_before_the_tellers_failed_refutes_nothing():
    """SSO's long download ends just before the school's network drops, and its answer is noted only after its gap,
    when two working servers have told the outage: it says nothing of the time after their failures."""
    clock = Clock()
    watch = mod._NetWatch(clock, lambda: True)
    watch.answered(SSO_Q)
    clock.t += 1000.0
    came = clock()  # the download's last byte
    clock.t += 5  # the network drops
    watch.down_after("ip:140.127.1.1")
    assert watch.down_after("ip:140.127.1.2") == (frozenset({"school"}), False)
    clock.t += 20  # SSO's gap is over: its answer is noted now
    assert not watch.answered(SSO_Q, at=came) and watch.still_told()  # the pause stands
    assert watch.answered(SSO_Q)  # a page that came during the pause lifts it


def test_a_school_outage_is_told_when_its_two_servers_first_fail_minutes_apart(tmp_path):
    """SSO's server is busy when the school's network drops; c.nknu's next page comes six minutes later (it was
    resting). Their first failures are minutes apart with no school answer between them: the outage is told, and for
    three hours neither goes red, though their tries stay minutes apart."""
    two = {**SSO, "c.nknu.edu.tw": "ip:140.127.40.30"}
    up = {"on": True}
    crawler, net, clock = make(tmp_path, {h: switch(up) for h in two}, queues=two)
    crawler.campus = "on"
    seed(crawler, "https://c.nknu.edu.tw/a")
    for step in range(200):
        if step == 20:
            up["on"] = False
        seed(crawler, f"https://sso.nknu.edu.tw/p{step}")
        if step >= 26:
            seed(crawler, f"https://c.nknu.edu.tw/p{step}")
        for q in two.values():
            drive(crawler, net, q)
        clock.t += 60
    assert offline(crawler)
    assert all(crawler.frontier.server_fails(q) <= 1 for q in two.values())
    assert not events(crawler, "server_down")


def test_a_server_down_is_warned_once_until_it_answers(tmp_path):
    crawler, _net, _clock = make(tmp_path, {})
    q = "ip:140.127.9.9"
    crawler.frontier.server_set(q, mod.CONN_GIVE_UP)
    crawler._server_down(q, mod.CONN_GIVE_UP)
    crawler._server_down(q, mod.CONN_GIVE_UP)  # the same streak again: a school pause gave a failure back
    assert len(events(crawler, "server_down")) == 1
    crawler._server_ok(q)  # it answered
    crawler.frontier.server_set(q, mod.CONN_GIVE_UP)
    crawler._server_down(q, mod.CONN_GIVE_UP)
    assert len(events(crawler, "server_down")) == 2


def test_the_campus_check_is_asked_when_the_school_pause_ends(tmp_path):
    """SSO's check page is a school server's answer: asked first when a pause ends, it says whether the school's
    network works before servers down on their own can tell the pause again."""
    crawler, _net, clock = make(tmp_path, {}, queues=SSO, campus_url=mod.CAMPUS_URL,
                                local_address=lambda: "140.127.200.9")
    crawler._campus_addr, crawler._campus_at = "140.127.200.9", clock()  # just checked
    assert not crawler._campus_due()
    crawler.frontier.pause(net_key("school"), clock() + 60, "the school's servers do not connect")
    clock.t += 61
    assert not crawler._paused(SSO_Q)  # over: the school's queues try again
    assert crawler._campus_due()


def test_a_campus_check_that_never_went_out_is_still_due(tmp_path):
    crawler, net, clock = make(tmp_path, {}, queues=SSO, campus_url=mod.CAMPUS_URL,
                               local_address=lambda: "140.127.200.9")
    crawler.frontier.pause(SSO_Q, clock() + 600, "a rest began while it was being asked")
    assert not crawler._step(crawler._campus_step, SSO_Q, net, Sink(crawler.root))
    assert crawler._campus_due()
