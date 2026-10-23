# -*- coding: utf-8 -*-
"""ETF cash distributions and officially disclosed distributable profit.

These are FUND-level measures, never an average of constituent dividends.
Cash distributions / exchange price measure historic cash payout yield;
they are not underlying-index dividend yield or a forecast of total return.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from io import BytesIO
import math
import re
import time
from typing import Any

import akshare as ak
import pandas as pd
import requests
from pypdf import PdfReader

from tools.utils import utils


_CACHE_TTL = 6 * 3600
_CACHE: dict[str, tuple[float, dict]] = {}
_REPORT_ID = re.compile(r"AN\d{15,22}\Z")


def _number(value: Any) -> float | None:
    try:
        n = float(str(value).replace(",", ""))
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _day(value: Any) -> date | None:
    try:
        result = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(result) else result.date()
    except (TypeError, ValueError):
        return None


def _cash_distributions_from_cumulative(df: Any, as_of: date) -> dict:
    """Sina provides total cumulative dividends, not individual payouts."""
    if df is None or getattr(df, "empty", True):
        return {}
    if not {"日期", "累计分红"}.issubset(df.columns):
        return {}
    events: dict[date, float] = {}
    for _, row in df.iterrows():
        day, amount = _day(row["日期"]), _number(row["累计分红"])
        if day is not None and day <= as_of and amount is not None and amount >= 0:
            events[day] = amount
    if not events:
        return {}
    ordered = sorted(events.items())
    previous, payments = 0.0, []
    for day, cumulative in ordered:
        # Do not invent negative cash payouts if the provider revised its
        # cumulative series (e.g. after a corporate action).
        difference = cumulative - previous
        if difference < -0.00001:
            return {}
        if difference > 0.000001:
            payments.append((day, round(difference, 6)))
        previous = cumulative
    cutoff = as_of - timedelta(days=365)
    trailing = sum(amount for day, amount in payments if cutoff < day <= as_of)
    if not payments:
        return {}
    latest_day, latest_cash = payments[-1]
    return {
        "dividend_ttm_per_share": round(trailing, 6),
        "latest_dividend_per_share": latest_cash,
        "latest_dividend_date": latest_day.isoformat(),
        "dividend_source": "新浪ETF累计分红记录（按相邻累计差额还原每次分红）",
    }


def _parse_official_distribution(text: str) -> dict:
    """Extract only the explicit *total* distributable profit from a notice."""
    compact = re.sub(r"\s+", "", text or "")
    basis = re.search(
        r"收益分配基准日[:：]?(\d{4})年(\d{1,2})月(\d{1,2})日", compact
    )
    found = re.search(
        r"(?:基准日基金)?可供分配利润.{0,90}?((?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)(?:\.\d{2,4})?)",
        compact,
    )
    if not (basis and found):
        return {}
    try:
        as_of = date(*map(int, basis.groups()))
    except ValueError:
        return {}
    profit = _number(found.group(1))
    # Values below 100,000 yuan usually indicate that a per-share field or
    # the report's date was accidentally matched, rather than the total.
    if profit is None or profit < 1e5:
        return {}
    return {
        "distributable_profit_yuan": round(profit, 2),
        "distributable_profit_as_of": as_of.isoformat(),
    }


def _latest_official_profit(code: str, today: date) -> dict:
    fn = getattr(ak, "fund_announcement_dividend_em", None)
    if not callable(fn):
        return {}
    announcements = fn(symbol=code)
    if announcements is None or getattr(announcements, "empty", True):
        return {}
    candidates: list[tuple[date, str]] = []
    for _, item in announcements.iterrows():
        heading = str(item.get("公告标题") or "")
        notice_id = str(item.get("报告ID") or "").strip()
        published = _day(item.get("公告日期"))
        if (published is None or published > today or
            not _REPORT_ID.fullmatch(notice_id) or
            not ("分红公告" in heading or "收益分配公告" in heading)):
            continue
        candidates.append((published, notice_id))
    candidates.sort(reverse=True)
    # The first two valid announcements suffice; each is an immutable,
    # officially disclosed snapshot, never a real-time account balance.
    for published, notice_id in candidates[:2]:
        pdf_url = f"https://pdf.dfcfw.com/pdf/H2_{notice_id}_1.pdf"
        try:
            response = requests.get(
                pdf_url, headers={"User-Agent": "Mozilla/5.0"},
                timeout=6,
            )
            response.raise_for_status()
            raw = response.content
            if not raw.startswith(b"%PDF-") or len(raw) > 7 * 1024 * 1024:
                continue
            reader = PdfReader(BytesIO(raw), strict=False)
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages[:4]
            )
            data = _parse_official_distribution(text)
            if data and _day(data["distributable_profit_as_of"]) <= today:
                data["distributable_profit_source"] = "基金管理人收益分配公告"
                data["distributable_profit_announcement_date"] = published.isoformat()
                data["distributable_profit_source_url"] = pdf_url
                return data
        except Exception as exc:
            utils.logger.warning("[etf] distribution notice %s: %s", code, exc)
    return {}


def _fetch_source_data(code: str, as_of: date) -> dict:
    result: dict = {}
    dividend_fn = getattr(ak, "fund_etf_dividend_sina", None)
    if callable(dividend_fn):
        try:
            symbol = ("sh" if code.startswith("5") else "sz") + code
            result.update(_cash_distributions_from_cumulative(
                dividend_fn(symbol=symbol), as_of,
            ))
        except Exception as exc:
            utils.logger.warning("[etf] dividend history %s: %s", code, exc)
    try:
        result.update(_latest_official_profit(code, as_of))
    except Exception as exc:
        utils.logger.warning("[etf] official distributable profit %s: %s", code, exc)
    return result


async def collect_etf_distributions(
    code: str, fund: dict, as_of: date,
) -> dict:
    """Fetch with bounded wait; cache reports but recalculate yield at price."""
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return {}
    cached = _CACHE.get(code)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL:
        facts = dict(cached[1])
    else:
        try:
            facts = await asyncio.wait_for(
                asyncio.to_thread(_fetch_source_data, code, as_of), timeout=16,
            )
        except Exception as exc:
            utils.logger.warning("[etf] payout data %s: %s", code, exc)
            facts = {}
        _CACHE[code] = (time.monotonic(), dict(facts))
    # The exchange-traded price is the investor's purchase price, rather
    # than a fund NAV, index level or constituent average.
    price = _number(fund.get("latest_price"))
    if price is not None and price > 0:
        dividend = _number(facts.get("dividend_ttm_per_share"))
        if dividend is not None:
            facts["dividend_yield_ttm_pct"] = round(dividend / price * 100, 3)
            facts["dividend_yield_reference_price"] = price
            facts["dividend_yield_price_date"] = (
                fund.get("quote_time") or fund.get("data_date") or
                "报价日期未核实"
            )
    return facts
