"""iCalendar export: the important slice of the feed as a subscribeable feed."""

from datetime import UTC, datetime, timedelta

from icalendar import Calendar
from icalendar import Event as IcsEvent
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.models import Event, UserEventState, UserState
from radar.models.enums import Lifecycle
from radar.pipeline.scoring import Feed, build_feed, key_datetime

PRODID = "-//radar//feed//EN"
VERSION = "2.0"
# Deadlines are instants; give them the same length Google Calendar sync uses.
DEFAULT_DURATION = timedelta(minutes=30)


def select_export_events(session: Session, user_id: str, now: datetime) -> list[Event]:
    """Saved + college + urgent/important events, mirroring the feed's filters.

    Closed, hidden and ignored events never export.
    """

    moment = _as_utc(now)
    feed = build_feed(session, user_id, moment)
    wanted = _ids(feed)
    if not wanted:
        return []
    rows = session.scalars(select(Event).where(Event.id.in_(wanted))).all()
    states = {
        row.event_id: row
        for row in session.scalars(
            select(UserEventState).where(UserEventState.user_id == user_id)
        )
    }
    selected: list[Event] = []
    for event in rows:
        state = states.get(event.id)
        if state is not None and state.state == UserState.IGNORED:
            continue
        if _closed(event, moment):
            continue
        selected.append(event)
    selected.sort(key=_sort_key)
    return selected


def build_ics(events: list[Event], now: datetime) -> str:
    calendar = Calendar()
    calendar.add("PRODID", PRODID)
    calendar.add("VERSION", VERSION)
    calendar.add("CALSCALE", "GREGORIAN")
    calendar.add("METHOD", "PUBLISH")
    calendar.add("X-WR-CALNAME", "Radar")
    moment = _as_utc(now)
    for event in events:
        component = _component(event, moment)
        if component is not None:
            calendar.add_component(component)
    return calendar.to_ical().decode("utf-8")


def _ids(feed: Feed) -> set[int]:
    wanted: set[int] = set()
    for item in (
        *feed.saved,
        *feed.college,
        *feed.deadlines_soon,
        *feed.important_updates,
    ):
        wanted.add(item.event_id)
    return wanted


def _closed(event: Event, now: datetime) -> bool:
    if event.lifecycle in (Lifecycle.COMPLETED, Lifecycle.CANCELLED):
        return True
    key = key_datetime(event)
    return key is not None and _as_utc(key) < now


def _component(event: Event, now: datetime) -> IcsEvent | None:
    start = key_datetime(event)
    if start is None:
        return None
    start = _as_utc(start)
    if start < now:
        return None
    end = _as_utc(event.end_at) if event.end_at is not None else None
    if end is None or end <= start:
        end = start + DEFAULT_DURATION
    component = IcsEvent()
    component.add("uid", f"radar-{event.id}@radar.local")
    component.add("dtstamp", now)
    component.add("dtstart", start)
    component.add("dtend", end)
    component.add("summary", event.title)
    if event.url:
        component.add("url", event.url)
        component.add("description", event.url)
    if event.location:
        component.add("location", event.location)
    return component


def _sort_key(event: Event) -> tuple[datetime, int]:
    key = key_datetime(event)
    moment = _as_utc(key) if key is not None else datetime.max.replace(tzinfo=UTC)
    return (moment, event.id)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
