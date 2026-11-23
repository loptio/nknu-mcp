"""Design 2026-11-16-mcp-interface-design §二 (第四版, the owner 11-17): how much text each result carries, at the
values the service uses (tests/retrieval/test_respond.py checks the same logic at the values it was written for)."""

from __future__ import annotations

import json

import pytest

from nknu.retrieval import respond
from nknu.retrieval.respond import build_response, read_response
from tests.retrieval.test_respond import TODAY, Tiny, articles, hit, prose, result


@pytest.fixture
def db(tmp_path):
    t = Tiny(tmp_path / "aca.db")
    yield t
    t.conn.close()


def size(resp: dict) -> int:
    return len(respond.to_json(resp).encode("utf-8"))


def bare(content: str) -> str:
    return content.replace("…", "").strip()


def test_the_levels_and_their_values():
    assert respond.DETAILS == ("snippet", "passage", "full") and respond.DEFAULT_DETAIL == "passage"
    assert (respond.SNIPPET_CHARS, respond.PASSAGE_CHARS, respond.FULL_PLACES) == (200, 500, 5)
    assert respond.FULL_CAP_CHARS == 3_000 and respond.RESPONSE_BUDGET_BYTES == 50_000
    assert respond.READ_BUDGET_BYTES == 36_000  # fetch keeps its budget


def test_passage_is_the_default_and_reaches_past_its_chunk(db):
    db.doc("dP", prose(3000), title="長公告", chunks=[(0, 1000, 1100)], ident=("2026-10-01", "published"))
    db.doc("dS", prose(300), title="短公告", ident=("2026-10-02", "published"))
    db.date("dP", "published", "2026-10-01")
    db.date("dS", "published", "2026-10-02")
    resp = build_response(db.conn, result(hit("dP", [1]), hit("dS", [2])), today=TODAY)
    long, short = resp["results"]
    assert long["content_type"] == "段落" and 300 < len(bare(long["content"])) <= 520
    assert len(bare(long["content"])) > 100  # past the 100-character chunk
    assert long["more"] == '全文共 3000 字；用 fetch 讀全文：fetch(id="dP")'
    assert short["content_type"] == "全文" and "more" not in short  # a text the 段落 holds whole
    assert "dates" in long and "dates" in short  # 段落 rows keep the whole header
    assert "applied" not in resp


def test_snippet_rows_are_short_and_lean(db):
    db.doc("dP", prose(3000), title="長公告", ident=("2026-10-01", "published"))
    db.date("dP", "published", "2026-10-01")
    db.conn.execute("INSERT INTO meta VALUES ('copies:dP', ?)",
                    (json.dumps([{"id": "dc", "url": "https://c.nknu.edu.tw/x/1"}]),))
    [row] = build_response(db.conn, result(hit("dP", [1])), today=TODAY, detail="snippet")["results"]
    assert row["content_type"] == "片段" and len(bare(row["content"])) <= 200
    assert set(row) <= {"rank", *respond.LEAN_KEYS, "content_type", "content", "more", "part", "heading"}
    assert "dates" not in row and "copies" not in row and "parent" not in row
    assert "2026-10-01" in row["identity"]  # the identity line still says when


def test_full_gives_five_places_then_snippets(db):
    hits = []
    for i in range(8):
        text, secs = articles(10, 500)
        hits.append(hit(f"dL{i}", db.doc(f"dL{i}", text, title=f"長文件{i}", sections=secs)[:2]))
    rs = build_response(db.conn, result(*hits), today=TODAY, detail="full")["results"]
    assert [r["content_type"] for r in rs] == ["章節"] * 5 + ["片段"] * 3
    assert all("toc" in r for r in rs[:5]) and all("toc" not in r and "dates" not in r for r in rs[5:])


def test_fifty_full_results_fit_the_budget(db):
    hits = []
    for i in range(50):
        text, secs = articles(40, 500)
        hits.append(hit(f"dL{i}", db.doc(f"dL{i}", text, title=f"長文件{i}" * 20, sections=secs)[:3]))
    resp = build_response(db.conn, result(*hits), today=TODAY, detail="full")
    assert size(resp) <= respond.RESPONSE_BUDGET_BYTES
    assert respond.NOTE_DOWNGRADED in resp["notes"]
    assert resp["results"][0]["content_type"] == "章節"  # rank 1 keeps its text


def test_applied_is_in_the_head_only_when_given(db):
    db.doc("d1", prose(300))
    applied = {"detail": "snippet", "limit": 5}
    resp = build_response(db.conn, result(hit("d1", [1])), today=TODAY, detail="snippet", applied=applied)
    assert resp["applied"] == applied
    assert build_response(db.conn, result(query="!!!"), today=TODAY, applied=applied)["applied"] == applied


def test_an_unknown_detail_is_refused(db):
    with pytest.raises(ValueError):
        build_response(db.conn, result(), today=TODAY, detail="summary")


def test_every_way_on_says_fetch(db):
    db.doc("dP", prose(30000), title="很長的文件")
    for detail in respond.DETAILS:
        text = respond.to_json(build_response(db.conn, result(hit("dP", [1])), today=TODAY, detail=detail))
        assert "read(" not in text and "用 read" not in text and "fetch(" in text, detail


def test_fetch_takes_a_url_the_library_holds(db):
    db.doc("dU", prose(300), url="https://c.nknu.edu.tw/aca/news/1")
    for url in ("https://c.nknu.edu.tw/aca/news/1", "http://c.nknu.edu.tw/aca/news/1"):
        assert read_response(db.conn, url, "", today=TODAY)["id"] == "dU", url
    assert read_response(db.conn, "https://elsewhere.example/1", "", today=TODAY)["found"] is False
