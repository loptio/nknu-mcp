"""The top bar every panel shows (redesign 2026-11-17, research/dashboard_redesign_2026-11-17/diagnosis.md: 資料總覽 had
no way to the others, 檢視台 had tabs plus three links, 資料庫 links only). 【Claude】

Since the structure design of 2026-11-17 (docs/superpowers/specs/2026-11-17-panels-structure-design.md section 二; owner:
「可以按你的建议走吧，不用问我了」) the bar holds four entries, 今晚 | 網站 | 試用 | 文件 (panels.json "pages"), and at its
right end a small 「工具」 menu (a native <details>: it opens with a click, Enter or Space, Escape closes it) holding the
two tools that are no entry of their own, 資料庫 and 爬蟲進度 (panels.json "tools"). A page of a tool marks the menu's
button, not an entry.

Standard library only, no package imports: 資料總覽's server (deploy/lab-overview/server.py) loads this file by path
from its own folder, 資料庫's plugin from the inspect copy, 檢視台 as nknu.ops.theme.bar. The style is theme.css's .nk-*.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
_OUT = ("<svg viewBox='0 0 16 16' width='11' height='11' fill='none' stroke='currentColor' stroke-width='1.5' "
        "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='M6.5 3.5H12.5V9.5M12.5 3.5 4 12'/>"
        "</svg>")
_DOWN = ("<svg viewBox='0 0 16 16' width='11' height='11' fill='none' stroke='currentColor' stroke-width='1.6' "
         "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='m4 6.2 4 4 4-4'/></svg>")
# The menu closes as a menu does: a click outside it, Escape (back to its button), or the focus leaving it. Without
# the script the menu still opens and closes by its button.
_MENU_JS = ("<script>(()=>{const t=document.querySelector('.nk-bar details.nk-tools');if(!t)return;"
            "const s=t.querySelector('summary');"
            "document.addEventListener('click',e=>{if(t.open&&!t.contains(e.target))t.open=false});"
            "document.addEventListener('keydown',e=>{if(e.key==='Escape'&&t.open){t.open=false;s.focus()}});"
            "t.addEventListener('focusout',e=>{if(t.open&&e.relatedTarget&&!t.contains(e.relatedTarget))t.open=false})"
            "})();</script>")


def panels(folder: Path = HERE) -> dict:
    """panels.json of folder: {'hosts': {host: origin}, 'pages': [{key, name, host, path, tip}], 'tools': [{key, name,
    host, path, tip, away?}], 'site': {host, path}}."""
    return json.loads((folder / "panels.json").read_text(encoding="utf-8"))


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


def bar(current: str, here: str, meta: str = "", folder: Path = HERE) -> str:
    """The bar as markup: current is the page's key (an entry's is marked; a tool's marks the 工具 button and its line
    in the menu), here the host this page is served by (its own pages are linked by path, so they work on any port),
    meta an already-escaped bit of markup for the right end, before the menu."""
    conf = panels(folder)
    links = []
    for page in conf["pages"]:
        mark = " aria-current='page'" if page["key"] == current else ""
        links.append(f"<a href='{html.escape(_href(page, here, conf))}' data-tip='{html.escape(page['tip'])}'{mark}>"
                     f"{html.escape(page['name'])}</a>")
    tools = conf.get("tools", [])
    items = []
    for tool in tools:
        mark = " aria-current='page'" if tool["key"] == current else ""
        away = " target='_blank' rel='noopener'" if tool.get("away") else ""
        items.append(f"<a href='{html.escape(_href(tool, here, conf))}' data-tip='{html.escape(tool['tip'])}'"
                     f"{mark}{away}>{html.escape(tool['name'])}{_OUT}</a>")
    on = any(t["key"] == current for t in tools)
    menu = (f"<details class='nk-tools{' nk-on' if on else ''}'><summary>工具{_DOWN}</summary>"
            f"<div class='nk-menu'>{''.join(items)}</div></details>" if items else "")
    tail = f"<span class='nk-meta'>{meta}</span>" if meta else ""
    return (f"<header class='nk-bar'><nav class='nk-tabs' aria-label='面板'>{''.join(links)}</nav>"
            f"<div class='nk-end'>{tail}{menu}</div></header>" + (_MENU_JS if items else ""))
