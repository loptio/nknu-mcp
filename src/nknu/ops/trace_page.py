"""文件追蹤's pages (nknu.ops.web serves them; the page was 逐份追蹤 until 11-15): one page or file followed through the five steps, drawn from what
nknu.ops.trace.trace returns; and, for words that are no id and no address, the documents whose titles hold them.

The layout is 【Claude】's (owner 11-15: 「信息太雜…沒有用一些視覺化方法點綴」; the reasons are in nknu.ops.look): the
document itself first (its title, what it is, whose, of when), then the five steps as one line of states, then a card
per step that leads with a few facts and folds the rest. What can be drawn is: the dates the build read on one line
with today, the document's chunks as a strip to click, how much of a page the content area held, how many pages
needed OCR. Every name is a plain one and what it means floats over it (owner 11-15: 「很多名字看着不直觉 然后感觉是
想缩短文字 但又想保留细节导致不上不下的」): a step with no record says why when hovered, not in a list under the steps."""

from __future__ import annotations

from datetime import date
from urllib.parse import unquote, urlencode

from nknu.ops.explain import WHY
from nknu.ops.look import KIND_ICON, e, icon, kind_tag, tw
from nknu.ops.search_page import why_url
from nknu.ops.trace import (
    DATE_KINDS,
    KINDS,
    cohort_words,
    how_converted,
    ident_words,
    merged_words,
    size_words,
    under_read_words,
    when,
)

MARKUP = (".html", ".htm", ".xhtml", ".svg")
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
STEP_NAMES = ("抓取", "匯出", "轉文字", "建庫", "搜尋")
STEP_MARKS = "①②③④⑤"
FOUND_MAX = 40  # how many documents a title search lists 【Claude】
ROOMY = 8  # a document of more chunks lists them in a box that scrolls 【Claude】
DENSE = 40  # and one of more than this many leaves the numbers off its strip (they no longer fit) 【Claude】
PREVIEW = 46  # characters of a chunk shown on its row before it is opened 【Claude】
POINTS = ("published", "inherited", "server")  # dates that are one day; the others come as a start and an end
SPANS = (("period_start", "period_end", "期間"), ("event_start", "event_end", "事件"))
RELATION = {"publisher": "誰發的", "applies_to": "適用於", "mentions": "提到"}
DOC_TYPES = {"journal": "學報校刊", "exam": "考題", "minutes": "會議紀錄"}  # docs.doc_type, as Datasette's cells say it
ZONES = {"content": "正文", "attachments": "附件區", "nav": "選單", "unknown": "找不到"}  # doc_refs.zone, the same
UNSEEN = "沒有紀錄"  # a step that left nothing of this document


def trace_form(ref: str, *, focus: bool = True) -> str:
    return (f"<form class='ask' action='/trace' method='get'><label class='box'>{icon('trace', 16)}"
            f"<input name='ref' value='{e(ref)}' placeholder='標題裡的字、網址或文件編號' aria-label='要追的文件'"
            f"{' autofocus' if focus else ''}></label><button>追蹤</button></form>")


def examples_html(found: list[dict]) -> str:
    """A few documents to start from ([{doc_id, title, kind}]): the first test questions' gold answers."""
    if not found:
        return ""
    links = "".join(f"<a href='/trace?{urlencode({'ref': x['doc_id']})}'>{icon(KIND_ICON.get(x.get('kind'), 'page'))}"
                    f"<span>{e(x['title'])}</span></a>" for x in found)
    return f"<div class='eg'>{links}</div>"


