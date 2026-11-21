"""The third round of 11-15 on the 檢視台 pages (owner: 「信息太雜…沒有用一些視覺化方法點綴，顏色也沒有考慮過」): what the
drawings must stay true to. A bar is the search's own sum; the ruler keeps the order of the places; the funnel shows
the steps that ran; the scoreboard is the explained search of every test question; a date line draws only dates that
are days; a step that left nothing has no card."""

from __future__ import annotations

import json
import re
import time
from datetime import date

import pytest
from starlette.testclient import TestClient

from nknu.ops import board, look, search_page, trace_page, web
from nknu.ops.explain import explain_search
from nknu.retrieval import vectors
from nknu.retrieval.server import aliases_of
from tests.ops import world
from tests.ops.library import SETTINGS, models
from tests.ops.world import NOTICE_URL, PAGER_URL
from tests.retrieval.test_search import TODAY

BASE = "http://127.0.0.1:8878"


def _explained(db, query: str, setting: str = "the owner's defaults", follow=()) -> dict:
    config, rank = SETTINGS[setting]
    return explain_search(db.conn, query, today=TODAY, aliases=aliases_of(db.conn), config=config, rank=rank,
                          follow=follow, **models())


# ---------------------------------------------------------------- a score as a bar of its parts

@pytest.mark.parametrize("setting", list(SETTINGS))
@pytest.mark.parametrize("query", ["停修", "115-1 停修", "化學系 停修", "退選"])
def test_a_bars_parts_add_up_to_the_searchs_own_score(db, setting, query):
    """(base + what was added) x the discounts + the unit bonus is the score the search gave, whatever ran."""
    report = _explained(db, query, setting)
    assert report["rows"]
    for row in report["rows"]:
        p = search_page.parts(row, report["weights"])
        times = 1.0
        for _, factor in p["factors"]:
            times *= factor
        assert p["pre"] == pytest.approx(p["base"] + sum(v for _, v in p["adds"]))
        assert p["pre"] * times + p["bonus"] == pytest.approx(row["score"], abs=1e-9), (setting, row["doc_id"])
        assert p["total"] == row["score"]


def test_a_part_that_changed_nothing_is_not_among_the_parts(db):
    report = _explained(db, "停修")
    by_id = {row["doc_id"]: search_page.parts(row, report["weights"]) for row in report["rows"]}
    assert not any(v == 0 for p in by_id.values() for _, v in p["adds"])
    assert not any(f == 1.0 for p in by_id.values() for _, f in p["factors"])
    assert [n for n, _ in by_id["dept"]["factors"]] == ["對口"]  # a department's own page, no department asked for
    assert "還有效" in [n for n, _ in by_id["d1"]["factors"]]  # last year's notice


def _flex(bar: str) -> dict[str, float]:
    """The lengths a bar draws, by kind, in score units."""
    got: dict[str, float] = {}
    for kind, value in re.findall(r"<i class='(\w+)' style='flex:(\d+) 0 0'>", bar):
        got[kind] = got.get(kind, 0.0) + int(value) / 1000
    return got


def test_a_bar_is_as_long_as_the_score_and_what_the_discounts_took(db):
    report = _explained(db, "停修")
    weights = report["weights"]
    every = [search_page.parts(row, weights) for row in report["rows"]]
    scale = max(p["pre"] for p in every)
    for row, p in zip(report["rows"], every, strict=True):
        drawn = _flex(search_page._bar(p, scale))
        kept = drawn.get("rel", 0) + drawn.get("add", 0)
        assert kept == pytest.approx(row["score"], abs=0.002), row["doc_id"]  # ink and green end at the score
        assert drawn.get("cut", 0) == pytest.approx(p["pre"] - row["score"], abs=0.002)  # pale red: what was taken
        assert sum(drawn.values()) == pytest.approx(scale, abs=0.004)  # every bar on the one scale
    assert search_page._bar(every[0], 0) == ""


