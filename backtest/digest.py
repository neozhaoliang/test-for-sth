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
用户观点摘录 (digest): 对每个用户的历史验证记录做离线聚合与提炼，
生成可直接用于报告/问答的总结文件。

- build: 加载全部验证记录, 按 (user_id, stock_code) 分组; 每组内 verdict=correct
  的论据是"预测被后续股价走势验证正确"的有价值观点。多条正确论据时用 LLM 提炼成
  一段连贯观点摘要 (按内容哈希缓存, 内容不变不重复调用 LLM); 只有一条时直接用
  原论据, 不做无谓的 LLM 调用。
- 产物: data/xueqiu/digest/user_digests.jsonl, 每行一个 (user, stock) 条目:
  {user_id, user_nickname, stock_code, stock_name, correct, incorrect, total,
   hit_rate, summary, theses, last_verified_at}
- load: 进程内缓存, 供报告生成按股票查询、问答按用户查询。
"""

import asyncio
import hashlib
import json
import os
from typing import Dict, List, Optional

import config
from backtest import score
from backtest.llm_client import call_text
from tools.time_util import get_date_str_from_unix_time
from tools.utils import utils

_DISTILL_CONCURRENCY = 3

_DISTILL_PROMPT = """以下是一位雪球用户"{nickname}"关于"{stock_name}"({stock_code})的多条预测论据，这些预测发布后都已被后续股价走势验证为正确。请把这几条论据整合提炼成一段连贯的观点总结 (该用户看好/看空什么、核心论据是什么、依据什么数据或逻辑)，300字以内，简洁要点式中文。直接输出内容本身，不要"以下是"之类的说明文字，也不要自我介绍。

