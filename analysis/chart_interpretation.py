"""Explain what report charts mean using actual issuer data and vetted KB ideas.

Chart readings are optional, short editorial comments, NOT additional facts.
They are generated after the twelve-dimensional research is available and
placed immediately below the chart they interpret. Avoid hard-coded stock
stories: changing the research corpus must change the explanation candidates.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)
_KEYS = frozenset(("profitability", "valuation_history", "market_breadth",
                   "dimension_scores"))
_PRIVATE = re.compile(
    r"https?://|(?:SH|SZ|BJ)\d{6}|(?<!\d)\d{6}(?!\d)|"
    r"买股票的老木匠|军师祭咖啡|双木林叔|陈chensir|"
    r"原帖|博主|某投资者|雪球账号|据投资者|资料显示",
    re.I,
)
# The actual numbers already appear in chart labels. Suppressing new numeric
# assertions eliminates one important source of incorrect narrative figures.
_DIGIT = re.compile(r"\d")


def _number(value: Any):
    if value is None or isinstance(value, bool):
        return None
    try:
        from math import isfinite
        value = float(value)
        return round(value, 3) if isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def chart_evidence(inputs: Any, dimension_scores=None) -> dict:
    """Small source-backed context for charts that actually have observations."""
    result = {}
    trends = (getattr(inputs, "profitability_trend", None) or {}).get("periods") or []
    trends = [
        {key: row.get(key) for key in (
            "period", "roe_pct", "gross_margin_pct", "net_margin_pct",
            "debt_ratio_pct",
        )}
        for row in trends
        if isinstance(row, dict) and row.get("period")
    ]
    if len(trends) >= 2:
        result["profitability"] = {
            "periods": trends[-6:],
            "note": "季报ROE可能是累计期间值，不可直接年化。",
        }

    vh = getattr(inputs, "valuation_history", None) or {}
    if len(vh.get("history_monthly") or []) >= 6:
        result["valuation_history"] = {
            "as_of": vh.get("as_of"),
            "pe_now": vh.get("current_pe_ttm"),
            "pb_now": vh.get("current_pb"),
            "pe_percentiles": (vh.get("pe_percentiles") or [])[:3],
            "pb_percentiles": (vh.get("pb_percentiles") or [])[:3],
            "notes": (vh.get("notes") or [])[:3],
            "monthly_recent": (vh.get("history_monthly") or [])[-3:],
        }

    industry = getattr(inputs, "industry_comparison", None) or {}
    sentiment = getattr(inputs, "sentiment", None) or {}
    if industry or sentiment:
        result["market_breadth"] = {
            "industry": {key: industry.get(key) for key in (
                "industry_name", "up_count", "down_count", "flat_count",
                "as_of", "source",
            )} if isinstance(industry, dict) else {},
            "sentiment": {key: sentiment.get(key) for key in (
                "bullish_pct", "bearish_pct", "sample_size", "posts_collected",
                "as_of",
            )} if isinstance(sentiment, dict) else {},
            "note": "帖子数量和涨跌家数不能证明机构买卖方向。",
        }
    if dimension_scores and len(dimension_scores) >= 6:
        result["dimension_scores"] = {
            "scores": [
                {key: entry.get(key) for key in ("dimension", "score", "note")}
                for entry in dimension_scores[:12]
                if isinstance(entry, dict)
            ],
            "note": "描述性主观倾向，不做平均或投票推导投资结论。",
        }
    return result


def validate_chart_interpretations(
    candidate: Any, available: Mapping[str, Any],
    *, archetype: str = "", private_excerpts=(),
) -> dict[str, str]:
    """Fail closed on ungrounded dates, copied sources and low-PE hype."""
    if not isinstance(candidate, dict):
        return {}
    source_texts = [
        re.sub(r"\s+", "", str(getattr(e, "distilled", "") or ""))
        for e in private_excerpts or []
    ]
    source_names = {
        str(getattr(e, attr, "") or "").strip()
        for e in private_excerpts or []
        for attr in ("author", "source")
    }
    source_names = {name for name in source_names if len(name) >= 3}
    result = {}
    for key in _KEYS:
        if key not in available:
            continue
        text = str(candidate.get(key) or "").strip()
        if not (25 <= len(text) <= 280):
            continue
        if (_PRIVATE.search(text) or _DIGIT.search(text)
                or any(name in text for name in source_names)):
            continue
        compact = re.sub(r"\s+", "", text)
        # Never copy raw distilled prose or an identifiable phrase from it.
        if any(
            source[i:i+35] in compact
            for source in source_texts if len(source) >= 35
            for i in range(0, len(source)-34, 18)
        ):
            continue
        if key == "valuation_history" and archetype == "cyclical":
            pe_rows = available[key].get("pe_percentiles") or []
            low = any(
                _number(row.get("percentile")) is not None
                and _number(row.get("percentile")) <= 25
                for row in pe_rows if isinstance(row, dict)
            )
            # Interpretive outcome is up to the model, but 'low PE means
            # unequivocally undervalued' is inconsistent with a cyclical
            # peak-profit denominator without normalized earnings.
            unsupported_bargain = bool(re.search(
                r"(?:因此|所以|说明|证明|属于|表明).{0,12}"
                r"(?:明显低估|十分便宜|非常便宜|充足安全边际)|"
                r"(?:低PE|市盈率低).{0,12}(?:就是低估|等于便宜|可以放心买)",
                text,
            ))
            if low and unsupported_bargain:
                continue
        result[key] = text
    return result


async def build_chart_interpretations(
    inputs: Any, analyses: Mapping[str, str],
    methods=(), dimension_scores=None,
) -> dict[str, str]:
    """One bounded LLM pass after research; no generic fixed company thesis."""
    available = chart_evidence(inputs, dimension_scores)
    if not available:
        return {}
    profile = getattr(inputs, "research_profile", None)
    archetype = str(getattr(profile, "archetype", "") or "")
    style = (getattr(inputs, "valuation_model_result", None) or {}).get(
        "style_context"
    ) or {}
    reasons = [
        {
            "idea": str(row.get("mechanism") or "")[:230],
            "limitation": str(row.get("alternative") or "")[:170],
        }
        for row in (style.get("causal_drivers") or [])[:3]
        if isinstance(row, dict)
    ]
    method_list = [
        {
            "idea": str(row.get("insight") or "")[:210],
            "use_when": str(row.get("use_when") or "")[:150],
            "possible_mistake": str(row.get("possible_mistake") or "")[:150],
        }
        for row in list(methods or [])[:4] if isinstance(row, dict)
    ]
    # The research model has already vetted private mechanisms. The chart
    # interpreter receives only anonymized causal abstractions, never posts.
    macro = (getattr(inputs, "valuation_model_result", None) or {}).get(
        "macro_context"
    ) or {}
    macro_observations = [
        {
            "metric": r.get("metric"), "value": r.get("value"),
            "as_of": r.get("as_of"), "usable": r.get("usable"),
        }
        for r in (macro.get("observations") or [])[:20]
        if isinstance(r, dict) and r.get("usable")
    ]
    related = {
        "profitability": ("fundamentals", "cycle_position", "risk_quality"),
        "valuation_history": ("price_position", "cycle_position", "growth_elasticity"),
        "market_breadth": ("retail_sentiment", "a_share_structure"),
        "dimension_scores": ("fundamentals", "policy_geopolitics", "risk_quality"),
    }
    research = {
        key: {dim: str(analyses.get(dim) or "")[:750] for dim in related[key]
              if analyses.get(dim)}
        for key in available
    }
    prompt = (
        "你正在编辑股票研究网页。每张图表下方要有一小段真正的解释，"
        "而不是重复一遍图上数字或把指标涨跌机械翻译成好坏。"
        "用普通投资者读得懂的中文说明：这张图里的变化可能来自什么，"
        "对公司的下一期利润、现金、估值或这只股票的价格预期有什么影响。"
        "投资讨论知识库里的方法只帮助提出因果解释；不要照抄方法、"
        "不要模仿作者，不能把它写成已经被证实的市场资金流向。"
        "核对盈利所处周期、市场风格与利率/实际收益率的传导，"
        "但仅当数据与本公司有关时才提，不要硬凑宏观话题。"
        "同时考虑反例与缺失的关键证据。"
        "关于估值图：历史百分位描述价格与当时利润分母的比值，"
        "不能孤立推出未来便宜或昂贵；根据公司周期和实际盈利组成解释。"
        "关于市场图：同期股价涨跌只支持风格相对表现，不能单凭它"
        "确认基金由某板块卖出后买进另一板块。"
        "一项指标没有数据、分析中也没有明确结论，就省略该项。"
        "不准引入输入材料之外的事件、人物、行业价格和利率方向。"
        "一句话不要塞许多术语，每项一段，约三到五句即可。"
        "图表本身已有数字，所以输出解释一律不写阿拉伯数字、"
        "日期、百分比、精确预测价，以免重新抄错。"
        "严禁外部作者、原帖、其他股票、账号或来源链接。"
        "只输出JSON对象：可用键为"
        + ", ".join(sorted(available)) + "。每项是字符串，不要新增其它键。\n"
        + "股票：" + str(getattr(inputs, "stock_name", "") or "") +
        "；行业：" + str(getattr(profile, "industry", "") or "") +
        "；经营类型：" + archetype + "\n"
        + "真实图表数据：" + json.dumps(available, ensure_ascii=False, default=str) + "\n"
        + "对应详细分析：" + json.dumps(research, ensure_ascii=False) + "\n"
        + "已匿名化的知识库机制候选（可能不适用）：" +
          json.dumps(method_list, ensure_ascii=False) + "\n"
        + "市场风格的知识库机制候选（尚非资金流事实）：" +
          json.dumps(reasons, ensure_ascii=False) + "\n"
        + "同期可用宏观事实（不可外推其它央行）：" +
          json.dumps(macro_observations, ensure_ascii=False)
    )
    try:
        from backtest.llm_client import call_json_ex
        generated, _ = await call_json_ex(
            prompt, max_tokens=2300,
            repair_requirements=(
                "返回JSON对象，键只允许profitability/valuation_history/"
                "market_breadth/dimension_scores；值为中文自然段。"
                "只解释数据对公司和股价的含义，不可新编数字、"
                "引入未核实加息或称价格涨跌证明实际基金调仓。"
            ),
        )
    except Exception as exc:
        logger.warning("Chart interpretation unavailable: %s", exc)
        return {}
    return validate_chart_interpretations(
        generated, available, archetype=archetype,
        private_excerpts=getattr(inputs, "knowledge_excerpts", None) or [],
    )
