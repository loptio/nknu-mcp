"""Body images (round-1 design §7.2, scope record 三·7): every way a page writes an image is found (src, the lazy
loaders' data-src / data-original / data-lazy-src / data-srcset, the largest srcset entry, <picture><source>,
background images in style, data-bg and the old background attribute, an <a> straight to an image file), each with
its zone (body, chrome, unknown) and alt text and size; and each image the round leaves out has its reason: a photo
album page (but c.nknu/aca's Album.aspx is a news list), data: images, sizes under 80, a decoration file name outside
the body, layout areas on sites without a profile. Paths with /images/ are not left out by themselves (uploadfile,
userfiles, Joomla's /images/<year>/), the repeat rule needs 5 pages and 30% and never the body, and EditPlus's
#HonorRollArea (honour posters) is body."""

from __future__ import annotations

from nknu.archive import links

URL = "https://c.nknu.edu.tw/aca/newsview.aspx?nid=10"


def imgs(html: str, url: str = URL, content_css: str | None = None) -> dict[str, links.Image]:
    return {i.src: i for i in links.extract(html, url, content_css=content_css).images}


def need(html: str, src: str, url: str = URL, content_css: str | None = None) -> tuple[str, str]:
    page = links.extract(html, url, content_css=content_css)
    img = next(i for i in page.images if i.src == src)
    return links.image_need(img, url, profiled=page.profiled)


# ---------------------------------------------------------------- every way an image is written


def test_plain_and_lazy_images_and_the_largest_srcset_entry():
    html = """<body><p>
      <img src="uploadfile/a.jpg" alt="報名流程" width="600" height="400">
      <img src="data:image/gif;base64,R0lGODlhAQABAIAAAP" data-src="uploadfile/b.jpg">
      <img data-original="uploadfile/c.png"><img data-lazy-src="uploadfile/d.webp">
      <img srcset="s.jpg 300w, l.jpg 1200w, m.jpg 600w">
      <img src="x1.jpg" srcset="x1.jpg 1x, x2.jpg 2x">
      <img data-srcset="ds-small.jpg 1x, ds-big.jpg 3x" class="lazyload">
      <picture><source srcset="p.webp 480w, p-big.webp 960w" type="image/webp"><img src="p.jpg"></picture>
    </p></body>"""
    got = imgs(html)
    assert got["uploadfile/a.jpg"].alt == "報名流程" and (got["uploadfile/a.jpg"].width, got["uploadfile/a.jpg"].height) \
        == (600, 400)
    assert got["uploadfile/b.jpg"].how == "data-src" and got["uploadfile/c.png"].how == "data-original"
    assert got["uploadfile/d.webp"].how == "data-lazy-src"
    assert got["l.jpg"].how == "srcset" and "s.jpg" not in got and "m.jpg" not in got
    assert {"x1.jpg", "x2.jpg"} <= set(got) and got["x2.jpg"].how == "srcset"
    assert "ds-big.jpg" in got and "ds-small.jpg" not in got
    assert got["p-big.webp"].how == "picture" and "p.jpg" in got
    assert got["data:image/gif;base64,R0lGODlhAQABAIAAAP"].how == "src"


def test_srcset_written_loosely_still_gives_its_largest():
    assert links.largest_in_srcset("a.jpg") == "a.jpg"
    assert links.largest_in_srcset(" a.jpg 1x ,b.jpg 2x ") == "b.jpg"
    assert links.largest_in_srcset("a.jpg 640w,b.jpg 1280w,") == "b.jpg"
    assert links.largest_in_srcset("") is None and links.largest_in_srcset(" , ") is None
    assert links.largest_in_srcset("https://x/img,w_100.jpg 100w, https://x/img,w_900.jpg 900w") == \
        "https://x/img,w_900.jpg"


