"""Opening a page in a browser and archiving what it shows (spec 2026-11-17 jsfetch: docs/superpowers/specs/
2026-11-17-jsfetch-design.md): round two's first batch, the five sites whose content (or the links to it) a page's own
script builds — w3, ord, ite, gauss, see (owner 11-16: 「不用 Browsertrix，直接扩充现有的浏览器检查工具来抓那 5 个纯 JS
网站」; owner 11-17: 做法 1).

The browser check's parts are reused as jscheck.SpotChecker does, jscheck.py itself untouched (spec §4.2): a JsCheck of
purpose jsfetch, its Sender (the crawl's client, one connection per server queue, one request at a time, the gap after
each answer, a host's Crawl-delay, the servers' rest table; every request a request row, its answer in the Sender's WARC
file jsfetch-<queue>-<run>), its PageBridge and PlaywrightBrowser. Added here:
- a page asks only its own host (OwnHostBridge): another host's frame, script or data request is never sent, its link
  stays in what the page shows for the crawler to judge by its own rules (spec §4.2);
- a cap on requests a host per fetcher (CappedSender, `--max-per-site`: the owner's limit for trial runs, 11-17
  「每站最多 10 个，由程序硬性卡住，到上限后面的请求一律不送」);
- what the page shows is archived as a WARC conversion record pointing at the page's own answer (RenderedWriter,
  read_rendered), and each look is a row of js_render (spec §4.3);
- why a look saw less than the page holds (incomplete_reason, spec §4.5), and which pages a pass opens (select_pages,
  spec §4.4);
- the pass and its commands: `python -m nknu.archive.jsfetch run|render|status` (spec §4.6; never registered under
  `nknu archive`: cli.py is the crawler session's, owner 11-16).

Following links, fetching what a rendered page links, exporting the rendered page and the nightly order are the
crawler's (spec §5).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import io
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

from warcio.archiveiterator import ArchiveIterator
from warcio.warcwriter import WARCWriter

from nknu.archive import jscheck, links, scope
from nknu.archive.crawler import CAMPUS_URL, GAP, AlreadyRunning, check_gap, hold_run_lock, local_address, resolve
from nknu.archive.frontier import Frontier, rehearsal_of, round_of, wall
from nknu.archive.jscheck import Answer, Ask, Browser, JsCheck, Observation, PageBridge, PlaywrightBrowser, Sender
from nknu.crawl import schoollock
from nknu.crawl.fetch import Client
from nknu.crawl.warc import WarcSink

# a picture by its address, whatever resource type the browser gives it (a site's icon is asked as "other"): the crawler
# fetches the pictures a shown page links, a look never needs them (11-17 offline trial: ord's /images/logo.png on every
# page) 【Claude】
_PICTURE = re.compile(r"(?i)\.(png|jpe?g|gif|ico|svg|webp|bmp)$")
MENU_PAGES = 3  # 【Claude】 a shown-only link on this many of a site's pages is the site's menu, no page's own
TEXT_CHARS = 100  # 【Claude】 this much text only the browser shows makes a page "text" even below judge's js line (200):
# ord's notice pages show 150–250 characters of body (11-17 offline trial: 199)
TEXT_EVERY_DAYS = 7  # spec §4.4: a page whose text only the browser shows, at least weekly (the update's standing pages)
PURPOSE = "jsfetch"  # request.purpose of every request a look sends, and the Sender's WARC files' name
RENDERED = "jsfetch-rendered"  # the rendered pages' WARC files: warc/YYYY/MM/DD/jsfetch-rendered-<run>.warc.gz

JS_RENDER = """
CREATE TABLE IF NOT EXISTS js_render(
  url_id INTEGER NOT NULL, run INTEGER NOT NULL, at TEXT NOT NULL, fetched_at TEXT, final_url TEXT, warc TEXT,
  warc_offset INTEGER, raw_chars INTEGER, chars INTEGER, new_chars INTEGER, verdict TEXT,
  links TEXT NOT NULL DEFAULT '[]', incomplete TEXT, strike INTEGER NOT NULL DEFAULT 0, requests TEXT,
  PRIMARY KEY(url_id, run)) WITHOUT ROWID;
