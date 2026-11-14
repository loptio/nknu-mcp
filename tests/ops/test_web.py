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
    for word in ("对题", "还有效", "对口", "115學年度第一學期線上申請停修", "讲什么的词", "追的文件", "/trace?ref=d3"):
        assert word in page.text, word
    assert page.headers["cache-control"] == "no-store"


def test_the_page_escapes_what_it_prints(lib, db):
    db.doc("dbad", "停修<script>alert(1)</script>", "停修 <b>注意</b>")
    db.conn.commit()
    page = lib.get("/search", params={"q": "停修 <script>alert(2)</script>"})
    assert "<script>alert" not in page.text and "&lt;script&gt;alert(1)" in page.text


def test_an_empty_search_page_is_the_form(lib):
    page = lib.get("/search")
    assert page.status_code == 200 and "<form" in page.text and "名次" not in page.text


def test_the_questions_are_offered_and_their_gold_is_followed(db, tmp_path):
    questions = tmp_path / "eval"
    questions.mkdir()
    (questions / "core20.json").write_text(json.dumps([{"id": "Q01", "question": "停修"},
                                                       {"id": "Q02", "question": "沒有標準答案的題"}]), encoding="utf-8")
    (questions / "noanswer22.json").write_text(json.dumps([{"id": "N01", "question": "退選"}]), encoding="utf-8")
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"Q01": {"docs": ["dx", "d3"]}, "N01": {"docs": ["dq"]}}), encoding="utf-8")
    with _client(tmp_path / "t.db", tmp_path, questions=questions, gold=gold) as client:
        home = client.get("/search").text
        assert "/search?qid=Q01" in home and "/search?qid=N01" in home and "qid=Q02" not in home
        page = client.get("/search", params={"qid": "Q01"}).text
        assert "标准答案" in page and "學生選課應行注意事項" in page
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
    for word in ("① 抓取", "② 导出", "③ 转文字", "④ 建库", "⑤ 检索", "115-1 停修公告", "公告頁日期欄", "註冊組",
                 "申請人：王OO。", "http://viewer.test/page?id=80", "/file?"):
        assert word in page.text, word


def test_the_trace_page_of_what_the_library_left_out(site):
    page = site.get("/trace", params={"ref": PAGER_URL}).text
    assert "没进库" in page and "列表的翻页" in page


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
    assert "2026-11-15" in home and "/search" in home and "/trace" in home


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


def test_the_followed_documents_share_one_table(lib):
    page = lib.get("/search", params={"q": "停修", "doc": "dx d3 none"}).text
    assert page.count("<table>") == 2  # the followed documents, then the results
    assert page.count("<td colspan='12'>") == 3


def test_an_error_inside_is_said_and_never_a_bare_500(site, monkeypatch):
    def broken(*args, **kwargs):
        raise TypeError("unhashable type: 'dict'")

    monkeypatch.setattr(web, "trace", broken)
    api = site.get("/api/trace", params={"ref": NOTICE_URL})
    assert api.status_code == 500 and "unhashable type" in api.json()["error"]
    page = site.get("/trace", params={"ref": NOTICE_URL})
    assert page.status_code == 500 and "出错了" in page.text and "unhashable type" in page.text


def test_an_archived_page_can_also_be_opened_in_a_tab_of_its_own(site):
    """Beside the frame: the frame's content is another origin's to the page, so a link is the way that always works."""
    page = site.get("/trace", params={"ref": NOTICE_URL}).text
    assert "<iframe class='orig' sandbox src='/file?" in page
    assert "target='_blank'>在新分页打开</a>" in page


# ---------------------------------------------------------------- after the review of 11-15

def test_the_search_page_says_what_ran_when_no_rerank_did(db, tmp_path):
    from nknu.retrieval.hybrid import HybridConfig

    with _client(tmp_path / "t.db", tmp_path, config=HybridConfig(rerank=False)) as client:
        page = client.get("/search", params={"q": "停修", "doc": "dx"}).text
    assert "没有重排" in page and "对题×" not in page and "进重排" not in page and "None" not in page


def test_a_merged_copy_on_another_site_is_shown_as_what_was_given(site):
    page = site.get("/trace", params={"ref": world.COPY_URL}).text
    assert "①～③ 是你给的那一份" in page and "範例處公告欄" in page


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
    assert "113 学年度起入学" in site.get("/trace", params={"ref": NOTICE_URL}).text


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
    assert "建库用的程序" in page and "nknu/content/build.py" in page and "重装" in page
    fresh = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(fresh, base_url=BASE) as client:
        assert "建库用的程序" not in client.get("/search").text


def test_a_file_the_pack_replaced_is_fine_once_the_pipeline_has_the_packs_own_version(tmp_path):
    """After the tools' commit reaches the pipeline's source too (the usual code sync), the pipeline's file is the
    installed copy's own: no difference to warn of (11-15, found right after the commit: it was compared with the
    version the pack was made from alone, so the warning would have come up falsely)."""
    work, code = _installed(tmp_path, changed={"nknu/retrieval/hybrid.py": "HYBRID = 1\nRECORDER = 1\n"})
    assert web.install_state(code, work / "src")["drift"] == []
