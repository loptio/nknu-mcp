"""The browser check after the round (round-1 design §8.4, §17 step 11): `nknu archive jscheck`. 10-25's jstest.py
(research/project_audit_2026-10-25/js_test_2026-10-25) as a module: a browser (Playwright 1.64.1 driving this
machine's own Chrome) opens the pages the round could not judge by itself and compares what it shows with what the
page's own answer holds, only to judge the page type. Nothing it sees is stored as content, and no address it meets is
queued to fetch: an address the browser was sent on to is written down as round two's (state later), never pending
(record 二·3, 四).

What it opens (§8.3, §8.4):
- one page of each type still todo that has pages this round (n_pages > 0), three for a type a click sent back to todo
  (why click: the pages with the click first), only from this round's 200 HTML pages; a type of the 10-25 test with no
  page this round is not opened and the report lists it with its sample's state in the old archive (read only,
  immutable), but 工设系's withdrawn uploads/* (pagetypes.withdraw_files_only);
- every page with a strong sign (js_hint) and every page whose jump menu fires itself on load (its page_res mark
  auto:<key>, why select:auto) that jscheck has not opened since its last fetch, whatever its type says and whoever
  judged it;
- once a type's page sent the browser elsewhere, that type's other pages under 150 characters without the js_redirect
  flag (a redirect inside a function or a setTimeout, which the crawl's literal rule does not read).
Each page it opens gets its js_page row (its own verdict: ok, js, frame, click, or unresolved when two looks found no
answer); a type's verdict is written with source jscheck. A page sent elsewhere gets js_redirect and final_url when it
has under 150 characters (login too when it lands on a login), the type ok with the note js_redirect (or
js_redirect_login) and stop:<where>, and where it stopped one url row state later, reason round2:jscheck_redirect,
found_via jscheck_redirect, unless a row is there already or a rule leaves it out.

Every request goes through our own program (§8.4): Chrome starts behind a proxy that answers nothing
(--proxy-server=127.0.0.1:9, loopback included), sends no UDP of its own (WebRTC's STUN and TURN go past both the proxy
and the route: --webrtc-ip-handling-policy=disable_non_proxied_udp, and no page has the WebRTC interfaces, review
10-26), with no secret in its environment, and every request its pages make is caught (context.route) and sent
by the crawl's client (crawler.archive_client: one kept connection per server queue), one request at a time per
queue, the gap after each answer (2–5 s unless told, never less than the answer took, at most 30 s), a host's
Crawl-delay, the machine-wide lock; each request on the wire is a request row (purpose jscheck) and its answer is in a
WARC file of jscheck's own (warc/YYYY/MM/DD/jscheck-<queue>-<run>.warc.gz), evidence only. A server that says 503,
asks for a wait or errs three times in a row rests as the crawl's do (the frontier's pause table, §6.10); a server
that does not connect rests 15 min. Not sent: images, media, fonts and style sheets, the statistics, tracking, social,
map and translation hosts (jstest.py's BLOCK_HOSTS), a site's favicon, and login systems and internal services
(NEVER_SEND). Everything else scope.judge leaves out is sent, since judging is not fetching (the library's home reads
Google sheets). A POST goes out once, and only a form body (the client sends forms only). Chrome would follow a 3xx it
is handed past the route, so a page's or a frame's 3xx goes back as a page that refreshes to the Location (a
navigation the route catches) and any other request's hops are followed here, each through the queue; a look sends
at most MAX_REQUESTS requests and MAX_NAVS navigations (a redirect loop, a page that keeps polling).

How a page is judged (§8.4, jstest.py:259-265), in this order:
1 the page's own answer failed (no answer, not 200, Chrome's error page): no conclusion;
2 the browser was sent on (a main-frame navigation to another address, by script or by 3xx): not js; login when the
  address is a login's (scope) or Google's sign-in, or the page it landed on is a sign-in form;
3 new text (lines the page's own answer does not have) of 200 characters or more once repeats are taken out (a
  marquee), and 1.3 times the page's own: js (method data_url when a data request's answer holds the new text);
4 200 characters or more in the school's frames: frame;
5 no conclusion: nothing shown, nothing in the page's own answer, too slow or never quiet, a request the page needed
  that got no answer, or a data request whose answer holds new text below the js line (data_text, english's home);
6 a click this round cannot press on the page (page_res how click, need round2): click, its elements in data_urls;
7 ok.
No conclusion: another of this round's 200 pages is opened (the same one when there is no other); still none, the type
is unresolved (inconclusive:<why>, 要人看) and `jscheck --set` is how a person judges it. A server resting, or a stop,
is no answer at all: the type stays as it was and nothing is written for that page.

Before anything: the archive is a round-1 one (frontier.round_of, never the 10-24 archive), no archive run holds
run.lock (jscheck holds it itself to the end, so that `archive stop` stops it too and no run starts meanwhile), and
the machine-wide lock is free (jscheck holds it to the end). The browser must be there: Playwright 1.64.1 (the
pyproject extra browser) and this machine's Chrome; without them jscheck says so and every type stays todo. A STOP
file in the archive directory stops it after the pages in flight; it ends with a jscheck_end event (finished, stop or
error, the types judged, the types still todo).

What the design leaves open and this module decides (【Claude 的执行办法】): login systems and internal services are
never sent (CLAUDE.md 「登录系统、验证码照旧不碰」; scope's other HARD_OUT rules are not applied, §8.4); a redirect is a
main-frame navigation, so a pushState or a hash change is no redirect; repeats are counted once by the line and by a
line's own period; a needed request with no answer (a script, a frame, a data request) is no conclusion rather than
a quiet ok; the page's own answer is the "direct" text (jstest.py), the crawl's text_chars says whether it is under
150 characters; a page whose look found no answer gets js_page unresolved, and `--set` writes the person's verdict
over its type's unresolved js_page rows too, so that its pages follow it; a sign-in form is the crawl's rule (a
password box on a page of few links, or a login title or path); note tokens keep no comma (an address's comma is
%2C, a person's note's is ，); the routing covers every page of the browser's context (context.route, a popup too).

SpotChecker is reconcile's R6 (§18.2b): the same browser, dead proxy, bridge and queues, one page at a time with a
picture of it (request rows purpose spotcheck), under the locks its caller holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import html as htmllib
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, Self
from urllib.parse import parse_qsl, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import google_api, links, outcome, pagetypes, round1, scope
from nknu.archive.crawler import (
    _KIND_OF0,
    _LOGIN_TITLE,
    _PRIO_OF0,
    BREAKER_THRESHOLD,
    CONN_KINDS,
    CONN_PAUSE_S,
    COOLDOWN,
    GAP,
    LOGIN_MAX_LINKS,
    MAX_BYTES,
    NO_RATE,
    REST_NOW,
    SERVER_REST_S,
    SLOW_CAP_S,
    AlreadyRunning,
    _erred,
    archive_client,
    check_gap,
    error_kind,
    hold_run_lock,
    netcheck,
    resolve,
)
from nknu.archive.frontier import Frontier, round_of, utc_now, wall
from nknu.archive.robots import MAX_CRAWL_DELAY_S
from nknu.crawl import schoollock
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import (
    CREDENTIALS,
    USER_AGENT,
    ArchiveWriteError,
    Client,
    FetchError,
    HostCircuitOpen,
    RetryLater,
    Snapshot,
)
from nknu.crawl.warc import WarcSink

log = logging.getLogger("nknu.archive.jscheck")

PURPOSE = "jscheck"  # request.purpose of every request jscheck sends
PLAYWRIGHT_VERSION = "1.64.1"  # pyproject's extra browser, the 10-25 test's version
DEAD_PROXY = "127.0.0.1:9"  # a port nothing answers on: what Chrome sends by itself goes nowhere (§8.4)
# Playwright 1.64.1's own --disable-features (its chromiumSwitches' disabledFeatures: no Google queries at start,
# interception that keeps a redirect's request). Chrome reads only the last --disable-features it is given, and ours
# comes after Playwright's, so ours carries Playwright's list too (review 10-26; test_review_phase4 compares it with the
# installed driver's)
PLAYWRIGHT_DISABLED_FEATURES = (
    "AvoidUnnecessaryBeforeUnloadCheckSync", "DestroyProfileOnBrowserClose", "DialMediaRouteProvider",
    "GlobalMediaControls", "HttpsUpgrades", "LensOverlay", "MediaRouter", "PaintHolding", "ThirdPartyStoragePartitioning",
    "BlockOriginHeaderModificationOnRedirect", "AvoidCorsURLLoaderRestartOnRedirect", "Translate", "AutoDeElevate",
    "OptimizationHints", "NetworkTimeServiceQuerying", "AimEnabled", "msForceBrowserSignIn",
    "msEdgeUpdateLaunchServicesPreferredVersion")
# jstest.py's: an http:// address stays http:// (the school's pages are asked as written)
OUR_DISABLED_FEATURES = ("HttpsUpgrades", "HttpsFirstBalancedModeAutoEnable", "HttpsFirstModeV2ForTypicallySecureUsers")
# jstest.py's flags, and the dead proxy for every address, loopback included (Chrome leaves loopback out by default).
# The proxy and the route hold for HTTP only: WebRTC sends its STUN and TURN packets over UDP by itself, past both
# (review 10-26: 18 packets reached a listener on this machine). Chrome's policy disable_non_proxied_udp sends no UDP
# that does not go through the proxy, which answers nothing (the review measured 0; --force-webrtc-ip-handling-policy
# changed nothing), and NO_WEBRTC takes the interfaces away from every page as well
CHROME_ARGS = (f"--proxy-server={DEAD_PROXY}", "--proxy-bypass-list=<-loopback>",
               "--disable-features=" + ",".join(dict.fromkeys((*PLAYWRIGHT_DISABLED_FEATURES, *OUR_DISABLED_FEATURES))),
               "--webrtc-ip-handling-policy=disable_non_proxied_udp",
               "--no-pings")
# run in every frame of every page before its own scripts (context.add_init_script): a page cannot open a peer
# connection or a WebTransport session, not even through a new blank frame's window (its scripts run there too)
NO_WEBRTC = """(() => {
  for (const name of ["RTCPeerConnection", "webkitRTCPeerConnection", "RTCDataChannel", "RTCSessionDescription",
                      "RTCIceCandidate", "RTCRtpSender", "RTCRtpReceiver", "RTCRtpTransceiver", "WebTransport"]) {
    try { delete globalThis[name]; } catch (e) {}
    try { Object.defineProperty(globalThis, name, {value: undefined, writable: false, configurable: false}); }
    catch (e) {}
  }
})();"""
# what Chrome is never handed (CLAUDE.md: only the fetch service holds the key): the crawl's own credentials
# (fetch.CREDENTIALS) and the names Chromium itself reads a Google key from. jscheck and R6 send no credential at all
SECRET_ENV = frozenset({*(env for _header, env, _hosts in CREDENTIALS.values()), "GOOGLE_API_KEY",
                        "GOOGLE_DEFAULT_CLIENT_ID", "GOOGLE_DEFAULT_CLIENT_SECRET"})
# what the browser may not load at all (design §8.4: images, media, fonts, style sheets; jstest.py:38-41's hosts)
BLOCK_TYPES = frozenset({"image", "media", "font", "stylesheet"})
BLOCK_HOSTS = re.compile(r"(?i)(^|\.)(youtube\.com|youtube-nocookie\.com|ytimg\.com|facebook\.(com|net)|fbcdn\.net|"
                         r"google-analytics\.com|googletagmanager\.com|doubleclick\.net|googlesyndication\.com|"
                         r"maps\.googleapis\.com|maps\.google\.com|instagram\.com|twitter\.com|x\.com|addthis\.com|"
                         r"sharethis\.com|clarity\.ms|hotjar\.com|translate\.googleapis\.com|translate\.google\.com|"
                         r"translate-pa\.googleapis\.com)$")
# scope.judge's reasons a request is never sent for, judging or not: a login system (a captcha's picture among them)
# and the internal web services that answer with student records (scope.INTERNAL_HOSTS) 【Claude 的执行办法】
NEVER_SEND = frozenset({"login", "internal_service"})
# every 4xx and 5xx is the server's answer, handed to the browser as it is (408, 429 and the retried 5xx are failures)
ANSWERS = frozenset(range(400, 600))
JS_CHARS, JS_RATIO, FRAME_CHARS = 200, 1.3, 200  # jstest.py:259-265
SHORT_PAGE = links.JS_REDIRECT_MAX_CHARS  # under this many characters, a page sent on is a redirect page (§8.3)
CLICK_SAMPLES = 3  # a type sent back by a click: up to three pages, the click's first (§8.4)
TRIES = 2  # looks per page or sample slot before unresolved (§8.4)
PAGES_AT_ONCE = 8  # jstest.py: pages open at once (on different server queues)
GOTO_TIMEOUT_MS, IDLE_TIMEOUT_MS, SETTLE_MS = 240_000, 90_000, 1500  # jstest.py
REST_WAIT_MAX_S = 1800.0  # a page whose server rests longer than this is left for the next jscheck
CACHE_MAX_BYTES, CACHE_ITEM_MAX = 200 * 1024**2, 5 * 1024**2  # scripts kept per run (jstest.py's cache)
STOP_POLL_S = 1.0
MAX_HOPS = 5  # a script's or a data request's redirects followed per request
MAX_NAVS = 10  # navigations per look (the page, its redirects, a script sending it on)
MAX_REQUESTS = 300  # requests sent per look: a page that keeps asking is not followed for ever
_REFRESH = '<!doctype html><meta http-equiv="refresh" content="0;url={}">'
TEXT_KEEP = 400_000  # characters of a data request's answer kept to look for new text (jstest.py)
STRICT = ("js", "click", "frame", "ok")  # a type's verdict from its samples: the strictest (pagetypes.STRICTNESS)
END_REASONS = ("finished", "stop", "error")
DATA_KINDS = frozenset({"xhr", "fetch"})
# requests a page needs: one of them sent and not answered, the page's look has no conclusion
NEEDED_KINDS = frozenset({"document", "script", "xhr", "fetch", "eventsource"})
# request headers the browser's request does not pass on: the connection's own, the client's (its User-Agent, its
# Accept-Encoding: it decodes what it asked for), secrets, and Chrome's own client hints, which name HeadlessChrome
_HOP = frozenset({"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "host",
                  "upgrade", "proxy-connection", "te", "trailer"})
_NOT_PASSED = _HOP | {"user-agent", "accept-encoding", "authorization", "proxy-authorization", "x-goog-api-key",
                      "cookie2"}
_FORM = "application/x-www-form-urlencoded"
_PUNCT = re.compile(r"[\[\]【】()（）《》〈〉「」『』:：,，.。!！?？/|\-—_\s]")  # jstest.py's windows
_PUNCT_Q = re.compile(r"[\[\]【】()（）《》〈〉「」『』:：,，.。!！?？/|\-—_\s\"']")
_U_ESC = re.compile(r"\\u([0-9a-fA-F]{4})")
_NOWHERE = ("chrome-error:", "about:")
_TEXT_DROP = ("script", "style", "noscript", "header", "nav", "footer", "aside", "template")  # jstest.py text_len


class JsCheckRefused(RuntimeError):
    """jscheck (or --set) may not start: not a round-1 archive, a run (or another jscheck) holds the directory, another
    program sends to the school, or the type does not exist."""


class BrowserMissing(RuntimeError):
    """Playwright or this machine's Chrome is not there: nothing was opened, every type stays todo."""