"""
# spec §4.3, with one change made while planning (plan 2026-11-17 Task 1, 【Claude】): verdict and links are kept
# instead of a stored usefulness, which select_pages works out from every latest look of a site (a menu shown on every
# page is the site's, not a page's own)


@dataclass
class Rendered:
    """One look at a page (spec §4.2): what the browser showed and how it got there."""

    url: str
    final_url: str = ""
    html: str = ""
    log: list = field(default_factory=list)  # jscheck.Exchange, one per request the page made
    verdict: str | None = None  # jscheck.judge's: js, ok, frame, click, None (no conclusion)
    raw_chars: int = 0  # the page's own answer's text (judge's direct_chars)
    chars: int = 0  # the shown page's text
    new_chars: int = 0  # text shown that the page's own answer does not hold, each repeat once
    links: list[str] = field(default_factory=list)  # same-site links shown that the page's own answer does not hold
    incomplete: str | None = None  # why the look saw less than the page holds (spec §4.5)
    strike: bool = False  # the look failed for a reason of the page's or the server's, not one of our rules'
    warc: str | None = None  # the rendered record, relative to the archive directory
    warc_offset: int | None = None
    error: str | None = None  # the page's own answer failed: nothing archived as rendered
    requests: dict[str, Any] = field(default_factory=lambda: {"sent": 0, "unsent": {}})


# ---------------------------------------------------------------- the rendered page in WARC (spec §4.3)


class RenderedWriter:
    """warc/YYYY/MM/DD/jsfetch-rendered-<run>.warc.gz: one gzip member per record, appended whole or not at all (as
    crawl.warc.WarcSink: on any error the file is cut back to where it was, since half a gzip member would hide every
    record after it)."""

    def __init__(self, root: Path, run_id: int, now: datetime):
        self.root = Path(root)
        d = self.root / "warc" / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{RENDERED}-{run_id}.warc.gz"
        self._fh = open(self.path, "ab", buffering=0)  # noqa: SIM115 - closed in close()

    def write(self, url: str, html: str, refers_to: str | None) -> tuple[str, int]:
        """A conversion record of url (WARC 1.1: another version of a record's content) holding html; refers_to: the
        WARC-Record-ID of the page's own answer, when known. Returns (path relative to root, offset)."""
        buf = io.BytesIO()
        w = WARCWriter(buf, gzip=True)
        heads = {"WARC-Refers-To": refers_to} if refers_to else {}
        rec = w.create_warc_record(url, "conversion", payload=io.BytesIO(html.encode("utf-8")),
                                   warc_content_type="text/html; charset=utf-8", warc_headers_dict=heads)
        w.write_record(rec)
        start = os.fstat(self._fh.fileno()).st_size
        try:
            self._raw_write(buf.getvalue())
        except BaseException:
            with contextlib.suppress(OSError):
                os.ftruncate(self._fh.fileno(), start)
            raise
        return str(self.path.relative_to(self.root)), start

    def _raw_write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            n = self._fh.write(view)
            if not n:
                raise OSError("the WARC file took no bytes")
            view = view[n:]

    def close(self) -> None:
        self._fh.close()


def read_rendered(root: Path, warc: str, offset: int) -> tuple[str, str]:
    """(url, html) of the rendered record at offset (the export's reader, spec §5 ⑤). Anything else there — no record,
    a broken one, a response — is a ValueError, so that a reader's except holds."""
    try:
        with open(Path(root) / warc, "rb") as fh:
            fh.seek(offset)
            rec = next(iter(ArchiveIterator(fh)))
            if rec.rec_type != "conversion":
                raise ValueError(f"{warc}@{offset} is a {rec.rec_type} record, not a rendered page")
            url = rec.rec_headers.get_header("WARC-Target-URI") or ""
            body = rec.content_stream().read()
    except ValueError:
        raise
    except Exception as exc:  # warcio's load failures, a short file, a bad gzip member
        raise ValueError(f"{warc}@{offset}: no record can be read there ({type(exc).__name__})") from exc
    return url, body.decode("utf-8")


def record_id_at(root: Path, warc: str, offset: int) -> str | None:
    """The WARC-Record-ID of the record at offset, None when nothing can be read there."""
    try:
        with open(Path(root) / warc, "rb") as fh:
            fh.seek(offset)
            rec = next(iter(ArchiveIterator(fh)))
            if not str(rec.format).startswith("warc"):
                return None
            return rec.rec_headers.get_header("WARC-Record-ID")
    except Exception:  # noqa: BLE001 - whatever stops reading: no id
        return None


# ---------------------------------------------------------------- the ledger's js_render (spec §4.3)


def ensure_table(fr: Frontier) -> None:
    """js_render made when missing (this module's own table, outside frontier.TABLES: a reader that never looked
    through a browser has none)."""
    with fr.lock:
        fr.conn.executescript(JS_RENDER)


def record(fr: Frontier, row: sqlite3.Row, got: Rendered, run_id: int, *, at: datetime) -> None:
    """One look at row's page, as a new row of js_render (rows are added, never replaced)."""
    fr.x("INSERT INTO js_render(url_id, run, at, fetched_at, final_url, warc, warc_offset, raw_chars, chars, new_chars, "
         "verdict, links, incomplete, strike, requests) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (row["id"], run_id, at.isoformat(timespec="seconds"), row["fetched_at"], got.final_url or None, got.warc,
          got.warc_offset, got.raw_chars, got.chars, got.new_chars, got.verdict,
          json.dumps(sorted(set(got.links)), ensure_ascii=False), got.incomplete, int(got.strike),
          json.dumps(got.requests, ensure_ascii=False, sort_keys=True)))


# ---------------------------------------------------------------- one look (spec §4.2, §4.5)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


class OwnHostBridge(PageBridge):
    """jscheck's bridge, asking only the page's own host (spec §4.2): another host's frame, script or data request is
    never sent (other_host). What it links stays in the shown page, for the crawler's own rules."""

    def _blocked(self, a: Ask) -> str | None:
        why = super()._blocked(a)
        if why is None and not a.navigation and _PICTURE.search(urlsplit(a.url).path or ""):
            return "type"
        if why is None and _host(a.url) != _host(self.url):
            return "other_host"
        return why


