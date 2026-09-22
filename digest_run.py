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
用户观点摘录入口脚本。

用法:
    python digest_run.py            # 从全部验证记录重建摘录文件 (提炼结果按内容哈希缓存)
    python digest_run.py --refresh  # 同上 (提炼缓存仍然生效; 想强制重新提炼请删除
                                    #  data/xueqiu/digest/distill_cache.jsonl)

对每个用户: 读取历史验证记录, 按股票聚合 verdict=correct 的论据 (被后续走势
验证正确的观点), 多条论据用 LLM 提炼成一段连贯观点总结, 写入
data/xueqiu/digest/user_digests.jsonl。之后报告生成/问答直接读这个文件。
"""

import argparse
import asyncio

from backtest import digest
from tools.utils import utils


async def run() -> None:
    entries = await digest.build_digests()
    users = len({e["user_id"] for e in entries})
    utils.logger.info(
        f"[digest_run] 完成: {len(entries)} 个 (用户, 股票) 观点条目, 覆盖 {users} 位用户"
    )
    for e in entries[:10]:
        utils.logger.info(
            f"[digest_run] {e['user_nickname']}({e['user_id']}) {e['stock_name']}({e['stock_code']}) "
            f"命中 {e['correct']}/{e['total']}, 摘要: {e['summary'][:60]}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="用户观点摘录 (digest) 构建")
    parser.add_argument("--refresh", action="store_true", help="刷新摘录文件 (保留提炼缓存)")
    args = parser.parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
