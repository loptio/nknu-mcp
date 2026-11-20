"""11-19 (the owner's three decisions of 11-18, relayed by 爬虫judge): two rules of the site list.

- A skip path may name how the query starts (`/page.aspx?name=start`): the address is left out only when its query
  starts so, any case. SSO's previewpdf.aspx serves the regulations (followed) and the teachers' works, the papers and
  books their vita pages link, by one parameter whose value starts \\Regulations\\ or \\NknuTeacher\\: the works are
  not fetched (decision 1).
- A site may say its rows of a kind past its own cap are not fetched (cap_out): out:cap_<kind>, a rule, where another
  site's are held for a person. 产业教育中心's Google Sites pages carry thousands of activity photographs past its
  picture cap (decision 3).

Made-up addresses and words."""

from __future__ import annotations

import pytest

from nknu.archive import export, round1
from tests.archive.plans import plan as make_plan
from tests.archive.plans import site
from tests.archive.test_crawler_round1 import HOME, QC, crawl, drive, page, seed

WORKS = "https://sso.nknu.edu.tw/previewpdf.aspx?cpathandfilename=%5CNknuTeacher%5C2901%5CC_Book1%5C2024_paper.pdf"
RULES = "https://sso.nknu.edu.tw/previewpdf.aspx?cpathandfilename=%5CRegulations%5C115000%5COOO%E8%A6%81%E9%BB%9E.pdf"
VITA = "https://sso.nknu.edu.tw/dataopen2search/vita.aspx?unid=1"
IEC = "sites.google.com/iec-nknu.page/iec"


@pytest.fixture(scope="module")
def plan():
    return round1.load()


def _site(**more) -> round1.Site:
    return round1.parse_site({"site": "x.nknu.edu.tw", "round": 1, "imp": "本系重要", "home": "https://x.nknu.edu.tw/",
                              **more})


def test_a_skip_path_may_name_how_the_query_starts():
    s = _site(skip=["/Show.aspx?File=%5CTeacher%5C"])
    assert s.skips("https://x.nknu.edu.tw/show.aspx?file=%5CTeacher%5C12%5Ca.pdf")
    assert s.skips("https://x.nknu.edu.tw/show.aspx?file=%5cteacher%5c12%5Ca.pdf")  # any case, as its path
    assert not s.skips("https://x.nknu.edu.tw/show.aspx?file=%5CRules%5C1%5Cb.pdf")  # the page's other files
    assert not s.skips("https://x.nknu.edu.tw/show.aspx")  # the page itself
    assert not s.skips("https://x.nknu.edu.tw/other.aspx?file=%5CTeacher%5C12%5Ca.pdf")  # another page


def test_a_skip_path_ending_in_a_question_mark_still_means_any_query():
    s = _site(skip=["/a/table.aspx?"])
    assert s.skips("https://x.nknu.edu.tw/a/table.aspx?c=1") and not s.skips("https://x.nknu.edu.tw/a/table.aspx")


def test_the_teachers_works_are_left_and_the_regulations_followed(plan):
    root = plan.site("sso.nknu.edu.tw")
    row = lambda url, owner: {"url": url, "owner_site": owner, "tier": 0, "id": 1}
    vita = row(VITA, "sso.nknu.edu.tw/dataopen2search")
    got = round1.verdict(plan, WORKS, vita, "link")
    assert (got.verdict, got.reason, got.state) == ("out", "out:skip_path", None)
    assert round1.verdict(plan, WORKS, None, "link").reason == "out:skip_path"  # wherever the link stands
    rules = round1.verdict(plan, RULES, row("https://sso.nknu.edu.tw/regulations/default.aspx",
                                            "sso.nknu.edu.tw/regulations"), "link")
    assert rules.state == "pending" and rules.reason is None
    assert round1.verdict(plan, RULES, vita, "link").state == "pending"
    assert not root.skips("https://sso.nknu.edu.tw/previewpdf.aspx")


