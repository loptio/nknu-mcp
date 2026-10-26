"""The progress dashboard's numbers (round-1 design §14, record 二·7): where the crawl is, how long it still takes and
which server holds it up, each site's state and what it lacks, politeness, recent errors, whether this machine is on
the school's network, and when a run that ended `waiting` may run again. viewer.py draws them (/progress,
/progress?part=main, /progress/site?k=<site>); nothing here writes anything.

Never slowing the crawler (§14.4):
- every read goes through connect(): `mode=ro` (never `immutable=1`, which drops the locks and reads half-written
  pages), `PRAGMA query_only`, a 2-second busy timeout; the result views go to the connection's own temp schema
  (outcome.create_views(temp=True)), so nothing reaches the file;
- one short read transaction per refresh (BEGIN … COMMIT): every block sees the same moment, and a long reader would
  keep the WAL file growing (SQLite's WAL notes: checkpoint starvation);
- the numbers are kept CACHE_S seconds; page_completeness, each site's state (monitor.site_status) and the cheap
  checks (monitor.checks) are worked out every SLOW_EVERY_S seconds by a background thread into a snapshot
  (`slow_snapshot`), which the pages read;
- the request log is read incrementally by request.id (Tracker: the politeness check and each server's speed), once
  in full when the dashboard starts;
- the link table (978k rows in the old archive) is never read: an address that redirects is found by url.final_url.

Every function takes the read-only connection and the time and gives back plain data (dicts and lists); Dashboard
holds the cache, the tracker and the thread. Times are shown in Asia/Taipei (fmt_clock: 今天 22:10, 明天 02:40).

What this module takes from others (Dashboard wires the monitor's; called with only the keywords they take):
- monitor.facts(conn, pages) -> monitor.Facts: page_completeness (the rows already read) and page_resource read once,
  handed to both functions below as fx= (page_resource is as costly as page_completeness).
- monitor.site_status(conn, now, pages=<page_completeness rows, PAGE_COLUMNS>, fx=…) -> {site: SiteStatus}, the §10.5
  state of each round-1 site (error / look / unchecked / crawling / unreconciled / done; or a mapping with state and
  why: _norm_status). Missing, or failing, the sites say 没检查 with the reason, never a colour of their own.
- monitor.checks(conn, now, cheap=True, pages=…, statuses=…, fx=…, root=…) -> [monitor.Check: no, title, status
  (pass, warn, fail, skip), detail, lines] (§13's cheap items, 1–12 and 16). Missing or failing: the block says 没检查.
- reconcile_site.checks (§18.2b, reconcile.py's: {"status", counts, "items"}): R1's, R3a's and R4a's items are the
  addresses the crawl has no result for (FOUND_CHECKS), listed under 要人看 in 漏抓检查.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nknu.archive import outcome
from nknu.archive.frontier import ALL_QUEUES, Frontier, OutdatedFrontier, _missing, net_key, network_of

TZ = ZoneInfo("Asia/Taipei")
REFRESH_S = 30  # the page asks again this often (record 二·7)
CACHE_S = 25.0  # §14.4: the numbers are kept this long
SLOW_EVERY_S = 120.0  # §14.4: page_completeness, the sites' states and the cheap checks, in the background
BUSY_MS = 2000  # §14.4
HOUR_S = 3600.0
IDLE_GAP_S = 120.0  # §14.3: a longer gap between one request's start and the next's is idle, a rest or a pause
MIN_HOUR_PAGES = 20  # §14.3: fewer page requests in the last hour: the whole round's average
GUESS_S = 4.0  # §14.3: no request at all yet: 4 s a request, marked 估计
NEW_CONN_RED = 0.10  # §6.2: more new connections than this on c.nknu's server: keep-alive is not working
NEW_CONN_MIN = 20  # requests in the hour before the share of new connections says anything
TOLERANCE_S = 0.05  # monitor.TOLERANCE_S
MIN_GAP_S = 2.0  # monitor.MIN_GAP_S: a run whose run_start says no gap
EXAMPLES = 20  # violations and warnings kept as examples (the counts are all)
MAX_ROWS = 3000  # one site's page lists at most this many rows of one kind
PER_BUCKET = 200  # one site's page, unfiltered: this many rows of each category
EVENTS_SHOWN = 15
TIER_NAME = {0: "全校重要", 1: "本系重要"}
ROUND1_TIERS = (0, 1)
KEEPALIVE_HOST = "c.nknu.edu.tw"  # §6.2: the server whose new-connection share turns red
STOP_FILE = "STOP"
WORK_PURPOSES = ("page", "postback")  # a request that is a row's or a press's own (the rest: robots, probes, …)
LAB = ("dns", "host_unreachable")  # outcome._LAB_REASONS: rows that wait for the lab machine, not this run
LAB_RESULTS = ("lab",)

# a site's state (design §10.5), as the dashboard shows it: sign, words, colour (§14.5: never colour alone)
STATE_SHOW = {
    "error": ("✗", "出错", "red"),
    "look": ("⚠", "要看", "yellow"),
    "crawling": ("", "还在抓", "none"),
    "unreconciled": ("⚠", "纪录齐了，还没对账", "yellow"),
    "done": ("✓", "爬完", "green"),
    "unchecked": ("⚠", "没检查", "yellow"),
}
STATE_RANK = {"error": 0, "look": 1, "unchecked": 2, "crawling": 3, "unreconciled": 4, "done": 5}
_STATE_WORDS = {"出错": "error", "要看": "look", "还在抓": "crawling", "纪录齐了，还没对账": "unreconciled",
                "爬完": "done", "没检查": "unchecked", "red": "error", "green": "done"}
# the order one site's pages are listed in (§14.2: 连接失败 → 要人看 → … → 完整), by page_completeness.label
PAGE_ORDER = ("failed", "thin", "review", "list", "list_unchecked", "miss_file", "miss_img", "miss_other", "unchecked",
              "js", "click", "round2", "retry", "todo", "complete_school", "complete")
LABEL_ZH = dict(outcome.LABEL_ORDER)
# the order of the other addresses (§14.2: the ten categories, 抓到了 last)
BUCKET_ORDER = ("failed", "review", "pending_check", "next_round", "queued", "login", "gone", outcome.SCHOOL_SIDE,
                "rule", "got")
BUCKET_ZH = {code: zh for code, zh, _then in outcome.BUCKETS}
BUCKET_THEN = {code: then for code, _zh, then in outcome.BUCKETS}
BUCKET_COLOR = {"failed": "red", "review": "red", "pending_check": "yellow", "next_round": "none", "queued": "none",
                "login": "none", "gone": "green", outcome.SCHOOL_SIDE: "green", "rule": "none", "got": "green"}
# a page's colour, by its label (§14.5: the colour of what the owner must do; §10.5: 连接失败, 要人看 and a list not
# walked to its end are red)
LABEL_COLOR = {"todo": "none", "retry": "yellow", "failed": "red", "thin": "red", "review": "red", "js": "yellow",
               "list": "red", "list_unchecked": "yellow", "miss_file": "yellow", "miss_img": "yellow",
               "miss_other": "yellow", "click": "yellow", "round2": "yellow", "unchecked": "yellow",
               "complete_school": "green", "complete": "green"}
# round-two rows by why (url.reason of a later row): what the batch is, in words
ROUND2_ZH = {
    "round2:other_site": "其他网站",
    "round2:js_site": "整站要 JavaScript 的网站",
    "round2:offsite_unit": "校外的本校单位网站（附表一）",
    "round2:offsite": "校外一层",
    "round2:offsite_file": "校外的档案",
    "round2:jscheck_redirect": "浏览器停在别的网址",
    "round2:google_published": "发布到网络的 Google 文件",
    "round2:click": "点了才载入的内容",
}
RUN_END_ZH = {
    "finished": "抓完了",
    "waiting": "先停下等重试",
    "stop_file": "你停的（STOP 档）",
    "sigterm": "你停的",
    "stop": "停了",
    "limit": "到了这次设的数量上限",
    "stuck": "出错停了：有该抓的网址却没有服务器接得了（爬虫的 bug）",
    "no_school_lock": "出错停了：没拿到全机锁就要发请求（程序的 bug）",
    "warc_write": "出错停了：存档写不进去",
    "disk_low": "出错停了：硬碟空间不够",
    "db_error": "出错停了：待抓清单读写出错",
}
RUN_END_BAD = ("stuck", "no_school_lock", "warc_write", "disk_low", "db_error")

# ---------------------------------------------------------------- plain words (§14.5)

# an error code (url.reason, crawler.error_kind, an event's code) → what happened and what happens next, in plain words
PLAIN = {
    "dns": "找不到这台主机的地址（DNS 查不到）：隔一阵再查，查了一天还不行才算连接失败",
    "connect": "连不上这台服务器：整台暂停 15 分钟，不扣重试次数",
    "conn_pause": "连不上这台服务器：整台暂停 15 分钟，不扣重试次数",
    "tls": "加密连线握手失败：算这个网址失败，隔 30 分钟再试，共 3 次",
    "tls_cert": "学校的证书没通过检查（过期或设定错）：算这个网址失败，3 次后列进「连接失败」",
    "timeout": "服务器太久没回应：隔 30 分钟再试，共 3 次",
    "too_slow": "下载太久没完成：隔 30 分钟再试，共 3 次",
    "net": "连线到一半被中断：隔 30 分钟再试，共 3 次",
    "busy": "服务器说「太忙」：整台休息至少 15 分钟，不扣重试次数",
    "retry_after": "服务器要我们晚点再来：整台照它说的时间休息，不扣重试次数",
    "http_5xx": "学校服务器出错：隔 30 分钟再试；同一台连续 3 次出错就整台休息 15 分钟",
    "http_408": "服务器说等太久：隔 30 分钟再试",
    "http_4xx": "学校回了错误：照它的回应记下来，归「连接失败」或「学校那边已经不存在」",
    "refused": "被拒绝（401、403、451）：归「连接失败」，每个写原因",
    "error_page": "学校服务器跳到出错页：保留旧值，隔 30 分钟再试",
    "too_large": "档案太大，没抓：归「要人看」，人决定要不要抓",
    "decode": "编码有问题：原样存档，归「要人看」",
    "media_type": "影音档：照规则不抓",
    "host_cooldown": "同一个主机名连续出错：这个主机名冷却 15 分钟",
    "server_rest": "服务器出错或说太忙：整台休息 15 分钟",
    "server_down": "服务器连续 8 次连不上（约 2 小时）：要人看是不是永久下线",
    "host_unreachable": "从没在校外回过我们的学校主机连不上：等搬到实验室机器再试",
    "offline": "本机网络或学校网络像是断了：暂停，恢复后自动接着抓",
    "online": "网络恢复了：接着抓",
    "dns_gave_up": "主机名查了一天都查不到：它的网址记连接失败",
    "robots_5xx": "robots.txt 回了出错：照常抓，规则照旧",
    "robots_unreachable": "robots.txt 读不到：这台主机等一小时再试",
    "robots_redirect": "robots.txt 跳到别处：当作没有",
    "robots_unparsed": "robots.txt 看不懂：当作没有",
    "host_denies": "同一个目录连续被拒：那个目录先暂缓 15 分钟，同主机其他目录照常",
    "scheme_switch": "http 和 https 自动改试：照能连的那一种抓",
    "soft404_host": "这台主机不存在的网页也回 200：长得一样的页面会被标出来",
    "key_check": "Google 不接受金钥：云端硬盘和日历这次先不读，不算失败",
    "campus": "校内还是校外网络的判断",
    "stuck": "有该抓的网址却没有服务器接得了：爬虫的 bug，这次 run 停下",
    "left_active": "有网址卡在「抓取中」没人处理：爬虫的 bug，已记成失败，可以 retry crash 放回",
    "no_school_lock": "没拿到全机锁就要发请求：程序的 bug，这次 run 停下",
    "warc_write": "存档写不进去：这次 run 停下，修好存档资料夹再跑",
    "disk_low": "硬碟空间不到 5 GB：这次 run 停下",
    "db_error": "待抓清单读写出错：这次 run 停下",
    "crash": "程序出错（爬虫的 bug）：这一行记下原因",
    "idle_closed": "闲置的连线被服务器关掉：在新连线上重发 1 次，不算出错",
    "fetch": "抓取失败：隔 30 分钟再试，共 3 次",
}
_MESSAGE_KINDS = (  # a free-text error (request.error, an event's message) → its code
    (re.compile(r"(?i)nodename nor servname|name or service not known|getaddrinfo|temporary failure in name"), "dns"),
    (re.compile(r"(?i)certificate verify failed|certificate has expired|self.signed|hostname mismatch"), "tls_cert"),
    (re.compile(r"(?i)\bssl\b|\btls\b|handshake"), "tls"),
    (re.compile(r"(?i)connecttimeout|connection refused|connect timeout|errno 61|errno 111|no route to host"),
     "connect"),
    (re.compile(r"(?i)too ?slow"), "too_slow"),
    (re.compile(r"(?i)readtimeout|writetimeout|pooltimeout|timed? ?out"), "timeout"),
    (re.compile(r"(?i)server disconnected|remoteprotocolerror|readerror|writeerror|connection reset|broken pipe"),
     "net"),
    (re.compile(r"(?i)retry.after"), "retry_after"),
    (re.compile(r"(?i)network is (still )?down"), "offline"),
    (re.compile(r"(?i)no connection"), "connect"),
    (re.compile(r"(?i)api_key"), "key_check"),
    (re.compile(r"(?i)warc_write"), "warc_write"),
    (re.compile(r"^crash:"), "crash"),
)
_HTTP = re.compile(r"(?:http_|HTTP )([1-5]\d\d)\b")


def error_code(code: str | None, message: str | None = None, status: int | None = None) -> str:
    """The plain-words key of a failure: its code as the crawler wrote it (url.reason, an event's code, error_kind),
    else what its free-text message or its status says; '' when nothing is known."""
    c = (code or "").strip()
    if c.startswith("net_"):
        return "net"
    if c.startswith("crash"):
        return "crash"
    if c in ("http_503", "http_429"):
        return "busy"
    if c == "http_408":
        return "http_408"
    if c.startswith("http_5"):
        return "http_5xx"
    if c in ("http_401", "http_403", "http_451"):
        return "refused"
    if c.startswith("http_4"):
        return "http_4xx"
    if c in PLAIN:
        return c
    text = message or ""
    for pattern, kind in _MESSAGE_KINDS:
        if pattern.search(text):
            return kind
    st = status
    if st is None and (m := _HTTP.search(text)):
        st = int(m[1])
    if st is not None:
        return error_code(f"http_{st}") if st >= 400 else ""
    return "fetch" if (code or text) else ""


def plain_error(code: str | None, message: str | None = None, status: int | None = None) -> str:
    """A failure in plain words, with what happens next (§14.5: the codes stay in the detail pages and the hover)."""
    key = error_code(code, message, status)
    if key:
        return PLAIN[key]
    return "（没有错误）"


# ---------------------------------------------------------------- time (Asia/Taipei, §14.5)

def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def fmt_clock(ts: float | None, now: float | None = None) -> str:
    """A moment as the dashboard says it, in Taipei: 今天 22:10, 明天 02:40, 昨天 23:05, else 10-28 02:40."""
    if ts is None:
        return "—"
    now = time.time() if now is None else now
    at, today = datetime.fromtimestamp(ts, TZ), datetime.fromtimestamp(now, TZ)
    days = (at.date() - today.date()).days
    day = {0: "今天", 1: "明天", -1: "昨天", 2: "后天"}.get(days, f"{at:%m-%d}")
    return f"{day} {at:%H:%M}"


def fmt_time(ts: float) -> str:
    """The clock time only, to the second (上次更新 16:42:10)."""
    return f"{datetime.fromtimestamp(ts, TZ):%H:%M:%S}"


def fmt_dur(seconds: float | None) -> str:
    """A length of time in words: 3 秒, 12 分钟, 7 小时 30 分, 2 天 4 小时."""
    if seconds is None:
        return "—"
    s = max(0, round(seconds))
    if s < 60:
        return f"{s} 秒"
    if s < 3600:
        return f"{s // 60} 分钟"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h} 小时" + (f" {m} 分" if m else "")
    d, h = divmod(s // 3600, 24)
    return f"{d} 天" + (f" {h} 小时" if h else "")


# ---------------------------------------------------------------- the read-only connection (§14.4)

def connect(db: Path) -> sqlite3.Connection:
    """frontier.db read-only: mode=ro (never immutable=1), the result views in this connection's temp schema, then
    query_only and a 2-second busy timeout. FileNotFoundError when there is no file (mode=ro never makes one),
    OutdatedFrontier when an older version wrote it (it lacks what the dashboard reads)."""
    if not db.exists():
        raise FileNotFoundError(db)
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=BUSY_MS / 1000,
                           isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        gaps = _missing(conn)
        if gaps:
            names = ", ".join(f"{t}.{c[1]}" if c else t for t, c in gaps[:6])
            raise OutdatedFrontier(f"{db} was written by an older version (no {names})")
        outcome.create_views(conn, temp=True)  # before query_only, which refuses even a temp view
        conn.execute("PRAGMA query_only=ON")
        conn.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    except BaseException:
        conn.close()
        raise
    return conn


def as_frontier(conn: sqlite3.Connection) -> Frontier:
    """A Frontier over this read-only connection, for the monitor's readers (stalled_queues, paused_until): no table,
    view or index is made (Frontier.__init__ is not run)."""
    f = Frontier.__new__(Frontier)
    f.path, f.lock, f.conn = None, threading.RLock(), conn
    return f


# ---------------------------------------------------------------- the request log, read once (§14.2, §14.3)

def _server(ip: str | None, queue: str | None) -> str:
    """monitor._server: the address a request answered from, else its queue's."""
    if ip:
        return ip
    return (queue or "").removeprefix("ip:")


def _least_gap(runs: list[tuple[float, float]], started: float) -> float:
    """monitor._least_gap: the least gap of the run a request was made in."""
    gap = MIN_GAP_S
    for at, g in runs:
        if at > started + TOLERANCE_S:
            break
        gap = g
    return gap


def run_gaps(conn: sqlite3.Connection) -> list[tuple[float, float]]:
    """(when a run started, its least gap), in order (monitor._run_gaps)."""
    out = []
    for r in conn.execute("SELECT at, message FROM event WHERE code='run_start' ORDER BY at"):
        at = _ts(r["at"])
        if at is None:
            continue
        try:
            gap = float(json.loads(r["message"] or "{}").get("gap", [MIN_GAP_S])[0])
        except (ValueError, TypeError, AttributeError, IndexError, KeyError):
            gap = MIN_GAP_S
        out.append((at, gap))
    return out


def crawl_delays(conn: sqlite3.Connection) -> dict[str, float]:
    return {r["host"]: min(float(r["crawl_delay"]), 300.0)
            for r in conn.execute("SELECT host, crawl_delay FROM host WHERE crawl_delay > 0")}


class Politeness:
    """The politeness check (§6.3, §14.2), fed the request log a piece at a time, in order (each server's requests come
    from one worker, one after another): the same rules as monitor.politeness and monitor.politeness_warnings, so
    feeding it in two pieces or at once gives the same answer, and the same answer as the monitor's full scan.

    Hard: two requests to one server overlapping, a gap below the run's least, a Crawl-delay not kept. Warning: after a
    slow answer, a shorter wait than the answer took (at most 30 s). Counts are all; EXAMPLES of each are kept."""

    def __init__(self, examples: int = EXAMPLES):
        self._last: dict[str, tuple[float, float, str]] = {}  # server → (started, latest end, url)
        self._prev: dict[str, tuple[float, float]] = {}  # server → the last request's own (started, ended)
        self._host_end: dict[str, float] = {}
        self.counts: Counter[str] = Counter()
        self.hard: deque[str] = deque(maxlen=examples)
        self.warn: deque[str] = deque(maxlen=examples)
        self.seen = 0

    def feed(self, rows, runs: list[tuple[float, float]], delays: dict[str, float]) -> None:
        """rows: request rows (ip, queue, host, url, started, ended) in the order they were made."""
        for r in rows:
            self.seen += 1
            key, host, url = _server(r["ip"], r["queue"]), r["host"], r["url"]
            started, ended = r["started"], r["ended"]
            prev = self._last.get(key)
            if prev is not None:
                least = _least_gap(runs, started)
                if started < prev[1] - TOLERANCE_S:
                    self.counts["overlap"] += 1
                    self.hard.append(f"同时连两条：服务器 {key} 在上一个请求结束前又发了请求（{prev[2]} → {url}）")
                elif started - prev[1] < least - TOLERANCE_S:
                    self.counts["gap"] += 1
                    self.hard.append(f"间隔太短：服务器 {key} 只隔 {started - prev[1]:.2f} 秒（下限 {least:g} 秒；{url}）")
            delay = delays.get(host)
            if delay and host in self._host_end and started - self._host_end[host] < delay - TOLERANCE_S:
                self.counts["delay"] += 1
                self.hard.append(f"没照网站要求的间隔：{host} 要求 {delay:g} 秒，只隔 "
                                 f"{started - self._host_end[host]:.1f} 秒（{url}）")
            own = self._prev.get(key)
            if own is not None:
                took = min(own[1] - own[0], 30.0)
                if own[1] <= started < own[1] + took - TOLERANCE_S:
                    self.counts["slow"] += 1
                    self.warn.append(f"服务器 {key} 上一个回应花了 {took:.1f} 秒，之后只等了 {started - own[1]:.1f} 秒（{url}）")
            self._last[key] = (started, max(ended, prev[1]) if prev else ended, url)
            self._prev[key] = (started, ended)
            self._host_end[host] = ended

    def result(self) -> dict:
        hard = self.counts["overlap"] + self.counts["gap"] + self.counts["delay"]
        return {"checked": self.seen, "hard": hard, "overlap": self.counts["overlap"], "gap": self.counts["gap"],
                "delay": self.counts["delay"], "slow": self.counts["slow"], "hard_examples": list(self.hard),
                "warn_examples": list(self.warn)}


@dataclass
class _Queue:
    """One server queue's requests (§14.3): the whole round's sums, and the last hour's requests."""
    last_started: float | None = None
    n: int = 0
    work: int = 0
    cycles: int = 0
    cycle_sum: float = 0.0
    took_sum: float = 0.0
    new_known: int = 0
    new_conn: int = 0
    # (started, cycle or None, took, new_conn or None, work, page)
    hour: deque = field(default_factory=deque)


class Tracker:
    """The request log read incrementally by request.id (§14.2: one full scan when the dashboard starts, then only the
    new rows): the politeness check, each server queue's speed, and where the last request was made from (campus)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.last_id = 0
        self.polite = Politeness()
        self.queues: dict[str, _Queue] = defaultdict(_Queue)
        self.campus_last: str | None = None
        self.campus_on = 0

    def update(self, conn: sqlite3.Connection) -> int:
        """Read the requests made since the last update; returns how many. A log that went back (another archive,
        or a fresh one) starts the tracker again."""
        with self.lock:
            top = conn.execute("SELECT coalesce(max(id), 0) FROM request").fetchone()[0]
            if top < self.last_id:
                self.reset()
            rows = conn.execute("SELECT id, queue, host, url, purpose, started, ended, ip, new_conn, attempt, campus "
                                "FROM request WHERE id > ? ORDER BY started, id", (self.last_id,)).fetchall()
            if not rows:
                return 0
            self.polite.feed(rows, run_gaps(conn), crawl_delays(conn))
            for r in rows:
                self._count(r)
                self.last_id = max(self.last_id, r["id"])
            last = max(rows, key=lambda r: r["id"])
            if last["campus"]:
                self.campus_last = last["campus"]
            return len(rows)

    def _count(self, r) -> None:
        q = self.queues[r["queue"] or "?"]
        started, took = r["started"], max(0.0, r["ended"] - r["started"])
        cycle = None
        if q.last_started is not None and 0 <= started - q.last_started <= IDLE_GAP_S:
            cycle = started - q.last_started
            q.cycles += 1
            q.cycle_sum += cycle
        q.last_started = started if q.last_started is None else max(q.last_started, started)
        work = r["purpose"] in WORK_PURPOSES and (r["attempt"] or 1) == 1
        q.n += 1
        q.work += work
        q.took_sum += took
        if r["new_conn"] is not None:
            q.new_known += 1
            q.new_conn += bool(r["new_conn"])
        q.hour.append((started, cycle, took, r["new_conn"], work, r["purpose"] == "page"))
        if r["campus"] == "on":
            self.campus_on += 1

    def speeds(self, now: float) -> dict[str, dict]:
        """Each queue's speed (§14.3): c (seconds from one request's start to the next's, gaps over IDLE_GAP_S left
        out), k (requests per row or press), the answer's own time and the wait, the share of new connections, and
        whether c is the last hour's, the whole round's or a guess."""
        out = {}
        with self.lock:
            for name, q in self.queues.items():
                while q.hour and q.hour[0][0] < now - HOUR_S:
                    q.hour.popleft()
                h_cycles = [e[1] for e in q.hour if e[1] is not None]
                h_pages = sum(1 for e in q.hour if e[5])
                h_work = sum(1 for e in q.hour if e[4])
                if h_pages >= MIN_HOUR_PAGES and h_cycles:
                    c, basis = sum(h_cycles) / len(h_cycles), "hour"
                    took = sum(e[2] for e in q.hour) / len(q.hour)
                    k = len(q.hour) / max(1, h_work)
                elif q.cycles:
                    c, basis = q.cycle_sum / q.cycles, "round"
                    took = q.took_sum / q.n
                    k = q.n / max(1, q.work)
                else:
                    c, basis, took, k = GUESS_S, "guess", None, 1.0
                known = [e[3] for e in q.hour if e[3] is not None]
                out[name] = {"c": c, "basis": basis, "k": max(1.0, k), "took": took,
                             "wait": None if took is None else max(0.0, c - took),
                             "hour_requests": len(q.hour), "hour_work": h_work,
                             "new_share": (sum(bool(x) for x in known) / len(known)) if known else None,
                             "new_known": len(known), "requests": q.n}
        return out


# ---------------------------------------------------------------- how long it still takes (§14.3)

def hosts_left(conn: sqlite3.Connection) -> dict[str, tuple[int, float | None]]:
    """Each host's rows still to fetch this run (url_result todo and retry: pending or active, lab rows aside) and the
    last retry's time. snapshot() works the same out of its one scan of url_result (_by_host)."""
    return {r["host"]: (r["n"], r["last_retry"]) for r in conn.execute(
        f"""SELECT host, count(*) n, max(CASE WHEN attempts > 0 THEN next_at END) last_retry
            FROM url WHERE state IN ('pending', 'active') AND coalesce(reason, '') NOT IN {outcome._sql_list(LAB)}
            GROUP BY host""")}


