"""`nknu archive export` (owner 10-27: 「等他爬完导出成一个资料夹：每页一个档、附件保留原档，再附一份清单（网址、标题、日期、
完整度）」; the folder's shape is Claude's proposal the owner agreed to, how it is done Claude's). A small archive of one
site: a list walked by its pager button (page 2 is a press's answer), three notices got by GET, a notice got by a
detail button, a file got by a download button, an attachment, a Drive file read through the API, a body picture, a
layout picture the rules leave out, an attachment that never connected and one left for round two. The export must
write each page once, keep every file's bytes as archived, list what was not got with its reason, find the dates the
pages state (民国 years too) and leave the others blank, refuse a folder that is not empty or that git would track,
and change nothing in the archive. No real names: the notices are made up."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nknu.archive import export, outcome
from nknu.archive.frontier import Frontier
from nknu.crawl.adapter import Target
from nknu.crawl.fetch import Snapshot
from nknu.crawl.warc import WarcSink

SITE = "c.nknu.edu.tw/aca"
BASE = "https://c.nknu.edu.tw/aca/"
FETCHED = "2026-10-27T01:02:03+00:00"
DRIVE_ID = "1AbCdEfGhIjKlMnOp"

LIST1 = """<html><head><meta charset="big5"><title>國立高雄師範大學教務處</title></head><body>
<h2>最新消息</h2><table id="MasterGrid"><tr><th>日期</th><th>主題</th></tr>
<tr><td>2025/10/01</td><td><a href="newsview.aspx?nid=1">114學年度第1學期加退選公告</a></td></tr>
<tr><td>2025/09/30</td><td><a href="newsview.aspx?nid=2">新生註冊須知</a></td></tr></table></body></html>"""
PAGE2 = """<html><head><meta charset="big5"><title>國立高雄師範大學教務處</title></head><body>
<h2>最新消息</h2><table id="MasterGrid"><tr><th>日期</th><th>主題</th></tr>
<tr><td>2026/10/02</td><td><a href="newsview.aspx?nid=3">校慶補假公告</a></td></tr>
<tr><td>2025/01/05</td><td><a href="javascript:__doPostBack('MasterGrid$ctl03$lbSel','')">寒假宿舍申請</a></td></tr>
</table></body></html>"""
DETAIL1 = """<html><head><meta charset="big5"><title>國立高雄師範大學教務處</title></head><body>
<div id="DataFormView"><span id="DataFormView_TitleLabel">114學年度第1學期加退選公告</span>
<p>發佈日期：114/10/01</p><p>王O明同學請洽教務處。</p><img src="/aca/uploadfile/pic.jpg">
<a href="/aca/uploadfile/rule.pdf">選課規則</a></div><img src="/aca/images/banner.jpg"></body></html>"""
DETAIL2 = """<html><head><title>國立高雄師範大學教務處</title></head><body><div id="DataFormView">
<span id="DataFormView_TitleLabel">新生註冊須知</span><p>公告日期：2025年9月30日</p></div></body></html>"""
DETAIL3 = """<html><head><title>國立高雄師範大學教務處</title></head><body><div id="DataFormView">
<span id="DataFormView_TitleLabel">校慶補假公告</span><table><tr><td>日　　期</td><td>2026/10/2 上午 12:00:00</td></tr>
</table></div></body></html>"""
ABOUT = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>教務處簡介</h2>
<p>教務處負責課務、註冊與招生。</p></body></html>"""
PRESSED = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>最新消息</h2>
<p>寒假宿舍申請：請於期限內申請。</p></body></html>"""
RULE_PDF = b"%PDF-1.4 rule of course selection\n%%EOF\n"
DRIVE_PDF = b"%PDF-1.7 admission brochure\n%%EOF\n"
FORM_PDF = b"%PDF-1.5 transcript form\n%%EOF\n"
PIC = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00 a picture"
DRIVE_META = json.dumps({"id": DRIVE_ID, "name": "招生簡章.pdf", "mimeType": "application/pdf"}).encode()
CALENDAR = "aca.example@gmail.com"
EVENTS = json.dumps({"kind": "calendar#events", "items": [{"summary": "期中考", "start": {"date": "2026-11-09"}}]},
                    ensure_ascii=False).encode()


class Arch:
    def __init__(self, root: Path):
        self.root = root
        self.ids: dict[str, int] = {}
        self.sha: dict[str, str] = {}


def _add(f: Frontier, **cols) -> int:
    row = {"host": "c.nknu.edu.tw", "site": SITE, "owner_site": SITE, "tier": 0, "kind": "page", "kind0": "page",
           "depth": 1, "found_via": "link", "state": "done", "status": 200, "fetched_at": FETCHED}
    row.update(cols)
    row.setdefault("path", row["url"].split("?", 1)[0])
    with f.lock:
        return f.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


def _action(f: Frontier, list_id: int, **cols) -> int:
    row = {"list_id": list_id, "epoch": 1, "step": 1, "kind": "detail", "queue": "ip:140.127.62.110",
           "host": "c.nknu.edu.tw", "site": SITE, "profile": "cnknu/MasterGrid", "form_src_warc": "warc/x.warc.gz",
           "form_src_offset": 0, "target": "MasterGrid", "argument": "", "state": "done", "status": 200,
           "done": 1793070000.0}
    row.update(cols)
    with f.lock:
        return f.conn.execute(f"INSERT INTO action({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


def _res(f: Frontier, page: int, key: str, role: str, need: str = "must", **cols) -> None:
    f.add_page_res(page, [{"res_key": key, "how": cols.pop("how", "get"), "role": role, "need": need, **cols}])


def make_archive(root: Path) -> Arch:
    """The small archive described at the top, its frontier.db closed (as after a run) when this returns."""
    a = Arch(root)
    sink = WarcSink(root / "warc", "ip_140.127.62.110", 1, datetime(2026, 10, 27, tzinfo=UTC))
    rel = str(sink.path.relative_to(root))

    def put(name: str, url: str, ctype: str, body: bytes, headers: dict | None = None) -> tuple[int, str]:
        sha = hashlib.sha256(body).hexdigest()
        snap = Snapshot(Target(url), url, 200, {"content-type": ctype, **(headers or {})}, body, None,
                        datetime(2026, 10, 27, 1, 2, 3, tzinfo=UTC), sha)
        offset, _len = sink.write(snap, {})
        a.sha[name] = sha
        return offset, sha

    f = Frontier(root / "frontier.db")
    f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
        (SITE, "教务处", "全校重要", "1", "c.nknu", BASE + "newslist.aspx"))
    f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:ok', ?, 'ok', 5)", (SITE,))
    html = "text/html; charset=utf-8"

    def page(name: str, url: str, body: str, **cols) -> int:
        data = body.encode("utf-8")
        offset, sha = put(name, url, html, data)
        uid = _add(f, url=url, final_url=url, ctype=html, bytes=len(data), sha256=sha, warc=rel, warc_offset=offset,
                   sniff="html", page_type="t:ok", title="國立高雄師範大學教務處", **cols)
        a.ids[name] = uid
        return uid

    lst = page("list", BASE + "newslist.aspx", LIST1, found_via="seed", depth=0, page_role="list")
    d1 = page("d1", BASE + "newsview.aspx?nid=1", DETAIL1)
    d2 = page("d2", BASE + "newsview.aspx?nid=2", DETAIL2)
    d3 = page("d3", BASE + "newsview.aspx?nid=3", DETAIL3)
    page("about", BASE + "about.aspx", ABOUT)
    for name, key in (("d1", "item:k1"), ("d2", "item:k2"), ("d3", "item:k3")):
        _res(f, lst, key, "item", need="no", why="item", res_id=a.ids[name])

    def file(name: str, url: str, body: bytes, ctype: str, *, kind: str = "file", sniff: str = "pdf",
             headers: dict | None = None, **cols) -> int:
        offset, sha = put(name, url, ctype, body, headers)
        uid = _add(f, url=url, final_url=url, kind=kind, kind0=kind, tier=2, ctype=ctype, bytes=len(body), sha256=sha,
                   warc=rel, warc_offset=offset, sniff=sniff, **cols)
        a.ids[name] = uid
        return uid

    # the attachment: no file name in the row, Content-Disposition in the archived answer (UTF-8, RFC 5987)
    rule = file("rule", BASE + "uploadfile/rule.pdf", RULE_PDF, "application/pdf",
                headers={"content-disposition": "attachment; filename*=UTF-8''%E9%81%B8%E8%AA%B2%E8%A6%8F%E5%89%87.pdf"})
    _res(f, d1, BASE + "uploadfile/rule.pdf", "file", res_id=rule, text="選課規則")
    pic = file("pic", BASE + "uploadfile/pic.jpg", PIC, "image/jpeg", kind="image", sniff="jpeg")
    _res(f, d1, BASE + "uploadfile/pic.jpg", "img", res_id=pic)
    # a layout picture: never fetched (links.image_need: chrome), no url row
    _res(f, d1, BASE + "images/banner.jpg", "img", need="no", why="chrome")
    # an attachment that never connected, after its three tries
    broken = _add(f, url=BASE + "uploadfile/broken.pdf", kind="file", kind0="file", tier=2, state="failed",
                  reason="connect", attempts=3, status=None, fetched_at=None)
    a.ids["broken"] = broken
    _res(f, d1, BASE + "uploadfile/broken.pdf", "file", res_id=broken, text="附件二")
    # an off-campus file: round two's
    later = _add(f, url="https://www.edu.tw/files/plan.pdf", host="www.edu.tw", site="www.edu.tw", kind="file",
                 kind0="file", tier=4, state="later", reason="round2:offsite_file", status=None, fetched_at=None)
    a.ids["later"] = later
    _res(f, d1, "https://www.edu.tw/files/plan.pdf", "file", need="round2", res_id=later, text="教育部計畫")
    # a Drive file: its metadata (files.get) and its content (alt=media), read through the API
    meta = file("drive_meta", f"https://www.googleapis.com/drive/v3/files/{DRIVE_ID}?fields=id", DRIVE_META,
                "application/json", kind="drive", sniff="json", host="www.googleapis.com", site="www.googleapis.com")
    content = file("drive", f"https://www.googleapis.com/drive/v3/files/{DRIVE_ID}?alt=media", DRIVE_PDF,
                   "application/pdf", host="www.googleapis.com", site="www.googleapis.com", found_via="drive")
    f.x("INSERT INTO drive_item(url_id, file_id, name, mime, size, content_id) VALUES (?,?,?,?,?,?)",
        (meta, DRIVE_ID, "招生簡章.pdf", "application/pdf", len(DRIVE_PDF), content))
    _res(f, d2, f"drive:{DRIVE_ID}", "drive", how="drive_api", res_id=content, text="招生簡章")
    # an embedded Google calendar, read through the Calendar API: its events, as they came, go to data/
    cal = file("calendar", f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR}/events?singleEvents=true",
               EVENTS, "application/json", kind="calendar", sniff="json", host="www.googleapis.com",
               site="www.googleapis.com")
    _res(f, d3, f"calendar:{CALENDAR}:p1", "calendar", how="calendar_api", res_id=cal, text="行事曆")

    # the list's buttons: its page 2 (pager), a notice behind a detail button, a file behind a download button
    off, sha = put("page2", BASE + "newslist.aspx", html, PAGE2.encode())
    pager = _action(f, lst, kind="pager", step=2, row_key="p2", row_text="2", target="MasterGrid",
                    argument="Page$2", sniff="html", ctype=html, bytes=len(PAGE2.encode()), sha256=sha, warc=rel,
                    warc_offset=off)
    off, sha = put("pressed", BASE + "newslist.aspx", html, PRESSED.encode())
    detail = _action(f, lst, kind="detail", step=2, row_key="k9", row_text="寒假宿舍申請",
                     target="MasterGrid$ctl03$lbSel", sniff="html", ctype=html, bytes=len(PRESSED.encode()),
                     sha256=sha, warc=rel, warc_offset=off)
    off, sha = put("form", BASE + "newslist.aspx", "application/pdf", FORM_PDF)
    download = _action(f, lst, kind="download", step=2, row_key="k8", row_text="成績單申請表",
                       target="MasterGrid$ctl04$lbFile", sniff="pdf", ctype="application/pdf", bytes=len(FORM_PDF),
                       sha256=sha, warc=rel, warc_offset=off, filename="成績單申請表.pdf")
    a.ids.update(pager=pager, detail=detail, download=download)
    _res(f, lst, "pb:pager:p2", "pager", how="postback", action_id=pager, text="2")
    _res(f, lst, "pb:detail:k9", "detail", how="postback", action_id=detail, text="寒假宿舍申請")
    _res(f, lst, "pb:download:k8", "download", how="postback", action_id=download, text="成績單申請表")
    f.x("INSERT INTO listwalk(list_id, site, profile, epoch, pages, rows, rows_unique, claimed, ended_by, ok) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", (lst, SITE, "cnknu", 1, 2, 4, 3, 4, "last_page", 1))
    sink.close()
    f.close()
    return a


def hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def rows_of(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def done(tmp_path):
    arch = make_archive(tmp_path / "archive")
    before = hashes(arch.root)
    out = tmp_path / "out" / "aca"
    got = export.export_site(arch.root, SITE, out)
    return arch, before, out, got


def by_url(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["网址"]: r for r in rows}


# ---------------------------------------------------------------- the folder


def test_each_page_is_one_file_and_the_buttons_answers_are_pages_too(done):
    _arch, _before, out, got = done
    pages = sorted(p.name for p in (out / "pages").iterdir())
    # the list, its page 2 (a press), three notices by GET, a notice by its button, the about page
    assert len(pages) == 7 and len(set(pages)) == 7
    assert all(p.endswith(".html") and p[:4].isdigit() and p[4] == "-" for p in pages)
    assert got.counts["网页"]["got"] == 7
    rows = rows_of(out / "manifest.csv")
    pressed = [r for r in rows if r["类型"] == "网页" and "寒假宿舍申請" in r["标题"]]
    assert len(pressed) == 1 and "按钮" in pressed[0]["怎么来的"] and pressed[0]["本地档案"].startswith("pages/")
    second = [r for r in rows if r["类型"] == "网页" and r["网址"] == BASE + "newslist.aspx#第2页"]
    assert len(second) == 1 and second[0]["本地档案"].startswith("pages/") and "翻页" in second[0]["怎么来的"]
    assert second[0]["标题"] == "最新消息（第 2 页）"
    assert "寒假宿舍申請" in (out / second[0]["本地档案"]).read_text(encoding="utf-8-sig")


def test_pages_are_the_crawlers_decoding_saved_as_utf8_and_otherwise_unchanged(done):
    _arch, _before, out, _got = done
    row = by_url(rows_of(out / "manifest.csv"))[BASE + "newsview.aspx?nid=1"]
    data = (out / row["本地档案"]).read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")  # a BOM: a browser reads it as UTF-8 whatever the meta says
    text = data.decode("utf-8-sig")
    assert text == DETAIL1  # the school's words, its meta charset="big5" included, nothing changed
    assert "big5" in (out / "README.md").read_text(encoding="utf-8")


def test_files_keep_every_byte_and_their_names(done):
    arch, _before, out, _got = done
    files = {p.name: p for p in (out / "files").iterdir()}
    assert len(files) == 3
    names = sorted(n.split("-", 1)[1] for n in files)
    # Content-Disposition's name, the Drive item's name, the download button's file name
    assert names == sorted(["選課規則.pdf", "招生簡章.pdf", "成績單申請表.pdf"])
    shas = {hashlib.sha256(p.read_bytes()).hexdigest() for p in files.values()}
    assert shas == {arch.sha["rule"], arch.sha["drive"], arch.sha["form"]}
    images = list((out / "images").iterdir())
    assert len(images) == 1 and images[0].read_bytes() == PIC and images[0].name.endswith("pic.jpg")
    data = list((out / "data").iterdir())  # the calendar's events, as they came
    assert len(data) == 1 and data[0].read_bytes() == EVENTS and data[0].suffix == ".json"
    cal = [r for r in rows_of(out / "manifest.csv") if r["类型"] == "资料"]
    assert len(cal) == 1 and CALENDAR in cal[0]["标题"] and cal[0]["本地档案"] == f"data/{data[0].name}"
    rows = by_url(rows_of(out / "manifest.csv"))
    for url, name in ((BASE + "uploadfile/rule.pdf", "rule"), (BASE + "uploadfile/pic.jpg", "pic")):
        r = rows[url]
        assert r["sha256"] == arch.sha[name] == hashlib.sha256((out / r["本地档案"]).read_bytes()).hexdigest()
        assert r["结果"] == outcome.RESULT_LABEL["ok"] and r["大小"] == str((out / r["本地档案"]).stat().st_size)


def test_the_manifest_has_its_columns_and_the_results_in_words(done):
    _arch, _before, out, _got = done
    rows = rows_of(out / "manifest.csv")
    for col in ("类型", "网址", "最后落地网址", "本地档案", "标题", "网页上写的日期", "日期原文", "完整度", "缺什么", "结果",
                "抓取时间", "大小", "sha256", "格式", "链到它的网页", "链接文字"):
        assert col in rows[0], col
    with open(out / "manifest.json", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["项目"] == rows  # the same rows, the same words
    by = by_url(rows)
    d1 = by[BASE + "newsview.aspx?nid=1"]
    assert d1["类型"] == "网页" and d1["标题"] == "114學年度第1學期加退選公告"
    assert d1["完整度"] == outcome.LEVEL_LABEL["partial"]  # its attachment never connected
    assert "缺附件" in d1["缺什么"] and "连接失败" in d1["缺什么"]
    assert d1["抓取时间"].startswith("2026-10-27T09:02:03+08:00")  # Taipei
    rule = by[BASE + "uploadfile/rule.pdf"]
    assert rule["类型"] == "附件" and rule["标题"] == "選課規則.pdf" and rule["格式"] == "pdf"
    assert rule["链到它的网页"] == BASE + "newsview.aspx?nid=1" and rule["链接文字"] == "選課規則"
    assert rule["完整度"] == "抓到了"
    drive = by[f"https://www.googleapis.com/drive/v3/files/{DRIVE_ID}?alt=media"]
    assert drive["类型"] == "附件" and drive["标题"] == "招生簡章.pdf" and drive["本地档案"].startswith("files/")
    assert "云端硬盘" in drive["怎么来的"]
    about = by[BASE + "about.aspx"]
    assert about["标题"] == "教務處簡介" and about["完整度"] == outcome.LEVEL_LABEL["complete"]


def test_what_was_not_got_is_listed_with_its_reason_in_the_manifest_and_in_missing(done):
    _arch, _before, out, got = done
    rows = by_url(rows_of(out / "manifest.csv"))
    missing = by_url(rows_of(out / "missing.csv"))
    broken = rows[BASE + "uploadfile/broken.pdf"]
    assert broken["本地档案"] == "" and broken["结果"] == outcome.RESULT_LABEL["failed"]
    assert broken["分类"] == "连接失败" and broken["链接文字"] == "附件二" and broken["标题"] == "broken.pdf"
    assert broken["链到它的网页"] == BASE + "newsview.aspx?nid=1"
    later = rows["https://www.edu.tw/files/plan.pdf"]
    assert later["本地档案"] == "" and later["结果"] == outcome.RESULT_LABEL["later"] and later["分类"] == "下一轮"
    banner = rows[BASE + "images/banner.jpg"]
    assert banner["类型"] == "图片" and banner["本地档案"] == "" and banner["分类"] == "规则不抓"
    assert banner["结果"] == outcome.RESULT_LABEL["rule"] and "版面" in banner["原因"]
    assert broken["原因"] == "连不上这台服务器" and later["原因"] == "校外的档案"
    assert set(missing) == {BASE + "uploadfile/broken.pdf", "https://www.edu.tw/files/plan.pdf",
                            BASE + "images/banner.jpg"}
    assert all(r["本地档案"] == "" for r in missing.values())
    # 连接失败 first, 规则不抓 last
    assert [r["分类"] for r in rows_of(out / "missing.csv")] == ["连接失败", "下一轮", "规则不抓"]
    assert got.counts["附件"] == {"got": 3, "missing": 2}
    assert got.counts["图片"] == {"got": 1, "missing": 1}


def test_dates_the_pages_state_are_found_in_every_writing_and_left_blank_when_none(done):
    _arch, _before, out, _got = done
    rows = by_url(rows_of(out / "manifest.csv"))
    d1, d2, d3 = (rows[BASE + f"newsview.aspx?nid={n}"] for n in (1, 2, 3))
    assert d1["网页上写的日期"] == "2025-10-01" and "114/10/01" in d1["日期原文"]  # 民国 114 年
    assert d2["网页上写的日期"] == "2025-09-30" and "2025年9月30日" in d2["日期原文"]
    assert d3["网页上写的日期"] == "2026-10-02" and "2026/10/2" in d3["日期原文"]  # 「日　　期」
    assert rows[BASE + "about.aspx"]["网页上写的日期"] == "" and rows[BASE + "about.aspx"]["日期原文"] == ""
    # a list holds many notices, each with its own date: none is the page's
    assert rows[BASE + "newslist.aspx"]["网页上写的日期"] == ""


@pytest.mark.parametrize(("text", "want"), [
    ("發佈日期：114/10/01", "2025-10-01"),
    ("公告日期 114.10.01", "2025-10-01"),
    ("更新：民國114年10月1日", "2025-10-01"),
    ("发布 2025-10-01 10:20", "2025-10-01"),
    ("Date: 2024/2/29", "2024-02-29"),
    ("Last updated: 2025/10/01", "2025-10-01"),
    ("日　　期\n2026/10/2 上午 12:00:00", "2026-10-02"),
    ("更新日期：2025年13月1日", None),  # no such month
    ("電話：07-7172930", None),
    ("Mandate 2025/10/01", None),  # 'date' inside another word is no label
    ("本校成立於 1954/10/01", None),  # no label
    ("日期：99/1/5", "2010-01-05"),  # 民国 99
    ("日期：25/10/01", None),  # 民国 25 would be 1936: taken for no date
])
def test_find_date(text, want):
    got = export.find_date(text)
    assert (got[0] if got else None) == want


# ---------------------------------------------------------------- the README and the state


def test_the_readme_says_the_site_the_state_the_counts_and_the_warning(done):
    _arch, _before, out, got = done
    text = (out / "README.md").read_text(encoding="utf-8")
    assert "教务处" in text and SITE in text
    assert "学生全名和学号" in text and "公开" in text
    assert "导出时" in text and got.state_words in text
    for words in ("网页", "附件", "图片", "资料", "连接失败", "下一轮", "规则不抓", "manifest.csv", "missing.csv"):
        assert words in text, words
    assert "程序找的" in text  # the dates are the program's finding, with the writing beside them
    # the list: two pages walked, three rows as recorded, four stated; four rows' notices exported (three by GET, one
    # by its button)
    assert f"| 最新消息（{BASE}newslist.aspx） | 2 | 3 | 4 | 4 | 翻到最后一页 |" in text
    # the few that matter most are named
    assert "uploadfile/broken.pdf" in text and "连不上这台服务器" in text


def test_a_file_button_that_brought_its_file_is_no_missing_button(tmp_path):
    """ee's file buttons (design §7.4): the POST answers the file's address, the file is a url row of its own."""
    arch = make_archive(tmp_path / "archive")
    f = Frontier(arch.root / "frontier.db")
    d1 = arch.ids["d1"]
    aid = _action(f, d1, kind="click", step=0, row_key="click:k=1,id=7", row_text="下載",
                  target="https://c.nknu.edu.tw/aca/fileAjax.html", location=BASE + "uploadfile/rule.pdf")
    _res(f, d1, "click:fileAjax.html(k=1,id=7)", "click", how="click", action_id=aid, text="下載")
    f.close()
    export.export_site(arch.root, SITE, tmp_path / "out")
    rows = rows_of(tmp_path / "out" / "manifest.csv")
    assert not [r for r in rows if r["类型"] == "按钮"]
    assert by_url(rows)[BASE + "uploadfile/rule.pdf"]["本地档案"].startswith("files/")


def test_an_empty_notice_page_is_said():
    from selectolax.lexbor import LexborHTMLParser

    empty = ("<html><body><div id='DataFormView'><span id='DataFormView_TitleLabel'></span>"
             "<span id='DataFormView_ContentsLabel'></span></div><h2>最新消息</h2></body></html>")
    full = DETAIL1.replace('<p>王O明', '<span id="DataFormView_ContentsLabel">王O明').replace("教務處。</p>", "教務處。</span>")
    assert export.empty_detail_area(LexborHTMLParser(empty), BASE + "newsview.aspx?nid=9", SITE)
    assert not export.empty_detail_area(LexborHTMLParser(full), BASE + "newsview.aspx?nid=1", SITE)
    assert not export.empty_detail_area(LexborHTMLParser(empty), BASE + "about.aspx", SITE)  # no notice page


def test_a_run_holding_the_archive_is_said(tmp_path):
    from nknu.archive.crawler import hold_run_lock

    arch = make_archive(tmp_path / "archive")
    lock = hold_run_lock(arch.root)
    try:
        got = export.export_site(arch.root, SITE, tmp_path / "out")
    finally:
        lock.close()
    assert got.running
    assert "爬虫正在跑" in (tmp_path / "out" / "README.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- refusals, and the archive untouched


def test_the_archive_is_not_changed(done):
    arch, before, _out, _got = done
    after = hashes(arch.root)
    assert {k: after[k] for k in before} == before
    # SQLite's own files of a read-only reader of a WAL database are all that may appear
    assert set(after) - set(before) <= {"frontier.db-wal", "frontier.db-shm"}


def test_an_out_folder_that_is_not_empty_is_refused(tmp_path):
    arch = make_archive(tmp_path / "archive")
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with pytest.raises(export.ExportRefused, match="不是空的"):
        export.export_site(arch.root, SITE, out)
    assert sorted(p.name for p in out.iterdir()) == ["keep.txt"]
    empty = tmp_path / "empty"
    empty.mkdir()
    export.export_site(arch.root, SITE, empty)  # an empty folder is fine
    assert (empty / "manifest.csv").exists()


def test_an_out_folder_git_would_track_is_refused_and_an_ignored_one_is_not(tmp_path):
    arch = make_archive(tmp_path / "archive")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("/archive*/\n")
    with pytest.raises(export.ExportRefused, match="archive-export"):
        export.export_site(arch.root, SITE, repo / "exports" / "aca")
    assert not (repo / "exports").exists()
    export.export_site(arch.root, SITE, repo / "archive-export" / "aca")
    assert (repo / "archive-export" / "aca" / "manifest.csv").exists()


def test_an_out_folder_inside_the_archive_is_refused(tmp_path):
    arch = make_archive(tmp_path / "archive")
    with pytest.raises(export.ExportRefused, match="存档"):
        export.export_site(arch.root, SITE, arch.root / "export")


def test_an_unknown_site_is_refused(tmp_path):
    arch = make_archive(tmp_path / "archive")
    with pytest.raises(export.ExportRefused, match="网站"):
        export.export_site(arch.root, "c.nknu.edu.tw/nope", tmp_path / "out")


def test_the_command(tmp_path):
    from typer.testing import CliRunner

    from nknu.admin.cli import app

    arch = make_archive(tmp_path / "archive")
    out = tmp_path / "out"
    r = CliRunner().invoke(app, ["archive", "export", "--dir", str(arch.root), "--site", SITE, "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "网页" in r.output and "README.md" in r.output and (out / "manifest.csv").exists()
    r = CliRunner().invoke(app, ["archive", "export", "--dir", str(arch.root), "--site", SITE, "--out", str(out)])
    assert r.exit_code == 1 and "不是空的" in r.output
