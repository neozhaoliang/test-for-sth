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

    state = {"confirmed": False, "poll_count": 0}

    async def api_get(url, **kwargs):
        if url.endswith("/x/web-interface/nav"):
            return Response({
                "code": 0,
                "data": {"isLogin": state["confirmed"]},
            })
        if url.endswith("/generate"):
            return Response({"code": 0, "data": {
                "url": "https://passport.bilibili.com/h5-app/passport/login/scan?test=1",
                "qrcode_key": "abcdef",
            }})
        assert url.endswith("/poll")
        assert kwargs["params"]["qrcode_key"] == "abcdef"
        state["poll_count"] += 1
        if state["poll_count"] == 1:
            return Response({"code": 0, "data": {"code": 86101}})
        if state["poll_count"] == 2:
            return Response({"code": 0, "data": {"code": 86090}})
        state["confirmed"] = True
        return Response({"code": 0, "data": {"code": 0, "url": ""}})

    page = SimpleNamespace(
        is_closed=lambda: False,
        close=AsyncMock(),
    )
    context = SimpleNamespace(
        request=SimpleNamespace(get=api_get),
        cookies=AsyncMock(side_effect=lambda: [
            {"name": "SESSDATA", "value": "session-cookie"}
        ] if state["confirmed"] else []),
    )
    login = BilibiliLogin("qrcode", context, page)
    login._display_qrcode = AsyncMock(return_value=page)
    login._show_qrcode_status = AsyncMock()
    from media_platform.bilibili import login as login_module
    original_sleep = login_module.asyncio.sleep

    async def instant_sleep(_):
        return None

    login_module.asyncio.sleep = instant_sleep
    try:
        await login._login_by_qrcode_api()
    finally:
        login_module.asyncio.sleep = original_sleep
    assert state["poll_count"] == 3
    assert login._show_qrcode_status.await_count == 3
    assert "已扫码" in login._show_qrcode_status.await_args_list[1].args[1]
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


@pytest.mark.asyncio
async def test_stale_session_cookie_is_not_treated_as_logged_in():
    class Response:
        ok = True

        async def json(self):
            return {"code": 0, "data": {"isLogin": False}}

    context = SimpleNamespace(
        cookies=AsyncMock(return_value=[
            {"name": "SESSDATA", "value": "expired-cookie"},
        ]),
        request=SimpleNamespace(get=AsyncMock(return_value=Response())),
    )
    login = BilibiliLogin("qrcode", context, SimpleNamespace())
    assert await login.check_login_state() is False



@pytest.mark.asyncio
async def test_expired_qr_is_refreshed_instead_of_waiting_for_manual_login(monkeypatch):
    import media_platform.bilibili.login as login_module

    class Response:
        ok = True
        status = 200

        def __init__(self, payload):
            self.payload = payload

        async def json(self):
            return self.payload

    state = {"generated": 0, "confirmed": False}

    async def api_get(url, **kwargs):
        if url.endswith("/x/web-interface/nav"):
            return Response({"code": 0, "data": {"isLogin": state["confirmed"]}})
        if url.endswith("/generate"):
            state["generated"] += 1
            return Response({"code": 0, "data": {
                "url": "https://passport.bilibili.com/h5-app/passport/login/scan?test=1",
                "qrcode_key": f"qr{state['generated']}",
            }})
        assert url.endswith("/poll")
        if kwargs["params"]["qrcode_key"] == "qr1":
            return Response({"code": 0, "data": {"code": 86038}})
        state["confirmed"] = True
        return Response({"code": 0, "data": {"code": 0, "url": ""}})

    async def instant_sleep(_):
        return None

    monkeypatch.setattr(login_module.asyncio, "sleep", instant_sleep)
    monkeypatch.setattr(login_module.config, "BILI_QR_MAX_ATTEMPTS", 2)
    pages = [
        SimpleNamespace(is_closed=lambda: False, close=AsyncMock())
        for _ in range(2)
    ]
    context = SimpleNamespace(
        request=SimpleNamespace(get=api_get),
        cookies=AsyncMock(side_effect=lambda: [
            {"name": "SESSDATA", "value": "valid"}
        ] if state["confirmed"] else []),
    )
    login = BilibiliLogin("qrcode", context, pages[0])
    login._display_qrcode = AsyncMock(side_effect=pages)
    login._show_qrcode_status = AsyncMock()

    await login._login_by_qrcode_api()

    assert state["generated"] == 2
    for page in pages:
        page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_qrcode_callback_rejects_foreign_domain():
    login = BilibiliLogin(
        "qrcode",
        SimpleNamespace(request=SimpleNamespace(get=AsyncMock())),
        SimpleNamespace(),
    )
    with pytest.raises(RuntimeError, match="unexpected QR callback host"):
        await login._finalize_qrcode_login(
            {"url": "https://bilibili.com.example.net/steal"}
        )
