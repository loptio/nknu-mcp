"""搜尋測試's pages (nknu.ops.web serves them; the page was 搜尋試驗台 until 11-15): the scoreboard of the test questions, and one search explained. They
draw what nknu.ops.explain.explain_search and nknu.ops.board return and score nothing themselves (tests/ops/
test_search_page.py pins that a bar's parts add up to the search's own score).

The layout is 【Claude】's (owner 11-15: 「信息太雜…沒有用一些視覺化方法點綴」; the reasons are in nknu.ops.look):
a search reads top to bottom as the search itself runs: how the question was read; how many candidates each path
brought and how many the reranker read (a funnel); then one row per document with its place on each path and its
score as a bar of its parts. A followed document (a test question's gold answer) is one of those rows, wherever it
stopped, and a ruler above says at a glance where they all stand. Every heading is a plain name and what it means
floats over it (owner 11-15: 「很多名字看着不直觉 然后感觉是想缩短文字 但又想保留细节导致不上不下的」)."""

from __future__ import annotations

import math
from urllib.parse import urlencode

from nknu.ops import board as grades
from nknu.ops.explain import IN_TOP, NO_CANDIDATE, NOT_FOUND, NOT_IN_HEAD, RANKED, background_words, order_words
from nknu.ops.look import e, icon, kind_tag, tw
from nknu.ops.trace import KINDS

SHOWN_ADD = 0.05  # a part that moved a score by less is drawn in the bar's tip but not named under it 【Claude】
DRAWN_ADD = 0.02  # and one smaller than this share of the longest bar is not drawn as a segment of its own 【Claude】
GRADE_WORDS = {grades.FIRST: "第 1 名", grades.TOP3: "第 2–3 名", grades.TOP: "第 4–10 名", grades.OUT: "10 名以外"}
STOPPED = {NOT_IN_HEAD: ("warn", "沒進重排"), NO_CANDIDATE: ("bad", "沒進候選"), NOT_FOUND: ("none", "庫裡沒有")}
# what each part of a score is, floating over its name wherever it is named (the sum, the notes under a bar, the 「?」)
PART_TIPS = {
    "對題": "重排模型判斷這份文件和問題有多對得上（0–1），再乘權重",
    "關鍵字": "沒經過重排時的分數：問題的詞對上多少，加上關鍵字分",
    "學期": "問題點了學期：文件標題是同一學期就加分，只同學年加得少一點",
    "屆別": "問題點了入學屆，文件也寫明適用那一屆時加分",
    "新舊": "越新加得越多；問題點了學期就不加",
    "第一關名次": "關鍵字和向量合併後的名次，換成一點分數加上去",
    "還有效": "會過期的文件（公告、標了學期的文件），過了它那個學年越久折得越多",
    "對口": "系所自己的文件，在問題沒點名系所時打一點折",
    "單位": "問題點名的單位對上文件的單位時加的分",
    "總分": "（對題＋加分）× 折扣 ＋ 單位",
}


# ---------------------------------------------------------------- where it lives, and the forms

# Since the structure design of 2026-11-17 (section 二; owner: 「可以按你的建议走吧，不用问我了」) 搜尋測試 is the second tab
# of 試用 (/try?tab=why…): /search redirects there, and every link of these pages goes there directly. 【Claude】
WHY_TAB = ("tab", "why")


def why_url(*, q: str = "", qid: str = "", docs: list[str] | tuple = (), limit: int | None = None,
            anchor: str = "") -> str:
    """The address of 試用's 「為什麼這樣排」 for a question (or a test question by its id), with documents to follow,
    a limit and an anchor (a row's id: r-<doc_id>)."""
    params = [WHY_TAB]
    if qid:
        params.append(("qid", qid))
    elif q:
        params.append(("q", q))
    params += [("doc", d) for d in docs if d]
    if limit is not None:
        params.append(("limit", str(limit)))
    return "/try?" + urlencode(params) + (f"#{anchor}" if anchor else "")


def follow_form(q: str, docs: list[str], question: dict | None = None) -> str:
    """The box for documents to follow through the search (a test question follows its gold answers by itself):
    the question rides along hidden, so the same search is explained again with them marked."""
    asked = (f"<input type='hidden' name='qid' value='{e(question['id'])}'>" if question
             else f"<input type='hidden' name='q' value='{e(q)}'>")
    return ("<form class='ask follow' action='/try' method='get'><input type='hidden' name='tab' value='why'>"
            f"{asked}<label class='box narrow' data-tip='填文件編號或網址：結果裡會標出它排第幾，沒排上的話停在哪一步'>"
            f"{icon('trace', 15)}<input name='doc' value='{e(' '.join(docs))}' "
            "placeholder='指定文件（可不填）' aria-label='指定文件'></label><button>看名次</button></form>")


