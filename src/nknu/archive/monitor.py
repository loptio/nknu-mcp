"""The archive crawl's health report: is it crawling wrong (爬错) or missing things (爬漏)?

Hard violations (exit code 2, must be fixed before the crawl goes on):
- politeness (round-1 design §6.3, review 10-26 C/B5): two requests to one server (by the address it answered from,
  request.ip, else its queue's; across queues, purposes and runs) overlapping, less than the run's least gap between
  one response's end and the next request (run_start says the run's gap: 2 s by default, 1 s at 1–2 s), or a host's
  Crawl-delay not kept; a slow answer not waited out is a warning (politeness_warnings);
- scope: a request to a login system, an internal service or anything else no request may reach (scope.HARD_OUT; the
  listed login re-checks, SSO's campus check page, and a let-in row's own robots.txt and probe aside), or a page
  request that round1.admit() did not let in, or whose placement (request.scope: admit:<verdict>:<owner>:<kind0>, B7)
  the list does not bear out. robots.txt is read and kept, never a block (owner 10-25).
Warnings and coverage: failures by kind (with examples), hosts in cool-down and queues paused, robots unreachable,
login and soft-404 pages, stalled queues (only work that is due: retries waiting, cool-downs and pauses are not
stalls), trap suspects (one site + path with very many query variants, platform paths aside), sites at the cap,
duplicate bodies and URLs differing only in case, seeds that never reached a page (followed through their
redirects), sites with almost no pages (by name), the directory's off-campus sites, how many of v1's announcements
link following found by itself (by UNID, so news.nknu's spelling counts), postback targets per site (what the platform
profiles must replay), skipped-link reasons, the off-campus candidate domains, and requests made on the school's
network (request.campus 'on', review 10-26 B4).

Round 1's acceptance (design §10.5, §13; the second half of this file): facts() reads the result views once
(page_completeness, page_resource); site_status() turns them into each round-1 site's state for the user (✗ 出错,
⚠ 要看, 还在抓, ⚠ 纪录齐了，还没对账, ✓ 爬完: 爬完 only with a reconciliation newer than the site's last crawl whose
every gate passed); checks() is §13's items 1–18 (the dashboard asks the cheap ones, 1–12 and 16); verify() writes
reports/verify-<time>.md and says 2 (something wrong), 1 (something to look at, or not checked) or 0. All of it reads
the frontier read-only and sends nothing.
"""

from __future__ import annotations

import gzip
import json
import re
import sqlite3
import time
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nknu.archive import links, outcome, profiles, scope
from nknu.archive.frontier import ALL_QUEUES, Frontier, net_key

MIN_GAP_S = 2.0  # the least gap of a run whose run_start does not say (the default 2–5 s)
TOLERANCE_S = 0.05
STALL_S = 15 * 60
TRAP_VARIANTS = 300
REDIRECTS = (301, 302, 303, 307, 308)
_UNID = re.compile(r"(?i)unid=([0-9a-z-]+)")
_OLD_REASON = re.compile(r"^https?://\S+?: (.*)$")
_HTTP = re.compile(r"HTTP (\d{3})\b")


def _alive(root: Path) -> str | None:
    """The pid of the run holding run.lock, or None: a run is going exactly while the lock is held."""
    from nknu.archive.crawler import lock_holder

    return lock_holder(root)


def _server(r) -> str:
    """The server a request went to: the address it answered from, else its queue's (ip:<address>, or google)."""
    if r["ip"]:
        return r["ip"]
    q = r["queue"] or ""
    return q.removeprefix("ip:")


def _run_gaps(f: Frontier) -> list[tuple[float, float]]:
    """(when a run started, the least gap it was set to), in order; a run_start of an older version says no gap."""
    out = []
    for r in f.q("SELECT at, message FROM event WHERE code='run_start' ORDER BY at"):
        try:
            at = datetime.fromisoformat(r["at"]).timestamp()
        except (TypeError, ValueError):
            continue
        try:
            gap = float(json.loads(r["message"] or "{}").get("gap", [MIN_GAP_S])[0])
        except (ValueError, TypeError, AttributeError, IndexError, KeyError):
            gap = MIN_GAP_S
        out.append((at, gap))
    return out


def _least_gap(runs: list[tuple[float, float]], started: float) -> float:
    gap = MIN_GAP_S
    for at, g in runs:
        if at > started + TOLERANCE_S:
            break
        gap = g
    return gap


def _requests(f: Frontier):
    return f.q("SELECT id, queue, host, url, purpose, started, ended, ip FROM request ORDER BY started, id")


def politeness(f: Frontier) -> list[str]:
    """The hard violations: overlapping requests to one server, a gap below the run's least, a Crawl-delay not kept."""
    out = []
    runs = _run_gaps(f)
    delays = {r["host"]: min(float(r["crawl_delay"]), 300.0) for r in f.q("SELECT host, crawl_delay FROM host "
                                                                             "WHERE crawl_delay > 0")}
    last: dict[str, tuple[float, float, str]] = {}
    last_host: dict[str, float] = {}
    for r in _requests(f):
        key = _server(r)
        prev = last.get(key)
        if prev is not None:
            least = _least_gap(runs, r["started"])
            if r["started"] < prev[1] - TOLERANCE_S:
                out.append(f"并发：服务器 {key} 在上一个请求结束前又发了请求（{prev[2]} → {r['url']}）")
            elif r["started"] - prev[1] < least - TOLERANCE_S:
                out.append(f"间隔不足：服务器 {key} 只隔 {r['started'] - prev[1]:.2f} 秒（下限 {least:g} 秒；{r['url']}）")
        delay = delays.get(r["host"])
        if delay and r["host"] in last_host and r["started"] - last_host[r["host"]] < delay - TOLERANCE_S:
            out.append(f"没照 Crawl-delay：{r['host']} 要求 {delay:g} 秒，只隔 {r['started'] - last_host[r['host']]:.1f} 秒"
                       f"（{r['url']}）")
        last[key] = (r["started"], max(r["ended"], prev[1]) if prev else r["ended"], r["url"])
        last_host[r["host"]] = r["ended"]
    return out


def politeness_warnings(f: Frontier) -> list[str]:
    """Our own rule (design §6.3), a warning only: after a slow answer, at least as long a wait (at most 30 s)."""
    out = []
    last: dict[str, tuple[float, float]] = {}
    for r in _requests(f):
        key = _server(r)
        prev = last.get(key)
        if prev is not None:
            took = min(prev[1] - prev[0], 30.0)
            if prev[1] <= r["started"] < prev[1] + took - TOLERANCE_S:
                out.append(f"服务器 {key} 上一个回应花了 {took:.1f} 秒，之后只等了 {r['started'] - prev[1]:.1f} 秒（{r['url']}）")
        last[key] = (r["started"], r["ended"])
    return out


# the request scopes the crawler writes: admit:<verdict>:<owner site>:<kind0> (Crawler._admitted_as; an older version
# wrote admit:<kind0>), and the crawler's own steps
_ADMITTED = ("admit:", "scheme_switch")
# what a row's own requests are for: its host's robots.txt and missing-page probe, asked because the row was let in
_FOR_A_ROW = ("robots", "probe")
# the browser check and reconcile's spot check (design §8.4, §18.2b R6): judging, not fetching, so scope.judge's
# HARD_OUT does not hold for them (the library's home reads a docs.google.com sheet) but for what they never send
# (jscheck.NEVER_SEND: login systems, internal services)
BROWSER_PURPOSES = ("jscheck", "spotcheck")


def _placement_breach(plan, url: str, admitted: str, owners: set[str]) -> str | None:
    """Whether the placement a page request went out under (admit:<verdict>:<owner>:<kind0>) holds by the list as it
    is (second review 10-26: every request said admit:<kind0>, so the audit could tell nothing). r1: the address is a
    page of its own round-1 site (inside its paths) or an extra page; resource: its owner is a round-1 site (or an extra
    page's or a login re-check's); round2: a round-two row `retry` brought back, allowed. An older scope says
    nothing to check."""
    parts = admitted.split(":", 3)
    if len(parts) != 4:
        return None
    _admit, verdict, owner, _kind0 = parts
    if verdict == "r1":
        key, s = scope.site_key(url), plan.site(scope.site_key(url))
        if plan.is_extra(url) or (key == owner and s is not None and s.round == "1" and s.holds(url)):
            return None
        return f"超出范围：{url}（放行纪录说是第一轮网站 {owner} 的网页，名单上不是）"
    if verdict == "resource":
        s = plan.site(owner)
        if owner in owners or (s is not None and s.round == "1"):
            return None
        return f"超出范围：{url}（放行纪录说是 {owner} 的网页用到的，它不是第一轮网站）"
    if verdict == "round2":
        return None
    return f"超出范围：{url}（看不懂的放行纪录 {admitted}）"


def scope_audit(f: Frontier, plan=None) -> list[str]:
    """What no request may reach (scope.HARD_OUT), and page requests nothing admitted (B7: request.scope is what
    admit() said when the row went in, so a page's off-campus attachment, a short link or a form is no breach). The
    listed login re-checks (design §4.2) and SSO's campus check page (B4) are login systems' addresses asked on purpose,
    once each. What jscheck and reconcile's R6 ask (BROWSER_PURPOSES) is a breach only when it is what they never
    send (a login system, an internal service). A row's robots.txt and probe say which row they were asked for (its placement): a form's host
    docs.google.com is no breach for its robots.txt (second review 10-26). Given the list (plan), a page request's
    placement is checked against it (_placement_breach)."""
    from nknu.archive.crawler import CAMPUS_URL
    from nknu.archive.jscheck import NEVER_SEND as never_send

    checks = {r[0] for r in f.q("SELECT url FROM url WHERE kind0='login_check'")}
    owners: set[str] = set()
    if plan is not None:
        checks |= {c for u in plan.login_checks if (c := scope.canonical(u))}
        owners = {scope.site_key(c) for u in (*plan.extra, *plan.login_checks) if (c := scope.canonical(u))}
    try:
        changed = {r["reason"]: r["at"] for r in f.q("SELECT reason, at FROM rule_change")}
    except Exception:  # noqa: BLE001 - a frontier from before the table existed
        changed = {}
    out = []
    for r in f.q("SELECT host, url, purpose, scope, started FROM request"):
        url, purpose, admitted = r["url"], r["purpose"], r["scope"] or ""
        if (purpose == "campus" and url == CAMPUS_URL) or (purpose == "page" and url in checks):
            continue
        if purpose in _FOR_A_ROW and admitted.startswith("admit:"):
            continue  # asked for a row that was let in: robots.txt and a missing page are never content
        v = scope.judge(url)
        if purpose in BROWSER_PURPOSES:
            if v.reason in never_send:
                out.append(f"超出范围：{url}（{v.reason}；浏览器检查绝不送这种请求）")
            continue
        if v.reason in changed and r["started"] < changed[v.reason]:
            continue  # fetched before the rule existed: reported by retro_counts, quarantined, not a breach
        if v.reason in scope.HARD_OUT:
            out.append(f"超出范围：{url}（{v.reason}）")
        elif purpose == "page" and not admitted.startswith(_ADMITTED):
            out.append(f"超出范围：{url}（没有放行纪录）")
        elif purpose == "page" and plan is not None and (breach := _placement_breach(plan, url, admitted, owners)):
            out.append(breach)
    return out


def _kind(reason: str | None) -> str:
    """A failure reason as a kind; reasons written by older versions began with the URL."""
    reason = reason or "?"
    m = _OLD_REASON.match(reason)
    if not m:
        return reason
    h = _HTTP.match(m[1])
    return f"http_{h[1]}" if h else m[1][:60]


def failure_groups(f: Frontier, state: str = "failed") -> list[tuple[str, int, list[str]]]:
    """(kind, how many, three examples) for the URLs in state, most first."""
    groups: dict[str, list[str]] = defaultdict(list)
    for r in f.q("SELECT reason, url FROM url WHERE state=? ORDER BY id", (state,)):
        groups[_kind(r["reason"])].append(r["url"])
    return sorted(((k, len(v), v[:3]) for k, v in groups.items()), key=lambda g: (-g[1], g[0]))


def stalled_queues(f: Frontier, now: float) -> list[str]:
    """Queues with work due right now (not waiting for a retry, a host cool-down or Crawl-delay, a queue pause or the
    network) whose last request ended more than STALL_S ago (plus the largest Crawl-delay among its due hosts)."""
    if f.paused_until(ALL_QUEUES) > now:
        return []
    started = f.q("SELECT max(at) FROM event WHERE code='run_start'")[0][0]
    since = datetime.fromisoformat(started).timestamp() if started else None
    out = []
    for r in f.q("""SELECT h.queue, max(coalesce(h.crawl_delay, 0)) delay FROM url u JOIN host h ON h.host = u.host
                    WHERE u.state = 'pending' AND u.next_at <= ? AND h.cooldown_until <= ? AND h.not_before <= ?
                      AND h.queue != 'dns-fail'
                    GROUP BY h.queue ORDER BY h.queue""", (now, now, now)):
        if f.paused_until(r["queue"]) > now:
            continue
        last = f.q("SELECT max(ended) FROM request WHERE queue=?", (r["queue"],))[0][0] or since
        if last is not None and now - last > STALL_S + (r["delay"] or 0):
            out.append(r["queue"])
    return out


def _seed_end(f: Frontier, seed) -> tuple[object, str | None]:
    """Where a seed really ends: through its redirects (at most six hops) to the row that holds the content."""
    row = seed
    for _ in range(6):
        if not (row["state"] == "done" and row["status"] in REDIRECTS):
            break
        hop = f.q("SELECT dst, verdict FROM link WHERE src=? AND text='[redirect]'", (row["id"],))
        if not hop:
            return row, "跳转目标不明"
        if hop[0]["verdict"].startswith(("out:", "skipped:")):
            return row, f"跳到 {hop[0]['dst']}（{hop[0]['verdict']}）"
        nxt = f.q("SELECT * FROM url WHERE url=?", (hop[0]["dst"],))
        if not nxt:
            return row, f"跳到 {hop[0]['dst']}（不在待抓清单）"
        row = nxt[0]
    return row, None


def seed_status(f: Frontier, seed) -> tuple[str, str | None]:
    """ok (reached a page with content), wait (not there yet) or bad, and why: a seed that ends off campus, at a
    login, at a fake page or at an error has crawled nothing, whatever its own row says."""
    end, why = _seed_end(f, seed)
    if why:
        return "bad", why
    via = f"（经跳转到 {end['url']}）" if end["id"] != seed["id"] else ""
    if end["state"] in ("failed", "skipped"):
        return "bad", f"{end['state']} {end['reason'] or ''}{via}"
    if end["state"] in ("pending", "active"):
        return "wait", f"还没抓到内容{via}"
    if end["status"] != 200:
        return "bad", f"HTTP {end['status']}{via}"
    flags = end["flags"] or ""
    if "login" in flags or "soft404" in flags:
        return "bad", f"{flags}{via}"
    return "ok", None


def report(root: Path) -> tuple[str, int]:
    """The report on root/frontier.db, which is opened read-only: the monitor never writes to a running crawl's
    database (OutdatedFrontier when it lacks what is read here)."""
    f = Frontier(root / "frontier.db", readonly=True)
    try:
        return _report(root, f)
    finally:
        f.close()


