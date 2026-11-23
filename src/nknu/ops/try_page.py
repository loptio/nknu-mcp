"""搜尋 (檢視台 /try; 試用 until the owner's 「那個不叫"搜索試用"，叫搜索就好」, 11-17): the search service as a
student's AI agent sees it, and its tools called as the agent calls them.

Since 11-17 evening (spec docs/superpowers/specs/2026-11-17-panels-v3-questions.md 八.7: the page follows the MCP
tools' own design, read from the service, nothing written here): the tool a question is asked with (search_tool) is the
page's one form, its first required text parameter a big box, an enum a row of buttons (each option's sentence of the
description floating over it, the default marked), an integer with a range a small number field, a text parameter
whose description gives examples in 「」 a box offering the names the service's instructions list beside them
(suggestions); the tool that reads one result again (follow_up: its one required parameter is a key the results
carry) has no form of its own: each result card calls it (讀這一份, and a value its description names, as 目錄), and a
document it returns links each of its sections; any other tool keeps a form of its own, folded. The reply stays on the
left exactly as the agent receives it.

Design: docs/superpowers/specs/2026-11-17-panels-structure-design.md section 三.1, which the owner approved; owner:
「真实的user agent看见的东西是什么样的，把他坐在一个网页上 让我尝试用user agent的角度去用这个工具，然后来看具体返回的东西」.
Redesign 2026-11-17 (research/dashboard_redesign_2026-11-17/diagnosis.md); the layout and the wording are 【Claude】's.

- On open the page shows what the agent reads before its first call, exactly as the service sent it: the handshake
  (initialize: the server's name and version and its instructions) and the tool list (tools/list: each tool's name,
  description and input schema), the bytes on the left (原文), a readable rendering on the right (排版), and how much
  of the agent's context they take (characters, and tokens estimated: nknu.ops.mcp_client.approx_tokens).
- One form per tool, made from the tool's own input schema (string: a text box, integer and number: a number box,
  enum: a choice, boolean: a check box, anything else: JSON text). No tool name and no parameter is written here: the
  interface session changes the tools (search gained detail, limit, unit and sort; read became fetch on 11-17), and
  the page follows whatever tools/list says.
- Sent, the call is made from this server (the browser never talks to the service), one at a time: a second call while
  one runs is told so, not queued (the service shares the lab's GPU). Left: what the agent receives, the text unchanged,
  its length, its tokens, how long it took; right: the same answer laid out, a search's results as cards. A card can be
  fetched again with the tool that takes its id (found in the schemas, not by name), and 「為什麼排在這裡」 opens the
  second tab for the same question with that document followed.
- Only tools/call of a tool the list marks read-only (annotations.readOnlyHint); nothing else is ever called.

搜尋測試 is this page's second tab (structure design section 二; done 2026-11-17 by the worker 「entries」): the result's
tab bar (result_tabs) has 「agent 收到的」 (the tool called through the service) and 「為什麼這樣排」 (/try?tab=why&q=…:
nknu.ops.search_page's explanation of the same question on the library, drawn here by nknu.ops.web; the service is
not called for it, so it works while the service is down). With nothing asked the page ends in the scoreboard of the
test questions (nknu.ops.board), as 搜尋測試's empty page did; /search redirects here.
"""

from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from nknu.ops.look import e, go_button, icon
from nknu.ops.mcp_client import Exchange, McpClient, Session, approx_tokens
from nknu.ops.search_page import why_url

CALL, ARG = "call", "a."  # the tool to call; one argument: a.<its name>
SNIP = 180  # characters of a result's text shown on its card before 「全文」 opens the rest 【Claude】
ID_KEYS = ("id", "url", "doc_id", "ref")  # what a card may be fetched again by, the first a tool takes wins 【Claude】
KIND_WORDS = (("公告", "notice", 0), ("網頁", "page", 1), ("附件", "attachment", 2), ("行事曆", "event", 3),
              ("校外連結", "link", 4), ("外部連結", "link", 4))  # nknu.retrieval.respond.KIND_LABELS, ops.trace.KINDS
KIND_ICON = {"notice": "notice", "page": "page", "attachment": "attachment", "event": "event", "link": "link"}
UNDERSTOOD = {"aliases": "同義詞", "terms": "學期", "cohorts": "屆別", "tokens": "詞"}
BODY_KEYS = ("content", "text", "snippet", "passage", "summary")
SHOWN = {"rank", "id", "identity", "title", "url", "data_as_of", "dates", "status", "content_type", "header", "more",
         "background", "parent", "copies", *BODY_KEYS}  # a card's keys drawn on it; any other is listed under it
TOKEN_TIP = "估算：中文字每字約 1 個 token，其他字元約 3 個算 1 個；實際依各家模型的分詞而定"
WAIT_WORDS = "服務剛重啟後的第一次要載入模型，約 30 秒"
REQUIRED = "<b class='tr-req'>必填</b>"


# ---------------------------------------------------------------- the schema → the form

@dataclass
class Field:
    """One parameter of a tool as the form asks it. control: text, integer, number, select, checkbox or json; types:
    the JSON types its value may take, in the schema's order."""

    name: str
    control: str
    required: bool
    description: str = ""
    has_default: bool = False
    default: object = None
    options: tuple = ()
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    types: tuple[str, ...] = ()


def _resolve(prop: object, schema: dict, depth: int = 0) -> dict:
    """prop with a local $ref (#/$defs/…, #/definitions/…) followed, its own keys kept over the target's."""
    if not isinstance(prop, dict):
        return {}
    ref = prop.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/") or depth > 5:
        return prop
    target: object = schema
    for part in ref[2:].split("/"):
        target = target.get(part) if isinstance(target, dict) else None
    rest = {k: v for k, v in prop.items() if k != "$ref"}
    return {**_resolve(target, schema, depth + 1), **rest}


def _types(prop: dict, schema: dict) -> tuple[str, ...]:
    kind = prop.get("type")
    if isinstance(kind, str):
        return (kind,)
    if isinstance(kind, list):
        return tuple(k for k in kind if isinstance(k, str) and k != "null")
    out: list[str] = []
    for branch in prop.get("anyOf") or prop.get("oneOf") or []:
        for k in _types(_resolve(branch, schema), schema):
            if k != "null" and k not in out:
                out.append(k)
    if not out and isinstance(prop.get("enum"), list):
        out = list(dict.fromkeys("integer" if isinstance(v, int) and not isinstance(v, bool) else
                                 "number" if isinstance(v, float) else "boolean" if isinstance(v, bool) else "string"
                                 for v in prop["enum"]))
    return tuple(out)


def _options(prop: dict, schema: dict) -> tuple:
    if isinstance(prop.get("enum"), list):
        return tuple(prop["enum"])
    if "const" in prop:
        return (prop["const"],)
    branches = [_resolve(b, schema) for b in prop.get("anyOf") or prop.get("oneOf") or []]
    values = [b.get("const") for b in branches if "const" in b]
    return tuple(values) if branches and len(values) == len(branches) else ()


def fields(schema: object) -> list[Field]:
    """The form's fields for a tool's inputSchema, in the schema's order."""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    required = set(schema.get("required") or [])
    out = []
    for name, raw in (props.items() if isinstance(props, dict) else []):
        prop = _resolve(raw, schema)
        types = _types(prop, schema)
        options = _options(prop, schema)
        if options:
            control = "select"
        elif types == ("boolean",):
            control = "checkbox"
        elif types == ("integer",):
            control = "integer"
        elif types == ("number",):
            control = "number"
        elif types and all(t in ("array", "object") for t in types):
            control = "json"
        else:
            control = "text"
        size = prop.get("maxLength")
        out.append(Field(name=str(name), control=control, required=name in required,
                         description=str(prop.get("description") or ""), has_default="default" in prop,
                         default=prop.get("default"), options=options, minimum=_number(prop, "minimum"),
                         maximum=_number(prop, "maximum"), max_length=size if isinstance(size, int) else None,
                         types=types))
    return out


def _number(prop: dict, key: str) -> float | None:
    v = prop.get(key)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _all(params: object, key: str) -> list[str]:
    if hasattr(params, "getlist"):
        return [str(v) for v in params.getlist(key)]
    if isinstance(params, dict) and key in params:
        v = params[key]
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]
    return []


