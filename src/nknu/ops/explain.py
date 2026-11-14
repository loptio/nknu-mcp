"""Why a search ranked as it did (plan 2026-11-15 T2; spec 2026-11-15 section 二.3). explain_search runs the real search
(nknu.retrieval.hybrid.hybrid_search) with its recorder on and shapes what it wrote: how the question was read, how
many candidates each path brought, one row per result with every part of its score, and, for each document the owner
asks about, where it stopped. Nothing is scored here: every number is the search's own (tests/ops/test_recorder.py pins
that they add up to the scores), so what the owner sees is what a student's AI got.

A reranked document's score (hybrid.py, step 6):
    (w_rerank x relevance + period + cohort + date [+ w_first x place]) x valid x scope + unit bonus
对题 is the relevance, 还有效 the valid factor, 对口 the scope factor (direction 2026-11-13 第一节). A document the
reranker did not read keeps its keyword score: coverage, bm25, period, cohort and date, times the scope factor."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from datetime import date

from nknu.ops.lookup import Resolved, columns, has_table, resolve
from nknu.retrieval.hybrid import HybridConfig, hybrid_search
from nknu.retrieval.search import TOP_N, W_BM25, W_COHORT, W_COVERAGE, W_PERIOD, RankConfig

_IN_MAX = 500  # ids per IN (...) query
WHY = {"copy": "另一个网址的同一份", "format": "同一份的另一种格式", "duplicate": "重复的纪录"}  # doc_alias.why
TIERS = ("主料", "背景")
NOT_FOUND, NO_CANDIDATE, NOT_IN_HEAD, RANKED, IN_TOP = "not_found", "no_candidate", "not_in_head", "ranked", "in_top"


def _rows(conn: sqlite3.Connection, sql: str, ids: list[str]) -> Iterator[tuple]:
    """sql, whose {} is the IN list, over ids, _IN_MAX at a time."""
    for i in range(0, len(ids), _IN_MAX):
        part = ids[i : i + _IN_MAX]
        yield from conn.execute(sql.format(",".join("?" * len(part))), part)


def _period(ay: int | None, sem: int | None) -> str:
    if ay is None:
        return ""
    return f"{ay}-{sem}" if sem else f"{ay}學年度"


def about_docs(conn: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
    """doc_id -> what a row says of the document itself: title, url, kind, unit, date, site, period, whether it goes
    out of date and of which academic year (None in a database built before plan 2026-11-14) and who published it
    ([] without doc_scope)."""
    ids = list(dict.fromkeys(ids))
    have = columns(conn, "docs")
    site = "site" if "site" in have else "''"
    expires = "expires" if "expires" in have else "NULL"
    valid_ay = "valid_ay" if "valid_ay" in have else "NULL"
    sql = (f"SELECT doc_id, title, url, kind_class, unit, ident_date, {site}, {expires}, {valid_ay}, period_ay,"
           " period_sem FROM docs WHERE doc_id IN ({})")
    out = {r[0]: {"title": r[1], "url": r[2], "kind": r[3], "unit": r[4], "date": r[5], "site": r[6], "expires": r[7],
                  "valid_ay": r[8], "period": _period(r[9], r[10]), "publisher": []} for r in _rows(conn, sql, ids)}
    if has_table(conn, "doc_scope"):
        for doc_id, unit_id in _rows(conn, "SELECT doc_id, unit_id FROM doc_scope WHERE relation = 'publisher'"
                                           " AND doc_id IN ({}) ORDER BY unit_id", ids):
            if doc_id in out and unit_id:
                out[doc_id]["publisher"].append(unit_id)
    return out


def doc_tokens(conn: sqlite3.Connection, doc_id: str) -> tuple[set[str], set[str]]:
    """(the tokens of the document's own words, those of its unit words) over all its chunks, as the keyword index
    holds them (chunk_fts; no unit column in a database built before pipeline-5)."""
    unit = ", unit_tokens" if "unit_tokens" in columns(conn, "chunk_fts") else ""
    own: set[str] = set()
    units: set[str] = set()
    for row in conn.execute(f"SELECT title_tokens, body_tokens{unit} FROM chunk_fts WHERE rowid IN"
                            " (SELECT chunk_id FROM chunks WHERE doc_id = ?)", (doc_id,)):
        own.update((row[0] or "").split(), (row[1] or "").split())
        if unit:
            units.update((row[2] or "").split())
    return own, units


class _View:
    """One search's record (hybrid_search's explain dict), read by document."""

    def __init__(self, raw: dict, config: HybridConfig, about: dict[str, dict]) -> None:
        self.raw, self.config, self.about = raw, config, about
        self.place = {d: i for i, d in enumerate(raw["final"], start=1)}
        self.background = set(raw["background"])
        self.fused = {d: i for tier in raw["tiers"] for i, d in enumerate(tier, start=1)}
        self.reranked = (raw["rerank"] or {}).get("docs", {})
        # the head is what the reranker read: none when no rerank ran, whatever the fused order's first documents were
        self.heads = [set(h) for h in raw["heads"]] if raw["rerank"] is not None else [set(), set()]
        sims = {c: s for c, s in raw["dense"]}
        self.sim = {d: max((sims[c] for c in cs if c in sims), default=0.0) for d, cs in raw["dense_docs"].items()}
        # each path's place within the document's tier, as step 5 fuses them (review 11-15: the keyword place was
        # over both tiers, so a background paper could read 「关键词第 1 名」 beside a primary document's 2/1/1)
        self.kw_place: dict[str, int] = {}
        self.dense_place: dict[str, int] = {}
        for back in (False, True):
            self.kw_place.update({d: i for i, d in enumerate(
                (d for d in raw["kw_order"] if (d in self.background) == back), start=1)})
            self.dense_place.update({d: i for i, d in enumerate(
                (d for d in raw["dense_docs"] if (d in self.background) == back), start=1)})
        self.valid_applies = bool(raw["rerank"] and raw["rerank"]["valid_applies"])
        # what shaped the order and whether the vectors took part: the words of the report follow these two
        self.order = "reranked" if raw["rerank"] is not None else "fused" if raw["ranking"] == "hybrid" else "keyword"
        self.vectors = ("failed" if raw.get("dense_error") else
                        "used" if raw["mode"] == "hybrid" and config.dense else "off")

    def score(self, doc_id: str) -> float:
        """The document's final score, as hybrid_search left it: the reranked one; else its keyword score after the
        audience factor and the unit bonus; else, for one only the vectors found, its unit bonus."""
        if doc_id in self.reranked:
            return self.reranked[doc_id]["score"]
        return self.raw["kw_scores"].get(doc_id, self.raw["bonus"].get(doc_id, 0.0))

    def row(self, doc_id: str) -> dict:
        raw, config = self.raw, self.config
        back = doc_id in self.background
        found = raw["keyword"]["docs"].get(doc_id)
        keyword = None
        if found is not None:
            keyword = {"rank": self.kw_place.get(doc_id), "score": raw["kw_scores"].get(doc_id, found["score"]),
                       "coverage": found["coverage"], "bm25": found["bm25"], "bm25_norm": found["bm25_norm"],
                       "period": found["period"], "cohort": found["cohort"], "date": found["date"],
                       "scope": config.scope_factor if doc_id in raw["off"] else 1.0}
        dense = {"rank": self.dense_place[doc_id], "sim": self.sim[doc_id]} if doc_id in self.dense_place else None
        rerank = None
        if (r := self.reranked.get(doc_id)) is not None:
            rerank = {"rel": r["rel"], "weighted": config.w_rerank * r["norm"], "period": r["parts"]["period"],
                      "cohort": r["parts"]["cohort"], "date": r["parts"]["date"],
                      "first": None if r["first"] is None else config.w_first * r["first"],
                      "valid": r["valid"] if r["valid"] is not None else (1.0 if self.valid_applies else None),
                      "scope": config.scope_factor if r["off"] else 1.0, "bonus": r["bonus"],
                      "chunks": sorted(([int(c), s] for c, s in r["chunks"].items()), key=lambda cs: (-cs[1], cs[0]))}
        return {"rank": self.place.get(doc_id), "doc_id": doc_id, **self.about.get(doc_id, {}), "background": back,
                "keyword": keyword, "dense": dense, "fused": self.fused.get(doc_id),
                "in_head": doc_id in self.heads[1 if back else 0], "rerank": rerank, "score": self.score(doc_id)}


def _follow(conn: sqlite3.Connection, res: Resolved, view: _View, limit: int) -> dict:
    """Where one followed document stopped, in words."""
    raw, config = view.raw, view.config
    entry = {"ref": res.ref, "doc_id": res.doc_id, "via": res.via, "why": res.why, "verdict": NOT_FOUND, "words": "",
             "row": None}
    if res.doc_id is None:
        entry["words"] = "库里没有这一份：文件编号、并掉的编号、网址都对不上。"
        return entry
    merged = f"这个编号并进了 {res.doc_id}（{WHY.get(res.why, res.why)}），下面看的是留下来的那一份。" if res.via == "alias" else ""
    place = view.place.get(res.doc_id)
    if place is None:
        entry["verdict"] = NO_CANDIDATE
        if raw["mode"] == "empty":
            entry["words"] = merged + "这个问题没有可以搜的词，所以没有任何候选。"
            return entry
        own, units = doc_tokens(conn, res.doc_id)
        topic, tokens = raw["keyword"]["topic"], raw["keyword"]["tokens"]
        entry["token_hit"] = any(t in own or t in units for t in tokens)
        entry["topic_hit"] = any(t in own for t in topic) if topic else None
        by_vectors = {"used": "向量也没有把它排进候选。", "failed": "这次向量没跑成，只有关键词在找。",
                      "off": "这次没有用向量。"}[view.vectors]
        if not entry["token_hit"]:
            words = "它的小段里没有这个问题的任何一个词，关键词找不到它；" + by_vectors
        elif topic and not entry["topic_hit"]:
            words = (f"它只对上了单位名或学期这类词，没有对上讲什么的词（{'、'.join(topic)}），所以没进候选；"
                     + by_vectors)
        else:
            words = "它对得上词，但没排进候选：关键词每个问题只留最好的那些份。" + by_vectors
        entry["words"] = merged + words
        return entry
    row = view.row(res.doc_id)
    entry["row"] = row
    tier = 1 if row["background"] else 0
    where = []
    if row["keyword"]:
        where.append(f"关键词第 {row['keyword']['rank']} 名")
    if row["dense"]:
        where.append(f"向量第 {row['dense']['rank']} 名")
    if row["fused"] is not None:  # no fused order when no model took part (the keyword order alone)
        where.append(f"合起来在{TIERS[tier]}里第 {row['fused']} 名")
    if place <= limit:
        entry["verdict"] = IN_TOP
        entry["words"] = merged + f"在前 {limit} 名里：第 {place} 名（{'，'.join(where)}）。"
    elif row["rerank"] is None and raw["rerank"] is not None:
        head = config.rerank_background if tier else config.rerank_docs
        entry["verdict"] = NOT_IN_HEAD
        entry["words"] = merged + (f"进了候选，没进重排名额：{'，'.join(where)}，重排只读{TIERS[tier]}的前 {head} 份。"
                                   f"最后第 {place} 名。")
    else:
        entry["verdict"] = RANKED
        how = f"重排分 {row['rerank']['rel']:.2f}，" if row["rerank"] else "这次没有重排，"
        entry["words"] = merged + f"{'，'.join(where)}；{how}最后第 {place} 名（这次只看前 {limit} 名）。"
    return entry


def explain_search(conn: sqlite3.Connection, query: str, *, today: date, aliases: list[list[str]],
                   config: HybridConfig | None = None, rank: RankConfig | None = None, limit: int = TOP_N,
                   follow: Iterable[str] = (), embedder=None, reranker=None, index=None) -> dict:
    """The search of query as hybrid_search runs it (same arguments), explained: the module docstring. follow: document
    ids, merged copies' ids or addresses to say of each where it stopped. The dict is JSON."""
    config = config if config is not None else HybridConfig()
    rank = rank if rank is not None else RankConfig()
    limit = max(limit, 0)
    raw: dict = {}
    result = hybrid_search(conn, query, today=today, aliases=aliases, limit=limit, rank=rank, config=config,
                           embedder=embedder, reranker=reranker, index=index, explain=raw)
    resolved = [resolve(conn, ref) for ref in follow]
    shown = raw["final"][:limit]
    view = _View(raw, config, about_docs(conn, [*shown, *(r.doc_id for r in resolved if r.doc_id)]))
    dense_back = sum(1 for d in raw["dense_docs"] if d in view.background)
    understood = result.understood
    return {
        "query": result.query,
        "understood": {"words": understood.tokens, "canonical": understood.canonical, "aliases": understood.aliases,
                       "terms": understood.terms, "cohorts": understood.cohorts, "topic": raw["keyword"]["topic"],
                       "tokens": raw["keyword"]["tokens"]},
        "recent": raw["keyword"]["recent"],
        "model_text": raw["text"],
        "mode": raw["mode"],
        "ranking": raw["ranking"],
        "order": view.order,  # reranked | fused (keyword and vector orders together) | keyword
        "vectors": view.vectors,  # used | failed | off
        "dense_error": raw.get("dense_error"),
        "rerank_error": raw.get("rerank_error"),
        "valid_applies": view.valid_applies,
        "counts": {"keyword": len(raw["kw_order"]), "dense": [len(raw["dense_docs"]) - dense_back, dense_back],
                   "fused": [len(t) for t in raw["tiers"]], "head": [len(h) for h in view.heads],
                   "final": len(raw["final"])},
        "weights": {"coverage": W_COVERAGE, "bm25": W_BM25, "period": W_PERIOD, "cohort": W_COHORT,
                    "rerank": config.w_rerank, "first": config.w_first, "valid_decay": config.valid_decay,
                    "scope_factor": config.scope_factor, "rerank_docs": config.rerank_docs,
                    "rerank_background": config.rerank_background, "topic_required": rank.topic_required},
        "limit": limit,
        "rows": [view.row(d) for d in shown],
        "follow": [_follow(conn, res, view, limit) for res in resolved],
    }


