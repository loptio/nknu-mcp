from datetime import date

from nknu.content.terms import (
    Term,
    canonical_tokens,
    current_term,
    event_term,
    find_cohorts,
    find_terms,
    query_terms,
    school_spelling,
)
from nknu.core.textnorm import tokenize

TODAY = date(2026, 11, 1)  # 115-1


def test_current_term():
    assert current_term(date(2026, 11, 1)) == Term(115, 1)
    assert current_term(date(2027, 1, 10)) == Term(115, 1)
    assert current_term(date(2026, 2, 15)) == Term(114, 2)
    assert current_term(date(2026, 7, 20)) == Term(114, 2)
    assert current_term(date(2026, 8, 1)) == Term(115, 1)


def test_same_semester_three_ways():
    for s in ("115-1", "115學年度第1學期", "115學年度上學期"):
        toks = canonical_tokens(s)
        assert "term115s1" in toks and "ay115" in toks, s


def test_semester_spellings():
    assert find_terms("115學年度第一學期線上申請停修") == [Term(115, 1)]
    assert find_terms("115學年第二學期") == [Term(115, 2)]
    assert find_terms("115學年度下學期") == [Term(115, 2)]
    assert find_terms("（１１５－１實施）") == [Term(115, 1)]  # full width, as some pages write it


def test_spaced_pdf_text():
    # PDF text puts spaces between digits and CJK: most 學年度 mentions in the corpus look like this
    assert find_terms("115 學年度第 1 學期") == [Term(115, 1)]
    assert find_terms("自 111 學年度\n起實施") == [Term(111, None)]
    assert find_cohorts("111 學年度 起 入學") == [(111, True)]


def test_dates_are_not_terms():
    for s in ("115-10-24", "07-7172930", "1150610"):
        assert canonical_tokens(s) == [], s


def test_glued_and_implausible_numbers_are_not_terms():
    assert find_terms("1115學年度") == []
    assert find_terms("2115-1") == []
    assert find_terms("115-1-3") == []
    assert find_terms("201-1 教室") == []  # room numbers; 115-1 style only for years 90-130
    assert find_terms("087-2 號") == []
    assert find_terms("1 12 學年度") == []  # PDF split of 112: never read as year 12
    assert find_terms("105.2.9 101 學年度起實施") == [Term(101, None)]  # a date then a year, as 沿革 writes it


def test_order_and_dedupe():
    text = "114學年度第2學期至115學年度第1學期；另見115-1、116學年度"
    assert find_terms(text) == [Term(114, 2), Term(115, 1), Term(116, None)]


def test_year_only():
    assert find_terms("116學年度研究所碩博士班甄試入學招生簡章") == [Term(116, None)]
    assert canonical_tokens("116學年度研究所碩博士班甄試入學招生簡章") == ["ay116"]


def test_cohort():
    assert find_cohorts("113學年度起入學學生適用") == [(113, True)]
    assert find_cohorts("113學年度入學") == [(113, False)]
    assert find_cohorts("110學年度(含)以後入學") == [(110, True)]
    assert find_cohorts("111學年度（含）以後入學之學生") == [(111, True)]
    assert find_cohorts("112學年度新生") == [(112, False)]


def test_cohort_is_not_a_period():
    assert find_terms("113學年度起入學學生適用") == []
    assert canonical_tokens("113學年度起入學學生適用") == ["entry113"]


def test_relative_words():
    assert "term115s2" in canonical_tokens("下學期停修", today=TODAY)
    assert "term115s2" not in canonical_tokens("下學期停修")


def test_relative_words_all():
    def toks(s, today=TODAY):
        return canonical_tokens(s, today=today)

    assert toks("這學期") == toks("本學期") == ["term115s1", "ay115"]
    assert toks("上學期") == ["term114s2", "ay114"]
    assert toks("本學年") == ["ay115"]
    assert toks("下學年") == ["ay116"]
    assert toks("上學年") == ["ay114"]
    assert toks("去年") == ["ay114"]
    spring = date(2026, 3, 1)  # 114-2
    assert toks("下學期", spring) == ["term115s1", "ay115"]
    assert toks("上學期", spring) == ["term114s1", "ay114"]


def test_absolute_term_is_not_also_relative():
    assert canonical_tokens("115學年度上學期", today=TODAY) == ["term115s1", "ay115"]
    assert canonical_tokens("115學年度下學期", today=TODAY) == ["term115s2", "ay115"]


def test_query_only_patterns():
    assert canonical_tokens("115上 選課", today=TODAY) == ["term115s1", "ay115"]
    assert canonical_tokens("115下行事曆", today=TODAY) == ["term115s2", "ay115"]
    assert canonical_tokens("115上 選課") == []  # document text: 115上 is not read
    assert canonical_tokens("115下午", today=TODAY) == []
    assert canonical_tokens("我是112入學的", today=TODAY) == ["entry112"]
    assert canonical_tokens("112入學") == []


def test_event_term_from_first_day():
    # fix round 2 (11-01): a calendar event whose title names no term takes its first day's 學年度 (Aug–Jul) and 學期
    assert event_term("大學部網路選課Online course registration", date(2027, 2, 15)) == Term(115, 2)
    assert event_term("申辦停修Application for course withdrawal", date(2026, 11, 3)) == Term(115, 1)
    assert event_term("註冊及全校上課開始Classes begin", date(2026, 9, 7)) == Term(115, 1)
    assert event_term("研究生申請參加論文考試截止", date(2027, 1, 15)) == Term(115, 1)  # January is still semester 1
    assert event_term("期末考試Final exams", date(2026, 6, 8)) == Term(114, 2)
    assert event_term("本學期研究生論文考試完畢", date(2027, 7, 30)) == Term(115, 2)  # 本學期 names no semester


