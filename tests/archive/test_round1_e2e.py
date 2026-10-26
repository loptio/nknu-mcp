"""The round-1 crawl end to end (design §17 step 6–8 wired into the crawler; §7.4–§7.6, §8.2–§8.3, §10.4, §11): one
`seed_round1` with the 10-25 page-type results, then one `Crawler.run()` with its worker threads, on a fake network
(httpx.MockTransport: no request leaves this machine) and a fake clock. The pages are minimal shapes of the school's
platforms (titles made up, no person's name):

- a c.nknu list walked by ASP.NET postback over 3 pages (MasterGrid / Page$N), its details by GET with their
  attachments, one detail empty (the id probe says so too: gone_id);
- SSO 公告中心: its 7 categories and the list of all of them (共 N 筆 added up), a category of 2 pages, details by
  GET, one attachment the school's page says is broken (bad_link);
- an SSO cLv2 unit page whose details and download are buttons (row presses), of a type the 10-25 test never saw;
- 国际处's tabs (one GET per tab, the shown one too; a tab's content holds an attachment);
- 电子系's download buttons (one POST per (k, number); one answers no address: mismatch);
- a Google Site with a Drive folder (a shortcut, a sub-folder, a native document), an embedded calendar of two pages
  and a Google form whose description links a Drive file.

The ledger is checked as a person would: each page's completeness, every button pressed, no page complete that
lacks anything, and the politeness and scope audits empty."""

from __future__ import annotations

import gzip
import io
import json
import threading
import time
import zipfile
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest

from nknu.archive import crawler as mod
from nknu.archive import google_api, outcome, pagetypes, scope, seeds
from nknu.archive.frontier import utc_now
from nknu.archive.monitor import politeness, scope_audit
from tests.archive.plans import plan, site

KEY = "test-key-not-a-real-one"  # monkeypatched into GOOGLE_API_KEY; fetch.py puts it in X-Goog-Api-Key only
PDF = b"%PDF-1.4\n%made up for a test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

ACA_LIST = "https://c.nknu.edu.tw/aca/NewsList.aspx?gi=1"
SSO_HOME = "https://sso.nknu.edu.tw/News/SubjectList.aspx?cat=SCHOOLADMIN"
CLV2 = "https://sso.nknu.edu.tw/services/site/index.aspx?clv2=105000"
OIA = "https://oia.nknu.edu.tw/sister-schools.php"
EE = "https://ee.nknu.edu.tw/download.html"
COUNSEL = "https://sites.google.com/mail.nknu.edu.tw/counseling/home"
PLAN = plan(
    site("c.nknu.edu.tw/aca", imp="全校重要", name="教务处", home=ACA_LIST),
    site("sso.nknu.edu.tw/news", imp="全校重要", name="SSO 公告中心", home=SSO_HOME),
    site("sso.nknu.edu.tw/webservices", rnd="resource"),
    site("sso.nknu.edu.tw/site/105000", name="某单位", home=CLV2),
    site("oia.nknu.edu.tw", name="国际处", home=OIA),
    site("ee.nknu.edu.tw", name="电子系", home=EE),
    site("sites.google.com/mail.nknu.edu.tw/counseling", name="谘商中心", home=COUNSEL),
)
QUEUES = {"c.nknu.edu.tw": "ip:140.127.62.110", "sso.nknu.edu.tw": "ip:140.127.40.173",
          "oia.nknu.edu.tw": "ip:140.127.5.10", "ee.nknu.edu.tw": "ip:140.127.5.11", "sites.google.com": "google"}

FOLDER, FILE1, DOC1, SHORT1 = "1FoLdErAbCdEfGhIjK", "1FiLeOnEaBcDeFgHiJ", "1DoCuMeNtAbCdEfGh", "1ShOrTcUtAbCdEfGh"
TARGET1, SUB1, FILE2, FILE3 = "1TaRgEtAbCdEfGhIj", "1SuBfOlDeRaBcDeFg", "1FiLeTwOaBcDeFgHi", "1FiLeThReEaBcDeFgH"
CALENDAR = "nknu-test-calendar@example.org"  # made up: no real calendar
FORM = "https://docs.google.com/forms/d/e/1FAIpQLSdXyZaBcDeFgHiJkL/viewform"

# ---------------------------------------------------------------- the pages (shapes of the school's, titles made up)


def _hidden(state: str) -> str:
    return ('<input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />'
            '<input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />'
            f'<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="{state}" />')


def _pager(target: str, n: int, last: int) -> str:
    cells = "".join(f"<td><span>{i}</span></td>" if i == n else
                    f'<td><a href="javascript:__doPostBack(&#39;{target}&#39;,&#39;Page${i}&#39;)">{i}</a></td>'
                    for i in range(1, last + 1))
    return f"<table><tr>{cells}</tr></table>"


ACA_ROWS = {1: [("2026.10.22", 663, "轉學考簡章公告"), ("2026.10.21", 662, "停修申請時間")],
            2: [("2026.10.07", 661, "選課注意事項")], 3: [("2026.09.30", 660, "某項說明")]}
ACA_DETAILS = {663: ("轉學考簡章公告", "報名日期與方式如附件。", "u663.pdf"),
               662: ("停修申請時間", "請於期限內申請。", "u662.pdf"),
               661: ("選課注意事項", "請詳閱附件。", "u661.pdf"),
               660: ("某項說明", "", None)}  # an empty detail: no body text, no attachment


def aca_list(n: int) -> str:
    rows = "".join(f'<tr class="RowStyle"><td>{d}</td><td>教務組</td><td><a href="NewsDetail.aspx?Nid={nid}&amp;GI=1">'
                   f"{t}</a></td></tr>" for d, nid, t in ACA_ROWS[n])
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>最新消息</title></head><body>'
            f'<form method="post" action="./newslist.aspx?gi=1" id="form1">{_hidden(f"VS-ACA-P{n}")}'
            '<table id="MasterGrid"><tr class="HeaderStyle"><th>發佈日期</th><th>組別</th><th>標題</th></tr>'
            f'{rows}<tr class="PagerStyle"><td colspan="3">{_pager("MasterGrid", n, 3)}</td></tr></table>'
            "</form></body></html>")


def aca_detail(title: str, body: str, file: str | None) -> str:
    link = f'<a href="uploadfile/{file}" target="_blank">{file}</a>' if file else ""
    return (f'<html><head><meta charset="utf-8"><title>最新消息</title></head><body>'
            f'<form method="post" action="./NewsDetail.aspx" id="form1"><div id="MainContent"><span id="lbl_Title">{title}</span><span id="lbl_Contents">{body}</span>'
            f'<span id="lbl_FileLink">{link}</span></div></form></body></html>')