# ---------------------------------------------------------------- as text (the command's output)

def _plus(value: float | None) -> str:
    return "  —  " if not value else f"{value:+.2f}"


def _times(value: float | None) -> str:
    return "  —  " if value is None else f"×{value:.2f}"


def _line(row: dict) -> str:
    """One document: its place, its score and the parts of it."""
    rank = f"{row['rank']:>3}" if row["rank"] else "  —"
    kw = row["keyword"]["rank"] if row["keyword"] else "—"
    dense = row["dense"]["rank"] if row["dense"] else "—"
    places = f"{kw}/{dense}/{row['fused'] or '—'}"
    tier = "【背景】" if row["background"] else ""
    tail = f"{places:<11} {row.get('date') or '无日期':<10}  {tier}{row.get('title', '')}  [{row['doc_id']}]"
    if (r := row["rerank"]) is not None:
        first = f" 第一关{_plus(r['first'])}" if r["first"] is not None else ""
        return (f"{rank}  {row['score']:6.2f}  {r['rel']:.2f}→{r['weighted']:.2f}  {_plus(r['period'])}  "
                f"{_plus(r['cohort'])}  {_plus(r['date'])}  {_times(r['valid'])}  {_times(r['scope'])}  "
                f"{_plus(r['bonus'])}{first}  {tail}")
    if (k := row["keyword"]) is not None:
        return (f"{rank}  {row['score']:6.2f}  （未重排）涵盖 {k['coverage']:.2f} 关键词分 {k['bm25_norm']:.2f} "
                f"学期 {k['period']:.1f} 新旧 {_plus(k['date'])} 对口 {_times(k['scope'])}  {tail}")
    return f"{rank}  {row['score']:6.2f}  （未重排，只有向量找到）  {tail}"


