"""單位 (檢視台 /units, a tool of the 工具 menu): the unit tree of the library with, for each unit, how many documents it
published, which sites they came from and the newest date among them, so that a unit with no document of its own, or
none in a year, stands out (no silent gaps: a unit's documents can be missing while every site looks complete, since
one site holds many units' and one unit's can sit on several sites).

A unit's own count is what doc_scope names it the publisher of; a parent's line adds its units' (a college with its
departments: a sum of their counts, so a document two of them published is counted twice). The newest date is the
latest published or inherited date (nknu.content.dates) not after today. Read only: four queries over units,
doc_scope, docs and dates. 【Claude】
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote

from nknu.ops.look import e, tw

STALE_DAYS = 365  # a unit whose newest document is older than this is marked 一年沒更新
OPEN_DEPTH = 1  # the school and its first level start unfolded; deeper levels fold
SITES_SHOWN = 3
# siblings in the order the school lists them: offices, their sections, colleges, departments, programmes, centres
KIND_ORDER = {"学校": 0, "行政单位": 1, "行政组别": 2, "学院": 3, "系所": 4, "学程": 5, "中心": 6}


@dataclass
class Unit:
    unit_id: str
    name: str
    kind: str
    parent_id: str | None
    campus: str | None
    docs: int = 0
    newest: str | None = None
    sites: dict[str, int] = field(default_factory=dict)
    children: list[Unit] = field(default_factory=list)
    total: int = 0  # its own documents and its units'
    total_newest: str | None = None


@dataclass
class Tree:
    root: Unit | None
    units: list[Unit]  # every unit, the root first, in the order the page shows them
    today: date

    def empty(self) -> list[Unit]:
        return [u for u in self.units if u.docs == 0 and u.total == 0]

    def stale(self) -> list[Unit]:
        cut = (self.today - timedelta(days=STALE_DAYS)).isoformat()
        return [u for u in self.units if u.docs and u.newest is not None and u.newest < cut]


def load(conn: sqlite3.Connection, today: date) -> Tree:
    """The tree as the library holds it; a library without the unit tables gives an empty tree."""
    try:
        rows = conn.execute("SELECT unit_id, name, kind, parent_id, campus FROM units").fetchall()
    except sqlite3.OperationalError:  # no units table: a library built before the unit tree
        return Tree(None, [], today)
    units = {r[0]: Unit(*r) for r in rows}
    pub = "FROM doc_scope s WHERE s.relation = 'publisher'"
    for uid, n in conn.execute(f"SELECT s.unit_id, count(DISTINCT s.doc_id) {pub} GROUP BY s.unit_id"):
        if uid in units:
            units[uid].docs = n
    for uid, site, n in conn.execute("SELECT s.unit_id, d.site, count(DISTINCT s.doc_id) FROM doc_scope s "
                                     "JOIN docs d ON d.doc_id = s.doc_id WHERE s.relation = 'publisher' "
                                     "GROUP BY s.unit_id, d.site"):
        if uid in units and site:
            units[uid].sites[site] = n
    for uid, newest in conn.execute("SELECT s.unit_id, max(t.date) FROM doc_scope s JOIN dates t ON t.doc_id = "
                                    "s.doc_id WHERE s.relation = 'publisher' AND t.kind IN ('published', 'inherited') "
                                    "AND t.date <= ? GROUP BY s.unit_id", (today.isoformat(),)):
        if uid in units:
            units[uid].newest = newest
    roots = [u for u in units.values() if not u.parent_id or u.parent_id not in units]
    root = next((u for u in roots if u.kind == "学校"), roots[0] if roots else None)
    for u in units.values():
        if u is root:
            continue
        parent = units.get(u.parent_id or "") or root
        if parent is not None:
            parent.children.append(u)
    order: list[Unit] = []
    if root is not None:
        _sum(root)
        _walk(root, order)
    return Tree(root, order, today)


def _sum(u: Unit) -> None:
    for c in u.children:
        _sum(c)
    u.children.sort(key=lambda c: (KIND_ORDER.get(c.kind, 9), -c.total, c.name))
    u.total = u.docs + sum(c.total for c in u.children)
    dates = [d for d in [u.newest, *(c.total_newest for c in u.children)] if d]
    u.total_newest = max(dates) if dates else None


def _walk(u: Unit, out: list[Unit]) -> None:
    out.append(u)
    for c in u.children:
        _walk(c, out)


# ---------------------------------------------------------------------------------------------------- the page

CSS = """
.ut-tiles{display:flex;gap:12px;flex-wrap:wrap;margin:4px 0 14px}
.ut-tile{background:var(--page);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.ut-tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.ut-tile.bad b{color:var(--bad-ink)}.ut-tile.warn b{color:var(--warn-ink)}
.ut-chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px}
.ut-chips a{text-decoration:none}
.ut-tree{list-style:none;margin:0;padding:0}
.ut-tree ul{list-style:none;margin:0;padding-left:22px;border-left:1px solid var(--line)}
.ut-row{display:grid;grid-template-columns:minmax(180px,2fr) minmax(120px,3fr) 70px 92px minmax(140px,2fr);
  gap:10px;align-items:center;padding:5px 6px;border-radius:6px}
