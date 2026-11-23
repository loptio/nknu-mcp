"""`nknu` command line: check (before a push), archive (the crawler), and the inspection tools' search and trace."""

from __future__ import annotations

import re

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="NKNU 校园资讯：爬虫、推送前检查、检视工具")

# one line of `nknu archive run --only`'s file: a url id as monitor and export write it (u50251), or bare (50251)
ONLY_ID = re.compile(r"[uU]?(\d+)")


@app.callback()
def main() -> None:
    """NKNU 校园资讯：爬虫、推送前检查、检视工具。"""


def _settings():
    from nknu.settings import load_settings

    return load_settings()


@app.command()
def check() -> None:
    """授权扫描、个资扫描、禁止关闭 TLS 验证、启动断言。"""
    from nknu.admin.checks import run_all_checks

    problems = run_all_checks(_settings())
    for p in problems:
        typer.echo(p)
    if problems:
        raise typer.Exit(1)
    typer.echo("all checks passed")


archive_app = typer.Typer(help="存档爬虫（v2 第一块）：礼貌地把学校网页原样存成 WARC，之后再处理")
app.add_typer(archive_app, name="archive")


def _archive_root(path: str | None):
    from pathlib import Path

    from nknu.settings import REPO_ROOT

    return Path(path).expanduser().resolve() if path else REPO_ROOT / "archive"


def _round1_root(dir: str | None, *, online: bool = False):
    """The archive directory of a command that writes to frontier.db (seed, run, retry): never the 10-24 crawler's
    archive, which is kept as it is (design §3, record 二·8) and whose crawl is not resumed (CLAUDE.md: 全新重爬). Its
    frontier.db has no round mark (frontier.round_of), and opening it to write would add the new columns and tables;
    run would clear its STOP and go on with the old crawl. Without --dir that is the repository's archive/ until it is
    renamed archive-2026-10-24. Review 10-26; 【Claude 的执行办法】."""
    from nknu.archive.frontier import rehearsal_of, round_of

    root = _archive_root(dir)
    db = root / "frontier.db"
    if db.exists() and round_of(db) is None:
        typer.echo(f"{root} 是 10-24 旧爬虫的存档（frontier.db 没有第一轮的标记）：第一轮不在旧存档上续跑，也不改它。"
                   "先照设计第 3 节把它改名成 archive-2026-10-24/（改名前先问用户），或用 --dir 指定新的存档目录。")
        raise typer.Exit(1)
    if online and rehearsal_of(db) is not None:  # online: the command may send requests (or look names up)
        typer.echo(f"{root} 是离线彩排的资料夹（设计第 18.1 节；主机地址是旧存档的或假的）：会连网的命令不在它上面跑。"
                   "要看它的结果用 status、browse。")
        raise typer.Exit(1)
    return root


def _archive_db(root):
    """The archive's frontier.db, which must already exist: only seed and run create one (a mistyped --dir is not an
    empty archive)."""
    db = root / "frontier.db"
    if not db.exists():
        typer.echo(f"找不到待抓清单：{db}（目录打错了？只有 nknu archive seed 或 run 会建立它）")
        raise typer.Exit(1)
    return db


def _hold_for_writing(root):
    """A command that writes to frontier.db (seed, retry) refuses while a run holds the directory, and holds run.lock
    itself while it writes, so that no run starts meanwhile (design §6.9: one writer; second review 10-26: they only
    looked when they started). Returns the lock, for the caller to close."""
    from nknu.archive.crawler import AlreadyRunning, hold_run_lock

    try:
        return hold_run_lock(root)
    except AlreadyRunning as exc:
        typer.echo(f"archive run 正在用这个目录（pid {exc.pid}）：先 nknu archive stop，改完再 run（续跑不会重抓已抓过的）。")
        raise typer.Exit(1) from None


@archive_app.command("seed")
def archive_seed(round_: int = typer.Option(1, "--round", help="哪一轮的起点（现在只有 1）"),
                 old: str = typer.Option(..., "--old", help="旧存档的目录（或它的 frontier.db），只读，例如 "
                                                         "archive-2026-10-24"),
                 dir: str = typer.Option(None, "--dir", help="新存档目录，默认是仓库里的 archive/"),
                 cc: str = typer.Option(None, "--cc", help="Common Crawl 网址索引的目录（*.cdx）；默认 research 里那份"),
                 probe: str = typer.Option(None, "--probe", help="网站普查 data.json（52 个校务系统起点）；默认 research 里那份")
                 ) -> None:
    """建第一轮的待抓清单（设计第 5 节）：名单的网站、附加起点、登录重查，旧存档的每一行和它链接表的目标（照新规则重判），
    Common Crawl、SiteMap、v1 已知公告、SSO 公告列表，第二轮的起点（分批记下）。可重复执行，已有的不会重复加。旧存档只读、
    绝不改。写 reports/seed-<时间>.md；有网站的起点比旧存档少，退出码 2 并列出少了哪些。"""
    from datetime import datetime
    from pathlib import Path

    from nknu.archive import crawler as archive
    from nknu.archive import round1, seeds

    if round_ != 1:
        typer.echo("现在只有第一轮的起点（--round 1）")
        raise typer.Exit(1)
    root = _round1_root(dir, online=True)
    old_path = Path(old).expanduser().resolve()
    old_db = old_path if old_path.is_file() else old_path / "frontier.db"
    if not old_db.exists():
        typer.echo(f"找不到旧存档的待抓清单：{old_db}")
        raise typer.Exit(1)
    if old_db.parent.resolve() == root.resolve():
        typer.echo("--old 和 --dir 是同一个目录：新的一轮要另开目录，旧存档只读")
        raise typer.Exit(1)
    lock = _hold_for_writing(root)  # held while seeding: no run may start meanwhile (design §6.9: one writer)
    try:
        crawler = archive.Crawler(root, plan=round1.load(), resolver=archive.resolve)
        report = seeds.seed_round1(crawler, old=old_path, facts_db=_settings().facts_path,
                                   cc_dir=Path(cc) if cc else seeds.CC_DIR, probe=Path(probe) if probe else seeds.PROBE)
        reports = root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        out = reports / f"seed-{datetime.now().astimezone():%Y%m%d-%H%M%S}.md"
        out.write_text(report.text(), encoding="utf-8")
        n = crawler.frontier.q("SELECT count(*) FROM url")[0][0]
    finally:
        lock.close()
    typer.echo(f"待抓清单共 {n} 行：{root / 'frontier.db'}；起点报告：{out}")
    if report.missing:
        typer.echo(f"有 {len(report.missing)} 个旧存档的网址这次没有起点（也不是被规则挡掉的），见报告「少了的网址」")
    raise typer.Exit(report.exit_code)


