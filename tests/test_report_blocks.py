from analysis.evidence import ResearchQuality
from analysis.report_blocks import (
    _build_a_share_structure_block,
    _build_dividend_chart,
    _build_fundamentals_block,
    _build_management_capital_block,
    _build_shareholder_block,
    _build_research_quality_block,
    _build_time_contract_block,
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



def test_historical_time_contract_forbids_model_memory_and_hindsight():
    class Inputs:
        research_mode = "historical"
        as_of = "2024-06-30"

    text = _build_time_contract_block(Inputs())

    assert "2024-06-30" in text
    assert "严禁使用模型记忆" in text
    assert "截止日之后" in text
    assert "后来证明" in text


def test_management_capital_block_does_not_render_unknown_dividend_amount_as_zero():
    text = _build_management_capital_block(
        {
            "alignment": {},
            "execution": {},
            "five_year": {
                "window_years": 5,
                "dividend_years_count": 4,
                "dividend_records": 4,
                "cash_dividend_per_10_total": None,
                "cash_dividend_amount_records": 0,
                "cash_dividend_amount_complete": False,
                "buyback_records": 0,
                "buyback_actual_amount_yuan": 0,
                "refinancing_records": 0,
                "primary_refinancing_announcements": 0,
                "insider_reduction_announcements": 0,
                "governance_negative_announcements": 0,
            },
            "notes": [],
        }
    )

    assert "分红覆盖 4/5" in text
    assert "累计金额暂缺" in text
    assert "累计每10股现金分红 0" not in text


def test_unknown_historical_dividend_amount_is_not_rendered_or_charted_as_zero():
    dividends = [
        {
            "announce_date": "2023-07-06",
            "dividend_per_10_shares": None,
            "progress": "implemented",
        }
    ]

    text = _build_shareholder_block(None, dividends, [])
    assert "每10股派息金额暂缺" in text
    assert "每10股派息 0" not in text

    assert _build_dividend_chart(
        dividends,
        [],
        {"total_shares": 10_000_000_000},
        {"latest_price": 34.19},
    ) is None


def test_management_capital_block_keeps_old_snapshot_amount_compatibility():
    text = _build_management_capital_block(
        {
            "alignment": {},
            "execution": {},
            "five_year": {
                "window_years": 5,
                "dividend_years_count": 4,
                "dividend_records": 4,
                "cash_dividend_per_10_total": 14.0,
                "buyback_records": 0,
                "buyback_actual_amount_yuan": 0,
                "refinancing_records": 0,
                "primary_refinancing_announcements": 0,
                "insider_reduction_announcements": 0,
                "governance_negative_announcements": 0,
            },
            "notes": [],
        }
    )

    assert "累计每10股现金分红 14.0 元" in text
    assert "0/4 条记录" not in text


def test_bank_fundamentals_block_uses_bank_metrics_not_manufacturing_cashflow_rule():
    text = _build_fundamentals_block(
        {
            "point_in_time": True,
            "facts": {
                "finance_period": "2024-03-31",
                "financial_subtype": "bank",
                "industry_hint": "银行",
                "revenue": 86_417_000_000,
                "net_profit": 38_077_000_000,
                "operating_cash_flow": -1_208_000_000,
                "cash_to_profit_ratio": -0.032,
                "net_interest_margin_pct": 2.02,
                "npl_ratio_pct": 0.92,
                "provision_coverage_pct": 436.82,
                "loan_provision_ratio_pct": 4.02,
                "core_tier1_capital_adequacy_pct": 13.50,
                "tier1_capital_adequacy_pct": 16.16,
                "capital_adequacy_pct": 18.24,
            },
        }
    )

    assert "银行核心指标" in text
    assert "净息差/净利息收益率 2.02%" in text
    assert "不良贷款率 0.92%" in text
    assert "拨备覆盖率 436.82%" in text
    assert "核心一级资本充足率 13.5%" in text
    assert "不得按制造业阈值判断经营质量" in text
    assert "显著小于 1 说明账面利润没有同步变成现金" not in text
