# -*- coding: utf-8 -*-
"""
Primary-source adapters for investment research.

Phase 2 starts with CNINFO because it is the designated disclosure platform for A-share
announcements and AkShare already exposes a maintained adapter.  Failures are intentionally
soft: primary-source enrichment must improve a report, never make the whole report fail.

The returned records are small metadata cards (title/date/category/link), not downloaded
announcement bodies.  Later phases can add PDF/text extraction behind the same contract.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import akshare as ak

from tools.utils import utils


_PRIMARY_CATEGORIES = (
    "公司治理",
    "权益分派",
    "增发",
    "配股",
    "股权激励",
    "股权变动",
    "风险提示",
    "补充更正",
)

_MAX_PER_CATEGORY = 20
_LOOKBACK_YEARS = 5
_CATEGORY_TIMEOUT_S = 30
_CONCURRENCY = 3


def _bare_code(stock_code: str) -> str:
    return re.sub(r"\D", "", stock_code)[-6:]


def _is_a_share_code(code6: str) -> bool:
    if len(code6) != 6 or not code6.isdigit():
        return False
    return code6.startswith(("0", "3", "6", "4", "8", "9"))


def _pick(row, *names):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


async def _fetch_category(
    code6: str,
    category: str,
    start_date: str,
    end_date: str,
    semaphore: asyncio.Semaphore,
) -> List[Dict]:
    async with semaphore:
        try:
            df = await asyncio.wait_for(
                asyncio.to_thread(
                    ak.stock_zh_a_disclosure_report_cninfo,
                    symbol=code6,
                    market="沪深京",
                    keyword="",
                    category=category,
                    start_date=start_date,
                    end_date=end_date,
                ),
                timeout=_CATEGORY_TIMEOUT_S,
            )
        except Exception as e:
            # 2026 年 AkShare/CNINFO 曾出现过上游字段改版；这里必须软失败。
            utils.logger.warning(
                f"[primary_sources] CNINFO {code6} {category} 获取失败: "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
            return []

    if df is None or df.empty:
        return []

    records: List[Dict] = []
    for _, row in df.head(_MAX_PER_CATEGORY).iterrows():
        title = _pick(row, "公告标题", "announcementTitle")
        published_at = _pick(row, "公告时间", "announcementTime")
        url = _pick(row, "公告链接", "announcementUrl", "url")
        name = _pick(row, "简称", "secName")
        if not title:
            continue
        records.append(
            {
                "source_name": "巨潮资讯",
                "source_tier": "S",
                "kind": "fact",
                "category": category,
                "title": str(title),
                "published_at": str(published_at or ""),
                "url": str(url or "") or None,
                "stock_name": str(name or ""),
            }
        )
    return records


async def get_cninfo_primary_evidence(
    stock_code: str,
    *,
    lookback_years: int = _LOOKBACK_YEARS,
) -> List[Dict]:
    """
    Fetch recent official disclosure metadata from CNINFO for governance/capital-allocation
    categories that materially affect the investment thesis.

    Returns [] for non-A-share codes, network failures, upstream schema changes, or no data.
    """
    code6 = _bare_code(stock_code)
    if not _is_a_share_code(code6):
        return []

    now = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        start = now.replace(year=now.year - lookback_years)
    except ValueError:
        # Feb 29 -> Feb 28 in a non-leap start year.
        start = now - timedelta(days=365 * lookback_years)

    start_date = start.strftime("%Y%m%d")
    end_date = now.strftime("%Y%m%d")
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    groups = await asyncio.gather(
        *(
            _fetch_category(code6, category, start_date, end_date, semaphore)
            for category in _PRIMARY_CATEGORIES
        )
    )

    # De-duplicate the same announcement appearing in related categories.
    dedup: Dict[str, Dict] = {}
    for group in groups:
        for item in group:
            key = item.get("url") or (
                f"{item.get('published_at','')}|{item.get('title','')}"
            )
            if key not in dedup:
                dedup[key] = item

    records = list(dedup.values())
    records.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    utils.logger.info(
        f"[primary_sources] {stock_code} CNINFO primary evidence: {len(records)} records"
    )
    return records
