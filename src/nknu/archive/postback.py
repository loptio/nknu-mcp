"""ASP.NET postbacks and list walks (round-1 design §7.5, §7.4 clicks, §10.3): which buttons of a stored page to
press, how to build each POST from the page as it was archived, what each answer means, and the evidence that a list
was walked to its last page (listwalk). Nothing here sends a request: the crawler sends what target_for() builds, on
its own polite queue, and hands every answer back (answered, failed).

The chain of one list (§7.5):
1. A list page arrives by GET and is archived; page_stored() reads it with its platform profile (profiles.py; the
   platform its own address is written on, profiles.profile_for: a /site/ key's pages that are WebIdx pages are read as
   WebIdx's, gaps ② ③ of 11-08; the site it counts for, url.owner_site, stays every row key's, action.site and
   listwalk.site): the rows, the row buttons to press (a detail, a download), the next page's button, the page shown,
   the 「共 N 筆」. Each
   press is one action row whose form_src points at that page's response record in the WARC, and one page_res entry
   of the list page (pb:<kind>:<row_key>) points at the action.
2. target_for() reads that very page back from the WARC and builds the form from it (webforms.postback, the form
   that holds the button: form_for_control): the form state and the row number ctlNN always come from one page. An
   ASP.NET ImageButton (<input type="image">, argument IMAGE_ARG: the software icons, gap ⑪ of 11-08) calls no
   __doPostBack: its press is the form that holds it (image_form) with the click's coordinates, name.x and name.y. A
   menu rule's press (profiles.Menu, argument MENU_ARG?<the values>: the course tables, gap ⑩ of 11-08, the owner
   11-09 「现在补 115」) is its form with the year menu set to today's academic year and one option of the other menu,
   sent by the form's own submit button (menu_form, menu_post), one press per option; its answer must show what was
   asked (menu_mismatch), and an option only an answer offers is pressed from that answer's form state.
3. A row's buttons go before the next page (the queue's order: next_action). A detail's title must begin with its
   row's (cut) title, or the press is mismatch and the chain starts again from its GET once; a detail with nothing
   in it but its title is thin (empty_answer, §10.1 nonempty); the next page's answer must show the page asked for,
   except a 「下一頁」 answer that names no page shown (c.nknu's pager past page 5 of a list longer than 15 pages),
   taken as the page asked for on what it shows (unnumbered_next, gap ⑤ of 11-08); an answer that holds no list or
   is another page ends the walk, whatever page it shows, its note saying why (answer_elsewhere).
   A button to press in an answer that no press covers is written on the list page (要人看: _unpressed). A
   redirect's Location is for the crawler to queue; a file is sniffed. A row's press sent elsewhere brought neither
   its detail nor its file (the session lapsed: §23.9 A2), except a download sent to a file on its own host, which is
   that file's address (file_redirect: WebIdx's notice files answer 302 to /WebServices/ajaxDownloadFileCourse.aspx,
   gap ① of 11-08), and a detail sent to another page of its own site that is neither the list, the site's home nor
   a sign-in, which is that detail's page (detail_redirect, gap ⑪ of 11-08): press_redirect says which. A detail's
   answer may show the button pressed again (an opened folder is still a button of its folder list): never pressed
   from that answer (_without_pressed).
4. A POST is never sent twice: a press that fails (or mismatches) starts the chain again from its GET once (a new
   epoch; rows pressed already are known by their row_key and not pressed again); failing again it stays failed, and a
   pager that fails twice ends the walk as post_failed. A stopped run's chains start again from their GET at the next
   run (resume): the actions left are superseded. `nknu archive retry post_failed` and `retry mismatch:page`
   (retry_walks) walk the lists that ended so again from their GET. `nknu archive retry download` (retry_downloads,
   §23 A3) puts the downloads that came to no file or failed back, never one a redirect sent to its file
   (file_redirects adopts that): their list's next GET presses only them (REDO_KEY). `nknu archive retry unpressed`
   (retry_unpressed) puts back the pages whose buttons today's platform file presses and nobody pressed, and the
   pages whose menu was not sent for this academic year: their next GET plans them with its own form state.
5. listwalk.ended_by says how the walk ended (§7.5 step 7): last_page, no_rows_ok, walking, mismatch:unknown_button
   (a button or a GET pager link the profile does not know: never last_page then), mismatch:no_next, mismatch:page,
   mismatch:empty, mismatch:clv2, mismatch:total, max_pages, post_failed. A list grid that holds nothing that leads
   anywhere but text is a table of content (PageRead.content, FIX 3 11-06): no list at all with no pager; with one
   its pages are pressed as a list's, and its last page ends it with no mismatch:empty.

GET-paged lists (a site with no profile, WordPress, the SSO unit sites' MoreNews and 书表) are walked by the crawler's
own GETs; get_walk() reads the pages' pager links (page_res role pager, with the ones a profile's pager box names:
profile_pagers) and writes the walk's end to every page of it; a list with no pager at all is judged on its one page.

The buttons a clicks rule presses with a POST (ee's file buttons, §7.4) are actions too (kind click, one per site and
row_key across pages), never part of a chain: a failed one waits for `nknu archive retry click` (retry_clicks).

row_key = sha1(site, list, the NFKC title, date, category), with a number added when one page repeats a key (§7.5;
research use_now 1): a row moved down by a new post keeps its key, its ctlNN does not. Every way of doing this the
design does not spell out is Claude's (【Claude 的执行办法】).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser, LexborNode
from warcio.archiveiterator import ArchiveIterator
from warcio.exceptions import ArchiveLoadFailed
from warcio.statusandheaders import StatusAndHeadersParserException

from nknu.archive import links, outcome, profiles, scope, sniff
from nknu.archive.frontier import Frontier, ids_clause, wall
from nknu.crawl import webforms
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import DecodeError, decode_body

MAX_PAGES = 500  # one chain walks at most this many pages (§7.5: max_pages)
GENERIC = "generic"  # listwalk.profile of a site no platform file describes
END_OK = ("last_page", "no_rows_ok")
ENDS = ("last_page", "no_rows_ok", "walking", "mismatch:unknown_button", "mismatch:no_next", "mismatch:page",
        "mismatch:empty", "mismatch:clv2", "mismatch:total", "max_pages", "post_failed")
REDIRECTS = (301, 302, 303, 307, 308)
# presses that belong to no list's chain (never superseded by a new GET of their page, never started again from it, no
# epoch of a walk): a file button's POST (click, §7.4) and a read of a data address (data, design §23 C1)
SOLO_KINDS = ("click", "data")
# periodic updates, stage 1 (design 2026-11-16 §7.2): a look's modes (list_look.mode) and its own end, a page with
# nothing new whose next page is not pressed, never written to listwalk
LOOK, LOOK_GET = "look", "look_get"
SEEN_PAGE = "seen_page"
FAIL_STATUSES = (408, 429)  # with the 5xx: a press that did not go through (the pager's 4xx too)
_DATE = re.compile(r"\d{4}\s*[-./年]\s*\d{1,2}\s*[-./月]\s*\d{1,2}")
_INT = re.compile(r"\s*(\d{1,4})\s*")
_ELLIPSIS = ".…。 "
_DIRECT_DETAIL_MIN = 8  # a detail title shorter than this is never taken as the row's cut title
_FORM_NAME = re.compile(r'form\[(?:id|name)="([^"]+)"\]')
_SIMPLE_ID = re.compile(r"[A-Za-z_][\w:.-]*")  # a form's id or name a CSS selector may name as written
IMAGE_ARG = profiles.IMAGE_ARG  # an ImageButton's argument (<input type="image">: no __doPostBack, gap ⑪ of 11-08)
_IMAGE_XY = ("1", "1")  # the click's coordinates an ImageButton's press sends (name.x, name.y): any point of the image
MENU_ARG = profiles.MENU_ARG  # a menu's press: its form sent by its submit button (profiles.Menu, gap ⑩ of 11-08)
MENU_KEY = "menu:"  # `retry unpressed`: the key a page counts for a menu not sent for this academic year (+ its name)
TAIPEI = ZoneInfo("Asia/Taipei")  # the day a menu's academic year is of (CLAUDE.md: dates by Asia/Taipei)


# ---------------------------------------------------------------- reading one page (no database)

@dataclass(frozen=True)
class Press:
    """One button of a page to press: a row's detail or download, or the next page (pager)."""

    kind: str  # detail, download, pager
    target: str
    argument: str
    row_key: str  # the row's key; the pager's is p<step>
    text: str  # the row's title (the pager's: its own text)
    step: int  # the page it is on (the pager: the page it goes to)
    form_id: str = ""  # the id of the form that holds the button ("" outside any)
    rowset: str = ""  # the row set its row is of ("" for the pager): a detail's answer is checked by its detail_title


@dataclass(frozen=True)
class Row:
    key: str
    title: str
    date: str
    category: str
    href: str | None  # a plain link of the row (as written): a GET detail, a file


@dataclass(frozen=True)
class PageRead:
    """What one page of a profiled site holds for the walk (design §7.5 steps 2 and 7)."""

    profile: str
    rowset: str | None  # the list grid of the page (a walk other than none), or None
    walk: str  # postback, get, one_get, none
    rows: tuple[Row, ...] = ()
    presses: tuple[Press, ...] = ()  # the rows' buttons (every row set of the page)
    next: Press | None = None
    pager_how: str | None = None  # page_n, next
    current: int | None = None
    max_seen: int | None = None
    has_pager: bool = False
    claimed: int | None = None
    empty_said: bool = False
    unknown: tuple[str, ...] = ()  # postback:<target>|<argument> of buttons the profile says nothing of
    unknown_pagers: tuple[str, ...] = ()  # GET pager links of a list the profile walks otherwise
    suspicious: bool = False  # cLv2: a row count that is a multiple of 10 and a pager on a one-GET list
    # postback:<target>[|<argument>] of buttons the profile says to press that no press of this page covers (not a
    # row's planned press, not the pager of a list the walk reads, not a detail read by GET): pressed by nobody, so
    # the crawler writes them as unhandled (要人看), never dropped unseen 【Claude 的执行办法】
    unplanned: tuple[str, ...] = ()
    unplanned_at: tuple[tuple[str, str], ...] = ()  # the same buttons, each (target, argument) as the page writes it
    # the list grid is a table of content, no list (_content_table; FIX 3, 11-06): with a pager its pages are pressed
    # as a list's and list_end gives no mismatch:empty for it; with none read_page gives walk none
    content: bool = False
    # the 「下一頁」 control the profile presses (target, argument, text, form id), found whatever page the pager says
    # it shows (a page past c.nknu's window names none: unnumbered_next, gap ⑤ of 11-08); None for a page_n pager
    next_control: tuple[str, str, str, str] | None = None
    # the action attribute of the form that holds the list grid, as written ("" when it has none; None: no form)
    posts_to: str | None = None
    # (button key, its text) of each button the profile presses, the first of a key's: an unpressed entry carries its
    # text, which a rule that names one decides by (安裝說明's folder, gap ⑪ of 11-08: verify check 8, retry unpressed)
    texts: tuple[tuple[str, str], ...] = ()


def norm(text: str | None) -> str:
    """NFKC, white space collapsed: what keys and comparisons read."""
    return " ".join(unicodedata.normalize("NFKC", text or "").split())


def clean(text: str | None) -> str:
    """White space collapsed, the school's characters kept: what row_text and page_res.text keep."""
    return " ".join((text or "").split())


def button_key(target: str, argument: str = "") -> str:
    """A button's page_res key, as the crawler writes it: postback:<target, ctlNN as ctl*>[|<argument>]
    (profiles.decision reads it: MasterGrid's Page$N is pressed, its Sort$x is not)."""
    return profiles.norm_key(target) + (f"|{argument}" if argument else "")


