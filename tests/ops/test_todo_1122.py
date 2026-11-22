"""要處理 on 總覽 (nknu.ops.todo, 2026-11-22 evening): one list of what to look at, the new first, what the owner said
is known folded with its note, this machine only past its warning line, the student questions that dropped out of the
top 10 since the night before. The rows and the card that draws them (nknu.ops.panorama.todo_card). Made-up rows,
meters and measurements only."""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from nknu.ops import panorama, standing, todo, tonight_page
from nknu.ops.pipeline import BAD, WARN


def row(where: str, sev: str, *keys: str, href: str = "", reason: str = "", **more) -> dict:
    """A row as tonight_page.problems makes it: one issue a key."""
    return {"sev": sev, "where": where, "href": href, "state": "出錯" if sev == BAD else "留意", "reason": reason,
            "code": "", "unknown": False, "when": "", **more,
            "issues": [{"key": k, "sev": sev, "what": reason} for k in keys]}


def card(rows: list[dict], parts: dict | None = None, *, meters=(), known=None) -> LexborHTMLParser:
    """總覽's 要處理 card of these rows, as the front page draws it once the steps are read."""
    got = todo.collect(parts, rows, list(meters), known or {})
    return LexborHTMLParser(panorama.todo_card(got, False))


def texts(tree: LexborHTMLParser, css: str = "#todo > .pn-tlist li") -> list[str]:
    return [li.text() for li in tree.css(css)]


A_CRAWL = row("甲處", WARN, "site:a.example:crawl", href="/site?site=a.example", reason="有 3 個網址抓不到")
B_EXPORT = row("乙處", BAD, "site:b.example:export", href="/site?site=b.example", reason="導出失敗")
SERVE = row("查詢服務", BAD, "step:serve", href="#step-serve", reason="MCP 沒有回應")
BUILD = row("建庫", WARN, "part:build:held", href="#nights", reason="有 2 站沒進庫")


def test_the_new_come_first_and_the_standing_say_how_long():
    rows = [B_EXPORT, A_CRAWL, BUILD, SERVE]
    base = {"night": "2026-11-15", "rows": {r["key"]: r for r in standing.records([A_CRAWL, BUILD])},
            "runs": {"site:a.example:crawl": (2, False), "part:build:held": (1, True)}}
    parts = standing.split(rows, base)
    got = todo.collect(parts, rows, [], {})
    assert [x["where"] for x in got["open"]] == ["乙處", "查詢服務", "甲處", "建庫"]  # new, worse first; then standing
    assert [x["tag"] for x in got["open"]] == ["新出現", "新出現", "第 3 晚", "至少 2 晚"]
    assert got["night"] == "2026-11-15" and got["known"] == []
    assert [x["kind"] for x in got["open"]] == ["網站", "每晚流程", "網站", "每晚流程"]
    tree = LexborHTMLParser(panorama.todo_card(got, False))
    assert tree.css_first("h2 .pn-tcount").text() == "4" and "和 11-15 夜比" in tree.css_first(".pn-tsub").text()
    assert [li.css_first(".pn-tnew, .pn-tage").text() for li in tree.css(".pn-tlist li")] == [
        "新出現", "新出現", "第 3 晚", "至少 2 晚"]
    # no night before kept: every row as it is, the worse first as problems() sorted them, and no 新出現 said
    plain = todo.collect(None, tonight_page.problems({}, {"steps": []}, []) + rows, [], {})
    assert [x["tag"] for x in plain["open"]] == ["", "", "", ""] and plain["night"] == ""
    assert "新出現" not in panorama.todo_card(plain, False)


def test_a_part_or_a_step_links_into_tonight_and_a_site_to_its_page():
    # tonight_page.problems' links were 每晚運行's own anchors (#nights, #step-serve): on 總覽 they lead there
    got = todo.collect(None, [SERVE, BUILD, A_CRAWL], [], {})
    assert {x["where"]: x["href"] for x in got["open"]} == {
        "查詢服務": "/tonight#step-serve", "建庫": "/tonight#nights", "甲處": "/site?site=a.example"}
    links = {a.text(): a.attributes["href"] for a in card([SERVE, BUILD, A_CRAWL]).css(".pn-twhat a")}
    assert links == {"查詢服務": "/tonight#step-serve", "建庫": "/tonight#nights", "甲處": "/site?site=a.example"}


