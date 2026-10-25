"""One events.list request with the real key. Run with: set -a; source ~/.config/nknu/google.env; set +a; then
uv run pytest -m live tests/live/test_calendar_api_live.py"""

import os
from datetime import date

import pytest

from nknu.core.records import EventRecord
from nknu.crawl.adapter import RateLimit, RunContext
from nknu.crawl.adapters.aca_calendar_ics import AcaCalendar
from nknu.crawl.fetch import Client


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GOOGLE_API_KEY"), reason="GOOGLE_API_KEY is not set")
def test_the_public_calendar_answers_with_academic_events():
    client = Client()
    try:
        a = AcaCalendar()
        ctx = RunContext(client=client, full_scan=True, today=date.today(), conn=None, known_keys=set(), rate=RateLimit())
        [s] = a.fetch_all(ctx)
    finally:
        client.close()
    assert s.status == 200 and os.environ["GOOGLE_API_KEY"] not in s.url
    assert sum(isinstance(r, EventRecord) for r in a.parse(s)) > 0
