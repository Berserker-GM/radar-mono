"""College notice-board collectors."""

from radar.collectors.college.extract import (
    ExtractedNotice,
    ExtractionError,
    NoticeCategory,
    extract_notice,
    to_raw_event,
)
from radar.collectors.college.site import (
    CollegeSiteCollector,
    NoticeSelectors,
    load_seen_notices,
    notice_content_hash,
)

__all__ = [
    "CollegeSiteCollector",
    "ExtractedNotice",
    "ExtractionError",
    "NoticeCategory",
    "NoticeSelectors",
    "extract_notice",
    "load_seen_notices",
    "notice_content_hash",
    "to_raw_event",
]