def test_a_known_row_is_folded_with_its_note_and_not_counted():
    known = {"site:b.example:export": "學校網站改版中", "step:serve": ""}
    got = todo.collect(None, [B_EXPORT, SERVE, A_CRAWL], [], known)
    assert [x["where"] for x in got["open"]] == ["甲處"]
    assert [(x["where"], x["note"]) for x in got["known"]] == [("乙處", "學校網站改版中"), ("查詢服務", "")]
    tree = card([B_EXPORT, SERVE, A_CRAWL], known=known)
    assert tree.css_first("h2 .pn-tcount").text() == "1" and len(texts(tree)) == 1
    folded = tree.css_first("details.pn-tknown")
    assert folded.css_first("summary").text() == "已知 2 件，不算在裡面"
    assert "已知：學校網站改版中" in folded.text() and folded.text().count("已知：") == 1  # an empty note says nothing
    # every row known: nothing to handle, said in one line, the known ones still there to be taken off again
    calm = card([B_EXPORT], known=known)
    assert "沒有要處理的" in calm.css_first(".pn-tnone").text() and calm.css_first("h2 .pn-tcount") is None
    assert "乙處" in calm.css_first("details.pn-tknown").text()


def test_a_sites_key_covers_all_its_issues_and_one_issue_known_is_not_enough():
    both = row("甲處", WARN, "site:a.example:crawl", "site:a.example:docs", href="/site?site=a.example",
               reason="有 3 個網址抓不到（還有 1 件）")
    assert todo.collect(None, [both], [], {"site:a.example": "改版中"})["known"][0]["note"] == "改版中"
    half = todo.collect(None, [both], [], {"site:a.example:crawl": "改版中"})
    assert [x["where"] for x in half["open"]] == ["甲處"] and half["known"] == []  # its docs issue still counts
    # a site's key is the site's own: neither another site's issues nor a step or a part of the night
    other = todo.collect(None, [B_EXPORT, SERVE, BUILD], [], {"site:a.example": "改版中"})
    assert len(other["open"]) == 3 and other["known"] == []
    prefix = todo.collect(None, [row("甲處二", WARN, "site:a.example2:docs")], [], {"site:a.example": "x"})
    assert prefix["known"] == []  # the key is the site's, not a prefix of another site's name


def test_this_machine_is_a_row_only_past_its_warning_line():
    meters = [{"label": "C 槽", "tone": "ok", "pct": 50.0, "detail": "還剩 300 GB，共 600"},
              {"label": "D 槽", "tone": WARN, "pct": 91.03448275862068, "detail": "還剩 52 GB，共 580"},
              {"label": "記憶體", "tone": BAD, "pct": 97.4, "detail": ""},
              {"label": "顯示卡", "tone": "none", "pct": 0.0, "detail": "讀不到"}]
    rows = todo.machine_rows(meters)
    assert [(x["where"], x["sev"], x["reason"]) for x in rows] == [
        ("D 槽", WARN, "用了 91%：還剩 52 GB，共 580"), ("記憶體", BAD, "用了 97%")]
    assert {x["kind"] for x in rows} == {"這台電腦"} and [x["issues"][0]["key"] for x in rows] == [
        "machine:D 槽", "machine:記憶體"]
    got = todo.collect(None, [], meters, {})
    assert [x["where"] for x in got["open"]] == ["記憶體", "D 槽"]  # 出錯 before 留意
    assert todo.collect(None, [], meters, {"machine:記憶體": "在跑實驗"})["known"][0]["where"] == "記憶體"


STUDENT = {"n": 3, "prev": {"measured": "2026-11-21T04:00:00", "totals": {}},
           "questions": [
               {"id": "S01", "q": "怎麼申請停修", "raw": 15, "agent": 2, "prev_raw": 3, "prev_agent": 2},
               {"id": "S02", "q": "宿舍什麼時候開放", "raw": None, "agent": None, "prev_raw": 1, "prev_agent": 12},
               {"id": "S03", "q": "畢業門檻", "raw": 4, "agent": 30, "prev_raw": 5, "prev_agent": 10},
               {"id": "S04", "q": "抵免學分", "raw": 11, "agent": 1, "prev_raw": 11, "prev_agent": None}]}


def test_an_unknown_code_is_shown_as_it_is_on_the_card():
    # spec 2026-11-17-panels-v3 八: a code nobody knows is listed as written and never read as normal; on 每晚運行's
    # list it was (problem_html), and the card that took the list over showed an empty line for it
    odd = row("建庫", WARN, "part:build:exploded · mystery_code_7", href="#nights", code="exploded · mystery_code_7",
              unknown=True)
    li = card([odd]).css_first(".pn-tlist li")
    assert li.attributes["data-sev"] == WARN and li.css_first(".pn-tcode").text() == "exploded · mystery_code_7"
    assert "沒見過" in li.text() and li.css_first(".pn-twhat a").attributes["href"] == "/tonight#nights"
    assert card([A_CRAWL]).css_first(".pn-tcode") is None  # no code, nothing drawn for it


