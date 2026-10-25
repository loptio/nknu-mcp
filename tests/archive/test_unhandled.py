"""What links.extract() reads beyond plain links (round-1 design §7.2, §7.8, §9): frames apart from links, ASP.NET
buttons with their target, argument, text, form and how they fire, GET paging links (which add no depth), and the
writings the round does not handle, so that none is dropped unseen: javascript: links (their function), __doPostBack
in onchange and setTimeout, <select> menus, and GET forms. The crawler writes each into page_res (how='unhandled');
the report and the acceptance check list them per site."""

from __future__ import annotations

import pytest

from nknu.archive import links

URL = "https://c.nknu.edu.tw/aca/default.aspx"


def kinds(page: links.PageLinks) -> dict[str, links.Unhandled]:
    return {u.key: u for u in page.unhandled}


# ---------------------------------------------------------------- javascript: links


def test_javascript_links_keep_their_function_and_text():
    html = """<body><nav><a href="javascript:" title="高中生專區" class="ph_down_link">高中生專區</a></nav>
      <a href="javascript:" title="2026 恭賀 王O明老師 榮獲特聘教授"></a>
      <a href="javascript:papago('80201 高雄市苓雅區和平一路116號')">80201 高雄市苓雅區和平一路116號</a>
      <a href='javascript:void(0);' data-target='newsTabPanel0'>全部公告</a><a href="javascript:void(0)">更多</a>
      <a href="javascript:window.print()">列印</a><a href="#" onclick="showNews(12); return false">課程異動</a>
      <a href="#top">top</a><a href="#"><img src="x.jpg"></a></body>"""
    page = links.extract(html, URL)
    got = kinds(page)
    blank = got["js_link:"]  # the sample comes from the body when one is there: the English site's news titles
    assert (blank.n, blank.text, blank.zone) == (2, "2026 恭賀 王O明老師 榮獲特聘教授", "unknown")
    assert got["js_link:papago"].text.startswith("80201") and got["js_link:papago"].zone == "unknown"
    assert got["js_link:void"].n == 2 and got["js_link:void"].text == "全部公告"
    assert got["js_link:window.print"].text == "列印"
    assert got["js_link:showNews"].text == "課程異動"  # href="#" plus a handler that leads nowhere we can follow
    assert set(got) == {"js_link:", "js_link:papago", "js_link:void", "js_link:window.print", "js_link:showNews"}
    assert {h for h, _ in page.links} == {"#", "#top"}  # none of them leads to a URL


def test_javascript_links_that_open_a_url_are_links_not_unhandled():
    html = """<a href="javascript:window.open('files/form.pdf')">表單</a>
      <a href="javascript:void(0)" onclick="location.href='Page.aspx?PN=3'">規章</a>
      <a href="javascript:__doPostBack('MasterGrid','Page$2')">2</a>"""
    page = links.extract(html, URL)
    assert ("files/form.pdf", "表單") in page.links and ("Page.aspx?PN=3", "[onclick]") in page.links
    assert page.unhandled == [] and [(b.target, b.argument) for b in page.buttons] == [("MasterGrid", "Page$2")]


# ---------------------------------------------------------------- ASP.NET buttons


SSO = """<html><body><form method="post" action="./Default.aspx?WebIdx=31" id="form1">
<div class="lang-switcher"><a id="btnLangTC" href="javascript:__doPostBack(&#39;btnLangTC&#39;,&#39;&#39;)">繁中</a></div>
<div id="content"><a id="ucNews_rptNews_ctl03_lnkShowDetail"
   href="javascript:__doPostBack('ucNews$rptNews$ctl03$lnkShowDetail','')">115學年度轉學考簡章</a>
<input type="button" value="下載" onclick="__doPostBack('ucNews$rptFiles$ctl00$lnkDownloadFile','')">
<a href="javascript:WebForm_DoPostBackWithOptions(new WebForm_PostBackOptions(&quot;lbnNext&quot;, &quot;&quot;, true, &quot;&quot;, &quot;&quot;, false, true))">下一頁</a>
<select name="ddlYear" id="ddlYear" onchange="javascript:setTimeout('__doPostBack(\\'ddlYear\\',\\'\\')', 0)">
  <option value="115" selected>115</option><option value="114">114</option><option value="113">113</option></select>
</div></form>
<form id="search" action="https://www.google.com/search"><input name="q"><input type="hidden" name="sitesearch" value="nknu.edu.tw"></form>
</body></html>"""


def test_buttons_carry_target_argument_text_form_and_trigger():
    page = links.extract(SSO, "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31", content_css="#content")
    got = {b.target: b for b in page.buttons}
    lang = got["btnLangTC"]
    assert (lang.argument, lang.text, lang.form_id, lang.trigger, lang.zone) == ("", "繁中", "form1", "href", "chrome")
    detail = got["ucNews$rptNews$ctl03$lnkShowDetail"]
    assert (detail.text, detail.trigger, detail.zone) == ("115學年度轉學考簡章", "href", "body")
    download = got["ucNews$rptFiles$ctl00$lnkDownloadFile"]
    assert (download.text, download.trigger) == ("下載", "onclick")
    nxt = got["lbnNext"]
    assert (nxt.text, nxt.trigger, nxt.form_id) == ("下一頁", "href", "form1")  # WebForm_PostBackOptions
    year = got["ddlYear"]
    assert (year.trigger, year.form_id) == ("onchange", "form1")
    assert page.postbacks == [f"{b.target}|{b.argument}" for b in page.buttons]  # what the 10-24 crawler counts


