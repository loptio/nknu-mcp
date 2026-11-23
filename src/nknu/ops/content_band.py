"""內容's band (11-23 design, owner: 「照这个重做」, then 「抓取覆蓋」): one card on top of the page of every site, 抓取覆蓋. It
answers one thing: what this round has not fetched yet and whose it is to act on. A big coverage figure with its line
over the last nights, then 要處理 (ours to look at or retry: 待確認 per cause, a failed connection, what still waits) and
學校那邊 (gone, behind a login: we only record them), a row a cause, every row opening its addresses in the crawl drawer
(window.RAWSHOW, the drawers layer), and 定時更新 in its head, the way into the night view (/sites?date=<run day>).

The numbers are one source: the crawler's coverage.jsonl (a line a night, written after the ingest: nknu.ops.sources),
summed over the library's sites; without it (an old install, a test) the live ledger reading of the crawl step
(flow.snapshot) stands in, and the card draws no line. The words are names.py's. The page of every site
(overview_page.html) is drawn once a library and kept as bytes; this band is drawn at each request above it. Its
styles and clicks are the content layer (theme/design/content.css, content.js).

Gone with the 11-23 design: the 爬蟲狀態 card's last-night strip, its sentence and side-run pills (the side runs live in
the night view's 爬取 step now), the 沒有文件的 card (owner: 「如果我想知道哪些没文件的话直接用筛选来实现」: 品質提醒's 無文件 row
filters the page), the page head with its 內容 title and 按網站 / 按單位 switch (the top bar names the page; 單位 is gone:
/units sends to /sites).
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from nknu.ops.look import e
from nknu.ops.names import rename

log = logging.getLogger(__name__)

# what web.py wires for this area (the integration adds the routes; this module draws the band):
# - GET /units: 單位 is gone (owner 11-23: 「把单位页面拿掉」); an old link lands on 內容.
# - GET /sites: html = overview.fill(page, band=content_band.band_for(where, data or {}, flow.snapshot(), night),
#   night_band=night_view.band_html(...), night_log=night_view.log_html()); no units_page.load any more. With
#   ?embed=1 (總覽's frame) the band and the night parts are left out: overview.fill(page, embed_side=...).
ROUTES_TO_WIRE = [
    {"path": "/units", "method": "GET", "handler": "nknu.ops.units_page.moved", "args": "request",
     "returns": "307 to /sites (Cache-Control: no-store), replacing web.py's units_view"},
]

# the crawler's buckets (nknu.archive.outcome.BUCKETS) by whose they are: 學校那邊 is the school's (we only record it),
# what the round does not mean to fetch is no loss, and everything else, a bucket a later crawler adds too, is ours
# (要處理), so no count goes missing from both lists while it lowers the figure
SCHOOL = ("gone", "login", "school_side")
NOT_MEANT = ("rule", "next_round")
GOT = "got"
HINT = "這一輪該抓的網址裡沒抓到的，大多是學校那邊刪了頁、連不上或不公開，不是昨晚才壞的；「待確認」的要人看一眼。"
GO_TIP = "每天 01:00 自動更新資料庫：抓取 → 導出 → 轉文字 → 建庫 → 換庫，每一次的經過"
CLOCK = ("<svg viewBox='0 0 24 24' width='17' height='17'><path d='M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8'/>"
         "<path d='M3 3v5h5'/><path d='M12 7v5l4 2'/></svg>")
REVIEW = "待確認："
NIGHTS = 7  # the line's nights (owner 11-23: the last week)


def _card_css() -> str:
    """總覽's card styles without what is 總覽's own page (its main's width and padding, the colour scheme line): the
    pages the band and the night view draw use its card shell and lists. Read when drawn, so a change there never
    stops this page from loading."""
    try:
        from nknu.ops import panorama

        css = panorama.CSS.replace(":root{color-scheme:light dark}", "")
        at = css.find("main{")
        return css[:at] + css[css.index("}", at) + 1:] if at >= 0 else css
    except Exception:  # noqa: BLE001 — the card has its own shell in content.css
        return ""


def run_day(night: str) -> str:
    """The day a night runs (its 01:00 start; owner 11-23: 「名字按开跑那天」) from the panel's night key (the evening
    before: nightly.night_of); the one mapping is nknu.ops.sources.run_day (critic CR-03)."""
    try:
        from nknu.ops.sources import run_day as of_night
    except ImportError:  # an install without it: the same rule here
        def of_night(n: str) -> str:
            return (date.fromisoformat(n[:10]) + timedelta(days=1)).isoformat()
    try:
        return of_night(night)
    except (TypeError, ValueError):
        return ""


def bucket_words() -> dict[str, str]:
    """A bucket's word, names.py's (連接失敗 -> 連線失敗, …)."""
    try:
        from nknu.ops.pipeline import bucket_words as words

        got = words()
    except Exception:  # noqa: BLE001 — the crawler's module missing: the code stands for itself
        got = {}
    return {b: rename(w) for b, w in got.items()}


def review_word(word: str) -> str:
    """A 待確認 cause as the drawer names it: 「待確認：」 and names.py's word (a raw result code is put in words first)."""
    if word.isascii():  # a result code (thin, api_error, …): the crawler's own words for it, the first clause
        try:
            from nknu.archive.outcome import RESULT_LABEL
            from nknu.ops.pipeline import tw

            word = tw(RESULT_LABEL.get(word) or word).split("；")[0]
        except ImportError:
            log.info("内容：认不得的抓取结果代码 %s，照原样显示", word)
    return rename(word if word.startswith(REVIEW) else REVIEW + word)


