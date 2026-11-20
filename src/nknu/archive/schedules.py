"""The course query's class schedules (design docs/superpowers/specs/2026-11-18-course-schedules-design.md §五): the
school's course query (sso.nknu.edu.tw/stu) shows a class's courses, with each one's time, room and teacher, only once
its form is sent. What the probe of 11-18 found (10 requests, read only): a blank course name is refused (「課程名稱」請輸入
資料), a department's 「全年級選課用」 class is not the sum of its year classes, and every row of a search links the class's
own address, scheduleDepartment.aspx?cDES=<a fixed token>, which shows the same table by a plain GET, with no cookie.

So the form is walked once (walk): by day and by night, each kind of programme, each department, each class, one
search a class, as a browser sends it (the page's own term, never one worked out here). The walk keeps no answer:
it keeps the addresses the rows name, and what it found is written beside the ledger's reports (REPORT). seed hands
those addresses to the crawler as pages of the site, one step in from its home: fetched by `nknu archive run`, kept
in the WARC, exported and looked at again like every standing page of a site under periodic update. A teacher's and
a room's table are the same courses again and are never followed (the site's skip paths, round1.Site.skips); the form's
own controls are no gap of a class page (profiles.STU).

    python -m nknu.archive.schedules walk --dir <archive> [--gap 1-2] [--limit 1500]
    python -m nknu.archive.schedules seed --dir <archive> [--write]
    python -m nknu.archive.schedules --dir <archive> night [--gap 1-2]

walk sends requests to the school: it holds the machine-wide school lock for its whole life (refused while a crawl
runs), one request at a time on the crawler's own client, the gap given between requests. It writes nothing to the
ledger. seed sends nothing; no run may hold run.lock.

night is the nightly update's step, between its put-back and its run (11-19, the wrap-up's 每学期初自动重查课表). The
form shows one term, the page's own; the site's home is that page, fetched every night. night reads the term off the
copy the archive holds (no request) and compares it with the term whose classes the ledger holds (meta TERM_KEY; a
ledger walked before 11-19 has it in the walk's report). The same: nothing is sent. Another: a warning event, then
walk and seed --write, as a person ran them on 11-18; the new classes are fetched by the run that follows, and the
term is open (meta OPEN_KEY). While a term is open the rows of this one site that the nightly growth cap holds
(cap:run_*: a term's outlines are some thousands, a night's cap 300) are let go, LET_GO a night, oldest first; the
term closes once nothing of the site is held or waiting on a night after the one it opened, or after OPEN_DAYS
whatever is left (a warning: what is still held waits for a person, as any site's). A copy that cannot be read, or a
walk that did not end, changes nothing and says so: the next night looks again. What the step found is written to
reports/TERM_REPORT. Never while a run holds run.lock."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser

SITE = "sso.nknu.edu.tw/stu"
PAGE = "https://sso.nknu.edu.tw/stu/scheduledepartment.aspx"
FORM_CSS = "form#uForm"
P = "ctl00$phMain$"
DN, KIND, DEPT, CLASS, SEARCH = P + "uDN", P + "uDeformType", P + "uDepartment", P + "uClass", P + "uSearch"
YEAR, SEMESTER = P + "uYearSemester$uYear", P + "uYearSemester$uSemester"
SHOWN_CSS = "#ctl00_phMain_upShow"
CLASS_LINK = re.compile(r"(?i)(?:^|/)scheduledepartment\.aspx\?cdes=[^&#\s]+$")
OUTLINE_LINK = re.compile(r"(?i)syllabusforenro\.aspx\?syllabusno=(\w+)")  # a course's outline, one a course
ROW_CSS = "[onclick*='showSelectList(']"  # each course row holds one 選課名單 picture (the page's script names it too)
REPORT = "schedule-classes.json"
SEED_SOURCE = "schedule_walk"  # seed_src: never one of update.HOME_SOURCES (a home is fetched every night)
LIMIT = 1500
TERM_KEY = "schedules:term"  # meta: the term whose classes the ledger holds (the last whole walk that was seeded)
OPEN_KEY = "schedules:open"  # meta: {"term", "opened": a Taipei date} while that term's pages still come in
TERM_REPORT = "schedule-term.json"
LET_GO = 1500  # rows of the site let go a night while a term is open (about 40 minutes at the nightly's gap)
OPEN_DAYS = 21  # a term still open after this many days is closed, and said
TZ = ZoneInfo("Asia/Taipei")
Ask = Callable[[str, "list[tuple[str, str]] | None"], str]


@dataclass(frozen=True)
class Found:
    """One class the form offers: by day or night (dn), its kind of programme (form), department and class as the
    form's values and words, how many course rows its search showed, and the class addresses those rows name (its own;
    a row of a class taught together names that class's)."""

    dn: str
    form: str
    form_name: str
    dept: str
    dept_name: str
    cls: str
    cls_name: str
    courses: int
    links: tuple[str, ...] = ()