def test_a_site_may_say_its_rows_past_a_cap_are_not_fetched():
    s = round1.parse_site({"site": "x.example.org", "round": 2, "why": "offsite_unit", "home": "https://x.example.org/",
                           "now": "2026-11-17", "caps": {"page": 3, "file": 3, "image": 1}, "cap_out": ["image"]})
    assert s.cap_out == ("image",)
    assert _site().cap_out == ()
    for bad in ("image", ["picture"], [3], ["image", "image"]):
        with pytest.raises(ValueError):
            _site(cap_out=bad)


def test_the_centre_s_pictures_past_its_cap_are_not_fetched(plan):
    assert plan.site(IEC).cap_out == ("image",)
    assert [s.site for s in plan.sites.values() if s.cap_out] == [IEC]  # this site alone


def test_a_picture_past_such_a_site_s_cap_is_left_by_rule_and_another_site_s_is_held(tmp_path):
    html = '<p>活動成果</p><img src="/aca/uploadfile/1.jpg"><img src="/aca/uploadfile/2.jpg"><img src="/aca/uploadfile/3.jpg">'
    aca = site("c.nknu.edu.tw/aca", imp="全校重要", home=HOME, caps=(9, 9, 1), cap_out=("image",))
    crawler, net, _ = crawl(tmp_path / "out", {"/aca/default.aspx": page(html)}, plan=make_plan(aca))
    seed(crawler, HOME)
    drive(crawler, net, QC)
    got = sorted((r["state"], r["reason"]) for r in crawler.frontier.q("SELECT state, reason FROM url WHERE kind='image'"))
    assert [g for g in got if g[0] == "skipped"] == [("skipped", "out:cap_image")] * 2
    assert not crawler.frontier.q("SELECT 1 FROM url WHERE state='held'")
    assert len(got) == 3
    held = site("c.nknu.edu.tw/aca", imp="全校重要", home=HOME, caps=(9, 9, 1))  # as before: held for a person
    crawler, net, _ = crawl(tmp_path / "held", {"/aca/default.aspx": page(html)}, plan=make_plan(held))
    seed(crawler, HOME)
    drive(crawler, net, QC)
    assert dict(crawler.frontier.q("SELECT reason, count(*) FROM url WHERE state='held' GROUP BY reason")) == {
        "cap:image": 2}


def test_a_run_s_start_lets_go_of_no_row_such_a_rule_left(tmp_path):
    html = '<p>活動成果</p><img src="/aca/uploadfile/1.jpg"><img src="/aca/uploadfile/2.jpg">'
    aca = site("c.nknu.edu.tw/aca", imp="全校重要", home=HOME, caps=(9, 9, 1), cap_out=("image",))
    crawler, net, _ = crawl(tmp_path, {"/aca/default.aspx": page(html)}, plan=make_plan(aca))
    seed(crawler, HOME)
    drive(crawler, net, QC)
    crawler.start_run_caps()
    assert crawler._release_caps() == 0
    assert crawler.frontier.q("SELECT count(*) FROM url WHERE reason='out:cap_image' AND state='skipped'")[0][0] == 1


def test_the_export_says_both_rules_in_words():
    assert "上限" in export.RULE_WHY["cap_image"] and "不抓" in export.RULE_WHY["cap_image"]
    assert "著作" in export.RULE_WHY["skip_path"]


def test_a_runtime_picture_the_cap_rule_left_keeps_its_row_when_its_page_is_read_again(tmp_path):
    """A Google Sites page writes each picture a new address at every reading. One the site's cap rule left is not
    one more row a week (the revisit reads its page again), and its new address is never asked for."""
    from tests.archive import test_gsites_images_1027 as gs
    from tests.archive.test_archive import Clock

    net = gs.Site(Clock())
    crawler = gs.make(tmp_path, net)
    seed(crawler, gs.HOME)
    gs.work(crawler, net)
    f = crawler.frontier
    old = f.row(gs.PIC + "AMxu72h1p3=w1280")["id"]  # the picture's own row, not the hop it answered with
    f.x("UPDATE url SET state='skipped', reason='out:cap_image', status=NULL, final_url=NULL WHERE id=?", (old,))
    rows_before = f.q("SELECT count(*) FROM url")[0][0]
    assert f.put_back(f.row(gs.HOME)["id"], "update_revisit")
    gs.work(crawler, net)
    assert net.reading[gs.HOME] == 2
    assert not [u for u in net.asked(gs.PIC) if "h2p" in u]  # no picture of the second reading is asked for
    assert f.q("SELECT count(*) FROM url")[0][0] == rows_before  # and none is a new row
    assert old in {r["res_id"] for r in gs.entries(f, gs.HOME).values()}
    assert (f.row_by_id(old)["state"], f.row_by_id(old)["reason"]) == ("skipped", "out:cap_image")


