from datetime import date

import pytest

from nknu.core.rocdate import norm, parse_dows, parse_hours_segments, parse_roc_date, parse_roc_range, roc_str, roc_year


def test_basic_dot():
    assert parse_roc_date('115.09.07') == date(2026, 9, 7)


def test_inclusive_end_becomes_half_open():
    assert parse_roc_range('115.09.07至116.01.08') == (date(2026, 9, 7), date(2027, 1, 9))


def test_range_split_across_spans_and_fullwidth():
    # what BeautifulSoup gives for pe_open_hours.html (newlines + spaces between spans)
    assert parse_roc_range('115.09.07至\n 116.01.08 ') == (date(2026, 9, 7), date(2027, 1, 9))
    assert parse_roc_range('１１５．０９．０７至１１６．０１．０８') == (date(2026, 9, 7), date(2027, 1, 9))


def test_chinese_form_with_weekday_and_inherited_year():
    assert parse_roc_range('115年10月24日（週六）至10月26日（週一）') == (date(2026, 10, 24), date(2026, 10, 27))


def test_cross_year_explicit():
    assert parse_roc_range('116年12月31日（週五）至117年1月2日（週日）') == (date(2027, 12, 31), date(2028, 1, 3))


def test_cross_year_implicit_rollover():
    assert parse_roc_range('115年12月31日至1月2日') == (date(2026, 12, 31), date(2027, 1, 3))


def test_single_day():
    assert parse_roc_range('116年6月9日（週三）') == (date(2027, 6, 9), date(2027, 6, 10))


def test_mmdd_needs_context_year():
    assert parse_roc_range('10/24(六)', default_year=2026) == (date(2026, 10, 24), date(2026, 10, 25))
    with pytest.raises(ValueError):
        parse_roc_date('10/24')


def test_gregorian_passthrough_and_compact():
    assert parse_roc_date('2022/9/16') == date(2022, 9, 16)
    assert parse_roc_date('1150907') == date(2026, 9, 7)
    assert roc_year(2026) == 2026


def test_invalid():
    with pytest.raises(ValueError):
        parse_roc_date('115.13.01')          # month 13
    with pytest.raises(ValueError):
        parse_roc_date('115.02.30')          # Feb 30
    with pytest.raises(ValueError):
        parse_roc_range('116.01.08至115.09.07')  # explicit reversed
    with pytest.raises(ValueError):
        roc_year(500)


def test_leap_day():
    assert parse_roc_date('113.02.29') == date(2024, 2, 29)   # 2024 leap
    with pytest.raises(ValueError):
        parse_roc_date('114.02.29')


def test_dows():
    assert parse_dows('週一、二、三、五') == [1, 2, 3, 5]
    assert parse_dows('週一至週五') == [1, 2, 3, 4, 5]
    assert parse_dows('週一及五') == [1, 5]
    assert parse_dows('週二至四') == [2, 3, 4]
    assert parse_dows('週六、週日') == [6, 7]
    assert parse_dows('Mon.~Fri.') == [1, 2, 3, 4, 5]


def test_hours_from_shredded_spans():
    raw = '週一\n、\n二\n、\n三\n、\n五\n 19：\n00~\n21：00\n 週\n四 18\n：00\n~\n20\n：00'
    assert parse_hours_segments(raw) == [
        {'dows': [1, 2, 3, 5], 'opens': '19:00', 'closes': '21:00'},
        {'dows': [4], 'opens': '18:00', 'closes': '20:00'}]


def test_hours_2022_wording():
    raw = '開放時間：週一及五 19:00 至 21:50 、週二至四 18:30 至 21:30'
    assert parse_hours_segments(raw) == [
        {'dows': [1, 5], 'opens': '19:00', 'closes': '21:50'},
        {'dows': [2, 3, 4], 'opens': '18:30', 'closes': '21:30'}]


def test_norm_fullwidth_colon():
    assert norm('19：00 ～ 21：00') == '19:00~21:00'


def test_weekday_mismatch_rejected():
    with pytest.raises(ValueError, match="weekday"):
        parse_roc_range("115年10月24日（週五）")  # 2026-10-24 is a Saturday


def test_weekday_match_ok():
    assert parse_roc_range("115年10月24日(六)至10月26日(一)") == (date(2026, 10, 24), date(2026, 10, 27))


def test_space_typo_in_time():
    assert parse_hours_segments("週一至週五 16: 50~18：00")[0]["opens"] == "16:50"


def test_cross_midnight_segment_kept():
    assert parse_hours_segments("週五 22:00~02:00") == [{"dows": [5], "opens": "22:00", "closes": "02:00"}]


def test_roc_str():
    assert roc_str(date(2027, 1, 8)) == "116.01.08"
