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

from analysis.etf_discovery import assets_yi, ranked_matches, matches, recent_day, theme, key
from analysis.etf_macro import get_etf_market_context
from analysis.etf_cycle import assess_cycle
from analysis.etf_holding_risk import screen_holdings
from analysis.profitability import get_profitability_trend
from analysis.valuation_history import get_valuation_history
from tools.utils import utils


# Fund identity, size and live quote are independent datasets. Neither
# equity trading turnover nor outstanding shares alone are fund net assets.
_CATALOG_TTL = 6 * 3600
_FAILURE_RETRY = 300
_SCALE_TTL = 20 * 60
_catalog_cache: tuple[float, list[dict]] = (0.0, [])
_catalog_lock = asyncio.Lock()
_catalog_refresh_task: asyncio.Task | None = None
_scale_cache: tuple[float, dict[str, dict]] = (0.0, {})
_scale_lock = asyncio.Lock()
_scale_refresh_task: asyncio.Task | None = None
_holdings_cache: dict[str, tuple[float, dict]] = {}
_EXCLUDED = re.compile(
    r"港股|恒生|纳斯达克|标普|日经|德国|法国|美国|日本|跨境|QDII|全球|"
    r"黄金|白银|原油|商品|国债|债券|货币|短融|可转债|REIT|不动产", re.I
)
_GOLD_EQUITY = re.compile(r"黄金股|黄金产业|金矿股|黄金矿业", re.I)


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


def _catalog_entry(code: Any, name: Any, *, source: str,
                   require_etf: bool = True) -> dict | None:
    digits = _digits(code)
    title = str(name or "").strip()
    # Exclude OTC ETF feeder shares and similarly named LOFs.
    if not digits or digits[0] not in "15" or not title or title.lower() == "nan":
        return None
    if "联接" in title or "LOF" in title.upper():
        return None
    if require_etf and "ETF" not in title.upper():
        return None
    row = _spot_row({"代码": digits, "名称": title})
    row["catalog_source"] = source
    return row


async def _catalog_from_source(fn_name: str, code_col: str, name_col: str,
                               *, kwargs: dict) -> list[dict]:
    fn = getattr(ak, fn_name, None)
    if fn is None:
        return []
    try:
        df = await asyncio.wait_for(asyncio.to_thread(fn, **kwargs), timeout=16)
        if df is None or df.empty or not {code_col, name_col}.issubset(df.columns):
            return []
        results = []
        for code, name in df[[code_col, name_col]].itertuples(index=False, name=None):
            row = _catalog_entry(code, name, source=fn_name)
            if row:
                results.append(row)
        return results
    except Exception as exc:
        utils.logger.warning("[etf] catalog %s unavailable: %s", fn_name, exc)
        return []


async def _refresh_catalog() -> list[dict]:
    global _catalog_cache
    async with _catalog_lock:
        # Two different public fund directories plus a separate ETF-only feed;
        # there is no manually-maintained "one ETF per theme" list.
        sina, ths, eastmoney = await asyncio.gather(
            _catalog_from_source("fund_etf_category_sina", "代码", "名称",
                                 kwargs={"symbol": "ETF基金"}),
            _catalog_from_source("fund_etf_category_ths", "基金代码", "基金名称",
                                 kwargs={"symbol": "ETF", "date": ""}),
            _catalog_from_source("fund_name_em", "基金代码", "基金简称",
                                 kwargs={}),
        )
        rows: dict[str, dict] = {}
        # Fund full names from Eastmoney help match long index descriptors.
        for row in sina + ths + eastmoney:
            if row["code"] in rows:
                old = rows[row["code"]]
                if len(row["name"]) > len(old["name"]):
                    rows[row["code"]] = row
            else:
                rows[row["code"]] = row
        if rows:
            _catalog_cache = (time.monotonic() + _CATALOG_TTL, list(rows.values()))
        else:
            # A failed refresh must not replace the last good listing.
            _catalog_cache = (time.monotonic() + _FAILURE_RETRY, _catalog_cache[1])
        return _catalog_cache[1]


async def _catalog() -> list[dict]:
    global _catalog_refresh_task
    if time.monotonic() >= _catalog_cache[0] and (
        _catalog_refresh_task is None or _catalog_refresh_task.done()
    ):
        _catalog_refresh_task = asyncio.create_task(_refresh_catalog())
    return _catalog_cache[1]


async def warm_etf_catalog() -> None:
    # Start fund identity and size discovery together; do not wait for the
    # entire fund listing before beginning slow independent size feeds.
    await _catalog()
    await _ensure_scale_refresh()
    tasks = [task for task in (_catalog_refresh_task, _scale_refresh_task) if task]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _lookup_rows(query: str) -> list[dict]:
    rows = await _catalog()
    pending = _catalog_refresh_task
    if pending is not None and not pending.done():
        try:
            # A category search must NEVER show a single warm-cache result
            # while the complete fund directory is still loading.
            await asyncio.wait_for(asyncio.shield(pending), timeout=8)
        except (asyncio.TimeoutError, Exception):
            pass
    return (_catalog_cache[1] or rows) if pending is not None else rows


