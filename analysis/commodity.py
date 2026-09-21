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
沪铜/COMEX铜价差 + 人民币汇率趋势。

铜价差部分仅对周期性矿业股触发，用于判断是否存在进口套利/囤货驱动的金融属性行情
(而非真实供需)。汇率趋势部分 (get_rmb_trend_signal) 不设行业门槛——汇率敞口取决于
海外收入占比，与行业无关。

akshare 不提供 LME 伦铜数据，用 COMEX 纽约铜代替 (同属反映内外盘套利的信号，
但不是同一交易所)。沪铜单位人民币元/吨，COMEX铜单位美元/磅，两者不直接可比，
价差计算需要磅->吨换算 + 人民币汇率。人民币汇率额外给出近期升贬值趋势，因为
汇率趋势本身也是独立于价差数值的信号 (人民币升值会单独影响进口套利经济性)。
"""

import time
from typing import Dict, List, Optional

import akshare as ak

from tools.utils import utils

_CYCLICAL_MINING_KEYWORDS = ("有色", "金属", "采掘", "矿业", "钢铁", "煤炭")

# 汇率趋势对所有有海外收入的公司都适用 (不只是矿业股)，独立于铜价单独取一份。
_RMB_TTL_SECONDS = 6 * 3600
_rmb_cache: Dict[str, object] = {}

_LB_PER_TON = 2204.62


def is_cyclical_mining_industry(industry_name: str) -> bool:
    return any(kw in industry_name for kw in _CYCLICAL_MINING_KEYWORDS)


async def _get_sh_copper_price() -> Optional[float]:
    import asyncio

    try:
        df = await asyncio.to_thread(ak.futures_main_sina, symbol="CU0")
    except Exception as e:
        utils.logger.error(f"[commodity] futures_main_sina(CU0) failed: {e}")
        return None
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["收盘价"])


async def _get_comex_copper_price() -> Optional[float]:
    import asyncio

    try:
        df = await asyncio.to_thread(ak.futures_global_spot_em)
    except Exception as e:
        utils.logger.error(f"[commodity] futures_global_spot_em failed: {e}")
        return None
    if df is None or df.empty:
        return None
    row = df[df["代码"] == "HG00Y"]
    if row.empty or row.iloc[0]["最新价"] != row.iloc[0]["最新价"]:
        return None
    return float(row.iloc[0]["最新价"])


async def _get_rmb_trend() -> Optional[str]:
    """近 30 个交易日美元/人民币中间价趋势 (报价为每 100 美元兑人民币)。"""
    import asyncio

    try:
        df = await asyncio.to_thread(ak.currency_boc_safe)
    except Exception as e:
        utils.logger.error(f"[commodity] currency_boc_safe failed: {e}")
        return None
    if df is None or df.empty or len(df) < 30:
        return None

    recent = df["美元"].tail(30)
    change_pct = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] * 100
    # 美元/人民币报价下降 = 同样多美元换的人民币变少 = 人民币升值
    if change_pct < -0.3:
        return "appreciating"
    if change_pct > 0.3:
        return "depreciating"
    return "stable"


async def get_rmb_trend_signal() -> Optional[Dict]:
    """
    人民币汇率趋势 (独立于铜价，不按行业设门槛)。

    汇率对收入的影响只对"有海外收入"的公司成立，但海外收入占比要到 F10 数据解析完
    才知道，且占比为 0 时丢掉这一项也无害；反过来若按行业设门槛，出口导向的制造业
    (恰恰是汇率敞口最大的) 反而永远拿不到数据。
    取不到就返回 None，调用方写"暂缺"，绝不猜方向。
    """
    if time.time() < (_rmb_cache.get("expire_at") or 0):
        return _rmb_cache["value"]  # type: ignore[return-value]

    trend = await _get_rmb_trend()
    if trend is None:
        utils.logger.warning("[commodity] 人民币汇率趋势获取失败，本维度记为暂缺")
        return None

    value = {
        "rmb_trend": trend,
        "rmb_trend_note": {
            "appreciating": "人民币近期升值 (近 30 个交易日美元兑人民币中间价下行)",
            "depreciating": "人民币近期贬值 (近 30 个交易日美元兑人民币中间价上行)",
            "stable": "人民币近期基本稳定 (近 30 个交易日中间价波动幅度小于 0.3%)",
        }.get(trend, trend),
    }
    _rmb_cache["value"] = value
    _rmb_cache["expire_at"] = time.time() + _RMB_TTL_SECONDS
    return value


async def get_copper_spread_signal(stock_code: str, industry_name: Optional[str]) -> Optional[Dict]:
    """
    仅当所属行业命中周期性矿业关键词时才拉取铜价数据，避免给无关股票塞入不相关信息。
    行业未知或不是周期性矿业股时返回 None。
    """
    if not industry_name or not is_cyclical_mining_industry(industry_name):
        return None

    sh_price = await _get_sh_copper_price()
    comex_price = await _get_comex_copper_price()
    rmb_trend = await _get_rmb_trend()

    notes: List[str] = [
        "沪铜单位: 人民币元/吨；COMEX铜单位: 美元/磅 (1吨=2204.62磅)，两者单位不同，"
        "不能直接相减，价差判断请结合汇率与单位换算自行判断内外盘套利空间是否扩大/收窄。"
    ]
    if sh_price is None:
        notes.append("沪铜价格数据获取失败。")
    if comex_price is None:
        notes.append("COMEX铜价格数据获取失败。")
    if rmb_trend is None:
        notes.append("人民币汇率趋势数据获取失败。")

    if sh_price is None and comex_price is None:
        return None

    return {
        "sh_copper_price": sh_price,
        "sh_copper_unit": "元/吨",
        "comex_copper_price": comex_price,
        "comex_copper_unit": "美元/磅",
        "rmb_trend": rmb_trend,
        "note": " ".join(notes),
    }
