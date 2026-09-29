from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from analysis.research_context import (
    ResearchMode,
    ResearchRequest,
    assert_request_supported,
    historical_readiness,
)


def test_live_request_defaults_to_today():
    req = ResearchRequest(stock_code="600000")
    assert req.mode == ResearchMode.LIVE
    assert req.as_of == datetime.now(ZoneInfo("Asia/Shanghai")).date()


def test_live_request_rejects_past_as_of():
    with pytest.raises(ValueError):
        ResearchRequest(stock_code="600000", mode="live", as_of=date(2024, 6, 30))


def test_historical_request_requires_past_as_of_and_point_in_time_core_is_ready():
    req = ResearchRequest(
        stock_code="600000",
        mode="historical",
        as_of=date(2024, 6, 30),
    )
    readiness = historical_readiness()

    assert readiness.ready
    assert readiness.blocking_sources == []
    for source in (
        "valuation_history",
        "primary_evidence",
        "realtime_quote",
        "fundamentals",
        "profitability",
        "shareholder_count",
        "dividend_buyback",
        "a_share_structure",
        "market_context",
        "margin",
        "macro_rates",
        "kol_knowledge",
        "candidate_credibility",
    ):
        assert source in readiness.safe_sources

    # Live-only sources are deliberately omitted rather than backfilled with today's data.
    assert "xueqiu_live" not in readiness.safe_sources
    assert "industry_cycle" not in readiness.safe_sources
    assert "policy_news" not in readiness.safe_sources
    assert_request_supported(req)
