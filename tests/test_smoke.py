from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.settings import load_settings


def test_cli_help():
    assert CliRunner().invoke(app, ["--help"]).exit_code == 0


def test_settings_defaults(tmp_path):
    s = load_settings({"NKNU_DATA_DIR": str(tmp_path)})
    assert s.facts_path == tmp_path / "facts.db" and s.warc_dir == tmp_path / "warc"
    assert s.allowed_hosts == ["127.0.0.1", "localhost"]


def test_settings_flags(tmp_path):
    s = load_settings({"NKNU_DATA_DIR": str(tmp_path), "NKNU_ALLOWED_HOSTS": "a.example, b.example"})
    assert s.allowed_hosts == ["a.example", "b.example"] and s.warc_dir == tmp_path / "warc"
