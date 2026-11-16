"""The top bar every panel shows (redesign 2026-11-17, research/dashboard_redesign_2026-11-17/diagnosis.md: 資料總覽 had
no way to the others, 檢視台 had tabs plus three links, 資料庫 links only). 【Claude】

Standard library only, no package imports: 資料總覽's server (deploy/lab-overview/server.py) loads this file by path
from its own folder, 資料庫's plugin from the inspect copy, 檢視台 as nknu.ops.theme.bar. The style is theme.css's .nk-*.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_OUT = ("<svg viewBox='0 0 16 16' width='11' height='11' fill='none' stroke='currentColor' stroke-width='1.5' "
        "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='M6.5 3.5H12.5V9.5M12.5 3.5 4 12'/>"
        "</svg>")


def panels(folder: Path = HERE) -> dict:
    """panels.json of folder: {'hosts': {host: origin}, 'pages': [{key, name, host, path, tip, away?}]}."""
    return json.loads((folder / "panels.json").read_text(encoding="utf-8"))


def theme_css(folder: Path = HERE) -> str:
    """theme.css of folder, the panels' one look."""
    return (folder / "theme.css").read_text(encoding="utf-8")


def bar(current: str, here: str, meta: str = "", folder: Path = HERE) -> str:
    """The bar as markup: current is the page's key (marked), here the host this page is served by (its own pages
    are linked by path, so they work on any port), meta an already-escaped bit of markup for the right end."""
    conf = panels(folder)
    links = []
    for page in conf["pages"]:
        href = page["path"] if page["host"] == here else conf["hosts"][page["host"]].rstrip("/") + page["path"]
        mark = " aria-current='page'" if page["key"] == current else ""
        away = " target='_blank' rel='noopener'" if page.get("away") else ""
        if page.get("away"):
            links.append("<span class='nk-sep' aria-hidden='true'></span>")
        links.append(f"<a href='{html.escape(href)}' data-tip='{html.escape(page['tip'])}'{mark}{away}>"
                     f"{html.escape(page['name'])}{_OUT if page.get('away') else ''}</a>")
    tail = f"<span class='nk-meta'>{meta}</span>" if meta else ""
    return f"<header class='nk-bar'><nav class='nk-tabs' aria-label='面板'>{''.join(links)}</nav>{tail}</header>"
