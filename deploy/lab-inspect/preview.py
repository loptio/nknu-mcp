"""檢視台 on the Mac, for page design while the lab PC runs its night (2026-11-22, owner: 「现在实验室电脑在跑，可以先在
mac上改ui ux设计吗」).

The pages are drawn by this checkout's code (nknu.ops), from a snapshot of what the lab's 檢視台 had read, so a change
to a page shows at once with the lab's real numbers, and nothing on the lab is restarted or written:

    uv run --no-sync python deploy/lab-inspect/preview.py capture      # through the tunnel (18878) and ssh: read only
    uv run --no-sync python deploy/lab-inspect/preview.py serve        # http://127.0.0.1:18990/, reloads on a change

capture keeps, in research/dashboard_preview/<time>/: /api/pipeline (the steps as last read), /api/tonight (the nights
and the last swap's comparison), /sites/data.json (網站), the ends of the seven logs, and a copy of the lab's ops.db
(the nights' record and the lists of problems: copied by sqlite's backup inside a throwaway container, the volume
mounted read only). serve draws 每晚運行 and 網站 from the newest snapshot; every other page and request is passed to
the lab's 檢視台 as it is (read only: GET), so those pages are the lab's, not this checkout's, until they get a
snapshot of their own. A line on top of every page drawn here says it is a preview and of when. 【Claude】
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SNAPS = REPO / "research" / "dashboard_preview"
LAB = os.environ.get("NKNU_PREVIEW_LAB", "http://127.0.0.1:18878")  # the lab's 檢視台 through the Mac's tunnel
PORT = 18990
JSONS = {"pipeline.json": "/api/pipeline", "tonight.json": "/api/tonight", "history.json": "/api/history",
         "sites.json": "/sites/data.json"}
LOGS = ("update", "export", "laneA", "laneB", "laneC", "build", "ingest")
OPS_COPY = r"""import collections, json, shutil, sqlite3
src = sqlite3.connect('file:/ops/ops.db?mode=ro', uri=True)
dst = sqlite3.connect('/out/ops-preview.db')
src.backup(dst)
dst.close()
try:
    shutil.copyfile('/ops/eval194.json', '/out/eval194-preview.json')
except OSError:
    pass
# the public service's calls a day: counts only, no query leaves the lab
calls, searches, first = collections.Counter(), collections.Counter(), None
for name in ('/public-log/queries.jsonl.1', '/public-log/queries.jsonl'):
    try:
        f = open(name, encoding='utf-8')
    except OSError:
        continue
    with f:
        for raw in f:
            try:
                x = json.loads(raw)
                day = x['t'][:10]
            except (ValueError, KeyError, TypeError):
                continue
            first = min(first or day, day)
            calls[day] += 1
            searches[day] += int(x.get('tool') == 'search' and not x.get('error'))
with open('/out/usage-days-preview.json', 'w') as f:
    json.dump({'first': first, 'calls': calls, 'searches': searches}, f)
