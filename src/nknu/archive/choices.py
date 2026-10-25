"""The owner's choice page: every pending scope decision with how much information it holds, built from the archive
(frontier.db, the WARC index, the site probe). The page lives in the local viewer (/choose); choices are saved to
<dir>/choices.json for Claude to apply. Nothing here sends a request anywhere.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from selectolax.lexbor import LexborHTMLParser

from nknu.archive import scope
from nknu.archive.seeds import PROBE
from nknu.archive.viewer import read_record

log = logging.getLogger("nknu.archive.choices")
ALBUM = re.compile(r"(?i)(album|gallery|photo|movieview|case_detail|活動花絮|相簿)")
DECOR_FILE = re.compile(r"(?i)(logo|icon|banner|arrow|btn|button|bg[_.-]|spacer|blank|bullet|qrcode|loading|sprite)")
TEMPLATE_DIR = re.compile(r"(?i)/(images?|img|template|templates|skins?|themes?|css|static|assets)/")
HONOR_NARROW = re.compile(r"恭賀|恭喜|狂賀|榮獲|榮譽榜|光榮榜|金榜|榮升|榮登|獲獎|得獎|獲頒|獲選|當選|入選|賀")
NOTICE_WORDS = re.compile(r"注意事項|須知|報到|核發|備取|說明")


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{root / 'frontier.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _top(counter: Counter, n: int = 12) -> list[str]:
    return [f"{k}（{v}）" if v > 1 else k for k, v in counter.most_common(n) if k]


def _units(conn, src_ids) -> list[str]:
    ids = list(src_ids)[:5000]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return _top(Counter(r[0] or r[1] for r in conn.execute(f"SELECT unit, site FROM url WHERE id IN ({marks})", ids)), 8)


def content_images(root: Path, conn) -> dict:
    """How many images sit in the body of archived pages (header, nav, footer, logos, icons and template folders
    left out), split into announcement/content pages and photo albums."""
    idx = sqlite3.connect(root / "viewer-index.db")
    pages = conn.execute("SELECT id, final_url, url, site, title FROM url WHERE state='done' AND status=200 "
                         "AND ctype LIKE 'text/html%'").fetchall()
    out = {"pages": 0, "with_images": 0, "content": set(), "album": set(), "per_site": Counter(), "samples": Counter(),
           "image_only": 0}
    for r in pages:
        hit = idx.execute("SELECT warc, offset FROM rec WHERE url=? LIMIT 1", (r["final_url"] or r["url"],)).fetchone()
        if not hit:
            continue
        try:
            _s, _h, body = read_record(root, *hit)
        except Exception as exc:  # noqa: BLE001 - a record being written: skip it
            log.debug("skipped %s: %s", r["url"], type(exc).__name__)
            continue
        out["pages"] += 1
        tree = LexborHTMLParser(body.decode("utf-8", "replace"))
        for bad in tree.css("header, nav, footer, aside, script, noscript, style"):
            bad.decompose()
        found = []
        for img in tree.css("img[src]"):
            src = (img.attributes.get("src") or "").strip()
            if not src or src.startswith("data:") or DECOR_FILE.search(src.rsplit("/", 1)[-1]) or TEMPLATE_DIR.search(src):
                continue
            w, h = img.attributes.get("width") or "", img.attributes.get("height") or ""
            if any(x.isdigit() and int(x) < 80 for x in (w, h)):
                continue
            found.append(scope.canonical(src, r["final_url"] or r["url"]) or src)
        if not found:
            continue
        out["with_images"] += 1
        album = bool(ALBUM.search(r["url"]))
        (out["album"] if album else out["content"]).update(found)
        if not album:
            out["per_site"][r["site"]] += len(set(found))
            text = tree.body.text(strip=True) if tree.body else ""
            if len(text) < 250:  # almost nothing but the image: the information is in the picture
                out["image_only"] += 1
                out["samples"][(r["title"] or r["url"])[:60]] += 1
    return out


def build(root: Path) -> dict:
    conn = _db(root)
    sections = []

    # ---- 1. rule fixes
    items = []
    google = conn.execute("SELECT src, dst, text FROM link WHERE dst LIKE 'https://www.google.com/url?%'").fetchall()
    targets, inside, texts, srcs = set(), set(), Counter(), set()
    for r in google:
        q = parse_qs(urlsplit(r["dst"]).query)
        real = scope.canonical((q.get("q") or q.get("url") or [""])[0])
        if real:
            targets.add(real)
            if scope.judge(real).ok:
                inside.add(real)
        texts[(r["text"] or "").strip()[:50]] += 1
        srcs.add(r["src"])
    items.append({
        "id": "unwrap_google", "title": "拆开 Google 协作平台的转址链接（google.com/url?q=…）",
        "why": "程序只拆 SSO 自己的转址，Google 的转址被当成校外网站", "where": "scope.py unwrap()",
        "docs": len(inside), "stats": f"{len(google)} 条链接、{len(targets)} 个不同目标，拆开后 {len(inside)} 个在学校范围内；"
                                      f"来自 {len(srcs)} 个页面",
        "units": _units(conn, srcs), "samples": _top(texts, 15), "recommend": "unblock",
        "options": [["unblock", "拆开（校内的照规则抓，校外的进候选）"], ["keep", "维持现状"]]})

    short = defaultdict(lambda: [0, set(), Counter()])
    for r in conn.execute("SELECT domain, n, sample FROM offsite"):
        if r["domain"] in scope.SHORTENERS:
            short[r["domain"]][0] += r["n"]
            short[r["domain"]][1].add(r["sample"])
    n_short = sum(v[0] for v in short.values())
    items.append({
        "id": "expand_shorteners", "title": "展开短网址（goo.gl、tinyurl、reurl.cc、bit.ly…）",
        "why": "短网址要先问一次短网址服务才知道真目标，目前只展开官方目录链到的", "where": "scope.py SHORTENERS / crawler",
        "docs": n_short, "stats": "；".join(f"{d} {v[0]} 条" for d, v in sorted(short.items(), key=lambda x: -x[1][0])),
        "units": [], "samples": [s for v in short.values() for s in list(v[1])[:3]][:12], "recommend": "redirect_only",
        "options": [["redirect_only", "只读跳转去哪里（每个短网址一次请求），真目标再照规则判断"], ["keep", "不展开"]]})

    imgs = content_images(root, conn)
    items.append({
        "id": "content_images", "title": "下载公告和内容页正文里的图片",
        "why": "抽链接的程序根本不看 <img>；回应是图片也不下载", "where": "links.py extract() / crawler.py SKIP_TYPES",
        "docs": len(imgs["content"]),
        "stats": f"扫了 {imgs['pages']} 个已存网页：{imgs['with_images']} 页正文有图；内容页图片 {len(imgs['content'])} 张（不同网址），"
                 f"相簿图片 {len(imgs['album'])} 张；约 {imgs['image_only']} 页正文几乎只有图（资讯在图里）",
        "units": _top(imgs["per_site"], 10), "samples": _top(imgs["samples"], 15), "recommend": "content",
        "options": [["content", "只下载内容页的图（不含相簿）"], ["all", "连相簿照片一起下载"], ["keep", "不下载图片"]]})

    media_rows = conn.execute("""SELECT l.dst, l.text, u.url AS page FROM link l JOIN url u ON u.id = l.src
                                  WHERE l.verdict='out:media'""").fetchall()
    album_imgs, other_imgs, per_site, texts = set(), set(), Counter(), Counter()
    for r in media_rows:
        if ALBUM.search(r["page"]) or ALBUM.search(r["dst"]):  # on a photo album page, or in an album folder
            album_imgs.add(r["dst"])
        else:
            other_imgs.add(r["dst"])
            per_site[scope.site_key(r["dst"])] += 1
            texts[(r["text"] or "").strip()[:40]] += 1
    items.append({
        "id": "linked_images", "title": "下载「链接直接指向图档」的图片（海报原图、平面图、简章图）",
        "why": "副档名是图片的链接一律不抓", "where": "scope.py MEDIA_EXT",
        "docs": len(other_imgs),
        "stats": f"{len(media_rows)} 条链接：相簿页上的 {len(album_imgs)} 张、其他 {len(other_imgs)} 张（不同网址）。其他的来自："
                 + "；".join(f"{s} {n}" for s, n in per_site.most_common(8)),
        "units": [], "samples": _top(texts, 15),
        "recommend": "non_album", "options": [["non_album", "下载，但相簿页上的不下载"], ["all", "全部下载"], ["keep", "不下载"]]})

    nh = defaultdict(lambda: [0, set(), Counter(), set()])
    for r in conn.execute("SELECT src, dst, text FROM link WHERE verdict='out:never_host'"):
        host = urlsplit(r["dst"]).hostname or ""
        nh[host][0] += 1
        nh[host][1].add(r["dst"])
        nh[host][2][(r["text"] or "").strip()[:50]] += 1
        nh[host][3].add(r["src"])
    for host, label, rec, opts in (
            ("drive.google.com", "Google 云端硬盘的档案（规章、申请表、课程表）", "drive_api",
             [["drive_api", "用 Drive API 读（同一个 Google Cloud 项目）"], ["keep", "只记链接"]]),
            ("docs.google.com", "Google 文件和试算表", "drive_api",
             [["drive_api", "用 Drive API / 导出功能读"], ["keep", "只记链接"]]),
            ("forms.gle", "Google 表单（报名、问卷）", "keep",
             [["keep", "只记链接（表单不是资讯）"], ["drive_api", "也试着读表单说明"]])):
        v = nh.get(host)
        if v is None:
            continue
        items.append({"id": f"never_{host.split('.')[0]}", "title": label, "why": "这类网址不抓网页，设计上改用 API",
                      "where": "scope.py NEVER_HOSTS", "docs": len(v[1]),
                      "stats": f"{v[0]} 条链接、{len(v[1])} 个不同档案，来自 {len(v[3])} 个页面",
                      "units": _units(conn, v[3]), "samples": _top(v[2], 15), "recommend": rec, "options": opts})

    honor_narrow, honor_notice = Counter(), Counter()
    honor_srcs = set()
    for r in conn.execute("SELECT src, dst, text FROM link WHERE verdict='skipped:honor'"):
        text = r["text"] or ""
        if not HONOR_NARROW.search(text):
            honor_narrow[text[:60]] += 1
            honor_srcs.add(r["src"])
            if NOTICE_WORDS.search(text):
                honor_notice[text[:60]] += 1
    items.append({
        "id": "honor_narrow", "title": "收窄荣誉规则：「名單」「錄取」「考取」单独出现不算荣誉榜",
        "why": "现在只要出现这几个字就只留标题，误挡导师名单、历年录取率、委员会名单等",
        "where": "core/pii.py HONOR_TITLE", "docs": len(honor_narrow),
        "stats": f"会被放行的链接 {sum(honor_narrow.values())} 条、{len(honor_narrow)} 种标题（真正的恭賀、榮獲、光榮榜仍然只留标题）",
        "units": _units(conn, honor_srcs), "samples": _top(honor_narrow, 20), "recommend": "narrow",
        "options": [["narrow", "收窄（只认恭賀、榮獲、光榮榜这类字）"], ["keep", "维持现状"]]})
    items.append({
        "id": "admission_notices", "title": "录取、核准名单公告：只收注意事项和日期，名单那段不收",
        "why": "这类公告正文常同时有注意事项和学生名单", "where": "core/pii.py + 处理层",
        "docs": len(honor_notice), "stats": f"标题带「注意事項／須知／報到／核發／備取」的 {len(honor_notice)} 种",
        "units": [], "samples": _top(honor_notice, 15), "recommend": "notice_only",
        "options": [["notice_only", "抓，但处理时去掉名单段落"], ["keep", "不抓（只留标题）"]]})

    for iid, title, prefix, rec in (
            ("allow_recruit_index", "放行招生报名首页（各管道报名期间）", "/recruit/index.aspx", "unblock"),
            ("allow_course_enroll", "放行课务查询首页（开课、教室、教学大纲查询入口）", "/staff/courseenrollinfo/courseenrollinfo.aspx",
             "unblock")):
        rows = conn.execute("SELECT src, text FROM link WHERE verdict='out:login' AND lower(dst) LIKE ?",
                            (f"https://sso.nknu.edu.tw{prefix}%",)).fetchall()
        items.append({"id": iid, "title": title, "why": "被「登录系统」路径规则误伤；网站实测是 200、没有密码栏",
                      "where": "scope.py LOGIN_PREFIXES / LOGIN_EXCEPTIONS", "docs": 1,
                      "stats": f"{len(rows)} 条链接", "units": _units(conn, {r['src'] for r in rows}),
                      "samples": _top(Counter((r["text"] or "")[:40] for r in rows), 8), "recommend": rec,
                      "options": [["unblock", "只放行这一页"], ["keep", "维持挡掉"]]})
    sections.append({"title": "一、规则：改了就能补回的资料", "items": sorted(items, key=lambda i: -i["docs"])})

    # ---- 2. 「校务系统」 seeds
    probe = json.loads(Path(PROBE).read_text(encoding="utf-8"))["sites"]
    recs = SYSTEM_RECOMMEND
    seed_items = []
    for r in probe:
        if r.get("k") != "校务系统" or r.get("c") not in ("direct", "render"):
            continue
        url = scope.canonical(r["u"]) or r["u"]
        n_links = conn.execute("SELECT count(*) FROM link WHERE dst=?", (url,)).fetchone()[0]
        row = conn.execute("SELECT state, reason FROM url WHERE url=?", (url,)).fetchone()
        site_pages = conn.execute("SELECT count(*) FROM url WHERE site=? AND state='done'", (scope.site_key(url),)).fetchone()[0]
        rec = next((v for k, v in recs if k in url.lower()), "check")
        seed_items.append({
            "id": "seed:" + url, "title": r["n"], "why": (r.get("d") or "")[:160], "where": url,
            "docs": site_pages, "stats": f"被 {n_links} 条链接指到；这个网站已抓 {site_pages} 页；目前状态："
                                         f"{(row['state'] + ' ' + (row['reason'] or '')) if row else '不在清单'}",
            "units": [], "samples": [], "recommend": rec,
            "options": [["keep_in", "收（公开资讯）"], ["drop", "不收（系统）"], ["check", "先查一下"]]})
    sections.append({"title": "二、「校务系统」起点：逐个决定收不收", "items": sorted(seed_items, key=lambda i: -i["docs"])})

    # ---- 3. off-campus candidates
    off_items = []
    for r in conn.execute("SELECT domain, n, sample FROM offsite ORDER BY n DESC"):
        d = r["domain"]
        if (d in scope.SHORTENERS or scope.NOT_A_SITE.search(d) or scope.INSTITUTIONS.search(d) or d.endswith("google.com")
                or r["n"] < 3):
            continue
        off_items.append({
            "id": "off:" + d, "title": d, "why": "校外网站，规则是进候选清单、每周确认", "where": r["sample"],
            "docs": r["n"], "stats": f"被链到 {r['n']} 次", "units": [], "samples": [r["sample"]],
            "recommend": "check", "options": [["admit", "收（写明出处）"], ["drop", "不收"], ["check", "以后再说"]]})
        if len(off_items) >= 60:
            break
    sections.append({"title": "三、校外网站候选（被链到 3 次以上，政府、外校、大平台已排除）", "items": off_items})

    # ---- 4. quarantine and albums
    q_items = []
    for reason, label, rec in (("system", "隔离区的 Moodle 页面（课程、讨论区；用户资料页其实没抓到）", "delete"),
                               ("trap", "路径陷阱的重复页面", "delete"), ("media", "timthumb 缩图", "delete")):
        n = conn.execute("SELECT count(*) FROM url WHERE reason=?", (f"quarantined_{reason}",)).fetchone()[0]
        q_items.append({"id": f"quarantine_{reason}", "title": label, "why": "规则更新前抓到", "where": "archive/quarantine/",
                        "docs": n, "stats": f"{n} 个网址", "units": [], "samples": [], "recommend": rec,
                        "options": [["delete", "永久删除"], ["keep", "保留在隔离区"]]})
    sections.append({"title": "四、隔离区", "items": q_items})
    return {"sections": sections}


# Claude's recommendation per 「校务系统」 seed (by URL fragment): public information in, systems out
SYSTEM_RECOMMEND = [
    ("email_desc", "keep_in"), ("campusactivities", "keep_in"), ("deptinfo", "keep_in"), ("teacherinfo", "keep_in"),
    ("freshmaninfo", "keep_in"), ("dengue", "keep_in"), ("subjectlist", "keep_in"), ("news.nknu", "keep_in"),
    ("lightnews", "keep_in"), ("highschoolstu", "keep_in"), ("procoursev2", "keep_in"), ("covid19", "keep_in"),
    ("regulations", "keep_in"), ("demo.aspx", "keep_in"), ("libdatademo", "keep_in"), ("coursemap", "keep_in"),
    ("statistics", "keep_in"), ("lastnewslist", "keep_in"), ("scheduledepartment", "keep_in"), ("isrcstusta", "keep_in"),
    ("stututors", "keep_in"), ("fm.nknu", "keep_in"), ("job-fair", "keep_in"),
    ("ssov3", "drop"), ("aio-admin-ai", "drop"), ("moodle", "drop"), ("survey.nknu", "drop"), ("ecp_system", "drop"),
    ("freshman/default", "drop"), ("regdb", "drop"), ("parkingpermit", "drop"), ("libactivity", "drop"),
    ("recruit/index", "keep_in"), ("/recruit", "drop"), ("c.nknu.edu.tw/space", "drop"), ("consumables", "drop"),
    ("stud_mails", "drop"), ("bookquery", "drop"), ("teachercertificate", "drop"), ("internationaladmissions", "drop"),
    ("pbws", "drop"), ("afats", "drop"), ("github.com", "check"), ("e.nknu", "check"), ("teachers.nknu.edu.tw/ecp", "check"),
    ("lostandfound", "drop"),
]