class CappedSender(Sender):
    """jscheck's Sender, with at most max_per_host requests on the wire to a host for this fetcher's life
    (`--max-per-site`, owner 11-17: 「每站最多 10 个，由程序硬性卡住，到上限后面的请求一律不送」; each of the five
    sites is one host). What counts is each request row the Sender writes (_log: a resend on a new connection after the
    server closed the kept one is a row too, review 11-17 ⑥), plus the requests in flight, so that two at once never
    pass the cap. None: no cap."""

    def __init__(self, check: JsCheck, max_per_host: int | None):
        super().__init__(check)
        self.max_per_host = max_per_host
        self._wire: Counter[str] = Counter()
        self._flying: Counter[str] = Counter()
        self._count_lock = threading.Lock()

    async def send(self, a: Ask) -> Answer:
        host = _host(a.url)
        if not self._goes_out(a):  # never sent (a JSON body, a method): it takes no place under the cap (lab trial 11-17)
            return await super().send(a)
        with self._count_lock:
            if self.max_per_host is not None and self._wire[host] + self._flying[host] >= self.max_per_host:
                return Answer(unsent="cap:site")
            self._flying[host] += 1
        try:
            return await super().send(a)
        finally:
            with self._count_lock:
                self._flying[host] -= 1

    def _goes_out(self, a: Ask) -> bool:
        """Whether the Sender would send a at all (jscheck._target makes a request of it, as _send asks it; with the
        crawler session's json switch once it is there, spec §5 ②)."""
        if "json" in inspect.signature(jscheck._target).parameters:
            made = jscheck._target(a, json=getattr(self.check, "json_post", False))
        else:
            made = jscheck._target(a)
        return not isinstance(made, str)

    def _log(self, lane, host: str, url: str, started: float, got, purpose: str | None = None) -> None:
        super()._log(lane, host, url, started, got, purpose)
        with self._count_lock:
            self._wire[host.lower()] += max(len(getattr(got, "sent", ()) or ()), 1)

    def sent_to(self, host: str) -> int:
        with self._count_lock:
            return self._wire[host.lower()]

    def capped(self, host: str) -> bool:
        with self._count_lock:
            return self.max_per_host is not None and (
                self._wire[host.lower()] + self._flying[host.lower()] >= self.max_per_host)


def incomplete_reason(obs: Observation) -> tuple[str | None, bool]:
    """Why a look saw less than the page holds, and whether that is a strike (spec §4.5): a strike is the page's or the
    server's failure (too slow, never quiet, the browser could not read the page, a needed request or a later
    navigation sent with no usable answer); what our own rules cause (another host, a JSON body the client does not
    send yet, the cap) or a rest or stop of ours is none, so that it never drops a page from later passes. None:
    complete. 【Claude】 the order, the first reason found wins (review 11-17 ①②: the browser's own error and a later
    navigation are reasons too)."""
    navs = [e for e in obs.log if e.navigation]
    for e in navs:
        if e.blocked == "other_host":
            return f"moved:{e.url}", False
    final = obs.final_url or ""
    if final.startswith(("http://", "https://")) and _host(final) != _host(obs.url):
        return f"moved:{final}", False
    if obs.error:
        return f"error {obs.error}"[:300], True
    for e in obs.log:
        if jscheck.data_post(e, obs.url):
            return f"post_body {e.url}", False
    for e in obs.log:
        if e.kind == "script" and e.blocked == "other_host":
            return f"other_host {e.url}", False
    if any(e.unsent == "cap:site" for e in obs.log):
        return "cap:site", False
    if obs.slow:
        return "slow", True
    if obs.not_idle:
        return "not_idle", True
    for e in navs[1:]:  # a 3xx's next hop, a script sending the page on: the page's own, whatever its kind says
        if e.error:
            return f"nav {e.url[:200]}: {e.error}", True
        if e.unsent or e.blocked:
            return f"nav {e.url[:200]}: {e.unsent or e.blocked}", False
    for e in obs.log:
        if e.navigation or e.kind not in jscheck.NEEDED_KINDS or jscheck.read_api(e, obs.url):
            continue
        if e.error:
            return f"{e.kind} {e.url[:200]}: {e.error}", True
        if e.unsent:
            return f"{e.kind} {e.url[:200]}: {e.unsent}", False
        if e.blocked in jscheck.NEVER_SEND:
            return f"{e.kind} {e.url[:200]}: not_sent:{e.blocked}", False
    if not final.startswith(("http://", "https://")):
        return "load_failed", True
    return None, False


def _site_links(html: str, url: str) -> set[str]:
    """The canonical addresses html links (a href) on url's own site."""
    canon = scope.canonical(url) or url
    site = scope.site_key(canon)
    pl = links.extract(html, url)
    out: set[str] = set()
    for href, _text in pl.links:
        target = scope.canonical(href, pl.base)
        if target is not None and scope.site_key(target) == site:
            out.add(target)
    return out


def _requests(log) -> dict[str, Any]:
    """What a look sent (each request on the wire once; a script from the run's cache is none) and what it did not,
    by reason."""
    sent = sum(1 for e in log if not e.cached and not e.blocked and not e.unsent
               and (e.status is not None or e.error is not None))
    unsent = Counter(e.blocked or e.unsent for e in log if e.blocked or e.unsent)
    return {"sent": sent, "unsent": dict(sorted(unsent.items()))}


