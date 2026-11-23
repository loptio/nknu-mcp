"""Which unit each crawled site belongs to (plan 2026-11-08 T3; spec 2026-11-08 §二 6): config/site_units.yaml, one
row per round-1 site key of config/archive_round1.json, site key -> a unit id of the unit tree (nknu.content.units),
with how the row was made (auto: by a rule; hand: why). research/step3_2026-11-08/site_units.py writes the table: 83
rows by rule, the others by hand (the system parts of SSO go to the unit that runs them, an unsure one to the school,
saying so). It is data the owner glances at, never a rule per page, and it decides only the publisher shown in each
result (the unit weights are 0 since 11-08) and, from Task 4, the unit words a document is indexed with.

A document's unit is its own URL's site's (scope.site_key, so a file another site shares gets its owner's unit: plan
2026-11-08 T3); a site the table does not have (an outside host, SSO's file service webservices) gives way to the
export the record came from, and so does a shared platform (shared: true: the announcement system and SSO's root and
common modules, where many units publish; 【Claude】: 教务处's export holds 35 files of news.nknu.edu.tw, its notices'
attachments, which the announcement system's row would make 秘書室's); the school when neither is known. When the
platform's own export holds the same row too, the build keeps the record of the export that brought it
(documents._rank, plan 2026-11-08 T4 review), so those files stay 教務處's. No model, no network."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from nknu.archive import scope
from nknu.settings import load_settings

SITE_UNITS = "site_units.yaml"  # in the configuration directory (NKNU_CONFIG_DIR, as pagetext reads the round-1 list)
HOWS = ("auto:", "hand:")  # how a row was made: by a rule of the generating script, or by hand with why
FIELDS = frozenset({"unit", "how", "shared"})


@dataclass(frozen=True)
class SiteUnit:
    site: str
    unit: str  # a unit id of the unit tree
    how: str
    shared: bool = False  # a platform many units publish on: what another site's export brought from it is that site's


@dataclass(frozen=True)
class SiteTable:
    school: str  # the unit of a site the table does not have
    rows: dict[str, SiteUnit]  # site key -> its row


class _Loader(yaml.SafeLoader):
    """yaml.SafeLoader that refuses a key written twice in one mapping (PyYAML keeps the last one quietly: a site
    written twice would lose a row without a word)."""


def _mapping(loader: _Loader, node: yaml.MappingNode) -> dict:
    keys = [loader.construct_object(key) for key, _ in node.value]
    if twice := sorted({str(k) for k in keys if keys.count(k) > 1}):
        raise ValueError(f"keys written twice: {twice}")
    return loader.construct_mapping(node)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _text(path: Path, where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {where} must be a non-empty string, got {value!r}")
    return value.strip()


def read_site_units_text(text: str, path: Path) -> SiteTable:
    """The table in text (path names it in errors), checked: {school, sites: {site key: {unit, how, shared?}}}, no key
    written twice, every how starting auto: or hand:, shared true or false. A broken table stops the reader: another
    shape raises TypeError, a bad value ValueError (whether each unit is in the tree is the build's and the tests' to
    say)."""
    try:
        data = yaml.load(text, Loader=_Loader)  # _Loader: yaml.SafeLoader with one more check
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("sites"), dict):
        raise TypeError(f"{path}: expected a mapping with a mapping under 'sites'")
    school = _text(path, "school", data.get("school"))
    rows: dict[str, SiteUnit] = {}
    for site, row in data["sites"].items():
        key = _text(path, "a site key", site)
        if not isinstance(row, dict):
            raise TypeError(f"{path}: {key}: expected a mapping, got {row!r}")
        if unknown := sorted(set(row) - FIELDS):
            raise ValueError(f"{path}: {key}: unknown fields {unknown}")
        how = _text(path, f"{key}: how", row.get("how"))
        if not how.startswith(HOWS):
            raise ValueError(f"{path}: {key}: how must start with auto: or hand:, got {how!r}")
        shared = row.get("shared", False)
        if not isinstance(shared, bool):
            raise TypeError(f"{path}: {key}: shared must be true or false, got {shared!r}")
        rows[key] = SiteUnit(key, _text(path, f"{key}: unit", row.get("unit")), how, shared)
    return SiteTable(school, rows)


def read_site_units(path: Path) -> SiteTable:
    return read_site_units_text(Path(path).read_text(encoding="utf-8"), Path(path))


@lru_cache(maxsize=4)
def _read(path: str) -> SiteTable:
    return read_site_units(Path(path))


def table() -> SiteTable:
    """The table of the configuration directory, read once; a missing one stops the caller (FileNotFoundError), never
    makes every document the school's quietly."""
    return _read(str(load_settings().config_dir / SITE_UNITS))


def unit_of_site(site_key: str) -> str:
    """The unit id of a site; the school for a site the table does not have (and for '')."""
    t = table()
    row = t.rows.get(site_key or "")
    return row.unit if row is not None else t.school


def unit_for(url: str, export_site: str = "") -> str:
    """The unit of what is at url, brought by the export of export_site (plan 2026-11-08 T3): url's own site's when
    the table has it, unless that site is a shared platform and the export is known; else the export's site's; the
    school when neither is in the table. A press-made row (<list url>#按钮=…) and an archive member
    (<archive url>#<path>) are their list's and their archive's."""
    row = table().rows.get(scope.site_key(url))
    if row is not None and not (row.shared and export_site):
        return row.unit
    return unit_of_site(export_site)
