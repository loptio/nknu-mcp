"""Google Drive, Docs, Calendar and Forms for the round-1 archive crawl (round-1 design §7.6, §4.4; scope record 三·8;
review 10-26 B11, B12): the Google step of round1.admit(), and what each Google API answer makes.

The school's pages link Drive files and folders, Google documents, embedded calendars and forms. Their web pages are
never fetched (CLAUDE.md): a file, a folder and a calendar are read through Google's APIs on www.googleapis.com with the
project's key, a form's page by one plain GET (never answered).

- parse_link(url) names what a Drive, Docs or Calendar address is, before scope.judge() and its 1,000-character limit
  (an embedded calendar's address is often longer): a file (an upload, or an item whose kind only Drive can say), a
  folder, a native document (Docs, Sheets, Slides, Drawings), a form, a calendar; a document published to the web
  (/d/e/2PACX…: Drive's API cannot read it, round two: record 五·13), not content (an app's own page, no id), never
  (a form's formResponse, edit, prefill: never_host as before), or unhandled (unhandled:google, for a person).
  /u/<n>/, /b/<n>/ (accounts), /a/<domain>/ and /hosted/<domain>/ (Workspace) are taken out wherever they stand.
- step(plan) is the round1.GoogleStep the crawler plugs into admit(); uses() says every url row and page_res entry one
  address makes (an embed with three calendars makes three), and attach() the entries a page gets for one of them
  once its row exists, a Drive folder already read with all it holds.
- A Drive item is read in three kinds of rows (design §7.6): its metadata (files.get, kind0 drive), its content
  (alt=media for an upload, at most 50 MB; export for a native document: docx, xlsx, pptx, pdf for a drawing, at most
  10 MB; a form's page; for a shortcut the metadata row of its target), and for a folder its listing (files.list, one
  row per page, kind0 drive_list). The page that links the item points at the metadata row first, then (read())
  at its content; everything a folder holds is written on that page too, level by level, so the page's completeness
  sees each file. The resource keys an item was shared with go with every request for it and the folders above it
  (X-Goog-Drive-Resource-Keys).
- classify() sorts a 4xx answer by Google's error reason (design §7.6's table); target() is the request for a row:
  the key named only (Target.credential: fetch.py reads GOOGLE_API_KEY when it sends, puts it in X-Goog-Api-Key
  only, sends it only to www.googleapis.com over https, never follows a redirect with it, and the WARC hides it).
- form_parts() reads a form's page for what its description holds: attachments, Drive files, images and other forms
  count on the page that uses the form (like a Google Sites embed box, design §4.4; B12), Google's own links and
  ordinary pages are not followed (§7.6: 不跟里面的链接).

Nothing here sends a request: the crawler's googleapis queue does (QUEUE, queue_for()).
"""

from __future__ import annotations

import base64
import binascii
import email.utils
import functools
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from nknu.archive import links, outcome, round1, scope
from nknu.archive.frontier import ids_clause
from nknu.crawl.adapter import Target

API_HOST = "www.googleapis.com"
QUEUE = "googleapis"  # design §6.1: the named queue of Drive API and Calendar API requests
FORMS_QUEUE = "google"  # a form's page is Google's web host (docs.google.com), on Google's queue
CREDENTIAL = "google_api_key"  # fetch.CREDENTIALS: the key travels in X-Goog-Api-Key, read when the request is sent
KEY_ENV = "GOOGLE_API_KEY"
RESOURCE_KEYS_HEADER = "X-Goog-Drive-Resource-Keys"
DRIVE_API = f"https://{API_HOST}/drive/v3/files"
CALENDAR_API = f"https://{API_HOST}/calendar/v3/calendars"
# files.get and files.list ask for the same fields (B11: resourceKey and shortcutDetails too)
ITEM_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,resourceKey,parents,shortcutDetails"
LIST_FIELDS = f"nextPageToken,files({ITEM_FIELDS})"
LIST_PAGE_SIZE = 1000  # files.list's largest page
EVENTS_PAGE_SIZE = 2500  # events.list's largest page (the v1 calendar reads the same)

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
FORM = "application/vnd.google-apps.form"
GOOGLE_APPS = "application/vnd.google-apps."
# a native document exported as an Office file (a sheet's CSV would hold its first sheet only), a drawing as a PDF
EXPORT = {
    "application/vnd.google-apps.document":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.google-apps.presentation":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.google-apps.drawing": "application/pdf",
}
EXPORT_MAX_BYTES = 10 * 1024 * 1024  # files.export's own limit (Drive API: exported content is limited to 10 MB)
DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024  # crawler.MAX_BYTES: an upload larger than this is not downloaded (too_large)
# the caps of design §7.6: held, with the page pointing at the held row (要人看), never skipped unseen
MAX_DRIVE_DEPTH = 20  # folders below the linked one; a folder on level 21 gets its row, held (cap:drive_depth)
SITE_DRIVE_CAP = 2000  # Drive downloads (alt=media, export) per site; more are held (cap:drive)
MAX_LIST_PAGES = 50  # a folder's listing pages; the 51st is held (cap:drive_list)
MAX_CALENDAR_PAGES = 10  # events.list pages per calendar; the 11th is held (cap:calendar)
RATE_PAUSE_S = 900.0  # design §7.6: a 5xx (no block) rests the googleapis queue 15 min
MAX_PAUSE_S = 86400.0
# Google's "automated queries" page (pilot 10-26, F9: after 21 Drive downloads every alt=media answered 403 with it,
# text/html, no JSON reason): a protection mechanism, never bypassed (CLAUDE.md). Every block (that page, a quota or
# rate reason, a 429) rests the googleapis queue an hour at least (a longer Retry-After as it says), doubling while it
# goes on, at most a day (crawler._api_error), and the rows lose no attempt. Design §23 D1, 【Claude 的执行办法】
BLOCK_PAUSE_S = 3600.0
BLOCK_REASON = "automated_queries"
# the block streak, kept in frontier.db's meta so that it outlives a run (design §23 D2): how many blocks in a row and
# when the first of them came (wall-clock seconds); a download that gets through clears both. Blocked more than
# BLOCK_LONG_S since the first, or the BLOCK_LONG_N-th time in a row: an error event google_block_long, for a person
BLOCK_STREAK_KEY = "google_block:streak"
BLOCK_FIRST_KEY = "google_block:first_at"
BLOCK_LONG_N = 6
BLOCK_LONG_S = 72 * 3600.0
# downloadQuotaExceeded: one file's downloads are used up for now (Drive API 《Resolve errors》: "The sharing user has
# exceeded the download quota for this file"), not the project's: only that row waits FILE_QUOTA_WAIT_S, an attempt
# each time, MAX_FILE_QUOTA_TRIES in all, then failed (url_result api_quota, 要人看). Design §23 D1
FILE_QUOTA_REASON = "downloadQuotaExceeded"
FILE_QUOTA_WAIT_S = 86400.0
MAX_FILE_QUOTA_TRIES = 3
_SORRY_PAGE = re.compile(r"(?i)automated queries|<title>\s*Sorry\.\.\.\s*</title>")
# a key Google refuses cannot be fixed inside a running process (fetch.py reads the variable of the running process):
# the queue rests for the rest of the run, and hold_for_key() lifts that rest when the next run starts 【Claude 的执行办法】
KEY_PAUSE_S = 6 * 3600.0
KEY_PAUSE_WHY = ("api_key", "no_api_key")

# Google's error reasons (Drive API 《Resolve errors》, Calendar API 《Handle API errors》, and google.rpc.ErrorInfo
# reasons for keys and quota). Which reason does what is design §7.6's table; the sets are 【Claude 的执行办法】
KEY_REASONS = frozenset({
    "keyInvalid", "keyExpired", "ipRefererBlocked", "accessNotConfigured", "dailyLimitExceededUnreg",
    "API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED",
    "API_KEY_IP_ADDRESS_BLOCKED", "API_KEY_ANDROID_APP_BLOCKED", "API_KEY_IOS_APP_BLOCKED", "SERVICE_DISABLED",
    "CONSUMER_INVALID", "CONSUMER_SUSPENDED", "CREDENTIALS_MISSING", "BILLING_DISABLED", "USER_PROJECT_DENIED"})
QUOTA_REASONS = frozenset({
    "rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded", "sharingRateLimitExceeded",
    "quotaExceeded", "RATE_LIMIT_EXCEEDED", "RESOURCE_PROJECT_RATE_LIMIT_EXCEEDED"})