def test_background_images_and_images_a_link_points_to():
    html = """<body><div class="card" style="background-image: url('/webservices/ajaximagereader.aspx?caes=A1')"></div>
      <div style="background:#fff url(uploadfile/bg-poster.jpg) no-repeat"></div>
      <div data-bg="uploadfile/hero.jpg"></div><table background="images/old.gif"><tr><td>x</td></tr></table>
      <a href="uploadfile/map.JPG">校區地圖</a><a href="uploadfile/rule.pdf">規章</a></body>"""
    got = imgs(html)
    assert got["/webservices/ajaximagereader.aspx?caes=A1"].how == "bg"
    assert got["uploadfile/bg-poster.jpg"].how == "bg"
    assert got["uploadfile/hero.jpg"].how == "data-bg" and got["images/old.gif"].how == "background"
    assert got["uploadfile/map.JPG"].how == "link" and got["uploadfile/map.JPG"].alt == "校區地圖"
    assert "uploadfile/rule.pdf" not in got  # an attachment, not an image (it stays in links)
    page = links.extract(html, URL)
    assert ("uploadfile/map.JPG", "校區地圖") in page.links  # a link is still a link


def test_one_image_written_twice_is_listed_once_with_the_body_zone_winning():
    html = """<body><header><img src="uploadfile/p.jpg"></header><main><img src="uploadfile/p.jpg"></main></body>"""
    page = links.extract(html, URL)
    assert [(i.src, i.zone) for i in page.images] == [("uploadfile/p.jpg", "unknown")]


def test_one_image_written_small_then_full_size_keeps_the_size_least_likely_to_be_tiny():
    # a sidebar thumbnail first, the same picture in the body at full size later: the body's picture is downloaded
    html = """<body><aside><img src="/aca/uploadfile/a.jpg" width="60"></aside><div><img src="/aca/uploadfile/a.jpg"></div>
      <img src="b.jpg" width="60" height="40"><img src="b.jpg" width="600" height="400" alt="海報">
      <img src="c.jpg" width="300"><img src="c.jpg" height="50"></body>"""
    got = imgs(html)
    assert (got["/aca/uploadfile/a.jpg"].zone, got["/aca/uploadfile/a.jpg"].width) == ("unknown", None)
    assert need(html, "/aca/uploadfile/a.jpg") == ("must", "")
    assert (got["b.jpg"].width, got["b.jpg"].height, got["b.jpg"].alt) == (600, 400, "海報")
    assert (got["c.jpg"].width, got["c.jpg"].height) == (None, None)  # each size unwritten once
    assert need(html, "c.jpg") == ("must", "")


# ---------------------------------------------------------------- zones


def test_zones_without_a_profile_header_nav_footer_aside_are_chrome_the_rest_unknown():
    html = """<body><header><img src="h.jpg"></header><nav><img src="n.jpg"></nav><div><img src="d.jpg"></div>
      <aside><img src="a.jpg"></aside><footer><img src="f.jpg"></footer></body>"""
    got = imgs(html)
    assert {k: i.zone for k, i in got.items()} == {"h.jpg": "chrome", "n.jpg": "chrome", "d.jpg": "unknown",
                                                   "a.jpg": "chrome", "f.jpg": "chrome"}
    assert not links.extract(html, URL).profiled


def test_a_header_or_footer_inside_an_article_main_or_section_is_content():
    # HTML-AAM: header and footer are the page's banner and footer only outside article, aside, main, nav and section.
    # WordPress (vd, fine-arts) puts a post's title and poster in <article><header class="entry-header">.
    html = """<body><header id="masthead"><img src="/wp-content/uploads/site-top.png"></header>
      <main><article><header class="entry-header"><h1>t</h1><img src="/wp-content/uploads/2024/05/poster.jpg"></header>
      <p>x</p><footer class="entry-footer"><img src="/wp-content/uploads/2024/05/sign.jpg"></footer></article></main>
      <section><footer><img src="sec.jpg"></footer></section><main><header><img src="main-head.jpg"></header></main>
      <article><nav><img src="art-nav.jpg"></nav><aside><img src="art-aside.jpg"></aside></article>
      <header><section><img src="head-sec.jpg"></section></header>
      <footer id="colophon"><article><header><img src="foot-art.jpg"></header></article></footer></body>"""
    zones = {k: i.zone for k, i in imgs(html, "https://vd.nknu.edu.tw/2024/05/1/").items()}
    assert zones == {"/wp-content/uploads/site-top.png": "chrome", "/wp-content/uploads/2024/05/poster.jpg": "unknown",
                     "/wp-content/uploads/2024/05/sign.jpg": "unknown", "sec.jpg": "unknown", "main-head.jpg": "unknown",
                     "art-nav.jpg": "chrome", "art-aside.jpg": "chrome",  # nav and aside stay layout anywhere
                     "head-sec.jpg": "chrome", "foot-art.jpg": "chrome"}  # inside the page's own header and footer
    assert need(html, "/wp-content/uploads/2024/05/poster.jpg", "https://vd.nknu.edu.tw/2024/05/1/") == ("must", "")


