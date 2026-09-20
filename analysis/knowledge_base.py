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
知识库: 与"历史验证过可信度的用户观点"不同,这里存放的是长文本类背景资料
(比如老木匠专栏的直播文字稿) —— 不参与 Wilson 分数验证,而是作为分析时的
背景知识始终加载,供 LLM 在生成报告时参考。

原始文字稿包含大量闲聊、寒暄、与投资无关的内容,直接整段塞给 LLM 会稀释
真正有价值的投资观点 (投资哲学、筹码博弈、行情判断、市场动态等),而这些
观点往往不会直接点名某只股票,用股票名称/代码做正则或关键词匹配一定会漏掉
大量有价值的内容。因此在加载阶段先用 LLM 把每篇原文提炼成"只保留投资观点"
的摘要 (去掉闲聊寒暄),提炼结果按内容哈希缓存到磁盘,原文不变则不重新调用
LLM。生成报告时不再按股票匹配筛选,而是把全部提炼摘要都交给 agent。

设计上不针对老木匠专栏做特殊处理: 新增一路知识来源只需要在 _SOURCES 里
注册一个新条目 (jsonl 所在目录 + 标题/正文字段名),不需要改动加载、提炼、
缓存、或 report.py 的调用方式。

进程启动时通过 ensure_loaded() 预加载并提炼一次,之后复用内存缓存。
"""

import asyncio
import glob
import hashlib
import json
import os
from typing import Dict, List, NamedTuple, Optional

from tools.utils import utils

_ROOT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_CACHE_PATH = os.path.join(_ROOT_DATA_DIR, "knowledge_base", "distilled_cache.jsonl")

_DISTILL_PROMPT = """以下是一篇投资相关的直播文字稿/专栏原文，包含大量闲聊、寒暄、与投资无关的内容。
请你提炼出其中真正有价值的投资观点，包括但不限于: 投资哲学与方法论、筹码博弈/资金面判断、
行情走势判断、宏观/市场动态、行业与个股看法、风险提示。忽略闲聊、寒暄、自我介绍、与投资无关的内容。
如果原文几乎没有任何投资相关内容，直接返回空字符串。

用简洁的要点式中文输出提炼结果，不要输出"以下是提炼结果"之类的说明文字，直接输出内容本身，300字以内。

原文标题: {title}

