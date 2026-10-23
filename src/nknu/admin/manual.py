"""`nknu add hours`: maintainer-entered facts (image-only announcements, on-site checks). Written as verified.

Entering a range again with the same source URL replaces the overlapping earlier entry (C1), so a mistyped range is
corrected by entering the right one; a mistake that overlaps nothing is taken down with `nknu undo`.

An entry that cites a crawled page corrects only the days it covers: a crawled fact of that page that also covers other
days is replaced (C1), and its other days are written again with the school's own content (keys manual:…, the page as
source), so correcting 10/26 of a 10/25-10/26 closure leaves 10/25 closed. The replaced crawled content is remembered
(note='manual'): the crawler does not write it back over the correction; changed page content is written as usual and
replaces the correction and the kept days alike."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from nknu.core.factsdb import bump_data_version
from nknu.core.records import FactRecord, HoursRule, SourceRef, content_hash
from nknu.core.vocab import resolve_place
from nknu.core.writer import apply_fact, displaced_by, set_state, suppress


class PlaceError(ValueError):
    pass


@dataclass
class Added:
    fid: str
    key: str
    replaced: list[str] = field(default_factory=list)  # record keys of the facts the entry replaced
    kept: list[str] = field(default_factory=list)  # record keys written for the other days of a replaced crawled fact


def add_hours(conn: sqlite3.Connection, *, place: str, from_: date, to: date | None, opens: str | None, closes: str | None,
              closed: bool, reason: str, source_url: str, publisher: str, title: str | None, site_visit: bool,
              now: str) -> Added:
    ids = resolve_place(conn, place)
    if len(ids) != 1:
        raise PlaceError(f"「{place}」對應到 {len(ids)} 個地點：{', '.join(ids) or '無'}（請改用實體 id）")
    if not closed and not (opens and closes):
        raise ValueError("需要 --opens 和 --closes，或是 --closed")
    to = to or from_
    if to < from_:
        raise ValueError("--to 早於 --from")
    eid = ids[0]
    days = (to - from_).days + 1
    dows = tuple(sorted({(from_ + timedelta(days=i)).isoweekday() for i in range(min(days, 7))}))
    key = f"manual:{eid}:{from_}:{to}"
    src = SourceRef(source_url, title or reason, publisher, "manual" if site_visit else "announcement",
                    hashlib.sha256(key.encode()).hexdigest())
    rules = () if closed else (HoursRule(dows, opens, closes),)
    quote = f"{from_}~{to} " + ("閉館" if closed else f"{opens}~{closes}") + f" {reason}"
    rec = FactRecord(key, eid, "hours", {"closed": closed, "reason": reason}, from_, to + timedelta(days=1), "explicit", 2,
                     False, src, quote[:200], "manual", rules)
    displaced = displaced_by(conn, rec)
    out = Added(apply_fact(conn, rec, adapter="manual", review="verified", now=now), key)
    set_state(conn, "manual", key, "fact", content_hash(rec), now)
    for d in displaced:
        out.replaced.append(d["record_key"])
        st = conn.execute("SELECT adapter, content_hash FROM record_state WHERE record_key=? AND kind='fact'",
                          (d["record_key"],)).fetchone()
        if st is None or st["adapter"] == "manual":  # an earlier entry of the maintainer's: replaced whole (a typo)
            continue
        # crawled content the maintainer corrected stays corrected until the page itself changes
        suppress(conn, adapter=st["adapter"], record_key=d["record_key"], content_hash=st["content_hash"], kind="fact",
                 ref=d["id"], note="manual", now=now)
        for part in _other_days(conn, d, rec):
            apply_fact(conn, part, adapter="manual", review=d["review"], now=now)
            set_state(conn, "manual", part.key, "fact", content_hash(part), now)
            out.kept.append(part.key)
    bump_data_version(conn)
    conn.commit()
    return out


def _other_days(conn: sqlite3.Connection, d: sqlite3.Row, rec: FactRecord) -> list[FactRecord]:
    """The days of the replaced crawled fact d outside rec's range, as facts with d's content and source."""
    lo, hi = date.fromisoformat(d["valid_from"]), date.fromisoformat(d["valid_to"])
    s = conn.execute("SELECT * FROM source_doc WHERE id=?", (d["source_doc"],)).fetchone()
    src = SourceRef(s["url"], s["title"], s["publisher"], s["kind"], s["warc_sha256"],
                    date.fromisoformat(s["published_on"]) if s["published_on"] else None)
    rules = tuple(HoursRule(tuple(json.loads(r["dows"])), r["opens"], r["closes"])
                  for r in conn.execute("SELECT dows, opens, closes FROM hours_rule WHERE fact_id=? ORDER BY rowid", (d["id"],)))
    out = []
    for a, b in ((lo, min(hi, rec.valid_from)), (max(lo, rec.valid_to), hi)):
        if a < b:
            out.append(FactRecord(f"manual:{d['entity_id']}:{a}:{b - timedelta(days=1)}", d["entity_id"], d["attribute"],
                                  json.loads(d["value_json"]), a, b, d["valid_basis"], d["specificity"], bool(d["propagates"]),
                                  src, d["quote"], d["extraction"], rules, d["qualifier"]))
    return out
