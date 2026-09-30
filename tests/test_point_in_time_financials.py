from datetime import date

from analysis.point_in_time_financials import (
    _latest_period_is_fresh_enough,
    parse_financial_report_text,
    to_historical_fundamentals,
    to_historical_profitability,
)


def test_parse_standard_periodic_report_metrics():
    text = """
    主要会计数据和财务指标
    营业收入 12,345,678,900.00
    归属于上市公司股东的净利润 1,234,567,890.00
    经营活动产生的现金流量净额 1,500,000,000.00
    加权平均净资产收益率 12.50%
    合并资产负债表
    资产总计 30,000,000,000.00
    负债合计 12,000,000,000.00
    """
    out = parse_financial_report_text(text)

    assert out["revenue"] == 12_345_678_900
    assert out["net_profit"] == 1_234_567_890
    assert out["operating_cash_flow"] == 1_500_000_000
    assert out["roe_pct"] == 12.5
    assert out["net_margin_pct"] == 10.0
    assert out["debt_ratio_pct"] == 40.0
    assert out["cash_to_profit_ratio"] == 1.215


def test_parse_parentheses_as_negative_financial_value():
    text = """
    主要会计数据和财务指标
    营业收入 1,000,000,000
    归属于上市公司股东的净利润 (50,000,000)
    经营活动产生的现金流量净额 (20,000,000)
    """
    out = parse_financial_report_text(text)

    assert out["net_profit"] == -50_000_000
    assert out["operating_cash_flow"] == -20_000_000
    assert out["net_margin_pct"] == -5.0
    assert out["cash_to_profit_ratio"] == 0.4


def test_historical_contract_keeps_period_and_publication_date_distinct():
    data = {
        "as_of": "2026-04-01",
        "latest_period": "2025-12-31",
        "latest_published_at": "2026-03-28",
        "latest": {
            "period": "2025-12-31",
            "published_at": "2026-03-28",
            "url": "https://static.cninfo.com.cn/annual.pdf",
            "revenue": 1000.0,
            "net_profit": 100.0,
            "operating_cash_flow": 120.0,
            "cash_to_profit_ratio": 1.2,
        },
        "periods": [
            {
                "period": "2024-12-31",
                "published_at": "2025-03-25",
                "net_margin_pct": 9.0,
                "roe_pct": 11.0,
                "debt_ratio_pct": 35.0,
            },
            {
                "period": "2025-12-31",
                "published_at": "2026-03-28",
                "net_margin_pct": 10.0,
                "roe_pct": 12.0,
                "debt_ratio_pct": 34.0,
            },
        ],
    }

    fundamentals = to_historical_fundamentals(data)
    profitability = to_historical_profitability(data)

    assert fundamentals["facts"]["finance_period"] == "2025-12-31"
    assert fundamentals["available_at"] == "2026-03-28"
    assert fundamentals["point_in_time"] is True
    assert profitability["as_of"] == "2026-04-01"
    assert profitability["available_at"] == "2026-03-28"
    assert profitability["point_in_time"] is True



def test_latest_period_freshness_rejects_obviously_stale_calendar():
    cutoff = date(2023, 6, 30)

    assert _latest_period_is_fresh_enough("2023-03-31", cutoff)
    assert _latest_period_is_fresh_enough("2022-12-31", cutoff)
    assert not _latest_period_is_fresh_enough("2019-12-31", cutoff)
    assert not _latest_period_is_fresh_enough("2024-03-31", cutoff)



def test_parse_bank_specific_financial_labels():
    text = """
    本集团主要会计数据及财务指标
    营业收入 86,417
    归属于本行股东的净利润 38,077
    年化后归属于本行普通股股东的加权平均净资产收益率(%) 16.08
    经营活动产生的现金流量净额 (1,208)
    总资产 11,520,226
    """
    out = parse_financial_report_text(text)

    assert out["revenue"] == 86417
    assert out["net_profit"] == 38077
    assert out["operating_cash_flow"] == -1208
    assert out["roe_pct"] == 16.08
