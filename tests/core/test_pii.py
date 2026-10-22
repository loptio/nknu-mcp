from nknu.core.pii import find_pii, mask_honor_names, mask_name


def test_mask_three():
    assert mask_name("陳宥余") == "陳O余"


def test_mask_two():
    assert mask_name("陳余") == "陳O"


def test_mask_four():
    assert mask_name("歐陽宥余") == "歐OO余"


def test_honor_title_masked():
    assert mask_honor_names("賀！本系陳宥余、林小華同學榮獲全國競賽第一名") == "賀！本系陳O余、林O華同學榮獲全國競賽第一名"


def test_honor_teacher_masked():
    assert mask_honor_names("恭賀王大明老師獲選優良導師") == "恭賀王O明老師獲選優良導師"


def test_already_masked_untouched():
    t = "賀！本系陳O余同學榮獲佳作"
    assert mask_honor_names(t) == t


def test_no_name_untouched():
    t = "本校榮獲教育部教學卓越獎"
    assert mask_honor_names(t) == t


def test_find_pii_hits():
    assert find_pii("王O明請假")
    assert find_pii("聯絡 0912-345-678")
    assert find_pii("身分證 A123456789")
    assert find_pii("學號 411277043")
    assert find_pii("寄到 someone@gmail.com")


def test_find_pii_ignores_office_contacts():
    assert not find_pii("體育室 07-7172930 分機 1551，信箱 noreply@nknu.edu.tw")


def test_generic_words_before_honorific_untouched():
    for t in ("歡迎全體同學參加", "本系同學請注意", "指導老師請填寫表單"):
        assert mask_honor_names(t) == t


def test_department_prefix_stripped():
    assert mask_honor_names("賀資訊教育系陳宥余同學獲獎") == "賀資訊教育系陳O余同學獲獎"
