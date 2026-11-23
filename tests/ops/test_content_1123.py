"""內容's 全部 view as the 11-23 design copy draws it (research/design_2026-11-23/sites.html; how each part was made:
scratchpad design scripts cards_all.py and content_night.py): the 抓取覆蓋 card from the crawler's coverage.jsonl (its
line over the nights, gaps where a night has no line, 要處理 and 學校那邊 in names.py's words, rows that open the crawl
drawer), the page's places for the night view, 品質提醒 as filters, the address (?ck, ?kind, ?embed=1), the words the
owner changed, and the floating words read for the eye (RICHTIP). Offline: made-up readings; the page's own script is
run in node where it decides something."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest

import nknu.ops
from nknu.content.schema import create_schema
from nknu.ops import content_band, design, overview, units_page

REPO = Path(__file__).resolve().parents[2]
PAGE = overview.PAGE.read_text(encoding="utf-8")
LAYER = REPO / "src/nknu/ops/theme/design"

# seven nights as coverage.jsonl gives them through nknu.ops.sources, summed over the library's sites: the design copy's
# numbers (its 11/16 … 11/22 from the nightly backups), so the card must say what the copy says
NIGHTS = [
    {"night": "2026-11-16", "got": 98874, "meant": 104242,
     "buckets": {"next_round": 4366, "review": 551, "failed": 1485, "gone": 3224, "got": 98874, "rule": 11303,
                 "login": 107, "queued": 1},
     "review": {"api_error": 74, "thin": 310, "too_large": 24, "decode": 116, "held": 22, "parked": 5}},
    {"night": "2026-11-17", "got": 112637, "meant": 125891,
     "buckets": {"got": 112637, "review": 7116, "failed": 1737, "gone": 3476, "rule": 16219, "next_round": 2632,
                 "login": 109, "queued": 816}, "review": {}},
    {"night": "2026-11-18", "got": 116660, "meant": 128192,
     "buckets": {"got": 116660, "review": 6012, "failed": 1785, "gone": 3510, "rule": 23266, "next_round": 2409,
                 "login": 109, "queued": 116}, "review": {}},
    {"night": "2026-11-19", "got": 117359, "meant": 123816,
     "buckets": {"got": 117359, "review": 979, "failed": 1785, "gone": 3513, "rule": 29315, "next_round": 2432,
                 "login": 109, "queued": 71}, "review": {}},
    {"night": "2026-11-20", "got": 117642, "meant": 124050,
     "buckets": {"got": 117642, "review": 962, "failed": 1785, "gone": 3514, "rule": 29320, "next_round": 2432,
                 "login": 109, "queued": 38}, "review": {}},
    {"night": "2026-11-21", "got": 117997, "meant": 124403,
     "buckets": {"got": 117997, "review": 974, "failed": 1785, "gone": 3516, "rule": 29345, "next_round": 2439,
                 "login": 109, "queued": 22}, "review": {}},
    {"night": "2026-11-22", "got": 118164, "meant": 124585,
     "buckets": {"got": 118164, "review": 976, "failed": 1785, "gone": 3529, "rule": 29346, "next_round": 2444,
                 "login": 110, "queued": 21},
     # sources gives the causes in names.py's words; a raw result code is put in words too
     "review": {"待確認：雲端檔案無法讀取": 191, "內容過少": 498, "too_large": 52, "decode": 116, "held": 100,
                "parked": 18, "looks_gone": 1}},
]
TONIGHT = {"default": "2026-11-22"}  # the panel's night key: the evening before the run day


def _rows(card: str, group: str) -> list[tuple[str, str, str]]:
    part = card.split(f"<div class='cs-grp {group}'>", 1)[1].split("</ul>", 1)[0]
    return re.findall(r"<li class='cs-go-li' data-(?:label|bucket)='([^']*)' role='button' tabindex='0'>"
                      r"<span>([^<]*)</span><i><b style='width:[\d.]+%'></b></i><b>([\d,]+)</b></li>", part)


# ---------------------------------------------------------------- 抓取覆蓋, from coverage.jsonl

def test_the_card_says_what_the_design_copy_says_from_the_last_line():
    card = content_band.coverage_card(NIGHTS, {}, "2026-11-22")
    assert card.startswith("<section class='pn-card cs' id='csCard'><div class='pn-chd'><h2>抓取覆蓋</h2>")
    # the headline is the last line, so it equals the line's last point (one source for the card and its line)
    assert "<b class='cs-pct'>94.8<small>%</small></b><span class='cs-cn'>118,164 / 124,585</span>" in card
    assert "data-tip-h='這一輪該抓的抓到' data-tip='118,164 / 124,585 個網址；點開看全部'" in card
    assert "<div class='cs-cov' role='button' tabindex='0'" in card and "cs-old" not in card
    assert "要處理 <b>2,782</b></h5>" in card and "學校那邊 <b>3,639</b></h5>" in card
    # names.py's words, 待確認 split by cause, largest first; the drawer's word on the row, the short one shown
    assert _rows(card, "cs-ours") == [
        ("連線失敗", "連線失敗", "1,785"), ("待確認：內容過少", "內容過少", "498"),
        ("待確認：雲端檔案無法讀取", "雲端檔案無法讀取", "191"), ("待確認：編碼異常", "編碼異常", "116"),
        ("待確認：超過抓取上限", "超過抓取上限", "100"), ("待確認：檔案過大", "檔案過大", "52"),
        ("排隊中／待重試", "排隊中／待重試", "21"), ("待確認：目錄連續遭拒", "目錄連續遭拒", "18"),
        ("待確認：疑似不存在", "疑似不存在", "1")]
    assert _rows(card, "cs-sch") == [("校方已移除", "已移除", "3,529"), ("需要登入", "需要登入", "110")]
    assert "<li class='cs-go-li' data-label='連線失敗' role='button' tabindex='0'><span>連線失敗</span>" \
           "<i><b style='width:100.0%'></b></i><b>1,785</b></li>" in card
    # the old words and the old card's parts are gone
    for old in ("連接失敗", "學校那邊已經不存在", "爬蟲狀態", "網頁核查", "沒抓到的原因", "pn-sidepill", "pn-rtag"):
        assert old not in card, old


def test_the_head_opens_the_night_view_and_says_what_the_card_counts():
    card = content_band.coverage_card(NIGHTS, {}, "2026-11-23")
    head = card.split("<div class='cs-body'>", 1)[0]
    assert ("<button type='button' class='hp cs-hp' data-tip='這一輪該抓的網址裡沒抓到的，大多是學校那邊刪了頁、連不上或不公開，"
            "不是昨晚才壞的；「待確認」的要人看一眼。'>?</button>") in head
    # 定時更新: the clock and the word; a link to the night (the night layer takes the click when it is there)
    assert ("<a class='cs-go' href='/sites?date=2026-11-23' aria-label='定時更新' data-tip-h='定時更新' "
            "data-tip='每天 01:00 自動更新資料庫：抓取 → 導出 → 轉文字 → 建庫 → 換庫，每一次的經過'>") in head
    assert "<path d='M12 7v5l4 2'/></svg><span>定時更新</span></a>" in head
    assert "data-tip='待確認的、連線失敗的、還在排的：要我們看一眼或重試'>要處理" in card
    assert "data-tip='學校刪了頁、要登入：我們修不了，只記下來'>學校那邊" in card


def test_the_nights_line_has_a_fixed_scale_every_day_and_a_column_a_night():
    svg = content_band.spark(NIGHTS)
    assert svg.startswith("<svg class='cs-spark' viewBox='0 0 216 64' width='216' height='64' role='img' "
                          "aria-label='近 7 晚的抓到比例，94.9% 到 94.8%'>")
    assert re.findall(r"<text class='gl' [^>]*>([^<]+)</text>", svg) == ["100%", "90", "80"]
    assert re.findall(r"<text class='dx[^']*' [^>]*>([^<]+)</text>", svg) == ["11/16", "17", "18", "19", "20", "21", "22"]
    assert "<text class='dx l' x='182.0' y='61'>22</text>" in svg  # the last night's day in bold
    assert svg.count("<polyline ") == 1 and len(re.search(r"<polyline points='([^']*)'", svg)[1].split()) == 7
    assert "<polyline points='6.0,16.8 35.3,28.1 64.7,24.9 94.0,17.0 123.3,16.8 152.7,16.8 182.0,16.8'/>" in svg
    assert "<circle class='last' cx='182.0' cy='16.8' r='3.4'/>" in svg
    assert svg.count("<g class='h' ") == 7
    assert ("<g class='h' data-tip-h='11/22 抓完 · 94.85%' data-tip='已取得 118,164、該抓 124,585、要處理 2,782、"
            "學校那邊 3,639'><rect x='166.7' y='0' width='30.7' height='64'/>") in svg
    assert "data-tip-h='11/17 抓完 · 89.47%' data-tip='已取得 112,637、該抓 125,891、要處理 9,669、學校那邊 3,585'" in svg


def test_a_night_without_a_line_is_a_gap_and_never_half_a_line():
    some = [n for n in NIGHTS if n["night"] not in ("2026-11-18", "2026-11-20")]
    svg = content_band.spark(some)
    assert re.findall(r"<text class='dx[^']*' [^>]*>([^<]+)</text>", svg) == ["11/16", "17", "18", "19", "20", "21", "22"]
    lines = re.findall(r"<polyline points='([^']*)'", svg)
    assert [len(p.split()) for p in lines] == [2, 2]  # 16–17 and 21–22; 19 stands alone, as a dot
    assert svg.count("<circle class='pt' ") == 1 and svg.count("<path class='a' ") == 2
    gaps = re.findall(r"<g class='h' data-tip-h='([^']*)' data-tip='這一晚沒有記下抓取覆蓋（那一步沒跑）'>", svg)
    assert gaps == ["11/18", "11/20"]
    assert "aria-label='近 5 晚的抓到比例" in svg
    # older lines than a week before the last are left out, and the week's days before 11-16 are gaps (the file
    # began on 11-01: the step was there to run); the scale goes a ten lower for a night under 80%
    low = [{"night": "2026-11-01", "got": 1, "meant": 2, "buckets": {}}, *NIGHTS[:2],
           {"night": "2026-11-18", "got": 75, "meant": 100, "buckets": {"got": 75, "failed": 25}}]
    svg = content_band.spark(low)
    assert re.findall(r"<text class='dx[^']*' [^>]*>([^<]+)</text>", svg) == ["11/12", "13", "14", "15", "16", "17", "18"]
    assert re.findall(r"<g class='h' data-tip-h='([^']*)' data-tip='這一晚沒有記下", svg) == ["11/12", "11/13", "11/14", "11/15"]
    assert re.findall(r"<text class='gl' [^>]*>([^<]+)</text>", svg) == ["100%", "85", "70"]
    assert content_band.spark([]) == ""
    # a week with no line at all draws nothing (the card then reads the live crawl step)
    assert content_band.spark(NIGHTS, "2026-12-30") == "" and content_band.window(NIGHTS, "2026-12-30") == []


def test_the_week_ends_on_the_run_day_so_tonight_and_a_leading_night_are_gaps_too():
    """Critic C03/CR-01: the line's week ends on the run day 定時更新 opens, not on the newest line, so a night whose
    line is not there (the step did not run, or it is before ~03:30 and tonight's is not written yet) is a gap at
    either end; the days before coverage.jsonl began are no gap."""
    svg = content_band.spark(NIGHTS, "2026-11-23")
    assert re.findall(r"<text class='dx[^']*' [^>]*>([^<]+)</text>", svg) == ["11/17", "18", "19", "20", "21", "22", "23"]
    # tonight: a gap that says its line comes once the night has run; the newest point is the marked one, its day bold
    assert svg.endswith("<g class='h' data-tip-h='11/23' data-tip='這一晚還沒有記下抓取覆蓋（整晚跑完才記；跑完了還沒有，"
                        "就是那一步沒跑）'><rect x='166.7' y='0' width='30.7' height='64'/></g></svg>")
    assert "<text class='dx l' x='152.7' y='61'>22</text>" in svg and svg.count("class='dx l'") == 1
    assert "<circle class='last' cx='152.7' cy='16.8' r='3.4'/>" in svg
    assert len(re.search(r"<polyline points='([^']*)'", svg)[1].split()) == 6
    # a missing first night of the week: a gap when the file began before it, left out when the file began after
    later = [n for n in NIGHTS if n["night"] >= "2026-11-18"]
    svg = content_band.spark(later, "2026-11-23", "2026-11-16")
    assert re.findall(r"<text class='dx[^']*' [^>]*>([^<]+)</text>", svg)[:2] == ["11/17", "18"]
    assert "<g class='h' data-tip-h='11/17' data-tip='這一晚沒有記下抓取覆蓋（那一步沒跑）'>" in svg
    svg = content_band.spark(later, "2026-11-23", "2026-11-18")
    assert re.findall(r"<text class='dx[^']*' [^>]*>([^<]+)</text>", svg) == ["11/18", "19", "20", "21", "22", "23"]
    # the run day never hides a newer line: the week ends on the newest line when that is later
    assert [d.isoformat() for d, _n in content_band.window(NIGHTS, "2026-11-20")][-1] == "2026-11-22"


def test_the_figure_says_which_night_it_is_when_tonights_line_is_not_there():
    card = content_band.coverage_card(NIGHTS, {}, "2026-11-23")
    assert ("<span class='cs-cn'>118,164 / 124,585</span><span class='cs-old'>11/22 那一晚的；11/23 這一晚還沒記下</span>"
            "<svg class='cs-spark'") in card
    assert "data-tip='118,164 / 124,585 個網址（11/22 那一晚的；11/23 這一晚還沒記下）；點開看全部'" in card
    assert "href='/sites?date=2026-11-23'" in card  # 定時更新 still opens tonight
    # tonight's line written: the figure is tonight's and says nothing more
    tonight = {"night": "2026-11-23", "got": 118287, "meant": 124698,
               "buckets": {"got": 118287, "failed": 1785, "gone": 3535, "login": 110, "review": 952, "queued": 29}}
    card = content_band.coverage_card([*NIGHTS, tonight], {}, "2026-11-23")
    assert "cs-old" not in card and "<span class='cs-cn'>118,287 / 124,698</span><svg" in card
    css = (LAYER / "content.css").read_text(encoding="utf-8")
    assert ".cs-cov>.cs-old{" in css


def test_every_bucket_has_its_side_so_no_count_goes_missing():
    ours, school = content_band.causes(
        {"got": 9, "review": 5, "failed": 4, "school_side": 3, "pending_check": 2, "new_kind": 1, "rule": 7,
         "next_round": 6, "gone": 1, "login": 0}, {"thin": 2, "decode": 2})  # the causes add up to less than 5
    assert [(r["label"], r["bucket"], r["n"]) for r in ours] == [
        ("待確認", "review", 5), ("連線失敗", "failed", 4), ("待判定", "pending_check", 2), ("new_kind", "new_kind", 1)]
    assert [(r["label"], r["n"]) for r in school] == [("學校那邊的問題（人看過）", 3), ("校方已移除", 1)]
    card = content_band.coverage_card([{"night": "2026-11-22", "got": 9, "meant": 25,
                                        "buckets": {"got": 9, "review": 5, "failed": 4, "school_side": 3,
                                                    "pending_check": 2, "new_kind": 1, "gone": 1}}], {}, "")
    # 待確認 that cannot say why opens the drawer on its bucket; 學校那邊 drops 「校方」 only for the eye
    assert "<li class='cs-go-li' data-bucket='review' role='button' tabindex='0'><span>待確認</span>" in card
    assert "data-label='校方已移除' role='button' tabindex='0'><span>已移除</span>" in card
    assert "要處理 <b>12</b>" in card and "學校那邊 <b>4</b>" in card


def test_more_causes_lengthen_the_two_columns_never_a_third():
    review = {f"待確認：原因{i}": 12 - i for i in range(11)}
    card = content_band.coverage_card([{"night": "2026-11-22", "got": 50, "meant": 50 + sum(review.values()) + 3,
                                        "buckets": {"got": 50, "review": sum(review.values()), "failed": 3},
                                        "review": review}], {}, "2026-11-23")
    assert "<ul style='grid-template-rows:repeat(6,auto)'>" in card and len(_rows(card, "cs-ours")) == 12
    css = (LAYER / "content.css").read_text(encoding="utf-8")
    assert ".cs-ours ul{grid-template-rows:none!important" in css.split("@media (max-width:760px)", 1)[1]


def test_without_coverage_the_live_reading_stands_in_without_a_line():
    snap = {"steps": [{"key": "crawl", "bar": [118287, 124698], "say": "x",
                       "lost": [["連接失敗", 1785], ["待確認：頁面幾乎是空的", 474], ["學校那邊已經不存在", 3535],
                                ["要登入", 110], ["還在排隊 / 等重試", 29]]}]}
    card = content_band.coverage_card([], snap, "2026-11-24")
    assert "<b class='cs-pct'>94.9<small>%</small></b><span class='cs-cn'>118,287 / 124,698</span></div>" in card
    assert "cs-spark" not in card
    assert _rows(card, "cs-ours") == [("連線失敗", "連線失敗", "1,785"), ("待確認：內容過少", "內容過少", "474"),
                                      ("排隊中／待重試", "排隊中／待重試", "29")]
    assert _rows(card, "cs-sch") == [("校方已移除", "已移除", "3,535"), ("需要登入", "需要登入", "110")]
    # the step's own rows with their buckets (pipeline, 11-23) are taken as they are
    snap["steps"][0]["lost_buckets"] = [["failed", "連線失敗", 5], ["login", "需要登入", 2]]
    card = content_band.coverage_card(None, snap, "")
    assert _rows(card, "cs-ours") == [("連線失敗", "連線失敗", "5")] and _rows(card, "cs-sch") == [("需要登入", "需要登入", "2")]
    # nothing to count: the step's own words, and still the way to the night view
    empty = content_band.coverage_card(None, {"steps": [{"key": "crawl", "say": "檢視台沒有掛上爬蟲的帳本"}]}, "2026-11-24")
    assert "<p class='pn-empty'>檢視台沒有掛上爬蟲的帳本</p>" in empty and "href='/sites?date=2026-11-24'" in empty
    assert "還沒有抓取覆蓋的紀錄" in content_band.coverage_card(None, {}, "")


# ---------------------------------------------------------------- the band and how /sites gets it

def test_the_band_is_the_one_card_and_the_page_head_is_gone():
    band = content_band.band({"sites": []}, {}, TONIGHT, None, nights=NIGHTS)
    assert "<section class='ct-band' id='crawl' aria-label='缺什麼'><h1 class='ct-h1'>內容</h1>" \
           "<div class='pn-bento cs-bento'><section class='pn-card cs' id='csCard'>" in band
    assert "href='/sites?date=2026-11-23'" in band  # the night after the evening 11-22: the run of 11-23
    markup = band.split("</style>", 1)[1]  # after 總覽's card shell styles, which the night view's cards use too
    for gone in ("ct-head", "按網站", "按單位", "/units", "沒有文件的", "爬蟲狀態", "網頁核查 →", "pn-span5", "pn-span7"):
        assert gone not in markup, gone
    assert content_band.run_day("2026-11-30") == "2026-12-01" and content_band.run_day("") == ""
    # an older caller passing the unit tree still gets the band
    assert "id='csCard'" in content_band.band({}, {}, {}, object())


def test_the_band_reads_coverage_for_the_librarys_sites_and_never_fails(monkeypatch, tmp_path):
    asked = {}

    def coverage_nights(root, sites=None, limit=7, **kw):
        asked.update(root=root, sites=sites, limit=limit, **kw)
        return NIGHTS

    def fake(**kw):  # nknu.ops.sources as the band uses it (the package attribute, once the module was imported)
        mod = types.SimpleNamespace(library_dir=lambda where: where.db.parent, **kw)
        monkeypatch.setitem(sys.modules, "nknu.ops.sources", mod)
        monkeypatch.setattr(nknu.ops, "sources", mod, raising=False)

    lines = [{"night": "2026-11-16"}, {"night": "2026-11-22"}]
    lib_sites: set[str] = {"c.nknu.edu.tw/aca", "y.edu.tw"}
    fake(coverage_nights=coverage_nights, coverage_lines=lambda root: lines, library_sites=lambda db: set(lib_sites))
    where = types.SimpleNamespace(archive=tmp_path / "archive", db=tmp_path / "lib" / "aca.db",
                                  library=tmp_path / "lib" / "aca.db")
    page = {"sites": [{"k": "c.nknu.edu.tw/aca"}, {"k": "x.edu.tw"}]}
    band = content_band.band_for(where, page, {}, TONIGHT)
    # the library's sites table (not the page's list), its folder so an older night is summed over that night's own
    # library, and the week up to the run day (CR-01)
    assert asked == {"root": tmp_path / "archive", "sites": {"c.nknu.edu.tw/aca", "y.edu.tw"}, "limit": 7,
                     "library": tmp_path / "lib", "until": "2026-11-23"}
    assert "cs-spark" in band and "94.8<small>%" in band
    assert "<g class='h' data-tip-h='11/23' data-tip='這一晚還沒有記下抓取覆蓋" in band  # tonight's line not there yet
    assert "11/22 那一晚的；11/23 這一晚還沒記下" in band
    # a newer line than the run day the panel knows: the week ends on it
    lines.append({"night": "2026-11-24"})
    content_band.band_for(where, page, {}, TONIGHT)
    assert asked["until"] == "2026-11-24"
    # a library that cannot say its sites: the page's list; neither: no coverage read at all (never every site)
    lib_sites.clear()
    content_band.band_for(where, page, {}, TONIGHT)
    assert asked["sites"] == {"c.nknu.edu.tw/aca", "x.edu.tw"}
    asked.clear()
    snap = {"steps": [{"key": "crawl", "bar": [3, 4], "lost": [["連接失敗", 1]]}]}
    band = content_band.band_for(where, {"sites": []}, snap, TONIGHT)
    assert asked == {} and "cs-spark" not in band and "75.0<small>%" in band
    assert content_band.coverage_of(tmp_path, set()) == []

    def broken(*_a, **_k):
        raise OSError("half a line")

    fake(coverage_nights=broken, coverage_lines=broken, library_sites=broken)
    snap = {"steps": [{"key": "crawl", "bar": [1, 2], "lost": [["連接失敗", 1]]}]}
    band = content_band.band_for(where, page, snap, TONIGHT)
    assert "cs-spark" not in band and "50.0<small>%" in band
    assert content_band.coverage_of(None, {"a"}) == []


def test_the_band_from_a_real_coverage_file(tmp_path):
    """The crawler's coverage.jsonl read through nknu.ops.sources as it is (one line a night, per-site buckets and
    review result codes, the school-wide line of other hosts beside them): summed over the library's sites only."""
    sources = pytest.importorskip("nknu.ops.sources")
    from nknu.ops.pipeline import Where

    reports = tmp_path / "archive" / "reports"
    reports.mkdir(parents=True)
    lines = [
        {"night": "2026-11-22", "ledger": "x", "written_at": "2026-11-22T03:30:00+08:00", "round2_open": [],
         "sites": {"a.edu.tw": {"buckets": {"got": 90, "failed": 5, "gone": 3, "rule": 50}, "review": {}},
                   "other.edu.tw": {"buckets": {"got": 1, "failed": 1000}}}},
        {"night": "2026-11-23", "ledger": "x", "written_at": "2026-11-23T03:30:00+08:00", "round2_open": [],
         "sites": {"a.edu.tw": {"buckets": {"got": 92, "review": 4, "login": 2, "next_round": 7},
                                "review": {"thin": 3, "decode": 1}},
                   "other.edu.tw": {"buckets": {"got": 1, "failed": 1000}}},
         "others": {"sites": 3, "buckets": {"failed": 99}}},
    ]
    (reports / sources.COVERAGE).write_text("\n".join(json.dumps(x) for x in lines) + "\n{cut", encoding="utf-8")
    (tmp_path / "lib").mkdir()
    conn = sqlite3.connect(tmp_path / "lib" / "aca.db")
    create_schema(conn)
    conn.execute("INSERT INTO sites (site, name, unit, docs) VALUES ('a.edu.tw', '甲', '甲', 1)")
    conn.commit()
    conn.close()
    where = Where(root=tmp_path, db=tmp_path / "lib" / "aca.db", archive=tmp_path / "archive")
    # the page lists other.edu.tw too (a site found in docs): the library's sites table is the scope
    band = content_band.band_for(where, {"sites": [{"k": "a.edu.tw"}, {"k": "other.edu.tw"}]}, {}, {"default": "2026-11-22"})
    assert "<b class='cs-pct'>93.9<small>%</small></b><span class='cs-cn'>92 / 98</span>" in band
    assert _rows(band, "cs-ours") == [("待確認：內容過少", "內容過少", "3"), ("待確認：編碼異常", "編碼異常", "1")]
    assert _rows(band, "cs-sch") == [("需要登入", "需要登入", "2")]
    assert re.findall(r"<text class='dx[^']*' [^>]*>([^<]+)</text>", band) == ["11/22", "23"]
    assert "data-tip-h='11/22 抓完 · 91.84%' data-tip='已取得 90、該抓 98、要處理 5、學校那邊 3'" in band
    assert "href='/sites?date=2026-11-23'" in band