def question_nav(questions: list[dict], current: dict | None) -> str:
    """On a page of results: the test question before and after (also the ← → keys), and any other one choice away."""
    if not questions:
        return ""
    at = next((i for i, q in enumerate(questions) if current and q["id"] == current["id"]), None)

    def step(key: str, to: int | None, name: str) -> str:
        if to is None or not 0 <= to < len(questions):
            return f"<a class='step' aria-disabled='true'>{icon(key, 15)}</a>"
        target = questions[to]
        return (f"<a class='step' data-key='{'prev' if key == 'left' else 'next'}' "
                f"href='{e(why_url(qid=target['id'], anchor='result'))}' "
                f"data-tip='{e(name)}：{e(target['id'])}　{e(tw(target['question']))}' "
                f"aria-label='{e(name)}'>{icon(key, 15)}</a>")

    first = "" if current else "<option value='' selected>選測試題…</option>"
    opts = "".join(f"<option value='{e(q['id'])}'{' selected' if current and q['id'] == current['id'] else ''}>"
                   f"{e(q['id'])}　{e(tw(q['question']))}</option>" for q in questions)
    arrows = (step("left", None if at is None else at - 1, "上一題")
              + step("right", 0 if at is None else at + 1, "下一題"))
    return (f"<form class='qnav' action='/try' method='get'><input type='hidden' name='tab' value='why'>{arrows}"
            f"<select name='qid' aria-label='測試題' onchange='this.form.submit()'>{first}{opts}</select>"
            "<a class='back' href='/try#board'>全部測試題</a></form>")


def why_head(questions: list[dict], question: dict | None, query: str, docs: list[str]) -> str:
    """Over an explained search on 試用's second tab: the test questions' arrows and picker, and the box for documents
    to follow (a test question's gold answers are followed by themselves, so only the others are in the box)."""
    typed = [d for d in docs if not question or d not in question["gold"]]
    return f"<div class='whyhead'>{question_nav(questions, question)}{follow_form(query, typed, question)}</div>"


# ---------------------------------------------------------------- the scoreboard

def board_html(snapshot: dict | None) -> str:
    """The test questions as one bar of how many stand where and one tile each (its place, its code, its words). The
    page's script fills in what is still being searched (data-board names the JSON). '' without questions, or
    without a snapshot."""
    if not snapshot or not snapshot["total"]:
        return ""
    counts, total = snapshot["counts"], snapshot["total"]
    waiting = total - sum(counts.values())
    segs = "".join(f"<span class='seg g-{g}' data-n='{counts[g]}' style='flex-grow:{counts[g]}' "
                   f"data-tip='{e(GRADE_WORDS.get(g, '沒搜成'))}：{counts[g]} 題'>{counts[g]}</span>"
                   for g in grades.GRADES)
    # While questions wait (after a library swap, each is searched again) the bar's rest is a calm striped part and the
    # legend's row ends in how far the run has come, 正在重算 7 / 33; a waiting tile is an empty pending box. Before
    # it said 還在算 26 題 on grey 「…」 squares, which read as broken. 【Claude】 redesign 2026-11-17
    # (research/dashboard_redesign_2026-11-17/diagnosis.md, 搜尋測試 ①); the page's script (look.JS) keeps both current.
    segs += (f"<span class='seg g-wait' data-n='{waiting}' style='flex-grow:{waiting}' "
             f"data-tip='還在算：{waiting} 題'></span>")
    legend = "".join(f"<span><i class='g-{g}'></i>{e(GRADE_WORDS[g])}</span>" for g in GRADE_WORDS)
    legend += (f"<span class='prog' data-prog data-tip='換庫或換日以後，{total} 道測試題各重搜一次；算好的題先顯示'"
               f"{'' if waiting else ' hidden'}>正在重算 {total - waiting} / {total}</span>")
    tiles = []
    for item in snapshot["items"]:
        mark = ("" if item["grade"] == grades.WAIT else "!" if item["grade"] == grades.FAILED
                else str(item["rank"]) if item["rank"] else "—")
        said = (f"標準答案第 {item['rank']} 名" if item["rank"] else "還在算" if item["grade"] == grades.WAIT
                else "這一題沒搜成" if item["grade"] == grades.FAILED else "標準答案沒排上")
        tiles.append(f"<a class='tile' href='{e(why_url(qid=item['id'], anchor='result'))}' data-q='{e(item['id'])}' "
                     f"data-grade='{e(item['grade'])}'><span class='g g-{e(item['grade'])}' data-tip='{e(said)}'>"
                     f"{e(mark)}</span>"
                     f"<span class='q'><b>{e(item['id'])}</b>{e(tw(item['question']))}</span></a>")
    running = " data-running" if snapshot["done"] < total else ""
    return (f"<h2 class='sec' id='board' data-tip='{total} 道測試題各搜一次，看標準答案排第幾名；點一題看那次搜尋'>測試題成績</h2>"
            f"<section class='card pad' data-board='/api/board'{running}><div class='tally' role='img' "
            f"aria-label='標準答案的名次分布'>{segs}</div><div class='legend'>{legend}</div>"
            f"<div class='tiles'>{''.join(tiles)}</div></section>")


