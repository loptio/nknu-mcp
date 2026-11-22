"""plan 2026-11-15 T5: 检视台, the two pages over nknu.ops.explain and nknu.ops.trace and the JSON the commands ask for
through the tunnel. It holds students' names like the library it reads, so: read-only, GET only, and only for a request
whose Host names this machine."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from nknu.ops import web
from nknu.ops.explain import explain_search
from nknu.ops.trace import trace
from nknu.retrieval import vectors
from nknu.retrieval.server import aliases_of, connect
from tests.ops import world
from tests.ops.library import models
from tests.ops.world import FORM_URL, NOTICE_URL, PAGER_URL, doc_id
from tests.retrieval.test_search import TODAY

BASE = "http://127.0.0.1:8878"


def _client(db_path, root, **kw) -> TestClient:
    app = web.create_app(db_path, root, models=models(), today_fn=lambda: TODAY, **kw)
    return TestClient(app, base_url=BASE)


@pytest.fixture
def lib(db, tmp_path):
    """The made-up library of the explain tests, served."""
    with _client(tmp_path / "t.db", tmp_path) as client:
        yield client


@pytest.fixture
def site(tmp_path, monkeypatch):
    """The made-up site of the trace tests (export, cache, corpus, library), served."""
    monkeypatch.setattr(vectors, "_CACHE", {})
    made = world.build(tmp_path)
    with _client(made.db, made.root, viewer_url="http://viewer.test") as client:
        client.world = made
        yield client
    made.conn.close()


# ---------------------------------------------------------------- who may ask

@pytest.mark.parametrize("path", ["/", "/search?q=停修", "/trace?ref=d3", "/api/search?q=停修", "/api/trace?ref=d3"])
def test_another_host_gets_403(lib, path):
    assert lib.get(path, headers={"host": "evil.example:8878"}).status_code == 403
    assert lib.get(path, headers={"host": "localhost:18878"}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/search", "/api/search", "/api/trace", "/file"])
def test_only_get(lib, path):
    assert lib.post(path, data={"q": "停修"}).status_code == 405
    assert lib.delete(path).status_code == 405


def test_an_unknown_path_is_404(lib):
    assert lib.get("/nothing").status_code == 404


# ---------------------------------------------------------------- the search explained

def test_the_api_is_the_explanation_itself(lib, db):
    got = lib.get("/api/search", params={"q": "停修", "doc": ["dx", "none"], "limit": "5"})
    assert got.status_code == 200 and got.headers["content-type"].startswith("application/json")
    want = explain_search(db.conn, "停修", today=TODAY, aliases=aliases_of(db.conn), limit=5, follow=["dx", "none"],
                          **models())
    assert got.json() == json.loads(json.dumps(want))


def test_without_explain_the_api_gives_the_mcp_answer(lib):
    answer = lib.get("/api/search", params={"q": "停修", "explain": "0"}).json()
    assert answer["results"] and "id" in answer["results"][0]


def test_the_search_page_shows_every_part(lib):
    page = lib.get("/search", params={"q": "停修", "doc": "dx"})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    for word in ("對題", "還有效", "對口", "115學年度第一學期線上申請停修", "查詢詞", "指定的文件", "/trace?ref=d3"):
        assert word in page.text, word
    assert page.headers["cache-control"] == "no-store"


def test_the_page_escapes_what_it_prints(lib, db):
    db.doc("dbad", "停修<script>alert(1)</script>", "停修 <b>注意</b>")
    db.conn.commit()
    page = lib.get("/search", params={"q": "停修 <script>alert(2)</script>"})
    assert "<script>alert" not in page.text and "&lt;script&gt;alert(1)" in page.text


def test_an_empty_search_page_is_try_with_its_board(lib):
    # structure design 2026-11-17 section 二: 搜尋測試 moved into 試用; asked nothing, 試用 itself
    page = lib.get("/search", follow_redirects=False)
    assert page.status_code == 307 and page.headers["location"] == "/try#board"
    page = lib.get("/search")
    assert page.status_code == 200 and "aria-current='page'>測試</a>" in page.text and "名次" not in page.text


def test_the_old_addresses_go_where_their_pages_went(lib):
    def to(path: str, **params) -> str:
        got = lib.get(path, params=params, follow_redirects=False)
        assert got.status_code == 307, (path, params)
        return got.headers["location"]

    assert to("/search", q="停修", doc="d3") == "/try?tab=why&q=%E5%81%9C%E4%BF%AE&doc=d3#result"
    assert to("/search", qid="Q01", limit="20") == "/try?tab=why&qid=Q01&limit=20#result"
    assert to("/trace") == "/doc" and to("/trace", ref="停修") == "/doc?q=%E5%81%9C%E4%BF%AE"
    assert to("/pipeline/site", site="a.example/x") == "/site?site=a.example%2Fx"
    assert to("/check", url="教務") == "/doc?q=%E6%95%99%E5%8B%99"  # words: 文件搜尋 finds them
    assert lib.get("/check", follow_redirects=False).status_code == 200  # nothing asked: 全校網頁核查 itself (11-18)
    assert to("/check", site="a.example", only="gap") == "/site?site=a.example&only=gap#check"
    assert lib.get("/trace", params={"ref": "d3"}, follow_redirects=False).status_code == 200  # a trace stays
    assert to("/site") == "/sites"  # no site asked: 網站, where every site is (the panel's own page since 11-21)


def test_the_second_tab_of_try_explains_the_search_on_the_library(lib):
    page = lib.get("/try", params={"tab": "why", "q": "停修", "doc": "dx"}).text
    assert "aria-current='page'>測試</a>" in page and "<a aria-current='page' data-seam='why'>為什麼這樣排</a>" in page
    assert "class='rhead'" in page and "id='r-dx'" in page and "class='star'" in page  # search_page's rows, dx marked
    assert "<form class='ask follow' action='/try'" in page and "name='doc' value='dx'" in page
    assert "<title>停修 · 測試</title>" in page


def test_the_questions_are_offered_and_their_gold_is_followed(db, tmp_path):
    questions = tmp_path / "eval"
    questions.mkdir()
    (questions / "core20.json").write_text(json.dumps([{"id": "Q01", "question": "停修"},
                                                       {"id": "Q02", "question": "沒有標準答案的題"}]), encoding="utf-8")
    (questions / "noanswer22.json").write_text(json.dumps([{"id": "N01", "question": "退選"}]), encoding="utf-8")
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"Q01": {"docs": ["dx", "d3"]}, "N01": {"docs": ["dq"]}}), encoding="utf-8")
    with _client(tmp_path / "t.db", tmp_path, questions=questions, gold=gold) as client:
        home = client.get("/try").text
        assert "/try?tab=why&amp;qid=Q01#result" in home and "/try?tab=why&amp;qid=N01#result" in home
        assert "qid=Q02" not in home
        page = client.get("/try", params={"tab": "why", "qid": "Q01"}).text
        assert "標準答案" in page and "學生選課應行注意事項" in page
        report = client.get("/api/search", params={"qid": "Q01"}).json()
        assert report["query"] == "停修" and [f["doc_id"] for f in report["follow"]] == ["dx", "d3"]
        assert client.get("/search", params={"qid": "Q99"}).status_code == 200


def test_a_bad_limit_is_the_default(lib):
    assert len(lib.get("/api/search", params={"q": "停修", "limit": "abc"}).json()["rows"]) <= 10


# ---------------------------------------------------------------- one document followed

def test_the_trace_api_is_the_trace_itself(site):
    made = site.world
    got = site.get("/api/trace", params={"ref": NOTICE_URL}).json()
    conn = connect(made.db)
    want = trace(conn, NOTICE_URL, root=made.root, today=TODAY)
    conn.close()
    for part in ("resolved", "export", "crawl", "totext", "record", "library", "left_out", "missing"):
        assert got[part] == json.loads(json.dumps(want[part])), part
    assert got["search"]["rank"] == 1


def test_the_trace_page_shows_the_five_steps(site):
    page = site.get("/trace", params={"ref": NOTICE_URL})
    assert page.status_code == 200
    for word in ("① 抓取", "② 匯出", "③ 轉文字", "④ 建庫", "⑤ 搜尋", "115-1 停修公告", "公告頁日期欄", "註冊組",
                 "申請人：王OO。", "/check?id=80", "存檔裡的原樣", "/file?"):
        assert word in page.text, word


def test_the_trace_page_of_what_the_library_left_out(site):
    page = site.get("/trace", params={"ref": PAGER_URL}).text
    assert "沒進庫" in page and "列表的翻頁" in page


def test_an_empty_trace_page_is_the_form(site):
    page = site.get("/trace")
    assert page.status_code == 200 and "<form" in page.text and "① 抓取" not in page.text


def test_the_original_file_is_served_from_the_export_folder_only(site):
    got = site.get("/file", params={"folder": world.EXPORT, "path": "files/0003-停修申請表.pdf"})
    assert got.status_code == 200 and got.content == b"%PDF-1.7 form"
    assert got.headers["content-type"] == "application/pdf"
    for folder, path in ((world.EXPORT, "../../../library.db"), (world.EXPORT, "/etc/hosts"), ("research", "x"),
                         (world.EXPORT, "files/nothing.pdf"), (world.EXPORT, ""), ("", "files/0003-停修申請表.pdf")):
        assert site.get("/file", params={"folder": folder, "path": path}).status_code in (403, 404), (folder, path)


def test_an_archived_page_is_served_so_that_it_loads_nothing(site):
    got = site.get("/file", params={"folder": world.EXPORT, "path": "pages/0001-news-停修公告.html"})
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/html")
    policy = got.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "sandbox" in policy and "form-action 'none'" in policy


def test_a_database_built_before_pipeline_8_still_draws(site):
    made = site.world
    for column in ("tier", "site", "unit_line", "date_line", "expires", "valid_ay", "rank_date", "rank_undated"):
        made.conn.execute(f"ALTER TABLE docs DROP COLUMN {column}")
    made.conn.executescript("DROP TABLE sites; DROP TABLE doc_alias; DROP TABLE doc_scope;")
    made.conn.commit()
    assert site.get("/trace", params={"ref": doc_id(FORM_URL)}).status_code == 200
    assert site.get("/search", params={"q": "停修"}).status_code == 200
    assert site.get("/").status_code == 200


def test_the_home_page_says_which_library_it_reads(site):
    home = site.get("/").text
    assert "2026-11-15" in home and "href='/try'" in home and "href='/doc'" in home


# ---------------------------------------------------------------- python -m nknu.ops.web

def test_main_serves_on_the_host_and_port_given(tmp_path, monkeypatch, db):
    seen = {}
    monkeypatch.setattr(web.uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    assert web.main(["--db", str(tmp_path / "t.db"), "--root", str(tmp_path), "--host", "0.0.0.0", "--port", "8878",
                     "--viewer-url", "http://127.0.0.1:18877"]) == 0
    assert (seen["host"], seen["port"]) == ("0.0.0.0", 8878) and seen["app"] is not None


def test_main_refuses_a_database_that_is_not_there(tmp_path, capsys):
    assert web.main(["--db", str(tmp_path / "none.db"), "--root", str(tmp_path)]) == 2
    assert "找不到" in capsys.readouterr().err


def test_every_followed_document_is_one_row_and_a_ruler_before_the_rows_says_where_they_stand(lib):
    """The third round of 11-15: a followed document is one of the rows (marked, wherever it stopped) instead of a
    second list; above the rows a verdict and a ruler say where they all stand; a document the library lacks is said
    so, not dropped."""
    page = lib.get("/search", params={"q": "停修", "doc": "dx d3 none"}).text
    assert page.index("指定的文件") < page.index("class='ruler") < page.index("class='rhead'")
    assert page.count("id='r-dx'") == 1 and page.count("id='r-d3'") == 1  # once each, not once per list
    assert page.count("class='star'") == 2  # the two the library holds are marked in their rows
    assert "href='#r-d3'" in page and "href='#r-dx'" in page  # the ruler's markers lead to their rows
    assert "庫裡沒有</span>" in page and "庫裡沒有這一份" in page


def test_a_result_is_a_bar_of_its_scores_parts_and_names_only_what_moved_it(lib):
    page = lib.get("/search", params={"q": "停修"}).text
    assert "<details class='row" in page and "class='bar'" in page and "class='calc'" in page
    notes = [part.split("</span></span>")[0] for part in page.split("class='bnotes'>")[1:]]
    assert notes and not any("×1.00" in n or "+0.00" in n for n in notes)  # a part that changed nothing is not named
    assert "還有效 ×0." in "".join(notes)  # last year's notices are: the discount is said under their bars


def test_a_test_question_says_first_where_its_gold_answer_stands(db, tmp_path):
    questions = tmp_path / "eval"
    questions.mkdir()
    (questions / "core20.json").write_text(json.dumps([{"id": "Q01", "question": "停修"}]), encoding="utf-8")
    (questions / "noanswer22.json").write_text("[]", encoding="utf-8")
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"Q01": {"docs": ["dx"]}}), encoding="utf-8")
    with _client(tmp_path / "t.db", tmp_path, questions=questions, gold=gold) as client:
        listed = client.get("/search").text
        assert "<b>Q01</b>停修</span>" in listed  # the question's words, not its code alone
        page = client.get("/search", params={"qid": "Q01"}).text
    assert "標準答案在第" in page or "沒有標準答案" in page
    assert "<select name='qid'" in page and "<option value='Q01' selected>" in page


def test_words_that_are_no_id_find_documents_by_title(site):
    # 文件 (/doc) finds them since the structure design of 2026-11-17; the trace's box sends words there
    page = site.get("/trace", params={"ref": "停修"}).text
    assert "庫裡的文件" in page and "/trace?ref=" in page and "① 抓取" not in page
    assert "aria-current='page'>文件搜尋</a>" in page and "value='停修'" in page
    none = site.get("/trace", params={"ref": "沒有這幾個字"}).text
    assert "標題裡沒有「沒有這幾個字」的文件" in none


def test_the_top_bar_is_the_panels_one_bar(db, tmp_path):
    # since 11-17 every panel shows nknu/ops/theme/bar.py's bar: 檢視台's own pages by path, the others by address
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(app, base_url=BASE) as client:
        pages = {path: client.get(path).text for path in ("/", "/tonight", "/try", "/doc", "/trace?ref=d3")}
    for path, page in pages.items():
        assert "href='/sites'" in page and "href='http://127.0.0.1:18001/'" in page, path
        assert "href='/doc'" in page and "href='/'" in page and "href='/tonight'" in page, path
        # 11-22: the tools in the same row (資料庫, another panel, with the arrow out), 更多 for a narrow window
        assert "class='nk-bar'" in page and "class='nk-tool'" in page and "<summary>更多" in page, path
        assert ">資料庫<svg" in page and "class='nk-mode'" in page and "翻庫" not in page, path
    marked = {path: page.split("aria-current='page'>", 1)[1].split("<", 1)[0] for path, page in pages.items()}
    assert marked == {"/": "總覽", "/tonight": "每晚運行", "/try": "測試", "/doc": "文件搜尋", "/trace?ref=d3": "文件搜尋"}
    assert "data-tip='每個網站收了多少、缺什麼" in pages["/tonight"]  # what each entry is floats over its name


def test_a_trace_opens_with_the_five_steps_state(site):
    page = site.get("/trace", params={"ref": NOTICE_URL}).text
    strip = page[page.index("<ol class='steps'>"):page.index("</ol>")]
    assert strip.count("<li>") == 5 and strip.count("<a class='ok'") == 5 and "第 1 名" in strip
    assert page.index("<h1>115-1 停修公告</h1>") < page.index("<ol class='steps'>")  # the document itself first


def test_an_error_inside_is_said_and_never_a_bare_500(site, monkeypatch):
    def broken(*args, **kwargs):
        raise TypeError("unhashable type: 'dict'")

    monkeypatch.setattr(web, "trace", broken)
    api = site.get("/api/trace", params={"ref": NOTICE_URL})
    assert api.status_code == 500 and "unhashable type" in api.json()["error"]
    page = site.get("/trace", params={"ref": NOTICE_URL})
    assert page.status_code == 500 and "出錯了" in page.text and "unhashable type" in page.text


def test_an_archived_page_can_also_be_opened_in_a_tab_of_its_own(site):
    """Beside the frame: the frame's content is another origin's to the page, so a link is the way that always works."""
    page = site.get("/trace", params={"ref": NOTICE_URL}).text
    assert "<iframe class='orig' sandbox src='/file?" in page
    assert "target='_blank'>新分頁打開</a>" in page


# ---------------------------------------------------------------- after the review of 11-15

def test_the_search_page_says_what_ran_when_no_rerank_did(db, tmp_path):
    from nknu.retrieval.hybrid import HybridConfig

    with _client(tmp_path / "t.db", tmp_path, config=HybridConfig(rerank=False)) as client:
        page = client.get("/search", params={"q": "停修", "doc": "dx"}).text
    assert "沒有重排" in page and "對題×" not in page and "進重排" not in page and "None" not in page


def test_a_merged_copy_on_another_site_is_shown_as_what_was_given(site):
    page = site.get("/trace", params={"ref": world.COPY_URL}).text
    assert "①～③ 是你給的那一份" in page and "範例處公告欄" in page


def test_a_pdf_and_a_picture_sit_beside_their_text(site):
    """The spec's 「原档和文字并排」: a PDF in the browser's own viewer, a picture as a picture; any other file is a
    link (review 11-15: a PDF was only a link)."""
    pdf = site.get("/trace", params={"ref": FORM_URL}).text
    assert "<iframe class='orig' src='/file?" in pdf and "sandbox src='/file?" not in pdf
    picture = site.get("/trace", params={"ref": world.POSTER_URL}).text
    assert "<img class='orig' src='/file?" in picture
    assert site.get("/file", params={"folder": world.EXPORT, "path": "files/0011-海報.png"}).headers[
        "content-type"] == "image/png"


def test_the_cohort_is_on_the_trace_page(site):
    assert "113 學年度起入學" in site.get("/trace", params={"ref": NOTICE_URL}).text


@pytest.mark.parametrize("limit", ["²", "-3", "9999999999999999999999", "1e3", ""])
def test_a_limit_that_is_no_small_number_is_the_default(lib, limit):
    got = lib.get("/api/search", params={"q": "停修", "limit": limit})
    assert got.status_code == 200 and got.json()["limit"] in (10, 50)


@pytest.mark.parametrize("host", ["[::1]evil.example", "[::1].evil.example:8878", "127.0.0.1.evil.example", "localhost.x"])
def test_a_host_that_only_begins_like_this_machine_is_refused(lib, host):
    assert lib.get("/", headers={"host": host}).status_code == 403


def test_the_bracketed_loopback_is_this_machine(lib):
    assert lib.get("/", headers={"host": "[::1]:8878"}).status_code == 200


def _installed(tmp_path, changed: dict[str, str] | None = None, base: str | None = None):
    """A pipeline source tree and an installed copy of it (as install.sh leaves one), with files changed since."""
    import hashlib

    work, code = tmp_path / "work", tmp_path / "work" / "inspect-code"
    files = {"nknu/content/build.py": "BUILD = 1\n", "nknu/retrieval/respond.py": "RESPOND = 1\n",
             "nknu/retrieval/hybrid.py": "HYBRID = 1\n", "nknu/ops/web.py": "WEB = 1\n"}
    for tree in (work / "src", code / "src"):
        for name, text in files.items():
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text(text, encoding="utf-8")
    (code / "src/nknu/retrieval/hybrid.py").write_text("HYBRID = 1\nRECORDER = 1\n", encoding="utf-8")  # the pack's
    sha = hashlib.sha256(b"HYBRID = 1\n").hexdigest() if base is None else base
    (code / "BASE.sha256").write_text(f"{sha}  src/nknu/retrieval/hybrid.py\n", encoding="utf-8")
    (code / "INSTALLED").write_text("2026-11-15T03:40:00+08:00 d021d8fe06995acd\n", encoding="utf-8")
    for name, text in (changed or {}).items():
        (work / "src" / name).write_text(text, encoding="utf-8")
    return work, code


def test_an_installed_copy_that_matches_the_pipelines_code_says_when_it_was_installed(tmp_path):
    work, code = _installed(tmp_path)
    state = web.install_state(code, work / "src")
    assert state == {"installed": "2026-11-15T03:40:00+08:00", "pack": "d021d8fe06995acd", "drift": []}


def test_a_pipeline_file_changed_since_the_install_is_named(tmp_path):
    work, code = _installed(tmp_path, changed={"nknu/content/build.py": "BUILD = 2\n"})
    assert web.install_state(code, work / "src")["drift"] == ["nknu/content/build.py"]


def test_a_file_the_pack_replaced_counts_when_the_pipelines_own_moved_on(tmp_path):
    work, code = _installed(tmp_path, changed={"nknu/retrieval/hybrid.py": "HYBRID = 2\n"})
    assert web.install_state(code, work / "src")["drift"] == ["nknu/retrieval/hybrid.py"]


def test_a_tree_that_is_no_install_has_no_state(tmp_path):
    assert web.install_state(tmp_path, tmp_path / "src") is None


def test_the_pages_warn_when_the_installed_copy_lags_the_pipelines_code(db, tmp_path):
    work, code = _installed(tmp_path, changed={"nknu/content/build.py": "BUILD = 2\n"})
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY, code_root=code,
                         pipeline_src=work / "src")
    with TestClient(app, base_url=BASE) as client:
        page = client.get("/search").text
    assert "建庫的程式不一樣" in page and "nknu/content/build.py" in page and "重裝" in page  # the files float
    fresh = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(fresh, base_url=BASE) as client:
        assert "建庫的程式不一樣" not in client.get("/search").text


def test_a_file_the_pack_replaced_is_fine_once_the_pipeline_has_the_packs_own_version(tmp_path):
    """After the tools' commit reaches the pipeline's source too (the usual code sync), the pipeline's file is the
    installed copy's own: no difference to warn of (11-15, found right after the commit: it was compared with the
    version the pack was made from alone, so the warning would have come up falsely)."""
    work, code = _installed(tmp_path, changed={"nknu/retrieval/hybrid.py": "HYBRID = 1\nRECORDER = 1\n"})
    assert web.install_state(code, work / "src")["drift"] == []


# ---------------------------------------------------------------- in Traditional Chinese (owner 11-15)

def test_the_pages_are_in_traditional_chinese(lib):
    """Owner 11-15: 「可视化dashboard全部换繁体字」. The pages say so to the browser and ask for Traditional fonts first."""
    for page in (lib.get("/").text, lib.get("/search", params={"q": "停修"}).text, lib.get("/trace").text):
        assert "<html lang='zh-Hant-TW'>" in page and '"PingFang TC", "Microsoft JhengHei", "Noto Sans CJK TC"' in page
        assert "zh-Hans" not in page and "PingFang SC" not in page


def test_what_the_crawl_and_the_export_wrote_is_shown_in_traditional_chinese(site):
    """The manifest's Simplified values are converted where a page shows them; the report keeps them as written."""
    notice = site.get("/trace", params={"ref": NOTICE_URL}).text
    assert "<dt>日期來源</dt><dd>詳細頁的日期欄</dd>" in notice
    pager = site.get("/trace", params={"ref": PAGER_URL}).text
    assert "<small>網頁 · 列表翻頁</small>" in pager and "<dt>頁別</dt><dd>列表翻頁</dd>" in pager
    assert "分類：default(列表翻頁)" in pager
    assert site.get("/api/trace", params={"ref": PAGER_URL}).json()["export"]["row"]["页别"] == "列表翻页"
    banner = site.get("/trace", params={"ref": world.MENU_URL}).text
    assert "<dt>結果</dt><dd><span class='pill warn'>沒抓到</span> <small>（HTTP 404）</small></dd>" in banner
    assert "<dt>類型</dt><dd>圖片</dd>" in banner