def unid(n: int) -> str:
    return "AAAA" + "0" * 35 + str(n)


SSO_CATS = ("SCHOOLADMIN", "ENTRANCEEXAM", "CAMPUS", "Lights", "LEARNING", "JOB", "GOOD")
SSO_NOTICES = {1: ("校園資安提醒", "2026-10-01", "F1"), 2: ("停修申請時間公告", "2026-10-21", "F2"),
               3: ("廠商資料總整理", "2026-09-07", "F3")}
SSO_PAGES = {("SCHOOLADMIN", 1): [1, 2], ("SCHOOLADMIN", 2): [3], ("", 1): [1, 2, 3]}  # "": every category


def sso_list(cat: str, n: int) -> str:
    notices = SSO_PAGES.get((cat, n), [])
    last = 2 if cat == "SCHOOLADMIN" else 1
    total = 3 if cat in ("SCHOOLADMIN", "") else 0
    rows = "".join(f'<tr><td><span id="gvFull_ctl0{i + 2}_Category">行政資訊</span></td><td>'
                   f'<a id="gvFull_ctl0{i + 2}_linkSubject_short" href="https://sso.nknu.edu.tw/News/lastNews.aspx?'
                   f'UNID={unid(k)}">{SSO_NOTICES[k][0]}</a></td><td><span id="gvFull_ctl0{i + 2}_StartDate">'
                   f"{SSO_NOTICES[k][1]}</span></td></tr>" for i, k in enumerate(notices)) or \
        '<tr><td colspan="3">查無資料</td></tr>'
    pager = f'<tr><td colspan="3">{_pager("gvFull", n, last)}</td></tr>' if last > 1 else ""
    action = f"./subjectlist.aspx?cat={cat}" if cat else "./subjectlist.aspx"
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>公告</title></head><body>'
            f'<form method="post" action="{action}" id="uForm">{_hidden(f"VS-SSO-{cat or "ALL"}-P{n}")}'
            '<select name="ddlCategory" id="ddlCategory"><option value="all">全部分類</option></select>'
            '<button onclick="__doPostBack(\'ctl09\',\'\')" type="button">查詢</button>'
            f'<span id="lbResult">搜尋結果：共{total}筆</span><table id="gvFull"><tr><th>分類</th><th>標題</th>'
            f"<th>開始</th></tr>{rows}{pager}</table></form></body></html>")


def sso_detail(k: int) -> str:
    title, _date, f = SSO_NOTICES[k]
    return (f'<html><head><meta charset="utf-8"><title>公告</title></head><body>'
            f'<span id="ctl00_phMain_lbSubject">{title}</span><span id="ctl00_phMain_lbContent">公告內容。</span>'
            f'<span id="ctl00_phMain_lbFileLink"><a href="AttachedFile.aspx?cEncrptedFilePath={f}">附件</a></span>'
            "</body></html>")


CLV2_NEWS = {"ctl02": "共同供應契約轉知", "ctl03": "經費分配原則修訂"}
CLV2_PAGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>單位網站</title></head><body>'
    f'<form method="post" action="./index.aspx?clv2=105000" id="form1">{_hidden("VS-CLV2")}'
    '<a href="#" onclick="__doPostBack(\'btnLangTC\',\'\')">繁中</a><div id="uSSONews_PanelList">'
    '<table id="uSSONews_gvList0"><tbody>'
    + "".join(f'<tr><td><span id="uSSONews_gvList0_{c}_lbcTypeName">一般公告</span></td><td>'
              f'<a id="uSSONews_gvList0_{c}_gv_lbSel" title="{t}" href="javascript:__doPostBack('
              f'&#39;uSSONews$gvList0${c}$gv_lbSel&#39;,&#39;&#39;)">{t}</a></td><td>2026-06-30</td></tr>'
              for c, t in CLV2_NEWS.items())
    + '</tbody></table></div><div id="uDataContentPos2_UpdatePanel2"><table id="uDataContentPos2_uList"><tr>'
    '<td><span id="uDataContentPos2_conLB0">申請表單</span></td><td><a href="javascript:__doPostBack('
    '&#39;uDataContentPos2$downloadBtn0&#39;,&#39;&#39;)">下載</a></td></tr></table></div></form></body></html>')


def clv2_detail(title: str) -> str:
    return (f'<html><head><meta charset="utf-8"><title>單位網站</title></head><body>'
            f'<form method="post" action="./index.aspx?clv2=105000" id="form1"><div id="divContent"><h4>{title}</h4><p>主旨：轉知事項。</p>'
            '<a href="/webservices/ajaxdownloadfile.aspx?f=9">附件</a></div></form></body></html>')


OIA_TABS = {20: "美洲", 21: "亞洲", 46: "歐洲"}
OIA_PAGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>本校姐妹校一覽</title></head><body><ul class="third_nav">'
    + "".join(f'<li><a href="javascript:void(0)" onclick="loadSubcategoryContent({n})" '
              f'class="{"active" if n == 20 else ""}">{t}</a></li>' for n, t in OIA_TABS.items())
    + '</ul><div class="side-post-content" id="dynamic-content"><table><caption>美洲地區</caption><tr><td>1</td>'
    '<td><a href="https://example.edu/">某大學</a></td></tr></table></div><script>\n'
    "function loadSubcategoryContent(subcategoryId) {\n"
    "\tconst contentDiv = document.getElementById('dynamic-content');\n"
    "\tcontentDiv.innerHTML = '<div class=\"text-center py-4\"> 載入中...</div>';\n"
    "\tfetch('load_content.php?sub_id=' + subcategoryId)\n"
    "\t\t.then(response => response.json())\n"
    "\t\t.then(data => { if (data.success) { contentDiv.innerHTML = data.content; } });\n"
    "}\n</script></body></html>")
OIA_CONTENT = {20: "<table><caption>美洲地區</caption><tr><td>某大學</td></tr></table>",
               21: '<table><caption>亞洲地區</caption><tr><td>某大學</td></tr></table>'
                   '<a href="files/asia-list.pdf">亞洲姊妹校名單</a>',
               46: "<table><caption>歐洲地區</caption><tr><td>某大學</td></tr></table>"}