def _gap_option(text: str) -> tuple[float, float]:
    """「2-5」 (the default) or 「1-2」 (the owner 10-26, once the pilot passed): seconds, at least 1."""
    from nknu.archive.crawler import check_gap

    try:
        lo, hi = (float(x) for x in text.replace("–", "-").split("-", 1))
        return check_gap((lo, hi))
    except ValueError as exc:
        typer.echo(f"--gap 要写成「下限-上限」秒，例如 2-5 或 1-2，下限至少 1 秒：{exc}")
        raise typer.Exit(1) from None


_STATE_ZH = {"done": "已经抓过", "failed": "已经失败", "skipped": "已经跳过", "later": "留给第二轮", "held": "被上限挡下"}


def _ids_text(ids, at_most: int = 20) -> str:
    """u5、u6…: the first at_most of them, then how many in all."""
    ids = sorted(ids)
    head = "、".join(f"u{i}" for i in ids[:at_most])
    return head if len(ids) <= at_most else f"{head} 等 {len(ids)} 个"


def _only_option(text: str) -> frozenset[int]:
    """--only PATH (the owner 11-02): the url ids of the rows the run may fetch, one a line (u50251 or 50251);
    blank lines and lines starting with # are skipped. A file that cannot be read: exit 1; a line that is no id, or
    no id at all: exit 2, nothing started."""
    from pathlib import Path

    path = Path(text).expanduser()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        typer.echo(f"读不了 --only 的文件 {path}：{exc}")
        raise typer.Exit(1) from None
    ids: set[int] = set()
    bad: list[str] = []
    for n, line in enumerate(lines, start=1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        m = ONLY_ID.fullmatch(entry)
        if m is None:
            bad.append(f"第 {n} 行「{entry[:60]}」")
        else:
            ids.add(int(m[1]))
    if bad:
        typer.echo(f"--only 的文件 {path} 有不是网址编号的行（一行一个编号，写 u50251 或 50251；空行和 # 开头的行不算）："
                   + "、".join(bad[:10]) + (f" 等 {len(bad)} 行" if len(bad) > 10 else "") + "。没有开始抓。")
        raise typer.Exit(2)
    if not ids:
        typer.echo(f"--only 的文件 {path} 里没有网址编号。没有开始抓。")
        raise typer.Exit(2)
    return frozenset(ids)


def _only_rows(root, ids: frozenset[int]) -> dict[int, str]:
    """The listed rows' states as the url table has them now (id -> state), read without writing."""
    import json
    import sqlite3

    conn = sqlite3.connect(f"file:{root / 'frontier.db'}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT id, state FROM url WHERE id IN (SELECT value FROM json_each(?))",
                                 (json.dumps(sorted(ids)),)).fetchall())
    finally:
        conn.close()


def _only_brought(root, ids: frozenset[int]) -> list[int]:
    """The rows the listed rows brought (found_from) that still wait and are not listed themselves, read without
    writing: a Drive item's content, a redirect's target, a page's links, which a run given --only enqueues and never
    fetches (review 11-02: a listed Drive row can be the item's metadata only)."""
    import json
    import sqlite3

    listed = json.dumps(sorted(ids))
    conn = sqlite3.connect(f"file:{root / 'frontier.db'}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT id FROM url WHERE state = 'pending' AND found_from IN (SELECT value FROM json_each(?)) "
            "AND id NOT IN (SELECT value FROM json_each(?)) ORDER BY id", (listed, listed))]
    finally:
        conn.close()


def _only_check(root, ids: frozenset[int]) -> None:
    """Before a run given --only (under run.lock: no run changes the rows meanwhile): an id the url table lacks
    refuses it (exit 2, every such id named); listed rows not waiting (done, failed, left for later…) are said and
    skipped (the crawler fetches the pending ones only); none waiting: nothing is started (exit 0)."""
    got = _only_rows(root, ids)
    missing = ids - got.keys()
    if missing:
        typer.echo(f"待抓清单（{root / 'frontier.db'}）里没有这些网址编号：{_ids_text(missing)}。--only 只能列待抓清单里"
                   "已有的网址（monitor、export 写的 u 开头的编号）。没有开始抓。")
        raise typer.Exit(2)
    skipped: dict[str, list[int]] = {}
    for i, state in got.items():
        if state not in ("pending", "active"):  # active: left by a run that was killed, taken back by this one
            skipped.setdefault(state, []).append(i)
    if skipped:
        said = (f"{_STATE_ZH.get(s, s)}（{s}）{len(v)} 个：{_ids_text(v)}" for s, v in sorted(skipped.items()))
        typer.echo(f"--only 列出的 {len(ids)} 个网址里，这些不在待抓，这次跳过：" + "；".join(said))
    left = len(ids) - sum(len(v) for v in skipped.values())
    if not left:
        typer.echo("--only 列出的网址没有一个在待抓。没有开始抓。")
        raise typer.Exit(0)
    typer.echo(f"--only：这次只抓列出的 {left} 个待抓网址；这些网站的其他网址都不抓、不改。抓的时候带出的新网址"
               "（云端硬盘档案的内容、转址的目标、网页上的链接）照常记进待抓清单，这次不抓。")


