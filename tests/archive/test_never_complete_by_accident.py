"""The one promise the result views make (round-1 design §1.3, §10.2–10.4; review note high): nothing looks fetched
that is not. Random pages, each with a random own row (state, reason, flags, status, kind, expected kind, sniffed
format, page type, what jscheck saw on it), random resources (url rows, button presses, rows that are missing, needs
nobody knows, why values of every kind) and random list walks, checked against an independent statement of what
`ok` and `complete` must mean:

- a url row is `ok` exactly when every piece of its evidence says so, and only `ok` is 抓到了 (user_bucket);
- a combination the rules do not know (a state, a flag, a page-type or jscheck verdict) is never `ok`;
- a page is `complete` only when its own row is ok and every resource it needs (must or round2) was fetched or left
  out by a rule; one resource not got, or one combination not known, and it is never complete.

Seeded, so that a failure can be repeated; run with every change to outcome.py, frontier.py or the crawler's writes.
Hypothesis is not a dependency of this project (and nothing may be installed offline), so the cases are a seeded
random grid instead.
"""

from __future__ import annotations

import itertools
import random

import pytest

from nknu.archive import outcome
from nknu.archive.frontier import Frontier

SITE = "c.nknu.edu.tw/aca"
# written out here, not read from outcome.py, so that a change there cannot change what this test expects: the kinds
# expected to come back as no web page (§10.2), and what a row that is not a page may be once fetched (§12)
EXPECTED_FILE = ("file", "image", "drive", "drive_list", "calendar", "sitemap", "click", "script")
FETCHED_KINDS = ("file", "image", "drive", "calendar", "embed")
KNOWN_FLAGS = ("login", "soft404", "decode", "gone_redirect", "gone_id", "error_page", "thin", "empty_detail",
               "js_hint", "form_closed", "js_redirect", "click_empty", "gone_page", "bad_link")
# a resource that must be a file or an image is not got when it rests on a web page (review 10-26 B5)
FILE_LIKE_ROLES = ("file", "download", "drive", "img")
FETCHED, BEFORE, AFTER = "2026-10-26T02:00:00+00:00", "2026-10-26T01:00:00+00:00", "2026-10-26T03:00:00+00:00"
TYPES = {"t:ok": "ok", "t:js": "js", "t:frame": "frame", "t:todo": "todo", "t:click": "click",
         "t:unresolved": "unresolved", "t:odd": "unsure"}
STATES = ("pending", "active", "done", "done", "done", "failed", "skipped", "held", "later", "parked")
KNOWN_STATES = ("pending", "active", "done", "failed", "skipped", "held", "later")
REASONS = (None, None, "dns", "too_large", "moved_scheme", "error_page", "site_cap", "cap:site", "out:login",
           "out:social", "decor:repeat", "album:drive", "media_type", "timeout", "robots", "round2:offsite", "odd")
STATUSES = (None, 200, 200, 200, 204, 301, 302, 400, 401, 403, 404, 410, 451, 500, 503)
ONE_FLAG = (*KNOWN_FLAGS, "weird")
KINDS = ("page", "file", "image", "drive", "calendar", "embed", "media")
KIND0S = (None, "page", "file", "image", "drive", "calendar", "form", "sitemap", "click", "script", "drive_list",
          "embed", "shortlink")
SNIFFS = (None, "html", "pdf", "json", "jpeg", "xml")
HOSTS = ("c.nknu.edu.tw", "c.nknu.edu.tw", "c.nknu.edu.tw", "www.googleapis.com")
JP_VERDICTS = ("ok", "js", "frame", "click", "unresolved", "weird")
ROLES = ("file", "img", "iframe", "frame", "drive", "click", "download", "script", "calendar")
NEEDS = ("must", "must", "must", "round2", "no", "maybe")
WHYS = (None, "out:social", "out:login", "out:media", "round2:click", "round2:unhandled", "round2:google_published",
        "unhandled:unknown", "unhandled:google", "unhandled:ignore", "click:shown_on_load", "click", "chrome")
ACTION_STATES = ("pending", "done", "done", "done", "failed", "mismatch", "superseded", "parked")
ACTION_KINDS = ("pager", "detail", "download", "click", "select")
ENDED_BY = ("last_page", "no_rows_ok", "walking", "mismatch:page", "post_failed", "max_pages", None, "odd")
_n = itertools.count()