def eta(conn: sqlite3.Connection, now: float, speeds: dict[str, dict],
        left: dict[str, tuple[int, float | None]] | None = None) -> dict:
    """The round's finishing time: each server's T = W × k × c (W: the rows still to fetch, lab rows and DNS failures
    aside, plus the presses still to make; k, c: Tracker.speeds), at least each slow host's rows × its Crawl-delay
    and the last retry's time from now plus c, plus what is left of a pause or a cool-down; the round is the largest
    T (the servers run side by side: the makespan), with the server that holds it up named. left: hosts_left(),
    when the caller has it already."""
    hosts = {r["host"]: r for r in conn.execute("SELECT host, queue, crawl_delay, cooldown_until FROM host")}
    per: dict[str, dict] = defaultdict(lambda: {"left": 0, "rows": 0, "presses": 0, "hosts": {}, "last_retry": None,
                                                "cool": 0.0})
    for host, (n, last_retry) in (hosts_left(conn) if left is None else left).items():
        if not n:
            continue
        h = hosts.get(host)
        queue = h["queue"] if h is not None else "?"
        if queue == "dns-fail":
            continue
        p = per[queue]
        p["rows"] += n
        p["hosts"][host] = n
        if last_retry is not None:
            p["last_retry"] = max(p["last_retry"] or 0.0, last_retry)
        if h is not None and (h["cooldown_until"] or 0) > now:
            p["cool"] = max(p["cool"], h["cooldown_until"] - now)
    for r in conn.execute("SELECT queue, count(*) n FROM action WHERE state IN ('pending', 'active') GROUP BY queue"):
        if r["queue"] != "dns-fail":
            per[r["queue"]]["presses"] += r["n"]
    paused = {r["queue"]: r["until"] for r in conn.execute("SELECT queue, until FROM pause WHERE until > ?", (now,))}
    out: dict[str, dict] = {}
    for queue, p in per.items():
        left = p["rows"] + p["presses"]
        if left == 0:
            continue
        sp = speeds.get(queue) or {"c": GUESS_S, "k": 1.0, "basis": "guess"}
        c, k = sp["c"], sp["k"]
        t, why = left * k * c, []
        for host, n in p["hosts"].items():
            delay = min(float((hosts.get(host) or {"crawl_delay": 0})["crawl_delay"] or 0), 300.0)
            if delay > c and n * delay > t:
                t = n * delay
                why.append(f"{host} 要求每次隔 {delay:g} 秒")
        if p["last_retry"] is not None and p["last_retry"] - now + c > t:
            t = p["last_retry"] - now + c
            why.append(f"最后一个重试在 {fmt_clock(p['last_retry'], now)}")
        pause = max((paused.get(k_, 0.0) for k_ in (queue, ALL_QUEUES, net_key(network_of(queue)))), default=0.0)
        rest = max(pause - now if pause > now else 0.0, p["cool"])
        if rest > 0:
            t += rest
            why.append(f"暂停或冷却还剩 {fmt_dur(rest)}")
        out[queue] = {"left": left, "rows": p["rows"], "presses": p["presses"], "c": c, "k": k, "t": t,
                      "per_row": k * c, "basis": sp.get("basis", "guess"), "why": why}
    slowest = max(out, key=lambda q: (out[q]["t"], q), default=None)
    total = out[slowest]["t"] if slowest else 0.0
    return {"servers": out, "slowest": slowest, "seconds": total, "finish_at": now + total if slowest else None,
            "guess": bool(slowest and out[slowest]["basis"] == "guess")}