def chrome_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment Chrome starts with: this process's, but no secret (SECRET_ENV). Playwright hands Chrome the whole
    environment unless it is told otherwise, and Chromium reads GOOGLE_API_KEY for its own Google requests."""
    return {k: v for k, v in (os.environ if environ is None else environ).items() if k not in SECRET_ENV}


# ---------------------------------------------------------------- what one look saw
@dataclass(frozen=True)
class Ask:
    """One request a page's browser makes, as the route sees it. navigation: the main frame's own navigation (the
    page, a redirect, a script sending it on)."""

    url: str
    method: str = "GET"
    kind: str = "document"  # Playwright's resource type: document, script, xhr, fetch, image, stylesheet, …
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    navigation: bool = False


@dataclass
class Answer:
    """What our client made of an Ask: an answer (status, headers as received, the decoded body), or why none."""

    status: int | None = None
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    error: str | None = None  # sent, no usable answer: crawler.error_kind's category
    unsent: str | None = None  # never sent: stop, rest, breaker, post_body, method, lock, dns
    until: float | None = None  # unsent rest: when the server may be asked again


@dataclass
class Exchange:
    """One request in a page's log."""

    url: str
    kind: str
    method: str
    navigation: bool
    status: int | None = None
    text: str | None = None  # a data request's or a document's answer, decoded
    blocked: str | None = None  # never sent by rule: type, host, scheme, login, internal_service
    unsent: str | None = None
    error: str | None = None
    cached: bool = False


@dataclass
class Observation:
    url: str  # the page asked for
    final_url: str  # where the browser's address bar ended
    rendered: str  # the main frame's HTML once shown
    frames: list[str] = field(default_factory=list)  # the school's frames' HTML
    log: list[Exchange] = field(default_factory=list)
    slow: bool = False  # the page did not load within GOTO_TIMEOUT_MS
    not_idle: bool = False  # it never went quiet within IDLE_TIMEOUT_MS
    error: str | None = None
    seconds: float = 0.0
    screenshot: str | None = None  # where the browser's picture of the page was saved (reconcile's R6), if asked


@dataclass(frozen=True)
class PageVerdict:
    """One look's conclusion. verdict None: no conclusion, why says which (inconclusive:<why>)."""

    verdict: str | None
    why: str | None = None
    method: str = "direct"  # direct, browser, data_url (jstest.py's), redirect
    direct_chars: int = 0
    rendered_chars: int = 0
    frame_chars: int = 0
    new_chars: int = 0  # new text, each repeat once
    stop: str | None = None  # where the browser was sent on to
    login: bool = False  # … a login's
    requests: tuple[dict, ...] = ()  # the data requests: url, method, new_text
    clicks: tuple[dict, ...] = ()  # the clicks this round cannot press: key, text
    transient: bool = False  # a server resting or a stop: nothing to say, nothing written
    offline: bool = False  # the page's own request did not connect: is our network down?


def text_lines(html: str) -> tuple[int, list[str]]:
    """jstest.py's text_len: the body's lines (white space removed, script, style, noscript, header, nav, footer, aside
    and template taken out) of 2 characters or more, and their characters."""
    if not html:
        return 0, []
    tree = LexborHTMLParser(html)
    for sel in _TEXT_DROP:
        for n in tree.css(sel):
            n.decompose()
    body = tree.body
    if not body:
        return 0, []
    lines = [re.sub(r"\s+", "", t) for t in body.text(separator="\n").split("\n")]
    lines = [t for t in lines if len(t) >= 2]
    return sum(map(len, lines)), lines


def period_unit(line: str) -> str:
    """A line that is one piece written again and again (a marquee: ABCABCABC) as that piece; any other line as it is
    (the prefix function's period, linear in the line)."""
    n = len(line)
    if n < 2:
        return line
    pi = [0] * n
    for i in range(1, n):
        k = pi[i - 1]
        while k and line[i] != line[k]:
            k = pi[k - 1]
        if line[i] == line[k]:
            k += 1
        pi[i] = k
    p = n - pi[-1]
    return line[:p] if p < n and n % p == 0 else line


def new_text(direct_lines: Sequence[str], rendered_lines: Sequence[str]) -> tuple[list[str], int]:
    """The rendered lines the page's own answer does not have, and their characters counted once each: a line written
    twice, and a line made of one piece repeated, count as that piece (design §8.4: a marquee is not new text)."""
    have = set(direct_lines)
    new = [t for t in rendered_lines if t not in have]
    units = {period_unit(t) for t in new}
    return new, sum(map(len, units))


