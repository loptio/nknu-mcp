"""What each row of the archive frontier came to (round-1 design §10): the crawler writes only evidence (state, reason,
status, flags, the sniffed format, the page's type, the list walk, the resources a page uses), and the rules here turn
it into one result code per URL (view url_result), per button press (action_result) and one completeness level and
label per page (page_completeness). These views are the only place the rules live: frontier.py drops and builds them
each time it opens the database, so a rule changed here holds for every row at the next open and nothing stored needs
rewriting; the dashboard, the reports and `nknu archive verify` read the views, never a second copy.

A combination the rules do not know (a new state, flag, kind, page-type verdict or need, a skip for a reason no scope
rule gives, a file nobody sniffed) comes to `unknown`, which is red and fails self_check(); nothing falls through to
`ok` or to `rule` (design §10.2, review notes high and medium). Nothing is taken as fetched without evidence of its
own: page_completeness follows a redirect to the row it lands on and a replaced press to the press that replaced it,
and a round-1 page leaves the table only when the scope sends it to round two or leaves it out, or when the page its
redirects land on is judged in its place. The error pages the crawler recognises by where the school sends a visitor
are ERROR_SIGNATURES (§10.1); RESULT_LABEL, BUCKETS and LABEL_ORDER are the plain words the dashboard shows (§10.2,
§10.2.1, §10.4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

# ---------------------------------------------------------------- §10.1: error pages, known by their address

GONE, ERROR = "gone", "error"
SSO_HOSTS = frozenset({"sso.nknu.edu.tw", "news.nknu.edu.tw"})  # the SSO server (140.127.40.173) and its notices
ERROR_LINK = "error_page"  # the link verdict of an error page: it is never queued (it holds nothing of the school's)
# what round1.admit() makes of an error page anything else brings (a seed list, a page's link or part, a short link's
# answer; review of the 10-26 rehearsal): left out, no url row. As a part of a page (page_res.why) or where a short link
# leads (the link verdict) it is the school's broken link (bad_link, 要人看), never 规则不抓 (_kept_out)
ERROR_OUT = "out:error_page"
# a link to this machine (scope.judge loopback, 11-06: never fetched, 【用户同意 Claude 的建议】): what it stands for
# is the school's broken link wherever it rests, as an error page's address is: a page's part, where a redirect or a
# short link leads, a url row skipped for it (bad_link, 要人看, a person may mark it the school's), never 规则不抓,
# which would count the page complete with its attachment missing 【Claude 的执行办法】
LOOPBACK_OUT = "out:loopback"
BROKEN_OUT = (ERROR_OUT, LOOPBACK_OUT)  # the link verdicts that are the school's broken link (bad_link)


@dataclass(frozen=True)
class ErrorSignature:
    """One kind of school error page. result GONE: the school has removed what was asked for (the row is done, with
    flag, and never fetched again); ERROR: the server failed (the row fails with reason and is retried like any GET,
    so the old archive's copy stands meanwhile)."""

    name: str
    result: str
    hosts: frozenset[str]  # empty: any host
    path_end: str | None  # the lowercased path ends with this
    param: str | None  # a query parameter (lowercase name) the address carries, whatever its value
    flag: str | None = None  # what the crawler adds to url.flags (GONE)
    reason: str | None = None  # the url.reason it fails with (ERROR)


# first match wins: SSO's not-found page carries aspxerrorpath= too. ASP.NET customErrors sends a missing page (404)
# and a program error to the pages configured for each and adds aspxerrorpath= (Microsoft's documentation); on SSO the
# address tells them apart (pageNotFound and ErrorPage); the titles cannot (SSO's error page has a notice's title) and
# neither can a digest (the VIEWSTATE differs each time). The 10-24 run: 71 redirects and 2 direct answers to
# pageNotFound, none to ErrorPage, 2 to the c.nknu template's filenotfound.htm?aspxerrorpath= (affair's sitemap.aspx,
# cacd's activeviewlist.aspx). That one is an error to retry (design §10.1, record 二·4 and 二·6): nothing but its name
# shows it is the page c.nknu's customErrors keeps for a 404 rather than its defaultRedirect, and affair's sitemap.aspx
# went there while aca's, ccee's and reality's answered 200 (the old archive, read-only), which looks like a program
# error more than a missing page. Review 10-26 B6 had made it gone; the second look (F4) put it back as the design has
# it: retried, then server_error (连接失败) for a person to look at, who may mark it the school's (school_side). SSO's
# 200 page 「附件無法下載：檔案不存在」 needs its text read, never its address: the platform file says so (flags
# gone_page, bad_link below).
ERROR_SIGNATURES = (
    ErrorSignature("sso_not_found", GONE, SSO_HOSTS, "/errorpage/pagenotfound.aspx", None, flag="gone_redirect"),
    ErrorSignature("sso_error", ERROR, SSO_HOSTS, "/errorpage/errorpage.aspx", None, reason="error_page"),
    ErrorSignature("aspx_error_path", ERROR, frozenset(), None, "aspxerrorpath", reason="error_page"),
)


def _matches(sig: ErrorSignature, host: str, path: str, params: set[str]) -> bool:
    return ((not sig.hosts or host in sig.hosts) and (sig.path_end is None or path.endswith(sig.path_end))
            and (sig.param is None or sig.param in params))


def error_signature(url: str) -> ErrorSignature | None:
    """The error page this address is (a redirect's Location, or a URL fetched or linked directly), or None."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    params = {k.lower() for k, _v in parse_qsl(parts.query, keep_blank_values=True)}
    path = parts.path.lower()
    return next((sig for sig in ERROR_SIGNATURES if _matches(sig, host, path, params)), None)


# ---------------------------------------------------------------- §10.2: one result per URL

# every flag the crawler may write to url.flags (comma-separated); any other makes the row `unknown`. js_redirect: the
# page sends the browser on by a meta refresh or an inline script (§8.3); click_empty: a tab pressed by its platform
# rule came back with nothing (§7.4); gone_page: the school's own page says what was asked for does not exist, seen
# twice (SSO's 「附件無法下載：檔案不存在」: the platform file reads it, and the first time the row is retried later;
# review 10-26 B6); bad_link: the school's page says the link itself is broken (SSO's 「檔案包含不支援的字元」);
# in_image: a page whose words alone would be thin, its body a picture (reconcile, design §23 E3: 内容在图片里, for the
# organising stage's text recognition): said, never a gap, so not one of PAGE_FLAGS
FLAGS = ("login", "soft404", "decode", "gone_redirect", "gone_id", "error_page", "thin", "empty_detail", "js_hint",
         "form_closed", "js_redirect", "click_empty", "gone_page", "bad_link", "in_image", "no_rows")
# no_rows: a notice list that says it has no notice (profiles.NEWS_NO_ROWS, 「搜尋結果：共0筆」): got, never thin (export
# batch T5 ③; the owner 11-16 「销账照表做」). gone_page is also set for c.nknu's 「404: File not found」 (CNKNU_FNF)
SAYS_NOTHING = ("no_rows", "gone_page")  # the thin step never marks a page with either
IN_IMAGE_WORDS = "内容在图片里（整理阶段做文字识别）"
# what a page's text says: on a file or an image, a crawler bug
PAGE_FLAGS = ("thin", "empty_detail", "js_hint", "js_redirect")
# what a non-page row may be once fetched: embed is a Google form or a Google Sites embed box, HTML by nature (§4.4)
FETCHED_KINDS = ("file", "image", "drive", "calendar", "embed")
# kind0 values that must not come back as a web page (§10.2): a form and an embed box are web pages by nature, so not
# these; a sitemap, a tab's JSON, a school script and a Drive folder's listing are not
_EXPECTED_FILE = ("file", "image", "drive", "drive_list", "calendar", "sitemap", "click", "script")
# Google's APIs (§7.6): read with a key, a file nobody may read and one deleted both answer 404, so neither is `gone`
API_HOSTS = ("www.googleapis.com",)
# google_api.FILE_QUOTA_REASON as the url.reason: one Drive file's downloads used up, three times (design §23 D1): the
# row failed with it is api_quota (要人看), never a failure of ours or of the school's
API_QUOTA_REASON = "api:downloadQuotaExceeded"
# crawler._park_directory: a directory refused twice over (a streak, its rest, a streak again) has its other rows failed
# with this reason, not one request sent for them (design §23 F1): parked, 要人看, never 「试了 3 次还是失败」 nor a mark
# of the school's; `nknu archive retry denied_dir` puts them back. A row still waiting out the rest is pending, as any
PARKED_REASON = "denied_dir"
# crawler.GSIMG_STALE: a Google Sites runtime picture's address that a new reading of its page replaced (design §23
# B2): never sent again, its answer (if any) kept; it comes to where its final_url points, the same picture's new
# address, judged there (redirect), never 连接失败 nor a picture lost
GSIMG_STALE = "gsimg_stale"
# a Drive item's metadata row (kind0 drive, §7.6) is evidence only: a redirect that ends on one comes to what the item
# came to (_drive_settled, review 10-26 phase 3: a page, an attachment or a short link sent to a Drive file counted on
# the metadata's 200). The Google types read without a download of their own (google_api: a folder's listing, a
# shortcut's target, a form's page, the exports); any other Google type has no content row (unhandled:drive_type, B11)
GOOGLE_APPS = "application/vnd.google-apps."
DRIVE_READ_TYPES = ("application/vnd.google-apps.folder", "application/vnd.google-apps.shortcut",
                    "application/vnd.google-apps.form", "application/vnd.google-apps.document",
                    "application/vnd.google-apps.spreadsheet", "application/vnd.google-apps.presentation",
                    "application/vnd.google-apps.drawing")
# programs and disk images, by a file's name (design §4.5: scope.MEDIA_EXT keeps exe, msi, dmg, iso and apk out of a
# plain address; with them macOS's installer package, pkg): a Drive upload named so is listed only, its name and size,
# as a video is (google_api.settle: media:drive_installer; _drive_settled: rule). Gap ⑪ of 11-08: the software pages
# link their installers on Drive, which round one does not download 【Claude 的执行办法】
INSTALLER_EXT = ("exe", "msi", "dmg", "pkg", "iso", "apk")


def installer_name(name: str | None) -> bool:
    """Whether a file's name ends in a program's or a disk image's extension (INSTALLER_EXT, any case)."""
    stem, dot, ext = (name or "").strip().lower().rpartition(".")
    return bool(dot and stem and ext in INSTALLER_EXT)


def _installer_sql(name: str, mime: str) -> str:
    """SQL: a Drive upload (no Google type) whose name says a program or a disk image (installer_name)."""
    ends = " OR ".join(f"lower(trim({name})) LIKE '%_.{e}'" for e in INSTALLER_EXT)
    return f"(coalesce({mime}, '') NOT LIKE '{GOOGLE_APPS}%' AND ({ends}))"
# a landing that holds no page of its own: what the page that is sent there uses is written on that page (the Google
# step's entries: drive:<id>, every file of a folder, a calendar's pages, a form), so that page is judged by them
GOOGLE_LANDING_KIND0 = ("drive", "drive_list", "calendar", "form")
# a short link's row (kind0 shortlink, design §7.7, review 10-26 A2): one GET to learn where it leads, never a page and
# never got by itself. A 3xx, or a 200 page whose target the crawler read (meta refresh, the service's rule) and kept
# as final_url, is a redirect, counted where it leads; a 404 or 410 is a dead short link (short_dead), not a page the
# school removed; a 200 page nobody could read a target from is unhandled (unhandled:shortlink, for a person)
SHORTLINK = "shortlink"
# a file or a picture whose address the school now sends to a school host's home page (*.nknu.edu.tw, path / or none,
# no query): the school retired the address, the school's dead link like a news file sent to the news site's not-found
# page (gone_redirect). The 11-02 教务处 export's 133 old www.nknu.edu.tw/~ attachments and pictures, every one sent to
# https://w3.nknu.edu.tw/ (11-03, 【Claude 的执行办法】). Worked out from the landing address already stored (view
# page_resource: result gone, gone_why gone_home), so nothing is fetched again; a page's link sent there is a page judged
# where it lands, as before
GONE_HOME = "gone_home"
GONE_WHY_LABEL = {GONE_HOME: "链接已失效：跳回学校首页"}  # what the export's 结果 says instead of gone's own words
SCHOOL_DOMAIN = "nknu.edu.tw"


def school_home(url: str | None) -> bool:
    """Whether this address is the root page of a school host (_school_home_sql, the same rule)."""
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme in ("http", "https") and (host == SCHOOL_DOMAIN or host.endswith("." + SCHOOL_DOMAIN))
            and port is None and "@" not in parts.netloc and parts.path.strip("/") == ""
            and not any(c in url for c in "?#"))


