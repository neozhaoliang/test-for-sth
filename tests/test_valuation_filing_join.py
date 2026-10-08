from analysis.valuation_payout import collect_declared_fiscal_payout_inputs

def test_fiscal_year_only_from_primary_implementation_title_and_annual_calendar():
    profitability={"periods":[{"period":"2024-12-31","roe_pct":12,"eps_yuan":2}]}
    dividends=[{"announce_date":"2025-06-18","dividend_per_10_shares":10,"progress":"实施"}]
    primary=[{"title":"某公司2024年末期权益分派实施公告","published_at":"2025-06-18 00:00:00","url":"https://example.com/div.pdf"}]
    calendar=[{"report_type":"annual","period":"2024-12-31","published_at":"2025-03-31","url":"https://example.com/annual.pdf"}]
    r=collect_declared_fiscal_payout_inputs(
        profitability_trend=profitability,dividend_history=dividends,
        primary_evidence=primary,stock_code="601717",as_of="2026-10-08",filing_calendar=calendar)
    assert r["annual_eps"][0]["fiscal_year"]==2024
    assert r["fiscal_dividends"][0]["fiscal_year"]==2024
    assert r["fiscal_dividends"][0]["source_url"]=="https://example.com/div.pdf"


def test_unstated_year_or_missing_filing_does_not_guess():
    r=collect_declared_fiscal_payout_inputs(
        profitability_trend={"periods":[{"period":"2024-12-31","eps_yuan":2}]},
        dividend_history=[{"announce_date":"2025-06-18","dividend_per_10_shares":10,"progress":"实施"}],
        primary_evidence=[{"title":"权益分派实施公告","published_at":"2025-06-18","url":"https://example.com/div.pdf"}],
        stock_code="601717",as_of="2026-10-08",filing_calendar=[])
    assert r["annual_eps"]==[]
    assert r["fiscal_dividends"]==[]
