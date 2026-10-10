"""Issuer operations source hierarchy and as-of privacy/reproducibility."""
from datetime import date

import pytest

from analysis.issuer_business import (
    normalize_exchange_qa, normalize_operating_filings, _topic_snippets,
    get_issuer_business_context, issuer_business_prompt_block,
)


def test_sse_qa_requires_issuer_reply_date_and_actual_answer():
    data=[
        {"股票代码":"600023","问题":"未来新增项目资本开支多少？",
         "回复":"项目投资将以正式公告为准","回复时间":"2026-10-09"},
        {"股票代码":"600023","问题":"公司是否签订10亿元订单？",
         "回复":"正在研究","回复时间":"2026-10-11"},
        {"股票代码":"600023","问题":"是否有新合同？",
         "回复":"","回复时间":"2026-10-08"},
        {"股票代码":"600023","问题":"年末煤价如何？",
         "回复":"暂无确切预测"},  # no response date => cannot use
        {"股票代码":"600020","问题":"订单披露","回复":"已签订",
         "回复时间":"2026-10-08"},
    ]
    ans=normalize_exchange_qa(data,"600023",as_of=date(2026,10,10),platform="上证e互动")
    assert len(ans)==1
    assert ans[0]["published_at"]=="2026-10-09"
    assert ans[0]["source_tier"]=="management_statement_unverified"
    assert ans[0]["verification_status"]=="not_a_filing_or_earned_order"


def test_operating_filing_title_is_not_order_value():
    records=[
        {"公告标题":"签订重大销售合同的公告","公告时间":"2026-10-08",
         "公告链接":"https://static.cninfo.com.cn/demo.pdf"},
        {"公告标题":"签订重大销售合同的公告","公告时间":"2026-10-08",
         "公告链接":"https://static.cninfo.com.cn/demo.pdf"},
        {"公告标题":"未来追加资本开支项目","公告时间":"2026-10-11",
         "公告链接":"https://static.cninfo.com.cn/later.pdf"},
        {"公告标题":"股东大会签到信息","公告时间":"2026-10-08",
         "公告链接":"https://static.cninfo.com.cn/sign.pdf"},
    ]
    rows=normalize_operating_filings(records,"600023",as_of=date(2026,10,10))
    assert len(rows)==1
    assert rows[0]["verification_status"]=="title_only_pending_document_review"
    assert "orders_and_contracts" in rows[0]["topics"]
    assert "contract_value" not in rows[0]


def test_original_filing_topics_remain_unstructured_excerpt():
    snippets=_topic_snippets(["某公司经营状况。项目总投资100亿元，预计2029年投产。", "其他内容"])
    assert snippets
    assert snippets[0]["page"]==1
    assert snippets[0]["topic"]=="capex"
    assert "project_capex_yuan" not in snippets[0]


@pytest.mark.asyncio
async def test_historical_mode_does_not_query_undated_qa_or_future_announcements(monkeypatch):
    async def fail(*a,**kw):
        raise AssertionError("historical mode must not call live issuer APIs")
    monkeypatch.setattr("analysis.issuer_business._fetch_exchange_answers",fail)
    monkeypatch.setattr("analysis.issuer_business._fetch_operating_metadata",fail)
    result=await get_issuer_business_context(
        "SH600023",as_of=date(2025,3,1),historical=True,
    )
    assert result["status"]=="historical_source_not_verified"
    assert not result["investor_qa"]


def test_prompt_labels_filing_title_and_qa_properly():
    ctx={
        "status":"available",
        "official_operating_filings":[
            {"published_at":"2026-10-09","title":"某重大合同公告",
             "url":"https://static.cninfo.com.cn/test.pdf"}],
        "pdf_context":[
            {"title":"2025年度报告","published_at":"2026-04-20",
             "url":"https://static.cninfo.com.cn/full.pdf",
             "topics":[{"page":18,"topic":"capex",
                       "excerpt":"项目建设资本支出计划待定"}]}],
        "investor_qa":[
            {"platform":"上证e互动","published_at":"2026-10-09",
             "question":"订单如何？","management_answer":"请以正式披露为准"}]
    }
    text=issuer_business_prompt_block(ctx)
    assert "仅标题" in text
    assert "第18页" in text
    assert "未经公告审计" in text
    assert "不能把提问者问题当公司事实" in text
    assert "订单签订/生效/交付/收入确认/回款" in text
