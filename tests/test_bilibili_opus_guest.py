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
