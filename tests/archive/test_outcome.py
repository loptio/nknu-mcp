"""What a frontier row came to (round-1 design §10, outcome.py): one test per row of the §10.2 result table, a grid
over every state, reason, flag, status, kind and page-type verdict that must land on a known code and never on `ok`
(nor on `rule`) unless every piece of evidence says so, the error pages of §10.1, the button results of §10.3, the
user's categories of §10.2.1, each level and label of the page completeness view (§10.4), and redirects and replaced
presses counted where they land, never as fetched by themselves and never dropping a round-1 page from the table."""

from __future__ import annotations

import itertools
import random

import pytest

from nknu.archive import outcome
from nknu.archive.frontier import Frontier

SITE = "c.nknu.edu.tw/aca"
_n = itertools.count()

# page types (design §8): one per verdict, and one with a verdict the rules do not know
TYPES = {"t:ok": "ok", "t:js": "js", "t:frame": "frame", "t:todo": "todo", "t:click": "click",
         "t:unresolved": "unresolved", "t:odd": "unsure"}
API = "https://www.googleapis.com/drive/v3/files/1AbCdEfG?alt=media"  # Google's API host (§7.6)


@pytest.fixture
def f(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    for key, verdict in TYPES.items():
        fr.x("INSERT INTO page_type(key, site, verdict) VALUES (?,?,?)", (key, SITE, verdict))
    yield fr
    fr.close()


def add_url(f: Frontier, **cols) -> int:
    row = {"url": f"https://c.nknu.edu.tw/aca/p{next(_n)}.aspx", "host": "c.nknu.edu.tw", "site": SITE,
           "owner_site": SITE, "tier": 0, "kind": "page", "kind0": "page", "depth": 0, "found_via": "seed",
           "state": "done", "status": 200, "page_type": "t:ok"}
    row.update(cols)
    row.setdefault("path", row["url"].split("?", 1)[0])
    names = ", ".join(row)
    with f.lock:
        return f.conn.execute(f"INSERT INTO url({names}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


def result_of(f: Frontier, uid: int) -> str:
    return f.q("SELECT result FROM url_result WHERE id=?", (uid,))[0][0]


# ---------------------------------------------------------------- §10.2: every row of the result table

FILE = {"kind": "file", "kind0": "file", "page_type": None, "sniff": "pdf"}
# a short link's own row (design §7.7, review 10-26 A2): one GET to learn where it leads, never a page
SHORT = {"url": "https://reurl.cc/AbCdE", "host": "reurl.cc", "site": "reurl.cc/AbCdE", "kind": "shortlink",
         "kind0": "shortlink", "page_type": None, "tier": 0}
SHORT_DEAD = {**SHORT, "url": "https://lurl.cc/dead1", "host": "lurl.cc", "status": 404}
URL_CASES = [
    # ok: fetched, every piece of evidence agrees
    ("ok", {}),
    ("ok", {"page_type": "t:frame"}),  # its frames are resources of the page (page_completeness)
    ("ok", {"flags": "form_closed"}),  # a closed form is still the form
    ("ok", FILE),
    ("ok", {**FILE, "kind": "image", "kind0": "image", "sniff": "jpeg"}),
    ("ok", {**FILE, "kind": "drive", "kind0": "drive", "sniff": "json"}),  # an API answer is sniffed too (§7.3)
    ("ok", {**FILE, "kind": "drive", "kind0": "drive", "sniff": "ooxml:docx"}),
    ("ok", {**FILE, "kind": "calendar", "kind0": "calendar", "sniff": "json"}),
    # what is not HTML is a file once fetched (§10.2): a sitemap, a school script, a tab's JSON, a Drive folder's page
    ("ok", {**FILE, "kind0": "sitemap", "sniff": "xml"}),
    ("ok", {**FILE, "kind0": "script", "sniff": "text"}),
    ("ok", {**FILE, "kind0": "click", "sniff": "json"}),
    ("ok", {**FILE, "kind0": "drive_list", "sniff": "json"}),
    # a Google form and a Google Sites embed box are HTML by nature: the page's part, not a page (§4.4)
    ("ok", {"kind": "embed", "kind0": "form", "sniff": "html", "page_type": None}),
    ("ok", {"kind": "embed", "kind0": "form", "sniff": "html", "page_type": None, "flags": "form_closed"}),
    ("ok", {"kind": "embed", "kind0": "embed", "sniff": "html", "page_type": None}),
    ("ok", {"url": "https://sso.nknu.edu.tw/news/lastnews.aspx?unid=8f0e", "host": "sso.nknu.edu.tw",
            "site": "sso.nknu.edu.tw"}),  # an expired notice that still answers in full is a notice (§10.1)
    # gone: the school has removed it
    ("gone", {"status": 302, "flags": "gone_redirect"}),  # sent to SSO's pageNotFound
    ("gone", {"status": 404}),
    ("gone", {"status": 410}),
    ("gone", {"status": 302, "flags": "soft404,gone_redirect"}),
    ("gone", {**FILE, "kind": "drive", "kind0": "drive", "sniff": "json", "status": 404}),  # a school host's 404
    ("gone", {**FILE, "sniff": "html", "flags": "gone_page"}),  # SSO's 「檔案不存在」 page, seen twice (review B6)
    # bad_link: the school's page says its own link is broken (SSO's 「檔案包含不支援的字元」, review B6)
    ("bad_link", {**FILE, "sniff": "html", "flags": "bad_link"}),
    # short_dead: a short link that answers 404 or 410 is dead, not a page the school removed (review A2)
    ("short_dead", SHORT_DEAD),
    # unhandled: a short link's 200 page nobody could read a target from (unhandled:shortlink, review A2)
    ("unhandled", {**SHORT, "url": "https://reurl.cc/unread0", "reason": "unhandled:shortlink"}),
    # looks_gone: only the program's comparison says so (§10.1): a person looks, never 学校那边已经不存在
    ("looks_gone", {"flags": "soft404"}),
    ("looks_gone", {"flags": "gone_id"}),
    ("looks_gone", {"flags": "soft404,gone_id,empty_detail"}),
    # api_error: Google's API cannot read it (private and deleted both answer 404 with a key, §7.6)
    ("api_error", {**FILE, "kind": "drive", "kind0": "drive", "sniff": "json", "status": 404,
                   "host": "www.googleapis.com", "url": API}),
    ("api_error", {**FILE, "kind": "drive", "kind0": "drive", "sniff": "json", "status": 403,
                   "host": "www.googleapis.com", "url": API + "&x=403"}),
    ("api_error", {**FILE, "kind": "calendar", "kind0": "calendar", "sniff": "json", "status": 410,
                   "host": "www.googleapis.com", "url": API + "&x=410"}),
    # login
    ("login", {"status": 302, "flags": "login"}),
    ("login", {"flags": "login"}),
    ("login", {"status": 403, "flags": "login"}),  # Drive: no permission
    ("login", {"status": 401, "flags": "login"}),
    ("login", {"flags": "login,js_redirect"}),  # a script sends the browser to a sign-in page (§8.3)
    ("login", {**FILE, "kind": "drive", "kind0": "drive", "sniff": "json", "status": 403, "flags": "login",
               "host": "www.googleapis.com", "url": API + "&x=perm"}),  # insufficientFilePermissions (§7.6)
    # redirect: the target is another row
    ("redirect", {"status": 301}),
    ("redirect", {"status": 302, "page_type": None}),
    ("redirect", {"state": "skipped", "reason": "moved_scheme", "status": None}),
    ("redirect", {"flags": "js_redirect", "final_url": "https://c.nknu.edu.tw/aca/index.html"}),  # §8.3
    ("redirect", {"flags": "js_redirect", "page_type": "t:todo", "final_url": "https://c.nknu.edu.tw/aca/a.aspx"}),
    ("redirect", {**SHORT, "url": "https://bit.ly/moved0", "status": 302, "final_url": "https://c.nknu.edu.tw/aca/"}),
    ("redirect", {**SHORT, "url": "https://reurl.cc/read0", "final_url": "https://c.nknu.edu.tw/aca/"}),  # its target read
    # rule: only the reasons the scope rules give (review note medium)
    ("rule", {"state": "skipped", "reason": "out:social", "status": None}),
    ("rule", {"state": "skipped", "reason": "decor:repeat", "status": None}),
    ("rule", {"state": "skipped", "reason": "media_type"}),
    ("rule", {**FILE, "kind": "image", "kind0": "image", "state": "skipped", "reason": "album:drive", "status": None}),
    # later
    ("later", {"state": "later", "reason": "round2:js_site", "status": None, "tier": 2}),
    # todo
    ("todo", {"state": "pending", "status": None}),
    ("todo", {"state": "active", "status": None}),
    ("todo", {"state": "pending", "reason": "no_api_key", "status": None}),  # Drive waits for the key, not a failure
    # retry
    ("retry", {"state": "pending", "attempts": 1, "reason": "timeout", "status": None}),
    ("retry", {"state": "pending", "attempts": 2, "reason": "error_page", "status": None}),  # SSO's ErrorPage
    ("retry", {"state": "active", "attempts": 1, "reason": "http_503"}),
    # lab
    ("lab", {"state": "pending", "reason": "dns", "status": None}),
    ("lab", {"state": "pending", "attempts": 2, "reason": "host_unreachable", "status": None}),
    ("lab", {"state": "failed", "attempts": 3, "reason": "dns", "status": None}),
    ("lab", {"state": "failed", "reason": "host_unreachable", "status": None}),
    # js
    ("js", {"page_type": "t:js"}),
    ("js", {"page_type": "t:js", "flags": "js_hint"}),
    # click: content only a click nobody can read loads (§7.8), for round two
    ("click", {"page_type": "t:click"}),
    ("click", {"page_type": "t:click", "flags": "js_hint"}),
    # unresolved: the browser test could not tell, twice (§8.4)
    ("unresolved", {"page_type": "t:unresolved"}),
    ("unresolved", {"page_type": "t:unresolved", "flags": "js_hint"}),
    # unchecked: a page type nobody has judged is never ok
    ("unchecked", {"page_type": "t:todo"}),
    ("unchecked", {"page_type": None}),
    ("unchecked", {"page_type": "t:missing"}),  # a key with no page_type row
    ("unchecked", {"flags": "js_hint"}),
    # thin
    ("thin", {"flags": "thin"}),
    ("thin", {"flags": "empty_detail"}),
    ("thin", {**FILE, "kind0": "click", "sniff": "json", "flags": "click_empty"}),  # a tab came back empty (§7.4)
    # refused: 401, 403, 451 without a sign-in is a refusal, not a login (review note medium)
    ("refused", {"status": 403}),
    ("refused", {"status": 401}),
    ("refused", {"status": 451}),
    # http_4xx
    ("http_4xx", {"status": 400}),
    ("http_4xx", {"status": 405, "page_type": None}),
    # decode
    ("decode", {"flags": "decode"}),
    # too_large
    ("too_large", {"state": "skipped", "reason": "too_large", "size_declared": 90_000_000}),
    # failed
    ("failed", {"state": "failed", "attempts": 3, "reason": "timeout", "status": None}),
    ("failed", {"state": "failed", "attempts": 3, "reason": "http_503", "status": 503}),
    ("failed", {"state": "failed", "reason": "crash:KeyError", "status": None}),
    # server_error
    ("server_error", {"state": "failed", "attempts": 3, "reason": "error_page", "status": 302}),  # ErrorPage, used up
    ("server_error", {"status": 302, "flags": "error_page"}),
    ("server_error", {"status": 500}),
    ("server_error", {"status": 503, "page_type": None}),
    # not_file: a file or image that came back as a web page
    ("not_file", {**FILE, "sniff": "html"}),
    ("not_file", {**FILE, "kind": "page", "sniff": "html"}),  # parsed as a page, still not the file it should be
    ("not_file", {**FILE, "kind": "image", "kind0": "image", "sniff": "html"}),
    ("not_file", {**FILE, "kind0": "drive", "sniff": "html"}),
    ("not_file", {**FILE, "kind0": None, "sniff": "html"}),  # a row older than kind0: its kind stands in
    ("not_file", {**FILE, "kind0": "sitemap", "sniff": "html"}),
    ("not_file", {"kind0": "sitemap", "sniff": "html"}),  # parsed as a page: still not the sitemap it should be
    ("not_file", {**FILE, "kind0": "script", "sniff": "html"}),
    ("not_file", {**FILE, "kind0": "click", "sniff": "html"}),
    ("not_file", {**FILE, "kind0": "drive_list", "sniff": "html"}),
    # held: a cap stopped it, never silently skipped (§9)
    ("held", {"state": "held", "reason": "cap:site", "status": None}),
    ("held", {"state": "held", "reason": "cap:drive", "status": None}),
    ("held", {"state": "skipped", "reason": "site_cap", "status": None}),  # the 10-24 crawler's way of writing it
    ("held", {"state": "skipped", "reason": "cap:variant", "status": None}),
    # unknown: what the rules do not know
    ("unknown", {"state": "parked", "status": None}),
    ("unknown", {"status": None}),
    ("unknown", {"status": 204}),
    ("unknown", {"status": 101}),
    ("unknown", {"flags": "weird"}),
    ("unknown", {"flags": "login,weird"}),
    ("unknown", {"flags": " login"}),
    ("unknown", {"page_type": "t:odd"}),
    ("unknown", {"kind": "media", "kind0": "media", "page_type": None}),
    ("unknown", {**FILE, "flags": "thin"}),  # a page's flag on a file
    ("unknown", {**FILE, "kind": "image", "kind0": "image", "flags": "js_hint"}),
    # a skip no scope rule gives is not a rule (robots no longer blocks, §6.8; 10-24's quarantine; round2 is `later`)
    ("unknown", {"state": "skipped", "reason": None, "status": None}),
    ("unknown", {"state": "skipped", "reason": "robots", "status": None}),
    ("unknown", {"state": "skipped", "reason": "crash:KeyError", "status": None}),
    ("unknown", {"state": "skipped", "reason": "quarantined_dir", "status": None}),
    ("unknown", {"state": "skipped", "reason": "round2:js_site", "status": None}),
    ("unknown", {"state": "skipped", "reason": "bogus", "status": None}),
    # a file nobody sniffed: no evidence it is the file and not a web page (§7.3; the old archive's 25)
    ("unknown", {**FILE, "sniff": None}),
    ("unknown", {**FILE, "sniff": None, "ctype": "text/html"}),
    ("unknown", {**FILE, "kind": "image", "kind0": "image", "sniff": None}),
    ("unknown", {**FILE, "kind": "drive", "kind0": "drive", "sniff": None}),
    ("unknown", {**FILE, "kind": "calendar", "kind0": "calendar", "sniff": None}),
    ("unknown", {"kind0": "file", "sniff": None}),  # an expected file read as a page, never sniffed
    ("unknown", {"kind0": "image", "sniff": None, "page_type": "t:ok"}),
    ("unknown", {**FILE, "flags": "js_redirect"}),  # a file that sends the browser on: a crawler bug (§8.3)
    ("unknown", {"kind": "embed", "kind0": "embed", "sniff": None, "page_type": None}),
    ("unknown", {"kind": "embed", "kind0": "embed", "sniff": "html", "page_type": None, "flags": "js_hint"}),
]


@pytest.mark.parametrize("want, cols", URL_CASES, ids=[f"{w}-{i}" for i, (w, _c) in enumerate(URL_CASES)])
def test_url_result_table(f, want, cols):
    assert result_of(f, add_url(f, **cols)) == want


def test_every_url_result_code_has_a_case():
    # school_side needs a person's mark (review_mark), never the row alone: its own tests are below
    assert {w for w, _c in URL_CASES} == set(outcome.URL_RESULTS) - set(outcome.MARK_RESULTS)


# one row's columns per code, for the completeness tests below: a page's where the table has one (a page can be the
# row judged or a resource), else the first case
ROW_FOR = {}
for _want, _cols in sorted(URL_CASES, key=lambda case: case[1].get("kind", "page") != "page"):
    ROW_FOR.setdefault(_want, _cols)
PAGE_CODES = sorted(code for code, cols in ROW_FOR.items() if cols.get("kind", "page") == "page")

# ---------------------------------------------------------------- js_page: this page, opened by jscheck (§8.3, §8.4)

FETCHED, BEFORE, AFTER = "2026-10-26T02:00:00+00:00", "2026-10-26T01:00:00+00:00", "2026-10-26T03:00:00+00:00"


def opened(f: Frontier, uid: int, verdict: str, at: str = AFTER) -> None:
    """jscheck opened this page at `at` and saw `verdict` (js_page, design §12)."""
    f.x("INSERT OR REPLACE INTO js_page(url_id, page_type, at, verdict) VALUES (?,?,?,?)", (uid, "t:ok", at, verdict))


@pytest.mark.parametrize("ptype", ["t:ok", "t:frame"])
def test_a_page_with_a_strong_hint_waits_for_jscheck_to_open_it_whoever_judged_its_type(f, ptype):
    # the type's source (import, jscheck, manual) does not matter: this very page must be opened after its fetch
    for source in ("import", "jscheck", "manual"):
        f.x("UPDATE page_type SET source=? WHERE key=?", (source, ptype))
        p = add_url(f, page_type=ptype, flags="js_hint", fetched_at=FETCHED)
        assert result_of(f, p) == "unchecked", source
        opened(f, p, "ok", BEFORE)  # opened before its last fetch: what the browser saw is not this copy
        assert result_of(f, p) == "unchecked", source
        opened(f, p, "ok")
        assert result_of(f, p) == "ok", source


@pytest.mark.parametrize("verdict, want", [("ok", "ok"), ("frame", "ok"), ("js", "js"), ("unresolved", "unresolved"),
                                           ("click", "click"), ("weird", "unknown")])
def test_what_jscheck_saw_on_this_page_decides_after_it_opened_it(f, verdict, want):
    p = add_url(f, flags="js_hint", fetched_at=FETCHED)
    opened(f, p, verdict)
    assert result_of(f, p) == want
    q = add_url(f, fetched_at=FETCHED)  # no hint: the page's own check still counts, the more cautious wins
    opened(f, q, verdict)
    assert result_of(f, q) == want


def test_a_page_opened_before_it_was_fetched_again_is_judged_by_its_type(f):
    p = add_url(f, page_type="t:ok", fetched_at=FETCHED)
    opened(f, p, "js", BEFORE)  # the browser saw an older copy
    assert result_of(f, p) == "ok"
    assert result_of(f, add_url(f, page_type="t:ok", flags="js_hint", fetched_at=None)) == "unchecked"


def test_a_type_still_to_judge_stays_unchecked_whatever_one_page_showed(f):
    p = add_url(f, page_type="t:todo", flags="js_hint", fetched_at=FETCHED)
    opened(f, p, "ok")
    assert result_of(f, p) == "unchecked"
    q = add_url(f, page_type="t:todo", fetched_at=FETCHED)
    opened(f, q, "js")
    assert result_of(f, q) == "js"

# ---------------------------------------------------------------- a grid: never ok unless all agrees

STATES = ("pending", "active", "done", "failed", "skipped", "held", "later", "parked")
REASONS = (None, "dns", "host_unreachable", "too_large", "moved_scheme", "error_page", "site_cap", "cap:site",
           "out:login", "http_503", "timeout", "no_api_key", "odd", "robots", "decor:repeat", "album:drive",
           "media_type")
STATUSES = (None, 101, 200, 204, 206, 301, 302, 304, 400, 401, 403, 404, 405, 410, 451, 500, 503)
FLAGS = (None, "", "login", "soft404", "gone_id", "gone_redirect", "error_page", "decode", "thin", "empty_detail",
         "js_hint", "form_closed", "weird", "login,soft404", "form_closed,thin", "decode,", "js_redirect",
         "js_redirect,login", "click_empty", "gone_page", "bad_link", "bad_link,login")
KINDS = ("page", "file", "image", "drive", "calendar", "media", "embed")
KIND0S = (None, "page", "file", "image", "drive", "calendar", "form", "shortlink", "sitemap", "login_check", "click",
          "script", "drive_list", "embed")
SNIFFS = (None, "html", "pdf", "ooxml:docx", "jpeg")
PTYPES = (None, *TYPES, "t:missing")


def strictly_ok(r) -> bool:
    """What `ok` must mean: fetched with a 200, no flag but a closed form's, the file it should be (and sniffed to
    show it), and for a page a type judged by the browser test to need nothing more."""
    flags = set(filter(None, (r["flags"] or "").split(",")))
    if r["state"] != "done" or r["status"] != 200 or not flags <= {"form_closed"}:
        return False
    if (r["kind0"] or r["kind"]) == "shortlink":  # a short link is never got itself: where it leads is
        return False
    expected_file = (r["kind0"] or r["kind"]) in outcome._EXPECTED_FILE
    if (expected_file and r["sniff"] == "html") or ((expected_file or r["kind"] != "page") and r["sniff"] is None):
        return False
    if r["kind"] == "page":
        return TYPES.get(r["page_type"]) in ("ok", "frame")
    return r["kind"] in outcome.FETCHED_KINDS


def is_rule_reason(reason: str | None) -> bool:
    return reason is not None and (reason.startswith(("out:", "decor:", "album:")) or reason == "media_type")


def _grid_rows():
    rng = random.Random(20261025)
    rows = [dict(zip(("state", "reason", "attempts", "status", "flags"), combo, strict=True))
            for combo in itertools.product(STATES, REASONS, (0, 2), STATUSES, FLAGS)]
    rows += [dict(zip(("flags", "kind", "kind0", "sniff", "page_type"), combo, strict=True))
             for combo in itertools.product(FLAGS, KINDS, KIND0S, SNIFFS, PTYPES)]
    # about one in ten on Google's API host, so that api_error is reached too
    rows += [{"state": rng.choice(STATES), "reason": rng.choice(REASONS), "attempts": rng.choice((0, 1, 3)),
              "status": rng.choice(STATUSES), "flags": rng.choice(FLAGS), "kind": rng.choice(KINDS),
              "kind0": rng.choice(KIND0S), "sniff": rng.choice(SNIFFS), "page_type": rng.choice(PTYPES),
              "host": "www.googleapis.com" if rng.random() < 0.1 else "g.nknu.edu.tw"}
             for _ in range(10_000)]
    return rows


def test_any_combination_lands_on_a_known_code_and_never_on_ok_by_accident(f):
    base = {"state": "done", "reason": None, "attempts": 0, "status": 200, "flags": None, "kind": "page",
            "kind0": "page", "sniff": None, "page_type": "t:ok", "host": "g.nknu.edu.tw"}
    rows = [{**base, **r} for r in _grid_rows()]
    cols = ("url", "path", "site", "tier", "depth", "found_via", *base)
    with f.lock:
        f.conn.execute("BEGIN")  # one transaction: the frontier's connection commits each statement by itself
        f.conn.executemany(f"INSERT INTO url({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                           [(f"https://{r['host']}/{i}", f"https://{r['host']}/{i}", r["host"], 0, 0, "seed",
                             *r.values()) for i, r in enumerate(rows)])
        f.conn.execute("COMMIT")
    got = f.q("SELECT state, reason, attempts, status, flags, kind, kind0, sniff, page_type, result FROM url_result")
    assert len(got) == len(rows)
    seen = set()
    for r in got:
        assert r["result"] in outcome.URL_RESULTS, dict(r)
        assert (r["result"] == "ok") == strictly_ok(r), dict(r)
        if r["state"] == "skipped":  # a rule only for a reason the scope rules give
            assert (r["result"] == "rule") == is_rule_reason(r["reason"]), dict(r)
        seen.add(r["result"])
    assert seen == set(outcome.URL_RESULTS) - set(outcome.MARK_RESULTS)  # every code a row alone can reach


