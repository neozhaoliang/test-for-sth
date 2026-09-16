# -*- coding: utf-8 -*-
"""
阿里云 WAF 滑块验证处理工具。

xueqiu.com 的用户主页等页面受阿里云 WAF 保护, 会弹出
"请按住滑块，拖动到最右边" 的滑块验证页。

策略:
  1. 自动尝试拟人轨迹拖动若干次 (成功则继续);
  2. 失败后提示用户在有头浏览器窗口中手动拖动 (等待验证通过)。

强制有头浏览器模式下窗口始终可见, 人工兜底最可靠。
"""

import asyncio
import random
from typing import List, TYPE_CHECKING

from tools import utils

if TYPE_CHECKING:
    from playwright.async_api import Page


SLIDER_SELECTOR = "#aliyunCaptcha-sliding-slider"
TRACK_SELECTOR = "#aliyunCaptcha-sliding-body"


async def is_waf_challenge(page: "Page") -> bool:
    """判断当前页面是否为阿里云 WAF 验证页。"""
    try:
        if await page.locator(SLIDER_SELECTOR).count() > 0:
            return True
        title = await page.title()
        return "滑动验证" in title
    except Exception:
        return False


def _human_track(distance: int) -> List[int]:
    """
    生成拟人拖动轨迹 (每步 x 增量)。

    参考人工成功轨迹特征:
      - 前段爆发 5-8 步大步长接近目标
      - 后段 2-9px 微调精确对准
      - 末段小幅回退再前进 (人类拖过头修正)
    """
    burst_frac = random.uniform(0.4, 0.6)
    burst_dist = distance * burst_frac
    n_burst = random.randint(5, 8)
    burst_steps = []
    remaining = burst_dist
    for i in range(n_burst):
        if i == n_burst - 1:
            step = remaining
        else:
            step = remaining * random.uniform(0.1, 0.25)
        burst_steps.append(max(2.0, step))
        remaining -= step

    fine_dist = distance - sum(burst_steps)
    fine_steps = []
    remaining = fine_dist
    guard = 0
    while remaining > 1 and guard < 100:
        step = min(remaining, random.uniform(2, 9))
        fine_steps.append(step)
        remaining -= step
        guard += 1

    return burst_steps + fine_steps


async def _read_fail_tip(page: "Page") -> str:
    """读取滑块失败提示文本 (用于判断失败原因)。"""
    try:
        tip = page.locator("#aliyunCaptcha-sliding-failTip").first
        if await tip.count() > 0:
            return (await tip.inner_text() or "").strip()[:100]
    except Exception:
        pass
    return ""


