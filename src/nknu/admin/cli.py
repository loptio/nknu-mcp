"""`nknu` command line: init, crawl, build, review, add, serve, check."""

from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="NKNU 校园事实层（v1，只读 MCP）")


@app.callback()
def main() -> None:
    """NKNU 校园事实层（v1，只读 MCP）。"""
