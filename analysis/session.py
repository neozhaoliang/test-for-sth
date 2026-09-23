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

CDP 连接与 WAF 等待逻辑与 media_platform/xueqiu/core.py 保持一致，但不依赖
该类的其它状态 (断点续爬/发现等)。所有雪球数据都通过真实页面导航 + DOM 读取
获取 (不发任何 API/XHR 请求)，避免被 WAF 识别为爬虫行为。
"""

import asyncio
import os
import time
from typing import Dict, List, Optional

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

import config
from media_platform.xueqiu import dom as xueqiu_dom
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

        # 反检测: 隐藏 webdriver/CDP 痕迹 (阿里云滑块会拒绝自动化控制的浏览器)
        if os.path.exists("libs/stealth.min.js"):
            await self.browser_context.add_init_script(path="libs/stealth.min.js")

        self.context_page = await self.browser_context.new_page()
        ok = await self._goto_with_waf(_INDEX_URL, what="雪球首页")
        if not ok:
            await self.close()
            return False

        # 未登录时停在首页，给用户时间在弹出的浏览器里登录雪球 (超时后降级继续)
        await self._wait_for_xueqiu_login()

        return True

    async def _is_xueqiu_logged_in(self) -> bool:
        """
        真实登录态检查: 首页不再出现"立即登录"入口才算已登录。
        仅凭 cookie 存在会误判——登录失败/过期会留下残缺 cookie (xq_a_token
        存在但账号实际未登录, 表现为接口第 1 页可用、第 2 页报"请登录")。
        """
        try:
            cookies = await self.browser_context.cookies("https://xueqiu.com/")
            if not any(c.get("name") == "xq_a_token" and c.get("value") for c in cookies):
                return False
            logged_in = await self.context_page.evaluate(
                """() => {
                    const text = document.body ? document.body.innerText.slice(0, 400) : '';
                    return !/立即登录/.test(text);
                }"""
            )
            return bool(logged_in)
        except Exception:
            return False

    async def _wait_for_xueqiu_login(self) -> None:
        """未登录时停在雪球首页等待用户登录，超时后降级为未登录状态继续。"""
        if not getattr(config, "CDP_WAIT_FOR_LOGIN", True):
            return
        timeout = int(getattr(config, "CDP_LOGIN_WAIT_SECONDS", 120) or 0)
        if timeout <= 0:
            return
        if await self._is_xueqiu_logged_in():
            utils.logger.info("[AnalysisBrowserSession] 雪球已登录")
            return
        utils.logger.info(
            f"[AnalysisBrowserSession] 雪球未登录，等待登录 (最多 {timeout}s): "
            "请在弹出的浏览器里完成雪球登录，登录成功后自动继续"
        )
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            await asyncio.sleep(3)
            if await self._is_xueqiu_logged_in():
                utils.logger.info("[AnalysisBrowserSession] 雪球登录成功，继续分析")
                return
        utils.logger.warning(
            "[AnalysisBrowserSession] 等待登录超时，以未登录状态继续 (部分页面/接口可能受限)"
        )

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
        """
        获取该用户最新发帖，失败返回空列表 (不抛异常中断整体任务)。

        不走 user_timeline.json 接口 (直接调用会被 WAF 识别为爬虫行为):
        改为真实页面导航到用户主页 + 拟人节奏滚动触发 SPA 懒加载，只读渲染后的
        DOM。与真人浏览行为一致，加载数据的 XHR 由页面自身发起。
        """
        ok = await self._goto_with_waf(
            f"{_INDEX_URL}/u/{user_id}", what=f"用户 {user_id} 主页"
        )
        if not ok:
            return []

        # 等 SPA 渲染出时间线卡片，再少量滚动触发懒加载 (拟人节奏，不追求翻完历史)
        await xueqiu_dom.wait_for_items(self.context_page, "article.timeline__item", timeout_s=20)
        await xueqiu_dom.scroll_until_stable(
            self.context_page, "article.timeline__item", max_rounds=8
        )

        posts = await self._extract_timeline_from_dom()
        if posts:
            utils.logger.info(
                f"[AnalysisBrowserSession] 用户 {user_id} DOM 提取 {len(posts)} 条发帖"
            )
        return posts[:page_size]

    async def _extract_timeline_from_dom(self) -> List[Dict]:
        """从当前页面 DOM 提取时间线帖子 (id/status_type/description/created_at)。"""
        items = await xueqiu_dom.extract_timeline_items(self.context_page)
        if not items:
            utils.logger.warning("[AnalysisBrowserSession] DOM 时间线提取为空")
        return items

    async def close(self) -> None:
        try:
            if self.cdp_manager:
                await self.cdp_manager.cleanup()
                self.cdp_manager = None
        finally:
            if self._playwright_cm:
                await self._playwright_cm.__aexit__(None, None, None)
                self._playwright_cm = None
