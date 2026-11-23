"""Datasette plugin: which columns a table's page shows (owner 11-15: 「我想看原始數據長什麼樣子，有幾個欄位，我想挑選我感興趣
的欄位看（默認就顯示一些比較重要的欄位）這些實現了嗎？為什麼我找不到怎麼用？」).

Datasette can show some of a table's columns (?_col=a&_col=b) but only from a menu hidden in each header, and a page
opens on all of them. So, all 【Claude】:

- a wide table's page opens on the few columns that matter (DEFAULT): a request for such a page that names no
  column is given those, before Datasette reads it, so every link Datasette then builds (sort, next page, facet,
  download) keeps them. Only the page: `<table>.json` and `.csv` asked directly stay whole.
- the page gets one chip per column of the table (templates/table.html draws them over the rows): shown or not,
  and the address that shows the same page with that one column switched; and the two addresses 「重要的」 (the
  default again) and 「全部」.

Loaded by `datasette --plugins-dir`; tests/ops/test_deploy_files.py loads it the same way."""

from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlencode

try:
    from datasette import hookimpl
except ImportError:  # the tests load this file where Datasette is not installed

    def hookimpl(fn):
        return fn


# the columns a table's page opens on (its key columns are always shown); a table not named here opens whole
DEFAULT = {
    "docs": ("title", "kind_class", "unit", "ident_date", "tier", "chars", "url"),
    "sites": ("name", "unit", "docs", "data_as_of", "missing_pages", "missing_files"),
    # the crawler's ledger (database frontier, 11-23)
    "url": ("url", "site", "kind", "found_via", "state", "reason", "status", "fetched_at"),
    "request": ("url", "purpose", "method", "status", "error", "campus", "bytes"),
}
CHOICE = ("_col", "_nocol")  # Datasette's own two ways to say which columns
MARK = "nknu_default_columns"  # set on the request's scope when the columns are the default, not a choice


def with_default(path: str, query: bytes) -> bytes | None:
    """The query string with the table's default columns added, or None when this request is not a table's page
    that names no column (another page, a table with no default, .json/.csv, a choice already made)."""
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        return None
    columns = DEFAULT.get(unquote(parts[1]))
    if not columns:
        return None
    if any(key in CHOICE for key, _ in parse_qsl(query.decode("latin-1"), keep_blank_values=True)):
        return None
    added = urlencode([("_col", c) for c in columns]).encode("latin-1")
    return query + b"&" + added if query else added


@hookimpl
def asgi_wrapper(datasette):
    def wrap(app):
        async def columns_first(scope, receive, send):
            if scope.get("type") == "http":
                query = with_default(scope.get("path") or "", scope.get("query_string") or b"")
                if query is not None:
                    scope = dict(scope, query_string=query)
                    scope[MARK] = True
            await app(scope, receive, send)

        return columns_first

    return wrap


def chips(names: list[str], keys: list[str], args: list[tuple[str, str]], base: str, table: str, by_default: bool) -> dict:
    """What the page draws: every column with whether it is shown and the address that switches it. args is the
    request's query as (key, value) pairs; a key column is always shown and has no address."""
    cols = [v for k, v in args if k == "_col"]
    hidden = {v for k, v in args if k == "_nocol"}
    shown = [c for c in names if c in keys or (c in cols if cols else c not in hidden)]
    keep = [(k, v) for k, v in args if k not in (*CHOICE, "_next")]

    def url(picked: list[str]) -> str:
        query = urlencode(keep + [("_col", c) for c in picked if c not in keys])
        return f"{base}?{query}" if query else base

    def switched(column: str) -> str:
        picked = [c for c in names if (c in shown) != (c == column)]
        # nothing left but the keys would read as "no choice": Datasette's own word for it keeps the page narrow
        return url(picked) if any(c not in keys for c in picked) else url([]) + ("&" if keep else "?") + urlencode(
            [("_nocol", c) for c in names if c not in keys])

    default = DEFAULT.get(table)
    return {
        "columns": [{"name": c, "shown": c in shown, "key": c in keys, "url": None if c in keys else switched(c)}
                    for c in names],
        "shown": len(shown), "total": len(names), "by_default": by_default,
        "default_url": url([]) if default else None, "all_url": url(names) if default else url([]),
        "narrowed": len(shown) < len(names),
    }


@hookimpl
def extra_template_vars(template, database, table, request, datasette):
    if template != "table.html" or not database or not table or request is None:
        return {}

    async def inner():
        db = datasette.get_database(database)
        names = list(await db.table_columns(table))
        keys = list(await db.primary_keys(table))
        args = [(key, value) for key in request.args for value in request.args.getlist(key)]
        return {"pick": chips(names, keys, args, datasette.urls.table(database, table), table,
                              bool(request.scope.get(MARK)))}

    return inner