# the reasons a whole-queue block is written under (url.reason api:<…>; progress words them, §23 D3): the quota and rate
# reasons, the "automated queries" page, and what a 429 says (its google.rpc status, or none)
BLOCK_REASONS = QUOTA_REASONS | {BLOCK_REASON, "RESOURCE_EXHAUSTED", "tooManyRequests", "http_429"}
LOGIN_REASONS = frozenset({"insufficientFilePermissions", "forbidden", "authError", "required"})
TOO_LARGE_REASONS = frozenset({"exportSizeLimitExceeded"})

# design §7.6 (10-26): only a folder whose name says outright that it is an album (相簿、照片、相片、花絮、album,
# photo and their like) keeps its pictures as a list, never downloaded (record 三·7: 「相簿我觉得不需要」). A folder of
# pictures only is no album by that alone, nor one whose name says only 活動: the school puts scanned notices (公文)
# and posters there, a notice's attachments, which are downloaded (record 三·7). The words besides the design's (the
# simplified 相册、the traditional 相冊、gallery) are 【Claude 的执行办法】.
_ALBUM_NAME = re.compile(r"(?i)相簿|相冊|相册|照片|相片|花絮|活動照|活动照|合照|album|photo|gallery")
# 11-17 (design 2026-11-17-round2-batch2 §三.6; the 11-14 handover's old debt c-3): 活動照 and 合照 too (生科系's
# 02_活動照, 00_大合照); a folder the list of album folders names (ALBUM_FOLDERS_FILE) or one under it, one under a folder
# with an album's name, and one an album page links (ALBUM_LINKED_KEY) are albums as well (album_folder)
ALBUM_FOLDERS_FILE = "album_folders.json"
ALBUM_LINKED_KEY = "drive:album_linked"  # meta: the Drive folders an album page links (a JSON list of ids)
_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_DOC_TYPES = frozenset({"document", "spreadsheets", "presentation", "drawings"})
_FORM_TAILS = ("viewform", "closedform")
# an app's own pages, with no file in them (design §7.6 第 5 种): Drive's home and its views, an editor's home
_DRIVE_APP = frozenset({"drive", "my-drive", "home", "recent", "shared-with-me", "starred", "trash", "computers",
                        "quota", "settings", "storage", "search", "priority", "shared-drives", "folders"})
_DOCS_APP = frozenset({*_DOC_TYPES, "forms", "create", "spreadsheet", "presentation", "document"})
_CALENDAR_APP = frozenset({"calendar", "r", "render", "day", "week", "month", "year", "agenda", "settings", "custom",
                           "customday", "customweek", "schedule"})
_CALENDAR_ID = re.compile(r"^[A-Za-z0-9._%+#-]+@[A-Za-z0-9.-]+$")
# a holiday calendar's id cut at its unescaped '#' (zh-tw.taiwan#holiday@…, the part after '#' read as a fragment
# and dropped by scope.canonical): a locale, a dot, a country 【Claude 的执行办法】
_HOLIDAY_STUB = re.compile(r"^[a-z]{2,3}(?:[_-][a-z]{2,4})?\.[a-z_]+$")
GOOGLE_OWN_CALENDARS = "@group.v.calendar.google.com"  # holidays, birthdays, week numbers: Google's, a link only
_UI_IMAGE_HOSTS = re.compile(r"(?i)(?:^|\.)gstatic\.com$|^(?:www\.)?google\.com$")


# an upload whose name says a program or a disk image (outcome.installer_name): listed only, as a video (settle; gap ⑪
# of 11-08: the software pages' installers on Drive are never downloaded)
INSTALLER_WHY = "media:drive_installer"


def album_name(name: str | None) -> bool:
    """Whether a Drive folder's name says it is an album (design §7.6): its pictures are listed, not downloaded."""
    return bool(_ALBUM_NAME.search(name or ""))


@functools.lru_cache(maxsize=4)
def _album_list(path: str, mtime: float) -> frozenset[str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    folders = data.get("folders") if isinstance(data, dict) else None
    return frozenset(e["id"] for e in folders or () if isinstance(e, dict) and isinstance(e.get("id"), str))


def album_folder_ids() -> frozenset[str]:
    """The folders the list of album folders names (config/album_folders.json; empty when there is none)."""
    from nknu.settings import load_settings

    path = load_settings().config_dir / ALBUM_FOLDERS_FILE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return frozenset()
    return _album_list(str(path), mtime)


def _album_linked(frontier) -> set[str]:
    raw = frontier.meta(ALBUM_LINKED_KEY)
    try:
        got = json.loads(raw) if raw else []
    except ValueError:
        return set()
    return {x for x in got if isinstance(x, str)} if isinstance(got, list) else set()


def album_folder(frontier, folder_id: str) -> bool:
    """Whether a Drive folder is an album by what is above it or said of it (design 2026-11-17-round2-batch2 §三.6):
    the list of album folders names it or a folder above it, a folder above it has an album's name, or an album page
    links it or a folder above it. Its own name is album_name's. Up its parents as far as the ledger knows them, at most
    MAX_DRIVE_DEPTH levels."""
    listed = album_folder_ids() | _album_linked(frontier)
    cur: str | None = folder_id
    for level in range(MAX_DRIVE_DEPTH + 1):
        if cur is None:
            return False
        if cur in listed:
            return True
        it = _item(frontier, cur)
        if it is None:
            return False
        if level and album_name(it["name"]):
            return True
        cur = it["parent_id"]
    return False


# ---------------------------------------------------------------- what an address is

@dataclass(frozen=True)
class GoogleLink:
    """What one Drive, Docs or Calendar address is (parse_link)."""

    url: str
    # file (a Drive item: an upload, or one only files.get can say: open?id=, uc?id=), folder, doc (a native
    # document), form, calendar, published (round two), not_content, never (scope.judge says never_host), unhandled
    kind: str
    file_id: str | None = None  # file, folder, doc
    resource_key: str | None = None  # the resourcekey the link carries (a file shared by link before 2021)
    doc_type: str | None = None  # doc: document, spreadsheets, presentation, drawings
    form_url: str | None = None  # form: the form's page with no account or domain prefix (its query kept)
    calendars: tuple[str, ...] = ()  # calendar: the ids of the school's calendars it shows, decoded
    google_calendars: tuple[str, ...] = ()  # Google's own (holidays …): a link only
    bad_calendars: tuple[str, ...] = ()  # src or cid values that are no calendar id (unhandled:google)


def _segments(path: str) -> list[str]:
    """The path's segments with the account (/u/<n>/, /b/<n>/) and Workspace (/a/<domain>/, /hosted/<domain>/) parts
    taken out, wherever they stand (/file/u/1/d/…, /spreadsheets/u/0/, /drive/u/0/folders/…)."""
    segs = [s for s in path.split("/") if s]
    out: list[str] = []
    i = 0
    while i < len(segs):
        s, nxt = segs[i], segs[i + 1] if i + 1 < len(segs) else None
        if nxt is not None and ((s in ("u", "b") and nxt.isdigit()) or (s in ("a", "hosted") and "." in nxt)):
            i += 2
            continue
        out.append(s)
        i += 1
    return out


def _params(query: str) -> dict[str, list[str]]:
    got: dict[str, list[str]] = {}
    for k, v in parse_qsl(query, keep_blank_values=True):
        got.setdefault(k.lower(), []).append(v.strip())
    return got


def _first_id(params: Mapping[str, list[str]], *names: str) -> str | None:
    for name in names:
        for v in params.get(name, ()):
            if _ID.match(v):
                return v
    return None


def calendar_id(value: str) -> str | None:
    """A calendar's id as an embed's src= or a subscribe link's cid= writes it: as it is when it has an '@', else
    base64 (Google's newer embed codes, and cid=), padding or not, either alphabet. None when it is neither. A holiday
    calendar cut at its unescaped '#' comes back as Google's own (…@group.v.calendar.google.com)."""
    v = value.strip()
    if "@" in v:
        return v if _CALENDAR_ID.match(v) else None
    if _HOLIDAY_STUB.match(v):
        return f"{v}#holiday{GOOGLE_OWN_CALENDARS}"
    try:
        raw = base64.urlsafe_b64decode((v.replace("+", "-").replace("/", "_") + "=" * (-len(v) % 4)).encode("ascii"))
        decoded = raw.decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError):
        return None
    return decoded if _CALENDAR_ID.match(decoded) else None


