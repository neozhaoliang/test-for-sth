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


def test_historical_request_requires_past_as_of_and_is_currently_blocked():
    req = ResearchRequest(
        stock_code="600000",
        mode="historical",
        as_of=date(2024, 6, 30),
    )
    readiness = historical_readiness()

    assert not readiness.ready
    assert "valuation_history" in readiness.safe_sources
    assert "primary_evidence" in readiness.safe_sources
    assert "fundamentals" in readiness.blocking_sources
    assert "xueqiu_live" in readiness.blocking_sources

    with pytest.raises(NotImplementedError):
        assert_request_supported(req)
