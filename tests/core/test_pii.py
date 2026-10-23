from nknu.core.pii import find_pii, is_honor_title, mask_honor_names, mask_name, redact


def test_mask_three():
    assert mask_name("甲乙丙") == "甲O丙"


def test_mask_two():
    assert mask_name("甲乙") == "甲O"


def test_mask_four():
    assert mask_name("甲乙丙丁") == "甲OO丁"


def test_honor_title_masked():
    assert mask_honor_names("賀！本系陳乙丙、林己庚同學榮獲全國競賽第一名") == "賀！本系陳O丙、林O庚同學榮獲全國競賽第一名"


def test_honor_teacher_masked():
    assert mask_honor_names("恭賀黃壬癸老師獲選優良導師") == "恭賀黃O癸老師獲選優良導師"


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
    assert find_pii("學號 400000000")
    assert find_pii("寄到 someone@gmail.com")


def test_find_pii_ignores_office_contacts():
    assert not find_pii("體育室 07-7172930 分機 1551，信箱 noreply@nknu.edu.tw")


def test_generic_words_before_honorific_untouched():
    for t in ("歡迎全體同學參加", "本系同學請注意", "指導老師請填寫表單"):
        assert mask_honor_names(t) == t


def test_department_prefix_stripped():
    assert mask_honor_names("賀資訊教育系張乙丙同學獲獎") == "賀資訊教育系張O丙同學獲獎"


def test_generated_ids_are_not_emails():
    assert find_pii("UID:0l8dnv5qpj1p4871b3bf62b8vu@google.com") == []
    assert find_pii("someone.name@google.com")


def test_name_before_teacher_with_long_prefix():
    assert mask_honor_names("恭喜 理學院化學系何乙丙老師榮升副教授~~") == "恭喜 理學院化學系何O丙老師榮升副教授~~"


def test_role_word_then_name_then_verb():
    assert mask_honor_names("【恭賀】本所研究生謝乙丙考取高雄市國民小學正式教師") == "【恭賀】本所研究生謝O丙考取高雄市國民小學正式教師"
    assert mask_honor_names("【恭賀】本所畢業生丁戊己考取高雄市正式教師") == "【恭賀】本所畢業生丁O己考取高雄市正式教師"


def test_team_is_not_a_name():
    t = "恭賀本系學生團隊榮獲全國競賽冠軍"
    assert mask_honor_names(t) == t


def test_two_char_name_after_department():
    assert mask_honor_names("賀化學系林乙老師獲獎") == "賀化學系林O老師獲獎"


def test_wikidata_qid_is_not_a_national_id():
    assert find_pii("wikidata_qid: Q127164121") == []
    assert find_pii("身分證 Q123456789")


def test_role_word_space_then_name_list_then_verb():
    # F-honor-title-shapes: 「系友 名字、名字參加…」 (role word, a space, names joined by 、, then 參加)
    t = "【賀榜】恭賀本系112級系友 陳乙丙、黃丁戊參加尋找感動地圖實踐計畫，榮獲創新無限獎!"
    assert mask_honor_names(t) == "【賀榜】恭賀本系112級系友 陳O丙、黃O戊參加尋找感動地圖實踐計畫，榮獲創新無限獎!"
    assert mask_honor_names("恭賀本所研究生、林乙丙榮獲佳作") == "恭賀本所研究生、林O丙榮獲佳作"


def test_space_between_name_and_honorific():
    assert mask_honor_names("恭賀本系 陳乙丙 同學榮獲書卷獎") == "恭賀本系 陳O丙 同學榮獲書卷獎"
    assert mask_honor_names("恭喜數學系 吳乙丙教授 榮登頂尖科學家榜單") == "恭喜數學系 吳O丙教授 榮登頂尖科學家榜單"


def test_name_after_a_grade_word():
    assert mask_honor_names("恭賀電子系大三陳乙同學榮獲獎學金") == "恭賀電子系大三陳O同學榮獲獎學金"
    assert mask_honor_names("恭賀電子系大三陳乙丙同學榮獲獎學金") == "恭賀電子系大三陳O丙同學榮獲獎學金"


def test_compound_surname_kept_whole():
    assert mask_honor_names("恭賀本系歐陽乙丙同學榮獲佳作") == "恭賀本系歐OO丙同學榮獲佳作"