def test_the_card_folds_past_eight_and_waits_while_the_steps_are_read():
    many = [row(f"站{i}", WARN, f"site:s{i}.example:docs", href=f"/site?site=s{i}.example", reason="少了")
            for i in range(panorama.TODO_SHOWN + 3)]
    tree = card(many)
    assert len(texts(tree)) == panorama.TODO_SHOWN and tree.css_first("h2 .pn-tcount").text() == str(len(many))
    rest = tree.css_first("details.pn-tmore:not(.pn-tknown)")
    assert rest.css_first("summary").text() == "還有 3 件" and len(rest.css("li")) == 3  # folded, never dropped
    waiting = panorama.todo_card(todo.collect(None, many, [], {}), True)
    assert "正在讀各步驟的紀錄" in waiting and "站0" not in waiting
    assert "&lt;b&gt;" in panorama.todo_card(todo.collect(None, [row("<b>壞</b>", WARN, "step:x")], [], {}), False)


def test_the_header_counts_what_is_to_handle():
    def pill(snap: dict, got: dict | None) -> str:
        return LexborHTMLParser(panorama.header_bar(snap, {}, {}, got)).css_first(".pn-status").text()

    read = {"steps": [{"key": "crawl"}]}
    assert pill(read, todo.collect(None, [A_CRAWL, B_EXPORT], [], {})) == "要處理 2 件"
    assert pill(read, todo.collect(None, [], [], {})) == "沒有要處理的"
    assert pill({"steps": []}, None) == "正在讀各步驟"  # just restarted: nothing known, nothing called normal
    lit = panorama.header_bar(read, {}, {}, todo.collect(None, [B_EXPORT], [], {}))
    assert "pn-pill-bad" in lit and "href='#todo'" in lit


def test_the_owner_marks_a_row_known_on_the_page_and_takes_it_off_again(tmp_path):
    """要處理's rows marked known on 總覽 itself (2026-11-22 night; it was a file edited by hand on the owner's word):
    kept in ops.db, a site's key covers all of its issues, taken off with 取消已知; only from the panel's own page."""
    from starlette.testclient import TestClient

    from nknu.ops import web

    store = todo.Known(tmp_path / "ops.db")
    assert store.load() == {} and todo.Known(None).load() == {}
    store.mark(["site:a.example"], "改版中")
    assert todo.Known(tmp_path / "ops.db").load() == {"site:a.example": "改版中"}  # survives a new process
    store.unmark(["site:a.example"])
    assert store.load() == {}

    app = web.create_app(tmp_path / "t.db", tmp_path, ops_db=tmp_path / "ops.db", record=False)
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        here = {"origin": "http://127.0.0.1:8878"}
        got = client.post("/todo/known", data={"keys": "step:serve site:b:docs", "note": "週末停機"}, headers=here,
                          follow_redirects=False)
        assert got.status_code == 303 and got.headers["location"] == "/#todo"
        assert todo.Known(tmp_path / "ops.db").load() == {"step:serve": "週末停機", "site:b:docs": "週末停機"}
        client.post("/todo/known", data={"keys": "step:serve", "off": "1"}, headers=here, follow_redirects=False)
        assert todo.Known(tmp_path / "ops.db").load() == {"site:b:docs": "週末停機"}
        away = client.post("/todo/known", data={"keys": "site:c:docs"}, headers={"origin": "https://evil.example"},
                           follow_redirects=False)
        none = client.post("/todo/known", data={"keys": "site:c:docs"}, follow_redirects=False)
        assert away.status_code == none.status_code == 403 and "site:c:docs" not in todo.Known(tmp_path / "ops.db").load()


def test_a_row_carries_its_buttons():
    open_ = todo.collect(None, [B_EXPORT], [], {})
    html = panorama.todo_card(open_, False)
    assert "action='/todo/known'" in html and "<summary>標成已知</summary>" in html
    assert "<button>取消已知</button>" not in html
    known = todo.collect(None, [B_EXPORT], [], {k["key"]: "已知的" for k in B_EXPORT["issues"]})
    html = panorama.todo_card(known, False)
    assert "<button>取消已知</button>" in html and "name='off' value='1'" in html and "已知：已知的" in html