# the pages a school sends the address of a notice it deleted to (2026-11-15 judge.md §4.5 (ii): the c.nknu template's
# nodata.html, 「很抱歉，找不到您所查詢的頁面」, where tecs sends 133 old notices, a c.nknu unit's too; clcts's 404.php),
# by the end of its path as profiles.CNKNU_NOT_FOUND and CLCTS_404 read it: such a page is gone,
# and a redirect to it follows it. Read by the views alone, by the address's path on a school host: never one of
# ERROR_SIGNATURES, which the crawler reads too (a link to one would turn bad_link, and the redirect would never be
# followed) (export batch T5; the owner 11-16 「销账照表做」)
NOT_FOUND_PATHS = ("/nodata.html", "/404.php")


def not_found_url(url: str | None) -> bool:
    """Whether this address is one of a school host's not-found pages (NOT_FOUND_PATHS; _not_found_sql, the same
    rule): a school host, no query, its path ending in one of them."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return (host.endswith("." + SCHOOL_DOMAIN) and not parts.query
            and any(parts.path.lower().endswith(x) for x in NOT_FOUND_PATHS))


def _not_found_sql(u: str) -> str:
    """SQL: url row `u` is one of a school host's not-found pages (NOT_FOUND_PATHS)."""
    rest = f"lower(substr({u}.url, instr({u}.url, '://') + 3))"
    host = f"substr({rest}, 1, instr({rest} || '/', '/') - 1)"
    path = f"substr({rest}, instr({rest} || '/', '/'))"
    return (f"({host} LIKE '%.{SCHOOL_DOMAIN}' AND instr({rest}, '?') = 0 AND "
            f"({' OR '.join(f'{path} LIKE {chr(39)}%{x}{chr(39)}' for x in NOT_FOUND_PATHS)}))")  # its path ends so


def _school_home_sql(x: str) -> str:
    """SQL: address `x` is the root page of a school host (school_home(), the same rule)."""
    rest = f"rtrim(substr({x}, instr({x}, '://') + 3), '/')"
    plain = " AND ".join(f"instr({rest}, '{c}') = 0" for c in "/?#@:")
    return (f"(({x} LIKE 'http://%' OR {x} LIKE 'https://%') AND {plain}"
            f" AND (lower({rest}) = '{SCHOOL_DOMAIN}' OR lower({rest}) LIKE '%.{SCHOOL_DOMAIN}'))")
# 「人看过，确认是学校那边的问题」 (review 10-26 B7): the results a person may mark as the school's (review_mark, mark
# school_side): what the school removed or broke, what only it can open again (a Drive file it does not share, a host
# gone for good, a server that always fails, a page it leaves empty). Never what is ours to fix (not_discovered,
# unknown, unhandled, mismatch: a crawler bug, a combination nobody knows, a writing nobody decided, a press that may be
# another notice), nor what waits or is a cap of ours, nor what may be ours (second look, F5 low): lab (the lab
# machine fetches it again, record 三·19), refused (a 401, 403 or 451 may be the school blocking our crawler),
# http_4xx (a 400 or 405 may be a request of ours), and of failed only a host that refused every connection or a
# server that kept answering 5xx (FAILED_SCHOOL_REASONS: a timeout, a TLS failure or a crash may be ours). A marked row
# counts as the school's (school_side, beside gone and login) for the fetch a person looked at: its result, reason,
# status and fetched_at stay what they were when marked; any change, a new fetch with the same answer included, and the
# mark lapses for good (the mark is about that fetch: fetched_at only grows). Which codes is 【Claude 的执行办法】, for
# the user to confirm.
SCHOOL_SIDE = "school_side"
SCHOOL_SIDE_RESULTS = ("gone", "looks_gone", "api_error", "bad_link", "short_dead", "not_file", "server_error", "failed",
                       "thin")
# a failed row may be marked for these reasons (server_down: a server that never answered, given up, crawler 11-08),
# or one that starts with http_5
FAILED_SCHOOL_REASONS = ("connect", "server_down")
FAILED_SCHOOL_PREFIX = "http_5"
MARK_RESULTS = (SCHOOL_SIDE,)  # the codes only a person's mark gives


def markable(result: str, reason: str | None) -> bool:
    """Whether a person may mark this result (with this url.reason) as the school's (school_side)."""
    if result not in SCHOOL_SIDE_RESULTS:
        return False
    return result != "failed" or reason in FAILED_SCHOOL_REASONS or (reason or "").startswith(FAILED_SCHOOL_PREFIX)


def _markable_sql(m: str) -> str:
    """SQL: review_mark row `m` is of a result a person may mark (markable(), the same rule)."""
    return (f"({m}.mark = '{SCHOOL_SIDE}' AND {m}.result IN {_sql_list(SCHOOL_SIDE_RESULTS)} AND ({m}.result <> 'failed'"
            f" OR {m}.reason IN {_sql_list(FAILED_SCHOOL_REASONS)}"
            f" OR substr({m}.reason, 1, {len(FAILED_SCHOOL_PREFIX)}) = '{FAILED_SCHOOL_PREFIX}'))")
