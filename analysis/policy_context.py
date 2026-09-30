# -*- coding: utf-8 -*-
"""
Current policy / geopolitical event clues.

This is deliberately a *clue* layer, not a primary-fact layer.  Company news and global
financial flashes are third-party reporting.  We only keep recent items that match the
company's disclosed industry/exposure topics and always preserve source/time/url.

Downstream prompts must not convert a headline into an established fact without primary
corroboration.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import akshare as ak


logger = logging.getLogger("MediaCrawler")

_TIMEOUT_S = 20
_MAX_ITEMS = 14
_COMPANY_NEWS_DAYS = 14
_GLOBAL_NEWS_DAYS = 4

_COMMON_TOPICS: Dict[str, Tuple[str, ...]] = {
    "rates_liquidity": (
        "美联储", "加息", "降息", "利率", "国债收益率", "LPR", "降准", "流动性",
    ),
    "trade_sanctions": (
        "关税", "制裁", "出口管制", "贸易摩擦", "实体清单", "反倾销", "反补贴",
    ),
    "war_shipping": (
        "战争", "冲突", "中东", "红海", "乌克兰", "俄罗斯", "以色列", "伊朗",
        "航运安全", "海峡",
    ),
    "fx": ("人民币", "美元", "汇率", "升值", "贬值"),
}

_ARCHETYPE_TOPICS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "technology": {
        "technology_policy": (
            "芯片", "半导体", "人工智能", "AI", "算力", "国产替代", "科技政策",
            "设备禁运", "先进制程", "供应链安全",
        ),
    },
    "cyclical": {
        "commodity_supply": (
            "OPEC", "产量", "减产", "增产", "矿山", "供应中断", "库存", "商品价格",
            "能源安全",
        ),
    },
    "financial": {
        "financial_policy": (
            "资本充足率", "存款利率", "贷款利率", "房地产政策", "金融监管",
            "保险资金", "净息差", "不良贷款",
        ),
    },
    "consumer_brand": {
        "consumption_policy": (
            "消费刺激", "以旧换新", "消费补贴", "免税", "食品安全", "消费税",
        ),
    },
    "stable_yield": {
        "utility_policy": (
            "电价", "煤价", "容量电价", "电力市场", "绿电", "新能源消纳", "公用事业",
            "天然气价格", "水价",
        ),
    },
}

_ROUTE_TOPICS: Dict[str, Tuple[str, ...]] = {
    "coal": ("煤炭", "动力煤", "焦煤", "煤价", "煤矿"),
    "crude_oil": ("原油", "油价", "OPEC", "石油", "天然气"),
    "gold_precious": ("黄金", "金价", "贵金属", "央行购金"),
    "steel": ("钢铁", "铁矿石", "螺纹钢", "钢材"),
    "lithium": ("碳酸锂", "锂价", "锂矿", "盐湖"),
    "copper": ("铜价", "铜矿", "电解铜"),
    "aluminum": ("铝价", "电解铝", "氧化铝"),
    "zinc": ("锌价", "锌矿"),
    "nickel": ("镍价", "镍矿"),
    "silicon": ("工业硅", "多晶硅", "硅料"),
    "glass": ("玻璃",),
    "soda_ash": ("纯碱",),
    "hog": ("生猪", "猪价", "能繁母猪"),
}


def _bare_code(stock_code: str) -> str:
    return re.sub(r"\D", "", stock_code)[-6:]


def _clean(text: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).replace("\u3000", " ").strip()


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    text = str(value).strip()
    # pandas/Timestamp string, ISO, or Eastmoney datetime.
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except ValueError:
            pass
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            )
        except ValueError:
            return None
    return None


def build_policy_topics(
    *,
    archetype: str,
    industry: str = "",
    commodity_route: Optional[str] = None,
    overseas_revenue_pct: Optional[float] = None,
) -> Dict[str, Tuple[str, ...]]:
    topics: Dict[str, Tuple[str, ...]] = {}

    # Current rate levels are already supplied by macro_rates.  News about rate/liquidity
    # changes is kept here only for businesses whose economics are especially rate-sensitive,
    # avoiding generic macro headlines flooding every company's policy block.
    if archetype in {"financial", "stable_yield"}:
        topics["rates_liquidity"] = _COMMON_TOPICS["rates_liquidity"]
    topics.update(_ARCHETYPE_TOPICS.get(archetype, {}))

    overseas = False
    try:
        overseas = overseas_revenue_pct is not None and float(overseas_revenue_pct) >= 5.0
    except (TypeError, ValueError):
        overseas = False

    if overseas or archetype == "technology":
        topics["trade_sanctions"] = _COMMON_TOPICS["trade_sanctions"]
        topics["fx"] = _COMMON_TOPICS["fx"]
    if overseas or archetype == "cyclical":
        topics["war_shipping"] = _COMMON_TOPICS["war_shipping"]

    if commodity_route and commodity_route in _ROUTE_TOPICS:
        topics[f"commodity_{commodity_route}"] = _ROUTE_TOPICS[commodity_route]

    if industry:
        # Exact industry name is a useful company-specific relevance term, but we do not
        # create a topic from tiny generic words.
        cleaned = re.sub(r"[ⅡⅢI]+$", "", industry).strip()
        if len(cleaned) >= 2:
            topics["industry"] = (cleaned,)

    return topics


def _match_topics(text: str, topics: Dict[str, Tuple[str, ...]]) -> Tuple[List[str], List[str]]:
    matched_topics: List[str] = []
    matched_terms: List[str] = []
    lower = text.lower()
    for topic, terms in topics.items():
        hits = [term for term in terms if term.lower() in lower]
        if hits:
            matched_topics.append(topic)
            matched_terms.extend(hits)
    return matched_topics, list(dict.fromkeys(matched_terms))


def _normalize_company_news(df, now: datetime) -> List[Dict]:
    if df is None or df.empty:
        return []
    cutoff = now - timedelta(days=_COMPANY_NEWS_DAYS)
    rows: List[Dict] = []
    for _, row in df.iterrows():
        published = _parse_dt(row.get("发布时间"))
        if published and published < cutoff:
            continue
        title = _clean(row.get("新闻标题"))
        summary = _clean(row.get("新闻内容"))
        if not title and not summary:
            continue
        rows.append(
            {
                "source_type": "company_news",
                "title": title,
                "summary": summary[:500],
                "published_at": published.isoformat() if published else str(row.get("发布时间") or ""),
                "media": _clean(row.get("文章来源")) or "东方财富聚合",
                "url": _clean(row.get("新闻链接")) or None,
            }
        )
    return rows


def _normalize_global_news(df, now: datetime) -> List[Dict]:
    if df is None or df.empty:
        return []
    cutoff = now - timedelta(days=_GLOBAL_NEWS_DAYS)
    rows: List[Dict] = []
    for _, row in df.iterrows():
        published = _parse_dt(row.get("发布时间"))
        if published and published < cutoff:
            continue
        title = _clean(row.get("标题"))
        summary = _clean(row.get("摘要"))
        if not title and not summary:
            continue
        rows.append(
            {
                "source_type": "global_flash",
                "title": title,
                "summary": summary[:500],
                "published_at": published.isoformat() if published else str(row.get("发布时间") or ""),
                "media": "东方财富全球财经快讯",
                "url": _clean(row.get("链接")) or None,
            }
        )
    return rows


def filter_policy_event_clues(
    rows: Sequence[Dict],
    *,
    topics: Dict[str, Tuple[str, ...]],
    company_terms: Iterable[str] = (),
    limit: int = _MAX_ITEMS,
) -> List[Dict]:
    company_terms = tuple(x for x in (str(v).strip() for v in company_terms) if len(x) >= 2)
    scored: List[Tuple[float, Dict]] = []
    for row in rows:
        text = f"{row.get('title') or ''} {row.get('summary') or ''}"
        matched_topics, matched_terms = _match_topics(text, topics)
        company_hits = [term for term in company_terms if term in text]
        # This module is specifically policy/geopolitical context.  Merely naming the company
        # is not enough; ordinary earnings/product news belongs elsewhere.
        if not matched_topics:
            continue

        score = len(matched_topics) * 2 + len(matched_terms) * 0.25 + len(company_hits) * 3
        item = dict(row)
        item["matched_topics"] = matched_topics
        item["matched_terms"] = list(dict.fromkeys(matched_terms + company_hits))
        item["relevance_score"] = round(score, 2)
        scored.append((score, item))

    scored.sort(
        key=lambda pair: (
            pair[0],
            pair[1].get("published_at") or "",
        ),
        reverse=True,
    )
    return [item for _, item in scored[:limit]]


async def get_policy_event_context(
    stock_code: str,
    stock_name: str,
    *,
    archetype: str,
    industry: str = "",
    commodity_route: Optional[str] = None,
    overseas_revenue_pct: Optional[float] = None,
) -> Optional[Dict]:
    code6 = _bare_code(stock_code)
    topics = build_policy_topics(
        archetype=archetype,
        industry=industry,
        commodity_route=commodity_route,
        overseas_revenue_pct=overseas_revenue_pct,
    )
    now = datetime.now(ZoneInfo("Asia/Shanghai"))

    async def company_news():
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(ak.stock_news_em, symbol=code6),
                timeout=_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning(f"[policy_context] company news {code6} failed: {e}")
            return None

    async def global_news():
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(ak.stock_info_global_em),
                timeout=_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning(f"[policy_context] global flash failed: {e}")
            return None

    company_df, global_df = await asyncio.gather(company_news(), global_news())
    rows = _normalize_company_news(company_df, now) + _normalize_global_news(global_df, now)
    clues = filter_policy_event_clues(
        rows,
        topics=topics,
        company_terms=(stock_name, code6, industry),
    )
    if not clues:
        return None

    newest = max((x.get("published_at") or "") for x in clues)
    return {
        "as_of": newest or now.isoformat(),
        "topics": {k: list(v) for k, v in topics.items()},
        "events": clues,
        "source_tier": "B",
        "kind": "reported_event",
        "note": (
            "本块是财经媒体/快讯的近期事件线索，不是一手事实。"
            "任何方向性结论都必须再结合公司实际暴露、官方公告/政策文件或可核验市场数据；"
            "新闻标题本身不能单独成为看多/看空证据。"
        ),
    }
