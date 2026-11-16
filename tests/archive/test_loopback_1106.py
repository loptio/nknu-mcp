"""Links to this machine (11-06, 【用户同意 Claude 的建议】: Claude's 11-04 fix 「127.0.0.1、localhost 这类网址直接
列为不抓」; the owner 11-04 「待会再修」, 11-06 the button Claude wrote, 「先修三件再一起重开」).

The first batch's SSO page (webidx 593, menuid 902) linked its attachment ＊共創性別平等友善校園宣導簡報＊ at
http://localhost/WebServices/ajaxDownloadFileCourse.aspx?…: judged an off-campus attachment of a round-1 page, it was
queued, resolved to 127.0.0.1 in the lab's container, and waited on queue ip:127.0.0.1 for ever (conn_pause), the
dashboard showing a dead server 127.0.0.1 and its site never finishing. Now scope.judge says loopback for a URL that
names this machine in any writing a resolver reads (no row, never a request: HARD_OUT), the row an older run left is
skipped before anything is sent, and what the link stood for is the school's broken link (bad_link, 要人看), never
规则不抓. A name that resolves to this machine resolves to no server (dns-fail: it waits, then fails, as such a name
does). Private addresses are not this machine. The school's broken link and the name guard: 【Claude 的执行办法】.
"""

from __future__ import annotations

import pytest

from nknu.archive import outcome, progress, rehearsal, scope
from nknu.archive.crawler import DNS_GIVE_UP
from nknu.archive.monitor import _landed_nowhere, scope_audit
from tests.archive.test_archive import make, seed, snap
from tests.archive.test_crawler_round1 import HOME, QC, crawl, drive, links_of, page, result, rows, settle

LOCALHOST_FILE = "http://localhost/WebServices/ajaxDownloadFileCourse.aspx?encode=1&path=Zm9vLnBwdHg="
LOOP_Q = "ip:127.0.0.1"


def _level(crawler, pid: int) -> tuple[str, str]:
    """The page's level and reasons once its type is judged (a person's ok: the page itself is complete)."""
    crawler.frontier.x("UPDATE page_type SET verdict='ok', source='manual'")
    return tuple(crawler.frontier.q("SELECT level, reasons FROM page_completeness WHERE id=?", (pid,))[0])


@pytest.mark.parametrize("url", [
    LOCALHOST_FILE, "http://LOCALHOST:8080/x", "https://foo.localhost/",
    "http://localhost.localdomain/", "http://127.0.0.1/a.pdf", "http://127.8.9.10/", "http://127.0.0.1:8877/progress",
    "http://0.0.0.0/", "http://[::1]/x", "http://[::]/", "http://[::ffff:127.0.0.1]/x", "http://[0:0:0:0:0:0:0:1]/",
    # the short ways a resolver still reads an IPv4 address (inet_aton): ipaddress refuses every one of them
    "http://127.1/a.pdf", "http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "http://0/",
])
def test_a_url_that_names_this_machine_is_never_fetched(url):
    assert scope.judge(url).reason == "loopback" and "loopback" in scope.HARD_OUT
    canon = scope.canonical(url)
    assert canon is None or not scope.judge(canon).ok  # IPv6: canonical() drops the brackets, bad_url


def test_a_trailing_dot_is_this_machine_once_canonical_and_never_fetched_raw():
    assert scope.judge(scope.canonical("http://localhost./x")).reason == "loopback"
    assert scope.judge("http://localhost./x").reason == "bad_host"  # its empty last label: HARD_OUT all the same


@pytest.mark.parametrize(("url", "reason"), [
    ("http://10.0.0.1/a.pdf", "offsite"), ("http://192.168.1.1/", "offsite"), ("http://172.16.0.1/", "offsite"),
    (f"http://{rehearsal.UNKNOWN_IP}/", "offsite"),  # the rehearsal's unknown hosts stay fetchable there
    ("https://140.127.40.173/x", "in_scope"), ("https://localhost.nknu.edu.tw/", "in_scope"),
    ("https://mylocalhost.com/", "offsite"), ("https://face.cafe/", "offsite"), ("http://1.2.3/", "offsite"),
])
def test_private_school_and_look_alike_addresses_are_not_this_machine(url, reason):
    assert scope.judge(url).reason == reason