def _parse_sina_scale(df: Any, now: date) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if df is None or df.empty:
        return out
    required = {"基金代码", "最近总份额", "单位净值", "更新日期"}
    if not required.issubset(df.columns):
        return out
    for _, row in df.iterrows():
        code = _digits(row.get("基金代码"))
        day = recent_day(row.get("更新日期"), 45, now)
        size = assets_yi(row.get("最近总份额"), row.get("单位净值"))
        if code and day and size is not None:
            out[code] = {"scale_yi": size, "scale_as_of": day,
                         "scale_source": "新浪基金总份额×单位净值",
                         "scale_kind": "基金资产规模估算"}
    return out


def _parse_spot_scale(df: Any, now: date) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if df is None or df.empty or not {"代码", "总市值", "数据日期"}.issubset(df.columns):
        return out
    for _, row in df.iterrows():
        code = _digits(row.get("代码"))
        day = recent_day(row.get("数据日期"), 7, now)
        market_value = _num(row.get("总市值"))
        if code and day and market_value is not None and market_value > 0:
            out[code] = {"scale_yi": round(market_value / 1e8, 4),
                         "scale_as_of": day,
                         "scale_source": "东财ETF总市值（市价估算，非基金净资产）",
                         "scale_kind": "二级市场市值近似"}
    return out


async def _scale_source(fn_name: str, kwargs: dict) -> Any:
    fn = getattr(ak, fn_name, None)
    if fn is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, **kwargs), timeout=16)
    except Exception as exc:
        utils.logger.warning("[etf] size source %s unavailable: %s", fn_name, exc)
        return None


async def _refresh_sizes() -> dict[str, dict]:
    global _scale_cache
    async with _scale_lock:
        sina, quote = await asyncio.gather(
            _scale_source("fund_scale_open_sina", {"symbol": "股票型基金"}),
            _scale_source("fund_etf_spot_em", {}),
        )
        now = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        market_proxy = _parse_spot_scale(quote, now)
        net_assets = _parse_sina_scale(sina, now)
        # Fresh fund NAV × shares is more faithful than exchange price × shares.
        combined = {**market_proxy, **net_assets}
        if combined:
            _scale_cache = (time.monotonic() + _SCALE_TTL, combined)
        else:
            _scale_cache = (time.monotonic() + _FAILURE_RETRY, _scale_cache[1])
        return _scale_cache[1]


async def _ensure_scale_refresh() -> None:
    global _scale_refresh_task
    if time.monotonic() >= _scale_cache[0] and (
        _scale_refresh_task is None or _scale_refresh_task.done()
    ):
        _scale_refresh_task = asyncio.create_task(_refresh_sizes())


async def resolve_etf(raw: str) -> dict | None:
    """Resolve one *unambiguous* fund; theme matches require user selection."""
    query = str(raw or "").strip()
    if not query:
        return None
    rows = await _lookup_rows(query)
    code = _digits(query)
    if code:
        return next((r for r in rows if r["code"] == code), None)
    partial = [r for r in rows if matches(query, r)]
    # "沪深300ETF" is an index-family request even if an issuer happens
    # to use exactly that short name. Never silently pick that issuer.
    broad_query = key(query) in {
        "黄金股", "黄金股etf", "黄金产业", "黄金产业etf",
        "黄金产业股票", "黄金产业股票etf",
        "沪深港黄金产业股票etf", "中证沪深港黄金产业股票etf",
        "沪深300", "沪深300etf", "中证沪深300etf",
    }
    if broad_query and len(partial) > 1:
        return None
    exact = [r for r in rows if r["name"].casefold() == query.casefold()]
    if len(exact) == 1:
        return exact[0]
    return partial[0] if len(partial) == 1 else None


async def search_etfs(query: str, limit: int = 40) -> list[dict]:
    q = str(query or "").strip()
    if not q:
        return []
    rows = await _lookup_rows(q)
    await _ensure_scale_refresh()
    if _scale_refresh_task is not None and not _scale_refresh_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(_scale_refresh_task), timeout=6)
        except (asyncio.TimeoutError, Exception):
            pass
    sizes = _scale_cache[1]
    enriched = [{**row, **sizes.get(row["code"], {})} for row in rows]
    selected = ranked_matches(q, enriched, limit)
    return [{"code": r["stock_code"], "name": r["name"],
             "market": r["stock_code"][:2], "asset_type": "etf",
             "scale_yi": r.get("scale_yi"), "scale_as_of": r.get("scale_as_of"),
             "scale_source": r.get("scale_source"),
             "scale_kind": r.get("scale_kind")}
            for r in selected]


def _trade_day(value: Any) -> date | None:
    """Recognize documented yyyy-mm-dd, yyyymmdd and Unix-second quote stamps."""
    if value is None or str(value).strip() in ("", "None", "nan", "-"):
        return None
    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value
    raw = str(value).strip()
    if re.fullmatch(r"\d{10}(?:\.\d+)?", raw):
        try:
            return datetime.fromtimestamp(float(raw), ZoneInfo("Asia/Shanghai")).date()
        except (ValueError, OverflowError, OSError):
            return None
    try:
        return date.fromisoformat(raw[:10]) if "-" in raw else (
            datetime.strptime(raw[:8], "%Y%m%d").date()
            if re.fullmatch(r"\d{8}", raw) else None
        )
    except ValueError:
        return None


def _valid_close_date(value: Any, today: date) -> date | None:
    d = _trade_day(value)
    # Chinese public holidays can be long, but an old price cannot be live.
    return d if d is not None and 0 <= (today - d).days <= 15 else None


