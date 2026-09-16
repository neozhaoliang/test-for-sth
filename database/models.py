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

from sqlalchemy import create_engine, Column, Integer, Text, String, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class BilibiliVideo(Base):
    __tablename__ = 'bilibili_video'
    id = Column(Integer, primary_key=True, comment='主键ID')
    video_id = Column(BigInteger, nullable=False, index=True, unique=True, comment='视频ID')
    video_url = Column(Text, nullable=False, comment='视频URL')
    user_id = Column(BigInteger, index=True, comment='用户ID')
    nickname = Column(Text, comment='用户昵称')
    avatar = Column(Text, comment='用户头像')
    liked_count = Column(Integer, comment='点赞数')
    add_ts = Column(BigInteger, comment='添加时间戳')
    last_modify_ts = Column(BigInteger, comment='最后修改时间戳')
    video_type = Column(Text, comment='视频类型')
    title = Column(Text, comment='视频标题')
    desc = Column(Text, comment='视频描述')
    create_time = Column(BigInteger, index=True, comment='创建时间戳')
    disliked_count = Column(Text, comment='点踩数')
    video_play_count = Column(Text, comment='播放数')
    video_favorite_count = Column(Text, comment='收藏数')
    video_share_count = Column(Text, comment='分享数')
    video_coin_count = Column(Text, comment='硬币数')
    video_danmaku = Column(Text, comment='弹幕数')
    video_comment = Column(Text, comment='评论数')
    video_cover_url = Column(Text, comment='视频封面URL')
    source_keyword = Column(Text, default='', comment='来源关键词')

class BilibiliVideoComment(Base):
    __tablename__ = 'bilibili_video_comment'
    id = Column(Integer, primary_key=True, comment='主键ID')
    user_id = Column(String(255), comment='用户ID')
    nickname = Column(Text, comment='用户昵称')
    sex = Column(Text, comment='性别')
    sign = Column(Text, comment='签名')
    avatar = Column(Text, comment='头像')
    add_ts = Column(BigInteger, comment='添加时间戳')
    last_modify_ts = Column(BigInteger, comment='最后修改时间戳')
    comment_id = Column(BigInteger, index=True, comment='评论ID')
    video_id = Column(BigInteger, index=True, comment='视频ID')
    content = Column(Text, comment='评论内容')
    create_time = Column(BigInteger, comment='创建时间戳')
    sub_comment_count = Column(Text, comment='子评论数')
    parent_comment_id = Column(String(255), comment='父评论ID')
    like_count = Column(Text, default='0', comment='点赞数')

class BilibiliUpInfo(Base):
    __tablename__ = 'bilibili_up_info'
    id = Column(Integer, primary_key=True, comment='主键ID')
    user_id = Column(BigInteger, index=True, comment='用户ID')
    nickname = Column(Text, comment='用户昵称')
    sex = Column(Text, comment='性别')
    sign = Column(Text, comment='签名')
    avatar = Column(Text, comment='头像')
    add_ts = Column(BigInteger, comment='添加时间戳')
    last_modify_ts = Column(BigInteger, comment='最后修改时间戳')
    total_fans = Column(Integer, comment='总粉丝数')
    total_liked = Column(Integer, comment='总获赞数')
    user_rank = Column(Integer, comment='用户等级')
    is_official = Column(Integer, comment='是否官方认证')

class BilibiliContactInfo(Base):
    __tablename__ = 'bilibili_contact_info'
    id = Column(Integer, primary_key=True, comment='主键ID')
    up_id = Column(BigInteger, index=True, comment='UP主ID')
    fan_id = Column(BigInteger, index=True, comment='粉丝ID')
    up_name = Column(Text, comment='UP主名称')
    fan_name = Column(Text, comment='粉丝名称')
    up_sign = Column(Text, comment='UP主签名')
    fan_sign = Column(Text, comment='粉丝签名')
    up_avatar = Column(Text, comment='UP主头像')
    fan_avatar = Column(Text, comment='粉丝头像')
    add_ts = Column(BigInteger, comment='添加时间戳')
    last_modify_ts = Column(BigInteger, comment='最后修改时间戳')