def test_zones_with_a_profile_only_its_content_area_is_body():
    html = """<body><div id="menu"><img src="m.jpg"></div><div id="content"><aside><img src="in.jpg"></aside>
      <img src="c.jpg"></div><header><img src="h.jpg"></header></body>"""
    page = links.extract(html, URL, content_css="#content")
    assert page.profiled
    assert {i.src: i.zone for i in page.images} == {"m.jpg": "chrome", "in.jpg": "body", "c.jpg": "body",
                                                    "h.jpg": "chrome"}


def test_the_honour_roll_area_is_body_on_every_site():
    # c.nknu's EditPlus home page (cprc): the honour posters sit in the left column, outside any content area
    html = """<body><div id="templatemo_main"><div id="HonorRollImg"><div id="HonorRollArea"><ul class="list2">
      <li><a href="#"><img src="UploadFile/HonorRoll/HonorRoll_1127172132.jpg"></a></li>
      <li><a href="#"><img src="UploadFile/HonorRoll/HonorRoll_banner_2.jpg"></a></li></ul></div></div></div>
      <aside><div id="HonorRollArea"><img src="UploadFile/HonorRoll/side.jpg"></div></aside></body>"""
    for css in (None, "#List_NewsA"):
        page = links.extract(html, "https://cprc.nknu.edu.tw/", content_css=css)
        assert {i.zone for i in page.images} == {"body"}
        for img in page.images:
            assert links.image_need(img, "https://cprc.nknu.edu.tw/", profiled=page.profiled) == ("must", "")


# ---------------------------------------------------------------- which images are not needed, and why


def test_albums_are_not_needed_but_aca_album_pages_are_news():
    html = '<body><img src="uploadfile/photo1.jpg" width="800"></body>'
    for album in ("https://c.nknu.edu.tw/affair/album_images.aspx?aid=3", "https://lis.nknu.edu.tw/gallery/12",
                  "https://sso.nknu.edu.tw/services/web/default.aspx?webidx=31&selpage=photo",
                  "https://geo.nknu.edu.tw/%E6%B4%BB%E5%8B%95%E8%8A%B1%E7%B5%AE/1"):
        assert need(html, "uploadfile/photo1.jpg", album) == ("no", "album"), album
    for news in ("https://c.nknu.edu.tw/aca/album.aspx", "https://c.nknu.edu.tw/aca/album_images.aspx?aid=3",
                 "https://c.nknu.edu.tw/aca/newsview.aspx?nid=3"):
        assert need(html, "uploadfile/photo1.jpg", news) == ("must", ""), news


def test_data_images_and_sizes_under_80():
    html = """<body><img src="data:image/png;base64,iVBORw0KGgo"><img src="pix.gif" width="1" height="1">
      <img src="t.jpg" width="79"><img src="ok.jpg" width="80" height="80"><img src="pct.jpg" width="100%">
      <img src="st.jpg" style="width: 40px; height: auto"><img src="px.jpg" height="60px"></body>"""
    assert need(html, "data:image/png;base64,iVBORw0KGgo") == ("no", "data")
    assert need(html, "pix.gif") == ("no", "tiny") and need(html, "t.jpg") == ("no", "tiny")
    assert need(html, "st.jpg") == ("no", "tiny") and need(html, "px.jpg") == ("no", "tiny")
    assert need(html, "ok.jpg") == ("must", "") and need(html, "pct.jpg") == ("must", "")


def test_a_data_image_is_listed_short():
    big = "data:image/png;base64," + "A" * 5000
    (img,) = links.extract(f'<img src="{big}">', URL).images
    assert img.src.startswith("data:image/png;base64,") and len(img.src) <= 80


