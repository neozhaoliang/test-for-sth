# -*- coding: utf-8 -*-
"""Broad-based A-share ETF look-through: benchmark first, company cycle second.

The ETF price is not the index, trading amount is not capital inflow,
the date a component entered an index is not an ETF purchase record, and
a high price percentile is not proof of a valuation bubble.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import math
import re
import time
from typing import Any
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

from tools.utils import utils


# Match an actual plain index, not a derivative strategy on the same universe.
# Do not guess "科创板ETF" -> 科创50, or "创业板50" -> 创业板指.
_FAMILIES = (
    ("上证50", "000016", "sh", "上证50"),
    ("沪深300", "000300", "sh", "沪深300"),
    ("创业板指", "399006", "sz", None),
    ("科创50", "000688", "sh", None),
    ("科创100", "000698", "sh", None),
    ("中证500", "000905", "sh", "中证500"),
    ("中证1000", "000852", "sh", "中证1000"),
)
_STRATEGY_EXCLUSIONS = re.compile(
    r"增强|价值|成长|红利|高股息|质量|低波|等权|ESG|动量|基本面|现金流|"
    r"策略|精选|Smart|红利低波|行业|消费|金融|银行|芯片|半导体|人工智能|"
    r"科技龙头|新能源|医药|机器人|科创创业|双创|创业板50|创业板100",
    re.I,
)
_TTL = 900
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


def classify_broad_index(name: str) -> dict | None:
    """ETF family, not a synonym for the underlying benchmark code."""
    n = re.sub(r"[\s（）()·_\-]+", "", str(name or ""))
    if _STRATEGY_EXCLUSIONS.search(n) or re.search(
        r"QDII|联接|LOF|指数增强|期权|债|商品|港股|海外|跨境", n, re.I,
    ):
        return None
    if "上证50" in n and "中证" not in n:
        family = _FAMILIES[0]
    elif "沪深300" in n:
        family = _FAMILIES[1]
    elif "创业板指" in n or "创业板ETF" in n or "创业板交易型" in n:
        family = _FAMILIES[2]
    elif "科创50" in n or "科创板50" in n:
        family = _FAMILIES[3]
    elif "科创100" in n or "科创板100" in n:
        family = _FAMILIES[4]
    elif "中证500" in n:
        family = _FAMILIES[5]
    elif "中证1000" in n:
        family = _FAMILIES[6]
    else:
        return None
    label, code, exchange, pe_symbol = family
    return {
        "family": label,
        "index_code": code,
        "exchange": exchange,
        "pe_symbol": pe_symbol,
        "method": "plain_broad_index",
        "source": "基金名称识别基础宽基；实际跟踪标的仍以基金合同为准",
    }


def _num(value: Any) -> float | None:
    try:
        n = float(str(value).replace(",", "").replace("%", ""))
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _day(value: Any) -> date | None:
    try:
        stamp = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(stamp) else stamp.date()
    except (ValueError, TypeError):
        return None


def _pct(a: float, b: float) -> float | None:
    return round((a / b - 1) * 100, 2) if b > 0 else None


def summarize_index_tape(frame: Any, *, as_of: date) -> dict:
    """Observed YTD, momentum, 52-week location, drawdown and comparable amount.

    YTD uses actual last close from the PRIOR calendar year; never January's
    first close, which silently loses the first trading day's return.
    """
    if frame is None or getattr(frame, "empty", True):
        return {"status": "unavailable"}
    f = pd.DataFrame(frame).copy()
    for col in ("日期", "收盘"):
        if col not in f.columns:
            return {"status": "unavailable"}
    f["_day"] = pd.to_datetime(f["日期"], errors="coerce")
    f["_close"] = pd.to_numeric(f["收盘"], errors="coerce")
    f["_amount"] = pd.to_numeric(f.get("成交额", pd.Series(index=f.index, dtype=float)),
                                  errors="coerce")
    f = f.dropna(subset=["_day", "_close"])
    f = f[(f["_day"].dt.date <= as_of) & (f["_close"] > 0)]
    f = f.sort_values("_day").drop_duplicates("_day", keep="last")
    if f.empty:
        return {"status": "unavailable"}
    latest = f.iloc[-1]
    observed = latest["_day"].date()
    if (as_of-observed).days > 12:
        return {"status": "stale", "as_of": observed.isoformat()}
    closes = f["_close"].astype(float).tolist()
    out = {
        "status": "observed", "as_of": observed.isoformat(),
        "index_close": round(closes[-1], 3),
        "source": "东方财富A股指数历史日行情",
        "sample_count": len(closes),
    }
    previous_year = f[f["_day"].dt.year == observed.year - 1]
    if not previous_year.empty:
        prev = previous_year.iloc[-1]
        if (date(observed.year, 1, 1)-prev["_day"].date()).days <= 15:
            out["ytd_pct"] = _pct(closes[-1], float(prev["_close"]))
            out["ytd_base_date"] = prev["_day"].date().isoformat()
    for n, key in ((20, "return_20d_pct"), (60, "return_60d_pct")):
        if len(closes) >= n+1:
            out[key] = _pct(closes[-1], closes[-1-n])
    if len(closes) >= 120:
        window = closes[-min(252, len(closes)):]
        lo, hi = min(window), max(window)
        out["price_position_52w_pct"] = (
            round((closes[-1]-lo)/(hi-lo)*100, 1) if hi > lo else None
        )
        out["below_52w_high_pct"] = _pct(closes[-1], hi)
    current_year = f[f["_day"].dt.year == observed.year]["_close"].tolist()
    if len(current_year) >= 20:
        high, maximum_drawdown = 0, 0.0
        for v in current_year:
            high = max(high, float(v))
            maximum_drawdown = min(maximum_drawdown, (float(v)/high-1)*100)
        out["ytd_max_drawdown_pct"] = round(maximum_drawdown, 2)
    amounts = f["_amount"].astype(float).tolist()
    if len(amounts) >= 25 and all(
        math.isfinite(v) and v > 0 for v in amounts[-25:]
    ):
        out["recent5_vs_prior20_amount_ratio"] = round(
            sum(amounts[-5:])/5/(sum(amounts[-25:-5])/20), 2
        )
        out["amount_as_of"] = observed.isoformat()
        out["amount_source"] = "指数成份股统计成交额（非ETF份额申购）"
    return out


def summarize_index_pe(frame: Any, *, as_of: date) -> dict:
    """Optional LeguLegu market-cap-weighted TTM index PE only, no fake PB."""
    if frame is None or getattr(frame, "empty", True):
        return {"status": "unavailable"}
    f = pd.DataFrame(frame)
    date_col = "日期" if "日期" in f else None
    pe_col = "滚动市盈率" if "滚动市盈率" in f else None
    if not date_col or not pe_col:
        return {"status": "unavailable"}
    f = f.copy()
    f["_day"] = pd.to_datetime(f[date_col], errors="coerce")
    f["_pe"] = pd.to_numeric(f[pe_col], errors="coerce")
    f = f.dropna(subset=["_day", "_pe"])
    f = f[(f["_day"].dt.date <= as_of) & (f["_pe"] > 0)]
    f = f.sort_values("_day").drop_duplicates("_day", keep="last")
    if f.empty:
        return {"status": "unavailable"}
    latest = f.iloc[-1]
    when = latest["_day"].date()
    if (as_of-when).days > 30:
        return {"status": "stale", "as_of": when.isoformat()}
    out = {"status": "observed", "pe_ttm": round(float(latest["_pe"]), 2),
           "as_of": when.isoformat(), "source": "乐咕乐股指数滚动PE"}
    trailing = f[f["_day"].dt.date >= when-timedelta(days=1095)]
    if len(trailing) >= 250:
        series = trailing["_pe"].astype(float)
        out["pe_3y_percentile_pct"] = round(
            (series <= float(latest["_pe"])).mean()*100, 1
        )
        out["pe_3y_samples"] = len(series)
    return out


def _recent_entries(frame: Any, *, as_of: date) -> dict:
    """Sina inclusion dates, NOT the CSIndex snapshot's column named 日期."""
    if frame is None or getattr(frame, "empty", True):
        return {"status": "unavailable", "recent_entries": []}
    f = pd.DataFrame(frame)
    required = {"品种代码", "品种名称", "纳入日期"}
    if not required.issubset(f.columns):
        return {"status": "unavailable", "recent_entries": []}
    seen: set[str] = set()
    selected = []
    total = 0
    for _, row in f.iterrows():
        code = str(row["品种代码"]).strip().zfill(6)
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            continue
        seen.add(code)
        total += 1
        enter = _day(row["纳入日期"])
        if enter is not None and 0 <= (as_of-enter).days <= 190:
            selected.append({
                "code": code, "name": str(row["品种名称"]),
                "included_on": enter.isoformat(),
            })
    selected.sort(key=lambda x: x["included_on"], reverse=True)
    # Do not claim full index breadth if source covers only a partial list.
    return {"status": "observed" if total >= 20 else "partial",
            "constituents_observed": total,
            "recent_count": len(selected),
            "recent_entries": selected[:8],
            "source": "新浪指数最新成份股及纳入日期（日期不等于ETF买入日）",
            "as_of": as_of.isoformat()}


