"""網站 (資料總覽), inside 檢視台 since 2026-11-21 (the owner, of panels built a stage at a time: 「现在就做」, to one set of
programs and one way to install them): the page of every site, what it holds and what it lacks, was a service of its
own (deploy/lab-overview: server.py, a container, port 8879) and is now 檢視台's /sites.

What it keeps of that service: the data (nknu.ops.overview_data) is computed in a thread of its own and only when the
library, the build report, the site list or the date changed, a change being used once it has held for one check (a
build still writing the library keeps changing it); a failed computation keeps the data before it on show and is tried
again; the page (overview_page.html, written like an Artifact page: no document skeleton of its own) carries its data
and asks /sites/stamp every minute, reloading when the stamp changed. A request never computes: it is answered from
what the thread last made, so the page costs 檢視台's other pages nothing.
"""

from __future__ import annotations

import datetime
import gzip
import hashlib
import json
import logging
import threading
import time
from pathlib import Path

from nknu.ops import overview_data
from nknu.ops.theme import bar as theme

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
PAGE = HERE / "overview_page.html"
PATH = "/sites"  # panels.json's entry 網站
WAITING = ("<!doctype html><html lang='zh-Hant-TW'><meta charset='utf-8'><meta http-equiv='refresh' content='3'>"
           "<title>網站</title><p style='font:14px system-ui;padding:24px'>正在統計，幾秒後自己更新。</p>").encode()


def today() -> datetime.date:
    return datetime.datetime.now().date()  # the container runs with TZ=Asia/Taipei


def signature(root: Path, day: datetime.date) -> tuple:
    """What a recomputation depends on: the files the data is read from, and the date ("today" and the chart's end)."""
    parts = []
    for rel in (overview_data.DB, overview_data.REPORT, overview_data.ROUND1, overview_data.CHECKS):
        p = root / rel
        st = p.stat() if p.exists() else None
        parts.append((rel, st.st_size, st.st_mtime_ns) if st else (rel, None, None))
    return (*parts, day.isoformat())


class Watch:
    """Decides when to recompute: at once the first time, then only for a change that has held for one check."""

    def __init__(self) -> None:
        self.seen = None
        self.done = None

    def due(self, sig: tuple) -> bool:
        held = sig == self.seen
        self.seen = sig
        return sig != self.done and (held or self.done is None)

    def finished(self, sig: tuple) -> None:
        self.done = sig


def head() -> str:
    """The skeleton an Artifact page gets, with the panels' theme first and their bar on top."""
    return ('<!doctype html><html lang="zh-Hant-TW"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            "<style>:root{color-scheme:light;padding-block:env(safe-area-inset-top,0px) env(safe-area-inset-bottom,0px)}"
            "body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>"
            f"<style>{theme.theme_css()}</style></head><body>{theme.bar('sites', 'ops')}")


def links() -> str:
    """Where the page's links go, from panels.json (one place for the addresses): one site's page (the list's names
    and the inspector's 「這個網站的詳情」), 資料庫's origin (its canned queries), and where the page asks for its stamp."""
    return json.dumps({"site": theme.site_prefix("ops"), "tables": theme.panels()["hosts"]["tables"].rstrip("/"),
                       "stamp": f"{PATH}/stamp"}, ensure_ascii=False)


def render(template: str, data: dict) -> bytes:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = template.replace("__DATA__", text).replace("__LINKS__", links().replace("</", "<\\/"))
    return (head() + page + "</body></html>").encode("utf-8")


class State:
    """The page as last computed, and the thread that keeps it current (start: once; stop: with the app)."""

    def __init__(self, root: Path, template: str | None = None, interval: float = 60.0,
                 kept: object | None = None) -> None:
        self.root, self.interval = root, interval
        self.kept = kept  # nknu.ops.keep.Kept: the data of the same files and day survives a restart (2026-11-22)
        self.template = template if template is not None else PAGE.read_text(encoding="utf-8")
        self.lock = threading.Lock()
        self.page = self.page_gz = self.data = None
        self.stamp, self.built, self.error, self.io_error = "", "", "", ""
        self.watch = Watch()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def tick(self) -> None:
        day = today()
        try:
            sig = signature(self.root, day)
        except OSError as e:
            self.io_error = f"讀不到資料庫：{e}"  # the page shows it (through its stamp)
            return
        self.io_error = ""
        if not self.watch.due(sig):
            return
        if self.data is None and self.kept is not None:  # after a restart: the same files and day, the same data
            got = self.kept.get("sites", sig)
            if isinstance(got, dict) and got.get("sites"):
                self._show(got)
                self.watch.finished(sig)
                log.info("网站总览：用了重启前算好的统计（资料库 %s 建）", got.get("built"))
                return
        t0 = time.monotonic()
        try:
            data = overview_data.collect(self.root, today=day)
        except Exception as e:  # keep serving the previous data, try again at the next check
            said = f"統計失敗：{e}"  # the page shows it (through its stamp); the log line stays Simplified
            if said != self.error:  # the same failure every minute is said once, with where it failed
                log.warning("网站总览统计失败：%s（继续显示上一次的资料，下次检查再试）", e, exc_info=True)
            self.error = said
            return
        body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        data["live"], data["stamp"] = True, hashlib.sha256(body).hexdigest()[:16]
        self._show(data)
        if self.kept is not None:
            self.kept.put("sites", sig, data)
        self.watch.finished(sig)
        log.info("网站总览统计完成：%d 个网站，资料库 %s 建，用了 %.1f 秒", len(data["sites"]), data["built"],
                 time.monotonic() - t0)

    def _show(self, data: dict) -> None:
        """data drawn into the page the requests get."""
        page = render(self.template, data)
        with self.lock:
            self.data, self.page, self.page_gz = data, page, gzip.compress(page, 6)
            self.stamp, self.built, self.error = data["stamp"], data["built"], ""

    def start(self) -> None:
        """The thread, once (the app's start, or the first request for the page)."""
        with self.lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._loop, name="overview", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)

    def close(self) -> None:
        self._stop.set()

    def said(self) -> dict:
        """What /sites/stamp answers: the page reloads when the stamp changed, and shows an error."""
        with self.lock:
            err = self.io_error or self.error
            return {"stamp": self.stamp, "built": self.built, "state": "error" if err else "ok", "error": err}
