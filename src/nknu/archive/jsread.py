"""Reading a page's inline scripts as text (round-1 design §7.8, §8.3): what a click or a change makes the page do, and
where a script sends the browser. No script is ever run: a bracket matcher that skips strings, comments and regular
expression literals, and a few regular expressions.

Ported from the 10-25 click scan of the old archive (research/project_audit_2026-10-25/js_test_2026-10-25/
click_scan.py, definitions a–d), which links.extract() and R7 (design §18.2b) now share: what a page defines
(Script.defs: function f, f = function, f: function, arrow functions, class and shorthand methods; the callback keys of
an $.ajax settings object are not page functions), the network calls a piece of code makes, directly or through the
page's own functions three levels down (net_calls: fetch and window.fetch, $.ajax / $.post / $.get / $.getJSON,
XMLHttpRequest, .load, axios, a URL set as an element's src or data; an element named as a picture getting a new src is
an image swap, not content), navigation to an address the code builds (nav_computed), the click and change handlers an
inline script binds (bindings: jQuery's .click/.on/.hover/.delegate, .on({click: …}), addEventListener, onclick =,
Bootstrap's tab and collapse events, document or window delegation, .each/.forEach loops and variables holding a
selection), the calls a handler hands over to functions the page does not define (undefined_calls), and whether an
element is the tab shown on load or holds a plain link elsewhere (shown_on_load, plain_href).

Every rule here is Claude's way of doing it (【Claude 的执行办法】), as the scan's were.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from nknu.archive import scope

_ID = r"[A-Za-z_$][\w$]*"
_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
KEYWORDS = frozenset({"if", "for", "while", "switch", "return", "function", "typeof", "void", "new", "catch", "with",
                      "do", "else", "in", "of", "delete", "instanceof", "await", "async", "yield", "case", "throw"})
# built-in or page-chrome calls that load nothing (design §7.8: a browser's own calls are never a page's function)
NOT_CONTENT = frozenset({
    "alert", "confirm", "prompt", "print", "window.print", "history.back", "history.go", "window.history.back",
    "window.history.go", "window.close", "self.close", "close", "scrollTo", "window.scrollTo", "scrollBy",
    "window.scroll", "focus", "blur", "setTimeout", "setInterval", "clearTimeout", "parseInt", "parseFloat",
    "encodeURIComponent", "decodeURIComponent", "encodeURI", "escape", "unescape", "String", "Number", "Boolean",
    "isNaN", "eval", "event.preventDefault", "event.stopPropagation", "preventDefault", "stopPropagation",
})
# calls written in an attribute that act on the page itself (the DOM, jQuery, the event): the network check reads them
DOM_PREFIXES = ("$", "jQuery", "this.", "document.", "event.", "e.", "window.location", "location.", "Math.", "JSON.",
                "console.", "parent.", "top.", "opener.", "self.")
NAV_CALLS = frozenset({"window.open", "open", "location.assign", "location.replace"})
POSTBACK_CALLS = ("__doPostBack", "WebForm_")


# ---------------------------------------------------------------- reading script text


def match_close(js: str, i: int) -> int:
    """js[i] is ( [ or {: the index of its partner (len(js) when unbalanced). Strings, template literals, comments
    (//, /* */, <!-- … end of line) and regular-expression literals are skipped."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = [pairs[js[i]]]
    j, n = i + 1, len(js)
    prev = js[i]
    while j < n:
        c = js[j]
        if c in "'\"`":
            j = skip_string(js, j)
            prev = "a"
            continue
        if c == "/" and j + 1 < n and js[j + 1] == "/":
            k = js.find("\n", j)
            j = n if k < 0 else k
            continue
        if c == "/" and j + 1 < n and js[j + 1] == "*":
            k = js.find("*/", j + 2)
            j = n if k < 0 else k + 2
            continue
        if c == "<" and js.startswith("<!--", j):
            k = js.find("\n", j)
            j = n if k < 0 else k
            continue
        if c == "/" and prev in _REGEX_PREV:
            k = j + 1
            while k < n and js[k] not in "/\n":
                k += 2 if js[k] == "\\" else 1
            j = k + 1
            prev = "a"
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}" and stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return j
        if not c.isspace():
            prev = c
        j += 1
    return n


def skip_string(js: str, j: int) -> int:
    """js[j] opens a string: the index just after it closes (a line break ends a quoted one, as JavaScript does)."""
    q, k, n = js[j], j + 1, len(js)
    while k < n:
        c = js[k]
        if c == "\\":
            k += 2
            continue
        if c == q:
            return k + 1
        if c == "\n" and q != "`":
            return k + 1
        k += 1
    return n


def split_args(args: str) -> list[str]:
    """Top-level comma split of an argument list's inside."""
    out, depth, cur, j = [], 0, [], 0
    while j < len(args):
        c = args[j]
        if c in "'\"`":
            k = skip_string(args, j)
            cur.append(args[j:k])
            j = k
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        j += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


_JS_ESCAPE = re.compile(r"\\(?:x([0-9A-Fa-f]{2})|u([0-9A-Fa-f]{4})|(.))", re.DOTALL)


def unescape(s: str) -> str:
    """A JavaScript string literal's escapes undone: \\/ is /, \\x41 and \\u0041 are A, \\' is '."""
    def one(m: re.Match) -> str:
        if m.group(1) or m.group(2):
            return chr(int(m.group(1) or m.group(2), 16))
        return {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(3), m.group(3))

    return _JS_ESCAPE.sub(one, s)


def literal(expr: str) -> str | None:
    """The text of a string literal written as the whole expression ('a', "a", `a` without ${}), else None."""
    e = expr.strip()
    if len(e) >= 2 and e[0] in "'\"`" and e[-1] == e[0] and skip_string(e, 0) == len(e):
        if e[0] == "`" and "${" in e:
            return None
        return e[1:-1]
    return None


def summarize(expr: str, local: str = "") -> str:
    """An expression built from string literals and variables, written as the URL it makes:
    'load_content.php?sub_id=' + id -> load_content.php?sub_id={id}. A plain variable is looked up in the code."""
    expr = expr.strip()
    lit = literal(expr)
    if lit is not None:
        return lit
    if re.fullmatch(_ID, expr) and local:
        m = re.search(rf"(?:var|let|const)?\s*\b{re.escape(expr)}\s*=\s*(['\"`][^'\"`\n]*['\"`](?:\s*\+\s*[^;\n]+)?)",
                      local)
        if m:
            return summarize(m.group(1))
    parts, out = [], []
    depth, cur, j = 0, [], 0
    while j < len(expr):
        c = expr[j]
        if c in "'\"`":
            k = skip_string(expr, j)
            cur.append(expr[j:k])
            j = k
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "+" and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        j += 1
    parts.append("".join(cur).strip())
    for p in parts:
        lit = literal(p)
        if lit is not None:
            out.append(lit.replace("${", "{"))
        elif p:
            out.append("{" + re.sub(r"\s+", "", p)[:30] + "}")
    return "".join(out)[:160]


class Script:
    """The page's inline scripts as one text, with the functions they define: name -> (parameters, body, start)."""

    DEF_PATTERNS = (
        re.compile(rf"\bfunction\s+({_ID})\s*\("),
        re.compile(rf"(?<![\w$])((?:{_ID}\.)*{_ID})\s*=\s*(?:async\s+)?function\b\s*(?:{_ID})?\s*\("),
        re.compile(rf"(?<![\w$.])({_ID})\s*:\s*(?:async\s+)?function\b\s*(?:{_ID})?\s*\("),
        re.compile(rf"(?<![\w$])((?:{_ID}\.)*{_ID})\s*=\s*(?:async\s*)?(?:\([^()]*\)|{_ID})\s*=>"),
        re.compile(rf"(?<![\w$.])({_ID})\s*\([^()]*\)\s*(?=\{{)"),  # a class method or a shorthand method
    )
    # the callback keys of an $.ajax settings object (success: function …) are not functions the page defines by name
    NOT_DEF_KEYS = frozenset({"success", "error", "complete", "beforeSend", "done", "fail", "then", "catch", "always",
                              "statusCode", "xhr", "dataFilter"})

    def __init__(self, code: str) -> None:
        self.code = code
        self.cache: dict = {}
        self.defs: dict[str, tuple[list[str], str, int]] = {}
        for n_rx, rx in enumerate(self.DEF_PATTERNS):
            for m in rx.finditer(code):
                name = m.group(1)
                if name in self.NOT_DEF_KEYS and n_rx in (2, 4):
                    continue
                if n_rx == 4 and (name in KEYWORDS or name in ("function", "catch", "switch")):
                    continue
                try:
                    if n_rx == 4:
                        got = self._body_after(code.index("(", m.start()), m)
                    else:
                        got = self._body_after(m.end() - 1 if code[m.end() - 1] == "(" else m.start(), m)
                except (ValueError, IndexError):
                    continue
                if got is None:
                    continue
                params, body = got
                for key in {name, name.split(".")[-1]}:
                    self.defs.setdefault(key, (params, body, m.start()))

    def _body_after(self, i: int, m: re.Match) -> tuple[list[str], str] | None:
        code = self.code
        if "=>" in m.group(0):
            pm = re.search(r"\(([^()]*)\)\s*=>|(" + _ID + r")\s*=>", m.group(0))
            params = [p.strip() for p in (pm.group(1) or pm.group(2) or "").split(",") if p.strip()] if pm else []
            k = m.end()
            while k < len(code) and code[k].isspace():
                k += 1
            if k < len(code) and code[k] == "{":
                return params, code[k + 1:match_close(code, k)]
            e = code.find(";", k)
            return params, code[k:len(code) if e < 0 else e]
        if code[i] != "(":
            return None
        e = match_close(code, i)
        params = [p.strip() for p in code[i + 1:e].split(",") if p.strip()]
        k = e + 1
        while k < len(code) and code[k].isspace():
            k += 1
        if k >= len(code) or code[k] != "{":
            return None
        return params, code[k + 1:match_close(code, k)]

    def defined(self, name: str) -> str | None:
        """The key a call's name is defined under on this page: the whole name, window.f as f, obj.method by its last
        part (design §7.8: the page's own definition first, else it is external)."""
        if name in self.defs:
            return name
        short = name.split(".")[-1]
        if name.startswith("window.") and short in self.defs:
            return short
        if "." in name and short in self.defs:
            return short
        return None


# ---------------------------------------------------------------- network calls and navigation


@dataclass(frozen=True)
class NetCall:
    """One request a handler makes: the call (fetch, $.ajax …, set_src), the URL it builds (summarize()), the method
    and the fields it sends, and the page's functions it went through."""

    call: str
    target: str
    method: str
    data: tuple[str, ...] = ()
    via: tuple[str, ...] = ()


NET = re.compile(r"""(?P<fetch>(?<![\w$.])(?:window\s*\.\s*)?fetch\s*\()"""
                 r"""|(?P<jq>(?:\$|jQuery)\s*\.\s*(?P<jqm>ajax|post|get|getJSON|getScript)\s*\()"""
                 r"""|(?P<ajax>\.\s*ajax\s*\()"""
                 r"""|(?P<xhr>\bXMLHttpRequest\b|\bActiveXObject\s*\(\s*['"](?:Microsoft|Msxml2)\.XMLHTTP)"""
                 r"""|(?P<load>\.\s*load\s*\(\s*(?!function\b|\))(?=['"`]|[A-Za-z_$]))"""
                 r"""|(?P<axios>\baxios\b\s*(?:\.\s*(?P<axm>get|post|request)\s*)?\()"""
                 r"""|(?P<src>\.\s*src\s*=(?!=)|\.\s*attr\s*\(\s*['"]src['"]\s*,"""
                 r"""|\.\s*setAttribute\s*\(\s*['"](?:src|data)['"]\s*,)""")
CALL = re.compile(rf"(?<![\w$.])({_ID}(?:\s*\.\s*{_ID})*)\s*\(")
# a request a script makes that is a captcha's (scope.CAPTCHA: the same words) or a sign-in's: never content
CAPTCHA = re.compile(rf"{scope.CAPTCHA.pattern}|(?i:login|logon|passw)")
IMAGE_URL = re.compile(r"(?i)\.(?:jpe?g|png|gif|bmp|webp|svg)(?:\b|$)|^\{[^}]*(?:img|image|photo|pic|thumb|src)[^}]*\}$")
_IMAGE_WORDS = frozenset({"img", "image", "images", "photo", "photos", "pic", "pics", "picture", "thumb", "thumbnail",
                          "avatar", "banner", "logo", "icon"})
NEW_BEFORE = re.compile(r"\bnew\s+$")


def image_lhs(s: str) -> bool:
    """Whether the element given a new src is named as a picture, by whole words (camelCase split): mainImg, #pic, but
    not topicFrame."""
    return any(w.lower() in _IMAGE_WORDS for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", s))


def _data_fields(inner: str) -> list[str]:
    """The fields a request sends: name=value when the value is written as a literal, name={…} otherwise."""
    out = []
    for part in split_args(inner):
        km = re.match(rf"""\s*['"]?({_ID})['"]?\s*:\s*(.*)$""", part, re.DOTALL)
        if not km:
            continue
        v = km.group(2).strip()
        lit = literal(v)
        if lit is not None:
            out.append(f"{km.group(1)}={lit[:30]}")
        elif re.fullmatch(r"-?\d+(?:\.\d+)?|true|false", v):
            out.append(f"{km.group(1)}={v}")
        else:
            out.append(km.group(1) + "={" + re.sub(r"\s+", "", v)[:40] + "}")
    return out


def stmt_end(code: str) -> int:
    """Where the statement starting at code[0] ends: ; or a line break or an unbalanced ) ] } at depth 0, strings
    skipped (location.href='javascript:history.back();' is one statement)."""
    depth, j = 0, 0
    while j < len(code):
        c = code[j]
        if c in "'\"`":
            j = skip_string(code, j)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif c in ";\n" and depth == 0:
            return j
        j += 1
    return j


def _one_net(code: str, m: re.Match) -> NetCall | None:
    """One network call found by NET, or None for an image swap, a captcha, or the old jQuery load event."""
    kind = next(g for g in ("fetch", "jq", "ajax", "xhr", "load", "axios", "src") if m.group(g))
    call = {"jq": "$." + (m.group("jqm") or ""), "load": ".load", "ajax": ".ajax", "src": "set_src"}.get(kind, kind)
    if kind == "src":
        if m.group(0).endswith(","):
            p = code.rfind("(", m.start(), m.end())
            args = split_args(code[p + 1:match_close(code, p)])
            expr = args[1] if len(args) > 1 else ""
        else:
            rest = code[m.end():m.end() + 400]
            expr = split_args(rest[:stmt_end(rest)] + ",")[0] if rest.strip() else ""
        target = summarize(expr, code)
        if not target or IMAGE_URL.search(target) or CAPTCHA.search(target):
            return None
        lhs = re.search(r"([\w$.\[\]'\"#()-]{1,80})\s*$", code[max(0, m.start() - 80):m.start()])
        if lhs and image_lhs(lhs.group(1)):
            return None  # img.src = …, $('#pic').attr('src', …): a picture swapped, not content loaded
        return NetCall(call, target, "GET")
    if kind == "load" and re.search(r"\$\(\s*(?:window|document)\s*\)\s*$|window\s*$", code[max(0, m.start() - 24):m.start()]):
        return None  # $(window).load(fn): the old jQuery load event
    target, method, data = "", "", []
    if kind == "xhr":
        om = re.search(r"\.open\s*\(", code[m.start():m.start() + 3000])
        if om:
            p = m.start() + om.end() - 1
            args = split_args(code[p + 1:match_close(code, p)])
            method = (literal(args[0]) or "").upper() if args else ""
            target = summarize(args[1], code) if len(args) > 1 else ""
    else:
        p = code.rfind("(", m.start(), m.end())
        args = split_args(code[p + 1:match_close(code, p)])
        first = args[0] if args else ""
        if first.startswith("{"):
            um = re.search(r"\burl\s*:\s*", first)
            if um:
                target = summarize(split_args(first[um.end():].rstrip("}") + ",")[0], code)
            tm = re.search(r"""\b(?:type|method)\s*:\s*['"](\w+)['"]""", first)
            method = tm.group(1).upper() if tm else "GET"
            dm = re.search(r"\bdata\s*:\s*\{", first)
            if dm:
                data = _data_fields(first[dm.end():match_close(first, dm.end() - 1)])
            elif re.search(r"\bdata\s*:\s*[^,}]*(?:serialize|FormData)", first):
                data = ["<form>"]
        else:
            target = summarize(first, code)
            method = {"$.post": "POST", "$.get": "GET", "$.getJSON": "GET", ".load": "GET", "fetch": "GET"}.get(call, "")
            if len(args) > 1 and re.search(r"""method\s*:\s*['"]POST""", args[1], re.IGNORECASE):
                method = "POST"
            if call in ("$.post", "$.get", ".load") and len(args) > 1 and args[1].startswith("{"):
                data = _data_fields(args[1].strip()[1:-1])
    if CAPTCHA.search(target or ""):
        return None
    return NetCall(call, target, method, tuple(data))


LEVELS = 3  # how many of the page's own functions down a handler is read (design §7.8)
DEEP_LEVELS = 8  # how far down links.extract looks to tell a handler that loads nothing from one read too shallow


def net_calls(code: str, sc: Script, depth: int = LEVELS, seen: frozenset = frozenset(),
              via: tuple[str, ...] = ()) -> list[NetCall]:
    """The network calls this code makes, directly or through functions the same page defines (3 levels down)."""
    found = []
    for m in NET.finditer(code):
        rec = _one_net(code, m)
        if rec is not None:
            found.append(NetCall(rec.call, rec.target, rec.method, rec.data, via))
    if depth > 0:
        for m in CALL.finditer(code):
            name = re.sub(r"\s+", "", m.group(1))
            if name.split(".")[-1] in KEYWORDS or name in seen:
                continue
            key = name if name in sc.defs else (name[7:] if name.startswith("window.") and name[7:] in sc.defs
                                                else None)
            if key is None or key in seen:
                continue
            ck = ("net", key, depth)
            if ck not in sc.cache:
                sc.cache[ck] = []  # a function calling itself stops here
                sc.cache[ck] = net_calls(sc.defs[key][1], sc, depth - 1, seen | {key}, ())
            found.extend(NetCall(x.call, x.target, x.method, x.data, (*via, key, *x.via)) for x in sc.cache[ck])
    return found


_NAV_AT = re.compile(r"(?<![\w$])(?:(?:window\.|document\.|top\.|parent\.|self\.)?location(?:\.href)?\s*=(?!=)\s*"
                     r"|(?:window\.open|location\.assign|location\.replace)\s*\()")


def nav_computed(code: str, sc: Script, depth: int = 2, seen: frozenset = frozenset()) -> list[str]:
    """Navigation to an address the code builds (location.href = x + …, window.open(base + id)), as summarize() writes
    it; a literal address is a plain link (links.extract reads it), never here."""
    out = []
    for m in _NAV_AT.finditer(code):
        if m.group(0).endswith("("):
            p = m.end() - 1
            args = split_args(code[p + 1:match_close(code, p)])
            expr = args[0] if args else ""
        else:
            rest = code[m.end():m.end() + 600]
            expr = split_args(rest[:stmt_end(rest)] + ",")[0] if rest.strip() else ""
        if not expr or literal(expr) is not None or re.match(r"""\s*['"]\s*javascript:""", expr):
            continue
        out.append(summarize(expr, code))
    if depth > 0:
        for m in CALL.finditer(code):
            name = re.sub(r"\s+", "", m.group(1))
            if name in seen or name.split(".")[-1] in KEYWORDS or name not in sc.defs:
                continue
            ck = ("nav", name, depth)
            if ck not in sc.cache:
                sc.cache[ck] = []
                sc.cache[ck] = nav_computed(sc.defs[name][1], sc, depth - 1, seen | {name})
            out.extend(sc.cache[ck])
    return out


# the roots of obj.method() calls that are the browser, the page itself, the event or jQuery: never an external .js's
# function (design §7.8: a browser's own calls are not counted)
BUILTIN_ROOTS = frozenset({
    "document", "window", "location", "history", "navigator", "screen", "console", "Math", "JSON", "Object", "Array",
    "String", "Number", "Boolean", "Date", "Promise", "RegExp", "Reflect", "Symbol", "Intl", "Map", "Set", "WeakMap",
    "WeakSet", "Error", "Function", "URL", "URLSearchParams", "FormData", "localStorage", "sessionStorage",
    "performance", "crypto", "caches", "indexedDB", "globalThis", "self", "top", "parent", "opener", "frames", "this",
    "e", "ev", "evt", "event", "$", "jQuery", "arguments", "super", "CSS", "customElements", "Element", "Node",
    "HTMLElement", "Notification", "XMLHttpRequest", "axios",
})
# constructors of the browser's own objects: an object made with one of them is no external .js's (new Date().getTime())
BUILTIN_CTORS = frozenset({
    "Date", "URL", "URLSearchParams", "FormData", "XMLHttpRequest", "ActiveXObject", "Image", "Audio", "Option",
    "RegExp", "Array", "Object", "Map", "Set", "WeakMap", "WeakSet", "Promise", "Error", "TypeError", "Function",
    "String", "Number", "Boolean", "Blob", "File", "FileReader", "Event", "CustomEvent", "MouseEvent",
    "KeyboardEvent", "IntersectionObserver", "MutationObserver", "ResizeObserver", "AbortController", "Headers",
    "Request", "Response", "DOMParser", "TextEncoder", "TextDecoder", "Worker", "WebSocket", "EventSource", "Intl",
})
# x = …, var x = …, window.x = …: the name, and the constructor when the value is new X(…)
_ASSIGN = re.compile(rf"(?:\b(?:var|let|const)\s+|(?<![\w$.])(?:window\s*\.\s*)?)({_ID})\s*=(?![=>])\s*"
                     rf"(?:new\s+({_ID}(?:\s*\.\s*{_ID})*))?")
_DECLARED = re.compile(rf"\b(?:var|let|const)\s+({_ID})|\b(?:var|let|const)\s*[{{\[]([^}}\]]*)[}}\]]")
_PARAMS = re.compile(rf"\bfunction\b\s*(?:{_ID})?\s*\(([^()]*)\)|\(([^()]*)\)\s*=>|({_ID})\s*=>|"
                     rf"\bcatch\s*\(\s*({_ID})")


def _assigned(code: str) -> tuple[dict[str, set[str]], set[str]]:
    """What the code gives a value: name -> the constructors of its new X(…) values ("" for any other value), and the
    names it declares (var, let, const, destructuring included). A namespace the code only makes sure exists
    (var App = window.App || {}) is given no value here: an external .js fills it."""
    values: dict[str, set[str]] = {}
    itself: set[str] = set()
    for m in _ASSIGN.finditer(code):
        name = m.group(1)
        if not m.group(2) and re.match(rf"(?:window\s*\.\s*)?{re.escape(name)}\s*\|\|\s*\{{",
                                       code[m.end():m.end() + 80]):  # X || [] (an analytics queue) is the page's
            itself.add(name)
            continue
        values.setdefault(name, set()).add(re.sub(r"\s+", "", m.group(2) or ""))
    declared = set()
    for m in _DECLARED.finditer(code):
        declared.update(x for x in re.findall(_ID, m.group(1) or m.group(2) or ""))
    return values, declared - (itself - set(values))


def _names_in(code: str) -> set[str]:
    """The parameters of every function the code holds (function (a, b), (a, b) =>, a =>, catch (err))."""
    out = set()
    for m in _PARAMS.finditer(code):
        for part in (m.group(1), m.group(2), m.group(3), m.group(4)):
            if part:
                out.update(p.split("=")[0].strip() for p in part.split(",") if p.strip())
    return out


def external_object(root: str, code: str, sc: Script, params=()) -> bool:
    """Whether root, in root.method() inside a handler, is an object an external .js makes (design §7.8: obj.method()
    the page does not define is unhandled): not the browser's, the page's, the event's or jQuery's (BUILTIN_ROOTS),
    not a parameter or a variable of the handler, and either given no value by any inline script (a global an external
    .js makes) or only made with new X(…) of a constructor no inline script defines and the browser does not have
    (var tabs = new ExtTabs()). A variable the page gives any other value (an element, a selection, an object literal,
    a number) is the page's own."""
    if root in BUILTIN_ROOTS or root in KEYWORDS or root.startswith("$") or root in params or root in _names_in(code):
        return False
    ck = ("assigned",)
    if ck not in sc.cache:
        sc.cache[ck] = _assigned(sc.code)
    for values, declared in (_assigned(code), sc.cache[ck]):
        if root in values:
            made = values[root]
            return all(c and c.split(".")[0] not in BUILTIN_CTORS and sc.defined(c) is None for c in made)
        if root in declared:
            return False  # declared, its value set where this reading cannot see: the page's own
    return True


def undefined_calls(code: str, sc: Script, params=(), depth: int = 3, seen: frozenset = frozenset()) -> list[str]:
    """Function calls this handler makes, directly or through the page's own functions (3 levels down), to functions
    the page does not define (an external .js): plain calls, and obj.method() on an object an external .js makes
    (external_object: ExtTabs.show(n), app.news.open(c)), as obj.method. Built-ins, constructors (new X), the
    handler's parameters and its local variables, and obj.method calls on the browser's, the page's or jQuery's
    objects (el.classList.add, console.log, document.getElementById) are not counted."""
    local = set(params) | set(re.findall(rf"\b(?:var|let|const)\s+({_ID})", code))
    out: list[str] = []

    def follow(key: str) -> None:
        if depth > 0 and key not in seen:
            for x in undefined_calls(sc.defs[key][1], sc, sc.defs[key][0], depth - 1, seen | {key}):
                if x not in out:
                    out.append(x)

    for m in CALL.finditer(code):
        name = re.sub(r"\s+", "", m.group(1))
        short = name.split(".")[-1]
        if short in KEYWORDS or name in NOT_CONTENT or short in NOT_CONTENT or name in local or name in seen:
            continue
        if name in ("$", "jQuery", "open", "window.open") or name.startswith("$"):
            continue
        if NEW_BEFORE.search(code[max(0, m.start() - 8):m.start()]):
            continue
        fname = name.removeprefix("window.")
        if "." in fname:
            root = fname.split(".")[0]
            if root in BUILTIN_ROOTS or root in local or root in params:
                continue
            key = sc.defined(fname) or (root if root in sc.defs else None)
            if key is not None:
                follow(key)  # the page's own method (app.open, f.call): read as the page's function
            elif external_object(root, code, sc, params) and fname not in out:
                out.append(fname)
            continue
        if fname in sc.defs:
            follow(fname)
            continue
        if fname not in out:
            out.append(fname)
    return out


# ---------------------------------------------------------------- handlers bound in inline scripts


@dataclass(frozen=True)
class Binding:
    """One handler an inline script binds: the selector of the elements it is bound to (<delegated> when a document's
    or a window's handler names none), the event, the handler's body and what it calls."""

    selector: str
    event: str
    how: str  # function, arrow, named:<f>, unknown:<…>
    body: str
    net: tuple[NetCall, ...]
    nav: tuple[str, ...]
    params: tuple[str, ...] = ()  # the handler's parameters (function (e) …, a page function's own)


@dataclass(frozen=True)
class AutoFire:
    """An event the script fires itself when the page loads ($('.category').change(), .trigger('click'))."""

    selector: str
    event: str


_JQ_START = re.compile(r"(?<![\w$.])(?:\$|jQuery)\s*\(")
_DOM_START = re.compile(r"(?<![\w$])document\s*\.\s*(querySelectorAll|querySelector|getElementById|"
                        r"getElementsByClassName|getElementsByTagName|getElementsByName)\s*\(")
JQ_PSEUDO = re.compile(r":(?:eq|gt|lt|nth)\(\s*\d+\s*\)|:first(?![-\w])|:last(?![-\w])|:even|:odd|:visible|:hidden|"
                       r":animated|:contains\([^)]*\)|:input|:button|:selected|:checked")
CLICK_EVENTS = re.compile(r"(?i)\b(click|change|tap|touchstart|mouseup|mousedown|mouseenter|mouseover|hover|"
                          r"shown\.bs\.tab|show\.bs\.tab|shown\.bs\.collapse|show\.bs\.collapse)\b")
_DELEGATE_START = re.compile(r"(?<![\w$.])(?:document(?:\s*\.\s*body)?|window)(?=\s*\.\s*addEventListener\b)")
_DELEGATE_SEL = re.compile(r"""\.\s*(?:matches|closest)\s*\(\s*['"]([^'"]+)['"]""")


def _chain_at(code: str, k: int) -> list[tuple[str, str, int, int]]:
    """From code[k] (just after a receiver), the method calls chained on it: (name, args, args_start, args_end); an
    .onclick = handler assignment ends the chain as ('=onclick', handler, …)."""
    calls = []
    n = len(code)
    while True:
        j = k
        while j < n and code[j].isspace():
            j += 1
        if j < n and code[j] == "[":
            k = match_close(code, j) + 1
            continue
        if j >= n or code[j] != ".":
            return calls
        j += 1
        while j < n and code[j].isspace():
            j += 1
        m = re.match(_ID, code[j:])
        if not m:
            return calls
        name = m.group(0)
        j += len(name)
        while j < n and code[j].isspace():
            j += 1
        if j < n and code[j] == "(":
            e = match_close(code, j)
            calls.append((name, code[j + 1:e], j + 1, e))
            k = e + 1
        elif name in ("onclick", "onchange") and re.match(r"\s*=(?!=)", code[j:]):
            am = re.match(r"\s*=\s*", code[j:])
            st = j + am.end()
            calls.append(("=" + name, code[st:st + 4000], st, st + 4000))
            return calls
        else:
            return calls


def _handler_body(arg: str, sc: Script) -> tuple[str, str, tuple[str, ...]]:
    """(body, how, parameters) of a handler argument: function(){…}, (e) => {…}, e => expr, or a name defined on the
    page."""
    a = arg.strip()
    m = re.match(r"(?:async\s+)?function\b\s*(?:" + _ID + r")?\s*\(", a)
    if m:
        e = match_close(a, m.end() - 1)
        k = a.find("{", e)
        if k >= 0:
            return a[k + 1:match_close(a, k)], "function", _param_list(a[m.end():e])
    m = re.match(r"(?:async\s*)?(?:\(([^()]*)\)|(" + _ID + r"))\s*=>\s*", a)
    if m:
        k, params = m.end(), _param_list(m.group(1) or m.group(2) or "")
        if k < len(a) and a[k] == "{":
            return a[k + 1:match_close(a, k)], "arrow", params
        return a[k:].split(";")[0], "arrow", params
    m = re.match(_ID + r"(?:\." + _ID + r")*", a)
    if m and m.group(0) in sc.defs:
        params, body, _start = sc.defs[m.group(0)]
        return body, "named:" + m.group(0), tuple(params)
    return "", "unknown:" + a[:40], ()


def _param_list(text: str) -> tuple[str, ...]:
    return tuple(p.split("=")[0].strip() for p in text.split(",") if p.strip())


def _receiver(code: str, start: int, kind: str) -> tuple[str, int]:
    """The selector a chain start ($(…) or document.querySelector…) names, and the index just after it."""
    p = code.find("(", start)
    if p < 0:
        raise ValueError("no argument list")
    e = match_close(code, p)
    inner = code[p + 1:e].strip()
    first = split_args(inner)[0] if inner else ""
    lit = literal(first)
    if kind == "jq":
        if lit is not None:
            return lit, e + 1
        if first in ("this", "e.currentTarget", "event.currentTarget", "e.target", "event.target"):
            return "<this>", e + 1
        if first in ("document", "window", "'body'", '"body"', "document.body"):
            return "<document>", e + 1
        return f"<var:{first[:30]}>", e + 1
    if lit is None:
        return f"<var:{first[:30]}>", e + 1
    return {"getElementById": "#" + lit, "getElementsByClassName": "." + lit.replace(" ", "."),
            "getElementsByTagName": lit, "getElementsByName": f"[name='{lit}']"}.get(kind, lit), e + 1


def _loop_params(cb: str) -> list[str]:
    pm = re.match(r"(?:function\s*\(([^)]*)\)|\(([^)]*)\)\s*=>|(" + _ID + r")\s*=>)", cb.strip())
    return [] if not pm else [x.strip() for x in (pm.group(1) or pm.group(2) or pm.group(3) or "").split(",")]


def bindings(sc: Script) -> tuple[list[Binding], list[AutoFire]]:
    """Click and change handlers bound in the page's inline scripts, and the events the scripts fire on load."""
    code = sc.code
    out: list[Binding] = []
    auto: list[AutoFire] = []
    scopes = []  # (start, end, selector, param) of .each / .forEach callbacks: $(this) and their parameter inside
    starts = [(m.start(), "jq") for m in _JQ_START.finditer(code)]
    starts += [(m.start(), m.group(1)) for m in _DOM_START.finditer(code)]
    starts += [(m.start(), "delegate:" + str(m.end())) for m in _DELEGATE_START.finditer(code)]
    starts.sort()
    parsed = []
    for st, kind in starts:
        try:
            if kind.startswith("delegate:"):
                sel, after = "<delegated>", int(kind.split(":", 1)[1])
            else:
                sel, after = _receiver(code, st, kind)
        except (ValueError, IndexError):
            continue
        calls = _chain_at(code, after)
        parsed.append((st, sel, calls))
        cur = sel
        for name, args, a0, a1 in calls:
            first = split_args(args)[0] if args.strip() else ""
            if name in ("find", "children", "filter") and literal(first) is not None:
                cur = f"{cur} {literal(first)}" if name != "filter" else f"{cur}{literal(first)}"
            if name in ("each", "forEach"):
                params = _loop_params(args)
                param = params[-1 if name == "each" else 0] if params and params[0] else ""
                scopes.append((a0, a1, cur, param))
    varsel = {}  # var tabs = document.querySelectorAll('.tab') / var $x = $('.x')
    for m in re.finditer(rf"(?:var|let|const)\s+({_ID})\s*=\s*(?=(?:\$|jQuery)\s*\(|document\s*\.)", code):
        dm = _DOM_START.match(code, m.end())
        try:
            varsel[m.group(1)], _ = _receiver(code, m.end(), dm.group(1) if dm else "jq")
        except (ValueError, IndexError):
            continue
    for m in re.finditer(rf"(?<![\w$.])({_ID})(?=\s*(?:\[[^\]]*\]\s*)?(?:\.\s*(?:addEventListener|onclick|onchange|"
                         rf"click|on|bind|forEach)\b))", code):
        if m.group(1) in ("document", "window", "$", "jQuery", "this") or m.group(1) in KEYWORDS:
            continue
        calls = _chain_at(code, m.end())
        parsed.append((m.start(), f"<var:{m.group(1)}>", calls))
        for name, args, a0, a1 in calls:
            if name == "forEach":
                params = _loop_params(args)
                scopes.append((a0, a1, f"<var:{m.group(1)}>", params[0] if params else ""))

    def resolve(sel: str, pos: int) -> str:
        """$(this), or the callback parameter of an enclosing .each/.forEach, is that loop's selection."""
        for _ in range(6):
            if sel == "<this>":
                inner = [x for x in scopes if x[0] <= pos <= x[1]]
            elif sel.startswith("<var:"):
                v = sel[5:-1]
                inner = [x for x in scopes if x[0] <= pos <= x[1] and x[3] == v]
                if not inner and v in varsel:
                    sel = varsel[v]
                    continue
            else:
                return sel
            if not inner:
                return sel
            x = min(inner, key=lambda x: x[1] - x[0])
            sel, pos = x[2], x[0] - 1
        return sel

    seen = set()
    for st, sel, calls in parsed:
        cur = sel
        for name, args, a0, _a1 in calls:
            al = split_args(args) if args.strip() else []
            if name in ("find", "children") and al and literal(al[0]) is not None:
                cur = f"{cur} {literal(al[0])}"
                continue
            if name == "filter" and al and literal(al[0]) is not None:
                cur = f"{cur}{literal(al[0])}"
                continue
            found = []  # (event, handler, child selector)
            if name in ("click", "change", "dblclick", "mouseenter", "mouseover") and al:
                found.append((name, al[-1], None))
            elif name == "hover" and al:
                found.append(("hover", al[0], None))
            elif name in ("on", "bind") and al and al[0].strip().startswith("{"):
                for part in split_args(al[0].strip()[1:-1]):
                    km = re.match(r"""\s*['"]?([\w.]+)['"]?\s*:\s*(.*)$""", part, re.DOTALL)
                    if km and CLICK_EVENTS.fullmatch(km.group(1)):
                        found.append((km.group(1).lower(), km.group(2), literal(al[1]) if len(al) >= 3 else None))
            elif (name in ("on", "bind", "live", "one") and len(al) >= 2 and literal(al[0]) is not None
                  and CLICK_EVENTS.search(literal(al[0]))):
                child = literal(al[1]) if len(al) >= 3 and literal(al[1]) is not None else None
                found.append((CLICK_EVENTS.search(literal(al[0])).group(1).lower(), al[-1], child))
            elif (name == "delegate" and len(al) >= 3 and literal(al[1]) is not None
                  and CLICK_EVENTS.search(literal(al[1]))):
                found.append((CLICK_EVENTS.search(literal(al[1])).group(1).lower(), al[-1], literal(al[0])))
            elif (name == "addEventListener" and len(al) >= 2 and literal(al[0]) is not None
                  and CLICK_EVENTS.search(literal(al[0]))):
                found.append((CLICK_EVENTS.search(literal(al[0])).group(1).lower(), al[1], None))
            elif name in ("=onclick", "=onchange"):
                found.append((name[3:], args, None))
            elif name in ("trigger", "triggerHandler") and al and literal(al[0]) in ("click", "change"):
                auto.append(AutoFire(resolve(cur, st), literal(al[0])))
                continue
            elif name in ("click", "change") and not al:
                auto.append(AutoFire(resolve(cur, st), name))
                continue
            for ev, handler, child in found:
                key = (a0, ev, handler[:40])
                if key in seen:
                    continue
                seen.add(key)
                base = resolve(cur, st)
                body, how, params = _handler_body(handler, sc)
                if base == "<delegated>":
                    dm = _DELEGATE_SEL.search(body or "")
                    target = dm.group(1) if dm else "<delegated>"
                elif child:
                    target = child if base == "<document>" else f"{base} {child}"
                else:
                    target = base
                out.append(Binding(target, ev, how, body, tuple(net_calls(body, sc)) if body else (),
                                   tuple(nav_computed(body, sc)) if body else (), params))
    return out, auto


def css_safe(tree, sel: str):
    """The elements a jQuery selector names, as a CSS selector lexbor can read (jQuery's own pseudo classes dropped);
    None when it names no element of the page (<this>, <var:…>, <delegated>) or cannot be read."""
    if sel.startswith("<"):
        return None
    s = re.sub(r"\s+>\s*$", "", JQ_PSEUDO.sub("", sel).strip())
    if not s:
        return None
    try:
        return tree.css(s)
    except Exception:  # noqa: BLE001 - a selector lexbor cannot read names nothing it can find
        return None


# ---------------------------------------------------------------- an element: shown on load, or a plain link


def real_href(h: str | None) -> bool:
    h = (h or "").strip()
    return bool(h) and not h.lower().startswith("javascript:") and not h.startswith("#")


def _same_page(a: str, b: str) -> bool:
    return a.split("#", 1)[0].rstrip("/") == b.split("#", 1)[0].rstrip("/")


def shown_on_load(node) -> bool:
    """Design §7.8 click:shown_on_load: the element, or the nearest <li> holding it, is marked as the one shown when
    the page loads (class active / current / selected / on, or aria-selected="true")."""
    def marked(x) -> bool:
        return (bool(re.search(r"(?i)(?:^|\s)(active|current|selected|on)(?:\s|$)", x.attributes.get("class") or ""))
                or (x.attributes.get("aria-selected") or "").lower() == "true")

    if marked(node):
        return True
    p = node.parent
    while p is not None and p.tag not in ("li", "body"):
        p = p.parent
    return p is not None and p.tag == "li" and marked(p)


# a literal address a handler opens (links.extract reads it as a link): the whole argument or right side one quoted
# string, followed by ; ) , } a line break or the end. 'news.php?id=' + id is no address (the old pattern took its
# first piece as one); findall gives (quote, address as written)
URL_CALL = re.compile(r"""(?:window\.open|location\.href\s*=|location\s*=|location\.assign|location\.replace)"""
                      r"""\s*\(?\s*(['"])((?:\\.|(?!\1)[^\\\n])+)\1(?=[ \t]*(?:[;),}\r\n]|$|//))""")


def plain_href(node, url: str) -> str | None:
    """Design §7.8 click:plain_href: the plain address the element is, holds or sits inside (an <a> whose href leads
    to another address: not javascript:, not #, not this page), or the literal URL its onclick opens; None if none.
    A crawler of plain GETs gets that address, so the click adds nothing."""
    def other(h: str | None) -> str | None:
        if not real_href(h):
            return None
        try:
            target = urljoin(url, h.strip())
        except ValueError:
            return None
        return None if _same_page(target, url) else target

    if node.tag in ("a", "area") and (t := other(node.attributes.get("href"))):
        return t
    if (m := URL_CALL.search(node.attributes.get("onclick") or "")) and (t := other(unescape(m.group(2)))):
        return t
    for a in node.css("a[href]"):
        if t := other(a.attributes.get("href")):
            return t
    p = node.parent
    while p is not None:
        if p.tag == "a" and (t := other(p.attributes.get("href"))):
            return t
        p = p.parent
    return None


# ---------------------------------------------------------------- where a script sends the browser (§8.3)


_REDIRECT_AT = re.compile(r"(?<![\w$.])(?:(?:window|document)\s*\.\s*)?location(?:\s*\.\s*href)?\s*=(?!=)"
                          r"|(?<![\w$.])(?:(?:window|document)\s*\.\s*)?location\s*\.\s*(?:replace|assign)\s*\(")
_BLOCK_WORDS = re.compile(r"(?:^|[^\w$])(?:if|while|for|with|else|do|switch)\s*$")
_STR_AT = re.compile(r"""(['"])((?:\\.|(?!\1)[^\\\n])*)\1""")


STRING, COMMENT = -1, -2  # what _top_level() marks a character of a string (or regex literal) and of a comment


def _top_level(code: str) -> list[int]:
    """The brace depth of each character of code: 0 for what is outside every { } block. A character of a string, a
    template or a regular-expression literal is STRING, one of a comment (//, /* */, <!--) COMMENT."""
    depth, out, j, n = 0, [0] * len(code), 0, len(code)
    prev = ";"

    def mark(a: int, b: int, what: int) -> None:
        for x in range(a, min(b, n)):
            out[x] = what

    while j < n:
        c = code[j]
        if c in "'\"`":
            k = skip_string(code, j)
            mark(j, k, STRING)
            j, prev = k, "a"
            continue
        if c == "/" and j + 1 < n and code[j + 1] == "/" or c == "<" and code.startswith("<!--", j):
            k = code.find("\n", j)
            k = n if k < 0 else k
            mark(j, k, COMMENT)
            j = k
            continue
        if c == "/" and j + 1 < n and code[j + 1] == "*":
            k = code.find("*/", j + 2)
            k = n if k < 0 else k + 2
            mark(j, k, COMMENT)
            j = k
            continue
        if c == "/" and prev in _REGEX_PREV:
            k = j + 1
            while k < n and code[k] not in "/\n":
                k += 2 if code[k] == "\\" else 1
            mark(j, k + 1, STRING)
            j, prev = k + 1, "a"
            continue
        if c == "{":
            depth += 1
        out[j] = depth
        if c == "}":
            depth = max(0, depth - 1)
        if not c.isspace():
            prev = c
        j += 1
    return out


def _statement_start(code: str, i: int, depth: list[int]) -> bool:
    """Whether code[i] starts a statement of its own, at the top level: nothing before it, or a ; or a } before it,
    or a line break after the end of the previous statement (never right after if (…), else, a = or an operator)."""
    k = i - 1
    newline = False
    while k >= 0 and (code[k].isspace() or depth[k] == COMMENT):
        if code[k] == "\n":
            newline = True
        k -= 1
    if k < 0 or (code[k] in ";}" and depth[k] != STRING):
        return True
    if not newline:
        return False
    if code[k] == ")" and depth[k] != STRING:
        # the ) of if (…), while (…), for (…): the statement is that block's
        o, level = k, 0
        while o >= 0:
            if depth[o] >= 0 and code[o] == ")":
                level += 1
            elif depth[o] >= 0 and code[o] == "(":
                level -= 1
                if level == 0:
                    break
            o -= 1
        return not _BLOCK_WORDS.search(code[max(0, o - 12):max(0, o)])
    if _BLOCK_WORDS.search(code[max(0, k - 10):k + 1]):
        return False
    return depth[k] == STRING or bool(re.match(r"[\w$\])]", code[k]))


def script_redirects(code: str) -> list[str]:
    """The addresses an inline script sends the browser to by itself (design §8.3): location.href, window.location,
    document.location (.href) = '…', or location.replace / .assign('…'), written as statements of their own at the top
    level (not in a function, an if or any { } block, not right after if (…) or else, not in a string such as a
    setTimeout's), the whole right side one string literal followed by ; ) } a line break or the end. Its escapes are
    undone (\\/ is /); an empty address, # and javascript: are none."""
    depth = _top_level(code)
    out = []
    for m in _REDIRECT_AT.finditer(code):
        i = m.start()
        if depth[i] != 0 or not _statement_start(code, i, depth):
            continue
        rest = code[m.end():]
        sm = _STR_AT.match(rest.lstrip(" \t"))
        if sm is None:
            continue
        after = rest.lstrip(" \t")[sm.end():]
        if m.group(0).endswith("("):
            if not re.match(r"\s*\)\s*(?:[;}]|$|\n)", after):
                continue
        elif not re.match(r"[ \t]*(?:[;)}\r\n]|$|//|/\*)", after):
            continue  # '?orderBy=' + $(this).val(): not one literal
        target = unescape(sm.group(2)).strip()
        if not target or target.startswith("#") or target.lower().startswith("javascript:"):
            continue
        out.append(target)
    return out