def found_html(words: str, found: list[dict], limit: int = FOUND_MAX) -> str:
    """The documents whose titles hold words (web.find_titles), newest first; or that there is none."""
    if not found:
        return (f"<section class='card empty' style='margin-top:16px'>沒有標題含「{e(words)}」的文件，"
                "它也不是庫裡的文件編號或網址。</section>")
    items = "".join(
        f"<a href='/trace?{urlencode({'ref': x['doc_id']})}'>{icon(KIND_ICON.get(x['kind'], 'page'))}"
        f"<span>{e(x['title'] or x['doc_id'])}</span><small>"
        f"{e(' · '.join(y for y in (x['name'] or x['site'], KINDS.get(x['kind'], x['kind']), x['date'] or '無日期') if y))}"
        "</small></a>" for x in found[:limit])
    count = f"最新的 {limit} 份（共 {len(found):,} 份）" if len(found) > limit else f"{len(found)} 份"
    return (f"<h2 class='sec'>標題含「{e(words)}」的文件<span class='faint'>{count}</span></h2>"
            f"<section class='card found'>{items}</section>")


# ---------------------------------------------------------------- small pieces

def _facts(pairs: list[tuple[str, str]], wide: tuple[str, ...] = ()) -> str:
    """Names over (already escaped) values, a few to a row; a pair with an empty value is left out."""
    cells = "".join(f"<div{' class=wide' if name in wide else ''}><dt>{e(name)}</dt><dd>{value}</dd></div>"
                    for name, value in pairs if value)
    return f"<dl class='facts'>{cells}</dl>" if cells else ""


def _fold(pairs: list[tuple[str, str]], wide: tuple[str, ...] = (), name: str = "更多") -> str:
    """The less used facts of a step, folded; nothing when every value is empty."""
    body = _facts(pairs, wide)
    return f"<details class='fold'><summary>{e(name)}</summary>{body}</details>" if body else ""


def _meter(name: str, share: float, said: str, warn: bool = False) -> str:
    width = max(min(share, 1.0), 0.0) * 100
    return (f"<span>{e(name)}</span><span class='t'><i{' class=w' if warn else ''} style='width:{width:.0f}%'></i></span>"
            f"<span>{e(said)}</span>")


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def dates_line(dates: list[dict], today: date) -> str:
    """The dates the build read of a document, on one line with today: a dot for a day, a bar for a period or an
    event. '' when no date can be read as a day. Labels that would sit on top of each other share one."""
    days = [(d["kind"], day) for d in dates if (day := _day(d.get("date"))) is not None]
    if not days:
        return ""
    by_kind: dict[str, list[date]] = {}
    for kind, day in days:
        by_kind.setdefault(kind, []).append(day)
    low, high = min([day for _, day in days] + [today]), max([day for _, day in days] + [today])
    width = max((high - low).days, 1)

    def x(day: date) -> float:
        return 4 + (day - low).days / width * 92

    marks: list[str] = []
    up: list[tuple[float, str]] = []
    down: list[tuple[float, str]] = []
    for start_kind, end_kind, word in SPANS:
        starts, ends = by_kind.get(start_kind, []), by_kind.get(end_kind, [])
        for i, start in enumerate(sorted(starts)):
            end = sorted(ends)[i] if i < len(ends) else None
            if end is not None and end >= start:
                marks.append(f"<i class='span' style='left:{x(start):.2f}%;width:{x(end) - x(start):.2f}%' "
                             f"data-tip='{word} {start.isoformat()} ～ {end.isoformat()}'></i>")
                down.append(((x(start) + x(end)) / 2, f"{word} <b>{start:%m-%d} ～ {end:%m-%d}</b>"))
            else:
                marks.append(f"<i class='pt' style='left:{x(start):.2f}%' data-tip='{word}開始 {start.isoformat()}'></i>")
                down.append((x(start), f"{word}開始 <b>{start.isoformat()}</b>"))
        for end in sorted(ends)[len(starts):]:
            marks.append(f"<i class='pt' style='left:{x(end):.2f}%' data-tip='{word}結束 {end.isoformat()}'></i>")
            down.append((x(end), f"{word}結束 <b>{end.isoformat()}</b>"))
    # a 「published」 day is named by where it was read (trace.ident_words: a file's own table row is no 公告日期)
    source = {(d["kind"], _day(d.get("date"))): d.get("source") for d in dates if d["kind"] == "published"}
    for kind in POINTS:
        for day in sorted(set(by_kind.get(kind, []))):
            word = source.get((kind, day)) or DATE_KINDS.get(kind, kind)
            marks.append(f"<i class='pt' style='left:{x(day):.2f}%' data-tip='{e(word)} {day.isoformat()}'></i>")
            up.append((x(day), f"{e(word)} <b>{day.isoformat()}</b>"))
    marks.append(f"<i class='now' style='left:{x(today):.2f}%' data-tip='今天 {today.isoformat()}'></i>")
    (up if not any(abs(at - x(today)) < 22 for at, _ in up) else down).append((x(today), "今天"))

    def labels(row: list[tuple[float, str]], cls: str) -> str:
        merged: list[tuple[float, str]] = []
        for at, text in sorted(row, key=lambda label: label[0]):  # by place alone: same day, the order given
            if merged and at - merged[-1][0] < 22:
                merged[-1] = ((merged[-1][0] + at) / 2, merged[-1][1] + "　" + text)
            else:
                merged.append((at, text))
        return "".join(f"<span class='lab {cls} {'l' if at < 18 else 'r' if at > 82 else 'c'}' "
                       f"style='left:{at:.2f}%'>{text}</span>" for at, text in merged)

    return (f"<div class='tline' role='img' aria-label='這份文件讀到的日期'><i class='axis'></i>{''.join(marks)}"
            f"{labels(up, 'up')}{labels(down, 'dn')}</div>")