def test_a_part_too_small_to_see_is_drawn_in_the_bases_segment():
    p = {"base": 3.0, "base_name": "對題", "adds": [("新舊", 0.004), ("學期", 0.5)], "factors": [], "bonus": 0.0,
         "pre": 3.504, "total": 3.504}
    bar = search_page._bar(p, 3.504)
    assert bar.count("class='add'") == 1 and _flex(bar) == pytest.approx({"rel": 3.004, "add": 0.5})


# ---------------------------------------------------------------- the ruler and the verdict

def test_the_ruler_keeps_the_order_of_the_places_and_gives_the_first_ten_most_of_its_length():
    last = 737
    at = [search_page.place_on_ruler(r, last) for r in (1, 2, 3, 10, 11, 50, 64, 187, 737)]
    assert at == sorted(at) and len(set(at)) == len(at)
    assert at[0] == 0 and at[3] == pytest.approx(56) and at[5] == pytest.approx(78) and at[-1] == pytest.approx(100)
    assert search_page.place_on_ruler(900, last) == 100  # never beyond the ruler's end
    assert 0 <= search_page.place_on_ruler(30, 12) <= 100  # a library smaller than the reranker's head


def _followed(*ranks_or_verdicts, limit=10, final=300) -> dict:
    follow = []
    for i, x in enumerate(ranks_or_verdicts):
        if isinstance(x, int):
            follow.append({"doc_id": f"d{i}", "verdict": "in_top" if x <= limit else "ranked", "ref": f"d{i}",
                           "row": {"rank": x, "title": f"標題{i}"}, "words": ""})
        else:
            follow.append({"doc_id": f"d{i}" if x == "no_candidate" else None, "verdict": x, "ref": f"d{i}", "row": None,
                           "words": ""})
    return {"follow": follow, "limit": limit, "counts": {"final": final}}


def test_the_verdict_says_where_the_best_followed_document_stands():
    question = {"id": "Q01"}
    top = search_page.verdict_html(_followed(3, 64, "no_candidate"), question)
    assert "標準答案在第 3 名" in top and "g-top3" in top and "3、64 名" in top and "沒進候選 1 份" in top
    assert top.count("class='m ") == 2 and "class='off'" in top and "ruler lost" in top
    out = search_page.verdict_html(_followed(24), question)
    assert "前 10 名裡沒有標準答案" in out and "v-badge g-out" in out and "href='#r-d0'" in out
    none = search_page.verdict_html(_followed("no_candidate", "not_found"), question)
    assert "前 10 名裡沒有標準答案" in none and "沒進候選 1 份 · 庫裡沒有 1 份" in none and "class='m " not in none
    asked = search_page.verdict_html(_followed(2), None)
    assert "指定的文件在第 2 名" in asked and "class='off'" not in asked and "ruler lost" not in asked
    assert search_page.verdict_html({"follow": [], "limit": 10, "counts": {"final": 0}}, None) == ""


def test_markers_that_would_overlap_take_a_second_lane():
    close = search_page.verdict_html(_followed(64, 70), {"id": "Q"})  # far down, the ruler is by the logarithm
    assert "class='m r0 " in close and "class='m r1 " in close and "ruler tall" in close
    apart = search_page.verdict_html(_followed(1, 9), {"id": "Q"})
    assert apart.count("class='m r0 ") == 2 and "ruler tall" not in apart


# ---------------------------------------------------------------- the funnel follows what ran

def test_the_funnel_shows_the_steps_that_ran(db):
    full = search_page.pipeline_html(_explained(db, "停修"))
    assert full.count("class='st'") == 4 and "</b>合併後</div>" in full and "</b>重排</div>" in full
    assert "</b>回傳</div>" in full
    assert "off-note" not in full
    no_rerank = search_page.pipeline_html(_explained(db, "停修", "dense only"))
    assert no_rerank.count("class='st'") == 3 and "</b>重排</div>" not in no_rerank and "沒有重排" in no_rerank
    keywords = search_page.pipeline_html(_explained(db, "停修", "keywords only"))
    assert keywords.count("class='st'") == 2 and "</b>合併後</div>" not in keywords and "只有關鍵字" in keywords
    assert "None" not in full + no_rerank + keywords


