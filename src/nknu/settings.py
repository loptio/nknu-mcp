"""Paths and environment switches shared by the crawler, builder and server."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    config_dir: Path
    readonly: bool
    disabled_sources: frozenset[str]
    allowed_hosts: list[str]
    allowed_origins: list[str]
    discord_webhook: str | None
    backup_dir: Path | None

    @property
    def facts_path(self) -> Path:
        return self.data_dir / "facts.db"

    @property
    def serve_path(self) -> Path:
        return self.data_dir / "serve.db"

    @property
    def warc_dir(self) -> Path:
        return self.data_dir / "warc"


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    backup = env.get("NKNU_BACKUP_DIR")
    return Settings(
        data_dir=Path(env.get("NKNU_DATA_DIR", "./data")),
        config_dir=Path(env.get("NKNU_CONFIG_DIR", REPO_ROOT / "config")),
        readonly=env.get("NKNU_READONLY", "") == "1",
        disabled_sources=frozenset(_csv(env.get("NKNU_DISABLED_SOURCES"))),
        allowed_hosts=_csv(env.get("NKNU_ALLOWED_HOSTS", "127.0.0.1,localhost")),
        allowed_origins=_csv(env.get("NKNU_ALLOWED_ORIGINS")),
        discord_webhook=env.get("NKNU_DISCORD_WEBHOOK") or None,
        backup_dir=Path(backup) if backup else None,
    )
