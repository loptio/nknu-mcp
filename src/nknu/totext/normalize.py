"""Text normalization (plan 2026-11-02 T5), applied by nknu.content.assemble to every stored text, in this order:
1. fold_compat: Kangxi radicals (U+2F00 for 一), radical supplements and compatibility ideographs (U+F900 for 豈)
   become the unified character NFKC gives, so search and the models see one spelling of a character;
2. fix_mismapped, PDF text only (review of phase 1): characters a font's text layer maps to the wrong code point
   (MISMAPPED) become the ones the page shows;
3. join_spaced: a letter-spaced run (「招 生 簡 章」) loses its spaces; outside PDF text (page, office, office_old and
   image text) also an isolated run of 2 or 3 (「分 機」「申 請 人」: addendum H1), U+3000 counting as the space;
4. join_wraps, PDF text-layer text only: the space a line wrap left between two CJK characters goes (「並以一 次」),
   which already joins a PDF's short runs.
An [[img:src]] line (pagetext) is left as it is: assemble finds the image by that src."""

from __future__ import annotations

import re
import unicodedata

from nknu.core.textnorm import CJK
from nknu.totext.pagetext import IMG_LINE

COMPAT_RANGES = ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF), (0xF900, 0xFAFF), (0x2F800, 0x2FA1F))
_COMPAT = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in COMPAT_RANGES) + "]")
# PDF text layers set in 標楷體 (Word's PDF export) give these glyphs the code points of rare look-alikes. Measured on
# the 10-27 export: 詴 416 times, 帄 72, only in that font and only where 試 and 平 belong (考詴, 面詴, 和帄校區, 公帄);
# neither occurs in any exported page. 郍 (131 times) is not here: it shows only as 郍吒, in 新細明體, which writes
# 那 correctly beside it, so that text layer may be the author's own. ocr.misread_count counts these as known.
MISMAPPED = {"詴": "試", "帄": "平"}
_MISMAPPED = str.maketrans(MISMAPPED)
_SPACED_PDF = re.compile(rf"[{CJK}](?: [{CJK}]){{3,}}")  # 4 or more CJK characters, one ASCII space between each
# outside PDF text the space between two CJK characters of a run may also be U+3000 (a full-width space); a run of 4
# or more joins wherever it stands (it goes first, so no run of 2 or 3 is ever a piece of a longer one), one of 2 or
# 3 only with no CJK character right before or after it (H1: 「分 機 1131」「地 址 高雄市」 join, 「申請 人」 stays)
_GAP = "[ 　]"
_SPACED = re.compile(rf"[{CJK}](?:{_GAP}[{CJK}]){{3,}}")
_SHORT = re.compile(rf"(?<![{CJK}])[{CJK}](?:{_GAP}[{CJK}]){{1,2}}(?![{CJK}])")
_WIDE = f"{CJK}、-〿！-｠"  # CJK, CJK punctuation, full-width forms
_WRAP = re.compile(rf"(?<=[{_WIDE}]) (?=[{_WIDE}])")
_MARK = "[[img:"


def fold_compat(text: str) -> tuple[str, int]:
    """Each character of COMPAT_RANGES whose NFKC form is one other character becomes it; (text, how many)."""
    count = 0

    def fold(m: re.Match) -> str:
        nonlocal count
        c = m.group()
        folded = unicodedata.normalize("NFKC", c)
        if len(folded) == 1 and folded != c:
            count += 1
            return folded
        return c

    return _COMPAT.sub(fold, text), count


def fix_mismapped(text: str) -> tuple[str, int]:
    """Each character of MISMAPPED becomes the one it stands for; (text, how many)."""
    count = sum(text.count(c) for c in MISMAPPED)
    return (text.translate(_MISMAPPED), count) if count else (text, 0)


def _joined(m: re.Match) -> str:
    return m.group().replace(" ", "").replace("　", "")


def join_spaced(text: str, *, pdf: bool = False) -> tuple[str, int]:
    """Letter-spaced CJK loses its spaces; (text, runs). Runs of 4 or more CJK characters each separated by exactly one
    space (ASCII, or U+3000), then (H1) isolated runs of 2 or 3: no CJK character right before or after. pdf=True:
    only runs of 4 or more with ASCII spaces, as before H1 (join_wraps joins the rest of a PDF's spaced CJK)."""
    if pdf:
        return _SPACED_PDF.subn(_joined, text)
    text, long_runs = _SPACED.subn(_joined, text)
    text, short_runs = _SHORT.subn(_joined, text)
    return text, long_runs + short_runs


def join_wraps(text: str) -> tuple[str, int]:
    """One ASCII space between two CJK, CJK-punctuation or full-width characters, out; (text, spaces)."""
    return _WRAP.subn("", text)


def normalize(text: str, *, pdf: bool) -> tuple[str, dict[str, int]]:
    """The four steps in order (fix_mismapped and join_wraps only for pdf; join_spaced's short runs only without it),
    [[img:…]] lines untouched; (text, {"compat", "mismapped", "spaced", "wraps"}). No step crosses a line end, so the
    text goes whole unless it holds an image line."""
    counts = {"compat": 0, "mismapped": 0, "spaced": 0, "wraps": 0}
    steps = ([("compat", fold_compat)] + [("mismapped", fix_mismapped)] * pdf
             + [("spaced", lambda part: join_spaced(part, pdf=pdf))] + [("wraps", join_wraps)] * pdf)
    marked = _MARK in text
    parts = text.split("\n") if marked else [text]
    for i, part in enumerate(parts):
        if marked and IMG_LINE.fullmatch(part):
            continue
        for name, step in steps:
            part, n = step(part)
            counts[name] += n
        parts[i] = part
    return "\n".join(parts), counts
