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


import asyncio
import json
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from playwright.async_api import Page

import config
from tools import utils

from .exception import CrawlInterruptedError, DataFetchError, WafChallengeError

XUEQIU_HOST = "https://xueqiu.com"

# 取数走顶层导航而不是页面内 XHR: 阿里云 WAF 对 XHR 请求持续下发挑战
# (即使注入 stealth、手动登录也一样), 而顶层导航中的 JS 挑战会在真实
# 浏览器里自动解析并重载出真实内容——实测导航到接口 URL 后页面直接
# 渲染为 JSON 文本。挑战页特征: renderData/_waf_ token (JS 挑战, 可自动
# 通过) 或"滑动验证页面" (滑块, 需要人工, 本项目不使用)。
_NAV_TIMEOUT_S = 30


class XueqiuClient:
    """雪球数据客户端 — 所有接口请求均通过真实页面导航发出 (导航自动通过
    WAF JS 挑战), 不使用 XHR/fetch/裸 HTTP。"""

    def __init__(self, playwright_page: Page):
        self.playwright_page = playwright_page
        self._host = XUEQIU_HOST

    async def _nav_json(self, url: str) -> Dict[str, Any]:
        """
        导航到接口 URL 并读取渲染出的 JSON。

        Raises:
            WafChallengeError: 响应为 WAF 验证页 (JS 挑战未自动通过/滑块) 而非 JSON
            DataFetchError: 请求失败/超时/返回错误
        """
        try:
            await asyncio.wait_for(
                self.playwright_page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_S * 1000),
                timeout=_NAV_TIMEOUT_S + 15,
            )
        except asyncio.TimeoutError as e:
            raise DataFetchError(f"Xueqiu API navigation timeout, url={url}") from e
        except Exception as e:
            raise DataFetchError(f"Xueqiu API navigation failed, url={url}: {e}") from e

        # JS 挑战在真实浏览器中自动解析后会重载出真实内容 (JSON 文本)
        body = ""
        for _ in range(12):
            try:
                body = await self.playwright_page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )
            except Exception:
                body = ""
            if body.strip().startswith(("{", "[")):
                break
            if "aliyun_waf" not in body and "滑动验证" not in body:
                break
            await asyncio.sleep(1)

        stripped = body.strip()
        if not stripped.startswith(("{", "[")):
            if "aliyun_waf" in stripped:
                raise WafChallengeError(f"Xueqiu API blocked by WAF challenge, url={url}")
            if "滑动验证" in stripped:
                raise WafChallengeError(f"Xueqiu API blocked by WAF slider, url={url}")
            raise DataFetchError(f"Xueqiu API returned non-JSON, url={url}, body={stripped[:300]}")
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise DataFetchError(f"Xueqiu API JSON decode failed, url={url}, body={stripped[:300]}") from exc

    async def _nav_json_with_retry(self, url: str, retries: int = 3) -> Dict[str, Any]:
        """
        带自适应重试的导航式 JSON 请求。

        正常情况下不做任何等待 (无速率限制);
        仅当请求被 WAF 拦截时才等待退避后重试导航。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                return await self._nav_json(url)
            except (WafChallengeError, DataFetchError) as e:
                last_exc = e
                if attempt >= retries:
                    break
                wait = min(5 * (2 ** (attempt - 1)), 20)
                utils.logger.warning(
                    f"[XueqiuClient] 请求被拦截/失败 (第 {attempt}/{retries} 次): {e}, "
                    f"{wait}s 后重试"
                )
                await asyncio.sleep(wait)
        raise last_exc  # type: ignore

    async def get_user_posts(self, user_id: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """
        获取用户发帖列表 (timeline, 含原创与转发)。

        Args:
            user_id: 用户数字 ID
            page: 页码 (从 1 开始)
            page_size: 每页数量

        Returns:
            API 原始响应: {"statuses": [...], "totalCount": N, "max_id": ...}
        """
        params = {"user_id": user_id, "page": page, "page_size": page_size}
        url = f"{self._host}/v4/statuses/user_timeline.json?{urlencode(params)}"
        utils.logger.info(f"[XueqiuClient.get_user_posts] user_id={user_id} page={page}")
        return await self._nav_json_with_retry(url)

    async def get_status_detail(self, status_id: str) -> Dict[str, Any]:
        """
        获取单条帖子详情。

        Args:
            status_id: 帖子 ID

        Returns:
            API 原始响应 (帖子 JSON)
        """
        url = f"{self._host}/statuses/show.json?id={status_id}"
        utils.logger.info(f"[XueqiuClient.get_status_detail] status_id={status_id}")
        return await self._nav_json_with_retry(url)

    async def get_status_comments(self, status_id: str, page: int = 1, count: int = 20) -> Dict[str, Any]:
        """
        获取帖子下的评论列表。

        Args:
            status_id: 帖子 ID
            page: 页码 (从 1 开始)
            count: 每页数量

        Returns:
            API 原始响应: {"comments": [...], "count": N, ...}
        """
        params = {"id": status_id, "page": page, "count": count}
        url = f"{self._host}/statuses/comments.json?{urlencode(params)}"
        utils.logger.info(f"[XueqiuClient.get_status_comments] status_id={status_id} page={page}")
        return await self._nav_json_with_retry(url)

    async def get_user_comments(self, user_id: str, max_id: int = -1, size: int = 20) -> Dict[str, Any]:
        """
        获取用户发出的回复/评论列表 (主页 "回复" tab 对应接口, 游标分页)。

        Args:
            user_id: 用户数字 ID
            max_id: 分页游标, -1 表示从最新开始; 后续传上一页响应的 next_max_id
            size: 每页数量

        Returns:
            API 原始响应: {"items": [...], "next_max_id": ..., "next_id": ...}
        """
        params = {"user_id": user_id, "size": size, "max_id": max_id}
        url = f"{self._host}/statuses/user/comments.json?{urlencode(params)}"
        utils.logger.info(f"[XueqiuClient.get_user_comments] user_id={user_id} max_id={max_id}")
        return await self._nav_json_with_retry(url)

    async def get_all_user_comments(
        self,
        user_id: str,
        callback: Optional[Callable] = None,
        max_count: int = 0,
        start_max_id: int = -1,
    ) -> List[Dict[str, Any]]:
        """
        获取用户全部回复/评论 (游标分页直至无更多)。

        Args:
            user_id: 用户数字 ID
            callback: 每页回调, 用于增量存储
            max_count: 最大条数, 0 表示不限制 (抓取全部)
            start_max_id: 起始分页游标 (用于断点续爬), -1 表示从最新开始

        Returns:
            全部评论原始 JSON 列表

        Raises:
            CrawlInterruptedError: 被 WAF 中断, 携带中断游标可从此处恢复
        """
        result: List[Dict[str, Any]] = []
        max_id = start_max_id
        while True:
            try:
                res = await self.get_user_comments(user_id, max_id=max_id)
            except (WafChallengeError, DataFetchError) as e:
                utils.logger.warning(
                    f"[XueqiuClient.get_all_user_comments] max_id={max_id} 被 WAF 中断: {e}"
                )
                raise CrawlInterruptedError(page=max_id, cause=e) from e
            items = res.get("items") or []
            if not items:
                utils.logger.info(f"[XueqiuClient.get_all_user_comments] max_id={max_id} 无数据, 回复抓取结束")
                break
            if callback:
                await callback(items)
            result.extend(items)
            if max_count and len(result) >= max_count:
                result = result[:max_count]
                break
            next_max_id = res.get("next_max_id")
            if next_max_id is None or next_max_id == max_id:
                utils.logger.info(f"[XueqiuClient.get_all_user_comments] 游标未推进, 回复抓取结束")
                break
            max_id = next_max_id
            # 可选节流: --pace N 时每页之间等待 N 秒 (降低触发 WAF 风控的概率)
            if getattr(config, "XUEQIU_PACE_SEC", 0) > 0:
                await asyncio.sleep(config.XUEQIU_PACE_SEC)
        utils.logger.info(f"[XueqiuClient.get_all_user_comments] user_id={user_id} 共获取 {len(result)} 条回复")
        return result

    async def get_all_user_posts(
        self,
        user_id: str,
        callback: Optional[Callable] = None,
        max_count: int = 0,
        start_page: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        获取用户全部发帖 (分页直至无更多)。

        Args:
            user_id: 用户数字 ID
            callback: 每页回调, 用于增量存储
            max_count: 最大条数, 0 表示不限制 (抓取全部)
            start_page: 起始页码 (用于断点续爬)

        Returns:
            全部帖子原始 JSON 列表

        Raises:
            CrawlInterruptedError: 被 WAF 中断, 携带中断页码可从此页恢复
        """
        result: List[Dict[str, Any]] = []
        page = start_page
        while True:
            try:
                res = await self.get_user_posts(user_id, page=page)
            except (WafChallengeError, DataFetchError) as e:
                utils.logger.warning(
                    f"[XueqiuClient.get_all_user_posts] page={page} 被 WAF 中断: {e}"
                )
                raise CrawlInterruptedError(page=page, cause=e) from e
            statuses = res.get("statuses") or res.get("list") or []
            if not statuses:
                utils.logger.info(f"[XueqiuClient.get_all_user_posts] page={page} 无数据, 发帖抓取结束")
                break
            if callback:
                await callback(statuses)
            result.extend(statuses)
            if max_count and len(result) >= max_count:
                result = result[:max_count]
                break
            # 可选节流: --pace N 时每页之间等待 N 秒 (降低触发 WAF 风控的概率)
            if getattr(config, "XUEQIU_PACE_SEC", 0) > 0:
                await asyncio.sleep(config.XUEQIU_PACE_SEC)
            page += 1
        utils.logger.info(f"[XueqiuClient.get_all_user_posts] user_id={user_id} 共获取 {len(result)} 条发帖")
        return result