@dataclass
class Walked:
    classes: list[Found] = field(default_factory=list)
    urls: dict[str, list[str]] = field(default_factory=dict)  # a class's address -> the words its links carry
    term: str = ""  # the year and semester the form was sent with: the page's own
    outlines: set[str] = field(default_factory=set)  # the course outlines the rows name (their numbers): how many courses
    requests: int = 0
    complete: bool = False
    note: str = ""


class _Stop(Exception):
    pass


# ---------------------------------------------------------------- the form, as a browser reads and sends it

def _form(html: str):
    return LexborHTMLParser(html or "").css_first(FORM_CSS)


def form_fields(html: str) -> list[tuple[str, str]]:
    """The form's fields as a browser sends them: hidden and text inputs, the checked radios, each select's chosen
    option (its first when none is marked); no button."""
    form = _form(html)
    out: list[tuple[str, str]] = []
    for n in (form.css("input, select, textarea") if form is not None else ()):
        name = n.attributes.get("name")
        if not name or "disabled" in n.attributes:
            continue
        if n.tag == "select":
            opts = n.css("option")
            chosen = [o for o in opts if "selected" in o.attributes] or opts[:1]
            if chosen:
                out.append((name, chosen[0].attributes.get("value") or ""))
        elif n.tag == "input":
            kind = (n.attributes.get("type") or "text").lower()
            if kind in ("submit", "button", "image", "file", "reset"):
                continue
            if kind in ("radio", "checkbox") and "checked" not in n.attributes:
                continue
            out.append((name, n.attributes.get("value") or ""))
        else:
            out.append((name, n.text() or ""))
    return out


def post(fields: list[tuple[str, str]], target: str, **over: str) -> list[tuple[str, str]]:
    """What __doPostBack(target, '') sends: the form's fields, each one in `over` set to its value (once), under the
    event's target."""
    out, seen = [], set()
    for k, v in fields:
        if k in ("__EVENTTARGET", "__EVENTARGUMENT"):
            continue
        if k in over:
            if k not in seen:
                out.append((k, over[k]))
            seen.add(k)
        else:
            out.append((k, v))
    out += [(k, v) for k, v in over.items() if k not in seen]
    return [("__EVENTTARGET", target), ("__EVENTARGUMENT", ""), *out]


def options(html: str, name: str) -> list[tuple[str, str, bool]]:
    """A select's options: (value, words, chosen); the first counts as chosen when none is marked."""
    form = _form(html)
    select = next((s for s in (form.css("select") if form is not None else ()) if s.attributes.get("name") == name), None)
    opts = [(o.attributes.get("value") or "", re.sub(r"\s+", " ", o.text() or "").strip(), "selected" in o.attributes)
            for o in (select.css("option") if select is not None else ())]
    if opts and not any(c for _v, _t, c in opts):
        opts[0] = (opts[0][0], opts[0][1], True)
    return opts


