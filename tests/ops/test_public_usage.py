"""The public service (8770) on 查詢服務 (2026-11-21; a review of the panels found the service students use on no
panel, and the owner: 「8770那个可以动了」): its /health asked, never a search; its query log's last day read from the
end back, the rotated copy too; how long and what failed said only once the log writes them. Made-up queries."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from nknu.core.timeutil import TAIPEI
from nknu.ops import home_page, pipeline
from nknu.ops.pipeline import BAD, NONE, OK, WARN, Where
from tests.ops.test_pipeline import _library

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=TAIPEI)


def line(minutes_ago: float, **more) -> str:
    at = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    return json.dumps({"t": at, "via": "mcp", "tool": "search", "query": "停修", "found": True, "ids": ["d1"], **more},
                      ensure_ascii=False)


def log(path: Path, *lines: str) -> Path:
    path.write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    return path


def rows_of(path: Path) -> dict:
    rows, _ = pipeline.public_usage(path, NOW)
    return {name: (tone, words) for name, tone, words in rows}


def test_the_log_as_it_is_today_says_what_it_has_and_what_it_does_not_keep(tmp_path):
    path = log(tmp_path / "queries.jsonl",
               line(60 * 30),  # older than a day: not counted
               line(120, via="web"), line(90, via="api", query="校園地圖", found=False, ids=[]),
               line(30, tool="fetch", query=None, id="d1"), line(10, query="[MASKED] 的宿舍", found=False, ids=[]))  # as the service logs it
    rows = rows_of(path)
    assert rows["查詢"] == (OK, "4 次：檢索 3、讀全文 1（MCP 2、網頁 1、接口 1）；最後一次 11-22 08:50")
    assert rows["查無資料"] == (OK, "2 次，占檢索的 67%")
    assert rows["多慢"] == (NONE, "紀錄還沒記每次花多久")
    assert rows["沒答成"] == (NONE, "紀錄還沒記沒答成的那幾次")  # never 「沒有」 from a log that cannot say
    _, missed = pipeline.public_usage(path, NOW)
    assert missed == [["11-22 08:50", "MCP", "[MASKED] 的宿舍"], ["11-22 07:30", "接口", "校園地圖"]]


def test_once_the_log_keeps_time_and_failures_they_are_said_and_a_failure_of_ours_lights_the_step(tmp_path):
    path = log(tmp_path / "queries.jsonl", *(line(100 - i, ms=900 + 100 * i) for i in range(10)))
    rows = rows_of(path)
    assert rows["多慢"] == (OK, "一半在 1.4 秒內答完，最慢的一成要 1.7 秒以上") and rows["沒答成"] == (OK, "沒有")
    log(path, line(50, ms=12_000), line(40, ms=20_100, error="busy", found=False, ids=[]),
        line(30, ms=5, error="bad_args", found=False, ids=[]), line(20, ms=15_000))
    rows = rows_of(path)
    assert rows["沒答成"] == (WARN, "2 次：排隊太久 1、參數不對 1")
    assert rows["查詢"][1].startswith("4 次：檢索 2")  # a failed call is a call, not a search that found nothing
    assert rows["查無資料"] == (OK, "0 次，占檢索的 0%") and rows["多慢"] == (OK, "答完的 2 次各花 12.0、15.0 秒")
    # five timed, one slow: most likely the first after a restart (it loads the models): said, not lit
    log(path, *(line(50 - i, ms=1000) for i in range(4)), line(40, ms=16_733))
    once = ("一半在 1.0 秒內答完，最慢的一成要 16.7 秒以上；超過 10 秒的只有 1 次"
            "（服務重啟後第一次檢索要先載入模型，約 17 秒）")
    assert rows_of(path)["多慢"] == (OK, once)
    log(path, *(line(50 - i, ms=1000) for i in range(3)), line(41, ms=12_000), line(40, ms=16_733))
    assert rows_of(path)["多慢"] == (WARN, "一半在 1.0 秒內答完，最慢的一成要 16.7 秒以上")
    # ten timed, one slow: the nearest-rank tenth is the second slowest, not the slowest
    log(path, *(line(60 - i, ms=1000 + 10 * i) for i in range(9)), line(40, ms=16_733))
    assert rows_of(path)["多慢"][1].startswith("一半在 1.1 秒內答完，最慢的一成要 1.1 秒以上")
    log(path, line(30, ms=5, error="bad_args", found=False, ids=[]))  # the caller's own mistake: not lit
    assert rows_of(path)["沒答成"] == (OK, "1 次：參數不對 1")
    assert rows_of(path)["多慢"] == (NONE, "沒有答完的檢索可以算")
    # only searches that answered are timed: a refusal or a fetch is milliseconds (the lab's log, 11-21 23:02)
    log(path, line(30, ms=1295), line(29, ms=3, error="bad_args", found=False, ids=[]),
        line(28, tool="fetch", query=None, id="x", ms=2, found=False), line(27, ms=1400))
    assert rows_of(path)["多慢"] == (OK, "答完的 2 次各花 1.3、1.4 秒")
    # a few searches, one of them the first after a restart (it loads the models): each said, nothing lit
    log(path, line(30, ms=16_700), line(20, ms=1295))
    assert rows_of(path)["多慢"] == (OK, "答完的 2 次各花 1.3、16.7 秒")


def test_the_same_use_is_kept_as_numbers_with_a_count_for_each_of_the_last_seven_days(tmp_path):
    """總覽 draws these (2026-11-22): it read them back out of the sentences until then."""
    path = log(tmp_path / "queries.jsonl",
               line(60 * 24 * 8),  # eight days back: in no day
               line(60 * 24 * 2, via="web"), line(60 * 24 * 2 + 5, tool="fetch", query=None, id="d1"),
               *(line(100 - i, ms=1000 + 100 * i) for i in range(6)),
               line(30, via="api", found=False, ids=[], ms=900), line(20, ms=5, error="bad_args", found=False, ids=[]),
               line(10, ms=20_100, error="busy", found=False, ids=[]))
    nums: dict = {}
    pipeline.public_usage(path, NOW, nums)
    assert nums["calls"] == 9 and nums["tools"] == {"search": 7, "fetch": 0} and nums["searches"] == 7
    assert nums["via"] == {"mcp": 8, "web": 0, "api": 1} and nums["missed"] == 1
    assert (nums["failed"], nums["failed_ours"], nums["errors"]) == (2, 1, {"bad_args": 1, "busy": 1})
    assert nums["timed"] == 7 and nums["mid_ms"] == 1200 and nums["slow_ms"] == 1500 and nums["over_ms"] == 0
    assert nums["days"][-1] == ["2026-11-22", 9, 7] and nums["days"][-3] == ["2026-11-20", 2, 1]
    assert len(nums["days"]) == pipeline.PUBLIC_DAYS and nums["days"][0][0] == "2026-11-16"
    quiet: dict = {}
    pipeline.public_usage(log(tmp_path / "q2.jsonl", line(60 * 30)), NOW, quiet)
    assert quiet["calls"] == 0 and quiet["days"][-2] == ["2026-11-21", 1, 1] and quiet["days"][-1] == ["2026-11-22", 0, 0]
    assert quiet["days"][0] == ["2026-11-16", None, None]  # before the log began: no record, not a quiet day


def test_the_machine_keeps_its_numbers_beside_its_words(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "memory", lambda: (64 * 2**30, 48 * 2**30, 2 * 2**30))
    monkeypatch.setattr(pipeline, "gpu_memory", lambda: (3686, 20475))
    got = {m["name"]: m for m in pipeline.machine_read(Where(root=tmp_path, db=tmp_path / "x.db"))}
    assert (got["記憶體"]["used"], got["記憶體"]["total"], got["記憶體"]["swap"]) == (16.0, 64.0, 2.0)
    assert (got["顯示卡記憶體"]["used"], got["顯示卡記憶體"]["total"]) == (3.6, 20.0)
    assert [r[2] for r in pipeline.machine(Where(root=tmp_path, db=tmp_path / "x.db"))] == [
        m["words"] for m in got.values()]


def test_a_quiet_day_says_when_the_last_query_was_and_a_missing_log_says_so(tmp_path):
    path = log(tmp_path / "queries.jsonl", line(60 * 30))
    assert rows_of(path) == {"查詢": (NONE, "沒有人查（最後一次 11-21 03:00）")}
    assert rows_of(tmp_path / "none.jsonl") == {"查詢紀錄": (NONE, "讀不到（紀錄檔沒有掛給檢視台，或服務還沒寫過）")}


def test_the_log_is_read_from_the_end_back_across_chunks_and_into_its_rotated_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "LOG_CHUNK", 64)  # every line crosses a chunk's edge
    path = tmp_path / "queries.jsonl"
    log(path.with_name("queries.jsonl.1"), line(60 * 30), line(300, query="舊檔裡的"))
    log(path, line(200), "not json", line(100), line(5))
    got = pipeline.query_lines(path, NOW - timedelta(hours=24))
    assert [x["t"][11:16] for x in got] == ["04:00", "05:40", "07:20", "08:55"]
    assert got[0]["query"] == "舊檔裡的"
    log(path, line(60 * 25), line(5))  # the current file reaches back past the day: the rotated copy is not read
    assert len(pipeline.query_lines(path, NOW - timedelta(hours=24))) == 1


class _Health(BaseHTTPRequestHandler):
    code, body, asked = 200, {}, []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        type(self).asked.append((self.path, self.headers["Host"]))
        data = json.dumps(self.body).encode()
        self.send_response(self.code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def health():
    _Health.code, _Health.asked = 200, []
    _Health.body = {"status": "ok", "built_at": "2026-11-21T04:03:26+08:00", "docs": 88966, "age_hours": 18.9,
                    "stale": False}
    srv = HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/health"
    srv.shutdown()


def test_the_public_service_is_asked_its_health_only_and_said_plainly(health, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "gpu_memory", lambda: None)
    monkeypatch.setattr(pipeline, "memory", lambda: None)
    monkeypatch.setattr(pipeline, "now_tpe", lambda: NOW)  # the log's line is 10 minutes before NOW, not before today
    got = pipeline.ask_service("對外服務（8770）", health, "public")
    assert got["up"] and got["interface"] and "讀的庫 11-21 04:03 建的、88,966 份" in got["words"]
    assert _Health.asked == [("/health", f"127.0.0.1:{health.rsplit(':', 1)[1].split('/')[0]}")]  # its own name
    where = Where(root=tmp_path, db=_library(tmp_path / "aca.db", "2026-11-21T04:03:26+08:00"), services=(("對外服務（8770）", health, "public"),),
                  public_log=log(tmp_path / "q.jsonl", line(10)))
    step = pipeline.serve_step(where)
    assert step.state == OK and step.usage_label == "對外服務最近 24 小時"
    assert [r[0] for r in step.usage] == ["查詢", "查無資料", "多慢", "沒答成"]
    (svc,) = step.nums["services"]  # its words carry how long it took to answer: not compared
    assert {k: svc[k] for k in ("name", "kind", "up")} == {"name": "對外服務（8770）", "kind": "public", "up": True}
    _Health.body = {**_Health.body, "age_hours": 50.2, "stale": True}  # the nights stopped: its library is old
    step = pipeline.serve_step(where)
    assert step.state == WARN and "它說庫已經 50.2 小時沒換" in step.say
    _Health.code = 503
    step = pipeline.serve_step(where)
    assert step.state == BAD and step.fix == pipeline.INTERFACE and "對外服務（8770） 沒有回應" in step.say
    assert step.nums["services"][0]["up"] is False
    assert step.lost == [("對外服務（8770）：回 503：它讀不到庫", 1)]


def test_the_home_draws_the_use_and_folds_the_searches_that_found_nothing():
    step = {"usage_label": "對外服務最近 24 小時",
            "usage": [["查詢", OK, "4 次"], ["沒答成", WARN, "1 次：排隊太久 1"]],
            "usage_missed": [["11-22 08:50", "MCP", "<b>宿舍</b>"]]}
    html = home_page.usage_html(step)
    assert "<h4>對外服務最近 24 小時</h4>" in html and "pill warn" in html and "排隊太久" in html
    assert "最近查無資料的 1 次" in html and "&lt;b&gt;宿舍&lt;/b&gt;" in html and "<b>宿舍" not in html
    assert home_page.usage_html({}) == ""