# ---------------------------------------------------------------- a skip rule with a name of its own

NAMED = {"site": "c.nknu.edu.tw/aca", "round": "1", "imp": "全校重要", "home": "https://c.nknu.edu.tw/aca/default.aspx",
         "paths": ["/aca"], "skip": ["/aca/About.aspx", "/aca/tags"], "skip_names": {"/aca/About.aspx": "teacher_fulltext"}}


def test_a_skip_path_may_carry_a_name_of_its_own():
    """The export's 原因代码 of every skip path is out:skip_path. The content stage lets the documents one named rule
    takes out of its library leave, and no other rule's (a rule written wrong must stop a swap): that rule's rows
    say their own name, out:skip_path:<name>."""
    s = round1.parse_site(NAMED)
    assert s.skip_name("https://c.nknu.edu.tw/aca/about.aspx") == "teacher_fulltext"
    assert s.skip_name("https://c.nknu.edu.tw/aca/tags/a") == ""  # a rule with no name
    assert s.skip_name("https://c.nknu.edu.tw/aca/news.aspx") == ""  # no rule at all
    for bad in ({"/aca/other.aspx": "x"}, {"/aca/About.aspx": "Two Words"}, {"/aca/About.aspx": ""}, ["teacher"]):
        with pytest.raises(ValueError):
            round1.parse_site({**NAMED, "skip_names": bad})


def test_the_works_rule_is_named_and_the_list_s_named_rules_are_the_ones_decided_by_name(plan):
    assert plan.site("sso.nknu.edu.tw").skip_name(WORKS) == "teacher_fulltext"
    assert plan.site("sso.nknu.edu.tw").skip_name(RULES) == ""
    named = {s.site: dict(s.skip_names) for s in plan.sites.values() if s.skip_names}
    # every name is one the content stage's guard was told of first: a new one here is a new line there
    assert named == {"sso.nknu.edu.tw": {"/previewpdf.aspx?cpathandfilename=%5cnknuteacher%5c": "teacher_fulltext"},
                     "cirn.k12ea.gov.tw": {"/web1240/dl_page.aspx": "teaching_materials"}}
    assert "著作" in export.RULE_WHY["skip_path:teacher_fulltext"]


def test_a_named_rule_s_rows_are_exported_under_its_name_got_before_or_never(tmp_path, monkeypatch):
    from tests.archive.test_export_1027 import BASE
    from tests.archive.test_export_step3_1108 import _with

    monkeypatch.setattr(export, "_site_rules", lambda site: round1.parse_site(NAMED))
    never = BASE + "about.aspx?tab=2"  # a row the ledger already holds as the rule (the one-time marking, a new link)

    def build(a):
        a.f.set_host("c.nknu.edu.tw", queue="ip:test")
        a.f.add(never, host="c.nknu.edu.tw", site="c.nknu.edu.tw/aca", unit=None, tier=0, kind="page", depth=1,
                found_via="link", found_from=None, state="skipped", reason="out:skip_path",
                owner_site="c.nknu.edu.tw/aca")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    for url in (BASE + "about.aspx", never):
        r = rows[url]
        assert (r["分类"], r["本地档案"], r["原因代码"]) == ("规则不抓", "", "out:skip_path:teacher_fulltext"), url
        assert export.RULE_WHY["skip_path:teacher_fulltext"] in r["原因"]
    assert all(r["原因代码"] != "out:skip_path:teacher_fulltext" for u, r in rows.items() if "about.aspx" not in u)