# ---------------------------------------------------------------- one score as its parts

def parts(row: dict, weights: dict) -> dict:
    """One result's score as the search added it up (nknu.ops.explain's docstring): {'base', 'base_name', 'adds':
    [(name, value)], 'factors': [(name, times)], 'bonus', 'pre': base and adds together, 'total'}; so that
    (base + adds) x factors + bonus is the row's score. A reranked document's base is the reranker's; any other
    document's is its keyword relevance, and one only the vectors found has none."""
    r, k = row.get("rerank"), row.get("keyword")
    if r is not None:
        base, name = r["weighted"], "對題"
        adds = [("學期", r["period"]), ("屆別", r["cohort"]), ("新舊", r["date"] or 0.0), ("第一關名次", r["first"] or 0.0)]
        factors = [("還有效", 1.0 if r["valid"] is None else r["valid"]), ("對口", r["scope"])]
        bonus = r["bonus"]
    elif k is not None:
        base, name = weights["coverage"] * k["coverage"] + weights["bm25"] * k["bm25_norm"], "關鍵字"
        adds = [("學期", weights["period"] * k["period"]), ("屆別", weights["cohort"] if k["cohort"] else 0.0),
                ("新舊", k["date"] or 0.0)]
        factors = [("對口", k["scope"])]
        bonus = row["score"] - (base + sum(v for _, v in adds)) * k["scope"]
    else:
        base, name, adds, factors, bonus = 0.0, "", [], [], row["score"]
    pre = base + sum(v for _, v in adds)
    return {"base": base, "base_name": name, "adds": [(n, v) for n, v in adds if v], "bonus": bonus, "pre": pre,
            "factors": [(n, f) for n, f in factors if abs(f - 1.0) > 1e-9], "total": row["score"]}


def _bar(p: dict, scale: float) -> str:
    """The parts as one bar on the page's common scale: what the score kept of its base (ink) and of each part that
    raised it (green), then what the discounts took (pale red), then air up to the longest bar. A part too small to
    see as a segment of its own stays in the base's."""
    if scale <= 0:
        return ""
    times = math.prod(f for _, f in p["factors"])
    kept = max(min(times, 1.0), 0.0)
    small = DRAWN_ADD * scale
    inside = [v for _, v in p["adds"] if v > 0 and v * kept >= small]
    segs = [("rel", max(p["pre"] - sum(inside), 0.0) * kept)] + [("add", v * kept) for v in inside]
    if times > 1.0 and p["pre"] * (times - 1.0) >= small:  # a factor that raises: none does today
        segs.append(("add", p["pre"] * (times - 1.0)))
    if p["bonus"] >= small:  # the unit bonus comes after the discounts
        segs.append(("add", p["bonus"]))
    if (cut := max(p["pre"], 0.0) * (1.0 - kept)) > 0:
        segs.append(("cut", cut))
    out = "".join(f"<i class='{name}' style='flex:{v * 1000:.0f} 0 0'></i>" for name, v in segs if v > 0)
    rest = scale - sum(v for _, v in segs if v > 0)
    if rest > 0:
        out += f"<i class='rest' style='flex:{rest * 1000:.0f} 0 0'></i>"
    return f"<span class='bar'>{out}</span>"


def _notes(p: dict) -> str:
    """Under a bar: only what moved the score enough to matter, each with its amount."""
    out = [f"<span class='{'up' if v > 0 else 'down'}'>{e(n)} {v:+.2f}</span>" for n, v in p["adds"]
           if abs(v) >= SHOWN_ADD]
    out += [f"<span class='{'down' if f < 1 else 'up'}'>{e(n)} ×{f:.2f}</span>" for n, f in p["factors"]]
    if abs(p["bonus"]) >= SHOWN_ADD:
        out.append(f"<span class='{'up' if p['bonus'] > 0 else 'down'}'>單位 {p['bonus']:+.2f}</span>")
    # (the bar's own floating word, the whole sum, covers these too: a name's meaning is in the opened row's sum)
    return f"<span class='bnotes'>{''.join(out)}</span>" if out else ""


