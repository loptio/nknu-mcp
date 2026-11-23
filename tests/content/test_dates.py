import io
import json
import time
from datetime import date

from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from nknu.content import dates, serverdates
from nknu.content.dates import IDENTITY_ORDER, DateFact, extract_periods, identity_date, load_server_dates
from nknu.content.serverdates import collect_last_modified


def _f(kind, iso):
    return DateFact(kind, date.fromisoformat(iso), iso, "測試")


def test_no_file_name_dates():
    # owner 11-03 (addendum L): dates are no longer read from file names (they ordered series, which went), so the
    # module has no reader for them
    assert not hasattr(dates, "extract_filename_dates") and not hasattr(dates, "_FN_RULES")


def test_no_phone_or_docno_dates():
    # a 文號 or a phone number in a notice body is never a date (the file-name half of this test went with
    # extract_filename_dates on 11-03)
    body = ("聯絡電話：07-7172930 轉 2105　傳真：(07)7111234\n發文字號：高師大教字第1151002990號\n"
            "申請時間依本校1151002號簽辦理。")
    assert extract_periods(body) == []


def test_periods():
    got = extract_periods("受理期間：115年11月3日上午8時起至11月16日下午5時止")
    assert [(f.kind, f.date, f.source) for f in got] == [
        ("period_start", date(2026, 11, 3), "正文期間"), ("period_end", date(2026, 11, 16), "正文期間")]
    assert got[0].raw == "115年11月3日上午8時起至11月16日下午5時止"


def test_period_forms():
    def p(text):
        return [(f.kind, f.date) for f in extract_periods(text)]

    # the end names only a day; the lone 8/26 has no keyword-led range, so it is not a period
    assert p("申請時間：選課第三階段(115年8月26日)及加退選期間(115年9月7日至15日)。") == [
        ("period_start", date(2026, 9, 7)), ("period_end", date(2026, 9, 15))]
    # weekday and time between the dates; the end, without a year, falls in the next year
    assert p("報名時間：115年12月16日(三)上午9時起至1月14日(四)止") == [
        ("period_start", date(2026, 12, 16)), ("period_end", date(2027, 1, 14))]
    assert p("報名時間:115年11月16日(上午9時起)至115年12月10日止") == [
        ("period_start", date(2026, 11, 16)), ("period_end", date(2026, 12, 10))]
    # an end naming a day only runs into the next month; a start without a year takes the end's
    assert p("報名期間：115年12月28日至5日") == [("period_start", date(2026, 12, 28)), ("period_end", date(2027, 1, 5))]
    assert p("受理期間：12月28日至116年1月5日") == [("period_start", date(2026, 12, 28)), ("period_end", date(2027, 1, 5))]
    # a year that the PDF text split with a space
    assert p("申請停修(11 3年11月18日至29日)") == [("period_start", date(2024, 11, 18)), ("period_end", date(2024, 11, 29))]
    # a lone 截止 date gives the end only
    assert p("報名資料寄件截止日期：115年01月14日（郵戳為憑）") == [("period_end", date(2026, 1, 14))]
    # no keyword right before the range, no year anywhere, or the end before the start: nothing
    assert p("本校於115年9月7日至9月15日舉辦校慶活動，歡迎報名參加") == []
    assert p("受理期間(11/3~11/16)") == []
    assert p("報名期間：115年9月15日至115年9月7日") == []
    assert p("受理時間：每月15日至115年1月9日止") == []   # a day alone never starts a range
    # the same range written twice gives each date once
    assert p("受理期間：115年11月3日至11月16日。再次提醒，受理期間：115年11月3日至11月16日") == [
        ("period_start", date(2026, 11, 3)), ("period_end", date(2026, 11, 16))]


def test_dates_out_of_range_dropped():
    # the two file-name cases became period cases on 11-03 (no file-name dates any more); the bounds are the same
    assert extract_periods("報名期間：70年1月5日至1月9日") == []   # ROC 70 is 1981, before 1990
    assert extract_periods("報名期間：300年1月5日至1月9日") == []  # ROC 300 is 2211, far past today + 400 days


def test_long_whitespace_runs_stay_fast():
    # PDF text has long runs of spaces and tabs; a pattern with adjacent optional \s* backtracks for minutes on them
    gap = " \t" * 500
    texts = ["某辦法\n111.12.21" + gap + "X\n第一條 本辦法。", "112.05" + gap + "X", "115年3月" + gap + "X",
             "受理期間：115年11月3日" + gap + "X"]
    start = time.perf_counter()
    for text in texts:
        extract_periods(text)
    assert time.perf_counter() - start < 1.0