def _report(root: Path, f: Frontier) -> tuple[str, int]:
    now = time.time()
    hard = politeness(f) + scope_audit(f)
    lines: list[str] = []
    w = lines.append
    pid = _alive(root)
    w(f"# 存档爬虫状态 {datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M')}")
    w("")
    w(f"- 爬虫进程：{'运行中（pid ' + str(pid) + '）' if pid else '没有在跑'}")
    states = dict(f.q("SELECT state, count(*) FROM url GROUP BY state"))
    total = sum(states.values())
    w(f"- 网址：共 {total}；" + "、".join(f"{k} {v}" for k, v in sorted(states.items())))
    req = f.q("SELECT count(*) n, min(started) a, max(ended) b, sum(purpose='page') p FROM request")[0]
    hour = f.q("SELECT count(*) FROM request WHERE started > ?", (now - 3600,))[0][0]
    size = f.q("SELECT coalesce(sum(bytes),0) FROM url WHERE state='done'")[0][0]
    w(f"- 请求：共 {req['n']}（页面 {req['p'] or 0}），最近一小时 {hour}；已存 {size / 1e6:.1f} MB")
    by_status = f.q("SELECT status, count(*) n FROM url WHERE state='done' GROUP BY status ORDER BY n DESC")
    w("- 回应状态：" + "、".join(f"{r['status']}×{r['n']}" for r in by_status))
    net = f.paused_until(ALL_QUEUES)
    if net > now:
        w(f"- 网络暂停：本机网络像是断了，所有队列暂停到 {datetime.fromtimestamp(net, UTC).astimezone():%H:%M}"
          "（恢复前会再检查一次网络）")
    for network, label in (("school", "校网（140.127.*）"), ("other", "校外网络")):
        until = f.pause_of(net_key(network))
        if until > now:
            w(f"- 网络暂停：{label}没有回应、连网检查也失败，它的队列暂停到 "
              f"{datetime.fromtimestamp(until, UTC).astimezone():%H:%M}（另一边照常抓）")
    w("")
    w("## 硬性检查（违反就要停下来修）")
    if hard:
        for x in hard[:30]:
            w(f"- ❌ {x}")
        if len(hard) > 30:
            w(f"- ……另有 {len(hard) - 30} 条")
    else:
        w("- ✅ 礼貌（每台服务器同时 1 条连线、间隔不低于这次 run 的下限、照 Crawl-delay）、范围都没有违反")
    errors = f.q("SELECT at, code, host, message FROM event WHERE level='error' AND code IN ('warc_write','disk_low') "
                 "ORDER BY rowid DESC LIMIT 5")
    for r in errors:
        w(f"- ⚠️ 爬虫因此停下：{r['at']} {r['code']} {r['host'] or ''}：{(r['message'] or '')[:160]}")
    w("")
    w("## 进度（每台服务器一条队）")
    w("| 队列 | 主机数 | 待抓 | 完成 | 失败/跳过 | 最近一小时请求 | 平均耗时 | 预计剩余 |")
    w("|---|---|---|---|---|---|---|---|")
    for r in f.q("""SELECT h.queue, count(DISTINCT h.host) hosts,
                      sum(u.state IN ('pending','active')) pend, sum(u.state='done') done,
                      sum(u.state IN ('failed','skipped')) bad
                    FROM host h JOIN url u ON u.host = h.host GROUP BY h.queue ORDER BY pend DESC LIMIT 40"""):
        stats = f.q("SELECT count(*) n, avg(ended - started) d FROM request WHERE queue=? AND started>?",
                    (r["queue"], now - 3600))[0]
        eta = f"{r['pend'] / stats['n']:.1f} 小时" if stats["n"] else "—"
        w(f"| {r['queue']} | {r['hosts']} | {r['pend']} | {r['done']} | {r['bad']} | {stats['n']} | "
          f"{(stats['d'] or 0):.1f} 秒 | {eta} |")
    w("")
    w("## 警告")
    for x in politeness_warnings(f)[:10]:
        w(f"- 服务器变慢时没多等：{x}")
    try:
        changes = f.q("SELECT reason, at, note FROM rule_change")
    except Exception:  # noqa: BLE001 - a frontier from before the table existed
        changes = []
    for r in changes:
        n = f.q("SELECT count(*) FROM url WHERE reason = ?", (f"quarantined_{r['reason']}",))[0][0]
        w(f"- 规则更新前抓到、已隔离（{r['reason']}）：{n} 个网址，{r['note'] or ''}")
    stalled = stalled_queues(f, now) if pid else []
    if stalled:
        w(f"- 卡住的队列（有现在就该抓的网址、没冷却、没暂停，15 分钟没有请求）：{', '.join(stalled)}")
    for kind, n, sample in failure_groups(f):
        w(f"- 失败：{kind} × {n}（例如 {', '.join(sample)}）")
    waiting = [g for g in failure_groups(f, "pending") if g[0] not in ("?",)]
    for kind, n, sample in waiting[:8]:
        w(f"- 在等（重试、冷却或网络）：{kind} × {n}（例如 {sample[0]}）")
    for r in f.q("SELECT queue, until, why FROM pause WHERE until > ? AND queue != ? AND queue NOT LIKE 'net:%'",
                 (now, ALL_QUEUES)):
        w(f"- 队列暂停：{r['queue']}（还要 {(r['until'] - now) / 60:.0f} 分钟；{r['why'] or ''}）")
    for r in f.q("SELECT host, cooldown_until FROM host WHERE cooldown_until > ?", (now,)):
        w(f"- 冷却中：{r['host']}（还要 {(r['cooldown_until'] - now) / 60:.0f} 分钟）")
    for r in f.q("SELECT dir, until FROM deny WHERE until > ?", (now,)):
        w(f"- 目录连续拒绝、暂缓：{r['dir']}（还要 {(r['until'] - now) / 60:.0f} 分钟；同主机其他目录照常抓）")
    for r in f.q("SELECT host, crawl_delay FROM host WHERE crawl_delay > 5"):
        w(f"- robots.txt 要求的 Crawl-delay：{r['host']} {min(r['crawl_delay'], 300):.0f} 秒（只这台主机等，同服务器的其他主机照常）")
    for r in f.q("SELECT host, robots_fail FROM host WHERE robots_fail > 0"):
        w(f"- robots.txt 读不到（这台等一小时再试，已连续 {r['robots_fail']} 次）：{r['host']}")
    redirected = f.q("SELECT count(DISTINCT host) FROM event WHERE code='robots_redirect'")[0][0]
    if redirected:
        w(f"- robots.txt 跳到别的服务器或跳太多次（照 RFC 9309 当成没有 robots.txt）：{redirected} 台主机")
    for r in f.q("SELECT host, probe_title FROM host WHERE probe_status=200"):
        w(f"- 不存在的网页也回 200：{r['host']}（标题「{(r['probe_title'] or '')[:30]}」，相同样子的页面会被标记）")
    for flag in ("login", "soft404", "decode"):
        n = f.q("SELECT count(*) FROM url WHERE flags LIKE ?", (f"%{flag}%",))[0][0]
        if n:
            sample = [r["url"] for r in f.q("SELECT url FROM url WHERE flags LIKE ? LIMIT 3", (f"%{flag}%",))]
            label = {"login": "登录页", "soft404": "疑似假页面", "decode": "无法严格解码（原样存档，链接照跟）"}[flag]
            w(f"- {label}：{n} 个，例如 {', '.join(sample)}")
    for r in f.q("SELECT reason, count(*) n FROM url WHERE state='skipped' GROUP BY reason ORDER BY n DESC"):
        w(f"- 跳过：{r['reason']} × {r['n']}")
    for r in f.q("SELECT site, count(*) n FROM url WHERE kind='page' GROUP BY site HAVING n >= 3000 ORDER BY n DESC"):
        w(f"- 网站接近上限：{r['site']}（{r['n']} 个网页）")
    # every way a page finds an address counts (a script redirect, a scheme switch, a short link's target …); the
    # seeds do not (design §8.3)
    traps = [r for r in f.q("""SELECT site, path, count(*) n FROM url
                               WHERE found_via NOT IN ('seed','v1_known','old_frontier','commoncrawl')
                               GROUP BY site, path HAVING n >= ? ORDER BY n DESC LIMIT 20""", (TRAP_VARIANTS,))
             if not scope.is_platform_path(r["path"])]
    for r in traps[:10]:
        w(f"- 可能是无限网址（同一网站同一路径很多参数组合）：{r['path']} × {r['n']}")
    for r in f.q("""SELECT sha256, count(*) n, min(url) u FROM url WHERE state='done' AND status=200 AND bytes > 500
                    GROUP BY sha256 HAVING n >= 20 ORDER BY n DESC LIMIT 5"""):
        w(f"- 很多网址内容完全相同（网址写法可能要合并）：{r['n']} 个，例如 {r['u']}")
    pairs = f.q("""SELECT count(*) g, coalesce(sum(n), 0) n FROM (SELECT sha256, count(*) n FROM url
                   WHERE state='done' AND status=200 AND bytes > 500 GROUP BY sha256 HAVING count(*) >= 2)""")[0]
    if pairs["g"]:
        w(f"- 内容完全相同的网址：{pairs['g']} 组共 {pairs['n']} 个（两个以上就算）")
    case = f.q("SELECT lower(url) k, count(*) n, min(url) u FROM url GROUP BY k HAVING n > 1 ORDER BY n DESC LIMIT 5")
    if case:
        w("- 网址只差大小写（那台服务器若不分大小写，就是同一页抓了几次）：" + "、".join(f"{r['u']} × {r['n']}" for r in case))
    campus = campus_requests(f)
    if campus.on:
        w(f"- {campus.line()}")
    for r in f.q("SELECT at, level, code, host, message FROM event ORDER BY rowid DESC LIMIT 8"):
        w(f"- 事件 {r['at']} [{r['level']}] {r['code']} {r['host'] or ''}：{(r['message'] or '')[:120]}")
    w("")
    w("## 爬漏检查")
    seeds = f.q("SELECT * FROM url WHERE found_via='seed' ORDER BY id")
    status = [(s, *seed_status(f, s)) for s in seeds]
    problems = [(s, why) for s, st, why in status if st == "bad"]
    reached = sum(st == "ok" for _, st, _ in status)
    w(f"- 起点网站：{len(seeds)} 个，抓到内容 {reached}，还在等 {len(seeds) - reached - len(problems)}，"
      f"有问题 {len(problems)}")
    for s, why in problems[:20]:
        w(f"  - {s['unit']}：{s['url']}（{why}）")
    v1 = f.q("SELECT url, state, status FROM url WHERE found_via='v1_known'")
    if v1:
        linked_unids = {m.lower() for (dst,) in f.q("SELECT dst FROM link WHERE dst LIKE '%lastnews.aspx%'")
                        for m in _UNID.findall(dst)}
        ok = sum(1 for r in v1 if r["state"] == "done" and r["status"] == 200)
        linked = sum(1 for r in v1 if any(m.lower() in linked_unids for m in _UNID.findall(r["url"])))
        w(f"- v1 已知的公告 {len(v1)} 则：已存 {ok}；其中靠跟链接也找得到的 {linked}（按 UNID 比对，news.nknu 的写法也算；"
          "找不到的，多半要靠列表翻页的回传，等平台档案补）")
    per_site = f.q("SELECT site, sum(state='done') d, count(*) n FROM url GROUP BY site ORDER BY n, d, site")
    thin = [r for r in per_site if r["n"] <= 2]
    names = "、".join(f"{r['site']}（已抓 {r['d']}／共 {r['n']}）" for r in thin[:30])
    w(f"- 网站数：{len(per_site)}；只有 1–2 个网址的网站 {len(thin)} 个（可能入口太浅、要 JS 或被挡）"
      + (f"：{names}" + ("……" if len(thin) > 30 else "") if thin else ""))
    dirs = f.directory_sites()
    if dirs:
        counts = {r["site"]: r for r in f.q("SELECT site, sum(state='done') d, count(*) n FROM url GROUP BY site")}
        w(f"- 目录直链的校外网站（官方目录页直接链接，自动收）：{len(dirs)} 个："
          + "、".join(f"{site}（已抓 {(counts[site]['d'] or 0) if site in counts else 0}／共 "
                     f"{counts[site]['n'] if site in counts else 0}，出自 {row['via_url']}）"
                     for site, row in list(dirs.items())[:20]))
    pb = f.q("SELECT site, count(*) t, sum(n) n FROM postback GROUP BY site ORDER BY n DESC LIMIT 10")
    if pb:
        w("- 只能靠回传（postback）才到得了的连结，按网站：" + "、".join(f"{r['site']} {r['n']}" for r in pb))
    skipped = defaultdict(int)
    for r in f.q("SELECT verdict, count(*) n FROM link WHERE verdict NOT IN ('queued','dup') GROUP BY verdict"):
        skipped[r["verdict"]] += r["n"]
    if skipped:
        w("- 没跟的连结：" + "、".join(f"{k} {v}" for k, v in sorted(skipped.items(), key=lambda x: -x[1])))
    off = f.q("SELECT domain, n FROM offsite ORDER BY n DESC LIMIT 15")
    if off:
        w("- 校外网域（候选清单，第二阶段每周确认）：" + "、".join(f"{r['domain']} {r['n']}" for r in off))
    text = "\n".join(lines) + "\n"
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
    (reports / f"status-{stamp}.md").write_text(text, encoding="utf-8")
    (reports / "latest.md").write_text(text, encoding="utf-8")
    return text, (2 if hard else 0)


# ================================================================ round 1's acceptance (design §10.5, §13)
#
# How each rule below is carried out, where the design does not spell it out, is 【Claude 的执行办法】: the owner may
# overturn any. Every reader takes a Frontier opened read-only, or the dashboard's own read-only connection
# (progress.connect: mode=ro, query_only, the result views in its temp schema); nothing here writes the database.

TAIPEI = ZoneInfo("Asia/Taipei")
# a check's result (design §13): ✓ 通过, ⚠ 要看, ✗ 出错, or 没检查 (yellow: what was not checked is not known fine)
PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
CHECK_ZH = {PASS: "✓ 通过", WARN: "⚠ 要看", FAIL: "✗ 出错", SKIP: "没检查"}
_RANK = {PASS: 0, SKIP: 1, WARN: 2, FAIL: 3}
# a site's state (design §10.5), the codes the dashboard reads (progress.STATE_SHOW)
ERROR, LOOK, CRAWLING, UNRECONCILED, DONE, NOT_CHECKED = "error", "look", "crawling", "unreconciled", "done", "unchecked"
SITE_STATE = {ERROR: ("✗ 出错", "red"), LOOK: ("⚠ 要看", "yellow"), NOT_CHECKED: ("⚠ 没检查", "yellow"),
              CRAWLING: ("还在抓", "none"), UNRECONCILED: ("⚠ 纪录齐了，还没对账", "yellow"), DONE: ("✓ 爬完", "green")}
SITE_ORDER = (ERROR, LOOK, NOT_CHECKED, CRAWLING, UNRECONCILED, DONE)
# the gates of a reconciliation (design §18.2b): every one passed, and the site may be 爬完; R3b, R4b only estimate.
# R5 (the 25 questions' answer pages) was one until 10-26 (the owner agreed to take it out: they measure the whole
# project's answers, not round 1's completeness): a reconciliation written before says it, and is read past
# (reconciled()); R6 and R7 keep their numbers
GATES = ("R1", "R2", "R3a", "R4a", "R6", "R7", "V13", "V14")
DROPPED_GATES = ("R5",)
# what makes a reconciliation stale (§10.5, §18.2b): a run or a rejudge that covers the site, and a person's verdict on
# a page type with pages there (jscheck --set, event verdict_set: thin is judged only on pages whose type is no js or
# frame, review 10-26 【Claude 的执行办法】)
STALE_CODES = ("run_end", "rejudge", "verdict_set")
STALE_ZH = {"run_end": "抓取", "rejudge": "rejudge", "verdict_set": "jscheck --set 改判"}
COVER_MIN = 0.8  # §18.2b R3b, R4b: below it a warning, never a gate (calibrated on the pilot: reconcile.py reads it)
SHRINK, SHRINK_MIN_BASE = 0.8, 20  # §10.5, §13 item 3: fewer than 80% of the old archive's rows, when it had 20 or more
SAMPLE, IMAGE_SAMPLE = 10, 5  # §13 item 15
REPORT_LINES = 200  # one item's lines in the report before 「……另有 N 条」
WAYBACK_GAP = "只有 Wayback 记得的过期公告约 10,900 则（SSO 6,842、c.nknu 约 4,100），等你同意才用"
BUCKET_ZH = {code: zh for code, zh, _then in outcome.BUCKETS}
LABEL_ZH = dict(outcome.LABEL_ORDER)
# what a site's home may come to and still be a page that came back (§10.5 「首页没抓到内容」): thin is not
_HOME_GOT = tuple(c for c in outcome.PAGE_CAME_BACK if c != "thin")
# how a result weighs for a site (§10.5): red, yellow, 待判定 (yellow until this round's jscheck ended, then red),
# waiting (还在抓), or nothing to do
RED, YELLOW, PENDING, WAIT, GOOD = "red", "yellow", "pending", "wait", "good"
_TONE = {"failed": RED, "review": RED, "pending_check": PENDING, "next_round": YELLOW, "queued": WAIT}


def _frontier(src) -> Frontier:
    """A Frontier as given, or one over a read-only connection the caller opened (the dashboard's): no table, view or
    index is made, nothing written (Frontier.__init__ is not run)."""
    if isinstance(src, Frontier):
        return src
    import threading

    f = Frontier.__new__(Frontier)
    f.path, f.lock, f.conn = None, threading.RLock(), src
    if src.row_factory is None:
        src.row_factory = sqlite3.Row
    return f


