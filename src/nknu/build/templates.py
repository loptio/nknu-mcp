"""Answer-sentence templates and the date/weekday formatting they share."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from nknu.core.rocdate import roc_str

DOWZH = "一二三四五六日"
TENTATIVE_NOTE = "（暫定，日期可能變動）"


@dataclass(frozen=True)
class Templates:
    ver: int
    texts: dict[str, str]

    def render(self, key: str, **fields: object) -> str:
        return self.texts[key].format(**fields)


def load_templates(path: Path) -> Templates:
    raw = yaml.safe_load(Path(path).read_text())
    ver = int(raw.pop("template_ver"))
    return Templates(ver, {k: str(v) for k, v in raw.items()})


def event_note(status: str | None) -> str:
    """The calendar_event template's {note}: a tentative event (written without review since U2) says so."""
    return TENTATIVE_NOTE if status == "tentative" else ""


def dows_zh(dows: list[int] | tuple[int, ...]) -> str:
    """[1,2,3,5] -> 週一、二、三、五; a run of >=4 days collapses: [1..5] -> 週一至週五."""
    ds = sorted(set(dows))
    runs: list[list[int]] = []
    for d in ds:
        if runs and d == runs[-1][-1] + 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    parts = []
    for run in runs:
        if len(run) >= 4:
            parts.append(f"{DOWZH[run[0] - 1]}至週{DOWZH[run[-1] - 1]}")
        else:
            parts += [DOWZH[d - 1] for d in run]
    return "週" + "、".join(parts)


def span_zh(lo: date, hi: date) -> str:
    """Half-open [lo, hi) as printed: 2026-09-07～2027-01-08（115.09.07–116.01.08）."""
    last = hi - timedelta(days=1)
    if last <= lo:
        return f"{lo}（{roc_str(lo)}）"
    return f"{lo}～{last}（{roc_str(lo)}–{roc_str(last)}）"