def test_a_test_question_is_shown_in_traditional_chinese_and_searched_as_written(db, tmp_path):
    """The list and the picker show a question in Traditional characters; the search box keeps, and the search runs,
    the question as its file has it."""
    questions = tmp_path / "eval"
    questions.mkdir()
    (questions / "core20.json").write_text(json.dumps([{"id": "Q01", "question": "怎么申请停修"}]), encoding="utf-8")
    (questions / "noanswer22.json").write_text("[]", encoding="utf-8")
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"Q01": {"docs": ["dx"]}}), encoding="utf-8")
    with _client(tmp_path / "t.db", tmp_path, questions=questions, gold=gold) as client:
        listed = client.get("/try").text
        page = client.get("/try", params={"tab": "why", "qid": "Q01"}).text
        report = client.get("/api/search", params={"qid": "Q01"}).json()
    assert "<b>Q01</b>怎麼申請停修</span>" in listed and "怎么申请停修" not in listed
    assert "Q01　怎麼申請停修</option>" in page and "<title>怎么申请停修 · 測試</title>" in page
    assert report["query"] == "怎么申请停修"


def test_the_words_the_index_folds_to_simplified_are_shown_in_traditional(lib):
    """The keyword index reads 化學系 選課 as 化学、学系、选课: the words the page shows the question was read as are
    in Traditional, as the explanation's sentences are, and say which of them a candidate must hold (選課: what the
    question is about) and which only add (the unit's name); the search box keeps the question and the report keeps
    the index's words."""
    page = lib.get("/search", params={"q": "化學系 選課"}).text
    read = page.split("<div class='words'>")[1].split("</div>")[0]
    assert [w.split(">")[1] for w in read.split("</span>")[:3]] == ["化學", "學系", "選課"]
    assert read.count("class='w plain'") == 2 and read.count("class='w must'") == 1
    assert "class='w must' data-tip='講什麼的詞：候選文件至少要有一個'>選課</span>" in read
    assert "化学" not in read and "学系" not in read and "选课" not in read
    assert "value='化學系 選課'" in page
    report = lib.get("/api/search", params={"q": "化學系 選課"}).json()
    assert "化学" in report["understood"]["words"] and report["understood"]["topic"] == ["选课"]