EE_FILES = {"31": "upload/files/31.pdf", "32": "upload/files/32.pdf", "33": None}  # 33: the answer is no address
EE_PAGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>電子工程學系</title>\n<script>\n'
    "\t$(function()\n\t{\n\t\t$(\".down\").each(function()\n\t\t{\n\t\t\t$(this).click(function()\n\t\t\t{\n"
    "\t\t\t\t$.ajax(\n\t\t\t\t{\n\t\t\t\t\ttype:\"POST\",\n\t\t\t\t\turl: \"fileAjax.html\",\n"
    "\t\t\t\t\tdata:\n\t\t\t\t\t{\n\t\t\t\t\t\tid : $(this).find('.download').val(),\n\t\t\t\t\t\tk : 1\n"
    "\t\t\t\t\t},\n\t\t\t\t\tsuccess: function(data)\n\t\t\t\t\t{\n\t\t\t\t\t\twindow.open(data,\"_blank\");\n"
    "\t\t\t\t\t}\n\t\t\t\t});\n\t\t\t});\n\t\t});\n\t});\n</script>\n</head><body><div id=\"ne_list\">"
    + "".join(f'<a href="javascript:" class="down"><input type="hidden" value="{n}" class="download"><p>{i}</p>'
              f"<p>表格-{n}</p></a>" for i, n in enumerate(EE_FILES, start=1))
    + "</div></body></html>")

COUNSEL_PAGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>諮商中心</title></head><body><div role="main">'
    "<h1>表單下載</h1><p>各項申請表單與活動行事曆。</p>"
    f'<iframe src="https://drive.google.com/embeddedfolderview?id={FOLDER}#list" width="100%" height="400"></iframe>'
    '<iframe src="https://calendar.google.com/calendar/embed?src=nknu-test-calendar%40example.org&amp;ctz=Asia%2FTaipei"'
    ' width="800" height="600"></iframe>'
    f'<a href="{FORM}">線上報名</a></div></body></html>')
FORM_PAGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>線上報名</title></head><body><div class="form">'
    "<p>報名前請先閱讀"
    f'<a href="https://www.google.com/url?q=https://drive.google.com/file/d/{FILE3}/view&amp;sa=D">簡章</a>。</p>'
    '<a href="https://www.example.org/info">說明網站</a><input type="text" name="entry.1"></div></body></html>')

FOLDER_MIME, SHORTCUT = google_api.FOLDER, google_api.SHORTCUT
META = {
    FOLDER: {"id": FOLDER, "name": "表單下載", "mimeType": FOLDER_MIME},
    FILE1: {"id": FILE1, "name": "申請表.pdf", "mimeType": "application/pdf", "size": str(len(PDF))},
    DOC1: {"id": DOC1, "name": "說明文件", "mimeType": "application/vnd.google-apps.document"},
    SHORT1: {"id": SHORT1, "name": "辦法捷徑", "mimeType": SHORTCUT,
             "shortcutDetails": {"targetId": TARGET1, "targetMimeType": "application/pdf"}},
    TARGET1: {"id": TARGET1, "name": "辦法.pdf", "mimeType": "application/pdf", "size": str(len(PDF))},
    SUB1: {"id": SUB1, "name": "附件", "mimeType": FOLDER_MIME},
    FILE2: {"id": FILE2, "name": "附件一.pdf", "mimeType": "application/pdf", "size": str(len(PDF))},
    FILE3: {"id": FILE3, "name": "簡章.pdf", "mimeType": "application/pdf", "size": str(len(PDF))},
}
LISTS = {FOLDER: [FILE1, DOC1, SHORT1, SUB1], SUB1: [FILE2]}
EVENTS = {None: {"kind": "calendar#events", "items": [{"id": "e1", "summary": "講座"}], "nextPageToken": "PAGE-2"},
          "PAGE-2": {"kind": "calendar#events", "items": [{"id": "e2", "summary": "團體"}]}}


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


# ---------------------------------------------------------------- the fake network