def parse_link(url: str) -> GoogleLink:
    """What a Drive, Docs or Calendar address (scope.is_google_link) is (design §7.6). Any other address, and one
    written in a way nobody has seen yet, is unhandled: a person adds the writing here, then rejudge."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return GoogleLink(url, "unhandled")
    host = (parts.hostname or "").lower()
    segs, params = _segments(parts.path), _params(parts.query)
    key = next(iter(params.get("resourcekey", ())), None) or None
    if host == "drive.google.com":
        return _drive(url, segs, params, key)
    if host in scope.LH3_HOSTS:  # a Drive file's preview picture (pilot 10-26, F2): the file itself
        m = scope.LH3_DRIVE.match(parts.path)
        return GoogleLink(url, "file", m.group(1), key) if m else GoogleLink(url, "unhandled")
    if host == "docs.google.com":
        return _docs(url, parts, segs, params, key)
    if host == "calendar.google.com" or (host in ("www.google.com", "google.com") and segs[:1] == ["calendar"]):
        return _calendar(url, segs, params)
    return GoogleLink(url, "unhandled")


def _drive(url: str, segs: list[str], params: dict, key: str | None) -> GoogleLink:
    if len(segs) >= 3 and segs[:2] == ["file", "d"] and _ID.match(segs[2]):
        return GoogleLink(url, "file", segs[2], key)
    if segs[:1] in (["open"], ["uc"], ["thumbnail"]) and (fid := _first_id(params, "id")):
        return GoogleLink(url, "file", fid, key)  # open?id= may be a folder: files.get says
    if segs[:2] in (["drive", "folders"], ["drive", "mobile"]) and "folders" in segs:  # drive/(mobile/)folders/<id>
        i = segs.index("folders")
        if i + 1 < len(segs) and _ID.match(segs[i + 1]):
            return GoogleLink(url, "folder", segs[i + 1], key)
    if segs[:1] in (["embeddedfolderview"], ["folderview"]) and (fid := _first_id(params, "id")):
        return GoogleLink(url, "folder", fid, key)
    if not _first_id(params, "id") and all(s in _DRIVE_APP for s in segs):
        return GoogleLink(url, "not_content")
    return GoogleLink(url, "unhandled")


def _docs(url: str, parts, segs: list[str], params: dict, key: str | None) -> GoogleLink:
    if segs[:1] == ["forms"]:
        return _form(url, parts, segs)
    if len(segs) >= 4 and segs[0] in _DOC_TYPES and segs[1:3] == ["d", "e"]:
        return GoogleLink(url, "published", doc_type=segs[0])  # a publish id, never a file id (Drive cannot read it)
    if len(segs) >= 3 and segs[0] in _DOC_TYPES and segs[1] == "d" and _ID.match(segs[2]):
        return GoogleLink(url, "doc", segs[2], key, doc_type=segs[0])
    if len(segs) >= 3 and segs[:2] == ["file", "d"] and _ID.match(segs[2]):
        return GoogleLink(url, "file", segs[2], key)  # the old address of a Drive file
    if segs[:1] in (["open"], ["uc"]) and (fid := _first_id(params, "id")):
        return GoogleLink(url, "file", fid, key)
    if segs[:1] == ["folderview"] and (fid := _first_id(params, "id")):
        return GoogleLink(url, "folder", fid, key)
    if segs[:1] == ["spreadsheet"] and segs[1:2] in (["ccc"], ["pub"], ["lv"]) and (fid := _first_id(params, "key")):
        return GoogleLink(url, "doc", fid, key, doc_type="spreadsheets")  # Sheets' old addresses name the file's key
    if segs[:2] == ["document", "pub"] and (fid := _first_id(params, "id")):
        return GoogleLink(url, "doc", fid, key, doc_type="document")
    if not params.get("id") and not params.get("key") and all(s in _DOCS_APP for s in segs):
        return GoogleLink(url, "not_content")
    return GoogleLink(url, "unhandled")


def _form(url: str, parts, segs: list[str]) -> GoogleLink:
    """forms/d/e/<id>[/viewform|/closedform] or forms/d/<id>[/…]: the form's page (read once by GET, never answered);
    formResponse, edit, prefill and any other page of a form stay never_host; the Forms app itself is no content."""
    rest = segs[1:]
    if rest[:2] == ["d", "e"]:
        fid, tail, path = (rest[2] if len(rest) > 2 else None), rest[3:], "d/e"
    elif rest[:1] == ["d"]:
        fid, tail, path = (rest[1] if len(rest) > 1 else None), rest[2:], "d"
    else:
        return GoogleLink(url, "not_content" if all(s in _DOCS_APP for s in segs) else "unhandled")
    if fid is None or not _ID.match(fid):
        return GoogleLink(url, "unhandled")
    if len(tail) > 1 or (tail and tail[0] not in _FORM_TAILS):
        return GoogleLink(url, "never")
    page = tail[0] if tail else "viewform"
    form_url = f"https://docs.google.com/forms/{path}/{fid}/{page}" + (f"?{parts.query}" if parts.query else "")
    return GoogleLink(url, "form", fid, form_url=form_url)


def _calendar(url: str, segs: list[str], params: dict) -> GoogleLink:
    rest = segs[1:] if segs[:1] == ["calendar"] else segs
    if rest[:1] in (["ical"], ["feeds"]) and len(rest) >= 2:
        values = [unquote(rest[1])]
    elif rest[:1] == ["event"] or params.get("eid"):
        return GoogleLink(url, "unhandled")  # one event's link: its id is the event's, with the calendar's cut short
    else:
        values = params.get("src", []) + params.get("cid", [])
        if not values:
            return GoogleLink(url, "not_content" if all(s in _CALENDAR_APP for s in segs) else "unhandled")
        if rest[:1] not in ([], ["embed"], ["htmlembed"], ["r"], ["render"]):
            return GoogleLink(url, "unhandled")
    school, google, bad = [], [], []
    for v in values:
        cid = calendar_id(v)
        if cid is None:
            bad.append(v)
        elif cid.endswith(GOOGLE_OWN_CALENDARS):
            google.append(cid)
        else:
            school.append(cid)
    if not school and not google:
        return GoogleLink(url, "unhandled", bad_calendars=tuple(dict.fromkeys(bad)))
    return GoogleLink(url, "calendar", calendars=tuple(dict.fromkeys(school)),
                      google_calendars=tuple(dict.fromkeys(google)), bad_calendars=tuple(dict.fromkeys(bad)))


# ---------------------------------------------------------------- the API's addresses

def _quote(value: str) -> str:
    return quote(str(value), safe="")


def item_url(file_id: str) -> str:
    """files.get of one Drive item: its kind and size first (design §7.6), then its content is asked for."""
    return f"{DRIVE_API}/{file_id}?fields={_quote(ITEM_FIELDS)}&supportsAllDrives=true"


def media_url(file_id: str) -> str:
    """An uploaded file's own bytes (alt=media)."""
    return f"{DRIVE_API}/{file_id}?alt=media&supportsAllDrives=true"


def export_url(file_id: str, mime: str) -> str:
    """A native document exported (files.export: 10 MB at most)."""
    return f"{DRIVE_API}/{file_id}/export?mimeType={_quote(mime)}"


def is_download(url: str) -> bool:
    """A Drive download (an upload's bytes, alt=media, or an export): the request Google's protection watches, which
    the crawler spaces out (crawler.DRIVE_MEDIA_GAP); metadata and listings are not."""
    return "alt=media" in url or "/export?" in url


def list_url(folder_id: str, token: str | None = None) -> str:
    """One page of a folder's listing (files.list), the next page by its pageToken."""
    q = _quote(f"'{folder_id}' in parents and trashed=false")
    url = (f"{DRIVE_API}?q={q}&fields={_quote(LIST_FIELDS)}&pageSize={LIST_PAGE_SIZE}&supportsAllDrives=true"
           "&includeItemsFromAllDrives=true")
    return url + (f"&pageToken={_quote(token)}" if token else "")


def events_url(calendar: str, token: str | None = None) -> str:
    """One page of a calendar's events (events.list, every event as its own instance, in start order, no start date:
    the archive keeps all of them; design §7.6)."""
    url = (f"{CALENDAR_API}/{_quote(calendar)}/events?singleEvents=true&orderBy=startTime"
           f"&maxResults={EVENTS_PAGE_SIZE}")
    return url + (f"&pageToken={_quote(token)}" if token else "")


def form_page_url(file_id: str) -> str:
    """A form Drive lists as an item (application/vnd.google-apps.form): its page, read by GET (B11)."""
    return f"https://docs.google.com/forms/d/{file_id}/viewform"


@dataclass(frozen=True)
class ApiRef:
    """What one www.googleapis.com address asks for."""

    kind: str  # item, media, export, list, events, other
    file_id: str | None = None  # item, media, export, list (the folder)
    calendar: str | None = None
    token: str | None = None


_LIST_Q = re.compile(r"^'([A-Za-z0-9_-]+)' in parents")


