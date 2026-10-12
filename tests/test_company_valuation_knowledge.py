"""Company-specific valuation theses are as-of safe and never made public."""
import json
from datetime import date

from analysis.company_valuation_knowledge import (
    load_company_valuation_views, company_valuation_prompt,
)
from analysis.investor_valuation import investor_reference_valuation
from analysis.knowledge_base import _build_sources


def _add(tmp_path, uid, rows):
    file = tmp_path / f"creator_{uid}_contents_2026-10-12.jsonl"
    file.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")


def test_long_term_valuation_investors_are_registered():
    names = {x.name for x in _build_sources()}
    assert "xueqiu_5270498878" in names
    assert "xueqiu_6262681062" in names


def test_direct_issuer_valuation_retrieval_uses_dated_posts_only(tmp_path):
    _add(tmp_path, "5270498878", [
        {"status_id": "111", "created_at": "2026-10-10",
         "description": "双木林叔关注华友钴业，归母净利情景80亿元、需扣除维持性资本开支，估值不能用顶部利润。"},
        {"status_id": "222", "created_at": "2026-10-14",
         "description": "华友钴业未来的估值观点不能倒灌到10月12日"},
        {"status_id": "333", "created_at": "2026-10-11",
         "description": "其他公司净利润同比上升，估值需关注回款"},
        {"status_id": "444", "created_at": "2026-10-11",
         "description": "华友钴业的车间建设在下雨，什么也没说"},
    ])
    _add(tmp_path, "6262681062", [
        {"status_id": "555", "created_at": "2026-10-11",
         "description": "陈chensir讨论华友钴业合理估值需将可持续ROE与现金分红分开"},
    ])
    found = load_company_valuation_views(
        stock_name="华友钴业", stock_code="SH603799",
        as_of="2026-10-12", directory=tmp_path,
    )
    assert len(found) == 2
    assert all(x["published_at"] <= "2026-10-12" for x in found)
    prompt = company_valuation_prompt(found, stock_name="华友钴业")
    assert "80亿元" in prompt
    for secret in ("双木林叔", "陈chensir", "xueqiu.com", "5270498878",
                   "6262681062", "status_id"):
        assert secret not in prompt
    assert "不是" in prompt or "未核验" in prompt


def test_no_recent_article_does_not_invent_price_or_new_author_post(tmp_path):
    result = load_company_valuation_views(
        stock_name="华友钴业", stock_code="SH603799",
        as_of="2026-10-12", directory=tmp_path,
    )
    assert result == []
    msg = company_valuation_prompt(result, stock_name="华友钴业")
    assert "不得声称已经读过" in msg


def test_issuer_earnings_power_no_hurdle_required():
    rows = [
        {"period": f"{y}-12-31", "eps_yuan":v, "roe_pct":r}
        for y, v, r in ((2022, 1.5, 7), (2023, 2.2, 9),
                         (2024, 1.8, 8), (2025, 3.0, 12))
    ]
    history = [
        {"date": f"{y}-{m:02d}-28", "pe_ttm": 8 + (m % 10) + (y-2022)}
        for y in range(2022, 2026) for m in range(1, 13)
    ]
    result = investor_reference_valuation(
        archetype="cyclical", as_of="2026-10-12",
        quote={"latest_price":34.53},
        valuation={"nav_per_share": 27},
        profitability_trend={"periods":rows},
        valuation_history={"history_monthly":history, "as_of":"2026-10-12"},
    )
    base = result["earnings_power"]
    assert base["status"] == "historical_sensitivity_only"
    assert len(base["scenarios"]) == 3
    assert all(x["conditional_price_yuan"] > 0 for x in base["scenarios"])
    assert base["earnings_years"] == [2022, 2023, 2024, 2025]
    assert base["pe_sample_months"] == 48
    assert result["return_decomposition"]["scenarios"] == []
    assert result["dividend_scenarios"] == []


def test_future_earnings_and_pe_not_visible_to_historical_valuations():
    rows = [
        {"period": f"{year}-12-31", "eps_yuan":float(year-2020)}
        for year in range(2022, 2027)
    ]
    months = [
        {"date": f"{year}-{mon:02d}-01", "pe_ttm":float(mon+10)}
        for year in range(2022, 2027) for mon in range(1, 13)
    ]
    result = investor_reference_valuation(
        archetype="cyclical", as_of="2025-04-01",
        quote={"latest_price":32},
        profitability_trend={"periods":rows},
        valuation_history={"history_monthly":months},
    )
    analysis = result["earnings_power"]
    assert analysis["earnings_years"] == [2022, 2023, 2024]
    assert analysis["pe_sample_months"] == 39