def _sina_quote_row(row: Any) -> dict:
    """Sina ETF list uses sh/sz-prefixed codes and provides no quote timestamp."""
    return {
        "latest_price": _num(row.get("最新价")),
        "price_change_pct": _num(row.get("涨跌幅")),
        "turnover_yuan": _num(row.get("成交额")),
        "volume": _num(row.get("成交量")),
    }


def _select_spot_row(df: Any, code: str) -> Any | None:
    if df is None or getattr(df, "empty", True) or "代码" not in df.columns:
        return None
    for _, row in df.iterrows():
        if _digits(row.get("代码")) == code:
            return row
    return None


def _market_quote(spot: dict, row: Any, *, source: str, today: date) -> dict | None:
    """An undated spot is explicitly *not* verified as the current quote."""
    if row is None:
        return None
    parsed = (_spot_row(row) if source == "东方财富ETF实时行情"
              else _sina_quote_row(row))
    price = _num(parsed.get("latest_price"))
    if price is None or price <= 0:
        return None
    day = _trade_day(parsed.get("data_date") or parsed.get("quote_time"))
    if day and not _valid_close_date(day, today):
        return None
    observed = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    parsed["quote_source"] = source
    parsed["quote_fetch_time"] = observed
    if day == today:
        parsed["quote_mode"] = "dated_market_snapshot"
        parsed["quote_label"] = "ETF最新行情"
        parsed["quote_time"] = (str(parsed.get("quote_time") or "").strip()
                                or day.isoformat())
    elif day:
        parsed["quote_mode"] = "previous_market_snapshot"
        parsed["quote_label"] = "最近有日期的市场报价"
        parsed["quote_time"] = day.isoformat() + "（非实时）"
    else:
        parsed["quote_mode"] = "undated_market_snapshot"
        parsed["quote_label"] = "ETF行情参考价"
        parsed["quote_time"] = "来源未提供报价日期；抓取于" + observed
    return {**spot, **{k: v for k, v in parsed.items()
                       if k not in ("code", "stock_code", "name")}}


async def _fund_with_live_quote(spot: dict) -> dict:
    """Two independent ETF market feeds; missing never means ETF has no price."""
    async def load(fn, **kwargs):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, **kwargs), timeout=12
            )
        except Exception as exc:
            utils.logger.warning("[etf] quote source %s failed for %s: %s",
                                 getattr(fn, "__name__", "unknown"), spot["code"], exc)
            return None

    east, sina = await asyncio.gather(
        load(ak.fund_etf_spot_em),
        load(ak.fund_etf_category_sina, symbol="ETF基金"),
    )
    for df, provider in (
        (east, "东方财富ETF实时行情"),
        (sina, "新浪ETF场内行情"),
    ):
        match = _select_spot_row(df, spot["code"])
        quote = _market_quote(spot, match, source=provider,
                              today=datetime.now(ZoneInfo("Asia/Shanghai")).date())
        if quote:
            return quote
    return {**spot, "quote_source": "行情暂缺，基金代码来自独立名录",
            "quote_mode": "missing", "quote_label": "ETF报价暂无数据",
            "latest_price": None, "iopv": None, "iopv_premium_pct": None,
            "price_change_pct": None, "turnover_yuan": None, "volume": None,
            "data_date": "", "quote_time": ""}


def _latest_daily_close(df: Any, *, date_column: str,
                        close_column: str, today: date) -> dict:
    """Last dated close does not require 200 trading sessions of history."""
    if (df is None or getattr(df, "empty", True) or
            date_column not in df.columns or close_column not in df.columns):
        return {}
    frame = df[[date_column, close_column]].copy()
    frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
    frame[close_column] = pd.to_numeric(frame[close_column], errors="coerce")
    frame = frame.dropna().sort_values(date_column)
    frame = frame[(frame[close_column] > 0) &
                  (frame[date_column].dt.date <= today)]
    if frame.empty:
        return {}
    last = frame.iloc[-1]
    day = _valid_close_date(last[date_column].date(), today)
    if day is None:
        return {}
    return {"recent_close": round(float(last[close_column]), 4),
            "recent_close_as_of": day.isoformat()}


def _with_recent_close(fund: dict, etf_price: dict, today: date) -> dict:
    """Use an as-of-dated exchange close when the live spot is unverified."""
    price = _num((etf_price or {}).get("recent_close"))
    date_text = (etf_price or {}).get("recent_close_as_of")
    day = _valid_close_date(date_text, today)
    if price is None or price <= 0 or day is None:
        return fund
    # A market snapshot may omit its own quotation time, but it remains a
    # retrieved market price (labeled "reference", not "realtime"). Preserve
    # that observed price over yesterday's closing price.
    if fund.get("quote_mode") in ("dated_market_snapshot", "undated_market_snapshot"):
        return fund
    updated = dict(fund)
    updated.update({
        "latest_price": price,
        "quote_mode": "last_verified_daily_close",
        "quote_label": "最近交易日收盘价",
        "quote_time": day.isoformat() + " 收盘（非实时）",
        "quote_source": (etf_price.get("recent_close_source") or
                         "ETF交易所日行情"),
        "data_date": day.isoformat(),
        "iopv": None,
        "iopv_premium_pct": None,
        "price_change_pct": None,
        "turnover_yuan": None,
        "volume": None,
    })
    return updated


