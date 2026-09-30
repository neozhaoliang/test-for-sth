from datetime import date

from analysis.point_in_time_financials import (
    _latest_period_is_fresh_enough,
    parse_financial_report_pages,
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
    （人民币百万元，特别注明除外）
    营业收入 86,417
    归属于本行股东的净利润 38,077
    年化后归属于本行普通股股东的加权平均净资产收益率(%) 16.08
    经营活动产生的现金流量净额 (1,208)
    总资产 11,520,226
    """
    out = parse_financial_report_text(text)

    assert out["revenue"] == 86_417_000_000
    assert out["net_profit"] == 38_077_000_000
    assert out["operating_cash_flow"] == -1_208_000_000
    assert out["total_assets"] == 11_520_226_000_000
    assert out["roe_pct"] == 16.08
    assert out["monetary_multiplier"] == 1_000_000



def test_monetary_unit_defaults_to_original_value_when_not_declared():
    out = parse_financial_report_text("营业收入 123.45 归属于上市公司股东的净利润 12.34")
    assert out["revenue"] == 123.45
    assert out["net_profit"] == 12.34
    assert out["monetary_multiplier"] == 1.0


def test_q3_parser_prefers_year_to_date_columns_and_local_unit():
    pages = [
        """
        2022年第三季度报告
        一、主要财务数据
        （一）主要会计数据和财务指标
        单位：元 币种：人民币
        项目 本报告期 本报告期比上年同期增减变动幅度(%) 年初至报告期末 年初至报告期末比上年同期增减变动幅度(%)
        营业收入 29,543,366,111.76 15.61 87,160,232,759.05 16.77
        归属于上市公司股东的净利润
        14,605,907,505.71 15.81 44,399,815,583.54 19.14
        归属于上市公司股东的扣除非经常性损益的净利润
        14,630,409,785.50 15.13 44,393,231,516.91 18.84
        """,
        """
        经营活动产生的现金流量净额
        不适用 不适用 9,405,337,008.75 -74.41
        加权平均净资产收益率（%）
        7.32 减少0.19个百分点 21.91 增加0.23个百分点
        本报告期末 上年度末
        总资产 247,756,923,862.35 255,168,195,159.90 -2.90
        """,
        """
        （四）销售情况
        单位：万元 币种：人民币
        主营业务收入
        7,439,989.21 1,254,038.88 3,188,162.21
        """,
        """
        合并资产负债表
        2022年9月30日
        单位：元 币种：人民币
        资产总计 247,756,923,862.35 255,168,195,159.90
        负债合计 34,310,347,852.50 58,210,688,454.56
        """,
    ]

    out = parse_financial_report_pages(pages)

    assert out["basis"] == "ytd"
    assert out["monetary_multiplier"] == 1.0
    assert out["revenue"] == 87_160_232_759.05
    assert out["net_profit"] == 44_399_815_583.54
    assert out["operating_cash_flow"] == 9_405_337_008.75
    assert out["roe_pct"] == 21.91
    assert out["total_assets"] == 247_756_923_862.35
    assert out["total_liabilities"] == 34_310_347_852.50


def test_bank_q1_parser_ignores_metric_footnotes_but_keeps_negative_cashflow():
    pages = [
        """
        2 主要财务数据
        2.1 本集团主要会计数据及财务指标
        （人民币百万元，特别注明除外）
        2024年1-3月 2023年1-3月 同比增减(%)
        营业收入 86,417 90,636 -4.65
        归属于本行股东的净利润 38,077 38,839 -1.96
        年化后归属于本行普通股股东的加权平均净资产收益率(%)(1)
        16.08 18.43 下降2.35个百分点
        净利息收益率(%) 2.02 2.29 下降0.27个百分点
        不良贷款率(%) 0.92 0.95 下降0.03个百分点
        拨备覆盖率(%) 436.82 437.70 下降0.88个百分点
        贷款拨备率(%) 4.02 4.16 下降0.14个百分点
        核心一级资本充足率(%) 13.50 13.73
        一级资本充足率(%) 16.16 16.27
        资本充足率(%) 18.24 18.30
        经营活动产生的现金流量净额(2) (1,208) (12,618) 90.43
        """,
        """
        合并资产负债表
        （人民币百万元）
        总资产 11,520,226 11,028,483 4.46
        负债合计 10,394,735 9,943,500 4.54
        """,
    ]

    out = parse_financial_report_pages(pages)

    assert out["monetary_multiplier"] == 1_000_000.0
    assert out["revenue"] == 86_417_000_000
    assert out["net_profit"] == 38_077_000_000
    assert out["roe_pct"] == 16.08
    assert out["operating_cash_flow"] == -1_208_000_000
    assert out["financial_subtype"] == "bank"
    assert out["industry_hint"] == "银行"
    assert out["net_interest_margin_pct"] == 2.02
    assert out["npl_ratio_pct"] == 0.92
    assert out["provision_coverage_pct"] == 436.82
    assert out["loan_provision_ratio_pct"] == 4.02
    assert out["core_tier1_capital_adequacy_pct"] == 13.50
    assert out["tier1_capital_adequacy_pct"] == 16.16
    assert out["capital_adequacy_pct"] == 18.24
