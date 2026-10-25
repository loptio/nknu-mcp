"""Rules added during the first full run (2026-10-24 09:20): learning platforms (Moodle), search-result pages and an
image resizer are out of scope; images, video and audio are not downloaded whatever their URL; requests made before a
rule existed are reported as quarantined, not as breaches."""

import httpx
import pytest

from nknu.archive import scope
from nknu.archive.monitor import scope_and_robots
from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.fetch import Client, UnwantedType
from tests.archive.test_archive import drain, make, seed, snap


@pytest.mark.parametrize("url, reason", [
    ("https://cc.nknu.edu.tw/moodle/course/view.php?id=3", "system"),
    ("https://moocs.nknu.edu.tw/moodle/calendar/view.php?view=day&time=1", "system"),
    ("https://cc.nknu.edu.tw/moodle/user/profile.php?id=9", "system"),
    ("https://nknulib.nknu.edu.tw/search*cht/a?searchtype=t", "system"),  # the whole host since record 三·19
    ("https://lis.nknu.edu.tw/search*cht/a?searchtype=t", "search"),
    ("https://www.nknu.edu.tw/search.php?q=x", "search"),
    ("https://geo.nknu.edu.tw/includes/timthumb/timthumb.php?src=a.jpg", "media"),
])
def test_systems_searches_and_resizers_are_out(url, reason):
    assert scope.judge(url).reason == reason


@pytest.mark.parametrize("url", ["https://rd.nknu.edu.tw/research/plan.aspx", "https://c.nknu.edu.tw/aca/Page.aspx?PN=8",
                                 "https://www.nknu.edu.tw/~moodlefan/x.html"])
def test_ordinary_pages_stay_in(url):
    assert scope.judge(url).ok


def test_images_without_an_image_extension_are_not_downloaded():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, content=b"\xff\xd8" + b"0" * 5000, headers={"content-type": "image/jpeg"})

    c = Client(transport=httpx.MockTransport(handler), sleep=lambda s: None, skip_types=("image/", "video/", "audio/"))
    with pytest.raises(UnwantedType):
        c.fetch(Target("https://lis.nknu.edu.tw/_dl/abc"), RateLimit(), max_bytes=10_000_000)
    assert seen == ["/_dl/abc"]


def test_the_crawler_skips_an_image_answer(tmp_path):
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "/pic": UnwantedType("image/jpeg not downloaded")})
    seed(crawler, "https://lis.nknu.edu.tw/pic")
    drain(crawler, net, "ip:lis.nknu.edu.tw")
    row = crawler.frontier.q("SELECT state, reason FROM url WHERE url='https://lis.nknu.edu.tw/pic'")[0]
    assert (row["state"], row["reason"]) == ("skipped", "media_type")


def test_requests_before_a_rule_change_are_not_breaches(tmp_path):
    crawler, _, _ = make(tmp_path, {})
    f = crawler.frontier
    f.request("ip:1", "cc.nknu.edu.tw", "https://cc.nknu.edu.tw/moodle/course/view.php?id=1", "page", 100.0, 101.0, 200,
              None)
    assert any("system" in x for x in scope_and_robots(f))
    f.x("INSERT INTO rule_change(reason, at, note) VALUES ('system', 200.0, 'Moodle')")
    assert not any("system" in x for x in scope_and_robots(f))
    f.request("ip:1", "cc.nknu.edu.tw", "https://cc.nknu.edu.tw/moodle/course/view.php?id=2", "page", 300.0, 301.0, 200,
              None)
    assert any("system" in x for x in scope_and_robots(f))  # after the rule: a breach again


@pytest.mark.parametrize("url, out", [
    ("https://id.nknu.edu.tw/linkpw4/category/link1L/category/category/category/category/", True),
    ("https://id.nknu.edu.tw/a/b/b/c", False),  # twice in a row is allowed (Heritrix default)
    ("https://id.nknu.edu.tw/a/b/b/b/c", True),
    ("https://x.nknu.edu.tw/" + "/".join(f"s{i}" for i in range(21)), True),
    ("https://x.nknu.edu.tw/" + "/".join(f"s{i}" for i in range(20)), False),
])
def test_pathological_paths_are_traps(url, out):
    assert (scope.judge(url).reason == "trap") is out
