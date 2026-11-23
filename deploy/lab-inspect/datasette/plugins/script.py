"""Datasette plugin: one SQL function, t2s(text), a text with every Traditional character written as its Simplified
one. The school's titles and names are Traditional and the owner types either script (owner 11-15, of finding a site
on these pages: 「只能一个个看名字…我覺得這很不人性化」), so a search compares both sides through it and never asks which
script to type 【Claude】.

The table (t2s.json beside this file: one string of Traditional characters and one of what each becomes) was made
once from OpenCC's t2s character table, for the CJK blocks U+3400-4DBF and U+4E00-9FFF. By character only: this way
round is many-to-one, so a wrong guess is rare; a word that differs between the two usages (軟體／软件) stays
different. Datasette loads only the .py files of its plugins folder, so the .json rides along untouched.

Loaded by `datasette --plugins-dir`; tests/ops/test_deploy_files.py loads it the same way."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from datasette import hookimpl
except ImportError:  # the tests load this file where Datasette is not installed

    def hookimpl(fn):
        return fn


def _table() -> dict:
    pairs = json.loads((Path(__file__).with_name("t2s.json")).read_text(encoding="utf-8"))
    return str.maketrans(pairs["from"], pairs["to"])


TABLE = _table()


def t2s(text):
    """The text in Simplified characters; anything that is not a text as it is."""
    return text.translate(TABLE) if isinstance(text, str) else text


def register(conn) -> None:
    conn.create_function("t2s", 1, t2s, deterministic=True)


@hookimpl
def prepare_connection(conn):
    register(conn)
