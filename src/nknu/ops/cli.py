"""The two commands of the inspection tools (plan 2026-11-15 T4; spec 2026-11-15 section 二): nknu search and nknu
trace. Each prints what nknu.ops.explain and nknu.ops.trace return, as text or as JSON.

Two places to ask. --db opens a library on this machine (the lab PC, where the library and the models are). --at, or the
environment's NKNU_OPS_URL when no --db is given, asks 检视台 (nknu.ops.web) instead: on the Mac, which holds no
library, that is the lab PC through the tunnel of scripts/inspect-tunnel.sh, and the answer is the same dict.

nknu.admin.cli registers the two (register); everything heavy is imported inside the functions, so the other commands
load as before."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

import typer

from nknu.core.timeutil import today_tpe

ENV = "NKNU_OPS_URL"
TIMEOUT = 180.0  # seconds: a first search loads the two models (29 s on the lab PC, 11-15) 【Claude】
REACH = f"先在 Mac 上开隧道（scripts/inspect-tunnel.sh），再设 {ENV}=http://127.0.0.1:18878 或加 --at"


class Failed(Exception):
    """What to tell the owner when a command cannot answer; exit code 2."""


def _service(at: str | None, db: Path | None) -> str | None:
    """The service to ask: --at; else the environment's, unless a library was named."""
    return at or (None if db is not None else os.environ.get(ENV) or None)


def _ask(base: str, path: str, params: dict) -> dict:
    """The service's JSON answer. trust_env=False: no proxy of the environment or of the system is followed, so the
    question's words go to the service alone (review 11-15)."""
    import httpx  # here, not at the top: nknu.admin.cli imports this module for every command

    url = base.rstrip("/") + path
    try:
        answer = httpx.get(url, params=params, timeout=TIMEOUT, trust_env=False)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise Failed(f"连不上检视台 {base}（{type(exc).__name__}）。{REACH}；在实验室电脑上就用 --db。") from None
    if answer.status_code != 200:
        try:
            said = answer.json().get("error")
        except (ValueError, AttributeError):
            said = None
        if said:  # it was reached and failed inside: its own words, no advice about the tunnel
            raise Failed(f"检视台出错了：{said}（完整的经过在它的纪录里）")
        raise Failed(f"检视台 {base} 回了 {answer.status_code}：{answer.text[:200]}。{REACH}。")
    return answer.json()


def _library(db: Path | None) -> Path:
    from nknu.retrieval.server import DEFAULT_DB

    path = Path(db) if db is not None else DEFAULT_DB
    if not path.is_file():
        raise Failed(f"找不到内容资料库 {path}。这台电脑上没有库的话：{REACH}。")
    return path


def search(query: str, *, explain: bool = False, follow: Iterable[str] = (), limit: int = 10, as_json: bool = False,
           db: Path | None = None, at: str | None = None, no_dense: bool = False, no_rerank: bool = False) -> str:
    """What nknu search prints: without explain, the JSON the MCP tool `search` answers; with it, why the search
    ranked as it did (explain_text, or the report as JSON)."""
    follow = list(follow)
    if (base := _service(at, db)) is not None:
        if no_dense or no_rerank:
            raise Failed("--no-dense、--no-rerank 只能配 --db 用：检视台用的是它自己的设定。")
        answer = _ask(base, "/api/search", {"q": query, "limit": str(limit), "explain": "1" if explain else "0",
                                            "doc": follow})
        if not explain:
            from nknu.retrieval.respond import to_json

            return to_json(answer)  # as the local mode prints it
        from nknu.ops.explain import explain_text

        return json.dumps(answer, ensure_ascii=False, indent=1) if as_json else explain_text(answer)
    path = _library(db)
    from nknu.ops.explain import explain_search, explain_text
    from nknu.retrieval import server
    from nknu.retrieval.hybrid import HybridConfig
    from nknu.retrieval.respond import to_json

    config = HybridConfig(dense=not no_dense, rerank=not no_rerank) if no_dense or no_rerank else None
    if not explain:
        return to_json(server.search_answer(path, query, search_fn=server.default_search(config), today=today_tpe()))
    conn = server.connect(path)
    try:
        report = explain_search(conn, query, today=today_tpe(), aliases=server.aliases_of(conn), config=config,
                                limit=limit, follow=follow)
    finally:
        conn.close()
    return json.dumps(report, ensure_ascii=False, indent=1) if as_json else explain_text(report)


