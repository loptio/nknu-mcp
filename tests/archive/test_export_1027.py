"""`nknu archive export` (owner 10-27: 「等他爬完导出成一个资料夹：每页一个档、附件保留原档，再附一份清单（网址、标题、日期、
完整度）」; the folder's shape is Claude's proposal the owner agreed to, how it is done Claude's). A small archive of one
site: a list walked by its pager button (page 2 is a press's answer), three notices got by GET, a notice got by a
detail button, a file got by a download button, an attachment, a Drive file read through the API, a body picture, a
layout picture the rules leave out, an attachment that never connected and one left for round two. The export must
write each page once, keep every file's bytes as archived, list what was not got with its reason, find the dates the
pages state (民国 years too) and leave the others blank, refuse a folder that is not empty or that git would track,
and change nothing in the archive. No real names: the notices are made up."""

from __future__ import annotations

import codecs
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
# a made-up calendar ID (calendar IDs are shaped like email addresses: nknu check allows this one in this file)
CALENDAR = "c_0123456789abcdef@group.calendar.google.com"
EVENTS = json.dumps({"kind": "calendar#events", "items": [{"summary": "期中考", "start": {"date": "2026-11-09"}}]},
                    ensure_ascii=False).encode()
HTML = "text/html; charset=utf-8"


class Arch:
    def __init__(self, root: Path):
        self.root = root
        self.ids: dict[str, int] = {}
        self.sha: dict[str, str] = {}
        self.f: Frontier | None = None
        self.sink: WarcSink | None = None

    def open(self) -> Arch:
        """Its frontier.db and its WARC file open to add to (a test's own case on top of the archive described above);
        close() after."""
        self.f = Frontier(self.root / "frontier.db")
        self.sink = WarcSink(self.root / "warc", "ip_140.127.62.110", 1, datetime(2026, 10, 27, tzinfo=UTC))
        return self

    def close(self) -> None:
        self.sink.close()
        self.f.close()

    @property
    def rel(self) -> str:
        return str(self.sink.path.relative_to(self.root))

    def put(self, name: str, url: str, ctype: str, body: bytes, headers: dict | None = None,
            status: int = 200) -> tuple[int, str]:
        sha = hashlib.sha256(body).hexdigest()
        snap = Snapshot(Target(url), url, status, {"content-type": ctype, **(headers or {})}, body, None,
                        datetime(2026, 10, 27, 1, 2, 3, tzinfo=UTC), sha)
        offset, _len = self.sink.write(snap, {})
        self.sha[name] = sha
        return offset, sha

    def page(self, name: str, url: str, body: str | bytes, ctype: str = HTML, **cols) -> int:
        data = body.encode("utf-8") if isinstance(body, str) else body
        offset, sha = self.put(name, url, ctype, data)
        cols.setdefault("title", "國立高雄師範大學教務處")
        uid = _add(self.f, url=url, final_url=url, ctype=ctype, bytes=len(data), sha256=sha, warc=self.rel,
                   warc_offset=offset, sniff="html", page_type="t:ok", **cols)
        self.ids[name] = uid
        return uid

    def file(self, name: str, url: str, body: bytes, ctype: str, *, kind: str = "file", sniff: str = "pdf",
             headers: dict | None = None, **cols) -> int:
        offset, sha = self.put(name, url, ctype, body, headers)
        cols.setdefault("sha256", sha)
        uid = _add(self.f, url=url, final_url=url, kind=kind, kind0=kind, tier=2, ctype=ctype, bytes=len(body),
                   warc=self.rel, warc_offset=offset, sniff=sniff, **cols)
        self.ids[name] = uid
        return uid


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
    a = Arch(root).open()
    f, put, page, file, html = a.f, a.put, a.page, a.file, HTML
    rel = a.rel
    f.x("INSERT INTO site_plan(site, name, imp, round, platform, home) VALUES (?,?,?,?,?,?)",
        (SITE, "教务处", "全校重要", "1", "c.nknu", BASE + "newslist.aspx"))
    f.x("INSERT INTO page_type(key, site, verdict, n_pages) VALUES ('t:ok', ?, 'ok', 5)", (SITE,))

    lst = page("list", BASE + "newslist.aspx", LIST1, found_via="seed", depth=0, page_role="list")
    d1 = page("d1", BASE + "newsview.aspx?nid=1", DETAIL1)
    d2 = page("d2", BASE + "newsview.aspx?nid=2", DETAIL2)
    d3 = page("d3", BASE + "newsview.aspx?nid=3", DETAIL3)
    page("about", BASE + "about.aspx", ABOUT)
    for name, key in (("d1", "item:k1"), ("d2", "item:k2"), ("d3", "item:k3")):
        _res(f, lst, key, "item", need="no", why="item", res_id=a.ids[name])

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
    a.close()
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
    assert got.counts["附件"] == {"got": 3, "to_do": 0, "missing": 2}
    assert got.counts["图片"] == {"got": 1, "to_do": 0, "missing": 1}


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
    # review 10-27 #4: 「更新時間」 and the like (体育室's recordview pages), 中華民國, full-width digits
    ("更新時間：2025.9.17", "2025-09-17"),
    ("發布時間：2025/10/01 10:00", "2025-10-01"),
    ("公告時間 114/10/01", "2025-10-01"),
    ("張貼日期：2025-10-01", "2025-10-01"),
    ("Posted on 2025/10/01", "2025-10-01"),
    ("發布日期：中華民國114年10月1日", "2025-10-01"),
    ("日期：２０２５／１０／０１", "2025-10-01"),
    ("公告日期：１１４．１０．０１", "2025-10-01"),
    # a 發布 or 公告 label is the page's date before a bare 日期 (an event's) that comes first
    ("活動日期：2025/12/24\n公告日期：2025/10/01", "2025-10-01"),
    ("公告期間：2025/10/01～2025/10/31", None),  # a period, no label of a date
])
def test_find_date(text, want):
    got = export.find_date(text)
    assert (got[0] if got else None) == want


