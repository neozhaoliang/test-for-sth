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

from backtest import score
from backtest.classify import classify_post
from backtest.extract import extract_from_posts
from backtest.store import build_record, store_record
from backtest.verify import verify_prediction
from media_platform.xueqiu.help import normalize_user_id
from tools.utils import utils

_CONCURRENCY = 3

# 回测结束后是否重建观点摘录文件 (由 main() 的 --skip-digest 控制)
skip_digest = False

# 记录结构版本: 升级后重跑回测会把旧版记录重新处理并压缩掉旧行
_SCHEMA_VERSION = 2


def _contents_files(creator_id: str) -> List[str]:
    base = os.path.join("data", "xueqiu", "jsonl")
    pattern = os.path.join(base, f"creator_{creator_id}_contents_*.jsonl")
    return sorted(glob.glob(pattern))


def _load_posts(paths: List[str], since: Optional[str]) -> List[Dict[str, Any]]:
    """合并该用户的全部发帖文件 (按天落盘, 只读最新一个会漏掉历史)，
    按 status_id 去重后按发布时间升序返回。"""
    since_ms = 0
    if since:
        since_ms = int(datetime.strptime(since, "%Y-%m-%d").timestamp() * 1000)

    by_id: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                post = json.loads(line)
                if since_ms and int(post.get("created_at") or 0) < since_ms:
                    continue
                sid = str(post.get("status_id") or "")
                if sid:
                    by_id[sid] = post  # 同日重复时保留后写入的
                else:
                    by_id[f"idx-{len(by_id)}"] = post
    posts = list(by_id.values())
    posts.sort(key=lambda p: int(p.get("created_at") or 0))
    return posts


async def _process_one(
    extracted, semaphore: asyncio.Semaphore, stats: Dict[str, int], done_pairs: set
) -> None:
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
            pair = (str(prediction["post"].get("status_id") or ""), prediction["stock_code"])
            if pair in done_pairs:
                continue  # 该帖子对该股票的记录已存在, 重跑不重复存储

            if not prediction["direction"]:
                # 观点型 (清仓理由/宏观判断等, 无方向): 不验证走势, 直接落盘
                record = build_record(prediction)
                await store_record(record)
                done_pairs.add(pair)
                stats["views"] += 1
                continue

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
                stats["no_date"] += 1
                continue
            if result["verdict"] == "inconclusive":
                stats["inconclusive"] += 1
                continue

            record = build_record(prediction, result)
            await store_record(record)
            done_pairs.add(pair)
            if result["verdict"] == "correct":
                stats["verified_correct"] += 1
            else:
                stats["verified_incorrect"] += 1


async def run(creator_id: str, since: Optional[str], limit: Optional[int]) -> None:
    # 兼容 "主页 URL" 输入 (与 web 端抓取按钮的输入约定一致)
    creator_id = normalize_user_id(creator_id)
    paths = _contents_files(creator_id)
    if not paths:
        utils.logger.error(f"[backtest_run] No crawled data found for creator_id={creator_id}")
        return

    utils.logger.info(f"[backtest_run] Loading posts from {len(paths)} file(s) for {creator_id}")
    posts = _load_posts(paths, since)
    if limit:
        posts = posts[:limit]

    extracted_posts = extract_from_posts(posts)
    utils.logger.info(
        f"[backtest_run] {len(posts)} posts loaded, {len(extracted_posts)} original posts to analyze"
    )

    stats = {
        "predictions_found": 0,
        "verified_correct": 0,
        "verified_incorrect": 0,
        "views": 0,
        "inconclusive": 0,
        "no_date": 0,
        "errors": 0,
    }
    # 已存储的 (status_id, stock_code) 集合: 重跑回测时不重复落盘。
    # 只跳过 schema 已达当前版本的记录——旧版本 (没有论据字段) 需要重新
    # 处理以补全论点/论据/背景, 压缩时再把旧版行清掉。
    done_pairs = {
        (str(r.get("status_id") or ""), str(r.get("stock_code") or ""))
        for r in score.load_records(creator_id)
        if int(r.get("schema_version") or 1) >= _SCHEMA_VERSION
    }
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    tasks = [_process_one(ep, semaphore, stats, done_pairs) for ep in extracted_posts]

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
        f"views={stats['views']}, inconclusive={stats['inconclusive']}, "
        f"no_date={stats['no_date']}, errors={stats['errors']}, "
        f"hit_rate={hit_rate:.1f}% (of {total_verified} conclusive predictions)"
    )

    # schema 升级重跑时旧版记录已被新行取代, 压缩掉旧行避免重复统计
    score.compact_records(creator_id)

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
