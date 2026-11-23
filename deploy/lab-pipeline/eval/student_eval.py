"""A pinned sample of the question set measured on a library, run by hand. Written 11-22 as build.sh's nightly step 5b
(the owner: a sample every night, its searches under 5 minutes, in place of the 33 questions' steps); the same night
the owner dropped every nightly question measure (a set that counts only the documents it lists marks a copy or the
school's newer version wrong), so no build runs it: the question set is for checking the whole project by hand, and
the night's flags below (run.json, the status's student_eval) are gone with step 5b. The question set is research/eval_2026-11-18 (its
README.md; school text and names, so in no commit: on the lab PC /work/research/eval_2026-11-18; this folder holds the
program only). Until now it was measured by hand through the search service (research/eval_2026-11-18/tools/
measure.py); this measures a pinned sample of it on a library file, so the build runs it every night (build.sh 5b).

The sample (SAMPLE: version, seed, and the ids themselves, pinned): only the half looked at during work (平时); the
sealed half (封存) is never read here — it is for an occasional full run — so every sampled question is listed.
  B  every open question of group B with an answer (43; written from the student's side): two searches each, the
     question as typed ("raw") and the FIRST simulated agent query ("agent"). The yardstick: n and totals are B's.
  A  15 open, standing questions of group A (written from documents), spread over as many sites and units of their
     standard source as there are (choose: a seeded shuffle, then those whose site and unit are both new first): raw.
  C  15 open questions of group C (content that came in after the set was written): every standing one (11), then
     the time-bound ones with the latest expires_on (ties in the seeded order): raw.
  D  not measured: its questions have no right document, so a rank says nothing.
choose() is how the ids were picked; the run measures SAMPLE's ids, and a question set that lacks one of them, or has
it in another group or half or without an answer, is refused (exit 5): a new sample is a new version.

Each search is the service's (nknu.retrieval.server.search_answer with its defaults: nknu.retrieval.hybrid.
hybrid_search, keywords + bge-m3 + bge-reranker, the library's own alias table, today in Taipei, no unit, limit
DEPTH, the most the search tool gives). The agent queries are agent_queries.json's: a model wrote two for each question
from the question and the tool's description alone (simulated, not a real AI's); the first is replayed, no LLM, and
it is text only, so no unit is passed (agent_unit "none"). A rank is the 1-based place of the first right document
among the first DEPTH results; null when none is there.

The right documents are measure.py's: a question's sources of role 标准出处 or 也算对, those the library still holds. A
source is found in this library by address, not only by the id it had in the library the set was written on (ids may
change with a rebuild): its doc_id, or the document a doc_alias row keeps for it; the document at its url, as written
or by nknu.content.ids.location_key (http and https, &amp;, a notice's gi make no second address); the document whose
copies list (meta copies:<kept>, a copy at another address merged by the build) holds its address. As in measure.py,
no other document counts: not offline_eval's exact copies (another document with the same text), not a newer
version. Each question's are written (gold: doc_ids) for the panel, which shows where they stand. As the question set
counts (measure.py), a time-bound question past its expires_on is not counted and is listed in expired, and one whose
sources the library no longer holds is not counted and is listed in flags.not_in_library; flags.standard_gone: measured
on its other right documents, the standard source gone.

The output (version 2; the panel reads exactly this): sample (version, seed, ids_sha256, groups, set: file, revised,
gold_sha256), measured (Taipei local time), seconds, library (path, built: the build's meta built_at, docs), depth,
agent_source, agent_unit, n and totals (B's, raw and agent, each top1/top3/top10), groups (B: n, raw, agent; A and C:
n, raw), questions (id, group, q, raw, agent (B's, else null), prev_raw, prev_agent, gold), expired, flags, prev.

prev: before writing, the file already at --out is read. Only a file of version 2 and of the same sample version is
compared with; another starts a new line (prev null). When it measured another library, its measured, library, n,
totals and groups become prev and each question's ranks prev_raw / prev_agent, so one file says what dropped since last
night. When it measured the same library (the same build time and document count: a library measured twice, under
another path too — a trial promoted to aca.db keeps its build time), its own prev and prev_* are kept, so a library is
never compared with itself.

Models: the process's (nknu.core.models.shared_embedder / shared_reranker, behind offline_eval's per-run caches): the
machine's model service when NKNU_MODELS_URL is set, else a copy loaded here. A query during which either model
failed, or that came back ranked by keywords alone, is a model failure: its question is asked once more; if one still
fails, nothing is written (keyword-only ranks would read as a drop) and the exit code is 4.

    python deploy/lab-pipeline/eval/student_eval.py run --db research/pipeline_2026-11-02/aca.db \
        --questions research/eval_2026-11-18 --out research/pipeline_2026-11-02/student_eval.json [--today 2026-11-22]

Exit codes: 0 written; 2 an input is missing or unreadable, or --out is in a folder never written; 4 a model failed;
5 the question set does not hold the pinned sample. Nothing is written but on 0.
Tests: uv run --extra pipeline python -m pytest deploy/lab-pipeline/eval/student_eval.py -q
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from offline_eval import OUT_REFUSED, MemoEmbedder, MemoReranker, connect_ro

from nknu.content.build import COPIES_KEY, SKELETON, _protected
from nknu.content.ids import location_key
from nknu.core.timeutil import TAIPEI, today_tpe

VERSION = 2
DEPTH = 50  # the most the search tool returns (server.MAX_LIMIT); measure.py's DEPTH
CUTS = (1, 3, 10)
GOLD_ROLES = ("标准出处", "也算对")  # measure.py's right documents
OPEN, ANSWERED, STANDING = "平时", "有答案", "常设"
QUESTIONS, AGENT = "questions.json", "agent_queries.json"
AGENT_SOURCE = "simulated (agent_queries.json), first query"  # a model wrote them from the question and tool text
AGENT_UNIT = "none"  # the simulated queries are text only: the replay passes no unit
GROUPS = ("B", "A", "C")  # B first: the yardstick
SIZES = {"A": 15, "C": 15}  # B: all of its open answered questions
IN_MAX = 500  # values per IN (...)
# choose(questions of the set's third version, revised 2026-11-22, SEED), pinned. A new sample is a new version: the
# nights before and after it are then two lines, never compared.
SEED = 20261122
SAMPLE = {
    "version": "s1-20261122", "seed": SEED,
    "B": ["B001", "B006", "B007", "B008", "B010", "B011", "B013", "B014", "B015", "B017", "B020", "B023", "B024",
          "B025", "B028", "B029", "B031", "B036", "B037", "B046", "B050", "B052", "B055", "B056", "B057", "B061",
          "B063", "B064", "B065", "B068", "B072", "B073", "B075", "B076", "B078", "B081", "B084", "B085", "B087",
          "B088", "B089", "B090", "B091"],
    "A": ["A009", "A010", "A015", "A026", "A027", "A040", "A044", "A045", "A051", "A069", "A072", "A081", "A086",
          "A091", "A100"],
    "C": ["C002", "C004", "C022", "C025", "C027", "C028", "C030", "C034", "C035", "C036", "C040", "C045", "C050",
          "C051", "C053"],
}


class ModelShort(Exception):
    """A model failed during a search, or the search came back ranked by keywords alone."""


# ---------------------------------------------------------------- the sample and its right documents


def group_of(q: dict) -> str:
    return str(q.get("path", ""))[:1]


def gold_sources(q: dict) -> list[dict]:
    """The question's right documents as the set lists them, the standard source first."""
    return [s for s in q.get("sources") or [] if s.get("role") in GOLD_ROLES]