def found_words(report: dict) -> str:
    """How many candidates each path brought, saying of each path what really happened to it."""
    c = report["counts"]
    parts = [f"关键词 {c['keyword']} 份"]
    if report["vectors"] == "used":
        parts.append(f"向量 主料 {c['dense'][0]}＋背景 {c['dense'][1]} 份")
    else:
        parts.append("向量没跑成" if report["vectors"] == "failed" else "没有用向量")
    if report["order"] == "reranked":
        parts.append(f"进重排 主料 {c['head'][0]}＋背景 {c['head'][1]} 份")
    return "候选：" + "　".join(parts) + f"　共 {c['final']} 份"


def order_words(report: dict) -> str:
    """What the order of the results is, by what ran: the formula only when a rerank shaped it (review 11-15: the
    formula was printed over a keyword order, and a failed vector search was not said)."""
    w = report["weights"]
    failed = f"向量没跑成（{report['dense_error']}）" if report["dense_error"] else ""
    if report["order"] == "reranked":
        formula = (f"排序：总分＝(对题×{w['rerank']:g}＋学期＋届别＋新旧) × 还有效 × 对口 ＋ 单位。"
                   f"还有效：每过一个学年 ×{w['valid_decay']:g}；对口：系所自己的文件、问题没点名系所时 ×{w['scope_factor']:g}。")
        return formula + (f"{failed}，候选只有关键词找到的。" if failed else "")
    if report["mode"] == "keyword":
        return "排序：只有关键词（没有用向量和重排模型）。"
    rerank = f"重排没跑成（{report['rerank_error']}）" if report["rerank_error"] else "没有重排"
    if report["order"] == "fused":
        return f"排序：{rerank}，用的是关键词和向量合起来的顺序。"
    return f"排序：{rerank}，{failed or '向量没有找到东西'}：用的是关键词的顺序。"


