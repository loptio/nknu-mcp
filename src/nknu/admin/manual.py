"""`nknu add hours`: maintainer-entered facts (image-only announcements, on-site checks). Written as verified.

Entering a range again with the same source URL replaces the overlapping earlier entry (C1), so a mistyped range is
corrected by entering the right one; a mistake that overlaps nothing is taken down with `nknu undo`.

An entry that cites a crawled page corrects only the days it covers: a crawled fact of that page that also covers other
days is replaced (C1), and its other days are written again with the school's own content as kept parts of it
(writer.kept_parts: keys {crawled key}:kept:{first}:{last}, under the crawled adapter), so correcting 10/26 of a
10/25-10/26 closure leaves 10/25 closed. The replaced crawled content is remembered (note='manual'): the crawler does not
write it back over the correction, but each time the page still states it the crawler writes its kept parts again if
they are gone (after a 404 the page answers again). The kept parts live and die with what the school says: they count as
listed while the page states the crawled fact, are retired with it (three full scans without it, a 404 page) and are
replaced by the page's new statement wherever the old one overlapped it (writer.displaced_by), like the correction."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from nknu.core.factsdb import bump_data_version
from nknu.core.records import FactRecord, HoursRule, SourceRef, content_hash
from nknu.core.vocab import resolve_place
from nknu.core.writer import (
    apply_fact,
    current_fact,
    displaced_by,
    fact_record,
    kept_parent,
    kept_parts,
    last_version,
    set_state,
    suppress,
)


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
    corrected: dict[str, tuple[str, str, FactRecord]] = {}  # crawled key -> (adapter, review, its last full statement)
    for d in displaced:
        out.replaced.append(d["record_key"])
        parent = kept_parent(d["record_key"]) or d["record_key"]
        st = conn.execute("SELECT adapter, content_hash FROM record_state WHERE record_key=? AND kind='fact'",
                          (parent,)).fetchone()
        if st is None or st["adapter"] == "manual":  # an earlier entry of the maintainer's: replaced whole (a typo)
            continue
        if parent == d["record_key"]:
            # crawled content the maintainer corrected stays corrected until the page itself changes
            suppress(conn, adapter=st["adapter"], record_key=parent, content_hash=st["content_hash"], kind="fact",
                     ref=d["id"], note="manual", now=now)
        whole = d if parent == d["record_key"] else last_version(conn, parent)
        corrected[parent] = (st["adapter"], whole["review"], fact_record(conn, whole))
    for adapter, review, whole in corrected.values():
        for part in kept_parts(conn, whole):
            if current_fact(conn, part.key) is None:
                apply_fact(conn, part, adapter=adapter, review=review, now=now)
                set_state(conn, adapter, part.key, "fact", content_hash(part), now)
                out.kept.append(part.key)
    bump_data_version(conn)
    conn.commit()
    return out
