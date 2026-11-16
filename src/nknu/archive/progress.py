"""The progress dashboard's numbers (crawler dashboard v4: docs/superpowers/specs/2026-11-04-crawler-dashboard-design.md
and the owner's 11-04 notes): the sites of the run, grouped into server cards by the server their home is on; each
site's and each card's progress (the rows and presses with a result over those known), busy time (用时, h:mm:ss) and
state (在抓, 休息中, 连不上 …); and 错 N, what the crawler stopped at and will not take up again by itself, each thing
once, with the problem log those numbers come from. viewer.py draws them; nothing here writes the database
(write_problem_log writes reports/ beside it).

Never slowing the crawler (design §2.8):
- every read goes through connect(): `mode=ro` (never `immutable=1`, which drops the locks and reads half-written
  pages), `PRAGMA query_only`, a 2-second busy timeout; the result views go to the connection's own temp schema
  (outcome.create_views(temp=True)), so nothing reaches the file;
- one short read transaction per refresh (BEGIN … COMMIT), kept CACHE_S seconds, that reads no result view (the
  indexes url_owner, url_next2 and url_added answer its counts): a long reader would keep the WAL file growing
  (SQLite's WAL notes: checkpoint starvation);
- the request log is read incrementally by request.id (Tracker: the politeness check, the busy time, each queue's
  newest request), once in full when the dashboard starts;
- 错 N needs page_completeness and each site's problem list: a background thread works them out every SLOW_EVERY_S
  seconds (slow_snapshot), with the server events and whether this machine is on campus;
- the link table is never read.

Every function takes the read-only connection and the time and gives back plain data; Dashboard holds the cache, the
tracker and the thread. Times are Asia/Taipei's (fmt_clock: 今天 22:10); lengths of time are h:mm:ss (fmt_hms).

What this module takes from others (imported where it is used, never copied):
- monitor.facts(conn, pages) -> monitor.Facts and monitor.site_status(conn, now, pages=…, fx=…) -> {site: SiteStatus}:
  each round-1 site's state (the site page: _norm_status) and its problem list (issues), to which problems() adds the
  four rules of design §2.4 the monitor lacks; monitor.STALL_S, monitor.BROWSER_PURPOSES and monitor.tone;
- monitor.checks(conn, now, cheap=True, …) for the acceptance page (checks_view), worked out when it is opened;
- crawler.lock_holder (the pid in run.lock: a run's only when that run wrote the last run_start, _run_state), deny_dir,
  CONN_GIVE_UP and MAX_ATTEMPTS; google_api's block streak (block_state, block_long).
"""

from __future__ import annotations

import bisect
import inspect
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from nknu.archive import google_api, outcome
from nknu.archive.frontier import ALL_QUEUES, Frontier, OutdatedFrontier, _missing, net_key, network_of

TZ = ZoneInfo("Asia/Taipei")
REFRESH_S = 10  # design §2.4: progress, busy time and states every 10 seconds (the page asks this often)
CACHE_S = 10.0  # the numbers are kept this long: several tabs share one reading
SLOW_EVERY_S = 120.0  # design §2.4: 错 N (page_completeness, each site's problem list) every 2 minutes, in the background
STALE_S = 120.0  # design §1.1: a page that got no new numbers this long greys out (看不到最新资料)
BUSY_MS = 2000
TOLERANCE_S = 0.05  # monitor.TOLERANCE_S
MIN_GAP_S = 2.0  # monitor.MIN_GAP_S: a run whose run_start says no gap
EXAMPLES = 20  # politeness warnings kept as examples (the counts are all)
POLITE_KEPT = 200  # politeness violations kept for the log (the counts are all)
MAX_ROWS = 3000  # one site's page lists at most this many rows of one kind
PER_BUCKET = 200  # one site's page, unfiltered: this many rows of each category
TIER_NAME = {0: "全校重要", 1: "本系重要"}
ROUND1_TIERS = (0, 1)
STOP_FILE = "STOP"
SEGMENT_EXTRA_S = 60.0  # design §2.3: a request counts until the next one starts, at most its own time and this long
DISCOVER_S = 600.0  # design §1.1: 发现中 while a site gained rows in the last 10 minutes
ALIVE_LATE_S = 180.0  # design §2.5 rule 2: a run that holds run.lock and wrote no beat this long hangs (爬虫没反应)
QUIET_S = 120.0  # design §2.5 rule 8: 在抓 while the queue's last request is this recent (past its Crawl-delay)
# a run that ended waiting and has not started again this long past its retry time is no planned pause (没重开):
# run_loop.sh starts the next run half a minute after it (review 11-04) 【Claude 的执行办法】
RESTART_LATE_S = 600.0
BIG_CARD = 3  # a server home to this many sites of the run is a card of its own; those home to one or two share one
EVENTS_KEPT = 500  # the problem log's server events: the newest this many

# a site's state (design §10.5), as the dashboard shows it: sign, words, colour (§14.5: never colour alone)
STATE_SHOW = {
    "error": ("✗", "出错", "red"),
    "look": ("⚠", "要看", "yellow"),
    "crawling": ("", "还在抓", "none"),
    "unreconciled": ("⚠", "纪录齐了，还没对账", "yellow"),
    "done": ("✓", "爬完", "green"),
    "unchecked": ("⚠", "没检查", "yellow"),
}
# crawling says only that rows wait: while no run of the site goes, the site page words it so (site_run; review 11-04)
UNFINISHED_SHOW = ("", "没抓完", "none")
_STATE_WORDS = {"出错": "error", "要看": "look", "还在抓": "crawling", "纪录齐了，还没对账": "unreconciled",
                "爬完": "done", "没检查": "unchecked", "red": "error", "green": "done"}
# the order one site's pages are listed in (§14.2: 连接失败 → 要人看 → … → 完整), by page_completeness.label
PAGE_ORDER = ("failed", "thin", "review", "list", "list_unchecked", "miss_file", "miss_img", "miss_other", "unchecked",
              "js", "click", "round2_img", "round2", "retry", "todo", "complete_school", "complete")
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
               "miss_other": "yellow", "click": "yellow", "round2_img": "yellow", "round2": "yellow",
               "unchecked": "yellow",
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
    # design §23 B3: no url row of its own (page_res.why), counted by picture (_round2)
    "round2:google_sites_image": "协作平台正文图片",
}
# what round two holds that has no url row: a page's resource by its page_res.why, counted once per page and picture
# (count DISTINCT res_key: a picture's key names its page, design §23 B2)
ROUND2_RES = ("round2:google_sites_image",)
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

# the cards (design §1.1, §2.1): the servers home to one or two sites of the run share one; a server home to none is a
# small card by its host names, with what the sites fetch there (counted for those sites too, so only 完成/总数)
UNITS = "units"
UNITS_NAME = "各单位自己的主机"
GSITES_NAME = "Google 协作平台"
SMALL_NAME = {"google": "Google 下载和表单", "school": "学校首页等", "offsite": "校外网站"}
WAIT_ZH = {"google": "等 Google", "school": "等学校首页", "offsite": "等校外网站"}
GOOGLE_QUEUES = (google_api.QUEUE, google_api.FORMS_QUEUE)
GOOGLE_DOMAINS = ("google.com", "googleapis.com", "googleusercontent.com", "gstatic.com", "goo.gl", "forms.gle")
# names that do not resolve: the crawler keeps them all on one queue, dns-fail, which went whole to the small card any
# one of its names picked (review 11-04: an off-campus name under 学校首页等); the dashboard puts each on its kind's
DNS_QUEUES = ("dns-fail", "dns-fail:google", "dns-fail:school", "dns-fail:offsite")
NO_WORKER = ("?", "", *DNS_QUEUES)  # a host nobody looked up yet, or whose name does not resolve: no state of its own
# design §2.2: the rows the crawl knows it must fetch, and those with a result (failed and held count in 错 too); a
# press is known until a restart replaced it (superseded)
KNOWN = ("pending", "active", "done", "failed", "held")
FINISHED = ("done", "failed", "held")
PRESS_FINISHED = ("done", "failed", "mismatch")
# a queue's state (design §2.5): a card says the worst of its queues' (§2.1 「取里面最坏的那台」), a red one before a
# yellow one before the rest, and of one colour the first of these
STATE_ORDER = ("offline", "no_key", "google_block", "conn", "rest", "stuck", "quiet", "working", "retry_wait")
_COLOR_RANK = {"red": 0, "yellow": 1}
# the problem log (design §1.3, §2.4): what a line is, and its words by code (outcome.RESULT_LABEL, and a page's own)
KIND_ZH = {"failed": "失败", "review": "要人看", "pending_check": "待判定"}
CODE_ZH = {**outcome.RESULT_LABEL,
           "lab": "连不上（只有校内连得到的主机，在校内也连不上）",
           "redirect": "一直跳转不落地",
           "rule": "网页跳到规则不抓的地方（要人看规则或名单）",
           "list": "列表没翻完",
           "frame_missing": "框架页一个都没抓到",
           "unexplained": "网页不完整，规则说不出原因"}
PRESS_ZH = {"pager": "翻页按钮", "detail": "内文按钮", "download": "下载按钮", "click": "按钮", "data": "资料网址"}
POLITE_ZH = {"overlap": "同时连两条", "gap": "间隔太短", "delay": "没照网站要求的间隔（Crawl-delay）"}
# warnings that are notes, never a server's trouble (design §1.3: 服务器事件 holds the rest of warn and error)
NOTE_EVENTS = ("robots_unparsed", "robots_redirect", "scheme_switch", "soft404_host", "campus")
LATEST_LOG = "problems-latest.log"
LOG_COLUMNS = ("时间", "服务器", "网站", "编号", "类别·说明", "代码", "细节", "网址")

# ---------------------------------------------------------------- plain words (§14.5)

