"""Units, aliases and scope detection (addendum 2026-11-02 F, task T6c). The site list and the config here are made up;
their shape follows research/site_probe_2026-10-23/data.json and config/units.yaml. Two tests read a real file, the
shipped config/units.yaml (no site list, no research data): the last checks it on its own, and the 軟工 test (addendum
H5) loads two of its entries with the made-up list."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from nknu.content.units import (
    CAMPUSES,
    LEVELS,
    Scope,
    Unit,
    ancestors,
    detect,
    doc_scope,
    load_units,
    reach,
    read_config,
    relation,
    resolve,
)

REPO = Path(__file__).resolve().parents[2]
SCHOOL = "國立高雄師範大學"
SOFT = "軟體工程與管理學系"
CHEM = "化學系"
CHINESE = "國文學系"


def site(n: str, k: str, p: str = "", a: tuple[str, ...] = (), s: str = "第一轮") -> dict:
    """One entry as the site probe writes it; only n, a, p, k and s are read."""
    return {"n": n, "a": list(a), "p": p, "k": k, "u": "https://example.invalid/", "h": "example.invalid",
            "c": "direct", "t": [], "r": 0, "R": 0, "x": "", "d": "", "s": s}


SITES = [
    site(f"{SCHOOL}（校首頁）", "学校", a=(f"{SCHOOL} 校首頁（新版）",)),
    site(f"{SCHOOL} English", "学校", p=SCHOOL),  # another page of the school, not a unit
    site("教務處", "行政单位", a=("教務處（教務長室）",)),
    site("和平教務組", "行政组别", p="教務處"),
    site("燕巢教務組", "行政组别", p="教務處"),
    site("招生企劃組", "行政组别", p="教務處"),
    site("進修學院", "行政单位", p="進修學院", a=("教師專業發展研究中心", "進修學院（院長室）")),  # names itself
    site("教務組", "行政组别", p="進修學院", a=("進修學院 教務組",)),
    site("科技學院", "学院"),
    site("理學院", "学院"),
    site("文學院", "学院"),
    site(SOFT, "系所", p="科技學院"),
    site("電機工程學系", "系所", p="科技學院", a=("品保網站總覽",)),
    site(CHEM, "系所", p="理學院"),
    site("數學系", "系所", p="理學院"),
    site("示範學系", "系所", p="理學院"),
    site(f"{CHINESE}（系所博覽連結）", "系所", p="文學院"),
    site(CHINESE, "系所", p="文學院"),
    site("英語學系", "系所", p="文學院"),
    site("東南亞學碩士學位學程", "系所", p="文學院", a=("東南亞研究中心",)),
    site("東南亞研究中心（舊站）", "中心", a=("東南亞研究中心",)),
    site("示範碩士班", "系所"),  # the list gives no parent; the config does
    site("通識教育中心", "中心", p="一個不在名單上的單位"),
    site("語文教學中心 華語課程子站", "中心", p="語文教學中心"),  # a sub-site, not a unit
    site("人工智慧學分學程", "学程/计划", p=SOFT),
    site("高師軟工 Facebook（系所）", "系所", p=SOFT, s="第二轮"),  # another presence of a unit
    site("高師大電機系 (Facebook)", "系所", p="電機工程學系", s="第二轮"),  # looks like a unit; round 2 even so
    site("辯論社", "社团", p="課外活動指導組 社團一覽", s="名册"),
    site("選課系統", "校务系统"),
]

CONFIG = """\
units:
  科技學院: {campus: 燕巢, evidence: 燕巢教務組的業務範圍列出這個學院的系}
  理學院: {campus: 燕巢, evidence: 燕巢教務組的業務範圍列出這個學院的系}
  文學院: {campus: 和平, evidence: 和平教務組承辦這個學院的教務}
  示範學系: {campus: 和平, evidence: 一個系自己寫的校區蓋過學院的}
  和平教務組:
    campus: 和平
    aliases: [和平校區教務組]
    evidence: 送和平教務組（和平校區學系）彙辦
  燕巢教務組: {campus: 燕巢, evidence: 送燕巢教務組（燕巢校區學系）彙辦}
  招生企劃組: {aliases: [招生組], evidence: 洽詢招生組的分機是招生企劃組的}
  軟體工程與管理學系: {aliases: [軟體系, 軟工系], evidence: 業務範圍列出軟體系}
  國文學系: {aliases: [國文系], evidence: 業務範圍列出國文系}
  教務組: {name: 進修學院教務組, evidence: 單寫的教務組都指兩個校區的教務組}
  示範碩士班: {parent: 化學系, evidence: 簡章寫化學系示範碩士班}
