"""Deterministic college-notice extraction. No model calls: labeled lines,
keyword roles, and dateparser. A matched span doubles as the verbatim
evidence quote, so every extracted field keeps its evidence."""

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any

import dateparser
from pydantic import BaseModel, ConfigDict, Field

from radar.collectors.base import EvidenceItem, RawEvent
from radar.models.enums import EventCategory, Lifecycle

logger = logging.getLogger(__name__)

# Zoneless notice times are local to the default display zone, then stored as UTC.
_KOLKATA = timezone(timedelta(hours=5, minutes=30))
_WHITESPACE = re.compile(r"\s+")
_MIDNIGHT = time.min
_LEADING_JUNK = re.compile(r"^[^\w\"'(\[]+")
_REMINDER_PREFIX = re.compile(r"^reminder\s*[:\-–]\s*", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Labeled lines win ("Deadline: 7th October"). Unlabeled dates are classified
# by the keyword around them, extension outranking deadline, and a date that
# matches no keyword at all stays out of the event ("mentioned is not a
# deadline").
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)"
)
_DATE_CANDIDATES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "day_month",
        re.compile(
            rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?"
            r"(?:,?\s*(?:19|20)\d{2})?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "month_day",
        re.compile(
            rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?"
            r"(?:,?\s*(?:19|20)\d{2})?\b",
            re.IGNORECASE,
        ),
    ),
    ("iso", re.compile(r"\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b")),
    ("numeric", re.compile(r"\b\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?\b")),
    (
        "relative",
        re.compile(
            r"\b(?:this|next|coming)\s+(?:mon|tues?|wed(?:nes)?|thur?s?|fri|"
            r"sat(?:ur)?|sun)[a-z]*\b|\b(?:tomorrow|today)\b",
            re.IGNORECASE,
        ),
    ),
)
_RANGE_SPLIT = re.compile(r"\s+(?:-|–|—|to|till|until)\s+", re.IGNORECASE)
_LABEL_ROLES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "registration_opens",
        re.compile(
            r"^registrations?\s+(?:opens?|starts?|begins?|commences?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "deadline_extended",
        re.compile(r"^(?:deadline\s+)?exten(?:ded|sion|s)\b", re.IGNORECASE),
    ),
    (
        "deadline",
        re.compile(
            r"^(?:deadline|last\s+date(?:\s+\w+){0,3}|register\s+by|"
            r"apply\s+(?:by|before)|submit\s+(?:by|before)|"
            r"submission\s+deadline|due\s+(?:date|by))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "date",
        re.compile(
            r"^(?:event\s+dates?|workshop\s+date|dates?|when|held\s+on|day)\b",
            re.IGNORECASE,
        ),
    ),
)
_VENUE_LABEL = re.compile(
    r"^(?:venue|location|place|where)\s*[:\-–]\s*(.+)$", re.IGNORECASE
)
_TIME_LABEL = re.compile(r"^times?\s*[:\-–]\s*(.+)$", re.IGNORECASE)
_TIME_RE = re.compile(
    r"\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?m\.?\b|\b(\d{1,2})[:.](\d{2})\b",
    re.IGNORECASE,
)
_EXTEND_KW = re.compile(
    r"\b(?:extended|postponed|rescheduled)\s+(?:to|till|until|by)\b|"
    r"\bdeadline\s+extended\b",
    re.IGNORECASE,
)
_DEADLINE_KW = re.compile(
    r"\b(?:last\s+date|deadline|register\s+by|registration\s+deadline|"
    r"apply\s+(?:by|before)|submit\s+(?:by|before)|closes?\s+(?:on|by))\b",
    re.IGNORECASE,
)
_EVENT_KW = re.compile(
    r"\b(?:held\s+on|will\s+be\s+held|scheduled\s+(?:for|on)|lecture|"
    r"workshop|seminar|webinar|hackathon|contest|session|fest|competition|"
    r"orientation|walkathon|event|examin(?:ation|e)|exam)\b",
    re.IGNORECASE,
)
_ELIGIBILITY_KW = re.compile(r"\bopen to\b|\beligib", re.IGNORECASE)
# Venues in notices are "Venue: X" or "in the X" inside the event sentence.
_VENUE_FALLBACK = re.compile(
    r"\bin the\s+([A-Za-z][\w&.'-]*(?:[\s,]+[A-Za-z][\w&.'-]*){0,2})",
    re.IGNORECASE,
)
_NOT_A_VENUE = frozenset(
    {
        "morning",
        "afternoon",
        "evening",
        "beginning",
        "end",
        "meantime",
        "first place",
        "meant time",
    }
)
_BLOCKLIST_RE = re.compile(
    r"\b(?:holiday circular|circular|holiday|timetable|fee payment|fees|"
    r"payment|revaluation|marks card)\b",
    re.IGNORECASE,
)
_RELATIVE_ONLY = re.compile(
    r"(?:(this|next|coming)\s+((?:mon|tues?|wed(?:nes)?|thur?s?|fri|"
    r"sat(?:ur)?|sun)[a-z]*))|(tomorrow|today)",
    re.IGNORECASE,
)
_WEEKDAYS: dict[str, int] = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "satur": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
_TAIL_TIME_WINDOW = 24
_MAX_TITLE = 150
_MAX_ELIGIBILITY = 200
_LABELED_CONFIDENCE = 1.0
_KEYWORD_CONFIDENCE = 0.85