# ---------------------------------------------------------------- an outline's title: its course, class and term

OUTLINE = "https://sso.nknu.edu.tw/stu/18weekssyllabusforenro.aspx?syllabusno=1151000001"


def _outline(*rows: tuple[str, str]) -> str:
    cells = "".join(f"<tr><td>{a}</td><td>x</td><td>{b}</td></tr>" for a, b in rows)
    return ('<html><head><title>單一登入</title></head><body><h2>教學大綱 Syllabus</h2>'
            f'<div id="ctl00_phMain_plCourseBasicData"><table>{cells}</table></div></body></html>')


def test_an_outline_s_title_carries_its_class_and_its_term():
    """Many courses share a name (專題研討 in every department) and an outline's words name no term the content stage
    reads (the year and the term stand in two cells): the title says the course, then its class and the term as the
    school writes a term."""
    from selectolax.lexbor import LexborHTMLParser

    full = _outline(("開課學年", "115"), ("開課學期", "1"), ("科目名稱", "甲課概論 Introduction to A"),
                    ("任課班級", "甲系碩一"))
    assert export.page_title(LexborHTMLParser(full), OUTLINE, "sso.nknu.edu.tw/stu") == (
        "甲課概論 Introduction to A（甲系碩一，115學年度第1學期）")
    no_class = _outline(("開課學年", "115"), ("開課學期", "1"), ("科目名稱", "體育（甲組） PE (A)"), ("任課班級", ""))
    assert export.page_title(LexborHTMLParser(no_class), OUTLINE, "sso.nknu.edu.tw/stu") == (
        "體育（甲組） PE (A)（115學年度第1學期）")  # a course of no class (PE, general education): its term all the same
    bare = _outline(("科目名稱", "甲課概論 Introduction to A"), ("任課班級", "甲系碩一"))  # no term: the name alone
    assert export.page_title(LexborHTMLParser(bare), OUTLINE, "sso.nknu.edu.tw/stu") == "甲課概論 Introduction to A"
    none = _outline(("開課學年", "115"), ("開課學期", "1"))  # no course shown: the headings, as before
    assert export.page_title(LexborHTMLParser(none), OUTLINE, "sso.nknu.edu.tw/stu") == "教學大綱 Syllabus"


def test_a_query_form_of_the_course_query_is_titled_by_its_own_caption():
    """The course query's form pages (nothing searched yet) have no h1 or h2, and their <title> is the platform's name
    on every page: the form's caption names the page (「開課資料查詢－依系所」), else its h3."""
    from selectolax.lexbor import LexborHTMLParser

    def form(inner: str) -> str:
        return ("<html><head><title>國立高雄師範大學 單一登入 網路資訊平台</title></head><body>"
                f"<h5 class='modal-title'>系統訊息：</h5><h3>開 課 資 訊 查 詢</h3>{inner}</body></html>")

    site = "sso.nknu.edu.tw/stu"
    by_unit = form("<table><caption>開課資料查詢－依系所</caption><tr><td>x</td></tr></table>")
    assert export.page_title(LexborHTMLParser(by_unit), "https://sso.nknu.edu.tw/stu/scheduledepartment.aspx",
                             site) == "開課資料查詢－依系所"
    assert export.page_title(LexborHTMLParser(form("")), "https://sso.nknu.edu.tw/stu/scheduleenglish.aspx",
                             site) == "開 課 資 訊 查 詢"
    assert export.page_title(LexborHTMLParser(by_unit), "https://sso.nknu.edu.tw/web/592/a.aspx",
                             "sso.nknu.edu.tw/web/592") != "開課資料查詢－依系所"  # no other site's pages


# ---------------------------------------------------------------- the Google files a kind of page names are not fetched
# (11-19: 工教系's seminar proceedings, whole volumes of papers on Drive, one a year on its 學術活動 pages; the works of
# the teachers again. The pages stay, and so do the files on the site's own host.)

