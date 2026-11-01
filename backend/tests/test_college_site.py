import asyncio
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from radar.collectors.college.extract import ExtractedNotice, NoticeCategory
from radar.collectors.college.site import (
    CollegeSiteCollector,
    NoticeSelectors,
    html_to_text,
    load_seen_notices,
    notice_content_hash,
)
from radar.models import (
    Base,
    EventCategory,
    HealthStatus,
    Lifecycle,
    Source,
    SourceKind,
)
from radar.pipeline.ingest import ingest

_FIXTURES = Path(__file__).parent / "fixtures" / "college"
_SITE = _FIXTURES / "site"
LIST_URL = "https://college.example/notices/"
OLD_URL = "https://college.example/notices/old"
WORKSHOP_URL = "https://college.example/notices/workshop"
SCHOLARSHIP_URL = "https://college.example/notices/scholarship"
SELECTORS = NoticeSelectors(
    title="a.notice-title",
    link="a.notice-title",
    date="time.notice-date",
)
TODAY = date(2026, 10, 11)

_SCHOLARSHIP_HTML = """
<html><body><article>
<h1>Merit Scholarship 2026</h1>
<p>Applications for the merit scholarship are open to 3rd year students.
Last date to apply: 20th October 2026, 5:00 PM.</p>
</article></body></html>
"""


def _html(name: str) -> str:
    return (_SITE / name).read_text(encoding="utf-8")


def _seen_hash(name: str) -> str:
    return notice_content_hash(html_to_text(_html(name)))


def _collect(
    seen: set[tuple[str, str]],
    *,
    list_html: str | None = None,
    selectors: NoticeSelectors = SELECTORS,
    pages: dict[str, str] | None = None,
) -> Any:
    html = _html("list.html") if list_html is None else list_html
    details = pages
    if details is None and list_html is None:
        details = {"old": _html("old.html"), "workshop": _html("workshop.html")}

    async def run() -> Any:
        async with respx.mock:
            respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=html))
            if details and "old" in details:
                respx.get(OLD_URL).mock(
                    return_value=httpx.Response(200, text=details["old"])
                )
            if details and "workshop" in details:
                respx.get(WORKSHOP_URL).mock(
                    return_value=httpx.Response(200, text=details["workshop"])
                )
            if details and "scholarship" in details:
                respx.get(SCHOLARSHIP_URL).mock(
                    return_value=httpx.Response(200, text=details["scholarship"])
                )
            collector = CollegeSiteCollector(
                list_url=LIST_URL,
                selectors=selectors,
                seen=seen,
                today=TODAY,
            )
            return await collector.collect()

    return asyncio.run(run())


def test_new_notice_is_extracted_once() -> None:
    result = _collect(seen={(OLD_URL, _seen_hash("old.html"))})

    assert result.status == "ok"
    assert result.error is None
    assert len(result.events) == 1
    event = result.events[0]
    assert event.category is EventCategory.COLLEGE
    assert event.external_id == WORKSHOP_URL
    assert event.url == WORKSHOP_URL
    assert event.lifecycle is Lifecycle.UNKNOWN
    assert event.registration_deadline is None
    assert event.location == "main auditorium"
    assert event.start_at is not None
    assert event.start_at.day == 15
    assert event.confidence["start_at"] == 0.85
    assert event.extra["notice_hash"] == _seen_hash("workshop.html")
    assert event.extra["notice_category"] == "college"
    by_field = {item.field: item for item in event.evidence}
    assert by_field["start_at"].source_url == WORKSHOP_URL
    assert "October 15" in by_field["start_at"].source_text


def test_scholarship_notice_is_still_a_college_event() -> None:
    result = _collect(
        seen=set(),
        list_html="""
        <a class="notice-title" href="/notices/scholarship">Merit Scholarship 2026</a>
        """,
        pages={"scholarship": _SCHOLARSHIP_HTML},
    )

    assert result.status == "ok"
    assert len(result.events) == 1
    event = result.events[0]
    assert event.category is EventCategory.COLLEGE
    assert event.extra["notice_category"] == "scholarship"
    assert event.registration_deadline is not None
    assert event.registration_deadline.hour == 17


def test_unchanged_notice_is_skipped() -> None:
    seen = {
        (OLD_URL, _seen_hash("old.html")),
        (WORKSHOP_URL, _seen_hash("workshop.html")),
    }

    result = _collect(seen)

    assert result.status == "ok"
    assert result.error is None
    assert result.events == []


def test_changed_hash_is_reextracted() -> None:
    seen = {
        (OLD_URL, _seen_hash("old.html")),
        (WORKSHOP_URL, "stale"),
    }

    result = _collect(seen)

    assert [event.external_id for event in result.events] == [WORKSHOP_URL]


def test_selectors_matching_nothing_is_malformed() -> None:
    result = _collect(
        seen=set(),
        list_html=_html("empty.html"),
        pages={},
    )

    assert result.status == "malformed"
    assert result.error == "notice selectors matched nothing"
    assert result.events == []


def test_uneven_selectors_are_malformed() -> None:
    html = """
    <a class="notice-title" href="/notices/a">A</a>
    <a class="notice-title" href="/notices/b">B</a>
    <a class="only" href="/notices/a">A</a>
    <time class="notice-date">1 Oct 2026</time>
    """
    result = _collect(
        seen=set(),
        list_html=html,
        selectors=NoticeSelectors(
            title="a.notice-title",
            link="a.only",
            date="time.notice-date",
        ),
        pages={},
    )

    assert result.status == "malformed"
    assert result.error == "notice title and link selectors matched different counts"
    assert result.events == []


def test_network_failure_is_unreachable() -> None:
    async def run() -> Any:
        async with respx.mock:
            respx.get(LIST_URL).mock(side_effect=httpx.ConnectError("dns failed"))
            collector = CollegeSiteCollector(
                list_url=LIST_URL,
                selectors=SELECTORS,
                today=TODAY,
            )
            return await collector.collect()

    result = asyncio.run(run())

    assert result.status == "unreachable"
    assert result.events == []
    assert result.error


def test_unactionable_detail_page_yields_no_events() -> None:
    result = _collect(
        seen=set(),
        list_html="""
        <a class="notice-title" href="/notices/old">Archived notice</a>
        """,
        pages={"old": _html("old.html")},
    )

    assert result.status == "ok"
    assert result.events == []


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
        db_session.rollback()


def test_load_seen_notices_reads_the_stored_hash(session: Session) -> None:
    source = Source(
        kind=SourceKind.COLLEGE,
        name="college",
        config={},
        enabled=True,
        health_status=HealthStatus.HEALTHY,
        consecutive_failures=0,
    )
    session.add(source)
    session.flush()
    notice = ExtractedNotice(
        actionable=True,
        category=NoticeCategory.COLLEGE,
        title="Workshop on October 15",
    )
    from radar.collectors.college.extract import to_raw_event

    raw = to_raw_event(
        notice,
        source_name="college",
        url=WORKSHOP_URL,
        notice_hash="abc",
    )
    assert raw is not None
    ingest(session, source, [raw])

    assert load_seen_notices(session, source.id) == {(WORKSHOP_URL, "abc")}
