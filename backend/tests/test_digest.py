from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from radar.models import Base, Event, EventCategory, UserEventState, UserState
from radar.pipeline.digest import build_digest_message, send_digest
from radar.pipeline.scoring import build_feed

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
SOON = datetime(2026, 10, 11, 20, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 20, tzinfo=UTC)


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


def _event(
    session: Session,
    *,
    title: str,
    category: EventCategory,
    deadline: datetime | None,
) -> Event:
    event = Event(
        source_id=_source(session).id,
        external_id=f"ext-{title}",
        fingerprint=f"fp-{title}",
        category=category,
        title=title,
        url=f"https://example.org/{title}",
        registration_deadline=deadline,
        lifecycle="upcoming",
        first_seen_at=NOW,
        last_seen_at=NOW,
        content_hash=f"hash-{title}",
    )
    session.add(event)
    session.flush()
    return event


def _source(session: Session) -> Any:
    from radar.models import HealthStatus, Source, SourceKind

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


def _ignore(session: Session, event: Event) -> None:
    session.add(
        UserEventState(
            user_id="local",
            event_id=event.id,
            state=UserState.IGNORED,
            relevance=0,
            urgency="none",
        )
    )
    session.flush()


def test_digest_lists_deadlines_new_and_college(session: Session) -> None:
    _event(
        session, title="Cloud Workshop", category=EventCategory.COLLEGE, deadline=SOON
    )
    _event(
        session,
        title="ML Hackathon",
        category=EventCategory.HACKATHON,
        deadline=LATER,
    )
    ignored = _event(
        session, title="Ignored Thing", category=EventCategory.COLLEGE, deadline=SOON
    )
    _ignore(session, ignored)
    session.flush()

    message = build_digest_message(session, NOW)

    assert message.startswith("Radar digest — Sun, 11 Oct")
    assert "Deadlines soon:" in message
    assert "• Cloud Workshop — Oct 12, 1:30 AM (critical)" in message
    assert "New opportunities:" in message
    assert "• ML Hackathon" in message
    assert "Ignored Thing" not in message


def test_digest_without_events_says_so(session: Session) -> None:
    message = build_digest_message(session, NOW)

    assert "Nothing needs your attention today." in message


def test_send_digest_needs_a_url(session: Session) -> None:
    assert send_digest(session, now=NOW) is False


def test_send_digest_posts_to_the_webhook(session: Session) -> None:
    _event(
        session, title="Cloud Workshop", category=EventCategory.COLLEGE, deadline=SOON
    )
    sent: list[tuple[str, str]] = []

    def poster(message: str, url: str) -> None:
        sent.append((message, url))

    sent_ok = send_digest(
        session, url="https://hooks.example/x", now=NOW, poster=poster
    )

    assert sent_ok is True
    assert len(sent) == 1
    message, url = sent[0]
    assert url == "https://hooks.example/x"
    assert "Cloud Workshop" in message


def test_feed_sections_feed_the_digest(session: Session) -> None:
    _event(
        session, title="Cloud Workshop", category=EventCategory.COLLEGE, deadline=SOON
    )

    feed = build_feed(session, "local", NOW)

    assert [item.title for item in feed.college] == ["Cloud Workshop"]
