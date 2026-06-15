# -*- coding: utf-8 -*-
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
from urllib.parse import urlencode

import httpx
from playwright.async_api import Page

from base.base_crawler import AbstractApiClient
from proxy.proxy_mixin import ProxyRefreshMixin
from tools import utils
from tools.httpx_util import make_async_client

if TYPE_CHECKING:
    from proxy.proxy_ip_pool import ProxyIpPool

from .exception import DataFetchError, IPBlockError, VideoNotFoundError
from .field import VideoCategory, SearchSortType
from .help import SO_API_KEY, LIST_API, PLAY_API, DETAIL_API


class SohuTvClient(AbstractApiClient, ProxyRefreshMixin):
    """搜狐视频 API 客户端"""

    def __init__(
        self,
        timeout: int = 30,
        proxy: Optional[str] = None,
        *,
        headers: Dict[str, str],
        playwright_page: Optional[Page],
        cookie_dict: Dict[str, str],
        proxy_ip_pool: Optional["ProxyIpPool"] = None,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        self._host = "https://tv.sohu.com"
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict
        self.init_proxy_pool(proxy_ip_pool)

    async def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> Any:
        """发送 HTTP 请求，带重试和代理刷新"""
        await self._refresh_proxy_if_expired()

        request_headers = {**self.headers, **(kwargs.pop("headers", {}) or {})}
        request_params = kwargs.pop("params", None)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with make_async_client(
                    proxy=self.proxy,
                    timeout=self.timeout,
                    headers=request_headers,
                    http2=True,
                ) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        params=request_params,
                        **kwargs,
                    )
                    if response.status_code in (403, 429):
                        raise IPBlockError(f"IP blocked, status={response.status_code}")
                    if response.status_code == 404:
                        raise VideoNotFoundError(f"Resource not found: {url}")
                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "")
                    if "json" in content_type:
                        return response.json()
                    return response.text

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                utils.logger.warning(f"[SohuTvClient.request] 请求失败 (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise DataFetchError(f"请求失败 after {max_retries} attempts: {e}")

    async def pong(self) -> bool:
        """检查登录状态"""
        try:
            await self.request("GET", self._host)
            return True
        except Exception:
            return False

    async def update_cookies(self, browser_context):
        """从浏览器提取 cookie 并更新请求头"""
        cookie_str, cookie_dict = utils.convert_cookies(await browser_context.cookies())
        self.cookie_dict = cookie_dict
        self.headers["Cookie"] = cookie_str

    # ═══════════════════════════════════════════════════════════════
    # 视频列表 / 搜索 API
    # ═══════════════════════════════════════════════════════════════

    async def get_video_list(
        self,
        category: Union[str, VideoCategory] = VideoCategory.TV_DRAMA,
        page: int = 1,
        page_size: int = 30,
        sort_type: Union[str, SearchSortType] = SearchSortType.LATEST,
        year: Optional[str] = None,
        area: Optional[str] = None,
    ) -> Dict:
        """
        获取搜狐视频列表
        API: https://so.tv.sohu.com/list_pc

        Args:
            category: 视频分类 (cid 参数)
            page: 页码
            page_size: 每页数量
            sort_type: 排序方式
            year: 年份筛选 (如 "2024")
            area: 地区筛选 (如 "内地")
        """
        cid = category.cid if isinstance(category, VideoCategory) else category
        sort = sort_type.value if isinstance(sort_type, SearchSortType) else sort_type

        params: Dict[str, str] = {
            "pid": "1",
            "api_key": SO_API_KEY,
            "ver": "5",
            "pagenum": str(page),
            "pagesize": str(page_size),
            "cid": cid,
            "o": sort,
            "ratio": "3",
        }
        if year:
            params["year"] = year
        if area:
            params["area"] = area

        utils.logger.info(f"[SohuTvClient.get_video_list] 分类={cid}, page={page}, size={page_size}")
        return await self.request("GET", LIST_API, params=params)

    async def search_videos(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 30,
        category: Optional[Union[str, VideoCategory]] = None,
    ) -> Dict:
        """
        搜索搜狐视频
        """
        params: Dict[str, str] = {
            "pid": "1",
            "api_key": SO_API_KEY,
            "ver": "5",
            "pagenum": str(page),
            "pagesize": str(page_size),
            "o": "0",
            "ratio": "3",
            "keyword": keyword,
        }
        if category:
            cid = category.cid if isinstance(category, VideoCategory) else category
            params["cid"] = cid

        utils.logger.info(f"[SohuTvClient.search_videos] keyword={keyword}, page={page}")
        return await self.request("GET", LIST_API, params=params)

    # ═══════════════════════════════════════════════════════════════
    # 视频详情 API
    # ═══════════════════════════════════════════════════════════════

    async def get_video_detail(self, vid: str) -> Dict:
        """获取视频详情"""
        url = DETAIL_API.format(vid=vid)
        utils.logger.info(f"[SohuTvClient.get_video_detail] vid={vid}")
        return await self.request("GET", url)

    async def get_video_play_info(self, vid: str) -> Dict:
        """
        获取视频播放信息 (包含播放地址)
        API: https://api.tv.sohu.com/video/playinfo/{vid}.json
        """
        url = PLAY_API.format(vid=vid)
        utils.logger.info(f"[SohuTvClient.get_video_play_info] vid={vid}")
        return await self.request("GET", url)

    # ═══════════════════════════════════════════════════════════════
    # 专辑 API
    # ═══════════════════════════════════════════════════════════════

    async def get_album_detail(self, album_id: str) -> Dict:
        """获取专辑详情 (包含剧集列表)"""
        url = f"https://api.tv.sohu.com/album/{album_id}.json"
        utils.logger.info(f"[SohuTvClient.get_album_detail] album_id={album_id}")
        return await self.request("GET", url)

    async def get_album_video_list(
        self,
        album_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict:
        """获取专辑下的视频列表"""
        params = {
            "pid": "1",
            "api_key": SO_API_KEY,
            "ver": "5",
            "pagenum": str(page),
            "pagesize": str(page_size),
            "album_id": album_id,
            "ratio": "3",
        }
        utils.logger.info(f"[SohuTvClient.get_album_video_list] album_id={album_id}, page={page}")
        return await self.request("GET", LIST_API, params=params)

    # ═══════════════════════════════════════════════════════════════
    # 热门推荐 API
    # ═══════════════════════════════════════════════════════════════

    async def get_hot_list(self, page: int = 1, page_size: int = 30) -> Dict:
        """获取热门视频列表"""
        params = {
            "pid": "1",
            "api_key": SO_API_KEY,
            "ver": "5",
            "pagenum": str(page),
            "pagesize": str(page_size),
            "o": "2",  # 最热
            "ratio": "3",
        }
        utils.logger.info(f"[SohuTvClient.get_hot_list] page={page}")
        return await self.request("GET", LIST_API, params=params)
