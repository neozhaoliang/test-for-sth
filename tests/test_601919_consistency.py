"""Regression against the real report which exposed the three source conflicts."""
import json
from datetime import date
from pathlib import Path

from analysis.financial_consistency import coherent_yoy_pct
from analysis.management_capital import build_management_capital_record
from analysis.report_validation import validate_report
from analysis.shareholder import reconcile_live_shareholder_trend
from tests.test_report_validation import valid_report


def captured_report():
    fixture = Path(__file__).parent / "fixtures/601919_consistency_2026-10-02.json"
    fields = json.loads(fixture.read_text(encoding="utf-8"))["report_fields"]
    return valid_report().model_copy(update=fields)


def test_original_601919_report_cannot_pass_data_consistency_validation():
    result = validate_report(captured_report())
    codes = {issue.code for issue in result.errors}
    assert not result.ok
    assert {"stale_shareholder_source", "cashflow_yoy_sign_conflict", "dividend_years_conflict"} <= codes


def test_corrected_601919_sources_pass_with_cash_dividend_years():
    report = captured_report()
    report.shareholder_trend = reconcile_live_shareholder_trend(
        report.shareholder_trend, report.fundamentals,
    )
    facts = report.fundamentals["facts"]
    facts["operating_cash_flow_yoy_pct"] = coherent_yoy_pct(
        facts["operating_cash_flow"], facts["operating_cash_flow_previous"],
        facts["operating_cash_flow_yoy_pct"],
    )
    report.management_capital = build_management_capital_record(
        dividend_history=report.dividend_history,
        primary_evidence=report.primary_evidence,
        as_of=date.fromisoformat(report.as_of),
    )
    assert report.shareholder_trend["period"] == "2026-06-30"
    assert report.shareholder_trend["latest_count"] == 430616
    assert facts["operating_cash_flow_yoy_pct"] == -9.49
    for window in ("five_year", "ten_year"):
        row = report.management_capital[window]
        assert row["dividend_years"] == [2026, 2025, 2024, 2023, 2022]
        assert row["dividend_years_count"] == 5
        assert row["cash_dividend_per_10_total"] == 75.6
    assert validate_report(report).ok


def test_historical_report_does_not_compare_with_live_holder_source():
    report = valid_report()
    report.research_mode = "historical"
    report.as_of = "2024-06-30"
    report.evidence = []
    report.shareholder_trend = {"period": "2024-03-31"}
    report.fundamentals = {"facts": {"holder_count_period": "2026-06-30"}}
    assert "stale_shareholder_source" not in {x.code for x in validate_report(report).errors}