PROC = {"site": "x.nknu.edu.tw", "round": 1, "imp": "本系重要", "home": "https://x.nknu.edu.tw/",
        "skip_google": {"/?p=acadact": "proceedings"}}
ACT, BOARD = "https://x.nknu.edu.tw/?p=acadact&id=34", "https://x.nknu.edu.tw/?p=billboard&id=7"
FILE_LINK = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view"
FILE_API = "https://www.googleapis.com/drive/v3/files/1AbCdEfGhIjKlMnOp?fields=id"


def test_a_site_may_name_the_pages_whose_google_files_it_leaves():
    s = round1.parse_site(PROC)
    assert s.skip_google_name(ACT) == "proceedings"
    assert s.skip_google_name("https://x.nknu.edu.tw/?P=AcadAct&id=3") == "proceedings"  # the query's start, any case
    assert s.skip_google_name(BOARD) == "" and s.skip_google_name("https://x.nknu.edu.tw/") == ""
    assert s.skip_google_name("https://y.nknu.edu.tw/?p=acadact&id=34") == ""  # another site's page
    assert round1.parse_site({k: v for k, v in PROC.items() if k != "skip_google"}).skip_google_name(ACT) == ""
    for bad in ({"/?p=acadact": "Two Words"}, {"/?p=acadact": ""}, ["/?p=acadact"], "proceedings"):
        with pytest.raises(ValueError):
            round1.parse_site({**PROC, "skip_google": bad})


def _google(kind0: str = "drive"):
    """The Google step, as google_api's makes a Drive file of a link: the page's own resource, at its API address."""
    def step(url, row, how):
        if "/forms/" in url:
            return None  # a form's page is left to the ordinary rules
        return round1.Admission(url, FILE_API, round1.Placement("resource", None, row["owner_site"], row["tier"], 1,
                                                                 kind0), "google")
    return step


def _from(url: str) -> dict:
    return {"id": 1, "url": url, "owner_site": "x.nknu.edu.tw", "tier": 1, "kind": "page", "kind0": "page",
            "state": "done", "depth": 1, "leaf": 0}


def test_a_drive_file_such_a_page_names_is_left_by_rule_and_another_page_s_is_followed():
    plan = make_plan(round1.parse_site(PROC))
    left = round1.admit(plan, FILE_LINK, from_row=_from(ACT), google=_google())
    assert (left.placement.verdict, left.placement.reason, left.placement.kind0) == ("out", "out:skip_path", "drive")
    assert left.url == FILE_API and left.placement.state is None  # no row: nothing is asked of Google
    kept = round1.admit(plan, FILE_LINK, from_row=_from(BOARD), google=_google())
    assert (kept.placement.verdict, kept.placement.owner_site) == ("resource", "x.nknu.edu.tw")
    # a calendar the page shows, a form it links and a file on the site's own host are as before
    assert round1.admit(plan, FILE_LINK, from_row=_from(ACT), google=_google("calendar")).placement.verdict == "resource"
    form = round1.admit(plan, "https://docs.google.com/forms/d/e/1FAIpQLSabc/viewform", from_row=_from(ACT),
                        google=_google())
    assert form.placement.reason != "out:skip_path"
    own = round1.admit(plan, "https://x.nknu.edu.tw/dl.php?id=3", from_row=_from(ACT), google=_google())
    assert own.placement.verdict == "r1"


def test_the_proceedings_rule_is_named_and_stands_on_the_department_s_activity_pages(plan):
    ite = plan.site("ite.nknu.edu.tw")
    assert ite.skip_google_name("https://ite.nknu.edu.tw/?p=acadact&id=34") == "proceedings"
    assert ite.skip_google_name("https://ite.nknu.edu.tw/?p=billboard&id=219") == ""
    assert {s.site: dict(s.skip_google) for s in plan.sites.values() if s.skip_google} == {
        "ite.nknu.edu.tw": {"/?p=acadact": "proceedings"}}
    assert "論文集" in export.RULE_WHY["skip_path:proceedings"] or "论文集" in export.RULE_WHY["skip_path:proceedings"]


