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
profiles must replay), skipped-link reasons, and the off-campus candidate domains.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from nknu.archive import scope
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
    once each. A row's robots.txt and probe say which row they were asked for (its placement): a form's host
    docs.google.com is no breach for its robots.txt (second review 10-26). Given the list (plan), a page request's
    placement is checked against it (_placement_breach)."""
    from nknu.archive.crawler import CAMPUS_URL

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