def choose(questions: list[dict], seed: int) -> dict[str, list[str]]:
    """How SAMPLE's ids were picked (the module docstring), from the open half's answered questions only."""
    rng = random.Random(seed)
    pool = sorted((q for q in questions if q.get("half") == OPEN and q.get("verdict") == ANSWERED),
                  key=lambda q: q["id"])
    in_group = {g: [q for q in pool if group_of(q) == g] for g in GROUPS}
    standing = [q for q in in_group["A"] if q.get("standing") == STANDING and not q.get("expires_on")]
    rng.shuffle(standing)
    picked: list[dict] = []
    sites: set[str] = set()
    units: set[str] = set()
    for rule in ("both", "site", "unit", "any"):
        for q in standing:
            src = (gold_sources(q) or [{}])[0]
            site, unit = src.get("site") or "", src.get("unit") or ""
            new = {"both": site not in sites and unit not in units, "site": site not in sites,
                   "unit": unit not in units, "any": True}[rule]
            if new and q not in picked and len(picked) < SIZES["A"]:
                picked.append(q)
                sites.add(site)
                units.add(unit)
    timed = [q for q in in_group["C"] if q.get("standing") != STANDING or q.get("expires_on")]
    rng.shuffle(timed)
    timed.sort(key=lambda q: q.get("expires_on") or "", reverse=True)  # stable: ties keep the seeded order
    kept = [q for q in in_group["C"] if q not in timed]
    c = kept + timed[:max(SIZES["C"] - len(kept), 0)]
    return {"B": [q["id"] for q in in_group["B"]], "A": sorted(q["id"] for q in picked),
            "C": sorted(q["id"] for q in c[:SIZES["C"]])}


