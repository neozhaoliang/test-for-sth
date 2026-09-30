from datetime import date

import pandas as pd
import pytest

from analysis import realtime_price
from backtest.price_source import _akshare_symbol


@pytest.mark.asyncio
async def test_historical_quote_drops_future_rows_and_uses_previous_trading_day(monkeypatch):
    async def fake_prices(code, start, end=None):
        assert end == date(2024, 6, 30)
        return pd.DataFrame(
            {
                "date": ["2024-06-27", "2024-06-28", "2025-01-02"],
                "close": [10.0, 11.0, 30.0],
            }
        )

    monkeypatch.setattr(realtime_price, "get_price_history", fake_prices)

    out = await realtime_price.get_historical_quote(
        "SH600000",
        date(2024, 6, 30),
    )

    assert out is not None
    assert out["date"] == "2024-06-28"
    assert out["latest_price"] == 11.0
    assert out["change_pct"] == 10.0
    assert out["volume"] is None
    assert out["quote_mode"] == "historical_daily_close"



def test_historical_price_source_normalizes_bare_a_share_codes():
    assert _akshare_symbol("SH600519") == "sh600519"
    assert _akshare_symbol("600519") == "sh600519"
    assert _akshare_symbol("SZ000001") == "sz000001"
    assert _akshare_symbol("000001") == "sz000001"
    assert _akshare_symbol("430047") == "bj430047"
