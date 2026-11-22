"""search(unit=...) also gives the best results outside the unit (11-22, research/eval_2026-11-18/runs/compare_1122.md
§3): a small model fills unit nearly every time and often the wrong office (總務處 for the dormitories, which 生活輔導組
under 學生事務處 runs), and the filter then hides the document that answers. results still holds the unit's documents
only; other_units adds the first OTHER_UNITS documents a plain search ranks that the filter leaves out."""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from nknu.retrieval import public, respond, server, unitfilter, web
from nknu.retrieval.hybrid import scope_units
from tests.retrieval.test_public_1121 import HOST, get
from tests.retrieval.test_respond import TODAY, Tiny, hit, prose, result

DOCS = {  # id: (unit, grp)
    "g0": ("總務處", "事務組"), "g1": ("總務處", "事務組"),
    "s0": ("學生事務處", "生活輔導組"), "s1": ("學生事務處", "生活輔導組"), "s2": ("學生事務處", "生活輔導組"),
    "a0": ("教務處", "註冊組"),
}
PLAIN = ["s0", "g0", "s1", "a0", "s2", "g1"]  # the order a search without a unit gives


class Split:
    """A search that keeps to the unit filter as the real one does: a filtered call gets the documents of the fixed
    order the filter allows, a plain call the fixed order itself."""

    def __init__(self, chunks: dict[str, list[int]], order: list[str]):
        self.chunks, self.order, self.calls = chunks, order, []

    def __call__(self, conn, query, *, today, aliases, limit=10, only=None):
        self.calls.append({"query": query, "limit": limit, "only": only})
        ids = self.order
        if only is not None:
            keep = unitfilter.allowed(conn, only)
            ids = [i for i in ids if i in keep]
        return result(*(hit(i, self.chunks[i]) for i in ids[:limit]), query=query)


@pytest.fixture
def lib(tmp_path):
    t = Tiny(tmp_path / "aca.db")
    t.conn.executemany("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, ?)", [
        ("國立高雄師範大學", "國立高雄師範大學", "学校", None, None),
        ("總務處", "總務處", "行政单位", "國立高雄師範大學", None),
        ("學生事務處", "學生事務處", "行政单位", "國立高雄師範大學", None),
        ("生活輔導組", "生活輔導組", "行政组别", "學生事務處", None),
        ("教務處", "教務處", "行政单位", "國立高雄師範大學", None),
    ])
    t.conn.executemany("INSERT INTO unit_aliases(alias, unit_id) VALUES (?, ?)", [
        ("國立高雄師範大學", "國立高雄師範大學"), ("總務處", "總務處"), ("學生事務處", "學生事務處"),
        ("生活輔導組", "生活輔導組"), ("教務處", "教務處")])
    # longer than a 段落 (respond.PASSAGE_CHARS), so a passage is not the whole text
    chunks = {d: t.doc(d, prose(3000), title=f"宿舍公告{d}", unit=u, grp=g, url=f"https://www.nknu.edu.tw/{d}",
                       ident=("2026-09-01", "published"))
              for d, (u, g) in DOCS.items()}
    t.conn.commit()
    t.conn.close()
    return tmp_path / "aca.db", chunks


async def search(lib, order, args):
    path, chunks = lib
    s = Split(chunks, order)
    async with Client(server.create_mcp(path, search_fn=s, today_fn=lambda: TODAY)) as c:
        r = await c.call_tool("search", args, raise_on_error=False)
    return json.loads(r.content[0].text), s.calls


async def test_a_wrong_office_still_shows_the_documents_that_answer(lib):
    body, calls = await search(lib, PLAIN, {"query": "宿舍 退宿", "unit": "總務處"})
    assert [r["id"] for r in body["results"]] == ["g0", "g1"]  # results: the unit's documents only, as before
    assert [r["id"] for r in body["other_units"]] == ["s0", "s1", "a0"]  # the first three the filter left out
    assert [r["rank"] for r in body["other_units"]] == [1, 2, 3]
    # the filtered search as before; then a plain one with the agent's own words
    assert calls[0]["only"].unit_id == "總務處" and calls[0]["query"] == "宿舍 退宿 總務處"
    assert calls[1]["only"] is None and calls[1]["query"] == "宿舍 退宿" and calls[1]["limit"] == server.OTHER_DEPTH
    said = [n for n in body["notes"] if "other_units" in n]
    assert len(said) == 1 and "總務處" in said[0]
    assert body["found"] is True and body["applied"]["unit"]["unit"] == "總務處"