def radios(html: str, name: str) -> list[tuple[str, str, str, bool]]:
    """A radio group: (value, words, the event target its click posts, checked), in the page's order. The target is
    the one its onclick names, else the group's name and the radio's place (ASP.NET's RadioButtonList)."""
    form = _form(html)
    tree = LexborHTMLParser(html or "")
    out = []
    group = [n for n in (form.css("input") if form is not None else ())
             if n.attributes.get("name") == name and (n.attributes.get("type") or "").lower() == "radio"]
    for i, n in enumerate(group):
        m = re.search(r"__doPostBack\(\\?'([^'\\]+)", n.attributes.get("onclick") or "")
        label = tree.css_first(f'label[for="{n.attributes.get("id")}"]') if n.attributes.get("id") else None
        words = re.sub(r"\s+", " ", label.text() or "").strip() if label is not None else ""
        out.append((n.attributes.get("value") or "", words, m.group(1) if m else f"{name}${i}", "checked" in n.attributes))
    return out


def _chosen(found: list[tuple], at: int = -1) -> str | None:
    return next((x[0] for x in found if x[at]), None)


def page_term(html: str) -> str:
    """The year and semester the query page shows: the page's own, as its form sends them ("" for a page with no
    query form)."""
    if _form(html) is None:
        return ""
    year, sem = options(html, YEAR), options(html, SEMESTER)
    return " ".join(x for x in (_chosen(year) or "", next((t for _v, t, c in sem if c), "")) if x)


def class_links(html: str, base: str = PAGE) -> tuple[int, dict[str, str]]:
    """What a search's answer shows: how many course rows, and the class addresses its rows name (absolute, as
    written) with the words of their links."""
    shown = LexborHTMLParser(html or "").css_first(SHOWN_CSS)
    if shown is None:
        return 0, {}
    found: dict[str, str] = {}
    for a in shown.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if CLASS_LINK.search(href):
            words = re.sub(r"\s+", " ", a.text() or "").strip()
            url = urljoin(base, href)
            if words or url not in found:
                found[url] = words or found.get(url, "")
    return len(shown.css(ROW_CSS)), found


# ---------------------------------------------------------------- the walk

def walk(ask: Ask, limit: int = LIMIT, say: Callable[[str], None] = lambda s: None) -> Walked:
    """Every class the form offers, searched once. ask(url, data) sends one request (data None: a GET) and returns the
    answer's text. A switch (day or night, a kind of programme, a department) is sent from the page the form is on,
    and each class's search from its department's page, so the lists the form holds are always the server's own. The
    kind of programme a switch lands on is walked first, never asked for twice. Stops, not complete, at `limit`
    requests or at an answer that holds no form."""
    got = Walked()

    def send(what: str, data: list[tuple[str, str]] | None) -> str:
        if got.requests >= limit:
            raise _Stop(f"the limit of {limit} requests was reached before {what}")
        got.requests += 1
        html = ask(PAGE, data)
        if _form(html) is None or not radios(html, DN):
            raise _Stop(f"the answer to {what} holds no query form")
        return html

    try:
        page = send("the query page", None)
        got.term = page_term(page)
        for dn, dn_words, dn_target, _on in radios(page, DN):
            if _chosen(radios(page, DN)) != dn:
                page = send(f"{dn_words or dn}", post(form_fields(page), dn_target, **{DN: dn}))
            kinds = radios(page, KIND)
            for kind, kind_words, kind_target, _on in sorted(kinds, key=lambda k: not k[3]):  # the one shown first
                if _chosen(radios(page, KIND)) != kind:
                    page = send(f"{dn}/{kind_words or kind}", post(form_fields(page), kind_target, **{KIND: kind}))
                for dept, dept_words, _sel in options(page, DEPT):
                    if _chosen(options(page, DEPT)) != dept:
                        page = send(f"{dn}/{kind}/{dept_words}", post(form_fields(page), DEPT, **{DEPT: dept}))
                    for cls, cls_words, _sel in options(page, CLASS):
                        answer = send(f"{dn}/{kind}/{dept_words}/{cls_words}",
                                      post(form_fields(page), SEARCH, **{CLASS: cls}))
                        n, links = class_links(answer)
                        got.outlines |= set(OUTLINE_LINK.findall(answer))
                        for url, words in links.items():
                            if words not in got.urls.setdefault(url, []):
                                got.urls[url].append(words)
                        got.classes.append(Found(dn, kind, kind_words, dept, dept_words, cls, cls_words, n, tuple(links)))
                    say(f"{dn} {kind_words or kind} {dept_words}: {len(options(page, CLASS))} 个班；到现在 "
                        f"{len(got.classes)} 个班、{len(got.urls)} 个班级网址、{got.requests} 个请求")
        got.complete = True
    except _Stop as stop:
        got.note = str(stop)
    return got