预测与论据 (按时间排列):
{theses_block}"""


def _digest_dir() -> str:
    base = config.SAVE_DATA_PATH if config.SAVE_DATA_PATH else "data"
    return os.path.join(base, "xueqiu", "digest")


def _digest_path() -> str:
    return os.path.join(_digest_dir(), "user_digests.jsonl")


def _distill_cache_path() -> str:
    return os.path.join(_digest_dir(), "distill_cache.jsonl")


def _group_hash(key_parts: str) -> str:
    return hashlib.sha256(key_parts.encode("utf-8")).hexdigest()[:16]


def _read_distill_cache() -> Dict[str, Dict]:
    """cache_key -> {content_hash, summary}"""
    cache: Dict[str, Dict] = {}
    path = _distill_cache_path()
    if not os.path.exists(path):
        return cache
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                cache[rec["cache_key"]] = rec
    except (OSError, json.JSONDecodeError) as e:
        utils.logger.error(f"[digest] 读取提炼缓存失败: {e}")
    return cache


def _append_distill_cache(rec: Dict) -> None:
    os.makedirs(os.path.dirname(_distill_cache_path()), exist_ok=True)
    with open(_distill_cache_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _date_str(unix_sec_or_ms: int) -> str:
    if not unix_sec_or_ms:
        return ""
    try:
        return get_date_str_from_unix_time(unix_sec_or_ms)
    except Exception:
        return ""


async def _distill_group(
    user_nickname: str, stock_code: str, stock_name: str, theses: List[Dict]
) -> str:
    """把同一用户对同一只股票的多条正确论据提炼成一段观点总结 (带哈希缓存)。"""
    parts = "\n".join(
        f"{t.get('predicted_at') or t.get('date') or ''} {t.get('direction', '')}: {t.get('thesis', '')}"
        for t in theses
    )
    content_hash = _group_hash(parts)
    cache = _read_distill_cache()
    for rec in cache.values():
        if rec.get("content_hash") == content_hash and rec.get("summary"):
            return rec["summary"]

    theses_block = "\n".join(
        f"- [{t.get('date', '')}] {t.get('direction', '')}: {t.get('thesis', '')}" for t in theses
    )
    prompt = _DISTILL_PROMPT.format(
        nickname=user_nickname,
        stock_name=stock_name or stock_code,
        stock_code=stock_code,
        theses_block=theses_block,
    )
    summary = (await call_text(prompt, max_tokens=768) or "").strip()
    if summary:
        _append_distill_cache({"content_hash": content_hash, "summary": summary})
    else:
        utils.logger.warning(
            f"[digest] {user_nickname}({stock_code}) 观点提炼失败, 回退为原始论据拼接"
        )
        summary = "；".join(t.get("thesis", "") for t in theses)
    return summary


async def build_digests(records: Optional[List[Dict]] = None) -> List[Dict]:
    """
    从验证记录聚合出每个 (用户, 股票) 的观点摘录条目并原子写入 digest 文件。
    records 为 None 时加载全部验证记录。返回写入的条目列表。
    """
    if records is None:
        records = score.load_records()

    groups: Dict[str, Dict] = {}
    for r in records:
        key = (str(r.get("user_id") or ""), str(r.get("stock_code") or ""))
        if not key[0] or not key[1]:
            continue
        g = groups.setdefault(key, {"correct": [], "incorrect": 0, "meta": r})
        if r.get("verdict") == "correct":
            g["correct"].append(r)
        elif r.get("verdict") == "incorrect":
            g["incorrect"] += 1

    entries: List[Dict] = []
    semaphore = asyncio.Semaphore(_DISTILL_CONCURRENCY)

    async def _one(key) -> Optional[Dict]:
        user_id, stock_code = key
        g = groups[key]
        correct_recs = g["correct"]
        if not correct_recs:
            return None  # 没有任何命中记录的用户-股票组不进摘录文件
        meta = g["meta"]
        # 论据去重 (同一条帖子可能被重复回测)
        seen_thesis = set()
        theses: List[Dict] = []
        for r in sorted(correct_recs, key=lambda x: x.get("predicted_at") or 0):
            t = (r.get("thesis") or "").strip()
            if not t or t in seen_thesis:
                continue
            seen_thesis.add(t)
            theses.append(
                {
                    "date": _date_str(r.get("predicted_at") or 0),
                    "direction": r.get("direction", ""),
                    "thesis": t,
                }
            )
        if not theses:
            return None
        if len(theses) == 1:
            summary = theses[0]["thesis"]
        else:
            async with semaphore:
                summary = await _distill_group(
                    meta.get("user_nickname", ""), stock_code, meta.get("stock_name", ""), theses
                )
        total = len(correct_recs) + g["incorrect"]
        return {
            "user_id": user_id,
            "user_nickname": meta.get("user_nickname", ""),
            "stock_code": stock_code,
            "stock_name": meta.get("stock_name", ""),
            "correct": len(correct_recs),
            "incorrect": g["incorrect"],
            "total": total,
            "hit_rate": round(len(correct_recs) / total, 4) if total else 0.0,
            "summary": summary,
            "theses": theses,
            "last_verified_at": max(r.get("verified_at") or 0 for r in correct_recs),
        }

    results = await asyncio.gather(*(_one(k) for k in groups))
    entries = [e for e in results if e is not None]
    entries.sort(key=lambda e: (e["user_id"], -e["correct"], e["stock_code"]))

    os.makedirs(_digest_dir(), exist_ok=True)
    tmp_path = _digest_path() + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp_path, _digest_path())

    invalidate()
    utils.logger.info(f"[digest] 摘录文件已更新: {len(entries)} 个 (用户, 股票) 条目 -> {_digest_path()}")
    return entries


_cache: Optional[List[Dict]] = None


def invalidate() -> None:
    """摘录文件重建后清空进程内缓存，下次 load 时重新读取。"""
    global _cache
    _cache = None


def load_digests() -> List[Dict]:
    """加载摘录文件 (进程内缓存)。文件不存在时返回空列表。"""
    global _cache
    if _cache is not None:
        return _cache
    _cache = []
    path = _digest_path()
    if not os.path.exists(path):
        return _cache
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                _cache.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as e:
        utils.logger.error(f"[digest] 读取摘录文件失败: {e}")
        _cache = []
    return _cache


def views_for_stock(stock_code: str) -> List[Dict]:
    """某只股票的全部用户观点摘录，按命中率降序 (预测正确即有价值的观点优先)。"""
    views = [e for e in load_digests() if e.get("stock_code") == stock_code]
    views.sort(key=lambda e: (-e.get("hit_rate", 0.0), -e.get("correct", 0)))
    return views


def views_for_user_stock(user_id: str, stock_code: str) -> List[Dict]:
    """某用户对某只股票的观点摘录条目 (通常 0 或 1 条)。"""
    return [
        e
        for e in load_digests()
        if str(e.get("user_id") or "") == str(user_id) and e.get("stock_code") == stock_code
    ]


def views_for_user(user_id: str) -> List[Dict]:
    """某用户对其谈论过的全部股票的观点摘录，按命中数降序。"""
    views = [e for e in load_digests() if str(e.get("user_id") or "") == str(user_id)]
    views.sort(key=lambda e: (-e.get("correct", 0), e.get("stock_code") or ""))
    return views