def test_own_address_reads_a_resolver_s_answer_and_never_raises():
    for host in ("127.0.0.1", "::1", "0.0.0.0", "::", "::ffff:127.0.0.1", "::1%lo", "LOCALHOST.", "127.1"):
        assert scope.own_address(host), host
    for host in ("140.127.40.173", "10.254.254.254", "1", "x", "c", "", None, "[::1", "999.1.1.1", "::127.0.0.1"):
        assert not scope.own_address(host), host


def test_the_first_batch_s_localhost_attachment_is_the_school_s_broken_link_and_nothing_is_sent(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(
        f'<p>教務處公告</p><a href="{LOCALHOST_FILE}">＊共創性別平等友善校園宣導簡報＊</a>')})
    crawler.seed_url(HOME)
    settle(crawler, net, [QC, LOOP_Q])
    assert LOCALHOST_FILE not in rows(crawler) and not [u for u in net.seen if "localhost" in u]
    assert links_of(crawler, HOME)[LOCALHOST_FILE] == outcome.LOOPBACK_OUT
    pid = rows(crawler)[HOME]["id"]
    got = crawler.frontier.q("SELECT need, why FROM page_res WHERE page_id=? AND res_key=?", (pid, LOCALHOST_FILE))
    assert [tuple(r) for r in got] == [("must", outcome.LOOPBACK_OUT)]
    assert [r[0] for r in crawler.frontier.q("SELECT result FROM page_resource WHERE page_id=?", (pid,))] == ["bad_link"]
    level, reasons = _level(crawler, pid)
    # the school's broken link: complete as far as the school allows (the owner 11-16 「销账照表做」, export batch T5)
    assert level == "complete_school"
    assert progress._no_row_result("must", outcome.LOOPBACK_OUT) == "bad_link"


def _left_by_the_11_04_code(crawler) -> int:
    """The lab's row as the 11-04 code left it: the page's attachment, queued, its host on queue ip:127.0.0.1, waiting
    out a connection pause."""
    f = crawler.frontier
    crawler.seed_url(HOME)
    drive(crawler, crawler.client_factory(None), QC)
    pid = rows(crawler)[HOME]["id"]
    f.add(LOCALHOST_FILE, host="localhost", site="localhost", unit=None, tier=0, kind="file", depth=1,
          found_via="link", found_from=pid, state="pending", reason="conn_pause", next_at=0.0,
          owner_site="c.nknu.edu.tw/aca", prio=2, leaf=1, kind0="file", added_at=crawler.clock())
    uid = rows(crawler)[LOCALHOST_FILE]["id"]
    f.set_host("localhost", queue=LOOP_Q, ip="127.0.0.1", cname="localhost")
    f.add_page_res(pid, [{"res_key": LOCALHOST_FILE, "how": "get", "role": "file", "need": "must", "res_id": uid}])
    f.request(LOOP_Q, "localhost", "http://localhost/robots.txt", "robots", 1.0, 1.1, None, "connection refused",
              scope="admit:resource:c.nknu.edu.tw/aca:file")
    return uid


def test_the_row_an_older_run_left_on_queue_ip_127_is_skipped_before_anything_is_sent(tmp_path):
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page("<p>教務處公告</p>")})
    uid = _left_by_the_11_04_code(crawler)
    sent = len(net.seen)
    drive(crawler, net, LOOP_Q)
    r = crawler.frontier.row_by_id(uid)
    assert (r["state"], r["reason"]) == ("skipped", outcome.LOOPBACK_OUT)
    assert len(net.seen) == sent and crawler.frontier.q("SELECT count(*) FROM request WHERE queue=?", (LOOP_Q,))[0][0] == 1
    assert result(crawler, LOCALHOST_FILE) == "bad_link"
    pid = rows(crawler)[HOME]["id"]
    assert [r[0] for r in crawler.frontier.q("SELECT result FROM page_resource WHERE page_id=?", (pid,))] == ["bad_link"]
    assert crawler.frontier.q("SELECT count(*) FROM url WHERE state IN ('pending','active')")[0][0] == 0
    assert scope_audit(crawler.frontier) == []  # its robots.txt asked for an admitted row: no breach
    crawler.frontier.mark_school_side(uid, "学校的附件链接写成 localhost")  # a person may say it is the school's
    assert result(crawler, LOCALHOST_FILE) == outcome.SCHOOL_SIDE
    assert _level(crawler, pid)[0] == "complete_school"


