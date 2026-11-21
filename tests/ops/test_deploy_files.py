"""plan 2026-11-15 T7: what the lab PC runs for the inspection tools. The files are configuration, so the tests pin
what a mistake in them would cost: a service reachable from another machine, a page answered to another site's name,
a Datasette description or canned query that no longer fits the schema."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess

import pytest

from nknu.ops.theme import bar as _theme

THEME = _theme.theme_css()
import yaml

from nknu.content.schema import create_schema
from nknu.settings import REPO_ROOT

KIT = REPO_ROOT / "deploy" / "lab-inspect"
PORTS = {"datasette": "127.0.0.1:8001:8001", "mcp": "127.0.0.1:8766:8766", "ops": "127.0.0.1:8878:8878"}


def _plugin(name: str = "local_only"):
    spec = importlib.util.spec_from_file_location(name, KIT / "datasette" / "plugins" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ask(wrapped, host: str | None, kind: str = "http") -> tuple[list[dict], list[str]]:
    """The messages wrapped sent for one request with that Host, and whether the app behind it was reached."""
    reached: list[str] = []
    sent: list[dict] = []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    headers = [] if host is None else [(b"host", host.encode())]
    asyncio.run(wrapped(app)({"type": kind, "headers": headers}, receive, send))
    return sent, reached


@pytest.mark.parametrize("host", ["127.0.0.1:18001", "localhost:8001", "LOCALHOST", "[::1]:8001", "127.0.0.1"])
def test_a_request_that_names_this_machine_reaches_datasette(host):
    sent, reached = _ask(_plugin().local_only, host)
    assert reached == ["http"] and sent == []


@pytest.mark.parametrize("host", ["evil.example:18001", "127.0.0.1.evil.example", "192.168.1.20:8001", "", None,
                                  "[::1]evil.example", "[::1].evil.example:8001", "localhost.evil.example"])
def test_any_other_host_gets_403_and_never_reaches_datasette(host):
    sent, reached = _ask(_plugin().local_only, host)
    assert reached == []
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 403


def test_what_is_not_a_request_passes_through():
    sent, reached = _ask(_plugin().local_only, None, kind="lifespan")
    assert reached == ["lifespan"] and sent == []


def test_the_plugin_gives_datasette_its_wrapper():
    plugin = _plugin()
    assert plugin.asgi_wrapper(None) is plugin.local_only


def _compose() -> dict:
    return yaml.safe_load((KIT / "compose.yaml").read_text(encoding="utf-8"))


def test_every_service_is_published_on_the_lab_pcs_own_loopback_only():
    services = _compose()["services"]
    assert {name: s["ports"] for name, s in services.items()} == {name: [port] for name, port in PORTS.items()}


def test_the_data_is_mounted_read_only():
    for name, service in _compose()["services"].items():
        work = [v for v in service["volumes"] if v.startswith("nknu-work:")]
        assert work == ["nknu-work:/work:ro"], name


def test_the_services_come_back_with_docker():
    assert {s.get("restart") for s in _compose()["services"].values()} == {"unless-stopped"}


def test_the_two_services_of_ours_listen_inside_the_container_only_for_docker():
    services = _compose()["services"]
    mcp, ops = " ".join(services["mcp"]["command"]), " ".join(services["ops"]["command"])
    assert "nknu.retrieval.server" in mcp and "--http" in mcp and "--host 0.0.0.0" in mcp and "--port 8766" in mcp
    assert "nknu.ops.web" in ops and "--host 0.0.0.0" in ops and "--port 8878" in ops and "--root /work" in ops
    # the gold ids of the library the service reads (the eval folder's own gold file is of an older build: 111 of its
    # 121 ids are not in the official library, lab PC 11-15)
    assert "--questions research/eval_2026-11-01 --gold research/pipeline_2026-11-02/gold_docs.json" in ops


def test_only_the_page_check_reads_the_crawlers_archive_and_only_read_only():
    """網頁核查 (design 2026-11-16-page-check-design.md section 四): the ledger's volume and the WARC folder, both
    read-only, on the ops service alone, and the page is told where the archive is."""
    compose = _compose()
    services = compose["services"]
    ops = services["ops"]["volumes"]
    assert "nknu-crawl:/crawl:ro" in ops and "D:/nknu-crawl/warc:/crawl/archive/warc:ro" in ops
    assert "nknu-work:/work:ro" in ops and "nknu-models:/models:ro" in ops
    assert all(v.endswith(":ro") for v in ops if v != "nknu-ops:/ops")
    for name in ("mcp", "datasette"):
        assert not any("crawl" in v for v in services[name]["volumes"]), name
    assert compose["volumes"]["nknu-crawl"] == {"external": True}
    assert "--archive /crawl/archive" in " ".join(services["ops"]["command"])


def test_the_nights_record_is_the_one_place_the_ops_service_writes():
    """每晚紀錄 (panels v3 2026-11-17, nknu.ops.history): a volume of its own, read-write, on ops alone, never inside
    /work or /crawl, and the service is told where its file is."""
    compose = _compose()
    services = compose["services"]
    writable = {name: [v for v in s["volumes"] if not v.endswith(":ro")] for name, s in services.items()}
    assert writable == {"datasette": [], "mcp": [], "ops": ["nknu-ops:/ops"]}
    assert compose["volumes"]["nknu-ops"] == {"name": "nknu-ops"}
    command = " ".join(services["ops"]["command"])
    assert "--ops-db /ops/ops.db" in command


def test_datasette_is_pinned_and_reads_its_settings_from_the_volume():
    datasette = _compose()["services"]["datasette"]
    assert datasette["image"] == "datasetteproject/datasette:0.65.5"
    command = " ".join(datasette["command"])
    assert "/work/inspect-code/deploy/lab-inspect/datasette/metadata.yaml" in command
    assert "/work/inspect-code/deploy/lab-inspect/datasette/plugins" in command and "allow_download off" in command
    assert "--template-dir /work/inspect-code/deploy/lab-inspect/datasette/templates" in command


def test_our_services_run_their_own_copy_of_the_code_and_the_volumes_configuration():
    """The inspection tools' code is installed beside the pipeline's (/work/inspect-code, install.sh), so sending it
    never replaces /work/src, which the build runs and other sessions' half-done edits must not reach."""
    services = _compose()["services"]
    for name in ("mcp", "ops"):
        env = services[name]["environment"]
        assert env["PYTHONPATH"] == "/work/inspect-code/src" and env["NKNU_CONFIG_DIR"] == "/work/config", name


def test_the_pack_and_install_scripts_parse_and_name_the_same_files():
    pack, install = KIT / "pack.sh", KIT / "install.sh"
    for script in (pack, install):
        assert subprocess.run(["bash", "-n", str(script)], check=False, capture_output=True).returncode == 0, script
    packed = pack.read_text(encoding="utf-8")
    for path in ("src/nknu/ops", "src/nknu/retrieval/hybrid.py", "src/nknu/retrieval/search.py",
                 "src/nknu/retrieval/server.py", "src/nknu/admin/cli.py", "deploy/lab-inspect"):
        assert path in packed, path
    installed = install.read_text(encoding="utf-8")
    assert "inspect-code" in installed and "sha256sum -c" in installed and 'cp -a "$WORK/src"' in installed
    # what the pages read to say when the copy was installed and whether the pipeline's code has moved on since
    assert '"$NEW/INSTALLED"' in installed and '"$NEW/BASE.sha256"' in installed
    assert "rm -f" in packed  # a reused output folder holds no file of an earlier pack


