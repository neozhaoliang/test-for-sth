from datetime import date, datetime
from zoneinfo import ZoneInfo

from analysis.knowledge_base import (
    KnowledgeEntry,
    _normalize_epoch_seconds,
    filter_entries_as_of,
)


def _ts(y, m, d):
    return int(datetime(y, m, d, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def test_normalize_epoch_seconds_handles_seconds_millis_and_micros():
    sec = 1_700_000_000
    assert _normalize_epoch_seconds(sec) == sec
    assert _normalize_epoch_seconds(sec * 1000) == sec
    assert _normalize_epoch_seconds(sec * 1_000_000) == sec


def test_historical_knowledge_excludes_future_and_unknown_timestamps():
    entries = [
        KnowledgeEntry(
            source="xueqiu_1",
            entry_id="old",
            title="旧观点",
            raw_content="",
            distilled="old",
            timestamp=_ts(2024, 6, 20),
            source_url="",
        ),
        KnowledgeEntry(
            source="bili",
            entry_id="future",
            title="未来观点",
            raw_content="",
            distilled="future",
            timestamp=_ts(2025, 1, 1),
            source_url="",
        ),
        KnowledgeEntry(
            source="bili",
            entry_id="unknown",
            title="未知时间",
            raw_content="",
            distilled="unknown",
            timestamp=0,
            source_url="",
        ),
    ]

    kept = filter_entries_as_of(entries, date(2024, 6, 30))

    assert [x.entry_id for x in kept] == ["old"]


def test_live_knowledge_keeps_unknown_time_entries():
    entry = KnowledgeEntry(
        source="bili",
        entry_id="unknown",
        title="未知时间",
        raw_content="",
        distilled="x",
        timestamp=0,
        source_url="",
    )
    assert filter_entries_as_of([entry], None) == [entry]