async def test_a_group_keeps_its_own_and_its_office_documents_that_name_it(lib):
    body, _ = await search(lib, PLAIN, {"query": "宿舍 退宿", "unit": "生活輔導組"})
    assert [r["id"] for r in body["results"]] == ["s0", "s1", "s2"]
    assert [r["id"] for r in body["other_units"]] == ["g0", "a0", "g1"]


@pytest.mark.parametrize("unit", ["", "火星系", "國立高雄師範大學"])
async def test_no_filter_no_second_search(lib, unit):
    # no unit, a name the tree does not know, the school: nothing was filtered, so nothing was left out
    body, calls = await search(lib, PLAIN, {"query": "宿舍 退宿", "unit": unit})
    assert len(calls) == 1 and calls[0]["only"] is None
    assert "other_units" not in body and not [n for n in body["notes"] if "other_units" in n]


async def test_nothing_left_out_nothing_added(lib):
    body, calls = await search(lib, ["g0", "g1"], {"query": "宿舍 退宿", "unit": "總務處"})
    assert len(calls) == 2 and [r["id"] for r in body["results"]] == ["g0", "g1"]
    assert "other_units" not in body and not [n for n in body["notes"] if "other_units" in n]


async def test_a_unit_with_nothing_on_it_says_so_and_still_shows_the_others(lib):
    body, _ = await search(lib, ["s0", "g0"], {"query": "宿舍 退宿", "unit": "教務處"})
    assert body["found"] is False and "results" not in body
    assert [r["id"] for r in body["other_units"]] == ["s0", "g0"]
    assert "教務處" in body["notes"][0] and "other_units" in body["notes"][0]


async def test_others_get_a_passage_or_the_snippet_asked_for_never_a_full_place(lib):
    body, _ = await search(lib, PLAIN, {"query": "宿舍 退宿", "unit": "總務處", "detail": "full"})
    assert body["results"][0]["content_type"] in ("全文", "章節")
    assert {r["content_type"] for r in body["other_units"]} == {"段落"}
    body, _ = await search(lib, PLAIN, {"query": "宿舍 退宿", "unit": "總務處", "detail": "snippet"})
    assert {r["content_type"] for r in body["other_units"]} == {"片段"}


async def test_sort_by_date_reorders_the_unit_documents_only(lib):
    body, _ = await search(lib, PLAIN, {"query": "宿舍 退宿", "unit": "總務處", "sort": "date"})
    assert [r["id"] for r in body["other_units"]] == ["s0", "s1", "a0"]  # the most relevant, not the newest


async def test_a_tight_budget_takes_the_other_units_before_any_result(lib, monkeypatch):
    monkeypatch.setattr(respond, "RESPONSE_BUDGET_BYTES", 1)  # nothing fits: everything that can go goes
    body, _ = await search(lib, PLAIN, {"query": "宿舍 退宿", "unit": "總務處"})
    assert "other_units" not in body and [r["id"] for r in body["results"]] == ["g0"]


async def test_the_page_and_the_log_show_the_other_units(lib, tmp_path):
    path, chunks = lib
    app = public.create_app(path, search_fn=Split(chunks, PLAIN), today_fn=lambda: TODAY, allowed_hosts=[HOST],
                            log_path=tmp_path / "q.jsonl")
    r = await get(app, "/search", params={"q": "宿舍 退宿", "unit": "總務處"})
    assert web.OTHER_UNITS_HEAD in r.text and 'href="/doc/s0"' in r.text and 'href="/doc/g0"' in r.text
    assert r.text.index('href="/doc/g0"') < r.text.index(web.OTHER_UNITS_HEAD) < r.text.index('href="/doc/s0"')
    line = json.loads((tmp_path / "q.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["ids"] == ["g0", "g1"] and line["other_ids"] == ["s0", "s1", "a0"]
    r = await get(app, "/api/search", params={"q": "宿舍 退宿", "unit": "總務處"})
    assert [x["id"] for x in r.json()["other_units"]] == ["s0", "s1", "a0"]


def test_outside_keeps_the_order_and_skips_what_the_answer_holds(lib):
    path, chunks = lib
    conn = server.connect(path)
    try:
        only, _ = unitfilter.unit_filter(scope_units(conn), "總務處")
        hits = [hit(i, chunks[i]) for i in PLAIN]
        assert [h.doc_id for h in server.outside(conn, only, hits, taken={"s0"})] == ["s1", "a0", "s2"]
        assert server.outside(conn, only, [], taken=set()) == []
    finally:
        conn.close()
