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
    results = run_all(s, today=today_tpe(), only=only or None, full_scan=full)
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


@app.command()
def fixture(url: str, out: str) -> None:
    """（开发用）礼貌地抓一页，存成解码后的 UTF-8 文字，用来做测试 fixture。之后要裁剪并遮罩个资。"""
    from pathlib import Path

    from nknu.crawl.adapter import RateLimit, Target
    from nknu.crawl.fetch import Client

    client = Client()
    try:
        snap = client.fetch(Target(url), RateLimit())
    finally:
        client.close()
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


def _round1_root(dir: str | None):
    """The archive directory of a command that writes to frontier.db (seed, run, retry): never the 10-24 crawler's
    archive, which is kept as it is (design §3, record 二·8) and whose crawl is not resumed (CLAUDE.md: 全新重爬). Its
    frontier.db has no round mark (frontier.round_of), and opening it to write would add the new columns and tables;
    run would clear its STOP and go on with the old crawl. Without --dir that is the repository's archive/ until it is
    renamed archive-2026-10-24. Review 10-26; 【Claude 的执行办法】."""
    from nknu.archive.frontier import round_of

    root = _archive_root(dir)
    db = root / "frontier.db"
    if db.exists() and round_of(db) is None:
        typer.echo(f"{root} 是 10-24 旧爬虫的存档（frontier.db 没有第一轮的标记）：第一轮不在旧存档上续跑，也不改它。"
                   "先照设计第 3 节把它改名成 archive-2026-10-24/（改名前先问用户），或用 --dir 指定新的存档目录。")
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


@archive_app.command("seed")
def archive_seed(dir: str = typer.Option(None, "--dir", help="存档目录，默认是仓库里的 archive/")) -> None:
    """按机构排好优先顺序，把起点网址放进待抓清单（可重复执行，已有的不会重复加）。"""
    from nknu.archive.crawler import Crawler
    from nknu.archive.seeds import all_seeds

    root = _round1_root(dir)
    crawler = Crawler(root)
    seeds = all_seeds(_settings().facts_path)
    added = 0
    for s in seeds:
        added += crawler.enqueue(s.url, unit=s.unit, tier=s.tier, depth=0, found_via=s.via, found_from=None) == "queued"
    crawler.load_site_meta()
    typer.echo(f"起点 {len(seeds)} 个，新加入 {added} 个；待抓清单：{root / 'frontier.db'}")


@archive_app.command("run")
def archive_run(dir: str = typer.Option(None, "--dir"),
                limit: int = typer.Option(None, "--limit", help="抓这么多个页面就停（试跑用）")) -> None:
    """开始或接着抓。每台服务器同时只连 1 条，回应结束后再等 2–5 秒；中断后重跑会接着抓。同一个目录只能有一个在跑。"""
    import logging
    import signal
    import threading

    from nknu.archive import crawler as archive

    root = _round1_root(dir)
    try:
        lock = archive.hold_run_lock(root)  # held until this process ends; the kernel lets go even after kill -9
    except archive.AlreadyRunning as exc:
        typer.echo(f"另一个 archive run 正在用这个目录（pid {exc.pid}），没有再启动。要换程序，先 nknu archive stop。")
        raise typer.Exit(1) from None
    try:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
        stop_file = root / "STOP"
        stop_file.unlink(missing_ok=True)  # only the run that holds the lock clears a STOP
        (root / "run.pid").unlink(missing_ok=True)  # left by older versions; run.lock says who runs now
        crawler = archive.Crawler(root, limit=limit)

        def watch() -> None:
            while not crawler.stop.wait(archive.STOP_POLL_S):
                if stop_file.exists():
                    logging.getLogger("nknu.archive").info("STOP file found: finishing the requests in flight")
                    crawler.stop.set()

        signal.signal(signal.SIGTERM, lambda *_: crawler.stop.set())
        threading.Thread(target=watch, daemon=True).start()
        crawler.run()
    finally:
        lock.close()
    typer.echo("archive run stopped")


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
                                                        "host_unreachable、robots_unreachable、host_cooldown、crash"),
                  dir: str = typer.Option(None, "--dir")) -> None:
    """把某种原因失败、跳过或在等的网址放回待抓：次数归零，主机的 robots 失败次数、冷却和 DNS 退避也一起清掉。
    已存好的网页不动，只有存成 4xx 拒绝（http_4xx）的才放回。原因开头至少 3 个字。"""
    from nknu.archive.frontier import RETRY_MIN_PREFIX, Frontier

    if len(reason.strip()) < RETRY_MIN_PREFIX:
        typer.echo(f"原因开头至少要 {RETRY_MIN_PREFIX} 个字（例如 http_5、dns），免得把不相干的网址一起放回")
        raise typer.Exit(1)
    n, hosts = Frontier(_archive_db(_round1_root(dir))).retry(reason)
    typer.echo(f"放回待抓：{n} 个网址（{hosts} 台主机一起重设）")


@archive_app.command("browse")
def archive_browse(dir: str = typer.Option(None, "--dir"),
                   port: int = typer.Option(8877, "--port", help="本机端口（只开在 127.0.0.1）")) -> None:
    """在本机浏览器检查抓到的每一页：按单位和网站列出、看文字或不连网的预览、问题清单。Ctrl+C 结束。"""
    from nknu.archive.viewer import build_index, serve

    root = _archive_root(dir)
    if not (root / "frontier.db").exists():
        typer.echo(f"找不到 {root / 'frontier.db'}")
        raise typer.Exit(1)
    import errno

    typer.echo("建立存档索引（第一次要几分钟，之后只补新的部分）……")
    typer.echo(f"新增 {build_index(root)} 笔")
    try:
        server = serve(root, port)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        typer.echo(f"端口 {port} 已经有程序在用：检查工具多半已经开着，直接打开 http://127.0.0.1:{port}/ ；"
                   f"要另开一个就加 --port 8878")
        raise typer.Exit(0) from None
    typer.echo(f"打开 http://127.0.0.1:{port}/")
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