def test_the_trace_page_leads_to_the_page_check_of_its_address(site):
    """design 2026-11-16-page-check-design.md section 一: a document with an address offers 「核查這一頁」."""
    page = site.get("/trace", params={"ref": NOTICE_URL}).text
    assert "/check?url=" in page and "核查這一頁" in page and "aria-current='page'>文件搜尋</a>" in page


def test_the_sites_overview_is_a_page_of_the_panel_itself(lib):
    # 2026-11-21: 資料總覽 was a service and a port of its own; it is /sites here, computed in its own thread, so a
    # request only hands out what that thread last made (the waiting page until the first computation ends)
    first = lib.get("/sites")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    stamp = lib.get("/sites/stamp").json()
    assert set(stamp) == {"stamp", "built", "state", "error"}
    state = lib.app.state.sites
    if state.page is None:  # the made-up library of this test is not one the overview can read: said, not a 500
        assert "正在統計" in first.text and lib.get("/sites/data.json").status_code == 404
    with state.lock:
        state.page, state.page_gz, state.data = b"<p>sites</p>", __import__("gzip").compress(b"<p>sites</p>"), {"k": 1}
    assert lib.get("/sites").text == "<p>sites</p>" and lib.get("/sites/data.json").json() == {"k": 1}
    assert lib.get("/sites", headers={"Accept-Encoding": "gzip"}).headers["content-encoding"] == "gzip"


def test_the_log_route_answers_a_known_name_and_nothing_else(lib):
    # 2026-11-21: the end of a step's log, asked for by the home's folds; a name of pipeline.LOG_FILES, never a path
    got = lib.get("/api/log", params={"name": "ingest"})
    assert got.status_code == 200 and got.json()["file"] == "ingest.log" and got.json()["lines"] == []
    assert got.headers["cache-control"] == "no-store"
    for bad in ("", "passwd", "../ingest.log", "/etc/passwd"):
        assert lib.get("/api/log", params={"name": bad}).status_code == 404, bad
    assert lib.get("/api/log", params={"name": "ingest"}, headers={"host": "evil.example"}).status_code == 403
