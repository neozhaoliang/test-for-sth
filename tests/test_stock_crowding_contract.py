"""Trading crowding must survive issuer report schemas and public projection."""
from model.m_analysis import AnalysisReport
from analysis.public_report import public_stock_report
from analysis.trading_crowding import assess_margin_crowding


def test_stock_crowding_survives_report_and_public_projection():
    margin = {
        "latest_balance_yi": 38.95, "change_pct": -3.43,
        "latest_date": "20261023", "days": 6, "source": "交易所融资明细",
        "latest_buy_yuan": 25000000,
    }
    activity = {
        "status": "observed", "as_of": "2026-10-23",
        "latest_turnover_yuan": 200000000,
        "latest_turnover_yi": 2,
        "latest_turnover_rate_pct": 2.9,
        "recent5_vs_prior20_ratio": 1.35,
        "source": "证券自身成交历史",
    }
    result = assess_margin_crowding(
        margin, activity, asset_type="stock",
        float_market_cap_yi=796.5, price_position_pct=64,
    )
    assert result["margin_balance_yi"] == 38.95
    assert result["margin_to_float_market_cap_pct"] > 0
    assert result["financing_buy_to_same_day_turnover_pct"] == 12.5
    report = AnalysisReport(stock_code="SH600000",stock_name="测试股票",
                            trading_crowding=result)
    assert report.model_dump()["trading_crowding"]["margin_balance_yi"] == 38.95
    public = public_stock_report(report.model_dump())
    assert public["trading_crowding"]["margin_change_pct"] == -3.43
    assert public["trading_crowding"]["recent5_turnover_ratio"] == 1.35


def test_missing_leverage_never_treated_as_zero_or_made_up():
    result = assess_margin_crowding(
        None, None, asset_type="stock",
    )
    assert result["margin_balance_yi"] is None
    assert result["recent5_turnover_ratio"] is None
    assert result["level"] == "unknown"
    assert public_stock_report(AnalysisReport(
        stock_code="SH600000", trading_crowding=result
    ).model_dump())["trading_crowding"]["coverage"]["financing"] is False
