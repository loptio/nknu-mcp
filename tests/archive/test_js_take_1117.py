"""The JS sites' handoff, item ④ (research/jssites_2026-11-17/handoff_crawler.md; plan
docs/superpowers/plans/2026-11-17-jsfetch-integration.md task 3): a page's rendered look (what the browser showed, jsfetch)
is read as the page's own answer is for what it links: the links it shows are admitted by the rules (new detail pages
and attachments pending), its page_res added to (never replaced), and reading the same look again adds nothing. The
real crawler on a fake network; made-up pages."""

from __future__ import annotations

from tests.archive.test_archive import seed, snap
from tests.archive.test_pilot_fixes import make, work

LIST = "https://c.nknu.edu.tw/aca/news.aspx"
RAW = "<html><head><title>最新消息</title></head><body><div id=\"app\"></div><script src=\"/app.js\"></script></body></html>"
SHOWN = ("<html><head><title>最新消息</title></head><body><div id=\"app\"><ul>"
         "<li><a href=\"/aca/news_detail.aspx?id=1\">公告一</a></li><li><a href=\"/aca/news_detail.aspx?id=2\">公告二</a></li>"
         "<li><a href=\"/aca/files/form.pdf\">申請表</a></li></ul></div></body></html>")


def _fetched(tmp_path):
    crawler, net, _clock = make(tmp_path, {("GET", "/robots.txt"): snap("x", 404),
                                          ("GET", "/aca/news.aspx"): snap(LIST, 200, html=RAW)})
    seed(crawler, LIST)
    work(crawler, net, "ip:c.nknu.edu.tw")
    return crawler, crawler.frontier.row(LIST)


def test_a_rendered_look_admits_what_it_shows(tmp_path):
    crawler, row = _fetched(tmp_path)
    f = crawler.frontier
    before = {r["url"] for r in f.q("SELECT url FROM url")}
    n = crawler.take_rendered(row, SHOWN, None, None)
    after = {r["url"]: r for r in f.q("SELECT url, state, kind FROM url")}
    new = set(after) - before
    assert {"https://c.nknu.edu.tw/aca/news_detail.aspx?id=1", "https://c.nknu.edu.tw/aca/news_detail.aspx?id=2",
            "https://c.nknu.edu.tw/aca/files/form.pdf"} <= new and n == len(new)
    assert all(after[u]["state"] == "pending" for u in new)
    linked = {r["dst"] for r in f.q("SELECT dst FROM link WHERE src=?", (row["id"],))}
    assert "https://c.nknu.edu.tw/aca/news_detail.aspx?id=1" in linked  # a page it links: the link table
    keys = {r["res_key"] for r in f.q("SELECT res_key FROM page_res WHERE page_id=?", (row["id"],))}
    assert any("form.pdf" in k for k in keys)  # a file it uses: its page_res


def test_the_page_res_it_had_stays_and_a_second_reading_adds_nothing(tmp_path):
    crawler, row = _fetched(tmp_path)
    f = crawler.frontier
    had = {r["res_key"] for r in f.q("SELECT res_key FROM page_res WHERE page_id=?", (row["id"],))}
    crawler.take_rendered(row, SHOWN, None, None)
    keys = {r["res_key"] for r in f.q("SELECT res_key FROM page_res WHERE page_id=?", (row["id"],))}
    assert had <= keys
    count = f.q("SELECT count(*) FROM url")[0][0]
    assert crawler.take_rendered(row, SHOWN, None, None) == 0
    assert f.q("SELECT count(*) FROM url")[0][0] == count


def test_js_take_reads_the_look_the_export_gives_of_each_page_that_shows_something(tmp_path, monkeypatch):
    """update js-take: each page jsfetch.classify calls links or text, its look for_export picks, read_rendered,
    take_rendered; a page that shows nothing (none) and a page with no complete look are left."""
    from nknu.archive import jsfetch, update

    crawler, row = _fetched(tmp_path)
    jsfetch.ensure_table(crawler.frontier)
    look = {"warc": "warc/x.warc.gz", "warc_offset": 7}
    monkeypatch.setattr(jsfetch, "classify", lambda fr, sites: {row["id"]: "links", 999: "none"})
    monkeypatch.setattr(jsfetch, "for_export", lambda fr, uid: (look, None) if uid == row["id"] else (None, "x"))
    read = []
    monkeypatch.setattr(jsfetch, "read_rendered", lambda root, w, o: read.append((w, o)) or (LIST, SHOWN))
    got = update.js_take(tmp_path, {"c.nknu.edu.tw/aca"}, crawler=crawler)
    assert read == [("warc/x.warc.gz", 7)]
    ((_site, (pages, added)),) = got.items()
    assert pages == 1 and added >= 3


def test_a_looks_own_scripts_are_not_taken(tmp_path):
    """11-17 on the lab: ord's pages load their scripts with a timestamp (/js/nav.js?t=261117085015): every look showed
    new script addresses, js-take queued them as files each round (39 a round, never ending). A look's scripts are the
    page's own program, which the browser asked (and jsfetch archived) when it ran: not taken; its links and files are."""
    crawler, row = _fetched(tmp_path)
    f = crawler.frontier
    shown = SHOWN.replace("</body>", "<script src=\"/js/nav.js?t=261117085015\"></script>"
                                     "<script src=\"https://c.nknu.edu.tw/aca/js/page.js?t=1\"></script></body>")
    crawler.take_rendered(row, shown, None, None)
    urls = {r["url"] for r in f.q("SELECT url FROM url")}
    assert not any("nav.js" in u or "page.js" in u for u in urls)
    assert "https://c.nknu.edu.tw/aca/files/form.pdf" in urls