class School:
    """What the fake network answers, and what it saw: every request (method, URL), and anything a request did that
    it must not (problems): a press built from another page's form state, an API request without its key header or
    with the key in its address, a form's answer sent."""

    HTML: ClassVar[dict[str, str]] = {"content-type": "text/html; charset=utf-8"}

    def __init__(self, aca_post_status: int | None = None):
        self.seen: list[tuple[str, str]] = []
        self.problems: list[str] = []
        self.lock = threading.Lock()
        self.aca_post_status = aca_post_status  # what c.nknu's list answers every press with (None: the next page)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.seen.append((request.method, str(request.url)))
        url = str(request.url)
        if KEY in url:
            self.problems.append(f"the key in an address: {url}")
        host, raw = request.url.host, request.url.path
        if host == google_api.API_HOST and raw.startswith(("/drive/", "/calendar/")):
            if request.headers.get("x-goog-api-key") != KEY:
                self.problems.append(f"an API request without its key header: {url}")
        elif "x-goog-api-key" in request.headers:
            self.problems.append(f"the key sent to {url}")
        path = raw.lower() if host != google_api.API_HOST else raw  # Drive's ids are case-sensitive
        if path.endswith("/robots.txt") or "nknu-archive-probe-" in path:
            return httpx.Response(404, headers=self.HTML, content=b"<html><body>not found</body></html>")
        query = parse_qs(request.url.query.decode(), keep_blank_values=True)
        form = parse_qs(request.content.decode(), keep_blank_values=True) if request.method == "POST" else {}
        handler = {"c.nknu.edu.tw": self.aca, "sso.nknu.edu.tw": self.sso, "oia.nknu.edu.tw": self.oia,
                   "ee.nknu.edu.tw": self.ee, "sites.google.com": self.sites, "docs.google.com": self.docs,
                   google_api.API_HOST: self.api}.get(host)
        got = handler(request.method, path, {k.lower(): v for k, v in query.items()}, form) if handler else None
        if got is None:
            self.problems.append(f"nothing answers {request.method} {url}")
            return httpx.Response(404, headers=self.HTML, content=b"<html><body>not found</body></html>")
        return got

    def page(self, html: str) -> httpx.Response:
        return httpx.Response(200, headers=self.HTML, content=html.encode("utf-8"))

    def pdf(self, name: str = "file.pdf") -> httpx.Response:
        disposition = f"attachment; filename=\"file.pdf\"; filename*=UTF-8''{quote(name)}"  # RFC 6266
        return httpx.Response(200, headers={"content-type": "application/pdf", "content-disposition": disposition},
                              content=PDF)

    def _state(self, form: dict, want: str) -> None:
        got = (form.get("__VIEWSTATE") or [""])[0]
        if got != want:
            self.problems.append(f"a press built from the form state {got!r}, not {want!r}")

    def aca(self, method, path, query, form):
        if path == "/aca/newslist.aspx" and method == "GET":
            return self.page(aca_list(1))
        if path == "/aca/newslist.aspx" and method == "POST":
            if self.aca_post_status is not None:
                return httpx.Response(self.aca_post_status, headers=self.HTML, content=b"<html>busy</html>")
            arg = (form.get("__EVENTARGUMENT") or [""])[0]
            n = int(arg.removeprefix("Page$"))
            self._state(form, f"VS-ACA-P{n - 1}")  # page N is asked for from page N-1, as archived
            return self.page(aca_list(n))
        if path == "/aca/newsdetail.aspx":
            nid = int(query["nid"][0])
            if nid == 999999999:  # the id probe: c.nknu answers an empty detail for an item that does not exist
                return self.page(aca_detail("", "", None))
            return self.page(aca_detail(*ACA_DETAILS[nid]))
        if path.startswith("/aca/uploadfile/"):
            return self.pdf(path.rsplit("/", 1)[-1])
        return None

    def sso(self, method, path, query, form):
        if path == "/news/subjectlist.aspx":
            cat = (query.get("cat") or [""])[0]
            cat = next((c for c in SSO_CATS if c.lower() == cat.lower()), cat)
            if method == "GET":
                return self.page(sso_list(cat, 1))
            self._state(form, f"VS-SSO-{cat or 'ALL'}-P1")
            return self.page(sso_list(cat, int((form.get("__EVENTARGUMENT") or ["Page$1"])[0][5:])))
        if path == "/news/lastnews.aspx":
            k = query["unid"][0]
            if set(k) == {"0"}:  # the id probe: SSO sends a missing notice to its error page
                return httpx.Response(302, headers={"location": "/ErrorPage/pageNotFound.aspx?aspxerrorpath="
                                                                "/News/lastNews.aspx"})
            return self.page(sso_detail(int(k[-1])))
        if path == "/news/attachedfile.aspx":
            if query["cencrptedfilepath"][0] == "F3":  # the school's own page: the link itself is broken
                return self.page("<html><body><h3>附件無法下載</h3><p>檔案包含不支援的字元</p></body></html>")
            return self.pdf()
        if path == "/services/site/index.aspx" and method == "GET":
            return self.page(CLV2_PAGE)
        if path == "/services/site/index.aspx" and method == "POST":
            self._state(form, "VS-CLV2")
            target = (form.get("__EVENTTARGET") or [""])[0]
            if target == "uDataContentPos2$downloadBtn0":
                return self.pdf("form.pdf")
            return self.page(clv2_detail(CLV2_NEWS[target.split("$")[2]]))
        if path == "/webservices/ajaxdownloadfile.aspx":
            return self.pdf()
        return None

    def oia(self, method, path, query, form):
        if path == "/sister-schools.php":
            return self.page(OIA_PAGE)
        if path == "/load_content.php":
            n = int(query["sub_id"][0])
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=json.dumps({"success": True, "content": OIA_CONTENT[n],
                                                      "message": ""}).encode())
        if path == "/files/asia-list.pdf":
            return self.pdf()
        return None

    def ee(self, method, path, query, form):
        if path == "/download.html":
            return self.page(EE_PAGE)
        if path == "/fileajax.html" and method == "POST":
            if form.get("k") != ["1"]:
                self.problems.append(f"ee's button posted k={form.get('k')}")
            answer = EE_FILES[form["id"][0]]
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                                  content=(answer or "<p>找不到檔案</p>").encode())
        if path.startswith("/upload/files/"):
            return self.pdf()
        return None

    def sites(self, method, path, query, form):
        return self.page(COUNSEL_PAGE) if path == "/mail.nknu.edu.tw/counseling/home" else None

    def docs(self, method, path, query, form):
        if method != "GET" or "formresponse" in path:
            self.problems.append(f"a form answered: {method} {path}")
            return None
        return self.page(FORM_PAGE) if path.endswith("/viewform") else None

    def api(self, method, path, query, form):
        if path == "/drive/v3/files":
            folder = query["q"][0].split("'")[1]
            files = [META[f] for f in LISTS[folder]]
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=json.dumps({"files": files}).encode())
        parts = path.split("/")
        if path.startswith("/drive/v3/files/") and len(parts) == 6 and parts[5] == "export":
            return httpx.Response(200, headers={"content-type": "application/vnd.openxmlformats-officedocument."
                                                                "wordprocessingml.document"}, content=_docx())
        if path.startswith("/drive/v3/files/"):
            fid = parts[4]
            if query.get("alt") == ["media"]:
                return self.pdf(META[fid]["name"])
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=json.dumps(META[fid]).encode())
        if path.startswith("/calendar/v3/calendars/") and path.endswith("/events"):
            token = (query.get("pagetoken") or [None])[0]
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=json.dumps(EVENTS[token]).encode())
        return None


class FastClock:
    """The run's clock, shared by its worker threads: a sleep moves it on at once (every gap is on the record), and
    real time runs 100 times faster (a worker's idle wait, IDLE_EXIT_S, passes in a fraction of a second)."""

    def __init__(self, scale: float = 100.0):
        self.base, self.slept, self.scale = 1_000_000.0, 0.0, scale
        self.start = time.monotonic()
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.base + self.slept + (time.monotonic() - self.start) * self.scale

    def sleep(self, s: float) -> None:
        with self.lock:
            self.slept += max(0.0, s)


# ---------------------------------------------------------------- the 10-25 page-type results (design §8.2)

def _key(url: str) -> str:
    c = scope.canonical(url)
    return pagetypes.type_key(c, scope.site_key(c))


TESTED = [ACA_LIST, "https://c.nknu.edu.tw/aca/NewsDetail.aspx?Nid=663&GI=1", SSO_HOME,
          "https://sso.nknu.edu.tw/News/SubjectList.aspx", f"https://sso.nknu.edu.tw/News/lastNews.aspx?UNID={unid(1)}",
          OIA, EE, COUNSEL]  # cLv2's type is not among them: new this round, todo (why new) until jscheck


def jstest_dir(root: Path) -> Path:
    """The five files of the 10-25 test, made up for the test's own page types: each tested ok (200, the same text
    directly and in the browser); oia's and ee's types noted by the click scan (click_profiled)."""
    d = root / "jstest"
    d.mkdir()
    lines = [{"type": _key(u), "url": scope.canonical(u), "direct_status": 200, "direct_chars": 500,
              "rendered_chars": 500, "method": "direct"} for u in TESTED]
    (d / pagetypes.RESULTS).write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                                       encoding="utf-8")
    (d / pagetypes.RETEST).write_text("", encoding="utf-8")
    (d / pagetypes.TYPES).write_text(json.dumps({x["type"]: [x["url"]] for x in lines}, ensure_ascii=False),
                                     encoding="utf-8")
    (d / pagetypes.REVIEW).write_text(json.dumps({"kinds": []}), encoding="utf-8")
    (d / pagetypes.CLICK_SCAN).write_text(json.dumps({"kinds": [{"type": _key(OIA), "hidden_pages": 1},
                                                                {"type": _key(EE), "hidden_pages": 1}]}),
                                          encoding="utf-8")
    return d


