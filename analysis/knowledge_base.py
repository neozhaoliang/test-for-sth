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

设计上不针对老木匠专栏做特殊处理: 新增一路知识来源只需要在 _SOURCES 里
注册一个新条目 (jsonl 所在目录 + 标题/正文字段名),不需要改动加载、匹配、
或 report.py 的调用方式。

进程内启动时通过 ensure_loaded() 预加载一次,之后复用内存缓存。
"""

import glob
import json
import os
from typing import Dict, List, NamedTuple, Optional

from tools.utils import utils

_ROOT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class KnowledgeSource(NamedTuple):
    """一路知识库来源的注册信息。"""
    name: str  # 来源标识 (用于日志/展示)，如 "bili_laomujiang"
    glob_pattern: str  # 相对 data/ 目录的 glob，如 "bili/jsonl/opus_opus_*.jsonl"
    title_field: str
    content_field: str
    time_field: str  # 用于排序的数值型时间字段 (如 last_modify_ts)；无则传空字符串


_SOURCES: List[KnowledgeSource] = [
    KnowledgeSource(
        name="bili_laomujiang",
        glob_pattern=os.path.join("bili", "jsonl", "opus_opus_*.jsonl"),
        title_field="title",
        content_field="content",
        time_field="last_modify_ts",
    ),
]


class KnowledgeEntry(NamedTuple):
    source: str
    title: str
    content: str
    timestamp: int


_cache: Optional[List[KnowledgeEntry]] = None


def _load_source(source: KnowledgeSource) -> List[KnowledgeEntry]:
    entries: List[KnowledgeEntry] = []
    pattern = os.path.join(_ROOT_DATA_DIR, source.glob_pattern)
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    entries.append(
                        KnowledgeEntry(
                            source=source.name,
                            title=str(rec.get(source.title_field, "")),
                            content=str(rec.get(source.content_field, "")),
                            timestamp=int(rec.get(source.time_field) or 0),
                        )
                    )
        except (OSError, json.JSONDecodeError) as e:
            utils.logger.error(f"[knowledge_base] 加载知识库文件失败 {path}: {e}")
    return entries


def ensure_loaded() -> List[KnowledgeEntry]:
    """加载全部已注册知识库来源，进程内只加载一次。"""
    global _cache
    if _cache is None:
        entries: List[KnowledgeEntry] = []
        for source in _SOURCES:
            entries.extend(_load_source(source))
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        _cache = entries
        utils.logger.info(f"[knowledge_base] 已加载 {len(entries)} 条知识库条目 (来源数: {len(_SOURCES)})")
    return _cache


def find_relevant_entries(keywords: List[str], limit: int = 3) -> List[KnowledgeEntry]:
    """
    在已加载的知识库中查找标题或正文包含任一 keyword 的条目，按时间新到旧取前 limit 条。
    keywords 为空时返回空列表 (不做无差别匹配)。
    """
    if not keywords:
        return []
    entries = ensure_loaded()
    matched = [
        e for e in entries
        if any(kw and (kw in e.title or kw in e.content) for kw in keywords)
    ]
    return matched[:limit]
