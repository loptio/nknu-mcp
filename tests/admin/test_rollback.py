"""`nknu build --rollback`: one step back, until the next build (F-rollback-one-step)."""

from typer.testing import CliRunner

from nknu.admin.cli import app


def _rollback(data):
    return CliRunner().invoke(app, ["build", "--rollback"], env={"NKNU_DATA_DIR": str(data)})


def test_rollback_says_it_is_temporary_and_a_second_one_is_a_friendly_error(tmp_path):
    (tmp_path / "serve.db").write_bytes(b"new")
    (tmp_path / "serve.db.prev").write_bytes(b"old")
    r = _rollback(tmp_path)
    assert r.exit_code == 0, r.output
    assert (tmp_path / "serve.db").read_bytes() == b"old" and not (tmp_path / "serve.db.prev").exists()
    assert "00:05" in r.output and "nknu undo" in r.output  # it lasts until the next build; undo is the lasting fix
    r = _rollback(tmp_path)
    assert r.exit_code == 1 and r.exception is not None and isinstance(r.exception, SystemExit)
    assert "只能退一步" in r.output and "nknu undo" in r.output and "Traceback" not in r.output
    assert (tmp_path / "serve.db").read_bytes() == b"old"
