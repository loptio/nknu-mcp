"""Read only: one site's export folder against another of it (the one the official library uses against a new one;
or the same ledger exported by two versions of the program), manifest.csv row by row:
- rows gone, rows that had a file and have none (each must have a reason before a folder is handed on), rows added,
  and how the rows in both moved between classes, by reason;
- the pictures each page's file shows that the manifest holds a file for, page by page (11-19): a reader pairs a
  page's <img> with the manifest's row by address, so a page whose pictures' rows stand under other addresses loses
  what its pictures say, though no row lost a file. Pages that pair fewer pictures than before are listed.
Usage: python compare_export.py <old folder> <new folder>"""
import csv
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin


def rows(folder: Path) -> dict:
    out, seen = {}, Counter()
    with (folder / "manifest.csv").open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            k = (r["网址"], r.get("按钮编号", ""))
            seen[k] += 1
            out[(*k, seen[k])] = r
    return out


def host(url: str) -> str:
    return url.split("/")[2] if "://" in url else "-"


old, new = Path(sys.argv[1]), Path(sys.argv[2])
a, b = rows(old), rows(new)
print(f"{old.name} → {new.name}: rows {len(a)} → {len(b)}; with a file {sum(1 for r in a.values() if r['本地档案'])} → "
      f"{sum(1 for r in b.values() if r['本地档案'])}")
gone = [k for k in a if k not in b]
print(f"rows gone: {len(gone)}", Counter((a[k]["类型"], a[k]["分类"], "with a file" if a[k]["本地档案"] else "no file")
                                         for k in gone).most_common(6))
for k in gone[:3]:
    print("   gone:", a[k]["类型"], a[k]["分类"], a[k].get("原因代码", ""), k[0][-100:], "|", k[1][:30])
lost = [k for k in a if k in b and a[k]["本地档案"] and not b[k]["本地档案"]]
print(f"rows that had a file and have none: {len(lost)}")
for key, n in Counter((a[k]["类型"], b[k]["分类"], b[k].get("原因代码", ""), host(k[0])) for k in lost).most_common(8):
    print(f"  {n:6d}  {key}")
for k in lost[:3]:
    print("   lost:", k[0][-110:], "|", b[k].get("原因", "")[:60])
added = [k for k in b if k not in a]
print(f"rows added: {len(added)}", Counter((b[k]["类型"], b[k]["分类"], b[k].get("原因代码", ""), bool(b[k]["本地档案"]))
                                           for k in added).most_common(8))
both = [k for k in a if k in b]
moved = Counter((a[k]["类型"], a[k]["分类"], a[k].get("原因代码", ""), "→", b[k]["分类"], b[k].get("原因代码", ""))
                for k in both if (a[k]["分类"], a[k].get("原因代码", "")) != (b[k]["分类"], b[k].get("原因代码", "")))
print(f"rows whose class or reason changed: {sum(moved.values())}")
for key, n in moved.most_common(10):
    print(f"  {n:6d}  {' '.join(key)}")
cols = Counter(c for k in both for c in a[k] if c != "序号" and a[k][c] != b[k].get(c))
print("columns that changed (rows in both):", dict(cols.most_common(10)))
print("classes now:", dict(Counter(r["分类"] for r in b.values()).most_common()))


IMG = re.compile(r"""(?is)<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")


def paired(folder: Path, got: dict) -> dict[str, tuple[int, int]]:
    """For each page of the folder with a file: how many pictures its file shows (<img src>, each address once), and
    how many of those the manifest holds a file for under that address."""
    with_file = {r["网址"] for r in got.values() if r["本地档案"] and r["类型"] != "网页"}
    out = {}
    for r in got.values():
        if r["类型"] != "网页" or not r["本地档案"] or not r["本地档案"].endswith((".html", ".htm")):
            continue
        try:
            html = (folder / r["本地档案"]).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        srcs = set()
        for m in IMG.finditer(html):
            src = (m.group(1) or m.group(2) or m.group(3) or "").strip().replace("&amp;", "&")
            if src and not src.startswith("data:"):
                srcs.add(urljoin(r["网址"], src).split("#", 1)[0])
        out[r["网址"]] = (len(srcs), len(srcs & with_file))
    return out


pa, pb = paired(old, a), paired(new, b)
shown_a, held_a = (sum(v[i] for v in pa.values()) for i in (0, 1))
shown_b, held_b = (sum(v[i] for v in pb.values()) for i in (0, 1))
fewer = sorted(((pa[u][1] - pb[u][1], u) for u in pa if u in pb and pb[u][1] < pa[u][1]), reverse=True)
print(f"pictures the pages' files show: {shown_a} → {shown_b}; of them with a file under that address: {held_a} → {held_b}")
print(f"pages that pair fewer pictures than before: {len(fewer)}" + (f" (in all {sum(n for n, _u in fewer)} pictures)" if fewer else ""))
for n, u in fewer[:5]:
    print(f"   -{n}  {pa[u][1]} → {pb[u][1]} of {pb[u][0]} shown  {u[-110:]}")