def windows(new: Sequence[str]) -> list[str]:
    """jstest.py's probes: 8-character pieces from inside the five longest new lines (brackets and punctuation the page
    adds are taken out), to look for in the data requests' answers."""
    out = []
    for pr in sorted(new, key=len, reverse=True)[:5]:
        core = _PUNCT.sub("", pr)
        if len(core) >= 8:
            for f in (0.25, 0.5, 0.75):
                i = max(0, min(len(core) - 8, int(len(core) * f) - 4))
                out.append(core[i:i + 8])
    return out


def holds_text(body: str | None, wins: Sequence[str]) -> bool:
    """A data request's answer holds one of the new text's pieces (its \\uXXXX escapes read, punctuation and quotes
    taken out, as jstest.py did)."""
    if not body or not wins:
        return False
    if "\\u" in body:
        body = _U_ESC.sub(lambda m: chr(int(m.group(1), 16)), body)
    flat = _PUNCT_Q.sub("", body)
    return any(w in flat for w in wins)


def login_page(html: str, url: str) -> bool:
    """The crawl's sign-in rule (crawler._page): a password box on a page with few links, or a login title or path."""
    if not html:
        return False
    tree = LexborHTMLParser(html)
    if tree.css_first("input[type=password]") is None:
        return False
    title = tree.css_first("title")
    n_links = len(tree.css("a[href], area[href]"))
    return (n_links < LOGIN_MAX_LINKS or bool(_LOGIN_TITLE.search(title.text(strip=True) if title else ""))
            or bool(scope.LOGIN_PATH.search(urlsplit(url).path)))


def _same(a: str, b: str) -> bool:
    return (scope.canonical(a.split("#", 1)[0]) or a) == (scope.canonical(b.split("#", 1)[0]) or b)


def _data_request(e: Exchange) -> bool:
    """§8.4: xhr, fetch and POST answers; never a document (the page itself, a frame, where a redirect lands)."""
    return e.kind in DATA_KINDS or (e.method == "POST" and e.kind != "document")


def judge(obs: Observation, clicks: Sequence[Mapping[str, Any]] = ()) -> PageVerdict:
    """One look, in the order of the module's description. clicks: the page's clicks this round cannot press
    (page_res how click, need round2: res_key and text)."""
    navs = [e for e in obs.log if e.navigation]
    if any(e.unsent == "stop" for e in obs.log):
        return PageVerdict(None, "inconclusive:stopped", transient=True)
    own = navs[0] if navs else None
    if own is None:
        return PageVerdict(None, "inconclusive:no_answer")
    if own.unsent == "rest":
        return PageVerdict(None, "inconclusive:server_rest", transient=True)
    if own.blocked or own.unsent or own.error:
        why = own.error or own.unsent or own.blocked
        return PageVerdict(None, "inconclusive:no_answer", offline=why in CONN_KINDS or why == "dns")
    last = navs[-1]
    if not _same(last.url, obs.url):  # sent on: by a 3xx or by script; where the browser stopped is the last address
        stop = scope.canonical(last.url) or last.url
        host = (urlsplit(stop).hostname or "").lower()
        login = (scope.judge(stop).reason == "login" or host == "accounts.google.com"
                 or (last.status == 200 and login_page(obs.rendered, stop)))
        return PageVerdict("ok", "js_redirect_login" if login else "js_redirect", method="redirect", stop=stop,
                           login=login)
    if own.status != 200:
        return PageVerdict(None, f"inconclusive:status_{own.status}")
    if obs.final_url.startswith(_NOWHERE[0]):
        return PageVerdict(None, "inconclusive:load_failed")
    d_len, d_lines = text_lines(own.text or "")
    r_len, r_lines = text_lines(obs.rendered)
    f_len = sum(text_lines(x)[0] for x in obs.frames)
    new, n_new = new_text(d_lines, r_lines)
    wins = windows(new)
    data = tuple({"url": e.url[:300], "method": e.method, "new_text": holds_text(e.text, wins)}
                 for e in obs.log if _data_request(e) and not e.blocked)
    clicked = tuple({"key": c["res_key"], "text": c.get("text")} for c in clicks)
    common = {"direct_chars": d_len, "rendered_chars": r_len, "frame_chars": f_len, "new_chars": n_new,
              "requests": data, "clicks": clicked}
    if n_new >= JS_CHARS and r_len >= JS_RATIO * max(d_len, 1):
        return PageVerdict("js", None, method="data_url" if any(d["new_text"] for d in data) else "browser", **common)
    if f_len >= FRAME_CHARS:
        return PageVerdict("frame", **common)
    if r_len == 0:
        return PageVerdict(None, "inconclusive:rendered0", **common)
    if d_len == 0:
        return PageVerdict(None, "inconclusive:direct0", **common)
    if obs.slow or obs.not_idle:
        return PageVerdict(None, "inconclusive:slow", **common)
    needed = [e for e in obs.log if not e.navigation and e.kind in NEEDED_KINDS]
    if any(e.unsent or e.error for e in needed):
        return PageVerdict(None, "inconclusive:request_failed", **common)
    if any(e.blocked in NEVER_SEND for e in needed):  # a login's or an internal service's: what it holds is unseen
        return PageVerdict(None, "inconclusive:not_sent", **common)
    if any(d["new_text"] for d in data):
        return PageVerdict(None, "inconclusive:data_text", **common)
    if clicked:
        return PageVerdict("click", "click", **common)
    return PageVerdict("ok", **common)


def incomplete(obs: Observation) -> str | None:
    """Why a look saw less than the page holds, or None (judge()'s own reasons for no conclusion, review 10-26: R6 read
    a page whose data never came as the page itself): too slow to load, never quiet, a request the page needed (a
    script, a frame, a data request: NEEDED_KINDS) sent with no answer or never sent (a server resting, a cap), or one
    never sent because it is a login's or an internal service's. The page's own navigation is the caller's."""
    if obs.slow:
        return "slow"
    if obs.not_idle:
        return "not_idle"
    for e in obs.log:
        if e.navigation or e.kind not in NEEDED_KINDS:
            continue
        if e.unsent or e.error:
            return f"{e.kind} {e.url[:200]}: {e.error or e.unsent}"
        if e.blocked in NEVER_SEND:
            return f"{e.kind} {e.url[:200]}: not_sent:{e.blocked}"
    return None


def type_verdict(looks: Sequence[PageVerdict]) -> PageVerdict | None:
    """The type's verdict from its samples' looks: the strictest conclusion (js > click > frame > ok); None when no look
    concluded."""
    done = [v for v in looks if v.verdict is not None]
    if not done:
        return None
    return min(done, key=lambda v: STRICT.index(v.verdict))


def note_token(kind: str, text: str) -> str:
    """A note token that keeps no comma (page_type.note is comma-separated, pagetypes.add_note)."""
    if kind == "stop":
        return "stop:" + text.replace(",", "%2C")
    return f"{kind}:" + text.replace(",", "，")


# ---------------------------------------------------------------- the browser
class Browser(Protocol):
    async def start(self) -> None: ...

    async def open(self, url: str, bridge: PageBridge, shot: Path | None = None) -> Observation: ...

    async def close(self) -> None: ...


class PlaywrightBrowser:
    """Playwright 1.64.1 driving this machine's Chrome (channel chrome: no browser is downloaded), headless, behind the
    dead proxy, with no UDP of its own (WebRTC) and no secret in its environment; each page in a context of its own
    (service workers and downloads off, no WebRTC interface), every request caught by context.route and answered
    through the bridge."""

    def __init__(self, *, settle_ms: int = SETTLE_MS, goto_ms: int = GOTO_TIMEOUT_MS, idle_ms: int = IDLE_TIMEOUT_MS):
        self.settle_ms, self.goto_ms, self.idle_ms = settle_ms, goto_ms, idle_ms
        self._pw = self._browser = None

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserMissing(f"没装 Playwright：uv sync --extra browser（Playwright {PLAYWRIGHT_VERSION}）") from exc
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(channel="chrome", headless=True, args=list(CHROME_ARGS),
                                                           env=chrome_env())
        except Exception as exc:  # whatever stops Chrome from starting: say it, open nothing
            await self._pw.stop()
            self._pw = None
            raise BrowserMissing(f"打不开本机的 Chrome（Playwright channel=chrome）：{str(exc)[:300]}") from exc

    async def open(self, url: str, bridge: PageBridge, shot: Path | None = None) -> Observation:
        """One look at url; shot: save a picture of the whole page there (reconcile's R6)."""
        ctx = await self._browser.new_context(user_agent=USER_AGENT, service_workers="block", accept_downloads=False)
        await ctx.add_init_script(NO_WEBRTC)
        page = await ctx.new_page()

        async def handler(route) -> None:
            req = route.request
            try:  # this page's own main frame: a popup's navigation is no redirect of the page
                main = req.is_navigation_request() and req.frame == page.main_frame
            except Exception:  # noqa: BLE001 - a request with no frame (gone, or a worker's)
                main = False
            try:
                headers = await req.all_headers()
            except Exception:  # noqa: BLE001
                headers = dict(req.headers)
            reply = await bridge.ask(Ask(req.url, req.method, req.resource_type, headers, req.post_data_buffer, main))
            with contextlib.suppress(Exception):  # the page went away meanwhile
                if reply.abort:
                    await route.abort(reply.abort)
                else:
                    await route.fulfill(status=reply.status, headers=reply.headers, body=reply.body)

        await ctx.route("**/*", handler)
        t0, slow, not_idle, error = time.monotonic(), False, False, None
        rendered, frames, final = "", [], url
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.goto_ms)
            except Exception as exc:  # noqa: BLE001 - a slow page is still measured with what has arrived
                slow, error = True, type(exc).__name__
            try:
                await page.wait_for_load_state("networkidle", timeout=self.idle_ms)
            except Exception:  # noqa: BLE001 - a page that never goes quiet is still measured
                not_idle = True
            await page.wait_for_timeout(self.settle_ms)
            if shot is not None:
                shot.parent.mkdir(parents=True, exist_ok=True)
                try:
                    await page.screenshot(path=str(shot), full_page=True)
                except Exception:  # noqa: BLE001 - no picture is no reason to lose the page's text
                    shot = None
            rendered = await page.content()
            for f in page.frames:
                if f != page.main_frame and scope.is_school((urlsplit(f.url).hostname or ""), urlsplit(f.url).path):
                    with contextlib.suppress(Exception):
                        frames.append(await f.content())
            final = page.url
        except Exception as exc:  # noqa: BLE001 - the look ends with what it has
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            with contextlib.suppress(Exception):
                await ctx.close()
        return bridge.observation(final_url=final, rendered=rendered, frames=frames, slow=slow, not_idle=not_idle,
                                  error=error, seconds=time.monotonic() - t0,
                                  screenshot=str(shot) if shot is not None and shot.exists() else None)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._browser is not None:
                await self._browser.close()
        with contextlib.suppress(Exception):
            if self._pw is not None:
                await self._pw.stop()


