"""11-06 (the deploy check, 【Claude 的执行办法】): `archive run --sites ""` (run_loop.sh when batch1_sites.txt is
gone) starts nothing, never a crawl of every site."""
import pytest
from typer.testing import CliRunner

from nknu.archive import crawler as mod


@pytest.mark.parametrize("given", ["", " , ", ","])
def test_run_refuses_an_empty_site_list(tmp_path, monkeypatch, given):
    from nknu.admin.cli import app

    class NoCrawler:
        def __init__(self, *a, **k):
            raise AssertionError("built a crawler for an empty --sites")

    monkeypatch.setattr(mod, "Crawler", NoCrawler)
    out = CliRunner().invoke(app, ["archive", "run", "--dir", str(tmp_path / "a"), "--sites", given])
    assert out.exit_code == 1 and "--sites 是空的" in out.output
    assert not (tmp_path / "a").exists()  # no run.lock, no frontier.db: nothing was started