class JsFetcher:
    """One page at a time (spec §4.2): jscheck's browser, dead proxy, bridge and queues, as SpotChecker uses them,
    with purpose jsfetch, the page's own host only and the cap. Called as fetcher(url) -> Rendered inside a with
    block; the browser lives on a loop of its own thread. It writes the Sender's request rows and WARC, and the
    rendered records (RenderedWriter); js_render rows are the pass's (record). The caller holds the machine-wide lock
    (and run.lock when fr is a crawl's ledger)."""

    def __init__(self, root: Path, fr: Frontier, *, browser: Browser | None = None, gap: tuple[float, float] = GAP,
                 client_factory: Callable[[WarcSink | None], Client] | None = None,
                 resolver: Callable[[str], tuple] = resolve, clock: Callable[[], float] = wall,
                 sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None,
                 max_per_host: int | None = None, campus_url: str | None = None,
                 local_address: Callable[[], str | None] = local_address):
        self.root = Path(root)
        self.fr = fr
        self.check = JsCheck(self.root, gap=gap, browser=browser, client_factory=client_factory, resolver=resolver,
                             clock=clock, sleep=sleep, rng=rng, purpose=PURPOSE, campus_url=campus_url,
                             local_address=local_address)
        self.check.fr = fr
        # JSON bodies of the page's own data requests: sent once jscheck's _target reads this (spec §5 ②, the crawler
        # session's change; jscheck itself leaves it unset and judges as before). Until then they stay post_body.
        self.check.json_post = True
        self.sender = CappedSender(self.check, max_per_host)
        self.check.sender = self.sender
        self.browser = browser or PlaywrightBrowser()
        self.run_id = self.check.run_id
        self._writer: RenderedWriter | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="jsfetch-browser", daemon=True)
        self._thread.start()
        try:
            asyncio.run_coroutine_threadsafe(self.browser.start(), self._loop).result()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def sent_to(self, host: str) -> int:
        return self.sender.sent_to(host)

    def capped(self, host: str) -> bool:
        return self.sender.capped(host)

    def halt(self) -> None:
        """Stop the page in flight: its requests not yet sent are not sent (jscheck's stop)."""
        self.check.halt("stop")

    def __call__(self, url: str) -> Rendered:
        if self._loop is None:
            raise RuntimeError("JsFetcher is used inside a with block")
        bridge = OwnHostBridge(self.check, url)
        try:
            obs = asyncio.run_coroutine_threadsafe(self.browser.open(url, bridge), self._loop).result()
        except Exception as exc:  # noqa: BLE001 - the browser itself failed: ours, no strike (review 11-17 ①)
            why = f"{type(exc).__name__}: {exc}"[:300]
            return Rendered(url, error=why, incomplete=f"browser {why}"[:300], strike=False, log=list(bridge.log),
                            requests=_requests(bridge.log))
        return self._rendered(url, obs)

    def _rendered(self, url: str, obs: Observation) -> Rendered:
        reason, strike = incomplete_reason(obs)
        got = Rendered(url, final_url=obs.final_url, html=obs.rendered, log=list(obs.log),
                       requests=_requests(obs.log), incomplete=reason, strike=strike)
        navs = [e for e in obs.log if e.navigation]
        own = navs[0] if navs else None
        if own is None or own.blocked or own.unsent or own.error or own.status is None:
            why = "no_answer" if own is None else (own.error or own.unsent or own.blocked or "no_answer")
            got.error, got.html = why, ""
            got.incomplete = reason or why
            got.strike = strike or (own is not None and own.error is not None)
            return got
        if reason is not None and reason.startswith("moved:"):
            got.html = ""
            return got
        last = next(e for e in reversed(navs) if e.status is not None)
        if last.status != 200:
            got.error, got.html = f"status {last.status}", ""
            got.incomplete, got.strike = (reason, strike) if reason else (got.error, True)  # review 11-17 ④
            return got
        d_len, d_lines = jscheck.text_lines(last.text or "")
        r_len, r_lines = jscheck.text_lines(obs.rendered)
        _new, n_new = jscheck.new_text(d_lines, r_lines)
        got.raw_chars, got.chars, got.new_chars = d_len, r_len, n_new
        got.verdict = jscheck.judge(obs).verdict
        page_url = obs.final_url if obs.final_url.startswith(("http://", "https://")) else last.url
        got.links = sorted(_site_links(obs.rendered, page_url) - _site_links(last.text or "", last.url))
        if obs.error or not obs.final_url.startswith(("http://", "https://")):
            got.html = ""  # what the browser showed is no page (an error, chrome-error://): nothing to archive
            return got
        got.warc, got.warc_offset = self._write(last.url, obs.rendered)
        return got

    def _write(self, url: str, html: str) -> tuple[str, int]:
        own = self.fr.q("SELECT warc, warc_offset FROM request WHERE purpose=? AND url=? AND warc_offset IS NOT NULL "
                        "ORDER BY id DESC LIMIT 1", (PURPOSE, url))
        refers = record_id_at(self.root, own[0]["warc"], own[0]["warc_offset"]) if own else None
        if self._writer is None:
            self._writer = RenderedWriter(self.root, self.run_id, datetime.now(UTC))
        return self._writer.write(url, html, refers)

    def close(self) -> None:
        """The browser closes, the last answer's gap is waited out (as the crawl does before it lets the locks go), the
        queues' clients and WARC files close."""
        loop, self._loop = self._loop, None
        if loop is not None:
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(self.browser.close(), loop).result(timeout=60)
            if (wait := self.sender.last_answer_due() - self.check.clock()) > 0:
                self.check.sleep(min(wait, jscheck.SLOW_CAP_S + 10))
            self.sender.close()
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=30)
            loop.close()
        if self._writer is not None:
            self._writer.close()
            self._writer = None


