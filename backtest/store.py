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
将验证通过 (verdict=correct) 的预测记录落盘为 jsonl，每个用户单独一个文件。
"""

from typing import Optional

from backtest.classify import ClassifiedPrediction
from backtest.verify import VerifyResult
from model.m_backtest import XueqiuPredictionRecord
from tools.async_file_writer import AsyncFileWriter

_writer = AsyncFileWriter(platform="xueqiu", crawler_type="backtest")


def build_record(
    prediction: ClassifiedPrediction, result: Optional[VerifyResult] = None
) -> XueqiuPredictionRecord:
    """
    组装回测记录。direction 为空的预测是"观点型" (无方向性预测, 如清仓理由/
    宏观判断), 不做走势验证, verdict 记为 "view"、验证字段留空;
    方向型预测必须带 VerifyResult。
    """
    post = prediction["post"]
    is_view = not (prediction.get("direction") or "")
    return XueqiuPredictionRecord(
        user_id=str(post.get("user_id") or ""),
        user_nickname=post.get("user_nickname", ""),
        status_id=str(post.get("status_id", "")),
        status_url=post.get("status_url", ""),
        predicted_at=int(post.get("created_at") or 0),
        stock_code=prediction["stock_code"],
        stock_name=prediction["stock_name"],
        direction=prediction["direction"],
        thesis=prediction["thesis"],
        industry_view=prediction["industry_view"],
        market_context=prediction["market_context"],
        verified_at=result["verified_at"] if result else 0,
        actual_trend=result["actual_trend"] if result else "",
        price_change_pct=result["price_change_pct"] if result else 0.0,
        verdict="view" if is_view else result["verdict"],
    )

async def store_record(record: XueqiuPredictionRecord) -> None:
    await _writer.write_to_jsonl(
        item_type="backtest",
        item=record.model_dump(),
        file_prefix=record.user_id,
    )
