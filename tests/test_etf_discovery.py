"""Source-independent ETF discovery: multiple managers, verified scale ordering."""
from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from analysis.etf_discovery import assets_yi, matches, ranked_matches, recent_day, theme
from analysis import etf_data


def _fund(code, name, scale=None):
    return {"code": code, "stock_code": ("SH" if code.startswith("5") else "SZ") + code,
            "name": name, "scale_yi": scale}


def test_gold_stock_theme_includes_all_managers_but_not_physical_gold():
    rows = [
        _fund("517520", "黄金股ETF永赢", 100),
        _fund("159562", "华夏中证沪深港黄金产业股票ETF", 65),
        _fund("517400", "国泰中证沪深港黄金产业股票ETF", 18),
        _fund("159321", "华安中证沪深港黄金产业股票ETF", 6),
        _fund("159315", "黄金股ETF工银", 4),
        _fund("159322", "平安中证沪深港黄金产业ETF", 2),
        _fund("518880", "黄金ETF华安", 250),
        _fund("159934", "黄金ETF易方达", 45),
    ]
    assert theme("黄金股 etf") == "黄金产业股票"
    assert [r["code"] for r in ranked_matches("黄金股ETF", rows)] == [
        "517520", "159562", "517400", "159321", "159315", "159322"]
    assert not matches("黄金股ETF", rows[6])


def test_300_etf_matches_manager_prefix_suffix_without_wrong_index_variants():
    rows = [
        _fund("510330", "沪深300ETF华夏", 380),
        _fund("510300", "华泰柏瑞沪深300ETF", 1200),
        _fund("159919", "嘉实沪深300ETF", 300),
        _fund("510310", "易方达沪深300ETF", 660),
        _fund("561990", "沪深300增强ETF", 9000),
        _fund("515300", "沪深300红利低波ETF", 6000),
        _fund("515100", "中证500ETF", 5000),
    ]
    assert [r["code"] for r in ranked_matches("沪深300etf", rows)] == [
        "510300", "510310", "510330", "159919"]
    assert [r["code"] for r in ranked_matches("510330", rows)] == ["510330"]


def test_size_unknown_sorts_last_never_as_zero_or_as_turnover():
    rows = [
        _fund("510330", "沪深300ETF华夏", None),
        _fund("510300", "沪深300ETF华泰柏瑞", 1000),
        _fund("510310", "沪深300ETF易方达", 700),
        _fund("159919", "沪深300ETF嘉实", None),
    ]
    assert [r["code"] for r in ranked_matches("沪深300ETF", rows)] == [
        "510300", "510310", "159919", "510330"]


def test_sina_nav_times_shares_units_and_stale_date_gate():
    now = date(2026, 10, 22)
    frame = pd.DataFrame([
        {"基金代码": "510300", "最近总份额": 20000000000,
         "单位净值": 5.0, "更新日期": "2026-10-21"},
        {"基金代码": "159919", "最近总份额": 5000000000,
         "单位净值": 4, "更新日期": "2022-01-01"},
        {"基金代码": "517520", "最近总份额": 0,
         "单位净值": 2, "更新日期": "2026-10-21"},
    ])
    sizes = etf_data._parse_sina_scale(frame, now)
    assert sizes["510300"]["scale_yi"] == 1000
    assert sizes["510300"]["scale_as_of"] == "2026-10-21"
    assert "159919" not in sizes
    assert "517520" not in sizes
    assert assets_yi(2e9, 2.5) == 50
    assert assets_yi(None, 2.5) is None
    assert recent_day("2022-01-01", 45, now) is None


def test_market_cap_is_labeled_price_proxy_not_nav():
    df = pd.DataFrame([
        {"代码": "517520", "数据日期": "2026-10-21", "总市值": 13_000_000_000},
        {"代码": "159562", "数据日期": "2024-10-21", "总市值": 90_000_000_000},
    ])
    result = etf_data._parse_spot_scale(df, date(2026, 10, 22))
    assert result["517520"]["scale_yi"] == 130
    assert result["517520"]["scale_kind"] == "二级市场市值近似"
    assert "159562" not in result


@pytest.mark.asyncio
async def test_ranked_search_is_generic_and_uses_cross_market_sizes(monkeypatch):
    rows = [
        etf_data._catalog_entry("510300", "沪深300ETF华泰柏瑞", source="fund_name_em"),
        etf_data._catalog_entry("510310", "沪深300ETF易方达", source="fund_name_em"),
        etf_data._catalog_entry("159919", "嘉实沪深300ETF", source="fund_name_em"),
    ]
    monkeypatch.setattr(etf_data, "_catalog", AsyncMock(return_value=rows))
    monkeypatch.setattr(etf_data, "_catalog_refresh_task", None)
    monkeypatch.setattr(etf_data, "_scale_refresh_task", None)
    monkeypatch.setattr(etf_data, "_scale_cache", (
        float("inf"), {
            "159919": {"scale_yi": 310, "scale_as_of": "2026-10-21",
                       "scale_kind": "基金资产规模估算"},
            "510300": {"scale_yi": 1210, "scale_as_of": "2026-10-21",
                       "scale_kind": "基金资产规模估算"},
        }))
    options = await etf_data.search_etfs("沪深300 ETF", 60)
    assert [r["code"] for r in options] == ["SH510300", "SZ159919", "SH510310"]
    assert options[-1]["scale_yi"] is None
    assert all(r["asset_type"] == "etf" for r in options)
    assert await etf_data.resolve_etf("沪深300ETF") is None