def test_find_date_keeps_the_words_as_the_page_writes_them():
    assert export.find_date("日期：２０２５／１０／０１ 上午") == ("2025-10-01", "日期：２０２５／１０／０１")
    assert export.find_date("更新時間：2025.9.17") == ("2025-09-17", "更新時間：2025.9.17")


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


# ---------------------------------------------------------------- the review's findings (10-27)


def _export(arch: Arch, tmp_path: Path, name: str = "out"):
    out = tmp_path / name
    got = export.export_site(arch.root, SITE, out)
    return got, out, by_url(rows_of(out / "manifest.csv")), (out / "README.md").read_text(encoding="utf-8")


# a notice the school deleted, as c.nknu shows it (10-26 pilot, 体育室: 27 such pages): the site's template, its
# DataFormView there with neither the title nor the text label in it
NOTICE_GONE = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>最新消息</h2>
<div id="News"><div id="DataFormView"></div></div><div id="footer">本網站著作權屬於教務處</div></body></html>"""
NOTICE_BLANK = """<html><head><title>國立高雄師範大學教務處</title></head><body><h2>最新消息</h2><div id="DataFormView">
<span id="DataFormView_TitleLabel"></span><span id="DataFormView_ContentsLabel"> </span></div></body></html>"""


def test_an_empty_notice_page_says_so_in_what_it_lacks_and_the_readme_names_it(tmp_path):
    """Review 10-27 #2: such a page is 抓到了, 完整 by the views (the crawler's empty check claims nothing when the text
    area is not on the page; whether it should is the main session's call): the export says it in 缺什么, where a
    reader filtering on 完整度 still sees it, and names each such page in the README."""
    arch = make_archive(tmp_path / "archive").open()
    arch.page("gone4", BASE + "newsview.aspx?nid=4", NOTICE_GONE, found_via="common_crawl")
    arch.page("gone5", BASE + "newsview.aspx?nid=5", NOTICE_BLANK, found_via="common_crawl")
    arch.close()
    _got, _out, rows, readme = _export(arch, tmp_path)
    gone4, gone5 = rows[BASE + "newsview.aspx?nid=4"], rows[BASE + "newsview.aspx?nid=5"]
    assert "找不到内文区" in gone4["缺什么"] and "内文区是空的" in gone5["缺什么"]
    for r in (gone4, gone5):
        assert r["完整度"] == outcome.LEVEL_LABEL["complete"] and r["分类"] == "抓到了"  # the views' judgement, kept
        assert f"| {r['网址']} |" in readme  # each one named in the README's own table
    assert "空的公告页" in readme and "2 页" in readme
    for n in (1, 2, 3):  # notices with their words are not
        assert "内文区" not in rows[BASE + f"newsview.aspx?nid={n}"]["缺什么"]


def test_the_readme_counts_what_is_still_queued_from_the_manifest_other_sites_rows_too(tmp_path):
    """Review 10-27 #3: what is left was counted among the site's own rows only, so a short link another site owns,
    which the site's page uses and which is still queued, read as 「待抓清单里没有剩下的」."""
    arch = make_archive(tmp_path / "archive").open()
    short = _add(arch.f, url="https://pse.is/AbC12", host="pse.is", site="vd.nknu.edu.tw", owner_site="vd.nknu.edu.tw",
                 kind="shortlink", kind0="shortlink", tier=1, state="pending", status=None, fetched_at=None)
    _res(arch.f, arch.ids["about"], "https://pse.is/AbC12", "shortlink", res_id=short, text="報名表")
    _add(arch.f, url=BASE + "todo.aspx", state="pending", status=None, fetched_at=None)
    arch.close()
    got, _out, rows, readme = _export(arch, tmp_path)
    for url in ("https://pse.is/AbC12", BASE + "todo.aspx"):
        assert rows[url]["分类"] == "还在排队 / 等重试" and rows[url]["本地档案"] == ""
    assert "没有剩下的" not in readme
    assert got.left_urls == 2 and got.left_presses == 0
    assert "还有 2 个网址、0 个按钮" in readme and "其中 1 个归属别的网站" in readme


