"""Broad A-share index ETF: family guardrails and dated, independently computable evidence."""
from datetime import date, timedelta

import pandas as pd
import pytest

from analysis import etf_broad_market, etf_macro, etf_report


@pytest.mark.parametrize("name,family,index", [
    ("华夏上证50ETF", "上证50", "000016"),
    ("华泰柏瑞沪深300ETF", "沪深300", "000300"),
    ("易方达创业板ETF", "创业板指", "399006"),
    ("华夏科创50ETF", "科创50", "000688"),
    ("科创板50ETF易方达", "科创50", "000688"),
    ("科创100ETF", "科创100", "000698"),
    ("科创综指ETF", "科创综指", "000680"),
    ("科创板综合ETF", "科创综指", "000680"),
    ("南方中证500ETF", "中证500", "000905"),
])
def test_broad_etf_family(name, family, index):
    result = etf_broad_market.classify_broad_index(name)
    assert result is not None
    assert result["family"] == family
    assert result["index_code"] == index
    assert etf_macro.sector_for_etf(name)["sector"] == "宽基市场"
    assert etf_report._rules_for_profile("宽基市场") == etf_report._BROAD_RULES


@pytest.mark.parametrize("name", [
    "沪深300增强ETF", "沪深300红利ETF", "沪深300质量ETF",
    "创业板50ETF", "创业板成长ETF", "科创板芯片ETF",
    "科创板半导体ETF", "科创板ETF", "科创创业50ETF",
    "恒生科技ETF(QDII)", "港股通高股息ETF",
])
def test_index_variants_not_misclassified_as_plain_broad(name):
    assert etf_broad_market.classify_broad_index(name) is None


def _price_tape():
    dates = pd.bdate_range("2025-10-01", "2026-10-22")
    closes = [100.0 if d.year == 2025 else
              100.0 + (i - sum(day.year == 2025 for day in dates))*0.2
              for i, d in enumerate(dates)]
    amounts = [1e10] * (len(dates)-5) + [2e10] * 5
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "收盘": closes, "成交额": amounts,
    })


def test_index_tape_ytd_uses_prior_year_last_close_and_independent_volume():
    result = etf_broad_market.summarize_index_tape(
        _price_tape(), as_of=date(2026, 10, 23),
    )
    assert result["status"] == "observed"
    assert result["ytd_base_date"] == "2025-12-31"
    assert result["ytd_pct"] > 0
    assert result["return_20d_pct"] > 0
    assert result["return_60d_pct"] > result["return_20d_pct"]
    assert result["recent5_vs_prior20_amount_ratio"] == pytest.approx(2.0)
    assert result["price_position_52w_pct"] == 100.0
    assert result["ytd_max_drawdown_pct"] == 0.0
    assert result["amount_as_of"] == "2026-10-22"


def test_index_tape_missing_previous_year_cannot_invent_ytd():
    dates = pd.bdate_range("2026-01-05", periods=40)
    frame = pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "收盘": [100+i for i in range(len(dates))],
    })
    out = etf_broad_market.summarize_index_tape(
        frame, as_of=date(2026, 3, 2),
    )
    assert "ytd_pct" not in out
    out_stale = etf_broad_market.summarize_index_tape(
        frame, as_of=date(2026, 10, 23),
    )
    assert out_stale["status"] == "stale"


def test_index_pe_only_when_dated_and_period_has_enough_history():
    dates = pd.bdate_range("2025-01-02", periods=310)
    df = pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "滚动市盈率": [12 + i/80 for i in range(len(dates))],
    })
    as_of = dates[-1].date()
    out = etf_broad_market.summarize_index_pe(df, as_of=as_of)
    assert out["status"] == "observed"
    assert out["pe_ttm"] > 12
    assert out["pe_3y_percentile_pct"] > 95
    assert etf_broad_market.summarize_index_pe(
        df, as_of=as_of + timedelta(days=40)
    )["status"] == "stale"


def test_recent_admissions_need_actual_inclusion_date_not_snapshot_date():
    frame = pd.DataFrame([
        {"品种代码": "688256", "品种名称": "样本甲",
         "纳入日期": "2026-09-15"},
        {"品种代码": "600519", "品种名称": "样本乙",
         "纳入日期": "2020-01-01"},
    ])
    out = etf_broad_market._recent_entries(frame, as_of=date(2026, 10, 23))
    assert out["recent_count"] == 1
    assert out["recent_entries"][0]["code"] == "688256"
    assert etf_broad_market._recent_entries(
        pd.DataFrame([{"日期": "2026-09-15", "成分券代码": "688256"}]),
        as_of=date(2026, 10, 23),
    )["status"] == "unavailable"


def test_high_flyer_means_verified_pre_inclusion_momentum_not_current_price():
    in_date = date(2026, 8, 3)
    dates = pd.bdate_range(end=pd.Timestamp(in_date)-timedelta(days=1), periods=70)
    df = pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "收盘": [20 + i*0.2 for i in range(70)],
    })
    out = etf_broad_market._pre_entry_momentum(df, admitted_on=in_date)
    assert out["pre_inclusion_60d_return_pct"] > 40
    assert out["momentum_flag"] is True
    assert etf_broad_market._pre_entry_momentum(
        df.iloc[-20:], admitted_on=in_date
    ) == {}


def test_broad_prompt_and_compaction_exclude_commodity_cycle():
    assert "指数今年以来" in etf_report._BROAD_RULES
    assert "纳入前" in etf_report._BROAD_RULES
    sections = etf_report._compact_etf_sections({
        "macro_liquidity": "利率变化",
        "commodity_market": "铜库存上涨，实际上与宽基无关",
        "industry_cycle": "盈利广度观察",
        "high_positions": "新纳入股票",
        "earnings_cycle": "重仓股盈利段",
        "valuation": "指数整体估值",
    }, "宽基市场")
    assert sections["macro_liquidity"] == "利率变化"
    assert sections["high_positions"] == "新纳入股票"
    assert sections["valuation"] == "指数整体估值"
    assert sections["industry_cycle"] == "盈利广度观察"
    assert sections["commodity_market"] == ""
    assert sections["earnings_cycle"] == ""
