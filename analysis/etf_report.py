# -*- coding: utf-8 -*-
"""A distinct ETF investment-research route; never run company F10 prompts on a fund."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from analysis.etf_data import collect_etf_data
from analysis.etf_macro import sector_for_etf
from backtest.llm_client import call_json_ex
from analysis.json_safety import finite_json_numbers
from analysis.research_context import ResearchMode, ResearchRequest
from tools.utils import utils

_SECTION_NAMES = (
    "macro_liquidity", "commodity_market", "industry_cycle",
    "holdings", "high_positions", "earnings_cycle",
    "valuation", "fund_structure", "market_price",
)
_SECTION_LABELS = {
    "macro_liquidity": "宏观、利率与流动性环境",
    "commodity_market": "产品价格及盈利驱动",
    "industry_cycle": "行业周期：底部、扩张期还是顶部",
    "holdings": "前十大重仓：究竟买了什么",
    "high_positions": "十大重仓股的股价位置与回撤风险",
    "earnings_cycle": "十大重仓股利润增速和景气变化",
    "valuation": "估值与安全边际",
    "fund_structure": "ETF结构、持仓集中度与成本",
    "market_price": "基金价格、溢折价与流动性",
}

_SYSTEM_RULES = """请为当前ETF写一份有判断力、短而自然的中文投资研究，不要把研究方法、提示词、
数据字段名或缺项清单写成读者看到的分析。读者已在页面顶部看到了：
基金全名、价格、溢价率、成交额、前十大重仓股，以及可获取的实际分红率和可供分配利润；
不要再次列这些表格，不要写“基金代码是”“来源未提供报价日期”等数据抓取工作细节。

你必须区分三件事：
1. 已观察的事实（标日期，关键数字才引用）；
2. 事实背后的盈利、现金分红、流动性或估值传导；
3. 仍无法确认的假设，只在实质影响结论时简短提一次。

根据真实基金的投资行业选择相关分析，不要从旧基金复制主题：
宏观利率（中国LPR/国债与美国10年TIPS实际利率）怎样改变估值或景气；
行业产品价格/盈利和资本开支处在周期底部、扩张还是顶部；
前十大里哪些有已核实的股价高位、PE/PB高分位、利润加速或回落；
指数定期纳入热门高位股的被动接盘风险，或经证实属于主动ETF时经理追高风险。
缺乏核实的交易记录时不能推断基金经理动机，也不能把季度权重变化写成加仓事实。
没有足够证据的方向直接留空，不要堆砌“需要核实、尚未提供、不能判断”。

段落严格分工，不准换个说法重复同一条证据：
- high_positions：只分析个股的股价位置、PE/PB估值分位及价格回撤，不再谈派息覆盖或ETF融资；
- earnings_cycle：只分析净利润增速、分红覆盖和未来派息可持续性，不再谈股票价格位置；
- valuation：只有新增独立的基金层面估值或投资回报测算时才填写，不能重述high_positions，
  也不能重复顶部已展示的历史基金现金分红率；无新增事实就留空；
- commodity_market 与 industry_cycle：跨行业高股息ETF若已在earnings_cycle里论述产业
  盈利分化，以上两个字段留空。除非存在真实、互不重复的产品价格与周期证据；
- risk_notes：仅放此前所有章节都没有提过的独立风险；否则留空；
- thesis_summary：只给投资态度及最关键因果判断，不要再次照抄后面章节所有数字。
每个上市公司同一季度同一指标只出现一次；融资余额和成交额在上方专属段落已有图表，
在上述所有章节不再重复引用融资余额或成交额的数值。

筹码与交易拥挤度必须参考trading_crowding中的有日期数据：
ETF只分析该基金证券自身融资余额、融资变化和成交额相对历史的放大倍数；
不将前十大股票融资余额加总，也不把ETF场内成交额当成资金净申购。
要交代融资上升、价格位置与换手放大是否同时发生；
融资余额高或单日放量不能独立推出高位抱团，缺失的数据不要解释成零风险。
相关判断融入当前风险或高位重仓段落，避免复制所有后台字段名。

