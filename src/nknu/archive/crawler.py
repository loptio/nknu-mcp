"""The round-1 archive crawl (round-1 design §1–§11, review 10-26 B1–B8): one worker thread per server queue, each
sending one request at a time over one kept-alive connection and waiting after every response, whichever host on that
server it fetches next. The crawler writes evidence only (state, reason, status, flags, sniff, page_res, …); what a
row or a page came to is outcome.py's views.

- One run per archive directory and one program per machine: `archive run` holds run.lock (an flock the kernel drops
  when the process ends, even after kill -9) and the machine-wide lock on school requests (crawl/schoollock.py, design
  §6.9), which fetch.Client itself asks for before every request to a school host.
- Every address goes in through round1.admit() (review 10-26 A2, B7): a page's links and parts, a 3xx, a script
  redirect, a short link's answer, the seeds. Wrappers come off, a short link is one GET whose answer
  (round1.short_answer) is judged as if the page had linked its target, a Drive, Docs or Calendar address goes to
  the Google step (google_api.step; one it cannot read is unhandled:google, 要人看, never 规则不抓: B8, as every such
  address is with google_step=False), and the rest is scope.judge() then round1.verdict():
  this round's (pending, owner_site, tier, leaf, kind0, prio), round two's (later, round2:<why>), or no row (out:<rule>,
  the link table only). A round-1 page's attachments, images, frames, forms, short links and school scripts are its
  resources: page_res rows with their need (must, no with why, round2), so nothing it uses is dropped unseen.
- Caps hold rows back (held, cap:site/file/image/variant/depth: design §9); a pager link adds no depth. A platform's
  list page goes first (prio 0) and has no variant cap; its detail pages, SSO's activity detail pages and 成教所's
  所友名錄 pages (scope.PLATFORM_DETAIL_PATHS, 11-06, 11-09) have no page cap (§7.4). A run's start lets go of a row one of these caps held
  that the rules of today no longer hold (_release_caps); Google's own caps are google_api's and stay.
- Queue = the server that answers (getaddrinfo, the lowest IPv4); Google's web names share one named queue (google),
  its APIs another (googleapis: google_api.queue_for). A name that
  does not resolve is tried again after 10 min, doubling up to 6 h, then given up (`archive retry dns`); one that
  resolves to this machine is one of those (_lookup, 11-06), and nothing is ever sent on such a queue. The order
  in a queue (§6.7): tier, prio, the host the connection is on, depth, id.
- The gap after every response (§6.3, B6): a random wait inside the run's gap (default 2–5 s; 1–2 s once the pilot
  passed and the owner says so: `archive run --gap 1-2`; never below 1 s), and never less than what the server took to
  answer (at most 30 s); a longer Crawl-delay is that host's own wait (host.not_before).
- Every request that may have reached a server is logged, one request row per request on the wire (fetch.Sent: the
  try, a new or kept connection, TCP and TLS times, the server's address, bytes, the WARC record) with where it was
  made from (campus: on, off or unknown, B4: SSO's own check page, on SSO's queue, hourly, whenever this machine's
  address changes and when a network pause ends; a check that cannot tell keeps the last finding, and a run on the
  same address starts from it; crawling goes on wherever it is, the dashboard warns), and followed by the full gap.
- robots.txt is read per host before its first request (robots.py): for its Crawl-delay, its sitemaps and whether the
  host answers; it blocks nothing (owner 10-25). A 5xx or no answer never overwrites rules already known (B3). A 404
  probe per page host records what a missing page looks like (soft404).
- What came back is sniffed (sniff.py): a page address that answers a PDF is a file, an attachment that answers a web
  page is not one (not_file) and is never read for links. School error pages are known by where a 3xx sends the
  visitor (outcome.ERROR_SIGNATURES): gone, or an error retried like any GET.
- http and https (§6.6): a page whose scheme refuses a connection or a TLS handshake is tried once on the other. An
  answer there moves the row (moved_scheme); only a page or a redirect elsewhere (2xx, a 3xx that does not send the
  visitor back) teaches that the host answers on that scheme only, so that later addresses are written in it. An
  answer that sends the visitor back to the scheme that failed is no way in: the address is the URL's failure (an
  expired certificate: tls_cert), never a loop (second review, 10-26).
- Failures: a 4xx (but 408/429) is the server's answer, archived as it is. Timeouts, 5xx and TLS failures are the URL's:
  tried again after 30 min × attempts, three in all. An answer stored starts the count again (_store; not the first
  look at the school's not-found page, nor a 3xx to a school error page, which are tries), and so does a fetched row
  put back to be fetched again (postback.restart_chain, _gsimg_reread). The server rests as a whole (second review
  10-26, CLAUDE.md
  「出错或回『太忙』整台休息」, design §6.3): a 503 at once, and three errors in a row (5xx, 408, timeouts, whichever of
  its names) a fixed SERVER_REST_S; only the requests that went out cost their URLs an attempt. A Retry-After (or a
  429) rests the whole server queue, no attempt used. The client's breaker (three in a row on one name) cools that name
  a fixed 15 min. A connection that cannot be made (refused, timed out, TLS handshake timed out) is the server's (B2):
  its whole queue pauses a fixed 15 min and no URL ever loses an attempt for it; at CONN_GIVE_UP in a row a warning
  (server_down), and a school host that never answered us off campus waits for the lab machine (host_unreachable). A
  door of a server that answers on another (a closed port, or a scheme that only sends the visitor back) is the URL's
  failure instead: no server pause. Ten 401/403/451 in a row from one directory make that directory wait 15 min.
  The offline rehearsal's not_archived (fetch.NotArchived: its old archive holds no answer, design §18.1) fails the
  URL or the press at once, never retried, nothing held against its server or the network; a robots.txt it lacks
  reads as no new rules, an id probe it lacks is not asked again that run.
- Network down: a connection failure on a network that answered nothing for 2 min asks the connectivity check; a
  failed check pauses the networks that answer nothing, 5 min at a time; two school servers that were working
  failing with no school answer since (within 17 min) while the check passes pause the school's queues 15 min (B2:
  the school's network is down, not ours). A server that never answered, or one already failing on its own, is no
  sign of it (owner 11-06), and a school answer that came after a teller's failure lifts the pause (the servers that
  told it failed on their own, and keep their own rests). What those failures cost is undone (a school pause: the
  servers' counts). When a pause is over, SSO's check page is asked first; nothing says the school's network is
  back until one of its servers answers: until then a connection failure there costs no server anything (second
  review: the check asks www.google.com only).
- No request goes out on a queue that is paused, nor to a host before its Crawl-delay is over.
- A run looks up the hosts of the rows it may fetch that nobody looked up (`retry` brings back round two's and held
  rows; the old archive's hosts are seeded with queue '?'), and ends when nothing is left, or when nothing left comes
  due within end_wait_s (B1: retries, DNS back-off, cool-downs and server pauses are left for the next run instead of
  holding the locks for hours; C0: END_WAIT_S by default, `archive run --end-wait`, RUN_END_WAIT_S there); a row left
  active with no worker holding it is a crash (crash:left_active), and rows due that no queue can take end the run
  (stuck). run_end says why (finished, waiting, stuck, stop_file, sigterm, limit, warc_write, disk_low, db_error,
  no_school_lock), which sites the run covered and how many presses wait.
- Platform files (profiles.py, design §7.4) and their postbacks (postback.py, §7.5): a page of a profiled site is read
  with its content area; once stored, its presses as archived (a list's next page, a row's detail or download) are
  action rows of its own server queue, which a worker takes before its url rows, builds from that very page in the
  WARC, sends once (a POST is never sent again by itself: a failure starts the chain again from its GET once, then
  post_failed) and hands back (an answer's links count on the list page, a Location is its resource). A click whose
  writing a clicks rule reads is a GET row (oia's tabs: their JSON's content counts on the page) or a POST press (ee's
  file buttons: the address they answer is the page's attachment). A button to press that no press covers is
  unhandled:unpressed (要人看). Every GET-paged list's walk is written again when one of its pages has its answer.
- Google (google_api.py, §7.6): a Drive, Docs or Calendar address is the Google step's API rows on the googleapis queue
  (its key in its header only), each answer read for the rows it makes; their entries are written on every page that
  uses them, a short link's or an attachment's way there too. With no key the queue waits (no_api_key, no failure).
- Page types (pagetypes.py, §8.3): each 200 web page gets its type and the signs it reads, before it is finished.
- A platform's detail page is checked against its host's id probe once per detail type (§10.1: empty_detail,
  gone_id); a file the school's page says is missing is looked at once more before it is gone (§10.2).
- A page fetched again writes its page_res anew; what its embed boxes, forms, tabs, file buttons and pressed details
  brought is read back from the archive (_replay_parts), never lost and never asked for again.
- A site that finishes (reconcile.site_finished: nothing left this run but retries and the lab, B1) is reconciled
  offline by this very process, the one writer (design §18.2a step 3: reconcile.site_done, every check but R6, which
  stays skip until `archive reconcile` runs it once the run stopped), on a thread of its own with a connection of its
  own (Frontier.sibling: the main loop goes on starting workers and minding a stop, and no worker waits for its long
  reads; a stop ends it between pages, review 10-26), and again whenever the site got more work and finished again;
  before run_end at a natural end. Only when given reconcile_sources (`archive run` gives it); a failure is an error
  event, never the run's end.
- A run given only (`archive run --only PATH`, the owner 11-02: these rows and none of the other rows of their sites)
  fetches the listed url rows that are pending when it starts, and nothing else: every choice of due work (the
  frontier's queues, next row, waiting and next-due counts, the chains' queues, next press and waiting) keeps to
  them, a press runs only when its own row (its list page, action.list_id) is listed, and the run ends once none of
  them is left (a retry follows end_wait_s as always). A row not listed is never requested, even on the same host,
  and keeps its state, attempts and next_at: what the run's start puts right (rows left active, chains left
  pressing, names to look up again, hosts nobody looked up, the Google key's reasons) and what it judges of a host,
  a directory or a site (refusals in a row, robots.txt unreachable, a server that never connects, an outage undone,
  rows left active with no worker, a Google Sites picture refused: that picture alone waits for round two) touch the
  listed rows only, and a run given only never gives a name up (that fails every row of its host: a full run's to
  do). What a listed row brings (its links, its redirect's target, its Drive item's content) is enqueued as usual
  (a round-two row it links becomes this round's) for a later run; the address of a listed row moved to its other
  scheme (§6.6) is the listed row's from then on, the same resource, even when that address had a row of its own;
  a listed Google Sites page read again settles its own pictures' rows as any reading does (§23 B2: addresses it
  replaced, pictures left for round two). Hosts, queues and directories keep their own state as in any run. No site
  is reconciled (they are not complete); run_start and run_end say how many ids were listed, and name the sites
  whose records the run can change (_only_sites) where a full run writes null, every site.
- Nothing is parsed into facts here.
"""

from __future__ import annotations

import fcntl
import gzip
import html as htmllib
import ipaddress
import json
import logging
import math
import os
import random
import re
import secrets
import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

from nknu.archive import google_api, links, outcome, pagetypes, postback, profiles, round1, scope, sniff
from nknu.archive.frontier import (
    ALL_QUEUES,
    NETWORKS,
    OLD_VIAS,
    Frontier,
    ids_clause,
    net_key,
    network_of,
    wall,
)
from nknu.archive.links import extract
from nknu.archive.robots import (
    FRESH_S,
    MAX_BODY,
    MAX_CRAWL_DELAY_S,
    RETRY_UNREACHABLE_S,
    Robots,
    decode,
    judge_answer,
    unparsed,
)
from nknu.crawl import schoollock
from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.fetch import (
    BREAKER_THRESHOLD,
    KEEPALIVE_LIMITS,
    ArchiveWriteError,
    Client,
    DecodeError,
    FetchError,
    HostCircuitOpen,
    NotArchived,
    RetryableStatus,
    RetryLater,
    Snapshot,
    TooLarge,
    UnwantedType,
)
from nknu.crawl.warc import WarcSink

log = logging.getLogger("nknu.archive")

MAX_BYTES = 50 * 1024 * 1024
SITE_CAP = 4000  # pages per site (design §9); more are held (cap:site) and listed
FILE_CAP = 6000  # attachments per site, counted apart from the pages
IMAGE_CAP = 6000  # images per site: they take none of the 4,000 pages
VARIANT_CAP = 2000  # URLs per site + path (query variants), platform paths aside: a guard against endless calendars
MAX_DEPTH = 15
# the reasons _cap gives (design §9): what a run's start judges again (_release_caps). Google's caps (cap:drive,
# cap:drive_depth, cap:drive_list, cap:calendar) are google_api's own counts, which _cap knows nothing of: never here
CAP_REASONS = ("cap:depth", "cap:file", "cap:image", "cap:site", "cap:variant")
# periodic updates, stage 1 (design 2026-11-16 §四, the owner 11-16: 「设计时要交代上限怎么放宽或分开算，别让定期更新卡死」):
# a site whose first crawl is finished (meta UPDATE_SITES_KEY, a JSON list that update's go-live writes) is held by
# what one run adds to it, not by all it holds; the depth limit stays. A run cap's row waits for a person (`nknu archive
# retry cap:run`), never let go by a run's start (not in CAP_REASONS). The numbers are 【Claude】's, to be set again
# after the first two weeks of nights (one night of the whole school brings about 24 notices).
UPDATE_SITES_KEY = "update:sites"


@dataclass(frozen=True)
class RunCaps:
    pages: int = 300
    files: int = 500
    images: int = 500


RUN_CAPS = RunCaps()
GAP = (2.0, 5.0)  # the gap after each response (CLAUDE.md, design §6.3): the default
GAP_FAST = (1.0, 2.0)  # the owner 10-26: once the pilot passed (record 一·3); never the default
MIN_GAP_S = 1.0  # no setting goes below this
SLOW_CAP_S = 30.0  # a slow answer is waited out up to this long (Heritrix's maxDelayMs)
# the gap after a Drive download (alt=media, export) on the googleapis queue, never shorter than this (pilot 10-26,
# F9: 21 downloads one every 4–5 s and Google's "automated queries" page blocked the rest; metadata and listings
# keep the run's gap). Slower than the school's servers get, since Google's protection is what it is, never bypassed
# 【Claude 的执行办法】
DRIVE_MEDIA_GAP = (20.0, 40.0)
RETRY_AFTER_S = 1800  # a failed URL is tried again after 30 min × attempts (plus up to 5 min), three attempts in all
RETRY_JITTER_S = 300.0
MAX_ATTEMPTS = 3
COOLDOWN = (900.0, 86400.0)  # a Retry-After is waited at least 15 min, at most a day; a breaker's cool-down is the least
SERVER_REST_S = 900.0  # second review 10-26: a server that said 503, or erred three times in a row, rests whole, fixed
REST_NOW = frozenset({"http_503"})  # 「太忙」: the server rests at the first one (a 429 or a Retry-After: RetryLater)
CONN_PAUSE_S = 900.0  # B2: a server that does not connect: its whole queue rests this long, never doubled
CONN_GIVE_UP = 8  # B2: that many in a row (about two hours): its rows fail (server_down), the lab's school hosts aside
# … a server that answered this archive before waits longer, a day of failures in a row (it may be down for maintenance,
# yet must not keep a run from ending for ever; review 11-08)
CONN_GIVE_UP_ANSWERED = 96
DOOR_WINDOW_S = 120.0  # a door (scheme, port) of a server that answers on another within this long is closed, not down
# the flags of a kept answer that make it no good for keeping a page's resources (_good_before: export batch T1b)
KEEP_RES_BAD = frozenset({"login", "soft404", "gone_page", "gone_id", "gone_redirect", "error_page", "empty_detail",
                          "thin"})
END_WAIT_S = 600.0  # B1: a run whose soonest row is due later than this ends, leaving its rows for the next run
# C0 (design §11): `archive run --end-wait`'s own default, longer than the fixed 15-minute rest of a server (and the
# pause of one that does not connect), so that one such rest never ends a long run nobody watches 【Claude 的执行办法】
RUN_END_WAIT_S = 1800.0
DNS_TIME_EVERY_S = 3600.0  # §6.2: each host's name lookup is timed again this often (host.dns_ms)
IDLE_EXIT_S = 30
IDLE_POLL_S = 5.0  # an idle queue looks again this often, or when its next row is due if that is sooner
PAGE_4XX = frozenset(range(400, 500)) - {408, 429}  # answers, archived as they are: 408 and 429 are retried instead
ROBOTS_4XX = frozenset(range(400, 500)) - {429}  # RFC 9309: any 4xx for robots.txt means no rules (429 is retried)
DENY_STATUSES = frozenset({401, 403, 451})
DENY_STREAK = 10  # this many URLs of one directory refused in a row: that directory waits DENY_WAIT_S
DENY_WAIT_S = 900.0  # fixed: a protected folder never grows into a day-long wait
# a directory refused this many streaks in a row (DENY_STREAK each, a wait between) is parked: its other URLs fail
# as denied_dir for a person to look at (`nknu archive retry denied_dir` puts them back), instead of being asked
# ten by ten every 15 min for hours (pilot 10-26, F8: 106 pictures, 76 left to drain over about 2 h, every one 403)
# 【Claude 的执行办法】
DENY_GIVE_UP = 2
UNREACHABLE_TRIES = 3  # robots.txt unreachable (no connection kind of its own) this many hours in a row: give up
ROBOTS_HOPS = 5
REDIRECTS = (301, 302, 303, 307, 308)
DNS_RETRY_S = 600.0  # a name that did not resolve is tried again after 10 min, doubling, at most 6 h
DNS_RETRY_MAX_S = 6 * 3600.0
# lookups (about an hour: 10, 20 and 40 min apart; 8, about a day, until the owner's 11-08 「不用再傻等了」): then its URLs
# fail with reason dns, so the run can end
DNS_GIVE_UP = 4
NET_KINDS = frozenset({"dns", "connect"})  # failures that may be our network's, not the server's
CONN_KINDS = frozenset({"connect"})  # B2: refused, connect timeout, TLS handshake timeout (httpx.ConnectTimeout)
TLS_KINDS = frozenset({"tls", "tls_cert"})  # a TLS failure; tls_cert: the certificate failed its check (expired …)
SWITCH_KINDS = CONN_KINDS | TLS_KINDS  # §6.6: a scheme the host may not speak
TRANSPORT_KINDS = frozenset({"dns", "connect", "tls", "tls_cert", "timeout"})  # reached no server (and net_<type>)
# the offline rehearsal's answer for what its old archive does not hold (design §18.1, fetch.NotArchived): the URL's
# own failure at once (state failed, reason not_archived), never retried, never a server's or a network's
NOT_ARCHIVED = "not_archived"
# the frontier database held by another connection past its wait, or written under a read (SQLITE_BUSY_SNAPSHOT: the
# lab run of 11-07 05:45 stopped on it, the database whole): never the run's end (the owner 11-08); the row is fetched
# again this much later
DB_BUSY = "db_busy"
DB_BUSY_RETRY_S = 60.0
SERVER_DOWN = "server_down"  # a row of a server that did not connect for about two hours (_server_down)
# what an address only an old source names answers once its site moved (lis 11-07: 151 of the old archive's download
# addresses): it fails at once, its server's streak untouched (the owner 11-08: 「lis 旧死链不重试」)
OLD_GONE = "http_500"
# … unless its server gave this many 5xx in a row with no other answer between: then it may be erring as a whole, and
# that 500 is the server's as before (it rests; review 11-08: lis's dead downloads come mixed with answers)
SPARE_RUN = 20
# the id probe (design §10.1, once per platform host and detail type): one that got no answer is asked again with a
# later detail page; a timeout or a broken answer (net_*) may have reached the server, so at most PROBE_TRIES of those
# a run; a lookup, a connection or a handshake that failed sent nothing, and does not count (review 10-26 phase 3,
# 【Claude 的执行办法】)
PROBE_TRIES = 2
PROBE_UNSENT = frozenset({"dns", "connect", "tls", "tls_cert"})
NET_WINDOW_S = 120.0
NET_QUEUES = 3
# the school's network is down when this many school servers that were working fail with no school answer (review
# 11-06: the first batch's school work is on two servers, SSO's and c.nknu's; with three it could never be told)
SCHOOL_SIGNS = 2
# a school server is still taken to have been working with at most this many connection failures in a row held
# against it: an outage's first failures are held against the servers when another school server answered moments
# before (review 11-06)
SIGN_STREAK = 1
# a working school server's failure stays a sign this long (a server's rest and the window), until a school answer
# comes after it: two whose first failures after an outage are minutes apart tell it (second review 11-06: one was
# resting when the network dropped, and both counted to red); a school pause undoes the costs of that long too
SIGN_KEEP_S = CONN_PAUSE_S + NET_WINDOW_S
NET_PAUSE_S = 300.0
NET_UNDO_S = 300.0
NETCHECK_CACHE_S = 30.0
MIN_FREE_BYTES = 5 * 1024**3
STOP_POLL_S = 1.0  # how often `archive run` looks for STOP, and `archive stop` for the lock to be let go
# §18.2a step 3: how often (real seconds) the main loop looks for sites that finished, to reconcile them offline: a
# finished site's look reads all its rows, and a minute's delay changes nothing of an early warning 【Claude 的执行办法】
SITE_LOOK_S = 60.0
# the main loop's beat (meta run:alive_at, run seconds): a run that holds run.lock yet wrote none for minutes hangs, where
# one whose queues all rest still beats (dashboard design 2026-11-04 §2.5) 【Claude 的执行办法】
ALIVE_S = 60.0
STOP_WAIT_S = 600.0
NO_RATE = RateLimit(per_host_concurrency=1, min_interval_s=0.0, jitter_s=0.0)  # the worker waits, per queue
# B4: where SSO tells a visitor whether they are on the school's network (research/site_probe_2026-10-23: from off
# campus it says 「您的IP 不在校內網路」; on campus it sends the visitor on to the system named by cURL, never followed)
CAMPUS_URL = "https://sso.nknu.edu.tw/zthat/checkcampusip.aspx?curl=edocap.nknu.edu.tw"
CAMPUS_EVERY_S = 3600.0
CAMPUS_KEY = "campus:last"  # meta: what the last check that could tell found, and this machine's address then
_CAMPUS_OFF = re.compile(r"不在校[內内]網路|不在校内网络")
_HTML = ("text/html", "application/xhtml+xml")
_LOGIN_TITLE = re.compile(r"(?i)登入|登錄|log ?in|sign ?in")
# what a listed login address's page says when it is one (design §4.2: 請登入, 無權限, 請連線 VPN)
_LOGIN_WORDS = re.compile(r"(?i)請先?登入|请先?登录|無權限|无权限|請連線\s*VPN|请连线\s*VPN|請使用\s*VPN")
LOGIN_MAX_LINKS = 15  # a password form on a page with more links is a sidebar member box, not a login page
_FORM_CLOSED = re.compile(r"(?i)不再接受回應|不再接受回应|已停止接受回應|no longer accepting responses")
_GOOGLE_CNAME = re.compile(r"(?:^|\.)(?:googlehosted\.com|ghs\.google\.com|google\.com|googleusercontent\.com)$")
_DNS_TEXT = re.compile(r"(?i)nodename nor servname|name or service not known|temporary failure in name resolution|"
                       r"no address associated|name resolution|getaddrinfo")
_TLS_TEXT = re.compile(r"(?i)certificate|ssl|tls")
# a certificate that failed its check (OpenSSL's words): expired, self-signed, another name's, an unknown issuer
_CERT_TEXT = re.compile(r"(?i)certificate[ _]verify[ _]failed|certificate has expired|self[- ]signed certificate|"
                        r"hostname mismatch|unable to get local issuer")
_HTTP_TEXT = re.compile(r"HTTP (\d{3})\b")
_CHARSET = re.compile(r"charset=[\"']?([\w.-]+)", re.IGNORECASE)
_CTL = re.compile(r"(?i)ctl\d+")  # an ASP.NET row control's number: postback:<target, ctlNN as ctl*> (design §7.4)
_LOC = re.compile(r"(?is)<loc>\s*(.*?)\s*</loc>")
_SEED_VIAS = ("seed", "v1_known", "old_frontier", "commoncrawl")
# what a row is expected to be when found (kind0) -> its kind until its answer says otherwise (design §12), and its
# place in its queue (prio, §6.7): lists, seeds, sitemaps and login checks 0, pages 1, files and forms 2, images and
# school scripts 3
_KIND_OF0 = {"page": "page", "login_check": "page", "file": "file", "sitemap": "file", "click": "file",
             "script": "file", "shortlink": "shortlink", "image": "image", "form": "embed", "embed": "embed",
             "drive": "drive", "drive_list": "drive", "calendar": "calendar"}
_PRIO_OF0 = {"page": 1, "login_check": 0, "sitemap": 0, "click": 0, "file": 2, "form": 2, "embed": 2,
             "shortlink": 2, "drive": 2, "drive_list": 2, "calendar": 2, "image": 3, "script": 3}
PRIO_LIST = 0
# Google Sites' runtime pictures (design §23 B, 【用户同意 Claude 的建议】 10-27: 「可以，听你的」; how and the numbers
# 【Claude 的执行办法】): a page's pictures go first on their queue right after the page is stored (PRIO_NOW, below every
# other row's, frontier.next_for), the hop a picture's 302 leads to before the next picture (PRIO_HOP), and the page
# read again for new addresses (PRIO_NOW, its own prio back once it is taken). A page whose pictures start to answer
# 403 halfway is read again at most GSIMG_REREADS times; a site none of whose pictures came yet reads its page again
# once at its first 403, and when the first GSIMG_TRIES pictures tried after that both answer 403 it sends no more of
# them (round2:google_sites_image, meta GSIMG_GIVEUP_KEY<site>: across runs). meta GSIMG_SITE_KEY<site>: whether a
# picture came, whether the page was read again for none having come, the tries since; GSIMG_PAGE_KEY<page id>: how
# often the page was read again and its own prio
PRIO_NOW, PRIO_HOP = -1, -2
GSIMG_REREADS = 2
GSIMG_TRIES = 2
GSIMG_HOW = links.GSIMG.rstrip(":")  # page_res.how of such a picture
GSIMG_STALE = "gsimg_stale"  # url.reason of an address a new reading of the page replaced (outcome: redirect)
GSIMG_REREAD = "gsimg_reread"  # url.reason of a page waiting to be read again for new addresses
GSIMG_GIVEUP_KEY, GSIMG_SITE_KEY, GSIMG_PAGE_KEY = "gsimg_giveup:", "gsimg_site:", "gsimg_reread:"
# a page read again for its pictures' new addresses keeps its stored answer when that reading brings no web page (a 403,
# a 5xx, a timeout, no connection …: design §23.9 B2, 【Claude 的执行办法】): these columns of the row as it stood when
# the reading was planned (meta GSIMG_PAGE_KEY<page id> "keep"), put back as they were, no attempt spent
GSIMG_KEEP_COLS = ("state", "reason", "next_at", "attempts", "status", "final_url", "ctype", "bytes", "sha256", "warc",
                   "title", "flags", "fetched_at", "elapsed_ms", "n_links", "n_postbacks", "kind", "page_role",
                   "page_type", "sniff", "text_chars", "filename", "size_declared", "warc_offset", "warc_len")
# a resource row's redirect is the same resource (Heritrix's TransclusionDecideRule: an embedded resource's redirect
# may leave the scope); a page's is a link of that page
# the missing-page probe's path: PROBE_PREFIX, 12 hex digits, .html (the 10-24 crawler's too: the offline rehearsal
# answers a probe with the old probe of the same host, rehearsal.OldArchive)
PROBE_PREFIX = "/nknu-archive-probe-"
_TRANSCLUDE = frozenset({"file", "image", "form", "script", "click", "embed", "sitemap", "drive", "drive_list",
                         "calendar"})
_ROLE_OF0 = {"file": "file", "image": "img", "form": "form", "shortlink": "shortlink", "script": "script",
             "embed": "iframe", "page": "file", "drive": "drive", "calendar": "calendar"}
_HARD_SKIP = scope.HARD_OUT | {"trap", "too_long"}  # a pending row the rules of today leave out
# what a page uses when it links it, on its own site too (design §4.4: a link to an attachment, a form, a short link)
_USED_KINDS = frozenset({"file", "form", "shortlink", "image"})
# a button the platform file says to press that no press of its page covers (postback.PageRead.unplanned: outside
# the grid the walk reads, a row without its title …): nobody presses it, so the page waits for a person 【Claude 的执行办法】
UNPRESSED = postback.UNPRESSED


class _Stopped(Exception):
    """The run is stopping: no new request goes out; the URL goes back to pending."""


class _Offline(Exception):
    """The network is down: the URL goes back to pending, and nothing counts against it."""


class _Paused(Exception):
    """The queue rests (a pause began while its row was being handled): nothing more is sent; the URL goes back to
    pending, and nothing counts against it."""


class _DiskLow(Exception):
    """Less than MIN_FREE_BYTES free under the archive directory: the run stops before the WARC files cannot be written."""


class AlreadyRunning(Exception):
    def __init__(self, pid: str):
        super().__init__(f"another archive run holds the lock (pid {pid})")
        self.pid = pid


def hold_run_lock(root: Path):
    """The run's exclusive lock on <root>/run.lock, held until the returned file is closed or the process ends (the
    kernel lets go even after kill -9). Raises AlreadyRunning with the holder's pid."""
    root.mkdir(parents=True, exist_ok=True)
    fh = open(root / "run.lock", "a+")  # noqa: SIM115 - held for the run's whole life; the caller closes it
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        pid = fh.read().strip() or "?"
        fh.close()
        raise AlreadyRunning(pid) from None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def lock_holder(root: Path) -> str | None:
    """The pid written in run.lock while a run holds it; None when no run does."""
    path = root / "run.lock"
    if not path.exists():
        return None
    with open(path) as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return fh.read().strip() or "?"
        fcntl.flock(fh, fcntl.LOCK_UN)
        return None