# what jscheck may say of one page it opened (js_page.verdict, §8.4); any other is `unknown`
JS_PAGE_VERDICTS = ("ok", "js", "frame", "click", "unresolved")
# a page whose jump menu (or address-building checkbox) its own script fires when it loads (§7.8, §8.4: why
# select:auto): the crawler writes a mark of its own, page_res res_key auto:<the writing's key>, how auto, need no, beside
# the writing's own entry (whose need and why the platform file's decision gives), so that jscheck opens the page and
# the page is 待判定 until it has (review 10-26, 【Claude 的执行办法】: one why cannot hold both)
AUTO_KEY, AUTO_WHY, AUTO_HOW = "auto:", "select:auto", "auto"
_CAP_REASONS = ("site_cap", "file_cap", "variant_cap", "depth")  # how the 10-24 crawler wrote a cap: held, not a rule
_LAB_REASONS = ("dns", "host_unreachable")  # only reachable inside the school's network, or not resolving
# the reasons a row skipped by the scope rules carries (design §4.5, §7.2, §7.6): out:<rule>, a template image
# (decor:repeat), an album's pictures (album:drive), a video or audio type. A skipped row with any other reason (robots,
# which round 1 no longer obeys as a block, §6.8; the 10-24 run's quarantined_*; round2:*, which is `later` now; none)
# is `unknown`, never a rule (review note medium)
RULE_PREFIXES = ("out:", "decor:", "album:")
RULE_REASONS = ("media_type",)