def test_a_google_file_got_before_such_a_rule_is_exported_as_the_rule_under_its_name(tmp_path, monkeypatch):
    """The file stays in the archive; the export writes its row as the rule, with no file, under the rule's own name
    (the content stage lets the documents of a rule it knows by name leave its library). The page that names it is
    told by the row's own trail (found_from: the file, its Drive record, the page), and by the pages that use it."""
    from tests.archive.test_export_1027 import BASE, DRIVE_ID, SITE
    from tests.archive.test_export_step3_1108 import NOTE_PDF, _with

    rule = round1.parse_site({"site": SITE, "round": "1", "imp": "全校重要", "home": BASE + "default.aspx",
                              "paths": ["/aca"], "skip_google": {"/aca/about.aspx": "proceedings"}})
    monkeypatch.setattr(export, "_site_rules", lambda site: rule)
    other = "1ZyXwVuTsRqPoNmLk"

    def build(arch):
        meta = arch.file("meta2", f"https://www.googleapis.com/drive/v3/files/{other}?fields=id", b"{}",
                         "application/json", kind="drive", sniff="json", host="www.googleapis.com",
                         site="www.googleapis.com", found_from=arch.ids["about"])
        content = arch.file("drive2", f"https://www.googleapis.com/drive/v3/files/{other}?alt=media", NOTE_PDF,
                            "application/pdf", host="www.googleapis.com", site="www.googleapis.com", found_via="drive",
                            found_from=meta)
        arch.f.x("INSERT INTO drive_item(url_id, file_id, name, mime, size, content_id) VALUES (?,?,?,?,?,?)",
                 (meta, other, "2026 研討會論文集.pdf", "application/pdf", len(NOTE_PDF), content))
        # the page read again since the rule came: it no longer says it uses the file

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    r = rows[f"https://www.googleapis.com/drive/v3/files/{other}?alt=media"]
    assert (r["分类"], r["本地档案"], r["原因代码"]) == ("规则不抓", "", "out:skip_path:proceedings")
    assert export.RULE_WHY["skip_path:proceedings"] in r["原因"]
    # the fixture's own Drive file, which another page names, keeps its file
    kept = rows[f"https://www.googleapis.com/drive/v3/files/{DRIVE_ID}?alt=media"]
    assert kept["本地档案"].startswith("files/") and kept["原因代码"] != "out:skip_path:proceedings"


# ---------------------------------------------------------------- a file only the pages a named rule leaves name
# (11-19: 国中小自造及科技辅导中心's download pages, cirn.k12ea.gov.tw/web1240/dl_page.aspx: teaching material for
# school teachers, some two hundred large files. The pages are a skip path with a name; a file reached only through
# them is left with them, the file a page that stays names too is kept.)

def test_the_centre_s_download_pages_are_a_named_rule_and_its_menu_and_notices_stay(plan):
    cirn = plan.site("cirn.k12ea.gov.tw")
    page = "https://cirn.k12ea.gov.tw/web1240/dl_page.aspx?o=50&fid=16877&nowPage=2&pageSize=10"
    assert cirn.skips(page) and cirn.skip_name(page) == "teaching_materials"
    assert cirn.skip_name("https://cirn.k12ea.gov.tw/web1240/DL_Page.aspx?fid=16859") == "teaching_materials"
    for kept in ("https://cirn.k12ea.gov.tw/web1240/List.aspx?fid=16877", "https://cirn.k12ea.gov.tw/web1240/news.aspx?id=3",
                 "https://cirn.k12ea.gov.tw/web1240/", "https://cirn.k12ea.gov.tw/web1240/link_page.aspx?fid=1"):
        assert not cirn.skips(kept), kept
    assert "教材" in export.RULE_WHY["skip_path:teaching_materials"]


