"""Teachers' CVs stand under the department their 校內職務 tab names (nknu.content.vita; decided 11-15: once round two
had every CV, a CV goes under the department its page names; written 11-18). The tab is the school's own record:
單位 | 職稱 | 分機 | 日期. The first current teaching post's unit, after the college, is the department; every page of
that teacher (the main page, each tab answer) states it, and the build places it as it places a notice's stated
publisher. The table rows below are made up; no model loads."""
from __future__ import annotations

import sqlite3

from nknu.content import build, vita
from nknu.content.corpus import Record
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm

CV = "https://sso.nknu.edu.tw/DataOpen2Search/vita.aspx?unid=7"
CV_SITE = "sso.nknu.edu.tw/dataopen2search"


def posts(*rows: tuple[str, str, str]) -> str:
    """A 校內職務 answer: one row per (單位, 職稱, 日期)."""
    body = "".join(f'<tr><td data-th="單位">{u}</td><td data-th="職稱">{t}</td><td data-th="分機"></td>'
                   f'<td data-th="日期">{d}</td></tr>' for u, t, d in rows)
    return ('<html><body><table><thead><tr><th>單位</th><th>職稱</th><th>分機</th><th>日期</th></tr></thead>'
            f"<tbody>{body}</tbody></table></body></html>")


def test_the_first_current_teaching_post_names_the_department():
    html = posts(("文學院 - 國文學系", "系主任", "2025/08/01 ~ 至今"),  # a post, not a teaching one
                 ("理學院 - 化學系", "助理教授", "2010/08/01 ~ 2015/07/31"),  # a teaching post that ended
                 ("文學院 - 國文學系", "教授", "2015/08/01 ~ 至今"),
                 ("教務處 - 教務創新組", "組長", "2018/08/01 ~ 至今"))
    assert vita.department(html) == ("國文學系", 1)


def test_a_joint_appointment_takes_the_first_and_is_counted():
    html = posts(("文學院 - 英語學系", "副教授", "2019/02/01 ~ 至今"), ("文學院 - 國文學系", "合聘副教授", "2019/02/01 ~ 至今"))
    assert vita.department(html) == ("英語學系", 2)


def test_nothing_current_or_no_such_table_names_nothing():
    assert vita.department(posts(("文學院 - 國文學系", "講師", "2001/08/01 ~ 2009/07/31"))) == ("", 0)
    assert vita.department(posts(("通識教育中心", "主任", "2020/08/01 ~ 至今"))) == ("", 0)
    other = "<table><thead><tr><th>學校</th><th>系所</th><th>日期</th></tr></thead><tbody><tr><td>某大學</td>" \
            "<td>國文學系</td><td>至今</td></tr></tbody></table>"
    assert vita.department(other) == ("", 0)
    assert vita.department("") == ("", 0)


def test_which_address_is_whose():
    assert vita.teacher(CV) == "7" and vita.teacher(CV + "#按钮=期刊論文") == "7"
    assert vita.is_posts_tab(CV + "#按钮=校內職務") and not vita.is_posts_tab(CV + "#按钮=期刊論文")
    assert not vita.is_posts_tab(CV)
    assert vita.teacher("https://sso.nknu.edu.tw/dataopen2search/OpenCourseTablePure.aspx?unid=7") == ""
    assert vita.teacher("https://sso.nknu.edu.tw/dataopen2search/vita.aspx") == ""


def test_departments_reads_each_teachers_tab_once():
    pages = {CV + "#按钮=校內職務": posts(("文學院 - 國文學系", "教授", "2015/08/01 ~ 至今")),
             CV.replace("=7", "=8") + "#按钮=校內職務": posts(("文學院 - 國文學系", "主任", "2015/08/01 ~ 至今"))}
    recs = [Record(f"p{i}", "page", url, f"pages/{i}.html", "", "", "", ()) for i, url in enumerate([CV, *pages])]
    assert vita.departments(recs, read=lambda r: pages.get(r.url)) == {"7": ("國文學系", 1)}


def test_the_build_puts_every_page_of_a_teacher_under_the_department(tmp_path):
    rows = [{"kind": "page:sso_dataopen2search", "url": CV, "title": "王OO", "text": "專長：古典文學。"},
            {"kind": "page:sso_dataopen2search", "url": CV + "#按钮=校內職務", "title": "王OO 校內職務",
             "text": "國文學系 教授"},
            {"kind": "page:sso_dataopen2search", "url": CV + "#按钮=期刊論文", "title": "王OO 期刊論文",
             "text": "論文一篇。"}]
    corpus, export = tc.site_input(tmp_path / "cv", CV_SITE, "資料開放查詢", rows)
    (export / "pages").mkdir()
    (export / "pages" / "0002.html").write_text(posts(("文學院 - 國文學系", "教授", "2015/08/01 ~ 至今")),
                                                 encoding="utf-8")
    paths = tm.tree_files(tmp_path / "tree")
    out = tmp_path / "db" / "a.db"
    report = tm.run(paths, out, inputs=[(corpus, export)])
    with sqlite3.connect(f"file:{out}?mode=ro", uri=True) as conn:
        units = {u for (u,) in conn.execute("SELECT unit FROM docs")}
        meta = dict(conn.execute("SELECT key, value FROM meta"))
    assert units == {"國文學系"} and meta["vita_units"] == "on"
    assert report.vita_units == {"on": True, "teachers": 1, "joint": 0, "documents": 3,
                                 "departments": {"國文學系": 1}}
    off = tm.run(paths, tmp_path / "db" / "b.db", inputs=[(corpus, export)], vita_units=False)
    with sqlite3.connect(f"file:{tmp_path / 'db' / 'b.db'}?mode=ro", uri=True) as conn:
        assert "國文學系" not in {u for (u,) in conn.execute("SELECT unit FROM docs")}
    assert off.vita_units["documents"] == 3 and off.vita_units["on"] is False
    assert build.BuildReport.__dataclass_fields__["vita_units"]
