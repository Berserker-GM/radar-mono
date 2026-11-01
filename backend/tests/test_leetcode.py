import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx

from radar.collectors.leetcode import LeetCodeCollector
from radar.models.enums import EventCategory, EventMode, Lifecycle

_NOW = datetime.now(UTC)
_FUTURE = _NOW + timedelta(days=3)
_PAST = _NOW - timedelta(days=3)
_URL = "https://leetcode.com/graphql"


def _payload() -> dict[str, Any]:
    return {
        "data": {
            "topTwoContests": [
                {
                    "title": "Weekly Contest 500",
                    "titleSlug": "weekly-contest-500",
                    "startTime": int(_FUTURE.timestamp()),
                    "duration": 5400,
                },
                {
                    "title": "Biweekly Contest Old",
                    "titleSlug": "biweekly-contest-old",
                    "startTime": int(_PAST.timestamp()),
                    "duration": 5400,
                },
            ]
        }
    }


def _collect(payload: object) -> Any:
    async def run() -> Any:
        async with respx.mock:
            respx.post(_URL).mock(return_value=httpx.Response(200, json=payload))
            return await LeetCodeCollector().collect()

    return asyncio.run(run())


def test_upcoming_contests_become_events_and_past_ones_do_not() -> None:
    result = _collect(_payload())

    assert result.status == "ok"
    assert [event.title for event in result.events] == ["Weekly Contest 500"]
    event = result.events[0]
    assert event.category is EventCategory.CONTEST
    assert event.external_id == "weekly-contest-500"
    assert event.url == "https://leetcode.com/contest/weekly-contest-500/"
    assert event.start_at == _FUTURE.replace(microsecond=0)
    assert event.end_at == _FUTURE.replace(microsecond=0) + timedelta(seconds=5400)
    assert event.mode is EventMode.ONLINE
    assert event.lifecycle is Lifecycle.UPCOMING


def test_graphql_errors_are_malformed() -> None:
    result = _collect({"errors": [{"message": "bad query"}]})

    assert result.status == "malformed"
    assert result.events == []
    assert result.error


def test_missing_fields_are_skipped() -> None:
    payload = {
        "data": {
            "topTwoContests": [
                {"title": "No slug", "startTime": int(_FUTURE.timestamp())},
                {
                    "title": "No time",
                    "titleSlug": "no-time",
                },
            ]
        }
    }

    result = _collect(payload)

    assert result.status == "ok"
    assert result.events == []
