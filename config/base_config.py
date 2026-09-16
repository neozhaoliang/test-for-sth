# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/config/base_config.py
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

# Basic configuration
PLATFORM = "xueqiu"  # Platform, xueqiu | bili

# 是否使用海外版小红书 (rednote.com)
# 开启后 API 走 webapi.rednote.com，cookie 域使用 .rednote.com
XHS_INTERNATIONAL = False

KEYWORDS = "编程副业,编程兼职"  # Keyword search configuration, separated by English commas
LOGIN_TYPE = "qrcode"  # qrcode or phone or cookie
COOKIES = ""
CRAWLER_TYPE = (
    "search"  # Crawling type, search (keyword search) | detail (post details) | creator (creator homepage data)
)
# Whether to enable IP proxy
ENABLE_IP_PROXY = False

# Number of proxy IP pools
IP_PROXY_POOL_COUNT = 2

# Proxy IP provider name
IP_PROXY_PROVIDER_NAME = "kuaidaili"  # kuaidaili | wandouhttp | static

# Static proxy configuration (used when IP_PROXY_PROVIDER_NAME is set to "static")
# Format: "http://your_home_domain:port" or "http://user:password@your_home_domain:port"
STATIC_PROXY_URL = ""

# 强制有头浏览器: 始终显示浏览器窗口 (无头模式已移除)
# 雪球有阿里云 WAF 滑块验证, 必须使用有头浏览器以便人工拖动验证
HEADLESS = False

# Whether to save login status
SAVE_LOGIN_STATE = True

# ==================== CDP (Chrome DevTools Protocol) 配置 ====================
# 是否启用 CDP 模式 - 使用用户本地的 Chrome/Edge 浏览器进行爬取，具有更好的反检测能力
# 开启后，会自动检测并启动用户的 Chrome/Edge 浏览器，通过 CDP 协议进行控制
# 该方式使用真实浏览器环境，包括用户的扩展、Cookie 和设置，大幅降低被风控检测的风险
ENABLE_CDP_MODE = True

# CDP 调试端口，用于与浏览器通信
# 如果端口被占用，系统会自动尝试下一个可用端口
CDP_DEBUG_PORT = 9222

# 自定义浏览器路径（可选）
# 如果为空，系统会自动检测 Chrome/Edge 的安装路径
# Windows 示例: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
# macOS 示例: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CUSTOM_BROWSER_PATH = ""

# CDP 模式同样强制有头 (无头模式已移除)
CDP_HEADLESS = False

# 浏览器启动超时时间（秒）
BROWSER_LAUNCH_TIMEOUT = 60

# 是否连接用户已打开的浏览器，而不是启动新的浏览器
# 开启后，程序会连接一个已经启用了远程调试的浏览器
# 用户需要在 Chrome 中开启远程调试：chrome://inspect/#remote-debugging
# 或者使用命令行参数启动 Chrome：--remote-debugging-port=9222
# 这种方式反检测效果最好，因为直接使用用户真实浏览器的所有 Cookie、扩展和浏览历史
CDP_CONNECT_EXISTING = True

# 程序结束时是否自动关闭浏览器
# 设置为 False 可以保持浏览器运行，方便调试
AUTO_CLOSE_BROWSER = True

# Data saving type option configuration, supports: csv, db, json, jsonl, sqlite, excel, postgres. It is best to save to DB, with deduplication function.
SAVE_DATA_OPTION = "jsonl"  # csv or db or json or jsonl or sqlite or excel or postgres

# Data saving path, if not specified by default, it will be saved to the data folder.
SAVE_DATA_PATH = ""

# Browser file configuration cached by the user's browser
USER_DATA_DIR = "%s_user_data_dir"  # %s will be replaced by platform name

# The number of pages to start crawling starts from the first page by default
START_PAGE = 1

# Control the number of crawled videos/posts
CRAWLER_MAX_NOTES_COUNT = 15

# Controlling the number of concurrent crawlers
MAX_CONCURRENCY_NUM = 1

# Whether to enable crawling media mode (including image or video resources), crawling media is not enabled by default
ENABLE_GET_MEIDAS = False

# Whether to enable comment crawling mode. Comment crawling is enabled by default.
ENABLE_GET_COMMENTS = True

# Control the number of crawled first-level comments (single video/post)
CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 10

# Whether to enable the mode of crawling second-level comments. By default, crawling of second-level comments is not enabled.
# If the old version of the project uses db, you need to refer to schema/tables.sql line 287 to add table fields.
ENABLE_GET_SUB_COMMENTS = False

# word cloud related
# Whether to enable generating comment word clouds
ENABLE_GET_WORDCLOUD = False
# Custom words and their groups
# Add rule: xx:yy where xx is a custom-added phrase, and yy is the group name to which the phrase xx is assigned.
CUSTOM_WORDS = {
    "零几": "年份",  # Recognize "zero points" as a whole
    "高频词": "专业术语",  # Example custom words
}

# Deactivate (disabled) word file path
STOP_WORDS_FILE = "./docs/hit_stopwords.txt"

# Chinese font file path
FONT_PATH = "./docs/STZHONGS.TTF"

# Crawl interval (速率限制已移除, 设为 0 即请求间不等待)
CRAWLER_MAX_SLEEP_SEC = 0

# 增量更新模式: True 时只抓取上次抓取之后新增的发帖/回复 (配合 --update)
XUEQIU_UPDATE_MODE = False

# 抓取节流 (秒/页): 0 = 不限速 (可能频繁触发 WAF 风控, 进入等待-续爬循环);
# 设 0.5-2 可匀速抓取, 触发风控的概率大幅降低, 总体反而更快
XUEQIU_PACE_SEC = 0

# 用户发现: 检索粉丝数 >= 该阈值的用户并抓取其帖子 (0 = 关闭)
# 来源: 已爬帖子数据中的用户 + 主页 "用户推荐" 候选
XUEQIU_DISCOVER_FANS = 0

# 单次发现最多抓取的用户数 (0 = 不限)
XUEQIU_DISCOVER_MAX_USERS = 0

# 发现时排除的用户 ID (英文逗号分隔, 如官方媒体号)
# 示例: 9485866208=雪球基金, 8152922548=今日话题, 5124430882=7X24快讯
XUEQIU_DISCOVER_EXCLUDE_IDS = ""

# 是否禁用 SSL 证书验证。仅在使用企业代理、Burp Suite、mitmproxy 等会注入自签名证书的中间人代理时设为 True。
# 警告：禁用 SSL 验证将使所有流量暴露于中间人攻击风险，请勿在生产环境中开启。
DISABLE_SSL_VERIFY = False

from .bilibili_config import *
from .xueqiu_config import *
