"""Datasette plugin: what the pages need to speak the owner's language (owner 11-15: 「目前数据库的界面是我完全看不懂 连有
几个表 怎么连的 每个表和字段代表什么都还不知道」, then, of the panels: 「太難看懂字太雜了…沒有用一些視覺化方法點綴，顏色也沒有
考慮過」).

For the home page (templates/index.html) of the official library: every table the owner can read, how many rows each
holds, how the tables join, what every column means, and the sites to pick from. For every page: the other panels'
addresses, each table's name in Chinese, a short Chinese name for a column where its meaning starts with one, and the
words for the values the library stores as codes (facets and cells show the word; the code floats over it).

The meanings are metadata.yaml's (one place for them); the tables' order, their names on the page, their groups and
places on the map and the joins are here 【Claude】 (the joins as src/nknu/content/schema.py's comments describe them:
the library declares no foreign keys, so Datasette cannot draw them itself).

Loaded by `datasette --plugins-dir`; no other code imports it."""

from __future__ import annotations

import math
import re

try:
    from datasette import hookimpl
except ImportError:  # the tests load this file where Datasette is not installed

    def hookimpl(fn):
        return fn


def _theme_module():
    """nknu/ops/theme/bar.py, loaded by path: the datasette service runs without the inspect copy's src on its
    PYTHONPATH (compose.yaml gives it only to mcp and ops); bar.py is standard library only."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "src" / "nknu" / "ops" / "theme" / "bar.py"
    spec = importlib.util.spec_from_file_location("nk_theme_bar", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


theme = _theme_module()

DATABASE = "aca"
# the panels' one top bar and look (nknu/ops/theme: the inspect copy's src is on PYTHONPATH, compose.yaml): this
# panel's key and host are both 'tables' (redesign 2026-11-17); since the structure design of 2026-11-17 (section 二)
# 資料庫 is a tool, so its pages mark the bar's 工具 menu, not one of its four entries
BAR = theme.bar("tables", "tables")
THEME = theme.theme_css()
# Datasette's words for a filter's comparisons -> the page's (templates/table.html)
OPS = {"exact": "等於", "not": "不等於", "contains": "包含", "endswith": "結尾是", "startswith": "開頭是", "gt": "大於",
       "gte": "大於等於", "lt": "小於", "lte": "小於等於", "like": "像（like）", "notlike": "不像（not like）",
       "glob": "像（glob）", "in": "是其中之一", "notin": "不是其中之一", "arraycontains": "陣列裡有",
       "arraynotcontains": "陣列裡沒有", "date": "日期是", "isnull": "是空的（null）", "notnull": "不是空的",
       "isblank": "是空白", "notblank": "不是空白"}
# 檢視台's addresses, from panels.json (one place for them): a document's trace, and one site's page (the home's list
# of sites links a site's name there: structure design 2026-11-17 section 二)
TRACE = theme.panels()["hosts"]["ops"].rstrip("/") + "/trace?ref="
SITE_PAGE = theme.site_prefix("tables")
# the map's groups: (key, the caption over its tables)
GROUPS = (("site", "網站和建庫紀錄"), ("docs", "文件和它的資料"), ("unit", "單位"), ("index", "搜尋用的索引"))
# (table, its name on the page, group, x and y of its box on the map, joins: (column, table it joins, that column))
TABLES = (
    ("docs", "文件", "docs", 380, 280, (("site", "sites", "site"), ("parent_id", "docs", "doc_id"))),
    ("sites", "網站", "site", 560, 70, ()),
    ("meta", "建庫紀錄", "site", 740, 70, ()),
    ("dates", "讀到的日期", "docs", 110, 160, (("doc_id", "docs", "doc_id"),)),
    ("doc_alias", "合併紀錄", "docs", 110, 240, (("doc_id", "docs", "doc_id"),)),
    ("doc_refs", "附件連結", "docs", 110, 320, (("doc_id", "docs", "doc_id"), ("ref_id", "docs", "doc_id"))),
    ("applies_to", "適用的入學屆", "docs", 110, 400, (("doc_id", "docs", "doc_id"),)),
    ("doc_scope", "文件和單位", "unit", 640, 210, (("doc_id", "docs", "doc_id"), ("unit_id", "units", "unit_id"))),
    ("units", "單位樹", "unit", 840, 210, (("parent_id", "units", "unit_id"),)),
    ("unit_aliases", "單位別名", "unit", 840, 290, (("unit_id", "units", "unit_id"),)),
    ("sections", "章節", "index", 560, 440, (("doc_id", "docs", "doc_id"),)),
    ("chunks", "小段", "index", 740, 440, (("doc_id", "docs", "doc_id"), ("sec_id", "sections", "sec_id"))),
    ("chunk_fts", "關鍵字索引", "index", 920, 410, (("rowid", "chunks", "chunk_id"),)),
    ("chunk_vecs", "向量索引", "index", 920, 490, (("chunk_id", "chunks", "chunk_id"),)),
)
BOX_W, BOX_H = 150, 56
MAP_W, MAP_H = 1012, 546
NAMES = {table: name for table, name, *_ in TABLES}
# the values the library stores as codes, by column: what a facet or a cell shows instead (the code stays in its title)
WORDS = {
    "kind_class": {"notice": "公告", "page": "一般網頁", "attachment": "附件", "event": "行事曆", "link": "校外連結"},
    "tier": {"primary": "主料", "background": "背景"},
    "ident_kind": {"published": "公告日期", "inherited": "跟所屬公告", "server": "伺服器檔案時間"},
    "kind": {"published": "公告日期", "inherited": "跟所屬公告", "server": "伺服器檔案時間", "period_start": "期間開始",
             "period_end": "期間結束", "event_start": "事件開始", "event_end": "事件結束"},
    "doc_type": {"journal": "學報校刊", "exam": "考題", "minutes": "會議紀錄"},
    "why": {"copy": "別的網址的同一份", "format": "同一份的另一種格式", "duplicate": "重複的紀錄"},
    "relation": {"publisher": "誰發的", "applies_to": "適用於", "mentions": "提到"},
    "zone": {"content": "正文", "attachments": "附件區", "nav": "選單", "unknown": "找不到"},
    "text_source": {"totext": "這次轉的", "none": "沒有"},
}
# a canned query's parameter, as its form names it
PARAMS = {"site": "網站", "words": "標題裡的字", "doc_id": "文件編號", "alias": "被合併的編號", "why": "原因（可不填）"}
# a kind's code -> the class that gives its dot the kind's hue (static/tc.css; the same pairs as plugins/cells.py)
KIND_CLASS = {"notice": "k0", "page": "k1", "attachment": "k2", "event": "k3", "link": "k4"}
SHORT = 8  # a column's short name is the start of its meaning when that is at most this long 【Claude】
_CACHE: dict = {}


def short_name(meaning: str) -> str:
    """The Chinese name a column's meaning starts with (「種類：notice 公告…」 -> 種類; metadata.yaml writes every
    meaning as 「名稱：說明」 or as the name alone), or '' when the meaning is a sentence with no such start."""
    head = re.split(r"[：:。，,（(；;]", (meaning or "").strip(), maxsplit=1)[0].strip()
    return head if 0 < len(head) <= SHORT and " " not in head else ""


def rest_of(meaning: str) -> str:
    """A meaning without the name it starts with: what is left to say beside that name ('' when the name is all)."""
    meaning = (meaning or "").strip()
    name = short_name(meaning)
    if not name:
        return meaning
    return meaning[len(name):].lstrip("：:。 ").strip()


def _place(name: str) -> tuple[float, float]:
    for table, _, _, x, y, _ in TABLES:
        if table == name:
            return x, y
    raise KeyError(name)


def edges() -> list[dict]:
    """One line per pair of joined tables (two joins between the same pair share a line and a label), from box
    centre to box centre; a table that joins itself gets a loop."""
    pairs: dict[tuple[str, str], list[str]] = {}
    for table, _, _, _, _, joins in TABLES:
        for column, other, _ in joins:
            pairs.setdefault((table, other), []).append(column)
    out = []
    for (a, b), cols in pairs.items():
        (x1, y1), (x2, y2) = _place(a), _place(b)
        out.append({"from": a, "to": b, "self": a == b, "label": "、".join(cols),
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2, "mx": (x1 + x2) / 2, "my": (y1 + y2) / 2})
    return out


def regions(shown: set[str]) -> list[dict]:
    """The frame around each group's tables that are shown, with its caption: grouping by enclosure, not by hue (the
    hues of these pages mean a document's kind)."""
    out = []
    for key, caption in GROUPS:
        boxes = [(x, y) for table, _, group, x, y, _ in TABLES if group == key and table in shown]
        if not boxes:
            continue
        left = min(x for x, _ in boxes) - BOX_W / 2 - 14
        top = min(y for _, y in boxes) - BOX_H / 2 - 30
        right = max(x for x, _ in boxes) + BOX_W / 2 + 14
        bottom = max(y for _, y in boxes) + BOX_H / 2 + 14
        out.append({"key": key, "caption": caption, "x": left, "y": top, "w": right - left, "h": bottom - top})
    return out


