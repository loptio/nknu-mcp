"""Generic ASP.NET WebForms form-state replay (no JS engine needed).

form_state(html)            -> (action_url, ordered list of (name, value)) like a browser would submit
postback(html, target, arg) -> same, with __EVENTTARGET/__EVENTARGUMENT set (= __doPostBack(target,arg))
find_postbacks(html)        -> [(target, argument, link_text)]
"""
import html as htmllib
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

PB_RE = re.compile(r"__doPostBack\(\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)")


def form_state(html: str, base_url: str, form_css: str = "form"):
    tree = LexborHTMLParser(html)
    form = tree.css_first(form_css)
    if form is None:
        raise ValueError("no form")
    action = urljoin(base_url, htmllib.unescape(form.attributes.get("action") or base_url))
    fields = []
    for el in form.css("input, select, textarea"):
        a = el.attributes
        name = a.get("name")
        if not name or "disabled" in a:
            continue
        if el.tag == "input":
            typ = (a.get("type") or "text").lower()
            if typ in ("submit", "button", "image", "reset", "file"):
                continue  # only the clicked submitter is sent; postbacks via links send none
            if typ in ("checkbox", "radio") and "checked" not in a:
                continue
            fields.append((name, a.get("value") or ("on" if typ in ("checkbox", "radio") else "")))
        elif el.tag == "select":
            opts = el.css("option")
            sel = [o for o in opts if "selected" in o.attributes] or opts[:1]
            for o in sel if "multiple" in a else sel[:1]:
                v = o.attributes.get("value")
                fields.append((name, v if v is not None else (o.text() or "").strip()))
        else:  # textarea
            fields.append((name, el.text() or ""))
    return action, fields


def postback(html: str, base_url: str, target: str, argument: str = "", overrides=None, form_css="form"):
    action, fields = form_state(html, base_url, form_css)
    d = {"__EVENTTARGET": target, "__EVENTARGUMENT": argument, **(overrides or {})}
    out, seen = [], set()
    for k, v in fields:
        if k in d:
            if k not in seen:
                out.append((k, d[k])); seen.add(k)
        else:
            out.append((k, v))
    for k, v in d.items():
        if k not in seen:
            out.append((k, v))
    return action, out


def find_postbacks(html: str):
    tree = LexborHTMLParser(html)
    res = []
    for a in tree.css("a[href]"):
        m = PB_RE.search(htmllib.unescape(a.attributes.get("href") or ""))
        if m:
            res.append((m.group(1), m.group(2), (a.text() or "").strip()))
    return res
