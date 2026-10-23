import logging
import time
from dataclasses import replace
from datetime import date

import pytest
from typer.testing import CliRunner

from nknu.admin.changes import crawl_summary
from nknu.admin.cli import app
from nknu.core.factsdb import connect
from nknu.core.records import HoursRule, SourceRef
from nknu.core.timeutil import iso_now
from nknu.core.writer import add_alert, apply_fact
from nknu.crawl.pipeline import run_adapter
from tests.crawl.test_pipeline import FakeAdapter
from tests.factories import HOLIDAY, LIB_CLOSED, LIB_REGULAR, T0, WR_TERM, make_db


def test_alerts_list_and_resolve(tmp_path):
    c = make_db(tmp_path)
    aid = add_alert(c, adapter="pe_news", level="warn", code="parse_issue", message="日期看不懂", now=T0)
    c.commit()
    env = {"NKNU_DATA_DIR": str(tmp_path)}
    r = CliRunner().invoke(app, ["alerts"], env=env)
    assert r.exit_code == 0 and "日期看不懂" in r.output
    assert CliRunner().invoke(app, ["alerts", "--resolve", str(aid)], env=env).exit_code == 0
    assert connect(tmp_path / "facts.db").execute("SELECT resolved_at FROM alert WHERE id=?", (aid,)).fetchone()[0]
    assert "日期看不懂" not in CliRunner().invoke(app, ["alerts"], env=env).output


def test_crawl_without_adapters_is_noop(tmp_path, monkeypatch):
    make_db(tmp_path).close()
    monkeypatch.setattr("nknu.crawl.adapter.REGISTRY", {})
    monkeypatch.setattr("nknu.crawl.adapter.load_adapters", dict)
    r = CliRunner().invoke(app, ["crawl", "--no-build"], env={"NKNU_DATA_DIR": str(tmp_path)})
    assert r.exit_code == 0, r.output


# C4: one summary per crawl when anything was written, retired or alerted; Discord too when a webhook is set

WEBHOOK = "https://discord.test/api/webhooks/1/x"


@pytest.fixture
def posts(monkeypatch):
    sent = []
    monkeypatch.setattr("nknu.core.notify.httpx.post", lambda url, json, timeout: sent.append((url, json["content"])))
    return sent


def crawl_with(tmp_path, monkeypatch, *adapters, env=None, build=False):
    monkeypatch.setattr("nknu.crawl.adapter.load_adapters", lambda: {a.name: (lambda a=a: a) for a in adapters})
    args = ["crawl", "--partial"] + ([] if build else ["--no-build"])
    return CliRunner().invoke(app, args, env={"NKNU_DATA_DIR": str(tmp_path), **(env or {})})


def test_crawl_with_writes_notifies_the_maintainer(tmp_path, monkeypatch, posts):
    make_db(tmp_path).close()
    r = crawl_with(tmp_path, monkeypatch, FakeAdapter([LIB_CLOSED, LIB_REGULAR], name="lib_open_time"),
                   env={"NKNU_DISCORD_WEBHOOK": WEBHOOK})
    assert r.exit_code == 0, r.output
    assert len(posts) == 1 and posts[0][0] == WEBHOOK
    text = posts[0][1]
    assert "lib_open_time：新增 2" in text
    assert "新增 [lib_open_time] 和平圖書館 2026-10-25～2026-10-26 不開放（臺灣光復暨金門古寧頭大捷紀念日及補假）" in text
    assert LIB_CLOSED.key in text and "nknu undo" in text
    # the next crawl finds nothing new: no message (summaries count from the crawl's start, to the second)
    time.sleep(1.01 - time.time() % 1)
    assert crawl_with(tmp_path, monkeypatch, FakeAdapter([LIB_CLOSED, LIB_REGULAR], name="lib_open_time"),
                      env={"NKNU_DISCORD_WEBHOOK": WEBHOOK}).exit_code == 0
    assert len(posts) == 1


def test_crawl_summary_goes_to_the_log_without_a_webhook(tmp_path, monkeypatch, posts, caplog):
    make_db(tmp_path).close()
    with caplog.at_level(logging.WARNING, logger="nknu"):
        assert crawl_with(tmp_path, monkeypatch, FakeAdapter([HOLIDAY], name="aca_calendar_ics")).exit_code == 0
    assert not posts and any("aca_calendar_ics：新增 1" in m for m in caplog.messages)