def _after(prefix: str) -> str:
    """The least string after every string that starts with prefix (its last character one higher): a prefix as an
    index range, which LIKE cannot be (it ignores case)."""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def _sql_list(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


# Both flag tests first ask whether the row has flags at all: most rows have none, and the string work it skips is most
# of what a row's result costs (page_completeness works out a result for each of some 400k resources: 0.76 s to 0.16 s)
def _has(flag: str, u: str = "u") -> str:
    """SQL: the flags of url row `u` hold this flag, as a whole item of the comma-separated list."""
    return f"({u}.flags IS NOT NULL AND instr(',' || {u}.flags || ',', ',{flag},') > 0)"


def _unknown_flags(u: str = "u") -> str:
    """SQL: the flags of url row `u` hold an item that is not one of FLAGS. Each comma is doubled so that every item has
    its own pair of commas around it; removing every known item, commas included, must leave only commas (an empty item
    is none)."""
    expr = f"',' || replace({u}.flags, ',', ',,') || ','"
    for flag in FLAGS:
        expr = f"replace({expr}, ',{flag},', '')"
    return f"({u}.flags IS NOT NULL AND replace({expr}, ',', '') <> '')"


def _by_rule(u: str) -> str:
    """SQL: url row `u` was skipped for a reason the scope rules give."""
    likes = " OR ".join(f"{u}.reason LIKE '{prefix}%'" for prefix in RULE_PREFIXES)
    return f"({likes} OR {u}.reason IN {_sql_list(RULE_REASONS)})"


def js_page_join(jp: str, u: str) -> str:
    """SQL: the jscheck record of url row `u` (alias `jp`), when the browser opened the page after its last fetch (both
    times are frontier.utc_now() strings, so they compare as text). Every caller of url_result_case joins it."""
    return f"LEFT JOIN js_page {jp} ON {jp}.url_id = {u}.id AND {jp}.at >= {u}.fetched_at"


def url_result_case(u: str = "u", pt: str = "pt", jp: str = "jp") -> str:
    """SQL: the result of url row `u`, whose page type is row `pt` (joined on pt.key = u.page_type) and whose jscheck
    record is row `jp` (js_page_join). The one statement of the §10.2 rules: the view url_result applies it, and so
    does page_completeness, which joins url, page_type and js_page itself so that SQLite looks each resource up by its
    index instead of building all of url_result first.

    A row a person marked as the school's (review_mark, review 10-26 B7) is school_side while the evidence is what it
    was when marked (the same result, reason, status and fetch) and the result is one a person may mark (markable());
    otherwise the mark is not there for these rules."""
    base = _url_result_base(u, pt, jp)
    m = f"{u}_mark"
    return f"""CASE WHEN EXISTS (SELECT 1 FROM review_mark {m} WHERE {m}.url_id = {u}.id AND {_markable_sql(m)}
                         AND {m}.reason IS {u}.reason AND {m}.status IS {u}.status
                         AND {m}.fetched_at IS {u}.fetched_at AND {m}.result = ({base}))
  THEN '{SCHOOL_SIDE}' ELSE ({base}) END"""


def _url_result_base(u: str, pt: str, jp: str) -> str:
    """SQL: the result of url row `u` by its own evidence, before any person's mark (url_result_case).

    A file, image, Drive item or calendar (or a row expected to be one) that came back with a 200 and no sniffed format
    is `unknown`: nothing shows it is not a web page (§7.3 sniffs every 200; the old archive's 25 HTML 'files'). An
    attachment or a picture (kind0, else kind, file or image) answered 200 with no byte at all is not_file (§23 A1 ①;
    the school's 「檔案不存在」 page stays gone or bad_link, which says more).

    A Google Sites runtime picture's address a new reading of its page replaced (reason gsimg_stale, §23 B2) is a
    redirect to the same picture's new address (final_url), sent or not.

    A page (§8.3, §8.4): one that sends the browser on is a redirect, judged where it lands; then the type's verdict and
    this page's own browser check (js, unresolved, click: the more cautious wins); a page with a strong hint, and a
    page whose jump menu fires on load (AUTO_KEY), is not checked until the browser opened this very page after its
    last fetch, whatever its type says. A jscheck record with
    a verdict these rules do not know is `unknown` (never the type's ok)."""

    def has(flag: str) -> str:
        return _has(flag, u)

    expected = f"coalesce({u}.kind0, {u}.kind) IN {_sql_list(_EXPECTED_FILE)}"
    short = f"coalesce({u}.kind0, {u}.kind) = '{SHORTLINK}'"
    return f"""CASE
  WHEN {u}.state IN ('pending','active') THEN
       CASE WHEN {u}.reason IN {_sql_list(_LAB_REASONS)} THEN 'lab'
            WHEN {u}.attempts > 0 THEN 'retry' ELSE 'todo' END
  WHEN {u}.state = 'held' THEN 'held'
  WHEN {u}.state = 'later' THEN 'later'
  WHEN {u}.state = 'skipped' THEN
       CASE WHEN {u}.reason = 'too_large' THEN 'too_large'
            WHEN {u}.reason IN ('moved_scheme', '{GSIMG_STALE}') THEN 'redirect'
            WHEN {u}.reason LIKE 'cap:%' OR {u}.reason IN {_sql_list(_CAP_REASONS)} THEN 'held'
            WHEN {u}.reason = '{LOOPBACK_OUT}' THEN 'bad_link'
            WHEN {_by_rule(u)} THEN 'rule'
            ELSE 'unknown' END
  WHEN {u}.state = 'failed' THEN
       CASE WHEN {u}.reason IN {_sql_list(_LAB_REASONS)} THEN 'lab'
            WHEN {u}.reason = '{PARKED_REASON}' THEN 'parked'
            WHEN {u}.reason = 'error_page' THEN 'server_error'
            WHEN {u}.reason = '{API_QUOTA_REASON}' THEN 'api_quota'
            ELSE 'failed' END
  WHEN {u}.state <> 'done' THEN 'unknown'
  WHEN {u}.reason = '{GSIMG_STALE}' THEN 'redirect'
  WHEN {_unknown_flags(u)} THEN 'unknown'
  WHEN {u}.status BETWEEN 300 AND 399 THEN
       CASE WHEN {has('gone_redirect')} THEN 'gone'
            WHEN {has('login')} THEN 'login'
            WHEN {has('error_page')} THEN 'server_error'
            ELSE 'redirect' END
  WHEN {short} AND {u}.status IN (404, 410) THEN 'short_dead'
  WHEN {u}.host IN {_sql_list(API_HOSTS)} AND {u}.status IN (403, 404, 410) AND NOT {has('login')} THEN 'api_error'
  WHEN {u}.status IN (404, 410) THEN 'gone'
  WHEN {u}.status IN (401, 403) AND {has('login')} THEN 'login'
  WHEN {u}.status IN (401, 403, 451) THEN 'refused'
  WHEN {u}.status >= 500 THEN 'server_error'
  WHEN {u}.status >= 400 THEN 'http_4xx'
  WHEN {u}.status IS NOT 200 THEN 'unknown'
  WHEN {has('login')} THEN 'login'
  WHEN {has('gone_redirect')} OR {has('gone_page')} THEN 'gone'
  WHEN {has('bad_link')} THEN 'bad_link'
  WHEN {short} THEN
       CASE WHEN {u}.final_url IS NOT NULL AND {u}.final_url <> {u}.url THEN 'redirect' ELSE 'later' END
  WHEN {has('gone_id')} THEN 'gone'
  WHEN {u}.kind = 'page' AND {_not_found_sql(u)} THEN 'gone'
  WHEN {has('soft404')} THEN 'looks_gone'
  WHEN {has('error_page')} THEN 'server_error'
  WHEN {has('decode')} THEN 'decode'
  WHEN {expected} AND {u}.sniff = 'html' THEN 'not_file'
  WHEN coalesce({u}.kind0, {u}.kind) IN ('file', 'image') AND {u}.bytes = 0 THEN 'not_file'
  WHEN {u}.sniff IS NULL AND ({u}.kind <> 'page' OR {expected}) THEN 'unknown'
  WHEN {has('click_empty')} THEN 'thin'
  WHEN {u}.kind = 'page' THEN
       CASE WHEN {has('js_redirect')} THEN 'redirect'
            WHEN {jp}.url_id IS NOT NULL AND {jp}.verdict NOT IN {_sql_list(JS_PAGE_VERDICTS)} THEN 'unknown'
            WHEN {pt}.verdict = 'js' OR {jp}.verdict = 'js' THEN 'js'
            WHEN ({has('thin')} AND NOT EXISTS (SELECT 1 FROM page_res {u}_alb WHERE {u}_alb.page_id = {u}.id
                 AND {u}_alb.why = 'album')) OR {has('empty_detail')} THEN 'thin'
            WHEN {pt}.verdict = 'unresolved' OR {jp}.verdict = 'unresolved' THEN 'unresolved'
            WHEN {pt}.verdict = 'click' OR {jp}.verdict = 'click' THEN 'click'
            WHEN {has('js_hint')} AND {jp}.url_id IS NULL THEN 'unchecked'
            WHEN {jp}.url_id IS NULL AND EXISTS (SELECT 1 FROM page_res {u}_auto WHERE {u}_auto.page_id = {u}.id
                 AND {u}_auto.res_key >= '{AUTO_KEY}' AND {u}_auto.res_key < '{_after(AUTO_KEY)}') THEN 'unchecked'
            -- a type no browser check judged, on a page with no sign of JavaScript: got (the owner 11-16, direction
            -- §四 2: 「待判定」改「抓到了」; jscheck does not run this round). A sign of JavaScript: unchecked, round two's
            WHEN coalesce({pt}.verdict, 'todo') = 'todo' THEN 'ok'
            WHEN {pt}.verdict IN ('ok', 'frame') THEN 'ok'
            ELSE 'unknown' END
  WHEN {u}.kind IN {_sql_list(FETCHED_KINDS)} AND NOT ({' OR '.join(has(flag) for flag in PAGE_FLAGS)}) THEN 'ok'
  ELSE 'unknown' END"""


URL_RESULT_SQL = f"""
SELECT u.*, pt.verdict AS type_verdict, {url_result_case()} AS result
FROM url u LEFT JOIN page_type pt ON pt.key = u.page_type
{js_page_join('jp', 'u')}
"""
URL_RESULTS = ("ok", "gone", "login", "redirect", "rule", "later", "todo", "retry", "lab", "js", "unchecked", "thin",
               "refused", "http_4xx", "decode", "too_large", "failed", "server_error", "not_file", "held", "unknown",
               "unresolved", "click", "looks_gone", "api_error", "api_quota", "bad_link", "short_dead",
               "parked", SCHOOL_SIDE)  # unhandled: a resource's or a press's only, since a short link with no way is later

# ---------------------------------------------------------------- §10.3: one result per button press

# click: a button pressed by its platform rule that posts for a file's address (ee's download buttons, §7.4); data:
# a read of a platform's data address, once per site (cLv2's timeline, design §23 C1)
ACTION_KINDS = ("pager", "detail", "download", "click", "data")
# profiles.DATA_READ, profiles.DATA_SCHOOL_ERROR: a data address's answer read (read:empty, read:events:<n>), or one
# that says the school's server erred (school_error:<its message>)
DATA_READ, DATA_SCHOOL_ERROR = "read:", "school_error:"
# what a data address's answer said, in words, after 抓到了 (design §23 C1: 「时间轴是空的」)
DATA_WORDS = {"read:empty": "时间轴是空的"}
EMPTY_DETAIL = "empty_detail"  # postback.EMPTY_DETAIL: the action.reason of a detail answered empty
NOT_FILE = "not_file:"  # postback.NOT_FILE: the action.reason of a download whose answer is no file (§23 A1, A2)


def action_result_case(a: str = "a") -> str:
    """SQL: the result of action row `a` (design §10.3). As a row, a press a restart replaced (superseded) and one
    answered with a redirect are `redirect`; page_completeness counts each by the press that replaced it, or by where
    it led. A download that came back with a 200 and no sniffed format is `unknown`, as a url row's is. A download the
    crawler found no file in (reason not_file:<why>: empty, no name and no file format, the school's 「檔案不存在」, an
    export GET that redirected twice; design §23 A1, A2) is not_file whatever its status; a press answered too large
    to keep (failed, reason too_large) is too_large, for a person to decide (§23 A3), never failed. A read of a data
    address (kind data, §23 C1) is ok only when its answer was read (reason read:…); one that says the school's server
    erred is server_error; any other 200 is unknown."""
    return f"""CASE
  WHEN {a}.state IN ('pending','active') THEN 'todo'
  WHEN {a}.state = 'superseded' THEN 'redirect'
  WHEN {a}.state = 'failed' AND {a}.reason = 'too_large' THEN 'too_large'
  WHEN {a}.state = 'failed' THEN 'failed'
  WHEN {a}.state = 'mismatch' THEN 'mismatch'
  WHEN {a}.state <> 'done' THEN 'unknown'
  WHEN {a}.kind = 'download' AND {a}.reason LIKE '{NOT_FILE}%' THEN 'not_file'
  WHEN {a}.status BETWEEN 300 AND 399 THEN 'redirect'
  WHEN {a}.status IN (401, 403, 451) THEN 'refused'
  WHEN {a}.status >= 500 THEN 'server_error'
  WHEN {a}.status >= 400 THEN 'http_4xx'
  WHEN {a}.status IS NOT 200 THEN 'unknown'
  WHEN {a}.kind NOT IN {_sql_list(ACTION_KINDS)} THEN 'unknown'
  WHEN {a}.kind = 'download' AND {a}.sniff = 'html' THEN 'not_file'
  WHEN {a}.kind = 'download' AND {a}.sniff IS NULL THEN 'unknown'
  WHEN {a}.kind = 'detail' AND {a}.reason = '{EMPTY_DETAIL}' THEN 'thin'
  WHEN {a}.kind = 'data' AND {a}.reason LIKE '{DATA_SCHOOL_ERROR}%' THEN 'server_error'
  WHEN {a}.kind = 'data' AND ({a}.reason IS NULL OR {a}.reason NOT LIKE '{DATA_READ}%') THEN 'unknown'
  ELSE 'ok' END"""


ACTION_RESULT_SQL = f"""
SELECT a.*, {action_result_case()} AS result
FROM action a
"""
# thin: a detail a press brought back with nothing in it (action.reason empty_detail, §10.1 nonempty as for a detail
# got by GET; review 10-26 phase 3, 【Claude 的执行办法】: §10.3's view has no such line)
ACTION_RESULTS = ("todo", "redirect", "failed", "mismatch", "refused", "server_error", "http_4xx", "not_file", "ok",
                  "thin", "too_large", "unknown")
# a resource of a page: a URL's or a press's result, or not_discovered when the page names it and nothing says what
# came of it (a crawler bug, never complete), or unhandled: a writing (or a Google address) nobody has decided about
# yet (§7.6, §7.8)
RESOURCE_RESULTS = tuple(dict.fromkeys((*URL_RESULTS, *ACTION_RESULTS, "not_discovered", "unhandled")))

# ---------------------------------------------------------------- §10.4: each page's completeness

# a needed resource's result, in exactly one group (test_outcome checks the split)
GROUPS = {
    "good": ("ok", "rule", "gone", "login", SCHOOL_SIDE, "bad_link", "short_dead"),  # nothing more to fetch
    "wait": ("todo", "retry", "lab"),
    "unchecked": ("unchecked",),
    "round2": ("later", "js", "click"),
    # redirect: page_completeness follows every redirect to where it landed, so none is left; one that is would be a
    # gap in these rules, never a resource fetched
    "bad": ("failed", "not_file", "held", "refused", "http_4xx", "server_error", "mismatch", "unknown",
            "not_discovered", "too_large", "decode", "thin", "redirect", "unresolved", "looks_gone", "api_error",
            "api_quota", "unhandled", "parked"),
}
# fetched as far as the school allows (school_side: a person said so; bad_link and short_dead: the school's broken link
# and dead short link, the owner 11-16 「销账照表做」)
SCHOOL = ("gone", "login", SCHOOL_SIDE, "bad_link", "short_dead")
LOST = ("not_discovered", "unknown")  # a resource nothing accounts for, or a combination nobody knows: a crawler bug
# 连接失败: nothing came from the server, after the retries (the user's category of the same name, §10.2.1)
FAILED = ("failed", "lab", "server_error", "refused", "http_4xx", "not_file")
# 要人看: got but maybe wrong, or the crawler stopped by itself, or a person must decide (§10.4). thin here is a
# resource's: a page that is itself thin has its own label. redirect: a resource whose redirects never land
REVIEW = ("held", "too_large", "mismatch", "unknown", "not_discovered", "decode", "thin", "unresolved", "looks_gone",
          "api_error", "api_quota", "unhandled", "redirect", "parked")
# a round-1 page whose redirects lead to what the rules leave out (a page that is itself skipped by a rule or too large
# is not judged at all): a person decides whether the rule or the site list should change
OWN_REVIEW = ("rule",)
NOT_JUDGED = ("later", "rule", "too_large")  # a page the scope sends to round two or leaves out (§10.4)
MAX_HOPS = 20  # httpx and Chrome give up after 20 redirects: a chain longer than that (a loop) never lands
FILE_ROLES = ("file", "download", "drive")
# a resource that must be a file or an image (review 10-26 B5): resting on a web page that came back readable (an
# attachment redirected to its site's home page) it is not_file, however good that page; a 404, a sign-in or a page
# still waiting says more and stays
FILE_LIKE_ROLES = (*FILE_ROLES, "img")
CARRY_FILE_ROLES = ("shortlink", "iframe", "frame")  # what may lead to a file without being one by its role
PAGE_CAME_BACK = ("ok", "thin", "unchecked", "js", "click", "unresolved")
FRAME_ROLES = ("frame", "iframe")
LIST_ENDS_OK = ("last_page", "no_rows_ok")  # listwalk.ended_by of a list walked to its end (design §7.5 step 7)

LEVEL_LABEL = {
    "waiting": "还没抓完",
    "complete_school": "完整（学校那边缺）",
    "unchecked": "待判定",
    "list_unchecked": "列表没核对",
    "incomplete": "不完整",
    "partial": "部分完整",
    "complete": "完整",
}
# the one label a page shows, the first that holds (design §10.4); `reasons` keeps all of them in this order
LABEL_ORDER = (
    ("todo", "还没抓完"),
    ("retry", "出错待重试"),
    ("failed", "连接失败"),
    ("thin", "要人看：页面几乎是空的"),
    ("review", "要人看"),
    ("js", "要 JavaScript（下一轮补）"),
    ("list", "列表没翻完"),
    ("list_unchecked", "列表没核对"),
    ("miss_file", "缺附件"),
    ("miss_img", "缺图片"),
    ("miss_other", "缺内嵌页或按钮"),
    ("click", "点了才载入的内容（下一轮补）"),
    # design §23 B3: what the page lacks is only pictures left for round two (Google Sites' runtime pictures)
    ("round2_img", "缺图片（下一轮补）"),
    ("round2", "有下一轮补的资源"),
    ("unchecked", "待判定"),
    ("complete_school", "完整（学校那边缺）"),
    ("complete", "完整"),
)
_LABEL_WHEN = {
    "todo": "own = 'todo' OR list_walking OR n_todo > 0",
    "retry": "own = 'retry' OR n_retry > 0",
    "failed": f"own IN {_sql_list(FAILED)} OR n_failed > 0",
    "thin": "own = 'thin'",
    # a page that is itself thin has its own label above; thin among the resources is 要人看
    "review": f"own IN {_sql_list((*(c for c in REVIEW if c != 'thin'), *OWN_REVIEW))} OR n_review > 0",
    "js": "own = 'js'",
    "list": "list_bad",
    "list_unchecked": "list_unchecked",
    "miss_file": "miss_file > 0",
    "miss_img": "miss_img > 0",
    "miss_other": "miss_other > 0 OR frame_missing",
    "click": "own = 'click' OR n_click > 0",
    "round2_img": "own <> 'later' AND n_round2_img > 0 AND n_round2 = n_round2_img",
    "round2": "own = 'later' OR n_round2 > 0",  # later: a page that now redirects to a second-round address
    "unchecked": "own = 'unchecked' OR n_unchecked > 0",
    "complete_school": f"own IN {_sql_list(SCHOOL)} OR n_school > 0",
}
# the level, the first that holds (design §10.4). lab waits (the lab machine fetches it again) though its label and
# the user's category say 连接失败
# gone, login: nothing of the page is there to need anything. A page a person marked as the school's (school_side, a
# thin page say) still needs what it uses (second look, F5 medium): it is judged as an ok page, and complete_school
# only once its resources are there too
_NOTHING_THERE = tuple(c for c in SCHOOL if c != SCHOOL_SIDE)
_LEVEL_WHEN = (
    ("waiting", "own IN ('todo', 'retry', 'lab') OR list_walking"),
    ("complete_school", f"own IN {_sql_list(_NOTHING_THERE)}"),
    ("unchecked", "own = 'unchecked'"),
    ("list_unchecked", "list_unchecked"),
    ("incomplete", f"own NOT IN ('ok', 'click', '{SCHOOL_SIDE}') OR list_bad"),
    ("partial", "n_bad + n_wait + n_unchecked + n_round2 > 0 OR frame_missing OR own = 'click'"),
    ("complete_school", f"n_school > 0 OR own = '{SCHOOL_SIDE}'"),
)
_COUNTS = ("n_must", "n_school", "n_todo", "n_retry", "n_failed", "n_wait", "n_unchecked", "n_round2", "n_click",
           "n_review", "n_bad", "miss_file", "miss_img", "miss_other", "n_frames", "n_round2_img")
JUDGED = ("level", "label", "reasons")  # what page_completeness works out of its other columns (_judged_sql)


def _judged_sql() -> str:
    """SQL: page_completeness's level, label and reasons out of a row's other columns (design §10.4)."""
    level = "".join(f"\n       WHEN {when} THEN '{code}'" for code, when in _LEVEL_WHEN)
    label = "".join(f"\n       WHEN {_LABEL_WHEN[code]} THEN '{code}'" for code, _zh in LABEL_ORDER[:-1])
    reasons = " || ".join(f"CASE WHEN {_LABEL_WHEN[code]} THEN '{code},' ELSE '' END" for code, _zh in LABEL_ORDER[:-1])
    return f"""CASE{level}
       ELSE 'complete' END AS level,
  CASE{label}
       ELSE 'complete' END AS label,
  rtrim({reasons}, ',') AS reasons"""


def judge_page(row: dict, **changed: object) -> dict[str, str]:
    """page_completeness's level, label and reasons of one of its rows with some columns changed (own='ok': a page the
    export read again whole, export.py, item ⑧ of the 11-08 gaps), by the view's own SQL over that one row in memory:
    never a second copy of the rules."""
    cols = [c for c in row if c not in JUDGED]
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"CREATE TABLE p ({', '.join(_quoted(c) for c in cols)})")
        conn.execute(f"INSERT INTO p VALUES ({', '.join('?' for _ in cols)})", [changed.get(c, row[c]) for c in cols])
        got = conn.execute(f"SELECT {_judged_sql()} FROM p").fetchone()
    finally:
        conn.close()
    return dict(zip(JUDGED, got, strict=True))


