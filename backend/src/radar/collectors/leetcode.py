"""LeetCode contest collector. The public GraphQL endpoint lists the two
scheduled contests (weekly + biweekly); LeetCode never schedules more."""

from datetime import UTC, datetime, timedelta

import httpx

from radar.collectors.base import Collector, RawEvent
from radar.models.enums import EventCategory, EventMode, Lifecycle

_SOURCE_NAME = "leetcode"
_URL = "https://leetcode.com/graphql"
_QUERY = "query { topTwoContests { title titleSlug startTime duration } }"
_TIMEOUT_SECONDS = 30.0


class LeetCodeCollector(Collector):
    async def fetch(self) -> list[RawEvent]:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _URL,
                json={"query": _QUERY},
                headers={
                    "Content-Type": "application/json",
                    "Referer": "https://leetcode.com/",
                },
            )
            response.raise_for_status()
            payload = response.json()
        contests = payload["data"]["topTwoContests"]
        now = datetime.now(UTC)
        events: list[RawEvent] = []
        for contest in contests:
            event = _contest_to_event(contest, now)
            if event is not None:
                events.append(event)
        return events


def _contest_to_event(contest: dict, now: datetime) -> RawEvent | None:
    title = str(contest.get("title") or "").strip()
    slug = str(contest.get("titleSlug") or "").strip()
    started = contest.get("startTime")
    if not title or not slug or not isinstance(started, (int, float)):
        return None
    start = datetime.fromtimestamp(started, tz=UTC)
    if start < now:
        return None
    duration = contest.get("duration")
    end = (
        start + timedelta(seconds=duration)
        if isinstance(duration, (int, float)) and duration > 0
        else None
    )
    return RawEvent(
        source_name=_SOURCE_NAME,
        title=title,
        category=EventCategory.CONTEST,
        external_id=slug,
        url=f"https://leetcode.com/contest/{slug}/",
        start_at=start,
        end_at=end,
        mode=EventMode.ONLINE,
        lifecycle=Lifecycle.UPCOMING,
        tags=["leetcode"],
    )