def test_void_run_and_its_alert_are_in_the_summary(tmp_path, monkeypatch, posts):
    make_db(tmp_path).close()
    broken = FakeAdapter([HOLIDAY], html="<html><body>維護中</body></html>", selectors=["#lbl_Contact"], name="pe_open_hours")
    r = crawl_with(tmp_path, monkeypatch, broken, env={"NKNU_DISCORD_WEBHOOK": WEBHOOK})
    assert r.exit_code == 1 and len(posts) == 1
    assert "pe_open_hours：作废" in posts[0][1] and "selector_missing" in posts[0][1]


def test_summary_alert_header_covers_daily_reminders(tmp_path):
    # a void alert left open is raised again once a day (C6): the summary header must not call it new
    conn = make_db(tmp_path)
    since = iso_now()
    add_alert(conn, adapter="sso_news", level="error", code="count_out_of_band", message="x", now=since)
    text = crawl_summary(conn, [], since)
    assert "告警 1 条" in text and "新告警" not in text and "每日提醒" in text

def test_summary_lists_at_most_ten_changes(tmp_path):
    from datetime import timedelta

    conn = make_db(tmp_path)
    since = iso_now()
    days = [replace(LIB_CLOSED, key=f"lib_open_time:nknu.heping.library.main:closed:x{i}",
                    valid_from=LIB_CLOSED.valid_from + timedelta(days=7 * i),
                    valid_to=LIB_CLOSED.valid_from + timedelta(days=7 * i + 1)) for i in range(12)]
    results = [run_adapter(conn, FakeAdapter(days, name="lib_open_time"), None, today=None, full_scan=False, readonly=False)]
    text = crawl_summary(conn, results, since)
    assert text.count("新增 [lib_open_time]") == 10 and "另有 2 条" in text and "lib_open_time：新增 12" in text
    assert crawl_summary(conn, results, "2099-01-01T00:00:00Z") is None
    for i in range(6):  # long alerts: the message still fits one Discord post, alerts first, changes cut
        add_alert(conn, adapter="lib_open_time", level="warn", code="parse_issue", message="很長的訊息" * 80, now=iso_now())
    text = crawl_summary(conn, results, since)
    assert len(text) <= 1800 and text.count("[warn]") == 5 and "另有" in text and "nknu undo" in text


def test_conflict_from_the_crawl_build_is_notified(tmp_path, monkeypatch, posts):
    # C4 / F-webhook-never-sent: a conflict that the crawl's own rebuild finds reaches the maintainer
    conn = make_db(tmp_path)
    wide = {"valid_from": date(2020, 1, 1), "valid_to": date(2100, 1, 1)}
    apply_fact(conn, replace(WR_TERM, **wide), adapter="pe_open_hours", review="verified", now=T0)
    other = SourceRef("https://c.nknu.edu.tw/reality/Page.aspx?PN=9", "場地開放時間(另一版本)", "nknu.org.pe", "table_page", "x")
    apply_fact(conn, replace(WR_TERM, key="v2", source=other, rules=(HoursRule((4,), "18:30", "20:30"),), **wide),
               adapter="pe_open_hours", review="verified", now=T0)
    conn.commit()
    conn.close()
    r = crawl_with(tmp_path, monkeypatch, FakeAdapter([HOLIDAY], name="aca_calendar_ics"), build=True,
                   env={"NKNU_DISCORD_WEBHOOK": WEBHOOK})
    assert r.exit_code == 0, r.output
    assert len(posts) == 1 and "conflict" in posts[0][1] and "nknu.heping.gym.weight-room" in posts[0][1]


def test_summary_is_sent_when_the_rebuild_fails(tmp_path, monkeypatch, posts):
    # the writes happened: the maintainer hears about them and about the failed rebuild (the old serve.db keeps serving)
    make_db(tmp_path).close()

    def broken_build(*args, **kwargs):
        raise RuntimeError("serve.db integrity check failed")

    monkeypatch.setattr("nknu.build.servedb.build_serve_db", broken_build)
    r = crawl_with(tmp_path, monkeypatch, FakeAdapter([LIB_CLOSED], name="lib_open_time"), build=True,
                   env={"NKNU_DISCORD_WEBHOOK": WEBHOOK})
    assert r.exit_code != 0 and len(posts) == 1
    assert "lib_open_time：新增 1" in posts[0][1] and "重建 serve.db 失败" in posts[0][1]
