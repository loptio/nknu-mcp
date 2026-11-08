"""The content database (one file per build, written by nknu.content.build, read-only for nknu.retrieval).

No status column anywhere: absolute status (over, ongoing, not started) is computed at query time from the written
dates in `dates` (owner decision 11-01: three facts, no stored current/old label). Chunk text is not stored twice: a
chunk is docs.text[start:end], its table header (T6) docs.text[head_start:head_end].

pipeline-1 (plan 2026-11-02 T7a, addendum B): docs.parent_title and docs.text_source; the chunks' table-header
offsets; doc_refs, every page document linking an attachment; chunk_vecs, the bge-m3 vectors T7b fills. T7d (addendum
F, same phase, same version): the unit tree (units, unit_aliases) and each document's scope (doc_scope), written by the
build's unit stage; a build without a site list leaves them empty, and search then treats every document as neutral.

pipeline-2 (owner 11-03, 「可以，这样砍」; plan addendum L): no series. The tables series and series_members, the column
docs.series_id and their indexes are gone, and no date is read from a file name (dates.kind 'filename'): each document
is its own search result, ranked by score.

pipeline-3 (plan 2026-11-08 T1): stable ids. A document's id is 'd' + its corpus record's id, which assemble derives
from the record's identity (nknu.content.ids; meta id_scheme says which scheme the corpus used); doc_alias keeps the id
of every record the build left out under another document, which read resolves; docs.tier says which documents are
background material (addendum M2 by source), which search reads in place of the 11-08 rule on the id's shape.

pipeline-4 (plan 2026-11-08 T4): one database from many exports. sites holds each export's site (its name, unit,
资料截至, missing pages and files, documents, folder), docs.site the export each document came from (respond shows
that site's 资料截至 with each result) and docs.unit_line the document's unit line (單位：<unit>（<short names>）
<組別>), the second line of every passage (nknu.content.embed). A database without them (pipeline-3 and before) keeps
the texts and passages of then.

pipeline-5 (owner 11-08 10:16, N11): chunk_fts.unit_tokens, the unit words in a column of their own.

pipeline-6 (owner 11-09, blueprint item 15, 「照建议定」): sites.deleted_notices, how many of the site's notices the
school deleted (only the template left) the build left out (documents.deleted_notice); a database before it has no
such column, and the per-site report shows nothing for it.
"""

from __future__ import annotations

import sqlite3

