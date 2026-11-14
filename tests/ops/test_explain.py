"""plan 2026-11-15 T2: why a search ranked as it did. explain_search runs the real search with the recorder and shapes
what it wrote: one row per result with every part of its score, and for each followed document where it stopped
(not in the library, no candidate, outside the reranked head, or its final place)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from nknu.ops.explain import explain_search, explain_text
from nknu.ops.lookup import resolve
from nknu.retrieval.hybrid import HybridConfig
from tests.ops.library import SETTINGS, models, search
from tests.retrieval.test_hybrid import FakeEmbedder
from tests.retrieval.test_search import TODAY


def _explain(db, query, setting="the owner's defaults", config=None, **kw):
    base, rank = SETTINGS[setting]
    return explain_search(db.conn, query, today=TODAY, aliases=[], config=config or base, rank=rank, **models(), **kw)


@pytest.mark.parametrize("setting", SETTINGS)
@pytest.mark.parametrize("query", ["停修", "115-1 停修", "化學系 停修", "退選"])
def test_the_rows_are_the_searchs_results_in_its_order_with_its_scores(db, query, setting):
    report = _explain(db, query, setting)
    hits = search(db, query, setting).hits
    assert [(r["rank"], r["doc_id"], r["score"]) for r in report["rows"]] == [
        (i, h.doc_id, h.score) for i, h in enumerate(hits, start=1)]
    assert report["ranking"] == search(db, query, setting).ranking


def test_a_row_has_every_step_of_its_document(db):
    report = _explain(db, "停修")
    rows = {r["doc_id"]: r for r in report["rows"]}
    d3 = rows["d3"]
    assert d3["title"] == "115學年度第一學期線上申請停修" and d3["url"] == "https://example.test/d3"
    assert d3["date"] == "2026-10-21" and d3["background"] is False
    assert d3["keyword"]["rank"] >= 1 and d3["keyword"]["coverage"] == 1.0
    assert d3["dense"]["rank"] >= 1 and 0 < d3["dense"]["sim"] <= 1.0001
    assert d3["fused"] >= 1 and d3["in_head"] is True
    assert d3["rerank"]["rel"] == 0.9 and d3["rerank"]["weighted"] == pytest.approx(2.7)
    assert d3["rerank"]["valid"] == 1.0 and d3["rerank"]["scope"] == 1.0
    assert rows["dq"]["keyword"] is None and rows["dq"]["dense"]["rank"] >= 1  # the vectors alone found it
    assert rows["j1"]["background"] is True
    assert rows["dept"]["rerank"]["scope"] == 0.9 and rows["dept"]["publisher"] == ["化學系"]
    assert rows["d1"]["rerank"]["valid"] < 1.0 and rows["d1"]["valid_ay"] == 114 and rows["d1"]["expires"] == 1


def test_the_header_says_how_the_question_was_read_and_what_was_found(db):
    report = _explain(db, "115-1 停修")
    assert report["query"] == "115-1 停修" and report["mode"] == "hybrid"
    assert report["understood"]["words"] == ["停修"] and report["understood"]["terms"] == ["115-1"]
    assert report["understood"]["topic"] == ["停修"]
    assert report["recent"] is False and report["valid_applies"] is False  # a term is named: no date part, no decay
    assert report["model_text"] and report["counts"]["keyword"] == 6 and report["counts"]["head"] == [6, 1]
    assert report["weights"]["rerank"] == 3.0 and report["weights"]["valid_decay"] == 0.85
    assert report["weights"]["scope_factor"] == 0.9 and report["weights"]["rerank_docs"] == 50


def test_a_followed_document_in_the_results(db):
    report = _explain(db, "停修", follow=["d3"])
    entry = report["follow"][0]
    assert entry["doc_id"] == "d3" and entry["via"] == "id" and entry["verdict"] == "in_top"
    assert entry["row"]["rank"] == 1 and "第 1 名" in entry["words"]


def test_a_followed_document_outside_the_reranked_head(db):
    config = replace(SETTINGS["the owner's defaults"][0], rerank_docs=2)
    report = _explain(db, "停修", config=config, limit=2, follow=["dx"])
    entry = report["follow"][0]
    assert entry["verdict"] == "not_in_head" and entry["row"]["in_head"] is False and entry["row"]["rerank"] is None
    assert entry["row"]["fused"] > 2 and "没进重排" in entry["words"] and "2 份" in entry["words"]


def test_a_followed_document_reranked_but_below_the_results(db):
    report = _explain(db, "停修", limit=2, follow=["dx"])
    entry = report["follow"][0]
    assert entry["verdict"] == "ranked" and entry["row"]["rank"] > 2 and entry["row"]["rerank"] is not None
    assert f"第 {entry['row']['rank']} 名" in entry["words"]


def test_a_followed_document_with_none_of_the_questions_words_is_no_candidate(db):
    db.doc("lib", "圖書館開放時間", "週一至週五上午八時開館")
    config = HybridConfig(dense=False)
    entry = _explain(db, "停修", config=config, follow=["lib"])["follow"][0]
    assert entry["verdict"] == "no_candidate" and entry["row"] is None
    assert entry["token_hit"] is False and "没有这个问题的任何一个词" in entry["words"]


def test_a_followed_document_with_the_units_name_alone_has_no_topic_word(db):
    db.doc("crest", "化學系系徽設計比賽", "化學系系徽徵件")
    db.scope("crest", ["化學系"], publisher="化學系")
    entry = _explain(db, "化學系 停修", config=HybridConfig(dense=False), follow=["crest"])["follow"][0]
    assert entry["verdict"] == "no_candidate" and entry["token_hit"] is True and entry["topic_hit"] is False
    assert "讲什么的词" in entry["words"] and "停修" in entry["words"]


def test_a_merged_copys_id_follows_the_document_kept_for_it(db):
    db.conn.execute("INSERT INTO doc_alias(alias, doc_id, why) VALUES ('dcopy', 'd3', 'copy')")
    entry = _explain(db, "停修", follow=["dcopy"])["follow"][0]
    assert entry["doc_id"] == "d3" and entry["via"] == "alias" and entry["why"] == "copy"
    assert entry["verdict"] == "in_top" and "并进了 d3" in entry["words"]


def test_a_followed_address_finds_its_document(db):
    entry = _explain(db, "停修", follow=["https://example.test/d2"])["follow"][0]
    assert entry["doc_id"] == "d2" and entry["via"] == "url"


@pytest.mark.parametrize("ref", ["nothing-like-this", "https://example.test/gone?x=1#frag", "", "  "])
def test_a_reference_the_library_does_not_have_is_said_in_words(db, ref):
    entry = _explain(db, "停修", follow=[ref])["follow"][0]
    assert entry["verdict"] == "not_found" and entry["doc_id"] is None and entry["row"] is None
    assert "库里没有" in entry["words"]


@pytest.mark.parametrize("query", ["!!!", "", "   "])
def test_nothing_to_search_for_gives_no_rows(db, query):
    report = _explain(db, query, follow=["d3"])
    assert report["rows"] == [] and report["mode"] == "empty"
    assert report["follow"][0]["verdict"] == "no_candidate"
    assert "没有可以搜的词" in explain_text(report)


def test_no_match_gives_no_rows(db):
    report = _explain(db, "宿舍", config=HybridConfig(dense=False))
    assert report["rows"] == [] and report["counts"]["keyword"] == 0


def test_without_models_it_is_the_keyword_order_and_no_row_was_reranked(db):
    report = _explain(db, "停修", "keywords only")
    assert report["mode"] == "keyword" and report["ranking"] == "keyword" and report["rows"]
    assert all(r["rerank"] is None and r["dense"] is None for r in report["rows"])
    assert "只有关键词" in explain_text(report)


def test_a_reranker_that_cannot_be_used_is_said(db):
    class Missing:
        model_id, revision = "fake/rerank", "0" * 40

        def score(self, query, passages, *, batch_size=16):
            raise RuntimeError("no such model")

    config, rank = SETTINGS["the owner's defaults"]
    report = explain_search(db.conn, "停修", today=TODAY, aliases=[], config=config, rank=rank,
                            embedder=models()["embedder"], reranker=Missing())
    assert report["rerank_error"] and all(r["rerank"] is None for r in report["rows"])
    assert "no such model" in explain_text(report)


def test_the_text_names_the_three_factors_and_has_a_line_per_row(db):
    report = _explain(db, "停修", follow=["dx", "nothing-like-this"])
    text = explain_text(report)
    for word in ("对题", "还有效", "对口", "读成", "候选"):
        assert word in text
    for row in report["rows"]:
        assert row["title"] in text
    assert "追的文件" in text and "库里没有" in text
    assert "['" not in text and "词 停修" in text  # words as the owner reads them, not as a program prints a list


def test_the_report_is_json(db):
    report = _explain(db, "115-1 停修", follow=["d3", "nothing"])
    assert json.loads(json.dumps(report, ensure_ascii=False))["rows"][0]["doc_id"] == report["rows"][0]["doc_id"]


def test_a_database_built_before_pipeline_8_still_explains(db):
    # the older columns first: dropped as the table's last column, one whose comment holds a comma breaks the stored
    # statement (schema.DDL's note)
    for column in ("tier", "site", "unit_line", "date_line", "expires", "valid_ay", "rank_date", "rank_undated"):
        db.conn.execute(f"ALTER TABLE docs DROP COLUMN {column}")
    db.conn.executescript("DROP TABLE sites; DROP TABLE doc_alias;")
    report = _explain(db, "停修", follow=["d3", "dcopy"])
    assert report["rows"] and report["follow"][0]["verdict"] == "in_top"
    assert report["follow"][1]["verdict"] == "not_found"
    assert report["rows"][0]["expires"] is None and report["rows"][0]["site"] == ""


def test_resolve_by_id_alias_address_and_the_id_the_address_gives(db):
    from nknu.content.ids import location_key, record_id

    assert resolve(db.conn, "d3").via == "id"
    assert resolve(db.conn, " d3 ").doc_id == "d3"
    db.conn.execute("INSERT INTO doc_alias(alias, doc_id, why) VALUES ('dold', 'd2', 'format')")
    assert (resolve(db.conn, "dold").doc_id, resolve(db.conn, "dold").via, resolve(db.conn, "dold").why) == (
        "d2", "alias", "format")
    assert resolve(db.conn, "https://example.test/d1").via == "url"
    keyed = "d" + record_id(location_key("https://school.example/page.aspx?id=7"))
    db.doc(keyed, "一份網頁", "內容")
    db.conn.execute("UPDATE docs SET url = 'https://school.example/page.aspx?id=7&amp;x=' WHERE doc_id = ?", (keyed,))
    assert resolve(db.conn, "https://school.example/page.aspx?id=7").via == "key"
    assert resolve(db.conn, "https://school.example/other").doc_id is None


def test_the_settings_can_be_given(db):
    off = _explain(db, "停修", config=HybridConfig(valid_decay=1.0, scope_factor=1.0))
    rows = {r["doc_id"]: r for r in off["rows"]}
    assert rows["d1"]["rerank"]["valid"] is None and rows["dept"]["rerank"]["scope"] == 1.0


class NoMemory(FakeEmbedder):
    """An embedder that fails on every text."""

    def encode(self, texts, *, batch_size=16):
        raise RuntimeError("no memory")


class NoModel:
    """A reranker that cannot be used."""

    model_id, revision = "fake/rerank", "0" * 40

    def score(self, query, passages, *, batch_size=16):
        raise RuntimeError("no such model")


def _with(db, query, embedder, reranker, **kw):
    config, rank = SETTINGS["the owner's defaults"]
    return explain_search(db.conn, query, today=TODAY, aliases=[], config=config, rank=rank, embedder=embedder,
                          reranker=reranker, **kw)


def test_a_failed_vector_search_is_said_and_no_document_is_told_the_vectors_passed_it_over(db):
    report = _with(db, "停修", NoMemory(), models()["reranker"], follow=["dq"])  # dq: only the vectors find it
    assert "no memory" in report["dense_error"] and report["vectors"] == "failed" and report["order"] == "reranked"
    text = explain_text(report)
    assert "向量没跑成" in text and "向量 主料" not in text
    entry = report["follow"][0]
    assert entry["verdict"] == "no_candidate"
    assert "向量没跑成" in entry["words"] and "向量也没有把它排进候选" not in entry["words"]


def test_with_both_models_failing_the_order_is_said_to_be_the_keyword_order(db):
    report = _with(db, "停修", NoMemory(), NoModel())
    assert report["order"] == "keyword" and report["ranking"] == "keyword"
    text = explain_text(report)
    assert "用的是关键词的顺序" in text and "合起来的顺序" not in text and "进重排" not in text and "对题×" not in text
    assert report["counts"]["head"] == [0, 0]


def test_a_failed_rerank_over_working_vectors_is_the_fused_order(db):
    report = _with(db, "停修", models()["embedder"], NoModel())
    assert report["order"] == "fused" and report["vectors"] == "used"
    assert "用的是关键词和向量合起来的顺序" in explain_text(report)


def test_in_keyword_mode_a_followed_document_is_given_no_fused_place(db):
    report = _explain(db, "停修", "keywords only", limit=2, follow=["dx"])
    assert report["vectors"] == "off" and report["order"] == "keyword"
    words = report["follow"][0]["words"]
    assert "None" not in words and "合起来" not in words and "关键词第" in words
    assert "None" not in explain_text(report)


def test_without_a_rerank_the_formula_is_not_printed(db):
    report = _explain(db, "停修", "dense only")
    text = explain_text(report)
    assert report["order"] == "fused" and "对题×" not in text and "没有重排" in text and "进重排" not in text
    assert report["counts"]["head"] == [0, 0] and all(r["in_head"] is False for r in report["rows"])


def test_the_keyword_place_is_within_its_tier_as_fusion_counts_it(db):
    """review 11-15: the keyword place was over both tiers while the vector and fused places were within one."""
    rows = {r["doc_id"]: r for r in _explain(db, "停修")["rows"]}
    assert rows["j1"]["background"] is True and rows["j1"]["keyword"]["rank"] == 1
    assert sorted(r["keyword"]["rank"] for r in rows.values() if r["keyword"] and not r["background"]) == [1, 2, 3, 4, 5]


def test_in_the_results_names_how_many_were_asked_for(db):
    entry = _explain(db, "停修", limit=3, follow=["d3"])["follow"][0]
    assert entry["verdict"] == "in_top" and "前 3 名" in entry["words"] and "回传" not in entry["words"]
