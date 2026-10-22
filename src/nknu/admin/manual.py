"""`nknu add hours`: maintainer-entered facts (image-only announcements, on-site checks). Written as verified."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta

from nknu.core.factsdb import bump_data_version
from nknu.core.records import FactRecord, HoursRule, SourceRef, content_hash
from nknu.core.vocab import resolve_place
from nknu.core.writer import apply_fact, set_state


class PlaceError(ValueError):
    pass


def add_hours(conn: sqlite3.Connection, *, place: str, from_: date, to: date | None, opens: str | None, closes: str | None,
              closed: bool, reason: str, source_url: str, publisher: str, title: str | None, site_visit: bool, now: str) -> str:
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
    fid = apply_fact(conn, rec, adapter="manual", review="verified", now=now)
    set_state(conn, "manual", key, "fact", content_hash(rec), now)
    bump_data_version(conn)
    conn.commit()
    return fid
