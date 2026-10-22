"""Identifiers: uuid7 for fact versions, uuid5 lineages, and the entity id rule."""

from __future__ import annotations

import os
import re
import threading
import time
from uuid import NAMESPACE_URL, UUID, uuid5

NKNU_NS = uuid5(NAMESPACE_URL, "https://github.com/loptio/nknu-mcp")

# nknu | nknu.<campus> | nknu.org.<slug> | nknu.<campus>.<type>.<slug>   (campus in the id keeps same-named places apart)
ENTITY_ID_RE = re.compile(r"^nknu(\.(heping|yanchao)(\.[a-z]+\.[a-z0-9-]+)?|\.org\.[a-z0-9-]+)?$")

_lock = threading.Lock()
_last = [0, 0]  # last ms timestamp, counter


def uuid7() -> str:
    """RFC 9562 UUIDv7: 48-bit ms timestamp, then a 12-bit counter so ids made in the same ms stay ordered."""
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last[0]:
            ms, _last[1] = _last[0], _last[1] + 1
        else:
            _last[1] = int.from_bytes(os.urandom(2)) & 0x3FF
        _last[0] = ms
        seq = _last[1] & 0xFFF
    rand = int.from_bytes(os.urandom(8)) & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (seq << 64) | (0b10 << 62) | rand
    return str(UUID(int=value))


def lineage_id(record_key: str) -> str:
    return str(uuid5(NKNU_NS, record_key))
