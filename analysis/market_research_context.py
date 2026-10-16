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


def select_evergreen_tech_finance_excerpts(
    excerpts: Iterable, *, as_of=None, limit: int = 10
) -> list:
    """Preserve older stockholder-cost and dilution arguments for technology.

    The originals stay in the *private* retrieval pipeline. Only generic
    verification principles, never these source objects, reach synthesis.
    """
    cutoff = _day(as_of) or date.today()
    ranked = []
    for item in excerpts or []:
        dt = _day(getattr(item, "published_at", None))
        if dt is None or dt > cutoff or (cutoff - dt).days > 3650:
            continue
        content = (str(getattr(item, "title", "") or "") + " " +
                   str(getattr(item, "distilled", "") or ""))
        if not _TECH_FINANCING_RE.search(content):
            continue
        # Prefer finance/ownership claims over generic AI announcements.
        weight = sum(bool(re.search(token, content, re.I))
                     for token in (
                         r"股民|股东|稀释|融资", r"减持|套现",
                         r"股权激励|回购|增发|H股", r"估值|泡沫"
                     ))
        ranked.append((weight, dt.toordinal(), item))
    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in ranked[:max(0, limit)]]


# Investment principles are the *only* output of KOL retrieval. The user-facing
# report must never contain original excerpts, examples, account identities or links.
# An extracted principle is tested against the issuer and market data; it is
# not treated as proof that a buy/sell signal has fired.
_TECH_FINANCING_RE = re.compile(
    r"科技|融资|再融资|定增|增发|H股|股权激励|二级市场|散户|股民|"
    r"管理层减持|控股股东减持|套现|资本市场|AI|算力|光模块|"
    r"高估值|市盈率|估值泡沫|研发费用|技术护城河|"
    r"订单|资本开支|债务融资|技术路线",
    re.I,
)

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
    tech = archetype == "technology"
    matched = set()
    for item in excerpts or []:
        dt = _day(getattr(item, "published_at", None))
        if dt is None or dt > cutoff:
            continue
        age = (cutoff - dt).days
        content = str(getattr(item, "title", "") or "") + " " + str(
            getattr(item, "distilled", "") or "")
        evergreen_tech = bool(tech and _TECH_FINANCING_RE.search(content))
        # A market observation expires quickly, but an investor's capital
        # allocation method should survive past the last 120 days. General
        # methods may be referenced for ten years, NEVER as current facts.
        if age > 120 and (not evergreen_tech or age > 3650):
            continue
        if not _STYLE_WORDS.search(content) and not evergreen_tech:
            continue
        if tech and _TECH_FINANCING_RE.search(content):
            matched.add("tech_capital_discipline")
        for key, pattern, _mechanism, _verification in _PRINCIPLE_RULES:
            if pattern.search(content):
                matched.add(key)
    if not applicable:
        matched.discard("dividend_sustainability")
    principles = [
        {"id": key, "principle": mechanism, "company_check": verification}
        for key, _pattern, mechanism, verification in _PRINCIPLE_RULES
        if key in matched
    ]
    if "tech_capital_discipline" in matched:
        principles.insert(0, {
            "id": "tech_capital_discipline",
            "principle": (
                "科技公司融资并非无成本利好：原股东承担新增股份稀释、"
                "高估值回落以及研发/扩产投入回报不足的风险；"
                "管理层或控股股东出售持股的所得不进入公司，"
                "公司回购用于员工股权激励也不同于注销回购。"
            ),
            "company_check": (
                "核验本公司已完成的减持数量与均价、增发/H股新股数、"
                "回购是已执行还是方案、现金流与项目资本回报；"
                "以当时复权价格和估值分位判断是否高位承接。"
            ),
        })
    return principles[:limit]


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
    sector_rotation = (market_context or {}).get("sector_rotation") or {}
    rotation_observed = (sector_rotation.get("status") in ("observed", "partial")
                         or bool(sector_rotation.get("benchmarks")))
    stock = (market_context or {}).get("stock") or {}
    stock_date = _day(stock.get("latest_date"))
    stock_20d = stock.get("d20_pct") if stock_date and (
        0 <= (cutoff - stock_date).days <= 7
    ) else None
    # The retail-facing stock-position context uses YTD high-to-latest CLOSE
    # rather than a transient 20-day return. Keep the short-term return only
    # for research comparisons, not for the dashboard's prominent stock line.
    ytd_day = _day(stock.get("ytd_close_as_of"))
    ytd_usable = bool(
        stock_date and ytd_day == stock_date
        and 0 <= (cutoff - ytd_day).days <= 7
        and stock.get("ytd_close_basis") == "unadjusted_daily_close"
        and isinstance(stock.get("ytd_close_drawdown_pct"), (int, float))
        and isinstance(stock.get("ytd_close_high"), (int, float))
        and isinstance(stock.get("ytd_close_latest"), (int, float))
        and 0 < stock["ytd_close_latest"] <= stock["ytd_close_high"]
    )
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
        "status": "observed" if rotation_observed or regime != "unknown" or evidence or principles else "insufficient_data",
        "sector_rotation": sector_rotation,
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
        "stock_ytd_close_high": stock.get("ytd_close_high") if ytd_usable else None,
        "stock_ytd_close_high_date": stock.get("ytd_close_high_date") if ytd_usable else None,
        "stock_ytd_latest_close": stock.get("ytd_close_latest") if ytd_usable else None,
        "stock_ytd_close_as_of": stock.get("ytd_close_as_of") if ytd_usable else None,
        "stock_ytd_close_drawdown_pct": (
            stock.get("ytd_close_drawdown_pct") if ytd_usable else None
        ),
        "stock_ytd_close_basis": "unadjusted_daily_close" if ytd_usable else None,
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
        "行业风格：优先使用多个行业同口径的20个交易日数据动态研判，"
        "禁止无条件把红利与科技作为市场主角。",
        f"截至 {ctx.get('as_of')}，传统红利/成长指数辅助对照={ctx.get('style_regime')}。",
        f"本公司是否有高股息特征={ctx.get('dividend_exposure_relevant')}；高股息只是可选因素，不是默认市场主线。",
        "近期已经公开的市场事件："
    ]
    if not events:
        rows.append("  暂无带日期且仍在有效期内的事件记录；不能据此声称没有资金轮动。")
    for e in events:
        rows.append(
            f"  {e['date']}：{e['headline']}；统计={e['facts']}；"
            f"来源={e['source_url']}；解读={e['interpretation']}"
        )
    rotation = ctx.get("sector_rotation") or {}
    if rotation.get("status") in ("observed", "partial") or rotation.get("benchmarks"):
        coverage = rotation.get("coverage", 0)
        rows.append(
            f"市场已获得同期行情：行业板块{coverage}/{rotation.get('universe_size')}类、"
            f"风格/宽基指数{len(rotation.get('benchmarks') or [])}个。"
            "这是实际观察范围而不是全市场覆盖。行业不足6个时不准据此概括A股主导行业；"
            "仅可描述已经显示的指数及行业强弱，不得编造未覆盖行业的涨跌。"
        )
        for item in (rotation.get("sectors") or []) + (rotation.get("benchmarks") or []):
            rows.append(
                f"  {item.get('name')}:近20日{item.get('d20_pct')}%，"
                f"此前20日{item.get('previous_d20_pct')}%，"
                f"截至{item.get('latest_date')}。"
            )
    else:
        rows.append(
            "没有得到可核验的同期行业或宽基指数；如有独立的红利/成长指数观测，"
            "仅把它作为局部比较，不因行业供应商暂时失败就完全回避已证实的涨跌。"
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
        "当期哪些行业真正领涨/领跌？有色、医药、消费、金融、科技等应按实际涨跌取舍，"
        "再讨论盈利改善、股息可持续性与可能的风险偏好变化；"
        "哪些已经有数据支持，哪些只是知识库作者提出的机制？",
        "禁止因为红利限购直接喊见顶；也禁止遗漏有可靠日期、与本行业相关的重大风格变化。"
    ])
    return "\n".join(rows)