print('ok')"""
# the real-AI test's verdicts (2026-11-22, the 批改 table of this report), for 總覽's draft
AI_TEST = REPO / "research" / "eval_2026-11-18" / "runs" / "strata_full_1122.md"


def _get(path: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(LAB + path, headers={"Host": "127.0.0.1:18878"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def capture() -> Path:
    at = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = SNAPS / at
    out.mkdir(parents=True, exist_ok=True)
    for name, path in JSONS.items():
        (out / name).write_bytes(_get(path))
        print(f"  {name} ← {path}")
    logs = {}
    for name in LOGS:
        try:
            logs[name] = json.loads(_get(f"/api/log?name={name}"))
        except (urllib.error.URLError, ValueError) as exc:
            logs[name] = None
            print(f"  log {name}: 读不到（{exc}）")
    (out / "logs.json").write_text(json.dumps(logs, ensure_ascii=False), encoding="utf-8")
    script = out / "ops_copy.py"
    script.write_text(OPS_COPY + "\n", encoding="utf-8")
    subprocess.run(["scp", "-q", str(script), "nknu-lab:C:/nknu-transfer/inspect/ops_copy.py"], check=True, timeout=60)
    script.unlink()
    cmd = ('set "DOCKER_CONFIG=%USERPROFILE%\\.docker-ssh" & docker run --rm --network none -v nknu-ops:/ops:ro '
           '-v C:\\nknu-transfer\\public\\log:/public-log:ro '
           '-v C:\\nknu-transfer\\inspect:/out nknu-pipeline python3 /out/ops_copy.py '
           '& del C:\\nknu-transfer\\inspect\\ops_copy.py')
    got = subprocess.run(["ssh", "nknu-lab", cmd], capture_output=True, text=True, timeout=120, check=False)
    if "ok" not in got.stdout:
        print(f"  ops.db 没抄成：{got.stderr.strip()[:300]}", file=sys.stderr)
    else:
        subprocess.run(["scp", "-q", "nknu-lab:C:/nknu-transfer/inspect/ops-preview.db", str(out / "ops.db")],
                       check=True, timeout=120)
        got = subprocess.run(["scp", "-q", "nknu-lab:C:/nknu-transfer/inspect/eval194-preview.json",
                              str(out / "eval194.json")], timeout=60, check=False)
        days = subprocess.run(["scp", "-q", "nknu-lab:C:/nknu-transfer/inspect/usage-days-preview.json",
                               str(out / "usage-days.json")], timeout=60, check=False)
        subprocess.run(["ssh", "nknu-lab", ("del C:\\nknu-transfer\\inspect\\ops-preview.db "
                                            "C:\\nknu-transfer\\inspect\\eval194-preview.json "
                                            "C:\\nknu-transfer\\inspect\\usage-days-preview.json")],
                       timeout=60, check=False)
        print("  ops.db ← 实验室 nknu-ops 磁碟区（只读挂，sqlite backup）"
              + ("；eval194.json 也抄了" if got.returncode == 0 else "；没有 eval194.json")
              + ("；对外服务每天的查询次数也数了" if days.returncode == 0 else ""))
    print(f"抓好了：{out}")
    return out


def ai_test() -> dict | None:
    """The real-AI test's verdicts as aitest_summary.py writes them for the lab (/ops/aitest.json), from the report."""
    sys.path.insert(0, str(Path(__file__).parent))
    from aitest_summary import summary

    try:
        return summary(AI_TEST.read_text(encoding="utf-8"), str(AI_TEST.relative_to(REPO)))
    except OSError:
        return None


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def fill_nums(snap: dict, days: dict | None, today: str) -> dict:
    """A snapshot from a lab that does not run pipeline's numbers yet (Step.nums, 2026-11-22) has them in words only:
    read them back here, for the preview alone, until the lab's 檢視台 is reinstalled. The page itself reads numbers.
    days: capture's count of the query log a day (usage-days.json), today: the snapshot's day."""
    steps = {s["key"]: s for s in snap.get("steps") or []}
    serve, check = steps.get("serve") or {}, steps.get("check") or {}
    if not serve.get("nums") and serve:
        nums: dict = {}
        box = []
        for name, tone, words in serve.get("sides") or []:
            d = re.search(r"還剩\s*([\d,.]+)\s*GB（共\s*([\d,.]+)\s*GB", words)
            u = re.search(r"用了\s*([\d,.]+)\s*GB（共\s*([\d,.]+)\s*GB", words)
            sw = re.search(r"交換區用了\s*([\d.]+)\s*GB", words)
            used, total = ((float(d.group(2).replace(",", "")) - float(d.group(1).replace(",", "")),
                            float(d.group(2).replace(",", ""))) if d
                           else (float(u.group(1)), float(u.group(2))) if u else (None, None))
            box.append({"name": name, "tone": tone, "used": used, "total": total,
                        **({"swap": float(sw.group(1))} if sw else {})})
        nums["machine"] = box
        rows = {r[0]: r[2] for r in serve.get("usage") or []}
        said = rows.get("查詢", "")
        if m := re.match(r"([\d,]+) 次", said):
            def count(word: str, text: str = said) -> int:
                got = re.search(rf"{word} ([\d,]+)", text)
                return _int(got.group(1)) if got else 0

            speed = rows.get("多慢", "")
            mid = re.search(r"一半在\s*([0-9.]+)\s*秒", speed)
            slow = re.search(r"最慢的一成要\s*([0-9.]+)\s*秒", speed)
            failed = rows.get("沒答成", "")
            errors = {k: count(w, failed) for k, w in (("bad_args", "參數不對"), ("busy", "排隊太久"),
                                                         ("unavailable", "庫讀不到"), ("internal", "程式出錯"))}
            errors = {k: v for k, v in errors.items() if v}
            last = said.split("最後一次 ")[-1] if "最後一次" in said else ""
            usage = {"hours": 24, "calls": _int(m.group(1)),
                     "tools": {"search": count("搜尋"), "fetch": count("讀全文")},
                     "via": {"mcp": count("MCP"), "web": count("網頁"), "api": count("接口")},
                     "searches": count("搜尋"), "missed": _int(re.match(r"([\d,]+)", rows.get("查無資料", "0")).group(1)),
                     "mid_ms": int(float(mid.group(1)) * 1000) if mid else None,
                     "slow_ms": int(float(slow.group(1)) * 1000) if slow else None,
                     "failed": sum(errors.values()), "failed_ours": sum(v for k, v in errors.items() if k != "bad_args"),
                     "errors": errors, "kept_failures": True,
                     "last": f"{today[:4]}-{last.replace(' ', 'T')}:00" if last else ""}
            if days:
                ds = [(datetime.fromisoformat(today) - timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
                usage["days"] = [[d, *((None, None) if days.get("first") and d < days["first"]
                                       else (days["calls"].get(d, 0), days["searches"].get(d, 0)))] for d in ds]
            nums["usage"] = usage
        serve["nums"] = nums
    if not check.get("nums") and check:
        say = check.get("say") or ""
        if m := re.search(r"排第 1 的 (\d+) 題、前 3 的 (\d+) 題、前 10 的 (\d+) 題", say):
            n = int((check.get("into") or [["", 33]])[0][1])
            check["nums"] = {"n": n, "top1": int(m.group(1)), "top3": int(m.group(2)), "top10": int(m.group(3))}
            if r := re.search(r"真資料檢查 (\d+) 項過了 (\d+) 項", say):
                check["nums"].update(real_passed=int(r.group(2)), real_total=int(r.group(1)))
    return snap


def newest() -> Path:
    snaps = sorted(p for p in SNAPS.glob("*") if (p / "pipeline.json").exists())
    if not snaps:
        raise SystemExit("还没有快照：先跑 preview.py capture")
    return snaps[-1]


# ------------------------------------------------------------------------------------------------ the server


def make_app():
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, Response
    from starlette.routing import Route

    from nknu.ops import (
        charts,
        check_page,
        check_sites,
        content_band,
        doc_page,
        history,
        home_page,
        look,
        nightly,
        overview,
        panorama,
        pipeline_page,
        search_page,
        site_page,
        standing,
        todo,
        tonight_page,
        try_page,
    )
    from nknu.ops.home_page import home_body
    from nknu.ops.look import e, shell
    from nknu.ops.theme import bar as themebar

    snap_dir = Path(os.environ.get("NKNU_PREVIEW_SNAP") or newest())
    taken = datetime.strptime(snap_dir.name, "%Y%m%d-%H%M%S").strftime("%m-%d %H:%M")
    read = lambda name: json.loads((snap_dir / name).read_text(encoding="utf-8"))
    def preview_banner(active: str) -> str:
        """The preview's line: what this is and of when, and one site's page to look at (the bar has the rest)."""
        sample = ("" if active == "site" else
                  " · <a href='/site?site=c.nknu.edu.tw%2Faca' style='color:inherit'>看一個網站的頁面</a>")
        return (
            "<div style='padding:4px max(var(--gutter,16px),calc((100% - var(--page,1440px))/2 + var(--gutter,16px)));"
            "background:var(--warn-bg);color:var(--warn-ink);font:12px/1.4 var(--font,system-ui)'>"
            f"預覽 · 快照 {e(taken)}{sample}</div>"
        )

    def with_banner(html: str, active: str = "") -> str:
        """The line on top: under the bar (which stays on top as the page scrolls); before the page's own content
        where it has no bar."""
        bar_html = preview_banner(active)
        if "class='nk-bar'" in html:
            return re.sub(r"(<header class='nk-bar'>.*?</header>(?:<script>.*?</script>)*)",
                          lambda m: m.group(1) + bar_html, html, count=1, flags=re.DOTALL)
        if re.search(r"<body[^>]*>", html):
            return re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + bar_html, html, count=1)
        return bar_html + html

    def new_bar(html: str, active: str) -> str:
        """A lab page's bar is the lab's installed one: swapped for this checkout's (its library time kept)."""
        meta = re.search(r"class='nk-meta'>(<span[^>]*>[^<]*</span>)</span>", html)
        return re.sub(r"<header class='nk-bar'>.*?</header>(?:<script>.*?</script>)*",
                      lambda _m: themebar.bar(active, "ops", meta.group(1) if meta else ""), html, count=1,
                      flags=re.DOTALL)

    def framed(resp: HTMLResponse, active: str = "") -> HTMLResponse:
        return HTMLResponse(with_banner(resp.body.decode("utf-8"), active), status_code=resp.status_code)

    def _upgrade_check_figs(html: str) -> str:
        """Inject local severity data-k attributes and compact mark legend into proxied .ck-figs cards."""
        fig_keys = {"正文區有漏": "gap", "選單沒抓到": "menu_links", "要查": "check", "沒抓下來": "nofetch"}
        for label, key in fig_keys.items():
            html = html.replace(
                f"class='ck-fig' data-tip-h='{label}'",
                f"class='ck-fig' data-k='{key}' data-tip-h='{label}'",
            ).replace(
                f"class='ck-fig z' data-tip-h='{label}'",
                f"class='ck-fig z' data-k='{key}' data-tip-h='{label}'",
            )
        if "class='ck-figs'" in html and "class='sv-key'" not in html:
            key_line = (
                "<div class='sv-key' aria-hidden='true'>"
                "<span><i class='sw' data-b='failed'></i>漏抓／失敗</span>"
                "<span><i class='sw' data-b='missing'></i>帳本沒有</span>"
                "<span><i class='sw' data-b='review'></i>待查</span>"
                "<span><i class='sw' data-b='next'></i>留到下一輪</span></div>"
            )
            html = re.sub(r"(<div class='ck-figs'>.*?</div>)(?=</section>)", lambda m: m.group(1) + key_line, html, count=1)
        return html

    def _upgrade_proxied(path: str, query: str, html: str) -> str:
        """Apply this checkout's local CSS and structural upgrades onto a read-only proxied lab HTML page."""
        # 1. Swap shell THEME + look.CSS (the first <style> block)
        html = re.sub(r"<style>.*?</style>", lambda _m: f"<style>{look.THEME}{look.CSS}</style>", html, count=1, flags=re.DOTALL)
        # 2. Swap page-specific <style> blocks inside <main>
        if "<main>" in html:
            pre, post = html.split("<main>", 1)
            if path == "/site":
                site_css = f"{charts.CSS}{pipeline_page.CSS}{pipeline_page.SITE_CSS}{tonight_page.CSS}{home_page.CSS}{site_page.CSS}"
                chk_css = f"{check_page.CHECK_CSS}{check_sites.SITES_CSS}"
                styles = iter([site_css, chk_css])
                post = re.sub(r"<style>.*?</style>", lambda _m: f"<style>{next(styles, '')}</style>", post, count=2, flags=re.DOTALL)
                # Move legacy .hm-does prose into the compact (?) info button beside <h3>
                post = re.sub(
                    r"<header><h3>([^<]+)</h3>(<span class='pill [^']+'>[^<]+</span>)</header><p class='hm-does'>([^<]+)</p>",
                    r"<header><h3>\1</h3><button type='button' class='hm-info' aria-label='\1說明' data-tip='\3'>?</button>\2</header>",
                    post,
                )
                post = _upgrade_check_figs(post)
            elif path == "/try":
                post = re.sub(r"<style>.*?</style>", lambda _m: f"<style>{try_page.CSS}</style>", post, count=1, flags=re.DOTALL)
                if "id='newset'" in post and (snap_dir / "eval194.json").exists():
                    t = read("tonight.json")
                    built = ((t.get("changes") or {}).get("new") or {}).get("built_at") or ""
                    fresh_ns = search_page.newset_html(read("eval194.json"), built)
                    if fresh_ns:
                        post = re.sub(r"<h2 class='sec' id='newset'.*?</section>", lambda _m: fresh_ns, post, count=1, flags=re.DOTALL)
            elif path == "/doc":
                doc_css = f"{check_page.CHECK_CSS}{check_sites.SITES_CSS}{doc_page.CSS}"
                post = re.sub(r"<style>.*?</style>", lambda _m: f"<style>{doc_css}</style>", post, count=1, flags=re.DOTALL)
            elif path == "/check":
                if "url=" in query or "ref=" in query or "id=" in query:
                    wide = check_page.WIDE_CSS if "class='chk-frame'" in post else ""
                    post = re.sub(r"<style>.*?</style>", lambda _m: f"<style>{wide}{check_page.CHECK_CSS}</style>", post, count=1, flags=re.DOTALL)
                else:
                    chk_css = f"{check_page.CHECK_CSS}{check_sites.SITES_CSS}{doc_page.CSS}"
                    post = re.sub(r"<style>.*?</style>", lambda _m: f"<style>{chk_css}</style>", post, count=1, flags=re.DOTALL)
                    post = _upgrade_check_figs(post)
            html = pre + "<main>" + post
        active = {"/site": "site", "/try": "try", "/doc": "doc", "/trace": "doc", "/check": "check",
                  "/units": "units"}.get(path, "")
        return with_banner(new_bar(html, active), active)

    def home(request: Request) -> Response:
        snap, t = read("pipeline.json"), read("tonight.json")
        changes = t.pop("changes", None)
        store = history.Store(snap_dir / "ops.db") if (snap_dir / "ops.db").exists() else history.Store(None)
        hist = history.home_history(store, nightly.night_of(datetime.fromisoformat(t["now"])))
        base = store.problem_base(t["default"])
        note = ""
        if base is None and store.enabled:
            note = ("問題清單從這一晚開始記；下一晚跑完，這裡會分成新出現的、之前就有的、好了的"
                    if store.problems_kept(t["default"]) else "這一晚的問題清單還沒記下（這一晚跑完、各步驟讀過一次以後才記）")
        panels = {"overview": "/sites", "board": "/try#students", "check": "/check", "tables": "http://127.0.0.1:18001"}
        body = home_body(snap, t, changes, hist, panels, "/sites", base=base, base_note=note,
                         now=datetime.fromisoformat(t["now"]))
        built = ((changes or {}).get("new") or {}).get("built_at") or ""
        return framed(shell("每晚運行 · 檢視台（預覽）", body, {"built_at": built}, tab="tonight"), "tonight")

    def overview_home(request: Request) -> Response:
        """總覽, the front page (nknu.ops.panorama), as web.py's front() draws it, from the snapshot."""
        newset = read("eval194.json") if (snap_dir / "eval194.json").exists() else None
        days = read("usage-days.json") if (snap_dir / "usage-days.json").exists() else None
        snap = fill_nums(read("pipeline.json"), days, snap_dir.name[:4] + "-" + snap_dir.name[4:6] + "-" + snap_dir.name[6:8])
        t = read("tonight.json")
        built = ((t.get("changes") or {}).get("new") or {}).get("built_at") or ""
        student = next((json.loads(f.read_text(encoding="utf-8")) for f in (
            snap_dir / "student_eval.json", REPO / "research/eval_2026-11-18/runs/student_eval_1122.json") if f.exists()),
            None)
        changes = t.get("changes")
        store = history.Store(snap_dir / "ops.db") if (snap_dir / "ops.db").exists() else history.Store(None)
        rows = tonight_page.problems(t, snap, tonight_page.handle_rows(snap, changes))
        parts = standing.split(rows, store.problem_base(t["default"]),
                               swap_known=(changes or {}).get("state") != "computing")
        todo_rows = todo.collect(parts, rows, panorama.machine_meters(snap), student, {})
        page = panorama.page(snap, t, read("sites.json"), read("history.json"), newset=newset, aitest=ai_test(),
                             built=built, todo=todo_rows, student=student)
        return HTMLResponse(with_banner(page, "home"))

    def sites(request: Request) -> Response:
        template = Path(overview.__file__).with_name("overview_page.html").read_text(encoding="utf-8")
        page = overview.render(template, read("sites.json")).decode("utf-8")
        # the band web.sites_page draws on top (the unit tree needs the library: the lab's /units has it)
        band = content_band.band(read("sites.json"), read("pipeline.json"), read("tonight.json"), None)
        page = page.replace('<main class="app">', '<main class="app">' + band, 1)
        return HTMLResponse(with_banner(page, "sites"))

    def doc_local(request: Request) -> Response:
        """Render /doc empty state locally with this checkout's doc_page; proxy non-empty queries with local CSS."""
        if request.query_params.get("q", "").strip():
            return passed(request)
        t = read("tonight.json")
        built = ((t.get("changes") or {}).get("new") or {}).get("built_at") or ""
        body = doc_page.doc_body("", doc_page.empty_html())
        return framed(shell("文件搜尋 · 檢視台（預覽）", body, {"built_at": built}, tab="doc"), "doc")

    def sites_data(request: Request) -> Response:
        return JSONResponse(read("sites.json"))

    def stamp(request: Request) -> Response:
        return JSONResponse({"state": "ok", "error": "", "built": "", "preview": snap_dir.name})

    def log(request: Request) -> Response:
        got = read("logs.json").get(request.query_params.get("name", ""))
        return JSONResponse(got or {}, status_code=200 if got else 404)

    def passed(request: Request) -> Response:
        """Proxy live lab pages (GET only) and overlay this checkout's local styles and preview banner."""
        path, query = request.url.path, request.url.query
        url = path + (f"?{query}" if query else "")
        try:
            req = urllib.request.Request(LAB + url, headers={"Host": "127.0.0.1:18878"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "text/html")
                if ctype.startswith("text/html") and path in ("/site", "/try", "/doc", "/trace", "/check", "/units"):
                    return HTMLResponse(_upgrade_proxied(path, query, raw.decode("utf-8", errors="replace")),
                                        status_code=resp.status)
                return Response(raw, status_code=resp.status, media_type=ctype)
        except urllib.error.HTTPError as exc:
            return Response(exc.read(), status_code=exc.code, media_type=exc.headers.get("Content-Type", "text/plain"))
        except urllib.error.URLError as exc:
            return HTMLResponse(f"{preview_banner('')}<p style='padding:16px'>連不上實驗室的檢視台（隧道開著嗎？）：{e(str(exc))}</p>",
                                status_code=502)

    routes = [Route("/", overview_home), Route("/tonight", home), Route("/sites", sites), Route("/doc", doc_local),
              Route("/sites/data.json", sites_data), Route("/sites/stamp", stamp), Route("/api/log", log),
              Route("/{rest:path}", passed, methods=["GET"])]
    return Starlette(routes=routes)


def serve(port: int) -> None:
    import uvicorn

    os.environ.setdefault("NKNU_PREVIEW_SNAP", str(newest()))
    print(f"预览：http://127.0.0.1:{port}/  （快照 {Path(os.environ['NKNU_PREVIEW_SNAP']).name}；改 src/nknu/ops 会自己重载）")
    uvicorn.run("preview:app_factory", factory=True, host="127.0.0.1", port=port, reload=True,
                reload_dirs=[str(REPO / "src" / "nknu" / "ops"), str(Path(__file__).parent)],
                app_dir=str(Path(__file__).parent), log_level="warning")


def app_factory():
    sys.path.insert(0, str(REPO / "src"))
    return make_app()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="preview.py", description="在 Mac 上用实验室的资料快照预览检视台的页面")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capture", help="经隧道和 ssh 只读地抓一份快照")
    s = sub.add_parser("serve", help="用最新的快照开预览")
    s.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args(argv)
    if args.cmd == "capture":
        capture()
    else:
        serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
