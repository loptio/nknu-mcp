"""Round two, batch two (design docs/superpowers/specs/2026-11-17-round2-batch2-design.md §三.6; the 11-14 handover's
old debt c-1; lis.nknu's move 11-07): at a run's start each host with something to fetch or press is looked up again,
and one whose server moved goes on the new address's queue, its waiting presses with it, so that two queues never
send to one server. A host that did not move, or does not resolve now, keeps its queue. Made-up hosts."""

from __future__ import annotations

from tests.archive.test_pilot_fixes import make


def _setup(tmp_path):
    queues = {"lib.example.org": "ip:10.0.0.1", "same.example.org": "ip:10.0.0.2"}
    crawler, _net, _clock = make(tmp_path, {}, queues=queues)
    f = crawler.frontier
    for host in queues:
        f.add(f"https://{host}/a", host=host, site=host, unit=None, tier=1, kind="page", depth=1, found_via="link",
              found_from=None, owner_site=host, kind0="page")
        crawler.queue_of(host)
    with f.lock:
        aid = f.conn.execute(
            "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, "
            "form_src_offset, target, argument, state, row_key) VALUES (1, 1, 1, 'detail', 'ip:10.0.0.1', 'lib.example.org', "
            "'lib.example.org', 'x/y', 'w', 0, 't', '', 'pending', 'r1')").lastrowid
    return crawler, f, queues, aid


def test_a_host_whose_server_moved_goes_on_the_new_queue_with_its_presses(tmp_path):
    crawler, f, queues, aid = _setup(tmp_path)
    queues["lib.example.org"] = "ip:10.0.0.9"
    assert crawler._moved_hosts() == 1
    assert f.host("lib.example.org")["queue"] == "ip:10.0.0.9"
    assert f.q("SELECT queue FROM action WHERE id=?", (aid,))[0][0] == "ip:10.0.0.9"
    assert f.host("same.example.org")["queue"] == "ip:10.0.0.2"
    assert f.q("SELECT 1 FROM event WHERE code='host_moved'")


def test_a_host_that_does_not_resolve_now_keeps_its_queue(tmp_path):
    crawler, f, queues, _aid = _setup(tmp_path)
    queues["lib.example.org"] = "dns-fail"
    assert crawler._moved_hosts() == 0
    assert f.host("lib.example.org")["queue"] == "ip:10.0.0.1"
