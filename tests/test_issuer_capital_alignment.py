"""Company capital attribution is independent of market posters and dividends."""
from datetime import date
from types import SimpleNamespace

from analysis.issuer_capital import build_issuer_capital_context
from analysis.public_report import public_stock_report


def _context(day="2026-10-12"):
    return build_issuer_capital_context(
        "SZ300308",as_of=day,primary_evidence=[],
        research_profile=SimpleNamespace(archetype="technology"),
    )


def test_300308_verified_2025_2026_sales_not_disguised_by_buyback():
    ctx=_context()
    trades=[
        x for x in ctx["source_verified_events"]
        if x["event_type"]=="insider_sale" and x["status"]=="completed"
    ]
    assert len(trades)==2
    assert {x["shares"] for x in trades}=={5_500_000,708_552}
    assert {round(x["price_yuan"],2) for x in trades}=={521.73,414.24}
    assert ctx["completed_insider_sales"]==2
    assert ctx["completed_insider_sale_shares"]==6_208_552
    assert ctx["disclosed_price_insider_proceeds_yuan"]>0
    assert ctx["completed_equity_issuances"]==1
    assert ctx["buyback_plan_records"]==1
    assert "稀释" in ctx["capital_question"]


def test_2026_july_ownership_change_is_restatement_not_third_sale():
    ctx=_context()
    rec=ctx["reconciled_ownership_not_additional_sales"]
    assert len(rec)==1
    assert rec[0]["fully_reconciled"]
    assert rec[0]["reported_group_sales_shares"]==6_208_552
    assert rec[0]["component_total_shares"]==6_208_552
    assert rec[0]["not_additional_trade"]


def test_historical_research_never_uses_future_insider_sales():
    before=_context("2025-09-01")
    assert not any(e["disclosed_at"]>"2025-09-01"
                   for e in before["source_verified_events"])
    assert before["completed_insider_sale_shares"]==0
    march=_context("2026-03-01")
    assert march["completed_insider_sale_shares"]==6_208_552
    assert not march["reconciled_ownership_not_additional_sales"]


def test_public_report_exposes_only_company_capital_fact_not_original_posts():
    private=_context()
    raw={
        "stock_code":"SZ300308", "stock_name":"中际旭创",
        "issuer_capital_context":private,
        "knowledge_excerpts":[{
            "author":"军师祭咖啡", "title":"其他股票观点",
            "distilled":"引用与本公司无关的股票分析。",
        }],
        "summary":{"thesis_summary":"股权融资需关注每股回报。"}
    }
    result=public_stock_report(raw)
    assert "issuer_capital_context" not in result
    assert "knowledge_excerpts" not in result
    summary=result["shareholder_capital_summary"]
    assert len(summary["completed_holder_sales"])>=2
    assert summary["equity_issuances"]
    assert summary["corporate_buybacks"]
    assert summary["reconciled_ownership_not_additional_sales"][0]["fully_reconciled"]
    assert "军师祭咖啡" not in str(result)
    # Idempotence is vital because /api/tasks applies this projection twice.
    assert public_stock_report(result)==result