# an error code (url.reason, crawler.error_kind, an event's code) → what happened and what happens next, in plain words
PLAIN = {
    "dns": "找不到这台主机的地址（DNS 查不到）：隔一阵再查，查了约一小时（4 次）还不行就算连接失败",
    "connect": "连不上这台服务器：整台暂停 15 分钟，不扣重试次数",
    "conn_pause": "连不上这台服务器：整台暂停 15 分钟，不扣重试次数",
    "tls": "加密连线握手失败：算这个网址失败，隔 30 分钟再试，共 3 次",
    "tls_cert": "证书检查没过（过期、设定错或缺中继证书）：算这个网址失败，3 次后列进「连接失败」",
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
    "server_down": ("服务器连续 8 次连不上（约 2 小时）：从没回过话的，它的网址不再等，记成连接失败（用户 11-08 定的），"
                    "之后才找到的也一样，它回话了就自动放回；回过话的照旧等，要人看是不是永久下线"),
    "host_unreachable": "从没在校外回过我们的学校主机连不上：等搬到实验室机器再试",
    "offline": "本机网络或学校网络像是断了：暂停，恢复后自动接着抓",
    "online": "网络恢复了：接着抓",
    "dns_gave_up": "主机名查了约一小时（4 次）都查不到：它的网址记连接失败（nknu archive retry dns 放回去再查）",
    "robots_5xx": "robots.txt 回了出错：照常抓，规则照旧",
    "robots_unreachable": "robots.txt 读不到：这台主机等一小时再试",
    "robots_redirect": "robots.txt 跳到别处：当作没有",
    "robots_unparsed": "robots.txt 看不懂：当作没有",
    "host_denies": "同一个目录连续被拒：那个目录先暂缓 15 分钟，同主机其他目录照常",
    "scheme_switch": "http 和 https 自动改试：照能连的那一种抓",
    "soft404_host": "这台主机不存在的网页也回 200：长得一样的页面会被标出来",
    "key_check": "Google 不接受金钥：云端硬盘和日历这次先不读，不算失败",
    # Google's protection and limits (design §23 D3): never 「隔 30 分钟再试，共 3 次」
    "google_block": "Google 暂时挡下载：整条 Google 队至少休息 1 小时，连续被挡就加倍，不扣重试次数",
    "google_file_quota": "Google：这个档案的下载次数被用完：只有这一个档案 24 小时后再试，3 次还不行就要人看",
    "google_block_long": "Google 连续挡下载超过 3 天或 6 次：要人看（不绕过：不换金钥、不换 IP、不伪装）",
    # a 5xx of Google's API (backendError, internalError …: §7.6), Google's server, never the school's (§23.9 D3)
    "google_5xx": "Google 服务器出错：Google 那条队休息 15 分钟，这个网址隔 30 分钟再试，共 3 次",
    "api_error": f"{outcome.RESULT_LABEL['api_error']}：照它的回应记下，归「要人看」",
    # Google Sites' runtime pictures (design §23 B2, B3)
    "gsimg_reread": "协作平台正文图片的网址过期（抓到一半开始 403）：重抓这一页拿新网址，每页最多 2 次",
    "gsimg_giveup": ("协作平台正文图片：重抓页面以后最先试的 2 张都被拒（403）：这个网站剩下的这种图不再发请求，"
                     "记下一轮用浏览器补（列在已知缺口和「下一轮」）"),
    "gsimg_reread_failed": ("协作平台网页为拿新图片网址重抓，没拿到网页（403、出错或逾时）：这一页保留原来存好的答案、不扣重试"
                            "次数，没拿到的正文图片记下一轮用浏览器补（列在已知缺口和「下一轮」）"),
    "campus": "校内还是校外网络的判断",
    "stuck": "有该抓的网址却没有服务器接得了：爬虫的 bug，这次 run 停下",
    "left_active": "有网址卡在「抓取中」没人处理：爬虫的 bug，已记成失败，可以 retry crash 放回",
    "no_school_lock": "没拿到全机锁就要发请求：程序的 bug，这次 run 停下",
    "warc_write": "存档写不进去：这次 run 停下，修好存档资料夹再跑",
    "disk_low": "硬碟空间不到 5 GB：这次 run 停下",
    "db_error": "待抓清单读写出错：这次 run 停下",
    "db_busy": "待抓清单一时被另一条连线占着：这个网址 1 分钟后再抓（按钮算没拿到回应），爬虫照常跑",
    "crash": "程序出错（爬虫的 bug）：这一行记下原因",
    "idle_closed": "闲置的连线被服务器关掉：在新连线上重发 1 次，不算出错",
    "fetch": "抓取失败：隔 30 分钟再试，共 3 次",
    # the other warnings of the problem log's 服务器事件 (design §1.3), in words of their own
    "robots_tls_cert": "robots.txt 的证书检查没过：当作没有新规则，一小时后再读；网址照抓，失败的各自记下",
    "host_denies_parked": ("同一个目录等过以后又连续被拒：那个目录剩下的网址不再发请求，记「要人看」"
                           "（nknu archive retry denied_dir 放回）"),
    "reconcile_failed": "抓完一个网站后自动对账出错：停下以后 nknu archive reconcile 会再对一次",
    "postback_no_warc": "列表页没有存档位置，它的按钮没法按（爬虫的 bug）",
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
_API_HOST_FIRST = re.compile(r"^[\w.-]+:\s+(?=api:)")  # a pause's why: "www.googleapis.com: api:automated_queries"
_API_SERVER = frozenset({"backendError", "internalError", "backendFailure", "INTERNAL", "UNAVAILABLE"})


def _api_code(text: str | None, status: int | None = None) -> str | None:
    """The plain-words key of a Google API reason as the crawler writes it (url.reason, a pause's why: api:<reason>,
    the host first in a pause's why; design §23 D3): a block (google_api.BLOCK_REASONS, a 429), one file's downloads
    used up, a 5xx (Google's server: google_5xx, never the school's, §23.9 D3), else what api_error says; None when
    the text is no api: reason."""
    t = _API_HOST_FIRST.sub("", (text or "").strip(), count=1)
    if not t.startswith("api:"):
        return None
    reason = re.split(r"[\s,;()]", t[4:], maxsplit=1)[0]
    if reason == google_api.FILE_QUOTA_REASON:
        return "google_file_quota"
    if reason in google_api.BLOCK_REASONS or reason == "http_429" or status == 429:
        return "google_block"
    if reason.startswith("http_5") or reason in _API_SERVER or (status or 0) >= 500:
        return "google_5xx"
    return "api_error"


def error_code(code: str | None, message: str | None = None, status: int | None = None) -> str:
    """The plain-words key of a failure: its code as the crawler wrote it (url.reason, an event's code, error_kind),
    else what its free-text message or its status says; '' when nothing is known."""
    c = (code or "").strip()
    if (api := _api_code(c, status)) is not None:
        return api
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
    if (api := _api_code(text, status)) is not None:
        return api
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


def fmt_hms(seconds: float | None) -> str:
    """A length of time as hours:minutes:seconds (the owner 11-04: 「用时几分几秒用 2:47:18 这种时间来表示就够了」):
    0:00:05, 2:47:18, past a day 26:05:09; — for None."""
    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def fmt_pct(finished: int, known: int) -> str:
    """A progress percentage (design §2.2), rounded down so that only nothing left says 100%: one decimal below 10%
    and from 99% on (0.4%, 99.9%), whole numbers between. Nothing known is nothing left: 100%."""
    if finished >= known:
        return "100%"
    tenths = 1000 * max(finished, 0) // known
    if tenths < 100 or tenths >= 990:
        return f"{tenths / 10:.1f}%"
    return f"{tenths // 10}%"


def _hm(ts: float | None, now: float) -> str:
    """A moment in a short state word (休息中 02:40, 等重试 明天 02:40): the clock time today, else fmt_clock's."""
    if ts is None:
        return "—"
    if datetime.fromtimestamp(ts, TZ).date() == datetime.fromtimestamp(now, TZ).date():
        return f"{datetime.fromtimestamp(ts, TZ):%H:%M}"
    return fmt_clock(ts, now)


def fmt_stamp(ts: float | None) -> str:
    """A moment in the problem log file: 2026-11-04 03:20:05, Taipei's."""
    return "—" if ts is None else f"{datetime.fromtimestamp(ts, TZ):%Y-%m-%d %H:%M:%S}"


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


# ---------------------------------------------------------------- the request log, read incrementally (§2.3, §2.6)

def _server(ip: str | None, queue: str | None) -> str:
    """monitor._server: the address a request answered from, else its queue's."""
    if ip:
        return ip
    return (queue or "").removeprefix("ip:")


def _polite_key(queue: str | None, ip: str | None) -> str:
    """The server one connection at a time is kept to (design §2.6): Google's two named queues by their names (google
    and googleapis may reach one Google address, each queue at its own pace: never 同时连两条), any other by the
    address it answered from (monitor._server; monitor.politeness keeps the address for those two as well)."""
    if queue in GOOGLE_QUEUES:
        return queue
    return _server(ip, queue)


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
    """The politeness check (design §2.6), fed the request log a piece at a time, in order (each server's requests come
    from one worker, one after another): the rules of monitor.politeness and monitor.politeness_warnings, so feeding it
    in two pieces or at once gives the same answer, and the monitor's full scan's (Google's two queues aside, which are
    told apart by name here: _polite_key).

    Hard: two requests to one server overlapping, a gap below the run's least, a Crawl-delay not kept; each one a
    record for the problem log (the last POLITE_KEPT: at, kind, server, detail, url). Warning: after a slow answer, a
    shorter wait than the answer took (at most 30 s); EXAMPLES of them are kept. The counts are all."""

    def __init__(self, examples: int = EXAMPLES, kept: int = POLITE_KEPT):
        self._last: dict[str, tuple[float, float, str]] = {}  # server → (started, latest end, url)
        self._prev: dict[str, tuple[float, float]] = {}  # server → the last request's own (started, ended)
        self._host_end: dict[str, float] = {}
        self.counts: Counter[str] = Counter()
        self.records: deque[dict] = deque(maxlen=kept)
        self.warn: deque[str] = deque(maxlen=examples)
        self.seen = 0

    def _hard(self, kind: str, at: float, server: str, detail: str, url: str) -> None:
        self.counts[kind] += 1
        self.records.append({"at": at, "kind": kind, "server": server, "detail": detail, "url": url})

    def feed(self, rows, runs: list[tuple[float, float]], delays: dict[str, float]) -> None:
        """rows: request rows (ip, queue, host, url, started, ended) in the order they were made."""
        for r in rows:
            self.seen += 1
            key, host, url = _polite_key(r["queue"], r["ip"]), r["host"], r["url"]
            started, ended = r["started"], r["ended"]
            prev = self._last.get(key)
            if prev is not None:
                least = _least_gap(runs, started)
                if started < prev[1] - TOLERANCE_S:
                    self._hard("overlap", started, key, f"上一个请求还没结束就发了这个（上一个：{prev[2]}）", url)
                elif started - prev[1] < least - TOLERANCE_S:
                    self._hard("gap", started, key, f"只隔 {started - prev[1]:.2f} 秒（下限 {least:g} 秒）", url)
            delay = delays.get(host)
            if delay and host in self._host_end and started - self._host_end[host] < delay - TOLERANCE_S:
                self._hard("delay", started, key,
                           f"{host} 要求每次隔 {delay:g} 秒，只隔 {started - self._host_end[host]:.1f} 秒", url)
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
                "delay": self.counts["delay"], "slow": self.counts["slow"], "records": list(self.records),
                "warn_examples": list(self.warn)}


def scope_site(scope: str | None) -> str | None:
    """The site a request was made for, from request.scope as the crawler writes it (admit:<verdict>:<site>:<kind0>,
    Crawler._admitted_as); None for any other (campus, a browser check's) and for a row of no site ('-')."""
    if not scope or not scope.startswith("admit:"):
        return None
    rest = scope[len("admit:"):]
    if rest.count(":") < 2:
        return None
    site = rest.split(":", 1)[1].rsplit(":", 1)[0]
    return None if site in ("", "-") else site


def _segment(last: tuple[float, float, str | None, float], until: float) -> float:
    """One request's share of its queue's busy time: from its start to `until` (the next request's start), at least
    its own time and at most its own time plus its extra (design §2.3)."""
    started, ended, _site, extra = last
    return max(0.0, min(max(until, ended) - started, ended - started + extra))


class Busy:
    """用时 (design §2.3): each request counts from its start to the next request's start on its queue (its own time
    and the polite wait after it), at most its own time plus SEGMENT_EXTRA_S (its host's Crawl-delay + 5 s when that
    is longer): a server's rest, a pause, a wait for a retry and a stopped run never count. A segment goes to (queue,
    the site its request.scope names); browser checks (monitor.BROWSER_PURPOSES) are no crawling and are left out. Fed
    the request log a piece at a time, in order, it adds up the same as fed at once."""

    def __init__(self):
        self.closed: Counter[tuple[str, str | None]] = Counter()
        self._last: dict[str, tuple[float, float, str | None, float]] = {}  # queue → (started, ended, site, extra)

    def feed(self, rows, delays: dict[str, float]) -> None:
        """rows: request rows (queue, host, purpose, started, ended, scope) in the order they were made; delays: each
        host's Crawl-delay (crawl_delays)."""
        from nknu.archive.monitor import BROWSER_PURPOSES

        for r in rows:
            if r["purpose"] in BROWSER_PURPOSES:
                continue
            queue = r["queue"] or "?"
            prev = self._last.get(queue)
            if prev is not None:
                self.closed[(queue, prev[2])] += _segment(prev, r["started"])
            extra = max(SEGMENT_EXTRA_S, delays.get(r["host"], 0.0) + 5.0)
            self._last[queue] = (r["started"], max(r["ended"], r["started"]), scope_site(r["scope"]), extra)

    def seconds(self, open_until: float | None = None, ends: list[float] | tuple = ()) -> dict:
        """Seconds per (queue, site): the closed segments, and each queue's last request up to the first run_end after
        its start (ends: the run_ends' times, in order), or, in the run going on, up to open_until (now, while a run
        holds run.lock; None: its own time only)."""
        out: dict[tuple[str, str | None], float] = dict(self.closed)
        for queue, last in self._last.items():
            i = bisect.bisect_left(ends, last[0])
            until = ends[i] if i < len(ends) else open_until
            key = (queue, last[2])
            out[key] = out.get(key, 0.0) + _segment(last, last[1] if until is None else until)
        return out


class Tracker:
    """The request log read incrementally by request.id (one full scan when the dashboard starts, then only the new
    rows): the politeness check (design §2.6), the busy time (§2.3), each queue's newest request (卡住, §2.5) and
    where the last request was made from (campus)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.last_id = 0
        self.polite = Politeness()
        self.busy = Busy()
        self.last_started: dict[str, float] = {}
        self.campus_last: str | None = None
        self.campus_on = 0

    def update(self, conn: sqlite3.Connection) -> int:
        """Read the requests made since the last update; returns how many. A log that went back (another archive,
        or a fresh one) starts the tracker again."""
        from nknu.archive.monitor import BROWSER_PURPOSES

        with self.lock:
            top = conn.execute("SELECT coalesce(max(id), 0) FROM request").fetchone()[0]
            if top < self.last_id:
                self.reset()
            rows = conn.execute("SELECT id, queue, host, url, purpose, started, ended, ip, attempt, campus, scope "
                                "FROM request WHERE id > ? ORDER BY started, id", (self.last_id,)).fetchall()
            if not rows:
                return 0
            delays = crawl_delays(conn)
            self.polite.feed(rows, run_gaps(conn), delays)
            self.busy.feed(rows, delays)
            for r in rows:
                if r["purpose"] not in BROWSER_PURPOSES:
                    queue = r["queue"] or "?"
                    self.last_started[queue] = max(self.last_started.get(queue, r["started"]), r["started"])
                if r["campus"] == "on":
                    self.campus_on += 1
                self.last_id = max(self.last_id, r["id"])
            last = max(rows, key=lambda r: r["id"])
            if last["campus"]:
                self.campus_last = last["campus"]
            return len(rows)


# ---------------------------------------------------------------- the sites of the run and their cards (§2.1, §2.7)

@dataclass
class Card:
    """A server card (design §1.1, §2.1): the queues its sites' homes are on and those sites; or, small, queues home to
    no site of the run (Google's downloads and forms, the school's home page and its other hosts, off campus), whose
    rows count for the sites that use them. tip: the host names and addresses, for the hover text only."""

    key: str
    name: str
    small: bool
    queues: tuple[str, ...]
    sites: tuple[str, ...]
    tip: str


@dataclass
class Scope:
    """What the dashboard shows (design §2.7): the sites of the last run_start (every round-1 site when it names none,
    or with no run yet), their names and homes ((host, queue): the server each is counted on), the cards, and where
    this run of these sites began: start_row the last run_start's rowid, first_row (started_at) the first of the
    run_starts before it naming the same sites (a run that ended waiting and started again is one run of them). only:
    how many ids a run given --only listed."""

    start_row: int
    first_row: int
    started_at: float | None
    sites: tuple[str, ...]
    names: dict[str, str]
    cards: list[Card]
    card_of_queue: dict[str, str]
    card_of_site: dict[str, str]
    only: int | None
    homes: dict[str, tuple[str, str]]

    def card(self, key: str | None) -> Card | None:
        return next((c for c in self.cards if c.key == key), None)


def _message(text: str | None) -> dict:
    try:
        msg = json.loads(text or "{}")
    except ValueError:
        return {}
    return msg if isinstance(msg, dict) else {}


def _runs(conn: sqlite3.Connection) -> list[tuple[int, str, float | None, dict]]:
    """The run_start and run_end events, in order: (rowid, code, at, message)."""
    return [(r[0], r[1], _ts(r[2]), _message(r[3])) for r in conn.execute(
        "SELECT rowid, code, at, message FROM event WHERE code IN ('run_start', 'run_end') ORDER BY rowid")]


def _sites_of(msg: dict) -> tuple[str, ...] | None:
    sites = msg.get("sites")
    return tuple(sorted({str(s) for s in sites})) if isinstance(sites, list) else None


def _host_of(url: str | None) -> str:
    m = re.match(r"(?i)^[a-z][a-z0-9+.-]*://(?:[^@/?#]*@)?(\[[^\]]*\]|[^:/?#]*)", url or "")
    return m[1].lower() if m else ""


def _google_host(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in GOOGLE_DOMAINS)


def _school_host(host: str) -> bool:
    return host == outcome.SCHOOL_DOMAIN or host.endswith("." + outcome.SCHOOL_DOMAIN)


def _dns_queue(host: str, queue: str) -> str:
    """The queue a host counts on in the dashboard: its own, but a name that does not resolve on the dns-fail queue of
    its small card's kind (DNS_QUEUES)."""
    if queue != "dns-fail":
        return queue
    return "dns-fail:" + ("google" if _google_host(host) else "school" if _school_host(host) else "offsite")


