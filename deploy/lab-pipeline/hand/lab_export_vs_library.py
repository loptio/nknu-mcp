"""11-18 (session 4bd91945), read only: a site's new export against the documents the library in use holds of that
site, before the site is let go: every document's address in the new manifest (a row with a file, a row without one
and why, no row), and what the new manifest holds by kind and mark.
Usage: lab_export_vs_library.py SITE NEW_EXPORT_FOLDER"""
import collections
import csv
import sqlite3
import sys
from pathlib import Path

site, folder = sys.argv[1:3]
c = sqlite3.connect("file:/work/research/pipeline_2026-11-02/aca.db?mode=ro", uri=True)
docs = c.execute("SELECT doc_id, kind_raw, title, url, length(text) FROM docs WHERE site=?", (site,)).fetchall()
with (Path(folder) / "manifest.csv").open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
by = {}
for r in rows:
    for a in (r.get("网址"), r.get("最后落地网址")):
        if a and (a not in by or (r.get("本地档案") and not by[a].get("本地档案"))):
            by[a] = r
state = collections.Counter()
odd = []
for doc_id, kind, title, url, n in docs:
    r = by.get(url)
    if r is None:
        s = "no row"
    elif r.get("本地档案"):
        s = "a row with a file" + ("; marked: " + r["缺什么"][:40] if any(w in (r.get("缺什么") or "") for w in ("学校回答没有资料", "学校已删除", "转到别的页")) else "")
    else:
        s = f"a row without a file: {r.get('分类')}/{r.get('原因代码')}"
    state[s] += 1
    if not s.startswith("a row with a file") or "marked" in s:
        odd.append((s, kind, (title or "")[:30], url[-70:], n))
print(f"{site}: {len(docs)} documents in the library in use; kinds {collections.Counter(d[1] for d in docs).most_common(6)}")
for s, n in state.most_common():
    print(f"  {n:5d}  {s}")
for o in odd[:10]:
    print("     ", o)
print(f"new manifest: {len(rows)} rows; with a file {sum(1 for r in rows if r.get('本地档案'))}")
print("  by 类型/分类:", collections.Counter((r.get("类型"), r.get("分类")) for r in rows).most_common(12))
print("  缺什么 (with a file):", collections.Counter((r.get("缺什么") or "")[:44] for r in rows if r.get("本地档案")).most_common(6))
press = [r for r in rows if "#按钮=" in (r.get("网址") or "")]
print("  button rows:", len(press), "with a file", sum(1 for r in press if r.get("本地档案")), "with 按钮编号", sum(1 for r in press if r.get("按钮编号")))
for r in press[:2]:
    print("     ", (r.get("标题") or "")[:50], "|", (r.get("网址") or "")[-60:], "|", r.get("类型"))
titles = collections.Counter(r.get("标题") for r in rows if r.get("本地档案") and "syllabus" in (r.get("网址") or "").lower())
print("  outline rows with a file:", sum(titles.values()), "| distinct titles:", len(titles), "| the commonest:", titles.most_common(3))