def _pre_entry_momentum(frame: Any, *, admitted_on: date) -> dict:
    if frame is None or getattr(frame, "empty", True):
        return {}
    f = pd.DataFrame(frame).copy()
    if not {"日期", "收盘"}.issubset(f.columns):
        return {}
    f["_day"] = pd.to_datetime(f["日期"], errors="coerce")
    f["_close"] = pd.to_numeric(f["收盘"], errors="coerce")
    f = f.dropna(subset=["_day", "_close"])
    f = f[(f["_day"].dt.date < admitted_on) & (f["_close"] > 0)]
    f = f.sort_values("_day").drop_duplicates("_day", keep="last")
    if len(f) < 61 or (admitted_on-f.iloc[-1]["_day"].date()).days > 10:
        return {}
    prices = f["_close"].astype(float).tolist()
    return {
        "pre_inclusion_60d_return_pct": _pct(prices[-1], prices[-61]),
        "pre_inclusion_close_as_of": f.iloc[-1]["_day"].date().isoformat(),
        "momentum_flag": _pct(prices[-1], prices[-61]) is not None
                         and _pct(prices[-1], prices[-61]) >= 40,
        "source": "东方财富股票前复权日行情，纳入日前约60交易日；上涨不等于泡沫",
    }


async def _ak_call(name: str, *, params: dict, timeout: float = 12) -> Any:
    fn = getattr(ak, name, None)
    if not callable(fn):
        return None
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, **params), timeout=timeout)
    except Exception as exc:
        utils.logger.warning("[broad ETF] %s failed: %s", name, exc)
        return None


