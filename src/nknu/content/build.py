"""Build the content database (plan 2026-11-01, Task E; plan 2026-11-02 T7a): a corpus (the one nknu.content.assemble
writes from the to-text run, or the 10-29 corpus) and one site's export become documents, typed dates, terms,
cohorts, sections, chunks (with their table headers) and the FTS index in one SQLite file. The inputs are only read;
the file is written next to its final name and moved over it whole, so a server reading the old one never
sees half a build, and a failed build leaves the old one. No LLM, no network.

References (addendum 2026-11-02 B): doc_refs lists every page document linking an attachment, with the zone (content,
attachments, unknown, nav) and what that page calls it; an attachment's parent and title come from them
(documents.py). Format siblings assemble folded into one record are listed as its copies, with their formats.

Dates (owner decision 11-01, changed later that day): a notice has the date its page shows (公告頁日期欄) and the
periods its body writes (正文期間); an attachment only inherits the published date of the notice it hangs on (所屬公告)
and has the server's Last-Modified when the crawl kept one (伺服器檔案時間); nothing is read from an attachment's own
text, nor since 11-03 from its file name. An event has its calendar days (行事曆). Plan 2026-11-08 T3: a notice no date
field dates (another platform's detail page) and a page have the date the export read on the page (網頁上寫的日期, the
manifest's 网页上写的日期, read after a label of putting the page up or changing it: documents._stated), the page's
as its own date only, and an attachment inherits such a notice's date with that source (_facts); the export's site
gives the unit of a record or event that names no site of its own (a corpus assembled before T3). Sub-step 4 (spec
2026-11-08 §四), from the step-3 export's columns: a date the platform's own field gave (公告頁日期欄, whatever page
it is on: documents._class) or the row of a list linking the page (列表上的日期: a notice's row on its list; since the
crawler's 53cf31b, blueprint item 22, the row of any list linking any page) needs no label (§二 2), and an
attachment's server time is its export row's 最后修改时间 (documents.Document.server_date, §二 3), else
server_dates.json's as before (an export made before 3c33a79 has no such column).

Terms (period_ay, period_sem): the first one a title names. A calendar event, whose title seldom names one, always has
one: its title's, else its first day's (terms.event_term; fix round 2, 11-01).

Copies (review 11-01): a page or attachment at several addresses with the same title and text (the same file bytes
when it has no text) is kept once; the others are counted as left out (<kind>(副本)) and listed with their id,
address, date and parent in meta under copies:<kept id>, which respond shows as `copies`. An attachment's title here
is its file name (a title from a page may differ between the addresses); the kept one takes the others' references
and list categories (blueprint item 22) and keeps their titles findable in its linktext.

Plan 2026-11-14 T2 (direction 2026-11-13 第 2 件; the owner 11-14 「你可以开始了」), three things, each with a switch
that builds as before, so one set of inputs built twice measures each alone:
- Copies, widened (copy_rule, --copy-rule text|title; COPY_TEXT is the default). 「一字不差的副本合成一份。跨站、同站同
  一个规则：同一类、去掉空白后相同、至少 200 字；公告也合；不要求标题相同」: besides the groups of the rule above (still
  made, as they were), documents of one kind_class, events aside, whose text is the same once all whitespace is removed
  and then at least COPY_MIN_CHARS long are one document, a notice of a detail path too, whatever their titles
  (_text_groups). Of such a group the earliest by its identity date is kept, 「显示最早的那份（原发单位）和它自己的日期」
  (an undated one after every dated one; on one day documents.home_rank, the plan's 「同一天照现有的 _rank」, then the
  order of before: _merge_copies; a primary document before any background one 【Claude】); the others are its copies
  as above, each with its own address and date, and their titles and file names join its link texts (_take_over). A
  group only the rule above makes (a text under 200 characters with one title, the same bytes with no text) keeps
  the choice of before, the one on the latest notice 【Claude: the plan names the earliest for the copies of its own
  rule】. A document whose words are not the document (has_text false: a scan with a few words) is no part of the new
  rule 【Claude】. COPY_TITLE is the rule above alone. Completed the same day, after the first rebuild (_rank_dates):
  the kept document shows its own date (docs.ident_date) and ranks by the latest among it and its copies
  (docs.rank_date, which nknu.retrieval.search reads: RankConfig.copy_dates), so that merging never ranks a document
  lower than before; BuildReport.dedupe['rank_dates'] counts them.
- Recurring calendar events (merge_recurring, --no-merge-recurring): the instances of one recurring event are one
  event document (documents.one_event_per_series, run on the events kept per id): 'e' + the series id, its first and
  last day as its two calendar dates, every instance's days in its text; the instances folded away are counted as
  行事历(重复系列), and the document's title tokens carry every term an instance falls in (_rows, _write_index), so a
  term still finds it.
- The alumni directory (alumni_background, --no-alumni-background): a page of the c.nknu template's 所友名錄, the page
  of one alumnus and the directory's list (ALUMNI_KINDS: the page kinds, never a site), is docs.tier background
  (tier_of); 「所友名录进背景（你 11-11 同意的）」.
BuildReport.dedupe (meta dedupe_json) counts all three: the copy groups, the documents they removed and, per site,
the groups whose kept document is the site's and the site's documents merged away; the series merged and the event
documents before and after; the alumni pages made background. meta copy_rule, recurring_events and alumni_background
say how the database was built.

Platform twins (the owner, 11-14 23:55: 「请接着把"公告平台和单位网站各一份"的那 191 对合并做掉，再重建一次（约 25 分钟）并更新
正式库。」; platform_twins, --no-platform-twins builds as before): a notice at a shared platform's address (sites: shared,
the announcement system and SSO's common parts) and a notice of the same identity date on a unit's own site are one
document when, page furniture, punctuation and list numbers aside, every line of one is found in the other
(_platform_twins says what is left out and why). Each one in the other: the same notice, kept as copies of one text
are (_merge_copies: an office's own site first). One wholly inside the other: the fuller one is kept, so no line the
school wrote leaves the database; but the platform's notice inside a longer one on a college's, a department's or a
programme's site is left alone (_merge_copies says why: such a page is often the department's own version). Two that
each hold a line the other lacks (an edited sentence, another deadline) stay two documents: hiding one would hide a
version that may be the right one. All of it is 【Claude】's reading of the owner's sentence; measured on the 111 sites
of 11-14 (research/pipeline_2026-11-02/v6_1114/near/: twin_rule2_1115.txt, the 192 pairs alike by the measure of
11-14, of which 38 each hold a line the other lacks; twin_check_1115.txt, this rule: 113 pairs the same, 77 one
inside the other, 21 of those a teaching unit's longer version). A merged copy whose words differ from the kept
document's says so in the copies' list (_copies_meta: wording_differs), and BuildReport.dedupe['twins'] counts the
pairs, the groups and the documents they removed, the longer versions left alone and the same-titled pairs left apart.

Stated publishing units (the owner, 11-15: 「在你的程序里读第 30 栏「公告单位」（合并时拿它定原发单位）」; stated_units,
--no-stated-units builds as before): the crawler's 2989f60 export carries, for the announcement platform's notice
pages, the words of their 「公告單位：」 cell (公告单位: 2,040 rows of the 11-15 exports, 82 spellings). A notice whose
page states a unit stands under that unit, with its section as its 組別, instead of the platform's placeholder or the
unit of the export that brought it, and the platform's files hanging on it follow (_stated_units, before the copies
are merged; units.name_unit places the words in the unit tree; words the tree does not name stay as written). The
merge reads it twice (_merge_copies): of a copy group one of whose copies states the publisher, only that unit's
copies stand for the group, its own site's before the platform's notice; and the platform's notice inside a longer one
on a department's site is merged into it when the platform states that department as the publisher. How it is done is
【Claude】's. BuildReport.publishers (meta publishers_json) counts the statements, those placed, the words left as
written and the files that followed; meta stated_units says on or off.

File titles (11-15, for the owner's 「请看一下附件标题取自链接文字还是档名的规则对不对，能修的话在这轮重建前一起修掉」;
all of it 【Claude】): an attachment is titled by what a page linking it from its text calls it, its file name
otherwise. The site map's names for the files of the menu it writes out no longer title them
(nknu.content.assemble: MENU_PAGES). A merged copy's link names stay findable in the document kept for it, not only
its title and file name (copy_names, --no-copy-names builds as before; _take_over, which says what was lost without
it; BuildReport.dedupe['copies']['names_carried']).
Label titles (label_titles, --label-titles; off unless asked): a title 20 or more attachments of one site take from
their pages (「檔案連結」, 「公文」) is a label many files stand under, and with the switch it gives way to the file name
when that has a Han character (documents.own_titles' TITLE_LABEL, run with the own titles below;
BuildReport.titles['label'] counts such files either way). Measured on the 111 sites of 11-15: 894 files so retitled,
and one of the 33 questions out of the top ten for it (documents.own_titles says which), so it waits for the owner.

Spec 2026-11-16 (docs/superpowers/specs/2026-11-16-data-fixes-design.md; the owner 11-16: 「可以，先写程序」), each with
a switch that builds as before; the schema stays pipeline-8, meta gains date_spikes_json, cohort_expiry and
file_row_dates:
- §二, three rules on dates: a file takes the date of its own row on a file table (file_row_dates,
  --no-file-row-dates; Document.row_date, ahead of its notice's date and the server's time); a day more than SPIKE_MIN
  files of one site share as their server time dates none of them (date_spikes, --no-date-spikes; _date_spikes,
  BuildReport.date_spikes); a document of one entry cohort alone goes out of date once that cohort has studied
  COHORT_YEARS years (cohort_expiry, --no-cohort-expiry; _expires_alone's 'cohort_single', _own_year).
- §五: a rebuild is not put in the place of the library in use when a site it built fell below half of that library's
  documents for it (_collapsed, SwapHeld: the new library stays beside it as <name>.held.db; --force-swap).
11-16, after the owner asked what tool the 「碎字」 PDFs need: none; documents.join_single_lines puts the runs of
one-character lines right when the documents are built (single_lines, --no-single-lines; BuildReport.single_lines,
meta single_lines_json).

Plan 2026-11-14 T3, T4 and T6 (direction 2026-11-13 第 3、4、6 件), the first two each with a switch that builds as
before:
- A calendar event's date line (event_dates, --no-event-dates; _event_lines): docs.date_line holds the event's term
  and days (documents.event_line: 「115學年度第2學期 2027-02-15～2027-02-18」; one day alone; a merged recurring event's
  first and last day and how many times it occurs). The line is the reranker's only (corrected 11-14 after the first
  rebuild, which had it in the vectors too: nknu.content.embed has the measurement): nknu.content.embed puts it
  after the unit line of the passages the reranker reads at query time (chunk_passages) and never into the text
  embedded here, which for an event is the one of before T3, so the vector cache of before serves it. A database
  without the column is read as before; the keyword index (chunk_fts) does not change. meta event_dates says rerank
  (EVENT_DATES_RERANK: the line is written, for the reranker) or off; on is what the two databases of 11-14's first
  rebuild say, whose vectors hold the line too.
- A document's own title (own_titles, --no-own-titles; _retitle): once the copies are merged, a page or a notice
  whose title 20 or more pages and notices of its site share, and an attachment titled by a file name without a Han
  character, take the title their own text gives (documents.own_titles: the first heading at the top, else the first
  line; not a line that is no title, nor one 20 documents of the site would share); the old title stays in linktext,
  so the index's title column holds both. What a title gives is then read again for those documents: the term
  (period_ay, period_sem), the cohort, the doc type and so the tier. BuildReport.titles (meta titles_json) counts
  the retitled and the kept per kind of title and per reason, per site, with examples to look at; meta own_titles
  says on or off.
- 「还有效」, always written (_validity, _expires_alone): docs.expires (1: the document goes out of date) and
  docs.valid_ay (the ROC academic year it belongs to), which hybrid.validity reads when HybridConfig.valid_decay is
  set. BuildReport.validity (meta validity_json) counts what expires per kind_class and per reason, and of the
  attachments on a document that expires what each clue kept from it (a fixed word in the title, a copy that does
  not expire, a cohort): the count that stands in for an audit by hand.

Ids (plan 2026-11-08 T1): a document's id is 'd' + its record's id, which assemble derives from the record's identity
(nknu.content.ids; meta id_scheme). The build stops (BuildError 「编号撞了」) when one id stands for two keys or one key
has two ids (a member's and an address's); records of one key are one document (documents.one_per_id, (重复)).
doc_alias keeps every id left out under another document, the copies (copy), the format siblings (format) and the
notices of one site and nid at another id (duplicate, a corpus with ids of its own), each pointing at the document
kept; read resolves them. docs.tier says background (an archive member, a journal, an exam: tier_of) or primary,
which search reads.

Calendar (owner 11-01): an event that starts before documents.CALENDAR_SINCE (2023-01-01, a fixed date that does not
roll) is left out and counted as 行事历(2023年以前); the export keeps it. Fix round 3's staff-schedule filter is gone.

Doc types (spec 「排序 v2」, 11-01): doc_type says journal (學報; since 11-09 教务处's old 校刊 and 校訊 too) or exam
(past exam papers), only where the title, the parent's title or the address says so plainly (doc_type); everything
else is ''. Lists and brochures are never typed.
tier_of makes both background in docs.tier, which search ranks after the primary documents (addendum M2; plan
2026-11-08 T1); search reads doc_type only in a database built before docs.tier (search.background_ids).

Vectors (plan 2026-11-02 T7b): with embed, every chunk's bge-m3 vector goes into chunk_vecs of the .tmp file before it
is moved into place (nknu.content.embed), so a failed or unavailable model leaves the old database; the model loads
before anything is written. A vector cache file (vec_cache) makes a rebuild encode only passages that changed.

No series (owner 11-03, 「可以，这样砍」; plan addendum L): editions of one thing are not grouped, there is no
correction table and no document gets a vector of its own; each document is its own search result, ranked by score.

Units (addendum 2026-11-02 F, T7d): with a site list (the command line always passes one), the unit tree that
nknu.content.units builds from it and config/units.yaml goes into units and unit_aliases (each unit's name, then its
aliases), and every document gets doc_scope rows: its publisher (the section of its unit that its 組別 names, else
its unit, the site table's since plan 2026-11-08 T3: nknu.content.sites; the section's campus is not the document's,
I2), applies_to (the publisher's reach, narrowed by what the title and the text's first SCOPE_HEADER characters name;
its first SCOPE_BODY characters only tell a list the header cuts off, I3; one row per unit x campus x level, NULL
where none is named, the cohort on each) and mentions.
build_report.json and meta scope_json count them. Without a site list the tables stay empty, and search treats every
document as neutral.

Many sites (plan 2026-11-08 T4; spec 2026-11-08 §二 5): the build reads any number of inputs, each a corpus and the
export it was assembled from (--input CORPUS::EXPORT, repeatable; --corpus/--export is the one-pair form). Per input
it reads the export's info, calendar, notice header fields and files (each record in its own export); one server-dates
map, keyed by address, serves them all. Records of one id from several inputs are one document
(documents.one_per_id: the newest 抓取时间, with what every record says) and events of one id are one
(documents.one_event_per_id); a corpus with ids of its own builds alone (BuildError). Each export is a row of the
sites table (its name, unit, 资料截至, missing pages and files, documents, folder and, since owner 11-09's blueprint
item 15, the school's deleted notices left out; two exports of one site are one row, the newer one's, with a
warning), and docs.site says which export a document came from, so each result shows its own site's 资料截至
(respond). meta keeps the first input in the keys of one input (corpus, export_dir, site, data_as_of
...; readers before T4 read them: gold's default corpus, check_realdata) and lists every input in inputs_json.

The unit patch (owner 11-08 04:08, plan 2026-11-08 T4): 「请把文件的单位名、组别（以及单位简称）加进索引和 passage，免得
搜『某系 + 某事』时搜不到对应系所的网页」. unit_words gives each document its unit's name and short names (the unit
tree's aliases), a section's office before it (學生事務處（學務處） 生活輔導組（生輔組）: T4 review), and its 組別 (with
the other names of the section it names: build.publisher_of), and since 11-08 10:16 the colleges, departments and
programs its scope applies to (適用：…); they go into every chunk's unit_tokens (title_tokens until 11-08 10:16), and
docs.unit_line (單位：<unit>（<short names>） <組別>) is the second line of every passage (nknu.content.embed.passage,
PASSAGE_VERSION 2), so the vector cache misses once for every chunk (BuildReport.passage). The school's own words are
left out: it is every unit's root (unit_words). Blueprint item 22 (owner 11-09, 「照这个做」): the crawler carries the
categories of the list rows linking a page (列表上的类别: documents.Document.categories, with those the other records
of its identity and its copies state: documents._joined, _take_over); they join the unit line as 類別：… after 適用,
so the keyword index's unit_tokens and the reranker read them, not the vectors, like the rest of the line 【Claude:
the place】. 12,898 kept records of the 11-09 exports have one, 1,883 of them in neither their title nor their text
(research/pipeline_2026-11-02/reading_gaps_1109/lists_1109g.log); by site, of the exports' 12,920 rows with one
(reexport_1109g.log there): sso.nknu.edu.tw/services 10,966 (專題演講或個人展演, 社團活動, 研討會 …),
sso.nknu.edu.tw/news 814, lis 625, the 17 SSO unit sites 500 in all (web/39 134, web/49 107, web/593 78, web/44 41,
web/578 40, …), news.nknu.edu.tw 9, c.nknu.edu.tw/aca 5, sso.nknu.edu.tw/stu 1. The report counts the documents
whose line names them (scope.unit_line_categories); a corpus assembled before these columns, beside an export that has
them, is warned of (build_sites: its pager pages stay and its lines name no category). The report lists the site
table's units the tree gives no short name (scope.site_units_no_alias). --no-unit-words leaves the whole patch out
(meta unit_words "off"), the units it applies to and the categories too, passages as version 1's: built twice from
one set of inputs, the two databases measure the whole patch, these parts together (T4 review; Review Focus 5).

    python -m nknu.content.build [--corpus research/pipeline_2026-11-02/corpus.json] [--export …] [--out …]
        [--site-list research/site_probe_2026-10-23/data.json] [--units config/units.yaml] [--no-unit-words]
    python -m nknu.content.build --input <corpus.json>::<export folder> [--input …] [--out …] …
        [--copy-rule text|title] [--no-merge-recurring] [--no-alumni-background] [--no-event-dates] [--no-own-titles]
    uv run --extra pipeline python -m nknu.content.build --embed [--vec-cache <out folder>/vec_cache.sqlite]
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from nknu.archive.scope import site_key
from nknu.content import ids, sites
from nknu.content.chunks import Chunk, make_chunks, split_sections
from nknu.content.corpus import (
    NO_DATA,
    TEMPLATE_PAGES,
    Event,
    Record,
    export_info,
    load_calendar,
    load_corpus,
    read_metas,
)
from nknu.content.dates import (
    FIELD_SOURCE,
    INHERITED_SOURCE,
    LIST_SOURCE,
    PAGE_SOURCE,
    DateFact,
    extract_periods,
    identity_date,
    load_server_dates,
)
from nknu.content.documents import (
    EVENT_KIND,
    LINK_SEP,
    SERIES,
    TITLE_FILE,
    TITLE_LABEL,
    TITLE_SHARED,
    Document,
    build_documents,
    deleted_notice,
    event_line,
    home_rank,
    kind_class,
    one_event_per_id,
    one_event_per_series,
    one_per_id,
    own_titles,
)
from nknu.content.embed import PAGE_SIZE, PASSAGE_VERSION, EmbedStats, VecCache, embed_chunks
from nknu.content.links import GENERIC
from nknu.content.schema import ALIAS_WHYS, BACKGROUND, EXAM, JOURNAL, MINUTES, PRIMARY, create_schema
from nknu.content.terms import (
    Term,
    canonical_tokens,
    current_term,
    event_term,
    find_cohorts,
    find_terms,
    first_term_at,
    term_tokens,
)
from nknu.content.units import (
    CAMPUSES,
    LEVELS,
    TEACHING_KINDS,
    Unit,
    ancestors,
    doc_scope,
    load_units,
    name_unit,
    reach,
    resolve,
)
from nknu.core import models
from nknu.core.textnorm import CJK, alias_norm, norm_text, tokenize
from nknu.core.timeutil import TAIPEI, today_tpe
from nknu.retrieval.aliases import load_aliases
from nknu.settings import REPO_ROOT

SKELETON = REPO_ROOT / "research" / "skeleton_2026-11-01"  # the skeleton's build: never written again
PIPELINE = REPO_ROOT / "research" / "pipeline_2026-11-02"
CORPUS = REPO_ROOT / "research" / "content_recon_2026-10-29" / "scratchpad_copy" / "content" / "aca_work" / "corpus.json"
CORPUS_NEW = PIPELINE / "corpus.json"  # nknu.content.assemble's output (CORPUS, the 10-29 text, is its fallback)
EXPORT = REPO_ROOT / "archive-export" / "aca-20261102"
OUT = PIPELINE / "aca.db"
SERVER_DATES = SKELETON / "server_dates.json"
ALIASES = REPO_ROOT / "config" / "aliases.yaml"
VEC_CACHE = "vec_cache.sqlite"  # the CLI's vector cache with --embed, beside the database unless --vec-cache
COHORT_ZONE = 300  # a cohort the title does not name is looked for in the text's first characters
EARLIEST, AHEAD = date(1990, 1, 1), timedelta(days=400)
EVENT_SOURCE = "行事曆"
NO_DATE = "none"
BATCH = 5_000  # FTS rows written at a time
COPY = "(副本)"  # excluded-count suffix: the same page or file at another address, kept once (review 11-01)
COPY_KINDS = ("page", "attachment")
COPIES_KEY = "copies:"  # meta key prefix: the kept document's id -> JSON list of its copies (url, date, parent;
#                          format for a format sibling)
# plan 2026-11-14 T2 (direction 2026-11-13 第 2 件 1): which documents are one (build_sites' copy_rule, --copy-rule).
# COPY_TEXT, the default: the rule of before and, whatever their titles, documents of one kind_class whose text is the
# same once all whitespace is removed and then at least COPY_MIN_CHARS long (the direction's 「至少 200 字」: under it
# two titles with one text are too often two things, 「簡章請見附件」). COPY_TITLE: the rule of before alone (one kind,
# title and text), so the database of before can be rebuilt and the two compared
COPY_TEXT, COPY_TITLE = "text", "title"
COPY_RULES = (COPY_TEXT, COPY_TITLE)
COPY_MIN_CHARS = 200
# The owner, 11-14 23:55 (the module docstring's 「Platform twins」; _platform_twins). COPY_TWIN: the name, in
# BuildReport.dedupe['copies']['by_rule'], of the copy groups a twin pair made or widened (no value of --copy-rule).
# The numbers are 【Claude】's: TWIN_MIN_CHARS, how much of its own a notice must say before its lines alone make it
# another's twin (under it two notices must share their title too: 「詳如附件」 is in many notices of one day);
# TWIN_TITLE_MIN, how long a shared title must be to count (「公告」 is no title); a line is page furniture when at
# least TWIN_TEMPLATE_MIN of a site's notices and one in TWIN_TEMPLATE_SHARE of them carry it (a menu, 「相關檔案」);
# TWIN_APART_SHOWN, the same-titled pairs left apart that the report lists
COPY_TWIN = "twin"
TWIN_MIN_CHARS, TWIN_TITLE_MIN = 30, 8
TWIN_TEMPLATE_MIN, TWIN_TEMPLATE_SHARE = 5, 50
TWIN_APART_SHOWN = 20
# on norm_text without whitespace: a line that is a file's name, with the size some sites write after it (an
# attachment list; the files are documents of their own, hung on the notice kept)
_TWIN_FILE = re.compile(r".+\.(?:pdf|docx?|xlsx?|pptx?|od[tsp]|rtf|txt|csv|zip|rar|7z|jpe?g|png|gif|bmp)(?:\([^()]*\))?")
# a line that is one date and nothing else, with a weekday or a time at most: year, month, day (_twin_lines leaves it
# out only when it is the notice's own date, which a unit's page prints beside the text and the platform's does not)
_TWIN_DATE = re.compile(r"(\d{2,4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})日?(?:\(?(?:星期|周)?[一二三四五六日]\)?)?"
                        r"(?:(?:上午|下午)?\d{1,2}[:时]\d{2}(?::\d{2})?分?)?")
# a line's list number, 「一、」「(二)」「3.」: one site writes it, another leaves it to an <ol>
# (not before a number: 「3.5 公里」 and 「一、二年級」 keep theirs, so a changed figure still tells two notices apart)
_TWIN_MARK = re.compile(rf"^[^{CJK}a-z0-9(]*(?:\((?:[一二三四五六七八九十]+|\d{{1,2}})\)"
                        r"|[一二三四五六七八九十]+、(?![一二三四五六七八九十])|\d{1,2}[.、)](?!\d))")
_SPACE = re.compile(r"\s+")  # every whitespace character (str.isspace's), as plan 2026-11-14 T1's scorer removes them
DEDUPE_KEY = "dedupe_json"  # meta: BuildReport.dedupe as JSON
LARGEST_GROUPS, TITLE_SHOWN = 10, 60  # the copy groups BuildReport.dedupe lists, and how much of a title 【Claude】
# plan 2026-11-14 T2 (direction 2026-11-13 第 2 件 4, 「所友名录进背景（你 11-11 同意的）」; the 11-11 design: 「按页种认
# （c.nknu 平台的所友详细页这一种），不按网站」): the page kinds of the c.nknu template's alumni directory, as
# pagetext.page_kind names a page of a c.nknu-template host (its leaf without .aspx; docs.kind_raw): the page of one
# alumnus (student_detail.aspx, which the crawler calls student_detail: 成教所's 1,976 on 11-09) and the directory's
# list (student.aspx; a row a press brought is student#翻页/按钮, left out already when the export marks it 列表翻页).
# No other platform gives these names (its pages are page:<profile>, detail:<profile>, page), so no site is named.
# A rule to keep up: another platform's directory would be another kind here
ALUMNI_KINDS = ("student_detail", "student")
# meta event_dates of a build that wrote the calendar events' date lines (plan 2026-11-14 T3, corrected the same day):
# the lines are in docs.date_line for the reranker's passages and in no vector. "on" is the value of the first rebuild
# of 11-14 (aca1114.db, sites111v6.db on the lab PC), whose vectors embed them too; "off": no line was written.
# A value of its own 【Claude】, so that a database says which of the two texts hold the line; nothing reads it back
EVENT_DATES_RERANK = "rerank"
TITLES_KEY = "titles_json"  # meta: BuildReport.titles as JSON (plan 2026-11-14 T4)
PUBLISHERS_KEY = "publishers_json"  # meta: BuildReport.publishers as JSON (the owner, 11-15: _stated_units)
VALIDITY_KEY = "validity_json"  # meta: BuildReport.validity as JSON (plan 2026-11-14 T6)
# plan 2026-11-14 T6 (direction 2026-11-13 第 6 件: 「标题带『辦法』『要點』『規則』『須知』『申請書』这类固定字眼」; the plan's
# list adds 準則、細則、章程、規程、申請表、表單; the main session's brief of 11-14 adds 規定 and 注意事項 【Claude】):
# the words of rules and forms in a title, on norm_text (Simplified). A document so titled does not go out of date
# (_expires_alone)
_FIXED_WORDS = re.compile(r"办法|要点|规则|须知|准则|细则|章程|规程|规定|注意事项|申请书|申请表|表单")
# docs.expires's reasons as BuildReport.validity counts them (_expires_alone, _validity): why a document goes out of
# date, and why it does not
EXPIRES_WHY = ("event", "title_year", "cohort_single", "notice", "dated_page", "parent")
# spec 2026-11-16 §二 【Claude】: more than this many files of one site with the same server day: the day the server
# was moved or a folder copied (_date_spikes; a judgment, the measured days are far above or below it: 96 pairs hold
# 21 to 326 files)
SPIKE_MIN = 20
# Only a shared day this recent is taken from its files (_date_spikes): one that would rank a file above an undated
# one, 365 x ln 2 days by search.RankConfig's defaults (w_recent x exp(-days / tau_days) against w_recent x
# no_date_factor: tau 365, factor 0.5; tests/content/test_dates_1116.py holds the two together). 【Claude, after the
# first trial build of 11-16】: left undated, the 1,247 files of 教务处's days of 2021 (the day its site moved
# platforms) gained the undated share, 0.15, over what a date of 2021 gives, 0.00, and two application forms passed
# the rule that answers N10; an old shared day ranks its files below an undated one as it is, and stays.
SPIKE_RECENT_DAYS = 253
DATE_SPIKES_KEY = "date_spikes_json"  # meta: BuildReport.date_spikes as JSON
# spec 2026-11-16 §二: the years a cohort normally studies 【Claude: the length of an undergraduate course, for every
# level】; a document of one entry cohort alone belongs to the last of them (_single_cohort, _own_year)
COHORT_YEARS = 4
# spec 2026-11-16 §五 【Claude】: a rebuild is held back when a site it built has fewer than half the documents the
# library in use holds for it, for a site of at least this many there (_collapsed, SwapHeld)
GUARD_MIN = 20
STAYS_WHY = ("undated", "fixed_words", "cohort", "standing_copy", "standing")
SOURCES = ("totext", "fallback-1029", "none", "corpus")  # BuildReport.text_sources, in the order the CLI prints
# the unit stage (addendum 2026-11-02 F, T7d)
SITE_LIST = REPO_ROOT / "research" / "site_probe_2026-10-23" / "data.json"  # the site probe's list: the units' names,
#                                                                             aliases, parents and kinds (read only)
UNITS = REPO_ROOT / "config" / "units.yaml"  # what that list lacks: campus, short names (units.load_units)
SCOPE_HEADER = 150  # doc_scope's header: the text's first characters (an attachment's title block, respond's 150)
SCOPE_BODY = 3_000  # the text doc_scope reads only to tell a list the header cuts off (main session, addendum I3)
RELATIONS = ("applies_to", "publisher", "mentions")  # doc_scope.relation, in the order a document's rows go in
SCOPE_KEY = "scope_json"  # meta: BuildReport.scope as JSON
SCOPE_SKIPPED = {"skipped": "没给单位名单（--site-list）：没有单位树，也没有每份文件的单位范围；搜索不按单位加减分"}
# doc_type patterns, on norm_text (simplified, lowercase); the address is its percent-decoded path
# the site's 學報 folder (issue contents, abstracts, papers) and, since owner 11-09 (blueprint item 17, 「照建议定」),
# 教务处's old 校刊 (/aca/uploadfile/spaper/, /~publish2/spaper/) and 校訊 (…/sinfo/) folders: text read from images, no
# date, page numbers for titles, and the first 7 results for 「校長是誰」 (a former president, with no date shown); 「建议
# 和学报一样算背景（在现有的学报规则里加两个资料夹）」. Only c.nknu.edu.tw/aca has such folders among the 111 exports.
_PAPER_DIRS = ("/nknupaper/", "/spaper/", "/sinfo/")
_ISSUE = re.compile(r"\d+\s*期\s*学报")  # 55期學報總檔, 上網-52期學報-目錄
_JOURNAL_PAGE = re.compile(r"学报(?![到考名])")  # 高雄師大學報; not 入學報到 / 入學報考 / 入學報名
_WHOLE_ISSUE = re.compile(r"总档|全上传")  # 1111228-53期-總檔, 51全上傳: a whole issue, on the 學報 page only
_ABOUT_JOURNAL = re.compile(r"征稿|投稿|撰稿|审查|格式|须知|启事|名单")  # forms and lists for authors: never papers
_EXAM = re.compile(r"考古题|历届[考试]题")  # 歷屆考題 folder; 歷屆簡章 is brochures, a bare 試題 can be a form
# a file of meeting minutes (owner 11-08 10:16): 會議紀錄 / 會議記錄 / 議事錄 in its title or its page's title; never
# a rule or a blank form (_is_minutes)
_MINUTES = re.compile(r"会议[纪记]录|议事录")
_RULE = re.compile(r"办法|要点|规则|规定|准则|章程|须知|细则|标准|注意事项|公约|守则|条例")
_FORM = re.compile(r"范本|范例|格式|表格|空白|申请表|[纪记]录表|样张|样本")
_LAWS = re.compile(r"法规|规章")  # a page 「相關法規及會議紀錄」 lists rules too
_NOTE = re.compile(r"\([^)]*\)|（[^）]*）|「[^」]*」|【[^】]*】")  # 會議紀錄(修正學生請假辦法): a note, not the title's kind
# an archive member's doc_id in a corpus assembled before 11-08 (a<序号>m<k>): still background (tier_of) 【Claude】
_OLD_MEMBER = re.compile(r"da\d+m\d+")
INPUT_SEP = "::"  # --input CORPUS::EXPORT (plan 2026-11-08 T4)
UNIT_LABEL = "單位："  # the unit line's label (plan 2026-11-08 T4's format); a passage's words, never an index token
APPLIES_LABEL = "適用："  # its second label (owner 11-08 10:16 (b)): the units the document's scope narrows to
# its third label (blueprint item 22, owner 11-09 「照这个做」; the place 【Claude】): the categories the list rows
# linking the document's page state (documents.Document.categories)
CATEGORY_LABEL = "類別："
APPLIES_KINDS = ("学院", "系所", "学程")  # units.KINDS a document's applies_to puts on its unit line (unit_words)
OFFICE_KIND, SECTION_KIND = "行政单位", "行政组别"  # the unit tree's kinds of an office and of its sections (office_of)
# BuildReport.passage (plan 2026-11-08 T4: 「Note that in the build report」): what changed in this PASSAGE_VERSION
# (the 適用 part since owner 11-08 10:16 (b), the 類別 part since blueprint item 22, owner 11-09 「照这个做」)
PASSAGE_NOTE = ("passage 第 2 版（计划 2026-11-08 T4，用户 11-08 04:08 要的单位补丁）：重排读的 passage 第二行多了单位行"
                "「單位：<单位>（<简称>） <组别>」，后面接「適用：<文件适用的学院、系所、学程>」（11-08 起）和"
                "「類別：<指到这一页的列表那一行写的类别>」（蓝图第 22 件，11-09 起）；单位是全校、又没有组别的文件没有"
                "「單位：」这一段，三段都没有的文件没有这一行（所以全校的文件可能只有「適用：…」或「類別：…」，"
                "像 sso.nknu.edu.tw/services 的活动页），关键词索引也有这些词；"
                "向量默认不放单位行（11-08 实验室电脑量过：放进向量，点名组别的问题会把同组别的文件都拉近），所以向量快取照常能用；"
                "加 --unit-words-in-vectors 才放进向量，那时第 1 版的向量快取对不上，第一次 --embed 每个小段都要重算")
# the same with --no-unit-words (plan 2026-11-08 T4 review: the patch measured alone, Review Focus 5; its 適用 and 類別
# parts go with it, so the two builds measure them together)
PASSAGE_NOTE_OFF = ("这次没加单位词（--no-unit-words，用来量单位补丁）：没有单位行，关键词索引（unit_tokens）里也没有单位名、组别、简称、"
                    "适用单位和列表类别，passage 和第 1 版一样，所以第 1 版的向量快取照样命中；和同样输入、加了单位词的库比，"
                    "差的是整个单位补丁（单位名、组别、简称、适用单位、列表类别一起，分不开）")


class BuildError(ValueError):
    """The inputs cannot be built as they are; nothing is written, and the command line prints the message (exit code
    2). Plan 2026-11-08 T1: an id that stands for two keys, a key with two ids (check_ids, across every input since
    T4). T4: a corpus paired with another site's export (_read_input: --input's two halves must pair, or its files and
    notice fields would be read in the wrong folder), and a corpus with serial ids (no keys: the 10-29 one, one
    assembled before T1) beside another input (its p1 is not another export's p1)."""


@dataclass
class BuildReport:
    counts: dict  # docs by kind_class (and total), sections, chunks, date facts by kind, cohort rows, doc types
    excluded: dict  # left-out records by kind (documents.build_documents)
    date_sources: dict  # kind_class -> identity-date kind (行事曆 for events, none) -> documents
    no_date: int  # documents shown as 無日期
    no_text: int  # found by title only
    warnings: list[str]
    text_sources: dict = field(default_factory=dict)  # kept docs by Document.source ('corpus' when it names none)
    vectors: dict = field(default_factory=dict)  # asdict(embed.EmbedStats) when the build embedded its chunks, else {}
    # T7d: site_list, units_config (paths and sha256), units, kinds, aliases, docs and rows per relation, narrowed
    # (total, title, header), named (units, campus, levels, cohort), publishers, grp_unplaced, seconds (unit_scope);
    # SCOPE_SKIPPED without a site list
    scope: dict = field(default_factory=dict)
    # plan 2026-11-08 T4: the sites table's rows (site, name, unit, data_as_of, missing_pages, missing_files, docs,
    # export; deleted_notices since owner 11-09, blueprint item 15), the inputs in order (corpus, export, site,
    # records, events) and what this passage version changed (version, unit_words: whether the build added them, note)
    sites: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    passage: dict = field(default_factory=dict)
    # plan 2026-11-14 T2: 'copies' (_copies_report: rule, min_chars, groups, removed, cross_site, by_rule, by_kind,
    # by_site: site -> kept, the groups whose kept document is its, and merged, its documents merged away; largest),
    # 'rank_dates' (_rank_dates: dated, the documents with a docs.rank_date; later, those that rank by a copy's later
    # day; from_copy, the undated ones that take a copy's; undated_copy, the dated ones a copy of which has no date),
    # 'recurring' (on, series: the recurring events merged, instances, removed, other_titles, id_taken, events_before,
    # events_after: the event documents without and with the merge, by_site: site -> series, instances) and 'alumni'
    # (on, background: the alumni directory's pages made background)
    dedupe: dict = field(default_factory=dict)
    # plan 2026-11-14 T3: {'on': whether the build wrote the calendar events' date lines (the reranker reads them; no
    # vector holds them), 'events': the events with one}
    event_dates: dict = field(default_factory=dict)
    # plan 2026-11-14 T4 (_retitle): documents.own_titles' report (on, same_min, max_chars, shared and file: documents,
    # retitled, kept, why; by_site; examples) and 'changed', what the new titles then gave: {'period': documents whose
    # new title set their term, 'typed': {doc type: documents their new title gave it}, 'untyped': {doc type:
    # documents whose new title took it away}}
    titles: dict = field(default_factory=dict)
    # plan 2026-11-14 T6 (_validity): 'expires' (documents that go out of date, per kind_class with any, and total),
    # 'why' (kind_class -> reason -> documents: EXPIRES_WHY and STAYS_WHY), 'attachments' (of those on a document that
    # expires: parent_expires, expire, kept, undated, clues: fixed_words, standing_copy, cohort), 'valid_ay' (where
    # each document's year came from: title, date, event, copy, none) and 'year_named' (documents that expire by the
    # year their title names though it holds a fixed word or states a cohort: fixed_words, cohort)
    validity: dict = field(default_factory=dict)
    # the owner, 11-15 (_stated_units): 'on'; 'stated', the documents whose page states its publishing unit (the
    # export's 公告单位); 'placed', those of them the unit tree names; 'as_written', the statements it does not name
    # (the words -> documents: their unit is those words); 'parts_as_written', the parts of a unit the tree lacks
    # (the words -> documents: their 組別); 'changed', the stated documents whose unit or 組別 is another than the
    # site table gave; 'files', the platform's files that follow the notice they hang on; 'units', the units named
    # (unit -> documents)
    publishers: dict = field(default_factory=dict)
    # spec 2026-11-16 §二 (_date_spikes): on, min, days (site and day pairs past SPIKE_MIN), documents (the files left
    # undated) and by_site
    date_spikes: dict = field(default_factory=dict)
    # 11-16 (documents.join_single_lines): on, documents (those it changed), joined (lines joined away), dropped (marks)
    single_lines: dict = field(default_factory=dict)


@dataclass(frozen=True)
class UnitWords:
    """A document's unit words (plan 2026-11-08 T4, the owner's unit patch): its unit's name and short names, its 組別
    as written and the other names of the section the 組別 names; for a unit that is a section of an office, the
    office's name and short names first (T4 review); then each college, department or program its scope applies to
    that these do not name already, with its short names (owner 11-08 10:16 (b)); then the categories of the list rows
    linking its page that none of these names (blueprint item 22, owner 11-09 「照这个做」). Empty parts are left out."""

    name: str = ""
    aliases: tuple[str, ...] = ()
    group: str = ""
    group_aliases: tuple[str, ...] = ()
    office: str = ""
    office_aliases: tuple[str, ...] = ()
    applies: tuple[tuple[str, tuple[str, ...]], ...] = ()  # (name, short names) per unit, in the tree's order
    categories: tuple[str, ...] = ()  # documents.Document.categories, in their order (item 22)

    def _parts(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (self.office, self.office_aliases), (self.name, self.aliases), (self.group, self.group_aliases)

    @property
    def line(self) -> str:
        """單位：[<office>（<its short names>） ]<unit>（<short names joined by 、>） <組別>（<its other names>）, the
        plan's format, then 適用：<unit>（<short names>） ... for the units it applies to, then 類別：<category>、...
        for its list rows' categories (blueprint item 22); '' when empty."""
        def said(parts: Sequence[tuple[str, tuple[str, ...]]]) -> str:
            return " ".join(w + (f"（{'、'.join(more)}）" if more else "") for w, more in parts if w)
        own, applies, categories = said(self._parts()), said(self.applies), "、".join(self.categories)
        return " ".join(([UNIT_LABEL + own] if own else []) + ([APPLIES_LABEL + applies] if applies else [])
                        + ([CATEGORY_LABEL + categories] if categories else []))

    @property
    def words(self) -> tuple[str, ...]:
        """Each name once, in the line's order (the categories last): what unit_tokens gets, a word at a time (no bigram
        across two)."""
        names = (w for name, more in (*self._parts(), *self.applies) for w in (name, *more))
        return tuple(dict.fromkeys(w for w in (*names, *self.categories) if w))


@dataclass
class _Row:
    doc: Document
    facts: list[DateFact]
    ident: DateFact | None
    ay: int | None
    sem: int | None
    cohorts: list[tuple[int, bool, str]]  # (學年度, onwards, as written)
    dtype: str = ""  # doc_type
    unit: UnitWords = field(default_factory=UnitWords)  # plan 2026-11-08 T4 (unit_words)
    # plan 2026-11-14 T2: the other terms the instances of a recurring event fall in (ay and sem are its first day's);
    # () for any other document
    terms: tuple[Term, ...] = ()
    # plan 2026-11-14 T3: docs.date_line, an event's term and days for the reranker's passages (_event_lines); '' else
    date_line: str = ""
    # plan 2026-11-14 T6 (_validity): docs.expires and docs.valid_ay
    expires: bool = False
    valid_ay: int | None = None
    # plan 2026-11-14 T2, completed (_rank_dates): docs.rank_date, the latest identity date among the document and the
    # copies merged into it (ident, its own, is what docs.ident_date and a response show)
    rank_date: date | None = None
    # plan 2026-11-14 T2 (main session, the same day): docs.rank_undated, a dated document one of whose merged copies
    # has no date; search gives it no less than an undated document gets (search._date_part)
    rank_undated: bool = False


def check_ids(records: list[Record]) -> None:
    """One key per id and one id per key, over the records that carry their key (plan 2026-11-08 T1). Raises
    BuildError naming both keys when two keys give one id (a collision of the 60-bit hash), and naming the key and its
    two ids when one key has a member's id and an address's. Records of one key and one id are one identity
    (documents.one_per_id keeps one)."""
    key_of: dict[str, str] = {}
    id_of: dict[str, str] = {}
    for r in records:
        if not r.key:
            continue
        if key_of.setdefault(r.id, r.key) != r.key:
            raise BuildError(f"编号撞了：{r.id} 是「{key_of[r.id]}」，也是「{r.key}」；不建库（换编号规则或回报）")
        if id_of.setdefault(r.key, r.id) != r.id:
            raise BuildError(f"编号撞了：「{r.key}」有两个编号 {id_of[r.key]} 和 {r.id}（压缩档成员和网址用了同一个身份）；不建库")


def id_scheme(records: list[Record]) -> str:
    """meta id_scheme: ids.ID_SCHEME when every record carries the key its id is derived from, else ids.SERIAL_SCHEME
    (the 10-29 corpus and hand-made ones: ids of their own, not stable across exports)."""
    return ids.ID_SCHEME if records and all(r.key for r in records) else ids.SERIAL_SCHEME


def alumni_page(kind_raw: str) -> bool:
    """Whether a document's kind (docs.kind_raw, the corpus record's kind) is a page of the c.nknu template's alumni
    directory (ALUMNI_KINDS; plan 2026-11-14 T2), a row a press brought too (<kind>#翻页/按钮)."""
    return kind_raw.partition("#")[0] in ALUMNI_KINDS


def tier_of(doc_id: str, dtype: str, kind_raw: str = "", alumni: bool = True) -> str:
    """docs.tier (plan 2026-11-08 T1; addendum M2 by source): BACKGROUND for an archive member (its record id starts
    with m, ids.record_id; an older corpus's a<序号>m<k>), for a journal, an exam or meeting minutes (doc_type) and,
    since plan 2026-11-14 T2, for a page of the alumni directory by its page kind (alumni_page of kind_raw; not with
    alumni=False, the build's --no-alumni-background, nor without a kind_raw: as before); PRIMARY else."""
    if doc_id.startswith("dm") or _OLD_MEMBER.fullmatch(doc_id) or dtype in (JOURNAL, EXAM, MINUTES):
        return BACKGROUND
    return BACKGROUND if alumni and alumni_page(kind_raw) else PRIMARY


class SwapHeld(Exception):
    """A rebuild that did not take the place of the library in use (spec 2026-11-16 §五): held, where the new library
    was put instead; sites, (site, documents in the library in use, documents in the new one) of each site that
    fell below half."""

    def __init__(self, held: Path, sites: list[tuple[str, int, int]]) -> None:
        self.held, self.sites = held, sites
        shown = "；".join(f"{site} {before:,} → {after:,}" for site, before, after in sites[:5])
        super().__init__(f"新库没有换上：{len(sites)} 个网站的文件数不到现在这份库的一半（{shown}"
                         f"{' 等' if len(sites) > 5 else ''}）。新库留在 {held}，现在的库没动；"
                         "看过确认要换，加 --force-swap 重建。")


def _collapsed(old: Path, new: Path) -> list[tuple[str, int, int]]:
    """The sites the new library built (its sites table) that hold fewer than half the documents the library at old
    holds for them, where that one holds GUARD_MIN or more: (site, before, after). [] when there is no library at
    old, or it cannot be read as one of this shape (a database before docs.site, a file that is no database): there
    is nothing to compare, and nothing a comparison could keep."""
    if not old.is_file():
        return []
    try:
        with closing(sqlite3.connect(f"file:{quote(str(old))}?mode=ro", uri=True)) as a, \
                closing(sqlite3.connect(f"file:{quote(str(new))}?mode=ro", uri=True)) as b:
            before = dict(a.execute("SELECT site, COUNT(*) FROM docs GROUP BY site"))
            after = dict(b.execute("SELECT site, COUNT(*) FROM docs GROUP BY site"))
            built = [r[0] for r in b.execute("SELECT site FROM sites ORDER BY site")]
    except sqlite3.Error:
        return []
    return [(site, before[site], after.get(site, 0)) for site in built
            if before.get(site, 0) >= GUARD_MIN and after.get(site, 0) * 2 < before[site]]


def _protected(out: Path, export_dir: Path | Sequence[Path]) -> bool:
    """The exports (one, or every input's: plan 2026-11-08 T4), ./data, archive*/ and the skeleton's folder are never
    written."""
    exports = [export_dir] if isinstance(export_dir, str | Path) else list(export_dir)
    target = Path(out).resolve()
    roots = [*(Path(e).resolve() for e in exports), (REPO_ROOT / "data").resolve(), SKELETON.resolve(),
             *(p.resolve() for p in REPO_ROOT.glob("archive*"))]
    return any(target == r or r in target.parents for r in roots)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _server_dates(path: Path | None, today: date, warnings: list[str]) -> dict[str, date]:
    if path is None or not Path(path).is_file():
        warnings.append(f"没有 server_dates.json（{path or '没给'}）：只有导出写了「最后修改时间」的附件有伺服器檔案時間"
                        "（3c33a79 以后的导出才有这一栏），别的附件挂在没日期的网页上时没有日期")
        return {}
    return {u: d for u, d in load_server_dates(Path(path)).items() if EARLIEST <= d <= today + AHEAD}


def _facts(d: Document, parent: Document | None, event: Event | None, server: dict[str, date],
           row_dates: bool = True) -> list[DateFact]:
    """A document's dates. A notice: the date it states (its date field, 公告頁日期欄; its list row's, 列表上的日期:
    sub-step 4; else the date the export read in its page's words, 網頁上寫的日期: plan 2026-11-08 T3) and the periods
    its body writes. An attachment: its notice's date, the server's time. An event: its days. A page: the date the
    export read on it, with the source documents._stated gave it, as its identity date (plan 2026-11-08 T3); its files
    never take it, nor is a page or a notice dated by the server's time (owner 11-01).

    An attachment's inherited date has the source 所屬公告 when its notice's date is a field's (the notice's date
    field, or its list row's), and keeps 網頁上寫的日期 when the export read it in the words of the notice's page, so
    one step down a date read from a page's words is never shown as a notice's own field (plan 2026-11-08 T3 review).
    Its server time is its export row's 最后修改时间 (Document.server_date: sub-step 4, spec 2026-11-08 §二 3), else
    server_dates.json's day of its address (an export made before 3c33a79, which has no such column).
    spec 2026-11-16 §二 (row_dates; the build's file_row_dates, --no-file-row-dates): ahead of both, the date on the
    file's own row of a file table (Document.row_date, source 列表上的日期), which the export's platform rule read:
    the first by IDENTITY_ORDER, so that is the day the file is shown and ranked by."""
    if d.kind_class == "notice":
        own = [DateFact("published", d.added, d.added_raw, d.added_source or FIELD_SOURCE)] if d.added else []
        return own + extract_periods(d.text)
    if d.kind_class == "page" and d.page_date:
        return [DateFact("published", d.page_date, d.page_date_raw, d.page_date_source or PAGE_SOURCE)]
    if d.kind_class == "attachment":
        out = []
        if row_dates and d.row_date:
            out.append(DateFact("published", d.row_date, d.row_date_raw, LIST_SOURCE))
        if parent is not None and parent.kind_class == "notice" and parent.added:
            source = PAGE_SOURCE if parent.added_source == PAGE_SOURCE else INHERITED_SOURCE
            out.append(DateFact("inherited", parent.added, parent.added_raw, source))
        # an archive member: its archive's time (its record carries its archive's row's 最后修改时间)
        if (day := d.server_date or server.get(d.url.split("#", 1)[0])) is not None:
            out.append(DateFact("server", day, day.isoformat(), "伺服器檔案時間"))
        return out
    if d.kind_class == "event" and event is not None:
        return [DateFact("event_start", event.start, event.start.isoformat(), EVENT_SOURCE),
                DateFact("event_end", event.end_inclusive, event.end_inclusive.isoformat(), EVENT_SOURCE)]
    return []


def _cohort_raw(src: str, ay: int, onwards: bool) -> str:
    """The words naming the cohort as written (whitespace squashed); a template when they cannot be found."""
    m = re.search(rf"(?<!\d){ay}(?!\d)\D{{0,12}}?(?:入學|入学|新生)", unicodedata.normalize("NFKC", src))
    return " ".join(m.group().split()) if m else f"{ay}學年度{'起' if onwards else ''}入學"


def _cohorts(d: Document) -> list[tuple[int, bool, str]]:
    """The cohorts the title names, else those the text's first COHORT_ZONE characters name; between the two, for a
    document plan 2026-11-14 T4 gave its own title, those its old title named (Document.old_title, '' else)."""
    for src in (d.title, d.old_title, d.text[:COHORT_ZONE]):
        if found := find_cohorts(src):
            return [(ay, onwards, _cohort_raw(src, ay, onwards)) for ay, onwards in found]
    return []


def _term(d: Document, event: Event | None) -> Term | None:
    """The first term the title names; a calendar event always has one (terms.event_term: else its first day's). A
    document plan 2026-11-14 T4 gave its own title keeps the term only its old title named (Document.old_title: a
    file name such as 115-1a.pdf) 【Claude】."""
    if d.kind_class == "event" and event is not None:
        return event_term(d.title, event.start)
    terms = find_terms(d.title) or (find_terms(d.old_title) if d.old_title else [])
    return terms[0] if terms else None


def doc_type(title: str, parent_title: str, url: str, kind_class: str = "") -> str:
    """JOURNAL, EXAM, MINUTES or '' (spec 「排序 v2」: only what the title, the parent's title or the address says
    plainly; rather too few than one wrong). exam: 考古題 / 歷屆考題 / 歷屆試題 in any of the three. journal: the site's
    學報 folder and, since 11-09 (owner, blueprint item 17), its old 校刊 and 校訊 folders (_PAPER_DIRS); a title
    naming an issue (55期學報); or a 總檔 / 全上傳 file whose parent is the 學報 page. A title about
    the journal's forms and lists (徵稿, 撰稿須知, 審查, 委員名單 ...) is never a paper.

    minutes (owner 11-08 10:16, 「其他网站很多会议纪录是单独上传的 PDF/Word，不在压缩包里」: 會議紀錄 / 會議記錄 /
    議事錄 in the title or the parent page's title, rules and blank forms left out, so 宿委會's minutes no longer push
    the rules off 「停車」): a file (kind_class attachment; a page named 會議紀錄 is where they are listed, and the
    owner named files 【Claude】) that _is_minutes."""
    t, p = norm_text(title), norm_text(parent_title)
    path = norm_text(unquote(urlsplit(url).path))
    if any(_EXAM.search(s) for s in (t, p, path)):
        return EXAM
    if any(d in path for d in _PAPER_DIRS):
        return JOURNAL
    if not _ABOUT_JOURNAL.search(t) and (_ISSUE.search(t) or (_JOURNAL_PAGE.search(p) and _WHOLE_ISSUE.search(t))):
        return JOURNAL
    if kind_class == "attachment" and _is_minutes(t, p):
        return MINUTES
    return ""


def _is_minutes(title: str, parent_title: str) -> bool:
    """title, parent_title: norm_text. A title that says 會議紀錄 (bracketed notes aside) is minutes unless it names a
    form anywhere (會議紀錄範本, 空白會議紀錄, 會議紀錄表) or a rule after it (會議紀錄撰寫要點); a rule before it is
    what the meeting was about (法規委員會會議紀錄, 研議「請假規則」修正案會議紀錄), and so is one in a note
    (會議紀錄(修正學生請假辦法)) 【Claude, after the review of 11-08 10:16's change】. A title that does not say it, or
    says it only in a note, is minutes when the title or its page's title says it and the title names no rule and no
    form (宿舍委員會設置辦法 on the page 會議紀錄 is a rule), the page's title naming neither (a page 「相關法規及會議紀錄」
    holds both, so there only a file's own title can say it is minutes)."""
    bare = _NOTE.sub(" ", title)
    said = list(_MINUTES.finditer(bare))
    if said:
        return not _FORM.search(title) and not _RULE.search(bare[said[-1].end():])
    named = _MINUTES.search(title) or (_MINUTES.search(parent_title)
                                       and not (_RULE.search(parent_title) or _FORM.search(parent_title)
                                                or _LAWS.search(parent_title)))
    return bool(named) and not (_RULE.search(title) or _FORM.search(title))


def _typed(d: Document) -> str:
    """A document's doc_type: by its title, its parent's title and its address (doc_type). A document plan 2026-11-14
    T4 gave its own title is typed by that title, the document's own word for itself (a file on a page of minutes
    whose own title is a rule is a rule; one whose own title says 會議紀錄 is minutes); and when that names no type, by
    what its old title alone says plainly (Document.old_title, without the parent's title or the address, which the
    first reading had already: 20 pages each titled 歷屆試題 stay exams) 【Claude】."""
    return (doc_type(d.title, d.parent_title, d.url, d.kind_class)
            or (doc_type(d.old_title, "", "", d.kind_class) if d.old_title else ""))


def _rows(docs: list[Document], events: dict[str, Event], server: dict[str, date],
          row_dates: bool = True) -> list[_Row]:
    by_id = {d.doc_id: d for d in docs}
    out = []
    for d in docs:
        event = events.get(d.doc_id)
        facts = _facts(d, by_id.get(d.parent_id or ""), event, server, row_dates)
        term = _term(d, event)
        more: tuple[Term, ...] = ()
        if event is not None and event.days:  # plan 2026-11-14 T2: a recurring event's instances in other terms
            more = tuple(t for t in dict.fromkeys(event_term(d.title, start) for start, _ in event.days) if t != term)
        out.append(_Row(d, facts, identity_date(facts), term.ay if term else None, term.sem if term else None,
                        _cohorts(d), _typed(d), terms=more))
    return out


def _date_spikes(rows: list[_Row], on: bool = True, today: date | None = None) -> dict:
    """spec 2026-11-16 §二 (the build's date_spikes, --no-date-spikes): a day on which more than SPIKE_MIN files of one
    site have the same server time, and which is recent enough to rank them above undated files (within
    SPIKE_RECENT_DAYS of today; every such day without today), dates none of them. Counted over the attachments whose
    identity date is the server's time, by site and day, in distinct addresses (the address before #: an archive's
    members carry their archive's time and are one upload); every such document of a day past the count loses its
    server date and is undated (its other facts stay; identity_date again). Before the copies are merged, so a
    merged document never keeps a copy's spike day. Returns BuildReport.date_spikes: on, min, recent_days, days
    (site and day pairs), documents, by_site, and old_days and old_documents, the shared days too old to matter and
    their files, which keep their date. Why: on the official library of 11-15, 4,817 of the 12,552 files dated by the server's time stood on 96
    such days (lis.nknu.edu.tw: 326 on 2026-10-22, the day its files were moved); of those whose title names a year,
    67% carried a day two or more academic years after it, against 18% elsewhere; 4,364 of them name no year and never
    expire, so that day was all that made them recent."""
    report = {"on": on, "min": SPIKE_MIN, "recent_days": SPIKE_RECENT_DAYS, "days": 0, "documents": 0, "by_site": {},
              "old_days": 0, "old_documents": 0}
    if not on:
        return report
    days: dict[tuple[str, date], dict[str, list[_Row]]] = {}
    for r in rows:
        if r.doc.kind_class == "attachment" and r.ident is not None and r.ident.kind == "server":
            days.setdefault((r.doc.site, r.ident.date), {}).setdefault(r.doc.url.split("#", 1)[0], []).append(r)
    by_site: Counter[str] = Counter()
    for (site, day), files in days.items():
        if len(files) <= SPIKE_MIN:
            continue
        if today is not None and (today - day).days >= SPIKE_RECENT_DAYS:
            report["old_days"] += 1
            report["old_documents"] += sum(len(group) for group in files.values())
            continue
        report["days"] += 1
        for r in (r for group in files.values() for r in group):
            r.facts = [f for f in r.facts if f.kind != "server"]
            r.ident = identity_date(r.facts)
            by_site[site] += 1
    report["documents"], report["by_site"] = sum(by_site.values()), dict(sorted(by_site.items()))
    return report


def _retitle(rows: list[_Row], on: bool, labels: bool = False) -> dict:
    """BuildReport.titles (plan 2026-11-14 T4; direction 2026-11-13 第 4 件): documents.own_titles on the documents
    kept (rows: after _merge_copies, so the 20 that make a title no title are 20 documents of the database, and a
    copy's title, already in the kept one's link texts, counts no more), then, for each document it gave a new title,
    what a title gives is read again: its term (_term: period_ay, period_sem, which search matches a query's term
    with and _validity reads), its cohort (_cohorts) and its doc type and so its tier (_typed). Its dates, its parent
    and what hangs on it stay: an attachment keeps the parent_title it has, as it keeps a merged page's (the index
    reads it) 【Claude: the plan names the document's title only】. With on False no title changes and the report
    still counts whose title says nothing. 'changed' in the report: {'period': the documents whose new title set
    their term, 'typed': {doc type: documents their new title gave it}, 'untyped': {doc type: documents whose new
    title took it away (a file on a page of minutes whose own title is a rule)}}, the side of the new titles to look
    at after a build: both move documents between the primary and the background ones. labels (11-15, the build's
    label_titles): own_titles' TITLE_LABEL, a file whose page-given title is a label many files carry titled by its
    file name again; what its title gives is read again as well."""
    report = own_titles([r.doc for r in rows], sites.table().school, on=on, labels=labels)
    period, typed, untyped = 0, Counter(), Counter()
    for r in rows:
        if not (r.doc.title_from_text or r.doc.title_was_label):
            continue
        term = _term(r.doc, None)
        ay, sem = (term.ay, term.sem) if term else (None, None)
        dtype = _typed(r.doc)
        period += (ay, sem) != (r.ay, r.sem)
        if dtype != r.dtype:
            (typed if dtype else untyped)[dtype or r.dtype] += 1
        r.ay, r.sem, r.cohorts, r.dtype = ay, sem, _cohorts(r.doc), dtype
    return {**report, "changed": {"period": period, "typed": dict(typed), "untyped": dict(untyped)}}


def _event_lines(rows: list[_Row], events: Mapping[str, Event], on: bool) -> dict:
    """BuildReport.event_dates (plan 2026-11-14 T3; direction 2026-11-13 第 3 件): each calendar event's date line
    (documents.event_line) into its row, which _write stores in docs.date_line and embed puts into the passages the
    reranker reads of the event, never into the text it embeds (corrected 11-14: nknu.content.embed): its term (the
    row's: its title's, else its first day's; a recurring event's first and last, _Row.terms), its first and last day
    and, for a merged recurring event, how many times it occurs (Event.days). on False (the build's event_dates off)
    writes none: every passage the reranker reads is the one of before. The vectors are the same either way."""
    n = 0
    for r in rows if on else ():
        event = events.get(r.doc.doc_id)
        if r.doc.kind_class != "event" or event is None:
            continue
        terms = [*([Term(r.ay, r.sem)] if r.ay is not None else []), *r.terms]
        r.date_line = event_line(terms, event.start, event.end_inclusive, len(event.days))
        n += 1
    return {"on": on, "events": n}


def _fixed_words(title: str) -> bool:
    """Whether a title holds a fixed word of rules and forms (_FIXED_WORDS; plan 2026-11-14 T6)."""
    return bool(_FIXED_WORDS.search(norm_text(title)))


def _year_notes_a_rule(title: str) -> bool:
    """A title whose fixed word of rules and forms stands before the first term it names: the year is a note on a
    rule, 「學生請假規則（113學年度第1學期第2次校務會議修正通過）」, 「…要點(111學年度起適用)」, not the year the document
    is of, as in 「114學年度第二學期 網路選課注意事項」 (main session 11-14, after the review 【Claude】: on the 111 sites
    the year comes first in 155 of the titles that have both and the fixed word in 9)."""
    at = first_term_at(title)
    word = _FIXED_WORDS.search(norm_text(title))
    return word is not None and at is not None and word.start() < at


def _single_cohort(r: _Row) -> int | None:
    """The latest entry cohort of a document that states cohorts and none of them onwards (「X學年度入學適用」, never
    「X學年度起入學」), else None (spec 2026-11-16 §二)."""
    if not r.cohorts or any(onwards for _, onwards, _ in r.cohorts):
        return None
    return max(ay for ay, _, _ in r.cohorts)


def _expires_alone(r: _Row, parent_expires: bool, cohort_expiry: bool = True) -> tuple[bool, str]:
    """(whether a document goes out of date, why), its merged copies aside (plan 2026-11-14 T6; direction 2026-11-13
    第 6 件: 「会过期的文件，按结构认，不按网站写规则：公告正文；有发布日期的帖子和新闻；标了学期或学年的文件；行事历事件；旧公告
    的附件」 and 「不会过期的：常设网页；没日期的文件；规章和表单」). The first that holds, the reason one of EXPIRES_WHY
    or STAYS_WHY:
    - a calendar event: it expires ('event'), always 【Claude: no clue below is asked of an event; 「期末考試，若教師另訂
      其他日期者，從其規定」 holds a fixed word and is over when its day is】;
    - its title names a term or year (period_ay): it expires ('title_year'), whatever cohort it holds and whether it
      has a date or not (main session 11-14, after the review 【Claude】: as first written an undated document never
      expired, so 758 undated files of the 111 sites that name their year, a 「108學年度行事曆」, kept their full
      weight for ever although docs.valid_ay knew the year); unless a fixed word stands before that year in the
      title (_year_notes_a_rule): then it does not ('fixed_words'). A fixed word after the year changes nothing
      【Claude, against the main session's brief of 11-14, which had the fixed words and the cohort win over every
      reason to expire: of 教务处's 498 notices 33 name a year beside a fixed word, 「114學年度第二學期 本校網路選課相關
      注意事項」 (the trap of question Q07, which the next term's notice replaces), 「110學年度大學部新生報到須知」, and
      the notices of 停修 hold 規定 in a bracket; with the final stage's date bonus gone (hybrid, valid_decay) they
      would no longer rank by their term at all】;
    - it states one entry cohort alone, none onwards (_single_cohort; spec 2026-11-16 §二, cohort_expiry: the build's
      --no-cohort-expiry skips this line): it expires ('cohort_single'), dated or not and whatever fixed word its title
      holds 【Claude: a rule 「105學年度入學適用」 is that cohort's alone】, with the year that cohort ends its
      COHORT_YEARS years (_own_year). The design: 「那一届读满四年以后算会过期…写『起』的照旧不过期」. Why: the table
      of 98 entrants ranked above the one of 111 and later (question Q17), its file being the newer upload;
    - no identity date: it does not ('undated');
    - its title holds a fixed word of rules and forms (_FIXED_WORDS): it does not ('fixed_words');
    - it states an entry cohort (「X學年度起入學適用」: its applies_to rows, _cohorts): it does not ('cohort'), valid
      for that cohort as long as it studies 【Claude: the plan's 「写了『入學適用』届别的」】;
    - a notice: it expires ('notice');
    - a page dated by its platform's date field or by the row of a list linking it (a post, a piece of news, an
      activity page: dates.FIELD_SOURCE, LIST_SOURCE): it expires ('dated_page'); a page the export dated by its own
      words (「更新日期：…」, PAGE_SOURCE) is a standing page that says when it was changed;
    - an attachment whose parent expires (parent_expires: the parent's docs.expires, its copies counted in): it
      expires ('parent');
    - anything else does not ('standing'): a standing page, a file on one or on none, a file on a rule's notice."""
    d = r.doc
    if d.kind_class == "event":
        return True, "event"
    if r.ay is not None:
        return (False, "fixed_words") if _year_notes_a_rule(d.title) else (True, "title_year")
    if cohort_expiry and _single_cohort(r) is not None:
        return True, "cohort_single"
    if r.ident is None:
        return False, "undated"
    if _fixed_words(d.title):
        return False, "fixed_words"
    if r.cohorts:
        return False, "cohort"
    if d.kind_class == "notice":
        return True, "notice"
    if d.kind_class == "page" and r.ident.kind == "published" and r.ident.source in (FIELD_SOURCE, LIST_SOURCE):
        return True, "dated_page"
    if d.kind_class == "attachment" and parent_expires:
        return True, "parent"
    return False, "standing"


def _own_year(r: _Row, cohort_expiry: bool = True) -> int | None:
    """The ROC academic year a document belongs to by itself (plan 2026-11-14 T6: docs.valid_ay): a calendar event's
    last day's (a merged recurring event's last instance's: its event_end fact); else the year its title names
    (period_ay), else, for a document of one entry cohort alone, the last year that cohort studies (spec 2026-11-16
    §二; not with cohort_expiry off), else its identity date's (the year turns on 1 August: terms.current_term), else
    None."""
    if r.doc.kind_class == "event":
        last = next((f.date for f in r.facts if f.kind == "event_end"), None)
        return current_term(last).ay if last is not None else None
    if r.ay is not None:
        return r.ay
    # spec 2026-11-16 §二: one entry cohort alone: the last of the COHORT_YEARS years it studies (98 -> 101)
    if cohort_expiry and (cohort := _single_cohort(r)) is not None:
        return cohort + COHORT_YEARS - 1
    return current_term(r.ident.date).ay if r.ident is not None else None


def _validity(rows: list[_Row], copies: Mapping[str, list[_Row]], cohort_expiry: bool = True) -> dict:
    """Each kept document's docs.expires and docs.valid_ay into its row, and BuildReport.validity (plan 2026-11-14 T6,
    the build's part; hybrid.validity reads the two columns). rows: the documents kept, with their final titles
    (after _retitle); copies: _merge_copies' (kept id -> the documents merged into it).
    - expires: a document and its merged copies are one document at several places, each judged alone
      (_expires_alone, a copy by its own title, date and parent), and the most favourable counts (the direction:
      「『还有效』取各副本里最有利的：有一份在常设页上，就算不会过期」): when one of them does not expire, the document does
      not ('standing_copy' when that one is a copy). Pages and notices first, then the attachments, whose parents are
      among those. One kind of place says nothing either way 【Claude】: an attachment on no kept document (linked only
      from a page the build leaves out, a list or an album) that stays only for want of a parent or of a date
      ('standing', 'undated') is no copy 「在常设页上」. It keeps no copy on a notice from expiring, and kept itself
      (the earliest) it takes the fate of a copy that expires, with that copy's reason. 教务处's album pages
      (album_images, 230 of them, titled 「2025/5/5｜公告…」 and left out as lists) hold a twin of many a notice's
      file at another address: on 11-14 (the Mac's partial corpus) 9 files, name lists of 轉系 and 輔系 among them,
      would not have expired for such a twin alone. Alone, with no copy that expires, such a file stays as
      _expires_alone says.
    - valid_ay: the latest of its own year and its copies' (_own_year) 【Claude: the kept document is the earliest
      (plan 2026-11-14 T2), so a text posted again in a later year would else count as old as its first posting; the
      plan's rule, the title's year else the date's, names no copies】.
    The report (the plan's 「先用程序数一数各线索认到多少。不做 200 份的人工抽查」): 'expires', the documents that go out
    of date per kind_class with any, and their total; 'why', per kind_class the documents by reason; 'attachments',
    of the attachments on a document that expires (themselves or by a merged copy): how many (parent_expires), how
    many expire and how many are kept from it, the undated among those, and per clue the kept ones it holds for, each
    clue counted where it would keep the document alone (its title holds a fixed word and names no year; it states a
    cohort and its title names no year; a copy of it does not expire), so one document can count under two;
    'valid_ay', where the documents' years came from (title, date, event, copy: a copy's later year, none);
    'year_named', the documents that expire by the year their title names though it holds a fixed word (fixed_words)
    or they state a cohort (cohort): what _expires_alone decides against the brief, to be weighed."""
    by_id = {r.doc.doc_id: r for r in rows}

    def parent_expires(r: _Row) -> bool:
        parent = by_id.get(r.doc.parent_id or "")
        return parent is not None and parent.expires

    def firm(r: _Row, expires: bool, reason: str) -> bool:
        """Whether r, judged alone, shows that the document does not expire: not a file on no kept document that
        stays only for want of a parent or a date."""
        placed = r.doc.kind_class != "attachment" or (r.doc.parent_id or "") in by_id
        return not expires and (placed or reason not in ("standing", "undated"))

    why: dict[str, str] = {}
    standing_copy: set[str] = set()  # the documents a copy of which shows that they do not expire
    hung: list[_Row] = []  # the attachments on a document that expires, themselves or by a copy
    sources: Counter[str] = Counter()
    for r in sorted(rows, key=lambda r: r.doc.kind_class == "attachment"):  # stable: the parents first
        members = [r, *copies.get(r.doc.doc_id, ())]
        verdicts = [_expires_alone(m, parent_expires(m), cohort_expiry) for m in members]
        shown = [firm(m, *v) for m, v in zip(members, verdicts, strict=True)]
        expires, reason = verdicts[0]
        if any(shown):
            if not shown[0]:
                expires, reason = False, "standing_copy"
        elif not expires:  # it stays for want of a parent or a date: a copy that expires decides
            expires, reason = next((v for v in verdicts[1:] if v[0]), (expires, reason))
        if any(shown[1:]):
            standing_copy.add(r.doc.doc_id)
        if r.doc.kind_class == "attachment" and any(map(parent_expires, members)):
            hung.append(r)
        r.expires, why[r.doc.doc_id] = expires, reason
        own = _own_year(r, cohort_expiry)
        r.valid_ay = max((y for y in (_own_year(m, cohort_expiry) for m in members) if y is not None), default=None)
        single = cohort_expiry and r.ay is None and _single_cohort(r) is not None
        sources["event" if r.doc.kind_class == "event" else "copy" if r.valid_ay != own
                else "title" if r.ay is not None else "cohort" if single
                else "date" if r.ident is not None else "none"] += 1
    reasons: dict[str, Counter[str]] = {}
    for r in rows:
        reasons.setdefault(r.doc.kind_class, Counter())[why[r.doc.doc_id]] += 1
    expiring = Counter(r.doc.kind_class for r in rows if r.expires)
    kept = [r for r in hung if not r.expires]
    dated = [r for r in rows if r.doc.kind_class != "event" and r.ident is not None and r.ay is not None]
    return {"expires": {**expiring, "total": sum(expiring.values())},
            "why": {kind: dict(c) for kind, c in reasons.items()},
            "attachments": {"parent_expires": len(hung), "expire": len(hung) - len(kept), "kept": len(kept),
                            "undated": sum(r.ident is None for r in kept),
                            "clues": {"fixed_words": sum(r.ay is None and _fixed_words(r.doc.title) for r in kept),
                                      "standing_copy": sum(r.doc.doc_id in standing_copy for r in kept),
                                      "cohort": sum(r.ay is None and bool(r.cohorts) for r in kept)}},
            "valid_ay": dict(sources),
            "year_named": {"fixed_words": sum(_fixed_words(r.doc.title) for r in dated),
                           "cohort": sum(bool(r.cohorts) for r in dated)}}


def _file_digest(export_dir: Path, records: list[Record]) -> Callable[[str], str | None]:
    """doc_id -> sha256 of the export file the corpus record came from (read-only), None when it is missing: in the
    record's own export (Record.export, plan 2026-11-08 T4), else in export_dir."""
    files = {"d" + r.id: (Path(r.export or export_dir).resolve(), r.file) for r in records if r.file}

    def digest(doc_id: str) -> str | None:
        root, file = files.get(doc_id, (Path(export_dir).resolve(), ""))
        path = (root / file).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return digest


def _title_groups(rows: list[_Row], digest: Callable[[str], str | None]) -> list[list[_Row]]:
    """The rule of before plan 2026-11-14 T2 (COPY_TITLE; until then _copy_groups).
    Pages or attachments that are one document at several addresses: same kind and title (an attachment's file
    name), and the same text; when there is no text, the same file bytes (looked at only for documents that share a
    kind and a title). Titles that differ never merge, so every title stays findable. A page documents._class made a
    notice (sub-step 4, spec 2026-11-08 §四: GEO shows one notice at every index= of its id, 145 of them at two
    addresses or more on 11-08) is still such a page here; it merges only with pages made notices too, its class
    being in the key, and a notice of a detail path (one address per notice) never merges (review 11-09)."""
    by_text: dict[tuple, list[_Row]] = {}
    blank: dict[tuple, list[_Row]] = {}
    for r in rows:
        d = r.doc
        if d.kind_class not in COPY_KINDS and not (d.kind_class == "notice" and kind_class(d.kind_raw) == "page"):
            continue
        name = d.file_title or d.title
        if d.text.strip():
            by_text.setdefault((d.kind_class, name, hashlib.sha256(d.text.encode()).hexdigest()), []).append(r)
        else:
            blank.setdefault((d.kind_class, name), []).append(r)
    groups = [g for g in by_text.values() if len(g) > 1]
    for same_title in blank.values():
        by_file: dict[str, list[_Row]] = {}
        for r in same_title if len(same_title) > 1 else ():
            if (h := digest(r.doc.doc_id)) is not None:
                by_file.setdefault(h, []).append(r)
        groups += [g for g in by_file.values() if len(g) > 1]
    return groups


def _bare(text: str) -> str:
    """text without any whitespace (plan 2026-11-14 T2; T1's scorer, offline_eval._bare, compares copies the same
    way)."""
    return _SPACE.sub("", text)


def _by_text(d: Document) -> str | None:
    """What the text rule compares a document by (plan 2026-11-14 T2): its text without whitespace when that is at
    least COPY_MIN_CHARS long; None for a document the rule leaves alone: a shorter one, a calendar event (no part
    of this rule: its text is its title, and its days are what tell two apart; documents.one_event_per_series merges
    the instances of one recurring event) and one whose words are not the document (has_text false: a scan's few
    words, which two scans can share; such files merge by the rule of before only) 【Claude】."""
    if d.kind_class == "event" or not d.has_text or len(d.text) < COPY_MIN_CHARS:
        return None
    bare = _bare(d.text)
    return bare if len(bare) >= COPY_MIN_CHARS else None


def _text_groups(rows: list[_Row]) -> list[list[_Row]]:
    """The text rule's groups (plan 2026-11-14 T2; direction 2026-11-13 第 2 件 1): documents of one kind_class with
    the same _by_text, across sites and inside one, a notice of a detail path too, whatever their titles (the kept
    one's link texts keep every title findable: _take_over)."""
    by_text: dict[tuple[str, str], list[_Row]] = {}
    for r in rows:
        if (bare := _by_text(r.doc)) is not None:
            by_text.setdefault((r.doc.kind_class, hashlib.sha256(bare.encode()).hexdigest()), []).append(r)
    return [g for g in by_text.values() if len(g) > 1]


def _copy_groups(rows: list[_Row], digest: Callable[[str], str | None], rule: str = COPY_TEXT) -> list[list[_Row]]:
    """The documents that are one, group by group. COPY_TITLE: the groups of before, as they were (_title_groups).
    COPY_TEXT (plan 2026-11-14 T2): those and the text rule's (_text_groups), two groups sharing a document being one
    (a page with one title and text as another is with it in both; the text rule only widens), each group in the
    rows' order and the groups by their first row; with no text group they are the groups of before."""
    groups = _title_groups(rows, digest)
    wider = _text_groups(rows) if rule == COPY_TEXT else []
    if not wider:
        return groups
    place = {id(r): i for i, r in enumerate(rows)}
    root = list(range(len(rows)))

    def find(i: int) -> int:
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    for group in (*wider, *groups):
        first = find(place[id(group[0])])
        for r in group[1:]:
            root[find(place[id(r)])] = first
    joined: dict[int, list[_Row]] = {}
    for i in sorted({place[id(r)] for group in (*wider, *groups) for r in group}):
        joined.setdefault(find(i), []).append(rows[i])
    return list(joined.values())


@dataclass
class _Twins:
    """_platform_twins' pairs, each of a notice at a shared platform's address and one on a unit's own site, of one
    identity date: same, (the platform's, the unit's), each one's every line found in the other; inside, (the lesser,
    the fuller, whether the fuller is the platform's), the lesser's every line found in the fuller, which says more;
    apart, (the platform's, the unit's) of one title neither of which holds the other."""
    same: list[tuple[_Row, _Row]] = field(default_factory=list)
    inside: list[tuple[_Row, _Row, bool]] = field(default_factory=list)
    apart: list[tuple[_Row, _Row]] = field(default_factory=list)


def _twin_lines(text: str, day: date | None) -> list[str]:
    """What a notice says, line by line, as _platform_twins compares it 【Claude】: each line on norm_text without
    whitespace, a list number at its start taken off (_TWIN_MARK), then letters, digits and Chinese characters only
    (alias_norm: two sites punctuate one text differently). Left out: an empty line, a line that is a file's name
    (_TWIN_FILE) and a line that is nothing but the notice's own date (_TWIN_DATE read as a day, a year under 1911
    being the Republic's; any other date stays: a deadline on a line of its own is what the notice says)."""
    out = []
    for raw in text.splitlines():
        line = "".join(norm_text(raw).split())
        if not line or _TWIN_FILE.fullmatch(line):
            continue
        if day is not None and (m := _TWIN_DATE.fullmatch(line)):
            y, mo, d = (int(g) for g in m.groups())
            if (y + 1911 if y < 1911 else y, mo, d) == (day.year, day.month, day.day):
                continue
        if bare := alias_norm(_TWIN_MARK.sub("", line)):
            out.append(bare)
    return out


def _platform_twins(rows: list[_Row]) -> _Twins:
    """The notices that stand once on a shared platform and once on a unit's own site (the owner, 11-14 23:55: 「公告平台和单位
    网站各一份」; the module docstring). Looked at: every pair of notices with words and one identity date, one at an
    address of a shared platform (sites: shared, by the address itself, whichever export brought it), the other not.
    Compared: their lines (_twin_lines) without page furniture, which is a line that many notices of either one's
    site carry (TWIN_TEMPLATE_MIN, TWIN_TEMPLATE_SHARE: a menu, 「最新消息」, 「相關檔案」, counted over all the site's
    notices) and either one's title (a page prints its title in its text, the other need not). A line is found in the
    other notice when it is part of the other's lines joined, so one site's line broken in two on the other, lines in
    another order and a line printed twice all compare equal, and nothing is a matter of degree: there is no
    threshold of likeness, a changed word or figure keeps two notices apart.
    - each one's lines all found in the other: the same notice (same);
    - one's all found in the other, which has lines of its own: the other is the fuller copy (inside);
    - else nothing; when they share their title (TWIN_TITLE_MIN) the pair is listed as apart, to be looked at.
    The one whose lines must be found has to say at least TWIN_MIN_CHARS, or the two share their title: a notice of
    「詳如附件」 alone is inside many of its day. No likeness to a page of another kind is looked for, and two units'
    sites are no pair here (the near reposts between units, counted on 11-14, are not this step)."""
    notices: list[tuple[_Row, str, bool, str, list[str]]] = []
    carried: dict[str, Counter[str]] = {}
    total: Counter[str] = Counter()
    shared = sites.table().rows
    for r in rows:
        d = r.doc
        if d.kind_class != "notice" or not d.has_text:
            continue
        site = site_key(d.url)
        lines = _twin_lines(d.text, r.ident.date if r.ident else None)
        carried.setdefault(site, Counter()).update(set(lines))
        total[site] += 1
        if r.ident is not None:
            platform = (row := shared.get(site)) is not None and row.shared
            notices.append((r, site, platform, alias_norm(d.title), lines))
    furniture = {site: {ln for ln, n in seen.items() if n >= max(TWIN_TEMPLATE_MIN, total[site] // TWIN_TEMPLATE_SHARE)}
                 for site, seen in carried.items()}
    by_day: dict[date, tuple[list, list]] = {}
    for n in notices:
        by_day.setdefault(n[0].ident.date, ([], []))[0 if n[2] else 1].append(n)
    out = _Twins()
    for platform, own in by_day.values():
        for p, p_site, _, p_title, p_lines in platform:
            for u, u_site, _, u_title, u_lines in own:
                skip = furniture[p_site] | furniture[u_site] | {p_title, u_title}
                p_says, u_says = ([ln for ln in lines if ln not in skip] for lines in (p_lines, u_lines))
                p_all, u_all = "".join(p_says), "".join(u_says)
                p_in_u, u_in_p = all(ln in u_all for ln in p_says), all(ln in p_all for ln in u_says)
                titled = p_title == u_title and len(p_title) >= TWIN_TITLE_MIN
                if p_in_u and u_in_p:
                    if titled or min(len(p_all), len(u_all)) >= TWIN_MIN_CHARS:
                        out.same.append((p, u))
                elif p_in_u or u_in_p:
                    if titled or len(p_all if p_in_u else u_all) >= TWIN_MIN_CHARS:
                        out.inside.append((p, u, False) if p_in_u else (u, p, True))
                elif titled:
                    out.apart.append((p, u))
    return out


def _join_twins(rows: list[_Row], groups: list[list[_Row]], twins: _Twins,
                level: Callable[[_Row], int]) -> tuple[list[list[_Row]], set[str], dict]:
    """groups (_copy_groups': documents of one text) with the platform twins joined in, and who may not be the one
    kept. Returns (the groups, in the rows' order and by their first row; the doc ids of the lesser ones; the twins'
    part of the report: 'linked', the doc ids of the pairs that joined two texts, 'groups', the groups such a pair
    made or widened, 'removed', the documents they take away beyond what the groups of one text did).
    - Documents of one text and the twins that are the same notice are one class (a notice the same as two others
      makes all three one).
    - A class wholly inside a fuller one hangs under it, so the two are one group whose kept document comes from the
      fuller class and no line is lost. A class inside two that are no twins of each other (the platform's short
      notice, two units' longer ones) hangs under one of them only, the one of the nearest owner (level; then the
      rows' order), and the other stays a document: joining all three would drop what only one of the two says.
      A pair that would close a ring is left out (it cannot come from real containment)."""
    place = {id(r): i for i, r in enumerate(rows)}
    root = list(range(len(rows)))

    def find(i: int) -> int:
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    for group in groups:
        first = find(place[id(group[0])])
        for r in group[1:]:
            root[find(place[id(r)])] = first
    texts = [find(i) for i in range(len(rows))]  # the classes of one text, before any twin
    linked: set[str] = set()
    for p, u in twins.same:
        a, b = place[id(p)], place[id(u)]
        if find(a) != find(b):
            root[find(a)] = find(b)
            linked |= {p.doc.doc_id, u.doc.doc_id}
    up: dict[int, int] = {}
    for lesser, fuller, _ in sorted(twins.inside, key=lambda t: (level(t[1]), place[id(t[1])], place[id(t[0])])):
        a, b = find(place[id(lesser)]), find(place[id(fuller)])
        top = b
        while top in up and top != a:
            top = up[top]
        if a == b or a in up or top == a:
            continue
        up[a] = b
        linked |= {lesser.doc.doc_id, fuller.doc.doc_id}

    def head(c: int) -> int:
        while c in up:
            c = up[c]
        return c

    members = {place[id(r)] for group in groups for r in group}
    members |= {place[id(r)] for pair in (*twins.same, *twins.inside) for r in pair[:2]}
    joined: dict[int, list[int]] = {}
    for i in sorted(members):
        joined.setdefault(head(find(i)), []).append(i)
    out = [[rows[i] for i in group] for group in joined.values() if len(group) > 1]
    lesser_ids = {rows[i].doc.doc_id for group in joined.values() for i in group if find(i) in up}
    twinned = [group for group in joined.values() if any(rows[i].doc.doc_id in linked for i in group)]
    return out, lesser_ids, {"linked": linked, "groups": len(twinned),
                             "removed": sum(len({texts[i] for i in group}) - 1 for group in twinned)}


def _inherited(r: _Row) -> date | None:
    return next((f.date for f in r.facts if f.kind == "inherited"), None)


def _take_over(keep: Document, other: Document, names: bool = True) -> int:
    """keep stands for other from now on: other's references join keep's, other's title stays findable in keep's
    linktext, and other's list categories join keep's, each once after keep's own (blueprint item 22, review 11-09:
    the unit line, written after this, would otherwise lose a category only a copy's list row states). Plan 2026-11-14
    T2: so does other's file name when a page gave it its title (file_title), since copies of one text need not share
    a file name any more; under the rule of before both have one file name, which keep's title or linktext holds
    already, so nothing is added there 【Claude】.
    names (11-15, the build's copy_names; 【Claude】): the names other's pages and menus link it by (its linktext, but
    those that name nothing: links.GENERIC, 「pdf」, 「下載」) stay findable in keep's linktext too, after its title and
    file name. Until then only the title and the file name were carried, so a name only a copy's menu gave was lost
    with the copy: the tuition table 教务处's menu calls 「日間大學部學雜費【學費及雜費】各學系學生收費標準」 is kept
    under 國際事務處's copy, and once 教务处's copy was titled 「大學部」 (the site map's names no longer title a
    file: assemble.MENU_PAGES) the keyword index no longer found it for 學雜費: of the 33 questions Q35's answer went
    from the 5th place to the 368th (research/pipeline_2026-11-02/fixes_1115/explain_Q35.txt). Returns how many
    such names it added."""
    known = {ref for ref, _, _ in keep.refs}
    keep.refs += tuple(ref for ref in other.refs if ref[0] not in known and ref[0] != keep.doc_id)
    for name in (other.title, other.file_title):
        if name and name != keep.title and name not in keep.linktext.split(LINK_SEP):
            keep.linktext = LINK_SEP.join(p for p in (keep.linktext, name) if p)
    added = 0
    for name in (part.strip() for part in other.linktext.split(LINK_SEP)) if names else ():
        if (name and name != keep.title and not GENERIC.fullmatch(name)
                and name not in keep.linktext.split(LINK_SEP)):
            keep.linktext = LINK_SEP.join(p for p in (keep.linktext, name) if p)
            added += 1
    keep.categories += tuple(c for c in other.categories if c not in keep.categories)
    # main session 11-14, after the review: whose sites hold a copy (unit_scope: a department's document that other
    # departments keep too applies to them all)
    if other.unit and other.unit != keep.unit and other.unit not in keep.also_units:
        keep.also_units += (other.unit,)
    keep.also_units += tuple(u for u in other.also_units if u != keep.unit and u not in keep.also_units)
    return added


def _house(unit: str, units: Mapping[str, Unit]) -> str:
    """The unit a document stands under, taken at its office when it is a section of one (a section's own site and its
    office's are one house: 生活輔導組's and 學生事務處's), as written when the tree does not have it. What two copies
    are compared by to say whether one unit holds both (_merge_copies) 【Claude】; a department and its college are
    two houses: a college's site that shows its department's notice re-posts it."""
    unit_id = resolve(unit, units)
    if unit_id is None:
        return " ".join(unit.split())
    return office_of(unit_id, units) or unit_id


def _stated_units(docs: list[Document], units: Mapping[str, Unit] | None, on: bool = True) -> dict:
    """BuildReport.publishers (the owner, 11-15: 「在你的程序里读第 30 栏「公告单位」（合并时拿它定原发单位）」; the
    crawler's 2989f60 reads the cell 「公告單位：」 of the announcement platform's notice pages into the export's
    公告单位). Until now a notice on the platform stood under the platform's placeholder unit (config/site_units.yaml:
    秘書室, 「各则公告其实是各单位发的，发布单位要等爬虫 session 看公告页哪一栏写单位」) or under the unit of the export
    that brought it; with the statement the page itself says who published it 【Claude: how】:
    - a document whose page states a unit (Document.stated_unit) takes the unit units.name_unit reads in it: a
      section of an office as that office with the section as its 組別, 「教務處和平教務組」 as 教务处's own notices
      stand; any other unit as itself (「科技學院電子工程學系」: 電子工程學系), with what the tree lacks of the name as
      its 組別 (「國際事務處國際合作組」: 國際事務處, 國際合作組); a statement that names no unit of the tree as
      written (「副校長室」), which the unit line then shows as the page writes it. unit_stated is set on each;
    - a file hanging on such a notice follows it, unit and 組別, when its own address is on no site of the site table
      or on a shared platform (the platform's file service: its unit was the placeholder's too, sites.unit_for);
    in place, before the copies are merged, so the merge, the unit line, the scope and the audience factor all read the
    stated unit. on False (the build's stated_units off, --no-stated-units) or no unit tree: nothing changes and the
    report still counts the statements."""
    stated = [d for d in docs if d.stated_unit]
    report: dict = {"on": bool(on and units is not None), "stated": len(stated), "placed": 0, "as_written": {},
                    "parts_as_written": {}, "changed": 0, "files": 0, "units": {}}
    if not report["on"] or units is None:
        return report
    as_written, parts, named = Counter(), Counter(), Counter()
    placed: dict[str, Document] = {}
    for d in stated:
        was = (d.unit, d.grp)
        unit_id, rest = name_unit(d.stated_unit, units)
        if unit_id is None:
            d.unit, d.grp = rest, ""
            as_written[rest] += 1
        else:
            office = office_of(unit_id, units)
            d.unit, d.grp = (office, units[unit_id].name) if office is not None and not rest else (unit_id, rest)
            report["placed"] += 1
            if rest:
                parts[rest] += 1
        d.unit_stated = True
        named[d.unit] += 1
        report["changed"] += (d.unit, d.grp) != was
        placed[d.doc_id] = d
    table = sites.table()
    for d in docs:
        notice = placed.get(d.parent_id or "")
        if notice is None or d.kind_class != "attachment":
            continue
        row = table.rows.get(site_key(d.url))
        if row is None or row.shared:
            d.unit, d.grp, d.unit_stated = notice.unit, notice.grp, True
            report["files"] += 1
    report.update(as_written=dict(as_written.most_common()), parts_as_written=dict(parts.most_common()),
                  units=dict(named.most_common()))
    return report


def _owner_level(d: Document, units: Mapping[str, Unit]) -> int:
    """How likely the unit a copy stands under is the one the text comes from, the lower the likelier (_merge_copies)
    【Claude】: 0 an office's, a section's or a centre's (when the same text is on an office's site and on
    departments', the departments took it from the office: a rule, a form, an admission brochure); 1 a college's, a
    department's or a programme's (its own rule that the school's rule system or announcement platform also shows is
    its own); 2 any other: the school as a whole, an unknown unit, and a shared platform's own export, whose unit is
    a placeholder (documents.home_rank 2). The unit is the one the site table gives the copy's address
    (Document.unit: sites.unit_for); since 11-15 a document whose page states its publishing unit stands under that
    unit wherever its address is (Document.unit_stated: _stated_units), so the platform's own notice is its
    publisher's and no placeholder's."""
    if home_rank(d.url, d.site) == 2 and not d.unit_stated:
        return 2
    unit_id = resolve(d.unit, units)
    if unit_id is None or units[unit_id].parent_id is None:
        return 2
    return 1 if units[unit_id].kind in TEACHING_KINDS else 0


def _merge_copies(rows: list[_Row], digest: Callable[[str], str | None], excluded: dict[str, int],
                  rule: str = COPY_TEXT, alumni: bool = True, units: Mapping[str, Unit] | None = None,
                  twins: bool = True, seen: dict | None = None,
                  names: bool = True) -> tuple[list[_Row], dict[str, list[_Row]]]:
    """Keep one document of each copy group (review 11-01: copies took half the places in the top ten; _copy_groups
    by rule). twins (the owner, 11-14 23:55; build_sites' platform_twins): a notice on a shared platform and its twin on a
    unit's own site are one group too (_platform_twins, _join_twins), kept as a group of the text rule is, but never
    one of the lesser ones: when one copy says more, a fuller one stays. One kind of pair is left alone 【Claude】: the
    platform's notice wholly inside a longer one on a teaching unit's site (_owner_level 1; units given). On the 111
    sites of 11-14 those were 21 pairs, and the longer page was the department's own version as often as a copy: its
    own procedure after the school's sentences, or its menu read as text (the pages of 電機系, whose content area
    starts at the menu); kept, it would stand for the school's notice under the department's name, with the audience
    factor of a department's own document. They stay two documents, counted as department_longer; since 11-15 not
    when the platform's notice states that department as its publisher (Document.unit_stated: the longer page is then
    its own fuller version, and the pair is merged as any other with one copy fuller). seen, when given,
    takes the twins' counts (BuildReport.dedupe['twins']) and 'linked', the doc ids of the pairs that joined two
    texts. Which one is kept:
    - a group the text rule made or widened (plan 2026-11-14 T2: one of its documents has a _by_text): first by
      whose copy it is (units given: _owner_level, which since 11-15 reads the unit a platform notice's page states
      instead of the platform's placeholder; then, the owner 11-15 「合并时拿它定原发单位」, among the copies of that
      nearest kind of owner only those of the unit a copy's page states as the publisher, when one does:
      Document.unit_stated, _house; that unit's own site, if it holds a copy, comes before the platform's notice by
      the home rank below, and no other unit's copy of that kind stands for the group; main session 11-14, after the review 【Claude】: the direction's
      「显示最早的那份（原发单位）」 takes the earliest for the one who first published, and dates do not say that: an
      attachment's date is its notice's or the server's. On the 111 sites 251 texts that an office keeps on its own
      site were kept under a department whose copy carried an earlier date, among them the school's 《抵免學分要點》
      and the 輔系 form of two test questions; the unit line, the scope and so the audience factor followed the
      department); then the earliest
      by its identity date, 「显示最早的那份（原发单位）和它自己的日期」, an undated one after every dated one; among
      those of that day the nearest home (documents.home_rank: a unit's own site before the export that brought a
      page, a shared platform's own export last; the plan's 「同一天照现有的 _rank」), then the choice of before. A
      primary document comes before a background one whatever their dates (tier_of, with alumni as the build's
      switch) 【Claude】: a file a notice links is often in an older archive too, and kept under the archive member it
      would leave the primary documents for the background ones, which the rule of before never did to another title;
    - any other group (the rule of before alone: every group under COPY_TITLE): the one on the latest dated notice
      (the school still posts the file then), else the latest identity date, else the first in corpus order.
    The others are counted in excluded as <kind>(副本) and returned, newest first, under the kept id; the kept one
    takes their references, titles and list categories (_take_over); a document hanging on a dropped page hangs on
    the kept one (the same text; its parent_title stays the dropped page's, which the index reads), and a reference to
    a dropped page points at the kept one."""
    order = {r.doc.doc_id: i for i, r in enumerate(rows)}

    def before(r: _Row) -> tuple:
        """The choice of before, the greatest first."""
        return (_inherited(r) is not None, _inherited(r) or date.min, r.ident.date if r.ident else date.min,
                -order[r.doc.doc_id])

    def earliest(group: list[_Row]) -> _Row:
        front = [r for r in group if tier_of(r.doc.doc_id, r.dtype, r.doc.kind_raw, alumni) == PRIMARY] or group
        if units is not None:
            nearest = min(_owner_level(r.doc, units) for r in front)
            front = [r for r in front if _owner_level(r.doc, units) == nearest]
            # the owner, 11-15 (「合并时拿它定原发单位」): among the copies of that nearest kind of owner, one whose
            # page states who published it settles whose text it is: only that unit's copies stand for the group,
            # the platform's notice among them. After the kind of owner, not before it: the statement says who put
            # that notice on the platform, and a department that posts an office's text there is no more its source
            # than one that copies it to its own site (the office's own site still comes first, as since 11-14)
            stated = {_house(r.doc.unit, units) for r in front if r.doc.unit_stated}
            if stated:
                front = [r for r in front if _house(r.doc.unit, units) in stated]
        first = min((r.ident.date for r in front if r.ident), default=None)
        tied = [r for r in front if (r.ident.date if r.ident else None) == first]
        home = min(home_rank(r.doc.url, r.doc.site) for r in tied)
        return max((r for r in tied if home_rank(r.doc.url, r.doc.site) == home), key=before)

    kept_of: dict[str, str] = {}
    copies: dict[str, list[_Row]] = {}
    carried: dict[str, int] = {}  # kept doc id -> the names of its copies' links it took over (_take_over)
    groups = _copy_groups(rows, digest, rule)
    lesser: set[str] = set()
    linked: set[str] = set()
    if twins:
        found = _platform_twins(rows)

        def level(r: _Row) -> int:
            return _owner_level(r.doc, units) if units is not None else 0

        def own(p: _Row, u: _Row) -> bool:
            """The platform's notice p states the unit of u as its publisher: u's longer page is that unit's own
            fuller version of its own notice (11-15), no other unit's version of the school's."""
            return (units is not None and p.doc.unit_stated
                    and _house(p.doc.unit, units) == _house(u.doc.unit, units))

        # a teaching unit's longer version of a notice that is not stated to be its own
        longer = [t for t in found.inside if not t[2] and level(t[1]) == 1 and not own(t[0], t[1])]
        found.inside = [t for t in found.inside if t[2] or level(t[1]) != 1 or own(t[0], t[1])]
        groups, lesser, joined = _join_twins(rows, groups, found, level)
        linked = joined["linked"]
        if seen is not None:
            def shown(pairs: Sequence[tuple[_Row, _Row]]) -> list[dict]:
                return [{"date": p.ident.date.isoformat(), "title": p.doc.title[:TITLE_SHOWN],
                         "platform": p.doc.doc_id, "unit": u.doc.doc_id} for p, u in pairs[:TWIN_APART_SHOWN]]

            seen.update(joined, same=len(found.same),
                        fuller={"platform": sum(on_platform for _, _, on_platform in found.inside),
                                "unit": sum(not on_platform for _, _, on_platform in found.inside)},
                        department_longer=len(longer), department_longer_shown=shown([t[:2] for t in longer]),
                        apart=len(found.apart), apart_shown=shown(found.apart))
    for group in groups:
        pool = [r for r in group if r.doc.doc_id not in lesser]
        by_text = rule == COPY_TEXT and any(_by_text(r.doc) is not None for r in group)
        keep = earliest(pool) if by_text or any(r.doc.doc_id in linked for r in group) else max(pool, key=before)
        others = sorted((r for r in group if r is not keep),
                        key=lambda r: (r.ident is None, -(r.ident.date.toordinal() if r.ident else 0),
                                       order[r.doc.doc_id]))
        copies[keep.doc.doc_id] = others
        for r in others:
            kept_of[r.doc.doc_id] = keep.doc.doc_id
            excluded[r.doc.kind_raw + COPY] = excluded.get(r.doc.kind_raw + COPY, 0) + 1
            if added := _take_over(keep.doc, r.doc, names):
                carried[keep.doc.doc_id] = carried.get(keep.doc.doc_id, 0) + added
    kept = [r for r in rows if r.doc.doc_id not in kept_of]
    for r in [*kept, *(c for cs in copies.values() for c in cs)]:
        r.doc.parent_id = kept_of.get(r.doc.parent_id or "", r.doc.parent_id)
    present = {r.doc.doc_id for r in kept}
    for r in kept:  # references to a dropped page go to the page kept for it; the best zone wins, as in documents
        refs: dict[str, tuple[str, str]] = {}
        for ref, zone, context in r.doc.refs:
            ref = kept_of.get(ref, ref)
            if ref in present and ref != r.doc.doc_id and ref not in refs:
                refs[ref] = (zone, context)
        r.doc.refs = tuple((ref, zone, context) for ref, (zone, context) in refs.items())
    if seen is not None:
        seen["names_carried"] = {"on": names, "documents": len(carried), "names": sum(carried.values())}
    return kept, copies


def _copies_report(kept: list[_Row], copies: dict[str, list[_Row]], rule: str,
                   site_order: Sequence[str] = (), linked: frozenset[str] | set[str] = frozenset()) -> dict:
    """BuildReport.dedupe['copies'] (plan 2026-11-14 T2: 「合了几组、少了几份、每个网站各留了几份、各被合掉几份」, and the
    direction's 「动手前先数 849 组的正本落在谁家」) of _merge_copies' result: the rule, COPY_MIN_CHARS, the groups
    merged and the documents they removed; the groups whose documents are of more than one site (cross_site); by_rule,
    the groups the text rule made or widened and those of the rule of before alone; by_kind, the documents removed
    per kind_class; by_site, per site (docs.site, the export a document came from; site_order first, then by name)
    kept, the groups whose kept document is the site's, and merged, the site's documents merged away; largest, the
    LARGEST_GROUPS groups with the most copies (the kept id, its site, kind_class and title cut to TITLE_SHOWN, the
    copies, the sites and the different titles in the group), to look at after a build: a group of many titles is a
    text many pages share, which the kept one's link texts then all name 【Claude】. linked (the owner, 11-14 23:55:
    _merge_copies' seen['linked']): a group holding one of these documents is one a platform twin made or widened,
    counted under by_rule's COPY_TWIN whatever else is in it (dedupe['twins'] says how many documents the twins
    themselves removed)."""
    by_id = {r.doc.doc_id: r for r in kept}
    by_rule = {name: {"groups": 0, "removed": 0} for name in (*COPY_RULES, COPY_TWIN)}
    by_kind: Counter[str] = Counter()
    sites_: dict[str, dict[str, int]] = {}
    cross = 0
    for kept_id, others in copies.items():
        group = [by_id[kept_id], *others]
        made = (COPY_TWIN if any(r.doc.doc_id in linked for r in group)
                else COPY_TEXT if rule == COPY_TEXT and any(_by_text(r.doc) is not None for r in group) else COPY_TITLE)
        by_rule[made]["groups"] += 1
        by_rule[made]["removed"] += len(others)
        by_kind[group[0].doc.kind_class] += len(others)
        cross += len({r.doc.site for r in group}) > 1
        for r, what in ((group[0], "kept"), *((o, "merged") for o in others)):
            sites_.setdefault(r.doc.site, {"kept": 0, "merged": 0})[what] += 1
    first = {site: i for i, site in enumerate(site_order)}
    largest = sorted(copies.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:LARGEST_GROUPS]
    return {"rule": rule, "min_chars": COPY_MIN_CHARS, "groups": len(copies),
            "removed": sum(len(others) for others in copies.values()), "cross_site": cross, "by_rule": by_rule,
            "by_kind": dict(by_kind),
            "by_site": {site: sites_[site] for site in sorted(sites_, key=lambda x: (first.get(x, len(first)), x))},
            "largest": [{"kept": kept_id, "site": by_id[kept_id].doc.site, "kind": by_id[kept_id].doc.kind_class,
                         "title": by_id[kept_id].doc.title[:TITLE_SHOWN], "copies": len(others),
                         "sites": len({r.doc.site for r in (by_id[kept_id], *others)}),
                         "titles": len({r.doc.title for r in (by_id[kept_id], *others)})}
                        for kept_id, others in largest]}


def _rank_dates(rows: list[_Row], copies: Mapping[str, list[_Row]]) -> dict:
    """Each kept document's docs.rank_date into its row, and BuildReport.dedupe['rank_dates'] (plan 2026-11-14 T2,
    completed the same day. The direction, 2026-11-13 第 2 件 1: 「显示最早的那份（原发单位）和它自己的日期」; the main
    session's brief of 11-14 after the first rebuild: for ranking by recency the most favourable among the copies
    counts, the latest date, so that merging never ranks a document lower than before). rows: the documents kept;
    copies: _merge_copies' (kept id -> the documents merged into it), of either copy rule.

    rank_date: the latest identity date among the document and its copies; its own for a document without copies;
    None when none of them is dated (an event has no identity date: search ranks it by its last day, as before). The
    kept document of the text rule is the earliest (_merge_copies), so until this its docs.ident_date, which search
    ranked by, was the earliest of the group: on the 教务处 control a gold document's keyword rank fell from 4 to 26
    once its copies were merged into an earlier one (main session, 11-14, the first rebuild). docs.ident_date stays
    the kept document's own, and every copy keeps its own in the copies' list (_copies_meta).

    The report, to read after a build: 'dated', the documents with a rank date; 'later', those dated themselves that
    rank by a copy's later day; 'from_copy', those without a date of their own that take a copy's (a primary file
    kept before a dated archive member: search gives it no less than an undated document gets, search._date_part);
    'undated_copy', those that rank by a date though a copy of theirs has none. The last is where 「never lower than
    before」 is not kept 【Claude: counted, not solved】: ranking v2 gives an undated document half of today's bonus,
    more than a date over about 250 days old gives, so such a copy ranked above where the merged document now does.
    Whether that is a loss depends on the copy (a rule's copy on a standing page loses its standing; a second address
    of one dated post only shows its age now), and a date column cannot say 「no date at one of its places」."""
    later = from_copy = undated_copy = 0
    for r in rows:
        members = [r, *copies.get(r.doc.doc_id, ())]
        r.rank_date = max((m.ident.date for m in members if m.ident is not None), default=None)
        if r.rank_date is None:
            continue
        later += r.ident is not None and r.rank_date > r.ident.date
        from_copy += r.ident is None
        # main session, 11-14 (the direction's v5 wording: 「没日期的副本照现在算 0.15。这样合并不会让一份文件比现在排得
        # 更后」): the flag search needs for what the docstring above calls counted, not solved
        r.rank_undated = r.ident is not None and any(m.ident is None for m in members)
        undated_copy += r.rank_undated
    return {"dated": sum(r.rank_date is not None for r in rows), "later": later, "from_copy": from_copy,
            "undated_copy": undated_copy}


def _copies_meta(rows: list[_Row], copies: dict[str, list[_Row]]) -> dict[str, str]:
    """copies:<kept id> for every kept document with copies: the same document at other addresses (newest first),
    then the format siblings folded into it or into one of those (url and format; their page is the kept one's). Each
    has its own id (plan 2026-11-08 T1: doc_alias reads it back to the kept one). A copy whose text, whitespace aside,
    is not the kept document's says wording_differs (the owner, 11-14 23:55: only a platform twin can be one, _platform_twins;
    every other copy is of one text): respond tells the reader that such a copy's lines are all in the kept one."""
    def entry(r: _Row, keep: _Row) -> dict:
        out = {"id": r.doc.doc_id, "url": r.doc.url, "date": r.ident.date.isoformat() if r.ident else None,
               "date_kind": r.ident.kind if r.ident else None, "parent_id": r.doc.parent_id,
               "parent_title": r.doc.parent_title,
               "wording_differs": r.doc.kind_class == "notice" and _bare(r.doc.text) != _bare(keep.doc.text)}
        return {k: v for k, v in out.items() if v}

    def formats(keep: Document, d: Document) -> list[dict]:
        return [{k: v for k, v in {"id": _format_id(f), "url": f.get("url"), "format": f.get("format"),
                                   "parent_id": keep.parent_id, "parent_title": keep.parent_title}.items() if v}
                for f in d.formats]

    out = {}
    for r in rows:
        others = copies.get(r.doc.doc_id, [])
        listed = ([entry(c, r) for c in others] + formats(r.doc, r.doc)
                  + [f for c in others for f in formats(r.doc, c.doc)])
        if listed:
            out[COPIES_KEY + r.doc.doc_id] = json.dumps(listed, ensure_ascii=False)
    return out


def _format_id(f: dict) -> str | None:
    """The doc_id a format sibling would have had (its record id from assemble), None when the corpus names none."""
    return "d" + str(f["id"]) if f.get("id") else None


def _aliases(rows: list[_Row], copies: dict[str, list[_Row]]) -> list[tuple[str, str, str]]:
    """doc_alias rows (alias, doc_id, why), plan 2026-11-08 T1: the copies _merge_copies left out (copy), the format
    siblings assemble folded into a kept document or into one of its copies (format) and the notices documents left
    out as (重复) of one of them (duplicate), each pointing at the document kept. An id the database holds as a
    document is never an alias, and the first reason found for an id stays."""
    present = {r.doc.doc_id for r in rows}
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        kept = r.doc.doc_id
        others = copies.get(kept, [])
        found = [(c.doc.doc_id, "copy") for c in others]
        for d in (r.doc, *(c.doc for c in others)):
            found += [(i, "format") for f in d.formats if (i := _format_id(f))]
            found += [(dup, "duplicate") for dup in d.duplicates]
        for alias, why in found:
            if alias not in present:
                out.setdefault(alias, (kept, why))
    return [(alias, kept, why) for alias, (kept, why) in out.items()]


def _tokens(text: str, extra: list[str] | None = None) -> str:
    """Bigrams and term tokens of text, then the extra tokens it does not already have."""
    toks = tokenize(text) + canonical_tokens(text)
    return " ".join(toks + [t for t in extra or [] if t not in toks])


def _chunks(d: Document) -> tuple[list, list[Chunk]]:
    """Sections and chunks of whatever text the document has, has_text or not (a mostly scanned PDF's few words still
    find it; review 11-01); a blank text gets one empty chunk, found by its title tokens."""
    sections = split_sections(d.text)
    return sections, make_chunks(d.doc_id, d.text, sections) or [Chunk(d.doc_id, 0, 0, 0)]


def _write_index(conn: sqlite3.Connection, rows: list[_Row]) -> tuple[int, int]:
    """sections, chunks (with the offsets of the table header a chunk starts under) and chunk_fts (rowid = chunk_id);
    title_tokens: title, parent title, link text and the section heading, each on a line of its own; body_tokens: the
    chunk, after its table header when it has one (T7a: a row far down a table still finds the column names). Both
    carry the term tokens (term115s1 ...); an event's title column also carries its own term's, which its title seldom
    writes (fix round 2, 11-01: 「下學期 選課」 finds next term's 選課), and a recurring event's every term an instance of
    it falls in (_Row.terms, plan 2026-11-14 T2: each instance carried its own before the merge, so the terms after
    its first would find it no more). unit_tokens: the unit words (plan 2026-11-08 T4:
    the unit's name and short names, the 組別, the units it applies to, the list rows' categories since blueprint item
    22; unit_words), a word at a time; in title_tokens until 11-08 10:16, a column of their own since (N11: search
    weighs them apart, RankConfig.unit_weight, and counts a query word only they have as part found,
    RankConfig.unit_cover)."""
    n_sec = chunk_id = 0
    chunk_rows, fts_rows = [], []

    def flush() -> None:
        conn.executemany("INSERT INTO chunks(chunk_id, doc_id, sec_id, start, end, head_start, head_end)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)", chunk_rows)
        conn.executemany("INSERT INTO chunk_fts(rowid, title_tokens, body_tokens, unit_tokens) VALUES (?, ?, ?, ?)",
                         fts_rows)
        chunk_rows.clear()
        fts_rows.clear()

    for r in rows:
        d = r.doc
        sections, chunks = _chunks(d)
        conn.executemany("INSERT INTO sections(doc_id, sec_id, heading, start, end) VALUES (?, ?, ?, ?, ?)",
                         [(d.doc_id, s.sec_id, s.heading, s.start, s.end) for s in sections])
        n_sec += len(sections)
        heading = {s.sec_id: s.heading for s in sections}
        own_term = term_tokens(Term(r.ay, r.sem)) if d.kind_class == "event" and r.ay is not None else None
        if own_term is not None and r.terms:
            own_term = list(dict.fromkeys(own_term + [t for term in r.terms for t in term_tokens(term)]))
        titles: dict[int, str] = {}
        unit = _tokens("\n".join(r.unit.words))
        for c in chunks:
            chunk_id += 1
            if c.sec_id not in titles:
                titles[c.sec_id] = _tokens("\n".join((d.title, d.parent_title, d.linktext, heading.get(c.sec_id, ""))),
                                           own_term)
            chunk_rows.append((chunk_id, d.doc_id, c.sec_id, c.start, c.end, c.head_start, c.head_end))
            body = d.text[c.start : c.end]
            if c.head_start is not None:
                body = d.text[c.head_start : c.head_end] + "\n" + body
            fts_rows.append((chunk_id, titles[c.sec_id], _tokens(body), unit))
        if len(fts_rows) >= BATCH:
            flush()
    flush()
    conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES ('optimize')")
    return n_sec, chunk_id


@dataclass
class _UnitScope:
    """The unit stage's output (addendum 2026-11-02 F, T7d): the tree's rows, every document's doc_scope rows, the
    report."""

    units: list[tuple[str, str, str, str | None, str | None]]  # (unit_id, name, kind, parent_id, campus), school first
    aliases: list[tuple[str, str]]  # (alias, unit_id): each unit's name, then its aliases
    rows: list[tuple[str, str, str, str | None, str | None, int | None]]  # (doc_id, relation, unit_id, campus, level,
    #                                                                        cohort_from)
    report: dict


def publisher_of(unit: str, grp: str, units: Mapping[str, Unit]) -> str | None:
    """The unit that published a document (T7d): the section its 組別 names when that section is its unit or a part of
    it (教務處's 和平教務組, 招生企劃組 ...), else its unit (教務處); None when its unit is not in the tree."""
    site = resolve(unit, units)
    section = resolve(grp, units) if grp else None
    if site is not None and section is not None and site in ancestors(units, section):
        return section
    return site


def office_of(unit: str | None, units: Mapping[str, Unit]) -> str | None:
    """The office a unit is a section of: the parent of a 行政组别 when that parent is a 行政单位 (生活輔導組's
    學生事務處, 檢定與實習組's 師資培育與就業輔導處); None for any other unit and for None (plan 2026-11-08 T4 review)."""
    if unit is None or units[unit].kind != SECTION_KIND:
        return None
    parent = units[unit].parent_id
    return parent if parent is not None and parent in units and units[parent].kind == OFFICE_KIND else None


def _squashed(s: str) -> str:
    return "".join(norm_text(s).split())


def _not_prefixed(names: Sequence[str], base: Sequence[str], prefixes: Sequence[str]) -> tuple[str, ...]:
    """names without those that only write one of prefixes before one of base (the site list's 「總務處 文書組」 for
    文書組, 「師就處 檢定與實習組」 for 檢定與實習組): the unit line says both already 【Claude】."""
    said = {_squashed(p) + _squashed(b) for p in prefixes if p for b in base if b}
    return tuple(n for n in names if _squashed(n) not in said)


def unit_words(d: Document, units: Mapping[str, Unit] | None, school: str | None = None,
               applies: Sequence[str] = ()) -> UnitWords:
    """The unit words of a document (_own_unit_words), then the units of applies (its doc_scope applies_to, in the
    tree's order) that are a college, a department or a program (APPLIES_KINDS) and that those words do not name, each
    with its short names (owner 11-08 10:16 (b): 「如果一份文件的 doc_scope 把 applies_to 缩小到了具体的学院/系所/学程
    （非全校），请把该适用单位的全称和简称也加进它的 unit_words / passage 里」, so the 323 pages of a 教務處 file for one
    department find 「某系 + 某事」 in every chunk). The school is no such unit; an office or a center is left out too.
    Then the document's categories (Document.categories: the list rows linking its page; blueprint item 22, owner
    11-09 「照这个做」) that these words do not name already."""
    own = _own_unit_words(d, units, school)
    tree = units or {}
    more = tuple((tree[u].name, tree[u].aliases) for u in applies
                 if u in tree and tree[u].kind in APPLIES_KINDS and tree[u].name not in own.words)
    words = dataclasses.replace(own, applies=more) if more else own
    categories = tuple(c for c in d.categories if c not in words.words)
    return dataclasses.replace(words, categories=categories) if categories else words


def _own_unit_words(d: Document, units: Mapping[str, Unit] | None, school: str | None = None) -> UnitWords:
    """The unit words of a document (plan 2026-11-08 T4; the owner, 11-08 04:08: the unit name, the 組別 and the
    unit's short names, so 「某系 + 某事」 finds the department's pages that never name it): its unit (Document.unit,
    the site table's) by its name in the tree with its aliases (units.yaml and the site probe's), as written when the
    tree does not have it or there is no tree; its 組別 as written, with the other names of the section it names when
    that section is a part of the unit (publisher_of: the publisher, 和平教務組's 和平校區教務組), and said once when it
    is the unit's own name or short name (in any spelling the tree resolves); a 組別 naming the school says nothing.
    The school (the tree's root, else sites.table().school) gives no words 【Claude】: it is every unit's root, and
    its name and short name (高師大) on its own documents (the unsure sites, outside hosts) would draw every query that
    names the school to them (4 of the 293 agent queries of 11-01 named 高師大); a 組別 on them stays.

    A unit that is a section of an office (office_of: 生活輔導組 of 學生事務處) comes after the office's name and short
    names, 「單位：學生事務處（學務處） 生活輔導組（生輔組）」 (plan 2026-11-08 T4 review 【Claude】: a section runs its own
    site, so its documents said only 「單位：生活輔導組」 and 「學務處 獎學金」 found nothing by their words, while
    教務處's say 「單位：教務處 和平教務組」); a 組別 naming that office is said once too. A short name that only writes
    the office (or, for a 組別, the unit) before the name is left out (_not_prefixed)."""
    tree = units or {}
    school = sites.table().school if school is None else school
    unit = resolve(d.unit, tree) if tree else None
    name, aliases = (tree[unit].name, tree[unit].aliases) if unit is not None else (" ".join(d.unit.split()), ())
    if (unit is not None and tree[unit].parent_id is None) or name == school:
        name, aliases = "", ()
    office = office_of(unit, tree)
    head: dict = {}
    if office is not None:
        head = {"office": tree[office].name, "office_aliases": tree[office].aliases}
        aliases = _not_prefixed(aliases, [name], [head["office"], *head["office_aliases"]])
    group = " ".join(d.grp.split())
    named = resolve(group, tree) if tree and group else None  # the unit the 組別 names, in any spelling
    # the unit itself, its office, or the school
    said = named is not None and (named in (unit, office) or tree[named].parent_id is None)
    if not group or group in (name, *aliases) or said:
        return UnitWords(name, aliases, **head)
    section = publisher_of(d.unit, group, tree) if tree else None
    more = ()
    if section is not None and section != unit:
        more = tuple(n for n in dict.fromkeys((tree[section].name, *tree[section].aliases)) if n != group)
        more = _not_prefixed(more, [tree[section].name, group],
                             [name, *aliases, head.get("office", ""), *head.get("office_aliases", ())])
    return UnitWords(name, aliases, group, more, **head)


def _set_unit_words(rows: list[_Row], units: Mapping[str, Unit] | None, on: bool,
                    applies: Mapping[str, Sequence[str]] | None = None) -> None:
    """Each row's unit words (unit_words, with the units its doc_scope applies to: applies, doc_id -> unit ids), or
    none when on is False (--no-unit-words, plan 2026-11-08 T4 review): no unit line and no unit tokens, so every
    passage is version 1's (the vector cache of a build before T4 serves it) and a build of the same inputs with and
    without them measures the owner's patch alone (Review Focus 5)."""
    school = sites.table().school
    applies = applies or {}
    for r in rows:
        r.unit = unit_words(r.doc, units, school, applies.get(r.doc.doc_id, ())) if on else UnitWords()


def _applies(scope: _UnitScope | None) -> dict[str, list[str]]:
    """doc_id -> the units its applies_to rows name, each once, in the rows' order (the tree's: unit_scope)."""
    out: dict[str, list[str]] = {}
    for doc_id, relation, unit_id, *_ in scope.rows if scope is not None else ():
        if relation == "applies_to" and unit_id not in out.setdefault(doc_id, []):
            out[doc_id].append(unit_id)
    return out


def site_units_without_alias(units: Mapping[str, Unit]) -> list[str]:
    """The units of the site table (sites.table(), every round-1 site) that the unit tree gives no short name, in the
    table's order, each once, as the table writes them (one the tree does not have too); the school left out, which
    gives no unit words (unit_words). The build report lists them (scope.site_units_no_alias, plan 2026-11-08 T4
    review), so the gap the unit words leave stays visible: config/units.yaml takes a short name with its evidence."""
    out: dict[str, None] = {}
    for row in sites.table().rows.values():
        unit = resolve(row.unit, units)
        if unit is None or (units[unit].parent_id is not None and not units[unit].aliases):
            out.setdefault(row.unit, None)
    return list(out)


def unit_scope(docs: list[Document], units: dict[str, Unit]) -> _UnitScope:
    """Every document's doc_scope rows (addendum 2026-11-02 F; units.doc_scope, T6c): its publisher (publisher_of),
    applies_to (the publisher's reach narrowed by what the title and the text's first SCOPE_HEADER characters name,
    the first SCOPE_BODY characters telling a list the header cuts off, I3; one row per unit x campus x level in the
    tree's order, NULL where none is named, each with the cohort) and mentions. No campus comes from the publisher
    (I2: 和平教務組 and 燕巢教務組 publish school-wide rules). The report counts documents and rows per relation, the
    documents narrowed (applies_to differs from the publisher's reach: by the title alone, or only once the header is
    read), what they name, the publishers, and the 組別 that name no part of their unit (their unit published them)."""
    started = time.monotonic()
    place = {k: i for i, k in enumerate(units)}

    def tree_order(ids: frozenset[str]) -> list[str]:
        return sorted(ids, key=lambda u: (place.get(u, len(place)), u))

    rows: list[tuple] = []
    docs_with, publishers, unplaced, narrowed, named = Counter(), Counter(), Counter(), Counter(), Counter()
    campus, levels = Counter(), Counter()
    for d in docs:
        pub = publisher_of(d.unit, d.grp, units)
        if d.grp and pub is not None and pub != resolve(d.grp, units):
            unplaced[d.grp] += 1
        applies, mentions = doc_scope(d.title, d.text[:SCOPE_HEADER], pub, units, body=d.text[:SCOPE_BODY])
        found = applies  # what the document itself says: the report's 'narrowed' counts read this
        # plan 2026-11-14 T2 (main session, after the review 【Claude】): one text on several departments' sites is
        # kept once, under one of them (_merge_copies); it applies to every college, department and programme whose
        # site holds a copy, so a question naming one of the others finds it with that unit's words and is not
        # told it is another department's (hybrid.off_audience). Only for a document that applies to such units
        # alone: an office's document applies to the school whoever copied it.
        shared = {u for u in (resolve(n, units) for n in d.also_units)
                  if u is not None and units[u].kind in TEACHING_KINDS} - applies.units
        if shared and applies.units and all(units[u].kind in TEACHING_KINDS for u in applies.units):
            applies = dataclasses.replace(applies, units=applies.units | shared)
            named["copies"] += 1
        own = [(d.doc_id, "applies_to", u, c, lv, applies.cohort_from) for u in tree_order(applies.units)
               for c in sorted(applies.campus, key=CAMPUSES.index) or [None]
               for lv in sorted(applies.levels, key=LEVELS.index) or [None]]
        if pub is not None:
            own.append((d.doc_id, "publisher", pub, None, None, None))
            publishers[pub] += 1
        own += [(d.doc_id, "mentions", u, None, None, None) for u in tree_order(mentions)]
        rows += own
        docs_with.update({r[1] for r in own})
        base = reach(pub, units)
        if found != base:
            narrowed["title" if doc_scope(d.title, "", pub, units)[0] != base else "header"] += 1
            named["units"] += int(found.units != base.units)
            named["cohort"] += int(found.cohort_from is not None)
            campus.update(found.campus)
            levels.update(found.levels)
    per_rel = Counter(r[1] for r in rows)
    report = {"units": len(units), "kinds": dict(Counter(u.kind for u in units.values())),
              "aliases": sum(len(u.aliases) for u in units.values()),
              "docs": {rel: docs_with[rel] for rel in RELATIONS}, "rows": {rel: per_rel[rel] for rel in RELATIONS},
              "narrowed": {"total": narrowed["title"] + narrowed["header"], "title": narrowed["title"],
                           "header": narrowed["header"]},
              "named": {"units": named["units"], "campus": {c: campus[c] for c in CAMPUSES if campus[c]},
                        "levels": {lv: levels[lv] for lv in LEVELS if levels[lv]}, "cohort": named["cohort"]},
              # documents whose applies_to also names the departments that hold a copy of them (Document.also_units)
              "copies_units": named["copies"],
              "publishers": dict(publishers.most_common()), "grp_unplaced": dict(unplaced.most_common()),
              "seconds": round(time.monotonic() - started, 2)}
    return _UnitScope([(u.unit_id, u.name, u.kind, u.parent_id, u.campus) for u in units.values()],
                      [(a, u.unit_id) for u in units.values() for a in (u.name, *u.aliases)], rows, report)


def _scope_warnings(report: dict, total: int) -> list[str]:
    """What the unit stage could not place, for the build's warnings (nothing goes missing silently)."""
    out = []
    if unplaced := report.get("grp_unplaced"):
        out.append(f"{sum(unplaced.values())} 份文件的組別不是所属单位的组、按所属单位算发布者："
                   + "、".join(f"{g} {n}" for g, n in unplaced.items()))
    if "docs" in report and (missing := total - report["docs"]["publisher"]):
        out.append(f"{missing} 份文件的所属单位不在单位树上：没有发布者，适用范围按全校算")
    return out


SITE_COLUMNS = ("site", "name", "unit", "data_as_of", "missing_pages", "missing_files", "docs", "export",
                "deleted_notices", "under_read")


def _write(path: Path, rows: list[_Row], meta: dict[str, str], counts: dict, scope: _UnitScope | None = None,
           aliases: list[tuple[str, str, str]] | None = None, site_rows: list[dict] | None = None,
           alumni: bool = True) -> None:
    """The database at path: schema, docs (with their tier, site and unit line, and since plan 2026-11-14 T3, T6 and
    T2 their date line, expires, valid_ay and rank_date: _event_lines, _validity, _rank_dates), doc_alias (aliases:
    _aliases' rows),
    doc_refs, dates, applies_to, the unit tables when scope is given, sites (site_rows: _site_rows'), the index,
    meta. alumni: whether the alumni directory's pages are background (tier_of; plan 2026-11-14 T2)."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA page_size={PAGE_SIZE}")  # before the first table: room for chunk_vecs' 2 KB rows (T7b)
        conn.execute("PRAGMA journal_mode=OFF")  # a scratch file until it is moved into place
        conn.execute("PRAGMA synchronous=OFF")
        create_schema(conn)
        conn.executemany(
            "INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, grp, parent_id, parent_title, linktext,"
            " has_text, text, chars, period_ay, period_sem, ident_date, ident_kind, doc_type, text_source, tier, site,"
            " unit_line, date_line, expires, valid_ay, rank_date, rank_undated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(r.doc.doc_id, r.doc.kind_class, r.doc.kind_raw, r.doc.title, r.doc.url, r.doc.unit, r.doc.grp,
              r.doc.parent_id, r.doc.parent_title, r.doc.linktext, int(r.doc.has_text), r.doc.text, len(r.doc.text),
              r.ay, r.sem, r.ident.date.isoformat() if r.ident else None, r.ident.kind if r.ident else None,
              r.dtype, r.doc.source, tier_of(r.doc.doc_id, r.dtype, r.doc.kind_raw, alumni), r.doc.site,
              r.unit.line, r.date_line, int(r.expires), r.valid_ay,
              r.rank_date.isoformat() if r.rank_date else None, int(r.rank_undated)) for r in rows])
        conn.executemany("INSERT INTO doc_alias(alias, doc_id, why) VALUES (?, ?, ?)", aliases or [])
        conn.executemany("INSERT INTO doc_refs(doc_id, ref_id, zone, context) VALUES (?, ?, ?, ?)",
                         [(r.doc.doc_id, *ref) for r in rows for ref in r.doc.refs])
        conn.executemany("INSERT INTO dates(doc_id, kind, date, raw, source) VALUES (?, ?, ?, ?, ?)",
                         [(r.doc.doc_id, f.kind, f.date.isoformat(), f.raw, f.source) for r in rows for f in r.facts])
        conn.executemany(
            "INSERT INTO applies_to(doc_id, unit, cohort_from, cohort_exact, raw) VALUES (?, ?, ?, ?, ?)",
            [(r.doc.doc_id, r.doc.unit, ay if onwards else None, None if onwards else ay, raw)
             for r in rows for ay, onwards, raw in r.cohorts]
            + [(r.doc.doc_id, r.doc.unit, None, None, "") for r in rows if not r.cohorts])
        if scope is not None:
            conn.executemany("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, ?)",
                             scope.units)
            conn.executemany("INSERT INTO unit_aliases(alias, unit_id) VALUES (?, ?)", scope.aliases)
            conn.executemany("INSERT INTO doc_scope(doc_id, relation, unit_id, campus, level, cohort_from)"
                             " VALUES (?, ?, ?, ?, ?, ?)", scope.rows)
        conn.executemany(f"INSERT INTO sites({', '.join(SITE_COLUMNS)}) VALUES ({', '.join('?' * len(SITE_COLUMNS))})",
                         [tuple(row[c] for c in SITE_COLUMNS) for row in site_rows or []])
        counts["sections"], counts["chunks"] = _write_index(conn, rows)
        meta["counts_json"] = json.dumps(counts, ensure_ascii=False)
        conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", meta.items())
        conn.commit()
    finally:
        conn.close()


def _embed(path: Path, embedder, vec_cache: Path | None, unit: bool = False) -> EmbedStats:
    """Every chunk's vector into the database at path (the build's .tmp file), through the cache file if given."""
    cache = VecCache(vec_cache) if vec_cache is not None else None
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA synchronous=OFF")  # a scratch file until it is moved into place
        return embed_chunks(conn, embedder, cache=cache, unit=unit)
    finally:
        conn.close()
        if cache is not None:
            cache.close()


@dataclass(eq=False)
class _Input:
    """One export the build reads (plan 2026-11-08 T4): the corpus assembled from it, its folder and what is read of
    it: its info (corpus.export_info), its records (each with its export, and the export's site when it names none)
    and its calendar's events (each with its site)."""

    corpus: Path
    export: Path
    info: dict
    records: list[Record]
    events: list[Event]


def _read_input(n: int, corpus: Path, export: Path) -> _Input:
    """Input n (1-based, for the messages). A corpus assembled from another site's export stops the build (BuildError
    【Claude】): its files and notice fields would be read in the wrong folder."""
    info = export_info(export)
    records = [dataclasses.replace(r, export=str(export), site=r.site or info["site"]) for r in load_corpus(corpus)]
    if wrong := sorted({r.site for r in records} - {info["site"]}):
        raise BuildError(f"第 {n} 份输入的 corpus（{corpus}）是网站 {'、'.join(wrong)} 的导出拼的，导出资料夹 {export} 却是"
                         f"网站 {info['site'] or '（没写）'} 的；不建库（--input 的两半要配成对）")
    events = [ev if ev.site else dataclasses.replace(ev, site=info["site"]) for ev in load_calendar(export)]
    return _Input(corpus, export, info, records, events)


def _site_rows(inputs: list[_Input], docs: list[Document], warnings: list[str],
               deleted: Mapping[str, int] | None = None, under: Mapping[str, int] | None = None) -> list[dict]:
    """The sites table (plan 2026-11-08 T4), one row per site in the inputs' order: the export's name, the site
    table's unit, its 资料截至, missing pages and files, the documents whose site it is, its folder, the school's
    deleted notices the build left out (deleted: site -> count, documents.deleted_notice; owner 11-09, blueprint item
    15: 「在每站报告里记个数」; 0 when none) and its documents read again whole (under: site -> count, assemble's
    under_read; owner 11-09, blueprint item 22: 「每站报告加『读到的字远少于整页』的页数」; None for a site not in it,
    none of whose pages assemble could judge). Two exports of
    one site (an old and a new one) are one row: the one with the newer 资料截至 (the first on a tie) gives the date,
    the counts and the folder 【Claude】, and a warning names them all."""
    by_site: dict[str, list[tuple[int, _Input]]] = {}
    for n, inp in enumerate(inputs):
        by_site.setdefault(inp.info["site"], []).append((n, inp))
    per_site = Counter(d.site for d in docs)
    out = []
    for site, numbered in by_site.items():
        best = max(numbered, key=lambda pair: (pair[1].info["data_as_of"], -pair[0]))[1]
        same = [inp for _, inp in numbered]
        if len(same) > 1:
            warnings.append(f"{len(same)} 份导出是同一个网站 {site}：{'、'.join(str(inp.export) for inp in same)}；"
                            f"资料截至和缺漏数用资料截至较新的 {best.export}")
        out.append({"site": site, "name": best.info["name"], "unit": sites.unit_of_site(site),
                    "data_as_of": best.info["data_as_of"], "missing_pages": best.info["missing_pages"],
                    "missing_files": best.info["missing_files"], "docs": per_site[site], "export": str(best.export),
                    "deleted_notices": (deleted or {}).get(site, 0), "under_read": (under or {}).get(site)})
    return out


def build_db(corpus: Path, export_dir: Path, out: Path, **kw) -> BuildReport:
    """The one-pair form (--corpus/--export): build_sites([(corpus, export_dir)], out, **kw)."""
    return build_sites([(Path(corpus), Path(export_dir))], out, **kw)


def build_sites(inputs: Sequence[tuple[Path, Path]], out: Path, *, server_dates: Path | None = None,
                aliases: Path = ALIASES, today: date | None = None, embed: bool = False, vec_cache: Path | None = None,
                embedder=None, site_list: Path | None = None, units_config: Path = UNITS,
                unit_words: bool = True, unit_in_vectors: bool = False, copy_rule: str = COPY_TEXT,
                merge_recurring: bool = True, alumni_background: bool = True, event_dates: bool = True,
                own_titles: bool = True, platform_twins: bool = True, stated_units: bool = True,
                label_titles: bool = False, copy_names: bool = True, date_spikes: bool = True,
                cohort_expiry: bool = True, file_row_dates: bool = True, force_swap: bool = False,
                single_lines: bool = True) -> BuildReport:
    """One database from inputs, each (corpus.json, the export folder it was assembled from), in order (plan
    2026-11-08 T4; the module docstring). Read every input first (a broken one stops the build before anything is
    written), then write out + '.tmp' and move it over out. today (default: Asia/Taipei today) bounds the server dates
    kept: 1990-01-01 … today+400 days; server_dates is one map by address for every input.
    embed: also every chunk's vector (T7b), by embedder (default models.load_embedder(), loaded before anything is
    written; ModelUnavailable passes up) through the vec_cache file when given; a failure leaves the old out.
    site_list (the site probe's list; the command line always passes one) and units_config: the unit tree and every
    document's scope (T7d, unit_scope) and unit words (the function unit_words), read first, so a broken config stops
    the build at once; with no site list the unit tables stay empty, the report says so and the unit words are the
    units as written. unit_words=False (--no-unit-words; T4 review): no unit line and no unit tokens anywhere, the
    owner's patch left out to be measured (_set_unit_words; meta unit_words says which). unit_in_vectors
    (--unit-words-in-vectors): the vectors embed the unit line too; off by default (embed_chunks says why), so the
    keyword index and the reranker carry the unit words and the vectors hold the text. Plan 2026-11-14 T2 (the module
    docstring), each default the new way and its other value the build of before: copy_rule (--copy-rule; COPY_TEXT,
    or COPY_TITLE for the copy rule of before alone; anything else is a ValueError), merge_recurring
    (--no-merge-recurring: every instance of a recurring calendar event stays a document) and alumni_background
    (--no-alumni-background: the alumni directory's pages stay primary). Plan 2026-11-14 T3 and T4, likewise:
    event_dates (--no-event-dates: no calendar event gets its date line, every passage the reranker reads is the one
    of before; the vectors hold no such line either way) and
    own_titles (--no-own-titles: every document keeps the title it has). T6's docs.expires and docs.valid_ay are
    always written (a new column each; nothing read them before). The owner, 11-14 23:55, likewise: platform_twins
    (--no-platform-twins: a notice on the announcement platform and its twin on a unit's own site stay two documents
    unless their texts are the same; the module docstring's 「Platform twins」). The owner, 11-15, likewise:
    stated_units (--no-stated-units: a notice keeps the site table's unit whatever publishing unit its page states;
    _stated_units) and copy_names (--no-copy-names: a merged copy's link names are not carried to the document kept
    for it, only its title and file name as before; _take_over). label_titles is the other way round, off unless
    asked (--label-titles: a file whose page-given name many files of the site carry is titled by its file name;
    documents.own_titles' TITLE_LABEL; never without own_titles), the module docstring's 「Label titles」 says why.
    BuildError: a corpus with ids
    of its own beside another input, a corpus with another export, an id of two keys or a key of two ids."""
    pairs = [(Path(c), Path(e)) for c, e in inputs]
    if not pairs:
        raise ValueError("至少要一份输入（corpus.json 和它的导出资料夹）")
    if copy_rule not in COPY_RULES:
        raise ValueError(f"copy_rule 只能是 {'、'.join(COPY_RULES)}，给的是 {copy_rule!r}")
    out = Path(out)
    vec_cache = Path(vec_cache) if vec_cache is not None else None
    tmp = out.with_name(out.name + ".tmp")
    exports = [e for _, e in pairs]
    for target in (out, vec_cache):
        if target is not None and _protected(target, exports):
            raise ValueError(f"refusing to write {target}: inside an export, ./data, archive*/ or {SKELETON.name}/")
    if vec_cache is not None and vec_cache.resolve() in (out.resolve(), tmp.resolve()):
        raise ValueError(f"refusing to keep the vector cache in the database itself: {vec_cache}")
    warnings: list[str] = []
    units = load_units(Path(site_list), Path(units_config)) if site_list is not None else None
    read = [_read_input(n, c, e) for n, (c, e) in enumerate(pairs, start=1)]
    for n, inp in enumerate(read, start=1):  # a corpus assembled before 11-09 has no area_mark (blueprint item 15)
        if inp.info.get("area_marked") and not any(r.area_mark for r in inp.records):
            warnings.append(f"第 {n} 份输入的 corpus（{inp.corpus}）没有 area_mark，是 11-09 以前拼的：导出里 "
                            f"{inp.info['area_marked']} 行标了「内文区」，学校删掉的公告这次没拿掉，每站数也是 0；"
                            "先用 python -m nknu.content.assemble 重拼")
        # blueprint item 22, the crawler's 53cf31b: a corpus assembled before content read 页别 and 列表上的类别 has
        # neither on any record (review 11-09); every 网页 row with a file is a record of a corpus assembled since
        if inp.info.get("list_marked") and not any(r.list_kind or r.list_category for r in inp.records):
            warnings.append(f"第 {n} 份输入的 corpus（{inp.corpus}）没有页别和列表上的类别，是会读这两栏以前的程序拼的："
                            f"导出里 {inp.info['list_marked']} 行网页标了「列表翻页」或写了列表上的类别，列表翻页的网页"
                            "这次没拿掉，单位行也没写列表类别；先用 python -m nknu.content.assemble 重拼")
        # the crawler's fa81f41: a corpus assembled before content read the export's 「学校回答没有资料」 has no such mark
        if inp.info.get("no_data_marked") and not any(r.area_mark == NO_DATA for r in inp.records):
            warnings.append(f"第 {n} 份输入的 corpus（{inp.corpus}）没有「学校回答没有资料」的标记，是会读这个标记以前的程序拼的："
                            f"导出里 {inp.info['no_data_marked']} 行网页标了，这些空网页这次没拿掉；"
                            "先用 python -m nknu.content.assemble 重拼")
        # owner 11-09, blueprint item 22: a corpus assembled before it judged no page (under_read None throughout)
        pages = [r for r in inp.records if r.source == "totext" and kind_class(r.kind) in ("notice", "page")]
        if len(pages) >= TEMPLATE_PAGES and all(r.under_read is None for r in pages):
            warnings.append(f"第 {n} 份输入的 corpus（{inp.corpus}）没有一页判断过正文读漏：多半是 11-09 以前拼的，读漏的"
                            "网页这次没改读整页，每站报告这一栏写「—」；先用 python -m nknu.content.assemble 重拼")
    if len(read) > 1 and (serial := [n for n, inp in enumerate(read, start=1) if not all(r.key for r in inp.records)]):
        raise BuildError(f"第 {'、'.join(map(str, serial))} 份输入的 corpus 是流水号编号（没有 key：10-29 的，或 11-08 "
                         "以前拼的），它的 p1 和别的导出的 p1 是两回事，只能单独建库；先用 python -m nknu.content.assemble 重拼")
    records = [r for inp in read for r in inp.records]
    check_ids(records)  # plan 2026-11-08 T1, across every input (T4): before anything is written
    scheme = id_scheme(records)
    in_inputs: dict[str, set[int]] = {}
    for n, inp in enumerate(read):
        for r in inp.records:
            in_inputs.setdefault(r.id, set()).add(n)
    records, merged = one_per_id(records)
    events, repeated = one_event_per_id([ev for inp in read for ev in inp.events])
    # plan 2026-11-14 T2: the instances of one recurring event are one event (after one per id: every export showing
    # the calendar brings each instance)
    folded = {"series": 0, "instances": 0, "other_titles": 0, "id_taken": 0}
    if merge_recurring:
        events, folded = one_event_per_series(events)
        if folded["id_taken"]:
            warnings.append(f"行事历有 {folded['id_taken']} 个重复系列的编号和别的事件的编号一样，没合并，各次照旧各算一份")
    first = read[0]
    # the school's deleted notices build_documents leaves out, per site (owner 11-09, blueprint item 15)
    deleted = Counter(r.site or first.info["site"] for r in records if deleted_notice(r))
    docs, excluded = build_documents(records, read_metas(first.export, records), events, site=first.info["site"],
                                     single_lines=single_lines)
    # 11-16 (documents.join_single_lines; --no-single-lines): the runs of one-character lines put right
    joined = {"on": single_lines, "documents": sum(bool(d.lines_joined or d.marks_dropped) for d in docs),
              "joined": sum(d.lines_joined for d in docs), "dropped": sum(d.marks_dropped for d in docs)}
    published = _stated_units(docs, units, stated_units)  # the owner, 11-15: before the copies are merged
    for kind, n in [*merged.items(), *repeated.items()]:
        excluded[kind] = excluded.get(kind, 0) + n
    if away := folded["instances"] - folded["series"]:  # the instances folded into their series' one document
        excluded[EVENT_KIND + SERIES] = away
    groups = load_aliases(aliases)
    server = _server_dates(server_dates, today or today_tpe(), warnings)
    by_event = {"e" + ev.id: ev for ev in events}
    twinned: dict = {}  # the owner, 11-14 23:55: what the platform twins joined (_merge_copies' seen)
    # spec 2026-11-16 §二: a file's own list row dates it (file_row_dates); then the days too many files share are
    # taken from them (date_spikes), before any copy is merged
    all_rows = _rows(docs, by_event, server, file_row_dates)
    spikes = _date_spikes(all_rows, date_spikes, today or today_tpe())
    rows, copies = _merge_copies(all_rows, _file_digest(first.export, records), excluded,
                                 copy_rule, alumni_background, units, platform_twins, twinned, copy_names)
    docs = [r.doc for r in rows]
    # plan 2026-11-14 T2's report first: it names the kept documents by the titles they were merged under
    copied = _copies_report(rows, copies, copy_rule, [inp.info["site"] for inp in read], twinned.get("linked", set()))
    copied["names_carried"] = twinned.pop("names_carried")  # 11-15: the copies' link names the kept ones took over
    ranked = _rank_dates(rows, copies)  # plan 2026-11-14 T2, completed: the date each merged document ranks by
    titled = _retitle(rows, own_titles, label_titles)  # plan 2026-11-14 T4: before anything below reads a title
    dated = _event_lines(rows, by_event, event_dates)  # plan 2026-11-14 T3
    valid = _validity(rows, copies, cohort_expiry)  # plan 2026-11-14 T6; spec 2026-11-16 §二 (cohort_expiry)
    scope = unit_scope(docs, units) if units is not None else None
    _set_unit_words(rows, units, unit_words, _applies(scope))
    id_aliases = _aliases(rows, copies)  # doc_alias (not config/aliases.yaml, the synonym groups)
    if scope is not None:
        named = Counter(len(r.unit.applies) for r in rows if r.unit.applies)
        scope.report = {"site_list": str(site_list), "site_list_sha256": _sha256(Path(site_list)),
                        "units_config": str(units_config), "units_config_sha256": _sha256(Path(units_config)),
                        **scope.report, "site_units_no_alias": site_units_without_alias(units),
                        # owner 11-08 10:16 (b): documents whose unit line names units they apply to, by how many
                        "unit_line_applies": {"docs": sum(named.values()), "by_count": dict(sorted(named.items()))},
                        # blueprint item 22 (owner 11-09): documents whose unit line names their list rows' categories
                        "unit_line_categories": {"docs": sum(bool(r.unit.categories) for r in rows)}}
        warnings += _scope_warnings(scope.report, len(docs))
    # the documents kept whose page assemble read again whole, per site (owner 11-09, blueprint item 22); a site none
    # of whose pages assemble could judge has no count (sites.under_read NULL, the report's —)
    whole = {"d" + r.id for r in records if r.under_read}
    judged = {r.site or first.info["site"] for r in records if r.under_read is not None}
    again = Counter(d.site for d in docs if d.doc_id in whole)
    site_rows = _site_rows(read, docs, warnings, deleted, {site: again.get(site, 0) for site in judged})

    attachments = [d for d in docs if d.kind_class == "attachment"]
    tiers = Counter(tier_of(r.doc.doc_id, r.dtype, r.doc.kind_raw, alumni_background) for r in rows)
    event_docs = sum(d.kind_class == "event" for d in docs)
    series_sites: dict[str, dict[str, int]] = {}  # site -> its recurring events merged and their instances
    for ev in events:
        if ev.days:
            per = series_sites.setdefault(ev.site or first.info["site"], {"series": 0, "instances": 0})
            per["series"] += 1
            per["instances"] += len(ev.days)
    dedupe = {  # plan 2026-11-14 T2 (BuildReport.dedupe)
        "copies": copied,
        "rank_dates": ranked,
        "recurring": {"on": merge_recurring, "series": folded["series"], "instances": folded["instances"],
                      "removed": away, "other_titles": folded["other_titles"], "id_taken": folded["id_taken"],
                      "events_before": event_docs + away, "events_after": event_docs, "by_site": series_sites},
        "alumni": {"on": alumni_background,
                   "background": sum(alumni_page(d.kind_raw) for d in docs) if alumni_background else 0},
        # the owner, 11-14 23:55 (_platform_twins): the pairs found (same; fuller, by whose copy says more), the copy groups
        # they made or widened and the documents that took away, a teaching unit's longer versions left alone
        # (department_longer) and the same-titled pairs left apart
        "twins": {"on": platform_twins, **{k: v for k, v in twinned.items() if k != "linked"}}}
    whys = Counter(why for _, _, why in id_aliases)
    sources: dict[str, Counter] = {}
    for r in rows:
        key = r.ident.kind if r.ident else EVENT_SOURCE if any(f.kind == "event_start" for f in r.facts) else NO_DATE
        sources.setdefault(r.doc.kind_class, Counter())[key] += 1
    counts = {"docs": {**Counter(d.kind_class for d in docs), "total": len(docs)}, "sections": 0, "chunks": 0,
              "dates": dict(Counter(f.kind for r in rows for f in r.facts)),
              "cohort_rows": sum(len(r.cohorts) for r in rows),
              "doc_types": {t: sum(r.dtype == t for r in rows) for t in (JOURNAL, EXAM, MINUTES)},
              "doc_refs": sum(len(d.refs) for d in docs),
              # title_file_name: still titled by its file name, so without those plan 2026-11-14 T4 gave their own
              # title (BuildReport.titles counts them)
              "attachments": {"title_from_page": sum(d.title_from_context for d in attachments),
                              "title_file_name": sum(not d.title_from_context and not d.title_from_text
                                                     for d in attachments),
                              "parent": sum(d.parent_id is not None for d in attachments),
                              "no_parent": sum(d.parent_id is None for d in attachments)},
              "tiers": {t: tiers[t] for t in (PRIMARY, BACKGROUND) if tiers[t]},  # plan 2026-11-08 T1
              "doc_alias": {w: whys[w] for w in ALIAS_WHYS if whys[w]},
              "shared_ids": sum(len(n) > 1 for n in in_inputs.values())}  # T4: ids more than one input holds
    text_sources = dict(Counter(d.source or "corpus" for d in docs))
    digests = [hashlib.sha256(inp.corpus.read_bytes()).hexdigest() for inp in read]
    meta = {"built_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "corpus": str(first.corpus),
            "corpus_sha256": digests[0], "export_dir": str(first.export),
            "server_dates": str(server_dates) if server else "", "site": first.info["site"],
            "name": first.info["name"], "data_as_of": first.info["data_as_of"],
            "missing_pages": str(first.info["missing_pages"]), "missing_files": str(first.info["missing_files"]),
            "inputs_json": json.dumps([{"corpus": str(inp.corpus), "corpus_sha256": sha, "export": str(inp.export),
                                        "site": inp.info["site"]} for inp, sha in zip(read, digests, strict=True)],
                                      ensure_ascii=False),
            "excluded_json": json.dumps(excluded, ensure_ascii=False),
            "aliases_json": json.dumps(groups, ensure_ascii=False), "id_scheme": scheme,
            "unit_words": "on" if unit_words else "off",  # T4 review: a build with --no-unit-words says so
            "unit_in_vectors": "on" if unit_words and unit_in_vectors else "off",  # 11-08: off unless asked
            # plan 2026-11-14 T2: how this database was built, and what that merged
            "copy_rule": copy_rule, "recurring_events": "on" if merge_recurring else "off",
            "alumni_background": "on" if alumni_background else "off",
            "platform_twins": "on" if platform_twins else "off",  # the owner, 11-14 23:55
            # the owner, 11-15: whether a notice stands under the publishing unit its page states, and whether a
            # file's page-given label gave way to its file name
            "stated_units": "on" if published["on"] else "off",
            "label_titles": "on" if own_titles and label_titles else "off",
            "copy_names": "on" if copy_names else "off",
            PUBLISHERS_KEY: json.dumps(published, ensure_ascii=False),
            # spec 2026-11-16 §二: the three date rules; hybrid reads cohort_expiry (validity's cohort_step)
            DATE_SPIKES_KEY: json.dumps(spikes, ensure_ascii=False),
            "single_lines_json": json.dumps(joined, ensure_ascii=False),
            "cohort_expiry": "on" if cohort_expiry else "off", "cohort_years": str(COHORT_YEARS),
            "file_row_dates": "on" if file_row_dates else "off",
            DEDUPE_KEY: json.dumps(dedupe, ensure_ascii=False),
            # plan 2026-11-14 T3, T4, T6: whether the events' passages carry their date line (docs.date_line holds
            # it: embed reads that column for the reranker's passages and never for the vectors, so the value is
            # EVENT_DATES_RERANK, not the first rebuild's "on"), whether documents took their own titles, and what
            # each counted
            "event_dates": EVENT_DATES_RERANK if event_dates else "off", "own_titles": "on" if own_titles else "off",
            TITLES_KEY: json.dumps(titled, ensure_ascii=False), VALIDITY_KEY: json.dumps(valid, ensure_ascii=False),
            "text_pipeline": "totext" if any(r.source == "totext" for r in records) else "",
            SCOPE_KEY: json.dumps(scope.report if scope is not None else SCOPE_SKIPPED, ensure_ascii=False),
            **_copies_meta(rows, copies)}

    if embed and embedder is None:
        embedder = models.load_embedder()  # through the module (the tests' guard); before anything is written
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp.unlink(missing_ok=True)
    vectors = None
    try:
        _write(tmp, rows, meta, counts, scope, id_aliases, site_rows, alumni_background)
        if embed:
            vectors = _embed(tmp, embedder, vec_cache, unit=unit_words and unit_in_vectors)
        # spec 2026-11-16 §五: a site fallen below half of what the library in use holds for it keeps that library in
        # place (the new one stays beside it); force_swap, --force-swap, puts the new one in whatever it holds
        if not force_swap and (gone := _collapsed(out, tmp)):
            held = out.with_name(f"{out.stem}.held{out.suffix}")
            os.replace(tmp, held)
            raise SwapHeld(held, gone)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return BuildReport(counts=counts, excluded=excluded,
                       date_sources={k: dict(v) for k, v in sources.items()},
                       no_date=sum(c[NO_DATE] for c in sources.values()), no_text=sum(not d.has_text for d in docs),
                       warnings=warnings, text_sources=text_sources,
                       vectors=asdict(vectors) if vectors is not None else {},
                       scope=scope.report if scope is not None else dict(SCOPE_SKIPPED), sites=site_rows,
                       inputs=[{"corpus": str(inp.corpus), "export": str(inp.export), "site": inp.info["site"],
                                "records": len(inp.records), "events": len(inp.events)} for inp in read],
                       passage={"version": PASSAGE_VERSION, "unit_words": unit_words,
                                "unit_in_vectors": unit_words and unit_in_vectors,
                                "note": PASSAGE_NOTE if unit_words else PASSAGE_NOTE_OFF},
                       dedupe=dedupe, event_dates=dated, titles=titled, validity=valid, publishers=published,
                       date_spikes=spikes, single_lines=joined)


def _progress_to_stderr() -> Callable[[], None]:
    """embed's progress lines on stderr for one command-line run; returns what undoes it."""
    logger = logging.getLogger("nknu.content.embed")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def undo() -> None:
        logger.removeHandler(handler)
        logger.setLevel(level)

    return undo


def _pairs(args: argparse.Namespace) -> list[tuple[Path, Path]] | str:
    """The inputs the command line names (plan 2026-11-08 T4): every --input CORPUS::EXPORT, else the one pair of
    --corpus and --export (their defaults when neither is given); or what is wrong with them, to print."""
    if args.input and (args.corpus is not None or args.export is not None):
        return "--input 和 --corpus/--export 只能用一种：多份导出都写成 --input CORPUS::EXPORT"
    if not args.input:
        return [(args.corpus or CORPUS_NEW, args.export or EXPORT)]
    pairs = []
    for spec in args.input:
        corpus, sep, export = spec.partition(INPUT_SEP)
        if not (sep and corpus.strip() and export.strip()):
            return f"--input 要写成 CORPUS::EXPORT（corpus.json 和它的导出资料夹，中间用 :: 隔开）：{spec}"
        pairs.append((Path(corpus.strip()), Path(export.strip())))
    return pairs


def _dedupe_words(dedupe: dict) -> list[str]:
    """The printed summary's parts for BuildReport.dedupe (plan 2026-11-14 T2), in Simplified Chinese: the copy
    groups and the documents they removed, the merged documents that rank by a copy's later date and those that rank
    by a date though a copy of theirs has none (each when there are any), the recurring calendar events merged with
    the event documents before and after, the alumni pages made background; a switch turned off says so."""
    c, r, a = dedupe["copies"], dedupe["recurring"], dedupe["alumni"]
    text, title = c["by_rule"][COPY_TEXT], c["by_rule"][COPY_TITLE]
    how = (f"去掉空白后全文相同、至少 {c['min_chars']} 字的 {text['groups']} 组，标题和全文都相同的 {title['groups']} 组"
           if c["rule"] == COPY_TEXT else f"照原来的规则，只合标题和全文都相同的：--copy-rule {COPY_TITLE}")
    copies = (f"副本合并 {c['groups']} 组、少 {c['removed']} 份（跨网站 {c['cross_site']} 组；{how}；"
              "每站数见报告 dedupe.copies.by_site）")
    out = [copies]
    d = dedupe.get("rank_dates") or {}  # plan 2026-11-14 T2, completed (_rank_dates)
    if moved := d.get("later", 0) + d.get("from_copy", 0):
        out.append(f"合并后按副本里较新的日期排名的 {moved} 份（显示的日期照旧是留下那份自己的）")
    if d.get("undated_copy"):
        out.append(f"合并后自己有日期、但有副本没日期的 {d['undated_copy']} 份照有日期的排名"
                   "（合并前那份没日期的副本可能排得比较前；报告 dedupe.rank_dates）")
    if not r["on"]:
        out.append("行事历重复系列没合并（--no-merge-recurring）")
    elif r["series"]:
        out.append(f"行事历重复系列 {r['series']} 个各合成一份（原 {r['instances']} 个单次事件；"
                   f"事件 {r['events_before']} → {r['events_after']} 份）")
    if not a["on"]:
        out.append("所友名录没算背景（--no-alumni-background）")
    elif a["background"]:
        out.append(f"所友名录 {a['background']} 份算背景")
    t = dedupe.get("twins") or {}  # the owner, 11-14 23:55 (_platform_twins); a report of before has none
    if t and not t["on"]:
        out.append("公告平台和单位网站各一份的公告没合并（--no-platform-twins）")
    elif t.get("groups") or t.get("apart"):
        out.append(f"公告平台和单位网站各一份的公告合了 {t['groups']} 组、少 {t['removed']} 份（内容相同的 {t['same']} 对，"
                   f"一份多几行、留多的那份：单位网站的多 {t['fuller']['unit']} 对、平台的多 {t['fuller']['platform']} 对）；"
                   f"没合的：系所网站上比平台长的版本 {t.get('department_longer', 0)} 对，"
                   f"同一天、同标题但两份各有对方没有的字 {t['apart']} 对（报告 dedupe.twins 的两个 _shown）")
    return out


KIND_WORDS = {"notice": "公告", "page": "网页", "attachment": "附件", "event": "行事历", "link": "外站链接"}  # _v6_words


def _v6_words(report: BuildReport) -> list[str]:
    """The printed summary's parts for plan 2026-11-14 T3, T4 and T6 (BuildReport.event_dates, titles, validity), in
    Simplified Chinese: the events given a date line (the reranker's, in no vector); the documents given their own
    title, per kind of title with those that kept theirs; the documents that go out of date per kind, and of the
    attachments on such a document how many each clue kept from it. A switch turned off says so."""
    out = []
    if not report.event_dates["on"]:
        out.append("行事历事件没加日期行（--no-event-dates）")
    elif report.event_dates["events"]:
        out.append(f"行事历事件加日期行 {report.event_dates['events']} 份（只给重排读，不放进向量）")
    t = report.titles
    shared, files, labels = t[TITLE_SHARED], t[TITLE_FILE], t[TITLE_LABEL]
    if not t["on"]:
        out.append("没换标题（--no-own-titles）")
    elif shared["documents"] or files["documents"]:
        out.append(f"标题换成文件自己的 {shared['retitled'] + files['retitled']} 份（同名网页 {shared['documents']} 份里换 "
                   f"{shared['retitled']}、留 {shared['kept']}；档名没有汉字的附件 {files['documents']} 份里换 "
                   f"{files['retitled']}、留 {files['kept']}）")
    if t["on"] and not t["labels"] and labels["documents"]:
        out.append(f"网页给的叫法是很多档共用的附件 {labels['documents']} 份（{labels['titles']} 种叫法）照旧用那个叫法当标题"
                   "（--label-titles 才改回档名）")
    elif t["on"] and labels["documents"]:
        out.append(f"网页给的叫法是很多档共用的附件 {labels['documents']} 份（{labels['titles']} 种叫法）里改回档名 "
                   f"{labels['retitled']}、留 {labels['kept']}（档名没有汉字）")
    pub = report.publishers
    if pub and not pub["on"]:
        out.append(f"没照网页写的公告单位定发布单位（--no-stated-units，或没有单位树；写了公告单位的 {pub['stated']} 份）")
    elif pub and pub["stated"]:
        loose = sum(pub["as_written"].values())
        out.append(f"照网页写的公告单位定发布单位 {pub['stated']} 份（单位树上认得 {pub['placed']}、照原文写 {loose}；"
                   f"单位或组别因此改了的 {pub['changed']} 份），跟着公告改的平台附件 {pub['files']} 份")
    v = report.validity
    kinds = "、".join(f"{word} {v['expires'][kind]}" for kind, word in KIND_WORDS.items() if v["expires"].get(kind))
    out.append(f"会过期的文件 {v['expires']['total']} 份" + (f"（{kinds}）" if kinds else ""))
    a = v["attachments"]
    if a["parent_expires"]:
        c = a["clues"]
        out.append(f"挂在会过期的文件上的附件 {a['parent_expires']} 份：照样会过期 {a['expire']}、不会过期 {a['kept']}"
                   f"（各线索认到的：固定字眼 {c['fixed_words']}、常设页上有副本 {c['standing_copy']}、"
                   f"入学届别 {c['cohort']}，一份可以算进两项）")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.content.build",
                                 description="把拼好的 corpus.json 和它们的导出建成一个资料库（输入只读）")
    ap.add_argument("--input", action="append", default=[], metavar="CORPUS::EXPORT",
                    help="一份导出资料夹和从它拼好的 corpus.json，中间用 :: 隔开；可以给好几次，建成一个资料库"
                         "（计划 2026-11-08 T4）；和 --corpus/--export 二选一")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="只有一份导出时：python -m nknu.content.assemble 写的 corpus.json"
                         "（默认 research/pipeline_2026-11-02/corpus.json）")
    ap.add_argument("--export", type=Path, default=None,
                    help="只有一份导出时：导出资料夹（默认 archive-export/aca-20261102）")
    ap.add_argument("--out", type=Path, default=OUT, help="资料库（默认 research/pipeline_2026-11-02/aca.db）")
    ap.add_argument("--server-dates", type=Path, default=None,
                    help="python -m nknu.content.serverdates 写的档，按网址给每份输入用"
                         "（默认 research/skeleton_2026-11-01/server_dates.json，有就用）")
    ap.add_argument("--aliases", type=Path, default=ALIASES, help="别名表（默认 config/aliases.yaml）")
    ap.add_argument("--report", type=Path, default=None, help="建置报告（默认和资料库同一个资料夹的 build_report.json）")
    ap.add_argument("--embed", action="store_true",
                    help="也算每个小段的 bge-m3 向量（要 pipeline 这组套件和本机快取的模型："
                         "uv run --extra pipeline …）")
    ap.add_argument("--vec-cache", type=Path, default=None,
                    help=f"向量快取（默认和资料库同一个资料夹的 {VEC_CACHE}；重建时只算改过的小段；"
                         "只在 --embed 时用）")
    ap.add_argument("--site-list", type=Path, default=SITE_LIST,
                    help="单位名单：网站普查的 data.json（默认 research/site_probe_2026-10-23/data.json，只读）")
    ap.add_argument("--units", type=Path, default=UNITS, help="单位设定：校区、简称（默认 config/units.yaml）")
    ap.add_argument("--unit-words-in-vectors", action="store_true",
                    help="单位行也放进向量（默认只放进关键词索引和重排读的文字：11-08 实验室电脑量过，放进向量会让同组别的文件都靠近点名组别的问题）")
    ap.add_argument("--no-unit-words", action="store_true",
                    help="不加单位词（单位行，和关键词索引 unit_tokens 里的单位名、组别、简称、适用单位、列表类别）："
                         "passage 和第 1 版一样，用来量整个单位补丁（这几样一起，分不开；计划 2026-11-08 T4 审查）；"
                         "平时不要用")
    ap.add_argument("--copy-rule", choices=COPY_RULES, default=COPY_TEXT,
                    help=f"哪些文件算同一份（计划 2026-11-14 T2）。{COPY_TEXT}（默认）：同一类、去掉空白后全文相同、至少 "
                         f"{COPY_MIN_CHARS} 字的合成一份，跨站、同站、公告都合，不要求标题相同，留最早的那份；原来会合的照旧合。"
                         f"{COPY_TITLE}：只照原来的规则（同类、同标题、同全文），用来重建旧的做法对照")
    ap.add_argument("--no-merge-recurring", action="store_true",
                    help="行事历的重复事件不合成一份，每一次照旧各算一份文件（计划 2026-11-14 T2；用来对照）")
    ap.add_argument("--no-alumni-background", action="store_true",
                    help="所友名录（c.nknu 模板的所友详细页和名单页）不算背景，照旧算主料（计划 2026-11-14 T2；用来对照）")
    ap.add_argument("--no-event-dates", action="store_true",
                    help="行事历事件给重排读的字不加「学期和日期」那一行，照旧只有标题（计划 2026-11-14 T3；用来对照）。"
                         "这一行只给重排读，向量本来就不放（11-14 量过：放进向量会把短标题冲淡），所以开不开向量都一样")
    ap.add_argument("--no-platform-twins", action="store_true",
                    help="公告平台和单位网站各一份的同一则公告不合成一份，照旧各算一份（用户 11-14 23:55 要合；用来对照）。合的条件："
                         "同一天、去掉页面杂项、标点和项目编号后，一份的每一行都能在另一份里找到；一份多几行的留多的那份")
    ap.add_argument("--no-stated-units", action="store_true",
                    help="公告平台的公告不照网页上「公告單位」那一格定发布单位，照旧算网站对单位表写的单位（用户 11-15 要读"
                         "导出的「公告单位」栏；用来对照）")
    ap.add_argument("--label-titles", action="store_true",
                    help="网页给附件的叫法是同一网站 20 份以上附件共用的（「檔案連結」「公文」）不当标题，档名有汉字的改回用档名"
                         "（11-15 做的，默认不开：量过，111 站 894 份标题更具体，但 33 题里「學號」那题从第 4 掉到第 15 名，"
                         "等用户定）")
    ap.add_argument("--no-single-lines", action="store_true",
                    help="转出来一字一行的地方照原样留着（11-16：默认把连续的单字行接成一行、丢掉公文左边那条装订虚线的点；"
                         "直排的标签转出来就是一字一行，字和顺序都对）")
    ap.add_argument("--force-swap", action="store_true",
                    help=f"某个网站的文件数不到现在这份库的一半（现在至少 {GUARD_MIN} 份）时照样换库；默认不换，新库留在旁边的 "
                         "<名字>.held.db、退出码 3（设计稿 2026-11-16 第五节）")
    ap.add_argument("--no-date-spikes", action="store_true",
                    help=f"同一个网站、同一天超过 {SPIKE_MIN} 份附件的档案时间相同时，照旧拿这一天当日期（设计稿 2026-11-16 第二节："
                         "默认把这种扎堆的日子当成搬服务器、整批复制的日子，这些附件算无日期）")
    ap.add_argument("--no-cohort-expiry", action="store_true",
                    help="只写某一届（「X學年度入學適用」，没有「起」）的文件照旧不会过期（设计稿 2026-11-16 第二节：默认那一届"
                         f"读满 {COHORT_YEARS} 年后算会过期）")
    ap.add_argument("--no-file-row-dates", action="store_true",
                    help="档案表格里按钮下载的档案不读它那一行的日期，照旧只继承所属公告的日期或用服务器档案时间"
                         "（设计稿 2026-11-16 第二节「加一条」）")
    ap.add_argument("--no-copy-names", action="store_true",
                    help="合并副本时不把被合掉那份的各个链接叫法带到留下的那份，只带它的标题和档名，照 11-15 以前（用来对照）")
    ap.add_argument("--no-own-titles", action="store_true",
                    help="不把没用的标题（同一网站 20 页以上一模一样的、档名没有汉字的附件）换成文件自己正文的标题，"
                         "照旧用原来的标题（计划 2026-11-14 T4；用来对照）")
    args = ap.parse_args(argv)
    pairs = _pairs(args)
    if isinstance(pairs, str):
        print(pairs, file=sys.stderr)
        return 2
    needed = [p for corpus, export in pairs for p in ((corpus, "corpus.json"), (export / "manifest.json", "导出资料夹"))]
    for path, what in (*needed, (args.site_list, "单位名单"), (args.units, "单位设定")):
        if not path.is_file():
            print(f"找不到{what}：{path}", file=sys.stderr)
            return 2
    server = args.server_dates if args.server_dates is not None else SERVER_DATES if SERVER_DATES.is_file() else None
    vec_cache = (args.vec_cache or args.out.with_name(VEC_CACHE)) if args.embed else None
    undo = _progress_to_stderr() if args.embed else None
    try:
        report = build_sites(pairs, args.out, server_dates=server, aliases=args.aliases, embed=args.embed,
                             vec_cache=vec_cache, site_list=args.site_list, units_config=args.units,
                             unit_words=not args.no_unit_words, unit_in_vectors=args.unit_words_in_vectors,
                             copy_rule=args.copy_rule, merge_recurring=not args.no_merge_recurring,
                             alumni_background=not args.no_alumni_background, event_dates=not args.no_event_dates,
                             own_titles=not args.no_own_titles, platform_twins=not args.no_platform_twins,
                             stated_units=not args.no_stated_units, label_titles=args.label_titles,
                             copy_names=not args.no_copy_names, date_spikes=not args.no_date_spikes,
                             cohort_expiry=not args.no_cohort_expiry, file_row_dates=not args.no_file_row_dates,
                             force_swap=args.force_swap, single_lines=not args.no_single_lines)
    except SwapHeld as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except models.ModelUnavailable as exc:
        print(f"算不了向量，资料库没动：{exc}", file=sys.stderr)
        return 2
    finally:
        if undo is not None:
            undo()
    report_path = args.report or args.out.with_name("build_report.json")
    report_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=1), encoding="utf-8")
    docs, types = report.counts["docs"], report.counts["doc_types"]
    sources = "、".join(f"{s} {report.text_sources[s]}" for s in SOURCES if report.text_sources.get(s))
    v = report.vectors
    vectors = (f"；向量 {v['chunks']} 小段（快取 {v['cached']}、新算 {v['encoded']}，{v['seconds']:.0f} 秒）"
               if v else "")
    n = report.scope.get("narrowed")
    scoped = (f"；单位范围：缩小 {n['total']} 份（标题 {n['title']}、开头 {n['header']}）、"
              f"提及单位 {report.scope['docs']['mentions']} 份" if n else "")
    bare = report.scope.get("site_units_no_alias")
    scoped += f"；网站的单位没有简称的 {len(bare)} 个（报告 scope.site_units_no_alias）" if bare else ""
    applied = report.scope.get("unit_line_applies", {}).get("docs")
    scoped += f"；单位行写了适用单位的 {applied} 份" if applied else ""
    categorized = report.scope.get("unit_line_categories", {}).get("docs")  # blueprint item 22 (owner 11-09)
    scoped += f"；单位行写了列表类别的 {categorized} 份" if categorized else ""
    words = "" if report.passage["unit_words"] else "；没加单位词（--no-unit-words）"
    gone = sum(s.get("deleted_notices", 0) for s in report.sites)  # owner 11-09, blueprint item 15
    words += f"；学校删掉的公告 {gone}（不收，每站数见 sites）" if gone else ""
    whole = sum(s.get("under_read") or 0 for s in report.sites)  # owner 11-09, blueprint item 22
    words += f"；读到的字远少于整页、改读整页 {whole} 份（每站数见 sites）" if whole else ""
    words += "".join(f"；{part}" for part in _dedupe_words(report.dedupe))  # plan 2026-11-14 T2
    words += "".join(f"；{part}" for part in _v6_words(report))  # plan 2026-11-14 T3, T4, T6
    print(f"{args.out}：{docs['total']} 份文件、{report.counts['chunks']} 小段；"
          f"网站 {len(report.sites)} 个（各站资料截至和缺漏见报告 sites）；"
          f"无日期 {report.no_date}、无文字 {report.no_text}；学报（含旧校刊、校讯） {types[JOURNAL]}、考题 {types[EXAM]}、"
          f"会议纪录 {types[MINUTES]}、"
          f"背景资料共 {report.counts['tiers'].get(BACKGROUND, 0)}；"
          f"编号转址 {sum(report.counts['doc_alias'].values())} 笔（副本、其他格式、重复）；"
          f"文字来源 {sources or '（没有）'}{vectors}{scoped}{words}；报告 {report_path}")
    if v and v["encoded"] and not v["cached"]:  # nothing from the cache: the first --embed after a passage change
        if report.passage["unit_words"]:
            print(f"注意：向量快取一个都没中。{report.passage['note']}", file=sys.stderr)
        else:  # the passages are version 1's: a cache of a build before T4 would have served them
            print(f"注意：向量快取一个都没中，可是这次没加单位词、passage 和第 1 版一样，本来应该命中："
                  f"{vec_cache} 是第 1 版建库用的那一份吗？", file=sys.stderr)
    if by_site := report.dedupe["copies"]["by_site"]:  # plan 2026-11-14 T2: where the kept ones are, per site
        print("副本各站（留：留作正本的组数；并掉：被合掉的份数）：" + "；".join(
            f"{site} 留 {n['kept']}、并掉 {n['merged']}" for site, n in by_site.items()))
    for w in report.warnings:
        print(f"注意：{w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
