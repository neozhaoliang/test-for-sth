# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/bilibili/login.py
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
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/2 18:44
# @Desc    : bilibili login implementation class

import asyncio
import base64
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page
import config
from base.base_crawler import AbstractLogin
from tools import utils


class BilibiliLogin(AbstractLogin):
    def __init__(self,
                 login_type: str,
                 browser_context: BrowserContext,
                 context_page: Page,
                 login_phone: Optional[str] = "",
                 cookie_str: str = ""
                 ):
        config.LOGIN_TYPE = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.cookie_str = cookie_str

    async def begin(self):
        """Start login bilibili"""
        utils.logger.info("[BilibiliLogin.begin] Begin login Bilibili ...")
        if config.LOGIN_TYPE == "qrcode":
            await self.login_by_qrcode()
        elif config.LOGIN_TYPE == "phone":
            await self.login_by_mobile()
        elif config.LOGIN_TYPE == "cookie":
            await self.login_by_cookies()
        else:
            raise ValueError(
                "[BilibiliLogin.begin] Invalid Login Type Currently only supported qrcode or phone or cookie ...")

    async def check_login_state(self) -> bool:
        """Read the browser session without hidden retries.

        DedeUserID alone is not proof of a valid authenticated session.
        """
        cookies = await self.browser_context.cookies()
        _, cookie_dict = utils.convert_cookies(cookies)
        if not cookie_dict.get("SESSDATA"):
            return False
        try:
            response = await self.browser_context.request.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers={"Referer": "https://www.bilibili.com/"},
                timeout=10000,
            )
            if not response.ok:
                return False
            result = await response.json()
            return bool(
                result.get("code") == 0
                and (result.get("data") or {}).get("isLogin") is True
            )
        except Exception:
            # A cookie can outlive the actual login; never mistake it for
            # a verified session if the account endpoint rejects it.
            return False

    async def _wait_for_login(self, timeout_seconds: int = 180) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await self.check_login_state():
                return
            await asyncio.sleep(2)
        raise TimeoutError(
            "Bilibili login timed out. Please scan and confirm the QR code "
            "in the Bilibili app, or log in manually in the Chrome window."
        )

    async def _display_qrcode(self, login_url: str) -> Page:
        """Show the Bilibili-issued QR URL in our existing Chrome context."""
        # OpenCV is already a project dependency, so no QR package or
        # third-party image service is needed. Verify the output with a
        # QRCodeDetector in tests to guard against invalid/undersized codes.
        import cv2

        qr = cv2.QRCodeEncoder_create().encode(login_url)
        qr = cv2.copyMakeBorder(
            qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255
        )
        qr = cv2.resize(
            qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST
        )
        encoded_ok, png = cv2.imencode(".png", qr)
        if not encoded_ok:
            raise RuntimeError("OpenCV failed to render the Bilibili QR image")
        encoded = base64.b64encode(png.tobytes()).decode("ascii")
        page = await self.browser_context.new_page()
        await page.set_content(
            "<html><head><meta charset='utf-8'><title>Bilibili QR Login</title>"
            "</head><body style='font-family:sans-serif;text-align:center;"
            "padding:35px'><h2>请使用哔哩哔哩 App 扫码并确认登录</h2>"
            "<img alt='Bilibili login QR code' width='280' height='280' "
            f"src='data:image/png;base64,{encoded}'>"
            "<p>二维码约 180 秒后过期，请勿分享登录二维码。</p></body></html>"
        )
        await page.bring_to_front()
        return page

    async def _login_by_qrcode_api(self) -> None:
        """Use Bilibili's official web QR login, independent of homepage DOM."""
        request = self.browser_context.request
        base_url = "https://passport.bilibili.com/x/passport-login/web/qrcode"
        headers = {"Referer": "https://www.bilibili.com/"}
        response = await request.get(
            f"{base_url}/generate", headers=headers, timeout=15000
        )
        if not response.ok:
            raise RuntimeError(f"Bilibili QR generation HTTP {response.status}")
        payload = await response.json()
        data = payload.get("data") or {}
        login_url, qr_key = data.get("url"), data.get("qrcode_key")
        if payload.get("code") != 0 or not login_url or not qr_key:
            raise RuntimeError(
                f"Bilibili QR generation rejected: {payload.get('message', 'unknown')}"
            )

        qr_page = await self._display_qrcode(login_url)
        utils.logger.info(
            "[BilibiliLogin] QR code displayed in Chrome; "
            "scan with Bilibili app and confirm within 180 seconds."
        )
        try:
            deadline = asyncio.get_running_loop().time() + 175
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(2)
                response = await request.get(
                    f"{base_url}/poll",
                    params={"qrcode_key": qr_key},
                    headers=headers,
                    timeout=15000,
                )
                if not response.ok:
                    raise RuntimeError(
                        f"Bilibili QR polling HTTP {response.status}"
                    )
                result = await response.json()
                if result.get("code") != 0:
                    raise RuntimeError(
                        f"Bilibili QR polling failed: {result.get('message', 'unknown')}"
                    )
                status = result.get("data") or {}
                code = status.get("code")
                if code in (86101, 86090):  # not scanned / waiting for confirmation
                    continue
                if code == 86038:
                    raise TimeoutError("Bilibili QR code expired; rerun to get a new one.")
                if code != 0:
                    raise RuntimeError(
                        f"Bilibili QR login rejected (status={code}): "
                        f"{status.get('message', 'unknown')}"
                    )

                # BrowserContext.request shares cookies with the browser context.
                # Some Bilibili sessions also need the same-site SSO redirect.
                if not await self.check_login_state():
                    callback = status.get("url") or ""
                    host = urlparse(callback).hostname or ""
                    if host == "bilibili.com" or host.endswith(".bilibili.com"):
                        await self.context_page.goto(
                            callback, wait_until="domcontentloaded", timeout=15000
                        )
                if await self.check_login_state():
                    utils.logger.info("[BilibiliLogin] QR login succeeded.")
                    return
                raise RuntimeError(
                    "Bilibili returned QR success but the browser has no SESSDATA cookie."
                )
            raise TimeoutError("Bilibili QR login expired before confirmation.")
        finally:
            if not qr_page.is_closed():
                await qr_page.close()

    async def _login_manually_in_browser(self) -> None:
        """Fallback if Bilibili's QR API is blocked or qrcode is unavailable."""
        utils.logger.warning(
            "[BilibiliLogin] Opening Bilibili for manual login. "
            "The crawler will resume after the browser has a valid session."
        )
        if self.context_page.is_closed():
            self.context_page = await self.browser_context.new_page()
        await self.context_page.goto(
            "https://www.bilibili.com/", wait_until="domcontentloaded"
        )
        # Multiple short probes replace the old fragile exact-class XPath.
        selectors = (
            ".right-entry__outside.go-login-btn",
            "[class*='go-login-btn']",
            "[class*='header-login-entry']",
            "[class*='login-entry']",
            "text=登录",
        )
        for selector in selectors:
            try:
                button = self.context_page.locator(selector).first
                if await button.is_visible(timeout=1500):
                    await button.click(timeout=2500)
                    break
            except Exception:
                continue
        utils.logger.info(
            "[BilibiliLogin] If the login modal is not open, "
            "click '登录' yourself in Chrome and scan the QR code."
        )
        await self._wait_for_login()
        utils.logger.info("[BilibiliLogin] Manual browser login succeeded.")

    async def login_by_qrcode(self) -> None:
        """Log in without assuming a particular Bilibili homepage layout."""
        utils.logger.info("[BilibiliLogin.login_by_qrcode] Begin Bilibili QR login...")
        try:
            await self._login_by_qrcode_api()
        except Exception as exc:
            utils.logger.warning(
                "[BilibiliLogin] QR API login unavailable (%s: %s). "
                "Falling back to manual browser login.",
                type(exc).__name__,
                exc,
            )
            await self._login_manually_in_browser()

    async def login_by_mobile(self):
        pass

    async def login_by_cookies(self):
        utils.logger.info("[BilibiliLogin.login_by_qrcode] Begin login bilibili by cookie ...")
        for key, value in utils.convert_str_cookie_to_dict(self.cookie_str).items():
            await self.browser_context.add_cookies([{
                'name': key,
                'value': value,
                'domain': ".bilibili.com",
                'path': "/"
            }])