def _tip(row: dict, p: dict, weights: dict) -> str:
    """The whole sum in words, for the word that floats over a bar."""
    lines = []
    if (r := row.get("rerank")) is not None:
        lines.append(f"對題 {r['rel']:.3f} × {weights['rerank']:g} = {p['base']:.2f}")
    elif row.get("keyword") is not None:
        lines.append(f"關鍵字 {p['base']:.2f}（沒經過重排）")
    lines += [f"{n} {v:+.2f}" for n, v in p["adds"]]
    lines += [f"{n} ×{f:.2f}" for n, f in p["factors"]]
    if p["bonus"]:
        lines.append(f"單位 {p['bonus']:+.2f}")
    lines.append(f"總分 {p['total']:.2f}")
    return "\n".join(lines)


# ---------------------------------------------------------------- one row

def _place(value: int | None, path: str) -> str:
    """A document's place on one path, set darker the nearer the top 【Claude】: the first 10, the first 50 (what the
    reranker reads of the merged order), the rest; a dot when the path did not find it."""
    if not value:
        return f"<span class='pl' data-tip='{e(path)}沒有找到它'>·</span>"
    tone = "a" if value <= 10 else "b" if value <= 50 else ""
    return f"<span class='pl {tone}' data-tip='{e(path)}第 {value} 名'>{value}</span>"


def _about(about: dict) -> str:
    bits = [kind_tag(about.get("kind"), KINDS), e(about.get("unit") or ""), e(about.get("date") or "無日期")]
    return "".join(f"<span>{b}</span>" for b in bits if b)


def _calc(row: dict, p: dict, weights: dict) -> str:
    """The sum as a small table: every part, also those that changed nothing (greyed), so the rule is all there."""
    r, k = row.get("rerank"), row.get("keyword")
    lines: list[tuple[str, str, str, bool]] = []
    if r is not None:
        lines.append(("對題", f"{r['rel']:.3f} × {weights['rerank']:g}", f"{p['base']:.2f}", True))
        every = (("學期", r["period"]), ("屆別", r["cohort"]), ("新舊", r["date"]))
        factors = (("還有效", r["valid"]), ("對口", r["scope"]))
    elif k is not None:
        lines.append(("關鍵字", f"涵蓋 {k['coverage']:.2f} × {weights['coverage']:g} ＋ 關鍵字分 {k['bm25_norm']:.2f}",
                      f"{p['base']:.2f}", True))
        every = (("學期", weights["period"] * k["period"]), ("屆別", weights["cohort"] if k["cohort"] else 0.0),
                 ("新舊", k["date"]))
        factors = (("對口", k["scope"]),)
    else:
        every, factors = (), ()
    for name, value in every:
        lines.append((name, "", "—" if not value else f"{value:+.2f}", bool(value)))
    if r is not None and r["first"] is not None:
        lines.append(("第一關名次", "", f"{r['first']:+.2f}", bool(r["first"])))
    for name, value in factors:
        moved = value is not None and abs(value - 1.0) > 1e-9
        lines.append((name, "", "—" if value is None else f"×{value:.2f}", moved))
    if p["bonus"]:
        lines.append(("單位", "", f"{p['bonus']:+.2f}", True))
    body = "".join(f"<tr{'' if on else ' class=off'}><td data-tip='{e(PART_TIPS.get(n, ''))}'>{e(n)}</td>"
                   f"<td>{e(how)}</td><td>{e(v)}</td></tr>" for n, how, v, on in lines)
    return (f"<table class='calc'>{body}<tr class='sum'><td data-tip='{PART_TIPS['總分']}'>總分</td><td></td>"
            f"<td>{p['total']:.2f}</td></tr></table>")