# ---------------------------------------------------------------- the 30-second numbers (§14.2)

def _cat(kind: str | None) -> str:
    return "page" if kind == "page" else "image" if kind == "image" else "file"


def _judged_page(kind: str | None, tier: int | None, result: str) -> bool:
    """A round-1 page judged by its completeness (page_completeness) or folded into where it lands (a redirect): never
    counted, nor listed, by its own url_result (§14.2: an incomplete page is never 抓到了 nor 规则不抓)."""
    return kind == "page" and tier in ROUND1_TIERS and result not in outcome.NOT_JUDGED


def _run_state(conn: sqlite3.Connection, now: float, running: str | None, stop_file: bool) -> dict:
    """The status bar: running or not, how the last run ended, pauses, when a waiting run may go again."""
    def last(code: str):
        r = conn.execute("SELECT rowid, at, message FROM event WHERE code=? ORDER BY rowid DESC LIMIT 1",
                         (code,)).fetchone()
        if r is None:
            return None, {}, 0
        try:
            msg = json.loads(r["message"] or "{}")
        except ValueError:
            msg = {}
        return _ts(r["at"]), msg if isinstance(msg, dict) else {}, r["rowid"]

    started_at, start, start_row = last("run_start")
    ended_at, end, end_row = last("run_end")
    last_req = conn.execute("SELECT max(ended) FROM request").fetchone()[0]
    out: dict[str, Any] = {"running": running, "stop_file": stop_file, "started_at": started_at,
                           "ended_at": ended_at, "end_reason": end.get("reason"), "last_request": last_req,
                           "gap": start.get("gap"), "sites": start.get("sites"), "pending": end.get("pending"),
                           "next_due": end.get("next_due"), "pauses": [], "stalled": []}
    if running:
        out["state"] = "running"
    elif started_at is None:
        out["state"] = "never"
    elif end_row < start_row:
        out["state"] = "crashed"  # a run_start with no run_end after it, and no run holds the lock: killed or crashed
    elif end.get("reason") in RUN_END_BAD:
        out["state"] = "bad_end"
    else:
        out["state"] = "ended"
    for r in conn.execute("SELECT queue, until, why FROM pause WHERE until > ? AND (queue = ? OR queue LIKE 'net:%')",
                          (now, ALL_QUEUES)):
        what = {ALL_QUEUES: "本机网络像是断了：全部服务器", net_key("school"): "学校网络没有回应：学校的服务器",
                net_key("other"): "校外网络没有回应：校外的服务器"}.get(r["queue"], r["queue"])
        out["pauses"].append({"what": what, "until": r["until"], "why": r["why"] or ""})
    if running:
        from nknu.archive import monitor

        try:
            out["stalled"] = monitor.stalled_queues(as_frontier(conn), now)
        except sqlite3.Error:
            out["stalled"] = []
    return out


