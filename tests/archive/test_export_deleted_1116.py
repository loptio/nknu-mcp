"""The export batch, T4 (plan docs/superpowers/plans/2026-11-16-export-batch.md; design 2026-11-16-periodic-update-
design.md §7.5; the content session's handover point 3): a row the school deleted (answered gone twice at least 24 h
apart, good once before: the nightly change list's deleted, watch.night) says 「学校已删除」 in 缺什么, after what else
it lacks; the content stage leaves such rows out by that substring (export.SCHOOL_DELETED). Made-up rows only."""

from __future__ import annotations

import pytest

from nknu.archive import export
from tests.archive.test_export_1027 import BASE
from tests.archive.test_export_prev_1116 import GONE_PAGE, _again
from tests.archive.test_export_step3_1108 import _with

NOTICE2 = BASE + "newsview.aspx?nid=2"
T0 = 1_794_800_000.0  # the first gone answer (2026-11-16, Taipei)
DAY = 24 * 3600


def _watch(arch, name: str, *, gone: int, since: float, checked: float) -> None:
    arch.f.x("INSERT OR REPLACE INTO page_watch(url_id, fp, checked_at, changed_at, changes, gone_at, gone) "
             "VALUES (?,?,?,?,?,?,?)", (arch.ids[name], None, checked, None, 0, since if gone else None, gone))


def _deleted(tmp_path, *, gone: int, after: float, refetched: bool = True, name: str = "d2", url: str = NOTICE2):
    def build(arch):
        if refetched:
            _again(arch, name, body=GONE_PAGE, status=404)
        _watch(arch, name, gone=gone, since=T0, checked=T0 + after)

    _arch, _got, _out, rows, readme = _with(tmp_path, build)
    return rows[url], readme


def test_the_constant_is_what_the_content_stage_reads():
    assert export.SCHOOL_DELETED == "学校已删除"


def test_a_page_gone_twice_a_day_apart_says_the_school_deleted_it(tmp_path):
    row, readme = _deleted(tmp_path, gone=2, after=DAY + 60)
    assert export.SCHOOL_DELETED in row["缺什么"]
    assert "2026-11-16" in row["缺什么"]
    assert row["本地档案"]  # the last good answer is still the file (T1)
    assert export.SCHOOL_DELETED in readme


@pytest.mark.parametrize(("gone", "after"), [(1, 3 * DAY), (2, DAY - 60), (0, 3 * DAY)],
                         ids=["once", "within_a_day", "back_again"])
def test_a_page_not_gone_twice_a_day_apart_is_not_marked(tmp_path, gone, after):
    row, _readme = _deleted(tmp_path, gone=gone, after=after)
    assert export.SCHOOL_DELETED not in row["缺什么"]


def test_a_row_never_good_is_never_said_deleted(tmp_path):
    """The broken attachment never came back good: gone twice is no deletion of something the school had."""
    row, _readme = _deleted(tmp_path, gone=2, after=2 * DAY, refetched=False, name="broken",
                            url=BASE + "uploadfile/broken.pdf")
    assert export.SCHOOL_DELETED not in row["缺什么"]
