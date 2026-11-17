"""The panels' one look (nknu/ops/theme, redesign 2026-11-17: research/dashboard_redesign_2026-11-17/diagnosis.md).

Before it the colours were written three times (檢視台's look.py, 資料庫's tc.css, 資料總覽's page.html) and had drifted
apart, the button colour was 公告's blue, and 15 font sizes were in use. These tests keep it one: the colours only in
theme.css, no blue for acting, the type scale, one top bar on every panel."""

from __future__ import annotations

import re
from pathlib import Path

from nknu.ops import check_page, check_sites, doc_page, look, pipeline_page, site_page, tonight_page
from nknu.ops.theme import bar

REPO = Path(__file__).resolve().parents[2]
THEME = bar.theme_css()
SHEETS = {
    "look.py": look.CSS,
    "pipeline_page.py": pipeline_page.CSS,
    "site_page.py": site_page.CSS,  # the four entries' new pages (structure design 2026-11-17 section 二)
    "tonight_page.py": tonight_page.CSS,  # panels v3 (2026-11-17): 今晚
    # the charts' card and script, whole: its drawings read every colour from theme.css when they draw
    "charts.py": (REPO / "src/nknu/ops/charts.py").read_text(encoding="utf-8"),
    "doc_page.py": doc_page.CSS,
    "check_page.py": (REPO / "src/nknu/ops/check_page.py").read_text(encoding="utf-8"),
    "check_sites.py": (REPO / "src/nknu/ops/check_sites.py").read_text(encoding="utf-8"),
    "tc.css": (REPO / "deploy/lab-inspect/datasette/static/tc.css").read_text(encoding="utf-8"),
    "page.html": (REPO / "deploy/lab-overview/page.html").read_text(encoding="utf-8"),
}
SIZES = {"11px", "12px", "13px", "14px", "16px", "20px", "26px"}
assert check_page and check_sites  # imported so a broken page module fails here too


def _blocks(css: str) -> list[str]:
    return re.findall(r":root(?:\[data-theme=\"dark\"\]|:not\(\[data-theme=\"light\"\]\))? \{([^}]*)\}", css)


def test_both_dark_blocks_are_the_same_and_every_colour_has_a_dark_step():
    light, dark_media, dark_forced = _blocks(THEME)
    assert dark_media.split() == dark_forced.split()
    names = set(re.findall(r"(--[a-z0-9-]+):", light)) - {"--font", "--mono", "--amt", "--page", "--gutter"}  # not colours
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
    """The four entries of a bar as they read, in order."""
    nav = html.split("<nav class='nk-tabs'", 1)[1].split("</nav>", 1)[0]
    return re.findall(r">([^<>]+)</a>", nav)


def test_the_bar_has_four_entries_and_a_tools_menu_and_links_its_own_host_by_path():
    # structure design 2026-11-17 section 二: 今晚 | 網站 | 試用 | 文件, and 資料庫, 爬蟲進度 in the 工具 menu
    html = bar.bar("try", "ops", "資料截至 x")
    assert _entries(html) == ["每晚運行", "網站", "搜尋", "找文件"]
    assert "aria-current='page'>搜尋</a>" in html and html.count("aria-current") == 1
    assert "href='/'" in html and "href='/try'" in html and "href='/doc'" in html  # its own host's: by path
    assert "href='http://127.0.0.1:18879/'" in html  # 網站 is 資料總覽, its own server
    menu = html.split("<details class='nk-tools'>", 1)[1].split("</details>", 1)[0]
    assert menu.startswith("<summary>工具") and ">資料庫<svg" in menu and ">爬蟲進度<svg" in menu
    assert "href='http://127.0.0.1:18001/'" in menu
    assert "href='http://127.0.0.1:18877/progress'" in menu and menu.count("target='_blank'") == 1  # the crawler's
    assert "class='nk-meta'>資料截至 x</span>" in html and html.index("nk-meta") < html.index("nk-tools")
    assert "e.key==='Escape'" in html  # the menu closes by Escape, a click outside, or the focus leaving it


def test_a_tools_page_marks_the_menus_button_and_no_entry():
    html = bar.bar("tables", "tables")
    assert "aria-current='page'>資料庫<svg" in html and "<details class='nk-tools nk-on'>" in html
    assert all("aria-current" not in a for a in html.split("</nav>", 1)[0].split("<a ")[1:])
    assert "href='http://127.0.0.1:18878/'" in html and "href='/'" in html  # 今晚 by address; 資料庫 itself by path
    assert "<details class='nk-tools'>" in bar.bar("doc", "ops")  # a page of an entry leaves the button plain


def test_the_entries_and_a_sites_page_are_one_address_wherever_they_are_linked_from():
    assert bar.entry_href("sites", "ops") == "http://127.0.0.1:18879/" and bar.entry_href("sites", "overview") == "/"
    assert bar.entry_href("tables", "ops") == "http://127.0.0.1:18001/" and bar.entry_href("nothing", "ops") == ""
    assert bar.site_href("c.nknu.edu.tw/aca", "ops") == "/site?site=c.nknu.edu.tw%2Faca"
    assert bar.site_prefix("overview") == "http://127.0.0.1:18878/site?site="


def test_the_overview_server_puts_the_theme_and_the_bar_on_its_page():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ov_server", REPO / "deploy/lab-overview/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    page = server.render("<p>x</p><script id='links'>__LINKS__</script>", {}).decode("utf-8")
    assert "--k0:" in page and "class='nk-bar'" in page and "aria-current='page'>網站</a>" in page
    assert _entries(page) == ["每晚運行", "網站", "搜尋", "找文件"] and "<details class='nk-tools'>" in page
    # its links to a site's page and to 資料庫 come from panels.json, as the bar's do
    assert '"site": "http://127.0.0.1:18878/site?site="' in page and '"tables": "http://127.0.0.1:18001"' in page
