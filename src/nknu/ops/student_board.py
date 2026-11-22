"""搜尋's scoreboard since 2026-11-22 evening: the student questions, measured each night on the library students got
(deploy/lab-pipeline/eval/student_eval.py → student_eval.json), in place of the 33 questions (owner: 「33题那个后面要
改，拿来当尺子就不对呀」).

Drawn from the file and nothing else: the 83 questions written from the student's side whose answer the library holds,
as typed and as an agent searched them (its recorded queries, the best of them), how many put the right document first,
in the top 3 and in the top 10, against the night before; the questions that dropped out of the top 10 since then; and
the half looked at during work question by question. The sealed half is said as totals only (its questions stay
unseen, so the work is not fitted to them). 【Claude】
"""

from __future__ import annotations

from urllib.parse import urlencode

from nknu.ops import todo
from nknu.ops.look import e

WAYS = (("raw", "學生原話", "學生問的那句話原樣送進搜尋"),
        ("agent", "AI 換說法", "錄下來的 AI 查詢（只看問題和工具說明寫的），最好的一次"))
# the nightly sample's groups (version 2 of student_eval.json, the owner's choice of 11-22 night: a sample of the open
# half, under five minutes): B the yardstick, as typed and as an AI rewords it; A and C as typed only
GROUPS = (("B", "學生角度", "學生會問的題，庫裡有答案：主指標"),
          ("A", "從文件出的題", "對著庫裡的文件寫的題：只看涵蓋，數字偏高，不當成績"),
          ("C", "新進庫的內容", "題庫寫好以後才進庫的內容（課程大綱、課表、教師履歷、新網站）：看新內容找不找得到"))

CSS = """
.sb{display:flex;flex-direction:column;gap:14px}
.sb-when{font-size:12px;color:var(--ink3);font-weight:400;margin-left:8px}
.sb-rows{display:flex;flex-direction:column;gap:10px}
.sb-row{display:grid;grid-template-columns:150px minmax(120px,1fr) auto;align-items:center;gap:6px 14px}
.sb-row b{font-size:13px}
.sb-track{position:relative;height:12px;border-radius:999px;background:var(--mark2);overflow:hidden}
.sb-track i{position:absolute;inset:0 auto 0 0;border-radius:999px}
.sb-track .t10{background:var(--q1)}.sb-track .t3{background:var(--q3)}.sb-track .t1{background:var(--q5)}
.sb-nums{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums}
.sb-nums strong{font-size:16px;color:var(--ink)}
.sb-delta{font-size:12px;color:var(--ink3)}
.sb-key{display:flex;gap:14px;font-size:12px;color:var(--ink3)}
.sb-key i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.sb-sealed{font-size:12px;color:var(--ink3);margin:0}
.sb h3{margin:6px 0 0;font-size:14px}
.sb-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}
.sb-list li{display:grid;grid-template-columns:52px minmax(0,1fr) 86px 86px;align-items:center;gap:8px;padding:7px 0;
border-top:1px solid var(--line);font-size:13px}
.sb-list li.sb-head{border-top:0;font-size:12px;color:var(--ink3)}
.sb-list a{color:var(--ink);text-decoration:none}
.sb-list a:hover{text-decoration:underline;text-underline-offset:3px}
.sb-id{font-size:12px;color:var(--ink3);font-variant-numeric:tabular-nums}
.sb-rank{font-variant-numeric:tabular-nums;white-space:nowrap}
.sb-rank[data-r=far]{color:var(--ink3)}
.sb-rank small{margin-left:4px;font-size:11px;color:var(--ink3)}
.sb-rank small[data-d=down]{color:var(--warn-ink)}
.sb-none{font-size:13px;color:var(--ink2);margin:0}
.sb-old{margin:0;padding:8px 12px;border-radius:8px;background:var(--warn-bg);color:var(--warn-ink);font-size:13px}
@media (max-width:640px){.sb-row{grid-template-columns:1fr}.sb-list li{grid-template-columns:40px minmax(0,1fr) 64px 64px}}
"""


