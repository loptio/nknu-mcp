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
