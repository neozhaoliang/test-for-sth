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
            "<img alt='Bilibili login QR code' width='384' height='384' "
            f"src='data:image/png;base64,{encoded}'>"
            "<p id='login-status' style='font-size:18px;color:#303030'>"
            "等待扫码，请使用哔哩哔哩 App 扫码并在手机确认</p>"
            "<p>约 180 秒后过期，失效后自动刷新。不要分享二维码。</p>"
            "<p>也可以在原 B 站标签页手动登录。</p></body></html>"
        )
        await page.bring_to_front()
        return page

    async def _show_qrcode_status(self, page: Page, message: str) -> None:
        """Update the Chrome tab with scan state, without exposing QR secrets."""
        try:
            await page.evaluate(
                "(message) => { const element = document.getElementById('login-status');"
                " if (element) element.textContent = message; }",
                message,
            )
        except Exception:
            pass  # Browser tab could have been closed by the user.

    async def _finalize_qrcode_login(self, status: dict) -> None:
        """Finish the cross-domain SSO callback and verify the browser session."""
        callback = status.get("url") or ""
        if callback:
            parsed = urlparse(callback)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not (
                host == "bilibili.com" or host.endswith(".bilibili.com")
            ):
                raise RuntimeError("Bilibili returned an unexpected QR callback host")
            # Since mid-2026 successful login may return a crossDomain ticket
            # URL. Following it sets SESSDATA through Set-Cookie, not URL params.
            reply = await self.browser_context.request.get(
                callback, timeout=15000
            )
            if not reply.ok:
                utils.logger.warning(
                    "[BilibiliLogin] SSO callback HTTP %s; checking Chrome session",
                    reply.status,
                )
        if await self.check_login_state():
            return
        if callback:
            # Some SSO responses require an actual browser navigation.
            await self.context_page.goto(
                callback, wait_until="domcontentloaded", timeout=15000
            )
        if not await self.check_login_state():
            raise RuntimeError(
                "Bilibili confirmed QR login, but /x/web-interface/nav still "
                "reports not logged in. Check that Chrome allows Bilibili cookies."
            )

    async def _login_by_qrcode_api(self) -> None:
        """Generate an official QR code, report states and refresh when expired."""
        request = self.browser_context.request
        base_url = "https://passport.bilibili.com/x/passport-login/web/qrcode"
        headers = {"Referer": "https://www.bilibili.com/"}
        max_attempts = max(1, int(getattr(config, "BILI_QR_MAX_ATTEMPTS", 2)))
        last_state = "not_scanned"
        for attempt in range(1, max_attempts + 1):
            response = await request.get(
                f"{base_url}/generate", headers=headers, timeout=15000
            )
            if not response.ok:
                raise RuntimeError(
                    f"Bilibili QR generation HTTP {response.status}"
                )
            payload = await response.json()
            data = payload.get("data") or {}
            login_url, qr_key = data.get("url"), data.get("qrcode_key")
            if payload.get("code") != 0 or not login_url or not qr_key:
                raise RuntimeError(
                    f"Bilibili QR generation rejected: "
                    f"{payload.get('message', 'unknown')}"
                )

            qr_page = await self._display_qrcode(login_url)
            utils.logger.info(
                "[BilibiliLogin] QR code %s/%s shown in Chrome. "
                "Scan with the Bilibili app, then confirm on your phone.",
                attempt, max_attempts,
            )
            previous_code = None
            try:
                deadline = asyncio.get_running_loop().time() + 175
                while asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(3)
                    # If the user logged into the original Bilibili tab, resume
                    # without waiting for the QR image or its poll result.
                    if await self.check_login_state():
                        utils.logger.info(
                            "[BilibiliLogin] Chrome session is authenticated."
                        )
                        return

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
                            f"Bilibili QR polling failed: "
                            f"{result.get('message', 'unknown')}"
                        )
                    status = result.get("data") or {}
                    code = status.get("code")
                    if code != previous_code:
                        previous_code = code
                        descriptions = {
                            86101: ("not_scanned", "等待扫码：使用 B 站 App 扫描电脑中的二维码"),
                            86090: ("scanned_waiting_confirmation", "已扫码，请在手机上点击确认登录"),
                            86038: ("expired", "二维码已过期，正在刷新"),
                            0: ("confirmed", "手机已确认，正在验证登录状态"),
                        }
                        state, message = descriptions.get(
                            code, ("unknown", f"二维码返回未知状态：{code}")
                        )
                        last_state = state
                        utils.logger.info(
                            "[BilibiliLogin] QR state=%s (code=%s), attempt=%s/%s",
                            state, code, attempt, max_attempts,
                        )
                        await self._show_qrcode_status(qr_page, message)
                    if code in (86101, 86090):
                        continue
                    if code == 86038:
                        break
                    if code == 0:
                        await self._finalize_qrcode_login(status)
                        utils.logger.info(
                            "[BilibiliLogin] Bilibili QR login verified."
                        )
                        return
                    raise RuntimeError(
                        f"Bilibili QR returned unsupported state {code}"
                    )
            finally:
                if not qr_page.is_closed():
                    await qr_page.close()

            if attempt < max_attempts:
                utils.logger.warning(
                    "[BilibiliLogin] QR session expired (state=%s). "
                    "Automatically generating a new QR code.", last_state
                )
        raise TimeoutError(
            f"Bilibili QR login not completed after {max_attempts} code(s) "
            f"(last_state={last_state}). Open Chrome, scan the QR code with "
            "the Bilibili app and tap Confirm on your phone."
        )

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
        except TimeoutError:
            # Do not launch another invisible three-minute manual wait after
            # the QR session has already expired.
            raise
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