各段必须回答**所以现在值得买、持有还是回避，为什么**，而不是指导用户如何研究。
不要写“若为指数产品...；若为主动产品...；需核实基金合同...”
这类把决策树原样念出来的话。研究过程不是投资结论。
盈利/宏观不可核实就集中写已证实的价格和盈利机制，绝不编造数据。
如果某一话题没有实质证据，sections对应字段可以是空字符串。
每段优先1段、2-5句话，整个报告减少重复套话。

只返回JSON对象：
{
 "stance":"bullish|neutral|bearish",
 "thesis_summary":"2-3段最重要的投资判断及核心原因",
 "sections":{
   "macro_liquidity":"仅有实证意义的利率与流动性影响；否则空串",
   "commodity_market":"实际持仓行业的产品、需求、成本或分红来源；否则空串",
   "industry_cycle":"证据支持的行业盈利阶段与拐点；否则空串",
   "holdings":"",
   "high_positions":"仅对确实取得价格与估值数据的重仓判断；否则空串",
   "earnings_cycle":"已核实的业绩趋势如何影响可持续利润；否则空串",
   "valuation":"将估值与可持续利润结合；否则空串",
   "fund_structure":"",
   "market_price":""
 },
 "risk_notes":"1-2个最重要且有具体传导的风险，不复述数据缺口",
 "invalidation_condition":"能使当前判断发生改变的可观察变化"
}
页面已经以指标卡展示价格和流动性，以表格展示持仓和高位风险。
holdings、fund_structure、market_price三栏永远留空，
不要把表格内容与基金合同核对流程再写一遍。"""


_GOLD_RULES = """
此基金明确属于黄金产业股票ETF，才启用以下附加研究：
通过有日期的国内金价gold_cny与美元金价gold_usd观察1/3/12个月变化，
关注真实利率、美元、官方储备变化；区分矿企金价、单位现金成本、
产量、资本支出与股权估值。无法取得金价时不能说金价方向。
高金价并不保证矿企持续高利润或低估值。
"""

_DIVIDEND_RULES = """
这是跨行业高股息策略，核心问题不是短线金属价格，而是
已披露持仓的盈利能否持续覆盖派息、自由现金流和资本开支。
如distribution里有正式基金可供分配利润、实际近12个月现金分红及价格分红率，
则区分基金层面的可分配利润、实际现金分红率与成分股当前股息率；
对这三者的差异作经营性解释，绝不把历史基金派息等同于未来收益承诺，
也不把可供分配利润写成已经收到的现金股息。
真正分析航运、能源、银行等**确有持仓**行业的不同利润周期，
重点识别景气高位利润退潮、股息率陷阱、派息下降和估值风险。
港股/QDII的汇率、税费仅在有证据时提及，不需要重复基金合同核验要求。
表格已经列出全部重仓代码和权重，正文不许逐一照抄。
"""

_SECTOR_RULES = """
根据实际sector、key_drivers与重仓股行业判断产业价格和利润：
例如周期性行业核对产品价格/价差、库存、供给和资本开支，
成长行业核对订单、利润率、产能利用率或研发变现，
必须与真实取得的数据相互印证。只有确证的单行业ETF才能判断
该行业是否在周期底部或顶部；其他ETF分析各自行业分化。
"""

_MIXED_RULES = """
基金可能是宽基、跨行业、跨境或混合主题：
必须先识别本基金真实的投资策略及前十大持仓，按可观察的行业分布、
盈利与估值讨论。不能默认它是某一资源、制造或成长产业，
也不应花大段篇幅列举与当前持仓无关的细分行业。
"""

_UNRELATED_FUND_RULES = re.compile(
    r"gold_cny|gold_usd|本基金不是黄金股ETF|不是黄金股ETF|"
    r"黄金股ETF永赢|黄金产业股票ETF|"
    r"未提供.{0,30}金价|黄金矿企的?利润", re.I,
)


def _profile_for_data(data: dict) -> str:
    # Derive from the CURRENT fund identity, not from cached other fund data.
    name = str((data.get("fund") or {}).get("name") or "")
    return sector_for_etf(name)["sector"]


def _rules_for_profile(profile: str) -> str:
    if profile == "黄金产业股票":
        return _GOLD_RULES
    if profile == "跨行业高股息":
        return _DIVIDEND_RULES
    if profile in {"其他行业或混合主题", "宽基市场"}:
        return _MIXED_RULES
    return _SECTOR_RULES


def _section_labels_for(profile: str) -> dict:
    labels = dict(_SECTION_LABELS)
    if profile == "黄金产业股票":
        labels["commodity_market"] = "黄金价格与矿企盈利传导"
    elif profile == "跨行业高股息":
        labels["commodity_market"] = "股息来源、现金流与分红可持续性"
        labels["industry_cycle"] = "不同重仓行业的盈利周期与派息风险"
        labels["valuation"] = "股息率、估值与长期股东回报"
    elif profile in {"宽基市场", "其他行业或混合主题"}:
        labels["commodity_market"] = "基金收益来源与持仓行业的盈利驱动"
    return labels


_PROMPT_ECHO = re.compile(
    r"(?:latest_shares|gold_cny|gold_usd|macro_market|industry_cycle|"
    r"portfolio_stats|price_observed_weight_pct|sample_count|"
    r"as_of|IOPV)\s*(?:为|是|[:：=])\s*(?:null|None|空|0\b)|"
    r"(?:若|如果)(?:为|是)(?:指数|主动管理|主动|被动)(?:产品|ETF|基金)?|"
    r"基金名称提示可能为|"
    r"(?:请|必须|不得)(?:核实|获取|查询)(?:基金合同|指数编制|数据源)",
    re.I,
)


def _has_prompt_echo(result: dict) -> bool:
    """Reject literal schema echoes and conditional research checklists.

    This is a publication-quality gate, not a semantic judgement of the
    investment stance. Allow normal remarks such as 'IOPV尚未披露' or a
    single missing observation, but reject repeated nulls and instructions.
    """
    pieces = [str(result.get("thesis_summary") or ""),
              str(result.get("risk_notes") or ""),
              str(result.get("invalidation_condition") or "")]
    sections = result.get("sections")
    if isinstance(sections, dict):
        pieces.extend(str(value or "") for key, value in sections.items()
                      if key not in ("holdings", "fund_structure", "market_price"))
    passage = " ".join(pieces)
    if _PROMPT_ECHO.search(passage):
        return True
    missing_terms = ("未提供", "未取得", "尚未核实", "不能判断", "无法判断",
                     "尚未确认", "没有数据", "缺少数据")
    return sum(passage.count(term) for term in missing_terms) >= 5


def _has_foreign_fund_material(result: dict, profile: str) -> bool:
    if profile == "黄金产业股票":
        return False
    summary = str(result.get("thesis_summary") or "")
    sections = result.get("sections") or {}
    if isinstance(sections, dict):
        summary += " ".join(str(value or "") for value in sections.values())
    return bool(_UNRELATED_FUND_RULES.search(summary))


def _text(value: Any, limit: int = 6000) -> str:
    return str(value or "").strip()[:limit]


def _strip_crowding_sentences(text: str) -> str:
    """The separate ETF crowding panel already owns financing/trading facts."""
    sentences = re.split(r"(?<=[。！？；])", text)
    return "".join(
        sentence for sentence in sentences
        if not re.search(r"融资|成交额|净申购|换手率|交易拥挤|融资盘", sentence)
    ).strip()


def _compact_etf_sections(
    sections: dict, profile: str, *, has_crowding: bool = False,
) -> dict:
    """Prevent overlapping narrative blocks, independent of LLM compliance."""
    result = {
        key: (_text(sections.get(key), 2400) if key not in
              ("holdings", "fund_structure", "market_price") else "")
        for key in _SECTION_NAMES
    }
    if has_crowding:
        for key in ("commodity_market", "industry_cycle", "high_positions",
                    "earnings_cycle", "valuation"):
            result[key] = _strip_crowding_sentences(result[key])
    if profile == "跨行业高股息":
        # Keep the richer quantitative price/valuation and profit/payout
        # narratives; a second generic valuation or cycle explanation tends
        # to repeat the same constituent figures and conclusions.
        if result["high_positions"]:
            result["valuation"] = ""
        if result["earnings_cycle"]:
            result["industry_cycle"] = ""
            result["commodity_market"] = ""
    return result


async def _write_etf_opinion(data: dict) -> dict:
    profile = _profile_for_data(data)
    current = data.get("fund") or {}
    identity = f"本次唯一研究标的：{current.get('name', '')}（{current.get('code', '')}）。研究类别：{profile}。"
    # Raw limitation arrays and API-status explanations are for the UI,
    # not investment prose: including them caused the LLM to parrot them.
    evidence = {k:v for k,v in data.items()
                if k not in ("limitations", "scope", "collected_on")}
    prompt = (_SYSTEM_RULES + "\n" + identity + "\n" + _rules_for_profile(profile) +
              "\n\n当前已取得的投资证据（勿照抄JSON字段名）:\n" +
              json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    parsed = None
    for attempt in range(2):
        try:
            request = prompt
            if attempt:
                request += ("\n上一稿错误地把其他基金的投资主题移植过来。"
                            "请严格围绕当前基金名称及重仓，完全重写，不得照搬上一稿。")
            parsed, _ = await call_json_ex(
                request, max_tokens=5800,
                repair_requirements="合法JSON对象；stance必须是bullish/neutral/bearish之一；"
                                    "必须包含thesis_summary、sections九个字段、risk_notes和invalidation_condition。",
            )
            if (isinstance(parsed, dict) and
                    (_has_foreign_fund_material(parsed, profile) or
                     _has_prompt_echo(parsed))):
                utils.logger.warning("[etf] rejected off-topic or prompt-echo text for %s",
                                     current.get("code", "unknown"))
                parsed = None
                continue
            break
        except Exception as exc:
            utils.logger.warning("[etf] LLM summary failed: %s", exc)
            parsed = None
            break

    if not isinstance(parsed, dict) or parsed.get("stance") not in (
        "bullish", "bearish", "neutral"
    ):
        return {
            "stance": "",
            "thesis_summary": "已取得基金行情及已披露的重仓股数据，但投资判断生成失败；下方保留可核实的原始指标，没有自动猜测看多或看空。",
            "sections": {},
            "risk_notes": "",
            "invalidation_condition": "",
            "generation_status": "unavailable",
        }
    sections = parsed.get("sections")
    if not isinstance(sections, dict):
        sections = {}
    clean_sections = _compact_etf_sections(
        sections, profile, has_crowding=bool(data.get('trading_crowding'))
    )
    repeated_risk = (profile == "跨行业高股息" and
                     (clean_sections["high_positions"] or
                      clean_sections["earnings_cycle"]))
    return {
        "stance": parsed["stance"],
        "thesis_summary": _text(parsed.get("thesis_summary")),
        "sections": clean_sections,
        "risk_notes": ("" if repeated_risk else
                       _text(parsed.get("risk_notes"), 1600)),
        "invalidation_condition": _text(parsed.get("invalidation_condition"), 1600),
        "generation_status": "complete",
    }


async def generate_etf_report(spot: dict, request: ResearchRequest) -> dict:
    if request.mode != ResearchMode.LIVE:
        raise NotImplementedError(
            "ETF历史分析尚未启用：季度重仓股的实际披露日期及当时成分权重"
            "尚未通过point-in-time校验，不能拿今天持仓冒充过去。"
        )
    if request.save_snapshot:
        raise NotImplementedError("ETF快照存储尚未实现；请取消勾选“保存标准研究快照”。")
    raw = await collect_etf_data(spot)
    data = finite_json_numbers(raw)
    summary = await _write_etf_opinion(data)
    return finite_json_numbers({
        "asset_type": "etf",
        "stock_code": spot["stock_code"],
        "stock_name": spot["name"],
        "research_mode": "live",
        "as_of": data["collected_on"],
        "etf_data": data,
        "summary": summary,
        "section_labels": _section_labels_for(_profile_for_data(data)),
        "generated_at": int(time.time()),
    })