# ---------------------------------------------------------------- one seed, one run

def crawler_on(root: Path, school: School, clock: FastClock, **kw) -> mod.Crawler:
    """A crawler of the archive at root on the fake network (its own WARC files, the fake clock), no DNS."""

    def client(warc):
        return mod.archive_client(warc, transport=httpx.MockTransport(school), clock=clock, sleep=clock.sleep)

    return mod.Crawler(root, plan=PLAN, clock=clock, sleep=clock.sleep,
                       resolver=lambda h: (QUEUES.get(h, f"ip:{h}"), None, h), client_factory=client,
                       netcheck=lambda: True, disk_free=lambda path: 10**12, campus_url=None, **kw)


def crawl(tmp_path: Path, monkeypatch, *, key: bool = True, school: School | None = None, **kw):
    if key:
        monkeypatch.setenv(google_api.KEY_ENV, KEY)
    else:
        monkeypatch.delenv(google_api.KEY_ENV, raising=False)
    monkeypatch.setattr(mod, "IDLE_POLL_S", 0.02)
    school = school or School()
    crawler = crawler_on(tmp_path / "archive", school, FastClock(), **kw)
    report = seeds.seed_round1(crawler, old=None, facts_db=None, cc_dir=None, probe=None,
                               jstest=jstest_dir(tmp_path))
    crawler.run(poll_s=0.01)
    return crawler, school, report


def _canon(url: str) -> str:
    return scope.canonical(url)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        yield crawl(tmp_path_factory.mktemp("e2e"), mp)
    finally:
        mp.undo()


def levels(f) -> dict[str, tuple[str, str]]:
    return {r["url"]: (r["level"], r["label"]) for r in f.q(
        "SELECT u.url, pc.level, pc.label FROM page_completeness pc JOIN url u ON u.id = pc.id")}


def test_seed_imports_the_page_types_and_the_run_finishes(world):
    crawler, school, report = world
    f = crawler.frontier
    assert school.problems == []
    assert any("导入 10-25 网页类型测试" in line for line in report.page_types)
    assert "网页类型：10-25 测试结果" in report.text()
    imported = {r["key"]: r for r in f.q("SELECT * FROM page_type WHERE source='import'")}
    assert set(imported) == {_key(u) for u in TESTED}
    assert "click_profiled" in (imported[_key(EE)]["note"] or "")
    end = json.loads(f.q("SELECT message FROM event WHERE code='run_end' ORDER BY rowid DESC LIMIT 1")[0][0])
    assert (end["reason"], end["pending"], end["presses"]) == ("finished", 0, 0)
    start = json.loads(f.q("SELECT message FROM event WHERE code='run_start' ORDER BY rowid DESC LIMIT 1")[0][0])
    assert start["end_wait_s"] == mod.END_WAIT_S  # C0: the module's default; `archive run` passes its own
    assert outcome.self_check(f.conn) == {"url_result": 0, "action_result": 0, "no_owner_site": 0}
    assert f.q("SELECT count(*) FROM url WHERE state IN ('pending','active','failed')")[0][0] == 0


def test_every_page_comes_to_the_completeness_its_evidence_gives(world):
    crawler, _school, _report = world
    got = levels(crawler.frontier)
    want = {ACA_LIST: "complete", SSO_HOME: "complete", "https://sso.nknu.edu.tw/News/SubjectList.aspx": "complete",
            OIA: "unchecked",  # 「載入中」 in its script: js_hint, its type back to todo (why hint) until jscheck
            EE: "partial",  # one button answered no file address: mismatch (要人看)
            CLV2: "unchecked",  # a type the 10-25 test never saw: new, todo
            COUNSEL: "complete"}
    want.update({f"https://c.nknu.edu.tw/aca/NewsDetail.aspx?Nid={n}&GI=1": "complete" for n in (661, 662, 663)})
    want["https://c.nknu.edu.tw/aca/NewsDetail.aspx?Nid=660&GI=1"] = "incomplete"  # empty, and the probe's: looks_gone
    want.update({f"https://sso.nknu.edu.tw/News/SubjectList.aspx?cat={c}": "complete" for c in SSO_CATS})
    want.update({f"https://sso.nknu.edu.tw/News/lastNews.aspx?UNID={unid(k)}": "complete" for k in (1, 2)})
    want[f"https://sso.nknu.edu.tw/News/lastNews.aspx?UNID={unid(3)}"] = "partial"  # its attachment: bad_link
    assert {_canon(u): lv for u, lv in want.items()} == {u: lv for u, (lv, _label) in got.items()}
    assert got[_canon(EE)][1] == "review" and got[_canon(f"https://sso.nknu.edu.tw/News/lastNews.aspx?UNID={unid(3)}")][1] \
        == "review"
    empty = crawler.frontier.row(_canon("https://c.nknu.edu.tw/aca/NewsDetail.aspx?Nid=660&GI=1"))
    assert set(empty["flags"].split(",")) == {"empty_detail", "gone_id"}
    probes = {r["host"]: r for r in crawler.frontier.q("SELECT * FROM id_probe")}
    assert (probes["c.nknu.edu.tw"]["status"], probes["c.nknu.edu.tw"]["empty"]) == (200, 1)
    assert probes["sso.nknu.edu.tw"]["status"] == 302  # SSO's probe: its error page, never a gone_id


def test_no_page_is_complete_that_lacks_anything(world):
    """An independent look at the ledger: every needed resource of a complete page is a row fetched with a 200 and
    sniffed, or a press done with a 200; nothing it needs waits, failed, or has no row at all."""
    f = world[0].frontier
    complete = [r[0] for r in f.q("SELECT id FROM page_completeness WHERE level='complete'")]
    assert len(complete) >= 15
    for pid in complete:
        page = f.row_by_id(pid)
        assert (page["state"], page["status"]) == ("done", 200) and page["kind"] == "page"
        for e in f.q("SELECT * FROM page_res WHERE page_id=? AND need IN ('must','round2')", (pid,)):
            assert e["need"] == "must", (page["url"], e["res_key"])
            if e["action_id"] is not None:
                a = f.q("SELECT * FROM action WHERE id=?", (e["action_id"],))[0]
                assert (a["state"], a["status"]) == ("done", 200), (page["url"], e["res_key"])
                continue
            assert e["res_id"] is not None, (page["url"], e["res_key"], e["why"])
            r = f.row_by_id(e["res_id"])
            assert (r["state"], r["status"]) == ("done", 200) and r["sniff"], (page["url"], e["res_key"])
            assert r["kind0"] != "drive", (page["url"], e["res_key"])  # never a Drive item's metadata for its file
        bad = f.q("SELECT res_key, result FROM page_resource WHERE page_id=? AND result NOT IN "
                  "('ok','rule','gone','login','school_side')", (pid,))
        assert bad == [], (page["url"], [tuple(b) for b in bad])


