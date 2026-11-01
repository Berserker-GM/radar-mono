"""ICS feed collector. Calendar URLs and categories come from Source.config:

{"calendars": ["https://example.org/calendar.ics"]}

Recurring events (RRULE) are kept as their single base occurrence; expansion
is left to the calendar client.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx
from icalendar import Calendar
from pydantic import BaseModel, ValidationError, field_validator

from radar.collectors.base import Collector, CollectResult, RawEvent
from radar.models.enums import EventCategory

_SOURCE_NAME = "ics"
_TIMEOUT_SECONDS = 30.0
_KOLKATA = timezone(timedelta(hours=5, minutes=30))


class IcsFeed(BaseModel):
    url: str
    category: EventCategory = EventCategory.OTHER

    @field_validator("url")
    @classmethod
    def _url_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("calendar url is empty")
        return value.strip()


def feeds_from_config(config: Any) -> list[IcsFeed]:
    if not isinstance(config, dict):
        return []
    raw = config.get("calendars")
    if not isinstance(raw, list):
        return []
    feeds: list[IcsFeed] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            feeds.append(IcsFeed(url=item.strip()))
        elif isinstance(item, dict):
            try:
                feeds.append(IcsFeed.model_validate(item))
            except ValidationError:
                continue
    return feeds


class IcsCollector(Collector):
    def __init__(self, feeds: list[IcsFeed]) -> None:
        self.feeds = feeds

    async def collect(self) -> CollectResult:
        try:
            return await super().collect()
        except ValueError as exc:
            # Calendar.from_ical signals garbage input with ValueError.
            return CollectResult(status="malformed", events=[], error=str(exc))

    async def fetch(self) -> list[RawEvent]:
        events: list[RawEvent] = []
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            for feed in self.feeds:
                response = await client.get(feed.url)
                response.raise_for_status()
                events.extend(_parse_calendar(response.content, feed))
        return events


def _parse_calendar(content: bytes, feed: IcsFeed) -> list[RawEvent]:
    calendar = Calendar.from_ical(content)
    events: list[RawEvent] = []
    for component in calendar.walk("VEVENT"):
        event = _component_to_event(component, feed)
        if event is not None:
            events.append(event)
    return events


def _component_to_event(component: Any, feed: IcsFeed) -> RawEvent | None:
    title = str(component.get("summary") or "").strip()
    uid = str(component.get("uid") or "").strip()
    if not title or not uid:
        return None
    start_pair = _as_datetime(component.get("dtstart"))
    if start_pair is None:
        return None
    start, start_is_date = start_pair
    end = None
    end_pair = _as_datetime(component.get("dtend"))
    if end_pair is not None:
        end = end_pair[0]
        if end <= start:
            end = None
        elif start_is_date and end_pair[1]:
            # DTEND is exclusive for all-day events; store the last covered
            # instant instead of midnight of the day after.
            end = end - timedelta(seconds=1)
    url = str(component.get("url") or "").strip() or None
    location = str(component.get("location") or "").strip() or None
    return RawEvent(
        source_name=_SOURCE_NAME,
        title=title,
        category=feed.category,
        external_id=uid,
        url=url,
        start_at=start,
        end_at=end,
        location=location,
        extra={"feed_url": feed.url},
    )


def _as_datetime(value: Any) -> tuple[datetime, bool] | None:
    if value is None:
        return None
    moment = getattr(value, "dt", None)
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            return moment.replace(tzinfo=_KOLKATA), False
        return moment, False
    if isinstance(moment, date):
        return datetime.combine(moment, time.min, tzinfo=_KOLKATA), True
    return None