def _steps(report: dict) -> str:
    """The five steps' state in one line, each a link to its card: the first thing a trace shows after the document
    itself."""
    c, x, t, lib, s = report["crawl"], report["export"], report["totext"], report["library"], report["search"]
    states: list[tuple[str, str]] = []
    if c is None:
        states.append(("", UNSEEN))
    elif c["result"] == "抓到了":
        states.append(("ok", f"抓到了 · {size_words(c['size'])}"))
    else:
        states.append(("warn", tw(c["result"]) or "沒抓到"))
    states.append(("", UNSEEN) if x is None else
                  ("ok", " · ".join(tw(v) for v in (x["row"].get("类型"), x["row"].get("页别")) if v) or "有"))
    if t is None:
        states.append(("", UNSEEN))
    else:
        states.append(("ok", f"{t['text_chars']:,} 字") if t["text_chars"] else ("warn", "沒讀到字"))
    if lib:
        d = lib["doc"]
        states.append(("ok", " · ".join(v for v in ("背景" if d.get("tier") == "background" else "主料",
                                                    KINDS.get(d["kind_class"], d["kind_class"]),
                                                    d["ident_date"] or "無日期") if v)))
    else:
        states.append(("bad", "沒進庫" if report["left_out"] else "庫裡找不到"))
    if s is None:
        states.append(("", "沒有搜尋"))
    else:
        states.append(("ok", f"標題搜尋第 {s['rank']} 名") if s["rank"] else ("bad", f"前 {s['within']} 名裡沒有"))
    mark = {"ok": "ok", "warn": "warn", "bad": "no", "": "dash"}
    why = "\n".join(report["missing"])  # why a step left nothing: trace's notes, floating over that step
    out = []
    for i, (kind, words) in enumerate(states):
        inner = (f"<span class='dot'>{icon(mark[kind], 16)}</span><b>{STEP_MARKS[i]} {STEP_NAMES[i]}</b>"
                 f"<small>{e(words)}</small>")
        tip = f" data-tip='{e(why)}'" if why and not kind else ""
        out.append(f"<li><a class='{kind}' href='#s{i + 1}'>{inner}</a></li>" if kind else
                   f"<li><span class='a'{tip}>{inner}</span></li>")  # nothing was left of this step: no card to go to
    return "<ol class='steps'>" + "".join(out) + "</ol>"


