"""`nknu` command line: init, crawl, build, review, changes, undo, add, serve, check, backup, notify-failure."""

from __future__ import annotations

import re

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="NKNU 校园事实层（v1，只读 MCP）")

BACKUP_KEEP = 14
# the names `nknu backup` gives its copies, and the only files its retention ever deletes
BACKUP_NAME = re.compile(r"facts-\d{8}-\d{6}\.db")
# `nknu notify-failure` gets the failed unit from systemd (%i); anything else is not echoed into a Discord message
UNIT_NAME = re.compile(r"nknu-[a-z]{1,20}")
UNIT_FAILED = {"nknu-backup": "facts.db 这次没有备份成功", "nknu-build": "serve.db 没有重建，答案的「今天」停在上一版"}


@app.callback()
def main() -> None:
    """NKNU 校园事实层（v1，只读 MCP）。"""


def _settings():
    from nknu.settings import load_settings

    return load_settings()


@app.command()
def init() -> None:
    """建立资料目录与 facts.db，并载入词汇表。"""
    from nknu.core.factsdb import connect, migrate
    from nknu.core.vocab import load_vocab, seed

    s = _settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(s.facts_path)
    migrate(conn)
    seed(conn, load_vocab(s.config_dir / "entities.yaml"))
    conn.close()
    typer.echo(f"facts.db ready: {s.facts_path}")


@app.command("seed")
def seed_cmd() -> None:
    """重新载入 config/entities.yaml（实体、关系、别名、主管单位）。"""
    from nknu.core.factsdb import connect, migrate
    from nknu.core.vocab import load_vocab, seed

    s = _settings()
    conn = connect(s.facts_path)
    migrate(conn)
    seed(conn, load_vocab(s.config_dir / "entities.yaml"))
    conn.close()
    typer.echo("vocabulary loaded")


@app.command()
def build(
    today: str = typer.Option(None, help="以哪一天为「今天」计算答案卡（YYYY-MM-DD，默认台北今天）"),
    rollback_: bool = typer.Option(False, "--rollback", help="换回上一个 serve.db 快照"),
) -> None:
    """由 facts.db 重建 serve.db（答案卡与检索索引），原子替换。"""
    from datetime import date

    from nknu.build.servedb import build_serve_db, rollback
    from nknu.core.timeutil import today_tpe

    s = _settings()
    if rollback_:
        try:
            rollback(s.serve_path)
        except FileNotFoundError:
            typer.echo("没有上一版可以换回：--rollback 只能退一步（已经退过一次，或还没重建过）。"
                       "要让某条坏资料不再出现：nknu changes 找到它，再 nknu undo <key>。")
            raise typer.Exit(1) from None
        typer.echo("serve.db rolled back：已换回上一版。这是暂时的，下一次重建（每晚 00:05，或下一轮有变动的抓取）"
                   "会照 facts.db 重新算回来；要长期修正，用 nknu undo <key>（见 nknu changes），"
                   "或在 /etc/nknu/env 设 NKNU_DISABLED_SOURCES=<来源> 后 nknu build。")
        return
    info = build_serve_db(s.facts_path, s.serve_path, today=date.fromisoformat(today) if today else today_tpe(),
                          templates_path=s.config_dir / "templates.yaml", disabled_sources=s.disabled_sources)
    typer.echo(f"serve.db built: build_id={info.build_id} cards={info.n_cards} facts_version={info.facts_version}")


def _build_now(s) -> None:
    from nknu.build.servedb import build_serve_db
    from nknu.core.timeutil import today_tpe

    info = build_serve_db(s.facts_path, s.serve_path, today=today_tpe(), templates_path=s.config_dir / "templates.yaml",
                          disabled_sources=s.disabled_sources)
    typer.echo(f"serve.db built: build_id={info.build_id} cards={info.n_cards}"
               + (f"；新的来源冲突 {info.new_conflicts} 处：nknu alerts" if info.new_conflicts else ""))


@app.command()
def crawl(
    only: list[str] = typer.Option(None, "--only", help="只跑这些 adapter（可重复）"),
    full: bool = typer.Option(None, "--full/--partial", help="强制全扫或只扫前几页（默认：当天第一次全扫）"),
    no_build: bool = typer.Option(False, "--no-build", help="抓完不重建 serve.db"),
) -> None:
    """礼貌抓取全部来源，通过改版守卫的资料直接写入 facts.db；资料有变动就重建 serve.db。有写入、下架或告警时，
    把摘要写进 log，并在设了 NKNU_DISCORD_WEBHOOK 时发到 Discord。"""
    from nknu.admin.changes import crawl_summary
    from nknu.core.factsdb import connect, data_version, migrate
    from nknu.core.notify import notify
    from nknu.core.timeutil import iso_now, today_tpe
    from nknu.crawl.pipeline import run_all

    s = _settings()
    conn = connect(s.facts_path)
    migrate(conn)
    before = data_version(conn)
    conn.close()
    started = iso_now()
    from nknu.crawl.schoollock import SchoolLockBusy, SchoolLockUnshared

    try:
        results = run_all(s, today=today_tpe(), only=only or None, full_scan=full)
    except SchoolLockBusy as exc:  # the archive crawl (or another program) is sending to the school right now
        typer.echo(f"另一个程序正在对学校发请求（pid {exc.pid}，多半是存档爬虫 nknu archive run），这次不抓；"
                   "v1 照旧用上一次的资料回答。等它停下再跑 nknu crawl。")
        raise typer.Exit(1) from None
    except SchoolLockUnshared as exc:
        typer.echo(_unshared(exc))
        raise typer.Exit(1) from None
    for r in results:
        state = "ok" if r.passed else "VOID"
        typer.echo(f"{r.adapter:18} {state:4} records={r.n_records} written={r.n_written} retired={r.n_retired}"
                   + (f"  {r.error}" if r.error else ""))
    conn = connect(s.facts_path)
    changed = data_version(conn) != before
    conn.close()
    build_error = None
    if changed and not no_build:
        try:
            _build_now(s)  # its new source conflicts are alerts of this crawl, so the summary carries them
        except Exception as exc:  # noqa: BLE001 - reported in the summary, then raised
            build_error = exc
    if not s.readonly:
        conn = connect(s.facts_path)
        summary = crawl_summary(conn, results, started)
        conn.close()
        if build_error is not None:
            summary = (summary or "NKNU 抓取摘要") + (f"\n重建 serve.db 失败（仍在提供上一版答案）：{type(build_error).__name__}: "
                                                   f"{str(build_error)[:200]}")
        if summary:
            notify(summary, s.discord_webhook, label="CRAWL")
    if build_error is not None:
        raise build_error
    if any(not r.passed for r in results):
        raise typer.Exit(1)


