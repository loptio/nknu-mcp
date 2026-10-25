"""What the archive crawl may fetch (design §2 「自动收」) and how a link becomes one canonical URL.

In scope: http(s) pages under *.nknu.edu.tw or the school's 140.127.* addresses, Google Sites built with school
accounts (sites.google.com/mail.nknu.edu.tw/…), and the off-campus sites an official directory page (tier 0) links to
directly: those are recorded with the page that linked them (frontier table directory_site) and passed to judge().
Other off-campus sites go to the candidate list (confirmed weekly, phase 2), so here they are only counted. Never
fetched: login systems (by host and lowercase path, whatever the scheme or spelling), the school's internal web services
(pbws, priws, *.asmx, *.svc, ?wsdl: research/2026-10-24-crawl-storage-review.md §0), social platforms, Google
Drive/Docs/Calendar pages (the Drive and Calendar APIs read those), media files, government and other schools' sites,
and any link a program would have to guess or post.

canonical(), judge() and site_key() never raise: a link written wrong is a link skipped, never a page that fails.
"""

from __future__ import annotations

import ipaddress
import itertools
import posixpath
import re
import string
from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote, unquote_plus, urljoin, urlsplit, urlunsplit

ATTACHMENT_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "pps", "ppsx", "odt", "ods", "odp", "rtf", "txt",
                  "csv", "zip", "rar", "7z"}
MEDIA_EXT = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico", "tif", "tiff", "heic", "mp4", "mov", "avi", "wmv",
             "mkv", "webm", "mp3", "wav", "m4a", "flv", "swf", "iso", "exe", "msi", "dmg", "apk", "css", "js", "woff",
             "woff2", "ttf", "eot", "map"}
SCHOOL_NET = ipaddress.ip_network("140.127.0.0/16")
GOOGLE_SITES = "sites.google.com"
GOOGLE_SITES_PREFIX = "/mail.nknu.edu.tw/"
# words login and personal systems use in their paths; fetched never, linked to only
LOGIN_PATH = re.compile(r"(?i)(?:^|[/_.\-])(?:log[io]n|logon|signin|sign_in|logout|userlogin|checkcampusip|webmail|"
                        r"passport|oauth|sso_login|captcha|register|signup)(?:[/_.\-?]|$)")
# the probe's 「要登录」 entries (research/site_probe_2026-10-23/data.json, class "login") on hosts that are otherwise
# public: lowercase path prefixes per host, matched on whole path segments, whatever the scheme. A page that turns out to
# hold a password form is archived but never followed (crawler).
LOGIN_PREFIXES = {
    "sso.nknu.edu.tw": ("/zthat", "/userlogin", "/staff", "/expertisedefault.aspx", "/osasys/st", "/osasys/stuptw",
                        "/osasys/housing", "/osasys/healthcenter", "/studentprofile", "/unit/info/showmeetingfiles.aspx",
                        "/assjob", "/recruit"),
    "acc.nknu.edu.tw": ("/apswis_q",),
    GOOGLE_SITES: ("/mail.nknu.edu.tw/nknu-departmentofgeography", "/mail.nknu.edu.tw/universitydepartment"),
}
# public pages under a login prefix above (the probe's 「公開統計頁」: 報名狀況統計)
LOGIN_EXCEPTIONS = {"sso.nknu.edu.tw": ("/recruit/statistics",)}
# the single sign-on portal itself, with any query and any PathInfo (/Default.aspx/x runs Default.aspx); its /News/
# pages are public
LOGIN_PAGES = {"sso.nknu.edu.tw": frozenset({"/", "/default.aspx", "/index.aspx"})}
LOGIN_HOSTS = {"flowing.nknu.edu.tw", "mbo.nknu.edu.tw", "cash.nknu.edu.tw", "mail.nknu.edu.tw", "webmail.nknu.edu.tw",
               "edocap.nknu.edu.tw", "queue-management-admin.nknu.edu.tw", "ucscam.nknu.edu.tw"}
