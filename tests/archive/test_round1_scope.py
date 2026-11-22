"""What round 1 fetches (round-1 design §4, scope record 三): the list in config/archive_round1.json (§4.1: 107 sites,
paths, the attachment service as a resource), round1.verdict() (§4.3: r1, resource, round 2 with its reason, the
owner site and tier each row counts towards, leaf), the extra seeds and the login re-check list (§4.2), and each rule
of §4.5 in scope.judge(), one example either way: w3's admin.html is not a back office but an /admin/ directory is,
the meeting minutes of type 19 are open while 4, 8 and 9 stay shut, the student mail page is open and the leave page is
not, the VPN and the bank's fee site are logins, the two internal services are blocked by their bare IP too, a
full-width colon in a path is a trap, Google Sites' /_/ and /feeds are its system, Wix's lightbox parameter is dropped;
Google redirects are unwrapped, TIF is an attachment, SSO's download programs are attachments and its image reader an
image."""

from __future__ import annotations

import importlib.util
import json
import re

import pytest
from opencc import OpenCC

from nknu.archive import round1, scope
from nknu.settings import REPO_ROOT

PLAN = round1.load()
CONFIG = REPO_ROOT / "config" / "archive_round1.json"


def page(owner: str, tier: int) -> dict:
    """The url row of the page a link was found on: only its owner site and tier matter here."""
    return {"owner_site": owner, "tier": tier}


ACA = page("c.nknu.edu.tw/aca", 0)  # 教務處, 全校重要
LATER = page("c.nknu.edu.tw/ae", 2)  # a round-2 page


def v(url: str, src=None, how: str = "link") -> round1.Placement:
    return round1.place(PLAN, scope.canonical(url) or url, src, how)


# ---------------------------------------------------------------- §4.1: the list


# the record's 附表一 (the school's own units on off-campus sites, round two) that the site map already lists
IN_SITE_MAP = ("sites.google.com/iec-nknu.page/iec", "sites.google.com/view/taiwanology",
               "pairnknu.wixstudio.io/pair-center", "www.nknu-vd.com", "sites.google.com/view/nknu-edu-teach1",
               "mandy900316.wixsite.com/website", "ocsnknu.wixsite.com/index", "nknubwy2.wixsite.com/nknu-bwy")


def test_the_list_holds_107_round_1_sites_one_resource_and_the_rest_for_round_2():
    by_round = {}
    for s in PLAN.sites.values():
        by_round.setdefault((s.round, s.why if s.round == "2" else s.imp), []).append(s.site)
    # 368 keys of the site map, and 27 more (design §4.1): 附表一's 27 URLs not in the site map as 25 keys (two Google
    # Groups forums, two TED pages), and the two centres of record 四
    assert len(PLAN.sites) == 368 + 27
    assert len(by_round[("1", "全校重要")]) == 34 and len(by_round[("1", "本系重要")]) == 73
    assert by_round[("resource", None)] == ["sso.nknu.edu.tw/webservices"]
    assert sorted(by_round[("2", "js_site")]) == ["gauss.nknu.edu.tw", "ite.nknu.edu.tw", "ord.nknu.edu.tw",
                                                  "see.nknu.edu.tw", "w3.nknu.edu.tw"]
    assert len(by_round[("2", "other_site")]) == 255 - 8
    assert len(by_round[("2", "offsite_unit")]) == 8 + 27
    assert set(IN_SITE_MAP) <= set(by_round[("2", "offsite_unit")])
    for key in IN_SITE_MAP:  # still the site map's row: its own home, the whole site
        assert (PLAN.sites[key].leaf, PLAN.sites[key].paths) == (0, ()), key


def test_the_units_off_campus_are_listed_with_their_seeds_paths_and_leaves():
    groups, ted = PLAN.sites["groups.google.com"], PLAN.sites["www.ted.com"]
    assert groups.seeds == ("https://groups.google.com/g/ujujnknu", "https://groups.google.com/g/nknutc")
    assert groups.paths == ("/g/ujujnknu", "/g/nknutc") and groups.leaf == 0
    assert ted.seeds == ("https://www.ted.com/tedx/events/53158", "https://www.ted.com/tedx/events/56115")
    assert ted.leaf == 1 and ted.paths == ("/tedx/events/53158", "/tedx/events/56115")
    for key, path in (("solink.soundon.fm", "/gigepodcast"), ("lit.link", "/zh-tw/119freshmancamp_nknu")):
        assert (PLAN.sites[key].leaf, PLAN.sites[key].paths) == (1, (path,)), key
    assert PLAN.sites["github.com"].paths == ("/gdg-on-campus-nknu",)
    assert PLAN.sites["2060631web-repository.github.io"].paths == ("/nknu-aim-i",)
    assert PLAN.sites["cirn.k12ea.gov.tw"].paths == ("/web1240/",)
    assert PLAN.sites["cirn.moe.edu.tw"].params == (("sid", "1219"),)
    assert PLAN.sites["nknuchinese.tw"].seeds == ("https://nknuchinese.tw/",)  # one seed: its home
    for s in PLAN.sites.values():  # every entry with a home starts there (the attachment service has none)
        assert s.seeds[0] == s.home if s.home else s.seeds == (), s.site


def test_the_record_s_off_campus_units_are_all_seeds_of_the_list():
    """附表一 of the scope record, read from the record itself (docs/ is not in git): each URL is a seed of a list
    entry with why=offsite_unit, so a change of the record shows up here."""
    record = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-10-25-crawl-scope-decisions.md"
    if not record.exists():
        pytest.skip("docs/ is not in git: the record is only on the maintainer's machine")
    table = record.read_text(encoding="utf-8").split("## 附表一", 1)[1].split("\n## ", 1)[0]
    urls = re.findall(r"https?://[^\s|、]+", table)
    assert len(urls) == 35
    seeds = {u: s for s in PLAN.sites.values() for u in s.seeds}
    for url in urls:
        canon = scope.canonical(url)
        if "#!forum/ujujnknu" in url:  # canonical() drops the #! part: the forum's own address stands for it
            canon = "https://groups.google.com/g/ujujnknu"
        key = scope.site_key(canon)
        site = PLAN.sites[key]
        assert site.why == "offsite_unit", url
        assert canon in seeds or (key in IN_SITE_MAP and site.holds(canon)), url


def test_the_pages_of_record_4_and_claude_s_two_are_round_2_pages():
    pages = dict(PLAN.round2_pages)
    assert len(pages) == 8
    assert sorted(set(pages.values())) == ["claude", "record4"]
    assert sum(b == "record4" for b in pages.values()) == 6
    assert pages["https://www.nknush.kh.edu.tw/"] == "claude" and pages["https://tqa.rcpet.edu.tw/"] == "claude"
    for url in pages:
        got = v(url)
        assert (got.verdict, got.reason, got.leaf, got.state) == ("round2", "round2:offsite", 1, "later"), url


def test_each_round_1_site_has_a_home_inside_its_own_round_1_part():
    for s in PLAN.sites.values():
        if s.round == "1":
            assert s.home and s.imp in round1.TIER_OF_IMP
            assert v(s.home).verdict == "r1", s.site  # the seed is fetched, not left for round 2
    ksped, lost = PLAN.sites["ksped.nknu.edu.tw"], PLAN.sites["sso.nknu.edu.tw/dataopen2search"]
    # round two, batch two (design 2026-11-17-round2-batch2 §三.1): ksped is round one whole; the CV pages join
    assert ksped.paths == () and ksped.imp == "全校重要"
    assert lost.paths == ("/dataopen2search/lostandfound.aspx", "/dataopen2search/opencoursetablepure.aspx",
                          "/dataopen2search/vita.aspx")


