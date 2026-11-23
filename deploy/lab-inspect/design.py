"""Design copies of the panels (2026-11-23; owner: 「我和你一起改，但是你会需要改很多次 所以有一个文件改为确定再去改程序
会比较顺畅」): every page of 檢視台 as the lab draws it now, saved as plain HTML files to change and look at in seconds;
once the owner says a page is right, the change is carried back into src/nknu/ops by hand.

    uv run --no-sync python deploy/lab-inspect/design.py capture [--out research/design_2026-11-23]
    uv run --no-sync python deploy/lab-inspect/design.py serve [--port 18995]

capture reads the live panel through the tunnel (GET only, http://127.0.0.1:18878): each page in PAGES with its real
data. The styles every page shares (theme/theme.css, look.CSS) go to shared/theme.css and shared/look.css, linked from
each page, so one change there shows on every page; a page's own styles stay in it. Links between captured pages point
at their files; any other link goes to the live panel. The folder is under research/ (not in git): the pages hold the
school's data. An existing folder is never overwritten: capture again into a new one.
serve shows the folder at http://127.0.0.1:<port>/ (index.html is 總覽).
"""

from __future__ import annotations

import argparse
import http.server
import re
import sys
import urllib.request
from functools import partial
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

LIVE = "http://127.0.0.1:18878"
DEFAULT_OUT = REPO / "research" / "design_2026-11-23"
# (file, path on the panel): every kind of page once, with real data
PAGES = (
    ("index.html", "/"),
    ("tonight.html", "/tonight"),
    ("sites.html", "/sites"),
    ("units.html", "/units"),
    ("site.html", "/site?site=c.nknu.edu.tw%2Faca"),
    ("try.html", "/try"),
    ("why.html", "/try?tab=why&qid=Q01"),
    ("check.html", "/check"),
    ("page-check.html", "/check?url=https%3A%2F%2Fc.nknu.edu.tw%2Faca%2Fsitemap.aspx"),
    ("doc.html", "/doc"),
    ("doc-found.html", "/doc?q=%E4%BC%91%E5%AD%B8"),
    ("trace.html", "/trace?ref=dupohh4cy6mbyi"),
)


def fetch(path: str) -> str:
    req = urllib.request.Request(LIVE + path, headers={"Host": "127.0.0.1:18878", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8")


def local_links(page: str) -> str:
    """href / action / src of the panel's own paths: a captured page's file, else the live panel."""
    files = {path: name for name, path in PAGES}
    files.update({path.replace("%2F", "/").replace("%3A", ":"): name for name, path in PAGES})

    def swap(m: re.Match) -> str:
        attr, quote, url = m.group(1), m.group(2), m.group(3)
        if not url.startswith("/") or url.startswith("//"):
            return m.group(0)
        bare, _, anchor = url.partition("#")
        target = files.get(bare.replace("&amp;", "&"))
        new = (target + (f"#{anchor}" if anchor else "")) if target else LIVE + url
        return f"{attr}={quote}{new}{quote}"

    return re.sub(r"""\b(href|action|src)=(['"])([^'"]*)\2""", swap, page)


def capture(out: Path) -> None:
    from nknu.ops.look import CSS as LOOK_CSS
    from nknu.ops.theme.bar import theme_css

    if out.exists():
        raise SystemExit(f"已经有 {out}：换一个资料夹名（--out），不覆盖改过的设计稿")
    (out / "shared").mkdir(parents=True)
    theme = theme_css()
    (out / "shared" / "theme.css").write_text(theme, encoding="utf-8")
    (out / "shared" / "look.css").write_text(LOOK_CSS, encoding="utf-8")
    links = ("<link rel='stylesheet' href='shared/theme.css'>", "<link rel='stylesheet' href='shared/look.css'>")
    for name, path in PAGES:
        page = fetch(path)
        shared = []
        if theme in page:
            page = page.replace(theme, "", 1)
            shared.append(links[0])
        if LOOK_CSS in page:
            page = page.replace(LOOK_CSS, "", 1)
            shared.append(links[1])
        head = "".join(shared)
        if "<head>" in page:
            page = page.replace("<head>", "<head>" + head, 1)
        else:  # the shell's pages start with <meta>: the links go first
            page = re.sub(r"(<html[^>]*>)", lambda m, head=head: m.group(1) + head, page, count=1)
        # a page caught while the panel was reading again reloads itself every few seconds (pipeline_page.AGAIN):
        # a design copy never does
        page = re.sub(r"const u=new URL\(location\.href\);u\.searchParams\.delete\('fresh'\);u\.hash='';"
                      r"setTimeout\(\(\)=>location\.replace\(u\.toString\(\)\),\d+\)", "", page)
        page = local_links(page)
        (out / name).write_text(page, encoding="utf-8")
        print(f"  {name:16} ← {path}（共用样式 {len(shared)} 份）")
    print(f"抓好了：{out}")


class Fresh(http.server.SimpleHTTPRequestHandler):
    """Every file sent as never to be kept: a design copy changed a moment ago shows on the next reload."""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(folder: Path, port: int) -> None:
    handler = partial(Fresh, directory=str(folder))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"设计稿：http://127.0.0.1:{port}/", flush=True)
        httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="面板的设计稿：从正式面板抓下来、在本机看")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="抓正式面板（经隧道，只读）存成设计稿")
    c.add_argument("--out", type=Path, default=DEFAULT_OUT)
    s = sub.add_parser("serve", help="在本机开设计稿")
    s.add_argument("--dir", type=Path, default=DEFAULT_OUT)
    s.add_argument("--port", type=int, default=18995)
    args = ap.parse_args(argv)
    if args.cmd == "capture":
        capture(args.out)
    else:
        serve(args.dir, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
