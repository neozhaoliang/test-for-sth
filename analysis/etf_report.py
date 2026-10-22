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
    "holdings", "high_positions", "earnings_cycle",
    "valuation", "fund_structure", "market_price",
)
_SECTION_LABELS = {
    "holdings": "基金究竟买了什么",
    "high_positions": "重仓股的高位与回撤风险",
    "earnings_cycle": "行业盈利与所处周期",
    "valuation": "成分股估值与安全边际",
    "fund_structure": "ETF结构与持有成本",
    "market_price": "价格位置、溢折价与交易",
}

_SYSTEM_RULES = """你是一位可以做出独立判断的投资经理，给普通读者分析ETF。
这不是上市公司，绝对不能套用个股模板。写自然中文，直接说明哪些事实会影响未来收益，
可以鲜明看多或看空，不要打分投票，不要使用“从几个维度来看”“综上所述”
“风险和机会并存”“本质是在买”等空话。不要暴露知识库作者、社交账号或工作流程。

仅能使用提供的JSON观测数据，不能凭记忆补出ETF跟踪指数、最新成份、
管理费、分红收益率、PE、PB、行业净利增速、机构持仓、资金净申购等。
必须回答：基金持有哪些已披露的大权重股票？前五/前十大集中度如何？
高位权重多不多？那些股票是业绩变好还是盈利回落？
相关行业可能位于周期什么阶段，当前可证实的指标有哪些，哪些证据还没有？
买ETF是分散风险还是同时重仓同一条产业链？当头部公司估值回落时会怎样？
最重要的是，区分“股价靠近一年高点”和“市盈率偏贵”，前者绝不是后者的证明；
如果没有可信的成分股PE/PB或指数估值分位，明确说没有，不允许补猜估值数值。
ETF季报重仓股不是今天的全部指数成分，也不是申购赎回篮子；
不能把未披露的90%股票当作已经核实。
不能从单日成交额、份额或成交量认定大资金买入/净申购；
IOPV盘中估值不等于收盘净值；溢折价必须结合行情时点解释。
如股票价格观察覆盖权重只有部分，应只基于已观察权重描述，不能外推全基金。
如果标为非A股股票ETF或混合资产产品，个股盈利样本为空，
不要用A股估值结论去分析海外/债券/黄金。
所有段落应紧凑、像人依据实际数字解释投资判断，不要写调查过程。
只返回JSON对象:
{
 "stance":"bullish|neutral|bearish",
 "thesis_summary":"2-5个自然段，最重要的真实投资判断",
 "sections":{
   "holdings":"重仓和集中度...",
   "high_positions":"哪些重仓股高位，权重与日期...",
   "earnings_cycle":"真实观察到的利润指标和行业周期假设...",
   "valuation":"估值是否真的有证据支持...",
   "fund_structure":"ETF结构、分散度、跟踪指数/费率信息是否缺失...",
   "market_price":"ETF价格区间、IOPV溢折价、流动性..."
 },
 "risk_notes":"最主要的具体风险",
 "invalidation_condition":"什么新数据或变化会改变这一判断"
}
结论不能因为缺乏数据就一律中性；但对没有证据的事项不得作方向性断言。
不要输出MD围栏，不要捏造具体数字。"""


def _text(value: Any, limit: int = 6000) -> str:
    return str(value or "").strip()[:limit]


async def _write_etf_opinion(data: dict) -> dict:
    prompt = _SYSTEM_RULES + "\n\n实际取得的ETF与披露重仓数据（所有价格均注明时间/来源）:\n" + json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    try:
        parsed, _ = await call_json_ex(
            prompt, max_tokens=3900,
            repair_requirements="合法JSON对象；stance必须是bullish/neutral/bearish之一；"
                                "必须包含thesis_summary、sections六个字段、risk_notes和invalidation_condition。",
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
