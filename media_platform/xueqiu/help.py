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


import json
import re
from typing import Any, Dict, List, Optional

from model.m_xueqiu import XueqiuComment, XueqiuCreator, XueqiuStatus


def normalize_user_id(raw: str) -> str:
    """
    将输入归一化为纯数字用户 ID。
    支持: "1263638109" / "https://xueqiu.com/u/1263638109"
    """
    raw = (raw or "").strip()
    match = re.search(r"/u/(\d+)", raw)
    if match:
        return match.group(1)
    if raw.isdigit():
        return raw
    return raw


def _build_avatar(photo_domain: str, profile_image_url: str) -> str:
    """拼接头像完整 URL。"""
    if not profile_image_url:
        return ""
    # profile_image_url 形如 "community/xxx.jpeg,community/xxx.jpeg!180x180.png,..."
    first = profile_image_url.split(",")[0]
    if not first:
        return ""
    if first.startswith("http"):
        return first
    domain = (photo_domain or "").strip()
    if domain.startswith("//"):
        domain = "https:" + domain
    return domain.rstrip("/") + "/" + first.lstrip("/")


def extract_creator_from_user_obj(user_obj: Dict[str, Any], user_id: str) -> XueqiuCreator:
    """
    从帖子内嵌的 user 对象提取用户信息。
    """
    avatar = _build_avatar(
        user_obj.get("photo_domain", ""),
        user_obj.get("profile_image_url", ""),
    )
    return XueqiuCreator(
        user_id=str(user_obj.get("id") or user_id or ""),
        user_link=f"https://xueqiu.com/{user_obj.get('id') or user_id}",
        user_nickname=user_obj.get("screen_name", ""),
        user_avatar=avatar,
        gender=str(user_obj.get("gender", "") or ""),
        description=user_obj.get("description", "") or "",
        city=user_obj.get("city", "") or "",
        province=user_obj.get("province", "") or "",
        followers_count=int(user_obj.get("followers_count") or 0),
        friends_count=int(user_obj.get("friends_count") or 0),
        status_count=int(user_obj.get("status_count") or 0),
        stocks_count=int(user_obj.get("stocks_count") or 0),
        verified=bool(user_obj.get("verified", False)),
        verified_description=user_obj.get("verified_description", "") or "",
    )


def extract_status_list(statuses_json: List[Dict[str, Any]]) -> List[XueqiuStatus]:
    """
    从 timeline API 的 statuses 数组提取帖子列表。
    """
    result: List[XueqiuStatus] = []
    for item in statuses_json or []:
        if not item:
            continue
        status_id = str(item.get("id") or "")
        user_obj = item.get("user") or {}
        retweeted = item.get("retweeted_status")
        result.append(
            XueqiuStatus(
                status_id=status_id,
                status_type="retweet" if retweeted else "original",
                title=item.get("title", "") or "",
                description=item.get("description", "") or "",
                created_at=int(item.get("created_at") or 0),
                source=item.get("source", "") or "",
                retweet_count=int(item.get("retweet_count") or 0),
                reply_count=int(item.get("reply_count") or item.get("comment_count") or 0),
                like_count=int(item.get("like_count") or 0),
                fav_count=int(item.get("fav_count") or 0),
                view_count=int(item.get("view_count") or 0),
                status_url=f"https://xueqiu.com/{user_obj.get('id', '')}/{status_id}",
                retweet_status_id=str(retweeted.get("id") or "") if retweeted else "",
                retweeted_status=json.dumps(retweeted, ensure_ascii=False) if retweeted else "",
                user_id=str(user_obj.get("id") or ""),
                user_link=f"https://xueqiu.com/{user_obj.get('id', '')}",
                user_nickname=user_obj.get("screen_name", "") or "",
                user_avatar=_build_avatar(user_obj.get("photo_domain", ""), user_obj.get("profile_image_url", "")),
                user_followers_count=int(user_obj.get("followers_count") or 0),
            )
        )
    return result


def extract_status_from_detail(detail: Dict[str, Any]) -> Optional[XueqiuStatus]:
    """
    从 statuses/show.json 响应提取单条帖子。
    """
    if not detail or not detail.get("id"):
        return None
    return extract_status_list([detail])[0]


def extract_comments(
    comments_json: List[Dict[str, Any]],
    status_id: str = "",
    status_detail: Optional[Dict[str, Any]] = None,
) -> List[XueqiuComment]:
    """
    从 comments.json 响应的 comments 数组提取评论列表。

    Args:
        comments_json: comments 数组
        status_id: 所属帖子 ID (无该字段的评论用此默认值)
        status_detail: 帖子详情 (用于生成帖子链接/标题)

    Returns:
        评论模型列表
    """
    result: List[XueqiuComment] = []
    for item in comments_json or []:
        if not item:
            continue
        user_obj = item.get("user") or {}
        sid = str(item.get("status_id") or status_id or "")
        status_user_id = ""
        if status_detail:
            status_user_id = str((status_detail.get("user") or {}).get("id") or "")
        result.append(
            XueqiuComment(
                comment_id=str(item.get("id") or ""),
                content=item.get("text") or item.get("description") or "",
                publish_time=int(item.get("created_at") or 0),
                like_count=int(item.get("like_count") or 0),
                reply_count=int(item.get("reply_count") or 0),
                status_id=sid,
                status_title=(status_detail or {}).get("title", "") or "",
                status_url=f"https://xueqiu.com/{status_user_id}/{sid}" if sid else "",
                user_id=str(user_obj.get("id") or ""),
                user_link=f"https://xueqiu.com/{user_obj.get('id', '')}",
                user_nickname=user_obj.get("screen_name", "") or "",
                user_avatar=_build_avatar(user_obj.get("photo_domain", ""), user_obj.get("profile_image_url", "")),
            )
        )
    return result
