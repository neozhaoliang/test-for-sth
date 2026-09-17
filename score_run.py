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
用户预测可信度评分报表入口。

用法:
    python score_run.py
    python score_run.py --creator_id 3058599833
    python score_run.py --top 10
"""

import argparse
from typing import Optional

from backtest.score import score_all_users, score_user, load_records


def _print_score(score) -> None:
    print(
        f"{score.user_nickname} (user_id={score.user_id}) "
        f"wilson={score.wilson_score:.4f} hit_rate={score.hit_rate:.1%} "
        f"({score.correct}/{score.correct + score.incorrect}, "
        f"inconclusive={score.inconclusive})"
    )
    for stock in score.by_stock:
        print(
            f"    {stock.stock_name}({stock.stock_code}) "
            f"wilson={stock.wilson_score:.4f} hit_rate={stock.hit_rate:.1%} "
            f"({stock.correct}/{stock.correct + stock.incorrect})"
        )


def run(creator_id: Optional[str], top: Optional[int]) -> None:
    if creator_id:
        score = score_user(load_records(creator_id))
        if score is None:
            print(f"未找到 user_id={creator_id} 的回测记录")
            return
        _print_score(score)
        return

    scores = score_all_users()
    if top:
        scores = scores[:top]
    if not scores:
        print("未找到任何回测记录，请先运行 backtest_run.py")
        return
    for score in scores:
        _print_score(score)


def main() -> None:
    parser = argparse.ArgumentParser(description="雪球用户预测可信度评分")
    parser.add_argument("--creator_id", default=None, help="只看该用户 (雪球用户 ID)")
    parser.add_argument("--top", type=int, default=None, help="只显示前 N 名 (按整体 Wilson 分数排序)")
    args = parser.parse_args()

    run(args.creator_id, args.top)


if __name__ == "__main__":
    main()