# ---------------------------------------------------------------- which pages a pass opens (spec §4.4)


def _in(sites: Collection[str]) -> tuple[str, tuple[str, ...]]:
    keys = tuple(dict.fromkeys(sites))
    return ",".join("?" * len(keys)), keys


def _looks(fr: Frontier, sites: Collection[str]) -> dict[int, list[sqlite3.Row]]:
    """Every look at the sites' pages, newest first, by url id (js_render joined to url for the site)."""
    marks, keys = _in(sites)
    out: dict[int, list[sqlite3.Row]] = {}
    for r in fr.q(f"SELECT r.*, u.site FROM js_render r JOIN url u ON u.id = r.url_id WHERE u.site IN ({marks}) "
                  "ORDER BY r.url_id, r.run DESC", keys):
        out.setdefault(r["url_id"], []).append(r)
    return out


def _judged_look(looks: list[sqlite3.Row]) -> sqlite3.Row:
    """The look a page is judged by: its newest complete one, else its newest."""
    return next((r for r in looks if r["incomplete"] is None), looks[0])


def _files(fr: Frontier, urls: set[str]) -> set[str]:
    """Of urls, those that are no page: an attachment or a picture by the address (scope.kind_of), or a row the
    crawler fetched as one (ord's /api/dl.php?file=… is a page by its address until it is fetched). A shown page's
    attachments make it no list (11-17 offline trial: ord's notice pages)."""
    out = {u for u in urls if scope.kind_of(u) != "page"}
    rest = sorted(urls - out)
    for i in range(0, len(rest), 500):
        chunk = rest[i:i + 500]
        out.update(r["url"] for r in fr.q(f"SELECT url FROM url WHERE url IN ({','.join('?' * len(chunk))}) "
                                          "AND kind IN ('file', 'image')", chunk))
    return out


def classify(fr: Frontier, sites: Collection[str], looks: dict[int, list[sqlite3.Row]] | None = None) -> dict[int, str]:
    """url id -> links (its look shows a page link of its own: one shown on fewer than MENU_PAGES of its site's pages;
    attachments and pictures are no such link), text (else judge said js, or the browser showed TEXT_CHARS of text the
    page's own answer lacks), none (the browser added nothing of its own). Worked
    out from every page's judged look at once, so that a menu a script puts on every page counts for no page (spec
    §4.4, plan 2026-11-17 Task 3)."""
    looks = _looks(fr, sites) if looks is None else looks
    judged = {uid: _judged_look(rs) for uid, rs in looks.items()}
    shown: dict[int, list[str]] = {uid: json.loads(r["links"] or "[]") for uid, r in judged.items()}
    files = _files(fr, {link for ls in shown.values() for link in ls})
    shown = {uid: [link for link in ls if link not in files] for uid, ls in shown.items()}
    pages_of: Counter[tuple[str, str]] = Counter()
    for uid, ls in shown.items():
        for link in set(ls):
            pages_of[(judged[uid]["site"], link)] += 1
    out: dict[int, str] = {}
    for uid, r in judged.items():
        if any(pages_of[(r["site"], link)] < MENU_PAGES for link in shown[uid]):
            out[uid] = "links"
        elif r["verdict"] == "js" or (r["new_chars"] or 0) >= TEXT_CHARS:
            out[uid] = "text"
        else:
            out[uid] = "none"
    return out


def _is_home(url: str) -> bool:
    parts = urlsplit(url)
    return parts.path in ("", "/") and not parts.query


