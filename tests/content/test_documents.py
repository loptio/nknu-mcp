from datetime import date

from nknu.content.corpus import Event, PageMeta, Record
from nknu.content.documents import EXCLUDED_KINDS, MENU_IMAGE, Document, build_documents, clean_text

ACA = "https://c.nknu.edu.tw/aca/"


def rec(id, kind, url, title="", text="", linktext="", linked=()):
    return Record(id, kind, url, f"pages/{id}.html", title, text, linktext, tuple(linked))


def notice(id, nid, gi, title, text="主旨：請查照。", amp=False):
    sep = "&amp;" if amp else "&"
    return rec(id, "newsdetail", f"{ACA}newsdetail.aspx?nid={nid}{sep}gi={gi}", title, text)


def meta(group, title, added=None):
    raw = f"{added.year}/{added.month}/{added.day} 上午 08:56:00" if added else ""
    return PageMeta(group, title, raw, added, ())


def by_id(docs):
    return {d.doc_id: d for d in docs}


def test_excluded_kinds_counted():
    assert EXCLUDED_KINDS == frozenset({"newslist", "newslist#翻页/按钮", "album", "album#翻页/按钮", "album_images",
                                        "sitemap", "linkslist"})
    records = [rec(f"p{i}", kind, f"{ACA}list{i}.aspx", "列表", "一則公告") for i, kind in enumerate(sorted(EXCLUDED_KINDS))]
    records += [
        notice("p32", 662, 2, "115學年度第一學期線上申請停修"),
        rec("p19", "page_single", f"{ACA}page_single.aspx?tid=4", "聯絡我們", "教務長室：(07)717-2930"),
        rec("p57", "外站:reurl.cc", "https://reurl.cc/52Lly6", "交通及住宿", "和平與燕巢校區交通住宿建議說明"),
        rec("p24", "member", f"{ACA}member.aspx?pn=8&pclass=A1002&gi=1", "教務長室 - 編制與業務", "業務"),
    ]
    docs, excluded = build_documents(records, {}, [])
    assert [(d.doc_id, d.kind_class) for d in docs] == [
        ("dp32", "notice"), ("dp19", "page"), ("dp57", "link"), ("dp24", "page")]
    assert excluded == {kind: 1 for kind in EXCLUDED_KINDS}


def test_notice_fields_from_meta():
    records = [notice("p32", 662, 2, "115學年度第一學期線上申請停修", "115學年度第一學期線上申請停修\n主旨：請查照。")]
    metas = {"p32": meta("和平教務組", "115學年度第一學期線上申請停修", date(2026, 10, 21))}
    (doc,), excluded = build_documents(records, metas, [])
    assert excluded == {}
    # plan 2026-11-08 T3 (deliberate): the unit is the site table's for the notice's own site (教務處's), and the date
    # says where it was read (the notice's date field; another platform's detail page has the export's)
    assert doc == Document(
        doc_id="dp32", kind_class="notice", kind_raw="newsdetail", title="115學年度第一學期線上申請停修",
        url=f"{ACA}newsdetail.aspx?nid=662&gi=2", unit="教務處", grp="和平教務組", parent_id=None, parent_title="",
        linktext="", has_text=True, text="115學年度第一學期線上申請停修\n主旨：請查照。",
        added=date(2026, 10, 21), added_raw="2026/10/21 上午 08:56:00", added_source="公告頁日期欄",
    )


