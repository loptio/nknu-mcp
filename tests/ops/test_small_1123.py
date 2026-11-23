"""Small items of the 11-23 port audit (research/design_2026-11-23/port_audit_open_1123.json): what the design copy did
that the live pages still lacked, or what only the copy needed."""
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from nknu.ops import doc_page, names
from nknu.ops.look import s2t

LAYER = Path(__file__).resolve().parents[2] / "src" / "nknu" / "ops" / "theme" / "design"


def test_a_documents_kind_is_its_icons_hover_in_every_list_never_the_grey_line():
    """doc-found: the kind word moved from the grey line to the icon's hover. found.js did it once on load, but the
    lists typed and polled replace the markup from /doc/find (_doc_rows): drawn on the server, every list is the same."""
    html = doc_page._doc_rows([{"doc_id": "d1", "kind": "attachment", "title": "休學申請表", "name": "教務處",
                                "date": "2026-11-20"}])
    row = LexborHTMLParser(html).css_first("a.prow")
    assert row.css_first(".pp").text() == "教務處 · 2026-11-20"
    assert row.css_first(".dc-k").attributes["data-tip"] == "附件"


def test_the_document_page_never_says_it_is_the_design_copys_one_sample():
    """trace: the copy held one sample document and said so when another was asked; the live page has them all."""
    js = (LAYER / "trace.js").read_text(encoding="utf-8")
    assert "設計稿" not in js and "pd-sample" not in js


def test_the_two_crawler_results_without_a_design_word_have_one_now():
    """The site page's 爬取 fold still showed two results as the crawler writes them (outcome.py OFFSITE_LANDED and
    retry); named in the design's manner, as their Simplified words come through s2t."""
    assert names.rename(s2t("按规则不抓（整页跳到校外页）")) == "依規則不抓（轉至校外）"
    assert names.rename(s2t("按规则不抓：整页跳到校外页（用户 11-17 决定不抓）")) == "依規則不抓（轉至校外）"
    assert names.rename(s2t("出错了，等重试")) == "出錯待重試"
