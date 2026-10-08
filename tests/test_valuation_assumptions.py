from analysis.valuation_assumptions import estimate_valuation_scenarios

def history():
    return {"periods": [
        {"period":"2022-12-31", "roe_pct":14},
        {"period":"2023-06-30", "roe_pct":80}, # must not annualize
        {"period":"2023-12-31", "roe_pct":12},
        {"period":"2024-12-31", "roe_pct":16},
        {"period":"2025-12-31", "roe_pct":11},
        {"period":"2026-12-31", "roe_pct":99}, # future leakage
    ]}

def test_only_complete_known_years():
    result = estimate_valuation_scenarios(
        profitability_trend=history(), as_of="2026-10-08",
        payout_pct=50, required_return_pct=11,
    )
    assert result["status"] == "estimated"
    assert result["observation_years"] == [2022,2023,2024,2025]
    assert result["scenarios"][1]["sustainable_roe_pct"] == 13
    assert all(0 <= row["retention_conversion_pct"] <= 100 for row in result["scenarios"])

def test_no_halfyear_roe_annualization():
    result = estimate_valuation_scenarios(
        profitability_trend={"periods":history()["periods"][:3]},
        as_of="2026-10-08", payout_pct=50, required_return_pct=11,
    )
    assert result["status"] == "insufficient_history"
    assert result["scenarios"] == []

def test_no_unsourced_payout_or_hurdle():
    assert estimate_valuation_scenarios(
        profitability_trend=history(), as_of="2026-10-08",
        required_return_pct=10)["status"] == "missing_payout"
    assert estimate_valuation_scenarios(
        profitability_trend=history(), as_of="2026-10-08",
        payout_pct=50)["status"] == "missing_hurdle"

def test_no_future_roe_when_historical():
    result = estimate_valuation_scenarios(
        profitability_trend=history(), as_of="2024-12-31",
        payout_pct=50, required_return_pct=11)
    assert result["status"] == "insufficient_history"
