"""ETF high-price risk is a warning to investigate, not proof of bad trading."""
from analysis.etf_holding_risk import management_style, screen_holdings


def test_index_etf_must_warn_passive_high_price_reconstitution_risk():
    fund = {"name":"华泰柏瑞中证港股通高股息投资ETF(QDII)", "code":"513530"}
    style = management_style(fund)
    assert style["kind"] == "index"
    assert style["verified"] is False
    result = screen_holdings(fund, {
        "disclosure_as_of":"2026-06-30",
        "holdings":[
            {"code":"01919","name":"中远海控","weight_pct":5.91,
             "price_position_pct":92,
             "pe_history_position":{"percentile":90},"price_as_of":"2026-10-22"},
            {"code":"00386","name":"中国石化","weight_pct":5.11,
             "price_position_pct":24},
            {"code":"01088","name":"中国神华","weight_pct":4.21,
             "pb_history_position":{"percentile":91}},
        ],
    })
    assert "被动高位承接" in result["risk_type"]
    assert result["high_price_weight_pct"] == 5.91
    assert result["high_valuation_weight_pct"] == 10.12
    assert result["high_price_and_valuation_weight_pct"] == 5.91
    assert result["price_observed_weight_pct"] == 11.02
    assert result["valuation_observed_weight_pct"] == 10.12
    assert len(result["watchlist"]) == 2
    assert any("指数" in text for text in result["verification_needed"])
    assert "不能" in result["limitations"] or "不能" in result["risk_summary"]


def test_active_etf_risk_must_not_assert_manager_manipulation():
    fund={"name":"某某主动管理ETF","code":"159002"}
    result=screen_holdings(fund, {"holdings":[{
        "code":"600519","name":"样本股份","weight_pct":12,
        "price_position_pct":95,
    }]})
    assert result["management_style"]["kind"]=="active"
    assert "追逐" in result["risk_summary"]
    assert "不能仅凭" in result["risk_summary"]
    assert any("经理" in line for line in result["verification_needed"])
    assert result["high_price_weight_pct"] == 12
    assert result["high_valuation_weight_pct"] is None
    assert result["valuation_observed_weight_pct"] == 0
    assert result["valuation_observed_count"] == 0


def test_unknown_style_warns_neither_type_as_fact_and_no_data_as_no_risk():
    fund={"name":"某主题ETF","code":"159900"}
    result=screen_holdings(fund, {"holdings":[]})
    assert result["management_style"]["kind"] == "unknown"
    assert result["management_style"]["verified"] is False
    assert "核实" in result["risk_summary"]
    assert result["price_observed_weight_pct"] == 0
    assert result["price_observed_count"] == 0
    assert result["high_price_weight_pct"] is None
    assert result["high_valuation_weight_pct"] is None
    assert result["watchlist"] == []


def test_other_index_name_and_price_high_not_equal_valuation_bubble():
    result=screen_holdings({"name":"沪深300ETF华泰柏瑞"},{
        "holdings":[{"name":"A","code":"600000","weight_pct":12,
                    "price_position_pct":85,
                    "pe_history_position":{"percentile":18}}],
    })
    assert result["management_style"]["kind"]=="index"
    assert result["high_price_weight_pct"] == 12
    assert result["high_valuation_weight_pct"] == 0
    assert result["high_price_and_valuation_weight_pct"] == 0
    assert len(result["watchlist"]) == 1
    assert "非泡沫认定" in result["limitations"]
