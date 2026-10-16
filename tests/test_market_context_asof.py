from datetime import date

import pandas as pd
import pytest

from analysis import market_context


@pytest.mark.asyncio
async def test_market_context_drops_future_stock_and_index_rows(monkeypatch):
    idx = pd.to_datetime(["2023-12-29", "2024-06-28", "2025-01-02"])
    index_series = {
        "沪深300": pd.Series([100.0, 110.0, 180.0], index=idx),
    }

    async def fake_indices():
        return index_series

    async def fake_prices(code, start, end=None):
        assert end == date(2024, 6, 30)
        return pd.DataFrame(
            {
                "date": ["2023-12-29", "2024-06-28", "2025-01-02"],
                "close": [10.0, 12.0, 30.0],
            }
        )

    async def fake_sectors(*, as_of=None):
        assert as_of == date(2024, 6, 30)
        sector_dates = pd.bdate_range("2024-05-13", periods=43)
        return {
            name: pd.Series([100.0 + i for i in range(43)], index=sector_dates)
            for name in ("有色金属", "化学制药", "医疗器械",
                         "半导体", "软件开发", "银行", "电力行业")
        }

    monkeypatch.setattr(market_context, "_get_sector_series", fake_sectors)
    monkeypatch.setattr(market_context, "_get_index_series", fake_indices)
    monkeypatch.setattr(market_context, "get_price_history", fake_prices)

    out = await market_context.get_market_context(
        "SH600000",
        as_of=date(2024, 6, 30),
    )

    assert out is not None
    assert out["stock"]["latest_date"] == "2024-06-28"
    assert out["stock"]["latest"] == 12.0
    assert out["stock"]["hist_high"] == 12.0
    assert out["indices"][0]["latest_date"] == "2024-06-28"
    assert out["indices"][0]["latest"] == 110.0

    assert out["sector_rotation"]["status"] == "observed"
    assert out["sector_rotation"]["coverage"] == 7
    assert out["sector_rotation"]["as_of"] == "2024-06-28"
    assert all(r["d20_pct"] is not None for r in out["sector_rotation"]["sectors"])


@pytest.mark.asyncio
async def test_sector_api_outage_keeps_all_five_valid_index_benchmarks(monkeypatch):
    dates=pd.bdate_range(end="2026-10-14",periods=70)
    async def no_boards(*,as_of=None):
        return {}
    async def five_indices():
        return {
            name:pd.Series([100.0+step*(i+1) for i in range(70)],index=dates)
            for name,step in (
                ("上证指数",-.08),("沪深300",-.06),("上证红利",.08),
                ("科创50",-.23),("创业板指",-.18),
            )
        }
    async def no_stock(*args,**kwargs):
        return pd.DataFrame(columns=["date","close"])
    monkeypatch.setattr(market_context,"_get_sector_series",no_boards)
    monkeypatch.setattr(market_context,"_get_index_series",five_indices)
    monkeypatch.setattr(market_context,"get_price_history",no_stock)
    out=await market_context.get_market_context("SH601899",as_of=date(2026,10,15))
    rotation=out["sector_rotation"]
    assert rotation["status"]=="insufficient_data"
    assert rotation["evidence_tier"]=="indices_only"
    assert rotation["coverage"]==0
    assert len(rotation["benchmarks"])==5
    assert {x["name"] for x in rotation["benchmarks"]}=={
        "上证指数","沪深300","上证红利","科创50","创业板指",
    }
    assert all(x["d20_pct"] is not None for x in rotation["benchmarks"])


@pytest.mark.asyncio
async def test_two_industry_boards_still_visible_and_not_called_full_market(monkeypatch):
    dates=pd.bdate_range(end="2026-10-14",periods=70)
    async def two_boards(*,as_of=None):
        return {
            "有色金属":pd.Series([100+i for i in range(70)],index=dates),
            "化学制药":pd.Series([100-i/3 for i in range(70)],index=dates),
        }
    async def few_indices():
        return {"沪深300":pd.Series([100-i/5 for i in range(70)],index=dates)}
    async def no_stock(*args,**kwargs):
        return pd.DataFrame(columns=["date","close"])
    monkeypatch.setattr(market_context,"_get_sector_series",two_boards)
    monkeypatch.setattr(market_context,"_get_index_series",few_indices)
    monkeypatch.setattr(market_context,"get_price_history",no_stock)
    out=await market_context.get_market_context("SH601899",as_of=date(2026,10,15))
    rotation=out["sector_rotation"]
    assert rotation["status"]=="partial"
    assert rotation["evidence_tier"]=="partial_sector_with_indices"
    assert len(rotation["sectors"])==2
    assert len(rotation["benchmarks"])==1


@pytest.mark.asyncio
async def test_stale_index_is_not_passed_off_as_contemporaneous(monkeypatch):
    old=pd.bdate_range(end="2026-09-20",periods=30)
    async def no_boards(*,as_of=None):return {}
    async def stale_index():
        return {"上证红利":pd.Series([100+i for i in range(30)],index=old)}
    async def no_stock(*args,**kwargs):
        return pd.DataFrame(columns=["date","close"])
    monkeypatch.setattr(market_context,"_get_sector_series",no_boards)
    monkeypatch.setattr(market_context,"_get_index_series",stale_index)
    monkeypatch.setattr(market_context,"get_price_history",no_stock)
    out=await market_context.get_market_context("SH601899",as_of=date(2026,10,15))
    assert out["sector_rotation"]["benchmarks"]==[]
    assert out["sector_rotation"]["evidence_tier"]=="none"
