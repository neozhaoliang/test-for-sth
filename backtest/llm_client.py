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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from tools.utils import utils

_ROOT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
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


_JSON_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)\n?```", re.DOTALL)


def _scan_balanced_fragments(text: str) -> List[str]:
    """扫描出所有顶层配对闭合的 {...}/[...] 片段 (字符串字面量内的括号不参与配对)，
    用于从夹带说明文字的 LLM 输出里截出真正的 JSON 部分。"""
    fragments: List[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] not in "{[":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, n):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    fragments.append(text[i : j + 1])
                    i = j + 1
                    break
        else:
            break  # 该片段未配对闭合 (输出被截断)，后面的也不再尝试
    return fragments


def _fix_json_text(text: str) -> str:
    """尽力修复模型 JSON 输出的常见语法问题 (纯字符串处理，不调用 LLM):
    - 字符串字面量内的原始换行/制表符 -> \\n / \\t (合法 JSON 不允许原始控制字符，
      而模型常把"分条换行"写成真实换行)
    - 对象/数组最后一个元素后的尾随逗号 -> 删除
    合法 JSON 经过此函数后内容不变 (合法 JSON 的字符串内不会出现原始控制字符)。
    """
    out: List[str] = []
    in_str = False
    esc = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                out.append(c)
                esc = False
            elif c == "\\":
                out.append(c)
                esc = True
            elif c == "\n":
                out.append("\\n")
            elif c == "\r":
                out.append("\\r")
            elif c == "\t":
                out.append("\\t")
            else:
                out.append(c)
                if c == '"':
                    in_str = False
        else:
            if c == '"':
                in_str = True
                out.append(c)
            elif c == ",":
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] in "}]":
                    pass  # 尾随逗号: 丢弃
                else:
                    out.append(c)
            else:
                out.append(c)
        i += 1
    return "".join(out)


def _parse_json(raw_text: str) -> Optional[Any]:
    """尽量宽容地解析 LLM 输出: 支持纯 JSON、``` 围栏 (可出现在说明文字之后)、
    以及前后夹带说明文字的裸 JSON。说明文字里的短括号片段 (如 "条目[49]") 也
    可能被误当成 JSON，所以配对片段按长度降序尝试，真正的答案通常更长。
    候选先直接 json.loads，失败后再过一遍 _fix_json_text 修复常见语法问题。"""
    text = raw_text.strip()
    if not text:
        return None
    candidates: List[str] = []

    def _add(c: str) -> None:
        c = c.strip()
        if c and c not in candidates:
            candidates.append(c)

    _add(text)
    if text.startswith("```") and text.count("```") >= 2:
        _add(text.split("```")[1].strip())
    for m in _JSON_FENCE_RE.finditer(text):
        _add(m.group(1))
    fragments = _scan_balanced_fragments(text)
    for f in sorted(fragments, key=len, reverse=True):
        _add(f)
    for f in fragments:
        _add(f)

    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass
        fixed = _fix_json_text(c)
        if fixed != c:
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                continue
    return None


_REPAIR_PROMPT = (
    "你上一次的输出无法解析为合法 JSON。请把下面引用的内容改写为严格合法的 JSON，"
    "直接输出 JSON 本身，不要任何解释、前言或代码围栏。{requirements}\n\n---\n{raw}\n---"
)


_REFUSAL_PHRASES = (
    "很抱歉，我无法回答您的问题",
    "抱歉，我无法回答您的问题",
    "我无法协助完成此请求",
    "我不能协助处理此请求",
    "i cannot assist with that request",
    "i can't help with that request",
    "i'm sorry, but i can't",
)


def _is_model_refusal(raw_text: Optional[str]) -> bool:
    """Do not feed a provider/model refusal back as malformed JSON for repair."""
    text = str(raw_text or "").strip().lower()
    # Restrict to short, standalone refusals; legitimate reports might mention
    # the same text as examples or quoted evidence.
    return bool(text) and len(text) <= 400 and any(
        text.startswith(phrase.lower()) for phrase in _REFUSAL_PHRASES
    )


async def call_json_ex(
    prompt: str, max_tokens: int = 1024, repair_requirements: str = ""
) -> tuple:
    """
    调用 LLM 并要求返回 JSON，返回 (解析结果, stop_reason)。

    暴露 stop_reason 是为了让调用方分辨"模型输出被 max_tokens 截断"和"模型输出了
    不合规的 JSON"——前者重试一次更长的上限就能救回来，后者重试多少次都没用。
    仅在确认被截断时才用 _RETRY_MAX_TOKENS 重试一次，不无条件加长。
    若输出完整但解析失败，则做一次"修复式"重试：让模型把自己的输出改写为合法 JSON。
    repair_requirements 可传入对输出 schema 的要求 (如必含字段与取值枚举)，会附在
    修复提示里，避免模型"修复"出一个格式合法但字段缺失的结果。
    """
    raw_text, stop_reason = await _call_llm_raw_ex(prompt, max_tokens)
    if raw_text is None:
        return None, stop_reason
    if _is_model_refusal(raw_text):
        utils.logger.error(
            f"[llm_client.call_json_ex] 模型或兼容网关拒绝生成结构化输出 "
            f"(model={_MODEL}, stop_reason={stop_reason}, len={len(raw_text)})；"
            "不将拒答送回JSON修复",
        )
        return None, "refusal"

    parsed = _parse_json(raw_text)
    if parsed is not None:
        return parsed, stop_reason

    if stop_reason == "max_tokens":
        utils.logger.warning(
            f"[llm_client.call_json_ex] 输出被 max_tokens={max_tokens} 截断 "
            f"(len={len(raw_text)})，用 {_RETRY_MAX_TOKENS} 重试一次"
        )
        raw_text, stop_reason = await _call_llm_raw_ex(prompt, _RETRY_MAX_TOKENS)
        if _is_model_refusal(raw_text):
            utils.logger.error(
                f"[llm_client.call_json_ex] 增长预算重试后收到模型拒答 (model={_MODEL})",
            )
            return None, "refusal"
        if raw_text is not None:
            parsed = _parse_json(raw_text)
            if parsed is not None:
                return parsed, stop_reason

    if raw_text:
        # 模型自己收尾 (end_turn) 却输出不合规 JSON: 再重试多少次原 prompt 都未必有用，
        # 改为把上一轮输出交还给模型修复。只试一次，失败即按原样放弃。
        utils.logger.warning(
            f"[llm_client.call_json_ex] JSON 解析失败 (stop_reason={stop_reason}, "
            f"len={len(raw_text)})，尝试让模型修复输出后重试一次"
        )
        requirements = (
            f"改写后的 JSON 必须满足以下要求: {repair_requirements}。"
            if repair_requirements
            else ""
        )
        repair_raw, repair_reason = await _call_llm_raw_ex(
            _REPAIR_PROMPT.format(raw=raw_text, requirements=requirements),
            _RETRY_MAX_TOKENS,
        )
        if _is_model_refusal(repair_raw):
            utils.logger.warning(
                f"[llm_client.call_json_ex] JSON修复请求遭到模型拒答 (model={_MODEL})",
            )
            return None, "refusal"
        if repair_raw:
            parsed = _parse_json(repair_raw)
            if parsed is not None:
                return parsed, repair_reason

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


async def call_analysis_with_tools(
    prompt: str,
    tools: List[Dict[str, Any]],
    required_tool_names: List[str],
    submit_tool_name: str,
    max_rounds: int = 10,
    max_tokens: int = 16384,
) -> tuple:
    """
    工具调用循环: 要求模型逐个调用 required_tool_names 里的分析工具 (每个
    工具的参数会被收集), 全部完成后调用 submit_tool_name 提交。

    返回 (analyses, submit_input, stop_reason): analyses 是 {工具名: 参数},
    submit_input 是提交工具的输入 (未调用时为 None), stop_reason 为
    "ok" / "max_rounds" / "error"。模型/网关不支持工具时不会产生 tool_use,
    循环会在 max_rounds 内空转后返回空结果, 由调用方回退到普通单次调用。
    """
    client = _get_client()
    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
    analyses: Dict[str, Any] = {}
    submit_input: Optional[Dict[str, Any]] = None

    for _ in range(max_rounds):
        try:
            async with client.messages.stream(
                model=_MODEL,
                max_tokens=max_tokens,
                tools=tools,
                messages=messages,
            ) as stream:
                response = await stream.get_final_message()
        except Exception as e:
            utils.logger.error(f"[llm_client.call_analysis_with_tools] LLM call failed: {e}")
            return analyses, submit_input, "error"

        stop_reason = getattr(response, "stop_reason", None)
        tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]

        if not tool_uses:
            non_tool_text = "".join(
                getattr(b, "text", "") or ""
                for b in response.content if getattr(b, "type", "") == "text"
            )
            if _is_model_refusal(non_tool_text):
                utils.logger.error(
                    f"[llm_client.call_analysis_with_tools] 模型/网关拒答 "
                    f"(model={_MODEL}, 已分析维度={len(analyses)})，提前停止工具循环"
                )
                return analyses, submit_input, "refusal"
            missing = [t for t in required_tool_names if t not in analyses]
            if missing and stop_reason != "max_tokens":
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"你还有未完成的维度分析: {'、'.join(missing)}。"
                            f"必须逐一调用对应工具完成, 全部完成后再调用 {submit_tool_name}。"
                        ),
                    }
                )
                continue
            if not missing:
                return analyses, submit_input, "ok"
            # 未完成且模型不再调用工具: 放弃
            return analyses, submit_input, "max_rounds"

        for b in tool_uses:
            name = getattr(b, "name", "")
            inp = getattr(b, "input", None) or {}
            if name in required_tool_names and isinstance(inp, dict) and inp.get("analysis"):
                analyses[name] = inp["analysis"]
            elif name == submit_tool_name and isinstance(inp, dict):
                submit_input = inp
        tool_results = [
            {"type": "tool_result", "tool_use_id": getattr(b, "id", ""), "content": "已记录"}
            for b in tool_uses
        ]
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        missing = [t for t in required_tool_names if t not in analyses]
        if not missing and submit_input is not None:
            return analyses, submit_input, "ok"

    return analyses, submit_input, "max_rounds"


async def call_text(prompt: str, max_tokens: int = 2048) -> Optional[str]:
    """调用 LLM 生成自由格式文本 (不解析 JSON)，调用异常时返回 None。"""
    return await _call_llm_raw(prompt, max_tokens)
