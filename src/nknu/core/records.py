"""What adapters emit. parse() turns a snapshot into these; the pipeline decides how each one is written."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

SourceKind = Literal["table_page", "announcement", "ics", "manual"]


@dataclass(frozen=True)
class SourceRef:
    url: str
    title: str | None
    publisher: str  # entity id of the publishing unit
    kind: SourceKind
    sha256: str  # of the fetched snapshot; not part of the content hash
    published_on: date | None = None
    evidence_key: str | None = None  # announcement body + its attachments share one key = one piece of evidence


@dataclass(frozen=True)
class HoursRule:
    dows: tuple[int, ...]
    opens: str
    closes: str  # earlier than opens = past midnight


@dataclass(frozen=True)
class FactRecord:
    key: str  # stable natural key; lineage_id = uuid5(key)
    entity_id: str
    attribute: Literal["hours", "holiday"]
    value: dict
    valid_from: date | None  # half-open; None + valid_basis='term' -> filled from the academic calendar
    valid_to: date | None
    valid_basis: Literal["explicit", "term", "ttl"]
    specificity: int  # 0 regular, 1 term table, 2 exception
    propagates: bool
    source: SourceRef
    quote: str
    extraction: Literal["rule", "ics", "manual"]
    rules: tuple[HoursRule, ...] = ()
    qualifier: str = ""


@dataclass(frozen=True)
class EventRecord:
    key: str
    uid: str
    summary: str
    during_from: date
    during_to: date
    category: str
    status: Literal["confirmed", "tentative", "cancelled"]
    sequence: int
    last_modified: str | None
    source: SourceRef


@dataclass(frozen=True)
class DocRecord:
    key: str
    doc_key: str
    title: str
    url: str
    unit: str | None = None
    category: str | None = None
    published_on: date | None = None
    announce_until: date | None = None  # inclusive last day, as printed on the page
    excerpt: str | None = None
    attachments: tuple[str, ...] = ()
    pii_level: str = "none"
    partial: bool = False  # detail-page fields only, merged into the list-page record
    gone: bool = False  # the page answered 404


@dataclass(frozen=True)
class EntityHint:
    key: str
    name: str
    context: str
    url: str


@dataclass(frozen=True)
class ParseIssue:
    key: str
    code: str  # 'date' counts against the date-parse success rate
    message: str


Record = FactRecord | EventRecord | DocRecord | EntityHint | ParseIssue
_TYPES = {t.__name__: t for t in (FactRecord, EventRecord, DocRecord, EntityHint, ParseIssue)}
_DATE_FIELDS = {"valid_from", "valid_to", "published_on", "during_from", "during_to", "announce_until"}


def _enc(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: _enc(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, (tuple, list)):
        return [_enc(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _enc(v) for k, v in obj.items()}
    return obj


def _dates(d: dict) -> dict:
    return {k: (date.fromisoformat(v) if k in _DATE_FIELDS and v else v) for k, v in d.items()}


def to_json(rec: Record) -> str:
    return json.dumps({"type": type(rec).__name__, **_enc(rec)}, ensure_ascii=False, sort_keys=True)


def from_json(s: str) -> Record:
    d = json.loads(s)
    cls = _TYPES[d.pop("type")]
    d = _dates(d)
    if "source" in d:
        d["source"] = SourceRef(**_dates(d["source"]))
    if "rules" in d:
        d["rules"] = tuple(HoursRule(tuple(r["dows"]), r["opens"], r["closes"]) for r in d["rules"])
    if "attachments" in d:
        d["attachments"] = tuple(d["attachments"])
    return cls(**d)


def content_hash(rec: Record) -> str:
    """Hash of the normalised record. The snapshot sha is excluded, so ViewState or hit-counter churn is not a change."""
    d = _enc(rec)
    if isinstance(d.get("source"), dict):
        d["source"].pop("sha256", None)
    blob = json.dumps({"type": type(rec).__name__, **d}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


__all__ = [
    "DocRecord", "EntityHint", "EventRecord", "FactRecord", "HoursRule", "ParseIssue", "Record", "SourceRef", "content_hash",
    "from_json", "to_json",
]
