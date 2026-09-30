from datetime import date

from analysis.management_capital import build_management_capital_record


def test_management_capital_builds_5y_10y_record_without_scalar_score():
    dividends = [
        {"announce_date": "2026-05-10", "dividend_per_10_shares": 5.0},
        {"announce_date": "2025-05-10", "dividend_per_10_shares": 4.0},
        {"announce_date": "2024-05-10", "dividend_per_10_shares": 3.0},
        {"announce_date": "2022-05-10", "dividend_per_10_shares": 2.0},
        {"announce_date": "2019-05-10", "dividend_per_10_shares": 1.0},
    ]
    buybacks = [
        {"announce_date": "2025-08-01", "actual_amount": 300_000_000},
        {"announce_date": "2023-08-01", "actual_amount": 100_000_000},
    ]
    refinancing = [
        {"announce_date": "2024-03-01", "kind": "增发"},
        {"announce_date": "2018-03-01", "kind": "可转债"},
    ]
    primary = [
        {
            "published_at": "2026-06-01",
            "category": "股权变动",
            "title": "关于部分董事、高级管理人员减持股份计划的公告",
            "url": "https://example.com/reduce",
        },
        {
            "published_at": "2025-06-01",
            "category": "公司治理",
            "title": "关于收到监管警示函的公告",
            "url": "https://example.com/warn",
        },
        {
            "published_at": "2024-06-01",
            "category": "增发",
            "title": "向特定对象发行股票预案",
            "url": "https://example.com/refi",
        },
    ]
    profitability = {
        "periods": [
            {"period": "2025-03-31", "roe_pct": 4.0, "net_profit_growth_pct": 5.0, "net_margin_pct": 8.0},
            {"period": "2025-06-30", "roe_pct": 5.0, "net_profit_growth_pct": -3.0, "net_margin_pct": 7.5},
            {"period": "2025-09-30", "roe_pct": 6.0, "net_profit_growth_pct": 8.0, "net_margin_pct": 9.0},
            {"period": "2025-12-31", "roe_pct": 7.0, "net_profit_growth_pct": 10.0, "net_margin_pct": 9.5},
        ]
    }

    record = build_management_capital_record(
        dividend_history=dividends,
        buyback_history=buybacks,
        refinancing_history=refinancing,
        primary_evidence=primary,
        executive_profile={
            "chairman": "张三",
            "joined_year": 2016,
            "chairman_salary_wan": 120.0,
            "chairman_shares": "100万股",
        },
        profitability_trend=profitability,
        as_of=date(2026, 9, 29),
    )

    assert "score" not in record
    assert record["source_tier"] == "A"
    assert record["alignment"]["tenure_years"] == 10
    assert record["five_year"]["dividend_years_count"] == 4
    assert record["five_year"]["cash_dividend_per_10_total"] == 14.0
    assert record["five_year"]["buyback_actual_amount_yuan"] == 400_000_000
    assert record["five_year"]["refinancing_records"] == 1
    assert record["five_year"]["insider_reduction_announcements"] == 1
    assert record["five_year"]["governance_negative_announcements"] == 1
    assert record["execution"]["roe_start_pct"] == 4.0
    assert record["execution"]["roe_latest_pct"] == 7.0
    assert record["execution"]["roe_change_pp"] == 3.0
    assert record["execution"]["net_profit_growth_positive_periods"] == 3
    assert record["five_year"]["primary_refinancing_announcements"] == 1


def test_management_capital_without_primary_is_b_tier_and_notes_limit():
    record = build_management_capital_record(
        dividend_history=[{"announce_date": "2025-05-10", "dividend_per_10_shares": 1.0}],
        primary_evidence=[],
        executive_profile={},
        as_of=date(2026, 9, 29),
    )

    assert record["source_tier"] == "B"
    assert record["five_year"]["dividend_years_count"] == 1
    assert any("巨潮" in note for note in record["notes"])
    assert any("高管" in note for note in record["notes"])


def test_management_capital_counts_implemented_dividend_with_unknown_amount():
    record = build_management_capital_record(
        dividend_history=[
            {
                "announce_date": "2023-07-06",
                "dividend_per_10_shares": None,
                "progress": "implemented",
                "title": "2022年年度A股分红派息实施公告",
            },
            {
                "announce_date": "2024-03-26",
                "dividend_per_10_shares": None,
                "progress": "proposal",
                "title": "2023年度利润分配方案公告",
            },
        ],
        as_of=date(2024, 6, 30),
    )

    row = record["five_year"]
    assert row["dividend_years_count"] == 1
    assert row["dividend_records"] == 1
    assert row["cash_dividend_per_10_total"] is None
    assert row["cash_dividend_amount_records"] == 0
    assert row["cash_dividend_amount_complete"] is False