def test_every_button_is_pressed_and_every_list_walked_to_its_end(world):
    f = world[0].frontier
    acts = f.q("SELECT a.*, u.url AS list_url FROM action a JOIN url u ON u.id = a.list_id ORDER BY a.id")
    assert {a["state"] for a in acts} <= {"done", "mismatch"}
    by = {}
    for a in acts:
        by.setdefault((a["list_url"], a["kind"]), []).append((a["state"], a["target"], a["argument"]))
    assert sorted(x[2] for x in by[(_canon(ACA_LIST), "pager")]) == ["Page$2", "Page$3"]
    assert [x[2] for x in by[(_canon(SSO_HOME), "pager")]] == ["Page$2"]
    assert sorted(x[1] for x in by[(_canon(CLV2), "detail")]) == ["uSSONews$gvList0$ctl02$gv_lbSel",
                                                                   "uSSONews$gvList0$ctl03$gv_lbSel"]
    assert [x[1] for x in by[(_canon(CLV2), "download")]] == ["uDataContentPos2$downloadBtn0"]
    clicks = sorted((x[0], x[2]) for x in by[(_canon(EE), "click")])
    assert clicks == [("done", "id=31&k=1"), ("done", "id=32&k=1"), ("mismatch", "id=33&k=1")]
    walks = {r["url"]: r for r in f.q("SELECT u.url, lw.* FROM listwalk lw JOIN url u ON u.id = lw.list_id")}
    aca = walks[_canon(ACA_LIST)]
    assert (aca["ended_by"], aca["pages"], aca["rows_unique"], aca["ok"]) == ("last_page", 3, 4, 1)
    sso = walks[_canon(SSO_HOME)]
    assert (sso["ended_by"], sso["pages"], sso["rows_unique"], sso["claimed"]) == ("last_page", 2, 3, 3)
    whole = walks[_canon("https://sso.nknu.edu.tw/News/SubjectList.aspx")]
    assert (whole["ended_by"], whole["claimed"]) == ("last_page", 3)  # 3 + six empty categories = the whole list's 3
    for c in SSO_CATS[1:]:
        assert walks[_canon(f"https://sso.nknu.edu.tw/News/SubjectList.aspx?cat={c}")]["ended_by"] == "no_rows_ok"
    assert walks[_canon(CLV2)]["ended_by"] == "last_page"
    tabs = f.q("SELECT url, state, kind0, flags FROM url WHERE url LIKE '%load_content.php%' ORDER BY url")
    assert [(r["state"], r["kind0"], r["flags"]) for r in tabs] == [("done", "click", None)] * 3  # the shown one too
    oia = f.row(_canon(OIA))
    assert "js_hint" in oia["flags"].split(",")
    pr = {r["res_key"]: r for r in f.q("SELECT * FROM page_res WHERE page_id=?", (oia["id"],))}
    asia = _canon("https://oia.nknu.edu.tw/files/asia-list.pdf")
    assert (pr[asia]["role"], pr[asia]["need"]) == ("file", "must")  # a tab's attachment counts on the page
    assert all(pr[f"click:loadSubcategoryContent({n})"]["need"] == "must" for n in OIA_TABS)
    ee = {r["res_key"]: r for r in f.q("SELECT * FROM page_res WHERE page_id=?", (f.row(_canon(EE))["id"],))}
    assert {k for k in ee if k.startswith("https://")} == {_canon(f"https://ee.nknu.edu.tw/{EE_FILES[n]}")
                                                            for n in ("31", "32")}


def test_drive_folder_calendar_and_form_are_read_through_the_api_and_count_on_the_page(world):
    crawler, school, _report = world
    f = crawler.frontier
    page = f.row(_canon(COUNSEL))
    pr = {r["res_key"]: r for r in f.q("SELECT * FROM page_res WHERE page_id=?", (page["id"],))}
    for fid in (FOLDER, FILE1, DOC1, SHORT1, SUB1, FILE2, FILE3):  # the shortcut's entry stands for its target
        assert pr[f"drive:{fid}"]["need"] == "must", fid
    content = {k: f.row_by_id(pr[f"drive:{k}"]["res_id"])["url"] for k in (FILE1, DOC1, SHORT1, FILE2, FILE3)}
    assert content[FILE1] == google_api.media_url(FILE1)
    assert content[DOC1].startswith(f"{google_api.DRIVE_API}/{DOC1}/export?")
    assert content[SHORT1] == google_api.media_url(TARGET1)  # a shortcut stands for its target's content
    assert content[FILE3] == google_api.media_url(FILE3)  # linked from the form's description
    assert f.row_by_id(pr[f"drive:{FOLDER}"]["res_id"])["kind0"] == "drive_list"
    assert pr[f"calendar:{CALENDAR}:p1"]["res_id"] and pr[f"calendar:{CALENDAR}:p2"]["res_id"]
    assert pr[_canon(FORM)]["role"] == "form"
    assert not any("drive.google.com" in u or "calendar.google.com" in u for _m, u in school.seen)
    assert not any(urlsplit(u).hostname == "www.example.org" for _m, u in school.seen)  # a form's ordinary links
    assert f.host(google_api.API_HOST)["queue"] == google_api.QUEUE
    assert f.host("docs.google.com")["queue"] == google_api.FORMS_QUEUE
    assert f.q("SELECT count(*) FROM link WHERE verdict LIKE 'unhandled:google%'")[0][0] == 0
    warcs = list((crawler.root / "warc").rglob("*.warc.gz"))
    assert warcs and not any(KEY.encode() in gzip.decompress(p.read_bytes()) for p in warcs)  # never archived


def test_the_politeness_and_scope_audits_are_empty(world):
    crawler, school, _report = world
    f = crawler.frontier
    assert politeness(f) == []
    assert scope_audit(f, PLAN) == []
    posts = f.q("SELECT method, purpose, scope FROM request WHERE method='POST'")
    assert len(posts) == 2 + 1 + 3 + 3  # c.nknu's pages 2-3, SSO's page 2, cLv2's 3 buttons, ee's 3
    assert {(r["purpose"], r["scope"].split(":")[0]) for r in posts} == {("postback", "admit")}
    assert len([m for m, _u in school.seen if m == "POST"]) == len(posts)  # each POST once, never sent again