@dataclass(frozen=True)
class Reply:
    abort: str | None = None  # Playwright's error code: blockedbyclient (by rule), failed (no answer)
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class PageBridge:
    """One page's requests: blocked by rule, answered from the script cache, or sent by our client; each one in the
    page's log.

    A 3xx: Chrome follows a redirect it was handed by itself, past the route (Playwright 1.64.1: the next hop goes to
    the network, which is the dead proxy). So a page's or a frame's 3xx is handed back as a page that refreshes to the
    Location at once, and Chrome asks for it as a navigation of its own, which the route catches; any other request's
    hops are followed here, each one through our queue (303, and 301 or 302 after a POST, go on as a GET, as browsers
    do). Hops and requests per look are capped (a redirect loop, a page that polls) 【Claude 的执行办法】."""

    def __init__(self, check: JsCheck, url: str):
        self.check, self.url = check, url
        self.log: list[Exchange] = []
        self._sent = 0

    async def ask(self, a: Ask) -> Reply:
        return await self._ask(a, 0)

    async def _ask(self, a: Ask, hops: int) -> Reply:
        e = Exchange(a.url, a.kind, a.method.upper(), a.navigation)
        self.log.append(e)
        blocked = self._blocked(a)
        if blocked is not None:
            e.blocked = blocked
            return Reply(abort="blockedbyclient")
        cached = self.check.cache_get(a)
        if cached is not None:
            e.status, e.cached = cached.status, True
            return _reply(cached)
        if self._sent >= MAX_REQUESTS or (a.navigation and sum(x.navigation for x in self.log) > MAX_NAVS):
            e.unsent = "cap"
            return Reply(abort="failed")
        self._sent += 1
        try:
            got = await self.check.sender.send(a)
        except Exception as exc:  # one request that breaks our side fails that request only
            log.exception("jscheck could not send %s", a.url)
            got = Answer(error=f"crash:{type(exc).__name__}")
        e.status, e.error, e.unsent = got.status, got.error, got.unsent
        if got.status is None:
            return Reply(abort="failed")
        location = next((v for k, v in got.headers if k.lower() == "location"), None)
        if 300 <= got.status < 400 and location:
            target = urljoin(a.url, location.strip())
            if a.kind == "document":  # Chrome navigates there itself: a navigation the route catches
                return Reply(status=200, headers={"content-type": "text/html; charset=utf-8"},
                             body=_REFRESH.format(htmllib.escape(target, quote=True)).encode())
            if hops >= MAX_HOPS:
                return Reply(abort="failed")
            keep = got.status in (307, 308) or a.method.upper() not in ("POST",)
            headers = {k: v for k, v in a.headers.items() if keep or k.lower() != "content-type"}
            nxt = Ask(target, a.method if keep else "GET", a.kind, headers, a.body if keep else None, False)
            return await self._ask(nxt, hops + 1)
        if got.status < 400 and (a.kind in ("document", *DATA_KINDS) or a.method.upper() == "POST"):
            e.text = _decode(got.body)[:TEXT_KEEP]
        self.check.cache_put(a, got)
        return _reply(got)

    def _blocked(self, a: Ask) -> str | None:
        """Why this request is never sent, or None."""
        if not a.url.lower().startswith(("http://", "https://")):
            return "scheme"
        host = (urlsplit(a.url).hostname or "").lower()
        if a.kind in BLOCK_TYPES or urlsplit(a.url).path.lower().endswith("/favicon.ico"):
            return "type"
        if BLOCK_HOSTS.search(host):
            return "host"
        url = scope.canonical(a.url)
        if url is None:
            return "scheme"
        reason = scope.judge(url).reason
        return reason if reason in NEVER_SEND else None

    def observation(self, *, final_url: str, rendered: str, frames: Sequence[str] = (), slow: bool = False,
                    not_idle: bool = False, error: str | None = None, seconds: float = 0.0,
                    screenshot: str | None = None) -> Observation:
        return Observation(self.url, final_url or self.url, rendered or "", list(frames), self.log, slow, not_idle,
                           error, seconds, screenshot)


def _reply(got: Answer) -> Reply:
    """The answer for the browser: its status and headers as received (Set-Cookie lines one per line, as Playwright
    takes them), the body decoded (the encoding and length headers left out)."""
    heads: dict[str, list[str]] = {}
    for k, v in got.headers:
        lk = k.lower()
        if lk in _HOP:
            continue
        heads.setdefault(lk, []).append(v)
    return Reply(status=got.status or 0, body=got.body,
                 headers={k: ("\n" if k == "set-cookie" else ", ").join(v) for k, v in heads.items()})


def _decode(body: bytes) -> str:
    for enc in ("utf-8", "cp950"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            pass
    return body.decode("utf-8", "replace")


# ---------------------------------------------------------------- our client, one lane per server queue
class _Lane:
    """One server queue: its own client (one kept connection), WARC file and sending thread."""

    def __init__(self, name: str, client: Client, sink: WarcSink | None):
        self.name, self.client, self.sink = name, client, sink
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"jscheck-{name}")
        self.next_at = 0.0  # no request before this (the gap after the last answer)


class Sender:
    """Every request jscheck sends (design §8.4, §6.3, §6.10): through the crawl's client, one at a time per server
    queue, the gap after each answer, a host's Crawl-delay; each request on the wire a request row (purpose jscheck)."""

    def __init__(self, check: JsCheck):
        self.check = check
        self.fr: Frontier = check.fr
        self._lanes: dict[str, _Lane] = {}
        self._queues: dict[str, str] = {}
        self._lock = threading.Lock()

    def queue_of(self, host: str) -> str:
        """The host's server queue: the crawl's (host table), Google's named ones, or a lookup (never written)."""
        host = host.lower()
        with self._lock:
            if host in self._queues:
                return self._queues[host]
        row = self.fr.host(host)
        if row is not None and row["queue"] not in ("?", "", None):
            q = row["queue"]
        else:
            q = google_api.queue_for(host) or self.check.resolver(host)[0]
        with self._lock:
            self._queues[host] = q
        return q

    def lane(self, queue: str) -> _Lane:
        with self._lock:
            got = self._lanes.get(queue)
            if got is None:
                sink = WarcSink(self.check.root / "warc", f"{self.check.purpose}-" + re.sub(r"[^\w.-]", "_", queue),
                                self.check.run_id, datetime.now(UTC))
                got = self._lanes[queue] = _Lane(queue, self.check.client_factory(sink), sink)
            return got

    async def send(self, a: Ask) -> Answer:
        loop = asyncio.get_running_loop()
        host = (urlsplit(a.url).hostname or "").lower()
        queue = await loop.run_in_executor(None, self.queue_of, host)
        if queue == "dns-fail":
            return Answer(error="dns")
        lane = self.lane(queue)
        try:
            return await loop.run_in_executor(lane.pool, self._send, lane, host, a)
        except RuntimeError:  # the lane was closed: the check is ending
            return Answer(unsent="stop")

    def _send(self, lane: _Lane, host: str, a: Ask) -> Answer:
        c = self.check
        if c.stop.is_set():
            return Answer(unsent="stop")
        until = self.fr.paused_until(lane.name)
        if until > c.clock():
            return Answer(unsent="rest", until=until)
        target = _target(a)
        if isinstance(target, str):
            return Answer(unsent=target)
        if (wait := lane.next_at - c.clock()) > 0:
            c.sleep(wait)
        h = self.fr.host(host)
        if h is not None and (wait := (h["not_before"] or 0) - c.clock()) > 0:
            c.sleep(min(wait, MAX_CRAWL_DELAY_S))
        if c.stop.is_set():
            return Answer(unsent="stop")
        started, got, sent = c.clock(), None, True
        try:
            got = lane.client.fetch(target, NO_RATE, max_bytes=MAX_BYTES, pass_statuses=ANSWERS)
        except HostCircuitOpen:  # refused before sending: nothing reached the server
            sent = False
            return Answer(unsent="breaker")
        except schoollock.SchoolLockNotHeld as exc:  # refused before sending: a program's bug
            sent = False
            c.halt("error", f"no_school_lock: {exc}")
            return Answer(unsent="lock")
        except ArchiveWriteError as exc:  # answered, but no evidence could be kept: nothing more is sent
            got = exc
            c.halt("error", f"warc_write: {exc}")
        except FetchError as exc:
            got = exc
        except Exception as exc:  # noqa: BLE001 - one request's crash fails that request only
            got = exc
        finally:
            if sent:
                self._log(lane, host, a.url, started, got)
                self._gap(lane, host, c.clock() - started)
        if isinstance(got, Snapshot):
            self.fr.server_err_clear(lane.name)
            return Answer(status=got.status, body=got.body, headers=tuple(_raw_headers(got)))
        kind = error_kind(got) if isinstance(got, FetchError) else f"crash:{type(got).__name__}"
        self._failed(lane, host, a.url, got, kind)
        return Answer(error=kind)

    def _gap(self, lane: _Lane, host: str, took: float) -> None:
        """The run's gap after an answer, never less than what it took (at most SLOW_CAP_S); a longer Crawl-delay is the
        host's own wait (crawler._gap)."""
        c = self.check
        ended = c.clock()
        delay = max(c.rng.uniform(*c.gap), min(max(took, 0.0), SLOW_CAP_S))
        lane.next_at = ended + delay
        h = self.fr.host(host)
        crawl_delay = min(float(h["crawl_delay"]), MAX_CRAWL_DELAY_S) if h is not None and h["crawl_delay"] else 0.0
        if crawl_delay > delay:
            self.fr.set_host(host, not_before=ended + crawl_delay)

    def _log(self, lane: _Lane, host: str, url: str, started: float, got) -> None:
        """One request row per request on the wire (fetch.Sent; crawler._log), purpose jscheck (or the caller's)."""
        c = self.check
        wire = getattr(got, "sent", ()) or ()
        warc = None
        if lane.sink is not None:
            with contextlib.suppress(ValueError):
                warc = str(Path(lane.sink.path).relative_to(c.root))
        if not wire:
            status = got.status if isinstance(got, Snapshot) else getattr(got, "status", None)
            error = None if isinstance(got, Snapshot) else f"{type(got).__name__}: {got}"[:300]
            self.fr.request(lane.name, host, url, c.purpose, started, c.clock(), status, error, campus="unknown")
        for s in wire:
            self.fr.request(lane.name, host, url, c.purpose, s.started, s.ended, s.status, s.error, attempt=s.attempt,
                            method=s.method, bytes=s.bytes, new_conn=s.new_conn, connect_ms=s.connect_ms,
                            tls_ms=s.tls_ms, ip=s.ip, warc=warc if s.warc_offset is not None else None,
                            warc_offset=s.warc_offset, campus="unknown")

    def _failed(self, lane: _Lane, host: str, url: str, exc, kind: str) -> None:
        """A server that asks for a wait, says 503, errs three times in a row or does not connect rests as a whole
        (design §6.10, the frontier's pause table, which the crawl reads too)."""
        c, q = self.check, lane.name
        now = c.clock()
        if isinstance(exc, RetryLater):
            wait = min(max(exc.seconds, COOLDOWN[0]), COOLDOWN[1])
            self.fr.pause(q, now + wait, f"jscheck: Retry-After {exc.seconds:.0f} s from {host}")
            lane.client.reset_host(host)
            self.fr.event("warn", "retry_after", f"jscheck: {host} asked for {exc.seconds:.0f} s (Retry-After): "
                          f"server queue {q} rests {wait / 60:.0f} min", host, url)
            return
        if kind in CONN_KINDS:
            self.fr.pause(q, now + CONN_PAUSE_S, f"jscheck: {host} does not connect")
            self.fr.event("warn", "conn_pause", f"jscheck: server queue {q} does not connect ({kind}): it rests "
                          f"{CONN_PAUSE_S / 60:.0f} min", host, url)
            return
        if not _erred(kind):
            return
        n = self.fr.server_erred(q)
        if kind not in REST_NOW and n < BREAKER_THRESHOLD:
            return
        self.fr.pause(q, now + SERVER_REST_S, f"jscheck: {host}: {kind}")
        self.fr.server_err_clear(q)
        lane.client.reset_host(host)
        why = "said it is too busy (503)" if kind in REST_NOW else f"erred {n} times in a row (last: {kind})"
        self.fr.event("warn", "server_rest", f"jscheck: server queue {q} {why}: it rests {SERVER_REST_S / 60:.0f} min",
                      host, url)

    def last_answer_due(self) -> float:
        """When the last lane's gap is over: jscheck waits that out before it lets the locks go, as the crawl does."""
        return max((lane.next_at for lane in self._lanes.values()), default=0.0)

    def close(self) -> None:
        for lane in self._lanes.values():
            lane.pool.shutdown(wait=True, cancel_futures=True)
            with contextlib.suppress(Exception):
                lane.client.close()
            with contextlib.suppress(Exception):
                if lane.sink is not None:
                    lane.sink.close()


