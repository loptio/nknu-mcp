"""Page types and JavaScript (round-1 design §8.1–§8.3): the one page-type key, the 10-25 browser test brought into
the page_type table, the cheap signs a page reads that its content needs a script, and what the crawl does with them.
Nothing here sends anything or runs a script; the browser check after the round (jscheck, §8.4) is not here.

- type_key(url): site|host|path template|parameter names, as the 10-25 test keyed its 1,093 types (page_types.json):
  the path lower-cased; when its last segment is a web page file (.html, .php, .aspx …) each run of digits in each
  segment is {n}, otherwise (a route: /news/page/2/, /9/458, an upload folder's file) the first segment stays and each
  later one is *; the names of the parameters that have a value, lower-cased and sorted (SSO's selpage among them, so
  WebIdx's home, list and item are three types). The site is scope.site_key's. This is click_scan.py's reading of the
  10-25 keys, which gives all 1,906 sample URLs their own key back (design §8.1 sketches the rule; where the sketch and
  the 1,906 samples differ, the samples win: no digits-only rule for routes, no selpage value in the key).
- import_jstest(): design §8.2. The https retest replaces the first run's line of the same type; the 10 types the 20:50
  review looked at take the review's verdict and notes, never the method column; every other type by the rules in
  order (no 200 → todo inconclusive:status_<code>; frame text ≥ 200 → frame; 0 characters shown → todo rendered0; 0
  fetched → todo direct0; slow or never idle → todo slow; new text in a data request → todo data_text unless a person
  found that text in another page's raw HTML (DATA_TEXT_CHECKED); else ok). The click scan's kinds with hidden pages
  only add the note click_profiled to an ok type: no tab number or file id is read from it (it masks digits and
  names). The browser's last address of a type it saw redirect by script is a note and a line of the report, never a
  seed. Several old types on one new key: the strictest verdict (js > todo > click > frame > ok), in the report.
- measure(html): js_pages_scan.py's rules (lines 17-19, 53-54): strong signs (a 「載入中」 kind of text anywhere in the
  HTML, script strings too; an empty single-page-app root and its kin; a platform profile's content area that is there
  but holds no text, picture or attachment) and weak ones (under 150 characters with 3 or more scripts, or an ajax
  call with under 400 characters).
- observe(): design §8.3, for each 200 HTML page the crawl stores. A new type is todo, why=new. A strong sign, a click
  this round cannot press (need='round2', or a no-rule tab shown on load: click:shown_on_load), or weak signs on 5 or
  more pages and 30% of the type's pages, send an ok type (frame too, for a strong sign) back to todo (why hint, click,
  weak) when the type came from the import or the crawl; a type jscheck or a person judged keeps its verdict and the
  signs are reported (judged_with_hints). The page with a strong sign carries js_hint whatever its type says (url_result
  holds it unchecked until jscheck opened that very page).

The rules for data_text, the merge order with unresolved, the js_redirect note found from the test's own evidence and
the notes kept as comma-separated tokens are Claude's way of doing it (【Claude 的执行办法】).
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import parse_qsl, urlsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import links, scope
from nknu.archive.frontier import Frontier

# ---------------------------------------------------------------- §8.1 the page-type key
# a last segment of these is a web page file: its path is a template with {n} for each run of digits; any other path is
# a route (or an upload folder's file) and is its first segment and one * per later segment
_PAGE_EXT = re.compile(r"\.(?:s?html?|php\d?|aspx?|jsp|cfm|xhtml|cgi|pl|txt|xml)$")


class TypeKey(NamedTuple):
    site: str
    host: str
    path_tpl: str
    params: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.site}|{self.host}|{self.path_tpl}|{self.params!r}"


def path_template(path: str) -> str:
    segs = [s for s in path.lower().strip("/").split("/") if s]
    if not segs:
        return ""
    if not _PAGE_EXT.search(segs[-1]):
        return "/".join([segs[0]] + ["*"] * (len(segs) - 1))
    return "/".join(re.sub(r"\d+", "{n}", s) for s in segs)


def type_parts(url: str, site: str | None = None) -> TypeKey:
    """The parts of a URL's page-type key; site: the row's site (scope.site_key(url) when not given)."""
    sp = urlsplit(url)
    names = tuple(sorted({k.lower() for k, _ in parse_qsl(sp.query)}))
    return TypeKey(scope.site_key(url) if site is None else site, (sp.hostname or "").lower(), path_template(sp.path),
                   names)


def type_key(url: str, site: str | None = None) -> str:
    """The page-type key of a page's URL (design §8.1), e.g. c.nknu.edu.tw/aca|c.nknu.edu.tw|aca/newsdetail.aspx|('gi',
    'nid')."""
    return type_parts(url, site).key


def parse_key(key: str) -> TypeKey:
    """A key back into its parts (ValueError for one that is not a key)."""
    try:
        site, host, rest = key.split("|", 2)
        path_tpl, params = rest.rsplit("|", 1)
        names = ast.literal_eval(params)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(f"not a page-type key: {key!r}") from exc
    if not isinstance(names, tuple) or not all(isinstance(n, str) for n in names):
        raise ValueError(f"not a page-type key: {key!r}")
    return TypeKey(site, host, path_tpl, names)


# ---------------------------------------------------------------- §8.3 the signs a page reads
# js_pages_scan.py:17-19, as the 10-25 scan read them (its 174 strong pages)
LOADING = re.compile(r"載入中|载入中|加載中|加载中|讀取中|读取中|Loading\.\.\.|loading\.\.\.")
SPA = re.compile(r'<div[^>]+id=["\'](?:app|root|__next|__nuxt)["\'][^>]*>\s*</div>|<app-root|ng-app=|v-cloak'
                 r"|\{\{\s*[\w.]+\s*\}\}|<nknu-[a-z-]+")
AJAX = re.compile(r"fetch\(\s*['\"`][^'\"`]+|\$\.(?:ajax|get|getJSON|post)\(|axios\.|XMLHttpRequest|/api/[\w./-]+")
WEAK_CHARS, WEAK_SCRIPTS, WEAK_AJAX_CHARS = 150, 3, 400  # js_pages_scan.py:53-54
WEAK_MIN_PAGES, WEAK_SHARE_TENTHS = 5, 3  # a type with weak signs on 5 or more pages and 30% of them (design §8.3)
_CONTENT_DROP = ("script", "style", "noscript", "template")
# what fills a content area without text: a picture, a player, a frame, a Google Sites embed-code block
_CONTENT_MEDIA = "img, picture, svg, video, audio, iframe, frame, embed, object, canvas, [data-code]"
_FILE_EXT = scope.ATTACHMENT_EXT | scope.IMAGE_EXT
_FILE_PATHS = scope.FILE_PATHS | scope.IMAGE_PATHS


@dataclass(frozen=True)
class Measure:
    text_chars: int  # the page's words (links.text_chars, or what extract() counted with embed-code blocks)
    scripts: int  # <script> elements, inline and external
    loading: bool  # a 「載入中」 kind of text anywhere in the HTML
    spa: bool  # an empty app root, an Angular or Vue template left unfilled, a custom element
    ajax: bool  # a script that asks for data: fetch('…'), $.ajax, axios, XMLHttpRequest, /api/
    content_found: bool = False  # the platform profile's content area is on the page
    content_empty: bool = False  # … and holds no text, picture or attachment

    @property
    def strong(self) -> bool:
        return self.loading or self.spa or self.content_empty

    @property
    def weak(self) -> bool:
        return ((self.text_chars < WEAK_CHARS and self.scripts >= WEAK_SCRIPTS)
                or (self.ajax and self.text_chars < WEAK_AJAX_CHARS))

    @property
    def signals(self) -> tuple[str, ...]:
        """For a report: which signs the page has."""
        out = [name for name, on in (("loading", self.loading), ("spa", self.spa),
                                     ("empty_content", self.content_empty)) if on]
        if self.text_chars < WEAK_CHARS and self.scripts >= WEAK_SCRIPTS:
            out.append("weak:scripts")
        if self.ajax and self.text_chars < WEAK_AJAX_CHARS:
            out.append("weak:ajax")
        return tuple(out)


def measure(html: str, *, content_css: str | None = None, text_chars: int | None = None) -> Measure:
    """The signs of one page's HTML (design §8.3). content_css: the platform profile's content area (None on a site
    without one: no empty-area sign); text_chars: links.extract()'s count when the caller has it (it adds the words of
    Google Sites embed-code blocks), otherwise counted here the same way (links.text_chars)."""
    tree = LexborHTMLParser(html)
    scripts = len(tree.css("script"))
    found = empty = False
    if content_css:
        areas = tree.css(content_css)
        found = bool(areas)
        empty = found and not any(_area_holds_content(node.html or "") for node in areas)
    if text_chars is None:
        text_chars = links.text_chars(tree)  # last: it takes script and layout out of the tree
    return Measure(text_chars=text_chars, scripts=scripts, loading=bool(LOADING.search(html)), spa=bool(SPA.search(html)),
                   ajax=bool(AJAX.search(html)), content_found=found, content_empty=empty)


def _area_holds_content(fragment: str) -> bool:
    """A content area holds something: words (not its scripts' or styles'), a picture or player, a background image,
    or a link to an attachment or a picture (design §8.3: 「正文区找得到、却没有字、也没有图片和附件」)."""
    sub = LexborHTMLParser(fragment)
    for tag in _CONTENT_DROP:
        for n in sub.css(tag):
            n.decompose()
    body = sub.body
    if body is None:
        return False
    if re.sub(r"\s+", "", body.text(separator=" ")):
        return True
    if body.css_first(_CONTENT_MEDIA) is not None:
        return True
    if any("url(" in (n.attributes.get("style") or "") for n in body.css("[style]")):
        return True
    return any(_file_like(n.attributes.get("href") or "") for n in body.css("a[href]"))


def _file_like(href: str) -> bool:
    try:
        sp = urlsplit(href.strip())
        host = (sp.hostname or "").lower()
    except ValueError:
        return False
    path = sp.path.lower()
    last = path.rsplit("/", 1)[-1]
    ext = last.rsplit(".", 1)[-1] if "." in last else ""
    return ext in _FILE_EXT or path in _FILE_PATHS or host in scope.GOOGLE_LINK_HOSTS


# ---------------------------------------------------------------- §8.3 what the crawl does with a page
CHANGEABLE_SOURCES = ("import", "crawl")  # a verdict the signs may send back to todo; jscheck's and a person's stay
JUDGED_SOURCES = ("jscheck", "manual")
HINT_FLAG = "js_hint"
_HAS_HINT = f"(flags IS NOT NULL AND instr(',' || flags || ',', ',{HINT_FLAG},') > 0)"
# a click this round cannot press (design §7.8, §8.3): the rule does not fit it (round2), or no rule and the tab shown
# on load; a tab pressed by its platform rule (must) and a tab with a plain link (click:plain_href) are not one
CLICK_HINT_SQL = ("SELECT 1 FROM page_res WHERE page_id=? AND how='click' "
                  "AND (need='round2' OR why='click:shown_on_load') LIMIT 1")


@dataclass(frozen=True)
class Observed:
    key: str  # url.page_type (observe() wrote it)
    new: bool  # the type first appeared on this page: todo, why=new, source crawl
    flags: tuple[str, ...]  # what the caller adds to url.flags: js_hint for a strong sign
    retyped: str | None  # why this page sent its type back to todo: hint, click, weak (None: unchanged)
    kept: tuple[str, ...]  # signs on a type jscheck or a person judged: its verdict stays, for the report


def click_hint(conn: sqlite3.Connection, page_id: int) -> bool:
    """The page has a click this round cannot press (its page_res rows, design §8.3)."""
    return conn.execute(CLICK_HINT_SQL, (page_id,)).fetchone() is not None


def observe(fr: Frontier, *, url_id: int, url: str, m: Measure, click: bool | None = None,
            site: str | None = None) -> Observed:
    """One 200 HTML page the crawl stored (design §8.3): writes url.page_type and the type's row (a new type: todo
    why=new, source crawl, this page its sample; a known one: its page count and signs, the sample when it has none
    this round), and sends an ok type back to todo when this page shows it needs a closer look. click: the page has a
    click this round cannot press; None reads it from the page's page_res rows (write them first). The same row seen
    again (a fetch again, rejudge) is not counted twice: its weak sign stays as first counted.

    The caller adds Observed.flags to url.flags; text_chars it writes from its own count (m.text_chars is the same)."""
    parts = type_parts(url, site)
    key = parts.key
    strong, weak = m.strong, m.weak
    with fr.lock:
        conn = fr.conn
        conn.execute("SAVEPOINT pagetypes_observe")
        try:
            prev = conn.execute(f"SELECT page_type, {_HAS_HINT} FROM url WHERE id=?", (url_id,)).fetchone()
            if prev is None:
                raise KeyError(f"url row {url_id} does not exist")
            again = prev[0] == key
            if prev[0] is not None and not again:  # counted under a key the rules no longer give: not there now
                conn.execute("UPDATE page_type SET n_pages=max(n_pages - 1, 0), n_strong=max(n_strong - ?, 0) "
                             "WHERE key=?", (int(bool(prev[1])), prev[0]))
            if click is None:
                click = click_hint(conn, url_id)
            row = conn.execute("SELECT * FROM page_type WHERE key=?", (key,)).fetchone()
            new = row is None
            retyped, kept = None, ()
            if new:
                conn.execute(
                    "INSERT INTO page_type(key, site, host, path_tpl, params, verdict, why, sample_id, sample_url, "
                    "n_pages, n_strong, n_weak, source) VALUES (?,?,?,?,?,'todo','new',?,?,1,?,?,'crawl')",
                    (key, parts.site, parts.host, parts.path_tpl, json.dumps(list(parts.params)), url_id, url,
                     int(strong), int(weak)))
            else:
                n_pages = row["n_pages"] + (0 if again else 1)
                n_strong = max(0, row["n_strong"] + int(strong) - int(again and bool(prev[1])))
                n_weak = row["n_weak"] + (0 if again else int(weak))
                signs = _signs(row["verdict"], strong=strong, click=bool(click), n_pages=n_pages, n_weak=n_weak)
                verdict, why = row["verdict"], row["why"]
                if signs and row["source"] in CHANGEABLE_SOURCES:
                    verdict, why, retyped = "todo", signs[0], signs[0]
                elif signs:
                    kept = signs
                conn.execute(
                    "UPDATE page_type SET n_pages=?, n_strong=?, n_weak=?, verdict=?, why=?, "
                    "sample_id=coalesce(sample_id, ?), sample_url=coalesce(sample_url, ?) WHERE key=?",
                    (n_pages, n_strong, n_weak, verdict, why, url_id, url, key))
            conn.execute("UPDATE url SET page_type=? WHERE id=?", (key, url_id))
        except BaseException:
            conn.execute("ROLLBACK TO pagetypes_observe")
            conn.execute("RELEASE pagetypes_observe")
            raise
        conn.execute("RELEASE pagetypes_observe")
    return Observed(key=key, new=new, flags=(HINT_FLAG,) if strong else (), retyped=retyped, kept=kept)


def _signs(verdict: str, *, strong: bool, click: bool, n_pages: int, n_weak: int) -> tuple[str, ...]:
    """Which of the §8.3 signs would send a type of this verdict back to todo, the strongest first."""
    out = []
    if strong and verdict in ("ok", "frame"):
        out.append("hint")
    if click and verdict == "ok":
        out.append("click")
    if verdict == "ok" and _weak_type(n_pages, n_weak):
        out.append("weak")
    return tuple(out)


def _weak_type(n_pages: int, n_weak: int) -> bool:
    return n_pages >= WEAK_MIN_PAGES and 10 * n_weak >= WEAK_SHARE_TENTHS * n_pages


def judged_with_hints(fr: Frontier) -> list[dict[str, Any]]:
    """The report of design §8.3: types jscheck or a person judged ok (or frame) whose pages this round show signs.
    Their verdict stays; a page with a strong sign is unchecked by itself until jscheck opens it (url_result)."""
    rows = fr.q(f"""SELECT pt.key, pt.verdict, pt.source, pt.n_pages, pt.n_strong, pt.n_weak,
                           EXISTS (SELECT 1 FROM url u JOIN page_res r ON r.page_id = u.id
                                    WHERE u.page_type = pt.key AND r.how = 'click'
                                      AND (r.need = 'round2' OR r.why = 'click:shown_on_load')) AS click
                      FROM page_type pt
                     WHERE pt.source IN ('{JUDGED_SOURCES[0]}', '{JUDGED_SOURCES[1]}')
                       AND pt.verdict IN ('ok', 'frame') ORDER BY pt.key""")
    out = []
    for r in rows:
        signs = _signs(r["verdict"], strong=r["n_strong"] > 0, click=bool(r["click"]), n_pages=r["n_pages"],
                       n_weak=r["n_weak"])
        if signs:
            out.append({"key": r["key"], "verdict": r["verdict"], "source": r["source"], "signs": signs,
                        "n_pages": r["n_pages"], "n_strong": r["n_strong"], "n_weak": r["n_weak"]})
    return out


def withdraw_files_only(fr: Frontier) -> list[str]:
    """Design §8.2, 工设系's uploads/*: a type imported as todo because its sample was a broken link
    (inconclusive:broken_sample), with no 200 HTML page of it this round, is withdrawn (note withdrawn:files_only: the
    folder holds files, which have no page type) rather than left waiting for a verdict. jscheck calls it."""
    out = []
    with fr.lock:
        for r in fr.conn.execute(
                "SELECT key, note FROM page_type WHERE verdict='todo' AND why='inconclusive:broken_sample'").fetchall():
            if fr.conn.execute("SELECT 1 FROM url WHERE page_type=? AND state='done' AND status=200 AND sniff='html' "
                               "LIMIT 1", (r["key"],)).fetchone() is not None:
                continue
            note = add_note(r["note"], "withdrawn:files_only")
            if note != r["note"]:
                fr.conn.execute("UPDATE page_type SET note=? WHERE key=?", (note, r["key"]))
            out.append(r["key"])
    return out


def add_note(note: str | None, *tokens: str) -> str | None:
    """page_type.note as comma-separated tokens (js_redirect, click_profiled …), each once, in the order added."""
    have = [t for t in (note or "").split(",") if t]
    for t in tokens:
        if t and t not in have:
            have.append(t)
    return ",".join(have) or None


# ---------------------------------------------------------------- §8.2 the 10-25 browser test
JSTEST_DIR = Path(__file__).resolve().parents[3] / "research" / "project_audit_2026-10-25" / "js_test_2026-10-25"
RESULTS, RETEST, TYPES = "results.jsonl", "results_https_retest.jsonl", "page_types.json"
REVIEW, CLICK_SCAN = "manual_review_2026-10-25.json", "click_scan_2026-10-25.json"
TESTED_AT = "2026-10-25"
VERDICTS = ("ok", "js", "frame", "click", "todo", "unresolved")
STRICTNESS = ("js", "unresolved", "todo", "click", "frame", "ok")  # the strictest first (design §8.2)
FRAME_CHARS = 200  # jstest.py: a frame's text of 200 or more is the page's content
# a type whose data request brought new text a person found in another page's raw HTML (design §8.2: the three news
# titles english's home loads by POST are in en/news.html): ok, noted; any other such type is todo, data_text
DATA_TEXT_CHECKED = {"english.nknu.edu.tw|english.nknu.edu.tw|en/index.html|()": "en/news.html"}
# notes the review table of design §8.2 gives that the review file does not hold
REVIEW_NOTES = {"clcts.nknu.edu.tw|clcts.nknu.edu.tw|uploads/voicebook-{n}/index.html|()": ("flip_pdf",)}
_WHY_CODE = re.compile(r"inconclusive:[a-z0-9_]+")
_NOWHERE = ("chrome-error:", "about:")


class ImportRefused(RuntimeError):
    """The page_type table already holds this round's work (a type the crawl found or counted, jscheck's or a
    person's verdict): the 10-25 results are brought in once, before the round records any page."""