def _as(value: str, kind: str) -> object:
    """value read as one JSON type; ValueError when it is not one."""
    if kind == "integer":
        text = value.strip()
        if not re.fullmatch(r"[+-]?\d{1,18}", text):
            raise ValueError(value)
        return int(text)
    if kind == "number":
        number = float(value.strip())
        if not math.isfinite(number):
            raise ValueError(value)
        return int(number) if number.is_integer() and "." not in value and "e" not in value.lower() else number
    if kind == "boolean":
        if value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValueError(value)
    if kind in ("array", "object"):
        got = json.loads(value)
        if (kind == "array") != isinstance(got, list) or (kind == "object" and not isinstance(got, dict)):
            raise ValueError(value)
        return got
    if kind == "null":
        raise ValueError(value)
    return value  # string, or a type the form cannot tell


def arguments(schema: object, params: object) -> dict:
    """The arguments of a tools/call from the submitted form: a field left empty is left out (the service then uses
    its default, as when an agent leaves it out); a value is sent as the first of its schema's types that reads it
    (a number box's text that is no number is sent as typed: the agent would get the service's own complaint); a check
    box is sent only when it differs from the default."""
    out: dict = {}
    for f in fields(schema):
        got = _all(params, ARG + f.name)
        if f.control == "checkbox":
            if not got:
                continue
            on = got[-1].strip().lower() == "true"
            if f.required or on != (f.default if f.has_default else False):
                out[f.name] = on
            continue
        if not got or not got[-1].strip():
            continue
        value = got[-1]
        if f.control == "select":
            if f.has_default and str(f.default) == value:  # the default: left out, as an agent leaves it
                continue
            out[f.name] = next((o for o in f.options if str(o) == value), value)
            continue
        kinds = f.types or ("string",)
        for kind in kinds:
            try:
                out[f.name] = _as(value, kind)
                break
            except ValueError:
                continue
        else:
            out[f.name] = value
    return out


def type_words(f: Field) -> str:
    """A parameter's type in words, for the readable tool list and the field's floating card."""
    if f.control == "select":
        return "選一個：" + "、".join(str(o) for o in f.options)
    names = {"string": "文字", "integer": "整數", "number": "數字", "boolean": "是／否", "array": "清單",
             "object": "物件"}
    words = "或".join(names.get(t, t) for t in f.types) or "不限"
    if f.minimum is not None or f.maximum is not None:
        lo = "" if f.minimum is None else f"{f.minimum:g}"
        hi = "" if f.maximum is None else f"{f.maximum:g}"
        words += f" {lo}～{hi}"
    if f.max_length:
        words += f"，最長 {f.max_length} 字"
    return words


def _shown_default(f: Field) -> str:
    if not f.has_default:
        return ""
    if f.default == "":
        return "空白"
    return json.dumps(f.default, ensure_ascii=False) if not isinstance(f.default, str) else f.default


def field_html(f: Field, value: str | None, *, wide: bool) -> str:
    """One field of a tool's form, its name in the agent's words (the parameter's own name), the schema's description
    floating over it."""
    name = ARG + f.name
    req = " required" if f.required else ""
    head = f"{f.name}　{type_words(f)}" + ("　必填" if f.required else "")
    label = (f"<span class='tr-k' data-tip-h='{e(head)}' data-tip='{e(f.description)}'>{name_html(f)}"
             + (REQUIRED if f.required else "") + "</span>")
    default = _shown_default(f)
    hint = f" placeholder='{e('預設 ' + default)}'" if default and default != "空白" else ""
    if f.control == "checkbox":
        on = (value or "").lower() == "true" if value is not None else bool(f.has_default and f.default is True)
        return (f"<label class='tr-f check'><input type='hidden' name='{e(name)}' value='false'>"
                f"<input type='checkbox' name='{e(name)}' value='true'{' checked' if on else ''}>{label}</label>")
    if f.control == "select":
        first = "" if f.required else (f"<option value=''>預設（{e(default)}）</option>" if default
                                        else "<option value=''>（不填）</option>")
        opts = "".join(f"<option value='{e(o)}'{' selected' if value is not None and str(o) == value else ''}>"
                       f"{e(o)}</option>" for o in f.options)
        return f"<label class='tr-f'>{label}<select name='{e(name)}'{req}>{first}{opts}</select></label>"
    if f.control == "json":
        return (f"<label class='tr-f wide'>{label}<textarea name='{e(name)}' rows='3' spellcheck='false'{req}>"
                f"{e(value or '')}</textarea></label>")
    if f.control in ("integer", "number"):
        lo = f" min='{f.minimum:g}'" if f.minimum is not None else ""
        hi = f" max='{f.maximum:g}'" if f.maximum is not None else ""
        step = " step='1'" if f.control == "integer" else " step='any'"
        return (f"<label class='tr-f'>{label}<input type='number' name='{e(name)}' value='{e(value or '')}'"
                f"{lo}{hi}{step}{hint}{req} inputmode='numeric'></label>")
    size = f" maxlength='{f.max_length}'" if f.max_length else ""
    return (f"<label class='tr-f{' wide' if wide else ''}'>{label}<input type='text' name='{e(name)}' "
            f"value='{e(value or '')}'{size}{hint}{req} autocomplete='off'></label>")


def read_only(tool: dict) -> bool:
    """Whether the list marks the tool read-only: the only kind this page calls."""
    notes = tool.get("annotations")
    return isinstance(notes, dict) and notes.get("readOnlyHint") is True


def query_field(tool: dict | None) -> str:
    """The parameter that holds the question: the tool's first required text parameter ('' when it has none)."""
    for f in fields((tool or {}).get("inputSchema")):
        if f.required and f.control == "text" and "string" in (f.types or ("string",)):
            return f.name
    return ""


def search_tool(tools: list[dict]) -> dict | None:
    """The tool a question is asked with: the first read-only tool whose question parameter is no id (ID_KEYS: the
    tool that reads one result again takes an id). Found in the schemas, never by the tool's name."""
    for t in tools:
        asked = query_field(t)
        if read_only(t) and asked and asked not in ID_KEYS:
            return t
    return None


def form_html(tool: dict, values: dict[str, str], *, shown: bool) -> str:
    """The form of one tool; values: what was sent last, by parameter name."""
    name = str(tool.get("name"))
    asked = query_field(tool)
    rows = "".join(field_html(f, values.get(f.name), wide=f.name == asked) for f in fields(tool.get("inputSchema")))
    if not rows:
        rows = "<p class='faint small tr-none'>這個工具沒有參數</p>"
    allowed = read_only(tool)
    lock = "" if allowed else " disabled"
    why = "" if allowed else ("<span class='pill none' data-tip='工具清單沒有把它標成只讀（readOnlyHint）；這一頁只呼叫只讀的工具'>"
                              f"{icon('no', 12)}不呼叫</span>")
    return (f"<form class='tr-form' method='get' action='/try#result' data-tool='{e(name)}'"
            f"{'' if shown else ' hidden'}><input type='hidden' name='{CALL}' value='{e(name)}'>"
            f"<fieldset{lock}><div class='tr-fields'>{rows}</div>"
            f"<div class='tr-go'><button type='submit'>送出</button>{why}"
            f"<span class='tr-wait' data-wait hidden></span></div></fieldset></form>")


def instructions_of(s: Session) -> str:
    """The service's instructions (initialize), '' without."""
    return str((s.hello.result or {}).get("instructions") or "")


def suggestions(description: str, instructions: str) -> list[str]:
    """Names to offer for a text parameter whose description gives examples in 「」: the names listed in the
    paragraphs of the service's instructions that hold one of the examples, read generically (each line's list after
    its own heading, split at 、；（）：). A parameter whose description gives no example gets none 【Claude】."""
    examples = re.findall(r"「([^」]{1,30})」", description)
    if not examples or not instructions:
        return []
    names: list[str] = []
    for block in re.split(r"\n\s*\n", instructions):
        if not any(x in block for x in examples):
            continue
        for line in block.splitlines():
            body = line.split("：", 1)[1] if "：" in line else ""
            for word in re.split(r"[、；;（）()：:，,。\s]+", body):
                word = word.strip()
                if 2 <= len(word) <= 24 and not re.search(r"[A-Za-z0-9]", word):
                    names.append(word)
    return list(dict.fromkeys(names))


def option_tip(description: str, option: object) -> str:
    """The sentence of a parameter's description that is about one of its options (it starts with the option),
    else the whole description."""
    for part in re.split(r"(?<=[。；])", description):
        if part.strip().startswith(str(option)):
            return part.strip()
    return description


