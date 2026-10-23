"""Tests for ETF FUND cash distribution / officially distributable profit.

No live HTTP or fund-service requests; inputs model published 513530 data.
"""
from datetime import date

import pandas as pd
import pytest

from analysis import etf_distribution


def test_cash_dividend_from_sina_cumulative_513530():
    dates = [
        "2025-06-16", "2025-07-14", "2025-08-18", "2025-09-15",
        "2025-10-20", "2025-11-17", "2025-12-15", "2026-01-21",
        "2026-02-26", "2026-03-23", "2026-04-20", "2026-05-18",
        "2026-06-15", "2026-07-20", "2026-08-17", "2026-09-14",
        "2026-10-19",
    ]
    payments = [.02, .02] + [.01] * 15
    cumulative = []
    total = 0
    for amount in payments:
        total += amount
        cumulative.append(round(total, 6))
    result = etf_distribution._cash_distributions_from_cumulative(
        pd.DataFrame({"日期": dates, "累计分红": cumulative}),
        date(2026, 10, 23),
    )
    assert result["dividend_ttm_per_share"] == pytest.approx(.12)
    assert result["latest_dividend_per_share"] == pytest.approx(.01)
    assert result["latest_dividend_date"] == "2026-10-19"


def test_official_distribution_profit_needs_explicit_date_and_total():
    snippet = (
        "基金主代码 513530\n"
        "收益分配基准日 2026 年 9 月 30 日\n"
        "基准日基金可供分配利润（单位：人民币元）\n"
        "1,057,954,195.22\n"
        "本次分红方案（单位：元/10份基金份额）0.1000\n"
    )
    result = etf_distribution._parse_official_distribution(snippet)
    assert result["distributable_profit_yuan"] == 1057954195.22
    assert result["distributable_profit_as_of"] == "2026-09-30"
    assert etf_distribution._parse_official_distribution(
        "可供分配利润 1,057,954,195.22"
    ) == {}


@pytest.mark.asyncio
async def test_trailing_cash_yield_uses_exchange_price_not_index(monkeypatch):
    etf_distribution._CACHE.clear()
    monkeypatch.setattr(etf_distribution, "_fetch_source_data", lambda *_: {
        "dividend_ttm_per_share": .12,
        "latest_dividend_per_share": .01,
        "distributable_profit_yuan": 1057954195.22,
        "distributable_profit_as_of": "2026-09-30",
    })
    result = await etf_distribution.collect_etf_distributions(
        "513530",
        {"latest_price": 1.491, "quote_time": "2026-10-23"},
        date(2026, 10, 23),
    )
    assert result["dividend_yield_ttm_pct"] == pytest.approx(8.048)
    assert result["distributable_profit_yuan"] == 1057954195.22
    assert result["dividend_yield_reference_price"] == 1.491
    result_no_price = await etf_distribution.collect_etf_distributions(
        "513530", {}, date(2026, 10, 23),
    )
    assert "dividend_yield_ttm_pct" not in result_no_price


def test_cumulative_decreases_never_generate_fake_payments():
    df = pd.DataFrame({
        "日期": ["2026-01-15", "2026-02-16"],
        "累计分红": [.15, .05],
    })
    assert etf_distribution._cash_distributions_from_cumulative(
        df, date(2026, 10, 23)
    ) == {}
