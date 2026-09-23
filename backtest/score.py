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
用户预测可信度评分。

从 backtest_run.py 落盘的逐条验证记录 (jsonl) 聚合出每个用户的历史命中率，
用 Wilson 区间下界代替原始命中率排序：样本量越少，下界越保守，
天然抑制"只测过 2 条全对"这类高噪声、不可靠的高分假象，不需要硬性最小样本阈值。

粒度：整体 + 按股票代码。行业维度暂缺 (个股->行业映射目前没有可用的免费数据源)。
"""

import glob
import json
import math
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

import config
from model.m_backtest import StockCredibilityScore, UserCredibilityScore

_Z_95 = 1.96  # 95% 置信度对应的标准正态分位数


def wilson_lower_bound(successes: int, total: int, z: float = _Z_95) -> float:
    """
    Wilson score interval 下界。total=0 时返回 0 (无样本，不给任何置信度)。
    """
    if total <= 0:
        return 0.0
    phat = successes / total
    denom = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denom)


def _jsonl_dir() -> str:
    base = config.SAVE_DATA_PATH if config.SAVE_DATA_PATH else "data"
    return os.path.join(base, "xueqiu", "jsonl")


def _record_files(user_id: Optional[str] = None) -> List[str]:
    pattern_id = user_id if user_id else "*"
    pattern = os.path.join(_jsonl_dir(), f"backtest_{pattern_id}_backtest_*.jsonl")
    return sorted(glob.glob(pattern))


def load_records(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """加载指定用户 (或全部用户，user_id=None) 的所有验证记录，跨多个日期文件合并。"""
    records: List[Dict[str, Any]] = []
    for path in _record_files(user_id):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
    return records


def compact_records(user_id: str) -> int:
    """
    清理被新版本记录取代的旧记录: 同一 (status_id, stock_code) 只保留
    出现位置最靠后的那条 (文件按名排序, 新文件在后)。schema 升级后重跑回测
    会留下旧版记录, 必须压缩掉, 否则命中率统计会被重复行翻倍。返回删除行数。
    """
    paths = _record_files(user_id)
    if not paths:
        return 0
    rows: List[tuple] = []  # (file_idx, rec)
    for i, path in enumerate(paths):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append((i, json.loads(line)))
                except json.JSONDecodeError:
                    continue

    last_idx: Dict[tuple, int] = {}
    for i, rec in rows:
        pair = (str(rec.get("status_id") or ""), str(rec.get("stock_code") or ""))
        if pair[0] and pair[1]:
            last_idx[pair] = i

    per_file_kept: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    dropped = 0
    for i, rec in rows:
        pair = (str(rec.get("status_id") or ""), str(rec.get("stock_code") or ""))
        if pair[0] and pair[1] and last_idx[pair] != i:
            dropped += 1
            continue
        per_file_kept[i].append(rec)

    if dropped:
        for i, path in enumerate(paths):
            original_count = sum(1 for j, _ in rows if j == i)
            if i not in per_file_kept or len(per_file_kept[i]) == original_count:
                continue  # 该文件没有行被删除, 不重写
            with open(path, "w", encoding="utf-8") as f:
                for rec in per_file_kept[i]:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        utils.logger.info(f"[score.compact] {user_id} 清理旧版本记录 {dropped} 行")
    return dropped


def _score_stock_group(stock_code: str, stock_name: str, records: List[Dict[str, Any]]) -> StockCredibilityScore:
    correct = sum(1 for r in records if r["verdict"] == "correct")
    incorrect = sum(1 for r in records if r["verdict"] == "incorrect")
    total = correct + incorrect
    hit_rate = (correct / total) if total else 0.0
    return StockCredibilityScore(
        stock_code=stock_code,
        stock_name=stock_name,
        correct=correct,
        incorrect=incorrect,
        hit_rate=round(hit_rate, 4),
        wilson_score=round(wilson_lower_bound(correct, total), 4),
    )


def score_user(records: List[Dict[str, Any]]) -> Optional[UserCredibilityScore]:
    """对单个用户的全部验证记录计算整体 + 按股票的可信度评分。records 为空时返回 None。"""
    if not records:
        return None

    user_id = str(records[0].get("user_id") or "")
    user_nickname = records[0].get("user_nickname", "")

    correct = sum(1 for r in records if r["verdict"] == "correct")
    incorrect = sum(1 for r in records if r["verdict"] == "incorrect")
    inconclusive = sum(1 for r in records if r["verdict"] == "inconclusive")
    total = correct + incorrect
    hit_rate = (correct / total) if total else 0.0

    by_stock_records: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    stock_names: Dict[str, str] = {}
    for r in records:
        if r["verdict"] not in ("correct", "incorrect"):
            continue
        code = r["stock_code"]
        by_stock_records[code].append(r)
        stock_names.setdefault(code, r.get("stock_name", ""))

    by_stock = [
        _score_stock_group(code, stock_names[code], group_records)
        for code, group_records in by_stock_records.items()
    ]
    by_stock.sort(key=lambda s: s.wilson_score, reverse=True)

    return UserCredibilityScore(
        user_id=user_id,
        user_nickname=user_nickname,
        total_predictions=len(records),
        correct=correct,
        incorrect=incorrect,
        inconclusive=inconclusive,
        hit_rate=round(hit_rate, 4),
        wilson_score=round(wilson_lower_bound(correct, total), 4),
        by_stock=by_stock,
    )


def score_all_users() -> List[UserCredibilityScore]:
    """加载所有用户的记录并计算评分，按整体 Wilson 下界降序排列。"""
    all_records = load_records()
    by_user: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in all_records:
        by_user[str(r.get("user_id") or "")].append(r)

    scores = [score_user(records) for records in by_user.values()]
    scores = [s for s in scores if s is not None]
    scores.sort(key=lambda s: s.wilson_score, reverse=True)
    return scores