def _order(row: dict) -> tuple:
    return -row["n"], row["label"]


def causes(buckets: dict[str, int], review: dict[str, int] | None) -> tuple[list[dict], list[dict]]:
    """要處理 and 學校那邊 of one reading: rows {label (the drawer's word), bucket, n}, largest first. 待確認 is split
    by cause when the causes add up to it (owner 11-21: a bucket that cannot say why says nothing)."""
    words = bucket_words()
    ours: list[dict] = []
    school: list[dict] = []
    split: dict[str, int] = {}
    for w, n in (review or {}).items():  # two causes the new words name alike are one row (names.merge's rule)
        if n:
            split[review_word(w)] = split.get(review_word(w), 0) + n
    for b, n in (buckets or {}).items():
        if not n or b == GOT or b in NOT_MEANT:
            continue
        if b == "review" and split and sum(split.values()) == n:
            ours += [{"label": w, "bucket": b, "n": k} for w, k in split.items()]
            continue
        row = {"label": words.get(b) or b, "bucket": b, "n": n, "whole": True}
        (school if b in SCHOOL else ours).append(row)
    return sorted(ours, key=_order), sorted(school, key=_order)


def _sides(buckets: dict[str, int]) -> tuple[int, int, int, int]:
    """got, meant, 要處理, 學校那邊 of a reading."""
    got = buckets.get(GOT, 0)
    meant = sum(n for b, n in buckets.items() if b not in NOT_MEANT)
    school = sum(buckets.get(b, 0) for b in SCHOOL)
    return got, meant, meant - got - school, school


def _day(s: object) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def window(nights: list[dict] | None, until: str | None = None, since: str | None = None) -> list[tuple[date, dict | None]]:
    """The line's days, oldest first, each with its coverage line or None (a gap): the week that ends on until (the
    run day 定時更新 opens: the latest night, whether its line is written yet or not; never before the newest line),
    from since on (the first night coverage.jsonl has: the days before it had no step to run, so they are no gap; by
    default the oldest line given). [] when no line falls in that week."""
    lines = sorted((n for n in nights or [] if _day(n.get("night")) and n.get("meant")), key=lambda n: n["night"])
    if not lines:
        return []
    newest = _day(lines[-1]["night"])
    end = max(newest, _day(until) or newest)
    began = min(_day(lines[0]["night"]), _day(since) or newest)
    first = max(end - timedelta(days=NIGHTS - 1), began)
    by = {n["night"][:10]: n for n in lines}
    slots = [(first + timedelta(days=i), by.get((first + timedelta(days=i)).isoformat()))
             for i in range((end - first).days + 1)]
    return slots if any(n for _d, n in slots) else []


def _gap_tip(d: date, end: date) -> str:
    if d == end:  # the latest night: its line is written once the night has run, so early in the morning it is not there yet
        return "這一晚還沒有記下抓取覆蓋（整晚跑完才記；跑完了還沒有，就是那一步沒跑）"
    return "這一晚沒有記下抓取覆蓋（那一步沒跑）"