def _hosts(conn: sqlite3.Connection) -> dict[str, dict]:
    """The host table by name, each host on the queue it counts on in the dashboard (_dns_queue)."""
    out = {}
    for r in conn.execute("SELECT host, queue, ip, crawl_delay, cooldown_until, not_before FROM host"):
        out[r["host"]] = {**dict(r), "queue": _dns_queue(r["host"], r["queue"])}
    return out


def _big_name(queue: str, home_hosts: list[str]) -> str:
    """A big card's name (design §1.1): Google 协作平台 for Google's queue, else the host most of its sites' homes are on
    without .edu.tw, and 主机 (c.nknu.edu.tw: c.nknu 主机; SSO's: SSO 主机)."""
    if queue == google_api.FORMS_QUEUE:
        return GSITES_NAME
    counts = Counter(h for h in home_hosts if h)
    host = min(counts.items(), key=lambda x: (-x[1], x[0]))[0] if counts else queue
    short = host.removesuffix(".edu.tw")
    return ("SSO" if short == "sso.nknu" else short) + " 主机"


def _hosts_tip(queues, queue_hosts: dict[str, list[str]], most: int = 8) -> str:
    parts = []
    for q in queues:
        hs = sorted(queue_hosts.get(q, ()))
        more = f" 等 {len(hs)} 个主机名" if len(hs) > most else ""
        parts.append(f"{q}：{'、'.join(hs[:most]) or '—'}{more}")
    return "；".join(parts)


def run_scope(conn: sqlite3.Connection, hosts: dict[str, dict], runs: list | None = None) -> Scope:
    """The sites of the run and their cards (design §2.1, §2.7): the last run_start's sites, whether that run is alive
    or not; each site's home server (the queue of its home's host; one whose host has no queue yet: the queue holding
    most of the site's rows); a server home to BIG_CARD sites or more is a card of its own, those home to one or two
    share 各单位自己的主机 (each row names its host), and a server home to none is a small card by its host names
    (Google, the school's, off campus). runs: _runs(conn), when the caller has it already."""
    runs = _runs(conn) if runs is None else runs
    starts = [x for x in runs if x[1] == "run_start"]
    plan = {r["site"]: r for r in conn.execute("SELECT site, name, round, home FROM site_plan")}
    sites, only, start_row, first_row, started_at = None, None, 0, 0, None
    if starts:
        start_row, _code, started_at, msg = starts[-1]
        sites, first_row = _sites_of(msg), start_row
        only = msg.get("only") if isinstance(msg.get("only"), int) else None
        for rowid, _code, at, msg in reversed(starts[:-1]):
            if _sites_of(msg) != sites:
                break
            first_row, started_at = rowid, at
    if sites is None:
        sites = tuple(sorted(s for s, p in plan.items() if p["round"] == "1"))
    names = {s: (plan[s]["name"] if s in plan else None) or s for s in sites}
    homes: dict[str, tuple[str, str]] = {}
    for s in sites:
        host = _host_of(plan[s]["home"]) if s in plan else ""
        h = hosts.get(host)
        homes[s] = (host, h["queue"] if h is not None else "")
    lost = [s for s in sites if homes[s][1] in ("?", "")]
    if lost:  # a home nobody looked up yet: the queue that holds most of the site's rows (url_owner)
        best: dict[str, tuple[str, int]] = {}
        for i in range(0, len(lost), 500):
            part = lost[i:i + 500]
            for r in conn.execute(f"""SELECT u.owner_site, h.queue, count(*) FROM url u JOIN host h ON h.host = u.host
                                      WHERE u.owner_site IN ({','.join('?' * len(part))}) AND h.queue NOT IN ('?', '')
                                      GROUP BY 1, 2""", part):
                if r[2] > best.get(r[0], ("", 0))[1]:
                    best[r[0]] = (r[1], r[2])
        for s in lost:
            homes[s] = (homes[s][0], best.get(s, ("?", 0))[0])
    queue_hosts: dict[str, list[str]] = defaultdict(list)
    for host, h in hosts.items():
        queue_hosts[h["queue"]].append(host)
    by_queue: dict[str, list[str]] = defaultdict(list)
    for s in sites:
        by_queue[homes[s][1]].append(s)

    def order(s: str) -> tuple[str, str]:
        return names[s], s

    cards: list[Card] = []
    card_of_queue: dict[str, str] = {}
    unit_queues: list[str] = []
    unit_sites: list[str] = []
    for q, ss in sorted(by_queue.items()):
        if len(ss) >= BIG_CARD and q not in NO_WORKER:
            key = q if q not in SMALL_NAME else f"{q}-sites"
            cards.append(Card(key, _big_name(q, [homes[s][0] for s in ss]), False, (q,),
                              tuple(sorted(ss, key=order)), _hosts_tip((q,), queue_hosts)))
            card_of_queue[q] = key
        else:
            unit_queues.append(q)
            unit_sites += ss
    if unit_sites:
        cards.append(Card(UNITS, UNITS_NAME, False, tuple(unit_queues), tuple(sorted(unit_sites, key=order)),
                          "很多台小服务器，每台只住一两个网站：点进去每一行写是哪台"))
        card_of_queue.update({q: UNITS for q in unit_queues})
    cards.sort(key=lambda c: (c.key == UNITS, c.name.casefold()))
    small: dict[str, list[str]] = defaultdict(list)
    for q in sorted(queue_hosts):
        if q in card_of_queue or q in ("?", ""):
            continue
        hs = queue_hosts[q]
        key = ("google" if q in GOOGLE_QUEUES or any(map(_google_host, hs)) else
               "school" if any(map(_school_host, hs)) else "offsite")
        small[key].append(q)
    for key, name in SMALL_NAME.items():
        if small[key]:
            cards.append(Card(key, name, True, tuple(small[key]), (), _hosts_tip(small[key], queue_hosts)))
            card_of_queue.update({q: key for q in small[key]})
    card_of_site = {s: c.key for c in cards if not c.small for s in c.sites}
    return Scope(start_row, first_row, started_at, tuple(sites), names, cards, card_of_queue, card_of_site, only, homes)


# ---------------------------------------------------------------- each site's and queue's numbers (§2.2, §2.5)

def _sql_in(values) -> str:
    return outcome._sql_list(values)


def _presses(conn: sqlite3.Connection, now: float) -> list[sqlite3.Row]:
    """The presses by the site they count for (their list page's, else action.site), queue, host and state, a press a
    restart replaced (superseded) aside: how many, how many are due now (their next_at passed), the soonest next_at,
    how many were planned in the last DISCOVER_S seconds, and since when the first of those due is (since: its retry
    came due or it was planned)."""
    return conn.execute("""SELECT coalesce(u.owner_site, a.site) AS site, a.queue, a.host, a.state, count(*) AS n,
                                  sum(a.next_at <= ?) AS due, min(a.next_at) AS first, sum(a.created > ?) AS new,
                                  min(CASE WHEN a.next_at <= ? THEN max(a.next_at, coalesce(a.created, 0)) END) AS since
                           FROM action a LEFT JOIN url u ON u.id = a.list_id
                           WHERE a.state <> 'superseded' GROUP BY 1, 2, 3, 4""",
                        (now, now - DISCOVER_S, now)).fetchall()


def site_numbers(conn: sqlite3.Connection, now: float, scope: Scope, presses: list | None = None) -> dict[str, dict]:
    """Each site's progress (design §2.2): known, the rows the crawl knows it must fetch (pending, active, done, failed,
    held) and its presses; finished, those with a result (done, failed, held; a press done, failed or mismatch); left,
    the rest (what the crawler still has to do: 0 is 100%); new, the rows and presses it gained in the last DISCOVER_S
    seconds (发现中). A site's rows on another server (a Drive file, an attachment on the school's home page, a short
    link) count for it as well. presses: _presses(), when the caller has it already."""
    out = {s: {"finished": 0, "known": 0, "left": 0, "new": 0} for s in scope.sites}
    # grouped in url_owner's order (owner_site, kind, state): one pass over the index, no sorting
    for site, _kind, state, n in conn.execute(f"SELECT owner_site, kind, state, count(*) FROM url "
                                              f"WHERE state IN {_sql_in(KNOWN)} GROUP BY 1, 2, 3"):
        s = out.get(site)
        if s is not None:
            s["known"] += n
            s["finished" if state in FINISHED else "left"] += n
    # the rows added lately, found by url_added (left to itself, SQLite walks url_owner for the grouping: every row)
    for site, n in conn.execute(f"""WITH recent AS MATERIALIZED (SELECT owner_site, state FROM url WHERE added_at > ?)
                                    SELECT owner_site, count(*) FROM recent WHERE state IN {_sql_in(KNOWN)}
                                    GROUP BY 1""", (now - DISCOVER_S,)):
        if site in out:
            out[site]["new"] += n
    for r in _presses(conn, now) if presses is None else presses:
        s = out.get(r["site"])
        if s is not None:
            s["known"] += r["n"]
            s["finished" if r["state"] in PRESS_FINISHED else "left"] += r["n"]
            s["new"] += r["new"] or 0
    return out


def _work() -> dict:
    return {"left": 0, "active": 0, "due": 0, "first_due": None, "due_since": None, "by_site": Counter(),
            "hosts": Counter(), "cooling": {}, "known": 0, "finished": 0, "sites_known": Counter(),
            "hosts_known": Counter()}


def queue_work(conn: sqlite3.Connection, now: float, scope: Scope, hosts: dict[str, dict],
               presses: list | None = None) -> dict[str, dict]:
    """Each queue's work for the sites of the run (design §2.5): the rows and presses left (pending, active) by site and
    host, those active (being fetched), those due now (next_at passed and their host not cooling down) and since when
    the first of them is (due_since: its retry came due, it was found, its host's cool-down or Crawl-delay ended), when
    the soonest is due (its retry, its host's cool-down or Crawl-delay) and the hosts cooling down; and, on a small
    card's queues, how many rows and presses of those sites it holds and how many have a result (its 完成/总数). hosts:
    _hosts' (a press's queue goes through _dns_queue the same way)."""
    want = set(scope.sites)
    out: dict[str, dict] = defaultdict(_work)

    def left(queue: str, site: str, host: str, state: str, n: int, due: int | None, first: float | None,
             since: float | None) -> None:
        h = hosts.get(host)
        w = out[queue]
        w["left"] += n
        w["by_site"][site] += n
        w["hosts"][host] += n
        if state == "active":
            w["active"] += n
            return
        cool = (h["cooldown_until"] or 0.0) if h is not None else 0.0
        gate = max(cool, (h["not_before"] or 0.0) if h is not None else 0.0)
        if cool > now:
            w["cooling"][host] = cool
        else:
            w["due"] += due or 0
            if due and since is not None:
                was = max(since, gate)
                w["due_since"] = was if w["due_since"] is None else min(w["due_since"], was)
        soon = max(first or 0.0, gate)
        w["first_due"] = soon if w["first_due"] is None else min(w["first_due"], soon)

    for site, host, state, n, due, first, since in conn.execute(
            "SELECT owner_site, host, state, count(*), sum(next_at <= ?), min(next_at), "
            "min(CASE WHEN next_at <= ? THEN max(next_at, coalesce(added_at, 0)) END) FROM url "
            "WHERE state IN ('pending', 'active') GROUP BY 1, 2, 3", (now, now)):
        if site in want:
            h = hosts.get(host)
            left(h["queue"] if h is not None else "?", site, host, state, n, due, first, since)
    presses = _presses(conn, now) if presses is None else presses
    for r in presses:
        if r["site"] in want and r["state"] in ("pending", "active"):
            left(_dns_queue(r["host"], r["queue"]), r["site"], r["host"], r["state"], r["n"], r["due"], r["first"],
                 r["since"])
    small = sorted(h for h, row in hosts.items() if scope.card_of_queue.get(row["queue"]) in SMALL_NAME)
    for i in range(0, len(small), 500):  # url_next2 (host, state, …)
        part = small[i:i + 500]
        for site, host, state, n in conn.execute(
                f"SELECT owner_site, host, state, count(*) FROM url WHERE host IN ({','.join('?' * len(part))}) "
                f"AND state IN {_sql_in(KNOWN)} GROUP BY 1, 2, 3", part):
            if site in want:
                w = out[hosts[host]["queue"]]
                w["known"] += n
                w["finished"] += n if state in FINISHED else 0
                w["sites_known"][site] += n  # a small card's page (the owner 11-04): whose rows each server holds
                w["hosts_known"][host] += n
    for r in presses:
        queue = _dns_queue(r["host"], r["queue"])
        if r["site"] in want and scope.card_of_queue.get(queue) in SMALL_NAME:
            w = out[queue]
            w["known"] += r["n"]
            w["finished"] += r["n"] if r["state"] in PRESS_FINISHED else 0
            w["sites_known"][r["site"]] += r["n"]
            w["hosts_known"][r["host"]] += r["n"]
    return dict(out)