# internal web services: their operations return student records and card numbers, so not even the help pages
INTERNAL_HOSTS = {"pbws.nknu.edu.tw", "priws.nknu.edu.tw"}
_SERVICE_PATH = re.compile(r"(?i)\.(?:asmx|svc)(?:/|$)")  # X.asmx, X.asmx?op=…, X.asmx/Operation?…
_SERVICE_QUERY = re.compile(r"(?i)(?:^|&)(?:wsdl|singlewsdl|disco)(?:=|&|$)")
# systems, not school information (2026-10-24, first full run): learning platforms (Moodle on cc and moocs: courses,
# user profiles, an endless calendar)
_SYSTEM_PATH = re.compile(r"(?i)(?:^|/)moodle(?:/|$)")
# search-result pages: a search is never sent (design §3.8); a GET link to one is the same thing (OPAC /search*cht)
_SEARCH_PATH = re.compile(r"(?i)(?:^|/)search(?:[*._\-][^/]*)?(?:/|$)")
_THUMB_PATH = re.compile(r"(?i)/timthumb\.php$")  # an image resizer: every URL is an image
# crawler traps (Heritrix PathologicalPathDecideRule / TooManyPathSegmentsDecideRule, same defaults): a path segment
# repeated more than twice in a row (relative links stacking category/category/category/…), or more than 20 segments
MAX_REPEAT, MAX_SEGMENTS = 2, 20


def pathological(path: str) -> bool:
    segs = [s for s in path.split("/") if s]
    if len(segs) > MAX_SEGMENTS:
        return True
    run = 1
    for a, b in itertools.pairwise(segs):
        run = run + 1 if a.lower() == b.lower() else 1
        if run > MAX_REPEAT:
            return True
    return False
NEVER_HOSTS = {"drive.google.com", "docs.google.com", "calendar.google.com", "forms.gle", "accounts.google.com"}
SOCIAL = re.compile(r"(?i)(?:^|\.)(?:facebook\.com|fb\.com|fb\.me|instagram\.com|youtube\.com|youtu\.be|line\.me|"
                    r"twitter\.com|x\.com|threads\.net|tiktok\.com|linkedin\.com|dcard\.tw|linktr\.ee|medium\.com|"
                    r"flickr\.com|plurk\.com|lin\.ee|discord\.gg|discord\.com)$")
# government and other schools (design §2 「不收」), even when the school's directory links to them
INSTITUTIONS = re.compile(r"(?i)(?:^|\.)(?:gov\.tw|gov|edu\.tw)$")
# big platforms whose host is not one unit's website (a directory page's search box, fonts, sign-in)
NOT_A_SITE = re.compile(r"(?i)(?:^|\.)(?:google\.com|googleapis\.com|gstatic\.com|microsoft\.com|office\.com|live\.com|"
                        r"apple\.com|github\.com)$")
# the verdicts no request may ever get, robots.txt and probe requests included (the monitor's audit)
HARD_OUT = frozenset({"login", "internal_service", "never_host", "social", "bad_host", "bad_url", "scheme", "system",
                      "search"})
# IIS servers (case-insensitive paths and parameter names): the probe's ASP.NET hosts, plus any school page whose path is
# an ASP.NET one. Other servers (WordPress, Apache) may well be case-sensitive, so they keep their spelling.
IIS_HOSTS = frozenset({"c.nknu.edu.tw", "sso.nknu.edu.tw", "news.nknu.edu.tw", "osa.nknu.edu.tw", "spe.nknu.edu.tw",
                       "ksped.nknu.edu.tw", "cacd.nknu.edu.tw", "cprc.nknu.edu.tw", "fm.nknu.edu.tw", "hrkm.nknu.edu.tw",
                       "teachers.nknu.edu.tw", "tecs.nknu.edu.tw", "www.fablab.nknu.edu.tw", "pbws.nknu.edu.tw",
                       "priws.nknu.edu.tw"})
_IIS_EXT = (".aspx", ".asp", ".ashx", ".asmx", ".axd")
# the platforms' own pages, one path for a whole platform's sites: no variant cap (how many there are is bounded by what
# pages really link to), and no trap warning
PLATFORM_PATHS = frozenset({"/services/web/default.aspx", "/services/site/index.aspx", "/news/lastnews.aspx",
                            "/news/attachedfile.aspx"})