def test_an_unknown_result_turns_the_self_check_red(f):
    add_url(f)
    add_url(f, state="pending", status=None)
    assert outcome.self_check(f.conn) == {"url_result": 0, "action_result": 0, "no_owner_site": 0}
    add_url(f, flags="weird")
    add_url(f, state="parked")
    lid = add_url(f, page_role="list")
    add_action(f, lid, state="parked")
    assert outcome.self_check(f.conn) == {"url_result": 2, "action_result": 1, "no_owner_site": 0}
    assert outcome.RESULT_COLOR["unknown"] == "red"
    assert outcome.user_bucket("unknown") == "review"  # 要人看, never 抓到了


def test_a_round_one_row_that_counts_for_no_site_turns_the_self_check_red(f):
    """url.owner_site is the one site a row counts for (design §2); url.site never stands in for it (review note low)."""
    page = add_url(f, owner_site=None, url="https://sso.nknu.edu.tw/webservices/x.aspx", host="sso.nknu.edu.tw",
                   site="sso.nknu.edu.tw")
    add_url(f, **FILE, owner_site=None, tier=1)
    add_url(f, owner_site=None, tier=2, state="later", status=None)  # round two: counted where round two decides
    assert outcome.self_check(f.conn)["no_owner_site"] == 2
    assert completeness(f, page)["site"] is None  # listed, under no site, never under its host's


