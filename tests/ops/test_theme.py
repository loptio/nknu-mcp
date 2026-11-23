"""The panels' one look (nknu/ops/theme, redesign 2026-11-17: research/dashboard_redesign_2026-11-17/diagnosis.md).

Before it the colours were written three times (檢視台's look.py, 資料庫's tc.css, 資料總覽's page.html) and had drifted
apart, the button colour was 公告's blue, and 15 font sizes were in use. These tests keep it one: the colours only in
theme.css, no blue for acting, the type scale, one top bar on every panel."""

from __future__ import annotations

import re
from pathlib import Path

from nknu.ops import check_page, check_sites, doc_page, home_page, look, pipeline_page, site_page, tonight_page
from nknu.ops.theme import bar

REPO = Path(__file__).resolve().parents[2]
THEME = bar.theme_css()
SHEETS = {
    "look.py": look.CSS,
    "pipeline_page.py": pipeline_page.CSS,
    "site_page.py": site_page.CSS,  # the four entries' new pages (structure design 2026-11-17 section 二)
    "tonight_page.py": tonight_page.CSS,  # the pieces the home and a site's page share
    "home_page.py": home_page.CSS,  # 每晚運行, rebuilt 2026-11-18
    # the charts' card and script, whole: its drawings read every colour from theme.css when they draw
    "charts.py": (REPO / "src/nknu/ops/charts.py").read_text(encoding="utf-8"),
    "doc_page.py": doc_page.CSS,
    "check_page.py": (REPO / "src/nknu/ops/check_page.py").read_text(encoding="utf-8"),
    "check_sites.py": (REPO / "src/nknu/ops/check_sites.py").read_text(encoding="utf-8"),
    "tc.css": (REPO / "deploy/lab-inspect/datasette/static/tc.css").read_text(encoding="utf-8"),
    "overview_page.html": (REPO / "src/nknu/ops/overview_page.html").read_text(encoding="utf-8"),
}
SIZES = {"11px", "12px", "13px", "14px", "16px", "20px", "26px"}
assert check_page and check_sites  # imported so a broken page module fails here too


def _blocks(css: str) -> list[str]:
    return re.findall(r":root(?:\[data-theme=\"dark\"\]|:not\(\[data-theme=\"light\"\]\))? \{([^}]*)\}", css)


def test_both_dark_blocks_are_the_same_and_every_colour_has_a_dark_step():
    light, dark_media, dark_forced = _blocks(THEME)
    assert dark_media.split() == dark_forced.split()
    names = set(re.findall(r"(--[a-z0-9-]+):", light)) - {"--font", "--mono", "--amt", "--page", "--gutter", "--nk-bar-h"}  # not colours
    assert names - set(re.findall(r"(--[a-z0-9-]+):", dark_media)) <= {"--on-w", "--on-k", "--on-good", "--on-good2",
                                                                         "--on-warn", "--on-bad"}


def test_no_panel_writes_its_own_colours_or_the_old_blue_accent():
    for name, css in SHEETS.items():
        assert "var(--acc" not in css, name
        own = set(re.findall(r"(--[a-z0-9-]+):\s*#", css))
        assert not own, (name, own)  # a colour is defined in theme.css only


def test_every_colour_a_panel_uses_is_defined():
    defined = set(re.findall(r"(--[a-z0-9-]+):", THEME)) | {"--w"}  # --w: a bar's width, set in the row's style
    for name, css in SHEETS.items():
        used = set(re.findall(r"var\((--[a-z0-9-]+)", css)) - {"--k", "--q"}  # 'var(--k' + i + ')': a script's --k0…
        local = set(re.findall(r"(--[a-z0-9-]+):", css)) | set(re.findall(r"['\"](--[a-z0-9-]+)['\"]", css))  # set by a script
        assert used <= defined | local, (name, used - defined - local)


def test_the_type_scale_has_no_half_sizes():
    for name, css in {**SHEETS, "theme.css": THEME}.items():
        sizes = set(re.findall(r"font-size:\s*([0-9.]+px)", css)) | set(re.findall(r"font:\s*(?:[0-9]{3}\s+)?([0-9.]+px)", css))
        assert sizes <= SIZES, (name, sizes - SIZES)


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_faint_grey_reads_on_every_paper_in_both_themes():
    for block in _blocks(THEME)[:2]:
        v = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-f]{6})", block))
        for paper in ("--bg", "--surface", "--sunk"):
            assert _contrast(v["--ink3"], v[paper]) >= 4.5, (paper, v["--ink3"])


def _entries(html: str) -> list[str]:
    """The entries of a bar as they read, in order (the tools after them, in the same row)."""
    nav = html.split("<nav class='nk-tabs'", 1)[1].split("<span class='nk-sep'", 1)[0]
    return re.findall(r">([^<>]+)</a>", nav)


