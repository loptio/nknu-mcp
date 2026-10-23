"""A complete fixture world: every adapter's fixtures go through the real pipeline into facts.db (written directly, U2;
any item still queued for review is approved), the image-only library announcement is entered by hand, then serve.db is
built."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from nknu.admin.manual import add_hours
from nknu.admin.review import decide, list_pending
from nknu.build.servedb import build_serve_db
from nknu.core.factsdb import connect, migrate
from nknu.core.vocab import load_vocab, seed
from nknu.crawl.adapter import Adapter
from nknu.crawl.adapters.aca_calendar_ics import ICS_URL, AcaCalendarIcs
from nknu.crawl.adapters.lib_open_time import URL as LIB_URL
from nknu.crawl.adapters.lib_open_time import LibOpenTime
from nknu.crawl.adapters.pe_news import DETAIL_META as PE_DETAIL
from nknu.crawl.adapters.pe_news import LIST_META as PE_LIST
from nknu.crawl.adapters.pe_news import LIST_URL as PE_LIST_URL
from nknu.crawl.adapters.pe_news import PeNews, detail_url
from nknu.crawl.adapters.pe_open_hours import URL as PE_URL
from nknu.crawl.adapters.pe_open_hours import PeOpenHours
from nknu.crawl.adapters.sso_news import BASE as SSO_BASE
from nknu.crawl.adapters.sso_news import DETAIL_META as SSO_DETAIL
from nknu.crawl.adapters.sso_news import SsoNews, list_meta
from nknu.crawl.adapters.sso_news import detail_url as sso_detail_url
from nknu.crawl.pipeline import run_adapter
from nknu.settings import REPO_ROOT
from tests.crawl.adapters.helpers import FIXTURES, read, snap

NOW = "2026-10-22T02:00:00Z"
LIB_NEWS = "https://lis.nknu.edu.tw/news/2380"


class Replay(Adapter):
    """Feeds saved snapshots to a real adapter's parse() and invariants()."""

    def __init__(self, real: Adapter, snaps):
        self.real, self.snaps, self.name = real, snaps, real.name
        self.required_selectors, self.pages_are_html = real.required_selectors, real.pages_are_html

    def fetch_all(self, ctx):
        return self.snaps

    def parse(self, s):
        return self.real.parse(s)

    def invariants(self, records, history, snaps):
        return self.real.invariants(records, history, snaps)

    def counts_missing(self, key, kind, span, records, today):
        return self.real.counts_missing(key, kind, span, records, today)


def _snapshots():
    ics = (FIXTURES / "aca_calendar_ics" / "calendar.ics").read_bytes()
    unid = "3E3FDDE24A008A28322CF9E129D050890FBE26AC"
    return [
        (AcaCalendarIcs(), [snap(ICS_URL, ics.decode(), body=ics)]),
        (PeOpenHours(), [snap(PE_URL, read("pe_open_hours", "page.html"))]),
        (LibOpenTime(), [snap(LIB_URL, read("lib_open_time", "page.html"))]),
        (PeNews(), [snap(PE_LIST_URL, read("pe_news", "list.html"), meta=PE_LIST)]
         + [snap(detail_url(n), read("pe_news", f), meta=PE_DETAIL)
            for n, f in (("460", "closure.html"), ("800", "old_2022.html"), ("868", "suspend_booking.html"))]),
        (SsoNews(), [snap(f"{SSO_BASE}?cat=SCHOOLADMIN", read("sso_news", "list_p1.html"), meta=list_meta("SCHOOLADMIN")),
                     snap(f"{SSO_BASE}?cat=SCHOOLADMIN", read("sso_news", "list_p2.html"), meta=list_meta("SCHOOLADMIN", 2)),
                     snap(f"{SSO_BASE}?cat=GOOD", read("sso_news", "list_good.html"), meta=list_meta("GOOD")),
                     snap(sso_detail_url(unid), read("sso_news", "detail.html"), meta=SSO_DETAIL)]),
    ]


def build_facts(tmp_path: Path, today: date = date(2026, 10, 22)) -> Path:
    facts = tmp_path / "facts.db"
    conn = connect(facts)
    migrate(conn)
    seed(conn, load_vocab(REPO_ROOT / "config" / "entities.yaml"))
    for real, snaps in _snapshots():
        res = run_adapter(conn, Replay(real, snaps), None, today=today, full_scan=False, readonly=False)
        assert res.passed, (real.name, res.violations, res.error)
    for item in list_pending(conn):
        decide(conn, item.id, "approve", NOW)
    for eid, opens in (("nknu.heping.library.main", "08:15"), ("nknu.yanchao.library.main", "09:15")):
        add_hours(conn, place=eid, from_=date(2026, 10, 24), to=None, opens=opens, closes="21:00", closed=False,
                  reason="期中考周延長開館", source_url=LIB_NEWS, publisher="nknu.org.lis",
                  title="115學年度第一學期，兩校區圖書館期中考延長開館時間表", site_visit=False, now=NOW)
    conn.close()
    return facts


def build_fixture_world(tmp_path: Path, today: date = date(2026, 10, 22)) -> tuple[Path, Path]:
    facts = build_facts(tmp_path, today)
    serve = tmp_path / "serve.db"
    build_serve_db(facts, serve, today=today, templates_path=REPO_ROOT / "config" / "templates.yaml")
    return facts, serve