async def _refresh_captcha(page: "Page") -> None:
    """滑块失败后尝试刷新验证码 (点击刷新按钮/链接)。"""
    for sel in (
        "#aliyunCaptcha-sliding-refresh-btn",
        "[id*='refresh']",
        "[class*='refresh']",
        "text=点击刷新",
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(timeout=3000)
                utils.logger.info(f"[WafSlider] 已刷新验证码 ({sel})")
                await asyncio.sleep(2)
                return
        except Exception:
            continue


async def _drag_once(page: "Page", variant: int = 0) -> bool:
    """
    执行一次拟人拖动, 返回验证是否通过。

    variant 控制拖动风格:
      0 = 爆发+微调 (标准)
      1 = 慢速均匀+更多抖动 (备选)
    """
    slider = page.locator(SLIDER_SELECTOR).first
    try:
        box = await slider.bounding_box()
        track = page.locator(TRACK_SELECTOR).first
        tbox = await track.bounding_box()
        if not box or not tbox:
            return False
    except Exception:
        return False

    distance = tbox["width"] - box["width"] - 4
    if distance < 20:
        return False

    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    track_steps = _human_track(distance)

    await page.mouse.move(start_x, start_y)
    await page.wait_for_timeout(random.randint(200, 600))
    await page.mouse.down()
    await page.wait_for_timeout(random.randint(30, 80))

    current_x = start_x
    y = start_y
    if variant == 0:
        # 标准: 爆发接近 + 微调, 9-18ms 采样, 偶发停顿
        for step in track_steps:
            current_x += step
            y += random.choice([-2, -1, 0, 0, 0, 1, 2])
            await page.mouse.move(current_x, y, steps=1)
            if random.random() < 0.15:
                await page.wait_for_timeout(random.randint(30, 80))
            else:
                await page.wait_for_timeout(random.randint(9, 18))
    else:
        # 备选: 慢速更细粒度 (20-40ms 采样, 更多 y 抖动), 总时长约 2-3s
        for step in track_steps:
            current_x += step
            y += random.choice([-3, -2, -1, 0, 0, 0, 1, 2, 3])
            await page.mouse.move(current_x, y, steps=1)
            if random.random() < 0.2:
                await page.wait_for_timeout(random.randint(60, 150))
            else:
                await page.wait_for_timeout(random.randint(20, 40))

    # 到末端后停留片刻再松手 (对齐人工习惯)
    await page.mouse.move(start_x + distance, y, steps=2)
    await page.wait_for_timeout(random.randint(150, 350))
    await page.mouse.up()
    await asyncio.sleep(3)
    return not await is_waf_challenge(page)


async def solve_waf_slider(
    page: "Page",
    max_auto_attempts: int = 4,
    manual_timeout_s: int = 600,
) -> bool:
    """
    处理 WAF 滑块验证: 先自动多次尝试 (交替拖动风格+失败刷新), 全部失败后提示用户手动拖动。

    Args:
        page: 当前页面
        max_auto_attempts: 自动拖动最大尝试次数
        manual_timeout_s: 等待人工验证的超时时间 (秒)

    Returns:
        验证是否通过
    """
    if not await is_waf_challenge(page):
        return True

    for attempt in range(1, max_auto_attempts + 1):
        variant = (attempt - 1) % 2
        style = "标准风格" if variant == 0 else "慢速风格"
        utils.logger.info(f"[WafSlider] 自动拖动滑块, 第 {attempt}/{max_auto_attempts} 次尝试 ({style})")
        if await _drag_once(page, variant=variant):
            utils.logger.info("[WafSlider] 滑块自动验证通过")
            return True

        fail_tip = await _read_fail_tip(page)
        if fail_tip:
            utils.logger.warning(f"[WafSlider] 失败提示: {fail_tip}")
        await _refresh_captcha(page)
        # 尝试间隔逐渐拉长, 给服务端风控窗口留冷却时间
        wait = min(5 * attempt, 30)
        await asyncio.sleep(wait)

    utils.logger.warning("=" * 60)
    utils.logger.warning("[WafSlider] 自动拖动失败, 需要人工验证!")
    utils.logger.warning("[WafSlider] 浏览器窗口为有头模式, 已弹出在屏幕上")
    utils.logger.warning("[WafSlider] 请手动拖动滑块到最右边, 完成后程序自动继续")
    utils.logger.warning(f"[WafSlider] 等待人工验证, 超时时间 {manual_timeout_s} 秒")
    utils.logger.warning("=" * 60)

    waited = 0
    while waited < manual_timeout_s:
        if not await is_waf_challenge(page):
            utils.logger.info("[WafSlider] 人工验证通过, 继续爬取")
            return True
        if waited % 60 == 0 and waited > 0:
            utils.logger.warning(f"[WafSlider] 仍等待人工拖动滑块... ({waited}s / {manual_timeout_s}s)")
        await asyncio.sleep(5)
        waited += 5

    utils.logger.error("[WafSlider] 等待人工验证超时")
    return False


async def ensure_page_accessible(page: "Page", timeout_s: int = 600) -> bool:
    """
    确保当前页面可正常访问 (非 WAF 验证页)。

    供页面导航后调用: 若页面被 WAF 拦截, 处理滑块直至通过。
    """
    if not await is_waf_challenge(page):
        return True
    return await solve_waf_slider(page, manual_timeout_s=timeout_s)
