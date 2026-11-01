import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import create_engine, select
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from radar.models import Base, Event, Source
from radar.pipeline.sources import (
    BUILTIN_SOURCE_NAMES,
    scan_college,
    scan_ics,
    scan_leetcode,
    scan_rss,
)

_RSS_URL = "https://dept.example/feed.xml"
_LEETCODE_URL = "https://leetcode.com/graphql"
_LIST_URL = "https://college.example/notices/"
_DETAIL_URL = "https://college.example/notices/workshop"

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Dept Notices</title>
<item><title>Cloud Workshop</title><link>https://dept.example/notices/cloud</link></item>
</channel></rss>
"""

_LEETCODE = {
    "data": {
        "topTwoContests": [
            {
                "title": "Weekly Contest 500",
                "titleSlug": "weekly-contest-500",
                "startTime": 1798000000,
                "duration": 5400,
            }
        ]
    }
}

_COLLEGE_LIST = '<a class="notice-title" href="/notices/workshop">Workshop</a>'
_COLLEGE_DETAIL = (
    "<html><body><p>Workshop on October 15 in the main auditorium.</p></body></html>"
)


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


def _set_config(session: Session, name: str, config: dict[str, Any]) -> None:
    source = session.scalar(select(Source).where(Source.name == name))
    assert source is not None
    source.config = config
    session.flush()


def test_builtin_names_cover_every_scanner() -> None:
    assert set(BUILTIN_SOURCE_NAMES) == {
        "codeforces",
        "clist",
        "devpost",
        "unstop",
        "devfolio",
        "leetcode",
        "college",
        "rss",
        "ics",
    }


def test_unconfigured_college_is_skipped(session: Session) -> None:
    outcome = asyncio.run(scan_college(session))
    session.commit()

    assert outcome.result.value == "skipped"
    assert outcome.warning is not None
    assert "list_url" in outcome.warning


def test_unconfigured_rss_and_ics_are_skipped(session: Session) -> None:
    rss = asyncio.run(scan_rss(session))
    ics = asyncio.run(scan_ics(session))
    session.commit()

    assert rss.result.value == "skipped"
    assert ics.result.value == "skipped"


def test_configured_rss_ingests_entries(session: Session) -> None:
    asyncio.run(scan_rss(session))
    _set_config(session, "rss", {"feeds": [_RSS_URL]})

    async def run() -> Any:
        async with respx.mock:
            respx.get(_RSS_URL).mock(return_value=httpx.Response(200, text=_RSS))
            return await scan_rss(session)

    outcome = asyncio.run(run())
    session.commit()

    assert outcome.result.value == "ok_new"
    titles = session.scalars(select(Event.title)).all()
    assert titles == ["Cloud Workshop"]


def test_configured_college_ingests_notices(session: Session) -> None:
    asyncio.run(scan_college(session))
    _set_config(
        session,
        "college",
        {
            "list_url": _LIST_URL,
            "selectors": {
                "title": "a.notice-title",
                "link": "a.notice-title",
                "date": "time",
            },
        },
    )

    async def run() -> Any:
        async with respx.mock:
            respx.get(_LIST_URL).mock(
                return_value=httpx.Response(200, text=_COLLEGE_LIST)
            )
            respx.get(_DETAIL_URL).mock(
                return_value=httpx.Response(200, text=_COLLEGE_DETAIL)
            )
            return await scan_college(session)

    outcome = asyncio.run(run())
    session.commit()

    assert outcome.result.value == "ok_new"
    events = session.scalars(select(Event)).all()
    assert len(events) == 1
    assert events[0].category.value == "college"
    assert events[0].start_at is not None


def test_leetcode_source_ingests_contests(session: Session) -> None:
    async def run() -> Any:
        async with respx.mock:
            respx.post(_LEETCODE_URL).mock(
                return_value=httpx.Response(200, json=_LEETCODE)
            )
            return await scan_leetcode(session)

    outcome = asyncio.run(run())
    session.commit()

    assert outcome.result.value == "ok_new"
    events = session.scalars(select(Event)).all()
    assert [event.external_id for event in events] == ["weekly-contest-500"]
    assert outcome.error is None
