"""11-17: the export service takes the 10-24 archive's copies unless told not to. The go-live re-export of 11-16 ran
without OLD_COPIES and 711 rows lost the copy item ⑦ had filled them with (【用户】 「⑦……在导出或整理时一并补上」;
CLAUDE.md: 抓取失败或网站改版时，保留旧值); the nightly export ran without it too. Unset now means the lab's copies
folder; only OLD_COPIES='' exports without them. On this machine the lab's folder is not there, so unset stops the
batch before any export and names the folder."""

from __future__ import annotations

import shutil

import pytest

from tests.archive.test_export_1027 import SITE, make_archive
from tests.archive.test_export_review_1108 import SERVICE, _service

LAB_COPIES = "/crawl/old-archive-2026-10-24/copies-1108"


@pytest.mark.skipif(not SERVICE.exists() or shutil.which("bash") is None,
                    reason="the lab tools are on the disk and in code.tar, not in git")
def test_unset_the_service_takes_the_labs_copies_folder(tmp_path):
    arch = make_archive(tmp_path / "archive")
    code, said = _service(tmp_path, arch, OLD_COPIES=None)
    assert code == 1 and LAB_COPIES in said and "读不了" in said and "开始" not in said, said


@pytest.mark.skipif(not SERVICE.exists() or shutil.which("bash") is None,
                    reason="the lab tools are on the disk and in code.tar, not in git")
def test_an_empty_old_copies_exports_without_them(tmp_path):
    arch = make_archive(tmp_path / "archive")
    code, said = _service(tmp_path, arch, OLD_COPIES="")
    assert code == 0 and f"好了 {SITE}" in said, said
