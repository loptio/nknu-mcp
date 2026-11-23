"""Which document a reference means (plan 2026-11-15 T2): the owner pastes a document id from a result, the id of a
copy the build merged away, or an address copied from the school's site."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from nknu.content.ids import location_key, record_id


@dataclass(frozen=True)
class Resolved:
    ref: str  # as given, stripped
    doc_id: str | None  # the document in the library; None: none was found
    via: str = ""  # id | alias (a merged copy's id) | url (docs.url) | key (the id the build gives that address) | ''
    why: str = ""  # doc_alias.why, when an alias led to the document: copy | format | duplicate
    alias: str = ""  # that alias


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
                        (name,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def as_address(ref: str) -> str:
    """ref when it looks like an address (with or without its scheme: it has a slash or a dot), else ''. A document
    id never does (a letter and base32 characters; an event: e and the calendar's id)."""
    ref = (ref or "").strip()
    return ref if "/" in ref or "." in ref else ""


def _is_doc(conn: sqlite3.Connection, doc_id: str) -> bool:
    return conn.execute("SELECT 1 FROM docs WHERE doc_id = ?", (doc_id,)).fetchone() is not None


def _kept(conn: sqlite3.Connection, alias: str) -> tuple[str, str] | None:
    """(the document kept for alias, why), from doc_alias; None without the table (a database built before plan
    2026-11-08 T1) or the row."""
    if not has_table(conn, "doc_alias"):
        return None
    return conn.execute("SELECT doc_id, why FROM doc_alias WHERE alias = ?", (alias,)).fetchone()


def resolve(conn: sqlite3.Connection, ref: str) -> Resolved:
    """ref as a document id; else as the id of a copy merged into a document; else as a document's address as stored;
    else, for something that looks like an address, as the id nknu.content.ids gives it (so another spelling of one
    notice's address finds it), itself or through doc_alias. Nothing found: doc_id None."""
    ref = (ref or "").strip()
    if not ref:
        return Resolved(ref, None)
    if _is_doc(conn, ref):
        return Resolved(ref, ref, "id")
    if kept := _kept(conn, ref):
        return Resolved(ref, kept[0], "alias", kept[1], ref)
    if row := conn.execute("SELECT doc_id FROM docs WHERE url = ? ORDER BY doc_id LIMIT 1", (ref,)).fetchone():
        return Resolved(ref, row[0], "url")
    if as_address(ref):
        keyed = "d" + record_id(location_key(ref))
        if _is_doc(conn, keyed):
            return Resolved(ref, keyed, "key")
        if kept := _kept(conn, keyed):
            return Resolved(ref, kept[0], "alias", kept[1], keyed)
    return Resolved(ref, None)
