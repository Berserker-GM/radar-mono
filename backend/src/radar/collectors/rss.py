"""Generic RSS/Atom collector. Feeds and categories come from Source.config:

{"feeds": ["https://example/feed.xml", {"url": "...", "category": "college"}]}
"""

from typing import Any

import feedparser
import httpx
from pydantic import BaseModel, ValidationError, field_validator

from radar.collectors.base import Collector, RawEvent
from radar.models.enums import EventCategory

_SOURCE_NAME = "rss"
_TIMEOUT_SECONDS = 30.0
_MAX_SUMMARY = 500


class RssFeed(BaseModel):
    url: str
    category: EventCategory = EventCategory.OTHER

    @field_validator("url")
    @classmethod
    def _url_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("feed url is empty")
        return value.strip()


class RssCollector(Collector):
    min_expected = 1

    def __init__(self, feeds: list[RssFeed]) -> None:
        self.feeds = feeds

    async def fetch(self) -> list[RawEvent]:
        events: list[RawEvent] = []
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            for feed in self.feeds:
                response = await client.get(feed.url)
                response.raise_for_status()
                events.extend(_parse_feed(response.text, feed))
        return events


def feeds_from_config(config: Any) -> list[RssFeed]:
    if not isinstance(config, dict):
        return []
    raw = config.get("feeds")
    if not isinstance(raw, list):
        return []
    feeds: list[RssFeed] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            feeds.append(RssFeed(url=item.strip()))
        elif isinstance(item, dict):
            try:
                feeds.append(RssFeed.model_validate(item))
            except ValidationError:
                continue
    return feeds


def _parse_feed(text: str, feed: RssFeed) -> list[RawEvent]:
    parsed = feedparser.parse(text)
    # A feed that is really an HTML error page parses to nothing with bozo set.
    if parsed.bozo and not parsed.entries:
        raise TypeError(f"unparseable feed {feed.url}: {parsed.bozo_exception}")
    events: list[RawEvent] = []
    for entry in parsed.entries:
        event = _entry_to_event(entry, feed)
        if event is not None:
            events.append(event)
    return events


def _entry_to_event(entry: Any, feed: RssFeed) -> RawEvent | None:
    title = str(entry.get("title") or "").strip()
    link = str(entry.get("link") or entry.get("id") or "").strip()
    if not title or not link:
        return None
    summary = str(entry.get("summary") or "").strip()
    return RawEvent(
        source_name=_SOURCE_NAME,
        title=title,
        category=feed.category,
        external_id=link,
        url=link,
        tags=_entry_tags(entry),
        extra={"summary": summary[:_MAX_SUMMARY]} if summary else None,
    )


def _entry_tags(entry: Any) -> list[str] | None:
    raw = entry.get("tags")
    if not isinstance(raw, list):
        return None
    tags = [
        str(item.get("term")).strip()
        for item in raw
        if isinstance(item, dict) and item.get("term")
    ]
    return tags or None