def _unshared(exc: Exception) -> str:
    """What to say when this machine runs v1's services and the command was not told their lock's path (second
    review 10-26: two programs on two lock files would both send to the school)."""
    return (f"这台机器装了 v1 的服务（{exc}）。没有设 NKNU_SCHOOL_LOCK，这个命令会用自己家目录下的全机锁，"
            "跟服务用的不是同一个，两边挡不住彼此，所以没有执行。先 export NKNU_SCHOOL_LOCK=/var/lib/nknu/school-requests.lock"
            "（和 /etc/nknu/env 里的一样），或改用 /usr/local/bin/nknu 执行。")


@app.command()
def fixture(url: str, out: str) -> None:
    """（开发用）礼貌地抓一页，存成解码后的 UTF-8 文字，用来做测试 fixture。之后要裁剪并遮罩个资。"""
    from pathlib import Path

    from nknu.crawl import schoollock
    from nknu.crawl.adapter import RateLimit, Target
    from nknu.crawl.fetch import Client

    try:  # one program at a time sends to the school (round-1 design §6.9)
        lock = schoollock.hold()
    except schoollock.SchoolLockBusy as exc:
        typer.echo(f"另一个程序正在对学校发请求（pid {exc.pid}），没有抓。等它停下再试。")
        raise typer.Exit(1) from None
    except schoollock.SchoolLockUnshared as exc:
        typer.echo(_unshared(exc))
        raise typer.Exit(1) from None
    client = Client()
    try:
        snap = client.fetch(Target(url), RateLimit())
    finally:
        client.close()
        lock.close()
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    if snap.text is not None:
        p.write_text(snap.text)
    else:
        p.write_bytes(snap.body)
    typer.echo(f"{snap.status} {len(snap.body)} bytes -> {p}")


@app.command()
def alerts(
    all_: bool = typer.Option(False, "--all", help="包含已处理的告警"),
    resolve: int = typer.Option(None, "--resolve", help="把这一条告警标为已处理"),
) -> None:
    """查看告警（改版侦测失败、新地点、解析问题、来源冲突）。"""
    from nknu.core.factsdb import connect, migrate
    from nknu.core.timeutil import iso_now

    s = _settings()
    conn = connect(s.facts_path)
    migrate(conn)
    if resolve is not None:
        conn.execute("UPDATE alert SET resolved_at=? WHERE id=? AND resolved_at IS NULL", (iso_now(), resolve))
        conn.commit()
        typer.echo(f"alert {resolve} resolved")
        return
    where = "" if all_ else "WHERE resolved_at IS NULL"
    for r in conn.execute(f"SELECT id, created_at, level, adapter, code, message FROM alert {where} ORDER BY id"):
        typer.echo(f"#{r['id']} {r['created_at']} [{r['level']}] {r['adapter'] or '-'} {r['code']}: {r['message']}")


@app.command()
def review(no_build: bool = typer.Option(False, "--no-build", help="审核完不重建 serve.db")) -> None:
    """处理残留的待审项目（旧版留下的；现在爬虫直接写入，不再新增待审）。日常请用 nknu changes / nknu undo。"""
    from nknu.admin.review import NotPending, decide, list_pending, render_item
    from nknu.build.templates import load_templates
    from nknu.core.factsdb import connect, migrate
    from nknu.core.timeutil import iso_now, today_tpe

    s = _settings()
    if s.readonly:
        typer.echo("NKNU_READONLY=1：审核已停用")
        raise typer.Exit(1)
    conn = connect(s.facts_path)
    migrate(conn)
    tpl = load_templates(s.config_dir / "templates.yaml")
    items = list_pending(conn)
    if not items:
        typer.echo("没有待审项目。")
        return
    for it in items:  # one item that cannot be shown or applied never stops the others
        try:
            typer.echo(render_item(conn, it, today_tpe(), tpl))
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"#{it.id} [{it.adapter}] {it.op} ({it.reason}) {it.record_key}\n{it.diff}\n"
                       f"  （无法预览：{type(exc).__name__}: {exc}）")
        typer.echo("-" * 60)
    choice = typer.prompt(f"共 {len(items)} 条。[y] 全部核准 / [e] 逐条处理 / [q] 离开", default="q").strip().lower()
    decided, failed = 0, 0

    def _decide(it, decision: str) -> None:
        nonlocal decided, failed
        try:
            decide(conn, it.id, decision, iso_now())
            decided += 1
        except NotPending as exc:  # a crawl replaced it while the list was open: the newer version waits for next time
            typer.echo(f"略过 {exc}；重新执行 nknu review 查看最新版本")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            typer.echo(f"#{it.id} {it.record_key} 处理失败，已略过（没有写入）：{type(exc).__name__}: {exc}")

    if choice == "y":
        for it in items:
            _decide(it, "approve")
    elif choice == "e":
        for it in items:
            typer.echo(f"#{it.id} {it.record_key}\n{it.diff}")
            a = typer.prompt("[a] 核准 [r] 驳回 [s] 略过", default="s").strip().lower()
            if a in ("a", "r"):
                _decide(it, "approve" if a == "a" else "reject")
    conn.close()
    typer.echo(f"已处理 {decided} 条。" + (f"{failed} 条失败，仍留在待审清单。" if failed else ""))
    if decided and not no_build:
        _build_now(s)
    if failed:
        raise typer.Exit(1)