# a Google address no Google step reads (round1.admit; the forms' pictures, docs.google.com/forms-images-rt: 795 of 801
# rows on 11-15): round two's, the owner's decision of 11-16 (direction §四 1), never 要人看
UNHANDLED_GOOGLE = "unhandled:google"


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _kept_out(verdict: str, link: bool = True) -> str:
    """SQL: what a resource or a redirect target with no url row came to, by the verdict that kept it out of the queue
    (the link table's, or page_res.why): a school error page's address or this machine's (BROKEN_OUT: the school's
    broken link), behind a sign-in, left out by a rule, or else lost (never complete). A link
    verdict unhandled:<why> (a Google address with no Google step to read it, round1.admit) is unhandled, for a person;
    a page_res.why of that kind is placed before this (need must), and with any other need is lost as before."""
    unhandled = (f"WHEN {verdict} = '{UNHANDLED_GOOGLE}' THEN 'later' WHEN {verdict} LIKE 'unhandled:%' "
                 "THEN 'unhandled' ") if link else ""
    return (f"CASE WHEN {verdict} IN {_sql_list(BROKEN_OUT)} THEN 'bad_link' "
            f"WHEN {verdict} LIKE 'out:login%' THEN 'login' WHEN {verdict} LIKE 'out:%' THEN 'rule' "
            f"{unhandled}ELSE 'not_discovered' END")


def _off_file(verdict: str) -> str:
    """SQL: a link verdict that says no file can come from where a file was expected to land (second look, F3): every
    out:<rule> but a sign-in (要登录, the school's) and media (the address is a video or a sound: the rule's own case,
    the file is what the rule leaves out). An attachment redirected to Facebook, to Drive's web page, to a trap is not
    got: 连接失败 (not_file), never 规则不抓."""
    return f"({verdict} LIKE 'out:%' AND {verdict} NOT LIKE 'out:login%' AND {verdict} <> 'out:media')"


def _drive_settled(meta: str, level: int = 0) -> str:
    """SQL: what the Drive item whose metadata row id is `meta` came to, by google_api.settle()'s rules, for a redirect
    that ends on that row once the row itself is ok (review 10-26 phase 3): its content row's result; a video or a
    sound listed only is `rule` (record 一·1: 只记档名和大小), as is a program or a disk image by its name (INSTALLER_EXT,
    gap ⑪ of 11-08); a Google type nobody can export, `unhandled`; read and no
    content row (a crawler bug), `not_discovered`. A shortcut's content is its target's metadata row: that one in turn,
    once (Drive's shortcuts never point at a shortcut; one that does is not_discovered, for a person)."""
    d, c = f"dz{level}", f"cz{level}"
    url_res, jpj = url_result_case, js_page_join
    if level == 0:
        target = (f"WHEN coalesce({c}.kind0, {c}.kind) = 'drive' THEN CASE WHEN ({url_res(c, c + 'pt', c + 'jp')}) "
                  f"<> 'ok' THEN ({url_res(c, c + 'pt', c + 'jp')}) ELSE {_drive_settled(f'{c}.id', 1)} END")
    else:
        target = f"WHEN coalesce({c}.kind0, {c}.kind) = 'drive' THEN 'not_discovered'"
    return f"""coalesce((SELECT CASE
        WHEN {d}.mime LIKE 'video/%' OR {d}.mime LIKE 'audio/%' THEN 'rule'
        WHEN {_installer_sql(f'{d}.name', f'{d}.mime')} THEN 'rule'
        WHEN {c}.id IS NULL AND {d}.mime LIKE '{GOOGLE_APPS}%' AND {d}.mime NOT IN {_sql_list(DRIVE_READ_TYPES)}
             THEN 'unhandled'
        WHEN {c}.id IS NULL THEN 'not_discovered'
        {target}
        ELSE ({url_res(c, c + 'pt', c + 'jp')}) END
      FROM drive_item {d} LEFT JOIN url {c} ON {c}.id = {d}.content_id
      LEFT JOIN page_type {c}pt ON {c}pt.key = {c}.page_type {jpj(c + 'jp', c)}
      WHERE {d}.url_id = {meta}), 'not_discovered')"""


