-- facts.db: the fact store. Written by the crawler, review and manual entry; facts are append-only.
-- Dates are Asia/Taipei days, half-open [valid_from, valid_to); timestamps are UTC ISO-8601.
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
CREATE TABLE entity(id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('CollegeOrUniversity','Campus','Organization','ExerciseGym','Place','Library')),
  name_zh TEXT NOT NULL, name_en TEXT, campus TEXT CHECK (campus IN ('heping','yanchao')),
  wikidata_qid TEXT CHECK (wikidata_qid GLOB 'Q[0-9]*'),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','renamed','merged','closed'))) STRICT;
CREATE TABLE entity_relation(subject TEXT NOT NULL REFERENCES entity,
  predicate TEXT NOT NULL CHECK (predicate IN ('containedInPlace','parentOrganization','managedBy')),
  object TEXT NOT NULL REFERENCES entity, valid_from TEXT NOT NULL DEFAULT '0001-01-01',
  valid_to TEXT NOT NULL DEFAULT '9999-12-31', note TEXT,
  PRIMARY KEY (subject, predicate, object, valid_from), CHECK (valid_from < valid_to)) STRICT;
CREATE TABLE entity_alias(entity_id TEXT NOT NULL REFERENCES entity, alias TEXT NOT NULL, alias_norm TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('official','short','colloquial','historical','en','typo')),
  ambiguous INTEGER NOT NULL DEFAULT 0 CHECK (ambiguous IN (0,1)), PRIMARY KEY (entity_id, alias_norm)) STRICT;
CREATE INDEX entity_alias_norm ON entity_alias(alias_norm);
CREATE TABLE attribute_authority(attribute TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES entity,
  org_id TEXT NOT NULL REFERENCES entity, PRIMARY KEY (attribute, entity_id)) STRICT;
CREATE TABLE source_doc(id INTEGER PRIMARY KEY, url TEXT NOT NULL, title TEXT, publisher TEXT NOT NULL REFERENCES entity,
  kind TEXT NOT NULL CHECK (kind IN ('table_page','announcement','ics','manual')), adapter TEXT NOT NULL,
  parent_id INTEGER REFERENCES source_doc, evidence_group INTEGER, published_on TEXT, fetched_at TEXT NOT NULL,
  warc_sha256 TEXT NOT NULL, pii_level TEXT NOT NULL DEFAULT 'none' CHECK (pii_level IN ('none','contact','personal')),
  UNIQUE (url, warc_sha256)) STRICT;
CREATE TABLE fact(id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL, record_key TEXT NOT NULL,
  entity_id TEXT NOT NULL REFERENCES entity, attribute TEXT NOT NULL CHECK (attribute IN ('hours','holiday')),
  qualifier TEXT NOT NULL DEFAULT '', value_json TEXT NOT NULL CHECK (json_valid(value_json)),
  valid_from TEXT NOT NULL CHECK (valid_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  valid_to   TEXT NOT NULL CHECK (valid_to   GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  valid_basis TEXT NOT NULL CHECK (valid_basis IN ('explicit','term','ttl')),
  specificity INTEGER NOT NULL CHECK (specificity BETWEEN 0 AND 2),
  propagates INTEGER NOT NULL DEFAULT 0 CHECK (propagates IN (0,1)),
  source_doc INTEGER NOT NULL REFERENCES source_doc, tier INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 4),
  quote TEXT CHECK (length(quote) <= 200), extraction TEXT NOT NULL CHECK (extraction IN ('rule','ics','manual')),
  rank TEXT NOT NULL DEFAULT 'normal' CHECK (rank IN ('preferred','normal','deprecated')), rank_reason TEXT,
  review TEXT NOT NULL CHECK (review IN ('auto_ok','verified','rejected')),
  recorded_from TEXT NOT NULL, recorded_to TEXT, supersedes TEXT REFERENCES fact,
  CHECK (valid_from < valid_to), CHECK (rank <> 'deprecated' OR rank_reason IS NOT NULL),
  CHECK (recorded_to IS NULL OR recorded_from <= recorded_to)) STRICT;
CREATE UNIQUE INDEX fact_one_current ON fact(lineage_id) WHERE recorded_to IS NULL;
CREATE INDEX fact_lookup ON fact(entity_id, attribute, valid_from) WHERE recorded_to IS NULL;
CREATE TABLE hours_rule(fact_id TEXT NOT NULL REFERENCES fact,
  dows TEXT NOT NULL CHECK (json_valid(dows) AND json_array_length(dows) > 0),
  opens TEXT NOT NULL CHECK (opens GLOB '[0-2][0-9]:[0-5][0-9]'),
  closes TEXT NOT NULL CHECK (closes GLOB '[0-2][0-9]:[0-5][0-9]'), PRIMARY KEY (fact_id, dows, opens)) STRICT;
CREATE TABLE calendar_event(uid TEXT PRIMARY KEY, summary TEXT NOT NULL, during_from TEXT NOT NULL, during_to TEXT NOT NULL,
  category TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('confirmed','tentative','cancelled','withdrawn')),
  sequence INTEGER NOT NULL DEFAULT 0, source_doc INTEGER NOT NULL REFERENCES source_doc, last_modified TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, CHECK (during_from < during_to)) STRICT;
