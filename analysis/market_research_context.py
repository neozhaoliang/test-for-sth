"""Join dated A-share style observations, news events and KOL mechanisms.

Never equate a commentator's claim with a price tick; never infer that a
subscription limit is proof of a top or an issuer-level fund inflow.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "market_events"
_STYLE_WORDS = re.compile(
    r"红利|高股息|高低切|风格切换|风格迁移|科技.{0,12}红利|"
    r"公募.{0,12}(调仓|蓝筹|红利)|(抱团|科技).{0,15}估值|蓝筹.{0,12}资金|"
    r"股息率|低波|ETF.{0,12}(资金|净流)|险资|避险资金",
    re.I,
)


def _day(raw):
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except (TypeError, ValueError):
        return None


def _events(cutoff: date) -> list:
    out = []
    for path in sorted(_DATA_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("events") if isinstance(payload, dict) else []
        except (OSError, UnicodeError, ValueError):
            continue
        for e in rows or []:
            if not isinstance(e, dict):
                continue
            dt = _day(e.get("published_at"))
            if not dt or dt > cutoff or not e.get("source_url"):
                continue
            if not e["source_url"].startswith(("https://", "http://")):
                continue
            max_age = min(90, max(1, int(e.get("max_age_days") or 21)))
            if (cutoff - dt).days > max_age:
                continue
            out.append({
                "id": e.get("id"),
                "date": dt.isoformat(),
                "headline": e.get("headline"),
                "facts": e.get("facts") or {},
                "source_title": e.get("source_title"),
                "source_url": e.get("source_url"),
                "interpretation": e.get("interpretation"),
                "event_type": e.get("event_type"),
            })
    return out[:12]


def select_recent_style_excerpts(excerpts: Iterable, *, as_of=None, limit=8) -> list:
    """Protect recent style/KOL source records from an LLM relevance false negative.

    This returns original KnowledgeExcerpt objects, retaining real author/date/URL.
    It does not endorse their interpretation or infer an issuer-level trade.
    """
    cutoff = _day(as_of) or date.today()
    ranked = []
    for item in excerpts or []:
        text = str(getattr(item, "title", "") or "") + " " + str(
            getattr(item, "distilled", "") or ""
        )
        when = _day(getattr(item, "published_at", None))
        if not when or when > cutoff or (cutoff - when).days > 120:
            continue
        if not _STYLE_WORDS.search(text):
            continue
        source = str(getattr(item, "source", "") or "")
        author = str(getattr(item, "author", "") or "")
        preferred = any(n in (source + author) for n in (
            "老木匠", "双木林叔", "军师祭咖啡", "陈chensir", "chensir"
        ))
        score = (3 if preferred else 0) + (
            3 if "红利" in text or "高低切" in text else 0
        )
        ranked.append((score, when.toordinal(), item))
    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in ranked[:max(0, limit)]]


# Investment principles are the *only* output of KOL retrieval. The user-facing
# report must never contain original excerpts, examples, account identities or links.
# An extracted principle is tested against the issuer and market data; it is
# not treated as proof that a buy/sell signal has fired.
_PRINCIPLE_RULES = (
    (
        "dividend_sustainability",
        re.compile(r"高股息|红利|股息|分红|派息", re.I),
        "先确认派息由可持续现金流覆盖，股价上涨使当前股息率下降时要重新衡量赔率。",
        "核验本公司历年已实施现金分红、维持性资本开支与现金流覆盖",
    ),
    (
        "style_and_crowding",
        re.compile(r"高低切|风格切换|风格迁移|红利.{0,12}(资金|ETF|限购)|ETF.{0,12}红利|公募.{0,12}(调仓|抱团)|科技.{0,15}(退潮|红利|调仓)", re.I),
        "价格上涨可能来自市场资金偏好转移而非盈利改善；区分风格驱动与公司价值。",
        "核验红利与成长指数最近20个交易日相对收益及公司股价是否跟涨",
    ),
    (
        "position_and_trading",
        re.compile(r"波段|高位|拥挤|回撤|阶段涨幅|逢高减仓|过热", re.I),
        "短期涨幅较大时关注交易拥挤与回撤，不把个别股票的涨跌阈值照搬到其他公司。",
        "核验公司当前52周股价位置、近期涨幅、成交和机构持仓变化",
    ),
    (
        "valuation_discipline",
        re.compile(r"估值|高估|低估|收益率|股息率|长期回报|成长股", re.I),
        "长期回报取决于买入价格与可持续盈利/分红，而不是热门叙事或过去涨幅。",
        "核验公司中周期收益、当前PB/PE和分红收益率",
    ),
)


def _synthesized_principles(excerpts: Iterable, cutoff: date, *,
                            archetype: str, industry: str, limit: int = 4) -> list:
    """Extract topic-level reusable checks, NOT excerpts or ticker examples.

    Knowledge is a source of analytical *questions*, never investor-facing
    quotes, identities, original titles, URLs, or unsupported conclusions.
    """
    applicable = archetype == "stable_yield" or any(
        x in str(industry) for x in ("电力", "水务", "煤炭", "银行", "燃气", "公用事业")
    )
    matched = set()
    for item in excerpts or []:
        dt = _day(getattr(item, "published_at", None))
        if dt is None or dt > cutoff or (cutoff - dt).days > 120:
            continue
        content = str(getattr(item, "title", "") or "") + " " + str(
            getattr(item, "distilled", "") or "")
        if not _STYLE_WORDS.search(content):
            continue
        for key, pattern, _mechanism, _verification in _PRINCIPLE_RULES:
            if pattern.search(content):
                matched.add(key)
    if not applicable:
        matched.discard("dividend_sustainability")
    return [
        {"id": key, "principle": mechanism, "company_check": verification}
        for key, _pattern, mechanism, verification in _PRINCIPLE_RULES
        if key in matched
    ][:limit]


def build_style_investment_context(
    *, as_of: Optional[str], archetype: str, industry: str,
    macro_context: Optional[Mapping] = None, knowledge_excerpts=None,
    market_context: Optional[Mapping] = None,
) -> dict:
    cutoff = _day(as_of) or date.today()
    macro = macro_context or {}
    style = macro.get("a_share_style") or {}
    regime = style.get("regime") if style.get("status") == "observed" else "unknown"
    evidence = _events(cutoff)
    principles = _synthesized_principles(
        knowledge_excerpts or [], cutoff, archetype=archetype, industry=industry
    )
    stock = (market_context or {}).get("stock") or {}
    stock_date = _day(stock.get("latest_date"))
    stock_20d = stock.get("d20_pct") if stock_date and (
        0 <= (cutoff - stock_date).days <= 7
    ) else None
    belongs = archetype == "stable_yield" or any(
        term in str(industry or "") for term in
        ("电力", "水务", "煤炭", "高速", "运营商", "公用事业")
    )
    tone = "unknown"
    if regime == "dividend_leading":
        tone = "relative_dividend_strength"
    elif regime == "growth_leading":
        tone = "relative_growth_strength"
    warnings = [
        "红利指数跑赢反映风格相对收益，不直接证明本公司的经营改善或现金分红可持续。",
        "基金限购不能直接推断市场见顶，需结合申赎、成交和价格相对位置验证拥挤度。",
        "来源于历史讨论的投资原则只是分析问题，必须用当前个股数据交叉检查。",
    ]
    return {
        "status": "observed" if regime != "unknown" or evidence or principles else "insufficient_data",
        "as_of": cutoff.isoformat(),
        "dividend_exposure_relevant": belongs,
        "style_regime": regime,
        "dividend_minus_growth_ytd_pp": style.get("dividend_minus_growth_ytd_pp"),
        "dividend_index_return_pct": style.get("dividend_index_return_pct"),
        "growth_index_mean_return_pct": style.get("growth_index_mean_return_pct"),
        "style_window": style.get("style_window") or "year to date",
        "style_observed_as_of": style.get("as_of"),
        "style_label": tone,
        "dated_market_events": evidence,
        "investment_principles": principles,
        "stock_20d_pct": stock_20d,
        "stock_20d_as_of": stock_date.isoformat() if stock_20d is not None else None,
        "interpretation": (
            "红利行情对本股的边际资金吸引力可能有帮助，但必须与派息能力、"
            "煤价/电价、公司相对涨幅及股价位置同时看；风格资金驱动与企业内在价值分开。"
            if belongs else
            "风格轮动会影响估值赔率，但应按该公司的实际风格暴露和价格趋势判断。"
        ),
        "warnings": warnings,
    }


def style_investment_prompt_block(ctx: Optional[Mapping]) -> str:
    if not ctx:
        return "市场风格和知识库尚未获得可核对的日期数据，不可编造近期资金动向。"
    events = ctx.get("dated_market_events") or []
    principles = ctx.get("investment_principles") or []
    rows = [
        "当前市场风格与投资者研究框架（这是投资判断的必要输入，不可略写为宏观资料缺失）：",
        f"截至 {ctx.get('as_of')}，风格={ctx.get('style_regime')}，"
        f"红利相对成长在{ctx.get('style_window')}内的涨跌差="
        f"{ctx.get('dividend_minus_growth_ytd_pp')}个百分点；"
        f"指标日期={ctx.get('style_observed_as_of')}。",
        f"公司是否与红利风格相关={ctx.get('dividend_exposure_relevant')}。",
        "近期已经公开的市场事件："
    ]
    if not events:
        rows.append("  暂无带日期且仍在有效期内的事件记录；不能据此声称没有资金轮动。")
    for e in events:
        rows.append(
            f"  {e['date']}：{e['headline']}；统计={e['facts']}；"
            f"来源={e['source_url']}；解读={e['interpretation']}"
        )
    rows.append("从已整理的投资经验中提炼出的分析原则（不含作者、原文或跨公司案例）：")
    if not principles:
        rows.append("  没有适用于本次公司与时间范围的额外原则；只依据当前资料分析。")
    for item in principles:
        rows.append(
            f"  判断框架：{item['principle']}；"
            f"本公司验证：{item['company_check']}"
        )
    rows.append(
        f"本公司近20个交易日涨跌幅={ctx.get('stock_20d_pct')}%；"
        f"价格观测截至={ctx.get('stock_20d_as_of')}。"
    )
    rows.append(
        "严格保密：公开摘要与任何维度分析都不得透露知识库作者、昵称、"
        "原帖标题、原文、URL、与当前公司无竞争/业务关系的其他股票或个案，"
        "也不得写'某投资者认为'等知识来源引述。"
        "仅用当前公司的可核对数据回应上述检查，并以自己的话给出判断。"
    )
    rows.extend([
        "必须在'市场情绪'、'A股资金结构'和'股价位置'三方面说明："
        "当期究竟是盈利改善、股息可持续性，还是资金高低切/估值扩张驱动？"
        "哪些已经有数据支持，哪些只是知识库作者提出的机制？",
        "禁止因为红利限购直接喊见顶；也禁止完全不讨论已核验的近期限购和风格切换。"
    ])
    return "\n".join(rows)