def _tools(html: str) -> list[str]:
    """The tools in the bar's row (the copy 更多 holds for a narrow window aside)."""
    row = html.split("<span class='nk-sep'", 1)[1].split("<details", 1)[0]
    return re.findall(r">([^<>]+)(?:<svg[^>]*>.*?</svg>)?</a>", row)


def test_the_bar_is_one_row_of_entries_and_tools_and_links_its_own_host_by_path():
    # structure design 2026-11-17 section 二: the entries; 2026-11-22 (owner: the pages top left and the tools top right
    # were two places to look): 總覽 first, the tools in the same row after a thin line, folded into 更多 when narrow;
    # 2026-11-22 evening: three entries by the question each answers, 每晚運行 reached from 總覽, 單位 from 內容
    html = bar.bar("try", "ops", "資料截至 x")
    assert _entries(html) == ["總覽", "內容", "檢索"]
    assert _tools(html) == ["網頁核查", "資料庫"]
    assert "aria-current='page'>檢索</a>" in html and html.count("aria-current") == 1
    assert "href='/'" in html and "href='/sites'" in html and "href='/try'" in html  # its own host's: by path
    assert "href='/tonight'" not in html and "href='/units'" not in html  # no entry of their own any more
    assert "18879" not in html  # 內容 (資料總覽) is 檢視台's own page since 2026-11-21
    assert html.count("class='nk-tool'") == 2 and ">網頁核查</a>" in html  # the same panel's tool: no arrow out
    more = html.split("<details class='nk-more'>", 1)[1].split("</details>", 1)[0]
    assert more.startswith("<summary>更多") and ">資料庫<svg" in more and "網頁核查" in more
    assert "href='http://127.0.0.1:18001/'" in more
    assert "爬蟲進度" not in html and "18877" not in html and "target='_blank'" not in more
    end = html.split("<div class='nk-end'>", 1)[1]
    assert end.startswith("<span class='nk-meta'>資料截至 x</span><button type='button' class='nk-mode'")
    assert "e.key==='Escape'" in html  # 更多 closes by Escape, a click outside, or the focus leaving it
    assert "localStorage.getItem(k)" in html and "r.setAttribute('data-theme',n)" in html  # the light/dark switch


def _find(html: str) -> str:
    """The bar's search box (bar.find_box), between the row of entries and the end."""
    return html.split("</nav>", 1)[1].split("<div class='nk-end'>", 1)[0]


def test_one_box_in_the_bar_finds_a_document_an_address_or_a_site_on_every_page():
    # 2026-11-22 evening: 文件搜尋 was an entry whose page was only this box; it is a box in the bar of every page
    conf = bar.panels()
    assert "doc" not in [p["key"] for p in conf["pages"]] and conf["find"]["path"] == "/doc"
    for current in ("home", "tonight", "try", "check", "units", "site"):
        box = _find(bar.bar(current, "ops"))
        assert box.startswith("<form class='nk-find' role='search' action='/doc' method='get'>"), current
        assert "<input type='search' name='q' " in box and "value=" not in box  # it starts empty
        assert f"placeholder='{conf['find']['placeholder']}'" in box and "aria-label=" in box
    # another panel's (資料庫's) box sends to 檢視台's /doc by its address, righted on the lab PC's own ports as the
    # links are (without it the box on 資料庫 opened there sent to the Mac's tunnel port, where nothing listens)
    tables = bar.bar("tables", "tables")
    away = _find(tables)
    assert "action='http://127.0.0.1:18878/doc'" in away and "class='nk-find'" in away
    assert "querySelectorAll('a[href],form[action]')" in tables and "a.tagName==='FORM'?'action':'href'" in tables
    # the document's own page marks the box, and no entry
    doc = bar.bar("doc", "ops")
    assert "<form class='nk-find nk-on'" in doc and "aria-current" not in doc.split("<span class='nk-sep'", 1)[0]
    assert bar.find_box("home", "ops", {"hosts": {}}) == ""  # panels.json without a box: none drawn
    assert ".nk-find.nk-on" in THEME and ".nk-find {" in THEME


def test_a_page_that_is_no_entry_marks_the_entry_it_is_reached_from():
    # panels.json "within": a night from 總覽, 單位 and one site's page from 內容 (aria-current='true', not 'page')
    within = bar.panels()["within"]
    assert within == {"tonight": "home", "units": "sites", "site": "sites"}
    for current, entry in (("tonight", "總覽"), ("units", "內容"), ("site", "內容")):
        html = bar.bar(current, "ops")
        assert f"aria-current='true'>{entry}</a>" in html and html.count("aria-current") == 1, current
        assert "aria-current='page'" not in html, current
    assert "aria-current='true'" not in bar.bar("check", "ops")  # a tool marks itself, as before
    assert "aria-current='page'>網頁核查</a>" in bar.bar("check", "ops")


