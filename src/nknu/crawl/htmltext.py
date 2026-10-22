"""Rule-based text extraction (ported from prototypes/acquisition/rulex.py).

- inline elements are concatenated WITHOUT separators, so Word-style fragments <span>19：</span><span>00~</span> rejoin;
- only block elements and <br> break lines;
- data tables are expanded into a grid (rowspan/colspan copied into every covered cell);
- document links are kept as absolute, URL-decoded hrefs;
- output is NFKC-normalised (full-width ：～（） -> ASCII) and "16: 50" typos become "16:50".
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urljoin

from selectolax.lexbor import LexborNode

BLOCK = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "tbody", "thead", "section",
         "article", "header", "footer", "dl", "dt", "dd", "blockquote", "pre", "hr", "form", "center", "main", "nav", "aside",
         "caption"}
DROP = {"script", "style", "noscript", "iframe", "svg", "template", "input", "select", "button", "textarea", "head", "link",
        "meta"}
DOC_RE = re.compile(r"\.(pdf|docx?|odt|xlsx?|ods|pptx?|zip|rar|7z)(\?|$)|AttachedFile\.aspx", re.IGNORECASE)
TIME_RE = re.compile(r"(?<!\d)([012]?\d)\s*:\s*([0-5]\d)(?!\d)")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return TIME_RE.sub(lambda m: f"{int(m.group(1)):02d}:{m.group(2)}", s)


def _inline(node: LexborNode, parts: list[str], base: str) -> None:
    for ch in node.iter(include_text=True):
        tag = ch.tag
        if tag == "-text":
            parts.append(ch.text_content or "")
            continue
        if tag in DROP or tag == "-comment":
            continue
        if tag == "br":
            parts.append("\n")
            continue
        if tag == "img":
            alt = (ch.attributes.get("alt") or "").strip()
            if alt:
                parts.append(f"[圖:{alt}]")
            continue
        if tag == "table":
            parts.append("\n")
            for row in table_grid(ch):
                dedup = [x for i, x in enumerate(row) if i == 0 or x != row[i - 1]]
                if any(dedup):
                    parts.append("\n| " + " | ".join(dedup) + " |")
            parts.append("\n")
            continue
        is_block = tag in BLOCK
        if is_block:
            parts.append("\n")
        if tag == "a":
            href = ch.attributes.get("href") or ""
            _inline(ch, parts, base)
            if href and DOC_RE.search(href):
                parts.append(f" <{unquote(urljoin(base, href))}>")
        else:
            _inline(ch, parts, base)
        if is_block:
            parts.append("\n")


def element_text(node: LexborNode, base_url: str) -> str:
    parts: list[str] = []
    _inline(node, parts, base_url)
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in _norm("".join(parts)).split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _cell_text(td: LexborNode) -> str:
    parts: list[str] = []
    _inline(td, parts, "")
    return re.sub(r"\s+", " ", _norm("".join(parts))).strip()


def _rows(tbl: LexborNode) -> list[LexborNode]:
    out = []
    for tr in tbl.css("tr"):
        p = tr.parent
        while p is not None and p.tag != "table":
            p = p.parent
        if p is not None and p.mem_id == tbl.mem_id:
            out.append(tr)
    return out


def _span(td: LexborNode, attr: str) -> int:
    return int(re.sub(r"\D", "", td.attributes.get(attr) or "1") or 1)


def table_grid(tbl: LexborNode) -> list[list[str]]:
    """Expanded grid of cell texts; every covered cell repeats the spanning cell's text (no de-duplication)."""
    grid: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(_rows(tbl)):
        c = 0
        for td in (x for x in tr.iter() if x.tag in ("td", "th")):
            while (r, c) in grid:
                c += 1
            txt, rs, cs = _cell_text(td), _span(td, "rowspan"), _span(td, "colspan")
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = txt
            c += cs
    if not grid:
        return []
    nr, nc = max(k[0] for k in grid) + 1, max(k[1] for k in grid) + 1
    return [[grid.get((r, c), "") for c in range(nc)] for r in range(nr)]


def leaf_tables(node: LexborNode) -> list[LexborNode]:
    """Tables that contain no nested table (layout tables are skipped)."""
    return [t for t in node.css("table") if not any(n.mem_id != t.mem_id for n in t.css("table"))]
