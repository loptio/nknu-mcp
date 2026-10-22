from selectolax.lexbor import LexborHTMLParser

from nknu.crawl.htmltext import element_text, leaf_tables, table_grid

BASE = "https://c.nknu.edu.tw/reality/"


def node(html, css="body"):
    return LexborHTMLParser(html).css_first(css)


def test_inline_fragments_rejoin_without_separator():
    assert element_text(node("<table><tr><td><span>19：</span><span>00~</span>21：00</td></tr></table>", "td"), BASE) == "19:00~21:00"


def test_blocks_and_br_make_lines():
    assert element_text(node("<div><p>第一行</p><p>第二<br>行</p></div>"), BASE).splitlines() == ["第一行", "第二", "行"]


def test_time_space_typo_fixed():
    assert element_text(node("<p>16: 50 開放</p>"), BASE) == "16:50 開放"


def test_scripts_dropped():
    assert element_text(node("<div>a<script>var x=1</script>b</div>"), BASE) == "ab"


def test_document_links_kept():
    t = element_text(node('<p><a href="files/%E8%A1%A8.pdf">附件</a></p>'), BASE)
    assert t == "附件 <https://c.nknu.edu.tw/reality/files/表.pdf>"


def test_rowspan_colspan_grid():
    tbl = node("""<table><tr><td rowspan=2>和平校區</td><td>體育館</td><td>週一 08:00~22:00</td></tr>
                  <tr><td>重量訓練室</td><td>週四<br>18：00~20：00</td></tr>
                  <tr><td colspan=2>燕巢校區</td><td>x</td></tr></table>""", "table")
    assert table_grid(tbl) == [["和平校區", "體育館", "週一 08:00~22:00"], ["和平校區", "重量訓練室", "週四 18:00~20:00"],
                               ["燕巢校區", "燕巢校區", "x"]]


def test_leaf_tables_skip_layout_tables():
    tree = LexborHTMLParser("<table><tr><td><table><tr><td>a</td></tr></table></td></tr></table><table><tr><td>b</td></tr></table>")
    assert [table_grid(t) for t in leaf_tables(tree.body)] == [[["a"]], [["b"]]]