def test_the_committed_list_is_what_the_script_makes_from_the_site_map():
    rows = REPO_ROOT / "research" / "project_audit_2026-10-25" / "site_map_rows.json"
    rules = REPO_ROOT / "research" / "project_audit_2026-10-25" / "rule_lists_full.json"
    if not (rows.exists() and rules.exists()):
        pytest.skip("research/ is not in git: the site map is only on the maintainer's machine")
    spec = importlib.util.spec_from_file_location("build_archive_round1", REPO_ROOT / "scripts" / "build_archive_round1.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.build(rows, rules) == json.loads(CONFIG.read_text(encoding="utf-8"))


def _script():
    spec = importlib.util.spec_from_file_location("build_archive_round1", REPO_ROOT / "scripts" / "build_archive_round1.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_names_and_notes_of_the_list_are_in_traditional_characters():
    # 2026-11-23: the list is shown as it is on the panels, whose text is Traditional Chinese in Taiwan's usage; the
    # site map's rows and the names typed in the script came in both scripts (313 of 395 names held simplified
    # characters). Every name and note goes through the script's tw_name on its way out; imp and platform are values
    # the programs compare and stay as they are.
    tw = _script().tw_name
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    words = ([s["name"] for s in plan["sites"]] + [e["name"] for e in plan["extra"]] + [e["why"] for e in plan["extra"]]
             + [s["why"] for s in plan["sites"] if re.search("[\u4e00-\u9fff]", s.get("why") or "")] + [plan["about"]])
    assert [w for w in words if tw(w) != w] == []
    one = OpenCC("s2tw")  # and by a count of its own: no character the converter would rewrite is left (台 is kept)
    assert sorted({ch for w in words for ch in w if ch != "台" and one.convert(ch) != ch}) == []
    name = {s["site"]: s["name"] for s in plan["sites"]}
    assert name["lis.nknu.edu.tw"] == "圖書資訊處（圖書館＋計網）" and name["c.nknu.edu.tw/aca"] == "教務處"
    # 台 stays as it was written (平台, 台灣): common in Taiwan, and what the school's own pages mostly write
    assert name["ai.nknu.edu.tw"] == "人工智慧研發產學中心（雲端平台）" and name["school.bot.com.tw"] == "台灣銀行學雜費入口網"
    # and the mainland terms the site map used are Taiwan's
    assert name["sso.nknu.edu.tw/software"] == "校園授權軟體與免費軟體總覽"
    assert name["sso.nknu.edu.tw/webservices"] == "SSO 附件介面（各單位網站掛的附件）"
    assert name["140.127.40.173"] == "SSO 伺服器的 IP 網址" and name["sso.nknu.edu.tw/web/633"] == "高教深耕計畫"
    assert name["sso.nknu.edu.tw/passwordstation"] == "帳號密碼服務站（PasswordStation）"
    # what the programs compare is not rewritten
    assert {s.get("imp") for s in plan["sites"]} >= {"全校重要", "本系重要", "基本没用", "偶尔有用"}
    assert tw("软件工程与管理学系高中生专区") == "軟體工程與管理學系高中生專區" and tw("教育學院（短网址）") == "教育學院（短網址）"


def test_the_list_is_read_from_the_configuration_directory(tmp_path, monkeypatch):
    # NKNU_CONFIG_DIR, as everything else reads its configuration (settings.py): the lab machine keeps it elsewhere
    entry = {"site": "c.nknu.edu.tw/aca", "imp": "全校重要", "round": 1, "home": "https://c.nknu.edu.tw/aca/"}
    (tmp_path / "archive_round1.json").write_text(json.dumps({"sites": [entry], "extra": [], "login_check": []}),
                                                  encoding="utf-8")
    monkeypatch.setenv("NKNU_CONFIG_DIR", str(tmp_path))
    assert round1.plan_path() == tmp_path / "archive_round1.json"
    assert list(round1.load().sites) == ["c.nknu.edu.tw/aca"]
    monkeypatch.delenv("NKNU_CONFIG_DIR")
    assert round1.plan_path() == CONFIG and len(round1.load().sites) == 368 + 27


def test_a_list_written_wrong_is_refused(tmp_path):
    good = {"site": "c.nknu.edu.tw/aca", "imp": "全校重要", "round": 1, "home": "https://c.nknu.edu.tw/aca/"}
    for bad in ([good, good],  # one key, twice
                [{**good, "round": 3}],
                [{**good, "imp": "偶尔有用"}],  # round 1 is the important sites only
                [{**good, "home": None}]):
        path = tmp_path / "plan.json"
        path.write_text(json.dumps({"sites": bad, "extra": [], "login_check": []}), encoding="utf-8")
        with pytest.raises(ValueError):
            round1.load(path)


# ---------------------------------------------------------------- §4.3: r1, resource, round 2, owner site


def test_a_page_of_a_round_1_site_is_r1_and_counts_towards_its_own_site():
    p = v("https://c.nknu.edu.tw/reality/newsview.aspx?nid=1", ACA)
    assert (p.verdict, p.state, p.reason, p.owner_site, p.tier, p.leaf) == ("r1", "pending", None,
                                                                           "c.nknu.edu.tw/reality", 0, 0)
    assert v("https://c.nknu.edu.tw/ccee/newsview.aspx?nid=1").tier == 1  # 本系重要
    assert v("https://sso.nknu.edu.tw/services/web/default.aspx?webidx=127&selpage=morenews").owner_site == \
        "sso.nknu.edu.tw/web/127"


def test_paths_split_one_key_between_the_rounds():
    # ksped is round one whole since round two's batch two (design 2026-11-17-round2-batch2 §三.1)
    assert v("https://ksped.nknu.edu.tw/resource/news.aspx?id=3").verdict == "r1"
    assert v("https://ksped.nknu.edu.tw/news.aspx?id=3").verdict == "r1"
    assert v("https://sso.nknu.edu.tw/DataOpen2Search/LostAndFound.aspx").verdict == "r1"
    assert v("https://sso.nknu.edu.tw/dataopen2search/opencoursetablepure.aspx?unit=209072").verdict == "r1"
    assert v("https://sso.nknu.edu.tw/DataOpen2Search/vita.aspx?UNID=3131").verdict == "r1"  # the CV pages join
    other = v("https://sso.nknu.edu.tw/dataopen2search/demo.aspx")
    assert (other.verdict, other.reason, other.state, other.tier) == ("round2", "round2:other_site", "later", 2)
    assert v("https://sso.nknu.edu.tw/dataopen2search/opendatasearch.aspx?dataidx=1").reason == "round2:other_site"


def test_the_attachment_service_is_a_resource_of_the_page_that_links_it():
    url = "https://sso.nknu.edu.tw/webservices/ajaxdownloadfile.aspx?caes=abc"
    got = v(url, ACA)
    assert (got.verdict, got.owner_site, got.tier, got.leaf, got.kind0) == ("resource", "c.nknu.edu.tw/aca", 0, 1,
                                                                            "file")
    dept = v(url, page("c.nknu.edu.tw/ccee", 1))
    assert (dept.owner_site, dept.tier) == ("c.nknu.edu.tw/ccee", 1)  # the tier of the page, not of the service
    for src in (LATER, None):  # only a round-2 page links it (or only the old archive knows it)
        later = v(url, src)
        assert (later.verdict, later.reason, later.owner_site, later.tier) == ("round2", "round2:other_site",
                                                                               "sso.nknu.edu.tw/webservices", 2)


def test_attachments_images_and_frames_follow_their_page_onto_any_host():
    school_file = v("https://c.nknu.edu.tw/ae/uploadfile/x.pdf", ACA)  # another unit's upload folder
    assert (school_file.verdict, school_file.owner_site, school_file.leaf) == ("resource", "c.nknu.edu.tw/aca", 1)
    image = v("https://i.imgur.com/AbCd.png", ACA, "image")  # an image host
    assert (image.verdict, image.kind0, image.leaf) == ("resource", "image", 1)
    gimg = v("https://lh3.googleusercontent.com/abc=w1280", ACA, "image")  # no extension: how decides
    assert (gimg.verdict, gimg.kind0) == ("resource", "image")
    linked = v("https://c.nknu.edu.tw/ae/uploadfile/poster.jpg", ACA)  # <a href="poster.jpg">
    assert (linked.verdict, linked.kind0) == ("resource", "image")
    frame = v("https://liling.nknu.edu.tw/main.htm", ACA, "frame")
    assert (frame.verdict, frame.owner_site, frame.leaf, frame.kind0) == ("resource", "c.nknu.edu.tw/aca", 0, "page")
    embed = v("https://abcd-atari-embeds.googleusercontent.com/embeds/16cb/x.html", page("sites.google.com/x", 1),
              "frame")
    assert (embed.verdict, embed.leaf) == ("resource", 1)
    canva = v("https://www.canva.com/design/DAF/view?embed", ACA, "frame")
    assert (canva.verdict, canva.reason, canva.leaf, canva.owner_site) == ("round2", "round2:offsite", 1,
                                                                           "www.canva.com")
    moe = v("https://www.edu.tw/files/x.pdf", ACA)  # an off-campus attachment: round one too (record 五·15)
    assert (moe.verdict, moe.owner_site, moe.leaf, moe.kind0) == ("resource", "c.nknu.edu.tw/aca", 1, "file")
    form = v("https://docs.google.com/forms/d/e/1FAIpQLSf/viewform?usp=sf_link", ACA)
    assert (form.verdict, form.kind0, form.leaf) == ("resource", "form", 1)


def test_a_round_2_page_links_nothing_into_round_1_but_round_1_sites():
    assert v("https://i.imgur.com/AbCd.png", LATER, "image").reason == "round2:offsite"
    assert v("https://liling.nknu.edu.tw/main.htm", LATER, "frame").reason == "round2:other_site"
    assert v("https://c.nknu.edu.tw/aca/default.aspx", LATER).verdict == "r1"


def test_ordinary_links_out_of_round_1():
    js = v("https://w3.nknu.edu.tw/zh/news", ACA)  # 11-17: round two's first batch crawls the JS sites now
    assert (js.verdict, js.owner_site, js.tier, js.leaf) == ("r1", "w3.nknu.edu.tw", 0, 0)
    other = v("https://c.nknu.edu.tw/ae/default.aspx", ACA)
    assert (other.reason, other.owner_site, other.leaf) == ("round2:other_site", "c.nknu.edu.tw/ae", 0)
    off = v("https://www.moe.gov.tw/news.aspx", ACA)  # one layer out, in round 2 (record 一·1)
    assert (off.reason, off.leaf, off.state, off.owner_site) == ("round2:offsite", 1, "later", "www.moe.gov.tw")
    gsite = v("https://sites.google.com/view/other-unit", ACA)
    assert (gsite.reason, gsite.leaf) == ("round2:offsite", 1)
    unit = v("https://sites.google.com/view/taiwanology", ACA)  # a school unit's site off campus: the whole site,
    # crawled now by round two's batch two (design 2026-11-17-round2-batch2 §三.1)
    assert (unit.verdict, unit.leaf, unit.owner_site, unit.tier) == ("r1", 0, "sites.google.com/view/taiwanology", 1)
    low = v("https://www.nknu-vd.com/works", ACA)  # one the list calls 基本没用: still round two's
    assert (low.reason, low.leaf, low.owner_site) == ("round2:offsite_unit", 0, "www.nknu-vd.com")


def test_out_verdicts_make_no_row():
    for url, why in (("https://www.facebook.com/nknu", "out:social"), ("https://sso.nknu.edu.tw/", "out:login"),
                     ("https://drive.google.com/file/d/x/view", "out:never_host"),
                     ("https://c.nknu.edu.tw/aca/a/a/a/a", "out:trap"), ("https://c.nknu.edu.tw/aca/x.mp4", "out:media")):
        got = v(url, ACA)
        assert (got.verdict, got.reason, got.state, got.row, got.owner_site) == ("out", why, None, False, None)
    assert v("https://c.nknu.edu.tw/aca/", ACA).row and v("https://www.moe.gov.tw/", ACA).row


@pytest.mark.parametrize("url", [
    "https://login.microsoftonline.com/",  # Office 365's sign-in, linked from lis (rule_lists_full.json C2; 三·13)
    "https://portal.office.com/", "https://outlook.office365.com/mail/",
    "https://someunit.wordpress.com/wp-login.php", "https://someunit.wordpress.com/wp-admin/",
    "https://example.com/admin/index.php", "https://sites.google.com/view/x/login",
    "https://search.ebscohost.com/login.aspx?direct=true&db=aph",
])
def test_off_campus_logins_and_back_offices_are_out_and_make_no_row(url):
    # layer 1 (§4.3): a login is never fetched, off campus too: no round-2 row that `retry round2` would fetch
    got = v(url, ACA)
    assert (got.verdict, got.reason, got.row) == ("out", "out:login", False)
    assert scope.judge(scope.canonical(url), {scope.site_key(scope.canonical(url))}).reason == "login"  # a directory too


def test_off_campus_content_next_to_a_login_is_still_round_2():
    assert v("https://example.com/admin/file/rule.pdf", LATER).reason == "round2:offsite_file"  # a file in an admin folder
    assert v("https://forms.office.com/r/UADrXeYXRz", ACA).reason == "round2:offsite"  # a form, not a sign-in
    assert v("https://www.microsoft.com/zh-tw/education", ACA).reason == "round2:offsite"


@pytest.mark.parametrize("url", [
    "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3678.42", "https://www.google.com/maps/place/NKNU",
    "https://google.com/maps?q=nknu", "https://maps.google.com/?q=nknu", "https://maps.google.com.tw/maps?q=x",
    "https://www.google.com.tw/maps/search/高師大", "https://maps.app.goo.gl/TsEYN6G7DzL565jG7",
    "https://goo.gl/maps/7Gr3NDiEYHVMkSUp6", "https://www.addtoany.com/share#url=x", "https://www.addthis.com/bookmark.php",
    "https://platform-api.sharethis.com/x", "https://pinterest.com/pin/create/button/?url=https://vd.nknu.edu.tw/x",
    "https://www.googletagmanager.com/ns.html?id=GTM-ABC", "https://www.google-analytics.com/collect?v=1",
])
def test_maps_share_buttons_and_trackers_are_links_only(url):
    # record 一·1 and design §4.4: not content, never a part the page lacks, never fetched in round 2 either
    canon = scope.canonical(url)
    assert scope.judge(canon).reason == "not_content" and scope.not_content(canon)
    for how in round1.HOW:
        got = v(url, ACA, how)
        assert (got.verdict, got.reason, got.row) == ("out", "out:not_content", False), how


def test_not_content_is_only_those():
    for url, reason in (("https://www.google.com/", "offsite"), ("https://goo.gl/AbCd", "offsite"),
                        ("https://www.pinterest.com/nknu/", "offsite"), ("https://www.openstreetmap.org/#map=18", "offsite"),
                        ("https://c.nknu.edu.tw/aca/maps.aspx", "in_scope"), ("https://www.google.com/mapsx", "offsite")):
        assert scope.judge(scope.canonical(url)).reason == reason, url
    assert "not_content" in scope.HARD_OUT


def test_a_frame_the_page_hides_is_a_frame_on_campus_and_only_a_link_off_campus():
    # links.Frame.hidden: width or height 0, display:none … (a tracker's frame); a school one is the page's part still
    off = v("https://track.example.com/px.html", ACA, "hidden")
    assert (off.verdict, off.reason, off.row) == ("out", "out:not_content", False)
    assert v("https://track.example.com/px.html", LATER, "hidden").reason == "out:not_content"
    school = v("https://liling.nknu.edu.tw/main.htm", ACA, "hidden")
    assert (school.verdict, school.owner_site, school.leaf) == ("resource", "c.nknu.edu.tw/aca", 0)
    assert v("https://c.nknu.edu.tw/aca/frame.aspx", ACA, "hidden").verdict == "r1"


def test_every_reason_is_one_of_the_known_codes():
    urls = ["https://c.nknu.edu.tw/aca/", "https://w3.nknu.edu.tw/", "https://c.nknu.edu.tw/ae/",
            "https://www.moe.gov.tw/", "https://www.edu.tw/a.pdf", "https://sso.nknu.edu.tw/webservices/ajaxfile.aspx",
            "https://github.com/GDG-on-Campus-NKNU/x", "https://www.ted.com/tedx/events/53158"]
    for url in urls:
        for src in (ACA, LATER, None):
            for how in round1.HOW:
                if how == "click" and src is not ACA:
                    continue  # a tab is pressed on a round-1 page only (the next test)
                got = v(url, src, how)
                if got.verdict == "out":  # an off-campus frame the page hides, or a script nobody fetches
                    assert (how, got.reason) in (("hidden", "out:not_content"), ("script", "out:media")), (url, how)
                    continue
                assert got.verdict in ("r1", "resource", "round2")
                assert got.reason is None if got.verdict != "round2" else got.reason in round1.ROUND2_REASONS


# ---------------------------------------------------------------- the units off campus, round two (§4.1, §4.3)


@pytest.mark.parametrize("url, reason, leaf, owner", [
    ("https://github.com/GDG-on-Campus-NKNU/NKNU-Connect", "round2:offsite_unit", 0, "github.com"),
    ("https://github.com/gdg-on-campus-nknu", "round2:offsite_unit", 0, "github.com"),
    ("https://github.com/someone-else/repo", "round2:offsite", 1, "github.com"),  # the same host, another organisation
    ("https://github.com/GDG-on-Campus-NKNU2", "round2:offsite", 1, "github.com"),  # whole segments only
    ("https://groups.google.com/g/nknutc/c/AbCdEf", "round2:offsite_unit", 0, "groups.google.com"),
    ("https://groups.google.com/g/nknutc2", "round2:offsite", 1, "groups.google.com"),  # /g/nknutc holds no nknutc2
    ("https://groups.google.com/g/ujujnknu", "round2:offsite_unit", 0, "groups.google.com"),
    ("https://www.ted.com/tedx/events/53158", "round2:offsite_unit", 1, "www.ted.com"),  # that page only
    ("https://www.ted.com/tedx/events/56115/", "round2:offsite_unit", 1, "www.ted.com"),
    ("https://www.ted.com/tedx/events/53158/x", "round2:offsite", 1, "www.ted.com"),  # under it: not the unit's
    ("https://www.ted.com/talks/x", "round2:offsite", 1, "www.ted.com"),
    ("https://solink.soundon.fm/gigepodcast", "round2:offsite_unit", 1, "solink.soundon.fm"),
    ("https://lit.link/zh-tw/119freshmancamp_NKNU", "round2:offsite_unit", 1, "lit.link"),
    ("https://cirn.moe.edu.tw/WebContent/index.aspx?sid=1219&mid=15043", "round2:offsite_unit", 0, "cirn.moe.edu.tw"),
    ("https://cirn.moe.edu.tw/WebContent/News.aspx?SID=1219", "round2:offsite_unit", 0, "cirn.moe.edu.tw"),
    ("https://cirn.moe.edu.tw/WebContent/index.aspx?sid=1220&mid=1", "round2:offsite", 1, "cirn.moe.edu.tw"),
    ("https://cirn.moe.edu.tw/WebContent/index.aspx?mid=1", "round2:offsite", 1, "cirn.moe.edu.tw"),
    ("https://cirn.moe.edu.tw/x.aspx?sid=1219&sid=1220", "round2:offsite", 1, "cirn.moe.edu.tw"),
    ("https://cirn.k12ea.gov.tw/web1240/news", "round2:offsite_unit", 0, "cirn.k12ea.gov.tw"),
    ("https://cirn.k12ea.gov.tw/web1240", "round2:offsite_unit", 0, "cirn.k12ea.gov.tw"),
    ("https://cirn.k12ea.gov.tw/web1241/", "round2:offsite", 1, "cirn.k12ea.gov.tw"),
    ("https://nknuchinese.tw/news/1", "round2:offsite_unit", 0, "nknuchinese.tw"),  # 整站
    ("https://www.nknu-vd.com/works", "round2:offsite_unit", 0, "www.nknu-vd.com"),  # in the site map already
    ("https://2060631web-repository.github.io/nknu-aim-i/about", "round2:offsite_unit", 0,
     "2060631web-repository.github.io"),
    ("https://2060631web-repository.github.io/other/", "round2:offsite", 1, "2060631web-repository.github.io"),
])
def test_a_school_units_site_off_campus_is_round_2_by_its_paths(url, reason, leaf, owner):
    """By its paths and params; a unit round two's batch two crawls now (design 2026-11-17-round2-batch2 §三.1: all
    but the 基本没用 ones) is this round's inside them, tier 本系重要."""
    batch = reason == "round2:offsite_unit" and PLAN.sites[owner].now
    for src in (ACA, LATER, None):
        got = v(url, src)
        want = ("r1", None, leaf, owner, 1) if batch else ("round2", reason, leaf, owner, 2)
        assert (got.verdict, got.reason, got.leaf, got.owner_site, got.tier) == want, src


def test_holds_compares_whole_path_segments_and_parameters():
    unit = round1.parse_site({"site": "groups.google.com", "round": 2, "why": "offsite_unit",
                              "home": "https://groups.google.com/g/nknutc", "paths": ["/g/nknutc"]})
    assert unit.holds("https://groups.google.com/g/nknutc") and unit.holds("https://groups.google.com/g/nknutc/c/x")
    assert not unit.holds("https://groups.google.com/g/nknutc2") and not unit.holds("https://groups.google.com/g")
    page = round1.parse_site({"site": "www.ted.com", "round": 2, "why": "offsite_unit", "leaf": 1,
                              "home": "https://www.ted.com/tedx/events/53158", "paths": ["/tedx/events/53158"]})
    assert page.holds("https://www.ted.com/tedx/events/53158/") and not page.holds("https://www.ted.com/tedx/events/53158/x")
    sid = round1.parse_site({"site": "cirn.moe.edu.tw", "round": 2, "why": "offsite_unit", "params": {"SID": "1219"},
                             "home": "https://cirn.moe.edu.tw/WebContent/index.aspx?sid=1219"})
    assert sid.params == (("sid", "1219"),)
    assert sid.holds("https://cirn.moe.edu.tw/a.aspx?Sid=1219") and not sid.holds("https://cirn.moe.edu.tw/a.aspx")
    whole = round1.parse_site({"site": "nknuchinese.tw", "round": 2, "why": "offsite_unit",
                               "home": "https://nknuchinese.tw/"})
    assert whole.holds("https://nknuchinese.tw/anything") and whole.seeds == ("https://nknuchinese.tw/",)


@pytest.mark.parametrize("entry", [
    {"why": "offsite_unit", "home": None},  # a round-2 seed needs its address
    {"why": "offsite_unit", "leaf": 1},  # one page only: which one
    {"why": "offsite_unit", "leaf": 1, "paths": ["/tedx/"]},  # a folder, not the page the seed is
    {"why": "offsite_unit", "leaf": 2, "paths": ["/tedx/events/53158"]},
    {"why": "offsite_unit", "params": {"sid": 1219}},  # a value is written as text
    {"why": "elsewhere"},
])
def test_a_round_2_entry_written_wrong_is_refused(entry):
    base = {"site": "www.ted.com", "round": 2, "home": "https://www.ted.com/tedx/events/53158"}
    with pytest.raises(ValueError):
        round1.parse_site({**base, **entry})


def test_merged_keys_are_one_entry_each(tmp_path):
    entry = {"site": "groups.google.com", "round": 2, "why": "offsite_unit", "home": "https://groups.google.com/g/a",
             "paths": ["/g/a"]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"sites": [entry, {**entry, "paths": ["/g/b"]}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="twice"):
        round1.load(path)


# ---------------------------------------------------------------- a page's own scripts and tabs (§4.4, §7.8)


def test_a_school_script_a_round_1_page_loads_is_its_resource():
    got = v("https://c.nknu.edu.tw/aca/js/menu.js?v=3", ACA, "script")
    assert (got.verdict, got.kind0, got.leaf, got.owner_site, got.tier) == ("resource", "script", 1,
                                                                          "c.nknu.edu.tw/aca", 0)
    other = v("https://ee.nknu.edu.tw/js/main.js", page("c.nknu.edu.tw/ccee", 1), "script")  # another school host
    assert (other.verdict, other.owner_site, other.tier) == ("resource", "c.nknu.edu.tw/ccee", 1)
    assert v("https://w3.nknu.edu.tw/js/app.js", ACA, "script").verdict == "resource"  # a round-2 site's script


@pytest.mark.parametrize("url, src", [
    ("https://code.jquery.com/jquery-3.7.1.min.js", ACA),  # a library's site (CDN): never fetched
    ("https://cdnjs.cloudflare.com/ajax/libs/x/1/x.js", ACA),
    ("https://c.nknu.edu.tw/aca/js/menu.js", LATER),  # a round-2 page's script
    ("https://c.nknu.edu.tw/aca/js/menu.js", None),
    ("https://c.nknu.edu.tw/aca/WebResource.axd?d=abc", ACA),  # not a .js
    ("https://cc.nknu.edu.tw/moodle/lib/x.js", ACA),  # a system's script: never fetched, a script or not
])
def test_scripts_nobody_fetches(url, src):
    got = v(url, src, "script")
    assert (got.verdict, got.row) == ("out", False)
    assert got.reason in ("out:media", "out:system"), got.reason
    assert v("https://cc.nknu.edu.tw/moodle/lib/x.js", ACA, "script").reason == "out:system"


def test_a_script_a_page_only_links_to_is_media_as_before():
    got = v("https://c.nknu.edu.tw/aca/js/menu.js", ACA)  # <a href="menu.js">
    assert (got.verdict, got.reason) == ("out", "out:media")


def test_a_tab_pressed_by_its_platform_rule_is_a_resource_of_its_page():
    # 国际处 (oia): load_content.php?sub_id=N is the tab's content, counted on the page, never a round-1 page itself
    oia = page("oia.nknu.edu.tw", 0)
    got = v("https://oia.nknu.edu.tw/load_content.php?sub_id=16", oia, "click")
    assert (got.verdict, got.kind0, got.leaf, got.owner_site, got.reason) == ("resource", "click", 1,
                                                                            "oia.nknu.edu.tw", None)
    for src in (LATER, None):  # a tab pressed with no round-1 page behind it is a crawler bug
        with pytest.raises(ValueError):
            v("https://oia.nknu.edu.tw/load_content.php?sub_id=16", src, "click")


def test_a_google_sites_embed_box_is_an_embed():
    got = v("https://abcd-atari-embeds.googleusercontent.com/embeds/16cb/x.html", page("sites.google.com/x", 1),
            "frame")
    assert (got.verdict, got.kind0, got.leaf) == ("resource", "embed", 1)
    form = v("https://docs.google.com/forms/d/e/1FAIpQLSf/viewform?embedded=true", ACA, "frame")
    assert form.kind0 == "form"


def test_a_seed_that_redirects_to_another_site_brings_that_site_into_round_1():
    plan = round1.load()
    assert round1.verdict(plan, "https://www.lis.nknu.edu.tw/", None).verdict == "round2"
    plan.adopt("https://www.lis.nknu.edu.tw/", "lis.nknu.edu.tw")  # the library moved: same important site
    got = round1.verdict(plan, "https://www.lis.nknu.edu.tw/news", None)
    assert (got.verdict, got.owner_site, got.tier) == ("r1", "lis.nknu.edu.tw", 0)
    plan.adopt("https://w3.nknu.edu.tw/", "c.nknu.edu.tw/aca")  # never a JavaScript site (owner, 10-25 15:33)
    assert round1.verdict(plan, "https://w3.nknu.edu.tw/", None).owner_site == "w3.nknu.edu.tw"  # its own (11-17: now)
    assert round1.verdict(PLAN, "https://www.lis.nknu.edu.tw/news", None).verdict == "round2"  # one plan only
    for host in ("https://github.com/nknu", "https://www.edu.tw/x", "https://www.google.com/"):  # never a whole host
        assert not plan.adopt(host, "c.nknu.edu.tw/aca")
    assert plan.adopt("https://sites.google.com/view/nknu-aca/home", "c.nknu.edu.tw/aca")  # one Google Site only
    assert round1.verdict(plan, "https://sites.google.com/view/other", None).reason == "round2:offsite"


def test_a_seed_that_redirects_off_campus_brings_in_only_the_same_units_site():
    # the 10-24 crawler adopted only what judge() let in (crawler.py: school hosts); off campus, only a redirect that
    # keeps the site (scope.redirect_keeps_site) or one site on a shared host. Anything else a person looks at.
    plan, aca = round1.load(), "c.nknu.edu.tw/aca"
    for elsewhere in ("https://www.vendor-example.com/", "https://www.facebook.com/x", "https://parked-example.net/"):
        assert not plan.adopt(elsewhere, aca), elsewhere
        assert round1.verdict(plan, elsewhere, None).verdict != "r1"
    assert plan.adopt("https://www.unit-example.org/", aca, seed="https://unit-example.org/")  # with www.
    assert plan.adopt("https://unit2-example.org/home", aca, seed="https://reurl.cc/AbCd")  # where a short link points
    assert plan.adopt("https://nknuaca.wixsite.com/aca", aca)  # one Wix site on a shared host
    for moved in ("https://www.unit-example.org/news", "https://unit2-example.org/x", "https://nknuaca.wixsite.com/aca/n"):
        got = round1.verdict(plan, moved, None)
        assert (got.verdict, got.owner_site, got.tier) == ("r1", aca, 0), moved
    assert not plan.adopt("https://www.unit-example.org/", aca, seed="https://c.nknu.edu.tw/aca/")  # adopted once


# ---------------------------------------------------------------- §4.2: extra seeds and the login re-check


@pytest.mark.parametrize("url", [
    "https://sso.nknu.edu.tw/recruit/index.aspx", "https://sso.nknu.edu.tw/Recruit/Index.aspx?classtype=M",
    "https://sso.nknu.edu.tw/recruit/index.aspx?classtype=T", "http://sso.nknu.edu.tw/recruit/index.aspx",
    "https://sso.nknu.edu.tw/staff/courseenrollinfo/courseenrollinfo.aspx",
    "https://sso.nknu.edu.tw/Unit/Info/ShowMeetingFiles.aspx?cMeetingType=19",
    "https://sso.nknu.edu.tw/StudentProfile/stud_mails.aspx",
])
def test_the_pages_the_owner_opened_are_extra_round_1_seeds(url):
    got = v(url)
    assert (got.verdict, got.tier, got.leaf, got.owner_site) == ("r1", 1, 0, scope.site_key(scope.canonical(url)))


def test_extra_seeds_are_those_pages_only():
    assert len(PLAN.extra) == 7 and all(scope.canonical(e) == e and v(e).verdict == "r1" for e in PLAN.extra)
    other = v("https://sso.nknu.edu.tw/staff/courseenrollinfo/other.aspx")
    assert other.verdict == "out"  # still behind the /staff login prefix
    assert v("https://sso.nknu.edu.tw/recruit/front.aspx").verdict == "out"
    seed = page("sso.nknu.edu.tw/recruit", 1)
    assert v("https://sso.nknu.edu.tw/upload/recruit/brochure.pdf", seed).verdict == "resource"
    assert v("https://sso.nknu.edu.tw/recruit/files/brochure.pdf", seed).reason == "out:login"  # under /recruit
    assert v("https://sso.nknu.edu.tw/recruit/statistics/a.aspx", seed).reason == "round2:other_site"


def test_the_login_list_to_check_once_is_the_16_urls_inside_round_1_sites():
    checks = PLAN.login_checks
    assert len(checks) == 16 and len(set(checks)) == 16
    for url in checks:
        assert round1.verdict(PLAN, url, None).verdict == "r1", url
        assert scope.judge(url).reason == "login", url  # only the crawler's own check opens them
    # ksped is round one whole since round two's batch two (design 2026-11-17-round2-batch2 §三.1): its admin page too
    assert "https://ksped.nknu.edu.tw/admin_login.aspx" in checks
    assert "https://ksped.nknu.edu.tw/resource/admin_login.aspx" in checks


# ---------------------------------------------------------------- §4.5: the rules, one example either way


@pytest.mark.parametrize("url, reason", [
    # record 三·1: robots.txt is no longer obeyed; what it used to block is written out
    ("https://w3.nknu.edu.tw/admin.html", "in_scope"),  # the directory of administrative units
    ("https://c.nknu.edu.tw/aca/administration.aspx", "in_scope"),
    ("https://fine-arts.nknu.edu.tw/admin/", "login"),
    ("https://fine-arts.nknu.edu.tw/admin", "login"),
    ("https://fine-arts.nknu.edu.tw/admin/index.php?x=1", "login"),
    ("https://vd.nknu.edu.tw/wp-admin/", "login"),
    ("https://vd.nknu.edu.tw/wp-admin/admin-ajax.php", "login"),
    ("https://c.nknu.edu.tw/tg/Admin_Student.aspx", "login"),
    ("https://c.nknu.edu.tw/tg/admin_news.aspx", "login"),
    ("https://c.nknu.edu.tw/aca/admin_news.aspx", "in_scope"),  # /tg/ only, as the record lists
    ("https://hrkm.nknu.edu.tw/admin/file/1071課表.pdf", "in_scope"),  # a file kept in an admin folder is content
    ("https://vpn.nknu.edu.tw/", "login"),
    ("https://school.bot.com.tw/newTwbank/index.aspx", "login"),
    ("https://sites.google.com/mail.nknu.edu.tw/nknumath/_/rsrc/123/home/a", "system"),
    ("https://sites.google.com/_/SitesUi/data/batchexecute", "system"),
    ("https://sites.google.com/feeds/content/mail.nknu.edu.tw/nknumath", "system"),
    ("https://sites.google.com/mail.nknu.edu.tw/nknumath/feeds-and-news", "in_scope"),
    ("https://sites.google.com/mail.nknu.edu.tw/nknumath/home", "in_scope"),
    # 三·4: the rule for things published by mistake is gone; the leave page stays a login page
    ("https://sso.nknu.edu.tw/StudentProfile/stud_mails.aspx", "in_scope"),
    ("https://sso.nknu.edu.tw/studentprofile/other.aspx", "login"),
    ("https://sso.nknu.edu.tw/staff/staffleavestate.aspx", "login"),
    # 三·5: what the owner opened
    ("https://sso.nknu.edu.tw/recruit/index.aspx", "in_scope"),
    ("http://sso.nknu.edu.tw/recruit/index.aspx?classtype=M", "in_scope"),
    ("https://sso.nknu.edu.tw/recruit/index.aspx?classtype=T", "in_scope"),
    ("https://sso.nknu.edu.tw/Recruit/front.aspx?RegisterAction=K", "login"),
    ("https://sso.nknu.edu.tw/recruit/statistics/x.aspx", "in_scope"),
    ("https://sso.nknu.edu.tw/staff/courseenrollinfo/courseenrollinfo.aspx", "in_scope"),
    ("https://sso.nknu.edu.tw/staff/other.aspx", "login"),
    ("https://sso.nknu.edu.tw/ExpertiseDefault.aspx", "in_scope"),
    ("https://sso.nknu.edu.tw/unit/info/showmeetingfiles.aspx?cmeetingtype=19", "in_scope"),
    ("https://sso.nknu.edu.tw/Unit/Info/ShowMeetingFiles.aspx?cMeetingType=19&page=2", "in_scope"),
    ("https://sso.nknu.edu.tw/unit/info/showmeetingfiles.aspx?cmeetingtype=4", "login"),
    ("https://sso.nknu.edu.tw/unit/info/showmeetingfiles.aspx?cmeetingtype=8", "login"),
    ("https://sso.nknu.edu.tw/unit/info/showmeetingfiles.aspx?cmeetingtype=9", "login"),
    ("https://sso.nknu.edu.tw/unit/info/showmeetingfiles.aspx?cmeetingtype=20", "login"),  # not tested by the owner
    ("https://sso.nknu.edu.tw/unit/info/showmeetingfiles.aspx", "login"),
    ("https://sso.nknu.edu.tw/unit/info/showmeetingfiles.aspx?cmeetingtype=19&cmeetingtype=4", "login"),
    # 三·13: logins as before
    ("https://c.nknu.edu.tw/aca/admin_login.aspx", "login"),
    ("https://sso.nknu.edu.tw/userlogin/login.aspx?curl=/default.aspx", "login"),
    # 三·14: platforms that refuse programs, apart from social media so that a report can count them
    ("https://linktr.ee/nknusa", "refuses"),
    ("https://www.dcard.tw/f/nknu", "refuses"),
    ("https://medium.com/those-as-art", "refuses"),
    ("http://site-1601611-3674-3901.strikingly.com/", "refuses"),
    # 三·15: social platforms as before
    ("https://www.facebook.com/nknu", "social"),
    ("https://www.instagram.com/nknu/", "social"),
    ("https://linktree.example.com/", "offsite"),
    # 三·16: Moodle and the learning platforms' hosts
    ("https://cc.nknu.edu.tw/moodle/course/view.php?id=3", "system"),
    ("https://cc.nknu.edu.tw/", "system"),
    ("https://moocs.nknu.edu.tw/local/x.php", "system"),
    ("https://hp3.nknu.edu.tw/index.php", "system"),
    ("https://ccc.nknu.edu.tw/", "in_scope"),
    # 三·19: the library's catalogue host and its off-campus proxy are systems, never retried
    ("https://nknulib.nknu.edu.tw/", "system"),
    ("https://nknulib.nknu.edu.tw/search~S1*cht?/tX/tx/1,1,1,B/frameset&FF=x", "system"),
    ("https://www-airitilibrary-com.rpa.lib.nknu.edu.tw/Article/Detail?DocID=1", "system"),
    ("https://a.b.rpa.lib.nknu.edu.tw/x", "system"),
    ("https://rpa.lib.nknu.edu.tw/", "in_scope"),  # the proxy's own name is not one of its sites
    ("https://lib.nknu.edu.tw/", "in_scope"),
    ("https://xrpa.lib.nknu.edu.tw/x", "in_scope"),  # a whole label only
    # 三·17: traps, search, thumbnails, internal services, the library's catalogue
    ("https://140.127.40.135/WebService.asmx", "internal_service"),
    ("https://140.127.40.135/", "internal_service"),
    ("http://140.127.40.119:8080/x", "internal_service"),
    ("https://140.127.40.1/x", "in_scope"),
    ("https://pbws.nknu.edu.tw/", "internal_service"),
    ("https://c.nknu.edu.tw/aca/http://www.moe.gov.tw/x", "trap"),
    ("https://c.nknu.edu.tw/aca/https:/www.moe.gov.tw/x", "trap"),
    ("https://c.nknu.edu.tw/aca/http%EF%BC%9A//www.moe.gov.tw/x", "trap"),
    ("https://c.nknu.edu.tw/aca/http：//www.moe.gov.tw/x", "trap"),
    ("https://c.nknu.edu.tw/aca/https-guide.aspx", "in_scope"),
    ("https://c.nknu.edu.tw/aca/default.aspx?u=http://www.moe.gov.tw/", "in_scope"),  # a parameter, not the path
    ("https://lis.nknu.edu.tw/library/bookquery/x.aspx", "system"),
    ("https://lis.nknu.edu.tw/library/booklist/x.aspx", "in_scope"),
    ("https://geo.nknu.edu.tw/includes/timthumb/timthumb.php?src=a.jpg", "media"),
    # file kinds (§4.5 「其他要改的」)
    ("https://c.nknu.edu.tw/aca/banner.jpg", "in_scope"),  # images are resources now, not media
    ("https://c.nknu.edu.tw/aca/scan.TIF", "in_scope"),
    ("https://c.nknu.edu.tw/aca/clip.mp4", "media"),
    ("https://c.nknu.edu.tw/aca/site.css", "media"),
    ("https://c.nknu.edu.tw/favicon.ico", "media"),
    # Google forms are read once (record 三·8); answering one never
    ("https://docs.google.com/forms/d/e/1FAIpQLSf/viewform?usp=sf_link", "form"),
    ("https://docs.google.com/forms/d/1AbC/viewform", "form"),
    ("https://docs.google.com/forms/u/0/d/e/1FAIpQLSf/viewform", "form"),
    ("https://docs.google.com/forms/d/e/1FAIpQLSf/closedform", "form"),
    ("https://docs.google.com/forms/d/e/1FAIpQLSfXYZ", "form"),  # Google redirects these to viewform
    ("https://docs.google.com/forms/d/1AbCdEf", "form"),
    ("https://docs.google.com/forms/d/e/1FAIpQLSf/viewform/", "form"),
    ("https://docs.google.com/forms/d/e/1FAIpQLSf/viewform?embedded=true", "form"),
    ("https://docs.google.com/forms/d/e/1FAIpQLSf/formResponse", "never_host"),
    ("https://docs.google.com/forms/d/e/1FAIpQLSf/formResponse/", "never_host"),
    ("https://docs.google.com/forms/d/1AbC/edit", "never_host"),
    ("https://docs.google.com/forms/u/0/", "never_host"),
    ("https://docs.google.com/document/d/1AbC/edit", "never_host"),
])
def test_judge_rules_of_the_round_1_record(url, reason):
    assert scope.judge(scope.canonical(url) or url).reason == reason


@pytest.mark.parametrize("url", [
    # back offices whose directory is not named exactly admin (record 三·13 「各站后台」; review 10-26): the 10-24
    # crawler fetched these three, titled 後台管理系統, 管理系統 and 後台
    "https://oia.nknu.edu.tw/pintech_admin/index.php",
    "https://oia.nknu.edu.tw/pintech_admin/",
    "https://english.nknu.edu.tw/managerx/index.php",
    "https://english.nknu.edu.tw/managerx/",
    "https://english.nknu.edu.tw/tw/conference/managerx/index.php",
    "https://lis.nknu.edu.tw/administrator/index.php",  # Joomla's
    "https://x.nknu.edu.tw/site-admin/login",
    "https://x.nknu.edu.tw/admin_panel/news.php",
])
def test_back_offices_by_any_name_are_logins_and_so_are_their_scripts(url):
    got = v(url, page("oia.nknu.edu.tw", 0))
    assert (got.verdict, got.reason, got.row) == ("out", "out:login", False)
    js = url[:url.rindex("/") + 1] + "assets/app.js"  # a script in the back office's folder
    assert v(js, page("oia.nknu.edu.tw", 0), "script").reason == "out:login", js


@pytest.mark.parametrize("url, reason", [
    # content whose name merely holds the word (the old archive's links and pages, read 10-26)
    ("https://w3.nknu.edu.tw/admin.html", "in_scope"),
    ("https://w3.nknu.edu.tw/management.html", "in_scope"),
    ("http://www.nknu.edu.tw/~badminton/", "in_scope"),
    ("https://lis.nknu.edu.tw/lightsrv/i-manage", "in_scope"),
    ("https://lis.nknu.edu.tw/lightsrv/i-management/borrowing", "in_scope"),
    ("https://sso.nknu.edu.tw/news/subjectlist.aspx?cat=SCHOOLADMIN", "in_scope"),
    ("https://elec.nknu.edu.tw/upload/ckfinder/files/109_ind_app_admin_interviewtime_1090414.pdf", "in_scope"),
    ("https://oia.nknu.edu.tw/pintech_admin/uploads/rule.pdf", "in_scope"),  # a file kept there is content
    ("https://x.nknu.edu.tw/fileadmin/a.pdf", "in_scope"),
    ("https://x.nknu.edu.tw/social-policy-administration/", "in_scope"),
])
def test_content_named_like_a_back_office_is_still_fetched(url, reason):
    assert scope.judge(scope.canonical(url) or url).reason == reason


@pytest.mark.parametrize("url", [
    # what makes a captcha (CLAUDE.md: 验证码不碰; review 10-26): its picture, its image service, Google's and others'
    "https://c.nknu.edu.tw/aca/CheckCode.aspx?t=1",
    "https://c.nknu.edu.tw/affair/VerifyCode.ashx",
    "https://c.nknu.edu.tw/aca/ValidateCode.aspx",
    "https://c.nknu.edu.tw/aca/captcha.aspx",
    "https://c.nknu.edu.tw/reality/ImgCode.aspx?r=0.5",
    "https://x.nknu.edu.tw/inc/ValidCode.php",
    "https://x.nknu.edu.tw/inc/chkcode.asp",
    "https://x.nknu.edu.tw/RandCode.aspx",
    "https://x.nknu.edu.tw/SecurityCode.ashx",
    "https://x.nknu.edu.tw/authimg.php",
    "https://x.nknu.edu.tw/vcode.php",
    "https://x.nknu.edu.tw/include/code.php",
    "https://oia.nknu.edu.tw/api/captcha64_generate.php",
    "https://www.google.com/recaptcha/api2/anchor?k=x",
    "https://www.google.com/recaptcha/api/fallback?k=x",
    "https://www.recaptcha.net/recaptcha/api.js",
    "https://hcaptcha.com/1/api.js",
    "https://sso.nknu.edu.tw/WebServices/ReCaptcha/SetTokenAndVerify.aspx",
])
def test_captchas_are_never_fetched_as_a_link_an_image_or_a_frame(url):
    for how in ("link", "image", "frame"):
        got = v(url, ACA, how)
        assert (got.verdict, got.reason, got.row) == ("out", "out:login", False), (url, how)
    assert scope.judge(scope.canonical(url) or url).reason in scope.HARD_OUT


def test_a_captcha_picture_in_the_body_makes_no_row():
    from nknu.archive import links

    html = """<form id="form1"><div id="content"><p>聯絡我們</p><img src="CheckCode.aspx?t=8">
      <img src="/aca/uploadfile/map.jpg"><input type="submit" name="btnReGenCode" value="換一張"></div></form>"""
    url = "https://c.nknu.edu.tw/affair/contact.aspx"
    got, need = {}, {}
    for img in links.extract(html, url).images:
        target = scope.canonical(img.src, url)
        got[target] = v(target, page("c.nknu.edu.tw/affair", 0), "image")
        need[target] = links.image_need(img, url)
    captcha = "https://c.nknu.edu.tw/affair/checkcode.aspx?t=8"
    assert (got[captcha].reason, got[captcha].row) == ("out:login", False)
    # never content, so never a picture the page lacks: the old archive's elec pages carry one in every page's login
    # box (authimg.html, 1,600 times in the body zone); counted, they would all read 完整（学校那边缺）
    assert need[captcha] == ("no", "captcha") and "captcha" in links.IMAGE_WHY
    assert got["https://c.nknu.edu.tw/aca/uploadfile/map.jpg"].row  # the page's own picture is fetched
    assert need["https://c.nknu.edu.tw/aca/uploadfile/map.jpg"] == ("must", "")


@pytest.mark.parametrize("url", [
    "https://c.nknu.edu.tw/aca/barcode.aspx", "https://x.nknu.edu.tw/qrcode.png", "https://x.nknu.edu.tw/devcode/",
    "https://x.nknu.edu.tw/zipcode.php", "https://x.nknu.edu.tw/encode.php", "https://x.nknu.edu.tw/codes/a.html",
])
def test_words_that_only_look_like_a_captchas_are_content(url):
    assert scope.judge(scope.canonical(url) or url).ok, url


def test_the_librarys_catalogue_and_proxy_make_no_row():
    # record 三·19: judged system, so round1.place() builds no url row (round two's retry cannot bring them back)
    for url in ("https://nknulib.nknu.edu.tw/", "https://www-ebsco-com.rpa.lib.nknu.edu.tw/x"):
        got = v(url, ACA)
        assert (got.verdict, got.reason, got.row) == ("out", "out:system", False), url


def test_refuses_and_internal_ips_are_hard_out():
    assert {"refuses", "internal_service", "login", "system"} <= scope.HARD_OUT
    assert scope.SOFT_OUT == frozenset({"offsite", "google_other"})
    assert not scope.SOCIAL.search("linktr.ee") and not scope.SOCIAL.search("www.dcard.tw")


def test_wix_lightbox_popups_are_one_page():
    assert scope.canonical("https://ocsnknu.wixsite.com/index?lightbox=dataItem-abc") == "https://ocsnknu.wixsite.com/index"
    assert scope.canonical("https://ocsnknu.wixsite.com/index?LightBox=x&a=1") == "https://ocsnknu.wixsite.com/index?a=1"


@pytest.mark.parametrize("href, target", [
    ("https://www.google.com/url?q=https%3A%2F%2Fc.nknu.edu.tw%2Faca%2FDefault.aspx&sa=D&sntz=1&usg=AOv",
     "https://c.nknu.edu.tw/aca/default.aspx"),
    ("http://www.google.com/url?q=http%3A%2F%2Fwww.nknu.edu.tw%2F%7Egeo%2F&sa=D", "http://www.nknu.edu.tw/~geo/"),
    ("https://google.com/url?url=https://lis.nknu.edu.tw/open-time&sa=t", "https://lis.nknu.edu.tw/open-time"),
    ("https://www.google.com/url?sa=D&q=https%3A%2F%2Fwww.facebook.com%2Fnknu", "https://www.facebook.com/nknu"),
    ("https://www.google.com/url?q=mailto%3Ax%40nknu.edu.tw", "https://www.google.com/url?q=mailto%3Ax%40nknu.edu.tw"),
    ("https://www.google.com/search?q=https%3A%2F%2Fx.nknu.edu.tw%2F",
     "https://www.google.com/search?q=https%3A%2F%2Fx.nknu.edu.tw%2F"),  # a search, not a redirect
    (("https://www.google.com/url?q=https%3A%2F%2Fsso.nknu.edu.tw%2FNews%2FExternalLink.aspx%3Furl%3Dhttps%253a%252f"
      "%252fc.nknu.edu.tw%252faca%252f"), "https://c.nknu.edu.tw/aca/"),  # one wrapper inside the other
])
def test_google_redirects_are_unwrapped_without_a_request(href, target):
    assert scope.canonical(href, "https://sites.google.com/mail.nknu.edu.tw/nknumath/home") == target


@pytest.mark.parametrize("url, kind", [
    ("https://c.nknu.edu.tw/aca/scan.tif", "file"),
    ("https://c.nknu.edu.tw/aca/scan.TIFF", "file"),
    ("https://c.nknu.edu.tw/aca/a.pdf", "file"),
    ("https://c.nknu.edu.tw/aca/a.JPG", "image"),
    ("https://c.nknu.edu.tw/aca/a.webp", "image"),
    ("https://c.nknu.edu.tw/aca/a.svg", "image"),
    ("https://c.nknu.edu.tw/aca/a.heic", "image"),
    ("https://sso.nknu.edu.tw/WebServices/ajaxDownloadFile.aspx?caes=x", "file"),
    ("https://sso.nknu.edu.tw/webservices/ajaxDownloadFileCourse.aspx?caes=x", "file"),
    ("https://sso.nknu.edu.tw/webservices/ajaxFile.aspx?caes=x", "file"),
    ("https://sso.nknu.edu.tw/webservices/ajaxImageReader.aspx?caes=x", "image"),
    ("https://sso.nknu.edu.tw/News/AttachedFile.aspx?cEncrptedFilePath=x", "file"),
    ("https://c.nknu.edu.tw/aca/default.aspx", "page"),
])
def test_kinds_by_url(url, kind):
    assert scope.kind_of(scope.canonical(url)) == kind


# ---------------------------------------------------------------- off-campus attachments, round one (record 五·15)


@pytest.mark.parametrize("url", [
    "https://www.edu.tw/files/x.pdf", "https://www.moe.gov.tw/News/Upload/計畫.docx", "https://example.com/a/b.ODT",
    "https://www.example.org/forms/apply.xlsx", "https://depart.moe.edu.tw/x/scan.tif",
    "https://example.com/admin/file/rule.pdf",  # a file in an admin folder is content, off campus too
])
def test_an_attachment_a_round_1_page_links_off_campus_is_fetched_now(url):
    """Owner 10-26 (record 五·15, 「抓吧」): the file a round-1 page links straight to is fetched in round one, that
    file only (leaf=1: never looked into); it counts on the page that links it. Its server is a queue of its own (the
    crawler queues by server: crawler.py)."""
    got = v(url, ACA)
    assert (got.verdict, got.reason, got.owner_site, got.tier, got.leaf, got.kind0, got.state) == (
        "resource", None, "c.nknu.edu.tw/aca", 0, 1, "file", "pending")
    dept = v(url, page("c.nknu.edu.tw/ccee", 1))
    assert (dept.owner_site, dept.tier) == ("c.nknu.edu.tw/ccee", 1)


def test_an_off_campus_attachment_only_a_round_2_page_or_nobody_links_is_still_round_2():
    for src in (LATER, None):
        got = v("https://www.edu.tw/files/x.pdf", src)
        assert (got.verdict, got.reason, got.leaf, got.state) == ("round2", "round2:offsite_file", 1, "later")


def test_off_campus_pages_and_frames_a_round_1_page_links_are_still_round_2():
    """Only attachments come forward (record 五·15: 「只限附件」): a web page off campus, an off-campus frame (Canva) and
    a download address whose URL does not say it is a file stay one layer out, in round two."""
    for url, how in (("https://www.moe.gov.tw/news.aspx", "link"), ("https://www.canva.com/design/DAF/view", "frame"),
                     ("https://www.edu.tw/download.php?id=3", "link")):
        got = v(url, ACA, how)
        assert (got.verdict, got.reason, got.leaf) == ("round2", "round2:offsite", 1), url


def test_an_off_campus_attachment_behind_a_rule_stays_out():
    for url, reason in (("https://www.facebook.com/x/a.pdf", "out:social"), ("https://example.com/login/a.pdf",
                                                                             "out:login")):
        got = v(url, ACA)
        assert (got.verdict, got.reason, got.row) == ("out", reason, False), url


# ---------------------------------------------------------------- gap ⑫ of 11-08: a unit's own site off campus, framed


GENDER = page("gender.nknu.edu.tw", 1)  # 性别所, 本系重要
WIX_PAGE = "https://nknugender.wixsite.com/nknugender/recruit"  # the list's offsite_unit entry (round two, whole site)


def test_a_round_1_page_framing_its_units_own_site_off_campus_fetches_that_page_now():
    """性别所's pages frame the Wix pages of the unit's own site (the list's offsite_unit), where their content is: a
    frame of one is the page's part, fetched now, one layer (leaf 1: what it links stays round two's). A link to it,
    a frame the page hides, another Wix site and a round-2 page's frame are as before."""
    framed = v(WIX_PAGE, GENDER, "frame")
    assert (framed.verdict, framed.reason, framed.owner_site, framed.tier, framed.leaf, framed.kind0) == (
        "resource", None, "gender.nknu.edu.tw", 1, 1, "page")
    linked = v(WIX_PAGE, GENDER)  # a link to it: the unit's own site, crawled now by round two's batch two
    assert (linked.verdict, linked.leaf, linked.owner_site, linked.tier) == ("r1", 0,
                                                                             "nknugender.wixsite.com/nknugender", 1)
    assert v(WIX_PAGE, GENDER, "hidden").reason == "out:not_content"
    other = v("https://someone.wixsite.com/site/a", GENDER, "frame")  # no unit's site of the list
    assert (other.reason, other.leaf, other.owner_site) == ("round2:offsite", 1, "someone.wixsite.com/site")
    assert v(WIX_PAGE, LATER, "frame").verdict == "r1"  # a round-2 page's frame: the batch's unit site
    canva = v("https://www.canva.com/design/DAF/view?embed", GENDER, "frame")
    assert (canva.verdict, canva.reason) == ("round2", "round2:offsite")


def test_a_units_page_framed_in_frames_nothing_more_in():
    """One layer (the spec: 抓一层…不再往下跟; review of 11-09): the Wix page a round-1 page frames is a part fetched
    with that page, off campus; what it frames in turn, of the unit's own site or of another unit's, stays round two's
    as any off-campus frame does, however many layers deep. Its pictures are still its parts."""
    framed = {"owner_site": "gender.nknu.edu.tw", "tier": 1, "url": scope.canonical(WIX_PAGE),
              "site": "nknugender.wixsite.com/nknugender"}
    for url in ("https://nknugender.wixsite.com/nknugender/blank-1", "https://ocsnknu.wixsite.com/index/about"):
        deeper = v(url, framed, "frame")
        assert (deeper.verdict, deeper.reason, deeper.tier, deeper.leaf) == ("round2", "round2:offsite", 2, 1), url
    pic = v("https://static.wixstatic.com/media/abc.jpg", framed, "image")
    assert (pic.verdict, pic.owner_site, pic.tier) == ("resource", "gender.nknu.edu.tw", 1)
    # framed by a school page (its url on a school host, as every round-1 site's is): the one layer
    school = {"owner_site": "gender.nknu.edu.tw", "tier": 1, "url": "https://gender.nknu.edu.tw/p/a.html",
              "site": "gender.nknu.edu.tw"}
    assert v(WIX_PAGE, school, "frame").verdict == "resource"


def test_a_framed_page_outside_a_units_paths_is_no_part_of_its_site():
    unit = round1.parse_site({"site": "unit.wixsite.com/unit", "round": 2, "why": "offsite_unit",
                              "home": "https://unit.wixsite.com/unit/news", "paths": ["/unit/news"]})
    plan = round1.Plan({unit.site: unit}, (), (), "test")
    inside = round1.place(plan, "https://unit.wixsite.com/unit/news/a", GENDER, "frame")
    assert (inside.verdict, inside.owner_site, inside.leaf) == ("resource", "gender.nknu.edu.tw", 1)
    outside = round1.place(plan, "https://unit.wixsite.com/unit/shop", GENDER, "frame")
    assert (outside.verdict, outside.reason, outside.leaf) == ("round2", "round2:offsite", 1)
