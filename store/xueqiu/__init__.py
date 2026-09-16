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
from typing import List

import config
from base.base_crawler import AbstractStore
from model.m_xueqiu import XueqiuComment, XueqiuCreator, XueqiuStatus
from ._store_impl import (
    XueqiuCsvStoreImplement,
    XueqiuDbStoreImplement,
    XueqiuJsonStoreImplement,
    XueqiuJsonlStoreImplement,
    XueqiuSqliteStoreImplement,
    XueqiuMongoStoreImplement,
    XueqiuExcelStoreImplement,
)
from tools import utils
from var import source_keyword_var


class XueqiuStoreFactory:
    STORES = {
        "csv": XueqiuCsvStoreImplement,
        "db": XueqiuDbStoreImplement,
        "postgres": XueqiuDbStoreImplement,
        "json": XueqiuJsonStoreImplement,
        "jsonl": XueqiuJsonlStoreImplement,
        "sqlite": XueqiuSqliteStoreImplement,
        "mongodb": XueqiuMongoStoreImplement,
        "excel": XueqiuExcelStoreImplement,
    }

    @staticmethod
    def create_store() -> AbstractStore:
        store_class = XueqiuStoreFactory.STORES.get(config.SAVE_DATA_OPTION)
        if not store_class:
            raise ValueError(
                "[XueqiuStoreFactory.create_store] Invalid save option only supported csv or db or json or jsonl or sqlite or mongodb or excel ..."
            )
        return store_class()


async def batch_update_xueqiu_statuses(statuses: List[XueqiuStatus]):
    """
    Batch update xueqiu statuses
    Args:
        statuses:

    Returns:

    """
    if not statuses:
        return

    for status_item in statuses:
        await update_xueqiu_status(status_item)


async def update_xueqiu_status(status_item: XueqiuStatus):
    """
    Update xueqiu status
    Args:
        status_item:

    Returns:

    """
    status_item.source_keyword = source_keyword_var.get()
    local_db_item = status_item.model_dump()
    local_db_item.update({"last_modify_ts": utils.get_current_timestamp()})
    utils.logger.info(f"[store.xueqiu.update_xueqiu_status] xueqiu status: {local_db_item}")
    await XueqiuStoreFactory.create_store().store_content(local_db_item)


async def batch_update_xueqiu_comments(comments: List[XueqiuComment]):
    """
    Batch update xueqiu comments (用户发出的回复)
    Args:
        comments:

    Returns:

    """
    if not comments:
        return

    for comment_item in comments:
        await update_xueqiu_comment(comment_item)


async def update_xueqiu_comment(comment_item: XueqiuComment):
    """
    Update xueqiu comment
    Args:
        comment_item:

    Returns:

    """
    local_db_item = comment_item.model_dump()
    local_db_item.update({"last_modify_ts": utils.get_current_timestamp()})
    utils.logger.info(f"[store.xueqiu.update_xueqiu_comment] xueqiu comment: {local_db_item}")
    await XueqiuStoreFactory.create_store().store_comment(local_db_item)


async def save_creator(creator: XueqiuCreator):
    """
    Save xueqiu creator information
    Args:
        creator:

    Returns:

    """
    if not creator:
        return
    local_db_item = creator.model_dump()
    local_db_item.update({"last_modify_ts": utils.get_current_timestamp()})
    await XueqiuStoreFactory.create_store().store_creator(local_db_item)