def _who(report: dict) -> str:
    """The document itself: its title, what it is, whose, of when, where it lives, and how a search for its own
    title finds it."""
    r, x, lib, s = report["resolved"], report["export"], report["library"], report["search"]
    if lib:
        d = lib["doc"]
        title = d["title"] or d["doc_id"]
        tier = "背景" if d.get("tier") == "background" else "主料"
        day = (f"{e(d['ident_date'])}<span class='faint'>（{e(ident_words(d, lib['dates']))}）</span>"
               if d["ident_date"] else "<span class='faint'>沒有日期</span>")
        bits = [kind_tag(d["kind_class"], KINDS), e(d["unit"]) + (f"　{e(d['grp'])}" if d["grp"] else ""), day,
                f"<span class='tag'>{tier}</span>"]
        if "expires" in d:
            bits.append("<span class='tag'>會過期" + (f"（{e(d['valid_ay'])} 學年）" if d.get("valid_ay") else "") + "</span>"
                        if d["expires"] else "<span class='tag'>不會過期</span>")
        url, doc_id = d["url"], d["doc_id"]
    elif x:
        row = x["row"]
        title = row["标题"] or row["网址"] or report["ref"]
        bits = [e(tw(row["类型"])), f"{e(tw(x['name']))}", "<span class='pill bad'>沒進庫</span>"]
        url, doc_id = row["网址"], ""
    else:
        title = report["ref"] or "（空白）"
        bits = ["<span class='pill bad'>庫裡找不到這一份</span>"]
        url, doc_id = "", ""
    about = "".join(f"<span>{b}</span>" for b in bits if b)
    addr = ""
    if str(url or "").startswith(("http://", "https://")):
        addr += (f"<a class='url' href='{e(url)}' target='_blank' rel='noopener' data-tip='打開學校網站上的這一頁'>"
                 f"<span>{e(unquote(url))}</span>{icon('out', 11)}</a>")
        # 網頁核查 (design 2026-11-16-page-check-design.md section 一): the archived page and its links' states; by
        # the document's id, or its whole address, so a press's document (#按钮=…) is checked as that press's answer
        addr += (f"<a class='btn' href='/check?{urlencode({'url': doc_id or url})}' "
                 f"data-tip='看存檔裡的這一頁：哪些連結、圖片、按鈕抓到了'>{icon('search', 12)}核查這一頁</a>")
    if doc_id:
        addr += (f"<button class='btn mono' type='button' data-copy='{e(doc_id)}' data-tip='複製文件編號'>"
                 f"{icon('copy', 12)}{e(doc_id)}</button>")
    place = ""
    if s is not None:
        why = why_url(q=s["query"], docs=[r["doc_id"] or ""], anchor=f"r-{r['doc_id']}" if r["doc_id"] else "")
        pill = (f"<span class='pill good'>{icon('ok', 12)}標題搜尋第 {s['rank']} 名</span>" if s["rank"]
                else f"<span class='pill bad'>{icon('no', 12)}標題搜尋：前 {s['within']} 名裡沒有</span>")
        place = (f"<a href='{e(why)}' style='text-decoration:none' "
                 f"data-tip='拿它的標題當問題搜一次，它排第幾；點了看那次搜尋怎麼排'>{pill}</a>")
    return (f"<section class='card pad who'><div><h1>{e(title)}</h1><div class='about'>{about}</div>"
            f"<div class='addr'>{addr}</div></div><div>{place}</div></section>")


