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
