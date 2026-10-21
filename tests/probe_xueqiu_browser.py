#!/usr/bin/env python3
"""One-page, no-credentials Linux browser probe of PUBLIC Xueqiu content.

Designed for CI on Linux. Does not solve captchas or store/share cookie values.
A working browser does NOT count as a successful scrape: we check public
timeline status IDs or a JSON response with at least one status.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from playwright.async_api import async_playwright

USER = "1263638109"
PROFILE = f"https://xueqiu.com/u/{USER}"
TIMELINE = (
    "https://xueqiu.com/v4/statuses/user_timeline.json"
    f"?user_id={USER}&page=1&page_size=5"
)


def diagnostics(html: str) -> dict:
    lowered = html.lower()
    return {
        "html_bytes": len(html),
        "aliyun_challenge": "aliyun_waf" in lowered or "_waf_" in lowered,
        "slider_detected": "aliyuncaptcha-sliding-slider" in lowered or "滑动验证" in html,
        "login_prompt": "立即登录" in html or "请登录" in html,
    }


async def run(mode: str) -> dict:
    output: dict = {"mode": mode, "test_user_id": USER, "success": False}
    started = time.monotonic()
    async with async_playwright() as playwright:
        with tempfile.TemporaryDirectory(prefix="xueqiu-probe-") as tmp:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=tmp,
                headless=(mode == "headless"),
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
            )
            try:
                stealth = Path("libs/stealth.min.js")
                if stealth.is_file():
                    await context.add_init_script(path=str(stealth))
                page = context.pages[0] if context.pages else await context.new_page()
                for phase, url in (("homepage", "https://xueqiu.com"), ("profile", PROFILE)):
                    try:
                        resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                        output[phase + "_http"] = resp.status if resp else None
                        await page.wait_for_timeout(5000)
                        output[phase + "_url"] = page.url.split("?")[0][:180]
                        html = await page.content()
                        output[phase] = diagnostics(html)
                        if phase == "profile":
                            output["timeline_dom_posts"] = await page.locator("article.timeline__item").count()
                            output["timeline_post_ids"] = await page.locator(
                                "article.timeline__item a.date-and-source[href]"
                            ).count()
                    except Exception as exc:
                        output[phase + "_error"] = type(exc).__name__ + ": " + str(exc)[:180]
                try:
                    resp = await page.goto(TIMELINE, wait_until="domcontentloaded", timeout=25000)
                    output["api_http"] = resp.status if resp else None
                    await page.wait_for_timeout(4000)
                    body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    output["api"] = diagnostics(body)
                    try:
                        payload = json.loads(body.strip())
                        entries = payload.get("statuses") or payload.get("list") or []
                        output["api_post_count"] = len(entries)
                        output["api_error_code"] = payload.get("error_code")
                        output["api_error_description"] = str(payload.get("error_description") or "")[:150]
                    except (json.JSONDecodeError, ValueError, TypeError):
                        output["api_non_json"] = True
                except Exception as exc:
                    output["api_error"] = type(exc).__name__ + ": " + str(exc)[:180]
                # A full-history crawler must advance beyond the first page.
                try:
                    page2 = TIMELINE.replace("page=1", "page=2")
                    resp = await page.goto(page2, wait_until="domcontentloaded", timeout=25000)
                    output["page2_http"] = resp.status if resp else None
                    await page.wait_for_timeout(5000)
                    body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    output["page2"] = diagnostics(body)
                    try:
                        payload2 = json.loads(body.strip())
                        entries2 = payload2.get("statuses") or payload2.get("list") or []
                        output["page2_post_count"] = len(entries2)
                        output["page2_error_code"] = payload2.get("error_code")
                    except (json.JSONDecodeError, ValueError, TypeError):
                        output["page2_non_json"] = True
                except Exception as exc:
                    output["page2_error"] = type(exc).__name__ + ": " + str(exc)[:180]
            finally:
                await context.close()
    output["success"] = bool(output.get("api_post_count", 0) or output.get("timeline_post_ids", 0))
    output["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("headless", "headed"), required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.mode))
    except Exception as exc:
        result = {"mode": args.mode, "success": False, "error": type(exc).__name__ + ": " + str(exc)[:300]}
    os.makedirs("artifacts", exist_ok=True)
    with open(f"artifacts/xueqiu_{args.mode}.json", "w", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print("XUEQIU_BROWSER_PROBE=" + json.dumps(result, ensure_ascii=False))