@dataclass
class ImportReport:
    by_old: Counter = field(default_factory=Counter)  # verdict per type of the 10-25 test (its 1,093)
    by_key: Counter = field(default_factory=Counter)  # verdict per page_type row written
    why: Counter = field(default_factory=Counter)  # the todo reasons, per old type
    notes: Counter = field(default_factory=Counter)  # the notes, per old type
    retested: list[str] = field(default_factory=list)  # types the https retest replaced
    reviewed: list[str] = field(default_factory=list)  # types the 20:50 review decided
    untested: list[str] = field(default_factory=list)  # page_types.json types no result line covers
    key_changed: list[tuple[str, str]] = field(default_factory=list)  # old key, the key its sample has now
    merged: list[tuple[str, tuple[str, ...], str]] = field(default_factory=list)  # new key, old types, verdict
    js_redirect: list[tuple[str, str]] = field(default_factory=list)  # type, where the browser stopped (not a seed)
    data_text: list[tuple[str, str]] = field(default_factory=list)  # type, verdict (or the page holding the text)
    click_hidden: list[tuple[str, str]] = field(default_factory=list)  # click scan kinds with hidden pages, verdict

    def lines(self) -> list[str]:
        def counts(c: Counter) -> str:
            names = ["ok", "click", "js", "frame", "todo"] + sorted(set(c) - {"ok", "click", "js", "frame", "todo"})
            return "、".join(f"{v} {c.get(v, 0):,}" for v in names)
        noted = sum(1 for _, v in self.click_hidden if v == "ok")
        out = [(f"导入 10-25 网页类型测试：按旧类型 {sum(self.by_old.values()):,} 类（{counts(self.by_old)}）；"
                f"写入 {sum(self.by_key.values()):,} 行（{counts(self.by_key)}）"),
               f"https 重测覆盖 {len(self.retested)} 类；照复看表 {len(self.reviewed)} 类；没有测试结果的 {len(self.untested)} 类",
               f"待判定的原因：{'、'.join(f'{k} {n}' for k, n in sorted(self.why.items())) or '无'}",
               f"备注：{'、'.join(f'{k} {n}' for k, n in sorted(self.notes.items())) or '无'}",
               f"点了才载入（扫描找到的）{len(self.click_hidden)} 类：照 ok 记 click_profiled {noted} 类，其余照原判定",
               f"新键和旧键不同 {len(self.key_changed)} 类；合并 {len(self.merged)} 处"]
        out += [f"  合并 {k}（{v}）← {'、'.join(olds)}" for k, olds, v in self.merged]
        out += [f"  程式跳转 {k}：浏览器停在 {to}（只记下来，不当起点）" for k, to in self.js_redirect]
        out += [f"  资料请求有新文字 {k}：{v}" for k, v in self.data_text]
        return out