def _original(x: dict) -> str:
    """The exported file beside its text: a page in a frame that loads nothing, a PDF in the browser's own viewer, a
    picture as a picture, anything else as a link (the spec's 「原档和文字并排」)."""
    src = "/file?" + urlencode({"folder": x["folder"], "path": x["file"]})
    beside = f"<a href='{e(src)}' target='_blank'>新分頁打開</a>"
    name = x["file"].lower()
    if name.endswith(MARKUP):
        return (f"<header><span data-tip='這裡打開時不連網，所以沒有圖'>原檔</span><span class='side'>{beside}</span></header>"
                f"<iframe class='orig' sandbox src='{e(src)}'></iframe>")
    if name.endswith(".pdf"):  # the browser's own viewer, which a sandboxed frame refuses
        return f"<header>原檔<span class='side'>{beside}</span></header><iframe class='orig' src='{e(src)}'></iframe>"
    if name.endswith(IMAGES):
        return f"<header>原檔<span class='side'>{beside}</span></header><div><img class='orig' src='{e(src)}' alt=''></div>"
    return (f"<header>原檔</header><div class='none'><a class='btn' href='{e(src)}' target='_blank' "
            "data-tip='瀏覽器開不了這種檔，沒辦法和文字並排'>下載原檔</a></div>")


def _crawl_card(c: dict | None, viewer_url: str) -> str:
    if not c:
        return f"<div class='in faint'>{UNSEEN}</div>"
    number = c["archive_id"][1:] if c["archive_id"][:1] == "u" and c["archive_id"][1:].isdigit() else ""
    link = (f"<a class='btn' href='{e(viewer_url.rstrip('/'))}/page?id={number}' target='_blank' "
            f"data-tip='在爬蟲存檔看抓下來的原樣'>{icon('out', 12)}爬蟲存檔</a>" if viewer_url and number else "")
    state = "good" if c["result"] == "抓到了" else "warn"
    result = (f"<span class='pill {state}'>{e(tw(c['result']) or '沒抓到')}</span>"
              + (f" <small>（{e(tw(c['reason']))}）</small>" if c["reason"] else ""))
    facts = _facts([("結果", result), ("抓取時間", e(when(c["fetched"]))), ("大小", e(size_words(c["size"]))),
                    ("怎麼找到的", e(tw(c["how"])))], wide=("怎麼找到的",))
    fold = _fold([("缺什麼", e(tw(c["lacks"]))), ("連到它的網頁", e(c["linked_from"])), ("存檔編號", e(c["archive_id"]))],
                 wide=("連到它的網頁",))
    return f"<div class='in'>{facts}{f'<div class=act>{link}</div>' if link else ''}{fold}</div>"


def _export_card(x: dict | None) -> str:
    if not x:
        return f"<div class='in faint'>{UNSEEN}</div>"
    row = x["row"]
    written = e(row["网页上写的日期"]) + (f" <small>「{e(row['日期原文'])}」</small>" if row["日期原文"] else "")
    # the site's name opens its page (every site name links there: structure design 2026-11-17 section 二)
    site = (f"<a href='/site?{urlencode({'site': x['site']})}'>{e(tw(x['name']))}</a> <small>{e(x['site'])}</small>"
            if x.get("site") else e(tw(x["name"])))
    facts = _facts([("網站", site), ("類型", e(tw(row["类型"]))),
                    ("網頁上寫的日期", written), ("日期來源", e(tw(row["日期来源"]))), ("頁別", e(tw(row["页别"]))),
                    ("最後修改時間", e(row["最后修改时间"]))])
    fold = _fold([("標題", e(row["标题"])), ("網址", e(row["网址"])), ("檔案", e(x["file"])),
                  ("列表上的類別", e(row["列表上的类别"])), ("公告單位", e(row.get("公告单位"))),
                  ("連結文字", e(row["链接文字"])), ("備註", e(tw(row["备注"]))), ("資料夾", e(x["folder"]))],
                 wide=("標題", "網址", "檔案", "資料夾", "備註"))
    return f"<div class='in'>{facts}{fold}</div>"


