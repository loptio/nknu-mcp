"""The top bar every panel shows (redesign 2026-11-17, research/dashboard_redesign_2026-11-17/diagnosis.md: 資料總覽 had
no way to the others, 檢視台 had tabs plus three links, 資料庫 links only). 【Claude】

Since 2026-11-22 one row that stays on top as a page scrolls: 總覽 first (the front page, /), then the pages, then the
tools in the same row (no more 工具 menu at the right end), folded into 更多 on a narrow window; bar() below. The
history that follows is of the entries' names.

Since the structure design of 2026-11-17 (docs/superpowers/specs/2026-11-17-panels-structure-design.md section 二; owner:
「可以按你的建议走吧，不用问我了」) the bar holds four entries, 每晚運行 | 網站 | 搜尋 | 找文件 (panels.json "pages"; 今晚 | 網站 | 試用 | 文件 until the owner's
「一个页面叫"今晚"你觉得合理吗」, 11-17), and at its
right end a small 「工具」 menu (a native <details>: it opens with a click, Enter or Space, Escape closes it) holding the
tools that are no entry of their own, 網頁核查 and 資料庫 (panels.json "tools"; 爬蟲進度, the crawler's own board, until
the panels became one on 2026-11-21). A page of a tool marks the menu's button, not an entry.

Standard library only, no package imports: 資料庫's plugin loads this file from the inspect copy, 檢視台 as
nknu.ops.theme.bar (網站, 資料總覽's page, is 檢視台's own since 2026-11-21). The style is theme.css's .nk-*.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent
_OUT = ("<svg viewBox='0 0 16 16' width='11' height='11' fill='none' stroke='currentColor' stroke-width='1.5' "
        "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='M6.5 3.5H12.5V9.5M12.5 3.5 4 12'/>"
        "</svg>")
_DOWN = ("<svg viewBox='0 0 16 16' width='11' height='11' fill='none' stroke='currentColor' stroke-width='1.6' "
         "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='m4 6.2 4 4 4-4'/></svg>")
_MODE = ("<svg viewBox='0 0 16 16' width='14' height='14' fill='none' aria-hidden='true'><circle cx='8' cy='8' r='5.5' "
         "stroke='currentColor' stroke-width='1.5'/><path d='M8 2.5a5.5 5.5 0 0 1 0 11z' fill='currentColor'/></svg>")
# 更多 (the tools, folded on a narrow window) closes as a menu does: a click outside it, Escape (back to its button),
# or the focus leaving it. Without the script it still opens and closes by its button.
_MENU_JS = ("<script>(()=>{const t=document.querySelector('.nk-bar details.nk-more');if(!t)return;"
            "const s=t.querySelector('summary');"
            "document.addEventListener('click',e=>{if(t.open&&!t.contains(e.target))t.open=false});"
            "document.addEventListener('keydown',e=>{if(e.key==='Escape'&&t.open){t.open=false;s.focus()}});"
            "t.addEventListener('focusout',e=>{if(t.open&&e.relatedTarget&&!t.contains(e.relatedTarget))t.open=false})"
            "})();</script>")
# light or dark: the system's until the switch is used, then the one picked (kept in this browser; theme.css reads
# :root[data-theme]); a page drawn by a script repaints on the attribute's change (charts.py watches it)
_MODE_JS = ("<script>(()=>{const r=document.documentElement,k='nknu-theme';let s=null;"
            "try{s=localStorage.getItem(k)}catch(e){}if(s==='light'||s==='dark')r.setAttribute('data-theme',s);"
            "const b=document.querySelector('.nk-bar .nk-mode');if(!b)return;"
            "const dark=()=>r.hasAttribute('data-theme')?r.getAttribute('data-theme')==='dark'"
            ":matchMedia('(prefers-color-scheme: dark)').matches;"
            "const say=()=>{const w=dark()?'換成淺色':'換成深色';b.setAttribute('aria-label',w);b.setAttribute('data-tip',w)};"
            "b.addEventListener('click',()=>{const n=dark()?'light':'dark';r.setAttribute('data-theme',n);"
            "try{localStorage.setItem(k,n)}catch(e){}say()});say()})();</script>")
# Every link from one panel to another is written with the ports of the owner's Mac (panels.json "hosts": 18878…; so are
# 資料庫's queries and 資料總覽's script). Opened on the machine that serves the panels (the lab PC: 127.0.0.1:8878),
# nothing listens on those ports, and every jump failed (2026-11-18). A page opened on one of the serving machine's own
# ports (panels.json "local") rights such links to that family of ports: the ones in the markup once the page has
# loaded, the ones a script draws later as the pointer or the keyboard reaches them (before a click, a middle click or
# the context menu acts on them). The find box's form (find_box: 資料庫's sends to 檢視台's /doc) is righted with the
# links. Opened through the tunnel it does nothing. %s is ports()'s map as JSON.
_PORT_JS = ("<script>(()=>{const M=%s,H=['127.0.0.1','localhost','[::1]'];"
            "if(!Object.values(M).includes(location.port))return;"
            "const fix=a=>{if(!a)return;const k=a.tagName==='FORM'?'action':'href';let u;try{u=new URL(a[k])}"
            "catch(e){return}"
            "if(H.includes(u.hostname)&&M[u.port]){u.port=M[u.port];u.hostname=location.hostname;a[k]=u.href}};"
            "const all=()=>document.querySelectorAll('a[href],form[action]').forEach(fix);"
            "all();document.addEventListener('DOMContentLoaded',all);"
            "for(const t of['pointerdown','mousedown','focusin','keydown','click','auxclick','contextmenu'])"
            "document.addEventListener(t,e=>fix(e.target&&e.target.closest&&e.target.closest('a[href]')),true)"
            "})();</script>")


def panels(folder: Path = HERE) -> dict:
    """panels.json of folder: {'hosts': {host: origin}, 'local': {host: origin}, 'pages': [{key, name, host, path,
    tip}], 'tools': [{key, name, host, path, tip, away?}], 'site': {host, path}}."""
    return json.loads((folder / "panels.json").read_text(encoding="utf-8"))


def ports(conf: dict) -> dict:
    """{a panel's port on the owner's Mac: its port on the machine that serves it}, both as text, from panels.json's
    "hosts" and "local" (a panel with one of the two, or the same port in both, has no line)."""
    out = {}
    for key, near in conf.get("local", {}).items():
        far = conf.get("hosts", {}).get(key, "")
        a, b = urlsplit(far).port, urlsplit(near).port
        if a and b and a != b:
            out[str(a)] = str(b)
    return out


def port_js(conf: dict) -> str:
    """The script that rights the links to the other panels on a page opened on the serving machine's own ports ('' when
    panels.json names no such ports)."""
    table = ports(conf)
    return _PORT_JS % json.dumps(table, sort_keys=True) if table else ""


def theme_css(folder: Path = HERE) -> str:
    """theme.css of folder, the panels' one look."""
    return (folder / "theme.css").read_text(encoding="utf-8")


