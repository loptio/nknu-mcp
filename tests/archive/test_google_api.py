"""Google Drive, Docs, Calendar and Forms (round-1 design §7.6, §4.4, §16 test_google_api.py; record 三·8; review
10-26 B11, B12): parse_link's kinds, the Google step of round1.admit(), the rows and page_res entries a Drive item, a
folder, a calendar and a form make, the caps, the error reasons, the key and resource keys, and a form's page.

Nothing leaves this machine: the API answers are written here in the shape of Google's documentation (files.get,
files.list, events.list, the error bodies), the one request test goes through httpx.MockTransport, and the key is a
test value of this file. Ids are made up; names in the fixtures are masked with O (CLAUDE.md)."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from nknu.archive import google_api as g
from nknu.archive import links, round1, scope
from nknu.archive.frontier import Frontier
from nknu.crawl.adapter import RateLimit, Target
from nknu.crawl.fetch import Client, FetchError, Snapshot
from nknu.crawl.warc import WarcSink
from tests.archive.plans import plan, site

ACA = "c.nknu.edu.tw/aca"
PLAN = plan(site(ACA, imp="全校重要", name="教务处", home="https://c.nknu.edu.tw/aca/default.aspx"),
            site("c.nknu.edu.tw/ae", rnd="2", why="other_site"))
PAGE = "https://c.nknu.edu.tw/aca/Page.aspx?PN=5"
ROUND2_PAGE = "https://c.nknu.edu.tw/ae/Page.aspx?PN=1"
FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
FOLDER_ID = "1FoLdErIdAbCdEfGhIjKlMnOpQrStUv"
SUB_ID = "1SuBfOlDeRiDaBcDeFgHiJkLmNoPqRs"
CAL = "c_0123456789abcdef@group.calendar.google.com"
CAL2 = "nknuOOO@gmail.com"
HOLIDAY = "zh-tw.taiwan#holiday@group.v.calendar.google.com"
SECRET = "test-key-not-real-0123456789"  # a test value of this file: never anyone's key
PDF = "application/pdf"


def b64(s: str) -> str:
    import base64

    return base64.b64encode(s.encode()).decode().rstrip("=")


# ---------------------------------------------------------------- parse_link: every kind (§16 test_google_api.py)

@pytest.mark.parametrize("url, file_id, key", [
    (f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing", FILE_ID, None),
    (f"https://drive.google.com/file/d/{FILE_ID}/preview", FILE_ID, None),
    (f"https://drive.google.com/file/d/{FILE_ID}/edit", FILE_ID, None),
    (f"https://drive.google.com/file/d/{FILE_ID}", FILE_ID, None),
    (f"https://drive.google.com/file/d/{FILE_ID}/view?resourcekey=0-AbCdEf&usp=sharing", FILE_ID, "0-AbCdEf"),
    (f"https://drive.google.com/file/u/1/d/{FILE_ID}/view?usp=sharing", FILE_ID, None),  # 工设系's /u/1/ (§7.6)
    (f"https://drive.google.com/a/mail.nknu.edu.tw/file/d/{FILE_ID}/view", FILE_ID, None),
    (f"https://drive.google.com/open?id={FILE_ID}", FILE_ID, None),
    (f"https://drive.google.com/uc?id={FILE_ID}&export=download", FILE_ID, None),
    (f"https://drive.google.com/thumbnail?id={FILE_ID}&sz=w1000", FILE_ID, None),
    (f"https://docs.google.com/file/d/{FILE_ID}/preview", FILE_ID, None),  # 总务处's old address (§7.6)
])
def test_drive_file_addresses_give_the_file_and_its_resource_key(url, file_id, key):
    got = g.parse_link(url)
    assert (got.kind, got.file_id, got.resource_key) == ("file", file_id, key)


@pytest.mark.parametrize("url", [
    f"https://drive.google.com/drive/folders/{FOLDER_ID}?usp=sharing",
    f"https://drive.google.com/drive/u/0/folders/{FOLDER_ID}",
    f"https://drive.google.com/drive/mobile/folders/{FOLDER_ID}",
    f"https://drive.google.com/embeddedfolderview?id={FOLDER_ID}",
    f"https://drive.google.com/folderview?id={FOLDER_ID}",
    f"https://docs.google.com/folderview?id={FOLDER_ID}&usp=sharing",  # 538 of them in the old archive (§7.6)
])
def test_folder_addresses(url):
    got = g.parse_link(url)
    assert (got.kind, got.file_id) == ("folder", FOLDER_ID)


@pytest.mark.parametrize("url, doc_type", [
    (f"https://docs.google.com/document/d/{FILE_ID}/edit?usp=sharing&ouid=1&rtpof=true&sd=true", "document"),
    (f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit#gid=0", "spreadsheets"),
    (f"https://docs.google.com/spreadsheets/d/{FILE_ID}/export?format=csv", "spreadsheets"),
    (f"https://docs.google.com/spreadsheets/u/0/d/{FILE_ID}/edit", "spreadsheets"),
    (f"https://docs.google.com/presentation/d/{FILE_ID}/edit?usp=sharing", "presentation"),
    (f"https://docs.google.com/drawings/d/{FILE_ID}/edit", "drawings"),
    (f"https://docs.google.com/a/mail.nknu.edu.tw/document/d/{FILE_ID}/edit", "document"),
    (f"https://docs.google.com/document/d/{FILE_ID}", "document"),
])
def test_native_documents(url, doc_type):
    got = g.parse_link(scope.canonical(url))
    assert (got.kind, got.file_id, got.doc_type) == ("doc", FILE_ID, doc_type)


def test_forms_pages_are_read_once_and_their_other_pages_never():
    fid = "1FAIpQLSdXyZaBcDeFgHiJkL"
    view = g.parse_link(f"https://docs.google.com/forms/d/e/{fid}/viewform?usp=sf_link")
    assert view.kind == "form" and view.form_url == f"https://docs.google.com/forms/d/e/{fid}/viewform?usp=sf_link"
    domain = g.parse_link(f"https://docs.google.com/a/mail.nknu.edu.tw/forms/d/e/{fid}/viewform")
    assert domain.kind == "form" and domain.form_url == f"https://docs.google.com/forms/d/e/{fid}/viewform"
    assert scope.is_google_form(domain.form_url) and not scope.is_google_form(domain.url)
    assert g.parse_link(f"https://docs.google.com/forms/d/e/{fid}/closedform").form_url.endswith("/closedform")
    assert g.parse_link(f"https://docs.google.com/forms/d/e/{fid}").form_url.endswith("/viewform")
    for tail in ("formResponse", "edit", "prefill", "viewanalytics"):
        assert g.parse_link(f"https://docs.google.com/forms/d/{fid}/{tail}").kind == "never"
    assert g.parse_link("https://docs.google.com/forms/u/0/").kind == "not_content"


def test_calendar_embeds_with_three_sources_and_their_encodings():
    # an embed may show several calendars (§7.6): one written as is, one in base64 (Google's newer embed code), and
    # Taiwan's holidays in base64 (c.nknu/ccee's: a link only)
    url = (f"https://www.google.com/calendar/embed?src={CAL.replace('@', '%40')}&src={b64(CAL2)}"
           f"&src={b64(HOLIDAY)}&ctz=Asia%2FTaipei")
    got = g.parse_link(scope.canonical(url))
    assert got.kind == "calendar" and got.calendars == (CAL, CAL2) and got.google_calendars == (HOLIDAY,)
    hosted = g.parse_link(f"https://www.google.com/calendar/hosted/nknu.edu.tw/embed?src={CAL}")
    assert hosted.calendars == (CAL,)
    assert g.parse_link(f"https://calendar.google.com/calendar/u/0/embed?src={CAL}").calendars == (CAL,)
    assert g.parse_link(f"https://calendar.google.com/calendar/ical/{CAL.replace('@', '%40')}/public/basic.ics"
                        ).calendars == (CAL,)
    assert g.parse_link(f"https://calendar.google.com/calendar/u/0?cid={b64(CAL)}").calendars == (CAL,)
    assert g.parse_link(f"https://calendar.google.com/calendar/r?cid={b64(CAL)}").calendars == (CAL,)
    # a holiday calendar written with a raw '#': what canonical leaves of it is still Google's own
    raw = g.parse_link(scope.canonical("https://calendar.google.com/calendar/embed?src=zh-tw.taiwan#holiday@group"))
    assert raw.kind == "calendar" and raw.calendars == () and raw.google_calendars


def test_an_embedded_calendar_longer_than_the_length_limit_is_still_read():
    # iecnknu's: 3 of 12 embeds were over 1,000 characters and too_long before the Google step (§4.5)
    srcs = "".join(f"&src=c_{i:064d}%40group.calendar.google.com&color=%23{i:06d}" for i in range(12))
    url = scope.canonical(f"https://calendar.google.com/calendar/embed?height=600&wkst=1&ctz=Asia%2FTaipei{srcs}")
    assert len(url) > scope.MAX_URL and scope.judge(url).reason == "too_long"
    assert len(g.parse_link(url).calendars) == 12
    adm = round1.admit(PLAN, url, None, page_row_dict(), "frame", google=g.step(PLAN))
    assert adm.step == "google" and adm.placement.kind0 == "calendar" and adm.placement.verdict == "resource"


@pytest.mark.parametrize("url", [
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vTaBcDeFgHiJkLmNoP/pubhtml?gid=0&single=true",
    "https://docs.google.com/spreadsheets/u/1/d/e/2PACX-1vTaBcDeFgHiJkLmNoP/pubhtml",
    "https://docs.google.com/document/d/e/2PACX-1vTaBcDeFgHiJkLmNoP/pub",
    "https://docs.google.com/presentation/d/e/2PACX-1vTaBcDeFgHiJkLmNoP/embed?start=false",
])
def test_documents_published_to_the_web_wait_for_round_two(url):
    assert g.parse_link(url).kind == "published"  # record 五·13: Drive's API cannot read a publish id


@pytest.mark.parametrize("url", [
    "https://docs.google.com/spreadsheets/u/0/", "https://drive.google.com/drive/quota",
    "https://drive.google.com/settings/storage", "https://drive.google.com/", "https://docs.google.com/",
    "https://calendar.google.com/calendar/r", "https://calendar.google.com/",
])
def test_an_apps_own_pages_are_no_content(url):
    assert g.parse_link(url).kind == "not_content"


@pytest.mark.parametrize("url", [
    "https://drive.google.com/viewer?srcid=x", "https://docs.google.com/viewer?url=https://c.nknu.edu.tw/x.pdf",
    f"https://drive.google.com/drive/shared-drives/{FOLDER_ID}", "https://calendar.google.com/calendar/event?eid=abc",
    "https://calendar.google.com/calendar/embed?src=not%20a%20calendar",
    "https://docs.google.com/document/d/short/edit",
])
def test_writings_nobody_has_seen_are_unhandled(url):
    assert g.parse_link(url).kind == "unhandled"


# ---------------------------------------------------------------- the Google step of round1.admit()

def page_row_dict(tier=0, owner=ACA, rid=1, depth=0):
    return {"id": rid, "tier": tier, "owner_site": owner, "depth": depth}


def test_the_google_step_turns_a_drive_address_into_its_metadata_row_before_the_rules():
    step = g.step(PLAN)
    href = f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing"
    adm = round1.admit(PLAN, href, PAGE, page_row_dict(), "link", google=step)
    pl = adm.placement
    assert adm.step == "google" and adm.link == href and adm.url == g.item_url(FILE_ID)
    assert (pl.verdict, pl.owner_site, pl.tier, pl.leaf, pl.kind0) == ("resource", ACA, 0, 1, "drive")
    assert g.queue_for(scope.canonical(adm.url).split("/")[2]) == g.QUEUE  # design §6.1: the googleapis queue
    # a round-two page's Drive link waits for round two, one layer out
    later = round1.admit(PLAN, href, ROUND2_PAGE, page_row_dict(tier=2, owner="c.nknu.edu.tw/ae"), "link",
                         google=step).placement
    assert (later.verdict, later.reason, later.state) == ("round2", "round2:offsite", "later")


def test_the_google_step_leaves_forms_to_the_rules_and_never_fetches_their_other_pages():
    step, fid = g.step(PLAN), "1FAIpQLSdXyZaBcDeFgHiJkL"
    plain = round1.admit(PLAN, f"https://docs.google.com/forms/d/e/{fid}/viewform", PAGE, page_row_dict(), "link",
                         google=step)
    assert plain.step == "place" and plain.placement.kind0 == "form" and plain.placement.verdict == "resource"
    domain = round1.admit(PLAN, f"https://docs.google.com/a/mail.nknu.edu.tw/forms/d/e/{fid}/viewform", PAGE,
                          page_row_dict(), "link", google=step)
    assert domain.step == "google" and domain.url == f"https://docs.google.com/forms/d/e/{fid}/viewform"
    assert domain.placement.kind0 == "form" and domain.placement.leaf == 1  # one GET, never followed
    answer = round1.admit(PLAN, f"https://docs.google.com/forms/d/e/{fid}/formResponse", PAGE, page_row_dict(),
                          "link", google=step)
    assert answer.placement.reason == "out:never_host"  # never answered (record 三·8)


def test_the_google_step_on_what_it_cannot_read():
    step = g.step(PLAN)
    odd = round1.admit(PLAN, "https://drive.google.com/viewer?srcid=x", PAGE, page_row_dict(), "frame", google=step)
    assert (odd.placement.verdict, odd.placement.reason, odd.placement.row) == ("unhandled", "unhandled:google",
                                                                               False)
    pub = round1.admit(PLAN, "https://docs.google.com/spreadsheets/d/e/2PACX-1vTaBcD/pubhtml", PAGE, page_row_dict(),
                       "frame", google=step)
    assert pub.placement.reason == "round2:google_published" and not pub.placement.row
    home = round1.admit(PLAN, "https://docs.google.com/spreadsheets/u/0/", PAGE, page_row_dict(), "link",
                        google=step)
    assert home.placement.reason == "out:not_content"


# ---------------------------------------------------------------- a frontier, a page, and the API's answers

@pytest.fixture
def f(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    yield fr
    fr.close()


def add_page(f, url=PAGE, tier=0, owner=ACA):
    f.add(url, host=scope.canonical(url).split("/")[2], site=scope.site_key(url), unit=None, tier=tier, kind="page",
          depth=0, found_via="seed", found_from=None, owner_site=owner, kind0="page")
    row = f.row(url)
    f.finish(row["id"], state="done", status=200, sniff="html", page_type="t:ok")
    f.x("INSERT OR IGNORE INTO page_type(key, site, verdict) VALUES ('t:ok', ?, 'ok')", (owner,))
    return f.row(url)


def use(f, page, href, how="frame", zone="body", text=None):
    """What the crawler's _parts does with one Google address once wired (the integration step): every Use made, its
    row added, the entries written on the page, then resync."""
    add = g.frontier_adder(f)
    entries = []
    for u in g.uses(PLAN, scope.canonical(href), page, how):
        rid = add(u.url, u.placement, page, "link")[0] if u.url else None
        entries += g.attach(f, u, rid, zone=zone, text=text)
    f.add_page_res(page["id"], entries)
    g.resync(f, page["id"])
    return entries


def snap_of(url, data, status=200):
    body = json.dumps(data).encode()
    return Snapshot(Target(url), url, status, {"content-type": "application/json; charset=UTF-8"}, body,
                    body.decode(), datetime.now(UTC), hashlib.sha256(body).hexdigest())


def answer(f, url, data):
    """The crawler stores a 200 JSON answer as any (kind file, sniff json), then hands it to google_api.read()."""
    row = f.row(url)
    f.finish(row["id"], state="done", status=200, sniff="json", kind="file", ctype="application/json")
    return g.read(f, f.row_by_id(row["id"]), snap_of(url, data), g.frontier_adder(f))


def got(f, url, sniffed="pdf", kind="file"):
    row = f.row(url)
    f.finish(row["id"], state="done", status=200, sniff=sniffed, kind=kind)
    return row["id"]


def res(f, page):
    return {r["res_key"]: (r["result"], r["need"], r["role"])
            for r in f.q("SELECT * FROM page_resource WHERE page_id=?", (page["id"],))}


def entries(f, page):
    return {r["res_key"]: dict(r) for r in f.q("SELECT * FROM page_res WHERE page_id=?", (page["id"],))}


def item(fid, mime, name="O.pdf", **kw):
    return {"id": fid, "name": name, "mimeType": mime, **kw}


DRIVE_LINK = f"https://drive.google.com/file/d/{FILE_ID}/view?resourcekey=0-FiLe"
FOLDER_LINK = f"https://drive.google.com/drive/folders/{FOLDER_ID}"


def test_reading_an_upload_makes_its_download_and_the_page_points_at_it(f):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    e = entries(f, page)[f"drive:{FILE_ID}"]
    meta = f.row(g.item_url(FILE_ID))
    assert (e["how"], e["role"], e["need"], e["res_id"]) == ("drive_api", "drive", "must", meta["id"])
    assert (meta["host"], meta["kind0"], meta["leaf"], meta["owner_site"]) == (g.API_HOST, "drive", 1, ACA)
    assert res(f, page)[f"drive:{FILE_ID}"][0] == "todo"
    r = answer(f, meta["url"], item(FILE_ID, PDF, size="20480", md5Checksum="d41d8c", parents=[FOLDER_ID],
                                    modifiedTime="2026-09-01T02:03:04.000Z", resourceKey="0-FiLe"))
    content = f.row(g.media_url(FILE_ID))
    assert r.content == content["id"] and (content["kind0"], content["found_via"], content["leaf"]) == ("file",
                                                                                                         "drive", 1)
    assert f.row_by_id(meta["id"])["kind"] == "drive"  # an API answer is drive, never a file (§12)
    assert entries(f, page)[f"drive:{FILE_ID}"]["res_id"] == content["id"]  # §7.6: the page points at the content
    d = f.q("SELECT * FROM drive_item WHERE url_id=?", (meta["id"],))[0]
    assert (d["file_id"], d["name"], d["mime"], d["size"], d["content_id"], d["resource_key"], d["depth"]) == (
        FILE_ID, "O.pdf", PDF, 20480, content["id"], "0-FiLe", 0)
    assert res(f, page)[f"drive:{FILE_ID}"][0] == "todo"
    got(f, content["url"])
    assert res(f, page)[f"drive:{FILE_ID}"] == ("ok", "must", "drive")


def test_a_metadata_row_read_but_never_followed_is_not_discovered(f):
    # §7.6 保险: the page still pointing at an ok metadata row means the crawler read the kind and made no content row
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    meta = f.row(g.item_url(FILE_ID))
    f.finish(meta["id"], state="done", status=200, sniff="json", kind="drive")
    assert res(f, page)[f"drive:{FILE_ID}"][0] == "not_discovered"


@pytest.mark.parametrize("mime, export", [
    ("application/vnd.google-apps.document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("application/vnd.google-apps.spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("application/vnd.google-apps.presentation",
     "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ("application/vnd.google-apps.drawing", "application/pdf"),
])
def test_native_documents_are_exported(f, mime, export):
    page = add_page(f)
    use(f, page, f"https://docs.google.com/document/d/{FILE_ID}/edit")
    answer(f, g.item_url(FILE_ID), item(FILE_ID, mime, name="O"))
    content = f.row(g.export_url(FILE_ID, export))
    assert content is not None and content["kind0"] == "file"
    assert g.max_bytes(content["url"]) == g.EXPORT_MAX_BYTES == 10 * 1024 * 1024  # B11: export is 10 MB at most
    assert entries(f, page)[f"drive:{FILE_ID}"]["res_id"] == content["id"]


def test_an_uploaded_office_file_opened_in_an_editor_is_downloaded_as_it_is(f):
    # rtpof=true: a .docx shown in Docs is an upload (design §7.6): alt=media, never an export
    page = add_page(f)
    use(f, page, f"https://docs.google.com/document/d/{FILE_ID}/edit?rtpof=true&sd=true")
    answer(f, g.item_url(FILE_ID), item(FILE_ID, "application/vnd.openxmlformats-officedocument.wordprocessingml"
                                                 ".document", size="1000"))
    assert f.row(g.media_url(FILE_ID)) is not None
    assert g.max_bytes(g.media_url(FILE_ID)) == g.DOWNLOAD_MAX_BYTES == 50 * 1024 * 1024


def test_a_picture_is_an_image_row(f):
    page = add_page(f)
    use(f, page, f"https://drive.google.com/uc?id={FILE_ID}", how="image")
    answer(f, g.item_url(FILE_ID), item(FILE_ID, "image/jpeg", size="5000"))
    assert f.row(g.media_url(FILE_ID))["kind0"] == "image"


def test_a_form_drive_lists_is_read_by_one_get_of_its_page(f):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    answer(f, g.item_url(FILE_ID), item(FILE_ID, g.FORM, name="報名表"))
    form = f.row(g.form_page_url(FILE_ID))
    assert form["kind0"] == "form" and form["host"] == "docs.google.com" and g.queue_for(form["host"]) == "google"
    e = entries(f, page)[f"drive:{FILE_ID}"]
    assert (e["res_id"], e["role"]) == (form["id"], "form")
    assert g.target(f, form["url"]).credential is None  # a plain GET, never the key


def test_a_video_is_listed_only(f):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    answer(f, g.item_url(FILE_ID), item(FILE_ID, "video/mp4", name="O.mp4", size="900000000"))
    assert f.row(g.media_url(FILE_ID)) is None
    e = entries(f, page)[f"drive:{FILE_ID}"]
    assert (e["need"], e["why"]) == ("no", "media:drive_video")
    d = f.q("SELECT name, size FROM drive_item")[0]
    assert (d["name"], d["size"]) == ("O.mp4", 900000000)  # 只记档名和大小


@pytest.mark.parametrize("mime", ["application/vnd.google-apps.site", "application/vnd.google-apps.map",
                                  "application/vnd.google-apps.jam"])
def test_a_google_type_nobody_can_export_is_for_a_person(f, mime):
    # B11: Sites, My Maps, Jamboard: need must, unhandled:drive_type (never need no)
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    answer(f, g.item_url(FILE_ID), item(FILE_ID, mime, name="O"))
    e = entries(f, page)[f"drive:{FILE_ID}"]
    assert (e["need"], e["why"], e["res_id"]) == ("must", "unhandled:drive_type", None)
    assert res(f, page)[f"drive:{FILE_ID}"][0] == "unhandled"


def test_an_upload_over_50_mb_is_too_large_without_a_request(f):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    answer(f, g.item_url(FILE_ID), item(FILE_ID, PDF, size=str(60 * 1024 * 1024)))
    content = f.row(g.media_url(FILE_ID))
    assert (content["state"], content["reason"], content["size_declared"]) == ("skipped", "too_large",
                                                                               60 * 1024 * 1024)
    assert res(f, page)[f"drive:{FILE_ID}"][0] == "too_large"


def test_a_shortcut_is_read_through_to_its_target_with_the_targets_key(f):
    page = add_page(f)
    target_id = "1TaRgEtIdAbCdEfGhIjKlMnOpQrSt"
    use(f, page, DRIVE_LINK)
    answer(f, g.item_url(FILE_ID), item(FILE_ID, g.SHORTCUT, name="O 捷徑", shortcutDetails={
        "targetId": target_id, "targetMimeType": PDF, "targetResourceKey": "0-TaRgEt"}))
    tmeta = f.row(g.item_url(target_id))
    assert tmeta is not None and entries(f, page)[f"drive:{FILE_ID}"]["res_id"] == tmeta["id"]
    assert g.target(f, tmeta["url"]).headers[g.RESOURCE_KEYS_HEADER] == f"{target_id}/0-TaRgEt"
    answer(f, tmeta["url"], item(target_id, PDF, size="10"))
    assert entries(f, page)[f"drive:{FILE_ID}"]["res_id"] == f.row(g.media_url(target_id))["id"]


def test_a_folder_lists_level_by_level_on_the_page_and_page_by_page(f):
    page = add_page(f)
    use(f, page, FOLDER_LINK)
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name="法規"))
    p1 = f.row(g.list_url(FOLDER_ID))
    assert p1["kind0"] == "drive_list" and entries(f, page)[f"drive:{FOLDER_ID}"]["res_id"] == p1["id"]
    a, b = "1ChIlDaAbCdEfGhIjKlMnOpQr", "1ChIlDbAbCdEfGhIjKlMnOpQr"
    answer(f, p1["url"], {"nextPageToken": "tok2", "files": [
        item(a, PDF, name="O 辦法.pdf", size="100", resourceKey="0-ChA"),
        item(SUB_ID, g.FOLDER, name="附件")]})
    e = entries(f, page)
    assert e[f"drive:{a}"]["res_id"] == f.row(g.item_url(a))["id"] and e[f"drive:{a}"]["text"] == "O 辦法.pdf"
    assert e[f"drive:{SUB_ID}"]["need"] == "must"
    p2 = f.row(g.list_url(FOLDER_ID, "tok2"))
    assert e[f"drivelist:{FOLDER_ID}:p2"]["res_id"] == p2["id"] and p2["found_from"] == p1["id"]
    answer(f, p2["url"], {"files": [item(b, "application/msword", name="O.doc", size="10")]})
    assert f"drive:{b}" in entries(f, page)
    # the subfolder, read and listed: its file is on the page too (§7.6: 子档案也写在原页上)
    answer(f, g.item_url(SUB_ID), item(SUB_ID, g.FOLDER, name="附件"))
    c = "1ChIlDcAbCdEfGhIjKlMnOpQr"
    answer(f, g.list_url(SUB_ID), {"files": [item(c, PDF, name="O.pdf", size="10")]})
    assert entries(f, page)[f"drive:{c}"]["res_id"] == f.row(g.item_url(c))["id"]
    depth = {r["file_id"]: r["depth"] for r in f.q("SELECT file_id, depth FROM drive_item")}
    assert (depth[FOLDER_ID], depth[a], depth[SUB_ID], depth[c]) == (0, 1, 1, 2)
    # a child's resource key goes with every request for it, and so do the folders' above it (B11)
    answer(f, g.item_url(a), item(a, PDF, size="100", resourceKey="0-ChA"))
    assert g.target(f, g.media_url(a)).headers[g.RESOURCE_KEYS_HEADER] == f"{a}/0-ChA"
    # every file still to be got keeps the page from being complete
    got_now = res(f, page)
    assert got_now[f"drive:{a}"][0] == "todo" and got_now[f"drivelist:{FOLDER_ID}:p2"][0] == "ok"


def test_a_second_page_linking_a_folder_already_read_gets_all_it_holds(f):
    first, second = add_page(f), add_page(f, "https://c.nknu.edu.tw/aca/Page.aspx?PN=9")
    use(f, first, FOLDER_LINK)
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name="O"))
    a = "1ChIlDaAbCdEfGhIjKlMnOpQr"
    answer(f, g.list_url(FOLDER_ID), {"files": [item(a, PDF, size="1")]})
    use(f, second, f"https://drive.google.com/embeddedfolderview?id={FOLDER_ID}")
    assert set(entries(f, second)) == {f"drive:{FOLDER_ID}", f"drive:{a}"}
    assert entries(f, second)[f"drive:{FOLDER_ID}"]["res_id"] == f.row(g.list_url(FOLDER_ID))["id"]


def test_resync_mends_entries_a_reading_overtook(f):
    # a page worker computes its entries, the googleapis worker reads the item, then the page writes them
    page = add_page(f)
    u = g.uses(PLAN, scope.canonical(DRIVE_LINK), page, "frame")[0]
    rid = g.frontier_adder(f)(u.url, u.placement, page, "link")[0]
    stale = g.attach(f, u, rid)
    answer(f, u.url, item(FILE_ID, PDF, size="1"))
    f.add_page_res(page["id"], stale)
    assert entries(f, page)[f"drive:{FILE_ID}"]["res_id"] == rid
    assert g.resync(f, page["id"]) == 1
    assert entries(f, page)[f"drive:{FILE_ID}"]["res_id"] == f.row(g.media_url(FILE_ID))["id"]
    assert g.resync(f, page["id"]) == 0  # idempotent


def test_an_album_folders_pictures_are_listed_not_downloaded(f):
    page = add_page(f)
    use(f, page, FOLDER_LINK)
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name="113 校慶活動照片"))
    pic, doc = "1PiCaAbCdEfGhIjKlMnOpQrSt", "1DoCaAbCdEfGhIjKlMnOpQrSt"
    answer(f, g.list_url(FOLDER_ID), {"files": [item(pic, "image/jpeg", name="IMG_1.jpg", size="1"),
                                                 item(doc, PDF, name="O 名單.pdf", size="1")]})
    e = entries(f, page)
    assert (e[f"drive:{pic}"]["need"], e[f"drive:{pic}"]["why"]) == ("no", "album:drive")
    assert f.row(g.item_url(pic))["state"] == "skipped" and f.row(g.item_url(pic))["reason"] == "album:drive"
    assert e[f"drive:{doc}"]["need"] == "must"  # an album's other files are got
    assert f.q("SELECT album FROM drive_item WHERE file_id=?", (pic,))[0][0] == 1
    assert e[f"drive:{FOLDER_ID}"]["need"] == "must"  # the listing itself is needed (§7.6)


def _pictures_folder(f, name: str, mime: str = "image/jpeg") -> tuple[dict, list[str]]:
    """A page linking a folder of that name whose only listing page holds pictures only."""
    page = add_page(f)
    use(f, page, FOLDER_LINK)
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name=name))
    pics = [f"1PiC{i}AbCdEfGhIjKlMnOpQrSt" for i in range(3)]
    answer(f, g.list_url(FOLDER_ID), {"files": [item(p, mime, name=f"scan{i}.jpg", size="1")
                                                 for i, p in enumerate(pics)]})
    return page, pics


def _got_as_attachments(f, page, pics) -> None:
    e = entries(f, page)
    for p in pics:
        assert (e[f"drive:{p}"]["need"], e[f"drive:{p}"]["why"]) == ("must", None), p
        assert f.row(g.item_url(p))["state"] == "pending" and f.row(g.item_url(p))["reason"] is None
    assert f.q("SELECT count(*) FROM drive_item WHERE album=1")[0][0] == 0


def test_a_folder_of_scanned_pictures_is_no_album_when_its_name_does_not_say_so(f):
    # design §7.6 (10-26): 「资料夹里全是图片」不再当相簿的依据: a 公文 scanned to JPG is the notice's attachment (record 三·7)
    page, pics = _pictures_folder(f, "公文")
    _got_as_attachments(f, page, pics)


def test_a_folder_named_only_for_an_event_is_no_album(f):
    # 名字只有「活動」也不算: 活動報名表 scanned to pictures are forms to download, never an album's pictures
    page, pics = _pictures_folder(f, "活動報名表", mime="image/png")
    _got_as_attachments(f, page, pics)
    assert g.album_name("活動報名表") is False and g.album_name("活动") is False


def test_a_folder_whose_name_says_photos_is_an_album(f):
    page, pics = _pictures_folder(f, "運動會照片")
    e = entries(f, page)
    assert all((e[f"drive:{p}"]["need"], e[f"drive:{p}"]["why"]) == ("no", "album:drive") for p in pics)
    assert all(f.row(g.item_url(p))["reason"] == "album:drive" for p in pics)
    assert e[f"drive:{FOLDER_ID}"]["need"] == "must"  # the listing itself is needed (§7.6)


@pytest.mark.parametrize("name", ["運動會照片", "113 校慶相片", "畢業典禮花絮", "活動花絮", "系學會相簿", "相册",
                                  "Photos 2024", "Album", "photo", "Gallery"])
def test_the_words_that_make_a_folder_an_album(name):
    assert g.album_name(name), name


@pytest.mark.parametrize("name", ["公文", "活動報名表", "活動", "活动海报", "海報", "1130901", "掃描檔", "會議紀錄",
                                  ""])
def test_names_that_make_no_album(name):
    assert not g.album_name(name), name


def test_a_picture_a_page_links_itself_is_got_even_from_an_album(f):
    page = add_page(f)
    use(f, page, FOLDER_LINK)
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name="相簿"))
    pic = "1PiCaAbCdEfGhIjKlMnOpQrSt"
    answer(f, g.list_url(FOLDER_ID), {"files": [item(pic, "image/jpeg", name="海報.jpg")]})
    poster = add_page(f, "https://c.nknu.edu.tw/aca/Page.aspx?PN=7")
    use(f, poster, f"https://drive.google.com/file/d/{pic}/view", how="link")
    assert f.row(g.item_url(pic))["state"] == "pending"
    assert entries(f, poster)[f"drive:{pic}"]["need"] == "must"


def test_drive_caps_are_held_and_the_page_points_at_them(f, monkeypatch):
    monkeypatch.setattr(g, "MAX_DRIVE_DEPTH", 1)
    monkeypatch.setattr(g, "MAX_LIST_PAGES", 1)
    page = add_page(f)
    use(f, page, FOLDER_LINK)
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name="O"))
    answer(f, g.list_url(FOLDER_ID), {"nextPageToken": "t2", "files": [item(SUB_ID, g.FOLDER, name="O")]})
    answer(f, g.item_url(SUB_ID), item(SUB_ID, g.FOLDER, name="O"))
    deep = "1DeEpAbCdEfGhIjKlMnOpQrStUv"
    answer(f, g.list_url(SUB_ID), {"files": [item(deep, g.FOLDER, name="O")]})
    deep_row, p2 = f.row(g.item_url(deep)), f.row(g.list_url(FOLDER_ID, "t2"))
    assert (deep_row["state"], deep_row["reason"]) == ("held", "cap:drive_depth")  # level 2 of 1
    assert (p2["state"], p2["reason"]) == ("held", "cap:drive_list")  # page 2 of 1
    r = res(f, page)
    assert r[f"drive:{deep}"][0] == "held" and r[f"drivelist:{FOLDER_ID}:p2"][0] == "held"
    # the same folder linked by a page itself is wanted: no longer held
    use(f, add_page(f, "https://c.nknu.edu.tw/aca/Page.aspx?PN=8"), f"https://drive.google.com/drive/folders/{deep}")
    assert f.row(g.item_url(deep))["state"] == "pending"


def test_a_sites_drive_downloads_are_capped(f, monkeypatch):
    monkeypatch.setattr(g, "SITE_DRIVE_CAP", 1)
    page = add_page(f)
    other = "1OtHeRaAbCdEfGhIjKlMnOpQrSt"
    use(f, page, DRIVE_LINK)
    use(f, page, f"https://drive.google.com/file/d/{other}/view")
    answer(f, g.item_url(FILE_ID), item(FILE_ID, PDF, size="1"))
    answer(f, g.item_url(other), item(other, PDF, size="1"))
    assert f.row(g.media_url(FILE_ID))["state"] == "pending"
    held = f.row(g.media_url(other))
    assert (held["state"], held["reason"]) == ("held", "cap:drive")
    assert res(f, page)[f"drive:{other}"][0] == "held"


def test_an_answer_that_cannot_be_read_fails_the_row(f):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    r = answer(f, g.item_url(FILE_ID), {"unexpected": True})
    row = f.row(g.item_url(FILE_ID))
    assert r.failed == "api:unreadable" and (row["state"], row["reason"]) == ("failed", "api:unreadable")
    assert res(f, page)[f"drive:{FILE_ID}"][0] == "failed"  # never got


# ---------------------------------------------------------------- calendars

def test_each_embedded_calendar_is_read_page_by_page_and_holidays_are_a_link(f, monkeypatch):
    monkeypatch.setattr(g, "MAX_CALENDAR_PAGES", 2)
    page = add_page(f)
    embed = f"https://calendar.google.com/calendar/embed?src={CAL}&src={b64(CAL2)}&src={b64(HOLIDAY)}"
    use(f, page, embed)
    e = entries(f, page)
    p1 = f.row(g.events_url(CAL))
    assert (e[f"calendar:{CAL}:p1"]["res_id"], e[f"calendar:{CAL}:p1"]["how"]) == (p1["id"], "calendar_api")
    assert f.row(g.events_url(CAL2)) is not None and e[f"calendar:{CAL2}:p1"]["need"] == "must"
    assert (e[f"calendar:{HOLIDAY}"]["need"], e[f"calendar:{HOLIDAY}"]["res_id"]) == ("no", None)
    assert f.row(g.events_url(HOLIDAY)) is None
    assert p1["kind0"] == "calendar" and g.queue_for(p1["host"]) == g.QUEUE
    assert "singleEvents=true" in p1["url"] and "orderBy=startTime" in p1["url"] and "timeMin" not in p1["url"]
    events = {"kind": "calendar#events", "summary": "O", "timeZone": "Asia/Taipei", "items": [
        {"kind": "calendar#event", "id": "e1", "status": "confirmed", "summary": "期中考試",
         "start": {"date": "2026-11-09"}, "end": {"date": "2026-11-14"}, "iCalUID": "e1@google.com"}]}
    answer(f, p1["url"], {**events, "nextPageToken": "c2"})
    assert f.row_by_id(p1["id"])["kind"] == "calendar"
    p2 = f.row(g.events_url(CAL, "c2"))
    assert entries(f, page)[f"calendar:{CAL}:p2"]["res_id"] == p2["id"]
    answer(f, p2["url"], {**events, "nextPageToken": "c3"})
    p3 = f.row(g.events_url(CAL, "c3"))
    assert (p3["state"], p3["reason"]) == ("held", "cap:calendar")  # the 3rd of 2 (the 11th of 10)
    assert res(f, page)[f"calendar:{CAL}:p3"][0] == "held"
    # a page that embeds the same calendar later gets every page read so far
    again = add_page(f, "https://c.nknu.edu.tw/aca/Page.aspx?PN=2")
    use(f, again, f"https://www.google.com/calendar/embed?src={CAL}")
    assert {k for k in entries(f, again)} == {f"calendar:{CAL}:p1", f"calendar:{CAL}:p2", f"calendar:{CAL}:p3"}


def test_a_link_to_an_apps_page_writes_nothing_on_the_page(f):
    page = add_page(f)
    assert use(f, page, "https://drive.google.com/drive/quota", how="link") == []
    published = use(f, page, "https://docs.google.com/spreadsheets/d/e/2PACX-1vTaBcD/pubhtml", how="frame")
    assert [(e["need"], e["why"], e["res_id"]) for e in published] == [("round2", "round2:google_published", None)]
    assert res(f, page)["google:https://docs.google.com/spreadsheets/d/e/2PACX-1vTaBcD/pubhtml"][0] == "later"
    odd = use(f, page, "https://drive.google.com/viewer?srcid=x")
    assert [(e["need"], e["why"]) for e in odd] == [("must", "unhandled:google")]


# ---------------------------------------------------------------- error answers (design §7.6's table)

def err(status, reason=None, details=None, status_name=None):
    e = {"code": status, "message": "Something Google says."}
    if reason:
        e["errors"] = [{"domain": "global", "reason": reason, "message": "Something Google says."}]
    if details:
        e["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": details,
                         "domain": "googleapis.com", "metadata": {"service": "drive.googleapis.com"}}]
    if status_name:
        e["status"] = status_name
    return json.dumps({"error": e}).encode()


@pytest.mark.parametrize("status, body, headers, action, url_reason, pause", [
    (404, err(404, "notFound"), {}, "answer", "api:notFound", None),
    (404, b"<html>Not Found</html>", {}, "answer", "api:notFound", None),
    (403, err(403, "rateLimitExceeded"), {}, "retry", "api:rateLimitExceeded", 900.0),
    (403, err(403, "userRateLimitExceeded"), {"retry-after": "120"}, "retry", "api:userRateLimitExceeded", 120.0),
    (403, err(403, "dailyLimitExceeded"), {}, "retry", "api:dailyLimitExceeded", 900.0),
    (403, err(403, "downloadQuotaExceeded"), {}, "retry", "api:downloadQuotaExceeded", 900.0),
    (429, err(429, details="RATE_LIMIT_EXCEEDED", status_name="RESOURCE_EXHAUSTED"), {}, "retry",
     "api:RATE_LIMIT_EXCEEDED", 900.0),
    (403, err(403, "insufficientFilePermissions"), {}, "login", "api:insufficientFilePermissions", None),
    (403, err(403, "forbidden"), {}, "login", "api:forbidden", None),
    (403, err(403, "exportSizeLimitExceeded"), {}, "too_large", "too_large", None),
    (400, err(400, "keyInvalid"), {}, "key", "api_key", g.KEY_PAUSE_S),
    (400, err(400, details="API_KEY_INVALID", status_name="INVALID_ARGUMENT"), {}, "key", "api_key", g.KEY_PAUSE_S),
    (403, err(403, "accessNotConfigured", details="SERVICE_DISABLED", status_name="PERMISSION_DENIED"), {}, "key",
     "api_key", g.KEY_PAUSE_S),
    (403, err(403, "cannotDownloadFile"), {}, "answer", "api:cannotDownloadFile", None),
    (500, err(500, "backendError"), {}, "retry", "api:backendError", None),
])
def test_error_reasons_decide_what_happens(status, body, headers, action, url_reason, pause):
    e = g.classify(status, body, headers)
    assert (e.action, e.url_reason, e.pause_s) == (action, url_reason, pause)
    assert "Something Google says" not in repr(e)  # reason codes only, never Google's message text
    assert g.classify(200, b"{}") is None


@pytest.mark.parametrize("status, body, result", [
    (404, err(404, "notFound"), "api_error"),  # 不公开和已删除分不出: never 学校那边已经不存在 (gone)
    (403, err(403, "cannotDownloadFile"), "api_error"),
    (403, err(403, "insufficientFilePermissions"), "login"),
    (403, err(403, "exportSizeLimitExceeded"), "too_large"),
])
def test_what_an_error_answer_comes_to(f, status, body, result):
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    row = f.row(g.item_url(FILE_ID))
    f.finish(row["id"], state="done", status=status, reason=f"http_{status}", sniff="json", kind="file")
    f.finish(row["id"], **g.error_cols(g.classify(status, body)))
    assert f.q("SELECT result FROM url_result WHERE id=?", (row["id"],))[0][0] == result
    assert res(f, page)[f"drive:{FILE_ID}"][0] == result


# ---------------------------------------------------------------- the key and resource keys

def test_the_key_travels_only_in_its_header_with_the_resource_keys(f, tmp_path, monkeypatch):
    monkeypatch.setenv(g.KEY_ENV, SECRET)
    page = add_page(f)
    use(f, page, FOLDER_LINK.replace(FOLDER_ID, FOLDER_ID) + "?resourcekey=0-FoLdEr")
    answer(f, g.item_url(FOLDER_ID), item(FOLDER_ID, g.FOLDER, name="O"))
    a = "1ChIlDaAbCdEfGhIjKlMnOpQr"
    answer(f, g.list_url(FOLDER_ID), {"files": [item(a, PDF, size="1", resourceKey="0-ChA")]})
    t = g.target(f, g.item_url(a))
    assert t.credential == g.CREDENTIAL and SECRET not in repr(t)
    seen = {}

    def handler(req):
        seen.update(req.headers, url=str(req.url))
        return httpx.Response(200, json=item(a, PDF, size="1"))

    sink = WarcSink(tmp_path / "warc", g.QUEUE, 1, datetime.now(UTC))
    client = Client(transport=httpx.MockTransport(handler), warc=sink, sleep=lambda s: None)
    client.fetch(t, RateLimit(min_interval_s=0, jitter_s=0))
    sink.close()
    assert seen["x-goog-api-key"] == SECRET and SECRET not in seen["url"]
    assert seen["x-goog-drive-resource-keys"] == f"{a}/0-ChA,{FOLDER_ID}/0-FoLdEr"
    warc = gzip.decompress(sink.path.read_bytes())
    assert SECRET.encode() not in warc and b"[redacted]" in warc


def test_a_request_with_the_key_never_follows_a_redirect(f, monkeypatch):
    monkeypatch.setenv(g.KEY_ENV, SECRET)
    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        return httpx.Response(302, headers={"location": "https://elsewhere.example/x"})

    client = Client(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(FetchError, match="302") as exc:
        client.fetch(g.target(f, g.media_url(FILE_ID)), RateLimit(min_interval_s=0, jitter_s=0))
    assert hosts == [g.API_HOST] and SECRET not in str(exc.value)


def test_without_a_key_the_api_rows_wait_and_nothing_fails(f, monkeypatch):
    monkeypatch.delenv(g.KEY_ENV, raising=False)
    page = add_page(f)
    use(f, page, DRIVE_LINK)
    assert not g.key_set() and g.hold_for_key(f, 1000.0) is False
    row = f.row(g.item_url(FILE_ID))
    assert (row["state"], row["reason"], row["attempts"]) == ("pending", "no_api_key", 0)
    assert f.paused_until(g.QUEUE) == 1000.0 + g.KEY_PAUSE_S
    assert f.q("SELECT code FROM event")[0][0] == "key_check"
    assert f.q("SELECT result FROM url_result WHERE id=?", (row["id"],))[0][0] == "todo"  # 不算失败
    monkeypatch.setenv(g.KEY_ENV, SECRET)
    assert g.hold_for_key(f, 2000.0) is True
    assert f.paused_until(g.QUEUE) == 0 and f.row(g.item_url(FILE_ID))["reason"] is None
    assert not any(SECRET in (r["message"] or "") for r in f.q("SELECT message FROM event"))


# ---------------------------------------------------------------- pages that hold Google's addresses

COUNSEL_PAGE = "https://sites.google.com/mail.nknu.edu.tw/counseling-center/home"
# the shape of the counseling centre's Google Sites page (old archive, read only; names masked, ids made up): the
# iframe's src is empty and the file is in data-src, the wrapper names the same file again (design §4.4, §16)
COUNSEL_HTML = f"""<html><head><title>OO中心</title></head><body><div class="tyJCtd">
<div class="WIdY2d M1aSXe" data-embed-doc-id="{FILE_ID}" data-embed-open-url="https://drive.google.com/open?id={FILE_ID}"
 data-embed-download-url="https://drive.google.com/uc?id={FILE_ID}&amp;export=download">
