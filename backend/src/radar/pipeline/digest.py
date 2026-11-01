"""Daily digest: the feed as one Discord/Telegram webhook message."""

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from radar.pipeline.scoring import FeedItem, build_feed

logger = logging.getLogger(__name__)

USER_ID = "local"
_IST = timezone(timedelta(hours=5, minutes=30))
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_SECTION_LIMITS: dict[str, int] = {
    "deadlines": 5,
    "changes": 3,
    "new": 5,
    "college": 3,
}


def build_digest_message(session: Session, now: datetime) -> str:
    moment = _as_utc(now)
    feed = build_feed(session, USER_ID, moment)
    header = f"Radar digest — {moment.astimezone(_IST).strftime('%a, %d %b')}"
    lines: list[str] = [header]

    deadlines = feed.deadlines_soon[: _SECTION_LIMITS["deadlines"]]
    changes = feed.important_updates[: _SECTION_LIMITS["changes"]]
    fresh = [item for item in feed.new_opportunities if item not in deadlines][
        : _SECTION_LIMITS["new"]
    ]
    college = feed.college[: _SECTION_LIMITS["college"]]

    _section(lines, "Deadlines soon", deadlines, _deadline_line)
    _section(lines, "Changed", changes, _change_line)
    _section(lines, "New opportunities", fresh, _simple_line)
    _section(lines, "College", college, _simple_line)

    if len(lines) == 1:
        lines.append("Nothing needs your attention today.")
    return "\n".join(lines)


def send_digest(
    session: Session,
    *,
    url: str | None = None,
    now: datetime | None = None,
    poster: Any = None,
) -> bool:
    """Post the digest. Returns False when there is nothing to send to."""

    from radar.config import get_settings
    from radar.pipeline.scan import post_alert

    target = url if url is not None else get_settings().digest_webhook_url
    if not target:
        return False
    moment = now if now is not None else datetime.now(UTC)
    message = build_digest_message(session, moment)
    (poster if poster is not None else post_alert)(message, target)
    return True


def _section(
    lines: list[str],
    heading: str,
    items: list[FeedItem],
    render: Any,
) -> None:
    if not items:
        return
    lines.append("")
    lines.append(f"{heading}:")
    for item in items:
        lines.append(f"• {render(item)}")


def _deadline_line(item: FeedItem) -> str:
    return f"{item.title} — {_when(item)}"


def _change_line(item: FeedItem) -> str:
    if item.diff:
        return f"{item.title} ({item.diff})"
    return item.title


def _simple_line(item: FeedItem) -> str:
    return item.title


def _when(item: FeedItem) -> str:
    moment = _as_utc(item.key_at)
    if moment is None:
        return "no date"
    local = moment.astimezone(_IST)
    clock = ""
    if local.hour or local.minute:
        clock = local.strftime(", %I:%M %p").replace(", 0", ", ", 1)
    label = f"{_MONTHS[local.month - 1]} {local.day}{clock}"
    if item.urgency.value == "critical":
        return f"{label} (critical)"
    return label


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