def test_a_redirect_row_sharing_its_landing_pages_file_says_what_that_file_is(tmp_path):
    """Review 10-27 #5: the row's size, sha256 and format were the 301's own (153 bytes) beside the landing page's
    file, and its completeness 抓到了 where the landing page's said 部分完整."""
    arch = make_archive(tmp_path / "archive").open()
    home = BASE.rstrip("/")
    off, sha = arch.put("moved", home, HTML, b"<a href=newslist.aspx>moved</a>",
                        headers={"location": BASE + "newslist.aspx"}, status=301)
    _add(arch.f, url=home, final_url=BASE + "newslist.aspx", status=301, bytes=31, sha256=sha, warc=arch.rel,
         warc_offset=off, ctype=HTML, found_via="seed", depth=0)
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    moved, landing = rows[home], rows[BASE + "newslist.aspx"]
    assert moved["本地档案"] == landing["本地档案"] != ""
    for col in ("大小", "sha256", "格式", "完整度", "缺什么", "抓取时间"):
        assert moved[col] == landing[col], col
    assert moved["结果"] == outcome.RESULT_LABEL["redirect"] and moved["最后落地网址"] == BASE + "newslist.aspx"


def test_what_a_page_lacks_names_the_schools_share_and_a_row_with_no_file_shows_its_category(tmp_path):
    """Review 10-27 #6: 缺什么 said 「完整（学校那边缺）」 again, not what is missing; a sign-in page with no file
    showed 完整（学校那边缺） as its completeness."""
    arch = make_archive(tmp_path / "archive").open()
    rules = arch.page("rules", BASE + "rules.aspx", ABOUT.replace("教務處簡介", "教務規章"))
    old = _add(arch.f, url=BASE + "uploadfile/old.pdf", kind="file", kind0="file", tier=2, status=404)
    _res(arch.f, rules, BASE + "uploadfile/old.pdf", "file", res_id=old, text="舊規章")
    _add(arch.f, url=BASE + "admin_login.aspx", flags="login", sniff="html", page_type="t:ok")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path)
    r = rows[BASE + "rules.aspx"]
    assert r["完整度"] == outcome.LEVEL_LABEL["complete_school"]
    assert r["缺什么"] == "学校那边已不存在、要登录或有问题的资源（1 个）"
    login = rows[BASE + "admin_login.aspx"]
    assert login["本地档案"] == "" and login["完整度"] == "要登录" and login["分类"] == "要登录"


def test_the_csv_names_twenty_pages_that_use_an_item_and_the_json_all_of_them(tmp_path):
    """Review 10-27 #7: a layout picture on every page listed every page (8774 characters on 体育室; past Excel's
    32767 a cell on a site of 900 pages)."""
    arch = make_archive(tmp_path / "archive").open()
    for n in range(25):
        pid = _add(arch.f, url=BASE + f"p{n:02d}.aspx", state="pending", status=None, fetched_at=None)
        _res(arch.f, pid, BASE + "images/logo.gif", "img", need="no", why="chrome")
    arch.close()
    _got, out, rows, _readme = _export(arch, tmp_path)
    cell = rows[BASE + "images/logo.gif"]["链到它的网页"].split(" | ")
    assert len(cell) == 21 and cell[0] == BASE + "p00.aspx" and cell[-1] == "…等 25 页（全部在 manifest.json）"
    data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    full = next(r for r in data["项目"] if r["网址"] == BASE + "images/logo.gif")
    assert len(full["链到它的网页"].split(" | ")) == 25