class ExtractionError(Exception):
    """Notice extraction failed unexpectedly."""


class NoticeCategory(StrEnum):
    COLLEGE = "college"
    SCHOLARSHIP = "scholarship"
    CLUB = "club"
    PLACEMENT = "placement"
    EXAM = "exam"
    OTHER = "other"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quote: str
    confidence: float


class ExtractedNotice(BaseModel):
    """Fields mirror tests/fixtures/college/noticeNN.expected.json."""

    model_config = ConfigDict(extra="ignore")

    actionable: bool = False
    title: str | None = None
    category: NoticeCategory = NoticeCategory.COLLEGE
    registration_opens: datetime | None = None
    date: datetime | None = None
    start: datetime | None = None
    end: datetime | None = None
    deadline: datetime | None = None
    deadline_extended: datetime | None = None
    venue: str | None = None
    link: str | None = None
    eligibility: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    evidence: dict[str, Evidence] = Field(default_factory=dict)


CATEGORY_KEYWORDS: tuple[tuple[NoticeCategory, re.Pattern[str]], ...] = (
    (
        NoticeCategory.SCHOLARSHIP,
        re.compile(r"\b(?:scholarship|stipend|freeship|financial aid)\b", re.I),
    ),
    (
        NoticeCategory.PLACEMENT,
        re.compile(
            r"\b(?:placement|recruitment drive|internship|job opening|"
            r"campus drive)\b",
            re.IGNORECASE,
        ),
    ),
    (
        NoticeCategory.CLUB,
        re.compile(r"\b(?:club|chapter|recruit\w*)\b", re.IGNORECASE),
    ),
    (
        NoticeCategory.EXAM,
        re.compile(
            r"\b(?:mid\s?-?sem|end\s?-?sem|semester\s+exam|quiz|"
            r"examination|test schedule)\b",
            re.IGNORECASE,
        ),
    ),
)


def extract_notice(text: str, *, today: date | None = None) -> ExtractedNotice:
    current_day = today if today is not None else date.today()
    try:
        return _extract(text, current_day)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"notice extraction failed: {exc}") from exc


