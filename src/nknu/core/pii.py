"""Personal-data rules from the design doc §3.

- Announcement text is copied verbatim; text the school already masked stays as it is.
- Only honor-roll titles get names masked, in the school's own style: 陳宥余 -> 陳O余, 陳余 -> 陳O.
- Masking never invents a name; when a span is ambiguous we mask a plausible span rather than leave a name readable.
"""

from __future__ import annotations

import re

_HAN = "一-鿿"
_MASKED = "OＯ○"
_HONORIFIC = r"(?:同學|同学|老師|老师|教授|校友|主任|博士|君)"
_SEG = rf"[{_HAN}{_MASKED}]+"
# a list of name segments separated by 、 right before an honorific
_HONOR_RE = re.compile(rf"({_SEG}(?:、{_SEG})*)({_HONORIFIC})")
_PREFIX_RE = re.compile(r"^(?:恭賀|恭贺|賀|贺|本校|本系|本所|本院|本中心|系上)+")
_UNIT_RE = re.compile(r"^.*?(?:學系|学系|學程|学程|研究所|中心|學院|学院|系|所|班|組|组)")
# words that sit right before an honorific but are not names
_NOT_NAMES = {
    "全體", "全体", "各位", "新生", "畢業", "毕业", "應屆", "应届", "全校", "參賽", "参赛", "優秀", "优秀", "資深", "资深",
    "指導", "指导", "退休", "榮譽", "荣誉", "客座", "專任", "专任", "兼任", "所有", "在校", "學生", "学生", "導師", "导师",
    "獲獎", "获奖", "得獎", "得奖", "傑出", "杰出", "表揚", "表扬", "以上", "下列", "相關", "相关",
}


def mask_name(name: str) -> str:
    """Keep the first and last character, O for everything between; a two-character name keeps only the first."""
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "O"
    return name[0] + "O" * (len(name) - 2) + name[-1]


def _is_masked(seg: str) -> bool:
    return any(c in _MASKED for c in seg)


def _first_name(run: str) -> tuple[str, str] | None:
    """Split the run before the first 、 into (prefix kept verbatim, name). None when no name is there."""
    if _is_masked(run):
        return None
    rest = _PREFIX_RE.sub("", run)
    unit = _UNIT_RE.match(rest)
    if unit and 2 <= len(rest) - unit.end() <= 4:
        rest = rest[unit.end():]
    if not rest or rest in _NOT_NAMES or any(rest.endswith(w) for w in _NOT_NAMES):
        return None
    if len(rest) > 4:
        rest = rest[-3:]  # ambiguous boundary: the most common name length
    if len(rest) < 2:
        return None
    return run[: len(run) - len(rest)], rest


def mask_honor_names(title: str) -> str:
    def repl(m: re.Match[str]) -> str:
        segs = m.group(1).split("、")
        out = []
        for i, seg in enumerate(segs):
            if i == 0:
                split = _first_name(seg)
                out.append(seg if split is None else split[0] + mask_name(split[1]))
            elif 2 <= len(seg) <= 4 and not _is_masked(seg) and seg not in _NOT_NAMES:
                out.append(mask_name(seg))
            else:
                out.append(seg)
        return "、".join(out) + m.group(2)

    return _HONOR_RE.sub(repl, title)


_GENERIC_LEAVE = {"學生", "学生", "教師", "教师", "職員", "职员", "員工", "员工", "同仁", "教職員", "师生", "師生", "線上",
                  "线上", "辦理", "办理", "申請", "申请"}
_PATTERNS = [
    ("mobile", re.compile(r"09\d{2}-?\d{3}-?\d{3}")),
    ("national_id", re.compile(r"\b[A-Z][12]\d{8}\b")),
    ("student_id", re.compile(r"(?<!\d)4\d{8}(?!\d)")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("staff_leave", re.compile(rf"(?:^|[\s,，、。:：;；()（）\[\]])([{_HAN}{_MASKED}]{{2,4}})\s*[，,。.．_]?\s*(?:上午|下午)?\s*"
                               r"(?:請假|休假|公假|事假|病假|出差)")),
]


def _email_is_personal(addr: str) -> bool:
    local, _, domain = addr.lower().partition("@")
    if local in ("noreply", "no-reply") or domain in ("example.com", "example.org"):
        return False
    if domain.endswith("nknu.edu.tw"):
        return local.isdigit()  # student mailboxes are numeric; unit mailboxes are public contacts
    return True


def find_pii(text: str) -> list[str]:
    """Return 'kind:match' strings for personal data that must not be published."""
    hits = []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            if kind == "email" and not _email_is_personal(m.group()):
                continue
            if kind == "staff_leave" and m.group(1) in _GENERIC_LEAVE:
                continue
            hits.append(f"{kind}:{m.group().strip()}")
    return hits