def _detail(row: dict, p: dict, weights: dict, words: str = "") -> str:
    """What a row holds when it is opened: the sum, how each path saw it, the passages the reranker read, and where
    to go from here."""
    left = f"<div><h3>分數明細</h3>{_calc(row, p, weights)}</div>"
    k, d, r = row.get("keyword"), row.get("dense"), row.get("rerank")
    paths = []
    if k is not None:
        paths.append(f"<span data-tip='問題的詞，這份文件對上了多少'>關鍵字</span>"
                     f"<span class='t'><i style='width:{min(k['coverage'], 1) * 100:.0f}%'></i></span>"
                     f"<span>詞對上 {k['coverage'] * 100:.0f}%</span>")
    if d:
        paths.append(f"<span data-tip='這份文件裡和問題意思最像的一小段有多像（0–1）'>向量</span>"
                     f"<span class='t'><i style='width:{max(min(d['sim'], 1), 0) * 100:.0f}%'></i></span>"
                     f"<span>最相似 {d['sim']:.2f}</span>")
    if r is not None:
        for chunk, score in r["chunks"][:3]:
            paths.append(f"<span data-tip='重排模型讀小段 {chunk}，判它和問題多對得上（0–1）'>重排・小段 {chunk}</span>"
                         f"<span class='t'><i style='width:{max(min(score, 1), 0) * 100:.0f}%'></i></span>"
                         f"<span>{score:.2f}</span>")
    facts = []
    if row.get("publisher"):
        facts.append(("發佈單位", e("、".join(row["publisher"]))))
    if row.get("expires") is not None:
        facts.append(("會過期", "會" + (f"<small>（{e(row['valid_ay'])} 學年）</small>" if row.get("valid_ay") else "")
                      if row["expires"] else "不會"))
    if row.get("period"):
        facts.append(("學期", e(row["period"])))
    if row.get("site"):  # its key, opening the site's page (structure design 2026-11-17 section 二)
        facts.append(("網站", f"<a href='/site?{urlencode({'site': row['site']})}'>{e(row['site'])}</a>"))
    right = ""
    if paths:
        right += f"<h3>關鍵字・向量・重排</h3><div class='mini'>{''.join(paths)}</div>"
    if facts:
        right += "<h3>文件資訊</h3><dl class='facts'>" + "".join(
            f"<div{' class=wide' if n == '網站' else ''}><dt>{n}</dt><dd>{v}</dd></div>" for n, v in facts) + "</dl>"
    if words:
        right += f"<h3>沒排上的原因</h3><div class='soft'>{e(words)}</div>"
    doc_id = row["doc_id"]
    act = (f"<a class='btn' href='/trace?{urlencode({'ref': doc_id})}' data-tip='看它從抓取到搜尋的每一步'>"
           f"{icon('trace', 13)}文件追蹤</a>"
           + (f"<a class='btn' href='{e(row['url'])}' target='_blank' rel='noopener' data-tip='打開學校網站上的這一頁'>"
              f"{icon('out', 12)}原網頁</a>"
              if str(row.get("url") or "").startswith(("http://", "https://")) else "")
           + f"<button class='btn mono' type='button' data-copy='{e(doc_id)}' data-tip='複製文件編號'>"
             f"{icon('copy', 12)}{e(doc_id)}</button>")
    return f"<div class='more'>{left}<div>{right}<div class='act'>{act}</div></div></div>"


def _row(row: dict, weights: dict, scale: float, *, marks: dict[str, str], limit: int, far: bool = False,
         stopped: tuple[str, str] | None = None, words: str = "") -> str:
    """One document: its final place, what it is, its place on each path, and its score as a bar (or, for a followed
    document the reranker never read, the word for where it stopped)."""
    p = parts(row, weights)
    doc_id = row["doc_id"]
    mark = marks.get(doc_id, "")
    star = (f"<span class='star' data-tip='{'標準答案' if mark == 'gold' else '指定的文件'}'>{icon('star', 10)}</span>"
            if mark else "")
    title = (f"{star}<a href='/trace?{urlencode({'ref': doc_id})}'>{e(row.get('title') or doc_id)}</a>"
             + (f" <span class='tag' data-tip='{e(background_words(weights))}'>背景</span>"
                if row.get("background") else ""))
    k, d = row.get("keyword"), row.get("dense")
    cells = (_place(k and k.get("rank"), "關鍵字") + _place(d and d.get("rank"), "向量")
             + _place(row.get("fused"), "合併後"))
    if stopped is not None:
        score = f"<span class='sc'><span class='pill {stopped[0]}'>{e(stopped[1])}</span></span><span class='tot none'>—</span>"
    else:
        score = (f"<span class='sc' data-tip='{e(_tip(row, p, weights))}'>{_bar(p, scale)}{_notes(p)}</span>"
                 f"<span class='tot'>{row['score']:.2f}</span>")
    rank = row.get("rank")
    why = ""  # where it stopped is the badge; the sentence is in the opened row (owner 11-15: 「頁面廢話太多了」)
    cls = "row" + (" gold" if mark else "")
    place = f"<b class='g-{_grade(rank, limit)}'>{rank}</b>" if mark and rank else str(rank or "—")
    return (f"<details class='{cls}' id='r-{e(doc_id)}'><summary>"
            f"<span class='rk{' far' if far and not mark else ''}'>{place}</span>"
            f"<div class='doc'><div class='ttl'>{title}</div><div class='about'>{_about(row)}</div>{why}</div>"
            f"{cells}{score}</summary>{_detail(row, p, weights, words)}</details>")


def _lost_row(entry: dict, marks: dict[str, str]) -> str:
    """A followed document no path brought, or one the library does not hold: what is known of it and why."""
    kind, label = STOPPED.get(entry["verdict"], ("none", ""))
    about = entry.get("about") or {}
    doc_id = entry.get("doc_id")
    mark = marks.get(doc_id or "", "")
    star = (f"<span class='star' data-tip='{'標準答案' if mark == 'gold' else '指定的文件'}'>{icon('star', 10)}</span>"
            if mark else "")
    name = about.get("title") or entry.get("ref") or doc_id or "（空白）"
    title = f"<a href='/trace?{urlencode({'ref': doc_id})}'>{e(name)}</a>" if doc_id else e(name)
    meta = f"<div class='about'>{_about(about)}</div>" if about else ""
    dots = "<span class='pl'>·</span>" * 3
    anchor = f" id='r-{e(doc_id)}'" if doc_id else ""
    return (f"<div class='row lost{' gold' if mark else ''}'{anchor}><div class='sum'>"
            f"<span class='rk far'>—</span><div class='doc'><div class='ttl'>{star}{title}</div>{meta}"
            f"</div>{dots}"
            f"<span class='sc'><span class='pill {kind}' data-tip='{e(entry['words'])}'>{e(label)}</span></span>"
            "<span class='tot none'>—</span>"
            "</div></div>")