def row_key(site: str, scope_of: str, title: str, date: str = "", category: str = "") -> str:
    """sha1 of the site, the list (or the detail a file sits in), the NFKC title, date and category (§7.5)."""
    raw = "\x1f".join((site or "", scope_of or "", norm(title), norm(date), norm(category)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _postbacks(tree: LexborHTMLParser, profile: profiles.Profile | None = None) -> list[tuple[LexborNode, str, str]]:
    """Every (element, target, argument) the page posts back with, in document order: a link's href, an onclick or an
    onchange (a submit button's WebForm_PostBackOptions); and an ASP.NET ImageButton that `profile` has a rule for, its
    name as the target and IMAGE_ARG as the argument (gap ⑪ of 11-08: it calls no __doPostBack, the browser sends its
    form with the click's coordinates). One no rule names stays unseen, as before (the report viewer's toolbar on
    english's pages) 【Claude 的执行办法】. Each element once: lexbor gives an element once for each selector of the list
    it matches (<a href="#" onclick="__doPostBack(…)">, a named submit input's onclick), which would press its row
    twice."""
    out, seen = [], set()
    for node in tree.css("[href], [onclick], [onchange], input[name]"):
        if node.mem_id in seen:
            continue
        seen.add(node.mem_id)
        got = []
        for attr in ("href", "onclick", "onchange"):
            code = node.attributes.get(attr)
            if not code or (attr == "href" and node.tag not in ("a", "area")):
                continue
            got += [(node, t, a) for t, a in webforms.postback_calls(code)]
        if not got and profile is not None and (name := _image_button(node)) is not None \
                and profiles.rule_in(profile, name, IMAGE_ARG, _label(node)) is not None:
            got.append((node, name, IMAGE_ARG))
        out += got
    return out


def _image_button(node: LexborNode) -> str | None:
    """The name of an ASP.NET ImageButton (<input type="image" name="…">, not disabled, in a form), else None."""
    a = node.attributes
    if node.tag != "input" or (a.get("type") or "").strip().lower() != "image" or "disabled" in a:
        return None
    name = (a.get("name") or "").strip()
    return name if name and _form_action(node) is not None else None


def image_form(html: str, name: str) -> tuple[str | None, str]:
    """The form a press of the ImageButton `name` sends (gap ⑪ of 11-08): a CSS selector that picks the one form that
    holds it, by its id, else its name (as webforms.form_for_control names a form), and ""; or None and why not:
    control_missing (the page holds no such ImageButton), no_form (the form that holds it cannot be named alone)."""
    tree = LexborHTMLParser(html or "")
    node = next((n for n in tree.css("input[name]") if _image_button(n) == name), None)
    if node is None:
        return None, "control_missing"
    form = node.parent
    while form is not None and form.tag != "form":
        form = form.parent
    for attr in ("id", "name"):
        value = form.attributes.get(attr) if form is not None else None
        if value and _SIMPLE_ID.fullmatch(value):
            css = f'form[{attr}="{value}"]'
            hits = tree.css(css)
            if len(hits) == 1 and hits[0].mem_id == form.mem_id:
                return css, ""
    return None, "no_form"


# ---------------------------------------------------------------- a menu's form (profiles.Menu, gap ⑩ of 11-08)

def _named(node: LexborNode, tag: str, name: str, typ: str | None = None) -> bool:
    """Whether node is a <tag> named `name` (of input type typ, when given) that is not disabled: a disabled control is
    never sent, so it counts as missing 【Claude 的执行办法】."""
    a = node.attributes
    return (node.tag == tag and (a.get("name") or "") == name and "disabled" not in a
            and (typ is None or (a.get("type") or "").strip().lower() == typ))


def _in_form(form: LexborNode | None, tag: str, name: str, typ: str | None = None) -> LexborNode | None:
    return next((n for n in form.css(tag) if _named(n, tag, name, typ)), None) if form is not None else None


def _the_form(tree: LexborHTMLParser, form_id: str) -> LexborNode | None:
    """The form of that id, when the page holds exactly one."""
    forms = [f for f in tree.css("form") if (f.attributes.get("id") or "") == form_id]
    return forms[0] if len(forms) == 1 else None


def _offered(select: LexborNode) -> list[LexborNode]:
    """A select's options a browser may send, in the page's order: none disabled or in a disabled optgroup, as
    webforms reads them for form_state (the press and the form state agree on what is offered)."""
    return [o for o in select.css("option") if not webforms._disabled(o)]


def _menu_presses(m: profiles.Menu, tree: LexborHTMLParser, *, site: str, scope_of: str,
                  year: int) -> tuple[list[Press], str]:
    """The presses of one menu rule on a page (gap ⑩ of 11-08; the owner 11-09 「现在补 115」): the form of id m.form,
    alone on the page, must hold the year select, the each select and the submit button (none of them disabled), and
    the year select must offer `year`; then one press of the submit button per option of the each select a browser
    may send (offered: in the page's order; its value webforms' reading, the value attribute else the text, white space
    collapsed; a value written twice is one press). A press is a detail (its answer a page of that year and option)
    whose argument names both values (profiles.menu_argument); its row key is the list's (scope_of: the page the menu
    was first read on, never a detail's key: the same option is one row whichever page or answer shows it) from the
    year and the value exactly as sent (quoted: row_key's NFKC and white space never make two options one row, the
    second never pressed; review of 11-09), its rule's name the category; its text the year, the option's optgroup
    label and its text. Returns the presses and the submit button's text (its value, "" when it is nowhere): with no
    press, the menu is written unpressed under that text (要人看). An each select with no option to send counts as
    missing 【Claude 的执行办法】."""
    form = _the_form(tree, m.form)
    years, each = _in_form(form, "select", m.year), _in_form(form, "select", m.each)
    submit = _in_form(form, "input", m.submit, "submit")
    button = submit if submit is not None else next(
        (n for n in tree.css("input") if _named(n, "input", m.submit, "submit")), None)
    text = clean(button.attributes.get("value") or "") if button is not None else ""
    if years is None or each is None or submit is None or \
            str(year) not in {webforms._option_value(o) for o in _offered(years)}:
        return [], text
    out: list[Press] = []
    seen: set[str] = set()
    for o in _offered(each):
        value = webforms._option_value(o)
        if value in seen:
            continue
        seen.add(value)
        up = o.parent
        group = (up.attributes.get("label") or "") if up is not None and up.tag == "optgroup" else ""
        out.append(Press("detail", m.submit, profiles.menu_argument(m, year, value),
                         row_key(site, scope_of, f"{year} {quote(value, safe='')}", category=m.name),
                         clean(f"{year}學年度 {group} {o.text() or ''}"), 1, m.form, m.name))
    return out, text


def menu_form(html: str, form_id: str, submit: str) -> tuple[str | None, str]:
    """The form a menu's press sends (gap ⑩ of 11-08): a CSS selector of the form of id form_id and "", or None and why
    not, as image_form says it: control_missing (the page holds no such submit button), no_form (no form of that id,
    alone on the page, holds it)."""
    tree = LexborHTMLParser(html or "")
    buttons = [n for n in tree.css("input") if _named(n, "input", submit, "submit")]
    if not buttons:
        return None, "control_missing"
    form = _the_form(tree, form_id) if _SIMPLE_ID.fullmatch(form_id or "") else None
    if form is None or not any(_ancestor(b, {form.mem_id}) is not None for b in buttons):
        return None, "no_form"
    return f'form[id="{form_id}"]', ""


def menu_post(html: str, css: str, fields: Iterable[tuple[str, str]], choice: Iterable[tuple[str, str]],
              submit: str) -> tuple[list[tuple[str, str]], str | None]:
    """What a browser sends for a menu's press (gap ⑩ of 11-08): the form's fields as webforms.form_state read them
    (fields; css: the form), each value chosen in place of its select's entry, then the submit button that sent the
    form, its name and value (the HTML Standard's entry list holds its submitter; no __EVENTTARGET, which no
    __doPostBack sets). Returns them and None, or the name of a select none of whose options the page offers is the
    value chosen (target_for: mismatch option_missing:<name>, nothing sent). A select with no entry (a list box with
    nothing selected) gets the value chosen at the end 【Claude 的执行办法】."""
    tree = LexborHTMLParser(html or "")
    form = tree.css_first(css)
    out = list(fields)
    for name, value in choice:
        select = _in_form(form, "select", name)
        if select is None or value not in {webforms._option_value(o) for o in _offered(select)}:
            return out, name
        at = [i for i, (k, _v) in enumerate(out) if k == name]
        if not at:
            out.append((name, value))
            continue
        out = [(name, value) if i == at[0] else x for i, x in enumerate(out) if i == at[0] or i not in at]
    button = _in_form(form, "input", submit, "submit")
    return [*out, (submit, (button.attributes.get("value") or "") if button is not None else "")], None


def menu_mismatch(html: str, url: str, form_id: str, choice: Iterable[tuple[str, str]]) -> str | None:
    """Why a menu press's answer is not what was asked (gap ⑩ of 11-08), in words (asked <values>, shows <values>), or
    None: the answer's form of id form_id, read as a browser sends it (webforms.form_state), holds each value asked as
    its select's entry. A course table shows the year and the department its courses are of; an answer the server
    gave for another choice (the page's default year: the session lapsed, the year was not taken) shows that one,
    never a title to check (title_ok)."""
    asked = list(choice)
    want = "/".join(v for _n, v in asked)
    try:
        if not _SIMPLE_ID.fullmatch(form_id or ""):
            raise ValueError("no form")
        _post, fields = webforms.form_state(html or "", url, f'form[id="{form_id}"]')
    except ValueError:
        return f"asked {want}, shows no form {form_id}"
    shown: dict[str, str] = {}
    for k, v in fields:
        shown.setdefault(k, v)
    if all(shown.get(n) == v for n, v in asked):
        return None
    return f"asked {want}, shows {'/'.join(shown.get(n, '?') for n, _v in asked)}"


def this_year(t: float) -> int:
    """The academic year (profiles.academic_year) of the day a time is in, in Asia/Taipei (CLAUDE.md: dates by
    Asia/Taipei): the year a menu is sent with (gap ⑩ of 11-08, the owner 11-09 「现在补 115」)."""
    return profiles.academic_year(datetime.fromtimestamp(t, TAIPEI).date())


_LIVE = ("done", "pending", "active")  # a press done well, or one the run presses (or starts again from its GET)
_TRIED = ("failed", "mismatch")  # a press that went and brought nothing it asked for (Chains._press: twice, a person's)


def menus_not_sent(f: Frontier, year: int, only: str = "",
                   args: Iterable[str] = ()) -> list[tuple[sqlite3.Row, profiles.Menu]]:
    """The stored pages whose menu is not sent for `year` as far as a new GET of the page could still send it (gap ⑩
    of 11-08, the owner 11-09 「现在补 115」), each with its menu rule: a page (kind page, done with a 200: one the school
    answered otherwise holds no form to send, and would come back each time) whose own platform (by its address; the
    sites first by url.site, as the crawler writes it) has a menu rule for its path, when no press of the rule for that
    year was sent or waits (none done, pending, active, failed or mismatched: the course tables stored before the rule,
    or last year's), or when an option's row has no press done, pending or active and one try that failed or
    mismatched, which the new GET presses again (Chains._press). Judged option by option (review of 11-09): a row tried
    twice is a person's (mismatch or failed, 要人看), and a row whose presses were all superseded was planned again by
    the GET that superseded them, or that page no longer offers it: neither puts the page back (each `retry unpressed`
    would fetch it again for nothing), and one option done hides no other that can still be pressed. only,
    args: a filter on u.owner_site (`retry unpressed --sites`). Reads only (verify check 4 names the same pages); the
    rows hold id, url and owner_site 【Claude 的执行办法】."""
    sites = [s for (s,) in f.q("SELECT DISTINCT site FROM url WHERE kind = 'page' AND state = 'done'")
             if (p := profiles.profile_for_site(s)) is not None and p.menus]
    if not sites:
        return []
    out = []
    for r in f.q(f"SELECT u.id, u.url, u.owner_site FROM url u WHERE u.kind = 'page' AND u.state = 'done' AND "
                 f"u.status = 200 AND u.site IN ({','.join('?' * len(sites))}){only} ORDER BY u.id", (*sites, *args)):
        prof = profiles.profile_for(r["url"])
        try:
            path = urlsplit(r["url"]).path.lower()
        except ValueError:
            continue
        for m in (prof.menus if prof is not None else ()):
            if not re.search(m.paths, path):
                continue
            rows: dict[str, list[str]] = defaultdict(list)  # the presses of this year by their option's row
            for a in f.q("SELECT row_key, argument, state FROM action WHERE list_id=? AND kind='detail'", (r["id"],)):
                if profiles.menu_year(m, a["argument"]) == year:
                    rows[a["row_key"]].append(a["state"])
            sent = any(s != "superseded" for states in rows.values() for s in states)
            again = any(not set(states) & set(_LIVE) and sum(s in _TRIED for s in states) == 1
                        for states in rows.values())
            if not sent or again:
                out.append((r, m))
    return out


def _label(node: LexborNode) -> str:
    a = node.attributes
    return clean((node.text() or "") if node.tag != "select" else "") or clean(a.get("value") or a.get("title") or "")


def _form_id(node: LexborNode | None) -> str:
    while node is not None:
        if node.tag == "form":
            return node.attributes.get("id") or node.attributes.get("name") or ""
        node = node.parent
    return ""


def _form_action(node: LexborNode | None) -> str | None:
    """The action attribute of the form that holds node, as written ("" when it has none), or None outside any."""
    while node is not None:
        if node.tag == "form":
            return (node.attributes.get("action") or "").strip()
        node = node.parent
    return None


def _real_href(node: LexborNode) -> str | None:
    href = (node.attributes.get("href") or "").strip()
    if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
        return None
    return href


def _int(text: str | None) -> int | None:
    m = _INT.fullmatch(text or "")
    return int(m.group(1)) if m else None


def _ancestor(node: LexborNode, ids: set[int]) -> LexborNode | None:
    """The nearest element above (or at) node whose mem_id is in ids."""
    while node is not None:
        if node.mem_id in ids:
            return node
        node = node.parent
    return None


def _inside_link(node: LexborNode, stop: LexborNode) -> bool:
    while node is not None and node.mem_id != stop.mem_id:
        if node.tag == "a":
            return True
        node = node.parent
    return False


def _title(rs: profiles.Rows, el: LexborNode, plain: LexborNode | None, first_press: str) -> str:
    if rs.title:
        t = el.css_first(rs.title)
        if t is not None:
            if rs.title_attr and (t.attributes.get(rs.title_attr) or "").strip():
                return clean(t.attributes[rs.title_attr])
            if rs.strip:
                parts = [n.text() or "" for n in t.css(rs.strip)]
                text = t.text() or ""
                for p in parts:
                    text = text.replace(p, " ", 1)
                return clean(text)
            return clean(t.text())
    if plain is not None:
        return clean(plain.text())
    return first_press


def _cell(el: LexborNode, css: str) -> str:
    if not css:
        return ""
    n = el.css_first(css)
    return clean(n.text()) if n is not None else ""


def read_page(profile: profiles.Profile, url: str, html: str, *, site: str, list_url: str | None = None,
              parent_key: str | None = None, page: links.PageLinks | None = None,
              step: int | None = None, walks: tuple[str, ...] = profiles.WALKS, year: int | None = None) -> PageRead:
    """Read one page of a profiled site: its list (the first row set whose grid is on the page and walks), every row
    set's row buttons to press, the next page's button. Its buttons are decided by `profile` (the platform the page
    is written on: profiles.rule_in), site is the site its rows count for (url.owner_site: their row keys).
    list_url: the chain's list (row keys are the list's, across its pages); parent_key: the row key of the detail this
    page is the answer of (its files' keys). page: the page's links.extract, when the caller has it (GET pager
    candidates are read from it). step: the page number this answer was asked for (the presses' step when the page
    shows none). walks: only the row sets that walk so (a detail's answer: none, its files). year: the academic year
    the profile's menu rules whose paths match the page are sent with (_menu_presses: one press per option, after the
    rows' presses; the row keys the list's, list_url or url, never parent_key's), a menu its page cannot send written
    unpressed (postback:<submit>|MENU_ARG, 要人看); None: no menu is pressed nor written (a reading that presses
    nothing: rejudge, the export). Gap ⑩ of 11-08, the owner 11-09 「现在补 115」."""
    tree = LexborHTMLParser(html or "")
    try:
        path, query = urlsplit(url).path.lower(), urlsplit(url).query.lower()
    except ValueError:
        path = query = ""
    pbs = _postbacks(tree, profile)
    rules = {}
    for node, t, a in pbs:
        if (t, a, node.mem_id) not in rules:
            rules[(t, a, node.mem_id)] = profiles.rule_in(profile, t, a, _label(node))
    unknown = tuple(dict.fromkeys(f"{profiles.norm_key(t)}|{a}" for node, t, a in pbs
                                  if rules[(t, a, node.mem_id)] is None))
    said: dict[str, str] = {}
    for node, t, a in pbs:
        if (b := rules[(t, a, node.mem_id)]) is not None and b.decision == "press":
            said.setdefault(button_key(t, a), _label(node))
    menu_presses: list[Press] = []
    menus_left: list[tuple[str, str]] = []  # (submit, MENU_ARG) of a menu its page cannot send
    for m in (profile.menus if year is not None else ()):
        if not re.search(m.paths, path):
            continue
        got, label = _menu_presses(m, tree, site=site, scope_of=list_url or url, year=year)
        menu_presses += got
        if not got:
            menus_left.append((m.submit, MENU_ARG))
            said.setdefault(button_key(m.submit, MENU_ARG), label)
    texts = tuple(said.items())
    sets = [rs for rs in profile.rows if rs.walk in walks and (not rs.paths or re.search(rs.paths, path))
            and (not rs.query or re.search(rs.query, query)) and tree.css_first(rs.grid)]
    lst = next((rs for rs in sets if rs.walk != "none"), None)
    pager = _pager(lst, tree, pbs, rules) if lst is not None and lst.walk == "postback" else None
    current = pager.current if pager else None
    here = current if current is not None else (step or 1)
    presses: list[Press] = []
    rows: tuple[Row, ...] = ()
    covered: set[tuple[str, str]] = set()
    for rs in sets:
        got_rows, got_presses, by_get = _rows(rs, tree, pbs, rules, site=site,
                                              scope_of=parent_key or list_url or url, profile=profile, base=url,
                                              step=here)
        presses += got_presses
        covered |= by_get
        if rs is lst:
            rows = got_rows
    presses += menu_presses
    covered |= {(p.target, p.argument) for p in presses}
    if pager is not None and pager.found:  # the walk steps through the pager: every page of it is reached
        covered |= {(t, a) for n, t, a in pbs if (b := rules[(t, a, n.mem_id)]) is not None and b.role == "pager"}
    unplanned_at = tuple(dict.fromkeys(
        [*((t, a) for n, t, a in pbs
           if (b := rules[(t, a, n.mem_id)]) is not None and b.decision == "press" and (t, a) not in covered),
         *menus_left]))
    unplanned = tuple(dict.fromkeys(button_key(t, a) for t, a in unplanned_at))
    if lst is None:
        return PageRead(profile.name, None, "none", presses=tuple(presses), unknown=unknown, unplanned=unplanned,
                        unplanned_at=unplanned_at, texts=texts)
    text = norm(tree.body.text(separator=" ")) if tree.body is not None else ""
    grid = tree.css_first(lst.grid)
    grid_text = norm(grid.text(separator=" ")) if grid is not None else ""
    claimed = None
    if lst.claimed and (m := re.search(lst.claimed, text)):
        claimed = int(m.group(1))
    empty = any(w in grid_text or (not rows and w in text) for w in lst.empty)
    content = not rows and not empty and claimed is None and _content_table(lst, tree, pbs, rules)
    if content and (pager is None or not pager.found):  # a table of content with no pager: no list (FIX 3, 11-06)
        return PageRead(profile.name, None, "none", presses=tuple(presses), unknown=unknown, unplanned=unplanned,
                        unplanned_at=unplanned_at, content=True, texts=texts)
    unknown_pagers: tuple[str, ...] = ()
    if lst.walk != "get":
        if page is None:
            page = links.extract(html, url, profile.content_css)
        unknown_pagers = tuple(page.pagers)
    suspicious = bool(lst.suspicious and rows and len(rows) % 10 == 0 and any(
        re.fullmatch(lst.suspicious, t) and a.startswith("Page$") for _n, t, a in pbs))
    posts_to = _form_action(grid)
    if pager is None or not pager.found:
        one = lst.walk in ("one_get", "postback")  # a postback list with no pager: one page
        return PageRead(profile.name, lst.name, lst.walk, rows, tuple(presses), claimed=claimed, empty_said=empty,
                        unknown=unknown, unknown_pagers=unknown_pagers, suspicious=suspicious,
                        current=1 if one else None, max_seen=1 if one else None, unplanned=unplanned,
                        unplanned_at=unplanned_at, posts_to=posts_to, texts=texts)
    return PageRead(profile.name, lst.name, lst.walk, rows, tuple(presses), pager.next, pager.how, pager.current,
                    pager.max_seen, pager.found, claimed, empty, unknown, unknown_pagers, suspicious, unplanned,
                    unplanned_at, content=content, next_control=pager.control, posts_to=posts_to, texts=texts)


_NO_PAGE = ("mailto:", "tel:")  # links that open no page: a table of people may hold them


def _grid_row(node: LexborNode, grid: LexborNode) -> LexborNode | None:
    """The row of the grid a node is in: its outermost tr inside the grid (a nested table's rows are its cell's)."""
    row = None
    while node is not None and node.mem_id != grid.mem_id:
        if node.tag == "tr":
            row = node
        node = node.parent
    return row


def _content_table(rs: profiles.Rows, tree: LexborHTMLParser, pbs, rules) -> bool:
    """Whether a list grid that shows no row (and no words for none, and no 「共 N 筆」: read_page) is a table of
    content, not a list (FIX 3, 11-06, 【用户同意 Claude 的建议】: Claude's 11-04 fix 「用一条统一的判断：表格里一个链接
    都没有、也没有翻页按钮的，就不当列表。这样不用一页一页加例外」, the owner 11-04 「待会再修」, 11-06 the button 「先修
    三件再一起重开」): affair's forumsview.aspx (a discussion thread), tecs's activeorder.aspx (a sign-up table),
    c.nknu's member.aspx (編制與業務, P51 11-03), tg's honor.aspx (an honour roll paged by Page$N). Nothing in the grid leads anywhere — no link (an <a href> or <area href>) but mailto:, tel: and the
    postbacks the profile knows as its pager's or ignores (a Sort$ header), no onclick, no form control but hidden
    inputs — and a cell (td) holds text in a row of the grid that holds no <a> but mailto: and tel: links and no
    postback: never the pager's row, whose page shown is a number outside any link (spe's last page writes its
    「下一頁」 as an <a> with no href). A javascript: link that is no postback, an href="#", a button, a postback the
    profile does not know or presses for a row: a list (mismatch:empty, 要人看), what it opens is not known. How the
    judgement reads a grid, and a paged table of content walked to its last page: 【Claude 的执行办法】"""
    grid = tree.css_first(rs.grid)
    if grid is None:
        return False
    posts = {n.mem_id for n, _t, _a in pbs}
    known = {n.mem_id for n, t, a in pbs
             if (b := rules[(t, a, n.mem_id)]) is not None and (b.role == "pager" or b.decision == "ignore")}
    for n in grid.css("a[href], area[href], [onclick]"):
        href = (n.attributes.get("href") or "").strip().lower()
        if n.mem_id not in known and not (href.startswith(_NO_PAGE) and "onclick" not in n.attributes):
            return False
    for n in grid.css("input, select, textarea, button"):
        hidden = n.tag == "input" and (n.attributes.get("type") or "").strip().lower() == "hidden"
        if n.mem_id not in known and not hidden:
            return False
    for td in grid.css("td"):
        if norm(td.text(separator=" ")) and (row := _grid_row(td, grid)) is not None and _plain_row(row, posts):
            return True
    return False


def _plain_row(row: LexborNode, posts: set[int]) -> bool:
    """A row of the grid with no postback in it and no <a> but mailto: and tel: links (an <a> with no href counts: spe's
    disabled 「下一頁」 on its last page)."""
    for x in row.css("a, [onclick], [onchange]"):
        href = (x.attributes.get("href") or "").strip().lower()
        if x.mem_id in posts or (x.tag == "a" and not href.startswith(_NO_PAGE)):
            return False
    return True


@dataclass(frozen=True)
class _PagerRead:
    how: str | None
    current: int | None
    max_seen: int | None
    next: Press | None
    found: bool
    control: tuple[str, str, str, str] | None = None  # PageRead.next_control


_BOX = {"page_n": ("table",), "next": ("div", "td", "p", "nav", "ul", "tr")}


def _pager(rs: profiles.Rows, tree: LexborHTMLParser, pbs, rules) -> _PagerRead:
    """The pager of a postback list: which way it pages (Page$N, or a 「下一頁」 control), the page shown (a number
    the pager writes outside any link), the largest page it names, and the next page's press. The pager is found by
    its family of controls, so the last page (where a 「下一頁」 may be gone) still says which page it is. A 「下一頁」
    the profile presses is kept as the control (target, argument, text, form id) whether or not a page shown was
    read: a page that names none is the caller's to judge (unnumbered_next)."""
    for pg in rs.pagers:
        if pg.how == "page_n":
            fam = [(n, t, a) for n, t, a in pbs if re.fullmatch(pg.target, t) and a.startswith("Page$")
                   and _int(a[5:]) is not None]
        else:
            fam = [(n, t, a) for n, t, a in pbs if re.fullmatch(pg.family or pg.target, t)]
        if fam:
            break
    else:
        return _PagerRead(None, None, None, None, False)
    box = fam[0][0].parent
    while box is not None and box.tag not in _BOX[pg.how]:
        box = box.parent
    grid = tree.css_first(rs.grid)
    if box is not None and grid is not None and box.mem_id == grid.mem_id:
        box = fam[0][0].parent  # a pager written straight into the grid's last row: that row, not the grid
        while box is not None and box.tag != "tr":
            box = box.parent
    current, numbers = None, []
    if box is not None:
        for span in box.css("span, b, strong"):
            if not _inside_link(span, box) and (n := _int(span.text())) is not None:
                current = n
                break
        numbers = [n for a in box.css("a") if (n := _int(a.text())) is not None]
    if pg.how == "page_n":
        numbers += [int(a[5:]) for _n, _t, a in fam]
        want = f"Page${current + 1}" if current is not None else None
        nxt = next(((n, t, a) for n, t, a in fam if a == want), None)
    else:
        nxt = next(((n, t, a) for n, t, a in fam if re.fullmatch(pg.target, t)
                    and (b := rules[(t, a, n.mem_id)]) is not None and b.role == "pager"), None)
    if nxt is not None and ((b := rules[(nxt[1], nxt[2], nxt[0].mem_id)]) is None or b.decision != "press"):
        nxt = None  # only what the profile says to press
    max_seen = max([*numbers, *([current] if current is not None else [])], default=None)
    press = control = None
    if nxt is not None and pg.how == "next":
        control = (nxt[1], nxt[2], _label(nxt[0]), _form_id(nxt[0]))
    if nxt is not None and current is not None:
        node, t, a = nxt
        press = Press("pager", t, a, f"p{current + 1}", _label(node), current + 1, _form_id(node))
    return _PagerRead(pg.how, current, max_seen, press, True, control)


def _rows(rs: profiles.Rows, tree, pbs, rules, *, site: str, scope_of: str, profile: profiles.Profile,
          base: str, step: int) -> tuple[tuple[Row, ...], list[Press], set[tuple[str, str]]]:
    """The rows of one row set and their buttons to press: a row is an element `row` inside `grid` that holds the
    title element (with no title selector: a plain link or a row button). A row whose plain link is one of the
    profile's detail pages is read by GET: its detail button is not pressed (§7.4: WebIdx's newsidx= link); those
    buttons are the third part of what it returns, (target, argument) each."""
    by_get_buttons: set[tuple[str, str]] = set()
    grid = tree.css_first(rs.grid)
    if grid is None:
        return (), [], by_get_buttons
    cand = list(grid.css(rs.row))
    ids = {n.mem_id for n in cand}
    by_row: dict[int, list[tuple[LexborNode, str, str, profiles.Button]]] = {}
    for node, t, a in pbs:
        b = rules[(t, a, node.mem_id)]
        holder = _ancestor(node, ids)
        if holder is not None and b is not None:
            by_row.setdefault(holder.mem_id, []).append((node, t, a, b))
    rows: list[Row] = []
    presses: list[Press] = []
    seen: Counter[str] = Counter()
    for el in cand:
        mine = by_row.get(el.mem_id, [])
        if any(b.role == "pager" for _n, _t, _a, b in mine):
            continue  # the pager's own row
        row_presses = [(n, t, a, b) for n, t, a, b in mine if b.decision == "press" and b.role in ("detail", "download")]
        plain = next((a for a in el.css("a[href]") if _real_href(a)
                      and (up := _ancestor(a, ids)) is not None and up.mem_id == el.mem_id), None)
        if rs.title:
            if el.css_first(rs.title) is None:
                continue
        elif plain is None and not row_presses:
            continue
        title = _title(rs, el, plain, _label(row_presses[0][0]) if row_presses else "")
        date = _cell(el, rs.date) or (m.group(0) if (m := _DATE.search(norm(el.text(separator=" ")))) else "")
        category = _cell(el, rs.category)
        key = row_key(site, scope_of, title, date, category)
        seen[key] += 1
        if seen[key] > 1:
            key = f"{key}#{seen[key]}"
        href = _real_href(plain) if plain is not None else None
        rows.append(Row(key, title, date, category, href))
        by_get = href is not None and profiles.page_kind(scope.canonical(href, base) or "") == "detail"
        n_kind: Counter[str] = Counter()
        for node, t, a, b in row_presses:
            if b.role == "detail" and by_get:
                by_get_buttons.add((t, a))
                continue
            n_kind[b.role] += 1
            rk = key if n_kind[b.role] == 1 else f"{key}/{n_kind[b.role]}"
            presses.append(Press(b.role, t, a, rk, title or _label(node), step, _form_id(node), rs.name))
    return tuple(rows), presses, by_get_buttons


def profile_pagers(url: str, html: str, base: str | None = None) -> list[str]:
    """The pages of a list walked by GET that its platform file's pager box names (Rows.get_pager; review 10-26 phase
    3): each link in the box whose text is a page number or a next word, on the page's own host and path, canonical.
    The crawler adds them to links.extract's pagers (a link is_pager does not see, as WebIdx's first MoreNews page
    links its pages with a sort=date it does not carry). The platform file is the one the page's own address is
    written on (profiles.profile_for: 图资处's MoreNews pages are WebIdx pages, gap ③ of 11-08). 【Claude 的执行办法】"""
    prof = profiles.profile_for(url)
    if prof is None:
        return []
    try:
        parts = urlsplit(url)
        path, query = parts.path.lower(), parts.query.lower()
    except ValueError:
        return []
    sets = [rs for rs in prof.rows if rs.walk == "get" and rs.get_pager and (not rs.paths or re.search(rs.paths, path))
            and (not rs.query or re.search(rs.query, query))]
    if not sets:
        return []
    tree = LexborHTMLParser(html or "")
    here = scope.canonical(url) or url
    hp = urlsplit(here)
    out: list[str] = []
    for rs in sets:
        if tree.css_first(rs.grid) is None:
            continue
        for node in tree.css(rs.get_pager):
            href, text = _real_href(node), norm(node.text())
            if href is None or not (text.strip("[]() ").isdigit() or any(w in text.lower() for w in links.NEXT_WORDS)):
                continue
            target = scope.canonical(href, base or url)
            if target is None or target == here or target in out:
                continue
            t = urlsplit(target)
            if (t.scheme, t.netloc, t.path) == (hp.scheme, hp.netloc, hp.path):
                out.append(target)
    return out


_NEXT_TEXT = ("下一頁", "下一页", "下頁", "後頁", "next", "nextpage")


def pager_candidates(url: str, page: links.PageLinks) -> tuple[str, ...]:
    """Links of a page that look like another page of a list (their text a page number from 2, or a next page's
    word, alone; on the page's own host and path) and that are not among its pagers: what a list judged to have one
    page only must not have (§7.5 step 7: never last_page then; review 10-26 phase 3) 【Claude 的执行办法】."""
    try:
        here = urlsplit(url)
    except ValueError:
        return ()
    known, out = set(page.pagers), []
    for href, text in page.links:
        t = re.sub(r"[\s<>‹›«»()\[\]]", "", norm(text)).lower()
        if not ((t.isdigit() and int(t) >= 2) or t in _NEXT_TEXT):
            continue
        target = scope.canonical(href, page.base)
        if target is None or target in known or target in out:
            continue
        got = urlsplit(target)
        if (got.scheme, got.netloc, got.path) == (here.scheme, here.netloc, here.path):
            out.append(target)
    return tuple(out)


def list_end(pr: PageRead, rows_unique: int | None = None) -> str:
    """How the walk ends at this page when there is no next page to press (§7.5 step 7)."""
    if pr.has_pager and pr.current is None:
        return "mismatch:page"
    if not pr.rows and not pr.content:  # a table of content's page has no rows: its pager says where it ends (FIX 3)
        return "no_rows_ok" if pr.empty_said else "mismatch:empty"
    if pr.suspicious:
        return "mismatch:clv2"
    if pr.has_pager and pr.max_seen is not None and pr.current is not None and pr.current < pr.max_seen:
        return "mismatch:no_next"
    if pr.unknown or pr.unknown_pagers:
        return "mismatch:unknown_button"  # a pager the profile does not know may hide the next page (§7.5 step 7)
    if pr.claimed is not None and rows_unique is not None and rows_unique != pr.claimed:
        return "mismatch:total"
    return "last_page"


# action.reason of a 「下一頁」 press whose answer named no page shown and was taken as the page asked for
# (unnumbered_next, gap ⑤ of 11-08): a trace for a person, its result stays ok
UNNUMBERED = "unnumbered"


def answer_elsewhere(pr: PageRead, asked: str) -> str | None:
    """Why the answer to a press of a list's pager is no page of that list, in words for the walk's note, or None
    (gap ⑤ of 11-08): it holds no list grid (c.nknu's pe album.aspx?pn=48 answers page 2 with the site's home page,
    tecs's activeviewlist.aspx draws no list on a postback), or the form that holds its list posts back to another
    program than the one asked (a Server.Transfer: tecs's publish.aspx answers page 2 with Default.aspx's, its form
    posting to Default.aspx). asked: the address the press was sent to, not the list's: a list page that the school
    answers with another program's page from its GET on (tecs's page.aspx, posting to Default.aspx) is walked where
    its form posts. Paths are compared in lower case (IIS reads them so); a grid in no form says nothing here.
    【Claude 的执行办法】"""
    if pr.rowset is None:
        return "holds no list"
    if pr.posts_to is None:
        return None
    try:
        there = urlsplit(urljoin(asked, pr.posts_to)).path.lower()
        here = urlsplit(asked).path.lower()
    except ValueError:
        return None
    return None if there == here else f"is another page ({there})"


def _walked_row(r: Row, walked: Collection[str], links: Mapping[str, str], base: str) -> bool:
    """A row on a page walked before: its key walked, and, when both it and the walked row of that key link a detail
    page, the same page. 成教所's 所友名錄 (11-09): its rows are a name linking the alumnus's own page, and one name
    can be two alumni, so a key alone took 12 new alumni of the fill-in's page 36 for rows walked already
    【Claude 的执行办法】."""
    if r.key not in walked:
        return False
    was = links.get(r.key)
    if was is None or not r.href:
        return True
    got = scope.canonical(r.href, base)
    return got is None or got == was


def unnumbered_next(pr: PageRead, want: int, walked: Collection[str], asked: str,
                    links: Mapping[str, str] | None = None) -> tuple[PageRead | None, str]:
    """A 「下一頁」 answer that names no page shown, read as the page asked for (gap ⑤ of 11-08, 【Claude 的执行办法】).
    c.nknu's pager (MasterGrid$ctlNN$lbnNext) writes ten page links and the page shown as a <span>; on page n ≥ 6 of an
    N-page list its links run from max(n − 5, N − 9) to N, so on a list of more than 15 pages, pages 6 … N − 10 name
    no page shown (tg's 最新消息, 112 pages: page 6 shows the links 103 … 112 and no span). A press of 「下一頁」 asks
    for the page after the one its form state was built from: the answer is taken as page `want` when all of these
    hold. It is a page of the list asked (answer_elsewhere: it holds the list, and its form posts back to the address
    asked); it still has the 「下一頁」 the profile presses; its pager names page `want` or a later one (or no page
    number at all); it has rows, at least half of them on no page walked before (walked: the row keys of this walk's
    earlier pages; links: the detail page each of them links, where known: _walked_row; a few rows pinned on every
    page pass, the same page again does not). Returns the page read so
    (shown: want; the next press built from that control) and "", or None and why not, in words for the walk's
    note. An answer that names its page, or a page_n pager's (ASP.NET's numeric pager always marks the page shown),
    is not judged here: (None, "")."""
    if pr.pager_how != "next" or pr.current is not None:
        return None, ""
    why = answer_elsewhere(pr, asked)
    if why is not None:
        return None, why
    if pr.next_control is None:
        return None, "shows no page number and no 「下一頁」 to press"
    if pr.max_seen is not None and pr.max_seen < want:
        return None, f"shows no page number and its pager names pages up to {pr.max_seen} only"
    if not pr.rows:
        return None, "shows no page number and no rows"
    old = sum(1 for r in pr.rows if _walked_row(r, walked, links or {}, asked))
    if old * 2 > len(pr.rows):
        return None, f"shows no page number and {old} of {len(pr.rows)} rows walked already"
    target, argument, text, form_id = pr.next_control
    nxt = Press("pager", target, argument, f"p{want + 1}", text, want + 1, form_id)
    return replace(pr, current=want, next=nxt), ""


def title_ok(row_title: str, html: str, css: str | None, exclude: str | None = None) -> bool:
    """§7.5 step 5: the detail's title area begins with the row's title (the list may cut it, an ellipsis after it).
    Spaces are not compared. With no title area known (css None) the row's title (its first 30 characters) must be
    in the page's text outside `exclude` (the list grid: an answer that shows the list again names every row's title,
    so only the rest of it can say which item it shows). A row with no title cannot be checked: True."""
    want = re.sub(r"\s+", "", norm(row_title)).rstrip(_ELLIPSIS)
    want = want.removesuffix("...")
    if not want:
        return True
    tree = LexborHTMLParser(html or "")
    if css:
        node = tree.css_first(css)
        if node is None:
            return False
        got = re.sub(r"\s+", "", norm(node.text()))
        return got.startswith(want) or (len(got) >= _DIRECT_DETAIL_MIN and want.startswith(got))
    if exclude:
        for node in tree.css(exclude):
            node.decompose()
    body = tree.body
    text = re.sub(r"\s+", "", norm(body.text(separator=" ")) if body is not None else "")
    return want[:30] in text


EMPTY_DETAIL = outcome.EMPTY_DETAIL  # action.reason of a detail answered with nothing but its title: thin


def _without_pressed(pr: PageRead, action) -> PageRead:
    """A detail's answer read for its own buttons, less the button that brought it (gap ⑪ of 11-08, 【Claude 的执行办法】):
    an answer may show the button pressed again (the 安裝說明 folder it opened is still a button of the folder list), and
    pressed from that answer it would bring the same answer again under a new row key, and again."""
    same = (action["target"], action["argument"] or "")
    return replace(pr, presses=tuple(p for p in pr.presses if (p.target, p.argument or "") != same))


# a download press's answer that names where the file is, in a frame the page opens: SSO's startup script creates a
# hidden iframe at /WebServices/ajaxDownloadFile.aspx (var iframeExport = document.createElement('iframe');
# iframeExport.src = '…'), which the session answers with the file once (pilot 10-26, F3: the 61 presses on web/595 and
# the 5 on site/100061 all answered 200 text/html, the page again with this script). A script may give a frame its
# address three ways (X.src = '…' for X an iframe it creates or one of the page's, document.getElementById('X').src =
# '…', $('#X').attr('src', '…')); the page may also come back with a hidden frame of its own filled in (osa's
# <iframe id="DownloadEventAtt" width="0" height="0">, empty on the page pressed: gap ④ of 11-08)
_SRC_SET = re.compile(r"""(?<![\w$.])(?P<who>[A-Za-z_$][\w$]*)\.src\s*=\s*(?P<q>['"])(?P<src>[^'"]+)(?P=q)""")
_ID_SRC_SET = re.compile(r"""document\.getElementById\(\s*(?P<q1>['"])(?P<who>[^'"]+)(?P=q1)\s*\)\.src\s*=\s*"""
                         r"""(?P<q>['"])(?P<src>[^'"]+)(?P=q)""")
_JQ_SRC_SET = re.compile(r"""\$\(\s*(?P<q1>['"])#(?P<who>[^'"\s]+)(?P=q1)\s*\)\.attr\(\s*(?P<q2>['"])src(?P=q2)\s*,\s*"""
                         r"""(?P<q>['"])(?P<src>[^'"]+)(?P=q)\s*\)""")
_MADE_FRAME = re.compile(r"""(?P<who>[A-Za-z_$][\w$]*)\s*=\s*document\.createElement\(\s*(['"])iframe\2\s*\)""",
                         re.IGNORECASE)
_HIDDEN_STYLE = re.compile(r"(?i)display\s*:\s*none|visibility\s*:\s*hidden")


def _same_host(url: str, base: str) -> bool:
    try:
        return (urlsplit(url).hostname or "").lower() == (urlsplit(base).hostname or "").lower()
    except ValueError:
        return False


def _frames(html: str) -> list[LexborNode]:
    return list(LexborHTMLParser(html or "").css("iframe"))


def _hidden(node: LexborNode) -> bool:
    a = node.attributes
    zero = {(a.get("width") or "").strip().lower(), (a.get("height") or "").strip().lower()} <= {"0", "0px"}
    return zero or "hidden" in a or bool(_HIDDEN_STYLE.search(a.get("style") or ""))


def file_followup(html: str, base: str, pressed: str | None = None) -> str | None:
    """The canonical address a download press's answer sends the browser to for the file, on the press's own host
    (the file is bound to this session: another host's address is no such export), or None: the page names none
    and is the answer itself. First an address a script of the answer gives a frame (_SRC_SET, _ID_SRC_SET,
    _JQ_SRC_SET: a frame the script creates, or one of the answer's <iframe> by its id or name), then a hidden
    <iframe> of the answer (width and height 0, hidden, display:none or visibility:hidden) whose address no frame of
    the page pressed had (pressed: that page's HTML; None: not known, every hidden frame counts). Only an address the
    crawler's own address rules let in (scope.judge ok, as file_redirect asks): the follow-up GET is sent as it is,
    and a sign-in, a log-out, a captcha or a back office the answer names is never fetched (review of 11-09); the next
    address named is taken instead, or none. What the follow-up GET brings is judged as any download's answer
    (Chains.file_answered): a wrong address can only end not_file, never ok. Gap ④ of 11-08, 【Claude 的执行办法】."""
    frames = _frames(html)
    names = {n.attributes.get(k) for n in frames for k in ("id", "name")} - {None, ""}
    made = {m.group("who") for m in _MADE_FRAME.finditer(html or "")}
    found = [(m.start(), m.group("src")) for m in _SRC_SET.finditer(html or "") if m.group("who") in made | names]
    found += [(m.start(), m.group("src")) for rx in (_ID_SRC_SET, _JQ_SRC_SET) for m in rx.finditer(html or "")
              if m.group("who") in names]
    before = {scope.canonical(n.attributes.get("src") or "", base) for n in _frames(pressed)} if pressed else set()
    srcs = [src for _at, src in sorted(found)]
    srcs += [n.attributes.get("src") or "" for n in frames if _hidden(n)]
    for src in srcs:
        url = scope.canonical(src.strip(), base) if src.strip() else None
        if url is not None and url not in before and _same_host(url, base) and scope.judge(url).ok:
            return url
    return None


def file_redirect(location: str | None, base: str) -> str | None:
    """Where a download press's redirect leads when that is its file (gap ① of 11-08, 【Claude 的执行办法】): the
    canonical Location when it stays on the press's own host (base: the address pressed) and the crawler's own address
    rules call it a file that may be fetched (scope.kind_of file or image: an attachment's or a picture's extension, or
    one of SSO's download programs, design §4.5; scope.judge ok). WebIdx's notice files (ucNews$rptFiles$ctlNN$
    lnkDownloadFile in a detail's answer) answer 302 to /WebServices/ajaxDownloadFileCourse.aspx?action=download&path=…,
    which a plain GET answers with the file (387 such rows of round one, every one done 200 a file). None for every
    other redirect: SSO's home, a sign-in page, an error page, another host: the session lapsed (§23.9 A2)."""
    url = scope.canonical(location, base) if location else None
    if url is None or not _same_host(url, base) or scope.kind_of(url) not in ("file", "image"):
        return None
    return url if scope.judge(url).ok else None


_HOME_LEAVES = ("default.aspx", "index.aspx")  # a site's home page by its address's last part (SSO's own and its units')


def _home(url: str) -> bool:
    """Whether an address is a site's home page: its host's root or its site key's (sso.nknu.edu.tw/software/), or a
    default.aspx or index.aspx anywhere (SSO's home, a WebIdx or cLv2 unit's home: default.aspx?webidx=N)."""
    try:
        path = urlsplit(url).path.lower().rstrip("/")
    except ValueError:
        return False
    root = scope.site_key(url).partition("/")[2]
    return path in ("", f"/{root}" if root else "") or path.rsplit("/", 1)[-1] in _HOME_LEAVES


def detail_redirect(location: str | None, base: str, list_url: str | None = None) -> str | None:
    """Where a detail press's redirect leads when that is the detail's own page (gap ⑪ of 11-08, 【Claude 的执行办法】:
    SSO may answer a software icon's press with 302 to campussoftware.aspx?cSoftID=N, which a GET answers): the
    canonical Location when it stays on the press's own host and site key (base: the address pressed), is neither the
    address pressed nor the list page (list_url), nor a site's home (_home), a sign-in page (scope.LOGIN_PATH) or the
    school's error page (outcome.error_signature), and the crawler's address rules let it in (scope.judge ok). None
    for every other redirect: the session lapsed (§23.9 A2)."""
    url = scope.canonical(location, base) if location else None
    if url is None or not _same_host(url, base) or scope.site_key(url) != scope.site_key(base):
        return None
    if url in (scope.canonical(base), scope.canonical(list_url or "")) or _home(url):
        return None
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    if scope.LOGIN_PATH.search(path) or outcome.error_signature(url) is not None:
        return None
    return url if scope.judge(url).ok else None


def press_redirect(kind: str, location: str | None, base: str, list_url: str | None = None) -> str | None:
    """Where a row's press answered with a redirect leads when that is what it was pressed for, one rule for both
    (gaps ① and ⑪ of 11-08): a download's file (file_redirect), a detail's page (detail_redirect); None for any other
    redirect of a row's press, the session lapsed (§23.9 A2), and for a kind no row has (the pager, a file button)."""
    if kind == "download":
        return file_redirect(location, base)
    if kind == "detail":
        return detail_redirect(location, base, list_url)
    return None


NOT_FILE = outcome.NOT_FILE  # action.reason of a download whose answer is no file: not_file:<why> (§23 A1, A2)
_TEXTUAL = ("html", "xml", "json", "text")


def not_file_reason(body: bytes, sniffed: str | None, name: str | None, text: str | None = None,
                    ctype: str | None = None) -> str | None:
    """Why what a download button brought back (its export GET's answer, or the press's own) is no file, as the
    action's reason (design §23 A1, 【Claude 的执行办法】): not_file:empty_body (①), not_file:saying:gone_page or
    not_file:saying:bad_link (③: the school's own words, profiles.file_says), not_file:no_name:<sniff> (②: no
    Content-Disposition name and no file format by its first bytes); None when it is a file."""
    if not body:
        return NOT_FILE + "empty_body"
    if sniffed in _TEXTUAL:
        said = profiles.file_says(text if text is not None else as_text(body, ctype))
        if said is not None:
            return f"{NOT_FILE}saying:{said.flag}"
    why = sniff.not_file(body, sniffed, name)
    return NOT_FILE + why if why else None


def empty_answer(row_title: str, html: str, rs: profiles.Rows | None) -> bool | None:
    """§10.1 nonempty for a detail a row's press brings back (review 10-26 phase 3), as profiles.empty_detail judges
    one got by GET: True when the row set's body area holds no text but the row's title (cLv2 writes the title inside
    it), no picture, link or frame, and no attachment link is on the page; None when the row set names no body area or
    the answer has none (another layout: nothing is claimed). 【Claude 的执行办法】"""
    if rs is None or not rs.detail_body:
        return None
    tree = LexborHTMLParser(html or "")
    body = tree.css(rs.detail_body)
    if not body:
        return None
    full = re.sub(r"\s+", "", norm(row_title))
    titles = [t for t in (full, full.rstrip(_ELLIPSIS).removesuffix("...")) if t]  # the list may cut the title
    for node in body:
        if node.css_first("img[src], a[href], iframe[src], embed, object"):
            return False
        text = re.sub(r"\s+", "", norm(node.text()))
        title = next((t for t in titles if t in text), "")
        if text.replace(title, "", 1) if title else text:
            return False
    if rs.detail_files:
        for node in tree.css(rs.detail_files):
            href = (node.attributes.get("href") or "").strip()
            if href and href != "#":
                return False
    return True


# ---------------------------------------------------------------- the archive (read only)

def read_response(root: Path, warc: str, offset: int, *,
                  payload: bool = True) -> tuple[str, int, dict[str, str], bytes]:
    """The response record at offset in root/warc (url.warc_offset and action.form_src_offset point at the response
    record, design §12): its address, status, headers (lowercase names) and body (Content-Encoding undone). A record
    that cannot be read there (no record, another kind, a broken or misplaced one: warcio's ArchiveLoadFailed and the
    like, which are no OSError) is a ValueError, so that every reader's except holds (review 10-26 phase 3). payload
    False: the body is not read (b""), for a reader that wants the headers only (the export's --reuse)."""
    with open(Path(root) / warc, "rb") as fh:
        fh.seek(offset)
        try:
            rec = next(iter(ArchiveIterator(fh)))
            # every record of this archive is WARC with its HTTP headers: warcio reads some runs of bytes as an old
            # ARC record (rec_type response, no HTTP headers, no body), which a misplaced offset may hit
            if not str(rec.format).startswith("warc") or rec.http_headers is None:
                raise ValueError(f"{warc}@{offset}: no WARC record starts there ({rec.format})")
            if rec.rec_type != "response":
                raise ValueError(f"{warc}@{offset} is a {rec.rec_type} record, not a response")
            url = rec.rec_headers.get_header("WARC-Target-URI") or ""
            status = int(rec.http_headers.get_statuscode() or 0) if rec.http_headers else 0
            headers = {k.lower(): v for k, v in rec.http_headers.headers} if rec.http_headers else {}
            body = rec.content_stream().read() if payload else b""
        except (StopIteration, EOFError, zlib.error, ArchiveLoadFailed, StatusAndHeadersParserException) as exc:
            raise ValueError(f"{warc}@{offset}: no record can be read there ({type(exc).__name__})") from exc
    return url, status, headers, body


def as_text(body: bytes, ctype: str | None) -> str:
    """Strict decoding first (CLAUDE.md: c.nknu's meta says big5, the page is UTF-8); a body that refuses it is read
    with bad bytes replaced (a form's hidden state is ASCII either way)."""
    try:
        return decode_body(body, ctype)
    except DecodeError:
        m = re.search(r"charset=[\"']?([\w.-]+)", ctype or "", re.IGNORECASE)
        for cs in ([m.group(1)] if m else []) + ["utf-8"]:
            try:
                return body.decode(cs, "replace")
            except LookupError:
                continue
        return body.decode("utf-8", "replace")


# ---------------------------------------------------------------- the chains (the frontier's action and listwalk)

@dataclass
class Stored:
    """What page_stored() did: the page's role by its profile (list: the crawler writes url.page_role), the presses
    planned, how its walk stands, and whether it is no list for a reason the page shows (content)."""

    page_role: str | None
    presses: int = 0
    ended_by: str | None = None
    unplanned: tuple[str, ...] = ()  # PageRead.unplanned: buttons to press that no press covers (要人看)
    # no list, its grid a table of content (PageRead.content, no pager, no GET pager link): the crawler takes off what
    # an older reading wrote of it as a list (its listwalk, page_role list; FIX 3, 11-06)
    content: bool = False
    texts: tuple[tuple[str, str], ...] = ()  # PageRead.texts: the unplanned buttons' texts (unpressed_entries)


@dataclass
class Followup:
    """What the crawler does after an answer: location, a redirect's target to queue as the list page's resource
    (link table: list page → it, text [postback]); files, the addresses a file button's POST gave, each queued as the
    page's attachment (kind0 file, page_res role file, need must); file_get, the address a download press's answer
    names for its file (file_followup: SSO's export iframe), to GET at once on the same connection and hand to
    file_answered(), the press staying active meanwhile; restart: the chain starts again from its GET (the list page's
    url row is pending again, reason chain_restart: nothing more to do)."""

    page_id: int
    location: str | None = None
    files: tuple[str, ...] = ()
    file_get: str | None = None
    restart: bool = False
    note: str = ""


@dataclass
class _Answer:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes
    sha256: str | None = None
    raw_headers: tuple = ()
    text: str | None = None
    warc_offset: int | None = None
    warc_len: int | None = None


class Chains:
    """The action and listwalk tables of one frontier: presses planned from stored pages, handed to the crawler one
    at a time per queue, and their answers. root: the archive directory (url.warc and action.form_src_warc are
    relative to it). read: (warc, offset) -> (url, status, headers, body), the archive's response record (tests may
    give their own)."""

    def __init__(self, frontier: Frontier, root: Path, *, clock: Callable[[], float] = wall,
                 read: Callable[[str, int], tuple[str, int, dict[str, str], bytes]] | None = None):
        self.f = frontier
        self.root = Path(root)
        self.clock = clock
        self.read = read or (lambda warc, offset: read_response(self.root, warc, offset))

    def _year(self) -> int:
        """The academic year of today (Asia/Taipei) by the chains' clock: the year a menu is sent with (gap ⑩ of 11-08,
        the owner 11-09 「现在补 115」)."""
        return this_year(self.clock())

    # ---- a page stored by GET
    def page_stored(self, row: sqlite3.Row, html: str, warc: str | None, warc_offset: int | None,
                    page: links.PageLinks | None = None) -> Stored:
        """A page of a round-1 site was fetched by GET and archived (after the crawler wrote its own page_res: this
        adds to it). Its profile's presses become actions of a new epoch (the leftovers of an older one superseded),
        its rows item entries of page_res, its walk a listwalk row. A page with GET pager links (no profile, or a
        profile that walks by GET) gets its walk from get_walk(). The profile is the platform the page's own address
        is written on (profiles.profile_for, gaps ② ③ of 11-08); the site it counts for (url.owner_site) keys its
        rows, its actions (action.site: a run's --sites) and its walk. Its menus are sent with today's academic year
        (gap ⑩ of 11-08)."""
        site = row["owner_site"] or scope.site_key(row["url"])
        prof = profiles.profile_for(row["url"])
        has_pagers = bool(page.pagers) if page is not None else row["page_role"] == "list"
        if prof is None:
            ended = self.get_walk(row["id"]) if has_pagers else None
            self._after_get_page(row)
            return Stored("list" if has_pagers else None, 0, ended)
        pr = read_page(prof, row["url"], html, site=site, list_url=row["url"], page=page, year=self._year())
        list_id = row["id"]
        if (warc is None or warc_offset is None) and (pr.presses or pr.next is not None):
            # nothing to build a form from: its buttons cannot be pressed, and its walk claims no end (a crawler bug)
            self.f.event("error", "postback_no_warc", f"{row['url']}: stored without a WARC position, no press planned",
                         row["host"], row["url"])
            if pr.walk in ("postback", "one_get"):
                self._walk(list_id, site, prof.name, 1, pages=1, rows=len(pr.rows), claimed=pr.claimed,
                           ended="walking", note="no_warc")
                self._no_end(list_id)
            nobody = tuple(dict.fromkeys((*pr.unplanned, *(button_key(p.target, p.argument)
                                                           for p in (*pr.presses, *([pr.next] if pr.next else []))))))
            return Stored("list" if pr.walk != "none" else None, 0, None, nobody, texts=pr.texts)
        redo = self._redo(list_id)
        if redo is not None:  # `retry download` put this list back for these downloads only (§23 A3)
            self._close_look(list_id, "redo")
            return self._redo_stored(row, pr, redo, warc, warc_offset, has_pagers)
        epoch = self._new_epoch(list_id)
        n, ended = self._plan(list_id, epoch, pr, row, warc, warc_offset, pages=1)
        if pr.walk in ("postback", "one_get"):
            self._walk(list_id, site, prof.name, epoch, pages=1, rows=len(pr.rows), claimed=pr.claimed,
                       ended=ended, note="")
        elif pr.walk == "get" or has_pagers:
            ended = self.get_walk(list_id)
            self._after_get_page(row)
            if ended is None and pr.walk == "get" and not has_pagers:
                # no pager at all: only one page (§7.5 step 7: rows > 0, or the list's own words for none), judged
                # as one GET's list is; a link that looks like a page of a list (pager_candidates: a pager box nobody
                # reads) makes it mismatch:unknown_button, never last_page (review 10-26 phase 3)
                cands = pager_candidates(row["url"], page) if page is not None else ()
                ended = list_end(replace(pr, current=1, max_seen=1, unknown_pagers=(*pr.unknown_pagers, *cands)),
                                 self._rows_unique(list_id))
                self._walk(list_id, site, prof.name, epoch, pages=1, rows=len(pr.rows), claimed=pr.claimed,
                           ended=ended, note="one page")
        if pr.walk == "none" and not has_pagers:
            self._close_look(list_id, "no_list")  # a look whose list page shows no list: the walk's record stays
        return Stored("list" if pr.walk != "none" or has_pagers else None, n, ended, pr.unplanned,
                      content=pr.content and pr.walk == "none" and not has_pagers, texts=pr.texts)

    def not_a_list(self, page_id: int) -> None:
        """A page read as a table of content (Stored.content: no list by its profile, no GET pager link; FIX 3, 11-06):
        what an older reading wrote of it as a list goes, its listwalk (列表没翻完) and its page_role list (列表没核对).
        Only on that proof: a page whose answer came back without its grid (no row set at all) keeps what its last
        reading said (CLAUDE.md: 保留旧值). 【Claude 的执行办法】"""
        if self._looking(page_id) is not None:  # one reading of a look proves nothing: the walk's record stays
            self._close_look(page_id, "not_a_list")
            return
        self.f.x("UPDATE url SET page_role=NULL WHERE id=? AND page_role='list'", (page_id,))
        self.f.x("DELETE FROM listwalk WHERE list_id=?", (page_id,))

    def _new_epoch(self, list_id: int) -> int:
        """A new GET of a list starts a new epoch: what is left of the older one is superseded (§7.5 step 9)."""
        with self.f.lock:
            prev = self.f.conn.execute("SELECT max(epoch) FROM action WHERE list_id=? AND kind NOT IN ('click', 'data')",
                                       (list_id,)).fetchone()[0]
            if not prev:
                return 1
            self.f.conn.execute("UPDATE action SET state='superseded', reason='new_epoch' WHERE list_id=? AND "
                                "kind NOT IN ('click', 'data') AND state IN ('pending','active')", (list_id,))
            return prev + 1

    def _plan(self, list_id: int, epoch: int, pr: PageRead, list_row, warc: str, offset: int, *, pages: int,
              base: str | None = None, only: frozenset[tuple[str, str]] | None = None,
              left: list[str] | None = None, seen: set[int] | None = None) -> tuple[int, str | None]:
        """The presses of one page of the chain (§7.5 step 2), the page's rows as item entries, the next page when
        there is one. Returns (presses planned, the walk's end at this page, None while it goes on). only: the
        (kind, row_key) presses that may be planned anew (`retry download`, §23 A3: _redo_stored); every other press
        points at what its row has already, the next page is not pressed, and a button no press of any epoch covers
        goes to left (its button key, for a person). seen: the done details of older epochs read again so far in
        this planning (_detail_files), each read once: the answers of a menu's presses name one another (gap ⑩ of
        11-08) 【Claude 的执行办法】."""
        seen = set() if seen is None else seen
        site = list_row["owner_site"] or scope.site_key(list_row["url"])
        host, queue = self._place(base or list_row["url"], list_row)
        if pages >= 1 and only is None:  # a page of the list itself (not a detail's answer), walked (not a redo)
            self._keep_rows(list_id, pr, base or list_row["url"], self.clock())
        entries = []
        for r in pr.rows:
            res_id = None
            if r.href and (canon := scope.canonical(r.href, base or list_row["url"])) is not None:
                hit = self.f.row(canon)
                res_id = hit["id"] if hit is not None else None
            entries.append({"res_key": f"item:{r.key}", "how": "get" if r.href else "postback", "role": "item",
                            "need": "no", "why": "item", "res_id": res_id, "text": r.title[:100]})
        n = 0
        done_details = []
        for p in pr.presses:
            aid = self._press(list_id, epoch, p, site, pr, host, queue, warc, offset, only=only)
            if aid is None:  # not to be pressed now, and never pressed before
                if left is not None:
                    left.append(button_key(p.target, p.argument))
                continue
            entries.append({"res_key": f"pb:{p.kind}:{p.row_key}", "how": "postback", "role": p.kind,
                            "need": "must", "action_id": aid, "text": p.text[:100]})
            n += 1
            if p.kind == "detail":
                done_details.append(aid)
        ended = None
        if only is not None:  # the walk is not walked again: its end stays what it was
            pass
        elif pr.next is not None and pr.walk == "postback":
            look = self._looking(list_id) if pages >= 1 and only is None else None
            if pages >= MAX_PAGES:
                ended = "max_pages"
            elif (look is not None and pages >= look["furthest"]
                  and not self._news(list_id, pr, base or list_row["url"], look["started"])):
                ended = SEEN_PAGE  # nothing new on this page: the next is not pressed (design §7.2)
            else:
                aid = self._press(list_id, epoch, pr.next, site, pr, host, queue, warc, offset)
                entries.append({"res_key": f"pb:pager:{pr.next.row_key}", "how": "postback", "role": "pager",
                                "need": "must", "action_id": aid, "text": pr.next.text[:100]})
                n += 1
        elif pr.walk in ("postback", "one_get"):
            self.f.add_page_res(list_id, entries)
            entries = []
            ended = list_end(pr, self._rows_unique(list_id))
        if entries:
            self.f.add_page_res(list_id, entries)
        for aid in done_details:
            n += self._detail_files(aid, list_id, epoch, list_row, redo=only is not None, left=left, seen=seen)
        return n, ended if pr.walk in ("postback", "one_get") else None

    # ---- a look at a list (periodic updates, stage 1: design 2026-11-16 §7.2)
    def _looking(self, list_id: int, mode: str = LOOK) -> sqlite3.Row | None:
        got = self.f.q("SELECT * FROM list_look WHERE list_id=? AND mode=? AND ended_at IS NULL", (list_id, mode))
        return got[0] if got else None

    def _walk_row(self, list_id: int) -> sqlite3.Row | None:
        """The walk's numbers so far (pages, rows, claimed): an open look's own, else listwalk's; None for neither."""
        look = self._looking(list_id)
        if look is not None:
            return look
        lw = self.f.q("SELECT * FROM listwalk WHERE list_id=?", (list_id,))
        return lw[0] if lw else None

    def _no_end(self, list_id: int) -> None:
        """A page stored with no WARC position: the walk claims no end (listwalk NULL); a look closes no_warc."""
        if self._looking(list_id) is not None:
            self._close_look(list_id, "no_warc")
        else:
            self.f.x("UPDATE listwalk SET ended_by=NULL, ok=0 WHERE list_id=?", (list_id,))

    def look(self, list_id: int) -> bool:
        """A look at a list walked by button (Task 3 of stage 1): its first pages read again, the next pressed while a
        page brings news (_news), the record of its last whole walk kept (list_look: the listwalk row and the list
        page's page_res, put back at any end but a whole walk). Only on a list whose walk ended (not walking, not with
        no end), with no press waiting and its page got or failed (a walk cut off is resume()'s to finish), and none
        already open. Returns whether it opened."""
        lw = self.f.q("SELECT * FROM listwalk WHERE list_id=?", (list_id,))
        row = self.f.row_by_id(list_id)
        if (not lw or lw[0]["ended_by"] in (None, "walking") or row is None or row["state"] not in ("done", "failed")
                or self.f.q("SELECT 1 FROM list_look WHERE list_id=? AND ended_at IS NULL", (list_id,))
                or self.f.q("SELECT 1 FROM action WHERE list_id=? AND kind NOT IN ('click', 'data') AND state IN "
                            "('pending', 'active') LIMIT 1", (list_id,))):
            return False
        epoch = (self.f.q("SELECT max(epoch) FROM action WHERE list_id=? AND kind NOT IN ('click', 'data')",
                          (list_id,))[0][0] or 0) + 1  # the next GET's (_new_epoch)
        res = [dict(r) for r in self.f.q("SELECT * FROM page_res WHERE page_id=?", (list_id,))]
        self.f.x("INSERT OR REPLACE INTO list_look(list_id, mode, started, epoch_from, saved_walk, saved_res) VALUES "
                 "(?,?,?,?,?,?)", (list_id, LOOK, self.clock(), epoch, json.dumps([dict(lw[0])], ensure_ascii=False),
                                   json.dumps(res, ensure_ascii=False)))
        self.restart_chain(list_id, "look", walk=False)
        return True

    def _news(self, list_id: int, pr: PageRead, base: str, started: float) -> int:
        """The rows of a page that are news since a look started: read for the first time, or changed (list_row,
        written by _keep_rows before this). Judged against the look's start, so a look started again (resume, a
        failed press) still sees what its first pass saw."""
        idents = list(dict.fromkeys((scope.canonical(r.href, base) if r.href else None) or f"key:{r.key}"
                                    for r in pr.rows))
        if not idents:
            return 0
        marks = ",".join("?" * len(idents))
        return self.f.q(f"SELECT count(*) FROM list_row WHERE list_id=? AND ident IN ({marks}) AND (first_seen >= ? OR "
                        "changed_at >= ?)", (list_id, *idents, started, started))[0][0]

    def _whole(self, look: sqlite3.Row, list_id: int, pages: int, ended: str) -> bool:
        """A look that reached the list's end is a whole walk (listwalk written) when it reached the pages the last
        whole walk had, or rows within max(10, 10%) of its rows; a list that shrank more, or a page saying there is
        nothing where the last walk had rows, is no whole walk (design §7.5: kept, and a warning)."""
        saved = json.loads(look["saved_walk"] or "[]")
        last = saved[0] if saved else {}
        had = last.get("rows_unique") or 0
        if ended == "no_rows_ok" and had > 0:
            return False
        if pages >= (last.get("pages") or 0):
            return True
        return self._rows_unique(list_id) >= had - max(10, had // 10)

    def _close_look(self, list_id: int, ended: str, *, whole: bool = False, note: str = "") -> None:
        """An open look (either mode) ends so. Unless it walked the list whole: the whole walk's listwalk rows and the
        page's page_res it saved come back (tonight's entries stand, but the saved pager entries replace tonight's),
        and the look's failed or mismatched presses no longer count (superseded, 'look: <reason>': not in
        _may_restart, not in _press's two failures). Any end but seen_page and a whole walk is an event."""
        got = self.f.q("SELECT * FROM list_look WHERE list_id=? AND ended_at IS NULL", (list_id,))
        if not got:
            return
        lk = got[0]
        self.f.x("UPDATE list_look SET ended_at=?, ended_by=?, note=coalesce(?, note) WHERE list_id=?",
                 (self.clock(), ended, (note or "")[:300] or None, list_id))
        if whole:
            return
        with self.f.lock:
            for w in json.loads(lk["saved_walk"] or "[]"):
                cols = list(w)
                self.f.conn.execute(f"INSERT OR REPLACE INTO listwalk({', '.join(cols)}) VALUES "
                                    f"({', '.join('?' * len(cols))})", tuple(w.values()))
            saved = json.loads(lk["saved_res"] or "[]")
            if saved:
                self.f.conn.execute("DELETE FROM page_res WHERE page_id=? AND res_key LIKE 'pb:pager:%'", (list_id,))
                self.f.add_page_res(list_id, saved)
            self.f.conn.execute("UPDATE action SET state='superseded', reason='look: ' || coalesce(reason, '') WHERE "
                                "list_id=? AND epoch >= ? AND kind NOT IN ('click', 'data') AND state IN ('failed', "
                                "'mismatch')", (list_id, lk["epoch_from"] or 0))
        if ended != SEEN_PAGE:
            row = self.f.row_by_id(list_id)
            self.f.event("warn", "look_ended", f"{lk['mode']} ended {ended}: the last whole walk's record kept"
                         + (f" ({note})" if note else ""), row["host"] if row is not None else None,
                         row["url"] if row is not None else None)

    def close_stale_looks(self) -> int:
        """Looks that cannot end by themselves (their page's GET never reached page_stored: a 404, a sign-in page, no
        answer three times; its row neither pending nor active, no press waiting) end not_got:<state>/<status or
        reason>, the whole walk's record kept. At each run's start and before a selection. Returns how many."""
        n = 0
        for lk in self.f.q("SELECT l.list_id, u.state, u.status, u.reason FROM list_look l JOIN url u ON u.id = "
                           "l.list_id WHERE l.ended_at IS NULL AND u.state NOT IN ('pending', 'active')"):
            if self.f.q("SELECT 1 FROM action WHERE list_id=? AND kind NOT IN ('click', 'data') AND state IN "
                        "('pending', 'active') LIMIT 1", (lk["list_id"],)):
                continue
            self._close_look(lk["list_id"], f"not_got:{lk['state']}/{lk['status'] or lk['reason'] or ''}")
            n += 1
        return n

    def look_get(self, page_id: int, *, started: float | None = None, saved_walk: str | None = None,
                 after: int | None = None) -> bool:
        """A look at a page of a list paged by GET (Task 4 of stage 1): the page put back to be fetched again; the
        group's listwalk is not written while the look is open (get_walk), and comes back as saved unless the looked
        pages came back well. A page that brings news puts the next page back the same way (_after_get_page). Only on
        a page whose group's walk ended, got or failed, with no look open in its group. started, saved_walk: a look
        going on to the next page keeps the first page's; after: the page whose look hands over to this one (its
        own open look does not count). Returns whether it opened."""
        row = self.f.row_by_id(page_id)
        group, _edges = self._group(page_id)
        marks = ",".join("?" * len(group))
        walks = [dict(r) for r in self.f.q(f"SELECT * FROM listwalk WHERE list_id IN ({marks}) ORDER BY list_id",
                                           sorted(group))]
        if (row is None or row["state"] not in ("done", "failed") or not walks
                or any(w["ended_by"] in (None, "walking") for w in walks)
                or self.f.q(f"SELECT 1 FROM list_look WHERE ended_at IS NULL AND list_id IN ({marks}) AND list_id "
                            f"IS NOT ? LIMIT 1", (*sorted(group), after))):
            return False
        res = [dict(r) for r in self.f.q("SELECT * FROM page_res WHERE page_id=?", (page_id,))]
        self.f.x("INSERT OR REPLACE INTO list_look(list_id, mode, started, saved_walk, saved_res) VALUES (?,?,?,?,?)",
                 (page_id, LOOK_GET, self.clock() if started is None else started,
                  saved_walk if saved_walk is not None else json.dumps(walks, ensure_ascii=False),
                  json.dumps(res, ensure_ascii=False)))
        self.f.put_back(page_id, "look_get")
        return True

    def _after_get_page(self, row: sqlite3.Row) -> None:
        """A page of a GET-paged list stored while a look is open on it: news (_get_news) puts the next page back and
        the look goes on there; none closes it seen_page. The group's walk is written (get_walk) once no look is open
        in it."""
        look = self._looking(row["id"], LOOK_GET)
        if look is None:
            return
        nxt = self._next_get_page(row["id"]) if self._get_news(row["id"], look["started"]) else None
        self.f.x("UPDATE list_look SET pages=1 WHERE list_id=?", (row["id"],))
        if nxt is not None and self.look_get(nxt, started=look["started"], saved_walk=look["saved_walk"],
                                             after=row["id"]):
            self._close_look(row["id"], "went_on", whole=True)
            return
        self._close_look(row["id"], SEEN_PAGE, whole=True)
        self.get_walk(row["id"])

    def _get_news(self, page_id: int, started: float) -> int:
        """What a GET-paged list's page brings since a look started: links of the page to pages or files first
        admitted since (whoever found them; a notice moved down from the page before is no news), and its rows read
        changed (list_row, a page's own rows when its platform reads them). The page's pager links are not news."""
        new = self.f.q("SELECT count(DISTINCT u.id) FROM link l JOIN url u ON u.url = l.dst WHERE l.src=? AND "
                       "u.added_at >= ? AND u.kind IN ('page', 'file') AND u.id NOT IN (SELECT res_id FROM page_res "
                       "WHERE page_id=? AND role='pager' AND res_id IS NOT NULL)", (page_id, started, page_id))[0][0]
        changed = self.f.q("SELECT count(*) FROM list_row WHERE list_id=? AND changed_at >= ?",
                           (page_id, started))[0][0]
        return new + changed

    def _next_get_page(self, page_id: int) -> int | None:
        """The page after this one in its GET-paged list (get_walk's numbering: the one numbered one more, else where
        its 「下一頁」 leads), or None."""
        _group, edges = self._group(page_id)
        num, nexts = _numbering(edges)
        me = num.get(page_id)
        if me is not None:
            for dst, n in sorted(num.items()):
                if n == me + 1:
                    return dst
        return next((dst for src, dst in nexts if src == page_id), None)

    def _keep_rows(self, list_id: int, pr: PageRead, base: str, at: float, *, put_back: bool = True) -> int:
        """Each row of a list page as read now, kept in list_row (periodic updates, stage 1: design 2026-11-16 §7.2): a
        row never read is added; a row read before whose title, date or category differ is changed (changed_at) and,
        with put_back, the detail its link names goes back to be fetched again when it was got (row_changed,
        Frontier.put_back); first_seen and last_seen take in `at`. A row on the page twice (a pinned one) once.
        Returns the rows changed."""
        changed = 0
        done: set[str] = set()
        with self.f.lock:
            c = self.f.conn
            for r in pr.rows:
                href = scope.canonical(r.href, base) if r.href else None
                ident = href or f"key:{r.key}"
                if ident in done:
                    continue
                done.add(ident)
                old = c.execute("SELECT title, date, category FROM list_row WHERE list_id=? AND ident=?",
                                (list_id, ident)).fetchone()
                if old is None:
                    c.execute("INSERT INTO list_row(list_id, ident, row_key, href, title, date, category, first_seen, "
                              "last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                              (list_id, ident, r.key, href, r.title, r.date, r.category, at, at))
                    continue
                moved = (old["title"], old["date"], old["category"]) != (r.title, r.date, r.category)
                c.execute("UPDATE list_row SET row_key=?, href=?, title=?, date=?, category=?, "
                          "first_seen=min(first_seen, ?), last_seen=max(last_seen, ?), "
                          "changed_at=CASE WHEN ? THEN ? ELSE changed_at END WHERE list_id=? AND ident=?",
                          (r.key, href, r.title, r.date, r.category, at, at, moved, at, list_id, ident))
                if moved:
                    changed += 1
                    got = self._fetched_row(href) if (put_back and href) else None
                    if got is not None:
                        self.f.put_back(got["id"], "row_changed")
        return changed

    def _fetched_row(self, url: str) -> sqlite3.Row | None:
        """The url row the crawler fetched for an address: its own, else the address in the one scheme its host
        answers on (host.scheme_only, crawler._scheme_rewrite); a row moved to its other scheme (moved_scheme) gives
        the row it moved to (its final_url)."""
        row = self.f.row(url)
        if row is None:
            h = self.f.host((urlsplit(url).hostname or "").lower())
            only = h["scheme_only"] if h is not None else None
            if only and not url.startswith(f"{only}://"):
                row = self.f.row(f"{only}{url[url.index('://'):]}")
        if row is not None and row["state"] == "skipped" and row["reason"] == "moved_scheme" and row["final_url"]:
            row = self.f.row(row["final_url"]) or row
        return row

    def _detail_files(self, action_id: int, list_id: int, epoch: int, list_row, *, redo: bool = False,
                      left: list[str] | None = None, seen: set[int] | None = None) -> int:
        """A detail pressed well in an older epoch is not pressed again; its own buttons (its files) are read again
        from its archived answer, so the list page still names them after its page_res was written anew (a done file
        stays done, one that failed once is pressed again). redo (`retry download`, §23 A3): nothing is pressed anew
        from an archived answer (its form state is an old POST's: never sent again as it was), only named. A menu's
        press is read again with the year it asked (its argument) and the list's row keys: an option its answer
        offers that no press covers is planned from that answer (gap ⑩ of 11-08). seen: _plan's; each detail is
        read again once in one planning."""
        if seen is not None:
            if action_id in seen:
                return 0
            seen.add(action_id)
        got = self.f.q("SELECT * FROM action WHERE id=?", (action_id,))
        if not got:
            return 0
        a = got[0]
        if a["state"] != "done" or a["epoch"] == epoch or not a["warc"] or a["warc_offset"] is None \
                or a["sniff"] != "html":
            return 0
        prof, _rs = self._profile(a)
        if prof is None:
            return 0
        try:
            url, _status, headers, body = self.read(a["warc"], a["warc_offset"])
        except (OSError, ValueError, StopIteration):
            return 0
        site = list_row["owner_site"] or scope.site_key(list_row["url"])
        year = self._asked_year(a, prof)
        pr = _without_pressed(read_page(prof, url, as_text(body, headers.get("content-type")), site=site,
                                        list_url=list_row["url"] if year is not None else None,
                                        parent_key=a["row_key"], step=a["step"], walks=("none",), year=year), a)
        n, _end = self._plan(list_id, epoch, pr, list_row, a["warc"], a["warc_offset"], pages=0, base=url,
                             only=frozenset() if redo else None, left=left, seen=seen)
        self._unpressed(list_id, pr.unplanned_at, pr.texts)
        return n

    @staticmethod
    def _asked_year(action, prof: profiles.Profile | None) -> int | None:
        """The academic year a menu's press asked for (its rule: the action's row set; its year select's value in the
        argument), or None for any other press (gap ⑩ of 11-08)."""
        rowset = (action["profile"] or "").partition("/")[2]
        m = next((x for x in (prof.menus if prof is not None else ()) if x.name == rowset), None)
        return profiles.menu_year(m, action["argument"]) if m is not None else None

    def rebuild(self, list_id: int) -> int:
        """The list page's press entries again from the action table, no epoch opened and nothing pressed: for a
        reading of the page again that sends nothing (rejudge), after the crawler wrote the page's own page_res anew.
        Each row's press: a done one, else the newest that is not superseded, else the newest. Returns the entries."""
        best: dict[tuple[str, str], sqlite3.Row] = {}
        for a in self.f.q("SELECT * FROM action WHERE list_id=? AND kind NOT IN ('click', 'data') ORDER BY epoch, id", (list_id,)):
            key = (a["kind"], a["row_key"])
            old = best.get(key)
            if old is None or old["state"] != "done" and (a["state"] == "done" or a["state"] != "superseded"
                                                            or old["state"] == "superseded"):
                best[key] = a
        entries = [{"res_key": f"pb:{a['kind']}:{a['row_key']}", "how": "postback", "role": a["kind"],
                    "need": "must", "action_id": a["id"], "text": (a["row_text"] or "")[:100]} for a in best.values()]
        self.f.add_page_res(list_id, entries)
        return len(entries)

    def _place(self, url: str, list_row) -> tuple[str, str]:
        host = (urlsplit(url).hostname or list_row["host"]).lower()
        h = self.f.host(host) or self.f.host(list_row["host"])
        return host, (h["queue"] if h is not None else "?")

    def _press(self, list_id: int, epoch: int, p: Press, site: str, pr: PageRead, host: str, queue: str,
               warc: str, offset: int, only: frozenset[tuple[str, str]] | None = None) -> int | None:
        """One action for a press, unless the row was pressed well before (a row's done press, any epoch: §7.5 step
        8, not pressed again) or failed or mismatched twice (left for a person), or is planned already this epoch (a
        pinned row on every page). The pager is pressed again in each epoch. only (§23 A3): a press not in it is never
        planned; its row's press as rebuild() would pick it, or None when it has none."""
        with self.f.lock:
            c = self.f.conn
            if only is not None and (p.kind, p.row_key) not in only:
                had = c.execute("SELECT id FROM action WHERE list_id=? AND kind=? AND row_key=? ORDER BY state='done' "
                                "DESC, state<>'superseded' DESC, epoch DESC, id DESC LIMIT 1",
                                (list_id, p.kind, p.row_key)).fetchone()
                return had[0] if had is not None else None
            if p.kind != "pager":
                done = c.execute("SELECT id FROM action WHERE list_id=? AND kind=? AND row_key=? AND state='done' "
                                 "ORDER BY epoch DESC, id DESC LIMIT 1", (list_id, p.kind, p.row_key)).fetchone()
                if done is not None:
                    return done[0]
            tries = c.execute("SELECT id FROM action WHERE list_id=? AND kind=? AND row_key=? AND state IN "
                              "('failed','mismatch') ORDER BY epoch DESC, id DESC", (list_id, p.kind, p.row_key)
                              ).fetchall()
            if len(tries) >= 2:
                return tries[0][0]
            same = c.execute("SELECT id FROM action WHERE list_id=? AND epoch=? AND kind=? AND row_key=?",
                             (list_id, epoch, p.kind, p.row_key)).fetchone()
            if same is not None:
                return same[0]
            # action.profile: the platform and the list's row set; a detail's own row set (whose detail_title checks its
            # answer: a folder button of a software page, which shows no list, gap ⑪ of 11-08)
            rowset = (p.rowset if p.kind == "detail" else "") or pr.rowset or "-"
            cur = c.execute(
                "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, "
                "form_src_offset, form_id, target, argument, row_key, row_text, state, created) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)",
                (list_id, epoch, p.step, p.kind, queue, host, site, f"{pr.profile}/{rowset}", warc, offset,
                 p.form_id, p.target, p.argument, p.row_key, p.text[:300], self.clock()))
            return cur.lastrowid

    def _unpressed(self, list_id: int, buttons: Iterable[tuple[str, str]],
                   texts: Iterable[tuple[str, str]] = ()) -> int:
        """Buttons to press in a press's answer (a detail, a next page) that no press covers (PageRead.unplanned_at):
        written on the list page as unhandled (要人看, the same why as a GET page's: UNPRESSED), never dropped unseen
        (review 10-26 phase 3), each with its text (texts: PageRead.texts). A button the list's presses press already
        (any epoch: an answer may show the whole list again) is no new one. Returns the entries written.
        【Claude 的执行办法】"""
        buttons = list(buttons)
        if not buttons:
            return 0
        planned = {(r[0], r[1]) for r in self.f.q("SELECT DISTINCT target, argument FROM action WHERE list_id=? AND "
                                                  "kind NOT IN ('click', 'data')", (list_id,))}
        keys = tuple(dict.fromkeys(button_key(t, a) for t, a in buttons if (t, a) not in planned))
        if keys:
            self.f.add_page_res(list_id, unpressed_entries(keys, texts))
        return len(keys)

    def _rows_unique(self, list_id: int) -> int:
        return self.f.q("SELECT count(*) FROM page_res WHERE page_id=? AND res_key LIKE 'item:%'", (list_id,))[0][0]

    def _walked(self, list_id: int) -> frozenset[str]:
        """The row keys of the pages this walk read so far: the list page's item entries (the GET that opened the
        epoch wrote its page_res anew, and each page's answer adds its own: _plan)."""
        return frozenset(r[0][len("item:"):] for r in self.f.q(
            "SELECT res_key FROM page_res WHERE page_id=? AND res_key LIKE 'item:%'", (list_id,)))

    def _walked_links(self, list_id: int) -> dict[str, str]:
        """The detail page each walked row links, by its row key: the item entry's res_id, the url row _plan found
        for the row's link (none when the link had no row then)."""
        return {r[0][len("item:"):]: r[1] for r in self.f.q(
            "SELECT p.res_key, u.url FROM page_res p JOIN url u ON u.id = p.res_id WHERE p.page_id=? AND "
            "p.res_key LIKE 'item:%'", (list_id,))}

    def _walk(self, list_id: int, site: str, profile: str, epoch: int, *, pages: int, rows: int | None,
              claimed: int | None, ended: str | None, note: str) -> None:
        """The walk's state after a page (listwalk). While a look is open on the list (periodic updates, stage 1) it
        is the look's (list_look): an end the walk reached whole writes listwalk as any walk and closes the look;
        any other end closes it, listwalk and the page's page_res as the last whole walk left them."""
        ended = ended or "walking"
        look = self._looking(list_id)
        if look is not None:
            self.f.x("UPDATE list_look SET pages=?, rows=?, claimed=?, furthest=max(furthest, ?) WHERE list_id=?",
                     (pages, rows, claimed, pages, list_id))
            if ended == "walking":
                return
            whole = ended in END_OK and self._whole(look, list_id, pages, ended)
            if whole:
                self._write_walk(list_id, site, profile, epoch, pages=pages, rows=rows, claimed=claimed, ended=ended,
                                 note=note)
            self._close_look(list_id, ended, whole=whole, note=note)
            return
        self._write_walk(list_id, site, profile, epoch, pages=pages, rows=rows, claimed=claimed, ended=ended,
                         note=note)

    def _write_walk(self, list_id: int, site: str, profile: str, epoch: int, *, pages: int, rows: int | None,
                    claimed: int | None, ended: str, note: str) -> None:
        ok = None if ended == "walking" else int(ended in END_OK)
        self.f.x("INSERT OR REPLACE INTO listwalk(list_id, site, profile, epoch, pages, rows, rows_unique, claimed, "
                 "ended_by, ok, note, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (list_id, site, profile, epoch, pages, rows, self._rows_unique(list_id), claimed, ended, ok,
                  note or None, self.clock()))

    # ---- handing presses to the crawler
    def next_action(self, queue: str, now: float | None = None, sites: Iterable[str] | None = None,
                    ids: Iterable[int] | None = None) -> sqlite3.Row | None:
        """The next press of a queue (design §6.7: a chain's next step goes before the queue's url rows), marked
        active: a chain's rows before its next page, chains in the order they began; hosts in cool-down or within
        their Crawl-delay wait. sites: only these sites' (a run given --sites); ids: only the presses of these url
        rows, a press going with its list page's (action.list_id: a run given --only)."""
        now = self.clock() if now is None else now
        only, args = "", [queue, now, now, now]
        if sites is not None:
            sites = sorted(sites)
            only = f" AND a.site IN ({','.join('?' * len(sites))})"
            args += sites
        by_id, id_args = ids_clause("a.list_id", ids)
        with self.f.lock:
            row = self.f.conn.execute(
                f"""SELECT a.* FROM action a LEFT JOIN host h ON h.host = a.host
                    WHERE a.queue = ? AND a.state = 'pending' AND coalesce(h.cooldown_until, 0) <= ?
                      AND coalesce(h.not_before, 0) <= ? AND a.next_at <= ?{only}{by_id}
                    ORDER BY a.kind = 'click', a.list_id, a.epoch, a.step, a.kind = 'pager', a.id LIMIT 1""",
                [*args, *id_args]).fetchone()
            if row is not None:
                self.f.conn.execute("UPDATE action SET state='active' WHERE id=?", (row["id"],))
            return row

    def due_queues(self, now: float | None = None, sites: Iterable[str] | None = None,
                   ids: Iterable[int] | None = None) -> list[str]:
        """The queues with a press due now (design §6.7: a chain's next step is that queue's work too), none paused
        (its own pause, its network's or every queue's): the run starts their workers as it does for url rows. ids:
        only the presses of these url rows (action.list_id: a run given --only)."""
        now = self.clock() if now is None else now
        only, args = "", [now, now, now]
        if sites is not None:
            sites = sorted(sites)
            only = f" AND a.site IN ({','.join('?' * len(sites))})"
            args += sites
        by_id, id_args = ids_clause("a.list_id", ids)
        queues = [r[0] for r in self.f.q(
            f"""SELECT DISTINCT a.queue FROM action a LEFT JOIN host h ON h.host = a.host
                WHERE a.state = 'pending' AND coalesce(h.cooldown_until, 0) <= ? AND coalesce(h.not_before, 0) <= ?
                  AND a.next_at <= ? AND a.queue NOT IN ('?', '', 'dns-fail'){only}{by_id}""", [*args, *id_args])]
        return [q for q in queues if self.f.paused_until(q) <= now]

    def waiting(self, sites: Iterable[str] | None = None,
                ids: Iterable[int] | None = None) -> tuple[int, float | None, list[sqlite3.Row]]:
        """For the run's end (review 10-26 B1): how many presses (of these sites; of these url rows, by
        action.list_id, for a run given --only) are pending or active, when the soonest pending one is due (its
        host's cool-down and Crawl-delay, its queue's pauses), and the active ones (with no worker alive, a crash's:
        Crawler._should_end)."""
        only, args = "", []
        if sites is not None:
            sites = sorted(sites)
            only = f" AND a.site IN ({','.join('?' * len(sites))})"
            args = sites
        by_id, id_args = ids_clause("a.list_id", ids)
        only, args = only + by_id, [*args, *id_args]
        n = self.f.q(f"SELECT count(*) FROM action a WHERE a.state IN ('pending','active'){only}", args)[0][0]
        due = self.f.q(f"""SELECT min(max(coalesce(h.cooldown_until, 0), coalesce(h.not_before, 0), a.next_at,
                              coalesce((SELECT max(p.until) FROM pause p WHERE p.queue IN
                                (a.queue, '*', 'net:' || CASE WHEN a.queue LIKE 'ip:140.127.%'
                                                          THEN 'school' ELSE 'other' END)), 0)))
                            FROM action a LEFT JOIN host h ON h.host = a.host
                            WHERE a.state = 'pending'{only}""", args)[0][0]
        active = self.f.q(f"SELECT * FROM action a WHERE a.state = 'active'{only}", args)
        return n, due, active

    def target_for(self, action: sqlite3.Row) -> Target | None:
        """The POST for an action, built from the page it was planned on as archived (§7.5 step 3): the form that
        holds its button, that page's form state, its target and argument. None when the page no longer holds the
        button or no form fits, or the form posts to another host, or a file button's clicks rule no longer presses
        it (rule_changed): the action is mismatch then, nothing is sent. A menu's press (argument MENU_ARG?…, gap ⑩
        of 11-08): the form of its form_id with the menus set and its submit button (menu_form, menu_post), mismatch
        option_missing:<select> when the page no longer offers a value it asks."""
        meta = {"action": action["id"], "kind": action["kind"], "list_id": action["list_id"]}
        if action["kind"] == "click":
            # the platform file asked again before the POST goes (§7.5 step 10): a press left pending from an earlier
            # run goes only while its clicks rule still presses it (review 10-26 phase 3)
            name = urlsplit(action["target"]).path.rsplit("/", 1)[-1]
            key = f"click:{name}({action['row_key'].removeprefix('click:')})"
            page = self._list_row(action)  # the platform its page is written on decides (action.site: the owner)
            if profiles.decision(scope.site_key(page["url"]) if page is not None else action["site"], key) != "handle":
                self._end(action, "mismatch", "rule_changed")
                return None
            return Target(action["target"], "POST", parse_qsl(action["argument"] or "", keep_blank_values=True),
                          meta=meta)
        try:
            url, _status, headers, body = self.read(action["form_src_warc"], action["form_src_offset"])
        except (OSError, ValueError, StopIteration) as exc:
            self._end(action, "mismatch", f"form_src: {type(exc).__name__}")
            return None
        html = as_text(body, headers.get("content-type"))
        image = action["argument"] == IMAGE_ARG
        choice = profiles.menu_choice(action["argument"])
        if choice is not None:  # a menu's form (gap ⑩ of 11-08): the one of its id, sent by its own submit button
            css, why = menu_form(html, action["form_id"] or "", action["target"])
            if css is None:
                self._end(action, "mismatch", why)
                return None
        elif image:  # an ImageButton (gap ⑪ of 11-08): the form that holds it, as a browser sends it when it is clicked
            css, why = image_form(html, action["target"])
            if css is None:
                self._end(action, "mismatch", why)
                return None
        else:
            if (action["target"], action["argument"]) not in {(t, a) for t, a, _x in webforms.find_postbacks(html)}:
                self._end(action, "mismatch", "control_missing")
                return None
            css = webforms.form_for_control(html, action["target"])
            if css is None:
                self._end(action, "mismatch", "no_form")
                return None
        prof, _rs = self._profile(action)
        form = _FORM_NAME.fullmatch(css)
        if prof is not None and prof.forms and (form is None or form.group(1) not in prof.forms):
            self._end(action, "mismatch", f"form_not_in_profile:{form.group(1) if form else css}")
            return None  # §7.5 step 10: a form the platform file does not name is never sent
        if choice is not None:
            # the form's own fields as archived, the menus set to the press's values (each one the page offers), then
            # its submit button: the HTML Standard's entry list of a form a submit button sends (no __doPostBack)
            post_url, fields = webforms.form_state(html, url, css)
            fields, wrong = menu_post(html, css, fields, choice, action["target"])
            if wrong is not None:
                self._end(action, "mismatch", f"option_missing:{wrong}")
                return None
        elif image:
            # the form's own fields (its hidden __EVENTTARGET too, when it has one) and the click's coordinates, as the
            # HTML Standard's entry list has them for an image button (name.x, name.y; no __doPostBack, nothing else)
            post_url, fields = webforms.form_state(html, url, css)
            fields = [*fields, (f"{action['target']}.x", _IMAGE_XY[0]), (f"{action['target']}.y", _IMAGE_XY[1])]
        else:
            post_url, fields = webforms.postback(html, url, action["target"], action["argument"], form_css=css)
        if (urlsplit(post_url).hostname or "").lower() != action["host"]:
            self._end(action, "mismatch", f"form_posts_to:{urlsplit(post_url).hostname}")
            return None
        return Target(post_url, "POST", fields, meta=meta)

    def _end(self, action, state: str, reason: str, **cols) -> None:
        cols = {"state": state, "reason": reason[:200] if reason else None, "done": self.clock(), **cols}
        self._set(action, **cols)

    def _set(self, action, **cols) -> None:
        sets = ", ".join(f"{k}=?" for k in cols)
        self.f.x(f"UPDATE action SET {sets} WHERE id=?", (*cols.values(), action["id"]))

    @staticmethod
    def _answer(snap, warc: str | None) -> tuple[_Answer, dict]:
        """A fetch.Snapshot (status, url, headers, body, sha256, warc_offset, warc_len) as an answer, with the columns
        every answer writes on its action."""
        a = _Answer(snap.status, snap.url, dict(snap.headers), snap.body, getattr(snap, "sha256", None),
                    getattr(snap, "raw_headers", ()), getattr(snap, "text", None), getattr(snap, "warc_offset", None),
                    getattr(snap, "warc_len", None))
        cols = {"status": a.status, "ctype": (a.headers.get("content-type") or "")[:100], "bytes": len(a.body),
                "sha256": a.sha256 or hashlib.sha256(a.body).hexdigest(), "warc": warc, "warc_offset": a.warc_offset,
                "warc_len": a.warc_len}
        return a, cols

    # ---- answers
    def answered(self, action: sqlite3.Row, snap, warc: str | None) -> Followup:
        """The answer to a press (a fetch.Snapshot: status, url, headers, body, sha256, warc_offset, warc_len), archived
        in warc. For a detail or a next page answered with a web page, the crawler reads its links first (as the
        list page's: _parts with the list row, add_page_res), then calls this. A download answered with a page that
        names where the file is (file_followup) stays active: Followup.file_get says what to GET, file_answered()
        takes what comes."""
        a, cols = self._answer(snap, warc)
        out = Followup(action["list_id"])
        if a.status in REDIRECTS:
            loc = scope.canonical(a.headers.get("location", ""), a.url)
            list_row = self._list_row(action) if action["kind"] == "detail" else None
            led = press_redirect(action["kind"], loc, a.url, list_row["url"] if list_row is not None else None)
            if profiles.menu_choice(action["argument"]) is not None:
                # a menu's press is judged by what its answer shows (menu_mismatch, gap ⑩ of 11-08): a redirect shows
                # neither the year nor the option asked, wherever it leads (another page of the site, the table's own
                # address with a query), so it is no detail's page: the press failed as below (review of 11-09: done,
                # its menu counted as sent and the department's table missing, nothing saying so) 【Claude 的执行办法】
                led = None
            if action["kind"] in ("download", "detail") and led is None:
                # a row's press sent elsewhere (the session or its view state lapsed: SSO's home, a sign-in page)
                # brought neither its file nor its detail (design §23.9 A2, 【Claude 的执行办法】; §23 A2 for the export
                # GET): the press failed, the chain walks again from its GET once (a new form state, never this POST
                # again); the second time a download is not_file:redirect, a detail mismatch (redirect). The Location
                # is never queued: no 要登录, no 学校那边缺 for where the session went
                out = self.failed(action, "press_redirect", location=loc, **cols)
                if not out.restart:
                    if action["kind"] == "download":
                        self._end(action, "done", NOT_FILE + "redirect", location=loc, **cols)
                    else:
                        self._end(action, "mismatch", "redirect", location=loc, **cols)
                return out
            # a next page's or a file button's redirect, a download sent to its file on the press's own host
            # (file_redirect, gap ① of 11-08), and a detail sent to its own page (detail_redirect, gap ⑪ of 11-08):
            # done, the Location for the crawler to queue as the list page's resource (the press's result is redirect:
            # page_completeness rests it on that row, which judges the file or the page)
            self._end(action, "done", "", location=loc, **cols)
            out.location = loc
            if action["kind"] == "pager":  # a redirect is no next page: the walk cannot go on
                self._walk_end(action, "mismatch:page", f"page {action['step']} answered a redirect to {loc}")
            return out
        if action["kind"] == "click":
            return self._click_answer(action, a, cols)
        if a.status != 200:
            if action["kind"] == "pager" or a.status >= 500 or a.status in FAIL_STATUSES:
                return self.failed(action, f"http_{a.status}", **cols)
            self._end(action, "done", "", **cols)
            return out
        cols.update(sniff=sniff.sniff(a.body, cols["ctype"]), filename=sniff.filename(a.raw_headers, a.headers))
        if action["kind"] == "pager" and cols["sniff"] != "html":
            self._end(action, "done", "", **cols)
            self._walk_end(action, "mismatch:page", f"page {action['step']} answered {cols['sniff']}, no web page")
            return out
        if action["kind"] == "download" and cols["sniff"] == "html":
            # SSO (WebIdx's lnkDownloadFile, cLv2's downloadBtnN) answers a download with the page again and a startup
            # script that opens the file's address in an iframe (pilot 10-26, F3): the file is the answer to that GET,
            # in this session, right after the press. The press's own answer is kept until the file's replaces it. A
            # hidden frame of the answer counts when the page pressed (form_src) had no such frame (gap ④ of 11-08)
            html = a.text if a.text is not None else as_text(a.body, cols["ctype"])
            if (follow := file_followup(html, a.url, self._pressed(action))) is not None:
                self._set(action, **cols)
                out.file_get = follow
                return out
        if action["kind"] == "download":  # the press answered with the file itself, or with something that is none
            self._end(action, "done", self._not_file(a, cols) or "", **cols)
            return out
        if cols["sniff"] != "html":
            self._end(action, "done", "", **cols)
            return out
        html = a.text if a.text is not None else as_text(a.body, cols["ctype"])
        if action["kind"] == "detail":
            return self._detail_answer(action, a, html, cols, warc)
        return self._pager_answer(action, a, html, cols, warc)

    def _list_row(self, action) -> sqlite3.Row | None:
        return self.f.row_by_id(action["list_id"])

    def _pressed(self, action) -> str | None:
        """The page a press was built from, as archived (form_src), as text; None when it cannot be read back."""
        if not action["form_src_warc"] or action["form_src_offset"] is None or action["form_src_offset"] < 0:
            return None
        try:
            _url, _status, headers, body = self.read(action["form_src_warc"], action["form_src_offset"])
        except (OSError, ValueError, StopIteration):
            return None
        return as_text(body, headers.get("content-type"))

    def _profile(self, action) -> tuple[profiles.Profile | None, profiles.Rows | None]:
        name, _sep, rowset = (action["profile"] or "").partition("/")
        prof = next((p for p in profiles.PROFILES if p.name == name), None)
        rs = next((r for r in prof.rows if r.name == rowset), None) if prof is not None else None
        return prof, rs

    def _detail_answer(self, action, a: _Answer, html: str, cols: dict, warc: str | None) -> Followup:
        """The answer to a row's detail press: its title must be the row's (title_ok) and it is thin when it holds
        nothing more (empty_answer); a menu's press (gap ⑩ of 11-08) is judged by what its form shows instead
        (menu_mismatch: the year and the option asked, else mismatch, its reason menu: <asked, shown>, the chain
        started again from its GET once as for a title), and never thin (no course shown is the school's answer for
        that choice). Then the answer's
        own buttons are planned (a menu's: the options it offers, with the year asked and the list's row keys; one
        pressed or planned already is that row), never the one just pressed."""
        prof, rs = self._profile(action)
        out = Followup(action["list_id"])
        choice = profiles.menu_choice(action["argument"])
        if choice is not None:
            wrong = menu_mismatch(html, a.url, action["form_id"] or "", choice)
            if wrong is not None:
                # what the answer showed, kept with the press (its reason: verify's and the dashboard's problem
                # lines), not only in the restart's event: the second mismatch starts nothing again (review of 11-09;
                # Followup.note is read by no caller) 【Claude 的执行办法】
                out.note = f"menu: {wrong}"
                self._end(action, "mismatch", out.note, **cols)
                if self._may_restart(action["list_id"]):
                    self.restart_chain(action["list_id"], f"menu mismatch on {action['row_key'][:12]}: {wrong}")
                    out.restart = True
                return out
            self._end(action, "done", "", **cols)
        elif not title_ok(action["row_text"] or "", html, rs.detail_title if rs is not None else None,
                          exclude=rs.grid if rs is not None else None):
            self._end(action, "mismatch", "title", **cols)
            out.note = "title"
            if self._may_restart(action["list_id"]):
                self.restart_chain(action["list_id"], f"title mismatch on {action['row_key'][:12]}")
                out.restart = True
            return out
        else:
            empty = empty_answer(action["row_text"] or "", html, rs)
            self._end(action, "done", EMPTY_DETAIL if empty else "", **cols)  # empty: thin, 要人看 (§10.1 nonempty)
        list_row = self._list_row(action)
        if prof is not None and list_row is not None:
            site = list_row["owner_site"] or scope.site_key(list_row["url"])
            year = self._asked_year(action, prof) if choice is not None else None
            # a detail's own buttons only (its files; a software page's 安裝說明 folder; a menu's other options), never
            # the one just pressed
            pr = _without_pressed(read_page(prof, a.url, html, site=site,
                                            list_url=list_row["url"] if year is not None else None,
                                            parent_key=action["row_key"], step=action["step"], walks=("none",),
                                            year=year), action)
            if warc is not None and a.warc_offset is not None:
                self._plan(action["list_id"], action["epoch"], pr, list_row, warc, a.warc_offset, pages=0, base=a.url)
                self._unpressed(action["list_id"], pr.unplanned_at, pr.texts)
            else:  # nothing to build its files' forms from: nobody presses them
                self._unpressed(action["list_id"], (*pr.unplanned_at, *((p.target, p.argument) for p in pr.presses)),
                                pr.texts)
        return out

    def _pager_answer(self, action, a: _Answer, html: str, cols: dict, warc: str | None) -> Followup:
        """The answer to a press of the pager (§7.5 steps 6, 7): the page asked for goes on (its presses, the next
        page), or the walk ends. An answer that is no page of the list (answer_elsewhere) ends it mismatch:page, its
        note saying why, even when it shows the page number asked for; a 「下一頁」 answer that names no page shown is
        read by unnumbered_next (gap ⑤ of 11-08)."""
        prof, _rs = self._profile(action)
        list_row = self._list_row(action)
        out = Followup(action["list_id"])
        self._end(action, "done", "", **cols)
        if prof is None or list_row is None:
            return out
        site = list_row["owner_site"] or scope.site_key(list_row["url"])
        lw = self._walk_row(action["list_id"])  # the walk's numbers so far (an open look's own)
        pages = (lw["pages"] or 0) if lw else 0
        rows = (lw["rows"] or 0) if lw else 0
        pr = read_page(prof, a.url, html, site=site, list_url=list_row["url"], step=action["step"])
        self._unpressed(action["list_id"], pr.unplanned_at, pr.texts)
        want = action["step"]
        note = ""
        why = answer_elsewhere(pr, a.url)  # a.url: the address the press was sent to
        if why is None and pr.current is None and pr.pager_how == "next":
            taken, why = unnumbered_next(pr, want, self._walked(action["list_id"]), a.url,
                                         self._walked_links(action["list_id"]))
            if taken is not None:
                pr, why = taken, None
                self._set(action, reason=UNNUMBERED)
                note = f"page {want} shows no page number: taken as asked (下一頁)"
        # an answer from elsewhere is no page of this list whatever page it shows (review of 11-09: a Server.Transfer
        # whose other program applies the posted Page$N to its own grid shows the very number asked for)
        if why is not None or pr.current != want:
            if why is None and pr.pager_how == "next" and pr.current == want - 1:
                # spe's 「下一頁」 answered with the page it was on: the list ends there (the server's own word; only
                # from the list's own page: another page's pager says nothing of where this list ends)
                end = list_end(replace(pr, next=None), self._rows_unique(action["list_id"]))
                self._walk(action["list_id"], site, prof.name, action["epoch"], pages=pages, rows=rows,
                           claimed=pr.claimed, ended=end, note=f"next stayed on page {pr.current}")
                return out
            if why is None:
                told = f"shows {pr.current}" if pr.current is not None else "shows no page number"
            else:
                told = f"{why}, showing {pr.current}" if pr.current is not None else why
            note = f"asked for page {want}, the answer {told}"
            self._walk(action["list_id"], site, prof.name, action["epoch"], pages=pages, rows=rows,
                       claimed=pr.claimed, ended="mismatch:page", note=note)
            return out
        if warc is None or a.warc_offset is None:  # nothing to build the next forms from: no end is claimed
            self._walk_end(action, "walking", "no_warc")
            self._no_end(action["list_id"])
            return out
        _n, ended = self._plan(action["list_id"], action["epoch"], pr, list_row, warc, a.warc_offset,
                               pages=pages + 1, base=a.url)
        self._walk(action["list_id"], site, prof.name, action["epoch"], pages=pages + 1, rows=rows + len(pr.rows),
                   claimed=pr.claimed if pr.claimed is not None else (lw["claimed"] if lw else None),
                   ended=ended, note=note)
        return out

    def _click_answer(self, action, a: _Answer, cols: dict) -> Followup:
        """A file button's POST (ee's fileAjax.html): its body is the file's address, or the press is mismatch."""
        out = Followup(action["list_id"])
        if a.status != 200:
            if a.status >= 500 or a.status in FAIL_STATUSES:
                return self.failed(action, f"http_{a.status}", **cols)
            self._end(action, "done", "", **cols)
            return out
        cols.update(sniff=sniff.sniff(a.body, cols["ctype"]))
        page = self.f.row_by_id(action["list_id"])
        url = profiles.click_file(a.text if a.text is not None else as_text(a.body, cols["ctype"]),
                                  page["url"] if page is not None else a.url)
        if url is None:
            self._end(action, "mismatch", "not_an_address", **cols)
            return out
        self._end(action, "done", "", location=url, **cols)
        out.files = (url,)
        return out

    def file_answered(self, action: sqlite3.Row, snap, warc: str | None) -> Followup:
        """The answer to the GET a download press's export page named (Followup.file_get): the press's file, judged
        as any download's answer (its format sniffed, its name from Content-Disposition), with the address it came
        from (location). What is no file is not_file, never ok (§23 A1: empty, no name and no file format, the school's
        「檔案不存在」; a web page again). A redirect is no file either (§23 A2): the press failed, the chain starts again
        from its GET once, and the second time the press is not_file:redirect; its Location is never queued (no
        要登录, no 学校那边缺: the session's export sent the browser elsewhere). A failure of that GET is the crawler's
        to hand to failed(): the chain starts again from its GET once (§6.4), the press with it."""
        a, cols = self._answer(snap, warc)
        out = Followup(action["list_id"])
        if a.status in REDIRECTS:
            loc = scope.canonical(a.headers.get("location", ""), a.url)
            out = self.failed(action, "file_get_redirect", location=loc, **cols)
            if not out.restart:  # the chain walked again once already: the press is done, and it is no file
                self._end(action, "done", NOT_FILE + "redirect", location=loc, **cols)
            return out
        if a.status != 200:
            if a.status >= 500 or a.status in FAIL_STATUSES:
                return self.failed(action, f"http_{a.status}", **cols)
            self._end(action, "done", "", location=a.url, **cols)
            return out
        cols.update(sniff=sniff.sniff(a.body, cols["ctype"]), filename=sniff.filename(a.raw_headers, a.headers))
        self._end(action, "done", self._not_file(a, cols) or "", location=a.url, **cols)
        return out

    @staticmethod
    def _not_file(a: _Answer, cols: dict) -> str | None:
        return not_file_reason(a.body, cols["sniff"], cols["filename"], a.text, cols["ctype"])

    def file_redirects(self, list_id: int) -> list[str]:
        """The files this list's download presses were sent to by a redirect (file_redirect, gap ① of 11-08), each
        once, for the crawler to queue as the list page's attachments (Crawler._replay_parts: on every reading of the
        page, a rejudge's too, so that none is left unqueued; a row already there is found again, nothing more). A
        press that took such a redirect for a lapsed session before 11-08 (§23.9 A2: done not_file:redirect, or failed
        press_redirect) is done now, its reason cleared: its result is redirect, page_completeness rests it on that
        file's row, and `retry download` no longer takes it. Sends nothing. 【Claude 的执行办法】"""
        page = self.f.row_by_id(list_id)
        if page is None:
            return []
        out: dict[str, None] = {}
        for a in self.f.q("SELECT id, state, reason, location FROM action WHERE list_id=? AND kind='download' AND "
                          "status BETWEEN 300 AND 399 AND location IS NOT NULL ORDER BY epoch, id", (list_id,)):
            old = (a["state"], a["reason"]) in (("done", NOT_FILE + "redirect"), ("failed", "press_redirect"))
            if not old and (a["state"], a["reason"]) != ("done", None):
                continue
            loc = file_redirect(a["location"], page["url"])
            if loc is None:
                continue
            if old:
                self.f.x("UPDATE action SET state='done', reason=NULL WHERE id=?", (a["id"],))
            out[loc] = None
        return list(out)

    def failed(self, action: sqlite3.Row, error: str, **cols) -> Followup:
        """A press that got no answer (or one that did not go through: a 5xx, a 408, a 429; a pager's 4xx; a download
        whose export GET failed or redirected; a download's or a detail's press that redirected, §23.9 A2). Never sent
        again as it was (§6.4): a chain starts again from its GET once (a new epoch); after that, a failed pager ends
        the walk as post_failed and a failed row stays failed. A file button waits for `retry click`, a download for
        `retry download` (§23 A3)."""
        self._end(action, "failed", error or "failed", **cols)
        out = Followup(action["list_id"], note=error)
        if action["kind"] in SOLO_KINDS:  # no chain's: nothing to start again from a GET
            return out
        if action["kind"] == "pager" and self._looking(action["list_id"]) is not None:
            # a look's page that did not come: the look ends, nothing starts again, and the press counts neither in
            # the list's one restart nor in its page's two failures (_close_look)
            self._close_look(action["list_id"], "post_failed", note=f"page {action['step']}: {error}")
            return out
        if self._may_restart(action["list_id"]):
            self.restart_chain(action["list_id"], f"{action['kind']} {action['row_key'][:12]} failed: {error}")
            out.restart = True
            return out
        if action["kind"] == "pager":
            self._walk_end(action, "post_failed", f"page {action['step']}: {error}")
        return out

    def _walk_end(self, action, ended: str, note: str) -> None:
        """The walk of an action's list ends so, its counts as they stand (an open look's own)."""
        lw = self._walk_row(action["list_id"])
        row = self._list_row(action)
        site = (row["owner_site"] if row is not None else None) or action["site"]
        self._walk(action["list_id"], site, (action["profile"] or "").partition("/")[0], action["epoch"],
                   pages=(lw["pages"] or 0) if lw else 0, rows=lw["rows"] if lw else None,
                   claimed=lw["claimed"] if lw else None, ended=ended, note=note)

    def _may_restart(self, list_id: int) -> bool:
        """One automatic start again per list (§7.5 step 8): true while only the newest epoch has a failed or
        mismatched press (a start again after a stop, resume, is no automatic one)."""
        n = self.f.q("SELECT count(DISTINCT epoch) FROM action WHERE list_id=? AND kind NOT IN ('click', 'data') AND state IN "
                     "('failed','mismatch')", (list_id,))[0][0]
        return n <= 1

    def restart_chain(self, list_id: int, why: str, *, walk: bool = True, keep_look: bool = True) -> None:
        """The chain starts again from its GET (§7.5 steps 8, 9): its presses left are superseded, its list page's url
        row is pending again (reason chain_restart, prio 0); the GET's answer opens a new epoch (page_stored). walk
        False (`retry download`): the list is not walked again, its listwalk row stays as it is. A look open on the
        list (periodic updates, stage 1) goes on, counting its pages again from the GET, its start and the whole
        walk's record kept (the automatic starts again: resume, a failed or mismatched press); keep_look False (a
        person's retry) closes it first (retried), and the list is walked as asked."""
        if not keep_look:
            self._close_look(list_id, "retried")
        looking = self._looking(list_id) is not None
        with self.f.lock:
            self.f.conn.execute("UPDATE action SET state='superseded', reason=? WHERE list_id=? AND kind NOT IN ('click', 'data') "
                                "AND state IN ('pending','active')", (f"restart: {why}"[:200], list_id))
            # a fetched list page taken again starts its count (review of 4be5e34, Important 2: one got on its third try
            # would fail for good on one timeout); one still waiting for its first answer keeps it
            self.f.conn.execute("UPDATE url SET state='pending', reason='chain_restart', prio=0, next_at=0, attempts="
                                "CASE WHEN state='done' THEN 0 ELSE attempts END WHERE id=?", (list_id,))
            if looking:
                self.f.conn.execute("UPDATE list_look SET pages=0, rows=NULL, note=? WHERE list_id=?",
                                    (f"restart: {why}"[:200], list_id))
            elif walk:
                self.f.conn.execute("UPDATE listwalk SET ended_by='walking', ok=NULL, note=?, at=? WHERE list_id=?",
                                    (f"restart: {why}"[:200], self.clock(), list_id))
        row = self.f.row_by_id(list_id)
        self.f.event("info", "chain_restart", why, row["host"] if row is not None else None,
                     row["url"] if row is not None else None)

    def resume(self, ids: Iterable[int] | None = None) -> int:
        """At a run's start (§7.5 step 9): every chain with a press pending or active starts again from its GET (an
        active press may have been sent: never sent again); a file button left active is failed (crash:left_active)
        for a person to put back; a read of a data address left active is pending again (it only reads: a new
        request, design §23 C1). ids: only the presses of these url rows (action.list_id: a run given --only leaves
        every other chain, and its list page's row, as it is, for the next full run). Returns the chains started
        again."""
        by_id, id_args = ids_clause("list_id", ids)
        lists = [r[0] for r in self.f.q("SELECT DISTINCT list_id FROM action WHERE kind NOT IN ('click', 'data') AND "
                                        f"state IN ('pending','active'){by_id}", id_args)]
        for list_id in lists:
            self.restart_chain(list_id, "resume")
        self.f.x("UPDATE action SET state='failed', reason='crash:left_active' WHERE kind='click' AND state='active'"
                 f"{by_id}", id_args)
        self.f.x(f"UPDATE action SET state='pending' WHERE kind='data' AND state='active'{by_id}", id_args)
        return len(lists)

    def retry_walks(self, ended: str, list_ids: Iterable[int] | None = None) -> int:
        """`nknu archive retry post_failed` and `retry mismatch:page` (a person looked first): the walks that ended
        so (or these lists) start again from their GET (restart_chain: chain_restart, prio 0, the walk walking), their
        failed and mismatched presses no longer counted (superseded, the old reason kept after "retried: "); a `retry
        download` waiting for the list goes (the whole walk, every press). mismatch:page (gap ⑤ of 11-08): the lists
        whose 「下一頁」 answers named no page shown are walked on by today's reading (unnumbered_next); a list the
        school answers with another page ends so again, its note saying why. Returns the lists put back."""
        if ended not in ENDS:
            raise ValueError(f"no walk ends {ended!r}")
        if list_ids is None:
            list_ids = [r[0] for r in self.f.q("SELECT list_id FROM listwalk WHERE ended_by=? ORDER BY list_id",
                                               (ended,))]
        list_ids = list(list_ids)
        for list_id in list_ids:
            self.f.x("UPDATE action SET state='superseded', reason='retried: ' || coalesce(reason, '') WHERE list_id=? "
                     "AND kind NOT IN ('click', 'data') AND state IN ('failed','mismatch')", (list_id,))
            self.f.x("DELETE FROM meta WHERE key=?", (f"{REDO_KEY}{list_id}",))  # the whole walk, every press
            self.restart_chain(list_id, f"retry {ended}", keep_look=False)
        return len(list_ids)

    def retry_post_failed(self, list_ids: Iterable[int] | None = None) -> int:
        """`nknu archive retry post_failed`: retry_walks of the walks that ended post_failed (or these lists)."""
        return self.retry_walks("post_failed", list_ids)

    def retry_downloads(self) -> tuple[int, int]:
        """`nknu archive retry download` (design §23 A3, 【Claude 的执行办法】; a person looked first): every download
        press whose row came last to no file (not_file) or failed is put back. Its list page's GET is pending again
        (restart_chain, chain_restart, prio 0; its walk stays what it was) and that GET presses only these buttons
        again (REDO_KEY: _redo_stored), with the form state of that new GET: no old POST is sent again as it was.
        Their older presses (failed, mismatched, not_file) are superseded, the reason kept after "retried: ". A press
        answered too large is too_large, not failed: a person decides about it, this leaves it. A press a redirect sent
        to a file on its own host (file_redirect: round one took it for a lapsed session, not_file:redirect or failed
        press_redirect) is left too: its file is that Location, which the page's next reading adopts and queues
        (file_redirects; review of 11-09: superseded here before the rejudge, it was adopted by nothing and its file
        never queued). Returns (lists, buttons)."""
        newest: dict[tuple[int, str], sqlite3.Row] = {}
        for a in self.f.q("SELECT a.list_id, a.row_key, a.status, a.location, r.result FROM action a JOIN "
                          "action_result r ON r.id = a.id WHERE a.kind = 'download' AND a.state <> 'superseded' "
                          "ORDER BY a.epoch, a.id"):
            newest[(a["list_id"], a["row_key"])] = a
        todo: dict[int, list[str]] = defaultdict(list)
        lists: dict[int, str | None] = {}  # list id -> its page's address, the base file_redirects judges with
        for (list_id, key), a in newest.items():
            if a["result"] not in ("not_file", "failed"):
                continue
            # the presses file_redirects adopts: a 3xx with a Location that file_redirect takes for the file's
            if a["status"] is not None and 300 <= a["status"] <= 399 and a["location"] is not None:
                if list_id not in lists:
                    page = self.f.row_by_id(list_id)
                    lists[list_id] = page["url"] if page is not None else None
                if lists[list_id] is not None and file_redirect(a["location"], lists[list_id]) is not None:
                    continue
            todo[list_id].append(key)
        for list_id, keys in sorted(todo.items()):
            for key in keys:
                ids = [r[0] for r in self.f.q(
                    "SELECT a.id FROM action a JOIN action_result r ON r.id = a.id WHERE a.list_id=? AND "
                    "a.kind='download' AND a.row_key=? AND (r.result IN ('not_file', 'failed') OR a.state='mismatch')",
                    (list_id, key))]
                for aid in ids:
                    self.f.x("UPDATE action SET state='superseded', reason=substr('retried: ' || coalesce(reason, ''), "
                             "1, 200) WHERE id=?", (aid,))
            self.f.meta(f"{REDO_KEY}{list_id}", json.dumps(sorted(keys)))
            self.restart_chain(list_id, f"retry download: {len(keys)}", walk=False, keep_look=False)
        return len(todo), sum(len(k) for k in todo.values())

    def _redo(self, list_id: int) -> frozenset[str] | None:
        """The download row keys `retry download` put back for this list's next GET, or None."""
        raw = self.f.meta(f"{REDO_KEY}{list_id}")
        if raw is None:
            return None
        try:
            return frozenset(str(k) for k in json.loads(raw))
        except (TypeError, ValueError):
            return frozenset()

    def _redo_stored(self, row: sqlite3.Row, pr: PageRead, keys: frozenset[str], warc: str, offset: int,
                     has_pagers: bool) -> Stored:
        """The new GET of a list `retry download` put back (§23 A3): a new epoch in which only those downloads are
        pressed, built from this GET's form state; nothing else is pressed (a detail, the next page, a download that
        came to a file), the walk stays what it was, and the page's press entries come back from the action table
        (rebuild). One of those buttons this page no longer shows is mismatch row_gone, for a person; a button to press
        that no press of any epoch covers (a row the school added since) is unpressed, for a person too."""
        list_id = row["id"]
        self.f.x("DELETE FROM meta WHERE key=?", (f"{REDO_KEY}{list_id}",))
        epoch = self._new_epoch(list_id)
        left: list[str] = []
        n, _end = self._plan(list_id, epoch, pr, row, warc, offset, pages=1,
                             only=frozenset(("download", k) for k in keys), left=left)
        planned = {r[0] for r in self.f.q("SELECT row_key FROM action WHERE list_id=? AND epoch=? AND kind='download'",
                                          (list_id, epoch))}
        gone = []
        for key in sorted(keys - planned):
            old = self.f.q("SELECT * FROM action WHERE list_id=? AND kind='download' AND row_key=? ORDER BY epoch DESC, "
                           "id DESC LIMIT 1", (list_id, key))
            if not old:
                continue
            o, now = old[0], self.clock()
            with self.f.lock:
                aid = self.f.conn.execute(
                    "INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, "
                    "form_src_offset, form_id, target, argument, row_key, row_text, state, reason, created, done) "
                    "VALUES (?,?,?,'download',?,?,?,?,?,?,?,?,?,?,?,'mismatch','row_gone',?,?)",
                    (list_id, epoch, o["step"], o["queue"], o["host"], o["site"], o["profile"], warc, offset,
                     o["form_id"], o["target"], o["argument"], key, o["row_text"], now, now)).lastrowid
            gone.append({"res_key": f"pb:download:{key}", "how": "postback", "role": "download", "need": "must",
                         "action_id": aid, "text": (o["row_text"] or "")[:100]})
        for e in gone:  # in place of any entry of that row written above (a detail's answer read again names it too)
            self.f.x("DELETE FROM page_res WHERE page_id=? AND res_key=?", (list_id, e["res_key"]))
        if gone:
            self.f.add_page_res(list_id, gone)
        self.rebuild(list_id)
        lw = self.f.q("SELECT ended_by FROM listwalk WHERE list_id=?", (list_id,))
        unplanned = tuple(dict.fromkeys((*pr.unplanned, *left)))
        return Stored("list" if pr.walk != "none" or has_pagers else None, n, lw[0][0] if lw else None, unplanned,
                      texts=pr.texts)

    def retry_unpressed(self, sites: Iterable[str] | None = None) -> tuple[int, int]:
        """`nknu archive retry unpressed` (gaps ② ③ ④ of 11-08, 【Claude 的执行办法】; a person looked first, after a
        rejudge): every stored page whose page_res names a button nobody pressed (how unhandled, need must: unpressed,
        or unknown when it was written before the platform file said to press it) that today's platform file, read
        by the page's own address (url.site) with the button's text, presses is put back: its GET is pending again
        (restart_chain: chain_restart, prio 0, its walk walking) and that GET plans the presses with its own form
        state, a button pressed well before never again (no old POST is sent again). A `retry download` waiting for
        the page is dropped (REDO_KEY): the new GET presses every button not pressed well before, those too. sites:
        only the pages of these sites (url.owner_site). A stored page whose menu is not sent for this academic year as
        far as a new GET could still send it (menus_not_sent: never sent, or an option tried once and not brought) is
        put back too, one key menu:<the rule's name> (gap ⑩ of 11-08, the owner 11-09 「现在补 115」: the course tables
        stored before the rule, or last year's): its GET sends the menu with this year. Returns (pages, button keys:
        postback:<control, ctlNN as ctl*>, one key for a whole column of buttons; menu:<name>, one for a page's
        menu)."""
        only, args = "", []
        if sites is not None:
            sites = sorted(set(sites))
            only, args = f" AND u.owner_site IN ({','.join('?' * len(sites))})", sites
        todo: dict[int, set[str]] = defaultdict(set)
        for r in self.f.q("SELECT pr.page_id, pr.res_key, pr.text, u.url, u.site FROM page_res pr JOIN url u ON "
                          "u.id = pr.page_id WHERE pr.how = 'unhandled' AND pr.need = 'must' AND pr.res_key LIKE "
                          f"'postback:%' AND u.kind = 'page' AND u.state = 'done'{only}", args):
            if profiles.decision(r["site"] or scope.site_key(r["url"]), r["res_key"], r["text"]) == "press":
                todo[r["page_id"]].add(r["res_key"])
        for r, m in menus_not_sent(self.f, self._year(), only, args):
            todo[r["id"]].discard(button_key(m.submit, MENU_ARG))  # the same menu, unpressed: one key
            todo[r["id"]].add(f"{MENU_KEY}{m.name}")
        for page_id in sorted(todo):
            self.f.x("DELETE FROM meta WHERE key=?", (f"{REDO_KEY}{page_id}",))
            self.restart_chain(page_id, f"retry unpressed: {len(todo[page_id])}", keep_look=False)
        return len(todo), sum(len(keys) for keys in todo.values())

    def retry_clicks(self) -> int:
        """`nknu archive retry click`: the file buttons that failed are pending again (a person looked first)."""
        return self.f.x("UPDATE action SET state='pending', reason=NULL, done=NULL WHERE kind='click' AND "
                        "state='failed'")

    # ---- file buttons a clicks rule presses with a POST (§7.4)
    def click_action(self, page_row: sqlite3.Row, plan: profiles.ClickPlan, warc: str | None,
                     warc_offset: int | None) -> int:
        """The action of one file button: one per site and row_key (click:k=<K>,id=<number>), whichever page shows
        it first is its list_id; later pages point their page_res at the same action (§7.4, index action_click)."""
        site = page_row["owner_site"] or scope.site_key(page_row["url"])
        prof = profiles.profile_for(page_row["url"])
        host, queue = self._place(plan.url, page_row)
        with self.f.lock:
            self.f.conn.execute(
                "INSERT OR IGNORE INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, "
                "form_src_offset, form_id, target, argument, row_key, row_text, state, created) "
                "VALUES (?,1,0,'click',?,?,?,?,?,?,NULL,?,?,?,?,'pending',?)",
                (page_row["id"], queue, host, site, f"{prof.name if prof is not None else GENERIC}/{plan.rule}",
                 warc or "", warc_offset if warc_offset is not None else -1, plan.url,
                 urlencode(plan.data), plan.row_key, plan.text[:300], self.clock()))
            return self.f.conn.execute("SELECT id FROM action WHERE site=? AND row_key=? AND kind='click'",
                                       (site, plan.row_key)).fetchone()[0]

    # ---- a platform's data address, read once per site (design §23 C1)
    def data_action(self, page_row: sqlite3.Row, api: profiles.DataApi, value: str, warc: str | None,
                    warc_offset: int | None) -> int:
        """The read of a data address for a site (one per site and address: index action_data), planned by the first
        page of the site that asks it (list_id; its archived page is form_src); target the address, argument the
        request's body as the page's script writes it. Later pages point their page_res at the same action."""
        site = page_row["owner_site"] or scope.site_key(page_row["url"])
        prof = profiles.profile_for(page_row["url"])
        host, queue = self._place(api.url, page_row)
        key = f"{api.name}:{value}"
        with self.f.lock:
            self.f.conn.execute(
                "INSERT OR IGNORE INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, "
                "form_src_offset, form_id, target, argument, row_key, row_text, state, created) "
                "VALUES (?,1,0,'data',?,?,?,?,?,?,NULL,?,?,?,?,'pending',?)",
                (page_row["id"], queue, host, site, f"{prof.name if prof is not None else GENERIC}/{api.name}",
                 warc or "", warc_offset if warc_offset is not None else -1, api.url,
                 profiles.data_api_body(api, value).decode("utf-8"), key, api.row_text or api.name, self.clock()))
            return self.f.conn.execute("SELECT id FROM action WHERE site=? AND row_key=? AND kind='data'",
                                       (site, key)).fetchone()[0]

    def data_answered(self, action: sqlite3.Row, snap, warc: str | None) -> profiles.DataRead | None:
        """A data address's answer (design §23 C1): a 200 read by its platform file (profiles.read_data_api: done with
        read:empty, read:events:<n> or school_error:<message>, or mismatch); any other answer kept as it came (done:
        action_result says what its status is). Returns the read, None for no 200; a 5xx, 408 or 429 is the
        caller's to read again later (data_retry)."""
        a, cols = self._answer(snap, warc)
        if a.status != 200:
            self._end(action, "done", "", **cols)
            return None
        cols.update(sniff=sniff.sniff(a.body, cols["ctype"]))
        api = profiles.data_api(action["profile"].partition("/")[2])
        if api is None:
            self._end(action, "mismatch", "no_data_api", **cols)
            return None
        got = profiles.read_data_api(api, a.text if a.text is not None else as_text(a.body, cols["ctype"]))
        self._end(action, got.state, got.reason, **cols)
        return got

    def retry_data(self) -> int:
        """`nknu archive retry data` (design §23.9 C1, 【Claude 的执行办法】; a person looked first): every read of a data
        address (kind data: cLv2's timeline, one per site) that came to nothing — failed (no answer three times),
        mismatch (no such answer), the school's error, or any other answer that is no read — is pending again, its
        attempts back, due now: the next run sends a new request (it only reads: no chain's POST is sent again). One
        answered too large stays, as a download's does (a person decides). Its old reason is kept after "retried: "
        until the new answer. Returns the reads put back."""
        ids = [r[0] for r in self.f.q("SELECT a.id FROM action a JOIN action_result r ON r.id = a.id WHERE "
                                      "a.kind = 'data' AND r.result NOT IN ('ok', 'todo', 'too_large')")]
        for aid in ids:
            self.f.x("UPDATE action SET state='pending', attempts=0, next_at=0, done=NULL, "
                     "reason=substr('retried: ' || coalesce(reason, ''), 1, 200) WHERE id=?", (aid,))
        return len(ids)

    def data_retry(self, action: sqlite3.Row, error: str, delay: float, max_attempts: int, **cols) -> bool:
        """A read of a data address that got no answer (a timeout, a TLS failure, a 5xx, a 408, a 429): it only reads,
        so it is read again with a new request after delay, as a url row is (design §23 C1), until max_attempts;
        then failed. Returns whether it waits for another try."""
        attempts = (action["attempts"] or 0) + 1
        if attempts < max_attempts:
            self._set(action, state="pending", reason=error[:200], attempts=attempts, next_at=self.clock() + delay,
                      **cols)
            return True
        self._end(action, "failed", error, attempts=attempts, **cols)
        return False

    # ---- SSO 公告中心: the categories add up to the whole (§7.5 step 7, mismatch:total; verify check 4)
    def sso_totals(self) -> tuple[int | None, int | None]:
        """The sum of 「共 N 筆」 over SSO 公告中心's category lists (SubjectList.aspx?cat=<a category>) and that of its
        list of every category (SubjectList.aspx with no cat, as seeds.SSO_LISTS seeds it (design §5 ⑧); cat=all; or
        lastNewsList.aspx). Once every one of those walks has ended well and the
        two differ, the whole list's walk is mismatch:total. Returns (the categories' sum, the whole's), None for a
        side that is not known yet (a list not walked, or a page that states no total)."""
        rows = self.f.q("SELECT u.url, lw.* FROM listwalk lw JOIN url u ON u.id = lw.list_id WHERE lw.profile = ?",
                        (profiles.SSO_NEWS.name,))
        parts, whole = [], []
        for r in rows:
            try:
                parts_ = urlsplit(r["url"])
            except ValueError:
                continue
            cat = next((v for k, v in parse_qsl(parts_.query) if k.lower() == "cat"), None)
            path = parts_.path.lower()
            if path.endswith("/lastnewslist.aspx") or (path.endswith("/subjectlist.aspx") and (cat or "").lower() in
                                                       ("", "all")):
                whole.append(r)
            elif path.endswith("/subjectlist.aspx") and cat:
                parts.append(r)
        if not parts or not whole or any(r["ended_by"] not in END_OK or r["claimed"] is None
                                         for r in (*parts, *whole)):
            return (sum(r["claimed"] or 0 for r in parts) if parts else None,
                    whole[0]["claimed"] if whole else None)
        total, all_ = sum(r["claimed"] for r in parts), whole[0]["claimed"]
        if total != all_:
            for r in whole:
                self.f.x("UPDATE listwalk SET ended_by='mismatch:total', ok=0, note=? WHERE list_id=?",
                         (f"categories add up to {total}, the whole list states {all_}", r["list_id"]))
        return total, all_

    # ---- lists walked by GET (§7.5 step 7, generic)
    def get_walk(self, page_id: int) -> str | None:
        """The walk of the GET-paged list this page is part of (the pages its pager links reach, either way): walking
        while a page of it waits; at the largest page number seen, stored and naming no 「下一頁」, last_page (or
        mismatch:unknown_button when a page holds a button the profile does not know); a 「下一頁」 there with no
        larger number, mismatch:no_next; that page not got, no end (NULL, a note: the page's own result says why).
        Written to every page of the walk. Returns the end."""
        group, edges = self._group(page_id)
        if not edges:
            return None
        rows = {r["id"]: r for r in self.f.q(
            f"SELECT id, url, state, status, kind, page_role, owner_site, reason FROM url WHERE id IN "
            f"({','.join('?' * len(group))})", sorted(group))}
        num, nexts = _numbering(edges)
        sources = {s for s, _d, _t in edges}

        def stored(i: int) -> bool:
            r = rows.get(i)
            return r is not None and r["state"] == "done" and r["status"] == 200 and r["kind"] == "page"

        top = max(num.values(), default=1)
        at_top = [i for i, n in num.items() if n == top]
        note = ""
        if any(rows.get(i) is not None and rows[i]["state"] in ("pending", "active") for i in group):
            ended = "walking"
        elif not any(stored(i) for i in at_top):
            ended = None
            r = next((rows.get(i) for i in at_top if rows.get(i) is not None), None)
            note = f"page {top} not got: {r['state']}/{r['reason']}" if r is not None else f"page {top} not got"
        elif any(src in at_top and num.get(dst, 0) <= top for src, dst in nexts):
            ended = "mismatch:no_next"
        elif self.f.q(f"SELECT 1 FROM page_res WHERE page_id IN ({','.join('?' * len(group))}) AND how='unhandled' "
                      "AND res_key LIKE 'postback:%' AND need='must' LIMIT 1", sorted(group)):
            ended = "mismatch:unknown_button"
        else:
            ended = "last_page"
        page_rows = [i for i in group if stored(i) and (rows[i]["page_role"] == "list" or i in sources)]
        n_pages = len([i for i in group if stored(i)])
        if self.f.q(f"SELECT 1 FROM list_look WHERE mode=? AND ended_at IS NULL AND list_id IN "
                    f"({','.join('?' * len(group))}) LIMIT 1", (LOOK_GET, *sorted(group))):
            return ended  # a look is reading the list again: its walk is written once the look is done (stage 1)
        for i in page_rows:
            site = rows[i]["owner_site"] or ""
            prof = profiles.profile_for(rows[i]["url"])  # the platform the page is written on (gaps ② ③ of 11-08)
            self._walk(i, site, prof.name if prof is not None else GENERIC, 1, pages=n_pages, rows=None, claimed=None,
                       ended=ended, note=note)
            if ended is None:
                self.f.x("UPDATE listwalk SET ended_by=NULL, ok=0 WHERE list_id=?", (i,))
        return ended

    def get_walk_for(self, url_id: int) -> str | None:
        """A row some list's pager links to has an answer (or none for good): that walk again."""
        got = self.f.q("SELECT page_id FROM page_res WHERE role='pager' AND res_id=? LIMIT 1", (url_id,))
        return self.get_walk(got[0][0]) if got else None

    def _group(self, page_id: int, limit: int = 2000) -> tuple[set[int], list[tuple[int, int, str]]]:
        seen, todo, edges, have = {page_id}, [page_id], [], set()
        while todo and len(seen) < limit:
            batch, todo = todo[:400], todo[400:]
            marks = ",".join("?" * len(batch))
            for r in self.f.q(f"SELECT page_id, res_id, text FROM page_res WHERE role='pager' AND how='get' AND "
                              f"res_id IS NOT NULL AND (page_id IN ({marks}) OR res_id IN ({marks}))",
                              (*batch, *batch)):
                e = (r["page_id"], r["res_id"], r["text"] or "")
                if e not in have:
                    have.add(e)
                    edges.append(e)
                for x in (r["page_id"], r["res_id"]):
                    if x not in seen:
                        seen.add(x)
                        todo.append(x)
        return seen, edges


def _epoch(stamp: str | None) -> float:
    try:
        return datetime.fromisoformat(stamp).timestamp() if stamp else 0.0
    except ValueError:
        return 0.0


def backfill_list_rows(frontier: Frontier, root: Path,
                       read: Callable[[str, int], tuple[str, int, dict[str, str], bytes]] | None = None) -> int:
    """list_row for an archive walked before it (periodic updates, stage 1; the lab's ledger): every stored GET of a
    list page and every pager answer its walk took (the page asked for, from this list: _pager_answer's own check),
    read by the page's profile, oldest first, each row's times its answer's (url.fetched_at, action.done); nothing
    is put back. Returns the rows list_row holds after."""
    chains = Chains(frontier, root, read=read)
    readings: list[tuple[float, int, int | None, str, int]] = []
    for r in frontier.q("SELECT id, fetched_at, warc, warc_offset FROM url WHERE (page_role='list' OR id IN "
                        "(SELECT list_id FROM listwalk)) AND state='done' AND status=200 AND warc IS NOT NULL AND "
                        "warc_offset IS NOT NULL"):
        readings.append((_epoch(r["fetched_at"]), r["id"], None, r["warc"], r["warc_offset"]))
    for a in frontier.q("SELECT list_id, step, done, warc, warc_offset FROM action WHERE kind='pager' AND state='done' "
                        "AND sniff='html' AND warc IS NOT NULL AND warc_offset IS NOT NULL"):
        readings.append((a["done"] or 0.0, a["list_id"], a["step"], a["warc"], a["warc_offset"]))
    for at, list_id, step, warc, offset in sorted(readings, key=lambda x: (x[0], x[1], x[2] or 0)):
        lr = frontier.row_by_id(list_id)
        prof = profiles.profile_for(lr["url"]) if lr is not None else None
        if prof is None:
            continue
        try:
            url, _status, headers, body = chains.read(warc, offset)
        except (OSError, ValueError, StopIteration):
            continue
        site = lr["owner_site"] or scope.site_key(lr["url"])
        html = as_text(body, headers.get("content-type"))
        if step is None:
            pr = read_page(prof, lr["url"], html, site=site, list_url=lr["url"])
        else:
            pr = read_page(prof, url, html, site=site, list_url=lr["url"], step=step)
            took = pr.current == step or (pr.current is None and pr.pager_how == "next")
            if answer_elsewhere(pr, url) is not None or not took:
                continue  # an answer the walk did not take (another program's grid, another page shown)
        chains._keep_rows(list_id, pr, url if step is not None else lr["url"], at, put_back=False)
    return frontier.q("SELECT count(*) FROM list_row")[0][0]


def _numbering(edges: Iterable[tuple[int, int, str]]) -> tuple[dict[int, int], list[tuple[int, int]]]:
    """The page number of each page of a GET-paged list from its pager links (get_walk): a link's digit names the
    page it leads to, a 「下一頁」 the page after its own, and a page that only links (a source) with no page 1 known is
    page 1. Returns (numbers by url id, the 下一頁 links)."""
    edges = list(edges)
    num: dict[int, int] = {}
    nexts = []
    for src, dst, text in edges:
        t = norm(text).strip("[]() ")
        if t.isdigit() and dst not in num:
            num[dst] = int(t)
        elif any(w in t.lower() for w in links.NEXT_WORDS):
            nexts.append((src, dst))
    changed = True
    while changed:
        changed = False
        for src, dst in nexts:
            if src in num and dst not in num:
                num[dst] = num[src] + 1
                changed = True
    for pid in sorted({s for s, _d, _t in edges}):
        if pid not in num and 1 not in num.values():
            num[pid] = 1
    return num, nexts


def later_get_pages(entries: Iterable[Mapping]) -> set[int]:
    """The pages a GET-paged list numbers 2 or more, from page_res entries (the export's 页别, blueprint item 22, part
    3; lists.md §3.4 item 1): the GET pager links (role pager, how get) to a row (res_id), their words read as get_walk
    reads them (norm, the brackets around a number left out): a row is a later page when the links to it show numbers,
    every one of them 2 or more (none 1), or, showing none, a next page's word (links.NEXT_WORDS). One page at a time,
    with no walk: on round one's archive (11-09) it named exactly the pages get_walk numbers 2 or more, of all 2,969
    list pages the exports wrote (1,658 later pages, 165 first pages, 1,146 with no GET pager link). 【Claude 的执行办法】"""
    texts: dict[int, list[str]] = defaultdict(list)
    for e in entries:
        if e.get("role") == "pager" and e.get("how") == "get" and e.get("res_id") is not None:
            texts[e["res_id"]].append(norm(e.get("text")).strip("[]() "))
    out: set[int] = set()
    for rid, said in texts.items():
        nums = [int(t) for t in said if t.isdecimal()]
        nexts = any(w in t.lower() for t in said if not t.isdecimal() for w in links.NEXT_WORDS)
        if (nums and 1 not in nums and min(nums) >= 2) or (not nums and nexts):
            out.add(rid)
    return out


# a button the platform file says to press that no press covers (PageRead.unplanned): nobody presses it, so the page
# waits for a person 【Claude 的执行办法】
UNPRESSED = "unhandled:unpressed"
REDO_KEY = "redo_download:"  # meta key + list id: the download row keys (JSON) `retry download` put back (§23 A3)


def unpressed_entries(keys: Iterable[str], texts: Iterable[tuple[str, str]] = ()) -> list[dict]:
    """The page_res entries of buttons to press that nobody presses (need must, why UNPRESSED: 要人看), each with its
    button's text where it is known (texts: (key, text), PageRead.texts): a rule that names a text decides by it
    (verify check 8, `retry unpressed`; 安裝說明's folder, gap ⑪ of 11-08), as page_res.text of any button."""
    said = dict(texts)
    return [{"res_key": k, "how": "unhandled", "role": "click", "need": "must", "why": UNPRESSED,
             "text": (said.get(k) or "")[:100] or None} for k in keys]


def click_entry(plan: profiles.ClickPlan, *, res_id: int | None = None, action_id: int | None = None) -> dict:
    """The page_res entry of a click a clicks rule presses (in place of the click's own key): need must, pointing at
    the GET's url row (a tab) or the POST's action (a file button)."""
    return {"res_key": plan.res_key, "how": "click", "role": "click", "need": "must", "res_id": res_id,
            "action_id": action_id, "zone": plan.zone, "text": plan.text[:100]}


