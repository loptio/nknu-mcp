"""One search or one read from the command line, the same answer the MCP tools give: the JSON on stdout, its size
in UTF-8 bytes on stderr (the budget is respond.RESPONSE_BUDGET_BYTES). search is the server's default, hybrid_search
(plan 2026-11-02 T8); --no-dense and --no-rerank turn either model off, as on the server.

    python -m nknu.retrieval.cli [--db research/pipeline_2026-11-02/aca.db] [--no-dense] [--no-rerank] "115-1 停修"
    python -m nknu.retrieval.cli --db research/skeleton_2026-11-01/aca.db --read dp662 [--part 3]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nknu.core.timeutil import today_tpe
from nknu.retrieval.respond import to_json
from nknu.retrieval.server import DEFAULT_DB, add_model_flags, config_from, default_search, read_answer, search_answer


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.retrieval.cli",
                                 description="查一次 search 或 read，印出回传的 JSON")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help="内容资料库（默认 research/pipeline_2026-11-02/aca.db）")
    ap.add_argument("query", nargs="?", help="搜索的问题或关键词")
    ap.add_argument("--read", metavar="ID", help="改成读这个 id（d… 文件、e… 行事历）")
    ap.add_argument("--part", default="", help="和 --read 一起用：节号、toc 或 节号:位置")
    add_model_flags(ap)
    args = ap.parse_args(argv)
    if (args.query is None) == (args.read is None):
        ap.error("要给一个 query，或者 --read ID（二选一）")
    if not args.db.is_file():
        print(f"找不到内容资料库 {args.db}；先跑 python -m nknu.content.build", file=sys.stderr)
        return 2
    today = today_tpe()
    if args.read is not None:
        resp = read_answer(args.db, args.read, args.part, today=today)
    else:
        resp = search_answer(args.db, args.query, search_fn=default_search(config_from(args)), today=today)
    text = to_json(resp)
    print(text)
    print(f"{len(text.encode('utf-8'))} bytes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
