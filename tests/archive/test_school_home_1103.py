"""A file or picture link the school now sends to a school host's home page is the school's dead link (11-03): the
11-02 教务处 export's 133 old www.nknu.edu.tw/~ attachments and pictures all redirect to https://w3.nknu.edu.tw/ and
showed as 连接失败 (not_file), our failure. The views now make such a link gone with its own reason code (gone_home),
from the landing address already stored, so no row is fetched again; the export keeps 完整度 学校那边已经不存在 and
says why. A page link sent there, and a file sent to any other page, stay as they were. Addresses are made up."""

from __future__ import annotations

import pytest

from nknu.archive import outcome, progress
from nknu.archive.frontier import Frontier
from tests.archive.test_export_1027 import BASE, _add, _export, _res, make_archive

HOME = "https://w3.nknu.edu.tw/"
OLD = "https://www.nknu.edu.tw/~academic/index/people/1001a.pdf"
OLD_PIC = "https://www.nknu.edu.tw/~academic/index/images/map.jpg"
OLD_PAGE = "https://www.nknu.edu.tw/~academic/index.htm"


def _archive(tmp_path, landing: str = HOME, home_kind: str = "page"):
    """The export test's archive, plus the school's home page (or another page) and old addresses sent there from d1:
    an attachment, a picture and a page link. home_kind file: the landing row as the 教务处 archive has it (queued as
    the target of an attachment's redirect, so its own result is not_file)."""
    arch = make_archive(tmp_path / "archive").open()
    host = landing.split("/")[2]
    arch.page("home", landing, "<html><body>國立高雄師範大學</body></html>", host=host, site=host, owner_site=host,
              kind=home_kind, kind0=home_kind)
    d1 = arch.ids["d1"]
    for name, url, kind, role in (("old", OLD, "file", "file"), ("old_pic", OLD_PIC, "image", "img"),
                                  ("old_page", OLD_PAGE, "page", "item")):
        arch.ids[name] = _add(arch.f, url=url, final_url=landing, host="www.nknu.edu.tw", kind=kind, kind0=kind,
                              tier=2 if kind != "page" else 3, status=301, bytes=0)
        _res(arch.f, d1, url, role, need="must", res_id=arch.ids[name], text=name)
    arch.close()
    return arch


def _resources(arch) -> dict[int, dict]:
    f = Frontier(arch.root / "frontier.db", readonly=True)
    try:
        return {r["via"]: dict(r) for r in f.q("SELECT * FROM page_resource WHERE via IS NOT NULL")}
    finally:
        f.close()


@pytest.mark.parametrize("home_kind", ["page", "file"])
def test_a_file_or_picture_sent_to_a_school_home_page_is_gone_with_its_own_reason(tmp_path, home_kind):
    arch = _archive(tmp_path, home_kind=home_kind)
    res = _resources(arch)
    for name in ("old", "old_pic"):
        r = res[arch.ids[name]]
        assert (r["result"], r["gone_why"]) == ("gone", outcome.GONE_HOME), name
    assert "gone" in outcome.GROUPS["good"]  # nothing more to fetch: the page is not 连接失败 for it


@pytest.mark.parametrize("home_kind", ["page", "file"])
def test_the_export_keeps_the_gone_category_and_says_it_went_home(tmp_path, home_kind):
    arch = _archive(tmp_path, home_kind=home_kind)
    _got, _out, rows, _readme = _export(arch, tmp_path)
    for url in (OLD, OLD_PIC):
        row = rows[url]
        assert row["完整度"] == progress.BUCKET_ZH["gone"] == "学校那边已经不存在", url
        assert row["分类"] == "学校那边已经不存在"
        assert row["结果"] == outcome.GONE_WHY_LABEL[outcome.GONE_HOME] == "链接已失效：跳回学校首页"
        assert outcome.GONE_HOME in row["原因代码"].split()
        assert row["最后落地网址"] == HOME and row["本地档案"] == ""
    assert rows[BASE + "newsview.aspx?nid=1"]["完整度"] != "连接失败"


@pytest.mark.parametrize("home_kind", ["page", "file"])
def test_a_file_sent_to_a_page_that_is_not_the_root_stays_not_file(tmp_path, home_kind):
    arch = _archive(tmp_path, landing="https://w3.nknu.edu.tw/news.aspx", home_kind=home_kind)
    res = _resources(arch)
    for name in ("old", "old_pic"):
        assert (res[arch.ids[name]]["result"], res[arch.ids[name]]["gone_why"]) == ("not_file", None), name
    _got, _out, rows, _readme = _export(arch, tmp_path)
    assert rows[OLD]["结果"] == outcome.RESULT_LABEL["not_file"] and rows[OLD]["完整度"] == "连接失败"


def test_a_root_page_off_the_school_hosts_stays_not_file(tmp_path):
    arch = _archive(tmp_path, landing="https://www.example.com/")
    res = _resources(arch)
    assert (res[arch.ids["old"]]["result"], res[arch.ids["old"]]["gone_why"]) == ("not_file", None)


def test_a_page_link_sent_to_the_home_page_is_unchanged(tmp_path):
    arch = _archive(tmp_path)
    r = _resources(arch)[arch.ids["old_page"]]
    assert (r["result"], r["gone_why"]) == ("ok", None)  # the home page came back: a page's link judged where it lands


def test_a_real_file_stays_ok(tmp_path):
    arch = _archive(tmp_path)
    r = _resources(arch)[arch.ids["rule"]]
    assert (r["result"], r["gone_why"]) == ("ok", None)
    _got, _out, rows, _readme = _export(arch, tmp_path)
    assert rows[BASE + "uploadfile/rule.pdf"]["结果"] == outcome.RESULT_LABEL["ok"]


def test_school_home_address():
    for url in (HOME, "https://w3.nknu.edu.tw", "http://nknu.edu.tw/", "http://c.nknu.edu.tw/"):
        assert outcome.school_home(url), url
    for url in ("https://w3.nknu.edu.tw/news.aspx", "https://w3.nknu.edu.tw/?lang=en", "https://www.example.com/",
                "https://nknu.edu.tw.example.com/", "https://w3.nknu.edu.tw/#top", None, ""):
        assert not outcome.school_home(url), url