def test_the_questions_words_say_which_a_candidate_must_hold(db):
    named = search_page.pipeline_html(_explained(db, "115-1 停修"))
    assert "class='w must'" in named and "class='w term'" in named and "115-1" in named
    assert "不加新舊分" in named  # the term's own tip: why no date part moves this search
    alone = search_page.pipeline_html(_explained(db, "停修"))
    assert "class='w must'" not in alone and "要對上" not in alone  # no legend for a kind of word that is not there


# ---------------------------------------------------------------- the scoreboard on the pages

def _questions(tmp_path) -> tuple:
    folder = tmp_path / "eval"
    folder.mkdir()
    (folder / "core20.json").write_text(json.dumps([{"id": "Q01", "question": "115-1 停修"},
                                                    {"id": "Q02", "question": "怎么退选"}]), encoding="utf-8")
    (folder / "noanswer22.json").write_text(json.dumps([{"id": "N01", "question": "校車"}]), encoding="utf-8")
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"Q01": {"docs": ["d3"]}, "Q02": {"docs": ["dx"]}, "N01": {"docs": ["nothere"]}}),
                    encoding="utf-8")
    return folder, gold


def _settled(client) -> dict:
    for _ in range(100):
        got = client.get("/api/board").json()
        if got["done"] == got["total"]:
            return got
        time.sleep(0.05)
    raise AssertionError("the board never settled")


def test_the_board_is_the_explained_search_of_every_test_question(db, tmp_path):
    folder, gold = _questions(tmp_path)
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY, questions=folder,
                         gold=gold)
    with TestClient(app, base_url=BASE) as client:
        got = _settled(client)
        by_id = {i["id"]: i for i in got["items"]}
        for qid in by_id:
            report = client.get("/api/search", params={"qid": qid}).json()
            assert {k: by_id[qid][k] for k in ("rank", "grade", "stop", "gold")} == board.outcome(report), qid
        assert by_id["Q01"]["grade"] == board.FIRST and by_id["N01"]["grade"] == board.OUT
        assert got["counts"][board.FIRST] >= 1 and sum(got["counts"].values()) == 3
        page = client.get("/search").text
    assert page.count("class='tile'") == 3 and "data-board='/api/board'" in page
    assert "data-q='Q01' data-grade='first'><span class='g g-first' data-tip='標準答案第 1 名'>1</span>" in page
    assert "<b>Q02</b>怎麼退選</span>" in page  # shown in Traditional, as every question is
    assert "data-board='/api/board'>" in page  # no data-running: nothing left to wait for, the page does not ask again
    assert "class='seg g-first' data-n='" in page


def test_the_board_api_is_only_for_this_machine_and_only_get(db, tmp_path):
    folder, gold = _questions(tmp_path)
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY, questions=folder,
                         gold=gold)
    with TestClient(app, base_url=BASE) as client:
        assert client.get("/api/board", headers={"host": "evil.example"}).status_code == 403
        assert client.post("/api/board").status_code == 405
        assert client.get("/api/board").headers["cache-control"] == "no-store"
        _settled(client)


def test_a_page_without_test_questions_has_no_board(db, tmp_path):
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(app, base_url=BASE) as client:
        assert "data-board" not in client.get("/").text.split("<script>")[0]
        assert client.get("/api/board").json()["total"] == 0


