"""Behavioral tests for guest-first Bilibili Opus collection."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from media_platform.bilibili.core import BilibiliCrawler
from media_platform.bilibili.exception import DataFetchError


@pytest.mark.asyncio
async def test_public_opus_succeeds_without_login():
    crawler = BilibiliCrawler()
    crawler._opus_guest_mode = True
    fetch = AsyncMock(return_value=[])
    crawler.bili_client = SimpleNamespace(get_creator_all_opus=fetch)
    crawler._login_and_refresh_client = AsyncMock()

    await crawler.get_opus(123456)

    fetch.assert_awaited_once()
    crawler._login_and_refresh_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_guest_api_refusal_triggers_one_authenticated_retry():
    crawler = BilibiliCrawler()
    crawler._opus_guest_mode = True
    fetch = AsyncMock(side_effect=[DataFetchError("账号未登录"), []])
    crawler.bili_client = SimpleNamespace(get_creator_all_opus=fetch)
    crawler._login_and_refresh_client = AsyncMock()

    await crawler.get_opus(123456)

    assert fetch.await_count == 2
    crawler._login_and_refresh_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_authenticated_api_failure_does_not_start_endless_login():
    crawler = BilibiliCrawler()
    crawler._opus_guest_mode = False
    fetch = AsyncMock(side_effect=DataFetchError("permission denied"))
    crawler.bili_client = SimpleNamespace(get_creator_all_opus=fetch)
    crawler._login_and_refresh_client = AsyncMock()

    await crawler.get_opus(123456)

    fetch.assert_awaited_once()
    crawler._login_and_refresh_client.assert_not_awaited()



@pytest.mark.asyncio
async def test_opus_start_does_not_block_on_login_when_public_first(monkeypatch):
    import media_platform.bilibili.core as module

    class FakePlaywright:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *args):
            return False

    page = SimpleNamespace(goto=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    crawler = BilibiliCrawler()
    crawler.launch_browser_with_cdp = AsyncMock(return_value=context)
    crawler.create_bilibili_client = AsyncMock(return_value=SimpleNamespace(
        pong=AsyncMock(return_value=False),
    ))
    crawler._login_and_refresh_client = AsyncMock()
    crawler.get_opus = AsyncMock()

    monkeypatch.setattr(module, "async_playwright", FakePlaywright)
    monkeypatch.setattr(module.config, "ENABLE_CDP_MODE", True)
    monkeypatch.setattr(module.config, "CRAWLER_TYPE", "opus")
    monkeypatch.setattr(module.config, "BILI_OPUS_PUBLIC_FIRST", True)
    monkeypatch.setattr(module.config, "ENABLE_IP_PROXY", False)
    monkeypatch.setattr(module.config, "BILI_CREATOR_ID_LIST", ["20813884"])

    await crawler.start()

    crawler._login_and_refresh_client.assert_not_awaited()
    crawler.get_opus.assert_awaited_once_with(20813884)