def select_pages(fr: Frontier, sites: Collection[str], now: datetime, *, again: bool = False) -> list[sqlite3.Row]:
    """The sites' fetched HTML pages a pass opens, homes first then in the ledger's order (spec §4.4): never looked at;
    a site's home (its menu and its boxes, every pass); its newest look incomplete, unless its two newest looks were
    both strikes (again: those too); links (every pass: these sites' lists have no rows the nightly update can read);
    text, when the crawler fetched it since the look it is judged by or that look is TEXT_EVERY_DAYS old."""
    marks, keys = _in(sites)
    rows = fr.q(f"SELECT * FROM url WHERE site IN ({marks}) AND kind='page' AND state='done' AND status=200 "
                "AND (sniff='html' OR (sniff IS NULL AND ctype LIKE 'text/html%')) ORDER BY id", keys)
    looks = _looks(fr, sites)
    kinds = classify(fr, sites, looks)
    picked: list[sqlite3.Row] = []
    for row in rows:
        mine = looks.get(row["id"])
        if not mine or _is_home(row["url"]):
            picked.append(row)
            continue
        if mine[0]["incomplete"] is not None:
            struck = len(mine) >= 2 and mine[0]["strike"] and mine[1]["strike"]
            if again or not struck:
                picked.append(row)
            continue
        kind = kinds.get(row["id"], "none")
        if kind == "links":
            picked.append(row)
        elif kind == "text":
            judged = _judged_look(mine)
            fetched_since = (row["fetched_at"] or "") > (judged["fetched_at"] or "")
            if fetched_since or now - datetime.fromisoformat(judged["at"]) >= timedelta(days=TEXT_EVERY_DAYS):
                picked.append(row)
    return sorted(picked, key=lambda r: (not _is_home(r["url"]), r["id"]))


def for_export(fr: Frontier, url_id: int) -> tuple[sqlite3.Row | None, str | None]:
    """Which look the export gives for a page (spec §5 ⑤): (the js_render row whose rendered record to export, None)
    when its newest complete look added something (judge's js, links of its own site, or any new text) — the newest
    look being incomplete falls back to the one before that was complete, as the export batch's last good answer does;
    (None, None) when there is no look or the newest complete one added nothing (the page's own answer is the page);
    (None, why) when no look was ever complete (the export says why in 缺什么)."""
    if not fr.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name='js_render'"):
        return None, None
    looks = fr.q("SELECT * FROM js_render WHERE url_id=? ORDER BY run DESC", (url_id,))
    if not looks:
        return None, None
    complete = next((r for r in looks if r["incomplete"] is None), None)
    if complete is None:
        return None, looks[0]["incomplete"]
    added = (complete["verdict"] == "js" or json.loads(complete["links"] or "[]") or (complete["new_chars"] or 0) > 0)
    return (complete, None) if added and complete["warc"] else (None, None)


# ---------------------------------------------------------------- the pass (spec §4.6)


class JsFetchRefused(RuntimeError):
    pass


@dataclass
class SiteReport:
    opened: int = 0  # pages opened this pass
    links: int = 0  # of the site's looked-at pages after it: list-like
    text: int = 0  # … text only the browser shows
    none: int = 0  # … the browser added nothing of their own
    incomplete: int = 0  # pages opened this pass whose look was incomplete
    requests: int = 0  # requests sent this pass
    skipped: int = 0  # pages due but not opened: the site's cap was reached


