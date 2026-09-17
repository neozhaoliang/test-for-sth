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
import pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

import config
from tools.utils import utils

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
    """雪球代码 (如 SH600519) -> akshare stock_zh_a_daily 的 symbol 参数 (如 sh600519)"""
    return code.lower()


def _fetch_from_akshare(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    # 用新浪财经接口 (stock_zh_a_daily)，东方财富的 stock_zh_a_hist 接口在部分网络环境下会被重置连接
    symbol = _akshare_symbol(code)
    df = ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "close"])
    df = df[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    return df


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
                utils.logger.warning(f"[price_source] Failed to read cache for {code}: {e}")
                cached = pd.DataFrame(columns=["date", "close"])

        need_fetch = True
        fetch_start = start
        if not cached.empty:
            cached_max = datetime.strptime(cached["date"].max(), "%Y-%m-%d").date()
            if cached_max >= end:
                need_fetch = False
            else:
                fetch_start = cached_max + timedelta(days=1)

        if need_fetch:
            try:
                fresh = await asyncio.to_thread(
                    _fetch_from_akshare,
                    code,
                    fetch_start.strftime("%Y%m%d"),
                    end.strftime("%Y%m%d"),
                )
            except Exception as e:
                utils.logger.error(f"[price_source] akshare fetch failed for {code}: {e}")
                fresh = pd.DataFrame(columns=["date", "close"])

            if not fresh.empty:
                merged = fresh if cached.empty else pd.concat([cached, fresh], ignore_index=True)
                merged = merged.drop_duplicates(subset="date").sort_values("date")
                await asyncio.to_thread(_write_cache_atomic, path, merged)
                cached = merged

    if cached.empty:
        return cached
    mask = (cached["date"] >= start.strftime("%Y-%m-%d")) & (cached["date"] <= end.strftime("%Y-%m-%d"))
    return cached.loc[mask].sort_values("date").reset_index(drop=True)