def test_event_term_semester_written_in_title():
    assert event_term("學生申請第2學期休學截止", date(2027, 6, 11)) == Term(115, 2)
    assert event_term("第一學期課業結束", date(2021, 1, 9)) == Term(109, 1)
    assert event_term("第二學期研究生網路選課", date(2027, 1, 25)) == Term(115, 2)  # the title, not January's 1
    assert event_term("下學期課表公布", date(2027, 1, 10)) == Term(115, 2)
    assert event_term("上學期成績補登截止", date(2027, 3, 1)) == Term(115, 1)


def test_event_term_title_names_the_term():
    # a whole term in the title is taken as written, also when the event falls in another 學年度 (the calendar has
    # next term's timetable published in July, and 109-2's deadlines extended into September 2021)
    assert event_term("公布109學年度第一學期班級課表", date(2020, 7, 6)) == Term(109, 1)
    assert event_term("109-2研究生學位考試截止", date(2021, 9, 30)) == Term(109, 2)
    assert event_term("112學年度第2學期大學部網路選課", date(2024, 1, 29)) == Term(112, 2)


def test_event_term_next_semester_words():
    # fix round 3 (11-01): at a semester's turn the calendar posts what the next one needs; a title that says so
    # (新學期 / 下學期 / 次學期) gets the semester after its first day's, Spring / 春季 the coming second semester and
    # Fall / 秋季 the coming first one (real titles, January and June)
    assert event_term("公布新學期班級課表Announcement of course schedule for the Spring semester",
                      date(2027, 1, 8)) == Term(115, 2)
    assert event_term("公布新學期班級課表", date(2027, 6, 25)) == Term(116, 1)
    assert event_term("公布班級課表 Announcement of course schedule for the Springsemester", date(2021, 1, 8)) == Term(109, 2)
    assert event_term("公布班級課表 nnouncement of course schedule for the Fallsemester", date(2022, 6, 24)) == Term(111, 1)
    assert event_term("次學期班級課表公布", date(2026, 6, 26)) == Term(115, 1)
    assert event_term("公布春季班級課表", date(2027, 1, 8)) == Term(115, 2)
    assert event_term("公布秋季班級課表", date(2027, 6, 25)) == Term(116, 1)
    # not these: 中秋, 春節, the previous semester
    assert event_term("中秋節(放假)Mid-Autumn Festival (National holiday)", date(2026, 9, 25)) == Term(115, 1)
    assert event_term("農曆除夕及春節假期(放假)Lunar New Year Holiday", date(2027, 2, 4)) == Term(115, 2)
    assert event_term("補考(請假補考)Make-up Final Exam from the previous semester", date(2020, 2, 24)) == Term(108, 2)


def test_event_term_named_semester_under_way_or_coming():
    # 第1學期 / 第2學期 without a year, Spring, Fall: that semester while it is under way, else the coming one (a July
    # 第1學期 is next 學年度's)
    assert event_term("第1學期網路預選課(試選-系統測試) Online course registration", date(2020, 7, 27)) == Term(109, 1)
    assert event_term("第一學期大學部網路選課", date(2026, 7, 20)) == Term(115, 1)
    assert event_term("第二學期結束，學年終了Second semester (Spring semester) ends", date(2022, 7, 31)) == Term(110, 2)
    assert event_term("Spring semester ends", date(2027, 7, 31)) == Term(115, 2)
    assert event_term("Fall semester final exams", date(2027, 1, 4)) == Term(115, 1)


def test_event_term_last_semester():
    # 上學期 is the semester before the first day's, as 下學期 is the one after it
    assert event_term("上學期成績補登截止", date(2026, 9, 15)) == Term(114, 2)


def test_event_term_year_alone_is_not_the_term():
    # a year alone in an event title is the admission year or a cohort: the first day decides
    assert event_term("114學年度大學美術術科考試", date(2025, 2, 8)) == Term(113, 2)
    assert event_term("研究生申報指導教授(限112學年(含)以前入學者)", date(2025, 10, 20)) == Term(114, 1)


def test_query_terms_rest():
    q = query_terms("115學年度第1學期 停修", TODAY)
    assert q.terms == [Term(115, 1)]
    assert tokenize(q.rest) == ["停修"]
    q = query_terms("我是113學年度入學的，下學期要停修", TODAY)
    assert q.terms == [Term(115, 2)]
    assert q.cohorts == [(113, False)]
    assert tokenize(q.rest) == ["我是", "的", "要停", "停修"]
    assert query_terms("下學期停修").terms == []  # no today: relative words stay words


def test_school_spelling():
    # plan 2026-11-02 T8: the one text bge-m3 and the reranker read, each term, cohort and relative word written the
    # school's way (11-01: one semester spelling), so three spellings of one semester get one model query
    today = date(2026, 11, 1)  # 115-1
    for q in ("115-1 停修", "這學期 停修", "１１５－１ 停修", "115學年度第1學期 停修"):
        assert school_spelling(q, today) == "115學年度第1學期 停修", q
    assert school_spelling("下學期選課", today) == "115學年度第2學期選課"
    assert school_spelling("112入學 畢業學分", today) == "112學年度入學 畢業學分"
    assert school_spelling("113學年度起入學 畢業學分", today) == "113學年度起入學 畢業學分"
    assert school_spelling("TOEIC 多益", today) == "TOEIC 多益"
    assert school_spelling("115上 選課", today) == "115學年度第1學期 選課"
    assert school_spelling("本學年 去年 行事曆", today) == "115學年度 114學年度 行事曆"
    # norm_text longer than NFKC (İ lowercases to two characters): the spellings go in front, the query stays
    assert school_spelling("İ 115-1 這學期 停修", today) == "115學年度第1學期 İ 115-1 這學期 停修"