def _pct(v: int, of: int) -> float:
    return v / of * 100 if of else 0.0


def _rank(now: int | None, before: int | None) -> str:
    """A rank with how it moved since the night before (only when it moved)."""
    far = now is None or now > todo.TOP
    words = "沒搜到" if now is None else f"第 {now}"
    move = ""
    if before is not None and now != before:
        worse = now is None or now > before
        move = (f"<small data-d='{'down' if worse else 'up'}' title='前一晚 {todo.rank_words(before)}'>"
                f"{'↓' if worse else '↑'}{todo.rank_words(before).replace('第 ', '')}</small>")
    return f"<span class='sb-rank' data-r='{'far' if far else 'in'}'>{e(words)}{move}</span>"


def why_href(q: str, qid: str = "") -> str:
    """The question explained on 搜尋's second tab: by its id when it has one (its right documents are followed, so
    the page shows where they stand), else by its words."""
    return "/try?" + urlencode({"tab": "why", "qid": qid} if qid else {"tab": "why", "q": q}) + "#result"


def _ladder(word: str, t: dict, n: int, before: dict | None, tip: str) -> str:
    """One row: the share at first place, in the top 3 and in the top 10, the numbers, and the change at first place
    since the night before."""
    p1, p3, p10 = (_pct(t.get(k, 0), n) for k in ("top1", "top3", "top10"))
    delta = "" if not before or before.get("top1") is None else f"比前一晚 {t.get('top1', 0) - before['top1']:+d} 題"
    return (f"<div class='sb-row' data-tip='{e(tip)}'><b>{e(word)}</b>"
            f"<span class='sb-track'><i class='t10' style='width:{p10:.1f}%'></i><i class='t3' style='width:{p3:.1f}%'></i>"
            f"<i class='t1' style='width:{p1:.1f}%'></i></span>"
            f"<span class='sb-nums'><span>第 1 名 <strong>{t.get('top1', 0)}</strong>（{p1:.0f}%）</span>"
            f"<span>前 3 <strong>{t.get('top3', 0)}</strong></span><span>前 10 <strong>{t.get('top10', 0)}</strong></span>"
            f"<span>共 {n} 題</span><span class='sb-delta'>{e(delta)}</span></span></div>")


def sample_rows(student: dict) -> tuple[str, str]:
    """(the rows of the nightly sample's groups, a line about the sample) for a version-2 file."""
    groups = student.get("groups") or {}
    prev = ((student.get("prev") or {}).get("groups")) or {}
    rows = []
    for key, word, tip in GROUPS:
        g = groups.get(key) or {}
        if not g.get("n"):
            continue
        rows.append(_ladder(f"{word} · 原話", g.get("raw") or {}, g["n"], (prev.get(key) or {}).get("raw"), tip))
        if g.get("agent"):
            rows.append(_ladder(f"{word} · AI 換說法", g["agent"], g["n"], (prev.get(key) or {}).get("agent"),
                                "錄下來的 AI 查詢（只看問題和工具說明寫的）的第一條"))
    s = student.get("sample") or {}
    expired = student.get("expired") or []
    line = (f"<p class='sb-sealed'>每晚量題庫平時那一半抽出來的 {sum((s.get('groups') or {}).values()) or ''} 題"
            f"（抽樣 {e(s.get('version') or '')}，{student.get('seconds') or '—'} 秒）；封存的那一半不在每晚量，"
            "留給偶爾的全量量測"
            + (f"；過了期限的 {len(expired)} 題不算進總數：{e('、'.join(expired))}" if expired else "") + "</p>")
    return "".join(rows), line


def stale(student: dict | None, built: str) -> bool:
    """Whether the measurement is of another library than the one in use (a night the swap was held keeps the last
    measurement: the content session's note, 11-22). built: the library in use, as db_info has it."""
    lib = ((student or {}).get("library") or {}).get("built") or ""
    return bool(built and lib) and lib[:16] != built[:16]


