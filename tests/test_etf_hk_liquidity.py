"""Date-gated Hong Kong ETF funding and the two-cycle research hypothesis."""
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from analysis import etf_hk_liquidity, etf_report


def test_hk_exposure_requires_actual_hk_market_not_qdii_alone():
    assert etf_hk_liquidity.is_hk_equity_exposure(
        "华泰柏瑞中证港股通高股息投资ETF(QDII)"
    )
    assert etf_hk_liquidity.is_hk_equity_exposure("恒生科技ETF")
    assert etf_hk_liquidity.is_hk_equity_exposure(
        "跨境指数ETF", [{"code":"01919","weight_pct":19.2}]
    )
    assert not etf_hk_liquidity.is_hk_equity_exposure("纳斯达克100ETF(QDII)")
    assert not etf_hk_liquidity.is_hk_equity_exposure("沪深300ETF")
    assert not etf_hk_liquidity.is_hk_equity_exposure(
        "跨境指数ETF", [{"code":"01919","weight_pct":2.0}]
    )


def _hkma_records():
    # Deliberately reverse order: must select observation by explicit date.
    return {"result": {"records": [
        {
            "end_of_date": (date(2026, 10, 22)-timedelta(days=i)).isoformat(),
            "hibor_fixing_1m": 4.0 if i < 10 else 2.7,
            "closing_balance": 46000 if i < 10 else 60000,
            "hibor_overnight": 3.4,
            "disc_win_base_rate": 4.25,
        } for i in range(90)
    ]}}


def test_hkma_real_dates_and_units_no_fake_low():
    out = etf_hk_liquidity.hkma_liquidity_snapshot(
        _hkma_records(), as_of=date(2026, 10, 23)
    )
    assert out["status"] == "observed"
    assert out["as_of"] == "2026-10-22"
    assert out["hibor_1m_pct"] == 4.0
    assert out["aggregate_balance_hkd_million"] == 46000
    assert out["hibor_1m_change_bp"] > 0
    assert out["aggregate_balance_change_pct"] < 0
    assert out["hibor_90d_percentile_pct"] >= 80
    assert out["balance_90d_percentile_pct"] <= 20
    watch = etf_hk_liquidity.assess_hk_liquidity(
        out, {"fed_target_change_60d_bp":25}, {}, {}
    )
    assert watch["recent_funding_extreme_watch"] is True
    assert watch["liquidity_bottom_confirmed"] is False
    assert etf_hk_liquidity.hkma_liquidity_snapshot(
        _hkma_records(), as_of=date(2026, 11, 23)
    )["status"] == "stale"


def test_fed_target_rate_must_have_real_history():
    dates = pd.bdate_range("2026-05-01", "2026-10-22")
    rates = [4.0 if x < pd.Timestamp("2026-09-15") else 4.25 for x in dates]
    df = pd.DataFrame({"observation_date": dates, "DFEDTARU": rates})
    out = etf_hk_liquidity.fed_rate_snapshot(df, as_of=date(2026, 10, 23))
    assert out["status"] == "observed"
    assert out["fed_target_upper_pct"] == 4.25
    assert out["fed_target_change_60d_bp"] == 25
    assert etf_hk_liquidity.fed_rate_snapshot(
        df, as_of=date(2026, 11, 13)
    )["status"] == "stale"


def test_cpi_ppi_dont_confuse_industrial_deflation_with_general_deflation():
    df = pd.Series([-1.5, -2.1],
                   index=["2026-08-10", "2026-09-10"])
    ppi = etf_hk_liquidity.inflation_snapshot(
        df, as_of=date(2026, 10, 23), indicator="PPI"
    )
    assert ppi["status"] == "observed"
    assert ppi["yoy_pct"] == -2.1
    cpi = {"status": "observed", "yoy_pct": 0.2}
    verdict = etf_hk_liquidity.assess_hk_liquidity(
        {}, {}, cpi, ppi
    )
    assert "不等于所有消费价格" in verdict["china_inflation_assessment"]
    assert verdict["liquidity_bottom_confirmed"] is False
    assert etf_hk_liquidity.inflation_snapshot(
        df, as_of=date(2027, 2, 15), indicator="PPI"
    )["status"] == "unavailable"


