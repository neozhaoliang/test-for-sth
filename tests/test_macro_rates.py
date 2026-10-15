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
    async def fake_us(as_of=None):
        return {
            "fed_target_freshness": {"fresh": False, "age_days": 20},
            "us10y_freshness": {"fresh": True, "age_days": 2},
        }

    async def fake_china(as_of=None):
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


def test_freshness_is_relative_to_historical_reference_date():
    out = macro_rates._freshness(
        date(2024, 6, 20),
        15,
        reference_date=date(2024, 6, 30),
    )
    assert out == {"fresh": True, "age_days": 10}


@pytest.mark.asyncio
async def test_macro_context_forwards_as_of_to_both_sources(monkeypatch):
    seen = []

    async def fake_us(as_of=None):
        seen.append(("us", as_of))
        return None

    async def fake_china(as_of=None):
        seen.append(("china", as_of))
        return None

    monkeypatch.setattr(macro_rates, "_fetch_us_rates", fake_us)
    monkeypatch.setattr(macro_rates, "_fetch_china_lpr", fake_china)

    result = await macro_rates.get_macro_rate_context(as_of=date(2024, 6, 30))

    assert result is None
    assert seen == [
        ("us", date(2024, 6, 30)),
        ("china", date(2024, 6, 30)),
    ]


def test_policy_decisions_handle_asof_direction_and_staleness_without_inventing_moves():
    import pandas as pd

    values = pd.DataFrame({
        "日期": ["2026-02-02", "2026-09-15", "2026-10-01", "2026-12-18"],
        "今值": [1.0, 1.0, 1.25, 3.0],
    })
    result = macro_rates._policy_decision_table(
        values, as_of=date(2026, 10, 15), bank="日本央行",
    )
    assert result["policy_rate_pct"] == 1.25
    assert result["decision_as_of"] == "2026-10-01"
    assert result["last_decision_change_pp"] == 0.25
    assert result["change_180d_pp"] == 0.25
    assert result["freshness"]["fresh"] is True

    stale = macro_rates._policy_decision_table(
        values, as_of=date(2026, 8, 1), bank="日本央行",
    )
    assert stale["policy_rate_pct"] == 1.0
    assert stale["freshness"]["fresh"] is False
    assert stale["last_decision_change_pp"] is None


@pytest.mark.asyncio
async def test_international_decision_fetch_partial_failure_is_not_false_tightening(monkeypatch):
    import pandas as pd
    import akshare

    def japan():
        return pd.DataFrame({"日期": ["2026-09-01"], "今值": [1.25]})

    def ecb():
        raise TimeoutError("network unreachable")

    monkeypatch.setattr(akshare, "macro_bank_japan_interest_rate", japan, raising=False)
    monkeypatch.setattr(akshare, "macro_bank_euro_interest_rate", ecb, raising=False)
    observed = await macro_rates.get_international_policy_rates(
        as_of=date(2026, 10, 15),
    )
    assert observed["japan"]["policy_rate_pct"] == 1.25
    assert observed["euro"] is None
    assert observed["japan"]["last_decision_change_pp"] is None
