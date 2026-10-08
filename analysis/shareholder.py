# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
股东回报与筹码维度: 股东户数环比变化 (散户情绪代理)、历史分红、历史回购。
akshare 接口均以不带市场前缀的 6 位数字代码为入参。
"""

import logging
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import akshare as ak


logger = logging.getLogger("MediaCrawler")

_BUYBACK_CACHE_TTL_SECONDS = 3600
_buyback_cache: Optional[Tuple[float, "object"]] = None

_MAX_DIVIDEND_RECORDS = 30
_MAX_BUYBACK_RECORDS = 20


def _bare_code(stock_code: str) -> str:
    return stock_code.strip()[-6:]


def _coerce_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text.replace("/", "-") if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    return None




def _trend_from_f10_facts(fundamentals: Optional[Dict]) -> Optional[Dict]:
    """Build a live shareholder trend from THS F10 holder-count history.

    The F10 series has no announcement timestamp, so this helper is live-only.  It is
    useful as a freshness backstop when the AKShare detail endpoint silently returns an
    old historical tail for an otherwise current stock.
    """
    facts = (fundamentals or {}).get("facts") or {}
    series = facts.get("holder_count_series") or []
    rows = []
    for item in series:
        period = _coerce_date((item or {}).get("period"))
        holders = (item or {}).get("holders")
        if period is None or holders in (None, 0):
            continue
        try:
            holders = int(holders)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "period": period,
                "holders": holders,
                "price": (item or {}).get("price"),
            }
        )
    if not rows:
        latest_count = facts.get("holder_count_latest")
        latest_period = _coerce_date(facts.get("holder_count_period"))
        if latest_count not in (None, 0) and latest_period is not None:
            try:
                latest_count = int(latest_count)
            except (TypeError, ValueError):
                return None
            return {
                "latest_count": latest_count,
                "change_pct": facts.get("holder_count_qoq_pct"),
                "yoy_pct": facts.get("holder_count_yoy_pct"),
                "trend": (
                    "increasing"
                    if (facts.get("holder_count_qoq_pct") or 0) > 0
                    else "decreasing"
                    if (facts.get("holder_count_qoq_pct") or 0) < 0
                    else "stable"
                    if facts.get("holder_count_qoq_pct") == 0
                    else "unknown"
                ),
                "as_of": latest_period.isoformat(),
                "period": latest_period.isoformat(),
                "published_at": None,
                "available_at": None,
                "source_mode": "ths_f10_holder_latest",
                "series": [],
            }
        return None

    rows.sort(key=lambda x: x["period"])
    latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else None
    change_pct = None
    if previous and previous["holders"]:
        change_pct = round(
            (latest["holders"] - previous["holders"]) / previous["holders"] * 100,
            3,
        )

    same_period_last_year = next(
        (
            row for row in reversed(rows[:-1])
            if row["period"].month == latest["period"].month
            and row["period"].day == latest["period"].day
        ),
        None,
    )
    yoy_pct = None
    if same_period_last_year and same_period_last_year["holders"]:
        yoy_pct = round(
            (latest["holders"] - same_period_last_year["holders"])
            / same_period_last_year["holders"]
            * 100,
            3,
        )

    return {
        "latest_count": latest["holders"],
        "change_pct": change_pct,
        "yoy_pct": yoy_pct,
        "trend": (
            "increasing"
            if change_pct is not None and change_pct > 0
            else "decreasing"
            if change_pct is not None and change_pct < 0
            else "stable"
            if change_pct == 0
            else "unknown"
        ),
        "as_of": latest["period"].isoformat(),
        "period": latest["period"].isoformat(),
        "published_at": None,
        "available_at": None,
        "source_mode": "ths_f10_holder_series",
        "series": [
            {
                "period": row["period"].isoformat(),
                "holders": row["holders"],
                "price": row.get("price"),
            }
            for row in rows
        ],
    }


def reconcile_live_shareholder_trend(
    shareholder_trend: Optional[Dict],
    fundamentals: Optional[Dict],
) -> Optional[Dict]:
    """Prefer the freshest live holder-count source instead of blindly trusting one API."""
    f10 = _trend_from_f10_facts(fundamentals)
    if not f10:
        return shareholder_trend
    if not shareholder_trend:
        return f10

    current_period = _coerce_date(
        shareholder_trend.get("period") or shareholder_trend.get("as_of")
    )
    f10_period = _coerce_date(f10.get("period"))
    if f10_period and (current_period is None or f10_period > current_period):
        logger.warning(
            "[shareholder] replacing stale holder-count source %s (%s) with THS F10 (%s)",
            shareholder_trend.get("source_mode"),
            current_period,
            f10_period,
        )
        return f10
    return shareholder_trend

async def get_shareholder_count_trend(
    stock_code: str,
    *,
    as_of: Optional[date] = None,
) -> Optional[Dict]:
    """
    股东户数变化，用作散户情绪/筹码集中度代理。

    Historical safety rule:
    - use the stock-specific detail table when available;
    - filter by disclosure/announcement date <= as_of, not by holder-count period;
    - never fall back to today's "latest" market-wide snapshot for a historical request.
    """
    import asyncio

    code6 = _bare_code(stock_code)

    # Preferred path: detail history contains both observation period and disclosure date.
    try:
        detail = await asyncio.to_thread(ak.stock_zh_a_gdhs_detail_em, symbol=code6)
    except Exception as e:
        detail = None
        logger.warning(
            f"[shareholder] stock_zh_a_gdhs_detail_em({stock_code}) failed: {e}"
        )

    if detail is not None and not detail.empty:
        rows = detail.copy()
        if as_of is not None:
            rows = rows[
                rows["股东户数公告日期"].apply(
                    lambda x: (_coerce_date(x) is not None and _coerce_date(x) <= as_of)
                )
            ]
        if not rows.empty:
            rows = rows.sort_values(
                by=["股东户数公告日期", "股东户数统计截止日"],
                ascending=[True, True],
            )
            row = rows.iloc[-1]
            change_pct = row.get("股东户数-增减比例")
            if change_pct is not None and change_pct == change_pct:
                disclosure_date = _coerce_date(row.get("股东户数公告日期"))
                period_date = _coerce_date(row.get("股东户数统计截止日"))
                return {
                    "latest_count": int(row["股东户数-本次"]),
                    "change_pct": float(change_pct),
                    "trend": "increasing" if float(change_pct) > 0 else "decreasing",
                    "as_of": period_date.isoformat() if period_date else str(row.get("股东户数统计截止日") or ""),
                    "period": period_date.isoformat() if period_date else None,
                    "published_at": disclosure_date.isoformat() if disclosure_date else None,
                    "available_at": disclosure_date.isoformat() if disclosure_date else None,
                    "source_mode": "detail_history",
                }

    if as_of is not None:
        # Historical mode must fail closed.  Today's latest snapshot would leak future info.
        return None

    # Live fallback for temporary upstream failures in the detail endpoint.
    try:
        df = await asyncio.to_thread(ak.stock_zh_a_gdhs, symbol="最新")
    except Exception as e:
        logger.error(f"[shareholder] stock_zh_a_gdhs(最新) failed: {e}")
        return None

    if df is None or df.empty:
        return None
    sub = df[df["代码"] == code6]
    if sub.empty:
        return None

    row = sub.iloc[0]
    change_pct = row.get("股东户数-增减比例")
    if change_pct is None or change_pct != change_pct:
        return None

    disclosure_date = _coerce_date(row.get("公告日期"))
    period_date = _coerce_date(row.get("股东户数统计截止日-本次"))
    return {
        "latest_count": int(row["股东户数-本次"]),
        "change_pct": float(change_pct),
        "trend": "increasing" if float(change_pct) > 0 else "decreasing",
        "as_of": period_date.isoformat() if period_date else str(row["股东户数统计截止日-本次"]),
        "period": period_date.isoformat() if period_date else None,
        "published_at": disclosure_date.isoformat() if disclosure_date else None,
        "available_at": disclosure_date.isoformat() if disclosure_date else None,
        "source_mode": "latest_snapshot",
    }


async def get_dividend_history(
    stock_code: str,
    *,
    as_of: Optional[date] = None,
) -> List[Dict]:

    """最近几年的分红记录 (年度公告日期 + 每股派息)，拉取失败返回空列表。"""
    import asyncio

    try:
        df = await asyncio.to_thread(
            ak.stock_history_dividend_detail, symbol=_bare_code(stock_code), indicator="分红"
        )
    except Exception as e:
        logger.error(f"[shareholder] stock_history_dividend_detail({stock_code}) failed: {e}")
        return []

    if df is None or df.empty:
        return []

    records = []
    for _, row in df.iterrows():
        announce_date = _coerce_date(row.get("公告日期"))
        if as_of is not None and (announce_date is None or announce_date > as_of):
            continue
        records.append(
            {
                "announce_date": announce_date.isoformat() if announce_date else str(row["公告日期"]),
                "available_at": announce_date.isoformat() if announce_date else None,
                "dividend_per_10_shares": (
                    float(row["派息"])
                    if row["派息"] is not None and row["派息"] == row["派息"]
                    else None
                ),
                "progress": str(row["进度"]),
            }
        )
        if len(records) >= _MAX_DIVIDEND_RECORDS:
            break
    return records


async def _get_buyback_df():
    import asyncio

    global _buyback_cache
    if _buyback_cache and (time.time() - _buyback_cache[0]) < _BUYBACK_CACHE_TTL_SECONDS:
        return _buyback_cache[1]

    try:
        df = await asyncio.to_thread(ak.stock_repurchase_em)
    except Exception as e:
        logger.error(f"[shareholder] stock_repurchase_em failed: {e}")
        return None

    _buyback_cache = (time.time(), df)
    return df


async def get_buyback_history(
    stock_code: str,
    *,
    as_of: Optional[date] = None,
) -> List[Dict]:
    """该股票的历史回购公告 (金额、进度、均价)，拉取失败或无记录返回空列表。"""
    df = await _get_buyback_df()
    if df is None:
        return []

    sub = df[df["股票代码"] == _bare_code(stock_code)]
    if sub.empty:
        return []

    records = []
    for _, row in sub.iterrows():
        announce_date = _coerce_date(row.get("最新公告日期"))
        if as_of is not None and (announce_date is None or announce_date > as_of):
            continue
        records.append(
            {
                "announce_date": announce_date.isoformat() if announce_date else str(row["最新公告日期"]),
                "available_at": announce_date.isoformat() if announce_date else None,
                "progress": str(row["实施进度"]),
                "planned_amount_range": [
                    float(row["计划回购金额区间-下限"]) if row["计划回购金额区间-下限"] == row["计划回购金额区间-下限"] else None,
                    float(row["计划回购金额区间-上限"]) if row["计划回购金额区间-上限"] == row["计划回购金额区间-上限"] else None,
                ],
                "actual_amount": float(row["已回购金额"]) if row["已回购金额"] == row["已回购金额"] else None,
            }
        )
        if len(records) >= _MAX_BUYBACK_RECORDS:
            break
    return records
