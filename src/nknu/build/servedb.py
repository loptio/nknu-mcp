"""Build serve.db: a read-only snapshot (facts copy + answer cards + bigram FTS) written to a temp file, then
atomically renamed over the old one. A failed build leaves the previous snapshot untouched."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from nknu.build.cards import CardRow, calendar_card_rows, hours_card_rows
from nknu.build.templates import load_templates
from nknu.core.factsdb import connect as connect_facts
from nknu.core.ids import uuid7
from nknu.core.textnorm import tokenize
from nknu.core.timeutil import iso_now
from nknu.core.writer import add_alert


@dataclass(frozen=True)
class BuildInfo:
    build_id: str
    built_at: str
    facts_version: int
    n_cards: int


_COPY = [
    ("entity", "SELECT * FROM src.entity"),
    ("entity_alias", "SELECT * FROM src.entity_alias"),
    ("entity_relation", "SELECT * FROM src.entity_relation"),
    ("attribute_authority", "SELECT * FROM src.attribute_authority"),
    ("source_doc", "SELECT * FROM src.source_doc WHERE adapter NOT IN (SELECT value FROM json_each(:disabled))"),
    ("fact", "SELECT * FROM src.fact WHERE source_doc IN (SELECT id FROM main.source_doc)"),
    ("hours_rule", "SELECT * FROM src.hours_rule WHERE fact_id IN (SELECT id FROM main.fact)"),
    ("calendar_event", ("SELECT * FROM src.calendar_event WHERE source_doc IN (SELECT id FROM main.source_doc) "
                       "AND status IN ('confirmed','tentative')")),
    ("doc", "SELECT * FROM src.doc WHERE withdrawn_at IS NULL AND adapter NOT IN (SELECT value FROM json_each(:disabled))"),
]
_INDEXES = """
CREATE INDEX fact_lineage ON fact(lineage_id);
CREATE INDEX fact_entity ON fact(entity_id, attribute);
CREATE INDEX hours_rule_fact ON hours_rule(fact_id);
CREATE INDEX alias_norm ON entity_alias(alias_norm);
CREATE INDEX event_from ON calendar_event(during_from);
CREATE INDEX doc_published ON doc(published_on);
CREATE TABLE answer_card(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, intent TEXT NOT NULL, qualifier TEXT NOT NULL,
  valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, status TEXT NOT NULL, decided_by TEXT NOT NULL, answer_zh TEXT NOT NULL,
  payload_json TEXT NOT NULL, fact_ids_json TEXT NOT NULL, template_ver INTEGER NOT NULL);
CREATE INDEX card_lookup ON answer_card(entity_id, intent, valid_from);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIRTUAL TABLE card_fts USING fts5(card_id UNINDEXED, intent UNINDEXED, tokens, tokenize='unicode61 remove_diacritics 0');
CREATE VIRTUAL TABLE doc_fts USING fts5(doc_id UNINDEXED, tokens, tokenize='unicode61 remove_diacritics 0');
"""


def _prev(out_path: Path) -> Path:
    return out_path.with_name(out_path.name + ".prev")


def _write_fts(conn: sqlite3.Connection, cards: list[CardRow]) -> None:
    labels: dict[str, list[str]] = {}
    for r in conn.execute("SELECT id, name_zh, coalesce(name_en, '') FROM entity"):
        labels[r[0]] = [r[1], r[2]]
    for r in conn.execute("SELECT entity_id, alias FROM entity_alias"):
        labels.setdefault(r[0], []).append(r[1])
    conn.executemany(
        "INSERT INTO card_fts(card_id, intent, tokens) VALUES (?,?,?)",
        [(c.id, c.intent, " ".join(tokenize(" ".join([c.answer_zh, *(labels.get(c.entity_id, []) if c.intent == "hours" else [])]))))
         for c in cards],
    )
    conn.executemany(
        "INSERT INTO doc_fts(doc_id, tokens) VALUES (?,?)",
        [(r[0], " ".join(tokenize(f"{r[1]} {r[2] or ''} {r[3] or ''}")))
         for r in conn.execute("SELECT id, title, unit, excerpt FROM doc")],
    )


def build_serve_db(facts_path: Path, out_path: Path, *, today: date, templates_path: Path,
                   disabled_sources: frozenset[str] = frozenset()) -> BuildInfo:
    out_path = Path(out_path)
    tpl = load_templates(templates_path)
    tmp = out_path.with_name(f"{out_path.name}.tmp-{os.getpid()}")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp.resolve().as_uri(), uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS src", (Path(facts_path).resolve().as_uri() + "?mode=ro",))
        disabled = json.dumps(sorted(disabled_sources))
        for table, select in _COPY:
            conn.execute(f"CREATE TABLE main.{table} AS {select}", {"disabled": disabled})
        facts_version = int(conn.execute("SELECT value FROM src.meta WHERE key='data_version'").fetchone()[0])
        conn.commit()
        conn.execute("DETACH DATABASE src")
        conn.executescript(_INDEXES)
        cards = hours_card_rows(conn, tpl, today) + calendar_card_rows(conn, tpl, today)
        conn.executemany("INSERT INTO answer_card VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         [tuple(c.__dict__.values()) for c in cards])
        _write_fts(conn, cards)
        info = BuildInfo(uuid7(), iso_now(), facts_version, len(cards))
        conn.executemany("INSERT INTO meta VALUES (?,?)", [
            ("build_id", info.build_id), ("built_at", info.built_at), ("facts_version", str(facts_version)),
            ("template_ver", str(tpl.ver)), ("today", today.isoformat())])
        conn.commit()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("serve.db integrity check failed")
        conn.execute("VACUUM")
        conn.close()
        if out_path.exists():
            shutil.copy2(out_path, _prev(out_path))
        os.replace(tmp, out_path)
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    _alert_conflicts(facts_path, cards)
    return info


def _alert_conflicts(facts_path: Path, cards: list[CardRow]) -> None:
    conflicts = [c for c in cards if c.status == "conflict"]
    if not conflicts:
        return
    fconn = connect_facts(facts_path)
    with fconn:
        for c in conflicts:
            add_alert(fconn, adapter=None, level="warn", code="conflict",
                      message=f"{c.entity_id} {c.valid_from}..{c.valid_to}: {c.answer_zh}",
                      dedup_key=f"conflict:{c.entity_id}:{c.valid_from}", now=iso_now())
    fconn.close()


def rollback(out_path: Path) -> None:
    prev = _prev(Path(out_path))
    if not prev.exists():
        raise FileNotFoundError(f"no previous snapshot at {prev}")
    os.replace(prev, out_path)
