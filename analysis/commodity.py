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
周期行业商品锚 + 人民币汇率趋势。

周期信号必须与公司的真实产品/行业匹配：
- 煤炭不能拿铜价代替；
- 黄金不能拿铜价代替；
- 油气、钢铁、锂等分别使用对应期货代理；
- 找不到可靠映射时返回 None，而不是硬塞一个“资源品指数”。

期货价格只是行业景气代理，不等同于公司实际结算价/现货价。报告必须同时结合公司
销量、成本、库存、资本开支和财务兑现。

铜产业额外保留沪铜/COMEX 铜的内外盘信息；汇率趋势则独立于行业提供。
"""

import asyncio
import logging
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import akshare as ak


logger = logging.getLogger("MediaCrawler")


_COPPER_KEYWORDS = ("铜矿", "铜冶炼", "铜加工", "电解铜", "阴极铜")

# 汇率趋势对所有有海外收入的公司都适用 (不只是矿业股)，独立于铜价单独取一份。
_RMB_TTL_SECONDS = 6 * 3600
_rmb_cache: Dict[str, object] = {}

_LB_PER_TON = 2204.62

# Ordered from specific products to broader categories.  Each route can expose more than one
# futures anchor; these are proxies for cycle direction, not the company's realized price.
_CYCLE_ROUTES: List[Dict[str, Any]] = [
    {
        "name": "lithium",
        "keywords": ("碳酸锂", "锂盐", "锂矿", "盐湖提锂"),
        "anchors": (("碳酸锂", "LC0", "元/吨"),),
    },
    {
        "name": "coal",
        "keywords": ("煤炭", "动力煤", "焦煤", "煤矿", "煤业"),
        "anchors": (
            ("动力煤期货代理", "ZC0", "元/吨"),
            ("焦煤", "JM0", "元/吨"),
        ),
    },
    {
        "name": "crude_oil",
        "keywords": ("原油", "油气", "石油开采", "石油天然气", "油田"),
        "anchors": (("INE原油", "SC0", "元/桶"),),
    },
    {
        "name": "gold_precious",
        "keywords": ("黄金", "金矿", "贵金属"),
        "anchors": (
            ("沪金", "AU0", "元/克"),
            ("沪银", "AG0", "元/千克"),
        ),
    },
    {
        "name": "steel",
        "keywords": ("钢铁", "钢材", "螺纹钢", "特钢"),
        "anchors": (
            ("螺纹钢", "RB0", "元/吨"),
            ("铁矿石", "I0", "元/吨"),
        ),
    },
    {
        "name": "copper",
        "keywords": ("铜矿", "铜冶炼", "电解铜", "阴极铜", "铜加工"),
        "anchors": (("沪铜", "CU0", "元/吨"),),
    },
    {
        "name": "aluminum",
        "keywords": ("铝业", "电解铝", "氧化铝", "铝加工"),
        "anchors": (("沪铝", "AL0", "元/吨"),),
    },
    {
        "name": "zinc",
        "keywords": ("锌矿", "锌冶炼", "锌业"),
        "anchors": (("沪锌", "ZN0", "元/吨"),),
    },
    {
        "name": "nickel",
        "keywords": ("镍矿", "镍业", "电解镍"),
        "anchors": (("沪镍", "NI0", "元/吨"),),
    },
    {
        "name": "silicon",
        "keywords": ("工业硅", "多晶硅", "硅料"),
        "anchors": (
            ("工业硅", "SI0", "元/吨"),
            ("多晶硅", "PS0", "元/吨"),
        ),
    },
    {
        "name": "glass",
        "keywords": ("玻璃",),
        "anchors": (("玻璃", "FG0", "元/吨"),),
    },
    {
        "name": "soda_ash",
        "keywords": ("纯碱",),
        "anchors": (("纯碱", "SA0", "元/吨"),),
    },
    {
        "name": "hog",
        "keywords": ("生猪", "养猪", "生猪养殖"),
        "anchors": (("生猪", "LH0", "元/吨"),),
    },
]


def _cycle_context(industry_name: Optional[str], fundamentals: Optional[Dict]) -> str:
    parts = [industry_name or ""]
    if fundamentals:
        facts = fundamentals.get("facts") or {}
        parts.extend(
            str(x or "")
            for x in (
                facts.get("sw_industry"),
                fundamentals.get("management_narrative"),
                fundamentals.get("self_disclosed_risks"),
            )
        )
    return " ".join(parts)


def _select_cycle_route(
    industry_name: Optional[str],
    fundamentals: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    text = _cycle_context(industry_name, fundamentals)
    if not text.strip():
        return None
    for route in _CYCLE_ROUTES:
        if any(keyword in text for keyword in route["keywords"]):
            return route
    return None


def _series_stats(dates: List[str], closes: List[float]) -> Dict[str, Any]:
    if not closes:
        return {}
    latest = float(closes[-1])

    def change(periods: int) -> Optional[float]:
        if len(closes) <= periods:
            return None
        old = float(closes[-periods - 1])
        if not old:
            return None
        return round((latest - old) / old * 100, 2)

    year = [float(x) for x in closes[-250:] if x is not None]
    position = None
    if year:
        lo, hi = min(year), max(year)
        if hi > lo:
            position = round((latest - lo) / (hi - lo) * 100, 1)

    return {
        "as_of": dates[-1] if dates else None,
        "latest": latest,
        "change_20d_pct": change(20),
        "change_60d_pct": change(60),
        "position_1y_pct": position,
        "observations": len(closes),
    }


async def _get_futures_anchor(
    name: str,
    symbol: str,
    unit: str,
    *,
    as_of: Optional[date] = None,
) -> Optional[Dict]:
    try:
        df = await asyncio.to_thread(ak.futures_main_sina, symbol=symbol)
    except Exception as e:
        logger.warning(f"[commodity] futures_main_sina({symbol}) failed: {e}")
        return None
    if df is None or df.empty or "收盘价" not in df.columns:
        return None

    date_col = "日期" if "日期" in df.columns else None
    if as_of is not None:
        if date_col is None:
            # Historical mode must not silently use an undated current series.
            return None
        import pandas as pd
        temp = df.copy()
        temp["_parsed_date"] = pd.to_datetime(temp[date_col], errors="coerce")
        temp = temp[temp["_parsed_date"].dt.date <= as_of]
        if temp.empty:
            return None
        df = temp

    dates: List[str] = []
    closes: List[float] = []
    date_col = "日期" if "日期" in df.columns else None
    for _, row in df.tail(300).iterrows():
        try:
            value = float(row["收盘价"])
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        closes.append(value)
        dates.append(str(row.get(date_col) or "") if date_col else "")

    stats = _series_stats(dates, closes)
    if not stats:
        return None
    return {
        "name": name,
        "symbol": symbol,
        "unit": unit,
        **stats,
    }


async def get_cycle_commodity_signal(
    stock_code: str,
    industry_name: Optional[str],
    fundamentals: Optional[Dict] = None,
    *,
    as_of: Optional[date] = None,
) -> Optional[Dict]:
    """
    Return product-matched futures proxies for cyclical industries.

    A missing route is a deliberate "no reliable commodity mapping" outcome.  It should
    lower cycle-evidence readiness rather than trigger a generic commodity guess.
    """
    route = _select_cycle_route(industry_name, fundamentals)
    if not route:
        return None

    anchors = await asyncio.gather(
        *(
            _get_futures_anchor(name, symbol, unit, as_of=as_of)
            for name, symbol, unit in route["anchors"]
        )
    )
    anchors = [x for x in anchors if x]
    if not anchors:
        return None

    result: Dict[str, Any] = {
        "route": route["name"],
        "anchors": anchors,
        "as_of": max((x.get("as_of") or "") for x in anchors) or None,
        "note": (
            "期货主力连续合约仅作周期方向代理，不等同于公司现货结算价。"
            "20/60交易日涨跌与1年位置用于识别景气方向，仍需结合公司销量、成本、库存和资本开支。"
        ),
    }

    # Copper gets the extra domestic/COMEX context; other resource industries do not.
    if route["name"] == "copper" and as_of is None:
        spread = await _get_copper_cross_market_context()
        if spread:
            result["copper_cross_market"] = spread
    elif route["name"] == "copper" and as_of is not None:
        result["historical_note"] = (
            "历史模式不使用当前COMEX现货/跨市场快照，只保留截至as_of的沪铜历史序列。"
        )
    return result


def is_cyclical_mining_industry(industry_name: str) -> bool:
    """Backward-compatible name: now means specifically a copper-related context."""
    return any(kw in (industry_name or "") for kw in _COPPER_KEYWORDS)


async def _get_sh_copper_price() -> Optional[float]:
    try:
        df = await asyncio.to_thread(ak.futures_main_sina, symbol="CU0")
    except Exception as e:
        logger.error(f"[commodity] futures_main_sina(CU0) failed: {e}")
        return None
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["收盘价"])


async def _get_comex_copper_price() -> Optional[float]:
    try:
        df = await asyncio.to_thread(ak.futures_global_spot_em)
    except Exception as e:
        logger.error(f"[commodity] futures_global_spot_em failed: {e}")
        return None
    if df is None or df.empty:
        return None
    row = df[df["代码"] == "HG00Y"]
    if row.empty or row.iloc[0]["最新价"] != row.iloc[0]["最新价"]:
        return None
    return float(row.iloc[0]["最新价"])


async def _get_rmb_observation(as_of: Optional[date] = None) -> Optional[Dict]:
    """Dated USD/CNY fixing direction, one USD quoted in CNY; never invent as-of."""
    try:
        import pandas as pd
        df = await asyncio.to_thread(ak.currency_boc_safe)
    except Exception as e:
        logger.error(f"[commodity] currency_boc_safe failed: {e}")
        return None
    if df is None or df.empty or "美元" not in df.columns:
        return None
    date_col = next((x for x in ("日期", "date", "时间") if x in df.columns), None)
    if not date_col:
        # No point-in-time date means no auditable exchange-rate observation.
        return None
    rows = df.copy()
    rows["_asof"] = pd.to_datetime(rows[date_col], errors="coerce").dt.date
    rows["_usd"] = pd.to_numeric(rows["美元"], errors="coerce")
    rows = rows.dropna(subset=["_asof", "_usd"])
    if as_of is not None:
        rows = rows[rows["_asof"] <= as_of]
    if rows.empty:
        return None
    rows = rows.sort_values("_asof").drop_duplicates(subset=["_asof"], keep="last")
    recent = rows.tail(30)
    if len(recent) < 30:
        return None
    first = float(recent["_usd"].iloc[0])
    last = float(recent["_usd"].iloc[-1])
    if first <= 0 or last <= 0:
        return None
    change_pct = (last / first - 1) * 100
    trend = (
        "appreciating" if change_pct < -.3 else
        "depreciating" if change_pct > .3 else "stable"
    )
    return {
        "as_of": str(recent["_asof"].iloc[-1]),
        "usdcny_midpoint_cny_per_usd": round(last / 100, 5),
        "usdcny_change_30obs_pct": round(change_pct, 3),
        "rmb_trend": trend,
        "source_name": "AkShare currency_boc_safe (SAFE fixing)",
        "currency_unit": "CNY per USD (original per 100 USD)",
        "sample_observations": 30,
    }


async def _get_rmb_trend(as_of: Optional[date] = None) -> Optional[str]:
    obs = await _get_rmb_observation(as_of=as_of)
    return obs["rmb_trend"] if obs else None


async def get_rmb_trend_signal(
    *,
    as_of: Optional[date] = None,
) -> Optional[Dict]:
    """
    人民币汇率趋势 (独立于铜价，不按行业设门槛)。

    汇率对收入的影响只对"有海外收入"的公司成立，但海外收入占比要到 F10 数据解析完
    才知道，且占比为 0 时丢掉这一项也无害；反过来若按行业设门槛，出口导向的制造业
    (恰恰是汇率敞口最大的) 反而永远拿不到数据。
    取不到就返回 None，调用方写"暂缺"，绝不猜方向。
    """
    if as_of is None and time.time() < (_rmb_cache.get("expire_at") or 0):
        return _rmb_cache["value"]  # type: ignore[return-value]

    observation = await _get_rmb_observation(as_of=as_of)
    if observation is None:
        logger.warning("[commodity] 人民币汇率中间价缺少可核验的最新日期/30个观察值")
        return None

    trend = observation["rmb_trend"]
    value = {
        **observation,
        "rmb_trend_note": {
            "appreciating": "人民币近期升值 (近30个报价观察日美元兑人民币中间价下降)",
            "depreciating": "人民币近期贬值 (近30个报价观察日美元兑人民币中间价上升)",
            "stable": "人民币近期基本稳定 (30个报价观察日波动小于0.3%)",
        }.get(trend, trend),
    }
    if as_of is None:
        _rmb_cache["value"] = value
        _rmb_cache["expire_at"] = time.time() + _RMB_TTL_SECONDS
    return value


async def _get_copper_cross_market_context() -> Optional[Dict]:
    sh_price, comex_price, rmb_trend = await asyncio.gather(
        _get_sh_copper_price(),
        _get_comex_copper_price(),
        _get_rmb_trend(),
    )
    if sh_price is None and comex_price is None:
        return None
    return {
        "sh_copper_price": sh_price,
        "sh_copper_unit": "元/吨",
        "comex_copper_price": comex_price,
        "comex_copper_unit": "美元/磅",
        "rmb_trend": rmb_trend,
        "note": (
            "沪铜与COMEX铜单位不同，不能直接相减；这里只提供内外盘背景，"
            "任何套利/囤货判断都需要额外证据。"
        ),
    }


async def get_copper_spread_signal(stock_code: str, industry_name: Optional[str]) -> Optional[Dict]:
    """
    仅当所属行业命中周期性矿业关键词时才拉取铜价数据，避免给无关股票塞入不相关信息。
    行业未知或不是周期性矿业股时返回 None。
    """
    if not industry_name or not is_cyclical_mining_industry(industry_name):
        return None

    sh_price = await _get_sh_copper_price()
    comex_price = await _get_comex_copper_price()
    rmb_trend = await _get_rmb_trend()

    notes: List[str] = [
        "沪铜单位: 人民币元/吨；COMEX铜单位: 美元/磅 (1吨=2204.62磅)，两者单位不同，"
        "不能直接相减，价差判断请结合汇率与单位换算自行判断内外盘套利空间是否扩大/收窄。"
    ]
    if sh_price is None:
        notes.append("沪铜价格数据获取失败。")
    if comex_price is None:
        notes.append("COMEX铜价格数据获取失败。")
    if rmb_trend is None:
        notes.append("人民币汇率趋势数据获取失败。")

    if sh_price is None and comex_price is None:
        return None

    return {
        "sh_copper_price": sh_price,
        "sh_copper_unit": "元/吨",
        "comex_copper_price": comex_price,
        "comex_copper_unit": "美元/磅",
        "rmb_trend": rmb_trend,
        "note": " ".join(notes),
    }
