"""The export batch, T6 (plan docs/superpowers/plans/2026-11-16-export-batch.md; the content session asked 11-16 for a
mark it leaves out by substring): a platform's notice address the school sends to a page of the same host that is no
notice (its home page, a list, an album: about 150 rows on 11-16, research/crawler_design_2026-11-16/export_batch/
landed_elsewhere_1116.out) says so in 缺什么 (export.NOTICE_MOVED). One sent off the school's host (a notice that is a
link to another university's page) or to the same notice under another scheme is not marked. Made-up rows only."""

from __future__ import annotations

from nknu.archive import export
from tests.archive.test_export_1027 import BASE, _add
from tests.archive.test_export_step3_1108 import _with

MOVED = BASE + "newsview.aspx?nid=9"
AWAY = BASE + "newsview.aspx?nid=10"


def test_the_words_are_what_the_content_stage_reads():
    assert export.NOTICE_MOVED == "学校把这则公告转到别的页（多半是删了）"


def test_a_notice_sent_to_a_page_of_the_same_host_that_is_no_notice_is_marked(tmp_path):
    def build(arch):
        _add(arch.f, url=MOVED, status=302, final_url=BASE + "about.aspx")
        _add(arch.f, url=AWAY, status=302, final_url="https://www.example.edu.tw/news/1", tier=4)

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert export.NOTICE_MOVED in rows[MOVED]["缺什么"]
    assert export.NOTICE_MOVED not in rows[AWAY]["缺什么"]


def test_a_notice_moved_to_its_other_scheme_is_not_marked(tmp_path):
    https = BASE + "newsview.aspx?nid=1"

    def build(arch):
        _add(arch.f, url=https.replace("https://", "http://"), status=301, final_url=https)

    _arch, _got, _out, rows, _readme = _with(tmp_path, build)
    assert export.NOTICE_MOVED not in rows[https.replace("https://", "http://")]["缺什么"]