def test_postbacks_in_onchange_and_settimeout_are_buttons_and_also_listed_unhandled():
    page = links.extract(SSO, "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31")
    got = kinds(page)
    assert got["js_postback:ddlYear"].form_id == "form1"
    sel = got["select:ddlYear"]
    assert (sel.form_id, sel.detail) == ("form1", "ddlYear options=3")
    form = got["get_form:https://www.google.com/search q,sitesearch"]
    assert form.form_id == "search"
    assert not any(k.startswith("get_form:") and "Default.aspx" in k for k in got)  # a POST form is a button's form


def test_postbacks_a_script_fires_are_buttons_and_unhandled_and_the_function_itself_is_not():
    html = """<form id="form1" method="post"><script type="text/javascript">
      function __doPostBack(eventTarget, eventArgument) { theForm.__EVENTTARGET.value = eventTarget; }
      setTimeout("__doPostBack('UpdateTimer','')", 60000);</script>
      <script src="/WebResource.axd?d=x"></script></form>"""
    page = links.extract(html, URL)
    assert [(b.target, b.trigger, b.text, b.form_id) for b in page.buttons] == [("UpdateTimer", "script", "", "form1")]
    assert list(kinds(page)) == ["js_postback:UpdateTimer"]


def test_each_link_keeps_its_best_zone():
    html = """<body><nav><a href="/aca/rule.pdf">規章</a><a href="/aca/menu.aspx">選單</a></nav>
      <div><a href="/aca/rule.pdf">附件：規章</a></div><footer><a href="/aca/privacy.pdf">隱私權</a></footer></body>"""
    page = links.extract(html, URL)
    assert page.link_zone == {"/aca/rule.pdf": "unknown", "/aca/menu.aspx": "chrome", "/aca/privacy.pdf": "chrome"}
    assert [h for h, _ in page.links] == ["/aca/rule.pdf", "/aca/menu.aspx", "/aca/rule.pdf", "/aca/privacy.pdf"]


def test_get_forms_and_selects_without_a_form():
    html = """<form action="/aca/news_search.aspx" method="GET" name="s"><input name="kw"><select name="cat">
      <option value="https://c.nknu.edu.tw/aca/a">A</option><option value="/aca/b">B</option><option>C</option>
      </select><button type="submit">搜尋</button></form>
      <select name="jump" onchange="location.href=this.value"><option value="http://x.nknu.edu.tw/">X</option></select>
      <form><input name="q"></form><form method="post"><input type="password" name="pw"></form>"""
    got = kinds(links.extract(html, URL))
    assert got["get_form:/aca/news_search.aspx kw,cat"].form_id == "s"
    assert got["select:cat"].detail == "cat options=3 url_options=2"  # a jump menu: its options are URLs
    assert (got["select:jump"].form_id, got["select:jump"].detail) == ("", "jump options=1 url_options=1")
    assert "get_form: q" in got  # no action: the form posts to its own page, by GET
    assert len([k for k in got if k.startswith("get_form:")]) == 2


# ---------------------------------------------------------------- frames, embeds


def test_frames_are_listed_apart_and_the_link_table_still_sees_them():
    frameset = links.extract('<frameset><frame src="menu.htm" name="left"><frame src="main.htm"></frameset>',
                             "https://liling.nknu.edu.tw/")
    assert [(f.src, f.tag, f.title) for f in frameset.frames] == [("menu.htm", "frame", "left"),
                                                                 ("main.htm", "frame", "")]
    html = """<body><iframe src="https://www.canva.com/design/x/view?embed" title="海報"></iframe>
      <iframe data-src="lazy.html"></iframe><embed src="uploadfile/rule.pdf" type="application/pdf">
      <object data="uploadfile/plan.pdf"></object><embed src="anim.swf"></body>"""
    page = links.extract(html, "https://liling.nknu.edu.tw/")
    assert [(f.src, f.tag) for f in page.frames] == [("https://www.canva.com/design/x/view?embed", "iframe"),
                                                    ("lazy.html", "iframe"), ("uploadfile/rule.pdf", "embed"),
                                                    ("uploadfile/plan.pdf", "object"), ("anim.swf", "embed")]
    assert page.frames[0].title == "海報" and {f.zone for f in page.frames} == {"unknown"}
    for doc in (frameset, page):
        assert {h for h, t in doc.links if t in ("[frame]", "[embed]")} == {f.src for f in doc.frames}


def test_lazy_frames_keep_their_real_address():
    # lazy loaders leave src blank (about:blank, javascript:) and keep the frame's address in data-src, or WP Rocket's
    # data-lazy-src: that address is the frame, in frames and in links
    html = """<iframe src="about:blank" data-src="https://c.nknu.edu.tw/aca/frame.aspx"></iframe>
      <iframe src="about:blank" data-rocket-lazyload="fitvidscompatible" data-lazy-src="https://www.canva.com/d/x/view"></iframe>
      <iframe src="javascript:false" data-original="lazy2.html"></iframe><iframe src=" " data-src="lazy3.html"></iframe>
      <iframe src="real.html" data-src="other.html"></iframe><iframe src="about:blank"></iframe>"""
    page = links.extract(html, "https://liling.nknu.edu.tw/")
    srcs = [f.src for f in page.frames]
    assert srcs == ["https://c.nknu.edu.tw/aca/frame.aspx", "https://www.canva.com/d/x/view", "lazy2.html", "lazy3.html",
                    "real.html", "about:blank"]  # a frame left blank is listed as written: canonical() drops it
    assert {h for h, _ in page.links} == set(srcs)