def snapshot(conn: sqlite3.Connection, now: float, *, tracker: Tracker | None = None, slow: dict | None = None,
             running: str | None = None, stop_file: bool = False) -> dict:
    """The dashboard's numbers at `now`, read in one transaction (§14.4). tracker: the request log read so far
    (updated here); slow: the background snapshot (slow_snapshot), or None while it is first being worked out;
    running: the pid holding run.lock (crawler.lock_holder), stop_file: whether STOP is there."""
    tracker = tracker or Tracker()
    conn.execute("BEGIN")
    try:
        tracker.update(conn)
        speeds = tracker.speeds(now)
        plan = {r["site"]: dict(r) for r in conn.execute("SELECT site, name, imp, round, home, platform, base_urls "
                                                         "FROM site_plan")}
        # the one scan of url_result (about 0.13 s on 60k rows, its CASE for each row): totals, servers and ETA
        rows = conn.execute("""SELECT host, owner_site, tier, kind, result, count(*) n,
                                      sum(coalesce(added_at, 0) <= ?) old,
                                      max(CASE WHEN result = 'retry' THEN next_at END) last_retry
                               FROM url_result GROUP BY host, owner_site, tier, kind, result""",
                            (now - HOUR_S,)).fetchall()
        acts = conn.execute("""SELECT coalesce(u.owner_site, a.site) site, a.result, count(*) n
                               FROM action_result a LEFT JOIN url u ON u.id = a.list_id
                               WHERE a.state <> 'superseded' GROUP BY 1, 2""").fetchall()
        by_host = _by_host(rows)
        server_rows = _servers(conn, now, speeds, by_host)
        est = eta(conn, now, speeds, {h: (v["left"], v["last_retry"]) for h, v in by_host.items()})
        run = _run_state(conn, now, running, stop_file)
        errors = _errors(conn, now)
        n_requests = conn.execute("SELECT count(*) FROM request").fetchone()[0]
    finally:
        conn.execute("COMMIT")
    total, tiers, sites, buckets = _totals(rows, acts, plan, slow)
    for name, s in est["servers"].items():
        if name in server_rows:
            server_rows[name]["eta"] = s
    servers = sorted(server_rows.values(), key=lambda s: (-(s.get("eta") or {}).get("t", 0.0), -s["left"], s["queue"]))
    for s in servers:
        s["sites"] = (slow or {}).get("sites_by_queue", {}).get(s["queue"], [])
        s["site_names"] = [plan.get(k, {}).get("name") or k for k, _n in s["sites"][:3]]
    counts = Counter(s["state"] for s in sites)
    return {
        "at": now, "run": run, "total": total, "eta": est, "tiers": tiers, "servers": servers, "sites": sites,
        "site_counts": {"all": len(sites), "problem": counts["error"] + counts["look"], "crawling": counts["crawling"],
                        "unreconciled": counts["unreconciled"], "done": counts["done"],
                        "unchecked": counts["unchecked"]},
        "buckets": buckets, "page_labels": (slow or {}).get("page_labels", []),
        "polite": {**tracker.polite.result(), "requests": n_requests},
        "errors": errors, "campus": {"last": tracker.campus_last, "on": tracker.campus_on},
        "slow_at": (slow or {}).get("at"), "slow_error": (slow or {}).get("pages_error"),
        "checks": (slow or {}).get("checks"), "checks_error": (slow or {}).get("checks_error"),
        "missing_found": (slow or {}).get("missing_found", []), "round2": (slow or {}).get("round2"),
    }


# a row this round asked the server for (an answer, a failure): not still to fetch, not left out
_NOT_ASKED = ("todo", "retry", "lab", "later", "rule", "held", "too_large")


def _by_host(rows) -> dict[str, dict]:
    """url_result counted by host: the rows still to fetch (todo, retry), the last retry's time, the round-1 rows
    asked for and all round-1 rows."""
    out: dict[str, dict] = defaultdict(lambda: {"left": 0, "last_retry": None, "fetched": 0, "n": 0})
    for r in rows:
        h = out[r["host"]]
        if r["result"] in ("todo", "retry"):
            h["left"] += r["n"]
            if r["last_retry"] is not None:
                h["last_retry"] = max(h["last_retry"] or 0.0, r["last_retry"])
        if r["tier"] in ROUND1_TIERS:
            h["n"] += r["n"]
            h["fetched"] += r["n"] if r["result"] not in _NOT_ASKED else 0
    return out