def _totext_card(report: dict) -> str:
    t, x = report["totext"], report["export"]
    if not t:
        return f"<div class='in faint'>{UNSEEN}</div>"
    notes = [e(how_converted(t))]
    # only what is out of the ordinary: a page read again whole (the usual reading needs no sentence)
    if report["record"] and report["record"]["under_read"]:
        notes.append(e(under_read_words(report["record"]["under_read"])))
    meters = []
    if t["full_chars"]:
        meters.append(_meter("正文區佔比", t["text_chars"] / t["full_chars"],
                             f"{t['text_chars']:,} / {t['full_chars']:,} 字"))
    if t["pages"] and t["route"] != "page":
        meters.append(_meter("掃描認字", t["ocr_pages"] / t["pages"], f"{t['ocr_pages']} / {t['pages']} 頁",
                             warn=bool(t["ocr_pages"])))
    if isinstance(t["ocr_mean_score"], int | float):
        meters.append(_meter("認字信心", float(t["ocr_mean_score"]), f"{t['ocr_mean_score']:.2f}"))
    meter = f"<div class='meter' style='margin-top:10px'>{''.join(meters)}</div>" if meters else ""
    left = _original(x) if x and x["file"] else "<header>原檔</header><div class='none'>沒有檔</div>"
    full = (f"<details class='fold' style='padding:0 12px 10px'><summary>整頁文字（{t['full_chars']:,} 字）</summary>"
            f"<pre class='raw'>{e(t['full'])}</pre></details>" if t["full"] is not None else "")
    right = (f"<header>轉出的文字<span class='side'>{t['text_chars']:,} 字</span></header><pre>{e(t['text'])}</pre>{full}")
    return (f"<div class='in'><div class='soft'>{'　·　'.join(notes)}</div>{meter}<div class='two'>"
            f"<div class='pane'>{left}</div><div class='pane'>{right}</div></div></div>")


def _links(items: list[tuple[str, str, str, str]]) -> str:
    """A list of documents to go to: (document id, kind, title, the word at the end)."""
    return "<div class='rel'>" + "".join(
        f"<a href='/trace?{urlencode({'ref': doc_id})}'>{icon(KIND_ICON.get(kind, 'page'))}<span>{e(title or doc_id)}"
        f"</span>{f'<small>{e(side)}</small>' if side else ''}</a>" for doc_id, kind, title, side in items) + "</div>"