# ---------------------------------------------------------------- §10.1: error pages, known by where they send you

@pytest.mark.parametrize("url, name, result", [
    ("https://sso.nknu.edu.tw/errorpage/pagenotfound.aspx?aspxerrorpath=/news/news_attachedfile.aspx",
     "sso_not_found", "gone"),
    ("https://sso.nknu.edu.tw/ErrorPage/pageNotFound.aspx", "sso_not_found", "gone"),
    ("https://news.nknu.edu.tw/ErrorPage/PageNotFound.aspx?aspxerrorpath=/x.aspx", "sso_not_found", "gone"),
    ("https://sso.nknu.edu.tw/ErrorPage/ErrorPage.aspx", "sso_error", "error"),
    ("https://sso.nknu.edu.tw/errorpage/errorpage.aspx?aspxerrorpath=/news/subjectlist.aspx", "sso_error", "error"),
    # c.nknu's filenotfound.htm: an error, retried (design §10.1, record 二·4 and 二·6; review 10-26 B6 had made it gone,
    # the second look put it back: test_review_followup)
    ("https://c.nknu.edu.tw/affair/filenotfound.htm?aspxerrorpath=/affair/sitemap.aspx", "aspx_error_path", "error"),
    ("https://cacd.nknu.edu.tw/cacd/filenotfound.htm?aspxerrorpath=/cacd/activeviewlist.aspx", "aspx_error_path",
     "error"),
    ("https://c.nknu.edu.tw/aca/Default.aspx?aspxerrorpath=/aca/x.aspx", "aspx_error_path", "error"),
    ("https://c.nknu.edu.tw/aca/default.aspx?AspxErrorPath=", "aspx_error_path", "error"),
])
def test_error_signatures(url, name, result):
    sig = outcome.error_signature(url)
    assert sig is not None and (sig.name, sig.result) == (name, result)


@pytest.mark.parametrize("url", [
    "https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=8f0e2c",  # an expired notice is a notice
    "https://sso.nknu.edu.tw/News/SubjectList.aspx?cat=ALL",
    "https://c.nknu.edu.tw/affair/filenotfound.htm",  # nothing says what was missing: fetched as it is
    "https://www.example.edu.tw/errorpage/pagenotfound.aspx",  # SSO's page, not another host's
    "https://c.nknu.edu.tw/aca/page.aspx?pn=44&errorpath=/x",
    "not a url",
    "",
])
def test_ordinary_pages_are_no_error_page(url):
    assert outcome.error_signature(url) is None


def test_error_signatures_say_what_the_crawler_writes():
    gone, error = (outcome.error_signature(u) for u in ("https://sso.nknu.edu.tw/errorpage/pagenotfound.aspx",
                                                        "https://sso.nknu.edu.tw/errorpage/errorpage.aspx"))
    assert (gone.flag, gone.reason) == ("gone_redirect", None)
    assert (error.flag, error.reason) == (None, "error_page")
    assert outcome.ERROR_LINK == "error_page"
    assert all(s.flag is None or s.flag in outcome.FLAGS for s in outcome.ERROR_SIGNATURES)


def test_what_the_crawler_writes_for_error_pages_reads_as_designed(f):
    """§10.1 → §10.2: a redirect to pageNotFound is gone; a redirect to ErrorPage retries, then is a server error."""
    assert result_of(f, add_url(f, status=302, flags=outcome.error_signature(
        "https://sso.nknu.edu.tw/errorpage/pagenotfound.aspx").flag)) == "gone"
    assert result_of(f, add_url(f, state="pending", attempts=1, reason="error_page", status=None)) == "retry"
    assert result_of(f, add_url(f, state="failed", attempts=3, reason="error_page", status=None)) == "server_error"


# ---------------------------------------------------------------- §10.3: button presses


