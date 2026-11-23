"""The 11-23 page design, laid over the pages the panel draws.

The owner designed every page on a copy first (research/design_2026-11-23), then asked for it in the panel. A page's
layer is a CSS and a script from theme/design/<name>.css and .js, which reshape the page in the browser (icons for kinds,
a clock for times, split bars for counts, a step's facts as one row of chips, filters that land on exactly the rows a
number counts). Content that needs data is drawn by the server (site_checks.py, and the round-2 modules).

Round 1 layers: site (one site), check (網頁核查, the whole school or one site's lists), pagecheck (one page's check),
doc (文件搜尋 with nothing asked), found (its results), trace (文件頁), try (搜尋), why (為什麼這樣排). A page may load
several layers (round 2: 內容 loads its own, the night view's and the drawers'). Every load starts with icons (the
shared kind icons and clock); a name without files is skipped."""
from functools import cache
from pathlib import Path

DIR = Path(__file__).parent / "theme" / "design"


@cache
def _read(name: str) -> str:
    path = DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def layer(*names: str) -> str:
    """The <style> and <script> blocks of the named layers (with the shared icons first), or '' when none has files."""
    names = tuple(n for n in names if n and n != "icons" and (_read(f"{n}.css") or _read(f"{n}.js")))
    if not names:
        return ""
    css = _read("icons.css") + "".join(_read(f"{n}.css") for n in names)
    js = _read("icons.js") + "".join(_read(f"{n}.js") for n in names)
    key = "-".join(names)
    return f"<style id='design-{key}'>{css}</style><script id='design-{key}-js'>{js}</script>"


# the round-1 names, kept for the tests that walk every layer
LAYERS = ("site", "check", "pagecheck", "doc", "found", "trace", "try", "why")
