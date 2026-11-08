import json
from datetime import date

from nknu.content.corpus import (
    Event,
    PageMeta,
    Record,
    export_info,
    load_calendar,
    load_corpus,
    parse_add_time,
    read_metas,
    read_page_meta,
)

PAGE_URL = "https://c.nknu.edu.tw/aca/newsdetail.aspx?nid=662&gi=2"
PAGE_FILE = "pages/0032-newsdetail-nid-662-gi-2.html"

# Modelled on a real notice page: the meta claims big5, the bytes are UTF-8 with a BOM.
NOTICE_HTML = """<!DOCTYPE html><html><head>
<meta http-equiv="Content-Type" content="text/html; charset=big5" /><title>教務處</title></head><body>
<div class="border_title"><h3>
<span class="border_title_h3">[<span id="lbl_GroupName">和平教務組</span>]</span>
<span id="lbl_Title">115學年度第一學期線上申請停修時間及相關事宜(11/3~11/16受理線上申請)</span></h3>
<h5>發佈人員：<span id="lbl_Editor">和平教務組</span>｜公告日期：<span id="lbl_AddTime">2026/10/21 上午 08:56:00</span></h5>
</div>
<span id="lbl_Contents"><p>主旨：本校115學年度第1學期課程停修相關事宜，請查照。</p></span>
<span id="lbl_FileLink"><a href="UploadFile/News/2026102185559/115-1停修公告.pdf" target="_blank">115-1停修公告.pdf&nbsp;(619775 Kb)</a><br />
<a href="javascript:void(0)">x</a><a href="https://ws.example.edu.tw/a.pdf">a.pdf</a></span>
</body></html>"""


