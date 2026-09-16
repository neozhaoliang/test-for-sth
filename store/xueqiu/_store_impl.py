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


# -*- coding: utf-8 -*-
from typing import Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from base.base_crawler import AbstractStore
from database.db_session import get_session
from database.models import XueqiuStatus, XueqiuComment, XueqiuCreator
from tools import utils
from var import crawler_type_var
from tools.async_file_writer import AsyncFileWriter
from database.mongodb_store_base import MongoDBStoreBase


class XueqiuCsvStoreImplement(AbstractStore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.writer = AsyncFileWriter(platform="xueqiu", crawler_type=crawler_type_var.get())

    async def store_content(self, content_item: Dict):
        # 每个用户单独一个文件 (file_prefix=user_id)
        prefix = str(content_item.get("user_id", "") or "")
        await self.writer.write_to_csv(item_type="contents", item=content_item, file_prefix=prefix)

    async def store_comment(self, comment_item: Dict):
        prefix = str(comment_item.get("user_id", "") or "")
        await self.writer.write_to_csv(item_type="comments", item=comment_item, file_prefix=prefix)

    async def store_creator(self, creator: Dict):
        prefix = str(creator.get("user_id", "") or "")
        await self.writer.write_to_csv(item_type="creators", item=creator, file_prefix=prefix)


class XueqiuDbStoreImplement(AbstractStore):
    async def store_content(self, content_item: Dict):
        """
        Xueqiu status DB storage implementation
        Args:
            content_item: status item dict
        """
        status_id = content_item.get("status_id")
        async with get_session() as session:
            stmt = select(XueqiuStatus).where(XueqiuStatus.status_id == status_id)
            result = await session.execute(stmt)
            existing_content = result.scalars().first()
            if existing_content:
                for key, value in content_item.items():
                    if hasattr(existing_content, key):
                        setattr(existing_content, key, value)
            else:
                if "add_ts" not in content_item:
                    content_item["add_ts"] = utils.get_current_timestamp()
                new_content = XueqiuStatus(**content_item)
                session.add(new_content)
            await session.commit()

    async def store_comment(self, comment_item: Dict):
        """
        Xueqiu comment DB storage implementation
        Args:
            comment_item: comment item dict
        """
        comment_id = comment_item.get("comment_id")
        async with get_session() as session:
            stmt = select(XueqiuComment).where(XueqiuComment.comment_id == comment_id)
            result = await session.execute(stmt)
            existing_comment = result.scalars().first()
            if existing_comment:
                for key, value in comment_item.items():
                    if hasattr(existing_comment, key):
                        setattr(existing_comment, key, value)
            else:
                if "add_ts" not in comment_item:
                    comment_item["add_ts"] = utils.get_current_timestamp()
                new_comment = XueqiuComment(**comment_item)
                session.add(new_comment)
            await session.commit()

    async def store_creator(self, creator: Dict):
        """
        Xueqiu creator DB storage implementation
        Args:
            creator: creator dict
        """
        user_id = creator.get("user_id")
        async with get_session() as session:
            stmt = select(XueqiuCreator).where(XueqiuCreator.user_id == user_id)
            result = await session.execute(stmt)
            existing_creator = result.scalars().first()
            if existing_creator:
                for key, value in creator.items():
                    if hasattr(existing_creator, key):
                        setattr(existing_creator, key, value)
            else:
                if "add_ts" not in creator:
                    creator["add_ts"] = utils.get_current_timestamp()
                new_creator = XueqiuCreator(**creator)
                session.add(new_creator)
            await session.commit()


class XueqiuJsonStoreImplement(AbstractStore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.writer = AsyncFileWriter(platform="xueqiu", crawler_type=crawler_type_var.get())

    async def store_content(self, content_item: Dict):
        # 每个用户单独一个文件 (file_prefix=user_id)
        prefix = str(content_item.get("user_id", "") or "")
        await self.writer.write_single_item_to_json(item_type="contents", item=content_item, file_prefix=prefix)

    async def store_comment(self, comment_item: Dict):
        prefix = str(comment_item.get("user_id", "") or "")
        await self.writer.write_single_item_to_json(item_type="comments", item=comment_item, file_prefix=prefix)

    async def store_creator(self, creator: Dict):
        prefix = str(creator.get("user_id", "") or "")
        await self.writer.write_single_item_to_json(item_type="creators", item=creator, file_prefix=prefix)


class XueqiuJsonlStoreImplement(AbstractStore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.writer = AsyncFileWriter(platform="xueqiu", crawler_type=crawler_type_var.get())

    async def store_content(self, content_item: Dict):
        # 每个用户单独一个文件 (file_prefix=user_id)
        prefix = str(content_item.get("user_id", "") or "")
        await self.writer.write_to_jsonl(item_type="contents", item=content_item, file_prefix=prefix)

    async def store_comment(self, comment_item: Dict):
        prefix = str(comment_item.get("user_id", "") or "")
        await self.writer.write_to_jsonl(item_type="comments", item=comment_item, file_prefix=prefix)

    async def store_creator(self, creator: Dict):
        prefix = str(creator.get("user_id", "") or "")
        await self.writer.write_to_jsonl(item_type="creators", item=creator, file_prefix=prefix)


class XueqiuSqliteStoreImplement(XueqiuDbStoreImplement):
    """
    Xueqiu content SQLite storage implementation
    """
    pass


class XueqiuMongoStoreImplement(AbstractStore):
    """Xueqiu MongoDB storage implementation"""

    def __init__(self):
        self.mongo_store = MongoDBStoreBase(collection_prefix="xueqiu")

    async def store_content(self, content_item: Dict):
        status_id = content_item.get("status_id")
        if not status_id:
            return

        await self.mongo_store.save_or_update(
            collection_suffix="contents",
            query={"status_id": status_id},
            data=content_item
        )
        utils.logger.info(f"[XueqiuMongoStoreImplement.store_content] Saved status {status_id} to MongoDB")

    async def store_comment(self, comment_item: Dict):
        comment_id = comment_item.get("comment_id")
        if not comment_id:
            return

        await self.mongo_store.save_or_update(
            collection_suffix="comments",
            query={"comment_id": comment_id},
            data=comment_item
        )
        utils.logger.info(f"[XueqiuMongoStoreImplement.store_comment] Saved comment {comment_id} to MongoDB")

    async def store_creator(self, creator_item: Dict):
        user_id = creator_item.get("user_id")
        if not user_id:
            return

        await self.mongo_store.save_or_update(
            collection_suffix="creators",
            query={"user_id": user_id},
            data=creator_item
        )
        utils.logger.info(f"[XueqiuMongoStoreImplement.store_creator] Saved creator {user_id} to MongoDB")


class XueqiuExcelStoreImplement:
    """Xueqiu Excel storage implementation - Global singleton"""

    def __new__(cls, *args, **kwargs):
        from store.excel_store_base import ExcelStoreBase
        return ExcelStoreBase.get_instance(
            platform="xueqiu",
            crawler_type=crawler_type_var.get()
        )
