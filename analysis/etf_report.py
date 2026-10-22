# -*- coding: utf-8 -*-
"""A distinct ETF investment-research route; never run company F10 prompts on a fund."""
from __future__ import annotations

import json
import time
from typing import Any

from analysis.etf_data import collect_etf_data
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
    "commodity_market": "黄金或行业产品价格：趋势与利润传导",
    "industry_cycle": "行业周期：底部、扩张期还是顶部",
    "holdings": "前十大重仓：究竟买了什么",
    "high_positions": "十大重仓股的股价位置与回撤风险",
    "earnings_cycle": "十大重仓股利润增速和景气变化",
    "valuation": "估值与安全边际",
    "fund_structure": "ETF结构、持仓集中度与成本",
    "market_price": "基金价格、溢折价与流动性",
}

_SYSTEM_RULES = """你是研究ETF的投资经理，写一篇真正能用于投资决策、自然、简明、有人味的中文分析。
不是上市公司的F10模板，不要写打分清单、套路句、空洞的两面话；
不要用“综上所述”“风险与机遇并存”“本质是在买”等套话。
要求观点来自可核实证据，但不是数据不足就机械给中性：
有数据的地方给出方向判断和因果；没有数据的驱动给出可证伪的条件情景。
不要暴露幕后工作、作者、数据调用或工作流程。

按以下研究顺序思考，再给结论：

【1. 宏观形势和流动性（必须认真写，不能略过）】
读取macro_market.observations中有日期的最新中国1Y LPR、M2增速、PMI、
中美10年名义国债收益率、美国10年TIPS实际收益率；
明确比较可比的上一期变化和时间，解释利率、信用、风险偏好和行业估值的传导。
名义利率不是实际利率，M2增加不是资金必然流入股票。
若指标缺失，仅就其他真实指标给出分析；不能笼统说“宏观数据全无”。
特别要区分“对黄金资产有利的利率下降”和“对黄金矿企利润是否有利”两个问题。

【2. 实物金价/商品价与产业链利润（必须结合观察值）】
如果是黄金股ETF，必须重点看gold_cny（人民币/克）与gold_usd（美元/盎司）
近1个月、3个月、1年变化，若有美国TIPS实际收益率需结合方向，
解释美元、实际利率、央行购金、地缘风险怎样影响黄金供需；
同时讨论矿企金价、单位采选成本、产量、资本开支和利润之间的关系：
高金价未必带来高增量利润，成本上升与A/H股估值会抵消。
“央行购金”只有储备有相邻观察才可说储备增减，不能凭情绪称当月持续增持。
如果gold_usd抓取失败而gold_cny成功，就用后者研判、披露计价口径；
如果都失败，不得伪造金价或编造金价方向。
对其他ETF根据macro_market.sector和key_drivers做不同产业链判断：
煤炭/有色/钢铁/化工看产品价格、库存、产能和利润价差；
银行看息差、信用成本、不良与LPR；半导体看去库存、订单、
制造产能利用率与资本开支；医药、消费、光伏、宽基分别分析自身周期。
通用PMI只能做宏观背景，不能代替细分行业的产量、价格或库存。

【3. 行业周期：底部看修复，顶部警惕拐点】
优先引用industry_cycle.earnings_breadth中的可比盈利覆盖权重、
增速改善/回落权重、正负增速权重，以及商品或产品价格3个月趋势。
必须写出对“盈利修复初期/扩张/高景气走弱/下行/分化”的实际判断、
导致这一判断的数字、何种证据会推翻它。
低价格、低PE、高股息≠周期底部；高PE≠周期顶部。
如果确有低利润改善、供给收缩、产品涨价证据，可以积极看多周期修复；
如果盈利处高位、增速放缓、产能过剩、产品价格下跌，应认真考虑看空。
但只看Q1和H1**累计口径**净利润同比增速差，不能说“二季度利润环比下降”，
两者并非独立季度的同比；区分增长速度变慢和绝对盈利下滑。

【4. 前十大披露重仓穿透】
逐一看 portfolio.holdings 中最多10只的代码、名称、净值权重、
股价52周区间位置和近一年回报、披露财报期间、净利润同比增长和增速变化、
估值位置（有则引用）；特别挑出高权重与业绩方向相反的股票。
不能只分析前5只然后笼统概括“前十”。
A股可以用财务指标；港股只能用返回的价格观察，港股财务缺失时不套A股数据。
对A/H重复持有要指出穿透后的同一公司风险，但不同法人主体不能未经核实
合并成同一家公司。解释单产业链相关性与集中风险。

【5. 价格-估值、流动性与最终结论】
区分矿企股票的一年价格高位和估值历史高分位。PE低可能是景气高利润；
将行业未来盈利的可持续性和估值结合，判断是低估还是周期陷阱。
ETF当前价格位置、IOPV、成交/份额、整体PE、费率未知时不编数字。
用最重要的可证实因果写2-5自然段投资结论；至少把“现在阶段、涨跌驱动、
未来哪些证据使看法改变”说清。
如果上下游价格/业绩信号反方向，明确权衡何者领先、何者滞后；
可以看多、看空、中性，但绝不因为有3项缺失就自动给中性。
全部观测均有source/as_of；不能把滞后的月度和季度数据当作今日事实。
绝对不能使用自身常识填补截至日期的实时金价、利率、机构动作或公司数据。

只返回一个JSON对象，无Markdown:
{
 "stance":"bullish|neutral|bearish",
 "thesis_summary":"2至5段自然中文，明确宏观、行情、产业盈利、持仓估值的逻辑与立场",
 "sections":{
   "macro_liquidity":"有日期的中美利率、实际利率、国内LPR/M2/PMI及影响；区分因果强弱",
   "commodity_market":"黄金国内外价格及其近1/3/12月走势与金矿利润传导；其他行业讲产品和供需",
   "industry_cycle":"明确判断行业处于什么周期阶段，引用盈利广度、产品价格和缺口",
   "holdings":"前十大持仓及权重，谁主导净值",
   "high_positions":"至少讨论前十大可核实的价格位置，高位/低位的权重与公司",
   "earnings_cycle":"前十大能够核实的利润同比增长和增速变动，说明报告期",
   "valuation":"前十估值分位与行业可持续利润辨析",
   "fund_structure":"同一产业链、A/H、费用和指数信息，只写查到的",
   "market_price":"ETF 52周区间、IOPV/流动性及观察时间"
 },
 "risk_notes":"最主要的2-4项明确风险",
 "invalidation_condition":"哪些具体后续数据会使判断方向反转"
}
必须包含所有sections，即使某一项无指标也应说明具体价格/盈利的传导和下个证据。
不允许一句“宏观环境复杂、需进一步观察”代替分析。"""


def _text(value: Any, limit: int = 6000) -> str:
    return str(value or "").strip()[:limit]


async def _write_etf_opinion(data: dict) -> dict:
    prompt = _SYSTEM_RULES + "\n\n实际取得的ETF与披露重仓数据（所有价格均注明时间/来源）:\n" + json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    try:
        parsed, _ = await call_json_ex(
            prompt, max_tokens=5800,
            repair_requirements="合法JSON对象；stance必须是bullish/neutral/bearish之一；"
                                "必须包含thesis_summary、sections九个字段、risk_notes和invalidation_condition。",
        )
    except Exception as exc:
        utils.logger.warning("[etf] LLM summary failed: %s", exc)
        parsed = None

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
    return {
        "stance": parsed["stance"],
        "thesis_summary": _text(parsed.get("thesis_summary")),
        "sections": {key: _text(sections.get(key), 2400) for key in _SECTION_NAMES},
        "risk_notes": _text(parsed.get("risk_notes"), 1600),
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
        "section_labels": _SECTION_LABELS,
        "generated_at": int(time.time()),
    })
