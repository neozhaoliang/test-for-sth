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
从雪球帖子中抽取股票代码提及，并清洗出纯文本供后续 LLM 分析使用。
"""

import re
from typing import Any, Dict, List, TypedDict

STOCK_TAG_RE = re.compile(r"\$([^$]+?)\(((?:SH|SZ|sh|sz)\d{6})\)\$")
HTML_TAG_RE = re.compile(r"<[^>]+>")


class StockMention(TypedDict):
    name: str
    code: str


class ExtractedPost(TypedDict):
    post: Dict[str, Any]
    text: str
    stocks: List[StockMention]


def strip_html(raw_html: str) -> str:
    """去除 HTML 标签，保留纯文本内容。"""
    if not raw_html:
        return ""
    text = HTML_TAG_RE.sub("", raw_html)
    return text.strip()


def extract_stock_mentions(description: str) -> List[StockMention]:
    """
    从帖子 description 原始 HTML 中提取股票代码提及，按代码去重。
    同一股票的 `<a href=...>$name(code)$</a>` 锚点和裸 `$name(code)$` 标签内容相同，
    用 dict 按 code 去重即可覆盖两种写法。
    """
    seen: Dict[str, str] = {}
    for name, code in STOCK_TAG_RE.findall(description or ""):
        seen[code.upper()] = name
    return [{"name": name, "code": code} for code, name in seen.items()]


def extract_from_posts(posts: List[Dict[str, Any]]) -> List[ExtractedPost]:
    """
    遍历帖子列表，只处理原创帖 (status_type == "original")：
    转发帖不代表该用户自己的预测，跳过。

    stocks 为 $标签$ 形式的提及列表——但很多用户 (尤其老派大 V) 从不打标签，
    直接用文字讨论股票。这类帖子 stocks 为空，由 classify_post 里的 LLM
    从正文自行识别股票 (检测+判定一次调用完成)。
    """
    result: List[ExtractedPost] = []
    for post in posts:
        if post.get("status_type") != "original":
            continue
        description = post.get("description") or ""
        text = strip_html(description)
        if not text:
            continue
        result.append({
            "post": post,
            "text": text,
            "stocks": extract_stock_mentions(description),
        })
    return result
