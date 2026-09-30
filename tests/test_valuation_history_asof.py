from datetime import date

import pandas as pd
import pytest

from analysis import valuation_history


@pytest.mark.asyncio
async def test_valuation_history_truncates_future_rows(monkeypatch):
    df = pd.DataFrame(
        {
            "数据日期": [
                date(2023, 12, 29),
                date(2024, 6, 28),
                date(2025, 1, 2),
            ],
            "当日收盘价": [10.0, 12.0, 20.0],
            "PE(TTM)": [8.0, 10.0, 30.0],
            "市净率": [1.0, 1.2, 2.5],
        }
    )

    monkeypatch.setattr(
        valuation_history.ak,
        "stock_value_em",
        lambda symbol: df.copy(),
    )

    out = await valuation_history.get_valuation_history(
        "600000",
        as_of=date(2024, 6, 30),
    )

    assert out is not None
    assert out["as_of"] == "2024-06-28"
    assert out["current_price"] == 12.0
    assert out["current_pe_ttm"] == 10.0
    assert all(row["date"] <= "2024-06-30" for row in out["history_monthly"])