def test_a_page_draws_without_its_board_when_the_library_file_cannot_be_looked_at(db, tmp_path, monkeypatch):
    """While a build swaps the library the file may be gone for a moment: the page is still a page."""
    folder, gold = _questions(tmp_path)
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY, questions=folder,
                         gold=gold)

    def gone(self, start=True):
        raise FileNotFoundError("t.db")

    monkeypatch.setattr(board.Board, "snapshot", gone)
    with TestClient(app, base_url=BASE) as client:
        page = client.get("/try")
        assert page.status_code == 200 and "class='tile'" not in page.text and "沒有設定" in page.text
        assert client.get("/").status_code == 200
    assert search_page.board_html(None) == ""


def test_the_board_draws_what_is_still_waiting():
    snapshot = {"total": 3, "done": 1, "running": True, "limit": 10,
                "counts": {board.FIRST: 1, board.TOP3: 0, board.TOP: 0, board.OUT: 0, board.FAILED: 0},
                "items": [{"id": "Q01", "question": "停修", "rank": 1, "grade": board.FIRST},
                          {"id": "Q02", "question": "<b>退选</b>", "rank": None, "grade": board.WAIT},
                          {"id": "Q03", "question": "校車", "rank": None, "grade": board.WAIT}]}
    page = search_page.board_html(snapshot)
    assert " data-running" in page and page.count("data-grade='wait'") == 2
    # redesign 2026-11-17 (diagnosis.md 搜尋測試 ①): how far the re-run has come, and pending boxes, not grey 「…」
    assert "data-prog" in page and ">正在重算 1 / 3</span>" in page and "還在算 2 題" not in page
    assert "<span class='g g-wait' data-tip='還在算'></span>" in page and "…" not in page
    assert "&lt;b&gt;退選&lt;/b&gt;" in page and "<b>退" not in page  # a question's words are text, never markup
    assert search_page.board_html({**snapshot, "total": 0, "items": []}) == ""


def test_a_finished_board_hides_its_progress_and_the_script_keeps_it_current():
    snapshot = {"total": 1, "done": 1, "running": False, "limit": 10,
                "counts": {board.FIRST: 1, board.TOP3: 0, board.TOP: 0, board.OUT: 0, board.FAILED: 0},
                "items": [{"id": "Q01", "question": "停修", "rank": 1, "grade": board.FIRST}]}
    page = search_page.board_html(snapshot)
    assert "data-prog" in page and " hidden>正在重算 1 / 1</span>" in page and " data-running" not in page
    # the poll fills the progress and gives a filled tile its own floating word (it kept 還在算 before)
    assert "'正在重算 '+(s.total-waiting)+' / '+s.total" in look.JS and "g.setAttribute('data-tip',SAID(it))" in look.JS
    assert "'…'" not in look.JS


# ---------------------------------------------------------------- the home page

def test_the_home_is_tonight_and_the_tools_went_to_their_entries(db, tmp_path):
    # owner 11-16: the home says whether every step of the pipeline is normal; since the structure design of 11-17
    # (section 二) it is 今晚, 搜尋測試 is 試用's second tab (the scoreboard on 試用's empty page) and the empty trace is
    # 文件's box
    folder, gold = _questions(tmp_path)
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY, questions=folder,
                         gold=gold)
    with TestClient(app, base_url=BASE) as client:
        home = client.get("/").text
        search = client.get("/search")
        page_of_trace = client.get("/trace")
        _settled(client)
    assert "aria-current='page'>每晚運行</a>" in home and "action='/search'" not in home
    assert str(search.url).endswith("/try#board") and "data-board='/api/board'" in search.text
    assert "aria-current='page'>測試</a>" in search.text
    assert str(page_of_trace.url).endswith("/doc") and "action='/doc'" in page_of_trace.text
    assert "aria-current='page'>文件搜尋</a>" in page_of_trace.text


# ---------------------------------------------------------------- a document's dates on one line

DAY = date(2026, 11, 15)


