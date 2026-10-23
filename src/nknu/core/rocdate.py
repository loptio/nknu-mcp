"""ROC (民國) date / weekday / hours parsing for NKNU pages.

Conventions
- All returned date ranges are HALF-OPEN [start, end): an inclusive end date in the
  source ("116.01.08 含當天") becomes end = 2027-01-09.
- Input text is normalised with NFKC first (全形冒號 '：' -> ':', 全形數字 -> ASCII),
  and all whitespace (incl. U+3000, NBSP, zero-width) is removed, so "19：\n00~\n21：00"
  coming from text split across many <span>s still parses.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, time, timedelta

ROC_OFFSET = 1911
_WS = re.compile(r'[\s\u200b\u200c\u200d\ufeff\xa0\u3000]+')


def norm(s: str) -> str:
    s = unicodedata.normalize('NFKC', s)
    s = _WS.sub('', s)
    # unify range separators
    s = s.replace('～', '~').replace('〜', '~').replace('－', '-').replace('–', '-').replace('—', '-')
    return s


def roc_str(d: date) -> str:
    """2027-01-08 -> '116.01.08'."""
    return f'{d.year - ROC_OFFSET}.{d.month:02d}.{d.day:02d}'


def roc_year(y: int) -> int:
    """ROC year -> Gregorian. Accepts already-Gregorian years (>=1912) unchanged."""
    if y >= 1912:
        return y
    if not 1 <= y <= 200:
        raise ValueError(f'implausible ROC year {y}')
    return y + ROC_OFFSET


# one date token: (Y)(sep)M(sep)D  or  M/D (year from context). A year written with . / - uses the same separator
# before the day, so in '10/24-10/26' the '10/' is a month, not 民國10年 (F-rocdate-dash)
_DATE = re.compile(
    r'(?:(?P<y>\d{2,4})(?:年|(?P<ys>[./-])))?(?P<m>\d{1,2})(?(ys)(?P=ys)|(?:月|\.|/|-))(?P<d>\d{1,2})日?'
    # weekday annotation, e.g. (四) / （週四） / 週四: only with brackets or a 週/星期/周 prefix,
    # so a following word like 一般 is never read as a weekday
    r'(?:[(（](?:週|星期|周)?(?P<w>[一二三四五六日天])[)）]|(?:週|星期|周)(?P<w2>[一二三四五六日天]))?')
_RANGE_SEP = r'(?:至|到|~|-|起至|─)'


def parse_roc_date(s: str, default_year: int | None = None) -> date:
    t = norm(s)
    m = re.fullmatch(r'(\d{3})(\d{2})(\d{2})', t)  # 1150907
    if m:
        return date(roc_year(int(m[1])), int(m[2]), int(m[3]))
    m = _DATE.fullmatch(t)
    if not m:
        raise ValueError(f'not a date: {s!r}')
    if m['y'] is None:
        if default_year is None:
            raise ValueError(f'year missing and no default: {s!r}')
        y = default_year
    else:
        y = roc_year(int(m['y']))
    d = date(y, int(m['m']), int(m['d']))
    w = m['w'] or m['w2']
    if w and _DOW[w] != d.isoweekday():
        raise ValueError(f'weekday mismatch in {s!r}: {d} is not 週{w}')
    return d


def parse_roc_range(s: str, default_year: int | None = None) -> tuple[date, date]:
    """'115.09.07至116.01.08' -> (2026-09-07, 2027-01-09)  (half-open, end inclusive in source).
    A single date yields a one-day range. The second date inherits the first one's year;
    if it then falls before the start (e.g. '12/31至1/2') the year is rolled forward."""
    t = norm(s)
    toks = list(_DATE.finditer(t))
    if not toks:
        raise ValueError(f'no date in {s!r}')
    first = toks[0]
    start = parse_roc_date(first.group(0), default_year)
    if len(toks) == 1:
        return start, start + timedelta(days=1)
    second = toks[1]
    between = t[first.end():second.start()]
    if not re.fullmatch(_RANGE_SEP, between):
        raise ValueError(f'two dates without range separator in {s!r}: {between!r}')
    explicit_year = second['y'] is not None
    end_incl = parse_roc_date(second.group(0), start.year)
    if end_incl < start:
        if explicit_year:
            raise ValueError(f'range end before start: {s!r}')
        end_incl = end_incl.replace(year=end_incl.year + 1)
    return start, end_incl + timedelta(days=1)


_DOW = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 7, '天': 7}
_DOW_EN = {'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6, 'sun': 7}


def parse_dows(s: str) -> list[int]:
    """'週一、二、三、五' -> [1,2,3,5]; '週一至週五' -> [1..5]; '週一及五' -> [1,5];
    '週二至四' -> [2,3,4]; '週六、週日' -> [6,7]; 'Mon.~Fri.' -> [1..5]."""
    t = norm(s).lower()
    t = re.sub(r'(週|星期|周|禮拜)', '', t)
    for k, v in _DOW_EN.items():
        t = re.sub(k + r'[a-z]*\.?', '一二三四五六日'[v - 1], t)
    out: set[int] = set()
    for m in re.finditer(r'([一二三四五六日天])(?:(?:至|到|~|-)([一二三四五六日天]))?', t):
        a = _DOW[m[1]]
        b = _DOW[m[2]] if m[2] else a
        if b < a:
            raise ValueError(f'reverse weekday range in {s!r}')
        out.update(range(a, b + 1))
    if not out:
        raise ValueError(f'no weekday in {s!r}')
    return sorted(out)


_HHMM = r'(\d{1,2}):(\d{2})'
_SEG = re.compile(
    r'(?P<dows>(?:週|星期|周)[一二三四五六日天](?:(?:、|,|及|與|和|至|到|~|-)(?:週|星期|周)?[一二三四五六日天])*)'
    r'(?:開放時間)?:?' + _HHMM + r'(?:~|至|到|-)' + _HHMM)


def parse_hhmm(h: str, m: str) -> time:
    hh, mm = int(h), int(m)
    if hh == 24 and mm == 0:
        return time(23, 59, 59)
    return time(hh, mm)


def parse_hours_segments(s: str) -> list[dict]:
    """'週一、二、三、五 19：00~21：00 週四 18：00~20：00' ->
    [{'dows':[1,2,3,5],'opens':'19:00','closes':'21:00'}, {'dows':[4],...}]"""
    t = norm(s)
    segs = []
    for m in _SEG.finditer(t):
        segs.append({'dows': parse_dows(m['dows']),
                     'opens': parse_hhmm(m[2], m[3]).strftime('%H:%M'),
                     'closes': parse_hhmm(m[4], m[5]).strftime('%H:%M')})
    if not segs:
        raise ValueError(f'no hours segment in {s!r}')
    return segs
