from datetime import date

import pandas as pd
import pytest

from analysis import shareholder


@pytest.mark.asyncio
async def test_shareholder_count_filters_by_disclosure_date_not_period(monkeypatch):
    detail = pd.DataFrame(
        {
            "股东户数统计截止日": [
                date(2024, 3, 31),
                date(2024, 6, 30),
            ],
            "股东户数公告日期": [
                date(2024, 4, 20),
                date(2024, 7, 15),
            ],
            "股东户数-本次": [10000, 8000],
            "股东户数-上次": [11000, 10000],
            "股东户数-增减": [-1000, -2000],
            "股东户数-增减比例": [-9.09, -20.0],
        }
    )

    monkeypatch.setattr(
        shareholder.ak,
        "stock_zh_a_gdhs_detail_em",
        lambda symbol: detail.copy(),
    )

    out = await shareholder.get_shareholder_count_trend(
        "SH600000",
        as_of=date(2024, 6, 30),
    )

    assert out is not None
    assert out["latest_count"] == 10000
    assert out["period"] == "2024-03-31"
    assert out["available_at"] == "2024-04-20"


@pytest.mark.asyncio
async def test_historical_shareholder_count_fails_closed_if_detail_unavailable(monkeypatch):
    def broken(symbol):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(shareholder.ak, "stock_zh_a_gdhs_detail_em", broken)

    async def should_not_call_latest(*args, **kwargs):
        raise AssertionError("historical mode must not fall back to today's latest snapshot")

    monkeypatch.setattr(shareholder.ak, "stock_zh_a_gdhs", should_not_call_latest)

    out = await shareholder.get_shareholder_count_trend(
        "600000",
        as_of=date(2024, 6, 30),
    )
    assert out is None


@pytest.mark.asyncio
async def test_dividend_history_excludes_future_announcements(monkeypatch):
    df = pd.DataFrame(
        {
            "公告日期": [date(2024, 5, 1), date(2025, 5, 1)],
            "派息": [5.0, 8.0],
            "进度": ["实施", "预案"],
        }
    )
    monkeypatch.setattr(
        shareholder.ak,
        "stock_history_dividend_detail",
        lambda symbol, indicator: df.copy(),
    )

    out = await shareholder.get_dividend_history(
        "600000",
        as_of=date(2024, 12, 31),
    )

    assert len(out) == 1
    assert out[0]["announce_date"] == "2024-05-01"
    assert out[0]["available_at"] == "2024-05-01"


@pytest.mark.asyncio
async def test_buyback_history_excludes_future_latest_announcements(monkeypatch):
    df = pd.DataFrame(
        {
            "股票代码": ["600000", "600000"],
            "最新公告日期": [date(2024, 4, 1), date(2025, 4, 1)],
            "实施进度": ["实施中", "完成"],
            "计划回购金额区间-下限": [100.0, 100.0],
            "计划回购金额区间-上限": [200.0, 200.0],
            "已回购金额": [50.0, 180.0],
        }
    )

    async def fake_df():
        return df.copy()

    monkeypatch.setattr(shareholder, "_get_buyback_df", fake_df)

    out = await shareholder.get_buyback_history(
        "600000",
        as_of=date(2024, 12, 31),
    )

    assert len(out) == 1
    assert out[0]["announce_date"] == "2024-04-01"
    assert out[0]["available_at"] == "2024-04-01"
