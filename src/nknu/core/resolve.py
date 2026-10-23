"""Deterministic five-step resolver: R1 validity -> R2 specificity -> R3 tier -> R4 recency -> R5 conflict (never guess).

Ported from prototypes/datamodel/resolve.py. The backend works on facts.db and on serve.db (same tables).
A candidate is a dict with: id, entity_id, depth, value, lo, hi (dates, half-open), valid_basis, specificity, tier,
published_on (date | None), evidence_group, source_url, source_title, publisher, rules [{dows, opens, closes}].
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date


def _vkey(c):
    """Comparable value: closed flag + sorted rules (reason text is informational, not compared)."""
    rules = sorted((tuple(r['dows']), r['opens'], r['closes']) for r in (c.get('rules') or []))
    return (bool(c['value'].get('closed')), tuple(rules), json.dumps({k: v for k, v in c['value'].items()
                                                                      if k not in ('closed', 'reason', 'label')}, sort_keys=True))


def day_hours(c, t: date):
    if c['value'].get('closed'):
        return {'open': False, 'reason': c['value'].get('reason')}
    segs = [{'opens': r['opens'], 'closes': r['closes']} for r in (c.get('rules') or []) if t.isoweekday() in r['dows']]
    if not segs and c.get('rules'):
        return {'open': False, 'reason': 'not scheduled on this weekday'}
    return {'open': True, 'segments': segs} if segs else {'open': None}


def _brief(c):
    return {'fact_id': str(c['id']), 'entity_id': c['entity_id'], 'inherited': c['depth'] > 0,
            'valid': [c['lo'].isoformat(), c['hi'].isoformat()], 'valid_basis': c['valid_basis'],
            'specificity': c['specificity'], 'tier': c['tier'],
            'published_on': c['published_on'].isoformat() if c['published_on'] else None,
            'source': {'url': c['source_url'], 'title': c['source_title'], 'publisher': c['publisher']},
            'value': c['value'], 'rules': c.get('rules') or []}


def resolve(backend, entity, attribute, t: date, qualifier='', as_of: str | None = None):
    """as_of: UTC ISO timestamp for a transaction-time view; None = current versions."""
    cands = backend.candidates(entity, attribute, qualifier, t, as_of)
    trace = []

    # R1 validity
    A = [c for c in cands if c['lo'] <= t < c['hi']]
    trace.append({'rule': 'R1', 'kept': len(A), 'dropped': len(cands) - len(A)})
    if not A:
        past = [c for c in cands if c['hi'] <= t]
        base = [c for c in past if c['specificity'] < 2]   # last *schedule*, never a holiday row
        last = max(base, key=lambda c: c['hi']) if base else None
        # last_known is a full brief: a stale answer states the last known hours with their validity and source (U3)
        return {'found': False, 'status': 'stale' if last else 'no_data', 'decided_by': 'R1', 'conflict': False,
                't': t.isoformat(), 'trace': trace, 'last_known': _brief(last) if last else None}
    decided = 'R1'

    # R2 specificity (exception > term table > regular)
    m = max(c['specificity'] for c in A)
    B = [c for c in A if c['specificity'] == m]
    trace.append({'rule': 'R2', 'kept': len(B), 'dropped': len(A) - len(B)})
    if len(B) < len(A):
        decided = 'R2'

    # R3 authority tier (1 best), relative to the entity's managing unit (frozen at load)
    m = min(c['tier'] for c in B)
    C = [c for c in B if c['tier'] == m]
    trace.append({'rule': 'R3', 'kept': len(C), 'dropped': len(B) - len(C)})
    if len(C) < len(B):
        decided = 'R3'

    # copies inside one evidence group (announcement body + its attachment) count once
    seen, D = set(), []
    for c in sorted(C, key=lambda c: (c['depth'], str(c['id']))):
        if c['evidence_group'] not in seen:
            seen.add(c['evidence_group'])
            D.append(c)

    # R4 recency within the same tier, only when every candidate is dated and the max is unique
    if len({_vkey(c) for c in D}) > 1:
        dates = [c['published_on'] for c in D]
        if all(dates):
            newest = max(dates)
            E = [c for c in D if c['published_on'] == newest]
            if len({_vkey(c) for c in E}) == 1:
                trace.append({'rule': 'R4', 'kept': len(E), 'dropped': len(D) - len(E)})
                D, decided = E, 'R4'
    # R5 never guess
    if len({_vkey(c) for c in D}) > 1:
        trace.append({'rule': 'R5', 'kept': len(D), 'dropped': 0})
        return {'found': True, 'status': 'conflict', 'conflict': True, 'decided_by': 'R5', 't': t.isoformat(),
                'trace': trace, 'alternatives': [dict(_brief(c), today=day_hours(c, t)) for c in D]}
    win = D[0]
    return {'found': True, 'status': 'current', 'conflict': False, 'decided_by': decided, 't': t.isoformat(),
            'trace': trace, 'winner': _brief(win), 'supporting': [_brief(c)['fact_id'] for c in D[1:]],
            'today': day_hours(win, t)}


brief = _brief


class SQLiteBackend:
    SQL = """
    WITH RECURSIVE anc(id, depth) AS (
      SELECT :e, 0
      UNION ALL
      SELECT r.object, a.depth + 1 FROM entity_relation r JOIN anc a ON r.subject = a.id
       WHERE r.predicate = 'containedInPlace' AND r.valid_from <= :t AND :t < r.valid_to AND a.depth < 6)
    SELECT f.id, f.entity_id, anc.depth, f.value_json AS value, f.valid_from AS lo, f.valid_to AS hi, f.valid_basis,
           f.specificity, f.tier, s.published_on, coalesce(s.evidence_group, s.id) AS evidence_group, s.url AS source_url,
           s.title AS source_title, s.publisher,
           (SELECT json_group_array(json_object('dows', json(h.dows), 'opens', h.opens, 'closes', h.closes))
              FROM (SELECT * FROM hours_rule WHERE fact_id = f.id ORDER BY dows, opens) h) AS rules
      FROM fact f JOIN anc ON f.entity_id = anc.id JOIN source_doc s ON s.id = f.source_doc
     WHERE f.attribute = :a AND f.qualifier = :q AND (anc.depth = 0 OR f.propagates = 1)
       AND f.rank <> 'deprecated' AND f.review IN ('auto_ok', 'verified')
       AND CASE WHEN :asof IS NULL THEN f.recorded_to IS NULL
                ELSE f.recorded_from <= :asof AND (f.recorded_to IS NULL OR :asof < f.recorded_to) END"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def candidates(self, e: str, a: str, q: str, t: date, as_of: str | None) -> list[dict]:
        cur = self.conn.execute(self.SQL, {"e": e, "a": a, "q": q, "t": t.isoformat(), "asof": as_of})
        cols = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            d = dict(zip(cols, row, strict=True))
            d["value"] = json.loads(d["value"])
            d["rules"] = sorted(json.loads(d["rules"]), key=lambda x: (x["dows"], x["opens"]))
            d["lo"], d["hi"] = date.fromisoformat(d["lo"]), date.fromisoformat(d["hi"])
            d["published_on"] = date.fromisoformat(d["published_on"]) if d["published_on"] else None
            out.append(d)
        return out