.ut-row:hover{background:var(--hover)}
.ut-tree summary{list-style:none;cursor:pointer}.ut-tree summary::-webkit-details-marker{display:none}
.ut-name{display:flex;gap:6px;align-items:baseline;min-width:0}
.ut-name b{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ut-name small{color:var(--ink3);white-space:nowrap}
.ut-twist{display:inline-block;width:12px;color:var(--ink3);transition:transform .15s}
details[open]>summary .ut-twist{transform:rotate(90deg)}
.ut-bar{height:10px;border-radius:5px;background:var(--hover);overflow:hidden}
.ut-bar i{display:block;height:100%;background:var(--act);border-radius:5px}
.ut-bar.none i{background:transparent}
.ut-n{text-align:right;font-variant-numeric:tabular-nums}
.ut-n small{display:block;color:var(--ink3);font-size:11px}
.ut-date{font-variant-numeric:tabular-nums;color:var(--ink2)}
.ut-date.warn{color:var(--warn-ink)}
.ut-sites{font-size:12px;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ut-sites a{color:inherit}
@media (max-width:760px){.ut-row{grid-template-columns:1fr 70px}.ut-bar,.ut-date,.ut-sites{display:none}}
"""


def body(conn: sqlite3.Connection, today: date) -> str:
    tree = load(conn, today)
    if tree.root is None:
        return ("<section class='card pad'><span class='pill none'>沒有單位表</span>　"
                "這份庫裡沒有 units 表（單位樹之前建的庫），看不了單位。</section>")
    empty, stale = tree.empty(), tree.stale()
    with_docs = sum(1 for u in tree.units if u.docs)
    tiles = ("<div class='ut-tiles'>"
             f"<div class='ut-tile' data-tip='庫裡的單位樹：全校、處室和組、學院、系所、中心'><b>{len(tree.units)}</b>個單位</div>"
             f"<div class='ut-tile good' data-tip='有自己發布的文件'><b>{with_docs}</b>有自己的文件</div>"
             f"<div class='ut-tile bad' data-tip='自己和底下的單位都沒有發布的文件：可能沒抓到它的網站，"
             f"也可能它的文件掛在別的單位名下'><b>{len(empty)}</b>沒有文件</div>"
             f"<div class='ut-tile warn' data-tip='自己最新一份文件超過 {STALE_DAYS} 天'><b>{len(stale)}</b>一年沒更新</div>"
             "</div>")
    index = {u.unit_id: i for i, u in enumerate(tree.units)}
    chips = ""
    if empty:
        chips = ("<div class='ut-chips'>" + "".join(
            f"<a href='#u{index[u.unit_id]}' class='pill bad'>{e(tw(u.name))}</a>" for u in empty) + "</div>")
    top = max((u.total for u in tree.units), default=0)
    tree_html = "<ul class='ut-tree'>" + _node(tree.root, 0, top, index, tree) + "</ul>"
    head = ("<section class='card pad'><h2>單位</h2><p class='faint small'>每個單位自己發布了多少文件、來自哪些網站、"
            "最新一份是哪天。上一層的數字加上底下的單位。點單位名展開；點網站名看那個網站從抓取到進庫的每一步。</p>"
            f"{tiles}{chips}{tree_html}</section>")
    return f"<style>{CSS}</style>{head}<script>{JS}</script>"


# a chip's unit can sit in a folded branch: following its link unfolds the branches above it
JS = ("function utOpen(){const t=document.getElementById(decodeURIComponent(location.hash.slice(1)));if(!t)return;"
      "for(let p=t.parentElement;p;p=p.parentElement){if(p.tagName==='DETAILS')p.open=true}"
      "t.scrollIntoView({block:'center'})}addEventListener('hashchange',utOpen);utOpen()")


def _node(u: Unit, depth: int, top: int, index: dict[str, int], tree: Tree) -> str:
    row = _row(u, top, index[u.unit_id], tree, folds=bool(u.children))
    if not u.children:
        return f"<li>{row}</li>"
    kids = "".join(_node(c, depth + 1, top, index, tree) for c in u.children)
    opened = " open" if depth < OPEN_DEPTH else ""
    return f"<li><details{opened}><summary>{row}</summary><ul>{kids}</ul></details></li>"


def _row(u: Unit, top: int, i: int, tree: Tree, *, folds: bool) -> str:
    stale_cut = (tree.today - timedelta(days=STALE_DAYS)).isoformat()
    twist = "<span class='ut-twist'>▸</span>" if folds else "<span class='ut-twist'></span>"
    where = f" · {tw(u.campus)}" if u.campus else ""
    name = (f"<span class='ut-name'>{twist}<b>{e(tw(u.name))}</b><small>{e(tw(u.kind))}{e(where)}</small></span>")
    width = 0.0 if top <= 0 or u.total <= 0 else 100 * math.log10(u.total + 1) / math.log10(top + 1)
    bar = (f"<span class='ut-bar{' none' if u.total == 0 else ''}' data-tip='寬度按文件數的對數畫：小單位也看得到'>"
           f"<i style='width:{width:.1f}%'></i></span>")
    own = f"<small>自己 {u.docs:,}</small>" if u.children and u.docs != u.total else ""
    count = f"<span class='ut-n'>{u.total:,}{own}</span>"
    newest = u.newest or u.total_newest
    if u.total == 0:
        when = "<span class='pill bad'>沒有文件</span>"
    elif newest is None:
        when = "<span class='ut-date' data-tip='文件都沒有發布日期'>沒日期</span>"
    elif u.docs and u.newest and u.newest < stale_cut:
        when = f"<span class='ut-date warn' data-tip='自己最新一份文件超過 {STALE_DAYS} 天'>{e(u.newest)}</span>"
    else:
        when = f"<span class='ut-date'>{e(newest)}</span>"
    shown = sorted(u.sites.items(), key=lambda kv: -kv[1])
    links = "、".join(f"<a href='/site?site={quote(s)}'>{e(s)}</a> {n:,}" for s, n in shown[:SITES_SHOWN])
    more = f" 等 {len(shown)} 個網站" if len(shown) > SITES_SHOWN else ""
    tip = "\n".join(f"{s}：{n:,}" for s, n in shown)
    sites = f"<span class='ut-sites' data-tip='{e(tip)}'>{links}{e(more)}</span>" if shown else "<span></span>"
    return f"<div class='ut-row' id='u{i}'>{name}{bar}{count}{when}{sites}</div>"
