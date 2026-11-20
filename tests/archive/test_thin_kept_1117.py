"""11-17: a page fetched again keeps what reconcile judged of its words (thin: 页面几乎是空的; in_image: 内容在图片里)
while its words did not grow. Only reconcile's look sets those two flags, and it never runs at night, so a night's
fetch, which writes a page's flags anew, used to drop them: 88 near-empty pages came to 抓到了 and lost the 10-24 copy
the export fills a thin page with (the content session found 6 lis pages empty in its library, 11-17). The owner,
relayed by 爬虫judge 11-17: 「可以」 to 「重抓时这一次的字数没变多就留着标记，有旧副本的照旧用副本补；已经被冲掉的那 88
页也要能标回来」. The real crawler on a fake network; made-up pages only."""

from __future__ import annotations

from nknu.archive import outcome, reconcile, update
from nknu.archive.frontier import JUDGED_FLAGS
from tests.archive.test_archive import seed, snap
from tests.archive.test_pilot_fixes import make, work

URL = "https://c.nknu.edu.tw/aca/about.aspx"
SITE = "c.nknu.edu.tw/aca"
THIN = "<html><head><title>教務處</title></head><body><p>建置中</p></body></html>"
MORE = ("<html><head><title>教務處</title></head><body><p>" + "本處負責全校學生的註冊、選課、成績與學籍事務。" * 12
        + "</p></body></html>")
FNF = "<html><head><title>404: File not found</title></head><body>404: File not found</body></html>"


def _flags(f, uid: int) -> list[str]:
    return [x for x in (f.row_by_id(uid)["flags"] or "").split(",") if x]


def _twice(tmp_path, second: str, judged: str = "thin"):
    """URL fetched once, judged by reconcile (`judged` written as its look writes it), then fetched again (a night's
    put_back) answering `second`."""
    answers = [snap(URL, 200, html=THIN), snap(URL, 200, html=second)]
    crawler, net, clock = make(tmp_path, {("GET", "/aca/about.aspx"): lambda target: answers.pop(0)})
    seed(crawler, URL)
    queue = "ip:c.nknu.edu.tw"
    work(crawler, net, queue)
    f = crawler.frontier
    uid = f.row(URL)["id"]
    f.x("UPDATE url SET flags=? WHERE id=?", (judged, uid))
    assert f.put_back(uid, "update:standing")
    clock.t += 3600
    work(crawler, net, queue)
    assert not answers  # both answers were asked
    return crawler, f, uid


def test_the_flags_kept_are_reconciles_own():
    assert set(JUDGED_FLAGS) == {"thin", reconcile.IN_IMAGE} and set(JUDGED_FLAGS) <= set(outcome.FLAGS)


def test_a_thin_page_fetched_again_with_no_more_words_stays_thin(tmp_path):
    _crawler, f, uid = _twice(tmp_path, THIN)
    assert "thin" in _flags(f, uid)


def test_a_page_whose_body_is_a_picture_stays_so(tmp_path):
    _crawler, f, uid = _twice(tmp_path, THIN, judged=reconcile.IN_IMAGE)
    assert _flags(f, uid).count(reconcile.IN_IMAGE) == 1 and "thin" not in _flags(f, uid)


def test_a_thin_page_with_more_words_now_is_judged_afresh(tmp_path):
    _crawler, f, uid = _twice(tmp_path, MORE)
    assert "thin" not in _flags(f, uid)


def test_a_page_that_now_says_the_school_holds_nothing_is_never_kept_thin(tmp_path):
    _crawler, f, uid = _twice(tmp_path, FNF)
    assert "gone_page" in _flags(f, uid) and "thin" not in _flags(f, uid)


# ------------------------------------------------------------- the pages a night already wiped (update thin-back)


def _wiped(tmp_path, second: str = THIN):
    """What the program before 11-17 left: the page fetched again, its thin flag gone (the first answer, thin, kept in
    url_version)."""
    crawler, f, uid = _twice(tmp_path, second)
    f.x("UPDATE url SET flags=NULL WHERE id=?", (uid,))
    return crawler, f, uid


def test_thin_back_marks_a_page_a_night_wiped(tmp_path):
    _crawler, f, uid = _wiped(tmp_path)
    assert f.thin_back(write=True) == {SITE: 1}
    assert "thin" in _flags(f, uid)
    assert f.thin_back(write=True) == {}  # once: nothing more to mark


def test_thin_back_counts_without_writing_on_a_dry_run(tmp_path):
    _crawler, f, uid = _wiped(tmp_path)
    assert f.thin_back(write=False) == {SITE: 1}
    assert "thin" not in _flags(f, uid)


def test_thin_back_leaves_a_page_whose_words_grew(tmp_path):
    _crawler, f, uid = _wiped(tmp_path, MORE)
    assert f.thin_back(write=True) == {}
    assert "thin" not in _flags(f, uid)


def test_thin_back_leaves_a_page_whose_words_grew_and_shrank_again(tmp_path):
    """A kept answer after the thin one with more words: what reconcile judged was of other words."""
    _crawler, f, uid = _wiped(tmp_path)
    f.x("INSERT INTO url_version(url_id, kept_at, status, kind, sniff, text_chars, warc, warc_offset) "
        "VALUES (?, 0, 200, 'page', 'html', 500, 'later.warc.gz', 1)", (uid,))  # kept after the thin one
    assert f.thin_back(write=True) == {}
    assert "thin" not in _flags(f, uid)


def test_update_thin_back_writes_the_sites_to_export_again(tmp_path):
    crawler, f, _uid = _wiped(tmp_path)
    out = tmp_path / "thin-back-sites.txt"
    f.close()
    assert update.main(["--dir", str(crawler.root), "thin-back", "--dry-run", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == f"{SITE}\n"
    assert update.main(["--dir", str(crawler.root), "thin-back", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == f"{SITE}\n"
    assert update.main(["--dir", str(crawler.root), "thin-back", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == ""


def test_thin_back_marks_the_kept_answers_a_night_wiped_too(tmp_path):
    """A page fetched by two nights before 11-17: the answer the second replaced (kept in url_version) lost its mark
    as well, and the export's fallback to a last good answer (_kept_good) would take that thin page for a good one
    instead of the 10-24 copy."""
    _crawler, f, uid = _wiped(tmp_path)
    f.x("INSERT INTO url_version(url_id, kept_at, status, kind, sniff, text_chars, warc, warc_offset) "
        "SELECT url_id, kept_at, 200, 'page', 'html', text_chars, 'night2.warc.gz', 1 FROM url_version WHERE url_id=?",
        (uid,))  # the first night's answer, kept when the second night fetched the page, its mark gone
    assert f.thin_back(write=True) == {SITE: 1}
    kept = [r["flags"] for r in f.q("SELECT flags FROM url_version WHERE url_id=? ORDER BY id", (uid,))]
    assert kept == ["thin", "thin"] and "thin" in _flags(f, uid)


def test_thin_back_marks_a_wiped_kept_answer_even_when_the_page_itself_kept_its_mark(tmp_path):
    _crawler, f, uid = _twice(tmp_path, THIN)  # the page itself kept its mark (this program)
    f.x("INSERT INTO url_version(url_id, kept_at, status, kind, sniff, text_chars, warc, warc_offset) "
        "SELECT url_id, kept_at, 200, 'page', 'html', text_chars, 'night2.warc.gz', 1 FROM url_version WHERE url_id=?",
        (uid,))
    assert f.thin_back(write=False) == {SITE: 1}
    assert f.thin_back(write=True) == {SITE: 1}
    assert f.thin_back(write=True) == {}
