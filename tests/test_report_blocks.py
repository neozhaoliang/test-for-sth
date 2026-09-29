from analysis.evidence import ResearchQuality
from analysis.report_blocks import (
    _build_a_share_structure_block,
    _build_fundamentals_block,
    _build_research_quality_block,
    _build_valuation_history_block,
)


def test_valuation_block_does_not_leak_fund_or_unlock_data():
    text = _build_valuation_history_block(
        {
            "as_of": "2026-09-29",
            "current_pe_ttm": 18.0,
            "current_pb": 2.0,
            "observations": 1200,
            "pe_percentiles": [{"years": 5, "percentile": 25.0, "median": 22, "min": 10, "max": 50}],
            "pb_percentiles": [{"years": 5, "percentile": 30.0, "median": 2.4, "min": 1.0, "max": 5.0}],
            # These keys used to be accidentally rendered by the valuation block.
            "fund_details": [{"name": "不应出现的基金"}],
            "unlock_supply": {"upcoming_12m": [{"date": "2027-01-01"}]},
        }
    )

    assert "PE(TTM) 分位" in text
    assert "不应出现的基金" not in text
    assert "限售解禁" not in text


def test_a_share_structure_block_contains_disclosed_qoq_and_unlock_supply():
    text = _build_a_share_structure_block(
        {
            "report_period": "2026-06-30",
            "previous_report_period": "2026-03-31",
            "institution_summary": [
                {
                    "type": "基金",
                    "institutions": 12,
                    "latest_float_ratio_pct": 8.5,
                    "float_ratio_change_pct": 1.2,
                }
            ],
            "institution_qoq": [
                {
                    "type": "基金",
                    "float_ratio_change_pp": 1.5,
                    "shares_change_pct": 20.0,
                    "institution_count_change": 2,
                }
            ],
            "fund_qoq": {
                "increased": [
                    {
                        "name": "A基金",
                        "float_ratio_change_pp": 0.4,
                    }
                ],
                "decreased": [],
                "newly_seen": [],
                "exited_top_list": [],
            },
            "fund_details": [{"name": "A基金", "latest_float_ratio_pct": 1.5}],
            "etf_details": [{"name": "某ETF", "latest_float_ratio_pct": 0.5}],
            "unlock_supply": {
                "upcoming_12m": [
                    {
                        "date": "2027-01-15",
                        "unlock_shares": 1000000,
                        "float_market_ratio_pct": 2.5,
                        "type": "定增机构配售股份",
                    }
                ],
                "recent_6m": [],
            },
            "special_holders": {},
            "notes": [],
        }
    )

    assert "2026-03-31" in text
    assert "占流通股变化 +1.5%" in text
    assert "A基金" in text
    assert "某ETF" in text
    assert "2027-01-15" in text
    assert "潜在供给" in text


def test_research_quality_block_marks_stale_data_as_history_only():
    q = ResearchQuality(
        coverage=0.75,
        covered_dimensions=9,
        total_dimensions=12,
        stale_evidence=[
            {
                "category": "a_share_structure",
                "label": "A股机构持股与特殊股东结构",
                "as_of": "2025-12-31",
                "age_days": 272,
                "max_age_days": 190,
            }
        ],
        missing_dimensions=["A股资金结构与市场风格"],
    )
    text = _build_research_quality_block(q)

    assert "只能作历史背景" in text
    assert "禁止当作当前状态" in text
    assert "2025-12-31" in text


def test_fundamentals_block_surfaces_working_capital_pressure():
    text = _build_fundamentals_block(
        {
            "facts": {
                "finance_period": "2026-06-30",
                "revenue": 10_000_000_000,
                "revenue_yoy_pct": 5.0,
                "net_profit": 1_000_000_000,
                "operating_cash_flow": 700_000_000,
                "cash_to_profit_ratio": 0.7,
                "accounts_receivable_yuan": 3_000_000_000,
                "accounts_receivable_yoy_pct": 25.0,
                "receivable_growth_minus_revenue_pp": 20.0,
                "receivable_to_revenue_pct": 30.0,
                "inventory_yuan": 2_000_000_000,
                "inventory_yoy_pct": 18.0,
                "inventory_growth_minus_revenue_pp": 13.0,
                "inventory_to_revenue_pct": 20.0,
            }
        }
    )

    assert "应收账款" in text
    assert "应收增速较营收高 +20.0" in text
    assert "存货" in text
    assert "存货增速较营收高 +13.0" in text
