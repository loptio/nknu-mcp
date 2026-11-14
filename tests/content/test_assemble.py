"""T7a: nknu.content.assemble on small made-up exports with a to-text cache written the way the run writes it (plan
2026-11-02 T7a; addendum B: page links, context titles, references and format siblings; G3, G4). Nothing converts and
no model loads; the pages follow the c.nknu template, the names in them are made up.

Since plan 2026-11-08 T1 a record's id is the stable id of its identity (ids.record_id; test_ids_1108.py tests them):
these tests name each record by its row as before (p<序号>, a<序号>, i<序号>, a<序号>m<k>: labelled)."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from nknu.content import assemble as assemble_module
from nknu.content import build, ids
from nknu.content.assemble import assemble, kind_of, main
from nknu.content.build import build_db
from nknu.totext.accounting import input_route, inputs
from nknu.totext.cache import Cache, sha256_file

ACA = "https://c.nknu.edu.tw/aca/"
NOTICE = ACA + "newsdetail.aspx?nid=1&gi=2"
ALBUM = ACA + "album_images.aspx?aid=1&gi=4"
FORMS = ACA + "filesdownload.aspx?pn=111&gi=2"
ZIP_URL = ACA + "uploadfile/news/1/m.zip"
PDF = b"%PDF-1.4 "
JPEG = b"\xff\xd8\xff\xe0 "
BODY = "本辦法依據本校學則訂定之，學生申請停修以一科為限，不予退費。"  # 30 characters: a text layer, not a scan
MEMBERS = {"會議.pdf": PDF + b"member one", "議程.pdf": PDF + b"member two"}


def notice_html(body: str = "", files: str = "", menu: str = "", title: str = "停修公告",
                added: str = "2026/10/21 上午 08:56:00", group: str = "和平教務組", after: str = "") -> bytes:
    """A c.nknu page as exported: its header fields, a side menu outside the content area, the text in #lbl_Contents
    and the attachments in #lbl_FileLink, then `after` (outside the content area too: a page album). It claims big5
    and is UTF-8 with a BOM."""
    return ('﻿<html><head><meta charset="big5"></head><body><form id="form1">'
            f'<div class="urbangreymenu"><ul id="navmenu-v">{menu}</ul></div>'
            f'<span id="lbl_GroupName">{group}</span><span id="lbl_Title">{title}</span>'
            f'<h5>公告日期：<span id="lbl_AddTime">{added}</span></h5>'
            f'<div id="lbl_Contents">{body}</div>'
            f'<div class="panel-body"><span id="lbl_FileLink">{files}</span></div>{after}'
            "</form></body></html>").encode()


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_export(root: Path, rows: list[dict], site: str = "c.nknu.edu.tw/aca", name: str = "教务处") -> Path:
    """manifest.json of the site's export with one row per item (序号 from 1); each item's bytes at its 本地档案. A row's
    date and date_raw are the export's 网页上写的日期 and 日期原文 (plan 2026-11-08 T3)."""
    items = []
    for n, row in enumerate(rows, start=1):
        rel = row.get("file", "")
        if rel and row.get("data") is not None:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(row["data"])
        items.append({"序号": str(n), "类型": row["type"], "网址": row["url"], "最后落地网址": row.get("final", ""),
                      "本地档案": rel, "标题": row.get("title", ""), "网页上写的日期": row.get("date", ""),
                      "日期原文": row.get("date_raw", ""), "格式": row.get("fmt", ""),
                      "链到它的网页": " | ".join(row.get("linked", ())), "链接文字": row.get("linktext", ""),
                      "抓取时间": row.get("fetched", "2026-10-27T07:00:00+08:00"),
                      "sha256": row.get("sha256") or sha(row.get("data") or b"")})
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"网站": site, "名称": name, "导出时间": "2026-11-02T10:00:00+08:00", "项目": items}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def fake_totext(tmp_path: Path, export: Path, results: dict[str, tuple[dict, str]]) -> Path:
    """Cache entries (result, text) for the given account keys, stored as T3b's run stores them: under the exported
    file's own sha256_file (a member under its own), read the way the row's route says; status ok unless given."""
    totext = tmp_path / "totext"
    cache = Cache(totext / "cache")
    by_key = {inp.key: inp for inp in inputs(export)}
    member_entries: dict[str, dict] = {}
    for key, (result, text) in results.items():
        if "!" in key:
            continue
        inp = by_key[key]
        path = export / inp.file
        route = input_route(inp, path)
        full = {"route": route.name, "kind": route.kind, "pdf_offset": route.pdf_offset, "page_kind": route.page_kind,
                "status": "ok", "reason": "", "first_key": key, "first_file": inp.file, **result}
        cache.save(sha256_file(path), full, text, {})
        member_entries |= {f"{key}!{m['path']}": m for m in full.get("members") or []}
    for key, (result, text) in results.items():
        if "!" in key:
            entry = member_entries[key]
            full = {"route": entry["route"], "kind": entry["kind"], "pdf_offset": 0, "page_kind": "", "status": "ok",
                    "reason": "", "first_key": key, **result}
            cache.save(entry["sha256"], full, text, {})
    return totext


def member_entry(index: int, path: str, data: bytes) -> dict:
    return {"index": index, "path": path, "sha256": sha(data), "route": "pdf", "kind": "pdf", "pdf_offset": 0,
            "size": len(data), "status": "ok", "reason": "", "note": ""}


FILES_AREA = ('<a href="uploadfile/news/1/a6.pdf">停修申請表.pdf&nbsp;(12 Kb)</a><br>'
              '<a href="uploadfile/news/1/m.zip">會議資料.zip</a><br><a href="uploadfile/news/1/a8.pdf">舊表.pdf</a>')