@app.command()
def changes(
    since: str = typer.Option(None, "--since", help="从这一天（台北日期 YYYY-MM-DD）起，默认 7 天前"),
    docs: bool = typer.Option(False, "--docs", help="逐条列出公告（默认只列条数）"),
) -> None:
    """列出最近写入的变更（场馆时段、闭馆、假日、行事历、公告），附上 nknu undo 要用的 id 或 key。"""
    from datetime import date, timedelta

    from nknu.admin.changes import format_change, list_changes, since_utc
    from nknu.core.factsdb import connect, migrate
    from nknu.core.timeutil import today_tpe

    s = _settings()
    day = date.fromisoformat(since) if since else today_tpe() - timedelta(days=7)
    conn = connect(s.facts_path)
    migrate(conn)
    found = list_changes(conn, since_utc(day))
    n_pending = conn.execute("SELECT count(*) FROM pending_change WHERE status='pending'").fetchone()[0]
    conn.close()
    doc_counts: dict[tuple[str, str], int] = {}
    for c in found:
        if c.kind == "doc" and not docs:
            doc_counts[(c.adapter, c.op)] = doc_counts.get((c.adapter, c.op), 0) + 1
        else:
            typer.echo(format_change(c))
    if doc_counts:
        from nknu.admin.changes import OPS

        typer.echo("公告：" + "、".join(f"{a} {OPS[op]} {n} 则" for (a, op), n in sorted(doc_counts.items()))
                   + "（加 --docs 逐条列出）")
    if not found:
        typer.echo(f"{day} 以来没有变更。")
    if n_pending:
        typer.echo(f"另有 {n_pending} 条待审：nknu review")


@app.command()
def undo(
    target: str = typer.Argument(..., help="fact id、lineage id 或 record key（见 nknu changes）"),
    cancel: bool = typer.Option(False, "--cancel", help="取消先前的撤回：忘记记住的内容，下一轮抓取会照常写入"),
    no_build: bool = typer.Option(False, "--no-build", help="不重建 serve.db"),
) -> None:
    """下架一条现行资料（场馆时段、闭馆、假日、行事历事件或公告），并记住这份内容：爬虫之后不会把一模一样的内容
    写回来，内容变了才会再写。完成后重建 serve.db。"""
    from nknu.admin.undo import UndoError, cancel_undo
    from nknu.admin.undo import undo as undo_item
    from nknu.core.factsdb import connect, migrate
    from nknu.core.timeutil import iso_now

    s = _settings()
    if s.readonly:
        typer.echo("NKNU_READONLY=1：撤回已停用")
        raise typer.Exit(1)
    conn = connect(s.facts_path)
    migrate(conn)
    try:
        if cancel:
            n = cancel_undo(conn, target)
            typer.echo(f"已忘记 {n} 份撤回的内容；下一轮抓取会照常写入（手动录入的资料请重新 nknu add）" if n
                       else f"「{target}」没有撤回纪录")
            return
        try:
            done = undo_item(conn, target, now=iso_now())
        except UndoError as exc:
            typer.echo(f"错误：{exc}")
            raise typer.Exit(2) from exc
    finally:
        conn.close()
    typer.echo(f"已撤回 {done.kind} {done.key}（{done.ref}）"
               + ("；之后抓到一模一样的内容不会再写入" if done.remembered else ""))
    if not no_build:
        _build_now(s)


add_app = typer.Typer(help="手动录入事实（只有图片的公告、现场确认）")
app.add_typer(add_app, name="add")


@add_app.command("hours")
def add_hours_cmd(
    place: str = typer.Option(..., help="地点：实体 id 或别名（有歧义会报错）"),
    from_: str = typer.Option(..., "--from", help="开始日期 YYYY-MM-DD"),
    to: str = typer.Option(None, help="结束日期（含当天），默认同开始日期"),
    opens: str = typer.Option(None, help="开放时间 HH:MM"),
    closes: str = typer.Option(None, help="结束时间 HH:MM（早于开放时间表示跨午夜）"),
    closed: bool = typer.Option(False, "--closed", help="这段期间不开放"),
    reason: str = typer.Option(..., help="原因，例如「期中考周延長開館」"),
    source_url: str = typer.Option(..., help="原公告网址"),
    publisher: str = typer.Option(..., help="发布单位的实体 id，例如 nknu.org.lis"),
    title: str = typer.Option(None, help="公告标题"),
    site_visit: bool = typer.Option(False, "--site-visit", help="维护者现场确认（来源等级 4）"),
    no_build: bool = typer.Option(False, "--no-build"),
) -> None:
    """录入一段特殊开放时间或闭馆（specificity=2），直接写入为 verified。"""
    import sqlite3
    from datetime import date

    from nknu.admin.manual import PlaceError, add_hours
    from nknu.core.factsdb import connect, migrate
    from nknu.core.timeutil import iso_now

    s = _settings()
    if s.readonly:
        typer.echo("NKNU_READONLY=1：录入已停用")
        raise typer.Exit(1)
    conn = connect(s.facts_path)
    migrate(conn)
    try:
        added = add_hours(conn, place=place, from_=date.fromisoformat(from_), to=date.fromisoformat(to) if to else None,
                          opens=opens, closes=closes, closed=closed, reason=reason, source_url=source_url,
                          publisher=publisher, title=title, site_visit=site_visit, now=iso_now())
    except (PlaceError, ValueError, sqlite3.IntegrityError) as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(2) from exc
    finally:
        conn.close()
    typer.echo(f"已写入 fact {added.fid}（{added.key}）")
    for k in added.replaced:
        typer.echo(f"  取代了 {k}")
    for k in added.kept:
        typer.echo(f"  被取代的爬虫资料其他日期照原内容保留为 {k}（要拿掉用 nknu undo {k}）")
    if not no_build:
        _build_now(s)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="监听位址（cloudflared 在本机连进来，保持 127.0.0.1）"),
    port: int = typer.Option(8765, help="监听埠"),
) -> None:
    """启动 MCP 服务（/mcp 与 /health）。serve.db 不存在或启动检查失败时拒绝启动。"""
    import uvicorn

    from nknu.server.app import create_app

    # proxy_headers=False: the rate limit trusts CF-Connecting-IP only from a loopback peer, so the peer must stay the
    # real TCP peer; access_log=False: no client addresses in the logs
    uvicorn.run(create_app(_settings()), host=host, port=port, proxy_headers=False, server_header=False,
                access_log=False)