# design §7.2: images are page resources now (SSO's 465 image attachments too); only video and audio are never read
SKIP_TYPES = ("video/", "audio/")


def check_gap(gap: tuple[float, float]) -> tuple[float, float]:
    lo, hi = float(gap[0]), float(gap[1])
    if lo < MIN_GAP_S or hi < lo:
        raise ValueError(f"the gap is from at least {MIN_GAP_S:g} s up to no less than that (got {lo:g}–{hi:g} s)")
    return lo, hi


def archive_client(warc: WarcSink | None, *, resend_gap_s: float = GAP[0], **kw) -> Client:
    """The archive crawl's client (design §6.2, §6.4; B7): one kept-alive connection per server, one request per call
    (the frontier retries), a 4xx (but 408/429) handed back as an answer, redirects handed back, a body that cannot be
    decoded strictly handed back without text, and a resend after an idle close waiting at least the gap's least.
    kw: clock and sleep (the crawl passes its own, so request times are on its clock), a transport for tests."""
    return Client(warc=warc, follow_redirects=False, pass_statuses=PAGE_4XX, strict_decode=False,
                  limits=KEEPALIVE_LIMITS, skip_types=SKIP_TYPES, attempts=1, resend_gap_s=resend_gap_s, **kw)


def resolve(host: str) -> tuple[str, str | None, str | None]:
    """(queue, ip, canonical name) for a host; queue 'dns-fail' when it does not resolve (never raises)."""
    if host == scope.GOOGLE_SITES:
        return "google", None, host
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, flags=socket.AI_CANONNAME)
        cname = next((i[3] for i in infos if i[3]), host).lower().rstrip(".")
        v4 = sorted({ipaddress.ip_address(i[4][0]) for i in infos if i[0] == socket.AF_INET})
        v6 = sorted({ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos if i[0] == socket.AF_INET6})
    except (OSError, UnicodeError, ValueError):
        return "dns-fail", None, None
    ips = v4 or v6
    ip = str(ips[0]) if ips else None
    if _GOOGLE_CNAME.search(cname) and cname != host:
        return "google", ip, cname
    return (f"ip:{ip}" if ip else "dns-fail"), ip, cname


def netcheck() -> bool:
    """Is there a network at all: a route out (connecting a UDP socket sends nothing) and a public name that
    resolves. Never touches a school server."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: fails at once when there is no route
        socket.getaddrinfo("www.google.com", 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def local_address() -> str | None:
    """This machine's address on its way out (B4: when it changes, the campus check is asked again): a UDP socket's
    connect sends no packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def error_kind(exc: Exception) -> str:
    """A failure's category for url.reason and the monitor (the full message stays in request.error): http_<status>,
    timeout, connect, dns, tls, retry_after, too_large, decode, host_cooldown, net_<type> or fetch."""
    if isinstance(exc, NotArchived):
        return NOT_ARCHIVED
    if isinstance(exc, RetryLater):
        return "retry_after"
    if isinstance(exc, UnwantedType):
        return "media_type"
    if isinstance(exc, TooLarge):
        return "too_large"
    if isinstance(exc, DecodeError):
        return "decode"
    if isinstance(exc, HostCircuitOpen):
        return "host_cooldown"
    cause = exc.__cause__
    if isinstance(cause, RetryableStatus):
        return f"http_{cause.status}"
    if isinstance(cause, httpx.ConnectTimeout):
        return "connect"
    if isinstance(cause, httpx.TimeoutException):
        return "timeout"
    if isinstance(cause, httpx.ConnectError):
        text = str(cause)
        if _DNS_TEXT.search(text):
            return "dns"
        if _CERT_TEXT.search(text):
            return "tls_cert"
        return "tls" if _TLS_TEXT.search(text) else "connect"
    if isinstance(cause, httpx.TransportError):
        return "net_" + type(cause).__name__
    m = _HTTP_TEXT.search(str(exc))
    return f"http_{m[1]}" if m else "fetch"


def _erred(kind: str | None) -> bool:
    """A failure that is the server erring (second review 10-26: 「出错」): a 5xx, a 408, a timeout, a connection it
    broke off; never a refusal to connect (B2's), a TLS failure, a 4xx answer or one we would not take."""
    return kind is not None and (kind.startswith(("http_5", "net_")) or kind in ("http_408", "timeout"))


def db_busy(exc: BaseException) -> bool:
    """A database error that passes (DB_BUSY): the file held by another connection (SQLITE_BUSY and its extended codes,
    SQLITE_BUSY_SNAPSHOT among them; SQLITE_LOCKED), never a broken, full or read-only one. Python 3.11+ gives the code;
    an error without one counts by its words."""
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc)


def _door(url: str) -> tuple[str, int]:
    """The door of a server an address knocks on: its scheme and port."""
    parts = urlsplit(url)
    return parts.scheme, parts.port or (443 if parts.scheme == "https" else 80)


def deny_dir(url: str) -> str:
    """Where a streak of refusals is counted: the host and the first two directories of the path (the file name not
    counted), e.g. c.nknu.edu.tw/aca/uploadfile for every attachment of 教務處's upload folder."""
    parts = urlsplit(url)
    return f"{(parts.hostname or '').lower()}/" + "/".join(parts.path.split("/")[1:-1][:2])


def _dns_wait(fails: int) -> float:
    return min(DNS_RETRY_S * 2 ** (max(fails, 1) - 1), DNS_RETRY_MAX_S)


def _lenient(body: bytes, ctype: str) -> str:
    """Text for link extraction from a body that strict decoding refused: the declared charset, bad bytes replaced."""
    m = _CHARSET.search(ctype or "")
    cs = m.group(1).lower() if m else "utf-8"
    cs = {"big5": "cp950", "big-5": "cp950", "x-big5": "cp950"}.get(cs, cs)
    try:
        return body.decode(cs, errors="replace").removeprefix("﻿")
    except (LookupError, UnicodeError):  # UnicodeError: charset=undefined, or idna (which cannot replace)
        return body.decode("utf-8", errors="replace").removeprefix("﻿")


def _sends_back(url: str, other: str, location: str | None) -> bool:
    """Whether a 3xx from the other scheme's address (other) sends the visitor back to url's scheme on url's host."""
    target = scope.canonical(location or "", other)
    if target is None:
        return False
    a, b = urlsplit(url), urlsplit(target)
    return b.scheme == a.scheme and (b.hostname or "") == (a.hostname or "")


def _other_scheme(url: str) -> str | None:
    """The same address in the other scheme, when it names no port of its own (design §6.6)."""
    parts = urlsplit(url)
    if parts.port is not None or parts.scheme not in ("http", "https"):
        return None
    return urlunsplit(parts._replace(scheme="https" if parts.scheme == "http" else "http"))


@dataclass(frozen=True)
class Enqueued:
    """What admitting one address came to: the link table's verdict (queued, dup, round2:<why>, cap:<x>, failed:dns,
    scheme_switch, out:<rule>, unhandled:<why>), the url row's address and id (none for out and unhandled), and the
    placement round1 gave it."""

    verdict: str
    url: str | None
    id: int | None
    placement: round1.Placement | None


class _NetWatch:
    """Tells our network going down from a server's own failure. Answers and connection errors are noted per network
    ("school" = 140.127.*, "other") at the time they came back (Crawler._send: before the gap after them). A connection
    error on a network that answered within NET_WINDOW_S before it is that server's problem. Otherwise the connectivity
    check is asked (a fresh one once NET_QUEUES of that network's queues failed in that window, the cached one before):
    when it fails, the networks that answered nothing within NET_WINDOW_S pause (our network is down). When it passes
    and SCHOOL_SIGNS school servers that were working failed with no school answer since (within SIGN_KEEP_S: one may
    have been resting when the network dropped), the school's network is down (B2: the check alone, www.google.com,
    cannot tell). A server that was working: it answered at some time (a request row with a status, this run's or an
    earlier one's: a server down from the start says nothing of its network), at most SIGN_STREAK of its connection
    failures in a row are held against it (which happens only while its network is taken to work), and no school
    answer refuted a pause it told (owner 11-06: the first batch's last school rows sat on servers down for days, and
    three of them failing together paused the whole school's network 105 times in 33 h, the live servers with it). An
    HTTP answer of a school server that came after a teller's failure says that one failed on its own (no sign until it
    answers); with fewer than SCHOOL_SIGNS tellers left the pause is lifted (Crawler._school_answered). What connection
    errors cost (attempts, robots_fail, cool-downs, server pauses) is noted per network, so a pause can undo the costs
    on the networks it holds.

    After a pause the other network is assumed to work until it shows otherwise. The school's is not (second review
    10-26: the check asks www.google.com, which says nothing of the school's): it is unsure until one of its servers
    answers, and while it is a connection failure there is held against no server (Crawler._conn_failed)."""

    def __init__(self, clock: Callable[[], float], check: Callable[[], bool],
                 streak: Callable[[str], int] = lambda queue: 0,
                 answered_ever: Callable[[str], bool] = lambda queue: True):
        self.clock, self._check, self._streak, self._ever = clock, check, streak, answered_ever
        self.lock = threading.Lock()
        self.last_answer: dict[str, float] = {}
        self.errors: list[tuple[float, str, str, bool]] = []  # (when, network, queue, whether it was working)
        self.charges: list[tuple[float, str, tuple]] = []
        self.unsure: set[str] = set()  # networks whose pause is over and none of whose servers answered since
        self.told: dict[str, float] = {}  # the school servers whose failures told the school's pause that holds: when
        self.refuted: set[str] = set()  # school servers whose pause a school answer refuted: no sign until they answer
        self.known: dict[str, bool] = {}  # school queues: whether they ever answered (looked up once, then each answer)
        self._checked_at, self._ok = -math.inf, True

    def answered(self, queue: str, http: bool = True, at: float | None = None) -> bool:
        """Something on the queue's network answered, at `at` (now when not given): an HTTP answer (any status), or not
        (http False: a timeout, a broken answer: the connection was made). Returns whether an HTTP answer of a school
        server lifts the school's pause that holds: the tellers that failed before it came failed on their own (no
        sign until each answers), and fewer than SCHOOL_SIGNS are left (second review 11-06: an answer that came
        before the network dropped, noted after its gap, refuted a real outage)."""
        with self.lock:
            net = network_of(queue)
            at = self.clock() if at is None else at
            self.last_answer[net] = max(self.last_answer.get(net, -math.inf), at)
            self.unsure.discard(net)
            if not http:
                return False
            self.known[queue] = True
            self.refuted.discard(queue)
            if net != "school" or not self.told:
                return False
            own = {q for q, t in self.told.items() if t <= at}  # at the same moment the answer wins (down_after too)
            self.refuted |= own - {queue}
            for q in own:
                del self.told[q]
            if len(self.told) >= SCHOOL_SIGNS:
                return False
            self.told = {}
            return True

    def grace(self, network: str | None = None) -> None:
        """After a pause (of every network, or of one): the other network is assumed to work until it shows otherwise;
        the school's is unsure until one of its servers answers. A school pause that ran its course was not refuted:
        its tellers stay signs."""
        nets = NETWORKS if network is None else (network,)
        with self.lock:
            now = self.clock()
            for n in nets:
                if n == "school":
                    self.unsure.add(n)
                    self.told = {}
                else:
                    self.last_answer[n] = now
            self.errors = [e for e in self.errors if e[1] not in nets]

    def on_probation(self, queue: str) -> bool:
        """Whether the queue's network is unsure (its pause is over, none of its servers answered since)."""
        with self.lock:
            return network_of(queue) in self.unsure

    def still_told(self) -> bool:
        """Whether the school's pause just told stands: no school answer lifted it since down_after told it."""
        with self.lock:
            return bool(self.told)

    def check(self, fresh: bool = False) -> bool:
        now = self.clock()
        if fresh or now - self._checked_at > NETCHECK_CACHE_S:
            self._ok, self._checked_at = bool(self._check()), now
        return self._ok

    def _known(self, queue: str) -> bool:
        """Whether this school server ever answered: looked up once, then kept by answered() (only the queue's own
        worker asks and answers for it)."""
        if queue not in self.known:
            self.known[queue] = bool(self._ever(queue))
        return self.known[queue]

    def down_after(self, queue: str, at: float | None = None) -> tuple[frozenset[str], bool]:
        """Called after a connection error from queue (that came back at `at`, now when not given), before it is held
        against the server: (the networks to pause, whether it is ours: the check failed). Empty: the server's own
        problem. Whether the server was working is taken now, so that the failure held against the first server of an
        outage still counts when a later one tells it. The school's network is told down on a fresh check, and not
        when one of its servers answered while the check was asked."""
        now, net = self.clock(), network_of(queue)
        at = now if at is None else at
        working = net == "school" and self._streak(queue) <= SIGN_STREAK and self._known(queue)
        with self.lock:
            working = working and queue not in self.refuted
            self.errors = [e for e in self.errors if now - e[0] <= (SIGN_KEEP_S if e[3] else NET_WINDOW_S)]
            self.errors.append((at, net, queue, working))
            seen = self.last_answer.get(net, -math.inf)
            if at - seen <= NET_WINDOW_S:
                return frozenset(), False
            many = len({q for t, n, q, _w in self.errors if n == net and now - t <= NET_WINDOW_S}) >= NET_QUEUES
            signs = {q: t for t, n, q, w in self.errors if n == net and w and t > seen}
        tell = len(signs) >= SCHOOL_SIGNS
        if self.check(fresh=many or tell):
            with self.lock:
                if not tell or self.last_answer.get(net, -math.inf) != seen:
                    return frozenset(), False
                self.told.update(signs)
            return frozenset({"school"}), False
        with self.lock:
            return frozenset(n for n in NETWORKS if now - self.last_answer.get(n, -math.inf) > NET_WINDOW_S), True

    def charge(self, queue: str, *undo) -> None:
        with self.lock:
            now = self.clock()
            self.charges = [c for c in self.charges if now - c[0] <= SIGN_KEEP_S] + [(now, network_of(queue), undo)]

    def take_charges(self, networks: frozenset[str], within: float = NET_UNDO_S) -> list[tuple]:
        """The costs on these networks of the last `within` seconds (to be undone); those on other networks are
        kept."""
        with self.lock:
            now = self.clock()
            out = [u for t, n, u in self.charges if now - t <= within and n in networks]
            self.charges = [c for c in self.charges if c[1] not in networks]
            return out


