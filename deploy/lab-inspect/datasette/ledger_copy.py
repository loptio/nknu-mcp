"""The crawler's ledger for 資料庫 (owner 11-23: 「frontier.db 为什么没有在资料库页面看的」「加，用每晚备份」).

Datasette opens the newest nightly backup of the ledger (D:\\nknu-backup\\crawler-nightly, made before each night's
crawl and never written again), as a copy in the container: the ledger holds four views (url_result, action_result,
page_resource, page_completeness) that the crawler's newer SQLite builds and Datasette's SQLite 3.40 cannot parse
(「parser stack overflow」, and then Datasette does not start at all, 11-23 05:17). The copy drops those views; every
table stays as it is. Run before Datasette starts (compose.yaml); it never fails the start: with no backup, or a copy
that does not work, it says so and leaves no file, and 資料庫 opens the library alone.

    python ledger_copy.py <backups folder> <copy>
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path


def copy(backups: Path, dest: Path) -> Path | None:
    """The newest nightly-*.db under backups, copied to dest without its views; None (and no dest) when it cannot."""
    found = sorted(backups.glob("nightly-*.db"))
    dest.unlink(missing_ok=True)
    if not found:
        print(f"ledger_copy: {backups} 里没有每晚备份，资料库只开正式库", flush=True)
        return None
    src = found[-1]
    part = dest.with_name(dest.name + ".part")
    try:
        shutil.copyfile(src, part)
        conn = sqlite3.connect(part)
        try:
            conn.execute("PRAGMA writable_schema=ON")  # the schema is read without parsing the views this SQLite cannot
            conn.execute("DELETE FROM sqlite_master WHERE type = 'view'")
            conn.commit()
            conn.execute("PRAGMA writable_schema=RESET")
        finally:
            conn.close()
        check = sqlite3.connect(f"file:{part}?immutable=1", uri=True)
        try:
            check.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            check.close()
        part.replace(dest)
    except (OSError, sqlite3.Error) as e:
        part.unlink(missing_ok=True)
        print(f"ledger_copy: {src.name} 复制不成（{e}），资料库只开正式库", flush=True)
        return None
    print(f"ledger_copy: {src.name} → {dest}（拿掉了帐本的检视）", flush=True)
    return dest


if __name__ == "__main__":
    copy(Path(sys.argv[1]), Path(sys.argv[2]))