def test_the_card_has_its_styles_and_clicks_in_the_content_layer():
    css = (LAYER / "content.css").read_text(encoding="utf-8")
    assert ".cs-body{display:grid;grid-template-columns:auto minmax(0,2.5fr) minmax(0,1fr);gap:32px;align-items:start" in css
    assert ".cs-cov{display:flex;flex-direction:column;align-items:center;gap:2px;cursor:pointer" in css
    assert ".cs-pct{font-size:34px" in css and ".cs-grp li>i>b{display:block;height:100%;background:var(--warn-s)" in css
    assert ".cs-sch li>i>b{background:var(--ink3)}" in css and ".hp{flex:none;width:22px" in css
    # the card's ? is a size smaller than the night view's .hp, whose layer is joined after this one (critic C26)
    assert ".cs .pn-chd .cs-hp{width:20px;height:20px;font-size:11px}" in css
    assert overview.LAYERS.index("night") > overview.LAYERS.index("content")
    # a phone gets one column: the narrow rules come last (the copy's last rule kept three columns)
    narrow = css.rsplit("@media (max-width:900px)", 1)[1]
    assert ".cs-body{grid-template-columns:minmax(0,1fr)}" in narrow
    assert "cs-ring" not in css and "cs-none" not in css and "cn-" not in css  # the copy's leftovers stay behind
    theme = set(re.findall(r"(--[a-z0-9-]+):", (REPO / "src/nknu/ops/theme/theme.css").read_text(encoding="utf-8")))
    assert set(re.findall(r"var\((--[a-z0-9-]+)", css)) <= theme


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def _run(script: str) -> object:
    out = subprocess.run([_node(), "-e", script], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def test_the_cards_clicks_open_the_drawer_and_the_night():
    """content.js in node with a stand-in document: a cause's row opens the crawl drawer on its word (or bucket), the
    headline on everything, a night's column of the line nothing, the clock the night view. ?date= is the night
    layer's to read (night.js), so the card's script leaves it alone."""
    js = (LAYER / "content.js").read_text(encoding="utf-8")
    script = """
const L = {}, got = [];
global.window = globalThis;
global.location = { search: '?date=2026-11-23' };
global.document = { readyState: 'loading', addEventListener: (t, f) => { (L[t] = L[t] || []).push(f); } };
window.RAWSHOW = (step, p) => got.push(['raw', step, p]);
window.NM = { set: (...a) => got.push(['nm', a]) };
const el = (sel, data, parent) => ({ dataset: data || {}, sel, closest(s) {
  for (let x = this; x; x = x.parent) if (s.split(',').some(q => q.trim().split(' ').pop() === x.sel || q.trim() === x.sel)) return x;
  return null; }, parent });
const card = el('#csCard'), cov = el('.cs-cov', {}, card), col = el('.h', {}, cov), go = el('.cs-go', {}, card);
const row = el('.cs-go-li', { label: '待確認：內容過少' }, card), whole = el('.cs-go-li', { bucket: 'review' }, card);
const fire = (t, target, key) => { let stop = false; L[t].forEach(f => f({ target, key, preventDefault() { stop = true; } }));
  return stop; };
""" + js + """
fire('click', row); fire('click', whole); fire('click', cov); fire('click', col);
const kept = fire('click', go); fire('keydown', row, 'Enter'); fire('keydown', row, 'a');
(L.DOMContentLoaded || []).forEach(f => f());
console.log(JSON.stringify({ got, kept }));
"""
    out = _run(script)
    assert out["got"] == [["raw", "crawl", {"label": "待確認：內容過少"}], ["raw", "crawl", {"bucket": "review"}],
                          ["raw", "crawl", {}], ["nm", [True]], ["raw", "crawl", {"label": "待確認：內容過少"}]]
    assert out["kept"] is True  # the clock's link is not followed when the night view is there


# ---------------------------------------------------------------- the page: its places, layers and address

def test_the_page_keeps_the_places_web_fills_in_the_designs_order():
    for mark in (overview.NIGHT_BAND, overview.NIGHT_LOG, overview.EMBED_SIDE):
        assert PAGE.count(mark) == 1, mark
    assert '<main class="app"><!--NIGHT-BAND-->\n  <header class="topbar" id="bar">' in PAGE  # above the map
    assert '<!--NIGHT-LOG--><section class="card" aria-labelledby="hList">' in PAGE  # where #nightLog sat
    assert "<div id=\"iSecs\"></div>\n    </div><!--EMBED-SIDE--></aside>" in PAGE
    page = overview.fill(PAGE, band="<B>", night_band="<NB>", night_log="<NL>", embed_side="<ES>")
    assert page.index('<main class="app"><B><NB>') < page.index('id="bar"') < page.index("<NL><section class=\"card\"")
    assert "<ES></aside>" in page and "<!--NIGHT" not in page
    assert overview.fill(PAGE) == PAGE  # nothing drawn: the places stay empty comments


def test_the_page_loads_its_layers_after_its_own_script():
    page = overview.render(PAGE, {"sites": [], "groups": [], "daily": {}, "later": []}).decode("utf-8")
    app = page.index("window.APP = { S, S0, render, byKey, LENS };")
    layers = page.index("<style id='design-content-richtip")
    assert app < layers < page.index("</body></html>")
    assert "window.RICHTIP=" in page and "RAWSHOW('crawl',p)" in page and "window.NM.set(true)" in page
    assert design.layer(*overview.LAYERS).startswith("<style id='design-content-richtip")
    head = page.split("</head>", 1)[0]
    assert overview.EMBED_JS in head and "<style id='embed-css'>" in head  # 總覽's frame is set before it draws
    assert '"page": "/sites"' in page


def test_the_seam_the_night_view_works_through():
    """The hooks content_night.py put into the app, now in the app itself (window.NM optional throughout)."""
    for hook in (
        "if (window.NM && S.lens === 'night') NM.tile(o.s, t.el, st);",
        "if (window.NM && S.lens === 'night') items = NM.items(S0.filter(inGrp));",
        "const gp = window.NM && S.lens === 'night' ? NM.sum(n.value) : pct1(n.value, grand),",
        "tm.empty.textContent = window.NM && S.lens === 'night' ? '這一晚沒有網站有變動' : '目前篩選下沒有文件';",
        "const nightMap = !!(window.NM && S.lens === 'night'), zoomed = S.grp !== null || nightMap,",
        "$('#hMapSub').textContent = S.grp !== null ? GROUPS[S.grp] : '';",
        "$('#tmBack').hidden = S.grp === null;",
        "syncSeg('segLens', S.lens);\n  if (window.NM) NM.after();",
        "function siteTip(s) {\n  if (window.NM && S.lens === 'night') return NM.tip(s);",
        ("function render() { renderBar(); renderMap(); renderInsp(); renderTime(); renderList(); addrUrl(); "
         "if (window.NM) NM.after(); }"),
        "window.APP = { S, S0, render, byKey, LENS };\nif (typeof d3 === 'undefined')",
    ):
        assert hook in PAGE, hook
    # the live page keeps saying when it cannot reach the lab (the copy silenced its own failing poll)
    assert ".catch(() => setLive('連不上實驗室電腦'" in PAGE


def _js(prefix: str) -> str:
    start = PAGE.index(prefix)
    return PAGE[start:PAGE.index(";\n", start) + 1]


def test_the_night_map_draws_its_tiles_whatever_kind_the_filter_keeps():
    """Review finding 3: a night tile is one block of its own size (NM.items: p = [v, 0, 0, 0, 0]); with one kind
    alone on (?kind=2 from 總覽's legend, a kind clicked in 內容) the kinds' filter left it no block, d3 gave it no
    children and renderMap threw. The night's blocks ignore the filter; 內容's own keep it; a tile with no block is
    left out (marked gone), never read."""
    script = ("let nightMap; const S = { kinds: [false, false, true, false, false] };\n" + _js("    const node = o =>")
              + """
const night = { s: { k: 'a' }, p: [3, 0, 0, 0, 0], v: 3 }, site = { s: { k: 'b' }, p: [4, 0, 5, 0, 1], v: 5 };
const out = {};
nightMap = true; out.night = [node(night).children, node(site).children.length];
nightMap = false; out.day = [node(night).children, node(site).children];
console.log(JSON.stringify(out));
""")
    out = _run(script)
    assert out["night"] == [[{"i": 0, "v": 3}], 1]
    assert out["day"] == [[], [{"i": 2, "v": 5}]]  # the kinds' filter, as before, outside the night
    loop = PAGE[PAGE.index("    for (const n of zoomed ? root.children"):PAGE.index("const kids = n.children")]
    assert "if (!n.children) continue;" in loop and loop.index("if (!n.children) continue;") < loop.index("seen.add(")


def test_a_reminder_filters_every_part_and_the_date_chart_keeps_a_series_per_filter():
    script = ("const CK = { none: [], self: [] }; const S = { grp: null, site: null, ck: null };\n"
              + _js("const ckHit =") + _js("const ckKey =") + _js("const inGrp =") + _js("const scopeKey =") + """
const sites = [{ k: 'a', g: 0, ck: { f: ['none'], sp: 0 } }, { k: 'b', g: 1, ck: { f: ['self'], sp: 3 } },
               { k: 'c', g: 1, ck: null }];
const out = {};
for (const ck of [null, 'none', 'self', 'spike']) { S.ck = ck; out[ck] = [sites.filter(inGrp).map(s => s.k), scopeKey()]; }
S.grp = 1; S.ck = 'none'; out.both = sites.filter(inGrp).map(s => s.k);
out.keys = ['none', 'spike', 'empty', 'toString', 3, null].map(ckKey);
console.log(JSON.stringify(out));
""")
    out = _run(script)
    assert out["null"] == [["a", "b", "c"], "all"]
    assert out["none"] == [["a"], "all|ck:none"] and out["self"] == [["b"], "all|ck:self"]
    assert out["spike"] == [["b"], "all|ck:spike"]  # 檔案時間雷同: the sites that have such files
    assert out["both"] == [] and out["keys"] == ["none", "spike", None, None, None, None]


def test_a_reminder_row_of_a_scope_is_a_button_and_one_sites_rows_stay_plain():
    script = ("const S = { ck: 'same_title' }; const esc = s => String(s);\n" + PAGE[PAGE.index("function ckRow("):
              PAGE.index("function checkRows(")] + """
console.log(JSON.stringify([ckRow('標題重複', 't', '43 站', false, 'same_title'), ckRow('無文件', 't', '5 站', true, 'none'),
                            ckRow('標題重複', 't', '16 份', false)]));
""")
    on, off, plain = _run(script)
    assert on.startswith('<div class="ck ck-f on" data-ck="same_title" role="button" tabindex="0" aria-pressed="true" '
                         'data-tip="t"><span>標題重複</span>')
    assert off.startswith('<div class="ck ck-f" data-ck="none" role="button" tabindex="0" aria-pressed="false"')
    assert plain.startswith('<div class="ck" data-tip="t">')
    # the rows answer a click and Enter / Space; the chip of the filter lets it go; the address keeps it
    assert "$('#iSecs').addEventListener('click', ev => { const r = ev.target.closest('.ck-f[data-ck]');" in PAGE
    assert "if (ev.key !== 'Enter' && ev.key !== ' ') return;\n    const r = ev.target.closest('.ck-f[data-ck]');" in PAGE
    assert ("if (S.ck) chips.push('<button class=\"chip\" data-x=\"ck\" data-tip=\"只看品質提醒點到這一項的網站\">' + "
            "esc(S.ck === 'spike' ? CK_SPIKE[0] : (CK[S.ck] || [S.ck])[0]) + '<i>✕</i></button>');") in PAGE
    assert "else if (b.dataset.x === 'ck') S.ck = null; else S.link = false;" in PAGE  # render() fixes the address
    # 檔案時間雷同 is a filter too, its sites counted in its hover
    assert "CK_SPIKE[1] + '（' + sc.filter(s => s.ck && s.ck.sp > 0).length + ' 站）', fmt(sp) + ' 份', false, 'spike')" in PAGE
    # a site's page opens on the reminder, and its name in the list does the same
    assert "go.href = SITE_PAGE + encodeURIComponent(one.k) + (S.ck ? '&ck=' + S.ck : '');" in PAGE
    assert "esc(SITE_PAGE + encodeURIComponent(s.k) + (S.ck ? '&ck=' + S.ck : ''))" in PAGE
    css = PAGE.split("</style>", 1)[0]
    assert ".ck-f.on { background: var(--sunk); box-shadow: inset 2px 0 0 var(--ink2); }" in css


def test_no_document_is_read_off_the_library_when_the_checks_name_no_site():
    """The card of sites with no document went; 無文件 must still find them after a swap, before the new library's
    checks have run (site_checks.json of another build: every ck null)."""
    start = PAGE.index("  if (!s.total && !(s.ck && s.ck.f.includes('none')))")
    stmt = PAGE[start:PAGE.index("\n});", start)]
    script = ("const sites = [{ total: 0, ck: null }, { total: 3, ck: null }, { total: 0, ck: { f: ['self'], nw: ['self'], sp: 2 } },"
              " { total: 0, ck: { f: ['none'], nw: [], sp: 0 } }];\nsites.forEach(s => {\n" + stmt + "\n});\n"
              "console.log(JSON.stringify(sites.map(s => s.ck && [s.ck.f, s.ck.nw, s.ck.sp])));")
    assert _run(script) == [[["none"], [], 0], None, [["none", "self"], ["self"], 2], [["none"], [], 0]]


def test_the_address_follows_the_filters_so_a_cleared_one_never_comes_back():
    """Critic C19: ?kind=N (and ?ck=) are written from the page's state at every drawing, gone once cleared, so the
    page's own reload after new data (saved view, then the address) never narrows to a kind the user let go; the night
    view's ?date / &step stay; 總覽's frame leaves its address alone."""
    fn = PAGE[PAGE.index("function addrUrl() {"):PAGE.index("function setCk(")]
    script = """
const out = [];
let EMBED = false;
global.location = { href: 'http://x/sites?ck=same_title&kind=2&date=2026-11-23', search: '?ck=same_title&kind=2&date=2026-11-23' };
global.history = { state: null, replaceState(st, t, u) { out.push(u); const q = u.indexOf('?');
  location.search = q < 0 ? '' : u.slice(q).split('#')[0]; location.href = 'http://x' + u; } };
const S = { ck: 'same_title', kinds: [false, false, true, false, false] };
""" + fn + """
addrUrl();                                   // nothing changed: the address is left as it is
S.kinds = [true, true, true, true, true]; addrUrl();      // the kinds' chip cleared
S.ck = null; addrUrl();                      // the reminder's chip cleared
S.kinds = [false, true, false, false, false]; addrUrl();  // one kind picked on the legend
S.kinds = [false, true, false, true, false]; addrUrl();   // a second one: no single kind to say
EMBED = true; S.ck = 'none'; addrUrl();       // the front page's frame
console.log(JSON.stringify(out));
"""
    assert _run(script) == ["/sites?ck=same_title&date=2026-11-23", "/sites?date=2026-11-23",
                            "/sites?date=2026-11-23&kind=1", "/sites?date=2026-11-23"]
    assert "ckUrl" not in PAGE


def test_the_address_brings_a_filter_a_kind_and_the_frame_of_the_front_page():
    init = PAGE[PAGE.index("function init() {"):]
    assert "if (q.has('ck')) { S.ck = ckKey(q.get('ck')); ckOpen(); }" in init
    assert "if (kq !== null && /^\\d$/.test(kq) && +kq < S.kinds.length) S.kinds = S.kinds.map((_, j) => j === +kq);" in init
    assert "const view = D.live && !EMBED ? takeView() : null;" in init and "if (!EMBED) {" in init
    assert "S.ck = ckKey(v.ck);" in PAGE  # the page's own reload keeps the filter
    # 總覽's frame: its title and donut open 內容, a kind of its legend opens 內容 with only that kind
    assert "[$('#iT'), $('#dn')].forEach(x => { x.classList.add('ov-go'); x.dataset.tip = '到內容看全部網站';" in init
    assert "to(page + '?kind=' + li.dataset.k)" in init and "const EMBED = document.documentElement.classList" in PAGE
    assert "esc(KTIP[i] + (EMBED ? '；點了到內容只看這一類' : ''))" in PAGE
    css = overview.EMBED_CSS
    assert "html.embed #segRange,html.embed #segView,html.embed #segSplit{display:none!important}" in css
    assert "html.embed .nk-bar,html.embed .ct-band,html.embed #bar,html.embed section[aria-labelledby=hMap]" in css
    assert "document.documentElement.classList.add('embed');const b=document.createElement('base');b.target='_top'" \
           in overview.EMBED_JS


def test_the_side_cards_head_and_the_pages_words_are_the_designs():
    assert ("$('#iT').innerHTML = one ? esc(one.n) : esc(S.grp !== null ? GROUPS[S.grp] : '全部網站') + "
            "' <small class=\"ih-n\">' + sc.length + '</small>';") in PAGE
    assert "    : '';\n  const parts = [0, 0, 0, 0, 0];" in PAGE and "#iS:empty { display: none; }" in PAGE
    for word in ("未取得的比例", "'未取得'", "'更新時間'", "'無文字'", "'OCR 認字'", ">品質提醒</span>",
                 ">未取得</button><button data-v=\"old\">更新時間</button>", ">已抓取<b id=\"nGot\">", ">待抓取<b id=\"nLater\">",
                 "miss.push('無日期 ' + amount(undated));", "$('#tNote').textContent = miss.join(' · ');"):
        assert word in PAGE, word
    code = PAGE.split("<script>", 1)[1]
    for old in ("未抓到", "最近更新", "'沒讀到字'", "'掃描認字'", "體檢提醒", "'沒有日期'", "'另有 '", "#foot"):
        assert old not in PAGE.replace(code, "") and old not in code, old
    assert 'id="foot"' not in PAGE  # the data stamp's sentence: the top bar's 庫建於 says it


def test_the_floating_words_read_as_a_head_rows_and_short_lines():
    js = (LAYER / "richtip.js").read_text(encoding="utf-8")
    assert "window.RICHTIP ? RICHTIP(el) : '<p>' + esc(el.dataset.tip) + '</p>'" in PAGE
    script = "global.window = globalThis;\n" + js + """
const el = (h, t) => ({ getAttribute: k => k === 'data-tip-h' ? h : k === 'data-tip' ? t : null });
console.log(JSON.stringify([
  RICHTIP(el('11/22 抓完 · 94.85%', '已取得 118,164、該抓 124,585、要處理 2,782、學校那邊 3,639')),
  RICHTIP(el(null, '新進：12\\n離開：3\\n　校方已刪除（學校刪了）：2\\n' + '字'.repeat(95))),
  RICHTIP(el('<b>', 'a & b')),
]));
"""
    rows, lines, escaped = _run(script)
    assert rows == ("<b class='rt-h'>11/22 抓完 · 94.85%</b><div class='rt-r'><span>已取得</span><b>118,164</b></div>"
                    "<div class='rt-r'><span>該抓</span><b>124,585</b></div><div class='rt-r'><span>要處理</span><b>2,782</b></div>"
                    "<div class='rt-r'><span>學校那邊</span><b>3,639</b></div>")
    assert lines.startswith("<div class='rt-r'><span>新進</span><b>12</b></div><div class='rt-r'><span>離開</span><b>3</b></div>"
                            "<div class='rt-r rt-sub'><span>校方已刪除</span><b>2</b></div><p class='rt-p'>")
    assert lines.endswith("字" * 90 + "…</p>")
    assert escaped == "<b class='rt-h'>&lt;b&gt;</b><p class='rt-p'>a &amp; b</p>"
    css = (LAYER / "richtip.css").read_text(encoding="utf-8")
    assert ".rt-h{display:block;font-size:12px;font-weight:650" in css and ".rt-r.rt-sub{padding-left:12px}" in css


# ---------------------------------------------------------------- /units

def test_the_units_address_is_a_route_for_web_to_wire():
    """單位 is gone (owner 11-23: 「把单位页面拿掉」): web.py routes /units to units_page.moved (test_units_page_1122)."""
    assert callable(units_page.moved)
    assert content_band.ROUTES_TO_WIRE == [{"path": "/units", "method": "GET", "handler": "nknu.ops.units_page.moved",
                                            "args": "request",
                                            "returns": "307 to /sites (Cache-Control: no-store), replacing web.py's "
                                                       "units_view"}]