def _ts(at) -> float | None:
    """An event's or a reconciliation's time (frontier.utc_now(): ISO with its offset) as a timestamp."""
    if isinstance(at, int | float):
        return float(at)
    try:
        return datetime.fromisoformat(at).timestamp() if at else None
    except (TypeError, ValueError):
        return None


def _taipei(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, TAIPEI).strftime("%m-%d %H:%M") if ts else "—"


def bucket(result: str, page: bool = False) -> str:
    """The user's category of a result (outcome.user_bucket) as the page layer counts it (§10.4): a resource whose
    redirects never land, and a round-1 page that itself leads where the rules leave out (OWN_REVIEW), are 要人看 there,
    never 规则不抓."""
    if result == "redirect" or (page and result in outcome.OWN_REVIEW):
        return "review"
    return outcome.user_bucket(result)


def tone(result: str, page: bool = False) -> str:
    """How a result weighs for its site (§10.5): 连接失败 and 要人看 red, but lab (the lab machine fetches it again)
    yellow; 下一轮 yellow; 待判定 its own (yellow until jscheck ended); 还在排队 waiting; the rest nothing to do."""
    if result == "lab":
        return YELLOW
    return _TONE.get(bucket(result, page), GOOD)


# ---------------------------------------------------------------- the runs, rejudges and browser checks (events)

@dataclass(frozen=True)
class Mark:
    """One run_end, rejudge, verdict_set, jscheck_end or reconcile event (rowid: the order they were written) and the sites it covers (None:
    every site; the message's "sites", as run_end writes it, design §11). A message nobody can read covers every site,
    which only ever makes a reconciliation older, never newer."""

    rowid: int
    code: str
    at: float | None
    sites: frozenset[str] | None
    reason: str | None

    def covers(self, site: str) -> bool:
        return self.sites is None or site in self.sites


class Rounds:
    """What the event table says of the crawls (run_end), the rejudges, the browser checks (jscheck_end) and the
    reconciliations (reconcile): their message is JSON with "sites" (null: all) and, for run_end and jscheck_end,
    "reason" (design §8.4, §11, §18.2a, §18.2b)."""

    CODES = ("run_end", "rejudge", "jscheck_end", "reconcile", "verdict_set")

    def __init__(self, f: Frontier):
        self.marks: list[Mark] = []
        for r in f.q(f"SELECT rowid AS rid, at, code, message FROM event WHERE code IN "
                     f"({','.join('?' * len(self.CODES))}) ORDER BY rowid", self.CODES):
            try:
                msg = json.loads(r["message"] or "")
            except (TypeError, ValueError):
                msg = None
            sites, reason = None, None
            if isinstance(msg, dict):
                if isinstance(msg.get("sites"), list):
                    sites = frozenset(str(s) for s in msg["sites"])
                reason = msg.get("reason") if isinstance(msg.get("reason"), str) else None
            self.marks.append(Mark(r["rid"], r["code"], _ts(r["at"]), sites, reason))

    def last(self, site: str, *codes: str) -> Mark | None:
        """The newest of these events that covers the site."""
        return next((m for m in reversed(self.marks) if m.code in codes and m.covers(site)), None)

    def jscheck_done(self, site: str) -> bool:
        """This round's browser check ended for the site (§8.4, §10.5): a jscheck_end that finished, covers it and was
        written after the last run_end that covers it. Until then 待判定 is yellow; after, red."""
        run = self.last(site, "run_end")
        return any(m.code == "jscheck_end" and m.reason == "finished" and m.covers(site)
                   and (run is None or m.rowid > run.rowid) for m in self.marks)


# ---------------------------------------------------------------- each site's reconciliation (§18.2b)

def _gate_status(v) -> str | None:
    """One item of reconcile_site.checks: a status as text, or an object with "result" (or "status")."""
    if isinstance(v, dict):
        v = v.get("result") or v.get("status")
    return v.lower() if isinstance(v, str) else None


def cover_words(source: str, counts: Mapping) -> str:
    """R3b's or R4b's numbers in words (design §18.2b; reconcile's report, the dashboard and verify write the same):
    what the estimate rests on, that it runs high (both lists are found by following links), and what is missed at
    least; an estimate no larger than what this round got gives no number at all (review 10-26)."""
    cover = counts.get("cover")
    if cover is None:
        return f"{source}覆盖率估不出"
    big_m, m = counts.get("known_ok"), counts.get("linked")
    rests = f"{source}记得、这一轮回 200 的 {big_m} 个里，{m} 个被这一轮的网页链到；" if None not in (big_m, m) else ""
    head = f"{source}覆盖率估计 {cover:.0%}（{rests}两份名单都是顺着链接找的，这个数偏高）"
    if None in (big_m, m):
        return head
    if not m:
        return f"{head}；估不出总数（没有一个被这一轮的网页链到）"
    if counts.get("missed_at_least") is None:
        return f"{head}；估不出还漏多少（这样估出的总数不比这一轮已经抓到的多）"
    return f"{head}；至少还漏约 {counts['missed_at_least']} 个（下限）"


COVER_SOURCES = (("R3b", "Common Crawl "), ("R4b", "旧存档"))
# a person's word on a menu item that is the school's broken link (R1 bad_link: the address of a school error page,
# which has no url row; design §10.2.1, the owner's 10-26 decision): an event of its own, written by
# reconcile.review_menu_mark, read by menu_marks() 【Claude 的执行办法: an event, not a table: a new table would make
# every archive an older version wrote (the rehearsal's among them) one that only a writer may open】
MENU_MARK_EVENT = "menu_mark"


def menu_marks(src, site: str) -> dict[str, dict]:
    """The marks a person put on a site's menu items that are the school's broken links (R1 bad_link), as long as
    what was looked at is what is there: the newest menu_mark event of each address, its page not fetched again (the
    same fetched_at: a new fetch, the same answer included, ends the mark for good, as a url row's mark), that page
    still linking it as a school error page's address, and still no row for the address. {address: the mark (site,
    target, page_id, page, fetched_at, mark, result, note, at)}. Nothing is written: a mark that no longer holds is
    simply not there."""
    f = _frontier(src)
    newest: dict[str, dict] = {}
    for r in f.q("SELECT at, message FROM event WHERE code=? ORDER BY rowid", (MENU_MARK_EVENT,)):
        try:
            msg = json.loads(r["message"] or "")
        except (TypeError, ValueError):
            continue
        if isinstance(msg, dict) and msg.get("site") == site and isinstance(msg.get("target"), str):
            newest[msg["target"]] = {**msg, "at": r["at"]}
    out = {}
    for target, m in newest.items():
        if m.get("mark") != outcome.SCHOOL_SIDE or not outcome.markable(m.get("result") or "", None):
            continue
        page = f.q("SELECT fetched_at FROM url WHERE id=?", (m.get("page_id"),))
        if not page or page[0][0] != m.get("fetched_at"):
            continue
        if not f.q("SELECT 1 FROM link WHERE src=? AND dst=? AND verdict=? LIMIT 1",
                   (m["page_id"], target, outcome.ERROR_OUT)):
            continue
        if f.q("SELECT 1 FROM url WHERE url=? LIMIT 1", (target,)):
            continue
        out[target] = m
    return out


def _n(v) -> int:
    """A count reconcile wrote (anything else, 0)."""
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


# a gate that passed with nothing to compare (reconcile.SiteResult.uncompared), in a few words
UNCOMPARED_ZH = {"R1": "R1 没认出选单", "R2": "R2 没有写总数的列表", "R3a": "R3a Common Crawl 不认得这个网站",
                 "R4a": "R4a 旧存档不认得这个网站"}


@dataclass(frozen=True)
class Reconciled:
    """A site's last reconciliation (reconcile_site) as §10.5 reads it: fresh when no run_end, rejudge or verdict_set
    (STALE_CODES) covering the site was written after it: after the last reconcile event that covers the site, in the order the events were
    written (reconcile.py writes one with each reconcile_site row: a run_end in the same second is still before or after
    it, never a guess); a row no reconcile event covers (an older writer's) goes by its time, the same second counting
    as after (never a false 爬完). Passed when gates_ok is 1 and every gate's own status says pass (the two disagreeing
    is no pass) 【Claude 的执行办法】; gates_ok here is the row's, or yes where reconciled() reads past what no longer
    holds a site back (an older R5, menu links a person marked since)."""

    site: str
    batch: str
    at: float | None
    gates: dict
    gates_ok: bool
    cover_cc: float | None
    cover_old: float | None
    stale_by: Mark | None
    covers: dict = field(default_factory=dict)  # R3b, R4b: their numbers as reconcile wrote them
    uncompared: tuple[str, ...] = ()  # the gates that passed with nothing to compare
    menu_school: int = 0  # R1: menu items that are the school's broken links a person marked as the school's
    menu_bad: int = 0  # R1: the school's broken menu links nobody marked yet (⚠ 要看)

    @property
    def fresh(self) -> bool:
        return self.stale_by is None

    @property
    def failing(self) -> tuple[str, ...]:
        return tuple(g for g in GATES if self.gates.get(g) == FAIL)

    @property
    def passed(self) -> bool:
        return self.gates_ok and all(self.gates.get(g) == PASS for g in GATES)

    def not_passed(self) -> list[str]:
        out = []
        for g in GATES:
            if self.gates.get(g) == PASS:
                continue
            why = CHECK_ZH.get(self.gates.get(g), "没写")
            if g == "R1" and self.menu_bad:
                why += (f"：选单上 {self.menu_bad} 个学校的坏链接还没人看过（人看过、确认是学校那边的问题的，"
                        "nknu archive review --mark school_side --menu <网址> --note …）")
            out.append(f"{g}（{why}）")
        return out

    def menu_words(self) -> str:
        """R1's menu items a person marked as the school's, in words (the dashboard, verify, design §10.2.1)."""
        return (f"选单上 {self.menu_school} 个学校的坏链接：{outcome.RESULT_LABEL[outcome.SCHOOL_SIDE]}"
                if self.menu_school else "")

    def low_cover(self) -> list[str]:
        return [f"{name} 覆盖率估计 {v:.0%}（低于 {COVER_MIN:.0%}）" for name, v in
                (("Common Crawl", self.cover_cc), ("旧存档", self.cover_old)) if v is not None and v < COVER_MIN]

    def when(self) -> str:
        return f"{self.batch} 批，{_taipei(self.at)}"

    def cover_lines(self) -> list[str]:
        """The discovery coverage in words, each list it was worked out against (R3b, R4b; §18.2b): what it rests on,
        that it runs high, and what is missed at least."""
        out = []
        for gate, source in COVER_SOURCES:
            counts = self.covers.get(gate)
            value = self.cover_cc if gate == "R3b" else self.cover_old
            if counts is None and value is None:
                continue
            out.append(cover_words(source, counts if counts is not None else {"cover": value}))
        return out

    def blind_words(self) -> str:
        return "、".join(UNCOMPARED_ZH.get(g, g) for g in self.uncompared)

    def stale_words(self) -> str:
        """What made it stale, in words: 抓取（run_end，10-26 01:00）."""
        m = self.stale_by
        return "" if m is None else f"{STALE_ZH.get(m.code, m.code)}（{m.code}，{_taipei(m.at)}）"


def _r1_marked(f: Frontier, site: str, r1: Mapping) -> tuple[int, bool]:
    """Of the school's broken menu links a reconciliation's R1 listed, how many a person has marked since (O1, design
    §10.2.1: they count as the school's at once, and leave 「还没人看过」, without another reconciliation; review 10-26,
    O review low), and whether that is all of them, so that they no longer hold R1 back: never when R1 counted more
    than it kept, or has anything else to say."""
    bad = [i.get("target") for i in r1.get("items") or [] if isinstance(i, dict) and i.get("status") == "bad_link"]
    if not bad:
        return 0, False
    marks = menu_marks(f, site)
    n = sum(1 for t in bad if t in marks)
    whole = (n == len(bad) == _n(r1.get("bad_link")) and not _n(r1.get("missing"))
             and not _n(r1.get("waiting")))
    return n, whole


def reconciled(f: Frontier, rounds: Rounds) -> dict[str, Reconciled]:
    """Each site's last reconciliation as §10.5 reads it. Two things the row says no for no longer hold a site back
    (10-26, the owner's decisions): an older writer's R5 (taken out), and an R1 whose only ⚠ is the school's broken
    menu links a person has marked since (O1). A row that says no for any other reason, or for none it gives, stays
    no 【Claude 的执行办法】."""
    out = {}
    for r in f.q("SELECT site, batch, at, checks, cover_cc, cover_old, gates_ok FROM reconcile_site"):
        try:
            data = json.loads(r["checks"] or "{}")
        except (TypeError, ValueError):
            data = {}
        items = data.items() if isinstance(data, dict) else ()
        raw = {k: s for k, v in items if (s := _gate_status(v))}
        gates = dict(raw)
        r1 = data.get("R1") if isinstance(data, dict) and isinstance(data.get("R1"), dict) else {}
        since, whole = _r1_marked(f, r["site"], r1)
        menu_school, menu_bad = _n(r1.get("school_side")) + since, max(_n(r1.get("bad_link")) - since, 0)
        cured = whole and gates.get("R1") == WARN
        if cured:
            gates["R1"] = PASS
        ok = bool(r["gates_ok"])
        if not ok and all(gates.get(g) == PASS for g in GATES):
            ok = bool(cured) or any(raw.get(g) not in (None, PASS) for g in DROPPED_GATES)
        covers = {k: v for k, v in items if k in ("R3b", "R4b") and isinstance(v, dict)}
        blind = tuple(g for g in GATES if isinstance(data, dict) and isinstance(data.get(g), dict)
                      and gates.get(g) == PASS and data[g].get("compared") is False)
        at = _ts(r["at"])
        last = rounds.last(r["site"], *STALE_CODES)
        mark = rounds.last(r["site"], "reconcile")
        if last is None:
            stale = None
        elif mark is not None:
            stale = last if last.rowid > mark.rowid else None
        else:
            stale = last if at is None or last.at is None or last.at >= at else None
        out[r["site"]] = Reconciled(r["site"], r["batch"], at, gates, ok, r["cover_cc"], r["cover_old"], stale,
                                    covers, blind, menu_school, menu_bad)
    return out


# ---------------------------------------------------------------- one read of the result views

@dataclass(frozen=True)
class Page:
    """One row of page_completeness (design §10.4)."""

    id: int
    site: str | None
    tier: int
    content_id: int
    own: str
    level: str
    label: str
    reasons: tuple[str, ...]
    page_type: str | None = None
    page_role: str | None = None
    ended_by: str | None = None
    list_bad: bool = False
    list_unchecked: bool = False
    list_walking: bool = False
    frame_missing: bool = False

    @classmethod
    def of(cls, r) -> Page:
        """From a page_completeness row, or from the dashboard's subset of its columns (progress.slow_snapshot): what
        is not given is read off the reasons (list: 列表没翻完, list_unchecked) and ended_by."""
        d = dict(r)
        reasons = tuple(x for x in (d.get("reasons") or "").split(",") if x)
        if "frame_missing" in d:
            frame = d["frame_missing"]
        else:  # 缺内嵌页或按钮 with no resource missing: a frame type's page that names no frame
            frame = "miss_other" in d and "miss_other" in reasons and not d["miss_other"]
        return cls(d["id"], d.get("site"), d.get("tier"), d.get("content_id") or d["id"], d.get("own"), d.get("level"),
                   d.get("label"), reasons, d.get("page_type"), d.get("page_role"), d.get("ended_by"),
                   bool(d.get("list_bad", "list" in reasons)),
                   bool(d.get("list_unchecked", "list_unchecked" in reasons)),
                   bool(d.get("list_walking", d.get("ended_by") == "walking")), bool(frame))


@dataclass(frozen=True)
class Res:
    """One needed resource of a page row (view page_resource, its copies made one): what it rests on (res: u<url id>,
    a<action id>, or k<res_key> with no row), its result after a person's mark, and the row the entry rests on first."""

    holder: int
    res: str
    res_key: str
    role: str
    need: str
    result: str
    via: int | None


@dataclass
class Holder:
    """What one page row's own page_res comes to: each result counted once per resource, the resources a person must
    look at or that wait for the browser check or the lab (issues), and the school frame pages it holds (§10.4 rule 1:
    their resources count for it too)."""

    results: Counter = field(default_factory=Counter)
    issues: list = field(default_factory=list)
    frames: set = field(default_factory=set)


_NO_HOLDER = Holder()


