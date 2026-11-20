"""Stable record ids (plan 2026-11-08 T1; spec 2026-11-08 §一): an id is a pure function of one manifest row, so a page,
a file or an archive member keeps its id when the manifest is renumbered (rows added or removed before it), the Mac
and the lab PC compute the same ids, and no ledger is kept (research/step3_2026-11-08/research.json object 1: 918 of
2,702 attachments changed their 序号 between the 10-27 and 11-02 exports of one crawl).

A key is the identity of what a row holds; the id is one letter and 12 lowercase base32 characters of its sha256:
- location_key: anything addressed by a URL, the manifest's 网址 as the export spelt it (the export canonicalised it:
  no scope.canonical here, which would tie ids to a second copy of the crawler's rules), scheme dropped, html.unescape
  applied, fragment kept; c.nknu notices, SSO news and Drive files by the number their platform gives them.
- press_key: a row a button press brought (网址 <list url>#按钮=<words>), which has no address of its own: since the
  step-3 export (3c33a79; sub-step 4, spec 2026-11-08 §四) by the crawler's own id of the button (按钮编号, its row_key:
  a fingerprint of site, list, title, date and category, which no two rows of one list share), else, in an export made
  before, by its words.
- member_key: a file inside an archive.
The site is no part of a key (spec §一: sites have been re-assigned before; it is a column of its own). Calendar
events keep 'e' + the Google event id (documents.py), which is stable already.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from nknu.archive import profiles

ID_SCHEME = "url-sha256-60:v1"  # meta id_scheme of a build whose corpus records carry their keys
SERIAL_SCHEME = "serial"  # meta id_scheme of a build from a corpus with ids of its own (the 10-29 corpus) 【Claude】
ID_CHARS = 12  # base32 characters: 60 bits, about 1.7e-8 odds of a collision at 200k keys (research object 1, fact 21)
URL, CNKNU, SSO_NEWS, DRIVE, PRESS = "url:", "cnknu:", "sso-news:", "drive:", "press:"  # the kinds of key
PRESS_MARK = "#按钮="  # how the export writes a row a button brought: <list url>#按钮=<its first 40 characters>
_SCHEME = re.compile(r"(?i)^https?://")
_NOTICE = re.compile(r"(?i)/news(?:detail|view)\.aspx$")  # c.nknu's notice pages (profiles._CNKNU_DETAILS, Nid)
_SSO_NEWS_HOSTS = frozenset({"news.nknu.edu.tw", "sso.nknu.edu.tw"})  # one notice is on both (research object 1)
_SSO_NEWS = re.compile(r"(?i)/lastnews\.aspx$")  # profiles.SSO_NEWS's detail page, UNID
_DRIVE_HOST = "www.googleapis.com"
_DRIVE_FILE = re.compile(r"/drive/v3/files/([A-Za-z0-9_-]+)(?:/export)?/?")  # item, media and export of one file
_C_NKNU = "c.nknu.edu.tw"  # the c.nknu-template host whose first path segment names the site (aca, ccee …)
# 11-18: a covered host of its own domain may hold sections too, each numbering its notices from 1 (特教中心:
# ksped.nknu.edu.tw/resource, /ebd-asd and the root, 200 numbers in more than one of them once round two crawled the
# other sections; the other seven such hosts keep their notices at the root). A notice in a folder is that folder's,
# on any covered host; one at the root keeps the host's key. NKNU_NOTICE_FOLDERS=0 in the environment (assemble
# writes the keys and the build joins by them, so both read it there; the build's meta notice_folders says which):
# the key of before, one notice per host and number.
NOTICE_FOLDERS = os.environ.get("NKNU_NOTICE_FOLDERS", "1") != "0"


def _param(query: str, name: str) -> str:
    """The first non-blank value of the parameter name (any case), stripped; '' when there is none."""
    return next((v.strip() for k, v in parse_qsl(query, keep_blank_values=True) if k.lower() == name and v.strip()),
                "")


def location_key(url: str) -> str:
    """The identity of anything addressed by a URL. Overrides, checked in this order:
    - a c.nknu-template notice (newsdetail.aspx or newsview.aspx with an nid, on any host profiles.CNKNU covers):
      cnknu:<host>[/<first path segment, when the page sits in a folder>]:<nid>, so 教務處's nid 578 is not ccee's
      and 特教中心's /resource nid 172 is not its /ebd-asd one (notices are numbered per site, and per section of a
      host of its own domain: NOTICE_FOLDERS, 11-18), and its gi or &amp; spelling makes no second notice;
    - SSO news (lastnews.aspx with a unid, on news.nknu.edu.tw or sso.nknu.edu.tw): sso-news:<UNID in upper case>;
    - a Drive API file (www.googleapis.com/drive/v3/files/<id>, its item, media or export URL): drive:<id>;
    - anything else: url:<the address without its scheme>."""
    bare = _SCHEME.sub("", html.unescape(url or "").strip(), count=1)
    try:
        parts = urlsplit("//" + bare)
        host = (parts.hostname or "").lower()
    except ValueError:
        return URL + bare
    if _NOTICE.search(parts.path) and profiles.CNKNU.covers(host) and (nid := _param(parts.query, "nid")):
        segs = [s for s in parts.path.split("/") if s]
        site = f"{host}/{segs[0].lower()}" if len(segs) >= 2 and (host == _C_NKNU or NOTICE_FOLDERS) else host
        return f"{CNKNU}{site}:{nid}"
    if host in _SSO_NEWS_HOSTS and _SSO_NEWS.search(parts.path) and (unid := _param(parts.query, "unid")):
        return SSO_NEWS + unid.upper()
    if host == _DRIVE_HOST and (m := _DRIVE_FILE.fullmatch(parts.path)):
        return DRIVE + m.group(1)
    return URL + bare


def _squashed(words: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", words or "").split())


def press_key(list_url: str, words: str, row_key: str = "", dup: str = "") -> str:
    """A row a button press brought: press:<location_key of its list without the fragment>#<row_key, else words in
    NFKC with whitespace squashed>[#<dup>]. The export writes the crawler's row_key (按钮编号) since the step-3 export
    (3c33a79; sub-step 4, spec 2026-11-08 §四); in an export made before, the words stand in, and dup (the row's
    存档编号, internal to one frontier.db) tells apart two rows of one list with the same words: such ids may change
    between exports (AssembleReport.unstable_ids)."""
    key = f"{PRESS}{location_key(list_url.split('#', 1)[0])}#{row_key or _squashed(words)}"
    return f"{key}#{dup}" if dup else key


def member_key(archive_key: str, path: str, repeat: int = 0) -> str:
    """A file inside an archive: <the archive's key>#<its path in NFC>, then #<repeat> when the path repeats in one
    archive (repeat: the 1-based occurrence after the first; the first has none)."""
    key = f"{archive_key}#{unicodedata.normalize('NFC', path)}"
    return f"{key}#{repeat}" if repeat else key


def record_id(key: str, member: bool = False) -> str:
    """'m' (an archive member) or 'u' (anything else), then the first ID_CHARS characters of sha256(key) in lowercase
    base32. A document's doc_id is 'd' + this id."""
    digest = base64.b32encode(hashlib.sha256(key.encode("utf-8")).digest()).decode("ascii").lower()
    return ("m" if member else "u") + digest[:ID_CHARS]


def _presses(rows: list[dict]) -> dict[str, list[tuple[int, str, str, str, str]]]:
    """The rows a button brought (their 网址 holds PRESS_MARK), by their key without dup: (序号, list url, words,
    存档编号, 按钮编号) of each; words are the row's whole 链接文字, else its 标题; 按钮编号 stripped, '' in an export made
    before the step-3 export (sub-step 4, spec 2026-11-08 §四)."""
    out: dict[str, list[tuple[int, str, str, str, str]]] = {}
    for row in rows:
        url = row.get("网址") or ""
        if PRESS_MARK in url:
            list_url, words = url.split(PRESS_MARK, 1)[0], row.get("链接文字") or row.get("标题") or ""
            row_key = (row.get("按钮编号") or "").strip()
            out.setdefault(press_key(list_url, words, row_key=row_key), []).append(
                (int(row["序号"]), list_url, words, row.get("存档编号") or "", row_key))
    return out


def dup_rows(rows: list[dict]) -> set[int]:
    """序号 of the rows a button brought, keyed by their words (no 按钮编号), whose list and words another such row
    shares: their keys carry a dup, which may change between exports. Rows sharing one 按钮编号 (never seen on 11-09:
    the crawler's row_key names one row of one list) carry a dup too but are not counted here: they are keyed by what
    the crawler keeps across exports, not by their words (sub-step 4, spec 2026-11-08 §四)."""
    return {n for same in _presses(rows).values() if len(same) > 1 for n, _, _, _, row_key in same if not row_key}


def manifest_keys(rows: list[dict]) -> dict[int, str]:
    """序号 -> key for every row of one export's manifest: press_key for a row a button brought (by its 按钮编号, else
    its words; its 存档编号 as dup when another row of its list has the same key; its 序号 when it has no 存档编号,
    【Claude】), location_key of 网址 for any other row (a pager row, #第N页, has an address of its own)."""
    out = {int(row["序号"]): location_key(row.get("网址") or "") for row in rows
           if PRESS_MARK not in (row.get("网址") or "")}
    for key, same in _presses(rows).items():
        for n, list_url, words, archived, row_key in same:
            out[n] = key if len(same) == 1 else press_key(list_url, words, row_key, dup=archived or f"序号{n}")
    return out
