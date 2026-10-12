"""Dependency-free behavioral checks for Bilibili login.

Extracts the *actual* BilibiliLogin class AST to run QR code state machines
without installing the whole crawler (or contacting Bilibili).
Run: python tests/test_bilibili_qr_standalone.py
"""
import ast
import asyncio
import base64
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlparse


def load_class():
    source = Path(__file__).resolve().parents[1] / "media_platform/bilibili/login.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    class_node = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "BilibiliLogin"
    )
    namespace = {
        "asyncio": asyncio,
        "base64": base64,
        "urlparse": urlparse,
        "Optional": Optional,
        "AbstractLogin": object,
        "BrowserContext": object,
        "Page": object,
        "config": SimpleNamespace(LOGIN_TYPE="qrcode", BILI_QR_MAX_ATTEMPTS=2),
        "utils": SimpleNamespace(logger=logging.getLogger("smoke"), convert_cookies=Mock()),
    }
    exec(compile(ast.Module(body=[class_node], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["BilibiliLogin"]


Login = load_class()


class Response:
    ok = True
    status = 200

    def __init__(self, data):
        self.data = data

    async def json(self):
        return self.data


class BilibiliQrStateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sleep_patcher = patch("asyncio.sleep", new=AsyncMock())
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    def create_login(self, poll_codes):
        state = {"attempts": 0, "poll": 0, "confirmed": False, "seen": []}
        codes = iter(poll_codes)

        async def get(url, **kwargs):
            if url.endswith("/generate"):
                state["attempts"] += 1
                return Response({"code": 0, "data": {
                    "url": "https://passport.bilibili.com/h5-app/passport/login/scan?key=test",
                    "qrcode_key": str(state["attempts"]),
                }})
            if url.endswith("/poll"):
                state["poll"] += 1
                code = next(codes)
                state["seen"].append(code)
                state["confirmed"] = code == 0
                return Response({"code": 0, "data": {"code": code, "url": ""}})
            raise AssertionError(url)

        context = SimpleNamespace(request=SimpleNamespace(get=get))
        login = Login("qrcode", context, SimpleNamespace())
        login.check_login_state = AsyncMock(side_effect=lambda: state["confirmed"])
        pages = []

        async def show_qr(_url):
            page = SimpleNamespace(
                is_closed=Mock(return_value=False),
                close=AsyncMock(),
            )
            pages.append(page)
            return page

        login._display_qrcode = show_qr
        login._show_qrcode_status = AsyncMock()
        return login, state, pages

    async def test_scan_and_confirmation(self):
        login, state, pages = self.create_login([86101, 86090, 0])
        await login._login_by_qrcode_api()
        self.assertEqual(state["seen"], [86101, 86090, 0])
        self.assertEqual(login._show_qrcode_status.await_count, 3)
        pages[0].close.assert_awaited_once()

    async def test_expiry_refresh(self):
        login, state, pages = self.create_login([86038, 0])
        await login._login_by_qrcode_api()
        self.assertEqual(state["attempts"], 2)
        self.assertEqual(len(pages), 2)
        for page in pages:
            page.close.assert_awaited_once()

    async def test_timeout_does_not_start_second_manual_wait(self):
        login, _, _ = self.create_login([])
        login._login_by_qrcode_api = AsyncMock(side_effect=TimeoutError("expired"))
        login._login_manually_in_browser = AsyncMock()
        with self.assertRaisesRegex(TimeoutError, "expired"):
            await login.login_by_qrcode()
        login._login_manually_in_browser.assert_not_awaited()

    async def test_sso_callback_rejects_foreign_host(self):
        login, _, _ = self.create_login([])
        with self.assertRaisesRegex(RuntimeError, "unexpected QR callback host"):
            await login._finalize_qrcode_login(
                {"url": "https://bilibili.com.evil.example/login"}
            )


if __name__ == "__main__":
    unittest.main()
