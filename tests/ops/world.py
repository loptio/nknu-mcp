"""Two made-up sites of one host as the three stores hold them (plan 2026-11-15 T3): each site's export folder with its
manifest and files, the to-text cache, the assembled corpus, and a library whose rows point at them, all under one
root, as on the lab PC (/work). No real data: the school's host is school.example, the names are masked with O."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from nknu.content import ids
from nknu.content.corpus import LIST_PAGER
from nknu.content.dates import OWN_FIELD
from nknu.content.ids import PRESS_MARK, location_key, record_id
from nknu.content.schema import create_schema
from nknu.core.textnorm import tokenize
from nknu.totext.cache import FULL, Cache, sha256_file

BOM = b"\xef\xbb\xbf"  # what the export puts before a page it saves
SITE = "school.example"
BOARD = "school.example/board"  # a second site of the host: the copy of the notice is its page
EXPORTS = {SITE: "archive-export/sites/school.example-20261115-000000",
           BOARD: "archive-export/sites/school.example_board-20261115-000000"}
CORPORA = {SITE: "research/pipeline_2026-11-02/corpus/school.example.json",
           BOARD: "research/pipeline_2026-11-02/corpus/school.example_board.json"}
NAMES = {SITE: "範例處", BOARD: "範例處公告欄"}
EXPORT, CORPUS = EXPORTS[SITE], CORPORA[SITE]
TOTEXT = "research/pipeline_2026-11-02/totext"
NOTICE_URL = "https://school.example/news.aspx?id=7"
TWIN_URL = "http://school.example/news.aspx?id=7"  # the notice's address spelt another way: one id, a second row
COPY_URL = "https://school.example/board/news.aspx?id=7"
FORM_URL = "https://school.example/files/form.pdf"
SCAN_URL = "https://school.example/files/scan.pdf"
LOST_URL = "https://school.example/files/lost.pdf"
POSTER_URL = "https://school.example/files/poster.png"
PAGER_URL = "https://school.example/list.aspx?page=2"
PRESS_URL = "https://school.example/list.aspx" + PRESS_MARK + "下載簡章"  # a row a button on the list brought
MENU_URL = "https://school.example/banner.png"
AWAY_URL = "https://files.example/old/calendar.pdf"  # a file the site links on another host: listed in its manifest
NOTICE_TEXT = "115學年度第1學期停修申請自11月3日起受理。\n申請人：王OO。\n逾期不予受理。"
FORM_TEXT = "停修申請表\n一、申請資格：修課學生。\n二、繳交期限：11月16日。"
SCAN_TEXT = "停修流程圖\n先填表再送件"


def doc_id(url: str) -> str:
    return "d" + record_id(location_key(url))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.exports = {site: root / folder for site, folder in EXPORTS.items()}
        self.export = self.exports[SITE]
        self.cache = Cache(root / TOTEXT / "cache")
        self.rows: dict[str, list[dict]] = {site: [] for site in EXPORTS}
        self.records: dict[str, list[dict]] = {site: [] for site in EXPORTS}
        self._site: dict[int, str] = {}  # id(row) -> its site
        self._made = 0
        self.db = root / "library.db"
        self.conn = sqlite3.connect(self.db)
        create_schema(self.conn)
        for site, folder in EXPORTS.items():
            self.conn.execute("INSERT INTO sites(site, name, unit, data_as_of, missing_pages, missing_files, docs,"
                              " export) VALUES (?, ?, '教務處', '2026-11-08', 0, 0, 3, ?)", (site, NAMES[site], folder))
        self.conn.execute("INSERT INTO meta(key, value) VALUES ('built_at', '2026-11-15T00:54:29+08:00')")

    # ---- the three stores

    def row(self, url: str, kind: str, file: str, data: bytes | None, title: str, site: str = SITE, **more) -> dict:
        """One manifest row of site; with data, its file is written into that export folder. The row's sha256 is the
        archived original's, as the export writes it: a page is re-saved with a BOM, so its exported file has another
        (the to-text cache is keyed by the exported file's)."""
        rows = self.rows[site]
        row = {"序号": str(len(rows) + 1), "类型": kind, "网址": url, "最后落地网址": url, "本地档案": file,
               "标题": title, "网页上写的日期": "", "日期原文": "", "完整度": "完整", "缺什么": "", "结果": "抓到了",
               "原因": "", "抓取时间": "2026-11-08T03:12:00+08:00", "大小": str(len(data or b"")),
               "sha256": _sha(data.removeprefix(BOM)) if data is not None else "",
               "格式": file.rsplit(".", 1)[-1] if file else "",
               "链到它的网页": "", "链接文字": "", "怎么来的": "链接", "备注": "", "存档编号": f"u{self._made + 80}",
               "按钮编号": "", "最后修改时间": "", "日期来源": "", "来源": "", "页别": "", "列表上的类别": "", **more}
        self._made += 1
        if data is not None and file:
            path = self.exports[site] / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        rows.append(row)
        self._site[id(row)] = site
        return row

    def converted(self, row: dict, text: str, *, full: str = "", **result) -> None:
        """The cache entry of the row's file."""
        extras = {FULL: full.encode("utf-8")} if full else {}
        base = {"route": "pdf", "status": "ok", "reason": "", "pages": 1, "ocr_pages": [], "chars": len(text),
                "ocr_lines": 0, "ocr_mean_score": None, "seconds": 0.4, "page_kind": "",
                "tools": {"pipeline": "totext-2", "docling": "2.145.0"}, "first_file": row["本地档案"]}
        exported = self.exports[self._site[id(row)]] / row["本地档案"]
        self.cache.save(sha256_file(exported), {**base, **result}, text, extras)

    def record(self, row: dict, kind: str, text: str, **more) -> dict:
        """The corpus record assemble makes of the row, under the key the build gives the row (a button's row: its
        button's)."""
        site = self._site[id(row)]
        key = ids.manifest_keys([row])[int(row["序号"])]
        rec = {"id": record_id(key), "key": key, "kind": kind, "url": row["网址"],
               "file": row["本地档案"], "title": row["标题"], "text": text, "linktext": "", "linked": [],
               "source": "totext", "totext_status": "ok", "site": site, "fetched": row["抓取时间"], "page_date": "",
               "page_date_raw": "", "date_source": row["日期来源"], "modified": row["最后修改时间"], "origin": "",
               "area_mark": "", "list_kind": row["页别"], "list_category": "", "under_read": None, **more}
        self.records[site].append(rec)
        return rec

    def doc(self, url: str, kind: str, title: str, text: str, *chunks: str, **columns) -> str:
        """One library document with its chunks (text[start:end]) and their keyword index rows."""
        did = doc_id(url)
        base = {"doc_id": did, "kind_class": kind, "kind_raw": "newsdetail" if kind == "notice" else "附件:pdf",
                "title": title, "url": url, "unit": "教務處", "has_text": 1, "text": text, "chars": len(text),
                "site": SITE, "text_source": "totext", **columns}
        self.conn.execute(f"INSERT INTO docs({', '.join(base)}) VALUES ({', '.join('?' * len(base))})",
                          list(base.values()))
        self.conn.execute("INSERT INTO sections(doc_id, sec_id, heading, start, end) VALUES (?, 0, '', 0, ?)",
                          (did, len(text)))
        start = 0
        for chunk in chunks or (text,):
            start = text.index(chunk, start)
            cur = self.conn.execute("INSERT INTO chunks(doc_id, sec_id, start, end) VALUES (?, 0, ?, ?)",
                                    (did, start, start + len(chunk)))
            self.conn.execute("INSERT INTO chunk_fts(rowid, title_tokens, body_tokens, unit_tokens) VALUES (?, ?, ?, '')",
                              (cur.lastrowid, " ".join(tokenize(title)), " ".join(tokenize(chunk))))
            start += len(chunk)
        return did

    def write(self) -> None:
        """Each site's manifest.json and corpus file, and the build's note of what it read (meta inputs_json, with
        each corpus file's sha256), after the rows and records are made."""
        inputs = []
        for site, export in self.exports.items():
            export.mkdir(parents=True, exist_ok=True)
            manifest = {"网站": site, "名称": NAMES[site], "导出时间": "2026-11-09T01:13:41+08:00",
                        "项目": self.rows[site]}
            (export / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            corpus = self.root / CORPORA[site]
            corpus.parent.mkdir(parents=True, exist_ok=True)
            corpus.write_text(json.dumps(self.records[site], ensure_ascii=False), encoding="utf-8")
            inputs.append({"corpus": CORPORA[site], "corpus_sha256": sha256_file(corpus), "export": EXPORTS[site],
                           "site": site})
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('inputs_json', ?)", (json.dumps(inputs),))
        self.conn.commit()


def build(root: Path) -> World:
    """On the first site: a notice with a date, a unit, a cohort and two chunks, listed a second time under another
    spelling of its address; a form with a text layer hanging on the notice; a scan read by OCR; a file whose cache
    entry is gone; a poster (a picture); a list's pager page, a row a button of the list brought, a menu banner and
    a file on another host, none of which the library holds. On the second site: the notice's copy at another
    address, merged away."""
    w = World(root)
    page = BOM + "<html><body><h1>停修公告</h1><p>內文</p></body></html>".encode()
    notice = w.row(NOTICE_URL, "网页", "pages/0001-news-停修公告.html", page, "115-1 停修公告",
                   网页上写的日期="2026-10-21", 日期原文="115.10.21", 日期来源=OWN_FIELD)
    w.converted(notice, NOTICE_TEXT, full="首頁 / 最新消息\n" + NOTICE_TEXT + "\n版權所有", route="page", pages=0,
                page_kind="newsdetail|cnknu:nothing|pages-4:0000000000", seconds=0.001)
    w.record(notice, "newsdetail", NOTICE_TEXT, page_date="2026-10-21", page_date_raw="115.10.21", under_read=False)
    nid = w.doc(NOTICE_URL, "notice", "115-1 停修公告", NOTICE_TEXT, "115學年度第1學期停修申請自11月3日起受理。",
                "申請人：王OO。\n逾期不予受理。", ident_date="2026-10-21", ident_kind="published", period_ay=115,
                period_sem=1, grp="註冊組", unit_line="單位：教務處 註冊組", expires=1, valid_ay=115,
                rank_date="2026-10-21")
    w.conn.execute("INSERT INTO dates VALUES (?, 'published', '2026-10-21', '115.10.21', '公告頁日期欄')", (nid,))
    w.conn.execute("INSERT INTO dates VALUES (?, 'period_start', '2026-11-03', '11月3日起', '正文期間')", (nid,))
    w.conn.executemany("INSERT INTO doc_scope(doc_id, relation, unit_id) VALUES (?, ?, ?)",
                       [(nid, "publisher", "教務處"), (nid, "applies_to", "國立高雄師範大學")])
    w.conn.execute("INSERT INTO applies_to(doc_id, unit, cohort_from, cohort_exact, raw) VALUES (?, '教務處', 113, NULL,"
                   " '113學年度起入學')", (nid,))

    copy = w.row(COPY_URL, "网页", "pages/0001-board-停修公告.html", page + b"<!-- board -->", "115-1 停修公告（公告欄）",
                 site=BOARD)
    w.converted(copy, NOTICE_TEXT, route="page", pages=0, page_kind="page|body|pages-4:0000000000")
    w.record(copy, "page", NOTICE_TEXT)
    w.conn.execute("INSERT INTO doc_alias(alias, doc_id, why) VALUES (?, ?, 'copy')", (doc_id(COPY_URL), nid))
    w.conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)",
                   ("copies:" + nid, json.dumps([{"id": doc_id(COPY_URL), "url": COPY_URL}])))

    form = w.row(FORM_URL, "附件", "files/0003-停修申請表.pdf", b"%PDF-1.7 form", "停修申請表",
                 最后修改时间="Mon, 01 Sep 2026 02:00:00 GMT", 链到它的网页=NOTICE_URL, 链接文字="申請表")
    w.converted(form, FORM_TEXT, pages=2)
    w.record(form, "附件:pdf:有文字层", FORM_TEXT)
    fid = w.doc(FORM_URL, "attachment", "停修申請表", FORM_TEXT, parent_id=nid, parent_title="115-1 停修公告",
                linktext="申請表", ident_date="2026-10-21", ident_kind="inherited")
    w.conn.execute("INSERT INTO dates VALUES (?, 'inherited', '2026-10-21', '115.10.21', '所屬公告')", (fid,))
    w.conn.execute("INSERT INTO doc_refs(doc_id, ref_id, zone, context) VALUES (?, ?, 'attachments', '申請表')",
                   (fid, nid))

    scan = w.row(SCAN_URL, "附件", "files/0004-流程圖.pdf", b"%PDF-1.7 scan", "停修流程圖")
    # ocr_pages as the to-text step writes it: one entry per page it read by OCR, pages counted from 1
    ocr = [{"page": n, "why": "few_chars", "reason": "没有可用的文字层", "lines": 1, "mean_score": 0.93} for n in (1, 2)]
    w.converted(scan, SCAN_TEXT, pages=2, ocr_pages=ocr, ocr_lines=2, ocr_mean_score=0.93, seconds=6.2)
    w.record(scan, "附件:pdf:无文字层", SCAN_TEXT)
    w.doc(SCAN_URL, "attachment", "停修流程圖", SCAN_TEXT, has_text=0)

    lost = w.row(LOST_URL, "附件", "files/0005-遺失.pdf", b"%PDF-1.7 lost", "舊表單")
    w.record(lost, "附件:pdf:有文字层", "舊表單內容")
    w.doc(LOST_URL, "attachment", "舊表單", "舊表單內容")

    pager = w.row(PAGER_URL, "网页", "pages/0006-list-第2頁.html", BOM + b"<html>list</html>", "最新消息",
                  页别=LIST_PAGER)
    w.converted(pager, "115-1 停修公告\n114-2 停修公告", route="page", pages=0,
                page_kind="default|body|pages-4:0000000000")
    w.record(pager, "default", "115-1 停修公告\n114-2 停修公告")

    w.row(MENU_URL, "图片", "", None, "", 结果="没抓到", 完整度="缺", 缺什么="档案", 原因="HTTP 404")
    w.row(AWAY_URL, "附件", "", None, "calendar.pdf", 结果="链接已失效：跳回学校首页", 完整度="缺", 缺什么="档案")

    # the notice's address spelt another way, listed after it and without a file: one id, two rows
    w.row(TWIN_URL, "网页", "", None, "115-1 停修公告", 结果="和另一个网址是同一页", 完整度="")

    press = w.row(PRESS_URL, "网页", "pages/0010-list-下載簡章.html", BOM + b"<html>pressed</html>", "下載簡章",
                  按钮编号="row-key-1", 链接文字="下載簡章")
    w.converted(press, "簡章下載清單", route="page", pages=0, page_kind="newslist|body|pages-4:0000000000")
    w.record(press, "newslist#翻页/按钮", "簡章下載清單")

    poster = w.row(POSTER_URL, "附件", "files/0011-海報.png", b"\x89PNG poster", "停修宣導海報")
    w.converted(poster, "停修宣導", route="image", pages=0, ocr_lines=1, ocr_mean_score=0.9)
    w.record(poster, "附件:图片", "停修宣導")
    w.doc(POSTER_URL, "attachment", "停修宣導海報", "停修宣導")
    w.write()
    return w