def test_parent_latest_notice():
    # changed on purpose (addendum B, T7a): the latest referencing page by its date is the parent and gives the date
    # (11-01: an attachment takes its page's date; with several pages, the latest); it was the earliest notice
    home = rec("p1", "default", f"{ACA}default.aspx", "教務處首頁", "最新消息")
    later = notice("p50", 50, 4, "另一則公告")
    earlier = notice("p40", 40, 2, "115學年度第一學期停修")
    att = rec("a1", "附件:pdf:有文字层", f"{ACA}uploadfile/news/1/a.pdf", "a.pdf", "內容", "a.pdf",
              linked=(later.url, home.url, earlier.url))
    page_only = rec("a2", "附件:pdf:有文字层", f"{ACA}uploadfile/userfiles/b.pdf", "b.pdf", "內容", "b",
                    linked=(home.url,))
    metas = {"p40": meta("和平教務組", earlier.title, date(2026, 10, 21)),
             "p50": meta("招生企劃組", later.title, date(2026, 10, 22))}
    docs = by_id(build_documents([home, later, earlier, att, page_only], metas, [])[0])
    a1 = docs["da1"]
    assert (a1.parent_id, a1.parent_title, a1.grp) == ("dp50", "另一則公告", "招生企劃組")
    assert a1.linked_from == ("dp1", "dp50", "dp40")  # corpus order
    assert (a1.kind_class, a1.linktext) == ("attachment", "a.pdf")
    a2 = docs["da2"]
    assert (a2.parent_id, a2.parent_title, a2.linked_from) == ("dp1", "教務處首頁", ("dp1",))


def test_parent_without_added_date_comes_last():
    dated = notice("p40", 40, 2, "有日期")
    undated = notice("p39", 39, 2, "沒日期")
    att = rec("a1", "附件:pdf:有文字层", f"{ACA}uploadfile/news/1/a.pdf", "a.pdf", "內容", linked=(undated.url, dated.url))
    metas = {"p40": meta("和平教務組", "有日期", date(2026, 10, 21)), "p39": meta("和平教務組", "沒日期")}
    assert by_id(build_documents([undated, dated, att], metas, [])[0])["da1"].parent_id == "dp40"


FORMS = f"{ACA}filesdownload.aspx?pn=111&pclass=A2004&gi=2"


def att(id, linked=(), name="u112.pdf", linktext="pdf", **fields):
    return Record(id, "附件:pdf:有文字层", f"{ACA}uploadfile/news/{id}/{name}", f"files/{id}.pdf", name, "內容",
                  linktext, tuple(linked), **fields)


def test_title_from_the_latest_page_that_names_the_file():
    # addendum B: the context its latest-dated referencing page gives; the file name stays in the link text
    old, new = notice("p40", 40, 2, "110學年度停修"), notice("p50", 50, 2, "114學年度停修")
    page = rec("p15", "filesdownload", FORMS, "表單下載", "表單")
    a = att("a1", (old.url, new.url, page.url), contexts={old.url: "舊表", new.url: "停修申請表", page.url: "表單名"})
    metas = {"p40": meta("和平教務組", old.title, date(2021, 3, 1)),
             "p50": meta("和平教務組", new.title, date(2025, 9, 1))}
    a1 = by_id(build_documents([old, new, page, a], metas, [])[0])["da1"]
    assert (a1.title, a1.file_title, a1.title_from_context, a1.linktext) == ("停修申請表", "u112.pdf", True,
                                                                             "pdf | u112.pdf")
    assert (a1.parent_id, a1.parent_title) == ("dp50", "114學年度停修")


def test_context_title_ties_take_the_most_frequent():
    pages = [rec(f"p{i}", "filesdownload", f"{FORMS}&x={i}", "表單下載", "表單") for i in (15, 16, 17)]
    a = att("a1", [p.url for p in pages], name="甲表.pdf", linktext="甲表.pdf",
            contexts={pages[0].url: "表A", pages[1].url: "表B", pages[2].url: "表B"})
    a1 = by_id(build_documents([*pages, a], {}, [])[0])["da1"]
    assert (a1.title, a1.linktext) == ("表B", "甲表.pdf")  # the file name is in the link text already
    plain = by_id(build_documents([*pages, att("a2", [pages[0].url])], {}, [])[0])["da2"]
    assert (plain.title, plain.file_title, plain.title_from_context) == ("u112.pdf", "", False)


def test_menu_pages_are_references_not_parents():
    n = notice("p40", 40, 2, "公告")
    a = att("a1", (), name="rule.pdf", nav_linked=(n.url,), zones={n.url: "nav"}, contexts={n.url: "借教室要點"})
    a1 = by_id(build_documents([n, a], {"p40": meta("和平教務組", "公告", date(2026, 1, 5))}, [])[0])["da1"]
    assert (a1.parent_id, a1.parent_title, a1.title) == (None, "", "rule.pdf")  # a menu's word is no title either
    assert a1.refs == (("dp40", "nav", "借教室要點"),)
    assert a1.grp == "和平教務組"  # a standing document of the unit whose menus link it