def test_the_dates_line_draws_days_and_periods_with_today():
    dates = [{"kind": "published", "date": "2026-10-21", "raw": "", "source": ""},
             {"kind": "period_start", "date": "2026-11-03", "raw": "", "source": ""},
             {"kind": "period_end", "date": "2026-11-16", "raw": "", "source": ""}]
    line = trace_page.dates_line(dates, DAY)
    assert line.count("class='pt'") == 1 and line.count("class='span'") == 1 and line.count("class='now'") == 1
    assert "公告日期 <b>2026-10-21</b>" in line and "期間 <b>11-03 ～ 11-16</b>" in line and "今天" in line
    left = [float(x) for x in re.findall(r"class='(?:pt|span|now)' style='left:([\d.]+)%", line)]
    assert all(0 <= x <= 100 for x in left)


def test_the_dates_line_leaves_out_what_is_no_day_and_is_empty_without_one():
    assert trace_page.dates_line([], DAY) == ""
    assert trace_page.dates_line([{"kind": "published", "date": "", "raw": "", "source": ""},
                                  {"kind": "server", "date": "不是日期", "raw": "", "source": ""}], DAY) == ""
    one = trace_page.dates_line([{"kind": "server", "date": "2026-11-15T08:00:00", "raw": "", "source": ""},
                                 {"kind": "published", "date": "?", "raw": "", "source": ""}], DAY)
    assert one.count("class='pt'") == 1  # a time of day is its day; the unreadable one is not drawn


def test_an_event_without_an_end_is_one_day_and_labels_that_would_collide_share_one():
    line = trace_page.dates_line([{"kind": "event_start", "date": "2026-11-14", "raw": "", "source": ""},
                                  {"kind": "published", "date": "2026-11-13", "raw": "", "source": ""},
                                  {"kind": "server", "date": "2026-11-13", "raw": "", "source": ""}], DAY)
    assert "事件開始 <b>2026-11-14</b>" in line and line.count("class='span'") == 0
    assert "公告日期 <b>2026-11-13</b>　伺服器檔案時間 <b>2026-11-13</b>" in line  # the two days of 11-13 share a label
    assert line.count("class='lab up") == 2  # that one, and today's


# ---------------------------------------------------------------- the trace page

@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    made = world.build(tmp_path)
    app = web.create_app(made.db, made.root, models=models(), today_fn=lambda: TODAY, viewer_url="http://viewer.test")
    with TestClient(app, base_url=BASE) as client:
        client.world = made
        yield client
    made.conn.close()


def test_a_step_that_left_nothing_has_no_card_and_its_place_on_the_line_is_no_link(site):
    made = site.world
    made.conn.execute("INSERT INTO docs(doc_id, kind_class, kind_raw, title, url, unit, has_text, text, chars, site,"
                      " text_source) VALUES ('e1', 'event', 'event', '停修截止', '', '教務處', 1, '停修截止', 4, ?, 'totext')",
                      (world.SITE,))
    made.conn.commit()
    page = site.get("/trace", params={"ref": "e1"}).text
    strip = page[page.index("<ol class='steps'>"):page.index("</ol>")]
    assert strip.count("<span class='a'") == 3 and strip.count("<a class=") == 2  # an event: only ④ and ⑤
    for step in ("s1", "s2", "s3"):
        assert f"id='{step}'" not in page
    # why the three left nothing floats over them (owner 11-15, of 資料總覽's floating words), not a list under them
    assert "id='s4'" in page and "class='gaps'" not in page
    assert strip.count("<span class='a' data-tip='") == 3 and "沒有紀錄" in strip


def test_every_step_that_left_something_has_its_card(site):
    page = site.get("/trace", params={"ref": NOTICE_URL}).text
    for step in ("s1", "s2", "s3", "s4", "s5"):
        assert f"id='{step}'" in page
    # 2026-11-18: the first two cards hold a few facts each and stand side by side on a wide screen
    assert "<div class='step-pair'><section class='card step' id='s1'>" in page
    assert page.index("id='s2'") < page.index("id='s3'") and "step-pair" not in page[page.index("id='s3'"):]
    assert "class='tline'" in page and "class='strip'" in page
    assert page.count("data-chunk='c") == 2 and page.count("<details class='chunk'") == 2  # one of each per chunk
    assert "class='chunks'" in page and "chunks many" not in page


