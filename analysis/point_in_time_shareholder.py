# -*- coding: utf-8 -*-
"""
Point-in-time shareholder-count trend from exact CNINFO periodic-report PDFs.

Strict historical research reads "报告期末普通股股东总数" only from original filing versions
published by the cutoff date, rather than from today's reconstructed historical database.
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

_PARSE_TIMEOUT_S = 45


def _num(text: str) -> Optional[int]:
    raw = str(text or "").replace(",", "").replace("，", "").strip()
    if not raw:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        return None
    return value if value >= 0 else None


def _extract_relevant_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: List[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if "股东总数" in text or "普通股股东" in text:
            pages.append(text)
    return "\n".join(pages)


def parse_shareholder_count_text(text: str) -> Optional[int]:
    if not text:
        return None
    normalized = text.replace("\u3000", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    patterns = (
        r"报告期末普通股股东总数(?:（户）|\(户\)|（如有）)?\s*[：:]?\s*([\d,，]+)",
        r"期末普通股股东总数(?:（户）|\(户\))?\s*[：:]?\s*([\d,，]+)",
        r"普通股股东总数\s*[：:]?\s*([\d,，]+)",
    )
    for pattern in patterns:
        m = re.search(pattern, normalized)
        if m:
            value = _num(m.group(1))
            if value is not None:
                return value
    return None


async def _fetch_count(item: Dict) -> Optional[Dict]:
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
    count = parse_shareholder_count_text(text)
    if count is None:
        return None
    published_at = str(item.get("published_at") or "")
    return {
        "period": str(item.get("period") or ""),
        "published_at": published_at,
        "available_at": published_at,
        "holders": count,
        "url": url,
        "title": str(item.get("title") or ""),
    }


async def get_point_in_time_shareholder_trend(
    stock_code: str,
    as_of: date,
    *,
    lookback_years: int = 4,
) -> Optional[Dict]:
    calendar = await get_financial_filing_calendar(
        stock_code,
        as_of=as_of,
        lookback_years=lookback_years,
    )
    selected = latest_available_filing_by_period(calendar, as_of)
    filings = sorted(
        selected.values(),
        key=lambda x: (str(x.get("period") or ""), str(x.get("published_at") or "")),
        reverse=True,
    )[:8]

    rows: List[Dict] = []
    for item in filings:
        parsed = await _fetch_count(item)
        if parsed:
            rows.append(parsed)

    if not rows:
        return None
    rows.sort(key=lambda x: x["period"])
    latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else None

    change_pct = None
    if previous and previous.get("holders"):
        change_pct = round(
            (latest["holders"] - previous["holders"]) / previous["holders"] * 100,
            3,
        )

    return {
        "latest_count": latest["holders"],
        "change_pct": change_pct,
        "trend": (
            "increasing"
            if change_pct is not None and change_pct > 0
            else "decreasing"
            if change_pct is not None and change_pct < 0
            else "stable"
            if change_pct == 0
            else "unknown"
        ),
        "as_of": latest["period"],
        "period": latest["period"],
        "published_at": latest["published_at"],
        "available_at": latest["available_at"],
        "series": rows,
        "source_mode": "cninfo_original_filings",
        "source_tier": "S",
        "source_url": latest["url"],
        "point_in_time": True,
    }
