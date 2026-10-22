from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.settings import load_settings


def test_cli_help():
    assert CliRunner().invoke(app, ["--help"]).exit_code == 0


def test_settings_defaults(tmp_path):
    s = load_settings({"NKNU_DATA_DIR": str(tmp_path), "NKNU_DISABLED_SOURCES": "sso_news, pe_news"})
    assert s.facts_path == tmp_path / "facts.db" and s.serve_path == tmp_path / "serve.db"
    assert s.disabled_sources == {"sso_news", "pe_news"} and s.readonly is False
    assert s.allowed_hosts == ["127.0.0.1", "localhost"]


def test_settings_flags(tmp_path):
    s = load_settings({"NKNU_DATA_DIR": str(tmp_path), "NKNU_READONLY": "1", "NKNU_ALLOWED_HOSTS": "a.example, b.example"})
    assert s.readonly is True and s.allowed_hosts == ["a.example", "b.example"] and s.warc_dir == tmp_path / "warc"