def _sha(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def sampled(questions: list[dict], sample: dict) -> tuple[list[dict], list[str]]:
    """(the sample's questions in GROUPS order, what is wrong): each pinned id must be in the set, open, answered and in
    its group. Only the open half is ever looked at."""
    by_id = {q["id"]: q for q in questions if q.get("half") == OPEN and q.get("id")}
    out, wrong = [], []
    for g in GROUPS:
        for qid in sample[g]:
            q = by_id.get(qid)
            if q is None or q.get("verdict") != ANSWERED or group_of(q) != g:
                wrong.append(qid)
            else:
                out.append(q)
    return out, wrong


def sample_of(meta: dict, qs: list[dict], sample: dict, source: str) -> dict:
    gold = [f"{q['id']}\t{s.get('doc_id') or ''}\t{s.get('url') or ''}" for q in qs for s in gold_sources(q)]
    return {"version": sample["version"], "seed": sample["seed"],
            "ids_sha256": _sha(sorted(q["id"] for q in qs)),
            "groups": {g: len(sample[g]) for g in GROUPS},
            "set": {"file": source, "revised": meta.get("revised"), "gold_sha256": _sha(sorted(gold))}}


def _in(conn: sqlite3.Connection, sql: str, values: list[str]) -> list[tuple]:
    """sql with one IN ({marks}) over values, a slice at a time."""
    rows: list[tuple] = []
    for k in range(0, len(values), IN_MAX):
        part = values[k:k + IN_MAX]
        rows += conn.execute(sql.format(marks=",".join("?" * len(part))), part).fetchall()
    return rows


def resolve(conn: sqlite3.Connection, sources: list[dict]) -> list[set[str]]:
    """Per source, the documents of this library that are it (the module docstring): by id or doc_alias, by url or
    location_key, through a copies list. An empty set: the library no longer holds it."""
    ids = sorted({s["doc_id"] for s in sources if s.get("doc_id")})
    urls = sorted({s["url"] for s in sources if s.get("url")})
    held = {r[0] for r in _in(conn, "SELECT doc_id FROM docs WHERE doc_id IN ({marks})", ids)}
    kept: dict[str, str] = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'doc_alias'").fetchone():
        kept = dict(_in(conn, "SELECT alias, doc_id FROM doc_alias WHERE alias IN ({marks})", ids))
    at: dict[str, set[str]] = {}
    for doc_id, url in _in(conn, "SELECT doc_id, url FROM docs WHERE url IN ({marks})", urls):
        at.setdefault(url, set()).add(doc_id)
    keys = {location_key(u) for u in urls}
    by_key: dict[str, set[str]] = {}
    for doc_id, url in conn.execute("SELECT doc_id, url FROM docs"):
        if (key := location_key(url or "")) in keys:
            by_key.setdefault(key, set()).add(doc_id)
    for key, value in conn.execute("SELECT key, value FROM meta WHERE key LIKE ?", (COPIES_KEY + "%",)):
        for c in json.loads(value or "[]"):
            if isinstance(c, dict) and c.get("url") and (k := location_key(c["url"])) in keys:
                by_key.setdefault(k, set()).add(key.removeprefix(COPIES_KEY))
    out = []
    for s in sources:
        doc_id, url = s.get("doc_id") or "", s.get("url") or ""
        found = ({doc_id} if doc_id in held else set()) | ({kept[doc_id]} if doc_id in kept else set())
        if url:
            found |= at.get(url, set()) | by_key.get(location_key(url), set())
        out.append(found)
    return out


def rank_of(hits: list[str], gold: set[str], depth: int = DEPTH) -> int | None:
    """1-based place of the first right document among the first depth hits; None when none is there."""
    return next((i for i, d in enumerate(hits[:depth], 1) if d in gold), None)


# ---------------------------------------------------------------- the search, as the service runs it


class _Watched:
    """A model behind a count of the calls it failed; the failure still goes on to hybrid, which ranks without it."""

    def __init__(self, inner) -> None:
        self._inner, self.failures = inner, 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def _call(self, name: str, *args, **kwargs):
        try:
            return getattr(self._inner, name)(*args, **kwargs)
        except Exception:
            self.failures += 1
            raise

    def encode(self, *args, **kwargs):
        return self._call("encode", *args, **kwargs)

    def score(self, *args, **kwargs):
        return self._call("score", *args, **kwargs)


class Searcher:
    """text -> the doc_ids of the first depth results, as nknu.retrieval.server.search_answer gets them with its
    defaults (no unit, sort by relevance): hybrid_search with the library's aliases and HybridConfig's defaults. Raises
    ModelShort when a model failed during the call or the order is the keywords' alone."""

    def __init__(self, conn: sqlite3.Connection, *, today: date, depth: int = DEPTH, embedder=None,
                 reranker=None) -> None:
        from nknu.retrieval.hybrid import hybrid_search

        row = conn.execute("SELECT value FROM meta WHERE key = 'aliases_json'").fetchone()  # server.aliases_of
        self._search, self.conn, self.today, self.depth = hybrid_search, conn, today, depth
        self.aliases = json.loads(row[0]) if row else []
        self.embedder = _Watched(embedder or MemoEmbedder())
        self.reranker = _Watched(reranker or MemoReranker())

    def __call__(self, text: str) -> list[str]:
        before = self.embedder.failures + self.reranker.failures
        result = self._search(self.conn, text, today=self.today, aliases=self.aliases, limit=self.depth,
                              embedder=self.embedder, reranker=self.reranker)
        if self.embedder.failures + self.reranker.failures > before or (
                result.candidates and result.ranking != "hybrid"):
            raise ModelShort
        return [h.doc_id for h in result.hits]


# ---------------------------------------------------------------- measuring


@dataclass
class Measured:
    rows: list[dict] = field(default_factory=list)  # {id, group, q, raw, agent, gold, standard_gone}, counted
    expired: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # every right document left the library
    failed: list[str] = field(default_factory=list)  # a model failed on them twice


def measure(conn: sqlite3.Connection, qs: list[dict], agent: dict[str, list[str]],
            search: Callable[[str], list[str]], *, today: date, depth: int = DEPTH) -> Measured:
    """Each sampled question's ranks: raw for all, agent (the first simulated query) for B."""
    out = Measured()
    found = resolve(conn, [s for q in qs for s in gold_sources(q)])
    k = 0
    for q in qs:
        mine = found[k:k + len(gold_sources(q))]
        k += len(mine)
        gold = set().union(*mine) if mine else set()
        if q.get("expires_on") and q["expires_on"] < today.isoformat():
            out.expired.append(q["id"])
            continue
        if not gold:
            out.gone.append(q["id"])
            continue
        first = (agent.get(q["id"]) or [None])[0] if group_of(q) == "B" else None
        texts = [q["question"]] + ([first] if first else [])
        ranks = None
        for _ in (1, 2):
            try:
                ranks = [rank_of(search(t), gold, depth) for t in texts]
                break
            except ModelShort:
                pass
        if ranks is None:
            out.failed.append(q["id"])
            continue
        out.rows.append({"id": q["id"], "group": group_of(q), "q": q["question"], "raw": ranks[0],
                         "agent": ranks[1] if first else None, "gold": sorted(gold),
                         "standard_gone": not mine[0]})
    return out


def counts(ranks: list[int | None]) -> dict[str, int]:
    return {f"top{c}": sum(1 for r in ranks if r is not None and r <= c) for c in CUTS}


def group_totals(rows: list[dict], g: str) -> dict:
    mine = [r for r in rows if r["group"] == g]
    got = {"n": len(mine), "raw": counts([r["raw"] for r in mine])}
    if g == "B":
        got["agent"] = counts([r["agent"] for r in mine])
    return got


# ---------------------------------------------------------------- the file


def library_of(conn: sqlite3.Connection, db: Path) -> dict:
    """path as given; built: the build's meta built_at (the file's time when it has none); docs."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
    built = row[0] if row and row[0] else datetime.fromtimestamp(Path(db).stat().st_mtime, TAIPEI).isoformat(
        timespec="seconds")
    return {"path": str(db), "built": built, "docs": conn.execute("SELECT count(*) FROM docs").fetchone()[0]}


def same_library(a: dict | None, b: dict | None) -> bool:
    """The same build: its build time and document count (the path may differ: the module docstring)."""
    return bool(a and b and a.get("built") and a.get("built") == b.get("built") and a.get("docs") == b.get("docs"))


def read_old(out: Path, sample_version: str) -> dict | None:
    """The file a run wrote before, when it is of this version and this sample; else None (said on stderr)."""
    if not out.is_file():
        return None
    try:
        old = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"注意：读不了上一份结果 {out}（{type(exc).__name__}），这次不比上一次", file=sys.stderr)
        return None
    if not isinstance(old, dict) or old.get("version") != VERSION:
        print(f"上一份结果 {out} 不是第 {VERSION} 版的格式：这次起是新的一条线，不比上一次", file=sys.stderr)
        return None
    if (old.get("sample") or {}).get("version") != sample_version:
        print(f"上一份结果量的是另一个样本（{(old.get('sample') or {}).get('version')}）：这次起是新的一条线，不比上一次",
              file=sys.stderr)
        return None
    return old


def carry(old: dict | None, library: dict) -> tuple[dict | None, dict[str, tuple]]:
    """(prev, {id: (prev_raw, prev_agent)}) from the file written before (the module docstring)."""
    if not old:
        return None, {}
    listed = [x for x in old.get("questions") or [] if isinstance(x, dict) and x.get("id")]
    if same_library(old.get("library"), library):
        return old.get("prev"), {x["id"]: (x.get("prev_raw"), x.get("prev_agent")) for x in listed}
    prev = {key: old.get(key) for key in ("measured", "library", "n", "totals", "groups")}
    return prev, {x["id"]: (x.get("raw"), x.get("agent")) for x in listed}


def report(m: Measured, *, sample: dict, library: dict, measured: str, seconds: float, depth: int,
           old: dict | None) -> dict:
    prev, before = carry(old, library)
    groups = {g: group_totals(m.rows, g) for g in GROUPS}
    return {
        "version": VERSION, "sample": sample, "measured": measured, "seconds": seconds, "library": library,
        "depth": depth, "agent_source": AGENT_SOURCE, "agent_unit": AGENT_UNIT,
        "n": groups["B"]["n"], "totals": {"raw": groups["B"]["raw"], "agent": groups["B"]["agent"]},
        "groups": groups,
        "questions": [{"id": r["id"], "group": r["group"], "q": r["q"], "raw": r["raw"], "agent": r["agent"],
                       "prev_raw": before.get(r["id"], (None, None))[0],
                       "prev_agent": before.get(r["id"], (None, None))[1], "gold": r["gold"]} for r in m.rows],
        "expired": m.expired,
        "flags": {"not_in_library": m.gone, "standard_gone": [r["id"] for r in m.rows if r["standard_gone"]]},
        "prev": prev}


def _worse(now: int | None, then: int | None) -> bool:
    return then is not None and (now is None or now > then)


def summary(rep: dict) -> list[str]:
    """The lines for stdout, Simplified Chinese."""
    def cells(t: dict) -> str:
        return " / ".join(str(t[f"top{c}"]) for c in CUTS)

    lib, s, g = rep["library"], rep["sample"], rep["groups"]
    then = (rep.get("prev") or {}).get("groups") or {}
    lines = [(f"每晚抽样 {s['version']}（只抽平时那一半：B {s['groups']['B']}、A {s['groups']['A']}、C "
              f"{s['groups']['C']} 题），库 {lib['path']}（{lib['built']} 建，{lib['docs']:,} 份文件），"
              f"名次查到前 {rep['depth']} 名。每格是进前 1 ／ 前 3 ／ 前 10 名的题数："),
             f"  B 学生原话：{cells(g['B']['raw'])}" + (f"（上一份库：{cells(then['B']['raw'])}）" if then else ""),
             f"  B 模拟 AI 的第一条查询：{cells(g['B']['agent'])}"
             + (f"（上一份库：{cells(then['B']['agent'])}）" if then else "")]
    for name in ("A", "C"):
        lines.append(f"  {name} 原话（{g[name]['n']} 题）：{cells(g[name]['raw'])}"
                     + (f"（上一份库：{cells(then[name]['raw'])}）" if then.get(name) else ""))
    lines.append(f"  B 算进去的 {g['B']['n']} 题。")
    if rep.get("prev"):
        down = [x["id"] for x in rep["questions"] if _worse(x["raw"], x["prev_raw"]) or _worse(x["agent"], x["prev_agent"])]
        lines.append(f"比上一份库名次变差的：{len(down)} 题" + (f"（{'、'.join(down)}）" if down else "")
                     + f"；上一份量于 {rep['prev'].get('measured')}。")
    else:
        lines.append("没有同一个样本的上一份结果可比：这次起是新的一条线。")
    f = rep["flags"]
    lines.append(f"不算进去的：过了到期日 {len(rep['expired'])} 题" + (f"（{'、'.join(rep['expired'])}）" if rep["expired"] else "")
                 + f"；出处都不在库里 {len(f['not_in_library'])} 题" + (
                     f"（{'、'.join(f['not_in_library'])}）" if f["not_in_library"] else "")
                 + f"。标准出处不在、靠「也算对」量的 {len(f['standard_gone'])} 题。")
    return lines


def write(out: Path, rep: dict) -> None:
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, out)


def run(db: Path, questions: Path, out: Path, *, today: date, depth: int = DEPTH, sample: dict = SAMPLE,
        search: Callable[[str], list[str]] | None = None, now: Callable[[], datetime] | None = None) -> int:
    """The whole run (the module docstring); search None: Searcher on db. The exit code."""
    t0 = time.monotonic()
    for path, what in ((db, "资料库"), (questions / QUESTIONS, "题库"), (questions / AGENT, "agent 查询")):
        if not path.is_file():
            print(f"找不到{what}：{path}", file=sys.stderr)
            return 2
    if not out.parent.is_dir():
        print(f"--out 的资料夹不在：{out.parent}", file=sys.stderr)
        return 2
    try:
        data = json.loads((questions / QUESTIONS).read_text(encoding="utf-8"))
        qs, wrong = sampled(data["questions"], sample)
        meta = data.get("meta") or {}
        agent = json.loads((questions / AGENT).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"题库读不了（{questions}）：{type(exc).__name__}", file=sys.stderr)
        return 2
    if wrong:
        print(f"题库里没有样本 {sample['version']} 要的题，或不在原来那一组、那一半、不是有答案的：{'、'.join(wrong)}。"
              f"每晚要量同一个样本；题库换了就出新的样本版本（改 SAMPLE）。{out} 保持原样。", file=sys.stderr)
        return 5
    conn = connect_ro(db)
    try:
        library = library_of(conn, db)
        m = measure(conn, qs, agent, search or Searcher(conn, today=today, depth=depth), today=today, depth=depth)
    finally:
        conn.close()
    if m.failed:
        print(f"模型没回应或排序退回关键词：{len(m.failed)} 题问了两次都没量成（{'、'.join(m.failed)}）。"
              f"这次不写结果，{out} 保持原样。", file=sys.stderr)
        return 4
    stamp = (now() if now else datetime.now(TAIPEI)).strftime("%Y-%m-%dT%H:%M:%S")
    rep = report(m, sample=sample_of(meta, qs, sample, str(questions / QUESTIONS)), library=library, measured=stamp,
                 seconds=round(time.monotonic() - t0, 1), depth=depth, old=read_old(out, sample["version"]))
    write(out, rep)
    print("\n".join(summary(rep)))
    print(f"写入 {out}（用时 {rep['seconds']} 秒）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="student_eval.py",
                                 description="每晚抽样量题库（平时那一半的 B、A、C）在一份库上排第几：学生原话，"
                                             "B 另加模拟 AI 的第一条查询（不调用语言模型，资料库只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="量一份库，写 JSON（面板读的就是这份）")
    r.add_argument("--db", type=Path, required=True, help="资料库（只读），例如 research/pipeline_2026-11-02/aca.db")
    r.add_argument("--questions", type=Path, required=True,
                   help=f"题库资料夹（不进 git），里面要有 {QUESTIONS} 和 {AGENT}，例如 research/eval_2026-11-18")
    r.add_argument("--out", type=Path, required=True, help="输出 JSON；已经有同一个样本的，先读它当「上一次」")
    r.add_argument("--today", type=date.fromisoformat, default=None, help="哪一天（默认台北今天）：到期日和排序都看它")
    r.add_argument("--depth", type=int, default=DEPTH, help=f"名次查到前几名（默认 {DEPTH}，搜索工具的上限）")
    args = ap.parse_args(argv)
    if _protected(args.out, SKELETON):
        ap.error(OUT_REFUSED.format(out=args.out))
    return run(args.db, args.questions, args.out, today=args.today or today_tpe(), depth=args.depth)


if __name__ == "__main__":
    raise SystemExit(main())


# ---- tests ------------------------------------------------------------------------------------------------------

SEALED_ID, SEALED_TEXT = "B902", "封存那題的原話"


def _library(path: Path, built: str = "2026-11-22T03:01:46+08:00", docs: int = 60, alias: bool = True,
             copies: dict | None = None) -> Path:
    """A library with docs d1..d<docs> at https://x.test/<n> (only the columns this program reads)."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE docs (doc_id TEXT PRIMARY KEY, url TEXT, title TEXT)")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO docs VALUES (?, ?, ?)", [(f"d{n}", f"https://x.test/{n}", f"t{n}")
                                                           for n in range(1, docs + 1)])
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [("built_at", built), ("aliases_json", "[]")]
                     + [(COPIES_KEY + k, json.dumps(v)) for k, v in (copies or {}).items()])
    if alias:
        conn.execute("CREATE TABLE doc_alias (alias TEXT PRIMARY KEY, doc_id TEXT, why TEXT)")
        conn.execute("INSERT INTO doc_alias VALUES ('old7', 'd7', 'copy')")
    conn.commit()
    conn.close()
    return path