def board(student: dict, built: str = "") -> str:
    """The scoreboard (the module docstring), or '' without a measurement. built: the library in use (a measurement
    of another one is said so)."""
    if not student or not student.get("n"):
        return ""
    n = student["n"]
    if student.get("groups"):  # version 2: the nightly sample
        return _board_v2(student, built)
    tot = student.get("totals") or {}
    prev = ((student.get("prev") or {}).get("totals")) or {}
    rows = []
    for key, word, tip in WAYS:
        t = tot.get(key) or {}
        p1, p3, p10 = (_pct(t.get(k, 0), n) for k in ("top1", "top3", "top10"))
        before = (prev.get(key) or {}).get("top1")
        delta = "" if before is None else f"比前一晚 {t.get('top1', 0) - before:+d} 題"
        rows.append(
            f"<div class='sb-row' data-tip='{e(tip)}'><b>{e(word)}</b>"
            f"<span class='sb-track'><i class='t10' style='width:{p10:.1f}%'></i><i class='t3' style='width:{p3:.1f}%'></i>"
            f"<i class='t1' style='width:{p1:.1f}%'></i></span>"
            f"<span class='sb-nums'><span>第 1 名 <strong>{t.get('top1', 0)}</strong>（{p1:.0f}%）</span>"
            f"<span>前 3 <strong>{t.get('top3', 0)}</strong></span><span>前 10 <strong>{t.get('top10', 0)}</strong></span>"
            f"<span class='sb-delta'>{e(delta)}</span></span></div>")
    key = ("<div class='sb-key'><span><i style='background:var(--q5)'></i>排第 1</span>"
           "<span><i style='background:var(--q3)'></i>前 3</span><span><i style='background:var(--q1)'></i>前 10</span></div>")
    halves = student.get("halves") or {}
    sealed = halves.get("sealed") or {}
    sealed_line = ""
    if sealed.get("n"):
        s_raw, s_agent = sealed.get("raw") or {}, sealed.get("agent") or {}
        sealed_line = (f"<p class='sb-sealed'>封存的 {sealed['n']} 題只看總數（題目不看，免得把搜尋調成只對它們好）："
                       f"學生原話第 1 名 {s_raw.get('top1', 0)}、前 10 {s_raw.get('top10', 0)}；"
                       f"AI 換說法第 1 名 {s_agent.get('top1', 0)}、前 10 {s_agent.get('top10', 0)}</p>")

    dropped = todo.drops(student)
    if student.get("prev") is None:
        drops_html = "<p class='sb-none'>第一次量，還沒有前一晚可以比</p>"
    elif dropped:
        lis = "".join(
            f"<li><span class='sb-id'>{e(x['id'])}</span><a href='{e(why_href(x['q']))}'>{e(x['q'])}</a>"
            f"<span>{e(x['way_word'])}</span>{_rank(x['now'], x['before'])}</li>" for x in dropped)
        drops_html = f"<ul class='sb-list'>{lis}</ul>"
    else:
        drops_html = "<p class='sb-none'>沒有掉出前 10 的題</p>"

    qs = sorted(student.get("questions") or [],
                key=lambda q: (-(q.get("raw") or 10**6), q.get("id") or ""))  # the worst first
    open_n = (halves.get("open") or {}).get("n") or len(qs)
    lis = "".join(
        f"<li><span class='sb-id'>{e(q.get('id') or '')}</span><a href='{e(why_href(q.get('q') or ''))}'>{e(q.get('q') or '')}</a>"
        f"{_rank(q.get('raw'), q.get('prev_raw'))}{_rank(q.get('agent'), q.get('prev_agent'))}</li>" for q in qs)
    head = "<li class='sb-head'><span></span><span>題目（點了看它怎麼排）</span><span>學生原話</span><span>AI 換說法</span></li>"
    when = (student.get("measured") or "")[5:16].replace("T", " ")
    now_built, built = built, ((student.get("library") or {}).get("built") or "")[5:16].replace("T", " ")
    old = ("<p class='sb-old'>不是現在這份庫的成績：學生搜到的庫換過了，這一份還沒量（每晚換庫後才量）</p>"
           if stale(student, now_built) else "")
    return (f"<style>{CSS}</style><h2 class='sec' id='students'>學生題的名次"
            f"<span class='sb-when'>{n} 題 · {e(when)} 量 · {e(built)} 建的庫</span></h2>"
            f"<section class='card pad sb'>{old}<div class='sb-rows'>{''.join(rows)}</div>{key}{sealed_line}"
            f"<h3 id='drops'>掉出前 10 的題<span class='sb-when'>和前一晚比</span></h3>{drops_html}"
            f"<h3>平時看的 {open_n} 題<span class='sb-when'>名次最差的在前</span></h3>"
            f"<ul class='sb-list'>{head}{lis}</ul></section>")