async def _collect_uncached(profile: dict, as_of: date) -> dict:
    code = profile["index_code"]
    begin = (date(as_of.year-1, 1, 1)-timedelta(days=25)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    series, valuation, constituents = await asyncio.gather(
        _ak_call("index_zh_a_hist", params={
            "symbol": code, "period": "daily",
            "start_date": begin, "end_date": end,
        }, timeout=16),
        _ak_call("stock_index_pe_lg", params={"symbol": profile["pe_symbol"]},
                 timeout=11) if profile["pe_symbol"] else asyncio.sleep(0, result=None),
        _ak_call("index_stock_cons", params={"symbol": code}, timeout=12),
    )
    price = summarize_index_tape(series, as_of=as_of)
    pe = summarize_index_pe(valuation, as_of=as_of)
    changes = _recent_entries(constituents, as_of=as_of)
    candidates = changes.get("recent_entries", [])[:5]
    if candidates:
        sem = asyncio.Semaphore(2)
        async def check(entry: dict) -> dict:
            async with sem:
                in_date = date.fromisoformat(entry["included_on"])
                df = await _ak_call("stock_zh_a_hist", params={
                    "symbol": entry["code"], "period": "daily",
                    "start_date": (in_date-timedelta(days=160)).strftime("%Y%m%d"),
                    "end_date": (in_date-timedelta(days=1)).strftime("%Y%m%d"),
                    "adjust": "qfq",
                }, timeout=11)
                return {**entry, **_pre_entry_momentum(df, admitted_on=in_date)}
        checked = await asyncio.gather(*(check(e) for e in candidates))
        changes["recent_entries"] = checked + changes["recent_entries"][len(checked):]
        changes["pre_entry_checked"] = sum(
            e.get("pre_inclusion_60d_return_pct") is not None for e in checked
        )
        changes["pre_entry_high_momentum"] = [
            e for e in checked if e.get("momentum_flag")
        ]
    outlook = "只代表指数行情与交易量观察，不等于全市场上涨家数、基金资金净流入或政策利好"
    r20 = _num(price.get("return_20d_pct"))
    volume = _num(price.get("recent5_vs_prior20_amount_ratio"))
    if r20 is not None and volume is not None:
        if r20 > 5 and volume >= 1.5:
            outlook = "近20日指数涨幅较高且成交明显放大：存在热度升温迹象，但不能仅据此认定泡沫"
        elif r20 < -5 and volume >= 1.5:
            outlook = "指数回落同时成交放大：短期风险偏好承压；成交额不能代表资金净流出"
        elif r20 > 0 and volume < .85:
            outlook = "指数上涨而成交额相对收缩：上涨确认度有限，不能判定机构已经撤离"
        else:
            outlook = "价格与成交指标未共同构成明显的单边情绪证据"
    return {
        "profile": profile, "benchmark_price": price, "index_valuation": pe,
        "constituent_changes": changes, "sentiment_observation": outlook,
        "index_methodology_risk": (
            "指数公司按规则纳入和调权成分股，ETF依照跟踪策略调整；"
            "只有同时核实纳入生效日与此前行情，才能判断是否存在追高纳入风险。"
            "ETF季报重仓变化和价格上涨不能反推基金曾在高位主动买入。"
        ),
        "as_of": as_of.isoformat(),
    }


async def get_broad_market_context(
    profile: dict | None, as_of: date | None = None,
) -> dict:
    if not profile:
        return {}
    as_of = as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    key = profile["index_code"]
    hit = _CACHE.get(key)
    if hit and (time.monotonic()-hit[0]) < _TTL:
        return hit[1]
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _CACHE.get(key)
        if hit and (time.monotonic()-hit[0]) < _TTL:
            return hit[1]
        result = await _collect_uncached(profile, as_of)
        _CACHE[key] = (time.monotonic(), result)
        return result