def api_ref(url: str) -> ApiRef:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ApiRef("other")
    if (parts.hostname or "").lower() != API_HOST:
        return ApiRef("other")
    p, params = parts.path, dict(parse_qsl(parts.query, keep_blank_values=True))
    token = params.get("pageToken")
    if p == "/drive/v3/files":
        m = _LIST_Q.match(params.get("q", ""))
        return ApiRef("list", m[1], token=token) if m else ApiRef("other")
    if m := re.fullmatch(r"/drive/v3/files/([A-Za-z0-9_-]+)(/export)?", p):
        if m[2]:
            return ApiRef("export", m[1])
        return ApiRef("media" if params.get("alt") == "media" else "item", m[1])
    if m := re.fullmatch(r"/calendar/v3/calendars/([^/]+)/events", p):
        return ApiRef("events", calendar=unquote(m[1]), token=token)
    return ApiRef("other")


def is_api(url: str) -> bool:
    try:
        return (urlsplit(url).hostname or "").lower() == API_HOST
    except ValueError:
        return False


def queue_for(host: str) -> str | None:
    """The named queue a Google host goes on (design §6.1): the APIs' googleapis, a form's page Google's; None for any
    other host (the crawler's resolver decides)."""
    host = host.lower()
    if host == API_HOST:
        return QUEUE
    if host == "docs.google.com":
        return FORMS_QUEUE
    return None


def max_bytes(url: str) -> int:
    """The most a row's answer may bring (design §7.6): an export 10 MB, anything else 50 MB."""
    return EXPORT_MAX_BYTES if api_ref(url).kind == "export" else DOWNLOAD_MAX_BYTES


# ---------------------------------------------------------------- the key

def key_set() -> bool:
    """Whether GOOGLE_API_KEY is set (design §7.6): only that it is there; the value is never kept, shown or logged."""
    return bool(os.environ.get(KEY_ENV, "").strip())


def hold_for_key(frontier, now: float, ids: Iterable[int] | None = None) -> bool:
    """At the start of a run (design §7.6). No key: the googleapis queue rests (why no_api_key), its waiting rows say
    no_api_key, nothing counts as a failure, and an event warns. A key: a rest left by a key problem of an earlier run
    is lifted and those rows are due again (the key can only change between runs). ids: only these rows say so or are
    due again (a run given --only touches no other row; the queue's rest is the queue's). Returns whether the key is
    set."""
    by_id, id_args = ids_clause("id", ids)
    with frontier.lock:
        if key_set():
            rows = frontier.q("SELECT why FROM pause WHERE queue=?", (QUEUE,))
            if rows and (rows[0]["why"] or "").startswith(KEY_PAUSE_WHY):
                frontier.unpause(QUEUE)
            frontier.x("UPDATE url SET reason=NULL, next_at=0 WHERE host=? AND state='pending' AND reason IN (?, ?)"
                       f"{by_id}", (API_HOST, *KEY_PAUSE_WHY, *id_args))
            return True
        frontier.pause(QUEUE, now + KEY_PAUSE_S, "no_api_key")
        n = frontier.x("UPDATE url SET reason='no_api_key' WHERE host=? AND state='pending' AND "
                       f"(reason IS NULL OR reason IN (?, ?)){by_id}", (API_HOST, *KEY_PAUSE_WHY, *id_args))
        frontier.event("warn", "key_check", f"{KEY_ENV} is not set: {n} Drive and calendar rows wait (no_api_key), "
                       "none counts as a failure; set it (set -a; source …; set +a) and run again")
        return False


def target(frontier, url: str) -> Target:
    """The request for a row. On www.googleapis.com: the key by name only (fetch.py adds it as X-Goog-Api-Key when it
    sends, to that host over https only, and never follows a redirect with it), and the resource keys of the item
    and of the folders above it (B11: 子档案的 resourceKey 一路带到下载请求). Anything else: a plain GET."""
    if not is_api(url):
        return Target(url)
    ref = api_ref(url)
    headers = {}
    if ref.file_id and (keys := resource_keys(frontier, ref.file_id)):
        headers[RESOURCE_KEYS_HEADER] = keys
    return Target(url, credential=CREDENTIAL, headers=headers)


def resource_keys(frontier, file_id: str) -> str:
    """X-Goog-Drive-Resource-Keys for an item: "<id>/<key>" for it and each folder above it that has one."""
    out, seen, fid = [], set(), file_id
    while fid and fid not in seen and len(seen) <= MAX_DRIVE_DEPTH + 1:
        seen.add(fid)
        rows = frontier.q("SELECT resource_key, parent_id FROM drive_item WHERE file_id=? ORDER BY url_id LIMIT 1",
                          (fid,))
        if not rows:
            break
        if rows[0]["resource_key"]:
            out.append(f"{fid}/{rows[0]['resource_key']}")
        fid = rows[0]["parent_id"]
    return ",".join(out)


# ---------------------------------------------------------------- an error answer

@dataclass(frozen=True)
class ApiError:
    """A 4xx (or 5xx) answer of Google's API, sorted by its reason (design §7.6's table). action:
    answer: kept as answered, never retried (url_result: 404/403/410 api_error, 400 http_4xx), reason url_reason;
    login: kept, flag login (要登录: 「云端硬盘回『没有权限』」, record 二·4); too_large: skipped, too_large;
    retry: the googleapis queue rests pause_s and the row is retried as a GET (an attempt; reason url_reason), or,
    when block is set (a quota or rate answer, a 429, Google's "automated queries" page: the service's protection,
    never the URL's fault), the row waits for the rest with no attempt used and the rest grows while the block goes
    on (crawler._api_error, pilot 10-26 F9; an hour at least, §23 D1); key: the queue rests pause_s, the row back to
    pending (api_key, no attempt), an event key_check; file_quota (downloadQuotaExceeded, §23 D1): that row alone
    waits FILE_QUOTA_WAIT_S (an attempt), the queue goes on."""

    status: int
    reason: str  # Google's (errors[].reason or details[].reason), else http_<status>
    action: str
    url_reason: str
    pause_s: float | None = None
    block: bool = False


def error_reasons(body: bytes | str | None) -> list[str]:
    """Google's reasons in an error answer, in order: error.errors[].reason, then error.details[].reason (ErrorInfo),
    then error.status. None of them in a body that is no JSON error."""
    try:
        data = json.loads(body if isinstance(body, str) else (body or b"").decode("utf-8"))
    except (ValueError, UnicodeError):
        return []
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return []
    out = [e.get("reason") for e in err.get("errors") or () if isinstance(e, dict)]
    out += [d.get("reason") for d in err.get("details") or () if isinstance(d, dict)]
    out.append(err.get("status"))
    return [r for r in out if isinstance(r, str) and r]


def retry_after(headers: Mapping[str, str]) -> float | None:
    v = (headers.get("retry-after") or "").strip()
    if not v:
        return None
    if v.isdigit():
        return float(v)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(v).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def classify(status: int, body: bytes | str | None, headers: Mapping[str, str] | None = None) -> ApiError | None:
    """What a Google API answer that is no success says (design §7.6); None for a 2xx or 3xx. Never reads the key:
    Google's answer holds none, and only the reason codes go anywhere (no message text)."""
    if status < 400:
        return None
    headers = headers or {}
    reasons = error_reasons(body)
    for r in reasons:
        if r in KEY_REASONS:
            return ApiError(status, r, "key", "api_key", KEY_PAUSE_S)
    if FILE_QUOTA_REASON in reasons:  # this one file, not the project: never a block of the queue (§23 D1)
        return ApiError(status, FILE_QUOTA_REASON, "file_quota", f"api:{FILE_QUOTA_REASON}", FILE_QUOTA_WAIT_S)
    for r in reasons:
        if r in QUOTA_REASONS:
            return ApiError(status, r, "retry", f"api:{r}", block_pause(headers), block=True)
    if status in (403, 429, 503) and not reasons and _sorry_page(body, headers):
        return ApiError(status, BLOCK_REASON, "retry", f"api:{BLOCK_REASON}", block_pause(headers), block=True)
    reason = next((r for r in reasons if r[:1].islower()), None) or (reasons[0] if reasons else f"http_{status}")
    if status == 429:
        return ApiError(status, reason, "retry", f"api:{reason}", block_pause(headers), block=True)
    if status >= 500:
        return ApiError(status, reason, "retry", f"api:{reason}")
    if any(r in TOO_LARGE_REASONS for r in reasons):
        return ApiError(status, reason, "too_large", "too_large")
    if status == 401 or any(r in LOGIN_REASONS for r in reasons):
        return ApiError(status, reason, "login", f"api:{reason}")
    if status in (404, 410) and reason == f"http_{status}":
        reason = "notFound"
    return ApiError(status, reason, "answer", f"api:{reason}")


