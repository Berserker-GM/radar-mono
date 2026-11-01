from datetime import UTC, datetime, timedelta

from radar.models import Event, EventCategory
from radar.pipeline.scoring import WEIGHTS, relevance

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def _hackathon(**overrides: object) -> Event:
    fields: dict[str, object] = {
        "category": EventCategory.HACKATHON,
        "title": "Some Hackathon",
        "tags": ["machine learning"],
        "registration_deadline": NOW + timedelta(days=10),
        "lifecycle": "upcoming",
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "content_hash": "hash",
    }
    fields.update(overrides)
    return Event(**fields)  # type: ignore[arg-type]


def test_synonym_topic_matches_the_expanded_tag() -> None:
    scored = relevance(_hackathon(), {"topics": ["ml"]})

    assert scored.score == WEIGHTS["topic"]
    assert scored.reasons == ["Topic: ml"]


def test_synonym_matches_in_both_directions() -> None:
    event = _hackathon(tags=["ml", "open source"])

    scored = relevance(event, {"topics": ["machine learning"]})

    assert scored.score == WEIGHTS["topic"]
    assert scored.reasons == ["Topic: machine learning"]


def test_unrelated_topics_do_not_match() -> None:
    scored = relevance(_hackathon(), {"topics": ["blockchain"]})

    assert scored.score == 0
    assert scored.reasons == []


def test_token_containment_matches_qualified_tags() -> None:
    event = _hackathon(tags=["machine learning and ai"])

    scored = relevance(event, {"topics": ["machine learning"]})

    assert scored.score == WEIGHTS["topic"]


def test_company_matches_qualified_sponsor_names() -> None:
    event = _hackathon(sponsors=[{"name": "Google India"}], tags=["cloud"])

    scored = relevance(event, {"companies": ["google"]})

    assert scored.score == WEIGHTS["company"]
    assert scored.reasons == ["Company: google"]


def test_short_names_do_not_prefix_match() -> None:
    scored = relevance(_hackathon(tags=["trust building"]), {"topics": ["rust"]})

    assert scored.score == 0