def test_an_image_only_menus_carry_is_left_out_and_counted():
    # I4 (main session, 11-02): assemble gives an image every page carries only outside its content area (a unit
    # banner) the kind MENU_IMAGE: no document, counted in excluded; a file linked only from a menu stays a document
    n = notice("p40", 40, 2, "公告")
    banner = Record("i7", MENU_IMAGE, f"{ACA}uploadfile/mainimg/m1.jpg", "images/i7.jpg", "m1.jpg",
                    "教務處 國立高雄師範大學", "", (), nav_linked=(n.url,), zones={n.url: "nav"})
    rule = att("a1", (), name="rule.pdf", nav_linked=(n.url,), zones={n.url: "nav"})
    docs, excluded = build_documents([n, banner, rule], {}, [])
    assert MENU_IMAGE == "只在选单出现的图片" and MENU_IMAGE not in EXCLUDED_KINDS  # pages only there
    assert [d.doc_id for d in docs] == ["dp40", "da1"] and excluded == {MENU_IMAGE: 1}
    assert by_id(docs)["da1"].refs == (("dp40", "nav", ""),)


def test_refs_list_every_page_document_with_its_zone_and_context():
    body, files, menu = notice("p40", 40, 2, "甲"), notice("p41", 41, 2, "乙"), notice("p42", 42, 2, "丙")
    album = rec("p126", "album_images", f"{ACA}album_images.aspx?aid=137&gi=4", "相簿", "x")
    a = att("a1", (body.url, files.url, album.url), nav_linked=(menu.url,),
            zones={body.url: "content", files.url: "attachments", album.url: "content", menu.url: "nav"},
            contexts={body.url: "停修申請表", menu.url: "表單"})
    docs, _ = build_documents([body, files, menu, album, a], {}, [])
    assert by_id(docs)["da1"].refs == (("dp40", "content", "停修申請表"), ("dp41", "attachments", ""),
                                       ("dp42", "nav", "表單"))  # the album is no document
    legacy = by_id(build_documents([body, att("a2", (body.url,))], {}, [])[0])["da2"]
    assert legacy.refs == (("dp40", "unknown", ""),)  # the 10-29 corpus names no zone


def test_formats_and_source():
    page = rec("p15", "filesdownload", FORMS, "表單下載", "表單")
    formats = ({"id": "a2", "url": f"{ACA}f/w.doc", "file": "files/a2.doc", "format": "doc", "kind": "附件:office"},
               {"id": "a3", "url": f"{ACA}f/w.odt", "file": "files/a3.odt", "format": "odt", "kind": "附件:office"})
    docs, excluded = build_documents([page, att("a1", (page.url,), source="totext", formats=formats)], {}, [])
    assert excluded == {"附件:office(其他格式)": 2}  # folded into a1 by assemble: counted, listed as its copies
    a1, page_doc = by_id(docs)["da1"], by_id(docs)["dp15"]
    assert (a1.formats, a1.source, page_doc.source) == (formats, "totext", "")


def test_no_text_attachment():
    page = rec("p15", "filesdownload", f"{ACA}filesdownload.aspx?pn=111&pclass=A2004&gi=2", "表單下載", "類別")
    cases = [("a1", "附件:pdf:无文字层", "筆試試場位置：研究大樓 1F\n\x0c", False),
             ("a2", "附件:无文字(zip)", "", False),
             ("a3", "附件:无文字(ooxml:xlsx)", "", False),
             ("a4", "附件:pdf:有文字层", "第一條 本辦法依大學法訂定。", True),
             ("a5", "附件:pdf:有文字层", " \n\x0c \xa0", False),
             # mostly scanned: the few words are not the document (review 11-01: shown as an empty-looking 全文)
             ("a6", "附件:pdf:文字很少(多半是扫描+少量字)", "登入說明", False)]
    records = [page] + [rec(i, kind, f"{ACA}uploadfile/files/{i}.pdf", f"{i}.pdf", text, linked=(page.url,))
                        for i, kind, text, _ in cases]
    docs = by_id(build_documents(records, {}, [])[0])
    assert {i: docs[f"d{i}"].has_text for i, *_ in cases} == {i: want for i, _, _, want in cases}
    assert docs["da1"].text == "筆試試場位置：研究大樓 1F"  # kept (indexed, shown as a few words), never as 全文
    assert docs["da6"].text == "登入說明"