# ---------------------------------------------------------------- the ruler of where the followed documents stand

def place_on_ruler(rank: float, last: int) -> float:
    """Where a final place sits on the ruler, in percent of its length 【Claude】: places 1 to 10, what a student's AI
    gets, take the first 56% evenly; 10 to 50 (what the reranker reads) the next 22% and 50 to the last candidate
    the rest, both by the place's logarithm, so that 64 and 187 are still apart."""
    last = max(last, 51)
    if rank <= 10:
        return max(rank - 1, 0) / 9 * 56
    if rank <= 50:
        return 56 + math.log(rank / 10) / math.log(5) * 22
    return min(78 + math.log(rank / 50) / math.log(last / 50) * 22, 100.0)


def _grade(rank: int | None, limit: int) -> str:
    if rank == 1:
        return grades.FIRST
    if rank is not None and rank <= 3:
        return grades.TOP3
    return grades.TOP if rank is not None and rank <= limit else grades.OUT


def verdict_html(report: dict, question: dict | None) -> str:
    """Above everything, when documents are followed: the best one's place as a badge, a sentence, and every followed
    document on one ruler (its place in a marker of its grade's colour; those no path brought counted at the end)."""
    followed = report["follow"]
    if not followed:
        return ""
    limit, last = report["limit"], report["counts"]["final"]
    ranked = sorted((en["row"]["rank"], en) for en in followed if en.get("row") and en["row"].get("rank"))
    lost = [en for en in followed if not (en.get("row") and en["row"].get("rank"))]
    best = ranked[0][0] if ranked else None
    grade = _grade(best, limit)
    what = "標準答案" if question else "指定的文件"
    if best is not None and best <= limit:
        head = f"{what}在第 {best} 名"
    elif question:
        head = f"前 {limit} 名裡沒有標準答案"
    else:
        head = f"{what}沒進前 {limit} 名"
    said = []
    if len(followed) > 1 or best is None or best > limit:
        if ranked:
            said.append("、".join(str(r) for r, _ in ranked[:8]) + (" …" if len(ranked) > 8 else "") + " 名")
        for verdict, word in ((NO_CANDIDATE, "沒進候選"), (NOT_FOUND, "庫裡沒有")):
            if n := sum(1 for en in lost if en["verdict"] == verdict):
                said.append(f"{word} {n} 份")
    sub = f"{len(followed)} 份：{' · '.join(said)}" if said else ""
    badge = (f"<div class='v-badge g-{grade}'><span>{best if best is not None else '—'}"
             f"{'<small>名</small>' if best is not None else ''}</span></div>")
    # the ruler
    zones = ((0, place_on_ruler(1.5, last), "z1"), (place_on_ruler(1.5, last), place_on_ruler(3.5, last), "z2"),
             (place_on_ruler(3.5, last), place_on_ruler(limit + 0.5, last), "z3"))
    track = "".join(f"<i class='zone {c}' style='left:{a:.2f}%;width:{b - a:.2f}%'></i>" for a, b, c in zones)
    ticks = "".join(f"<span style='left:{place_on_ruler(t, last):.2f}%'>{t}</span>"
                    for t in dict.fromkeys((1, 3, limit, 50, max(last, 51))))
    taken: list[float] = [-100.0, -100.0]
    marks, tall = [], False
    for rank, en in ranked:
        x = place_on_ruler(rank, last)
        lane = 0 if x - taken[0] >= 5 else 1
        tall = tall or lane == 1
        taken[lane] = x
        title = en["row"].get("title") or en["doc_id"]
        marks.append(f"<a class='m r{lane} g-{_grade(rank, limit)}' style='left:{x:.2f}%' href='#r-{e(en['doc_id'])}' "
                     f"data-tip='第 {rank} 名\n{e(title)}'>{rank}</a>")
    off = ""
    if lost:
        word = "庫裡沒有" if all(en["verdict"] == NOT_FOUND for en in lost) else "沒進候選"
        off = f"<div class='off'><b class='g-out'>{len(lost)}</b>{word}</div>"
    ruler = (f"<div class='ruler{' tall' if tall else ''}{' lost' if lost else ''}' aria-label='{e(head)}'>"
             "<div class='marks'>"
             f"{''.join(marks)}</div><div class='track'>{track}</div><div class='ticks'>{ticks}</div>{off}</div>")
    return (f"<section class='card pad verdict'><div class='v-main'>{badge}<div class='v-text'><b>{e(head)}</b>"
            f"<span>{e(sub)}</span></div></div>{ruler}</section>")


