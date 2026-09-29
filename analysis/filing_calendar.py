# -*- coding: utf-8 -*-
"""
Official CNINFO filing calendar for A-share periodic reports.

This module answers one narrow historical question:
"When did the market first receive a report for period P?"

It intentionally does NOT make today's third-party financial history point-in-time safe:
historical rows may later be restated.  The calendar is groundwork for fetching the exact
original filing version that was available at a historical as_of.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from analysis.primary_sources import (
    _CNINFO_HEADERS,
    _DIRECT_TIMEOUT_S,
    _announcement_url,
    _format_cninfo_time,
    _get_org_id,
)

logger = logging.getLogger("MediaCrawler")

_REPORT_CATEGORIES = {
    "annual": "category_ndbg_szsh",
    "semiannual": "category_bndbg_szsh",
    "q1": "category_yjdbg_szsh",
    "q3": "category_sjdbg_szsh",
}

_REPORT_PAGE_SIZE = 30
_CONCURRENCY = 4


def _bare_code(stock_code: str) -> str:
    return re.sub(r"\D", "", stock_code)[-6:]


def _period_from_title(title: str, report_type: str) -> Optional[str]:
    clean = re.sub(r"</?em>", "", title or "").strip()
    m = re.search(r"(20\d{2})年", clean)
    if not m:
        return None
    year = int(m.group(1))
    suffix = {
        "annual": "12-31",
        "semiannual": "06-30",
        "q1": "03-31",
        "q3": "09-30",
    }.get(report_type)
    return f"{year}-{suffix}" if suffix else None


def _is_full_report(title: str, report_type: str) -> bool:
    clean = re.sub(r"</?em>", "", title or "").strip()
    if any(x in clean for x in ("摘要", "英文版", "审计报告", "取消披露")):
        return False
    patterns = {
        "annual": r"20\d{2}年年度报告(?:（[^）]+）)?$",
        "semiannual": r"20\d{2}年半年度报告(?:（[^）]+）)?$",
        "q1": r"20\d{2}年第一季度报告(?:（[^）]+）)?$",
        "q3": r"20\d{2}年第三季度报告(?:（[^）]+）)?$",
    }
    pattern = patterns.get(report_type)
    return bool(pattern and re.search(pattern, clean))


def _published_date(value) -> Optional[date]:
    text = _format_cninfo_time(value)
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


async def _fetch_type(
    client,
    code6: str,
    org_id: str,
    report_type: str,
    start: date,
    end: date,
    semaphore: asyncio.Semaphore,
) -> List[Dict]:
    category = _REPORT_CATEGORIES[report_type]
    payload = {
        "pageNum": "1",
        "pageSize": str(_REPORT_PAGE_SIZE),
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{code6},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": category,
        "trade": "",
        "seDate": f"{start:%Y-%m-%d}~{end:%Y-%m-%d}",
        "sortName": "time",
        "sortType": "desc",
        "isHLtitle": "true",
    }
    async with semaphore:
        try:
            resp = await client.post(
                "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                data=payload,
                timeout=_DIRECT_TIMEOUT_S,
            )
            resp.raise_for_status()
            announcements = (resp.json() or {}).get("announcements") or []
        except Exception as e:
            logger.warning(
                f"[filing_calendar] {code6} {report_type} failed: "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
            return []

    rows: List[Dict] = []
    for item in announcements:
        title = re.sub(
            r"</?em>",
            "",
            str(item.get("announcementTitle") or ""),
        ).strip()
        if not _is_full_report(title, report_type):
            continue
        published = _published_date(item.get("announcementTime"))
        period = _period_from_title(title, report_type)
        if not published or not period or published > end:
            continue
        rows.append(
            {
                "report_type": report_type,
                "period": period,
                "published_at": published.isoformat(),
                "title": title,
                "url": _announcement_url(item),
                "announcement_id": str(item.get("announcementId") or ""),
            }
        )
    return rows


async def get_financial_filing_calendar(
    stock_code: str,
    *,
    as_of: Optional[date] = None,
    lookback_years: int = 6,
) -> List[Dict]:
    code6 = _bare_code(stock_code)
    if len(code6) != 6:
        return []

    end = as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        start = end.replace(year=end.year - lookback_years)
    except ValueError:
        start = end - timedelta(days=365 * lookback_years)

    import httpx

    async with httpx.AsyncClient(
        headers=_CNINFO_HEADERS,
        follow_redirects=True,
        timeout=_DIRECT_TIMEOUT_S,
    ) as client:
        org_id = await _get_org_id(client, code6)
        if not org_id:
            return []
        sem = asyncio.Semaphore(_CONCURRENCY)
        groups = await asyncio.gather(
            *(
                _fetch_type(
                    client,
                    code6,
                    org_id,
                    report_type,
                    start,
                    end,
                    sem,
                )
                for report_type in _REPORT_CATEGORIES
            )
        )

    # Same period may have original + revised versions. Keep every version so historical
    # reconstruction can choose the latest version that was actually available by as_of.
    rows = [item for group in groups for item in group]
    rows.sort(key=lambda x: (x["period"], x["published_at"]))
    return rows


def latest_available_filing_by_period(
    calendar: List[Dict],
    as_of: date,
) -> Dict[str, Dict]:
    """
    For each report period, return the latest filing version published on/before as_of.
    """
    out: Dict[str, Dict] = {}
    cutoff = as_of.isoformat()
    for item in calendar:
        published = str(item.get("published_at") or "")
        period = str(item.get("period") or "")
        if not period or not published or published > cutoff:
            continue
        old = out.get(period)
        if old is None or published >= str(old.get("published_at") or ""):
            out[period] = item
    return out