# ---------------------------------------------------------------- the independent statement of `ok`


def _flags(row) -> set[str]:
    return set(filter(None, (row["flags"] or "").split(",")))


def url_ok(row, jp) -> bool:
    """`ok` as the design means it (§10.2), stated apart from the SQL: fetched with a 200, no flag but a closed form's
    (and, on a page, a strong hint the browser has since cleared on this very copy), the file it should be and sniffed
    to show it, and for a page a type the browser test judged to need nothing more, and nothing this page's own browser
    check said against it."""
    if row["state"] != "done" or row["status"] != 200:
        return False
    if (row["kind0"] or row["kind"]) == "shortlink":  # a short link is never got itself: where it leads is (§7.7)
        return False
    page = row["kind"] == "page"
    if not _flags(row) <= ({"form_closed", "js_hint"} if page else {"form_closed"}):
        return False
    expected = (row["kind0"] or row["kind"]) in EXPECTED_FILE
    if expected and row["sniff"] == "html":
        return False
    if row["sniff"] is None and (not page or expected):
        return False
    if not page:
        return row["kind"] in FETCHED_KINDS
    seen = jp["verdict"] if jp is not None and row["fetched_at"] is not None and jp["at"] >= row["fetched_at"] else None
    if seen is not None and seen not in ("ok", "frame"):
        return False
    if "js_hint" in _flags(row) and seen is None:
        return False
    return TYPES.get(row["page_type"]) in ("ok", "frame")


def url_rule(row) -> bool:
    """Left out by a scope rule (§4.5): skipped for a rule's reason, and nothing else."""
    reason = row["reason"] or ""
    return (row["state"] == "skipped" and reason not in ("too_large", "moved_scheme") and not reason.startswith("cap:")
            and reason not in ("site_cap", "file_cap", "variant_cap", "depth")
            and (reason.startswith(("out:", "decor:", "album:")) or reason == "media_type"))


def action_ok(a) -> bool:
    return (a["state"] == "done" and a["status"] == 200 and a["kind"] in ("pager", "detail", "download", "click")
            and not (a["kind"] == "download" and a["sniff"] in ("html", None)))


# ---------------------------------------------------------------- random rows


def _row(rng: random.Random, **fixed) -> dict:
    host = rng.choice(HOSTS)
    row = {"url": f"https://{host}/x{next(_n)}", "host": host, "site": SITE, "owner_site": SITE,
           "tier": rng.choice((0, 1)), "kind": rng.choice(KINDS), "kind0": rng.choice(KIND0S), "depth": 1,
           "found_via": "link", "state": rng.choice(STATES), "reason": rng.choice(REASONS),
           "attempts": rng.choice((0, 0, 1, 3)), "status": rng.choice(STATUSES),
           "flags": ",".join(rng.sample(ONE_FLAG, rng.choice((0, 0, 0, 1, 1, 2)))) or None,
           "sniff": rng.choice(SNIFFS), "page_type": rng.choice((*TYPES, None, "t:missing")),
           "fetched_at": rng.choice((FETCHED, FETCHED, None)), "page_role": rng.choice((None, "detail", "list"))}
    row.update(fixed)
    return row


def _good(rng: random.Random, **fixed) -> dict:
    """A row whose evidence all agrees, so that complete pages are reached too (the test is not vacuous)."""
    kind = fixed.get("kind", "file")
    row = _row(rng, state="done", status=200, reason=None, flags=None, kind=kind, kind0=kind,
               sniff="html" if kind == "page" else "pdf", page_type="t:ok" if kind == "page" else None,
               host="c.nknu.edu.tw", page_role=None)
    row.update(fixed)
    return row


# what one column may be set to when a good row is changed in one place (the boundary every rule sits on)
DOMAIN = {"state": STATES, "reason": REASONS, "status": STATUSES, "kind": KINDS, "kind0": KIND0S, "sniff": SNIFFS,
          "page_type": (*TYPES, None, "t:missing"), "fetched_at": (FETCHED, None), "host": HOSTS,
          "page_role": (None, "detail", "list")}