def block_pause(headers: Mapping[str, str]) -> float:
    """The first rest of a block (§23 D1): an hour at least; a Retry-After that asks longer, as it asks (a day at
    most). crawler._api_error doubles it while the block goes on."""
    wait = retry_after(headers)
    return max(min(wait, MAX_PAUSE_S), BLOCK_PAUSE_S) if wait is not None else BLOCK_PAUSE_S


def block_state(src) -> tuple[int, float | None]:
    """The block streak kept in frontier.db (§23 D2): (blocks in a row, when the first came; None with no streak).
    src: a Frontier, or a sqlite3 connection (the dashboard's read-only one)."""
    sql = "SELECT key, value FROM meta WHERE key IN (?, ?)"
    args = (BLOCK_STREAK_KEY, BLOCK_FIRST_KEY)
    rows = src.q(sql, args) if hasattr(src, "q") else src.execute(sql, args).fetchall()
    got = {r[0]: r[1] for r in rows}
    try:
        streak = int(got.get(BLOCK_STREAK_KEY) or 0)
    except ValueError:
        streak = 0
    try:
        first = float(got[BLOCK_FIRST_KEY]) if got.get(BLOCK_FIRST_KEY) else None
    except ValueError:
        first = None
    return (streak, first) if streak > 0 else (0, None)


def block_long(streak: int, first: float | None, now: float) -> bool:
    """Blocked for long (§23 D2): the BLOCK_LONG_N-th block in a row or more, or more than BLOCK_LONG_S since the
    first: a person must look (no key, address or disguise is changed to get round it)."""
    return streak >= BLOCK_LONG_N or (streak > 0 and first is not None and now - first > BLOCK_LONG_S)


def block_words(streak: int, first: float | None, now: float) -> str:
    """A long block in plain words (the event, the dashboard, verify's item 17)."""
    days = (now - first) / 86400.0 if first is not None else 0.0
    return f"Google 连续挡下载 {streak} 次、已经 {days:.1f} 天：要人看"


def sorry_page(body: bytes | str | None, headers: Mapping[str, str]) -> bool:
    """_sorry_page for the crawler's google queue (11-17): the same page on a web page's answer."""
    return _sorry_page(body, headers)


def _sorry_page(body: bytes | str | None, headers: Mapping[str, str]) -> bool:
    """Google's "We're sorry... automated queries" page: a web page (text/html) saying so, in place of the API's JSON."""
    ctype = (headers.get("content-type") or "").lower()
    if "html" not in ctype and not isinstance(body, str):
        return False
    text = body if isinstance(body, str) else (body or b"")[:4000].decode("utf-8", "replace")
    return _SORRY_PAGE.search(text) is not None


def error_cols(err: ApiError) -> dict[str, Any] | None:
    """The columns that finish a row for an answer kept as it is (answer, login, too_large), over what the crawler's
    _store writes for any 4xx; None for retry and key, which are the crawler's (_failed, a pause)."""
    if err.action == "answer":
        return {"reason": err.url_reason}
    if err.action == "login":
        return {"reason": err.url_reason, "flags": "login"}
    if err.action == "too_large":
        return {"state": "skipped", "reason": "too_large"}
    return None


# ---------------------------------------------------------------- what an address makes

@dataclass(frozen=True)
class Use:
    """One url row (or none) and the page_res entry a Google address makes on the page that uses it."""

    link: str  # the address as the page wrote it, canonical
    url: str | None  # the url row's address: an API address, a form's page; None: no row (the entry says why)
    placement: round1.Placement
    key: str  # page_res.res_key: drive:<id>, calendar:<id>:p1, a form's address, google:<link>
    how: str  # page_res.how: drive_api, calendar_api, get, unhandled, link_only
    role: str  # drive, calendar, form
    need: str  # must, round2 (a round-two row), no
    why: str | None = None
    used_as: str = "link"  # how the page uses it (round1.HOW)
    file_id: str | None = None
    resource_key: str | None = None
    calendar: str | None = None


def _placement(from_row: Mapping[str, Any] | None, link: str, kind0: str) -> round1.Placement:
    """A part of the round-1 page that uses it (design §4.4: Drive and calendars are the page's resources); on a
    round-two page, or with no page (a seed list), round two's, one layer out like any off-campus link."""
    if from_row is not None and from_row["tier"] in round1.TIER_OF_IMP.values():
        return round1.Placement("resource", None, from_row["owner_site"], from_row["tier"], 1, kind0)
    return round1.Placement("round2", "round2:offsite", scope.site_key(link), round1.ROUND2_TIER, 1, kind0)


def _none(reason: str, kind0: str) -> round1.Placement:
    return round1.Placement("out" if reason.startswith("out:") else "unhandled", reason, None, None, 0, kind0)


def uses(plan: round1.Plan, url: str, from_row: Mapping[str, Any] | None = None, how: str = "link",
         link: GoogleLink | None = None, directory_sites=()) -> list[Use]:
    """Every row and page_res entry one Google address makes (design §7.6), the first row-making one first. A form is
    placed by the rules as any form's page (round1.place); never says nothing (scope.judge's never_host stands)."""
    g = link or parse_link(url)
    if g.kind in ("file", "folder", "doc"):
        pl = _placement(from_row, url, "drive")
        return [Use(url, item_url(g.file_id), pl, f"drive:{g.file_id}", "drive_api", "drive",
                    "round2" if pl.verdict == "round2" else "must", used_as=how, file_id=g.file_id,
                    resource_key=g.resource_key)]
    if g.kind == "form":
        pl = round1.place(plan, g.form_url, from_row, how, directory_sites)
        need = "round2" if pl.verdict == "round2" else "must"
        return [Use(url, g.form_url if pl.row else None, pl, g.form_url, "get" if pl.row else "link_only", "form",
                    need, None if pl.row else pl.reason, used_as=how)]
    if g.kind == "calendar":
        out = []
        for cid in g.calendars:
            pl = _placement(from_row, url, "calendar")
            out.append(Use(url, events_url(cid), pl, f"calendar:{cid}:p1", "calendar_api", "calendar",
                           "round2" if pl.verdict == "round2" else "must", used_as=how, calendar=cid))
        for cid in g.google_calendars:
            out.append(Use(url, None, _none("out:not_content", "calendar"), f"calendar:{cid}", "link_only",
                           "calendar", "no", "out:not_content", used_as=how, calendar=cid))
        if g.bad_calendars:
            out.append(Use(url, None, _none(round1.GOOGLE_UNHANDLED, "calendar"), f"google:{url}", "unhandled",
                           "calendar", "must", round1.GOOGLE_UNHANDLED, used_as=how))
        return out
    if g.kind == "published":
        return [Use(url, None, _none("round2:google_published", "drive"), f"google:{url}", "link_only", "drive",
                    "round2", "round2:google_published", used_as=how)]
    if g.kind == "not_content":
        return [Use(url, None, _none("out:not_content", "page"), url, "link_only", "drive", "no", "out:not_content",
                    used_as=how)]
    if g.kind == "never":
        return []
    role = "calendar" if "calendar" in (urlsplit(url).hostname or "") or "/calendar" in url else "drive"
    return [Use(url, None, _none(round1.GOOGLE_UNHANDLED, "drive"), f"google:{url}", "unhandled", role, "must",
                round1.GOOGLE_UNHANDLED, used_as=how)]


def step(plan: round1.Plan, directory_sites=()) -> round1.GoogleStep:
    """The Google step for round1.admit() (Crawler.google). It answers with the first row one address makes (an
    embed's other calendars: uses()); a form's page that scope already reads as one (scope.is_google_form), and a
    form's page that is never fetched (formResponse, edit …), are left to scope.judge (None)."""

    def google(url: str, from_row: Mapping[str, Any] | None, how: str) -> round1.Admission | None:
        g = parse_link(url)
        if g.kind == "never" or (g.kind == "form" and scope.is_google_form(url)):
            return None
        got = uses(plan, url, from_row, how, g, directory_sites)
        if not got:
            return None
        first = next((u for u in got if u.url is not None), got[0])
        return round1.Admission(url, first.url or url, first.placement, "google")

    return google


# ---------------------------------------------------------------- Drive items: drive_item and what a page points at

def _entry(key: str, res_id: int | None, need: str, why: str | None, role: str, how: str = "drive_api",
           zone: str | None = None, text: str | None = None) -> dict[str, Any]:
    return {"res_key": key, "how": how, "role": role, "need": need, "why": why, "res_id": res_id, "zone": zone,
            "text": (text or None) and text[:200]}


def _item(frontier, file_id: str):
    rows = frontier.q("SELECT d.*, u.state, u.status, u.reason AS u_reason FROM drive_item d JOIN url u ON u.id = d.url_id "
                      "WHERE d.file_id=? ORDER BY d.url_id LIMIT 1", (file_id,))
    return rows[0] if rows else None


