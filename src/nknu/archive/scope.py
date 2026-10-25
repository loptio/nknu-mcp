"""What the archive crawl may never fetch (round-1 design §4.3 layer 1, §4.5; scope record 三) and how a link becomes
one canonical URL.

judge() holds only the rules nothing overrides: login systems (by host, and by lowercase path and, for SSO's meeting
minutes, parameter, whatever the scheme or spelling; the VPN, the bank's fee site and Office 365 among them), back
offices (/admin/, /wp-admin/, /pintech_admin/, /managerx/ pages; a login page's or a back office's path off campus
too), captchas (their pictures and services, judged login: CLAUDE.md 验证码不碰), the school's internal web
services (pbws, priws and their bare IPs, *.asmx, *.svc, ?wsdl: research/2026-10-24-crawl-storage-review.md §0),
learning platforms (Moodle, cc, moocs, hp3) and other systems (Google Sites' /_/ and /feeds, the library catalogue and
its host nknulib, the library's off-campus proxy *.rpa.lib),
social platforms, platforms that refuse programs (Linktree, Dcard, Medium, Strikingly: scope record 三·14), what is not
content (maps, share buttons, trackers: record 一·1), Google Drive/Docs/Calendar pages (their APIs read those; a Google
form is read once by GET), media files (video, sound, style sheets, scripts, fonts; images are page resources now),
search pages, crawler traps, and any link a program would have to guess or post. robots.txt no longer blocks anything
(owner, 10-25): what it used to block is written out here.

Everything else is "in_scope" (school hosts, the school's 140.127.* addresses, school-account Google Sites) or off
campus ("offsite", "google_other": SOFT_OUT). Which of those this round fetches, which wait for round 2 and which page
they belong to is round1.verdict()'s call, not judge()'s.

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

# three groups (design §4.5): attachments (TIF and TIFF are mostly scanned documents), images (page resources, record
# 三·7), and media the round never downloads (video, sound, programs, style sheets, scripts, fonts, icons)
ATTACHMENT_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "pps", "ppsx", "odt", "ods", "odp", "rtf", "txt",
                  "csv", "zip", "rar", "7z", "tif", "tiff"}
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "heic"}
MEDIA_EXT = {"ico", "mp4", "mov", "avi", "wmv", "mkv", "webm", "mp3", "wav", "m4a", "flv", "swf", "iso", "exe", "msi",
             "dmg", "apk", "css", "js", "woff", "woff2", "ttf", "eot", "map"}
SCHOOL_NET = ipaddress.ip_network("140.127.0.0/16")
GOOGLE_SITES = "sites.google.com"
GOOGLE_SITES_PREFIX = "/mail.nknu.edu.tw/"
_GOOGLE_SITES_SYSTEM = re.compile(r"(?i)(?:^|/)(?:_|feeds)(?:/|$)")  # Google Sites' internal API and its old feeds
# words login and personal systems use in their paths; fetched never, linked to only
LOGIN_PATH = re.compile(r"(?i)(?:^|[/_.\-])(?:log[io]n|logon|signin|sign_in|logout|userlogin|checkcampusip|webmail|"
                        r"passport|oauth|sso_login|captcha|register|signup)(?:[/_.\-?]|$)")
# back offices robots.txt used to keep us out of (record 三·1) and each site's back office (record 三·13): a directory
# named admin, or admin joined to other words by _ or - (wp-admin, oia's and e's pintech_admin, admin_panel), Joomla's
# administrator, english's managerx (and manager), and /tg/'s Admin_*.aspx pages (review 10-26: the 10-24 crawler
# fetched pintech_admin and managerx, titled 後台管理系統 and 管理系統). Whole segments only: w3's admin.html is the
# directory of administrative units (seeds.py), ~badminton, fileadmin and lis's i-manage are content. Pages only:
# hrkm keeps files in /admin/file/ and /admin/image/, and a PDF there is content, not a back office.
ADMIN_PATH = re.compile(r"(?i)(?:^|/)(?:[a-z0-9]+[_-])*(?:admin|administrator)(?:[_-][a-z0-9]+)*(?:/|$)"
                        r"|(?:^|/)managerx?(?:/|$)|(?:^|/)tg/admin_[^/]*\.aspx$")
# what makes a captcha (CLAUDE.md: 验证码不碰; review 10-26), by the words programs name it with, anywhere in the host
# or the path: its picture or its image service (CheckCode.aspx, VerifyCode.ashx, ImgCode.aspx, captcha64_generate.php,
# code.php), and the captcha services (Google's /recaptcha/, recaptcha.net, hcaptcha.com). Judged before media and
# images: a captcha picture in a page's body is never fetched. jsread reads a script's requests with the same words.
CAPTCHA = re.compile(r"(?i)captcha|authimg|checkcode|chkcode|verifycode|validatecode|validcode|randcode|imgcode|"
                     r"securitycode|(?<!de)vcode|(?:^|/)code\.php\b")  # getVCode.ashx, never devcode
# the probe's 「要登录」 entries (research/site_probe_2026-10-23/data.json, class "login") on hosts that are otherwise
# public: lowercase path prefixes per host, matched on whole path segments, whatever the scheme. A page that turns out to
# hold a password form is archived but never followed (crawler).
LOGIN_PREFIXES = {
    "sso.nknu.edu.tw": ("/zthat", "/userlogin", "/staff", "/osasys/st", "/osasys/stuptw", "/osasys/housing",
                        "/osasys/healthcenter", "/studentprofile", "/unit/info/showmeetingfiles.aspx", "/assjob",
                        "/recruit"),
    "acc.nknu.edu.tw": ("/apswis_q",),
    GOOGLE_SITES: ("/mail.nknu.edu.tw/nknu-departmentofgeography", "/mail.nknu.edu.tw/universitydepartment"),
}
# public pages under a login prefix above: the probe's 「公開統計頁」 (報名狀況統計), and the pages the owner opened on
# 10-25 (record 三·4, 三·5): student mail pick-up, admissions sign-up's home (any classtype), the course query's home.
# The staff leave page stays behind /staff (the owner could not open it).
LOGIN_EXCEPTIONS = {"sso.nknu.edu.tw": ("/recruit/statistics", "/recruit/index.aspx", "/studentprofile/stud_mails.aspx",
                                        "/staff/courseenrollinfo/courseenrollinfo.aspx")}
# public pages under a login prefix that only one parameter value opens (record 三·5): the 專案小組 meeting minutes
# (cmeetingtype=19) are open; 校務會議 (4), 行政主管會報 (8), 行政會議 (9) and every value nobody tested stay shut
LOGIN_QUERY_EXCEPTIONS = {"sso.nknu.edu.tw": {"/unit/info/showmeetingfiles.aspx": ("cmeetingtype", frozenset({"19"}))}}
# the single sign-on portal itself, with any query and any PathInfo (/Default.aspx/x runs Default.aspx); its /News/
# pages are public
LOGIN_PAGES = {"sso.nknu.edu.tw": frozenset({"/", "/default.aspx", "/index.aspx"})}
# the VPN and the bank's fee site (record 三·1, 三·13), and Office 365's sign-in and portals (三·13; lis links its
# sign-in page: rule_lists_full.json C2), and Google's sign-in host: a form only the school's accounts may answer
# sends a visitor there (forms.gle → viewform → accounts.google.com/ServiceLogin), which is 要登录, never a rule
# (second look F2; it was never_host)
LOGIN_HOSTS = {"flowing.nknu.edu.tw", "mbo.nknu.edu.tw", "cash.nknu.edu.tw", "mail.nknu.edu.tw", "webmail.nknu.edu.tw",
               "edocap.nknu.edu.tw", "queue-management-admin.nknu.edu.tw", "ucscam.nknu.edu.tw",
               "vpn.nknu.edu.tw", "school.bot.com.tw",
               "login.microsoftonline.com", "login.live.com", "portal.office.com", "outlook.office365.com",
               "outlook.office.com", "accounts.google.com"}
# internal web services: their operations return student records and card numbers, so not even the help pages; by
# name, and by the bare IPs pages write instead (only an IP written in the URL: a name that resolves there is judged
# by its name)
INTERNAL_HOSTS = {"pbws.nknu.edu.tw", "priws.nknu.edu.tw"}
INTERNAL_IPS = {"140.127.40.135", "140.127.40.119"}
_SERVICE_PATH = re.compile(r"(?i)\.(?:asmx|svc)(?:/|$)")  # X.asmx, X.asmx?op=…, X.asmx/Operation?…
_SERVICE_QUERY = re.compile(r"(?i)(?:^|&)(?:wsdl|singlewsdl|disco)(?:=|&|$)")
# systems, not school information (2026-10-24, first full run): learning platforms (Moodle on cc and moocs: courses,
# user profiles, an endless calendar; record 三·16 adds the hosts themselves), and the library's catalogue queries
_SYSTEM_PATH = re.compile(r"(?i)(?:^|/)moodle(?:/|$)|(?:^|/)library/bookquery(?:/|$)")
# the learning platforms' hosts (record 三·16), and the library's catalogue host (record 三·19【用户同意 Claude 的建议】:
# its queries are a system, never content to retry)
SYSTEM_HOSTS = {"cc.nknu.edu.tw", "moocs.nknu.edu.tw", "hp3.nknu.edu.tw", "nknulib.nknu.edu.tw"}
# the library's off-campus proxy (record 三·19【Claude】): each database it relays is a host under this name
SYSTEM_HOST_SUFFIXES = (".rpa.lib.nknu.edu.tw",)
# a URL inside a path (a relative link written without its scheme's slashes, or with a full-width colon: ：, %EF%BC%9A):
# every page then links one level deeper into nonsense
_URL_IN_PATH = re.compile(r"(?i)https?[:：]")
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
# forms.gle is a short link now (review 10-26 A2: 125 of the round-1 pages' 161 forms are behind it): its target, a
# Google form's page, is read once by GET like any other form
NEVER_HOSTS = {"drive.google.com", "docs.google.com", "calendar.google.com"}
# the hosts whose addresses the Google step reads (google_api.parse_link, design §7.6): Drive, Docs and Calendar, and
# Calendar's old address under www.google.com/calendar/; asked before judge() and its length limit (round1.admit)
GOOGLE_LINK_HOSTS = frozenset({"drive.google.com", "docs.google.com", "calendar.google.com"})
_GOOGLE_OLD_CALENDAR = re.compile(r"(?i)^/calendar(?:/|$)")
# a Google form's page, read once by GET and never answered (record 三·8; design §7.6): viewform, closedform for a
# form that no longer takes answers, or the form's own address (/forms/d/e/ID, a slash or not), which Google redirects
# to viewform (the crawler follows a resource's redirect, design §7.1). formResponse, edit and every other
# docs.google.com page stay never_host.
_GOOGLE_FORM = re.compile(r"^/forms/(?:u/\d+/)?d/(?:e/)?[A-Za-z0-9_-]+(?:/(?:viewform|closedform))?/?$")
# not content (record 一·1 「分享按钮、地图嵌入这类不是内容的链接」; design §4.4: links only, never a part the page
# lacks): maps (Google Maps on its own hosts, under google.com's /maps, goo.gl/maps and maps.app.goo.gl), share-button
# services, and trackers (Google Tag Manager's noscript iframe, Google Analytics' pixel). Pinterest's 「Pin it」
# button (vd's WordPress, 850 links: rule_lists_full.json C2) is a share button too; social platforms' own share
# buttons are social already.
NOT_CONTENT_HOSTS = re.compile(r"(?i)(?:^|\.)(?:addtoany\.com|addthis\.com|sharethis\.com|googletagmanager\.com|"
                               r"google-analytics\.com|maps\.google\.com(?:\.tw)?|maps\.app\.goo\.gl)$")
_NOT_CONTENT_PATHS = (
    (re.compile(r"(?i)^(?:www\.)?google\.com(?:\.tw)?$|^goo\.gl$"), re.compile(r"(?i)^/maps(?:/|$)")),
    (re.compile(r"(?i)(?:^|\.)pinterest\.com$"), re.compile(r"(?i)^/pin/create(?:/|$)")),
)
# YouTube's no-cookie host serves the same embeds, and ytimg.com its thumbnails (owner 10-26, record 五·15: videos are
# links only, 不对 YouTube 发请求; links.youtube_id notes them, a thumbnail's video too)
SOCIAL = re.compile(r"(?i)(?:^|\.)(?:facebook\.com|fb\.com|fb\.me|instagram\.com|youtube\.com|youtube-nocookie\.com|"
                    r"youtu\.be|ytimg\.com|line\.me|"
                    r"twitter\.com|x\.com|threads\.net|tiktok\.com|linkedin\.com|flickr\.com|plurk\.com|lin\.ee|"
                    r"discord\.gg|discord\.com)$")
# platforms that answered our programs with 403 (record 三·14, rule_lists_full.json A3): links only, counted apart from
# social platforms in the report
REFUSES = re.compile(r"(?i)(?:^|\.)(?:linktr\.ee|dcard\.tw|medium\.com|strikingly\.com)$")
# government and other schools (design §2 「不收」), even when the school's directory links to them
INSTITUTIONS = re.compile(r"(?i)(?:^|\.)(?:gov\.tw|gov|edu\.tw)$")
# big platforms whose host is not one unit's website (a directory page's search box, fonts, sign-in)
NOT_A_SITE = re.compile(r"(?i)(?:^|\.)(?:google\.com|googleapis\.com|gstatic\.com|microsoft\.com|office\.com|live\.com|"
                        r"apple\.com|github\.com)$")
# the verdicts no request may ever get, robots.txt and probe requests included (the monitor's audit)
HARD_OUT = frozenset({"login", "internal_service", "never_host", "social", "refuses", "bad_host", "bad_url", "scheme",
                      "system", "search", "not_content"})
# off campus: not a rule against fetching. round1.verdict() places these (round 2, one layer out: record 一·1).
SOFT_OUT = frozenset({"offsite", "google_other"})
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
# attachments and images that have no file extension in their URL: SSO's download programs (design §4.5)
FILE_PATHS = frozenset({"/news/attachedfile.aspx", "/webservices/ajaxdownloadfile.aspx",
                        "/webservices/ajaxdownloadfilecourse.aspx", "/webservices/ajaxfile.aspx"})
IMAGE_PATHS = frozenset({"/webservices/ajaximagereader.aspx"})
FIRST_SEGMENT_HOSTS = frozenset({"c.nknu.edu.tw", "sso.nknu.edu.tw", "osa.nknu.edu.tw"})  # one unit per first directory
_WIX = re.compile(r"(?i)\.(?:wixsite\.com|wixstudio\.io|wixstudio\.com)$")  # several sites per host, by first segment
# short links: each one is its own "site", so a directory's short link admits that link (and, through its redirect, the
# unit's real site), never every short link on the service. forms.gle (Google Forms' own) and lurl.cc (35 on round-1
# pages) since review 10-26 A2
SHORTENERS = frozenset({"reurl.cc", "bit.ly", "tinyurl.com", "goo.gl", "t.ly", "lihi.cc", "lihi1.cc", "lihi1.com",
                        "ppt.cc", "pse.is", "shorturl.at", "is.gd", "forms.gle", "lurl.cc"})
# 「短网址服务规则表」 (review 10-26 A2): where a service's 200 page (not a 3xx) writes its target, when no meta refresh
# says it: (CSS selector, attribute). reurl.cc's 「Redirecting...」 page keeps it in <input id="target" value=…> and
# goes there by script (the old archive's reurl.cc/3Lp06M). Only a service listed here is read so; any other 200
# page with no refresh is unhandled:shortlink, for a person (round1.short_answer), never guessed.
SHORTLINK_RULES = {"reurl.cc": ("input#target", "value")}
MAX_URL = 1000
_SAFE_PATH = "/%:@!$&'()*+,;=-._~"
_SAFE_QUERY = _SAFE_PATH + "?"
# session and tracking parameters, and Wix's ?lightbox= pop-ups (the page itself again; record 三·1)
_DROP_PARAMS = {"phpsessid", "jsessionid", "aspsessionid", "utm_source", "utm_medium", "utm_campaign", "utm_term",
                "utm_content", "fbclid", "gclid", "lightbox"}
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_HOST_CHARS = re.compile(r"[a-z0-9_.-]+")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    # "in_scope", "directory_site", "form" (a Google form's page), or why not: login, internal_service, social,
    # refuses, not_content, system, search, trap, offsite, media, never_host, scheme, too_long, google_other, bad_host,
    # bad_url
    reason: str


_GOOGLE_REDIRECT_HOSTS = frozenset({"www.google.com", "google.com"})
# Facebook's link shim (review 10-26 A2: 6 on round-1 pages, every one leading to a short link): l.facebook.com, and
# lm.facebook.com, its mobile twin 【Claude 的执行办法】; /l.php?u=<target>&h=<token>
_FACEBOOK_SHIM_HOSTS = frozenset({"l.facebook.com", "lm.facebook.com"})
_WRAPS = 3  # a wrapper inside a wrapper (a Google redirect to SSO's ExternalLink) is unwrapped too


def unwrap(url: str) -> str:
    """The real target of a link wrapper, without sending anything: SSO's ExternalLink.aspx, Google's /url redirect
    and Facebook's l.php, nested ones included."""
    for _ in range(_WRAPS):
        target = _unwrap_google(url)
        target = _unwrap_facebook(target) if target == url else target
        target = _unwrap_sso(target) if target == url else target
        if target == url:
            return url
        url = target
    return url


