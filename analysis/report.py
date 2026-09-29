# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
综合分析报告生成: 候选高可信度用户 (历史) + 实时抓取最新发帖 + 实时行情
+ 同花顺 F10 结构性事实 + 估值 + 雪球个股 -> LLM 结构化摘要。
"""

import asyncio
import re
import time
from typing import Dict, List, Optional

from analysis.a_share_structure import get_a_share_structure
from analysis.candidates import find_candidates
from analysis.commodity import get_cycle_commodity_signal, get_rmb_trend_signal
from analysis.freight import get_container_freight_signal
from analysis.fundamentals import get_ths_fundamentals
from analysis.filing_calendar import get_financial_filing_calendar
from analysis.industry import get_industry_comparison
from analysis.margin import get_margin_signal
from analysis.management_capital import build_management_capital_record
from analysis.macro_rates import get_macro_rate_context
from analysis.market_context import get_market_context
from analysis.profitability import get_profitability_trend
from analysis.primary_sources import get_cninfo_primary_evidence
from analysis.point_in_time_financials import (
    get_point_in_time_financials,
    to_historical_fundamentals,
    to_historical_profitability,
)
from analysis.point_in_time_ownership import get_point_in_time_ownership
from analysis.point_in_time_shareholder import get_point_in_time_shareholder_trend
from analysis.point_in_time_capital_returns import build_point_in_time_capital_returns
from analysis.policy_context import get_policy_event_context
from analysis.realtime_price import get_historical_quote, get_realtime_quote, get_stock_name
from analysis.rd_team import get_rd_team_composition
from analysis.valuation_history import get_valuation_history
from analysis.evidence import ResearchQuality, build_evidence_ledger, evaluate_research_quality
from analysis.research_profile import ResearchProfile, classify_research_profile
from analysis.report_contract import _PROMPT_VERSION
from analysis.report_inputs import AnalysisInputs
from analysis.report_validation import validate_report
from analysis.research_context import ResearchMode, ResearchRequest, assert_request_supported
from analysis.snapshot_store import save_report_snapshot

from analysis.report_blocks import _build_dividend_chart
from analysis.report_synthesis import _generate_summary
from analysis.report_social import (
    collect_historical_candidate_context,
    collect_live_social_context,
    filter_relevant_knowledge,
    load_knowledge_excerpts,
)
from analysis.shareholder import get_buyback_history, get_dividend_history, get_shareholder_count_trend
from model.m_analysis import AnalysisReport, CandidateOpinion, KnowledgeExcerpt
from tools.utils import utils

def _resolve_stock_name(stock_code: str, candidate_scores) -> str:
    for user in candidate_scores:
        for stock in user.by_stock:
            if stock.stock_code == stock_code and stock.stock_name:
                return stock.stock_name
    return ""


_RD_TEAM_INDUSTRY_RE = re.compile(
    r"半导体|电子|通信|计算机|软件|互联网|光学|光电子|电池|光伏|风电|"
    r"电网设备|自动化|机器人|机械设备|通用设备|专用设备|汽车零部件|"
    r"国防军工|航空|航天|医疗器械|生物制品|化学制药|医疗服务"
)


def _should_fetch_rd_team(fundamentals: Optional[Dict]) -> bool:
    facts = (fundamentals or {}).get("facts") or {}
    industry = str(facts.get("sw_industry") or "")
    rd_intensity = facts.get("rd_intensity_pct")
    try:
        rd_intensity = float(rd_intensity) if rd_intensity is not None else None
    except (TypeError, ValueError):
        rd_intensity = None
    return bool(_RD_TEAM_INDUSTRY_RE.search(industry)) or (
        rd_intensity is not None and rd_intensity >= 2.0
    )


async def generate_report(
    stock_code: str,
    request: Optional[ResearchRequest] = None,
) -> AnalysisReport:
    request = request or ResearchRequest(stock_code=stock_code)
    if request.stock_code != stock_code:
        raise ValueError(
            f"request.stock_code={request.stock_code} does not match stock_code={stock_code}"
        )
    assert_request_supported(request)

    candidate_scores = find_candidates(
        stock_code,
        as_of=request.as_of
        if request.mode == ResearchMode.HISTORICAL
        else None,
    )

    stock_name = _resolve_stock_name(stock_code, candidate_scores)
    quote = (
        await get_historical_quote(stock_code, request.as_of)
        if request.mode == ResearchMode.HISTORICAL
        else await get_realtime_quote(stock_code)
    )
    if not stock_name:
        stock_name = await get_stock_name(stock_code) or ""

    historical_mode = request.mode == ResearchMode.HISTORICAL
    if historical_mode:
        pit_financials_task = get_point_in_time_financials(stock_code, request.as_of)
        ownership_task = get_point_in_time_ownership(stock_code, request.as_of)
        shareholder_task = get_point_in_time_shareholder_trend(stock_code, request.as_of)
        dividend_task = asyncio.sleep(0, result=[])
        buyback_task = asyncio.sleep(0, result=[])
        fundamentals_task = asyncio.sleep(0, result=None)
        profitability_task = asyncio.sleep(0, result=None)
        live_a_share_structure_task = asyncio.sleep(0, result=None)
    else:
        pit_financials_task = asyncio.sleep(0, result=None)
        ownership_task = asyncio.sleep(0, result=None)
        shareholder_task = get_shareholder_count_trend(stock_code)
        dividend_task = get_dividend_history(stock_code)
        buyback_task = get_buyback_history(stock_code)
        fundamentals_task = get_ths_fundamentals(stock_code)
        profitability_task = get_profitability_trend(stock_code)
        live_a_share_structure_task = get_a_share_structure(stock_code)

    (
        knowledge_excerpts,
        shareholder_trend,
        dividend_history,
        buyback_history,
        profitability_trend,
        fundamentals,
        market_context,
        margin_signal,
        primary_evidence,
        a_share_structure,
        valuation_history,
        macro_rates,
        filing_calendar,
        point_in_time_financials,
        point_in_time_ownership,
    ) = await asyncio.gather(
        load_knowledge_excerpts(
            as_of=request.as_of
            if request.mode == ResearchMode.HISTORICAL
            else None
        ),
        shareholder_task,
        dividend_task,
        buyback_task,
        profitability_task,
        fundamentals_task,
        get_market_context(stock_code, as_of=request.as_of),
        get_margin_signal(stock_code, as_of=request.as_of),
        get_cninfo_primary_evidence(stock_code, as_of=request.as_of),
        live_a_share_structure_task,
        get_valuation_history(stock_code, as_of=request.as_of),
        get_macro_rate_context(as_of=request.as_of),
        get_financial_filing_calendar(stock_code, as_of=request.as_of),
        pit_financials_task,
        ownership_task,
    )

    if historical_mode:
        fundamentals = to_historical_fundamentals(point_in_time_financials)
        profitability_trend = to_historical_profitability(point_in_time_financials)
        a_share_structure = point_in_time_ownership
        dividend_history, buyback_history = build_point_in_time_capital_returns(
            primary_evidence
        )

    rd_team: Optional[Dict] = None
    if historical_mode:
        rd_team = None
    elif _should_fetch_rd_team(fundamentals):
        rd_team = await get_rd_team_composition(stock_code)
    else:
        utils.logger.info(
            f"[analysis.report] {stock_code} 非高研发/科技先进制造画像，"
            "跳过年报PDF研发人员表抓取"
        )

    # 行业反查的起点是 F10 公司概要页里的申万行业名，所以必须等 fundamentals 回来。
    # 这一步不再有按行业板块的逐次网络请求，只是本地匹配，放在 gather 之后不拖慢。
    industry_comparison = await get_industry_comparison(
        stock_code, (fundamentals or {}).get("facts", {}).get("sw_industry")
    )

    # 铜价信号按行业关键词判断，申万行业名同样可用——匹配不到同花顺板块时
    # industry_name 为空，但不能因此漏掉"有色金属"这类能直接识别的申万名。
    industry_name = None
    if industry_comparison:
        industry_name = industry_comparison.get("industry_name") or industry_comparison.get("sw_industry")
    preliminary_profile = classify_research_profile(
        fundamentals=fundamentals,
        evidence=[],
    )
    commodity_signal, rmb_signal = await asyncio.gather(
        get_cycle_commodity_signal(
            stock_code,
            industry_name,
            fundamentals,
            as_of=request.as_of if historical_mode else None,
        ),
        get_rmb_trend_signal(
            as_of=request.as_of if historical_mode else None,
        ),
    )
    # 运价与近期政策/地缘事件线索都依赖已经识别出的行业/暴露路径。
    # 新闻只作为低权重线索，不替代巨潮公告、官方政策或市场数据。
    facts_for_exposure = (fundamentals or {}).get("facts") or {}
    sw_industry = facts_for_exposure.get("sw_industry")
    policy_task = (
        asyncio.sleep(0, result=None)
        if historical_mode
        else get_policy_event_context(
            stock_code,
            stock_name,
            archetype=preliminary_profile.archetype,
            industry=str(sw_industry or industry_name or ""),
            commodity_route=(commodity_signal or {}).get("route"),
            overseas_revenue_pct=facts_for_exposure.get("overseas_revenue_pct"),
        )
    )
    freight_signal, policy_events = await asyncio.gather(
        get_container_freight_signal(
            stock_code,
            sw_industry,
            as_of=request.as_of if historical_mode else None,
        ),
        policy_task,
    )
    knowledge_excerpts = await filter_relevant_knowledge(
        knowledge_excerpts, stock_code, stock_name, industry_name
    )

    # 估值与同花顺 F10 同页解析，拆成独立维度是因为报告前端与 prompt 都要单列。
    # 必须拷一份再 pop: get_ths_fundamentals 返回的是带 6 小时缓存的同一个 dict，
    # 直接 pop 会把缓存里的估值维度永久删掉。
    valuation = None
    major_events: List[Dict] = []
    refinancing_history: List[Dict] = []
    executive_profile: Optional[Dict] = None
    governance_alerts: List[Dict] = []
    if fundamentals:
        fundamentals = dict(fundamentals)
        valuation = fundamentals.pop("valuation", None)
        major_events = fundamentals.pop("major_events", None) or []
        refinancing_history = fundamentals.pop("refinancing_history", None) or []
        executive_profile = fundamentals.pop("executive_profile", None)
        governance_alerts = fundamentals.pop("governance_alerts", None) or []

    management_capital = build_management_capital_record(
        dividend_history=dividend_history,
        buyback_history=buyback_history,
        refinancing_history=refinancing_history,
        primary_evidence=primary_evidence,
        executive_profile=executive_profile,
        profitability_trend=profitability_trend,
    )

    inputs = AnalysisInputs(
        stock_code=stock_code,
        stock_name=stock_name,
        quote=quote or {},
        knowledge_excerpts=knowledge_excerpts,
        industry_comparison=industry_comparison,
        shareholder_trend=shareholder_trend,
        dividend_history=dividend_history,
        buyback_history=buyback_history,
        profitability_trend=profitability_trend,
        commodity_signal=commodity_signal,
        rmb_signal=rmb_signal,
        macro_rates=macro_rates,
        policy_events=policy_events,
        fundamentals=fundamentals,
        valuation=valuation,
        valuation_history=valuation_history,
        rd_team=rd_team,
        major_events=major_events,
        refinancing_history=refinancing_history,
        executive_profile=executive_profile,
        governance_alerts=governance_alerts,
        market_context=market_context,
        freight_signal=freight_signal,
        margin_signal=margin_signal,
        primary_evidence=primary_evidence,
        a_share_structure=a_share_structure,
        management_capital=management_capital,
        filing_calendar=filing_calendar,
    )

    # Social context has a hard live/historical split. Historical mode never opens the
    # current Xueqiu page or reads today's latest posts.
    candidates = (
        collect_historical_candidate_context(
            inputs,
            candidate_scores,
            as_of=request.as_of,
        )
        if historical_mode
        else await collect_live_social_context(inputs, candidate_scores)
    )

    evidence = build_evidence_ledger(inputs, candidates)
    research_quality = evaluate_research_quality(evidence, today=request.as_of)
    research_profile = classify_research_profile(
        fundamentals=inputs.fundamentals,
        management_capital=inputs.management_capital,
        evidence=evidence,
    )
    inputs.research_profile = research_profile
    inputs.research_quality = research_quality
    summary, review = await _generate_summary(inputs, candidates, evidence)
    # 置信度同时受全局证据覆盖率、公司画像重点证据准备度限制，
    # 再扣除跨维度冲突/弱证据惩罚。
    if summary.confidence:
        summary.confidence = round(
            max(
                0.0,
                min(
                    summary.confidence,
                    research_quality.coverage,
                    research_profile.readiness,
                )
                - review.confidence_penalty,
            ),
            3,
        )

    report = AnalysisReport(
        stock_code=stock_code,
        stock_name=stock_name,
        research_mode=request.mode.value,
        as_of=str(request.as_of or ""),
        realtime_quote=quote,
        candidates=candidates,
        knowledge_excerpts=knowledge_excerpts,
        industry_comparison=industry_comparison,
        shareholder_trend=shareholder_trend,
        dividend_history=dividend_history,
        buyback_history=buyback_history,
        profitability_trend=profitability_trend,
        commodity_signal=commodity_signal,
        rmb_signal=rmb_signal,
        macro_rates=macro_rates,
        policy_events=policy_events,
        fundamentals=inputs.fundamentals,
        valuation=inputs.valuation,
        valuation_history=valuation_history,
        rd_team=rd_team,
        xueqiu_stock=inputs.xueqiu_stock,
        a_share_structure=a_share_structure,
        management_capital=management_capital,
        filing_calendar=filing_calendar,
        debate=inputs.debate,
        sentiment=inputs.sentiment,
        primary_evidence=primary_evidence,
        evidence=evidence,
        research_quality=research_quality,
        review=review,
        dividend_chart=_build_dividend_chart(
            dividend_history, buyback_history, valuation, quote
        ),
        market_context=market_context,
        freight_signal=freight_signal,
        margin_signal=margin_signal,
        summary=summary,
        prompt_version=_PROMPT_VERSION,
        generated_at=int(time.time()),
    )

    validation = validate_report(report)
    report.validation = validation.model_dump()
    if not validation.ok:
        messages = "；".join(x.message for x in validation.errors)
        raise RuntimeError(f"报告合同校验失败: {messages}")
    if validation.warnings:
        utils.logger.warning(
            "[analysis.report] %s 报告合同告警: %s",
            stock_code,
            "；".join(x.message for x in validation.warnings),
        )

    if request.save_snapshot:
        snapshot_path = save_report_snapshot(report, request)
        utils.logger.info(
            "[analysis.report] %s snapshot saved: %s",
            stock_code,
            snapshot_path,
        )
    return report