def test_frames_the_page_hides_are_marked_hidden():
    # a tracker's frame (Google Tag Manager's noscript iframe and the like): round1 records it as a link only
    html = """<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-ABC" height="0" width="0"
      style="display:none;visibility:hidden"></iframe></noscript>
      <iframe src="https://track.example.com/px" width="0" height="0"></iframe>
      <iframe src="https://ads.example.com/x" style="border:0; display: none"></iframe><iframe src="h.html" hidden></iframe>
      <iframe src="v.html" style="visibility:hidden"></iframe><iframe src="w0.html" style="width:0px"></iframe>
      <iframe src="auto.aspx" height="0" onload="resizeFrame(this)"></iframe>
      <iframe src="https://www.canva.com/design/x/view?embed" width="100%" height="600"></iframe>
      <iframe src="main.htm" width="760" height="1200" frameborder="0"></iframe>"""
    got = {f.src: f for f in links.extract(html, "https://liling.nknu.edu.tw/").frames}
    hidden = {src for src, f in got.items() if f.hidden}
    assert hidden == {"https://www.googletagmanager.com/ns.html?id=GTM-ABC", "https://track.example.com/px",
                      "https://ads.example.com/x", "h.html", "v.html", "w0.html"}
    assert not got["auto.aspx"].hidden  # a frame a script sizes when it loads shows its page then
    assert (got["main.htm"].width, got["main.htm"].height) == (760, 1200)
    assert (got["https://www.canva.com/design/x/view?embed"].width, got["https://track.example.com/px"].width) == (None, 0)


# ---------------------------------------------------------------- GET paging links (design §9)


@pytest.mark.parametrize("page_url, href, text, pager", [
    ("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=43&selpage=MoreNews&pagenum=1",
     "default.aspx?webidx=43&selpage=MoreNews&pagenum=2", "2", True),
    ("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=43&selpage=MoreNews",
     "default.aspx?webidx=43&selpage=MoreNews&pagenum=2", "下一頁", True),  # page 1 has no page parameter
    ("https://lis.nknu.edu.tw/index.php/news", "/index.php/news?start=10", "下頁", True),
    ("https://lis.nknu.edu.tw/index.php/news", "/index.php/news?start=10", " [ 2 ] ", True),
    ("https://vd.nknu.edu.tw/news/", "/news/page/2/", "2", True),  # WordPress
    ("https://vd.nknu.edu.tw/news/page/2/", "/news/page/3/", "Next »", True),
    ("https://vd.nknu.edu.tw/news/page/3/", "/news/", "1", True),
    ("https://fine-arts.nknu.edu.tw/?cat=4", "/?cat=4&paged=2", "›", True),
    ("https://x.nknu.edu.tw/list.php?Page=1", "list.php?Page=2", "後頁", True),
    ("https://c.nknu.edu.tw/aca/page.aspx?pn=8", "page.aspx?pn=9", "9", False),  # pn is a page's number, not paging
    ("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=43&pagenum=1", "default.aspx?webidx=44&pagenum=2", "2",
     False),  # two parameters differ
    ("https://lis.nknu.edu.tw/index.php/news", "/index.php/events?start=10", "2", False),  # another path
    ("https://lis.nknu.edu.tw/index.php/news", "/index.php/news?start=10", "活動花絮", False),  # not a page's text
    ("https://lis.nknu.edu.tw/index.php/news", "https://lis2.nknu.edu.tw/index.php/news?start=10", "2", False),
    ("https://lis.nknu.edu.tw/index.php/news?start=10", "/index.php/news?start=10", "2", False),  # itself
])
def test_get_paging_links(page_url, href, text, pager):
    page = links.extract(f'<a href="{href}">{text}</a>', page_url)
    assert bool(page.pagers) == pager
    if pager:
        assert page.pagers == [links.scope.canonical(href, page_url)]


# ---------------------------------------------------------------- clicks that load content (design §7.8)
# The pages below are cut down from the old archive's own pages (read only, 10-25): oia's tabs, oia's regulation tabs
# with plain links, ee's download buttons and its year menu, the English site's news tabs, SSO's search box.

OIA = """<html><body><ul class="third_nav"><li><a href="javascript:void(0)" onclick="loadSubcategoryContent(20)"
 class="active">美洲</a></li><li><a href="javascript:void(0)" onclick="loadSubcategoryContent(21)" class="">亞洲</a></li>
<li><a href="javascript:void(0)" onclick="loadSubcategoryContent(46)" class="">歐洲</a></li></ul>
<div id="dynamic-content"><p>第一區的內容</p></div>
<script src="js/global.js"></script><script src="https://cdnjs.cloudflare.com/ajax/libs/x/1/x.min.js"></script>
<script>
function loadSubcategoryContent(subcategoryId) {
  const contentDiv = document.getElementById('dynamic-content');
  contentDiv.innerHTML = '<div class="text-center py-4"><i class="fas fa-spinner fa-spin"></i> 載入中...</div>';
  document.querySelectorAll('.third_nav a').forEach(link => { link.classList.remove('active'); });
  event.target.classList.add('active');
  fetch('load_content.php?sub_id=' + subcategoryId)
    .then(response => response.json())
    .then(data => { if (data.success) { contentDiv.innerHTML = data.content; } });
}
</script></body></html>"""
OIA_URL = "https://oia.nknu.edu.tw/sister-schools.php"


