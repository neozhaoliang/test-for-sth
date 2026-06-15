# -*- coding: utf-8 -*-
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

import asyncio
import sys
from typing import Optional

from playwright.async_api import BrowserContext, Page

import config
from base.base_crawler import AbstractLogin
from tools import utils


class SohuTvLogin(AbstractLogin):
    """搜狐视频登录处理器"""

    def __init__(
        self,
        login_type: str,
        browser_context: BrowserContext,
        context_page: Page,
        login_phone: Optional[str] = "",
        cookie_str: Optional[str] = "",
    ):
        config.LOGIN_TYPE = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.cookie_str = cookie_str

    async def begin(self):
        """开始登录"""
        utils.logger.info("[SohuTvLogin.begin] 开始搜狐视频登录流程...")

        if config.LOGIN_TYPE == "qrcode":
            await self.login_by_qrcode()
        elif config.LOGIN_TYPE == "phone":
            await self.login_by_mobile()
        elif config.LOGIN_TYPE == "cookie":
            await self.login_by_cookies()
        else:
            raise ValueError("[SohuTvLogin.begin] 不支持的登录类型")

        await asyncio.sleep(3)

    async def login_by_qrcode(self):
        """扫码登录"""
        utils.logger.info("[SohuTvLogin.login_by_qrcode] 扫码登录暂不支持，请使用 cookie 登录")
        utils.logger.info("[SohuTvLogin.login_by_qrcode] 搜狐视频浏览内容通常不需要登录，跳过登录步骤")

    async def login_by_mobile(self):
        """手机号登录"""
        utils.logger.info("[SohuTvLogin.login_by_mobile] 手机号登录暂不支持，请使用 cookie 登录")

    async def login_by_cookies(self):
        """Cookie 登录"""
        utils.logger.info("[SohuTvLogin.login_by_cookies] 开始通过 cookie 登录...")
        if not self.cookie_str:
            utils.logger.info("[SohuTvLogin.login_by_cookies] 未提供 cookie，搜狐视频浏览通常不需要登录，继续...")
            return

        for key, value in utils.convert_str_cookie_to_dict(self.cookie_str).items():
            await self.browser_context.add_cookies([{
                'name': key,
                'value': value,
                'domain': ".sohu.com",
                'path': "/",
            }])
        utils.logger.info("[SohuTvLogin.login_by_cookies] cookie 注入完成")
