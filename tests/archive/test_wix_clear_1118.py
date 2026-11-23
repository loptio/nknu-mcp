"""11-18 evening (爬虫judge: 「357 张只有模糊版的补抓原图：做…目标是认得出字，不需要最大的原始档」「见到 blur_ 就排原图
这条规则进每晚用的程序」): a Wix page's <img> may name its picture only by the blurred placeholder its script replaces
(…/media/<id>/v1/fill/w_49,h_28,…,blur_2,…/<id>); read as written, the archive holds a picture nobody can read. A
picture of Wix's media server a page writes in blurred sizes only is also asked for whole and clear, inside 1920 by
1920 (…/media/<id>/v1/fit/w_1920,h_1920,q_90/<id>: Wix answers the whole picture scaled down, probe of 11-18), as a
picture of that page with the placeholder's place, words and size. A page that writes a clear size too asks for no
more. Made-up media ids."""

from __future__ import annotations

from nknu.archive import links

PAGE = "https://example.wixsite.com/unit/post/a"
WIX = "https://static.wixstatic.com/media/"
ID = "abc123_0f918803a45242df8bc357fce427a8f0~mv2.png"
OTHER = "abc123_923f0490dec24ad79bede49a089b96f6~mv2_d_4800_3200_s_4_2.jpg"
BLUR = f"{WIX}{ID}/v1/fill/w_49,h_28,al_c,q_85,usm_0.66_1.00_0.01,blur_2,enc_avif,quality_auto/{ID}"
BLUR_BIG = f"{WIX}{ID}/v1/fill/w_1774,h_1000,fp_0.50_0.50,q_35,blur_30,enc_avif,quality_auto/{ID}"
CLEAR = f"{WIX}{ID}/v1/fill/w_1128,h_636,fp_0.50_0.50,q_95,enc_avif,quality_auto/{ID}"
WHOLE = f"{WIX}{ID}/v1/fit/w_1920,h_1920,q_90/{ID}"


def page(*imgs: str) -> str:
    return "<html><body><main><h1>公告</h1><p>內文</p>" + "".join(imgs) + "</main></body></html>"


def images(html: str) -> dict[str, links.Image]:
    return {i.src: i for i in links.extract(html, PAGE).images}


def test_a_picture_written_blurred_only_is_also_asked_for_whole_and_clear():
    got = images(page(f'<img src="{BLUR}" alt="海報" width="980" height="551">'))
    assert set(got) == {BLUR, WHOLE}
    whole, blur = got[WHOLE], got[BLUR]
    assert whole.how == "wix-clear" and blur.how == "src"
    assert (whole.zone, whole.alt, whole.width, whole.height) == (blur.zone, "海報", 980, 551)
    assert links.image_need(whole, PAGE) == ("must", "")


def test_two_blurred_sizes_of_one_picture_ask_for_it_once():
    got = images(page(f'<img src="{BLUR}">', f'<img src="{BLUR_BIG}" alt="海報">'))
    assert set(got) == {BLUR, BLUR_BIG, WHOLE} and got[WHOLE].alt == "海報"


def test_a_page_that_writes_a_clear_size_asks_for_no_more():
    assert set(images(page(f'<img src="{BLUR}">', f'<img src="{CLEAR}">'))) == {BLUR, CLEAR}
    assert set(images(page(f'<img src="{BLUR}" srcset="{CLEAR} 1128w">'))) == {BLUR, CLEAR}
    assert set(images(page(f'<img src="{WIX}{ID}">'))) == {f"{WIX}{ID}"}  # the picture itself, no size


def test_each_picture_is_judged_by_itself():
    other_blur = f"{WIX}{OTHER}/v1/fill/w_288,h_192,al_c,q_80,usm_0.66_1.00_0.01,blur_2,enc_avif,quality_auto/{OTHER}"
    got = images(page(f'<img src="{BLUR}">', f'<img src="{CLEAR}">', f'<img src="{other_blur}">'))
    assert set(got) == {BLUR, CLEAR, other_blur, f"{WIX}{OTHER}/v1/fit/w_1920,h_1920,q_90/{OTHER}"}


def test_a_tiny_placeholder_s_whole_picture_is_tiny_too():
    got = images(page(f'<img src="{BLUR}" width="24" height="24">'))
    assert links.image_need(got[WHOLE], PAGE) == ("no", "tiny")


def test_other_servers_pictures_and_clear_pictures_are_as_before():
    elsewhere = "https://img.example.org/media/a.png/v1/fill/w_49,h_28,blur_2/a.png"
    assert set(images(page(f'<img src="{elsewhere}">'))) == {elsewhere}
    assert set(images(page(f'<img src="{CLEAR}">'))) == {CLEAR}
    relative = f'<img src="//static.wixstatic.com/media/{ID}/v1/fill/w_49,h_28,blur_2/{ID}">'
    assert WHOLE in images(page(relative))  # a scheme-relative address is Wix's all the same
