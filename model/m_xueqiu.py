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


from typing import Optional

from pydantic import BaseModel, Field


class XueqiuStatus(BaseModel):
    """
    雪球帖子 (status) — 用户的发帖，含原创与转发
    """
    status_id: str = Field(default="", description="帖子 ID")
    status_type: str = Field(default="", description="帖子类型 (original=原创 | retweet=转发)")
    title: str = Field(default="", description="标题 (长文时非空)")
    description: str = Field(default="", description="帖子正文")
    created_at: int = Field(default=0, description="发布时间戳 (ms)")
    source: str = Field(default="", description="发布来源")
    retweet_count: int = Field(default=0, description="转发数")
    reply_count: int = Field(default=0, description="评论数")
    like_count: int = Field(default=0, description="点赞数")
    fav_count: int = Field(default=0, description="收藏数")
    view_count: int = Field(default=0, description="阅读数")
    status_url: str = Field(default="", description="帖子链接")
    retweet_status_id: str = Field(default="", description="转发的原帖 ID (转发类型时有值)")
    retweeted_status: str = Field(default="", description="转发的原帖内容 (JSON 字符串)")
    source_keyword: str = Field(default="", description="来源关键字")

    user_id: str = Field(default="", description="用户 ID")
    user_link: str = Field(default="", description="用户主页链接")
    user_nickname: str = Field(default="", description="用户昵称")
    user_avatar: str = Field(default="", description="用户头像 URL")
    user_followers_count: int = Field(default=0, description="用户粉丝数 (0=未知, 用于用户发现)")


class XueqiuComment(BaseModel):
    """
    雪球评论 (comment) — 用户在某帖子下的回复
    """
    comment_id: str = Field(default="", description="评论 ID")
    content: str = Field(default="", description="评论内容")
    publish_time: int = Field(default=0, description="发布时间戳 (ms)")
    like_count: int = Field(default=0, description="点赞数")
    reply_count: int = Field(default=0, description="该评论的回复数")
    status_id: str = Field(default="", description="所属帖子 ID")
    status_title: str = Field(default="", description="所属帖子标题/摘要")
    status_url: str = Field(default="", description="所属帖子链接")

    user_id: str = Field(default="", description="评论者用户 ID")
    user_link: str = Field(default="", description="评论者主页链接")
    user_nickname: str = Field(default="", description="评论者昵称")
    user_avatar: str = Field(default="", description="评论者头像 URL")


class XueqiuCreator(BaseModel):
    """
    雪球用户 (creator) 信息
    """
    user_id: str = Field(default="", description="用户 ID")
    user_link: str = Field(default="", description="用户主页链接")
    user_nickname: str = Field(default="", description="用户昵称")
    user_avatar: str = Field(default="", description="用户头像 URL")
    gender: str = Field(default="", description="性别")
    description: str = Field(default="", description="个人简介")
    city: str = Field(default="", description="所在城市")
    province: str = Field(default="", description="所在省份")
    followers_count: int = Field(default=0, description="粉丝数")
    friends_count: int = Field(default=0, description="关注数")
    status_count: int = Field(default=0, description="发帖数")
    stocks_count: int = Field(default=0, description="关注股票数")
    verified: bool = Field(default=False, description="是否认证")
    verified_description: str = Field(default="", description="认证信息")
