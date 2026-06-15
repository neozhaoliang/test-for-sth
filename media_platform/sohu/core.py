# -*- coding: utf-8 -*-
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

import asyncio
import os
import random
from asyncio import Task
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import sohu as sohu_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import SohuTvClient
from .exception import DataFetchError, VideoNotFoundError
from .field import VideoCategory, SearchSortType
from .login import SohuTvLogin


class SohuTvCrawler(AbstractCrawler):
    """搜狐视频爬虫"""

    context_page: Page
    sohu_client: SohuTvClient
    browser_context: BrowserContext

    def __init__(self) -> None:
        self.index_url = "https://tv.sohu.com"
        self.cookie_urls = [
            "https://tv.sohu.com",
            "https://so.tv.sohu.com",
            "https://www.sohu.com",
        ]
        self.cdp_manager: Optional[CDPBrowserManager] = None
        self.ip_proxy_pool = None

    async def start(self) -> None:
        """启动爬虫"""
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            self.ip_proxy_pool = await create_ip_pool(
                config.IP_PROXY_POOL_COUNT, enable_validate_ip=True
            )
            ip_proxy_info: IpInfoModel = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # 启动浏览器
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[SohuTvCrawler] 使用 CDP 模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    None,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[SohuTvCrawler] 使用标准模式启动浏览器")
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    user_agent=None,
                    headless=config.HEADLESS,
                )
                await self.browser_context.add_init_script(path="libs/stealth.min.js")

            self.context_page = await self.browser_context.new_page()
            await self.context_page.goto(self.index_url)

            self.sohu_client = await self.create_sohu_client(httpx_proxy_format)

            # 搜狐视频大部分内容不需要登录即可浏览
            if config.LOGIN_TYPE != "cookie" or not getattr(config, "SO_LOGIN_COOKIE", ""):
                utils.logger.info("[SohuTvCrawler] 搜狐视频无需登录即可浏览，跳过登录")
            else:
                await SohuTvLogin(
                    login_type=config.LOGIN_TYPE,
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=getattr(config, "SO_LOGIN_COOKIE", ""),
                ).begin()

            await self.sohu_client.update_cookies(self.browser_context)

            # 根据爬取类型执行不同任务
            if config.CRAWLER_TYPE == "search":
                await self.search()
            elif config.CRAWLER_TYPE == "detail":
                await self.get_specified_videos()
            elif config.CRAWLER_TYPE == "creator":
                await self.get_album_videos()
            else:
                # 默认执行搜索模式
                await self.search()

            utils.logger.info("[SohuTvCrawler] 搜狐视频爬取完成")

    async def create_sohu_client(self, httpx_proxy: Optional[str]) -> SohuTvClient:
        """创建搜狐视频 API 客户端"""
        cookie_str, cookie_dict = utils.convert_cookies(await self.browser_context.cookies())
        headers = {
            "User-Agent": utils.get_user_agent(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://tv.sohu.com/",
            "Origin": "https://tv.sohu.com",
            "Cookie": cookie_str,
        }
        return SohuTvClient(
            timeout=30,
            proxy=httpx_proxy,
            headers=headers,
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=self.ip_proxy_pool,
        )

    # ═══════════════════════════════════════════════════════════════
    # 搜索模式
    # ═══════════════════════════════════════════════════════════════

    async def search(self) -> None:
        """搜索视频并爬取"""
        utils.logger.info("[SohuTvCrawler.search] 开始搜索模式...")

        keywords = [k.strip() for k in config.KEYWORDS.split(",") if k.strip()]
        if not keywords:
            keywords = ["热门"]

        max_count = config.CRAWLER_MAX_NOTES_COUNT
        category = getattr(config, "SO_CATEGORY", VideoCategory.TV_DRAMA)
        if isinstance(category, str):
            for cat in VideoCategory:
                if cat.cid == category or cat.label == category:
                    category = cat
                    break

        for keyword in keywords:
            source_keyword_var.set(keyword)
            utils.logger.info(f"[SohuTvCrawler.search] 搜索关键词: {keyword}")

            page = 1
            collected = 0
            while collected < max_count:
                try:
                    if keyword == "热门":
                        result = await self.sohu_client.get_hot_list(page=page, page_size=30)
                    else:
                        result = await self.sohu_client.search_videos(
                            keyword=keyword,
                            page=page,
                            page_size=30,
                            category=category,
                        )

                    videos = self._extract_video_list(result)
                    if not videos:
                        utils.logger.info(f"[SohuTvCrawler.search] 第{page}页没有更多视频")
                        break

                    for video in videos:
                        if collected >= max_count:
                            break
                        try:
                            await self._process_video(video)
                            collected += 1
                        except Exception as e:
                            utils.logger.error(f"[SohuTvCrawler.search] 处理视频失败: {e}")
                            continue

                    utils.logger.info(f"[SohuTvCrawler.search] keyword={keyword} 已收集 {collected}/{max_count}")
                    page += 1
                    await asyncio.sleep(random.uniform(1, 3))

                except DataFetchError as e:
                    utils.logger.error(f"[SohuTvCrawler.search] 数据获取失败: {e}")
                    break
                except Exception as e:
                    utils.logger.error(f"[SohuTvCrawler.search] 未知错误: {e}")
                    break

    # ═══════════════════════════════════════════════════════════════
    # 指定视频模式
    # ═══════════════════════════════════════════════════════════════

    async def get_specified_videos(self) -> None:
        """获取指定的视频详情"""
        utils.logger.info("[SohuTvCrawler.get_specified_videos] 开始获取指定视频...")

        video_ids = getattr(config, "SO_VIDEO_ID_LIST", [])
        if not video_ids:
            utils.logger.error("[SohuTvCrawler.get_specified_videos] 未配置 SO_VIDEO_ID_LIST")
            return

        for vid in video_ids:
            try:
                detail = await self.sohu_client.get_video_detail(vid)
                await self._process_video(detail)
                await asyncio.sleep(random.uniform(1, 2))
            except VideoNotFoundError:
                utils.logger.warning(f"[SohuTvCrawler.get_specified_videos] 视频不存在: {vid}")
            except Exception as e:
                utils.logger.error(f"[SohuTvCrawler.get_specified_videos] 获取视频失败 {vid}: {e}")

    # ═══════════════════════════════════════════════════════════════
    # 专辑模式
    # ═══════════════════════════════════════════════════════════════

    async def get_album_videos(self) -> None:
        """获取指定专辑下的所有视频"""
        utils.logger.info("[SohuTvCrawler.get_album_videos] 开始获取专辑视频...")

        album_ids = getattr(config, "SO_ALBUM_ID_LIST", [])
        if not album_ids:
            utils.logger.error("[SohuTvCrawler.get_album_videos] 未配置 SO_ALBUM_ID_LIST")
            return

        for album_id in album_ids:
            try:
                page = 1
                while True:
                    result = await self.sohu_client.get_album_video_list(
                        album_id=album_id, page=page, page_size=50
                    )
                    videos = self._extract_video_list(result)
                    if not videos:
                        break

                    for video in videos:
                        try:
                            await self._process_video(video)
                        except Exception as e:
                            utils.logger.error(f"[SohuTvCrawler.get_album_videos] 处理视频失败: {e}")
                            continue

                    page += 1
                    await asyncio.sleep(random.uniform(1, 2))

            except Exception as e:
                utils.logger.error(f"[SohuTvCrawler.get_album_videos] 获取专辑失败 {album_id}: {e}")

    # ═══════════════════════════════════════════════════════════════
    # 视频处理
    # ═══════════════════════════════════════════════════════════════

    async def _process_video(self, video_data: Dict) -> None:
        """处理单个视频：获取详情、播放信息、保存"""
        vid = video_data.get("vid") or video_data.get("video_id") or video_data.get("id", "")

        # 尝试获取更详细的播放信息
        try:
            play_info = await self.sohu_client.get_video_play_info(vid)
            video_data["_play_info"] = play_info
        except Exception:
            pass

        # 保存到存储
        await sohu_store.update_sohu_video(video_data)

        # 获取评论（如果启用）
        if config.ENABLE_GET_COMMENTS and vid:
            try:
                await self._get_video_comments(vid)
            except Exception as e:
                utils.logger.warning(f"[SohuTvCrawler._process_video] 获取评论失败 vid={vid}: {e}")

    async def _get_video_comments(self, vid: str) -> None:
        """获取视频评论"""
        utils.logger.info(f"[SohuTvCrawler._get_video_comments] 获取评论 vid={vid}")
        # 搜狐视频评论 API (需要登录)
        try:
            comment_url = f"https://api.tv.sohu.com/comment/{vid}/hot.json"
            result = await self.sohu_client.request("GET", comment_url)
            comments = result.get("comments", []) if isinstance(result, dict) else []
            if comments:
                await sohu_store.batch_update_sohu_comments(vid, comments)
        except Exception as e:
            utils.logger.warning(f"[SohuTvCrawler._get_video_comments] 评论获取失败: {e}")

    # ═══════════════════════════════════════════════════════════════
    # 数据提取
    # ═══════════════════════════════════════════════════════════════

    def _extract_video_list(self, response: Dict) -> List[Dict]:
        """从 API 响应中提取视频列表"""
        if not isinstance(response, dict):
            return []

        # 常见返回结构: {"data": {"videos": [...]}} 或 {"videos": [...]}
        data = response.get("data", response)
        if isinstance(data, dict):
            for key in ("videos", "video_list", "list", "items", "results"):
                if key in data:
                    items = data[key]
                    if isinstance(items, list):
                        return items
        if isinstance(data, list):
            return data

        return []

    async def close(self):
        """清理资源"""
        if self.cdp_manager:
            await self.cdp_manager.close()
        utils.logger.info("[SohuTvCrawler] 爬虫已关闭")
