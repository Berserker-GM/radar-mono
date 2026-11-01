"""Fixture-driven tests for the deterministic notice extractor."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from radar.collectors.college.extract import (
    ExtractedNotice,
    ExtractionError,
    NoticeCategory,
    extract_notice,
    normalize_text,
    to_raw_event,
)
from radar.models.enums import EventCategory, Lifecycle

TODAY = date(2026, 10, 11)
URL = "https://college.example/notices/workshop"
_KOLKATA = timezone(timedelta(hours=5, minutes=30))
_FIXTURES = Path(__file__).parent / "fixtures" / "college"
_COMPARED = (
    "actionable",
    "registration_opens",
    "date",
    "start",
    "end",
    "deadline",
    "deadline_extended",
    "venue",
    "link",
)


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text("utf-8")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.hour == 0 and value.minute == 0:
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%dT%H:%M")


def _fixture_paths() -> list[Path]:
    return sorted(_FIXTURES.glob("notice*.txt"))


def _extracted(path: Path) -> dict[str, object]:
    notice = extract_notice(path.read_text("utf-8"), today=TODAY)
    return {
        "actionable": notice.actionable,
        "registration_opens": _iso(notice.registration_opens),
        "date": _iso(notice.date),
        "start": _iso(notice.start),
        "end": _iso(notice.end),
        "deadline": _iso(notice.deadline),
        "deadline_extended": _iso(notice.deadline_extended),
        "venue": notice.venue,
        "link": notice.link,
    }


@pytest.mark.parametrize("path", _fixture_paths(), ids=lambda path: path.name)
def test_notice_matches_expected_extraction(path: Path) -> None:
    expected = json.loads(path.with_suffix(".expected.json").read_text("utf-8"))
    got = _extracted(path)

    for field in _COMPARED:
        assert got[field] == expected[field], f"{field}: {got[field]!r}"


def test_every_extracted_field_carries_verbatim_evidence() -> None:
    for path in _fixture_paths():
        text = path.read_text("utf-8")
        notice = extract_notice(text, today=TODAY)
        raw = to_raw_event(notice, source_name="college", url=URL)
        if raw is None:
            continue
        for item in raw.evidence:
            assert normalize_text(item.source_text) in normalize_text(text)


def test_labeled_fields_carry_full_confidence() -> None:
    notice = extract_notice(_load("notice06.txt"), today=TODAY)

    assert notice.confidence["deadline"] == 1.0
    assert notice.confidence["venue"] == 1.0
    assert notice.confidence["date"] == 1.0


def test_extension_sets_the_effective_deadline() -> None:
    notice = extract_notice(_load("notice05.txt"), today=TODAY)
    raw = to_raw_event(
        notice,
        source_name="college",
        url="https://college.example/notices/extension",
    )

    assert raw is not None
    assert raw.registration_deadline == datetime(2026, 10, 10, 23, 59, tzinfo=_KOLKATA)
    assert raw.extra["deadline_changed"] is True
    assert raw.extra["mentions_extension"] is True
    by_field = {item.field: item for item in raw.evidence}
    assert (
        "extended to 10th October, 11:59 PM"
        in by_field["registration_deadline"].source_text
    )


def test_original_deadline_is_kept_when_extended() -> None:
    notice = extract_notice(_load("notice03.txt"), today=TODAY)
    raw = to_raw_event(notice, source_name="college", url=URL)

    assert raw.registration_deadline == datetime(2026, 10, 10, tzinfo=_KOLKATA)
    assert (
        raw.extra["original_deadline"]
        == datetime(2026, 10, 3, 20, 0, tzinfo=_KOLKATA).isoformat()
    )


def test_registration_opens_becomes_an_extra_date() -> None:
    notice = extract_notice(_load("notice06.txt"), today=TODAY)
    raw = to_raw_event(notice, source_name="college", url=URL)

    assert raw.extra["other_dates"] == [
        {
            "type": "registration_opens",
            "label": "Registration opens",
            "at": datetime(2026, 10, 14, tzinfo=_KOLKATA).isoformat(),
            "confidence": 1.0,
        }
    ]
    assert "registration_opens" in {item.field for item in raw.evidence}


def test_in_text_link_is_kept_in_extra() -> None:
    notice = extract_notice(_load("notice01.txt"), today=TODAY)
    raw = to_raw_event(notice, source_name="college", url=URL)

    assert raw.url == URL
    assert raw.extra["link"] == "https://forms.gle/upyz4FsyQuVC7NMt8"


def test_non_actionable_notice_is_not_an_event() -> None:
    notice = extract_notice(_load("notice04.txt"), today=TODAY)

    assert notice.actionable is False
    assert notice.date is None
    assert to_raw_event(notice, source_name="college", url=URL) is None


def test_notice_without_dates_or_link_is_not_actionable() -> None:
    text = (
        "Department meeting minutes\n\n"
        "The department discussed the upcoming semester plan on Tuesday. "
        "Minutes were approved by the committee.\n"
    )

    notice = extract_notice(text, today=TODAY)

    assert notice.actionable is False


def test_mentioned_dates_stay_out_of_the_deadline() -> None:
    text = (
        "Cloud workshop announcement\n\n"
        "The registration link will be shared on the department notice board "
        "on 14th October. Seats are limited to 60.\n"
    )

    notice = extract_notice(text, today=TODAY)

    assert notice.deadline is None
    assert notice.date is None
    assert notice.actionable is False


def test_recruitment_notice_is_classified_as_club() -> None:
    notice = extract_notice(_load("notice03.txt"), today=TODAY)
    raw = to_raw_event(notice, source_name="college", url=URL)

    assert notice.category is NoticeCategory.CLUB
    assert raw.category is EventCategory.CLUB
    assert raw.extra["notice_category"] == "club"


def test_exam_notice_maps_to_other_category() -> None:
    text = (
        "Mid semester examination schedule\n\n"
        "The examination is on 20th October 2026 at 10 AM in the Main Hall.\n"
    )

    notice = extract_notice(text, today=TODAY)

    assert notice.category is NoticeCategory.EXAM
    raw = to_raw_event(notice, source_name="college", url=URL)
    assert raw.category is EventCategory.OTHER
    assert raw.extra["notice_category"] == "exam"


def test_bare_time_line_attaches_to_the_previous_date() -> None:
    text = "Cryptography workshop\nDate: 21st October 2026\nTime: 2:30 PM\n"

    notice = extract_notice(text, today=TODAY)

    assert notice.date == datetime(2026, 10, 21, 14, 30)
    assert notice.confidence["date"] == 1.0


def test_time_after_a_deadline_label_attaches_to_the_deadline() -> None:
    text = "Recruitment\nDeadline : 3rd October\nTime: 8 PM\n"

    notice = extract_notice(text, today=TODAY)

    assert notice.deadline == datetime(2026, 10, 3, 20, 0)
    assert notice.date is None


def test_date_range_sets_start_and_end() -> None:
    text = "AI hackathon\nEvent dates : 10th October - 12th October\n"

    notice = extract_notice(text, today=TODAY)

    assert notice.start == datetime(2026, 10, 10)
    assert notice.end == datetime(2026, 10, 12)
    assert notice.date is None


def test_relative_date_resolves_from_today() -> None:
    text = "Guest lecture\nThe guest lecture is this Friday at 2 PM.\n"

    notice = extract_notice(text, today=TODAY)

    assert notice.date == datetime(2026, 10, 16, 14, 0)


def test_next_month_is_not_a_date() -> None:
    text = (
        "Seminar plan\nThe department is planning a seminar next month. "
        "No dates are final yet.\n"
    )

    notice = extract_notice(text, today=TODAY)

    assert notice.date is None
    assert notice.deadline is None


def test_fallback_venue_comes_from_the_event_sentence() -> None:
    text = (
        "Guest Lecture\n\n"
        'All students are requested to attend the guest lecture on "ML" '
        "this Friday at 2:00 PM in the Seminar Hall.\n"
    )

    notice = extract_notice(text, today=TODAY)

    assert notice.venue == "Seminar Hall"
    assert notice.confidence["venue"] == 0.85


def test_venue_fallback_skips_time_of_day_phrases() -> None:
    text = "Club meetup\nThe club meetup is this Saturday in the morning.\n"

    notice = extract_notice(text, today=TODAY)

    assert notice.venue is None


def test_eligibility_line_is_captured() -> None:
    notice = extract_notice(_load("notice01.txt"), today=TODAY)

    assert notice.eligibility is not None
    assert notice.eligibility.startswith("Open to all")


def test_extract_failure_is_wrapped() -> None:
    class Boom:
        def strip(self) -> str:
            raise RuntimeError("boom")

    with pytest.raises(ExtractionError):
        extract_notice(Boom())  # type: ignore[arg-type]


def test_unsupported_category_falls_back_to_college() -> None:
    notice = ExtractedNotice(actionable=True, title="Something")
    raw = to_raw_event(notice, source_name="college", url=URL)

    assert raw is not None
    assert raw.category is EventCategory.COLLEGE
    assert raw.lifecycle is Lifecycle.UNKNOWN
    assert raw.external_id == URL
    assert raw.extra["notice_category"] == "college"