@dataclass
class Facts:
    """page_completeness and page_resource read once (as costly as page_completeness: design §14.4 says no request
    handler reads it; the dashboard's background thread makes one every 2 minutes), with the events."""

    at: float
    pages: list[Page]
    holders: dict[int, Holder]
    rounds: Rounds
    # the sites one of whose round-1 pages has any page_res row (need no included: its scripts, its template images):
    # a site whose fetched pages have none has no resource layer yet (§10.5: 没检查, never green; the dashboard's rule)
    res_sites: set
    # each site's round-1 rows that are no page and that no page uses (Orphan), by site
    orphans: dict = field(default_factory=dict)

    def closure(self, holder: int) -> list[int]:
        """A page row and the school frame pages it holds, frames inside frames too, each once (§10.4 rule 1)."""
        out, todo, seen = [], [holder], {holder}
        while todo:
            h = todo.pop()
            out.append(h)
            for c in self.holders.get(h, _NO_HOLDER).frames:
                if c not in seen:
                    seen.add(c)
                    todo.append(c)
        return out


def facts(src, pages: Iterable | None = None) -> Facts:
    """Read the result views once. pages: page_completeness rows the caller has already (the dashboard's snapshot);
    page_resource is read here, grouped so that the copies of one resource are one (§10.4 rule 4: a person's mark on
    one copy holds for them all)."""
    f = _frontier(src)
    rows = pages if pages is not None else f.q("SELECT * FROM page_completeness")
    got = [Page.of(r) for r in rows]
    page_rows = {r[0] for r in f.q("SELECT id FROM url WHERE kind = 'page' AND tier IN (0, 1)")}
    holders: dict[int, Holder] = defaultdict(Holder)
    frames = "(" + ", ".join(f"'{r}'" for r in outcome.FRAME_ROLES) + ")"
    with f.lock:
        cur = f.conn.execute(f"""SELECT page_id, res, result, max(need = 'must') AS must, min(role) AS role,
                                   min(res_key) AS res_key, max(marked) AS marked, min(via) AS via,
                                   max(role IN {frames}) AS framed
                                 FROM page_resource GROUP BY page_id, res, result""")
        for r in cur:
            result = outcome.SCHOOL_SIDE if r["marked"] else r["result"]
            h = holders[r["page_id"]]
            h.results[result] += 1
            if tone(result) in (RED, PENDING) or result == "lab":
                h.issues.append(Res(r["page_id"], r["res"], r["res_key"], r["role"], "must" if r["must"] else "round2",
                                    result, r["via"]))
            if r["framed"] and r["res"].startswith("u") and r["res"][1:].isdigit() and int(r["res"][1:]) in page_rows:
                h.frames.add(int(r["res"][1:]))
    res_sites = {r[0] for r in f.q("""SELECT DISTINCT u.owner_site FROM url u WHERE u.kind = 'page' AND u.tier IN (0, 1)
                                        AND EXISTS (SELECT 1 FROM page_res pr WHERE pr.page_id = u.id)""")}
    return Facts(time.time(), got, dict(holders), Rounds(f), res_sites, orphans(f))


# ---------------------------------------------------------------- round-1 rows no page uses (review 10-26)

FOUND_VIA_ZH = {"commoncrawl": "Common Crawl", "old_frontier": "旧存档", "v1_known": "v1 的公告", "seed": "起点",
                "link": "链接", "redirect": "跳转", "scheme_switch": "改试 http/https", "reconcile": "对账补抓",
                "rejudge": "rejudge", "click": "按钮", "jscheck_redirect": "jscheck"}
# every row some page uses: a page_res entry's row (need no too: a school script, a template picture), a row a press
# led to, and onward where these redirect (url.final_url) or a Drive item's content is (drive_item.content_id)
_USED = """WITH RECURSIVE used(id) AS (
  SELECT res_id FROM page_res WHERE res_id IS NOT NULL
  UNION SELECT x.id FROM action a JOIN url x ON x.url = a.location WHERE a.location IS NOT NULL
  UNION SELECT t.id FROM used JOIN url r ON r.id = used.id JOIN url t ON t.url = r.final_url
        WHERE r.final_url IS NOT NULL AND r.final_url <> r.url
  UNION SELECT d.content_id FROM used JOIN drive_item d ON d.url_id = used.id WHERE d.content_id IS NOT NULL)"""


@dataclass(frozen=True)
class Orphan:
    """A round-1 row of a site that is no page and that no page uses (review 10-26, §13 item 17 「每个网站列出……的网址和
    按钮」): an attachment, a picture or a sitemap that came in as a start of its own (Common Crawl, the old archive,
    v1, robots.txt) and that no page links to. A page's resources are judged with the page (§10.4); such a row is
    judged by itself, by its own result, so that one the school refuses is 连接失败 here too. A row whose redirects land
    on another row is that row's (judged by itself in turn), but one that was to be a file and lands on a web page that
    came back is not_file (连接失败), as a page's attachment sent to its site's home page is (§10.4 rule 2; the owner's
    10-26 decision: never the web page's result in its place); one whose redirects land nowhere comes to what the last
    hop's link verdict says, as page_completeness has it 【Claude 的执行办法】. moved: it is a redirect (what it comes to
    is worked out here, so a person's mark on it is looked up here too: Frontier.mark_school_side); to: where it
    went."""

    id: int
    site: str
    url: str
    kind: str
    found_via: str | None
    result: str
    reason: str | None
    status: int | None
    moved: bool = False
    to: str | None = None

    @property
    def tone(self) -> str:
        return YELLOW if self.result == "lab" else _TONE.get(outcome.user_bucket(self.result), GOOD)

    def text(self) -> str:
        via = FOUND_VIA_ZH.get(self.found_via or "", self.found_via or "—")
        went = f"，跳到 {self.to}" if self.moved and self.to else ""
        return f"{self.kind}：{outcome.RESULT_LABEL.get(self.result, self.result)}（没有网页用到它：从{via}进来的{went}）"


def _landed_nowhere(f: Frontier, uid: int) -> str | None:
    """What a redirecting row comes to when its redirects land on no row (page_completeness's moved: a loop or too
    many hops is a server error; else the last hop's link verdict: a school error page's address (bad_link),
    out:login, out:<rule>, unhandled:<why>, or lost);
    None when they land on a row."""
    end, nowhere = landing(f, uid)
    return _nowhere(f, end, nowhere)


def _nowhere(f: Frontier, end, nowhere: str | None) -> str | None:
    if nowhere is None:
        return None
    if nowhere in ("跳转绕圈", "跳太多次"):
        return "server_error"
    to = end["final_url"] if end is not None else None
    got = f.q("SELECT verdict FROM link WHERE src=? AND dst=?", (end["id"], to)) if end is not None and to else []
    verdict = (got[0][0] or "") if got else ""
    if verdict == outcome.ERROR_OUT:  # a school error page's address (outcome._kept_out)
        return "bad_link"
    if verdict.startswith("out:login"):
        return "login"
    if verdict.startswith("out:"):
        return "rule"
    return "unhandled" if verdict.startswith("unhandled:") else "not_discovered"


def _expected_file(f: Frontier, uid: int) -> bool:
    """Whether a redirecting row, or any row its redirects pass through before they land, was to be a file (its kind0
    one of outcome._EXPECTED_FILE: an attachment, a picture, a Drive item, a sitemap…): page_completeness's exp of hop
    (§10.4 rule 2), over the same rows."""
    cur, seen = uid, set()
    for _ in range(outcome.MAX_HOPS):
        rows = f.q("SELECT id, final_url, coalesce(kind0, kind) AS k, result FROM url_result WHERE id=?", (cur,))
        if not rows or rows[0]["result"] != "redirect" or rows[0]["id"] in seen:
            return False
        r = rows[0]
        if r["k"] in outcome._EXPECTED_FILE:
            return True
        seen.add(r["id"])
        nxt = f.q("SELECT id FROM url WHERE url=?", (r["final_url"],)) if r["final_url"] else []
        if not nxt:
            return False
        cur = nxt[0][0]
    return False


def _redirect_alone(f: Frontier, uid: int) -> tuple[str | None, str | None]:
    """What a redirecting row no page uses comes to, before any person's mark, and where it went: what its redirects
    landing nowhere says (_nowhere); not_file when it was to be a file and lands on a web page that came back (the
    landing row's own result, a person's mark on it aside: page_completeness's rule for a page's attachment, §10.4
    rule 2, the owner's 10-26 decision); else None: it lands on a row, which is judged by itself."""
    end, nowhere = landing(f, uid)
    if nowhere is not None:
        return _nowhere(f, end, nowhere), (end["final_url"] if end is not None else None)
    if end is None:
        return None, None
    came = end["result"]
    if came == outcome.SCHOOL_SIDE:
        got = f.q("SELECT result FROM review_mark WHERE url_id=?", (end["id"],))
        came = got[0][0] if got else came
    kind = f.q("SELECT kind FROM url WHERE id=?", (end["id"],))
    if kind and kind[0][0] == "page" and came in outcome.PAGE_CAME_BACK and _expected_file(f, uid):
        return "not_file", end["url"]
    return None, end["url"]


def _marked_alone(f: Frontier, r, result: str) -> bool:
    """A person's mark on a redirecting row no page uses (review_mark, Frontier.mark_school_side): it holds while the
    row is as it was when marked (reason, status, fetched_at: a new fetch ends it) and still comes to what was marked,
    a result a person may mark (outcome.markable): the rule of a url row's own mark (outcome.url_result_case)."""
    return outcome.markable(result, r["reason"]) and bool(f.q(
        "SELECT 1 FROM review_mark WHERE url_id=? AND mark=? AND result=? AND reason IS ? AND status IS ? "
        "AND fetched_at IS ?", (r["id"], outcome.SCHOOL_SIDE, result, r["reason"], r["status"], r["fetched_at"])))


_ORPHAN_ROWS = f"""{_USED}
SELECT r.id, r.owner_site, r.url, r.kind, r.found_via, r.result, r.reason, r.status, r.fetched_at
FROM url_result r WHERE r.owner_site IS NOT NULL AND r.tier IN (0, 1) AND r.kind <> 'page' AND r.state <> 'later'
  AND r.id NOT IN (SELECT id FROM used WHERE id IS NOT NULL)"""


def orphans(src) -> dict[str, list[Orphan]]:
    """Each site's round-1 rows (tier 0, 1; never round two's, state later) that are no page and that no page uses
    (_USED), with what each came to."""
    f = _frontier(src)
    out: dict[str, list[Orphan]] = defaultdict(list)
    for r in f.q(f"{_ORPHAN_ROWS} ORDER BY r.owner_site, r.url"):
        result, moved, to = r["result"], r["result"] == "redirect", None
        if moved:
            result, to = _redirect_alone(f, r["id"])
            if result is None:
                continue
            if _marked_alone(f, r, result):
                result = outcome.SCHOOL_SIDE
        out[r["owner_site"]].append(Orphan(r["id"], r["owner_site"], r["url"], r["kind"], r["found_via"], result,
                                           r["reason"], r["status"], moved, to))
    return dict(out)


def orphan_result(src, uid: int) -> str | None:
    """What a redirecting round-1 row that no page uses comes to (orphans()), before any person's mark: what
    Frontier.mark_school_side marks it by (a page's resource is marked by page_resource instead). None for any other
    row, and for one whose redirects land on a row (judged by itself: mark that one)."""
    f = _frontier(src)
    rows = f.q(f"{_ORPHAN_ROWS} AND r.id = ?", (uid,))
    if not rows or rows[0]["result"] != "redirect":
        return None
    return _redirect_alone(f, uid)[0]


# ---------------------------------------------------------------- requests made on the school's network (B4)

@dataclass
class Campus:
    """Requests made on the school's network (request.campus 'on', review 10-26 B4: some pages show their full text on
    campus only), by the site they were made for, and those whose network nobody could tell (unknown, or not written
    by an older version: counted, never a warning)."""

    on: int = 0
    unknown: int = 0
    by_site: Counter = field(default_factory=Counter)
    first: float | None = None
    last: float | None = None

    def line(self) -> str:
        return (f"在校内网络抓的请求 {self.on} 个（{_taipei(self.first)} 到 {_taipei(self.last)}，台北）：有些页面校内看得到"
                "全文、校外只看到标题，这些网页存下的内容可能比校外看得到的多；照抓、照存，能不能对外提供要先问你"
                "（记录五·3；§6.10 B4「照抓、记下来、看板提醒」）")


def _request_site(scope_txt: str | None, owner: str | None, url: str) -> str:
    """The site a request was made for: its placement (admit:<verdict>:<owner>:<kind0>), else its row's owner."""
    parts = (scope_txt or "").split(":", 3)
    if len(parts) == 4 and parts[0] == "admit" and parts[2]:
        return parts[2]
    return owner or scope.site_key(url)


def campus_requests(src) -> Campus:
    f = _frontier(src)
    c = Campus()
    for r in f.q("""SELECT r.scope, r.url, r.started, u.owner_site FROM request r LEFT JOIN url u ON u.url = r.url
                    WHERE r.campus = 'on' AND r.purpose <> 'campus'"""):
        c.on += 1
        c.by_site[_request_site(r["scope"], r["owner_site"], r["url"])] += 1
        c.first = r["started"] if c.first is None else min(c.first, r["started"])
        c.last = r["started"] if c.last is None else max(c.last, r["started"])
    c.unknown = f.q("SELECT count(*) FROM request WHERE purpose <> 'campus' AND (campus IS NULL OR "
                    "campus NOT IN ('on', 'off'))")[0][0]
    return c


# ---------------------------------------------------------------- where a starting page leads (§10.5, §13 item 1)

def _result_row(f: Frontier, uid: int):
    rows = f.q("SELECT id, url, final_url, result, reason, status, owner_site FROM url_result WHERE id=?", (uid,))
    return rows[0] if rows else None


def find_row(f: Frontier, url: str):
    """The url row of an address as written, its canonical spelling, or the other scheme's (a scheme switch, §6.6)."""
    for u in dict.fromkeys(x for x in (url, scope.canonical(url)) if x):
        for v in (u, u.replace("https://", "http://", 1) if u.startswith("https://")
                  else u.replace("http://", "https://", 1)):
            rows = f.q("SELECT id, url FROM url WHERE url=?", (v,))
            if rows:
                return rows[0]
    return None


def landing(f: Frontier, uid: int) -> tuple[object | None, str | None]:
    """Where a row's redirects (3xx, a script or a meta refresh, a scheme switch: url_result 'redirect') land, hop by
    hop through url.final_url as page_completeness follows them: (that row, None), or (the last row, why it lands
    nowhere)."""
    cur, seen = _result_row(f, uid), set()
    for _ in range(outcome.MAX_HOPS):
        if cur is None or cur["result"] != "redirect":
            return cur, None
        seen.add(cur["id"])
        to = cur["final_url"]
        if not to or to == cur["url"]:
            return cur, "跳转目标不明"
        nxt = f.q("SELECT id FROM url WHERE url=?", (to,))
        if not nxt:
            v = f.q("SELECT verdict FROM link WHERE src=? AND dst=?", (cur["id"], to))
            return cur, f"跳到 {to}（{v[0][0] if v else '不在待抓清单'}）"
        if nxt[0][0] in seen:
            return cur, "跳转绕圈"
        cur = _result_row(f, nxt[0][0])
    return cur, "跳太多次"


def _why(r) -> str:
    bits = [x for x in (r["reason"], f"HTTP {r['status']}" if r["status"] else None) if x]
    return f"（{'，'.join(bits)}）" if bits else ""


def home_state(f: Frontier, uid: int) -> tuple[str, str | None]:
    """ok (a page came back where the start leads), wait, lab, or bad, and why: one that ends at a sign-in, off the
    list, at an error, at an empty page or nowhere has crawled nothing (§10.5, §13 item 1)."""
    end, nowhere = landing(f, uid)
    if end is None:
        return "bad", "没有纪录"
    if nowhere:
        return "bad", nowhere
    via = f"（经跳转到 {end['url']}）" if end["id"] != uid else ""
    res = end["result"]
    if res in ("todo", "retry"):
        return "wait", f"还没抓到内容{via}"
    if res == "lab":
        return "lab", f"只限校内网络或查不到主机，等搬到实验室{via}"
    if res in _HOME_GOT or res == outcome.SCHOOL_SIDE:
        return "ok", None
    return "bad", f"{outcome.RESULT_LABEL.get(res, res)}{_why(end)}{via}"


