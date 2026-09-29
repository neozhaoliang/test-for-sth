# -*- coding: utf-8 -*-
"""
Primary-source adapters for investment research.

CNINFO is queried directly first so we can cap each category at one recent page instead of
letting a wrapper paginate years of announcements.  AkShare is retained only as a soft
fallback for categories whose direct request fails.

Returned records are metadata cards (title/date/category/link), not interpreted conclusions.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import akshare as ak
import httpx

from tools.utils import utils


_PRIMARY_CATEGORIES = (
    "公司治理",
    "权益分派",
    "增发",
    "配股",
    "股权激励",
    "股权变动",
    "可转债",
    "其他融资",
    "解禁",
    "风险提示",
    "补充更正",
    "澄清致歉",
    "特别处理和退市",
)

_CNINFO_CATEGORY_IDS = {
    "公司治理": "category_gszl_szsh",
    "权益分派": "category_qyfpxzcs_szsh",
    "增发": "category_zf_szsh",
    "配股": "category_pg_szsh",
    "股权激励": "category_gqjl_szsh",
    "股权变动": "category_gqbd_szsh",
    "可转债": "category_kzzq_szsh",
    "其他融资": "category_qtrz_szsh",
    "解禁": "category_jj_szsh",
    "风险提示": "category_fxts_szsh",
    "补充更正": "category_bcgz_szsh",
    "澄清致歉": "category_cqdq_szsh",
    "特别处理和退市": "category_tbclts_szsh",
}

_MAX_PER_CATEGORY = 20
_LOOKBACK_YEARS = 5
_DIRECT_TIMEOUT_S = 15
_FALLBACK_TIMEOUT_S = 25
_CONCURRENCY = 4

_CNINFO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    "Origin": "https://www.cninfo.com.cn",
    "X-Requested-With": "XMLHttpRequest",
}


def _bare_code(stock_code: str) -> str:
    return re.sub(r"\D", "", stock_code)[-6:]


def _is_a_share_code(code6: str) -> bool:
    if len(code6) != 6 or not code6.isdigit():
        return False
    return code6.startswith(("0", "3", "6", "4", "8", "9"))


def _pick(row, *names):
    for name in names:
        if name not in row:
            continue
        value = row[name]
        if value is None or value == "":
            continue
        try:
            if value != value:  # NaN
                continue
        except Exception:
            pass
        return value
    return None


def _format_cninfo_time(value) -> str:
    if value in (None, ""):
        return ""
    try:
        ts = int(value)
        return datetime.fromtimestamp(
            ts / 1000, tz=ZoneInfo("Asia/Shanghai")
        ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(value)


def _announcement_url(item: Dict) -> Optional[str]:
    adjunct = str(item.get("adjunctUrl") or "").strip().lstrip("/")
    if adjunct:
        return f"https://static.cninfo.com.cn/{adjunct}"

    code = str(item.get("secCode") or "")
    ann_id = str(item.get("announcementId") or "")
    org_id = str(item.get("orgId") or "")
    published = _format_cninfo_time(item.get("announcementTime"))
    if code and ann_id and org_id:
        return (
            "https://www.cninfo.com.cn/new/disclosure/detail?"
            f"stockCode={code}&announcementId={ann_id}&orgId={org_id}"
            f"&announcementTime={published}"
        )
    return None


async def _get_org_id(client: httpx.AsyncClient, code6: str) -> Optional[str]:
    try:
        resp = await client.get(
            "https://www.cninfo.com.cn/new/data/szse_stock.json",
            timeout=_DIRECT_TIMEOUT_S,
        )
        resp.raise_for_status()
        rows = (resp.json() or {}).get("stockList") or []
    except Exception as e:
        utils.logger.warning(
            f"[primary_sources] CNINFO stock map failed: {type(e).__name__}: {str(e)[:140]}"
        )
        return None

    item = next((x for x in rows if str(x.get("code") or "") == code6), None)
    return str((item or {}).get("orgId") or "") or None


async def _fetch_category_direct(
    client: httpx.AsyncClient,
    code6: str,
    org_id: str,
    category: str,
    start_date: str,
    end_date: str,
    semaphore: asyncio.Semaphore,
) -> Tuple[bool, List[Dict]]:
    """
    Return (request_succeeded, records).  Empty records with success=True means CNINFO
    genuinely returned no announcements; only request failures should trigger AkShare fallback.
    """
    category_id = _CNINFO_CATEGORY_IDS[category]
    payload = {
        "pageNum": "1",
        "pageSize": str(max(30, _MAX_PER_CATEGORY)),
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{code6},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": category_id,
        "trade": "",
        "seDate": (
            f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}~"
            f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
        ),
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
            data = resp.json() or {}
            announcements = data.get("announcements") or []
        except Exception as e:
            utils.logger.warning(
                f"[primary_sources] direct CNINFO {code6} {category} failed: "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
            return False, []

    records: List[Dict] = []
    for item in announcements[:_MAX_PER_CATEGORY]:
        title = re.sub(r"</?em>", "", str(item.get("announcementTitle") or "")).strip()
        if not title:
            continue
        records.append(
            {
                "source_name": "巨潮资讯",
                "source_tier": "S",
                "kind": "fact",
                "category": category,
                "title": title,
                "published_at": _format_cninfo_time(item.get("announcementTime")),
                "url": _announcement_url(item),
                "stock_name": str(item.get("secName") or ""),
                "announcement_id": str(item.get("announcementId") or ""),
                "acquisition": "direct_cninfo",
            }
        )
    return True, records


async def _fetch_category_fallback(
    code6: str,
    category: str,
    start_date: str,
    end_date: str,
    semaphore: asyncio.Semaphore,
) -> List[Dict]:
    """AkShare fallback; timeout is defensive but a running thread cannot be force-cancelled."""
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
                timeout=_FALLBACK_TIMEOUT_S,
            )
        except Exception as e:
            utils.logger.warning(
                f"[primary_sources] AkShare fallback {code6} {category} failed: "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
            return []

    if df is None or df.empty:
        return []

    records: List[Dict] = []
    for _, row in df.head(_MAX_PER_CATEGORY).iterrows():
        title = _pick(row, "公告标题", "announcementTitle")
        if not title:
            continue
        records.append(
            {
                "source_name": "巨潮资讯",
                "source_tier": "S",
                "kind": "fact",
                "category": category,
                "title": re.sub(r"</?em>", "", str(title)).strip(),
                "published_at": str(_pick(row, "公告时间", "announcementTime") or ""),
                "url": str(_pick(row, "公告链接", "announcementUrl", "url") or "") or None,
                "stock_name": str(_pick(row, "简称", "secName") or ""),
                "acquisition": "akshare_fallback",
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

    Direct CNINFO requests fetch only the first recent page per category.  AkShare is called
    only for categories whose direct request fails.  All failures are soft.
    """
    code6 = _bare_code(stock_code)
    if not _is_a_share_code(code6):
        return []

    now = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        start = now.replace(year=now.year - lookback_years)
    except ValueError:
        start = now - timedelta(days=365 * lookback_years)

    start_date = start.strftime("%Y%m%d")
    end_date = now.strftime("%Y%m%d")
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        headers=_CNINFO_HEADERS,
        follow_redirects=True,
        timeout=_DIRECT_TIMEOUT_S,
    ) as client:
        org_id = await _get_org_id(client, code6)
        direct_results: List[Tuple[bool, List[Dict]]] = []
        if org_id:
            direct_results = await asyncio.gather(
                *(
                    _fetch_category_direct(
                        client,
                        code6,
                        org_id,
                        category,
                        start_date,
                        end_date,
                        semaphore,
                    )
                    for category in _PRIMARY_CATEGORIES
                )
            )
        else:
            direct_results = [(False, []) for _ in _PRIMARY_CATEGORIES]

    groups: List[List[Dict]] = [[] for _ in _PRIMARY_CATEGORIES]
    fallback_sem = asyncio.Semaphore(3)
    fallback_jobs = []
    fallback_indices = []
    for idx, (category, (success, records)) in enumerate(
        zip(_PRIMARY_CATEGORIES, direct_results)
    ):
        if success:
            groups[idx] = records
        else:
            fallback_indices.append(idx)
            fallback_jobs.append(
                _fetch_category_fallback(
                    code6, category, start_date, end_date, fallback_sem
                )
            )
    if fallback_jobs:
        fallback_results = await asyncio.gather(*fallback_jobs)
        for idx, records in zip(fallback_indices, fallback_results):
            groups[idx] = records

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
    direct_n = sum(1 for x in records if x.get("acquisition") == "direct_cninfo")
    fallback_n = sum(1 for x in records if x.get("acquisition") == "akshare_fallback")
    utils.logger.info(
        f"[primary_sources] {stock_code} CNINFO evidence: {len(records)} "
        f"(direct={direct_n}, fallback={fallback_n})"
    )
    return records