def _unwrap_facebook(url: str) -> str:
    """Facebook's https://l.facebook.com/l.php?u=<target>&h=…: the u parameter is the target, fully escaped."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in _FACEBOOK_SHIM_HOSTS or parts.path != "/l.php":
        return url
    target = dict(parse_qsl(parts.query)).get("u") or ""
    return target if target.startswith(("http://", "https://")) else url


def _unwrap_google(url: str) -> str:
    """Google Sites wraps every outbound link as https://www.google.com/url?q=<target>&sa=D&…: the q (or url)
    parameter is the target (design §4.5, record 三·10). Google writes the target fully escaped, so it is unambiguous."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in _GOOGLE_REDIRECT_HOSTS or parts.path != "/url":
        return url
    params = dict(parse_qsl(parts.query))
    target = params.get("q") or params.get("url") or ""
    return target if target.startswith(("http://", "https://")) else url


def _unwrap_sso(url: str) -> str:
    """SSO's /News/ExternalLink.aspx?url=… (the url parameter is the target, once percent-decoded), when it is
    unambiguous: url= is the only parameter, or the target as written has no "?" of its own.
    ExternalLink.aspx?url=https://x/Page.aspx?PN=3&Sn=17 could be Page.aspx?PN=3&Sn=17 or Page.aspx?PN=3 plus a wrapper
    parameter: that wrapper is kept as it is, and its own redirect says where it goes."""
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
    """page, file or image, by the URL alone; the first bytes of what comes back decide after the fetch (sniff)."""
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return "page"
    ext = extension(url)
    if ext in IMAGE_EXT or path in IMAGE_PATHS:
        return "image"
    return "file" if ext in ATTACHMENT_EXT or path in FILE_PATHS else "page"