def _metadata() -> dict:
    return yaml.safe_load((KIT / "datasette" / "metadata.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def empty():
    """The library's schema, empty, with the SQL functions the Datasette plugins give every connection."""
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _plugin("undated").register(conn)
    _plugin("script").register(conn)
    yield conn
    conn.close()


def _params(sql: str) -> dict:
    return {n: "x" for n in re.findall(r":([A-Za-z_]\w*)", sql)}


def test_the_descriptions_name_only_tables_and_columns_the_schema_has(empty):
    have = {r[0] for r in empty.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    tables = _metadata()["databases"]["aca"]["tables"]
    assert set(tables) <= have
    for table, about in tables.items():
        columns = {r[1] for r in empty.execute(f"PRAGMA table_info({table})")}
        assert set(about.get("columns", {})) <= columns, table
        assert set(about.get("facets", [])) <= columns, table


def test_the_vectors_and_the_index_tables_are_hidden(empty):
    tables = _metadata()["databases"]["aca"]["tables"]
    shadow = {r[0] for r in empty.execute("SELECT name FROM sqlite_master WHERE name LIKE 'chunk_fts_%'")}
    assert shadow and all(tables[t]["hidden"] for t in ("chunk_vecs", *shadow))


def test_every_canned_query_runs_on_the_schema(empty):
    queries = _metadata()["databases"]["aca"]["queries"]
    assert len(queries) >= 5
    for name, query in queries.items():
        empty.execute(query["sql"], _params(query["sql"])).fetchall()
        assert query["title"], name


# --- the home page's map (plugins/guide.py, templates/index.html) and why a document has no date (plugins/undated.py)


def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info([{table}])")]


def test_the_map_names_only_tables_and_joins_the_schema_has(empty):
    guide = _plugin("guide")
    have = {r[0] for r in empty.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table, _, _, _, _, joins in guide.TABLES:
        assert table in have, table
        for column, other, to in joins:
            assert column == "rowid" or column in _columns(empty, table), (table, column)
            assert to in _columns(empty, other), (other, to)


def test_every_table_on_the_map_says_what_it_is_and_what_each_column_means(empty):
    """The owner (11-15): 「每个表和字段代表什么都还不知道」: no table on the map without a description, no column of it
    without a meaning."""
    tables = _metadata()["databases"]["aca"]["tables"]
    for table, *_ in _plugin("guide").TABLES:
        assert tables[table].get("description"), table
        missing = set(_columns(empty, table)) - set(tables[table].get("columns", {}))
        assert not missing, (table, missing)


class _Rows:
    def __init__(self, rows):
        self.rows = rows


class _Database:
    """What guide() asks of a Datasette database, over a sqlite3 connection."""

    hash, size = "h1", 1

    def __init__(self, conn):
        self.conn = conn

    async def table_names(self):
        return [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]

    async def execute(self, sql):
        return _Rows(self.conn.execute(sql).fetchall())

    async def table_columns(self, table):
        return _columns(self.conn, table)


class _Urls:
    def database(self, name):
        return f"/{name}"


class _Datasette:
    urls = _Urls()

    def __init__(self, conn):
        self.db = _Database(conn)
        self.asked: list[bool] = []

    def get_database(self, name):
        if name != "aca":
            raise KeyError(name)
        return self.db

    def metadata(self, key=None, database=None, table=None, fallback=True):
        if key is not None:  # one key of the whole file, as Datasette gives it
            return _metadata().get(key)
        self.asked.append(fallback)
        return _metadata()["databases"][database]["tables"].get(table, {})


def _vars(guide, template, ds, database=None, table=None) -> dict:
    """What the plugin gives one page's template."""
    guide._CACHE.clear()
    return asyncio.run(guide.extra_template_vars(template, database, table, ds)())


def test_the_home_page_gets_every_table_its_rows_joins_and_column_meanings(empty):
    guide = _plugin("guide")
    empty.execute("INSERT INTO meta VALUES ('built_at', '2026-11-15')")
    empty.execute("INSERT INTO sites (site, name, unit, docs) VALUES ('s1', '某系網站', '某系', 3)")
    empty.execute("INSERT INTO sites (site, name, unit, docs) VALUES ('s2', '某處網站', '某處', 9)")
    ds = _Datasette(empty)
    got = _vars(guide, "index.html", ds)["guide"]
    by = {t["name"]: t for t in got["tables"]}
    rows = empty.execute("SELECT count(*) FROM meta").fetchone()[0]  # the schema's own row and this one
    assert set(by) == {t[0] for t in guide.TABLES} and by["meta"]["count"] == rows >= 1 and by["docs"]["count"] == 0
    assert by["docs"]["description"].startswith("一列一份文件") and not any(ds.asked)  # the table's own entry only
    meaning = _metadata()["databases"]["aca"]["tables"]["docs"]["columns"]["ident_kind"]
    assert {"name": "ident_kind", "description": meaning, "short": "日期類別",
            "rest": meaning.removeprefix("日期類別：")} in by["docs"]["columns"]
    assert {"name": "title", "description": "標題", "short": "標題", "rest": ""} in by["docs"]["columns"]
    lines = {(e["from"], e["to"]): e for e in got["edges"]}
    assert lines[("docs", "sites")]["label"] == "site" and lines[("docs", "docs")]["self"]
    assert lines[("doc_refs", "docs")]["label"] == "doc_id、ref_id"  # two joins of one pair share a line
    # what joins a table from the other side is said too, so the panel beside the map can show both directions
    assert {"table": "dates", "zh": "讀到的日期", "column": "doc_id", "to": "doc_id"} in by["docs"]["joined_by"]
    assert by["dates"]["joins"] == [{"column": "doc_id", "table": "docs", "to": "doc_id", "zh": "文件"}]
    assert 0 < by["sites"]["share"] <= 1 and by["docs"]["share"] == 0  # a bar by the logarithm of the rows
    assert [s["site"] for s in got["sites"]] == ["s2", "s1"]  # the sites to pick from, the largest first


def test_every_page_gets_the_other_panels_and_the_words_for_what_the_library_codes(empty):
    guide = _plugin("guide")
    ds = _Datasette(empty)
    table = _vars(guide, "table.html", ds, "aca", "docs")
    # since 11-17 the bar is the panels' one (nknu/ops/theme): four entries by their tunnel addresses, and 資料庫, a
    # tool, marked in the 工具 menu (structure design 2026-11-17 section 二)
    assert "aria-current='page'>資料庫<svg" in table["nk_bar"] and "<details class='nk-tools nk-on'>" in table["nk_bar"]
    assert "href='http://127.0.0.1:18878/try'" in table["nk_bar"] and "href='http://127.0.0.1:18878/doc'" in table["nk_bar"]
    assert "data-tip='每個網站收了多少、缺什麼" in table["nk_bar"]  # what each entry is floats over its name
    # a site's name on the home's list opens its page in 檢視台; a document's trace too
    assert table["site_page"] == "http://127.0.0.1:18878/site?site=" and table["trace_url"].endswith(":18878/trace?ref=")
    assert "--k0:" in table["nk_theme"] and "--act:" in table["nk_theme"]
    assert table["zh_tables"]["docs"] == "文件" and table["value_words"]["kind_class"]["notice"] == "公告"
    assert table["column_names"]["kind_class"]["short"] == "類型" and "guide" not in table
    assert table["column_names"]["kind_class"]["rest"].startswith("公告 notice")  # what floats over the name
    assert table["op_words"]["exact"] == "等於" and table["op_words"]["isnull"].startswith("是空的")
    assert table["trace_url"].endswith("/trace?ref=") and table["kind_classes"]["attachment"] == "k2"
    assert table["db_titles"] == {"aca": "正式庫"}  # the crumbs name a database by these (對照庫 gone 11-19)
    assert _vars(guide, "query.html", ds, "aca", None)["column_names"] == {}
    assert "sites" in _vars(guide, "query.html", ds, "aca", None) and table["site_names"] == {}  # a facet's names


def test_the_home_page_has_no_map_when_the_official_library_is_not_open(empty):
    guide = _plugin("guide")
    ds = _Datasette(empty)
    ds.get_database = lambda name: (_ for _ in ()).throw(KeyError(name))
    assert _vars(guide, "index.html", ds)["guide"] is None
    assert _vars(guide, "query.html", ds, "aca", None)["sites"] == []


def test_every_columns_meaning_starts_with_a_short_chinese_name(empty):
    """The owner (11-15): 「每个表和字段代表什么都还不知道」, then 「字太雜了」: a table's header shows a column's short
    Chinese name with the column's own name under it, so metadata.yaml writes every meaning as 「名稱：說明」 (or the
    name alone) and the plugin takes the name from its start."""
    guide = _plugin("guide")
    tables = _metadata()["databases"]["aca"]["tables"]
    for table, *_ in guide.TABLES:
        for column, meaning in tables[table]["columns"].items():
            name = guide.short_name(meaning)
            assert name and len(name) <= guide.SHORT, (table, column, meaning)
            rest = guide.rest_of(meaning)
            assert rest != meaning and not rest.startswith(("：", name + "：")), (table, column)
    assert guide.short_name("這個網站屬於哪個單位的那一個") == "" and guide.rest_of("一句沒有名稱的話，很長很長") != ""
    assert guide.short_name("") == "" and guide.rest_of("標題") == ""


def _overlap(a: dict, b: dict) -> bool:
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def test_the_maps_groups_are_frames_that_do_not_overlap_and_fit_the_drawing():
    guide = _plugin("guide")
    frames = guide.regions({t[0] for t in guide.TABLES})
    assert [f["key"] for f in frames] == [key for key, _ in guide.GROUPS]
    assert {t[2] for t in guide.TABLES} == {key for key, _ in guide.GROUPS}  # every table is in a group there is
    for i, a in enumerate(frames):
        assert a["x"] >= 0 and a["y"] >= 0 and a["x"] + a["w"] <= guide.MAP_W and a["y"] + a["h"] <= guide.MAP_H, a
        assert not any(_overlap(a, b) for b in frames[i + 1:]), a["key"]
    assert [f["key"] for f in guide.regions({"docs"})] == ["docs"]  # only the groups of what is shown


def test_the_two_plugins_agree_on_the_words_and_the_kinds_hues():
    guide, cells = _plugin("guide"), _plugin("cells")
    assert guide.WORDS == cells.WORDS and guide.TRACE == cells.TRACE
    assert all(cells.KIND_CLASS[code] == cls for code, cls in guide.KIND_CLASS.items())
    assert set(guide.KIND_CLASS) == set(guide.WORDS["kind_class"])
    css = (KIT / "datasette" / "static" / "tc.css").read_text(encoding="utf-8")
    for cls in set(guide.KIND_CLASS.values()):
        assert f".tag.{cls} i" in css and f"--{cls}:" in THEME, cls  # each kind's class has its hue (the shared theme)


def test_every_canned_query_is_filed_and_every_parameter_has_its_name():
    guide = _plugin("guide")
    queries = _metadata()["databases"]["aca"]["queries"]
    assert {q.get("group") for q in queries.values()} == {"網站", "文件"}
    named = set(re.findall(r":([A-Za-z_]\w*)", " ".join(q["sql"] for q in queries.values())))
    assert named and named <= set(guide.PARAMS)  # a form never shows a bare parameter name


TEMPLATES = sorted((KIT / "datasette" / "templates").glob("*.html"))


def test_the_home_page_template_parses():
    jinja2 = pytest.importorskip("jinja2")
    source = (KIT / "datasette" / "templates" / "index.html").read_text(encoding="utf-8")
    jinja2.Environment().parse(source)
    assert '{% extends "base.html" %}' in source and "guide.edges" in source and "t.columns" in source


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_every_template_parses_and_asks_nothing_datasettes_rendering_cannot_give(template):
    """Datasette renders templates in Jinja's async mode, where map() is an async generator: max, unique and `in`
    cannot walk one (11-15: three pages answered 500 on the lab PC's staging copy until they were asked another
    way)."""
    jinja2 = pytest.importorskip("jinja2")
    source = template.read_text(encoding="utf-8")
    jinja2.Environment().parse(source)
    assert "|map(" not in source and "|unique" not in source, template.name


def test_the_pages_datasette_serves_are_all_ours_and_every_one_extends_our_frame():
    names = {t.name for t in TEMPLATES}
    assert {"base.html", "index.html", "database.html", "table.html", "query.html", "row.html", "error.html",
            "_table.html", "_facet_results.html", "_crumbs.html", "_footer.html"} <= names
    for name in ("index.html", "database.html", "table.html", "query.html", "row.html", "error.html"):
        source = (KIT / "datasette" / "templates" / name).read_text(encoding="utf-8")
        assert source.startswith('{% extends "base.html" %}'), name
    base = (KIT / "datasette" / "templates" / "base.html").read_text(encoding="utf-8")
    assert '<html lang="zh-Hant-TW">' in base and "panels" in base and "{% block content %}" in base
    assert "urls.static('app.css')" in base and "extra_css_urls" in base  # Datasette's own sheet, then ours
    # an error page gets no extra_css_urls from Datasette: the frame then links our sheet by its own address (11-15:
    # the staging copy's "table not found" page came out in Datasette's own blue)
    assert "{% if not extra_css_urls %}" in base and "urls.path('/static/tc.css')" in base


def test_the_table_page_keeps_what_datasettes_script_and_forms_rely_on():
    table = (KIT / "datasette" / "templates" / "table.html").read_text(encoding="utf-8")
    rows = (KIT / "datasette" / "templates" / "_table.html").read_text(encoding="utf-8")
    for name in ('name="_filter_column"', 'name="_filter_op"', 'name="_filter_value"', "form_hidden_args", "table.js"):
        assert name in table, name
    for attribute in ("data-column=", "data-column-type=", "data-column-not-null=", "data-is-pk=", "rows-and-columns"):
        assert attribute in rows, attribute


@pytest.mark.parametrize(("kind", "raw", "url", "parent", "menu", "pdate", "text", "code"), [
    ("notice", "newsdetail", "https://x.example/n?id=1", None, 0, None, "", "notice"),
    ("page", "student_detail", "https://x.example/p", None, 0, None, "畢業 2019-06-30", "people"),
    ("page", "page", "https://x.example/p", None, 0, None, "系所簡介 電話 07-7172930", "page"),
    ("page", "page", "https://x.example/p", None, 0, None, None, "page"),
    ("page", "page", "https://x.example/p", None, 0, None, "活動日期* | 2017-12-21", "page-written"),
    ("page", "page:sso_osa", "https://x.example/p", None, 0, None, "活動日期： 115年05月11日", "page-written"),
    ("page", "page", "https://x.example/p", None, 0, None, " ".join(f"2020-01-{d:02d}" for d in range(1, 10)),
     "page-written"),
    ("page", "page", "https://x.example/p", None, 0, None, " ".join(f"2020-01-{d:02d}" for d in range(1, 11)),
     "page-many"),  # ten dates or more: a list's, none the page's own
    ("attachment", "附件:office", "https://x.example/f.doc", "notice", 0, None, "", "notice-parent"),
    ("attachment", "附件:office", "https://x.example/p#按钮=表一", "page", 0, "2026-01-02", "", "button"),
    ("attachment", "附件:office", "https://x.example/p#按钮=表一", None, 0, None, "", "button"),
    ("attachment", "附件:office", "https://x.example/f.doc", "page", 1, None, "2024-01-02", "page-parent"),
    ("attachment", "附件:office", "https://x.example/f.doc", "page", 0, "2026-01-02", "", "page-parent-dated"),
    ("attachment", "附件:office", "https://x.example/f.doc", None, 1, None, "", "menu"),
    ("attachment", "附件:office", "https://x.example/f.doc", None, 0, None, "", "orphan"),
    ("event", "event", "https://x.example/e", None, 0, None, "", "other"),
])
def test_an_undated_document_falls_in_the_case_the_date_rules_give(kind, raw, url, parent, menu, pdate, text, code):
    undated = _plugin("undated")
    assert undated.undated_why(kind, raw, url, parent, menu, pdate, text) == code
    assert undated.undated_say(code) == undated.SAY[code] and undated.undated_do(code) in undated.DO_TIP


def test_a_journals_page_is_its_own_case_whatever_dates_its_articles_write():
    """A page of an old 校刊 or 校訊 (doc_type journal) writes its articles' dates, not its issue's: no rule to change."""
    undated = _plugin("undated")
    assert undated.undated_why("page", "静态:spaper", "https://x.example/p", None, 0, None, "本所於 98年10月", "journal") \
        == "journal" and undated.DO["journal"] == "不用管"
    assert undated.undated_why("page", "page", "https://x.example/p", None, 0, None, "本所於 98年10月", None) \
        == "page-written"


@pytest.mark.parametrize(("text", "seen"), [
    ("活動日期* | 2017-12-21 ~ 2017-12-21", "（2 個）活動日期* | 2017-12-21"),
    ("2026/07/21 Views", "2026/07/21"),
    ("修正 107.06.13 行政會議", "修正 107.06.13"),
    ("校刊 2024年3月 出刊", "校刊 2024年3月"),
    ("TPC 13.8/6.9-50 電話 07-7172930 ISBN 978-986-123 第 1450 號", ""),  # no date: a product code, a phone, numbers
    ("", ""),
    (None, ""),
])
def test_a_date_a_page_writes_is_found_in_any_form_and_shown_with_what_comes_before_it(text, seen):
    assert _plugin("undated").date_seen(text) == seen


def test_every_canned_query_keeps_its_sql_folded():
    assert all(q.get("hide_sql") is True for q in _metadata()["databases"]["aca"]["queries"].values())


def _cell(cells, value, column, table=None, database="aca"):
    return cells.render_cell(value, column, table, database, _Datasette(None))


def test_a_link_column_shows_a_short_word_and_any_other_cell_renders_as_usual():
    cells = _plugin("cells")
    trace = str(_cell(cells, "http://127.0.0.1:18878/trace?ref=du1", "追蹤"))
    assert trace == ('<a class="go" href="http://127.0.0.1:18878/trace?ref=du1" target="_blank" rel="noopener" '
                     'data-tip="在檢視台的文件追蹤看這一份：從抓取到搜尋的每一步">追蹤 ↗</a>')
    listed = str(_cell(cells, "http://127.0.0.1:18001/aca/site_undated?site=s1&why=menu", "看清單"))
    assert listed == ('<a class="go" href="http://127.0.0.1:18001/aca/site_undated?site=s1&amp;why=menu" '
                      'data-tip="列出這個原因的每一份文件">看清單</a>')
    site = str(_cell(cells, "http://127.0.0.1:18001/aca/site_docs?site=c.nknu.edu.tw/aca", "代號"))
    assert site.endswith(">c.nknu.edu.tw/aca</a>")
    assert _cell(cells, "https://www.nknu.edu.tw/x", "追蹤") is None  # not one of ours
    assert _cell(cells, "http://127.0.0.1:18878/trace?ref=du1", "網址") is None  # not a link column
    assert _cell(cells, 3, "追蹤") is None and _cell(cells, "http://127.0.0.1:18001/a b", "追蹤") is None
    queries = _metadata()["databases"]["aca"]["queries"]
    named = {c for q in queries.values() for c in re.findall(r'as "([^"]+)"', q["sql"]) if "http" in q["sql"]}
    assert {"追蹤", "看清單", "代號"} <= named  # the columns it shortens are the queries' own


def test_a_coded_value_shows_its_word_and_floats_its_code():
    cells = _plugin("cells")
    assert (str(_cell(cells, "notice", "kind_class", "docs"))
            == '<span class="tag kind k0" data-tip="庫裡存成 notice"><i></i>公告</span>')
    shown = str(_cell(cells, "附件", "類型"))  # a canned query's own Chinese takes the same hue, and floats nothing
    assert shown == '<span class="tag kind k2"><i></i>附件</span>'
    assert str(_cell(cells, "background", "tier")) == '<span class="tag bg" data-tip="庫裡存成 background">背景</span>'
    assert str(_cell(cells, "主料", "主料／背景")) == '<span class="tag">主料</span>'
    assert str(_cell(cells, "copy", "why")) == '<span class="tag" data-tip="庫裡存成 copy">別的網址的同一份</span>'
    assert _cell(cells, "学院", "kind", "units") is None  # a value that is no code of that column stays as it is
    assert _cell(cells, "notice", "title") is None and _cell(cells, "", "kind_class") is None
    assert _cell(cells, None, "tier") is None
    assert _cell(cells, '<b>"x"</b>', "why") is None  # an unknown value is not made into markup at all


def test_a_document_id_leads_to_its_trace_and_a_sites_code_to_its_documents():
    cells = _plugin("cells")
    doc = str(_cell(cells, "du45abc", "parent_id", "docs"))
    assert '<code class="id">du45abc</code>' in doc and 'href="http://127.0.0.1:18878/trace?ref=du45abc"' in doc
    assert 'target="_blank"' in doc
    assert _cell(cells, "https://x.example/a", "doc_id") is None and _cell(cells, "a b", "doc_id") is None
    assert _cell(cells, "du45abc", "title") is None and _cell(cells, 5, "doc_id") is None
    escaped = str(_cell(cells, 'x"><script>', "ref_id"))
    assert "<script>" not in escaped and "%22%3E%3Cscript%3E" in escaped
    site = str(_cell(cells, "c.nknu.edu.tw/aca", "site", "docs"))
    assert site == '<a href="/aca/site_docs?site=c.nknu.edu.tw%2Faca" data-tip="這個網站的文件">c.nknu.edu.tw/aca</a>'
    assert _cell(cells, "c.nknu.edu.tw/aca", "site", "dates") is None  # only in the tables that store a site
    assert _cell(cells, "c.nknu.edu.tw/aca", "site", "docs", database=None) is None


def test_a_date_stays_on_one_line_and_anything_else_is_left_alone():
    cells = _plugin("cells")
    assert str(_cell(cells, "2026-11-06", "資料截至")) == '<span class="nw">2026-11-06</span>'
    assert str(_cell(cells, "2026-11-06", "ident_date", "docs")) == '<span class="nw">2026-11-06</span>'
    assert _cell(cells, "2026-11-06 起", "raw") is None and _cell(cells, "2026/11/06", "raw") is None
    assert _cell(cells, "<b>2026-11-06</b>", "raw") is None and _cell(cells, 20261106, "date") is None


def test_a_count_is_printed_with_separators_and_an_id_never_is():
    cells = _plugin("cells")
    assert str(_cell(cells, 227733, "chars")) == "227,733" and str(_cell(cells, 11592, "文件數")) == "11,592"
    assert _cell(cells, 999, "chars") is None and _cell(cells, 624675, "chunk_id") is None
    assert _cell(cells, True, "chars") is None and _cell(cells, "227733", "chars") is None


def test_every_case_has_its_name_and_sentence_and_an_unknown_code_shows_as_it_is():
    """Owner 11-15: 「想缩短文字 但又想保留细节导致不上不下的」: a reason is a short name on the page and its whole
    sentence floats over it; the two plugins hold the same pairs (each is loaded alone)."""
    undated, cells = _plugin("undated"), _plugin("cells")
    assert set(undated.SAY) == set(undated.TIP) == set(undated.DO) and all(undated.TIP.values())
    assert cells.REASONS == {undated.SAY[c]: undated.TIP[c] for c in undated.SAY}
    # what to do about each: one of four words, who does what floating over it; listed what can be done first
    # a word may have no case for now (爬蟲可補 since the library of 11-16 dated the buttons' files)
    assert set(undated.DO.values()) <= set(undated.DO_TIP) == set(undated.DO_ORDER) == set(cells.ACTIONS)
    assert {w: tip for w, (_, tip) in cells.ACTIONS.items()} == undated.DO_TIP
    assert undated.undated_rank("page-written") < undated.undated_rank("orphan") < undated.undated_rank("button")
    assert undated.undated_rank("orphan") < undated.undated_rank("people") < undated.undated_rank("zz")
    assert undated.undated_do("zz") == ""
    done = str(_cell(cells, "爬蟲可補", "怎麼辦"))
    assert done == f'<span class="tag do-fix" data-tip="{undated.DO_TIP["爬蟲可補"]}">爬蟲可補</span>'
    assert 'class="tag do-ok"' in str(_cell(cells, "不用管", "怎麼辦"))
    assert 'class="tag do-look"' in str(_cell(cells, "要查", "怎麼辦"))
    css = (KIT / "datasette" / "static" / "tc.css").read_text(encoding="utf-8")
    assert all(f".tag.do-{tone}" in css for tone, _ in cells.ACTIONS.values())
    assert max(len(name) for name in undated.SAY.values()) <= 10
    assert undated.undated_say("zz") == "zz" and undated.undated_say(None) == ""
    shown = str(_cell(cells, undated.SAY["button"], "原因"))
    assert shown == f'<span data-tip="{undated.TIP["button"]}">{undated.SAY["button"]}</span>'
    assert _cell(cells, "有日期：跟所屬公告", "原因") is None  # a dated one's words stay as they are


def _doc(conn, doc_id, kind, *, raw="page", url="https://x.example/p", parent=None, date=None, site="s1", text=""):
    conn.execute("INSERT INTO docs (doc_id, kind_class, kind_raw, title, url, unit, parent_id, has_text, chars,"
                 " ident_date, ident_kind, site, text) VALUES (?, ?, ?, ?, ?, '某系', ?, 1, 10, ?, ?, ?, ?)",
                 (doc_id, kind, raw, f"標題{doc_id}", url, parent, date, "published" if date else None, site, text))


def _run(conn, name: str, **params) -> list[dict]:
    sql = _metadata()["databases"]["aca"]["queries"][name]["sql"]
    cur = conn.execute(sql, {**_params(sql), **params})
    return [dict(zip([c[0] for c in cur.description], row, strict=True)) for row in cur]


def test_the_why_queries_count_list_and_explain_on_a_small_library(empty):
    undated = _plugin("undated")
    empty.execute("INSERT INTO sites (site, name, unit) VALUES ('s1', '某系網站', '某系')")
    _doc(empty, "dn", "notice", date="2026-10-01")
    _doc(empty, "dp", "page")
    _doc(empty, "da", "attachment", url="https://x.example/f.pdf", parent="dp")
    _doc(empty, "db", "attachment", url="https://x.example/p#按钮=一", parent="dp")
    _doc(empty, "dc", "attachment", url="https://x.example/g.pdf")
    _doc(empty, "dz", "attachment", url="https://y.example/h.pdf", site="s2")
    _doc(empty, "dw", "page", text="活動日期* | 2017-12-21")  # a page that writes a date the build does not take
    empty.execute("INSERT INTO doc_refs (doc_id, ref_id, zone, context) VALUES ('dc', 'dp', 'nav', '下載')")
    empty.execute("INSERT INTO doc_alias (alias, doc_id, why) VALUES ('dold', 'da', 'copy')")
    summary = _run(empty, "site_undated_why", site="s1")
    assert {r["原因"]: r["份數"] for r in summary} == {
        undated.SAY[c]: 1 for c in ("page", "page-parent", "button", "menu", "page-written")}  # dated, s2: out
    assert [r["怎麼辦"] for r in summary] == ["改建庫規則可補", "不用管", "不用管", "不用管", "不用管"]  # do first
    link = next(r["看清單"] for r in summary if r["原因"] == undated.SAY["menu"])
    assert link == "http://127.0.0.1:18001/aca/site_undated?site=s1&why=只在選單出現的附件"  # readable in the box
    every = _run(empty, "site_undated", site="s1", why="")
    assert len(every) == 5 and {r["所屬文件"] for r in every} == {"", "標題dp"}
    assert [r["頁面上的日期"] for r in every if r["標題"] == "標題dw"] == ["活動日期* | 2017-12-21"]
    assert all(r["頁面上的日期"] == "" for r in every if r["標題"] != "標題dw")
    only = _run(empty, "site_undated", site="s1", why="button")
    assert [r["標題"] for r in only] == ["標題db"] and only[0]["原因"] == undated.SAY["button"]
    assert _run(empty, "site_undated", site="s1", why=None) == every  # a link without why= lists them all
    assert _run(empty, "site_undated", site="s1", why=undated.SAY["button"]) == only  # by its name, as 看清單 asks
    one = _run(empty, "why_undated", doc_id="dold")  # a merged-away number finds the one kept
    assert one[0]["標題"] == "標題da" and one[0]["原因"] == undated.SAY["page-parent"] and one[0]["日期"] == "沒有"
    assert one[0]["怎麼辦"] == "不用管" and len(one) == 1
    dated = _run(empty, "why_undated", doc_id="dn")
    assert dated[0]["日期"] == "2026-10-01" and dated[0]["原因"].startswith("有日期") and dated[0]["怎麼辦"] == ""
    written = _run(empty, "why_undated", doc_id="dw")
    assert written[0]["怎麼辦"] == "改建庫規則可補" and written[0]["頁面上的日期"] == "活動日期* | 2017-12-21"


def test_only_the_library_in_use_is_opened():
    # 11-19: the 11-15 control library (aca1115.db) was cleared from the lab PC; Datasette opens the library in use only
    command = " ".join(_compose()["services"]["datasette"]["command"])
    assert "aca1115" not in command and "aca.db" in command
    assert list(_metadata()["databases"]) == ["aca"]


def test_every_page_links_a_stylesheet_the_service_serves_and_it_puts_the_traditional_fonts_first():
    """Owner 11-15: 「可视化dashboard全部换繁体字」. The fonts come from a stylesheet every page links (extra_css_urls)
    and Datasette serves from the kit (--static); a mount or a name that does not match would only show as a 404."""
    command = " ".join(_compose()["services"]["datasette"]["command"])
    mounts = dict(m.split(":", 1) for m in re.findall(r"--static (\S+)", command))
    urls = _metadata()["extra_css_urls"]
    assert urls
    for url in urls:
        mount, _, name = url.removeprefix("/").partition("/")
        folder = mounts[mount]
        assert folder.startswith("/work/inspect-code/deploy/lab-inspect/"), folder  # what pack.sh ships
        css = (REPO_ROOT / folder.removeprefix("/work/inspect-code/") / name).read_text(encoding="utf-8")
        assert '"PingFang TC", "Microsoft JhengHei", "Noto Sans CJK TC"' in css
        assert not any(sc in css for sc in ("PingFang SC", "Microsoft YaHei", "CJK SC"))


def test_the_tunnel_script_forwards_the_three_services():
    script = REPO_ROOT / "scripts" / "inspect-tunnel.sh"
    assert subprocess.run(["bash", "-n", str(script)], check=False, capture_output=True).returncode == 0
    text = script.read_text(encoding="utf-8")
    for forward in ("18001:127.0.0.1:8001", "18766:127.0.0.1:8766", "18878:127.0.0.1:8878"):
        assert forward in text


# ---------------------------------------------------------------- install.sh and pack.sh, run for real

needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in ("bash", "tar", "sha256sum", "shasum", "git")),
                                 reason="needs bash, tar, sha256sum, shasum and git")
OLD, PACKED = "HYBRID = 1\n", "HYBRID = 1\nRECORDER = 1\n"
REPLACED = "src/nknu/retrieval/hybrid.py"


def _lab(tmp_path, on_the_lab: str):
    """A lab volume whose pipeline source has on_the_lab as the file the pack replaces, and a pack beside it as
    pack.sh writes one (made from OLD, carrying PACKED)."""
    work, into, stage = tmp_path / "work", tmp_path / "in", tmp_path / "stage"
    for tree, text in ((work, on_the_lab), (stage, PACKED)):
        (tree / REPLACED).parent.mkdir(parents=True)
        (tree / REPLACED).write_text(text, encoding="utf-8")
    (work / "src/nknu/content").mkdir(parents=True)
    (work / "src/nknu/content/build.py").write_text("BUILD = 1\n", encoding="utf-8")
    (stage / "src/nknu/ops").mkdir(parents=True)
    (stage / "src/nknu/ops/web.py").write_text("WEB = 1\n", encoding="utf-8")
    into.mkdir()
    subprocess.run(["tar", "-cf", str(into / "inspect-code.tar"), "-C", str(stage), "src"], check=True)
    packed = hashlib.sha256((into / "inspect-code.tar").read_bytes()).hexdigest()
    (into / "inspect-code.tar.sha256").write_text(f"{packed}  inspect-code.tar\n", encoding="utf-8")
    (into / "inspect-code.base.sha256").write_text(
        f"{hashlib.sha256(OLD.encode()).hexdigest()}  {REPLACED}\n", encoding="utf-8")
    return work, into


def _install(work, into):
    env = {**os.environ, "WORK": str(work), "IN": str(into)}
    return subprocess.run(["bash", str(KIT / "install.sh")], env=env, check=False, capture_output=True, text=True)


@needs_tools
def test_install_lays_the_pack_over_a_copy_and_leaves_the_pipelines_code_alone(tmp_path):
    work, into = _lab(tmp_path, OLD)  # the lab has the version the pack was made from
    got = _install(work, into)
    assert got.returncode == 0, got.stderr
    code = work / "inspect-code"
    assert (code / REPLACED).read_text(encoding="utf-8") == PACKED
    assert (code / "src/nknu/content/build.py").read_text(encoding="utf-8") == "BUILD = 1\n"
    assert (code / "src/nknu/ops/web.py").is_file() and (code / "BASE.sha256").is_file()
    assert len((code / "INSTALLED").read_text(encoding="utf-8").split()) == 2
    assert (work / REPLACED).read_text(encoding="utf-8") == OLD


@needs_tools
def test_install_takes_a_pipeline_that_already_has_the_packs_own_version(tmp_path):
    """After the usual code sync the lab's file is the pack's own: installing again must not be refused."""
    work, into = _lab(tmp_path, PACKED)
    assert _install(work, into).returncode == 0


@needs_tools
def test_install_refuses_a_pipeline_file_that_is_neither_and_installs_nothing(tmp_path):
    work, into = _lab(tmp_path, "HYBRID = 3\n")
    got = _install(work, into)
    assert got.returncode == 1 and "停" in got.stderr and REPLACED in got.stderr
    assert not (work / "inspect-code").exists() and not (work / "inspect-code.new").exists()


@needs_tools
def test_install_keeps_the_copy_of_before(tmp_path):
    work, into = _lab(tmp_path, OLD)
    assert _install(work, into).returncode == 0 and _install(work, into).returncode == 0
    assert (work / "inspect-code.prev" / REPLACED).is_file() and (work / "inspect-code" / REPLACED).is_file()


@needs_tools
def test_install_refuses_a_damaged_pack(tmp_path):
    work, into = _lab(tmp_path, OLD)
    with (into / "inspect-code.tar").open("ab") as fh:
        fh.write(b"x")
    assert _install(work, into).returncode != 0 and not (work / "inspect-code").exists()


def _git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t"}
    subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True)


@needs_tools
def test_pack_names_the_version_each_replaced_file_was_made_from(tmp_path):
    """The base a pack names is what the lab PC is expected to hold: for a file as committed, the version before its
    last commit; for a file changed since, HEAD's (11-15: after the tools' own commit, HEAD's version was named for a
    committed file, which the lab PC does not hold until the next code sync)."""
    repo, out = tmp_path / "repo", tmp_path / "out"
    files = ("src/nknu/retrieval/hybrid.py", "src/nknu/retrieval/search.py", "src/nknu/retrieval/server.py",
             "src/nknu/admin/cli.py", "src/nknu/ops/web.py", "deploy/lab-inspect/install.sh",
             "deploy/lab-inspect/compose.yaml")
    for name in files:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("ONE\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "one")
    (repo / files[0]).write_text("TWO\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "two")  # hybrid.py as committed: made from ONE
    (repo / files[1]).write_text("THREE\n", encoding="utf-8")  # search.py changed since: made from HEAD's ONE
    got = subprocess.run(["bash", str(KIT / "pack.sh"), str(out)], cwd=repo, check=False, capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    base = dict(reversed(line.split("  ")) for line in (out / "inspect-code.base.sha256").read_text().splitlines())
    one = hashlib.sha256(b"ONE\n").hexdigest()
    assert base == {name: one for name in files[:4]}
    names = subprocess.run(["tar", "-tf", str(out / "inspect-code.tar")], check=True, capture_output=True, text=True)
    assert "src/nknu/ops/web.py" in names.stdout and "src/nknu/retrieval/hybrid.py" in names.stdout


# --- this round (owner 11-15: 「頁面廢話太多了」「我想挑選我感興趣的欄位看（默認就顯示一些比較重要的欄位）」「找網站的時候有點費勁」)


def test_a_wide_tables_page_opens_on_the_columns_that_matter_and_a_choice_is_left_alone(empty):
    columns = _plugin("columns")
    for table, default in columns.DEFAULT.items():
        assert set(default) <= set(_columns(empty, table)), table  # the defaults name real columns
    got = columns.with_default("/aca/docs", b"")
    assert got == "&".join(f"_col={c}" for c in columns.DEFAULT["docs"]).encode()
    assert columns.with_default("/aca/docs", b"_sort=chars").startswith(b"_sort=chars&_col=title")
    for path, query in (("/aca/docs", b"_col=url"), ("/aca/docs", b"_nocol=text"), ("/aca/docs.json", b""),
                        ("/aca/docs/d1", b""), ("/aca/dates", b""), ("/aca", b""), ("/", b"")):
        assert columns.with_default(path, query) is None, (path, query)


def test_the_request_datasette_reads_carries_the_default_columns_and_says_so():
    columns = _plugin("columns")
    seen: list[dict] = []

    async def app(scope, receive, send):
        seen.append(scope)

    wrapped = columns.asgi_wrapper(None)(app)
    asyncio.run(wrapped({"type": "http", "path": "/aca/docs", "query_string": b""}, None, None))
    asyncio.run(wrapped({"type": "http", "path": "/aca/docs", "query_string": b"_col=url"}, None, None))
    asyncio.run(wrapped({"type": "lifespan"}, None, None))
    assert b"_col=title" in seen[0]["query_string"] and seen[0][columns.MARK] is True
    assert seen[1]["query_string"] == b"_col=url" and columns.MARK not in seen[1] and seen[2] == {"type": "lifespan"}


def test_every_column_is_a_chip_that_switches_it_and_keeps_the_rest_of_the_page():
    columns = _plugin("columns")
    names, keys = ["doc_id", "title", "url", "chars"], ["doc_id"]
    args = [("_col", "title"), ("_col", "chars"), ("_sort", "chars"), ("_next", "9")]
    got = columns.chips(names, keys, args, "/aca/docs", "docs", False)
    by = {c["name"]: c for c in got["columns"]}
    assert (got["shown"], got["total"]) == (3, 4) and by["doc_id"] == {"name": "doc_id", "shown": True, "key": True, "url": None}
    assert by["url"]["shown"] is False and by["url"]["url"] == "/aca/docs?_sort=chars&_col=title&_col=url&_col=chars"
    assert by["title"]["url"] == "/aca/docs?_sort=chars&_col=chars"  # switched off; the sort stays, the page mark goes
    assert got["default_url"] == "/aca/docs?_sort=chars" and got["all_url"].endswith("_col=title&_col=url&_col=chars")
    # the last column switched off: Datasette's own word for hiding, or "no choice" would bring the default back
    last = columns.chips(names, keys, [("_col", "title")], "/aca/docs", "docs", False)
    assert {c["name"]: c for c in last["columns"]}["title"]["url"] == "/aca/docs?_nocol=title&_nocol=url&_nocol=chars"
    # a table with no default shows everything until a column is switched off
    whole = columns.chips(names, keys, [], "/aca/dates", "dates", False)
    assert whole["shown"] == 4 and whole["default_url"] is None and whole["all_url"] == "/aca/dates"
    hidden = columns.chips(names, keys, [("_nocol", "url")], "/aca/dates", "dates", False)
    assert [c["name"] for c in hidden["columns"] if c["shown"]] == ["doc_id", "title", "chars"]


def test_a_text_is_compared_in_one_script(empty):
    script = _plugin("script")
    assert script.t2s("軟體工程與管理學系") == "软体工程与管理学系" and script.t2s("abc 一二") == "abc 一二"
    assert script.t2s(None) is None and script.t2s(7) == 7
    empty.execute("INSERT INTO docs (doc_id, kind_class, kind_raw, title, url, unit, has_text, chars) "
                  "VALUES ('d1', 'page', 'page', '標題：軟體系課程', 'https://x.example/1', '某系', 1, 9)")
    sql = _metadata()["databases"]["aca"]["queries"]["find_title"]["sql"]
    for words in ("軟體", "软体"):  # the school's script or the other one
        assert len(empty.execute(sql, {"words": words}).fetchall()) == 1, words
    assert empty.execute(sql, {"words": "硬體"}).fetchall() == []


def test_a_site_is_found_by_its_names_in_both_scripts_and_says_how_many_documents_have_no_date(empty):
    guide = _plugin("guide")
    empty.execute("INSERT INTO sites (site, name, unit, docs) VALUES ('x.example/se', '軟體工程與管理學系', '軟體工程與管理學系', 2)")
    empty.execute("INSERT INTO sites (site, name, unit, docs) VALUES ('x.example/qa', '軟體工程與管理學系品保網站', '全校', 0)")
    empty.execute("INSERT INTO unit_aliases (alias, unit_id) VALUES ('軟工系', '軟體工程與管理學系')")
    for doc_id, kind, date in (("d1", "page", None), ("d2", "notice", "2026-11-01"), ("e1", "event", None)):
        empty.execute("INSERT INTO docs (doc_id, kind_class, kind_raw, title, url, unit, has_text, chars, ident_date, site) "
                      "VALUES (?, ?, 'x', '標題', 'https://x.example/' || ?, '某系', 1, 9, ?, 'x.example/se')",
                      (doc_id, kind, doc_id, date))
    sites = _vars(guide, "index.html", _Datasette(empty))["guide"]["sites"]
    se, qa = sites
    assert (se["site"], se["docs"], se["undated"], se["share"]) == ("x.example/se", 2, 1, 50)  # an event has no such date
    assert (qa["docs"], qa["undated"], qa["share"]) == (0, 0, 0)
    assert se["found_by"].split("|") == ["軟體工程與管理學系", "软体工程与管理学系", "x.example/se", "軟工系", "软工系"]
    assert "全校" in qa["found_by"].split("|")
    assert guide.site_keys(["A b", "a b", "", None, "乙"]) == "a b|乙"


def test_the_list_of_sites_is_one_piece_the_home_page_and_a_sites_query_share():
    templates = KIT / "datasette" / "templates"
    piece = (templates / "_sitefind.html").read_text(encoding="utf-8")
    assert "{% macro finder(sites, go, why" in piece and 'data-k="{{ s.found_by }}"' in piece and "s.undated" in piece
    for name in ("index.html", "query.html"):
        source = (templates / name).read_text(encoding="utf-8")
        assert '{% from "_sitefind.html" import finder %}' in source and "/static/sitefind.js" in source, name
    script = (KIT / "datasette" / "static" / "sitefind.js").read_text(encoding="utf-8")
    assert "dataset.k" in script and "inOrder" in script
    query = (templates / "query.html").read_text(encoding="utf-8")
    assert "沒有文件進庫" in query and "都有日期" in query  # why a site's query has no rows, not only that it has none
    # 2026-11-18: a site's list is cut at the query's limit and says so with the site's whole count; a column of numbers
    # reads from the right
    assert 'canned_query == "site_docs" and at.cur and at.cur.docs > display_rows|length' in query
    assert "只列最新的" in query and "classList.add('numc')" in query
    assert "td.numc" in (KIT / "datasette" / "static" / "tc.css").read_text(encoding="utf-8")
    # the home's list: a site's name opens its page in 檢視台 (structure design 2026-11-17 section 二); a query's list
    # keeps switching that query's site
    assert 'name_go=site_page' in (templates / "index.html").read_text(encoding="utf-8")
    assert "name_go" not in query and '{{ name_go or go }}{{ s.site|urlencode }}' in piece


def test_no_page_prints_a_description_and_the_table_page_draws_the_column_chips():
    templates = KIT / "datasette" / "templates"
    for name in ("table.html", "query.html", "database.html", "index.html"):
        assert "_description_source_license" not in (templates / name).read_text(encoding="utf-8"), name
    table = (templates / "table.html").read_text(encoding="utf-8")
    assert "pick.columns" in table and "pick.default_url" in table and "pick.all_url" in table
    queries = _metadata()["databases"]["aca"]["queries"]
    assert not [name for name, q in queries.items() if q.get("description")]
    assert max(len(q["title"]) for q in queries.values()) <= 10


def test_a_facet_gets_the_time_to_count_the_documents_kinds():
    command = " ".join(_compose()["services"]["datasette"]["command"])
    assert "facet_time_limit_ms 3000" in command


# ---------------------------------------------------------------- names and floating words (owner 11-15, the 7th round)

def test_the_panel_is_called_what_it_is_and_every_page_floats_its_detail_the_way_the_overview_does():
    """Owner 11-15: 「datasettle那个文字的名字叫“翻库里的表” 你看看这像话吗？」 and 「想缩短文字 但又想保留细节导致不上不下
    的」, pointing at 资料总览's floating words. The panel is 資料庫; a name is shown and its detail floats over it in the
    overview's card, by one script that 檢視台 runs too; no page leans on the browser's own slow title box."""
    from nknu.ops import look

    base = (KIT / "datasette" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "{{ nk_bar|safe }}" in base and "urls.path('/static/tip.js')" in base
    assert _metadata()["title"] == "資料庫"
    tip = (KIT / "datasette" / "static" / "tip.js").read_text(encoding="utf-8")
    assert tip == look.TIP_JS and "data-tip-h" in tip and "textContent" in tip and "innerHTML" not in tip
    css = (KIT / "datasette" / "static" / "tc.css").read_text(encoding="utf-8")
    assert ".tip {" in css and "background: var(--surface)" in css and ".tip{" in look.CSS
    # Datasette's app.css makes every div a block, over the hidden attribute: an empty card sat at the page's foot
    assert ".tip[hidden] { display: none; }" in css and ".tip[hidden]{display:none}" in look.CSS
    for template in TEMPLATES:
        source = template.read_text(encoding="utf-8")
        assert " title=" not in source and "<title>{{" not in source.replace("<title>{% block title", ""), template.name
        assert "翻庫" not in source.split("-#}")[-1], template.name  # the old name only in a comment's history
    for name in ("cells", "guide", "undated", "columns"):
        source = (KIT / "datasette" / "plugins" / f"{name}.py").read_text(encoding="utf-8")
        assert 'title="' not in source, name


def test_a_tables_filter_names_its_columns_and_comparisons_in_chinese():
    table = (KIT / "datasette" / "templates" / "table.html").read_text(encoding="utf-8")
    assert "op_words" in table and 'get("short") or c }}' in table and "篩選欄位…" in table
    assert "下載的其他形式" not in table and "table_definition" not in table  # the page keeps what is read
    guide = _plugin("guide")
    datasette_ops = ("exact", "not", "contains", "endswith", "startswith", "gt", "gte", "lt", "lte", "like", "notlike",
                     "glob", "in", "notin", "arraycontains", "arraynotcontains", "date", "isnull", "notnull",
                     "isblank", "notblank")  # Datasette 0.65.5's filters.py, every lookup it offers
    assert set(guide.OPS) == set(datasette_ops) and all(guide.OPS.values())


def test_one_word_names_one_thing_on_every_panel():
    """The words the queries' columns use are the tables' short names: 類型, not 種類 here and 類型 there."""
    queries = _metadata()["databases"]["aca"]["queries"]
    named = {c for q in queries.values() for c in re.findall(r'as "([^"]+)"', q["sql"])}
    assert {"類型", "主料／背景", "原因", "怎麼辦", "頁面上的日期", "所屬文件"} <= named
    assert not named & {"種類", "主料還是背景", "為什麼", "為什麼沒有日期", "所屬的那一份", "哪一種", "從哪裡讀到的"}



# ---------------------------------------------------------------- redesign 2026-11-17 (research/dashboard_redesign_2026-11-17)

def test_the_list_of_sites_ends_on_a_whole_row_and_fades_while_more_wait_below():
    """diagnosis.md 資料庫 ①: the box was 298px over rows of about 35px, so its 9th row was cut in half."""
    css = (KIT / "datasette" / "static" / "tc.css").read_text(encoding="utf-8")
    row = re.search(r"\.sitefind li \{[^}]*\bheight: (\d+)px;", css)
    box = re.search(r"\.sitefind ol \{[^}]*max-height: calc\((\d+) \* (\d+)px\)", css)
    assert row and box and box.group(2) == row.group(1) and "box-sizing: border-box" in row.group(0)
    link = re.search(r"\.sitefind li a:link, \.sitefind li a:visited \{[^}]*\}", css).group(0)
    assert "padding: 0;" in link and f"line-height: {int(row.group(1)) - 1}px" in link  # the row's rule is the 1px left
    assert ".sitefind ol.more {" in css and "mask-image: linear-gradient" in css
    script = (KIT / "datasette" / "static" / "sitefind.js").read_text(encoding="utf-8")
    assert "classList.toggle('more'" in script and "addEventListener('scroll', hint" in script


def test_the_table_panel_names_a_column_first_and_its_code_after_it_quieter():
    """diagnosis.md 資料庫 ③: the codes (doc_id…) stood in a column of their own, as prominent as the names."""
    jinja2 = pytest.importorskip("jinja2")
    source = (KIT / "datasette" / "templates" / "index.html").read_text(encoding="utf-8")
    start = source.index("{%- for c in t.columns %}")
    loop = source[start:source.index("{%- endfor %}", start) + len("{%- endfor %}")]
    rows = jinja2.Environment().from_string(loop).render(t={"columns": [
        {"name": "doc_id", "short": "文件編號", "rest": "一份文件的編號"}, {"name": "x_raw", "short": "", "rest": ""}]})
    assert '<th><span data-tip-h="文件編號" data-tip="一份文件的編號">文件編號</span><code>doc_id</code></th>' in rows
    assert "<th><span>x_raw</span></th>" in rows and "<td" not in rows  # a column without a name is not named twice
    css = (KIT / "datasette" / "static" / "tc.css").read_text(encoding="utf-8")
    code = re.search(r"\.insp table\.cols th code \{[^}]*\}", css).group(0)
    assert "font-size: 11px" in code and "var(--ink3)" in code and "var(--mono)" in code


# ---------------------------------------------------------------- the new question set's totals (11-21)

def _eval_summary():
    spec = importlib.util.spec_from_file_location("eval_summary", REPO_ROOT / "deploy/lab-inspect/eval_summary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUN_ROWS = [{"id": "B001", "path": "B", "half": "平时", "question": "暑修怎么报名？", "q": 1, "a1": 4, "a": 2},
            {"id": "B002", "path": "B", "half": "封存", "question": "封存的题目", "q": None, "a1": 12, "a": 9},
            {"id": "B003", "path": "B", "half": "封存", "question": "封存的另一题", "q": 3, "a1": None, "a": None},
            {"id": "A001", "path": "A", "half": "平时", "question": "从文件出的题", "q": 11, "a1": 1, "a": 1}]


def test_the_new_question_sets_summary_holds_totals_and_no_question():
    import json

    module = _eval_summary()
    questions = {"questions": [{"path": "B 从学生出发", "verdict": "有答案"}] * 3 + [
        {"path": "B 从学生出发", "verdict": "只有相关"}, {"path": "B 从学生出发", "verdict": "库里没有"},
        {"path": "A 从文件出题", "verdict": "有答案"}]}
    got = module.summary({"library": "aca.db 2026-11-21T04:03:26+08:00（88966 份文件）", "rows": RUN_ROWS}, questions,
                         "2026-11-21T10:00:00+08:00", "final")
    groups = {g["key"]: g for g in got["groups"]}
    assert groups["B"] == {"key": "B", "n": 3, "q": [1, 2, 2], "a1": [0, 0, 1], "a": [0, 1, 2]}
    assert groups["B/open"]["n"] == 1 and groups["B/sealed"]["q"] == [0, 1, 1] and groups["A"]["a"] == [1, 1, 1]
    assert got["b_asked"] == {"all": 5, "answered": 3, "expired": 0, "related_only": 1, "absent": 1}
    assert got["questions"] == 6
    # a time-limited question past its date when the run was made is not among the run's rows: said, not hidden
    late = {"questions": [{"path": "B 从学生出发", "verdict": "有答案", "expires_on": "2026-11-30"},
                          {"path": "B 从学生出发", "verdict": "有答案", "expires_on": "2026-12-31"}]}
    assert module.summary({"rows": []}, late, "2026-12-01T10:00:00+08:00", "x")["b_asked"]["expired"] == 1
    said = json.dumps(got, ensure_ascii=False)
    assert "暑修" not in said and "B001" not in said and "封存的题目" not in said  # counts only: no question, no id