# ---------------------------------------------------------------- the states (§2.5)

def _state(code: str, words: str, color: str, tip: str = "", at: float | None = None) -> dict:
    return {"code": code, "words": words, "color": color, "tip": tip, "at": at}


def queue_state(queue: str, w: dict | None, env: dict, now: float) -> dict | None:
    """The state of one queue that holds work of the run (w: queue_work's; None when it holds none, and for a host
    nobody looked up or whose name does not resolve: NO_WORKER), by design §2.5's rules 3–6 in order: 网络断了, 被 Google
    挡 (Google's download queue only; red when long, red 没有 Google 金钥 when it rests for the key), 连不上 (red at
    CONN_GIVE_UP in a row), 休息中 HH:MM (a rest, or every host with work cooling down); then 在抓 (rule 8: a row or
    press being fetched, or due work whose queue made a request within QUIET_S past its Crawl-delay), 没动静 (yellow)
    past that, 卡住 (red) past monitor.STALL_S; and 等重试 HH:MM when nothing is due before then. Quiet counts from the
    last request of the run, or the run's start, but never from before its work became due (w's due_since) or before
    its rest, or our network's, ended (review 11-04: a server found late, a retry just due or a rest just over showed
    卡住 until its first request). env: the snapshot's pauses, pause_ended, servers, block, last_started, run_started,
    running and delay."""
    from nknu.archive.crawler import CONN_GIVE_UP
    from nknu.archive.monitor import STALL_S

    if not w or not w["left"] or queue in NO_WORKER:
        return None
    pauses = env["pauses"]
    network = network_of(queue)
    net = max(pauses.get(k, (0.0, ""))[0] for k in (ALL_QUEUES, net_key(network)))
    if net > now:
        where = "学校的网络" if network == "school" else "校外的网络"
        return _state("offline", "网络断了", "yellow", f"本机或{where}像是断了：{fmt_clock(net, now)} 再试，恢复后自动接着抓")
    until, why = pauses.get(queue, (0.0, ""))
    if queue == google_api.QUEUE and until > now:
        if why.startswith(google_api.KEY_PAUSE_WHY):
            return _state("no_key", "没有 Google 金钥", "red",
                          "没有设 Google 金钥，或 Google 不接受：云端硬盘和日历这次先不读（不算失败），设好金钥再开跑")
        if error_code(None, why) == "google_block":
            streak, first = env["block"]
            if google_api.block_long(streak, first, now):
                return _state("google_block", "被 Google 挡", "red", google_api.block_words(streak, first, now))
            return _state("google_block", "被 Google 挡", "yellow",
                          f"{PLAIN['google_block']}；{fmt_clock(until, now)} 再试")
    conn_fail = env["servers"].get(queue, (0, 0))[0]
    if conn_fail >= 1 or (until > now and "no connection" in why.lower()):
        tip = f"连续 {conn_fail} 次连不上" if conn_fail else "连不上"
        if conn_fail >= CONN_GIVE_UP:
            return _state("conn", "连不上", "red", f"{tip}（约 2 小时）：要人看是不是永久下线")
        return _state("conn", "连不上", "yellow", f"{tip}：整台休息 15 分钟再试，不扣重试次数")
    if until > now:
        return _state("rest", f"休息中 {_hm(until, now)}", "yellow",
                      f"{fmt_clock(until, now)} 继续：{plain_error(None, why)}")
    if w["cooling"] and not w["due"] and not w["active"] and set(w["cooling"]) >= set(w["hosts"]):
        resume = min(w["cooling"].values())
        return _state("rest", f"休息中 {_hm(resume, now)}", "yellow",
                      f"{len(w['cooling'])} 个主机名连续出错，冷却到 {fmt_clock(resume, now)}")
    if w["active"]:
        return _state("working", "在抓", "green", "")
    if w["due"]:
        last = env["last_started"].get(queue)
        run_started = env.get("run_started")
        asked = last is not None and (run_started is None or last >= run_started)
        since = last if asked else run_started
        if since is not None:
            ended = env.get("pause_ended", {})
            since = max(since, w.get("due_since") or 0.0, *(ended.get(k, 0.0) for k in (queue, ALL_QUEUES,
                                                                                         net_key(network))))
        quiet = now - since - env["delay"].get(queue, 0.0) if env.get("running") and since is not None else 0.0
        said = f"最后一个请求在 {fmt_clock(last, now)}" if asked else "这次还没向它发过请求"
        if quiet > STALL_S:
            return _state("stuck", "卡住", "red", f"有该抓的网址，{said}：爬虫可能卡住了")
        if quiet > QUIET_S:  # the design names no state between its 2 minutes and 卡住's 15 【Claude 的执行办法】
            ago = f"（{fmt_hms(now - last)} 前）" if asked else ""
            return _state("quiet", "没动静", "yellow",
                          f"有该抓的网址，{said}{ago}：超过 {STALL_S / 60:.0f} 分钟没动静算卡住")
        return _state("working", "在抓", "green", "")
    return _state("retry_wait", f"等重试 {_hm(w['first_due'], now)}", "none",
                  f"剩下的 {w['left']:,} 个都在等重试或冷却，最早 {fmt_clock(w['first_due'], now)}", w["first_due"])


def _worst(states) -> dict | None:
    """The worst of some queues' states (design §2.1: a card takes its worst server's): red before yellow before the
    rest, then STATE_ORDER (review 11-04: a red server is never hidden behind a yellow one); of several 等重试, the
    soonest."""
    states = [s for s in states if s is not None]
    if not states:
        return None
    worst = min(states, key=lambda s: (_COLOR_RANK.get(s["color"], 2), STATE_ORDER.index(s["code"])))
    if worst["code"] == "retry_wait":  # the soonest of them
        return min((s for s in states if s["code"] == "retry_wait"), key=lambda s: s["at"] or 0.0)
    return worst


def _dns_state() -> dict:
    """查不到地址: the rows of hosts whose names do not resolve (DNS_QUEUES) wait for the crawler to look again."""
    from nknu.archive.crawler import DNS_GIVE_UP

    return _state("dns", "查不到地址", "none",
                  f"主机名查不到地址：爬虫隔一阵再查，查 {DNS_GIVE_UP} 次（约一小时）还不行，这些网址就算失败")


def card_state(card: Card, nums: dict[str, dict], work: dict[str, dict], env: dict, now: float) -> dict:
    """A card's state (design §2.5): 完成 when its sites have nothing left (a small card: its queues hold none of the
    run's work); 没在跑 or 爬虫没反应 (the run's, on every card); else the worst state of its queues that hold its
    work (queue_state); 查不到地址 when what is left on its own queues is on names that do not resolve; a big card
    whose own queues hold none waits on where the rest is (等 Google, 等学校首页, 等校外网站; review 11-04: rows on a
    name that does not resolve too, 查不到地址 in the hover text, never 等查地址), never 完成; 等查地址 only for names
    nobody looked up yet."""
    if card.small:
        mine = {q: work.get(q) for q in card.queues if (work.get(q) or {}).get("left")}
    else:
        mine = {q: w for q, w in work.items() if any(w["by_site"].get(s) for s in card.sites)}
    if not mine:
        return _state("done", "完成", "green")
    run = env["run"]
    if not run["running"]:
        return _state("idle", "没在跑", "none", run["words"])
    if run["hung"]:
        return _state("hung", "爬虫没反应", "red", run["tip"])
    own = [queue_state(q, w, env, now) for q, w in mine.items() if q in card.queues]
    got = _worst(own)
    if got is not None:
        return got
    if any(q in DNS_QUEUES for q in mine if q in card.queues):  # what is left on its own: names that do not resolve
        return _dns_state()
    elsewhere = Counter()
    for q, w in mine.items():
        if q not in card.queues:
            elsewhere[q] += sum(w["by_site"].get(s, 0) for s in card.sites)
    if not elsewhere:
        return _state("lookup", "等查地址", "none", "剩下的网址主机名还没查到地址")
    q = min(elsewhere.items(), key=lambda x: (-x[1], x[0]))[0]
    if q in ("?", ""):
        return _state("lookup", "等查地址", "none", f"还有 {elsewhere[q]:,} 个网址的主机名还没查到地址")
    key = env["card_of_queue"].get(q)
    there = _dns_state() if q in DNS_QUEUES else queue_state(q, work.get(q), env, now)
    words = WAIT_ZH.get(key) or f"等 {env['card_names'].get(key, '别台服务器')}"
    tip = f"自己这台没活了，还有 {elsewhere[q]:,} 个在那边" + (f"（那边：{there['words']}）" if there else "")
    return _state("waiting", words, "none", tip)


# ---------------------------------------------------------------- periodic updates (the top line's last mark)

# the nightly script (research/crawler_lab_2026-11-04/nightly_update.sh) and update.py's keys and status file: pinned
# equal by tests/archive/test_update_nightfix_1116.py (progress imports nothing of update.py, which pulls the export in)
UPDATE_SITES_KEY = "update:sites"
UPDATE_NIGHT_KEY = "update:night_started"
UPDATE_REVIEWED_KEY = "update:night_reviewed"
UPDATE_STATUS = "update-status.json"
UPDATE_LATE_S = 26 * 3600  # one night a day: past this, one was missed 【Claude】
UPDATE_SAY = {"new": "新的", "changed": "改了", "same": "没变", "no_answer": "这次没回话", "not_seen": "这次没看到",
              "not_got": "没拿到", "deleted": "学校删了", "still_gone": "还是删了", "back": "回来了"}