def _servers(conn: sqlite3.Connection, now: float, speeds: dict[str, dict], by_host: dict[str, dict]
             ) -> dict[str, dict]:
    """Each server queue: its hosts, rows fetched and left, speed, Crawl-delay, pauses, cool-downs and failures."""
    out: dict[str, dict] = {}
    hosts = conn.execute("SELECT host, queue, crawl_delay, cooldown_until FROM host").fetchall()
    host_queue = {h["host"]: h["queue"] for h in hosts}
    counts = [{"host": host, "left_": v["left"], "fetched": v["fetched"], "n": v["n"]} for host, v in by_host.items()
              if v["n"] or v["left"]]
    pauses = {r["queue"]: (r["until"], r["why"]) for r in conn.execute("SELECT queue, until, why FROM pause "
                                                                       "WHERE until > ?", (now,))}
    fails = {r["queue"]: r for r in conn.execute("SELECT queue, conn_fail, err_fail FROM server")}
    presses, press_host = {}, {}
    for r in conn.execute("SELECT queue, count(*) n, max(host) host FROM action WHERE state IN ('pending', 'active') "
                          "GROUP BY queue"):
        presses[r["queue"]], press_host[r["queue"]] = r["n"], r["host"]
    def server(queue: str) -> dict:
        return out.setdefault(queue, {"queue": queue, "hosts": Counter(), "fetched": 0, "left": 0, "delay": 0.0,
                                      "cooling": []})

    for r in counts:
        s = server(host_queue.get(r["host"], "?"))
        s["hosts"][r["host"]] += r["n"]
        s["fetched"] += r["fetched"] or 0
        s["left"] += r["left_"] or 0
    for queue in presses:  # a server whose only work left is pressing buttons
        server(queue)
    for h in hosts:
        s = out.get(h["queue"])
        if s is None:
            continue
        if h["crawl_delay"]:
            s["delay"] = max(s["delay"], min(float(h["crawl_delay"]), 300.0))
        if (h["cooldown_until"] or 0) > now:
            s["cooling"].append((h["host"], h["cooldown_until"]))
    from nknu.archive.crawler import CONN_GIVE_UP

    for queue, s in out.items():
        s["left"] += presses.get(queue, 0)
        hosts_ = s.pop("hosts")
        s["main_host"] = hosts_.most_common(1)[0][0] if hosts_ else press_host.get(queue) or queue
        s["n_hosts"] = len(hosts_)
        s["speed"] = speeds.get(queue)
        pause = max((pauses.get(k, (0.0, None)) for k in (queue, ALL_QUEUES, net_key(network_of(queue)))),
                    key=lambda p: p[0])
        f = fails.get(queue)
        sign, words, color = "✓", "正常", "green"
        if queue == "dns-fail":
            sign, words, color = "⚠", "主机名查不到地址：隔一阵再查", "yellow"
        elif queue == "?":
            sign, words, color = "", "还没查地址：下一次开跑时先查", "none"
        elif f is not None and (f["conn_fail"] or 0) >= CONN_GIVE_UP:
            sign, words, color = "✗", f"连续 {f['conn_fail']} 次连不上：要人看是不是永久下线", "red"
        elif pause[0] > now:
            sign, words, color = "⚠", f"暂停中，{fmt_clock(pause[0], now)} 继续（{plain_error(None, pause[1])}）", "yellow"
        elif s["cooling"]:
            until = max(c[1] for c in s["cooling"])
            sign, words, color = "⚠", f"{len(s['cooling'])} 个主机名连续出错，{fmt_clock(until, now)} 继续", "yellow"
        elif f is not None and (f["err_fail"] or 0) > 0:
            sign, words, color = "⚠", f"连续出错 {f['err_fail']} 次（3 次就整台休息 15 分钟）", "yellow"
        sp = s["speed"] or {}
        share = sp.get("new_share")
        if share is not None and sp.get("new_known", 0) >= NEW_CONN_MIN and share > NEW_CONN_RED and color != "red":
            red = KEEPALIVE_HOST in hosts_
            sign, color = ("✗", "red") if red else ("⚠", "yellow")
            words = f"新连线占 {share:.0%}（连线保持可能没生效）" + ("" if words == "正常" else f"；{words}")
        s["status"] = {"sign": sign, "words": words, "color": color}
    return out


def _errors(conn: sqlite3.Connection, now: float) -> list[dict]:
    """The last hour's warnings and errors (events), and the requests that failed, grouped by what happened."""
    since = datetime.fromtimestamp(now - HOUR_S, UTC).isoformat(timespec="seconds")
    out = []
    for r in conn.execute("""SELECT at, level, code, host, url, message FROM event
                             WHERE level IN ('warn', 'error') AND at >= ? ORDER BY rowid DESC LIMIT ?""",
                          (since, EVENTS_SHOWN)):
        out.append({"at": _ts(r["at"]), "level": r["level"], "code": r["code"], "where": r["host"] or "",
                    "plain": plain_error(r["code"], r["message"]), "raw": (r["message"] or "")[:300], "n": 1})
    groups: dict[str, dict] = {}
    for r in conn.execute("""SELECT host, url, status, error, started FROM request
                             WHERE started > ? AND (error IS NOT NULL OR status >= 400) ORDER BY id DESC LIMIT 2000""",
                          (now - HOUR_S,)):
        key = error_code(None, r["error"], r["status"])
        g = groups.setdefault(key, {"at": r["started"], "level": "request", "code": key, "where": r["host"],
                                    "plain": plain_error(None, r["error"], r["status"]),
                                    "raw": (r["error"] or f"HTTP {r['status']}")[:300], "n": 0, "example": r["url"]})
        g["n"] += 1
    out += sorted(groups.values(), key=lambda g: -g["n"])
    return out


def _totals(rows, acts, plan: dict[str, dict], slow: dict | None):
    """The round's total, each tier, each site and the categories, from url_result counted by site, tier, kind and
    result (and the presses by site and result). The denominator leaves out rule, later and redirect (§14.2)."""
    round1 = {k for k, p in plan.items() if p["round"] == "1"}
    site = {k: {"site": k, "name": plan[k]["name"] or k, "home": plan[k]["home"], "tier": tier_of(plan[k]),
                "platform": plan[k]["platform"], "cats": defaultdict(Counter), "buckets": Counter(),
                "lab": 0, "failed": 0} for k in round1}
    total = {"done": 0, "all": 0, "all_hour_ago": 0, "lab": 0, "retry": 0}
    buckets = {b: [0, 0] for b in BUCKET_ORDER}  # [addresses, presses]
    for r in rows:
        if r["tier"] not in ROUND1_TIERS:
            continue
        result, n, cat = r["result"], r["n"], _cat(r["kind"])
        s = site.get(r["owner_site"])
        counted = result not in ("rule", "later", "redirect")
        if counted:
            total["all"] += n
            total["all_hour_ago"] += r["old"] or 0
            total["retry"] += n if result == "retry" else 0
            if result in LAB_RESULTS:
                total["lab"] += n
            elif result not in ("todo", "retry"):
                total["done"] += n
        if s is not None and counted:
            s["cats"][cat]["all"] += n
            if result not in ("todo", "retry", *LAB_RESULTS):
                s["cats"][cat]["done"] += n
        if _judged_page(r["kind"], r["tier"], result):
            continue  # a page shows its completeness (§14.2), never its own url_result
        b = outcome.user_bucket(result)
        buckets[b][0] += n
        if s is not None:
            s["buckets"][b] += n
            s["lab"] += n if result in LAB_RESULTS else 0
    for r in acts:
        b = outcome.user_bucket(r["result"])
        buckets[b][1] += r["n"]
        s = site.get(r["site"])
        if s is not None:
            s["buckets"][b] += r["n"]
    total["pct"] = (total["done"] / total["all"]) if total["all"] else 0.0
    pages_of = (slow or {}).get("site_pages", {})
    statuses = (slow or {}).get("statuses", {})
    have_res = (slow or {}).get("sites_with_res", set())
    failed_pages = (slow or {}).get("failed_pages", {})
    out_sites = []
    for k, s in site.items():
        levels = pages_of.get(k, Counter())
        bad_pages = sum(n for lv, n in levels.items() if lv not in ("complete", "complete_school", "waiting"))
        s["levels"] = dict(levels)
        s["bad_pages"] = bad_pages
        s["failed"] = s["buckets"]["failed"] + failed_pages.get(k, 0)
        s["missing"] = bad_pages + s["buckets"]["failed"] + s["buckets"]["review"] + s["buckets"]["pending_check"]
        st = _site_state(k, slow, statuses, have_res, levels)
        s.update(st)
        s["cats"] = {c: dict(v) for c, v in s["cats"].items()}
        s["buckets"] = dict(s["buckets"])
        s["lists"] = (slow or {}).get("lists", {}).get(k, {})
        s["js_types"] = (slow or {}).get("js_types", {}).get(k, 0)
        s["reconcile"] = (slow or {}).get("reconcile", {}).get(k)
        s["summary"] = _site_summary(s)
        out_sites.append(s)
    # what is missing (§14.6 「有缺 N 个（M 个会自动重试）」): rows waiting for a retry, the other addresses and presses
    # that are 连接失败, 要人看 or 待判定, and the pages that are not complete (a page still to fetch aside)
    total["lacking"] = (total["retry"] + sum(buckets[b][0] + buckets[b][1] for b in ("failed", "review", "pending_check"))
                        + sum(s["bad_pages"] for s in out_sites))
    # §14.2, record 二·7: 全校重要 first; in a group, those with 连接失败 and those missing more first
    out_sites.sort(key=lambda s: (s["tier"], s["failed"] == 0, -s["missing"], STATE_RANK.get(s["state"], 9),
                                  s["name"]))
    tiers = []
    for t in ROUND1_TIERS:
        group = [s for s in out_sites if s["tier"] == t]
        agg = {c: Counter() for c in ("page", "file", "image")}
        for s in group:
            for c, v in s["cats"].items():
                agg[c].update(v)
        tiers.append({"tier": t, "name": TIER_NAME[t], "sites": len(group),
                      "records_done": sum(s["state"] in ("unreconciled", "done") for s in group),
                      "done": sum(s["state"] == "done" for s in group),
                      **{c: (agg[c]["done"], agg[c]["all"]) for c in agg}})
    blist = [{"code": b, "zh": BUCKET_ZH[b], "then": BUCKET_THEN[b], "color": BUCKET_COLOR[b], "urls": v[0],
              "presses": v[1]} for b, v in buckets.items()]
    return total, tiers, out_sites, blist