def test_the_readme_counts_what_was_got_but_still_needs_a_look(tmp_path):
    """Review 10-27 #8: a page or a file with a file but 要人看 (a 404 text served as a page) was counted among 抓到
    only; for files and pictures the README did not show it at all."""
    arch = make_archive(tmp_path / "archive").open()
    arch.page("thin", BASE + "main.aspx", "<html><body>404: File not found</body></html>", flags="thin")
    arch.file("odd", BASE + "uploadfile/odd.pdf", b"%PDF-1.4 odd\n", "application/pdf", flags="decode")
    _res(arch.f, arch.ids["about"], BASE + "uploadfile/odd.pdf", "file", res_id=arch.ids["odd"], text="奇怪的檔")
    arch.close()
    got, _out, rows, readme = _export(arch, tmp_path)
    for url in (BASE + "main.aspx", BASE + "uploadfile/odd.pdf"):
        assert rows[url]["本地档案"] and rows[url]["分类"] == "要人看", url
    assert "| 类型 | 抓到（已导出） | 其中有档但还要处理 | 没抓到 |" in readme
    assert "| 网页 | 8 | 1 | 0 |" in readme and "| 附件 | 4 | 1 | 2 |" in readme
    assert got.counts["网页"]["to_do"] == 1 and got.counts["附件"]["to_do"] == 1
    assert "main.aspx" in readme and "uploadfile/odd.pdf" in readme  # few: named


def test_a_big5_page_is_saved_as_utf8_with_the_same_words(tmp_path):
    """Review 10-27 #9: a page that is really Big5 (no charset in the header, the crawler's third try) comes out as
    UTF-8 with a BOM, word for word."""
    words = ('<html><head><meta charset="big5"><title>體育室</title></head><body><h2>場地借用須知</h2>'
             "<p>發佈日期：114/09/17</p></body></html>")
    arch = make_archive(tmp_path / "archive").open()
    arch.page("big5", BASE + "big5.aspx", words.encode("big5"), ctype="text/html")
    arch.close()
    _got, out, rows, _readme = _export(arch, tmp_path)
    r = rows[BASE + "big5.aspx"]
    data = (out / r["本地档案"]).read_bytes()
    assert data.startswith(codecs.BOM_UTF8) and data.decode("utf-8-sig") == words
    assert r["标题"] == "場地借用須知" and r["网页上写的日期"] == "2025-09-17"
    assert r["sha256"] == hashlib.sha256(words.encode("big5")).hexdigest()  # the archived bytes'


def test_a_file_name_from_the_server_stays_inside_the_folder_and_shows_what_it_is(tmp_path):
    """Review 10-27 #9, #10: ../, a backslash, a control character and a right-to-left override in the name the
    server gives."""
    arch = make_archive(tmp_path / "archive").open()
    evil = "attachment; filename*=UTF-8''..%2F..%2Fx%5Cy%01z%E2%80%AEfdp.exe.pdf"
    arch.file("evil", BASE + "uploadfile/evil.pdf", b"%PDF-1.4 evil\n", "application/pdf",
              headers={"content-disposition": evil})
    _res(arch.f, arch.ids["about"], BASE + "uploadfile/evil.pdf", "file", res_id=arch.ids["evil"], text="附件")
    arch.close()
    _got, out, rows, _readme = _export(arch, tmp_path)
    path = rows[BASE + "uploadfile/evil.pdf"]["本地档案"]
    assert path.startswith("files/") and path.count("/") == 1
    assert (out / path).resolve().parent == (out / "files").resolve()
    name = path.split("/", 1)[1]
    assert "\\" not in name and "\x01" not in name and "\u202e" not in name and name.endswith(".pdf")
    tops = sorted(p.relative_to(out).parts[0] for p in out.rglob("*") if p.is_file())
    assert tops == sorted(["manifest.csv", "manifest.json", "missing.csv", "README.md", "data", *["files"] * 4,
                           "images", *["pages"] * 7])