async def _sina_last_close(code: str, today: date) -> dict:
    """Independent Sina daily tape when Eastmoney daily candles are unavailable."""
    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(ak.fund_etf_hist_sina,
                              symbol=_etf_code(code).lower()),
            timeout=12,
        )
        close = _latest_daily_close(
            df, date_column="date", close_column="close", today=today
        )
        if close:
            close["recent_close_source"] = "新浪ETF历史日行情（未复权）"
        return close
    except Exception as exc:
        utils.logger.warning("[etf] Sina ETF close %s unavailable: %s", code, exc)
        return {}


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
        raw_code = str(row.get("股票代码") or "").strip()
        code = _digits(raw_code)
        # Hong Kong holdings may use 4-5 digits. Keep them in the disclosure,
        # but never pass them into the A-share price or earnings pipeline.
        if not code and re.fullmatch(r"[0-9]{4,5}", raw_code):
            code = raw_code.zfill(5)
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
    """Newest actually published quarter; never hardcode one ETF's top ten.

    AKShare supports date="" for the latest available year. The current
    calendar year may not yet have been published, hence both responses
    are parsed and compared by report quarter. An empty/error provider
    yields an explicit failure rather than last year's holdings as "latest".
    """
    cached = _holdings_cache.get(code)
    if cached and time.monotonic() - cached[0] < 1800:
        return cached[1]

    async def one(year: str) -> dict | None:
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(
                    ak.fund_portfolio_hold_em, symbol=code, date=year
                ), timeout=22
            )
            return _parse_holdings(df, today)
        except Exception as exc:
            utils.logger.warning("[etf] holdings %s year=%s unavailable: %s",
                                 code, year or "latest", exc)
            return None

    # "latest" is a documented selector, not an artificial stock list.
    # Validate report period because newer rows may arrive between fetches.
    latest, current = await asyncio.gather(one(""), one(str(today.year)))
    candidates = [r for r in (latest, current) if r and r.get("holdings")]
    if not candidates:
        old = await one(str(today.year - 1))
        if old:
            candidates.append(old)
    if candidates:
        result = max(candidates, key=lambda r: r.get("as_of") or "")
        result = {
            **result,
            "disclosure_as_of": (
                result["as_of"][:4] + "-" +
                {"Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31"}
                .get(result["as_of"][-2:], "")
            ),
            "retrieved_on": today.isoformat(),
            "holding_count": len(result["holdings"]),
            "scope": "最新可取得的基金定期报告前十大重仓（非盘中实时成分）",
            "source": "天天基金/东方财富：基金投资组合持仓披露",
        }
        _holdings_cache[code] = (time.monotonic(), result)
        return result
    return {
        "holdings": [], "as_of": None, "disclosure_as_of": None,
        "retrieved_on": today.isoformat(), "holding_count": 0,
        "scope": "没有取得可核实的最新定期报告持仓",
        "source": "天天基金/东方财富：基金投资组合持仓披露",
        "error": "持仓数据源不可用；不以指数实时权重或过往静态样本冒充本基金最新前十",
    }


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
        position = _position_from_prices(df, date_column="日期", close_column="收盘")
        latest = _latest_daily_close(
            df, date_column="日期", close_column="收盘", today=today
        )
        if latest:
            latest["recent_close_source"] = "东方财富ETF历史日行情（前复权收盘）"
        return {**position, **latest}
    except Exception as exc:
        utils.logger.warning("[etf] ETF history %s: %s", code, exc)
        return {}


def _hk_daily_observation(df: Any, today: date, *, source: str,
                          date_column: str, close_column: str) -> dict:
    """A dated HK closing price and (if >=200 sessions) a 52-week position.

    If the provider only has a short history, show the latest verified close
    WITHOUT inventing a complete one-year high/low.
    """
    recent = _latest_daily_close(
        df, date_column=date_column, close_column=close_column, today=today
    )
    if not recent:
        return {}
    result = {
        "price_currency": "HKD",
        "price_source": source,
        "price_as_of": recent["recent_close_as_of"],
        "price_close": recent["recent_close"],
    }
    full_window = _position_from_prices(
        df, date_column=date_column, close_column=close_column
    )
    if full_window:
        # The recent close was verified as fresh; the window covers the
        # SAME series, so the price and percentile are comparable.
        result.update(full_window)
    else:
        result["price_window_status"] = "历史数据不足200个交易日，52周位置未核实"
    return result


async def _hk_stock_price(code: str, today: date) -> dict:
    """Two independently sourced HK daily feeds; conservative date checks."""
    providers = (
        ("东方财富港股历史行情（前复权）", "stock_hk_hist",
         {"symbol": code, "period": "daily",
          "start_date": (today-timedelta(days=445)).strftime("%Y%m%d"),
          "end_date": today.strftime("%Y%m%d"), "adjust": "qfq"},
         "日期", "收盘"),
        ("新浪港股历史行情（前复权）", "stock_hk_daily",
         {"symbol": code, "adjust": "qfq"}, "date", "close"),
    )
    failure_notes = []
    for source, api, kwargs, date_col, close_col in providers:
        fn = getattr(ak, api, None)
        if not callable(fn):
            failure_notes.append(api + ":接口未安装")
            continue
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(fn, **kwargs), timeout=12
            )
            outcome = _hk_daily_observation(
                df, today, source=source,
                date_column=date_col, close_column=close_col
            )
            if outcome:
                return outcome
            failure_notes.append(api + ":无近期有效日线")
        except Exception as exc:
            failure_notes.append(api + ":" + type(exc).__name__)
            utils.logger.warning("[etf] HK holding price %s %s: %s",
                                 code, api, exc)
    # Two independent HTTP endpoints provide dated K-lines outside AKShare.
    # This addresses broker-site CDN outages and stale Sina updates.
    from analysis.etf_hk_market import get_hk_web_price
    web_price = await get_hk_web_price(code, today)
    if web_price:
        return web_price
    return {
        "price_lookup_status": "东财/新浪/腾讯/Yahoo均未返回有效近期日线",
        "price_lookup_sources": [
            "stock_hk_hist", "stock_hk_daily", "Tencent HK", "Yahoo HK"
        ],
        # Missing is not a numeric zero and never authorizes an inference.
    }


