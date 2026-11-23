# Adapted from RAGFlow rag/nlp/__init__.py @85f3e89 (BULLET_PATTERN, not_bullet, bullets_category, QUESTION_PATTERN)
# and internal/ingestion/component/chunker/title.go @4b520c2, Copyright The InfiniFlow Authors, Apache License 2.0
# (this repository's LICENSE). Modified: Traditional variants (編 節 條 問 個 隻), 壹、 and 款/目 levels, full-width
# digits, 第X條之Y, 問題：, Markdown and list decoration stripped, used for section splitting only.
# Taiwan's law levels (中央法規標準法 §8): 條, then 項 (an unnumbered paragraph: not a heading), then 款 written
# 一、, then 目 written （一）; so 款 and 目 are the 一、 and （一） patterns, and 第X款/第X目 are cross-references,
# not headings.
"""Numbering marks (plan 2026-11-02 T6): the lines of a text that start a numbered part, with a level, for
chunks.split_sections. One group of patterns is picked per text, RAGFlow's way: the group matching the most lines
(first on ties); a line's level is the index of the first pattern of that group it matches (0 the top), else
QA_LEVEL for a Q&A marker, else MD_LEVEL plus its #s for a Markdown heading nothing else matched. Blank lines,
table-of-contents lines and lines that only look numbered (NOT_BULLET: 0 元, 3 個工作天, 1.2.3的, and a line with no
letter or Han character: a table cell, a date, a phone number) are never marks, nor votes. A line that is only a list
number (1. 2、 (1) （1）) is one when the next non-blank line holds text (the text layer split the number from it),
else it is a column of row numbers and no mark (review 11-02). Patterns are matched with re.match at the start of a
line without its Markdown decoration (strip_decor).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_CHARS = 40
QA_LEVEL, MD_LEVEL = 50, 100  # numbering levels 0–6, then Q&A markers, then Markdown-only headings (100 + #s)
# a table-of-contents line: a heading, a dot leader and maybe a page number (壹、報考資格 ........ 1); never a section
# start, or a hit there shows that one line (review 11-01); moved here from chunks._CONTENTS
CONTENTS_LINE = re.compile(r"(?:[.．…‥·‧・]\s*){4,}\d*$")

_N = "零一二三四五六七八九十百千〇兩"  # RAGFlow's numerals + 千 〇 兩
_FORMAL = "壹貳參叁肆伍陸柒捌玖拾佰"  # Taiwan's top level: 壹、
_D = "0-9０-９"
GROUPS = (  # (name, patterns from the top level down)
    ("zh_law", (rf"第\s*[{_N}{_D}]+\s*(?:分?[編编]|部分)", rf"第\s*[{_N}{_D}]+\s*章",
                rf"第\s*[{_N}{_D}]+\s*[節节]", rf"第\s*[{_N}{_D}]+\s*[條条](?:之[{_N}{_D}]+)?",
                rf"[{_N}]+\s*[、．.]", rf"[(（]\s*[{_N}]+\s*[)）]")),
    ("numeric", (rf"第\s*[{_D}]+\s*章", rf"第\s*[{_D}]+\s*[節节]", rf"[{_D}]{{1,2}}\s*[.．、](?![{_D}])",
                 rf"[{_D}]{{1,2}}[.．][{_D}]{{1,2}}(?![.．{_D}a-zA-Z/%~-])",
                 rf"[{_D}]{{1,2}}(?:[.．][{_D}]{{1,2}}){{2}}(?![.．{_D}])",
                 rf"[{_D}]{{1,2}}(?:[.．][{_D}]{{1,2}}){{3}}")),
    ("zh_num", (rf"[{_FORMAL}]+\s*[、．.]", rf"第\s*[{_N}{_D}]+\s*章", rf"第\s*[{_N}{_D}]+\s*[節节]",
                rf"[{_N}]+\s*[、．.]", rf"[(（]\s*[{_N}]+\s*[)）]", rf"[{_D}]{{1,2}}\s*[.．、](?![{_D}])",
                rf"[(（]\s*[{_D}]{{1,2}}\s*[)）]")),
    ("en", (r"PART (?:ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\b", r"Chapter (?:I+V?|VI*|XI|IX|X)\b",
            r"Section [0-9]+", r"Article [0-9]+")),
)
QA = (rf"第\s*[{_N}{_D}]+\s*[問问]", rf"(?:問題|问题|問|问)\s*[{_D}]*\s*[:：、.]",
      rf"[Qq]\s*[{_D}]*\s*[.、:：)）]|[Qq][{_D}]+")
_LIST_NUMBER = rf"(?:[(（]\s*[{_D}]{{1,2}}\s*[)）][.．、]?|[{_D}]{{1,2}}\s*[.．、])$"  # a whole line: 1. 2、 (1) （1）
NOT_BULLET = (r"0", rf"[{_D}]+ +[{_D}~個个隻只-]", rf"[{_D}]+[.．]{{2,}}", rf"[{_D}]+(?:[.．][{_D}]+){{2,}}[的中]",
              rf"(?!{_LIST_NUMBER})[\W\d_]+$")  # no letter or Han character (8.5, 39.72±1.23, 97.03.24), review 11-02

_GROUPS = tuple(tuple(re.compile(p) for p in patterns) for _, patterns in GROUPS)
_QA = tuple(re.compile(p) for p in QA)
_NOT_BULLET = tuple(re.compile(p) for p in NOT_BULLET)
_BARE_NUMBER = re.compile(_LIST_NUMBER)
_TEXT = re.compile(r"[^\W\d_]")  # a letter or a Han character
_ATX = re.compile(r"(#{1,6})(?:[ \t]+|$)")  # a Markdown heading's #s: at most six, then a space or the line end
_LIST = re.compile(r"[-*+][ \t]+")  # a Markdown list item's marker (Docling writes - 一、 text)


@dataclass(frozen=True)
class Mark:
    offset: int  # the start of the heading's line
    level: int
    heading: str


def strip_decor(line: str) -> tuple[str, int]:
    """(the line stripped of a Markdown heading's leading #s, a list marker ('- ', '* ', '+ ') and the ** or __ around
    its start, then of surrounding whitespace; the number of #s, 0 for none)."""
    s = line.strip()
    hashes = 0
    if m := _ATX.match(s):
        hashes, s = len(m.group(1)), s[m.end():]
    if m := _LIST.match(s):
        s = s[m.end():]
    for bold in ("**", "__"):
        if s.startswith(bold):  # its closing partner goes too: **一、報名**：見下 -> 一、報名：見下
            close = s.find(bold, 2)
            s = s[2:] if close < 0 else s[2:close] + s[close + 2:]
            break
    return s.strip(), hashes


def pick_group(lines: list[str]) -> int | None:
    """The index in GROUPS of the group whose patterns match most of these lines (cleaned by strip_decor), the first on
    ties; None when no line matches any group (RAGFlow's bullets_category)."""
    hits = [sum(any(p.match(line) for p in patterns) for line in lines) for patterns in _GROUPS]
    best = max(hits)
    return hits.index(best) if best else None


def marks(text: str) -> list[Mark]:
    """The heading lines of text in order, each with its line's offset, its level and its cleaned line cut to
    HEADING_CHARS."""
    lines: list[tuple[int, str, int]] = []  # (offset, cleaned line, #s) of the non-blank lines
    pos = 0
    for line in text.split("\n"):
        clean, hashes = strip_decor(line)
        if clean:
            lines.append((pos, clean, hashes))
        pos += len(line) + 1
    kept = [(offset, clean, hashes) for i, (offset, clean, hashes) in enumerate(lines)
            if not CONTENTS_LINE.search(clean) and not any(p.match(clean) for p in _NOT_BULLET)
            and not (_BARE_NUMBER.match(clean) and not (i + 1 < len(lines) and _TEXT.search(lines[i + 1][1])))]
    group = pick_group([clean for _, clean, _ in kept])
    patterns = _GROUPS[group] if group is not None else ()
    out = []
    for offset, clean, hashes in kept:
        level = next((i for i, p in enumerate(patterns) if p.match(clean)), None)
        if level is None and any(p.match(clean) for p in _QA):
            level = QA_LEVEL
        if level is None and hashes:
            level = MD_LEVEL + hashes
        if level is not None:
            out.append(Mark(offset, level, clean[:HEADING_CHARS]))
    return out
