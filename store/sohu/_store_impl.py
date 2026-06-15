# -*- coding: utf-8 -*-
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

import asyncio
import json
import os
from typing import Dict, List

from base.base_crawler import AbstractStore
from database.mongodb_store_base import MongoDBStoreBase
from tools import utils
from tools.async_file_writer import AsyncFileWriter


# ═════════════════════════════════════════════════════════════════
# CSV 存储
# ═════════════════════════════════════════════════════════════════

class SohuCsvStoreImplement(AbstractStore):
    _csv_writer: AsyncFileWriter = None
    _csv_comments_writer: AsyncFileWriter = None
    _file_lock = asyncio.Lock()
    _writer_initialized = False

    def __init__(self):
        self._ensure_writer_initialized()

    def _ensure_writer_initialized(self):
        if self._writer_initialized:
            return
        self.__class__._csv_writer = AsyncFileWriter("csv", "data/sohu/videos")
        self.__class__._csv_comments_writer = AsyncFileWriter("csv", "data/sohu/comments")
        self.__class__._writer_initialized = True

    async def store_content(self, content_item: Dict):
        await self._csv_writer.write_dict(content_item)

    async def store_comment(self, comment_item: Dict):
        await self._csv_comments_writer.write_dict(comment_item)

    async def store_creator(self, creator: Dict):
        pass


# ═════════════════════════════════════════════════════════════════
# JSON 存储
# ═════════════════════════════════════════════════════════════════

class SohuJsonStoreImplement(AbstractStore):
    _json_writer: AsyncFileWriter = None
    _json_comments_writer: AsyncFileWriter = None
    _writer_initialized = False

    def __init__(self):
        if not self._writer_initialized:
            self.__class__._json_writer = AsyncFileWriter("json", "data/sohu/videos")
            self.__class__._json_comments_writer = AsyncFileWriter("json", "data/sohu/comments")
            self.__class__._writer_initialized = True

    async def store_content(self, content_item: Dict):
        await self._json_writer.write_dict(content_item)

    async def store_comment(self, comment_item: Dict):
        await self._json_comments_writer.write_dict(comment_item)

    async def store_creator(self, creator: Dict):
        pass


# ═════════════════════════════════════════════════════════════════
# JSONL 存储
# ═════════════════════════════════════════════════════════════════

class SohuJsonlStoreImplement(AbstractStore):
    _jsonl_writer: AsyncFileWriter = None
    _writer_initialized = False

    def __init__(self):
        if not self._writer_initialized:
            self.__class__._jsonl_writer = AsyncFileWriter("jsonl", "data/sohu")
            self.__class__._writer_initialized = True

    async def store_content(self, content_item: Dict):
        await self._jsonl_writer.write_dict(content_item)

    async def store_comment(self, comment_item: Dict):
        await self._jsonl_writer.write_dict(comment_item)

    async def store_creator(self, creator: Dict):
        pass


# ═════════════════════════════════════════════════════════════════
# DB (MySQL) 存储
# ═════════════════════════════════════════════════════════════════

class SohuDbStoreImplement(AbstractStore):
    async def store_content(self, content_item: Dict):
        from database.db_session import get_session
        from database.models import SohuVideo
        async with get_session() as session:
            async with session.begin():
                existing = await session.get(SohuVideo, content_item.get("video_id"))
                if existing:
                    for k, v in content_item.items():
                        if hasattr(existing, k) and v:
                            setattr(existing, k, v)
                else:
                    session.add(SohuVideo(**content_item))

    async def store_comment(self, comment_item: Dict):
        from database.db_session import get_session
        from database.models import SohuVideoComment
        async with get_session() as session:
            async with session.begin():
                existing = await session.get(SohuVideoComment, comment_item.get("comment_id"))
                if existing:
                    for k, v in comment_item.items():
                        if hasattr(existing, k) and v:
                            setattr(existing, k, v)
                else:
                    session.add(SohuVideoComment(**comment_item))

    async def store_creator(self, creator: Dict):
        pass


# ═════════════════════════════════════════════════════════════════
# SQLite 存储
# ═════════════════════════════════════════════════════════════════

class SohuSqliteStoreImplement(AbstractStore):
    async def store_content(self, content_item: Dict):
        from database.db_session import get_session
        from database.models import SohuVideo
        async with get_session() as session:
            async with session.begin():
                session.add(SohuVideo(**content_item))
                await session.commit()

    async def store_comment(self, comment_item: Dict):
        from database.db_session import get_session
        from database.models import SohuVideoComment
        async with get_session() as session:
            async with session.begin():
                session.add(SohuVideoComment(**comment_item))
                await session.commit()

    async def store_creator(self, creator: Dict):
        pass


# ═════════════════════════════════════════════════════════════════
# MongoDB 存储
# ═════════════════════════════════════════════════════════════════

class SohuMongoStoreImplement(AbstractStore):
    async def store_content(self, content_item: Dict):
        await MongoDBStoreBase().save_or_update(
            collection_name="sohu_videos",
            document=content_item,
            query_key="video_id",
        )

    async def store_comment(self, comment_item: Dict):
        await MongoDBStoreBase().save_or_update(
            collection_name="sohu_video_comments",
            document=comment_item,
            query_key="comment_id",
        )

    async def store_creator(self, creator: Dict):
        pass


# ═════════════════════════════════════════════════════════════════
# Excel 存储
# ═════════════════════════════════════════════════════════════════

class SohuExcelStoreImplement(AbstractStore):
    async def store_content(self, content_item: Dict):
        from store.excel_store_base import ExcelStoreBase
        await ExcelStoreBase.get_instance("sohu_videos").write_dict(content_item)

    async def store_comment(self, comment_item: Dict):
        from store.excel_store_base import ExcelStoreBase
        await ExcelStoreBase.get_instance("sohu_comments").write_dict(comment_item)

    async def store_creator(self, creator: Dict):
        pass