def clicks(page: links.PageLinks) -> dict[str, links.Click]:
    return {c.key: c for c in page.clicks}


def test_a_tab_whose_handler_fetches_its_content_is_a_click_not_an_unhandled_link():
    page = links.extract(OIA, OIA_URL)
    got = clicks(page)
    assert set(got) == {"click:loadSubcategoryContent(20)", "click:loadSubcategoryContent(21)",
                        "click:loadSubcategoryContent(46)"}
    tab = got["click:loadSubcategoryContent(21)"]
    assert (tab.fn, tab.args, tab.params, tab.text, tab.trigger, tab.group) == (
        "loadSubcategoryContent", "21", ("subcategoryId",), "亞洲", "onclick", "loadSubcategoryContent")
    (call,) = tab.calls
    assert (call.call, call.target, call.method) == ("fetch", "load_content.php?sub_id={subcategoryId}", "GET")
    assert not any(k.startswith("js_link:") for k in kinds(page))  # the javascript:void(0) is not a gap of its own
    # the tab shown on load is told apart; the others need a click
    assert got["click:loadSubcategoryContent(20)"].shown_on_load and not tab.shown_on_load


def test_what_a_click_needs_follows_the_platform_rule_then_what_the_page_shows():
    got = clicks(links.extract(OIA, OIA_URL))
    shown, hidden = got["click:loadSubcategoryContent(20)"], got["click:loadSubcategoryContent(21)"]
    assert links.click_need(hidden) == ("round2", "round2:click")  # no rule: the next round clicks it
    assert links.click_need(shown) == ("no", "click:shown_on_load")
    for tab in (shown, hidden):  # a rule presses every tab, the one shown on load too
        assert links.click_need(tab, "handle") == ("must", "")
        assert links.click_need(tab, "no_content") == ("no", "click:no_content")
        assert links.click_need(tab, "round2") == ("round2", "round2:click")  # the rule no longer fits
        with pytest.raises(ValueError):
            links.click_need(tab, "ignore")  # a click that loads something is never ignored


def test_a_platform_file_with_no_rule_for_a_click_is_no_rule_and_never_presses_one_it_bars():
    # profiles.decision() says 'unknown' when the file says nothing (design §15): the same as no rule at all, so the
    # tab shown on load and the tab with a plain link need no click (review 10-26: they were left for round two)
    got = clicks(links.extract(OIA, OIA_URL))
    shown, hidden = got["click:loadSubcategoryContent(20)"], got["click:loadSubcategoryContent(21)"]
    assert links.click_need(shown, "unknown") == links.click_need(shown) == ("no", "click:shown_on_load")
    assert links.click_need(hidden, "unknown") == ("round2", "round2:click")
    plain = links.extract(REGULATIONS, "https://oia.nknu.edu.tw/regulations-international.php").clicks[0]
    assert links.click_need(plain, "unknown") == ("no", "click:plain_href")
    # never (登录、验证码、送出表单、搜索: design §7.4, §7.8): never pressed, by this round or the next one's browser
    for tab in (shown, hidden, plain):
        assert links.click_need(tab, "never") == ("no", "click:never")
    assert links.click_need(hidden, "something_new") == ("round2", "round2:click")  # a decision nobody knows: no rule


def test_a_group_of_tabs_nobody_marks_or_all_mark_is_all_for_the_next_round():
    none = OIA.replace('class="active"', 'class=""')
    assert not any(c.shown_on_load for c in links.extract(none, OIA_URL).clicks)
    every = OIA.replace('class=""', 'class="on"')
    assert not any(c.shown_on_load for c in links.extract(every, OIA_URL).clicks)
    li = OIA.replace('<li><a href="javascript:void(0)" onclick="loadSubcategoryContent(21)" class="">',
                     '<li class="current"><a href="javascript:void(0)" onclick="loadSubcategoryContent(21)">')
    li = li.replace('class="active"', "")
    got = clicks(links.extract(li, OIA_URL))
    assert got["click:loadSubcategoryContent(21)"].shown_on_load  # the nearest <li> marks it
    aria = OIA.replace('class="active"', 'aria-selected="true"')
    assert clicks(links.extract(aria, OIA_URL))["click:loadSubcategoryContent(20)"].shown_on_load


def test_non_link_elements_with_a_click_handler_are_read_too():
    html = OIA.replace('<a href="javascript:void(0)" onclick="loadSubcategoryContent(46)" class="">歐洲</a>',
                       '<span onclick="loadSubcategoryContent(46)">歐洲</span>')
    html += """<div class="more" onclick="openPanel('news', 3)">更多</div><button onclick="showYear(114)">114</button>
      <li onclick="toggleMenu()">選單</li>"""
    page = links.extract(html, OIA_URL)
    assert "click:loadSubcategoryContent(46)" in clicks(page)
    got = kinds(page)
    panel = got["js_link:openPanel"]  # a function no inline script defines: an external .js
    assert (panel.args, panel.cls, panel.text) == ("'news', 3", "more", "更多")
    assert {"js_link:showYear", "js_link:toggleMenu"} <= set(got)