def base_rows() -> list[dict]:
    return [
        {"type": "网页", "url": NOTICE, "file": "pages/0001-notice.html", "title": "停修公告", "fmt": "html",
         "data": notice_html(body='<p>停修公告</p><img src="uploadfile/a.jpg"><p>本文</p>', files=FILES_AREA)},
        {"type": "图片", "url": ACA + "uploadfile/a.jpg", "file": "images/0001-a.jpg", "data": JPEG + b"a",
         "title": "a.jpg", "fmt": "jpeg", "linked": [NOTICE]},
        {"type": "网页", "url": ALBUM, "file": "pages/0002-album.html", "title": "相簿", "fmt": "html",
         "data": notice_html(body='<img src="uploadfile/b.jpg">', title="")},
        {"type": "图片", "url": ACA + "uploadfile/b.jpg", "file": "images/0002-b.jpg", "data": JPEG + b"b",
         "title": "b.jpg", "fmt": "jpeg", "linked": [ALBUM]},
        {"type": "图片", "url": ACA + "uploadfile/c.jpg", "file": "images/0003-c.jpg", "data": JPEG + b"c",
         "title": "c.jpg", "fmt": "jpeg", "linked": [NOTICE]},
        {"type": "附件", "url": ACA + "uploadfile/news/1/a6.pdf", "file": "files/0001-a6.pdf", "data": PDF + b"six",
         "title": "a6.pdf", "fmt": "pdf", "linked": [NOTICE], "linktext": "停修申請表.pdf (12 Kb)"},
        {"type": "附件", "url": ZIP_URL, "file": "files/0002-m.zip", "data": zip_bytes(MEMBERS), "title": "m.zip",
         "fmt": "zip", "linked": [NOTICE], "linktext": "會議資料.zip"},
        {"type": "附件", "url": ACA + "uploadfile/news/1/a8.pdf", "file": "files/0003-a8.pdf", "data": PDF + b"eight",
         "title": "a8.pdf", "fmt": "pdf", "linked": [NOTICE], "linktext": "舊表.pdf"},
    ]


def base_results() -> dict[str, tuple[dict, str]]:
    members = [member_entry(i, path, data) for i, (path, data) in enumerate(MEMBERS.items(), start=1)]
    return {
        "1": ({"page": {"title": "停修公告", "album": "", "images": ["uploadfile/a.jpg"]}},
              "停修公告\n[[img:uploadfile/a.jpg]]\n本文"),
        "2": ({}, "115學年度第1學期停修"),
        "3": ({"page": {"title": "", "album": "畢業典禮", "images": ["uploadfile/b.jpg"]}}, "[[img:uploadfile/b.jpg]]"),
        "4": ({}, "畢業典禮於禮堂舉行請準時出席"),
        "5": ({}, "圖"),
        "6": ({"pages": 1, "page_starts": [0], "ocr_pages": []}, "停修申請表\n" + BODY),
        "7": ({"reason": "解开 2 份成员", "members": members}, ""),
        "7!會議.pdf": ({"pages": 1, "page_starts": [0]}, "會議紀錄\n" + BODY),
        "7!議程.pdf": ({"pages": 1, "page_starts": [0]}, "議程\n" + BODY),
        "8": ({"status": "failed", "reason": "Docling 讀不了這個檔"}, ""),
    }


OLD = [  # the 10-29 corpus: per-file fallback only
    {"id": "a6", "kind": "附件:pdf:有文字层", "url": ACA + "uploadfile/news/1/a6.pdf", "file": "files/0001-a6.pdf",
     "title": "a6.pdf", "text": "10-29 粗轉的停修申請表"},
    {"id": "a8", "kind": "附件:pdf:有文字层", "url": ACA + "uploadfile/news/1/a8.pdf", "file": "files/0003-a8.pdf",
     "title": "a8.pdf", "text": "10-29 粗轉的舊表\n請填寫學號"},
]


def run_assemble(tmp_path: Path, rows: list[dict] | None = None, results: dict | None = None,
                 old: list[dict] | None = OLD, site: str = "c.nknu.edu.tw/aca", name: str = "教务处", **kw):
    export = write_export(tmp_path / "export", base_rows() if rows is None else rows, site, name)
    totext = fake_totext(tmp_path, export, base_results() if results is None else results)
    fallback = None
    if old is not None:
        fallback = tmp_path / "corpus-1029.json"
        fallback.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out" / "corpus.json"
    report = assemble(export, totext, out, fallback=fallback, **kw)
    return labelled(json.loads(out.read_text(encoding="utf-8")), export), report


def row_ids(export: Path) -> dict[str, str]:
    """The stable id of each row's record (plan 2026-11-08 T1) by the name these tests give it: p<序号> for a page,
    a<序号> for an attachment, i<序号> for an image."""
    items = json.loads((export / "manifest.json").read_text(encoding="utf-8"))["项目"]
    keys = ids.manifest_keys(items)
    letters = {"网页": "p", "附件": "a", "图片": "i"}
    return {f"{letters[it['类型']]}{it['序号']}": ids.record_id(keys[int(it["序号"])]) for it in items
            if it["类型"] in letters}


def labelled(records: list[dict], export: Path) -> list[dict]:
    """Each record (and each format sibling listed in one) with _label, the name its row gives it (row_ids; an archive
    member a<序号>m<k>, k its place among its archive's members). Every record has one: an id no row gives fails."""
    names = {rid: name for name, rid in row_ids(export).items()}
    keys = ids.manifest_keys(json.loads((export / "manifest.json").read_text(encoding="utf-8"))["项目"])
    members: Counter[int] = Counter()
    for r in records:
        if r["id"].startswith("m"):
            row = max((n for n, k in keys.items() if r["key"].startswith(k + "#")), key=lambda n: len(keys[n]))
            members[row] += 1
            r["_label"] = f"a{row}m{members[row]}"
        else:
            r["_label"] = names[r["id"]]
        for f in r.get("formats") or ():
            f["_label"] = names[f["id"]]
    return records


def by_id(records: list[dict]) -> dict[str, dict]:
    return {r["_label"]: r for r in records}


# ---------------------------------------------------------------- pages, images, members, fallback (plan T7a)

def test_records_have_the_1029_shape_and_ids(tmp_path):
    records, report = run_assemble(tmp_path)
    assert [r["_label"] for r in records] == ["p1", "p3", "i4", "a6", "a7m1", "a7m2", "a8"]  # manifest order
    for r in records:
        assert {"id", "kind", "url", "file", "title", "text", "linktext", "linked", "source", "totext_status"} <= set(r)
        # changed on purpose (plan 2026-11-08 T1): the id is its identity's (it was p<序号> …), its key beside it
        assert r["id"] == ids.record_id(r["key"], member=r["id"].startswith("m")) and len(r["id"]) == 13
    assert [r["key"] for r in records[:2]] == ["cnknu:c.nknu.edu.tw/aca:1", ids.location_key(ALBUM)]
    r = by_id(records)
    assert (r["p1"]["kind"], r["p1"]["title"], r["p1"]["url"], r["p1"]["file"]) == (
        "newsdetail", "停修公告", NOTICE, "pages/0001-notice.html")
    assert (r["p3"]["kind"], r["p3"]["title"]) == ("album_images", "畢業典禮")  # the album's name first
    assert (r["a6"]["kind"], r["a6"]["title"], r["a6"]["linktext"], r["a6"]["source"], r["a6"]["totext_status"]) == (
        "附件:pdf:有文字层", "a6.pdf", "停修申請表.pdf (12 Kb)", "totext", "ok")
    assert report.records == 7 and report.by_source == {"totext": 6, "fallback-1029": 1}
    assert report.by_kind["附件:pdf:有文字层"] == 4 and report.pending == []


