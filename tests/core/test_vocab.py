from datetime import date

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.core.factsdb import connect, migrate
from nknu.core.ids import ENTITY_ID_RE
from nknu.core.textnorm import alias_norm
from nknu.core.vocab import ancestors, current_term, load_vocab, managing_org, resolve_place, seed, tier
from nknu.settings import REPO_ROOT

VOCAB_PATH = REPO_ROOT / "config" / "entities.yaml"


@pytest.fixture
def vocab():
    return load_vocab(VOCAB_PATH)


@pytest.fixture
def conn(tmp_path, vocab):
    c = connect(tmp_path / "facts.db")
    migrate(c)
    seed(c, vocab)
    yield c
    c.close()


def test_all_ids_follow_rule(vocab):
    assert all(ENTITY_ID_RE.match(e["id"]) for e in vocab["entities"])


def test_campus_in_id_prevents_merge(conn):
    rows = conn.execute("SELECT campus FROM entity WHERE name_zh LIKE '%重量訓練室%'").fetchall()
    assert {r["campus"] for r in rows} == {"heping", "yanchao"}


def test_gym_alias_is_ambiguous(conn):
    rows = conn.execute("SELECT entity_id, ambiguous FROM entity_alias WHERE alias_norm=?", (alias_norm("健身房"),)).fetchall()
    assert len(rows) == 2 and all(r["ambiguous"] for r in rows)


def test_ancestors(conn):
    assert ancestors(conn, "nknu.heping.gym.weight-room", date(2026, 10, 22)) == ["nknu.heping.gym.main", "nknu.heping"]


def test_managing_org(conn):
    assert managing_org(conn, "nknu.heping.gym.weight-room", "hours") == "nknu.org.pe"
    assert managing_org(conn, "nknu.yanchao.library.main", "hours") == "nknu.org.lis"
    assert managing_org(conn, "nknu", "holiday") == "nknu.org.aca"


@pytest.mark.parametrize(
    "pub,kind,expect",
    [("nknu.org.pe", "table_page", 1), ("nknu.org.pe", "announcement", 2), ("nknu.org.lis", "announcement", 3),
     ("nknu.org.pe", "manual", 4)],
)
def test_tier(conn, pub, kind, expect):
    assert tier(conn, publisher=pub, kind=kind, entity_id="nknu.heping.gym.weight-room", attribute="hours") == expect


def test_seed_idempotent(conn, vocab):
    seed(conn, vocab)
    seed(conn, vocab)
    assert conn.execute("SELECT count(*) FROM entity").fetchone()[0] == len(vocab["entities"])


def test_resolve_place(conn):
    assert resolve_place(conn, "和平重訓室") == ["nknu.heping.gym.weight-room"]
    assert resolve_place(conn, "nknu.yanchao.library.main") == ["nknu.yanchao.library.main"]
    assert sorted(resolve_place(conn, "圖書館")) == ["nknu.heping.library.main", "nknu.yanchao.library.main"]
    assert len(resolve_place(conn, "重訓室")) == 2
    assert resolve_place(conn, "Heping Library") == ["nknu.heping.library.main"]
    assert resolve_place(conn, "不存在的館") == []


def test_current_term(conn):
    conn.execute("INSERT INTO source_doc(url, publisher, kind, adapter, fetched_at, warc_sha256) "
                 "VALUES ('ics','nknu.org.aca','ics','aca_calendar_ics','t','s')")
    for uid, cat, d0, d1 in [("a", "term_begin", "2026-09-07", "2026-09-08"), ("b", "vacation_begin", "2027-01-18", "2027-01-19"),
                             ("c", "term_begin", "2027-02-22", "2027-02-23")]:
        conn.execute("INSERT INTO calendar_event VALUES (?,?,?,?,?,'confirmed',0,1,NULL,'t','t')", (uid, uid, d0, d1, cat))
    assert current_term(conn, date(2026, 10, 22)) == (date(2026, 9, 7), date(2027, 1, 18))
    assert current_term(conn, date(2027, 1, 25)) is None  # winter vacation: no term in force


def test_cli_init_creates_db(tmp_path):
    r = CliRunner().invoke(app, ["init"], env={"NKNU_DATA_DIR": str(tmp_path)})
    assert r.exit_code == 0, r.output
    c = connect(tmp_path / "facts.db")
    assert c.execute("SELECT count(*) FROM entity").fetchone()[0] > 10
