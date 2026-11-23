"""Paths and environment switches shared by the crawler, the builder and the public service (deploy/env.example lists
every one)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _off(value: str | None) -> bool:
    return (value or "").strip().lower() in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    config_dir: Path
    allowed_hosts: list[str]
    allowed_origins: list[str]
    # Cloudflare tunnel: trust cloudflared's CF-Connecting-IP from loopback; Tailscale Funnel: 0 (see ratelimit.py)
    trust_cf_header: bool = True
    # the machine-wide lock on requests to the school (archive/schoollock.py, round-1 design §6.9): every program that
    # sends to the school's servers takes this one file, so they must all read the same path
    school_lock: Path = Path.home() / ".cache" / "nknu" / "school-requests.lock"

    @property
    def facts_path(self) -> Path:  # v1's facts library, which `nknu archive seed` reads round 1's start from when present
        return self.data_dir / "facts.db"

    @property
    def warc_dir(self) -> Path:
        return self.data_dir / "warc"


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    return Settings(
        data_dir=Path(env.get("NKNU_DATA_DIR", "./data")),
        config_dir=Path(env.get("NKNU_CONFIG_DIR", REPO_ROOT / "config")),
        allowed_hosts=_csv(env.get("NKNU_ALLOWED_HOSTS", "127.0.0.1,localhost")),
        allowed_origins=_csv(env.get("NKNU_ALLOWED_ORIGINS")),
        trust_cf_header=not _off(env.get("NKNU_TRUST_CF_HEADER")),
        school_lock=Path(env.get("NKNU_SCHOOL_LOCK") or Path.home() / ".cache" / "nknu" / "school-requests.lock"),
    )