# pipeline-6 (owner 11-09, blueprint item 15): sites.deleted_notices
SCHEMA_VERSION = "pipeline-6"
# docs.doc_type: journal | exam | minutes | '' (build.doc_type, spec 2026-11-01 「排序 v2」); build.tier_of makes all
# three background in docs.tier, which search ranks after the primary documents (addendum M2; plan 2026-11-08 T1)
JOURNAL = "journal"  # 學報: issues, tables of contents, abstracts, papers; since 11-09 (owner, blueprint item 17)
#                       教务处's old 校刊 and 校訊 folders too (build._PAPER_DIRS)
EXAM = "exam"  # past exam papers (歷屆考題, 考古題)
MINUTES = "minutes"  # a file of meeting minutes and their material (owner 11-08 10:16, addendum M9)
# docs.tier (plan 2026-11-08 T1; addendum M2): background material is ranked after the first primary documents
PRIMARY = "primary"
BACKGROUND = "background"  # archive members, journals, exams, meeting minutes
# doc_alias.why (plan 2026-11-08 T1): a copy at another address, another format of it, a second record of its identity
ALIAS_WHYS = ("copy", "format", "duplicate")

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE docs (
    doc_id      TEXT PRIMARY KEY,           -- 'd' + corpus id ('du…', 'dm…'; 'dp9' in a corpus with ids of its own);
                                            -- events 'e' + calendar event id
    kind_class  TEXT NOT NULL,              -- notice | page | attachment | event | link
    kind_raw    TEXT NOT NULL,              -- the corpus kind, as written
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    unit        TEXT NOT NULL,              -- the site table's unit (nknu.content.sites: its own address's site,
                                            -- else its export's: '教務處'); its publisher and scope are in doc_scope
    grp         TEXT NOT NULL DEFAULT '',   -- 組別: lbl_GroupName, else the gi -> group map
    parent_id   TEXT,                       -- attachments: the latest-dated page linking them from its text
    parent_title TEXT NOT NULL DEFAULT '',  -- the parent's title; without a parent, a left-out list page's title
    linktext    TEXT NOT NULL DEFAULT '',   -- the texts of the links to it; the file name when the title is a page's
    has_text    INTEGER NOT NULL,           -- 0: scanned, no text layer or binary; found by title only
    text        TEXT NOT NULL DEFAULT '',
    chars       INTEGER NOT NULL,
    period_ay   INTEGER,                    -- 學年度 the title names
    period_sem  INTEGER,                    -- 1 or 2; NULL when the title names a year only
    ident_date  TEXT,                       -- ISO date: the first date by IDENTITY_ORDER, NULL when none
    ident_kind  TEXT,                       -- dates.kind of ident_date
    doc_type    TEXT NOT NULL DEFAULT '',   -- journal | exam | minutes | '' (build.doc_type; lists, brochures stay '')
    text_source TEXT NOT NULL DEFAULT '',   -- totext | fallback-1029 | none (assemble); '' for the 10-29 corpus
    tier        TEXT NOT NULL DEFAULT 'primary', -- primary | background (build.tier_of: archive members, journals,
                                                 -- exams, meeting minutes)
    site        TEXT NOT NULL DEFAULT '',   -- the export it came from: a sites.site (plan 2026-11-08 T4)
    unit_line   TEXT NOT NULL DEFAULT ''    -- 單位：<unit>（<short names>） <組別>, a passage's second line; ''
                                            -- when its unit is the school and it has no 組別 (build.unit_words)
);

CREATE TABLE sites (                 -- one row per export's site (plan 2026-11-08 T4; build._site_rows)
    site           TEXT PRIMARY KEY, -- the export's 网站 (site key), as docs.site writes it
    name           TEXT,             -- the export's 名称, Traditional
    unit           TEXT,             -- the site table's unit (nknu.content.sites.unit_of_site)
    data_as_of     TEXT,             -- ISO date: the site's crawl day (corpus.export_info)
    missing_pages  INTEGER,          -- pages and files the export lists in missing.csv
    missing_files  INTEGER,
    docs           INTEGER,          -- documents whose docs.site it is (events included)
    export         TEXT,             -- the export folder the build read
    deleted_notices INTEGER          -- the school's deleted notices the build left out: documents.deleted_notice,
                                     -- owner 11-09 (blueprint item 15)
);

CREATE TABLE doc_alias (             -- an id the build left out under another document (plan 2026-11-08 T1)
    alias   TEXT PRIMARY KEY,        -- never a docs.doc_id
    doc_id  TEXT NOT NULL,           -- the document kept for it
    why     TEXT NOT NULL CHECK (why IN ('copy', 'format', 'duplicate'))
);

CREATE TABLE dates (
    doc_id  TEXT NOT NULL,
    kind    TEXT NOT NULL,   -- published|inherited|server|period_start|period_end|event_start|event_end
    date    TEXT NOT NULL,   -- ISO yyyy-mm-dd, Asia/Taipei calendar date
    raw     TEXT NOT NULL,   -- as written in the source
    source  TEXT NOT NULL    -- 公告頁日期欄|網頁上寫的日期|所屬公告|伺服器檔案時間|正文期間|行事曆; an inherited date:
                             -- 所屬公告, or 網頁上寫的日期 when its notice's came from there (plan 2026-11-08 T3)
);

CREATE TABLE applies_to (          -- the 適用於 relation; cohort is its qualifier (owner decision 11-01)
    doc_id        TEXT NOT NULL,
    unit          TEXT NOT NULL,
    cohort_from   INTEGER,         -- "X學年度起入學": X and later
    cohort_exact  INTEGER,         -- "X學年度入學": X only
    raw           TEXT NOT NULL
);