def to_raw_event(
    notice: ExtractedNotice,
    *,
    source_name: str,
    url: str,
    notice_hash: str | None = None,
    list_title: str | None = None,
) -> RawEvent | None:
    if not notice.actionable:
        return None
    title = (notice.title or list_title or "").strip()
    if not title:
        return None

    start = notice.date or notice.start
    end = notice.end
    effective_deadline = notice.deadline_extended or notice.deadline

    confidence: dict[str, float] = {}
    evidence: list[EvidenceItem] = []

    def emit(event_field: str, key: str) -> None:
        item = notice.evidence.get(key)
        found = notice.confidence.get(key)
        if item is None or found is None:
            return
        confidence[event_field] = found
        evidence.append(
            EvidenceItem(
                field=event_field,
                source_url=url,
                source_text=item.quote,
                confidence=found,
            )
        )

    if start is not None:
        emit("start_at", "date" if notice.date is not None else "start")
    if end is not None:
        emit("end_at", "end")
    if effective_deadline is not None:
        emit(
            "registration_deadline",
            "deadline_extended" if notice.deadline_extended is not None else "deadline",
        )
    if notice.venue is not None:
        emit("location", "venue")
    if notice.eligibility is not None:
        emit("eligibility", "eligibility")
    emit("link", "link")
    if notice.registration_opens is not None:
        emit("registration_opens", "registration_opens")

    extra: dict[str, Any] = {}
    if notice.registration_opens is not None:
        extra["other_dates"] = [
            {
                "type": "registration_opens",
                "label": "Registration opens",
                "at": _localize(notice.registration_opens).isoformat(),
                "confidence": notice.confidence.get(
                    "registration_opens", _LABELED_CONFIDENCE
                ),
            }
        ]
    if notice.link:
        extra["link"] = notice.link
    if notice.deadline is not None and notice.deadline_extended is not None:
        extra["original_deadline"] = _localize(notice.deadline).isoformat()
    if notice.deadline_extended is not None:
        extra["deadline_changed"] = True
        extra["mentions_extension"] = True
    if notice_hash is not None:
        extra["notice_hash"] = notice_hash
    extra["notice_category"] = notice.category.value

    return RawEvent(
        source_name=source_name,
        title=title,
        category=_event_category(notice.category),
        external_id=url,
        url=url,
        start_at=_localize_optional(start),
        end_at=_localize_optional(end),
        registration_deadline=_localize_optional(effective_deadline),
        location=notice.venue,
        eligibility=notice.eligibility,
        lifecycle=Lifecycle.UNKNOWN,
        confidence=confidence,
        evidence=evidence,
        extra=extra,
    )


def normalize_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text).casefold().strip()


def _extract(text: str, today: date) -> ExtractedNotice:
    category = _classify(text)
    blocked = _BLOCKLIST_RE.search(text)
    if blocked is not None:
        return ExtractedNotice(
            actionable=False,
            title=blocked.group(0).strip(),
            category=category,
        )

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    dates: dict[str, datetime] = {}
    quotes: dict[str, str] = {}
    confs: dict[str, float] = {}
    venue: str | None = None
    venue_quote: str | None = None
    venue_conf: float | None = None
    link: str | None = None
    eligibility: str | None = None
    eligibility_quote: str | None = None
    last_role: str | None = None

    for line in lines:
        clean = _strip_junk(line)
        if link is None:
            found = _URL_RE.search(line)
            if found is not None:
                link = found.group(0).rstrip(").,;>]")
                quotes["link"] = line
                confs["link"] = _LABELED_CONFIDENCE

        time_match = _TIME_LABEL.match(clean)
        if time_match is not None:
            _apply_time(dates, last_role, time_match.group(1))
            continue

        venue_match = _VENUE_LABEL.match(clean)
        if venue_match is not None:
            venue = _clean_value(venue_match.group(1))
            venue_quote = line
            venue_conf = _LABELED_CONFIDENCE
            continue

        role, rest = _label_role(clean)
        if role is not None:
            values = _parse_rest(rest, today)
            if values:
                _assign(dates, quotes, confs, role, values, line)
                last_role = "start" if role == "date" and "start" in dates else role
            continue

        assigned = _assign_unlabeled(line, today, dates, quotes, confs)
        if assigned:
            last_role = assigned

    if venue is None:
        for line in lines:
            found = _venue_from_line(line)
            if found is not None:
                venue, venue_quote, venue_conf = found
                break

    for line in lines:
        if _ELIGIBILITY_KW.search(line) and eligibility is None:
            eligibility = _clean_value(line)[:_MAX_ELIGIBILITY]
            eligibility_quote = line
            break

    if not dates and link is None:
        return ExtractedNotice(
            actionable=False,
            title=_title_from(lines),
            category=category,
        )

    notice = ExtractedNotice(
        actionable=True,
        title=_title_from(lines),
        category=category,
        venue=venue,
        link=link,
        eligibility=eligibility,
    )
    if "registration_opens" in dates:
        notice.registration_opens = dates["registration_opens"]
    if "date" in dates:
        notice.date = dates["date"]
    if "start" in dates:
        notice.start = dates["start"]
    if "end" in dates:
        notice.end = dates["end"]
    if "deadline" in dates:
        notice.deadline = dates["deadline"]
    if "deadline_extended" in dates:
        notice.deadline_extended = dates["deadline_extended"]

    for key, quote in quotes.items():
        conf = confs.get(key, _LABELED_CONFIDENCE)
        notice.evidence[key] = Evidence(quote=quote, confidence=conf)
        notice.confidence[key] = conf
    if venue is not None and venue_quote is not None:
        notice.venue = venue
        notice.evidence["venue"] = Evidence(
            quote=venue_quote, confidence=venue_conf or _KEYWORD_CONFIDENCE
        )
        notice.confidence["venue"] = venue_conf or _KEYWORD_CONFIDENCE
    if eligibility is not None and eligibility_quote is not None:
        notice.evidence["eligibility"] = Evidence(
            quote=eligibility_quote, confidence=_KEYWORD_CONFIDENCE
        )
        notice.confidence["eligibility"] = _KEYWORD_CONFIDENCE
    return notice