def test_link_text_file_size_dropped():
    # fix round 2 (11-01): the site writes the file size after each attachment link, 「115-1停修公告.pdf (619775 Kb)」;
    # the size became words of the title column (619775, kb). Only a size at the very end is dropped.
    page = notice("p32", 662, 2, "115學年度第一學期線上申請停修")
    cases = [("a1", "115-1停修公告.pdf\xa0(619775 Kb)", "115-1停修公告.pdf"),
             ("a2", "交通車時刻表(115_9_5起實施).pdf\xa0(152.5390625 Kb)", "交通車時刻表(115_9_5起實施).pdf"),
             ("a3", "選課說明(更新)", "選課說明(更新)"),  # brackets that hold no size stay
             ("a4", "大小 (12 Kb) 寫在中間", "大小 (12 Kb) 寫在中間"),  # not at the end: left as written
             ("a5", "(2048 Kb)", ""),
             # the export joins the texts of every link to one file with 「 | 」: each one ends in its size
             ("a6", "郵寄切結書.pdf\xa0(152780 Kb) | 郵寄切結書.pdf\xa0(126229 Kb)", "郵寄切結書.pdf | 郵寄切結書.pdf")]
    records = [page] + [rec(i, "附件:pdf:有文字层", f"{ACA}uploadfile/news/{i}.pdf", f"{i}.pdf", "內容", lt,
                            linked=(page.url,)) for i, lt, _ in cases]
    docs = by_id(build_documents(records, {}, [])[0])
    assert {i: docs[f"d{i}"].linktext for i, *_ in cases} == {i: want for i, _, want in cases}


def test_notices_deduped_by_nid():
    escaped = notice("p330", 606, 4, "115學年度學士班暑假轉學招生", amp=True)
    clean = notice("p64", 606, 4, "115學年度學士班暑假轉學招生")
    att = rec("a1", "附件:pdf:有文字层", f"{ACA}uploadfile/news/2/u115.pdf", "u115.pdf", "簡章", linked=(escaped.url,))
    docs, excluded = build_documents([escaped, clean, att], {}, [])
    assert [d.doc_id for d in docs] == ["dp64", "da1"]  # the URL without &amp; is kept
    assert excluded == {"newsdetail(重复)": 1}
    assert docs[1].parent_id == "dp64"


def test_blank_records_dropped_and_counted():
    empty = notice("p723", 303, 6, "", "\n\n")
    spaper = rec("p900", "静态:spaper", f"{ACA}uploadfile/userfiles/files/spaper/97/02.html", " ", "\n\t\n")
    docs, excluded = build_documents([empty, spaper], {"p723": meta("", "")}, [])
    assert docs == []
    assert excluded == {"newsdetail(空白)": 1, "静态:spaper(空白)": 1}


def test_gi_group_map():
    records = [
        notice("p40", 40, 2, "和平公告"),
        notice("p41", 41, 6, "創新公告"),
        notice("p42", 42, 6, "沒有組別的公告"),
        notice("p43", 43, 6, "也沒有組別"),
        rec("p12", "page", f"{ACA}page.aspx?pn=28&pclass=A2005&gi=2", "行事曆", "訊息"),
        rec("p9", "page", f"{ACA}page.aspx?pn=188&pclass=A6006&gi=6", "系所品保認可", "最新消息"),
        rec("p20", "default", f"{ACA}default.aspx?gi=1", "首頁", "最新消息"),
        rec("p19", "page_single", f"{ACA}page_single.aspx?tid=4", "聯絡我們", "分機"),
    ]
    metas = {"p40": meta("和平教務組", "和平公告"), "p41": meta("教務創新組", "創新公告"),
             "p42": meta("", "沒有組別的公告"), "p43": meta("", "也沒有組別")}
    docs = by_id(build_documents(records, metas, [])[0])
    assert {k: docs[k].grp for k in ("dp12", "dp9", "dp20", "dp19")} == {
        "dp12": "和平教務組", "dp9": "教務創新組", "dp20": "", "dp19": ""}


