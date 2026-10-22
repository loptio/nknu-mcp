from nknu.crawl.webforms import find_postbacks, form_state, postback

HTML = """<form id="uForm" method="post" action="./SubjectList.aspx?cat=SCHOOLADMIN">
<input type="hidden" name="__EVENTTARGET" value="" /><input type="hidden" name="__EVENTARGUMENT" value="" />
<input type="hidden" name="__VIEWSTATE" value="VS123" /><input type="hidden" name="__VIEWSTATEGENERATOR" value="4836FE09" />
<input type="submit" name="btnSearch" value="查詢" /><input type="checkbox" name="cb" />
<select name="ddlCategory"><option value="ALL">全部</option><option value="SCHOOLADMIN" selected>行政</option></select>
<input type="text" name="tbKeyword" value="" />
<a href="javascript:__doPostBack('gvFull','Page$2')">2</a><a href="javascript:__doPostBack(&#39;gvFull&#39;,&#39;Page$3&#39;)">3</a>
</form>"""
URL = "https://sso.nknu.edu.tw/News/SubjectList.aspx?cat=SCHOOLADMIN"


def test_form_state_like_a_browser():
    action, fields = form_state(HTML, URL)
    assert action == "https://sso.nknu.edu.tw/News/SubjectList.aspx?cat=SCHOOLADMIN"
    names = [k for k, _ in fields]
    assert "btnSearch" not in names and "cb" not in names and ("ddlCategory", "SCHOOLADMIN") in fields


def test_postback_sets_event_fields_and_keeps_viewstate():
    _, fields = postback(HTML, URL, "gvFull", "Page$2")
    assert ("__EVENTTARGET", "gvFull") in fields and ("__EVENTARGUMENT", "Page$2") in fields and ("__VIEWSTATE", "VS123") in fields
    assert [k for k, _ in fields].count("__EVENTTARGET") == 1


def test_find_postbacks_unescapes():
    assert [(t, a) for t, a, _ in find_postbacks(HTML)] == [("gvFull", "Page$2"), ("gvFull", "Page$3")]