def trace_ref(ref: str, *, as_json: bool = False, db: Path | None = None, root: Path | None = None,
              at: str | None = None, chunks: int = 3) -> str:
    """What nknu trace prints: one page or file at each step (trace_text, or the report as JSON)."""
    from nknu.ops.trace import trace, trace_text

    if (base := _service(at, db)) is not None:
        report = _ask(base, "/api/trace", {"ref": ref})
    else:
        path = _library(db)
        from nknu.retrieval import server
        from nknu.settings import REPO_ROOT

        conn = server.connect(path)
        try:
            report = trace(conn, ref, root=Path(root) if root is not None else REPO_ROOT, today=today_tpe())
        finally:
            conn.close()
    return json.dumps(report, ensure_ascii=False, indent=1) if as_json else trace_text(report, chunks=chunks)


def _echo(answer: Callable[[], str]) -> None:
    """Prints the answer; what cannot be answered is said in words with exit code 2, never as a traceback: a service
    that does not answer (Failed), and a library or one of its files that cannot be read (a file that is no
    database, a layout too old for a query)."""
    try:
        typer.echo(answer())
    except Failed as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    except (sqlite3.Error, OSError) as exc:
        typer.echo(f"读不了这个库，或它旁边的档：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(2) from None


def register(app: typer.Typer) -> None:
    """Adds nknu search and nknu trace to app."""

    @app.command("search")
    def search_cmd(query: str = typer.Argument(..., help="问题或关键词"),
                   explain: bool = typer.Option(False, "--explain", help="列出每一笔的各项分数和折扣，说明为什么这样排"),
                   doc: list[str] = typer.Option(None, "--doc", help="想追的文件（编号或网址，可以给几次）：说它停在哪一步"),
                   limit: int = typer.Option(10, "--limit", help="看前几名"),
                   as_json: bool = typer.Option(False, "--json", help="印 JSON（给程序读）"),
                   db: Path = typer.Option(None, "--db", help="直接读这个库（在实验室电脑上用）"),
                   at: str = typer.Option(None, "--at", help=f"问这个网址上的检视台（不给就读环境变数 {ENV}）"),
                   no_dense: bool = typer.Option(False, "--no-dense", help="不用向量（只配 --db）"),
                   no_rerank: bool = typer.Option(False, "--no-rerank", help="不用重排模型（只配 --db）")) -> None:
        """搜一次：印出学生的 AI 会拿到的回传；加 --explain 看前几名的各项打分、折扣，和某一份为什么没排上来
        （--doc）。Mac 上没有库：先开隧道 scripts/inspect-tunnel.sh，再设 NKNU_OPS_URL。"""
        _echo(lambda: search(query, explain=explain, follow=doc or (), limit=limit, as_json=as_json, db=db, at=at,
                             no_dense=no_dense, no_rerank=no_rerank))

    @app.command("trace")
    def trace_cmd(ref: str = typer.Argument(..., help="网址或文件编号"),
                  as_json: bool = typer.Option(False, "--json", help="印 JSON（给程序读）"),
                  chunks: int = typer.Option(3, "--chunks", help="印出前几个小段的字"),
                  db: Path = typer.Option(None, "--db", help="直接读这个库（在实验室电脑上用）"),
                  root: Path = typer.Option(None, "--root", help="库里的相对路径从哪个资料夹算起（实验室电脑是 /work）"),
                  at: str = typer.Option(None, "--at", help=f"问这个网址上的检视台（不给就读环境变数 {ENV}）")) -> None:
        """追一份文件：它在抓取、导出、转文字、建库、检索每一步各是什么样；没进库的说原因。"""
        _echo(lambda: trace_ref(ref, as_json=as_json, db=db, root=root, at=at, chunks=chunks))