REGULATIONS = """<html><body><ul class="third_nav mb-5"><li><a href="?category=1" class="regulations-nav-link active"
 data-category-id="1">國際鏈結相關</a></li><li><a href="?category=2" class="regulations-nav-link"
 data-category-id="2">學術交流相關</a></li></ul><div id="regulations-content"></div>
<script>
$(document).ready(function() {
  $('.third_nav a.regulations-nav-link').on('click', function(e) {
    e.preventDefault();
    const categoryId = $(this).data('category-id');
    $('.third_nav a.regulations-nav-link').removeClass('active');
    $(this).addClass('active');
    loadRegulationsContent(categoryId);
  });
  function loadRegulationsContent(categoryId) {
    $('#regulations-content').html('<div>載入中...</div>');
    $.ajax({ url: window.location.pathname, method: 'GET', data: { ajax: 1, category: categoryId }, dataType: 'json',
      success: function(response) { $('#regulations-content').html(response.content_html); } });
  }
});
</script></body></html>"""


def test_tabs_with_plain_links_are_read_but_need_no_click():
    url = "https://oia.nknu.edu.tw/regulations-international.php"
    page = links.extract(REGULATIONS, url)
    got = page.clicks
    assert len(got) == 2 and {c.trigger for c in got} == {"bind:click"}
    assert {c.plain_href for c in got} == {f"{url}?category=1", f"{url}?category=2"}
    assert all(links.click_need(c) == ("no", "click:plain_href") for c in got)
    (call,) = got[0].calls
    assert (call.call, call.method, call.via) == ("$.ajax", "GET", ("loadRegulationsContent",))
    assert ("?category=2", "學術交流相關") in page.links  # the plain link is queued as any link


EE = """<html><head><SCRIPT>
  <!--img-->
  $(function(){
    var html = "student4";
    $('.img').mouseover(function(e) { $(this).hover().stop().fadeTo(200,0.7); });
    $(".option").change(function()
    {
      location.href = html + ".html?getId=" + $(this).val();
    });
    var getId = "";
    if (getId == "")
    {
      $(".category").change();
    }
  })
</SCRIPT><script>
  $(function()
  {
    $(".down").each(function()
    {
      $(this).click(function()
      {
        $.ajax({ type:"POST", url: "fileAjax.html",
          data: { id : $(this).find('.download').val(), k : 2 },
          success: function(data) { window.open(data,"_blank"); } });
      });
    });
  });
  $("#lab_line").click(function(){ $("#lab_st").slideToggle(); });
</script></head><body>
<p>類別：<select name="" class="category option"><option value="4" >106學年度</option><option value="5" >109學年度</option>
<option value="6" >108學年度</option><option value="7" >110學年度</option><option value="8" >111學年度</option></select></p>
<div id="ne_list"><a href="javascript:" class="down"><input type="hidden" value="67" class="download"><p>1</p>
<p>課程系統表-113~114學年度入學新生</p></a><a href="javascript:" class="down"><input type="hidden" value="70"
class="download"><p>2</p><p>課程系統表-114~115學年度入學新生</p></a></div><span id="lab_line">實驗室學生</span>
</body></html>"""
EE_URL = "https://ee.nknu.edu.tw/student4.html"


def test_buttons_bound_by_an_inline_script_are_clicks_with_their_values():
    page = links.extract(EE, EE_URL)
    got = [c for c in page.clicks if c.group == ".down"]
    assert [c.key for c in got] == ["click:.down[0]", "click:.down[1]"]
    assert [c.values for c in got] == [("67",), ("70",)]
    (call,) = got[0].calls
    assert (call.call, call.target, call.method, call.data) == ("$.ajax", "fileAjax.html", "POST",
                                                               ("id={$(this).find('.download').val()}", "k=2"))
    assert all(links.click_need(c) == ("round2", "round2:click") for c in got)
    assert not any(k.startswith("js_link:") for k in kinds(page))  # the bound javascript: links are those clicks
    assert not any(c.group == "#lab_line" for c in page.clicks)  # slideToggle loads nothing


def test_a_jump_menu_keeps_how_it_builds_its_address_its_options_and_that_the_page_fires_it():
    got = kinds(links.extract(EE, EE_URL))
    menu = got["select:.option"]
    assert menu.template == "{html}.html?getId={$(this).val()}"
    assert menu.options == (("4", "106學年度"), ("5", "109學年度"), ("6", "108學年度"), ("7", "110學年度"),
                            ("8", "111學年度"))
    assert menu.auto  # if (getId == "") $(".category").change(); and .category names this very menu
    # the same template on a page without the menu (ee's course.html, news.html) records no menu at all
    bare = EE.replace(EE[EE.index("<p>類別"):EE.index("</select></p>") + len("</select></p>")], "")
    assert not any(k.startswith("select:") for k in kinds(links.extract(bare, "https://ee.nknu.edu.tw/news.html")))


def test_a_menu_the_page_never_fires_is_no_auto_menu():
    calm = EE.replace('$(".category").change();', "")
    menu = kinds(links.extract(calm, EE_URL))["select:.option"]
    assert menu.template and not menu.auto
    elsewhere = EE.replace('$(".category").change();', '$(".other").change();')
    assert not kinds(links.extract(elsewhere, EE_URL))["select:.option"].auto


