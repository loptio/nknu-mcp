"""11-03 fixes of how pages are read (P51, P53). Offline: the pages are inline fixtures of the real shapes, names
O-masked; nothing is sent."""

from __future__ import annotations

import re

from nknu.archive import links, postback, profiles

# P51: c.nknu's member.aspx (編制與業務, staff introductions) lays its people out in a #MasterGrid with no link and no
# press in a row: the shape of the official archive's /aca/member.aspx pages (11-02 copy, 7 of them ended
# mismatch:empty), names and numbers made up
MEMBER = "https://c.nknu.edu.tw/aca/member.aspx?pn=8&pclass=A1002&gi=1"
_PERSON = """<tr><td><div class="row"><div class="col-md-3"><img src='UploadFile/MemberImg/ImgMember_1_Shrink.png'
 alt='王O明' /></div><div class="col-md-9"><h3><span id="MasterGrid_MemNameLable_{i}">王O明</span>&nbsp;<small>
<span id="MasterGrid_MemTitleLabel_{i}">組員</span></small></h3><strong>分　　機：</strong>
<span id="MasterGrid_MemPhoneLabel_{i}">1000</span><br><h4>業務職掌：</h4>
<span id="MasterGrid_MemJob_{i}">1.綜合業務<br/>2.其他交辦事項</span></div></div><hr /></td></tr>"""
MEMBER_HTML = f"""<html><body><form method="post" action="./member.aspx?pn=8" id="form1">
<input type="hidden" name="__VIEWSTATE" value="x" /><h4><span id="lbl_Title">教務長室 - 編制與業務</span></h4>
<div class="FocusMain"><div><table cellspacing="0" cellpadding="3" id="MasterGrid" width="100%">
<tr><th scope="col">&nbsp;</th></tr>{"".join(_PERSON.format(i=i) for i in range(3))}</table></div></div>
</form></body></html>"""


def test_member_page_is_a_content_page_not_an_empty_list():
    pr = postback.read_page(profiles.CNKNU, MEMBER, MEMBER_HTML, site="c.nknu.edu.tw/aca", list_url=MEMBER)
    assert pr.rowset is None and pr.walk == "none"  # no list walk, so no listwalk row ending mismatch:empty (要人看)
    assert not pr.unknown and not pr.unplanned and pr.content  # by the one judgement (FIX 3, 11-06), not its path


def test_other_cnknu_master_grids_are_still_lists():
    # the same grid on the pages that are lists (newslist, filesdownload …) still walks
    rows = next(rs for rs in profiles.CNKNU.rows if rs.name == "MasterGrid")
    for path in ("/aca/newslist.aspx", "/aca/filesdownload.aspx", "/aca/album.aspx", "/reality/record.aspx",
                 "/aca/default.aspx", "/reality/"):
        assert not rows.paths or re.search(rows.paths, path), path
    assert rows.paths == ""  # no page is left out by its path any more: member.aspx reads as a table (FIX 3, 11-06)


# P53: SSO WebIdx's .js (R7 reads it; 生活辅导组 webidx=595 in the 10-26/27 pilot) holds two unexpanded server
# template strings in a __doPostBack call: not a control name, not an address
def test_unexpanded_asp_template_in_sso_js_is_ignored():
    site = "sso.nknu.edu.tw/web/595"
    for key in ("js_postback:<%= btnPageNav.UniqueID %>", "js_postback:<%= btnClearFileSearch.UniqueID %>"):
        assert profiles.decision(site, key) == "ignore", key
        assert links.unhandled_need(profiles.decision(site, key)) == ("no", "unhandled:ignore")
    # a real control name there is still nobody's decision
    assert profiles.decision(site, "js_postback:ucNews$btnPageNav") == profiles.UNKNOWN
    assert profiles.check_profiles() == []
