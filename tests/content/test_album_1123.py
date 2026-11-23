"""11-23 (the owner through session judge): an album's photos are not collected. The crawler's export declines them by
rule (分类 规则不抓, 原因代码 album), yet some had a row of their own with a file too (fetched before the rule, or linked
from a unit's front page), and the build kept them as 附件:图片: 44 thumbnails of /wccafpsfs/case/ in the library of
11-23, two of which left that night for no reason the guard knew. Now the build leaves out a record at an address the
export declines that way (build._leave_declined), counted as <kind>(相簿不收), and its leaving is explained
(decided_album). The sentences are made up; no model loads."""

from __future__ import annotations

import json
import sqlite3

from nknu.content import build
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content import test_replaced_1118 as tr

CHAIR, CHAIR_SITE = tc.CHAIR, tc.CHAIR_SITE
CASE = "https://sso.nknu.edu.tw/services/site/wccafpsfs/case/175/thumbnail/"
THUMB, OTHER_THUMB = CASE + "175_9242386_0_s.jpg", CASE + "175_9242386_1_s.jpg"
POSTER = "https://sso.nknu.edu.tw/services/site/uploadfile/poster.jpg"
PHOTO = "附件:图片"
WORDS = "本頁說明修課順序與先修科目，請同學依年級選課。"


def _decline(export, url: str, code: str = "album") -> None:
    """A row of the export the crawler declined by rule, without a file, at url."""
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    manifest["项目"].append({"序号": str(len(manifest["项目"]) + 1), "类型": "图片", "网址": url, "最后落地网址": "",
                           "本地档案": "", "分类": build.RULE_DECLINED, "结果": "按规则不抓", "原因代码": code,
                           "原因": "相簿页的照片（相簿不收）"})
    (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_an_album_photo_the_export_also_gives_a_file_for_is_left_out(tmp_path):
    rows = [{"kind": "page:sso_clv2", "url": CHAIR + "1", "title": "單位首頁", "text": WORDS},
            {"kind": PHOTO, "url": THUMB, "title": "175_9242386_0_s.jpg", "text": "活動照片裡辨識出來的幾個字，多半不成句。"},
            {"kind": PHOTO, "url": POSTER, "title": "海報.jpg", "text": "研習海報：時間、地點與報名方式寫在下面。"}]
    corpus, export = tc.site_input(tmp_path / "chair", CHAIR_SITE, "系所", rows)
    _decline(export, THUMB)
    _decline(export, OTHER_THUMB)  # declined with no file of its own: nothing of it to leave out
    _decline(export, POSTER, code="wix_size")  # another rule's row explains nothing here: the poster stays
    out = tmp_path / "db" / "a.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=[(corpus, export)])
    with sqlite3.connect(f"file:{out}?mode=ro", uri=True) as conn:
        urls = {r[0] for r in conn.execute("SELECT url FROM docs")}
    assert THUMB not in urls and POSTER in urls and CHAIR + "1" in urls
    assert report.excluded[PHOTO + "(相簿不收)"] == 1


def test_the_rows_are_read_by_their_code_and_their_kind_only(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    (export / "manifest.json").write_text(json.dumps({"网站": CHAIR_SITE, "项目": []}), encoding="utf-8")
    _decline(export, THUMB)
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    manifest["项目"].append({"网址": OTHER_THUMB, "本地档案": "", "分类": "抓到了", "原因代码": "album"})  # not 规则不抓
    (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    records = [tc.rec("a1", PHOTO, THUMB.replace("https://", "http://"), "a.jpg", "字", CHAIR_SITE),
               tc.rec("a2", PHOTO, OTHER_THUMB, "b.jpg", "字", CHAIR_SITE)]
    kept, left, counted = build._leave_declined(records, [export, tmp_path / "no-such-export"])
    assert [r.id for r in kept] == ["a2"]  # the scheme aside, the address is the declined one's
    assert left == {"da1": PHOTO + "(相簿不收)"} and counted == {PHOTO + "(相簿不收)": 1}
    assert build._leave_declined(records, [tmp_path / "no-such-export"]) == (records, {}, {})
    # the same picture a notice shows in its text or its attachment area stays: it is the notice's
    shown = tc.rec("a3", PHOTO, THUMB, "c.jpg", "校訊第二版的字", CHAIR_SITE, linked=(CHAIR + "9",))
    kept, left, _ = build._leave_declined([shown], [export])
    assert [r.id for r in kept] == ["a3"] and left == {}
    # an honor roll's picture an album shows too stays (KEEP_DECLINED)
    honor = "https://tecs.nknu.edu.tw/uploadfile/honorroll/honorroll_114161139.jpg"
    _decline(export, honor)
    kept, left, _ = build._leave_declined([tc.rec("a4", PHOTO, honor, "d.jpg", "恭賀 榮獲教學傑出獎", CHAIR_SITE)], [export])
    assert [r.id for r in kept] == ["a4"] and left == {}


def test_an_album_photo_leaves_the_library_for_a_reason(tmp_path):
    old = tr.library(tmp_path / "old.db", [("dthumb", "attachment", "175_9242386_0_s.jpg", THUMB, "照片的字"),
                                           ("dform", "attachment", "表.pdf", POSTER, "申請表的說明。")])
    new = tr.library(tmp_path / "new.db", [("dform", "attachment", "表.pdf", POSTER, "申請表的說明。")])
    left = build._departures(old, new, {"dthumb": PHOTO + "(相簿不收)"}, [])
    assert left["left"] == 1 and left["explained"] == {"decided_album": 1} and left["unexplained"] == {}
    assert left["words"]["decided_album"] == "定了不收：相簿照片" and not build._too_many_left(left)
    assert build._why_left(PHOTO + "(相簿不收)") == ("decided_album", True)
    assert build._why_left(build.marks.MENU_IMAGE) == ("kind_not_kept", False)  # a menu picture alone, as before
    assert all(why in build.LEFT_WORDS for _, why in build.DECLINED_OUT.values())