# a parameter's plain name when its description does not open with one (search's sort opens with its first option);
# only a reading aid: the name the agent sends stays beside it, and a name not here simply has none 【Claude】
PLAIN_NAMES = {"query": "要找什麼", "sort": "排序", "limit": "幾筆", "detail": "每筆多少原文", "unit": "單位", "id": "哪一份",
               "part": "哪一節"}
GLOSS_MAX = 12  # characters a description's opening clause may have to stand as the parameter's plain name


def gloss(f: Field) -> str:
    """A parameter's name in plain words, for the person reading the form: the opening clause of the service's own
    description when it is a short one in the reader's language (每筆給多少原文, 要幾筆), else PLAIN_NAMES', else ''."""
    first = re.split(r"[，。：；、（(]", " ".join((f.description or "").split()), maxsplit=1)[0].strip()
    if first and not first[0].isascii() and len(first) <= GLOSS_MAX:
        return first
    return PLAIN_NAMES.get(f.name, "")


def name_html(f: Field) -> str:
    """A parameter's label: its plain name (gloss) and, beside it, the name the agent sends."""
    plain = gloss(f)
    return (f"<span class='tr-g'>{e(plain)}</span><code>{e(f.name)}</code>" if plain else f"<code>{e(f.name)}</code>")


def param_html(f: Field, value: str | None, instructions: str, tool: str) -> str:
    """One parameter of the search form, as compact as its schema allows: an enum as a row of buttons (its default
    picked and marked 預設), an integer or a number as a small number field with its range, a text box with the
    names the instructions list (suggestions), anything else as the general form's field."""
    label = (f"<span class='tr-k' data-tip-h='{e(f.name)}　{e(type_words(f))}' data-tip='{e(f.description)}'>"
             f"{name_html(f)}" + (REQUIRED if f.required else "") + "</span>")
    name = ARG + f.name
    if f.control == "select":
        picked = value if value is not None and value != "" else (str(f.default) if f.has_default else "")
        if not picked and f.required and f.options:
            picked = str(f.options[0])
        opts = []
        if not f.required and not f.has_default:
            opts.append(f"<label data-tip='不填（服務自己決定）'><input type='radio' name='{e(name)}' value=''"
                        f"{' checked' if not picked else ''}><span>不填</span></label>")
        for o in f.options:
            mark = "<i>預設</i>" if f.has_default and o == f.default else ""
            opts.append(f"<label data-tip='{e(option_tip(f.description, o))}'><input type='radio' name='{e(name)}' "
                        f"value='{e(o)}'{' checked' if str(o) == picked else ''}><span>{e(o)}{mark}</span></label>")
        return f"<div class='sr-p'>{label}<div class='sr-seg' role='radiogroup'>{''.join(opts)}</div></div>"
    if f.control in ("integer", "number"):
        lo = f" min='{f.minimum:g}'" if f.minimum is not None else ""
        hi = f" max='{f.maximum:g}'" if f.maximum is not None else ""
        step = " step='1'" if f.control == "integer" else " step='any'"
        default = _shown_default(f)
        hint = f" placeholder='{e(default)}'" if default and default != "空白" else ""
        return (f"<div class='sr-p'>{label}<input class='sr-num' type='number' name='{e(name)}' value='{e(value or '')}'"
                f"{lo}{hi}{step}{hint} inputmode='numeric'></div>")
    if f.control == "text":
        names = suggestions(f.description, instructions)
        listed = f"sr-dl-{tool}-{f.name}"
        dl = ("<datalist id='" + e(listed) + "'>" + "".join(f"<option value='{e(x)}'>" for x in names)
              + "</datalist>") if names else ""
        hint = f" placeholder='{e('、'.join(names[:2]))}…'" if names else ""
        size = f" maxlength='{f.max_length}'" if f.max_length else ""
        return (f"<div class='sr-p'>{label}<input class='sr-txt' type='text' name='{e(name)}' value='{e(value or '')}'"
                + (f" list='{e(listed)}'" if names else "") + f"{hint}{size} autocomplete='off'>{dl}</div>")
    return field_html(f, value, wide=False)


def search_form_html(tool: dict, values: dict[str, str], instructions: str) -> str:
    """The page's one form: the search tool (search_tool), its question a big box, its other parameters in a row
    under it (param_html); values: what was sent last, by parameter name."""
    name = str(tool.get("name"))
    asked = query_field(tool)
    fs = fields(tool.get("inputSchema"))
    q = next((f for f in fs if f.name == asked), None)
    hint = _first_line(q.description, 40) if q is not None else ""
    top = (f"<div class='sr-top'><div class='sr-box'><input class='sr-q' type='text' "
           f"name='{e(ARG + asked)}' value='{e(values.get(asked) or '')}' placeholder='{e(hint)}' "
           f"aria-label='{e(asked)}' data-tip-h='{e(asked)}' data-tip='{e(q.description if q else '')}' required "
           f"autocomplete='off'>{go_button('搜尋', submit=True)}</div></div>")
    rest = "".join(param_html(f, values.get(f.name), instructions, name) for f in fs if f.name != asked)
    return (f"<search><form class='sr-form card pad' method='get' action='/try#result' data-tool='{e(name)}'>"
            f"<input type='hidden' name='{CALL}' value='{e(name)}'><fieldset>{top}"
            + (f"<div class='sr-opts'>{rest}</div>" if rest else "")
            + "<span class='tr-wait' data-wait hidden></span></fieldset></form></search>")


def _first_line(text: str, n: int = 90) -> str:
    one = " ".join(text.split())
    return one if len(one) <= n else one[:n] + "…"


# ---------------------------------------------------------------- numbers of a text

def counts_html(text: str, ms: float | None = None) -> str:
    """A text's length and tokens (and how long it took), for a pane's header."""
    parts = [f"<span class='num'>{len(text):,} 字</span>",
             f"<span class='num' data-tip='{e(TOKEN_TIP)}'>約 {approx_tokens(text):,} token</span>"]
    if ms is not None:
        parts.append(f"<span class='num'>{_seconds(ms)}</span>")
    return f"<span class='side tr-count'>{''.join(parts)}</span>"


def _seconds(ms: float) -> str:
    return f"{ms:.0f} 毫秒" if ms < 1000 else f"{ms / 1000:.1f} 秒"


def _pretty(raw: str) -> str:
    """raw as indented JSON when it is JSON ('' when it is not)."""
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        return ""


def raw_block(raw: str, caption: str = "") -> str:
    """A text exactly as received, and when it is JSON the same indented, behind 「排成多行」 (the script flips them)."""
    pretty = _pretty(raw)
    flip = ("<button type='button' class='btn tr-flip' data-flip>排成多行</button>" if pretty and pretty != raw else "")
    cap = f"<div class='tr-cap'><span class='mono'>{e(caption)}</span>{flip}</div>" if caption or flip else ""
    alt = f"<pre class='tr-pre' data-pretty hidden>{e(pretty)}</pre>" if flip else ""
    return f"<div class='tr-rawb'>{cap}<pre class='tr-pre' data-asis>{e(raw)}</pre>{alt}</div>"


# ---------------------------------------------------------------- what the agent sees first

def _tool_text(tool: dict) -> str:
    """What of a tool an agent's model reads: its name, description and input schema."""
    keep = {k: tool[k] for k in ("name", "title", "description", "inputSchema") if k in tool}
    return json.dumps(keep, ensure_ascii=False)