# ---------------------------------------------------------------- how the question was read, and the funnel

def _words(report: dict) -> str:
    """The question's words as the search read them: the ones a candidate must hold one of, the ones that only add
    (a unit's name, a term), and the ones an alias brought in."""
    u = report["understood"]
    topic, read = list(u["topic"]), list(u["words"])
    need = bool(topic)
    chips = []
    for word in read:
        must = need and word in topic
        tip = ("講什麼的詞：候選文件至少要有一個" if must else
               "只加分的詞（單位名、學期這類）：對上了加分，沒對上也照樣可以是候選" if need else "問題切出的詞")
        chips.append(f"<span class='w {'must' if must else 'plain'}' data-tip='{tip}'>{e(tw(word))}</span>")
    for word in topic:
        if word not in read:
            chips.append(f"<span class='w also' data-tip='別名帶出來的詞，也算講什麼的詞'>＋{e(tw(word))}</span>")
    off = "這次不加新舊分，也不打「還有效」的折扣" if not report["recent"] and report["order"] == "reranked" else ""
    for term in u["terms"]:
        chips.append(f"<span class='w term' data-tip='問題點名的學期{'：' + off if off else ''}'>{icon('event', 12)}{e(term)}"
                     "</span>")
    for cohort in u["cohorts"]:
        chips.append(f"<span class='w term' data-tip='問題點名的入學屆'>{e(cohort)} 入學</span>")
    key = []
    if need:
        # three words, no sentence (owner 11-15: 「頁面廢話太多了」); what each means floats over its chips
        key.append("<span><i class='must'></i>要對上</span><span><i class='plain'></i>只加分</span>")
        if any(word not in read for word in topic):
            key.append("<span><i class='also'></i>別名</span>")
    return (f"<div><div class='cap' data-tip='問題切成的詞：搜尋拿它們去比對'>查詢詞</div><div class='words'>{''.join(chips)}</div>"
            f"<div class='keyline'>{''.join(key)}</div></div>")


# a funnel stage's name -> what floats over its block
STAGE_TIPS = {"關鍵字": "關鍵字找到", "向量": "向量找到", "合併後": "兩路合併、去掉重複後", "重排": "重排模型讀了",
              "回傳": "最後回傳"}


def _funnel(report: dict) -> str:
    """How many documents each step held, drawn: the two paths' candidates merge, the reranker reads the first of
    them, and a few are given out. A block's height follows the square root of its count 【Claude】 (737 beside 10 on
    a straight scale would leave nothing to see)."""
    c = report["counts"]
    vectors, reranked = report["vectors"] == "used", report["order"] == "reranked"
    first = [("關鍵字", c["keyword"], "")] + ([("向量", sum(c["dense"]), "b")] if vectors else [])
    stages: list[tuple[list[tuple[str, int, str]], str, str]] = [
        (first, "＋".join(f"{n:,}" for _, n, _ in first), "＋".join(name for name, _, _ in first))]
    if vectors:
        stages.append(([("合併後", c["final"], "")], f"{c['final']:,}", "合併後"))
    if reranked:
        head = sum(c["head"])
        stages.append(([("重排", head, "")], f"{head:,}", "重排"))
    shown = len(report["rows"])
    stages.append(([("回傳", shown, "")], f"{shown:,}", "回傳"))
    tallest = max(sum(math.sqrt(max(n, 0)) for _, n, _ in blocks) for blocks, _, _ in stages) or 1.0
    unit = 62 / tallest

    def height(n: int) -> float:
        return max(math.sqrt(max(n, 0)) * unit, 3.0)

    def stack(blocks) -> float:
        return sum(height(n) for _, n, _ in blocks) + 3 * (len(blocks) - 1)

    out, labels = [], []
    for i, (blocks, number, name) in enumerate(stages):
        if i:
            a, b = stack(stages[i - 1][0]), stack(blocks)
            out.append(f"<span class='flow' style='clip-path:polygon(0 calc(50% - {a / 2:.1f}px),"
                       f"100% calc(50% - {b / 2:.1f}px),100% calc(50% + {b / 2:.1f}px),"
                       f"0 calc(50% + {a / 2:.1f}px))'></span>")
        tips = "\n".join(f"{STAGE_TIPS.get(n, n)}：{count:,} 份" for n, count, _ in blocks)
        out.append(f"<span class='st' data-tip='{e(tips)}'>" + "".join(
            f"<i class='blk {tone}' style='height:{height(n):.1f}px'></i>" for _, n, tone in blocks) + "</span>")
        labels.append(f"<div><b>{number}</b>{name}</div>")
    note = ""
    if report["vectors"] != "used" or not reranked or report["dense_error"]:
        note = f"<div class='off-note'>{e(order_words(report))}</div>"
    return (f"<div><div class='cap' data-tip='關鍵字和向量各找一批，合併去掉重複，重排模型讀最前面的，最後回傳幾份'>"
            f"候選篩選</div><div class='funnel'>{''.join(out)}</div>"
            f"<div class='flabels'>{''.join(labels)}</div>{note}</div>")