class JsFetchRun:
    """`python -m nknu.archive.jsfetch run`: run() takes the locks, opens the sites' pages select_pages names, one at
    a time, records each look (js_render) and returns what it did per site. A STOP file in the archive directory stops
    it after the page in flight; it ends with a jsfetch_end event (finished, stop or error). Refused, before anything
    is written or sent, as jscheck is: no ledger, a ledger that is not round 1's (the 10-24 archive) or the offline
    rehearsal's, run.lock held (an archive run, jscheck, another pass), the machine-wide lock held, no site."""

    def __init__(self, root: Path, sites: Collection[str], *, gap: tuple[float, float] = GAP, limit: int | None = None,
                 max_per_site: int | None = None, again: bool = False, browser: Browser | None = None,
                 client_factory: Callable[[WarcSink | None], Client] | None = None,
                 resolver: Callable[[str], tuple] = resolve, clock: Callable[[], float] = wall,
                 sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None,
                 school_lock: Path | None = None, campus_url: str | None = CAMPUS_URL,
                 local_address: Callable[[], str | None] = local_address):
        self.root = Path(root)
        self.sites = tuple(dict.fromkeys(sites))
        self.gap = check_gap(gap)
        self.limit, self.max_per_site, self.again = limit, max_per_site, again
        self.browser, self.client_factory, self.resolver = browser, client_factory, resolver
        self.clock, self.sleep, self.rng = clock, sleep, rng
        # a trial with a cap asks nothing but the named sites (review 11-17 ⑤: the hourly campus check is SSO's page,
        # past the cap and the own-host rule); a full pass asks it as every crawl program does (crawler B4)
        self.school_lock, self.local_address = school_lock, local_address
        self.campus_url = None if max_per_site is not None else campus_url
        self.end_reason: str | None = None
        self.end_detail: str | None = None
        self.fetcher: JsFetcher | None = None

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), UTC)

    def run(self) -> dict[str, SiteReport]:
        db = self.root / "frontier.db"
        if not self.sites:
            raise JsFetchRefused("没有给网站（--sites）：什么都不开")
        if not db.exists():
            raise JsFetchRefused(f"找不到待抓清单：{db}")
        if round_of(db) != "1":
            raise JsFetchRefused(f"{self.root} 不是第一轮的存档（frontier.db 没有第一轮的标记）：jsfetch 不碰旧存档")
        if rehearsal_of(db):
            raise JsFetchRefused(f"{self.root} 是离线彩排的资料库：主机地址是旧的或编的，不能对它发请求")
        try:
            run_lock = hold_run_lock(self.root)
        except AlreadyRunning as exc:
            raise JsFetchRefused(f"archive run（或 jscheck、另一遍 jsfetch）正在用这个目录（pid {exc.pid}）：等它停下"
                                 "再开") from None
        try:
            try:
                school = schoollock.hold(self.school_lock)
            except schoollock.SchoolLockBusy as exc:
                raise JsFetchRefused(f"另一个程序正在对学校发请求（pid {exc.pid}，全机锁 {exc.path}）：等它停下再开"
                                     ) from None
            except schoollock.SchoolLockUnshared as exc:
                raise JsFetchRefused(str(exc)) from None
            try:
                (self.root / "STOP").unlink(missing_ok=True)  # only the program that holds run.lock clears a STOP
                fr = Frontier(db)
                try:
                    return self._run(fr)
                finally:
                    fr.close()
            finally:
                school.close()
        finally:
            run_lock.close()

    def _run(self, fr: Frontier) -> dict[str, SiteReport]:
        ensure_table(fr)
        report = {site: SiteReport() for site in self.sites}
        due = select_pages(fr, self.sites, self._now(), again=self.again)
        fr.event("info", "jsfetch_start", json.dumps({"sites": list(self.sites), "due": len(due),
                                                      "max_per_site": self.max_per_site}, ensure_ascii=False))
        fetcher = JsFetcher(self.root, fr, browser=self.browser, gap=self.gap, client_factory=self.client_factory,
                            resolver=self.resolver, clock=self.clock, sleep=self.sleep, rng=self.rng,
                            max_per_host=self.max_per_site, campus_url=self.campus_url,
                            local_address=self.local_address)
        self.fetcher = fetcher
        self.end_reason = "finished"
        try:
            with fetcher:
                for row in due:
                    if self._halted(fetcher):
                        break
                    if (self.root / "STOP").exists():
                        self.end_reason = "stop"
                        break
                    rep = report[row["site"]]
                    if self.limit is not None and sum(r.opened for r in report.values()) >= self.limit:
                        break
                    if fetcher.capped(row["host"]):
                        rep.skipped += 1
                        continue
                    got = fetcher(row["url"])
                    record(fr, row, got, fetcher.run_id, at=self._now())
                    rep.opened += 1
                    rep.incomplete += got.incomplete is not None
                    rep.requests += got.requests["sent"]
                    if got.incomplete and got.incomplete.startswith("browser "):
                        self.end_reason, self.end_detail = "error", got.error  # Chrome itself failed: stop here
                        break
                else:
                    self._halted(fetcher)
        except jscheck.BrowserMissing as exc:
            self.end_reason, self.end_detail = "error", str(exc)
        except Exception as exc:  # written down as the pass's end, then raised
            self.end_reason, self.end_detail = "error", f"{type(exc).__name__}: {exc}"[:300]
            self._end(fr, report)
            raise
        for uid, kind in classify(fr, self.sites).items():
            site = fr.q("SELECT site FROM url WHERE id=?", (uid,))[0]["site"]
            setattr(report[site], kind, getattr(report[site], kind) + 1)
        self._end(fr, report)
        return report

    def _halted(self, fetcher: JsFetcher) -> bool:
        """The Sender stopped the look (jscheck's halt: a WARC that cannot be written, the machine-wide lock gone, a
        stop): the pass ends with its reason (review 11-17 ③)."""
        if not fetcher.check.stop.is_set():
            return False
        self.end_reason = fetcher.check.end_reason or "stop"
        self.end_detail = fetcher.check.end_detail
        return True

    def _end(self, fr: Frontier, report: dict[str, SiteReport]) -> None:
        fr.event("info" if self.end_reason != "error" else "error", "jsfetch_end", json.dumps(
            {"end": self.end_reason, "detail": self.end_detail,
             "sites": {k: vars(v) for k, v in report.items()}}, ensure_ascii=False))


# ---------------------------------------------------------------- the commands (spec §4.6)

_factories: dict[str, Any] = {}  # tests put a made-up browser and transport here; empty in use


def _gap(text: str) -> tuple[float, float]:
    lo, _, hi = text.partition("-")
    return check_gap((float(lo), float(hi or lo)))


def _report_line(site: str, r: SiteReport) -> str:
    return (f"{site}：开了 {r.opened} 页（列表 {r.links}、正文 {r.text}、没用 {r.none}），没开完整 {r.incomplete}，"
            f"送了 {r.requests} 个请求" + (f"，到上限没开 {r.skipped} 页" if r.skipped else ""))


def _cmd_run(a: argparse.Namespace) -> int:
    sites = [s.strip() for s in a.sites.split(",") if s.strip()]
    run = JsFetchRun(Path(a.dir), sites, gap=_gap(a.gap), limit=a.limit, max_per_site=a.max_per_site,
                     again=a.again, **_factories)
    try:
        report = run.run()
    except JsFetchRefused as exc:
        print(f"没有开：{exc}")
        return 2
    for site, r in report.items():
        print(_report_line(site, r))
    print(f"结束：{ {'finished': '做完了', 'stop': '照 STOP 停了', 'error': '出错'}.get(run.end_reason or '', '?') }"
          + (f"（{run.end_detail}）" if run.end_detail else ""))
    return 1 if run.end_reason == "error" else 0