"""


def write(tmp: Path, sites: list[dict] = SITES, config: str = CONFIG) -> tuple[Path, Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "data.json").write_text(json.dumps({"sites": sites, "nourl": [], "probed": "2026-10-23"},
                                              ensure_ascii=False), encoding="utf-8")
    (tmp / "units.yaml").write_text(config, encoding="utf-8")
    return tmp / "data.json", tmp / "units.yaml"


@pytest.fixture(scope="module")
def units(tmp_path_factory) -> dict[str, Unit]:
    return load_units(*write(tmp_path_factory.mktemp("units")))


def scope(units=(), campus=(), levels=(), cohort=None) -> Scope:
    return Scope(frozenset(units), frozenset(campus), frozenset(levels), cohort)


# --- load_units: the tree ---------------------------------------------------------------------------------------------

def test_interface():
    assert LEVELS == ("學士", "碩士", "博士", "進修")
    assert CAMPUSES == ("和平", "燕巢")
    assert [f.name for f in dataclasses.fields(Unit)] == ["unit_id", "name", "kind", "parent_id", "campus", "aliases"]
    assert [f.name for f in dataclasses.fields(Scope)] == ["units", "campus", "levels", "cohort_from"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope().cohort_from = 111  # type: ignore[misc]


def test_units_are_round_one_entries_of_the_unit_kinds(units):
    assert set(units) == {
        SCHOOL, "教務處", "和平教務組", "燕巢教務組", "招生企劃組", "進修學院", "進修學院教務組", "科技學院", "理學院",
        "文學院", SOFT, "電機工程學系", CHEM, "數學系", "示範學系", CHINESE, "英語學系", "東南亞學碩士學位學程",
        "東南亞研究中心", "示範碩士班", "通識教育中心", "人工智慧學分學程"}
    assert next(iter(units)) == SCHOOL
    assert all(u.unit_id == key == u.name for key, u in units.items())
    assert units[SCHOOL].kind == "学校" and units["和平教務組"].kind == "行政组别" and units[SOFT].kind == "系所"
    assert units["人工智慧學分學程"].kind == "学程"  # the list's 学程/计划


def test_parents_come_from_the_list_else_the_school(units):
    parent = {key: u.parent_id for key, u in units.items()}
    assert parent[SCHOOL] is None
    assert parent["教務處"] == SCHOOL and parent["和平教務組"] == "教務處"
    assert parent[SOFT] == "科技學院" and parent["科技學院"] == SCHOOL
    assert parent[CHINESE] == "文學院"  # its two entries are one unit
    assert parent["進修學院"] == SCHOOL  # an entry naming itself as parent
    assert parent["進修學院教務組"] == "進修學院"  # renamed by the config, found by its list name
    assert parent["通識教育中心"] == SCHOOL  # a parent the list does not have
    assert parent["示範碩士班"] == CHEM  # given by the config
    assert parent["人工智慧學分學程"] == SOFT


def test_ancestors(units):
    assert ancestors(units, SOFT) == [SOFT, "科技學院", SCHOOL]
    assert ancestors(units, "示範碩士班") == ["示範碩士班", CHEM, "理學院", SCHOOL]
    assert ancestors(units, SCHOOL) == [SCHOOL]
    with pytest.raises(KeyError):
        ancestors(units, "沒有這個單位")


def test_aliases_name_only_their_own_unit(units):
    assert units[SOFT].aliases == ("軟體系", "軟工系")
    assert units["和平教務組"].aliases == ("和平校區教務組",)
    assert units[SCHOOL].aliases == ()  # 「…校首頁」 names a page, not a unit
    assert units["教務處"].aliases == ()  # 「教務處（教務長室）」 is its own name with a note
    assert units["電機工程學系"].aliases == ()  # 「品保網站總覽」 is not a unit's name
    assert units["東南亞學碩士學位學程"].aliases == ()  # another unit's name
    assert units["進修學院"].aliases == ()  # an entry naming itself lists its parts, not its names
    assert units["進修學院教務組"].aliases == ()  # 「進修學院 教務組」 is its new name


def test_campus_is_inherited_unless_a_unit_sets_its_own(units):
    campus = {key: u.campus for key, u in units.items()}
    assert campus["科技學院"] == campus[SOFT] == campus["人工智慧學分學程"] == "燕巢"
    assert campus["示範碩士班"] == "燕巢"  # under 化學系, under 理學院
    assert campus["示範學系"] == "和平"  # its own entry wins over its college's
    assert campus[CHINESE] == campus["和平教務組"] == "和平" and campus["燕巢教務組"] == "燕巢"
    for key in (SCHOOL, "教務處", "招生企劃組", "進修學院", "通識教育中心", "東南亞研究中心"):
        assert campus[key] is None, key


def test_the_config_can_add_a_unit(tmp_path):
    config = CONFIG + "  教務長室: {kind: 行政组别, parent: 教務處, evidence: 教務長室的編制與業務頁}\n"
    added = load_units(*write(tmp_path, config=config))["教務長室"]
    assert (added.kind, added.parent_id, added.campus, added.aliases) == ("行政组别", "教務處", None, ())


@pytest.mark.parametrize(("entry", "error"), [
    ("科技學院: {campus: 台北, evidence: x}", ValueError),  # not one of CAMPUSES
    ("科技學院: {campus: 燕巢}", ValueError),  # no evidence
    ("科技學院: {campus: 燕巢, evidence: ' '}", ValueError),
    ("科技學院: {campuss: 燕巢, evidence: x}", ValueError),  # an unknown field
    ("不存在的學系: {campus: 燕巢, evidence: x}", ValueError),  # not in the list, and no kind and parent to add it
    ("新單位: {kind: 社团, parent: 教務處, evidence: x}", ValueError),  # not a unit kind
    ("化學系: {aliases: [數學系], evidence: x}", ValueError),  # another unit's name
    ("化學系: {aliases: [軟體系], evidence: x}", ValueError),  # an alias SOFT already has
    ("科技學院: {parent: 軟體工程與管理學系, evidence: x}", ValueError),  # a loop
    ("化學系: {aliases: ['（）'], evidence: x}", ValueError),  # nothing to match
    ("化學系: [燕巢]", TypeError),
])
def test_a_broken_config_stops_the_load(tmp_path, entry, error):
    with pytest.raises(error):
        load_units(*write(tmp_path, config=CONFIG + "  " + entry + "\n"))


def test_a_config_that_is_not_a_mapping_of_units_stops_the_load(tmp_path):
    for text in ("- 科技學院\n", "units: [科技學院]\n"):
        with pytest.raises(TypeError):
            load_units(*write(tmp_path, config=text))
    assert set(load_units(*write(tmp_path, config=""))) == set(load_units(*write(tmp_path, config="units:\n")))


def test_a_list_without_a_school_stops_the_load(tmp_path):
    with pytest.raises(ValueError):
        load_units(*write(tmp_path, sites=SITES[2:], config=""))


# --- detect -----------------------------------------------------------------------------------------------------------

def test_names_and_aliases_longest_first(units):
    assert detect(f"{SOFT} 碩士班", units) == scope({SOFT}, levels={"碩士"})
    assert detect("軟體系碩士班", units).units == {SOFT}
    assert detect("教務處燕巢教務組公告", units).units == {"教務處", "燕巢教務組"}
    assert detect("和平校區教務組", units) == scope({"和平教務組"}, {"和平"})  # the campus word inside it counts too


def test_the_short_name_ruan_gong_from_the_shipped_config(tmp_path):
    # H5 (addendum, main session 11-02): 招生企劃組's past exams write
    # 「系所別：數學、物理、光通、電子、軟工等系二年級」, and the owner asks 「高师大软工的毕业门槛」 (core20 Q50).
    # The shipped entries of the department and the school, loaded with the made-up list.
    shipped = read_config(REPO / "config" / "units.yaml")
    assert shipped[SOFT]["aliases"] == ["軟體系", "軟工系", "軟工"] and "軟工等系二年級" in shipped[SOFT]["evidence"]
    entries = {name: {k: v for k, v in shipped[name].items() if v} for name in (SOFT, SCHOOL)}
    u = load_units(*write(tmp_path, config=yaml.safe_dump({"units": entries}, allow_unicode=True)))
    assert u[SOFT].aliases == ("軟體系", "軟工系", "軟工") and resolve("軟工", u) == SOFT
    assert detect("高師大軟工的畢業門檻", u).units == {SCHOOL, SOFT}
    assert detect("高师大软工的毕业门槛", u).units == {SCHOOL, SOFT}
    # 軟工系 still wins the longest match: taken whole before 軟工, with nothing left over beside it
    assert detect("軟工系的必修科目", u).units == {SOFT}
    assert detect("軟工系、化學系與國文學系", u).units == {SOFT, CHEM, CHINESE}
    assert detect("新版選課軟體系統上線", u).units == frozenset()  # 系統 still guards 軟體系


def test_simplified_and_spaced_forms(units):
    assert detect("软体工程与管理学系 硕士班", units) == scope({SOFT}, levels={"碩士"})
    assert detect("軟 體 工 程 與 管 理 學 系", units).units == {SOFT}
    assert detect("国文学系", units).units == {CHINESE}


def test_words_that_only_contain_a_name(units):
    assert detect("新版選課軟體系統上線", units).units == frozenset()  # 系統
    assert detect("國文系列講座", units).units == frozenset()  # 系列
    assert detect("招生組別：一般生", units).units == frozenset()  # 組別
    assert detect("招生組長", units).units == {"招生企劃組"}
    assert detect("請依教務組公告辦理", units).units == frozenset()  # renamed: the bare name is no unit's
    assert detect("進修學院教務組", units).units == {"進修學院教務組"}


def test_campus_words(units):
    assert detect("燕巢校區停水公告", units).campus == {"燕巢"}
    assert detect("和平與燕巢校區交通說明", units).campus == {"和平", "燕巢"}
    assert detect("燕巢校区", units).campus == {"燕巢"}
    assert detect("二二八和平紀念日放假", units).campus == frozenset()  # 和平 alone is a word, not a campus
    assert detect("高雄市苓雅區和平一路", units).campus == frozenset()


@pytest.mark.parametrize(("text", "levels"), [
    ("大學部新生", {"學士"}), ("日間學士班", {"學士"}), ("學士學位", {"學士"}),
    ("碩士班考試", {"碩士"}), ("研究所甄試", {"碩士"}), ("碩士學位考試", {"碩士"}),
    ("碩士在職專班", {"碩士", "進修"}), ("在職專班", {"進修"}), ("碩士在職學位學程", {"碩士", "進修"}),
    ("進修部", {"進修"}), ("博士班", {"博士"}),
    ("碩博士班", {"碩士", "博士"}), ("博碩士班", {"碩士", "博士"}), ("碩、博士班", {"碩士", "博士"}),
    ("学士班", {"學士"}), ("硕士班", {"碩士"}),
    ("學士後多元專長", set()), ("名譽博士頒授", set()), ("博士後研究", set()), ("學生證", set()),
])
def test_level_words(units, text, levels):
    assert detect(text, units).levels == levels


def test_cohort_follows_the_existing_rule(units):
    assert detect("111學年度起入學學生適用", units).cohort_from == 111
    assert detect("108 學年度（含）以後入學", units).cohort_from == 108
    assert detect("113學年度入學新生", units).cohort_from is None  # one cohort, not 「起」: applies_to's cohort_exact
    assert detect("115學年度第1學期", units).cohort_from is None


def test_nothing_named(units):
    assert detect("", units) == scope()
    assert detect("期末考週注意事項", units) == scope()


# --- doc_scope --------------------------------------------------------------------------------------------------------

def test_an_office_notice_applies_to_the_school(units):
    assert doc_scope("期末考週注意事項", "", "教務處", units) == (scope({SCHOOL}), frozenset())
    assert reach("教務處", units) == scope({SCHOOL})


def test_a_sections_documents_apply_to_the_school_even_when_it_has_a_campus(units):
    # departure from addendum F (「a section with a campus → that campus」): both campus sections publish
    # school-wide procedures (all 43 燕巢教務組 notices of the export: 轉系, 輔系, 離校手續 ...); the section's
    # campus stays a fact of the unit, used when a query names the section
    assert doc_scope("公告本校各學系轉系標準", "", "燕巢教務組", units) == (scope({SCHOOL}), frozenset())
    assert reach("燕巢教務組", units) == reach("招生企劃組", units) == scope({SCHOOL})
    assert doc_scope("燕巢校區學生證補發", "", "燕巢教務組", units)[0] == scope({SCHOOL}, {"燕巢"})  # named: narrows


def test_the_publisher_and_its_parents_never_narrow(units):
    letterhead = f"{SCHOOL}教務處燕巢教務組 公告"
    assert doc_scope("學生證補發", letterhead, "燕巢教務組", units) == (scope({SCHOOL}), frozenset())


def test_offices_and_sections_named_are_mentions(units):
    # a contact, a forwarder or a co-organiser, not the audience (the export's fee page names 出納組 and 進修學院)
    applies, mentions = doc_scope("收費標準", "進修學院 學分學程 (轉知-招生組訊息)", "和平教務組", units)
    assert (applies, mentions) == (scope({SCHOOL}), {"進修學院", "招生企劃組"})
    applies, mentions = doc_scope("軟體系碩士班考試", "請洽和平教務組", "招生企劃組", units)
    assert (applies, mentions) == (scope({SOFT}, levels={"碩士"}), {"和平教務組"})


def test_a_title_naming_a_master_programme_narrows_the_level(units):
    assert doc_scope("碩士班招生考試簡章", "", "招生企劃組", units)[0] == scope({SCHOOL}, levels={"碩士"})
    title = f"{SCHOOL}{SOFT}碩士班考試 試場分配"
    assert doc_scope(title, "", "招生企劃組", units)[0] == scope({SOFT}, levels={"碩士"})


def test_the_title_and_header_narrow_together(units):
    applies, mentions = doc_scope("轉系申請", "燕巢校區 111學年度起入學 軟體系", "和平教務組", units)
    assert applies == scope({SOFT}, {"燕巢"}, cohort=111)  # the publisher, a 和平 section, gives no campus
    assert mentions == frozenset()


def test_a_list_naming_three_departments_mentions_them(units):
    header = f"錄取名單 {SOFT} {CHEM} {CHINESE}"
    assert doc_scope("碩士班考試錄取名單", header, "招生企劃組", units) == (
        scope({SCHOOL}, levels={"碩士"}), frozenset({SOFT, CHEM, CHINESE}))
    applies, mentions = doc_scope("理學院系所名單", f"{CHEM} 數學系 示範學系", "招生企劃組", units)
    assert (applies.units, mentions) == ({"理學院"}, {CHEM, "數學系", "示範學系"})  # the college stays
    applies, mentions = doc_scope("交換生一覽", "文學院 理學院 科技學院 化學系", "招生企劃組", units)
    assert (applies.units, mentions) == ({CHEM}, {"文學院", "理學院", "科技學院"})  # three colleges are a list too
    assert doc_scope("轉系名額", f"{SOFT} {CHEM}", "招生企劃組", units)[0].units == {SOFT, CHEM}  # two: applies_to


def test_a_title_or_header_saying_each_department_is_a_list(units):
    # a 150-character header shows a list's first rows only: the export's fee table names 國文學系 and 英語學系 there,
    # its 前三名 list 國文學系 alone, and both apply to every department
    header = f"大學部各學系學生收費一覽表 文學院 {CHINESE} 英語學系"
    applies, mentions = doc_scope("大學部收費.pdf", header, "和平教務組", units)
    assert (applies, mentions) == (scope({SCHOOL}, levels={"學士"}), {"文學院", CHINESE, "英語學系"})
    assert doc_scope("各班前三名學生名單", CHINESE, "和平教務組", units) == (scope({SCHOOL}), frozenset({CHINESE}))
    assert doc_scope("各系所甄試名額", "", "招生企劃組", units)[0] == scope({SCHOOL})


def test_the_body_tells_a_cut_off_list(units):
    # the export's graduate fee table: its first 150 characters end after the first row (院別 系別 國文學系)
    header, body = f"研究所收費標準 院別 系別 {CHINESE}", f"{CHINESE} 2萬 英語學系 2萬 {CHEM} 2.1萬 {SOFT} 2.2萬"
    assert doc_scope("研究所收費.pdf", header, "和平教務組", units)[0].units == {CHINESE}  # without the body
    assert doc_scope("研究所收費.pdf", header, "和平教務組", units, body=body) == (
        scope({SCHOOL}, levels={"碩士"}), frozenset({CHINESE}))
    title = "軟體系碩士班招生簡章"  # a department in the title stays, whatever the body lists
    assert doc_scope(title, "", "招生企劃組", units, body=body)[0] == scope({SOFT}, levels={"碩士"})
    assert doc_scope("考試試題", CHINESE, "招生企劃組", units, body=f"{CHINESE} {CHEM}")[0].units == {CHINESE}


def with_ruan_gong(tmp: Path) -> dict[str, Unit]:
    """The made-up units plus 物理學系, with the shipped short name 軟工 (addendum H5)."""
    config = CONFIG.replace("[軟體系, 軟工系]", "[軟體系, 軟工系, 軟工]")
    return load_units(*write(tmp, sites=[*SITES, site("物理學系", "系所", p="理學院")], config=config))


EXAM_HEADER = "國立高雄師範大學 100 學年度學士班轉學生招生考試試題\n系所別：數學、物理、光通、電子、軟工等系二年級\n科目：微積分"


def test_an_enumeration_of_short_names_is_a_list(tmp_path):
    # phase-2 review of H5: 招生企劃組's past transfer exams write 「系所別：數學、物理、光通、電子、軟工等系二年級」,
    # five departments by bare stems, of which only 軟工 is a name the tree has. It is a list all the same, so no
    # department narrows applies_to; a stem the tree knows with the shared ending (數學 + 系, 物理 + 學系) or alone
    # (軟工) is a mention, as when the header writes the full names
    u = with_ruan_gong(tmp_path)
    applies, mentions = doc_scope("100b.pdf", EXAM_HEADER, "招生企劃組", u, body=EXAM_HEADER)
    assert (applies, mentions) == (scope({SCHOOL}, levels={"學士"}), {"數學系", "物理學系", SOFT})
    full = EXAM_HEADER.replace("數學、物理、光通、電子、軟工等系", f"數學系、物理學系、光通系、電子系、{SOFT}")
    assert doc_scope("100b.pdf", full, "招生企劃組", u) == (applies, mentions)
    for query in ("數學系轉學考微積分考古題", "物理學系轉學考題", "軟工轉學考微積分"):
        assert relation(applies, detect(query, u), u) == 0  # neutral, not -1 for the other four departments
    # however few of its items the tree knows
    assert doc_scope("試題", "系所別：光通、電子、軟工等系二年級", "招生企劃組", u) == (scope({SCHOOL}), {SOFT})
    # two items are no list: 軟工 narrows, as two named departments do
    assert doc_scope("試題", "系所別：電子、軟工等系二年級", "招生企劃組", u)[0].units == {SOFT}


def test_an_enumeration_beside_other_names(tmp_path):
    u = with_ruan_gong(tmp_path)
    # words run into the first item (歡迎軟工): the name that ends where the item ends is the item
    assert doc_scope("轉學考", "歡迎軟工、數學、物理等系同學報名", "招生企劃組", u) == (
        scope({SCHOOL}), {SOFT, "數學系", "物理學系"})
    # a department the title names outside a list is the document's subject and stays; the list's others are mentions
    assert doc_scope("軟體系碩士班招生簡章", "報考資格：數學、物理、軟工等系畢業", "招生企劃組", u) == (
        scope({SOFT}, levels={"碩士"}), {"數學系", "物理學系"})
    # a list in the title is a list
    assert doc_scope("數學、物理、軟工等系轉學考試題", "", "招生企劃組", u) == (
        scope({SCHOOL}), {"數學系", "物理學系", SOFT})
    # the header cuts the list off before 等系, the body shows it whole: the header's 軟工 is a list item too
    header, body = "系所別：數學、物理、光通、電子、軟工", "系所別：數學、物理、光通、電子、軟工等系二年級 科目：微積分"
    assert doc_scope("試題", header, "招生企劃組", u)[0].units == {SOFT}  # without the body
    assert doc_scope("試題", header, "招生企劃組", u, body=body) == (scope({SCHOOL}), {SOFT})
    # 系列 and 系統 end no list
    assert doc_scope("課程", "數學、物理、軟工等系列課程", "招生企劃組", u)[0].units == {SOFT}
    assert doc_scope("系統", "報名、繳費、軟工等系統操作", "招生企劃組", u)[0].units == {SOFT}


def test_publishers_found_by_name_alias_or_not_at_all(units):
    assert resolve("和平校區教務組", units) == "和平教務組"
    assert resolve("軟体系", units) == SOFT
    assert resolve("沒有這個單位", units) is None and resolve(None, units) is None
    assert reach(None, units) == reach("沒有這個單位", units) == scope({SCHOOL})
    assert reach("軟體系", units) == scope({SOFT})  # a department reaches itself
    assert doc_scope("系務會議紀錄", "", SOFT, units)[0] == scope({SOFT})


# --- relation ---------------------------------------------------------------------------------------------------------

def test_relation_covers_what_the_query_names(units):
    q = detect("軟體系 碩士班", units)
    assert relation(scope({SOFT}, levels={"碩士"}), q, units) == 1  # the unit's +1; the shared level adds nothing
    assert relation(scope({"科技學院"}), q, units) == 1  # a college covers its department
    assert relation(scope({SCHOOL}, {"燕巢"}), q, units) == 1  # the department's campus
    assert relation(scope({SOFT, CHEM}), q, units) == 1  # naming it wins over naming another one too


def test_relation_neutral(units):
    q = detect("軟體系 碩士班", units)
    assert relation(scope({SCHOOL}), q, units) == 0  # school-wide
    # changed on purpose (addendum J, 11-03): the query's level alone, without its unit, was +1 and is 0 now
    assert relation(scope({SCHOOL}, levels={"碩士"}), q, units) == 0
    assert relation(scope(), q, units) == 0
    assert relation(scope({"理學院"}), q, units) == 0  # another kind, not one of its parents
    assert relation(scope({"招生企劃組"}), q, units) == 0
    assert relation(scope({"沒有這個單位"}), q, units) == 0
    assert relation(scope({SOFT}, {"和平"}, {"碩士"}), detect("期末考週", units), units) == 0  # the query names nothing
    assert relation(scope({"示範碩士班"}), detect(CHEM, units), units) == 0  # one of its own programmes
    assert relation(scope({SCHOOL}, {"和平", "燕巢"}), q, units) == 0  # every campus is the whole school
    assert relation(scope({SCHOOL}, {"和平"}), detect("和平與燕巢校區", units), units) == 0


def test_relation_names_something_else(units):
    q = detect("軟體系 碩士班", units)
    assert relation(scope({CHEM}, levels={"碩士"}), q, units) == -1  # another department
    assert relation(scope({CHINESE}), q, units) == -1
    assert relation(scope({SCHOOL}, {"和平"}), q, units) == -1  # another campus
    assert relation(scope({SOFT}, levels={"學士"}), q, units) == -1  # another level
    assert relation(scope({"理學院"}), detect("科技學院", units), units) == -1  # another college


def test_a_level_alone_never_raises(units):
    # main session 11-03 (addendum J; addendum F gave +1 for a shared level): a level can only lower a score. With
    # the +1, T10 (keyword-only, partial corpus) saw a query saying only 大學部 or 學士班 lift every document that
    # writes 學士 above the school-wide rules that answer it (Q08's gold from rank 7 to 18, Q18's from 3 to 10)
    for text in ("大學部每學期最多、最少修幾學分？", "學士班最低畢業學分是多少？"):  # Q08, Q18
        q = detect(text, units)
        assert (q.units, q.campus, q.levels) == (frozenset(), frozenset(), {"學士"}), text
        assert relation(scope({SCHOOL}, levels={"學士"}), q, units) == 0, text  # a 學士 document
        assert relation(scope({SOFT}, levels={"學士"}), q, units) == 0, text  # a department's 學士 document
        assert relation(scope({SCHOOL}, levels={"碩士"}), q, units) == -1, text  # a 碩士 document
        assert relation(scope({SCHOOL}), q, units) == 0, text  # a school-wide document, no level
    # a unit named beside a level still gives the unit's +1
    q = detect("軟體系碩士班畢業門檻", units)
    assert (q.units, q.levels) == ({SOFT}, {"碩士"})
    assert relation(scope({SOFT}, levels={"碩士"}), q, units) == 1


def test_relation_with_a_campus_or_a_section_in_the_query(units):
    q = detect("燕巢校區停水", units)
    assert relation(scope({SCHOOL}, {"燕巢"}), q, units) == 1
    assert relation(scope({SCHOOL}, {"和平"}), q, units) == -1
    assert relation(scope({CHINESE}), q, units) == 0  # a unit is not a campus word
    q = detect("和平教務組 電話", units)
    assert relation(scope({SCHOOL}, {"和平"}), q, units) == 1
    assert relation(scope({"燕巢教務組"}, {"燕巢"}), q, units) == -1
    assert relation(scope({"教務處"}), q, units) == 1  # its office


# --- the shipped config -----------------------------------------------------------------------------------------------

def test_the_shipped_config_is_valid():
    entries = read_config(REPO / "config" / "units.yaml")
    assert entries
    for name, entry in entries.items():
        assert entry["evidence"].strip(), name
        assert entry.get("campus") in (None, *CAMPUSES), name
