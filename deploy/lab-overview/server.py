"""资料总览, live: a small read-only web service on the lab PC (owner 11-15, 「照这样做」 to: the page runs on the lab PC like
the inspection tools, opened through the SSH tunnel; it checks every minute whether the library has changed, recomputes
then, and the page refreshes itself keeping what was being looked at).

    python server.py --root /work --port 8879

What it serves (GET only, and only to requests made under a local name: 127.0.0.1, localhost, [::1], the way the crawler
viewer and the inspection tools guard against other sites using the browser to reach them):
    /             the page, today's data written into it
    /stamp        {"stamp", "built", "state", "error"}: the page asks every minute and reloads when the stamp changes
    /data.json    the same data the page carries

When it recomputes 【Claude】: every --interval seconds it looks at the size and time of the library, the build report and
the round-1 list, and at the date. A change is used only once it has held for one whole interval (a build still writing
the library keeps changing it), and only if it differs from what was last computed. A failed computation keeps the
previous data on show and is tried again at the next check. Standard library only (the nknu-pipeline image has it).
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overview_data

LOCAL_NAMES = {"127.0.0.1", "localhost", "[::1]"}
WAITING = ("<!doctype html><html lang='zh-Hant-TW'><meta charset='utf-8'><meta http-equiv='refresh' content='3'>"
           "<title>資料總覽</title><p style='font:14px system-ui;padding:24px'>正在統計，幾秒後自己更新。</p>").encode()


def host_ok(host: str | None) -> bool:
    """Whether a Host header names this machine (any port)."""
    h = (host or "").strip().lower()
    name = h[: h.index("]") + 1] if h.startswith("[") and "]" in h else h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    return name in LOCAL_NAMES


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


# The panels' one look and top bar (redesign 2026-11-17, nknu/ops/theme): on the lab PC theme.css, panels.json and
# bar.py are sent into this folder with the others (README.md 「换程序」); run from the repository, they are read where
# they live. bar.py is standard library only, loaded by path (this server does not import the nknu package).
HERE = Path(__file__).resolve().parent
THEME_DIR = HERE if (HERE / "theme.css").exists() else HERE.parents[1] / "src" / "nknu" / "ops" / "theme"


def _load_bar():
    import importlib.util
    spec = importlib.util.spec_from_file_location("nk_theme_bar", THEME_DIR / "bar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BAR = _load_bar()

# page.html is written like an Artifact page (no document skeleton of its own); this is the skeleton an Artifact gets,
# with the panels' theme first and their bar on top
HEAD = ('<!doctype html><html lang="zh-Hant-TW"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        "<style>:root{color-scheme:light;padding-block:env(safe-area-inset-top,0px) env(safe-area-inset-bottom,0px)}"
        "body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>"
        f"<style>{_BAR.theme_css(THEME_DIR)}</style>"
        f"</head><body>{_BAR.bar('overview', 'overview', folder=THEME_DIR)}")


def render(template: str, data: dict) -> bytes:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (HEAD + template.replace("__DATA__", text) + "</body></html>").encode("utf-8")


class State:
    def __init__(self, root: Path, template: str) -> None:
        self.root, self.template = root, template
        self.lock = threading.Lock()
        self.page = self.page_gz = self.data = None
        self.stamp, self.built, self.error, self.io_error = "", "", "", ""
        self.watch = Watch()

    def tick(self) -> None:
        day = today()
        try:
            sig = signature(self.root, day)
        except OSError as e:
            self.io_error = f"讀不到資料庫：{e}"  # the page shows it (through /stamp)
            return
        self.io_error = ""
        if not self.watch.due(sig):
            if sig != self.watch.done and self.data is not None:
                print("资料库有变动，等它一分钟不再变动再统计", flush=True)
            return
        t0 = time.monotonic()
        try:
            data = overview_data.collect(self.root, today=day)
        except Exception as e:  # noqa: BLE001 - keep serving the previous data, try again at the next check
            self.error = f"統計失敗：{e}"  # the page shows it (through /stamp); the console line stays Simplified
            print(f"统计失败：{e}（继续显示上一次的资料，下次检查再试）", flush=True)
            traceback.print_exc()
            return
        body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        data["live"], data["stamp"] = True, hashlib.sha256(body).hexdigest()[:16]
        page = render(self.template, data)
        with self.lock:
            self.data, self.page, self.page_gz = data, page, gzip.compress(page, 6)
            self.stamp, self.built, self.error = data["stamp"], data["built"], ""
        self.watch.finished(sig)
        print(f"统计完成：{len(data['sites'])} 个网站、{sum(sum(s['t']) for s in data['sites']):,} 份文件，"
              f"资料库 {data['built']} 建，用了 {time.monotonic() - t0:.1f} 秒", flush=True)

    def loop(self, interval: float) -> None:
        while True:
            self.tick()
            time.sleep(interval)


def handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        server_version = "nknu-overview"

        def log_message(self, *args) -> None:  # one line per request would drown the useful lines
            pass

        def _send(self, code: int, body: bytes, ctype: str, gz: bytes | None = None) -> None:
            use_gz = gz is not None and "gzip" in (self.headers.get("Accept-Encoding") or "")
            out = gz if use_gz else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if use_gz:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(out)

        def do_GET(self) -> None:
            if not host_ok(self.headers.get("Host")):
                self._send(403, "只回答用本機名字連進來的請求".encode(), "text/plain; charset=utf-8")
                return
            path = self.path.split("?", 1)[0]
            with state.lock:
                page, page_gz, data = state.page, state.page_gz, state.data
                err = state.io_error or state.error
                stamp = {"stamp": state.stamp, "built": state.built, "state": "error" if err else "ok", "error": err}
            if path == "/":
                if page is None:
                    self._send(200, WAITING, "text/html; charset=utf-8")
                else:
                    self._send(200, page, "text/html; charset=utf-8", page_gz)
            elif path == "/stamp":
                self._send(200, json.dumps(stamp, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            elif path == "/data.json" and data is not None:
                self._send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            else:
                self._send(404, "沒有這一頁".encode(), "text/plain; charset=utf-8")

        do_HEAD = do_GET

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="资料总览：读实验室电脑上的正式库，页面每分钟检查一次有没有更新")
    ap.add_argument("--root", default="/work", help="仓库布局的根（实验室电脑的磁碟区 nknu-work 挂在 /work）")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8879)
    ap.add_argument("--interval", type=float, default=60, help="几秒检查一次资料库有没有换")
    args = ap.parse_args()
    template = (Path(__file__).resolve().parent / "page.html").read_text(encoding="utf-8")
    state = State(Path(args.root), template)
    threading.Thread(target=state.loop, args=(args.interval,), daemon=True).start()
    print(f"资料总览开在 {args.host}:{args.port}，每 {args.interval:g} 秒检查一次资料库", flush=True)
    ThreadingHTTPServer((args.host, args.port), handler(state)).serve_forever()


if __name__ == "__main__":
    main()