CREATE TABLE doc(id INTEGER PRIMARY KEY, adapter TEXT NOT NULL, doc_key TEXT NOT NULL, title TEXT NOT NULL, unit TEXT,
  category TEXT, published_on TEXT, announce_until TEXT, url TEXT NOT NULL, excerpt TEXT CHECK (length(excerpt) <= 200),
  attachments_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(attachments_json)),
  pii_level TEXT NOT NULL DEFAULT 'none', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, withdrawn_at TEXT,
  UNIQUE (adapter, doc_key)) STRICT;
CREATE TABLE record_state(adapter TEXT NOT NULL, record_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('fact','event','doc')), content_hash TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  missing_full_scans INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')), PRIMARY KEY (adapter, record_key)) STRICT;
CREATE TABLE pending_change(id INTEGER PRIMARY KEY, adapter TEXT NOT NULL, record_key TEXT NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('upsert','retire')), record_json TEXT NOT NULL CHECK (json_valid(record_json)),
  content_hash TEXT NOT NULL, diff TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','superseded')),
  decided_at TEXT) STRICT;
CREATE UNIQUE INDEX pending_one_open ON pending_change(adapter, record_key) WHERE status = 'pending';
CREATE TABLE alert(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, adapter TEXT,
  level TEXT NOT NULL CHECK (level IN ('info','warn','error')), code TEXT NOT NULL, message TEXT NOT NULL,
  dedup_key TEXT, resolved_at TEXT) STRICT;
CREATE UNIQUE INDEX alert_open_dedup ON alert(dedup_key) WHERE resolved_at IS NULL AND dedup_key IS NOT NULL;
CREATE TABLE crawl_run(id INTEGER PRIMARY KEY, adapter TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  full_scan INTEGER NOT NULL, n_snapshots INTEGER NOT NULL DEFAULT 0, n_records INTEGER NOT NULL DEFAULT 0,
  passed INTEGER, n_written INTEGER NOT NULL DEFAULT 0, n_pending INTEGER NOT NULL DEFAULT 0, error TEXT) STRICT;

-- fact is append-only: no DELETE; the only legal UPDATE closes an open recorded_to (= the version was superseded)
CREATE TRIGGER fact_no_delete BEFORE DELETE ON fact
BEGIN SELECT RAISE(ABORT, 'fact is append-only'); END;
CREATE TRIGGER fact_only_close BEFORE UPDATE ON fact
WHEN OLD.recorded_to IS NOT NULL OR NEW.recorded_to IS NULL
     OR NEW.id IS NOT OLD.id
     OR NEW.lineage_id IS NOT OLD.lineage_id
     OR NEW.record_key IS NOT OLD.record_key
     OR NEW.entity_id IS NOT OLD.entity_id
     OR NEW.attribute IS NOT OLD.attribute
     OR NEW.qualifier IS NOT OLD.qualifier
     OR NEW.value_json IS NOT OLD.value_json
     OR NEW.valid_from IS NOT OLD.valid_from
     OR NEW.valid_to IS NOT OLD.valid_to
     OR NEW.valid_basis IS NOT OLD.valid_basis
     OR NEW.specificity IS NOT OLD.specificity
     OR NEW.propagates IS NOT OLD.propagates
     OR NEW.source_doc IS NOT OLD.source_doc
     OR NEW.tier IS NOT OLD.tier
     OR NEW.quote IS NOT OLD.quote
     OR NEW.extraction IS NOT OLD.extraction
     OR NEW.rank IS NOT OLD.rank
     OR NEW.rank_reason IS NOT OLD.rank_reason
     OR NEW.review IS NOT OLD.review
     OR NEW.recorded_from IS NOT OLD.recorded_from
     OR NEW.supersedes IS NOT OLD.supersedes
BEGIN SELECT RAISE(ABORT, 'fact is append-only: only closing recorded_to is allowed'); END;
-- one source (= one page URL) may not say two things about the same slot at the same time;
-- different sources may conflict and resolve() reports it
CREATE TRIGGER fact_same_source_no_overlap BEFORE INSERT ON fact
WHEN NEW.recorded_to IS NULL AND EXISTS (
  SELECT 1 FROM fact f JOIN source_doc s ON s.id = f.source_doc
   WHERE f.recorded_to IS NULL AND f.lineage_id <> NEW.lineage_id
     AND s.url = (SELECT url FROM source_doc WHERE id = NEW.source_doc)
     AND f.entity_id = NEW.entity_id AND f.attribute = NEW.attribute AND f.qualifier = NEW.qualifier
     AND f.specificity = NEW.specificity AND f.valid_from < NEW.valid_to AND NEW.valid_from < f.valid_to)
BEGIN SELECT RAISE(ABORT, 'overlapping fact from the same source'); END;
CREATE TRIGGER hours_rule_no_delete BEFORE DELETE ON hours_rule
BEGIN SELECT RAISE(ABORT, 'hours_rule is append-only'); END;
CREATE TRIGGER hours_rule_no_update BEFORE UPDATE ON hours_rule
BEGIN SELECT RAISE(ABORT, 'hours_rule is append-only'); END;