def test_inline_image_text_goes_into_its_page(tmp_path):
    records, report = run_assemble(tmp_path)
    r = by_id(records)
    assert r["p1"]["text"] == "停修公告\n115學年度第1學期停修\n本文" and "i2" not in r
    assert report.images_inlined == 1


def test_inline_image_with_a_cjk_name(tmp_path):
    rows, results = base_rows(), base_results()
    rows[1]["url"] = ACA + "uploadfile/%E6%9C%83%E8%AD%B0%20%E7%85%A7%E7%89%87.jpg"
    results["1"] = (results["1"][0], "停修公告\n[[img:uploadfile/會議 照片.jpg]]\n本文")
    records, _ = run_assemble(tmp_path, rows, results)
    assert by_id(records)["p1"]["text"] == "停修公告\n115學年度第1學期停修\n本文"


def test_image_marker_without_an_exported_image_is_removed(tmp_path):
    results = base_results()
    results["1"] = (results["1"][0], "停修公告\n[[img:uploadfile/gone.jpg]]\n[[img:data:image/png]]\n本文")
    records, report = run_assemble(tmp_path, results=results)
    assert by_id(records)["p1"]["text"] == "停修公告\n本文" and report.markers_unmatched == 2


def test_cache_is_found_by_the_exported_files_hash(tmp_path):
    rows = base_rows()
    rows[0]["sha256"] = sha(b"the archived original, before the export re-saved it as UTF-8 with a BOM")
    records, report = run_assemble(tmp_path, rows)
    assert by_id(records)["p1"]["text"].startswith("停修公告") and report.pending == []


def test_image_on_an_excluded_page_becomes_a_document(tmp_path):
    records, report = run_assemble(tmp_path)
    i4 = by_id(records)["i4"]
    assert (i4["kind"], i4["linked"], i4["title"]) == ("附件:图片", ALBUM, "b.jpg")
    assert i4["text"] == "畢業典禮於禮堂舉行請準時出席"
    assert (i4["url"], i4["file"], i4["source"]) == (ACA + "uploadfile/b.jpg", "images/0002-b.jpg", "totext")
    assert report.image_docs == 1


def test_image_pages_by_how_they_show_it(tmp_path):
    # an image a page links by name from its text is referenced (and named) there; one a page only carries outside its
    # content area (a header banner, a menu) is no reference of that page: nav, never a parent
    rows, results = base_rows(), base_results()
    rows[0]["data"] = notice_html(body='<p>停修公告</p><p><a href="uploadfile/c.jpg">校區平面圖</a></p>',
                                  menu='<li><img src="uploadfile/a.jpg"></li>', files=FILES_AREA)
    results["1"] = (results["1"][0], "停修公告\n校區平面圖")  # its text shows neither image
    results["2"] = (results["2"][0], "教務處 國立高雄師範大學 教務長室")  # a banner's words
    results["5"] = (results["5"][0], "和平校區平面圖 行政大樓 圖書館")
    records, report = run_assemble(tmp_path, rows, results)
    r = by_id(records)
    assert (r["i5"]["linked"], r["i5"]["zones"], r["i5"]["contexts"]) == (NOTICE, {NOTICE: "content"},
                                                                         {NOTICE: "校區平面圖"})
    assert (r["i2"]["linked"], r["i2"]["nav_linked"], r["i2"]["zones"]) == ("", NOTICE, {NOTICE: "nav"})
    assert report.button_downloads == 0 and report.unknown_refs == 0  # images never count as attachments' buttons


def test_image_with_little_text_is_counted_not_kept(tmp_path):
    records, report = run_assemble(tmp_path)
    assert "i5" not in by_id(records) and report.images_no_text == 1


def test_pdf_kinds():
    assert kind_of("pdf", {"ocr_pages": []}, 200, "pdf") == "附件:pdf:有文字层"
    assert kind_of("pdf", {"ocr_pages": [{"page": 1, "why": "few_chars", "lines": 3, "mean_score": 0.9}]}, 200,
                   "pdf") == "附件:pdf:含OCR"
    assert kind_of("pdf", {"ocr_pages": []}, 12, "pdf") == "附件:pdf:无文字层"
    assert kind_of("pdf", {"ocr_pages": [], "docling_ocr_pages": [2]}, 200, "pdf") == "附件:pdf:含OCR"  # region OCR
    no_lines = {"ocr_pages": [{"page": 1, "why": "few_chars", "lines": 0}]}  # our OCR read nothing there
    assert kind_of("pdf", no_lines, 200, "pdf") == "附件:pdf:有文字层"


def test_other_kinds():
    assert kind_of("office", {}, 5, "ooxml:docx") == "附件:office"
    assert kind_of("office_old", {"status": "empty"}, 0, "ole") == "附件:无文字(office)"
    assert kind_of("image", {}, 10, "jpeg") == "附件:图片"
    assert kind_of("image", {"status": "skipped"}, 0, "jpeg") == "附件:无文字(图片)"
    assert kind_of("archive", {"status": "failed"}, 0, "zip") == "附件:无文字(压缩档)"
    assert kind_of("pdf", {"status": "failed"}, 0, "pdf") == "附件:无文字(pdf)"
    assert kind_of("unsupported", {"status": "skipped"}, 0, "other") == "附件:无文字(other)"
    assert kind_of("pdf", {"status": "pending"}, 0, "") == "附件:无文字(pdf)"


def test_members_are_records_and_the_container_goes(tmp_path):
    records, report = run_assemble(tmp_path)
    r = by_id(records)
    assert "a7" not in r
    m1 = r["a7m1"]
    assert m1["url"] == ZIP_URL + "#%E6%9C%83%E8%AD%B0.pdf" and m1["linked"] == NOTICE
    assert (m1["title"], m1["file"], m1["linktext"], m1["kind"]) == (
        "會議.pdf", "files/0002-m.zip", "會議資料.zip", "附件:pdf:有文字层")
    assert m1["text"] == "會議紀錄\n" + BODY and r["a7m2"]["text"] == "議程\n" + BODY
    assert m1.get("contexts", {}) == {}  # the page names the archive, never its members
    assert (report.members, report.containers_dropped) == (2, 1)