def _resource_ctes() -> str:
    """The WITH clause both views share: every needed resource of a page and what it came to (page_resource reads it
    as it is, page_completeness counts it). In the order it is built:

    hop, landing, moved: every row answered with a redirect, followed hop by hop through url.final_url (at most
    MAX_HOPS) to the row it lands on, which gives its result; with no row there, the link verdict of the last hop
    does (out:login, out:<rule>, unhandled:<why>), else not_discovered; a chain that never lands is a server error
    (review note medium: a redirect is never `fetched` by itself, and the §7.1 copy of a page_res row is no longer
    needed for it). hop also notes whether any row along the way was expected to be a file (exp: its kind0 is one of
    _EXPECTED_FILE), however the page reached it (second look, F3: a short link to an attachment, a frame of a PDF).
    A chain that lands on a Drive item's metadata row (kind0 drive) comes to what the item came to (_drive_settled:
    its content row's result, never the metadata's own 200), and to_google says it landed on a row that is no page
    of its own (Google's API, a form): a page sent there is judged by its own entries, which the crawler writes from
    the Google step (review 10-26 phase 3, 【Claude 的执行办法】).
    press, pressed, rests, r00: each needed resource of a page (§7.1), a replaced press counted by the newest press of
    its row (§7.5 step 9), a press answered with a redirect by the url row of its Location (link verdicts from the
    list page). A resource with no row and no press is what its why says (§7.1): round2:<why> waits for the next round
    (a click nobody can read, a writing the platform file leaves for round two, a Google document published to the
    web), unhandled:<why> is a writing nobody has decided about (要人看), out:<rule> a rule, anything else lost. A Drive
    item whose metadata was read and whose content row was never made is lost too (§7.6), however well the metadata
    came. A resource expected to be a file (its role is a file's or an image's, or a row along its redirects was
    expected to be one) that rests on a web page that came back, or where a rule fetches nothing (_off_file), is
    not_file (review 10-26 B5; second look, F3). via is the url row the page's entry rests on first (the one that
    redirects, for a redirected resource). One of those whose redirects end on a school host's home page is gone
    instead, gone_why gone_home (GONE_HOME, 11-03): the school retired the address.
    r01, mk, r0: a person's school_side mark on the row a resource rests on first (review_mark on a redirecting
    address: the school's link sends its attachment home, review 10-26 B7 and second look F5) makes it school_side
    while that row is as it was when marked and the resource comes to what was marked; the mark covers the other
    entries of the page that land on the same row with the same result (mk: the crawler's §7.1 copy for the redirect's
    target). Only a result a person may mark is looked up (10-26, an Apple M2, 60k url rows and 420k resources: the
    marks step adds about 0.3 s to page_completeness, 2.5 s to 2.8 s; a GROUP BY of every resource had added 1.1 s). r0 counts each resource once by the row it rests on (the original
    and a copied row are one resource)."""
    url_res, act_res, jpj = url_result_case, action_result_case, js_page_join
    file_like, can_mark = _sql_list(FILE_LIKE_ROLES), _sql_list(SCHOOL_SIDE_RESULTS)
    # a resource expected to be a file or a picture whose address redirects to a school host's home page (GONE_HOME)
    home_file = f"coalesce(mv.home, 0) AND (rests.role IN {file_like} OR coalesce(mv.exp, 0))"

    def expect(x: str) -> str:
        return f"coalesce({x}.kind0, {x}.kind) IN {_sql_list(_EXPECTED_FILE)}"

    return f"""
WITH RECURSIVE
hop(id, src, at, n, exp) AS (
  SELECT u.id, u.id, u.final_url, 1, {expect('u')}
  FROM url u LEFT JOIN page_type pt ON pt.key = u.page_type {jpj('jp', 'u')}
  WHERE {url_res('u', 'pt', 'jp')} = 'redirect'
  UNION ALL
  SELECT h.id, t.id, t.final_url, h.n + 1, h.exp OR {expect('t')}
  FROM hop h JOIN url t ON t.url = h.at LEFT JOIN page_type tpt ON tpt.key = t.page_type {jpj('tjp', 't')}
  WHERE h.n < {MAX_HOPS} AND {url_res('t', 'tpt', 'tjp')} = 'redirect'),
landing0 AS (
  SELECT e.id, e.exp, t.id AS to_id, t.kind AS to_kind, t.tier AS to_tier, coalesce(t.kind0, t.kind) AS to_kind0,
         t.host AS to_host, {url_res('t', 'tpt', 'tjp')} AS to_own, l.verdict, {_school_home_sql('e.at')} AS home
  FROM (SELECT id, src, at, exp, max(n) FROM hop GROUP BY id) e
  LEFT JOIN url t ON t.url = e.at LEFT JOIN page_type tpt ON tpt.key = t.page_type {jpj('tjp', 't')}
  LEFT JOIN link l ON l.src = e.src AND l.dst = e.at),
landing AS (
  SELECT id, exp, to_id, to_kind, to_tier, verdict, home,
         coalesce(to_id IS NOT NULL AND (to_host IN {_sql_list(API_HOSTS)}
                                         OR to_kind0 IN {_sql_list(GOOGLE_LANDING_KIND0)}), 0) AS to_google,
         CASE WHEN to_kind0 = 'drive' AND to_own = 'ok' THEN {_drive_settled('landing0.to_id')}
              ELSE to_own END AS to_result
  FROM landing0),
moved AS (
  SELECT id, to_id, to_kind, to_tier, to_google, exp, verdict, home,
         to_id IS NOT NULL AND to_result <> 'redirect' AS landed,
         CASE WHEN to_id IS NULL THEN {_kept_out('verdict')}
              WHEN to_result = 'redirect' THEN 'server_error'
              ELSE to_result END AS result
  FROM landing),
press AS (
  SELECT pr.page_id, pr.res_key, pr.role, pr.need, pr.why, pr.res_id, pr.action_id,
         CASE WHEN a.state = 'superseded' THEN
                   (SELECT s.id FROM action s WHERE s.list_id = a.list_id AND s.kind = a.kind
                    AND s.row_key = a.row_key ORDER BY s.epoch DESC LIMIT 1)
              ELSE a.id END AS act_id
  FROM page_res pr LEFT JOIN action a ON a.id = pr.action_id
  WHERE pr.need <> 'no'),
pressed AS (
  SELECT press.*, b.state AS act_state, b.list_id AS act_list, b.location AS act_to, {act_res('b')} AS act_result
  FROM press LEFT JOIN action b ON b.id = press.act_id),
rests AS (
  SELECT pressed.*,
         CASE WHEN action_id IS NULL THEN res_id
              WHEN act_result = 'redirect' THEN (SELECT x.id FROM url x WHERE x.url = act_to) END AS url_id
  FROM pressed),
r00 AS (
  SELECT rests.page_id, rests.res_key, rests.role,
         CASE WHEN rests.need = 'round2' THEN 'round2' ELSE 'must' END AS need,
         CASE WHEN rests.need NOT IN ('must', 'round2') THEN 'unknown'
              WHEN rests.res_id IS NULL AND rests.action_id IS NULL AND rests.need = 'round2'
                   AND rests.why LIKE 'round2:%' THEN 'later'
              WHEN rests.res_id IS NULL AND rests.action_id IS NULL AND rests.need = 'must'
                   AND rests.why = '{UNHANDLED_GOOGLE}' THEN 'later'
              WHEN rests.res_id IS NULL AND rests.action_id IS NULL AND rests.need = 'must'
                   AND rests.why LIKE 'unhandled:%' THEN 'unhandled'
              WHEN rests.action_id IS NOT NULL AND (rests.act_id IS NULL OR rests.act_state = 'superseded')
                   THEN 'not_discovered'
              WHEN rests.action_id IS NOT NULL AND rests.act_result <> 'redirect' THEN rests.act_result
              WHEN x.id IS NOT NULL AND rests.action_id IS NULL AND rests.role = 'drive' AND x.kind0 = 'drive'
                   AND coalesce(mv.result, {url_res('x', 'xpt', 'xjp')}) = 'ok' THEN 'not_discovered'
              WHEN x.id IS NOT NULL AND {home_file}
                   AND (mv.result = 'not_file' OR (mv.landed AND mv.to_kind = 'page'
                                                    AND mv.result IN {_sql_list(PAGE_CAME_BACK)})
                        OR (mv.to_id IS NULL AND {_off_file('mv.verdict')}))
                   THEN 'gone'
              WHEN x.id IS NOT NULL AND (rests.role IN {file_like} OR coalesce(mv.exp, 0))
                   AND (((CASE WHEN mv.landed THEN mv.to_kind ELSE x.kind END) = 'page'
                         AND coalesce(mv.result, {url_res('x', 'xpt', 'xjp')}) IN {_sql_list(PAGE_CAME_BACK)})
                        OR (mv.id IS NOT NULL AND mv.to_id IS NULL AND {_off_file('mv.verdict')}))
                   THEN 'not_file'
              WHEN x.id IS NOT NULL THEN coalesce(mv.result, {url_res('x', 'xpt', 'xjp')})
              WHEN rests.action_id IS NOT NULL AND rests.role IN {file_like} AND {_off_file('lk.verdict')}
                   THEN 'not_file'
              WHEN rests.action_id IS NOT NULL THEN {_kept_out('lk.verdict')}
              WHEN rests.res_id IS NOT NULL THEN 'not_discovered'
              ELSE {_kept_out('rests.why', link=False)} END AS result,
         CASE WHEN x.id IS NOT NULL THEN 'u' || coalesce(mv.to_id, x.id)
              WHEN rests.act_id IS NOT NULL THEN 'a' || rests.act_id
              ELSE 'k' || rests.res_key END AS res,
         x.id AS via, x.id IS NOT NULL AND {home_file} AS home_file
  FROM rests
  LEFT JOIN url x ON x.id = rests.url_id LEFT JOIN page_type xpt ON xpt.key = x.page_type {jpj('xjp', 'x')}
  LEFT JOIN moved mv ON mv.id = x.id
  LEFT JOIN link lk ON lk.src = rests.act_list AND lk.dst = rests.act_to),
r01 AS (
  SELECT r00.*, CASE WHEN r00.result = 'gone' AND r00.home_file THEN '{GONE_HOME}' END AS gone_why,
         CASE WHEN r00.via IS NOT NULL AND r00.result IN {can_mark} THEN EXISTS (
           SELECT 1 FROM review_mark m JOIN url v ON v.id = m.url_id
           WHERE m.url_id = r00.via AND {_markable_sql('m')} AND m.result = r00.result
             AND m.reason IS v.reason AND m.status IS v.status AND m.fetched_at IS v.fetched_at) ELSE 0 END AS marked
  FROM r00),
mk AS MATERIALIZED (SELECT DISTINCT page_id, res, result FROM r01 WHERE marked),
r0 AS MATERIALIZED (
  SELECT DISTINCT page_id, role, need,
         CASE WHEN result IN {can_mark} AND EXISTS (SELECT 1 FROM mk WHERE mk.page_id = r01.page_id
                                                    AND mk.res = r01.res AND mk.result = r01.result)
              THEN '{SCHOOL_SIDE}' ELSE result END AS result, res
  FROM r01)"""