def test_a_file_only_a_named_rule_s_pages_name_is_exported_as_that_rule(tmp_path, monkeypatch):
    from tests.archive.test_export_1027 import BASE, SITE, _res
    from tests.archive.test_export_step3_1108 import NOTE_PDF, _with

    rule = round1.parse_site({"site": SITE, "round": "1", "imp": "全校重要", "home": BASE + "default.aspx",
                              "paths": ["/aca"], "skip": ["/aca/about.aspx", "/aca/tags"],
                              "skip_names": {"/aca/about.aspx": "teaching_materials"}})
    monkeypatch.setattr(export, "_site_rules", lambda site: rule)
    only, both, trail, unnamed = (BASE + f"uploadfile/{n}.pdf" for n in ("only", "both", "trail", "unnamed"))

    def build(arch):
        about, d1 = arch.ids["about"], arch.ids["d1"]
        a = arch.file("only", only, NOTE_PDF, "application/pdf")
        _res(arch.f, about, only, "file", res_id=a, text="教材一")
        b = arch.file("both", both, NOTE_PDF + b"2", "application/pdf")
        _res(arch.f, about, both, "file", res_id=b, text="教材二")
        _res(arch.f, d1, both, "file", res_id=b, text="公告的附件")
        # found on the rule's page, which no longer says it uses it (the page was read again under the rule)
        arch.file("trail", trail, NOTE_PDF + b"3", "application/pdf", found_from=about)
        # a file only a page of a rule with no name names stays as it was: only a rule decided by name takes files
        tags = arch.page("tags", BASE + "tags/a", "<html><body><a href='/aca/uploadfile/unnamed.pdf'>x</a></body></html>")
        u = arch.file("unnamed", unnamed, NOTE_PDF + b"4", "application/pdf")
        _res(arch.f, tags, unnamed, "file", res_id=u, text="x")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    for url in (only, trail):
        r = rows[url]
        assert (r["分类"], r["本地档案"], r["原因代码"]) == ("规则不抓", "", "out:skip_path:teaching_materials"), url
    assert rows[both]["本地档案"].startswith("files/") and rows[both]["分类"] == "抓到了"
    assert rows[unnamed]["本地档案"].startswith("files/")
    assert rows[BASE + "about.aspx"]["原因代码"] == "out:skip_path:teaching_materials"


def test_a_redirect_to_a_named_rule_s_page_is_exported_as_that_rule(tmp_path, monkeypatch):
    """The centre's menu sends a folder to its download page (List.aspx?fid=… answers 302): the menu row shared the
    download page's file. With that page left by a rule that has a name, the row that only leads to it says the same
    rule in 原因代码, so that what left with the rule is told by one code; a redirect to a page that stays is as it was."""
    from tests.archive.test_export_1027 import BASE, SITE, _add
    from tests.archive.test_export_step3_1108 import _with

    rule = round1.parse_site({"site": SITE, "round": "1", "imp": "全校重要", "home": BASE + "default.aspx",
                              "paths": ["/aca"], "skip": ["/aca/about.aspx"],
                              "skip_names": {"/aca/about.aspx": "teaching_materials"}})
    monkeypatch.setattr(export, "_site_rules", lambda site: rule)
    to_rule, to_kept = BASE + "list.aspx?fid=1", BASE + "list.aspx?fid=2"

    def build(arch):
        for url, target in ((to_rule, BASE + "about.aspx"), (to_kept, BASE + "newsview.aspx?nid=2")):
            off, sha = arch.put(url[-5:], url, "text/html", b"moved", headers={"location": target}, status=302)
            _add(arch.f, url=url, final_url=target, status=302, bytes=5, sha256=sha, warc=arch.rel, warc_offset=off,
                 ctype="text/html", sniff="html", page_type="t:ok")

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    r = rows[to_rule]
    assert (r["分类"], r["本地档案"], r["原因代码"]) == ("规则不抓", "", "out:skip_path:teaching_materials")
    assert "跳转" in r["备注"]
    assert rows[to_kept]["本地档案"] and rows[to_kept]["原因代码"] != "out:skip_path:teaching_materials"