def tier_of(p: dict) -> int:
    """A site_plan row's tier: 0 全校重要, 1 本系重要 (round1.TIER_OF_IMP)."""
    from nknu.archive.round1 import TIER_OF_IMP

    return TIER_OF_IMP.get(p.get("imp") or "", 1)


def _norm_status(raw: Any) -> tuple[str, list[str], dict]:
    """monitor.site_status's answer for one site, in the shapes it may take: a state code (error, look, crawling,
    unreconciled, done) or its words (出错 …), or a mapping with state (or status, code, level) and why (or reasons).
    Anything else is unchecked: never a colour this function made up."""
    extra: dict = {}
    if isinstance(raw, str):
        state, why = raw, []
    elif isinstance(raw, dict) or hasattr(raw, "keys"):
        d = dict(raw)
        state = next((d[k] for k in ("state", "status", "code", "level") if d.get(k)), "")
        why = d.get("why") or d.get("reasons") or []
        why = [why] if isinstance(why, str) else list(why)
        extra = {k: d.get(k) for k in ("cover_cc", "cover_old", "cc_only", "cover_line", "warnings")
                 if d.get(k) is not None}
    else:
        state = getattr(raw, "state", "") or ""
        why = list(getattr(raw, "why", []) or [])
    state = str(state).strip("✓⚠✗ ")
    state = _STATE_WORDS.get(state, state)
    if state not in STATE_SHOW:
        return "unchecked", [f"网站状态看不懂（{state or raw!r}）"], {}
    return state, [str(w) for w in why], extra


def _site_state(site: str, slow: dict | None, statuses: dict, have_res: set, levels) -> dict:
    """A site's state for the dashboard: monitor.site_status's (§10.5), but 没检查 while page completeness has not
    been worked out, when site_status could not be had, and when the site has pages but no resource row at all (the
    resource layer has no data yet: §10.5 「资源层还没有资料时显示『没检查』（黄），不显示绿」). A fetched page of a
    real site always has resource rows (its images and the school's scripts are page_res rows, need 'no' included), so
    a site with fetched pages and none at all is one whose resources were never recorded (【Claude 的执行办法】)."""
    if slow is None or slow.get("pages_error"):
        why = ["完整度还在算（第一次要几秒）"] if slow is None else [f"完整度算不出来：{slow['pages_error']}"]
        return {"state": "unchecked", "why": why}
    if slow.get("status_error"):
        return {"state": "unchecked", "why": [f"网站状态还没接上：{slow['status_error']}"]}
    if site not in statuses:
        return {"state": "unchecked", "why": ["网站状态没有这个网站"]}
    state, why, extra = _norm_status(statuses[site])
    fetched = sum(n for lv, n in levels.items() if lv != "waiting")
    if fetched and site not in have_res:
        note = "这个网站的网页都还没有资源纪录（资源层没资料）"
        if state in ("done", "unreconciled"):  # what says the records are complete: never without them
            return {"state": "unchecked", "why": list(dict.fromkeys([note, *why])), **extra}
        why = list(dict.fromkeys([*why, note]))
    return {"state": state, "why": why, **extra}


def _site_summary(s: dict) -> str:
    """One line of what a site lacks (§14.6), in words: the state's reasons first, then the counts."""
    parts = list(s.get("why") or [])[:2]
    b = s["buckets"]
    if b.get("failed"):
        parts.append(f"连接失败 {b['failed']} 个" + (f"（{s['lab']} 个只限校内）" if s.get("lab") else ""))
    if b.get("review"):
        parts.append(f"要人看 {b['review']} 个")
    if s.get("bad_pages"):
        parts.append(f"{s['bad_pages']} 页不完整")
    lists = s.get("lists") or {}
    if lists.get("bad"):
        parts.append(f"列表没翻完 {lists['bad']} 个")
    if s.get("js_types"):
        parts.append(f"{s['js_types']} 种网页要 JavaScript（下一轮补）")
    if s["levels"].get("unchecked"):
        parts.append(f"待判定 {s['levels']['unchecked']} 页")
    cats = s["cats"]
    counts = " · ".join(f"{zh} {cats[c].get('done', 0):,}/{cats[c]['all']:,}" for c, zh in
                        (("page", "网页"), ("file", "附件"), ("image", "图片")) if cats.get(c, {}).get("all"))
    if counts:
        parts.append(counts)
    return "；".join(dict.fromkeys(p for p in parts if p))


# ---------------------------------------------------------------- the background snapshot (§14.4)

def _call(fn, *args, **kw):
    """fn(*args, **kw) with only the keywords fn takes (monitor.site_status and monitor.checks take the pages, the
    states and the archive directory; a stand-in may take fewer)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kw)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kw)
    return fn(*args, **{k: v for k, v in kw.items() if k in params})


# the page_completeness columns the dashboard keeps (monitor.Page.of reads the same)
PAGE_COLUMNS = ("id, site, tier, content_id, page_type, page_role, own, level, label, reasons, ended_by, list_ok, "
                "list_walking, list_bad, list_unchecked, frame_missing, miss_file, miss_img, miss_other, n_failed, "
                "n_review")


def slow_snapshot(conn: sqlite3.Connection, now: float, *, site_status=None, checks=None, facts=None,
                  root: Path | None = None) -> dict:
    """What is too costly for every refresh: page_completeness (§10.4: about 1.4–2.8 s on 60k rows), each site's state
    (site_status, given these pages), the round-two list, which sites each server holds, list walks, pages of a type
    that needs JavaScript and the reconciliations, in one read transaction; then the cheap checks (given the same
    pages and states) in a second one, so that no single read lasts the whole job. facts: monitor.facts, read once
    (page_resource is as costly as page_completeness) and handed to both site_status and checks (fx=). A function the
    integration has not wired yet (site_status, checks) is said so, never guessed. root: the archive directory, for
    the checks."""
    out: dict[str, Any] = {"at": now}
    conn.execute("BEGIN")
    try:
        try:
            pages = conn.execute(f"SELECT {PAGE_COLUMNS} FROM page_completeness").fetchall()
        except sqlite3.Error as exc:
            pages, out["pages_error"] = [], f"{type(exc).__name__}: {exc}"
        out["pages"] = [dict(p) for p in pages]
        site_pages: dict[str, Counter] = defaultdict(Counter)
        failed_pages: Counter[str] = Counter()
        labels: Counter[str] = Counter()
        for p in out["pages"]:
            site_pages[p["site"]][p["level"]] += 1
            failed_pages[p["site"]] += p["label"] == "failed"
            labels[p["label"]] += 1
        out["site_pages"], out["failed_pages"] = dict(site_pages), dict(failed_pages)
        out["page_labels"] = [{"code": c, "zh": LABEL_ZH.get(c, c), "n": labels[c]} for c in PAGE_ORDER if labels[c]]
        out["sites_with_res"] = {r[0] for r in conn.execute(
            "SELECT DISTINCT u.owner_site FROM url u WHERE u.kind = 'page' AND u.tier IN (0, 1) "
            "AND EXISTS (SELECT 1 FROM page_res pr WHERE pr.page_id = u.id)")}
        lists: dict[str, Counter] = defaultdict(Counter)
        for r in conn.execute("SELECT site, ended_by, ok FROM listwalk"):
            ok = r["ended_by"] in outcome.LIST_ENDS_OK and r["ok"] != 0
            lists[r["site"]]["bad" if not ok and r["ended_by"] != "walking" else "ok" if ok else "walking"] += 1
        out["lists"] = {k: dict(v) for k, v in lists.items()}
        out["js_types"] = dict(conn.execute("SELECT site, count(*) FROM page_type WHERE verdict = 'js' "
                                            "AND n_pages > 0 GROUP BY site").fetchall())
        by_queue: dict[str, Counter] = defaultdict(Counter)
        for r in conn.execute("""SELECT h.queue, u.owner_site, count(*) n FROM url u JOIN host h ON h.host = u.host
                                 WHERE u.tier IN (0, 1) GROUP BY h.queue, u.owner_site"""):
            by_queue[r["queue"]][r["owner_site"]] += r["n"]
        out["sites_by_queue"] = {q: c.most_common() for q, c in by_queue.items()}
        out["reconcile"], out["missing_found"] = _reconciled(conn)
        out["round2"] = _round2(conn)
        raw: dict = {}
        fx = None
        if facts is not None and "pages_error" not in out and (site_status is not None or checks is not None):
            try:
                fx = facts(conn, out["pages"])
            except Exception as exc:  # noqa: BLE001 - each function then reads them itself, or says 没检查
                out["facts_error"] = f"{type(exc).__name__}: {exc}"
        if site_status is None:
            out["status_error"] = "monitor.site_status 还没有"
        elif "pages_error" not in out:
            try:
                raw = dict(_call(site_status, conn, now, pages=out["pages"], fx=fx))
            except Exception as exc:  # noqa: BLE001 - shown on the page as 没检查, never a colour of its own
                out["status_error"] = f"{type(exc).__name__}: {exc}"
        out["statuses"] = raw
    finally:
        conn.execute("COMMIT")
    if checks is None:
        out["checks"], out["checks_error"] = None, "monitor.checks 还没有"
        return out
    conn.execute("BEGIN")
    try:
        cs = _call(checks, conn, now, cheap=True, pages=None if "pages_error" in out else out["pages"],
                   statuses=raw or None, fx=fx, root=root)
        out["checks"] = [_norm_check(c) for c in cs]
    except Exception as exc:  # noqa: BLE001 - shown as 没检查
        out["checks"], out["checks_error"] = None, f"{type(exc).__name__}: {exc}"
    finally:
        conn.execute("COMMIT")
    return out


def _norm_check(c: Any) -> dict:
    """One of monitor.checks' items (monitor.Check: no, title, status, detail, lines), or a mapping of the same."""
    get = (lambda k: c.get(k)) if isinstance(c, dict) else (lambda k: getattr(c, k, None))
    result = get("result") or get("status") or "skip"
    lines = [str(x) for x in (get("lines") or [])]
    detail = "；".join(x for x in [get("detail") or "", *lines[:5]] if x) + ("……" if len(lines) > 5 else "")
    return {"no": get("no"), "title": get("title") or "", "result": str(result), "detail": detail}