# what counts as "no date" beside a site: the kinds that carry one (the same three as metadata.yaml's undated queries)
UNDATED = "ident_date is null and kind_class in ('notice', 'page', 'attachment')"


def site_keys(names: list[str]) -> str:
    """What a site is found by, as one lower-case string of alternatives joined by '|' (static/sitefind.js matches
    what is typed against each): its name, its code, its unit and that unit's other names, each in both scripts."""
    seen: list[str] = []
    for name in names:
        name = (name or "").strip().lower()
        if name and name not in seen:
            seen.append(name)
    return "|".join(seen)


async def _sites(db, have: set[str]) -> list[dict]:
    """Every site, the one with most documents first: its documents, how many of them have no date, and its keys.
    t2s() is plugins/script.py's; without it (that plugin not loaded) the keys are in the school's script only."""
    if "sites" not in have:
        return []
    undated = {}
    if "docs" in have:
        undated = dict((await db.execute(f"select site, count(*) from docs where {UNDATED} group by site")).rows)
    try:
        rows = (await db.execute("select site, name, unit, docs, t2s(name), t2s(unit) from sites order by docs desc, site")).rows
        aliases = (await db.execute("select unit_id, alias, t2s(alias) from unit_aliases")).rows if "unit_aliases" in have else []
    except Exception:  # noqa: BLE001 - no t2s(): the same rows without the second script
        rows = (await db.execute("select site, name, unit, docs, name, unit from sites order by docs desc, site")).rows
        aliases = (await db.execute("select unit_id, alias, alias from unit_aliases")).rows if "unit_aliases" in have else []
    called: dict[str, list[str]] = {}
    for unit, alias, simple in aliases:
        called.setdefault(unit, []).extend([alias, simple])
    out = []
    for site, name, unit, docs, name_s, unit_s in rows:
        docs, none = docs or 0, undated.get(site, 0)
        out.append({"site": site, "name": name or site, "unit": unit or "", "docs": docs, "undated": none,
                    "share": round(100 * none / docs) if docs else 0,
                    "found_by": site_keys([name, name_s, site, unit, unit_s, *called.get(unit, [])])})
    return out


