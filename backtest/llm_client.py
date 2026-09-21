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

_RETRY_MAX_TOKENS = 24576

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
    text, _ = await _call_llm_raw_ex(prompt, max_tokens)
    return text


async def _call_llm_raw_ex(prompt: str, max_tokens: int) -> tuple:
    """
    调用 LLM，返回 (原始文本, stop_reason)。调用异常时返回 (None, None)。

    一律走流式: 非流式接口在 max_tokens 较大时会被 SDK 直接拒绝
    ("Streaming is required for operations that may take longer than 10 minutes")，
    而报告类 prompt 需要给推理模型留足思考预算，上限必须开得比较大。
    注意只取 text 块——推理模型的响应里还有 thinking 块，它不是答案。
    """
    client = _get_client()
    try:
        async with client.messages.stream(
            model=_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = await stream.get_final_message()
        raw_text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        stop_reason = getattr(response, "stop_reason", None)
        if not raw_text.strip():
            # 推理模型把预算全烧在思考上时会返回纯 thinking 响应。若不打这一行，
            # 上层只会看到 "len=0 解析失败"，完全看不出真正原因是预算耗尽。
            thinking_chars = sum(
                len(getattr(b, "thinking", "") or "")
                for b in response.content
                if getattr(b, "type", "") == "thinking"
            )
            utils.logger.error(
                f"[llm_client._call_llm_raw] 无 text 块 (stop_reason={stop_reason}, "
                f"max_tokens={max_tokens}, thinking={thinking_chars}字符) —— "
                f"通常是思考耗尽预算，需提高 max_tokens"
            )
    except Exception as e:
        utils.logger.error(f"[llm_client._call_llm_raw] LLM call failed: {e}")
        return None, None
    return raw_text.strip(), stop_reason


def _parse_json(raw_text: str) -> Optional[Any]:
    text = raw_text
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 模型有时会在 JSON 前后夹带说明性文字，尝试截取最外层的 {...} 或 [...] 再解析一次
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


async def call_json_ex(prompt: str, max_tokens: int = 1024) -> tuple:
    """
    调用 LLM 并要求返回 JSON，返回 (解析结果, stop_reason)。

    暴露 stop_reason 是为了让调用方分辨"模型输出被 max_tokens 截断"和"模型输出了
    不合规的 JSON"——前者重试一次更长的上限就能救回来，后者重试多少次都没用。
    仅在确认被截断时才用 _RETRY_MAX_TOKENS 重试一次，不无条件加长。
    """
    raw_text, stop_reason = await _call_llm_raw_ex(prompt, max_tokens)
    if raw_text is None:
        return None, stop_reason

    parsed = _parse_json(raw_text)
    if parsed is not None:
        return parsed, stop_reason

    if stop_reason == "max_tokens":
        utils.logger.warning(
            f"[llm_client.call_json_ex] 输出被 max_tokens={max_tokens} 截断 "
            f"(len={len(raw_text)})，用 {_RETRY_MAX_TOKENS} 重试一次"
        )
        raw_text, stop_reason = await _call_llm_raw_ex(prompt, _RETRY_MAX_TOKENS)
        if raw_text is not None:
            parsed = _parse_json(raw_text)
            if parsed is not None:
                return parsed, stop_reason

    utils.logger.warning(
        f"[llm_client.call_json_ex] Failed to parse LLM response as JSON "
        f"(stop_reason={stop_reason}, len={len(raw_text)}): "
        f"head={raw_text[:200]!r} tail={raw_text[-200:]!r}"
    )
    return None, stop_reason


async def call_json(prompt: str, max_tokens: int = 1024) -> Optional[Dict[str, Any]]:
    """
    调用 LLM，要求其返回 JSON，解析失败或调用异常时返回 None（调用方需自行跳过该条）。
    """
    parsed, _ = await call_json_ex(prompt, max_tokens)
    return parsed


async def call_text(prompt: str, max_tokens: int = 2048) -> Optional[str]:
    """调用 LLM 生成自由格式文本 (不解析 JSON)，调用异常时返回 None。"""
    return await _call_llm_raw(prompt, max_tokens)