def _label_role(clean: str) -> tuple[str | None, str]:
    for role, pattern in _LABEL_ROLES:
        matched = pattern.match(clean)
        if matched is None:
            continue
        rest = clean[matched.end() :]
        if re.match(r"^\s*[:\-–]", rest) is None:
            continue
        return role, re.sub(r"^\s*[:\-–]\s*", "", rest)
    return None, ""


def _assign(
    dates: dict[str, datetime],
    quotes: dict[str, str],
    confs: dict[str, float],
    role: str,
    values: list[datetime],
    line: str,
) -> None:
    if role == "date" and len(values) >= 2:
        dates.setdefault("start", values[0])
        dates.setdefault("end", values[1])
        quotes.setdefault("start", line)
        quotes.setdefault("end", line)
        confs.setdefault("start", _LABELED_CONFIDENCE)
        confs.setdefault("end", _LABELED_CONFIDENCE)
        return
    value = values[0]
    dates.setdefault(role, value)
    quotes.setdefault(role, line)
    confs.setdefault(role, _LABELED_CONFIDENCE)


def _assign_unlabeled(
    line: str,
    today: date,
    dates: dict[str, datetime],
    quotes: dict[str, str],
    confs: dict[str, float],
) -> str | None:
    moment = _extract_datetime(line, today)
    if moment is None:
        return None
    if _EXTEND_KW.search(line) is not None:
        role = "deadline_extended"
    elif _DEADLINE_KW.search(line) is not None:
        role = "deadline"
    elif _EVENT_KW.search(line) is not None:
        role = "date"
    else:
        return None
    if role in dates or (role == "date" and ("date" in dates or "start" in dates)):
        return None
    dates[role] = moment
    quotes[role] = line
    confs[role] = _KEYWORD_CONFIDENCE
    return role


def _apply_time(dates: dict[str, datetime], last_role: str | None, raw: str) -> None:
    if last_role is None or last_role not in dates:
        return
    parsed = _parse_time(raw)
    if parsed is None:
        return
    current = dates[last_role]
    if current.time() != _MIDNIGHT:
        return
    dates[last_role] = current.replace(hour=parsed[0], minute=parsed[1])


