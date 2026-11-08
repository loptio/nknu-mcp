"""R6 and the date picker's helper page (11-03, P52): My97 DatePicker opens its own helper page
(My97DatePicker.htm) in a frame of the page the browser shows; it is the library's, not a menu item the round missed
(教务处's false ✗ of 10-27). Offline, on reconcile's synthetic site; made-up text, no names."""

from __future__ import annotations

from dataclasses import replace

from nknu.archive import reconcile
from nknu.archive.reconcile import FAIL, PASS, Sources
from tests.archive.test_reconcile import PLAN, SITE, W, _clean_site, _same_spot

_PICKER = '<iframe src="/aca/js/WdatePicker/My97DatePicker.htm" frameborder="0"></iframe>'


def _r6(tmp_path, extra: str):
    c, net = _clean_site(tmp_path)
    fr = c.frontier
    same = _same_spot(tmp_path)

    def spot(url):
        view = same(url)
        return replace(view, html=view.html.replace("</nav>", extra + "</nav>").replace("</body>", extra + "</body>"))

    try:
        return reconcile.reconcile_sites(fr, tmp_path, [SITE], batch="b", plan=PLAN, sources=Sources(cc_dir=None),
                                         spot=spot).sites[0].checks["R6"]
    finally:
        net.sink.close()
        fr.close()


def test_the_date_pickers_helper_page_is_no_missed_menu_item(tmp_path):
    r6 = _r6(tmp_path, _PICKER)
    assert r6.status == PASS, r6.items


def test_a_frame_only_the_browser_sees_is_still_a_missed_menu_item(tmp_path):
    r6 = _r6(tmp_path, '<iframe src="/hidden-frame.html"></iframe>')
    assert r6.status == FAIL
    assert {"what": "menu", "target": f"{W}/hidden-frame.html"} in [m for i in r6.items for m in i.get("missing", [])]
