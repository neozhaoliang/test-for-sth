# -*- coding: utf-8 -*-
"""
R&D team composition from the latest official CNINFO annual report.

The parser is deliberately narrow: it extracts only the standardized "研发人员情况表"
facts (headcount, ratio, education/age structure).  It never infers talent quality from a
company narrative.  Network/PDF failures return None and leave the dimension explicitly
missing.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo


logger = logging.getLogger("MediaCrawler")

_QUERY_TIMEOUT_S = 25
_PDF_TIMEOUT_S = 45
_PARSE_TIMEOUT_S = 75
_MAX_PDF_BYTES = 40 * 1024 * 1024


def _bare_code(stock_code: str) -> str:
    return re.sub(r"\D", "", stock_code)[-6:]


def _clean_title(title: str) -> str:
    return re.sub(r"</?em>", "", title or "").strip()


def _is_main_annual_report(title: str) -> bool:
    title = _clean_title(title)
    if any(x in title for x in ("摘要", "英文版", "审计报告", "内部控制", "提示性公告")):
        return False
    return bool(re.search(r"\d{4}年年度报告(?:（修订版）)?$", title))


def _to_float(text: str) -> Optional[float]:
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _to_int(text: str) -> Optional[int]:
    value = _to_float(text)
    return int(value) if value is not None else None


def _first_match(text: str, patterns: List[str], cast):
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            value = cast(m.group(1))
            if value is not None:
                return value
    return None


def _parse_labeled_counts(text: str, labels: Dict[str, List[str]]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for key, variants in labels.items():
        for label in variants:
            # Annual-report PDF extraction normally preserves table rows as label + value.
            m = re.search(rf"{label}\s*[：:]?\s*([\d,]+)", text)
            if m:
                value = _to_int(m.group(1))
                if value is not None:
                    result[key] = value
                    break
    return result


def parse_rd_team_text(text: str) -> Optional[Dict]:
    """Parse standardized R&D personnel facts from extracted annual-report text."""
    if not text or "研发人员" not in text:
        return None

    normalized = text.replace("\u3000", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)

    headcount = _first_match(
        normalized,
        [
            r"(?:公司)?研发人员(?:的)?数量\s*[（(]?人?[）)]?\s*[：:]?\s*([\d,]+)",
            r"研发人员人数\s*[：:]?\s*([\d,]+)",
        ],
        _to_int,
    )
    ratio = _first_match(
        normalized,
        [
            r"研发人员数量占公司总人数的比例\s*[（(]?%[）)]?\s*[：:]?\s*([\d.]+)",
            r"研发人员(?:人数)?占比\s*[：:]?\s*([\d.]+)\s*%",
        ],
        _to_float,
    )

    education = _parse_labeled_counts(
        normalized,
        {
            "doctor": [r"博士研究生", r"博士"],
            "master": [r"硕士研究生", r"硕士"],
            "bachelor": [r"本科"],
            "college": [r"专科"],
            "high_school_or_below": [r"高中及以下"],
        },
    )
    age = _parse_labeled_counts(
        normalized,
        {
            "under_30": [r"30\s*岁以下（不含\s*30\s*岁）", r"30\s*岁以下"],
            "30_to_40": [r"30\s*[-—至]\s*40\s*岁[^\d\n]*"],
            "40_to_50": [r"40\s*[-—至]\s*50\s*岁[^\d\n]*"],
            "50_to_60": [r"50\s*[-—至]\s*60\s*岁[^\d\n]*"],
            "60_or_above": [r"60\s*岁及以上", r"60\s*岁以上"],
        },
    )

    if headcount is None and ratio is None and not education and not age:
        return None

    return {
        "rd_headcount": headcount,
        "rd_staff_ratio_pct": ratio,
        "education": education,
        "age": age,
    }


async def _query_latest_annual_report(code6: str) -> Optional[Dict]:
    import httpx

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "Origin": "https://www.cninfo.com.cn",
        "X-Requested-With": "XMLHttpRequest",
    }
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    start_year = now.year - 3

    try:
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=True, timeout=_QUERY_TIMEOUT_S
        ) as client:
            stock_resp = await client.get(
                "https://www.cninfo.com.cn/new/data/szse_stock.json"
            )
            stock_resp.raise_for_status()
            stock_list = (stock_resp.json() or {}).get("stockList") or []
            stock = next(
                (x for x in stock_list if str(x.get("code") or "") == code6),
                None,
            )
            if not stock:
                return None
            org_id = stock.get("orgId")
            if not org_id:
                return None

            payload = {
                "pageNum": "1",
                "pageSize": "30",
                "column": "szse",
                "tabName": "fulltext",
                "plate": "",
                "stock": f"{code6},{org_id}",
                "searchkey": "",
                "secid": "",
                "category": "category_ndbg_szsh",
                "trade": "",
                "seDate": f"{start_year}-01-01~{now:%Y-%m-%d}",
                "sortName": "time",
                "sortType": "desc",
                "isHLtitle": "true",
            }
            resp = await client.post(
                "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                data=payload,
            )
            resp.raise_for_status()
            announcements = (resp.json() or {}).get("announcements") or []
    except Exception as e:
        logger.warning(
            f"[rd_team] CNINFO annual-report query {code6} failed: "
            f"{type(e).__name__}: {str(e)[:160]}"
        )
        return None

    candidates = [
        x for x in announcements
        if str(x.get("secCode") or "") == code6
        and _is_main_annual_report(str(x.get("announcementTitle") or ""))
        and x.get("adjunctUrl")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: int(x.get("announcementTime") or 0), reverse=True)
    item = candidates[0]

    ts = int(item.get("announcementTime") or 0)
    published_at = ""
    if ts:
        published_at = datetime.fromtimestamp(
            ts / 1000, tz=ZoneInfo("Asia/Shanghai")
        ).strftime("%Y-%m-%d")
    adjunct = str(item.get("adjunctUrl") or "").lstrip("/")
    return {
        "title": _clean_title(str(item.get("announcementTitle") or "")),
        "published_at": published_at,
        "pdf_url": f"https://static.cninfo.com.cn/{adjunct}",
        "announcement_id": str(item.get("announcementId") or ""),
    }


async def _download_pdf(url: str) -> Optional[bytes]:
    import httpx

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_PDF_TIMEOUT_S,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.cninfo.com.cn/",
                "Accept": "application/pdf,*/*",
                # Do not advertise brotli for binary PDF CDN responses.
                "Accept-Encoding": "gzip, deflate",
            },
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = resp.content
    except Exception as e:
        logger.warning(
            f"[rd_team] annual-report PDF download failed: "
            f"{type(e).__name__}: {str(e)[:160]}"
        )
        return None

    if not body.startswith(b"%PDF") or len(body) > _MAX_PDF_BYTES:
        logger.warning(
            f"[rd_team] invalid/oversize annual-report PDF: {len(body)} bytes"
        )
        return None
    return body


def _extract_relevant_pdf_text(pdf_bytes: bytes) -> tuple[str, List[int]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_texts: Dict[int, str] = {}
    hits: List[int] = []

    for idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        page_texts[idx] = text
        if "研发人员" in text and (
            "学历" in text or "研发人员数量" in text or "研发人员的数量" in text
        ):
            hits.append(idx)

    if not hits:
        return "", []

    selected = set()
    for idx in hits:
        selected.update(i for i in (idx - 1, idx, idx + 1) if 0 <= i < len(reader.pages))
    ordered = sorted(selected)
    return "\n".join(page_texts.get(i, "") for i in ordered), [i + 1 for i in hits]


async def get_rd_team_composition(stock_code: str) -> Optional[Dict]:
    code6 = _bare_code(stock_code)
    if len(code6) != 6:
        return None

    report = await _query_latest_annual_report(code6)
    if not report:
        return None
    pdf_bytes = await _download_pdf(report["pdf_url"])
    if not pdf_bytes:
        return {
            **report,
            "source_name": "巨潮资讯年报",
            "source_tier": "S",
            "parse_status": "pdf_download_failed",
        }

    try:
        text, hit_pages = await asyncio.wait_for(
            asyncio.to_thread(_extract_relevant_pdf_text, pdf_bytes),
            timeout=_PARSE_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(
            f"[rd_team] annual-report PDF parse failed: "
            f"{type(e).__name__}: {str(e)[:160]}"
        )
        return {
            **report,
            "source_name": "巨潮资讯年报",
            "source_tier": "S",
            "parse_status": "pdf_parse_failed",
        }

    facts = parse_rd_team_text(text)
    if not facts:
        return {
            **report,
            "source_name": "巨潮资讯年报",
            "source_tier": "S",
            "parse_status": "rd_table_not_found",
            "hit_pages": hit_pages,
        }

    return {
        **report,
        **facts,
        "source_name": "巨潮资讯年报",
        "source_tier": "S",
        "parse_status": "ok",
        "hit_pages": hit_pages,
    }