def _cmd_render(a: argparse.Namespace) -> int:
    root = Path(a.dir)
    db = root / "frontier.db"
    if db.exists():
        fr = Frontier(db, readonly=True)
        try:
            rows = fr.q("SELECT count(*) FROM url")[0][0]
        finally:
            fr.close()
        if rows:
            print(f"没有开：{db} 里已经有 {rows} 个网址，像是正式账本；单页试只用空的临时资料夹")
            return 2
    try:
        lock = hold_run_lock(root)
    except AlreadyRunning as exc:
        print(f"没有开：另一个程序正在用这个目录（pid {exc.pid}）")
        return 2
    try:
        try:
            school = schoollock.hold()
        except (schoollock.SchoolLockBusy, schoollock.SchoolLockUnshared) as exc:
            print(f"没有开：全机锁拿不到（{exc}）")
            return 2
        try:
            fr = Frontier(db)
            try:
                kw = {k: v for k, v in _factories.items() if k != "browser"}
                with JsFetcher(root, fr, browser=_factories.get("browser"), gap=_gap(a.gap),
                               max_per_host=a.max_per_site, campus_url=None, **kw) as fx:
                    got = fx(a.url)
            finally:
                fr.close()
        except jscheck.BrowserMissing as exc:
            print(f"没有开：{exc}")
            return 1
        finally:
            school.close()
    finally:
        lock.close()
    print(f"网址：{got.url}\n停在：{got.final_url}\n判定：{got.verdict}\n原文 {got.raw_chars} 字、显示后 {got.chars} 字、"
          f"多出 {got.new_chars} 字\n原文没有的同站链接：{len(got.links)} 个\n没开完整：{got.incomplete or '没有'}"
          f"\n出错：{got.error or '没有'}\n请求：{json.dumps(got.requests, ensure_ascii=False)}\n"
          f"显示版存在：{got.warc}@{got.warc_offset}")
    return 0 if got.error is None else 1


def _cmd_status(a: argparse.Namespace) -> int:
    db = Path(a.dir) / "frontier.db"
    if not db.exists():
        print(f"找不到待抓清单：{db}")
        return 2
    fr = Frontier(db, readonly=True)
    try:
        if not fr.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name='js_render'"):
            print("还没有用浏览器开过任何一页（没有 js_render 表）")
            return 0
        sites = ([s.strip() for s in a.sites.split(",") if s.strip()] if a.sites else
                 [r["site"] for r in fr.q("SELECT DISTINCT u.site FROM js_render r JOIN url u ON u.id = r.url_id "
                                          "ORDER BY u.site")])
        if not sites:
            print("还没有用浏览器开过任何一页")
            return 0
        looks = _looks(fr, sites)
        kinds = classify(fr, sites, looks)
        due = {r["id"] for r in select_pages(fr, sites, datetime.now(UTC))}
        for site in sites:
            marks = fr.q("SELECT id FROM url WHERE site=? AND kind='page' AND state='done' AND status=200", (site,))
            ids = {r["id"] for r in marks}
            seen = [uid for uid in ids if uid in looks]
            count = Counter(kinds.get(uid, "none") for uid in seen)
            bad = sum(1 for uid in seen if looks[uid][0]["incomplete"] is not None)
            struck = sum(1 for uid in seen if len(looks[uid]) >= 2 and looks[uid][0]["strike"] and looks[uid][1]["strike"])
            print(f"{site}：网页 {len(ids)}，开过 {len(seen)}（列表 {count['links']}、正文 {count['text']}、"
                  f"没用 {count['none']}），最近一次没开完整 {bad}（其中连续两次失手、不再自动试 {struck}），"
                  f"现在该开 {len(due & ids)}")
    finally:
        fr.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m nknu.archive.jsfetch",
                                description="用浏览器打开网页再存档（第二轮第一批，设计稿 2026-11-17）")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="在爬虫停着时，把指定网站里该开的页逐页打开、存档、记账")
    r.add_argument("--dir", required=True, help="存档资料夹（有 frontier.db）")
    r.add_argument("--sites", required=True, help="网站键，逗号分开")
    r.add_argument("--gap", default="2-5", help="每个回应之后等几秒（下限-上限），默认 2-5")
    r.add_argument("--limit", type=int, default=None, help="这一遍最多开几页")
    r.add_argument("--max-per-site", type=int, default=None, help="这一遍每个网站最多送几个请求（试跑用）")
    r.add_argument("--again", action="store_true", help="连续两次失手、不再自动试的页也再开一次")
    r.set_defaults(fn=_cmd_run)
    one = sub.add_parser("render", help="单页试：在一个空的临时资料夹里开一页，印出结果")
    one.add_argument("url")
    one.add_argument("--dir", required=True, help="空的临时资料夹（不能是正式账本）")
    one.add_argument("--gap", default="2-5")
    one.add_argument("--max-per-site", type=int, default=None)
    one.set_defaults(fn=_cmd_render)
    st = sub.add_parser("status", help="每站开过几页、有用几页、没开完整几页、现在该开几页（只读）")
    st.add_argument("--dir", required=True)
    st.add_argument("--sites", default=None)
    st.set_defaults(fn=_cmd_status)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