async def _facts(datasette) -> dict | None:
    """Row counts, the tables that exist and the sites, kept per library file (its hash, or its own size when
    Datasette gives no hash): a rebuilt library is read again. None when the official library is not open."""
    try:
        db = datasette.get_database(DATABASE)
    except KeyError:
        return None
    key = (db.hash, getattr(db, "size", None))
    if _CACHE.get("key") != key:
        have = set(await db.table_names())
        counts = {}
        for table, *_ in TABLES:
            if table in have:
                counts[table] = (await db.execute(f"select count(*) from [{table}]")).rows[0][0]
        _CACHE.update(key=key, counts=counts, have=have, sites=await _sites(db, have))
    return _CACHE


async def guide(datasette) -> dict | None:
    """The home page's data, or None when the official library is not open."""
    facts = await _facts(datasette)
    if facts is None:
        return None
    counts, have = facts["counts"], facts["have"]
    db = datasette.get_database(DATABASE)
    most = max([math.log10(n + 1) for n in counts.values()] or [1.0]) or 1.0
    tables = []
    for table, name, group, x, y, joins in TABLES:
        if table not in have:
            continue
        # fallback=False: the table's own entry only (merged, the database's and the instance's would override it)
        about = datasette.metadata(database=DATABASE, table=table, fallback=False) or {}
        described = about.get("columns") or {}
        count = counts.get(table, 0)
        tables.append({
            "name": table, "zh": name, "group": group, "x": x, "y": y, "count": count,
            "share": math.log10(count + 1) / most,  # how long its bar is: by the logarithm (111 beside 624,675)
            "description": about.get("description") or "", "hidden": bool(about.get("hidden")),
            "joins": [{"column": c, "table": t, "to": o, "zh": NAMES.get(t, t)} for c, t, o in joins],
            "joined_by": [{"table": other, "zh": NAMES.get(other, other), "column": c, "to": o}
                          for other, _, _, _, _, theirs in TABLES if other in have
                          for c, t, o in theirs if t == table and other != table],
            # the tables it joins either way, each once, in the map's order: the chips beside it
            "near": [{"table": other, "zh": NAMES[other]} for other, *_ in TABLES if other in have and other != table
                     and (any(t == other for _, t, _ in joins)
                          or any(t == table for _, t, _ in next(j for n, _, _, _, _, j in TABLES if n == other)))],
            "columns": [{"name": c, "description": described.get(c, ""), "short": short_name(described.get(c, "")),
                         "rest": rest_of(described.get(c, ""))} for c in await db.table_columns(table)],
        })
    shown = {t["name"] for t in tables}
    lines = [e for e in edges() if e["from"] in shown and e["to"] in shown]
    return {"database": DATABASE, "tables": tables, "edges": lines, "regions": regions(shown), "w": BOX_W, "h": BOX_H,
            "map_w": MAP_W, "map_h": MAP_H, "sites": facts["sites"]}