def test_a_document_of_many_chunks_lists_them_in_a_box_that_scrolls(site, monkeypatch):
    monkeypatch.setattr(trace_page, "ROOMY", 1)
    monkeypatch.setattr(trace_page, "DENSE", 1)
    page = site.get("/trace", params={"ref": NOTICE_URL}).text
    assert "class='chunks many'" in page and "class='strip dense'" in page


def test_what_the_library_left_out_shows_what_the_export_knows_of_it(site):
    page = site.get("/trace", params={"ref": PAGER_URL}).text
    assert "<h1>最新消息</h1>" in page and "<span class='pill bad'>沒進庫</span>" in page
    assert "id='s4'" in page and "id='s5'" not in page  # no search of a title the library does not hold


def test_an_address_is_shown_readable_and_linked_as_it_is(site):
    made = site.world
    made.conn.execute("UPDATE docs SET url = 'https://school.example/files/%E5%81%9C%E4%BF%AE.pdf' WHERE doc_id = ?",
                      (world.doc_id(world.FORM_URL),))
    made.conn.commit()
    page = site.get("/trace", params={"ref": world.doc_id(world.FORM_URL)}).text
    assert "href='https://school.example/files/%E5%81%9C%E4%BF%AE.pdf'" in page
    assert "<span>https://school.example/files/停修.pdf</span>" in page


# ---------------------------------------------------------------- the look

def test_the_pages_carry_their_own_style_and_script_and_load_nothing_from_elsewhere(db, tmp_path):
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(app, base_url=BASE) as client:
        page = client.get("/search", params={"q": "停修"}).text
    assert "<style>" in page and "<script>" in page
    assert not re.search(r"<(?:script|link|img)[^>]+(?:src|href)=['\"]https?://", page)  # nothing fetched from outside


def test_the_shared_theme_defines_every_colour_the_pages_use():
    # the colours live in nknu/ops/theme/theme.css since the redesign of 11-17 (tests/ops/test_theme.py checks it)
    defined = set(re.findall(r"(--[a-z0-9-]+):", look.THEME.split("}")[0]))
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", look.CSS + look.JS))
    assert used <= defined, used - defined


def test_a_documents_kind_is_an_icon_and_its_word_never_a_hue():
    tag = look.kind_tag("notice", {"notice": "公告"})
    assert "<svg" in tag and "公告" in tag and "style=" not in tag
    assert look.kind_tag(None, {}) == "" and "<svg" in look.kind_tag("something new", {})
    assert look.icon("no such icon") == ""


def test_a_page_beside_its_text_is_the_page_checks_copy_when_the_archive_is_there():
    # 2026-11-18: the bare export, its styles gone, drew its menus over one another; 網頁核查's copy frames the area
    # the library reads and greys the rest, which is what the pane is for
    from nknu.ops.trace_page import _original

    x = {"folder": "a.example-1", "file": "pages/p1.html"}
    with_copy = _original(x, "https://a.example/p?x=1")
    assert "src='/check/raw?url=https%3A%2F%2Fa.example%2Fp%3Fx%3D1'" in with_copy and "存檔裡的這一頁" in with_copy
    assert "href='/check?url=https%3A%2F%2Fa.example%2Fp%3Fx%3D1'" in with_copy and "/file?" not in with_copy
    assert "w.scrollTo(" in with_copy and "scrollIntoView" not in with_copy  # the frame scrolls, the page stays
    bare = _original(x)  # no archive mounted: the export as before
    assert "/file?folder=a.example-1" in bare and "/check/raw" not in bare
    assert "/check/raw" not in _original({"folder": "a.example-1", "file": "files/f1.pdf"}, "https://a.example/f1.pdf")


