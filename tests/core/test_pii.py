from nknu.core.pii import find_pii, mask_honor_names, mask_name


def test_mask_three():
    assert mask_name("甲乙丙") == "甲O丙"


def test_mask_two():
    assert mask_name("甲乙") == "甲O"


def test_mask_four():
    assert mask_name("甲乙丙丁") == "甲OO丁"


def test_honor_title_masked():
    assert mask_honor_names("賀！本系甲乙丙、戊己庚同學榮獲全國競賽第一名") == "賀！本系甲O丙、戊O庚同學榮獲全國競賽第一名"


def test_honor_teacher_masked():
    assert mask_honor_names("恭賀辛壬癸老師獲選優良導師") == "恭賀辛O癸老師獲選優良導師"


def test_already_masked_untouched():
    t = "賀！本系甲O丙同學榮獲佳作"
    assert mask_honor_names(t) == t


def test_no_name_untouched():
    t = "本校榮獲教育部教學卓越獎"
    assert mask_honor_names(t) == t


def test_find_pii_hits():
    assert find_pii("辛O癸請假")
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
    assert mask_honor_names("賀資訊教育系甲乙丙同學獲獎") == "賀資訊教育系甲O丙同學獲獎"


def test_generated_ids_are_not_emails():
    assert find_pii("UID:0l8dnv5qpj1p4871b3bf62b8vu@google.com") == []
    assert find_pii("someone.name@google.com")


def test_name_before_teacher_with_long_prefix():
    assert mask_honor_names("恭喜 理學院化學系甲乙丙老師榮升副教授~~") == "恭喜 理學院化學系甲O丙老師榮升副教授~~"


def test_role_word_then_name_then_verb():
    assert mask_honor_names("【恭賀】本所研究生甲乙丙考取高雄市國民小學正式教師") == "【恭賀】本所研究生甲O丙考取高雄市國民小學正式教師"
    assert mask_honor_names("【恭賀】本所畢業生丁戊己考取高雄市正式教師") == "【恭賀】本所畢業生丁O己考取高雄市正式教師"


def test_team_is_not_a_name():
    t = "恭賀本系學生團隊榮獲全國競賽冠軍"
    assert mask_honor_names(t) == t


def test_two_char_name_after_department():
    assert mask_honor_names("賀化學系甲乙老師獲獎") == "賀化學系甲O老師獲獎"


def test_wikidata_qid_is_not_a_national_id():
    assert find_pii("wikidata_qid: Q127164121") == []
    assert find_pii("身分證 Q123456789")