@archive_app.command("run")
def archive_run(dir: str = typer.Option(None, "--dir"),
                limit: int = typer.Option(None, "--limit", help="抓这么多个页面就停（试跑用）"),
                gap: str = typer.Option("2-5", "--gap", help="每个回应之后等几秒（下限-上限）；默认 2-5，试跑通过、"
                                                           "用户说开始用以后才设 1-2"),
                sites: str = typer.Option(None, "--sites", help="只抓这些网站（网站键，逗号分隔，例如 "
                                                              "c.nknu.edu.tw/reality,sso.nknu.edu.tw/web/595）"),
                end_wait: float = typer.Option(1800.0, "--end-wait", help="剩下的网址最早也要这么多秒以后才到期时，这次 run "
                                                                       "先结束、留给下一次；默认 1800（30 分钟，比服务器"
                                                                       "固定休息的 15 分钟长）"),
                only: str = typer.Option(None, "--only", help="只抓这个文件列出的网址：一行一个 url 编号（u50251 或 "
                                                            "50251，空行和 # 开头的行不算）；这些网站的其他网址都不抓、"
                                                            "不改")) -> None:
    """开始或接着抓。每台服务器同时只连 1 条，回应结束后再等（默认 2–5 秒）；中断后重跑会接着抓。同一个目录只能有一个在跑，
    同一台机器只能有一个程序对学校发请求（全机锁）。只剩等重试的网址、而且最早也要 --end-wait 秒以后才到期时，自己结束，
    留给下一次。加 --only <文件>（用户 11-02）：只抓文件列出、还在待抓的网址，同一个网站、同一台
    服务器的其他网址都不抓、不改；列出的抓完（或只剩等重试的）就结束。编号不在待抓清单里：退出码 2，没有开始抓；
    已经抓过或失败的列出来、跳过。和 --sites 一起用时两个条件都要符合。列出的网址所在的服务器只回 http 或只回 https 时，
    抓的是另一种写法的同一个地址（设计 §6.6）。抓的时候带出的新网址（云端硬盘档案的内容、转址的目标、网页上的链接）只记进待抓清单，
    这次不抓，跑完会列出它们的编号。"""
    import logging
    import signal
    import threading
    from collections import Counter

    from nknu.archive import crawler as archive
    from nknu.archive import schoollock

    root = _round1_root(dir, online=True)
    span = _gap_option(gap)
    if end_wait <= 0:
        typer.echo("--end-wait 是秒数，要大于 0（默认 1800）")
        raise typer.Exit(1)
    site_keys = frozenset(x.strip() for x in sites.split(",") if x.strip()) if sites is not None else None
    if site_keys is not None and not site_keys:  # 11-06: a site list a script could not read is no site, never all
        typer.echo("--sites 是空的（一个网站键都没有：名单档读不到？），没有开始。要抓所有网站就不要加 --sites。")
        raise typer.Exit(1)
    ids = _only_option(only) if only is not None else None
    if ids is not None:
        _archive_db(root)  # the ids are the archive's rows: a mistyped --dir is no archive, and nothing is made there
    try:
        lock = archive.hold_run_lock(root)  # held until this process ends; the kernel lets go even after kill -9
    except archive.AlreadyRunning as exc:
        typer.echo(f"另一个 archive run 正在用这个目录（pid {exc.pid}），没有再启动。要换程序，先 nknu archive stop。")
        raise typer.Exit(1) from None
    if ids is not None:
        try:
            _only_check(root, ids)
        except typer.Exit:
            lock.close()
            raise
    try:
        school = schoollock.hold()  # one program per machine sends to the school (design §6.9)
    except schoollock.SchoolLockBusy as exc:
        lock.close()
        typer.echo(f"另一个程序正在对学校发请求（pid {exc.pid}，全机锁 {exc.path}），没有启动。等它停下再跑。")
        raise typer.Exit(1) from None
    typer.echo(f"全机锁：{school.path}（对学校发请求的程序都要用这一个档）")
    try:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
        stop_file = root / "STOP"
        stop_file.unlink(missing_ok=True)  # only the run that holds the lock clears a STOP
        (root / "run.pid").unlink(missing_ok=True)  # left by older versions; run.lock says who runs now
        crawler = archive.Crawler(root, limit=limit, gap=span, sites=site_keys, end_wait_s=end_wait, only=ids)

        def watch() -> None:
            while not crawler.stop.wait(archive.STOP_POLL_S):
                if stop_file.exists():
                    logging.getLogger("nknu.archive").info("STOP file found: finishing the requests in flight")
                    crawler.halt("stop_file")

        signal.signal(signal.SIGTERM, lambda *_: crawler.halt("sigterm"))
        threading.Thread(target=watch, daemon=True).start()
        crawler.run()
    finally:
        school.close()
        lock.close()
    typer.echo(f"archive run stopped ({crawler.end_reason or 'stop'})")
    if ids is not None:
        states = sorted(Counter(_only_rows(root, ids).values()).items())
        typer.echo(f"--only 列出的 {len(ids)} 个网址现在：" + "、".join(f"{s} {n} 个" for s, n in states))
        if brought := _only_brought(root, ids):  # a listed row done is not always its file in hand
            typer.echo(f"列出的网址带出的 {len(brought)} 个网址还在待抓，这次没抓（云端硬盘档案的内容、转址的目标、网页上的链接"
                       f"等）：{_ids_text(brought)}。要抓，把它们列进 --only 的文件再跑一次。")
    if crawler.end_reason == "waiting":
        _next_run(crawler, only)