def test_identity_order():
    # owner 11-01 (later change): the page's own date, else the date inherited from the notice, else the server's
    # file time (the two file-name cases went 11-03 with file-name dates: no build writes that kind)
    assert IDENTITY_ORDER == ("published", "inherited", "server")
    assert identity_date([_f("inherited", "2026-01-05"), _f("published", "2026-01-09")]).kind == "published"
    assert identity_date([_f("server", "2026-02-01"), _f("inherited", "2026-01-05")]).kind == "inherited"
    assert identity_date([_f("period_start", "2026-01-05"), _f("period_end", "2026-02-01")]) is None
    assert identity_date([]) is None


def test_load_server_dates(tmp_path):
    assert load_server_dates(None) == {}
    assert load_server_dates(tmp_path / "missing.json") == {}
    p = tmp_path / "server_dates.json"
    p.write_text(json.dumps({"https://x/a.pdf": "2026-04-07", "https://x/b.pdf": "Tue, 06 Oct"}), encoding="utf-8")
    assert load_server_dates(p) == {"https://x/a.pdf": date(2026, 4, 7)}


U = "https://c.nknu.edu.tw/aca/uploadfile/"


def _warc(path, responses):
    """One request and one response record per (url, status line, Last-Modified or None, WARC-Date)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        w = WARCWriter(fh, gzip=True)
        for url, status, lm, when in responses:
            req = StatusAndHeaders("GET / HTTP/1.1", [("Host", "c.nknu.edu.tw")], is_http_request=True)
            w.write_record(w.create_warc_record(url, "request", payload=io.BytesIO(b""), http_headers=req,
                                                warc_headers_dict={"WARC-Date": when}))
            head = StatusAndHeaders(status, [("Content-Type", "application/pdf")] + ([("Last-Modified", lm)] if lm else []),
                                    protocol="HTTP/1.1")
            w.write_record(w.create_warc_record(url, "response", payload=io.BytesIO(b"%PDF-1.4"), http_headers=head,
                                                warc_headers_dict={"WARC-Date": when}))


def _archive(root):
    # a.pdf was fetched twice; the later fetch sits in the file that sorts first
    _warc(root / "2026/10/27/1.warc.gz", [(U + "a.pdf", "200 OK", "Mon, 26 Oct 2026 02:00:00 GMT", "2026-10-27T08:00:00Z")])
    _warc(root / "2026/10/27/2.warc.gz", [
        (U + "a.pdf", "200 OK", "Tue, 06 Oct 2026 01:00:00 GMT", "2026-10-27T06:21:00Z"),
        (U + "tz.pdf", "200 OK", "Tue, 06 Oct 2026 17:30:00 GMT", "2026-10-27T06:22:00Z"),  # 01:30 the next day in Taipei
        (U + "gone.pdf", "404 Not Found", "Tue, 06 Oct 2026 01:00:00 GMT", "2026-10-27T06:23:00Z"),
        (U + "nolm.pdf", "200 OK", None, "2026-10-27T06:24:00Z"),
        (U + "bad.pdf", "200 OK", "not a date", "2026-10-27T06:25:00Z"),
        (U + "epoch.pdf", "200 OK", "Thu, 01 Jan 1970 00:00:00 GMT", "2026-10-27T06:26:00Z"),
    ])
    # a file cut short while it was written (sorts first): its record before the cut counts and the scan goes on
    cut = root / "2026/10/27/0.warc.gz"
    _warc(cut, [(U + "early.pdf", "200 OK", "Fri, 02 Oct 2026 03:00:00 GMT", "2026-10-27T06:00:00Z")])
    with open(cut, "ab") as fh:
        fh.write(b"WARC/1.1\r\nWARC-Type: response\r\nContent-Length: 900\r\n\r\nHTTP/1.1 200")


WANT = {U + "a.pdf": "2026-10-26", U + "early.pdf": "2026-10-02", U + "tz.pdf": "2026-10-07"}


def test_collect_last_modified(tmp_path):
    _archive(tmp_path / "warc")
    assert collect_last_modified(tmp_path / "warc") == WANT
    assert collect_last_modified(tmp_path / "warc", urls={U + "tz.pdf", U + "other.pdf"}) == {U + "tz.pdf": "2026-10-07"}


def test_serverdates_cli(tmp_path):
    _archive(tmp_path / "warc")
    out = tmp_path / "skeleton" / "server_dates.json"
    assert serverdates.main(["--warc-dir", str(tmp_path / "warc"), "--out", str(out)]) == 0
    assert load_server_dates(out) == {url: date.fromisoformat(day) for url, day in WANT.items()}
    assert serverdates.main(["--warc-dir", str(tmp_path / "nowhere"), "--out", str(out)]) == 2
