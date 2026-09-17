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
轻量雪球浏览器会话，生命周期 = 一次分析任务。

复用 media_platform/xueqiu/core.py 里 XueqiuCrawler 的 CDP 连接 + WAF 等待
逻辑，但不依赖该类的其它状态 (断点续爬/发现等)，只用于拉取候选用户的最新发帖。
"""

import asyncio
import time
from typing import Dict, List, Optional

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

import config
from media_platform.xueqiu.client import XueqiuClient
from media_platform.xueqiu.exception import CrawlInterruptedError, DataFetchError, WafChallengeError
from tools.cdp_browser import CDPBrowserManager
from tools.utils import utils
from tools.waf_slider import is_waf_challenge, solve_waf_slider

_INDEX_URL = "https://xueqiu.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class AnalysisBrowserSession:
    """一次分析任务共享的浏览器会话: 连接 CDP -> 等待 WAF -> 顺序拉取候选用户最新发帖。"""

    def __init__(self) -> None:
        self._playwright_cm = None
        self.browser_context: Optional[BrowserContext] = None
        self.context_page: Optional[Page] = None
        self.xueqiu_client: Optional[XueqiuClient] = None
        self.cdp_manager: Optional[CDPBrowserManager] = None

    async def start(self) -> bool:
        """连接 CDP 浏览器并访问雪球首页，成功返回 True。"""
        self._playwright_cm = async_playwright()
        playwright: Playwright = await self._playwright_cm.__aenter__()

        try:
            self.cdp_manager = CDPBrowserManager()
            self.browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=None,
                user_agent=_USER_AGENT,
                headless=False,
            )
        except Exception as e:
            utils.logger.error(f"[AnalysisBrowserSession] CDP 连接失败: {e}")
            await self._playwright_cm.__aexit__(None, None, None)
            return False

        self.context_page = await self.browser_context.new_page()
        ok = await self._goto_with_waf(_INDEX_URL, what="雪球首页")
        if not ok:
            await self.close()
            return False

        self.xueqiu_client = XueqiuClient(playwright_page=self.context_page)
        return True

    async def _recreate_page_if_needed(self) -> bool:
        try:
            if self.context_page and not self.context_page.is_closed():
                return True
        except Exception:
            pass
        try:
            self.context_page = await self.browser_context.new_page()
            return True
        except Exception as e:
            utils.logger.error(f"[AnalysisBrowserSession] 重新打开页面失败: {e}")
            return False

    async def _goto_with_waf(self, url: str, what: str = "", timeout_s: int = 600) -> bool:
        utils.logger.info(f"[AnalysisBrowserSession] 访问 {what}: {url}")
        if not await self._recreate_page_if_needed():
            return False
        try:
            await self.context_page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            utils.logger.warning(f"[AnalysisBrowserSession] goto {url} 异常: {e}")

        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if not await self._recreate_page_if_needed():
                return False
            try:
                if await is_waf_challenge(self.context_page):
                    await solve_waf_slider(self.context_page, manual_timeout_s=timeout_s)
                    continue
                html = await asyncio.wait_for(self.context_page.content(), timeout=30)
            except asyncio.TimeoutError:
                utils.logger.warning("[AnalysisBrowserSession] content() 超时, 重新开页导航")
                if not await self._recreate_page_if_needed():
                    return False
                try:
                    await self.context_page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as goto_exc:
                    utils.logger.warning(f"[AnalysisBrowserSession] goto {url} 重试异常: {goto_exc}")
                await asyncio.sleep(2)
                continue
            except Exception as e:
                utils.logger.warning(f"[AnalysisBrowserSession] 页面访问异常 ({e}), 重新开页导航")
                if not await self._recreate_page_if_needed():
                    return False
                try:
                    await self.context_page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as goto_exc:
                    utils.logger.warning(f"[AnalysisBrowserSession] goto {url} 重试异常: {goto_exc}")
                await asyncio.sleep(2)
                continue
            head = html[:5000]
            if "_waf_" in head or "aliyun_waf" in head:
                await asyncio.sleep(2)
                continue
            if len(html) > 50000:
                utils.logger.info(f"[AnalysisBrowserSession] {what} 加载完成 ({len(html)} bytes)")
                return True
            await asyncio.sleep(2)
        utils.logger.error(f"[AnalysisBrowserSession] 访问 {what} 超时")
        return False

    async def get_latest_posts(self, user_id: str, page_size: int = 20) -> List[Dict]:
        """获取该用户最新一页发帖，失败返回空列表 (不抛异常中断整体任务)。"""
        if not self.xueqiu_client:
            return []
        try:
            res = await self.xueqiu_client.get_user_posts(user_id, page=1, page_size=page_size)
        except (WafChallengeError, DataFetchError, CrawlInterruptedError) as e:
            utils.logger.warning(f"[AnalysisBrowserSession] 获取用户 {user_id} 最新发帖失败: {e}")
            return []
        return res.get("statuses") or []

    async def close(self) -> None:
        try:
            if self.cdp_manager:
                await self.cdp_manager.cleanup()
                self.cdp_manager = None
        finally:
            if self._playwright_cm:
                await self._playwright_cm.__aexit__(None, None, None)
                self._playwright_cm = None
