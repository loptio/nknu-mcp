"""检视台 (plan 2026-11-15 T5; spec 2026-11-15 section 二): two pages for the owner, 搜索试验台 (why a search ranked
as it did: nknu.ops.explain) and 逐份追踪 (what one page or file became at each step: nknu.ops.trace), and the same two
as JSON for the commands (nknu.ops.cli's --at). The pages print what the two modules return and compute nothing.

It reads the library, which holds students' names, so it is read-only (the database is opened read-only on every
request), every route is GET only, and a request is answered only when its Host names this machine (127.0.0.1,
localhost, [::1]): the lab's Docker container listens on 0.0.0.0, Docker forwards only the lab PC's own 127.0.0.1 to
it, and the Mac comes in through an SSH tunnel, as with the archive viewer. An archived page is shown under a policy
that lets it load nothing.

    python -m nknu.ops.web [--db research/pipeline_2026-11-02/aca.db] [--root .] [--host 127.0.0.1] [--port 8878]
                           [--viewer-url http://127.0.0.1:18877] [--questions DIR] [--gold FILE]
                           [--no-dense] [--no-rerank]
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from nknu.core.timeutil import today_tpe
from nknu.ops.explain import WHY, explain_search, found_words, order_words
from nknu.ops.trace import (
    DATE_KINDS,
    HOW,
    KINDS,
    WITHIN,
    cohort_words,
    how_converted,
    merged_words,
    site_exports,
    size_words,
    trace,
    under_read_words,
    when,
)
from nknu.retrieval.hybrid import HybridConfig, hybrid_search
from nknu.retrieval.search import TOP_N
from nknu.retrieval.server import DEFAULT_DB, add_model_flags, aliases_of, config_from, connect, search_answer
from nknu.settings import REPO_ROOT
from nknu.totext.cache import sha256_file

log = logging.getLogger(__name__)

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
DEFAULT_PORT = 8878
MAX_LIMIT = 50  # the most results a page or the API explains at once 【Claude】
# what an archived page is shown under: it loads nothing and runs nothing (the archive viewer's PREVIEW_CSP)
ARCHIVED_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; media-src 'none'; "
                "frame-src 'none'; form-action 'none'; sandbox")
TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8"}
MARKUP = (".html", ".htm", ".xhtml", ".svg")
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
OWN_CODE = "nknu/ops/"  # the tools' own package: never the pipeline's (install_state)
STATE_SECONDS = 60.0  # how long an install state is kept before the two source trees are compared again 【Claude】
CSS = """
:root{--ink:#1c1c1a;--soft:#6b6a65;--line:#e2e0da;--bg:#fbfaf7;--card:#fff;--mark:#b3261e;--good:#2e6f4e;--bar:#3578b8}
@media (prefers-color-scheme:dark){:root{--ink:#eceae4;--soft:#a3a199;--line:#3a3935;--bg:#1b1b19;--card:#242422;
--mark:#f2857c;--good:#7fc8a0;--bar:#6ebbe8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,"PingFang SC",
"Noto Sans CJK SC",sans-serif}a{color:inherit}main{max-width:1280px;margin:0 auto;padding:16px}
nav{display:flex;gap:18px;align-items:baseline;padding:10px 16px;border-bottom:1px solid var(--line)}
nav b{font-size:17px}nav .db{margin-left:auto;color:var(--soft);font-size:13px}
form.ask{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}form.ask input{flex:1 1 280px;padding:8px 10px;
border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);font:inherit}
form.ask button{padding:8px 18px;border:0;border-radius:6px;background:var(--ink);color:var(--bg);font:inherit}
.qs{color:var(--soft);font-size:13px;line-height:2}.qs a{margin-right:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:12px 0}
.chip{display:inline-block;padding:0 8px;margin:0 4px 2px 0;border:1px solid var(--line);border-radius:10px}
.soft{color:var(--soft)}.small{font-size:13px}.mark{color:var(--mark)}.good{color:var(--good)}
table{border-collapse:collapse;width:100%}th,td{padding:6px 8px;border-bottom:1px solid var(--line);
text-align:left;vertical-align:top}th{font-weight:600;font-size:13px;color:var(--soft);white-space:nowrap}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}tr.back td{background:rgba(127,127,127,.07)}
.bar{display:inline-block;width:56px;height:8px;background:var(--line);border-radius:4px;vertical-align:middle;
margin-right:6px}.bar i{display:block;height:8px;border-radius:4px;background:var(--bar)}
details{margin-top:4px}summary{cursor:pointer;color:var(--soft);font-size:13px}
pre{white-space:pre-wrap;word-break:break-word;margin:6px 0;font:13px/1.5 ui-monospace,Menlo,monospace}
h2{font-size:16px;margin:18px 0 6px}.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:900px){.two{grid-template-columns:1fr}}iframe.orig{width:100%;height:48vh;border:1px solid var(--line);
background:#fff}img.orig{max-width:100%;max-height:48vh;border:1px solid var(--line);background:#fff}.two pre{max-height:48vh;overflow:auto}.warn{border-color:var(--mark)}.kv td:first-child{color:var(--soft);white-space:nowrap;width:9em}
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def local_host(header: str) -> bool:
    """Whether a Host header names this machine: one of LOCAL_HOSTS, alone or followed by a port, and nothing else
    (「[::1]evil.example」 and 「127.0.0.1.evil.example」 do not)."""
    host = header.strip().lower()
    if host.startswith("["):
        name, _, rest = host.partition("]")
        name += "]"
    else:
        name, colon, port = host.partition(":")
        rest = colon + port
    return name in LOCAL_HOSTS and (not rest or (rest[0] == ":" and rest[1:].isascii() and rest[1:].isdigit()))


class LocalOnly:
    """The ASGI app that passes a request on only when its Host is one of LOCAL_HOSTS (any port); any other gets 403:
    a page of another site, opened in the owner's browser, cannot read the library under a name of its own."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), "")
            if not local_host(host):
                await PlainTextResponse("forbidden", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def install_state(code_root: Path, pipeline_src: Path) -> dict | None:
    """What install.sh left of the copy the tools run from, and which files of the pipeline's source have changed
    since: {'installed', 'pack', 'drift'}; None when code_root is no installed copy (no INSTALLED file: the
    repository itself, where the tools and the pipeline are one tree).
    The tools run a copy of the pipeline's source with their own files laid over it (deploy/lab-inspect/install.sh;
    plan 2026-11-15 T8), so after the pipeline's code changes, the scores and the left-out reasons shown here are the
    older rules' until the copy is installed again (review 11-15: nothing on the pages showed it). drift: the
    pipeline's .py files that differ from the copy's, a new file of the pipeline's counting too; a file the pack
    replaced counts only when the pipeline's is neither the version the pack was made from (BASE.sha256) nor the
    copy's own (after a code sync the pipeline has the tools' changes too: nothing to warn of); the tools' own
    package aside."""
    try:
        stamp = (code_root / "INSTALLED").read_text(encoding="utf-8").split()
    except OSError:
        return None
    base: dict[str, str] = {}
    try:
        for line in (code_root / "BASE.sha256").read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition("  ")
            if name.strip():
                base[name.strip()] = sha.strip()
    except OSError:
        pass
    drift = []
    for path in sorted(pipeline_src.rglob("*.py")):
        rel = path.relative_to(pipeline_src).as_posix()
        if rel.startswith(OWN_CODE):
            continue
        ours = code_root / "src" / rel
        same = {sha256_file(ours)} if ours.is_file() else set()
        if (made_from := base.get("src/" + rel)) is not None:
            same.add(made_from)
        if sha256_file(path) not in same:
            drift.append(rel)
    return {"installed": stamp[0] if stamp else "", "pack": stamp[1] if len(stamp) > 1 else "", "drift": drift}


def load_questions(folder: Path | None, gold: Path | None) -> list[dict]:
    """The test questions that have gold documents, [{id, question, gold}], in the files' order: core20.json and
    noanswer22.json of folder (research/eval_2026-11-01) and the gold file (default: folder's gold_docs.json). [] when
    a file is missing or unreadable: the pages then offer no question."""
    if folder is None:
        return []
    try:
        answers = json.loads(Path(gold or Path(folder) / "gold_docs.json").read_text(encoding="utf-8"))
        asked = [q for name in ("core20.json", "noanswer22.json")
                 for q in json.loads((Path(folder) / name).read_text(encoding="utf-8"))]
        return [{"id": q["id"], "question": q["question"], "gold": list(answers[q["id"]].get("docs") or [])}
                for q in asked if q.get("id") in answers and q.get("question")]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        log.warning("读不了测试题（%s）：页面上不列题目", folder)
        return []


# ---------------------------------------------------------------- the pages' pieces

def _shell(title: str, body: str, db: dict, status: int = 200) -> HTMLResponse:
    built = f"读的库：{_e((db.get('built_at') or '不知道什么时候')[:16].replace('T', ' '))} 建 · {_e(db.get('schema') or '')}"
    state = db.get("install")
    if state:
        built += f" · 检视工具 {_e(state['installed'][:16].replace('T', ' '))} 装"
        if state["drift"]:
            some = "、".join(state["drift"][:3]) + (" 等" if len(state["drift"]) > 3 else "")
            body = (f"<div class='card warn'><b class='mark'>建库用的程序有 {len(state['drift'])} 个档和检视工具装的这份不一样</b>"
                    f"（{_e(some)}）：这里的打分和「没进库的原因」可能不是现在的规则。照 deploy/lab-inspect/README.md"
                    "「换程序」重装一次就一致了。</div>") + body
    page = (f"<!doctype html><html lang='zh-Hans'><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
            f"initial-scale=1'><title>{_e(title)}</title><style>{CSS}</style><nav><b>检视台</b>"
            f"<a href='/search'>搜索试验台</a><a href='/trace'>逐份追踪</a><span class='db'>{built}</span></nav>"
            f"<main>{body}</main></html>")
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "no-store"})


def _plus(value: float | None) -> str:
    return "<span class='soft'>—</span>" if not value else f"{value:+.2f}"


def _times(value: float | None) -> str:
    if value is None:
        return "<span class='soft'>—</span>"
    return f"<span class='{'mark' if value < 1 else 'soft'}'>×{value:.2f}</span>"


def _row_html(row: dict, gold: set[str]) -> str:
    """One document as a table row: its place, its score, the parts of it, and where each path found it."""
    kw = row["keyword"]["rank"] if row["keyword"] else "—"
    dense = row["dense"]["rank"] if row["dense"] else "—"
    star = "<span class='good' title='标准答案'>★</span> " if row["doc_id"] in gold else ""
    about = " · ".join(_e(x) for x in (row.get("unit"), KINDS.get(row.get("kind"), row.get("kind")),
                                       "背景" if row["background"] else "", row["doc_id"]) if x)
    more = []
    if row.get("publisher"):
        more.append(f"谁发的：{_e('、'.join(row['publisher']))}")
    if row.get("expires") is not None:
        more.append("会过期" + (f"（{_e(row['valid_ay'])} 学年）" if row.get("valid_ay") else "") if row["expires"]
                    else "不会过期")
    if (k := row["keyword"]) is not None:
        more.append(f"关键词这一关：涵盖 {k['coverage']:.2f}，关键词分 {k['bm25_norm']:.2f}，学期 {k['period']:.1f}，"
                    f"届别 {'有' if k['cohort'] else '无'}，新旧 {_plus(k['date'])}，对口 {_times(k['scope'])}，"
                    f"合计 {k['score']:.2f}")
    if row["dense"]:
        more.append(f"向量这一关：最像的一段相似度 {row['dense']['sim']:.3f}")
    if (r := row["rerank"]) is not None:
        more.append("重排读的小段：" + "、".join(f"{c}（{s:.2f}）" for c, s in r["chunks"]))
        cells = (f"<td class='n'><span class='bar'><i style='width:{max(0.0, min(1.0, r['rel'])) * 100:.0f}%'></i></span>"
                 f"{r['rel']:.2f}</td><td class='n'>{_plus(r['period'])}</td><td class='n'>{_plus(r['cohort'])}</td>"
                 f"<td class='n'>{_plus(r['date'])}</td><td class='n'>{_times(r['valid'])}</td>"
                 f"<td class='n'>{_times(r['scope'])}</td><td class='n'>{_plus(r['bonus'])}</td>")
    else:
        cells = "<td colspan='7' class='soft small'>没经过重排，总分是关键词这一关的分</td>"
    details = f"<details><summary>细节</summary><div class='small'>{'<br>'.join(more)}</div></details>" if more else ""
    return (f"<tr class='{'back' if row['background'] else ''}'><td class='n'>{row['rank'] or '—'}</td>"
            f"<td class='n'><b>{row['score']:.2f}</b></td>{cells}"
            f"<td class='n' title='关键词 / 向量 / 两边合起来，各第几名'>{kw} / {dense} / {row['fused'] or '—'}</td>"
            f"<td class='n'>{_e(row.get('date') or '无日期')}</td>"
            f"<td>{star}<a href='/trace?{urlencode({'ref': row['doc_id']})}'>{_e(row.get('title') or row['doc_id'])}</a>"
            f"<div class='soft small'>{about}</div>{details}</td></tr>")


TABLE_HEAD = ("<table><thead><tr><th>名次</th><th>总分</th><th>对题</th><th>学期</th><th>届别</th><th>新旧</th>"
              "<th>还有效</th><th>对口</th><th>单位</th><th>找到它的名次</th><th>日期</th><th>文件</th></tr></thead><tbody>")


def _search_form(q: str, docs: list[str], questions: list[dict]) -> str:
    offered = "".join(f"<a href='/search?{urlencode({'qid': x['id']})}' title='{_e(x['question'])}'>{_e(x['id'])}</a>"
                      for x in questions)
    return (f"<form class='ask' action='/search' method='get'><input name='q' value='{_e(q)}' placeholder='问题或关键词'"
            f" autofocus><input name='doc' value='{_e(' '.join(docs))}' placeholder='想追的文件：编号或网址，空白隔开'>"
            "<button>搜</button></form>" + (f"<p class='qs'>测试题：{offered}</p>" if offered else ""))


def _search_body(report: dict, gold: set[str], question: dict | None) -> str:
    u = report["understood"]
    if report["mode"] == "empty":
        return "<div class='card'>这个问题没有可以搜的词（只有标点或空白）。</div>"

    def chips(words: list) -> str:
        return "".join(f"<span class='chip'>{_e(x)}</span>" for x in words)

    read = [f"词 {chips(u['words'])}"]
    if u["terms"]:
        read.append(f"学期 {chips(u['terms'])}")
    if u["cohorts"]:
        read.append(f"入学届 {chips(u['cohorts'])}")
    if u["aliases"]:
        read.append(f"别名 {chips(u['aliases'])}")
    read.append(f"讲什么的词 {chips(u['topic'])}<span class='soft small'>至少要对上一个</span>" if u["topic"]
                else "<span class='soft'>讲什么的词：这次没有要求</span>")
    # the two sentences are the explanation's own (explain.found_words, order_words): one wording for the command and
    # the page; marked when the order is not the usual reranked one, or a model's part is missing
    usual = report["order"] == "reranked" and not report["dense_error"]
    order = _e(order_words(report)) if usual else f"<span class='mark'>{_e(order_words(report))}</span>"
    term = ("<br><span class='soft small'>问题点了学期：这次不加新旧分，也不打「还有效」的折扣</span>"
            if not report["recent"] and report["order"] == "reranked" else "")
    asked = f"<b>{_e(question['id'])}</b>　" if question else ""
    out = [f"<div class='card'>{asked}读成：{'　'.join(read)}<br>{_e(found_words(report))}<br>{order}{term}</div>"]
    if report["follow"]:
        out.append(f"<h2>{'标准答案' if question else '追的文件'}</h2><div class='card'>")
        out.append(TABLE_HEAD)  # one table: each document's sentence, then its row when it was a candidate
        for entry in report["follow"]:
            out.append(f"<tr><td colspan='12'><b>{_e(entry['ref'] or '（空白）')}</b>：{_e(entry['words'])}</td></tr>")
            if entry["row"] is not None:
                out.append(_row_html(entry["row"], gold))
        out.append("</tbody></table></div>")
    if report["rows"]:
        out.append(f"<h2>前 {len(report['rows'])} 名</h2>" + TABLE_HEAD
                   + "".join(_row_html(row, gold) for row in report["rows"]) + "</tbody></table>")
    else:
        out.append("<div class='card'>没有结果。</div>")
    return "".join(out)


def _kv(pairs: list[tuple[str, str]]) -> str:
    """A two-column table of name and (already escaped) value; rows with an empty value are left out."""
    return "<table class='kv'>" + "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k, v in pairs if v) + "</table>"


def _trace_body(report: dict, viewer_url: str) -> str:
    r, c, e, t, lib = report["resolved"], report["crawl"], report["export"], report["totext"], report["library"]
    out = []
    if r["via"] == "alias":
        out.append(f"<div class='card'>这个编号并进了 <b>{_e(r['doc_id'])}</b>（{_e(WHY.get(r['why'], r['why']))}）："
                   f"{_e(merged_words(report))}</div>")
    out.append("<h2>① 抓取</h2><div class='card'>")
    if c:
        number = c["archive_id"][1:] if c["archive_id"][:1] == "u" and c["archive_id"][1:].isdigit() else ""
        link = (f"<a href='{_e(viewer_url.rstrip('/'))}/page?id={number}' target='_blank'>在爬虫检视器看抓下来的原样</a>"
                if viewer_url and number else "")
        out.append(_kv([("抓取时间", _e(when(c["fetched"]))), ("大小", _e(size_words(c["size"]))),
                        ("结果", _e(c["result"]) + (f"（{_e(c['reason'])}）" if c["reason"] else "")),
                        ("缺什么", _e(c["lacks"])), ("怎么找到的", _e(c["how"])), ("链到它的网页", _e(c["linked_from"])),
                        ("存档编号", _e(c["archive_id"]) + ("　" + link if link else ""))]))
    else:
        out.append("<span class='soft'>看不到</span>")
    out.append("</div><h2>② 导出</h2><div class='card'>")
    if e:
        row = e["row"]
        out.append(_kv([("网站", f"{_e(e['site'])}（{_e(e['name'])}）"), ("种类", _e(row["类型"])), ("标题", _e(row["标题"])),
                        ("网址", _e(row["网址"])), ("档案", _e(e["file"])),
                        ("网页上写的日期", _e(row["网页上写的日期"]) + (f"「{_e(row['日期原文'])}」" if row["日期原文"] else "")),
                        ("日期来源", _e(row["日期来源"])), ("最后修改时间", _e(row["最后修改时间"])), ("页别", _e(row["页别"])),
                        ("列表上的类别", _e(row["列表上的类别"])), ("公告单位", _e(row.get("公告单位"))),
                        ("链接文字", _e(row["链接文字"])), ("备注", _e(row["备注"])), ("资料夹", _e(e["folder"]))]))
    else:
        out.append("<span class='soft'>看不到</span>")
    out.append("</div><h2>③ 转文字</h2>")
    if t:
        notes = [_e(how_converted(t))]
        if t["seconds"] is not None:
            notes.append(f"花了 {float(t['seconds']):.2f} 秒")
        if report["record"] and (words := under_read_words(report["record"]["under_read"])):
            notes.append(_e(words))
        out.append(f"<div class='card'>{'　·　'.join(notes)}</div><div class='two'><div class='card'><b>原档</b>")
        if e and e["file"]:
            src = "/file?" + urlencode({"folder": e["folder"], "path": e["file"]})
            beside = f"<a href='{_e(src)}' target='_blank'>在新分页打开</a>"
            if e["file"].lower().endswith(MARKUP):
                out.append(f"<iframe class='orig' sandbox src='{_e(src)}'></iframe>"
                           f"<div class='soft small'>存下来的网页原文，不连网：没有图和版面。{beside}</div>")
            elif e["file"].lower().endswith(".pdf"):  # the browser's own viewer, which a sandboxed frame refuses
                out.append(f"<iframe class='orig' src='{_e(src)}'></iframe><div class='soft small'>{beside}</div>")
            elif e["file"].lower().endswith(IMAGES):
                out.append(f"<div><img class='orig' src='{_e(src)}' alt=''></div><div class='soft small'>{beside}</div>")
            else:
                out.append(f"<p><a href='{_e(src)}' target='_blank'>下载原档</a>"
                           "<span class='soft small'>　这种档浏览器开不了，没办法并排</span></p>")
        out.append(f"</div><div class='card'><b>转出的文字</b>　<span class='soft small'>{t['text_chars']:,} 字</span>"
                   f"<pre>{_e(t['text'])}</pre>")
        if t["full"] is not None:
            out.append(f"<details><summary>整页读法（{t['full_chars']:,} 字）</summary><pre>{_e(t['full'])}</pre></details>")
        out.append("</div></div>")
    else:
        out.append("<div class='card'><span class='soft'>看不到</span></div>")
    out.append("<h2>④ 建库</h2>")
    if lib:
        d = lib["doc"]
        day = (f"<b>{_e(d['ident_date'])}</b>（{_e(DATE_KINDS.get(d['ident_kind'], d['ident_kind']))}）" if d["ident_date"]
               else "没有日期")
        relation = {"publisher": "谁发的", "applies_to": "适用于", "mentions": "提到"}
        scope = "　".join(f"{relation.get(s['relation'], s['relation'])} {_e(s['unit_id'])}"
                         + "".join(f"·{_e(s[k])}" for k in ("campus", "level") if s.get(k)) for s in lib["scope"])
        expires = ""
        if "expires" in d:
            expires = ("会" + (f"（算 {_e(d['valid_ay'])} 学年的）" if d.get("valid_ay") else "")) if d["expires"] else "不会"
        copies = "<br>".join(f"{_e(x['alias'])}（{_e(WHY.get(x['why'], x['why']))}）{_e(x['url'])}" for x in lib["copies"])
        parent = ""
        if lib["parent"]:
            parent = (f"<a href='/trace?{urlencode({'ref': lib['parent']['doc_id']})}'>{_e(lib['parent']['title'])}</a>"
                      f" <span class='soft small'>{_e(lib['parent']['doc_id'])}</span>")
        children = "<br>".join(f"<a href='/trace?{urlencode({'ref': x['doc_id']})}'>{_e(x['title'])}</a>"
                               for x in lib["children"])
        out.append("<div class='card'>" + _kv([
            ("文件编号", f"<b>{_e(d['doc_id'])}</b>"), ("标题", _e(d["title"])),
            ("种类", _e(KINDS.get(d["kind_class"], d["kind_class"])) + f" <span class='soft small'>{_e(d['kind_raw'])}</span>"),
            ("主料还是背景", ("背景" if d.get("tier") == "background" else "主料") + (f"（{_e(d['doc_type'])}）" if d["doc_type"] else "")),
            ("日期", day), ("标题写的学期", _e(f"{d['period_ay']}-{d['period_sem']}" if d["period_sem"] else d["period_ay"] or "")),
            ("单位", _e(d["unit"])), ("组别", _e(d["grp"])), ("和单位的关系", scope),
            ("适用的入学届", _e(cohort_words(lib["applies_to"]))), ("给模型读的单位行", _e(d.get("unit_line"))),
            ("会不会过期", expires), ("排序用的日期", _e(d.get("rank_date"))), ("网址", _e(d["url"])),
            ("挂在哪份底下", parent), (f"底下的附件（{lib['children_total']}）" if lib["children_total"] else "", children),
            ("并进它的副本", copies), ("有没有读到字", "有" if d["has_text"] else "没有（只能靠标题找）"),
            ("文字哪里来的", _e(d["text_source"])), ("字数", f"{d['chars']:,}")]) + "</div>")
        if lib["dates"]:
            out.append("<div class='card'><b>读到的每个日期</b><table><thead><tr><th>哪一种</th><th>日期</th><th>原文</th>"
                       "<th>从哪里读到的</th></tr></thead><tbody>" + "".join(
                           f"<tr><td>{_e(DATE_KINDS.get(x['kind'], x['kind']))}</td><td>{_e(x['date'])}</td>"
                           f"<td>{_e(x['raw'])}</td><td>{_e(x['source'])}</td></tr>" for x in lib["dates"]) + "</tbody></table></div>")
        if lib["refs"]:
            out.append("<div class='card'><b>链到它的网页</b><table><tbody>" + "".join(
                f"<tr><td><a href='/trace?{urlencode({'ref': x['ref_id']})}'>{_e(x['title'] or x['ref_id'])}</a></td>"
                f"<td>{_e(x['zone'])}</td><td>{_e(x['context'])}</td></tr>" for x in lib["refs"]) + "</tbody></table></div>")
        shown = f"（只列前 {len(lib['chunks'])} 段）" if lib["chunks_total"] > len(lib["chunks"]) else ""
        out.append(f"<div class='card'><b>切成 {len(lib['sections'])} 节 {lib['chunks_total']} 小段</b>{shown}")
        for i, chunk in enumerate(lib["chunks"], start=1):
            head = f"　<span class='soft small'>{_e(chunk['heading'])}</span>" if chunk["heading"] else ""
            out.append(f"<details {'open' if i <= 3 else ''}><summary>第 {i} 段 · {chunk['end'] - chunk['start']} 字 · "
                       f"编号 {chunk['chunk_id']}{head}</summary><pre>{_e(chunk['text'])}</pre>"
                       f"<details><summary>模型实际读到的字</summary><pre>{_e(chunk['passage'])}</pre></details></details>")
        out.append("</div>")
    elif report["left_out"]:
        out.append(f"<div class='card'><b class='mark'>没进库</b>：{_e(report['left_out']['words'])}"
                   + (f"<div class='soft small'>建库的分类：{_e(report['left_out']['key'])}</div>" if report["left_out"]["key"] else "")
                   + "</div>")
    else:
        out.append("<div class='card'><span class='mark'>库里找不到这一份。</span></div>")
    out.append("<h2>⑤ 检索</h2><div class='card'>")
    if (s := report["search"]) is not None:
        place = f"<b>第 {s['rank']} 名</b>" if s["rank"] else f"<span class='mark'>前 {s['within']} 名里没有</span>"
        why = f"<a href='/search?{urlencode({'q': s['query'], 'doc': r['doc_id'] or ''})}'>看为什么</a>"
        out.append(f"拿它的标题去搜（{_e(HOW.get(s['how'], s['how']))}）：{place}　{why}")
    else:
        out.append("<span class='soft'>没有搜</span>")
    out.append("</div>")
    if report["missing"]:
        out.append("<div class='card'><b>看不到的</b><ul>" + "".join(f"<li>{_e(m)}</li>" for m in report["missing"]) + "</ul></div>")
    return "".join(out)


# ---------------------------------------------------------------- the app

def create_app(db_path: Path, root: Path, *, viewer_url: str = "", questions: Path | None = None,
               gold: Path | None = None, config: HybridConfig | None = None, models: dict | None = None,
               today_fn: Callable[[], date] = today_tpe, code_root: Path | None = None,
               pipeline_src: Path | None = None) -> Starlette:
    """The app over one library file. root: the folder its relative paths start from; viewer_url: where the owner's
    browser reaches the archive viewer ('' : no link); questions and gold: load_questions; config: the search's
    settings (None: the server's); models: embedder and reranker in place of the process's shared ones (tests);
    code_root and pipeline_src: the tree this code runs from (default: the running package's) and the pipeline's
    source (default: root / src), compared by install_state."""
    db_path, root = Path(db_path), Path(root)
    code_root = Path(code_root) if code_root is not None else REPO_ROOT
    pipeline_src = Path(pipeline_src) if pipeline_src is not None else root / "src"
    checked: dict = {"at": 0.0, "state": None}

    def installed() -> dict | None:
        """install_state, compared again at most every STATE_SECONDS."""
        if time.monotonic() - checked["at"] > STATE_SECONDS or not checked["at"]:
            try:
                checked["state"] = install_state(code_root, pipeline_src)
            except OSError:
                log.exception("比不了检视工具装的程序和建库用的程序")
                checked["state"] = None
            checked["at"] = time.monotonic()
        return checked["state"]

    models = dict(models or {})
    asked = load_questions(questions, gold)
    by_id = {q["id"]: q for q in asked}
    busy = threading.Lock()  # one search at a time: the two models are shared by every request

    def run_search(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]], **kw):
        return hybrid_search(conn, query, today=today, aliases=aliases, config=config, **models, **kw)

    def db_info(conn: sqlite3.Connection) -> dict:
        meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('built_at', 'schema_version')"))
        return {"built_at": meta.get("built_at"), "schema": meta.get("schema_version"), "install": installed()}

    def asked_for(request: Request) -> tuple[str, list[str], int, dict | None]:
        """(the question, the documents to follow, the limit, the test question when one was picked)."""
        p = request.query_params
        question = by_id.get(p.get("qid", ""))
        docs = [d for value in p.getlist("doc") for d in value.split()]
        asked_limit = p.get("limit", "")  # 「²」 is a digit to str.isdigit and no number to int
        plain = asked_limit.isascii() and asked_limit.isdigit() and len(asked_limit) <= 4
        limit = min(int(asked_limit), MAX_LIMIT) if plain else TOP_N
        if question is not None:
            return question["question"], list(dict.fromkeys([*question["gold"], *docs])), limit, question
        return p.get("q", "").strip(), docs, limit, None

    def explained(conn: sqlite3.Connection, query: str, docs: list[str], limit: int) -> dict:
        with busy:
            return explain_search(conn, query, today=today_fn(), aliases=aliases_of(conn), config=config, limit=limit,
                                  follow=docs, **models)

    def traced(conn: sqlite3.Connection, ref: str) -> dict:
        def own_title(c: sqlite3.Connection, title: str) -> tuple[list[str], str]:
            with busy:
                found = run_search(c, title, today=today_fn(), aliases=aliases_of(c), limit=WITHIN)
            return [h.doc_id for h in found.hits], found.ranking

        return trace(conn, ref, root=root, today=today_fn(), own_title_search=own_title)

    def home(request: Request) -> Response:
        conn = connect(db_path)
        try:
            body = ("<h2>搜索试验台</h2>" + _search_form("", [], asked) + "<h2>逐份追踪</h2><form class='ask' action='/trace'"
                    " method='get'><input name='ref' placeholder='网址或文件编号'><button>追</button></form>")
            return _shell("检视台", body, db_info(conn))
        finally:
            conn.close()

    def search_page(request: Request) -> Response:
        query, docs, limit, question = asked_for(request)
        conn = connect(db_path)
        try:
            typed = [] if question else docs
            body = _search_form(query, typed, asked)
            if query or question:
                report = explained(conn, query, docs, limit)
                gold_ids = {f["doc_id"] for f in report["follow"] if f["doc_id"]} if question else set()
                body += _search_body(report, gold_ids, question)
            return _shell(f"{query} · 搜索试验台" if query else "搜索试验台", body, db_info(conn))
        finally:
            conn.close()

    def search_api(request: Request) -> Response:
        query, docs, limit, _ = asked_for(request)
        if request.query_params.get("explain", "1") == "0":
            with busy:
                return JSONResponse(search_answer(db_path, query, search_fn=run_search, today=today_fn()))
        conn = connect(db_path)
        try:
            return JSONResponse(explained(conn, query, docs, limit))
        finally:
            conn.close()

    def trace_page(request: Request) -> Response:
        ref = request.query_params.get("ref", "").strip()
        conn = connect(db_path)
        try:
            body = (f"<form class='ask' action='/trace' method='get'><input name='ref' value='{_e(ref)}'"
                    " placeholder='网址或文件编号' autofocus><button>追</button></form>")
            if ref:
                body += _trace_body(traced(conn, ref), viewer_url)
            return _shell(f"{ref} · 逐份追踪" if ref else "逐份追踪", body, db_info(conn))
        finally:
            conn.close()

    def trace_api(request: Request) -> Response:
        conn = connect(db_path)
        try:
            return JSONResponse(traced(conn, request.query_params.get("ref", "").strip()))
        finally:
            conn.close()

    def original(request: Request) -> Response:
        """One file of an export folder the library names, and nothing outside it."""
        folder, rel = request.query_params.get("folder", ""), request.query_params.get("path", "")
        conn = connect(db_path)
        try:
            known = set(site_exports(conn).values())
        finally:
            conn.close()
        if not folder or folder not in known or not rel:
            return PlainTextResponse("not found", status_code=404)
        base = (root / folder).resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base):
            return PlainTextResponse("forbidden", status_code=403)
        if not target.is_file():
            return PlainTextResponse("not found", status_code=404)
        data, suffix = target.read_bytes(), target.suffix.lower()
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if suffix in MARKUP:
            kind = "image/svg+xml" if suffix == ".svg" else "text/html"
            try:
                data.decode("utf-8")
                kind += "; charset=utf-8"
            except UnicodeDecodeError:
                pass  # not UTF-8: the browser reads the page's own declaration
            return Response(data, media_type=kind, headers={**headers, "Content-Security-Policy": ARCHIVED_CSP})
        if suffix in TYPES:
            return Response(data, media_type=TYPES[suffix], headers=headers)
        name = quote(target.name)
        return Response(data, media_type="application/octet-stream",
                        headers={**headers, "Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

    def guarded(endpoint: Callable[[Request], Response], api: bool) -> Callable[[Request], Response]:
        """endpoint, whose failure is said in words (as JSON for the commands, as a page for the owner) and logged
        whole: one odd record never leaves the owner with a bare 500."""

        def run(request: Request) -> Response:
            try:
                return endpoint(request)
            except Exception as exc:
                log.exception("检视台出错：%s", request.url)
                words = f"{type(exc).__name__}: {exc}"
                if api:
                    return JSONResponse({"error": words}, status_code=500)
                body = (f"<div class='card'><b class='mark'>出错了</b>：{_e(words)}<div class='soft small'>这是检视台"
                        "自己的错，不是你给的东西有问题；完整的经过在它的纪录里。</div></div>")
                return _shell("出错了", body, {}, status=500)

        return run

    routes = [Route("/", guarded(home, False), methods=["GET"]),
              Route("/search", guarded(search_page, False), methods=["GET"]),
              Route("/trace", guarded(trace_page, False), methods=["GET"]),
              Route("/api/search", guarded(search_api, True), methods=["GET"]),
              Route("/api/trace", guarded(trace_api, True), methods=["GET"]),
              Route("/file", guarded(original, True), methods=["GET"])]
    app = Starlette(routes=routes)
    app.add_middleware(LocalOnly)
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.ops.web", description="检视台：搜索试验台和逐份追踪（只读）")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="内容资料库（默认 research/pipeline_2026-11-02/aca.db）")
    ap.add_argument("--root", type=Path, default=REPO_ROOT,
                    help="库里的相对路径从哪个资料夹算起（实验室电脑是 /work；默认是仓库的根）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="在哪个位址上听：默认 127.0.0.1；实验室的 Docker 容器里用 0.0.0.0，由 Docker 只从主机自己的 "
                         "127.0.0.1 转进来。不管听哪里，只回答用本机名字连进来的请求")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--viewer-url", default="", help="从你的浏览器看爬虫检视器的网址（逐份追踪第一步会链过去）")
    ap.add_argument("--questions", type=Path, default=None, help="测试题的资料夹（research/eval_2026-11-01）")
    ap.add_argument("--gold", type=Path, default=None, help="标准答案的档（默认是测试题资料夹里的 gold_docs.json）")
    add_model_flags(ap)
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到内容资料库 {args.db}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app(args.db, args.root, viewer_url=args.viewer_url, questions=args.questions, gold=args.gold,
                     config=config_from(args))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