def _item_of(frontier, url_id: int):
    rows = frontier.q("SELECT d.*, u.state, u.status, u.reason AS u_reason FROM drive_item d JOIN url u ON u.id = d.url_id "
                      "WHERE d.url_id=?", (url_id,))
    return rows[0] if rows else None


def note_item(frontier, url_id: int, file_id: str, **cols) -> None:
    """The drive_item row of an item's metadata row: made when the item is first met, then filled in. A resource key
    is kept once known; depth is the shallowest the item was met at; the others take the newest value given."""
    with frontier.lock:
        frontier.x("INSERT OR IGNORE INTO drive_item(url_id, file_id) VALUES (?, ?)", (url_id, file_id))
        sets, args = [], []
        for name, value in cols.items():
            if value is None:
                continue
            if name == "resource_key":
                sets.append("resource_key = coalesce(resource_key, ?)")
            elif name == "depth":
                sets.append("depth = CASE WHEN depth IS NULL OR depth > ? THEN ? ELSE depth END")
                args.append(value)
            elif name == "parent_id":
                sets.append("parent_id = coalesce(parent_id, ?)")
            else:
                sets.append(f"{name} = ?")
            args.append(value)
        if sets:
            frontier.x(f"UPDATE drive_item SET {', '.join(sets)} WHERE url_id=?", (*args, url_id))


def settle(frontier, item, _hops: int = 0) -> tuple[int | None, str, str | None, str]:
    """Where a page's entry for one Drive item points, and with what need (design §7.6): (res_id, need, why, role).
    Not read yet (waiting, held, failed, an API error): its metadata row, need must (its own result says it); a
    round-two row: round2. Read: its content row (a form's page: role form); a shortcut: what its target comes to; a
    video or a sound: the metadata row, need no (media:drive_video, media:drive_audio: 只记档名和大小), and so an upload
    whose name says a program or a disk image (outcome.installer_name: media:drive_installer, gap ⑪ of 11-08, one
    downloaded before this rule too); a picture of
    an album: need no (album:drive); a Google type nobody can export (Sites, My Maps, Jamboard …): no row, need must,
    unhandled:drive_type (B11). Read and no content row (a crawler bug): the metadata row, which page_completeness
    counts as not_discovered."""
    meta = item["url_id"]
    if item["state"] == "later":
        return meta, "round2", None, "drive"
    if item["state"] == "skipped" and item["u_reason"] == "album:drive":
        return meta, "no", "album:drive", "drive"
    if item["state"] != "done" or item["status"] != 200 or not item["mime"]:
        return meta, "must", None, "drive"
    mime = item["mime"]
    if mime.startswith("video/"):
        return meta, "no", "media:drive_video", "drive"
    if mime.startswith("audio/"):
        return meta, "no", "media:drive_audio", "drive"
    if not mime.startswith(GOOGLE_APPS) and outcome.installer_name(item["name"]):
        return meta, "no", INSTALLER_WHY, "drive"
    if mime.startswith(GOOGLE_APPS) and mime not in (FOLDER, SHORTCUT, FORM, *EXPORT):
        return None, "must", "unhandled:drive_type", "drive"
    if item["content_id"] is None:
        return meta, "must", None, "drive"
    if mime == SHORTCUT:
        target_item = _item_of(frontier, item["content_id"])
        if target_item is None or _hops > MAX_DRIVE_DEPTH:
            return item["content_id"], "must", None, "drive"
        return settle(frontier, target_item, _hops + 1)
    return item["content_id"], "must", None, "form" if mime == FORM else "drive"


def chain(frontier, first_id: int) -> list[int]:
    """The rows of one listing or one calendar's events, page 1 first: each next page was made from the page before
    it (found_from) and asks for the same folder or calendar."""
    first = frontier.row_by_id(first_id)
    if first is None:
        return []
    ref = api_ref(first["url"])
    out, seen = [first_id], {first_id}
    while len(out) <= max(MAX_LIST_PAGES, MAX_CALENDAR_PAGES) + 1:
        nxt = None
        for r in frontier.q("SELECT id, url FROM url WHERE found_from=? AND kind0=? ORDER BY id", (out[-1],
                                                                                                first["kind0"])):
            got = api_ref(r["url"])
            if got.kind == ref.kind and got.file_id == ref.file_id and got.calendar == ref.calendar and got.token:
                nxt = r["id"]
                break
        if nxt is None or nxt in seen:
            break
        out.append(nxt)
        seen.add(nxt)
    return out


def _page_no(frontier, row) -> tuple[int, int]:
    """(the page number of a listing or events row, the row of its first page)."""
    ref, n, cur = api_ref(row["url"]), 1, row
    while ref.token is not None and n <= max(MAX_LIST_PAGES, MAX_CALENDAR_PAGES) + 2:
        prev = frontier.row_by_id(cur["found_from"])
        if prev is None or prev["kind0"] != row["kind0"]:
            break
        got = api_ref(prev["url"])
        if got.kind != ref.kind or got.file_id != ref.file_id or got.calendar != ref.calendar:
            break
        n, cur, ref = n + 1, prev, got
    return n, cur["id"]


def expand(frontier, file_id: str, *, key: str | None = None, zone: str | None = None,
           text: str | None = None) -> list[dict[str, Any]]:
    """The entries a page that uses one Drive item gets (design §7.6): the item's own (settle), and for a folder that
    was listed, each further page of its listing (drivelist:<id>:p<N>) and everything it holds, level by level
    (drive:<child id>, its name as the entry's text); a shortcut brings what its target holds. A round-two row stands
    alone."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(fid: str, k: str, z: str | None, t: str | None, level: int) -> None:
        if fid in seen or level > MAX_DRIVE_DEPTH + 1:
            return
        seen.add(fid)
        item = _item(frontier, fid)
        if item is None:
            return
        res_id, need, why, role = settle(frontier, item)
        out.append(_entry(k, res_id, need, why, role, zone=z, text=t))
        if item["state"] != "done" or item["status"] != 200 or item["content_id"] is None:
            return
        if item["mime"] == SHORTCUT:
            target_item = _item_of(frontier, item["content_id"])
            if target_item is not None:
                walk(target_item["file_id"], f"drive:{target_item['file_id']}", None, target_item["name"], level)
            return
        if item["mime"] != FOLDER:
            return
        for n, list_id in enumerate(chain(frontier, item["content_id"])[1:], start=2):
            out.append(_entry(f"drivelist:{fid}:p{n}", list_id, "must", None, "drive"))
        for child in frontier.q("SELECT file_id, name FROM drive_item WHERE parent_id=? ORDER BY url_id", (fid,)):
            walk(child["file_id"], f"drive:{child['file_id']}", None, child["name"], level + 1)

    walk(file_id, key or f"drive:{file_id}", zone, text, 0)
    return out


def _calendar_entries(frontier, calendar: str, first_id: int, need: str, zone: str | None,
                      text: str | None) -> list[dict[str, Any]]:
    ids = chain(frontier, first_id) if need != "round2" else [first_id]
    return [_entry(f"calendar:{calendar}:p{n}", rid, need, None, "calendar", "calendar_api",
                   zone if n == 1 else None, text if n == 1 else None) for n, rid in enumerate(ids, start=1)]


def _wake(frontier, url_id: int) -> None:
    """An item a page links itself is wanted however deep or in whatever album a folder held it: a row held for its
    depth, or a picture skipped as an album's, waits to be read."""
    frontier.x("UPDATE url SET state='pending', reason=NULL, next_at=0 WHERE id=? AND "
               "((state='held' AND reason='cap:drive_depth') OR (state='skipped' AND reason='album:drive'))", (url_id,))
    frontier.x("UPDATE drive_item SET album=0 WHERE url_id=? AND album=1", (url_id,))


def attach(frontier, use: Use, res_id: int | None, *, zone: str | None = None,
           text: str | None = None, album_page: bool = False) -> list[dict[str, Any]]:
    """The page_res entries the page gets for one Use, once its url row is made or found (res_id; None when it makes
    none). A Drive item's drive_item row is noted (its resource key, depth 0), and a folder already read brings all
    it holds (expand); a calendar read already, all its pages. The crawler writes them with the page's others, then
    calls resync() for the page (a reading on the googleapis queue may have come in between)."""
    if use.url is None or res_id is None:
        if use.how == "link_only" and use.used_as == "link" and use.need == "no":
            return []  # a link to an app's page or a holiday calendar: the link table holds it, the page lacks nothing
        return [_entry(use.key, None, use.need, use.why, use.role, use.how, zone, text)]
    if use.file_id is not None:
        with frontier.lock:
            note_item(frontier, res_id, use.file_id, resource_key=use.resource_key, depth=0)
            if album_page:  # an album page links it (links.album_page): a folder of it is an album (album_folder)
                linked = _album_linked(frontier)
                if use.file_id not in linked:
                    frontier.meta(ALBUM_LINKED_KEY, json.dumps(sorted(linked | {use.file_id})))
            if use.need != "round2":
                _wake(frontier, res_id)
                return expand(frontier, use.file_id, key=use.key, zone=zone, text=text)
        return [_entry(use.key, res_id, "round2", None, "drive", zone=zone, text=text)]
    if use.calendar is not None:
        with frontier.lock:
            return _calendar_entries(frontier, use.calendar, res_id, use.need, zone, text)
    return [_entry(use.key, res_id, use.need, use.why, use.role, use.how, zone, text)]