@dataclass
class _Decision:
    verdict: str
    why: str | None
    notes: list[str]
    rec: dict | None
    sample: str | None


def import_jstest(fr: Frontier, src: Path = JSTEST_DIR) -> ImportReport:
    """Bring the 10-25 browser test into page_type (design §8.2), before the round records any page (ImportRefused
    otherwise; the import's own rows, untouched by the crawl, are replaced). src holds the five files of
    research/project_audit_2026-10-25/js_test_2026-10-25; a missing one is an error. No url row is written: the
    test's addresses are no seeds (§5 ⑩)."""
    results = {r["type"]: r for r in _jsonl(src / RESULTS)}
    report = ImportReport()
    for r in _jsonl(src / RETEST):
        report.retested.append(r["type"])
        results[r["type"]] = r
    types: dict[str, list[str]] = json.loads((src / TYPES).read_text(encoding="utf-8"))
    review = {k["type"]: k for k in json.loads((src / REVIEW).read_text(encoding="utf-8"))["kinds"]}
    hidden = {k["type"] for k in json.loads((src / CLICK_SCAN).read_text(encoding="utf-8"))["kinds"]
              if (k.get("hidden_pages") or 0) > 0}

    decided: dict[str, _Decision] = {}
    for old in dict.fromkeys([*types, *results]):
        rec = results.get(old)
        sample = rec["url"] if rec else next(iter(types.get(old) or ()), None)
        if old in review:
            report.reviewed.append(old)
            dec = _reviewed(old, review[old], rec, sample)
        elif rec is None:
            report.untested.append(old)
            dec = _Decision("todo", "inconclusive:untested", [], None, sample)
        else:
            dec = _by_rules(old, rec, sample, report)
        if rec is not None and rec.get("login"):  # a password box anywhere on it: a note only (design §8.2)
            dec.notes.append("login_field")
        if old in hidden:
            report.click_hidden.append((old, dec.verdict))
            if dec.verdict == "ok":
                dec.notes.append("click_profiled")
        decided[old] = dec
        report.by_old[dec.verdict] += 1
        if dec.why:
            report.why[dec.why] += 1
        for n in dict.fromkeys(dec.notes):
            report.notes[n] += 1

    by_key: dict[str, list[str]] = {}
    for old, dec in decided.items():
        new = type_key(dec.sample) if dec.sample else old
        if new != old:
            report.key_changed.append((old, new))
        by_key.setdefault(new, []).append(old)

    rows = []
    for key, olds in by_key.items():
        olds.sort(key=lambda o: STRICTNESS.index(decided[o].verdict))
        top = decided[olds[0]]
        notes = add_note(None, *(n for o in olds for n in decided[o].notes))
        if len(olds) > 1:
            report.merged.append((key, tuple(olds), top.verdict))
        parts = parse_key(key)
        rec = top.rec or {}
        rows.append((key, parts.site, parts.host, parts.path_tpl, json.dumps(list(parts.params)), top.verdict, top.why,
                     rec.get("method"), rec.get("direct_chars"), rec.get("rendered_chars"), _data_urls(rec),
                     top.sample, TESTED_AT, notes))
        report.by_key[top.verdict] += 1

    with fr.lock:
        conn = fr.conn
        conn.execute("SAVEPOINT pagetypes_import")
        try:
            busy = conn.execute("SELECT count(*) FROM page_type WHERE coalesce(source, '') <> 'import' OR n_pages > 0 "
                                "OR sample_id IS NOT NULL").fetchone()[0]
            if busy or conn.execute("SELECT 1 FROM url WHERE page_type IS NOT NULL LIMIT 1").fetchone():
                raise ImportRefused("page_type already holds this round's work: the 10-25 results are imported once, "
                                    "before the round records any page")
            conn.execute("DELETE FROM page_type WHERE source = 'import'")
            conn.executemany(
                "INSERT INTO page_type(key, site, host, path_tpl, params, verdict, why, method, direct_chars, "
                "rendered_chars, data_urls, sample_url, source, tested_at, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'import',?,?)", rows)
        except BaseException:
            conn.execute("ROLLBACK TO pagetypes_import")
            conn.execute("RELEASE pagetypes_import")
            raise
        conn.execute("RELEASE pagetypes_import")
    return report


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _reviewed(old: str, entry: dict, rec: dict | None, sample: str | None) -> _Decision:
    """One of the 10 types the 20:50 review decided (design §8.2's first table): its verdict and note, not the method
    column; a todo keeps the review's inconclusive:<reason>."""
    verdict = entry.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"{REVIEW}: {old} has a verdict this version does not know: {verdict!r}")
    why = None
    if verdict == "todo":
        m = _WHY_CODE.match(entry.get("why") or "")
        why = m.group() if m else "inconclusive:reviewed"
    notes = [entry["note"]] if entry.get("note") else []
    notes += REVIEW_NOTES.get(old, ())
    return _Decision(verdict, why, notes, rec, sample)


