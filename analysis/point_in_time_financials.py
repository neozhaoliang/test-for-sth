# -*- coding: utf-8 -*-
"""
Point-in-time financial facts from exact CNINFO periodic-report PDFs.

Historical mode must not use today's reconstructed F10 history.  This adapter reads the
latest filing version that was actually published by as_of and extracts a deliberately
small set of standardized metrics from the original PDF text.

Scope is intentionally conservative:
- revenue
- attributable net profit
- operating cash flow
- weighted-average ROE
- total assets
- total liabilities when extractable

Missing fields stay None.  The parser never substitutes values from a later filing.
"""

from __future__ import annotations

import asyncio
import io
import re
from datetime import date
from typing import Dict, List, Optional

from pypdf import PdfReader

from analysis.filing_archive import _download_pdf
from analysis.filing_calendar import (
    get_financial_filing_calendar,
    latest_available_filing_by_period,
)


_PARSE_TIMEOUT_S = 60
_MAX_FILINGS = 8


def _clean(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _num(text: str) -> Optional[float]:
    raw = str(text or "").replace(",", "").replace("，", "").strip()
    if raw in {"", "-", "--", "—", "不适用"}:
        return None
    # Parentheses in financial statements mean negative.
    neg = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    try:
        value = float(raw)
    except ValueError:
        return None
    return -value if neg else value


def _first_number_after(text: str, labels: List[str]) -> Optional[float]:
    for label in labels:
        # Keep the match local to one logical line / short PDF-text run so we do not jump
        # to an unrelated later table value.
        pattern = (
            re.escape(label)
            + r"[^\d()\-]{0,45}"
            + r"([()\-—]?[\d,]+(?:\.\d+)?\)?)"
        )
        m = re.search(pattern, text)
        if m:
            value = _num(m.group(1))
            if value is not None:
                return value
    return None


def _first_percent_after(text: str, labels: List[str]) -> Optional[float]:
    for label in labels:
        m = re.search(
            re.escape(label)
            + r"[^\d()\-]{0,60}"
            + r"([()\-—]?[\d,]+(?:\.\d+)?\)?)\s*%",
            text,
        )
        if m:
            return _num(m.group(1))
    return None


def _extract_relevant_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: List[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text:
            continue
        if any(
            key in text
            for key in (
                "主要会计数据和财务指标",
                "营业收入",
                "归属于上市公司股东的净利润",
                "经营活动产生的现金流量净额",
                "合并资产负债表",
                "资产总计",
                "负债合计",
            )
        ):
            pages.append(text)
    return _clean("\n".join(pages))


def parse_financial_report_text(text: str) -> Dict:
    text = _clean(text)
    revenue = _first_number_after(text, ["营业收入"])
    net_profit = _first_number_after(
        text,
        [
            "归属于上市公司股东的净利润",
            "归属于母公司所有者的净利润",
        ],
    )
    ocf = _first_number_after(text, ["经营活动产生的现金流量净额"])
    roe = _first_percent_after(
        text,
        ["加权平均净资产收益率", "净资产收益率"],
    )
    total_assets = _first_number_after(
        text,
        ["资产总计", "总资产"],
    )
    total_liabilities = _first_number_after(
        text,
        ["负债合计", "总负债"],
    )

    net_margin = (
        round(net_profit / revenue * 100, 3)
        if revenue not in (None, 0) and net_profit is not None
        else None
    )
    debt_ratio = (
        round(total_liabilities / total_assets * 100, 3)
        if total_assets not in (None, 0) and total_liabilities is not None
        else None
    )
    cash_to_profit = (
        round(ocf / net_profit, 3)
        if net_profit not in (None, 0) and ocf is not None
        else None
    )

    return {
        "revenue": revenue,
        "net_profit": net_profit,
        "operating_cash_flow": ocf,
        "roe_pct": roe,
        "net_margin_pct": net_margin,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "debt_ratio_pct": debt_ratio,
        "cash_to_profit_ratio": cash_to_profit,
    }


async def _fetch_and_parse(item: Dict) -> Optional[Dict]:
    url = str(item.get("url") or "")
    if not url:
        return None
    body = await _download_pdf(url)
    if not body:
        return None
    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(_extract_relevant_text, body),
            timeout=_PARSE_TIMEOUT_S,
        )
    except Exception:
        return None
    metrics = parse_financial_report_text(text)
    if not any(v is not None for v in metrics.values()):
        return None
    return {
        "period": str(item.get("period") or ""),
        "published_at": str(item.get("published_at") or ""),
        "report_type": str(item.get("report_type") or ""),
        "title": str(item.get("title") or ""),
        "url": url,
        "announcement_id": str(item.get("announcement_id") or ""),
        **metrics,
    }


async def get_point_in_time_financials(
    stock_code: str,
    as_of: date,
    *,
    lookback_years: int = 6,
) -> Optional[Dict]:
    calendar = await get_financial_filing_calendar(
        stock_code,
        as_of=as_of,
        lookback_years=lookback_years,
    )
    selected = latest_available_filing_by_period(calendar, as_of)
    rows = sorted(
        selected.values(),
        key=lambda x: (str(x.get("period") or ""), str(x.get("published_at") or "")),
        reverse=True,
    )[:_MAX_FILINGS]

    parsed: List[Dict] = []
    # Serial on purpose: PDFs are large and CNINFO should not be hammered.
    for item in rows:
        result = await _fetch_and_parse(item)
        if result:
            parsed.append(result)

    if not parsed:
        return None

    parsed.sort(key=lambda x: x["period"])
    latest = parsed[-1]
    periods = [
        {
            "period": x["period"],
            "published_at": x["published_at"],
            "net_margin_pct": x.get("net_margin_pct"),
            "roe_pct": x.get("roe_pct"),
            "debt_ratio_pct": x.get("debt_ratio_pct"),
            "cash_to_profit_ratio": x.get("cash_to_profit_ratio"),
            "revenue": x.get("revenue"),
            "net_profit": x.get("net_profit"),
            "operating_cash_flow": x.get("operating_cash_flow"),
        }
        for x in parsed
    ]

    return {
        "as_of": as_of.isoformat(),
        "latest_period": latest["period"],
        "latest_published_at": latest["published_at"],
        "latest": latest,
        "periods": periods,
        "source_tier": "S",
        "source": "CNINFO original periodic-report PDFs",
    }



def to_historical_fundamentals(data: Optional[Dict]) -> Optional[Dict]:
    """Map point-in-time filing facts into the report's fundamentals contract."""
    if not data:
        return None
    latest = data.get("latest") or {}
    facts = {
        "finance_period": latest.get("period"),
        "revenue": latest.get("revenue"),
        "net_profit": latest.get("net_profit"),
        "operating_cash_flow": latest.get("operating_cash_flow"),
        "cash_to_profit_ratio": latest.get("cash_to_profit_ratio"),
        # Historical PDF parser intentionally does not pretend to know fields it did not
        # extract from the exact filing version.
        "rd_investment_yuan": None,
        "rd_intensity_pct": None,
        "accounts_receivable_yuan": None,
        "inventory_yuan": None,
        "sw_industry": None,
    }
    missing = []
    for field, label in (
        ("revenue", "营业收入"),
        ("net_profit", "归母净利润"),
        ("operating_cash_flow", "经营现金流"),
    ):
        if facts.get(field) is None:
            missing.append(label)
    missing.extend(
        [
            "客户/供应商集中度(历史原始财报解析暂未覆盖)",
            "专利/研发结构(历史原始财报解析暂未覆盖)",
            "申万行业(历史原始财报解析暂未覆盖)",
        ]
    )
    url = latest.get("url")
    return {
        "facts": facts,
        "valuation": None,
        "management_narrative": "",
        "self_disclosed_risks": "",
        "top_customers": [],
        "top_suppliers": [],
        "major_events": [],
        "refinancing_history": [],
        "executive_profile": None,
        "governance_alerts": [],
        "missing": missing,
        "sources": [url] if url else [],
        "source_map": {"finance": url} if url else {},
        "point_in_time": True,
        "available_at": latest.get("published_at"),
    }


def _trend_note(periods: List[Dict], key: str, label: str) -> str:
    usable = [x for x in periods if x.get(key) is not None]
    if len(usable) < 2:
        return f"{label}数据不足"
    first, last = usable[0], usable[-1]
    a, b = float(first[key]), float(last[key])
    diff = b - a
    direction = "上升" if diff > 0.5 else ("下降" if diff < -0.5 else "基本持平")
    return (
        f"{label}从 {a:.2f}% {direction}至 {b:.2f}% "
        f"(区间 {first['period']}~{last['period']})"
    )


def to_historical_profitability(data: Optional[Dict]) -> Optional[Dict]:
    """Map exact filing metrics into the existing profitability-trend contract."""
    if not data:
        return None
    periods = data.get("periods") or []
    if not periods:
        return None
    return {
        "periods": periods,
        "gross_margin_trend_note": "毛利率历史原始财报解析暂未覆盖",
        "net_margin_trend_note": _trend_note(periods, "net_margin_pct", "净利率"),
        "roe_trend_note": _trend_note(periods, "roe_pct", "ROE"),
        "debt_ratio_trend_note": _trend_note(periods, "debt_ratio_pct", "资产负债率"),
        "point_in_time": True,
        "as_of": data.get("as_of"),
        "source_tier": "S",
    }
