# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
雪球 DOM 提取工具: 时间线卡片 / 回复列表的提取 + 拟人节奏滚动。

API 直连 (user_timeline.json 等) 会被阿里云 WAF 识别为爬虫行为并拦截，
而真实页面导航本身不受影响——改为导航到页面后滚动触发 SPA 懒加载，只读
渲染好的 DOM。注意 SPA 懒加载在滚动若干轮后可能自行停止 (前端状态提前
判定"到底")，因此 DOM 路径拿到的是"SPA 愿意渲染的部分"，不保证与接口
全量一致，调用方日志会如实报告条数。

卡片 DOM 结构 (2026-09 实测，发帖与回复共用同一组件):
  article.timeline__item
    div.timeline__item__info > a.date-and-source   (href=/<uid>/<status_id>, 时间+来源)
    div.timeline__item__content .content          (正文)
    blockquote.timeline__item__forward             (转发卡片才有: 被转发内容)
      a.fake-anchor[data-id]                       (回复场景: 评论 ID)
      .user-name                                  (被回复用户)
      .content                                    (被回复原文)
      a.replay-count                              (" · 讨论 N")
"""

import asyncio
import random
from typing import Dict, List

from playwright.async_api import Page

from model.m_xueqiu import XueqiuStatus

TIMELINE_ITEM_JS = """() => {
    // 时间线卡片的时间是相对文本 ("昨天 15:32"/"N分钟前")，用页面时钟折算成
    // 毫秒时间戳。天级精度对回测窗口 (以月计) 足够；"刚刚"等无法折算的返回 0。
    function parseTimeText(t) {
        const now = new Date();
        let m = t.match(/^(\\d{4})-(\\d{2})-(\\d{2})/);
        if (m) return new Date(m[1], m[2] - 1, m[3]).getTime();
        m = t.match(/^(\\d{2})-(\\d{2})\\s+(\\d{2}):(\\d{2})/);
        if (m) return new Date(now.getFullYear(), m[1] - 1, m[2], m[3], m[4]).getTime();
        m = t.match(/昨天\\s*(\\d{2}):(\\d{2})/);
        if (m) { const d = new Date(now); d.setDate(d.getDate() - 1); d.setHours(m[1], m[2], 0, 0); return d.getTime(); }
        m = t.match(/(\\d+)\\s*天前/);
        if (m) { const d = new Date(now); d.setDate(d.getDate() - Number(m[1])); d.setHours(12, 0, 0, 0); return d.getTime(); }
        m = t.match(/(\\d+)\\s*小时前/);
        if (m) return now.getTime() - Number(m[1]) * 3600000;
        m = t.match(/(\\d+)\\s*分钟前/);
        if (m) return now.getTime() - Number(m[1]) * 60000;
        return 0;
    }
    const out = [];
    const seen = new Set();
    for (const el of document.querySelectorAll('article.timeline__item')) {
        const dateLink = el.querySelector('a.date-and-source');
        const href = dateLink ? dateLink.getAttribute('href') : '';
        const m = href ? href.match(/\\/(\\d+)\\/(\\d+)/) : null;
        const sid = m ? m[2] : '';
        if (!sid || seen.has(sid)) continue;
        seen.add(sid);
        const contentEl = el.querySelector('.timeline__item__content .content, .timeline__item__content');
        const forwardEl = el.querySelector('blockquote.timeline__item__forward, .timeline__item__forward');
        let text = contentEl ? contentEl.innerText.trim() : '';
        if (forwardEl) {
            const fwd = (forwardEl.innerText || '').trim();
            text = text ? text + '\\n//转发:\\n' + fwd : fwd;
        }
        const dateText = dateLink ? (dateLink.innerText || '').trim() : '';
        out.push({
            id: sid,
            status_type: forwardEl ? 'repost' : 'original',
            description: text,
            created_at: parseTimeText(dateText),
            created_at_text: dateText,
        });
    }
    return out;
}"""

COMMENT_ITEM_JS = """() => {
    const out = [];
    for (const el of document.querySelectorAll('article.timeline__item')) {
        const dateLink = el.querySelector('a.date-and-source');
        const href = dateLink ? dateLink.getAttribute('href') : '';
        const m = href ? href.match(/\\/(\\d+)\\/(\\d+)/) : null;
        const contentEl = el.querySelector('.timeline__item__content .content, .timeline__item__content');
        const anchor = el.querySelector('.timeline__item__forward a.fake-anchor, blockquote a.fake-anchor');
        const replyCountEl = el.querySelector('a.replay-count');
        const replyCount = replyCountEl ? (replyCountEl.innerText.match(/\\d+/) || ['0'])[0] : '0';
        out.push({
            comment_id: anchor ? String(anchor.getAttribute('data-id')) : '',
            content: contentEl ? contentEl.innerText.trim() : '',
            publish_text: dateLink ? dateLink.innerText.trim() : '',
            status_url: m ? 'https://xueqiu.com' + href.split('#')[0] : '',
            reply_count: parseInt(replyCount || '0', 10),
        });
    }
    return out;
}"""


async def wait_for_items(page: Page, selector: str, timeout_s: int = 20) -> int:
    """等待 SPA 渲染出卡片，返回条目数 (超时返回 0)。"""
    for _ in range(timeout_s):
        try:
            count = await page.evaluate(
                f"() => document.querySelectorAll({selector!r}).length"
            )
        except Exception:
            count = 0
        if count > 0:
            return count
        await asyncio.sleep(1)
    return 0


async def scroll_until_stable(
    page: Page, selector: str, max_rounds: int = 50, no_growth_stop: int = 3
) -> int:
    """
    拟人节奏滚动触发懒加载 (步长与间隔都带抖动)，直到条目数连续
    no_growth_stop 轮不再增长或达到 max_rounds。返回最终条目数。
    """
    try:
        prev = await page.evaluate(f"() => document.querySelectorAll({selector!r}).length")
    except Exception:
        prev = 0
    no_growth = 0
    for _ in range(max_rounds):
        try:
            await page.evaluate("() => window.scrollBy(0, 500 + Math.random() * 600)")
        except Exception:
            break
        await asyncio.sleep(0.8 + random.random() * 1.2)
        try:
            cur = await page.evaluate(
                f"() => document.querySelectorAll({selector!r}).length"
            )
        except Exception:
            break
        if cur == prev:
            no_growth += 1
            if no_growth >= no_growth_stop:
                break
        else:
            no_growth = 0
            prev = cur
    return prev


async def extract_timeline_items(page: Page) -> List[Dict]:
    """提取当前页面渲染出的全部时间线帖子 (id/status_type/description/created_at)。"""
    try:
        return await page.evaluate(TIMELINE_ITEM_JS)
    except Exception:
        return []


async def extract_comment_items(page: Page) -> List[Dict]:
    """提取当前页面渲染出的全部回复 (comment_id/content/status_url/reply_count)。"""
    try:
        return await page.evaluate(COMMENT_ITEM_JS)
    except Exception:
        return []


def item_to_status(item: Dict, user_id: str) -> XueqiuStatus:
    """把 DOM 时间线条目转成 XueqiuStatus (DOM 拿不到的字段留空)。"""
    sid = str(item.get("id") or "")
    return XueqiuStatus(
        status_id=sid,
        status_type="retweet" if item.get("status_type") == "repost" else "original",
        description=item.get("description", "") or "",
        created_at=int(item.get("created_at") or 0),
        status_url=f"https://xueqiu.com/{user_id}/{sid}" if sid else "",
        user_id=user_id,
        user_link=f"https://xueqiu.com/{user_id}",
    )
