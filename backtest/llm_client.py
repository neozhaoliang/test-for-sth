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
极简 LLM 调用封装，用于判断帖子是否包含"有论据支撑的预测"并抽取结构化信息。
认证信息优先从仓库根目录 .env 读取，其次回退系统环境变量（与 Claude Code 的
settings.json env 配置保持一致：ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL，
兼容官方 ANTHROPIC_API_KEY 作为备选）。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from tools.utils import utils

_ROOT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ROOT_ENV_PATH.exists():
    load_dotenv(_ROOT_ENV_PATH)

_MODEL = os.environ.get("BACKTEST_LLM_MODEL", "claude-sonnet-5")

_client: Optional[AsyncAnthropic] = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        if not auth_token:
            raise RuntimeError(
                "ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY not set. "
                "Put it in the repo root .env or export it as an env var."
            )
        base_url = os.environ.get("ANTHROPIC_BASE_URL")
        _client = AsyncAnthropic(auth_token=auth_token, base_url=base_url)
    return _client


async def _call_llm_raw(prompt: str, max_tokens: int) -> Optional[str]:
    """调用 LLM 并返回原始文本，调用异常时返回 None。"""
    client = _get_client()
    try:
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
    except Exception as e:
        utils.logger.error(f"[llm_client._call_llm_raw] LLM call failed: {e}")
        return None
    return raw_text.strip()


async def call_json(prompt: str, max_tokens: int = 1024) -> Optional[Dict[str, Any]]:
    """
    调用 LLM，要求其返回 JSON，解析失败或调用异常时返回 None（调用方需自行跳过该条）。
    """
    raw_text = await _call_llm_raw(prompt, max_tokens)
    if raw_text is None:
        return None

    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        utils.logger.warning(f"[llm_client.call_json] Failed to parse LLM response as JSON: {raw_text[:200]}")
        return None


async def call_text(prompt: str, max_tokens: int = 2048) -> Optional[str]:
    """调用 LLM 生成自由格式文本 (不解析 JSON)，调用异常时返回 None。"""
    return await _call_llm_raw(prompt, max_tokens)