def test_container_kept_when_extraction_failed(tmp_path):
    results = base_results()
    results["7"] = ({"status": "failed", "reason": "压缩档打不开（BadZipFile）"}, "")
    del results["7!會議.pdf"], results["7!議程.pdf"]
    records, report = run_assemble(tmp_path, results=results)
    a7 = by_id(records)["a7"]
    assert (a7["kind"], a7["text"], a7["source"], a7["totext_status"]) == ("附件:无文字(压缩档)", "", "none", "failed")
    assert (report.members, report.containers_dropped) == (0, 0)


def test_fallback_used_only_on_failed_and_same_file(tmp_path):
    records, _ = run_assemble(tmp_path)
    r = by_id(records)
    assert (r["a8"]["source"], r["a8"]["text"], r["a8"]["kind"], r["a8"]["totext_status"]) == (
        "fallback-1029", "10-29 粗轉的舊表\n請填寫學號", "附件:pdf:有文字层", "failed")
    assert r["a6"]["source"] == "totext" and r["a6"]["text"] == "停修申請表\n" + BODY


def test_fallback_refused_when_the_file_moved(tmp_path):
    old = [dict(OLD[0]), dict(OLD[1], file="files/0099-x.pdf")]
    records, report = run_assemble(tmp_path, old=old)
    a8 = by_id(records)["a8"]
    assert (a8["kind"], a8["text"], a8["source"]) == ("附件:无文字(pdf)", "", "none")
    assert report.fallback_refused == ["a8"]


def test_no_fallback(tmp_path):
    records, report = run_assemble(tmp_path, old=None)
    assert by_id(records)["a8"]["source"] == "none" and report.fallback_refused == []


def test_texts_are_normalized(tmp_path):
    results = base_results()
    results["6"] = (results["6"][0], f"{chr(0x2F00)}年級\n招 生 簡 章\n並以一 次\n考詴日期\n" + BODY)
    results["1"] = (results["1"][0], "停修公告\n招 生 簡 章\n並以一 次\n[[img:uploadfile/a.jpg]]")
    results["7!會議.pdf"] = (results["7!會議.pdf"][0], "和帄校區考詴\n" + BODY)  # G3: members too
    records, report = run_assemble(tmp_path, results=results)
    r = by_id(records)
    assert r["a6"]["text"] == "一年級\n招生簡章\n並以一次\n考試日期\n" + BODY
    assert r["p1"]["text"] == "停修公告\n招生簡章\n並以一 次\n115學年度第1學期停修"  # wraps are joined in PDF text only
    assert r["a7m1"]["text"] == "和平校區考試\n" + BODY
    assert report.normalized["compat"] == 1 and report.normalized["mismapped"] == 3


