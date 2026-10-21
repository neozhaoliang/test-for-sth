#!/usr/bin/env python3
"""Interactive one-time Xueqiu login in Docker's Xvfb Chromium.

Use noVNC over SSH tunnel at http://127.0.0.1:6080/vnc.html.
The persistent browser profile stays on /app/browser_data (a Docker volume).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import time

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "browser_data" / "xueqiu_user_data_dir"


def logged_in_cookie_names(cookies: list[dict]) -> bool:
    names = {str(c.get("name")) for c in cookies if c.get("value")}
    return {"xq_a_token", "u"}.issubset(names)


def timeline_result(text: str) -> tuple[bool, str]:
    """An HTTP 200 is not proof of login; require real statuses on page 2."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return False, "接口未返回 JSON；可能遇到 WAF/滑块"
    if not isinstance(data, dict):
        return False, "接口响应不是对象"
    code = data.get("error_code")
    if code not in (None, "", 0, "0"):
        return False, f"error_code={code}，请检查雪球登录状态与网页验证"
    posts = data.get("statuses") or data.get("list") or []
    if not isinstance(posts, list) or not posts:
        return False, "第 2 页没有帖子；不能确认分页权限"
    if not all(isinstance(item, dict) and item.get("id") for item in posts):
        return False, "帖子缺少 id，接口内容异常"
    return True, f"第 2 页返回 {len(posts)} 条帖子，登录后翻页接口可用"


async def check_page_two(page, user_id: str) -> tuple[bool, str]:
    url = (f"https://xueqiu.com/v4/statuses/user_timeline.json"
           f"?user_id={user_id}&page=2&page_size=20")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        return timeline_result(body)
    except Exception as exc:
        return False, f"接口访问失败：{type(exc).__name__}: {str(exc)[:180]}"


async def login(user_id: str, timeout_s: int) -> int:
    if not os.environ.get("DISPLAY"):
        print("缺少 DISPLAY；请通过容器入口启动 Xvfb。", flush=True)
        return 2
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE.chmod(0o700)
    print(
        "\nSSH 隧道：http://127.0.0.1:6080/vnc.html\n"
        "请在远程浏览器里登录雪球；本程序不输出 Cookie 或登录密码。\n"
        "登录后会实际访问发帖接口第 2 页进行验证。\n"
        "注意：人工登录时不要同时执行雪球爬虫（它们共享同一个浏览器用户目录）。\n",
        flush=True,
    )
    async with async_playwright() as playwright:
        browser_proxy = os.environ.get("XUEQIU_BROWSER_PROXY", "").strip()
        context = await playwright.chromium.launch_persistent_context(
            proxy={"server": browser_proxy} if browser_proxy else None,
            user_data_dir=str(PROFILE),
            executable_path=playwright.chromium.executable_path,
            headless=False, viewport={"width": 1440, "height": 900}, locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(
                    "https://xueqiu.com/login",
                    wait_until="domcontentloaded", timeout=25000,
                )
            except Exception as exc:
                print(f"登录页未完成加载，可在 VNC 中手动打开：{type(exc).__name__}", flush=True)
            expires = time.monotonic() + timeout_s
            last_status = None
            while time.monotonic() < expires:
                cookies = await context.cookies("https://xueqiu.com/")
                if logged_in_cookie_names(cookies):
                    ok, msg = await check_page_two(page, user_id)
                    if msg != last_status:
                        print(msg, flush=True)
                        last_status = msg
                    if ok:
                        return 0
                    # Allow manual CAPTCHA/verification through noVNC.
                    await asyncio.sleep(8)
                else:
                    await asyncio.sleep(3)
            print("登录或翻页验证超时；无法确认全量抓取权限。", flush=True)
            return 1
        finally:
            await context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", default="1263638109",
                        help="检查第2页权限的雪球用户ID")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if not args.user_id.isdecimal() or len(args.user_id) > 24:
        parser.error("--user-id 必须是数字用户ID")
    if not 30 <= args.timeout <= 3600:
        parser.error("--timeout 必须在30～3600秒之间")
    raise SystemExit(asyncio.run(login(args.user_id, args.timeout)))