def pipeline_html(report: dict) -> str:
    return f"<section class='card pipe'>{_words(report)}{_funnel(report)}</section>"


# ---------------------------------------------------------------- the whole page of one search

def _help(report: dict) -> str:
    """One click away: the rule in the explanation's own sentence (explain.order_words) and what each part is."""
    w = report["weights"]
    rows = (("對題", f"{PART_TIPS['對題']}（{w['rerank']:g}）"),
            *((n, PART_TIPS[n]) for n in ("學期", "屆別", "新舊", "還有效", "對口")),
            ("關鍵字・向量・合併", "關鍵字、向量各自把它排第幾，兩邊合併後第幾；重排只讀合併後最前面的那些"))
    listed = "".join(f"<dt>{e(n)}</dt><dd>{e(v)}</dd>" for n, v in rows)
    return (f"<details class='help'><summary aria-label='分數怎麼算' data-tip='分數怎麼算'>?</summary><div class='pop'><b>{e(order_words(report))}"
            f"</b><dl>{listed}</dl></div></details>")


def search_body(report: dict, gold: set[str], question: dict | None) -> str:
    """One search, explained: the verdict (when documents are followed), how the question was read and the funnel,
    then the documents."""
    if report["mode"] == "empty":
        return "<section class='card empty'>這個問題沒有可以搜的詞（只有標點或空白）。</section>"
    weights, limit = report["weights"], report["limit"]
    rows = report["rows"]
    followed = report["follow"]
    marks = {en["doc_id"]: ("gold" if en["doc_id"] in gold else "ask") for en in followed if en.get("doc_id")}
    inside = {row["doc_id"] for row in rows}
    extra = [en for en in followed if not en.get("doc_id") or en["doc_id"] not in inside]
    scored = rows + [en["row"] for en in extra if en.get("row") and en["verdict"] != NOT_IN_HEAD]
    scale = max((parts(row, weights)["pre"] + max(parts(row, weights)["bonus"], 0.0) for row in scored), default=0.0)
    base = "對題" if report["order"] == "reranked" else "關鍵字"
    head = ("<div class='rhead'><span class='r'>名次</span><span>文件</span>"
            "<span class='c' data-tip='關鍵字那一路把它排第幾'>關鍵字</span>"
            "<span class='c' data-tip='向量那一路把它排第幾'>向量</span>"
            "<span class='c' data-tip='兩路合併後排第幾；重排只讀最前面的那些'>合併</span>"
            f"<span class='lg'><span data-tip='{e(PART_TIPS[base])}'><i class='rel'></i>{base}</span>"
            "<span data-tip='學期、屆別、新舊、單位加的分'><i class='add'></i>加分</span>"
            f"<span data-tip='「還有效」「對口」折掉的分'><i class='cut'></i>折扣</span>{_help(report)}</span>"
            "<span class='r'>總分</span></div>")
    by_id = {en["doc_id"]: en for en in followed if en.get("doc_id")}
    body = "".join(_row(row, weights, scale, marks=marks, limit=limit,
                        words=(by_id.get(row["doc_id"]) or {}).get("words", "")) for row in rows)
    if not rows:
        body = "<div class='empty'>沒有結果。</div>"
    if extra:
        what = "標準答案" if question else "指定的文件"
        body += f"<div class='rdiv'>沒進前 {limit} 名的{what}</div>"
        order = {RANKED: 0, IN_TOP: 0, NOT_IN_HEAD: 1, NO_CANDIDATE: 2, NOT_FOUND: 3}
        for en in sorted(extra, key=lambda x: (order.get(x["verdict"], 9), (x.get("row") or {}).get("rank") or 0)):
            if en.get("row"):
                stopped = STOPPED.get(en["verdict"])
                body += _row(en["row"], weights, scale, marks=marks, limit=limit, far=True, stopped=stopped,
                             words=en["words"])
            else:
                body += _lost_row(en, marks)
    count = f"前 {len(rows)} 名" if rows else "結果"
    return (verdict_html(report, question) + pipeline_html(report)
            + f"<h2 class='sec'>{count}</h2>"
            + f"<section class='card rows'>{head}{body}</section>")