def _next_run(crawler, only: str | None = None) -> None:
    """A run that ended with only waits left (B1) says when the soonest of them comes due: the earliest a run again
    has something to do (second review 10-26, END_WAIT_S: a person, or a timer, starts it). only: the --only file
    of the run, whose listed rows these are: the run to start again is one given it (a plain run would fetch the
    other rows of those sites too)."""
    from datetime import datetime

    left, nxt = crawler.frontier.waiting(crawler.sites, ids=getattr(crawler, "only", None) if only else None)
    if nxt is not None:
        again = f"同样加 --only {only} 的 nknu archive run（不加 --only 会连这些网站的其他网址一起抓）" if only \
            else "nknu archive run"
        typer.echo(f"{'列出的网址' if only else ''}还有 {left} 个网址在等（重试、DNS、冷却或服务器休息）；最早 "
                   f"{datetime.fromtimestamp(nxt).astimezone():%m-%d %H:%M} 起再执行一次 {again} 就会接着抓")


@archive_app.command("status")
def archive_status(dir: str = typer.Option(None, "--dir")) -> None:
    """健康报告：有没有爬错（礼貌、范围、robots）或爬漏。写进 archive/reports/，顺便重写问题记录
    reports/problems-latest.log（一行一个问题，Tab 分隔；爬虫跑完后另存一份带时间的）。有硬性违反时退出码 2。"""
    import sqlite3

    from nknu.archive import progress
    from nknu.archive.frontier import OutdatedFrontier
    from nknu.archive.monitor import report

    root = _archive_root(dir)
    _archive_db(root)
    try:
        text, code = report(root)
    except OutdatedFrontier as exc:
        typer.echo(f"{exc}。监控只读、不改资料库：先执行一次 nknu archive seed（或 archive run）升级它，再看状态。")
        raise typer.Exit(1) from None
    typer.echo(text)
    # the problem log (dashboard design 2026-11-04 §1.3): the lines the dashboard's 错 N counts; a failure to write it
    # is said and leaves the exit code report()'s
    try:
        written = progress.write_problem_log(root)
    except (OSError, sqlite3.Error, OutdatedFrontier) as exc:
        typer.echo(f"问题记录没写成（{type(exc).__name__}: {exc}）")
    else:
        latest = (root / "reports" / progress.LATEST_LOG).read_text(encoding="utf-8").splitlines()
        typer.echo(f"问题记录：{written[0]}（{latest[1].removeprefix('# ')}）")
        for path in written[1:]:
            typer.echo(f"这次 run 的问题记录另存一份：{path}")
    raise typer.Exit(code)


@archive_app.command("stop")
def archive_stop(dir: str = typer.Option(None, "--dir")) -> None:
    """请正在跑的爬虫在手上的请求做完后停下，并等到它真的停下（它放开 run.lock）才返回。"""
    import time

    from nknu.archive import crawler as archive

    root = _archive_root(dir)
    pid = archive.lock_holder(root)
    if pid is None:
        typer.echo("这个目录没有正在跑的 archive run")
        return
    (root / "STOP").write_text("stop\n")
    typer.echo(f"已请爬虫（pid {pid}）在手上的请求做完后停下，等它放开 run.lock……")
    deadline = time.monotonic() + archive.STOP_WAIT_S
    while time.monotonic() < deadline:
        if archive.lock_holder(root) is None:
            typer.echo("已停下")
            return
        time.sleep(archive.STOP_POLL_S)
    typer.echo(f"等了 {archive.STOP_WAIT_S / 60:.0f} 分钟还没停下：可能有请求在重试（最长 5 分钟）或大档在下载。"
               "再执行一次 nknu archive stop 接着等；不要另开 archive run。")
    raise typer.Exit(1)