@app.command()
def backup(
    to: str = typer.Option(None, "--to", help="备份目录，默认 NKNU_BACKUP_DIR；最好放在另一颗磁碟或 NAS"),
) -> None:
    """把 facts.db 线上备份成 DIR/facts-YYYYmmdd-HHMMSS.db（台北时间），只保留最新 14 份。serve.db 可以重算，不备份。"""
    import os
    import sqlite3
    from pathlib import Path

    from nknu.core.factsdb import connect
    from nknu.core.timeutil import TAIPEI, now_utc

    s = _settings()
    target = Path(to) if to else s.backup_dir
    if target is None:
        typer.echo("没有备份目录：用 --to DIR 指定，或在 /etc/nknu/env 设定 NKNU_BACKUP_DIR")
        raise typer.Exit(2)
    if not s.facts_path.is_file():  # never back up (or create) an empty facts.db, and then prune the good copies
        typer.echo(f"找不到 facts.db：{s.facts_path}（NKNU_DATA_DIR 设对了吗？）")
        raise typer.Exit(1)
    target.mkdir(parents=True, exist_ok=True)
    out = target / f"facts-{now_utc().astimezone(TAIPEI):%Y%m%d-%H%M%S}.db"
    if out.exists():
        typer.echo(f"{out} 已存在，一秒后再试")
        raise typer.Exit(1)
    # copy into a hidden temp file first: a half-written copy never carries a backup's name
    tmp = target / f".{out.name}.tmp-{os.getpid()}"
    tmp.unlink(missing_ok=True)
    src = connect(s.facts_path, readonly=True)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)  # SQLite online backup: a consistent snapshot even while a crawl is writing
            dst.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, no -wal/-shm beside it
            check = dst.execute("PRAGMA quick_check").fetchone()[0]
            n_facts = dst.execute("SELECT count(*) FROM fact").fetchone()[0]
        finally:
            dst.close()
        if check != "ok":
            raise sqlite3.DatabaseError(f"quick_check: {check}")
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        src.close()
    typer.echo(f"facts.db backed up: {out} ({n_facts} facts)")
    # retention runs only after a good copy exists, and only over this command's own file names
    mine = sorted(p for p in target.iterdir() if BACKUP_NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink())
    for old in mine[:-BACKUP_KEEP]:
        old.unlink()
        typer.echo(f"removed old backup {old.name}")


@app.command("notify-failure")
def notify_failure(unit: str = typer.Argument(..., help="失败的服务名，例如 nknu-backup（systemd 的 OnFailure= 传入）")) -> None:
    """（systemd 用）备份或夜间重建失败时，记一条告警（nknu alerts）并通知维护者（log；设了 NKNU_DISCORD_WEBHOOK
    也发 Discord）。同一个服务的告警没处理时，一天最多通知一次。"""
    from nknu.core.factsdb import connect
    from nknu.core.notify import notify
    from nknu.core.timeutil import iso_now
    from nknu.core.writer import add_alert

    s = _settings()
    unit = unit if UNIT_NAME.fullmatch(unit) else "nknu-?"
    what = UNIT_FAILED.get(unit, "排程工作没有完成")
    message = f"{unit}.service 执行失败：{what}。原因：sudo journalctl -u {unit} -n 50；修好后 nknu alerts --resolve <编号>"
    if s.facts_path.is_file():  # never create an empty facts.db here
        try:
            conn = connect(s.facts_path)
            try:
                with conn:
                    aid = add_alert(conn, adapter=None, level="error", code="unit_failed", message=message,
                                    dedup_key=f"unit_failed:{unit}", now=iso_now(), webhook=s.discord_webhook,
                                    remind_daily=True)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - a full disk or a locked facts.db must not swallow the report
            message += f"（告警写不进 facts.db：{type(exc).__name__}）"
        else:
            if aid is None:
                typer.echo(f"{unit}：同一则告警 24 小时内已通知过，这次不再发通知")
            return  # add_alert logged it and posted it to the webhook
    notify(message, s.discord_webhook, label="FAILURE")


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
                   "要看彩排结果用 verify、status、browse、reconcile --no-r6，或 nknu archive rehearse --check。")
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
                 probe: str = typer.Option(None, "--probe", help="网站普查 data.json（52 个校务系统起点）；默认 research 里那份"),
                 jstest: str = typer.Option(None, "--jstest", help="10-25 网页类型测试结果的目录（5 个档）；默认 research 里那份，"
                                                                 "实验室机器上没有 research/ 时要指定")
                 ) -> None:
    """建第一轮的待抓清单（设计第 5 节）：名单的网站、附加起点、登录重查，旧存档的每一行和它链接表的目标（照新规则重判），
    Common Crawl、SiteMap、v1 已知公告、SSO 公告列表，第二轮的起点（分批记下），以及 10-25 网页类型测试结果（只进
    page_type 表，不当起点）。可重复执行，已有的不会重复加。旧存档只读、绝不改。写 reports/seed-<时间>.md；有网站的起点比
    旧存档少，退出码 2 并列出少了哪些。"""
    from datetime import datetime
    from pathlib import Path

    from nknu.archive import crawler as archive
    from nknu.archive import pagetypes, round1, seeds

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
    jstest_dir = Path(jstest).expanduser().resolve() if jstest else pagetypes.JSTEST_DIR
    missing = [n for n in (pagetypes.RESULTS, pagetypes.RETEST, pagetypes.TYPES, pagetypes.REVIEW,
                           pagetypes.CLICK_SCAN) if not (jstest_dir / n).is_file()]
    if missing:  # design §5 ⑩: never seeded without it (every type would be new and unchecked, unseen)
        typer.echo(f"找不到 10-25 网页类型测试结果：{jstest_dir} 缺 {'、'.join(missing)}（用 --jstest 指定目录）")
        raise typer.Exit(1)
    lock = _hold_for_writing(root)  # held while seeding: no run may start meanwhile (design §6.9: one writer)
    try:
        crawler = archive.Crawler(root, plan=round1.load(), resolver=archive.resolve)
        report = seeds.seed_round1(crawler, old=old_path, facts_db=_settings().facts_path,
                                   cc_dir=Path(cc) if cc else seeds.CC_DIR, probe=Path(probe) if probe else seeds.PROBE,
                                   jstest=jstest_dir)
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