def _by_rules(old: str, rec: dict, sample: str | None, report: ImportReport) -> _Decision:
    """Design §8.2's rules, in order, for a type the review did not look at."""
    def todo(why: str) -> _Decision:
        return _Decision("todo", why, [], rec, sample)

    status = rec.get("direct_status")
    if status != 200:
        return todo(f"inconclusive:status_{'none' if status is None else status}")
    if rec.get("iframe") or (rec.get("frame_chars") or 0) >= FRAME_CHARS:
        return _Decision("frame", None, [], rec, sample)
    if not rec.get("rendered_chars"):
        return todo("inconclusive:rendered0")
    if not rec.get("direct_chars"):
        return todo("inconclusive:direct0")
    if rec.get("slow") or rec.get("not_idle"):
        return todo("inconclusive:slow")
    if rec.get("js") or rec.get("method") not in (None, "direct"):  # the review covers every one the test called js
        return todo("inconclusive:not_reviewed")
    notes = []
    if any(d.get("has_new_text") for d in _data_requests(rec)):
        if old not in DATA_TEXT_CHECKED:
            report.data_text.append((old, "todo"))
            return todo("data_text")
        report.data_text.append((old, f"ok（内容在 {DATA_TEXT_CHECKED[old]} 的原始网页里）"))
        notes.append("data_text_checked")
    stop = _scripted_stop(rec)
    if stop:
        notes.append("js_redirect")
        report.js_redirect.append((old, stop))
    return _Decision("ok", None, notes, rec, sample)