def test_a_checkbox_that_rebuilds_the_address_is_listed_as_an_input():
    html = """<input type="checkbox" class="btn-check category-checkbox check-all" id="classification_all" value=""
      checked><label for="classification_all">全部</label>
      <script>(function() {
        const checkAll = document.getElementById('classification_all');
        checkAll.addEventListener('change', function() {
          const url = new URL(window.location.href);
          window.location.href = url.pathname + (url.searchParams.toString() ? '?' + url.searchParams.toString() : '');
        });
      })();</script>"""
    got = kinds(links.extract(html, "https://clcts.nknu.edu.tw/news.php"))
    box = got["input:#classification_all"]
    assert box.template.startswith("{url.pathname}")


ENGLISH = """<html><body><div class="news_tab"><a href="javascript:" data-id="1">最新消息</a><a href="javascript:"
data-id="3">活動訊息</a></div><div class="news_list"></div><script src="../js/jquery-3.7.1.min.js"></script>
<script src="../js/top.js"></script></body></html>"""


def test_tabs_whose_handler_is_in_an_external_script_are_unhandled():
    page = links.extract(ENGLISH, "https://english.nknu.edu.tw/tw/index.html")
    got = kinds(page)
    assert got["js_link:"].n == 2 and page.clicks == []
    assert page.scripts == ["../js/jquery-3.7.1.min.js", "../js/top.js"]


@pytest.mark.parametrize("decision, want", [
    (None, ("must", "unhandled:unknown")), ("unknown", ("must", "unhandled:unknown")),
    ("ignore", ("no", "unhandled:ignore")), ("never", ("no", "unhandled:never")),
    ("no_content", ("no", "unhandled:no_content")), ("round2", ("round2", "round2:unhandled")),
    ("something_new", ("must", "unhandled:unknown")),  # a decision this version does not know decides nothing
])
def test_what_an_unhandled_writing_needs_follows_the_platform_files_decision(decision, want):
    assert links.unhandled_need(decision) == want


def test_a_writing_turned_into_a_rule_is_no_longer_unhandled():
    with pytest.raises(ValueError):
        links.unhandled_need("handle")


def test_object_methods_and_handlers_that_hand_over_to_external_functions_are_unhandled():
    html = """<th><a href="#" onclick="Joomla.tableOrdering('a.title','asc','');return false;">標題</a></th>
      <form id="news"><input name="newskw"><a href="javascript:void(0)" onclick="clearNewskwSearchInput()">x</a></form>
      <a href="https://www.facebook.com/sharer.php" onclick="popUp=window.open('https://www.facebook.com/sharer.php',
        'popupwindow', 'scrollbars=yes,width=800,height=400');popUp.focus();return false">分享</a>
      <script>function clearNewskwSearchInput() { document.getElementById('newskw').value = ''; toggleClearBtn(); }
      function localOnly() { document.title = 'x'; }</script><a href="#" onclick="localOnly()">本頁</a>"""
    page = links.extract(html, "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31")
    got = kinds(page)
    assert "js_link:Joomla.tableOrdering" in got  # obj.method() no inline script defines
    assert got["js_link:toggleClearBtn"].detail == "via clearNewskwSearchInput"  # handed over to an external .js
    assert "js_link:clearNewskwSearchInput" not in got and "js_link:localOnly" not in got  # read: loads nothing
    assert not any("focus" in k for k in got)  # a browser's own call is never the page's function
    assert ("https://www.facebook.com/sharer.php", "[onclick]") in page.links  # window.open's literal address


@pytest.mark.parametrize("html, key, detail", [
    # the page's own function hands over to an object an external .js makes (review 10-26: nothing was recorded)
    ("""<ul><li onclick="showTab(2)">Tab B</li><li onclick="showTab(3)">Tab C</li></ul><div id="c"></div>
     <script>function showTab(n){ ExtTabs.show(n); }</script>""", "js_link:ExtTabs.show", "via showTab"),
    ("""<a href="javascript:void(0)" onclick="pick(5)">Cat 5</a>
     <script>function pick(c){ app.news.open(c); }</script>""", "js_link:app.news.open", "via pick"),
    ("""<ul><li class="t">B</li><li class="t">C</li></ul>
     <script>$('.t').click(function(){ ExtTabs.show($(this).index()); });</script>""", "js_link:ExtTabs.show",
     "via bind .t"),
    # an object the page makes with new from a constructor no inline script defines is the external .js's too
    ("""<ul><li class="t">B</li></ul><script>var tabs = new ExtTabs('#c');
     $('.t').on('click', function(e){ e.preventDefault(); tabs.show($(this).index()); });</script>""",
     "js_link:tabs.show", "via bind .t"),
    # a namespace the page only makes sure exists (var App = window.App || {}) is filled by an external .js
    ("""<ul><li class="t">B</li></ul><script>var App = window.App || {};
     $('.t').click(function(){ App.news.open($(this).data('cat')); });</script>""", "js_link:App.news.open",
     "via bind .t"),
])
def test_a_handler_that_hands_over_to_an_external_objects_method_is_unhandled(html, key, detail):
    page = links.extract(html, "https://x.nknu.edu.tw/page.html")
    got = kinds(page)
    assert key in got and got[key].detail == detail, sorted(got)
    assert page.clicks == []