def test_attachment_on_album_only():
    album = rec("p126", "album_images", f"{ACA}album_images.aspx?aid=137&gi=4", "2026/2/10｜申請入學備審資料準備指引", "x")
    records = [album, notice("p40", 40, 4, "招生公告"),
               rec("a1", "附件:pdf:有文字层", f"{ACA}uploadfile/userfiles/4/a.pdf", "112年各系書審準備指引重點彙整.pdf",
                   "內容", "112 學年度", linked=(album.url,))]
    docs, excluded = build_documents(records, {"p40": meta("招生企劃組", "招生公告")}, [])
    a1 = by_id(docs)["da1"]
    assert (a1.parent_id, a1.linked_from) == (None, ())
    assert a1.parent_title == "2026/2/10｜申請入學備審資料準備指引"  # the list page's title still names its context
    assert a1.grp == "招生企劃組"  # from the list page's gi
    assert excluded == {"album_images": 1}


def test_events_become_documents():
    link = "https://www.google.com/calendar/event?eid=abc"
    events = [Event("abc", "期中考試", date(2026, 10, 27), date(2026, 11, 2), link),
              Event("x", "", date(2026, 9, 21), date(2026, 9, 21), link)]
    # plan 2026-11-08 T3 (deliberate): an event takes its export's site's unit (it was 教務處 for every event); these,
    # made by hand, name no site of their own, so the export's is given
    docs, excluded = build_documents([], {}, events, site="c.nknu.edu.tw/aca")
    # plan 2026-11-08 T4 (deliberate): a document names the site of the export it came from (docs.site)
    assert docs == [Document("eabc", "event", "行事历", "期中考試", link, "教務處", "", None, "", "", True, "期中考試",
                             site="c.nknu.edu.tw/aca")]
    assert excluded == {"行事历(空白)": 1}


def test_events_before_2023_left_out_and_counted():
    # owner 11-01: only the calendar from 2023-01-01 on, a fixed day that does not roll. An event that starts earlier
    # is no document, blank or not and whenever it ends; it is counted like any left-out record (the export keeps it)
    link = "https://www.google.com/calendar/event?eid=abc"
    events = [Event("a", "寒假開始", date(2022, 12, 31), date(2023, 1, 8), link),
              Event("b", "開國紀念日", date(2023, 1, 1), date(2023, 1, 1), link),
              Event("c", "", date(2017, 9, 21), date(2017, 9, 21), link),
              Event("d", "學測", date(2017, 11, 10), date(2017, 11, 10), link),
              Event("e", " ", date(2026, 9, 21), date(2026, 9, 21), link)]
    docs, excluded = build_documents([], {}, events)
    assert [d.doc_id for d in docs] == ["eb"]
    assert excluded == {"行事历(2023年以前)": 3, "行事历(空白)": 1}


def test_clean_text():
    assert clean_text("a \t b\r\n\n\n\n\xa0\u3000\nc\x0cd\u200b  ") == "a b\n\nc\nd"
    assert clean_text("111.12.21 111學年度") == "111.12.21 111學年度"
    assert clean_text(" \n\t ") == ""


def test_texts_and_titles_are_cleaned():
    records = [rec("p12", "page", f"{ACA}page.aspx?pn=28&gi=2", "行事曆(School Calendar)  -  行事曆",
                   "行事曆\n\n\t\t\n\n\t\t\t\n訊 息\n\n\t\t\t\n")]
    (doc,), _ = build_documents(records, {}, [])
    assert (doc.title, doc.text) == ("行事曆(School Calendar) - 行事曆", "行事曆\n\n訊 息")
