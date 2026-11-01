import asyncio
from typing import Any

import httpx
import pytest
import respx

from radar.collectors.rss import RssCollector, RssFeed, feeds_from_config
from radar.models.enums import EventCategory

FEED_URL = "https://dept.example/feed.xml"

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Dept Notices</title>
<link>https://dept.example/</link>
<description>Notices</description>
<item>
  <title>Cloud Workshop</title>
  <link>https://dept.example/notices/cloud</link>
  <description>Hands-on cloud workshop</description>
  <category>workshop</category>
</item>
<item>
  <title>Linkless item</title>
  <description>no link here</description>
</item>
</channel></rss>
"""


def _collect(feeds: list) -> Any:
    wrapped = [
        RssFeed(url=feed) if isinstance(feed, str) else RssFeed.model_validate(feed)
        for feed in feeds
    ]

    async def run() -> Any:
        async with respx.mock:
            respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=_RSS))
            return await RssCollector(feeds=wrapped).collect()

    return asyncio.run(run())


def test_feeds_from_config_accepts_strings_and_objects() -> None:
    feeds = feeds_from_config(
        {
            "feeds": [
                FEED_URL,
                {"url": "https://other.example/rss", "category": "hackathon"},
                42,
                {"url": ""},
            ]
        }
    )

    assert [feed.url for feed in feeds] == [
        FEED_URL,
        "https://other.example/rss",
    ]
    assert feeds[0].category is EventCategory.OTHER
    assert feeds[1].category is EventCategory.HACKATHON


def test_feeds_from_config_ignores_junk() -> None:
    assert feeds_from_config(None) == []
    assert feeds_from_config({}) == []
    assert feeds_from_config({"feeds": "not-a-list"}) == []


def test_entries_become_events_with_the_configured_category() -> None:
    result = _collect([{"url": FEED_URL, "category": "college"}])

    assert result.status == "ok"
    assert len(result.events) == 1
    event = result.events[0]
    assert event.title == "Cloud Workshop"
    assert event.url == "https://dept.example/notices/cloud"
    assert event.external_id == "https://dept.example/notices/cloud"
    assert event.category is EventCategory.COLLEGE
    assert event.tags == ["workshop"]
    assert event.extra is not None
    assert event.extra["summary"] == "Hands-on cloud workshop"


def test_entry_without_a_link_is_dropped() -> None:
    result = _collect([FEED_URL])

    assert result.status == "ok"
    assert [event.title for event in result.events] == ["Cloud Workshop"]


def test_html_error_page_is_malformed() -> None:
    async def run() -> Any:
        async with respx.mock:
            respx.get(FEED_URL).mock(
                return_value=httpx.Response(200, text="<html>502 blown up</html>")
            )
            return await RssCollector(feeds=[RssFeed(url=FEED_URL)]).collect()

    result = asyncio.run(run())

    assert result.status == "malformed"
    assert result.events == []
    assert result.error


def test_http_error_is_unreachable() -> None:
    async def run() -> Any:
        async with respx.mock:
            respx.get(FEED_URL).mock(side_effect=httpx.ConnectError("dns failed"))
            return await RssCollector(feeds=[RssFeed(url=FEED_URL)]).collect()

    result = asyncio.run(run())

    assert result.status == "unreachable"
    assert result.events == []


def test_unknown_category_is_rejected() -> None:
    # feeds_from_config skips entries it cannot validate.
    feeds = feeds_from_config({"feeds": [{"url": FEED_URL, "category": "nope"}]})

    assert feeds == []
    with pytest.raises(ValueError):
        RssFeed.model_validate({"url": FEED_URL, "category": "nope"})
