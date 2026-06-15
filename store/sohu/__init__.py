# -*- coding: utf-8 -*-
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

from typing import Dict, List

import config
from var import source_keyword_var

from ._store_impl import *

from tools import utils


class SohuStoreFactory:
    STORES = {
        "csv": SohuCsvStoreImplement,
        "db": SohuDbStoreImplement,
        "postgres": SohuDbStoreImplement,
        "json": SohuJsonStoreImplement,
        "jsonl": SohuJsonlStoreImplement,
        "sqlite": SohuSqliteStoreImplement,
        "mongodb": SohuMongoStoreImplement,
        "excel": SohuExcelStoreImplement,
    }

    @staticmethod
    def create_store() -> AbstractStore:
        store_class = SohuStoreFactory.STORES.get(config.SAVE_DATA_OPTION)
        if not store_class:
            raise ValueError(
                "[SohuStoreFactory.create_store] Invalid save option "
                "only supported csv, db, json, sqlite, mongodb, excel, postgres"
            )
        return store_class()


async def update_sohu_video(video_data: Dict):
    """保存搜狐视频数据"""
    vid = (
        video_data.get("vid")
        or video_data.get("video_id")
        or video_data.get("id", "")
    )
    album_info = video_data.get("album", {}) or {}
    play_info = video_data.get("_play_info", {}) or {}

    save_item = {
        "video_id": str(vid),
        "video_name": video_data.get("video_name") or video_data.get("title") or video_data.get("name", ""),
        "album_id": str(video_data.get("album_id") or album_info.get("album_id", "")),
        "album_name": album_info.get("album_name") or video_data.get("album_name", ""),
        "category": str(video_data.get("cid") or video_data.get("category", "")),
        "category_name": video_data.get("category_name", ""),
        "episode": str(video_data.get("video_order") or video_data.get("episode", "")),
        "description": video_data.get("description") or video_data.get("desc", ""),
        "cover_url": (
            video_data.get("cover_url")
            or video_data.get("hor_pic_url")
            or video_data.get("ver_pic_url")
            or video_data.get("pic_url", "")
        ),
        "director": video_data.get("director", ""),
        "actors": _join_list(video_data.get("actor") or video_data.get("actors", [])),
        "area": video_data.get("area", ""),
        "year": str(video_data.get("year", "")),
        "duration": str(video_data.get("duration") or video_data.get("total_duration", "")),
        "play_count": str(video_data.get("play_count") or video_data.get("vv", "")),
        "score": str(video_data.get("score", "")),
        "video_url": f"https://tv.sohu.com/v/{vid}.html" if vid else "",
        "source_keyword": source_keyword_var.get(),
        "last_modify_ts": utils.get_current_timestamp(),
    }

    utils.logger.info(
        f"[store.sohu.update_sohu_video] vid={vid}, "
        f"title={save_item.get('video_name')[:50]}"
    )
    await SohuStoreFactory.create_store().store_content(content_item=save_item)


async def batch_update_sohu_comments(video_id: str, comments: List[Dict]):
    """批量保存搜狐视频评论"""
    if not comments:
        return
    for comment in comments:
        await update_sohu_comment(video_id, comment)


async def update_sohu_comment(video_id: str, comment: Dict):
    """保存单条评论"""
    user_info = comment.get("user", {}) or {}
    save_item = {
        "comment_id": str(comment.get("comment_id") or comment.get("id", "")),
        "video_id": video_id,
        "content": comment.get("content") or comment.get("text", ""),
        "user_id": str(user_info.get("uid") or user_info.get("user_id", "")),
        "nickname": user_info.get("nickname") or comment.get("passport_name", ""),
        "avatar": user_info.get("avatar") or user_info.get("head_url", ""),
        "like_count": str(comment.get("like_count") or comment.get("up", 0)),
        "reply_count": str(comment.get("reply_count") or comment.get("comment_num", 0)),
        "create_time": comment.get("create_time") or comment.get("ctime", ""),
        "last_modify_ts": utils.get_current_timestamp(),
    }
    utils.logger.info(
        f"[store.sohu.update_sohu_comment] comment_id={save_item['comment_id']}"
    )
    await SohuStoreFactory.create_store().store_comment(comment_item=save_item)


def _join_list(value):
    """将列表转为逗号分隔的字符串"""
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value) if value else ""