def resync(frontier, page_id: int) -> int:
    """Bring one page's Drive and calendar entries up to what is known now (idempotent): each item it uses settled
    again and expanded (a folder listed meanwhile brings its files), each calendar's pages. Existing entries keep
    their zone and text. Returns how many entries were written or changed."""
    changed = 0
    with frontier.lock:
        have = {r["res_key"]: r for r in frontier.q(
            "SELECT res_key, res_id, need, why, role FROM page_res WHERE page_id=? AND "
            "(res_key LIKE 'drive:%' OR res_key LIKE 'drivelist:%' OR res_key LIKE 'calendar:%')", (page_id,))}
        want: dict[str, dict[str, Any]] = {}
        for k in have:
            if k.startswith("drive:"):
                for e in expand(frontier, k.removeprefix("drive:")):
                    want.setdefault(e["res_key"], e)
            elif k.startswith("calendar:") and k.endswith(":p1") and have[k]["res_id"] is not None:
                cal = k.removeprefix("calendar:").removesuffix(":p1")
                need = have[k]["need"]
                for e in _calendar_entries(frontier, cal, have[k]["res_id"], need, None, None):
                    want.setdefault(e["res_key"], e)
        new = []
        for k, e in want.items():
            old = have.get(k)
            if old is None:
                new.append(e)
            elif (old["res_id"], old["need"], old["why"], old["role"]) != (e["res_id"], e["need"], e["why"],
                                                                         e["role"]):
                changed += frontier.x("UPDATE page_res SET res_id=?, need=?, why=?, role=? WHERE page_id=? AND res_key=?",
                                      (e["res_id"], e["need"], e["why"], e["role"], page_id, k))
        if new:
            frontier.add_page_res(page_id, new)
            changed += len(new)
    return changed


def _pages_using(frontier, *row_ids: int | None) -> list[int]:
    ids = [i for i in row_ids if i is not None]
    if not ids:
        return []
    return [r[0] for r in frontier.q(f"SELECT DISTINCT page_id FROM page_res WHERE res_id IN ({','.join('?' * len(ids))})",
                                     ids)]


def _repoint(frontier, meta_id: int, item) -> int:
    """Every entry that points at an item's metadata row, on any page, now points where settle() says."""
    res_id, need, why, role = settle(frontier, item)
    if (res_id, need, why) == (meta_id, "must", None):
        return 0
    return frontier.x("UPDATE page_res SET res_id=?, need=?, why=?, role=? WHERE res_id=?",
                      (res_id, need, why, role, meta_id))


# ---------------------------------------------------------------- reading a 200 answer

# (url, placement, the row it was found from, found_via) -> (the row's id, whether it is new). The crawler's:
#   e = self._enqueue(url, placement=placement, url=url, found_via=found_via, found_from=from_row["id"],
#                     depth=from_row["depth"]); return e.id, e.verdict not in ("dup", "scheme_switch")
Add = Callable[[str, round1.Placement, Mapping[str, Any], str], tuple[int | None, bool]]
_KIND_OF0 = {"drive": "drive", "drive_list": "drive", "calendar": "calendar", "file": "file", "image": "image",
             "form": "embed"}
_PRIO_OF0 = {"drive": 2, "drive_list": 2, "calendar": 2, "file": 2, "form": 2, "image": 3}


def frontier_adder(frontier, clock: Callable[[], float] = time.time) -> Add:
    """An Add that writes the row straight into the frontier (tests, tools); the crawler passes its own _enqueue."""

    def add(url: str, placement: round1.Placement, from_row: Mapping[str, Any], found_via: str):
        added = frontier.add(url, host=urlsplit(url).hostname or "", site=scope.site_key(url), unit=None,
                             tier=placement.tier, kind=_KIND_OF0.get(placement.kind0, "file"),
                             depth=from_row["depth"], found_via=found_via, found_from=from_row["id"],
                             state=placement.state or "pending", reason=placement.reason,
                             owner_site=placement.owner_site, prio=_PRIO_OF0.get(placement.kind0, 2),
                             leaf=placement.leaf, kind0=placement.kind0, added_at=clock())
        row = frontier.row(url)
        return (row["id"] if row else None), added

    return add


@dataclass
class Reading:
    """What one 200 answer of the API made (read())."""

    kind: str | None = None  # the row's kind now: drive (an item, a listing), calendar
    content: int | None = None  # the content row (export, alt=media, a form's page, a shortcut's target, a listing)
    added: list[int] = field(default_factory=list)  # rows made
    held: list[int] = field(default_factory=list)  # rows made held back by a cap (cap:drive, cap:drive_depth …)
    entries: int = 0  # page_res entries written or changed
    failed: str | None = None  # the row was failed instead (api:unreadable)


def _body(snap) -> Any:
    try:
        text = snap.text if getattr(snap, "text", None) is not None else snap.body.decode("utf-8")
        return json.loads(text)
    except (ValueError, UnicodeError, AttributeError):
        return None


def _part_of(row, kind0: str) -> round1.Placement:
    """A row the reading of `row` makes is the same page's resource (owner and tier of the row it came from)."""
    return round1.Placement("resource", None, row["owner_site"], row["tier"], 1, kind0)


def _hold(frontier, rid: int | None, new: bool, state: str, reason: str, **cols) -> bool:
    """A new row held back (or skipped) instead of waiting; an existing row keeps what it is."""
    if rid is None or not new:
        return False
    sets = ", ".join(f"{k}=?" for k in ("state", "reason", *cols))
    return frontier.x(f"UPDATE url SET {sets} WHERE id=? AND state='pending'",
                      (state, reason, *cols.values(), rid)) == 1


def drive_downloads(frontier, owner_site: str | None) -> int:
    """The Drive downloads (alt=media, export) a site will make (SITE_DRIVE_CAP)."""
    return frontier.q("SELECT count(*) FROM url WHERE owner_site IS ? AND host=? AND kind0 IN ('file', 'image') "
                      "AND state IN ('pending', 'active', 'done', 'failed')", (owner_site, API_HOST))[0][0]


def read(frontier, row, snap, add: Add) -> Reading:
    """A 200 answer of a Drive or calendar row (kind0 drive, drive_list, calendar), after the crawler stored it as
    any answer: the row's kind (drive, calendar), and what the answer makes (design §7.6). An item: its content row,
    and every page entry that pointed at the item now points at it. A listing page: a metadata row per item it holds
    (an album's pictures listed only, a folder too deep held) written on every page that uses the folder, and the
    next page's row. An events page: the next page's row. An answer that cannot be read fails the row
    (api:unreadable: retry api: tries it again), never stands as got.

    Runs on the googleapis queue's one worker. Rows are made first (add may look a host up: never under the
    frontier's lock); then, under the lock, the pages that use the item are found and their entries written, so that
    a page worker writing its entries meanwhile is either among them or, by its resync() after, sees the new rows."""
    kind0 = row["kind0"]
    if kind0 not in ("drive", "drive_list", "calendar"):
        return Reading()
    data = _body(snap)
    if kind0 == "drive":
        got = _read_item(frontier, row, data, add)
    elif kind0 == "drive_list":
        got = _read_list(frontier, row, data, add)
    else:
        got = _read_events(frontier, row, data, add)
    if got.failed:
        frontier.finish(row["id"], state="failed", reason=got.failed, attempts=row["attempts"] + 1)
    elif got.kind:
        frontier.x("UPDATE url SET kind=? WHERE id=?", (got.kind, row["id"]))
    return got


