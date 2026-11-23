"""YouTube on the school's pages (owner 10-26, scope record 五·15: 「先不用管youtube影片，不用api 先记下youtube链接和
标题也可以吧」): no request ever goes to YouTube; links.extract notes each video a page links or embeds with the title
the school's page gives it (an embed's title, a link's text), the frontier keeps them in the video table for the
sorting stage, and the dashboard never counts them as something the page lacks (out:social, a rule)."""

from __future__ import annotations

import pytest

from nknu.archive import links, round1, scope
from nknu.archive.frontier import Frontier

PAGE = "https://c.nknu.edu.tw/aca/news_detail.aspx?id=7"
ACA = {"owner_site": "c.nknu.edu.tw/aca", "tier": 0}
HTML = """<html><body><main>
<p><a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ&amp;t=10s">113 學年度校慶影片</a></p>
<p><a href="https://youtu.be/AbCdEfGhIjK?si=share"><img src="/thumb.jpg" alt="招生宣傳短片"></a></p>
<iframe src="https://www.youtube.com/embed/ZyXwVuTsRqP?rel=0" title="畢業典禮直播" width="560" height="315"></iframe>
<iframe src="about:blank" data-src="https://www.youtube-nocookie.com/embed/QwErTyUiOpA" title="系所介紹"></iframe>
<embed src="http://www.youtube.com/v/MnBvCxZlKjH&amp;hl=zh_TW" width="425" height="350">
<a href="https://www.youtube.com/shorts/Shorts12345">新生入學須知</a>
<a href="https://www.google.com/url?q=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DWrApPeD1234&amp;sa=D">講座錄影</a>
<a href="https://www.youtube.com/watch?v=ZyXwVuTsRqP">同一支，再連一次</a>
<a href="https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv">本處頻道</a>
<a href="https://www.youtube.com/playlist?list=PLabcdefghijklmnop">活動影片清單</a>
<a href="https://www.youtube.com/watch?v=short">壞掉的連結</a>
</main></body></html>"""


def videos() -> dict[str, links.Video]:
    return {v.video_id: v for v in links.extract(HTML, PAGE).videos}


def test_each_video_a_page_links_or_embeds_is_noted_with_the_schools_title():
    got = videos()
    assert {k: (v.title, v.how) for k, v in got.items()} == {
        "dQw4w9WgXcQ": ("113 學年度校慶影片", "link"),
        "AbCdEfGhIjK": ("招生宣傳短片", "link"),  # a picture link: its alt text
        "ZyXwVuTsRqP": ("畢業典禮直播", "embed"),  # embedded and linked: one video, the embed's title first written
        "QwErTyUiOpA": ("系所介紹", "embed"),  # a lazy embed's data-src, on YouTube's no-cookie host
        "MnBvCxZlKjH": ("", "embed"),  # the old Flash embed: no title on the page
        "Shorts12345": ("新生入學須知", "link"),
        "WrApPeD1234": ("講座錄影", "link"),  # Google Sites' wrapper taken off
        "list:PLabcdefghijklmnop": ("活動影片清單", "link"),  # a playlist, under its own key (second look F7)
    }
    assert got["dQw4w9WgXcQ"].url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s"
    assert got["ZyXwVuTsRqP"].zone == "unknown"  # no platform profile here: anything not layout counts as body


def test_channels_and_broken_ids_are_links_not_videos_and_a_playlist_is_kept_as_one():
    ids = set(videos())
    assert not any(i.startswith(("UC", "PL")) for i in ids) and "short" not in ids
    assert "list:PLabcdefghijklmnop" in ids and not any(i.startswith("UC") or "channel" in i for i in ids)


def test_videos_in_a_google_sites_embed_code_block_count_on_the_page():
    code = '<iframe src="https://www.youtube.com/embed/EmBeDcOdE01" title="社團成果"></iframe>'
    html = f'<html><body><div data-code="{code.replace(chr(34), "&quot;")}"></div></body></html>'
    got = links.extract(html, "https://sites.google.com/mail.nknu.edu.tw/x/home").videos
    assert [(v.video_id, v.title, v.how) for v in got] == [("EmBeDcOdE01", "社團成果", "embed")]


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/AbCdEfGhIjK",
    "https://www.youtube.com/embed/ZyXwVuTsRqP", "https://www.youtube-nocookie.com/embed/QwErTyUiOpA",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
])
def test_no_video_is_ever_fetched_and_none_is_a_part_the_page_lacks(url):
    canon = scope.canonical(url)
    assert scope.judge(canon).reason == "social" and "social" in scope.HARD_OUT
    for how in round1.HOW:
        if how == "click":
            continue  # a platform rule never presses YouTube
        got = round1.place(round1.load(), canon, ACA, how)
        assert (got.verdict, got.reason, got.row) == ("out", "out:social", False), how


def test_the_frontier_keeps_each_pages_videos(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    try:
        for v in videos().values():
            f.video(7, v)
        f.video(7, links.Video("ZyXwVuTsRqP", "https://www.youtube.com/embed/ZyXwVuTsRqP", "畢業典禮直播", "embed",
                               "body"))  # seen again (the page fetched again): still one row
        rows = {r["video_id"]: r for r in f.q("SELECT * FROM video WHERE page_id=7")}
        assert len(rows) == 8 and rows["ZyXwVuTsRqP"]["title"] == "畢業典禮直播"
        assert rows["dQw4w9WgXcQ"]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s"
    finally:
        f.close()


def test_an_embedded_video_leaves_the_page_complete(tmp_path):
    """The crawler writes the embed as the page's frame left out by a rule (why out:social): never 缺内嵌页, never 下一轮."""
    f = Frontier(tmp_path / "frontier.db")
    try:
        f.x("INSERT INTO page_type(key, site, verdict) VALUES ('t:ok', 'c.nknu.edu.tw/aca', 'ok')")
        with f.lock:
            pid = f.conn.execute(
                "INSERT INTO url(url, path, host, site, owner_site, tier, kind, kind0, depth, found_via, state, status, "
                "page_type) VALUES (?,?,?,?,?,0,'page','page',0,'seed','done',200,'t:ok')",
                (PAGE, PAGE.split("?")[0], "c.nknu.edu.tw", "c.nknu.edu.tw/aca", "c.nknu.edu.tw/aca")).lastrowid
        f.x("INSERT INTO page_res(page_id, res_key, how, role, need, why) VALUES (?,?,?,?,?,?)",
            (pid, "https://www.youtube.com/embed/ZyXwVuTsRqP", "link_only", "iframe", "must", "out:social"))
        r = f.q("SELECT level, label FROM page_completeness WHERE id=?", (pid,))[0]
        assert (r["level"], r["label"]) == ("complete", "complete")
    finally:
        f.close()