@pytest.mark.parametrize(("name", "want"), [
    ("../../etc/passwd", "_.._etc_passwd"),
    ("a\\b\x01c.pdf", "a_b_c.pdf"),
    ("\u202egpj.exe", "gpj.exe"),  # a right-to-left override: shown as exe.jpg otherwise
    ("報告\u200b.pdf", "報告.pdf"),  # a zero-width space
    ("  .hidden. ", "hidden"),
])
def test_safe_name(name, want):
    assert export.safe_name(name) == want


def test_a_file_whose_bytes_are_not_what_the_archive_recorded_is_not_exported(tmp_path):
    arch = make_archive(tmp_path / "archive").open()
    arch.file("bad", BASE + "uploadfile/bad.pdf", b"%PDF-1.4 bad\n", "application/pdf", sha256="0" * 64)
    _res(arch.f, arch.ids["about"], BASE + "uploadfile/bad.pdf", "file", res_id=arch.ids["bad"], text="壞檔")
    arch.close()
    _got, out, rows, readme = _export(arch, tmp_path)
    r = rows[BASE + "uploadfile/bad.pdf"]
    assert r["本地档案"] == "" and r["分类"] == "要人看" and "sha256 对不上" in r["备注"]
    assert len(list((out / "files").iterdir())) == 3 and "uploadfile/bad.pdf" in readme


def test_two_buttons_with_the_same_words_on_the_same_page_are_two_addresses(tmp_path):
    """Review 10-27 #10: a pressed notice's address is its list's with the button's words: two buttons with the same
    words were one address."""
    arch = make_archive(tmp_path / "archive").open()
    body = PRESSED.replace("請於期限內申請", "第二梯次").encode()
    off, sha = arch.put("pressed2", BASE + "newslist.aspx", HTML, body)
    aid = _action(arch.f, arch.ids["list"], kind="detail", step=2, row_key="k10", row_text="寒假宿舍申請",
                  target="MasterGrid$ctl05$lbSel", sniff="html", ctype=HTML, bytes=len(body), sha256=sha,
                  warc=arch.rel, warc_offset=off)
    _res(arch.f, arch.ids["list"], "pb:detail:k10", "detail", how="postback", action_id=aid, text="寒假宿舍申請")
    arch.close()
    _got, out, _rows, _readme = _export(arch, tmp_path)
    pressed = [r for r in rows_of(out / "manifest.csv") if r["标题"] == "寒假宿舍申請"]
    assert len(pressed) == 2 and len({r["网址"] for r in pressed}) == 2 and len({r["本地档案"] for r in pressed}) == 2
    assert all(r["网址"].startswith(BASE + "newslist.aspx#按钮=寒假宿舍申請") for r in pressed)


def test_every_row_and_press_of_the_site_is_in_the_manifest_or_counted_in_the_readme(tmp_path, monkeypatch):
    """Review 10-27 #9: the export passes over a row in a dozen places (a script, Drive's bookkeeping, a press a newer
    one stands for …). Whatever none of its branches lists or counts is listed as 要人看 at the end, never left out:
    here _others and _presses are made to list nothing."""
    arch = make_archive(tmp_path / "archive").open()
    arch.file("cc", BASE + "uploadfile/cc.pdf", b"%PDF-1.4 cc\n", "application/pdf", found_via="common_crawl")
    arch.close()
    _got, _out, rows, _readme = _export(arch, tmp_path, "whole")
    assert rows[BASE + "uploadfile/cc.pdf"]["本地档案"].startswith("files/")  # nothing uses it: listed all the same
    assert not [r for r in rows.values() if "没处理到" in r["备注"]]
    monkeypatch.setattr(export.Builder, "_others", lambda self, covered: None)
    monkeypatch.setattr(export.Builder, "_presses", lambda self: None)
    _got, out, rows, readme = _export(arch, tmp_path, "gap")
    gaps = [r for r in rows_of(out / "manifest.csv") if "导出程式没处理到" in r["备注"]]
    want = {f"u{arch.ids['cc']}", *(f"a{arch.ids[k]}" for k in ("pager", "detail", "download"))}
    assert {r["存档编号"] for r in gaps} == want
    assert all(r["分类"] == "要人看" and r["本地档案"] == "" for r in gaps)
    assert "导出程式没处理到" in readme