@archive_app.command("run")
def archive_run(dir: str = typer.Option(None, "--dir"),
                limit: int = typer.Option(None, "--limit", help="抓这么多个页面就停（试跑用）"),
                gap: str = typer.Option("2-5", "--gap", help="每个回应之后等几秒（下限-上限）；默认 2-5，试跑通过、"
                                                           "用户说开始用以后才设 1-2"),
                sites: str = typer.Option(None, "--sites", help="只抓这些网站（网站键，逗号分隔，例如 "
                                                              "c.nknu.edu.tw/reality,sso.nknu.edu.tw/web/595）"),
                end_wait: float = typer.Option(1800.0, "--end-wait", help="剩下的网址最早也要这么多秒以后才到期时，这次 run "
                                                                       "先结束、留给下一次；默认 1800（30 分钟，比服务器"
                                                                       "固定休息的 15 分钟长）")) -> None:
    """开始或接着抓。每台服务器同时只连 1 条，回应结束后再等（默认 2–5 秒）；中断后重跑会接着抓。同一个目录只能有一个在跑，
    同一台机器只能有一个程序对学校发请求（全机锁）。只剩等重试的网址、而且最早也要 --end-wait 秒以后才到期时，自己结束，
    留给下一次。一个网站抓完（只剩等重试的），就先对它做不连网的对账（浏览器抽查 R6 等 run 停下后用 archive reconcile 做），
    看板上它会变成「纪录齐了，还没对账」。"""
    import logging
    import signal
    import threading

    from nknu.archive import crawler as archive
    from nknu.archive import reconcile
    from nknu.crawl import schoollock

    root = _round1_root(dir, online=True)
    span = _gap_option(gap)
    if end_wait <= 0:
        typer.echo("--end-wait 是秒数，要大于 0（默认 1800）")
        raise typer.Exit(1)
    only = frozenset(x.strip() for x in sites.split(",") if x.strip()) if sites else None
    try:
        lock = archive.hold_run_lock(root)  # held until this process ends; the kernel lets go even after kill -9
    except archive.AlreadyRunning as exc:
        typer.echo(f"另一个 archive run 正在用这个目录（pid {exc.pid}），没有再启动。要换程序，先 nknu archive stop。")
        raise typer.Exit(1) from None
    try:
        school = schoollock.hold()  # one program per machine sends to the school (design §6.9)
    except schoollock.SchoolLockBusy as exc:
        lock.close()
        typer.echo(f"另一个程序正在对学校发请求（pid {exc.pid}，全机锁 {exc.path}），没有启动。等它停下再跑。")
        raise typer.Exit(1) from None
    except schoollock.SchoolLockUnshared as exc:
        lock.close()
        typer.echo(_unshared(exc))
        raise typer.Exit(1) from None
    typer.echo(f"全机锁：{school.path}（对学校发请求的程序都要用这一个档）")
    try:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
        stop_file = root / "STOP"
        stop_file.unlink(missing_ok=True)  # only the run that holds the lock clears a STOP
        (root / "run.pid").unlink(missing_ok=True)  # left by older versions; run.lock says who runs now
        # each site that finishes is reconciled offline by this process (design §18.2a step 3; R6 waits for
        # `archive reconcile` once the run stopped)
        crawler = archive.Crawler(root, limit=limit, gap=span, sites=only, end_wait_s=end_wait,
                                  reconcile_sources=reconcile.Sources())

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
    if crawler.end_reason == "waiting":
        _next_run(crawler)


def _next_run(crawler) -> None:
    """A run that ended with only waits left (B1) says when the soonest of them comes due: the earliest a run again
    has something to do (second review 10-26, END_WAIT_S: a person, or a timer, starts it)."""
    from datetime import datetime

    left, nxt = crawler.frontier.waiting(crawler.sites)
    if nxt is not None:
        typer.echo(f"还有 {left} 个网址在等（重试、DNS、冷却或服务器休息）；最早 "
                   f"{datetime.fromtimestamp(nxt).astimezone():%m-%d %H:%M} 起再执行一次 nknu archive run 就会接着抓")