def column_names(datasette, database: str | None, table: str | None) -> dict[str, dict]:
    """column -> {'short': its short Chinese name or '', 'meaning': metadata.yaml's sentence, 'rest': that sentence
    without the name it starts with} for one table. The page shows the short name; the rest floats over it."""
    if not database or not table:
        return {}
    about = datasette.metadata(database=database, table=table, fallback=False) or {}
    return {c: {"short": short_name(meaning), "meaning": meaning, "rest": rest_of(meaning)}
            for c, meaning in (about.get("columns") or {}).items()}


@hookimpl
def extra_template_vars(template, database, table, datasette):
    async def inner():
        titles = {name: (about or {}).get("title") or "" for name, about in (datasette.metadata("databases") or {}).items()}
        out = {"nk_bar": BAR, "nk_theme": THEME, "zh_tables": NAMES, "op_words": OPS, "value_words": WORDS, "param_names": PARAMS,
               "trace_url": TRACE, "site_page": SITE_PAGE, "column_names": column_names(datasette, database, table), "kind_classes": KIND_CLASS,
               "db_titles": titles}
        if template == "index.html":
            out["guide"] = await guide(datasette)
        if template == "database.html" and database:  # a table's card floats what the table is
            out["table_about"] = {t: (datasette.metadata(database=database, table=t, fallback=False) or {})
                                  .get("description") or "" for t in NAMES}
        if template in ("query.html", "database.html", "table.html"):
            facts = await _facts(datasette)
            out["sites"] = facts["sites"] if facts else []
            out["site_names"] = {s["site"]: s["name"] for s in out["sites"]}  # a facet of sites shows names
        return out

    return inner