def _target(a: Ask) -> Target | str:
    """The request for our client: the browser's headers but those it does not pass on; a POST's form body as fields
    (the client sends forms only: any other body is not sent, post_body). A string says why it is not sent."""
    method = a.method.upper()
    headers = {k: v for k, v in a.headers.items()
               if not k.startswith(":") and k.lower() not in _NOT_PASSED and not k.lower().startswith("sec-ch-ua")}
    if method in ("GET", "HEAD", "OPTIONS"):
        return Target(a.url, method, headers=headers)
    if method != "POST":
        return "method"
    ctype = next((v for k, v in a.headers.items() if k.lower() == "content-type"), "")
    body = a.body or b""
    if body and ctype.split(";", 1)[0].strip().lower() != _FORM:
        return "post_body"
    try:
        text = body.decode("ascii")
        data = parse_qsl(text, keep_blank_values=True, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):  # a field that is not UTF-8 would not go out as it came
        return "post_body"
    if len(data) != len([p for p in text.split("&") if p]):
        return "post_body"
    headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    return Target(a.url, "POST", data=data, headers=headers)


def _raw_headers(snap: Snapshot) -> list[tuple[str, str]]:
    if snap.raw_headers:
        return [(k.decode("latin-1"), v.decode("latin-1")) for k, v in snap.raw_headers]
    return list(snap.headers.items())


# ---------------------------------------------------------------- what to open
@dataclass
class _Page:
    id: int
    url: str
    host: str
    page_type: str | None
    text_chars: int | None
    depth: int
    flags: str | None
    tier: int
    owner_site: str | None
    kind0: str | None
    kind: str
    found_via: str
    site: str

    @classmethod
    def of(cls, r: sqlite3.Row) -> _Page:
        return cls(r["id"], r["url"], r["host"], r["page_type"], r["text_chars"], r["depth"], r["flags"], r["tier"],
                   r["owner_site"], r["kind0"], r["kind"], r["found_via"], r["site"])

    def has(self, flag: str) -> bool:
        return flag in (self.flags or "").split(",")


@dataclass
class _TypeWork:
    key: str
    why: str | None
    note: str | None
    candidates: list[_Page]
    want: int


_PAGE_OK = "u.kind = 'page' AND u.state = 'done' AND u.status = 200 AND u.sniff = 'html'"
_HINT = f"(u.flags IS NOT NULL AND instr(',' || u.flags || ',', ',{pagetypes.HINT_FLAG},') > 0)"
_UNOPENED = "NOT EXISTS (SELECT 1 FROM js_page jp WHERE jp.url_id = u.id AND jp.at >= u.fetched_at)"
# a jump menu the page fires on load (its mark, outcome.AUTO_KEY; url_result reads the same mark)
_AUTO = (f"EXISTS (SELECT 1 FROM page_res r WHERE r.page_id = u.id AND r.res_key >= '{outcome.AUTO_KEY}' "
         f"AND r.res_key < '{outcome._after(outcome.AUTO_KEY)}')")
_CLICK_HIT = ("EXISTS (SELECT 1 FROM page_res r WHERE r.page_id = u.id AND r.how = 'click' "
              "AND (r.need = 'round2' OR r.why = 'click:shown_on_load'))")


@dataclass
class JsCheckReport:
    reason: str = "finished"
    detail: str | None = None
    tested: list[tuple[str, str, str | None, int]] = field(default_factory=list)  # key, verdict, why, looks
    pages: Counter = field(default_factory=Counter)  # js_page verdicts written
    opened: int = 0
    unresolved_pages: list[tuple[int, str, str | None]] = field(default_factory=list)
    not_seen: list[tuple[str, str, str | None, str]] = field(default_factory=list)  # key, verdict, sample, old state
    withdrawn: list[str] = field(default_factory=list)
    redirects: list[tuple[str, str, str]] = field(default_factory=list)  # page, where it stopped, what was written
    login_with_content: list[tuple[str, str]] = field(default_factory=list)
    left: list[tuple[str, str]] = field(default_factory=list)  # type or page, why it is left for the next jscheck
    todo_left: int = 0

    def lines(self) -> list[str]:
        by = Counter(v for _k, v, _w, _n in self.tested)
        out = [f"jscheck 结束：{self.reason}" + (f"（{self.detail}）" if self.detail else ""),
               (f"测了 {len(self.tested)} 类（{'、'.join(f'{v} {n}' for v, n in sorted(by.items())) or '无'}），打开 "
                f"{self.opened} 次；还剩待判定 {self.todo_left} 类"),
               f"每页的结论：{'、'.join(f'{v} {n}' for v, n in sorted(self.pages.items())) or '无'}"]
        out += [f"  判定不了（要人看，jscheck --set 改判）：{k}（{w}）" for k, v, w, _n in self.tested if v == "unresolved"]
        out += [f"  这一页判定不了（要人看）：{u}（{w}）" for _i, u, w in self.unresolved_pages]
        out += [f"  浏览器停在别的网址：{p} → {s}（{w}）" for p, s, w in self.redirects]
        out += [f"  有内容、却跳到登录页：{p} → {s}" for p, s in self.login_with_content]
        out += [f"  留给下一次 jscheck：{k}（{w}）" for k, w in self.left]
        out.append(f"旧类型这一轮没出现 {len(self.not_seen)} 类（不算待判定；附旧存档里样本的状态）")
        out += [f"  {k}（{v}）样本 {s or '-'}：{o}" for k, v, s, o in self.not_seen]
        if self.withdrawn:
            out.append(f"撤掉（几乎都是档案，这一轮没有回 200 的网页）：{'、'.join(self.withdrawn)}")
        return out