PATHS = {"A": "A 从文件出题", "B": "B 从学生出发", "C": "C 新进库的内容", "D": "D 不该答的题"}


def _q(qid: str, text: str, *gold: tuple[str, str], half: str = OPEN, expires: str | None = None, **kw) -> dict:
    sources = [{"role": "标准出处" if i == 0 else "也算对", "doc_id": d, "url": u, "site": f"s-{d}", "unit": f"u-{d}"}
               for i, (d, u) in enumerate(gold)]
    return {"id": qid, "half": half, "question": text, "path": PATHS[qid[0]], "verdict": ANSWERED,
            "standing": "时效" if expires else STANDING, "expires_on": expires, "sources": sources, **kw}


def _d(n: int) -> tuple[str, str]:
    return f"d{n}", f"https://x.test/{n}"


def _questions() -> list[dict]:
    return [_q("B001", "開放題一", _d(1)), _q("B002", "開放題二", _d(2), _d(3)),
            _q(SEALED_ID, SEALED_TEXT, _d(4), half="封存"),
            _q("A001", "A 題一", _d(5)), _q("C001", "C 題一", _d(6)),
            _q("D001", "D 題", _d(7)) | {"verdict": "库里没有"}]


TEST_SAMPLE = {"version": "t1", "seed": 7, "B": ["B001", "B002"], "A": ["A001"], "C": ["C001"]}
TABLE = {"開放題一": ["d9", "d1"], "開放題二": ["d3"], SEALED_TEXT: ["d4"], "A 題一": ["d8", "d8", "d5"],
         "C 題一": [], "agent 一": ["d1"], "agent 二": ["d8"], "agent 封": ["d4"]}