def test_a_handlers_own_calls_on_the_page_the_browser_and_jquery_are_no_external_function():
    html = """<ul><li class="t">B</li></ul><a href="#" onclick="mark(this); return false">m</a>
      <script>
      var box = document.getElementById('box'), list = $('#list');
      var started = new Date();
      var app = { open: function(c) { box.textContent = c; } };
      function mark(el) {
        el.classList.add('on'); console.log(el); document.getElementById('a').focus(); box.appendChild(el);
        var parts = el.id.split('-'); parts.push('x'); Math.max(1, 2); JSON.parse('{}'); started.getTime();
        localStorage.setItem('k', parts.join('')); window.location.reload(); history.pushState({}, '', '#x');
        [1, 2].forEach(function(item) { item.toFixed(1); }); app.open(1); this.blur();
        try { list.hide(); } catch (err) { err.toString(); }
      }
      $('.t').click(function(e) { e.stopPropagation(); $(this).addClass('on'); self.focus(); mark(this); });
      </script>"""
    page = links.extract(html, "https://x.nknu.edu.tw/page.html")
    assert not any(k.startswith("js_link:") for k in kinds(page)), sorted(kinds(page))


def test_vue_and_alpine_click_attributes_delegation_and_hover_are_read():
    html = """<div id="app"><button @click="loadTab(2)">二</button><span x-on:click="loadTab(3)">三</span></div>
      <ul class="menu"><li class="item" data-k="9">九</li></ul>
      <script>
      function loadTab(n) { axios.get('/api/tab?n=' + n).then(r => {}); }
      document.addEventListener('click', function(e) { if (e.target.matches('.item')) { window.fetch('/x?k=' + 1); } });
      $('.menu li').hover(function() { $('#p').load('panel.html'); });
      $('#pic').click(function() { $('#pic').attr('src', 'b.jpg'); });
      </script>"""
    page = links.extract(html, "https://x.nknu.edu.tw/")
    got = clicks(page)
    assert {"click:loadTab(2)", "click:loadTab(3)"} <= set(got)
    assert got["click:loadTab(2)"].trigger == "@click" and got["click:loadTab(2)"].calls[0].call == "axios"
    groups = {c.group for c in page.clicks}
    assert ".item" in groups  # the document's handler names its target
    assert ".menu li" in groups  # hover runs a load as well
    assert "#pic" not in groups  # a picture swapped is no content


def test_an_inline_script_that_binds_a_network_call_to_nothing_on_the_page_is_unhandled():
    html = """<script>$('.load-more').click(function() { $.get('more.php', function(d) {}); });</script><p>x</p>"""
    got = kinds(links.extract(html, "https://x.nknu.edu.tw/"))
    assert "js_bind:click .load-more" in got


# ---------------------------------------------------------------- addresses written in attributes (design §7.8)


def test_addresses_written_only_in_data_attributes_are_links():
    html = """<div class="camera_wrap"><div data-link="https://lis.nknu.edu.tw/index.php/news_display/2371"
      data-src="images/slide1.jpg"></div><li data-href="/aca/rule.aspx">規章</li><span data-url="./x.php?id=3">x</span>
      <a href="/a.pdf" data-cke-saved-href="/a.pdf">附件</a><img data-url="/not-a-link.jpg" src="i.jpg">
      <div data-link="not a url"></div>
      <div data-code="&lt;p&gt;x&lt;/p&gt;" data-url="https://abc-atari-embeds.googleusercontent.com/embeds/1/x.html"></div>
      <div data-url="https://1234-atari-embeds.googleusercontent.com/embeds/16cb/y.html"></div></div>"""
    page = links.extract(html, "https://lis.nknu.edu.tw/")
    written = [(h, t) for h, t in page.links]
    assert ("https://lis.nknu.edu.tw/index.php/news_display/2371", "[data-link]") in written
    assert ("/aca/rule.aspx", "[data-href]") in written and ("./x.php?id=3", "[data-url]") in written
    assert [h for h, _t in written].count("/a.pdf") == 1  # an editor's copy of the href is not a second link
    assert not any("atari-embeds" in h or h in ("/not-a-link.jpg", "not a url") for h, _t in written)


SITES_EMBED = """<html><body><div class="tyJCtd"><p>中心簡介</p></div>
<div class="w536ob" data-code="&lt;div&gt;&lt;h3&gt;課程資訊與報名&lt;/h3&gt;&lt;p&gt;本學期開設十門課程，請見下列連結與附件說明。&lt;/p&gt;
&lt;a href=&quot;https://sites.google.com/mail.nknu.edu.tw/aiml/course&quot;&gt;課程&lt;/a&gt;
&lt;a href=&quot;https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=sharing&quot;&gt;簡章&lt;/a&gt;
&lt;img src=&quot;https://lh3.googleusercontent.com/poster=w1280&quot;&gt;
&lt;iframe src=&quot;https://docs.google.com/forms/d/e/1FAIpQLSfX/viewform?embedded=true&quot;&gt;&lt;/iframe&gt;&lt;/div&gt;"
 data-url="https://1234-atari-embeds.googleusercontent.com/embeds/16cb/x.html"></div></body></html>"""