# ---------------------------------------------------------------- the command line

def _school(gap: tuple[float, float]) -> tuple[Ask, Callable[[], None]]:
    """The school asked for real: the crawler's own client under the machine-wide school lock (SchoolLockBusy while a
    crawl or anything else sends), one request at a time, the gap between requests. Returns ask and what closes it."""
    from nknu.crawl import schoollock
    from nknu.crawl.adapter import RateLimit, Target
    from nknu.crawl.fetch import Client

    lock = schoollock.hold()
    client = Client(attempts=1)
    rate = RateLimit(min_interval_s=gap[0], jitter_s=max(0.0, gap[1] - gap[0]))

    def ask(url: str, data: list[tuple[str, str]] | None) -> str:
        snap = client.fetch(Target(url, method="GET" if data is None else "POST", data=data), rate)
        if snap.status != 200:
            return ""
        return snap.text or ""

    def close() -> None:
        client.close()
        lock.close()

    return ask, close


def _gap(text: str) -> tuple[float, float]:
    lo, _, hi = text.partition("-")
    a, b = float(lo), float(hi or lo)
    if not 1.0 <= a <= b:
        raise argparse.ArgumentTypeError("间隔要写成 小-大（秒），最小不低于 1")
    return a, b


def _walk(root: Path, gap: tuple[float, float], limit: int) -> int:
    ask, close = _school(gap)
    started = time.time()
    try:
        got = walk(ask, limit, say=lambda s: print(s, flush=True))
    finally:
        close()
    path = root / "reports" / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".new")
    part.write_text(json.dumps({
        "walked_at": datetime.fromtimestamp(started, ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "term": got.term, "requests": got.requests, "complete": got.complete, "note": got.note,
        "outlines": len(got.outlines),
        "urls": got.urls, "classes": [asdict(c) for c in got.classes]}, ensure_ascii=False, indent=1), encoding="utf-8")
    part.replace(path)
    empty = sum(1 for c in got.classes if not c.courses)
    print(f"课表走查{'走完了' if got.complete else '没走完（' + got.note + '）'}：{got.term}，{got.requests} 个请求，"
          f"{len(got.classes)} 个班（其中 {empty} 个班没有课），{len(got.urls)} 个班级网址，课表里共 {len(got.outlines)} 份课程大纲；"
          f"写了 {path}")
    return 0 if got.complete else 1


def _seed(root: Path, write: bool) -> int:
    from nknu.archive import crawler as crawl_mod
    from nknu.archive import scope

    path = root / "reports" / REPORT
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"读不到走查的结果 {path}（{type(exc).__name__}）：先跑 walk", file=sys.stderr)
        return 1
    if not report.get("complete"):
        print(f"上一次走查没走完（{report.get('note') or '原因没写'}）：不拿半份清单加网址，先把 walk 跑完", file=sys.stderr)
        return 1
    urls = sorted(u for u in (report.get("urls") or {}) if CLASS_LINK.search(u))
    if crawl_mod.lock_holder(root):
        print("有抓取正拿着 run.lock：等它结束再加", file=sys.stderr)
        return 1
    crawler = crawl_mod.Crawler(root, sites=[SITE])
    f = crawler.frontier
    known = {u for u in urls if (c := scope.canonical(u)) and f.row(c) is not None}
    if not write:
        print(f"走查记下 {len(urls)} 个班级网址：账本里已经有 {len(known)} 个，要新加 {len(urls) - len(known)} 个"
              "（没带 --write，什么都没改）")
        return 0
    said: dict[str, int] = {}
    ids: list[int] = []
    for u in urls:
        e = crawler._enqueue(u, depth=1, found_via="seed", prio=None)
        said[e.verdict] = said.get(e.verdict, 0) + 1
        if e.url is not None:
            f.seed_src(e.url, SEED_SOURCE)
        if e.id is not None and u not in known:
            ids.append(e.id)
    # the growth cap of a site under periodic update holds what one run adds past it for a person (cap:run_page):
    # these are the classes a person decided to add, all of them
    held = 0
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        held += f.x(f"UPDATE url SET state='pending', next_at=0, attempts=0 WHERE id IN ({','.join('?' * len(part))}) "
                    "AND state='held' AND reason='cap:run_page'", part)
    f.event("info", "schedule_seed", f"{SITE}: {len(ids)} class pages added from the form's walk "
            f"({report.get('term') or '?'}; {held} of them past the nightly growth cap, let go)", "sso.nknu.edu.tw", "")
    print(f"加了 {len(ids)} 个班级网址（本来就有 {len(known)} 个）；其中 {held} 个超过每晚增长上限、已放行；判定 {said}")
    return 0


