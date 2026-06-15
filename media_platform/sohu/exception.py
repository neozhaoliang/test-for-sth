# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

from httpx import RequestError


class DataFetchError(RequestError):
    """data fetch error"""


class IPBlockError(RequestError):
    """IP blocked by server"""


class VideoNotFoundError(DataFetchError):
    """video not found or removed"""