def add_action(f: Frontier, list_id: int, **cols) -> int:
    row = {"list_id": list_id, "epoch": 1, "step": 1, "kind": "detail", "queue": "ip:140.127.40.173",
           "host": "sso.nknu.edu.tw", "site": SITE, "profile": "sso_news", "form_src_warc": "warc/a.warc.gz",
           "form_src_offset": 0, "target": "gvFull", "argument": "", "row_key": f"r{next(_n)}", "state": "done",
           "status": 200}
    row.update(cols)
    with f.lock:
        return f.conn.execute(f"INSERT INTO action({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


ACTION_CASES = [
    ("ok", {"kind": "click", "row_key": "click:fileAjax.html(k=2,id=61)"}),  # ee's download button (§7.4)
    ("server_error", {"kind": "click", "status": 500, "row_key": "click:fileAjax.html(k=1,id=7)"}),
    ("todo", {"state": "pending", "status": None}),
    ("todo", {"state": "active", "status": None}),
    ("redirect", {"state": "superseded", "status": None}),  # an older epoch's: the new one carries the result
    ("failed", {"state": "failed", "status": None}),
    ("mismatch", {"state": "mismatch"}),  # the detail's title is another notice's
    ("redirect", {"status": 302, "location": "https://sso.nknu.edu.tw/news/lastnews.aspx?unid=1"}),
    ("refused", {"status": 403}),
    ("refused", {"status": 401}),
    ("refused", {"status": 451}),
    ("http_4xx", {"status": 404}),
    ("server_error", {"status": 500}),
    ("not_file", {"kind": "download", "sniff": "html"}),
    ("unknown", {"kind": "download", "sniff": None}),  # a download nobody sniffed (§7.3)
    ("ok", {"kind": "download", "sniff": "pdf"}),
    ("ok", {"kind": "pager"}),
    ("ok", {}),
    ("thin", {"kind": "detail", "reason": "empty_detail"}),  # a detail a press brought back empty (§10.1 nonempty)
    ("ok", {"kind": "pager", "reason": "empty_detail"}),  # only a detail is judged by its content
    ("unknown", {"state": "parked"}),
    ("unknown", {"status": None}),
    ("unknown", {"status": 204}),
    ("unknown", {"kind": "select"}),
]


@pytest.mark.parametrize("want, cols", ACTION_CASES, ids=[f"{w}-{i}" for i, (w, _c) in enumerate(ACTION_CASES)])
def test_action_result_table(f, want, cols):
    aid = add_action(f, add_url(f, page_role="list"), **cols)
    assert f.q("SELECT result FROM action_result WHERE id=?", (aid,))[0][0] == want


def test_every_action_result_code_has_a_case():
    assert {w for w, _c in ACTION_CASES} == set(outcome.ACTION_RESULTS)


def test_any_button_combination_lands_on_a_known_code_and_never_on_ok_by_accident(f):
    lid = add_url(f, page_role="list")
    for state, status, kind, sniff, reason in itertools.product(
            ("pending", "active", "done", "failed", "mismatch", "superseded", "parked"),
            (None, 200, 204, 302, 401, 403, 404, 451, 500), ("pager", "detail", "download", "click", "select"),
            (None, "html", "pdf"), (None, "empty_detail")):
        add_action(f, lid, state=state, status=status, kind=kind, sniff=sniff, reason=reason)
    for r in f.q("SELECT * FROM action_result"):
        assert r["result"] in outcome.ACTION_RESULTS
        ok = (r["state"] == "done" and r["status"] == 200 and r["kind"] in ("pager", "detail", "download", "click")
              and not (r["kind"] == "download" and r["sniff"] in ("html", None))
              and not (r["kind"] == "detail" and r["reason"] == "empty_detail"))
        assert (r["result"] == "ok") == ok, dict(r)


# ---------------------------------------------------------------- §10.2.1: what the user sees


def test_every_result_has_a_label_a_colour_and_a_category():
    codes = set(outcome.URL_RESULTS) | set(outcome.ACTION_RESULTS) | set(outcome.RESOURCE_RESULTS)
    assert codes == set(outcome.RESULT_LABEL) == set(outcome.RESULT_COLOR)
    assert set(outcome.RESULT_COLOR.values()) <= {"green", "yellow", "red", "none"}
    buckets = {code for code, _zh, _then in outcome.BUCKETS}
    assert {outcome.user_bucket(c) for c in codes} <= buckets


@pytest.mark.parametrize("bucket, codes", [
    ("got", ("ok",)),
    ("queued", ("todo", "retry")),
    ("failed", ("failed", "lab", "server_error", "refused", "http_4xx", "not_file")),
    ("login", ("login",)),
    ("gone", ("gone",)),
    ("pending_check", ("unchecked",)),
    ("next_round", ("later", "js", "click")),
    ("rule", ("rule", "redirect")),
    ("review", ("held", "thin", "decode", "mismatch", "not_discovered", "unknown", "too_large", "unresolved",
                "looks_gone", "api_error", "unhandled", "bad_link", "short_dead")),
    ("school_side", ("school_side",)),
])
def test_user_categories(bucket, codes):
    assert {outcome.user_bucket(c) for c in codes} == {bucket}


def test_the_user_categories_are_the_designs_nine_and_the_school_side_a_person_marked():
    # the design's nine (§10.2.1), and review 10-26 B7's 「人看过，确认是学校那边的问题」 next to 学校那边已经不存在
    assert [c for c, _zh, _then in outcome.BUCKETS] == ["got", "queued", "failed", "review", "login", "gone",
                                                        "school_side", "pending_check", "next_round", "rule"]


def test_only_ok_is_got_and_what_is_not_known_is_for_a_person_to_look_at():
    assert [c for c in outcome.RESULT_LABEL if outcome.user_bucket(c) == "got"] == ["ok"]
    assert outcome.user_bucket("something_new") == "review"
    zh = {c: zh for c, zh, _then in outcome.BUCKETS}
    assert (zh["failed"], zh["review"], zh["pending_check"]) == ("连接失败", "要人看", "待判定")


def test_the_colours_follow_the_user_categories():
    # 连接失败 (but lab) and 要人看 red (§10.2, §10.5): refused, http_4xx, thin, decode and too_large are no longer yellow
    for code in outcome.RESULT_LABEL:
        bucket = outcome.user_bucket(code)
        if bucket in ("failed", "review") and code != "lab":
            assert outcome.RESULT_COLOR[code] == "red", code
    assert {outcome.RESULT_COLOR[c] for c in ("lab", "unchecked", "js", "click", "retry")} == {"yellow"}


def test_resource_groups_split_every_result_once():
    groups = outcome.GROUPS
    every = [c for g in groups.values() for c in g]
    assert sorted(every) == sorted(set(every)) and set(every) == set(outcome.RESOURCE_RESULTS)


# ---------------------------------------------------------------- §10.4: each page's completeness


def add_res(f: Frontier, page: int, role: str = "file", *, need: str = "must", res: int | None = None,
            action: int | None = None, why: str | None = None, how: str = "get") -> None:
    f.x("INSERT INTO page_res(page_id, res_key, how, role, need, why, res_id, action_id) VALUES (?,?,?,?,?,?,?,?)",
        (page, f"k{next(_n)}", how, role, need, why, res, action))


def walk(f: Frontier, list_id: int, ended_by: str | None, ok: int | None = None) -> None:
    f.x("INSERT INTO listwalk(list_id, site, profile, epoch, pages, ended_by, ok) VALUES (?,?,?,?,?,?,?)",
        (list_id, SITE, "cnknu", 1, 3, ended_by, ok))


def res_url(f: Frontier, code: str, kind: str = "file") -> int:
    cols = dict(ROW_FOR[code])
    if code == "ok":
        cols = {**FILE, "kind": kind, "kind0": kind}
    return add_url(f, **cols)


def completeness(f: Frontier, page: int):
    rows = f.q("SELECT * FROM page_completeness WHERE id=?", (page,))
    return rows[0] if rows else None


def lv(f: Frontier, page: int) -> tuple[str, str]:
    r = completeness(f, page)
    return r["level"], r["label"]


def test_level_waiting(f):
    assert lv(f, add_url(f, **ROW_FOR["todo"])) == ("waiting", "todo")
    assert lv(f, add_url(f, **ROW_FOR["retry"])) == ("waiting", "retry")
    assert lv(f, add_url(f, **ROW_FOR["lab"])) == ("waiting", "failed")  # waits for the lab; 连接失败 for now
    lst = add_url(f, page_role="list")
    walk(f, lst, "walking")
    assert lv(f, lst) == ("waiting", "todo")


def test_level_complete_school(f):
    assert lv(f, add_url(f, **ROW_FOR["gone"])) == ("complete_school", "complete_school")
    assert lv(f, add_url(f, **ROW_FOR["login"])) == ("complete_school", "complete_school")
    p = add_url(f)
    add_res(f, p, res=res_url(f, "gone"))
    add_res(f, p, role="img", why="out:login")  # behind a sign-in, never fetched
    add_res(f, p, role="img", res=res_url(f, "ok", "image"))
    r = completeness(f, p)
    assert (r["level"], r["label"], r["n_school"], r["n_must"]) == ("complete_school", "complete_school", 2, 3)


def test_level_unchecked(f):
    assert lv(f, add_url(f, **ROW_FOR["unchecked"])) == ("unchecked", "unchecked")
    lst = add_url(f, page_role="list")  # a list nobody walked: 列表没核对, never complete, and not 待判定
    assert lv(f, lst) == ("list_unchecked", "list_unchecked")
    assert outcome.LEVEL_LABEL["list_unchecked"] == "列表没核对" and outcome.LEVEL_LABEL["unchecked"] == "待判定"


@pytest.mark.parametrize("own, label", [("js", "js"), ("thin", "thin"), ("failed", "failed"), ("held", "review"),
                                        ("refused", "failed"), ("http_4xx", "failed"), ("server_error", "failed"),
                                        ("not_file", "failed"), ("decode", "review"), ("unknown", "review"),
                                        ("unresolved", "review"), ("looks_gone", "review")])
def test_level_incomplete_for_the_page_itself(f, own, label):
    assert lv(f, add_url(f, **ROW_FOR[own])) == ("incomplete", label)


def test_a_page_whose_own_content_needs_a_click_is_partial_and_for_the_next_round(f):
    r = completeness(f, add_url(f, page_type="t:click"))
    assert (r["level"], r["label"]) == ("partial", "click")
    assert outcome.user_bucket(r["own"]) == "next_round"


@pytest.mark.parametrize("ended_by, ok", [("mismatch:no_next", 0), ("mismatch:page", None), ("mismatch:empty", 0),
                                          ("mismatch:clv2", 0), ("mismatch:total", 0), ("max_pages", 0),
                                          ("post_failed", 0), ("odd", 1), (None, None), ("last_page", 0)])
def test_level_incomplete_for_a_list_not_walked_to_the_end(f, ended_by, ok):
    lst = add_url(f, page_role="list")
    walk(f, lst, ended_by, ok)
    assert lv(f, lst) == ("incomplete", "list")


@pytest.mark.parametrize("ended_by", ["last_page", "no_rows_ok"])
def test_a_list_walked_to_the_end_is_complete(f, ended_by):
    lst = add_url(f, page_role="list")
    walk(f, lst, ended_by, 1)
    r = completeness(f, lst)
    assert (r["level"], r["label"], r["ended_by"], r["list_ok"]) == ("complete", "complete", ended_by, 1)


@pytest.mark.parametrize("role, code, label", [
    ("file", "todo", "todo"),
    ("img", "retry", "retry"),
    ("file", "lab", "failed"),
    ("file", "failed", "failed"),
    ("file", "not_file", "failed"),
    ("download", "held", "review"),
    ("file", "unknown", "review"),
    ("file", "too_large", "review"),
    ("drive", "decode", "review"),
    ("iframe", "thin", "review"),  # a thin page as an image would be not_file (review 10-26 B5)
    ("img", "later", "miss_img"),
    ("iframe", "unchecked", "miss_other"),
    ("frame", "js", "miss_other"),
    ("drive", "api_error", "review"),
    ("iframe", "looks_gone", "review"),
    ("frame", "click", "miss_other"),
    ("iframe", "unresolved", "review"),
])
def test_level_partial(f, role, code, label):
    p = add_url(f)
    add_res(f, p, role, res=add_url(f, **ROW_FOR[code]))
    assert lv(f, p) == ("partial", label)


def test_a_resource_left_for_round_two_is_partial(f):
    p = add_url(f)
    add_res(f, p, "iframe", need="round2", res=add_url(f, **ROW_FOR["later"]))  # a Canva poster off campus
    r = completeness(f, p)
    assert (r["level"], r["label"], r["n_round2"], r["n_must"]) == ("partial", "round2", 1, 0)


def test_level_complete(f):
    assert lv(f, add_url(f)) == ("complete", "complete")  # a page that uses nothing
    p = add_url(f)
    add_res(f, p, "file", res=res_url(f, "ok"))
    add_res(f, p, "img", res=res_url(f, "ok", "image"))
    add_res(f, p, "file", why="out:social")  # a share button: a rule, not a gap
    add_res(f, p, "img", need="no", why="decor_name", res=add_url(f, **ROW_FOR["failed"]))  # not needed
    lst = add_url(f, page_role="list")
    walk(f, lst, "last_page", 1)
    add_res(f, lst, "download", how="postback", action=add_action(f, lst, kind="download", sniff="pdf"))
    assert lv(f, p) == ("complete", "complete") and lv(f, lst) == ("complete", "complete")


def test_a_button_press_counts_by_its_own_result(f):
    lst = add_url(f, page_role="list")
    walk(f, lst, "last_page", 1)
    add_res(f, lst, "detail", how="postback", action=add_action(f, lst, state="mismatch"))
    add_res(f, lst, "download", how="postback", action=add_action(f, lst, state="pending", status=None))
    r = completeness(f, lst)
    assert (r["level"], r["label"], r["reasons"]) == ("partial", "todo", "todo,review,miss_file,miss_other")


@pytest.mark.parametrize("cols", [
    {"res": 999_999},  # points to no row
    {"action": 999_999},
    {},  # neither a row nor a rule: the crawler lost it
    {"why": "chrome"},
])
def test_a_resource_with_no_result_is_never_complete(f, cols):
    p = add_url(f)
    add_res(f, p, "file", **cols)
    assert lv(f, p) == ("partial", "review")


def test_a_need_the_rules_do_not_know_is_never_complete(f):
    p = add_url(f)
    add_res(f, p, "file", need="maybe", res=res_url(f, "ok"))
    assert lv(f, p) == ("partial", "review")


def test_a_frame_type_without_its_frame_is_partial(f):
    p = add_url(f, page_type="t:frame")
    assert lv(f, p) == ("partial", "miss_other")
    add_res(f, p, "frame", res=add_url(f))  # the frame page, fetched
    assert lv(f, p) == ("complete", "complete")


def test_what_a_school_frame_page_lacks_its_page_lacks_too(f):
    # record 二·4: 抓到了 is 正常抓到（框架里的内容也抓到了）; record 第三节: content in frames counts only once the
    # frame pages are fetched with it. The frame page's attachments and images are its own rows (design §4.4), so the
    # page that holds the frame counts them as well (review 10-26: it showed complete while its frame page lacked one)
    parent = add_url(f, page_type="t:frame")
    frame = add_url(f, leaf=0)  # the school frame page, fetched (a round-1 row: the page's tier)
    add_res(f, parent, "frame", res=frame)
    assert lv(f, parent) == ("complete", "complete")
    pdf = add_url(f, **ROW_FOR["failed"])
    add_res(f, frame, "file", res=pdf)
    assert lv(f, frame) == ("partial", "failed")
    r = completeness(f, parent)
    assert (r["level"], r["label"], r["reasons"]) == ("partial", "failed", "failed,miss_file")
    # a frame inside the frame page (a frameset's frame holding an iframe): its gaps count on the top page too
    inner = add_url(f, leaf=0)
    add_res(f, frame, "iframe", res=inner)
    add_res(f, inner, "img", res=add_url(f, **ROW_FOR["todo"]))
    assert completeness(f, parent)["n_todo"] == 1
    # each resource once, however many frames lead to it; and frames that frame each other end
    add_res(f, parent, "file", res=pdf)
    add_res(f, inner, "frame", res=frame)
    r = completeness(f, parent)
    assert (r["n_failed"], r["n_todo"], r["level"]) == (1, 1, "partial")


def test_a_frame_page_off_this_round_or_not_a_page_brings_nothing_of_its_own(f):
    # only a round-1 page (kind page, tier 0 or 1) is looked into: an off-campus frame waits for round two by itself
    parent = add_url(f)
    later = add_url(f, **ROW_FOR["later"], kind="page")
    add_res(f, parent, "iframe", need="round2", res=later)
    add_res(f, later, "file", res=add_url(f, **ROW_FOR["failed"]))  # nothing round one fetches has it
    r = completeness(f, parent)
    assert (r["level"], r["label"], r["n_failed"]) == ("partial", "round2", 0)


def test_a_page_the_browser_saw_keep_its_content_in_frames_needs_its_frames(f):
    p = add_url(f, page_type="t:ok", fetched_at=FETCHED)
    opened(f, p, "frame")
    assert lv(f, p) == ("partial", "miss_other")
    add_res(f, p, "iframe", res=add_url(f))
    assert lv(f, p) == ("complete", "complete")


# ---------------------------------------------------------------- clicks, unhandled writings, Drive (§7.1, §7.6, §7.8)


@pytest.mark.parametrize("how, why, want", [
    ("click", "round2:click", ("partial", "click")),  # a click nobody can read: the next round presses it
    ("unhandled", "round2:unhandled", ("partial", "click")),  # the platform file leaves the writing for round two
    ("click", "click", ("partial", "review")),  # round2 without its round2: prefix: nothing says what came of it
])
def test_a_click_left_for_the_next_round_makes_the_page_partial(f, how, why, want):
    p = add_url(f)
    add_res(f, p, "click", how=how, need="round2", why=why)
    assert lv(f, p) == want


def test_a_writing_nobody_has_decided_about_is_never_complete(f):
    """The English site's home page: news tabs whose handler is in an external .js and whose list is empty in the
    page as archived (design §7.8): until R7 decides, the page is for a person to look at, never complete."""
    p = add_url(f)
    add_res(f, p, "click", how="unhandled", need="must", why="unhandled:unknown")
    r = completeness(f, p)
    assert (r["level"], r["label"], r["n_review"], r["n_bad"]) == ("partial", "review", 1, 1)
    q = add_url(f)
    add_res(f, q, "click", how="unhandled", need="must", why="unhandled:google")  # a Google address not read
    assert lv(f, q) == ("partial", "review")
    ok = add_url(f)
    add_res(f, ok, "click", how="unhandled", need="no", why="unhandled:ignore")  # decided: only the sort order
    add_res(f, ok, "click", how="click", need="no", why="click:shown_on_load")
    add_res(f, ok, "click", how="click", need="no", why="click:plain_href")
    add_res(f, ok, "script", need="no", why="script")  # a school script: read for its clicks, never content
    assert lv(f, ok) == ("complete", "complete")


def test_a_google_document_published_to_the_web_is_for_the_next_round(f):
    p = add_url(f)
    add_res(f, p, "drive", how="drive_api", need="round2", why="round2:google_published")
    assert lv(f, p) == ("partial", "round2")


def test_a_drive_item_read_without_its_content_row_is_lost(f):
    """§7.6: the metadata row came back fine, but nobody made the row of its content: never complete."""
    meta = add_url(f, **{**FILE, "kind": "drive", "kind0": "drive", "sniff": "json"})
    p = add_url(f)
    add_res(f, p, "drive", how="drive_api", res=meta)
    r = completeness(f, p)
    assert (r["level"], r["label"], r["miss_file"]) == ("partial", "review", 1)
    q = add_url(f)
    add_res(f, q, "drive", how="drive_api", res=add_url(f, **FILE))  # pointed at the content row instead
    assert lv(f, q) == ("complete", "complete")
    failed_meta = add_url(f, **{**FILE, "kind": "drive", "kind0": "drive", **ROW_FOR["failed"]})
    m = add_url(f)
    add_res(f, m, "drive", how="drive_api", res=failed_meta)
    assert lv(f, m) == ("partial", "failed")  # the metadata itself failed: its own result


def test_a_tab_pressed_by_its_platform_rule_counts_by_what_came_back(f):
    page = add_url(f)
    add_res(f, page, "click", how="click", res=add_url(f, **{**FILE, "kind0": "click", "sniff": "json"}))
    assert lv(f, page) == ("complete", "complete")
    failed = add_url(f)
    add_res(f, failed, "click", how="click", res=add_url(f, **{**FILE, "kind0": "click", **ROW_FOR["failed"]}))
    assert lv(f, failed) == ("partial", "failed")
    empty = add_url(f)
    add_res(f, empty, "click", how="click", res=add_url(f, **{**FILE, "kind0": "click", "sniff": "json",
                                                              "flags": "click_empty"}))
    assert lv(f, empty) == ("partial", "review")
    button = add_url(f)  # ee's download button: a POST whose answer is not a file's address
    add_res(f, button, "click", how="click", action=add_action(f, button, kind="click", state="mismatch"))
    assert lv(f, button) == ("partial", "review")


def test_one_press_per_site_and_button(f):
    a, b = add_url(f), add_url(f)
    add_action(f, a, kind="click", row_key="click:fileAjax.html(k=2,id=61)")
    with pytest.raises(Exception, match="UNIQUE"):
        add_action(f, b, kind="click", row_key="click:fileAjax.html(k=2,id=61)")  # the same button on another page
    add_action(f, b, kind="click", row_key="click:fileAjax.html(k=1,id=61)")  # (k, id) together: another file


def test_a_page_that_sends_the_browser_on_is_judged_where_it_lands(f):
    """§8.3: a script or meta refresh that sends the browser on is a redirect: the page is judged where it lands."""
    end = add_url(f)
    start = add_url(f, flags="js_redirect", final_url=url_of(f, end))
    assert completeness(f, start) is None and lv(f, end) == ("complete", "complete")
    later = add_url(f, tier=2, state="later", reason="round2:offsite", status=None)
    gone_on = add_url(f, flags="js_redirect", final_url=url_of(f, later))
    r = completeness(f, gone_on)
    assert (r["own"], r["level"], r["label"]) == ("later", "incomplete", "round2")
    lost = add_url(f, flags="js_redirect")  # no target kept: never complete
    assert completeness(f, lost)["own"] == "not_discovered"


def test_a_type_the_browser_could_not_judge_is_for_a_person_not_pending(f):
    r = completeness(f, add_url(f, page_type="t:unresolved"))
    assert (r["level"], r["label"]) == ("incomplete", "review")
    assert outcome.user_bucket(r["own"]) == "review"


# ---------------------------------------------------------------- redirects: judged where they land (§7.1)


def url_of(f: Frontier, uid: int) -> str:
    return f.q("SELECT url FROM url WHERE id=?", (uid,))[0][0]


def moved(f: Frontier, to: int | str | None, **cols) -> int:
    """A row answered with a 302 to the row `to` (or to an address no row holds); final_url is the target."""
    target = url_of(f, to) if isinstance(to, int) else to
    return add_url(f, **{"status": 302, "page_type": None, "final_url": target, **cols})


def test_a_redirected_resource_counts_by_its_target_without_a_copied_row(f):
    """The view follows the redirect itself: a crawler that forgot to copy the page_res row to the target cannot make
    a missing file look fetched (review note medium)."""
    p = add_url(f)
    target = add_url(f, **{**FILE, **ROW_FOR["failed"]})
    add_res(f, p, "file", res=moved(f, target, **FILE))
    assert lv(f, p) == ("partial", "failed")
    f.x("UPDATE url SET state='done', status=200, reason=NULL WHERE id=?", (target,))
    assert lv(f, p) == ("complete", "complete")


def test_a_copied_row_and_its_original_count_once(f):
    """§7.1's copy and the original both lead to the one target: one resource, not two."""
    p = add_url(f)
    target = add_url(f, **{**FILE, **ROW_FOR["failed"]})
    add_res(f, p, "file", res=moved(f, target, **FILE))
    add_res(f, p, "file", res=target)
    r = completeness(f, p)
    assert (r["level"], r["label"], r["n_must"], r["n_bad"], r["miss_file"]) == ("partial", "failed", 1, 1, 1)


def test_a_chain_of_redirects_counts_by_where_it_lands(f):
    p = add_url(f)
    end = add_url(f, **{**FILE, **ROW_FOR["failed"]})
    add_res(f, p, "file", res=moved(f, moved(f, moved(f, end, **FILE), **FILE), **FILE))
    assert lv(f, p) == ("partial", "failed")
    f.x("UPDATE url SET state='done', status=200, reason=NULL WHERE id=?", (end,))
    r = completeness(f, p)
    assert (r["level"], r["n_must"]) == ("complete", 1)


@pytest.mark.parametrize("verdict, level, label", [
    (None, "partial", "review"),  # led nowhere the crawler kept: never fetched
    ("queued", "partial", "review"),  # queued, yet no row: lost
    ("out:login", "complete_school", "complete_school"),
    # a file sent where a rule fetches nothing is not got (second look F3), but for a video or a sound: the rule's own
    ("out:social", "partial", "failed"),
    ("out:media", "complete", "complete"),
])
def test_a_resource_redirected_to_no_row_counts_by_the_link_verdict(f, verdict, level, label):
    p = add_url(f)
    r = moved(f, "https://accounts.google.com/x", **FILE)
    if verdict:
        f.link(r, "https://accounts.google.com/x", "[redirect]", verdict)
    add_res(f, p, "file", res=r)
    assert lv(f, p) == (level, label)


def test_a_resource_redirected_without_a_target_address_is_never_complete(f):
    p = add_url(f)
    add_res(f, p, "file", res=moved(f, None, **FILE))  # a 3xx whose Location the crawler did not keep
    add_res(f, p, "img", res=add_url(f, **{**FILE, "state": "skipped", "reason": "moved_scheme", "status": None}))
    r = completeness(f, p)
    assert (r["level"], r["label"], r["n_review"]) == ("partial", "review", 2)


def test_a_redirect_loop_never_lands_and_is_never_complete(f):
    p = add_url(f)
    a = add_url(f, **{**FILE, "status": 302})
    b = moved(f, a, **FILE)
    f.x("UPDATE url SET final_url=? WHERE id=?", (url_of(f, b), a))
    add_res(f, p, "file", res=a)
    assert lv(f, p) == ("partial", "failed")  # a loop is a server error


def test_a_press_answered_with_a_redirect_counts_by_where_it_led(f):
    """§10.3: the Location of a press's 3xx is a url row of its own; the press counts by it (and by its redirects)."""
    lst = add_url(f, page_role="list")
    walk(f, lst, "last_page", 1)
    file_ = add_url(f, **{**FILE, **ROW_FOR["failed"]})
    hop = moved(f, file_, **FILE)
    add_res(f, lst, "download", how="postback", action=add_action(f, lst, kind="download", status=302,
                                                                    location=url_of(f, hop), sniff=None))
    assert lv(f, lst) == ("partial", "failed")
    f.x("UPDATE url SET state='done', status=200, reason=NULL WHERE id=?", (file_,))
    assert lv(f, lst) == ("complete", "complete")


@pytest.mark.parametrize("verdict, level", [(None, "partial"), ("out:login", "complete_school"),
                                            ("out:social", "partial"),  # a download sent where no file comes from
                                            ("out:media", "complete")])
def test_a_press_redirected_to_no_row_counts_by_the_list_pages_link(f, verdict, level):
    lst = add_url(f, page_role="list")
    walk(f, lst, "last_page", 1)
    to = "https://webmail.nknu.edu.tw/login"
    if verdict:
        f.link(lst, to, "[redirect]", verdict)  # the list page links where its press led (§7.5)
    add_res(f, lst, "download", how="postback", action=add_action(f, lst, kind="download", status=302, location=to))
    assert lv(f, lst)[0] == level


def test_a_replaced_press_counts_by_its_replacement(f):
    """§7.5 step 9: a restart supersedes the old walk's unfinished presses; the newest press of the row counts."""
    lst = add_url(f, page_role="list")
    walk(f, lst, "last_page", 1)
    old = add_action(f, lst, kind="download", sniff="pdf", row_key="r-same", epoch=1, state="superseded", status=None)
    add_res(f, lst, "download", how="postback", action=old)
    assert lv(f, lst) == ("partial", "review")  # nothing stands for it yet: not complete
    new = add_action(f, lst, kind="download", sniff="pdf", row_key="r-same", epoch=2, state="pending", status=None)
    assert lv(f, lst) == ("partial", "todo")
    f.x("UPDATE action SET state='done', status=200 WHERE id=?", (new,))
    add_res(f, lst, "download", how="postback", action=new)  # the new walk's own row: still one resource
    r = completeness(f, lst)
    assert (r["level"], r["n_must"]) == ("complete", 1)
    add_action(f, lst, kind="download", sniff="pdf", row_key="r-same", epoch=3, state="superseded", status=None)
    assert lv(f, lst) == ("partial", "review")  # the newest is replaced too, by nothing yet


def test_a_page_that_redirects_to_a_judged_page_is_judged_there_once(f):
    end = add_url(f, **ROW_FOR["failed"])
    hop = moved(f, end)
    start = moved(f, hop)
    assert completeness(f, start) is None and completeness(f, hop) is None
    assert lv(f, end) == ("incomplete", "failed")


@pytest.mark.parametrize("target, own, level, label", [
    ({"tier": 2, "state": "later", "reason": "round2:offsite", "status": None}, "later", "incomplete", "round2"),
    ({"state": "later", "reason": "round2:js_site", "status": None}, "later", "incomplete", "round2"),
    ({"tier": 2, "state": "pending", "status": None}, "todo", "waiting", "todo"),
    ({**FILE}, "ok", "complete", "complete"),  # a page address that now serves the PDF
    ({**FILE, **ROW_FOR["failed"]}, "failed", "incomplete", "failed"),
    ({"state": "skipped", "reason": "out:media", "status": None}, "rule", "incomplete", "review"),
    ({"state": "skipped", "reason": "too_large", "status": None}, "too_large", "incomplete", "review"),
])
def test_a_page_redirected_where_round_one_judges_nothing_stays_and_counts_by_its_target(f, target, own, level, label):
    """A round-1 page whose redirects lead to a second-round row, a file or what the rules leave out is not dropped from
    the completeness table: it counts by where it landed (review note high)."""
    start = moved(f, add_url(f, **target))
    r = completeness(f, start)
    assert r is not None and (r["own"], r["level"], r["label"]) == (own, level, label)


def test_a_page_redirected_to_a_second_round_page_counts_that_pages_resources(f):
    end = add_url(f, tier=2)  # fetched as it is, though its site is round two
    add_res(f, end, "file", res=add_url(f, **{**FILE, **ROW_FOR["failed"]}))
    start = moved(f, end)
    r = completeness(f, start)
    assert (r["own"], r["level"], r["label"], r["content_id"]) == ("ok", "partial", "failed", end)
    assert completeness(f, end) is None  # tier 2: judged only through the round-1 page


@pytest.mark.parametrize("verdict, own, level", [
    (None, "not_discovered", "incomplete"),
    ("out:login", "login", "complete_school"),
    ("out:offsite", "rule", "incomplete"),
])
def test_a_page_redirected_to_no_row_counts_by_the_link_verdict(f, verdict, own, level):
    to = "https://www.facebook.com/nknu"
    start = moved(f, to)
    if verdict:
        f.link(start, to, "[redirect]", verdict)
    r = completeness(f, start)
    assert (r["own"], r["level"]) == (own, level)


@pytest.mark.parametrize("cols", [
    {"status": 302, "final_url": None},  # the Location was not kept
    {"state": "skipped", "reason": "moved_scheme", "status": None},  # §6.6 before step 4 writes its final_url
])
def test_a_page_redirected_to_an_address_not_kept_is_listed(f, cols):
    r = completeness(f, add_url(f, **cols))
    assert (r["own"], r["level"], r["label"]) == ("not_discovered", "incomplete", "review")


def test_a_page_moved_to_https_is_judged_on_the_https_row(f):
    https = add_url(f, url="https://c.nknu.edu.tw/aca/moved.aspx")
    http = add_url(f, url="http://c.nknu.edu.tw/aca/moved.aspx", state="skipped", reason="moved_scheme", status=None,
                   final_url="https://c.nknu.edu.tw/aca/moved.aspx")
    assert completeness(f, http) is None and lv(f, https) == ("complete", "complete")


def test_a_page_redirect_loop_lists_both_pages(f):
    a = add_url(f, status=302, page_type=None)
    b = moved(f, a)
    f.x("UPDATE url SET final_url=? WHERE id=?", (url_of(f, b), a))
    for page in (a, b):
        r = completeness(f, page)
        assert (r["own"], r["level"], r["label"]) == ("server_error", "incomplete", "failed")


def test_a_round_one_page_drops_out_only_where_another_row_is_judged_for_it(f):
    """Random redirects among round-1 pages, second-round rows, files, ruled-out pages and an address no row holds,
    loops included: every round-1 page is in the table, unless the scope sends it to round two or leaves it out, or its
    redirects land on a round-1 page judged in its own right; one that stays counts by where it landed."""
    rng = random.Random(11)
    kinds = {"ok": {}, "failed": ROW_FOR["failed"], "later": ROW_FOR["later"], "rule": ROW_FOR["rule"],
             "tier2": {"tier": 2}, "file": FILE, "redirect": {"status": 302, "page_type": None}}
    what = {add_url(f, **kinds[k]): k for k in (rng.choice((*kinds, "redirect", "redirect")) for _ in range(400))}
    url = {uid: url_of(f, uid) for uid in what}
    by_url = {u: uid for uid, u in url.items()}
    nowhere = "https://nowhere.nknu.edu.tw/x"
    hops, anywhere = [url[uid] for uid, k in what.items() if k == "redirect"], list(url.values())

    def target() -> str:  # many chains, some loops, some to nowhere
        roll = rng.random()
        return nowhere if roll < 0.1 else rng.choice(hops) if roll < 0.5 else rng.choice(anywhere)

    final = {uid: target() for uid, k in what.items() if k == "redirect"}
    a, b, c = list(final)[:3]
    final.update({a: url[b], b: url[a], c: url[c]})  # a loop of two and one to itself, whatever else came out
    for uid, to in final.items():
        f.x("UPDATE url SET final_url=? WHERE id=?", (to, uid))

    def lands(uid: int) -> int | str:
        for _ in range(outcome.MAX_HOPS):
            if what[uid] != "redirect":
                return uid
            if final[uid] not in by_url:
                return "nowhere"
            uid = by_url[final[uid]]
        return uid if what[uid] != "redirect" else "loop"

    own_of = {"ok": "ok", "failed": "failed", "later": "later", "rule": "rule", "tier2": "ok", "file": "ok",
              "nowhere": "not_discovered", "loop": "server_error"}
    rows = {r["id"]: r for r in f.q("SELECT * FROM page_completeness")}
    for uid, k in what.items():
        end = lands(uid) if k == "redirect" else uid
        end_kind = end if isinstance(end, str) else what[end]
        judged_elsewhere = k == "redirect" and end_kind in ("ok", "failed")
        assert (uid in rows) == (k in ("ok", "failed", "redirect") and not judged_elsewhere), (uid, k, end_kind)
        if uid in rows:
            assert rows[uid]["own"] == own_of[end_kind], (uid, k, end_kind)
    assert {"nowhere", "loop"} <= {lands(uid) for uid, k in what.items() if k == "redirect"}  # both were reached


@pytest.mark.parametrize("cols", [
    {"state": "skipped", "reason": "site_cap", "status": None},  # how the 10-24 crawler wrote a cap
    {"state": "skipped", "reason": "depth", "status": None},
    {"state": "skipped", "reason": "variant_cap", "status": None},
    {"state": "skipped", "reason": "cap:site", "status": None},
    {"state": "held", "reason": "cap:depth", "status": None},
])
def test_a_page_stopped_by_a_cap_is_listed_for_review(f, cols):
    r = completeness(f, add_url(f, **cols))
    assert (r["own"], r["level"], r["label"]) == ("held", "incomplete", "review")


@pytest.mark.parametrize("reason", ["robots", None, "crash:KeyError", "quarantined_dir"])
def test_a_page_skipped_for_no_rule_is_listed_for_review(f, reason):
    r = completeness(f, add_url(f, state="skipped", reason=reason, status=None))
    assert (r["own"], r["level"], r["label"]) == ("unknown", "incomplete", "review")


@pytest.mark.parametrize("reason", ["robots", None, "crash:KeyError", "bogus"])
def test_a_resource_skipped_for_no_rule_is_never_complete(f, reason):
    p = add_url(f)
    add_res(f, p, "file", res=add_url(f, **{**FILE, "state": "skipped", "reason": reason, "status": None}))
    assert lv(f, p) == ("partial", "review")


def test_labels_keep_every_reason_in_order(f):
    p = add_url(f, page_type="t:todo")  # not judged yet
    add_res(f, p, "img", res=add_url(f, **{**FILE, "kind": "image", **ROW_FOR["failed"]}))
    add_res(f, p, "file", res=add_url(f, **ROW_FOR["too_large"]))
    add_res(f, p, "img", res=add_url(f, **ROW_FOR["later"]))
    r = completeness(f, p)
    assert (r["level"], r["label"]) == ("unchecked", "failed")
    assert r["reasons"] == "failed,review,miss_file,miss_img,round2,unchecked"
    assert (r["miss_file"], r["miss_img"], r["n_bad"], r["n_round2"]) == (1, 2, 2, 1)


@pytest.mark.parametrize("cols", [
    ROW_FOR["later"], ROW_FOR["rule"], ROW_FOR["too_large"], {"state": "skipped", "reason": "decor:repeat"},
    {"tier": 2}, FILE, {"kind": "image", "kind0": "image", "page_type": None, "sniff": "jpeg"},
])
def test_what_is_not_a_round_one_page_is_not_judged(f, cols):
    assert completeness(f, add_url(f, **cols)) is None


def test_every_level_and_label_is_reached_and_named():
    assert set(outcome.LEVEL_LABEL) == {"waiting", "complete_school", "unchecked", "list_unchecked", "incomplete",
                                        "partial", "complete"}
    assert [c for c, _zh in outcome.LABEL_ORDER] == [
        "todo", "retry", "failed", "thin", "review", "js", "list", "list_unchecked", "miss_file", "miss_img",
        "miss_other", "click", "round2", "unchecked", "complete_school", "complete"]
    assert len({c for c, _zh in outcome.LABEL_ORDER}) == len(outcome.LABEL_ORDER) == 16
    zh = dict(outcome.LABEL_ORDER)
    assert (zh["thin"], zh["js"], zh["click"], zh["round2"], zh["unchecked"]) == (
        "要人看：页面几乎是空的", "要 JavaScript（下一轮补）", "点了才载入的内容（下一轮补）", "有下一轮补的资源", "待判定")


def test_random_pages_have_a_known_level_and_complete_means_complete(f):
    """Any mix of a page's own result, its list walk and its resources: a known level and label; `complete` only when
    the page is ok and each needed resource is fetched or ruled out (and the label agrees with the level)."""
    rng = random.Random(7)
    pool = {code: add_url(f, **{**cols, "tier": 2}) for code, cols in ROW_FOR.items()}  # tier 2: not judged
    good = set(outcome.GROUPS["good"])
    expect = {}
    for _ in range(400):
        own = rng.choice(sorted(set(PAGE_CODES) - {"later", "rule", "redirect", "too_large"}))
        page = add_url(f, **ROW_FOR[own], page_role=rng.choice((None, "detail", "list")))
        if rng.random() < 0.5:
            walk(f, page, rng.choice(("last_page", "no_rows_ok", "walking", "mismatch:page", "post_failed")),
                 rng.choice((None, 0, 1)))
        needed = []
        for _ in range(rng.randrange(4)):
            code = rng.choice(sorted(pool))
            need = rng.choice(("must", "must", "round2", "no"))
            add_res(f, page, rng.choice(("file", "img", "iframe", "download", "drive")), need=need, res=pool[code])
            needed.append((need, code))
        expect[page] = (own, needed)
    levels = set(outcome.LEVEL_LABEL)
    labels = {c for c, _zh in outcome.LABEL_ORDER}
    rows = {r["id"]: r for r in f.q("SELECT * FROM page_completeness")}
    assert set(rows) == set(expect)  # the pool's rows are tier 2: not judged
    for page, (own, needed) in expect.items():
        r = rows[page]
        assert r["level"] in levels and r["label"] in labels
        assert (r["level"] == "complete") == (r["label"] == "complete")
        if r["level"] == "complete":
            assert own == "ok" and all(c in good - {"gone", "login"} for need, c in needed if need == "must")
            assert not any(need == "round2" for need, _c in needed)
            assert r["ended_by"] in (None, "last_page", "no_rows_ok") and r["list_ok"] in (None, 1)


# ---------------------------------------------------------------- the 10-26 review (research/round1_review_2026-10-26)

FILE_LIKE = ("file", "download", "drive", "img")


@pytest.mark.parametrize("role", FILE_LIKE)
def test_an_attachment_that_lands_on_a_web_page_is_not_got(f, role):
    """B5: elec's .doc and english's .pdf redirect to their site's home page, which is fetched and ok; the attachment
    is still not got (应该是档案，回来是网页), however good the page it lands on."""
    p = add_url(f)
    home = add_url(f)  # the site's home page: a round-1 page, ok
    add_res(f, p, role, res=moved(f, home, **FILE))
    r = completeness(f, p)
    assert (r["level"], r["label"], r["n_failed"]) == ("partial", "failed", 1)
    assert (r["miss_img"] if role == "img" else r["miss_file"]) == 1
    two = add_url(f)  # two hops, the same
    add_res(f, two, role, res=moved(f, moved(f, home, **FILE), **FILE))
    assert lv(f, two) == ("partial", "failed")


def test_a_download_press_answered_with_a_web_page_is_not_got(f):
    lst = add_url(f, page_role="list")
    walk(f, lst, "last_page", 1)
    home = add_url(f)
    add_res(f, lst, "download", how="postback", action=add_action(f, lst, kind="download", status=302,
                                                                    location=url_of(f, home), sniff=None))
    assert lv(f, lst) == ("partial", "failed")


def test_an_attachment_landing_on_a_page_that_is_not_there_keeps_that_result(f):
    """Landing on a page counts as not_file only when a web page came back: a 404, a sign-in, a page still waiting or a
    page not judged yet say more about it."""
    for code, want in (("gone", ("complete_school", "complete_school")),
                       ("login", ("complete_school", "complete_school")), ("todo", ("partial", "todo")),
                       ("failed", ("partial", "failed")), ("looks_gone", ("partial", "review"))):
        p = add_url(f)
        add_res(f, p, "file", res=moved(f, add_url(f, **ROW_FOR[code]), **FILE))
        assert lv(f, p) == want, code


def test_a_frame_or_a_link_that_lands_on_a_web_page_is_got(f):
    """A frame page, a tab's address or a short link that moved to a web page: nothing along the way was expected to
    be a file (one that was is not got: test_review_followup)."""
    for role in ("iframe", "frame", "click", "shortlink"):
        p = add_url(f)
        add_res(f, p, role, res=moved(f, add_url(f, tier=2)))
        assert lv(f, p) == ("complete", "complete"), role


def test_a_file_that_lands_on_a_file_is_got(f):
    p = add_url(f)
    add_res(f, p, "file", res=moved(f, add_url(f, **FILE), **FILE))
    assert lv(f, p) == ("complete", "complete")


def test_the_school_saying_a_file_does_not_exist_is_gone_and_its_broken_link_is_for_a_person(f):
    """B6: SSO answers 200 with a page 「附件無法下載：檔案不存在」. The platform file (phase 3) reads it: gone_page
    once seen twice (the first time it is retried later), bad_link when the page says 「檔案包含不支援的字元」 (the
    school's own link is broken)."""
    assert result_of(f, add_url(f, **{**FILE, "sniff": "html", "flags": "gone_page"})) == "gone"
    assert result_of(f, add_url(f, **{**FILE, "sniff": "html", "flags": "bad_link"})) == "bad_link"
    assert result_of(f, add_url(f, **{**FILE, "state": "pending", "attempts": 1, "reason": "gone_page",
                                      "status": None})) == "retry"
    assert outcome.RESULT_LABEL["bad_link"] == "学校的链接坏了"
    assert outcome.user_bucket("bad_link") == "review" and outcome.RESULT_COLOR["bad_link"] == "red"
    p = add_url(f)
    add_res(f, p, res=add_url(f, **{**FILE, "flags": "gone_page"}))
    assert lv(f, p) == ("complete_school", "complete_school")


# ---- B7: 「人看过，确认是学校那边的问题」


def mark(f: Frontier, uid: int, note: str = "人看过：学校的链接本身坏了") -> None:
    f.mark_school_side(uid, note)


# a failed row may be marked when the server refused every connection or kept failing (outcome.markable)
MARKABLE_ROW = {**ROW_FOR, "failed": {"state": "failed", "attempts": 3, "reason": "connect", "status": None}}


@pytest.mark.parametrize("code", outcome.SCHOOL_SIDE_RESULTS)
def test_a_school_side_result_a_person_marked_counts_as_the_schools(f, code):
    uid = add_url(f, **MARKABLE_ROW[code])
    assert result_of(f, uid) == code
    p = add_url(f)
    add_res(f, p, res=uid)
    mark(f, uid)
    assert result_of(f, uid) == "school_side"
    assert lv(f, p) == ("complete_school", "complete_school")
    assert outcome.user_bucket("school_side") == "school_side"  # listed apart, never 抓到了
    if completeness(f, uid) is not None:  # the row is a round-1 page itself
        assert lv(f, uid) == ("complete_school", "complete_school")


@pytest.mark.parametrize("code", ["not_discovered", "unknown", "unhandled", "mismatch", "ok", "todo", "retry", "held",
                                  "too_large", "later", "js", "unchecked", "redirect", "rule"])
def test_only_the_schools_results_can_be_marked(f, code):
    """B7: what is ours to fix (a crawler bug, a combination nobody knows, a writing nobody decided, a press that may
    be another notice) and what is not a problem at all is never marked."""
    assert code not in outcome.SCHOOL_SIDE_RESULTS
    if code in ROW_FOR:
        uid = add_url(f, **ROW_FOR[code])
        with pytest.raises(ValueError, match="school"):
            mark(f, uid)
        assert result_of(f, uid) == code
    with pytest.raises(ValueError):
        mark(f, 10_000_000)  # no such row


@pytest.mark.parametrize("change", [{"status": 500}, {"reason": "http_503"}, {"flags": None, "sniff": "pdf"}])
def test_a_mark_lapses_when_the_result_reason_or_status_changes(f, change):
    uid = add_url(f, **{**FILE, "flags": "bad_link"})
    p = add_url(f)
    add_res(f, p, res=uid)
    mark(f, uid)
    assert result_of(f, uid) == "school_side"
    sets = ", ".join(f"{k}=?" for k in change)
    f.x(f"UPDATE url SET {sets} WHERE id=?", (*change.values(), uid))
    assert result_of(f, uid) != "school_side"
    assert lv(f, p)[0] != "complete_school" or result_of(f, uid) in outcome.SCHOOL
    # fetched again with the first answer (the crawler writes every fetch through finish, with its time): the mark
    # was for the fetch a person looked at and does not come back (second look F5 low)
    f.finish(uid, state="done", status=200, reason=None, flags="bad_link", sniff="pdf")
    assert result_of(f, uid) == "bad_link"


def test_a_marked_row_keeps_its_mark_and_note_for_the_report(f):
    uid = add_url(f, **ROW_FOR["api_error"])
    mark(f, uid, "Drive 档案不公开，单位说不会再开")
    row = f.q("SELECT * FROM review_mark WHERE url_id=?", (uid,))[0]
    assert (row["mark"], row["result"], row["status"], row["note"]) == ("school_side", "api_error", 404,
                                                                        "Drive 档案不公开，单位说不会再开")
    mark(f, uid, "再看一次")  # marked again: the newest note and evidence
    assert f.q("SELECT note FROM review_mark WHERE url_id=?", (uid,))[0][0] == "再看一次"


# ---- short links (A2): a dead one is not 学校那边不存在; one whose target nobody can read is for a person

def test_short_link_results(f):
    assert result_of(f, add_url(f, **SHORT_DEAD)) == "short_dead"
    assert result_of(f, add_url(f, **{**SHORT_DEAD, "url": "https://bit.ly/x", "status": 410})) == "short_dead"
    assert result_of(f, add_url(f, **{**SHORT, "url": "https://bit.ly/y", "status": 301,
                                      "final_url": "https://c.nknu.edu.tw/aca/"})) == "redirect"
    # a 200 page whose target was read (meta refresh, reurl's input#target): a redirect to it
    assert result_of(f, add_url(f, **{**SHORT, "final_url": "https://c.nknu.edu.tw/aca/x.pdf"})) == "redirect"
    # a 200 page nobody could read a target from: unhandled:shortlink, for a person
    assert result_of(f, add_url(f, **{**SHORT, "url": "https://reurl.cc/none", "reason": "unhandled:shortlink"})) \
        == "unhandled"
    assert outcome.RESULT_LABEL["short_dead"] == "短网址已失效" and outcome.user_bucket("short_dead") == "review"


def test_a_short_link_counts_by_where_it_leads(f):
    p = add_url(f)
    target = add_url(f, **FILE)
    add_res(f, p, "shortlink", res=add_url(f, **{**SHORT, "final_url": url_of(f, target)}))
    assert lv(f, p) == ("complete", "complete")
    dead = add_url(f)
    add_res(f, dead, "shortlink", res=add_url(f, **{**SHORT_DEAD, "url": "https://lurl.cc/dead2"}))
    assert lv(f, dead) == ("partial", "review")  # never 学校那边已经不存在 by itself
    unread = add_url(f)
    add_res(f, unread, "shortlink", res=add_url(f, **{**SHORT, "url": "https://reurl.cc/unread"}))
    assert lv(f, unread) == ("partial", "review")


def test_a_short_link_row_is_never_a_round_one_page(f):
    assert completeness(f, add_url(f, **{**SHORT, "final_url": "https://c.nknu.edu.tw/aca/"})) is None
