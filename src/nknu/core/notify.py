"""Alerts always go to the log; a Discord webhook is optional and never breaks a run."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("nknu")


def notify(message: str, webhook: str | None, *, label: str = "ALERT") -> None:
    log.warning("%s %s", label, message)
    if not webhook:
        return
    try:
        httpx.post(webhook, json={"content": message[:1900]}, timeout=10)
    except Exception as exc:  # noqa: BLE001 - notification failure must not stop the crawl
        log.error("discord webhook failed: %s", exc)