AGENTS = {"B001": ["agent 一", "agent 二"], "B002": ["agent 二", "agent 一"], SEALED_ID: ["agent 封"]}
DAY = date(2026, 11, 22)


def _set(tmp: Path, questions: list[dict]) -> Path:
    folder = tmp / "set"
    folder.mkdir(exist_ok=True)
    data = {"meta": {"revised": "2026-11-22"}, "questions": questions}
    (folder / QUESTIONS).write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    (folder / AGENT).write_text(json.dumps(AGENTS, ensure_ascii=False), "utf-8")
    return folder


def _run(tmp: Path, db: Path, *, table=TABLE, questions=None, sample=TEST_SAMPLE, now=None,
         asked: list | None = None) -> tuple[int, dict | None]:
    def search(text):
        if asked is not None:
            asked.append(text)
        return table.get(text, [])

    out = tmp / "student_eval.json"
    code = run(db, _set(tmp, _questions() if questions is None else questions), out, today=DAY, sample=sample,
               search=search, now=now or (lambda: datetime(2026, 11, 22, 21, 40)))
    return code, json.loads(out.read_text("utf-8")) if out.is_file() else None


def test_the_version_2_shape(tmp_path):
    code, rep = _run(tmp_path, _library(tmp_path / "aca.db"))
    assert code == 0
    assert list(rep) == ["version", "sample", "measured", "seconds", "library", "depth", "agent_source", "agent_unit",
                         "n", "totals", "groups", "questions", "expired", "flags", "prev"]
    assert rep["version"] == 2 and rep["measured"] == "2026-11-22T21:40:00" and isinstance(rep["seconds"], float)
    assert rep["sample"]["version"] == "t1" and rep["sample"]["seed"] == 7
    assert rep["sample"]["groups"] == {"B": 2, "A": 1, "C": 1} and rep["sample"]["set"]["revised"] == "2026-11-22"
    assert rep["sample"]["ids_sha256"] == _sha(["A001", "B001", "B002", "C001"])
    assert rep["library"] == {"path": str(tmp_path / "aca.db"), "built": "2026-11-22T03:01:46+08:00", "docs": 60}
    assert rep["agent_source"] == AGENT_SOURCE and rep["agent_unit"] == "none" and rep["depth"] == DEPTH
    assert rep["questions"] == [
        {"id": "B001", "group": "B", "q": "開放題一", "raw": 2, "agent": 1, "prev_raw": None, "prev_agent": None,
         "gold": ["d1"]},
        {"id": "B002", "group": "B", "q": "開放題二", "raw": 1, "agent": None, "prev_raw": None, "prev_agent": None,
         "gold": ["d2", "d3"]},
        {"id": "A001", "group": "A", "q": "A 題一", "raw": 3, "agent": None, "prev_raw": None, "prev_agent": None,
         "gold": ["d5"]},
        {"id": "C001", "group": "C", "q": "C 題一", "raw": None, "agent": None, "prev_raw": None, "prev_agent": None,
         "gold": ["d6"]}]
    assert rep["n"] == 2 and rep["totals"] == {"raw": {"top1": 1, "top3": 2, "top10": 2},
                                               "agent": {"top1": 1, "top3": 1, "top10": 1}}
    assert rep["groups"]["A"] == {"n": 1, "raw": {"top1": 0, "top3": 1, "top10": 1}}
    assert rep["groups"]["C"] == {"n": 1, "raw": {"top1": 0, "top3": 0, "top10": 0}}
    assert rep["expired"] == [] and rep["flags"] == {"not_in_library": [], "standard_gone": []} and rep["prev"] is None