def first_look_html(s: Session, *, open_: bool) -> str:
    """The handshake and the tool list: the readable rendering, and beside it the bytes as received (two_html's
    layout), under a head that says how much of the agent's context they take before its first call."""
    result = s.hello.result or {}
    words = str(result.get("instructions") or "")
    info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
    taken = words + "".join(_tool_text(t) for t in s.tools)
    head = (f"<summary class='sec'>agent 先看到的<span class='tr-figs'>"
            f"<span class='num' data-tip='開場說明（instructions）的字數'>開場說明 {len(words):,} 字</span>"
            f"<span class='num'>工具 {len(s.tools)} 個</span>"
            f"<span class='num' data-tip-h='開工前就讀進去的' data-tip='開場說明，加上每個工具的名字、說明和參數。"
            f"{e(TOKEN_TIP)}'>約 {approx_tokens(taken):,} token</span></span></summary>")
    raws = raw_block(s.hello.raw, f"initialize　{_seconds(s.hello.ms)}")
    if s.listing is not None:
        raws += raw_block(s.listing.raw, f"tools/list　{_seconds(s.listing.ms)}")
    raw = f"<aside class='pane tr-raw'><header>原文</header><div class='tr-scroll'>{raws}</div></aside>"
    server = " ".join(x for x in (str(info.get("name") or ""), str(info.get("version") or "")) if x)
    proto = str(result.get("protocolVersion") or "")
    top = (f"<div class='tr-srv'><b class='mono'>{e(server or '（沒有名字）')}</b>"
           + (f"<span class='tag' data-tip='MCP 協定版本'>{e(proto)}</span>" if proto else "") + "</div>")
    said = (f"<h3 class='tr-h'>開場說明{counts_html(words)}</h3><div class='tr-words'>{e(words)}</div>" if words
            else "<h3 class='tr-h'>開場說明</h3><p class='faint small'>服務沒有給開場說明</p>")
    if s.listing is not None and not s.listing.ok:
        tools = f"<p class='tr-bad'><span class='pill bad'>{icon('no', 12)}拿不到工具清單</span>{e(s.listing.error)}</p>"
    else:
        tools = "".join(tool_card(t) for t in s.tools) or "<p class='faint small'>服務沒有列出工具</p>"
    laid = f"<div class='tr-main tr-read'>{top}{said}<h3 class='tr-h'>工具</h3>{tools}</div>"
    return (f"<details class='tr-first' id='first'{' open' if open_ else ''}>{head}"
            f"<div class='tr-two'>{laid}{raw}</div></details>")


def tool_card(tool: dict) -> str:
    """One tool as the agent reads it: name, description, parameters."""
    desc = str(tool.get("description") or "")
    title = str(tool.get("title") or "")
    rows = "".join(
        f"<tr><td class='mono'>{e(f.name)}{REQUIRED if f.required else ''}</td>"
        f"<td>{e(type_words(f))}</td><td class='mono tr-def'>{e(_shown_default(f))}</td><td>{e(f.description)}</td></tr>"
        for f in fields(tool.get("inputSchema")))
    table = (f"<table class='grid tr-params'><tr><th>參數</th><th>類型</th><th>預設</th><th>說明</th></tr>{rows}</table>"
             if rows else "<p class='faint small'>沒有參數</p>")
    ro = ("<span class='tag' data-tip='工具清單標成只讀（readOnlyHint）'>只讀</span>" if read_only(tool)
          else "<span class='pill none' data-tip='工具清單沒有標成只讀；這一頁不呼叫它'>沒標只讀</span>")
    return (f"<article class='tr-tool'><header><b class='mono'>{e(tool.get('name'))}</b>"
            + (f"<span class='faint'>{e(title)}</span>" if title and title != tool.get("name") else "")
            + f"{ro}{counts_html(desc)}</header><div class='tr-words'>{e(desc)}</div>{table}</article>")


# ---------------------------------------------------------------- one answer, laid out

def _text_of(result: dict) -> str:
    """What the agent's model reads of a tools/call result: its content's text, in order; a part that is no text is
    named in its place."""
    parts = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        else:
            parts.append(f"[{item.get('type', '?')} {item.get('mimeType', '')}]".replace(" ]", "]"))
    return "\n\n".join(parts)


def reader_key(tool: dict) -> str:
    """The one required parameter of a tool that reads one thing (a read-only tool with exactly one required
    parameter), '' for any other."""
    req = [f.name for f in fields(tool.get("inputSchema")) if f.required]
    return req[0] if read_only(tool) and len(req) == 1 else ""


def follow_up(tools: list[dict], keys: set[str]) -> tuple[str, str] | None:
    """The tool that reads one result again: a read-only tool with exactly one required parameter, named as a key the
    results carry (ID_KEYS first: id before url; then any other key). Found in the schemas, never by the tool's name."""
    for key in (*ID_KEYS, *sorted(keys - set(ID_KEYS))):
        if key not in keys:
            continue
        for t in tools:
            if reader_key(t) == key:
                return str(t["name"]), key
    return None


def reader_choices(tool: dict | None) -> list[tuple[str, str, str, str]]:
    """What a result card offers besides reading the whole: for each optional parameter of the reading tool, every
    lowercase literal its description names (「填 toc 只看目錄」 → toc), as (parameter, value, label, the clause): the
    label is 目錄 when the clause says so, else the value itself 【Claude】."""
    out = []
    for f in fields((tool or {}).get("inputSchema")):
        if f.required or f.control not in ("text",):
            continue
        for clause in re.split(r"[；;。]", f.description):
            for value in re.findall(r"(?<![A-Za-z])([a-z][a-z_]{1,15})(?![A-Za-z])", clause):
                out.append((f.name, value, "目錄" if "目錄" in clause else value, clause.strip()))
    return list(dict.fromkeys(out))


def read_href(tool: str, key: str, value: str, extra: tuple[str, str] | None = None) -> str:
    args = {CALL: tool, ARG + key: value}
    if extra:
        args[ARG + extra[0]] = extra[1]
    return "/try?" + urlencode(args) + "#result"


def _kind(parts: list[str]) -> tuple[str, int, str] | None:
    """(kind, its colour's number, its word) of an identity's part that names one."""
    for p in parts:
        for word, kind, k in KIND_WORDS:
            if p.startswith(word):
                return kind, k, p
    return None


def _short_url(url: str) -> str:
    parts = urlsplit(url)
    return (parts.netloc + parts.path) if parts.netloc else url


