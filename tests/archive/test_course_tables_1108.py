"""Round one's gap ⑩ (docs/round1-gaps-2026-11-08.md; the owner 11-08 19:58 had ⑩⑪⑫ filled in, how 【Claude 的执行办法】):
19 academic units' course tables (dataopen2search/opencoursetablepure.aspx?unit=<code>) were never reached, because
no page round one fetched links them. They go in by `nknu archive reconcile --enqueue <list>` and need no change of
the crawler: this guards that such a list is admitted as round one's (the site's own path, tier 0, found_via
reconcile, the address written with a lower-case unit=), and that the course table without 「pure」 stays round two's
(the list holds only the lost-and-found and course-table pages of the site: config/archive_round1.json)."""

from __future__ import annotations

from nknu.archive import reconcile, round1
from nknu.archive.frontier import Frontier

SITE = "sso.nknu.edu.tw/dataopen2search"
WANTED = "https://sso.nknu.edu.tw/dataopen2search/opencoursetablepure.aspx?unit=999999"  # a made-up unit code


def test_a_listed_course_table_is_queued_as_round_ones(tmp_path):
    Frontier(tmp_path / "frontier.db").close()  # an empty round-1 archive
    listed = tmp_path / "course_units.txt"
    listed.write_text("# 人看过：课程查询系统的教师系所选单里有、存档里没有的单位\n"
                      "https://sso.nknu.edu.tw/dataopen2search/openCourseTablePure.aspx?Unit=999999\t999999 測試學系\n"
                      "https://sso.nknu.edu.tw/dataopen2search/opencoursetable.aspx\t不是 pure 的课表\n",
                      encoding="utf-8")
    got = reconcile.enqueue(tmp_path, reconcile.read_list(listed), plan=round1.load())
    assert [v for _u, v in got] == ["queued", "round2:other_site"]
    f = Frontier(tmp_path / "frontier.db")
    row = f.row(WANTED)  # written as the crawler writes every address: lower-case, unit= in lower case
    assert (row["state"], row["owner_site"], row["tier"], row["found_via"], row["kind0"]) == (
        "pending", SITE, 0, "reconcile", "page")
    later = f.row("https://sso.nknu.edu.tw/dataopen2search/opencoursetable.aspx")
    assert (later["state"], later["reason"]) == ("later", "round2:other_site")
    again = reconcile.enqueue(tmp_path, [WANTED], plan=round1.load())
    assert again == [(WANTED, "dup")]  # listed twice: one row
    f.close()
