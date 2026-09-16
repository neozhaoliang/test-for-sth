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

from . import discover
from .client import XueqiuClient
from .exception import CrawlInterruptedError, DataFetchError, WafChallengeError
from .help import (
    extract_comments,
    extract_creator_from_user_obj,
    extract_status_list,
    normalize_user_id,
)
from .login import XueqiuLogin


class XueqiuCrawler(AbstractCrawler):
    context_page: Page
    xueqiu_client: XueqiuClient
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

            self.context_page = await self.browser_context.new_page()

            # 访问首页: WAF JS 挑战会自动在真实浏览器中解析通过
            await self._goto_with_waf(self.index_url, what="雪球首页")

            # Create a client to interact with the xueqiu website.
            self.xueqiu_client = XueqiuClient(playwright_page=self.context_page)

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
            # 增量更新: 从第 1 页开始, 遇到已存在的帖子即停 (帖子按时间倒序)
            await self.get_creator_posts_update(user_id)
        else:
            # 全量抓取 (timeline API, 页面内 XHR)
            # 被 WAF 中断时重新访问主页恢复信任状态, 然后从中断页码继续;
            # 断点跨运行持久化到 data/xueqiu/resume/, 重跑时自动续爬;
            # 已完整抓取过 (posts_done) 则跳过, 删除 resume 文件可强制重新抓取。
            resume_state = self._load_resume_state(user_id)
            if resume_state.get("posts_done"):
                utils.logger.info(
                    f"[XueqiuCrawler] 用户 {user_id} 发帖此前已完整抓取, 跳过 "
                    f"(如需重新抓取, 删除 data/xueqiu/resume/resume_{user_id}.json)"
                )
            else:
                start_page = resume_state.get("last_page", 1)
                if start_page > 1:
                    utils.logger.info(f"[XueqiuCrawler] 检测到断点记录, 从第 {start_page} 页继续发帖抓取")
                max_resume = 10
                for resume_round in range(max_resume):
                    try:
                        await self.xueqiu_client.get_all_user_posts(
                            user_id=user_id,
                            callback=self._store_statuses_callback,
                            start_page=start_page,
                        )
                        resume_state["posts_done"] = True
                        resume_state["last_page"] = start_page
                        self._save_resume_state(user_id, resume_state)
                        break
                    except CrawlInterruptedError as exc:
                        utils.logger.warning(
                            f"[XueqiuCrawler] 发帖抓取在第 {exc.page} 页被 WAF 中断 "
                            f"(第 {resume_round + 1} 次), 等待 {30 * resume_round + 10}s 后重新访问主页续爬"
                        )
                        resume_state["last_page"] = exc.page
                        self._save_resume_state(user_id, resume_state)
                        # 等 WAF 风控窗口冷却 (仅在被拦截时等待, 正常抓取无任何等待)
                        await asyncio.sleep(30 * resume_round + 10)
                        await self._goto_with_waf(
                            f"{self.index_url}/u/{user_id}",
                            what=f"用户 {user_id} 主页 (断点续爬)",
                        )
                        start_page = exc.page
                else:
                    utils.logger.error(
                        f"[XueqiuCrawler] 发帖抓取连续中断 {max_resume} 次, 放弃剩余分页, "
                        f"断点已保存, 稍后重跑可继续"
                    )

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

        # 2. 主页推荐候选补查粉丝数
        candidates = discover.load_candidates()
        if candidates:
            utils.logger.info(f"[XueqiuCrawler] 补查 {len(candidates)} 个推荐候选的粉丝数 ...")
            for cand in candidates:
                uid = str(cand.get("user_id") or "")
                if not uid or uid in discovered:
                    continue
                try:
                    res = await self.xueqiu_client.get_user_posts(uid, page=1)
                    statuses = res.get("statuses") or []
                    if statuses and statuses[0].get("user"):
                        d = discover._user_dict(statuses[0]["user"], "recommend")
                        if d and d["followers_count"] >= min_followers:
                            discovered[uid] = d
                            utils.logger.info(
                                f"[XueqiuCrawler] 推荐候选 {uid} ({d['screen_name']}) "
                                f"粉丝 {d['followers_count']} 达标"
                            )
                except (WafChallengeError, DataFetchError) as e:
                    utils.logger.warning(f"[XueqiuCrawler] 候选 {uid} 粉丝数查询失败: {e}")
                await asyncio.sleep(0.5)  # 候选查询轻量限速, 降低风控
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
        增量更新用户发帖: 从第 1 页开始抓取 (帖子按时间倒序),
        遇到已存在的帖子即停止, 只存储新增部分。

        可反复执行 (幂等): 本次更新的结果会同步进已知 ID 索引,
        下次运行只抓更新增内容。
        """
        known = self._load_known_status_ids(user_id)
        utils.logger.info(f"[XueqiuCrawler] 增量更新模式, 已有 {len(known)} 条历史帖子索引")
        page = 1
        new_total = 0
        max_rounds = 10
        for resume_round in range(max_rounds):
            try:
                res = await self.xueqiu_client.get_user_posts(user_id, page=page)
            except (WafChallengeError, DataFetchError) as e:
                wait = 30 * resume_round + 10
                utils.logger.warning(
                    f"[XueqiuCrawler] 增量更新第 {page} 页被 WAF 中断 "
                    f"(第 {resume_round + 1} 次): {e}, {wait}s 后重试"
                )
                await asyncio.sleep(wait)
                await self._goto_with_waf(
                    f"{self.index_url}/u/{user_id}",
                    what=f"用户 {user_id} 主页 (增量更新)",
                )
                continue

            statuses = res.get("statuses") or []
            if not statuses:
                utils.logger.info(f"[XueqiuCrawler] 增量更新第 {page} 页无数据, 结束")
                break

            fresh = [s for s in statuses if str(s.get("id") or "") not in known]
            if fresh:
                await self._store_statuses_callback(fresh)
                new_total += len(fresh)
                for s in fresh:
                    known.add(str(s.get("id") or ""))
                self._save_known_status_ids(user_id, known)
                utils.logger.info(f"[XueqiuCrawler] 第 {page} 页新增 {len(fresh)} 条 (累计新增 {new_total})")

            if len(fresh) < len(statuses):
                # 本页出现已存在的帖子 → 时间倒序边界, 更新完成
                utils.logger.info(
                    f"[XueqiuCrawler] 第 {page} 页发现已存在帖子, 增量更新完成, "
                    f"共新增 {new_total} 条发帖"
                )
                break
            page += 1
        else:
            utils.logger.error(
                f"[XueqiuCrawler] 增量更新连续中断 {max_rounds} 次, 放弃; "
                f"稍后重跑 --update 即可继续"
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
        抓取用户的全部回复 (主页 "回复" tab, SPA 路由 #/comments)。

        策略:
          1. 点击 a.tab-comments (Playwright 点击; 若被 WAF 遮罩拦截则 JS 点击兜底);
          2. 滚动加载并解析 DOM 中 article.timeline__item 结构的回复列表。
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
            await page.evaluate(
                "() => { location.hash = '#/comments'; }"
            )

        # 等待回复列表渲染
        for _ in range(15):
            count = await page.evaluate("() => document.querySelectorAll('.timeline__item').length")
            if count > 0:
                break
            await asyncio.sleep(1)

        # 2. 滚动加载 + 解析回复列表
        comments: List[XueqiuComment] = []
        seen_ids = self._load_stored_comment_ids(user_id)
        if seen_ids:
            utils.logger.info(f"[XueqiuCrawler] 已存在 {len(seen_ids)} 条历史回复, 将跳过重复项")
        no_growth_rounds = 0
        for _ in range(500):
            page_comments = await self._extract_comments_from_dom(user_id)
            new_items = [c for c in page_comments if c.comment_id and c.comment_id not in seen_ids]
            if new_items:
                for c in new_items:
                    seen_ids.add(c.comment_id)
                comments.extend(new_items)
                await xueqiu_store.batch_update_xueqiu_comments(new_items)
                utils.logger.info(f"[XueqiuCrawler] 已抓取 {len(comments)} 条回复")
                no_growth_rounds = 0
            else:
                no_growth_rounds += 1
                if no_growth_rounds >= 3:
                    utils.logger.info(f"[XueqiuCrawler] 回复列表连续 {no_growth_rounds} 轮无新增, 结束, 共 {len(comments)} 条")
                    break

            # 滚动到底部加载更多
            reached_bottom = await page.evaluate(
                "() => { window.scrollTo(0, document.body.scrollHeight); "
                "return document.body.scrollHeight; }"
            )
            await asyncio.sleep(1)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == reached_bottom:
                # 再等一次, 确认无增量
                await asyncio.sleep(2)
                if await page.evaluate("() => document.body.scrollHeight") == reached_bottom:
                    utils.logger.info(f"[XueqiuCrawler] 回复列表滚动到底, 共 {len(comments)} 条")
                    break

        utils.logger.info(f"[XueqiuCrawler] 用户 {user_id} 回复抓取完成, 共 {len(comments)} 条")

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
        try:
            raw_items = await self.context_page.evaluate(
                """() => {
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
            )
        except Exception as e:
            utils.logger.warning(f"[XueqiuCrawler] DOM 回复提取失败: {e}")
            return []

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