# ---------------------------------------------------------------- the check
class JsCheck:
    """`nknu archive jscheck`: run() takes the locks, opens what is left to judge and writes what it saw. halt() (a
    SIGTERM, or the STOP file) stops it after the pages in flight."""

    def __init__(self, root: Path, *, old: Path | None = None, gap: tuple[float, float] = GAP,
                 browser: Browser | None = None, client_factory: Callable[[WarcSink | None], Client] | None = None,
                 resolver: Callable[[str], tuple] = resolve, netcheck: Callable[[], bool] = netcheck,
                 plan: round1.Plan | None = None, clock: Callable[[], float] = wall,
                 sleep: Callable[[float], None] = time.sleep,
                 asleep: Callable[[float], Awaitable[None]] = asyncio.sleep, rng: random.Random | None = None,
                 pages_at_once: int = PAGES_AT_ONCE, school_lock: Path | None = None, purpose: str = PURPOSE):
        """old: the 10-24 archive (its directory or frontier.db), read only, for the types with no page this round.
        browser: PlaywrightBrowser() when not given. client_factory: crawler.archive_client's (a test's transport).
        plan: the site list (round1.load() when needed). school_lock: the machine-wide lock's path (schoollock's).
        purpose: request.purpose and the WARC files' name (reconcile's R6 shares the browser and queues: spotcheck)."""
        self.root = Path(root)
        self.old = old
        self.gap = check_gap(gap)
        self.browser = browser
        self.clock, self.sleep, self.asleep = clock, sleep, asleep
        self.client_factory = client_factory or (lambda warc: archive_client(warc, resend_gap_s=self.gap[0],
                                                                             clock=self.clock, sleep=self.sleep))
        self.resolver, self.netcheck = resolver, netcheck
        self._plan = plan
        self.rng = rng or random.Random()
        self.pages_at_once = pages_at_once
        self.school_lock = school_lock
        self.purpose = purpose
        self.stop = threading.Event()
        self.end_reason: str | None = None
        self.end_detail: str | None = None
        self.run_id = int(time.time())
        self.report = JsCheckReport()
        self.fr: Frontier | None = None
        self.sender: Sender | None = None
        self._tries: Counter = Counter()  # page id -> looks this run
        self._judged: dict[int, PageVerdict] = {}  # page id -> its last look this run
        self._cache: dict[str, Answer] = {}
        self._cache_bytes = 0
        self._queue_work: dict[str, list] = {}

    # ---- stopping
    def halt(self, reason: str = "stop", detail: str | None = None) -> None:
        """Stop after the pages in flight (their requests not yet sent are not sent); the first reason stays."""
        if self.end_reason is None:
            self.end_reason, self.end_detail = reason, detail
        self.stop.set()

    # ---- the run
    def run(self) -> JsCheckReport:
        db = self.root / "frontier.db"
        if not db.exists():
            raise JsCheckRefused(f"找不到待抓清单：{db}")
        if round_of(db) != "1":
            raise JsCheckRefused(f"{self.root} 不是第一轮的存档（frontier.db 没有第一轮的标记）：jscheck 不碰旧存档")
        try:
            run_lock = hold_run_lock(self.root)
        except AlreadyRunning as exc:
            raise JsCheckRefused(f"archive run（或另一个 jscheck）正在用这个目录（pid {exc.pid}）：先 nknu archive stop，"
                                 "等它停下再 jscheck") from None
        try:
            try:
                school = schoollock.hold(self.school_lock)
            except schoollock.SchoolLockBusy as exc:
                raise JsCheckRefused(f"另一个程序正在对学校发请求（pid {exc.pid}，全机锁 {exc.path}）：等它停下再 "
                                     "jscheck") from None
            except schoollock.SchoolLockUnshared as exc:
                raise JsCheckRefused(str(exc)) from None
            try:
                (self.root / "STOP").unlink(missing_ok=True)  # only the program that holds run.lock clears a STOP
                self.fr = Frontier(db)
                try:
                    return self._run()
                finally:
                    self.fr.close()
            finally:
                school.close()
        finally:
            run_lock.close()

    def _run(self) -> JsCheckReport:
        browser = self.browser or PlaywrightBrowser()
        self.browser = browser
        try:
            asyncio.run(self._main(browser))
        except BrowserMissing as exc:
            self.halt("error", f"no_browser: {exc}")
            self._end()
            raise
        except Exception as exc:
            self.halt("error", f"{type(exc).__name__}: {exc}"[:300])
            self._end()
            raise
        self._end()
        return self.report

    async def _main(self, browser: Browser) -> None:
        await browser.start()  # BrowserMissing before anything is written
        self.sender = Sender(self)
        watcher = asyncio.ensure_future(self._watch_stop())
        try:
            self.report.withdrawn = pagetypes.withdraw_files_only(self.fr)
            self._plan_work()
            sem = asyncio.Semaphore(max(1, self.pages_at_once))
            await asyncio.gather(*(self._run_queue(q, units, browser, sem)
                                   for q, units in sorted(self._queue_work.items())))
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            await browser.close()
            if (wait := self.sender.last_answer_due() - self.clock()) > 0:
                await asyncio.get_running_loop().run_in_executor(None, self.sleep, min(wait, SLOW_CAP_S + 10))
            self.sender.close()

    async def _watch_stop(self) -> None:
        stop_file = self.root / "STOP"
        while not self.stop.is_set():
            if stop_file.exists():
                log.info("STOP file found: finishing the pages in flight")
                self.halt("stop", "stop_file")
                return
            await asyncio.sleep(STOP_POLL_S)

    def _end(self) -> None:
        reason = self.end_reason or "finished"
        if reason not in END_REASONS:
            reason = "stop"
        r = self.report
        r.reason, r.detail = reason, self.end_detail
        r.pages = Counter(v.verdict or "unresolved" for v in self._judged.values())  # each page's last look
        with contextlib.suppress(sqlite3.Error):
            r.todo_left = self.fr.q("SELECT count(*) FROM page_type WHERE verdict='todo' AND n_pages > 0")[0][0]
        msg = {"reason": reason, "detail": self.end_detail, "sites": None, "tested": len(r.tested), "opened": r.opened,
               "todo_left": r.todo_left, "unresolved": sum(1 for t in r.tested if t[1] == "unresolved"),
               "unresolved_pages": len(r.unresolved_pages), "not_seen": len(r.not_seen), "left": len(r.left)}
        with contextlib.suppress(sqlite3.Error):
            self.fr.event("error" if reason == "error" else "info", "jscheck_end", json.dumps(msg, ensure_ascii=False))

    # ---- planning
    @property
    def plan(self) -> round1.Plan:
        if self._plan is None:
            self._plan = round1.load()
        return self._plan

    def _plan_work(self) -> None:
        fr = self.fr
        queued: set[int] = set()
        types = []
        for t in fr.q("SELECT key, why, note FROM page_type WHERE verdict='todo' AND n_pages > 0 ORDER BY key"):
            if "withdrawn:files_only" in (t["note"] or "").split(","):
                continue
            # a click's type: the pages with the click first (§8.4); then a page with a strong sign, whose own look
            # this one is too
            first = f"{_CLICK_HIT} DESC, " if t["why"] == "click" else ""
            rows = fr.q(f"SELECT u.* FROM url u WHERE u.page_type = ? AND {_PAGE_OK} "
                        f"ORDER BY {first}{_HINT} DESC, u.id", (t["key"],))
            cands = [_Page.of(r) for r in rows]
            want = min(CLICK_SAMPLES, len(cands)) if t["why"] == "click" else 1
            types.append(_TypeWork(t["key"], t["why"], t["note"], cands, max(want, 1)))
        for tw in types:
            q = self._queue_of_type(tw)
            self._queue_work.setdefault(q, []).append(("type", tw))
        pages = fr.q(f"SELECT u.* FROM url u WHERE {_PAGE_OK} AND {_UNOPENED} AND ({_HINT} OR {_AUTO}) ORDER BY u.id")
        for r in pages:
            if r["id"] in queued:
                continue
            queued.add(r["id"])
            self._queue_work.setdefault(self.sender.queue_of(r["host"]), []).append(("page", _Page.of(r)))
        self._not_seen()

    def _queue_of_type(self, tw: _TypeWork) -> str:
        """The server queue of the type's pages; a type with no page to open sends nothing (a queue of its own)."""
        return self.sender.queue_of(tw.candidates[0].host) if tw.candidates else "-"

    def _not_seen(self) -> None:
        """Design §8.4: the 10-25 test's types with no page this round, each with its sample's state in the old
        archive (read only, immutable); 工设系's withdrawn type aside."""
        rows = self.fr.q("SELECT key, verdict, sample_url, note FROM page_type WHERE n_pages = 0 AND source = 'import' "
                         "ORDER BY key")
        rows = [r for r in rows if "withdrawn:files_only" not in (r["note"] or "").split(",")]
        states = old_states(self.old, [r["sample_url"] for r in rows if r["sample_url"]])
        self.report.not_seen = [(r["key"], r["verdict"], r["sample_url"],
                                 states.get(r["sample_url"] or "", "没给旧存档" if self.old is None else "旧存档里没有"))
                                for r in rows]

    # ---- looking
    async def _run_queue(self, queue: str, units: list, browser: Browser, sem: asyncio.Semaphore) -> None:
        i = 0
        while i < len(units) and not self.stop.is_set():
            kind, unit = units[i]
            i += 1
            try:
                if kind == "type":
                    await self._type_unit(unit, browser, sem)
                else:
                    await self._page_unit(unit, browser, sem)
            except sqlite3.DatabaseError as exc:
                log.exception("the frontier database failed")
                self.halt("error", f"db_error: {exc}")
                return
            except Exception as exc:  # a bug: jscheck stops, what it wrote stays
                log.exception("jscheck failed on %s", getattr(unit, "key", None) or getattr(unit, "url", None))
                self.halt("error", f"{type(exc).__name__}: {exc}"[:300])
                return

    async def _wait_rest(self, page: _Page) -> bool:
        """Wait while the page's own server rests (at most REST_WAIT_MAX_S); False: it rests longer, or a stop came."""
        queue = self.sender.queue_of(page.host)
        while not self.stop.is_set():
            wait = self.fr.paused_until(queue) - self.clock()
            if wait <= 0:
                return True
            if wait > REST_WAIT_MAX_S:
                return False
            await self.asleep(min(wait, 30.0))
        return False

    async def _look(self, page: _Page, browser: Browser, sem: asyncio.Semaphore) -> PageVerdict | None:
        """Open one page once and write what this look says of it (js_page, a redirect's flags and its later row).
        None: nothing to say (a stop, the server resting, our network down)."""
        if not await self._wait_rest(page):
            if not self.stop.is_set():
                self.report.left.append((page.url, "server_rest"))
            return None
        async with sem:
            if self.stop.is_set():
                return None
            bridge = PageBridge(self, page.url)
            try:
                obs = await browser.open(page.url, bridge)
            except BrowserMissing:
                raise
            except Exception as exc:  # noqa: BLE001 - one page that breaks the browser is no conclusion of that page
                obs = bridge.observation(final_url=page.url, rendered="", error=f"{type(exc).__name__}: {exc}"[:200])
        clicks = self.fr.q("SELECT res_key, text FROM page_res WHERE page_id=? AND how='click' AND need='round2' "
                           "ORDER BY res_key", (page.id,))
        pv = judge(obs, [dict(c) for c in clicks])
        if pv.transient or self.stop.is_set():
            if pv.why == "inconclusive:server_rest" and not self.stop.is_set():
                self.report.left.append((page.url, "server_rest"))
            return None
        if pv.offline:
            up = await asyncio.get_running_loop().run_in_executor(None, self.netcheck)
            if not up:
                self.halt("error", "offline: this machine's network is down")
                return None
        self._tries[page.id] += 1
        self._judged[page.id] = pv
        self.report.opened += 1
        self._record_page(page, pv)
        return pv

    async def _type_unit(self, tw: _TypeWork, browser: Browser, sem: asyncio.Semaphore) -> None:
        """Design §8.4: tw.want sample slots; a slot whose look has no conclusion tries another page (the same one when
        there is no other); the type's verdict is its samples' strictest, unresolved when none concluded."""
        if not tw.candidates:
            self._write_type(tw, [], [], 0, "inconclusive:no_sample")
            return
        used: set[int] = set()
        looks: list[PageVerdict] = []
        pages: list[_Page] = []
        n = 0
        for _slot in range(tw.want):
            page = next((p for p in tw.candidates if p.id not in used), None)
            if page is None:
                break
            for _try in range(TRIES):
                used.add(page.id)
                pv = await self._look(page, browser, sem)
                if pv is None:
                    if not self.stop.is_set():
                        self.report.left.append((tw.key, "server_rest"))
                    return  # the type stays as it was
                n += 1
                looks.append(pv)
                pages.append(page)
                if pv.verdict is not None:
                    break
                page = next((p for p in tw.candidates if p.id not in used), page)
        last_why = next((v.why for v in reversed(looks) if v.verdict is None), "inconclusive:no_sample")
        self._write_type(tw, looks, pages, n, last_why)
        top = type_verdict(looks)
        if top is not None and any(v.stop for v in looks):
            self._add_family(tw, pages)

    def _add_family(self, tw: _TypeWork, opened: Sequence[_Page]) -> None:
        """Design §8.4: a type whose page sent the browser on: its other pages under 150 characters without the
        js_redirect flag are opened one by one too (on this queue, after what it has now)."""
        seen = {p.id for p in opened}
        q = self.sender.queue_of(tw.candidates[0].host)
        queued = {u.id for k, u in self._queue_work.get(q, []) if k == "page"}
        for p in tw.candidates:
            if p.id in seen or p.id in queued or p.has("js_redirect"):
                continue
            if p.text_chars is not None and p.text_chars >= SHORT_PAGE:
                continue
            self._queue_work.setdefault(q, []).append(("page", p))

    async def _page_unit(self, page: _Page, browser: Browser, sem: asyncio.Semaphore) -> None:
        """A page opened for itself (a strong sign, a jump menu fired on load, a redirect's type): up to TRIES looks
        this run, counting a look it had as a type's sample."""
        while self._tries[page.id] < TRIES and not self.stop.is_set():
            done = self._judged.get(page.id)
            if done is not None and done.verdict is not None:
                return
            if await self._look(page, browser, sem) is None:
                return

    # ---- writing
    def _record_page(self, page: _Page, pv: PageVerdict) -> None:
        """This look's js_page row (its own verdict; unresolved for no conclusion), and for a page sent elsewhere the
        flags and final_url (under 150 characters) and where it stopped as round two's row."""
        verdict = pv.verdict or "unresolved"
        outcome = None
        with self.fr.lock:
            conn = self.fr.conn
            conn.execute("SAVEPOINT jscheck_page")
            try:
                conn.execute("INSERT OR REPLACE INTO js_page(url_id, page_type, at, verdict, why, final_url) "
                             "VALUES (?,?,?,?,?,?)", (page.id, page.page_type, utc_now(), verdict, pv.why, pv.stop))
                if pv.stop:
                    outcome = self._sent_on(page, pv)
            except BaseException:
                conn.execute("ROLLBACK TO jscheck_page")
                conn.execute("RELEASE jscheck_page")
                raise
            conn.execute("RELEASE jscheck_page")
        # the report keeps each page's last look: one that found no answer is listed until a later look finds one
        self.report.unresolved_pages = [u for u in self.report.unresolved_pages if u[0] != page.id]
        if pv.verdict is None:
            self.report.unresolved_pages.append((page.id, page.url, pv.why))
        if outcome is not None:
            self.report.redirects.append((page.url, pv.stop, outcome))

    def _sent_on(self, page: _Page, pv: PageVerdict) -> str:
        """A page the browser left for pv.stop (design §8.4): under 150 characters, flags js_redirect (and login for a
        login's address) and final_url, as the crawl writes a script redirect (§8.3); fetched_at stays, so js_page's
        look stays after it. Where it stopped is only written down: a row state later (round2:jscheck_redirect) when
        there is none and the rules give it one, never pending. Returns what was written, for the report."""
        conn = self.fr.conn
        short = page.text_chars is not None and page.text_chars < SHORT_PAGE
        if short:
            flags = [f for f in (page.flags or "").split(",") if f]
            flags += ["js_redirect"] + (["login"] if pv.login else [])
            joined = ",".join(dict.fromkeys(flags))
            conn.execute("UPDATE url SET flags=?, final_url=? WHERE id=?", (joined, pv.stop, page.id))
            page.flags = joined
        elif pv.login:
            self.report.login_with_content.append((page.url, pv.stop))
        if pv.login:
            return "login：只记下来，不建行" + ("" if short else "（这一页有内容，照它自己判）")
        adm = round1.admit(self.plan, pv.stop, None, _row_of(page), "link")
        if adm is None:
            return "不是网址"
        placement = adm.placement
        if placement.state is None:
            return f"{placement.reason}：规则不建行，只记下来"
        if conn.execute("SELECT 1 FROM url WHERE url=?", (adm.url,)).fetchone() is not None:
            return "url 表里已经有这一行，照旧"
        kind0 = placement.kind0
        owner = placement.owner_site
        s = self.plan.site(owner) if owner else None
        conn.execute(
            "INSERT INTO url(url, path, host, site, unit, tier, kind, depth, found_via, found_from, state, reason, "
            "next_at, directory, owner_site, prio, leaf, kind0, added_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'later',?,0,0,?,?,?,?,?)",
            (adm.url, adm.url.split("?", 1)[0], (urlsplit(adm.url).hostname or "").lower(), scope.site_key(adm.url),
             (s.name or None) if s else None, placement.tier if placement.tier is not None else round1.ROUND2_TIER,
             _KIND_OF0.get(kind0, "page"), page.depth, "jscheck_redirect", page.id, REDIRECT_REASON, owner,
             _PRIO_OF0.get(kind0, 1), placement.leaf, kind0, self.clock()))
        return f"记成第二轮（later，{REDIRECT_REASON}）"

    def _write_type(self, tw: _TypeWork, looks: Sequence[PageVerdict], pages: Sequence[_Page], n: int,
                    why_none: str) -> None:
        top = type_verdict(looks)
        note = tw.note
        for v in looks:
            if v.stop:
                note = pagetypes.add_note(note, "js_redirect_login" if v.login else "js_redirect",
                                          note_token("stop", v.stop))
        reqs: dict[tuple[str, str], bool] = {}
        clicks: dict[str, Any] = {}
        for v in looks:
            for d in v.requests:
                k = (d["method"], d["url"])
                reqs[k] = reqs.get(k, False) or d["new_text"]
            for c in v.clicks:
                clicks.setdefault(c["key"], c.get("text"))
        data: dict[str, Any] = {}
        if reqs:
            data["requests"] = [{"url": u, "method": m, "new_text": t} for (m, u), t in reqs.items()]
        if clicks:
            data["clicks"] = [{"key": k, "text": t} for k, t in clicks.items()]
        if top is None:
            verdict, why, method, sample, dc, rc = "unresolved", why_none, None, None, None, None
            if pages:
                sample = pages[-1]
                dc, rc = looks[-1].direct_chars, looks[-1].rendered_chars
        else:
            verdict, why, method = top.verdict, (top.why if top.verdict == "click" else None), top.method
            i = next(j for j, v in enumerate(looks) if v is top)
            sample, dc, rc = pages[i], top.direct_chars, top.rendered_chars
        self.fr.x("UPDATE page_type SET verdict=?, why=?, method=?, direct_chars=?, rendered_chars=?, data_urls=?, "
                  "sample_id=coalesce(?, sample_id), sample_url=coalesce(?, sample_url), tries=?, source='jscheck', "
                  "tested_at=?, note=? WHERE key=?",
                  (verdict, why, method, dc, rc, json.dumps(data, ensure_ascii=False) if data else None,
                   sample.id if sample else None, sample.url if sample else None, n, utc_now(), note, tw.key))
        self.report.tested.append((tw.key, verdict, why, n))

    # ---- the script cache (jstest.py): a GET script answered 200 is asked once per run
    def cache_get(self, a: Ask) -> Answer | None:
        if a.method.upper() != "GET" or a.kind != "script":
            return None
        return self._cache.get(a.url)

    def cache_put(self, a: Ask, got: Answer) -> None:
        if a.method.upper() != "GET" or a.kind != "script" or got.status != 200:
            return
        size = len(got.body)
        if size > CACHE_ITEM_MAX or self._cache_bytes + size > CACHE_MAX_BYTES:
            return
        self._cache[a.url] = got
        self._cache_bytes += size


