# -*- coding: utf-8 -*-
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

from enum import Enum
from typing import NamedTuple


class VideoCategory(Enum):
    """搜狐视频分类 (cid 参数)"""
    TV_DRAMA = ("2", "电视剧")
    MOVIE = ("1", "电影")
    VARIETY = ("7", "综艺")
    ANIME = ("4", "动漫")
    DOCUMENTARY = ("5", "纪录片")
    KIDS = ("10", "少儿")
    MUSIC = ("9", "音乐")
    NEWS = ("3", "新闻")
    SPORTS = ("6", "体育")
    SHORT_VIDEO = ("8", "短视频")
    EDUCATION = ("11", "教育")

    def __init__(self, cid: str, label: str):
        self.cid = cid
        self.label = label


class SearchSortType(Enum):
    """搜索排序类型"""
    DEFAULT = "0"      # 综合排序
    LATEST = "1"       # 最新
    HOTTEST = "2"      # 最热


class VideoType(Enum):
    """视频类型"""
    SHORT = "short"       # 短视频
    LONG = "long"         # 长视频 (剧集/电影)
    ALBUM = "album"       # 专辑


class SohuVideoInfo(NamedTuple):
    """搜狐视频信息"""
    vid: str              # 视频ID
    video_name: str       # 视频名称
    album_id: str         # 专辑ID
    album_name: str       # 专辑名称
    video_url: str        # 视频页面URL


class SohuAlbumInfo(NamedTuple):
    """搜狐专辑信息"""
    album_id: str         # 专辑ID
    album_name: str       # 专辑名称
    category: str         # 分类
    total_episodes: int   # 总集数