@archive_app.command("retry")
def archive_retry(reason: str = typer.Argument(..., help="原因代码的开头，例如 http_5（所有 5xx）、timeout、connect、dns、"
                                                        "server_down（连续约 2 小时连不上的服务器）、"
                                                        "host_unreachable、robots_unreachable、host_cooldown、crash；"
                                                        "post_failed：回传失败的列表从第 1 页重走；mismatch:page：按下一页"
                                                        "回来的不是那一页的列表从第 1 页重走（人看过以后用）；click：失败的下载按钮"
                                                        "（电子系）放回再按；download：SSO 的下载按钮取回来不是档案或"
                                                        "失败的，重新 GET 列表、只按这些按钮；data：资料接口（cLv2 的"
                                                        "时间轴）读失败或读不懂的，用新的请求再读一次；unpressed：现在"
                                                        "的平台档案说要按、却没人按过的按钮，和这一学年度的选单还没送完"
                                                        "的网页（开课课表），它们所在的网页重新 GET、照现在的平台档案按"),
                  dir: str = typer.Option(None, "--dir"),
                  sites: str = typer.Option(None, "--sites", help="只给 unpressed 用：只放回这些网站的网页（网站键，逗号"
                                                                "分隔）；默认全部")) -> None:
    """把某种原因失败、跳过或在等的网址放回待抓：次数归零，主机的 robots 失败次数、冷却和 DNS 退避也一起清掉。
    已存好的网页不动，只有存成 4xx 拒绝（http_4xx）的才放回。原因开头至少 3 个字。
    post_failed：回传两次都失败的列表（listwalk 记 post_failed）从列表第 1 页的 GET 重走；click：照规则按、失败了的
    POST 下载按钮放回待按（设计第 7.4、7.5 节；都只在人看过以后用，POST 绝不自动重送）。
    mismatch:page：按「下一页」回来的不是要的那一页的列表（listwalk 记 mismatch:page），同样从第 1 页的 GET 重走。
    11-08 缺口 ⑤：c.nknu 模板的「下一頁」翻到第 6 页以后，超过 15 页的列表回来的页面上不写现在是第几页，以前的程序就停在
    第 5 页；现在照回来的页面认（还是这个列表、还有「下一頁」、页码排得到、一半以上的条目没见过），重走就能翻到底。学校
    回别的网页的（体育系相簿、师培处两个列表）重走还是一样停，备注写清楚原因。
    download：SSO 的下载按钮（单位网站的附件、书表）最后一次的结果是「应该是档案，回来不是档案」或失败的，它们所在的
    列表重新 GET 一次、只按这些按钮（详情、翻页、已经抓到档案的都不再按；表单状态取自新的 GET，不原样重送旧的 POST）；
    新的 GET 上找不到的按钮记「按到的可能是别的公告」（row_gone）要人看。档案太大的不放回（设计第 23 节 A3）。
    data：资料接口（cLv2 单位网站的时间轴，每个网站读一次）试了 3 次都失败、回来读不懂、或学校说出错的，放回待读、次数归零，
    下一次 archive run 用新的请求再读一次（只是读资料，不是回传；设计第 23.9 节 C1）。
    unpressed：已存好的网页上，现在的平台档案（照网页自己的网址认平台）说要按、却没人按过的按钮（记成「该按没按」或
    「平台档案没写决定」的），它们所在的网页放回待抓；下一次 archive run 重新 GET 这一页、用新的表单状态按（已经按好的不再按，
    旧的 POST 不原样重送）。改了平台档案、rejudge 过以后用（11-08 缺口 ②③④）。平台档案有选单规则的网页（开课课表：学年度
    选当学年度、逐个系所按「顯示資料」，11-08 缺口 ⑩、用户 11-09「现在补 115」），这一学年度的选单还没送完的也放回：还没
    送过（没有已按好、待按、正在按或按过没按成的），或者有系所选项只按过一次、没按成（按过两次都没按成的留给人看，不再放回），
    一页算一种（menu:<规则名>）；下一次 archive run 重新 GET、照当学年度按还没按好的系所。
    --sites 只放回这些网站的网页。
    和 retry 的其他原因一样，没有「只列出、不改」的模式：放回只是把网页改回待抓，下一次 archive run 才发请求；
    印出放回了几页、几种按钮。"""
    from nknu.archive.frontier import RETRY_MIN_PREFIX, Frontier

    if len(reason.strip()) < RETRY_MIN_PREFIX:
        typer.echo(f"原因开头至少要 {RETRY_MIN_PREFIX} 个字（例如 http_5、dns），免得把不相干的网址一起放回")
        raise typer.Exit(1)
    if sites is not None and reason.strip() != "unpressed":
        typer.echo("--sites 只给 unpressed 用（其他原因放回全部）")
        raise typer.Exit(1)
    site_keys = [x.strip() for x in sites.split(",") if x.strip()] if sites is not None else None
    if site_keys is not None and not site_keys:  # as archive run (11-06): a list a script could not read is no site
        typer.echo("--sites 是空的（一个网站键都没有：名单档读不到？），没有放回。要放回所有网站的就不要加 --sites。")
        raise typer.Exit(1)
    root = _round1_root(dir)
    db = _archive_db(root)
    lock = _hold_for_writing(root)
    try:
        if reason.strip() in ("post_failed", "mismatch:page", "click", "download", "data", "unpressed"):
            from nknu.archive.postback import Chains

            f = Frontier(db)
            chains = Chains(f, root)
            if reason.strip() == "post_failed":
                n = chains.retry_walks("post_failed")
                msg = f"从第 1 页重走：{n} 个回传失败的列表（列表页放回待抓，下一次 archive run 接着按）"
            elif reason.strip() == "mismatch:page":
                n = chains.retry_walks("mismatch:page")
                msg = (f"从第 1 页重走：{n} 个按下一页回来的不是要的那一页的列表（列表页放回待抓，下一次 archive run "
                       "重新 GET、接着按）")
            elif reason.strip() == "download":
                lists, buttons = chains.retry_downloads()
                msg = (f"放回重按：{lists} 个列表、{buttons} 个下载按钮（列表页放回待抓，下一次 archive run 重新 GET "
                       "列表、只按这些按钮）")
            elif reason.strip() == "unpressed":
                pages, keys = chains.retry_unpressed(site_keys)
                msg = (f"放回重按：{pages} 个网页、{keys} 种该按没按的按钮或这一学年度还没送完的选单（网页放回待抓，下一次 "
                       "archive run 重新 GET、照现在的平台档案按；一种按钮可能是一整栏，一个选单一页算一种）")
            elif reason.strip() == "data":
                n = chains.retry_data()
                msg = f"放回再读：{n} 个网站的资料接口（cLv2 时间轴；下一次 archive run 用新的请求再读一次）"
            else:
                n = chains.retry_clicks()
                msg = f"放回待按：{n} 个失败的下载按钮（下一次 archive run 再按一次）"
            f.close()
            typer.echo(msg)
            return
        n, hosts = Frontier(db).retry(reason)
    finally:
        lock.close()
    typer.echo(f"放回待抓：{n} 个网址（{hosts} 台主机一起重设）；还没查过地址的主机，下一次 archive run 开始时先查")