def spark(nights: list[dict], until: str | None = None, since: str | None = None) -> str:
    """The nights' coverage as a small line (owner 11-23: 「可视化再优化一下」): a fixed scale from 80% (or lower, a
    whole ten below the lowest night) to 100% with three quiet grid lines, a soft area, every night's day under its
    point, the newest point marked (its day in bold: the headline's night), and a column a night as the hover target.
    The days are window()'s week up to the run day: a night with no line in coverage.jsonl (the step did not run, or
    tonight's not written yet) is a gap, the last day included: no point, no line through it, its column says so."""
    days = window(nights, until, since)
    if not days:
        return ""
    slots = []
    for d, n in days:
        if n is None:
            slots.append((d, None))
            continue
        got, meant, ours, school = _sides(n.get("buckets") or {})
        got, meant = n.get("got", got), n.get("meant", meant)
        slots.append((d, (got / meant * 100, got, meant, ours, school)))
    pts = [p for _d, p in slots if p]
    end = slots[-1][0]
    newest = max(k for k, (_d, p) in enumerate(slots) if p)
    lo = min(80.0, (min(p[0] for p in pts) // 10) * 10)
    hi = 100.0
    w2, h2, pl, pr, pt, pb = 216, 64, 2, 30, 6, 16
    cw = (w2 - pl - pr) / max(1, len(slots) - 1)

    def x_of(i: int) -> float:
        return pl + 4 + i * (w2 - pl - pr - 8) / max(1, len(slots) - 1)

    def y_of(v: float) -> float:
        return pt + (hi - v) / (hi - lo) * (h2 - pt - pb)

    base = y_of(lo)
    xy = [(x_of(i), y_of(p[0]) if p else None) for i, (_d, p) in enumerate(slots)]
    runs: list[list[tuple[float, float]]] = [[]]
    for x, y in xy:
        if y is None:
            runs.append([])
        else:
            runs[-1].append((x, y))
    runs = [r for r in runs if r]
    area = "".join(
        f"<path class='a' d='M{r[0][0]:.1f},{base:.1f} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in r)
        + f" L{r[-1][0]:.1f},{base:.1f} Z'/>" for r in runs if len(r) > 1)
    line = "".join(f"<polyline points='{' '.join(f'{x:.1f},{y:.1f}' for x, y in r)}'/>" for r in runs if len(r) > 1)
    alone = "".join(f"<circle class='pt' cx='{r[0][0]:.1f}' cy='{r[0][1]:.1f}' r='2.5'/>" for r in runs[:-1] if len(r) == 1)
    grid = "".join(f"<line class='g' x1='{pl}' x2='{w2 - pr + 4}' y1='{y_of(v):.1f}' y2='{y_of(v):.1f}'/>"
                   f"<text class='gl' x='{w2 - pr + 7}' y='{y_of(v) + 3:.1f}'>{v:.0f}{'%' if v == hi else ''}</text>"
                   for v in (hi, (hi + lo) / 2, lo))
    labels = "".join(f"<text class='dx{' l' if k == newest else ''}' x='{x:.1f}' y='{h2 - 3}'>"
                     f"{(f'{d.month}/' if k == 0 else '') + str(d.day)}</text>"
                     for k, ((x, _y), (d, _p)) in enumerate(zip(xy, slots)))
    hits = []
    for (x, y), (d, p) in zip(xy, slots):
        rect = f"<rect x='{x - cw / 2:.1f}' y='0' width='{cw:.1f}' height='{h2}'/>"
        if p is None:
            hits.append(f"<g class='h' data-tip-h='{d.month}/{d.day}' data-tip='{_gap_tip(d, end)}'>{rect}</g>")
            continue
        pct, got, meant, ours, school = p
        hits.append(f"<g class='h' data-tip-h='{d.month}/{d.day} 抓完 · {pct:.2f}%' "
                    f"data-tip='{rename('抓到了')} {got:,}、該抓 {meant:,}、要處理 {ours:,}、學校那邊 {school:,}'>{rect}"
                    f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{pt}' y2='{base:.1f}'/>"
                    f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3'/></g>")
    lx, ly = xy[newest]
    return (f"<svg class='cs-spark' viewBox='0 0 {w2} {h2}' width='{w2}' height='{h2}' role='img' "
            f"aria-label='近 {len(pts)} 晚的抓到比例，{pts[0][0]:.1f}% 到 {pts[-1][0]:.1f}%'>{grid}"
            f"{area}{line}{alone}<circle class='last' cx='{lx:.1f}' cy='{ly:.1f}' r='3.4'/>{labels}{''.join(hits)}</svg>")


def _rows(items: list[dict]) -> str:
    """A cause a row: its name (「待確認：」 and the school's 「校方」 left to the group's title), a bar scaled to the
    largest, the count; data-label is the drawer's word, so the drawer opens on exactly what the row counts."""
    mx = max([r["n"] for r in items] or [1])
    out = []
    for r in items:
        name = r["label"].replace(REVIEW, "").removeprefix("校方")
        key = f"data-bucket='{e(r['bucket'])}'" if r.get("whole") and r["bucket"] == "review" else f"data-label='{e(r['label'])}'"
        out.append(f"<li class='cs-go-li' {key} role='button' tabindex='0'><span>{e(name)}</span>"
                   f"<i><b style='width:{max(1.5, r['n'] / mx * 100):.1f}%'></b></i><b>{r['n']:,}</b></li>")
    return "".join(out)


def _from_step(snap: dict) -> tuple[int, int, list[dict], list[dict], str]:
    """The card's numbers from the live crawl step when coverage.jsonl is not there: got, meant, the two lists, and
    the step's own words when it has no figure (檢視台沒有掛上爬蟲的帳本, …)."""
    crawl = next((s for s in snap.get("steps") or [] if s.get("key") == "crawl"), {})
    bar = crawl.get("bar") or []
    got, meant = (bar[0], bar[1]) if len(bar) == 2 and bar[1] else (0, 0)
    words = bucket_words()
    of_word = {w: b for b, w in words.items()}
    rows = crawl.get("lost_buckets")  # [(bucket, word, n)] (DATA, 11-23), else the words alone
    if rows:
        lost = [(rename(w), b, n) for b, w, n in rows]
    else:
        lost = []
        for w, n in crawl.get("lost") or []:
            w = rename(w)
            lost.append((w, "review" if w.startswith(REVIEW) or w == words.get("review") else of_word.get(w, ""), n))
    ours, school = [], []
    for w, b, n in lost:
        if not n or b in NOT_MEANT or b == GOT:
            continue
        row = {"label": w, "bucket": b or "review", "n": n, "whole": not w.startswith(REVIEW)}
        (school if b in SCHOOL else ours).append(row)
    return got, meant, sorted(ours, key=_order), sorted(school, key=_order), crawl.get("say") or ""


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def coverage_card(nights: list[dict] | None, snap: dict, day: str, since: str | None = None) -> str:
    """抓取覆蓋 (cards_all.py's markup): the head with its help and the clock to 定時更新, the coverage figure with the
    nights' line, 要處理 and 學校那邊. day: the run day 定時更新 opens, where the line's week ends; since: the first
    night coverage.jsonl has (window). The figure is the newest line in that week; when that is not the run day's (its
    line not written yet, or the step did not run) the figure says which night it is."""
    days = window(nights, day, since)
    lines = [n for _d, n in days if n]
    old = ""
    if lines:
        last = lines[-1]
        got, meant = last.get("got", 0), last.get("meant", 0)
        ours, school = causes(last.get("buckets") or {}, last.get("review"))
        line, said = spark(nights or [], day, since), ""
        at, end = _day(last["night"]), days[-1][0]
        if at < end:
            old = f"{_md(at)} 那一晚的；{_md(end)} 這一晚還沒記下"
    else:
        got, meant, ours, school, said = _from_step(snap)
        line = ""
    href = f"/sites?date={quote(day)}" if day else "/sites?date="
    head = (f"<div class='pn-chd'><h2>抓取覆蓋</h2><button type='button' class='hp cs-hp' data-tip='{e(HINT)}'>?</button>"
            f"<a class='cs-go' href='{e(href)}' aria-label='定時更新' data-tip-h='定時更新' data-tip='{e(GO_TIP)}'>"
            f"{CLOCK}<span>定時更新</span></a></div>")
    if not meant:
        return (f"<section class='pn-card cs' id='csCard'>{head}"
                f"<p class='pn-empty'>{e(said or '還沒有抓取覆蓋的紀錄')}</p></section>")
    pct = got / meant * 100
    which = f"<span class='cs-old'>{old}</span>" if old else ""
    many = len(ours) > 10  # two columns of five; more causes lengthen the columns, never a third
    rows_style = f" style='grid-template-rows:repeat({math.ceil(len(ours) / 2)},auto)'" if many else ""
    return (f"<section class='pn-card cs' id='csCard'>{head}<div class='cs-body'>"
            f"<div class='cs-cov' role='button' tabindex='0' data-tip-h='這一輪該抓的抓到' "
            f"data-tip='{got:,} / {meant:,} 個網址{f'（{old}）' if old else ''}；點開看全部'>"
            f"<b class='cs-pct'>{pct:.1f}<small>%</small></b><span class='cs-cn'>{got:,} / {meant:,}</span>"
            f"{which}{line}</div>"
            f"<div class='cs-grp cs-ours'><h5 data-tip='待確認的、連線失敗的、還在排的：要我們看一眼或重試'>要處理 "
            f"<b>{sum(r['n'] for r in ours):,}</b></h5><ul{rows_style}>{_rows(ours)}</ul></div>"
            f"<div class='cs-grp cs-sch'><h5 data-tip='學校刪了頁、要登入：我們修不了，只記下來'>學校那邊 "
            f"<b>{sum(r['n'] for r in school):,}</b></h5><ul>{_rows(school)}</ul></div>"
            f"</div></section>")


def band(sites: dict, snap: dict, tonight: dict, tree: object = None, *, nights: list[dict] | None = None,
         since: str | None = None) -> str:
    """The band: a heading for screen readers (the top bar names the page), then 抓取覆蓋. tonight gives the night
    定時更新 opens, where the line's week ends; nights: coverage.jsonl's last lines and since its first night
    (band_for reads them). tree is no longer used (單位 is gone) and is taken only so an older caller still draws."""
    del sites, tree
    card = coverage_card(nights, snap, run_day(str((tonight or {}).get("default") or "")), since)
    return (f"<style>{_card_css()}</style><section class='ct-band' id='crawl' aria-label='缺什麼'>"
            f"<h1 class='ct-h1'>內容</h1><div class='pn-bento cs-bento'>{card}</div></section>")


def coverage_of(archive: Path | None, scope: set[str], library: Path | None = None,
                until: str | None = None) -> list[dict]:
    """coverage.jsonl's nights of the week up to until (a run day; by default its newest line's) summed over scope, the
    library's site keys (nknu.ops.sources; with library, the folder of the library in use, a night with a swap record
    is summed over that night's own library), [] when it cannot be read or there is no scope (never every site of the
    line: round two's and other hosts are not the library's): the card then draws from the live reading, no line."""
    if archive is None or not scope:
        return []
    try:
        from nknu.ops import sources

        kw = {"library": library} if library is not None else {}
        if until:
            kw["until"] = until
        return list(sources.coverage_nights(Path(archive), set(scope), limit=NIGHTS, **kw) or [])
    except Exception as exc:  # noqa: BLE001 — an old install, a half-written line: the card says less, never fails
        log.info("内容：读不到抓取覆盖（%s），用帐本当下的数字", exc)
        return []


def _scope(where: object, sites: dict, sources: object) -> set[str]:
    """The sites the card sums over: the library's sites table (CR-01), else (a library being swapped in, a test
    without one) the sites the page of every site lists; empty when neither says."""
    try:
        keys = set(sources.library_sites(where.library) or ())
    except Exception:  # noqa: BLE001 — no library to read: the page's own list
        keys = set()
    if not keys:
        keys = {s.get("k") for s in (sites or {}).get("sites") or [] if s.get("k")}
        if keys:
            log.info("内容：读不到库的网站表，抓取覆盖按页面列的 %d 个网站加总", len(keys))
    return keys


def band_for(where: object, sites: dict, snap: dict, tonight: dict) -> str:
    """The band as /sites draws it: coverage read from the crawler's archive folder (where.archive) over the library's
    sites, each night over its own library's sites when the night has a swap record beside the library in use, for the
    week that ends on the run day 定時更新 opens (or on the newest line, when that is later): a night without a line,
    tonight's too, is a gap."""
    archive = getattr(where, "archive", None)
    day = run_day(str((tonight or {}).get("default") or ""))
    try:
        from nknu.ops import sources
    except ImportError:  # an install without it: the live reading alone
        return band(sites, snap, tonight)
    try:
        library = sources.library_dir(where)
    except Exception:  # noqa: BLE001 — no library path: today's sites for every night
        library = None
    try:
        lines = list(sources.coverage_lines(Path(archive))) if archive is not None else []
    except Exception:  # noqa: BLE001 — unreadable: coverage_of says so
        lines = []
    newest = str(lines[-1].get("night") or "") if lines else ""
    until = max(day, newest) or None
    since = str(lines[0].get("night") or "") or None if lines else None
    nights = coverage_of(archive, _scope(where, sites, sources), library, until)
    return band(sites, snap, tonight, nights=nights, since=since)