def test_short_spaced_runs_join_outside_pdf_text(tmp_path):
    # H1: page, office, office_old and image text (every route but pdf) join an isolated 「分 機」, counted as spaced;
    # PDF text keeps join_wraps, which joins the same label there and counts it as a wrap
    rows, results = base_rows(), base_results()
    docx = zip_bytes({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>"})
    odt = zip_bytes({"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": b"<office/>"})
    rows += [{"type": "附件", "url": ACA + "uploadfile/news/1/f.docx", "file": "files/0004-f.docx", "data": docx,
              "title": "f.docx", "fmt": "docx", "linked": [NOTICE]},
             {"type": "附件", "url": ACA + "uploadfile/news/1/g.odt", "file": "files/0005-g.odt", "data": odt,
              "title": "g.odt", "fmt": "odt", "linked": [NOTICE]}]
    results["1"] = (results["1"][0], "停修公告\n分 機 1131\n[[img:uploadfile/a.jpg]]")
    results["2"] = (results["2"][0], "備　註：115學年度第1學期停修")  # the image the page shows
    results["6"] = (results["6"][0], "停修申請表\n摘 要\n" + BODY)
    results["9"] = ({}, "申 請 人：學生本人")
    results["10"] = ({}, "承辦人 分 機 1131")
    records, report = run_assemble(tmp_path, rows, results)
    r = by_id(records)
    assert r["p1"]["text"] == "停修公告\n分機 1131\n備註：115學年度第1學期停修"
    assert (r["a9"]["kind"], r["a9"]["text"]) == ("附件:office", "申請人：學生本人")  # office (docx)
    assert (r["a10"]["kind"], r["a10"]["text"]) == ("附件:office", "承辦人 分機 1131")  # office_old (odt)
    assert r["a6"]["text"] == "停修申請表\n摘要\n" + BODY
    assert report.normalized["spaced"] == 4 and report.normalized["wraps"] == 1


def test_pending_inputs_refuse_the_run(tmp_path, capsys):
    results = base_results()
    del results["6"]
    export = write_export(tmp_path / "export", base_rows())
    totext = fake_totext(tmp_path, export, results)
    out = tmp_path / "out" / "corpus.json"
    assert main(["--export", str(export), "--totext", str(totext), "--out", str(out), "--no-fallback"]) == 2
    assert "1 个" in capsys.readouterr().err and not out.exists()
    with pytest.raises(assemble_module.PendingInputs):
        assemble(export, totext, out, fallback=None)


def test_allow_pending_takes_the_fallback(tmp_path):
    results = base_results()
    del results["6"]
    records, report = run_assemble(tmp_path, results=results, allow_pending=True)
    a6 = by_id(records)["a6"]
    assert (a6["source"], a6["text"], a6["totext_status"]) == ("fallback-1029", "10-29 粗轉的停修申請表", "pending")
    assert report.pending == ["6"]


DATA_ROWS = [  # 资料 rows as the export writes them (data/ files, titles from export._data_title)
    {"type": "资料", "url": "https://www.googleapis.com/calendar/v3/calendars/x/events", "fmt": "json",
     "file": "data/0001-Google 行事历 x@example.invalid 的事件.json", "data": b'{"items": []}',
     "title": "Google 行事历 x@example.invalid 的事件"},
    {"type": "资料", "url": "https://www.googleapis.com/drive/v3/files?q=x", "fmt": "json",
     "file": "data/0002-云端硬盘资料夹「表單下載」的清单.json", "data": b'{"files": []}',
     "title": "云端硬盘资料夹「表單下載」的清单"},
    {"type": "资料", "url": ACA + "sitemap.json", "fmt": "json", "file": "data/0003-sitemap.json", "data": b"{}",
     "title": "sitemap.json"},
]
DATA_SKIPPED = ({"status": "skipped", "reason": "结构化资料：行事历由建库直接读"}, "")  # to-text's one reason for all


def test_data_rows_are_accounted_one_reason_each(tmp_path):
    # phase-2 review: the export's 资料 rows become no record; the report says why for each one: the calendar is read
    # by the build, a Drive folder's listing is not read (its files are rows of their own), anything else not at all
    results = base_results() | {"9": DATA_SKIPPED, "10": DATA_SKIPPED, "11": DATA_SKIPPED}
    records, report = run_assemble(tmp_path, rows=base_rows() + DATA_ROWS, results=results)
    assert report.data_rows == {"9": assemble_module.DATA_CALENDAR, "10": assemble_module.DATA_DRIVE_LIST,
                                "11": assemble_module.DATA_UNREAD}
    assert len(records) == 7 and report.records == 7  # as without them: none is a record


def test_main_writes_the_report(tmp_path, capsys):
    export = write_export(tmp_path / "export", base_rows() + DATA_ROWS[:1])
    totext = fake_totext(tmp_path, export, base_results() | {"9": DATA_SKIPPED})
    out = tmp_path / "out" / "corpus.json"
    assert main(["--export", str(export), "--totext", str(totext), "--out", str(out), "--no-fallback"]) == 0
    report = json.loads((tmp_path / "out" / "assemble_report.json").read_text(encoding="utf-8"))
    assert report["records"] == 7 and report["members"] == 2
    assert report["data_rows"] == {"9": assemble_module.DATA_CALENDAR}
    assert str(out) in capsys.readouterr().out
    assert main(["--export", str(export), "--totext", str(totext), "--out", str(export / "c.json")]) == 2  # never
    assert not (export / "c.json").exists()  # into the export


# ---------------------------------------------------------------- page links (addendum B)

def links_export(tmp_path: Path, pages: list[tuple[str, bytes]], files: list[tuple[str, list[str], str]],
                 **kw) -> tuple[dict[str, dict], object]:
    """pages: (url, html); files: (url, linking page urls, text), each a PDF unless its url says otherwise. Every
    page's text is its title; rows run pages first, then files."""
    rows, results = [], {}
    for n, (url, html) in enumerate(pages, start=1):
        rows.append({"type": "网页", "url": url, "file": f"pages/{n:04d}.html", "data": html, "title": f"頁{n}"})
        results[str(n)] = ({"page": {"title": f"頁{n}", "album": "", "images": []}}, f"頁{n}")
    for url, linked, text in files:
        n = len(rows) + 1
        name = url.rsplit("/", 1)[-1]
        rows.append({"type": "附件", "url": url, "file": f"files/{n:04d}-{name}",
                     "data": PDF + name.encode() if name.endswith(".pdf") else b"other " + name.encode(),
                     "title": name, "fmt": name.rsplit(".", 1)[-1], "linked": linked})
        route = {"pdf": "pdf"}.get(name.rsplit(".", 1)[-1], "")
        results[str(n)] = (({"pages": 1, "page_starts": [0]}, text) if route else
                           ({"status": "skipped", "reason": "认不出的档案格式"}, ""))
    records, report = run_assemble(tmp_path, rows, results, old=None, **kw)
    return by_id(records), report


def test_zones_keep_content_and_attachment_pages(tmp_path):
    html = notice_html(body='<p>詳見<a href="uploadfile/a.pdf">實施要點</a></p>',
                       files='<a href="uploadfile/b.pdf">說明.pdf</a>',
                       menu='<li><a href="UploadFile/Rule.pdf">借教室要點</a></li>')
    r, report = links_export(tmp_path, [(NOTICE, html)], [(ACA + "uploadfile/a.pdf", [NOTICE], BODY),
                                                          (ACA + "uploadfile/b.pdf", [NOTICE], BODY),
                                                          (ACA + "uploadfile/rule.pdf", [NOTICE], BODY)])
    assert (r["a2"]["linked"], r["a2"]["zones"]) == (NOTICE, {NOTICE: "content"})
    assert r["a2"]["contexts"] == {NOTICE: "實施要點"}
    assert (r["a3"]["linked"], r["a3"]["zones"], r["a3"]["contexts"]) == (NOTICE, {NOTICE: "attachments"},
                                                                         {NOTICE: "說明"})
    rule = r["a4"]
    assert (rule["linked"], rule["nav_linked"], rule["zones"]) == ("", NOTICE, {NOTICE: "nav"})  # a menu only
    assert report.nav_only == 1 and report.with_context == 2


def test_generic_anchor_takes_its_rows_context(tmp_path):
    html = notice_html(body='<table><tr><td>1</td><td>學生退學申請書</td><td><a href="f/q.pdf">pdf</a></td></tr>'
                            "</table>")
    r, _ = links_export(tmp_path, [(FORMS, html)], [(ACA + "f/q.pdf", [FORMS], BODY)])
    assert r["a2"]["contexts"] == {FORMS: "學生退學申請書"}
    assert r["a2"]["rows"] == {FORMS: FORMS + "#/html[1]/body[1]/form[1]/div[2]/table[1]/tbody[1]/tr[1]"}


def test_button_download_keeps_its_pages_as_unknown(tmp_path):
    html = notice_html(body='<p><input type="button" onclick="__doPostBack(\'b\',\'\')" value="下載"></p>')
    r, report = links_export(tmp_path, [(NOTICE, html)], [(ACA + "f/btn.pdf", [NOTICE, ACA + "gone.aspx"], BODY)])
    assert (r["a2"]["linked"], r["a2"]["zones"]) == (f"{NOTICE} | {ACA}gone.aspx",
                                                     {NOTICE: "unknown", ACA + "gone.aspx": "unknown"})
    assert report.button_downloads == 1


def test_pages_outside_the_export_are_unknown_and_no_button(tmp_path):
    r, report = links_export(tmp_path, [(NOTICE, notice_html())], [(ACA + "f/far.pdf", [ACA + "gone.aspx"], BODY)])
    assert (r["a2"]["linked"], r["a2"]["zones"]) == (ACA + "gone.aspx", {ACA + "gone.aspx": "unknown"})
    assert report.button_downloads == 0 and report.unknown_refs == 1  # nothing to look at: not called a button


def test_a_page_without_the_link_beside_pages_with_it_is_unknown(tmp_path):
    other = ACA + "newsdetail.aspx?nid=2&gi=2"
    with_link = notice_html(body='<p><a href="f/z.pdf">轉系辦法</a></p>')
    r, report = links_export(tmp_path, [(NOTICE, with_link), (other, notice_html(body="<p>見附件</p>"))],
                             [(ACA + "f/z.pdf", [NOTICE, other], BODY)])
    assert (r["a3"]["linked"], r["a3"]["zones"]) == (f"{NOTICE} | {other}", {NOTICE: "content", other: "unknown"})
    assert report.button_downloads == 0


def test_a_linking_page_is_found_by_its_notice_id_or_its_landing_address(tmp_path):
    spelled = ACA + "newsdetail.aspx?gi=2&nid=1"  # the same notice, its parameters in another order
    html = notice_html(body='<p><a href="f/z.pdf">轉系辦法</a></p>')
    r, _ = links_export(tmp_path, [(NOTICE, html)], [(ACA + "f/z.pdf", [spelled], BODY)])
    assert r["a2"]["zones"] == {spelled: "content"} and r["a2"]["contexts"] == {spelled: "轉系辦法"}
    short = "https://reurl.cc/Ab1"  # a short link the crawler followed to the page: listed by where it landed
    rows = [{"type": "网页", "url": short, "final": FORMS, "file": "pages/0001.html", "title": "表單",
             "data": notice_html(body='<p><a href="f/z.pdf">轉系辦法</a></p>')},
            {"type": "附件", "url": ACA + "f/z.pdf", "file": "files/0002-z.pdf", "data": PDF + b"z", "title": "z.pdf",
             "linked": [FORMS]}]
    results = {"1": ({"page": {"title": "表單", "album": "", "images": []}}, "表單"),
               "2": ({"pages": 1, "page_starts": [0]}, BODY)}
    records, _ = run_assemble(tmp_path / "landed", rows, results, old=None)
    assert by_id(records)["a2"]["zones"] == {FORMS: "content"}


# ---------------------------------------------------------------- format siblings (addendum B as rewritten, G2)

FORM_ROW = ('<table><tr><td>1</td><td>學生退學申請書</td><td><a href="f/w.pdf">pdf</a> <a href="f/w.doc">doc</a> '
            '<a href="f/w.odt">odt</a></td></tr></table>')


def test_formats_of_one_row_become_one_record(tmp_path):
    files = [(ACA + "f/w.pdf", [FORMS], BODY), (ACA + "f/w.doc", [FORMS], BODY), (ACA + "f/w.odt", [FORMS], BODY)]
    r, report = links_export(tmp_path, [(FORMS, notice_html(body=FORM_ROW))], files)
    assert "a3" not in r and "a4" not in r  # the doc and the odt (no text here: skipped) fold into the PDF
    assert [(f["_label"], f["format"], f["url"]) for f in r["a2"]["formats"]] == [
        ("a3", "doc", ACA + "f/w.doc"), ("a4", "odt", ACA + "f/w.odt")]
    named = row_ids(tmp_path / "export")
    assert [f["id"] for f in r["a2"]["formats"]] == [named["a3"], named["a4"]]  # their own ids (doc_alias)
    assert report.format_merged == 2 and report.records == 2


def test_the_format_with_the_most_text_stays_pdf_on_ties(tmp_path):
    files = [(ACA + "f/w.pdf", [FORMS], BODY), (ACA + "f/w.doc", [FORMS], BODY)]
    rows_html = notice_html(body=FORM_ROW.replace(' <a href="f/w.odt">odt</a>', ""))
    export_rows = [{"type": "网页", "url": FORMS, "file": "pages/0001.html", "data": rows_html, "title": "表單"}]
    results = {"1": ({"page": {"title": "表單", "album": "", "images": []}}, "表單")}
    for n, (url, _linked, _text) in enumerate(files, start=2):
        name = url.rsplit("/", 1)[-1]
        export_rows.append({"type": "附件", "url": url, "file": f"files/{n:04d}-{name}", "title": name,
                            "data": PDF + name.encode(), "linked": [FORMS]})  # both read as PDFs
        results[str(n)] = ({"pages": 1, "page_starts": [0]}, BODY)
    records, _ = run_assemble(tmp_path, export_rows, results, old=None)
    assert [f["_label"] for f in by_id(records)["a2"]["formats"]] == ["a3"]  # equal text: the PDF
    results["3"] = ({"pages": 1, "page_starts": [0]}, BODY + "，附註")
    records, _ = run_assemble(tmp_path / "again", export_rows, results, old=None)
    assert "a2" not in by_id(records) and [f["_label"] for f in by_id(records)["a3"]["formats"]] == ["a2"]


def test_named_anchors_merge_when_their_file_names_match(tmp_path):
    html = notice_html(body='<p><a href="f/stop.pdf">停修申請表.pdf</a> <a href="f/stop.odt">停修申請表.odt</a></p>')
    r, report = links_export(tmp_path, [(NOTICE, html)], [(ACA + "f/stop.pdf", [NOTICE], BODY),
                                                          (ACA + "f/stop.odt", [NOTICE], "")])
    assert "a3" not in r and [f["format"] for f in r["a2"]["formats"]] == ["odt"] and report.format_merged == 1


def test_different_files_in_one_row_stay_apart(tmp_path):
    html = notice_html(body='<table><tr><td>表單</td><td><a href="f/x1.pdf">甲表.pdf</a></td>'
                            '<td><a href="f/x2.doc">乙表.doc</a></td></tr></table>')
    r, report = links_export(tmp_path, [(FORMS, html)], [(ACA + "f/x1.pdf", [FORMS], BODY),
                                                         (ACA + "f/x2.doc", [FORMS], "")])
    assert {"a2", "a3"} <= set(r) and report.format_merged == 0


def test_year_grid_editions_never_merge(tmp_path):
    # two semesters of one table (anchors are dates, file names differ in a number): editions, not formats
    grid = ('<table><tr><td>114.10.15</td><td><a href="f/1141a.pdf">114.10.15</a></td>'
            '<td><a href="f/1142a.{ext}">115.3.15</a></td></tr></table>')
    for ext in ("pdf", "doc"):
        r, report = links_export(tmp_path / ext, [(FORMS, notice_html(body=grid.format(ext=ext)))],
                                 [(ACA + "f/1141a.pdf", [FORMS], BODY), (ACA + f"f/1142a.{ext}", [FORMS], BODY)])
        assert {"a2", "a3"} <= set(r) and report.format_merged == 0, ext


def test_same_format_under_format_words_stays_apart(tmp_path):
    html = notice_html(body='<table><tr><td>表單</td><td><a href="f/y1.pdf">pdf</a> <a href="f/y2.pdf">pdf</a></td>'
                            "</tr></table>")
    r, report = links_export(tmp_path, [(FORMS, html)], [(ACA + "f/y1.pdf", [FORMS], BODY),
                                                         (ACA + "f/y2.pdf", [FORMS], BODY)])
    assert {"a2", "a3"} <= set(r) and report.format_merged == 0


def test_a_group_holding_one_format_twice_is_refused(tmp_path):
    # pdf, doc and pdf under format words on one row: which PDF goes with the doc cannot be told
    html = notice_html(body='<table><tr><td>表單</td><td><a href="f/va.pdf">pdf</a> <a href="f/vb.doc">doc</a> '
                            '<a href="f/vc.pdf">pdf</a></td></tr></table>')
    r, report = links_export(tmp_path, [(FORMS, html)], [(ACA + "f/va.pdf", [FORMS], BODY),
                                                         (ACA + "f/vb.doc", [FORMS], ""),
                                                         (ACA + "f/vc.pdf", [FORMS], BODY)])
    assert {"a2", "a3", "a4"} <= set(r) and report.format_merged == 0 and report.format_refused == 1


def test_links_without_a_row_never_merge(tmp_path):
    html = notice_html(files='<a href="uploadfile/u.pdf">pdf</a><br><a href="uploadfile/u.doc">doc</a>')
    r, report = links_export(tmp_path, [(NOTICE, html)], [(ACA + "uploadfile/u.pdf", [NOTICE], BODY),
                                                          (ACA + "uploadfile/u.doc", [NOTICE], "")])
    assert r["a2"]["rows"] == {} and {"a2", "a3"} <= set(r) and report.format_merged == 0


# ---------------------------------------------------------------- the assembled corpus through the build

N2021 = ACA + "newsdetail.aspx?nid=21&gi=2"
N2025 = ACA + "newsdetail.aspx?nid=25&gi=2"
MENU = '<li><a href="UploadFile/Rule.pdf">借教室要點</a></li>'


def test_assembled_corpus_builds(tmp_path):
    """Addendum B end to end: the latest referencing notice is the parent and gives the date and the title; every
    referencing page is in doc_refs; a file linked only from menus has no parent and the server's date; the formats
    of one row are one document whose copies list the others with their formats."""
    pages = [
        (N2021, notice_html(body='<p>請下載<a href="f/stop.pdf">停修申請表.pdf</a></p>', menu=MENU,
                            title="110學年度停修公告", added="2021/3/1 上午 09:00:00")),
        (N2025, notice_html(body='<table><tr><td>1</td><td>學生停修申請表</td><td><a href="f/stop.pdf">pdf</a>'
                                 "</td></tr></table>", menu=MENU, title="114學年度停修公告",
                            added="2025/9/1 上午 09:00:00")),
        (FORMS, notice_html(body=FORM_ROW, title="表單下載")),
    ]
    files = [(ACA + "f/stop.pdf", [N2021, N2025], BODY), (ACA + "uploadfile/rule.pdf", [N2021, N2025], BODY),
             (ACA + "f/w.pdf", [FORMS], BODY), (ACA + "f/w.doc", [FORMS], BODY), (ACA + "f/w.odt", [FORMS], BODY)]
    links_export(tmp_path, pages, files)
    d = {name: "d" + rid for name, rid in row_ids(tmp_path / "export").items()}  # plan 2026-11-08 T1: stable ids
    server = tmp_path / "server_dates.json"
    server.write_text(json.dumps({ACA + "uploadfile/rule.pdf": "2024-02-03", ACA + "f/stop.pdf": "2020-01-01"}),
                      encoding="utf-8")
    (tmp_path / "aliases.yaml").write_text("groups: []\n", encoding="utf-8")
    out = tmp_path / "db" / "aca.db"
    # 11-03 (deliberate): build_db takes no series correction table any more (addendum L)
    report = build_db(tmp_path / "out" / "corpus.json", tmp_path / "export", out, server_dates=server,
                      aliases=tmp_path / "aliases.yaml")
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        def one(sql, *args):
            return conn.execute(sql, args).fetchone()

        stop = one("SELECT title, parent_id, parent_title, ident_date, ident_kind, linktext, text_source FROM docs"
                   " WHERE doc_id=?", d["a4"])
        assert stop == ("學生停修申請表", d["p2"], "114學年度停修公告", "2025-09-01", "inherited", "stop.pdf", "totext")
        refs = conn.execute("SELECT ref_id, zone, context FROM doc_refs WHERE doc_id=?", (d["a4"],)).fetchall()
        assert sorted(refs) == sorted([(d["p1"], "content", "停修申請表"), (d["p2"], "content", "學生停修申請表")])
        rule = one("SELECT title, parent_id, parent_title, ident_date, ident_kind, linktext FROM docs WHERE doc_id=?",
                   d["a5"])
        # menus only: no parent, the server's date. Changed 11-14 on purpose (plan 2026-11-14 T4): no page names
        # rule.pdf and the file name has no Han character, so its title is the first line of its own text and the
        # file name stays in its link texts (build_db(own_titles=False) keeps 「rule.pdf」: test_own_titles_1114)
        assert rule == (BODY, None, "", "2024-02-03", "server", "rule.pdf")
        assert sorted(conn.execute("SELECT ref_id, zone FROM doc_refs WHERE doc_id=?", (d["a5"],)).fetchall()) == (
            sorted([(d["p1"], "nav"), (d["p2"], "nav")]))
        assert one("SELECT title FROM docs WHERE doc_id=?", d["a6"]) == ("學生退學申請書",)
        assert one("SELECT count(*) FROM docs WHERE doc_id IN (?, ?)", d["a7"], d["a8"]) == (0,)
        copies = json.loads(one("SELECT value FROM meta WHERE key=?", "copies:" + d["a6"])[0])
        assert [(c["id"], c["url"], c["format"]) for c in copies] == [(d["a7"], ACA + "f/w.doc", "doc"),
                                                                      (d["a8"], ACA + "f/w.odt", "odt")]
        assert all(c["parent_id"] == d["p3"] for c in copies)
        assert sorted(conn.execute("SELECT alias, doc_id, why FROM doc_alias")) == sorted(
            [(d["a7"], d["a6"], "format"), (d["a8"], d["a6"], "format")])  # plan 2026-11-08 T1: read resolves them
        assert one("SELECT value FROM meta WHERE key='text_pipeline'") == ("totext",)
    finally:
        conn.close()
    assert report.excluded == {"附件:无文字(doc)(其他格式)": 1, "附件:无文字(odt)(其他格式)": 1}
    assert report.text_sources == {"totext": 6}
    assert report.counts["doc_refs"] == 5
    # changed 11-14 on purpose (plan 2026-11-14 T4): rule.pdf is no longer titled by its file name
    assert report.counts["attachments"] == {"title_from_page": 2, "title_file_name": 0, "parent": 2, "no_parent": 1}
    assert report.titles["file"] == {"documents": 1, "retitled": 1, "kept": 0, "why": {}}


BANNER = ACA + "uploadfile/mainimg/m1.jpg"
ALBUM_PHOTO = ACA + "uploadfile/pagealbum/9/p1.jpg"
FOLDER_FILE = "https://www.googleapis.com/drive/v3/files/F1?alt=media"


def menu_image_export(tmp_path: Path, linked_from_text: bool = False) -> tuple[dict[str, dict], object]:
    """A notice whose menu holds a unit banner (an <img>) and a link to a rule; below its content, a page album links
    a photo by its thumbnail; the export also has a file of a folder the notice links (no link to the file itself).
    linked_from_text: a second notice links the banner from its text, and the banner's row lists it."""
    album = ('<div id="ShowImages"><a href="UploadFile/PageAlbum/9/p1.jpg">'
             '<img src="UploadFile/PageAlbum/9/Thumbnail/p1_S.jpg"></a></div>')
    page = notice_html(body="<p>獎學金申請方式</p>", menu='<li><img src="UploadFile/mainimg/m1.jpg"></li>' + MENU,
                       after=album)
    other = ACA + "newsdetail.aspx?nid=2&gi=2"
    body = '<p><a href="UploadFile/mainimg/m1.jpg">本處標誌</a></p>' if linked_from_text else "<p>校區簡介</p>"
    rows = [{"type": "网页", "url": NOTICE, "file": "pages/0001.html", "data": page, "title": "獎學金"},
            {"type": "网页", "url": other, "file": "pages/0002.html", "title": "校區簡介",
             "data": notice_html(body=body, title="校區簡介")},
            {"type": "图片", "url": BANNER, "file": "images/0001-m1.jpg", "data": JPEG + b"m1",
             "linked": [NOTICE] + [other] * linked_from_text},
            {"type": "图片", "url": ALBUM_PHOTO, "file": "images/0002-p1.jpg", "data": JPEG + b"p1",
             "linked": [NOTICE]},
            {"type": "图片", "url": FOLDER_FILE, "file": "images/0003-f1.jpg", "data": JPEG + b"f1",
             "linked": [NOTICE]},
            {"type": "附件", "url": ACA + "uploadfile/rule.pdf", "file": "files/0001-rule.pdf", "data": PDF + b"rule",
             "title": "rule.pdf", "linked": [NOTICE], "linktext": "借教室要點"}]
    results = {"1": ({"page": {"title": "獎學金", "album": "", "images": []}}, "獎學金\n獎學金申請方式"),
               "2": ({"page": {"title": "校區簡介", "album": "", "images": []}}, "校區簡介\n校區簡介"),
               "3": ({}, "教務處 國立高雄師範大學 教務長室"),
               "4": ({}, "學習護照申請流程 線上申請 選擇領取地點"),
               "5": ({}, "正面設計說明 完稿尺寸 出血尺寸"),
               "6": ({"pages": 1, "page_starts": [0]}, BODY)}
    records, report = run_assemble(tmp_path, rows, results, old=None)
    return by_id(records), report


def test_an_image_only_menus_carry_is_no_document(tmp_path):
    # I4 (main session, 11-02): an image every page referencing it carries only as a picture outside its content area
    # (a unit banner) takes the kind 只在选单出现的图片, which the build leaves out and counts in excluded. Still
    # images: a photo a page album links (outside the content area, yet no menu) and a file of a folder the page links
    # (no page shows it); still a document: a file linked only from a menu (a standing file of its unit)
    r, report = menu_image_export(tmp_path)
    assert (r["i3"]["kind"], r["i3"]["linked"], r["i3"]["nav_linked"]) == ("只在选单出现的图片", "", NOTICE)
    assert (r["i4"]["kind"], r["i4"]["zones"]) == ("附件:图片", {NOTICE: "nav"})
    assert (r["i5"]["kind"], r["i5"]["zones"]) == ("附件:图片", {NOTICE: "nav"})
    assert (r["a6"]["linked"], r["a6"]["nav_linked"]) == ("", NOTICE)
    assert (report.images_menu_only, report.image_docs, report.nav_only) == (1, 2, 1)
    (tmp_path / "aliases.yaml").write_text("groups: []\n", encoding="utf-8")
    out = tmp_path / "db" / "aca.db"
    assert out.resolve().is_relative_to(tmp_path.resolve()) and not out.resolve().is_relative_to(
        build.SKELETON.resolve())  # a test build never writes outside tmp_path
    built = build_db(tmp_path / "out" / "corpus.json", tmp_path / "export", out, server_dates=None,
                     aliases=tmp_path / "aliases.yaml")  # 11-03 (deliberate): no correction table (addendum L)
    assert built.excluded == {"只在选单出现的图片": 1}
    d = {name: "d" + rid for name, rid in row_ids(tmp_path / "export").items()}  # plan 2026-11-08 T1: stable ids
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        docs = dict(conn.execute("SELECT doc_id, parent_id FROM docs WHERE doc_id IN (?, ?, ?, ?)",
                                 (d["i3"], d["i4"], d["i5"], d["a6"])))
    finally:
        conn.close()
    assert docs == {d["i4"]: None, d["i5"]: None, d["a6"]: None}


def test_an_image_one_page_links_from_its_text_is_no_menu_image(tmp_path):
    # I4 asks every reference to be a menu's: the banner a second notice links from its text stays an image, with
    # that notice as its text reference and the first as a menu reference
    r, report = menu_image_export(tmp_path, linked_from_text=True)
    other = ACA + "newsdetail.aspx?nid=2&gi=2"
    assert (r["i3"]["kind"], r["i3"]["linked"], r["i3"]["nav_linked"]) == ("附件:图片", other, NOTICE)
    assert r["i3"]["contexts"] == {other: "本處標誌"}
    assert (report.images_menu_only, report.image_docs) == (0, 3)