def test_the_bar_stays_on_top_and_the_tools_fold_into_more_on_a_narrow_window():
    assert re.search(r"\.nk-bar \{ position: sticky; top: 0;", THEME) and "--nk-bar-h:" in THEME
    narrow = THEME.split("@media (max-width: 860px) {", 1)[1].split("\n}", 1)[0]
    assert "a.nk-tool, .nk-sep, .nk-meta { display: none; }" in narrow and ".nk-more { display: block; }" in narrow
    # a part of a page that stays on top as it scrolls starts under the bar
    for name in ("home_page.py", "try_page.py", "check_page.py", "tc.css", "overview_page.html"):
        assert "var(--nk-bar-h" in SHEETS.get(name, "") or "var(--nk-bar-h" in (
            REPO / "src/nknu/ops" / name).read_text(encoding="utf-8"), name


def test_a_tools_page_marks_its_tool_and_more_and_no_entry():
    html = bar.bar("tables", "tables")
    assert "aria-current='page'>資料庫</a>" in html and "<details class='nk-more nk-on'>" in html  # its own: no arrow
    assert all("aria-current" not in a for a in html.split("<span class='nk-sep'", 1)[0].split("<a ")[1:])
    assert "href='http://127.0.0.1:18878/'" in html and "href='/'" in html  # 總覽 by address; 資料庫 itself by path
    assert "<details class='nk-more'>" in bar.bar("doc", "ops")  # a page of an entry leaves 更多 plain


def test_the_entries_and_a_sites_page_are_one_address_wherever_they_are_linked_from():
    # 網站 is 檢視台's own page since 2026-11-21 (資料總覽 was a service of its own): a path there, an address elsewhere
    assert bar.entry_href("sites", "ops") == "/sites" and bar.entry_href("sites", "tables") == "http://127.0.0.1:18878/sites"
    assert bar.entry_href("tables", "ops") == "http://127.0.0.1:18001/" and bar.entry_href("nothing", "ops") == ""
    assert bar.site_href("c.nknu.edu.tw/aca", "ops") == "/site?site=c.nknu.edu.tw%2Faca"
    assert bar.site_prefix("tables") == "http://127.0.0.1:18878/site?site=" and bar.site_prefix("ops") == "/site?site="


def test_a_page_opened_on_the_serving_machine_rights_its_links_to_that_machines_ports():
    # 2026-11-18: opened on the lab PC itself (127.0.0.1:8878) every jump to another panel went to a port starting
    # with 1, the Mac's tunnel, where nothing listens there
    conf = bar.panels()
    assert bar.ports(conf) == {"18878": "8878", "18001": "8001"}
    assert set(conf["local"]) == set(conf["hosts"])  # a panel with no line would keep the Mac's port over there
    for html in (bar.bar("tonight", "ops"), bar.bar("tables", "tables"), bar.bar("sites", "ops")):
        js = html.rsplit("<script>", 1)[1]
        assert 'const M={"18001": "8001", "18878": "8878"}' in js
        assert "Object.values(M).includes(location.port)" in js  # opened through the tunnel: nothing is touched
        assert "u.port=M[u.port]" in js and "H.includes(u.hostname)" in js  # only a panel's address, never a school's
        assert "DOMContentLoaded" in js and "'pointerdown'" in js and "'contextmenu'" in js  # links drawn later too
    assert bar.port_js({"hosts": {"ops": "http://127.0.0.1:18878"}}) == ""  # no ports of its own named: no script
    assert bar.ports({"hosts": {"a": "http://x:1"}, "local": {"a": "http://x:1", "b": "http://x:2"}}) == {}


def test_the_ports_the_bar_rights_links_to_are_the_ones_the_compose_files_publish():
    conf = bar.panels()
    files = {"ops": "deploy/lab-inspect/compose.yaml", "tables": "deploy/lab-inspect/compose.yaml"}
    for key, file in files.items():
        port = conf["local"][key].rsplit(":", 1)[1]
        assert f'"127.0.0.1:{port}:{port}"' in (REPO / file).read_text(encoding="utf-8"), key


def test_the_sites_page_carries_the_theme_and_the_bar_as_every_page_of_the_panel():
    from nknu.ops import overview

    page = overview.render("<p>x</p><script id='links'>__LINKS__</script>", {}).decode("utf-8")
    assert "--k0:" in page and "class='nk-bar'" in page and "aria-current='page'>內容</a>" in page
    assert _entries(page) == ["總覽", "內容", "檢索"] and "<details class='nk-more'>" in page
    assert "<form class='nk-find' role='search' action='/doc'" in page  # the bar's box, as on every page
    # its links to a site's page and to 資料庫 come from panels.json, as the bar's do: a site's page by path (the same
    # service now), 資料庫 by its address, righted on the lab PC's own ports as the bar's are
    assert '"site": "/site?site="' in page and '"tables": "http://127.0.0.1:18001"' in page
    assert "Object.values(M).includes(location.port)" in page