def test_only_the_first_agent_query_and_only_for_b(tmp_path):
    asked: list[str] = []
    _, rep = _run(tmp_path, _library(tmp_path / "aca.db"), asked=asked)
    assert asked == ["開放題一", "agent 一", "開放題二", "agent 二", "A 題一", "C 題一"]
    assert rep["questions"][1]["agent"] is None  # B002's first query finds d8 only; its second would find d1


def test_the_sealed_half_is_never_touched(tmp_path, capsys):
    asked: list[str] = []
    db = _library(tmp_path / "aca.db")
    code, _ = _run(tmp_path, db, asked=asked)
    assert code == 0 and SEALED_TEXT not in asked and "agent 封" not in asked
    said = capsys.readouterr()
    for text in ((tmp_path / "student_eval.json").read_text("utf-8"), said.out, said.err):
        assert SEALED_ID not in text and SEALED_TEXT not in text
    code, _ = _run(tmp_path, db, sample=TEST_SAMPLE | {"B": ["B001", SEALED_ID]})
    assert code == 5  # a pinned id in the sealed half is refused, never measured


def test_a_set_without_the_pinned_sample_is_refused(tmp_path):
    db = _library(tmp_path / "aca.db")
    out = tmp_path / "student_eval.json"
    for qs in ([q for q in _questions() if q["id"] != "A001"],                          # gone
               [q | {"path": PATHS["C"]} if q["id"] == "A001" else q for q in _questions()],  # another group
               [q | {"verdict": "只有相关"} if q["id"] == "C001" else q for q in _questions()]):  # no answer now
        assert _run(tmp_path, db, questions=qs)[0] == 5 and not out.exists()