def _href(item: dict, here: str, conf: dict) -> str:
    """An entry's or a tool's address as a page served by here links it: its path alone on its own host."""
    return item["path"] if item["host"] == here else conf["hosts"][item["host"]].rstrip("/") + item["path"]


def entry_href(key: str, here: str, folder: Path = HERE) -> str:
    """The address of the entry or tool named key, as a page served by here links it ('' for a key there is none)."""
    conf = panels(folder)
    item = next((x for x in [*conf["pages"], *conf.get("tools", [])] if x["key"] == key), None)
    return _href(item, here, conf) if item else ""


def site_href(key: str, here: str, folder: Path = HERE) -> str:
    """The address of one site's page (panels.json "site": 檢視台's /site?site=…) as a page served by here links it."""
    conf = panels(folder)
    return _href(conf["site"], here, conf) + quote(key, safe="")


def site_prefix(here: str, folder: Path = HERE) -> str:
    """What the address of one site's page starts with, as a page served by here links it (its key goes after it)."""
    conf = panels(folder)
    return _href(conf["site"], here, conf)


def _link(item: dict, current: str, here: str, conf: dict, cls: str = "") -> str:
    """One entry or tool as a link: marked when it is this page (aria-current='page') or the entry this page is reached
    from (panels.json "within": aria-current='true'); another panel's (資料庫) with the arrow out."""
    mark = (" aria-current='page'" if item["key"] == current
            else " aria-current='true'" if conf.get("within", {}).get(current) == item["key"] else "")
    away = " target='_blank' rel='noopener'" if item.get("away") else ""
    out = _OUT if item["host"] != here else ""
    return (f"<a{cls} href='{html.escape(_href(item, here, conf))}' data-tip='{html.escape(item['tip'])}'{mark}{away}>"
            f"{html.escape(item['name'])}{out}</a>")