def _page_completeness_sql() -> str:
    """The view, in the order it is built after the resources (_resource_ctes):

    fe, r: a school frame page (a frame or iframe resource that is a round-1 page, kind page, tier 0 or 1) brings its
    own needed resources to the page that holds it, frames inside frames too (each pair once, so frames that frame
    each other end): record 二·4, 抓到了 is 正常抓到（框架里的内容也抓到了）, and the frame page's attachments and
    images are its own rows (§4.4). Each resource still counts once per page (DISTINCT on its row). This is Claude's
    way of doing it (【Claude 的执行办法】, review 10-26): §10.4 does not say it.
    pg, pj, p: the round-1 pages (tier 0, 1) but those the scope sends to round two or leaves out; a page answered
    with a redirect (a 3xx, or a script or meta refresh that sends the browser on: §8.3) is judged where it lands
    (content_id), unless that is a round-1 page judged in its own right (review note high: a 3xx page or a page held
    by a cap never drops out of the table); one that lands on Google (a Drive item, a calendar, a form: to_google) is
    judged with its own entries, what the Google step wrote on it (every file of a folder, review 10-26 phase 3). A page of a frame type, or one the browser saw keep its content in
    frames, that names no frame lacks its frames (frame_missing). p is MATERIALIZED: the
    level, the label and the reasons test its columns some thirty times, and SQLite would otherwise work each out
    again for every test.

    Cost (10-25, an Apple M2; 60k url rows, 2% of them redirects; 420k page_res rows; 20k presses): about 1.4 s for
    all pages, and as much for one (`WHERE id = ?` cannot reach inside the aggregates). The school_side marks
    (review_mark, looked up by each row's id) add about 7% (10-26, 60k rows and 420k resources: 2.2 s to 2.4 s). So no
    request handler reads this view: the dashboard's background thread works it out every 2 minutes into a snapshot
    (design §14.4), and the site pages and site_status read the snapshot."""
    url_res, jpj = url_result_case, js_page_join
    good, bad, not_judged = _sql_list(GROUPS["good"]), _sql_list(GROUPS["bad"]), _sql_list(NOT_JUDGED)
    frames = _sql_list(FRAME_ROLES)
    must = "need = 'must'"
    # a resource left for the next round that nothing accounts for (no row and no round2: why, a need nobody knows) is
    # a crawler bug like a needed one: 要人看, whatever its need
    lost_later = f"(need = 'round2' AND result IN {_sql_list(LOST)})"
    # an attachment a short link or a frame led to that is not got: 缺附件, not 缺内嵌页或按钮 (second look F3)
    carried_file = f"(role IN {_sql_list(CARRY_FILE_ROLES)} AND result = 'not_file')"
    counts = ", ".join(f"coalesce(a.{c}, 0) AS {c}" for c in _COUNTS)
    return f"""{_resource_ctes()},
fe(page_id, frame_id) AS (
  SELECT r0.page_id, fu.id
  FROM r0 JOIN url fu ON fu.id = CAST(substr(r0.res, 2) AS INTEGER)
  WHERE r0.role IN {frames} AND r0.res LIKE 'u%' AND fu.kind = 'page' AND fu.tier IN (0, 1)
  UNION
  SELECT fe.page_id, fu.id
  FROM fe JOIN r0 ON r0.page_id = fe.frame_id JOIN url fu ON fu.id = CAST(substr(r0.res, 2) AS INTEGER)
  WHERE r0.role IN {frames} AND r0.res LIKE 'u%' AND fu.kind = 'page' AND fu.tier IN (0, 1)),
r AS (
  SELECT page_id, role, need, result, res FROM r0
  UNION
  SELECT fe.page_id, r0.role, r0.need, r0.result, r0.res
  FROM fe JOIN r0 ON r0.page_id = fe.frame_id
  WHERE fe.frame_id <> fe.page_id),
a AS (
  SELECT page_id,
    sum({must}) AS n_must,
    sum({must} AND result IN {_sql_list(SCHOOL)}) AS n_school,
    sum({must} AND result = 'todo') AS n_todo,
    sum({must} AND result = 'retry') AS n_retry,
    sum({must} AND result IN {_sql_list(FAILED)}) AS n_failed,
    sum({must} AND result IN {_sql_list(GROUPS["wait"])}) AS n_wait,
    sum({must} AND result = 'unchecked') AS n_unchecked,
    sum(need = 'round2' OR result IN {_sql_list(GROUPS["round2"])}) AS n_round2,
    sum(role = 'click' AND need = 'round2') AS n_click,
    sum(role = 'img' AND need = 'round2') AS n_round2_img,
    sum(({must} AND result IN {_sql_list(REVIEW)}) OR {lost_later}) AS n_review,
    sum(({must} AND result IN {bad}) OR {lost_later}) AS n_bad,
    sum({must} AND result NOT IN {good} AND (role IN {_sql_list(FILE_ROLES)} OR {carried_file})) AS miss_file,
    sum({must} AND role = 'img' AND result NOT IN {good}) AS miss_img,
    sum({must} AND role NOT IN {_sql_list((*FILE_ROLES, "img"))} AND result NOT IN {good} AND NOT {carried_file})
      AS miss_other,
    sum(role IN {_sql_list(FRAME_ROLES)}) AS n_frames
  FROM r GROUP BY page_id),
pg AS (
  SELECT u.id, u.owner_site, u.tier, {url_res('u', 'pt', 'jp')} AS result
  FROM url u LEFT JOIN page_type pt ON pt.key = u.page_type {jpj('jp', 'u')}
  WHERE u.kind = 'page' AND u.tier IN (0, 1)),
pj AS (
  SELECT pg.id, pg.owner_site AS site, pg.tier,
         CASE WHEN pg.result = 'redirect' THEN mv.result ELSE pg.result END AS own,
         CASE WHEN mv.landed AND NOT mv.to_google THEN mv.to_id ELSE pg.id END AS content_id
  FROM pg LEFT JOIN moved mv ON mv.id = pg.id
  WHERE pg.result NOT IN {not_judged}
    AND NOT coalesce(mv.landed AND mv.to_kind = 'page' AND mv.to_tier IN (0, 1) AND mv.result NOT IN {not_judged}, 0)),
p AS MATERIALIZED (
  SELECT pj.id, pj.site, pj.tier, pj.content_id, c.page_type, c.page_role, cpt.verdict AS type_verdict, pj.own,
         {counts},
         lw.ended_by, lw.ok AS list_ok,
         coalesce(lw.ended_by = 'walking', 0) AS list_walking,
         coalesce(lw.list_id IS NOT NULL AND (lw.ended_by IS NULL OR lw.ended_by NOT IN
                  {_sql_list((*LIST_ENDS_OK, "walking"))} OR lw.ok = 0), 0) AS list_bad,
         coalesce(lw.list_id IS NULL AND c.page_role = 'list', 0) AS list_unchecked,
         coalesce((cpt.verdict = 'frame' OR cjp.verdict = 'frame') AND coalesce(a.n_frames, 0) = 0, 0)
           AS frame_missing
  FROM pj JOIN url c ON c.id = pj.content_id LEFT JOIN page_type cpt ON cpt.key = c.page_type {jpj('cjp', 'c')}
  LEFT JOIN a ON a.page_id = pj.content_id
  LEFT JOIN listwalk lw ON lw.list_id = pj.content_id)
SELECT p.*,
  {_judged_sql()}
FROM p
"""


