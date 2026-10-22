"""Regression: public task polling must NEVER strip ETF fund data and asset type."""
from unittest.mock import AsyncMock

import pytest
from analysis.public_report import public_etf_report, public_stock_report
from web import app as web_app


def _etf_payload():
    return {
        "asset_type": "etf", "stock_code": "SH517520",
        "stock_name": "黄金股ETF永赢", "research_mode": "live",
        "as_of": "2026-10-22",
        "etf_data": {
            "asset_type": "etf", "fund": {"latest_price": 1.716,
                         "quote_label": "最近交易日收盘价",
                         "quote_time": "2026-10-22（非实时）"},
            "portfolio": {
                "as_of": "2026-Q2", "disclosure_as_of": "2026-06-30",
                "holdings": [
                    {"name": f"公司{i}", "code": f"600{i:03d}",
                     "weight_pct": 10.0-i*0.5}
                    for i in range(1,11)
                ],
            },
            "portfolio_stats": {"top10_weight_pct": 77.5},
            "etf_price_position": {"recent_close": 1.716,
                                   "recent_close_as_of": "2026-10-22"},
            "macro_market": {
                "sector": "黄金产业股票",
                "observations": {"gold_cny": {
                    "value": 880.3, "as_of": "2026-10-22",
                    "source": "上海黄金交易所"
                }},
            },
            "industry_cycle": {"stage": "盈利分化"},
        },
        "summary": {
            "stance": "neutral",
            "thesis_summary": "基于真实黄金与盈利数据判断。",
            "sections": {
                "macro_liquidity": "实际利率影响黄金资产定价",
                "industry_cycle": "金矿行业盈利分化",
            },
            "risk_notes": "金价下行、成本抬升。",
            "invalidation_condition": "行业利润继续下滑。",
        },
        "section_labels": {"macro_liquidity": "宏观与流动性环境"},
        "generated_at": 12345,
        # This internal key must NEVER escape either public projection.
        "knowledge_excerpts": [{"author": "internal", "body": "private"}],
    }


def test_etf_projection_preserves_all_etf_only_data_without_private_fields():
    out = public_etf_report(_etf_payload())
    assert out["asset_type"] == "etf"
    assert out["stock_code"] == "SH517520"
    assert out["etf_data"]["fund"]["latest_price"] == 1.716
    assert len(out["etf_data"]["portfolio"]["holdings"]) == 10
    assert out["etf_data"]["macro_market"]["observations"]["gold_cny"]["value"] == 880.3
    assert out["summary"]["risk_notes"] == "金价下行、成本抬升。"
    assert out["summary"]["invalidation_condition"] == "行业利润继续下滑。"
    assert "knowledge_excerpts" not in out
    assert public_etf_report(out) == out


@pytest.mark.asyncio
async def test_task_poll_does_not_run_equity_allowlist_on_an_etf(monkeypatch):
    raw = _etf_payload()
    tid = "unit-test-etf-task-contract"
    monkeypatch.setitem(web_app._tasks, tid, {
        "status": "done", "result": raw,
        "asset_type": "etf", "error": None,
    })
    result = await web_app.get_task(tid)
    assert result["status"] == "done"
    assert result["result"]["asset_type"] == "etf"
    assert result["result"]["stock_name"] == "黄金股ETF永赢"
    assert result["result"]["etf_data"]["fund"]["latest_price"] == 1.716
    assert len(result["result"]["etf_data"]["portfolio"]["holdings"]) == 10
    assert result["result"]["summary"]["sections"]["macro_liquidity"]


@pytest.mark.asyncio
async def test_old_task_with_etf_tag_is_recovered_without_issuer_projection(monkeypatch):
    tid = "unit-test-etf-legacy-tag"
    original = _etf_payload()
    original.pop("asset_type")
    monkeypatch.setitem(web_app._tasks, tid, {
        "status": "done", "result": original,
        "asset_type": "etf", "error": None,
    })
    result = await web_app.get_task(tid)
    assert result["result"]["asset_type"] == "etf"
    assert result["result"]["etf_data"]["portfolio"]["disclosure_as_of"] == "2026-06-30"


@pytest.mark.asyncio
async def test_etf_overview_api_returns_quote_and_ten_without_llm(monkeypatch):
    fund = {"code": "517520", "stock_code": "SH517520", "name": "黄金股ETF永赢"}
    monkeypatch.setattr(web_app, "resolve_etf", AsyncMock(return_value=fund))
    monkeypatch.setattr(web_app, "collect_etf_overview", AsyncMock(return_value={
        "asset_type": "etf", "fund": {"latest_price": 1.716},
        "portfolio": {"as_of": "2026-Q2", "holdings": _etf_payload()[
            "etf_data"]["portfolio"]["holdings"]},
        "portfolio_stats": {"top10_weight_pct": 77.5},
    }))
    result = await web_app.get_etf_overview("SH517520")
    assert result["asset_type"] == "etf"
    assert result["etf_data"]["fund"]["latest_price"] == 1.716
    assert len(result["etf_data"]["portfolio"]["holdings"]) == 10
    web_app.collect_etf_overview.assert_awaited_once_with(fund)


def test_stock_projection_remains_private_and_unmodified():
    stock = public_stock_report({
        "stock_code": "SH600519", "stock_name": "贵州茅台",
        "asset_type": "stock",
        "etf_data": {"fake": "secret"},
        "knowledge_excerpts": [{"author": "internal", "body": "private"}],
    })
    assert stock["stock_name"] == "贵州茅台"
    assert "etf_data" not in stock
    assert "knowledge_excerpts" not in stock