# the checks whose items are addresses the crawl has no row for (§18.2b: R1 a menu item, R3a Common Crawl's, R4a the
# old archive's), as reconcile.py writes them in reconcile_site.checks: {"status", counts…, "items": [{"target" or
# "url", "status" (R1: missing or waiting)}], "more": the items past the ones kept}
FOUND_CHECKS = ("R1", "R3a", "R4a")


def _status_of(v) -> str | None:
    """A check's result in reconcile_site.checks: "status" (reconcile.py) or "result"."""
    if isinstance(v, dict):
        v = v.get("status") or v.get("result")
    return v.lower() if isinstance(v, str) else None


def _found(v: dict) -> tuple[int, list[str]]:
    """The addresses one of FOUND_CHECKS found the crawl has no row for, and how many there are in all (the items
    kept are the first reconcile.ITEMS_KEPT; its count "missing" says the rest)."""
    if isinstance(v.get("missing"), list):  # a plain list of addresses
        urls = [str(u) for u in v["missing"]]
        return len(urls), urls
    urls = [str(i.get("target") or i.get("url")) for i in v.get("items") or []
            if isinstance(i, dict) and (i.get("target") or i.get("url")) and i.get("status", "missing") == "missing"]
    n = v.get("missing") if isinstance(v.get("missing"), int) else len(urls) + int(v.get("more") or 0)
    return max(n, len(urls)), urls


def _reconciled(conn: sqlite3.Connection) -> tuple[dict, list[dict]]:
    """Each site's last reconciliation (reconcile_site) and the addresses it found the crawl has no result for (R1,
    R3a, R4a: FOUND_CHECKS), which 漏抓检查 lists under 要人看 (§10.2.1, §18.2b)."""
    rec, missing = {}, []
    for r in conn.execute("SELECT site, batch, at, checks, cover_cc, cover_old, gates_ok FROM reconcile_site"):
        try:
            checks = json.loads(r["checks"] or "{}")
        except ValueError:
            checks = {}
        if not isinstance(checks, dict):
            checks = {}
        failed = sorted(k for k, v in checks.items() if _status_of(v) == "fail")
        rec[r["site"]] = {"batch": r["batch"], "at": _ts(r["at"]), "gates_ok": r["gates_ok"],
                          "cover_cc": r["cover_cc"], "cover_old": r["cover_old"], "failed": failed}
        for item, v in checks.items():
            if item in FOUND_CHECKS and isinstance(v, dict):
                n, urls = _found(v)
                if n:
                    missing.append({"site": r["site"], "item": item, "n": n, "urls": urls[:10]})
    return rec, missing


def _round2(conn: sqlite3.Connection) -> dict:
    """What round two holds (§14.2): rows by why, by the seed list they came from, and the top off-campus domains."""
    by_why = [{"why": r["reason"] or "", "zh": ROUND2_ZH.get(r["reason"] or "", r["reason"] or "（没写原因）"),
               "n": r["n"], "sites": r["sites"]}
              for r in conn.execute("""SELECT reason, count(*) n, count(DISTINCT owner_site) sites FROM url
                                       WHERE state = 'later' GROUP BY reason ORDER BY n DESC""")]
    by_source = [dict(r) for r in conn.execute("""SELECT s.source, count(*) n FROM url u
                                                   JOIN seed_src s ON s.url = u.url
                                                   WHERE u.state = 'later' GROUP BY s.source ORDER BY n DESC""")]
    domains = [dict(r) for r in conn.execute("SELECT domain, n FROM offsite ORDER BY n DESC LIMIT 20")]
    return {"by_why": by_why, "by_source": by_source, "domains": domains, "total": sum(x["n"] for x in by_why)}


# ---------------------------------------------------------------- one site (§14.2: 点进去看明细)

def _no_row_result(need: str, why: str | None, res_id: int | None = None, action_id: int | None = None) -> str:
    """A page's resource with neither a url row nor a press there (outcome._resource_ctes, r00's branches): one that
    names a row or a press that is not there is lost; one that names none is what its why says."""
    why = why or ""
    if res_id is not None or action_id is not None:
        return "not_discovered"
    if need == "round2" and why.startswith("round2:"):
        return "later"
    if need == "must" and why.startswith("unhandled:"):
        return "unhandled"
    if why.startswith("out:login"):
        return "login"
    if why.startswith("out:"):
        return "rule"
    return "not_discovered"