@archive_app.command("export")
def archive_export(dir: str = typer.Option(None, "--dir", help="存档目录，默认是仓库里的 archive/"),
                   site: str = typer.Option(..., "--site", help="网站键，例如 c.nknu.edu.tw/aca（进度看板上的网站网址）"),
                   out: str = typer.Option(..., "--out", help="导出到哪个新资料夹（不存在或空的；不能在 git 会追踪的地方，"
                                                              "放 archive-export/ 或仓库外）"),
                   reuse: str = typer.Option(None, "--reuse", help="同一个网站上一次导出的资料夹：附件和图片内容一样的"
                                                                  "（sha256、大小都对得上）直接硬链接过来，不再从存档读；"
                                                                  "对不上或链不过来的照常从存档读"),
                   old_copies: str = typer.Option(None, "--old-copies", help="10-24 旧存档的副本资料夹（research/"
                                                  "crawler_lab_2026-11-04/old_copies_1108.py 做的：index.json 和 "
                                                  "files/）：这一轮没抓到能用内容的行（学校那边没了、连不上、等实验室、不是"
                                                  "档案、跳到首页或查无资料页、内容很少、看起来像不存在）用里面的正常副本当"
                                                  "档案，结果、分类照这一轮的；索引读不了、副本的 sha256 对不上就不用，照"
                                                  "这一轮的导出"),
                   skip_same: str = typer.Option(None, "--skip-same", help="这个网站现用的那份导出：它的导出依据（资料"
                                                 "截至、存档内容、程式、旧存档副本）和这一次的都一样，就什么都不写，"
                                                 "退出码 3（导出服务用来跳过没有变动的网站）")) -> None:
    """把一个网站从存档导出成一个资料夹（给「内容优化」用）：每个网页一个档、附件和正文图片保留原档，加一份清单
    manifest.csv（网址、标题、网页上写的日期、完整度、结果……，没抓到的也列，另有 missing.csv）和中文 README.md。只读、
    不连网；爬虫在跑也能导，README 会写明导出时这个网站的状态。资料里有学生全名和学号：不能进 git、不能放公开的地方。"""
    from pathlib import Path

    from nknu.archive import export, scope

    root = _archive_root(dir)
    _archive_db(root)
    key = site.strip()
    key = scope.site_key(key) if key.startswith(("http://", "https://")) else key.strip("/")
    try:
        got = export.export_site(root, key, Path(out), reuse=Path(reuse) if reuse else None,
                                 old_copies=Path(old_copies) if old_copies else None,
                                 skip_same=Path(skip_same) if skip_same else None)
    except export.ExportRefused as exc:
        typer.echo(f"没有导出：{exc}")
        raise typer.Exit(1) from None
    except export.Unchanged as exc:
        typer.echo(f"没有变动：{exc}")
        raise typer.Exit(export.UNCHANGED) from None
    for line in got.lines:
        typer.echo(line)


@archive_app.command("ready")
def archive_ready(dir: str = typer.Option(None, "--dir", help="存档目录，默认是仓库里的 archive/"),
                  sites: str = typer.Option(None, "--sites", help="只看这些网站键，逗号隔开（默认是进度看板上的网站）"),
                  as_json: bool = typer.Option(False, "--json", help="印成 JSON（给导出服务读）")) -> None:
    """哪些网站现在可以导出（第 3 步设计第二节：网站没有待抓的网址了就导出它）：每个网站一行，写可以导出还是还没爬完
    （还有几个网址、几个按钮在排队）、进度看板上的状态、资料截至（这个网站最后一次抓到东西的时间，台北时间）。和进度
    看板同一套判断；只读、不连网，爬虫在跑也能看。"""
    import json
    import sqlite3

    from nknu.archive import export, scope

    root = _archive_root(dir)
    _archive_db(root)
    keys = None
    if sites:
        keys = [scope.site_key(k) if k.startswith(("http://", "https://")) else k.strip("/")
                for k in _site_list(sites) or []]
    try:
        got, pid = export.ready(root, keys)
    except (export.ExportRefused, sqlite3.Error) as exc:
        typer.echo(f"看不了：{exc}")
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps({"存档": str(root), "爬虫正在跑": pid is not None, "网站": [r.as_dict() for r in got]},
                              ensure_ascii=False, indent=1))
        return
    for r in got:
        typer.echo(r.line())
    can = sum(1 for r in got if r.can)
    typer.echo(f"共 {len(got)} 个网站：可以导出 {can}，不能导出 {len(got) - can}"
               + (f"；爬虫正在跑（pid {pid}）" if pid is not None else ""))