REDIRECT_REASON = "round2:jscheck_redirect"


def _row_of(page: _Page) -> dict[str, Any]:
    """The page as round1.admit's from_row (its id, owner_site, tier, depth)."""
    return {"id": page.id, "owner_site": page.owner_site, "tier": page.tier, "depth": page.depth, "url": page.url,
            "kind0": page.kind0, "kind": page.kind, "found_via": page.found_via, "site": page.site}


def old_states(old: Path | None, urls: Iterable[str]) -> dict[str, str]:
    """Each URL's state in the 10-24 archive (state, status, reason), read only and immutable (it is never written,
    not even its -wal and -shm files); the other scheme too, since the crawl may have moved a page between them."""
    out: dict[str, str] = {}
    if old is None:
        return out
    path = Path(old)
    if path.is_dir():
        path = path / "frontier.db"
    if not path.exists():
        return {u: f"找不到旧存档 {path}" for u in urls}
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        for u in urls:
            scheme, sep, rest = u.partition("://")
            other = f"{'http' if scheme == 'https' else 'https'}://{rest}" if sep else u
            row = conn.execute("SELECT state, status, reason, url FROM url WHERE url IN (?, ?) ORDER BY url = ? DESC "
                               "LIMIT 1", (u, other, u)).fetchone()
            if row is None:
                out[u] = "旧存档里没有"
            else:
                state, status, reason, at = row
                out[u] = " ".join(str(x) for x in (state, status, reason) if x not in (None, "")) + (
                    "" if at == u else f"（{at}）")
    except sqlite3.Error as exc:
        return {u: f"读不了旧存档：{exc}" for u in urls}
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------- reconcile's R6: one page, as a person would see it
@dataclass(frozen=True)
class SpotLook:
    """What the browser showed of one page (the fields of reconcile.SpotView): the rendered HTML, where the browser
    ended, the picture it saved (relative to the archive directory), or why it could not open the page; incomplete:
    why what it showed may be less than the page holds (incomplete()), so that it proves nothing."""

    url: str
    final_url: str | None = None
    html: str | None = None
    screenshot: str | None = None
    error: str | None = None
    incomplete: str | None = None


