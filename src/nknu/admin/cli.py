"""`nknu` command line: init, crawl, build, review, add, serve, check."""

from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="NKNU 校园事实层（v1，只读 MCP）")


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
        rollback(s.serve_path)
        typer.echo("serve.db rolled back")
        return
    info = build_serve_db(s.facts_path, s.serve_path, today=date.fromisoformat(today) if today else today_tpe(),
                          templates_path=s.config_dir / "templates.yaml", disabled_sources=s.disabled_sources)
    typer.echo(f"serve.db built: build_id={info.build_id} cards={info.n_cards} facts_version={info.facts_version}")


def _build_now(s) -> None:
    from nknu.build.servedb import build_serve_db
    from nknu.core.timeutil import today_tpe

    info = build_serve_db(s.facts_path, s.serve_path, today=today_tpe(), templates_path=s.config_dir / "templates.yaml",
                          disabled_sources=s.disabled_sources)
    typer.echo(f"serve.db built: build_id={info.build_id} cards={info.n_cards}")


@app.command()
def crawl(
    only: list[str] = typer.Option(None, "--only", help="只跑这些 adapter（可重复）"),
    full: bool = typer.Option(None, "--full/--partial", help="强制全扫或只扫前几页（默认：当天第一次全扫）"),
    no_build: bool = typer.Option(False, "--no-build", help="抓完不重建 serve.db"),
) -> None:
    """礼貌抓取全部来源，写入 facts.db；资料有变动就重建 serve.db。"""
    from nknu.core.factsdb import connect, data_version, migrate
    from nknu.core.timeutil import today_tpe
    from nknu.crawl.pipeline import run_all

    s = _settings()
    conn = connect(s.facts_path)
    migrate(conn)
    before = data_version(conn)
    conn.close()
    results = run_all(s, today=today_tpe(), only=only or None, full_scan=full)
    for r in results:
        state = "ok" if r.passed else "VOID"
        typer.echo(f"{r.adapter:18} {state:4} records={r.n_records} written={r.n_written} pending={r.n_pending}"
                   + (f"  {r.error}" if r.error else ""))
    conn = connect(s.facts_path)
    changed = data_version(conn) != before
    conn.close()
    if changed and not no_build:
        _build_now(s)
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
    """逐条查看待审变更（场馆时段、闭馆、行事历改期），核准后才生效。"""
    from nknu.admin.review import decide, list_pending, render_item
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
    for it in items:
        typer.echo(render_item(conn, it, today_tpe(), tpl))
        typer.echo("-" * 60)
    choice = typer.prompt(f"共 {len(items)} 条。[y] 全部核准 / [e] 逐条处理 / [q] 离开", default="q").strip().lower()
    decided = 0
    if choice == "y":
        for it in items:
            decide(conn, it.id, "approve", iso_now())
            decided += 1
    elif choice == "e":
        for it in items:
            typer.echo(f"#{it.id} {it.record_key}\n{it.diff}")
            a = typer.prompt("[a] 核准 [r] 驳回 [s] 略过", default="s").strip().lower()
            if a in ("a", "r"):
                decide(conn, it.id, "approve" if a == "a" else "reject", iso_now())
                decided += 1
    conn.close()
    typer.echo(f"已处理 {decided} 条。")
    if decided and not no_build:
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
        fid = add_hours(conn, place=place, from_=date.fromisoformat(from_), to=date.fromisoformat(to) if to else None,
                        opens=opens, closes=closes, closed=closed, reason=reason, source_url=source_url, publisher=publisher,
                        title=title, site_visit=site_visit, now=iso_now())
    except (PlaceError, ValueError) as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(2) from exc
    finally:
        conn.close()
    typer.echo(f"已写入 fact {fid}")
    if not no_build:
        _build_now(s)


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