_FIND = ("<svg viewBox='0 0 16 16' width='14' height='14' fill='none' stroke='currentColor' stroke-width='1.6' "
         "stroke-linecap='round' aria-hidden='true'><circle cx='7' cy='7' r='4.5'/><path d='M10.5 10.5 14 14'/></svg>")


def find_box(current: str, here: str, conf: dict) -> str:
    """The one box that finds a document, an address or a site (panels.json "find": 文件搜尋, /doc), in the bar of every
    page since 2026-11-22 evening (it was a page of its own whose page was only this box). The page it answers on
    keeps what was typed (its own box), so the bar's starts empty."""
    find = conf.get("find")
    if not find:
        return ""
    on = " nk-on" if current == "doc" else ""
    return (f"<form class='nk-find{on}' role='search' action='{html.escape(_href(find, here, conf))}' method='get'>"
            f"{_FIND}<input type='search' name='{html.escape(find['name'])}' "
            f"placeholder='{html.escape(find['placeholder'])}' aria-label='{html.escape(find['placeholder'])}' "
            f"data-tip='{html.escape(find['tip'])}' autocomplete='off' enterkeyhint='search'></form>")


def bar(current: str, here: str, meta: str = "", folder: Path = HERE) -> str:
    """The bar as markup, one row on top of every page (2026-11-22; owner: the pages at the top left and the tools at
    the top right were two places to look, and 總覽 had a column of its own): the entries, a thin line, the tools a
    shade lighter in the same row; at the right end only meta (an already-escaped bit of markup: when the library was
    built) and the light/dark switch. On a narrow window the tools fold into 更多 (theme.css). current is the page's
    key (its entry or tool is marked; a tool's also marks 更多), here the host this page is served by (its own pages are
    linked by path, so they work on any port)."""
    conf = panels(folder)
    links = [_link(p, current, here, conf) for p in conf["pages"]]
    tools = conf.get("tools", [])
    inline = [_link(t, current, here, conf, " class='nk-tool'") for t in tools]
    items = [_link(t, current, here, conf) for t in tools]
    on = any(t["key"] == current for t in tools)
    more = (f"<span class='nk-sep' aria-hidden='true'></span>{''.join(inline)}"
            f"<details class='nk-more{' nk-on' if on else ''}'><summary>更多{_DOWN}</summary>"
            f"<div class='nk-menu'>{''.join(items)}</div></details>" if tools else "")
    tail = f"<span class='nk-meta'>{meta}</span>" if meta else ""
    mode = f"<button type='button' class='nk-mode' aria-label='換成深色'>{_MODE}</button>"
    return (f"<header class='nk-bar'><nav class='nk-tabs' aria-label='面板'>{''.join(links)}{more}</nav>"
            f"{find_box(current, here, conf)}<div class='nk-end'>{tail}{mode}</div></header>" + (_MENU_JS if tools else "") + _MODE_JS
            + port_js(conf))
