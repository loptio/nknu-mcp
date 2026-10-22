from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.core.factsdb import connect
from nknu.core.writer import add_alert
from tests.factories import T0, make_db


def test_alerts_list_and_resolve(tmp_path):
    c = make_db(tmp_path)
    aid = add_alert(c, adapter="pe_news", level="warn", code="parse_issue", message="日期看不懂", now=T0)
    c.commit()
    env = {"NKNU_DATA_DIR": str(tmp_path)}
    r = CliRunner().invoke(app, ["alerts"], env=env)
    assert r.exit_code == 0 and "日期看不懂" in r.output
    assert CliRunner().invoke(app, ["alerts", "--resolve", str(aid)], env=env).exit_code == 0
    assert connect(tmp_path / "facts.db").execute("SELECT resolved_at FROM alert WHERE id=?", (aid,)).fetchone()[0]
    assert "日期看不懂" not in CliRunner().invoke(app, ["alerts"], env=env).output


def test_crawl_without_adapters_is_noop(tmp_path, monkeypatch):
    make_db(tmp_path).close()
    monkeypatch.setattr("nknu.crawl.adapter.REGISTRY", {})
    monkeypatch.setattr("nknu.crawl.adapter.load_adapters", dict)
    r = CliRunner().invoke(app, ["crawl", "--no-build"], env={"NKNU_DATA_DIR": str(tmp_path)})
    assert r.exit_code == 0, r.output