def _data_requests(rec: dict) -> list[dict]:
    """The test's data requests (design §8.4): xhr, fetch and POST answers, never a document (the page itself, a
    frame, where a redirect lands)."""
    return [d for d in rec.get("data_requests") or () if d.get("type") in ("xhr", "fetch")
            or (d.get("method") == "POST" and d.get("type") != "document")]


def _data_urls(rec: dict) -> str | None:
    """page_type.data_urls: the data requests the test saw, each once, with whether its answer held the new text."""
    seen: dict[tuple[str | None, str], bool] = {}
    for d in _data_requests(rec):
        k = (d.get("method"), d.get("url") or "")
        seen[k] = seen.get(k, False) or bool(d.get("has_new_text"))
    if not seen:
        return None
    return json.dumps({"requests": [{"url": u, "method": m, "new_text": t} for (m, u), t in seen.items()]},
                      ensure_ascii=False)


def _scripted_stop(rec: dict) -> str | None:
    """Where the browser stopped when the page sent it on by script (design §8.2: the test's last address is not the
    sample's, the sample itself answered 200 and no 3xx sent it there), else None."""
    url = rec.get("url") or ""
    stop = (rec.get("final_url") or "").split("#", 1)[0]
    if not stop or stop == url or stop.startswith(_NOWHERE):
        return None
    if any(d.get("type") == "document" and 300 <= (d.get("status") or 0) < 400 for d in rec.get("data_requests") or ()):
        return None
    return stop