def test_a_google_sites_embed_code_block_is_read_as_part_of_the_page():
    page = links.extract(SITES_EMBED, "https://sites.google.com/mail.nknu.edu.tw/aiml/home")
    hrefs = {h for h, _t in page.links}
    assert {"https://sites.google.com/mail.nknu.edu.tw/aiml/course",
            "https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=sharing"} <= hrefs
    assert "https://lh3.googleusercontent.com/poster=w1280" in {i.src for i in page.images}
    assert [f.src for f in page.frames] == ["https://docs.google.com/forms/d/e/1FAIpQLSfX/viewform?embedded=true"]
    assert not any("atari-embeds" in h for h in hrefs)  # the loader is never a link
    plain = links.extract(SITES_EMBED.replace(SITES_EMBED[SITES_EMBED.index('data-code="'):
                                                          SITES_EMBED.index('data-url=')], ""),
                          "https://sites.google.com/mail.nknu.edu.tw/aiml/home")
    assert page.text_chars > plain.text_chars + 20  # its words count as the page's


def test_a_school_sites_scripts_are_listed_and_inline_ones_are_not():
    page = links.extract(OIA, OIA_URL)
    assert page.scripts == ["js/global.js", "https://cdnjs.cloudflare.com/ajax/libs/x/1/x.min.js"]


@pytest.mark.parametrize("script, group", [
    ("$('.tabs').on({click: function() { $.getJSON('tab.php', {}); }, mouseenter: function() {}});", ".tabs"),
    ("$('a.tabs[data-tab]').on('shown.bs.tab', function(e) { $('#p').load('panel.php'); });", "a.tabs[data-tab]"),
    (("var items = document.querySelectorAll('.tabs');\n"
      "for (var i = 0; i < items.length; i++) { items[i].addEventListener('click', function() { fetch('t.php'); }); }"),
     ".tabs"),
    ("$('.tabs').click(function() { document.getElementById('topicFrame').src = 'page.php?id=' + 1; });", ".tabs"),
    ("$('.tabs').click(function() { frame.setAttribute('src', 'page.php?id=2'); });", ".tabs"),
    (("$('.tabs').click(function() { show(1); });\nfunction show(n) { step(n); }\nfunction step(n) { more(n); }\n"
      "function more(n) { $.post('more.php', {n: n}); }"), ".tabs"),  # three of the page's own functions down
])
def test_every_way_a_script_binds_a_click_that_loads_is_read(script, group):
    html = f'<a class="tabs" data-tab="1">一</a><a class="tabs" data-tab="2">二</a><script>{script}</script>'
    page = links.extract(html, "https://x.nknu.edu.tw/")
    got = [c for c in page.clicks if c.group == group]
    assert len(got) == 2 and got[0].values == ("data-tab=1",), [c.key for c in page.clicks]


@pytest.mark.parametrize("script", [
    "$('.tabs').click(function() { $('#mainImg').attr('src', 'b.jpg'); });",  # a picture swapped
    "$('.tabs').click(function() { img.src = 'big/' + n + '.png'; });",
    "$('.tabs').click(function() { $(this).toggleClass('open'); });",  # the page's own toggle
])
def test_bound_handlers_that_load_nothing_are_no_clicks(script):
    page = links.extract(f'<a class="tabs">一</a><script>{script}</script>', "https://x.nknu.edu.tw/")
    assert page.clicks == [] and page.unhandled == []


DEEP = ("function show(n) { a(n); }\nfunction a(n) { b(n); }\nfunction b(n) { c(n); }\n"
        "function c(n) { $.post('deep.php'); }")


def test_a_network_call_deeper_than_three_functions_is_listed_not_dropped():
    # design §7.8 reads three of the page's own functions down; one further is not read as a click, nor dropped
    bound = links.extract(f'<a class="tabs">一</a><script>$(".tabs").click(function() {{ show(1); }});\n{DEEP}</script>',
                          "https://x.nknu.edu.tw/")
    assert bound.clicks == [] and kinds(bound)["js_deep:.tabs"].detail.startswith("network call")
    attr = links.extract(f'<span onclick="show(1)">一</span><script>{DEEP}</script>', "https://x.nknu.edu.tw/")
    assert attr.clicks == [] and "js_deep:show" in kinds(attr)


def test_an_ajax_callback_key_is_not_a_page_function():
    html = """<a href="#" onclick="success(3)">x</a>
      <script>$.ajax({ url: 'a.php', success: function(d) { fetch('elsewhere.php'); } });</script>"""
    page = links.extract(html, "https://x.nknu.edu.tw/")
    assert page.clicks == [] and "js_link:success" in kinds(page)


def test_a_click_written_wholly_in_the_attribute_is_read():
    html = """<button onclick="$('#box').load('part.php?id=7')">更多</button>"""
    (click,) = links.extract(html, "https://x.nknu.edu.tw/").clicks
    assert (click.trigger, click.calls[0].call, click.calls[0].target) == ("onclick", ".load", "part.php?id=7")


def test_a_script_that_cannot_be_read_is_listed_never_a_crash(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("odd script")

    monkeypatch.setattr(links.jsread, "bindings", broken)
    page = links.extract("<a class='x'>x</a><script>$('.x').click(function(){});</script>", "https://x.nknu.edu.tw/")
    assert "js_unread:bound" in kinds(page)
    for odd in ("$(", "document.querySelector", "var x = $", "function (", "$('.a').on({", "location.href = '",
                "items[", "x.addEventListener('click'", "`${", "/* never closed", "'never closed"):
        links.extract(f"<a onclick=\"{odd}\">x</a><script>{odd}</script>", "https://x.nknu.edu.tw/")  # no exception