PAGE_COMPLETENESS_SQL = _page_completeness_sql()
# each needed resource of a page as page_completeness counts it, before the copies of one resource are made one: the
# page_res entry (res_key), the row it rests on first (via) and where it ends (res), its result before a person's mark,
# whether a mark on via makes it school_side (marked), and why it is gone when the reason is its own (gone_why:
# GONE_HOME, else NULL). Frontier.mark_school_side reads what a redirecting address
# comes to here; a report of what a page lacks may too. As costly as page_completeness: never in a request handler
PAGE_RESOURCE_SQL = f"""{_resource_ctes()}
SELECT page_id, res_key, role, need, result, res, via, marked, gone_why FROM r01
"""
# built in this order (each reads the ones before it), dropped in the reverse
VIEWS = (("url_result", URL_RESULT_SQL), ("action_result", ACTION_RESULT_SQL),
         ("page_resource", PAGE_RESOURCE_SQL), ("page_completeness", PAGE_COMPLETENESS_SQL))


def create_views(conn: sqlite3.Connection, *, temp: bool = False) -> None:
    """Drop and build the views from the rules above, so a rule changed here holds at the next open. temp: for a
    read-only connection (the monitor, the dashboard): the views go to the connection's own temp schema, which is read
    before the file's, so a reader applies this version's rules even to a database a crawler of another version keeps
    open, and writes nothing to the file. (Before PRAGMA query_only, which refuses even a temp view.)"""
    schema, create = ("temp", "CREATE TEMP VIEW") if temp else ("main", "CREATE VIEW")
    script = "".join(f"DROP VIEW IF EXISTS {schema}.{name};\n" for name, _sql in reversed(VIEWS))
    script += "".join(f"{create} {name} AS {sql};\n" for name, sql in VIEWS)
    conn.executescript(script)


def self_check(conn: sqlite3.Connection) -> dict[str, int]:
    """What the rules cannot place, each zero or verify check 2 is red: the rows of a combination the rules do not
    know, per view, and the round-1 rows (tier 0, 1) that count for no site. url.owner_site is the one site a row
    counts for (design §2); url.site never stands in for it, so page_completeness lists such a page under no site."""
    out = {name: conn.execute(f"SELECT count(*) FROM {name} WHERE result = 'unknown'").fetchone()[0]
           for name in ("url_result", "action_result")}
    out["no_owner_site"] = conn.execute("SELECT count(*) FROM url WHERE owner_site IS NULL AND tier IN (0, 1)"
                                        ).fetchone()[0]
    return out


# ---------------------------------------------------------------- what the dashboard says (§10.2, §10.2.1)

RESULT_LABEL = {
    "ok": "抓到了",
    "gone": "学校那边已经不存在",
    "looks_gone": "看起来像不存在（程序比对的）",
    "api_error": "Google API 读不到（不公开或已删除，分不出）",
    "api_quota": "Google：这个档案的下载次数被用完",
    "login": "要登录，进不去",
    "redirect": "跳到别的网址（目标另算）",
    "rule": "按规则不抓",
    "later": "下一轮再抓",
    "todo": "还没抓",
    "retry": "出错了，等重试",
    "lab": "连不上，等搬到实验室",
    "js": "内容要 JavaScript（下一轮补）",
    "click": "点了才载入的内容（下一轮补）",
    "unchecked": "有要 JavaScript 的迹象，下一轮用浏览器看",
    "unresolved": "这类网页用浏览器也判定不了",
    "thin": "页面几乎是空的",
    "refused": "被拒绝（401、403、451）",
    "http_4xx": "学校回了别的错误",
    "decode": "编码有问题",
    "too_large": "档案太大，没抓",
    "failed": "试了 3 次还是失败",
    "server_error": "学校服务器出错（出错页或 5xx）",
    "not_file": "应该是档案，回来不是档案（网页、空白或错误说明）",
    "held": "撞到上限没抓",
    "unknown": "程序不认得的组合",
    "mismatch": "按到的可能是别的公告",
    "not_discovered": "页面用到了，却没有取得的纪录",
    "unhandled": "这一轮还没决定怎么处理的写法",
    "bad_link": "学校的链接坏了",
    "short_dead": "短网址已失效",
    "parked": "这个目录连续被拒两轮，剩下的网址没发请求；用 nknu archive retry denied_dir 放回",
    SCHOOL_SIDE: "学校那边的问题（人看过）",
}
# green: fetched; yellow: to look at; red: wrong; none: nothing to do (design §14.5: shown with a sign and words too).
# The colours follow the user's categories (§10.2.1, §10.5): 连接失败 (but lab) and 要人看 red; 待判定, lab, waiting for a
# retry and round two's js and click yellow
RESULT_COLOR = {
    "ok": "green", "gone": "green",
    "login": "none", "redirect": "none", "rule": "none", "later": "none", "todo": "none",
    "retry": "yellow", "lab": "yellow", "js": "yellow", "click": "yellow", "unchecked": "yellow",
    "failed": "red", "server_error": "red", "not_file": "red", "refused": "red", "http_4xx": "red",
    "held": "red", "unknown": "red", "mismatch": "red", "not_discovered": "red", "thin": "red", "decode": "red",
    "too_large": "red", "unresolved": "red", "looks_gone": "red", "api_error": "red", "api_quota": "red",
    "unhandled": "red", "parked": "red",
    "bad_link": "red", "short_dead": "red", SCHOOL_SIDE: "green",
}
# the categories the user sees instead of the codes (10-25: 「爬不到的放进连接失败」), with what happens next, in the
# order the dashboard lists them (design §10.2.1)
BUCKETS = (
    ("got", "抓到了", "—"),
    ("queued", "还在排队 / 等重试", "爬虫会自己处理"),
    ("failed", "连接失败", "列进「连接失败」清单，之后再想办法；只限校内的到实验室机器再试"),
    ("review", "要人看",
     ("抓到了却可能不全或不对，或爬虫自己停下没抓（撞上限、太大）、判定不了、程序比对像不存在、Google API 读不到、"
      "Google 档案的下载次数被用完、连续被拒两轮停下没发的目录、还没决定的写法、程序不认得的组合：要人看过再决定")),
    ("login", "要登录", "单独标出，重试没用"),
    ("gone", "学校那边已经不存在", "单独标出，重试没用"),
    (SCHOOL_SIDE, "学校那边的问题（人看过）",
     ("人看过、确认是学校那边的问题（链接坏了、短网址失效、档案不公开、主机下线……），照样列出，重试没用；"
      "结果、原因或状态码一变，标记自动作废，回到原来的分类")),
    ("pending_check", "待判定", "这一轮结束后用 jscheck 测，测完前不算完整；不是下一轮"),
    ("next_round", "下一轮", "下一轮处理，不算完整"),
    ("rule", "规则不抓", "照抓取范围决定，不是漏"),
)
_BUCKET_OF = {
    "ok": "got",
    "todo": "queued", "retry": "queued",
    # what we could not get from the server, after the retries: 连接失败, with its reason
    "failed": "failed", "lab": "failed", "server_error": "failed", "refused": "failed", "http_4xx": "failed",
    "not_file": "failed",
    "held": "review", "thin": "review", "decode": "review", "mismatch": "review", "not_discovered": "review",
    "too_large": "review", "unresolved": "review", "looks_gone": "review", "api_error": "review", "api_quota": "review",
    "unhandled": "review",
    "unknown": "review", "parked": "review",
    "bad_link": "gone", "short_dead": "gone",  # the school's (the owner 11-16 「销账照表做」)
    SCHOOL_SIDE: SCHOOL_SIDE,
    "login": "login",
    "gone": "gone",
    "unchecked": "next_round",  # a sign of JavaScript, no browser check this round (the owner 11-16)
    "later": "next_round", "js": "next_round", "click": "next_round",
    "rule": "rule", "redirect": "rule",
}


def user_bucket(result: str) -> str:
    """The user's category of a result code. A code this version does not know is 要人看, never 抓到了."""
    return _BUCKET_OF.get(result, "review")