def site_detail(conn: sqlite3.Connection, site: str, now: float, slow: dict | None, *, bucket: str | None = None,
                label: str | None = None) -> dict | None:
    """One site's pages, by their completeness (§14.2: 连接失败 first, 完整 last; an address that redirects is written
    on the page it lands on; the resources with no row, not_discovered and unhandled, under their page), and its other
    addresses and presses by the ten categories (抓到了 last). bucket, label: only that category, or that page label.
    None when the site is not on the list."""
    conn.execute("BEGIN")
    try:
        p = conn.execute("SELECT site, name, imp, round, home, platform, base_urls FROM site_plan WHERE site=?",
                         (site,)).fetchone()
        if p is None:
            return None
        info = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, url, title, page_type, final_url FROM url WHERE owner_site=? AND kind='page'", (site,))}
        came = dict(conn.execute("""SELECT final_url, count(*) FROM url WHERE owner_site=? AND final_url IS NOT NULL
                                    AND final_url <> url AND (status BETWEEN 300 AND 399 OR reason = 'moved_scheme'
                                         OR ',' || coalesce(flags, '') || ',' LIKE '%,js_redirect,%')
                                    GROUP BY final_url""", (site,)).fetchall())
        lost = defaultdict(list)
        for r in conn.execute("""SELECT pr.page_id, pr.res_key, pr.role, pr.need, pr.why, pr.res_id, pr.action_id
                                 FROM page_res pr
                                 JOIN url pg ON pg.id = pr.page_id
                                 LEFT JOIN url x ON x.id = pr.res_id LEFT JOIN action a ON a.id = pr.action_id
                                 WHERE pg.owner_site = ? AND pr.need IN ('must', 'round2')
                                   AND x.id IS NULL AND a.id IS NULL""", (site,)):
            res = _no_row_result(r["need"], r["why"], r["res_id"], r["action_id"])
            if res in ("not_discovered", "unhandled"):
                lost[r["page_id"]].append({"res_key": r["res_key"], "role": r["role"], "result": res,
                                           "zh": outcome.RESULT_LABEL[res], "why": r["why"] or ""})
        others = conn.execute("""SELECT r.id, r.url, r.title, r.kind, r.kind0, r.tier, r.state, r.reason, r.status,
                                        r.result, r.page_type, pt.why AS type_why
                                 FROM url_result r LEFT JOIN page_type pt ON pt.key = r.page_type
                                 WHERE r.owner_site = ?""", (site,)).fetchall()
        acts = conn.execute("""SELECT a.id, a.kind, a.state, a.reason, a.status, a.result, a.row_text, a.list_id,
                                      a.target
                               FROM action_result a LEFT JOIN url u ON u.id = a.list_id
                               WHERE coalesce(u.owner_site, a.site) = ? AND a.state <> 'superseded'""",
                            (site,)).fetchall()
        rec = conn.execute("SELECT batch, at, checks, cover_cc, gates_ok FROM reconcile_site WHERE site=?",
                           (site,)).fetchone()
        from nknu.archive import monitor  # the monitor's reading of it (fresh or stale, §10.5), not one of our own

        fr = monitor._frontier(conn)
        rec_now = monitor.reconciled(fr, monitor.Rounds(fr)).get(site) if rec is not None else None
    finally:
        conn.execute("COMMIT")
    pages = []
    for row in (x for x in (slow or {}).get("pages", []) if x["site"] == site):
        u = info.get(row["id"], {})
        landing = info.get(row["content_id"]) if row["content_id"] != row["id"] else None
        pages.append({**row, "url": u.get("url", ""), "title": u.get("title") or "", "zh": LABEL_ZH.get(row["label"]),
                      "level_zh": outcome.LEVEL_LABEL.get(row["level"], row["level"]),
                      "color": LABEL_COLOR.get(row["label"], "red"),
                      "reasons_zh": [LABEL_ZH.get(c, c) for c in (row["reasons"] or "").split(",") if c],
                      "came_from": came.get(u.get("url"), 0), "landing": (landing or {}).get("url"),
                      "lost": lost.get(row["id"], [])})
    order = {c: i for i, c in enumerate(PAGE_ORDER)}
    pages.sort(key=lambda x: (order.get(x["label"], -1), x["id"]))
    page_counts = Counter(x["label"] for x in pages)
    if label:
        pages = [x for x in pages if x["label"] == label]
    rows = []
    for r in others:
        if _judged_page(r["kind"], r["tier"], r["result"]):
            continue  # listed with its completeness above, or written on the page it lands on
        b = outcome.user_bucket(r["result"])
        rows.append({"what": "url", "id": r["id"], "url": r["url"], "title": r["title"] or "", "kind": r["kind"],
                     "result": r["result"], "zh": outcome.RESULT_LABEL.get(r["result"], r["result"]),
                     "reason": r["reason"] or "", "status": r["status"], "bucket": b,
                     "type": r["page_type"] if b == "pending_check" else None, "type_why": r["type_why"] or ""})
    for a in acts:
        rows.append({"what": "press", "id": a["id"], "url": a["target"], "title": a["row_text"] or "",
                     "kind": a["kind"], "result": a["result"], "zh": outcome.RESULT_LABEL.get(a["result"], a["result"]),
                     "reason": a["reason"] or "", "status": a["status"], "bucket": outcome.user_bucket(a["result"]),
                     "list_id": a["list_id"], "type": None, "type_why": ""})
    border = {b: i for i, b in enumerate(BUCKET_ORDER)}
    rows.sort(key=lambda x: (border.get(x["bucket"], -1), x["result"], x["reason"], x["what"], x["id"]))
    bucket_counts = Counter(x["bucket"] for x in rows)
    if bucket:
        rows = [x for x in rows if x["bucket"] == bucket][:MAX_ROWS]
    else:
        kept, seen = [], Counter()
        for x in rows:
            seen[x["bucket"]] += 1
            if seen[x["bucket"]] <= PER_BUCKET:
                kept.append(x)
        rows = kept
    state = _site_state(site, slow, (slow or {}).get("statuses", {}), (slow or {}).get("sites_with_res", set()),
                        (slow or {}).get("site_pages", {}).get(site, Counter()))
    return {"site": site, "name": p["name"] or site, "home": p["home"], "tier": tier_of(dict(p)),
            "round": p["round"], "platform": p["platform"], **state, "pages": pages[:MAX_ROWS],
            "page_counts": [(c, LABEL_ZH.get(c, c), page_counts[c]) for c in PAGE_ORDER if page_counts[c]],
            "rows": rows, "bucket_counts": [(b, BUCKET_ZH[b], bucket_counts[b]) for b in BUCKET_ORDER
                                            if bucket_counts[b]],
            "bucket": bucket, "label": label, "slow_at": (slow or {}).get("at"),
            "reconcile": None if rec is None else {
                "batch": rec["batch"], "at": _ts(rec["at"]), "gates_ok": rec["gates_ok"], "cover_cc": rec["cover_cc"],
                # §10.5: stale once a run, a rejudge or a person's verdict covered the site after it (review 10-26)
                "fresh": rec_now.fresh if rec_now is not None else None,
                "stale": rec_now.stale_words() if rec_now is not None else "",
                "cover_lines": rec_now.cover_lines() if rec_now is not None else [],
                "blind": rec_now.blind_words() if rec_now is not None else ""},
            "at": now}


# ---------------------------------------------------------------- the cache and the background thread (§14.4)

class Dashboard:
    """The numbers the viewer draws: snapshot() kept CACHE_S seconds, slow_snapshot() every SLOW_EVERY_S seconds in
    a daemon thread (started by the first main()), the request log read incrementally. Read-only throughout.
    site_status and checks default to the monitor's (monitor.site_status, monitor.checks) when it has them."""

    def __init__(self, root: Path, *, clock=time.time, site_status=None, checks=None, facts=None,
                 cache_s: float = CACHE_S, slow_every_s: float = SLOW_EVERY_S, background: bool = True):
        self.root, self.db = root, root / "frontier.db"
        self.clock, self.cache_s, self.slow_every_s, self.background = clock, cache_s, slow_every_s, background
        if site_status is None or checks is None or facts is None:
            from nknu.archive import monitor

            # the monitor's own, for what is not given: its facts read once and handed to both (fx=)
            site_status = site_status or monitor.site_status
            checks = checks or monitor.checks
            facts = facts or monitor.facts
        self.site_status, self.checks, self.facts = site_status, checks, facts
        self.tracker = Tracker()
        self._lock = threading.Lock()
        self._main: dict | None = None
        self._main_at = float("-inf")
        self.slow: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: sqlite3.Connection | None = None  # main()'s, kept between refreshes (connect() costs ~25 ms)
        self._conn_id: tuple[int, int] | None = None

    def _running(self) -> tuple[str | None, bool]:
        from nknu.archive.crawler import lock_holder

        return lock_holder(self.root), (self.root / STOP_FILE).exists()

    def _main_conn(self) -> sqlite3.Connection:
        """main()'s connection, kept between refreshes: in autocommit it holds no lock between them, so the crawler
        never waits for it. Opened again when the file at the path is another one (a new archive: the request log
        is read again from its start) or after an error."""
        st = self.db.stat()  # FileNotFoundError when there is none
        ident = (st.st_dev, st.st_ino)
        if self._conn is not None and self._conn_id == ident:
            return self._conn
        self._close_conn()
        conn = connect(self.db)
        if self._conn_id is not None:
            self.tracker.reset()
        self._conn, self._conn_id = conn, ident
        return conn

    def _close_conn(self) -> None:
        if self._conn is not None:
            with suppress(sqlite3.Error):
                self._conn.close()
        self._conn = None

    def main(self) -> dict:
        """The numbers, at most CACHE_S seconds old; a problem reading the file is said, not raised."""
        self._start()
        with self._lock:
            now = self.clock()
            if self._main is not None and now - self._main_at < self.cache_s:
                return self._main
            try:
                conn = self._main_conn()
                running, stop = self._running()
                self._main = snapshot(conn, now, tracker=self.tracker, slow=self.slow, running=running,
                                      stop_file=stop)
            except (FileNotFoundError, OutdatedFrontier, sqlite3.Error) as exc:
                self._close_conn()
                return {"at": now, "problem": _problem(exc, self.db)}
            self._main_at = now
            return self._main

    def site(self, key: str, *, bucket: str | None = None, label: str | None = None) -> dict | None:
        try:
            conn = connect(self.db)
        except (FileNotFoundError, OutdatedFrontier, sqlite3.Error) as exc:
            return {"problem": _problem(exc, self.db)}
        try:
            return site_detail(conn, key, self.clock(), self.slow, bucket=bucket, label=label)
        finally:
            conn.close()

    def refresh_slow(self) -> dict | None:
        """Work out the background snapshot now (the thread's step; tests call it directly)."""
        try:
            conn = connect(self.db)
        except (FileNotFoundError, OutdatedFrontier, sqlite3.Error):
            return None
        try:
            slow = slow_snapshot(conn, self.clock(), site_status=self.site_status, checks=self.checks,
                                 facts=self.facts, root=self.root)
        except sqlite3.Error as exc:
            slow = {"at": self.clock(), "pages_error": f"{type(exc).__name__}: {exc}", "pages": []}
        finally:
            conn.close()
        with self._lock:
            self.slow = slow
            self._main_at = float("-inf")  # the next main() shows it
        return slow

    def _start(self) -> None:
        if not self.background or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="progress-slow", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh_slow()
            self._stop.wait(self.slow_every_s)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        with self._lock:
            self._close_conn()


def _problem(exc: Exception, db: Path) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"找不到待抓清单 {db}：还没 seed，或资料夹不对（--dir）"
    if isinstance(exc, OutdatedFrontier):
        return "这个待抓清单是旧版程序写的，看板读不了：先用新版 archive seed 或 archive run 打开一次，会自动补上"
    return f"读不了待抓清单（{type(exc).__name__}: {exc}）：过一会儿会自动再试"


# the commands the dashboard shows to copy (§11; no button: the dashboard writes nothing)
COMMANDS = {
    "stop": "nknu archive stop",
    "run": ("set -a; source ~/.config/nknu/google.env; set +a      # 载入 Drive 金钥，不会显示\n"
            "nohup caffeinate -is .venv/bin/nknu archive run >> archive/run.log 2>&1 < /dev/null &"),
    "lab": "export NKNU_SCHOOL_LOCK=/var/lib/nknu/school-requests.lock   # 在实验室：和 /etc/nknu/env 里写的同一个档",
    # §18.3 step 4 (the order after a run stopped): the browser check, the reconciliation with R6, the acceptance
    "after": (".venv/bin/nknu archive jscheck                 # 浏览器判定网页类型（只判定，不抓）\n"
              ".venv/bin/nknu archive reconcile --batch <批>   # 对账，含浏览器抽查 R6\n"
              ".venv/bin/nknu archive verify --golden research/project_audit_2026-10-25/web_search_baseline_25q.json"),
}