def _hk_financial_period(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() in ("nan", "none"):
        return ""
    found = re.search(r"(20\d{2})[-/.年](0?[1-9]|1[0-2])(?:[-/.月](\d{1,2}))?", raw)
    if found:
        yyyy, mm, dd = int(found[1]), int(found[2]), int(found[3] or 1)
        try:
            return date(yyyy, mm, dd).isoformat()
        except ValueError:
            pass
    return ""


def _hk_indicator_column(columns: list, *, include: tuple[str, ...],
                         exclude: tuple[str, ...] = ()) -> str | None:
    for col in columns:
        text = str(col).replace(" ", "")
        if all(part in text for part in include) and not any(part in text for part in exclude):
            return col
    return None


def _hk_financial_from_frame(df: Any, today: date) -> dict:
    """Read ACTUAL HK periodic fields only. No A-share indicator substitution."""
    if df is None or getattr(df, "empty", True):
        return {}
    frame = pd.DataFrame(df)
    cols = frame.columns.tolist()
    period_col = next(
        (col for col in cols if str(col).strip() in (
            "周期", "报告期", "报告日期", "日期", "报告时间",
            "REPORT_DATE", "REPORT_PERIOD")), None
    )
    if period_col is None:
        return {}
    rows = []
    profit_yoy = (
        _hk_indicator_column(cols, include=("归母净利润", "同比"))
        or _hk_indicator_column(cols, include=("归属母公司净利润", "同比"))
        or _hk_indicator_column(cols, include=("净利润", "同比"),
                                exclude=("非经常", "扣非"))
        or _hk_indicator_column(cols, include=("归母净利润", "增长"))
    )
    roe_col = (
        _hk_indicator_column(cols, include=("净资产收益率",),
                            exclude=("同比", "增长"))
        or _hk_indicator_column(cols, include=("股东权益回报率",),
                            exclude=("同比", "增长"))
        or _hk_indicator_column(cols, include=("ROE",), exclude=("同比",))
    )
    for _, r in frame.iterrows():
        period = _hk_financial_period(r.get(period_col))
        if not period or period > today.isoformat():
            continue
        growth = _num(str(r.get(profit_yoy)).replace("%", "").replace(",", "")) if profit_yoy else None
        roe = _num(str(r.get(roe_col)).replace("%", "").replace(",", "")) if roe_col else None
        if growth is not None or roe is not None:
            rows.append({
                "period": period, "net_profit_growth_pct": growth, "roe_pct": roe
            })
    if not rows:
        return {}
    rows = sorted(rows, key=lambda item:item["period"])
    latest = rows[-1]
    valid_growth = [x for x in rows if x.get("net_profit_growth_pct") is not None]
    result = {
        "financial_period": latest["period"],
        "financial_source": "东方财富港股财务分析（报告期）",
        "financial_evidence_kind": "报告期港股真实财务字段",
        "financial_history": rows[-8:],
        "net_profit_growth_pct": latest.get("net_profit_growth_pct"),
        "roe_pct": latest.get("roe_pct"),
    }
    if len(valid_growth) >= 2:
        recent, previous = valid_growth[-1], valid_growth[-2]
        result.update({
            "financial_period": recent["period"],
            "previous_financial_period": previous["period"],
            "previous_net_profit_growth_pct": previous["net_profit_growth_pct"],
            "growth_change_pp": round(
                recent["net_profit_growth_pct"] - previous["net_profit_growth_pct"], 2
            ),
            "growth_change_definition": "港股报告期净利润同比增速之差，非季度利润环比",
        })
    return result


_HK_PARENT_PROFIT_CODES = frozenset((
    "001025002",  # banks
    "002014002",  # insurers
    "003015002",  # securities
    "004025002",  # general industrial companies
))


def _hk_profit_from_statement(df: Any, today: date) -> dict:
    """Compute actual HK parent-profit YoY from year-to-date income statements.

    The EM report is a LONG table: REPORT_DATE / STD_ITEM_CODE /
    STD_ITEM_NAME / AMOUNT. Comparing a report only to the *same* fiscal
    period last year avoids mixing annual with semiannual cumulative profits.
    Different issuer accounting templates require different profit codes.
    """
    if df is None or getattr(df, "empty", True):
        return {}
    frame = pd.DataFrame(df)
    required = {"REPORT_DATE", "STD_ITEM_CODE", "AMOUNT"}
    if not required.issubset(frame.columns):
        return {}
    frame = frame.copy()
    frame["_report"] = pd.to_datetime(frame["REPORT_DATE"], errors="coerce")
    frame["_amount"] = pd.to_numeric(frame["AMOUNT"], errors="coerce")
    frame["_code"] = frame["STD_ITEM_CODE"].astype(str).str.strip()
    frame = frame[
        frame["_code"].isin(_HK_PARENT_PROFIT_CODES)
        & frame["_report"].notna()
        & (frame["_report"].dt.date <= today)
        & frame["_amount"].notna()
    ]
    if frame.empty:
        return {}
    money = {}
    for day, amount in zip(frame["_report"], frame["_amount"]):
        value = float(amount)
        if math.isfinite(value):
            money[day.date()] = value
    observations = []
    for period in sorted(money):
        prior = date(period.year - 1, period.month, period.day) if (
            not (period.month == 2 and period.day == 29)
        ) else None
        value = money.get(period)
        before = money.get(prior) if prior else None
        if before is None or before <= 0:
            continue
        growth = (value / before - 1.0) * 100
        if math.isfinite(growth):
            observations.append({
                "period":period.isoformat(),
                "net_profit_growth_pct": round(growth, 2),
            })
    if not observations:
        return {}
    result = {
        "financial_period": observations[-1]["period"],
        "net_profit_growth_pct": observations[-1]["net_profit_growth_pct"],
        "financial_history": observations[-8:],
        "financial_source": "东方财富港股利润表（按相同财政报告期计算股东应占溢利同比）",
        "growth_change_definition": "累计报告期归母净利同比增速变动，非单季度环比",
        "profit_currency_note": "同一数据源同一公司原始报表计量单位，计算的是百分比",
    }
    if len(observations) >= 2:
        result["previous_financial_period"] = observations[-2]["period"]
        result["previous_net_profit_growth_pct"] = observations[-2]["net_profit_growth_pct"]
        result["growth_change_pp"] = round(
            observations[-1]["net_profit_growth_pct"] -
            observations[-2]["net_profit_growth_pct"], 2
        )
    return result


async def _hk_stock_financial(code: str, today: date) -> dict:
    """Actual Hong Kong indicator feed, with income-statement YoY fallback."""
    indicator = {}
    fn = getattr(ak, "stock_financial_hk_analysis_indicator_em", None)
    if callable(fn):
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(fn, symbol=code, indicator="报告期"),
                timeout=11,
            )
            indicator = _hk_financial_from_frame(df, today)
        except Exception as exc:
            utils.logger.warning("[etf] HK financial indicator %s: %s", code, exc)
    # The indicator feed often has ROE but not an explicit net-profit-YoY
    # column; use real period statements rather than treating this as zero.
    if indicator.get("net_profit_growth_pct") is not None:
        return indicator
    report_fn = getattr(ak, "stock_financial_hk_report_em", None)
    if not callable(report_fn):
        return indicator
    try:
        statement = await asyncio.wait_for(
            asyncio.to_thread(
                report_fn, stock=code, symbol="利润表", indicator="报告期"
            ), timeout=13,
        )
        profit = _hk_profit_from_statement(statement, today)
        if profit:
            return {**indicator, **profit}
    except Exception as exc:
        utils.logger.warning("[etf] HK profit statement %s: %s", code, exc)
    return indicator


