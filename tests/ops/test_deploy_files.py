"""plan 2026-11-15 T7: what the lab PC runs for the inspection tools. The files are configuration, so the tests pin
what a mistake in them would cost: a service reachable from another machine, a page answered to another site's name,
a Datasette description or canned query that no longer fits the schema."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import shutil
import sqlite3
import subprocess

import pytest
import yaml

from nknu.content.schema import create_schema
from nknu.settings import REPO_ROOT

KIT = REPO_ROOT / "deploy" / "lab-inspect"
PORTS = {"datasette": "127.0.0.1:8001:8001", "mcp": "127.0.0.1:8766:8766", "ops": "127.0.0.1:8878:8878"}


def _plugin():
    spec = importlib.util.spec_from_file_location("local_only", KIT / "datasette" / "plugins" / "local_only.py")
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


def test_datasette_is_pinned_and_reads_its_settings_from_the_volume():
    datasette = _compose()["services"]["datasette"]
    assert datasette["image"] == "datasetteproject/datasette:0.65.5"
    command = " ".join(datasette["command"])
    assert "/work/inspect-code/deploy/lab-inspect/datasette/metadata.yaml" in command
    assert "/work/inspect-code/deploy/lab-inspect/datasette/plugins" in command and "allow_download off" in command


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
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


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
        names = {w[1:] for w in query["sql"].replace("\n", " ").split() if w.startswith(":")}
        params = {n.strip("(),|'%"): "x" for n in names}
        empty.execute(query["sql"], params).fetchall()
        assert query["title"], name


def test_the_control_database_shares_the_descriptions():
    databases = _metadata()["databases"]
    assert databases["aca1115"]["tables"] == databases["aca"]["tables"]


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
