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
用户预测回测入口脚本。

用法:
    python backtest_run.py --creator_id 3058599833
    python backtest_run.py --creator_id 3058599833 --since 2020-01-01
    python backtest_run.py --creator_id 3058599833 --limit 20

遍历指定用户已爬取的帖子 (data/xueqiu/jsonl/creator_<user_id>_contents_*.jsonl 中最新的一个)，
抽取股票提及 -> LLM 判断是否为有论据的预测 -> 用后续真实股价验证 -> 只保存验证通过的记录。
"""

import argparse
import asyncio
import glob
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from backtest.classify import classify_post
from backtest.extract import extract_from_posts
from backtest.store import build_record, store_record
from backtest.verify import verify_prediction
from media_platform.xueqiu.help import normalize_user_id
from tools.utils import utils

_CONCURRENCY = 3

# 回测结束后是否重建观点摘录文件 (由 main() 的 --skip-digest 控制)
skip_digest = False


def _latest_contents_file(creator_id: str) -> Optional[str]:
    base = os.path.join("data", "xueqiu", "jsonl")
    pattern = os.path.join(base, f"creator_{creator_id}_contents_*.jsonl")
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def _load_posts(path: str, since: Optional[str]) -> List[Dict[str, Any]]:
    since_ms = 0
    if since:
        since_ms = int(datetime.strptime(since, "%Y-%m-%d").timestamp() * 1000)

    posts: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            post = json.loads(line)
            if since_ms and int(post.get("created_at") or 0) < since_ms:
                continue
            posts.append(post)
    return posts


async def _process_one(extracted, semaphore: asyncio.Semaphore, stats: Dict[str, int]) -> None:
    async with semaphore:
        try:
            predictions = await classify_post(extracted)
        except Exception as e:
            utils.logger.error(
                f"[backtest_run] classify_post failed for status_id="
                f"{extracted['post'].get('status_id')}: {e}"
            )
            stats["errors"] += 1
            return
        stats["predictions_found"] += len(predictions)

        for prediction in predictions:
            try:
                result = await verify_prediction(prediction)
            except Exception as e:
                utils.logger.error(
                    f"[backtest_run] verify_prediction failed for status_id="
                    f"{prediction['post'].get('status_id')} stock_code={prediction['stock_code']}: {e}"
                )
                stats["errors"] += 1
                continue

            if result is None:
                continue
            if result["verdict"] == "inconclusive":
                stats["inconclusive"] += 1
                continue

            record = build_record(prediction, result)
            await store_record(record)
            if result["verdict"] == "correct":
                stats["verified_correct"] += 1
            else:
                stats["verified_incorrect"] += 1


async def run(creator_id: str, since: Optional[str], limit: Optional[int]) -> None:
    # 兼容 "主页 URL" 输入 (与 web 端抓取按钮的输入约定一致)
    creator_id = normalize_user_id(creator_id)
    path = _latest_contents_file(creator_id)
    if not path:
        utils.logger.error(f"[backtest_run] No crawled data found for creator_id={creator_id}")
        return

    utils.logger.info(f"[backtest_run] Loading posts from {path}")
    posts = _load_posts(path, since)
    if limit:
        posts = posts[:limit]

    extracted_posts = extract_from_posts(posts)
    utils.logger.info(
        f"[backtest_run] {len(posts)} posts loaded, {len(extracted_posts)} contain stock mentions"
    )

    stats = {
        "predictions_found": 0,
        "verified_correct": 0,
        "verified_incorrect": 0,
        "inconclusive": 0,
        "errors": 0,
    }
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    tasks = [_process_one(ep, semaphore, stats) for ep in extracted_posts]

    processed = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        processed += 1
        if processed % 20 == 0 or processed == len(tasks):
            utils.logger.info(
                f"[backtest_run] Progress: {processed}/{len(tasks)} posts, "
                f"predictions_found={stats['predictions_found']}, "
                f"correct={stats['verified_correct']}, incorrect={stats['verified_incorrect']}, "
                f"inconclusive={stats['inconclusive']}, errors={stats['errors']}"
            )

    total_verified = stats["verified_correct"] + stats["verified_incorrect"]
    hit_rate = (stats["verified_correct"] / total_verified * 100) if total_verified else 0.0
    utils.logger.info(
        f"[backtest_run] Done. predictions_found={stats['predictions_found']}, "
        f"correct={stats['verified_correct']}, incorrect={stats['verified_incorrect']}, "
        f"inconclusive={stats['inconclusive']}, errors={stats['errors']}, "
        f"hit_rate={hit_rate:.1f}% (of {total_verified} conclusive predictions)"
    )

    if not skip_digest:
        # 回测完自动重建观点摘录文件 (提炼按内容哈希缓存, 只有新增/变化的
        # 用户-股票组才触发 LLM 调用), 保证报告与问答读到的摘录总是最新的。
        from backtest import digest

        await digest.build_digests()


def main() -> None:
    global skip_digest
    parser = argparse.ArgumentParser(description="雪球用户预测回测")
    parser.add_argument("--creator_id", required=True, help="雪球用户 ID")
    parser.add_argument("--since", default=None, help="只回测该日期之后发布的帖子 (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None, help="限制处理的帖子数量 (调试用)")
    parser.add_argument("--skip-digest", action="store_true", help="回测结束后不重建观点摘录文件")
    args = parser.parse_args()
    skip_digest = args.skip_digest

    asyncio.run(run(args.creator_id, args.since, args.limit))


if __name__ == "__main__":
    main()
