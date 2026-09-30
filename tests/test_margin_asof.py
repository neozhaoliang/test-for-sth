from datetime import date

import pytest

from analysis import margin


@pytest.mark.asyncio
async def test_margin_signal_uses_as_of_as_backward_anchor(monkeypatch):
    seen = {}

    def fake_fetch(exchange, symbol, as_of=None):
        seen["exchange"] = exchange
        seen["symbol"] = symbol
        seen["as_of"] = as_of
        return [
            {"date": "20240624", "balance_yuan": 10_000_000_000},
            {"date": "20240625", "balance_yuan": 10_200_000_000},
            {"date": "20240626", "balance_yuan": 10_300_000_000},
            {"date": "20240627", "balance_yuan": 10_400_000_000},
            {"date": "20240628", "balance_yuan": 10_500_000_000},
            {"date": "20240630", "balance_yuan": 10_600_000_000},
        ]

    monkeypatch.setattr(margin, "_fetch_series", fake_fetch)

    out = await margin.get_margin_signal(
        "SH600000",
        as_of=date(2024, 6, 30),
    )

    assert seen == {
        "exchange": "sse",
        "symbol": "600000",
        "as_of": date(2024, 6, 30),
    }
    assert out is not None
    assert out["latest_date"] == "20240630"
    assert out["latest_balance_yi"] == 106.0
    assert out["days"] == 6
