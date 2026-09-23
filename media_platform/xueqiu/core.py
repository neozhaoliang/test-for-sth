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


import asyncio
import glob
import hashlib
import json
import os
import pathlib
import re
import time
from typing import Dict, List, Optional

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from model.m_xueqiu import XueqiuComment, XueqiuCreator, XueqiuStatus
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import xueqiu as xueqiu_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from tools.waf_slider import is_waf_challenge, solve_waf_slider
from var import crawler_type_var

from . import discover, dom as xueqiu_dom
from .client import XueqiuClient
from .exception import CrawlInterruptedError, DataFetchError, WafChallengeError
from .help import (
    extract_creator_from_user_obj,
    extract_status_list,
    normalize_user_id,
)
from .login import XueqiuLogin


class XueqiuCrawler(AbstractCrawler):
    context_page: Page
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://xueqiu.com"
        self.cookie_urls = [self.index_url]
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        self.cdp_manager = None
        self.ip_proxy_pool = None
        self._last_status_user_obj: Optional[Dict] = None

    async def start(self) -> None:
        """
        Start the crawler
        Returns:

        """
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            self.ip_proxy_pool = await create_ip_pool(
                config.IP_PROXY_POOL_COUNT, enable_validate_ip=True
            )
            ip_proxy_info: IpInfoModel = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(
                ip_proxy_info
            )

        async with async_playwright() as playwright:
            # Choose launch mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[XueqiuCrawler] Launching browser in CDP mode (headed)")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    self.user_agent,
                    headless=False,
                )
            else:
                utils.logger.info("[XueqiuCrawler] Launching browser in standard mode (headed)")
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    self.user_agent,
                    headless=False,
                )

            # 反检测: 隐藏 webdriver/CDP 痕迹——阿里云滑块会拒绝"自动化软件控制的
            # 浏览器", 不注入 stealth 时连人工拖动都会被判失败
            if os.path.exists("libs/stealth.min.js"):
                await self.browser_context.add_init_script(path="libs/stealth.min.js")
                utils.logger.info("[XueqiuCrawler] 已注入 stealth 反检测脚本")

            self.context_page = await self.browser_context.new_page()

            # 访问首页: WAF JS 挑战会自动在真实浏览器中解析通过
            await self._goto_with_waf(self.index_url, what="雪球首页")

            # 等待手动登录: 登录后的会话 WAF 信任度更高, 接口可以翻页;
            # 未登录时接口大概率被拦 (此时会降级为 DOM 首屏抓取)。
            await self._wait_for_xueqiu_login()

            # 登录 (可选): 爬取公开数据无需登录; 配置了 cookie 时写入浏览器
            if config.COOKIES:
                login_obj = XueqiuLogin(
                    login_type="cookie",
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "creator":
                # 输入用户 ID, 爬取其全部发帖和回复
                await self.get_creators_and_notes()
            elif config.CRAWLER_TYPE == "search":
                await self.search()
            else:
                utils.logger.error(f"[XueqiuCrawler.start] Unsupported crawler type: {config.CRAWLER_TYPE}")

            utils.logger.info("[XueqiuCrawler.start] Xueqiu Crawler finished ...")

    async def _recreate_page_if_needed(self) -> bool:
        """
        标签页被 WAF 挑战流程关闭/替换时, 重新开一个新页面。
        """
        try:
            if self.context_page and not self.context_page.is_closed():
                return True
        except Exception:
            pass
        try:
            self.context_page = await self.browser_context.new_page()
            utils.logger.info("[XueqiuCrawler] 标签页已关闭, 已重新打开新页面")
            return True
        except Exception as e:
            utils.logger.error(f"[XueqiuCrawler] 重新打开页面失败: {e}")
            return False

    async def _is_xueqiu_logged_in(self) -> bool:
        """通过雪球登录 cookie (xq_a_token) 判断当前会话是否已登录。"""
        try:
            cookies = await self.browser_context.cookies("https://xueqiu.com/")
        except Exception:
            return False
        return any(c.get("name") == "xq_a_token" and c.get("value") for c in cookies)

    async def _wait_for_xueqiu_login(self) -> None:
        """
        未登录时停在雪球首页等待用户在浏览器里手动登录 (不做滑块验证)。
        登录后的会话 WAF 信任度更高, 接口可以正常分页; 超时后继续, 接口
        被拦时发帖抓取会降级为 DOM 首屏路径。
        """
        if not getattr(config, "CDP_WAIT_FOR_LOGIN", True):
            return
        timeout = int(getattr(config, "CDP_LOGIN_WAIT_SECONDS", 120) or 0)
        if timeout <= 0:
            return
        if await self._is_xueqiu_logged_in():
            utils.logger.info("[XueqiuCrawler] 雪球已登录")
            return
        utils.logger.info(
            f"[XueqiuCrawler] 雪球未登录，等待手动登录 (最多 {timeout}s): "
            "请在弹出的浏览器里完成雪球登录，登录成功后自动继续"
        )
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            await asyncio.sleep(3)
            if await self._is_xueqiu_logged_in():
                utils.logger.info("[XueqiuCrawler] 雪球登录成功，继续抓取")
                return
        utils.logger.warning(
            "[XueqiuCrawler] 等待登录超时，以未登录状态继续 (接口可能被 WAF 拦截, 发帖将降级 DOM)"
        )

    async def _goto_with_waf(self, url: str, what: str = "", timeout_s: int = 600) -> bool:
        """
        导航到指定 URL 并处理阿里云 WAF:
          - JS 挑战页 (非交互) 会在真实浏览器中自动解析并重载;
          - 滑块挑战 (交互) 先自动拖动, 失败则等待人工拖动;
          - 标签页被关闭时自动开新页重试。
        """
        utils.logger.info(f"[XueqiuCrawler] 访问 {what}: {url}")
        if not await self._recreate_page_if_needed():
            return False
        try:
            await self.context_page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            utils.logger.warning(f"[XueqiuCrawler] goto {url} 异常: {e}")

        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if not await self._recreate_page_if_needed():
                return False
            try:
                if await is_waf_challenge(self.context_page):
                    await solve_waf_slider(self.context_page, manual_timeout_s=timeout_s)
                    continue
                # wait_for 兜底: 防止页面卡死导致 content() 无限等待
                html = await asyncio.wait_for(self.context_page.content(), timeout=30)
            except asyncio.TimeoutError:
                utils.logger.warning("[XueqiuCrawler] content() 超时, 重新开页导航")
                if not await self._recreate_page_if_needed():
                    return False
                try:
                    await self.context_page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as goto_exc:
                    utils.logger.warning(f"[XueqiuCrawler] goto {url} 重试异常: {goto_exc}")
                await asyncio.sleep(2)
                continue
            except Exception as e:
                # 页面在等待期间被关闭: 开新页重新导航后继续等待
                utils.logger.warning(f"[XueqiuCrawler] 页面访问异常 ({e}), 重新开页导航")
                if not await self._recreate_page_if_needed():
                    return False
                try:
                    await self.context_page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as goto_exc:
                    utils.logger.warning(f"[XueqiuCrawler] goto {url} 重试异常: {goto_exc}")
                await asyncio.sleep(2)
                continue
            head = html[:5000]
            # WAF JS 挑战页特征: renderData 里带 _waf_ token; 等待其自动解析重载
            if "_waf_" in head or "aliyun_waf" in head:
                await asyncio.sleep(2)
                continue
            if len(html) > 50000:
                utils.logger.info(f"[XueqiuCrawler] {what} 加载完成 ({len(html)} bytes)")
                return True
            await asyncio.sleep(2)
        utils.logger.error(f"[XueqiuCrawler] 访问 {what} 超时")
        return False

    async def search(self) -> None:
        """搜索模式 (雪球平台暂只支持 creator 模式)"""
        utils.logger.warning("[XueqiuCrawler.search] 雪球平台当前仅支持 creator 模式 (输入用户 ID 爬发帖+回复)")

    async def get_creators_and_notes(self) -> None:
        """
        获取用户信息、其全部发帖和回复。
        若开启用户发现 (--discover_fans), 则在指定用户爬完后
        检索粉丝数达标的用户并逐一抓取。
        """
        utils.logger.info("[XueqiuCrawler.get_creators_and_notes] Begin get xueqiu creators")
        for raw_user_id in config.XUEQIU_CREATOR_ID_LIST:
            user_id = normalize_user_id(raw_user_id)
            await self.crawl_single_user(user_id)

        # 用户发现: 检索粉丝数 >= 阈值的用户并抓取
        if getattr(config, "XUEQIU_DISCOVER_FANS", 0) > 0:
            await self.discover_and_crawl(
                min_followers=config.XUEQIU_DISCOVER_FANS,
                max_users=getattr(config, "XUEQIU_DISCOVER_MAX_USERS", 0),
            )

    async def crawl_single_user(self, user_id: str) -> None:
        """
        爬取单个用户的全部发帖和回复 (含 WAF 处理与断点续爬)。
        """
        utils.logger.info(f"[XueqiuCrawler] 开始爬取用户 {user_id}")

        # 1. 打开用户主页 (处理 WAF 滑块)
        ok = await self._goto_with_waf(
            f"{self.index_url}/u/{user_id}",
            what=f"用户 {user_id} 主页",
        )
        if not ok:
            utils.logger.error(f"[XueqiuCrawler] 用户 {user_id} 主页访问失败, 跳过")
            return

        # 顺带收集主页 "用户推荐" (用户发现候选)
        await self._collect_profile_recommendations(user_id)

        # 2. 抓取发帖
        if config.XUEQIU_UPDATE_MODE:
            # 增量更新: 打开主页滚动加载时间线, 只存已知 ID 之外的新帖 (帖子按时间倒序)
            await self.get_creator_posts_update(user_id)
        else:
            # 全量抓取: 接口优先 (登录后 WAF 放行, 可翻全部历史);
            # 接口被拦时降级 DOM 首屏 (约 20 条), 不标记完成, 下次运行重试接口。
            resume_state = self._load_resume_state(user_id)
            if resume_state.get("posts_done"):
                utils.logger.info(
                    f"[XueqiuCrawler] 用户 {user_id} 发帖此前已完整抓取, 跳过 "
                    f"(如需重新抓取, 删除 data/xueqiu/resume/resume_{user_id}.json)"
                )
            else:
                try:
                    await self._crawl_user_posts_api(user_id, resume_state)
                except CrawlInterruptedError as e:
                    utils.logger.warning(
                        f"[XueqiuCrawler] 接口全量抓取被 WAF 中断 (位置 {e.page}), "
                        f"降级为 DOM 首屏路径, 下次运行将重试接口"
                    )
                    await self._crawl_user_posts_dom(user_id, resume_state, mark_done=False)

        # 3. 用户信息: 优先用已存帖子内嵌的 user 对象, 失败再用主页 DOM
        creator: Optional[XueqiuCreator] = None
        if self._last_status_user_obj:
            creator = extract_creator_from_user_obj(self._last_status_user_obj, user_id)
        if not creator:
            creator = await self._extract_creator_from_profile(user_id)
        if creator:
            utils.logger.info(f"[XueqiuCrawler] Creator info: {creator}")
            await xueqiu_store.save_creator(creator=creator)

        # 4. 抓取全部回复 (用户主页 "回复" tab)
        await self._crawl_user_replies(user_id)

    async def discover_and_crawl(self, min_followers: int = 1000, max_users: int = 0) -> None:
        """
        用户发现 + 抓取:
          1. 从已爬帖子数据中检索粉丝数 >= min_followers 的用户;
          2. 对主页推荐候选补查粉丝数并过滤;
          3. 排除已抓取过的用户, 保存发现列表;
          4. 逐一抓取新发现用户 (max_users 可限制数量, 0 = 不限)。
        """
        utils.logger.info(f"[XueqiuCrawler] 开始用户发现 (粉丝数 >= {min_followers}) ...")

        # 1. 从数据中发现
        discovered: Dict[str, Dict] = {}
        for u in discover.discover_users_from_data(min_followers):
            discovered[u["user_id"]] = u

        # 2. 主页推荐候选补查粉丝数 (DOM: 打开候选主页读用户卡片, 不走接口)
        candidates = discover.load_candidates()
        if candidates:
            utils.logger.info(f"[XueqiuCrawler] 补查 {len(candidates)} 个推荐候选的粉丝数 ...")
            for cand in candidates:
                uid = str(cand.get("user_id") or "")
                if not uid or uid in discovered:
                    continue
                ok = await self._goto_with_waf(
                    f"{self.index_url}/u/{uid}", what=f"候选 {uid} 主页"
                )
                if ok:
                    creator = await self._extract_creator_from_profile(uid)
                    if creator and creator.followers_count >= min_followers:
                        d = discover._user_dict(
                            {
                                "id": uid,
                                "screen_name": creator.user_nickname,
                                "followers_count": creator.followers_count,
                                "status_count": creator.status_count,
                                "description": creator.description,
                            },
                            "recommend",
                        )
                        if d:
                            discovered[uid] = d
                            utils.logger.info(
                                f"[XueqiuCrawler] 推荐候选 {uid} ({d['screen_name']}) "
                                f"粉丝 {d['followers_count']} 达标"
                            )
                await asyncio.sleep(1.5)  # 候选查询轻量限速, 降低风控
        else:
            utils.logger.info("[XueqiuCrawler] 无主页推荐候选 (爬取更多用户后自动积累)")

        # 3. 排除已抓取过的用户 + 配置的排除名单
        exclude_ids = {
            x.strip() for x in (getattr(config, "XUEQIU_DISCOVER_EXCLUDE_IDS", "") or "").split(",")
            if x.strip()
        }
        crawled = discover.already_crawled_user_ids()
        new_users = [
            u for uid, u in discovered.items()
            if uid not in crawled and uid not in exclude_ids
        ]
        new_users.sort(key=lambda u: -u.get("followers_count", 0))
        utils.logger.info(
            f"[XueqiuCrawler] 发现用户 {len(discovered)} 个, 其中未抓取过的 {len(new_users)} 个"
        )

        if not new_users:
            utils.logger.info("[XueqiuCrawler] 没有新用户需要抓取")
            return

        discover.save_discovered(new_users)

        if max_users > 0 and len(new_users) > max_users:
            utils.logger.info(f"[XueqiuCrawler] 按 --discover_max_users={max_users} 截取")
            new_users = new_users[:max_users]

        for i, u in enumerate(new_users, 1):
            uid = u["user_id"]
            utils.logger.info(
                f"[XueqiuCrawler] 抓取发现用户 {i}/{len(new_users)}: "
                f"{uid} ({u.get('screen_name')}, 粉丝 {u.get('followers_count')})"
            )
            await self.crawl_single_user(uid)
        utils.logger.info("[XueqiuCrawler] 用户发现抓取完成")

    async def _collect_profile_recommendations(self, user_id: str) -> None:
        """
        从用户主页收集 "用户推荐" 列表 (只有 ID 和昵称, 无粉丝数),
        作为用户发现的候选池。
        """
        try:
            items = await self.context_page.evaluate(
                """() => {
                    const out = [];
                    // 用户推荐区域内的用户链接
                    const seen = new Set();
                    const links = document.querySelectorAll(
                        '[class*="recommend"] a[href], .recommend a[href], [class*="Recommend"] a[href]'
                    );
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/^\\/(\\d+)$/);
                        if (!m || seen.has(m[1])) continue;
                        seen.add(m[1]);
                        out.push({user_id: m[1], screen_name: (a.innerText || '').trim().slice(0, 30)});
                        if (out.length >= 20) break;
                    }
                    return out;
                }"""
            )
            if items:
                discover.save_candidates(items)
                utils.logger.info(f"[XueqiuCrawler] 收集主页推荐候选 {len(items)} 个")
        except Exception as e:
            utils.logger.warning(f"[XueqiuCrawler] 主页推荐收集失败: {e}")

    async def get_creator_posts_update(self, user_id: str) -> None:
        """
        增量更新用户发帖: 接口优先 (登录后 WAF 放行, 可翻全部新帖),
        接口被拦时降级 DOM 首屏 (约 20 条)。可反复执行 (幂等)。
        """
        known = self._load_known_status_ids(user_id)
        utils.logger.info(f"[XueqiuCrawler] 增量更新模式, 已有 {len(known)} 条历史帖子索引")
        try:
            new_total = await self._update_posts_api(user_id, known)
            utils.logger.info(f"[XueqiuCrawler] 增量更新 (接口路径) 新增 {new_total} 条")
            return
        except (WafChallengeError, DataFetchError) as e:
            utils.logger.warning(
                f"[XueqiuCrawler] 接口被 WAF 拦截 ({type(e).__name__}: {str(e)[:80]}), "
                f"降级为 DOM 首屏路径 (仅约 20 条)"
            )
            await self._update_posts_dom(user_id, known)

    async def _update_posts_api(self, user_id: str, known: set) -> int:
        """接口分页增量更新: 逐页拉取直到遇到已知帖子 (帖子按时间倒序)。"""
        client = XueqiuClient(playwright_page=self.context_page)
        new_total = 0
        page = 1
        while page <= 50:
            res = await client.get_user_posts(user_id, page=page)
            statuses = res.get("statuses") or []
            if not statuses:
                utils.logger.info(f"[XueqiuCrawler] 接口第 {page} 页无数据, 增量更新结束")
                break
            fresh = [s for s in statuses if str(s.get("id") or "") not in known]
            if fresh:
                models = extract_status_list(fresh)
                await xueqiu_store.batch_update_xueqiu_statuses(models)
                for s in fresh:
                    known.add(str(s.get("id") or ""))
                self._save_known_status_ids(user_id, known)
                new_total += len(fresh)
            if len(fresh) < len(statuses):
                utils.logger.info(f"[XueqiuCrawler] 接口第 {page} 页出现已存在帖子, 增量更新完成")
                break
            page += 1
        return new_total

    async def _update_posts_dom(self, user_id: str, known: set) -> None:
        """DOM 首屏增量更新 (接口被拦时的降级路径)。"""
        no_ts_ids = self._load_status_ids_without_timestamp(user_id)
        ok = await self._goto_with_waf(
            f"{self.index_url}/u/{user_id}",
            what=f"用户 {user_id} 主页 (增量更新)",
        )
        if not ok:
            utils.logger.error(f"[XueqiuCrawler] 用户 {user_id} 主页访问失败, 增量更新跳过")
            return
        await xueqiu_dom.wait_for_items(self.context_page, "article.timeline__item", timeout_s=20)
        await xueqiu_dom.scroll_until_stable(
            self.context_page, "article.timeline__item", max_rounds=30
        )
        items = await xueqiu_dom.extract_timeline_items(self.context_page)
        statuses = [xueqiu_dom.item_to_status(item, user_id) for item in items]
        fresh = [
            s for s in statuses
            if s.status_id and (s.status_id not in known or s.status_id in no_ts_ids)
        ]
        if fresh:
            await xueqiu_store.batch_update_xueqiu_statuses(fresh)
            for s in fresh:
                known.add(s.status_id)
            self._save_known_status_ids(user_id, known)
            utils.logger.info(
                f"[XueqiuCrawler] 增量更新新增 {len(fresh)} 条 (DOM 渲染 {len(items)} 条)"
            )
        else:
            utils.logger.info(
                f"[XueqiuCrawler] 增量更新无新增 (DOM 渲染 {len(items)} 条)"
            )

    async def _crawl_user_posts_api(self, user_id: str, resume_state: Dict) -> None:
        """接口全量抓发帖 (可翻全部历史), 完成时置 posts_done。
        WAF 拦截时 get_all_user_posts 抛 CrawlInterruptedError, 由调用方降级 DOM。"""
        client = XueqiuClient(playwright_page=self.context_page)
        await client.get_all_user_posts(
            user_id=user_id,
            callback=self._store_statuses_callback,
        )
        resume_state["posts_done"] = True
        self._save_resume_state(user_id, resume_state)
        utils.logger.info(f"[XueqiuCrawler] 用户 {user_id} 接口全量发帖抓取完成")

    async def _store_statuses_callback(self, statuses_json: List[Dict]):
        """timeline 每页回调: 解析并存储帖子"""
        statuses: List[XueqiuStatus] = extract_status_list(statuses_json)
        await xueqiu_store.batch_update_xueqiu_statuses(statuses)
        # 记录最后一个 user 对象用于用户信息提取
        for item in reversed(statuses_json or []):
            if item and item.get("user"):
                self._last_status_user_obj = item["user"]
                break
        utils.logger.info(f"[XueqiuCrawler] 已存储 {len(statuses)} 条发帖")

    async def _crawl_user_posts_dom(
        self, user_id: str, resume_state: Dict, mark_done: bool = True
    ) -> None:
        """
        全量抓发帖 (DOM 滚动降级路径): 打开主页, 滚动直到 SPA 停止懒加载, 提取并存储。
        SPA 懒加载可能提前停止, 因此拿到的是"DOM 可达的最近 N 条", 日志如实报告。
        mark_done=False 时 (接口被拦的降级场景) 不置 posts_done, 下次运行重试接口。
        """
        ok = await self._goto_with_waf(
            f"{self.index_url}/u/{user_id}", what=f"用户 {user_id} 主页"
        )
        if not ok:
            utils.logger.error(f"[XueqiuCrawler] 用户 {user_id} 主页访问失败, 发帖抓取跳过")
            return
        await xueqiu_dom.wait_for_items(self.context_page, "article.timeline__item", timeout_s=20)
        await xueqiu_dom.scroll_until_stable(
            self.context_page, "article.timeline__item", max_rounds=50
        )
        items = await xueqiu_dom.extract_timeline_items(self.context_page)
        known = self._load_known_status_ids(user_id)
        no_ts_ids = self._load_status_ids_without_timestamp(user_id)
        statuses = [xueqiu_dom.item_to_status(item, user_id) for item in items]
        fresh = [
            s for s in statuses
            if s.status_id and (s.status_id not in known or s.status_id in no_ts_ids)
        ]
        if fresh:
            await xueqiu_store.batch_update_xueqiu_statuses(fresh)
            for s in fresh:
                known.add(s.status_id)
            self._save_known_status_ids(user_id, known)
        if mark_done:
            resume_state["posts_done"] = True
            self._save_resume_state(user_id, resume_state)
        utils.logger.info(
            f"[XueqiuCrawler] 用户 {user_id} DOM 发帖抓取完成: 渲染 {len(items)} 条, "
            f"新增 {len(fresh)} 条 (受 SPA 懒加载限制, 不保证与接口全量一致; "
            f"posts_done={'已标记' if mark_done else '未标记, 下次重试接口'})"
        )

    def _load_known_status_ids(self, user_id: str) -> set:
        """
        加载该用户已存储的帖子 ID 索引 (用于增量更新边界判断)。

        优先读断点文件中的索引; 首次运行 (旧数据无索引) 时扫描已有 jsonl 建立。
        """
        state = self._load_resume_state(user_id)
        ids = set(state.get("known_ids") or [])
        if ids:
            return ids
        # 首次: 扫描已有数据文件建立索引
        base = os.path.join(config.SAVE_DATA_PATH or "data", "xueqiu", "jsonl")
        for path in glob.glob(os.path.join(base, f"creator_{user_id}_contents_*.jsonl")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except Exception:
                            continue
                        sid = item.get("status_id", "")
                        if sid:
                            ids.add(sid)
                utils.logger.info(f"[XueqiuCrawler] 已从 {os.path.basename(path)} 索引 {len(ids)} 条")
            except Exception as e:
                utils.logger.warning(f"[XueqiuCrawler] 扫描 {path} 失败: {e}")
        state["known_ids"] = sorted(ids)
        self._save_resume_state(user_id, state)
        return ids

    def _save_known_status_ids(self, user_id: str, ids: set) -> None:
        state = self._load_resume_state(user_id)
        state["known_ids"] = sorted(ids)
        self._save_resume_state(user_id, state)

    def _load_status_ids_without_timestamp(self, user_id: str) -> set:
        """
        存量数据里 created_at 为 0 的帖子 ID 集合 (DOM 路径早期版本没有解析
        发布时间)。再次抓取时这些帖子虽然已在 known_ids 里，但需要重存一次
        补上时间戳，否则回测会因缺少发布时间而跳过它们。
        """
        ids = set()
        base = os.path.join(config.SAVE_DATA_PATH or "data", "xueqiu", "jsonl")
        for path in glob.glob(os.path.join(base, f"creator_{user_id}_contents_*.jsonl")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except Exception:
                            continue
                        if int(item.get("created_at") or 0) <= 0 and item.get("status_id"):
                            ids.add(str(item.get("status_id")))
            except Exception as e:
                utils.logger.warning(f"[XueqiuCrawler] 扫描 {path} 失败: {e}")
        if ids:
            utils.logger.info(f"[XueqiuCrawler] 检测到 {len(ids)} 条存量帖子缺少发布时间, 本次重抓将补齐")
        return ids

    def _resume_file_path(self, user_id: str) -> str:
        base = config.SAVE_DATA_PATH or "data"
        return os.path.join(base, "xueqiu", "resume", f"resume_{user_id}.json")

    def _load_resume_state(self, user_id: str) -> Dict:
        path = self._resume_file_path(user_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"last_page": 1, "posts_done": False}

    def _save_resume_state(self, user_id: str, state: Dict) -> None:
        path = self._resume_file_path(user_id)
        pathlib.Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    async def _extract_creator_from_profile(self, user_id: str) -> Optional[XueqiuCreator]:
        """
        从用户主页 DOM 提取用户信息 (尽力而为, 取不到返回 None)。
        """
        utils.logger.info(f"[XueqiuCrawler] 从主页 DOM 提取用户 {user_id} 信息 ...")
        try:
            await asyncio.sleep(2)
            data = await self.context_page.evaluate(
                """() => {
                    const out = {nickname: '', avatar: '', description: '',
                                 followers: 0, friends: 0, status_count: 0};
                    // 用户信息卡片: .profiles__hd__info 首行是昵称
                    const infoEl = document.querySelector('.profiles__hd__info');
                    const text = infoEl ? infoEl.innerText : '';
                    const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
                    if (lines.length) out.nickname = lines[0];
                    // 统计: "43 关注 / 8626 粉丝 / 帖子 5773"
                    const mFollow = text.match(/([\\d.]+万?)\\s*关注/);
                    const mFans = text.match(/([\\d.]+万?)\\s*粉丝/);
                    const mPosts = text.match(/帖子\\s*\\n?\\s*([\\d.]+万?)/);
                    if (mFollow) out.friends = mFollow[1];
                    if (mFans) out.followers = mFans[1];
                    if (mPosts) out.status_count = mPosts[1];
                    // 简介: 粉丝行之后到 "||" 之前的最后一行非空文本
                    const introM = text.match(/粉丝\\s*\\n?\\s*(?:[^\\n]*\\n)?([\\s\\S]*?)\\n\\s*\\|\\|/);
                    if (introM) {
                        const introLines = introM[1].split('\\n').map(s => s.trim()).filter(Boolean);
                        out.description = introLines.length ? introLines[introLines.length - 1] : '';
                    }
                    // 头像
                    const avatarEl = document.querySelector('.profiles__hd img, .avatar img, img[class*="avatar"]');
                    if (avatarEl) out.avatar = avatarEl.src || '';
                    return out;
                }"""
            )
            if not data.get("nickname"):
                return None
            return XueqiuCreator(
                user_id=user_id,
                user_link=f"{self.index_url}/{user_id}",
                user_nickname=data["nickname"],
                user_avatar=data.get("avatar", "") or "",
                description=data.get("description", "") or "",
                followers_count=int(str(data.get("followers", 0)).replace("万", "0000") or 0),
                friends_count=int(str(data.get("friends", 0)).replace("万", "0000") or 0),
                status_count=int(str(data.get("status_count", 0)).replace("万", "0000") or 0),
            )
        except Exception as e:
            utils.logger.warning(f"[XueqiuCrawler] 主页 DOM 提取失败: {e}")
            return None

    async def _crawl_user_replies(self, user_id: str) -> None:
        """
        抓取用户的回复 (主页 "回复" tab, DOM 滚动路径, 不走接口)。

        接口 (statuses/user/comments.json) 被 WAF 识别为爬虫行为, 而页面导航
        不受影响; 改为点击 "回复" tab 后滚动加载并解析 DOM。受 SPA 懒加载提前
        停止限制, 拿到的是"DOM 可达的最近 N 条回复", 日志如实报告。
        """
        utils.logger.info(f"[XueqiuCrawler] 开始抓取用户 {user_id} 的回复 ...")
        page = self.context_page

        # 1. 切到 "回复" tab (a.tab-comments, SPA hash 路由 #/comments)
        clicked = False
        try:
            loc = page.locator("a.tab-comments").first
            if await loc.count() > 0:
                try:
                    await loc.click(timeout=5000)
                    clicked = True
                except Exception:
                    # WAF 遮罩 (waf_nc_block) 会拦截 pointer 事件, JS 点击兜底
                    utils.logger.warning("[XueqiuCrawler] 正常点击被拦截 (WAF 遮罩?), 改用 JS 点击")
                    await page.evaluate(
                        "() => { const a = document.querySelector('a.tab-comments'); a && a.click(); }"
                    )
                    clicked = True
        except Exception as e:
            utils.logger.warning(f"[XueqiuCrawler] 未找到 '回复' tab: {e}")

        if not clicked:
            utils.logger.warning("[XueqiuCrawler] 未找到 '回复' tab, 尝试 hash 路由直接访问")
            await page.evaluate("() => { location.hash = '#/comments'; }")

        # 2. 滚动加载 + 解析回复列表
        await xueqiu_dom.wait_for_items(page, "article.timeline__item", timeout_s=15)
        await xueqiu_dom.scroll_until_stable(
            page, "article.timeline__item", max_rounds=50
        )

        seen_ids = self._load_stored_comment_ids(user_id)
        page_comments = await self._extract_comments_from_dom(user_id)
        new_items = [c for c in page_comments if c.comment_id and c.comment_id not in seen_ids]
        if new_items:
            for c in new_items:
                seen_ids.add(c.comment_id)
            await xueqiu_store.batch_update_xueqiu_comments(new_items)

        utils.logger.info(
            f"[XueqiuCrawler] 用户 {user_id} 回复抓取完成 (DOM 路径): 渲染 {len(page_comments)} 条, "
            f"新增 {len(new_items)} 条 (受 SPA 懒加载限制, 不保证与接口全量一致)"
        )

    async def _extract_comments_from_dom(self, user_id: str) -> List[XueqiuComment]:
        """
        从当前页面 DOM 提取回复列表。

        雪球 "回复" tab 的 DOM 结构 (2026-09 实测):
          article.timeline__item
            div.timeline__item__info > a.date-and-source   (href=/<uid>/<status_id>, 时间+来源)
            div.timeline__item__content .content          (回复正文)
            blockquote.timeline__item__forward
              a.fake-anchor[data-id]                       (评论 ID)
              .user-name                                  (被回复用户)
              .content                                    (被回复原文)
              a.replay-count                              (" · 讨论 N")
        """
        raw_items = await xueqiu_dom.extract_comment_items(self.context_page)
        comments: List[XueqiuComment] = []
        for item in raw_items:
            content = (item.get("content") or "").strip()
            if not content:
                continue
            status_url = item.get("status_url") or ""
            status_id = ""
            m = re.search(r"/(\d+)/(\d+)", status_url)
            if m:
                status_id = m.group(2)
            # 无 data-id 时用内容+帖子链接的稳定哈希兜底 (跨进程一致)
            fallback_id = f"dom-{int(hashlib.md5((content + status_url).encode('utf-8')).hexdigest(), 16) % 10**10}"
            comments.append(
                XueqiuComment(
                    comment_id=str(item.get("comment_id") or fallback_id),
                    content=content,
                    publish_time=0,  # 发布时间由 date-and-source 文本给出, 留待后续解析
                    like_count=0,
                    reply_count=int(item.get("reply_count") or 0),
                    status_id=status_id,
                    status_title="",
                    status_url=status_url,
                    user_id=user_id,
                    user_link=f"{self.index_url}/{user_id}",
                    user_nickname="",
                    user_avatar="",
                )
            )
        return comments

    def _load_stored_comment_ids(self, user_id: str) -> set:
        """
        读取该用户所有历史回复 jsonl, 返回已有 comment_id 集合 (跨运行/跨天去重)。
        """
        ids = set()
        try:
            base = os.path.join(config.SAVE_DATA_PATH or "data", "xueqiu", "jsonl")
            # 新命名: creator_<用户ID>_comments_<日期>.jsonl; 兼容旧命名 (无用户 ID)
            patterns = [
                os.path.join(base, f"creator_{user_id}_comments_*.jsonl"),
                os.path.join(base, "creator_comments_*.jsonl"),
            ]
            for pattern in patterns:
                for path in glob.glob(pattern):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    item = json.loads(line)
                                except Exception:
                                    continue
                                cid = item.get("comment_id", "")
                                if cid and not cid.startswith("dom-"):
                                    ids.add(cid)
                    except Exception as e:
                        utils.logger.warning(f"[XueqiuCrawler] 读取 {path} 失败: {e}")
        except Exception as e:
            utils.logger.warning(f"[XueqiuCrawler] 读取历史回复记录失败: {e}")
        return ids

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        Launch browser and create browser context.
        强制有头模式: headless 参数被忽略, 始终可见。
        """
        utils.logger.info("[XueqiuCrawler.launch_browser] Begin create browser context (headed) ...")
        headless = False  # 强制有头
        if config.SAVE_LOGIN_STATE:
            # 保存登录状态, 避免每次重复验证/登录
            user_data_dir = os.path.join(
                os.getcwd(), "browser_data", config.USER_DATA_DIR % config.PLATFORM
            )  # type: ignore
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={"width": 1920, "height": 1080},
                user_agent=user_agent,
                channel="chrome",  # 使用系统安装的稳定版 Chrome
            )
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy, channel="chrome")  # type: ignore
            browser_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080}, user_agent=user_agent
            )
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        Launch browser using CDP mode (headed).
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=False,  # 强制有头
            )

            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[XueqiuCrawler] CDP browser info: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[XueqiuCrawler] CDP mode launch failed, falling back to standard mode: {e}")
            chromium = playwright.chromium
            return await self.launch_browser(
                chromium, playwright_proxy, user_agent, headless=False
            )

    async def close(self):
        """Close browser context"""
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[XueqiuCrawler.close] Browser context closed ...")
