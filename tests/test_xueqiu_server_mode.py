"""No-network regression checks for authenticated Xueqiu server mode."""
import json
import pytest

from media_platform.xueqiu.client import XueqiuClient
from media_platform.xueqiu.exception import DataFetchError
from tools.xueqiu_server_login import logged_in_cookie_names, timeline_result


class FakePage:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    async def goto(self, url, **kwargs):
        self.urls.append(url)

    async def evaluate(self, source):
        return json.dumps(self.payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_login_required_on_page_two_is_an_error_not_end_of_posts():
    page = FakePage({"error_code": "10022", "error_description": "请登录"})
    client = XueqiuClient(page)
    with pytest.raises(DataFetchError, match="10022"):
        await client.get_user_posts("1263638109", page=2)
    assert "page=2" in page.urls[0]


@pytest.mark.asyncio
async def test_valid_timeline_page_has_statuses():
    page = FakePage({"statuses": [{"id": "101"}, {"id": "102"}]})
    client = XueqiuClient(page)
    result = await client.get_user_posts("1263638109", page=2)
    assert len(result["statuses"]) == 2


def test_login_cookie_check_does_not_expose_or_accept_partial_state():
    assert not logged_in_cookie_names([{"name": "xq_a_token", "value": "secret"}])
    assert logged_in_cookie_names([
        {"name": "xq_a_token", "value": "private"},
        {"name": "u", "value": "123"},
    ])
    assert not logged_in_cookie_names([
        {"name": "xq_a_token", "value": ""},
        {"name": "u", "value": "123"},
    ])


def test_page2_verify_distinguishes_empty_blocked_from_success():
    assert not timeline_result('{"error_code":"10022","error_description":"请登录"}')[0]
    assert not timeline_result('{"statuses":[]}')[0]
    assert not timeline_result("WAF sliding captcha")[0]
    assert timeline_result('{"statuses":[{"id":123}]}')[0]
