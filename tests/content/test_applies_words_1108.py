"""Owner 11-08 10:16 (b): a document whose scope applies to a college, a department or a program (doc_scope's
applies_to) carries that unit's name and short names on its unit line (適用：…) and in every chunk's unit tokens, so a
long 教務處 file for one department finds 「某系 + 某事」 in the chunks that never name it; an office or a center does
not, nor a unit the line names already. No model loads; the names are made up."""

from __future__ import annotations

import sqlite3

from nknu.content import ids
from nknu.content.build import APPLIES_LABEL, UNIT_LABEL, UnitWords
from nknu.content.embed import chunk_passages
from tests.content import test_multisite_1108 as tm

ACA = tm.ACA
FILLER = "\n".join(f"第{i}條 課程規劃說明，依本校課程規劃辦理。" for i in range(1, 120))  # long, so chunks are many
TABLE = ACA + "page.aspx?pn=31"  # 教務處's file for one department: its header names it, its later chunks do not
CHEM = ACA + "page.aspx?pn=32"  # one for another department, with the same words late in it
OFFICE = ACA + "page.aspx?pn=33"  # one for an office


def aca_input(tmp_path):
    return tm.hand_input(tmp_path / "aca", "c.nknu.edu.tw/aca", "教务处", [
        (TABLE, "114學年度入學 開課系統表", f"軟體工程與管理學系學士班 開課系統表\n{FILLER}\n畢業學分：128學分"),
        (CHEM, "114學年度入學 課程表", f"化學系學士班 課程表\n{FILLER}\n畢業學分：132學分"),
        (OFFICE, "宿舍申請", "總務處 出納組 宿舍費繳費說明"),
    ])


def doc_id(url: str) -> str:
    return "d" + ids.record_id(ids.location_key(url))


def test_the_line_names_the_department_a_file_applies_to(tmp_path):
    out = tmp_path / "x.db"
    report = tm.run(tm.tree_files(tmp_path / "tree"), out, inputs=[aca_input(tmp_path)])
    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        lines = {d: conn.execute("SELECT unit_line FROM docs WHERE doc_id = ?", (doc_id(u),)).fetchone()[0]
                 for d, u in (("table", TABLE), ("chem", CHEM), ("office", OFFICE))}
        units = {r[0] for r in conn.execute(
            "SELECT f.unit_tokens FROM chunk_fts f JOIN chunks c ON c.chunk_id = f.rowid WHERE c.doc_id = ?",
            (doc_id(TABLE),))}
        chunks = [c for (c,) in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id = ? ORDER BY chunk_id",
                                             (doc_id(TABLE),))]
        last = conn.execute("SELECT text FROM docs WHERE doc_id = ?", (doc_id(TABLE),)).fetchone()[0]
        end = conn.execute("SELECT start, end FROM chunks WHERE chunk_id = ?", (chunks[-1],)).fetchone()
        # the chunk that says 畢業學分 and never names the department: found by the department's word alone
        matched = {r[0] for r in conn.execute("SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH 'unit_tokens:软工'")}
        passage = chunk_passages(conn, [chunks[-1]])[chunks[-1]].split("\n")
    finally:
        conn.close()
    assert lines == {"table": f"{UNIT_LABEL}教務處 {APPLIES_LABEL}{tm.tb.SOFT}（軟體系、軟工系）",
                     "chem": f"{UNIT_LABEL}教務處 {APPLIES_LABEL}化學系",
                     "office": f"{UNIT_LABEL}教務處"}  # 總務處 and 出納組 are an office and a section: no 適用
    assert len(chunks) > 3 and len(units) == 1 and {"软体", "体系", "软工"} <= set(units.pop().split())  # every chunk
    assert "畢業學分" in last[end[0]:end[1]] and "軟體" not in last[end[0]:end[1]] and chunks[-1] in matched
    assert passage[1] == lines["table"]  # the reranker reads the line with the chunk that does not name it
    assert report.scope["unit_line_applies"] == {"docs": 2, "by_count": {1: 2}}


def test_a_unit_the_line_names_already_is_not_said_again():
    words = UnitWords(name=tm.tb.SOFT, aliases=("軟體系",), applies=(("文學院", ()),))
    assert words.line == f"{UNIT_LABEL}{tm.tb.SOFT}（軟體系） {APPLIES_LABEL}文學院"
    assert words.words == (tm.tb.SOFT, "軟體系", "文學院")
    assert UnitWords(applies=((tm.tb.SOFT, ("軟體系",)),)).line == f"{APPLIES_LABEL}{tm.tb.SOFT}（軟體系）"
