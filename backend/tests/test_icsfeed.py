import asyncio
from datetime import UTC, datetime, timedelta, timezone

import httpx
import respx

from radar.collectors.icsfeed import IcsCollector, IcsFeed, feeds_from_config
from radar.models.enums import EventCategory

_KOLKATA = timezone(timedelta(hours=5, minutes=30))
_ICS_URL = "https://cal.example/college.ics"

_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:orient-1@cal.example
SUMMARY:Club Orientation
DTSTART:20261015T100000Z
DTEND:20261015T120000Z
LOCATION:Main Hall
URL:https://clubs.example/orientation
END:VEVENT
BEGIN:VEVENT
UID:hackday-1@cal.example
SUMMARY:Hack Day
DTSTART;VALUE=DATE:20261020
DTEND;VALUE=DATE:20261022
END:VEVENT
BEGIN:VEVENT
SUMMARY:No UID here
DTSTART:20261021T100000Z
END:VEVENT
END:VCALENDAR
"""


def _collect(feeds: list) -> object:
    wrapped = [IcsFeed(url=feed) if isinstance(feed, str) else feed for feed in feeds]

    async def run() -> object:
        async with respx.mock:
            respx.get(_ICS_URL).mock(
                return_value=httpx.Response(200, content=_ICS.encode())
            )
            return await IcsCollector(feeds=wrapped).collect()

    return asyncio.run(run())


def test_feeds_from_config_reads_calendars() -> None:
    feeds = feeds_from_config({"calendars": [_ICS_URL, {"nope": 1}]})

    assert [feed.url for feed in feeds] == [_ICS_URL]
    assert feeds[0].category is EventCategory.OTHER


def test_timed_and_all_day_events_become_events() -> None:
    result = _collect([_ICS_URL])

    assert result.status == "ok"
    assert len(result.events) == 2
    timed, all_day = result.events
    assert timed.title == "Club Orientation"
    assert timed.external_id == "orient-1@cal.example"
    assert timed.start_at == datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
    assert timed.end_at == datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert timed.location == "Main Hall"
    assert timed.url == "https://clubs.example/orientation"
    assert all_day.title == "Hack Day"
    assert all_day.start_at == datetime(2026, 10, 20, tzinfo=_KOLKATA)
    # DTEND on all-day events is exclusive; the stored end covers the last day.
    assert all_day.end_at == datetime(2026, 10, 21, 23, 59, 59, tzinfo=_KOLKATA)


def test_component_without_uid_is_dropped() -> None:
    result = _collect([_ICS_URL])

    assert "No UID here" not in [event.title for event in result.events]


def test_garbage_body_is_malformed() -> None:
    async def run() -> object:
        async with respx.mock:
            respx.get(_ICS_URL).mock(
                return_value=httpx.Response(200, text="<html>not a calendar</html>")
            )
            return await IcsCollector(feeds=[IcsFeed(url=_ICS_URL)]).collect()

    result = asyncio.run(run())

    assert result.status == "malformed"
    assert result.events == []
    assert result.error