def test_once_jscheck_clears_the_tabs_page_it_is_complete(world):
    """oia's page waits for the browser check only (its type sent back to todo by its 「載入中」, the page itself
    js_hint): every tab, the shown one too, was pressed and what they hold was got. What jscheck would write (the
    type ok, this very page opened) makes it complete; nothing else was missing. Rolled back after."""
    f = world[0].frontier
    oia = f.row(_canon(OIA))
    pt = f.q("SELECT verdict, why, source FROM page_type WHERE key=?", (oia["page_type"],))[0]
    assert tuple(pt) == ("todo", "hint", "import")
    with f.lock:
        f.conn.execute("BEGIN")
        try:
            f.conn.execute("UPDATE page_type SET verdict='ok', source='jscheck' WHERE key=?", (oia["page_type"],))
            f.conn.execute("INSERT INTO js_page(url_id, page_type, at, verdict) VALUES (?,?,?, 'ok')",
                           (oia["id"], oia["page_type"], utc_now()))
            assert levels(f)[_canon(OIA)] == ("complete", "complete")
        finally:
            f.conn.execute("ROLLBACK")
    assert levels(f)[_canon(OIA)][0] == "unchecked"


# ---------------------------------------------------------------- without a key; C0


def test_with_no_key_the_api_rows_wait_and_the_run_ends_waiting(tmp_path, monkeypatch):
    crawler, school, _report = crawl(tmp_path, monkeypatch, key=False)
    f = crawler.frontier
    assert not any(urlsplit(u).hostname == google_api.API_HOST for _m, u in school.seen)
    api = f.q("SELECT state, reason, attempts FROM url WHERE host=?", (google_api.API_HOST,))
    assert api and {tuple(r) for r in api} == {("pending", "no_api_key", 0)}  # waits, never a failure
    end = json.loads(f.q("SELECT message FROM event WHERE code='run_end' ORDER BY rowid DESC LIMIT 1")[0][0])
    assert end["reason"] == "waiting"
    assert levels(f)[_canon(COUNSEL)] == ("partial", "todo")  # its Drive and calendar rows wait: never complete
    assert f.q("SELECT count(*) FROM event WHERE code='key_check'")[0][0] >= 1


def test_end_wait_is_set_by_the_run_and_the_command_waits_30_minutes(tmp_path):
    import inspect

    from nknu.admin import cli

    crawler = mod.Crawler(tmp_path, plan=PLAN, campus_url=None, end_wait_s=1800.0,
                          resolver=lambda h: (QUEUES.get(h, f"ip:{h}"), None, h), client_factory=lambda w: None)
    crawler.seed_url(ACA_LIST)
    now = crawler.clock()
    crawler.frontier.x("UPDATE url SET next_at=?, attempts=1", (now + 1000,))
    assert crawler._should_end() is False  # 1000 s: waited for under 30 min (the module's 600 s would end it)
    crawler.frontier.x("UPDATE url SET next_at=?", (now + 2000,))
    assert crawler._should_end() is True and crawler.end_reason == "waiting"
    assert mod.END_WAIT_S == 600.0 and mod.RUN_END_WAIT_S == 1800.0
    default = inspect.signature(cli.archive_run).parameters["end_wait"].default
    assert getattr(default, "default", default) == mod.RUN_END_WAIT_S
    with pytest.raises(ValueError):
        mod.Crawler(tmp_path / "bad", plan=PLAN, end_wait_s=0, client_factory=lambda w: None)


# ---------------------------------------------------------------- the wiring, one piece at a time


def _res(f, url: str) -> dict[str, tuple]:
    pid = f.row(_canon(url))["id"]
    return {r["res_key"]: (r["how"], r["role"], r["need"], r["why"], r["res_id"], r["action_id"])
            for r in f.q("SELECT * FROM page_res WHERE page_id=?", (pid,))}


def test_a_page_fetched_again_keeps_what_its_parts_brought_and_presses_nothing_again(tmp_path, monkeypatch):
    """A page's page_res is written anew at each fetch; what its tabs, its form, its file buttons and its pressed
    details brought is read back from the archive (Crawler._replay_parts), never lost unseen, and nothing that was
    pressed or fetched once is asked again."""
    crawler, school, _report = crawl(tmp_path, monkeypatch)
    f = crawler.frontier
    pages = (OIA, EE, CLV2, COUNSEL)
    before = {u: (_res(f, u), levels(f)[_canon(u)], f.row(_canon(u))["text_chars"]) for u in pages}
    asked = len(school.seen)
    f.x(f"UPDATE url SET state='pending', next_at=0 WHERE url IN ({','.join('?' * len(pages))})",
        [_canon(u) for u in pages])
    again = crawler_on(crawler.root, school, crawler.clock)
    again.run(poll_s=0.01)
    g = again.frontier
    for u in pages:
        res, level, chars = before[u]
        now = _res(g, u)
        assert {k: now.get(k) for k in res} == res, u  # every entry it had, as it was
        assert (levels(g)[_canon(u)], g.row(_canon(u))["text_chars"]) == (level, chars), u
    # the one thing more: the shortcut's target, read since, now has its own entry beside the shortcut's
    assert set(_res(g, COUNSEL)) - set(before[COUNSEL][0]) <= {f"drive:{TARGET1}"}
    assert sorted(u for _m, u in school.seen[asked:]) == sorted(_canon(u) for u in pages)  # the pages, nothing else
    assert school.problems == []


def test_a_list_whose_press_fails_starts_again_from_its_get_once_then_ends_post_failed(tmp_path, monkeypatch):
    crawler, school, _report = crawl(tmp_path, monkeypatch, school=School(aca_post_status=500))
    f = crawler.frontier
    lw = f.q("SELECT lw.* FROM listwalk lw JOIN url u ON u.id = lw.list_id WHERE u.url=?", (_canon(ACA_LIST),))[0]
    assert (lw["ended_by"], lw["ok"]) == ("post_failed", 0)
    posts = [u for m, u in school.seen if m == "POST" and "newslist" in u]
    assert len(posts) == 2  # one press in each epoch: never sent twice in one, never a third time by itself
    gets = [u for m, u in school.seen if m == "GET" and u == _canon(ACA_LIST)]
    assert len(gets) == 2  # the chain started again from its GET once
    assert levels(f)[_canon(ACA_LIST)][0] == "incomplete"
    assert any("failed" in m for (m,) in f.q("SELECT message FROM event WHERE code='chain_restart'"))