def _snip(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= SNIP else one[:SNIP] + "…"


def _val(v: object) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def meta_tags(r: dict) -> list[str]:
    """The small marks under a title: background, the identity's parts (the kind with its icon in its kind's hue), the
    first date (all of them float over it), how many copies."""
    ident = [p for p in str(r.get("identity") or "").split("｜") if p]
    kind = _kind(ident)
    tags = ["<span class='tag' data-tip='背景資料（會議紀錄、學報、考題等）'>背景</span>"] if r.get("background") else []
    dates = [d for d in r.get("dates") or [] if isinstance(d, dict)]
    first = str(dates[0].get("date") or "") if dates else ""
    tip = "\n".join(f"{d.get('type', '')} {d.get('date', '')}（{d.get('source', '')}）" for d in dates)
    said = False  # the first date already in the identity: its every date floats over that part, no second mark
    for p in ident:
        if kind and p == kind[2]:
            tags.append(f"<span class='tr-kind tr-k{kind[1]}'>{icon(KIND_ICON[kind[0]], 13)}{e(p)}</span>")
        elif first and not said and first in p:
            said = True
            tags.append(f"<span class='num' data-tip-h='日期' data-tip='{e(tip)}'>{icon('event', 12)}{e(p)}</span>")
        else:
            tags.append(f"<span>{e(p)}</span>")
    if first and not said:
        tags.append(f"<span class='num' data-tip-h='日期' data-tip='{e(tip)}'>{icon('event', 12)}{e(first)}</span>")
    copies = [c for c in r.get("copies") or [] if isinstance(c, dict)]
    if copies:
        tip = "\n".join(_short_url(str(c.get("url") or c.get("id") or "")) for c in copies[:6])
        tags.append(f"<span data-tip-h='同一份文件在別的網址' data-tip='{e(tip)}'>副本 {len(copies)}</span>")
    return tags


def link_html(url: str) -> str:
    """An address: a link to the school's page in a new window (the full address floats over it), or plain text."""
    if url.startswith(("http://", "https://")):
        return (f"<a class='tr-url' href='{e(url)}' target='_blank' rel='noopener noreferrer' data-tip='{e(url)}'>"
                f"<span>{e(_short_url(url))}</span>{icon('out', 11)}</a>")
    return f"<div class='tr-url'><span>{e(url)}</span></div>" if url else ""


def card_html(r: dict, n: int, *, query: str, follow: tuple[str, str] | None,
              choices: list[tuple[str, str, str, str]] = ()) -> str:
    """One result: its place, title, unit, kind, date, address and a snippet of its text, then its whole text and
    the two ways on (read it again; why it ranked here)."""
    rank = r.get("rank") if isinstance(r.get("rank"), int) else n
    title = str(r.get("title") or r.get("name") or r.get("id") or "（沒有標題）")
    tags = meta_tags(r)
    status = f"<div class='tr-status'>{e(r['status'])}</div>" if r.get("status") else ""
    link = link_html(str(r.get("url") or ""))
    parent = r.get("parent") if isinstance(r.get("parent"), dict) else None
    under = (f"<div class='tr-under'>所屬：{e(parent.get('title') or parent.get('id') or '')}</div>"
             if parent and (parent.get("title") or parent.get("id")) else "")
    body = next((str(r[k]) for k in BODY_KEYS if isinstance(r.get(k), str) and r[k]), "")
    whole = (str(r["header"]) + "\n…\n" + body) if isinstance(r.get("header"), str) and r["header"] else body
    snip = f"<p class='tr-snip'>{e(_snip(whole))}</p>" if whole else ""
    more_words = f"<div class='tr-under'>{e(r['more'])}</div>" if r.get("more") else ""
    if whole and len(" ".join(whole.split())) > SNIP:  # the snippet is cut: the whole text one click away
        named = str(r.get("content_type") or "原文")
        full = (f"<details class='tr-full'><summary>{e(named)} {len(whole):,} 字</summary>"
                f"<div class='tr-text'>{e(whole)}</div>{more_words}</details>")
    else:
        full = more_words
    acts = []
    if follow and r.get(follow[1]):
        key = str(r[follow[1]])
        acts.append(f"<a class='btn' href='{e(read_href(follow[0], follow[1], key))}' "
                    f"data-tip='等於 agent 接著呼叫 {e(follow[0])}（{e(follow[1])}={e(key)}）'>{icon('page', 13)}讀這一份</a>")
        for param, value, label, clause in choices:
            acts.append(f"<a class='btn' href='{e(read_href(follow[0], follow[1], key, (param, value)))}' "
                        f"data-tip='{e(clause)}（{e(param)}={e(value)}）'>{e(label)}</a>")
    if query and r.get("id"):
        href = why_url(q=query, docs=[str(r["id"])], anchor=f"r-{r['id']}")
        acts.append(f"<a class='btn' href='{e(href)}' data-tip='「為什麼這樣排」：這一份的名次和分數怎麼算出來的'>"
                    f"{icon('search', 13)}為什麼排在這裡</a>")
    extra = {k: v for k, v in r.items() if k not in SHOWN}
    more = ("<div class='tr-extra'>" + "".join(f"<span><b class='mono'>{e(k)}</b> {e(_snip(_val(v)))}</span>"
                                                for k, v in extra.items()) + "</div>") if extra else ""
    return (f"<article class='tr-card'><header><span class='tr-rk num'>{e(rank)}</span>"
            f"<h4 class='tr-ttl'>{e(title)}</h4></header><div class='tr-meta'>{''.join(tags)}</div>{status}{link}"
            f"{under}{snip}{full}{more}"
            + (f"<div class='tr-acts'>{''.join(acts)}</div>" if acts else "") + "</article>")


def _fold(name: str, inner: str, tip: str = "") -> str:
    t = f" data-tip='{e(tip)}'" if tip else ""
    return f"<details class='fold tr-fold'><summary{t}>{e(name)}</summary><div class='tr-fold-in'>{inner}</div></details>"


def answer_html(answer: dict, *, query: str, follow_tools: list[dict],
                reading: tuple[dict, str, str] | None = None) -> str:
    """An answer that is JSON, laid out: a search's head and its results as cards; else one document (reading: the
    tool that read it, doc_html)."""
    results = answer.get("results")
    if isinstance(results, list):
        cards = [r for r in results if isinstance(r, dict)]
        keys = set().union(*(r.keys() for r in cards)) if cards else set()
        follow = follow_up(follow_tools, keys)
        choices = reader_choices(next((t for t in follow_tools if follow and t.get("name") == follow[0]), None))
        head = []
        if answer.get("found") is False or not cards:
            head.append(f"<span class='pill none'>{icon('dash', 12)}查無資料</span>")
        else:
            head.append(f"<span class='num tr-big'>{len(cards)} 筆</span>")
        understood = answer.get("understood") if isinstance(answer.get("understood"), dict) else {}
        for k, v in understood.items():
            words = "、".join(str(x) for x in v) if isinstance(v, list) else str(v)
            head.append(f"<span class='tag' data-tip='服務怎麼讀這個問題'>{e(UNDERSTOOD.get(k, k))}：{e(words)}</span>")
        applied = answer.get("applied") if isinstance(answer.get("applied"), dict) else {}
        for k, v in applied.items():
            head.append(f"<span class='tag mono' data-tip='這次用的參數（不是預設的）'>{e(k)}={e(_val(v))}</span>")
        folds = _side_folds(answer, skip={"results", "understood", "applied", "found", "query"})
        body = "".join(card_html(r, i, query=query, follow=follow, choices=choices) for i, r in enumerate(cards, 1))
        return f"<div class='tr-ahead'>{''.join(head)}</div>{folds}{body}"
    return doc_html(answer, reading=reading)


def _side_folds(answer: dict, *, skip: set[str]) -> str:
    """The parts of an answer around its results: today, coverage, notes, and any other key."""
    out = []
    today = answer.get("today")
    if isinstance(today, dict):
        out.append(f"<span class='tr-today num'>今天 {e(today.get('roc_date') or today.get('date') or '')} "
                   f"{e(today.get('weekday') or '')} {e(today.get('term') or '')}</span>")
    folds = []
    if isinstance(answer.get("coverage"), str):
        folds.append(_fold("收錄範圍", f"<div class='tr-words'>{e(answer['coverage'])}</div>"))
    notes = answer.get("notes")
    if isinstance(notes, list) and notes:
        folds.append(_fold(f"附註 {len(notes)}", "".join(f"<p class='tr-note'>{e(n)}</p>" for n in notes)))
    other = {k: v for k, v in answer.items() if k not in skip | {"today", "coverage", "notes"}}
    if other:
        folds.append(_fold(f"其他欄位 {len(other)}", "".join(
            f"<div class='tr-kv'><b class='mono'>{e(k)}</b><span>{e(_val(v))}</span></div>" for k, v in other.items())))
    return f"<div class='tr-around'>{''.join(out)}{''.join(folds)}</div>" if out or folds else ""


def doc_html(doc: dict, *, reading: tuple[dict, str, str] | None = None) -> str:
    """One document (a fetch-like answer): who it is, its whole text, its table of contents (each entry that names a
    value of an optional parameter of the tool that read it a link reading that part: reading is (that tool, its
    required parameter, the value it was called with)), the rest."""
    if doc.get("found") is False:
        top = f"<span class='pill none'>{icon('dash', 12)}找不到這一份</span>"
    else:
        top = ""
    shown = {"title", "identity", "url", "dates", "status", "content_type", "found", "toc", "more", "copies",
             "background", *BODY_KEYS}
    title = f"<h4 class='tr-ttl tr-doc-t'>{e(doc['title'])}</h4>" if doc.get("title") else ""
    ident = "".join(meta_tags(doc))
    link = link_html(str(doc.get("url") or ""))
    status = f"<div class='tr-status'>{e(doc['status'])}</div>" if doc.get("status") else ""
    body = next((str(doc[k]) for k in BODY_KEYS if isinstance(doc.get(k), str) and doc[k]), "")
    text = (f"<div class='tr-cap'><span>{e(doc.get('content_type') or '原文')} {len(body):,} 字</span></div>"
            f"<div class='tr-text tr-doc'>{e(body)}</div>") if body else ""
    more = f"<div class='tr-under'>{e(doc['more'])}</div>" if doc.get("more") else ""
    toc = doc.get("toc")
    toc_html = ""
    if isinstance(toc, list) and toc:
        optional = ({f.name for f in fields(reading[0].get("inputSchema")) if not f.required} if reading else set())
        items = []
        for x in toc:
            words = _val(x) if not isinstance(x, dict) else str(x.get("heading") or x.get("title") or _val(x))
            part = next((k for k in (x if isinstance(x, dict) else {}) if k in optional), None)
            if reading and part:
                href = read_href(str(reading[0]["name"]), reading[1], reading[2], (part, str(x[part])))
                items.append(f"<li><a href='{e(href)}' data-tip='讀這一節（{e(part)}={e(x[part])}）'>{e(words)}</a></li>")
            else:
                items.append(f"<li>{e(words)}</li>")
        toc_html = _fold(f"目錄 {len(toc)}", "<ol class='tr-toc'>" + "".join(items) + "</ol>")
        if reading:
            toc_html = toc_html.replace("<details class='fold tr-fold'>", "<details class='fold tr-fold' open>", 1)
    elif isinstance(toc, str) and toc:
        toc_html = _fold("目錄", f"<div class='tr-words'>{e(toc)}</div>")
    folds = _side_folds(doc, skip=shown)
    return (f"<div class='tr-ahead'>{top}</div>{title}<div class='tr-meta'>{ident}</div>{status}{link}{folds}"
            f"{text}{more}{toc_html}")


def why_href(query: str) -> str:
    """Where 「為什麼這樣排」 goes for a question: this page's second tab on it."""
    return why_url(q=query, anchor="result")


def agent_href(tool: dict | None, query: str) -> str:
    """Where 「agent 收到的」 goes for a question: the search tool called with it ('' without a search tool)."""
    asked = query_field(tool)
    if tool is None or not asked or not query:
        return ""
    return "/try?" + urlencode({CALL: str(tool["name"]), ARG + asked: query}) + "#result"


def result_tabs(query: str, *, current: str = "agent", tool: dict | None = None) -> str:
    """The result's two tabs: what the agent received (the tool called through the service) and why the search ranked
    as it did (search_page's explanation of the same question, drawn here: /try?tab=why…); current is the one shown
    ('agent' or 'why'), tool the search tool the first tab calls."""
    if current == "why":
        href = agent_href(tool, query)
        agent = (f"<a href='{e(href)}' data-tip='同一個問題，經搜尋服務送出，agent 實際收到的'>agent 收到的</a>" if href
                 else "<a aria-disabled='true' data-tip='搜尋服務沒有回應，或沒有搜尋的工具'>agent 收到的</a>")
        return (f"<nav class='tr-tabs' aria-label='結果'>{agent}"
                "<a aria-current='page' data-seam='why'>為什麼這樣排</a></nav>")
    if query:
        why = (f"<a href='{e(why_href(query))}' data-seam='why' data-tip='同一個問題，每一份的名次和分數怎麼算出來的'>"
               "為什麼這樣排</a>")
    else:
        why = "<a aria-disabled='true' data-seam='why' data-tip='這一次不是搜尋，沒有排名'>為什麼這樣排</a>"
    return f"<nav class='tr-tabs' aria-label='結果'><a aria-current='page'>agent 收到的</a>{why}</nav>"


def why_html(query: str, inner: str, tool: dict | None) -> str:
    """The second tab shown: its tab bar and the explanation (inner: drawn by nknu.ops.web from search_page)."""
    return f"<section id='result' class='tr-result tr-why'>{result_tabs(query, current='why', tool=tool)}{inner}</section>"


def result_html(tool: dict, args: dict, got: Exchange, tools: list[dict]) -> str:
    """One call: what was sent, then what came back: laid out, the page's main column, and beside it the text as
    received in a narrower box that stays in view and scrolls by itself (2026-11-18: the two were halves of the
    page, the raw text a wall as wide as the results). The question 「為什麼
    這樣排」 asks 搜尋測試 about is the tool's question parameter, and only for an answer that ranks results."""
    sent = json.dumps({"name": tool.get("name"), "arguments": args}, ensure_ascii=False)
    result = got.result
    text = _text_of(result) if result is not None else ""
    try:
        parsed = json.loads(text) if text else None
    except ValueError:
        parsed = None
    asked = args.get(query_field(tool)) if query_field(tool) else None
    ranked = isinstance(parsed, dict) and isinstance(parsed.get("results"), list)
    query = asked if ranked and isinstance(asked, str) else ""
    if result is None:
        state = f"<span class='pill bad'>{icon('no', 12)}沒拿到結果</span><span>{e(got.error)}</span>"
    elif result.get("isError"):
        state = (f"<span class='pill bad' data-tip='工具回了 isError：agent 收到的是一句錯誤'>{icon('warn', 12)}"
                 "工具回報錯誤</span>")
    else:
        state = f"<span class='pill good'>{icon('ok', 12)}有結果</span>"
    # how much of the agent's context the answer takes, with what was sent: the number the page is opened for, at the
    # top at every width (the text itself is beside the results, under them on a narrow screen)
    line = (f"<div class='tr-sent'><span class='mono faint'>tools/call</span><code>{e(sent)}</code>{state}"
            f"{counts_html(text if result is not None else got.raw, got.ms)}</div>")
    if result is None:
        raw = ("<aside class='pane tr-raw'><header>原文</header><div class='tr-scroll'>"
               + (raw_block(got.raw) if got.raw else "<p class='faint small tr-none'>沒有回應內容</p>")
               + "</div></aside>")
        laid = f"<div class='tr-main tr-read'><p class='tr-plain'>{e(got.error)}</p></div>"
        return f"<section id='result' class='tr-result'>{result_tabs(query)}{line}<div class='tr-two'>{laid}{raw}</div></section>"
    envelope = _fold("整個回應（JSON-RPC）", raw_block(got.raw), "傳輸層收到的整段，agent 的模型讀的是上面的文字")
    raw = (f"<aside class='pane tr-raw'><header data-tip='agent 的模型讀到的就是這段文字，一字不差'>原文</header>"
           f"<div class='tr-scroll'>{raw_block(text)}{envelope}</div></aside>")
    if isinstance(parsed, dict):
        key = reader_key(tool)
        reading = (tool, key, str(args.get(key))) if key and args.get(key) is not None else None
        laid = answer_html(parsed, query=query, follow_tools=tools, reading=reading)
    else:
        laid = f"<p class='tr-plain'>{e(text)}</p>" if text else "<p class='faint small'>沒有文字</p>"
    if isinstance(result.get("structuredContent"), (dict, list)):
        laid += _fold("structuredContent", raw_block(json.dumps(result["structuredContent"], ensure_ascii=False)))
    return (f"<section id='result' class='tr-result'>{result_tabs(query)}{line}"
            f"<div class='tr-two'><div class='tr-main tr-read'>{laid}</div>{raw}</div></section>")


# ---------------------------------------------------------------- the page

class TryPage:
    """The page over one service address ('' : not set). One call at a time across every request (busy)."""

    def __init__(self, url: str, client: McpClient | None = None) -> None:
        self.url = url.strip()
        self.client = client or (McpClient(self.url) if self.url else None)
        self.busy = threading.Lock()

    def page(self, params: object, *, why: tuple[str, str] | None = None, board: str = "") -> tuple[str, str]:
        """(the page's title, its body) for the request's parameters. why: (the question, its explanation) when the
        second tab is asked for (nknu.ops.web draws it from search_page; no tool is called then); board: the
        scoreboard, shown under the forms when nothing is asked."""
        body = f"<style>{CSS}</style>"
        tail = f"<script>{JS.replace('WAIT_WORDS', json.dumps(WAIT_WORDS))}</script>"
        if self.client is None:
            state = ("<section class='card pad tr-state'><span class='pill none'>沒有設定</span>"
                     "<span data-tip='啟動檢視台時加 --mcp-url（實驗室是 http://mcp:8766/mcp）'>"
                     "檢視台沒有設定搜尋服務的位址</span></section>")
            return self._without_service(body + state, why, board)
        s = self.client.open()
        if not s.hello.ok:
            raw = raw_block(s.hello.raw, "initialize") if s.hello.raw else ""
            state = (f"<section class='card pad tr-state'><span class='pill bad'>{icon('no', 12)}連不上"
                     f"</span><span>搜尋服務：{e(s.hello.error)}</span>"
                     f"<span class='faint mono small'>{e(self.url)}</span>{raw}</section>")
            return self._without_service(body + state, why, board)
        by_name = {str(t["name"]): t for t in s.tools}
        asker = search_tool(s.tools)
        others = [t for t in s.tools if t is not asker and not reader_key(t)]
        called = (_all(params, CALL) or [""])[-1] if why is None else ""
        result, values, title, filled = "", {}, "搜尋", called
        if why is not None:  # the second tab: the question in its tool's box, the explanation under it
            query, inner = why
            result = why_html(query, inner, asker)
            if asker is not None:
                values, filled = {query_field(asker): query}, str(asker["name"])
            title = f"{query} · 搜尋" if query else "搜尋"
        elif called:
            tool = by_name.get(called)
            if tool is None:
                result = (f"<section id='result' class='tr-result card pad tr-state'><span class='pill none'>沒有這個工具"
                          f"</span><span class='mono'>{e(called)}</span></section>")
            elif not read_only(tool):
                result = (f"<section id='result' class='tr-result card pad tr-state'><span class='pill none'>不呼叫"
                          f"</span><span>{e(called)} 沒有標成只讀</span></section>")
            else:
                args = arguments(tool.get("inputSchema"), params)
                values = {k[len(ARG):]: v for k in _keys(params) if k.startswith(ARG) for v in _all(params, k)[-1:]}
                if self.busy.acquire(blocking=False):
                    try:
                        got = self.client.call(called, args, session=s.session)
                    finally:
                        self.busy.release()
                    result = result_html(tool, args, got, s.tools)
                    asked = args.get(query_field(tool)) if query_field(tool) else None
                    title = f"{asked} · 搜尋" if isinstance(asked, str) and asked else f"{called} · 搜尋"
                else:
                    result = (f"<section id='result' class='tr-result card pad tr-state'><span class='pill warn'>"
                              f"{icon('warn', 12)}正在查</span><span>另一個查詢還沒查完，查完再送一次</span></section>")
        ask = (search_form_html(asker, values if filled == asker["name"] else {}, instructions_of(s))
               if asker is not None else "<section class='card pad tr-state'><span class='pill none'>沒有搜尋的工具"
               "</span><span>工具清單裡沒有一個只讀、要一段文字的工具</span></section>")
        forms = "".join(form_html(t, values if t["name"] == filled else {}, shown=True) for t in others)
        call = (f"<details class='fold tr-others' id='call'{' open' if filled in {t['name'] for t in others} else ''}>"
                f"<summary class='sec'>其他工具 {len(others)}</summary>{forms}</details>" if others else "")
        idle = "" if called or why is not None else board
        return title, (body + ask + result + call + first_look_html(s, open_=False) + idle + tail)

    @staticmethod
    def _without_service(top: str, why: tuple[str, str] | None, board: str) -> tuple[str, str]:
        """The page when the service cannot be asked: what stands in the way, a plain question box for the second tab
        (no tool's form to put the question in), then that tab or the scoreboard (both are drawn from the library, not
        from the service)."""
        query = why[0] if why is not None else ""
        ask = (f"<form class='ask tr-alone' action='/try' method='get'><input type='hidden' name='tab' value='why'>"
               f"<div class='box'><input name='q' value='{e(query)}' "
               f"placeholder='問一個學生會問的問題，看搜尋怎麼排' aria-label='問題'>{go_button('搜尋')}</div></form>")
        if why is not None:
            return (f"{query} · 搜尋" if query else "搜尋"), top + ask + why_html(query, why[1], None)
        return "搜尋", top + ask + board


def _keys(params: object) -> list[str]:
    if hasattr(params, "keys"):
        return list(dict.fromkeys(params.keys()))
    return []


# ---------------------------------------------------------------- the look and the script (tr- only)

CSS = """
.tr-first{margin:12px 0 0;border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:9px 14px;box-shadow:var(--shadow)}
.tr-first>summary{cursor:pointer;list-style:none;margin:0;font-size:13px;font-weight:650}
.tr-first>summary::-webkit-details-marker{display:none}
.tr-first>summary::before{content:"›";display:inline-block;transition:transform .15s;color:var(--ink3);margin-right:2px}
.tr-first[open]>summary::before{transform:rotate(90deg)}
.tr-first[open]>.tr-two{margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.tr-figs{display:inline-flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;font-weight:400;color:var(--ink3);margin-left:auto}
.tr-two{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);gap:18px;align-items:start}
.tr-main{min-width:0}
.tr-two .pane{background:var(--surface);border-radius:12px}
.tr-two .pane>header{flex-wrap:wrap}
.tr-raw{position:sticky;top:calc(var(--nk-bar-h,0px) + 12px)}
.tr-count{display:inline-flex;flex-wrap:wrap;gap:2px 12px}
.tr-sent .tr-count{margin-left:0;color:var(--ink3)}
.tr-scroll{max-height:calc(100vh - 72px);overflow:auto;padding:12px 14px;min-width:0}
@media (max-width:900px){.tr-two{grid-template-columns:minmax(0,1fr)}.tr-raw{position:static}
.tr-scroll{max-height:50vh}}
.tr-rawb+.tr-rawb{margin-top:14px}
.tr-cap{display:flex;align-items:center;gap:10px;margin:0 0 6px;font-size:12px;color:var(--ink3)}
.tr-cap .btn{margin-left:auto;padding:2px 10px;font-size:12px}
.tr-rawb .tr-pre{margin:0;padding:0;max-height:none;overflow:visible;white-space:pre-wrap;word-break:break-word;
font:12px/1.6 var(--mono);color:var(--ink)}
.tr-rawb .tr-pre[hidden]{display:none}
.tr-read{font-size:13px}
.tr-srv{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tr-h{display:flex;align-items:center;flex-wrap:wrap;gap:4px 10px;margin:16px 0 6px;font-size:12px;color:var(--ink3);
font-weight:600}.tr-h .side{margin-left:auto;font-weight:400}
.tr-words{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;color:var(--ink)}
.tr-tool{border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:var(--sunk)}.tr-tool+.tr-tool{margin-top:10px}
.tr-tool>header{display:flex;align-items:center;flex-wrap:wrap;gap:4px 10px;margin-bottom:8px}
.tr-tool>header b{font-size:14px}.tr-tool>header .side{margin-left:auto;color:var(--ink3);font-size:12px}
.tr-params td:first-child,.tr-params td.tr-def{white-space:nowrap}.tr-params td{font-size:12px;overflow-wrap:break-word}
.tr-req{margin-left:6px;padding:0 6px;border-radius:6px;background:var(--hover);color:var(--ink);font:650 11px/18px var(--font)}

.sr-form{margin:0 0 8px}.sr-form fieldset{border:0;margin:0;padding:0;min-width:0}
.sr-top{display:flex;gap:10px;align-items:stretch}
.sr-box{position:relative;flex:1 1 auto;min-width:0;display:flex}
.sr-q{flex:1 1 auto;min-width:0;width:100%;padding:13px 60px 13px 16px;border:1px solid var(--line2);border-radius:14px;
background:var(--sunk);font-size:16px;transition:background-color .14s,border-color .14s}
.sr-q:focus{background:var(--surface);border-color:var(--act)}
.sr-q:focus,.sr-num:focus,.sr-txt:focus{outline:2px solid var(--act);outline-offset:1px}
.sr-box .go{position:absolute;right:5px;top:5px;bottom:5px;width:46px;display:grid;place-items:center;padding:0;border:0;
border-radius:10px;background:var(--act);color:var(--on-act);cursor:pointer}.sr-box .go:hover{opacity:.88}
.sr-box .go[disabled]{opacity:.5;cursor:default}
.sr-opts{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px 22px;margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.sr-p{display:flex;flex-direction:column;gap:5px;min-width:0}
.sr-seg{display:inline-flex;flex-wrap:wrap;gap:2px;padding:3px;border:1px solid var(--line);border-radius:10px;
background:var(--sunk)}
.sr-seg label{position:relative;cursor:pointer}
.sr-seg input{position:absolute;opacity:0;pointer-events:none}
.sr-seg span{display:block;padding:4px 11px;border-radius:7px;font:12px/1.4 var(--mono);color:var(--ink2);transition:background-color .14s,color .14s}
.sr-seg label:hover span{color:var(--ink)}
.sr-seg input:checked+span{background:var(--surface);color:var(--ink);font-weight:700;box-shadow:0 0 0 1px var(--line2),var(--shadow)}
.sr-seg input:focus-visible+span{outline:2px solid var(--act);outline-offset:1px}
.sr-seg i{font-style:normal;font-size:11px;margin-left:5px;color:var(--ink3)}
.sr-num{width:86px;padding:6px 10px;border:1px solid var(--line2);border-radius:9px;background:var(--sunk);
font-size:13px;font-variant-numeric:tabular-nums}
.sr-txt{width:220px;max-width:100%;padding:6px 10px;border:1px solid var(--line2);border-radius:9px;
background:var(--sunk);font-size:13px}
.tr-others{margin-top:14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:9px 14px}
.tr-others>summary{cursor:pointer;list-style:none;margin:0;font-size:13px}
@media (max-width:640px){.sr-txt{width:100%}}
.tr-call{margin-top:8px;scroll-margin-top:64px}
.tr-form{margin-top:12px}.tr-form[hidden]{display:none}
.tr-form fieldset{border:0;margin:0;padding:0;min-width:0}
.tr-fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px 14px;align-items:end}
.tr-f{display:flex;flex-direction:column;gap:5px;min-width:0}.tr-f.wide{grid-column:1/-1}
.tr-k{display:inline-flex;align-items:baseline;gap:6px;font-size:12px;line-height:1.4;color:var(--ink2);cursor:help}
.tr-k code{font:11px/1.4 var(--mono);color:var(--ink3)}.tr-k .tr-g{font-weight:600;color:var(--ink)}
.tr-f input[type=text],.tr-f input[type=number],.tr-f select,.tr-f textarea{width:100%;min-width:0;padding:9px 12px;
border:1px solid var(--line2);border-radius:10px;background:var(--surface);font-size:14px}
.tr-f textarea{font-family:var(--mono);font-size:13px;resize:vertical}
.tr-f.wide input[type=text]{padding:11px 14px;font-size:16px;border-radius:12px}
.tr-f input:focus,.tr-f select:focus,.tr-f textarea:focus{outline:2px solid var(--act);outline-offset:1px}
.tr-f.check{flex-direction:row;align-items:center;gap:8px;min-height:40px}
.tr-go{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;margin-top:14px}
.tr-go button{padding:0 26px;min-height:44px;border:0;border-radius:12px;background:var(--act);color:var(--on-act);
font-weight:650;cursor:pointer}.tr-go button:hover{opacity:.88}.tr-go button[disabled]{opacity:.5;cursor:default}
fieldset[disabled] .tr-go button{opacity:.4;cursor:not-allowed}
.tr-wait{font-size:13px;color:var(--ink2)}.tr-wait[hidden]{display:none}
.tr-wait b{color:var(--ink);font-variant-numeric:tabular-nums}
.tr-none{margin:0}

.tr-result{margin-top:10px;scroll-margin-top:64px}
.tr-tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:20px 0 14px}
.tr-tabs a{position:relative;padding:8px 16px;text-decoration:none;color:var(--ink2);font-weight:650;font-size:13px;border-radius:8px 8px 0 0}
.tr-tabs a[href]:hover{color:var(--ink);background:var(--hover)}
.tr-tabs a[aria-current]{color:var(--ink)}
.tr-tabs a[aria-current]::after{content:"";position:absolute;inset:auto 0 -1px;border-bottom:2px solid var(--act)}
.tr-tabs a[aria-disabled]{opacity:.45}
.tr-sent{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin:0 0 12px;padding:8px 12px;border-radius:10px;
background:var(--sunk);border:1px solid var(--line);font-size:12px;min-width:0}
.tr-sent code{min-width:0;padding:2px 8px;border-radius:6px;background:var(--surface);border:1px solid var(--line);
font:12px/1.5 var(--mono);overflow-wrap:anywhere}
.tr-state{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin-top:14px}
form.ask.tr-alone{margin-top:14px}
.tr-state .tr-rawb{flex-basis:100%}
.tr-plain{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65}
.tr-bad{display:flex;flex-wrap:wrap;gap:8px;align-items:center}

.tr-ahead{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
.tr-big{font-size:16px;font-weight:700}
.tr-around{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;margin:8px 0 12px}
.tr-around .tr-fold{margin-top:0}.tr-around .tr-fold[open]{flex-basis:100%}
.tr-today{font-size:12px;color:var(--ink3)}
.tr-fold-in{margin-top:8px;padding:10px 12px;border-radius:8px;background:var(--sunk);font-size:13px}
.tr-note{margin:0 0 6px;line-height:1.6}.tr-note:last-child{margin:0}
.tr-kv{display:grid;grid-template-columns:minmax(80px,auto) minmax(0,1fr);gap:4px 12px;font-size:12px}
.tr-kv span{overflow-wrap:anywhere}
.tr-card{border:1px solid var(--line);border-radius:14px;padding:14px 16px;background:var(--surface);box-shadow:var(--shadow)}
.tr-card+.tr-card{margin-top:10px}
.tr-card>header{display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px;align-items:start}
.tr-rk{display:grid;place-items:center;min-width:28px;height:24px;padding:0 6px;border-radius:7px;background:var(--sunk);
border:1px solid var(--line);font-size:12px;font-weight:700}
.tr-ttl{margin:0;font-size:14px;font-weight:650;line-height:1.45;overflow-wrap:anywhere}
.tr-doc-t{font-size:16px;margin-top:4px}
.tr-meta{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;margin-top:6px;font-size:12px;color:var(--ink2)}
.tr-meta>span{display:inline-flex;align-items:center;gap:4px}
.tr-kind{color:var(--ink)}.tr-k0 .ic{color:var(--k0)}.tr-k1 .ic{color:var(--k1)}.tr-k2 .ic{color:var(--k2)}
.tr-k3 .ic{color:var(--k3)}.tr-k4 .ic{color:var(--ink3)}
.tr-status{margin-top:4px;font-size:12px;color:var(--ink2)}
.tr-url{display:flex;align-items:center;gap:5px;min-width:0;max-width:100%;margin-top:6px;font-size:12px;
color:var(--ink3);text-decoration:none}.tr-url span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
a.tr-url:hover{color:var(--ink);text-decoration:underline}
.tr-under{margin-top:4px;font-size:12px;color:var(--ink3);overflow-wrap:anywhere}
.tr-snip{margin:8px 0 0;line-height:1.6;color:var(--ink);overflow-wrap:anywhere}
.tr-full{margin-top:6px}.tr-full>summary{cursor:pointer;font-size:12px;color:var(--ink2);list-style:none}
.tr-full>summary::-webkit-details-marker{display:none}.tr-full>summary::before{content:"› ";color:var(--ink3)}
.tr-full[open]>summary::before{content:"⌄ "}.tr-full>summary:hover{color:var(--ink)}
.tr-text{margin-top:6px;padding:10px 12px;border-radius:8px;background:var(--sunk);white-space:pre-wrap;
overflow-wrap:anywhere;line-height:1.65;font-size:13px}
.tr-doc{margin-top:0}
.tr-extra{display:flex;flex-wrap:wrap;gap:2px 14px;margin-top:6px;font-size:12px;color:var(--ink3)}
.tr-acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.tr-toc{margin:0;padding-left:20px}
@media (max-width:640px){.tr-figs{gap:2px 12px}
.tr-params tr:first-child{display:none}.tr-params tr{display:grid;grid-template-columns:auto minmax(0,1fr) auto;
gap:2px 10px;padding:6px 0;border-bottom:1px solid var(--line)}.tr-params tr:last-child{border-bottom:0}
table.grid.tr-params td{padding:0;border:0}.tr-params td:last-child{grid-column:1/-1;color:var(--ink2)}}
"""

# The page works without the script (every form submits, every link goes); it only says how long a call has been
# waiting and flips a raw block between as-is and indented.
JS = """
(()=>{
const forms=[...document.querySelectorAll('form.tr-form,form.sr-form')];
let timer=null;
forms.forEach(f=>f.addEventListener('submit',()=>{const b=f.querySelector('button[type=submit]');
const w=f.querySelector('[data-wait]');const t0=Date.now();if(b)b.disabled=true;
if(w){w.hidden=false;const tick=()=>{w.textContent='';const s=document.createElement('b');
s.textContent='查詢中 '+Math.floor((Date.now()-t0)/1000)+' 秒';w.append(s,'　'+WAIT_WORDS)};tick();timer=setInterval(tick,1000)}}));
addEventListener('pageshow',()=>{clearInterval(timer);forms.forEach(f=>{const b=f.querySelector('button[type=submit]');
if(b)b.disabled=false;const w=f.querySelector('[data-wait]');if(w)w.hidden=true})});
document.addEventListener('click',ev=>{const b=ev.target.closest('[data-flip]');if(!b)return;
const box=b.closest('.tr-rawb');const asis=box.querySelector('[data-asis]'),pretty=box.querySelector('[data-pretty]');
if(!asis||!pretty)return;const show=pretty.hidden;pretty.hidden=!show;asis.hidden=show;
b.textContent=show?'原樣':'排成多行'});
})();
"""