async def _hk_stock_snapshot(code: str, today: date) -> dict:
    """Latest HK F10 indicators (dividend/ROE/PE/PB) with explicit date caveat.

    This is NOT a replacement for dated profit growth or a valuation history.
    Some AKShare versions omit the REPORT_DATE column altogether.
    """
    fn = getattr(ak, "stock_hk_financial_indicator_em", None)
    if not callable(fn):
        return {}
    try:
        df = await asyncio.wait_for(
            asyncio.to_thread(fn, symbol=code), timeout=10,
        )
        if df is None or getattr(df, "empty", True):
            return {}
        row = pd.DataFrame(df).iloc[0]
        fields = {
            "roe_pct": ("股东权益回报率(%)", "ROE_AVG"),
            "pe_ttm": ("市盈率", "PE_TTM"),
            "pb": ("市净率", "PB_TTM"),
            "dividend_yield_ttm_pct": ("股息率TTM(%)", "DIVIDEND_RATE"),
            "payout_ratio_pct": ("派息比率(%)", "DIVI_RATIO"),
        }
        out = {}
        for key, aliases in fields.items():
            for label in aliases:
                if label in row.index:
                    v = _num(str(row[label]).replace("%", "").replace(",", ""))
                    if v is not None and math.isfinite(v):
                        out[key] = v
                        break
        if out:
            out["snapshot_source"] = "东方财富港股核心指标快照（财报截止日未核实）"
            out["snapshot_fetched_on"] = today.isoformat()
            out["snapshot_verified_report_period"] = False
        return out
    except Exception as exc:
        utils.logger.warning("[etf] HK F10 snapshot %s: %s", code, exc)
        return {}


