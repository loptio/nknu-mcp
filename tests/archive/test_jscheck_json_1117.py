"""11-17 (the JS sites' handoff, item ②, research/jssites_2026-11-17/handoff_crawler.md; the plan
docs/superpowers/plans/2026-11-17-jsfetch-integration.md task 1): a page's own JSON POST to its data address (the
school home's news box) is sent as it is when the check says so (json_post: jsfetch's JsFetcher sets it); jscheck itself
never does, its verdicts unchanged. Made-up addresses."""

from __future__ import annotations

from nknu.archive import jscheck
from nknu.archive.jscheck import Ask

W3 = "https://w3.nknu.edu.tw"
JSON = {"content-type": "application/json; charset=UTF-8", "accept": "application/json"}


def test_a_json_post_is_sent_as_it_is_when_asked():
    t = jscheck._target(Ask(f"{W3}/api/news", "POST", "fetch", JSON, b'{"page":1}'), json=True)
    assert (t.method, t.body, t.data) == ("POST", b'{"page":1}', None)
    assert {k.lower(): v for k, v in t.headers.items()}["content-type"] == "application/json; charset=UTF-8"


def test_a_json_post_is_not_sent_by_default():
    assert jscheck._target(Ask(f"{W3}/api/news", "POST", "fetch", JSON, b'{"page":1}')) == "post_body"


def test_other_bodies_stay_unsent_and_forms_go_as_before():
    assert jscheck._target(Ask(f"{W3}/a", "POST", "xhr", {"content-type": "text/plain"}, b"x"), json=True) == "post_body"
    form = jscheck._target(Ask(f"{W3}/a", "POST", "xhr", {"content-type": jscheck._FORM}, b"a=1&b=2"), json=True)
    assert form.data == [("a", "1"), ("b", "2")] and form.body is None


def test_the_sender_sends_json_only_for_a_check_that_says_so():
    import inspect

    src = inspect.getsource(jscheck.Sender._send)
    assert 'json=getattr(c, "json_post", False)' in src
