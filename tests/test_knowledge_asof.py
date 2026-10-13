from datetime import date, datetime
from zoneinfo import ZoneInfo

from analysis import knowledge_base
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


def test_cached_knowledge_loader_uses_only_matching_distill_cache(monkeypatch):
    raw = {
        "source": "test",
        "entry_id": "1",
        "title": "历史观点",
        "content": "raw historical content",
        "timestamp": _ts(2024, 6, 20),
        "source_url": "https://example.test/1",
    }
    monkeypatch.setattr(knowledge_base, "_build_sources", lambda: [object()])
    monkeypatch.setattr(
        knowledge_base,
        "_load_raw_entries",
        lambda _source: [raw],
    )
    monkeypatch.setattr(
        knowledge_base,
        "_read_distill_cache",
        lambda: {
            "test:1": {
                "cache_key": "test:1",
                "content_hash": knowledge_base._content_hash(raw["content"]),
                "distilled": "已缓存提炼",
            }
        },
    )

    rows = knowledge_base.load_cached_entries()
    assert len(rows) == 1
    assert rows[0].distilled == "已缓存提炼"

    monkeypatch.setattr(
        knowledge_base,
        "_read_distill_cache",
        lambda: {
            "test:1": {
                "cache_key": "test:1",
                "content_hash": "stale-hash",
                "distilled": "旧提炼",
            }
        },
    )
    assert knowledge_base.load_cached_entries() == []


import pytest


@pytest.mark.asyncio
async def test_empty_distillation_is_not_written_to_cache(monkeypatch):
    raw = {
        "source": "test", "entry_id": "lost", "title": "投资观点",
        "content": "核对现金流和ROE", "timestamp": _ts(2026, 10, 12),
    }
    appended = []

    async def empty(_raw):
        return ""

    monkeypatch.setattr(knowledge_base, "_distill_one", empty)
    monkeypatch.setattr(knowledge_base, "_append_distill_cache", appended.append)
    result = await knowledge_base._distill_missing([raw], {})
    assert result == {}
    assert appended == []


@pytest.mark.asyncio
async def test_old_empty_cache_is_reprocessed_after_model_budget_fix(monkeypatch):
    raw = {
        "source": "test", "entry_id": "lost", "title": "投资观点",
        "content": "核对现金流和ROE", "timestamp": _ts(2026, 10, 12),
    }
    old = {
        "test:lost": {
            "cache_key": "test:lost",
            "content_hash": knowledge_base._content_hash(raw["content"]),
            "distilled": "",
        }
    }
    appended = []

    async def complete(_raw):
        return "【机制】用现金流验证净利润。"

    monkeypatch.setattr(knowledge_base, "_distill_one", complete)
    monkeypatch.setattr(knowledge_base, "_append_distill_cache", appended.append)
    result = await knowledge_base._distill_missing([raw], old)
    assert result["test:lost"]["distilled"] == "【机制】用现金流验证净利润。"
    assert len(appended) == 1


@pytest.mark.asyncio
async def test_explicit_no_investment_marker_is_negative_cached(monkeypatch):
    raw = {
        "source": "bili", "entry_id": "offtopic", "title": "生活趣事",
        "content": "这是一段不涉及投资的旅行记录",
        "timestamp": _ts(2026, 10, 13),
    }
    saved = []
    processed = []

    async def no_knowledge(item):
        processed.append(item["entry_id"])
        return knowledge_base._NO_KNOWLEDGE_MARKER

    monkeypatch.setattr(knowledge_base, "_distill_one", no_knowledge)
    monkeypatch.setattr(knowledge_base, "_append_distill_cache", saved.append)
    cache = await knowledge_base._distill_missing([raw], {})
    assert len(saved) == 1
    assert cache["bili:offtopic"]["distilled"] == ""
    assert cache["bili:offtopic"]["skipped_no_relevant_knowledge"] is True
    cache = await knowledge_base._distill_missing([raw], cache)
    assert processed == ["offtopic"]


@pytest.mark.asyncio
async def test_distill_request_uses_16k_default_and_explicit_none_marker(monkeypatch):
    import backtest.llm_client as llm

    calls = []
    async def fake(prompt, max_tokens):
        calls.append((prompt, max_tokens))
        return knowledge_base._NO_KNOWLEDGE_MARKER

    monkeypatch.setattr(llm, "call_text", fake)
    monkeypatch.delenv("BACKTEST_DISTILL_MAX_TOKENS", raising=False)
    out = await knowledge_base._distill_one({
        "entry_id": "no", "title": "旅游",
        "content": "今天的天气十分晴朗",
    })
    assert out == knowledge_base._NO_KNOWLEDGE_MARKER
    assert calls[0][1] == 16384
    assert knowledge_base._NO_KNOWLEDGE_MARKER in calls[0][0]


def test_distill_token_override_is_bounded(monkeypatch):
    monkeypatch.setenv("BACKTEST_DISTILL_MAX_TOKENS", "32768")
    assert knowledge_base._distill_max_tokens() == 32768
    monkeypatch.setenv("BACKTEST_DISTILL_MAX_TOKENS", "not-a-number")
    assert knowledge_base._distill_max_tokens() == 16384
    monkeypatch.setenv("BACKTEST_DISTILL_MAX_TOKENS", "999999")
    assert knowledge_base._distill_max_tokens() == 65536
