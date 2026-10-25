"""Where a page sends the browser by itself (round-1 design §8.3), as links.extract() reads it: a meta refresh on every
page, and an inline script's literal redirect on a page of fewer than 150 characters, at the top level only. The
crawler's part (flags js_redirect and login, final_url, no added depth, a seed's site moving) is written with the
crawler (design §17 step 4); what the result views make of the flag is in test_outcome.py."""

from __future__ import annotations

import pytest

from nknu.archive import links

ELEC = """<script type='text/javascript'>
<!--
alert("sorry! page close!");
//-->
</script>
	<script Language='JavaScript'>location.href='index.html';</script>"""


def redirect(html: str, url: str = "https://elec.nknu.edu.tw/job.html"):
    return links.extract(html, url).js_redirect


def test_a_closed_page_that_sends_the_browser_home_is_a_redirect():
    page = links.extract(ELEC, "https://elec.nknu.edu.tw/job.html")
    r = page.js_redirect
    assert (r.href, r.how) == ("index.html", "script")
    assert ("index.html", "[js_redirect]") in page.links and page.text_chars < links.JS_REDIRECT_MAX_CHARS


def test_spaces_around_the_equals_sign_and_a_sign_in_page_target():
    html = "<script>alert('請先登入'); document.location.href = '../login.php'</script>"
    assert redirect(html, "https://clcts.nknu.edu.tw/clctm/apply_step1.php?id=x").href == "../login.php"


def test_location_replace_and_escaped_slashes_and_the_base_href():
    html = '<base href="https://clcts.nknu.edu.tw/CLCT/"><script>window.location.replace("index\\/main.php");</script>'
    page = links.extract(html, "https://clcts.nknu.edu.tw/CLCT/")
    assert page.js_redirect.href == "index/main.php" and page.base == "https://clcts.nknu.edu.tw/CLCT/"


@pytest.mark.parametrize("script", [
    'document.location.href = "?orderBy="+$(this).val();',  # artcenter: a built address is not a literal one
    "function go() { location.href = 'a.html'; }",
    "if (x) { location.href = 'a.html'; }",
    "if (x) location.href = 'a.html';",
    "setTimeout(\"location.href='a.html'\", 3000);",
    "location.href = '';",
    "location.href = '#top';",
    "location.href = 'javascript:void(0)';",
])
def test_what_is_not_a_redirect_of_the_page_itself(script):
    assert redirect(f"<script>{script}</script>") is None


def test_an_onclick_address_is_a_link_not_a_redirect():
    page = links.extract("""<span onClick="location.href='index.html'">logo</span>""", "https://ee.nknu.edu.tw/x.html")
    assert page.js_redirect is None and ("index.html", "[onclick]") in page.links


def test_a_long_page_s_script_redirect_is_not_read():
    words = "<p>" + "這是一段很長的公告內容。" * 20 + "</p>"
    page = links.extract(words + "<script>location.href='index.html';</script>", "https://x.nknu.edu.tw/a.html")
    assert page.text_chars >= links.JS_REDIRECT_MAX_CHARS
    assert page.js_redirect is None and not any(t == "[js_redirect]" for _h, t in page.links)


def test_a_meta_refresh_is_read_on_every_page_but_moves_only_a_short_one():
    refresh = '<meta http-equiv="refresh" content="0; url=https://x.nknu.edu.tw/new/">'
    short = links.extract(refresh, "https://x.nknu.edu.tw/old/")
    assert (short.js_redirect.href, short.js_redirect.how) == ("https://x.nknu.edu.tw/new/", "refresh")
    long_ = links.extract(refresh + "<p>" + "本頁內容仍然有效，請參考下列說明。" * 15 + "</p>", "https://x.nknu.edu.tw/old/")
    assert long_.js_redirect is None  # judged by its own content
    assert ("https://x.nknu.edu.tw/new/", "[refresh]") in long_.links  # its target is still queued


def test_a_page_that_refreshes_itself_goes_nowhere():
    page = links.extract('<meta http-equiv="refresh" content="300">', "https://x.nknu.edu.tw/board.aspx")
    assert page.js_redirect is None
    same = links.extract('<meta http-equiv="refresh" content="60; URL=board.aspx">', "https://x.nknu.edu.tw/board.aspx")
    assert same.js_redirect is None and same.redirects[0].to_self


def test_a_concatenated_address_in_an_attribute_is_not_cut_into_half_a_link():
    # the old _URL_CALL stopped at the first quote: "?orderBy=" became a link of its own
    page = links.extract("""<select onchange="location.href='?orderBy='+this.value"><option>a</option></select>""",
                         "https://artcenter.nknu.edu.tw/product_list.asp")
    assert not any(h.startswith("?orderBy") for h, _t in page.links)
    menu = {u.key: u for u in page.unhandled}["select:"]
    assert menu.template == "?orderBy={this.value}"