def _library_card(report: dict, today: date) -> str:
    lib = report["library"]
    if not lib:
        if report["left_out"]:
            key = (f"<div class='faint small' style='margin-top:6px'>分類：{e(tw(report['left_out']['key']))}</div>"
                   if report["left_out"]["key"] else "")
            return (f"<div class='in'><span class='pill bad'>沒進庫</span>　{e(report['left_out']['words'])}{key}</div>")
        return "<div class='in'><span class='pill bad'>庫裡找不到這一份</span></div>"
    d = lib["doc"]
    kind = e(KINDS.get(d["kind_class"], d["kind_class"])) + f" <small>{e(tw(d['kind_raw']))}</small>"
    tier = (("背景" if d.get("tier") == "background" else "主料")
            + (f" <small>{e(DOC_TYPES.get(d['doc_type'], d['doc_type']))}</small>" if d["doc_type"] else ""))
    day = (f"{e(d['ident_date'])} <small>{e(ident_words(d, lib['dates']))}</small>"
           if d["ident_date"] else "沒有日期")
    expires = ""
    if "expires" in d:
        expires = ("會" + (f" <small>算 {e(d['valid_ay'])} 學年的</small>" if d.get("valid_ay") else "")) if d["expires"] else "不會"
    period = e(f"{d['period_ay']}-{d['period_sem']}" if d["period_sem"] else d["period_ay"] or "")
    facts = _facts([
        ("類型", kind), ("主料／背景", tier), ("日期", day),
        ("單位", e(d["unit"]) + (f" <small>{e(d['grp'])}</small>" if d["grp"] else "")),
        ("讀到字", f"有 <small>{d['chars']:,} 字</small>" if d["has_text"] else "沒有 <small>只能靠標題找</small>"),
        ("會過期", expires), ("學期", period), ("適用的入學屆", e(cohort_words(lib["applies_to"])))])
    scope = "　".join(f"{RELATION.get(s['relation'], s['relation'])} {e(s['unit_id'])}"
                     + "".join(f"·{e(s[k])}" for k in ("campus", "level") if s.get(k)) for s in lib["scope"])
    fold = _fold([("和單位的關係", scope), ("單位行", e(d.get("unit_line"))), ("排序日期", e(d.get("rank_date"))),
                  ("文字來源", e(d["text_source"])), ("文件編號", f"<span class='mono'>{e(d['doc_id'])}</span>"),
                  ("標題", e(d["title"])), ("網址", e(d["url"]))], wide=("和單位的關係", "單位行", "標題", "網址"))
    out = [f"<div class='in'>{facts}{fold}"]
    if lib["dates"]:
        table = ("<table class='grid'><thead><tr><th>日期類別</th><th>日期</th><th>原文</th><th>出處</th></tr></thead>"
                 "<tbody>" + "".join(
                     f"<tr><td>{e(DATE_KINDS.get(v['kind'], v['kind']))}</td><td>{e(v['date'])}</td><td>{e(v['raw'])}</td>"
                     f"<td>{e(v['source'])}</td></tr>" for v in lib["dates"]) + "</tbody></table>")
        out.append(f"<h3 class='cap' style='margin-top:18px'>讀到的日期</h3>{dates_line(lib['dates'], today)}"
                   f"<details class='fold'><summary>日期明細（{len(lib['dates'])}）</summary>{table}</details>")
    around = []
    if lib["parent"]:
        around.append("<h3 class='cap' style='margin-top:18px'>所屬文件</h3>"
                      + _links([(lib["parent"]["doc_id"], "notice", lib["parent"]["title"], "")]))
    if lib["children_total"]:
        more = f"（列出前 {len(lib['children'])} 份）" if lib["children_total"] > len(lib["children"]) else ""
        around.append(f"<h3 class='cap' style='margin-top:18px'>附件（{lib['children_total']}）{more}</h3>"
                      + _links([(v["doc_id"], v.get("kind_class") or "attachment", v["title"], "") for v in lib["children"]]))
    if lib["copies"]:
        rows = "".join(f"<tr><td class='mono'>{e(v['alias'])}</td><td>{e(WHY.get(v['why'], v['why']))}</td>"
                       f"<td>{e(v['url'])}</td></tr>" for v in lib["copies"])
        around.append(f"<details class='fold'><summary>合併進來的副本（{len(lib['copies'])}）</summary>"
                      f"<table class='grid'><tbody>{rows}</tbody></table></details>")
    if lib["refs"]:
        rows = "".join(f"<tr><td><a href='/trace?{urlencode({'ref': v['ref_id']})}'>{e(v['title'] or v['ref_id'])}</a></td>"
                       f"<td>{e(ZONES.get(v['zone'], v['zone']))}</td><td>{e(v['context'])}</td></tr>" for v in lib["refs"])
        around.append(f"<details class='fold'><summary>連到它的網頁（{len(lib['refs'])}）</summary>"
                      f"<table class='grid'><tbody>{rows}</tbody></table></details>")
    out.append("".join(around))
    shown = f"，列出前 {len(lib['chunks'])} 段" if lib["chunks_total"] > len(lib["chunks"]) else ""
    out.append(f"<h3 class='cap' style='margin-top:18px' data-tip='搜尋是一小段一小段比對的'>小段"
               f"<span class='faint' style='font-weight:400'>　{len(lib['sections'])} 節 · {lib['chunks_total']} 段{shown}"
               "</span></h3>")
    chunks = lib["chunks"]
    if chunks:
        dense = " dense" if len(chunks) > DENSE else ""
        out.append(f"<div class='strip{dense}'>" + "".join(
            f"<a href='#c{c['chunk_id']}' data-chunk='c{c['chunk_id']}' style='flex:{max(c['end'] - c['start'], 1)} 0 0' "
            f"data-tip='第 {i} 段 · {c['end'] - c['start']} 字{'　' + e(c['heading']) if c['heading'] else ''}'>{i}</a>"
            for i, c in enumerate(chunks, start=1)) + "</div>")
    rows = []
    for i, c in enumerate(chunks, start=1):
        first = " ".join(c["text"].split())
        peek = f"<span class='faint peek'>{e(first[:PREVIEW])}{'…' if len(first) > PREVIEW else ''}</span>"
        rows.append(f"<details class='chunk' id='c{c['chunk_id']}'><summary><b>第 {i} 段</b>{peek}"
                    f"<span class='side'>{c['end'] - c['start']} 字 · 編號 {c['chunk_id']}</span></summary>"
                    + (f"<div class='faint small' style='padding:8px 12px 0'>章節：{e(c['heading'])}</div>"
                       if c["heading"] else "")
                    + f"<pre>{e(c['text'])}</pre><details class='fold'><summary data-tip='重排模型讀的是這一段，"
                    "前面加上標題和單位行'>重排模型讀的字</summary>"
                    f"<pre class='raw'>{e(c['passage'])}</pre></details></details>")
    if rows:
        out.append(f"<div class='chunks{' many' if len(rows) > ROOMY else ''}'>{''.join(rows)}</div>")
    out.append("</div>")
    return "".join(out)