def _read_item(frontier, row, data, add: Add) -> Reading:
    ref = api_ref(row["url"])
    if ref.kind != "item" or not isinstance(data, dict) or not isinstance(data.get("mimeType"), str):
        return Reading(failed="api:unreadable")
    fid, mime, out = ref.file_id, data["mimeType"], Reading(kind="drive")
    size = data.get("size")
    size = int(size) if isinstance(size, (int, str)) and str(size).isdigit() else None
    parents = data.get("parents") if isinstance(data.get("parents"), list) else []
    note_item(frontier, row["id"], fid, name=str(data.get("name") or "")[:300] or None, mime=mime, size=size,
              md5=data.get("md5Checksum"), modified=data.get("modifiedTime"), resource_key=data.get("resourceKey"),
              parent_id=parents[0] if parents and isinstance(parents[0], str) else None)
    item = _item_of(frontier, row["id"])
    content, kind0, cap = None, None, None
    if mime == FOLDER:
        content, kind0 = list_url(fid), "drive_list"
    elif mime == SHORTCUT:
        sd = data.get("shortcutDetails") if isinstance(data.get("shortcutDetails"), dict) else {}
        tid = sd.get("targetId")
        if not isinstance(tid, str) or not _ID.match(tid):
            return Reading(failed="api:unreadable")
        content, kind0 = item_url(tid), "drive"
    elif mime == FORM:
        content, kind0 = form_page_url(fid), "form"
    elif mime in EXPORT:
        content, kind0 = export_url(fid, EXPORT[mime]), "file"
    elif mime.startswith(("video/", "audio/", GOOGLE_APPS)) or outcome.installer_name(data.get("name")):
        # listed only (a video's or a program's name and size: settle), or a Google type nobody can export
        # (unhandled:drive_type)
        pass
    else:
        content, kind0 = media_url(fid), "image" if mime.startswith("image/") else "file"
        if size is not None and size > DOWNLOAD_MAX_BYTES:
            cap = ("skipped", "too_large")
    if content is not None:
        if kind0 in ("file", "image") and cap is None and drive_downloads(frontier, row["owner_site"]) >= SITE_DRIVE_CAP:
            cap = ("held", "cap:drive")
        cid, new = add(content, _part_of(row, kind0), row, "drive")
        if cid is not None:
            out.content = cid
            if new:
                out.added.append(cid)
            if cap and _hold(frontier, cid, new, *cap, **({"size_declared": size} if cap[1] == "too_large" else {})):
                out.held.append(cid)
            frontier.x("UPDATE drive_item SET content_id=? WHERE url_id=?", (cid, row["id"]))
            if mime == SHORTCUT:
                sd = data["shortcutDetails"]
                note_item(frontier, cid, sd["targetId"], resource_key=sd.get("targetResourceKey"),
                          mime=sd.get("targetMimeType"), depth=item["depth"], parent_id=item["parent_id"])
    with frontier.lock:
        item = _item_of(frontier, row["id"])
        pages = _pages_using(frontier, row["id"])
        out.entries += _repoint(frontier, row["id"], item)
        if mime == SHORTCUT and out.content is not None:  # a target read already brings what it holds
            target_item = _item_of(frontier, out.content)
            if target_item is not None and target_item["state"] == "done":
                for page in pages:
                    out.entries += resync(frontier, page)
    return out


def _read_list(frontier, row, data, add: Add) -> Reading:
    ref = api_ref(row["url"])
    if ref.kind != "list" or not isinstance(data, dict) or not isinstance(data.get("files", []), list):
        return Reading(failed="api:unreadable")
    files = [f for f in data.get("files", []) if isinstance(f, dict)]
    if any(not isinstance(f.get("id"), str) or not _ID.match(f["id"]) or not isinstance(f.get("mimeType"), str)
           for f in files):
        return Reading(failed="api:unreadable")
    out, folder_id = Reading(kind="drive"), ref.file_id
    n, first = _page_no(frontier, row)
    folder = _item(frontier, folder_id)
    depth = (folder["depth"] or 0) if folder is not None else 0
    token = data.get("nextPageToken") if isinstance(data.get("nextPageToken"), str) else None
    name = (folder["name"] or "") if folder is not None else ""
    # an album (design §7.6, 10-26): its name says so, and nothing else makes one (a folder of pictures only is not)
    album = album_name(name) or album_folder(frontier, folder_id)
    children: list[tuple[str, str | None]] = []
    next_page: int | None = None
    for f in files:
        mime, cid_file = f["mimeType"], f["id"]
        picture = album and mime.startswith("image/")
        cid, new = add(item_url(cid_file), _part_of(row, "drive"), row, "drive")
        if cid is None:
            continue
        if new:
            out.added.append(cid)
            if picture:
                _hold(frontier, cid, new, "skipped", "album:drive")
            elif mime == FOLDER and depth + 1 > MAX_DRIVE_DEPTH and _hold(frontier, cid, new, "held",
                                                                           "cap:drive_depth"):
                out.held.append(cid)
        size = f.get("size")
        note_item(frontier, cid, cid_file, name=str(f.get("name") or "")[:300] or None, mime=mime,
                  size=int(size) if isinstance(size, (int, str)) and str(size).isdigit() else None,
                  md5=f.get("md5Checksum"), modified=f.get("modifiedTime"), resource_key=f.get("resourceKey"),
                  parent_id=folder_id, depth=depth + 1, album=int(picture) if new else None)
        children.append((cid_file, str(f.get("name") or "") or None))
    if token is not None:
        nid, new = add(list_url(folder_id, token), _part_of(row, "drive_list"), row, "drive")
        if nid is not None:
            next_page = nid
            if new:
                out.added.append(nid)
                if n + 1 > MAX_LIST_PAGES and _hold(frontier, nid, new, "held", "cap:drive_list"):
                    out.held.append(nid)
    with frontier.lock:  # every row above is made: a page found now gets them here, one writing later by resync()
        news = [e for fid, name in children for e in expand(frontier, fid, text=name)]
        if next_page is not None:
            news.append(_entry(f"drivelist:{folder_id}:p{n + 1}", next_page, "must", None, "drive"))
        pages = _pages_using(frontier, first, folder["url_id"] if folder is not None else None)
        for page in pages:
            frontier.add_page_res(page, news)
    out.entries += len(news) * len(pages)
    return out


def _read_events(frontier, row, data, add: Add) -> Reading:
    ref = api_ref(row["url"])
    if ref.kind != "events" or not isinstance(data, dict) or not isinstance(data.get("items", []), list) or (
            data.get("kind") not in (None, "calendar#events")):
        return Reading(failed="api:unreadable")
    out = Reading(kind="calendar")
    token = data.get("nextPageToken") if isinstance(data.get("nextPageToken"), str) else None
    if token is None:
        return out
    n, first = _page_no(frontier, row)
    nid, new = add(events_url(ref.calendar, token), _part_of(row, "calendar"), row, "link")
    if nid is None:
        return out
    if new:
        out.added.append(nid)
        if n + 1 > MAX_CALENDAR_PAGES and _hold(frontier, nid, new, "held", "cap:calendar"):
            out.held.append(nid)
    e = _entry(f"calendar:{ref.calendar}:p{n + 1}", nid, "must", None, "calendar", "calendar_api")
    with frontier.lock:
        pages = _pages_using(frontier, first)
        for page in pages:
            frontier.add_page_res(page, [e])
    out.entries += len(pages)
    return out


# ---------------------------------------------------------------- a form's page

def _form_part(target: str) -> bool:
    """What a form's description may hold that is the page's (B12): an attachment or a picture on any host, a short
    link, a Drive file, a document, a calendar, another form; never an ordinary page, nor Google's own pages."""
    if scope.is_shortlink(target):
        return True
    if scope.is_google_link(target):
        return parse_link(target).kind not in ("never", "not_content")
    return scope.kind_of(target) in ("file", "image")


def form_parts(text: str, url: str) -> links.PageLinks:
    """A form's page read for what counts on the page that uses the form (B12, design §7.6; like a Google Sites embed
    box, design §4.4): its attachments, Drive files, documents, other forms, pictures (Google's own interface
    pictures aside) and their words. Links Google wraps (google.com/url?q=) are unwrapped by scope.canonical as the
    crawler admits them. Ordinary links, buttons, scripts and the form's own fields are left out: the form is read
    once and never answered, nothing on it is followed. The crawler hands the result to its _parts for the page that
    uses the form, as _embed does for an embed box."""
    page = links.extract(text, url)

    def keep(href: str) -> bool:
        target = scope.canonical(href, page.base)
        return target is not None and _form_part(target)

    kept = [(h, t) for h, t in page.links if keep(h)]
    hrefs = {h for h, _t in kept}
    images = [i for i in page.images
              if not _UI_IMAGE_HOSTS.search(urlsplit(scope.canonical(i.src, page.base) or "").hostname or "")]
    return replace(page, links=kept, link_zone={h: z for h, z in page.link_zone.items() if h in hrefs},
                   frames=[f for f in page.frames if keep(f.src)], images=images, buttons=[], pagers=[],
                   unhandled=[], clicks=[], scripts=[], redirects=[], login_form=False)