CREATE TABLE sections (
    doc_id   TEXT NOT NULL,
    sec_id   INTEGER NOT NULL,
    heading  TEXT NOT NULL,
    start    INTEGER NOT NULL,
    end      INTEGER NOT NULL,
    PRIMARY KEY (doc_id, sec_id)
);

CREATE TABLE chunks (
    chunk_id    INTEGER PRIMARY KEY,   -- = chunk_fts.rowid
    doc_id      TEXT NOT NULL,
    sec_id      INTEGER NOT NULL,
    start       INTEGER NOT NULL,
    end         INTEGER NOT NULL,
    head_start  INTEGER,               -- the header row of the table the chunk starts inside (chunks.table_header);
    head_end    INTEGER                -- NULL outside a table
);

CREATE TABLE doc_refs (              -- every page document that links an attachment (addendum B)
    doc_id   TEXT NOT NULL,          -- the attachment
    ref_id   TEXT NOT NULL,          -- the linking page's doc_id
    zone     TEXT NOT NULL,          -- content | attachments | unknown (no link found: a button) | nav (a menu)
    context  TEXT NOT NULL,          -- what that page calls the file ('' when it names nothing)
    PRIMARY KEY (doc_id, ref_id)
);

CREATE TABLE chunk_vecs (            -- float16 bge-m3 vectors, one per chunk (T7b); empty until then
    chunk_id  INTEGER PRIMARY KEY,
    vec       BLOB NOT NULL
);

-- the unit tree (addendum F, nknu.content.units): school -> colleges -> departments, school -> offices -> sections;
-- centres and programmes under their parents. Rows in units.load_units' order, the school first.
CREATE TABLE units (
    unit_id    TEXT PRIMARY KEY,     -- units.Unit.unit_id: the name the unit goes by here
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,        -- units.KINDS: 学校 | 行政单位 | 行政组别 | 学院 | 系所 | 中心 | 学程
    parent_id  TEXT,                 -- NULL for the school only
    campus     TEXT                  -- 和平 | 燕巢: its own, else its nearest ancestor's; NULL: both, or not known
);

CREATE TABLE unit_aliases (          -- every name a unit is matched by: its name first, then its aliases; no index,
    alias    TEXT NOT NULL,          -- so a plain scan reads them in this order
    unit_id  TEXT NOT NULL
);

-- each document's place in the tree (addendum F): the unit that published it, the units it applies to (inherited
-- downwards: a document for a college covers its departments, one for the school everyone) and the units it mentions
CREATE TABLE doc_scope (
    doc_id       TEXT NOT NULL,
    relation     TEXT NOT NULL CHECK (relation IN ('applies_to', 'publisher', 'mentions')),
    unit_id      TEXT NOT NULL,
    campus       TEXT,               -- applies_to: a campus the document writes (I2: never its publisher's); NULL: none
    level        TEXT,               -- applies_to: 學士 | 碩士 | 博士 | 進修 the document names; NULL: none
    cohort_from  INTEGER             -- applies_to: 「X學年度起入學」 X; NULL: none
);                                   -- applies_to: one row per unit x campus x level, the cohort on each

-- space-joined tokens from nknu.core.textnorm.tokenize plus nknu.content.terms.canonical_tokens; unit_tokens: the
-- document's unit words (build.unit_words), a column of their own since pipeline-5 (owner 11-08 10:16, N11): search
-- weighs them apart and counts a query word they alone have as part found (RankConfig.unit_weight, unit_cover)
CREATE VIRTUAL TABLE chunk_fts USING fts5(title_tokens, body_tokens, unit_tokens,
                                          tokenize='unicode61 remove_diacritics 0');

CREATE INDEX dates_doc ON dates(doc_id);
CREATE INDEX applies_doc ON applies_to(doc_id);
CREATE INDEX chunks_doc ON chunks(doc_id);
CREATE INDEX refs_ref ON doc_refs(ref_id);
CREATE INDEX scope_doc ON doc_scope(doc_id);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