def _manifest(tmp_path, items=(), **extra):
    data = {"网站": "c.nknu.edu.tw/aca", "名称": "教务处", "导出时间": "2026-10-27T09:19:03+08:00", "项目": list(items)}
    data.update(extra)
    (tmp_path / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _notice_export(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / PAGE_FILE).write_bytes(b"\xef\xbb\xbf" + NOTICE_HTML.encode("utf-8"))
    _manifest(tmp_path, [{"类型": "网页", "网址": PAGE_URL, "最后落地网址": "", "本地档案": PAGE_FILE}])
    return tmp_path


def test_load_corpus(tmp_path):
    rows = [
        {"id": "p32", "kind": "newsdetail", "url": PAGE_URL, "file": PAGE_FILE, "title": "停修", "date": "x", "text": "t"},
        {"id": "a2031", "kind": "附件:pdf:有文字层", "url": "https://c.nknu.edu.tw/aca/u/a.pdf", "file": "files/a.pdf",
         "title": "a.pdf", "linktext": "說明 | 手冊", "linked": "https://c.nknu.edu.tw/aca/x | https://c.nknu.edu.tw/aca/y",
         "text": "內容"},
    ]
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    recs = load_corpus(path)
    assert recs[0] == Record("p32", "newsdetail", PAGE_URL, PAGE_FILE, "停修", "t", "", ())
    assert recs[1].linked == ("https://c.nknu.edu.tw/aca/x", "https://c.nknu.edu.tw/aca/y")
    assert recs[1].linktext == "說明 | 手冊"


def test_load_corpus_reads_the_assembled_fields(tmp_path):
    # T7a's corpus.json: where each text came from, and the pages linking an attachment with their zone, the title
    # they give it and the row it sits on; format siblings folded into a record
    n1, n2, n3 = (f"https://c.nknu.edu.tw/aca/newsdetail.aspx?nid={i}" for i in (1, 2, 3))
    rows = [{"id": "a7m1", "kind": "附件:pdf:有文字层", "url": "https://c.nknu.edu.tw/aca/m.zip#a.pdf",
             "file": "files/0002-m.zip", "title": "a.pdf", "text": "t", "linktext": "會議資料.zip",
             "linked": f"{n1} | {n2}", "nav_linked": n3, "source": "totext", "totext_status": "ok",
             "zones": {n1: "content", n2: "unknown", n3: "nav"}, "contexts": {n1: "會議資料"},
             "rows": {n1: n1 + "#/html[1]/body[1]/p[1]"},
             "formats": [{"id": "a8", "url": "https://c.nknu.edu.tw/aca/a.doc", "file": "files/0003-a.doc",
                          "format": "doc", "kind": "附件:office"}]}]
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    (r,) = load_corpus(path)
    assert (r.linked, r.nav_linked, r.source) == ((n1, n2), (n3,), "totext")
    assert (r.zones, r.contexts, r.rows) == (rows[0]["zones"], {n1: "會議資料"}, rows[0]["rows"])
    assert [f["format"] for f in r.formats] == ["doc"]
    assert len({r, r}) == 1  # still hashable: the dict fields stay out of the hash


def test_parse_add_time():
    assert parse_add_time("2026/10/22 上午 10:49:00") == date(2026, 10, 22)
    assert parse_add_time("2026/4/24 下午 02:34:22") == date(2026, 4, 24)
    assert parse_add_time("") is None
    assert parse_add_time("公告日期") is None
    assert parse_add_time("2026/13/40 上午 10:49:00") is None
    assert parse_add_time("1980/1/1 上午 10:49:00") is None  # outside 1990-01-01 .. today+400


def test_read_page_meta(tmp_path):
    meta = read_page_meta(_notice_export(tmp_path), PAGE_FILE)
    assert meta == PageMeta(
        group="和平教務組",
        title="115學年度第一學期線上申請停修時間及相關事宜(11/3~11/16受理線上申請)",
        added_raw="2026/10/21 上午 08:56:00",
        added=date(2026, 10, 21),
        attachment_urls=(
            "https://c.nknu.edu.tw/aca/UploadFile/News/2026102185559/115-1停修公告.pdf",
            "https://ws.example.edu.tw/a.pdf",
        ),
    )


def test_read_page_meta_unlisted_page_keeps_hrefs(tmp_path):
    _notice_export(tmp_path)
    (tmp_path / "pages" / "other.html").write_bytes(NOTICE_HTML.encode("utf-8"))
    meta = read_page_meta(tmp_path, "pages/other.html")
    assert meta.attachment_urls[0] == "UploadFile/News/2026102185559/115-1停修公告.pdf"  # no URL to resolve against


def test_read_page_meta_decodes_strictly(tmp_path):
    _notice_export(tmp_path)
    (tmp_path / "pages" / "big5.html").write_bytes(NOTICE_HTML.encode("big5", errors="replace"))
    try:
        read_page_meta(tmp_path, "pages/big5.html")
    except UnicodeDecodeError:
        return
    raise AssertionError("a page that is not UTF-8 must not be read with replacement characters")


def test_read_page_meta_not_a_notice(tmp_path):
    _notice_export(tmp_path)
    (tmp_path / "pages" / "plain.html").write_bytes("<html><body><p>聯絡我們</p></body></html>".encode())
    assert read_page_meta(tmp_path, "pages/plain.html") is None
    assert read_page_meta(tmp_path, "pages/missing.html") is None


def test_read_metas_keyed_by_record_id(tmp_path):
    _notice_export(tmp_path)
    recs = [Record("p32", "newsdetail", PAGE_URL, PAGE_FILE, "停修", "", "", ()),
            Record("p19", "page_single", "https://c.nknu.edu.tw/aca/page_single.aspx?tid=4", PAGE_FILE, "聯絡", "", "", ())]
    metas = read_metas(tmp_path, recs)
    assert list(metas) == ["p32"]
    assert metas["p32"].group == "和平教務組"


def _calendar(tmp_path, items):
    (tmp_path / "data").mkdir()
    body = {"kind": "calendar#events", "timeZone": "Asia/Taipei", "items": items}
    (tmp_path / "data" / "0001-Google 行事历 nknubf@gmail.com 的事件.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8")


def test_calendar_end_exclusive(tmp_path):
    link = "https://www.google.com/calendar/event?eid=abc"
    _calendar(tmp_path, [
        {"id": "e1", "status": "confirmed", "summary": "申請停修", "htmlLink": link,
         "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-17"}},
        {"id": "e2", "status": "confirmed", "summary": "放假一天", "htmlLink": link,
         "start": {"date": "2026-10-24"}, "end": {"date": "2026-10-25"}},
        {"id": "e3", "status": "confirmed", "summary": "說明會", "htmlLink": link,
         "start": {"dateTime": "2026-11-05T10:00:00+08:00"}, "end": {"dateTime": "2026-11-05T12:00:00+08:00"}},
        {"id": "e4", "status": "confirmed", "summary": "跨夜", "htmlLink": link,  # 01:00-03:00 in Taipei the next day
         "start": {"dateTime": "2026-11-05T17:00:00Z"}, "end": {"dateTime": "2026-11-05T19:00:00Z"}},
        {"id": "e5", "status": "cancelled", "summary": "取消的", "htmlLink": link,
         "start": {"date": "2026-11-01"}, "end": {"date": "2026-11-02"}},
    ])
    events = load_calendar(tmp_path)
    assert events == [
        Event("e1", "申請停修", date(2026, 11, 3), date(2026, 11, 16), link),
        Event("e2", "放假一天", date(2026, 10, 24), date(2026, 10, 24), link),
        Event("e3", "說明會", date(2026, 11, 5), date(2026, 11, 5), link),
        Event("e4", "跨夜", date(2026, 11, 6), date(2026, 11, 6), link),
    ]


