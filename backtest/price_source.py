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
A股历史日线价格数据源，基于 akshare，本地 CSV 缓存避免重复请求同一股票。
"""

import asyncio
import logging
import pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

import config

logger = logging.getLogger("MediaCrawler")

# 按股票代码分别加锁，不同股票的缓存读写/网络请求可以并发，
# 同一股票的并发请求仍序列化以避免缓存文件读写竞态。
_CACHE_LOCKS: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _cache_dir() -> pathlib.Path:
    if config.SAVE_DATA_PATH:
        base = pathlib.Path(config.SAVE_DATA_PATH) / "xueqiu" / "backtest" / "price_cache"
    else:
        base = pathlib.Path("data") / "xueqiu" / "backtest" / "price_cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cache_path(code: str) -> pathlib.Path:
    return _cache_dir() / f"{code}.csv"


def _akshare_symbol(code: str) -> str:
    """
    Normalize project/bare A-share codes for ak.stock_zh_a_daily.

    Accepted examples:
    - SH600519 -> sh600519
    - 600519   -> sh600519
    - SZ000001 -> sz000001
    - 000001   -> sz000001
    - BJ430047 / 430047 -> bj430047

    The data layer must not rely on the Web/API resolver having added a market prefix.
    """
    raw = str(code or "").strip().lower()
    if raw.startswith(("sh", "sz", "bj")) and len(raw) == 8:
        return raw

    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 6:
        return raw

    if digits.startswith(("4", "8")) or digits.startswith("92"):
        return "bj" + digits
    if digits.startswith(("0", "1", "2", "3")):
        return "sz" + digits
    return "sh" + digits


def _fetch_from_akshare(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Unadjusted daily closes. Sina first; Eastmoney when Sina is unavailable.

    Historical price joining must not silently fail just because one quote host
    blocks or resets a connection in the current network environment.
    """
    try:
        symbol = _akshare_symbol(code)
        df = ak.stock_zh_a_daily(
            symbol=symbol, start_date=start_date, end_date=end_date,
        )
        if df is not None and not df.empty:
            out = df[["date", "close"]].copy()
            out["date"] = pd.to_datetime(out["date"]).dt.date.astype(str)
            return out
    except Exception as exc:
        logger.warning("[price_source] Sina daily unavailable for %s: %s", code, exc)

    try:
        df = ak.stock_zh_a_hist(
            symbol="".join(ch for ch in code if ch.isdigit())[-6:],
            period="daily", start_date=start_date, end_date=end_date,
            adjust="",  # No forward/backward adjustment: dates use raw closes.
        )
        if df is not None and not df.empty:
            out = df[["日期", "收盘"]].rename(
                columns={"日期": "date", "收盘": "close"}
            ).copy()
            out["date"] = pd.to_datetime(out["date"]).dt.date.astype(str)
            out["close"] = pd.to_numeric(out["close"], errors="coerce")
            return out.dropna(subset=["date", "close"])
    except Exception as exc:
        logger.warning("[price_source] Eastmoney daily unavailable for %s: %s", code, exc)
    return pd.DataFrame(columns=["date", "close"])


def _write_cache_atomic(path: pathlib.Path, df: pd.DataFrame) -> None:
    """临时文件写入后原子性 rename，避免写入过程中崩溃产生半写/损坏的缓存文件。"""
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    df.to_csv(tmp_path, index=False)
    tmp_path.replace(path)


async def get_price_history(code: str, start: date, end: Optional[date] = None) -> pd.DataFrame:
    """
    获取 [start, end] 区间的收盘价序列 (date, close)，end 默认今天。
    本地按股票代码缓存整段历史，若缓存最新日期早于所需 end，则增量补拉。
    """
    end = end or date.today()
    path = _cache_path(code)

    async with _CACHE_LOCKS[code]:
        cached = pd.DataFrame(columns=["date", "close"])
        if path.exists():
            try:
                cached = pd.read_csv(path, dtype={"date": str})
                cached = cached.dropna(subset=["date"])
                cached = cached[cached["date"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
            except Exception as e:
                logger.warning(f"[price_source] Failed to read cache for {code}: {e}")
                cached = pd.DataFrame(columns=["date", "close"])

        # Coverage means *both* ends of the requested range. A cache whose
        # latest date is recent can still miss the earlier observation periods
        # needed for shareholder/price trend alignment.
        windows = []
        if cached.empty:
            windows.append((start, end))
        else:
            cached_min = datetime.strptime(cached["date"].min(), "%Y-%m-%d").date()
            cached_max = datetime.strptime(cached["date"].max(), "%Y-%m-%d").date()
            if cached_min > start:
                windows.append((start, min(end, cached_min - timedelta(days=1))))
            if cached_max < end:
                windows.append((max(start, cached_max + timedelta(days=1)), end))
        for fetch_start, fetch_end in windows:
            if fetch_start > fetch_end:
                continue
            try:
                fresh = await asyncio.to_thread(
                    _fetch_from_akshare, code,
                    fetch_start.strftime("%Y%m%d"),
                    fetch_end.strftime("%Y%m%d"),
                )
            except Exception as e:
                logger.error("[price_source] daily history fetch failed for %s: %s", code, e)
                continue
            if fresh is None or fresh.empty:
                continue
            cached = (fresh if cached.empty
                      else pd.concat([cached, fresh], ignore_index=True))
            cached = cached.drop_duplicates(subset="date").sort_values("date")
        if not cached.empty and windows:
            await asyncio.to_thread(_write_cache_atomic, path, cached)

    if cached.empty:
        return cached
    mask = (cached["date"] >= start.strftime("%Y-%m-%d")) & (cached["date"] <= end.strftime("%Y-%m-%d"))
    return cached.loc[mask].sort_values("date").reset_index(drop=True)
