"""Worlds derived from the session fixture world: each copies facts.db, changes the copy and rebuilds serve.db.
Test-only records use NewsView ids >= 9000 (no such notices exist) and titles that start with 測試."""

import sqlite3
from datetime import date, datetime

import pytest

from nknu.admin.manual import add_hours
from nknu.build.servedb import build_serve_db
from nknu.core.factsdb import connect
from nknu.core.ids import lineage_id
from nknu.core.records import DocRecord, FactRecord, HoursRule, SourceRef, content_hash
from nknu.core.timeutil import TAIPEI
from nknu.core.writer import add_pending, apply_fact, upsert_doc
from nknu.server.query import get_hours
from nknu.server.servedb_reader import ServeDB
from nknu.settings import REPO_ROOT
from tests.scenario import build as build_scenario

TPL = REPO_ROOT / "config" / "templates.yaml"
TODAY = date(2026, 10, 22)
NOW = datetime(2026, 10, 22, 14, 5, tzinfo=TAIPEI)
WR = "nknu.heping.gym.weight-room"
GYM = "nknu.heping.gym.main"
CONFLICT_DAY = "2026-11-14"
NEWS = "https://c.nknu.edu.tw/reality/NewsView.aspx?Nid="


def tpe(*args) -> datetime:
    return datetime(*args, tzinfo=TAIPEI)


def _derive(world, tmp_path_factory, name, change):
    out = tmp_path_factory.mktemp(name)
    src, dst = sqlite3.connect(world[0]), sqlite3.connect(out / "facts.db")
    src.backup(dst)
    src.close()
    dst.close()
    conn = connect(out / "facts.db")
    extra = change(conn)
    conn.commit()
    conn.close()
    build_serve_db(out / "facts.db", out / "serve.db", today=TODAY, templates_path=TPL)
    return ServeDB(out / "serve.db"), extra


def _notice(nid: str, title: str, published: date) -> SourceRef:
    return SourceRef(NEWS + nid, title, "nknu.org.pe", "announcement", f"sha-{nid}", published_on=published)


@pytest.fixture(scope="session")
def conflict_world(world, tmp_path_factory):
    """Three same-tier announcements disagree about the weight room on 2026-11-14 (none newer than the others)."""
    def change(conn):
        day = date.fromisoformat(CONFLICT_DAY)
        variants = [("9001", "測試公告：11/14 重量訓練室全日暫停開放", True, ()),
                    ("9002", "測試公告：11/14 重量訓練室上午開放", False, (HoursRule((6,), "09:00", "12:00"),)),
                    ("9003", "測試公告：11/14 重量訓練室下午開放", False, (HoursRule((6,), "13:00", "17:00"),))]
        for nid, title, closed, rules in variants:
            rec = FactRecord(f"test:conflict:{nid}", WR, "hours", {"closed": closed}, day, date(2026, 11, 15), "explicit", 2,
                             False, _notice(nid, title, date(2026, 11, 1)), title, "rule", rules)
            apply_fact(conn, rec, adapter="pe_news", review="verified", now="2026-10-22T03:00:00Z")

    return _derive(world, tmp_path_factory, "conflict", change)[0]


@pytest.fixture(scope="session")
def world_with_pending_closure(world, tmp_path_factory):
    """A recent 體育室 closure notice whose closure fact still waits in the review queue."""
    def change(conn):
        title = "測試公告：本校體育館(含室外運動場地)於115年11月7日，全日暫停開放"
        upsert_doc(conn, DocRecord("pe_news:9100", "9100", title, NEWS + "9100", unit="體育室", category="一般公告",
                                   published_on=date(2026, 10, 20)), adapter="pe_news", now="2026-10-22T03:00:00Z")
        rec = FactRecord("pe_news:9100:nknu.heping.gym.main", GYM, "hours", {"closed": True, "reason": "測試"},
                         date(2026, 11, 7), date(2026, 11, 8), "explicit", 2, True, _notice("9100", title, date(2026, 10, 20)),
                         title, "rule")
        add_pending(conn, adapter="pe_news", record_key=rec.key, op="upsert", record=rec, content_hash=content_hash(rec),
                    diff="+ closed 2026-11-07", reason="new", now="2026-10-22T03:00:00Z")

    return _derive(world, tmp_path_factory, "pending", change)[0]


@pytest.fixture(scope="session")
def world_superseded(world, tmp_path_factory):
    """One manual fact entered twice (the second entry corrects the first): a lineage with two versions."""
    def change(conn):
        for opens, now in (("09:00", "2026-10-22T03:00:00Z"), ("10:00", "2026-10-22T04:00:00Z")):
            add_hours(conn, place=WR, from_=date(2026, 11, 21), to=None, opens=opens, closes="12:00", closed=False,
                      reason="測試：臨時開放", source_url=NEWS + "9200", publisher="nknu.org.pe", title="測試公告：臨時開放",
                      site_visit=False, now=now)
        return lineage_id(f"manual:{WR}:2026-11-21:2026-11-21")

    return _derive(world, tmp_path_factory, "superseded", change)


@pytest.fixture(scope="session")
def cross_midnight_world(tmp_path_factory):
    """tests/scenario.py world: its test-only entity nknu.heping.gym.night-test opens Fridays 22:00–02:00."""
    out = tmp_path_factory.mktemp("night")
    build_scenario(out).close()
    build_serve_db(out / "facts.db", out / "serve.db", today=TODAY, templates_path=TPL)
    return ServeDB(out / "serve.db")


@pytest.fixture(scope="session")
def sample_envelopes(world, conflict_world, world_with_pending_closure):
    db = ServeDB(world[1])
    return {
        "current": get_hours(db, WR, "2026-10-22", now=NOW),
        "conflict": get_hours(conflict_world, WR, CONFLICT_DAY, now=NOW),
        "days14": get_hours(db, WR, "2026-10-22", days=14, now=NOW),
        "stale": get_hours(db, WR, "2027-02-01", now=NOW),
        "no_data": get_hours(db, "不存在的館", now=NOW),
        "notices": get_hours(world_with_pending_closure, GYM, "2026-10-22", now=NOW),
    }