@archive_app.command("status")
def archive_status(dir: str = typer.Option(None, "--dir")) -> None:
    """健康报告：有没有爬错（礼貌、范围、robots）或爬漏。写进 archive/reports/。有硬性违反时退出码 2。"""
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
                                                        "host_unreachable、robots_unreachable、host_cooldown、crash；"
                                                        "post_failed：回传失败的列表从第 1 页重走；click：失败的下载按钮"
                                                        "（电子系）放回再按"),
                  dir: str = typer.Option(None, "--dir")) -> None:
    """把某种原因失败、跳过或在等的网址放回待抓：次数归零，主机的 robots 失败次数、冷却和 DNS 退避也一起清掉。
    已存好的网页不动，只有存成 4xx 拒绝（http_4xx）的才放回。原因开头至少 3 个字。
    post_failed：回传两次都失败的列表（listwalk 记 post_failed）从列表第 1 页的 GET 重走；click：照规则按、失败了的
    POST 下载按钮放回待按（设计第 7.4、7.5 节；都只在人看过以后用，POST 绝不自动重送）。"""
    from nknu.archive.frontier import RETRY_MIN_PREFIX, Frontier

    if len(reason.strip()) < RETRY_MIN_PREFIX:
        typer.echo(f"原因开头至少要 {RETRY_MIN_PREFIX} 个字（例如 http_5、dns），免得把不相干的网址一起放回")
        raise typer.Exit(1)
    root = _round1_root(dir)
    db = _archive_db(root)
    lock = _hold_for_writing(root)
    try:
        if reason.strip() in ("post_failed", "click"):
            from nknu.archive.postback import Chains

            f = Frontier(db)
            chains = Chains(f, root)
            if reason.strip() == "post_failed":
                n = chains.retry_post_failed()
                msg = f"从第 1 页重走：{n} 个回传失败的列表（列表页放回待抓，下一次 archive run 接着按）"
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