原文内容:
{content}"""

_MAX_DISTILL_CHARS = 6000  # 原文超长时截断，避免单次 LLM 调用过大
_DISTILL_CONCURRENCY = 3


class KnowledgeSource(NamedTuple):
    """一路知识库来源的注册信息。"""
    name: str  # 来源标识 (用于日志/展示)，如 "bili_laomujiang"
    glob_pattern: str  # 相对 data/ 目录的 glob，如 "bili/jsonl/opus_opus_*.jsonl"
    id_field: str
    title_field: str
    content_field: str
    time_field: str  # 用于排序的数值型时间字段 (如 last_modify_ts)；无则传空字符串


_SOURCES: List[KnowledgeSource] = [
    KnowledgeSource(
        name="bili_laomujiang",
        glob_pattern=os.path.join("bili", "jsonl", "opus_opus_*.jsonl"),
        id_field="opus_id",
        title_field="title",
        content_field="content",
        time_field="last_modify_ts",
    ),
]


class KnowledgeEntry(NamedTuple):
    source: str
    entry_id: str
    title: str
    raw_content: str
    distilled: str  # 提炼后的投资观点摘要，未提炼成功时为空字符串
    timestamp: int


_cache: Optional[List[KnowledgeEntry]] = None


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _cache_key(source_name: str, entry_id: str) -> str:
    return f"{source_name}:{entry_id}"


def _load_raw_entries(source: KnowledgeSource) -> List[Dict]:
    raws: List[Dict] = []
    pattern = os.path.join(_ROOT_DATA_DIR, source.glob_pattern)
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    raws.append(
                        {
                            "source": source.name,
                            "entry_id": str(rec.get(source.id_field, "")),
                            "title": str(rec.get(source.title_field, "")),
                            "content": str(rec.get(source.content_field, "")),
                            "timestamp": int(rec.get(source.time_field) or 0),
                        }
                    )
        except (OSError, json.JSONDecodeError) as e:
            utils.logger.error(f"[knowledge_base] 加载知识库文件失败 {path}: {e}")
    return raws


def _read_distill_cache() -> Dict[str, Dict]:
    """cache_key -> {content_hash, distilled}"""
    if not os.path.exists(_CACHE_PATH):
        return {}
    cache: Dict[str, Dict] = {}
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                cache[rec["cache_key"]] = rec
    except (OSError, json.JSONDecodeError) as e:
        utils.logger.error(f"[knowledge_base] 读取提炼缓存失败: {e}")
    return cache


def _append_distill_cache(rec: Dict) -> None:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    with open(_CACHE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _rewrite_distill_cache(records: List[Dict]) -> None:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def _distill_one(raw: Dict) -> str:
    from backtest.llm_client import call_text

    content = raw["content"][:_MAX_DISTILL_CHARS]
    prompt = _DISTILL_PROMPT.format(title=raw["title"], content=content)
    result = await call_text(prompt, max_tokens=768)
    return (result or "").strip()


async def _distill_missing(raws: List[Dict], cache: Dict[str, Dict]) -> Dict[str, Dict]:
    """对缓存中缺失或原文哈希已变化的条目重新提炼，返回更新后的完整缓存 (cache_key -> record)。"""
    to_distill: List[Dict] = []
    for raw in raws:
        key = _cache_key(raw["source"], raw["entry_id"])
        content_hash = _content_hash(raw["content"])
        cached = cache.get(key)
        if cached and cached.get("content_hash") == content_hash:
            continue
        to_distill.append({**raw, "cache_key": key, "content_hash": content_hash})

    if not to_distill:
        return cache

    utils.logger.info(f"[knowledge_base] 需要提炼 {len(to_distill)} 条知识库条目 (新增/内容变化)")
    semaphore = asyncio.Semaphore(_DISTILL_CONCURRENCY)

    async def _run(raw: Dict) -> Dict:
        async with semaphore:
            distilled = await _distill_one(raw)
        return {
            "cache_key": raw["cache_key"],
            "content_hash": raw["content_hash"],
            "source": raw["source"],
            "entry_id": raw["entry_id"],
            "title": raw["title"],
            "distilled": distilled,
        }

    results = await asyncio.gather(*(_run(raw) for raw in to_distill))
    updated = dict(cache)
    for rec in results:
        updated[rec["cache_key"]] = rec
        _append_distill_cache(rec)
    return updated


async def ensure_loaded() -> List[KnowledgeEntry]:
    """
    加载全部已注册知识库来源；原文若不在缓存中或内容有变化，先用 LLM 提炼投资观点
    再缓存到磁盘。进程内只做一次，之后复用内存缓存。
    """
    global _cache
    if _cache is not None:
        return _cache

    raws: List[Dict] = []
    for source in _SOURCES:
        raws.extend(_load_raw_entries(source))

    disk_cache = _read_distill_cache()
    disk_cache = await _distill_missing(raws, disk_cache)

    entries: List[KnowledgeEntry] = []
    for raw in raws:
        key = _cache_key(raw["source"], raw["entry_id"])
        rec = disk_cache.get(key, {})
        entries.append(
            KnowledgeEntry(
                source=raw["source"],
                entry_id=raw["entry_id"],
                title=raw["title"],
                raw_content=raw["content"],
                distilled=rec.get("distilled", ""),
                timestamp=raw["timestamp"],
            )
        )
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    _cache = entries
    utils.logger.info(f"[knowledge_base] 已加载 {len(entries)} 条知识库条目 (来源数: {len(_SOURCES)})")
    return _cache


def get_all_distilled() -> List[KnowledgeEntry]:
    """返回已加载的知识库条目 (仅供已调用过 ensure_loaded() 之后使用)，不做任何筛选。"""
    return _cache or []