def test_expired_questions_are_listed_not_counted(tmp_path):
    qs = _questions() + [_q("B010", "過期", _d(1), expires="2026-11-21"), _q("B012", "今天到期", _d(1),
                                                                            expires="2026-11-22")]
    sample = TEST_SAMPLE | {"B": ["B001", "B002", "B010", "B012"]}
    _, rep = _run(tmp_path, _library(tmp_path / "aca.db"), questions=qs, sample=sample,
                  table=TABLE | {"過期": ["d1"], "今天到期": ["d1"]})
    assert rep["expired"] == ["B010"] and rep["n"] == 3
    assert [x["id"] for x in rep["questions"] if x["group"] == "B"] == ["B001", "B002", "B012"]


def test_vanished_and_standard_gone_are_flagged(tmp_path):
    qs = _questions() + [_q("B011", "沒了", ("gone", "https://x.test/gone")),
                         _q("B013", "開放題二", ("gone", "https://x.test/gone"), _d(3))]
    sample = TEST_SAMPLE | {"B": ["B001", "B011", "B013"]}
    _, rep = _run(tmp_path, _library(tmp_path / "aca.db"), questions=qs, sample=sample)
    assert rep["flags"] == {"not_in_library": ["B011"], "standard_gone": ["B013"]} and rep["n"] == 2
    assert rep["questions"][1] == {"id": "B013", "group": "B", "q": "開放題二", "raw": 1, "agent": None,
                                   "prev_raw": None, "prev_agent": None, "gold": ["d3"]}


def test_a_model_failure_writes_nothing(tmp_path, capsys):
    db = _library(tmp_path / "aca.db")
    out = tmp_path / "student_eval.json"
    out.write_text('{"version": 2, "kept": true}', "utf-8")

    def search(text):
        if text == "A 題一":
            raise ModelShort
        return TABLE.get(text, [])

    assert run(db, _set(tmp_path, _questions()), out, today=DAY, sample=TEST_SAMPLE, search=search) == 4
    assert json.loads(out.read_text("utf-8")) == {"version": 2, "kept": True} and "A001" in capsys.readouterr().err


def test_a_model_failure_is_asked_again_once(tmp_path):
    calls: list[str] = []

    def search(text):
        calls.append(text)
        if text == "開放題一" and calls.count(text) == 1:
            raise ModelShort
        return TABLE.get(text, [])

    out = tmp_path / "student_eval.json"
    assert run(_library(tmp_path / "aca.db"), _set(tmp_path, _questions()), out, today=DAY, sample=TEST_SAMPLE,
               search=search) == 0
    assert calls.count("開放題一") == 2 and json.loads(out.read_text("utf-8"))["questions"][0]["raw"] == 2


def test_prev_is_carried_from_another_library_of_the_same_sample(tmp_path):
    _run(tmp_path, _library(tmp_path / "old.db", built="2026-11-21T03:00:00+08:00"))
    worse = TABLE | {"開放題一": ["d9", "d8", "d7", "d1"]}
    code, rep = _run(tmp_path, _library(tmp_path / "aca.db"), table=worse, now=lambda: datetime(2026, 11, 23, 5))
    assert code == 0 and rep["prev"]["measured"] == "2026-11-22T21:40:00"
    assert rep["prev"]["library"]["built"] == "2026-11-21T03:00:00+08:00"
    assert rep["prev"]["totals"]["raw"] == {"top1": 1, "top3": 2, "top10": 2} and rep["prev"]["n"] == 2
    assert rep["prev"]["groups"]["A"]["raw"]["top3"] == 1
    assert [(x["raw"], x["prev_raw"], x["agent"], x["prev_agent"]) for x in rep["questions"][:1]] == [(4, 2, 1, 1)]


def test_another_sample_version_starts_a_new_line(tmp_path):
    _run(tmp_path, _library(tmp_path / "old.db", built="2026-11-21T03:00:00+08:00"))
    db = _library(tmp_path / "aca.db")
    _, rep = _run(tmp_path, db, sample=TEST_SAMPLE | {"version": "t2"})
    assert rep["prev"] is None and all(x["prev_raw"] is None for x in rep["questions"])
    out = tmp_path / "student_eval.json"
    out.write_text(json.dumps({"version": 1, "n": 85}), "utf-8")  # the first night's shape
    _, rep = _run(tmp_path, db)
    assert rep["prev"] is None


def test_the_same_library_keeps_its_prev(tmp_path):
    _run(tmp_path, _library(tmp_path / "old.db", built="2026-11-21T03:00:00+08:00"))
    db = _library(tmp_path / "aca.db")
    _, first = _run(tmp_path, db)
    _, again = _run(tmp_path, db, now=lambda: datetime(2026, 11, 22, 22, 10))
    assert again["measured"] == "2026-11-22T22:10:00" and again["prev"] == first["prev"]
    assert again["prev"]["library"]["built"] == "2026-11-21T03:00:00+08:00"
    assert [(x["prev_raw"], x["prev_agent"]) for x in again["questions"]] == [(2, 1), (1, None), (3, None),
                                                                             (None, None)]


