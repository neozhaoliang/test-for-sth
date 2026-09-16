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


class DataFetchError(Exception):
    """数据抓取失败"""
    pass


class WafChallengeError(Exception):
    """请求被阿里云 WAF 拦截 (返回了验证页而非 JSON)"""
    pass


class CrawlInterruptedError(Exception):
    """
    分页抓取被 WAF 中断, 记录中断页码以便从断点恢复。

    Attributes:
        page: 中断时正在抓取的页码 (恢复时应从此页继续)
        cause: 原始异常
    """

    def __init__(self, page: int, cause: Exception):
        self.page = page
        self.cause = cause
        super().__init__(f"Crawl interrupted at page {page}: {cause}")