def _hk_valuation_history(df: Any, *, today: date,
                          metric: str) -> dict:
    """Baidu history: current metric and percentile from dated valid points."""
    if df is None or getattr(df, "empty", True):
        return {}
    frame = pd.DataFrame(df)
    date_col = next((col for col in frame.columns
                     if str(col).lower() in ("date", "日期", "时间", "time")), None)
    value_col = next((col for col in frame.columns if str(col) in (
        "value", "值", "数值", "市盈率(TTM)", "市净率", metric)), None)
    if date_col is None or value_col is None:
        return {}
    frame = frame[[date_col, value_col]].copy()
    frame.columns = ["date", "value"]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(
        frame["value"].astype(str).str.replace(",", "", regex=False), errors="coerce"
    )
    frame = frame.dropna().sort_values("date")
    frame = frame[(frame["date"].dt.date <= today) &
                  (frame["date"].dt.date >= today-timedelta(days=440))]
    frame = frame[frame["value"] > 0]
    if frame.empty:
        return {}
    last = frame.iloc[-1]
    last_day = _valid_close_date(last["date"].date(), today)
    if not last_day:
        return {}
    out = {"latest":round(float(last["value"]), 3),
           "as_of":last_day.isoformat()}
    vals = frame.tail(260)["value"].tolist()
    if len(vals) >= 90:
        current = float(last["value"])
        out["history_position"] = {
            "years":1, "observations":len(vals),
            "percentile":round(sum(value <= current for value in vals) /
                               len(vals) * 100, 1),
        }
    return out


async def _hk_stock_valuation(code: str, today: date) -> dict:
    fn = getattr(ak, "stock_hk_valuation_baidu", None)
    if not callable(fn):
        return {}
    async def get(indicator):
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(
                    fn, symbol=code, indicator=indicator, period="近一年"
                ), timeout=10
            )
            return _hk_valuation_history(df, today=today, metric=indicator)
        except Exception as exc:
            utils.logger.warning("[etf] HK valuation %s %s unavailable: %s",
                                 code, indicator, exc)
            return {}
    pe, pb = await asyncio.gather(get("市盈率(TTM)"), get("市净率"))
    out = {}
    if pe:
        out["pe_ttm"] = pe["latest"]
        if pe.get("history_position"):
            out["pe_history_position"] = pe["history_position"]
    if pb:
        out["pb"] = pb["latest"]
        if pb.get("history_position"):
            out["pb_history_position"] = pb["history_position"]
    if pe or pb:
        out["valuation_observed_on"] = (pe or pb)["as_of"]
        out["valuation_source"] = "百度股市通港股估值历史（近一年）"
    return out


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


def _profit_period_change(periods: list[dict]) -> dict:
    """Adjacent *reported cumulative-YoY* growth rates, not single-quarter QoQ."""
    valid = sorted(
        [p for p in periods if p.get("period") and _num(p.get("net_profit_growth_pct")) is not None],
        key=lambda p: str(p.get("period")),
    )
    if not valid:
        return {}
    current = valid[-1]
    previous = valid[-2] if len(valid) > 1 else None
    growth = _num(current["net_profit_growth_pct"])
    prior = _num(previous["net_profit_growth_pct"]) if previous else None
    out = {
        "financial_period": str(current["period"]),
        "net_profit_growth_pct": growth,
        "roe_pct": _num(current.get("roe_pct")),
        "gross_margin_pct": _num(current.get("gross_margin_pct")),
        "net_margin_pct": _num(current.get("net_margin_pct")),
        "financial_history": [
            {"period": str(p.get("period") or ""),
             "net_profit_growth_pct": _num(p.get("net_profit_growth_pct")),
             "roe_pct": _num(p.get("roe_pct"))}
            for p in sorted(periods, key=lambda p: str(p.get("period") or ""))[-8:]
        ],
    }
    if previous and growth is not None and prior is not None:
        out.update({
            "previous_financial_period": str(previous["period"]),
            "previous_net_profit_growth_pct": prior,
            "growth_change_pp": round(growth-prior, 2),
            "growth_change_definition": "相邻两披露期累计口径净利同比增速变化（百分点），非单季度环比",
        })
    return out


async def _stock_profit(code: str) -> dict:
    try:
        result = await asyncio.wait_for(get_profitability_trend(code), timeout=18)
        periods = (result or {}).get("periods") or []
        return _profit_period_change(periods)
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
        "profit_and_price_lookthrough_count": len(holdings),
        "hk_price_observed_weight_pct": round(
            sum(h["weight_pct"] for h in observed if h.get("price_currency") == "HKD"), 2),
        "financial_observed_count": len(fin),
        "price_high_rule": "近252交易日前复权收盘价区间位置≥80%（不等于估值贵）",
        "price_low_rule": "近252交易日前复权收盘价区间位置≤20%（不等于估值便宜）",
        "denominator": "全部ETF净值权重；仅统计有数据的披露重仓股，不外推未覆盖成分",
    }


async def collect_etf_overview(spot: dict) -> dict:
    """Lightweight, LLM-free ETF card for both new and legacy report display.

    Quotation and latest published holdings are fetched independently. Never
    fill an ETF's historical top ten from another fund or from an index basket.
    """
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    code = spot["code"]
    holdings, etf_price, fund = await asyncio.gather(
        _holdings(code, today),
        _etf_price(code, today),
        _fund_with_live_quote(spot),
    )
    if fund.get("quote_mode") not in (
        "dated_market_snapshot", "undated_market_snapshot"
    ):
        if not _valid_close_date(etf_price.get("recent_close_as_of"), today):
            sina_close = await _sina_last_close(code, today)
            if sina_close:
                etf_price = {**etf_price, **sina_close}
        fund = _with_recent_close(fund, etf_price, today)
    return {
        "asset_type": "etf",
        "fund": fund,
        "portfolio": holdings,
        "portfolio_stats": _portfolio_stats(holdings.get("holdings") or []),
        "holding_risk": screen_holdings(fund, holdings),
        "etf_price_position": etf_price,
        "collected_on": today.isoformat(),
    }