def test_decoration_file_names_are_left_out_only_outside_the_body():
    html = """<body><header><img src="images/logo.png"></header><div><img src="uploadfile/banner-2026.jpg"></div>
      <div id="content"><img src="uploadfile/icon_seminar.jpg"></div><footer><img src="images/campus.jpg"></footer>
      </body>"""
    assert need(html, "images/logo.png") == ("no", "decor_name")
    assert need(html, "uploadfile/banner-2026.jpg") == ("must", "")  # unknown counts as body
    assert need(html, "images/campus.jpg") == ("no", "chrome")
    prof = "#content"
    assert need(html, "uploadfile/icon_seminar.jpg", content_css=prof) == ("must", "")
    assert need(html, "uploadfile/banner-2026.jpg", content_css=prof) == ("no", "decor_name")
    assert need(html, "images/campus.jpg", content_css=prof) == ("must", "")  # the profile decides layout


def test_images_paths_are_never_left_out_by_their_folder_alone():
    html = """<body><div><img src="/images/2022/12/24/notice.jpg"><img src="/aca/uploadfile/userfiles/2/images/擷取-流程.jpg">
      <img src="/reality/images/centermap.jpg"><img src="/templates/t3/img/photo.jpg"></div></body>"""
    page = links.extract(html, "https://lis.nknu.edu.tw/index.php/news/1")
    assert {links.image_need(i, "https://lis.nknu.edu.tw/index.php/news/1")[0] for i in page.images} == {"must"}


def test_the_repeat_rule_needs_5_pages_30_percent_and_never_the_body():
    tpl = "https://c.nknu.edu.tw/aca/images/top_bg.jpg"
    assert links.decor_repeat(tpl, pages_with=5, site_pages=16, ever_body=False)
    assert not links.decor_repeat(tpl, pages_with=4, site_pages=5, ever_body=False)  # 4 pages
    assert not links.decor_repeat(tpl, pages_with=5, site_pages=17, ever_body=False)  # 29%
    assert not links.decor_repeat(tpl, pages_with=50, site_pages=60, ever_body=True)  # once in the body
    for kept in ("https://c.nknu.edu.tw/aca/uploadfile/userfiles/2/images/flow.jpg",
                 "https://c.nknu.edu.tw/aca/UploadFile/HonorRoll/x.jpg",
                 "https://lis.nknu.edu.tw/images/2022/12/24/notice.jpg"):
        assert links.never_template(kept)
        assert not links.decor_repeat(kept, pages_with=50, site_pages=60, ever_body=False), kept
    assert not links.never_template(tpl) and not links.never_template("https://lis.nknu.edu.tw/images/logo.png")


def test_every_image_left_out_has_its_reason():
    html = """<body><header><img src="logo.png"><img src="h.jpg"></header><img src="data:,x"><img src="s.gif" width="8">
      <div><img src="a.jpg" data-src="b.jpg" srcset="c.jpg 2x"></div><nav style="background-image:url(n.jpg)"></nav>
      </body>"""
    page = links.extract(html, URL)
    assert len(page.images) == 8
    for img in page.images:
        got, why = links.image_need(img, URL, profiled=page.profiled)
        assert got in ("must", "no") and (why != "" if got == "no" else why == "")
        assert why in ("", *links.IMAGE_WHY)


def test_a_file_written_as_a_background_is_an_attachment_not_an_image():
    # elec's news list writes each notice's attachment as the box's CSS background (design §7.2): the shorthand
    # background:url(…) is read like background-image, and a document's extension makes it an attachment
    html = """<body><div id="ne_list_pic" style="background:url(https://elec.nknu.edu.tw/upload/item32/cyAb12.pdf)
      #FFFFFF no-repeat center center; background-size:contain;"></div>
      <div id="ne_list_pic" style="background:url(https://elec.nknu.edu.tw/upload/item32/cyCd34.jpg) #FFFFFF"></div>
      <div style="background-image: url('files/form.DOCX?v=2')"></div><table><tr><td background="old/scan.tif">x</td></tr></table>
      <div data-bg="upload/plan.odt"></div></body>"""
    page = links.extract(html, "https://elec.nknu.edu.tw/news.html")
    files = {h for h, t in page.links if t == "[background]"}
    assert files == {"https://elec.nknu.edu.tw/upload/item32/cyAb12.pdf", "files/form.DOCX?v=2", "old/scan.tif",
                     "upload/plan.odt"}
    assert {i.src for i in page.images} == {"https://elec.nknu.edu.tw/upload/item32/cyCd34.jpg"}
