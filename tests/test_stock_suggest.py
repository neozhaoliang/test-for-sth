"""Stock autocomplete: matching quality, cache, fallback, bounded API results."""
import asyncio

import pandas as pd
import pytest

from analysis import stock_suggest


@pytest.fixture(autouse=True)
def reset_catalog(monkeypatch):
    monkeypatch.setattr(stock_suggest, "_cache_until", 0.0)
    monkeypatch.setattr(stock_suggest, "_cache_rows", [])
    monkeypatch.setattr(stock_suggest, "_cache_source", "local")


def fake_spot():
    return pd.DataFrame({
        "代码": ["sh600938", "sh601088", "sh601919", "sz000333", "sh601899",
                 "sh600000", "sh600001", "bad123"],
        "名称": ["中国海油", "中国神华", "中远海控", "美的集团", "紫金矿业",
                 "中国海洋", "中国海洋能源", "invalid"],
    })


@pytest.mark.asyncio
async def test_exact_name_substring_code_prefix_and_suffix(monkeypatch):
    async def spot():
        return fake_spot()
    monkeypatch.setattr(stock_suggest.realtime_price, "_get_spot_df", spot)
    exact = await stock_suggest.search_stocks("中国海油")
    assert exact["source"] == "spot"
    assert exact["items"][0]["code"] == "SH600938"
    assert exact["items"][0]["name"] == "中国海油"
    assert (await stock_suggest.search_stocks("海油"))["items"][0]["code"] == "SH600938"
    assert (await stock_suggest.search_stocks("600938"))["items"][0]["code"] == "SH600938"
    assert (await stock_suggest.search_stocks("sh600938"))["items"][0]["code"] == "SH600938"
    assert (await stock_suggest.search_stocks("紫金"))["items"][0]["code"] == "SH601899"
    assert (await stock_suggest.search_stocks("美的"))["items"][0]["code"] == "SZ000333"
    assert any(item["name"] == "中国海油"\n               for item in (await stock_suggest.search_stocks("中海"))["items"])
    # A full ticker and its corresponding exact name outrank fuzzy subsequences.
    assert (await stock_suggest.search_stocks("中国海洋"))["items"][0]["code"] == "SH600000"
    assert all(item["code"] != "BAD123" for item in exact["items"])


@pytest.mark.asyncio
async def test_market_list_downloaded_once_for_many_queries(monkeypatch):
    count = 0
    async def spot():
        nonlocal count
        count += 1
        await asyncio.sleep(.01)
        return fake_spot()
    monkeypatch.setattr(stock_suggest.realtime_price, "_get_spot_df", spot)
    results = await asyncio.gather(*(stock_suggest.search_stocks(q) for q in [
        "中", "中国", "中国海", "海油", "600938", "美的"
    ]))
    assert count == 1
    assert all(result["items"] for result in results)


@pytest.mark.asyncio
async def test_missing_spot_uses_local_without_fabricating_market_prices(monkeypatch):
    async def offline():
        return None
    monkeypatch.setattr(stock_suggest.realtime_price, "_get_spot_df", offline)
    from analysis import candidates
    monkeypatch.setattr(candidates, "list_supported_stocks",
                        lambda: [{"stock_code": "SZ002230", "stock_name": "科大讯飞"}])
    result = await stock_suggest.search_stocks("讯飞")
    assert result["source"] == "local"
    assert result["items"][0] == {"code": "SZ002230", "name": "科大讯飞", "market": "SZ"}
    # Frequently used stocks remain searchable if the external quote service is down.
    assert (await stock_suggest.search_stocks("中国海油"))["items"][0]["code"] == "SH600938"


@pytest.mark.asyncio
async def test_bounds_empty_values_unicode_and_no_data_leak(monkeypatch):
    async def spot():
        return fake_spot()
    monkeypatch.setattr(stock_suggest.realtime_price, "_get_spot_df", spot)
    assert (await stock_suggest.search_stocks("  "))["items"] == []
    assert (await stock_suggest.search_stocks("６００９３８"))["items"][0]["code"] == "SH600938"
    assert len((await stock_suggest.search_stocks("中", limit=1))["items"]) == 1
    assert len((await stock_suggest.search_stocks("中", limit=100))["items"]) <= 15
    assert (await stock_suggest.search_stocks("绝对不存在的股票代码"))["items"] == []
    for result in (await stock_suggest.search_stocks("中国"))["items"]:
        assert set(result) == {"code", "name", "market"}


def test_optional_pinyin_initials_when_library_available(monkeypatch):
    monkeypatch.setattr(stock_suggest, "lazy_pinyin",
                        lambda s: ["zhong", "guo", "hai", "you"] if s == "中国海油" else [])
    row = {"code": "SH600938", "name": "中国海油", "market": "SH"}
    assert stock_suggest._score("zghy", row) == 5
    assert stock_suggest._score("zhongguohaiyou", row) == 6