class BilibiliUpDynamic(Base):
    __tablename__ = 'bilibili_up_dynamic'
    id = Column(Integer, primary_key=True, comment='主键ID')
    dynamic_id = Column(BigInteger, index=True, comment='动态ID')
    user_id = Column(String(255), comment='用户ID')
    user_name = Column(Text, comment='用户名称')
    text = Column(Text, comment='动态内容')
    type = Column(Text, comment='动态类型')
    pub_ts = Column(BigInteger, comment='发布时间戳')
    total_comments = Column(Integer, comment='总评论数')
    total_forwards = Column(Integer, comment='总转发数')
    total_liked = Column(Integer, comment='总点赞数')
    add_ts = Column(BigInteger, comment='添加时间戳')
    last_modify_ts = Column(BigInteger, comment='最后修改时间戳')


# ═══════════════════════════════════════════════════════════════
# 雪球 (xueqiu.com)
# ═══════════════════════════════════════════════════════════════

class XueqiuStatus(Base):
    __tablename__ = "xueqiu_status"
    id = Column(Integer, primary_key=True, comment="主键ID")
    status_id = Column(String(64), index=True, comment="帖子ID")
    status_type = Column(Text, comment="帖子类型 (original | retweet)")
    title = Column(Text, comment="标题")
    description = Column(Text, comment="帖子正文")
    created_at = Column(BigInteger, comment="发布时间戳 (ms)")
    source = Column(Text, comment="发布来源")
    retweet_count = Column(Integer, default=0, comment="转发数")
    reply_count = Column(Integer, default=0, comment="评论数")
    like_count = Column(Integer, default=0, comment="点赞数")
    fav_count = Column(Integer, default=0, comment="收藏数")
    view_count = Column(Integer, default=0, comment="阅读数")
    status_url = Column(Text, comment="帖子链接")
    retweet_status_id = Column(String(64), comment="转发的原帖ID")
    retweeted_status = Column(Text, comment="转发的原帖内容 (JSON)")
    source_keyword = Column(Text, default="", comment="来源关键词")
    user_id = Column(String(64), comment="用户ID")
    user_link = Column(Text, comment="用户主页链接")
    user_nickname = Column(Text, comment="用户昵称")
    user_avatar = Column(Text, comment="用户头像URL")
    add_ts = Column(BigInteger, comment="添加时间戳")
    last_modify_ts = Column(BigInteger, comment="最后修改时间戳")


class XueqiuComment(Base):
    __tablename__ = "xueqiu_comment"
    id = Column(Integer, primary_key=True, comment="主键ID")
    comment_id = Column(String(64), index=True, comment="评论ID")
    content = Column(Text, comment="评论内容")
    publish_time = Column(BigInteger, comment="发布时间戳 (ms)")
    like_count = Column(Integer, default=0, comment="点赞数")
    reply_count = Column(Integer, default=0, comment="该评论的回复数")
    status_id = Column(String(64), comment="所属帖子ID")
    status_title = Column(Text, comment="所属帖子标题/摘要")
    status_url = Column(Text, comment="所属帖子链接")
    user_id = Column(String(64), comment="评论者用户ID")
    user_link = Column(Text, comment="评论者主页链接")
    user_nickname = Column(Text, comment="评论者昵称")
    user_avatar = Column(Text, comment="评论者头像URL")
    add_ts = Column(BigInteger, comment="添加时间戳")
    last_modify_ts = Column(BigInteger, comment="最后修改时间戳")


class XueqiuCreator(Base):
    __tablename__ = "xueqiu_creator"
    id = Column(Integer, primary_key=True, comment="主键ID")
    user_id = Column(String(64), unique=True, index=True, comment="用户ID")
    user_link = Column(Text, comment="用户主页链接")
    user_nickname = Column(Text, comment="用户昵称")
    user_avatar = Column(Text, comment="用户头像URL")
    gender = Column(Text, comment="性别")
    description = Column(Text, comment="个人简介")
    city = Column(Text, comment="所在城市")
    province = Column(Text, comment="所在省份")
    followers_count = Column(Integer, default=0, comment="粉丝数")
    friends_count = Column(Integer, default=0, comment="关注数")
    status_count = Column(Integer, default=0, comment="发帖数")
    stocks_count = Column(Integer, default=0, comment="关注股票数")
    verified = Column(Integer, default=0, comment="是否认证")
    verified_description = Column(Text, comment="认证信息")
    add_ts = Column(BigInteger, comment="添加时间戳")
    last_modify_ts = Column(BigInteger, comment="最后修改时间戳")