def update_state(conn: sqlite3.Connection, root: Path, now: float) -> dict | None:
    """The nightly update as one mark of the top line (11-16, after the first real night: 「漏跑的晚上也没有任何提醒」,
    the owner: 「先修这三件」): None before go-live (no meta update:sites); red when the last night stopped at a step
    (status file failed_step) or held a site by an alarm; yellow when no night started for more than UPDATE_LATE_S
    (the computer off, Docker not started, the schedule stopped: nothing else would say it); 在跑 while tonight's
    review is not written; else green with the last night's time, its counts in the hover text. words, color, tip."""
    if conn.execute("SELECT 1 FROM meta WHERE key = ?", (UPDATE_SITES_KEY,)).fetchone() is None:
        return None
    meta = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM meta WHERE key IN (?, ?)",
                                              (UPDATE_NIGHT_KEY, UPDATE_REVIEWED_KEY))}
    try:
        night = float(meta[UPDATE_NIGHT_KEY]) if meta.get(UPDATE_NIGHT_KEY) else None
    except ValueError:
        night = None
    try:
        status = json.loads((root / "reports" / UPDATE_STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = {}
    status = status if isinstance(status, dict) else {}
    when = fmt_clock(night, now) if night is not None else None
    if status.get("failed_step"):
        at = status.get("failed_at") or status.get("started") or when or "?"
        return {"color": "red", "words": "定期更新没跑完",
                "tip": f"上一晚（{at}）停在「{status['failed_step']}」" + (f"：{status['note']}" if status.get("note")
                                                                       else "") + "；细节在存档的 update.log"}
    alarms = status.get("alarms") or {}
    if alarms:
        return {"color": "red", "words": f"定期更新警报 {len(alarms)} 站",
                "tip": "这几站这一晚不导出，学校恢复以前每晚再看：" + "；".join(f"{k}（{v}）" for k, v in alarms.items())}
    if night is None:
        return {"color": "none", "words": "定期更新还没跑过", "tip": "已经 go-live，还没跑过一晚"}
    late = now - night
    if late > UPDATE_LATE_S:
        gap = f"{int(late // 3600)} 小时" if late < 2 * 86400 else f"{int(late // 86400)} 天"
        return {"color": "yellow", "words": f"定期更新 {gap}没跑",
                "tip": f"上一晚 {when} 开始；电脑关着、Docker 没开、排程停了，都会这样"}
    if meta.get(UPDATE_REVIEWED_KEY) != meta.get(UPDATE_NIGHT_KEY):
        return {"color": "none", "words": "定期更新在跑", "tip": f"{when} 开始，变动清单还没算"}
    counts = status.get("counts") or {}
    said = "、".join(f"{UPDATE_SAY.get(k, k)} {n:,}" for k, n in counts.items() if n)
    changed = status.get("sites_changed") or []
    return {"color": "green", "words": f"定期更新 {when}",
            "tip": f"上一晚 {when} 开始：{said or '什么都没看'}；有变动的网站 {len(changed)} 个（还没导出）"}


# ---------------------------------------------------------------- the run (§1.1 top line, §2.5 rule 2)

def _run_state(conn: sqlite3.Connection, now: float, running: str | None, stop_file: bool,
               runs: list | None = None) -> dict:
    """Whether a run is going and how the last one ended (design §1.1, §2.5 rule 2). A run goes while the process that
    wrote the last run_start holds run.lock (running: the pid in it, crawler.lock_holder; the kernel lets go even after
    kill -9) and no run_end followed it. jscheck, reconcile, seed and retry take run.lock too, and a run takes it a
    while before it writes its run_start: a lock held by another pid is never 在跑 (review 11-04: 「停了还写在抓」),
    it is `other`, said in the hover text. The last run_start and run_end say how the last run ended; meta run:alive_at
    when the running one's main loop last went round (爬虫没反应 after ALIVE_LATE_S; no beat yet, no such check).
    words, color, tip: the top line's 在跑 6:12:05, 你停的, 意外停了, 出错停了, 等重试 …, 没重开"""
    runs = _runs(conn) if runs is None else runs
    start = next((x for x in reversed(runs) if x[1] == "run_start"), None)
    end = next((x for x in reversed(runs) if x[1] == "run_end"), None)
    start_row, started_at, smsg = (start[0], start[2], start[3]) if start else (0, None, {})
    end_row, ended_at, emsg = (end[0], end[2], end[3]) if end else (0, None, {})
    got = conn.execute("SELECT value FROM meta WHERE key = 'run:alive_at'").fetchone()
    try:
        alive_at = float(got[0]) if got is not None and got[0] is not None else None
    except ValueError:
        alive_at = None
    # the last run's own process: running until its run_end, then letting go of the lock as it ends
    mine = running is not None and start is not None and str(smsg.get("pid")) == str(running)
    own = mine and end_row < start_row
    out: dict[str, Any] = {"running": running if own else None, "other": None if mine else running,
                           "stop_file": stop_file, "own": own, "started_at": started_at,
                           "ended_at": ended_at, "end_reason": emsg.get("reason"), "pending": emsg.get("pending"),
                           "next_due": emsg.get("next_due"), "gap": smsg.get("gap"), "sites": smsg.get("sites"),
                           "pid": smsg.get("pid"), "only": smsg.get("only"), "alive_at": alive_at, "hung": False}
    if own:
        out["state"] = "running"
        since = max(alive_at or 0.0, started_at or 0.0)
        out["hung"] = alive_at is not None and now - since > ALIVE_LATE_S
    elif start is None:
        out["state"] = "never"
    elif end_row < start_row:
        out["state"] = "crashed"  # a run_start with no run_end after it, and no run holds the lock: killed or crashed
    elif emsg.get("reason") in RUN_END_BAD:
        out["state"] = "bad_end"
    else:
        out["state"] = "ended"
    out.update(_run_words(out, now))
    return out


def _run_words(run: dict, now: float) -> dict:
    """The top line's state in words (design §1.1): 在跑 and how long (wall time), else why the run is not going; with
    run.lock held by another program (other), who holds it first in the hover text."""
    got = _run_said(run, now)
    if run.get("other") is not None:
        last = "上次 run：" if run["state"] != "never" else ""
        got["tip"] = (f"run.lock 被 pid {run['other']} 拿着，不是爬虫的 run：浏览器检查、对账、seed、retry 也拿这把锁，"
                      f"也可能是爬虫正要开跑、开跑纪录还没写；它放开以前爬虫开不了。{last}{got['tip']}")
    return got


def _run_said(run: dict, now: float) -> dict:
    """The words of the run's own state (_run_words adds who else holds run.lock)."""
    state, reason = run["state"], run["end_reason"] or "stop"
    if state == "running":
        if run["hung"]:
            late = now - max(run["alive_at"] or 0.0, run["started_at"] or 0.0)
            return {"words": "爬虫没反应", "color": "red",
                    "tip": f"run.lock 还在，爬虫却 {fmt_hms(late)} 没写「还活着」的记号：可能卡死了"}
        if run["stop_file"]:
            return {"words": "正在停", "color": "yellow", "tip": "STOP 档在：手上的请求做完就停"}
        if run["started_at"] is not None:
            return {"words": f"在跑 {fmt_hms(now - run['started_at'])}", "color": "green",
                    "tip": f"{fmt_clock(run['started_at'], now)} 开跑（pid {run['running']}）"}
        return {"words": "在跑", "color": "green", "tip": f"pid {run['running']}"}
    if state == "never":
        return {"words": "还没开始", "color": "none", "tip": "这个存档还没开跑过"}
    if state == "crashed":
        return {"words": "意外停了", "color": "red",
                "tip": f"{fmt_clock(run['started_at'], now)} 开跑，没写结束就停了（被关掉或当掉）：再开跑会接着抓"}
    when = fmt_clock(run["ended_at"], now)
    if state == "bad_end":
        return {"words": "出错停了", "color": "red", "tip": f"{when} {RUN_END_ZH.get(reason, reason)}"}
    if reason == "waiting":
        due = run["next_due"]
        left = f"还有 {run['pending'] or 0:,} 个网址在等"
        again = max((t for t in (due, run["ended_at"]) if t is not None), default=None)
        if again is not None and now > again + RESTART_LATE_S:  # review 11-04: never a planned pause for ever
            held = run.get("other") is not None
            why = ("run.lock 被别的程序占着，开不了" if held else
                   "接着跑的小脚本停了（爬虫容器被停、电脑或 Docker 重开过），要接着抓就再开一次爬虫")
            return {"words": "没重开", "color": "yellow" if held else "red",
                    "tip": f"{when} {RUN_END_ZH['waiting']}，{fmt_clock(again, now)} 该接着跑，到现在还没开跑：{why}；{left}"}
        words = f"等重试 {_hm(due, now)} 再开" if due else "等重试"
        return {"words": words, "color": "none", "tip": f"{when} {RUN_END_ZH['waiting']}：{left}"}
    if reason == "finished":  # review 10-26: nothing left to fetch is no 爬完 (§10.5): neutral, never green
        return {"words": "抓完了", "color": "none", "tip": f"{when} 这次 run 没有要抓的了（不等于每个网站都爬完）"}
    if reason in ("stop_file", "sigterm"):
        return {"words": "你停的", "color": "none", "tip": f"{when} {RUN_END_ZH[reason]}"}
    return {"words": "停了", "color": "none", "tip": f"{when} {RUN_END_ZH.get(reason, reason)}"}


# ---------------------------------------------------------------- the 10-second numbers (§1.1, §2.2–§2.6)

def _site_errors(slow: dict | None, scope: Scope) -> tuple[dict | None, str]:
    """Each site's 错 from the background snapshot, or None and why there is none yet (design §2.4: until the first
    one is worked out, or when it failed, the badge is a dim 「错 …」 that says why)."""
    if slow is None:
        return None, "错的条数还在算（第一次要几十秒）"
    if slow.get("errors_error"):
        return None, f"错的条数算不出来：{slow['errors_error']}"
    if tuple(slow.get("scope_sites") or ()) != scope.sites:
        return None, "这次 run 的网站刚换，错的条数 2 分钟内更新"
    return slow.get("errors_by_site") or {}, ""


def _errors_of(counts: list[dict | None]) -> dict | None:
    """错 N of a card or of the whole (the sum of its sites'), with its colour (design §2.4): red with any 失败, else
    yellow (待判定 counts as 要人看 for the colour); none at 0."""
    if any(c is None for c in counts):
        return None
    out = {k: sum(c.get(k, 0) for c in counts) for k in ("n", "failed", "review", "pending")}
    out["color"] = "red" if out["failed"] else "yellow" if out["n"] else "none"
    return out


def snapshot(conn: sqlite3.Connection, now: float, *, tracker: Tracker | None = None, slow: dict | None = None,
             running: str | None = None, stop_file: bool = False) -> dict:
    """The dashboard's numbers at `now`, read in one short transaction (design §2.8): the run (_run_state), the sites of
    the run and their cards (run_scope), each site's and card's progress (site_numbers, the cards' the sums of their
    sites'), busy time (Tracker.busy: a segment on a small card's queue goes to that server's row of the card's page,
    the card's the sum of its rows' (review 11-04: a server holding no row of the run counts on neither); any other to
    the site in its scope), state (card_state), the small cards (完成/总数, busy time, state, a row a server that holds
    rows of the run: _server_row), politeness, and 错 N from the
    background snapshot (slow_snapshot; dim until there is one). tracker: the request log read so far (updated here);
    running: the pid in run.lock (crawler.lock_holder; a run's only when it wrote the last run_start: _run_state);
    stop_file: whether STOP is there."""
    tracker = tracker or Tracker()
    conn.execute("BEGIN")
    try:
        tracker.update(conn)
        runs = _runs(conn)
        run = _run_state(conn, now, running, stop_file, runs)
        hosts = _hosts(conn)
        scope = run_scope(conn, hosts, runs)
        presses = _presses(conn, now)
        nums = site_numbers(conn, now, scope, presses)
        work = queue_work(conn, now, scope, hosts, presses)
        pauses: dict[str, tuple[float, str]] = {}
        pause_ended: dict[str, float] = {}  # a rest that is over stays in the table until the queue's next request
        for r in conn.execute("SELECT queue, until, why FROM pause"):
            if r["until"] > now:
                pauses[r["queue"]] = (r["until"], r["why"] or "")
            else:
                pause_ended[r["queue"]] = r["until"]
        servers = {r["queue"]: (r["conn_fail"] or 0, r["err_fail"] or 0) for r in conn.execute(
            "SELECT queue, conn_fail, err_fail FROM server")}
        block = google_api.block_state(conn)
    finally:
        conn.execute("COMMIT")
    delay: dict[str, float] = defaultdict(float)
    for h in hosts.values():
        delay[h["queue"]] = max(delay[h["queue"]], min(float(h["crawl_delay"] or 0.0), 300.0))
    ends = sorted(at for _row, code, at, _msg in runs if code == "run_end" and at is not None)
    busy = tracker.busy.seconds(now if run["running"] else None, ends)
    small_queues = {q for c in scope.cards if c.small for q in c.queues}
    site_busy: Counter = Counter()
    queue_busy: Counter = Counter()  # a small card's servers, the rows of its page
    for (queue, site), seconds in busy.items():
        if site not in nums:
            continue  # a campus check, a browser check, a site outside the run: no card shows it
        if queue in small_queues:
            queue_busy[queue] += seconds
        else:
            site_busy[site] += seconds
    if run["own"] and scope.started_at is not None and scope.started_at < (run["started_at"] or 0.0):
        run["tip"] += f"；这批网站 {fmt_clock(scope.started_at, now)} 第一次开跑"
    errs, errs_why = _site_errors(slow, scope)
    env = {"run": run, "running": run["running"] is not None, "run_started": run["started_at"] if run["own"] else None,
           "pauses": pauses, "pause_ended": pause_ended, "servers": servers, "block": block,
           "last_started": dict(tracker.last_started),
           "delay": delay, "card_of_queue": scope.card_of_queue, "card_names": {c.key: c.name for c in scope.cards}}
    cards, small = [], []
    for c in scope.cards:
        if c.small:
            numbers = [work[q] for q in c.queues if q in work]
            known = sum(w["known"] for w in numbers)
            if not known and not any(w["left"] for w in numbers):
                continue  # design §1.1: a small card shows only when it holds rows of the run's sites
            dns = sum(work[q]["left"] for q in c.queues if q in work and q in DNS_QUEUES)
            finished = sum(w["finished"] for w in numbers)
            server_rows = [_server_row(q, work[q], env, now, queue_busy[q], scope)
                           for q in c.queues if (work.get(q) or {}).get("known")]
            server_rows.sort(key=lambda r: (r["order"], -r["left"], r["hosts"][0] if r["hosts"] else r["queue"]))
            small.append({"key": c.key, "name": c.name, "small": True, "queues": c.queues,
                          "tip": c.tip + (f"；另有 {dns:,} 个网址的主机名查不到地址" if dns else ""),
                          "finished": finished, "known": known, "pct": fmt_pct(finished, known),
                          "share": finished / known if known else 1.0,
                          "left": sum(w["left"] for w in numbers), "busy": sum(r["busy"] for r in server_rows),
                          "state": card_state(c, nums, work, env, now), "rows": server_rows})
            continue
        rows = [_site_row(s, nums[s], site_busy[s], errs, scope) for s in c.sites]
        rows.sort(key=lambda r: (r["order"], r["name"], r["site"]))
        finished, known = sum(r["finished"] for r in rows), sum(r["known"] for r in rows)
        cards.append({"key": c.key, "name": c.name, "small": False, "queues": c.queues, "tip": c.tip,
                      "sites_total": len(rows), "sites_done": sum(r["done"] for r in rows), "finished": finished,
                      "known": known, "left": sum(r["left"] for r in rows), "pct": fmt_pct(finished, known),
                      "share": finished / known if known else 1.0, "busy": sum(r["busy"] for r in rows),
                      "errors": _errors_of([r["errors"] for r in rows]), "new": sum(r["new"] for r in rows),
                      "discovering": any(r["discovering"] for r in rows), "state": card_state(c, nums, work, env, now),
                      "rows": rows})
    finished, known = sum(c["finished"] for c in cards), sum(c["known"] for c in cards)
    total = {"finished": finished, "known": known, "pct": fmt_pct(finished, known), "left": sum(c["left"] for c in cards),
             "share": finished / known if known else 1.0, "sites_total": sum(c["sites_total"] for c in cards),
             "sites_done": sum(c["sites_done"] for c in cards)}
    errors = _errors_of([c["errors"] for c in cards]) if errs is not None else None
    return {"at": now, "run": run, "scope": scope, "only": scope.only, "first_start": scope.started_at,
            "total": total, "errors": errors if errors is not None else {"n": None, "why": errs_why},
            "polite": tracker.polite.result(), "cards": cards, "small": small, "slow_at": (slow or {}).get("at"),
            "campus": {"last": tracker.campus_last, "on": tracker.campus_on}}


def _server_row(queue: str, w: dict, env: dict, now: float, busy: float, scope: Scope) -> dict:
    """One server of a small card, a row of its page (the owner 11-04: 「为什么这三个卡片我点不进去详细看」): its host
    names, the most rows first; its state, 完成 once it holds none of the run's work, else the run's own 没在跑 or 爬虫
    没反应, else queue_state (连不上, 被 Google 挡, 休息中 …); 完成/总数 and the bar; 用时; and the sites whose rows it
    holds. Red first, then yellow, the rest, those done last."""
    run = env["run"]
    if not w["left"]:
        state = _state("done", "完成", "green")
    elif not run["running"]:
        state = _state("idle", "没在跑", "none", run["words"])
    elif run["hung"]:
        state = _state("hung", "爬虫没反应", "red", run["tip"])
    elif queue in DNS_QUEUES:
        state = _dns_state()
    else:
        state = queue_state(queue, w, env, now) or _state("lookup", "等查地址", "none", "这台的主机名还没查到地址")
    hosts = [h for h, _n in sorted((w["hosts_known"] or w["hosts"]).items(), key=lambda x: (-x[1], x[0]))]
    sites = [(scope.names.get(s, s), n) for s, n in sorted(w["sites_known"].items(), key=lambda x: (-x[1], x[0]))]
    order = 3 if state["code"] == "done" else _COLOR_RANK.get(state["color"], 2)
    return {"queue": queue, "hosts": hosts, "known": w["known"], "finished": w["finished"], "left": w["left"],
            "pct": fmt_pct(w["finished"], w["known"]), "share": w["finished"] / w["known"] if w["known"] else 1.0,
            "busy": busy, "state": state, "sites": sites, "order": order}


def _site_row(site: str, n: dict, busy: float, errs: dict | None, scope: Scope) -> dict:
    """One site's line on its card's page (design §1.2): ✓ when nothing is left (nothing known too), 排队 while it has
    neither a result nor busy time (the sites of one server are fetched interleaved: a department's waits for the
    school-wide ones), else its bar; the ones being fetched first, then those queued, then those done."""
    done = n["left"] == 0
    queued = not done and n["finished"] == 0 and busy <= 0
    host, queue = scope.homes.get(site, ("", ""))
    err = (errs.get(site) or {"n": 0, "failed": 0, "review": 0, "pending": 0}) if errs is not None else None
    return {"site": site, "name": scope.names.get(site, site), "host": host, "queue": queue, **n,
            "pct": fmt_pct(n["finished"], n["known"]), "share": n["finished"] / n["known"] if n["known"] else 1.0,
            "busy": busy, "errors": None if err is None else {**err, "color": _errors_of([err])["color"]},
            "done": done, "queued": queued, "discovering": bool(n["new"]) and not done,
            "order": 2 if done else 1 if queued else 0}


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


def _judged_page(kind: str | None, tier: int | None, result: str) -> bool:
    """A round-1 page judged by its completeness (page_completeness) or folded into where it lands (a redirect): never
    counted, nor listed, by its own url_result (§14.2: an incomplete page is never 抓到了 nor 规则不抓)."""
    return kind == "page" and tier in ROUND1_TIERS and result not in outcome.NOT_JUDGED


# ---------------------------------------------------------------- the problems (§1.3, §2.4)

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


@dataclass
class Problem:
    """One line of the problem log (design §1.3, §2.4): what the crawler stopped at and will not take up again by
    itself, counted once. kind: failed (失败), review (要人看) or pending_check (待判定); code: the result (outcome's
    codes), or a page's list, frame_missing or unexplained; ref: u<url id>, p<page id>, a<press id>, k<res_key> (a
    res_key may hold an address: masked, mask_text);
    n: how many rows the line stands for (one cap, one parked directory). Only codes, statuses and reasons, never a
    page's title (it may hold a student's name)."""

    at: float | None
    card: str
    site: str
    ref: str
    kind: str
    code: str
    label: str
    detail: str
    url: str
    n: int = 1


@dataclass
class ServerEvent:
    """A warning or an error of the crawler since this run of these sites began (event, warn and error, its notes
    aside): in the log's own section, never counted in 错 (design §1.3). card: the card of its host's queue; None for
    one that names no host (shown with every card)."""

    at: float | None
    card: str | None
    ref: str
    code: str
    label: str
    detail: str


_KEY_SHAPE = re.compile(r"AIza[0-9A-Za-z_-]{35}")
_SECRET_NAME = re.compile(r"(?i)key|token|sig|secret|password|auth")
_SECRET_PAIR = re.compile(r"(?i)([\w.%-]*(?:key|token|sig|secret|password|auth)[\w.%-]*=)[^&#\s'\"<>]*")


def mask_text(text: str | None) -> str:
    """Free text for the log (a reason, an event's message): a key-shaped string (AIza…) becomes [redacted], the value
    of a key, token, sig, secret, password or auth parameter … (design §1.3: the ledger holds none of ours; some of the
    school's links carry other people's)."""
    return _SECRET_PAIR.sub(r"\1…", _KEY_SHAPE.sub("[redacted]", text or ""))


def mask_url(url: str | None) -> str:
    """An address for the log: mask_text's rules, the rest of it as it is (its other parameters, their order and their
    encoding: the address stays one to look up)."""
    u = _KEY_SHAPE.sub("[redacted]", url or "")
    head, sep, rest = u.partition("?")
    if not sep:
        return u
    query, hash_, fragment = rest.partition("#")
    pairs = []
    for pair in query.split("&"):
        name, eq, _value = pair.partition("=")
        pairs.append(f"{name}=…" if eq and _SECRET_NAME.search(unquote(name)) else pair)
    return f"{head}?{'&'.join(pairs)}{hash_}{fragment}"


def _clean(text: str) -> str:
    """A field of the tab-separated log: no tab, no line break."""
    return re.sub(r"[\t\r\n]+", " ", text).strip()


def _by_id(conn: sqlite3.Connection, sql: str, column: str, ids) -> dict[int, sqlite3.Row]:
    """Rows looked up by id, 500 to a statement (sql: the SELECT without its WHERE; column: the id it is looked up
    by, which comes back as id)."""
    ids, out = sorted(set(ids)), {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute(f"{sql} WHERE {column} IN ({','.join('?' * len(part))})", part):
            out[r["id"]] = r
    return out


def _field(status: Any, key: str) -> Any:
    """A field of one of monitor.site_status's answers (a SiteStatus, or a mapping a stand-in gives)."""
    if hasattr(status, key):
        return getattr(status, key)
    return status.get(key) if isinstance(status, dict) else None


def _ref_id(ref: str, *prefixes: str) -> int | None:
    return int(ref[1:]) if ref[:1] in prefixes and ref[1:].isdigit() else None


def problems(conn: sqlite3.Connection, statuses: dict, pages: list[dict], *, scope: Scope, on_lab: bool,
             fx: Any = None) -> list[Problem]:
    """The problems of the run's sites (design §2.4): each site's problem list as monitor.site_status gives it
    (issues: its pages by their completeness, the resources they need and the rows no page uses, each once), with
    the four rules the monitor lacks: ① lab (only the school's network reaches it) is 失败 only on campus (on_lab) and
    once the row failed, else it waits; ② 待判定 counts only once the site's browser check ended (jscheck_done);
    ③ the rows one cap held are one line per site and cap (cap:variant, a cap of one path, per path); ④ the rows of
    one parked directory (crawler.deny_dir) are one line. A row listed as a page (p<id>) and as a resource (u<id>)
    is one problem; one that pages of several sites list is one problem too, of the site it belongs to (the row's
    owner_site, a press's list page's: whose progress counts it, §2.2), else of the first of them, so that the sites'
    错 add up to the whole's (§2.1, §2.4 「同一个附件被很多页用到，也只算 1 条」; review 11-04) 【Claude 的执行办法】. The
    time of a line: the row's fetch, else when it was added (held and parked rows, which no event names); a list's
    walk; a press's; an entry with no row its page's. A line's ref is masked as its address is (mask_text: an entry's
    holds its address). ValueError when a status has no problem list."""
    from nknu.archive import monitor
    from nknu.archive.crawler import MAX_ATTEMPTS, deny_dir

    def thing(ref: str) -> str:  # a row's page and resource refs, one key
        return f"u{ref[1:]}" if ref[:1] == "p" else ref

    page_of = {p["id"]: p for p in pages}
    picked: list[tuple[str, Any]] = []
    for site in scope.sites:
        st = statuses.get(site)
        if st is None:
            continue  # no round-1 judgement of it (a round-two site a run named): nothing found
        issues = _field(st, "issues")
        if issues is None or any(not hasattr(i, "ref") for i in issues):
            raise ValueError(f"网站状态没有问题清单（{site}）")
        js_done = bool(_field(st, "jscheck_done"))
        seen: set[str] = set()
        for i in issues:
            key = thing(i.ref)
            if key in seen:
                continue
            seen.add(key)
            if i.bucket == "pending_check" and not js_done:  # ②
                continue
            picked.append((site, i))
    url_ids = {x for _s, i in picked if (x := _ref_id(i.ref, "p", "u")) is not None}
    act_ids = {x for _s, i in picked if (x := _ref_id(i.ref, "a")) is not None}
    holder = {}  # an entry with no row: the page it was found on (for its time)
    if fx is not None:
        for h in getattr(fx, "holders", {}).values():
            for res in h.issues:
                holder.setdefault(res.res, res.holder)
    entry_pages = {holder[i.ref] for _s, i in picked if i.ref[:1] == "k" and i.ref in holder}
    urls = _by_id(conn, "SELECT id, url, host, path, state, reason, status, attempts, fetched_at, added_at, "
                        "size_declared, owner_site FROM url", "id", url_ids | entry_pages)
    acts = _by_id(conn, "SELECT a.id AS id, a.list_id, a.kind, a.queue, a.host, a.state, a.reason, a.status, a.done, "
                        "a.created, coalesce(l.owner_site, a.site) AS owner_site FROM action a "
                        "LEFT JOIN url l ON l.id = a.list_id", "a.id", act_ids)
    listed: dict[str, list[int]] = defaultdict(list)  # one thing → where it is in picked, a site each
    for n, (_site, i) in enumerate(picked):
        listed[thing(i.ref)].append(n)
    drop: set[int] = set()
    for key, ns in listed.items():
        if len(ns) > 1:
            uid = _ref_id(key, "u")
            row = urls.get(uid) if uid is not None else acts.get(_ref_id(key, "a"))
            owner = row["owner_site"] if row is not None else None
            keep = next((n for n in ns if picked[n][0] == owner), ns[0])
            drop.update(n for n in ns if n != keep)
    picked = [x for n, x in enumerate(picked) if n not in drop]
    walks = _by_id(conn, "SELECT list_id AS id, ended_by, pages, rows, at FROM listwalk", "list_id",
                   {x for _s, i in picked if (x := _ref_id(i.ref, "p")) is not None})
    queue_of = dict(conn.execute("SELECT host, queue FROM host").fetchall())

    def where(queue: str | None, host: str | None, site: str) -> str | None:
        if queue is None or queue == scope.homes.get(site, ("", ""))[1]:
            return None
        return f"在 {queue if queue in GOOGLE_QUEUES else host or queue} 抓"

    out: list[Problem] = []
    groups: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for site, i in picked:
        uid, aid = _ref_id(i.ref, "p", "u"), _ref_id(i.ref, "a")
        row = urls.get(uid) if uid is not None else None
        kind = i.bucket if i.bucket in KIND_ZH else "review"
        code = i.result
        if code == "lab":  # ①
            if not (on_lab and row is not None and row["state"] == "failed"):
                continue
            kind = "failed"
        page = page_of.get(uid) if i.ref[:1] == "p" else None
        if page is not None and not (monitor.tone(code, page=True) in (monitor.RED, monitor.PENDING) or code == "lab"):
            code = "list" if page.get("list_bad") else "frame_missing" if page.get("frame_missing") else "unexplained"
        if code == "held" and row is not None:  # ③
            reason = row["reason"] or "cap"
            groups[(site, "held", reason, row["path"] if reason == "cap:variant" else "")].append(row)
            continue
        if code == "parked" and row is not None:  # ④
            groups[(site, "parked", deny_dir(row["url"]))].append(row)
            continue
        bits, at = [], None
        if row is not None:
            at = _ts(row["fetched_at"]) or row["added_at"]
            status = row["status"] if row["status"] is not None else i.status
            if status is not None:
                bits.append(f"HTTP {status}")
            if row["reason"]:
                bits.append(_clean(mask_text(row["reason"]))[:80])
                said = error_code(row["reason"], None, row["status"])  # what it means (Google's 5xx: Google's)
                words = PLAIN[said].split("：", 1)[0] if said in PLAIN else ""
                if said not in ("fetch", "dns", "host_unreachable") and words and words not in CODE_ZH.get(code, ""):
                    bits.append(words)
            if row["attempts"]:
                bits.append(f"试 {row['attempts']}/{MAX_ATTEMPTS} 次")
            if code == "too_large" and row["size_declared"]:
                bits.append(f"宣告 {row['size_declared'] / 1e6:.1f} MB")
            if code == "list" and (walk := walks.get(uid)) is not None:
                at = walk["at"] or at
                bits.append(f"列表停在 {walk['ended_by'] or '（没写）'}（第 {walk['pages'] or 0} 页 "
                            f"{walk['rows'] or 0} 行）")
            bits.append(where(queue_of.get(row["host"]), row["host"], site))
        elif (a := acts.get(aid)) is not None:
            at = a["done"] or a["created"]
            bits.append(PRESS_ZH.get(a["kind"], a["kind"]))
            if a["status"] is not None:
                bits.append(f"HTTP {a['status']}")
            if a["reason"]:
                bits.append(_clean(mask_text(a["reason"]))[:80])
            bits.append(where(a["queue"], a["host"], site))
        else:  # an entry with no row: what its page said of it
            got = urls.get(holder.get(i.ref, -1))
            at = (_ts(got["fetched_at"]) or got["added_at"]) if got is not None else None
            if i.reason:
                bits.append(_clean(mask_text(i.reason))[:80])
        if i.what != "page" and i.pages > 1:
            bits.append(f"{i.pages} 页用到")
        out.append(Problem(at, scope.card_of_site.get(site, ""), site, mask_text(i.ref), kind, code,
                           CODE_ZH.get(code, code), " · ".join(b for b in bits if b), mask_url(i.url)))
    for key, rows in groups.items():
        site, code = key[0], key[1]
        rows.sort(key=lambda r: r["id"])
        first = rows[0]
        if code == "held":
            detail, url = f"{key[2]} · {len(rows):,} 个网址没抓", first["url"]
        else:
            detail = f"整个目录被拒 · {len(rows):,} 个网址没抓"
            url = f"{first['url'].split('://', 1)[0]}://{key[2]}/"
        at = min((r["added_at"] for r in rows if r["added_at"] is not None), default=None)
        out.append(Problem(at, scope.card_of_site.get(site, ""), site, f"u{first['id']}", "review", code,
                           CODE_ZH[code], detail, mask_url(url), len(rows)))
    return out


def server_events(conn: sqlite3.Connection, scope: Scope, hosts: dict[str, dict]) -> list[ServerEvent]:
    """The crawler's warnings and errors since this run of these sites began (scope.first_row), the newest
    EVENTS_KEPT, notes aside (NOTE_EVENTS), in plain words (plain_error), the message masked (design §1.3)."""
    out = []
    for r in conn.execute("""SELECT rowid AS rid, at, code, host, message FROM event
                             WHERE level IN ('warn', 'error') AND rowid >= ? ORDER BY rowid DESC LIMIT ?""",
                          (scope.first_row, EVENTS_KEPT)):
        if r["code"] in NOTE_EVENTS:
            continue
        h = hosts.get(r["host"] or "")
        card = scope.card_of_queue.get(h["queue"]) if h is not None else None
        detail = " · ".join(x for x in (r["host"] or "", _clean(mask_text(r["message"] or ""))[:300]) if x)
        out.append(ServerEvent(_ts(r["at"]), card, f"e{r['rid']}", r["code"], plain_error(r["code"], r["message"]),
                               detail))
    return out


def _tally(found: list[Problem]) -> dict[str, dict]:
    """Each site's 错: its lines, by kind (a line that stands for a cap's rows is one)."""
    out: dict[str, dict] = {}
    for p in found:
        c = out.setdefault(p.site, {"n": 0, "failed": 0, "review": 0, "pending": 0})
        c["n"] += 1
        c[{"failed": "failed", "pending_check": "pending"}.get(p.kind, "review")] += 1
    return out


# ---------------------------------------------------------------- the background snapshot (§2.4)

def slow_snapshot(conn: sqlite3.Connection, now: float, *, site_status=None, facts=None, scope: Scope | None = None,
                  on_lab: bool | None = None) -> dict:
    """What is too costly for every refresh, in one read transaction (design §2.4): page_completeness (about 1.4–2.8 s
    on 60k rows), each round-1 site's state and problem list (site_status, given these pages and facts' fx, read
    once), the problems of the run's sites (problems()) and each site's 错, the server events since this run of these
    sites began, and whether this machine is on campus (on_lab: the newest request that says). site_status, facts:
    the monitor's (Dashboard wires them); one not wired, or failing, is said so (status_error, errors_error), never
    guessed. scope: run_scope's, worked out here when not given."""
    out: dict[str, Any] = {"at": now}
    conn.execute("BEGIN")
    try:
        try:
            pages = conn.execute(f"SELECT {PAGE_COLUMNS} FROM page_completeness").fetchall()
        except sqlite3.Error as exc:
            pages, out["pages_error"] = [], f"{type(exc).__name__}: {exc}"
        out["pages"] = [dict(p) for p in pages]
        site_pages: dict[str, Counter] = defaultdict(Counter)
        for p in out["pages"]:
            site_pages[p["site"]][p["level"]] += 1
        out["site_pages"] = dict(site_pages)
        out["sites_with_res"] = {r[0] for r in conn.execute(
            "SELECT DISTINCT u.owner_site FROM url u WHERE u.kind = 'page' AND u.tier IN (0, 1) "
            "AND EXISTS (SELECT 1 FROM page_res pr WHERE pr.page_id = u.id)")}
        hosts = _hosts(conn)
        scope = scope or run_scope(conn, hosts)
        out["scope_sites"] = scope.sites
        if on_lab is None:  # where the newest request that knew it was made from (unknown: the one before)
            got = conn.execute("SELECT campus FROM request WHERE campus IN ('on', 'off') ORDER BY id DESC LIMIT 1"
                               ).fetchone()
            on_lab = got is not None and got[0] == "on"
        out["on_lab"] = on_lab
        fx, raw = None, {}
        if facts is not None and site_status is not None and "pages_error" not in out:
            try:
                fx = facts(conn, out["pages"])
            except Exception as exc:  # noqa: BLE001 - site_status then reads them itself
                out["facts_error"] = f"{type(exc).__name__}: {exc}"
        if site_status is None:
            out["status_error"] = "monitor.site_status 还没有"
        elif "pages_error" not in out:
            try:
                raw = dict(_call(site_status, conn, now, pages=out["pages"], fx=fx))
            except Exception as exc:  # noqa: BLE001 - the site page says 没检查, the badge 错 …, never a colour
                out["status_error"] = f"{type(exc).__name__}: {exc}"
        out["statuses"], out["fx"] = raw, fx
        out["events"] = server_events(conn, scope, hosts)
        why = out.get("pages_error") or out.get("status_error")
        if why:
            out["errors_error"] = why
        else:
            try:
                out["problems"] = problems(conn, raw, out["pages"], scope=scope, on_lab=on_lab, fx=fx)
            except (ValueError, AttributeError, TypeError) as exc:
                out["errors_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        conn.execute("COMMIT")
    out["errors_by_site"] = _tally(out.get("problems") or [])
    return out


# ---------------------------------------------------------------- the problem log (§1.3)

def log_view(snap: dict, slow: dict | None, *, site: str | None = None, card: str | None = None,
             code: str | None = None) -> dict:
    """The problem log as the page and the file show it (design §1.3): the lines of one site, of one card's sites, or
    of every site of the run, newest first (code: only those of one code); their counts, which are 错 N of that site,
    card or of the whole; the subtotals by code; the server events (never in a site's view; one that names no host
    in every card's) and the politeness violations (not in a site's view), neither counted. error: why there are no
    lines yet (the background snapshot is still being worked out, or failed)."""
    scope: Scope = snap["scope"]
    errs, why = _site_errors(slow, scope)
    if site is not None:
        sites = {site} if site in scope.sites else set()
    elif card is not None:
        c = scope.card(card)
        sites = set(c.sites) if c is not None else set()
    else:
        sites = set(scope.sites)
    names = {c.key: c.name for c in scope.cards}
    found = [p for p in (slow or {}).get("problems") or [] if p.site in sites] if errs is not None else []
    counts = _tally(found)
    total = {k: sum(c[k] for c in counts.values()) for k in ("n", "failed", "review", "pending")}
    by_code = Counter(p.code for p in found)
    codes = [{"code": c, "label": CODE_ZH.get(c, c), "n": n} for c, n in sorted(by_code.items(), key=lambda x: (-x[1],
                                                                                                                 x[0]))]
    shown = [p for p in found if code is None or p.code == code]
    shown.sort(key=lambda p: (p.at is not None, p.at or 0.0, p.ref), reverse=True)
    rows = [{"at": p.at, "card": p.card, "card_name": names.get(scope.card_of_site.get(p.site, p.card), ""),
             "site": p.site, "site_name": scope.names.get(p.site, p.site), "ref": p.ref, "kind": p.kind,
             "kind_zh": KIND_ZH.get(p.kind, p.kind), "code": p.code, "label": p.label, "detail": p.detail,
             "url": p.url, "n": p.n} for p in shown]
    events = []
    if site is None:
        for e in (slow or {}).get("events") or []:
            if card is None or e.card is None or e.card == card:
                events.append({"at": e.at, "card": e.card, "card_name": names.get(e.card, ""), "ref": e.ref,
                               "code": e.code, "label": e.label, "detail": e.detail})
    polite = []
    if site is None:
        for r in reversed(snap.get("polite", {}).get("records") or []):
            owner = scope.card_of_queue.get(r["server"]) or scope.card_of_queue.get(f"ip:{r['server']}")
            if card is None or owner is None or owner == card:
                polite.append({**r, "card": owner, "card_name": names.get(owner, ""),
                               "kind_zh": POLITE_ZH.get(r["kind"], r["kind"])})
    title = (scope.names.get(site, site) if site is not None else names.get(card, card) if card is not None
             else "全部网站")
    return {"at": snap["at"], "slow_at": (slow or {}).get("at"), "site": site, "card": card, "code": code,
            "title": title, "error": None if errs is not None else why, **total, "codes": codes, "rows": rows,
            "events": events, "polite": polite, "cards": [(c.key, c.name, c.queues) for c in scope.cards],
            "polite_hard": snap.get("polite", {}).get("hard", 0)}


def log_text(view: dict, db: Path) -> str:
    """The problem log as a file (design §1.3): `#` lines first (when, from where, the counts, what was looked at, the
    columns, which queues each card is), then one problem a line, oldest first, the fields tab-separated (a tab or a
    line break inside one is a space), then the server events and the politeness violations, each under a `#` line.
    Times are Taipei's."""
    head = (f"# 爬虫问题记录 · 产生 {fmt_stamp(view['at'])}（台北）· 资料 {db} · 范围 {view['title']}"
            + (f" · 只看 {view['code']}" if view["code"] else ""))
    if view["error"]:
        counts = f"# 错的条数还没有：{view['error']}"
    else:
        counts = (f"# {view['n']} 条（失败 {view['failed']} · 要人看 {view['review']} · 待判定 {view['pending']}）"
                  f" · 资料时间 {fmt_stamp(view['slow_at'])}（每 2 分钟更新）· 服务器事件 {len(view['events'])}（不算条数）"
                  f" · 礼貌违规 {view['polite_hard']}")
    cards = "# 服务器：" + "；".join(f"{name}={','.join(queues)}" for _key, name, queues in view["cards"])
    lines = [head, counts, cards, "# " + "\t".join(LOG_COLUMNS)]

    def line(*fields) -> str:
        return "\t".join(_clean(str(f if f not in (None, "") else "—")) for f in fields)

    for r in reversed(view["rows"]):
        lines.append(line(fmt_stamp(r["at"]), r["card_name"], r["site"], r["ref"], f"{r['kind_zh']}·{r['label']}",
                          r["code"], r["detail"], r["url"]))
    lines.append("# 服务器事件（不算条数）")
    for e in sorted(view["events"], key=lambda e: (e["at"] or 0.0, e["ref"])):
        lines.append(line(fmt_stamp(e["at"]), e["card_name"], "", e["ref"], f"服务器·{e['label']}", e["code"],
                          e["detail"], ""))
    lines.append("# 礼貌违规（应该一直是空的）")
    for p in sorted(view["polite"], key=lambda p: p["at"] or 0.0):
        lines.append(line(fmt_stamp(p["at"]), p["card_name"] or p["server"], "", "", f"礼貌·{p['kind_zh']}", p["kind"],
                          mask_text(p["detail"]), mask_url(p["url"])))
    return "\n".join(lines) + "\n"


def _frozen_name(snap: dict) -> str | None:
    """The per-run copy's name (design §1.3: 每次爬虫跑完，另外存一份): problems-<the run_end's YYYYMMDD-HHMM, Taipei>.log,
    once the last run has ended (no run holds the lock, a run_end after the last run_start); None before."""
    run = snap["run"]
    if run["running"] or run["state"] not in ("ended", "bad_end") or run["ended_at"] is None:
        return None
    return f"problems-{datetime.fromtimestamp(run['ended_at'], TZ):%Y%m%d-%H%M}.log"


def write_logs(root: Path, snap: dict, slow: dict | None, *, latest: bool = True) -> list[Path]:
    """Write the problem log under root/reports: problems-latest.log in its place (a temporary file, then os.replace),
    and the per-run copy when the last run has ended and there is none yet (created exclusively: of two writers, the
    second writes nothing). Returns the files written."""
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    text = log_text(log_view(snap, slow), root / "frontier.db")
    out = []
    if latest:
        tmp = reports / f".{LATEST_LOG}.{os.getpid()}.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, reports / LATEST_LOG)
        out.append(reports / LATEST_LOG)
    name = _frozen_name(snap)
    if name is not None and not (reports / name).exists():
        try:
            with open(reports / name, "x", encoding="utf-8") as fh:
                fh.write(text)
            out.append(reports / name)
        except FileExistsError:
            pass
    return out


def write_problem_log(root: Path, now: float | None = None) -> list[Path]:
    """`nknu archive status`'s problem log (design §1.3): the numbers and the background snapshot worked out once
    (with the monitor's site_status and facts), written by write_logs. Read-only on frontier.db."""
    from nknu.archive import monitor
    from nknu.archive.crawler import lock_holder

    now = time.time() if now is None else now
    conn = connect(root / "frontier.db")
    try:
        snap = snapshot(conn, now, running=lock_holder(root), stop_file=(root / STOP_FILE).exists())
        slow = slow_snapshot(conn, now, site_status=monitor.site_status, facts=monitor.facts, scope=snap["scope"])
    finally:
        conn.close()
    return write_logs(root, snap, slow)


# ---------------------------------------------------------------- the acceptance page (/progress/checks)

def checks_view(conn: sqlite3.Connection, now: float, slow: dict | None, *, checks=None,
                root: Path | None = None) -> dict:
    """The acceptance checks, worked out when the page is opened (design §4: kept as they were until P49 settles what
    爬完 is): the reconciliations and what they found the crawl has no row for, the round-two list, and the cheap
    checks (monitor.checks, given the background snapshot's pages, states and facts when there is one)."""
    out: dict[str, Any] = {"at": now, "slow_at": (slow or {}).get("at")}
    conn.execute("BEGIN")
    try:
        out["reconcile"], out["missing_found"] = _reconciled(conn)
        out["round2"] = _round2(conn)
    finally:
        conn.execute("COMMIT")
    if checks is None:
        out["checks"], out["checks_error"] = None, "monitor.checks 还没有"
        return out
    good = slow is not None and not slow.get("pages_error")
    conn.execute("BEGIN")
    try:
        cs = _call(checks, conn, now, cheap=True, pages=slow["pages"] if good else None,
                   statuses=(slow or {}).get("statuses") or None, fx=(slow or {}).get("fx") if good else None,
                   root=root)
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
    from nknu.archive import monitor

    rec, missing = {}, []
    for r in conn.execute("SELECT site, batch, at, checks, cover_cc, cover_old, gates_ok FROM reconcile_site"):
        try:
            checks = json.loads(r["checks"] or "{}")
        except ValueError:
            checks = {}
        if not isinstance(checks, dict):
            checks = {}
        # an older reconciliation's R5 (taken out 10-26) is read past, as monitor.reconciled does
        failed = sorted(k for k, v in checks.items() if _status_of(v) == "fail" and k not in monitor.DROPPED_GATES)
        rec[r["site"]] = {"batch": r["batch"], "at": _ts(r["at"]), "gates_ok": r["gates_ok"],
                          "cover_cc": r["cover_cc"], "cover_old": r["cover_old"], "failed": failed}
        for item, v in checks.items():
            if item in FOUND_CHECKS and isinstance(v, dict):
                n, urls = _found(v)
                if n:
                    missing.append({"site": r["site"], "item": item, "n": n, "urls": urls[:10]})
    return rec, missing


def _round2(conn: sqlite3.Connection) -> dict:
    """What round two holds (§14.2): rows by why, by the seed list they came from, and the top off-campus domains. A
    resource that has no url row of its own (ROUND2_RES: Google Sites' runtime pictures, design §23 B3) is counted by
    its page_res rows, once per picture of a page; its rows, when it has any, are not counted again."""
    marks = ",".join("?" * len(ROUND2_RES))
    rows = [dict(r) for r in conn.execute(f"""SELECT reason, count(*) n, count(DISTINCT owner_site) sites FROM url
                                              WHERE state = 'later' AND coalesce(reason, '') NOT IN ({marks})
                                              GROUP BY reason""", ROUND2_RES)]
    rows += [dict(r) for r in conn.execute(f"""SELECT pr.why AS reason, count(DISTINCT pr.res_key) n,
                                                      count(DISTINCT p.owner_site) sites
                                               FROM page_res pr JOIN url p ON p.id = pr.page_id
                                               WHERE pr.need = 'round2' AND pr.why IN ({marks}) GROUP BY pr.why""",
                                           ROUND2_RES) if r["n"]]
    rows.sort(key=lambda r: -r["n"])
    by_why = [{"why": r["reason"] or "", "zh": ROUND2_ZH.get(r["reason"] or "", r["reason"] or "（没写原因）"),
               "n": r["n"], "sites": r["sites"]} for r in rows]
    by_source = [dict(r) for r in conn.execute("""SELECT s.source, count(*) n FROM url u
                                                   JOIN seed_src s ON s.url = u.url
                                                   WHERE u.state = 'later' GROUP BY s.source ORDER BY n DESC""")]
    domains = [dict(r) for r in conn.execute("SELECT domain, n FROM offsite ORDER BY n DESC LIMIT 20")]
    return {"by_why": by_why, "by_source": by_source, "domains": domains, "total": sum(x["n"] for x in by_why)}


# ---------------------------------------------------------------- one site (§14.2: 点进去看明细)

def _no_row_result(need: str, why: str | None, res_id: int | None = None, action_id: int | None = None) -> str:
    """A page's resource with neither a url row nor a press there (outcome._resource_ctes, r00's branches): one that
    names a row or a press that is not there is lost; one that names none is what its why says: a school error page's
    address is the school's broken link (bad_link, 要人看: outcome._kept_out; review 10-26, it read 规则不抓)."""
    why = why or ""
    if res_id is not None or action_id is not None:
        return "not_discovered"
    if need == "round2" and why.startswith("round2:"):
        return "later"
    if need == "must" and why.startswith("unhandled:"):
        return "unhandled"
    if why in outcome.BROKEN_OUT:  # an error page's address, or this machine's (11-06)
        return "bad_link"
    if why.startswith("out:login"):
        return "login"
    if why.startswith("out:"):
        return "rule"
    return "not_discovered"


def site_run(snap: dict, site: str) -> dict | None:
    """Whether a run is fetching this site, for its page (review 11-04: monitor's 还在抓 says only that rows wait):
    going, when a run goes (_run_state: its own process holds run.lock), answers and names the site; else why not, in
    words. None when the numbers could not be read."""
    if "problem" in snap:
        return None
    run, scope = snap["run"], snap["scope"]
    if run["running"] is None:
        return {"going": False, "why": f"没在跑：{run['words']}"}
    if run["hung"]:
        return {"going": False, "why": run["words"]}
    if site not in scope.sites:
        return {"going": False, "why": "这次 run 没有这个网站（开跑时 --sites 没列它）"}
    return {"going": True, "why": ""}


def site_detail(conn: sqlite3.Connection, site: str, now: float, slow: dict | None, *, bucket: str | None = None,
                label: str | None = None, run: dict | None = None) -> dict | None:
    """One site's pages, by their completeness (§14.2: 连接失败 first, 完整 last; an address that redirects is written
    on the page it lands on; the resources with no row, not_discovered and unhandled, under their page), and its other
    addresses and presses by the ten categories (抓到了 last). bucket, label: only that category, or that page label.
    shown: the state's sign, words and colour; run: site_run's, by which the monitor's 还在抓 (rows wait) is drawn 没抓完
    while no run of the site goes, why first (the state stays the monitor's). None when the site is not on the list."""
    conn.execute("BEGIN")
    try:
        p = conn.execute("SELECT site, name, imp, round, home, platform, base_urls FROM site_plan WHERE site=?",
                         (site,)).fetchone()
        if p is None:
            return None
        info = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, url, title, page_type, final_url, flags FROM url WHERE owner_site=? AND kind='page'", (site,))}
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
            if res in ("not_discovered", "unhandled", "bad_link"):
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
                      # design §23 E3: its words alone would be thin, its body a picture (内容在图片里)
                      "in_image": "in_image" in (u.get("flags") or "").split(","),
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
        said = outcome.DATA_WORDS.get(a["reason"] or "") if a["kind"] == "data" else None  # 「时间轴是空的」 (§23 C1)
        rows.append({"what": "press", "id": a["id"], "url": a["target"], "title": a["row_text"] or "",
                     "kind": a["kind"], "result": a["result"],
                     "zh": outcome.RESULT_LABEL.get(a["result"], a["result"]) + (f"：{said}" if said else ""),
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
    shown = STATE_SHOW.get(state["state"], STATE_SHOW["unchecked"])
    if state["state"] == "crawling" and run is not None and not run["going"]:
        state, shown = {**state, "why": [run["why"], *state["why"]]}, UNFINISHED_SHOW
    return {"site": site, "name": p["name"] or site, "home": p["home"], "tier": tier_of(dict(p)),
            "round": p["round"], "platform": p["platform"], **state, "shown": shown, "pages": pages[:MAX_ROWS],
            "page_counts": [(c, LABEL_ZH.get(c, c), page_counts[c]) for c in PAGE_ORDER if page_counts[c]],
            "rows": rows, "bucket_counts": [(b, BUCKET_ZH[b], bucket_counts[b]) for b in BUCKET_ORDER
                                            if bucket_counts[b]],
            "bucket": bucket, "label": label, "slow_at": (slow or {}).get("at"),
            "reconcile": None if rec is None else {
                "batch": rec["batch"], "at": _ts(rec["at"]), "cover_cc": rec["cover_cc"],
                # the monitor's reading (an older R5 read past, menu links a person marked since: §10.2.1, §18.2b)
                "gates_ok": rec_now.passed if rec_now is not None else rec["gates_ok"],
                "menu": rec_now.menu_words() if rec_now is not None else "",
                # §10.5: stale once a run, a rejudge or a person's verdict covered the site after it (review 10-26)
                "fresh": rec_now.fresh if rec_now is not None else None,
                "stale": rec_now.stale_words() if rec_now is not None else "",
                "cover_lines": rec_now.cover_lines() if rec_now is not None else [],
                "blind": rec_now.blind_words() if rec_now is not None else ""},
            "at": now}


# ---------------------------------------------------------------- the cache and the background thread (§2.4, §2.8)

class Dashboard:
    """The numbers the viewer draws: snapshot() kept CACHE_S seconds, slow_snapshot() every SLOW_EVERY_S seconds in
    a daemon thread (started by the first main()), the request log read incrementally. Read-only on frontier.db.
    site_status, checks and facts default to the monitor's. freeze_reports (the viewer's): after each background
    snapshot, the per-run copy of the problem log is written once the last run has ended (write_logs); tests leave it
    off."""

    def __init__(self, root: Path, *, clock=time.time, site_status=None, checks=None, facts=None,
                 cache_s: float = CACHE_S, slow_every_s: float = SLOW_EVERY_S, background: bool = True,
                 freeze_reports: bool = False):
        self.root, self.db = root, root / "frontier.db"
        self.clock, self.cache_s, self.slow_every_s, self.background = clock, cache_s, slow_every_s, background
        self.freeze_reports = freeze_reports
        if site_status is None or checks is None or facts is None:
            from nknu.archive import monitor

            # the monitor's own, for what is not given: its facts read once and handed to site_status (fx=)
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
                self._main["update"] = update_state(conn, self.root, now)
            except (FileNotFoundError, OutdatedFrontier, sqlite3.Error) as exc:
                self._close_conn()
                return {"at": now, "problem": _problem(exc, self.db)}
            self._main_at = now
            return self._main

    def log(self, site: str | None = None, card: str | None = None, code: str | None = None) -> dict:
        """The problem log of one site, one card or the whole run (log_view), from the numbers and the background
        snapshot the cards show: its counts are their 错."""
        snap = self.main()
        if "problem" in snap:
            return {"at": snap["at"], "problem": snap["problem"]}
        return log_view(snap, self.slow, site=site, card=card, code=code)

    def log_file(self, site: str | None = None, card: str | None = None, code: str | None = None) -> str:
        """The same lines as a tab-separated file (log_text)."""
        view = self.log(site, card, code)
        if "problem" in view:
            return f"# {view['problem']}\n"
        return log_text(view, self.db)

    def checks_page(self) -> dict:
        """The acceptance checks (checks_view), worked out now."""
        try:
            conn = connect(self.db)
        except (FileNotFoundError, OutdatedFrontier, sqlite3.Error) as exc:
            return {"problem": _problem(exc, self.db)}
        try:
            return checks_view(conn, self.clock(), self.slow, checks=self.checks, root=self.root)
        finally:
            conn.close()

    def site(self, key: str, *, bucket: str | None = None, label: str | None = None) -> dict | None:
        """One site's page (site_detail), its state told by whether a run of it goes (site_run, from the numbers the
        cards show)."""
        run = site_run(self.main(), key)
        try:
            conn = connect(self.db)
        except (FileNotFoundError, OutdatedFrontier, sqlite3.Error) as exc:
            return {"problem": _problem(exc, self.db)}
        try:
            return site_detail(conn, key, self.clock(), self.slow, bucket=bucket, label=label, run=run)
        finally:
            conn.close()

    def refresh_slow(self) -> dict | None:
        """Work out the background snapshot now (the thread's step; tests call it directly), and with freeze_reports
        the per-run copy of the problem log once the last run has ended."""
        try:
            conn = connect(self.db)
        except (FileNotFoundError, OutdatedFrontier, sqlite3.Error):
            return None
        try:
            slow = slow_snapshot(conn, self.clock(), site_status=self.site_status, facts=self.facts)
        except sqlite3.Error as exc:
            why = f"{type(exc).__name__}: {exc}"
            slow = {"at": self.clock(), "pages_error": why, "errors_error": why, "pages": []}
        finally:
            conn.close()
        with self._lock:
            self.slow = slow
            self._main_at = float("-inf")  # the next main() shows it
        if self.freeze_reports:
            snap = self.main()
            if "problem" not in snap and _frozen_name(snap) is not None:
                with suppress(OSError):
                    write_logs(self.root, snap, slow, latest=False)
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


# the commands the acceptance page shows to copy (§18.3 step 4: the order after a run stopped; no button: the dashboard
# writes nothing), as the lab machine runs them (review 11-04: the Mac's .venv/bin/nknu is not there): one-off
# containers of the crawler service, its own code and archive (research/crawler_lab_2026-11-04/compose.yaml; over SSH
# the DOCKER_CONFIG prefix first). The browser check and R6 need Chrome, which the image gets once the crawl is done
# (design 2026-11-04 §3)
LAB_ARCHIVE = ('docker compose -f C:\\nknu-crawl\\compose.yaml run --rm crawler '
               'python -c "from nknu.admin.cli import app; app()" archive')
COMMANDS = {
    "after": ("# 在实验室电脑上（经 SSH 时前面加 DOCKER_CONFIG 那段）\n"
              "# 1. 浏览器判定网页类型（只判定，不抓；要先做加了 Chrome 的映像）\n"
              f"{LAB_ARCHIVE} jscheck --dir /crawl/archive\n"
              "# 2. 对账，含浏览器抽查 R6（同样要 Chrome）\n"
              f"{LAB_ARCHIVE} reconcile --dir /crawl/archive --batch <批>\n"
              "# 3. 验收（不连网）\n"
              f"{LAB_ARCHIVE} verify --dir /crawl/archive"),
}
