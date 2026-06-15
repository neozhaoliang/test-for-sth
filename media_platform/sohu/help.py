# -*- coding: utf-8 -*-
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

import re
from typing import Optional

from .field import SohuVideoInfo, SohuAlbumInfo


def parse_video_info_from_url(url: str) -> SohuVideoInfo:
    """
    从搜狐视频 URL 解析视频信息
    支持格式:
      1. 视频页: https://tv.sohu.com/v/dXMvMzM1MTY3MzA5LzQ3NDg3NjY4OC5zaHRtbA==.html
      2. 专辑视频: https://tv.sohu.com/v/MjAyNTA2MDkvbjYwMTExMDMyMi5zaHRtbA==.html
      3. 短链接: https://my.tv.sohu.com/us/123456/7890.shtml
      4. 纯 vid

    Args:
        url: 搜狐视频链接或ID
    Returns:
        SohuVideoInfo
    """
    if not url.startswith("http"):
        return SohuVideoInfo(vid=url, video_name="", album_id="", album_name="", video_url=f"https://tv.sohu.com/v/{url}")

    # 提取 /v/xxx.html 或 /v/xxx.shtml 中的 vid
    m = re.search(r'/v/([^/?#]+)\.(?:s?html)', url)
    if m:
        vid = m.group(1)
        return SohuVideoInfo(vid=vid, video_name="", album_id="", album_name="", video_url=url)

    # 提取 /us/xxx/yyy.shtml
    m = re.search(r'/us/(\d+)/(\d+)', url)
    if m:
        vid = f"{m.group(1)}_{m.group(2)}"
        return SohuVideoInfo(vid=vid, video_name="", album_id="", album_name="", video_url=url)

    raise ValueError(f"无法从URL解析视频ID: {url}")


def parse_album_info_from_url(url: str) -> SohuAlbumInfo:
    """
    从搜狐专辑 URL 解析专辑信息
    支持格式:
      1. https://tv.sohu.com/album/12345.html
      2. https://tv.sohu.com/s2016/dsj2016/

    Args:
        url: 搜狐专辑链接
    Returns:
        SohuAlbumInfo
    """
    # /album/xxx.html
    m = re.search(r'/album/(\d+)', url)
    if m:
        return SohuAlbumInfo(album_id=m.group(1), album_name="", category="", total_episodes=0)

    raise ValueError(f"无法从URL解析专辑ID: {url}")


VIDEO_URL_TEMPLATE = "https://tv.sohu.com/v/{vid}.html"
ALBUM_URL_TEMPLATE = "https://tv.sohu.com/album/{album_id}.html"
SEARCH_URL = "https://so.tv.sohu.com/list_pc?pid=1&o=2&ver=5&pagenum=1&pagesize=30"

# 搜狐视频 API key (公开的 web 端 key)
SO_API_KEY = "9857461e0fe5d6782dd2e0e438c85801"
LIST_API = "https://so.tv.sohu.com/list_pc"
PLAY_API = "https://api.tv.sohu.com/video/playinfo/{vid}.json"
DETAIL_API = "https://api.tv.sohu.com/video/detail/{vid}.json"
ALBUM_API = "https://api.tv.sohu.com/album/{album_id}.json"
SEARCH_SUGGEST_API = "https://s.sohu.com/search"
