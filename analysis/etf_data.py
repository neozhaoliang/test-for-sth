# -*- coding: utf-8 -*-
"""Exchange-traded fund data, kept separate from issuer-level stock research.

Only live mode: historical constituent disclosures without publication timestamps
cannot be treated as point-in-time safe. A fund's quarterly heavy positions are
NOT its daily creation/redemption basket or the index's complete constituents.
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

from analysis.profitability import get_profitability_trend
from analysis.valuation_history import get_valuation_history
from tools.utils import utils

_CACHE_TTL = 240
_catalog_cache: tuple[float, list[dict]] = (0.0, [])
_catalog_lock = asyncio.Lock()
_holdings_cache: dict[str, tuple[float, dict]] = {}
_EXCLUDED = re.compile(r"港股|恒生|纳斯达克|标普|日经|德国|法国|美国|日本|跨境|QDII|全球|黄金|白银|原油|商品|国债|债券|货币|短融|可转债|REIT|不动产", re.I)


def _num(value: Any) -> float | None:
    try:
        n = float(value)
        return round(n, 6) if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _digits(code: Any) -> str:
    value = str(code or "").strip().upper()
    if re.fullmatch(r"(?:SH|SZ)?[0-9]{6}", value):
        return value[-6:]
    return ""


def _etf_code(code: str) -> str:
    return ("SH" if code.startswith("5") else "SZ") + code


def _spot_row(row: Any) -> dict:
    code = _digits(row.get("代码"))
    if not code:
        return {}
    price, iopv = _num(row.get("最新价")), _num(row.get("IOPV实时估值"))
    premium = (price / iopv - 1) * 100 if price and iopv and iopv > 0 else None
    return {
        "code": code, "stock_code": _etf_code(code),
        "name": str(row.get("名称") or "").strip(),
        "latest_price": price, "iopv": iopv,
        "iopv_premium_pct": round(premium, 3) if premium is not None else None,
        "price_change_pct": _num(row.get("涨跌幅")),
        "turnover_yuan": _num(row.get("成交额")),
        "latest_shares": _num(row.get("最新份额")),
        "volume": _num(row.get("成交量")),
        "data_date": str(row.get("数据日期") or ""),
        "quote_time": str(row.get("更新时间") or ""),
    }


async def _catalog() -> list[dict]:
    global _catalog_cache
    if _catalog_cache[1] and time.monotonic() - _catalog_cache[0] < _CACHE_TTL:
        return _catalog_cache[1]
    async with _catalog_lock:
        if _catalog_cache[1] and time.monotonic() - _catalog_cache[0] < _CACHE_TTL:
            return _catalog_cache[1]
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(ak.fund_etf_spot_em), timeout=28
            )
            rows = [_spot_row(row) for _, row in df.iterrows()] if df is not None else []
            rows = [row for row in rows if row and row["name"]]
            if rows:
                _catalog_cache = (time.monotonic(), rows)
                return rows
        except Exception as exc:
            utils.logger.warning("[etf] ETF spot unavailable: %s", exc)
        # A stale quote must never be presented as a current tradeable price.
        return []


async def resolve_etf(raw: str) -> dict | None:
    """Return a verified ETF; no guess based on 5/1 prefixes alone."""
    query = str(raw or "").strip()
    if not query:
        return None
    rows = await _catalog()
    code = _digits(query)
    if code:
        return next((r for r in rows if r["code"] == code), None)
    exact = [r for r in rows if r["name"] == query]
    if len(exact) == 1:
        return exact[0]
    partial = [r for r in rows if query in r["name"]]
    return partial[0] if len(partial) == 1 else None


async def search_etfs(query: str, limit: int = 8) -> list[dict]:
    q = str(query or "").strip().casefold()
    if not q:
        return []
    rows = await _catalog()
    matched = [r for r in rows if q in r["name"].casefold()
               or q in r["stock_code"].casefold() or q in r["code"]]
    matched.sort(key=lambda r: (r["code"] != q and r["stock_code"].casefold() != q,
                                not r["name"].casefold().startswith(q), len(r["name"])))
    return [{"code": r["stock_code"], "name": r["name"],
             "market": r["stock_code"][:2], "asset_type": "etf"}
            for r in matched[:max(1, min(limit, 15))]]


def _quarter(value: Any) -> tuple[int, int] | None:
    text = str(value or "")
    match = re.search(r"(20\d{2})\s*年\s*第?\s*([1-4])\s*季", text)
    if match:
        return int(match[1]), int(match[2])
    match = re.search(r"(20\d{2})[-/](0?[1-9]|1[0-2])[-/]\d{1,2}", text)
    if match:
        return int(match[1]), (int(match[2]) - 1) // 3 + 1
    return None


def _parse_holdings(df: Any, today: date) -> dict | None:
    if df is None or df.empty or "季度" not in df.columns:
        return None
    items: list[tuple[tuple[int, int], dict]] = []
    for _, row in df.iterrows():
        quarter = _quarter(row.get("季度"))
        if quarter is None or quarter > (today.year, (today.month - 1) // 3 + 1):
            continue
        code = _digits(row.get("股票代码"))
        name = str(row.get("股票名称") or "").strip()
        weight = _num(row.get("占净值比例"))
        if not code or not name or weight is None or weight <= 0 or weight > 100:
            continue
        items.append((quarter, {
            "code": code, "name": name, "weight_pct": weight,
            "market_value_10k": _num(row.get("持仓市值")),
        }))
    if not items:
        return None
    latest = max(x[0] for x in items)
    # Some fund pages contain multiple quarters; never join two disclosures.
    securities: dict[str, dict] = {}
    for quarter, holding in items:
        if quarter == latest:
            securities[holding["code"]] = holding
    holdings = sorted(securities.values(), key=lambda r: r["weight_pct"], reverse=True)
    return {
        "as_of": f"{latest[0]}-Q{latest[1]}",
        "holdings": holdings[:10],
        "disclosed_count": len(holdings),
        "scope": "最近一期披露的股票持仓（不代表当前每日完整权重）",
        "source": "东方财富基金档案 / 基金投资组合披露",
    }


async def _holdings(code: str, today: date) -> dict:
    cached = _holdings_cache.get(code)
    if cached and time.monotonic() - cached[0] < 1800:
        return cached[1]
    for year in (today.year, today.year - 1):
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(ak.fund_portfolio_hold_em,
                                  symbol=code, date=str(year)),
                timeout=22,
            )
            result = _parse_holdings(df, today)
            if result is not None:
                _holdings_cache[code] = (time.monotonic(), result)
                return result
        except Exception as exc:
            utils.logger.warning("[etf] holdings %s year=%s unavailable: %s", code, year, exc)
    return {"holdings": [], "as_of": None, "scope": "成分股权重未核实", "source": None}


def _position_from_prices(df: Any, *, date_column: str, close_column: str) -> dict:
    if df is None or df.empty or date_column not in df or close_column not in df:
        return {}
    frame = df[[date_column, close_column]].copy()
    frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
    frame[close_column] = pd.to_numeric(frame[close_column], errors="coerce")
    frame = frame.dropna().sort_values(date_column)
    frame = frame[frame[close_column] > 0].tail(252)
    if len(frame) < 200:
        # Fewer than 200 sessions is not a defensible one-year price window.
        return {}
    closes = frame[close_column]
    first = float(closes.iloc[0])
    price = float(closes.iloc[-1])
    low, high = float(closes.min()), float(closes.max())
    return {
        "price_as_of": str(frame.iloc[-1][date_column].date()),
        "price_close": round(price, 4),
        "price_52w_low": round(low, 4),
        "price_52w_high": round(high, 4),
        "price_position_pct": round((price - low) / (high - low) * 100, 1)
                              if high > low else None,
        "below_52w_high_pct": round((price / high - 1) * 100, 1),
        "period_return_pct": round((price / first - 1) * 100, 1),
        "history_trading_days": len(frame),
    }


async def _stock_price(code: str, today: date) -> dict:
    start = (today - timedelta(days=400)).strftime("%Y%m%d")
    try:
        # Adjusted closes reduce false highs/lows around ex-dividend dates.
        df = await asyncio.wait_for(
            asyncio.to_thread(ak.stock_zh_a_hist,
                symbol=code, period="daily", start_date=start,
                end_date=today.strftime("%Y%m%d"), adjust="qfq"),
            timeout=16,
        )
        return _position_from_prices(df, date_column="日期", close_column="收盘")
    except Exception as exc:
        utils.logger.warning("[etf] A-share history %s: %s", code, exc)
        return {}


async def _etf_price(code: str, today: date) -> dict:
    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(ak.fund_etf_hist_em,
                symbol=code, period="daily",
                start_date=(today-timedelta(days=400)).strftime("%Y%m%d"),
                end_date=today.strftime("%Y%m%d"), adjust="qfq"),
            timeout=18,
        )
        return _position_from_prices(df, date_column="日期", close_column="收盘")
    except Exception as exc:
        utils.logger.warning("[etf] ETF history %s: %s", code, exc)
        return {}


async def _stock_valuation(code: str) -> dict:
    """Issuer valuation and 3/5-year history, not an ETF index multiple."""
    try:
        history = await asyncio.wait_for(
            get_valuation_history(code), timeout=33,
        )
        if history:
            def percentile(rows):
                for years in (5, 3, 10):
                    item = next((x for x in rows
                                 if x.get("years") == years
                                 and x.get("observations", 0) >= 100), None)
                    if item:
                        return {
                            "years": years,
                            "percentile": item["percentile"],
                            "observations": item["observations"],
                        }
                return None
            return {
                "pe_ttm": _num(history.get("current_pe_ttm")),
                "pb": _num(history.get("current_pb")),
                "pe_history_position": percentile(history.get("pe_percentiles") or []),
                "pb_history_position": percentile(history.get("pb_percentiles") or []),
                "valuation_observed_on": history.get("as_of"),
                "valuation_source": history.get("source"),
                "valuation_notes": history.get("notes") or [],
            }
    except Exception as exc:
        utils.logger.warning("[etf] historical valuation %s: %s", code, exc)
    # Optional fallback when the history provider has no data. It never fabricates a percentile.
    prefix = "SH" if code.startswith(("6", "9")) else (
        "BJ" if code.startswith(("4", "8")) else "SZ")
    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(ak.stock_individual_spot_xq, symbol=prefix + code),
            timeout=12,
        )
        if df is None or df.empty:
            return {}
        row = df.iloc[0]
        pe = _num(row.get("市盈率(TTM)"))
        pb = _num(row.get("市净率"))
        return {
            "pe_ttm": round(pe, 2) if pe is not None and pe > 0 else None,
            "pb": round(pb, 2) if pb is not None and pb > 0 else None,
            "valuation_observed_on": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "valuation_source": "雪球个股行情：PE(TTM)/PB，非历史分位",
        }
    except Exception as exc:
        utils.logger.warning("[etf] issuer valuation %s: %s", code, exc)
        return {}


async def _stock_profit(code: str) -> dict:
    try:
        result = await asyncio.wait_for(get_profitability_trend(code), timeout=18)
        periods = (result or {}).get("periods") or []
        if not periods:
            return {}
        latest = max(periods, key=lambda p: str(p.get("period") or ""))
        return {
            "financial_period": str(latest.get("period") or ""),
            "net_profit_growth_pct": _num(latest.get("net_profit_growth_pct")),
            "roe_pct": _num(latest.get("roe_pct")),
            "gross_margin_pct": _num(latest.get("gross_margin_pct")),
            "net_margin_pct": _num(latest.get("net_margin_pct")),
            "financial_history": [
                {"period": str(p.get("period") or ""),
                 "net_profit_growth_pct": _num(p.get("net_profit_growth_pct")),
                 "roe_pct": _num(p.get("roe_pct"))}
                for p in sorted(periods, key=lambda p: str(p.get("period") or ""))[-6:]
            ],
        }
    except Exception as exc:
        utils.logger.warning("[etf] issuer profitability %s: %s", code, exc)
        return {}


def _portfolio_stats(holdings: list[dict]) -> dict:
    top5 = round(sum(h["weight_pct"] for h in holdings[:5]), 2)
    top10 = round(sum(h["weight_pct"] for h in holdings[:10]), 2)
    observed = [h for h in holdings if h.get("price_position_pct") is not None]
    high = [h for h in observed if h["price_position_pct"] >= 80]
    depressed = [h for h in observed if h["price_position_pct"] <= 20]
    fin = [h for h in holdings if h.get("net_profit_growth_pct") is not None]
    vals = [h for h in holdings if h.get("pe_ttm") is not None and h.get("pb") is not None]
    priced = [h for h in holdings if (
        h.get("pe_history_position") or h.get("pb_history_position")
    )]
    valuation_high = [h for h in priced if any(
        float((h.get(key) or {}).get("percentile") or 0) >= 80
        for key in ("pe_history_position", "pb_history_position")
    )]
    return {
        "top5_weight_pct": top5 if holdings else None,
        "top10_weight_pct": top10 if holdings else None,
        "price_observed_weight_pct": round(sum(h["weight_pct"] for h in observed), 2),
        "high_position_weight_pct": round(sum(h["weight_pct"] for h in high), 2),
        "low_position_weight_pct": round(sum(h["weight_pct"] for h in depressed), 2),
        "profit_observed_weight_pct": round(sum(h["weight_pct"] for h in fin), 2),
        "pe_pb_observed_weight_pct": round(sum(h["weight_pct"] for h in vals), 2),
        "historical_valuation_observed_weight_pct": round(
            sum(h["weight_pct"] for h in priced), 2),
        "high_valuation_position_weight_pct": round(
            sum(h["weight_pct"] for h in valuation_high), 2),
        "negative_profit_growth_weight_pct": round(
            sum(h["weight_pct"] for h in fin if h["net_profit_growth_pct"] < 0), 2),
        "price_high_rule": "近252交易日前复权收盘价区间位置≥80%（不等于估值贵）",
        "price_low_rule": "近252交易日前复权收盘价区间位置≤20%（不等于估值便宜）",
        "denominator": "全部ETF净值权重；仅统计有数据的披露重仓股，不外推未覆盖成分",
    }


async def collect_etf_data(spot: dict) -> dict:
    """Gather distinct ETF and constituent observations without company-only fields."""
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    code, name = spot["code"], spot["name"]
    equity = not bool(_EXCLUDED.search(name))
    holdings, etf_price = await asyncio.gather(_holdings(code, today), _etf_price(code, today))
    original = holdings.get("holdings") or []
    if not equity:
        # Never run A-share financials on Hong Kong, bond or gold holdings.
        for holding in original:
            holding["not_an_a_share_lookthrough"] = True

    sem = asyncio.Semaphore(4)
    async def enrich(holding: dict, index: int) -> dict:
        row = dict(holding)
        if equity and re.fullmatch(r"(?:[0369]\d{5}|[48]\d{5})", row["code"]):
            async with sem:
                row.update(await _stock_price(row["code"], today))
                if index < 5:
                    profit, valuation = await asyncio.gather(
                        _stock_profit(row["code"]), _stock_valuation(row["code"])
                    )
                    row.update(profit)
                    row.update(valuation)
        return row

    enriched = await asyncio.gather(*(enrich(h, i) for i, h in enumerate(original[:10])))
    return {
        "asset_type": "etf",
        "scope": "A股股票ETF" if equity else "非A股股票ETF或混合资产ETF（限制适用个股透视）",
        "fund": spot,
        "portfolio": {**holdings, "holdings": enriched},
        "portfolio_stats": _portfolio_stats(enriched),
        "etf_price_position": etf_price,
        "limitations": [
            "基金季报披露重仓股不等于今日全部指数成份股，亦不是申赎篮子",
            "股价52周位置只反映价格相对高低，不能代替估值PE/PB分位",
            "单次份额与成交额不能推出ETF资金净流入，更不能推出主动看好",
            "IOPV是盘中估值，不等于已确认的基金净值；注意报价时间",
            "成分股财务数据为披露期指标，不可视为今日盈利预测",
            "已观察的前五大个股PE(TTM)/PB不代表ETF整体估值；历史分位需观察期和披露日期",
        ],
        "collected_on": today.isoformat(),
    }
