"""The tools of design 2026-11-16-mcp-interface-design §一～§四 (第四版): search(query, detail, limit, unit, sort) and
fetch(id, part), through fastmcp.Client with a stand-in search."""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from nknu.retrieval import server
from tests.retrieval.test_respond import TODAY, Tiny, hit, prose, result


class Recorder:
    """A search that answers with fixed hits and records what it was asked (the text, the limit, the unit filter)."""

    def __init__(self, *hits):
        self.hits, self.calls = list(hits), []

    def __call__(self, conn, query, *, today, aliases, limit=10, only=None):
        self.calls.append({"query": query, "limit": limit, "only": only})
        return result(*self.hits[:limit], query=query)


@pytest.fixture
def db_path(tmp_path):
    t = Tiny(tmp_path / "aca.db")
    t.conn.executemany("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, ?)", [
        ("國立高雄師範大學", "國立高雄師範大學", "学校", None, None),
        ("教務處", "教務處", "行政单位", "國立高雄師範大學", None),
    ])
    t.conn.executemany("INSERT INTO unit_aliases(alias, unit_id) VALUES (?, ?)",
                       [("國立高雄師範大學", "國立高雄師範大學"), ("教務處", "教務處")])
    for i, day in enumerate(["2026-09-01", None, "2026-10-15", "2026-03-01"]):
        t.doc(f"d{i}", prose(300), title=f"公告{i}", ident=(day, "published") if day else None,
              url=f"https://c.nknu.edu.tw/aca/news/{i}")
    t.conn.commit()
    t.conn.close()
    return tmp_path / "aca.db"


async def call(db_path, search_fn, tool, args):
    mcp = server.create_mcp(db_path, search_fn=search_fn, today_fn=lambda: TODAY)
    async with Client(mcp) as c:
        return await c.call_tool(tool, args, raise_on_error=False)


async def test_the_schema_offers_the_four_parameters(db_path):
    async with Client(server.create_mcp(db_path, search_fn=Recorder())) as c:
        tools = {t.name: t for t in await c.list_tools()}
    props = tools["search"].input_schema["properties"]
    assert tools["search"].input_schema["required"] == ["query"]
    assert props["detail"]["enum"] == ["snippet", "passage", "full"] and props["detail"]["default"] == "passage"
    assert props["sort"]["enum"] == ["relevance", "date"] and props["sort"]["default"] == "relevance"
    assert (props["limit"]["minimum"], props["limit"]["maximum"], props["limit"]["default"]) == (1, 50, 10)
    assert props["unit"]["default"] == "" and "單位" in props["unit"]["description"]
    assert set(tools["fetch"].input_schema["properties"]) == {"id", "part"}


@pytest.mark.parametrize(("args", "said"), [
    ({"query": "停修", "detail": "summary"}, "'snippet', 'passage' or 'full'"),
    ({"query": "停修", "sort": "newest"}, "'relevance' or 'date'"),
    ({"query": "停修", "limit": 0}, "1～50"),
    ({"query": "停修", "limit": 51}, "1～50"),
])
async def test_a_bad_value_says_what_is_allowed(db_path, args, said):
    r = await call(db_path, Recorder(), "search", args)
    assert r.is_error and r.content[0].text.startswith("錯誤 bad_argument") and said in r.content[0].text


async def test_defaults_say_nothing_and_limit_reaches_the_search(db_path):
    rec = Recorder(*(hit(f"d{i}", [i + 1]) for i in range(4)))
    body = json.loads((await call(db_path, rec, "search", {"query": "公告"})).content[0].text)
    assert "applied" not in body and rec.calls[-1]["limit"] == 10
    body = json.loads((await call(db_path, rec, "search", {"query": "公告", "limit": 2, "detail": "snippet"}))
                      .content[0].text)
    assert body["applied"] == {"detail": "snippet", "limit": 2} and len(body["results"]) == 2


async def test_a_unit_filters_the_search_and_joins_the_searched_text(db_path):
    rec = Recorder(hit("d0", [1]))
    body = json.loads((await call(db_path, rec, "search", {"query": "停修", "unit": "教务处"})).content[0].text)
    asked = rec.calls[-1]
    assert asked["query"] == "停修 教務處" and asked["only"] is not None and asked["only"].unit_id == "教務處"
    assert body["query"] == "停修"  # the agent's own query is echoed
    assert body["applied"] == {"unit": {"input": "教务处", "applied": True, "unit": "教務處"}}
    await call(db_path, rec, "search", {"query": "教務處 停修", "unit": "教務處"})
    assert rec.calls[-1]["query"] == "教務處 停修"  # named already


async def test_an_unknown_unit_searches_everything_and_says_so(db_path):
    rec = Recorder(hit("d0", [1]))
    body = json.loads((await call(db_path, rec, "search", {"query": "停修", "unit": "火星系"})).content[0].text)
    assert rec.calls[-1]["only"] is None and rec.calls[-1]["query"] == "停修"
    assert body["applied"]["unit"]["applied"] is False and body["results"]


async def test_sort_by_date_puts_the_newest_first_and_the_undated_last(db_path):
    rec = Recorder(*(hit(f"d{i}", [i + 1]) for i in range(4)))
    body = json.loads((await call(db_path, rec, "search", {"query": "公告", "sort": "date", "limit": 3}))
                      .content[0].text)
    assert rec.calls[-1]["limit"] == 20  # reorders at least the 20 most relevant
    assert [r["id"] for r in body["results"]] == ["d2", "d0", "d3"]  # d1 has no date


async def test_fetch_reads_by_id_or_by_url(db_path):
    for ref in ("d2", "https://c.nknu.edu.tw/aca/news/2", "http://c.nknu.edu.tw/aca/news/2"):
        body = json.loads((await call(db_path, Recorder(), "fetch", {"id": ref})).content[0].text)
        assert body["found"] is True and body["id"] == "d2", ref
