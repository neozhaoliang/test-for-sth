# -*- coding: utf-8 -*-
"""
Point-in-time ownership structure from exact CNINFO periodic-report PDFs.

Historical mode deliberately prefers a smaller but provably available ownership snapshot
over today's reconstructed institutional-holding database. We parse the latest filing
version published by as_of and extract identifiable top-shareholder names from the original
report text.

This does NOT claim to recover all fund holdings. It only reports what is visible in the
filing's top-shareholder section.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from typing import Dict, List, Optional

from analysis.filing_archive import _get_pdf_pages_text
from analysis.filing_calendar import (
    get_financial_filing_calendar,
    latest_available_filing_by_period,
)

_PARSE_TIMEOUT_S = 60

_SPECIAL_PATTERNS = {
    "national_team": re.compile(
        r"中央汇金|中国证券金融|证金|国新投资|国新央企|中国国新|诚通金控|中国诚通"
    ),
    "social_security": re.compile(r"全国社保|社保基金"),
    "insurance": re.compile(
        r"保险|人寿|太平洋人寿|新华人寿|泰康|太保寿险|平安人寿"
    ),
    "foreign": re.compile(
        r"香港中央结算|QFII|挪威中央银行|阿布达比|新加坡政府|摩根|高盛|瑞银|花旗|美林"
    ),
    "public_fund": re.compile(r"证券投资基金|基金管理|基金|ETF|指数"),
}

_SECTION_MARKERS = (
    "前10名无限售条件股东持股情况",
    "前十名无限售条件股东持股情况",
    "前10名股东持股情况",
    "前十名股东持股情况",
)

_NAME_PATTERNS = (
    re.compile(
        r"([\u4e00-\u9fffA-Za-z0-9（）()·\-—]{4,100}?"
        r"(?:有限责任公司|股份有限公司|有限公司|中央结算有限公司|"
        r"证券投资基金|基金|资产管理计划|集合资产管理计划|组合|"
        r"银行|保险|人寿|证券|信托))"
    ),
    re.compile(r"(全国社保基金[一二三四五六七八九十零〇\d]+组合)"),
)


def classify_holder_name(name: str) -> List[str]:
    return [
        category
        for category, pattern in _SPECIAL_PATTERNS.items()
        if pattern.search(name or "")
    ]


def _extract_holder_section(pages: List[str]) -> str:
    selected: List[str] = []
    for text in pages:
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        if any(marker in compact for marker in _SECTION_MARKERS):
            selected.append(text)
    return "\n".join(selected)


def parse_top_holder_names(text: str) -> List[Dict]:
    if not text:
        return []
    # PDF table extraction often inserts spaces/newlines between every Chinese token.
    # Holder names are structural strings, so remove all whitespace before matching.
    normalized = re.sub(r"\s+", "", text.replace("\u3000", " "))

    names: List[str] = []
    for pattern in _NAME_PATTERNS:
        for match in pattern.finditer(normalized):
            name = re.sub(r"\s+", "", match.group(1)).strip("：:，,；;")
            if len(name) < 4:
                continue
            if name in {
                "股东名称",
                "股东性质",
                "股份有限公司",
                "有限责任公司",
                "证券投资基金",
            }:
                continue
            if name not in names:
                names.append(name)

    return [
        {
            "name": name,
            "categories": classify_holder_name(name),
            "source_scope": "periodic_report_top_holders",
        }
        for name in names[:30]
    ]


def _summaries(
    rows: List[Dict],
) -> tuple[List[Dict], Dict[str, List[Dict]], List[Dict]]:
    special: Dict[str, List[Dict]] = {key: [] for key in _SPECIAL_PATTERNS}
    for row in rows:
        for category in row.get("categories") or []:
            special[category].append(row)

    institution_summary: List[Dict] = []
    labels = {
        "public_fund": "基金",
        "social_security": "全国社保",
        "insurance": "保险",
        "foreign": "QFII/境外",
        "national_team": "国家资本",
    }
    for category, label in labels.items():
        holders = special.get(category) or []
        if holders:
            institution_summary.append(
                {
                    "type": label,
                    "institutions": len(holders),
                    "latest_float_ratio_pct": None,
                    "latest_shares": None,
                    "basis": "periodic_report_top_holders_only",
                }
            )

    funds = [
        {"name": x["name"], "latest_float_ratio_pct": None}
        for x in special.get("public_fund") or []
    ]
    return institution_summary, special, funds


async def get_point_in_time_ownership(
    stock_code: str,
    as_of: date,
    *,
    lookback_years: int = 3,
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
    )
    if not filings:
        return None

    for item in filings[:6]:
        url = str(item.get("url") or "")
        if not url:
            continue
        pages = await _get_pdf_pages_text(url)
        if not pages:
            continue
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(_extract_holder_section, pages),
                timeout=_PARSE_TIMEOUT_S,
            )
        except Exception:
            continue
        holders = parse_top_holder_names(text)
        if not holders:
            continue

        institution_summary, special, funds = _summaries(holders)
        period = str(item.get("period") or "")
        published_at = str(item.get("published_at") or "")
        return {
            "report_period": period,
            "quarter_code": None,
            "previous_quarter_code": None,
            "previous_report_period": None,
            "available_at": published_at,
            "institution_summary": institution_summary,
            "previous_institution_summary": [],
            "institution_qoq": [],
            "fund_details": funds,
            "etf_details": [
                x for x in funds if re.search(r"ETF|指数", x.get("name") or "", re.I)
            ],
            "fund_qoq": {
                "increased": [],
                "decreased": [],
                "newly_seen": [],
                "exited_top_list": [],
            },
            "etf_qoq": {
                "increased": [],
                "decreased": [],
                "newly_seen": [],
                "exited_top_list": [],
            },
            "top10_free_holders": holders,
            "special_holders": special,
            "unlock_supply": {
                "upcoming_12m": [],
                "recent_6m": [],
                "max_upcoming_float_ratio_pct": None,
            },
            "notes": [
                "历史模式仅使用 as_of 当天已公开的原始定期报告前十大股东信息；"
                "不使用今天数据库回填的完整机构持仓。",
                "该快照只能证明股东出现在对应定期报告的前十大/前十大无限售名单，"
                "不能推断名单外机构是否持有。",
                "历史模式暂不从今天的解禁日历反推过去；未展示解禁不等于当时没有解禁安排。",
            ],
            "source_tier": "S",
            "point_in_time": True,
            "source": "CNINFO original periodic-report PDF",
            "sources": [url],
        }
    return None
