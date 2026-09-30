import asyncio
from datetime import date

import pytest

from analysis import filing_calendar

from analysis.filing_calendar import (
    _is_full_report,
    _period_from_title,
    latest_available_filing_by_period,
)


def test_period_from_cninfo_report_titles():
    assert _period_from_title("2024年年度报告", "annual") == "2024-12-31"
    assert _period_from_title("2024年半年度报告", "semiannual") == "2024-06-30"
    assert _period_from_title("2024年第一季度报告", "q1") == "2024-03-31"
    assert _period_from_title("2024年第三季度报告", "q3") == "2024-09-30"


def test_full_report_filter_rejects_summary():
    assert _is_full_report("2024年年度报告", "annual")
    assert not _is_full_report("2024年年度报告摘要", "annual")


def test_latest_available_filing_uses_latest_version_known_by_as_of():
    calendar = [
        {
            "period": "2024-12-31",
            "published_at": "2025-03-20",
            "title": "2024年年度报告",
            "url": "original",
        },
        {
            "period": "2024-12-31",
            "published_at": "2025-04-15",
            "title": "2024年年度报告（修订版）",
            "url": "revised",
        },
        {
            "period": "2025-03-31",
            "published_at": "2025-04-25",
            "title": "2025年第一季度报告",
            "url": "q1",
        },
    ]

    before_revision = latest_available_filing_by_period(
        calendar, date(2025, 4, 1)
    )
    assert before_revision["2024-12-31"]["url"] == "original"
    assert "2025-03-31" not in before_revision

    after_revision = latest_available_filing_by_period(
        calendar, date(2025, 4, 30)
    )
    assert after_revision["2024-12-31"]["url"] == "revised"
    assert after_revision["2025-03-31"]["url"] == "q1"



@pytest.mark.asyncio
async def test_calendar_concurrent_requests_share_one_inflight_fetch(monkeypatch):
    filing_calendar._calendar_cache.clear()
    filing_calendar._calendar_inflight.clear()
    calls = 0

    async def fake_fetch(stock_code, *, as_of=None, lookback_years=6):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return [
            {
                "period": "2024-12-31",
                "published_at": "2025-03-20",
                "url": "x",
            }
        ]

    monkeypatch.setattr(
        filing_calendar,
        "_fetch_financial_filing_calendar_uncached",
        fake_fetch,
    )
    cutoff = date(2025, 4, 1)
    a, b = await asyncio.gather(
        filing_calendar.get_financial_filing_calendar("600000", as_of=cutoff),
        filing_calendar.get_financial_filing_calendar("600000", as_of=cutoff),
    )

    assert calls == 1
    assert a == b
    assert a is not b
