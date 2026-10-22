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
_N = rf"[{_HAN}{_MASKED}]"
# a name is 2-4 characters right before an honorific; a list of names is joined by 、
_HONOR_RE = re.compile(rf"((?:{_N}{{2,4}}、)*)({_N}{{2,4}})({_HONORIFIC})")
# role word + name + verb: 研究生甲乙丙考取 …
_ROLE_RE = re.compile(rf"(研究生|畢業生|大學部|學生|同學|校友|老師|教師|教授|博士生|碩士生|博士班|碩士班|學長|學姐|學姊|系友|所友|選手|隊員)"
                      rf"([{_HAN}]{{2,3}})(?=考取|榮獲|獲得|獲選|榮升|通過|錄取|榮登|入選|當選|獲頒|奪得|勇奪)")
_PREFIX_RE = re.compile(r"^(?:恭賀|恭贺|恭喜|賀|贺|本校|本系|本所|本院|本中心|系上)+")
_UNIT_RE = re.compile(r"^.*?(?:學系|学系|學程|学程|研究所|中心|學院|学院|系|所|班|組|组)")
_COMPOUND_SURNAMES = {"歐陽", "欧阳", "司馬", "司马", "諸葛", "诸葛", "上官", "東方", "东方", "皇甫", "公孫", "公孙", "慕容",
                      "長孫", "长孙", "宇文", "司徒", "夏侯", "令狐", "張簡", "张简", "范姜"}
# words that sit right before an honorific but are not names
_NOT_NAMES = {
    "全體", "全体", "各位", "新生", "畢業", "毕业", "應屆", "应届", "全校", "參賽", "参赛", "優秀", "优秀", "資深", "资深",
    "指導", "指导", "退休", "榮譽", "荣誉", "客座", "專任", "专任", "兼任", "所有", "在校", "學生", "学生", "導師", "导师",
    "獲獎", "获奖", "得獎", "得奖", "傑出", "杰出", "表揚", "表扬", "以上", "下列", "相關", "相关", "團隊", "团队",
    "代表隊", "隊伍", "副", "助理", "全體同", "研究",
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


def _not_a_name(s: str) -> bool:
    return not s or s in _NOT_NAMES or any(s.endswith(w) for w in _NOT_NAMES)


def _split_name(seg: str) -> tuple[str, str] | None:
    """(text kept verbatim, name) for the first segment, whose left edge may include 本系/恭賀/a department name."""
    if _is_masked(seg):
        return None
    rest = _PREFIX_RE.sub("", seg)
    unit = _UNIT_RE.match(rest)
    if unit and 2 <= len(rest) - unit.end() <= 4:
        rest = rest[unit.end():]
    if len(rest) == 4 and rest[:2] not in _COMPOUND_SURNAMES:
        rest = rest[1:]
    if len(rest) < 2 or _not_a_name(rest):
        return None
    return seg[: len(seg) - len(rest)], rest


def mask_honor_names(title: str) -> str:
    def honor(m: re.Match[str]) -> str:
        segs = [x for x in m.group(1).split("、") if x] + [m.group(2)]
        out = []
        for i, seg in enumerate(segs):
            if i == 0:
                split = _split_name(seg)
                out.append(seg if split is None else split[0] + mask_name(split[1]))
            elif 2 <= len(seg) <= 4 and not _is_masked(seg) and not _not_a_name(seg):
                out.append(mask_name(seg))
            else:
                out.append(seg)
        return "、".join(out) + m.group(3)

    def role(m: re.Match[str]) -> str:
        return m.group(1) + (m.group(2) if _not_a_name(m.group(2)) else mask_name(m.group(2)))

    return _ROLE_RE.sub(role, _HONOR_RE.sub(honor, title))


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
    if len(local) >= 20 and local.isalnum():
        return False  # machine-generated ids such as ICS UIDs (…@google.com)
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
            if kind == "national_id" and re.search(r"wikidata|qid", text[max(0, m.start() - 24):m.start()], re.IGNORECASE):
                continue
            hits.append(f"{kind}:{m.group().strip()}")
    return hits


def redact(text: str) -> str:
    """Replace every find_pii hit with [MASKED] (logs, alerts, the found=false query log)."""
    for hit in find_pii(text):
        text = text.replace(hit.split(":", 1)[1], "[MASKED]")
    return text