class Crawler:
    def __init__(self, root: Path, *, plan: round1.Plan | None = None, limit: int | None = None,
                 clock: Callable[[], float] = wall, sleep: Callable[[float], None] = time.sleep,
                 resolver: Callable[[str], tuple] = resolve,
                 client_factory: Callable[[WarcSink | None], Client] | None = None, rng: random.Random | None = None,
                 site_cap: int = SITE_CAP, variant_cap: int = VARIANT_CAP, file_cap: int = FILE_CAP,
                 image_cap: int = IMAGE_CAP, netcheck: Callable[[], bool] = netcheck,
                 disk_free: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free,
                 gap: tuple[float, float] = GAP, sites: frozenset[str] | None = None,
                 campus_url: str | None = CAMPUS_URL, local_address: Callable[[], str | None] = local_address,
                 decide: Callable[[str, str], str | None] | None = profiles.decision, end_wait_s: float = END_WAIT_S,
                 google_step: bool = True, reconcile_sources: Any = None, only: Iterable[int] | None = None):
        """plan: the site list (round1.load() when not given). gap: the run's (check_gap). sites: only these sites'
        rows (`archive run --sites`). campus_url: the campus check (None: no check, campus stays unknown). decide:
        the platform file's decision for a page's own site key (the platform it is written on) and a writing's key
        (profiles.decision; None: nothing decided, every writing unknown). end_wait_s: a run ends when what is left
        comes due later than this (C0, `archive run --end-wait`). google_step: Drive, Docs and Calendar addresses go
        to google_api's step (False: they wait as unhandled:google, B8). reconcile_sources: a reconcile.Sources; given,
        each round-1 site of the run is
        reconciled offline by this process as it finishes (design §18.2a step 3: reconcile.site_done, every check but
        R6), and again whenever it got more work and finished again (`archive run` gives it; None: no reconciling, as
        a test's crawl and rejudge's offline crawler). only: url ids (`archive run --only`): the run fetches those of
        them pending when it starts and touches no other row (the module's docstring); no site is reconciled then,
        reconcile_sources or not. An empty only is no row at all, never every row."""
        if end_wait_s <= 0:
            raise ValueError(f"end_wait_s is a time in seconds greater than 0 (got {end_wait_s:g})")
        self.root = root
        self.gap = check_gap(gap)
        self.plan = plan if plan is not None else round1.load()
        self.frontier = Frontier(root / "frontier.db")
        self.limit, self.clock, self.sleep, self.resolver = limit, clock, sleep, resolver
        self.client_factory = client_factory or self._new_client
        self.rng = rng or random.Random()
        self.site_cap, self.variant_cap, self.file_cap, self.image_cap = site_cap, variant_cap, file_cap, image_cap
        self.disk_free = disk_free
        self.sites = frozenset(sites) if sites else None
        # --only: the rows this run may fetch (narrowed to those pending when it starts, run(); the row of a listed
        # address moved to its other scheme joins them, _carry_only), and how many ids it was given (run_start, run_end)
        self.only = None if only is None else frozenset(int(i) for i in only)
        self._only_listed = None if self.only is None else len(self.only)
        self._only_lock = threading.Lock()
        self.net = _NetWatch(clock, netcheck, streak=self.frontier.server_fails, answered_ever=self._ever_answered)
        self.stop = threading.Event()
        self.end_reason: str | None = None
        self.run_id = int(time.time())
        self.campus_url, self.local_address = campus_url, local_address
        self.campus = "unknown"  # on, off or unknown: written on every request row (B4)
        self._campus_at, self._campus_addr = -math.inf, None
        self._campus_resume()
        self.decide = decide or (lambda _site, _key: None)
        self.end_wait_s = float(end_wait_s)
        # the Google step (design §7.6): a Drive, Docs or Calendar address becomes its API rows; None: B8's wait
        self.google: round1.GoogleStep | None = google_api.step(self.plan) if google_step else None
        # the platform chains (design §7.5): the presses planned from stored pages, and every list's walk
        self.chains = postback.Chains(self.frontier, root, clock=clock)
        self.start_run_caps()  # run() again at its start
        self._real_net = client_factory is None  # the real network: run() needs the machine-wide lock
        self._fetched = 0
        self._count_lock = threading.Lock()
        self._pause_lock = threading.Lock()
        self._campus_lock = threading.Lock()
        self._door_lock = threading.Lock()
        self._tl = threading.local()  # row: the url row a worker is handling now (a scheme move hands it another)
        self._last_kind: dict[str, str] = {}
        self._came: dict[str, float] = {}  # queue -> when its last request's answer or failure came back (_send)
        self._down_warned: set[str] = set()  # server queues warned server_down, until they answer
        self._fives: dict[str, int] = {}  # a server queue's 5xx in a row with no other answer between (SPARE_RUN)
        self._prefer: dict[str, str] = {}
        self._deny_rests: dict[str, int] = {}  # directory -> streaks of refusals answered by a wait this run
        self._answered_off: set[str] = set()
        self._dns_timed: dict[str, float] = {}
        self._doors: dict[str, dict[tuple[str, int], float]] = {}  # queue -> (scheme, port) -> its last answer
        self._stuck = 0  # loops in a row with rows due that no queue takes
        self._probe_tries: dict[tuple[str, str], int] = {}  # (host, detail type) -> id probes that got no answer
        # §18.2a step 3: the sites to reconcile as they finish (those this run worked on, and those never reconciled),
        # and those reconciled since they last got work; none for a run given --only (its sites are not complete)
        self.reconcile_sources = reconcile_sources if self.only is None else None
        self._site_lock = threading.Lock()
        self._to_reconcile: set[str] = set()
        self._reconciled_now: set[str] = set()
        self._site_look_at = -math.inf  # time.monotonic() of the main loop's last look for finished sites
        self._alive_at = -math.inf  # self.clock() of the main loop's last beat (ALIVE_S)
        self._reconciler: threading.Thread | None = None  # the thread that reconciles finished sites (review 10-26)
        self._gsimg_lock = threading.RLock()  # a site's Google Sites picture state (design §23 B): one writer at a time

    def _new_client(self, warc: WarcSink | None) -> Client:
        return archive_client(warc, resend_gap_s=self.gap[0], clock=self.clock, sleep=self.sleep)

    def halt(self, reason: str) -> None:
        """Stop the run (its in-flight requests finish); the first reason given is the one run_end says."""
        if self.end_reason is None:
            self.end_reason = reason
        self.stop.set()

    # ---- discovery
    def queue_of(self, host: str) -> str:
        row = self.frontier.host(host)
        if row is not None and row["queue"] not in ("?", "", None):
            return row["queue"]
        named = google_api.queue_for(host)  # Google's APIs and its forms' host: a named queue each (design §6.1)
        if named is not None:
            self.frontier.set_host(host, queue=named, dns_at=self.clock(), dns_fail=0)
            return named
        started = time.perf_counter()
        queue, ip, cname = self._lookup(host)
        self.frontier.set_host(host, queue=queue, ip=ip, cname=cname, dns_at=self.clock(),
                               dns_fail=int(queue == "dns-fail"), dns_ms=(time.perf_counter() - started) * 1000)
        self._dns_timed[queue] = self.clock()
        return queue

    def _lookup(self, host: str) -> tuple[str, str | None, str | None]:
        """The resolver's answer, but a name that resolves to this machine (scope.own_address: a loopback or an
        unspecified address) resolves to no server we may ask: dns-fail, which no worker sends on (run()), so its rows
        wait and then fail as a name that does not resolve does (its back-off, `archive retry dns`, failed dns at
        DNS_GIVE_UP: the lab bucket on the pages that use them, 等搬到实验室 / 校内也连不上, never 规则不抓). A URL that
        names this machine is scope.judge's (loopback), before any lookup. 11-06, with that rule 【Claude 的执行办法】"""
        queue, ip, cname = self.resolver(host)
        if scope.own_address(ip) or (queue.startswith("ip:") and scope.own_address(queue[3:])):
            return "dns-fail", None, None
        return queue, ip, cname

    def _dns_step(self, queue: str) -> bool:
        """§6.2: TCP's connect time includes the name lookup, so each host of a queue has its lookup timed again every
        DNS_TIME_EVERY_S (host.dns_ms), on the queue's own thread; the answer never moves the host (that is
        _reresolve's, for names that did not resolve). Whether it timed them now."""
        if self.clock() - self._dns_timed.get(queue, -math.inf) < DNS_TIME_EVERY_S:
            return False
        self._dns_timed[queue] = self.clock()
        for host in self.frontier.hosts_of(queue):
            started = time.perf_counter()
            self.resolver(host)
            self.frontier.set_host(host, dns_ms=(time.perf_counter() - started) * 1000)
        return True

    def admit(self, href: str, base: str | None = None, from_row: Mapping[str, Any] | None = None,
              how: str = "link") -> round1.Admission | None:
        return round1.admit(self.plan, href, base, from_row, how, google=self.google)

    def enqueue(self, href: str, *, base: str | None = None, from_row: Mapping[str, Any] | None = None,
                how: str = "link", depth: int = 0, found_via: str = "link", prio: int | None = None,
                kind0: str | None = None, old_ref: int | None = None) -> str:
        """Admit one address (round1.admit) and add its url row; returns the link table's verdict."""
        return self._enqueue(href, base=base, from_row=from_row, how=how, depth=depth, found_via=found_via, prio=prio,
                             kind0=kind0, old_ref=old_ref).verdict

    def seed_url(self, url: str, *, source: str = "seed", found_via: str = "seed", prio: int | None = PRIO_LIST,
                 kind0: str | None = None, depth: int = 0, old_ref: int | None = None,
                 from_row: Mapping[str, Any] | None = None) -> str:
        """A seed (design §5): admitted with no page behind it, its source noted (seed_src: a URL may have several)."""
        e = self._enqueue(url, from_row=from_row, depth=depth, found_via=found_via, prio=prio, kind0=kind0,
                          old_ref=old_ref)
        if e.url is not None:
            self.frontier.seed_src(e.url, source)
        return e.verdict

    def seed_login_check(self, url: str) -> str:
        """One of the listed login addresses inside round-1 sites (design §4.2): one plain GET (kind0 login_check), its
        own login rule set aside; what comes back says whether it is one."""
        canon = scope.canonical(url)
        if canon is None:
            return "bad_url"
        s = self.plan.site(scope.site_key(canon))
        owner, tier = (s.site, s.tier) if s is not None and s.round == "1" else (scope.site_key(canon), round1.EXTRA_TIER)
        e = self._enqueue(canon, placement=round1.Placement("r1", None, owner, tier, 0, "login_check"), url=canon,
                          found_via="seed", prio=PRIO_LIST, kind0="login_check")
        if e.url is not None:
            self.frontier.seed_src(e.url, "login_check")
        return e.verdict

    def _enqueue(self, href: str, *, base: str | None = None, from_row: Mapping[str, Any] | None = None,
                 how: str = "link", depth: int = 0, found_via: str = "link", prio: int | None = None,
                 kind0: str | None = None, old_ref: int | None = None, found_from: int | None = None,
                 placement: round1.Placement | None = None, url: str | None = None,
                 referrer: str | None = None) -> Enqueued:
        if placement is None:
            adm = self.admit(href, base, from_row, how)
            if adm is None:
                return Enqueued("bad_url", None, None, None)
            url, placement = adm.url, adm.placement
        if placement.state is None:  # out:<rule> or unhandled:<why>: the link table only
            return Enqueued(placement.reason or "out", None, None, placement)
        if found_from is None and from_row is not None:
            found_from = from_row["id"]
        kind0 = kind0 or placement.kind0
        rewritten = False
        if placement.state == "pending":
            moved = self._scheme_rewrite(url)
            rewritten, url = moved != url, moved
        if prio is None and kind0 == "page" and profiles.page_kind(url) == "list":
            prio = PRIO_LIST  # a platform's list page (design §7.4 list_paths: prio 0)
        prio = _PRIO_OF0.get(kind0, 1) if prio is None else prio
        existing = self.frontier.row(url)
        if existing is not None:
            self._upgrade(existing, placement, prio, kind0, found_from)
            return Enqueued("scheme_switch" if rewritten else "dup", url, existing["id"], placement)
        host = urlsplit(url).hostname or ""
        kind = _KIND_OF0.get(kind0, "page")
        state, reason, next_at = placement.state, placement.reason, 0.0
        if state == "pending":
            cap = self._cap(url, placement.owner_site, kind, depth)
            if cap is not None:
                state, reason = "held", cap
                if cap.startswith("cap:run_") and (placement.owner_site, kind) not in self._run_cap_warned:
                    self._run_cap_warned.add((placement.owner_site, kind))
                    self.frontier.event("warn", "run_cap", f"{placement.owner_site}: this run added as many {kind} "
                                        f"rows as one run may ({cap}); the rest are held for a person to look "
                                        "(`nknu archive retry cap:run` lets them go)", host, url)
            else:
                state, reason, next_at = self._due(url, host)
        owner = placement.owner_site
        s = self.plan.site(owner) if owner else None
        added = self.frontier.add(url, host=host, site=scope.site_key(url), unit=(s.name or None) if s else None,
                                  tier=placement.tier, kind=kind, depth=depth, found_via=found_via,
                                  found_from=found_from, state=state, reason=reason, next_at=next_at,
                                  owner_site=owner, prio=prio, leaf=placement.leaf, kind0=kind0,
                                  added_at=self.clock(), old_ref=old_ref, referrer=referrer)
        row = self.frontier.row(url)
        if not added:
            return Enqueued("scheme_switch" if rewritten else "dup", url, row["id"] if row else None, placement)
        verdict = {"pending": "queued", "held": reason, "later": reason, "failed": f"failed:{reason}"}[state]
        return Enqueued("scheme_switch" if rewritten else verdict, url, row["id"], placement)

    def _due(self, url: str, host: str) -> tuple[str, str | None, float]:
        """A row this round fetches: (state, reason, next_at) by its host's name lookup and its directory's refusals."""
        queue = self.queue_of(host)
        if queue == "dns-fail":  # waits for the name to resolve (run() tries again)
            h = self.frontier.host(host)
            state = "failed" if h["dns_fail"] >= DNS_GIVE_UP else "pending"
            return state, "dns", h["dns_at"] + _dns_wait(h["dns_fail"])
        if host == google_api.API_HOST and not google_api.key_set():
            return "pending", "no_api_key", 0.0  # §7.6: waits for a run with the key (hold_for_key), no failure
        if (until := self.frontier.deny_until(deny_dir(url))) > self.clock():
            return "pending", "denied_dir", until  # its directory is waiting out a streak of refusals
        return "pending", None, 0.0

    def _upgrade(self, row, placement: round1.Placement, prio: int, kind0: str, found_from: int | None) -> None:
        """An address known already. Round two's row that a round-1 page now uses becomes this round's (the page's
        resource, design §4.4); one this round's page links again comes forward when it only had an old source
        (prio, §6.7: an old link or Common Crawl adds 1)."""
        if row["state"] == "later" and placement.state == "pending":
            owner = placement.owner_site
            s = self.plan.site(owner) if owner else None
            state, reason, next_at = self._due(row["url"], row["host"])
            self.frontier.x("UPDATE url SET state=?, reason=?, next_at=?, tier=?, owner_site=?, leaf=?, kind0=?, "
                            "kind=?, prio=?, found_from=coalesce(?, found_from), unit=? WHERE id=? AND state='later'",
                            (state, reason, next_at, placement.tier, owner, placement.leaf, kind0,
                             _KIND_OF0.get(kind0, "page"), prio, found_from, (s.name or None) if s else None,
                             row["id"]))
        elif prio < row["prio"]:
            self.frontier.x("UPDATE url SET prio=? WHERE id=? AND prio>?", (prio, row["id"], prio))

    def start_run_caps(self) -> None:
        """A run's start for the growth caps (UPDATE_SITES_KEY): the sites under periodic update and the moment from
        which a row counts as this run's."""
        raw = self.frontier.meta(UPDATE_SITES_KEY)
        try:
            sites = json.loads(raw) if raw else []
        except ValueError:
            sites = []
        if not isinstance(sites, list):
            sites = []
        self._update_sites = frozenset(x for x in sites if isinstance(x, str))
        self._run_started = self.clock()
        self._let_go: dict[tuple[str, str], int] = {}  # rows a run cap counts that _release_caps let go this run
        self._run_cap_warned: set[tuple[str, str]] = set()

    def _cap(self, url: str, owner: str | None, kind: str, depth: int) -> str | None:
        """The cap a new row of this round would pass (design §9): held, never skipped, and listed. A site under
        periodic update has its run caps instead (RUN_CAPS: what this run added to it, the rows let go included)."""
        if depth > MAX_DEPTH:
            return "cap:depth"
        if owner is None:
            return None
        if owner in self._update_sites:
            limit = {"page": RUN_CAPS.pages, "file": RUN_CAPS.files, "image": RUN_CAPS.images}.get(kind)
            if limit is not None and (self.frontier.count_added(owner, kind, self._run_started)
                                      + self._let_go.get((owner, kind), 0)) >= limit:
                return f"cap:run_{kind}"
            return None
        pk = profiles.page_kind(url)
        if kind == "page" and (pk == "detail" or scope.is_platform_detail(url)):
            # a platform's detail pages, SSO's activity detail pages (11-06) and 成教所's 所友名錄 pages (11-09) are
            # free of the caps (design §7.4 detail_paths: 免上限), the depth limit aside; they still count for their
            # site (frontier.COUNT_SITE)
            return None
        if kind == "file" and self.frontier.count_site(owner, "file") >= self.file_cap:
            return "cap:file"
        if kind == "image" and self.frontier.count_site(owner, "image") >= self.image_cap:
            return "cap:image"
        if kind == "page" and self.frontier.count_site(owner, "page") >= self.site_cap:
            return "cap:site"
        if (kind in ("page", "file") and not scope.is_platform_path(url) and pk != "list"  # lists: no variant cap
                and self.frontier.count_path(owner, url.split("?", 1)[0]) >= self.variant_cap):
            return "cap:variant"
        return None

    def _release_caps(self) -> int:
        """At a run's start: a row held by one of the crawler's caps (CAP_REASONS) that the rules of today no longer
        hold is due again, as a new row would be (_due: its host is looked up now if nobody had); one a cap still
        holds stays held, its reason as it was. In id order, the order the rows were found: each one let go counts at
        once (frontier.COUNT_SITE), so a site with room for k more pages lets go of its first k. Every site's rows, a
        run given --sites too, as _skip_aliases. 11-03 (P02): the variant cap, SSO's activity detail pages free of it;
        11-06: every cap _cap gives, SSO's activity detail pages free of the site cap too (scope.PLATFORM_DETAIL_PATHS;
        【用户同意 Claude 的建议】: Claude's 11-04 fix 「让这两种活动页也不受整站上限；重开时，被挡下的会自动放回去抓」,
        the owner 11-04 「待会再修」, 11-06 the button 「先修三件再一起重开」; every cap of _cap and none of Google's:
        【Claude 的执行办法】). A run given --only lets none go: its rows are those pending at its start (_narrow_only,
        before this). Returns the rows let go."""
        by_id, id_args = ids_clause("id", self.only)
        marks = ",".join("?" * len(CAP_REASONS))
        let_go: dict[str, int] = {}
        for r in self.frontier.q(f"SELECT * FROM url WHERE state='held' AND reason IN ({marks}){by_id} ORDER BY id",
                                 (*CAP_REASONS, *id_args)):
            if self._cap(r["url"], r["owner_site"], r["kind"], r["depth"]) is not None:
                continue
            state, reason, next_at = self._due(r["url"], r["host"])
            if self.frontier.x("UPDATE url SET state=?, reason=?, next_at=? WHERE id=? AND state='held'",
                               (state, reason, next_at, r["id"])):
                let_go[r["reason"]] = let_go.get(r["reason"], 0) + 1
                if r["owner_site"] in self._update_sites:  # counts toward the site's run cap (stage 1)
                    k = (r["owner_site"], r["kind"])
                    self._let_go[k] = self._let_go.get(k, 0) + 1
        n = sum(let_go.values())
        if n:
            self.frontier.event("info", "caps_released", f"{n} rows held by a cap are free of it now ("
                                + ", ".join(f"{k} {v}" for k, v in sorted(let_go.items())) + ")")
        return n

    def _skip_aliases(self) -> int:
        """At a run's start (11-03, P05): a row not fetched yet (pending, held, later) whose address canonical() now
        writes another way (an SSO WebIdx page under cwebidx/cmenuid or with pagenum=1) is skipped, out:alias, its
        final_url the one address, and is neither fetched nor pressed: that address's row is (made from this one, with
        its state, when there is none, as _moved_row does), and a page's resource that pointed at the alias points
        there. --only: the listed rows only. Returns the rows skipped."""
        by_id, id_args = ids_clause("id", self.only)
        n = 0
        for r in self.frontier.q(f"SELECT * FROM url WHERE state IN ('pending','held','later'){by_id} ORDER BY id",
                                 id_args):
            canon = scope.canonical(r["url"])
            if canon is None or canon == r["url"]:
                continue
            if self.frontier.row(canon) is None:
                self.frontier.add(canon, host=r["host"], site=scope.site_key(canon), unit=r["unit"], tier=r["tier"],
                                  kind=r["kind"], depth=r["depth"], found_via=r["found_via"],
                                  found_from=r["found_from"], state=r["state"], reason=r["reason"],
                                  next_at=r["next_at"], owner_site=r["owner_site"], prio=r["prio"], leaf=r["leaf"],
                                  kind0=r["kind0"], added_at=self.clock(), old_ref=r["old_ref"],
                                  referrer=r["referrer"])
            one = self.frontier.row(canon)
            self.frontier.x("UPDATE page_res SET res_id=? WHERE res_id=?", (one["id"], r["id"]))
            self.frontier.finish(r["id"], state="skipped", reason="out:alias", final_url=canon, fetched_at=None)
            n += 1
        if n:
            self.frontier.event("info", "aliases_skipped", f"{n} rows are the same page as another row's address")
        return n

    def _scheme_rewrite(self, url: str) -> str:
        """An address of a host known to answer on one scheme only is written in that one (design §6.6)."""
        parts = urlsplit(url)
        h = self.frontier.host(parts.hostname or "")
        if h is None or not h["scheme_only"] or h["scheme_only"] == parts.scheme or parts.port is not None:
            return url
        return urlunsplit(parts._replace(scheme=h["scheme_only"]))

    def _reresolve(self, force: bool = False) -> None:
        """Names that did not resolve, tried again when their back-off is over (all of them when force). Every pending
        row of such a host waits for its name, whatever brought it (a row `retry` put back keeps no next_at of its
        own: second review 10-26), and its reason says so (dns); nothing was ever sent to a name that never resolved,
        so when it resolves they are all due. A run given --only looks up again the names of its listed rows only and
        moves those rows only (the host's other rows keep their wait), and never gives a name up: that fails every row
        of the host, a full run's to do (its lookups still count, up to one short of it)."""
        now = self.clock()
        if force and self.only is None:
            # a name looked up DNS_GIVE_UP times or more under a higher limit (8 before 11-08) is never looked up again:
            # its rows still waiting give up now, as at the lookup that reaches the limit
            for h in self.frontier.q("SELECT host, dns_fail FROM host WHERE queue='dns-fail' AND dns_fail >= ? AND host "
                                     "IN (SELECT host FROM url WHERE state='pending')", (DNS_GIVE_UP,)):
                n = self.frontier.x("UPDATE url SET state='failed', reason='dns' WHERE host=? AND state='pending'",
                                    (h["host"],))
                self.frontier.event("warn", "dns_gave_up", f"did not resolve {h['dns_fail']} times: {n} URLs failed "
                                    "(nknu archive retry dns tries again)", h["host"])
        by_id, id_args = self._ids("u.id")
        mine = f" AND host IN (SELECT u.host FROM url u WHERE u.state = 'pending'{by_id})" if by_id else ""
        row_id, row_args = self._ids("id")
        for h in self.frontier.q("SELECT host, dns_fail, dns_at FROM host WHERE queue='dns-fail' AND dns_fail < ?"
                                 f"{mine}", (DNS_GIVE_UP, *id_args)):
            host = h["host"]
            if not force and now < h["dns_at"] + _dns_wait(h["dns_fail"]):
                continue
            queue, ip, cname = self._lookup(host)
            if queue != "dns-fail":
                self.frontier.set_host(host, queue=queue, ip=ip, cname=cname, dns_fail=0, dns_at=now)
                n = self.frontier.x("UPDATE url SET next_at=0, reason=CASE WHEN reason='dns' THEN NULL ELSE reason END "
                                    f"WHERE host=? AND state='pending'{row_id}", (host, *row_args))
                self.frontier.event("info", "dns_back", f"resolves now ({queue}): {n} URLs due", host)
                continue
            fails = h["dns_fail"] + (1 if self.net.check() else 0)  # an outage is not the name's fault
            if self.only is not None:
                fails = min(fails, DNS_GIVE_UP - 1)
            self.frontier.set_host(host, dns_fail=fails, dns_at=now)
            if fails >= DNS_GIVE_UP:
                n = self.frontier.x("UPDATE url SET state='failed', reason='dns' WHERE host=? AND state='pending'",
                                    (host,))
                self.frontier.event("warn", "dns_gave_up", f"did not resolve {fails} times: {n} URLs failed "
                                    "(nknu archive retry dns tries again)", host)
            else:
                self.frontier.x("UPDATE url SET next_at=max(next_at, ?), reason='dns' WHERE host=? AND state='pending'"
                                f"{row_id}", (now + _dns_wait(fails), host, *row_args))

    def _place_hosts(self) -> int:
        """The hosts of pending rows that nobody looked up (second review 10-26: `retry` brings back round two's rows
        and those a cap held, which never had their host looked up; the old archive's hosts are seeded with queue '?'):
        looked up now, before any is fetched, so that each goes out on its server's own queue. One that does not
        resolve waits like any other such name (reason dns, its back-off). A run given --only looks up the hosts of
        its listed rows only, and moves those rows only. Returns how many hosts were looked up."""
        by_id, id_args = self._ids("u.id")
        hosts = [r[0] for r in self.frontier.q(
            "SELECT DISTINCT u.host FROM url u LEFT JOIN host h ON h.host = u.host "
            f"WHERE u.state = 'pending' AND (h.host IS NULL OR h.queue IN ('?', '')){by_id}", id_args)]
        row_id, row_args = self._ids("id")
        for host in hosts:
            if self.queue_of(host) == "dns-fail":
                h = self.frontier.host(host)
                self.frontier.x("UPDATE url SET next_at=max(next_at, ?), reason='dns' WHERE host=? AND state='pending'"
                                f"{row_id}", (h["dns_at"] + _dns_wait(h["dns_fail"]), host, *row_args))
        if hosts:
            log.info("looked up %d hosts nobody had looked up", len(hosts))
        return len(hosts)

    # ---- running
    def run(self, poll_s: float = 5.0) -> None:
        """The caller holds run.lock (hold_run_lock) and the school lock: only then may rows left active be taken back.
        On the real network a run that does not hold the school lock is refused before any row changes
        (schoollock.SchoolLockNotHeld: second review 10-26). Ends when nothing is left, when what is left only waits past
        END_WAIT_S (B1), when rows are due that no queue can take (stuck), or when halted."""
        if self._real_net and not schoollock.held():
            raise schoollock.SchoolLockNotHeld(f"a crawl on the real network holds the machine-wide lock on school "
                                               f"requests ({schoollock.lock_path()}) for its whole run: none was taken")
        n = self.frontier.requeue_active(ids=self.only)
        if n:
            log.info("requeued %d URLs left active by the last run", n)
        if self.only is not None:
            self._narrow_only()
        # §7.5 step 9: a chain left pressing starts again from its GET (--only: a listed row's chain only)
        chains = self.chains.resume(ids=self.only)
        if chains:
            log.info("%d lists start their walk again from their GET (left pressing by the last run)", chains)
        if self.only is None and self.chains.close_stale_looks():  # periodic updates, stage 1: looks no answer ends
            log.info("looks whose list page never came back closed, the last whole walks' records kept")
        # §7.6: no key, the googleapis queue waits (no failure); --only: the listed rows' reasons only
        google_api.hold_for_key(self.frontier, self.clock(), ids=self.only)
        self._skip_aliases()  # 11-03 P05: a row under a spelling canonical() now merges is never fetched
        self.start_run_caps()  # periodic updates, stage 1: the sites under update and this run's start
        self._release_caps()  # 11-03 P02, 11-06: a row held by a cap the rules of today no longer hold is let go
        sites = sorted(self.sites) if self.sites else None
        if self.only is not None and self.sites is None:
            sites = self._only_sites()  # never null (every site) for a run given --only
        start = {"pid": os.getpid(), "gap": list(self.gap), "sites": sites, "end_wait_s": self.end_wait_s,
                 "school_lock": str(schoollock.lock_path())}
        if self._only_listed is not None:  # --only: how many ids were listed, and how many of them were pending
            start |= {"only": self._only_listed, "only_pending": len(self.only)}
        self.frontier.event("info", "run_start", json.dumps(start))
        workers: dict[str, threading.Thread] = {}
        self._never_reconciled()
        try:
            self._reresolve(force=True)
            self._place_hosts()
            while not self.stop.is_set():
                try:
                    self._beat()
                    if self._limit_reached():
                        self.halt("limit")
                        break
                    self._reresolve()
                    if not self._paused():
                        now = self.clock()
                        due = self.frontier.queues(now, self.sites, ids=self.only)
                        # a queue whose only work is a press
                        for q in self.chains.due_queues(now, self.sites, ids=self.only):
                            due.setdefault(q, [])
                        # the campus check goes on SSO's queue, and only when that queue may send: a worker started for a
                        # check its paused queue cannot make would idle, and keep the run from ever ending (second review)
                        if (self._campus_due() and (cq := self.campus_queue()) not in (None, "dns-fail")
                                and self.frontier.paused_until(cq) <= now):
                            due.setdefault(cq, [])
                        for queue in due:
                            if queue == "dns-fail":
                                continue
                            t = workers.get(queue)
                            if t is None or not t.is_alive():
                                t = threading.Thread(target=self._work, args=(queue,), name=f"q-{queue}", daemon=True)
                                workers[queue] = t
                                t.start()
                    if not any(t.is_alive() for t in workers.values()) and self._should_end():
                        if self.end_reason in ("finished", "waiting"):  # the sites it leaves done, before its run_end
                            self._join_reconciler()
                            self._reconcile_finished()
                        break
                    if time.monotonic() - self._site_look_at >= SITE_LOOK_S:
                        self._site_look_at = time.monotonic()
                        self._reconcile_in_background()
                except sqlite3.DatabaseError as exc:
                    if not db_busy(exc):
                        raise
                    # held by another connection (db_busy): the next turn tries again, the run goes on (11-08)
                    log.warning("the frontier database was busy (%s): the run goes on", exc)
                    self._db_busy_event(f"the run's main loop: {exc}; tried again at its next turn")
                self.stop.wait(poll_s)
        except sqlite3.DatabaseError:
            log.exception("the frontier database failed, stopping the run")
            self.halt("db_error")
        finally:
            self.stop.set()
            for t in workers.values():
                t.join(timeout=120)
            self._join_reconciler()  # it stops between pages: the stop above reached it
            with suppress(sqlite3.Error):
                self.chains.sso_totals()  # §7.5 step 7: SSO 公告中心's categories add up to its whole list
            with suppress(sqlite3.Error):
                left, nxt = self.frontier.waiting(self.sites, ids=self.only)
                presses, p_nxt, _active = self.chains.waiting(self.sites, ids=self.only)
                nxt = min((t for t in (nxt, p_nxt) if t is not None), default=None)
                if self.only is not None and self.sites is None:  # and those of a listed row moved to its other scheme
                    sites = sorted({*(sites or ()), *self._only_sites()})
                end = {"reason": self.end_reason or "stop", "sites": sites, "pending": left, "presses": presses,
                       "next_due": nxt}
                if self._only_listed is not None:  # --only: pending and presses are the listed rows'
                    end["only"] = self._only_listed
                self.frontier.event("info", "run_end", json.dumps(end))

    def _ids(self, column: str) -> tuple[str, list[str]]:
        """--only's condition on a column holding a url id (frontier.ids_clause), with its argument: ('', []) for a
        run without it."""
        return ids_clause(column, self.only)

    def _narrow_only(self) -> None:
        """At the start of a run given --only (after rows left active went back to pending): the listed rows it may
        fetch are those pending now. One done, failed or left for later is skipped, its presses with it (a done
        list page's chain is never started again from a new GET of it)."""
        by_id, id_args = self._ids("id")
        pending = frozenset(r[0] for r in self.frontier.q(f"SELECT id FROM url WHERE state = 'pending'{by_id}",
                                                          id_args))
        with self._only_lock:
            skipped, self.only = len(self.only) - len(pending), pending
        if skipped:
            log.info("--only: %d of the %d listed rows are not pending (done, failed or left for later): skipped",
                     skipped, self._only_listed)

    def _only_sites(self) -> list[str]:
        """The sites whose records a run given --only (and no --sites) can change: those its rows count for, and those
        of the pages that use them (page_res; '' for a row of no site, as the dashboard keys it). Its run_start and
        run_end name them where a full run writes null, every site: monitor reads a run_end as newer than the
        reconciliation and the browser check of each site it covers (review 11-02)."""
        by_id, id_args = self._ids("u.id")
        by_res, res_args = self._ids("r.res_id")
        return [r[0] for r in self.frontier.q(
            f"SELECT coalesce(u.owner_site, '') FROM url u WHERE 1{by_id} UNION SELECT coalesce(p.owner_site, '') "
            f"FROM page_res r JOIN url p ON p.id = r.page_id WHERE 1{by_res} ORDER BY 1", [*id_args, *res_args])]

    def _carry_only(self, uid: int, moved_id: int) -> None:
        """A listed row moved to the address in its other scheme (design §6.6, _move_scheme, _switch_scheme): that
        row is the listed row's from now on (a run given --only fetches, retries and waits for it as it would the
        listed row)."""
        if self.only is None or uid not in self.only:
            return
        with self._only_lock:
            self.only = self.only | {moved_id}

    def _should_end(self) -> bool:
        """With no worker alive: whether the run ends now (and why, in end_reason). Nothing left: finished. Rows still
        active have no worker to hold them (a bug's): each is a crash, and the run looks again. The soonest row due
        later than END_WAIT_S: waiting (B1). Rows due now that no queue takes (none looked up, or one nobody can
        dispatch): the hosts are looked up once more, and if that is not it two loops in a row, the run ends (stuck),
        with an error event, instead of waiting for ever with both locks held. A run given --only counts, and puts
        right, its listed rows and their presses only: it ends once none of them is left."""
        left, nxt = self.frontier.waiting(self.sites, ids=self.only)
        presses, p_nxt, active = self.chains.waiting(self.sites, ids=self.only)
        if left == 0 and presses == 0:
            self.end_reason = self.end_reason or "finished"
            log.info("nothing left to fetch")
            return True
        if active:  # presses left active with no worker holding them (a bug's): sent or not, never sent again
            for list_id in sorted({a["list_id"] for a in active if a["kind"] not in postback.SOLO_KINDS}):
                self.chains.restart_chain(list_id, "crash:left_active")
            by_list, list_args = self._ids("list_id")
            n = self.frontier.x("UPDATE action SET state='failed', reason='crash:left_active' WHERE kind='click' "
                                f"AND state='active'{by_list}", list_args)
            # a read of a data address only reads (design §23 C1): read again with a new request
            self.frontier.x(f"UPDATE action SET state='pending' WHERE kind='data' AND state='active'{by_list}",
                            list_args)
            self.frontier.event("error", "left_active", f"{len(active)} presses were left active with no worker "
                                f"holding them (a crawler bug): their lists start again from their GET, {n} file "
                                "buttons failed as crash:left_active (nknu archive retry click puts them back)")
            return False
        if presses:  # what the presses wait for counts as the rows' waits do
            nxt = min((t for t in (nxt, p_nxt) if t is not None), default=None)
            if nxt is None:
                return False  # only active rows and presses being restarted: the run looks again
        if nxt is None:  # only active rows, and no worker holds them
            by_id, id_args = self._ids("id")
            n = self.frontier.x("UPDATE url SET state='failed', reason='crash:left_active' WHERE state='active'"
                                f"{by_id}", id_args)
            self.frontier.event("error", "left_active", f"{n} URLs were left active with no worker holding them (a "
                                "crawler bug): failed as crash:left_active (nknu archive retry crash tries again)")
            return False
        now = self.clock()
        if nxt - now > self.end_wait_s:
            self.end_reason = self.end_reason or "waiting"
            log.info("only rows that wait for later are left (%d, %d presses): left for the next run", left, presses)
            return True
        if (nxt <= now and not [q for q in self.frontier.queues(now, self.sites, ids=self.only) if q != "dns-fail"]
                and not self.chains.due_queues(now, self.sites, ids=self.only)):
            if self._place_hosts():
                self._stuck = 0
                return False
            self._stuck += 1
            if self._stuck >= 2:
                self.frontier.event("error", "stuck", f"{left} URLs are due that no queue takes (a crawler bug): the "
                                    "run ends instead of waiting for ever")
                self.end_reason = self.end_reason or "stuck"
                return True
            return False
        self._stuck = 0
        return False

    # ---- a site finished: its offline reconciliation (design §18.2a step 3)
    def _round1_site(self, site: str | None) -> bool:
        s = self.plan.site(site) if site else None
        return s is not None and s.round == "1" and (self.sites is None or site in self.sites)

    def _reconciled_here(self, site: str | None) -> bool:
        """Whether this run reconciles the site as it finishes: a round-1 site of the run, unless it is under periodic
        update (UPDATE_SITES_KEY): the nightly change list (watch.night) stands in for it there. On the whole school a
        night's reconcile re-read every page of every site it touched, about 1.5–3 h, and services' was thrown away at
        the 600 s join (the check of the first real night, wf_9c6e1448-2cc; the owner 11-16: 「先修这三件」); a person's
        `nknu archive reconcile` still reconciles any site."""
        return (self.reconcile_sources is not None and self._round1_site(site)
                and site not in self._update_sites)

    def _worked_on(self, site: str | None) -> None:
        """A row or a press of this site is being handled: the site is to be reconciled (again) once it finishes."""
        if self._reconciled_here(site):
            with self._site_lock:
                self._to_reconcile.add(site)
                self._reconciled_now.discard(site)

    def _never_reconciled(self) -> None:
        """At the start of a run: the run's round-1 sites that have no reconciliation at all are looked at too (one
        that finished in a run that ended before it could be reconciled)."""
        if self.reconcile_sources is None:
            return
        have = {r[0] for r in self.frontier.q("SELECT site FROM reconcile_site")}
        sites = self.sites if self.sites is not None else {s.site for s in self.plan.sites.values() if s.round == "1"}
        with self._site_lock:
            self._to_reconcile |= {s for s in sites if s not in have and self._reconciled_here(s)}

    def _beat(self) -> None:
        """meta run:alive_at, at most once per ALIVE_S: when the main loop last went round, so the dashboard tells a run
        that hangs while it holds run.lock from one whose queues all rest (dashboard design 2026-11-04 §2.5)."""
        now = self.clock()
        if now - self._alive_at >= ALIVE_S:
            self._alive_at = now
            self.frontier.meta("run:alive_at", f"{now:.3f}")

    def _reconcile_in_background(self) -> None:
        """Start the reconciliation of finished sites on a thread of its own, unless one is at work (review 10-26: on
        the main loop it kept the loop from starting workers again, looking whether the run should end and minding a
        stop, for as long as a large site took)."""
        if self.reconcile_sources is None or (self._reconciler is not None and self._reconciler.is_alive()):
            return
        with self._site_lock:
            if not self._to_reconcile - self._reconciled_now:
                return
        self._reconciler = threading.Thread(target=self._reconcile_finished, name="reconcile-sites", daemon=True)
        self._reconciler.start()

    def _join_reconciler(self) -> None:
        t = self._reconciler
        if t is not None and t.is_alive():
            t.join(timeout=STOP_WAIT_S)
            if t.is_alive():
                log.error("the reconciliation of finished sites has not stopped in %.0f s", STOP_WAIT_S)

    def _reconcile_finished(self) -> int:
        """Reconcile, offline and in this process (the one writer, design §6.9), each site to reconcile that has
        nothing left this run but retries and the lab (reconcile.site_finished, B1): reconcile.site_done, every check
        but R6 (skip, so gates_ok 0: 「⚠ 纪录齐了，还没对账」, with what failed already listed); R6 and a person's look
        wait for `nknu archive reconcile` once the run stopped. A site that gets work again is reconciled again when it
        finishes again. A failure is an error event, never the run's end. It reads and writes through a connection of
        its own (Frontier.sibling: a long read never holds the lock every worker takes) and stops between pages when
        the run stops (reconcile.Stopped: nothing of that site written as done). Returns how many it reconciled."""
        if self.reconcile_sources is None:
            return 0
        from nknu.archive import reconcile

        with self._site_lock:
            todo = sorted(self._to_reconcile - self._reconciled_now)
        if not todo:
            return 0
        n = 0
        fr = self.frontier.sibling()
        try:
            for site in todo:
                if self.stop.is_set():
                    break
                try:
                    if not reconcile.site_finished(fr, site):
                        continue
                except sqlite3.Error:
                    log.exception("whether %s finished could not be read", site)
                    continue
                with self._site_lock:
                    if site in self._reconciled_now or site not in self._to_reconcile:
                        continue
                    self._reconciled_now.add(site)
                t0 = time.monotonic()
                try:
                    got = reconcile.site_done(fr, self.root, site, plan=self.plan, batch=f"run:{self.run_id}",
                                              sources=self.reconcile_sources, stop=self.stop.is_set)
                    n += 1
                    failing = [c for c, k in got.checks.items() if c in reconcile.GATES and k.status == reconcile.FAIL]
                    log.info("site %s finished: reconciled offline in %.1f s (R6 waits for the run to stop)%s", site,
                             time.monotonic() - t0, f"; failed: {', '.join(failing)}" if failing else "")
                except reconcile.Stopped:
                    with self._site_lock:
                        self._reconciled_now.discard(site)
                    log.info("site %s: its offline reconciliation stopped with the run after %.1f s", site,
                             time.monotonic() - t0)
                    with suppress(sqlite3.Error):
                        fr.event("info", "reconcile_stopped", f"{site}: stopped with the run (nknu archive reconcile, "
                                 "or the next run, does it again)")
                    break
                except Exception as exc:  # a reconciliation that broke never stops the crawl
                    log.exception("site %s: the offline reconciliation failed", site)
                    with suppress(sqlite3.Error):
                        fr.event("error", "reconcile_failed",
                                 f"{site}: {type(exc).__name__}: {exc} (nknu archive reconcile tries again "
                                 "once the run stopped)"[:500])
        finally:
            fr.close()
        return n

    def _limit_reached(self) -> bool:
        with self._count_lock:
            return self.limit is not None and self._fetched >= self.limit

    def _count(self) -> None:
        with self._count_lock:
            self._fetched += 1
            if self.limit is not None and self._fetched >= self.limit:
                self.halt("limit")

    def _paused(self, queue: str | None = None) -> bool:
        """Whether a network pause holds: every queue's, or (given a queue) also its network's. When one is over, a
        fresh connectivity check decides: resume, or five more minutes."""
        keys = [ALL_QUEUES] + ([net_key(network_of(queue))] if queue else [])
        with self._pause_lock:
            for key in keys:
                until = self.frontier.pause_of(key)
                if until == 0:
                    continue
                now = self.clock()
                if until > now:
                    return True
                if not self.net.check(fresh=True):
                    self.frontier.pause(key, now + NET_PAUSE_S, "the network is still down")
                    return True
                self.frontier.unpause(key)
                network = None if key == ALL_QUEUES else network_of(queue)
                self.net.grace(network)
                if network in (None, "school"):
                    self._campus_again()
                self.frontier.event("info", "online", "the network is back: every queue resumes" if network is None
                                    else "the school network's pause is over: its queues try again, and no connection "
                                    "failure is held against its servers until one of them answers"
                                    if network == "school" else f"the {network} network is back: its queues resume")
            return False

    def _next(self, queue: str) -> sqlite3.Row | None:
        """The queue's next row, as its worker takes it: the run's sites (and --only's rows) only, the host it is on
        first."""
        return self.frontier.next_for(self.frontier.hosts_of(queue), self.clock(), prefer_host=self._prefer.get(queue),
                                      sites=self.sites, ids=self.only)

    def _work(self, queue: str) -> None:
        sink = WarcSink(self.root / "warc", re.sub(r"[^\w.-]", "_", queue), self.run_id, _utc())
        client = self.client_factory(sink)
        idle_since, was_paused = None, False
        try:
            while not self.stop.is_set():
                now = self.clock()
                paused = self._paused(queue) or self.frontier.paused_until(queue) > now
                if paused and not was_paused:  # what this client counted during the outage is forgotten
                    for h in self.frontier.hosts_of(queue):
                        client.reset_host(h)
                was_paused = paused
                if not paused and self._step(self._campus_step, queue, client, sink):
                    continue
                self._dns_step(queue)
                # a chain's next press goes before the queue's url rows (design §6.7, §7.5 step 4); --only: a press of a
                # listed row only
                press = None if paused else self.chains.next_action(queue, self.clock(), self.sites, ids=self.only)
                if press is not None:
                    idle_since = None
                    self._prefer[queue] = press["host"]
                    self._press(queue, client, sink, press)
                    continue
                row = None if paused else self._next(queue)
                if row is None:
                    idle_since = idle_since or self.clock()
                    if self.clock() - idle_since > IDLE_EXIT_S:
                        return
                    due = None if paused else self.frontier.next_due(self.frontier.hosts_of(queue), ids=self.only)
                    self.stop.wait(min(IDLE_POLL_S, max(0.05, due - self.clock())) if due else IDLE_POLL_S)
                    continue
                idle_since = None
                self._prefer[queue] = row["host"]
                self._handle(queue, client, sink, row)
        finally:
            with suppress(Exception):
                client.close()
            with suppress(Exception):
                sink.close()

    def _step(self, step, queue: str, client: Client, sink: WarcSink) -> bool:
        """A step of a worker that is no row's (the campus check): what stops the run stops it."""
        try:
            return bool(step(queue, client, sink))
        except (_Stopped, _Offline, _Paused):
            return False
        except (ArchiveWriteError, _DiskLow):
            self.halt("warc_write")
            return False
        except schoollock.SchoolLockNotHeld as exc:
            self._no_school_lock(exc, None)
            return False

    def _no_school_lock(self, exc: Exception, row) -> None:
        """A request refused before it was sent because this process does not hold the machine-wide lock: a program's
        bug, never the page's (second review 10-26): the run stops, the row waits as it was."""
        self.frontier.event("error", "no_school_lock", f"{exc} — the run stops (nknu archive run takes the lock)"[:500],
                            row["host"] if row else None, row["url"] if row else None)
        self.halt("no_school_lock")

    def _handle(self, queue: str, client: Client, sink: WarcSink, row) -> None:
        """One row, whatever happens: the run stops for what no page can fix (archive, disk, database, the school
        lock). What happens is the row's the worker is handling at that moment: a scheme move hands it the row of the
        address in the other scheme (self._tl.row), whose crash is its own, and the row it came from stays moved
        (second review 10-26: it was left active, and the run never ended)."""
        self._tl.row = row["id"]
        self._worked_on(row["owner_site"])
        try:
            self._process(queue, client, sink, row)
            self._walk_check(self._tl.row)
        except (_Stopped, _Paused):
            self.frontier.finish(self._tl.row, state="pending", fetched_at=None)
        except _Offline:
            self.frontier.finish(self._tl.row, state="pending", reason="net_down", fetched_at=None)
            for h in self.frontier.hosts_of(queue):
                client.reset_host(h)
        except (ArchiveWriteError, _DiskLow) as exc:
            self.frontier.finish(self._tl.row, state="pending", fetched_at=None)
            if isinstance(exc, ArchiveWriteError):
                self.frontier.event("error", "warc_write", f"{exc} — the run stops; fix the archive directory, then "
                                    "nknu archive run again"[:500], row["host"], row["url"])
            self.halt("warc_write" if isinstance(exc, ArchiveWriteError) else "disk_low")
        except sqlite3.DatabaseError as exc:
            if db_busy(exc) and self._db_busy_row(queue, row, exc):
                return
            log.exception("queue %s: the frontier database failed, stopping the run", queue)
            self.halt("db_error")
        except schoollock.SchoolLockNotHeld as exc:
            self.frontier.finish(self._tl.row, state="pending", fetched_at=None)
            self._no_school_lock(exc, row)
        except Exception as exc:  # one bad page never stops the queue
            current = self.frontier.row_by_id(self._tl.row) or row
            log.exception("queue %s: %s", queue, current["url"])
            self.frontier.finish(current["id"], state="failed", reason=f"crash:{type(exc).__name__}")
            self.frontier.event("error", "crash", f"{type(exc).__name__}: {exc}"[:500], current["host"],
                                current["url"])

    def _walk_check(self, uid: int) -> None:
        """A row some GET-paged list's pager links to has its answer, or none for good (failed, held, skipped, a file
        where a page was expected): that list's walk again (postback.get_walk_for), so that it never stays walking
        or claims an end its last page did not show. A row still waiting changes nothing."""
        r = self.frontier.row_by_id(uid)
        if r is not None and r["state"] not in ("pending", "active"):
            self.chains.get_walk_for(uid)

    def _db_busy_row(self, queue: str, row, exc: sqlite3.DatabaseError) -> bool:
        """The row being handled met the frontier database held by another connection (DB_BUSY; the lab run of 11-07
        05:45 stopped on it, the database whole): it is pending again, fetched again DB_BUSY_RETRY_S later (what of its
        answer got written may be only part of it), no attempt used, and the run goes on (the owner 11-08: 「修好'数据库
        写入撞车'」). False when it cannot be put back either: the run stops, as for any other database error."""
        uid = self._tl.row
        try:  # a row already given its end keeps it (a Google Sites page's kept answer among them, §23.9 B2)
            back = self.frontier.x("UPDATE url SET state='pending', next_at=?, fetched_at=NULL WHERE id=? AND "
                                   "state='active'", (self.clock() + DB_BUSY_RETRY_S, uid))
        except sqlite3.Error:
            return False
        what = f"fetched again in {DB_BUSY_RETRY_S:.0f} s" if back else "already given its end, kept"
        log.warning("queue %s: the frontier database was busy (%s): row %s %s", queue, exc, uid, what)
        self._db_busy_event(f"row {uid}: {exc}; {what}, the run goes on", row["host"], row["url"])
        return True

    def _db_busy_press(self, a, exc: sqlite3.DatabaseError) -> bool:
        """A press that met the frontier database held by another connection (_db_busy_row): a read of a data address
        only reads, so it is pending again, DB_BUSY_RETRY_S later (as resume() puts one back after a stop); any other may
        have reached the server and is never sent again (§6.4): still active (no answer written), it failed as one that
        got no answer (postback.Chains.failed: its chain starts again from its GET once; a file button waits for `retry
        click`); answered, its chain starts again from its GET so that what the answer brings is found again (review
        11-08). False when that cannot be written either: the run stops, as for any other database error."""
        try:
            now = self.frontier.q("SELECT state FROM action WHERE id=?", (a["id"],))
            if a["kind"] == "data":  # whatever got written of its answer: read again (what it links, written again)
                self.frontier.x("UPDATE action SET state='pending', next_at=? WHERE id=?",
                                (self.clock() + DB_BUSY_RETRY_S, a["id"]))
                what = "read again later"
            elif now and now[0][0] == "active":
                self.chains.failed(a, DB_BUSY)
                what = "failed as one that got no answer"
            elif a["kind"] not in postback.SOLO_KINDS:  # answered: what its answer brings is found again from the GET
                self.chains.restart_chain(a["list_id"], f"{a['kind']} {a['row_key'][:12]}: {DB_BUSY} after its answer")
                what = "its answer kept, its chain starts again from its GET"
            else:
                what = "its answer kept (a file button: nknu archive retry click presses it again)"
        except sqlite3.Error:
            return False
        log.warning("press %s: the frontier database was busy (%s): %s", a["id"], exc, what)
        self._db_busy_event(f"press {a['id']} ({a['kind']}): {exc}; {what}, the run goes on", a["host"], a["target"])
        return True

    def _db_busy_event(self, what: str, host: str | None = None, url: str | None = None) -> None:
        with suppress(sqlite3.Error):  # the database may still be held: the log above has it
            self.frontier.event("warn", DB_BUSY, what[:500], host, url)

    # ---- a press (design §7.4 clicks, §7.5)
    def _press(self, queue: str, client: Client, sink: WarcSink, a) -> None:
        """One press of a chain or of a file button, whatever happens: a press that never went out (the run stops, the
        queue rests, the network is down) waits as pending; one that may have reached the server is never sent again
        (design §6.4: a POST is sent once). What stops the run stops it."""
        with suppress(KeyError, IndexError):  # action rows hold their site (postback.Chains.next_action: a.*)
            self._worked_on(a["site"])
        try:
            self._press_one(queue, client, sink, a)
        except (_Stopped, _Paused, _Offline) as exc:
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))
            if isinstance(exc, _Offline):
                for h in self.frontier.hosts_of(queue):
                    client.reset_host(h)
        except _DiskLow:
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))
            self.halt("disk_low")
        except ArchiveWriteError as exc:  # sent, its answer not kept: failed, never sent again
            self.chains.failed(a, "warc_write")
            self.frontier.event("error", "warc_write", f"{exc} — the run stops; fix the archive directory, then "
                                "nknu archive run again"[:500], a["host"], a["target"])
            self.halt("warc_write")
        except sqlite3.DatabaseError as exc:
            if db_busy(exc) and self._db_busy_press(a, exc):
                return
            log.exception("queue %s: the frontier database failed, stopping the run", queue)
            self.halt("db_error")
        except schoollock.SchoolLockNotHeld as exc:
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))
            self._no_school_lock(exc, None)
        except Exception as exc:  # one bad press never stops the queue
            log.exception("queue %s: press %s", queue, a["id"])
            self._press_dead(a, f"crash:{type(exc).__name__}")
            self.frontier.event("error", "crash", f"press {a['id']}: {type(exc).__name__}: {exc}"[:500], a["host"],
                                a["target"])

    def _press_dead(self, a, reason: str) -> None:
        """A press that came to nothing the chain can go on from, and that no automatic start again would mend (an
        answer too large to keep, a crash): failed; a next page's ends its walk as post_failed (`nknu archive retry
        post_failed` walks it again once a person looked). reason too_large is the result too_large (要人看: a person
        decides, design §23 A3), never 连接失败; a download that failed otherwise goes back with `retry download`."""
        self.frontier.x("UPDATE action SET state='failed', reason=?, done=? WHERE id=? AND state IN ('active',"
                        "'pending')", (reason, self.clock(), a["id"]))
        if a["kind"] == "pager":
            self.chains._walk_end(a, "post_failed", f"page {a['step']}: {reason}")

    def _press_one(self, queue: str, client: Client, sink: WarcSink, a) -> None:
        """Build the POST from the page it was planned on as archived (postback.target_for), send it once on the
        list's own server queue after the gap like any request, and hand the answer back (_press_answer)."""
        if self.disk_free(self.root) < MIN_FREE_BYTES:
            self.frontier.event("error", "disk_low", f"less than {MIN_FREE_BYTES // 1024**3} GB free under "
                                f"{self.root}: the run stops before the archive cannot be written", a["host"],
                                a["target"])
            raise _DiskLow
        list_row = self.frontier.row_by_id(a["list_id"])
        if list_row is None:
            self._press_dead(a, "no_list_row")
            return
        if a["kind"] == "data":  # a read of a platform's data address, no button's press (design §23 C1)
            self._data_press(queue, client, sink, a, list_row)
            return
        target = self.chains.target_for(a)
        if target is None:  # the press cannot be built from its page (mismatch, written by target_for)
            if a["kind"] == "pager":  # the walk cannot go on: never last_page
                reason = self.frontier.q("SELECT reason FROM action WHERE id=?", (a["id"],))[0][0]
                self.chains._walk_end(a, "mismatch:page", f"page {a['step']}: no press could be built ({reason})")
            return
        host = urlsplit(target.url).hostname or a["host"]
        got = self._request(queue, client, target, "postback", scope_reason=self._admitted_as(list_row))
        if isinstance(got, HostCircuitOpen):  # nothing was sent
            self._cool_host(host)
            client.reset_host(host)
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))
            return
        self._count()
        if isinstance(got, RetryLater):  # the server asked us to wait: it may have read the POST, never sent again
            self._rest_queue(queue, client, host, target.url, got)
            self.chains.failed(a, "retry_after")
            return
        if isinstance(got, TooLarge):  # answered, too large to keep: nobody presses it again by itself
            self._press_dead(a, "media_type" if isinstance(got, UnwantedType) else "too_large")
            return
        if isinstance(got, FetchError):
            kind = error_kind(got)
            if kind == NOT_ARCHIVED:  # the rehearsal: failed, never pressed again, no start again from the GET (§18.1)
                self._press_dead(a, kind)
                return
            if kind in CONN_KINDS:  # it never reached the server: the server's failure (B2), the press waits

                def back() -> None:
                    self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))

                self._conn_failed(queue, client, {"host": host, "id": None}, target.url, got, back=back,
                                  fail=lambda: self.chains.failed(a, kind))
                return
            self.chains.failed(a, kind)  # a timeout, a TLS failure, a 5xx: the chain starts again from its GET once
            return
        self._answered(host, target.url, got.status)
        self._press_answer(queue, client, a, list_row, got, sink)

    def _press_answer(self, queue: str, client: Client, a, list_row, got: Snapshot, sink) -> None:
        """An answer to a press (design §7.5 step 6). A page that answers a detail or a next page is read as the list
        page's (its links and parts, _parts with the list row) before postback.answered() checks it; a redirect's
        Location is the list page's resource (link table: [postback]); a file button's address is the attachment of
        every page that shows the button (§7.4); a download's export page names where its file is (_press_file)."""
        warc = self._warc_path(sink)
        list_id = a["list_id"]
        ctype = got.headers.get("content-type", "")
        if got.status == 200 and a["kind"] in ("pager", "detail") and sniff.sniff(got.body, ctype) == "html":
            text = got.text if got.text is not None else _lenient(got.body, ctype)
            page = extract(text, got.url, profiles.content_css(got.url))
            entries, _n = self._parts(list_row, page, list_id, html=text, warc=warc, warc_offset=got.warc_offset)
            self.frontier.add_page_res(list_id, entries)
            google_api.resync(self.frontier, list_id)
        out = self.chains.answered(a, got, warc)
        if out.file_get is not None:
            out = self._press_file(queue, client, sink, a, list_row, out.file_get)
            if out is None:
                return
        if out.location is not None:
            # a download's redirect is here only when it leads to its file (postback.file_redirect, gap ① of 11-08):
            # the page's attachment, wherever its address is (kind0 file)
            e = self._resource_of(out.location, list_row, "redirect", list_row, [list_id],
                                  kind0="file" if a["kind"] == "download" else None)
            self.frontier.link(list_id, out.location, "[postback]", e.verdict)
        for f in out.files:
            pages = [r[0] for r in self.frontier.q("SELECT DISTINCT page_id FROM page_res WHERE action_id=?",
                                                   (a["id"],))] or [list_id]
            e = self._resource_of(f, list_row, "click", list_row, pages, kind0="file")
            self.frontier.link(list_id, f, "[click]", e.verdict)
            if e.id is not None:
                for pid in pages:
                    self.frontier.add_page_res(pid, [{"res_key": f, "how": "get", "role": "file", "need": "must",
                                                      "res_id": e.id}])

    def _press_file(self, queue: str, client: Client, sink, a, list_row, url: str):
        """The GET a download press's export page named (postback.file_followup; pilot 10-26 F3: SSO's download
        buttons answer with the page again and an iframe at /WebServices/ajaxDownloadFile.aspx, which the session
        answers with the file once): the next request on the list's own queue, after the gap like any request (2–5 s,
        --gap, a longer Crawl-delay), on the connection that pressed (its cookies), and handed back as the press's
        answer (chains.file_answered: what is no file is not_file, a redirect a failure, design §23 A1, A2). What goes
        wrong is the press's: the same POST is never sent again as it was (§6.4), the chain starts again from its GET
        once (chains.failed) and the button is pressed again in that new walk; a server's refusal to connect pauses
        the server as any (B2). Returns the Followup, or None when there is nothing more to do."""
        host = urlsplit(url).hostname or a["host"]
        try:
            got = self._request(queue, client, Target(url), "postback", scope_reason=self._admitted_as(list_row))
        except (_Paused, _Offline, _DiskLow, schoollock.SchoolLockNotHeld):
            # the GET did not go out (a pause, the network down, the run halting): the press, sent and answered, may
            # not go back to pending, or next_action would send its POST again (§6.4; review of the pilot fixes). It
            # fails and the chain starts again from its GET once, as after any failure of this GET. A stop is the
            # next run's resume(): the chain is superseded there, nothing sent twice
            self.chains.failed(a, "file_get_interrupted")
            raise
        if isinstance(got, HostCircuitOpen):  # nothing was sent: the chain starts again once the breaker rests
            self._cool_host(host)
            client.reset_host(host)
            self.chains.failed(a, "breaker")
            return None
        self._count()
        if isinstance(got, RetryLater):  # the server asked us to wait: the chain starts again after the rest
            self._rest_queue(queue, client, host, url, got)
            self.chains.failed(a, "retry_after")
            return None
        if isinstance(got, TooLarge):
            self._press_dead(a, "media_type" if isinstance(got, UnwantedType) else "too_large")
            return None
        if isinstance(got, FetchError):
            kind = error_kind(got)
            if kind == NOT_ARCHIVED:  # the rehearsal: no answer will come (§18.1)
                self._press_dead(a, kind)
                return None
            if kind in CONN_KINDS:  # it never reached the server: the server's failure (B2); the chain starts again
                self._conn_failed(queue, client, {"host": host, "id": None}, url, got,
                                  back=lambda: self.chains.failed(a, kind), fail=lambda: self.chains.failed(a, kind))
                return None
            self.chains.failed(a, kind)
            return None
        self._answered(host, url, got.status)
        return self.chains.file_answered(a, got, self._warc_path(sink))

    # ---- a platform's data address, read once per site (design §23 C, 【用户同意 Claude 的建议】 10-27; how: 【Claude 的执行办法】)
    def _data_press(self, queue: str, client: Client, sink, a, list_row) -> None:
        """The read of a data address (design §23 C1): the POST the page's own script sends (profiles.DataApi: its
        address, headers and body as written there), on the site's server queue after the gap like any request, a
        request row (purpose data_api) and the WARC's request (its body too) and response. It only reads, so what
        goes wrong is read again with a new request, as a url row is (30 min × attempts, three in all; a server's
        refusal to connect or its Retry-After: no attempt used), never a chain started again; the same POST is no
        回传 to keep from sending twice. Its answer read (Chains.data_answered): an event's links are the site's home
        page's (the page that asked it first; _data_links)."""
        api = profiles.data_api((a["profile"] or "").partition("/")[2])
        if api is None:
            self._press_dead(a, "no_data_api")
            return
        target = Target(a["target"], "POST", body=(a["argument"] or "").encode("utf-8"), headers=dict(api.headers),
                        meta={"action": a["id"], "kind": "data", "list_id": a["list_id"]})
        host = urlsplit(target.url).hostname or a["host"]

        def back() -> None:
            self.frontier.x("UPDATE action SET state='pending' WHERE id=? AND state='active'", (a["id"],))

        got = self._request(queue, client, target, "data_api", scope_reason=self._admitted_as(list_row))
        if isinstance(got, HostCircuitOpen):  # nothing was sent
            self._cool_host(host)
            client.reset_host(host)
            back()
            return
        self._count()
        if isinstance(got, RetryLater):  # the server asked us to wait: no attempt used
            self._rest_queue(queue, client, host, target.url, got)
            back()
            return
        if isinstance(got, TooLarge):
            self._press_dead(a, "media_type" if isinstance(got, UnwantedType) else "too_large")
            return
        if isinstance(got, FetchError):
            kind = error_kind(got)
            if kind == NOT_ARCHIVED:  # the rehearsal: no answer will come (§18.1)
                self._press_dead(a, kind)
                return
            if kind in CONN_KINDS:  # it never reached the server (B2): the server pauses, no attempt used
                self._conn_failed(queue, client, {"host": host, "id": None}, target.url, got, back=back,
                                  fail=lambda: self._data_again(a, kind))
                return
            self._data_again(a, kind)
            return
        self._answered(host, target.url, got.status)
        warc = self._warc_path(sink)
        if got.status >= 500 or got.status in postback.FAIL_STATUSES:
            _ans, cols = postback.Chains._answer(got, warc)
            self._data_again(a, f"http_{got.status}", **cols)
            return
        read = self.chains.data_answered(a, got, warc)
        if read is not None and read.html:
            self._data_links(self._site_home(a["site"], list_row), read.html)

    def _data_again(self, a, error: str, **cols) -> None:
        attempts = (a["attempts"] or 0) + 1
        delay = RETRY_AFTER_S * attempts + self.rng.uniform(0, RETRY_JITTER_S)
        self.chains.data_retry(a, error, delay, MAX_ATTEMPTS, **cols)

    def _site_home(self, site: str, fallback):
        """A site's home page as stored (the site list's home, a web page fetched), else the given row (the page that
        asked first)."""
        s = self.plan.site(site) if site else None
        canon = scope.canonical(s.home) if s is not None and s.home else None
        home = self.frontier.row(canon) if canon else None
        return home if home is not None and home["kind"] == "page" and home["state"] == "done" else fallback

    def _data_links(self, page_row, html: str) -> None:
        """What a data address's events link (profiles.read_data_api wrote them out), as the links and parts of the
        page that asked it first (the site's home: its page_res and link table, judged by round1.admit as any of its
        links; design §23 C1, §4.4)."""
        page = extract(html, page_row["url"])
        entries, _n = self._parts(page_row, page, page_row["id"])
        self.frontier.add_page_res(page_row["id"], entries)
        google_api.resync(self.frontier, page_row["id"])

    def _data_entries(self, row, html: str, warc: str | None, warc_offset: int | None) -> list[dict]:
        """A round-1 page's data addresses its platform file reads (profiles.data_api_reads, design §23 C1): one
        page_res entry each, res_key data:<name>:<value>, role data, need must, pointing at the site's one read of it
        (Chains.data_action: planned by the first page that asks it). A page that asks it with no value to send:
        unhandled:data_api_value (要人看), nothing sent."""
        if row["tier"] not in round1.TIER_OF_IMP.values():
            return []
        out = []
        for api, value in profiles.data_api_reads(scope.site_key(row["url"]), html):  # the page's platform's
            key = profiles.data_api_key(api, value)
            if value is None:
                out.append({"res_key": key, "how": "unhandled", "role": "data", "need": "must",
                            "why": "unhandled:data_api_value"})
                continue
            aid = self.chains.data_action(row, api, value, warc, warc_offset)
            out.append({"res_key": key, "how": "data_api", "role": "data", "need": "must", "action_id": aid})
        return out

    def _resource_of(self, target: str, page_row, found_via: str, via, page_ids: list[int],
                     kind0: str | None = None) -> Enqueued:
        """An address a press led to, as the page's (design §7.5 step 6, §7.4): judged like a link of that page (a
        Drive or Docs address by the Google step, its entries on the pages; a short link asked where it leads);
        kind0 file: a file button's answer, the page's attachment unless a rule leaves the address out."""
        adm = self.admit(target, None, page_row, "link")
        if adm is None:
            return Enqueued("bad_url", None, None, None)
        if adm.step == "google" and self.google is not None:
            return self._google_users(adm, page_row, via, "link", found_via, page_ids)
        placement = adm.placement
        if kind0 == "file" and adm.step == "place" and placement.verdict in ("r1", "round2"):
            placement = self._transclude(adm.url, page_row, "file")  # the page's attachment, wherever it is
        return self._enqueue(target, placement=placement, url=adm.url, from_row=page_row,
                             depth=page_row["depth"] + 1, found_via=found_via, found_from=via["id"])

    # ---- the wait after each request (design §6.3)
    def _gap(self, host: str, took: float, url: str | None = None) -> None:
        """The run's gap after a response, on this queue, never cut short, and never less than what the answer took
        (at most SLOW_CAP_S); after a Drive download, DRIVE_MEDIA_GAP at least. A longer Crawl-delay of the host (at
        most 300 s) is that host's own wait: it may not be asked again before not_before, the other hosts on the
        server may."""
        ended = self.clock()
        low, high = self.gap
        if url is not None and host == google_api.API_HOST and google_api.is_download(url):
            low, high = max(low, DRIVE_MEDIA_GAP[0]), max(high, DRIVE_MEDIA_GAP[1])
        delay = max(self.rng.uniform(low, high), min(max(took, 0.0), SLOW_CAP_S))
        h = self.frontier.host(host)
        crawl_delay = min(float(h["crawl_delay"]), MAX_CRAWL_DELAY_S) if h is not None and h["crawl_delay"] else 0.0
        if crawl_delay > delay:
            self.frontier.set_host(host, not_before=ended + crawl_delay)
        self.sleep(delay)  # never cut short, not even by a stop: the next request on this server waits it out
        self._tl.gap, self._tl.ended = delay, ended

    def _not_due(self, host: str) -> bool:
        """The host is within its Crawl-delay since its last request."""
        h = self.frontier.host(host)
        return h is not None and (h["not_before"] or 0) > self.clock()

    def _crawl_delay(self, host: str) -> None:
        """Before any request: a host still within its Crawl-delay is waited for (at most MAX_CRAWL_DELAY_S), whatever
        sends it (second review 10-26: robots.txt's redirects and its other scheme, a page's other scheme went out
        after the gap only). A row's worker hands its row back at the three places a wait is foreseen (_not_due); this
        is for the requests one row makes in a row."""
        h = self.frontier.host(host)
        wait = (h["not_before"] or 0) - self.clock() if h is not None else 0.0
        if wait > 0:
            self.sleep(min(wait, MAX_CRAWL_DELAY_S))

    def _send(self, queue: str, client: Client, target: Target, purpose: str, pass_statuses: frozenset[int] | None,
              scope_reason: str | None, robots_sha: str | None, max_bytes: int = MAX_BYTES) -> Snapshot | FetchError:
        """One fetch. Whatever follows a request that may have reached the server (an answer, a failure, an
        exception of any kind), each request it put on the wire is logged and the full gap is waited."""
        host = urlsplit(target.url).hostname or ""
        if self.stop.is_set():
            raise _Stopped
        if self.frontier.paused_until(queue) > self.clock():  # a rest began while this row was being handled
            raise _Paused
        self._crawl_delay(host)
        started, status, error, sent, got = self.clock(), None, None, True, None
        try:
            got = client.fetch(target, NO_RATE, max_bytes=max_bytes, pass_statuses=pass_statuses)
            status = got.status
            return got
        except HostCircuitOpen as exc:  # refused before sending: nothing reached the server, nothing to wait for
            sent = False
            return exc
        except FetchError as exc:
            got, error = exc, str(exc)[:300]
            return exc
        except ArchiveWriteError as exc:
            got, status, error = exc, exc.status, f"warc_write: {exc}"[:300]
            raise
        except schoollock.SchoolLockNotHeld:  # refused before sending: a program's bug, never a request
            sent = False
            raise
        except BaseException as exc:
            error = f"crash:{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            if sent:
                self._came[queue] = self.clock()  # before the log and the gap: what the network said, and when
                try:
                    self._log(queue, host, target.url, purpose, started, got, status, error, scope_reason, robots_sha,
                              client)
                finally:
                    self._gap(host, self.clock() - started, url=target.url)

    def _log(self, queue: str, host: str, url: str, purpose: str, started: float, got, status: int | None,
             error: str | None, scope_reason: str | None, robots_sha: str | None, client) -> None:
        """One request row per request on the wire (B7: fetch.Sent, a resend after an idle close included); a client
        that says nothing of its wire (a test's) gets one row."""
        wire = getattr(got, "sent", ()) or ()
        sink = getattr(client, "warc", None)
        warc = self._warc_path(sink) if sink is not None else None
        if not wire:
            self.frontier.request(queue, host, url, purpose, started, self.clock(), status, error, scope=scope_reason,
                                  robots_sha=robots_sha, campus=self.campus)
        for s in wire:
            self.frontier.request(queue, host, url, purpose, s.started, s.ended, s.status, s.error,
                                  scope=scope_reason, robots_sha=robots_sha, attempt=s.attempt, method=s.method,
                                  bytes=s.bytes, new_conn=s.new_conn, connect_ms=s.connect_ms, tls_ms=s.tls_ms,
                                  ip=s.ip, warc=warc if s.warc_offset is not None else None,
                                  warc_offset=s.warc_offset, campus=self.campus)
        answered = status is not None or any(s.status is not None for s in wire)
        if answered and self.campus != "on" and host not in self._answered_off:
            self._answered_off.add(host)
            self.frontier.set_host(host, answered_off=1)

    def _warc_path(self, sink) -> str | None:
        try:
            return str(Path(sink.path).relative_to(self.root))
        except (ValueError, AttributeError, TypeError):
            return str(getattr(sink, "path", "")) or None

    def _request(self, queue: str, client: Client, target: Target, purpose: str,
                 pass_statuses: frozenset[int] | None = None, scope_reason: str | None = None,
                 robots_sha: str | None = None, max_bytes: int = MAX_BYTES,
                 spare: bool = False) -> Snapshot | FetchError:
        """One request and what its answer says of its server. spare: the address only an old source names, whose 500
        (OLD_GONE) is its own end (_gone): neither the server's error streak nor the client's breaker counts it (the
        owner 11-08), while its server gave no more than SPARE_RUN 5xx in a row with no other answer between; a 502,
        503 or 504 there, or a 500 past that, is the server's as any other."""
        got = self._send(queue, client, target, purpose, pass_statuses, scope_reason, robots_sha, max_bytes)
        came = self._came.pop(queue, None)
        if isinstance(got, HostCircuitOpen):
            return got
        host = urlsplit(target.url).hostname or ""
        kind = error_kind(got) if isinstance(got, FetchError) else None
        if kind == NOT_ARCHIVED:  # the rehearsal's old archive holds no answer: nothing of the server or the network
            return got
        if kind is not None:
            self._last_kind[host] = kind
        answer = kind is None or kind.startswith("http_") or kind == "retry_after"  # a 5xx too: it is there
        if answer:
            self._server_ok(queue)
            self._door_answered(queue, target.url)
        if kind is not None and kind.startswith("http_5"):
            self._fives[queue] = self._fives.get(queue, 0) + 1
        elif answer:
            self._fives[queue] = 0
        if self._gone(queue, spare, kind):  # _process_row fails it at once
            client.reset_host(host)  # the client's breaker (three in a row on one name) forgets it
        elif _erred(kind):
            self._server_erred(queue, client, host, target.url, kind)
        elif kind is None:
            self.frontier.server_err_clear(queue)
        if kind not in NET_KINDS:
            if self.net.answered(queue, http=answer, at=came):  # something on that network answered
                self._school_answered(host)
            return got
        networks, ours = self.net.down_after(queue, at=came)
        if networks:
            self._go_offline(host, networks, ours, queue)
            raise _Offline
        return got

    def _gone(self, queue: str, spare: bool, kind: str | None) -> bool:
        """An address only an old source names (spare) whose 500 is its own end (OLD_GONE): while its server gave no
        more than SPARE_RUN 5xx in a row with no other answer between (_request counts them)."""
        return spare and kind == OLD_GONE and self._fives.get(queue, 0) <= SPARE_RUN

    def _ever_answered(self, queue: str) -> bool:
        """Whether this server queue ever answered us (a request row with an HTTP status, this run's or an earlier
        one's): a school server that never did is no sign of its network going down (_NetWatch)."""
        return bool(self.frontier.q("SELECT 1 FROM request WHERE queue=? AND status IS NOT NULL LIMIT 1", (queue,)))

    def _server_ok(self, queue: str) -> None:
        self._down_warned.discard(queue)
        if fails := self.frontier.server_fails(queue):
            if fails >= CONN_GIVE_UP:  # given up (_server_down): what it failed is due again
                self._server_back(queue)
            self.frontier.server_answered(queue, self.clock())

    def _server_back(self, queue: str) -> None:
        """A server _server_down gave up on answered: its rows failed as SERVER_DOWN are due again (review 11-08: a
        server that only seemed dead must not keep them failed). A run given --only: its listed rows only."""
        row_id, row_args = self._ids("id")
        n = 0
        for host in self.frontier.hosts_of(queue):
            n += self.frontier.x(f"UPDATE url SET state='pending', reason=NULL, next_at=0 WHERE host=? AND "
                                 f"state='failed' AND reason=?{row_id}", (host, SERVER_DOWN, *row_args))
        if n:
            self.frontier.event("info", "server_back", f"server queue {queue} answered again: {n} URLs it failed as "
                                f"{SERVER_DOWN} are due again")

    def _go_offline(self, host: str, networks: frozenset[str], ours: bool, queue: str | None = None) -> None:
        """Pause the networks that do not answer (every queue when none does) and undo what their connection errors
        of the last minutes cost. ours: the connectivity check failed (5 min, then checked again); else the school's
        network is down while ours works (B2: 15 min, then its servers are asked again). A school pause gives the
        servers back their counts, not the rests of their own failures, and the server whose failure (queue) told it
        rests too: a school answer may still lift it, and none of them is asked again before its rest is over (second
        review 11-06, R3)."""
        now = self.clock()
        every = networks >= frozenset(NETWORKS)
        keys = [ALL_QUEUES] if every else [net_key(n) for n in sorted(networks)]
        first = all(self.frontier.paused_until(k) <= now for k in keys)
        wait = NET_PAUSE_S if ours else CONN_PAUSE_S
        what = "every queue" if every else " and ".join(
            {"school": "the school network (140.127.*)", "other": "the other (off-campus) network"}[n]
            for n in sorted(networks)) + "'s queues"
        for key in keys:
            self.frontier.pause(key, now + wait, "the network is down" if every else
                                "this network does not answer and the connectivity check failed" if ours else
                                "the school's servers do not connect while the internet answers")
        undone = self._undo(self.net.take_charges(networks, NET_UNDO_S if ours else SIGN_KEEP_S), keep_rests=not ours)
        if not ours and queue is not None:
            self.frontier.pause(queue, now + CONN_PAUSE_S, f"{host}: no connection")
        if not ours and not self.net.still_told():  # a school answer came while the pause was written: lifted
            self.frontier.unpause(net_key("school"))
        if first:
            why = ("the network is down" if ours else
                   "school servers that were working do not connect while the internet works: the school's network "
                   "is down")
            self.frontier.event("warn", "offline", f"{why}: {what} pause {wait / 60:.0f} min; "
                                f"{undone} connection failures are not held against URLs, hosts or servers", host)

    def _school_answered(self, host: str) -> None:
        """A school server answered while the school's pause held (its request was on the wire when it began): the
        school's network works, so the pause is lifted, and the servers that told it failed on their own
        (_NetWatch.refuted: each counts its failures, no sign until it answers)."""
        self.frontier.unpause(net_key("school"))
        self.frontier.event("info", "online", f"a school server ({host}) answered while the school network's pause "
                            "held: the school's network works, its queues resume, and the servers that told the pause "
                            "count their own connection failures", host)

    def _undo(self, charges: list[tuple], keep_rests: bool = False) -> int:
        """Take back what connection errors cost while the network was in fact down (latest first, so each row ends
        as it was before the outage). A run given --only puts back its listed rows only (it failed no other).
        keep_rests: the servers' counts only, each keeps the rest its failure began (a school pause)."""
        row_id, row_args = self._ids("id")
        for kind, *args in reversed(charges):
            if kind == "url":
                uid, attempts = args
                self.frontier.x("UPDATE url SET state='pending', attempts=?, next_at=0, reason='net_down' "
                                "WHERE id=? AND state IN ('pending','failed')", (attempts, uid))
            elif kind == "host":
                host, before, mass = args
                self.frontier.set_host(host, **before)
                if mass:
                    self.frontier.x("UPDATE url SET state='pending', reason='net_down' WHERE host=? AND state='failed' "
                                    f"AND reason='host_unreachable'{row_id}", (host, *row_args))
            elif kind == "cool":
                host, until, seconds = args
                self.frontier.set_host(host, cooldown_until=until, cooldown_s=seconds)
            elif kind == "conn":
                queue, fails, until = args
                self.frontier.server_set(queue, fails)
                if keep_rests:
                    continue
                if until > 0:
                    self.frontier.x("UPDATE pause SET until=? WHERE queue=?", (until, queue))
                else:
                    self.frontier.unpause(queue)
        return len(charges)

    def _cool_host(self, host: str) -> float:
        """The client's breaker (three failures in a row on this name): the name cools down a fixed 15 min (second
        review 10-26: it doubled up to a day). The whole server's rest for its errors is _server_erred's."""
        wait = COOLDOWN[0]
        self.frontier.set_host(host, cooldown_until=self.clock() + wait, cooldown_s=wait)
        return wait

    def _server_erred(self, queue: str, client: Client, host: str, url: str, kind: str) -> None:
        """The server erred (_erred): one more in a row, whichever of its names. A 503 (「太忙」) rests the whole server
        queue at once, BREAKER_THRESHOLD in a row rest it too: a fixed SERVER_REST_S, never doubled (second review
        10-26: CLAUDE.md 「出错或回『太忙』整台休息」, design §6.3 「整台服务器先休息」, record 一·3 「15 分钟以上」).
        The request that erred costs its URL an attempt as before (_failed); the rows the rest sends back cost
        nothing."""
        n = self.frontier.server_erred(queue)
        if kind not in REST_NOW and n < BREAKER_THRESHOLD:
            return
        until = self.clock() + SERVER_REST_S
        self.frontier.pause(queue, until, f"{host}: {kind}" + ("" if kind in REST_NOW else f" ({n} errors in a row)"))
        self.frontier.server_err_clear(queue)
        for h in self.frontier.hosts_of(queue):
            client.reset_host(h)
        why = "said it is too busy (503)" if kind in REST_NOW else f"erred {n} times in a row (last: {kind})"
        self.frontier.event("warn", "server_rest", f"server queue {queue} {why}: it rests {SERVER_REST_S / 60:.0f} min, "
                            "every name on it (no URL loses an attempt for the rest)", host, url)

    # ---- doors: a server that answers on one scheme or port and refuses another is up
    def _door_answered(self, queue: str, url: str) -> None:
        with self._door_lock:
            self._doors.setdefault(queue, {})[_door(url)] = self.clock()

    def _closed_door(self, queue: str, url: str) -> bool:
        """Whether a connection refused at this address's door is that door's (closed: a port or a scheme the server
        does not serve) rather than the server's: its other doors answered within DOOR_WINDOW_S, this one did not."""
        now, door = self.clock(), _door(url)
        with self._door_lock:
            doors = dict(self._doors.get(queue, {}))
        if now - doors.get(door, -math.inf) <= DOOR_WINDOW_S:
            return False
        return any(now - t <= DOOR_WINDOW_S for d, t in doors.items() if d != door)

    def _rest_queue(self, queue: str, client: Client, host: str, url: str, exc: RetryLater) -> float:
        """The server asked us to come back later: every host on it waits that long (at least 15 min, at most a day)."""
        wait = min(max(exc.seconds, COOLDOWN[0]), COOLDOWN[1])
        until = self.clock() + wait
        self.frontier.pause(queue, until, f"Retry-After {exc.seconds:.0f} s from {host}")
        client.reset_host(host)  # the wait is kept here, in the frontier, not in the client's "skip this run"
        self.frontier.event("warn", "retry_after", f"{host} asked for {exc.seconds:.0f} s (Retry-After): server queue "
                            f"{queue} rests {wait / 60:.0f} min", host, url)
        return until

    # ---- a server that does not connect (B2)
    def _conn_failed(self, queue: str, client: Client, row, url: str, exc: FetchError | None, *,
                     back: Callable[[], None] | None = None, fail: Callable[[], None] | None = None) -> None:
        """No connection at this address. A door (scheme, port) of a server that answers on its other doors is closed:
        the URL's failure (_failed: an attempt, retried later), no server pause (second review 10-26: else one such
        address would pause its whole server each time it came up, for ever). Otherwise the server does not connect:
        its whole queue rests CONN_PAUSE_S (fixed) and the row waits, no URL ever losing an attempt for it (B2; second
        review: it used to cost every row of the server one attempt every CONN_GIVE_UP failures). The failure counts
        against the server only while its network is known to work: not during a pause of that network, nor after
        the school's pause until one of its servers answered (_NetWatch.unsure; review 11-06: a server already failing
        on its own then counted, and a long outage undid its count at every school pause, warning server_down every 15
        minutes). At CONN_GIVE_UP in a row, _server_down. back, fail: what a press does instead of a row (a POST that
        never reached the server waits; one at a closed door fails, never sent again)."""
        host = row["host"]
        if self._closed_door(queue, url):
            if fail is not None:
                fail()
            else:
                self._failed(queue, row, exc, kind="connect")
            return
        now = self.clock()
        before, before_pause = self.frontier.server_fails(queue), self.frontier.pause_of(queue)
        net = network_of(queue)
        unsure = (self.net.on_probation(queue)
                  or max(self.frontier.pause_of(net_key(net)), self.frontier.pause_of(ALL_QUEUES)) > now)
        n = before if unsure else self.frontier.server_failed(queue, now)
        self.frontier.pause(queue, now + CONN_PAUSE_S, f"{host}: no connection" + ("" if unsure else
                                                                                   f" ({n} in a row)"))
        self.net.charge(queue, "conn", queue, before, before_pause)
        for h in self.frontier.hosts_of(queue):
            client.reset_host(h)
        if back is not None:
            back()
        else:
            self._back(row["id"], "conn_pause")
        if n == 1 and not unsure:
            self.frontier.event("warn", "conn_pause", f"{host} does not connect: server queue {queue} rests "
                                f"{CONN_PAUSE_S / 60:.0f} min (no URL loses an attempt)", host, url)
        if n >= CONN_GIVE_UP and not unsure:
            self._server_down(queue, n)

    def _lab_may_reach(self, host: str, h: Mapping[str, Any] | None) -> bool:
        """A school host this run could not reach that the school's network alone may reach (host_unreachable: the lab
        machine tries again, record 三·19): one that never answered us off campus, while this run is not itself on
        campus. On the lab machine (campus on; the first batch runs there, owner 11-04) such a host is down like any
        other: handing it to the lab would leave its rows waiting for a machine that already tried them. A run with a
        campus check hands nothing over while the check has not told where this machine is (unknown; the deploy check
        11-06, 【Claude 的执行办法】: the lab's ledger, written by the 11-04 code, holds no campus:last, and in Docker a
        container's address, local_address, is new at each start, so the lab's restarts start unknown, and a long-dead
        school server that failed before SSO's check came back handed its rows to the lab machine): its rows are
        treated as on campus, waiting with every attempt at a server's connection failures (_server_down, B2) and
        losing one when robots.txt stays unreachable for hours (_give_up_hosts), and go to the lab at a later failure
        once the check found this machine off campus."""
        if self.campus == "on" or (self.campus == "unknown" and self.campus_url):
            return False
        return schoollock.is_school_host(host) and not (h is not None and h["answered_off"])

    def _give_up_hosts(self, hosts: list[str], kind: str) -> tuple[int, int]:
        """Hosts that did not answer for hours: a school host that never answered us off campus may be one only the
        school's network reaches (host_unreachable: the lab machine tries again, record 三·19); any other is down for
        now (B3), and each of its rows loses one attempt (reason kind), three in all. A run given --only: its listed
        rows only (a later run that meets the host so judges the others). Returns (lab, charged)."""
        now, lab, charged = self.clock(), 0, 0
        row_id, row_args = self._ids("id")
        for host in hosts:
            h = self.frontier.host(host)
            if self._lab_may_reach(host, h):
                lab += self.frontier.x("UPDATE url SET state='failed', reason='host_unreachable' WHERE host=? AND "
                                       f"state IN ('pending','active'){row_id}", (host, *row_args))
                continue
            for r in self.frontier.q("SELECT id, attempts FROM url WHERE host=? AND state IN ('pending','active')"
                                     f"{row_id}", (host, *row_args)):
                attempts = r["attempts"] + 1
                if attempts < MAX_ATTEMPTS:
                    self.frontier.finish(r["id"], state="pending", attempts=attempts, reason=kind, fetched_at=None,
                                         next_at=now + RETRY_AFTER_S * attempts + self.rng.uniform(0, RETRY_JITTER_S))
                else:
                    self.frontier.finish(r["id"], state="failed", attempts=attempts, reason=kind)
                charged += 1
        return lab, charged

    def _server_down(self, queue: str, n: int) -> None:
        """A server that did not connect n ≥ CONN_GIVE_UP times in a row (about two hours of pauses): a school host on
        it that never answered us off campus may be one only the school's network reaches (host_unreachable: the lab
        machine tries again, record 三·19); every other row of it still waiting fails, reason SERVER_DOWN, no attempt
        charged, and a row found on it later fails at its next failure (the owner 11-08: 「连不上的死服务器……直接标成连不上
        /跳过，不用再傻等了」; until then B2 of the second review 10-26 kept them waiting with every attempt, so the run
        never ended). `nknu archive retry server_down` puts them back. One warning when the streak reaches CONN_GIVE_UP
        (the dashboard shows it red), once until the server answers (second review 11-06: a school pause that gave a
        failure back made it reach CONN_GIVE_UP twice); the streak goes on counting until the server answers. A run
        given --only fails its listed rows only (the streak stays the server's: a later run that meets it fails the
        others)."""
        lab = failed = 0
        row_id, row_args = self._ids("id")
        # a run whose campus check has not told yet where this machine is (each lab restart: unknown till SSO's check
        # answers) fails nothing: on campus its rows fail at the next failure after it, off campus they go to the lab
        unsure = self.campus == "unknown" and bool(self.campus_url)
        # only a server that never answered this archive is dead (the owner's 168 URLs, 11-08); one that answered
        # before is down for now (maintenance, a firewall): its rows keep waiting, every attempt kept (B2), for a day
        # (CONN_GIVE_UP_ANSWERED; review 11-08)
        dead = n >= CONN_GIVE_UP_ANSWERED or not self._ever_answered(queue)
        for host in self.frontier.hosts_of(queue):
            h = self.frontier.host(host)
            if self._lab_may_reach(host, h):
                reason = "host_unreachable"
            elif unsure or not dead:
                continue
            else:
                reason = SERVER_DOWN
            k = self.frontier.x(f"UPDATE url SET state='failed', reason=? WHERE host=? AND state IN ('pending','active')"
                                f"{row_id}", (reason, host, *row_args))
            if reason == SERVER_DOWN:
                failed += k
            else:
                lab += k
        if lab:
            self.frontier.event("warn", "host_unreachable", f"server queue {queue} did not connect {n} times in a row: "
                                f"{lab} URLs of school hosts that never answered off campus wait for the lab machine "
                                "(nknu archive retry host_unreachable)")
        if n == CONN_GIVE_UP and queue not in self._down_warned:
            self._down_warned.add(queue)
            if not dead:
                what = ("it answered before, so its rows keep waiting, every one with all its attempts (B2), until "
                        f"{CONN_GIVE_UP_ANSWERED} in a row (about a day); a person should look whether it is down for good")
            elif unsure:
                what = (f"it never answered: its rows wait until the campus check tells where this machine is, then "
                        f"fail ({SERVER_DOWN})")
            else:
                what = (f"it never answered: {failed} URLs failed ({SERVER_DOWN}, no attempt charged), and a later one "
                        f"fails at its next failure; they are due again if it answers (nknu archive retry "
                        f"{SERVER_DOWN} puts them back too)")
            self.frontier.event("warn", "server_down", f"server queue {queue} did not connect {n} times in a row "
                                f"(about {n * CONN_PAUSE_S / 3600:.0f} h): {what}")
        elif failed:
            log.info("server queue %s, down (%d in a row): %d more URLs failed (%s)", queue, n, failed, SERVER_DOWN)

    # ---- robots.txt and the probe
    def _robots(self, queue: str, client: Client, row, admitted: str) -> Robots:
        """The host's robots.txt (design §6.8), read again after 24 h: never a block. Rules already known are never
        overwritten by a 5xx or by no answer (B3); a connection that cannot be made is the server's (Robots('conn')).
        Its requests say which row they were made for (admitted: the row's placement, _admitted_as), so that the scope
        audit finds robots.txt of a form's or a Drive file's host allowed (second review 10-26). When its scheme
        refuses, the other scheme's robots.txt is read instead; that teaches nothing of the host's scheme (a 404 or a
        redirect there is no proof that the pages answer on it: second review, an expired certificate whose http only
        sends the visitor back); the page's own try on the other scheme does (_switch_scheme)."""
        url = row["url"]
        parts = urlsplit(url)
        host = parts.hostname or ""
        h = self.frontier.host(host)
        age = self.clock() - ((h["robots_at"] or 0) if h is not None else 0)
        if h is not None and h["robots_state"] in ("rules", "none") and age < FRESH_S:
            return Robots(h["robots_state"], h["robots_body"] or "")
        self._tl.ended = None
        answer, kind = self._read_robots(queue, client, host, f"{parts.scheme}://{parts.netloc}/robots.txt", admitted)
        if answer.state == "unreachable" and kind in SWITCH_KINDS and parts.port is None:
            # an http link to an https-only host (or the other way round) must not cost the host its robots.txt
            other = "https" if parts.scheme == "http" else "http"
            alt, alt_kind = self._read_robots(queue, client, host, f"{other}://{parts.netloc}/robots.txt", admitted)
            if alt.state != "unreachable" or alt_kind is None or alt_kind.startswith("http_"):
                answer, kind = alt, alt_kind
        if answer.state == "wait":
            return answer
        if answer.state == "unreachable" and kind == NOT_ARCHIVED:
            # the rehearsal's old archive has no robots.txt of this host (design §18.1): what was known stays (the seed
            # brought the old rules), else none; not asked again for FRESH_S, and no host waits or fails for it
            known = h is not None and h["robots_state"] in ("rules", "none")
            self.frontier.set_host(host, robots_state=h["robots_state"] if known else "none", robots_at=self.clock(),
                                   robots_fail=0)
            h = self.frontier.host(host)
            return Robots(h["robots_state"], h["robots_body"] or "")
        if answer.state == "unreachable" and (kind is None or kind.startswith("http_")):
            # a 5xx: an answer with no rules this time; what was known stays (B3), and it is asked again in an hour
            self.frontier.set_host(host, robots_state=(h["robots_state"] if h is not None and h["robots_state"]
                                                       in ("rules", "none") else "none"),
                                   robots_at=self.clock() - FRESH_S + RETRY_UNREACHABLE_S, robots_fail=0)
            self.frontier.event("warn", "robots_5xx", f"robots.txt answered {kind or 'a 5xx'}: read as no new rules; "
                                "what was known stays", host, url)
            h = self.frontier.host(host)
            return Robots(h["robots_state"], h["robots_body"] or "")
        if answer.state == "unreachable" and kind in CONN_KINDS:
            return Robots("conn")
        if answer.state == "unreachable" and kind == "tls_cert":
            # the certificate failed its check (expired, set up wrong, an intermediate missing: design §23 F5): no
            # robots.txt can be read on that door, and it says nothing of the host being down. What was known stays,
            # it is read again in an hour, the host waits for nothing; each URL meets the same check on its own and
            # ends as tls_cert by the TLS rules (§6.6, §6.10), never as robots_unreachable
            known = h is not None and h["robots_state"] in ("rules", "none")
            self.frontier.set_host(host, robots_state=h["robots_state"] if known else "none",
                                   robots_at=self.clock() - FRESH_S + RETRY_UNREACHABLE_S, robots_fail=0)
            self.frontier.event("warn", "robots_tls_cert", "robots.txt: the certificate failed its check (tls_cert); "
                                "read as no new rules, read again in an hour; the URLs go on and fail on their own",
                                host, url)
            h = self.frontier.host(host)
            return Robots(h["robots_state"], h["robots_body"] or "")
        if answer.state == "unreachable" and h is not None and h["robots_state"] in ("rules", "none"):
            # keep the last good copy, try again in an hour
            self.frontier.set_host(host, robots_at=self.clock() - FRESH_S + RETRY_UNREACHABLE_S)
            return Robots(h["robots_state"], h["robots_body"] or "")
        before = {k: h[k] for k in ("robots_state", "robots_body", "robots_sha", "robots_at", "crawl_delay",
                                    "robots_fail", "cooldown_until", "not_before")} if h is not None else None
        if answer.state == "unreachable":
            fails = (h["robots_fail"] or 0) + 1 if h is not None else 1
            self.frontier.set_host(host, robots_at=self.clock(), robots_fail=fails)  # rules, body, delay: untouched
            mass = False
            if fails >= UNREACHABLE_TRIES:
                lab, charged = self._give_up_hosts([host], kind or "fetch")
                self.frontier.set_host(host, robots_fail=0 if not lab else fails)
                mass = bool(lab)
                self.frontier.event("warn", "host_unreachable", f"robots.txt unreachable {fails} times in a row: "
                                    f"{lab} URLs wait for a run from the lab machine (nknu archive retry "
                                    f"host_unreachable), {charged} lose an attempt (down for now)", host, url)
            else:
                self.frontier.event("warn", "robots_unreachable", "robots.txt unreachable: host waits an hour", host,
                                    url)
            if kind in NET_KINDS and before is not None:
                self.net.charge(queue, "host", host, before, mass)
            return Robots("unreachable")
        if answer.state == "rules":
            self.frontier.robots_version(answer.sha, host, answer.body, self.clock())
        delay, gap, ended = answer.crawl_delay(), getattr(self._tl, "gap", 0.0), getattr(self._tl, "ended", None)
        # the robots.txt just read rules the wait after it: the request after it is the host's next one
        not_before = ended + delay if delay and ended is not None and delay > gap else 0.0
        self.frontier.set_host(host, robots_state=answer.state, robots_body=answer.body[:MAX_BODY],
                               robots_sha=answer.sha, robots_at=self.clock(), crawl_delay=delay, robots_fail=0,
                               not_before=not_before)
        for sm in answer.sitemaps():  # design §5 ⑥: the sitemaps it names are read for their addresses
            self._enqueue(sm, from_row=row, depth=row["depth"], found_via="link", prio=PRIO_LIST, kind0="sitemap")
        return answer

    def _learn_scheme(self, host: str, scheme: str) -> None:
        h = self.frontier.host(host)
        if h is None or h["scheme_only"] != scheme:
            self.frontier.set_host(host, scheme_only=scheme)
            self.frontier.event("info", "scheme_switch", f"answers on {scheme} only: its addresses are written so",
                                host)

    def _unlearn_scheme(self, host: str, sent_to: str) -> None:
        """The host was taken to answer on one scheme only, and a page there sends the visitor to the other (sent_to):
        it is not so. Its addresses are written as they come again, and the row of sent_to, if it was moved, is
        fetched again (second review 10-26: else the two rows point at each other, and the content is never read)."""
        self.frontier.set_host(host, scheme_only=None)
        self.frontier.x("UPDATE url SET state='pending', reason=NULL, final_url=NULL, next_at=0 WHERE url=? "
                        "AND state='skipped' AND reason='moved_scheme'", (sent_to,))
        self.frontier.event("warn", "scheme_switch", f"a page sends the visitor to {urlsplit(sent_to).scheme}: the host "
                            "is no longer taken to answer on one scheme only", host, sent_to)

    def _read_robots(self, queue: str, client: Client, host: str, target: str,
                     admitted: str | None = None) -> tuple[Robots, str | None]:
        """One host's robots.txt, following redirects that stay on this queue: (answer, failure kind)."""
        for hop in range(ROBOTS_HOPS + 1):
            got = self._request(queue, client, Target(target), "robots", pass_statuses=ROBOTS_4XX,
                                scope_reason=admitted)
            if isinstance(got, RetryLater):
                self._rest_queue(queue, client, host, target, got)
                return Robots("wait"), None
            if isinstance(got, HostCircuitOpen):  # nothing was sent: not an unreachable robots.txt
                client.reset_host(host)
                self._cool_host(host)
                return Robots("wait"), None
            if isinstance(got, FetchError):
                return Robots("unreachable"), error_kind(got)
            if got.status not in REDIRECTS:
                text = decode(got.body) if 200 <= got.status < 300 else None
                answer = judge_answer(got.status, text)
                if answer.state == "rules" and unparsed(text):
                    self.frontier.event("warn", "robots_unparsed", "robots.txt answered with no rule in it (an HTML "
                                        "page?): crawled as if there were none", host, target)
                return answer, (f"http_{got.status}" if got.status >= 500 else None)
            nxt = scope.canonical(got.headers.get("location", ""), target)
            why = None
            if hop >= ROBOTS_HOPS:
                why = f"more than {ROBOTS_HOPS} redirects"
            elif nxt is None:
                why = "a redirect without a usable Location"
            elif scope.judge(nxt).reason in scope.HARD_OUT:
                why = f"a redirect to {nxt} ({scope.judge(nxt).reason})"
            elif self.queue_of(urlsplit(nxt).hostname or "") != queue:
                why = f"a redirect to another server ({nxt})"
            if why:
                self.frontier.event("warn", "robots_redirect", f"robots.txt: {why}; read as no robots.txt (RFC 9309)",
                                    host, target)
                return Robots("none"), None
            target = nxt
        return Robots("none"), None

    def _probe(self, queue: str, client: Client, url: str, robots: Robots,
               scope_reason: str) -> float | FetchError | None:
        """Once per page host: what a page that does not exist looks like (status, digest, length, title). Returns
        None to go on with the page; the time the server queue rests until (Retry-After); or the failure, when the
        probe reached no server (a connection, a TLS handshake or a name that failed: the page would meet the same
        door, so it is not sent; the probe is tried again with a later page)."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        h = self.frontier.host(host)
        if h is not None and h["probe_status"] is not None:
            return None
        target = f"{parts.scheme}://{parts.netloc}{PROBE_PREFIX}{secrets.token_hex(6)}.html"
        got = self._request(queue, client, Target(target), "probe", scope_reason=scope_reason, robots_sha=robots.sha)
        if isinstance(got, HostCircuitOpen):
            return None  # nothing was sent; the page request meets the same breaker
        if isinstance(got, RetryLater):
            return self._rest_queue(queue, client, host, target, got)
        if isinstance(got, FetchError):
            if error_kind(got) in SWITCH_KINDS | {"dns"}:  # the server is not reachable at this door
                return got
            self.frontier.set_host(host, probe_status=0)  # a slow or dropped missing page says nothing about the rest
            return None
        try:
            text = got.text if got.text is not None else _lenient(got.body, got.headers.get("content-type", ""))
            title = extract(text, got.url).title if text else ""
        except Exception:  # noqa: BLE001 - the probe's answer is kept whatever its title extraction makes of it
            title = ""
        self.frontier.set_host(host, probe_status=got.status, probe_sha=got.sha256, probe_len=len(got.body),
                               probe_title=title)
        if got.status == 200:
            self.frontier.event("info", "soft404_host", "a missing page answers 200 here: matching pages get flagged",
                                host, target)
        return None

    def _admitted_as(self, row) -> str:
        """What let a row in, for its requests' request.scope (B7; second review 10-26: it said admit:<kind0> for
        every row, so the audit could tell nothing): admit:<verdict>:<owner site>:<kind0>, verdict r1 (a page of its
        own round-1 site inside the list's paths for it, as round1.verdict() places one, or an extra page), resource
        (a part of a round-1 page, on its own site's host outside those paths too, or a page of a site a seed moved
        to), or round2 (a round-two row `retry` brought back: its reason says why). The 10-26 rehearsal: ksped's
        pictures and attachments under /uploadfile/ (its list entry is /resource/ only) said r1, and the scope audit
        (monitor._placement_breach) read 13 requests as out of bounds. Its review (清单①): on its own site's host
        outside the paths, only a part that is no page, or a page some page uses (a frame: _used_by_a_page), is a
        resource; any other page there says r1, so that the audit reads it against the list and sees a page a bug let
        in, never a resource that proves itself 【Claude 的执行办法】."""
        kind0 = row["kind0"] or row["kind"]
        owner = row["owner_site"] or "-"
        own = self.plan.site(owner) if owner == scope.site_key(row["url"]) else None
        if row["tier"] not in round1.TIER_OF_IMP.values():
            verdict = "round2"
        elif self.plan.is_extra(row["url"]) or (own is not None and own.round == "1" and own.holds(row["url"])):
            verdict = "r1"
        elif own is not None and kind0 == "page" and not self._used_by_a_page(row):
            verdict = "r1"  # a page of its own site's host outside the list's paths that no page uses: a breach
        else:
            verdict = "resource"
        return f"admit:{verdict}:{owner}:{kind0}"

    def _used_by_a_page(self, row) -> bool:
        """Whether a page uses this row as its part (a page_res entry points at it), or uses the row it was found from
        when that is no page (a short link, a redirecting part: what it leads to stands for it, _users_of). A row found
        on a page as a link is not used by it."""
        cur, seen = row, {row["id"]}
        for _ in range(outcome.MAX_HOPS):
            if self.frontier.q("SELECT 1 FROM page_res WHERE res_id=? LIMIT 1", (cur["id"],)):
                return True
            up = cur["found_from"]
            if up is None or up in seen or cur["found_via"] not in ("redirect", "js_redirect"):
                return False
            seen.add(up)
            parent = self.frontier.row_by_id(up)
            if parent is None or parent["kind"] == "page":
                return False
            cur = parent
        return False

    # ---- one row
    def _process(self, queue: str, client: Client, sink: WarcSink, row) -> None:
        """One row's request and its answer (_process_row). A page read again only for its pictures' new addresses
        (§23 B2) that brings no web page keeps the answer it had (_gsimg_reread_end, §23.9 B2)."""
        keep = self._gsimg_keep(row)
        self._process_row(queue, client, sink, row)
        if keep is not None:
            self._gsimg_reread_end(row["id"], *keep)

    def _process_row(self, queue: str, client: Client, sink: WarcSink, row) -> None:
        kind0 = row["kind0"] or row["kind"]
        if row["prio"] < 0 and kind0 == "page":  # a page read again for its pictures' new addresses (§23 B2)
            self._gsimg_prio_back(row)
        # an API row is the Google step's own address (design §7.6): never judged again by scope (a listing's next
        # page carries its token and may pass 1,000 characters, which scope would read as too_long)
        api = google_api.is_api(row["url"])
        if kind0 != "login_check" and not api:  # the rules of today, for rows that entered under older ones
            verdict = scope.judge(row["url"])
            if verdict.reason in _HARD_SKIP and not (kind0 == "script" and verdict.reason == "media"):
                self.frontier.finish(row["id"], state="skipped", reason=f"out:{verdict.reason}", fetched_at=None)
                return
        if queue.startswith("ip:") and scope.own_address(queue[3:]):
            # a name looked up before 11-06 that resolves to this machine: nothing is sent (robots.txt neither); looked
            # up again (_lookup: dns-fail), it waits as a name that does not resolve
            self.frontier.set_host(row["host"], queue="?")
            self.frontier.finish(row["id"], state="pending", fetched_at=None)
            self._place_hosts()
            return
        if row["referrer"] and not self._gsimg_live(row):  # a runtime picture no longer to ask for (§23 B2, B3)
            return
        if self.disk_free(self.root) < MIN_FREE_BYTES:
            self.frontier.event("error", "disk_low", f"less than {MIN_FREE_BYTES // 1024**3} GB free under "
                                f"{self.root}: the run stops before the archive cannot be written", row["host"],
                                row["url"])
            raise _DiskLow
        row = self._move_scheme(row)
        if row is None:
            return
        url, host, uid = row["url"], row["host"], row["id"]
        admitted = self._admitted_as(row)
        robots = self._robots(queue, client, row, admitted)
        if robots.state == "wait":
            self.frontier.finish(uid, state="pending", reason="robots_wait", fetched_at=None)
            return
        if robots.state == "conn":
            self._conn_failed(queue, client, row, url, None)
            return
        if robots.state == "unreachable":
            if self.frontier.row_by_id(uid)["state"] == "active":  # else it was given up with its host
                self.frontier.set_host(host, cooldown_until=self.clock() + RETRY_UNREACHABLE_S)
                self.frontier.finish(uid, state="pending", reason="robots_unreachable", fetched_at=None)
            return
        row = self._move_scheme(row)  # robots.txt may have shown the host answers on one scheme only
        if row is None:
            return
        url, host, uid = row["url"], row["host"], row["id"]
        if self._not_due(host):  # the Crawl-delay after robots.txt: the row comes back when the host is due
            self.frontier.finish(uid, state="pending", fetched_at=None)
            return
        if kind0 in ("page", "login_check"):  # only a page host's missing page says something (design §6.8)
            probed = self._probe(queue, client, url, robots, admitted)
            if isinstance(probed, float):  # the server asked to be left alone: no attempt is used
                self.frontier.finish(uid, state="pending", reason="retry_after", next_at=probed, fetched_at=None)
                return
            if isinstance(probed, FetchError):
                kind = error_kind(probed)
                if kind in SWITCH_KINDS:  # the page would meet the same door: its other scheme first (§6.6)
                    self._door_failed(queue, client, sink, row, probed, kind)
                else:
                    self._failed(queue, row, probed)
                return
            if self._not_due(host):  # the Crawl-delay after the probe
                self.frontier.finish(uid, state="pending", fetched_at=None)
                return
        if kind0 == "page":  # a platform's detail page: its host's id probe once per detail type (design §10.1)
            waited = self._id_probe(queue, client, row, robots, admitted)
            if waited is not None:
                self.frontier.finish(uid, state="pending", reason="retry_after", next_at=waited, fetched_at=None)
                return
            if self._not_due(host):
                self.frontier.finish(uid, state="pending", fetched_at=None)
                return
        if kind0 == "image" and self._decor_repeat(row):
            self.frontier.finish(uid, state="skipped", reason="decor:repeat", fetched_at=None)
            return
        # a Google Sites runtime picture carries the Referer its page's referrer policy gives, and nothing else of a
        # browser's (design §23 B1); every other request carries none
        target = google_api.target(self.frontier, url) if api else Target(url, headers=self._referer(row, url))
        # an address only an old source names: its 500 is its own end, never its server's error (OLD_GONE, lis 11-07)
        spare = row["found_via"] in OLD_VIAS and self.frontier.old_only(uid)
        got = self._request(queue, client, target, "page", scope_reason=admitted, robots_sha=robots.sha,
                            max_bytes=google_api.max_bytes(url) if api else MAX_BYTES, spare=spare)
        if isinstance(got, HostCircuitOpen):  # nothing was sent
            h = self.frontier.host(host)
            before = (h["cooldown_until"], h["cooldown_s"])
            wait = self._cool_host(host)
            if self._last_kind.get(host) in NET_KINDS:
                self.net.charge(queue, "cool", host, *before)
            client.reset_host(host)
            self.frontier.finish(uid, state="pending", reason="host_cooldown", fetched_at=None)
            self.frontier.event("warn", "host_cooldown", f"{BREAKER_THRESHOLD} timeouts, 5xx or connection errors in a "
                                f"row: the host waits {wait / 60:.0f} min", host, url)
            return
        self._count()
        if isinstance(got, RetryLater):  # not the URL's fault: no attempt is used
            until = self._rest_queue(queue, client, host, url, got)
            self.frontier.finish(uid, state="pending", reason="retry_after", next_at=until, fetched_at=None)
            return
        if isinstance(got, TooLarge):
            size = re.search(r"(\d+) bytes declared", str(got))
            self.frontier.finish(uid, state="skipped", reason="media_type" if isinstance(got, UnwantedType)
                                 else "too_large", size_declared=int(size[1]) if size else None)
            return
        if isinstance(got, DecodeError):
            self.frontier.finish(uid, state="failed", reason="decode")
            return
        if isinstance(got, FetchError):
            kind = error_kind(got)
            if kind in SWITCH_KINDS:
                self._door_failed(queue, client, sink, row, got, kind)
                return
            if self._gone(queue, spare, kind):  # failed at once, never tried again by itself (`retry http_5` puts it back)
                self.frontier.finish(uid, state="failed", attempts=row["attempts"] + 1, reason=kind)
                return
            self._failed(queue, row, got)
            return
        if api and got.status >= 400 and self._api_error(queue, row, got, sink):
            return
        if not (row["referrer"] and got.status in DENY_STATUSES):  # a runtime picture's 403 is §23 B2's, no streak
            self._answered(host, url, got.status)
        self._store(queue, row, got, sink)
        if row["referrer"]:
            self._gsimg_after(row)
        if api and got.status == 200 and kind0 in ("drive", "drive_list", "calendar"):
            stored = self.frontier.row_by_id(uid)
            if stored is not None and stored["state"] == "done":  # what the answer holds (google_api.read)
                google_api.read(self.frontier, stored, got, self._google_add)

    # ---- Google's APIs (design §7.6)
    @property
    def _google_blocks(self) -> int:
        """Google's blocks in a row (google_api.block_state: kept in frontier.db, across runs; design §23 D2)."""
        return google_api.block_state(self.frontier)[0]

    def _api_error(self, queue: str, row, got: Snapshot, sink) -> bool:
        """A 4xx of Google's API, sorted by its reason (google_api.classify, design §7.6's table): kept as answered
        (api_error, login, too_large: _store, then the columns it says); a quota or rate answer rests the queue and
        retries the row as any GET; one file's downloads used up puts that row alone off a day (§23 D1); a key problem
        rests the queue for the run and puts the row back, no attempt used (the next run's hold_for_key lifts the
        rest). Never a refusal streak of a directory (_answered: ten 403s of quota would pause /drive/v3/files whole).
        Whether it handled the answer."""
        err = google_api.classify(got.status, got.body, got.headers)
        if err is None:
            return False
        now = self.clock()
        if err.action == "file_quota":
            # downloadQuotaExceeded: this one file's downloads are used up, not ours (§23 D1, 【Claude 的执行办法】):
            # the row alone waits a day, an attempt each time, the queue goes on; the third time it is api_quota
            attempts = row["attempts"] + 1
            last = attempts >= google_api.MAX_FILE_QUOTA_TRIES
            if last:
                self.frontier.finish(row["id"], state="failed", attempts=attempts, reason=err.url_reason)
            else:
                self.frontier.finish(row["id"], state="pending", attempts=attempts, reason=err.url_reason,
                                     next_at=now + (err.pause_s or google_api.FILE_QUOTA_WAIT_S), fetched_at=None)
            self.frontier.event("warn", "google_file_quota", (
                f"Google：这个档案的下载次数被用完（第 {attempts} 次）：" + (
                    "试了 3 次还是这样，记下来要人看" if last else "只有这一个档案 24 小时后再试，其他照常")),
                row["host"], row["url"])
            return True
        if err.action == "retry" and err.block:
            # the service's protection (a quota, a rate, Google's "automated queries" page; pilot 10-26 F9): the
            # queue rests, longer each time the block goes on (doubling, at most MAX_PAUSE_S), and the row waits for
            # the rest with no attempt used: nothing about the row is wrong, and nothing is bypassed (CLAUDE.md). The
            # streak is kept in frontier.db, so the next run goes on doubling (§23 D2); blocked for long, a person
            # must look (google_block_long)
            streak, first = google_api.block_state(self.frontier)
            streak, first = streak + 1, first if first is not None else now
            self.frontier.meta(google_api.BLOCK_STREAK_KEY, str(streak))
            self.frontier.meta(google_api.BLOCK_FIRST_KEY, repr(first))
            base = err.pause_s if err.pause_s is not None else google_api.BLOCK_PAUSE_S
            wait = min(base * 2 ** (streak - 1), google_api.MAX_PAUSE_S)
            until = now + wait
            self.frontier.pause(queue, until, f"{row['host']}: {err.url_reason}")
            self.frontier.finish(row["id"], state="pending", reason=err.url_reason, next_at=until, fetched_at=None)
            self.frontier.event("warn", "google_block", f"Google 回「{err.reason}」（连续第 {streak} 次）：Google 暂时挡"
                                f"下载，googleapis 这条队休息 {wait / 60:.0f} 分钟再试；这一行放回待抓、不算失败；这是 "
                                "Google 的保护机制，不绕过", row["host"], row["url"])
            if google_api.block_long(streak, first, now):
                self.frontier.event("error", "google_block_long", google_api.block_words(streak, first, now) +
                                    "（不换金钥、不换 IP、不伪装；看过以后决定要不要再等）", row["host"], row["url"])
            return True
        if err.action == "retry":
            self.frontier.pause(queue, now + (err.pause_s if err.pause_s is not None else google_api.RATE_PAUSE_S),
                                f"{row['host']}: {err.url_reason}")
            self._failed(queue, row, kind=err.url_reason)
            return True
        if err.action == "key":
            # the reason starts with api_key, which hold_for_key() reads as a key's rest when the next run starts
            self.frontier.pause(queue, now + (err.pause_s or google_api.KEY_PAUSE_S), f"api_key: {err.reason}")
            self.frontier.event("warn", "key_check", f"Google 不接受金钥（{err.reason}）：googleapis 这条队这次 run "
                                "暂停，下次 nknu archive run 开跑时再试；这一行放回待抓，不算失败", row["host"],
                                row["url"])
            self.frontier.finish(row["id"], state="pending", reason="api_key", fetched_at=None)
            return True
        self._store(queue, row, got, sink)
        cols = google_api.error_cols(err)
        if cols:
            if "flags" in cols:
                old = self.frontier.row_by_id(row["id"])["flags"]
                cols["flags"] = ",".join(dict.fromkeys(filter(None, [*(old or "").split(","), cols["flags"]])))
            self.frontier.finish(row["id"], **cols)
        return True

    def _google_add(self, url: str, placement: round1.Placement, from_row, found_via: str) -> tuple[int | None, bool]:
        """google_api.read()'s way to make a row (google_api.Add): a content row, a listing's next page, a child."""
        e = self._enqueue(url, placement=placement, url=url, from_row=from_row, found_via=found_via,
                          found_from=from_row["id"], depth=from_row["depth"])
        return e.id, e.verdict not in ("dup", "scheme_switch")

    def _google(self, adm: round1.Admission, *, from_row, how: str, depth: int, found_via: str,
                found_from: int | None = None, zone: str | None = None,
                text: str | None = None) -> tuple[Enqueued, list[dict]]:
        """An address the Google step took (design §7.6): every row it makes and the page_res entries of the page that
        uses it (google_api.uses: an embed showing three calendars makes three; attach: a folder read already brings
        all it holds). Returns the Enqueued of its first row (else of its first use) for the link table, and the
        entries."""
        first: Enqueued | None = None
        entries: list[dict] = []
        for u in google_api.uses(self.plan, adm.link, from_row, how):
            e = self._enqueue(u.url or u.link, placement=u.placement, url=u.url or u.link, from_row=from_row,
                              depth=depth, found_via=found_via, found_from=found_from)
            if first is None or (first.id is None and e.id is not None):
                first = e
            entries += google_api.attach(self.frontier, u, e.id if u.url is not None else None, zone=zone, text=text)
        return first or Enqueued(adm.placement.reason or "out", None, None, adm.placement), entries

    def _google_users(self, adm: round1.Admission, from_row, via, how: str, found_via: str,
                      page_ids: list[int]) -> Enqueued:
        """A resource row `via` (a short link, an attachment, a frame) that leads to a Drive, Docs or Calendar address:
        the pages that use it get the address's entries as if they had linked it (design §7.7), each brought up to
        date. from_row: the round-1 row its placement is by (the page, or the resource row, which has its owner and
        tier)."""
        e, entries = self._google(adm, from_row=from_row, how=how, depth=via["depth"], found_via=found_via,
                                  found_from=via["id"])
        for pid in page_ids:
            self.frontier.add_page_res(pid, entries)
            google_api.resync(self.frontier, pid)
        return e

    # ---- the id probe (design §10.1)
    def _id_probe(self, queue: str, client: Client, row, robots: Robots, admitted: str) -> float | None:
        """Once per platform host and detail type, before its first detail page: the address of an item that cannot
        exist (profiles.id_probe_url: SSO's UNID of 40 zeros, c.nknu's nid=999999999), its answer kept in id_probe
        (with whether it is an empty detail). A page is gone_id only when the probe answered 200 with an empty detail
        and the page is itself one (_page). Returns the time the server queue rests until (Retry-After), else None. A
        probe that got no answer is asked again with a later page (one that may have reached the server, a timeout or
        a broken answer, at most PROBE_TRIES times a run); any answer, an error too, is kept."""
        probe_url = profiles.id_probe_url(row["url"])
        if probe_url is None:
            return None
        host, ptype = row["host"], pagetypes.type_key(row["url"], row["site"])
        if self.frontier.q("SELECT 1 FROM id_probe WHERE host=? AND page_type=?", (host, ptype)):
            return None
        if self._probe_tries.get((host, ptype), 0) >= PROBE_TRIES:
            return None  # it timed out (or broke off) that often this run: the next run asks again
        got = self._request(queue, client, Target(probe_url), "id_probe", scope_reason=admitted,
                            robots_sha=robots.sha)
        if isinstance(got, HostCircuitOpen):
            return None
        if isinstance(got, RetryLater):
            return self._rest_queue(queue, client, host, probe_url, got)
        cols: dict[str, Any] = {"url": probe_url, "at": self.clock()}
        if isinstance(got, FetchError):
            kind = error_kind(got)
            if kind == NOT_ARCHIVED:  # the rehearsal: no answer to keep, and none will come this run (design §18.1)
                self._probe_tries[(host, ptype)] = PROBE_TRIES
                return None
            if kind in TRANSPORT_KINDS or kind.startswith("net_"):
                if kind not in PROBE_UNSENT:  # a timeout or a broken answer may have reached the server (review
                    key = (host, ptype)       # 10-26 phase 3): not asked again before every detail page of this run
                    self._probe_tries[key] = self._probe_tries.get(key, 0) + 1
                return None
            cols["status"] = int(kind[5:]) if kind.startswith("http_") and kind[5:].isdigit() else 0
        else:
            text = None
            if got.status == 200 and sniff.sniff(got.body, got.headers.get("content-type", "")) == "html":
                text = got.text if got.text is not None else _lenient(got.body, got.headers.get("content-type", ""))
            empty = profiles.empty_detail(probe_url, text) if text is not None else None
            cols.update(status=got.status, sha256=got.sha256, len=len(got.body),
                        final_url=scope.canonical(got.headers.get("location", ""), probe_url)
                        if got.status in REDIRECTS else None,
                        title=extract(text, probe_url).title if text else None,
                        empty=None if empty is None else int(empty))
        names = ["host", "page_type", *cols]
        self.frontier.x(f"INSERT OR REPLACE INTO id_probe({', '.join(names)}) VALUES ({','.join('?' * len(names))})",
                        (host, ptype, *cols.values()))
        return None

    def _door_failed(self, queue: str, client: Client, sink: WarcSink, row, exc: FetchError, kind: str) -> None:
        """The row's address refused a connection or a TLS handshake (its probe's, or its own): its other scheme once
        (design §6.6). Moved there: done. The other scheme answered without letting the page in (it sends the visitor
        back, or errs): the server is up and the address cannot be reached, the URL's failure (an attempt, retried
        later: an expired certificate ends as tls_cert, 连接失败). Neither answered: a TLS failure is the URL's, a
        connection the server's (_conn_failed)."""
        switched = self._switch_scheme(queue, client, sink, row)
        if switched in ("moved", "handled"):
            return
        if switched == "up" or kind in TLS_KINDS:
            self._failed(queue, row, exc, kind=kind)
            return
        self._conn_failed(queue, client, row, row["url"], exc)

    def _back(self, uid: int, reason: str) -> None:
        """The row goes back to pending, no attempt used, unless its server was given up meanwhile (_give_up_hosts)."""
        self.frontier.x("UPDATE url SET state='pending', reason=? WHERE id=? AND state='active'", (reason, uid))

    def _move_scheme(self, row):
        """A row on a host that answers on the other scheme only goes there (design §6.6): the row of the address in
        that scheme (made from this one when there is none, found_via scheme_switch) is fetched instead, active, and
        is the row the worker handles from now on (self._tl.row); this one is skipped (moved_scheme) with final_url
        pointing there. None when the other row was fetched already."""
        h = self.frontier.host(row["host"])
        other = _other_scheme(row["url"])
        if h is None or not h["scheme_only"] or other is None or not other.startswith(h["scheme_only"] + "://"):
            return row
        moved = self._moved_row(row, other)
        self.frontier.finish(row["id"], state="skipped", reason="moved_scheme", final_url=other)
        if moved is None or moved["state"] not in ("pending", "active"):
            return None
        self.frontier.activate(moved["id"])  # its answer, when it holds one, kept first (review of 4be5e34)
        self._tl.row = moved["id"]
        self._carry_only(row["id"], moved["id"])
        return self.frontier.row_by_id(moved["id"])

    def _moved_row(self, row, other: str):
        existing = self.frontier.row(other)
        if existing is not None:
            return existing
        self.frontier.add(other, host=row["host"], site=scope.site_key(other), unit=row["unit"], tier=row["tier"],
                          kind=row["kind"], depth=row["depth"], found_via="scheme_switch", found_from=row["id"],
                          owner_site=row["owner_site"], prio=row["prio"], leaf=row["leaf"], kind0=row["kind0"],
                          added_at=self.clock(), old_ref=row["old_ref"])
        moved = self.frontier.row(other)
        self.frontier.x("UPDATE page_res SET res_id=? WHERE res_id=?", (moved["id"], row["id"]))
        return moved

    def _switch_scheme(self, queue: str, client: Client, sink: WarcSink, row) -> str | None:
        """The row's scheme refused a connection or a TLS handshake: once, on the same queue after the gap, the other
        scheme (design §6.6). 'moved': it answered there, the row moved and the answer is stored on the other row; the
        host is taken to answer on that scheme only when the answer is a page or a redirect elsewhere (2xx, 3xx), never
        a 4xx or 5xx, which says nothing of its other pages (second review 10-26). 'up': it answered, but only to send
        the visitor back to the scheme that failed, or with an error: the server is there, the address cannot be
        reached (the caller's: the URL's failure, never a loop). 'handled': it asked us to come back later (the row
        waits, no attempt used). None: that scheme did not answer either, or there is none to try."""
        other = _other_scheme(row["url"])
        if other is None or row["found_via"] == "scheme_switch":
            return None
        got = self._request(queue, client, Target(other), "page", scope_reason=self._admitted_as(row))
        if isinstance(got, HostCircuitOpen):
            return None  # nothing was sent
        if isinstance(got, RetryLater):
            until = self._rest_queue(queue, client, row["host"], other, got)
            self.frontier.finish(row["id"], state="pending", reason="retry_after", next_at=until, fetched_at=None)
            return "handled"
        if isinstance(got, FetchError):
            return None if error_kind(got) in SWITCH_KINDS | {"dns"} else "up"
        if got.status in REDIRECTS and _sends_back(row["url"], other, got.headers.get("location")):
            return "up"
        moved = self._moved_row(row, other)
        self.frontier.finish(row["id"], state="skipped", reason="moved_scheme", final_url=other)
        if 200 <= got.status < 400:
            self._learn_scheme(row["host"], urlsplit(other).scheme)
        self._answered(row["host"], other, got.status)
        if moved["state"] in ("pending", "active"):
            self.frontier.activate(moved["id"])  # its answer, when it holds one, kept first (review of 4be5e34)
            self._tl.row = moved["id"]
            self._carry_only(row["id"], moved["id"])
            self._store(queue, self.frontier.row_by_id(moved["id"]), got, sink)
        return "moved"

    def _decor_repeat(self, row) -> bool:
        """A layout image (design §7.2): on 5 or more of its site's stored pages and 30% of them, never in a body."""
        got = self.frontier.q("SELECT count(DISTINCT page_id), coalesce(max(zone = 'body'), 0) FROM page_res "
                              "WHERE res_id=? AND role='img'", (row["id"],))[0]
        site_pages = self.frontier.q("SELECT count(*) FROM url WHERE owner_site=? AND kind='page' AND state='done'",
                                     (row["owner_site"],))[0][0]
        return links.decor_repeat(row["url"], got[0], site_pages, bool(got[1]))

    # ---- Google Sites' runtime pictures (design §23 B, 【用户同意 Claude 的建议】 10-27; how: 【Claude 的执行办法】)
    def _referer(self, row, url: str) -> dict[str, str]:
        """The Referer of a runtime picture's request (or of its hop): what its page's referrer policy gives for this
        address (links.referrer_for, W3C Referrer Policy); no header for any other row, or when the policy says none."""
        if not row["referrer"]:
            return {}
        try:
            ref = json.loads(row["referrer"])
        except (TypeError, ValueError):
            return {}
        value = links.referrer_for(str(ref.get("policy") or ""), str(ref.get("from") or ""), url)
        return {"Referer": value} if value else {}

    def _gsimg_part(self, row, page: links.PageLinks, img: links.Image, key: str, need: str, why: str,
                    depth: int) -> dict:
        """A page's runtime picture as its page_res entry (how gsimg, res_key by where it sits). One the ordinary rules
        leave out is that (need no); one of a site given up is round two's, no row (§23 B3); one this page got
        already (its row from an earlier reading came to the picture) keeps that row and its new address is never
        asked for; else its address of this reading is queued first on its queue (PRIO_NOW) with the page's referrer
        policy, and the address of an earlier reading this one replaces is stale (_gsimg_replaced). The same address
        again from a reading of the page for new ones, answered already without the picture: round two's."""
        entry = {"res_key": key, "how": GSIMG_HOW, "role": "img", "zone": img.zone, "text": img.alt}
        if need != "must":
            return {**entry, "need": need, "why": why}
        if self._gsimg_given_up(row["owner_site"] or row["site"]):
            return {**entry, "need": "round2", "why": links.GOOGLE_SITES_ROUND2}
        old = self.frontier.q("SELECT res_id FROM page_res WHERE page_id=? AND res_key=?", (row["id"], key))
        old_id = old[0]["res_id"] if old else None
        if old_id is not None and self._gsimg_got(old_id):
            return {**entry, "need": "must", "res_id": old_id}
        adm = self.admit(img.src, page.base, row, "image")
        if adm is None:
            return {**entry, "need": "no", "why": "bad_url"}
        known = self.frontier.row(adm.url)
        if known is not None and known["state"] == "later" and known["reason"] == links.GOOGLE_SITES_ROUND2:
            # an address another page's picture already left for round two (refused, or as old as one that was):
            # the same for this page, never asked again
            return {**entry, "need": "round2", "why": links.GOOGLE_SITES_ROUND2}
        ref = json.dumps({"policy": page.policy, "from": row["url"]}, ensure_ascii=False)
        e = self._enqueue(img.src, base=page.base, placement=adm.placement, url=adm.url, from_row=row, how="image",
                          depth=depth, found_via="link", prio=PRIO_NOW, referrer=ref)
        if e.id is None:  # a rule leaves the address out: as any picture's (_parts' use)
            pl = e.placement
            return {**entry, "how": "link_only", "need": "must", "why": (pl.reason if pl is not None else None)}
        if old_id is not None and old_id != e.id:
            self._gsimg_replaced(old_id, adm.url)
        elif old_id == e.id and self._gsimg_refused_end(e.id):
            # the page read again for new addresses gave this picture the address that was just refused: nothing new
            # to ask for, round two's (another page that shows the same address shares its row and its outcome)
            self._gsimg_rows_later(e.id)
            return {**entry, "need": "round2", "why": links.GOOGLE_SITES_ROUND2}
        return {**entry, "need": "must", "res_id": e.id}

    def _gsimg_chain(self, res_id: int) -> list:
        """A runtime picture's rows, its address first, then each 3xx's target (its hop to lh*-rt), as far as known."""
        out, seen = [], set()
        cur = self.frontier.row_by_id(res_id)
        while cur is not None and cur["id"] not in seen and len(out) < outcome.MAX_HOPS:
            out.append(cur)
            seen.add(cur["id"])
            if not (cur["state"] == "done" and cur["status"] in REDIRECTS and cur["final_url"]
                    and cur["final_url"] != cur["url"]):
                break
            cur = self.frontier.row(cur["final_url"])
        return out

    def _gsimg_landing(self, res_id: int):
        chain = self._gsimg_chain(res_id)
        return chain[-1] if chain else None

    def _gsimg_got(self, res_id: int) -> bool:
        """Whether a runtime picture's rows came to the picture: a 200 that sniffed as an image, where they land."""
        end = self._gsimg_landing(res_id)
        return end is not None and end["state"] == "done" and end["status"] == 200 and end["kind"] == "image"

    def _gsimg_replaced(self, old_id: int, new_url: str) -> None:
        """A picture's rows of an earlier reading of its page, now that a new reading gives it a new address
        (§23 B2): none is sent any more (pending: skipped), each answer stays as it was (status, WARC), and each that
        is no redirect points at the new address (reason gsimg_stale, final_url): its result is redirect, the new
        row's is the picture's, so it is never 连接失败 nor a picture lost."""
        for r in self._gsimg_chain(old_id):
            if r["state"] == "active":
                continue  # in flight on another queue: its answer comes back to _gsimg_after, where it is no one's
            if r["state"] == "done" and r["status"] in REDIRECTS:
                continue  # it leads to the next row of the chain, which is marked
            state = "done" if r["state"] == "done" else "skipped"
            self.frontier.x("UPDATE url SET state=?, reason=?, final_url=?, next_at=0 WHERE id=?",
                            (state, GSIMG_STALE, new_url, r["id"]))

    def _gsimg_refused_end(self, res_id: int) -> bool:
        """Whether a runtime picture's rows came to a refusal (401, 403, 451 where they land)."""
        end = self._gsimg_landing(res_id)
        return end is not None and end["state"] == "done" and end["status"] in DENY_STATUSES

    def _gsimg_waits(self, res_id: int | None) -> bool:
        """Whether a runtime picture is still to be asked for, or was refused: what §23 B2, B3 leave for round two. One
        that came, failed (a timeout, a 5xx: the URL's failure, 连接失败 as any) or is in flight stays as it is."""
        if res_id is None:
            return True
        end = self._gsimg_landing(res_id)
        return end is not None and (end["state"] == "pending" or self._gsimg_refused_end(res_id))

    def _gsimg_rows_later(self, res_id: int) -> None:
        """A picture left for round two (§23 B2, B3): its rows not sent wait there, the one it came to (a 403) too,
        its answer kept (state later, reason round2:google_sites_image: neither 连接失败 nor a row nobody uses)."""
        for r in self._gsimg_chain(res_id):
            if r["state"] == "pending" or (r["state"] == "done" and r["status"] in DENY_STATUSES):
                self.frontier.x("UPDATE url SET state='later', reason=?, next_at=0 WHERE id=?",
                                (links.GOOGLE_SITES_ROUND2, r["id"]))

    def _gsimg_round2(self, page_id: int, keys) -> None:
        """These pictures of a page wait for round two's browser (page_res need round2, why
        round2:google_sites_image, no row: §23 B3), and their rows with them: those not sent yet, or refused
        (_gsimg_waits); one that came, failed or is in flight keeps its row and its result."""
        for key in dict.fromkeys(keys):
            got = self.frontier.q("SELECT res_id FROM page_res WHERE page_id=? AND res_key=?", (page_id, key))
            if not got or not self._gsimg_waits(got[0]["res_id"]):
                continue
            self.frontier.x("UPDATE page_res SET need='round2', why=?, res_id=NULL WHERE page_id=? AND res_key=?",
                            (links.GOOGLE_SITES_ROUND2, page_id, key))
            if got[0]["res_id"] is not None:
                self._gsimg_rows_later(got[0]["res_id"])

    def _gsimg_entry(self, row) -> tuple[int, str] | None:
        """The page and the picture (page_res page_id, res_key) whose current address this row is, or whose hop it is;
        None when no page's picture is this row any more."""
        cur = row
        for _ in range(outcome.MAX_HOPS):
            got = self.frontier.q("SELECT page_id, res_key FROM page_res WHERE res_id=? AND how=? LIMIT 1",
                                  (cur["id"], GSIMG_HOW))
            if got:
                return got[0]["page_id"], got[0]["res_key"]
            if cur["found_via"] != "redirect" or cur["found_from"] is None:
                return None
            cur = self.frontier.row_by_id(cur["found_from"])
            if cur is None or not cur["referrer"]:
                return None
        return None

    def _gsimg_live(self, row) -> bool:
        """Before a runtime picture's request (§23 B2, B3): whether it is still a page's picture to ask for. A row no
        page's picture is any more is not sent (stale; round two's again when it had been left there: a retry of
        round two's rows sends none of them); one of a site given up waits for round two."""
        entry = self._gsimg_entry(row)
        if entry is None:
            if row["reason"] == links.GOOGLE_SITES_ROUND2:
                self.frontier.x("UPDATE url SET state='later' WHERE id=?", (row["id"],))
            else:
                self.frontier.x("UPDATE url SET state='skipped', reason=? WHERE id=?", (GSIMG_STALE, row["id"]))
            return False
        if self._gsimg_given_up(row["owner_site"] or ""):
            self.frontier.x("UPDATE url SET state='pending' WHERE id=?", (row["id"],))  # not sent: round two's
            with self._gsimg_lock:
                self._gsimg_round2(entry[0], [entry[1]])
            return False
        return True

    def _gsimg_given_up(self, site: str) -> bool:
        return bool(site) and self.frontier.meta(GSIMG_GIVEUP_KEY + site) is not None

    def _gsimg_state(self, site: str) -> dict:
        raw = self.frontier.meta(GSIMG_SITE_KEY + site)
        try:
            st = json.loads(raw) if raw else {}
        except ValueError:
            st = {}
        return {"ok": bool(st.get("ok")), "reread": bool(st.get("reread")), "tries": list(st.get("tries") or [])}

    def _gsimg_save(self, site: str, st: dict) -> None:
        self.frontier.meta(GSIMG_SITE_KEY + site, json.dumps(st, ensure_ascii=False))

    def _gsimg_after(self, row) -> None:
        """A runtime picture's answer (§23 B): a picture marks its site as one whose pictures come; a refusal (403) is
        an address that expired, or a site whose pictures never come (_gsimg_refused); a 302's hop is queued by
        _redirect (_follow), with the same Referer, first on its server's queue."""
        stored = self.frontier.row_by_id(row["id"])
        if stored is None or stored["state"] != "done":
            return
        site = stored["owner_site"] or ""
        if stored["status"] == 200 and stored["kind"] == "image":
            with self._gsimg_lock:
                st = self._gsimg_state(site)
                if not st["ok"]:
                    st["ok"] = True
                    self._gsimg_save(site, st)
            return
        if stored["status"] in DENY_STATUSES:
            self._gsimg_refused(stored)

    def _gsimg_refused(self, row) -> None:
        """A runtime picture refused (§23 B2, B3; 【Claude 的执行办法】). A site whose pictures come: the page is read
        again for new addresses (GSIMG_REREADS times at most); after that, this picture and the page's pictures not
        sent yet (their addresses as old as this one) wait for round two. A site none of whose pictures came yet:
        its page is read again once, at the first refusal; each refusal after that leaves that picture for round two,
        and the GSIMG_TRIES-th gives the site up (_gsimg_giveup). A run given --only: this picture alone waits for
        round two (the page read again, the page's other pictures, the site's count and its giving up are rows and
        judgments beyond the listed ones: a full run's)."""
        entry = self._gsimg_entry(row)
        if entry is None:
            return
        page_id, key = entry
        site = row["owner_site"] or ""
        with self._gsimg_lock:
            if self.only is not None or self._gsimg_given_up(site):
                self._gsimg_round2(page_id, [key])
                return
            st = self._gsimg_state(site)
            if st["ok"]:
                if not self._gsimg_reread(page_id):
                    self._gsimg_round2(page_id, [key, *self._gsimg_unsent(page_id)])
                return
            if not st["reread"] and self._gsimg_reread(page_id):
                st["reread"] = True
                self._gsimg_save(site, st)
                return
            st["tries"].append([row["url"], row["status"]])
            self._gsimg_save(site, st)
            self._gsimg_round2(page_id, [key])
            if len(st["tries"]) >= GSIMG_TRIES:
                self._gsimg_giveup(site, st["tries"], row)

    def _gsimg_unsent(self, page_id: int) -> list[str]:
        """The page's runtime pictures to fetch whose rows are not sent yet."""
        out = []
        for r in self.frontier.q("SELECT res_key, res_id FROM page_res WHERE page_id=? AND how=? AND need='must' "
                                 "AND res_id IS NOT NULL", (page_id, GSIMG_HOW)):
            end = self._gsimg_landing(r["res_id"])
            if end is not None and end["state"] == "pending":
                out.append(r["res_key"])
        return out

    def _gsimg_reread(self, page_id: int) -> bool:
        """The page read again for new addresses of its pictures (§23 B2): pending, first on its queue (PRIO_NOW; its
        own prio kept in meta, back once it is taken). False when it was read again GSIMG_REREADS times already;
        True when it waits for that already."""
        page = self.frontier.row_by_id(page_id)
        if page is None:
            return False
        if page["state"] in ("pending", "active"):
            return True
        raw = self.frontier.meta(f"{GSIMG_PAGE_KEY}{page_id}")
        try:
            st = json.loads(raw) if raw else {}
        except ValueError:
            st = {}
        n = int(st.get("n") or 0)
        if n >= GSIMG_REREADS or page["state"] != "done" or page["status"] != 200 or page["kind"] != "page":
            return False  # only a web page stored well is read again for new addresses (§23.9 B2)
        prio = st.get("prio", page["prio"]) if page["prio"] >= 0 else st.get("prio", 1)
        keep = {k: page[k] for k in GSIMG_KEEP_COLS}
        self.frontier.meta(f"{GSIMG_PAGE_KEY}{page_id}", json.dumps({"n": n + 1, "prio": prio, "keep": keep},
                                                                    ensure_ascii=False))
        # attempts 0: a fetched row taken again starts its count (review of 4be5e34, Important 2)
        self.frontier.x("UPDATE url SET state='pending', reason=?, next_at=0, prio=?, attempts=0 WHERE id=? AND "
                        "state='done'", (GSIMG_REREAD, PRIO_NOW, page_id))
        self.frontier.event("info", "gsimg_reread", f"Google Sites pictures answered 403 halfway: the page is read "
                            f"again for new addresses ({n + 1}/{GSIMG_REREADS})", page["host"], page["url"])
        return True

    def _gsimg_prio_back(self, row) -> None:
        """A page taken to be read again for its pictures (§23 B2): its own place in its queue back."""
        raw = self.frontier.meta(f"{GSIMG_PAGE_KEY}{row['id']}")
        try:
            prio = int(json.loads(raw).get("prio", 1)) if raw else 1
        except (ValueError, TypeError, AttributeError):
            prio = 1
        self.frontier.x("UPDATE url SET prio=? WHERE id=? AND prio < 0", (max(prio, 0), row["id"]))

    def _gsimg_page_meta(self, page_id: int) -> dict:
        raw = self.frontier.meta(f"{GSIMG_PAGE_KEY}{page_id}")
        try:
            st = json.loads(raw) if raw else {}
        except ValueError:
            st = {}
        return st if isinstance(st, dict) else {}

    def _gsimg_keep(self, row) -> tuple[dict, list[dict]] | None:
        """For a page about to be read again for its pictures' new addresses (§23 B2): its answer as stored when the
        reading was planned, and its page_res as it stands now (§23.9 B2); None for any other row."""
        if (row["kind0"] or row["kind"]) != "page":
            return None
        keep = self._gsimg_page_meta(row["id"]).get("keep")
        if not isinstance(keep, dict):
            return None
        res = [dict(r) for r in self.frontier.q("SELECT * FROM page_res WHERE page_id=?", (row["id"],))]
        return keep, res

    def _gsimg_reread_end(self, page_id: int, keep: dict, res: list[dict]) -> None:
        """A page read again for its pictures' new addresses (§23 B2), after that reading (design §23.9 B2,
        【Claude 的执行办法】). A web page stored (a 200, read as a page, no sign-in or soft 404): done. Anything else (a 403,
        a 5xx, a timeout, no connection, a wait that sent nothing, a sign-in page …): that reading only was for new
        addresses, so the page keeps the answer it had (GSIMG_KEEP_COLS, no attempt spent; its page_res back too when
        the reading emptied it), it is not read again for its pictures, and those of them not got wait for round two
        (round2:google_sites_image: the known gaps, the dashboard's 下一轮). What the reading answered stays in the
        request table and the WARC; a warn event gsimg_reread_failed. A row the rules of today leave out (out:…)
        keeps that."""
        st = self._gsimg_page_meta(page_id)
        st.pop("keep", None)
        row = self.frontier.row_by_id(page_id)
        if row is None:
            return
        flags = set((row["flags"] or "").split(","))
        if (row["state"] == "done" and row["status"] == 200 and row["kind"] == "page"
                and not flags & {"login", "soft404"}):
            self.frontier.meta(f"{GSIMG_PAGE_KEY}{page_id}", json.dumps(st, ensure_ascii=False))
            return
        if row["state"] == "skipped" and (row["reason"] or "").startswith("out:"):
            self.frontier.meta(f"{GSIMG_PAGE_KEY}{page_id}", json.dumps(st, ensure_ascii=False))
            return
        got = f"{row['state']} {row['status'] or ''} {row['reason'] or ''}".strip()
        st["n"] = GSIMG_REREADS  # never read again for its pictures
        self.frontier.meta(f"{GSIMG_PAGE_KEY}{page_id}", json.dumps(st, ensure_ascii=False))
        self.frontier.finish(page_id, **{k: keep[k] for k in GSIMG_KEEP_COLS if k in keep})
        if res and not self.frontier.q("SELECT 1 FROM page_res WHERE page_id=? LIMIT 1", (page_id,)):
            self.frontier.set_page_res(page_id, res)  # a sign-in page or a soft 404 emptied it: back as it was
        self.frontier.event("warn", "gsimg_reread_failed", f"the page read again for its Google Sites pictures' new "
                            f"addresses brought no web page ({got}): it keeps the answer it had, its pictures not got "
                            "wait for round two (round2:google_sites_image)"[:1000], row["host"], row["url"])
        with self._gsimg_lock:
            keys = [r["res_key"] for r in self.frontier.q("SELECT res_key FROM page_res WHERE page_id=? AND how=? AND "
                                                           "need='must'", (page_id, GSIMG_HOW))]
            self._gsimg_round2(page_id, keys)  # those not sent yet or refused (_gsimg_waits)

    def _gsimg_giveup(self, site: str, tries: list, row) -> None:
        """No runtime picture of this site comes (§23 B3): kept in meta (across runs), a warn event, and every picture
        of the site not got yet waits for round two's browser, none of them sent."""
        self.frontier.meta(GSIMG_GIVEUP_KEY + site, json.dumps({"at": self.clock(), "tries": tries},
                                                               ensure_ascii=False))
        said = "; ".join(f"{u} {st}" for u, st in tries)
        self.frontier.event("warn", "gsimg_giveup", f"{site}: the first {len(tries)} Google Sites pictures tried "
                            f"after the page was read again answered 403 ({said}): its other runtime pictures are "
                            "not asked for, round2:google_sites_image (the next round's browser)"[:1000],
                            row["host"], row["url"])
        for r in self.frontier.q("SELECT pr.page_id, pr.res_key FROM page_res pr JOIN url p ON p.id = pr.page_id "
                                 "WHERE pr.how=? AND pr.need='must' AND p.owner_site=?", (GSIMG_HOW, site)):
            self._gsimg_round2(r["page_id"], [r["res_key"]])  # those not sent yet or refused (_gsimg_waits)

    def _failed(self, queue: str, row, exc: FetchError | None = None, kind: str | None = None) -> None:
        """A request for this row reached no answer (or an error page): tried again later (30 min × attempts), three
        attempts in all."""
        uid, kind = row["id"], kind or error_kind(exc)
        attempts = row["attempts"] + 1
        if kind == NOT_ARCHIVED:  # the rehearsal: no answer will ever come from its old archive (design §18.1)
            self.frontier.finish(uid, state="failed", attempts=attempts, reason=kind)
            return
        if kind in NET_KINDS:
            self.net.charge(queue, "url", uid, row["attempts"])
        if attempts < MAX_ATTEMPTS:
            self.frontier.finish(uid, state="pending", attempts=attempts, reason=kind, fetched_at=None,
                                 next_at=self.clock() + RETRY_AFTER_S * attempts + self.rng.uniform(0, RETRY_JITTER_S))
        else:
            self.frontier.finish(uid, state="failed", attempts=attempts, reason=kind)

    def _answered(self, host: str, url: str, status: int) -> None:
        """The host answered: its breaker's cool-down starts again from 15 min. A refusal (401/403/451) counts towards
        its directory's streak instead; the tenth in a row makes that directory's URLs wait DENY_WAIT_S, a fixed
        15 min, while the rest of the host goes on (a protected upload folder never stalls c.nknu)."""
        h = self.frontier.host(host)
        if h is None:
            return
        if host == google_api.API_HOST and status < 400 and google_api.is_download(url) and self._google_blocks:
            # a download got through: the next block's rest starts from the first again. Metadata and listings answer
            # 200 while downloads are blocked (pilot 10-26), so they say nothing of the block (review of the fixes)
            self.frontier.x("DELETE FROM meta WHERE key IN (?, ?)",
                            (google_api.BLOCK_STREAK_KEY, google_api.BLOCK_FIRST_KEY))
        directory = deny_dir(url)
        if status in DENY_STATUSES and host != google_api.API_HOST:  # an API's 403 is its reason's (_api_error)
            run = self.frontier.deny(directory, host)
            if run >= DENY_STREAK:
                until = self.clock() + DENY_WAIT_S
                rests = self._deny_rests[directory] = self._deny_rests.get(directory, 0) + 1
                if rests >= DENY_GIVE_UP:  # refused again after a wait: parked for a person, not drained for hours
                    n = self._park_directory(host, directory)
                    self.frontier.deny_rest(directory, until)
                    self.frontier.event("warn", "host_denies_parked", f"{run} URLs in a row under {directory} "
                                        f"answered 401/403/451 again after a {DENY_WAIT_S / 60:.0f}-min wait: its {n} "
                                        "other URLs are parked (failed, denied_dir) for a person to look at (nknu "
                                        "archive retry denied_dir puts them back)", host, url)
                    return
                n = self._defer_directory(host, directory, until)
                self.frontier.deny_rest(directory, until)
                self.frontier.event("warn", "host_denies", f"{run} URLs in a row under {directory} answered "
                                    f"401/403/451: its {n} other URLs wait {DENY_WAIT_S / 60:.0f} min, the rest of the "
                                    "host goes on (nknu archive retry http_403 puts the refused ones back if it was "
                                    "blocking us)", host, url)
            return
        self.frontier.deny_clear(directory)
        self._deny_rests.pop(directory, None)  # answered: a later streak waits again before any parking
        if h["cooldown_s"]:
            self.frontier.set_host(host, cooldown_s=0)

    def _defer_directory(self, host: str, directory: str, until: float) -> int:
        ids = self._directory_rows(host, directory)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            self.frontier.x(f"UPDATE url SET next_at = max(next_at, ?), reason = coalesce(reason, 'denied_dir') "
                            f"WHERE id IN ({','.join('?' * len(part))})", (until, *part))
        return len(ids)

    def _park_directory(self, host: str, directory: str) -> int:
        """The directory's rows still to fetch fail as denied_dir (要人看; `retry denied_dir` puts them back): nothing
        more is asked of a directory that refused DENY_GIVE_UP streaks in a row (pilot 10-26, F8)."""
        ids = self._directory_rows(host, directory)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            self.frontier.x(f"UPDATE url SET state='failed', reason='denied_dir', next_at=0 "
                            f"WHERE id IN ({','.join('?' * len(part))})", part)
        return len(ids)

    def _directory_rows(self, host: str, directory: str) -> list[int]:
        """The directory's rows still to fetch, which a streak of refusals holds back or parks (a run given --only:
        its listed rows only)."""
        row_id, row_args = self._ids("id")
        return [r["id"] for r in self.frontier.q(f"SELECT id, url FROM url WHERE host=? AND state='pending'{row_id}",
                                                 (host, *row_args))
                if deny_dir(r["url"]) == directory]

    # ---- B4: where the requests are made from
    def _campus_resume(self) -> None:
        """A run on the address the last check that could tell was made from starts from what it found (second review
        11-06: on the lab machine every run started unknown, and a long-dead school server failing before SSO's check
        came back had its rows handed to the lab machine, _lab_may_reach); the check is still asked at once."""
        if not self.campus_url:
            return
        try:
            last = json.loads(self.frontier.meta(CAMPUS_KEY) or "null")
        except ValueError:
            return
        if isinstance(last, dict) and last.get("state") in ("on", "off") and last.get("addr") == self.local_address():
            self.campus, self._campus_addr = last["state"], last["addr"]

    def _campus_again(self) -> None:
        """A network pause is over: SSO's check page is asked at once. Its answer is a school server's: it ends the
        school's unsure time, or lifts a pause told again by servers down on their own (second review 11-06)."""
        with self._campus_lock:
            self._campus_at = -math.inf

    def campus_queue(self) -> str | None:
        if not self.campus_url:
            return None
        return self.queue_of(urlsplit(self.campus_url).hostname or "")

    def _campus_system(self) -> str:
        """The host of the system the check page's cURL names (edocap.nknu.edu.tw), where it sends a visitor on
        campus."""
        for k, v in parse_qsl(urlsplit(self.campus_url or "").query):
            if k.lower() == "curl":
                return (urlsplit(v if "://" in v else f"https://{v}").hostname or "").lower()
        return ""

    def _campus_due(self) -> bool:
        """Whether the campus check is due: hourly, and at once when this machine's address changed, from which moment
        the requests say campus unknown until the check has been made (second review 10-26: they said what the last
        check found, on or off, for as long as SSO's queue was busy or paused)."""
        if not self.campus_url:
            return False
        if self.local_address() != self._campus_addr:
            if self._campus_addr is not None and self.campus != "unknown":
                self.campus = "unknown"
                self.frontier.event("info", "campus", "这台机器的地址变了：校内还是校外，等 SSO 的判断页再查一次之前记 unknown",
                                    None, self.campus_url)
            return True
        return self.clock() - self._campus_at >= CAMPUS_EVERY_S

    def _campus_step(self, queue: str, client: Client, sink: WarcSink) -> bool:
        """On the campus check page's own queue (SSO's), when due: one GET of SSO's check page. Off campus it says so
        (「您的IP 不在校內網路」); on campus it sends the visitor on (never followed). Every request row after it says
        where it was made from. Crawling goes on either way (the owner 10-26 「听你的」: 照抓、记下来、看板提醒). A
        check that cannot tell (no answer, an error page, another page) keeps what the last one found while this
        machine's address is the same (second review 11-06: on the lab machine an unknown handed the long-dead school
        servers' rows to the lab machine); what one that could tell found is kept in meta (CAMPUS_KEY)."""
        with self._campus_lock:
            if queue != self.campus_queue() or not self._campus_due():
                return False
            before = self._campus_at
            self._campus_addr, self._campus_at = self.local_address(), self.clock()
        try:
            got = self._request(queue, client, Target(self.campus_url), "campus", scope_reason="campus")
        except _Paused:  # a rest began on its queue: never sent, still due
            with self._campus_lock:
                self._campus_at = before
            raise
        if isinstance(got, Snapshot) and got.status in REDIRECTS:
            # on campus SSO sends the visitor on to the system cURL names; any other redirect (its error page, https)
            # says nothing of where we are (second review 10-26)
            to = scope.canonical(got.headers.get("location", ""), self.campus_url)
            state = "on" if to is not None and urlsplit(to).hostname == self._campus_system() else "unknown"
            if state == "unknown":
                self.frontier.event("warn", "campus", f"SSO 的校内判断页转去 {to or '（没有 Location）'}，不是 cURL 指的 "
                                    f"{self._campus_system()}：判断不出在校内还是校外", None, self.campus_url)
        elif isinstance(got, Snapshot) and got.status == 200:
            text = got.text if got.text is not None else _lenient(got.body, got.headers.get("content-type", ""))
            state = "off" if _CAMPUS_OFF.search(text or "") else "unknown"
            if state == "unknown":
                self.frontier.event("warn", "campus", "SSO 的校内判断页样子变了，判断不出在校内还是校外", None,
                                    self.campus_url)
        else:
            state = "unknown"
        if state == "unknown":  # it cannot tell: this machine did not move, so it is where the last check found
            state = self.campus
        else:
            self.frontier.meta(CAMPUS_KEY, json.dumps({"state": state, "addr": self._campus_addr}))
        if state != self.campus:
            if state == "on":
                self.frontier.event("warn", "campus", "这台机器在校内网络：有些页面校内看得到全文、校外只看到标题；照抓，"
                                    "每个请求都记下在校内抓的", None, self.campus_url)
            else:
                self.frontier.event("info", "campus", f"campus: {state} (这台机器{'在校外网络' if state == 'off' else '在哪里判断不出'})",
                                    None, self.campus_url)
        self.campus = state
        return True

    # ---- what came back
    def _store(self, queue: str, row, snap: Snapshot, sink) -> None:
        uid, ctype = row["id"], snap.headers.get("content-type", "").lower()
        kind0 = row["kind0"] or row["kind"]
        if not self._to_page(kind0, snap, ctype):
            self._forget_hint(row)  # its flags go (below), and pagetypes.observe will not see this answer
        # attempts 0: an answer came, so the tries that got none are not held against the row's next fetch (periodic
        # updates, stage 0a: design 2026-11-16 §7.1; a row first got on its third try would otherwise fail for good on
        # one timeout of a later fetch). Not so: the first look at the school's not-found page (says_step, below) and
        # a 3xx to a school error page (_redirect: _failed), each a try.
        cols: dict[str, Any] = {"state": "done", "reason": None, "status": snap.status, "final_url": snap.url,
                                "ctype": ctype[:100], "bytes": len(snap.body), "sha256": snap.sha256,
                                "warc": self._warc_path(sink), "warc_offset": snap.warc_offset,
                                "warc_len": snap.warc_len, "attempts": 0,
                                # what an answer says of itself is this answer's: a row fetched again (periodic updates,
                                # stage 1) keeps none of the last answer's (a 404 after a page: no title, no links, no
                                # thin mark); the paths below set what this answer has
                                "flags": None, "title": None, "sniff": None, "filename": None, "text_chars": None,
                                "n_links": None, "n_postbacks": None}
        if kind0 == outcome.SHORTLINK:
            self._shortlink(row, snap, cols)
            return
        if snap.status in REDIRECTS:
            self._redirect(queue, row, snap, cols)
            return
        if snap.status != 200:
            if snap.status >= 400:
                cols["reason"] = f"http_{snap.status}"
            self.frontier.finish(uid, **cols)
            return
        sniffed = sniff.sniff(snap.body, ctype)
        cols.update(sniff=sniffed, filename=sniff.filename(snap.raw_headers, snap.headers))
        flags: list[str] = []
        sig = outcome.error_signature(row["url"])
        if sig is not None and sig.result == outcome.GONE and sig.flag:
            flags.append(sig.flag)  # SSO's not-found page fetched directly (design §10.1)
        text = None
        if sniffed in ("html", "xml", "json", "text"):
            text = snap.text if snap.text is not None else _lenient(snap.body, ctype)
            if snap.target.meta.get("decode_error"):
                flags.append("decode")
        if kind0 in ("form", "embed") and sniffed == "html":
            cols["kind"] = "embed"  # the page's part, never a round-1 page itself (design §4.4, §7.6)
            if kind0 == "form" and ("closedform" in row["url"] or _FORM_CLOSED.search(text or "")):
                flags.append("form_closed")
            cols["flags"] = ",".join(flags) or None
            self.frontier.finish(uid, **cols)
            # what an embed box holds, and what a form's description holds (B12), count on the pages that use it
            self._part_answer(row, text or "", cols["warc"], snap.warc_offset, self._users_of(row))
            return
        if kind0 in outcome._EXPECTED_FILE:
            cols["kind"] = _KIND_OF0.get(kind0, "file") if sniffed == "html" else sniff.kind_of(sniffed)
            if sniffed == "html" and "<input" in (text or "").lower() and \
                    re.search(r"(?i)<input[^>]+type\s*=\s*['\"]?password", text or ""):
                flags.append("login")  # an attachment that sends a sign-in page (not_file otherwise: never followed)
            said = profiles.school_says(row["url"], text) if sniffed == "html" else None
            if said is not None:  # the school's own page says the file is missing, or its link broken (§10.2, B6)
                step = profiles.says_step(said, row["reason"], row["attempts"], self.clock())
                if "flags" in step:
                    flags.append(step.pop("flags"))
                cols.update(step)
                if cols["state"] == "pending":
                    cols["fetched_at"] = None  # looked at once more GONE_RECHECK_S later: the same twice is gone
            if kind0 == "image" and cols["kind"] == "file":  # a scan sent as a picture: the page's attachment
                self.frontier.x("UPDATE page_res SET role='file' WHERE res_id=? AND role='img'", (uid,))
            if kind0 == "sitemap" and sniffed != "html":
                self._sitemap(row, snap)
            content = None
            if kind0 == "click" and sniffed != "html":  # a tab pressed by its platform rule (§7.4): oia's JSON
                content = profiles.tab_content(text)
                if content is None:
                    flags.append("click_empty")  # success is not true, or the content is empty: 要人看 (thin)
            cols["flags"] = ",".join(dict.fromkeys(flags)) or None
            self.frontier.finish(uid, **cols)
            if content is not None:  # the tab's content is part of the pages that use it
                self._part_answer(row, content, cols["warc"], snap.warc_offset, self._users_of(row))
            return
        if sniffed != "html":  # a page address that answers a PDF, a picture, a JSON: a file (design §7.3)
            cols.update(kind=sniff.kind_of(sniffed), flags=",".join(flags) or None)
            self.frontier.finish(uid, **cols)
            return
        self._page(row, snap, text or "", cols, flags)

    @staticmethod
    def _to_page(kind0: str, snap: Snapshot, ctype: str) -> bool:
        """Whether _store sends this answer to _page (and so to pagetypes.observe)."""
        return (kind0 != outcome.SHORTLINK and snap.status == 200 and kind0 not in ("form", "embed")
                and kind0 not in outcome._EXPECTED_FILE and sniff.sniff(snap.body, ctype) == "html")

    def _forget_hint(self, row) -> None:
        """A row whose last answer carried a strong sign (js_hint, counted in its page type's n_strong) answered with
        what is no web page: the count takes it back, so the next page answer is counted once (observe takes the
        row's js_hint as already counted; review of 7f12e29..8474164)."""
        if row["page_type"] and pagetypes.HINT_FLAG in (row["flags"] or "").split(","):
            self.frontier.x("UPDATE page_type SET n_strong=max(n_strong - 1, 0) WHERE key=?", (row["page_type"],))

    def _shortlink(self, row, snap: Snapshot, cols: dict) -> None:
        """A short link's one GET (design §7.7, round1.short_answer): its target, read from a 3xx's Location or the
        service's 200 page, is admitted as if the page that names the short link had linked it directly."""
        text = None
        if snap.status == 200:
            cols["sniff"] = sniff.sniff(snap.body, cols.get("ctype") or "")
            if cols["sniff"] == "html":
                text = snap.text if snap.text is not None else _lenient(snap.body, cols.get("ctype") or "")
        answer = round1.short_answer(row["url"], snap.status, snap.headers.get("location"), text)
        cols["kind"] = outcome.SHORTLINK
        if snap.status >= 400 and snap.status not in (404, 410):
            cols["reason"] = f"http_{snap.status}"
        target = scope.canonical(answer.target) if answer.target else None
        if target is not None:
            page = self.frontier.row_by_id(row["found_from"])
            how = self._use_of(row)
            adm = self.admit(target, None, page, how)
            if adm is not None and adm.step == "google" and self.google is not None and page is not None:
                # a short link to Drive, Docs or a calendar: the pages that name it use what it leads to (§7.7)
                e = self._google_users(adm, page, row, how, "redirect", self._users_of(row))
            elif adm is not None:
                e = self._enqueue(target, placement=adm.placement, url=adm.url, from_row=page, depth=row["depth"],
                                  found_via="redirect", found_from=row["id"])
            else:
                e = Enqueued("bad_url", None, None, None)
            self.frontier.link(row["id"], target, "[shortlink]", e.verdict)
            cols["final_url"] = e.url or target
        self.frontier.finish(row["id"], **cols)

    def _use_of(self, row) -> str:
        """How the page uses this row (its page_res role): a frame, an image or a link."""
        got = self.frontier.q("SELECT role FROM page_res WHERE res_id=? LIMIT 1", (row["id"],))
        role = got[0][0] if got else None
        return {"iframe": "frame", "frame": "frame", "img": "image"}.get(role, "link")

    def _redirect(self, queue: str, row, snap: Snapshot, cols: dict) -> None:
        uid = row["id"]
        target = scope.canonical(snap.headers.get("location", ""), row["url"])
        if target is None:
            self.frontier.finish(uid, **cols)
            return
        sig = outcome.error_signature(target)
        if sig is not None:  # a school error page (design §10.1): never queued, it holds nothing of the school's
            self.frontier.link(uid, target, "[redirect]", outcome.ERROR_LINK)
            if sig.result == outcome.GONE:
                cols.update(flags=sig.flag, final_url=target)
                self.frontier.finish(uid, **cols)
            else:  # the server failed: retried as any GET, then server_error (the old archive's copy stands)
                self._failed(queue, row, kind=sig.reason or outcome.ERROR_LINK)
                self.frontier.x("UPDATE url SET status=?, final_url=? WHERE id=?", (snap.status, target, uid))
            return
        if target != row["url"] and self._scheme_rewrite(target) == row["url"]:
            self._unlearn_scheme(row["host"], target)  # it sends the visitor to the scheme it was moved away from
        google: list[dict] = []
        e = self._follow(row, target, "redirect", google)
        self.frontier.link(uid, target, "[redirect]", e.verdict)
        if e.verdict == "out:login" or urlsplit(target).hostname == "accounts.google.com":
            cols["flags"] = "login"  # the page sends visitors to a sign-in
        cols["final_url"] = e.url or target
        if google:  # a page sent to Drive, Docs or a calendar: what it leads to is all this reading of it uses
            self.frontier.set_page_res(uid, google)
            google_api.resync(self.frontier, uid)
        self.frontier.finish(uid, **cols)

    def _follow(self, row, target: str, found_via: str, entries: list[dict] | None = None) -> Enqueued:
        """Where a row sends the browser (a 3xx, a script or a refresh: design §8.3), no depth added. A resource's
        redirect stays that page's resource (Heritrix's TransclusionDecideRule), unless a rule leaves the address out;
        a page's is that page's link; a seed's redirect to another site is the same important site (it moved).

        A page sent to a Drive, Docs or Calendar address uses what it leads to as if it linked it (the Google step's
        rows, and their page_res entries added to `entries` for the caller to write on the page): its completeness is
        the item's, every file of a folder included, never the metadata row's 200 (review 10-26 phase 3, 【Claude 的执行
        办法】)."""
        kind0 = row["kind0"] or row["kind"]
        if kind0 in _TRANSCLUDE:
            how = {"image": "image", "embed": "frame"}.get(kind0, "link")
            adm = self.admit(target, None, row, how)
            if adm is not None and adm.step == "google" and self.google is not None:
                # an attachment, a frame or a picture that leads to Drive, Docs or a calendar: the pages that use the
                # row use what it leads to (its rows and their entries, design §7.6)
                return self._google_users(adm, row, row, how, found_via, self._users_of(row))
            if adm is not None and adm.step in ("shortlink", "google"):
                # the one way in (B7; second review 10-26): a short link is asked where it leads, a Google address
                # waits for the Google step, both as the page's resource (the resource row stands for the page)
                return self._enqueue(target, placement=adm.placement, url=adm.url, depth=row["depth"],
                                     found_via=found_via, found_from=row["id"])
            # a Google Sites runtime picture's hop (lh*-rt): the same picture, its Referer worked out from the same
            # page and policy, first on its own server's queue (design §23 B1)
            return self._enqueue(target, placement=self._transclude(target, row, kind0), url=target,
                                 depth=row["depth"], found_via=found_via, found_from=row["id"], kind0=kind0,
                                 prio=PRIO_HOP if row["referrer"] else None, referrer=row["referrer"])
        if (row["depth"] == 0 and row["found_via"] in _SEED_VIAS and row["owner_site"]
                and scope.site_key(target) != scope.site_key(row["url"])
                and not self.plan.adopt(target, row["owner_site"], row["url"])):
            self.frontier.event("info", "seed_moved", f"a seed of {row['owner_site']} redirects to {target}, which "
                                "is not taken as the same site: judged as any link", row["host"], row["url"])
        adm = self.admit(target, None, row, "link")
        if adm is None:
            return Enqueued("bad_url", None, None, None)
        if adm.step == "google" and self.google is not None:
            e, got = self._google(adm, from_row=row, how="link", depth=row["depth"], found_via=found_via,
                                  found_from=row["id"])
            if entries is not None:
                entries += got
            return e
        return self._enqueue(target, placement=adm.placement, url=adm.url, from_row=row, depth=row["depth"],
                             found_via=found_via)

    def _transclude(self, target: str, row, kind0: str) -> round1.Placement:
        """Where a resource's redirect goes that admit() places by the rules (no short link, no Google address): the
        same resource of the same page, unless a rule leaves the address out."""
        v = scope.judge(target)
        if not v.ok and v.reason not in scope.SOFT_OUT and not (kind0 == "script" and v.reason == "media"):
            return round1.Placement("out", f"out:{v.reason}", None, None, 0, kind0)
        return round1.Placement("resource", None, row["owner_site"], row["tier"], 1, kind0)

    def _good_before(self, uid: int) -> bool:
        """Whether a page held a good answer before this fetch (url_version: a kept 200 web page with none of the
        flags that make a page no good). Its resources then stay as they were when this time's answer is a sign-in,
        a soft 404 or an empty notice template, so that the files it linked still name it while the export falls back
        on its last good answer (export batch T1b; the content session's question 4, 11-16)."""
        for k in self.frontier.q("SELECT flags FROM url_version WHERE url_id=? AND status=200 AND kind='page' "
                                 "AND sniff='html'", (uid,)):
            if not set((k["flags"] or "").split(",")) & KEEP_RES_BAD:
                return True
        return False

    def _page(self, row, snap: Snapshot, text: str, cols: dict, flags: list[str]) -> None:
        """A web page of this round: its parts (page_res, _parts), its page type and the signs it reads (design §8.3:
        pagetypes.measure and observe, before finish), its platform's presses and walk (postback.page_stored, after
        finish), and what its embed boxes, forms, tabs and pressed details brought before (_replay_parts)."""
        uid, kind0 = row["id"], row["kind0"] or row["kind"]
        css = profiles.content_css(row["url"])  # the platform file's content area (design §7.2, §7.4)
        page = extract(text, snap.url, css)
        # its referrer policy: the answer's Referrer-Policy header, then its <meta name="referrer"> (design §23 B1)
        page.policy = links.referrer_policy(snap.headers.get("referrer-policy"), page.referrer_meta)
        # a GET-walked list's pages its platform file's pager box names (WebIdx: sort=date, review 10-26 phase 3; the
        # platform the page's own address is written on, gaps ② ③ of 11-08)
        page.pagers.extend(u for u in postback.profile_pagers(snap.url, text, page.base) if u not in page.pagers)
        h = self.frontier.host(row["host"])
        root = urlsplit(row["url"]).path in ("", "/") or row["found_via"] == "seed"
        if not root and h is not None and h["probe_status"] == 200 and (snap.sha256 == h["probe_sha"] or (
                page.title and page.title == (h["probe_title"] or "")
                and abs(len(snap.body) - (h["probe_len"] or 0)) <= min(100, 0.005 * len(snap.body)))):
            flags.append("soft404")  # looks exactly like this host's answer for a page that does not exist
        if page.login_form and (len(page.links) < LOGIN_MAX_LINKS or _LOGIN_TITLE.search(page.title)
                                or scope.LOGIN_PATH.search(urlsplit(row["url"]).path)):
            flags.append("login")
        elif page.login_form:
            cols["reason"] = "has_login_box"  # followed: the form is a sidebar box on an ordinary page
        if kind0 == "login_check" and "login" not in flags and _LOGIN_WORDS.search(text):
            flags.append("login")  # design §4.2: 請登入, 無權限, 請連線 VPN
        said = profiles.school_no_data(row["url"], text)  # export batch T5 ③: two the views read by a flag
        if said is profiles.CNKNU_FNF:
            flags.append("gone_page")  # c.nknu's whole answer 「404: File not found」: the school's not-found page
        elif said is profiles.NEWS_NO_ROWS:
            flags.append("no_rows")  # a notice list that says 「搜尋結果：共0筆」: nothing to hold, never thin
        if profiles.detail_of(row["url"]) is not None and profiles.empty_detail(row["url"], text):
            flags.append("empty_detail")  # design §10.1 nonempty: no body text, attachment, picture or related link
            probe = self.frontier.q("SELECT status, empty FROM id_probe WHERE host=? AND page_type=?",
                                    (row["host"], pagetypes.type_key(row["url"], row["site"])))
            if probe and profiles.gone_id(probe[0]["status"], bool(probe[0]["empty"]), True, True):
                flags.append("gone_id")  # the three at once: the probe's 200 empty detail, and this one empty too
        cols.update(kind="page", title=page.title, n_postbacks=len(page.buttons), text_chars=page.text_chars)
        redirect = page.js_redirect
        google: list[dict] = []  # what a script or a refresh sends the page to on Drive, Docs or a calendar
        if redirect is not None and (target := scope.canonical(redirect.href, page.base)) and target != row["url"]:
            tag = "[js_redirect]" if redirect.how == "script" else "[refresh]"
            sig = outcome.error_signature(target)
            if sig is not None:  # a school error page (§10.1), as a 3xx to it (_redirect): never queued; the page is
                # gone (gone_redirect) or the server's error (error_page: server_error, 连接失败), its words kept as
                # archived. Not retried as a 3xx's failure is: the page itself answered 200 (【Claude 的执行办法】)
                self.frontier.link(uid, target, tag, outcome.ERROR_LINK)
                flags += ["js_redirect", sig.flag if sig.result == outcome.GONE and sig.flag else "error_page"]
                cols["final_url"] = target
            else:
                e = self._follow(row, target, "js_redirect", google)
                self.frontier.link(uid, target, tag, e.verdict)
                flags.append("js_redirect")
                cols["final_url"] = e.url or target
                if e.verdict == "out:login" or urlsplit(target).hostname == "accounts.google.com":
                    flags.append("login")
        cols["flags"] = ",".join(dict.fromkeys(flags)) or None
        if "login" in flags or "soft404" in flags:  # archived as it is; not a place to look for more pages
            cols["n_links"] = 0
            if not self._good_before(uid):  # a page good before keeps what it linked (export batch T1b)
                self.frontier.set_page_res(uid, [])
            self.frontier.finish(uid, **cols)
            return
        if page.pagers:
            cols["page_role"] = "list"  # a list by its GET paging (design §7.5 generic); its walk is listwalk's
        entries, n = self._parts(row, page, uid, html=text, warc=cols.get("warc"), warc_offset=snap.warc_offset)
        entries += self._data_entries(row, text, cols.get("warc"), snap.warc_offset)  # design §23 C1
        keys = {e["res_key"] for e in entries}
        if "empty_detail" not in flags or not self._good_before(uid):  # a page good before keeps what it linked (T1b)
            self.frontier.set_page_res(uid, entries + [g for g in google if g["res_key"] not in keys])
        google_api.resync(self.frontier, uid)  # a folder or calendar read meanwhile brings what it holds
        # design §8.3: the page's type and its signs, after its page_res (a click it cannot press is read from it)
        # and before finish (observe reads the row's js_hint of its last fetch)
        m = pagetypes.measure(text, content_css=css, text_chars=page.text_chars)
        seen = pagetypes.observe(self.frontier, url_id=uid, url=row["url"], m=m, site=row["site"])
        flags += seen.flags
        cols["flags"] = ",".join(dict.fromkeys(flags)) or None
        cols["n_links"] = n
        self.frontier.finish(uid, **cols)
        # design §7.5: the platform's presses of this page as archived (actions), and how its list is walked
        stored = self.chains.page_stored(self.frontier.row_by_id(uid), text, cols.get("warc"), snap.warc_offset, page)
        if stored.page_role == "list" and cols.get("page_role") != "list":
            self.frontier.x("UPDATE url SET page_role='list' WHERE id=?", (uid,))
        elif stored.page_role is None and stored.content:  # a table of content (FIX 3, 11-06): no list any more
            self.chains.not_a_list(uid)
        if stored.unplanned:  # buttons the platform file says to press that no press of this page covers: 要人看
            self.frontier.add_page_res(uid, postback.unpressed_entries(stored.unplanned, stored.texts))
        self._replay_parts(uid)

    # ---- what a page's parts brought (design §4.4, §7.4, §7.5, §7.6)
    def _users_of(self, row) -> list[int]:
        """The pages that use a resource row (their page_res points at it), or the page it was found on. A row found
        from another resource (a redirect's target, what a short link leads to) is used by that resource's pages:
        page_res is a page's, never written on an attachment's or a short link's row (review 10-26 phase 3)."""
        cur, seen = row, {row["id"]}
        for _ in range(outcome.MAX_HOPS):
            ids = [r[0] for r in self.frontier.q("SELECT DISTINCT page_id FROM page_res WHERE res_id=? ORDER BY "
                                                 "page_id", (cur["id"],))]
            if ids:
                return [i for i in ids if i != row["id"]]
            up = cur["found_from"]
            if up is None or up in seen:
                return []
            seen.add(up)
            parent = self.frontier.row_by_id(up)
            if parent is None or parent["kind"] == "page":
                return [up]
            cur = parent
        return []

    def _part_answer(self, row, text: str, warc: str | None, warc_offset: int | None, page_ids: list[int]) -> None:
        """What a page's part brought counts on the pages that use it, their parts and their words (design §4.4): a
        Google Sites embed box (kind0 embed), a form's description (form: google_api.form_parts, B12: its attachments,
        Drive files and other forms, never its ordinary links; its words are the form's, not the page's) and a tab's
        content its platform rule pressed for (click: oia's load_content.php, read as part of the page, relative
        addresses by the page's own)."""
        kind0 = row["kind0"] or row["kind"]
        for pid in page_ids:
            parent = self.frontier.row_by_id(pid)
            if parent is None:
                continue
            if kind0 == "form":
                page = google_api.form_parts(text, row["url"])
            else:
                page = extract(text, parent["url"] if kind0 == "click" else row["url"])
            entries, _n = self._parts(parent, page, row["id"], html=text, warc=warc, warc_offset=warc_offset)
            self.frontier.add_page_res(pid, entries)
            google_api.resync(self.frontier, pid)
            if kind0 in ("embed", "click"):
                self.frontier.x("UPDATE url SET text_chars = coalesce(text_chars, 0) + ? WHERE id=?",
                                (page.text_chars, pid))

    def _replay_parts(self, page_id: int) -> None:
        """A page fetched again writes its page_res anew; what its parts brought when they were fetched (an embed box,
        a form, a tab, a file button's address, a detail pressed by POST: none of them is fetched or pressed again)
        is read back from the archive, so that it is never lost from the page unseen. Sends nothing."""
        page = self.frontier.row_by_id(page_id)
        if page is None:
            return
        for r in self.frontier.q(
                "SELECT u.* FROM page_res pr JOIN url u ON u.id = pr.res_id WHERE pr.page_id=? AND u.state='done' "
                "AND u.status=200 AND u.kind0 IN ('embed', 'form', 'click') AND u.warc IS NOT NULL "
                "AND u.warc_offset IS NOT NULL", (page_id,)):
            text = self._archived_text(r["warc"], r["warc_offset"])
            if text is None:
                continue
            if (r["kind0"] or "") == "click":
                text = profiles.tab_content(text)
                if text is None:
                    continue
            elif r["sniff"] != "html":
                continue
            self._part_answer(r, text, r["warc"], r["warc_offset"], [page_id])
        for r in self.frontier.q(
                "SELECT DISTINCT u.* FROM page_res pr JOIN url u ON u.id = pr.res_id WHERE pr.page_id=? AND "
                "u.state='done' AND u.final_url IS NOT NULL AND u.final_url <> u.url AND "
                "(u.status BETWEEN 300 AND 399 OR u.kind0='shortlink')", (page_id,)):
            self._replay_google(page, r)
        files = [{"res_key": a["location"], "how": "get", "role": "file", "need": "must", "res_id": u["id"]}
                 for a in self.frontier.q("SELECT a.location FROM page_res pr JOIN action a ON a.id = pr.action_id "
                                          "WHERE pr.page_id=? AND a.kind='click' AND a.state='done' "
                                          "AND a.location IS NOT NULL", (page_id,))
                 if (u := self.frontier.row(a["location"])) is not None]
        if files:
            self.frontier.add_page_res(page_id, files)
        # the files its download presses were sent to (gap ① of 11-08): queued as the page's attachments, as the
        # answer itself queues them (_press_answer); the presses round one took for a lapsed session are adopted here
        for loc in self.chains.file_redirects(page_id):
            e = self._resource_of(loc, page, "redirect", page, [page_id], kind0="file")
            self.frontier.link(page_id, loc, "[postback]", e.verdict)
        for a in self.frontier.q("SELECT * FROM action WHERE site=? AND kind='data' AND state='done' AND reason "
                                 "LIKE 'read:events:%' AND warc IS NOT NULL AND warc_offset IS NOT NULL",
                                 (page["owner_site"] or page["site"],)):
            if self._site_home(a["site"], self.frontier.row_by_id(a["list_id"]) or page)["id"] != page_id:
                continue  # its events' links are the site's home page's (design §23 C1)
            api = profiles.data_api((a["profile"] or "").partition("/")[2])
            text = self._archived_text(a["warc"], a["warc_offset"]) if api is not None else None
            if text is not None and (read := profiles.read_data_api(api, text)).html:
                self._data_links(page, read.html)
        for a in self.frontier.q("SELECT * FROM action WHERE list_id=? AND kind='detail' AND state='done' "
                                 "AND sniff='html' AND warc IS NOT NULL AND warc_offset IS NOT NULL", (page_id,)):
            text = self._archived_text(a["warc"], a["warc_offset"])
            if text is not None:
                got = extract(text, page["url"], profiles.content_css(page["url"]))
                entries, _n = self._parts(page, got, page_id, html=text, warc=a["warc"], warc_offset=a["warc_offset"])
                self.frontier.add_page_res(page_id, entries)
                google_api.resync(self.frontier, page_id)

    def _replay_google(self, page, row) -> None:
        """A resource of the page that redirects (a 3xx, a short link's target) and, hop by hop, ends on a Drive, Docs
        or Calendar address: that address's entries on the page again (the Google step's rows, all known already), as
        when the redirect was first read (_follow, _shortlink → _google_users), so that the page fetched again keeps
        them (review 10-26 phase 3). Sends nothing."""
        how = self._use_of(row)
        cur, seen = row, set()
        for _ in range(outcome.MAX_HOPS):
            if cur is None or cur["id"] in seen or cur["state"] != "done":
                return
            seen.add(cur["id"])
            to = cur["final_url"]
            if not to or to == cur["url"] or not (cur["status"] in REDIRECTS
                                                  or (cur["kind0"] or cur["kind"]) == outcome.SHORTLINK):
                return
            for link in self.frontier.q("SELECT dst FROM link WHERE src=?", (cur["id"],)):  # [redirect], [shortlink]
                adm = self.admit(link["dst"], None, page, how)
                if adm is not None and adm.step == "google" and self.google is not None and adm.url == to:
                    self._google_users(adm, page, cur, how, "redirect", [page["id"]])
                    return
            cur = self.frontier.row(to)

    def _archived_text(self, warc: str, offset: int) -> str | None:
        """A response record's body as text (postback.read_response), None when it cannot be read back."""
        try:
            _url, _status, headers, body = postback.read_response(self.root, warc, offset)
        except (OSError, ValueError, StopIteration):
            return None
        return postback.as_text(body, headers.get("content-type"))

    def _sitemap(self, row, snap: Snapshot) -> None:
        """A sitemap (sitemaps.org; design §5 ⑥): an index's sitemaps are sitemaps again, a list's addresses are
        queued like links (leaf 0). Read with a pattern, never an XML parser that would expand entities."""
        body = snap.body
        if body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)[:MAX_BYTES]
            except (OSError, EOFError):
                return
        text = body.decode("utf-8", "replace")
        index = "<sitemapindex" in text[:2000].lower()
        for loc in _LOC.findall(text):
            href = htmllib.unescape(loc).strip()
            if index:
                e = self._enqueue(href, from_row=row, depth=row["depth"], found_via="link", prio=PRIO_LIST,
                                  kind0="sitemap")
            else:
                e = self._enqueue(href, from_row=row, depth=row["depth"] + 1, found_via="link")
            if (canon := scope.canonical(href)) is not None:
                self.frontier.link(row["id"], canon, "[sitemap]", e.verdict)

    def _parts(self, row, page: links.PageLinks, src_id: int, *, html: str | None = None, warc: str | None = None,
               warc_offset: int | None = None) -> tuple[list[dict], int]:
        """Admit what a page links and uses (design §4.4, §7.1, §7.2, §7.8): its links (the link table), and a
        page_res entry for each thing it uses, with its need and why. row: the page these count for (an embed box's
        parent); src_id: where they were read (the link table's src). html: what was read (a click rule reads the
        bound elements' values from it); warc, warc_offset: where it is archived (a file button's press keeps it as
        its evidence). A Drive, Docs or Calendar address the Google step takes makes its rows and entries
        (google_api.uses, attach: design §7.6); a click its platform rule presses is a GET row or a POST press
        (profiles.click_plan, design §7.4). Returns (page_res entries, links seen)."""
        base, url = page.base, row["url"]
        entries: dict[str, dict] = {}
        google_done: set[str] = set()

        def put(key: str, **e) -> None:
            entries.setdefault(key, {"res_key": key, **e})

        def admit(href: str, how: str, depth_: int, zone: str | None = None, text: str | None = None,
                  prio: int | None = None) -> Enqueued:
            adm = self.admit(href, base, row, how)
            if adm is None:
                return Enqueued("bad_url", None, None, None)
            if adm.step == "google" and self.google is not None:
                e, got = self._google(adm, from_row=row, how=how, depth=depth_, found_via="link", zone=zone,
                                      text=text)
                for x in got:
                    entries.setdefault(x["res_key"], x)
                google_done.add(adm.link)
                return e
            return self._enqueue(href, base=base, placement=adm.placement, url=adm.url, from_row=row, how=how,
                                 depth=depth_, found_via="link", prio=prio)

        def use(key: str, e: Enqueued, role: str, zone: str | None = None, text: str | None = None) -> None:
            pl = e.placement
            if pl is None:
                return
            if pl.verdict == "unhandled":
                put(f"google:{key}", how="unhandled", role="calendar" if "calendar" in key else "drive",
                    need="must", why=pl.reason, zone=zone, text=text)
            elif pl.verdict == "out":
                put(key, how="link_only", role=role, need="must", why=pl.reason, zone=zone, text=text)
            elif e.id is not None:
                put(key, how="get", role=role, need="round2" if pl.verdict == "round2" else "must", res_id=e.id,
                    zone=zone, text=text)

        depth = row["depth"] + 1
        gsimg = links.google_sites_keys(page, src_id)  # Google Sites' runtime pictures, by where they sit (§23 B2)
        for img in page.images:
            if img.src.startswith("data:"):
                put(img.src, how="get", role="img", need="no", why="data", zone=img.zone, text=img.alt)
                continue
            target = scope.canonical(img.src, base)
            if target is None:
                continue
            need, why = links.image_need(img, url, page.profiled)
            if img.src in gsimg:
                entries.setdefault(gsimg[img.src], self._gsimg_part(row, page, img, gsimg[img.src], need, why, depth))
                continue
            if need != "must":  # no, with why
                put(target, how="get", role="img", need=need, why=why, zone=img.zone, text=img.alt)
                continue
            e = admit(img.src, "image", depth, img.zone, img.alt)
            if target not in google_done:
                use(target, e, "img", img.zone, img.alt)
        frame_srcs = set()
        for fr in page.frames:
            target = scope.canonical(fr.src, base)
            if target is None:
                continue
            frame_srcs.add(fr.src)
            e = admit(fr.src, "hidden" if fr.hidden else "frame", depth, fr.zone, fr.title)
            if e.verdict != "out:internal_service":
                self.frontier.link(src_id, target, "[frame]" if fr.tag in ("iframe", "frame") else "[embed]",
                                   e.verdict)
            if target in google_done:
                continue
            role = "frame" if fr.tag == "frame" else "iframe"
            if e.placement is not None and e.placement.kind0 == outcome.SHORTLINK:
                role = "shortlink"
            use(target, e, role, fr.zone, fr.title)
        pagers, n = set(page.pagers), 0
        pictures = {scope.canonical(img.src, base) for img in page.images}
        for href, text in page.links:
            if text in ("[frame]", "[embed]") and href in frame_srcs:
                continue
            target = scope.canonical(href, base)
            if target is None:
                continue
            n += 1
            if target in pictures:
                continue  # an <a> straight to a picture: the images above, by its need
            pager = target in pagers
            e = admit(href, "link", row["depth"] if pager else depth, page.link_zone.get(href), text,
                      prio=PRIO_LIST if pager else None)
            if e.verdict == "out:internal_service":
                continue  # not even in the link table
            self.frontier.link(src_id, target, text, e.verdict)
            pl = e.placement
            if pl is not None and pl.reason in ("round2:offsite", "round2:offsite_file"):
                self.frontier.offsite(urlsplit(target).hostname or "", src_id, target[:300])
            if pl is None or target in google_done:
                continue
            if pl.verdict == "resource" or pl.kind0 in _USED_KINDS or (
                    pl.verdict == "unhandled" and pl.reason == round1.GOOGLE_UNHANDLED):
                use(target, e, _ROLE_OF0.get(pl.kind0, "file"), page.link_zone.get(href), text)
            elif pager and e.id is not None:
                put(target, how="get", role="pager", need="must", res_id=e.id, text=text)
        for src in page.scripts:
            target = scope.canonical(src, base)
            if target is None:
                continue
            e = self._enqueue(src, base=base, from_row=row, how="script", depth=depth, found_via="link")
            if e.id is not None:
                put(target, how="get", role="script", need="no", why="script", res_id=e.id)
        # the site these count for keys the postback table and a file button's action; the platform the page's own
        # address is written on decides (gaps ② ③ of 11-08: a /site/ key's WebIdx pages are WebIdx's)
        site, plat = row["owner_site"] or "", scope.site_key(url)
        for c in page.clicks:
            # the platform file's clicks rule reads the click's writing exactly (handle), else its decision (§7.8)
            decision = (profiles.click_decision(plat, c, html, url) if self.decide is profiles.decision
                        else self.decide(plat, c.key))
            plan = (profiles.click_plan(plat, c, html, url)
                    if decision == "handle" and row["tier"] in round1.TIER_OF_IMP.values() else None)
            if plan is not None and plan.method == "GET":  # oia's tab: one GET per tab, the shown one too (§7.4)
                e = self._enqueue(plan.url, from_row=row, how="click", depth=depth, found_via="click")
                self.frontier.link(src_id, plan.url, "[click]", e.verdict)
                entry = postback.click_entry(plan, res_id=e.id)
                if e.id is None:  # a rule leaves the tab's address out: what it says is the tab's result
                    entry["why"] = (e.placement.reason if e.placement is not None else None) or "unhandled:unknown"
                entries.setdefault(plan.res_key, entry)
                continue
            if plan is not None:  # ee's file button: one POST per (k, number) of the site, a press (§7.4)
                aid = self.chains.click_action(row, plan, warc, warc_offset)
                entries.setdefault(plan.res_key, postback.click_entry(plan, action_id=aid))
                continue
            need, why = links.click_need(c, "round2" if decision == "handle" else decision)
            put(c.key, how="click", role="click", need=need, why=why or None, zone=c.zone, text=c.text)
        for u in page.unhandled:
            need, why = links.unhandled_need(self.decide(plat, u.key))
            put(u.key, how="unhandled", role="click", need=need, why=why, zone=u.zone, text=u.text)
            if u.auto:  # §8.4: the page fires it on load; jscheck opens the page (outcome.AUTO_KEY)
                put(outcome.AUTO_KEY + u.key, how=outcome.AUTO_HOW, role="click", need="no", why=outcome.AUTO_WHY,
                    zone=u.zone, text=u.text)
        for b in page.buttons:
            # with its argument: MasterGrid's Page$N is pressed, its Sort$x is not (profiles._button_key); with its
            # text: a rule that names one holds for that text only (SSO's bare ctlNN 「查詢」, review 10-26 phase 3)
            key = postback.button_key(b.target, b.argument)
            decision = (profiles.decision(plat, key, b.text) if self.decide is profiles.decision
                        else self.decide(plat, key)) or "unknown"
            self.frontier.postback(site, b.target, src_id, b.argument, b.text, b.form_id,
                                   decision if decision in ("press", "never", "ignore") else "unknown")
            if decision == "press":
                continue  # pressed by postback.py (page_stored plans it from the archived page), an action row
            need, why = links.unhandled_need(decision)
            old = entries.get(key)
            if old is None or (old["need"] == "no" and need != "no"):
                # one key, several buttons (ctlNN as ctl*): the one that still needs something is the one written
                entries[key] = {"res_key": key, "how": "unhandled", "role": "click", "need": need, "why": why,
                                "zone": b.zone, "text": b.text}
        for v in page.videos:
            self.frontier.video(row["id"], v)
        return list(entries.values()), n


def _utc():
    from datetime import UTC, datetime

    return datetime.now(UTC)
