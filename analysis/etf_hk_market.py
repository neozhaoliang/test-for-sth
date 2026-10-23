# -*- coding: utf-8 -*-
"""Independent HK daily price feeds, used when AKShare providers fail.

Public Tencent and Yahoo endpoints require no API key. A current, dated close
is mandatory. Do not invent one-year statistics from stale or short histories.
Do not interpolate across adjusted/unadjusted prices or mix currencies.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import json
import math
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from tools.utils import utils


def _hk_symbol(code: str) -> str:
    if not code.isdigit() or len(code) != 5:
        raise ValueError("HK stock code must be exactly five decimal digits")
    return code


def _to_float(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n > 0 else None
    except (TypeError, ValueError):
        return None


def _tencent_frame(payload: dict, code: str) -> pd.DataFrame:
    key = "hk" + _hk_symbol(code)
    root = payload.get("data") or {}
    block = root.get(key) or {}
    records = block.get("qfqday") or block.get("day") or []
    output = []
    for item in records:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        value = _to_float(item[2])
        if value is not None:
            output.append((str(item[0])[:10], value))
    return pd.DataFrame(output, columns=["date", "close"])


def _yahoo_frame(payload: dict, code: str) -> pd.DataFrame:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame(columns=["date", "close"])
    entry = result[0] or {}
    stamps = entry.get("timestamp") or []
    indicators = entry.get("indicators") or {}
    quotes = indicators.get("quote") or [{}]
    closings = (quotes[0] or {}).get("close") or []
    adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
    # Prefer consistently adjusted series only when it exists for every day.
    adjusted_ok = (len(adjusted) == len(stamps) and
                   all(_to_float(v) for v in adjusted))
    values = adjusted if adjusted_ok else closings
    output = []
    for ts, value in zip(stamps, values):
        price = _to_float(value)
        if price is None:
            continue
        try:
            d = datetime.fromtimestamp(int(ts), ZoneInfo("Asia/Hong_Kong")).date()
            output.append((d.isoformat(), price))
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return pd.DataFrame(output, columns=["date", "close"])


async def _request_json(url: str, params: dict, timeout: float = 8.0):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ResearchDesk/1.0)",
                 "Accept": "application/json,text/javascript,*/*",
                 "Referer": "https://finance.qq.com/"},
        trust_env=True,
    ) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        text = response.text.strip()
        if not text.startswith("{"):
            # Tencent also supports JSONP; don't execute embedded JavaScript.
            eq = text.find("=")
            if eq >= 0:
                text = text[eq + 1:].strip().rstrip(";")
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("unexpected JSON payload")
        return result


async def get_hk_web_price(code: str, today: date) -> dict:
    """Return a fresh dated HKD close with optional 252-session position."""
    from analysis.etf_data import _hk_daily_observation
    try:
        code = _hk_symbol(code)
    except ValueError:
        return {}
    providers = (
        ("腾讯财经港股复权日K", "tencent", "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
         {"param": f"hk{code},day,,,330,qfq"}, _tencent_frame),
        ("Yahoo Finance港股复权日K", "yahoo", f"https://query1.finance.yahoo.com/v8/finance/chart/{int(code)}.HK",
         {"range": "2y", "interval": "1d"}, _yahoo_frame),
    )

    async def get(source, identifier, url, params, parser):
        try:
            payload = await asyncio.wait_for(_request_json(url, params), timeout=10)
            frame = parser(payload, code)
            observation = _hk_daily_observation(
                frame, today, source=source,
                date_column="date", close_column="close",
            )
            return observation
        except Exception as exc:
            utils.logger.warning("[etf] HK independent price %s via %s failed: %s",
                                 code, identifier, exc)
            return {}
    gathered = await asyncio.gather(*(get(*row) for row in providers))
    usable = [r for r in gathered if r]
    if not usable:
        return {}
    # Newer dated source first, then a full annual price window.
    usable.sort(key=lambda r: (
        r.get("price_as_of") or "",
        r.get("price_position_pct") is not None,
    ), reverse=True)
    return usable[0]



async def get_etf_web_price(code: str, today: date) -> dict:
    """ETF recent exchange close and 52-week position using plain Tencent HTTP.

    AKShare fund_etf_hist_sina calls MiniRacer() in-process to decrypt JS.
    Avoid that route in a multithreaded Windows uvicorn server.
    """
    digits = str(code or "").strip().upper().removeprefix("SH").removeprefix("SZ")
    if len(digits) != 6 or not digits.isdigit() or digits[0] not in "15":
        return {}
    prefix = "sh" if digits.startswith("5") else "sz"
    market_code = prefix + digits
    try:
        payload = await asyncio.wait_for(
            _request_json(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                {"param": f"{market_code},day,,,330,qfq"},
            ), timeout=10,
        )
        block = (payload.get("data") or {}).get(market_code) or {}
        records = block.get("qfqday") or block.get("day") or []
        values = []
        for item in records:
            if isinstance(item, (tuple, list)) and len(item) >= 3:
                close = _to_float(item[2])
                if close is not None:
                    values.append((str(item[0])[:10], close))
        if not values:
            return {}
        frame = pd.DataFrame(values, columns=["date", "close"])
        from analysis.etf_data import _latest_daily_close, _position_from_prices
        recent = _latest_daily_close(
            frame, date_column="date", close_column="close", today=today,
        )
        if not recent:
            return {}
        return {
            **_position_from_prices(
                frame, date_column="date", close_column="close",
            ),
            **recent,
            "recent_close_source":"腾讯ETF历史日行情（前复权，独立HTTP）",
        }
    except Exception as exc:
        utils.logger.warning(
            "[etf] independent ETF daily quote %s unavailable: %s", digits, exc
        )
        return {}