class SpotChecker:
    """Reconcile's R6 (design §18.2b): jscheck's browser, dead proxy, bridge and queues for one page at a time (request
    rows purpose spotcheck, WARC files spotcheck-*), called as checker(url) -> SpotLook. It writes nothing but those
    rows, the WARC and the pictures (<root>/spotcheck/<run>/…png): no url row, no verdict. The caller holds run.lock
    and the machine-wide lock (reconcile does, for its whole run: design §6.9) and closes it (a with block). Its
    browser lives on a loop of its own thread, so that a caller that is not asynchronous can call it."""

    def __init__(self, root: Path, fr: Frontier, *, browser: Browser | None = None, gap: tuple[float, float] = GAP,
                 client_factory: Callable[[WarcSink | None], Client] | None = None,
                 resolver: Callable[[str], tuple] = resolve, clock: Callable[[], float] = wall,
                 sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None, shots: bool = True):
        self.check = JsCheck(root, gap=gap, browser=browser, client_factory=client_factory, resolver=resolver,
                             clock=clock, sleep=sleep, rng=rng, purpose="spotcheck")
        self.check.fr = fr
        self.check.sender = Sender(self.check)
        self.browser = browser or PlaywrightBrowser()
        self.shots = shots
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._n = 0

    def __enter__(self) -> Self:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="spotcheck-browser", daemon=True)
        self._thread.start()
        try:
            asyncio.run_coroutine_threadsafe(self.browser.start(), self._loop).result()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __call__(self, url: str) -> SpotLook:
        if self._loop is None:
            raise RuntimeError("SpotChecker is used inside a with block")
        self._n += 1
        rel = Path("spotcheck") / str(self.check.run_id) / f"{self._n:05d}.png" if self.shots else None
        try:
            obs = asyncio.run_coroutine_threadsafe(self._open(url, rel), self._loop).result()
        except Exception as exc:  # noqa: BLE001 - the page could not be opened: say why
            return SpotLook(url, error=f"{type(exc).__name__}: {exc}"[:300])
        failed = next((e for e in obs.log if e.navigation and (e.error or e.unsent or e.blocked)), None)
        error = obs.error or (f"{failed.url}: {failed.error or failed.unsent or failed.blocked}" if failed else None)
        shot = None
        if obs.screenshot:
            with contextlib.suppress(ValueError):
                shot = str(Path(obs.screenshot).relative_to(self.check.root))
        return SpotLook(url, obs.final_url, obs.rendered or None, shot, error, None if error else incomplete(obs))

    async def _open(self, url: str, rel: Path | None) -> Observation:
        return await self.browser.open(url, PageBridge(self.check, url), None if rel is None else self.check.root / rel)

    def close(self) -> None:
        """The browser closes, the last answer's gap is waited out (as the crawl does before it lets the locks go), the
        queues' clients and WARC files close."""
        loop, self._loop = self._loop, None
        if loop is None:
            return
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(self.browser.close(), loop).result(timeout=60)
        if (wait := self.check.sender.last_answer_due() - self.check.clock()) > 0:
            self.check.sleep(min(wait, SLOW_CAP_S + 10))
        self.check.sender.close()
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=30)
        loop.close()


# ---------------------------------------------------------------- a person's verdict
MANUAL_VERDICTS = ("ok", "js", "frame", "click")


VERDICT_SET = "verdict_set"  # the event a person's verdict writes: monitor.Rounds counts it as a rejudge


def set_verdict(root: Path, key: str, verdict: str, note: str | None = None) -> list[str]:
    """`nknu archive jscheck --set <key> ok|js|frame|click --note …` (design §8.4): a person's verdict on a type
    (source manual, tested_at now, the note kept as manual:<note>), and on its pages' unresolved js_page rows, so that
    its pages follow it. Sends nothing. Refused while a run (or jscheck) holds the directory; holds run.lock itself
    while it writes (design §6.9). Returns the sites whose reconciliation it makes stale: an event verdict_set names
    them (the sites with pages of the type, else the type's own), since what a reconciliation judged rests on the
    verdict (thin is not judged on a js or frame page, review 10-26) 【Claude 的执行办法】."""
    if verdict not in MANUAL_VERDICTS:
        raise JsCheckRefused(f"--set 只收 {'、'.join(MANUAL_VERDICTS)}（收到 {verdict!r}）")
    root = Path(root)
    db = root / "frontier.db"
    if not db.exists():
        raise JsCheckRefused(f"找不到待抓清单：{db}")
    if round_of(db) != "1":
        raise JsCheckRefused(f"{root} 不是第一轮的存档：--set 不改旧存档")
    try:
        lock = hold_run_lock(root)
    except AlreadyRunning as exc:
        raise JsCheckRefused(f"archive run（或 jscheck）正在用这个目录（pid {exc.pid}）：先 nknu archive stop，"
                             "改完再 run") from None
    try:
        fr = Frontier(db)
        try:
            with fr.lock:
                conn = fr.conn
                row = conn.execute("SELECT note, site, verdict FROM page_type WHERE key=?", (key,)).fetchone()
                if row is None:
                    raise JsCheckRefused(f"没有这个网页类型：{key}")
                sites = sorted({r[0] for r in conn.execute("SELECT DISTINCT owner_site FROM url WHERE page_type=? "
                                                           "AND owner_site IS NOT NULL", (key,))})
                if not sites and row["site"] and row["site"] != "*":
                    sites = [row["site"]]
                new_note = pagetypes.add_note(row["note"], *([note_token("manual", note)] if note else ["manual"]))
                conn.execute("SAVEPOINT jscheck_set")
                try:
                    conn.execute("UPDATE page_type SET verdict=?, why=NULL, source='manual', tested_at=?, note=? "
                                 "WHERE key=?", (verdict, utc_now(), new_note, key))
                    conn.execute("UPDATE js_page SET verdict=?, why='manual' WHERE verdict='unresolved' AND url_id IN "
                                 "(SELECT id FROM url WHERE page_type=?)", (verdict, key))
                    conn.execute("INSERT INTO event(at, level, code, message) VALUES (?, 'info', ?, ?)",
                                 (utc_now(), VERDICT_SET, json.dumps({"sites": sites, "key": key, "verdict": verdict,
                                                                      "was": row["verdict"]}, ensure_ascii=False)))
                except BaseException:
                    conn.execute("ROLLBACK TO jscheck_set")
                    conn.execute("RELEASE jscheck_set")
                    raise
                conn.execute("RELEASE jscheck_set")
        finally:
            fr.close()
    finally:
        lock.close()
    return sites


def last_end(fr: Frontier) -> dict[str, Any] | None:
    """The last jscheck_end that counts (design §8.4, §10.5, §13 items 7 and 17): reason finished and written after
    the last run_end (the order the events were written, as monitor.Rounds.jscheck_done reads it: two events in one
    second are still one before the other); None when there is none. Its message as a dict, with its time (at)."""
    run = fr.q("SELECT max(rowid) FROM event WHERE code='run_end'")[0][0]
    for r in fr.q("SELECT rowid AS rid, at, message FROM event WHERE code='jscheck_end' ORDER BY rowid DESC"):
        if run is not None and r["rid"] < run:
            return None
        try:
            msg = json.loads(r["message"] or "{}")
        except ValueError:
            continue
        if msg.get("reason") == "finished":
            return {**msg, "at": r["at"]}
    return None


__all__ = ["CHROME_ARGS", "NO_WEBRTC", "Answer", "Ask", "Browser", "BrowserMissing", "Exchange", "JsCheck",
           "JsCheckRefused", "JsCheckReport", "Observation", "PageBridge", "PageVerdict", "PlaywrightBrowser",
           "SpotChecker", "SpotLook", "chrome_env", "incomplete", "judge", "last_end", "old_states", "set_verdict",
           "text_lines", "type_verdict"]