def test_pressure_vs_easing_requires_two_independent_confirmations():
    stress = etf_hk_liquidity.assess_hk_liquidity(
        {"hibor_1m_change_bp": 125,
         "aggregate_balance_change_pct": -21},
        {"fed_target_change_60d_bp":25},
        {"status":"observed","yoy_pct":-0.3},
        {"status":"observed","yoy_pct":-3.2},
    )
    assert stress["regime"] == "funding_pressure"
    assert stress["sustained_pressure_confirmed"] is True
    assert stress["liquidity_bottom_confirmed"] is False
    easing = etf_hk_liquidity.assess_hk_liquidity(
        {"hibor_1m_change_bp": -45,
         "aggregate_balance_change_pct": 30},
        {"fed_target_change_60d_bp":0}, {}, {}
    )
    assert easing["regime"] == "funding_easing"
    assert easing["funding_rebound_confirmed"] is True
    uncertain = etf_hk_liquidity.assess_hk_liquidity(
        {"hibor_1m_change_bp": 60}, {}, {}, {}
    )
    assert uncertain["regime"] == "insufficient"


@pytest.mark.asyncio
async def test_hk_etf_report_uses_sanitized_mechanism_not_kol_raw_text(monkeypatch):
    data = {
        "fund":{"code":"513530",
                "name":"华泰柏瑞中证港股通高股息投资ETF(QDII)"},
        "hk_liquidity":{
            "exposure":"hk_equities",
            "as_of":"2026-10-23",
            "hkma":{"status":"observed","as_of":"2026-10-22",
                    "hibor_1m_pct":4.0,"hibor_1m_change_bp":72},
            "fed":{"status":"observed","fed_target_change_60d_bp":25},
            "china_cpi":{"status":"observed","yoy_pct":0.2},
            "china_ppi":{"status":"observed","yoy_pct":-2.1},
            "assessment":{"regime":"funding_pressure"},
        },
    }
    prompts = []
    async def fake(prompt, **kwargs):
        prompts.append(prompt)
        return ({
            "stance":"neutral",
            "thesis_summary":"内地工业价格偏弱，离岸融资更紧，现阶段不急于追高。",
            "sections":{"macro_liquidity":"美元与港元资金更紧，需关注折现率。",
                        "earnings_cycle":"报告期利润兑现仍决定估值下限。"},
            "risk_notes":"",
            "invalidation_condition":"HIBOR回落并伴随内地盈利修复",
        }, None)
    monkeypatch.setattr(etf_report, "call_json_ex", fake)
    out = await etf_report._write_etf_opinion(data)
    assert out["generation_status"] == "complete"
    assert len(prompts) == 1
    assert "联系汇率" in prompts[0]
    assert "港元拆息" in prompts[0]
    assert "离岸融资压力" in prompts[0]
    assert "hk_liquidity" in prompts[0]
    for private in ("老木匠", "买股票的老木匠", "雪球", "Bilibili直播",
                    "视频标题", "原帖全文"):
        assert private not in prompts[0], private
    assert "内地工业价格" in out["thesis_summary"]


@pytest.mark.asyncio
async def test_non_hk_etf_report_does_not_receive_hk_principle(monkeypatch):
    prompts = []
    async def fake(prompt, **kwargs):
        prompts.append(prompt)
        return ({
            "stance":"neutral", "thesis_summary":"A股自身流动性与估值观察。",
            "sections":{}, "risk_notes":"", "invalidation_condition":"",
        }, None)
    monkeypatch.setattr(etf_report, "call_json_ex", fake)
    await etf_report._write_etf_opinion({
        "fund":{"name":"华泰柏瑞沪深300ETF","code":"510300"}
    })
    assert len(prompts) == 1
    assert "港元联系汇率" not in prompts[0]
