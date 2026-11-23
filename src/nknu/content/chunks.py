"""Sections, for reading (全文 / 章節 / read part=N), and chunks, for finding: about TARGET_CHARS characters packed
from paragraphs inside one section, never more than MAX_CHARS. Both are offsets into the document text, never copies.

Sections (plan 2026-11-02 T6): a document is split at its numbering marks (numbering.marks: 第X章/節/條, 壹、, 一、,
（一）, 1., Q&A markers, Markdown headings) only while a part is longer than SECTION_MAX, one level at a time, top
level first (WeKnora's 「超过目标长度 2 倍就往下细分一层」, applied to sections). A chunk that starts inside a Markdown
table carries the span of the table's header row (head_start, head_end) for the index, the embeddings and the rerank
passages (T7a, T7b, T8); the response does not show it (that would change the 11-01 format).
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from nknu.content.numbering import CONTENTS_LINE, HEADING_CHARS, Mark, marks, strip_decor

TARGET_CHARS = 400
MAX_CHARS = 800
SECTION_MAX = 3_000  # = respond.FULL_CAP_CHARS: a longer section cannot be shown whole (2 × a 1,500-char target)

_CN = "一二三四五六七八九十百零〇"
# The skeleton's heading pattern and contents-line pattern (11-01), unused here since T6 (numbering.marks decides);
# kept because research/content_explain_2026-11-02/chunking_index/ (the 11-02 attribution's scripts) imports them.
_HEADING = re.compile(  # matched at the start of a stripped line
    rf"第\s*[{_CN}\d]+\s*[章節节條条]"  # 第一章 第3節 第十二條
    r"|[壹貳參肆伍陸柒捌玖拾]+\s*、"  # 壹、
    rf"|[{_CN}]+\s*、"  # 一、
    r"|[Qq]\s*\d*\s*[.、:：)）]|[Qq]\d"  # Q1： Q. Q1
    r"|問\s*\d*\s*[:：、.]"  # 問： 問1：
)
_CONTENTS = CONTENTS_LINE
_PARAGRAPH_BREAK = re.compile(r"\n[^\S\n]*\n")
_LINE_BREAK = re.compile(r"\n")
_SENTENCE_END = re.compile(r"[。！？；!?;]")
# a GFM table's delimiter row, | --- | :-: |, or Docling's compact | - | -: | (T3a writes compact tables); fullmatch
# on one line
_SEPARATOR = re.compile(r"\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*")


@dataclass(frozen=True)
class Section:
    sec_id: int
    heading: str
    start: int
    end: int


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    sec_id: int
    start: int
    end: int
    head_start: int | None = None  # the header of the table the chunk starts inside (table_header), None outside one
    head_end: int | None = None


def _first_line(s: str) -> str:
    """The first line with text, without its Markdown decoration, cut to HEADING_CHARS."""
    return next((clean for line in s.split("\n") if (clean := strip_decor(line)[0])), s.strip())[:HEADING_CHARS]


def _first_text_line(text: str, start: int, end: int) -> int:
    """The offset of the first line in text[start:end] with text after strip_decor; end when there is none."""
    pos = start
    while pos < end:
        stop = text.find("\n", pos, end)
        stop = end if stop < 0 else stop
        if strip_decor(text[pos:stop])[0]:
            return pos
        pos = stop + 1
    return end


def split_sections(text: str, *, max_chars: int = SECTION_MAX) -> list[Section]:
    """[] for blank text; else sections numbered 0.. that cover the text with no gaps. The whole text is one part,
    headed by its first line; then, for each mark level from the top: a part longer than max_chars is cut at the marks
    of that level after its own first line (its heading), each new part headed by its mark's line. The part before
    the first cut keeps the old heading (it holds the part's first line, so leading blank lines stay with the first
    heading), unless it has under TARGET_CHARS // 2 characters of text and max_chars > 0: a heading straight above its
    children, or a short preamble, joins its first child, headed f"{heading} {child heading}"; a long piece heading (a
    title) gives up its tail for the child's, keeping at least HEADING_CHARS // 2 (review 11-02). Alone it would be a
    one-chunk section of a few characters: bm25 favours it, and it becomes the 摘要. max_chars=0 cuts at every mark,
    as the skeleton did (11-01)."""
    if not text.strip():
        return []
    by_level: dict[int, list[Mark]] = {}
    for m in marks(text):
        by_level.setdefault(m.level, []).append(m)
    pieces = [(0, len(text), _first_line(text))]
    for level in sorted(by_level):
        found = by_level[level]
        offsets = [m.offset for m in found]
        out: list[tuple[int, int, str]] = []
        for start, end, heading in pieces:
            cuts: list[Mark] = []
            if end - start > max_chars:
                cuts = found[bisect_right(offsets, _first_text_line(text, start, end)):bisect_left(offsets, end)]
            if not cuts:
                out.append((start, end, heading))
                continue
            children = [(m.offset, nxt, m.heading) for m, nxt in zip(cuts, [m.offset for m in cuts[1:]] + [end])]
            if max_chars > 0 and len(text[start:cuts[0].offset].strip()) < TARGET_CHARS // 2:
                _, nxt, child = children[0]  # a long heading (a title) gives up its tail so the child's shows
                room = max(HEADING_CHARS // 2, HEADING_CHARS - 1 - len(child))
                children[0] = (start, nxt, f"{heading[:room].rstrip()} {child}"[:HEADING_CHARS])
            else:
                children.insert(0, (start, cuts[0].offset, heading))
            out += children
        pieces = out
    return [Section(i, heading, start, end) for i, (start, end, heading) in enumerate(pieces)]


def _line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def _line_end(text: str, pos: int) -> int:
    end = text.find("\n", pos)
    return len(text) if end < 0 else end


def _table_first(text: str, line: int, known: tuple[int, int] | None = None) -> int:
    """The first line of the table (a run of lines beginning with |) that holds the row starting at line. known: a row
    found before, (its line start, its table's first line); the walk back stops there, so make_chunks stays linear on
    a table of many thousand rows."""
    first = line
    while first > 0:
        if known and first == known[0]:  # every line walked so far is a row: the same table
            return known[1]
        prev = _line_start(text, first - 1)
        if not text.startswith("|", prev):
            break
        first = prev
    return first


def _row_header(text: str, line: int, first: int) -> tuple[int, int] | None:
    """table_header for a row starting at line, in a table whose first line starts at first."""
    if first == line:
        return None
    end = _line_end(text, first)
    if end + 1 < line and _SEPARATOR.fullmatch(text, end + 1, _line_end(text, end + 1)):
        end = _line_end(text, end + 1)
    return first, end


def table_header(text: str, start: int) -> tuple[int, int] | None:
    """The header of the Markdown table a chunk starting at start is inside: (start, end) of the table's first line,
    with the separator line (| --- |) when that is the table's second line and the chunk starts below it. A table is
    a run of lines beginning with |. None when the chunk starts on the table's first line, or on a line that is no
    table row."""
    line = _line_start(text, start)
    return _row_header(text, line, _table_first(text, line)) if text.startswith("|", line) else None


def _trim(text: str, a: int, b: int) -> tuple[int, int] | None:
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return (a, b) if a < b else None


def _pieces(text: str, a: int, b: int, sep: re.Pattern) -> list[tuple[int, int]]:
    """[a, b) cut after each match of sep, each piece trimmed, blank pieces dropped."""
    cuts = [m.end() for m in sep.finditer(text, a, b)]
    return [t for x, y in zip([a, *cuts], [*cuts, b]) if (t := _trim(text, x, y))]


def _fit(text: str, a: int, b: int) -> list[tuple[int, int]]:
    """A paragraph over MAX_CHARS cut at line ends, a line over it at sentence ends, a sentence over it every MAX_CHARS."""
    if b - a <= MAX_CHARS:
        return [(a, b)]
    out: list[tuple[int, int]] = []
    for la, lb in _pieces(text, a, b, _LINE_BREAK):
        if lb - la <= MAX_CHARS:
            out.append((la, lb))
            continue
        for sa, sb in _pieces(text, la, lb, _SENTENCE_END):
            out += [t for x in range(sa, sb, MAX_CHARS) if (t := _trim(text, x, min(x + MAX_CHARS, sb)))]
    return out


def _pack(units: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Consecutive units joined while the result stays within TARGET_CHARS, or within MAX_CHARS when the chunk so far
    is under half the target; then a chunk still under half the target (a section's last line, or one before a
    paragraph too long to join) goes back into the chunk before it within MAX_CHARS. A crumb of a few characters
    holds little but the repeated title words, so it would win bm25 and be the whole summary (review 11-01)."""
    out: list[tuple[int, int]] = []
    for a, b in units:
        if out and (b - out[-1][0] <= TARGET_CHARS
                    or (out[-1][1] - out[-1][0] < TARGET_CHARS // 2 and b - out[-1][0] <= MAX_CHARS)):
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    packed: list[tuple[int, int]] = []
    for a, b in out:
        if packed and b - a < TARGET_CHARS // 2 and b - packed[-1][0] <= MAX_CHARS:
            packed[-1] = (packed[-1][0], b)
        else:
            packed.append((a, b))
    return packed


def make_chunks(doc_id: str, text: str, sections: list[Section]) -> list[Chunk]:
    """Paragraph packing inside each section, each chunk with the header of the table it starts inside
    (table_header); [] when there are no sections (blank text)."""
    out: list[Chunk] = []
    known: tuple[int, int] | None = None  # the last chunk's table row: (its line start, its table's first line)
    for s in sections:
        units = [u for a, b in _pieces(text, s.start, s.end, _PARAGRAPH_BREAK) for u in _fit(text, a, b)]
        for a, b in _pack(units):
            head, line = None, _line_start(text, a)
            if text.startswith("|", line):  # table_header(text, a), with the walk back cut short
                known = (line, _table_first(text, line, known))
                head = _row_header(text, line, known[1])
            out.append(Chunk(doc_id, s.sec_id, a, b, *(head or (None, None))))
    return out