def test_words_before_an_honorific_are_not_names():
    # F-honor-overmask: no name-like span (a surname first), nothing masked
    for t in ("恭喜 理學院數學系葉乙丙老師榮升教授~~", "賀歷史系校友榮獲傑出校友", "恭賀本校講座教授榮獲學術獎",
              "恭賀本系特聘教授榮獲國科會傑出研究獎", "高師大校慶 聚焦名譽博士頒授典禮", "高師大校友會贈樹植花",
              "高師大高雄市校友會辦藝術品義賣", "傑出企業家連任全國校友總會長", "管理學院校友回娘家暨永續發展專題演講",
              "-請校內住宿同學注意自身安全", "敦親睦鄰!籲請同學勿於鄰近公園喝酒", "【學生期中文字意見回饋】歡迎同學踴躍填答",
              "高師大首次辦理教師研習 千位老師熱烈參與", "115學年度博士班招生", "【徵才】成教所林教授兼任研究助理徵才公告",
              "美術系林主任個展開幕", "請所屬畢業年度的校友上網填答", "日本教授來訪", "恭賀本校高中老師榮獲師鐸獎",
              "校長為居家檢疫同學打氣慰問", "前進澳門教育展 校友主動行銷暨成立校友籌備會",
              "調查本國籍畢業生，請所屬畢業年度的校友上網填答", "國文系王主任受專訪"):
        assert mask_honor_names(t) == t.replace("葉乙丙", "葉O丙"), t


def test_role_then_verb_needs_a_name():
    for t in ("歡迎同學踴躍參加", "本校選手代表參加全國賽", "指導老師帶領學生參加競賽", "恭賀本系學生團隊榮獲全國競賽冠軍"):
        assert mask_honor_names(t) == t


def test_honor_titles_by_title_not_category():
    # F-sso-crosspost-honor: the same 恭賀 post under 徵才就業 is honor content
    for t in ("【恭賀】本所研究生謝乙丙考取苗栗縣115學年度國民中學正式教師", "賀 本系學生參加競賽 榮獲金牌", "【狂賀】電子系陳乙丙同學榮獲獎學金",
              "恭喜 理學院化學系何乙丙老師榮升副教授~~", "電子系112半導體佈局設計工程師能力鑑定考試通過名單", "[物理學系]恭賀 115級研究所榜單",
              "114年體育運動獎助學金-獲選名單"):
        assert is_honor_title(t), t
    for t in ("【徵才】成教所兼任研究助理徵才公告", "115學年度第1學期停修申請", "高師大校友會贈樹植花", "道賀",
              "115年第二次學生專業證照取得獎勵活動申請事宜", "學生參與觀賞運動競技或表演補助辦法115年度第3次公告適用賽事名單"):
        assert not is_honor_title(t), t


def test_national_id_next_to_chinese_and_spaced_mobile():
    # F-redact-gaps
    assert find_pii("身分證A123456789號碼")
    assert find_pii("手機0912 345 678")
    assert find_pii("手機 0912 345678")
    assert redact("我的身分證A123456789，手機0912 345 678") == "我的身分證[MASKED]，手機[MASKED]"


def test_hex_hashes_are_not_phone_or_student_numbers():
    # F-check-scope: uv.lock hashes once read as mobiles and student numbers
    assert find_pii("sha256:c7e58ce09192557605d8bbd") == []
    assert find_pii("05221a5d8b3c476330730b3adb4") == []


def test_names_after_the_school_or_a_role_word_in_an_honor_title():
    # review of G4: shapes the honorific and role+verb rules missed on the owner's real titles (names here are synthetic)
    t = "高師大袁乙丙挺身傳愛 獲頒「2016年全球熱愛生命」獎學金105.11.29"  # school name, a name, then any words
    assert is_honor_title(t) and mask_honor_names(t) == "高師大袁O丙挺身傳愛 獲頒「2016年全球熱愛生命」獎學金105.11.29"
    t = "高師大視設系碩二生曾乙丙獲周丁戊熱愛生命獎104.10.15"  # 獲…獎, and a grade word as the role
    assert is_honor_title(t) and "曾O丙獲" in mask_honor_names(t)
    t = "高師大王乙丙謝丁戊獲國家新創獎104.12.24"  # two names written together
    assert is_honor_title(t) and mask_honor_names(t) == "高師大王O丙謝O戊獲國家新創獎104.12.24"
    t = "高師大腦麻研究生林乙丙以腳創作藝術 獲總統教育獎111.06.07"  # role word, a name, then any words
    assert is_honor_title(t) and mask_honor_names(t) == "高師大腦麻研究生林O丙以腳創作藝術 獲總統教育獎111.06.07"


def test_school_and_role_words_followed_by_ordinary_words_stay():
    for t in ("[恭賀]115年度物理系教師甄試榜單", "恭賀本校高教深耕計畫榮獲教育部肯定", "高師大高雄市校友會榮獲表揚",
              "賀 本系學生團隊榮獲金牌", "恭賀研究生學會榮獲優良社團"):
        assert mask_honor_names(t) == t, t
    assert not is_honor_title("獲教育部補助經費辦理活動，詳見附件")  # 獲 with no 獎 after it


def test_full_width_digits_and_a_lowercase_id_letter_are_found():
    # review of G7: a phone IME types full-width digits, and an id is often typed in lower case (misses.jsonl)
    assert redact("我的學號４０００００００１怎麼查成績") == "我的學號[MASKED]怎麼查成績"
    assert redact("手機０９１２３４５６７８聯絡誰") == "手機[MASKED]聯絡誰"
    assert redact("身分證a123456789") == "身分證[MASKED]"
    assert find_pii("sha256:a123456789bcdef0") == []  # a hash that starts like an id is not one
