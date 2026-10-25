"""Links out of an archived HTML page: <a>/<area> hrefs, frames and iframes, meta refresh, and URLs written into
onclick handlers (window.open / location.href). ASP.NET postbacks (javascript:__doPostBack) are counted, never sent:
the platform profiles replay the allowed ones later (design §3.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

_ONCLICK = re.compile(r"""(?:window\.open|location\.href\s*=|location\s*=|location\.assign)\s*\(?\s*['"]([^'"]+)['"]""")
_REFRESH = re.compile(r"(?i)url\s*=\s*['\"]?([^'\";]+)")
_POSTBACK = re.compile(r"__doPostBack\(\s*'([^']*)'\s*,\s*'([^']*)'")


@dataclass
class PageLinks:
    base: str
    links: list[tuple[str, str]] = field(default_factory=list)  # (href as written, link text)
    postbacks: list[str] = field(default_factory=list)  # "control|argument"
    login_form: bool = False
    title: str = ""


def _base(url: str, href: str | None) -> str:
    """The page's <base href>, or the page itself when the base is written wrong (an unusable base is not a crash)."""
    if not href:
        return url
    try:
        joined = urljoin(url, href)
        urlsplit(joined).port  # noqa: B018 - raises for a port that is not a number
        return joined
    except ValueError:
        return url


def extract(html: str, url: str) -> PageLinks:
    tree = LexborHTMLParser(html)
    base_node = tree.css_first("base[href]")
    out = PageLinks(base=_base(url, base_node.attributes.get("href") if base_node else None))
    title = tree.css_first("title")
    out.title = title.text(strip=True)[:200] if title else ""
    out.login_form = tree.css_first("input[type=password]") is not None
    for node in tree.css("a[href], area[href]"):
        href = (node.attributes.get("href") or "").strip()
        text = node.text(strip=True)[:100] if node.tag == "a" else (node.attributes.get("alt") or "")[:100]
        if href.lower().startswith("javascript:"):
            out.postbacks += [f"{c}|{a}" for c, a in _POSTBACK.findall(href)]
            continue
        out.links.append((href, text))
    for node in tree.css("iframe[src], frame[src]"):
        out.links.append(((node.attributes.get("src") or "").strip(), "[frame]"))
    for node in tree.css("meta[http-equiv]"):
        if (node.attributes.get("http-equiv") or "").lower() == "refresh":
            m = _REFRESH.search(node.attributes.get("content") or "")
            if m:
                out.links.append((m.group(1).strip(), "[refresh]"))
    for node in tree.css("[onclick]"):
        handler = node.attributes.get("onclick") or ""
        out.links += [(u.strip(), "[onclick]") for u in _ONCLICK.findall(handler)]
        out.postbacks += [f"{c}|{a}" for c, a in _POSTBACK.findall(handler)]
    return out
