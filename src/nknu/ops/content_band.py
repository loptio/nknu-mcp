"""內容's first band (2026-11-22 evening): 缺什麼 before what is there. 內容 is the page of every site (overview_page.html,
drawn once a library and kept as bytes); this band is drawn at each request above it, from what changes during a day:
the crawler's last night and this round's coverage (panorama.crawler_card, moved here from 總覽: what the crawler
missed is a question about the content), and what holds no document at all (sites with none in the library, units
with none or none in a year, nknu.ops.units_page). Above it a switch between the page by site and the page by unit
(單位, /units, which is no entry of the bar of its own any more). 【Claude】
"""

from __future__ import annotations

from urllib.parse import quote

from nknu.ops import panorama, units_page
from nknu.ops.look import e

SITES_SHOWN = 8  # sites with no document named before 還有 N 個

# 總覽's card styles without what is 總覽's own page (its main's width and padding, the colour scheme line)
CARD_CSS = panorama.CSS.replace(":root{color-scheme:light dark}", "")
CARD_CSS = CARD_CSS[: CARD_CSS.index("main{")] + CARD_CSS[CARD_CSS.index("}", CARD_CSS.index("main{")) + 1:]

CSS = """
.ct-band{margin:0 0 18px}
.ct-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px 16px;margin:4px 0 14px}
.ct-head h1{margin:0;font-size:20px;font-weight:700;letter-spacing:-.02em}
.ct-by{display:inline-flex;gap:2px;padding:3px;border:1px solid var(--line);border-radius:10px;background:var(--sunk)}
.ct-by a{padding:4px 12px;border-radius:7px;color:var(--ink2);text-decoration:none;font-size:13px}
.ct-by a[aria-current]{background:var(--surface);color:var(--ink);font-weight:650;box-shadow:var(--shadow)}
.ct-none{list-style:none;margin:0 0 10px;padding:0;display:flex;flex-direction:column;gap:6px;font-size:13px}
.ct-none li{display:flex;justify-content:space-between;gap:10px}
.ct-none a{color:var(--ink);text-decoration:none}
.ct-none a:hover{text-decoration:underline;text-underline-offset:3px}
.ct-none small{color:var(--ink3);white-space:nowrap}
.ct-units{display:flex;flex-wrap:wrap;gap:8px 18px;margin:12px 0 0;font-size:13px;color:var(--ink2)}
.ct-units b{font-size:18px;color:var(--ink);font-variant-numeric:tabular-nums;margin-right:4px}
"""


def switch(current: str) -> str:
    """The page by site (/sites) or by unit (/units)."""
    by = (("sites", "/sites", "按網站"), ("units", "/units", "按單位"))
    return "<nav class='ct-by' aria-label='內容怎麼排'>" + "".join(
        f"<a href='{href}'{' aria-current=page' if key == current else ''}>{word}</a>" for key, href, word in by
    ) + "</nav>"


def empty_card(sites: dict, tree: units_page.Tree | None) -> str:
    """What holds no document: the sites the library has none of, and the units with none or none in a year."""
    none = sorted((s for s in sites.get("sites") or [] if not s.get("b")), key=lambda s: s.get("n") or s.get("k") or "")
    lis = "".join(
        f"<li><a href='/site?site={e(quote(s['k'], safe=''))}'>{e(s.get('n') or s['k'])}</a>"
        f"<small>{e(s.get('u') or '')}</small></li>" for s in none[:SITES_SHOWN])
    more = f"<p class='pn-empty'>還有 {len(none) - SITES_SHOWN} 個</p>" if len(none) > SITES_SHOWN else ""
    body = (f"<div class='pn-subhd'><span>庫裡沒有文件的網站 {len(none)} 個</span></div><ul class='ct-none'>{lis}</ul>{more}"
            if none else "<p class='pn-empty'>每個網站都有文件進庫</p>")
    if tree is not None and tree.root is not None:
        empty, stale = tree.empty(), tree.stale()
        body += (f"<div class='pn-subhd pn-subhd-sep'><span>單位</span><a class='pn-link' href='/units'>看單位 →</a></div>"
                 f"<div class='ct-units'><span data-tip='自己和底下的單位都沒有發布的文件'><b>{len(empty)}</b>個沒有文件</span>"
                 f"<span data-tip='自己最新一份文件超過 {units_page.STALE_DAYS} 天'><b>{len(stale)}</b>個一年沒更新</span>"
                 f"<span><b>{len(tree.units)}</b>個單位</span></div>")
    return (f"<section class='pn-card pn-span5'><div class='pn-chd'><h2>沒有文件的</h2></div>{body}</section>")


def band(sites: dict, snap: dict, tonight: dict, tree: units_page.Tree | None) -> str:
    """The band, its styles with it: the page's title and the switch, then the crawler and what holds nothing."""
    crawl = panorama.crawler_card(snap, tonight).replace("pn-card pn-span5", "pn-card pn-span7", 1)
    return (f"<style>{CARD_CSS}{CSS}</style><section class='ct-band' id='crawl' aria-label='缺什麼'>"
            f"<div class='ct-head'><h1>內容</h1>{switch('sites')}</div>"
            f"<div class='pn-bento'>{crawl}{empty_card(sites, tree)}</div></section>")