FILE_PATHS = frozenset({"/news/attachedfile.aspx"})  # attachments that have no file extension in their URL
FIRST_SEGMENT_HOSTS = frozenset({"c.nknu.edu.tw", "sso.nknu.edu.tw", "osa.nknu.edu.tw"})  # one unit per first directory
_WIX = re.compile(r"(?i)\.(?:wixsite\.com|wixstudio\.io|wixstudio\.com)$")  # several sites per host, by first segment
# short links: each one is its own "site", so a directory's short link admits that link (and, through its redirect, the
# unit's real site), never every short link on the service
SHORTENERS = frozenset({"reurl.cc", "bit.ly", "tinyurl.com", "goo.gl", "t.ly", "lihi.cc", "lihi1.cc", "lihi1.com",
                        "ppt.cc", "pse.is", "shorturl.at", "is.gd"})
MAX_URL = 1000
_SAFE_PATH = "/%:@!$&'()*+,;=-._~"
_SAFE_QUERY = _SAFE_PATH + "?"
_DROP_PARAMS = {"phpsessid", "jsessionid", "aspsessionid", "utm_source", "utm_medium", "utm_campaign", "utm_term",
                "utm_content", "fbclid", "gclid"}
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_HOST_CHARS = re.compile(r"[a-z0-9_.-]+")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    # "in_scope", "directory_site", or why not: login, internal_service, social, offsite, media, never_host, scheme,
    # too_long, google_other, bad_host, bad_url
    reason: str


def unwrap(url: str) -> str:
    """The real target of the school's link wrappers: SSO's /News/ExternalLink.aspx?url=… (the url parameter is the
    target, once percent-decoded), when it is unambiguous: url= is the only parameter, or the target as written has no
    "?" of its own. ExternalLink.aspx?url=https://x/Page.aspx?PN=3&Sn=17 could be Page.aspx?PN=3&Sn=17 or Page.aspx?PN=3
    plus a wrapper parameter: that wrapper is kept as it is, and its own redirect says where it goes."""
    parts = urlsplit(url)
    if not parts.path.lower().endswith("/externallink.aspx"):
        return url
    params = [p for p in parts.query.split("&") if p]
    targets = [v for k, _, v in (p.partition("=") for p in params) if k.lower() == "url"]
    if len(targets) != 1 or (len(params) > 1 and "?" in targets[0]):
        return url
    target = unquote_plus(targets[0])
    return target if target.startswith(("http://", "https://")) else url


_DOT_SEGMENT = re.compile(r"(?:^|/)\.{1,2}(?:/|$)")


def _escapes(s: str) -> str:
    """RFC 3986 §6.2.2: an escaped unreserved character is that character; any other escape in uppercase hex."""
    def one(m: re.Match) -> str:
        ch = chr(int(m[1], 16))
        return ch if ch in _UNRESERVED else "%" + m[1].upper()

    return _ESCAPE.sub(one, s)


def _iis_lower(s: str) -> str:
    """Lowercase for a case-insensitive server, keeping escapes in uppercase hex (s is ASCII once quoted)."""
    return _ESCAPE.sub(lambda m: "%" + m[1].upper(), s.lower())


def is_iis(host: str, path: str) -> bool:
    return host in IIS_HOSTS or (host.endswith(".nknu.edu.tw") and path.lower().endswith(_IIS_EXT))


def canonical(url: str, base: str | None = None) -> str | None:
    """One spelling per resource: joined to its base, wrappers removed, scheme and host lowercased, default port,
    fragment and session/tracking parameters dropped, empty path as "/", repeated slashes as one, escapes normalised;
    on IIS servers the path and the parameter names (not their values) lowercased. The scheme stays as written: http
    and https are two URLs, and a server that serves one of them redirects the other. None for what is not an http(s)
    URL, and for anything that cannot be parsed."""
    try:
        return _canonical(url, base)
    except Exception:  # noqa: BLE001 - a link written wrong is skipped, whatever the parser makes of it
        return None