def test_a_get_paged_list_whose_last_page_failed_never_claims_its_end(tmp_path):
    from tests.archive.test_archive import make, seed, snap
    from tests.archive.test_crawler_round1 import drive

    first = '<a href="list.php?page=2">2</a><a href="item.php?id=1">公告一</a>'
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "list.php?page=1": lambda u: snap(u, html=first),
                                      "list.php?page=2": lambda u: snap(u, 404), "item.php": lambda u: snap(u, html="x")})
    seed(crawler, "https://w.nknu.edu.tw/list.php?page=1")
    drive(crawler, net, "ip:w.nknu.edu.tw")
    f = crawler.frontier
    p1 = f.row("https://w.nknu.edu.tw/list.php?page=1")
    lw = f.q("SELECT * FROM listwalk WHERE list_id=?", (p1["id"],))[0]
    assert (lw["ended_by"], lw["ok"], lw["profile"]) == (None, 0, "generic") and "page 2" in lw["note"]
    assert p1["page_role"] == "list"
    level = f.q("SELECT level FROM page_completeness WHERE id=?", (p1["id"],))[0][0]
    assert level != "complete"


def test_google_api_errors_are_sorted_by_their_reason(tmp_path, monkeypatch):
    from tests.archive.test_archive import make, seed, snap
    from tests.archive.test_crawler_round1 import drive

    monkeypatch.setenv(google_api.KEY_ENV, KEY)
    ids = {"gone": "1GoNeAbCdEfGhIjKl", "busy": "1BuSyAbCdEfGhIjKl", "key": "1KeYsAbCdEfGhIjKl"}
    page = "".join(f'<a href="https://drive.google.com/file/d/{i}/view">{k}</a>' for k, i in ids.items())

    def error(status, reason):
        body = json.dumps({"error": {"code": status, "errors": [{"reason": reason}]}}).encode()
        return lambda u: snap(u, status, body=body, headers={"content-type": "application/json"})

    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404), "/a/home": lambda u: snap(u, html=page),
                                          ids["gone"]: error(404, "notFound"),
                                          ids["busy"]: error(403, "rateLimitExceeded"),
                                          ids["key"]: error(400, "keyInvalid")})
    seed(crawler, "https://w.nknu.edu.tw/a/home")
    drive(crawler, net, "ip:w.nknu.edu.tw")
    f = crawler.frontier
    drive(crawler, net, google_api.QUEUE)  # gone, then busy: the queue rests
    assert f.paused_until(google_api.QUEUE) > clock()
    f.unpause(google_api.QUEUE)
    drive(crawler, net, google_api.QUEUE)  # key: the queue rests for the run
    row = {k: f.row(google_api.item_url(i)) for k, i in ids.items()}
    assert (row["gone"]["state"], row["gone"]["reason"]) == ("done", "api:notFound")
    assert f.q("SELECT result FROM url_result WHERE id=?", (row["gone"]["id"],))[0][0] == "api_error"
    assert (row["busy"]["state"], row["busy"]["reason"], row["busy"]["attempts"]) == \
        ("pending", "api:rateLimitExceeded", 1)
    assert (row["key"]["state"], row["key"]["reason"], row["key"]["attempts"]) == ("pending", "api_key", 0)
    why = f.q("SELECT why FROM pause WHERE queue=?", (google_api.QUEUE,))[0][0]
    assert why.startswith("api_key")  # hold_for_key() lifts it when the next run starts with a key
    assert f.q("SELECT count(*) FROM deny")[0][0] == 0  # an API's 403 is no refusal streak of a directory
    assert f.q("SELECT count(*) FROM event WHERE code='key_check'")[0][0] == 1
    assert google_api.hold_for_key(f, clock()) and f.paused_until(google_api.QUEUE) == 0


def test_a_file_the_schools_page_says_is_missing_is_gone_only_the_second_time(tmp_path):
    from tests.archive.test_archive import make, snap
    from tests.archive.test_crawler_round1 import drive

    notice = "https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=" + unid(7)
    gone = "<html><body><h3>附件無法下載</h3><p>檔案不存在</p></body></html>"
    crawler, net, clock = make(tmp_path, {"robots.txt": snap("x", 404),
                                          "lastnews.aspx": lambda u: snap(u, html=sso_detail(1)),
                                          "attachedfile.aspx": lambda u: snap(u, html=gone)},
                               queues={"sso.nknu.edu.tw": "ip:sso"}, plan=PLAN)
    crawler.seed_url(notice)
    drive(crawler, net, "ip:sso")
    f = crawler.frontier
    att = f.row(_canon("https://sso.nknu.edu.tw/News/AttachedFile.aspx?cEncrptedFilePath=F1"))
    assert (att["state"], att["reason"], att["attempts"]) == ("pending", "gone_page:recheck", 1)
    assert f.q("SELECT result FROM url_result WHERE id=?", (att["id"],))[0][0] == "retry"
    clock.t = att["next_at"] + 1
    drive(crawler, net, "ip:sso")
    att = f.row_by_id(att["id"])
    assert (att["state"], att["flags"]) == ("done", "gone_page")
    assert f.q("SELECT result FROM url_result WHERE id=?", (att["id"],))[0][0] == "gone"


def test_a_short_link_to_a_drive_file_is_read_through_the_api_for_the_page_that_names_it(tmp_path):
    from tests.archive.test_archive import make, seed, snap
    from tests.archive.test_crawler_round1 import drive

    to = f"https://drive.google.com/file/d/{FILE1}/view"
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404),
                                      "/a/home": lambda u: snap(u, html='<a href="https://bit.ly/3AbCdEf">簡章</a>'),
                                      "bit.ly": snap("x", 301, headers={"location": to})})
    seed(crawler, "https://w.nknu.edu.tw/a/home")
    drive(crawler, net, "ip:w.nknu.edu.tw")
    drive(crawler, net, "ip:bit.ly")
    f = crawler.frontier
    meta = f.row(google_api.item_url(FILE1))
    assert (meta["state"], meta["kind0"], meta["owner_site"]) == ("pending", "drive", "w.nknu.edu.tw")
    pr = _res(f, "https://w.nknu.edu.tw/a/home")
    assert pr[f"drive:{FILE1}"][:3] == ("drive_api", "drive", "must") and pr[f"drive:{FILE1}"][4] == meta["id"]
    short = f.row("https://bit.ly/3AbCdEf")
    assert f.q("SELECT verdict FROM link WHERE src=? AND dst=?", (short["id"], to))[0][0] == "queued"
    assert not any("drive.google.com" in u for u in net.seen)