def explain_text(report: dict) -> str:
    """The report as the command prints it, in Simplified Chinese."""
    u = report["understood"]
    out = [f"问题：{report['query']}"]
    if report["mode"] == "empty":
        out.append("这个问题没有可以搜的词（只有标点或空白），没有结果。")
    else:
        def listed(words: list) -> str:
            return "、".join(str(x) for x in words)

        read = [f"词 {listed(u['words'])}"]
        if u["terms"]:
            read.append(f"学期 {listed(u['terms'])}")
        if u["cohorts"]:
            read.append(f"入学届 {listed(u['cohorts'])}")
        if u["aliases"]:
            read.append(f"别名 {listed(u['aliases'])}")
        read.append(f"讲什么的词 {listed(u['topic'])}（至少要对上一个）" if u["topic"] else "讲什么的词：这次没有要求")
        out.append("读成：" + "　".join(read))
        if report["model_text"]:
            out.append(f"给模型读的字：{report['model_text']}")
        out.append(found_words(report))
        out.append(order_words(report))
        if not report["recent"] and report["order"] == "reranked":
            out.append("问题点了学期，所以这次不加新旧分，也不打「还有效」的折扣。")
    if report["rows"]:
        out += ["", "名次   总分   对题(重排)   学期    届别    新旧    还有效   对口    单位    关键词/向量/合  日期        标题"]
        out += [_line(row) for row in report["rows"]]
    elif report["mode"] != "empty":
        out += ["", "没有结果。"]
    if report["follow"]:
        out += ["", "追的文件："]
        for entry in report["follow"]:
            out.append(f"- {entry['ref'] or '（空白）'}：{entry['words']}")
            if entry["row"] is not None:
                out.append("  " + _line(entry["row"]))
    return "\n".join(out)
