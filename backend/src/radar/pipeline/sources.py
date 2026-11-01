"""Collect and ingest one named source.

The CLI imports these functions. This module does not import the CLI.
"""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.collectors.clist import ClistCollector
from radar.collectors.codeforces import CodeforcesCollector
from radar.collectors.college.site import (
    CollegeSiteCollector,
    NoticeSelectors,
    load_seen_notices,
)
from radar.collectors.devfolio import DevfolioCollector
from radar.collectors.devpost import DevpostCollector
from radar.collectors.icsfeed import IcsCollector
from radar.collectors.icsfeed import feeds_from_config as ics_from_config
from radar.collectors.leetcode import LeetCodeCollector
from radar.collectors.rss import RssCollector
from radar.collectors.rss import feeds_from_config as rss_from_config
from radar.collectors.unstop import UnstopCollector
from radar.models import HealthStatus, Source, SourceKind, SourceResult
from radar.pipeline.ingest import SourceRunResult, ingest_run

_CODEFORCES = "codeforces"
_CLIST = "clist"
_DEVPOST = "devpost"
_UNSTOP = "unstop"
_DEVFOLIO = "devfolio"
_LEETCODE = "leetcode"
_COLLEGE = "college"
_RSS = "rss"
_ICS = "ics"

_SOURCE_KINDS: dict[str, SourceKind] = {
    _CODEFORCES: SourceKind.API,
    _CLIST: SourceKind.API,
    _DEVPOST: SourceKind.API,
    _UNSTOP: SourceKind.API,
    _DEVFOLIO: SourceKind.API,
    _LEETCODE: SourceKind.API,
    _COLLEGE: SourceKind.COLLEGE,
    _RSS: SourceKind.SCRAPE,
    _ICS: SourceKind.SCRAPE,
}

# Rows for these are auto-created with an empty config. The config-driven
# sources only get a row once they are actually scanned, and report
# "skipped" until their config appears.
API_SOURCE_NAMES: frozenset[str] = frozenset(
    {_CODEFORCES, _CLIST, _DEVPOST, _UNSTOP, _DEVFOLIO, _LEETCODE}
)


async def scan_codeforces(session: Session) -> SourceRunResult:
    collected = await CodeforcesCollector().collect()
    return ingest_run(session, _get_source(session, _CODEFORCES), collected)


async def scan_clist(session: Session) -> SourceRunResult:
    collected = await ClistCollector().collect()
    return ingest_run(session, _get_source(session, _CLIST), collected)


async def scan_devpost(session: Session) -> SourceRunResult:
    collected = await DevpostCollector().collect()
    return ingest_run(session, _get_source(session, _DEVPOST), collected)


async def scan_unstop(session: Session) -> SourceRunResult:
    collected = await UnstopCollector().collect()
    return ingest_run(session, _get_source(session, _UNSTOP), collected)


async def scan_devfolio(session: Session) -> SourceRunResult:
    collected = await DevfolioCollector().collect()
    return ingest_run(session, _get_source(session, _DEVFOLIO), collected)


async def scan_leetcode(session: Session) -> SourceRunResult:
    collected = await LeetCodeCollector().collect()
    return ingest_run(session, _get_source(session, _LEETCODE), collected)


async def scan_college(session: Session) -> SourceRunResult:
    source = _get_source(session, _COLLEGE)
    config = source.config if isinstance(source.config, dict) else {}
    list_url = config.get("list_url")
    selectors = config.get("selectors")
    if (
        not isinstance(list_url, str)
        or not list_url.strip()
        or not isinstance(selectors, dict)
    ):
        return _unconfigured(_COLLEGE, "list_url and selectors {title, link, date}")
    try:
        parsed = NoticeSelectors.model_validate(selectors)
    except ValidationError as exc:
        return SourceRunResult(
            result=SourceResult.MALFORMED,
            error=f"invalid college selectors: {_single_line(exc)}",
        )
    collector = CollegeSiteCollector(
        list_url=list_url,
        selectors=parsed,
        seen=load_seen_notices(session, source.id),
    )
    collected = await collector.collect()
    return ingest_run(session, source, collected)


async def scan_rss(session: Session) -> SourceRunResult:
    source = _get_source(session, _RSS)
    feeds = rss_from_config(source.config)
    if not feeds:
        return _unconfigured(_RSS, 'config {"feeds": [url or {url, category}]}')
    collected = await RssCollector(feeds=feeds).collect()
    return ingest_run(session, source, collected)


async def scan_ics(session: Session) -> SourceRunResult:
    source = _get_source(session, _ICS)
    feeds = ics_from_config(source.config)
    if not feeds:
        return _unconfigured(_ICS, 'config {"calendars": [url]}')
    collected = await IcsCollector(feeds=feeds).collect()
    return ingest_run(session, source, collected)


SCANNERS = {
    _CODEFORCES: scan_codeforces,
    _CLIST: scan_clist,
    _DEVPOST: scan_devpost,
    _UNSTOP: scan_unstop,
    _DEVFOLIO: scan_devfolio,
    _LEETCODE: scan_leetcode,
    _COLLEGE: scan_college,
    _RSS: scan_rss,
    _ICS: scan_ics,
}

BUILTIN_SOURCE_NAMES: tuple[str, ...] = tuple(SCANNERS)


def _unconfigured(name: str, needs: str) -> SourceRunResult:
    """A configured-looking source with no config yet is skipped, not failed."""

    return SourceRunResult(
        result=SourceResult.SKIPPED,
        warning=f"{name} source is not configured; needs {needs}",
    )


def _single_line(exc: ValidationError) -> str:
    return " ".join(str(exc).split())[:200]


def _get_source(session: Session, name: str) -> Source:
    source = session.scalar(select(Source).where(Source.name == name))
    if source is not None:
        return source
    source = Source(
        kind=_SOURCE_KINDS[name],
        name=name,
        config={},
        enabled=True,
        health_status=HealthStatus.HEALTHY,
        consecutive_failures=0,
    )
    session.add(source)
    session.flush()
    return source