def _board_v2(student: dict, built: str) -> str:
    """The nightly sample (student_eval.json version 2): each group's ladder, the questions that dropped out of the
    top 10, then every sampled question by group, the worst first, each opening its explanation."""
    rows, line = sample_rows(student)
    key = ("<div class='sb-key'><span><i style='background:var(--q5)'></i>排第 1</span>"
           "<span><i style='background:var(--q3)'></i>前 3</span><span><i style='background:var(--q1)'></i>前 10</span></div>")
    dropped = todo.drops(student)
    if student.get("prev") is None:
        drops_html = "<p class='sb-none'>第一次量這份抽樣，還沒有前一晚可以比</p>"
    elif dropped:
        drops_html = "<ul class='sb-list'>" + "".join(
            f"<li><span class='sb-id'>{e(x['id'])}</span><a href='{e(why_href(x['q'], x['id']))}'>{e(x['q'])}</a>"
            f"<span>{e(x['way_word'])}</span>{_rank(x['now'], x['before'])}</li>" for x in dropped) + "</ul>"
    else:
        drops_html = "<p class='sb-none'>沒有掉出前 10 的題</p>"
    lists = []
    head = "<li class='sb-head'><span></span><span>題目（點了看它怎麼排）</span><span>原話</span><span>AI 換說法</span></li>"
    for gkey, word, _tip in GROUPS:
        qs = sorted((q for q in student.get("questions") or [] if q.get("group") == gkey),
                    key=lambda q: (-(q.get("raw") or 10**6), q.get("id") or ""))
        if not qs:
            continue
        lis = "".join(
            f"<li><span class='sb-id'>{e(q.get('id') or '')}</span>"
            f"<a href='{e(why_href(q.get('q') or '', q.get('id') or ''))}'>{e(q.get('q') or '')}</a>"
            f"{_rank(q.get('raw'), q.get('prev_raw'))}"
            + (_rank(q.get("agent"), q.get("prev_agent")) if q.get("group") == "B" else "<span></span>") + "</li>"
            for q in qs)
        lists.append(f"<h3>{e(word)}<span class='sb-when'>{len(qs)} 題，名次最差的在前</span></h3>"
                     f"<ul class='sb-list'>{head}{lis}</ul>")
    when = (student.get("measured") or "")[5:16].replace("T", " ")
    lib = ((student.get("library") or {}).get("built") or "")[5:16].replace("T", " ")
    old = ("<p class='sb-old'>不是現在這份庫的成績：學生搜到的庫換過了，這一份還沒量（每晚換庫後才量）</p>"
           if stale(student, built) else "")
    return (f"<style>{CSS}</style><h2 class='sec' id='students'>學生題的名次"
            f"<span class='sb-when'>{e(when)} 量 · {e(lib)} 建的庫</span></h2>"
            f"<section class='card pad sb'>{old}<div class='sb-rows'>{rows}</div>{key}{line}"
            f"<h3 id='drops'>掉出前 10 的題<span class='sb-when'>和前一晚比</span></h3>{drops_html}"
            f"{''.join(lists)}</section>")