def _parse_rest(rest: str, today: date) -> list[datetime]:
    parts = [part for part in _RANGE_SPLIT.split(rest.strip()) if part.strip()]
    if len(parts) >= 2:
        first = _extract_datetime(parts[0], today)
        second = _extract_datetime(parts[1], today)
        if first is not None and second is not None:
            return [first, second]
        if first is not None:
            return [first]
        return [second] if second is not None else []
    if not parts:
        return []
    moment = _extract_datetime(parts[0], today)
    return [] if moment is None else [moment]


def _extract_datetime(text: str, today: date) -> datetime | None:
    for _name, pattern in _DATE_CANDIDATES:
        matched = pattern.search(text)
        if matched is None:
            continue
        moment = _parse_date_text(matched.group(0), today)
        if moment is None:
            continue
        tail = text[matched.end() : matched.end() + _TAIL_TIME_WINDOW]
        clock = _parse_time(tail)
        if clock is not None and moment.time() == _MIDNIGHT:
            moment = moment.replace(hour=clock[0], minute=clock[1])
        return moment
    return None


def _parse_date_text(text: str, today: date) -> datetime | None:
    relative = _RELATIVE_ONLY.fullmatch(text.strip())
    if relative is not None:
        return _relative_datetime(text.strip(), today)
    parsed = dateparser.parse(
        text.strip(),
        settings={
            "RELATIVE_BASE": datetime.combine(today, _MIDNIGHT),
            "DATE_ORDER": "DMY",
            "STRICT_PARSING": False,
        },
    )
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _relative_datetime(text: str, today: date) -> datetime | None:
    matched = _RELATIVE_ONLY.fullmatch(text)
    if matched is None:
        return None
    if matched.group(3) is not None:
        offset = 1 if matched.group(3) == "tomorrow" else 0
        return datetime.combine(today + timedelta(days=offset), _MIDNIGHT)
    weekday = _weekday_index(matched.group(2))
    if weekday is None:
        return None
    ahead = (weekday - today.weekday()) % 7
    if matched.group(1) == "next" and ahead == 0:
        ahead = 7
    return datetime.combine(today + timedelta(days=ahead), _MIDNIGHT)


def _weekday_index(word: str) -> int | None:
    return _WEEKDAYS.get(word.casefold())


def _parse_time(text: str) -> tuple[int, int] | None:
    matched = _TIME_RE.search(text)
    if matched is None:
        return None
    if matched.group(1) is not None:
        hour = int(matched.group(1))
        minute = int(matched.group(2) or 0)
        meridiem = matched.group(3).casefold()
        if meridiem == "p" and hour != 12:
            hour += 12
        if meridiem == "a" and hour == 12:
            hour = 0
        return hour, minute
    return int(matched.group(4)), int(matched.group(5))


def _venue_from_line(line: str) -> tuple[str, str, float] | None:
    matched = _VENUE_FALLBACK.search(line)
    if matched is None:
        return None
    value = _clean_value(matched.group(1))
    if not value or value.casefold() in _NOT_A_VENUE:
        return None
    return value, matched.group(0).strip(), _KEYWORD_CONFIDENCE


def _classify(text: str) -> NoticeCategory:
    folded = text.casefold()
    for category, pattern in CATEGORY_KEYWORDS:
        if pattern.search(folded) is not None:
            return category
    return NoticeCategory.COLLEGE


def _title_from(lines: list[str]) -> str | None:
    if not lines:
        return None
    title = _REMINDER_PREFIX.sub("", _strip_junk(lines[0])).strip().rstrip(":")
    title = title.strip()
    if not title:
        return None
    return title[:_MAX_TITLE]


def _strip_junk(line: str) -> str:
    return _LEADING_JUNK.sub("", line).strip()


def _clean_value(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().strip(".,;:—–- ").strip()


def _event_category(category: NoticeCategory) -> EventCategory:
    if category is NoticeCategory.EXAM:
        return EventCategory.OTHER
    return EventCategory(category.value)


def _localize_optional(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _localize(value)


def _localize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_KOLKATA)
    return value.astimezone(_KOLKATA)
