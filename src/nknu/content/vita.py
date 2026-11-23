"""Teachers' CVs (sso.nknu.edu.tw/dataopen2search/vita.aspx?unid=N, with a tab answer per #按钮=<tab>): the department
a teacher's pages stand under. The site table puts the whole CV system under the school (config/site_units.yaml:
sso.nknu.edu.tw/dataopen2search); the pages themselves say whose teacher it is, on the 校內職務 tab, the school's own
record of the posts a teacher holds: 單位 | 職稱 | 分機 | 日期, 「科技學院 - 工業設計學系 | 教授 | … | 2015/03/01 ~ 至今」.
Decided 11-15: a teacher's CV goes under the department its page names once round two has fetched every CV (it had,
11-17: 563 CV pages, 412 with their tabs); written 11-18 for the next build.

The rule: of the 校內職務 rows whose date runs 「至今」 and whose 職稱 is a teaching post (TEACHING), the first one's
unit, the part after the last 「 - 」 (the college before it is the tree's own parent). No such row (a CV without the tab,
or none current): no department, the pages keep the site table's unit. A teacher with current teaching posts in two
units (a joint appointment) takes the first the school lists, and the build counts them. The build hands the name to
the same placement as a notice's stated publisher (build._stated_units: units.name_unit), so a name the tree lacks
stands as written."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from selectolax.lexbor import LexborHTMLParser

VITA_PATH = "/dataopen2search/vita.aspx"
POSTS_TAB = "校內職務"
TEACHING = re.compile(r"教授|講師|研究員|教師")
CURRENT = "至今"
_DASH = re.compile(r"\s+-\s+")


def teacher(url: str) -> str:
    """The teacher a CV address is about: its unid, '' for an address that is no CV page or tab answer."""
    try:
        parts = urlsplit(url.partition("#")[0])
    except ValueError:
        return ""
    if parts.path.lower() != VITA_PATH:
        return ""
    unid = {k.lower(): v for k, v in parse_qs(parts.query).items()}.get("unid") or [""]
    return unid[0].strip()


def is_posts_tab(url: str) -> bool:
    """The CV's 校內職務 tab answer (the export's #按钮=校內職務 row)."""
    frag = url.partition("#")[2]
    return bool(teacher(url)) and frag.endswith("=" + POSTS_TAB)


def department(html: str) -> tuple[str, int]:
    """(the unit the 校內職務 tab's first current teaching post names, how many distinct units current teaching posts
    name); ('', 0) for none."""
    tree = LexborHTMLParser(html)
    found: list[str] = []
    for table in tree.css("table"):
        head = [" ".join((th.text() or "").split()) for th in table.css("thead th")]
        if not {"單位", "職稱", "日期"} <= set(head):
            continue
        at = {name: head.index(name) for name in ("單位", "職稱", "日期")}
        for tr in table.css("tbody tr"):
            cells = [" ".join((td.text() or "").split()) for td in tr.css("td")]
            if len(cells) <= max(at.values()):
                continue
            unit, title, when = cells[at["單位"]], cells[at["職稱"]], cells[at["日期"]]
            if unit and CURRENT in when and TEACHING.search(title):
                found.append(_DASH.split(unit)[-1].strip())
    distinct = list(dict.fromkeys(found))
    return (distinct[0], len(distinct)) if distinct else ("", 0)


def departments(records: Iterable, read=None) -> dict[str, tuple[str, int]]:
    """{unid: department(...)} of every record that is a CV's 校內職務 tab answer and names one; read(record) gives
    its page's text (default: the export's file, Record.export / Record.file; None when it cannot be read)."""
    read = read or _read
    out: dict[str, tuple[str, int]] = {}
    for r in records:
        if not is_posts_tab(r.url) or (who := teacher(r.url)) in out:
            continue
        html = read(r)
        if html and (found := department(html))[0]:
            out[who] = found
    return out


def _read(r) -> str | None:
    if not r.export or not r.file:
        return None
    path = Path(r.export) / r.file
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