@archive_app.command("browse")
def archive_browse(dir: str = typer.Option(None, "--dir"),
                   port: int = typer.Option(8877, "--port", help="本机端口（只开在 127.0.0.1）")) -> None:
    """在本机浏览器看进度看板和抓到的每一页：进度看板（每 30 秒自动更新，只读、不拖慢爬虫）、按单位和网站列出、看文字或
    不连网的预览、问题清单。Ctrl+C 结束。"""
    import errno

    from nknu.archive.viewer import index_in_background, serve

    root = _archive_root(dir)
    if not (root / "frontier.db").exists():
        typer.echo(f"找不到 {root / 'frontier.db'}")
        raise typer.Exit(1)
    try:
        server = serve(root, port)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        typer.echo(f"端口 {port} 已经有程序在用：检查工具多半已经开着，直接打开 http://127.0.0.1:{port}/progress ；"
                   f"要另开一个就加 --port 8878")
        raise typer.Exit(0) from None
    typer.echo(f"进度看板：http://127.0.0.1:{port}/progress")
    typer.echo(f"存档总览：http://127.0.0.1:{port}/")
    typer.echo("存档索引在背景建立（第一次要几分钟，之后只补新的部分；看板不用等它）")
    index_in_background(root, lambda added: typer.echo(f"存档索引好了：新增 {added} 笔"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


@archive_app.command("choices")
def archive_choices(dir: str = typer.Option(None, "--dir")) -> None:
    """算出每个待决定项目的资讯量，给检查工具的「选择放行」页用（扫一遍存档，要几分钟）。"""
    import json

    from nknu.archive.choices import build
    from nknu.archive.viewer import build_index

    root = _archive_root(dir)
    if not (root / "frontier.db").exists():
        typer.echo(f"找不到 {root / 'frontier.db'}")
        raise typer.Exit(1)
    build_index(root)
    data = build(root)
    (root / "choices-data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    typer.echo("、".join(f"{s['title']} {len(s['items'])} 项" for s in data["sections"]))


# ---------------------------------------------------------------- round 1 after the crawl: verify, jscheck, reconcile


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


@archive_app.command("verify")
def archive_verify(dir: str = typer.Option(None, "--dir"),
                   golden: str = typer.Option(None, "--golden", help="25 题的答案档（例如 research/project_audit_2026-10-25/"
                                                                   "web_search_baseline_25q.json），另核对第 G 项")
                   ) -> None:
    """第一轮验收（设计第 13 节）：只读、不连网，逐项核对（每项 ✓ 通过 / ⚠ 要看 / ✗ 出错 / 没检查）和每个网站的状态，写
    reports/verify-<时间>.md。退出码 0 全过、1 有要看的或没检查的、2 有出错的。已知缺口（第 16 项）会一直列着，所以最好
    也只到「第一轮完成，有已知缺口 N 项」。爬虫在跑时也能看，但第 14 项（存档读得回来）要等它停下。"""
    from pathlib import Path

    from nknu.archive import monitor, round1
    from nknu.archive.frontier import OutdatedFrontier

    root = _archive_root(dir)
    _archive_db(root)
    gold = Path(golden).expanduser().resolve() if golden else None
    if gold is not None and not gold.is_file():
        typer.echo(f"找不到 25 题的答案档：{gold}")
        raise typer.Exit(1)
    plan = round1.load()
    try:
        got = monitor.verify(root, plan=plan, golden=gold, resource_check=monitor.extract_check(plan))
    except OutdatedFrontier as exc:
        typer.echo(f"{exc}。验收只读、不改资料库：这多半是 10-24 旧爬虫的存档，或旧版程序写的待抓清单，先用 --dir 指定第一轮的"
                   "存档目录。")
        raise typer.Exit(1) from None
    typer.echo(got.text)
    if got.path is not None:
        typer.echo(f"报告：{got.path}")
    raise typer.Exit(got.code)


def _drop_secrets(names) -> None:
    """A command that opens Chrome and never needs a credential (jscheck, reconcile's R6) holds none from its start:
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

    from nknu.archive import jscheck

    _drop_secrets(jscheck.SECRET_ENV)
    root = _round1_root(dir, online=set_ == (None, None))
    _archive_db(root)
    if set_ != (None, None):
        key, verdict = set_
        try:
            stale = jscheck.set_verdict(root, key, verdict, note)
        except jscheck.JsCheckRefused as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        typer.echo(f"已改判：{key} → {verdict}（人工）；各页的结果会跟着变")
        if stale:
            typer.echo(f"这些网站的对账因此过期，要重新对账（nknu archive reconcile --batch … --sites …）：{'、'.join(stale)}")
        return
    if note:
        typer.echo("--note 只和 --set 一起用")
        raise typer.Exit(1)
    span = _gap_option(gap)
    check = jscheck.JsCheck(root, old=_old_of(root, old), gap=span)
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


@archive_app.command("reconcile")
def archive_reconcile(batch: str = typer.Option(None, "--batch", help="这一批的名字（写进报告和 reconcile_site）"),
                      dir: str = typer.Option(None, "--dir"),
                      sites: str = typer.Option(None, "--sites", help="只对这些网站（网站键，逗号分隔）；默认第一轮全部"),
                      no_r6: bool = typer.Option(False, "--no-r6", help="不做浏览器抽查（R6）：不连网、不拿全机锁；"
                                                                    "这样网站不会变成「爬完」"),
                      old: str = typer.Option(None, "--old", help="10-24 旧存档（只读，R4）；默认是 seed 记下的那一份"),
                      gap: str = typer.Option("2-5", "--gap", help="R6 每个回应之后等几秒（下限-上限）"),
                      enqueue: str = typer.Option(None, "--enqueue", help="人看过、要抓的网址清单（一行一个，"
                                                                        "例如对账写的 -found.txt），写进待抓清单")
                      ) -> None:
    """对账（设计第 18.2b 节）：拿 Common Crawl、旧存档、25 题、浏览器抽查和爬虫自己的纪录比，逐站写 reconcile_site 和
    reports/reconcile-<批>-<时间>.md。R6 用 jscheck 的浏览器和队列打开每站首页加几页（要拿全机锁、run 停着）；其余几项
    不连网。run 在跑时拒绝。退出码 0 每站关卡全过、1 有没过或没检查的关卡、2 有关卡出错。
    --enqueue <清单>：把人确认要抓的网址写进待抓清单（found_via=reconcile），下一次 run 照老方法抓。"""
    from pathlib import Path

    from nknu.archive import crawler as archive
    from nknu.archive import jscheck, reconcile
    from nknu.crawl import schoollock

    _drop_secrets(jscheck.SECRET_ENV)
    root = _round1_root(dir, online=not (no_r6 or enqueue))
    _archive_db(root)
    try:
        if enqueue:
            listed = Path(enqueue).expanduser()
            if not listed.is_file():
                typer.echo(f"找不到清单：{listed}")
                raise typer.Exit(1)
            got = reconcile.enqueue(root, reconcile.read_list(listed))
            queued = sum(v == "queued" for _u, v in got)
            typer.echo(f"清单 {len(got)} 个网址：写进待抓清单 {queued} 个；其余（已经有了、第二轮、规则不抓）：")
            for u, v in got:
                if v != "queued":
                    typer.echo(f"  {v}  {u}")
            return
        if not batch:
            typer.echo("要给这一批一个名字：--batch <批>（或用 --enqueue <清单> 把网址写进待抓清单）")
            raise typer.Exit(1)
        sources = reconcile.Sources(old=_old_of(root, old))
        if no_r6:
            got, out = reconcile.reconcile(root, batch=batch, sites=_site_list(sites), sources=sources)
        else:
            span = _gap_option(gap)
            try:
                school = schoollock.hold()  # R6 opens school pages (design §6.9)
            except schoollock.SchoolLockBusy as exc:
                typer.echo(f"另一个程序正在对学校发请求（pid {exc.pid}，全机锁 {exc.path}）：等它停下再对账，或加 --no-r6 "
                           "只做不连网的几项")
                raise typer.Exit(1) from None
            except schoollock.SchoolLockUnshared as exc:
                typer.echo(_unshared(exc))
                raise typer.Exit(1) from None
            try:
                got, out = reconcile.reconcile(root, batch=batch, sites=_site_list(sites), sources=sources,
                                               spot_factory=lambda fr: jscheck.SpotChecker(root, fr, gap=span))
            finally:
                school.close()
    except archive.AlreadyRunning as exc:
        typer.echo(f"archive run（或 jscheck）正在用这个目录（pid {exc.pid}）：先 nknu archive stop，等它停下再对账")
        raise typer.Exit(1) from None
    except reconcile.NotRound1 as exc:
        typer.echo(f"{exc}：对账只对第一轮的存档做，旧存档只读")
        raise typer.Exit(1) from None
    except jscheck.BrowserMissing as exc:
        typer.echo(f"打不开浏览器（R6 要用）：{exc}。装好选用依赖组 browser 和本机 Chrome，或加 --no-r6 只做不连网的几项")
        raise typer.Exit(1) from None
    except schoollock.SchoolLockNotHeld as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    worst = 0
    for res in got.sites:
        failing = [c for c, k in res.checks.items() if c in reconcile.GATES and k.status == reconcile.FAIL]
        open_ = [c for c, k in res.checks.items() if c in reconcile.GATES and k.status != reconcile.PASS]
        worst = max(worst, 2 if failing else 1 if open_ else 0)
        typer.echo(f"{res.site}：" + ("关卡全过" if res.gates_ok else
                                      "没过 " + "、".join(f"{c}（{reconcile.STATUS_LABEL[res.checks[c].status]}）"
                                                         for c in open_)))
    if got.found:
        typer.echo(f"对账发现爬虫没找到的网址 {len(got.found)} 个（要人看）：见报告和 -found.txt；要抓的用 "
                   "nknu archive reconcile --enqueue <清单>")
    typer.echo(f"报告：{out}")
    raise typer.Exit(worst)


@archive_app.command("rejudge")
def archive_rejudge(sites: str = typer.Option(..., "--sites", help="要重算的网站（网站键，逗号分隔）"),
                    dir: str = typer.Option(None, "--dir")) -> None:
    """不连网重算（设计第 18.2a 节第 4 步）：从存档按位置读回这几个网站已存的网页，用现在的规则和平台档案重算资源、类型、
    记号和「页面几乎是空的」；新找到的网址写进待抓清单（found_via=rejudge），已经抓过的不重抓。run 在跑时拒绝。之后这几个
    网站的对账要重做。"""
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
        typer.echo(f"有 {len(got.unread)} 页按位置读不回来（见 archive verify 第 14 项）")
    typer.echo("这几个网站之前的对账已经过期：nknu archive reconcile --batch <批> --sites …")


@archive_app.command("review")
def archive_review(url_id: int = typer.Argument(None, help="要标记的网址编号（不给就列出可以标记的网址）"),
                   mark: str = typer.Option(None, "--mark", help="标记：school_side（学校那边的问题，人看过）"),
                   note: str = typer.Option(None, "--note", help="为什么这样标（必填）"),
                   site: str = typer.Option(None, "--site", help="只列这个网站的"),
                   dir: str = typer.Option(None, "--dir")) -> None:
    """人看过、确认是学校那边的问题的网址（例如云端档案不公开、学校的链接坏了），标成「学校那边的问题（人看过）」（设计第
    10.2.1 节）：nknu archive review --mark school_side <编号> --note …。那个网址再抓一次，标记就作废。不给 --mark 时只
    列出可以标记的网址（只读）。run 在跑时拒绝标记。"""
    from nknu.archive import crawler as archive
    from nknu.archive import outcome, reconcile
    from nknu.archive.frontier import Frontier

    root = _round1_root(dir)
    db = _archive_db(root)
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
    typer.echo(f"已标记 {url_id}（原来是「{outcome.RESULT_LABEL.get(result, result)}」）为学校那边的问题（人看过）；"
               "这个网址再抓一次，标记就作废")


@archive_app.command("rehearse", hidden=True)
def archive_rehearse(old: str = typer.Option(None, "--old", help="10-24 旧存档的目录（只读：数据库按 immutable 打开，WARC 只读）"),
                     dir: str = typer.Option(..., "--dir", help="彩排的新资料夹（不能是旧存档，也不能在它里面）"),
                     sites: str = typer.Option(None, "--sites", help="只跑这些网站（网站键，逗号分隔）；默认第一轮全部"),
                     check: str = typer.Option(None, "--check", help="预期清单（rehearsal_expect.json）：不跑彩排，"
                                                                    "只拿 --dir 的彩排结果逐条核对"),
                     facts: str = typer.Option(None, "--facts", help="v1 的 facts.db（只读，v1 已知公告当起点）；默认不读")
                     ) -> None:
    """离线彩排（设计第 18.1 节，隐藏命令）：用真的爬虫程序（seed --round 1 + run）跑第一轮，网络换成「从旧存档回答」：同一个
    网址（规范写法相同）的 GET 照旧存档的回应原样回，旧存档没有的（没按过的按钮 POST、没抓过的附件、Google API）回
    not_archived（失败、不重试、不算服务器或网络的）。不连网（断网绊线）、DNS 用旧库 host 表、时钟是假的（等待只把时钟往前拨），
    不拿全机锁、不用金钥。跑完自动 reconcile（不含 R6）和 verify，报告写进 <dir>/reports/。
    --check <清单> --dir <彩排资料夹>：逐条核对，印出 PASS/FAIL 和理由；全过退出码 0，有 FAIL 退出码 2。"""
    import logging
    from pathlib import Path

    from nknu.archive import rehearsal

    root = Path(dir).expanduser().resolve()
    if check:
        expect = Path(check).expanduser().resolve()
        if not expect.is_file():
            typer.echo(f"找不到预期清单：{expect}")
            raise typer.Exit(1)
        if not (root / "frontier.db").is_file():
            typer.echo(f"找不到彩排的待抓清单：{root / 'frontier.db'}")
            raise typer.Exit(1)
        try:
            got = rehearsal.check(expect, root)
        except (ValueError, KeyError) as exc:
            typer.echo(f"预期清单读不懂：{exc}")
            raise typer.Exit(1) from None
        for r in got:
            typer.echo(f"{'PASS' if r.ok else 'FAIL'}  {r.id}：{r.why}")
        failed = [r for r in got if not r.ok]
        typer.echo(f"共 {len(got)} 条：PASS {len(got) - len(failed)}、FAIL {len(failed)}")
        raise typer.Exit(2 if failed else 0)
    if not old:
        typer.echo("要给旧存档的目录：--old <旧存档>（或用 --check 只核对）")
        raise typer.Exit(1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per replayed request is noise here
    try:
        done = rehearsal.rehearse(Path(old).expanduser(), root, sites=_site_list(sites),
                                  facts_db=Path(facts).expanduser() if facts else None, say=typer.echo)
    except rehearsal.RehearsalRefused as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    except FileNotFoundError as exc:
        typer.echo(f"找不到：{exc}")
        raise typer.Exit(1) from None
    typer.echo(f"彩排完：run 结束原因 {done.run_end.get('reason')}；验收 {done.verify_conclusion}（退出码 "
               f"{done.verify_code}）；摘要 {done.summary}")
