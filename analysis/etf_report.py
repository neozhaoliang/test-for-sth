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

_SYSTEM_RULES = """你是基金投资研究员。当前只研究紧随其后的基金名称、代码和它实际披露的重仓股，
不得复用之前研究过的ETF、公司、指数、商品或行业的研究段落。
语言自然、像专业投资者平实地讨论证据，不要说套话，更不能写数据接口字段名。
严禁把JSON键名写给读者（例如不要写“某字段未提供”），只解释业务含义。
基金名称决定投资策略的初步分类，真实披露的持仓决定具体暴露；没有核实的细分行业
不得仅凭联想列举，重仓股的个别行业不能未经权重测算代表全部基金。

一、宏观利率与流动性：只引用macro_market中确有数据和日期的中国LPR、M2、
PMI、中美国债收益率及美国10年TIPS实际利率，说明利率、估值与盈利的关系。
名义收益率与实际收益率不同，M2增速不是ETF资金流。无法核实就不编数字。
若某项数据缺失，把重点放在已有且相关的指标上，不要写长篇缺数据清单。

二、基金的真实经济驱动：先根据投资目标及前十大持仓辨认收益来源，说明
它们如何通过收入、成本、毛利、现金流、分红或估值传导到基金价格；
跨行业基金不得强行指定单一商品价格或单一行业周期。
仅讨论有持仓证据支持的行业，避免把无关公司的股票、业务和周期机械罗列。

三、可核查覆盖与缺失的区别：
先看holding_risk.price_observed_count、valuation_observed_count及实际披露重仓只数。
如果已核实0只，必须写“暂无可验证数据，无法判断高位重仓比例”，
**不得**写“高位股权重为0%”或“估值安全”；只有取得可比数据的样本里
计算出来的0才是实际观察值。部分覆盖只能表述部分持仓的现象，不得外推。
当港股价格数据均缺失时说明两路行情未取得近期有效日线，
并提出可操作的来源校验，但不要反复堆叠空字段或JSON键名。

三、盈利周期判断：根据industry_cycle和已披露重仓股业绩，判断盈利在
周期底部修复、扩张、高位回落、衰退或行业分化；低股价和低PE都不是底部的证据，
顶部与底部需要行业供需和可持续盈利交叉验证。
Q1与H1是两个不同的累计同比增速口径，同比增速变化不代表季度净利润环比变动。
如果没有足够盈利数据，对不同业务的机制提出可验证的条件，不得编造周期位置。

四、前十大持仓：系统页面已经用表格完整列出了股票名称、代码和权重，
文字分析不许再次逐只复述前十大名单或抄写全部权重！
只总结集中度、2至3个真正左右判断的重仓公司、估值与业绩分化，
并根据有日期的财报期和52周股价位置指出已经核实的风险或机会。
港股需使用港股利润表及估值数据；无法核实时不要套用A股F10口径。
如果只有部分公司可观察，说清样本覆盖，但不要大段复制缺失字段名。

五、基金管理方式与高位持仓风险（必须检查holding_risk）：
先阅读holding_risk.management_style.kind及其source。通过基金名称判断的管理方式
只是线索，必须提醒基金合同和指数编制规则仍需核实，不要把猜测写成已确认。
对可能的指数ETF，必须讨论其规则性调样、指数扩容或权重上涨是否可能让基金
在高估值热门股上被动承接；检验持仓52周价格位置、PE/PB历史分位以及
指数调样日期、基金份额变化。价格高位与估值高位不同，
季度持仓权重上升不必然代表主动买入或被动买入。
对可能的主动管理ETF，必须讨论经理是否可能为了短期业绩而追逐热门高估值股票，
核对连续季报持仓变化、买入时的估值和交易纪律；
没有交易记录和动机证据，不能断言经理为拉抬净值故意买泡沫股。
管理方式未核实则说明两类风险分别需要核实哪类合同及调仓证据。
不能仅凭价格52周高位就宣称股票是泡沫，也不能因为估值缺失就不提示风险。

六、结论：用2-5段中文说明当前驱动、估值是否预支了利润、
最可能的利多/利空及使判断改变的证据。
允许看多、看空、中性，但不能因为缺几项信息自动给中性；
也不能因为没有证据就装作已经核实。
所有数字保留来源日期，不补写未提供的公司派息率、产业库存、商品价格、汇率。
不要输出模型如何查询数据等工作过程。

请返回JSON对象，字段为：
{
 "stance":"bullish|neutral|bearish",
 "thesis_summary":"2至5段自然中文的投资逻辑",
 "sections":{
   "macro_liquidity":"真实宏观、利率与流动性环境及影响",
   "commodity_market":"当前基金实际的产业盈利或分红来源，不是默认某个商品",
   "industry_cycle":"根据真实基金持仓和证据讨论行业周期，不把不同产业混作一体",
   "holdings":"前十大持仓和权重集中度",
   "high_positions":"有数据的重仓股价格位置与潜在回撤",
   "earnings_cycle":"重仓股有披露的利润同比及其变化",
   "valuation":"现价、估值与可持续盈利",
   "fund_structure":"指数或主动管理方式及验证状态、被动接盘或经理追高风险、重仓集中、费用和税费（只写已核实）",
   "market_price":"基金自身的最近价格、溢折价、成交与时点"
 },
 "risk_notes":"主要且真实的风险",
 "invalidation_condition":"哪些未来数字将改变现在的判断"
}
保留所有sections键，不相关部分允许返回空字符串而不是硬凑话。
全篇不得无依据引入另一只ETF的研究主题，不得把示例当成事实。"""

