"""The local archive viewer: an offset index over the WARC files, every page reachable, previews that load nothing."""

import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime

from nknu.archive.fetch import Snapshot
from nknu.archive.frontier import Frontier
from nknu.archive.target import Target
from nknu.archive.viewer import PREVIEW_CSP, build_index, page_text, serve
from nknu.archive.warc import WarcSink

HTML = "<html><head><title>教務處</title><script>alert(1)</script></head><body><h1>規章</h1><img src='https://c.nknu.edu.tw/x.jpg'></body></html>"


def archive(tmp_path):
    sink = WarcSink(tmp_path / "warc", "ip_1", 1, datetime(2026, 10, 24, tzinfo=UTC))
    for url, ctype, body in (("https://c.nknu.edu.tw/aca/", "text/html; charset=utf-8", HTML.encode()),
                             ("https://c.nknu.edu.tw/aca/a.pdf", "application/pdf", b"%PDF-1.4 x")):
        sink.write(Snapshot(Target(url), url, 200, {"content-type": ctype}, body, None, datetime.now(UTC), "s"), {})
    sink.close()
    f = Frontier(tmp_path / "frontier.db")
    rel = str(sink.path.relative_to(tmp_path))
    for url, ctype in (("https://c.nknu.edu.tw/aca/", "text/html; charset=utf-8"), ("https://c.nknu.edu.tw/aca/a.pdf",
                                                                                   "application/pdf")):
        f.add(url, host="c.nknu.edu.tw", site="c.nknu.edu.tw/aca", unit="教務處", tier=1, kind="page", depth=0,
              found_via="seed", found_from=None)
        f.x("UPDATE url SET state='done', status=200, ctype=?, final_url=url, warc=?, title='教務處' WHERE url=?",
            (ctype, rel, url))
    return f


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, dict(r.headers), r.read()


def test_index_is_incremental(tmp_path):
    archive(tmp_path)
    assert build_index(tmp_path) == 2
    assert build_index(tmp_path) == 0


def test_text_drops_scripts():
    assert page_text(HTML) == "規章"


def test_every_view_answers_and_previews_load_nothing(tmp_path):
    archive(tmp_path)
    server = serve(tmp_path, 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ("/", "/site?k=c.nknu.edu.tw/aca", "/issues", "/search?q=%E6%95%99%E5%8B%99", "/page?id=1", "/page?id=2"):
            assert get(port, path)[0] == 200, path
        status, headers, body = get(port, "/preview?id=1")
        assert status == 200 and headers["Content-Security-Policy"] == PREVIEW_CSP and "規章" in body.decode()
        assert get(port, "/text?id=1")[2].decode().count("規章") == 1
        status, headers, body = get(port, "/raw?id=2")
        assert body.startswith(b"%PDF") and "Content-Security-Policy" not in headers
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.shutdown()


def test_records_appended_after_indexing_are_found(tmp_path):
    f = archive(tmp_path)
    assert build_index(tmp_path) == 2
    sink = WarcSink(tmp_path / "warc", "ip_1", 1, datetime(2026, 10, 24, tzinfo=UTC))  # the same file, appended
    url = "https://c.nknu.edu.tw/aca/new.html"
    sink.write(Snapshot(Target(url), url, 200, {"content-type": "text/html"}, b"<p>later</p>", None, datetime.now(UTC),
                        "s"), {})
    sink.close()
    assert build_index(tmp_path) == 1
    f.add(url, host="c.nknu.edu.tw", site="c.nknu.edu.tw/aca", unit="", tier=1, kind="page", depth=1,
          found_via="link", found_from=1)
    f.x("UPDATE url SET state='done', status=200, ctype='text/html', final_url=url WHERE url=?", (url,))
    from nknu.archive.viewer import Viewer
    v = Viewer(tmp_path)
    uid = f.q("SELECT id FROM url WHERE url=?", (url,))[0][0]
    assert v.body_of(uid)[1] == b"<p>later</p>"
