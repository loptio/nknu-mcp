"""Generic ASP.NET WebForms form-state replay (no JS engine needed).

form_state(html)            -> (action_url, ordered list of (name, value)) like a browser would submit
postback(html, target, arg) -> same, with __EVENTTARGET/__EVENTARGUMENT set (= __doPostBack(target,arg))
find_postbacks(html)        -> [(target, argument, link_text)]: links, and onclick/onchange handlers (WebForm_PostBack…)
form_for_control(html, t)   -> the CSS selector of the form a __doPostBack(t, …) submits (round-1 design §7.4)

What a browser sends is the HTML Standard's "constructing the entry list" (§4.10.21.4): a select's options whose
selectedness is true and that are not disabled. A select-one shown as a drop-down (no multiple, display size 1) with
no option marked selected has its first enabled option selected (the "selectedness setting algorithm"); a select
multiple (or a list box, size > 1) with none marked sends nothing (review 10-26 C: this sent the first option). Of
several options marked selected in a select-one, the last wins. An option's value is its value attribute, else its
text with white space stripped and collapsed.
"""
import html as htmllib
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

PB_RE = re.compile(r"__doPostBack\(\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)")
# WebForm_DoPostBackWithOptions(new WebForm_PostBackOptions("Btn_Send", "", true, …)): a submit button's own handler
PB_OPTIONS_RE = re.compile(r"WebForm_PostBackOptions\(\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"]")
# the server form __doPostBack submits: ASP.NET writes `var theForm = document.forms['form1'];`
_THE_FORM = re.compile(r"""theForm\s*=\s*document\.forms\[\s*['"]([^'"]+)['"]\s*\]""")
_HANDLERS = ("href", "onclick", "onchange")
_SIMPLE_ID = re.compile(r"[A-Za-z_][\w:.-]*")


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
            if typ in ("checkbox", "radio"):
                if "checked" not in a:
                    continue
                fields.append((name, (a["value"] or "") if "value" in a else "on"))  # no value attribute: "on"
                continue
            fields.append((name, a.get("value") or ""))
        elif el.tag == "select":
            fields += [(name, _option_value(o)) for o in _selected(el)]
        else:  # textarea
            fields.append((name, el.text() or ""))
    return action, fields


def _disabled(option: LexborNode) -> bool:
    """An option is disabled by its own attribute or by the optgroup it sits in."""
    if "disabled" in option.attributes:
        return True
    parent = option.parent
    return parent is not None and parent.tag == "optgroup" and "disabled" in parent.attributes


def _display_size(select: LexborNode) -> int:
    a = select.attributes
    try:
        size = int((a.get("size") or "").strip())
    except ValueError:
        size = 0
    if size > 0:
        return size
    return 4 if "multiple" in a else 1


def _selected(select: LexborNode) -> list[LexborNode]:
    """The options a browser submits for this select (HTML Standard, see the module's docstring)."""
    options = list(select.css("option"))
    marked = [o for o in options if "selected" in o.attributes]
    if "multiple" in select.attributes:
        chosen = marked
    elif marked:
        chosen = marked[-1:]  # several marked in a select-one: the last one wins
    elif _display_size(select) == 1:
        chosen = next(([o] for o in options if not _disabled(o)), [])
    else:
        chosen = []  # a list box with nothing selected sends nothing
    return [o for o in chosen if not _disabled(o)]


def _option_value(option: LexborNode) -> str:
    if "value" in option.attributes:
        return option.attributes["value"] or ""
    return " ".join((option.text() or "").split())


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


def postback_calls(code: str) -> list[tuple[str, str]]:
    """The (target, argument) pairs a piece of script posts back with: __doPostBack(…) and WebForm_PostBackOptions(…),
    an attribute's entities and a quoted string's escaped quotes undone."""
    code = htmllib.unescape(code).replace("\\'", "'")
    return PB_RE.findall(code) + PB_OPTIONS_RE.findall(code)


def _text(node: LexborNode) -> str:
    a = node.attributes
    return ((node.text() or "").strip() if node.tag != "select" else "") or (a.get("value") or a.get("title") or "")


def find_postbacks(html: str):
    """Every (target, argument, text) the page posts back with, in document order, each once: a link's
    javascript:__doPostBack, and an element's onclick or onchange handler (a submit button's WebForm_PostBackOptions,
    an AutoPostBack select's setTimeout('__doPostBack(…)'))."""
    tree = LexborHTMLParser(html)
    res, seen = [], set()
    for node in tree.css("[href], [onclick], [onchange]"):
        for attr in _HANDLERS:
            code = node.attributes.get(attr)
            if not code or (attr == "href" and node.tag not in ("a", "area")):
                continue
            for target, arg in postback_calls(code):
                if (target, arg) not in seen:
                    seen.add((target, arg))
                    res.append((target, arg, _text(node)))
    return res


def _form_selector(tree: LexborHTMLParser, form: LexborNode) -> str | None:
    """A CSS selector that picks exactly this form: by its id, else its name; None when neither is unique."""
    for attr in ("id", "name"):
        value = form.attributes.get(attr)
        if value and _SIMPLE_ID.fullmatch(value):
            css = f'form[{attr}="{value}"]'
            hits = tree.css(css)
            if len(hits) == 1 and hits[0].mem_id == form.mem_id:
                return css
    return None


def _has_state(form: LexborNode) -> bool:
    return form.css_first("input[name='__VIEWSTATE']") is not None


def form_for_control(html: str, target: str) -> str | None:
    """The form __doPostBack(target, …) submits, as a CSS selector for form_state/postback (round-1 design §7.4):
    the form that holds the element calling it, when that form carries the page's state (__VIEWSTATE) or the page
    names no server form; else the form the page's own script names (`theForm = document.forms['form1']`, which is
    the one ASP.NET's __doPostBack submits wherever the link sits, a language button outside the form too); else the
    one form with a __VIEWSTATE. spe's first form is the site search (no state): never chosen for its pager. None when
    no form fits or none can be named uniquely: then nothing is sent."""
    tree = LexborHTMLParser(html)
    holder = None
    for node in tree.css("[href], [onclick], [onchange]"):
        if any(t == target for code in (node.attributes.get(a) for a in _HANDLERS) if code
               for t, _arg in postback_calls(code)):
            holder = node
            break
    if holder is None:
        return None
    form = holder.parent
    while form is not None and form.tag != "form":
        form = form.parent
    named = None
    for script in tree.css("script"):
        m = _THE_FORM.search(script.text() or "")
        if m:
            named = next((f for f in tree.css("form")
                          if m.group(1) in (f.attributes.get("id"), f.attributes.get("name"))), None)
            break
    if form is not None and (_has_state(form) or named is None):
        return _form_selector(tree, form)
    if named is not None:
        return _form_selector(tree, named)
    stated = [f for f in tree.css("form") if _has_state(f)]
    return _form_selector(tree, stated[0]) if len(stated) == 1 else None