def _canonical(url: str, base: str | None) -> str | None:
    raw = (url or "").strip()
    if not raw or raw.startswith("#"):
        return None
    joined = unwrap(urljoin(base, raw) if base else raw)
    parts = urlsplit(joined)
    port = parts.port
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if "/." in path:  # /a/./b and /a/../b resolve; keep a trailing slash
        trailing = path.endswith("/")
        path = posixpath.normpath(path) + ("/" if trailing else "")
        path = re.sub(r"/{2,}", "/", path) or "/"
    params = [p for p in parts.query.split("&") if p and p.split("=", 1)[0].lower() not in _DROP_PARAMS]
    # one spelling for characters written raw or escaped: 招生目錄 and %E6%8B%9B%E7%94%9F… are the same file
    path = _escapes(quote(path, safe=_SAFE_PATH))
    # %2e%2e becomes ".." only now: resolve dot segments again, so that the URL judged (login, robots) is the one sent
    if "/." in path:
        trailing = path.endswith("/")
        path = re.sub(r"/{2,}", "/", posixpath.normpath(path) + ("/" if trailing else "")) or "/"
    if _DOT_SEGMENT.search(path):
        return None
    params = [_escapes(quote(p, safe=_SAFE_QUERY)) for p in params]
    if is_iis(host, path):
        path = _iis_lower(path)
        params = [_iis_name(p) for p in params]
    return urlunsplit((scheme, netloc, path, "&".join(params), ""))


def _iis_name(param: str) -> str:
    """A query part on a case-insensitive server: name=value gets its name lowercased (never its value). A part
    without a value (?QmFzZTY0, ?ZmlsZUE=, ?ZmlsZQ==: a key, base64 padding included) is kept as written, since two
    such keys differing only in case may be two different pages."""
    name, _, value = param.partition("=")
    return f"{_iis_lower(name)}={value}" if value.strip("=") else param


def extension(url: str) -> str:
    try:
        last = urlsplit(url).path.rsplit("/", 1)[-1]
    except ValueError:
        return ""
    return last.rsplit(".", 1)[-1].lower() if "." in last else ""


def kind_of(url: str) -> str:
    """page or file, by the URL alone; the content type decides after the fetch."""
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return "page"
    return "file" if extension(url) in ATTACHMENT_EXT or path in FILE_PATHS else "page"


def is_platform_path(url: str) -> bool:
    try:
        return urlsplit(url).path.lower() in PLATFORM_PATHS
    except ValueError:
        return False


def _school_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host) in SCHOOL_NET
    except ValueError:
        return False


def good_host(host: str) -> bool:
    """A host name a resolver can take: labels of 1–63 characters, 253 in all (or an IP address)."""
    if not host or len(host) > 253:
        return False
    if ":" in host:
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False
    if host.isascii():
        return bool(_HOST_CHARS.fullmatch(host)) and all(0 < len(label) <= 63 for label in host.split("."))
    try:
        host.encode("idna")
        return True
    except UnicodeError:
        return False


def _login_url(host: str, path: str) -> bool:
    """A login system's page by host and path: lowercase, unescaped, repeated slashes as one, whatever the scheme."""
    p = re.sub(r"/{2,}", "/", unquote(path or "/").lower())
    p = p.rstrip("/") or "/"

    def under(prefix: str) -> bool:
        return p == prefix or p.startswith(prefix + "/")

    if any(p == page or (page != "/" and under(page)) for page in LOGIN_PAGES.get(host, ())):
        return True
    if any(under(pre) for pre in LOGIN_EXCEPTIONS.get(host, ())):
        return False
    return any(under(pre) for pre in LOGIN_PREFIXES.get(host, ()))


def directory_link_ok(url: str) -> bool:
    """Whether an off-campus link on an official directory page makes its site part of the crawl (design §2): an
    ordinary off-campus site or someone's Google Site, but not a government or another school's site, nor a login."""
    v = judge(url)
    if v.reason not in ("offsite", "google_other"):
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == GOOGLE_SITES or host in SHORTENERS:  # one Google Site or one short link, never the whole service
        return site_key(url) != host and not LOGIN_PATH.search(parts.path)
    return not INSTITUTIONS.search(host) and not NOT_A_SITE.search(host) and not LOGIN_PATH.search(parts.path)