def test_a_library_under_another_path_is_the_same_library():
    a = {"path": "research/x/trial.db", "built": "2026-11-22T03:01:46+08:00", "docs": 9}
    assert same_library(a, a | {"path": "/work/research/x/aca.db"})
    assert not same_library(a, a | {"docs": 10}) and not same_library(a, a | {"built": "2026-11-23T03:00:00+08:00"})
    assert not same_library(None, a) and not same_library(a | {"built": None}, a | {"built": None})


def test_choose_is_reproducible_open_only_and_spread(tmp_path):
    qs = [_q(f"A{n:03d}", f"a{n}", (f"x{n % 5}", f"https://x.test/a{n}")) for n in range(1, 31)]
    qs += [_q(f"A{n:03d}", f"a{n}", _d(n), half="封存") for n in range(31, 41)]
    qs += [_q(f"C{n:03d}", f"c{n}", _d(n)) for n in range(1, 4)]
    qs += [_q(f"C{n:03d}", f"c{n}", _d(n), expires=f"2027-0{n % 9 + 1}-01") for n in range(4, 30)]
    qs += [_q("B001", "b", _d(1)), _q("B002", "b", _d(2), half="封存")]
    got = choose(qs, 5)
    assert got == choose(list(reversed(qs)), 5) and got != choose(qs, 6)
    assert got["B"] == ["B001"] and all(int(i[1:]) <= 30 for i in got["A"]) and len(got["A"]) == 15
    first = [q for q in qs if q["id"] in got["A"]]
    assert len({q["sources"][0]["site"] for q in first}) == 5  # every site the pool has
    assert {"C001", "C002", "C003"} <= set(got["C"]) and len(got["C"]) == 15
    timed = sorted((q["expires_on"] for q in qs if q["id"].startswith("C") and q["expires_on"]), reverse=True)
    assert min(q["expires_on"] for q in qs if q["id"] in got["C"] and q["expires_on"]) >= timed[11]


def test_the_pinned_sample_is_choose_on_the_third_version():
    here = next((d for d in Path(__file__).resolve().parents if (d / "research").is_dir()), None)
    path = here / "research" / "eval_2026-11-18" / QUESTIONS if here else None
    if path is None or not path.is_file():  # the set is in no commit: only where it is
        return
    got = choose(json.loads(path.read_text(encoding="utf-8"))["questions"], SAMPLE["seed"])
    assert got == {g: SAMPLE[g] for g in GROUPS}
    assert {g: len(got[g]) for g in GROUPS} == {"B": 43, "A": 15, "C": 15}


def test_rank_is_null_past_the_depth():
    hits = [f"x{n}" for n in range(DEPTH)] + ["d1"]
    assert rank_of(hits, {"d1"}) is None
    assert rank_of(hits, {"d1"}, depth=DEPTH + 1) == DEPTH + 1
    assert rank_of(["d2", "d1"], {"d1", "d2"}) == 1


def test_gold_is_found_by_address(tmp_path):
    conn = sqlite3.connect(_library(tmp_path / "aca.db", copies={"d8": [{"url": "https://y.test/copy"}]}))
    got = resolve(conn, [{"doc_id": "dX", "url": "http://x.test/3"},       # another id, the same address
                         {"doc_id": "old7", "url": "https://z.test/old"},  # an alias row keeps d7 for it
                         {"doc_id": "dY", "url": "https://y.test/copy"},   # merged into d8 as a copy
                         {"doc_id": "d1", "url": "https://x.test/1"},
                         {"doc_id": "gone", "url": "https://x.test/gone"}])
    assert got == [{"d3"}, {"d7"}, {"d8"}, {"d1"}, set()]


def test_searcher_raises_when_a_model_failed_or_the_order_is_keywords_alone(tmp_path):
    from nknu.retrieval.types import DocHit, SearchResult, Understood

    class Model:
        model_id, revision = "m", "r"

        def encode(self, texts, **kw):
            raise RuntimeError("down")

        def score(self, query, passages, **kw):
            return [0.0] * len(passages)

    def fake(conn, text, *, today, aliases, limit, embedder, reranker):
        if text == "fails":
            try:
                embedder.encode([text])
            except RuntimeError:
                pass
        hits = [DocHit("d1", 1.0, 1.0, 0.0, [])]
        return SearchResult(text, Understood([], [], [], [], []), hits, 1, ranking="keyword" if text == "kw" else
                            "hybrid")

    conn = sqlite3.connect(_library(tmp_path / "aca.db"))
    s = Searcher(conn, today=DAY, embedder=Model(), reranker=Model())
    s._search = fake
    assert s("fine") == ["d1"]
    for text in ("fails", "kw"):
        try:
            s(text)
        except ModelShort:
            continue
        raise AssertionError(text)


def test_main_refuses_an_out_file_in_the_skeleton_folder(tmp_path):
    try:
        main(["run", "--db", str(tmp_path / "a.db"), "--questions", str(tmp_path), "--out",
              str(SKELETON / "student_eval.json")])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("not refused")


def test_missing_inputs_exit_2(tmp_path):
    out = tmp_path / "student_eval.json"
    assert run(tmp_path / "none.db", tmp_path, out, today=DAY, sample=TEST_SAMPLE) == 2
    assert run(_library(tmp_path / "aca.db"), tmp_path / "noset", out, today=DAY, sample=TEST_SAMPLE) == 2
    assert not out.exists()
