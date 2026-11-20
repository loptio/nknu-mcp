"""Round two, batch two (design docs/superpowers/specs/2026-11-17-round2-batch2-design.md §三.6; the 11-14 handover's
old debt c-3; the owner's 11-08 「按相簿跳过」 and 11-09 item 16): a Drive folder is an album, its pictures listed and
never downloaded, when its name says so (now 活動照 and 合照 too: 生科系's 02_活動照, 00_大合照), when the list of
album folders names it (config/album_folders.json: the folders the owner had skipped by hand) or a folder above it,
when a folder above it has an album's name, or when an album page links it (a page links.album_page reads as one).
Made-up folders."""

from __future__ import annotations

import json

import pytest

from nknu.archive import google_api as g
from nknu.archive import scope
from nknu.archive.frontier import Frontier
from tests.archive.test_google_api import FOLDER_ID, FOLDER_LINK, PLAN, SUB_ID, add_page, answer, entries, item


@pytest.fixture
def f(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    yield fr
    fr.close()


@pytest.mark.parametrize("name", ["02_活動照", "00_大合照", "畢業典禮合照"])
def test_the_new_words_that_make_a_folder_an_album(name):
    assert g.album_name(name)


def _use(f, page, href, album_page=False):
    add = g.frontier_adder(f)
    got = []
    for u in g.uses(PLAN, scope.canonical(href), page, "link"):
        rid = add(u.url, u.placement, page, "link")[0] if u.url else None
        got += g.attach(f, u, rid, album_page=album_page)
    f.add_page_res(page["id"], got)
    g.resync(f, page["id"])


def _sub_with_pictures(f, top_name: str, sub_name: str = "01"):
    """A page links a folder named top_name; it holds a subfolder (sub_name) of pictures only."""
    page = add_page(f)
    _use(f, page, FOLDER_LINK)
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name=top_name))
    answer(f, g.list_url(FOLDER_ID), {"files": [item(SUB_ID, g.FOLDER, name=sub_name)]})
    answer(f, g.item_url(SUB_ID), item(SUB_ID, g.FOLDER, name=sub_name))
    pics = [f"1PiC{i}AbCdEfGhIjKlMnOpQrSt" for i in range(2)]
    answer(f, g.list_url(SUB_ID), {"files": [item(p, "image/jpeg", name=f"IMG_{i}.jpg", size="1")
                                              for i, p in enumerate(pics)]})
    return page, pics


def _album(f, pics) -> bool:
    return all(f.row(g.item_url(p))["reason"] == "album:drive" for p in pics)


def test_a_subfolder_of_an_album_is_an_album(f):
    _page, pics = _sub_with_pictures(f, "113 生物科能力競賽照片")
    assert _album(f, pics)


def test_a_subfolder_of_a_plain_folder_is_no_album(f):
    _page, pics = _sub_with_pictures(f, "113 生物科能力競賽")
    assert not _album(f, pics)


def test_a_listed_folder_and_what_it_holds_are_albums(f, tmp_path, monkeypatch):
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "album_folders.json").write_text(json.dumps({"folders": [{"id": FOLDER_ID, "why": "测试"}]}),
                                             encoding="utf-8")
    monkeypatch.setenv("NKNU_CONFIG_DIR", str(conf))
    _page, pics = _sub_with_pictures(f, "113 生物科能力競賽")
    assert _album(f, pics)


def test_a_folder_an_album_page_links_is_an_album(f):
    page = add_page(f, url="https://c.nknu.edu.tw/aca/album.aspx?aid=1")
    _use(f, page, FOLDER_LINK, album_page=True)
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name="113"))
    pics = ["1PiCaAbCdEfGhIjKlMnOpQrSt"]
    answer(f, g.list_url(FOLDER_ID), {"files": [item(pics[0], "image/jpeg", name="IMG.jpg", size="1")]})
    assert _album(f, pics)
    assert entries(f, page)[f"drive:{pics[0]}"]["why"] == "album:drive"


def test_the_committed_list_names_the_folders_skipped_by_hand():
    from nknu.settings import load_settings

    data = json.loads((load_settings().config_dir / "album_folders.json").read_text(encoding="utf-8"))
    ids = {e["id"] for e in data["folders"]}
    assert {"1Tcg5_7ux0OFrBiKYtybRRpNEQWaasEsu", "1Lq6X7gcSaRa_HPsj1tsKa9OgQ6Mr5HbU"} <= ids
    assert all(e.get("why") for e in data["folders"])
