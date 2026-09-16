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


import asyncio
from typing import Optional

from playwright.async_api import BrowserContext, Page

from tools import utils


class XueqiuLogin:
    """
    雪球登录 (可选)。爬取公开数据时通常无需登录;
    若配置了 cookie 则写入浏览器, 若选择了扫码登录则等待人工完成。
    """

    def __init__(
        self,
        login_type: str,
        browser_context: BrowserContext,
        context_page: Page,
        login_phone: Optional[str] = "",
        cookie_str: str = "",
    ):
        self.login_type = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.cookie_str = cookie_str

    async def begin(self):
        utils.logger.info("[XueqiuLogin.begin] Begin login xueqiu ...")
        if self.login_type == "qrcode":
            await self.login_by_qrcode()
        elif self.login_type == "cookie":
            await self.login_by_cookies()
        else:
            raise ValueError(f"[XueqiuLogin.begin] Invalid login_type: {self.login_type}")

    async def login_by_qrcode(self):
        """
        扫码登录: 跳转到登录页, 等待用户在有头浏览器中扫码完成。
        """
        utils.logger.info("[XueqiuLogin.login_by_qrcode] 打开登录页, 请在有头浏览器中扫码/输入账号密码 ...")
        await self.context_page.goto("https://xueqiu.com/login", wait_until="domcontentloaded")
        # 等待用户完成登录: 轮询检查 cookie 中是否出现有效登录标识
        for _ in range(120):
            cookies = await self.browser_context.cookies()
            cookie_names = {c.get("name") for c in cookies}
            if "xq_a_token" in cookie_names and "u" in cookie_names:
                utils.logger.info("[XueqiuLogin.login_by_qrcode] 登录成功")
                return
            await asyncio.sleep(5)
        utils.logger.error("[XueqiuLogin.login_by_qrcode] 等待登录超时 (10 分钟)")

    async def login_by_cookies(self):
        utils.logger.info("[XueqiuLogin.login_by_cookies] Begin login xueqiu by cookie ...")
        cookie_dict = utils.convert_str_cookie_to_dict(self.cookie_str)
        domain = ".xueqiu.com"
        for key, value in cookie_dict.items():
            await self.browser_context.add_cookies([{
                "name": key,
                "value": value,
                "domain": domain,
                "path": "/",
            }])
        utils.logger.info("[XueqiuLogin.login_by_cookies] Cookie 已写入浏览器")