def trace_body(report: dict, viewer_url: str, today: date) -> str:
    """One document followed: itself, the five steps' states, what cannot be seen and why, then a card per step."""
    r, s = report["resolved"], report["search"]
    out = []
    if r["via"] == "alias":
        out.append(f"<div class='note' style='margin-top:16px'>{icon('warn', 16)}<div>這個編號併進了 <b>{e(r['doc_id'])}</b>"
                   f"（{e(WHY.get(r['why'], r['why']))}）：{e(merged_words(report))}</div></div>")
    out.append(_who(report))
    out.append(_steps(report))
    unseen = any(report[k] is None for k in ("crawl", "export", "totext")) or report["search"] is None
    if report["missing"] and not unseen:  # no step without a record to float them over
        out.append("<ul class='gaps'>" + "".join(f"<li>{e(m)}</li>" for m in report["missing"]) + "</ul>")
    cards = (_crawl_card(report["crawl"], viewer_url) if report["crawl"] else "",
             _export_card(report["export"]) if report["export"] else "",
             _totext_card(report) if report["totext"] else "", _library_card(report, today))
    for i, card in enumerate(cards):
        if card:  # a step that left nothing has no card: the line of steps says so, the notes above say why
            out.append(f"<section class='card step' id='s{i + 1}'><h2>{STEP_MARKS[i]} {STEP_NAMES[i]}</h2>{card}</section>")
    if s is not None:
        place = (f"<span class='pill good'>第 {s['rank']} 名</span>" if s["rank"]
                 else f"<span class='pill bad'>前 {s['within']} 名裡沒有</span>")
        href = why_url(q=s["query"], docs=[r["doc_id"] or ""], anchor=f"r-{r['doc_id']}" if r["doc_id"] else "")
        why = (f"<a class='btn' href='{e(href)}' "
               f"data-tip='搜尋的「為什麼這樣排」：那次搜尋怎麼排'>{icon('search', 12)}看那次搜尋</a>")
        last = (f"<div class='in' style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
                f"<span data-tip='拿它的標題當問題搜一次'>標題搜尋</span>{place}{why}</div>")
        out.append(f"<section class='card step' id='s5'><h2>{STEP_MARKS[4]} {STEP_NAMES[4]}</h2>{last}</section>")
    return "".join(out)