def redirect_keeps_site(src: str, dst: str) -> bool:
    """Whether a directory site's redirect leads on to the same unit's site, which the directory then admits too: a
    short link (it stands for wherever it points), or the same name with or without "www.". Any other off-campus host
    it redirects to (a parked domain's sales page, say) is only a candidate."""
    try:
        a, b = (urlsplit(src).hostname or "").lower(), (urlsplit(dst).hostname or "").lower()
    except ValueError:
        return False
    return a in SHORTENERS or a == f"www.{b}" or b == f"www.{a}"


def _off_campus(url: str, path: str, host: str, reason: str, directory_sites: Collection[str]) -> Verdict:
    if directory_sites and site_key(url) in directory_sites and not INSTITUTIONS.search(host):
        return Verdict(False, "login") if LOGIN_PATH.search(path) else Verdict(True, "directory_site")
    return Verdict(False, reason)


def judge(url: str, directory_sites: Collection[str] = ()) -> Verdict:
    """Whether the archive crawl may fetch this canonical URL, and why not. directory_sites: the site keys an official
    directory page linked to (the crawler and the monitor pass the same set)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 - raises for a port that is not a number
    except ValueError:
        return Verdict(False, "bad_url")
    if len(url) > MAX_URL:
        return Verdict(False, "too_long")
    if parts.scheme.lower() not in ("http", "https"):
        return Verdict(False, "scheme")
    if not good_host(host):
        return Verdict(False, "bad_host")
    if extension(url) in MEDIA_EXT or _THUMB_PATH.search(parts.path):
        return Verdict(False, "media")
    if _SYSTEM_PATH.search(parts.path):
        return Verdict(False, "system")
    if _SEARCH_PATH.search(parts.path):
        return Verdict(False, "search")
    if pathological(parts.path):
        return Verdict(False, "trap")
    if host in NEVER_HOSTS:
        return Verdict(False, "never_host")
    if SOCIAL.search(host):
        return Verdict(False, "social")
    if host in INTERNAL_HOSTS or _SERVICE_PATH.search(parts.path) or _SERVICE_QUERY.search(parts.query):
        return Verdict(False, "internal_service")
    if host in LOGIN_HOSTS or _login_url(host, parts.path):
        return Verdict(False, "login")
    if host == GOOGLE_SITES:
        if parts.path.lower().startswith(GOOGLE_SITES_PREFIX):
            return Verdict(True, "in_scope")
        return _off_campus(url, parts.path, host, "google_other", directory_sites)
    if not (host == "nknu.edu.tw" or host.endswith(".nknu.edu.tw") or _school_ip(host)):
        return _off_campus(url, parts.path, host, "offsite", directory_sites)
    if LOGIN_PATH.search(parts.path):
        return Verdict(False, "login")
    return Verdict(True, "in_scope")


def site_key(url: str) -> str:
    """The site a URL belongs to, for caps, units and reports (design §3.2): SSO unit sites by webIdx and cLv2 sites by
    cLv2 (parameter names in any case), c.nknu, SSO's other parts and osa by their first path segment, the old
    www.nknu.edu.tw/~user pages by user, Google Sites by /<domain>/<site>, Wix by its first segment, a short link by
    itself, others by host."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return ""
    segs = [s for s in parts.path.split("/") if s]
    if host == GOOGLE_SITES and len(segs) >= 2:
        return f"{host}/{segs[0].lower()}/{segs[1].lower()}"
    if host == "sso.nknu.edu.tw":
        path = parts.path.lower()
        q = {k.lower(): v.strip().lower() for k, v in parse_qsl(parts.query)}
        if path.startswith("/services/web/") and (n := q.get("webidx") or q.get("cwebidx")):
            return f"{host}/web/{n}"
        if path.startswith("/services/site/") and (n := q.get("clv2")):
            return f"{host}/site/{n}"
    if host in FIRST_SEGMENT_HOSTS and segs and "." not in segs[0]:
        return f"{host}/{segs[0].lower()}"
    if host == "www.nknu.edu.tw" and segs and segs[0].startswith("~"):
        return f"{host}/{segs[0].lower()}"
    if _WIX.search(host) and segs:
        return f"{host}/{segs[0].lower()}"
    if host in SHORTENERS and segs:
        return f"{host}/{segs[0]}"
    return host