def _near(rng: random.Random, **fixed) -> dict:
    """A good row changed in one or two places: one more flag, or one column set to another value."""
    row = _good(rng, **fixed)
    for _ in range(rng.choice((1, 1, 2))):
        col = rng.choice((*DOMAIN, "flags", "flags"))
        if col == "flags":
            row["flags"] = ",".join(sorted(set(filter(None, (row["flags"] or "").split(","))) | {rng.choice(ONE_FLAG)}))
        else:
            row[col] = rng.choice(DOMAIN[col])
    if row["host"] != "c.nknu.edu.tw":
        row["url"] = f"https://{row['host']}/x{next(_n)}"
    return row


def _any(rng: random.Random, **fixed) -> dict:
    roll = rng.random()
    return _good(rng, **fixed) if roll < 0.3 else _near(rng, **fixed) if roll < 0.75 else _row(rng, **fixed)


def _insert(f: Frontier, row: dict) -> int:
    row = {**row, "path": row["url"].split("?", 1)[0]}
    with f.lock:
        return f.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


def _action(f: Frontier, rng: random.Random, list_id: int) -> int:
    row = {"list_id": list_id, "epoch": 1, "step": 1, "kind": rng.choice(ACTION_KINDS), "queue": "ip:140.127.40.173",
           "host": "sso.nknu.edu.tw", "site": SITE, "profile": "p", "form_src_warc": "w", "form_src_offset": 0,
           "target": "t", "row_key": f"r{next(_n)}", "state": rng.choice(ACTION_STATES),
           "status": rng.choice(STATUSES), "sniff": rng.choice(SNIFFS)}
    with f.lock:
        return f.conn.execute(f"INSERT INTO action({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


@pytest.fixture
def world(tmp_path):
    """About 900 round-1 pages and their resources, at random but seeded: some rows good, most a good row changed in
    one or two places (where the rules' boundaries are), the rest anything at all."""
    rng = random.Random(20261026)
    f = Frontier(tmp_path / "frontier.db")
    for key, verdict in TYPES.items():
        f.x("INSERT INTO page_type(key, site, verdict, source) VALUES (?,?,?,?)",
            (key, SITE, verdict, rng.choice(("import", "crawl", "jscheck", "manual"))))
    kinds = [{"kind": k, "kind0": k} for k in ("file", "image", "page", "drive", "calendar", "embed")]
    kinds += [{"kind": "file", "kind0": k} for k in ("click", "script", "sitemap", "drive_list", "drive")]
    kinds += [{"kind": "embed", "kind0": "form", "sniff": "html"}, {"kind": "drive", "kind0": "drive", "sniff": "json"}]
    pool = [_insert(f, _any(rng, tier=2, **rng.choice(kinds))) for _ in range(700)]
    pages = []
    for _ in range(900):
        page = _insert(f, _any(rng, kind="page"))
        pages.append(page)
        for _k in range(rng.choice((0, 0, 1, 2, 3, 5))):
            need, role, why, res_id, act_id = rng.choice(NEEDS), rng.choice(ROLES), rng.choice(WHYS), None, None
            roll = rng.random()
            if roll < 0.6:
                res_id = rng.choice(pool)
            elif roll < 0.75:
                act_id = _action(f, rng, page)
            elif roll < 0.8:
                res_id = 10_000_000 + next(_n)  # points to no row
            f.x("INSERT INTO page_res(page_id, res_key, how, role, need, why, res_id, action_id) VALUES (?,?,?,?,?,?,?,?)",
                (page, f"k{next(_n)}", "get", role, need, why, res_id, act_id))
        if rng.random() < 0.3:
            f.x("INSERT INTO listwalk(list_id, site, profile, epoch, pages, ended_by, ok) VALUES (?,?,?,?,?,?,?)",
                (page, SITE, "p", 1, 2, rng.choice(ENDED_BY), rng.choice((None, 0, 1))))
    for uid in rng.sample(pages + pool, 500):  # what jscheck saw, some before the last fetch
        f.x("INSERT INTO js_page(url_id, page_type, at, verdict) VALUES (?,?,?,?)",
            (uid, "t:ok", rng.choice((BEFORE, AFTER)), rng.choice(JP_VERDICTS)))
    yield f, pages
    f.close()


def test_a_url_is_ok_and_got_only_when_every_piece_of_evidence_says_so(world):
    f, _pages = world
    jp = {r["url_id"]: r for r in f.q("SELECT * FROM js_page")}
    seen = set()
    for r in f.q("SELECT * FROM url_result"):
        result = r["result"]
        seen.add(result)
        assert result in outcome.URL_RESULTS, dict(r)
        assert (result == "ok") == url_ok(r, jp.get(r["id"])), dict(r)
        assert (outcome.user_bucket(result) == "got") == (result == "ok"), dict(r)
        if r["state"] not in KNOWN_STATES:
            assert result == "unknown", dict(r)
        if r["state"] == "done" and not _flags(r) <= set(KNOWN_FLAGS):
            assert result == "unknown", dict(r)
        if result == "rule":
            assert url_rule(r), dict(r)
    assert {"ok", "unknown", "redirect", "api_error", "looks_gone", "unchecked", "click", "js"} <= seen


def test_a_press_is_ok_only_when_it_came_back_as_it_should(world):
    f, _pages = world
    for r in f.q("SELECT * FROM action_result"):
        assert r["result"] in outcome.ACTION_RESULTS, dict(r)
        assert (r["result"] == "ok") == action_ok(r), dict(r)


def test_a_page_is_complete_only_when_it_and_everything_it_needs_was_got(world):
    f, pages = world
    urls = {r["id"]: r for r in f.q("SELECT * FROM url")}
    actions = {r["id"]: r for r in f.q("SELECT * FROM action")}
    jp = {r["url_id"]: r for r in f.q("SELECT * FROM js_page")}
    walks = {r["list_id"]: r for r in f.q("SELECT * FROM listwalk")}
    res = {}
    for r in f.q("SELECT * FROM page_res"):
        res.setdefault(r["page_id"], []).append(r)
    rows = {r["id"]: r for r in f.q("SELECT * FROM page_completeness")}
    levels, labels = set(outcome.LEVEL_LABEL), {c for c, _zh in outcome.LABEL_ORDER}

    def got(pr) -> bool:
        """This needed resource was fetched as it should be, or a rule leaves it out."""
        if pr["need"] != "must":
            return False  # round2: the next round's; any other need: nobody knows it
        if pr["action_id"] is not None:
            a = actions.get(pr["action_id"])
            return a is not None and action_ok(a)
        if pr["res_id"] is not None:
            u = urls.get(pr["res_id"])
            if u is None:
                return False
            if url_rule(u):
                return True
            # a Drive item's metadata row stands for nothing until its content row exists (§7.6)
            return (url_ok(u, jp.get(u["id"])) and not (pr["role"] == "drive" and u["kind0"] == "drive")
                    and not (pr["role"] in FILE_LIKE_ROLES and u["kind"] == "page"))
        return (pr["why"] or "").startswith("out:") and not (pr["why"] or "").startswith("out:login")

    n_complete = 0
    for page in pages:
        own = urls[page]
        r = rows.get(page)
        own_result = f.q("SELECT result FROM url_result WHERE id=?", (page,))[0][0]
        if own["kind"] != "page" or own_result in outcome.NOT_JUDGED:  # changed into a file, or left out by the scope
            assert r is None
            continue
        assert r is not None, dict(own)  # a round-1 page never drops out of the table unseen
        assert r["level"] in levels and r["label"] in labels
        assert (r["level"] == "complete") == (r["label"] == "complete"), dict(r)
        needed = [pr for pr in res.get(page, []) if pr["need"] != "no"]
        everything_got = url_ok(own, jp.get(page)) and all(got(pr) for pr in needed)
        walk = walks.get(page)
        if walk is not None:
            everything_got &= walk["ended_by"] in outcome.LIST_ENDS_OK and walk["ok"] != 0
        elif own["page_role"] == "list":
            everything_got = False  # a list nobody walked
        seen = jp.get(page)
        frames = TYPES.get(own["page_type"]) == "frame" or (
            seen is not None and own["fetched_at"] is not None and seen["at"] >= own["fetched_at"]
            and seen["verdict"] == "frame")
        if frames and not any(pr["role"] in outcome.FRAME_ROLES for pr in needed):
            everything_got = False
        if r["level"] == "complete":
            n_complete += 1
            assert everything_got, (dict(r), dict(own), [dict(x) for x in needed])
        if not everything_got:
            assert r["level"] != "complete" and r["label"] != "complete", (dict(r), dict(own))
        if r["level"] == "complete_school":
            assert r["own"] in ("ok", *outcome.SCHOOL), dict(r)
    assert n_complete >= 30 and n_complete < len(pages) // 2  # both sides are reached
