"""Search-side normalisation and the bigram tokenizer (index and query use the same function).

Traditional->Simplified folding is for matching only; anything shown to users stays in the original text.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from opencc import OpenCC

_t2s = OpenCC("t2s")
CJK = "㐀-䶿一-鿿豈-﫿"
_RUN = re.compile(rf"[{CJK}]+|[a-z0-9]+(?:[.\-][a-z0-9]+)*")
_CJK_START = re.compile(rf"[{CJK}]")
_NOT_ALIAS_CHAR = re.compile(rf"[^{CJK}a-z0-9]+")


@lru_cache(maxsize=100_000)
def norm_text(s: str) -> str:
    """NFKC (full-width -> ASCII) + lowercase + OpenCC t2s (folds 臺/台, 館/馆, ...)."""
    return _t2s.convert(unicodedata.normalize("NFKC", s).lower())


def alias_norm(s: str) -> str:
    """Key for alias lookup: norm_text, 臺->台, punctuation and whitespace removed; every CJK char is kept."""
    return _NOT_ALIAS_CHAR.sub("", norm_text(s).replace("臺", "台"))


def tokenize(s: str) -> list[str]:
    """CJK runs -> overlapping bigrams (a lone CJK char stays a unigram); ASCII words kept whole."""
    out: list[str] = []
    for m in _RUN.finditer(norm_text(s)):
        run = m.group()
        if _CJK_START.match(run):
            out += [run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)]
        else:
            out.append(run)
    return out


def fts_query(tokens: list[str]) -> str:
    """FTS5 MATCH expression: every token quoted, OR-ed (bm25 ranks by how many match)."""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