@archive_app.command("browse")
def archive_browse(dir: str = typer.Option(None, "--dir"),
                   port: int = typer.Option(8877, "--port", help="端口"),
                   host: str = typer.Option("127.0.0.1", "--host",
                                            help="在哪个位址上听：预设 127.0.0.1，只有本机连得到；实验室的 Docker 容器里用 "
                                                 "0.0.0.0，由 Docker 只从主机自己的 127.0.0.1 转进来")) -> None:
    """在本机浏览器看进度看板和抓到的每一页：进度看板（每 10 秒自动更新，只读、不拖慢爬虫）、错误记录、按单位和网站列出、
    看文字或不连网的预览、问题清单。不管在哪个位址上听，只回答用本机名字（127.0.0.1、localhost）连进来的请求。Ctrl+C 结束。"""
    import errno

    from nknu.archive.viewer import index_in_background, serve

    root = _archive_root(dir)
    if not (root / "frontier.db").exists():
        typer.echo(f"找不到 {root / 'frontier.db'}")
        raise typer.Exit(1)
    try:
        server = serve(root, port, host)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        typer.echo(f"端口 {port} 已经有程序在用：检查工具多半已经开着，直接打开 http://127.0.0.1:{port}/progress ；"
                   f"要另开一个就加 --port 8878")
        raise typer.Exit(0) from None
    typer.echo(f"进度看板：http://127.0.0.1:{port}/progress" + (f"（在 {host} 上听）" if host != "127.0.0.1" else ""))
    typer.echo(f"存档总览：http://127.0.0.1:{port}/")
    typer.echo("存档索引在背景建立（第一次要几分钟，之后只补新的部分；看板不用等它）")
    index_in_background(root, lambda added: typer.echo(f"存档索引好了：新增 {added} 笔"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


# ---------------------------------------------------------------- round 1 after the crawl: jscheck, rejudge, review


def _old_of(root, old: str | None):
    """The 10-24 archive to read (only read): --old, else the one seed noted (meta old_archive), else none."""
    from pathlib import Path

    from nknu.archive.frontier import Frontier

    if old:
        return Path(old).expanduser().resolve()
    f = Frontier(root / "frontier.db", readonly=True)
    try:
        noted = f.meta("old_archive")
    finally:
        f.close()
    return Path(noted) if noted else None


def _site_list(sites: str | None) -> list[str] | None:
    return [x.strip() for x in sites.split(",") if x.strip()] if sites else None


def _drop_secrets(names) -> None:
    """A command that opens Chrome and never needs a credential (jscheck) holds none from its start:
    the shell may have sourced google.env for `archive run` (CLAUDE.md: only the fetch service holds the key), and
    Playwright's driver takes this process's environment (Chrome itself is started without them: jscheck.chrome_env)
    【Claude 的执行办法】, review 10-26."""
    import os

    for name in names:
        os.environ.pop(name, None)


@archive_app.command("jscheck")
def archive_jscheck(dir: str = typer.Option(None, "--dir"),
                    old: str = typer.Option(None, "--old", help="10-24 旧存档（只读），给「旧类型这一轮没出现」查样本的状态；"
                                                             "默认是 seed 记下的那一份"),
                    gap: str = typer.Option("2-5", "--gap", help="每个回应之后等几秒（下限-上限），和 archive run 一样"),
                    set_: tuple[str, str] = typer.Option((None, None), "--set", metavar="类型键 ok|js|frame|click",
                                                         help="人看过以后改判一个网页类型（不发任何请求）"),
                    note: str = typer.Option(None, "--note", help="--set 的理由")) -> None:
    """爬虫停下以后，用本机 Chrome（Playwright，选用依赖组 browser）把待判定的网页类型各抽一页打开，和直接抓到的比，只为了
    判定类型（设计第 8.4 节）：不存内容，也不往待抓清单写要抓的网址。所有请求都经过我们自己的队列（每台服务器 1 条连线、
    照间隔、拿全机锁）。目录里放 STOP 档、nknu archive stop 或 SIGTERM 会在手上的页面做完后停下。
    --set <类型键> ok|js|frame|click --note …：人看过以后改判（run 在跑时拒绝）。"""
    import signal

    from nknu.archive import crawler as archive
    from nknu.archive import jscheck

    _drop_secrets(jscheck.SECRET_ENV)
    root = _round1_root(dir, online=set_ == (None, None))
    _archive_db(root)
    if set_ != (None, None):
        key, verdict = set_
        try:
            jscheck.set_verdict(root, key, verdict, note)
        except jscheck.JsCheckRefused as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        typer.echo(f"已改判：{key} → {verdict}（人工）；各页的结果会跟着变")
        return
    if note:
        typer.echo("--note 只和 --set 一起用")
        raise typer.Exit(1)
    span = _gap_option(gap)
    check = jscheck.JsCheck(root, old=_old_of(root, old), gap=span, campus_url=archive.CAMPUS_URL)  # 11-03, P33
    signal.signal(signal.SIGTERM, lambda *_: check.halt("stop", "sigterm"))
    try:
        report = check.run()
    except jscheck.JsCheckRefused as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    except jscheck.BrowserMissing as exc:
        typer.echo(f"打不开浏览器：{exc}。要先装选用依赖组 browser（Playwright {jscheck.PLAYWRIGHT_VERSION}）和本机的 "
                   "Chrome；网页类型都保持待判定。")
        raise typer.Exit(1) from None
    for line in report.lines():
        typer.echo(line)
    raise typer.Exit(0 if report.reason == "finished" else 1)


@archive_app.command("enqueue")
def archive_enqueue(listed: str = typer.Argument(..., help="人看过、要抓的网址清单（一行一个）"),
                    dir: str = typer.Option(None, "--dir")) -> None:
    """把人确认要抓的网址写进待抓清单（found_via=reconcile），下一次 run 照老方法抓。已经有了的、第二轮的、规则不抓的列出来，
    不写。run 在跑时拒绝。"""
    from pathlib import Path

    from nknu.archive import crawler as archive
    from nknu.archive import reconcile

    root = _round1_root(dir)
    _archive_db(root)
    path = Path(listed).expanduser()
    if not path.is_file():
        typer.echo(f"找不到清单：{path}")
        raise typer.Exit(1)
    try:
        got = reconcile.enqueue(root, reconcile.read_list(path))
    except archive.AlreadyRunning as exc:
        typer.echo(f"archive run（或 jscheck）正在用这个目录（pid {exc.pid}）：先 nknu archive stop，等它停下再写")
        raise typer.Exit(1) from None
    except reconcile.NotRound1 as exc:
        typer.echo(f"{exc}：只对第一轮的存档做，旧存档只读")
        raise typer.Exit(1) from None
    queued = sum(v == "queued" for _u, v in got)
    typer.echo(f"清单 {len(got)} 个网址：写进待抓清单 {queued} 个；其余（已经有了、第二轮、规则不抓）：")
    for u, v in got:
        if v != "queued":
            typer.echo(f"  {v}  {u}")


@archive_app.command("rejudge")
def archive_rejudge(sites: str = typer.Option(..., "--sites", help="要重算的网站（网站键，逗号分隔）"),
                    dir: str = typer.Option(None, "--dir")) -> None:
    """不连网重算（设计第 18.2a 节第 4 步）：从存档按位置读回这几个网站已存的网页，用现在的规则和平台档案重算资源、类型、
    记号和「页面几乎是空的」；新找到的网址写进待抓清单（found_via=rejudge），已经抓过的不重抓。run 在跑时拒绝。"""
    from nknu.archive import crawler as archive
    from nknu.archive import reconcile

    root = _round1_root(dir)
    _archive_db(root)
    only = _site_list(sites)
    if not only:
        typer.echo("--sites 要写至少一个网站键")
        raise typer.Exit(1)
    try:
        got = reconcile.rejudge(root, only)
    except archive.AlreadyRunning as exc:
        typer.echo(f"archive run 正在用这个目录（pid {exc.pid}）：先 nknu archive stop，改完再 run")
        raise typer.Exit(1) from None
    except reconcile.NotRound1 as exc:
        typer.echo(f"{exc}：rejudge 只对第一轮的存档做，旧存档只读")
        raise typer.Exit(1) from None
    typer.echo(f"重算 {len(got.sites)} 个网站：网页 {got.pages} 页、短网址 {got.shortlinks} 个；新找到的网址 {got.new_rows} 个"
               f"（下一次 archive run 抓）；R7 写回 {got.r7_rows} 行；「页面几乎是空的」新加 {got.thin_set}、拿掉 "
               f"{got.thin_cleared}")
    if got.unread:
        typer.echo(f"有 {len(got.unread)} 页按位置读不回来（存档里那一段读不出来，这几页没有重算）")


@archive_app.command("review")
def archive_review(url_id: int = typer.Argument(None, help="要标记的网址编号（不给就列出可以标记的网址）"),
                   mark: str = typer.Option(None, "--mark", help="标记：school_side（学校那边的问题，人看过）"),
                   note: str = typer.Option(None, "--note", help="为什么这样标（必填）"),
                   site: str = typer.Option(None, "--site", help="只列这个网站的"),
                   dir: str = typer.Option(None, "--dir")) -> None:
    """人看过、确认是学校那边的问题的网址（例如云端档案不公开、学校的链接坏了），标成「学校那边的问题（人看过）」（设计第
    10.2.1 节）：nknu archive review --mark school_side <编号> --note …。那个网址再抓一次，标记就作废。不给 --mark 时只列出
    可以标记的网址（只读）。run 在跑时拒绝标记。"""
    from nknu.archive import crawler as archive
    from nknu.archive import outcome, reconcile
    from nknu.archive.frontier import Frontier

    root = _round1_root(dir)
    db = _archive_db(root)
    school = outcome.RESULT_LABEL[outcome.SCHOOL_SIDE]
    if mark is None:
        f = Frontier(db, readonly=True)
        try:
            rows = reconcile.markable_rows(f, site)
        finally:
            f.close()
        typer.echo(f"可以标记的网址 {len(rows)} 个（nknu archive review --mark school_side <编号> --note …）：")
        for r in rows:
            label = outcome.RESULT_LABEL.get(r["result"], r["result"])
            typer.echo(f"  {r['id']}  {label}（{r['reason'] or '—'}，{r['status'] or '—'}）  {r['url']}")
        return
    if url_id is None:
        typer.echo("要给网址编号：nknu archive review --mark school_side <编号> --note …")
        raise typer.Exit(1)
    try:
        result = reconcile.review_mark(root, url_id, note=note or "", mark=mark)
    except archive.AlreadyRunning as exc:
        typer.echo(f"archive run 正在用这个目录（pid {exc.pid}）：先 nknu archive stop，改完再 run")
        raise typer.Exit(1) from None
    except reconcile.NotRound1 as exc:
        typer.echo(f"{exc}：标记只对第一轮的存档做")
        raise typer.Exit(1) from None
    except ValueError as exc:
        typer.echo(f"没有标记：{exc}")
        raise typer.Exit(1) from None
    typer.echo(f"已标记 {url_id}（原来是「{outcome.RESULT_LABEL.get(result, result)}」）为{school}；"
               "这个网址再抓一次，标记就作废")


# The inspection tools' two commands, nknu search and nknu trace (plan 2026-11-15 T4; the owner, 11-15: 「可以，开工」).
# The code is nknu.ops.cli; only the registration is here, and what is heavy loads inside the commands
from nknu.ops.cli import register as _register_ops

_register_ops(app)
