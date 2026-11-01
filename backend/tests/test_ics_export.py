from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from radar.api.main import USER_ID, create_app, get_session
from radar.calendar.ics import build_ics, select_export_events
from radar.models import (
    Base,
    Event,
    EventCategory,
    HealthStatus,
    Source,
    SourceKind,
    UserEventState,
    UserState,
)

TOKEN = "test-token"
NOW = datetime.now(UTC)
SOON = NOW + timedelta(hours=3)
PAST = NOW - timedelta(days=10)
FUTURE = NOW + timedelta(days=20)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def client(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("API_TOKEN", TOKEN)
    app = create_app(session_factory)
    app.dependency_overrides[get_session] = _session_override(session_factory)
    with TestClient(app) as test_client:
        yield test_client


def _session_override(factory: sessionmaker[Session]):
    def override() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return override


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as db_session:
        yield db_session
        db_session.rollback()


def _source(session: Session) -> Source:
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
    return source


def _event(
    session: Session,
    *,
    title: str,
    category: EventCategory,
    deadline: datetime | None,
    start: datetime | None = None,
) -> Event:
    event = Event(
        source_id=_source(session).id,
        external_id=f"ext-{title}",
        fingerprint=f"fp-{title}",
        category=category,
        title=title,
        url=f"https://example.org/{title}",
        start_at=start,
        registration_deadline=deadline,
        lifecycle="upcoming",
        first_seen_at=NOW,
        last_seen_at=NOW,
        content_hash=f"hash-{title}",
    )
    session.add(event)
    session.flush()
    return event


def _save(session: Session, event: Event) -> None:
    session.add(
        UserEventState(
            user_id=USER_ID,
            event_id=event.id,
            state=UserState.SAVED,
            relevance=50,
            urgency="later",
        )
    )
    session.flush()


def _ignore(session: Session, event: Event) -> None:
    session.add(
        UserEventState(
            user_id=USER_ID,
            event_id=event.id,
            state=UserState.IGNORED,
            relevance=0,
            urgency="none",
        )
    )
    session.flush()


def test_export_covers_saved_college_and_urgent(session: Session) -> None:
    _event(
        session, title="College Fest", category=EventCategory.COLLEGE, deadline=FUTURE
    )
    saved = _event(
        session,
        title="Saved Hackathon",
        category=EventCategory.HACKATHON,
        deadline=FUTURE,
    )
    urgent = _event(
        session,
        title="Urgent Hackathon",
        category=EventCategory.HACKATHON,
        deadline=SOON,
    )
    ignored = _event(
        session, title="Ignored Fest", category=EventCategory.COLLEGE, deadline=FUTURE
    )
    closed = _event(
        session, title="Over Fest", category=EventCategory.COLLEGE, deadline=PAST
    )
    hidden_contest = _event(
        session,
        title="Unwanted Contest",
        category=EventCategory.CONTEST,
        deadline=None,
        start=FUTURE,
    )
    _save(session, saved)
    _ignore(session, ignored)

    events = select_export_events(session, USER_ID, NOW)

    titles = {event.title for event in events}
    assert titles == {"College Fest", "Saved Hackathon", "Urgent Hackathon"}
    assert closed.title not in titles
    assert hidden_contest.title not in titles
    assert urgent.title in titles


def test_ics_body_carries_uids_and_times(session: Session) -> None:
    college = _event(
        session,
        title="College Fest",
        category=EventCategory.COLLEGE,
        deadline=(NOW + timedelta(days=9)).replace(
            hour=17, minute=0, second=0, microsecond=0
        ),
    )

    body = build_ics([college], NOW)

    assert "BEGIN:VCALENDAR" in body
    assert f"UID:radar-{college.id}@radar.local" in body
    assert "SUMMARY:College Fest" in body
    start_line = next(line for line in body.splitlines() if line.startswith("DTSTART:"))
    end_line = next(line for line in body.splitlines() if line.startswith("DTEND:"))
    assert start_line.endswith("170000Z")
    assert end_line.endswith("173000Z")


def test_feed_ics_endpoint_serves_a_calendar(
    client: TestClient, session: Session
) -> None:
    _event(
        session, title="College Fest", category=EventCategory.COLLEGE, deadline=FUTURE
    )

    response = client.get("/feed.ics", params={"token": TOKEN})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in response.text
    assert "SUMMARY:College Fest" in response.text


def test_feed_ics_endpoint_rejects_bad_tokens(client: TestClient) -> None:
    assert client.get("/feed.ics", params={"token": "wrong"}).status_code == 401
    assert client.get("/feed.ics").status_code == 422


def test_feed_ics_endpoint_is_empty_without_token_config(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("API_TOKEN", raising=False)
    app = create_app(session_factory)
    app.dependency_overrides[get_session] = _session_override(session_factory)
    with TestClient(app) as bare_client:
        response = bare_client.get("/feed.ics", params={"token": "anything"})

    assert response.status_code == 401