async def collect_etf_data(spot: dict) -> dict:
    """ETF-specific look-through, macro environment and dated cycle observations."""
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    code, name = spot["code"], spot["name"]
    gold_equity = bool(_GOLD_EQUITY.search(name))
    equity = gold_equity or not bool(_EXCLUDED.search(name))
    holdings, etf_price, fund, market = await asyncio.gather(
        _holdings(code, today), _etf_price(code, today),
        _fund_with_live_quote(spot), get_etf_market_context(name, today),
    )
    # Quote availability is separate from the historical 52-week analysis.
    # Fetch a dated independent daily close only if neither quote feed nor
    # the Eastmoney daily candle supplied a usable verified recent price.
    if fund.get("quote_mode") not in ("dated_market_snapshot", "undated_market_snapshot"):
        if not _valid_close_date(etf_price.get("recent_close_as_of"), today):
            external_close = await _sina_last_close(code, today)
            if external_close:
                etf_price = {**etf_price, **external_close}
        fund = _with_recent_close(fund, etf_price, today)
    original = holdings.get("holdings") or []
    if not equity:
        for holding in original:
            holding["not_an_a_share_lookthrough"] = True

    # F10/price calls for all ten disclosed heavy stocks. Underlying public
    # data can fail independently; do not let one issuer block the full report.
    sem = asyncio.Semaphore(5)
    async def enrich(holding: dict) -> dict:
        row = dict(holding)
        code = row.get("code") or ""
        async with sem:
            if re.fullmatch(r"[0-9]{5}", code):
                # Hong Kong financial indicators have their own report
                # periods and definitions; do not use A-share F10 data.
                price, financial, valuation, snapshot = await asyncio.gather(
                    _hk_stock_price(code, today),
                    _hk_stock_financial(code, today),
                    _hk_stock_valuation(code, today),
                    _hk_stock_snapshot(code, today),
                )
                # Snapshot values fill gaps without overriding dated
                # report periods or dated valuation series.
                row.update(snapshot)
                row.update(price)
                row.update(financial)
                row.update(valuation)
            elif equity and re.fullmatch(r"(?:[0369]\d{5}|[48]\d{5})", code):
                price, profit, valuation = await asyncio.gather(
                    _stock_price(code, today),
                    _stock_profit(code),
                    _stock_valuation(code),
                )
                row.update(price)
                row.update(profit)
                row.update(valuation)
        return row

    enriched = await asyncio.gather(*(enrich(h) for h in original[:10]))
    cycle = assess_cycle(market, enriched)
    holding_risk = screen_holdings(fund, {**holdings, 'holdings': enriched})
    profile = market.get("sector") or "其他行业或混合主题"
    limitations = [
        "最近交易日收盘价与盘中最新行情分别标注，不将前一交易日收盘冒充实时价",
        "行情参考价若无源报价日期仅视为未经时点核实的快照；过期日线不用于现价展示",
        "基金季报披露重仓股不等于今日全部指数成份股，亦不是申赎篮子",
        "股价52周位置不是PE/PB估值高低，更不能独立证明周期底部或顶部",
        "Q1与H1累计净利润同比增速之差不是单季度利润环比",
        "宏观与财务指标各有披露日期，不能把滞后数据当作今日事实",
        "单次成交额、份额不能推断ETF资金净申购",
        "ETF整体PE/PB和费用必须来自基金或指数披露，不得平均重仓股编造",
        "未覆盖的重仓股、行业库存、成本和分红率不能推测为已知",
    ]
    if gold_equity:
        limitations.extend([
            "黄金股ETF持有黄金产业股票而非实物黄金；港股不套用A股财务接口",
            "黄金期货与矿企股票属于不同资产，价格走势不能直接外推矿企利润",
        ])
    elif profile == "跨行业高股息":
        limitations.extend([
            "基金历史分红率、重仓公司股息率、派息率和现金流覆盖不同，不得相互替代",
            "港股财务指标来自港股报告期接口，各公司披露频率和会计准则不同，不套用A股口径",
            "港股股息税、汇率、QDII规则须按基金和投资者适用范围核实",
            "没有可比的分红覆盖数据时，不得从高股息指数名称推导持续分红承诺",
        ])
    return {
        "asset_type": "etf",
        "scope": ("黄金产业股票ETF（披露的A股/港股重仓价格可核查，"
                  "个股财务透视仅涵盖A股）" if gold_equity
                  else "跨行业高股息股票ETF（需分行业验证分红和现金流）"
                  if profile == "跨行业高股息"
                  else "A股股票ETF" if equity
                  else "非A股股票ETF或混合资产ETF（限制适用个股透视）"),
        "fund": fund,
        "portfolio": {**holdings, "holdings": enriched},
        "portfolio_stats": _portfolio_stats(enriched),
        "etf_price_position": etf_price,
        "macro_market": market,
        "industry_cycle": cycle,
        "holding_risk": holding_risk,
        "limitations": limitations,
        "collected_on": today.isoformat(),
    }
