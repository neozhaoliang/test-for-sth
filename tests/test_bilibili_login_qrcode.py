"""Offline regression tests for Bilibili QR login.

No live account, network call, or real QR scan is needed.
"""
import base64
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest

from media_platform.bilibili.login import BilibiliLogin


@pytest.mark.asyncio
async def test_qrcode_png_is_decodable_and_shown_in_chrome():
    url = (
        "https://passport.bilibili.com/h5-app/passport/login/scan?"
        "navhide=1&qrcode_key=8587cf8106a0b863c46d6bab913537f6"
    )
    page = SimpleNamespace(set_content=AsyncMock(), bring_to_front=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    login = BilibiliLogin("qrcode", context, page)

    assert await login._display_qrcode(url) is page
    html = page.set_content.await_args.args[0]
    b64 = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html)
    assert b64 is not None
    pixels = np.frombuffer(base64.b64decode(b64.group(1)), dtype=np.uint8)
    image = cv2.imdecode(pixels, cv2.IMREAD_GRAYSCALE)
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    assert decoded == url
    page.bring_to_front.assert_awaited_once()


@pytest.mark.asyncio
async def test_qrcode_poll_success_reuses_browser_session():
    class Response:
        ok = True
        status = 200

        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body

    async def api_get(url, **kwargs):
        if url.endswith("/x/web-interface/nav"):
            return Response({"code": 0, "data": {"isLogin": True}})
        if url.endswith("/generate"):
            return Response({"code": 0, "data": {
                "url": "https://passport.bilibili.com/h5-app/passport/login/scan?test=1",
                "qrcode_key": "abcdef",
            }})
        assert url.endswith("/poll")
        assert kwargs["params"]["qrcode_key"] == "abcdef"
        return Response({"code": 0, "data": {"code": 0, "url": ""}})

    page = SimpleNamespace(
        is_closed=lambda: False,
        close=AsyncMock(),
    )
    context = SimpleNamespace(
        request=SimpleNamespace(get=api_get),
        cookies=AsyncMock(return_value=[
            {"name": "SESSDATA", "value": "session-cookie"},
        ]),
    )
    login = BilibiliLogin("qrcode", context, page)
    login._display_qrcode = AsyncMock(return_value=page)
    await login._login_by_qrcode_api()
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_qrcode_api_failure_falls_back_to_manual_login():
    context = SimpleNamespace(cookies=AsyncMock(return_value=[]))
    login = BilibiliLogin("qrcode", context, SimpleNamespace())
    login._login_by_qrcode_api = AsyncMock(side_effect=RuntimeError("API unavailable"))
    login._login_manually_in_browser = AsyncMock()

    await login.login_by_qrcode()

    login._login_manually_in_browser.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_login_state_rejects_only_user_id_cookie():
    context = SimpleNamespace(
        cookies=AsyncMock(return_value=[{"name": "DedeUserID", "value": "1234"}])
    )
    login = BilibiliLogin("qrcode", context, SimpleNamespace())
    assert await login.check_login_state() is False