_GOLD_RULES = """
此基金明确属于黄金产业股票ETF，才启用以下附加研究：
通过有日期的国内金价gold_cny与美元金价gold_usd观察1/3/12个月变化，
关注真实利率、美元、官方储备变化；区分矿企金价、单位现金成本、
产量、资本支出与股权估值。无法取得金价时不能说金价方向。
高金价并不保证矿企持续高利润或低估值。
"""

_DIVIDEND_RULES = """
这只基金明确跟踪跨行业高股息/红利策略，不是某个商品行业的ETF：
第一步按已披露前十大重仓的实际名称、代码和权重分析银行、能源、航运、
电信等行业（仅在该行业确实出现在当前持仓时讨论），不得套用固定板块清单。
核心是股息率与分红可持续性：派息率、利润及自由现金流覆盖、资本开支、
负债与偿债能力、景气高位后是否可能削减股息；
必须区分个股分红率、基金已派息和基金整体真实现金分配率。
高股息并不代表高实际回报，分母可能是高景气不可持续利润。
港股/QDII基金关注港币/人民币汇率、港股税务规则、海外利率、
QDII投资限制、费率及溢折价；未知的数值和税率不要编造。
多行业持仓需分别讨论利润周期，不得强行宣称全基金处于
单一行业底部或顶部。没有真实现金流及股息覆盖数据，就分析
可能导致派息减少的条件，并明确尚未量化。
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


async def _write_etf_opinion(data: dict) -> dict:
    profile = _profile_for_data(data)
    current = data.get("fund") or {}
    identity = f"本次唯一研究标的：{current.get('name', '')}（{current.get('code', '')}）。研究类别：{profile}。"
    prompt = (_SYSTEM_RULES + "\n" + identity + "\n" + _rules_for_profile(profile) +
              "\n\n当前ETF真实数据（全部价格必须核对日期和来源）:\n" +
              json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
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
                    _has_foreign_fund_material(parsed, profile)):
                utils.logger.warning("[etf] rejected other-fund thesis for %s",
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
        "section_labels": _section_labels_for(_profile_for_data(data)),
        "generated_at": int(time.time()),
    })