def home_states(src) -> dict[str, list[tuple[str, str, str | None]]]:
    """Each round-1 site's starting pages and how far each got: the list's home (site_plan) and every seed_src row the
    site's own seeding wrote (site_home: a merged key has several). A home with no row at all is bad."""
    f = _frontier(src)
    out: dict[str, list] = defaultdict(list)
    seen: set[str] = set()
    for r in f.q("SELECT site, home FROM site_plan WHERE round = '1' AND home IS NOT NULL ORDER BY site"):
        row = find_row(f, r["home"])
        if row is None:
            out[r["site"]].append((r["home"], "bad", "名单上的首页没有纪录（还没 seed？）"))
            continue
        seen.add(row["url"])
        out[r["site"]].append((row["url"], *home_state(f, row["id"])))
    for r in f.q("""SELECT s.url, u.id, u.owner_site FROM seed_src s JOIN url u ON u.url = s.url
                    WHERE s.source = 'site_home' ORDER BY s.url"""):
        if r["url"] not in seen:
            seen.add(r["url"])
            out[r["owner_site"] or scope.site_key(r["url"])].append((r["url"], *home_state(f, r["id"])))
    return dict(out)


# ---------------------------------------------------------------- each site's state (§10.5)

@dataclass
class Issue:
    """One entry of a site's problem list (§13 item 17; the dashboard's site detail reads the same list): a page by
    its completeness, or a URL, a press or an entry with no row by its result, with its reason and status."""

    bucket: str  # failed, review, pending_check (outcome.BUCKETS)
    what: str  # page, url, press, entry
    ref: str  # p<page id>, u<url id>, a<action id>, k<res_key>
    url: str
    result: str
    text: str
    reason: str | None = None
    status: int | None = None
    pages: int = 1

    @property
    def lab(self) -> bool:
        return self.result == "lab"

    @property
    def red(self) -> bool:
        return self.bucket in ("failed", "review") and not self.lab

    def line(self) -> str:
        bits = [x for x in (f"原因 {self.reason}" if self.reason else None,
                            f"HTTP {self.status}" if self.status else None,
                            f"{self.pages} 页用到" if self.what != "page" and self.pages > 1 else None) if x]
        return f"[{BUCKET_ZH.get(self.bucket, self.bucket)}] {self.url}：{self.text}" + (f"（{'，'.join(bits)}）"
                                                                                         if bits else "")


@dataclass
class SiteStatus:
    """A round-1 site's state for the user (§10.5): state (ERROR … DONE, NOT_CHECKED), why (the reasons for it, in
    words), warnings (beside the state, never changing it: requests made on campus, a coverage estimate below its
    threshold), its pages by level, its pages and resources by the user's ten categories (a resource once per page row
    that uses it), and its problem list. Reads as a mapping too (state, why, cover_cc, cover_old, …), which is what
    the dashboard's progress._norm_status takes."""

    site: str
    name: str
    tier: int | None
    state: str
    why: list[str]
    warnings: list[str]
    pages: int
    levels: Counter
    buckets: Counter
    issues: list[Issue]
    reconcile: Reconciled | None
    jscheck_done: bool

    @property
    def label(self) -> str:
        return SITE_STATE[self.state][0]

    @property
    def color(self) -> str:
        return SITE_STATE[self.state][1]

    def as_dict(self) -> dict:
        rec = self.reconcile
        return {"site": self.site, "name": self.name, "tier": self.tier, "state": self.state, "label": self.label,
                "color": self.color, "why": list(self.why), "warnings": list(self.warnings), "pages": self.pages,
                "levels": dict(self.levels), "buckets": dict(self.buckets),
                "issues": [i.line() for i in self.issues], "cover_cc": rec.cover_cc if rec else None,
                "cover_old": rec.cover_old if rec else None, "cover_line": "；".join(rec.cover_lines()) if rec else "",
                "jscheck_done": self.jscheck_done}

    def keys(self):
        return self.as_dict().keys()

    def __getitem__(self, key: str):
        return self.as_dict()[key]

    def line(self) -> str:
        return f"{self.label}：{'；'.join(self.why) or '—'}" + (f"（另：{'；'.join(self.warnings)}）"
                                                                if self.warnings else "")