def test_calendar_keeps_staff_entries(tmp_path):
    # owner 11-01: no staff-schedule filter (fix round 3's is gone); a staff member's leave or meeting is read like any
    # other entry. Titles as the calendar writes them, names masked.
    link = "https://www.google.com/calendar/event?eid=abc"
    _calendar(tmp_path, [
        {"id": "s1", "status": "confirmed", "summary": "盈O，休假", "htmlLink": link,
         "start": {"date": "2024-05-10"}, "end": {"date": "2024-05-11"}},
        {"id": "s2", "status": "confirmed", "summary": "總量會議-6F3", "htmlLink": link,
         "start": {"dateTime": "2024-05-13T10:00:00+08:00"}, "end": {"dateTime": "2024-05-13T12:00:00+08:00"}},
    ])
    assert load_calendar(tmp_path) == [Event("s1", "盈O，休假", date(2024, 5, 10), date(2024, 5, 10), link),
                                       Event("s2", "總量會議-6F3", date(2024, 5, 13), date(2024, 5, 13), link)]


def test_calendar_missing_is_empty(tmp_path):
    assert load_calendar(tmp_path) == []


def test_export_info(tmp_path):
    _manifest(tmp_path)
    header = "序号,类型,网址,本地档案,分类\n"
    rows = ("1,网页,https://a/1,,连接失败\n2,附件,https://a/2.pdf,,要人看\n3,附件,https://a/3.pdf,,连接失败\n"
            "4,图片,https://a/4.jpg,,规则不抓\n")
    (tmp_path / "missing.csv").write_text("\ufeff" + header + rows, encoding="utf-8")
    assert export_info(tmp_path) == {
        "site": "c.nknu.edu.tw/aca", "name": "教務處", "data_as_of": "2026-10-27",
        "missing_pages": 1, "missing_files": 2,
    }


def test_data_as_of_is_the_last_fetch_not_the_export(tmp_path):
    # the data is as new as the site's last crawl: the export of 11-02 re-read a crawl that ended on 10-27; files
    # fetched one by one later (the 11-02 off-site attachments, nknu archive run --only) do not re-read the site's
    # pages, so they do not move the date (phase-2 review)
    items = [{"类型": "网页", "网址": "https://a/1", "本地档案": "pages/1.html",
              "抓取时间": "2026-10-27T09:02:03+08:00"},
             {"类型": "附件", "网址": "https://a/2.pdf", "本地档案": "files/2.pdf",
              "抓取时间": "2026-10-27T05:00:00+08:00"},
             {"类型": "附件", "网址": "https://a/3.pdf", "本地档案": "", "抓取时间": "2026-10-31T05:00:00+08:00"},
             {"类型": "图片", "网址": "https://a/4.jpg", "本地档案": "images/4.jpg", "抓取时间": "bad"},
             {"类型": "附件", "网址": "https://b/5.doc", "本地档案": "files/5.doc",
              "抓取时间": "2026-11-02T19:34:23+08:00"},
             {"类型": "网页", "网址": "https://a/6", "本地档案": "", "抓取时间": "2026-11-01T08:00:00+08:00"}]
    _manifest(tmp_path, items, 导出时间="2026-11-02T10:00:00+08:00")
    assert export_info(tmp_path)["data_as_of"] == "2026-10-27"  # the rows without a file were never fetched whole


def test_data_as_of_without_pages_takes_the_files_then_the_export(tmp_path):
    items = [{"类型": "附件", "网址": "https://a/2.pdf", "本地档案": "files/2.pdf",
              "抓取时间": "2026-10-28T05:00:00+08:00"},
             {"类型": "网页", "网址": "https://a/1", "本地档案": "", "抓取时间": "2026-10-30T09:00:00+08:00"}]
    files, nothing = tmp_path / "files", tmp_path / "nothing"  # one folder each: corpus caches a manifest by its path
    for folder, rows in ((files, items), (nothing, items[1:])):
        folder.mkdir()
        _manifest(folder, rows, 导出时间="2026-11-02T10:00:00+08:00")
    assert export_info(files)["data_as_of"] == "2026-10-28"  # no page fetched whole: the files' last fetch
    assert export_info(nothing)["data_as_of"] == "2026-11-02"  # nothing fetched whole: the export day


def test_data_as_of_is_a_taipei_date(tmp_path):
    items = [{"类型": "网页", "网址": "https://a/1", "本地档案": "pages/1.html", "抓取时间": "2026-10-26T17:30:00Z"}]
    _manifest(tmp_path, items, 导出时间="2026-11-02T10:00:00+08:00")
    assert export_info(tmp_path)["data_as_of"] == "2026-10-27"  # 01:30 in Taipei