def test_a_page_request_to_this_machine_is_a_breach(tmp_path):
    crawler, _, _ = make(tmp_path, {})
    crawler.frontier.request(LOOP_Q, "localhost", LOCALHOST_FILE, "page", 1.0, 1.1, None, "refused",
                             scope="admit:resource:c.nknu.edu.tw/aca:file")
    assert any("loopback" in x for x in scope_audit(crawler.frontier))


def test_a_redirect_to_this_machine_makes_no_row_and_its_attachment_is_not_got(tmp_path):
    crawler, net, _ = crawl(tmp_path, {
        "/aca/default.aspx": page('<p>教務處公告</p><a href="/aca/a.pdf">附件</a>'),
        "/aca/a.pdf": snap("x", 302, headers={"location": "http://127.0.0.1/a.pdf"})})
    crawler.seed_url(HOME)
    settle(crawler, net, [QC, LOOP_Q])
    a = rows(crawler)["https://c.nknu.edu.tw/aca/a.pdf"]
    assert "http://127.0.0.1/a.pdf" not in rows(crawler) and "http://127.0.0.1/a.pdf" not in net.seen
    assert links_of(crawler, a["url"])["http://127.0.0.1/a.pdf"] == outcome.LOOPBACK_OUT
    pid = rows(crawler)[HOME]["id"]
    got = [r[0] for r in crawler.frontier.q("SELECT result FROM page_resource WHERE page_id=?", (pid,))]
    assert got == ["not_file"]  # an attachment led nowhere a file comes from: 连接失败, never 规则不抓
    assert _landed_nowhere(crawler.frontier, a["id"]) == "bad_link"


@pytest.mark.parametrize(("queue", "ip"), [(LOOP_Q, "127.0.0.1"), ("ip:0.0.0.0", "0.0.0.0"), ("ip:::1", "::1")])
def test_a_name_that_resolves_to_this_machine_waits_and_fails_as_a_name_that_does_not_resolve(tmp_path, queue, ip):
    crawler, net, clock = make(tmp_path, {}, resolver=lambda h: (queue, ip, h))
    assert seed(crawler, "https://lo.nknu.edu.tw/a") == "queued"
    h = crawler.frontier.host("lo.nknu.edu.tw")
    assert (h["queue"], h["dns_fail"]) == ("dns-fail", 1)
    assert queue not in crawler.frontier.queues(clock() + 10**6)  # no worker is ever started for it
    for _ in range(DNS_GIVE_UP + 2):
        row = rows(crawler)["https://lo.nknu.edu.tw/a"]
        if row["state"] != "pending":
            break
        assert row["reason"] == "dns"
        clock.t = row["next_at"]
        crawler._reresolve()
    row = rows(crawler)["https://lo.nknu.edu.tw/a"]
    assert (row["state"], row["reason"]) == ("failed", "dns") and result(crawler, row["url"]) == "lab"
    assert net.seen == [] and crawler.frontier.q("SELECT count(*) FROM request")[0][0] == 0


def test_a_host_an_older_run_put_on_a_loopback_queue_is_looked_up_again_before_anything_is_sent(tmp_path):
    crawler, net, clock = make(tmp_path, {}, resolver=lambda h: (LOOP_Q, "127.0.0.1", h))
    crawler.resolver = lambda h: ("ip:140.127.9.9", None, h)  # how the name resolved when it was queued
    seed(crawler, "https://lo.nknu.edu.tw/a")
    crawler.frontier.set_host("lo.nknu.edu.tw", queue=LOOP_Q)  # the 11-04 code kept it on ip:127.0.0.1
    crawler.resolver = lambda h: (LOOP_Q, "127.0.0.1", h)
    drive(crawler, net, LOOP_Q)
    assert net.seen == [] and crawler.frontier.q("SELECT count(*) FROM request")[0][0] == 0
    assert crawler.frontier.host("lo.nknu.edu.tw")["queue"] == "dns-fail"
    row = rows(crawler)["https://lo.nknu.edu.tw/a"]
    assert (row["state"], row["reason"]) == ("pending", "dns") and row["next_at"] > clock()