<iframe jsname="L5Fo6c" class="YMEQtf" frameborder="0" aria-label="Drive, OO心理師招募公告_OOO.doc"
 data-src="https://drive.google.com/file/d/{FILE_ID}/preview" allowfullscreen></iframe></div></div></body></html>"""


def test_a_google_sites_drive_embed_is_read_from_its_data_src(f):
    page = links.extract(COUNSEL_HTML, COUNSEL_PAGE)
    [frame] = page.frames
    assert g.parse_link(scope.canonical(frame.src)).file_id == FILE_ID
    counsel = plan(site("sites.google.com/mail.nknu.edu.tw/counseling-center", name="谘商中心",
                        home=COUNSEL_PAGE))
    adm = round1.admit(counsel, frame.src, COUNSEL_PAGE,
                       page_row_dict(tier=1, owner="sites.google.com/mail.nknu.edu.tw/counseling-center"), "frame",
                       google=g.step(counsel))
    assert adm.url == g.item_url(FILE_ID) and adm.placement.verdict == "resource"


FORM_PAGE = "https://docs.google.com/forms/d/e/1FAIpQLSdXyZaBcDeFgHiJkL/viewform"
# a form's page, as small as it can be: its description links Google wraps (google.com/url?q=), a header picture,
# Google's own logo and links, its fields and its button
FORM_HTML = """<html><head><title>OO 研習報名</title></head><body>
<img src="https://lh7-rt.googleusercontent.com/formsz/AbCdEf=w740" alt="">
<div class="description">報名前請先閱讀
<a href="https://www.google.com/url?q=https://c.nknu.edu.tw/aca/upload/O%E8%BE%A6%E6%B3%95.pdf&amp;sa=D&amp;ust=1">辦法</a>、
<a href="https://www.google.com/url?q=https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view&amp;sa=D">海報</a>，
詳見 <a href="https://www.google.com/url?q=https://c.nknu.edu.tw/aca/news.aspx&amp;sa=D">公告</a>。</div>
<input type="text" name="entry.1" aria-label="姓名"><div role="button">提交</div>
<img src="https://www.gstatic.com/images/branding/googlelogo/svg/googlelogo_clr_74x24px.svg" alt="Google">
<a href="https://policies.google.com/terms">服務條款</a>
<a href="https://docs.google.com/forms/d/e/1FAIpQLSdXyZaBcDeFgHiJkL/reportabuse">檢舉濫用情形</a>
</body></html>"""


def test_a_forms_description_counts_on_the_page_and_nothing_else_is_followed():
    parts = g.form_parts(FORM_HTML, FORM_PAGE)
    targets = {scope.canonical(h, parts.base) for h, _t in parts.links}
    assert targets == {scope.canonical("https://c.nknu.edu.tw/aca/upload/O%E8%BE%A6%E6%B3%95.pdf"),
                       f"https://drive.google.com/file/d/{FILE_ID}/view"}  # B12: unwrapped, attachments and Drive only
    assert [i.src for i in parts.images] == ["https://lh7-rt.googleusercontent.com/formsz/AbCdEf=w740"]
    assert parts.text_chars > 0 and not parts.buttons and not parts.pagers and not parts.login_form
    # the Drive file of a form's description is the page's: the page that uses the form, never the form's own
    adm = round1.admit(PLAN, f"https://www.google.com/url?q=https://drive.google.com/file/d/{FILE_ID}/view&sa=D",
                       FORM_PAGE, page_row_dict(), "link", google=g.step(PLAN))
    assert adm.url == g.item_url(FILE_ID) and adm.placement.owner_site == ACA


def test_a_form_is_one_get_with_no_key(f):
    page = add_page(f)
    [u] = g.uses(PLAN, "https://docs.google.com/a/mail.nknu.edu.tw/forms/d/e/1FAIpQLSdXyZaBcDeFgHiJkL/viewform",
                 page, "link")
    assert (u.url, u.placement.kind0, u.placement.leaf, u.role) == (FORM_PAGE, "form", 1, "form")
    t = g.target(f, u.url)
    assert (t.method, t.credential, t.data) == ("GET", None, None)


# ---------------------------------------------------------------- with the crawler as it is (the step plugged in)

def test_plugged_into_the_crawler_a_drive_frame_becomes_a_metadata_row_on_the_googleapis_queue(tmp_path):
    from tests.archive.test_archive import drain, make, snap

    html = f'<html><body><iframe src="https://drive.google.com/file/d/{FILE_ID}/preview"></iframe></body></html>'
    crawler, net, _ = make(tmp_path, {"robots.txt": snap("x", 404), "page.aspx?pn=5": snap(PAGE, html=html)},
                           queues={"c.nknu.edu.tw": "ip:c", g.API_HOST: g.QUEUE}, plan=PLAN)
    crawler.google = g.step(crawler.plan)
    crawler.seed_url(PAGE)
    drain(crawler, net, "ip:c")
    meta = crawler.frontier.row(g.item_url(FILE_ID))
    assert (meta["state"], meta["kind0"], meta["owner_site"], meta["leaf"]) == ("pending", "drive", ACA, 1)
    assert crawler.frontier.host(g.API_HOST)["queue"] == g.QUEUE
    link = crawler.frontier.q("SELECT verdict FROM link WHERE dst=?",
                              (f"https://drive.google.com/file/d/{FILE_ID}/preview",))
    assert link[0][0] == "queued"  # never unhandled:google once the step is in
    assert not any("drive.google.com" in u for u in net.seen)  # Drive's web pages are never fetched
