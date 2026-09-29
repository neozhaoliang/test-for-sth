from datetime import date

import pytest

import analysis.macro_rates as macro_rates


def test_change_from_days_uses_observation_at_or_before_cutoff():
    rows = [
        {"date": date(2026, 1, 1), "value": 5.0},
        {"date": date(2026, 3, 1), "value": 4.5},
        {"date": date(2026, 6, 30), "value": 4.0},
    ]
    assert macro_rates._change_from_days(rows, 120) == -0.5


@pytest.mark.asyncio
async def test_macro_context_warns_when_upstream_marks_series_stale(monkeypatch):
    async def fake_us():
        return {
            "fed_target_freshness": {"fresh": False, "age_days": 20},
            "us10y_freshness": {"fresh": True, "age_days": 2},
        }

    async def fake_china():
        return {
            "freshness": {"fresh": False, "age_days": 60},
        }

    monkeypatch.setattr(macro_rates, "_fetch_us_rates", fake_us)
    monkeypatch.setattr(macro_rates, "_fetch_china_lpr", fake_china)

    result = await macro_rates.get_macro_rate_context()

    assert result is not None
    assert len(result["warnings"]) == 2
    assert any("联邦基金" in x for x in result["warnings"])
    assert any("LPR" in x for x in result["warnings"])