def is_google_form(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (parts.hostname or "").lower() == "docs.google.com" and bool(_GOOGLE_FORM.match(parts.path))


def _not_content(host: str, path: str) -> bool:
    return bool(NOT_CONTENT_HOSTS.search(host)) or any(h.search(host) and p.search(path) for h, p in _NOT_CONTENT_PATHS)


def not_content(url: str) -> bool:
    """A map, a share button or a tracker (record 一·1): a link only. goo.gl/maps is a short link too, so the short-link
    step asks this first (design §7.7)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return _not_content((parts.hostname or "").lower(), parts.path)


def is_shortlink(url: str) -> bool:
    """A short link to ask once where it leads (design §7.7): an address of a short-link service with a path of its own
    (the service's own pages are not), and no map (goo.gl/maps is a link only: not_content is asked first)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return host in SHORTENERS and any(parts.path.split("/")) and not _not_content(host, parts.path)


def is_google_link(url: str) -> bool:
    """An address the Google step reads (design §7.6, google_api.parse_link): Drive, Docs, Calendar, and Calendar's old
    address www.google.com/calendar/…."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return host in GOOGLE_LINK_HOSTS or (host in _GOOGLE_REDIRECT_HOSTS and bool(_GOOGLE_OLD_CALENDAR.match(parts.path)))


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


def is_school(host: str, path: str) -> bool:
    """A school host: nknu.edu.tw and its subdomains, the school's 140.127.* addresses, and Google Sites built with
    school accounts."""
    if host == GOOGLE_SITES:
        return path.lower().startswith(GOOGLE_SITES_PREFIX)
    return host == "nknu.edu.tw" or host.endswith(".nknu.edu.tw") or _school_ip(host)


def school_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return is_school((parts.hostname or "").lower(), parts.path)


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


def _login_url(host: str, path: str, query: str = "") -> bool:
    """A login system's page by host and path (lowercase, unescaped, repeated slashes as one, whatever the scheme),
    and for a few pages one parameter: parameter names in any case, every value of it must be one the owner opened."""
    p = re.sub(r"/{2,}", "/", unquote(path or "/").lower())
    p = p.rstrip("/") or "/"

    def under(prefix: str) -> bool:
        return p == prefix or p.startswith(prefix + "/")

    if any(p == page or (page != "/" and under(page)) for page in LOGIN_PAGES.get(host, ())):
        return True
    if any(under(pre) for pre in LOGIN_EXCEPTIONS.get(host, ())):
        return False
    if (rule := LOGIN_QUERY_EXCEPTIONS.get(host, {}).get(p)) is not None:
        name, values = rule
        got = [v.strip() for k, v in parse_qsl(query, keep_blank_values=True) if k.lower() == name]
        if got and all(v in values for v in got):
            return False
    return any(under(pre) for pre in LOGIN_PREFIXES.get(host, ()))


def _back_office(url: str, path: str) -> bool:
    """A login page by its path's words, or a back office's page (files kept in an admin folder are content)."""
    return bool(LOGIN_PATH.search(path)) or (bool(ADMIN_PATH.search(path)) and kind_of(url) == "page")


def directory_link_ok(url: str) -> bool:
    """Whether an off-campus link on an official directory page makes its site part of the crawl (design §2): an
    ordinary off-campus site or someone's Google Site, but not a government or another school's site, nor a login
    (judge() says login for those, off campus too)."""
    v = judge(url)
    if v.reason not in ("offsite", "google_other"):
        return False
    host = (urlsplit(url).hostname or "").lower()
    if host == GOOGLE_SITES or host in SHORTENERS:  # one Google Site or one short link, never the whole service
        return site_key(url) != host
    return not INSTITUTIONS.search(host) and not NOT_A_SITE.search(host)


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
    """Off campus: a login or back office is never fetched, whatever site it is on (design §4.3 layer 1: no round-2
    row either); a directory's site is crawled; the rest is round1.verdict()'s to place (SOFT_OUT)."""
    if _back_office(url, path):
        return Verdict(False, "login")
    if directory_sites and site_key(url) in directory_sites and not INSTITUTIONS.search(host):
        return Verdict(True, "directory_site")
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
    if CAPTCHA.search(host + unquote(parts.path)):  # before media and images: a captcha's picture is never fetched
        return Verdict(False, "login")
    if _not_content(host, parts.path):  # before search: www.google.com/maps/search/… is a map
        return Verdict(False, "not_content")
    if extension(url) in MEDIA_EXT or _THUMB_PATH.search(parts.path):
        return Verdict(False, "media")
    if host in SYSTEM_HOSTS or host.endswith(SYSTEM_HOST_SUFFIXES) or _SYSTEM_PATH.search(parts.path):
        return Verdict(False, "system")
    if _SEARCH_PATH.search(parts.path):
        return Verdict(False, "search")
    if pathological(parts.path) or _URL_IN_PATH.search(unquote(parts.path)):
        return Verdict(False, "trap")
    if is_google_form(url):
        return Verdict(True, "form")
    if host in NEVER_HOSTS:
        return Verdict(False, "never_host")
    if SOCIAL.search(host):
        return Verdict(False, "social")
    if REFUSES.search(host):
        return Verdict(False, "refuses")
    if (host in INTERNAL_HOSTS or host in INTERNAL_IPS or _SERVICE_PATH.search(parts.path)
            or _SERVICE_QUERY.search(parts.query)):
        return Verdict(False, "internal_service")
    if host in LOGIN_HOSTS or _login_url(host, parts.path, parts.query):
        return Verdict(False, "login")
    if host == GOOGLE_SITES:
        if _GOOGLE_SITES_SYSTEM.search(parts.path):
            return Verdict(False, "system")
        if is_school(host, parts.path):
            return Verdict(True, "in_scope")
        return _off_campus(url, parts.path, host, "google_other", directory_sites)
    if not is_school(host, parts.path):
        return _off_campus(url, parts.path, host, "offsite", directory_sites)
    if _back_office(url, parts.path):
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