def test_the_stored_page_is_drawn_in_plain_flow_after_its_own_scraps_of_style():
    # 2026-11-18: a page's own <style> scraps (no style sheet file is archived) drew its menus over one another
    from nknu.ops import pagecheck

    css = pagecheck.FLOW_CSS
    assert "position:static!important" in css and "margin:revert!important" in css and "height:auto!important" in css
    for kept in (".nk-label", ".nk-box", ".nk-noimg", ".nk-bg", "img"):  # the marks and pictures keep their own
        assert f":not({kept})" in css
    import inspect

    src = inspect.getsource(pagecheck.Marked.html)
    assert "{styles}<style>{MARK_CSS}{FLOW_CSS}</style>" in src  # after the page's own styles and the marks


# ---------------------------------------------------------------- the new question set on 測試 (11-21)

NEWSET = {"set": "2026-11-18", "run": "final", "questions": 194, "tops": [1, 3, 10],
          "library": "aca.db 2026-11-21T04:03:26+08:00（88966 份文件）", "measured_at": "2026-11-21T10:00:00+08:00",
          "groups": [{"key": "B", "n": 83, "q": [29, 48, 69], "a1": [37, 53, 72], "a": [44, 65, 77]},
                     {"key": "B/open", "n": 41, "q": [15, 24, 35], "a1": [1, 2, 3], "a": [22, 33, 39]},
                     {"key": "B/sealed", "n": 42, "q": [14, 24, 34], "a1": [1, 2, 3], "a": [22, 32, 38]},
                     {"key": "A", "n": 100, "q": [75, 82, 91], "a1": [1, 2, 3], "a": [72, 93, 97]}],
          "b_asked": {"all": 94, "answered": 83, "related_only": 9, "absent": 2}}


def test_the_new_question_sets_totals_say_which_library_they_measured():
    page = search_page.newset_html(NEWSET, "2026-11-21T04:03:26+08:00")
    assert "新題庫成績" in page and "現在這份庫的成績" in page and "不是現在這份庫的成績" not in page
    row = re.search(r"<tr class=main>.*?</tr>", page, re.DOTALL).group(0)
    assert re.findall(r">(\d+)</td>", row) == ["83", "29", "48", "69", "44", "65", "77"]  # as written, then the agent's
    assert "77 / 83 題（93%）" in row and "共出了 94 題，其中 83 題庫裡有答案" in page and "9 題庫裡只有相關的資料" in page
    # measured on another library than the one in use: said on top, never left to be read as today's score
    old = search_page.newset_html(NEWSET, "2026-11-22T04:01:00+08:00")
    assert "不是現在這份庫的成績" in old and "2026-11-22 04:01" in old
    assert search_page.newset_html(None, "x") == "" and search_page.newset_html({"groups": []}, "x") == ""
    late = search_page.newset_html({**NEWSET, "b_asked": {**NEWSET["b_asked"], "expired": 2}}, "x")
    assert "其中 2 題有時效、量的時候已經過期，沒算" in late and "過期" not in page and "不加在一起" in page


def test_the_test_page_shows_the_new_sets_totals_when_the_file_is_beside_the_record(tmp_path, tmp_path_factory,
                                                                                    monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})
    w = world.build(tmp_path)
    ops = tmp_path_factory.mktemp("ops") / "ops.db"
    app = web.create_app(w.db, tmp_path, models=models(), today_fn=lambda: TODAY, ops_db=ops, record=False)
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        assert "新題庫成績" not in client.get("/try").text
        ops.with_name(web.NEWSET_FILE).write_text(json.dumps(NEWSET, ensure_ascii=False), encoding="utf-8")
        page = client.get("/try").text
        assert "新題庫成績" in page and "不是現在這份庫的成績" in page
        ops.with_name(web.NEWSET_FILE).write_text("{broken", encoding="utf-8")
        assert "新題庫成績" not in client.get("/try").text