def _rows_by_id(f: Frontier, table: str, cols: str, ids: Iterable[int]) -> dict[int, sqlite3.Row]:
    ids, out = sorted(set(ids)), {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in f.q(f"SELECT {cols} FROM {table} WHERE id IN ({','.join('?' * len(part))})", part):
            out[r["id"]] = r
    return out


def site_status(src, now: float | None = None, *, pages: Iterable | None = None,
                fx: Facts | None = None) -> dict[str, SiteStatus]:
    """Every round-1 site's state (design §10.5), most important and worst first: the sites of the list (site_plan,
    round 1) and any other a round-1 row counts for. pages: page_completeness rows the caller has already (the
    dashboard's snapshot); fx: a whole facts() read. Only round 1 counts: the site's round-1 pages and what they need
    (need must or round2), and its round-1 rows that are no page and that no page uses, each by its own result
    (Orphan: a Common Crawl attachment the school refuses is 连接失败 too, review 10-26); its own round-two rows (state
    later) never do."""
    f = _frontier(src)
    fx = fx if fx is not None else facts(f, pages)
    plans = {r["site"]: r for r in f.q("SELECT site, name, imp, base_urls FROM site_plan WHERE round = '1'")}
    by_site: dict[str, list[Page]] = defaultdict(list)
    for p in fx.pages:
        by_site[p.site or ""].append(p)
    rows = {r[0]: (r[1], r[2] or 0) for r in f.q("""SELECT owner_site, count(*), sum(state IN ('pending', 'active'))
                                                    FROM url WHERE owner_site IS NOT NULL GROUP BY owner_site""")}
    presses = dict(f.q("""SELECT u.owner_site, count(*) FROM action a JOIN url u ON u.id = a.list_id
                          WHERE a.state IN ('pending', 'active') GROUP BY u.owner_site"""))
    types = {r[0]: (r[1] or 0) for r in f.q("""SELECT site, sum(verdict = 'todo') FROM page_type WHERE n_pages > 0
                                               GROUP BY site""")}
    campus = campus_requests(f)
    homes = home_states(f)
    recs = reconciled(f, fx.rounds)
    from nknu.archive.round1 import TIER_OF_IMP

    keys = set(plans) | {s for s in by_site if s} | ({""} if by_site.get("") else set())
    work: dict[str, dict] = {}
    for s in keys:
        work[s] = _judge_site(s, by_site.get(s, []), fx, plans.get(s), rows.get(s, (0, 0)), presses.get(s, 0),
                              types.get(s, 0), homes.get(s, []), recs.get(s), campus.by_site.get(s, 0))
    issues = _issues(f, fx, work)
    out = []
    for s, w in work.items():
        plan = plans.get(s)
        tier = TIER_OF_IMP.get(plan["imp"]) if plan is not None else (min(p.tier for p in by_site[s])
                                                                       if by_site.get(s) else None)
        name = (plan["name"] if plan is not None else "") or ("（没有 owner_site 的网页）" if s == "" else "")
        out.append(SiteStatus(s, name, tier, w["state"], w["why"], w["warnings"], len(by_site.get(s, [])),
                              w["levels"], w["buckets"], issues.get(s, []), recs.get(s), w["js_done"]))
    out.sort(key=lambda x: (9 if x.tier is None else x.tier, SITE_ORDER.index(x.state), x.site))
    return {x.site: x for x in out}


def _judge_site(s: str, pages: list[Page], fx: Facts, plan, rows: tuple[int, int], presses: int, todo_types: int,
                homes: list, rec: Reconciled | None, campus_on: int) -> dict:
    """§10.5 for one site, in its order: the first of ✗ 出错, ⚠ 要看, 还在抓, ⚠ 纪录齐了，还没对账, ✓ 爬完."""
    js_done = fx.rounds.jscheck_done(s)
    red, yellow, wait, warnings = [], [], [], []
    levels, buckets, tally = Counter(), Counter(), Counter()
    page_bad: list[tuple[Page, str]] = []  # (page, bucket) for the problem list
    res_seen: dict[str, Res] = {}
    res_pages: Counter = Counter()
    flags = Counter()
    holders_seen: set[int] = set()
    for p in pages:
        levels[p.level] += 1
        own_b = bucket(p.own, page=True)
        buckets[own_b] += 1
        tones = {tone(p.own, page=True)}
        tally[p.own if p.own == "lab" else own_b] += 1
        if tones & {RED, PENDING} or p.own == "lab":
            page_bad.append((p, own_b))
        for h in fx.closure(p.content_id):
            holder = fx.holders.get(h, _NO_HOLDER)
            for result, n in holder.results.items():
                tones.add(tone(result))
                if h not in holders_seen:
                    buckets[bucket(result)] += n
                    tally[result if result == "lab" else bucket(result)] += n
            holders_seen.add(h)
            for res in holder.issues:
                res_seen.setdefault(res.res, res)
                res_pages[res.res] += 1
        if p.list_bad:
            tones.add(RED)
            flags["list_bad"] += 1
            page_bad.append((p, "review"))
        if p.frame_missing:
            tones.add(RED)
            flags["frame_missing"] += 1
            page_bad.append((p, "review"))
        if p.list_unchecked:
            tones.add(YELLOW)
            flags["list_unchecked"] += 1
        if p.list_walking or p.level == "waiting":
            tones.add(WAIT)
        if p.level not in ("complete", "complete_school", "waiting") and tones <= {GOOD}:
            flags["unexplained"] += 1
            page_bad.append((p, "review"))
    # the round-1 rows no page uses (review 10-26): judged by their own result, each once
    lone = fx.orphans.get(s, [])
    for o in lone:
        b = outcome.user_bucket(o.result)
        buckets[b] += 1
        tally[o.result if o.result == "lab" else b] += 1
    lone_red = [o for o in lone if o.tone == RED]
    # what the problem list holds, counted once each (pages by their own result, resources once per site)
    own_red = [p for p in pages if tone(p.own, page=True) == RED]
    res_red = [r for r in res_seen.values() if tone(r.result) == RED]
    n_failed = (sum(bucket(p.own, True) == "failed" for p in own_red) + sum(bucket(r.result) == "failed" for r in res_red)
                + sum(outcome.user_bucket(o.result) == "failed" for o in lone_red))
    n_review = len(own_red) + len(res_red) + len(lone_red) - n_failed
    n_pending = (sum(tone(p.own, True) == PENDING for p in pages) + sum(tone(r.result) == PENDING
                                                                          for r in res_seen.values())
                 + sum(o.tone == PENDING for o in lone))
    n_lab = (sum(p.own == "lab" for p in pages) + sum(r.result == "lab" for r in res_seen.values())
             + sum(o.result == "lab" for o in lone))
    if s == "":
        red.append(f"{len(pages)} 个网页不属于任何网站（owner_site 是空的：爬虫的 bug）")
    if n_failed:
        red.append(f"{n_failed} 个网页或资源连接失败")
    if n_review:
        red.append(f"{n_review} 个网页或资源要人看")
    if flags["list_bad"]:
        red.append(f"{flags['list_bad']} 个列表没翻完（对不上、按钮失败或翻到上限）")
    if flags["frame_missing"]:
        red.append(f"{flags['frame_missing']} 个框架类型的网页一个框架页都没有")
    if flags["unexplained"]:
        red.append(f"{flags['unexplained']} 个网页不完整、这里的规则说不出原因（要人看）")
    if n_pending:
        (red if js_done else yellow).append(
            f"{n_pending} 个待判定" + ("（这一轮的 jscheck 已经跑完）" if js_done else "（这一轮的 jscheck 还没跑完）"))
    if todo_types:
        (red if js_done else yellow).append(f"{todo_types} 类有网页的类型还没判定" +
                                            ("（jscheck 已经跑完）" if js_done else "（jscheck 还没跑完）"))
    for url, state, why in homes:
        if state == "bad":
            red.append(f"首页没抓到内容：{url}（{why}）")
        elif state == "lab":
            yellow.append(f"首页{why}：{url}")
        elif state == "wait":
            wait.append(f"首页还没抓到：{url}")
    if plan is not None and not homes:
        red.append("名单上没有首页纪录")
    n_rows, n_waiting = rows
    base = plan["base_urls"] if plan is not None else None
    if base and base >= SHRINK_MIN_BASE and not n_waiting and not presses and n_rows < base * SHRINK:
        red.append(f"已经没有待抓，总数 {n_rows} 个却比旧存档的 {base} 个少 {1 - n_rows / base:.0%}")
    if rec is not None and rec.failing:
        red.append(f"最新一次对账（{rec.when()}{'，已过期' if not rec.fresh else ''}）有没过的关卡："
                   + "、".join(rec.failing))
    if n_lab:
        yellow.append(f"{n_lab} 个只限校内网络或查不到主机（等搬到实验室）")
    if tally["next_round"]:
        yellow.append(f"{tally['next_round']} 个下一轮补（要 JavaScript、点了才载入、校外资源、平台档案标 round2 的写法）")
    if flags["list_unchecked"]:
        yellow.append(f"{flags['list_unchecked']} 个列表没核对（没有翻页纪录）")
    if levels["waiting"]:
        wait.append(f"{levels['waiting']} 个网页还没抓完")
    if tally["queued"]:
        wait.append(f"{tally['queued']} 个资源还在排队或等重试")
    if n_waiting:
        wait.append(f"{n_waiting} 个网址还在待抓清单里")
    if presses:
        wait.append(f"{presses} 个按钮还没按")
    if campus_on:
        warnings.append(f"有 {campus_on} 个请求是在校内网络抓的（内容可能比校外看得到的多，对外提供前要先问你）")
    if rec is not None:
        warnings += rec.low_cover()
        if rec.uncompared:
            warnings.append(f"对账有几项没比到（不等于没问题）：{rec.blind_words()}")
        if rec.menu_school:
            warnings.append(rec.menu_words())
    if red:
        state, why = ERROR, red
    elif s not in fx.res_sites and any(p.level != "waiting" for p in pages):
        state, why = NOT_CHECKED, ["这个网站抓到的网页都还没有资源纪录（资源层没资料）：没检查，不是绿色", *yellow]
    elif yellow:
        state, why = LOOK, yellow
    elif wait:
        state, why = CRAWLING, wait
    elif rec is None:
        state, why = UNRECONCILED, ["这个网站还没对账（nknu archive reconcile，第 18.2b 节）"]
    elif not rec.fresh:
        state, why = UNRECONCILED, [f"对账（{rec.when()}）比之后涵盖这个网站的{rec.stale_words()}旧，要重对"]
    elif not rec.passed:
        state, why = UNRECONCILED, [f"对账（{rec.when()}）还有关卡没过：" + "、".join(rec.not_passed())]
    else:
        state, why = DONE, [f"每一页都完整，对账全过（{rec.when()}）"]
    return {"state": state, "why": why, "warnings": warnings, "levels": levels, "buckets": buckets,
            "js_done": js_done, "page_bad": page_bad, "res": res_seen, "res_pages": res_pages,
            "lone": [o for o in lone if o.tone in (RED, PENDING) or o.result == "lab"]}


def _issues(f: Frontier, fx: Facts, work: dict[str, dict]) -> dict[str, list[Issue]]:
    """Each site's problem list in words: its pages (by their completeness), the resources they need and its round-1
    rows no page uses (Orphan), in the user's categories 连接失败, 要人看 and 待判定 (lab within 连接失败), pages
    first."""
    page_ids = {p.id for w in work.values() for p, _b in w["page_bad"]}
    url_ids = set(page_ids)
    act_ids = set()
    for w in work.values():
        for r in w["res"].values():
            if r.res.startswith("u") and r.res[1:].isdigit():
                url_ids.add(int(r.res[1:]))
            elif r.res.startswith("a") and r.res[1:].isdigit():
                act_ids.add(int(r.res[1:]))
            if r.via is not None:
                url_ids.add(r.via)
    urls = _rows_by_id(f, "url", "id, url, reason, status, page_type", url_ids)
    acts = _rows_by_id(f, "action", "id, list_id, kind, target, argument, reason, status", act_ids)
    lists = _rows_by_id(f, "url", "id, url", {a["list_id"] for a in acts.values()})
    type_keys = sorted({urls[i]["page_type"] for i in page_ids if i in urls and urls[i]["page_type"]})
    type_why = {}
    for i in range(0, len(type_keys), 500):
        part = type_keys[i:i + 500]
        type_why.update({r[0]: r[1] for r in f.q(f"SELECT key, why FROM page_type WHERE key IN "
                                                 f"({','.join('?' * len(part))})", part)})
    out: dict[str, list[Issue]] = {}
    for s, w in work.items():
        got: list[Issue] = []
        done_pages: set[int] = set()
        for p, b in w["page_bad"]:
            if p.id in done_pages:
                continue
            done_pages.add(p.id)
            u = urls.get(p.id)
            text = f"{outcome.LEVEL_LABEL.get(p.level, p.level)}：{LABEL_ZH.get(p.label, p.label)}"
            if p.reasons:
                text += "（" + "、".join(LABEL_ZH.get(x, x) for x in p.reasons) + "）"
            if p.own != "ok":
                text += f"；网页本身：{outcome.RESULT_LABEL.get(p.own, p.own)}"
            if p.list_bad:
                text += f"；列表结束于 {p.ended_by or '（没写）'}"
            if tone(p.own, True) == PENDING and u is not None:
                text += f"；类型 {u['page_type'] or '（没有）'}（{type_why.get(u['page_type']) or '没写原因'}）"
            bkt = b if b in ("failed", "review", "pending_check") else "review"
            got.append(Issue(bkt, "page", f"p{p.id}", u["url"] if u else f"#{p.id}", p.own, text,
                             u["reason"] if u else None, u["status"] if u else None))
        for r in w["res"].values():
            label = outcome.RESULT_LABEL.get(r.result, r.result)
            if r.res.startswith("u") and r.res[1:].isdigit() and int(r.res[1:]) in urls:
                u = urls[int(r.res[1:])]
                via = urls.get(r.via) if r.via is not None and r.via != u["id"] else None
                text = label + (f"（经 {via['url']}）" if via is not None else "")
                got.append(Issue(bucket(r.result), "url", r.res, u["url"], r.result, f"{r.role}：{text}", u["reason"],
                                 u["status"], w["res_pages"][r.res]))
            elif r.res.startswith("a") and r.res[1:].isdigit() and int(r.res[1:]) in acts:
                a = acts[int(r.res[1:])]
                page = lists.get(a["list_id"])
                arg = f"|{a['argument']}" if a["argument"] else ""
                got.append(Issue(bucket(r.result), "press", r.res, page["url"] if page else f"#{a['list_id']}",
                                 r.result, f"按钮 {a['kind']} {a['target']}{arg}：{label}", a["reason"], a["status"],
                                 w["res_pages"][r.res]))
            else:
                why = f.q("SELECT why FROM page_res WHERE page_id=? AND res_key=?", (r.holder, r.res_key))
                got.append(Issue(bucket(r.result), "entry", r.res, r.res_key, r.result, f"{r.role}：{label}",
                                 why[0][0] if why else None, None, w["res_pages"][r.res]))
        for o in w.get("lone", ()):
            got.append(Issue(outcome.user_bucket(o.result), "url", f"u{o.id}", o.url, o.result, o.text(), o.reason,
                             o.status, 0))
        order = {"failed": 0, "review": 1, "pending_check": 2}
        got.sort(key=lambda i: (order.get(i.bucket, 3), i.what != "page", i.url))
        out[s] = got
    return out


# ---------------------------------------------------------------- the checks (§13)

@dataclass
class Check:
    """One item of §13: no (1–18; 11b the scope audit, 11c requests made on campus, G the 25 questions), its status,
    a one-line detail and the lines that say what is wrong. Read by the dashboard as progress._norm_check reads it
    (no, title, status, detail)."""

    no: str
    title: str
    status: str = PASS
    detail: str = ""
    lines: list[str] = field(default_factory=list)

    def add(self, status: str, line: str | None = None) -> None:
        if _RANK[status] > _RANK[self.status]:
            self.status = status
        if line is not None:
            self.lines.append(line)

    @property
    def result(self) -> str:
        return self.status


@dataclass
class _Ctx:
    f: Frontier
    root: Path | None
    plan: object | None
    cheap: bool
    fx: Facts | None
    statuses: dict[str, SiteStatus] | None
    rounds: Rounds
    alive: bool
    wayback_open: bool
    resource_check: Callable | None
    pages: Iterable | None = None

    def facts(self) -> Facts | None:
        """The result views read once, when an item needs them: in the dashboard's cheap round only when it gave its
        snapshot's pages (otherwise that item is 没检查)."""
        if self.fx is None and (not self.cheap or self.pages is not None):
            self.fx = facts(self.f, self.pages)
        return self.fx

    def sites(self) -> dict[str, SiteStatus] | None:
        if self.statuses is None and (fx := self.facts()) is not None:
            self.statuses = site_status(self.f, fx=fx)
        return self.statuses

    def round1_sites(self) -> list[str]:
        return [r[0] for r in self.f.q("SELECT site FROM site_plan WHERE round = '1' ORDER BY site")]


def _cap(lines: list[str], n: int = REPORT_LINES) -> list[str]:
    return lines if len(lines) <= n else [*lines[:n], f"……另有 {len(lines) - n} 条"]


def _c1(ctx: _Ctx, c: Check) -> None:
    f = ctx.f
    n = Counter()
    for site, entries in sorted(home_states(f).items()):
        for url, state, why in entries:
            n[state] += 1
            if state == "bad":
                c.add(FAIL, f"{site}：{url}（{why}）")
            elif state in ("wait", "lab"):
                c.add(WARN, f"{site}：{url}（{why}）")
    for r in f.q("SELECT s.url, u.id, u.owner_site FROM seed_src s LEFT JOIN url u ON u.url = s.url "
                 "WHERE s.source = 'extra' ORDER BY s.url"):
        state, why = home_state(f, r["id"]) if r["id"] is not None else ("bad", "没有纪录")
        n[state] += 1
        if state == "bad":
            c.add(FAIL, f"附加起点 {r['url']}（{why}）")
        elif state in ("wait", "lab"):
            c.add(WARN, f"附加起点 {r['url']}（{why}）")
    total = sum(n.values())
    if not total:
        c.add(SKIP, "没有起点纪录（还没 seed？）")
    c.detail = f"起点 {total} 个：抓到内容 {n['ok']}，还在等 {n['wait']}，只限校内 {n['lab']}，有问题 {n['bad']}"


def _c2(ctx: _Ctx, c: Check) -> None:
    f = ctx.f
    with f.lock:
        got = outcome.self_check(f.conn)
    c.detail = (f"程序不认得的组合：网址 {got['url_result']}、按钮 {got['action_result']}；"
                f"不属于任何网站的第一轮网址 {got['no_owner_site']}")
    for r in f.q("""SELECT url, state, reason, status, flags, kind, kind0, sniff FROM url_result WHERE result = 'unknown'
                    ORDER BY id LIMIT 50"""):
        c.add(FAIL, f"{r['url']}：state={r['state']} reason={r['reason']} status={r['status']} flags={r['flags']} "
                    f"kind={r['kind']}/{r['kind0']} sniff={r['sniff']}")
    for r in f.q("""SELECT a.id, a.state, a.kind, a.status, a.sniff, u.url FROM action_result a
                    LEFT JOIN url u ON u.id = a.list_id WHERE a.result = 'unknown' ORDER BY a.id LIMIT 50"""):
        c.add(FAIL, f"按钮 a{r['id']}（{r['url']}）：state={r['state']} kind={r['kind']} status={r['status']} "
                    f"sniff={r['sniff']}")
    for r in f.q("SELECT url FROM url WHERE owner_site IS NULL AND tier IN (0, 1) ORDER BY id LIMIT 50"):
        c.add(FAIL, f"{r['url']}：没有 owner_site")
    if any(got.values()):
        c.add(FAIL)


def _c3(ctx: _Ctx, c: Check) -> None:
    rows = ctx.f.q("""SELECT p.site, p.base_urls,
                        (SELECT count(*) FROM url u WHERE u.owner_site = p.site) AS n,
                        (SELECT count(*) FROM url u WHERE u.owner_site = p.site AND u.state IN ('pending', 'active'))
                          AS w
                      FROM site_plan p WHERE p.round = '1' AND p.base_urls >= ? ORDER BY p.site""", (SHRINK_MIN_BASE,))
    if not rows and not ctx.f.q("SELECT 1 FROM site_plan WHERE round = '1' AND base_urls IS NOT NULL LIMIT 1"):
        c.add(SKIP, "旧存档没有带过来（site_plan.base_urls 是空的）：比不了")
    short = 0
    for r in rows:
        if r["n"] < r["base_urls"] * SHRINK:
            short += 1
            if r["w"]:
                c.add(WARN, f"{r['site']}：现在 {r['n']} 个，旧存档 {r['base_urls']} 个（还有 {r['w']} 个在待抓，抓完再比）")
            else:
                c.add(FAIL, f"{r['site']}：现在 {r['n']} 个，旧存档 {r['base_urls']} 个，少了 {1 - r['n'] / r['base_urls']:.0%}")
    c.detail = (f"比了 {len(rows)} 个网站（旧存档至少 {SHRINK_MIN_BASE} 个网址的），少 20% 以上的 {short} 个；"
                "「起点不少于旧的」由 seed 的起点报告管（少了就退出码 2）")


def _c4(ctx: _Ctx, c: Check) -> None:
    rows = ctx.f.q("""SELECT lw.list_id, lw.ended_by, lw.ok, lw.pages, lw.rows, lw.claimed, lw.note, u.url, u.owner_site
                      FROM listwalk lw JOIN url u ON u.id = lw.list_id WHERE u.tier IN (0, 1)
                      ORDER BY u.owner_site, u.url""")
    lists = ctx.f.q("SELECT count(*) FROM url WHERE kind = 'page' AND tier IN (0, 1) AND page_role = 'list'")[0][0]
    if not rows and lists:
        c.add(SKIP, f"有 {lists} 个列表页，还没有任何翻页纪录")
    n = Counter()
    for r in rows:
        if r["ended_by"] == "walking":
            n["walking"] += 1
            c.add(WARN, f"{r['owner_site']}：{r['url']} 还在翻（已翻 {r['pages'] or 0} 页）")
        elif r["ended_by"] in outcome.LIST_ENDS_OK and r["ok"] != 0:
            n["ok"] += 1
        else:
            n["bad"] += 1
            claimed = f"，页面写共 {r['claimed']} 筆" if r["claimed"] is not None else ""
            c.add(FAIL, f"{r['owner_site']}：{r['url']} 结束于 {r['ended_by'] or '（没写）'}（{r['pages'] or 0} 页、"
                        f"{r['rows'] or 0} 行{claimed}；{r['note'] or ''}）")
    c.detail = f"列表 {len(rows)} 个：翻到底 {n['ok']}，还在翻 {n['walking']}，没翻完 {n['bad']}（SSO 公告中心各类加总对不上的记 mismatch:total）"


def _c5(ctx: _Ctx, c: Check) -> None:
    fx = ctx.facts()
    if fx is None:
        c.add(SKIP, "完整度还没算好（看板背景每 2 分钟算一次）")
        return
    by_site: dict[str, list[Page]] = defaultdict(list)
    for p in fx.pages:
        if p.page_role == "list":
            by_site[p.site or ""].append(p)
    unchecked = 0
    for s, ps in sorted(by_site.items()):
        bad = [p for p in ps if p.list_unchecked]
        unchecked += len(bad)
        if bad and len(bad) == len(ps):
            c.add(SKIP, f"{s}：{len(ps)} 个列表页都还没有翻页纪录（这个网站这一项没检查）")
        elif bad:
            urls = _rows_by_id(ctx.f, "url", "id, url", [p.id for p in bad])
            for p in bad:
                c.add(WARN, f"{s}：{urls[p.id]['url'] if p.id in urls else p.id} 列表没核对")
    c.detail = f"列表页 {sum(len(v) for v in by_site.values())} 个，没有翻页纪录的 {unchecked} 个"


def _c6(ctx: _Ctx, c: Check) -> None:
    f = ctx.f
    n = Counter()
    seen: set[int] = set()
    for r in f.q("""SELECT id, url, owner_site, result, flags, reason, status FROM url_result
                    WHERE tier IN (0, 1) AND result IN ('thin', 'looks_gone', 'not_file') ORDER BY owner_site, url"""):
        n[r["result"]] += 1
        seen.add(r["id"])
        c.add(FAIL, f"{r['owner_site']}：{r['url']}：{outcome.RESULT_LABEL[r['result']]}（flags {r['flags'] or '—'}）")
    for r in f.q("""SELECT a.id, a.kind, a.target, a.result, u.url, u.owner_site FROM action_result a
                    JOIN url u ON u.id = a.list_id WHERE a.result IN ('thin', 'not_file') ORDER BY u.owner_site, a.id"""):
        n[r["result"]] += 1
        c.add(FAIL, f"{r['owner_site']}：{r['url']} 上的按钮 {r['kind']} {r['target']}："
                    f"{outcome.RESULT_LABEL[r['result']]}")
    fx = ctx.facts()
    if fx is None:
        c.add(SKIP, "跳转后落在网页上的附件没算（完整度还没算好）")
    else:
        moved = {}
        for h in fx.holders.values():
            for r in h.issues:
                if r.result == "not_file" and r.via is not None and r.via not in seen:
                    moved.setdefault(r.via, r)
        urls = _rows_by_id(f, "url", "id, url, owner_site", moved)
        for via in sorted(moved, key=lambda i: urls[i]["url"] if i in urls else ""):
            if via in urls:
                n["not_file"] += 1
                c.add(FAIL, f"{urls[via]['owner_site']}：{urls[via]['url']}：应该是档案，跳到了网页或规则不抓的地方")
    reconciled_sites = {r[0] for r in f.q("SELECT site FROM reconcile_site")}
    missing = [s for s in ctx.round1_sites() if s not in reconciled_sites]
    if missing:
        c.add(SKIP, f"「页面几乎是空的」每批对账时才判（reconcile 写 thin 记号），这 {len(missing)} 个网站还没对账，"
                    f"这一部分没检查：{'、'.join(missing[:30])}{'……' if len(missing) > 30 else ''}")
    c.detail = (f"页面几乎是空的 {n['thin']}、看起来像不存在 {n['looks_gone']}、应该是档案却回来网页 {n['not_file']}；"
                f"还没对账、空页没判的网站 {len(missing)} 个")


def _c7(ctx: _Ctx, c: Check) -> None:
    f, rounds = ctx.f, ctx.rounds
    todo = defaultdict(list)
    for r in f.q("""SELECT key, site, verdict, why, n_pages, note FROM page_type WHERE n_pages > 0
                    AND verdict IN ('todo', 'unresolved') ORDER BY site, key"""):
        if r["verdict"] == "unresolved":
            c.add(FAIL, f"{r['site']}：类型 {r['key']} 用浏览器也判定不了（{r['why'] or r['note'] or '没写原因'}，"
                        f"{r['n_pages']} 页）：人看过用 jscheck --set 改判")
        else:
            todo[r["site"]].append(r)
    unchecked = dict(f.q("""SELECT owner_site, count(*) FROM url_result WHERE kind = 'page' AND tier IN (0, 1)
                            AND result = 'unchecked' GROUP BY owner_site"""))
    n_types, n_pages = 0, 0
    for s in sorted(set(todo) | set(unchecked)):
        types, pages = todo.get(s, []), unchecked.get(s, 0)
        n_types += len(types)
        n_pages += pages
        done = rounds.jscheck_done(s or "")
        what = f"{s}：还没判定的类型 {len(types)} 类、待判定的网页 {pages} 页"
        if types:
            what += "（" + "、".join(f"{t['key']}（{t['why'] or '没写原因'}）" for t in types[:5]) + \
                    ("……" if len(types) > 5 else "") + "）"
        c.add(FAIL if done else WARN, what + ("：这一轮的 jscheck 已经跑完，还剩着" if done else "：jscheck 还没跑完"))
    # §13 item 7: until this round's jscheck ended (finished, after the last run_end) the item is ⚠, whatever is left
    # to judge: nothing todo yet is no sign that nothing needs the browser (review 10-26)
    waiting = [s for s in ctx.round1_sites() if not rounds.jscheck_done(s)]
    if waiting:
        c.add(WARN, f"这一轮的 jscheck 还没跑完（没有比最后一次 run_end 晚、结束原因是 finished 的 jscheck_end，第 8.4 节）："
                    f"{len(waiting)} 个网站（{'、'.join(waiting[:10])}{'……' if len(waiting) > 10 else ''}）")
    old = f.q("SELECT count(*) FROM page_type WHERE n_pages = 0")[0][0]
    c.detail = (f"还没判定的类型 {n_types} 类、待判定的网页 {n_pages} 页；jscheck 还没跑完的网站 {len(waiting)} 个；"
                f"旧类型这一轮没出现 {old} 类（不算进这一项）")
    if old:
        sample = [r[0] for r in f.q("SELECT key || '（' || verdict || '）' FROM page_type WHERE n_pages = 0 "
                                    "ORDER BY key LIMIT 20")]
        c.lines.append("旧类型这一轮没出现（只列出，不算）：" + "、".join(sample) + ("……" if old > 20 else ""))


def expected_unhandled(site: str | None, res_key: str, text: str | None, why_now: str | None) -> tuple[str, str] | None:
    """need and why the platform file gives a how='unhandled' page_res entry now (design §7.8, §10.4 rule 9: with the
    button's text, page_res.text, or a rule that names a text never holds and a decided 「查詢」 reads unknown). None:
    no such entry should be there (the file now presses the button or handles the click; a rejudge drops it). A button
    the file says to press that nobody pressed is unhandled:unpressed (postback.UNPRESSED) while it says press."""
    from nknu.archive.postback import UNPRESSED

    d = profiles.decision(site, res_key, text)
    if d == "press":
        return ("must", UNPRESSED) if why_now == UNPRESSED else None
    if d == "handle":
        return None
    return links.unhandled_need(d)


def _c8(ctx: _Ctx, c: Check) -> None:
    from nknu.archive.postback import button_key

    f = ctx.f
    n = Counter()
    for r in f.q("""SELECT p.site, p.target, p.argument, p.text, p.n, u.url FROM postback p
                    LEFT JOIN url u ON u.id = p.sample ORDER BY p.site, p.target"""):
        key = button_key(r["target"], r["argument"] or "")
        try:
            d = profiles.decision(r["site"], key, r["text"])
        except ValueError as exc:
            d, why = "unknown", str(exc)
        else:
            why = ""
        if d == profiles.UNKNOWN:
            n["buttons"] += 1
            c.add(FAIL, f"{r['site']}：按钮 {key} 平台档案没写决定（{r['n']} 次，例如 {r['url'] or '—'}）{why}")
    for r in f.q("""SELECT u.owner_site, pr.why, pr.res_key, count(*) n, min(u.url) u FROM page_res pr
                    JOIN url u ON u.id = pr.page_id WHERE pr.why IN ('unhandled:unknown', 'unhandled:google')
                    AND pr.need <> 'no' GROUP BY u.owner_site, pr.why, pr.res_key ORDER BY u.owner_site, pr.res_key"""):
        n[r["why"]] += r["n"]
        what = "认不出的 Google 网址" if r["why"] == "unhandled:google" else "平台档案没写决定的写法"
        c.add(FAIL, f"{r['owner_site']}：{what} {r['res_key']}（{r['n']} 页，例如 {r['u']}）")
    cache: dict[tuple, tuple | None] = {}
    wrong: dict[tuple, list] = {}
    for r in f.q("""SELECT pr.res_key, pr.need, pr.why, pr.text, u.owner_site, u.url FROM page_res pr
                    JOIN url u ON u.id = pr.page_id WHERE pr.how = 'unhandled'"""):
        if r["res_key"].startswith("google:") or (r["why"] or "").startswith("unhandled:google"):
            continue  # what google_api.parse_link cannot read: no platform file decides it (counted above)
        k = (r["owner_site"], r["res_key"], r["text"], r["why"])
        if k not in cache:
            try:
                cache[k] = expected_unhandled(*k)
            except ValueError as exc:  # a click the file marks ignore: never allowed (§7.8)
                cache[k] = ("error", str(exc))
        want = cache[k]
        if want != (r["need"], r["why"]):
            wrong.setdefault((r["owner_site"], r["res_key"], r["need"], r["why"], want), []).append(r["url"])
    for (site, key, need, why, want), urls in sorted(wrong.items(), key=lambda x: (x[0][0] or "", x[0][1])):
        n["stale"] += len(urls)
        now = "不该再是没处理的写法（现在按或照规则点）" if want is None else f"{want[0]} / {want[1]}"
        c.add(FAIL, f"{site}：{key} 爬的时候记 {need} / {why}，现在的平台档案是 {now}（{len(urls)} 页，例如 {urls[0]}）："
                    "平台档案改了、还没 rejudge")
    c.detail = (f"没决定的按钮 {n['buttons']} 种、没决定的写法 {n['unhandled:unknown']} 处、认不出的 Google 网址 "
                f"{n['unhandled:google']} 处、和现在的平台档案对不上 {n['stale']} 处（标 round2 的算看过，列在第 16 项）")


def _c9(ctx: _Ctx, c: Check) -> None:
    rows = ctx.f.q("""SELECT owner_site, reason, count(*) n, min(url) u FROM url WHERE state = 'held'
                      GROUP BY owner_site, reason ORDER BY owner_site, reason""")
    for r in rows:
        c.add(FAIL, f"{r['owner_site']}：{r['reason']} × {r['n']}（例如 {r['u']}）")
    c.detail = f"撞到上限没抓的 {sum(r['n'] for r in rows)} 个（要人看过、决定抓不抓）"


def _c10(ctx: _Ctx, c: Check) -> None:
    from nknu.archive import google_api

    f = ctx.f
    api = google_api.API_HOST
    n_rows = f.q("SELECT count(*) FROM url WHERE host = ?", (api,))[0][0]
    n_res = f.q("SELECT count(*) FROM page_res WHERE role IN ('drive', 'calendar')")[0][0]
    if not n_rows and not n_res:
        c.detail = "没有云端硬盘或日历，没有要核对的"
        return
    marks = ",".join("?" * len(google_api.KEY_PAUSE_WHY))
    waiting = f.q(f"SELECT count(*) FROM url WHERE host = ? AND state = 'pending' AND reason IN ({marks})",
                  (api, *google_api.KEY_PAUSE_WHY))[0][0]
    ok = f.q("SELECT count(*) FROM request WHERE host = ? AND status = 200", (api,))[0][0]
    if waiting:
        c.add(FAIL, f"金钥没设或不能用：{waiting} 个云端硬盘、日历的网址在等金钥")
    elif n_rows and not ok:
        c.add(WARN, "还没有任何一个 Google API 请求成功过，看不出金钥能不能用")
    for r in f.q("""SELECT u.owner_site, pr.res_key, d.url FROM page_res pr JOIN url u ON u.id = pr.page_id
                    JOIN url_result d ON d.id = pr.res_id
                    WHERE pr.role IN ('drive', 'calendar') AND pr.need = 'must' AND d.kind0 = 'drive' AND d.result = 'ok'
                    ORDER BY u.owner_site LIMIT 200"""):
        c.add(FAIL, f"{r['owner_site']}：{r['res_key']} 还指着「读格式」那一行 {r['url']}（爬虫的 bug：内容行没接上）")
    for r in f.q("""SELECT owner_site, url, reason FROM url_result WHERE result = 'api_error'
                    ORDER BY owner_site, url"""):
        c.add(FAIL, f"{r['owner_site']}：Google API 读不到 {r['url']}（{r['reason'] or '没写原因'}）")
    for r in f.q("""SELECT owner_site, reason, count(*) n FROM url WHERE state = 'held'
                    AND (reason LIKE 'cap:drive%' OR reason = 'cap:calendar') GROUP BY owner_site, reason"""):
        c.add(FAIL, f"{r['owner_site']}：撞到云端硬盘或日历的上限（{r['reason']} × {r['n']}）")
    for r in f.q("""SELECT u.owner_site, pr.res_key FROM page_res pr JOIN url u ON u.id = pr.page_id
                    WHERE pr.why = 'unhandled:drive_type' AND pr.need = 'must' ORDER BY u.owner_site"""):
        c.add(FAIL, f"{r['owner_site']}：{r['res_key']} 是导不出的 Google 类型（协作平台、My Maps……）")
    c.detail = f"Google API 的网址 {n_rows} 个、成功的请求 {ok} 个、等金钥的 {waiting} 个"


def _c11(ctx: _Ctx, c: Check) -> None:
    f = ctx.f
    n = f.q("SELECT count(*) FROM request")[0][0]
    if not n:
        c.add(SKIP, "还没有任何请求")
    hard, soft = politeness(f), politeness_warnings(f)
    for x in hard:
        c.add(FAIL, x)
    for x in soft:
        c.add(WARN, f"服务器变慢时没多等：{x}")
    c.detail = f"请求 {n} 个（按 request.ip 分服务器，跨队列、跨用途、跨 run）：硬性违反 {len(hard)}、警告 {len(soft)}"


def _c11b(ctx: _Ctx, c: Check) -> None:
    out = scope_audit(ctx.f, ctx.plan)
    for x in out:
        c.add(FAIL, x)
    c.detail = f"超出范围的请求 {len(out)} 个" + ("" if ctx.plan is not None else "（没给名单：放行纪录本身没核对）")


def _c11c(ctx: _Ctx, c: Check) -> None:
    got = campus_requests(ctx.f)
    if got.on:
        c.add(WARN, got.line())
        for s, k in got.by_site.most_common():
            c.lines.append(f"{s}：{k} 个")
    c.detail = (f"在校内网络抓的 {got.on} 个；判断不出在校内还是校外的 {got.unknown} 个（只计数：SSO 的判断页还没查到，"
                "或旧版没记）")


def _c12(ctx: _Ctx, c: Check) -> None:
    f = ctx.f
    rows = f.q("SELECT s.url, u.id FROM seed_src s LEFT JOIN url u ON u.url = s.url WHERE s.source = 'v1_known' "
               "ORDER BY s.url")
    if not rows:
        c.add(SKIP, "没有 v1 已知的公告（seed 时没给 v1 的事实库？）")
    n = Counter()
    for r in rows:
        if r["id"] is None:
            n["bad"] += 1
            c.add(FAIL, f"{r['url']}：没有纪录")
            continue
        end, nowhere = landing(f, r["id"])
        res = end["result"] if end is not None else "unknown"
        if nowhere:
            n["bad"] += 1
            c.add(FAIL, f"{r['url']}：{nowhere}")
        elif res in ("ok", "gone", outcome.SCHOOL_SIDE):
            n["ok"] += 1
        elif res in ("todo", "retry", "lab"):
            n["wait"] += 1
            c.add(WARN, f"{r['url']}：{outcome.RESULT_LABEL[res]}")
        else:
            n["bad"] += 1
            c.add(FAIL, f"{r['url']}：{outcome.RESULT_LABEL.get(res, res)}{_why(end)}")
    c.detail = f"v1 已知的公告 {len(rows)} 则：抓到或学校那边已删 {n['ok']}，还在等 {n['wait']}，有问题 {n['bad']}"


def _gate_of_sites(ctx: _Ctx, c: Check, gate: str) -> None:
    """Items 13 and 14 as the batches' reconciliations ran them (reconcile.py runs the same functions, §18.2b)."""
    recs = reconciled(ctx.f, ctx.rounds)
    missing = []
    for s in ctx.round1_sites():
        rec = recs.get(s)
        st = rec.gates.get(gate) if rec is not None and rec.fresh else None
        if st == FAIL:
            c.add(FAIL, f"{s}：对账的 {gate} 没过（{rec.when()}）")
        elif st != PASS:
            missing.append(s)
    if missing:
        c.add(SKIP, f"这 {len(missing)} 个网站还没有新的对账：{'、'.join(missing[:30])}{'……' if len(missing) > 30 else ''}")


def _c13(ctx: _Ctx, c: Check) -> None:
    if ctx.resource_check is not None:
        problems = list(ctx.resource_check(ctx.f, ctx.root))
        for x in problems:
            c.add(FAIL, x)
        c.detail = f"从存档重新解析每一页、对照 page_res：对不上 {len(problems)} 处"
        return
    _gate_of_sites(ctx, c, "V13")
    c.detail = "从存档重新解析每一页对照 page_res（reconcile.py 的同一套函数，每批对账时跑，这里读对账结果 V13）"


_V13_MISSING = {"link": "链接表没有这个目标", "page_res": "page_res 没有这个资源", "not_discovered": "用到了却没有取得纪录"}


def extract_check(plan=None) -> Callable:
    """Item 13's function for `nknu archive verify` (§13: 从 WARC 重新解析每一页，用同一个 links.extract，对照 page_res):
    reconcile.verify_extract, the very function each batch's reconciliation runs (V13, §18.2b), over every round-1
    site, its problems in words (a page that does not read back is item 14's, counted here). plan: the site list
    (round1.load() when not given). Read only."""

    def check(f: Frontier, root: Path) -> list[str]:
        from nknu.archive.reconcile import verify_extract

        sites = [r[0] for r in f.q("SELECT site FROM site_plan WHERE round = '1' ORDER BY site")]
        out: list[str] = []
        for site, ck in verify_extract(f, root, sites, plan).items():
            if ck.counts.get("unread"):
                out.append(f"{site}：{ck.counts['unread']} 页按位置读不回来，没能重新解析（见第 14 项）")
            for i in ck.items:
                what = _V13_MISSING.get(i.get("missing"), i.get("missing") or "对不上")
                out.append(f"{site}：{i.get('page')} 的 {i.get('target')}：{what}"
                           + (f"（{i['what']}）" if i.get("what") else ""))
        return out

    return check


def _c14(ctx: _Ctx, c: Check) -> None:
    if ctx.root is None:
        c.add(SKIP, "没给存档目录")
        return
    if ctx.alive:
        c.add(SKIP, "爬虫在跑，存档档案还在写：停下再验")
        return
    problems, n = warc_readback(ctx.root, ctx.f)
    for x in problems:
        c.add(FAIL, x)
    c.detail = f"每个存档档案用 gzip 串流读完，按位置读回 {n} 笔回应比对 sha256：有问题 {len(problems)} 处"


def _sample(f: Frontier, sql: str, args=(), k: int = SAMPLE) -> list:
    """k rows spread evenly over what sql selects in its ORDER BY (every n-th), the same each time, without holding
    them all (a template image may sit on every page: some 10^5 rows)."""
    n = f.q(f"SELECT count(*) FROM ({sql})", args)[0][0]
    if n <= k:
        return f.q(sql, args)
    return [f.q(f"{sql} LIMIT 1 OFFSET ?", (*args, int(i * n / k)))[0] for i in range(k)]


def _c15(ctx: _Ctx, c: Check) -> None:
    f = ctx.f
    total = 0
    for result, what in (("login", "被判「要登录」"), ("looks_gone", "「看起来像不存在」"), ("thin", "「页面几乎是空的」")):
        rows = _sample(f, "SELECT url, flags FROM url_result WHERE tier IN (0, 1) AND result = ? ORDER BY id",
                       (result,))
        total += len(rows)
        for r in rows:
            c.add(WARN, f"{what}：{r['url']}（flags {r['flags'] or '—'}）")
    for (why,) in f.q("SELECT DISTINCT why FROM page_res WHERE role = 'img' AND need = 'no' ORDER BY why"):
        rows = _sample(f, "SELECT res_key FROM page_res WHERE role = 'img' AND need = 'no' AND why IS ? "
                          "ORDER BY page_id, res_key", (why,), IMAGE_SAMPLE)
        total += len(rows)
        for r in rows:
            c.add(WARN, f"不收的图片（{why or '没写原因'}）：{r['res_key'][:200]}")
    c.detail = f"抽了 {total} 个给人看（误判以前没有复核：要登录、像不存在、几乎是空的各 {SAMPLE} 个，不收的图片每种原因 {IMAGE_SAMPLE} 张）"


def known_gaps(src, wayback_open: bool = True) -> list[tuple[str, int | None, str]]:
    """§13 item 16: the gaps this round leaves on purpose, always listed (never read as fine): (words, how many, the
    unit; None for the fixed Wayback line)."""
    f = _frontier(src)

    def one(sql: str, args=()) -> int:
        return f.q(sql, args)[0][0] or 0

    out: list[tuple[str, int | None, str]] = []
    if wayback_open:
        out.append((WAYBACK_GAP, None, ""))
    out.append(("只限校内网络的网址，等搬到实验室", one("SELECT count(*) FROM url_result WHERE result = 'lab'"), "个"))
    out.append(("要 JavaScript 的网页，下一轮补", one("""SELECT count(*) FROM url_result WHERE kind = 'page'
                                                     AND tier IN (0, 1) AND result = 'js'"""), "页"))
    out.append(("写法看不懂的点了才载入内容的网页（分页签、下载按钮），下一轮补",
                one("""SELECT count(*) FROM (SELECT page_id FROM page_res WHERE role = 'click' AND need = 'round2'
                       UNION SELECT id FROM url_result WHERE kind = 'page' AND tier IN (0, 1) AND result = 'click')"""),
                "页"))
    out.append(("这一轮做不到的写法（平台档案标 round2 的：电子系的学年选单、R7 读了 .js 还确认不了的外部函数……），下一轮补",
                one("SELECT count(DISTINCT res_key) FROM page_res WHERE why = 'round2:unhandled'"), "种"))
    out.append(("jscheck 看到浏览器停在别的网址、留到下一轮的网址（round2:jscheck_redirect）",
                one("SELECT count(*) FROM url WHERE state = 'later' AND reason = 'round2:jscheck_redirect'"), "个"))
    out.append(("校外一层，下一轮", one("""SELECT count(*) FROM url WHERE state = 'later'
                                          AND reason IN ('round2:offsite', 'round2:offsite_file')"""), "个"))
    out.append(("发布到网络的 Google 文件（round2:google_published），等你决定怎么读",
                one("SELECT count(DISTINCT res_key) FROM page_res WHERE why = 'round2:google_published'"), "个"))
    return out


def _c16(ctx: _Ctx, c: Check) -> None:
    gaps = known_gaps(ctx.f, ctx.wayback_open)
    open_ = 0
    for words, k, unit in gaps:
        if k is None:
            open_ += 1
            c.add(WARN, f"已知缺口：{words}")
        elif k:
            open_ += 1
            c.add(WARN, f"已知缺口：{words}：{k} {unit}")
        else:
            c.lines.append(f"{words}：0 {unit}")
    c.detail = f"已知缺口 {open_} 项（固定列出，不会被当成没问题）"


def _c17(ctx: _Ctx, c: Check) -> None:
    sts = ctx.sites()
    if sts is None:
        c.add(SKIP, "完整度还没算好（看板背景每 2 分钟算一次）")
        return
    n = Counter()
    for s in sts.values():
        if not s.issues:
            continue
        red = [i for i in s.issues if i.red]
        pending = [i for i in s.issues if i.bucket == "pending_check"]
        lab = [i for i in s.issues if i.lab]
        n["sites"] += 1
        n["red"] += len(red)
        n["pending"] += len(pending)
        n["lab"] += len(lab)
        status = FAIL if red or (pending and s.jscheck_done) else WARN
        c.add(status, f"{s.site}（{s.name or '—'}）：连接失败或要人看 {len(red)}、待判定 {len(pending)}、只限校内 {len(lab)}"
                      + ("（这一轮的 jscheck 已经跑完）" if pending and s.jscheck_done else ""))
        c.lines += [f"  - {i.line()}" for i in s.issues[:REPORT_LINES]]
        if len(s.issues) > REPORT_LINES:
            c.lines.append(f"  - ……另有 {len(s.issues) - REPORT_LINES} 条")
    c.detail = (f"有问题的网站 {n['sites']} 个：连接失败或要人看 {n['red']}、待判定 {n['pending']}、只限校内 {n['lab']}"
                "（网页照页的完整度列，资源照结果列）")


def _c18(ctx: _Ctx, c: Check) -> None:
    f = ctx.f
    recs = reconciled(f, ctx.rounds)
    sites = ctx.round1_sites()
    if not sites:
        c.add(SKIP, "名单还没导入（site_plan 是空的）")
    n = Counter()
    for s in sites:
        rec = recs.get(s)
        if rec is None:
            n["none"] += 1
            c.add(FAIL, f"{s}：还没对账")
            continue
        if not rec.fresh:
            n["stale"] += 1
            c.add(FAIL, f"{s}：对账（{rec.when()}）比之后涵盖它的{rec.stale_words()}旧")
        elif not rec.passed:
            n["failed"] += 1
            c.add(FAIL, f"{s}：对账（{rec.when()}）没过：{'、'.join(rec.not_passed())}")
        else:
            n["ok"] += 1
        low = rec.low_cover()
        if low:
            n["low"] += 1
            c.add(WARN, f"{s}：{'；'.join(low)}")
            for u in _only_from_seeds(f, s)[:20]:
                c.lines.append(f"  - 只靠 Common Crawl 或旧清单才进来、没有这一轮网页链到：{u}")
        # §13 item 18: the discovery coverage site by site, as the estimate it is (review 10-26)
        c.lines += [f"  - {s}：{line}" for line in rec.cover_lines()]
        if rec.uncompared:
            c.lines.append(f"  - {s}：对账有几项没比到（不等于没问题）：{rec.blind_words()}")
        if rec.menu_school:
            c.lines.append(f"  - {s}：{rec.menu_words()}")
    c.detail = (f"第一轮网站 {len(sites)} 个：对账全过而且是新的 {n['ok']}，没对账 {n['none']}，过期 {n['stale']}，"
                f"没过 {n['failed']}；覆盖率低于 {COVER_MIN:.0%} 的 {n['low']}（只提醒，不挡「爬完」）")


def _c19(ctx: _Ctx, c: Check) -> None:
    """Every round-1 site 爬完 (§10.5): the items above each look at one side (the pages' problems, the
    reconciliations…), none at what is still to fetch or retry, so without this one verify could say 第一轮完成 while a
    site is still 还在抓 (found by the end-to-end test, 10-26) 【Claude 的执行办法】. ✗ 出错 is ✗; any other state not 爬完
    (要看, 没检查, 还在抓, 纪录齐了、还没对账) ⚠."""
    sts = ctx.sites()
    if sts is None:
        c.add(SKIP, "完整度还没算好")
        return
    states = Counter()
    for s in sts.values():
        states[s.state] += 1
        if s.state == DONE:
            continue
        c.add(FAIL if s.state == ERROR else WARN, f"{s.site}（{s.name or '—'}）：{s.label}：{'；'.join(s.why[:3]) or '—'}")
    if not sts:
        c.add(SKIP, "没有第一轮的网站")
    c.detail = f"第一轮网站 {sum(states.values())} 个：" + "、".join(f"{SITE_STATE[k][0]} {states[k]}" for k in SITE_ORDER
                                                               if states[k])


def _only_from_seeds(f: Frontier, site: str) -> list[str]:
    """A site's addresses that came in only from Common Crawl or the old archive and that no page of this round links
    to (§18.2b R3b, R4b; record 二·5 「会估出漏的比例」)."""
    return [r[0] for r in f.q("""SELECT u.url FROM url u WHERE u.owner_site = ? AND u.found_via IN ('commoncrawl',
                                   'old_frontier') AND NOT EXISTS (SELECT 1 FROM link l WHERE l.dst = u.url
                                   AND l.src <> u.id) ORDER BY u.url""", (site,))]


# the items in §13's order, and which the dashboard asks every 2 minutes (§14.2: 1–12 and 16)
ITEMS: tuple[tuple[str, str, Callable, bool], ...] = (
    ("1", "每个网站的首页和附加起点都抓到内容", _c1, True),
    ("2", "每一行都有已知的结果", _c2, True),
    ("3", "每站总数没比旧存档少 20% 以上", _c3, True),
    ("4", "列表都翻到底", _c4, True),
    ("5", "有列表的平台站都有翻页纪录", _c5, True),
    ("6", "网页不空、附件是真档案", _c6, True),
    ("7", "网页类型都判定过", _c7, True),
    ("8", "没有未知按钮、没处理的写法", _c8, True),
    ("9", "撞上限的都看过", _c9, True),
    ("10", "云端硬盘和日历", _c10, True),
    ("11", "礼貌", _c11, True),
    ("11b", "范围（没有碰不准碰的网址）", _c11b, True),
    ("11c", "在校内网络抓的请求", _c11c, True),
    ("12", "v1 已知的公告都存到了", _c12, True),
    ("13", "资源没有漏抽", _c13, False),
    ("14", "存档读得回来", _c14, False),
    ("15", "抽样给人看", _c15, False),
    ("16", "已知缺口（固定列出）", _c16, True),
    ("17", "逐站的问题清单", _c17, False),
    ("18", "每个网站都对过账", _c18, False),
    ("19", "每个网站都爬完", _c19, False),
)


def checks(src, now: float | None = None, *, cheap: bool = False, root: Path | None = None, plan=None,
           fx: Facts | None = None, pages: Iterable | None = None, statuses: dict[str, SiteStatus] | None = None,
           alive: bool | None = None, wayback_open: bool = True, resource_check: Callable | None = None
           ) -> list[Check]:
    """§13's items (the dashboard: cheap=True, items 1–12 and 16, §14.2; an item that needs page completeness reads
    the pages it is given, or says 没检查). root: the archive directory (item 14 reads its WARC files; and whether a
    run holds run.lock). plan: the site list (round1.load), for the scope audit's placements. resource_check: item
    13's function (reconcile.py's: (frontier, root) -> problems), else item 13 reads each site's reconciliation (V13).
    wayback_open: item 16 keeps its Wayback line until the user agrees and it is filled. (The 25 questions' item G is
    no item since 10-26, with reconcile's R5: §13.)"""
    f = _frontier(src)
    if alive is None:
        alive = root is not None and _alive(root) is not None
    ctx = _Ctx(f, root, plan, cheap, fx, statuses, fx.rounds if fx is not None else Rounds(f), alive, wayback_open,
               resource_check, pages)
    out = []
    for no, title, fn, is_cheap in ITEMS:
        if cheap and not is_cheap:
            continue
        c = Check(no, title)
        fn(ctx, c)
        out.append(c)
    return out


def exit_code(cs: Iterable[Check]) -> int:
    """2: something is wrong; 1: something to look at, or not checked (没检查 is no pass); 0: all passed."""
    worst = max((_RANK[c.status] for c in cs), default=0)
    return 2 if worst == _RANK[FAIL] else 1 if worst else 0


def conclusion(cs: list[Check]) -> str:
    """The one line on top (§13): never all green while a known gap is open: at best 「第一轮完成，有已知缺口 N 项」."""
    bad = [c for c in cs if c.status == FAIL]
    look = [c for c in cs if c.status in (WARN, SKIP) and c.no != "16"]
    gaps = next((c for c in cs if c.no == "16"), None)
    n_gaps = sum(1 for x in (gaps.lines if gaps else ()) if x.startswith("已知缺口："))
    if bad or look:
        return (f"还没完成：✗ 出错 {len(bad)} 项（{'、'.join(c.no for c in bad) or '—'}），⚠ 要看或没检查 {len(look)} 项"
                f"（{'、'.join(c.no for c in look) or '—'}）；另有已知缺口 {n_gaps} 项")
    if n_gaps:
        return f"第一轮完成，有已知缺口 {n_gaps} 项（第 16 项逐项列出）"
    return "第一轮完成"


# ---------------------------------------------------------------- item 14: the archive reads back (§13, §18.2b V14)

def _read_through(path: Path) -> str | None:
    """Read a .warc.gz to its end with Python's gzip, member after member (warcio check exits 0 on a gzip error)."""
    try:
        with gzip.open(path, "rb") as fh:
            while fh.read(1 << 20):
                pass
    except (OSError, EOFError, zlib.error) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


_V14_LINE = {"no_position": "抓过却没有存档位置：{label}", "unreadable": "按位置读不回来：{label}",
             "sha256": "读回来的内容和纪录的 sha256 不同：{label}",
             "other_url": "这个位置存的是别的网址：{label}（存档里是 {archived}）"}


def _v14_line(item: dict) -> str:
    if item.get("what") == "file":
        rel, broke = item.get("url"), item.get("problem")
        return f"存档档案不见了：{rel}" if broke == "no such file" else f"存档档案读不完：{rel}（{broke}）"
    label = item.get("url") if item.get("what") != "action" else f"按钮 a{item.get('id')}（{item.get('url')}）"
    return _V14_LINE.get(item.get("problem"), "{label}：{problem}").format(label=label, archived=item.get("archived"),
                                                                           problem=item.get("problem"))


def warc_readback(root: Path, src, sites: Iterable[str] | None = None) -> tuple[list[str], int]:
    """§13 item 14 over reconcile.verify_archive, the one implementation (§18.2b's V14 is the same function for a
    batch's sites): every archived answer (a url row's, a press's) read back where warc_offset says, its sha256 (and
    its address) compared with the row's, a row that came back (done, with a status) and has no place in the archive
    a problem too, and every WARC file those rows use read through gzip to its very end. sites None: every site with
    such rows, and every other .warc.gz under warc/ (jscheck's and R6's evidence, a file no row names) read through as
    well. Returns (problems in words, answers read back). Only when no run writes the files (verify's item 14 waits)."""
    from nknu.archive.reconcile import verify_archive

    f = _frontier(src)
    if sites is None:
        only = sorted({r[0] for r in f.q("SELECT DISTINCT owner_site FROM url WHERE state = 'done' AND status IS NOT "
                                           "NULL AND owner_site IS NOT NULL")}
                      | {r[0] for r in f.q("SELECT DISTINCT site FROM action WHERE state IN ('done', 'mismatch') AND "
                                           "status IS NOT NULL AND site IS NOT NULL")})
    else:
        only = sorted(set(sites))
    problems: list[str] = []
    n = 0
    named: set[str] = set()
    for ck in verify_archive(f, root, only, whole=True).values():
        n += ck.counts.get("records", 0)
        problems += [_v14_line(i) for i in ck.items]
    if only:
        marks = ",".join("?" * len(only))
        named = {r[0] for r in f.q(f"SELECT DISTINCT warc FROM url WHERE warc IS NOT NULL AND owner_site IN ({marks})",
                                   only)}
        named |= {r[0] for r in f.q(f"SELECT DISTINCT warc FROM action WHERE warc IS NOT NULL AND site IN ({marks})",
                                    only)}
    if sites is None and (root / "warc").exists():
        for path in sorted((root / "warc").rglob("*.warc.gz")):
            rel = str(path.relative_to(root))
            if rel not in named and (err := _read_through(path)) is not None:
                problems.append(f"存档档案读不完：{rel}（{err}）")
    return problems, n


# ---------------------------------------------------------------- nknu archive verify

@dataclass
class Verified:
    text: str
    code: int
    checks: list[Check]
    sites: dict[str, SiteStatus]
    path: Path | None


def verify(root: Path, *, plan=None, wayback_open: bool = True, resource_check: Callable | None = None,
           write: bool = True) -> Verified:
    """`nknu archive verify` (design §13): the frontier opened read-only (OutdatedFrontier for one an older version
    wrote), nothing sent, every item, each site's state; writes reports/verify-<time>.md under root (write=False: the
    text only). The exit code: 2 something wrong, 1 something to look at or not checked, 0 all passed."""
    f = Frontier(root / "frontier.db", readonly=True)
    try:
        alive = _alive(root)
        fx = facts(f)
        sts = site_status(f, fx=fx)
        cs = checks(f, root=root, plan=plan, fx=fx, statuses=sts, alive=alive is not None, wayback_open=wayback_open,
                    resource_check=resource_check)
    finally:
        f.close()
    code = exit_code(cs)
    text = render(root, cs, sts, code, alive)
    path = None
    if write:
        reports = root / "reports"
        reports.mkdir(exist_ok=True)
        path = reports / f"verify-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.md"
        path.write_text(text, encoding="utf-8")
    return Verified(text, code, cs, sts, path)


def render(root: Path, cs: list[Check], sts: dict[str, SiteStatus], code: int, alive: str | None) -> str:
    lines: list[str] = []
    w = lines.append
    w(f"# 第一轮验收（nknu archive verify）{datetime.now(TAIPEI):%Y-%m-%d %H:%M}（台北）")
    w("")
    w(f"- 存档：{root}")
    w(f"- 爬虫：{'在跑（pid ' + alive + '）：结果随时会变' if alive else '没有在跑'}")
    w(f"- 结论：{conclusion(cs)}")
    w(f"- 退出码：{code}（0 全过、1 有要看的或没检查的、2 有出错的）")
    w("")
    w("| # | 检查 | 结果 | 摘要 |")
    w("|---|---|---|---|")
    for c in cs:
        w(f"| {c.no} | {c.title} | {CHECK_ZH[c.status]} | {c.detail.replace('|', '／')} |")
    w("")
    states = Counter(s.state for s in sts.values())
    w("## 每个网站的状态（第 10.5 节）")
    w("")
    w(f"共 {len(sts)} 个：" + "、".join(f"{SITE_STATE[k][0]} {states[k]}" for k in SITE_ORDER if states[k]))
    w("")
    w("| 网站 | 单位 | 重要程度 | 状态 | 原因 |")
    w("|---|---|---|---|---|")
    tiers = {0: "全校重要", 1: "本系重要"}
    for s in sts.values():
        why = "；".join(s.why[:4]) + ("……" if len(s.why) > 4 else "")
        if s.warnings:
            why += "（另：" + "；".join(s.warnings) + "）"
        w(f"| {s.site or '（空）'} | {s.name or '—'} | {tiers.get(s.tier, '—')} | {s.label} | {why.replace('|', '／')} |")
    for c in cs:
        w("")
        w(f"## {c.no}. {c.title} — {CHECK_ZH[c.status]}")
        w("")
        w(c.detail)
        if c.lines:
            w("")
            for x in _cap(c.lines):
                w(x if x.startswith("  - ") else f"- {x}")
    return "\n".join(lines) + "\n"