# ---------------------------------------------------------------- the nightly step

def stored_term(root: Path, f) -> str | None:
    """The term on the copy of the query page the archive holds (the site's home, fetched every night); None when
    there is no good copy or it shows no query form."""
    from nknu.archive import postback, scope

    row = f.row(scope.canonical(PAGE))
    if row is None or row["status"] != 200 or not row["warc"] or row["warc_offset"] is None:
        return None
    try:
        _url, _status, headers, body = postback.read_response(root, row["warc"], row["warc_offset"])
    except (OSError, ValueError):
        return None
    return page_term(postback.as_text(body, headers.get("content-type"))) or None


def _walked_term(root: Path) -> str:
    """The term of the last walk, when it was whole ("" otherwise)."""
    try:
        report = json.loads((root / "reports" / REPORT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(report.get("term") or "") if report.get("complete") else ""


def night(root: Path, *, gap: tuple[float, float] = (1.0, 2.0), limit: int = LIMIT, today: str | None = None) -> int:
    """The nightly step (the module's docstring). today: the Taipei date, for a test. Returns 0, or 1 when the term
    could not be told or the new term's walk did not end (the night goes on either way)."""
    from nknu.archive import crawler as crawl_mod
    from nknu.archive.frontier import Frontier

    today = today or datetime.now(TZ).date().isoformat()
    if crawl_mod.lock_holder(root):
        print("有抓取正拿着 run.lock：课表这一步不做，下一晚再看")
        return 1
    out: dict = {"at": datetime.now(TZ).isoformat(timespec="seconds"), "page_term": None, "ledger_term": None,
                 "changed": None, "walk": None, "open": None, "let_go": 0, "held_left": 0}
    f = Frontier(root / "frontier.db")
    try:
        shown = stored_term(root, f)
        known = f.meta(TERM_KEY)
        if not known and (known := _walked_term(root)):
            f.meta(TERM_KEY, known)  # a ledger walked before the term was kept in it
        if shown is not None and shown != known:
            f.event("warn", "schedule_term", f"{SITE}: the query page shows {shown}, the classes the ledger holds are "
                    f"of {known or 'no term on record'}: the form is walked again", "sso.nknu.edu.tw", PAGE)
    finally:
        f.close()
    out["page_term"], out["ledger_term"] = shown, known or None
    rc = 0
    if shown is None:
        print("读不到课程查询页存下的那一份（还没抓到，或上面没有查询表单）：学期有没有换不知道，这一晚不重走，下一晚再看")
        rc = 1
    elif shown == known:
        out["changed"] = False
        print(f"课表学期没变：{shown}")
    else:
        out["changed"] = True
        print(f"课程查询页的默认学期变了：{known or '（没有纪录）'} → {shown}，重走课表")
        if _walk(root, gap, limit) == 0 and _seed(root, True) == 0:
            term = _walked_term(root) or shown
            f = Frontier(root / "frontier.db")
            try:
                f.meta(TERM_KEY, term)
                f.meta(OPEN_KEY, json.dumps({"term": term, "opened": today}, ensure_ascii=False))
            finally:
                f.close()
            out["walk"] = "done"
        else:
            out["walk"] = "not whole"
            rc = 1
            print("课表没走完（或班级网址没加成）：学期纪录不改，下一晚再走")
    f = Frontier(root / "frontier.db")
    try:
        held_sql = "FROM url WHERE owner_site=? AND state='held' AND reason LIKE 'cap:run_%'"
        try:
            opened = json.loads(f.meta(OPEN_KEY) or "null")
        except ValueError:
            opened = None
        if isinstance(opened, dict) and opened.get("term"):
            held = f.q(f"SELECT count(*) {held_sql}", (SITE,))[0][0]
            waiting = f.q("SELECT count(*) FROM url WHERE owner_site=? AND state IN ('pending', 'active')", (SITE,))[0][0]
            try:
                days = (date.fromisoformat(today) - date.fromisoformat(str(opened.get("opened")))).days
            except ValueError:
                days = OPEN_DAYS
            out["open"] = opened["term"]
            if days >= OPEN_DAYS:
                f.x("DELETE FROM meta WHERE key=?", (OPEN_KEY,))
                f.event("warn", "schedule_term_closed", f"{SITE}: {opened['term']} was still open after {days} days "
                        f"({held} rows held by the nightly growth cap wait for a person)", "sso.nknu.edu.tw", "")
                out["open"] = None
                print(f"{opened['term']} 开了 {days} 天还没进齐：不再自动放行，还挡着的 {held} 行等人看")
            elif held:
                out["let_go"] = f.x(f"UPDATE url SET state='pending', next_at=0, attempts=0 WHERE id IN (SELECT id "
                                    f"{held_sql} ORDER BY id LIMIT ?)", (SITE, LET_GO))
                f.event("info", "schedule_let_go", f"{SITE}: {out['let_go']} of the {held} rows a nightly growth cap "
                        f"held go back to pending ({opened['term']} is open; this site only)", "sso.nknu.edu.tw", "")
                print(f"{opened['term']} 的课表还在进：放行 {out['let_go']} 行（每晚最多 {LET_GO}），还挡着 {held - out['let_go']} 行")
            elif days >= 1 and not waiting:
                f.x("DELETE FROM meta WHERE key=?", (OPEN_KEY,))
                out["open"] = None
                print(f"{opened['term']} 的课表进齐了：这一站回到每晚增长上限底下")
        out["held_left"] = f.q(f"SELECT count(*) {held_sql}", (SITE,))[0][0]
    finally:
        f.close()
    path = root / "reports" / TERM_REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".new")
    part.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    part.replace(path)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.archive.schedules", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["walk", "seed", "night"])
    ap.add_argument("--dir", type=Path, required=True, help="存档目录（里面有 frontier.db）")
    ap.add_argument("--gap", type=_gap, default=(2.0, 5.0), help="walk、night 用：两次请求的间隔，小-大（秒），默认 2-5")
    ap.add_argument("--limit", type=int, default=LIMIT, help=f"walk 用：最多送几个请求（默认 {LIMIT}）")
    ap.add_argument("--write", action="store_true", help="seed 用：真的写进账本（不带就只数）")
    a = ap.parse_args(argv)
    if not (a.dir / "frontier.db").is_file():
        print(f"{a.dir} 里没有 frontier.db（--dir 打错了？）：什么都没做", file=sys.stderr)
        return 1
    if a.action == "night":
        return night(a.dir, gap=a.gap, limit=a.limit)
    return _walk(a.dir, a.gap, a.limit) if a.action == "walk" else _seed(a.dir, a.write)


if __name__ == "__main__":
    sys.exit(main())
