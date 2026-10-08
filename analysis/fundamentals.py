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
# 3. 不得进行大规模爬取或对目标平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
同花顺 F10 结构性事实抓取 (纯 HTTP，无需登录，GBK/GB18030 编码)。

研报和财经新闻本质上是股价上涨之后的追认叙事，结构上不会告诉你一家公司有多脆弱。
这里直接抓公司自己披露的原始数字和公司自己写下的风险段落，交给 LLM 的是可核验的
披露数据: 客户/供应商集中度、海外收入占比、经营现金流对净利润的覆盖、研发强度、
发明专利占比、境外销量占比、股东人数序列。

派生指标一律在 Python 里算好，不交给 LLM 做算术；缺失一律返回 None 而不是 0.0
(0.0 会被 LLM 当成真实数值引用)。
"""

import asyncio
import json
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from analysis.financial_consistency import coherent_yoy_pct
from tools.httpx_util import make_async_client
from tools.utils import utils

_BASE = "http://basic.10jqka.com.cn/{code6}/"
_URLS = {
    "operate": _BASE + "operate.html",
    "holder": _BASE + "holder.html",
    "finance": _BASE + "finance.html",
    "profile": _BASE,
    "event": _BASE + "event.html",
    "capital": _BASE + "capital.html",
    "company": _BASE + "company.html",
}
_PAGES = tuple(_URLS)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "http://basic.10jqka.com.cn/",
}
_CACHE_TTL_SECONDS = 6 * 3600
# 低于此体积视为异常页 (验证页/错误页)。F10 首页比内页小得多，单独放宽。
_MIN_PAGE_BYTES = {
    "operate": 50_000,
    "holder": 50_000,
    "finance": 50_000,
    "profile": 20_000,
    "event": 30_000,
    "capital": 30_000,
    "company": 30_000,
}
# 某些维度对特定行业本来就不披露 (如银行没有客户/供应商集中度)，这类缺失属正常，
# 仍记入 missing 供报告如实说明"暂缺"，但日志降级为 info 而非 WARNING。
_SECTOR_INHERENT_MISSING = {
    "客户/供应商集中度": ("银行", "证券", "保险", "非银金融", "多元金融"),
}

_cache: Dict[str, tuple] = {}
_locks: "defaultdict[str, asyncio.Lock]" = defaultdict(asyncio.Lock)

_UNIT_MULT = {"亿": 1e8, "万": 1e4}


def _bare_code(stock_code: str) -> str:
    return re.sub(r"\D", "", stock_code)[-6:]


def _decode(content: bytes) -> Optional[str]:
    for enc in ("gb18030", "gbk", "utf-8"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[　\s ]+", " ", text).strip()


def _section(html: str, section_id: str) -> str:
    """按 id 截取 m_box 段落，结束于下一个 m_box (无则到文末)。"""
    m = re.search(r'id="%s"' % re.escape(section_id), html)
    if not m:
        return ""
    start = html.rfind("<div", 0, m.start())
    nxt = re.search(r'<div class="m_box', html[m.end():])
    end = m.end() + nxt.start() if nxt else len(html)
    return html[max(start, 0):end]


def _table_rows(fragment: str) -> List[List[str]]:
    """取该片段里第一张表的行，每行是去标签后的单元格文本。"""
    m = re.search(r"<table\b.*?</table>", fragment, re.S | re.I)
    if not m:
        return []
    rows: List[List[str]] = []
    for tr in re.findall(r"<tr\b.*?</tr>", m.group(0), re.S | re.I):
        cells = [_strip_tags(c) for c in re.findall(r"<t[dh]\b.*?</t[dh]>", tr, re.S | re.I)]
        if cells:
            rows.append(cells)
    return rows


def _amount_yuan(text: Optional[str]) -> Optional[float]:
    """'92.01亿' / '1,685.00万' -> 元。无法解析返回 None。"""
    if not text:
        return None
    t = text.strip().replace(",", "").replace("，", "")
    m = re.match(r"^(-?[\d.]+)\s*(亿|万)?\s*元?$", t)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return val * _UNIT_MULT.get(m.group(2) or "", 1.0)


def _pct(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"(-?[\d.]+)\s*%", text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None

def _first_number(text: str) -> Optional[float]:
    m = re.search(r"-?[\d.]+", text or "")
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _is_blank(value: Optional[str]) -> bool:
    return not value or value.strip() in ("-", "--", "—", "")


_DOWN_WORDS = ("减少", "下降")


def _signed_yoy(direction: str, value: str) -> Optional[float]:
    """同比方向词带符号: 公告原文写"同比减少 44.08%"，丢掉方向词会让现金流下滑
    44% 在 prompt 里显示成增长 44%——方向反了的数字比没有数字更危险。"""
    try:
        magnitude = float(value)
    except ValueError:
        return None
    return -magnitude if direction in _DOWN_WORDS else magnitude


# --------------------------------------------------------------------------
# operate.html
# --------------------------------------------------------------------------

def _parse_operate_table(html: str) -> Optional[Dict]:
    """运营业务数据表: 第一行是期间表头，每行一个业务指标。"""
    rows = _table_rows(_section(html, "invest"))
    if len(rows) < 2:
        return None
    header = rows[0]
    periods = [h for h in header[1:] if re.match(r"\d{4}-\d{2}-\d{2}", h.strip())]
    if not periods:
        return None
    table: Dict[str, Dict[str, str]] = {}
    for row in rows[1:]:
        name = row[0].strip()
        if not name:
            continue
        table[name] = {
            periods[i]: row[i + 1]
            for i in range(min(len(periods), len(row) - 1))
        }
    return {"periods": periods, "rows": table}


def _row_latest(table: Optional[Dict], name: str) -> Optional[str]:
    """取该指标最新一个非空期间的值 (期间按时间倒序排列)。"""
    if not table:
        return None
    row = table.get("rows", {}).get(name)
    if not row:
        return None
    for period in table.get("periods", []):
        value = row.get(period)
        if not _is_blank(value):
            return value
    return None


def _parse_provider(html: str) -> Optional[Dict]:
    """主要客户及供应商: 页面把每个报告期都渲染进来，只取第一个 (最新期)。"""
    section = _section(html, "provider")
    if not section:
        return None

    period_m = re.search(r'<li class="cur">.*?operateTab"[^>]*>([\d-]+)</a>', section, re.S)
    period = period_m.group(1) if period_m else None

    text = _strip_tags(section)
    out: Dict = {"period": period}

    cust = re.search(
        r"前5大客户[：:]\s*共销售了\s*([\d.]+)\s*(亿|万)?\s*元\s*[,，]\s*占营业收入的\s*([\d.]+)\s*%",
        text,
    )
    if cust:
        out["top5_customer_amount_yuan"] = _amount_yuan(f"{cust.group(1)}{cust.group(2) or ''}")
        out["top5_customer_pct"] = float(cust.group(3))

    supp = re.search(
        r"前5大供应商[：:]\s*共采购了\s*([\d.]+)\s*(亿|万)?\s*元\s*[,，]\s*占总采购额的\s*([\d.]+)\s*%",
        text,
    )
    if supp:
        out["top5_supplier_amount_yuan"] = _amount_yuan(f"{supp.group(1)}{supp.group(2) or ''}")
        out["top5_supplier_pct"] = float(supp.group(3))

    # 逐个客户/供应商明细: 段落内第一张表是客户表，第二张是供应商表
    tables = re.findall(r"<table\b.*?</table>", section, re.S | re.I)
    for idx, key, name_key in ((0, "customers", "name"), (1, "suppliers", "name")):
        if idx >= len(tables):
            continue
        entries = []
        for tr in re.findall(r"<tr\b.*?</tr>", tables[idx], re.S | re.I):
            cells = [_strip_tags(c) for c in re.findall(r"<t[dh]\b.*?</t[dh]>", tr, re.S | re.I)]
            if len(cells) < 3 or not cells[0] or "名称" in cells[0]:
                continue
            entries.append(
                {
                    name_key: cells[0],
                    "amount_yuan": _amount_yuan(cells[1]),
                    "pct": _pct(cells[2]),
                }
            )
        if entries:
            out[key] = entries

    return out


def _split_observe_blocks(section: str) -> str:
    """董事会经营评述把所有报告期都渲染在同一段落里，只取第一个 (最新期)。"""
    parts = re.split(r'<div class="m_tab_content m_tab_content2"', section)
    return parts[1] if len(parts) > 1 else section


_HEADING_RE = re.compile(r"[一二三四五六七八九十]、[^ ]{2,28}")


def _heading_spans(text: str) -> List[tuple]:
    """一级小标题及其区间，用于按语义 (标题含"风险"/"经营情况") 定位段落，
    而不是硬编码某一家公司的标题文案。"""
    marks = list(_HEADING_RE.finditer(text))
    return [
        (m.group(0), m.start(), marks[i + 1].start() if i + 1 < len(marks) else len(text))
        for i, m in enumerate(marks)
    ]


def _parse_observe(html: str) -> Optional[Dict]:
    section = _section(html, "observe")
    if not section:
        return None
    text = _strip_tags(_split_observe_blocks(section))
    if not text:
        return None
    text = re.sub(r"(收起▲|查看全部▼|展开▼)", " ", text)
    text = re.sub(r"[　\s ]+", " ", text).strip()
    if not text:
        return None

    # 同一段文字在页面里渲染了两遍 (截断预览 + 全文)，只保留全文，别把 prompt 预算翻倍
    first = _HEADING_RE.search(text)
    if first:
        again = text.find(first.group(0), first.end())
        if again != -1 and again < len(text) * 0.6:
            text = text[again:]

    out: Dict = {}
    spans = _heading_spans(text)

    # 公司自己披露的风险: 标题含"风险"的一级小节；部分公司没有该小节，
    # 改为 "N、…风险" 小标题列表，取到下一个一级标题为止
    risks = ""
    for title, start, end in spans:
        if "风险" in title:
            risks = text[start:end].strip()
            break
    if not risks:
        m = re.search(r"\d、[^。]{2,30}?风险", text)
        if m:
            nxt = re.search(r"[一二三四五六七八九十]、", text[m.end():])
            risk_end = m.end() + nxt.start() if nxt else len(text)
            risks = text[m.start():risk_end].strip()
    # 银行等行业的"风险管理"小节是治理框架套话，可以很长，截断以免挤占 prompt 预算
    out["self_disclosed_risks"] = risks[:2000]

    # 经营情况讨论 (含公司自己的前瞻性表述)。找不到该小节时退回整段，
    # 至少保证公司自己的话能进 prompt，而不是由检索方替它转述。
    narrative = ""
    for title, start, end in spans:
        if "经营情况" in title or "主营业务分析" in title or "经营分析" in title:
            narrative = text[start:end].strip()
            break
    out["management_narrative"] = (narrative or text)[:4000]

    facts = out.setdefault("_facts", {})

    # 每个字段按"措辞最精确者优先"依次尝试: 银行等行业的经营评述不写
    # "归属于上市公司股东的净利润"，退化为"净利润"时必须同时记录口径，
    # 否则 prompt 无从判断这个数字是否含少数股东权益。
    _YOY = r"同比(?P<dir>增加|增长|上升|减少|下降)\s*(?P<yoy>[\d\.]+)\s*%"
    for key, alternatives in (
        (
            "revenue",
            [(r"营业收入\s*(?P<val>[\d,\.]+)\s*亿元?[，,]?\s*" + _YOY, None)],
        ),
        (
            "net_profit",
            [
                (
                    r"归属于(?:上市公司|母公司)股东的净利润\s*(?P<val>[\d,\.]+)\s*亿元?[，,]?\s*" + _YOY,
                    "归属于上市公司股东的净利润",
                ),
                (
                    r"归母净利润\s*(?P<val>[\d,\.]+)\s*亿元?[，,]?\s*" + _YOY,
                    "归属于上市公司股东的净利润",
                ),
                (
                    r"(?:实现)?净利润\s*(?P<val>[\d,\.]+)\s*亿元?[，,]?\s*" + _YOY,
                    "净利润(含少数股东权益)",
                ),
            ],
        ),
        (
            "operating_cash_flow",
            [
                (
                    r"经营活动产生的现金流量净额\s*(?P<val>[\d,\.]+)\s*亿元?[，,]?\s*" + _YOY,
                    None,
                )
            ],
        ),
    ):
        for pattern, basis in alternatives:
            m = re.search(pattern, text)
            if not m:
                continue
            facts[key] = _amount_yuan(m.group("val") + "亿")
            facts[key + "_yoy_pct"] = _signed_yoy(m.group("dir"), m.group("yoy"))
            if basis:
                facts[key + "_basis"] = basis
            break

    m = re.search(r"资产负债率\s*([\d\.]+)\s*%", text)
    if m:
        facts["debt_ratio_pct"] = float(m.group(1))

    # 海外收入: "海外业务收入约为人民币39,614.7百万元，较…增加了209.9%"
    m = re.search(
        r"海外业务收入约为?人民币\s*(?P<val>[\d,\.]+)\s*百万元"
        r"(?:.*?(?:同比)?(?P<dir>增加|增长|上升|减少|下降)了?\s*(?P<yoy>[\d\.]+)\s*%)?",
        text,
        re.S,
    )
    if m:
        val = _first_number(m.group("val").replace(",", ""))
        if val is not None:
            facts["overseas_revenue_yuan"] = val * 1e6
        if m.group("yoy"):
            facts["overseas_revenue_yoy_pct"] = _signed_yoy(m.group("dir"), m.group("yoy"))

    return out


# --------------------------------------------------------------------------
# holder.html / finance.html
# --------------------------------------------------------------------------

_PROFILE_LABEL_RE = re.compile(
    r">([^<>：:]{1,16})[：:]\s*</span>\s*<span class=\"tip f12\"[^>]*>([^<]{1,30})</span>"
)

# 公司概要里的标签 -> 输出字段。前两个是 (提取方式) 中的数值清洗函数。
_PROFILE_NUMERIC = {
    "市盈率(动态)": "pe_dynamic",
    "市盈率(静态)": "pe_static",
    "市净率": "pb",
    "每股净资产": "nav_per_share",
    "每股收益": "eps",
    "净资产收益率": "roe_pct",
    "毛利率": "gross_margin_pct",
    "质押股份占A股总股本比": "pledge_ratio_pct",
}


# 同花顺 F10 首页的"所属申万行业"与其他带标签字段的标记不同 (值是 tip f14 不是 tip f12)，
# 所以 _PROFILE_LABEL_RE 抓不到，单独匹配。两种写法都留：标签与值同在一个 span 与分在两个 span。
_SW_INDUSTRY_RE = re.compile(
    r"所属申万行业[：:]\s*</span>\s*<span[^>]*>([^<>]{1,20})</span>"
)
_SW_INDUSTRY_INLINE_RE = re.compile(r"所属申万行业[：:]\s*([一-龥A-Za-z]{2,20})")


def _parse_sw_industry(html: str) -> Optional[str]:
    """
    公司概要页标注的申万行业名 (如 "白酒Ⅱ")。

    这只是股票 -> 行业反查的起点，不是同花顺板块名，两套分类体系并不一一对应，
    由 analysis.industry 负责匹配。同花顺自家的"所属行业"字段是 JS 填充的，
    静态 HTML 里为空，取不到。
    """
    for pattern in (_SW_INDUSTRY_RE, _SW_INDUSTRY_INLINE_RE):
        m = pattern.search(html)
        if m:
            return m.group(1).strip()
    return None


def _parse_profile(html: str) -> Dict[str, Any]:
    """公司概要: 同花顺 F10 首页的带标签字段 (估值/每股指标/股权质押)。"""
    out: Dict[str, Any] = {}
    for label, value in _PROFILE_LABEL_RE.findall(html):
        label = label.strip()
        if label in _PROFILE_NUMERIC:
            out[_PROFILE_NUMERIC[label]] = _first_number(value)
        elif label == "每股经营现金流":
            out["ocf_per_share"] = _first_number(value)
        elif label == "流通A股":
            out["float_shares"] = _amount_yuan(value.replace("股", ""))
        elif label == "总股本":
            out["total_shares"] = _amount_yuan(value.replace("股", ""))
        elif label == "更新日期":
            out["valuation_as_of"] = value.strip()
    # 总股本的值 span 里有隐藏 input (<input type="hidden" value="76.30" id="stockzgb" />),
    # 走 _PROFILE_LABEL_RE 抓不到, 直接匹配该隐藏字段 (属性顺序是 value 在前)
    if "total_shares" not in out:
        m = re.search(r'value="([\d.]+)"\s+id="stockzgb"', html)
        if m:
            out["total_shares"] = float(m.group(1)) * 1e8
    return out


def _parse_holdernum(html: str) -> Optional[List[Dict]]:
    m = re.search(r'id="gdrsFlashData"[^>]*>\s*(\[.*?\])\s*</div>', html, re.S)
    if not m:
        return None
    try:
        series = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    out = []
    for item in series:
        if not isinstance(item, list) or len(item) < 3:
            continue
        count = _first_number(item[1])
        if count is None:
            continue
        out.append({"period": str(item[0]), "holders": int(count), "price": _first_number(item[2])})
    return out or None


_FINANCE_TAB_SPLIT = re.compile(r'<div class="m_tab_content')
_FINANCE_PERIOD_RE = re.compile(r"data='data_(\d{4}-\d{2}-\d{2})'")


def _parse_finance_metrics(html: str) -> tuple:
    """
    "变动科目" 表: <th>变动科目</th><td>本期数值</td><td>上期数值</td><td>变动幅度</td><td>变动原因</td>

    页面把每个报告期各渲染一张表 (最新期在前)，且**每张表只列该期变动较大的科目**——
    各期表的行集合不同。若按行名跨表取值，会把不同报告期的数字拼在一起 (实测 601899
    会把 2026Q1 的净利润和 2026H1 的营收算到同一组比率里)。因此只取最新一期那张表，
    期间取自该表自己的 data 标记；取不到标记就不给数，绝不猜。

    返回 (期间, {科目: {current, previous, yoy_pct}})，期间未知时返回 (None, {})。
    """
    newest_period: Optional[str] = None
    newest_metrics: Dict[str, Dict] = {}

    for chunk in _FINANCE_TAB_SPLIT.split(html):
        period_m = _FINANCE_PERIOD_RE.search(chunk)
        if not period_m:
            continue
        period = period_m.group(1)
        if newest_period is not None and period <= newest_period:
            continue
        table_m = re.search(r"<table\b.*?</table>", chunk, re.S | re.I)
        if not table_m:
            continue
        rows = [
            [_strip_tags(c) for c in re.findall(r"<t[dh]\b.*?</t[dh]>", tr, re.S | re.I)]
            for tr in re.findall(r"<tr\b.*?</tr>", table_m.group(0), re.S | re.I)
        ]
        if not rows or "变动科目" not in " ".join(rows[0]):
            continue

        metrics: Dict[str, Dict] = {}
        for cells in rows[1:]:
            if len(cells) < 3 or not cells[0]:
                continue
            current = _amount_yuan(cells[1])
            previous = _amount_yuan(cells[2])
            reported_yoy = _pct(cells[3]) if len(cells) > 3 else None
            metrics[cells[0]] = {
                "current": current,
                "previous": previous,
                "yoy_pct": coherent_yoy_pct(current, previous, reported_yoy),
            }
        newest_period, newest_metrics = period, metrics

    return newest_period, newest_metrics


def _parse_major_events(
    html: str, max_events: int = 10, kind_filter: Optional["re.Pattern[str]"] = None
) -> List[Dict]:
    """
    公司大事页 (event.html): 每行 <tr> 内是 日期 (<td class="hltip">) + 事项标签
    (<strong>发布公告：</strong> 等) + 公告标题链接。返回 [{date, kind, title}]，
    按页面顺序 (最新在前)。银行页面天然覆盖定增/注资/股东会等再融资事件。
    kind_filter 给定时只保留标签+标题匹配的事件 (如治理警示类)。
    """
    events: List[Dict] = []
    for tr in re.findall(r"<tr\b.*?</tr>", html, re.S | re.I):
        date_m = re.search(
            r'class="[^"]*hltip[^"]*"[^>]*>\s*(\d{4}-\d{2}-\d{2})', tr, re.S
        )
        if not date_m:
            continue
        kind = "事项"
        kind_m = re.search(r"<strong[^>]*>\s*([^<：:]{2,12})[：:]?\s*</strong>", tr)
        if kind_m:
            kind = kind_m.group(1).strip()
        title = ""
        for a in re.findall(r"<a\b[^>]*>(.*?)</a>", tr, re.S | re.I):
            t = re.sub(r"<[^>]+>", "", a)
            t = re.sub(r"&nbsp;?", " ", t).strip()
            if not t or "更多" in t or "详情" in t or "详细内容" in t:
                continue
            title = t
            break
        if not title:
            # 股东会议案等行的正文在 <span> 里而非链接里，取整行纯文本兜底
            plain = re.sub(r"<[^>]+>", " ", tr)
            plain = re.sub(r"&nbsp;?", " ", plain)
            plain = re.sub(r"\s+", " ", plain).strip()
            if not plain or "换肤" in plain or "更多" in plain or "详情" in plain:
                continue
            title = plain[:100]
        if kind_filter and not kind_filter.search(f"{kind} {title}"):
            continue
        events.append({"date": date_m.group(1), "kind": kind, "title": title})
        if len(events) >= max_events:
            break
    return events


def _parse_bank_industry_metrics(html: Optional[str]) -> Optional[Dict]:
    """
    经营分析页行业综述段落 (仅银行业页面存在): "6月末，商业银行本外币总资产…
    不良贷款余额3.72万亿元，不良贷款率1.52%，拨备覆盖率202.87%；资本充足率15.26%。"
    这是**行业口径**而非本行口径，供报告对比本行水平用，字段名与注释都标明 industry_。
    """
    if not html:
        return None
    # 分号会打断 [^。；]*, 而行业综述句中间有分号; 改用 [^。] + 有界惰性匹配，
    # 既允许分号又不会跨到别的段落去。
    m = re.search(
        r"(\d+)月末[^。]{0,150}?不良贷款余额\s*([\d.,]+)万亿[^。]{0,80}?"
        r"不良贷款率\s*([\d.]+)%[^。]{0,80}?拨备覆盖率\s*([\d.]+)%[^。]{0,80}?"
        r"资本充足率\s*([\d.]+)%",
        html,
    )
    if not m:
        return None
    return {
        "period": f"{m.group(1)}月末",
        "industry_npl_balance_trillion": float(m.group(2).replace(",", "")),
        "industry_npl_ratio_pct": float(m.group(3)),
        "industry_provision_coverage_pct": float(m.group(4)),
        "industry_capital_adequacy_pct": float(m.group(5)),
    }


def _parse_refinancing_records(html: str) -> List[Dict]:
    """
    资本运作页 "募集资金来源" 表: 增发/配股/可转债等再融资记录
    (公告日期/发行类别/发行起始日期/实际募集资金净额)。判断公司是否
    "滥发定增" 的客观依据。
    """
    records: List[Dict] = []
    for tr in re.findall(r"<tr\b.*?</tr>", html, re.S | re.I):
        cells = [_strip_tags(c) for c in re.findall(r"<t[dh]\b.*?</t[dh]>", tr, re.S | re.I)]
        if len(cells) < 4:
            continue
        date0, kind = cells[0], cells[1]
        if not re.match(r"\d{4}-\d{2}-\d{2}", date0) or not kind:
            continue
        if not re.search(r"增发|配股|可转债|优先股", kind):
            continue
        records.append(
            {
                "announce_date": date0,
                "kind": kind,
                "start_date": cells[2] if len(cells) > 2 else "",
                "amount": cells[3] if len(cells) > 3 else "",
            }
        )
        if len(records) >= 10:
            break
    return records


def _parse_executive_profile(html: str) -> Optional[Dict]:
    """
    公司资料页高管介绍: 董事长姓名/加入年份/薪酬/持股数。只取可核验的
    客观事实, 供管理层评价引用; "人品/美誉度"本身无法从数据自动评价,
    只能由这些记录侧面印证 (任职年限、薪酬持股与股东利益绑定等)。
    """
    text = _strip_tags(html)
    text = re.sub(r"\s+", " ", text)
    profile: Dict = {}
    m = re.search(r"董事长[：:]\s*([一-龥·A-Za-z]{2,8})", text)
    if m:
        profile["chairman"] = m.group(1)
    m = re.search(r"薪酬[：:]\s*([\d.]+)\s*万", text)
    if m:
        profile["chairman_salary_wan"] = float(m.group(1))
    m = re.search(r"持股数[：:]\s*([\d.]+)\s*(亿|万)股?", text)
    if m:
        profile["chairman_shares"] = f"{m.group(1)}{m.group(2)}股"
    years = re.findall(r"(\d{4})年(?:加入|进入)", text)
    if years:
        profile["joined_year"] = int(years[0])
    m = re.search(r"\d+\s*岁\s*(博士|硕士|本科)", text)
    if m:
        profile["chairman_education"] = m.group(1)
    if not profile:
        return None
    return profile


_GOVERNANCE_ALERT_RE = re.compile(r"减持|立案|处罚|问询|警示|监管|违规|调查|诉讼|仲裁")


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


# --------------------------------------------------------------------------
# 抓取与组装
# --------------------------------------------------------------------------

async def _fetch_pages(code6: str) -> Dict[str, Optional[str]]:
    results: Dict[str, Optional[str]] = {}
    async with make_async_client(headers=_HEADERS, timeout=25.0, follow_redirects=True) as client:

        async def _one(page: str) -> tuple:
            url = _URLS[page].format(code6=code6)
            try:
                resp = await client.get(url)
            except Exception as e:
                utils.logger.warning(f"[fundamentals] 请求失败 {url}: {e}")
                return page, None
            if resp.status_code != 200:
                utils.logger.warning(f"[fundamentals] HTTP {resp.status_code}: {url}")
                return page, None
            text = _decode(resp.content)
            if text is None:
                utils.logger.warning(f"[fundamentals] 解码失败: {url}")
                return page, None
            if len(resp.content) < _MIN_PAGE_BYTES[page]:
                utils.logger.warning(f"[fundamentals] 页面异常偏小 ({len(resp.content)}B): {url}")
                return page, None
            return page, text

        for page, html in await asyncio.gather(*(_one(p) for p in _PAGES), return_exceptions=False):
            results[page] = html
    return results


def _assemble(code6: str, pages: Dict[str, Optional[str]]) -> Optional[Dict]:
    operate = pages.get("operate")
    holder = pages.get("holder")
    finance = pages.get("finance")
    profile = pages.get("profile")
    event = pages.get("event")
    capital = pages.get("capital")
    company = pages.get("company")
    if not any((operate, holder, finance, profile, event, capital, company)):
        return None

    facts: Dict = {}
    missing: List[str] = []

    operate_table = _parse_operate_table(operate) if operate else None
    provider = _parse_provider(operate) if operate else None
    observe = _parse_observe(operate) if operate else None

    if operate_table:
        patents = _first_number(_row_latest(operate_table, "专利数量:授权专利(件)") or "")
        inventions = _first_number(_row_latest(operate_table, "专利数量:授权专利:发明专利(件)") or "")
        sold = _amount_yuan(_row_latest(operate_table, "销量:光通信收发模块(只)"))
        sold_overseas = _amount_yuan(_row_latest(operate_table, "销量:光通信收发模块:境外(只)"))
        if sold is None:
            # 指标名随公司而异 (如 "销量:光通信模块(只)")，退化为按前缀匹配唯一一条
            for name in (operate_table.get("rows") or {}):
                if name.startswith("销量:") and "境外" not in name:
                    sold = _amount_yuan(_row_latest(operate_table, name))
                    if sold:
                        facts["sales_metric_name"] = name
                        break
        if sold_overseas is None:
            for name in (operate_table.get("rows") or {}):
                if name.startswith("销量:") and "境外" in name:
                    sold_overseas = _amount_yuan(_row_latest(operate_table, name))
                    if sold_overseas:
                        facts["overseas_sales_metric_name"] = name
                        break

        facts["operate_period"] = operate_table["periods"][0]
        facts["patents_granted"] = patents
        facts["patents_invention"] = inventions
        facts["invention_ratio_pct"] = (
            round(inventions / patents * 100, 2) if patents and inventions is not None else None
        )
        facts["overseas_sales_pct"] = (
            round(sold_overseas / sold * 100, 2) if sold and sold_overseas is not None else None
        )
    else:
        missing.append("运营业务数据(专利/销量)")

    if provider:
        facts["concentration_period"] = provider.get("period")
        facts["top5_customer_pct"] = provider.get("top5_customer_pct")
        facts["top5_supplier_pct"] = provider.get("top5_supplier_pct")
        facts["top5_customer_amount_yuan"] = provider.get("top5_customer_amount_yuan")
        facts["top5_supplier_amount_yuan"] = provider.get("top5_supplier_amount_yuan")
    else:
        missing.append("客户/供应商集中度")

    if observe:
        o_facts = observe.get("_facts", {})
        facts.update({k: v for k, v in o_facts.items()})
    else:
        missing.append("董事会经营评述")

    # 银行行业口径资产质量 (经营分析页行业综述段落, 仅银行业页面存在; 可选维度)
    bank_metrics = _parse_bank_industry_metrics(operate)
    if bank_metrics:
        facts["bank_industry_metrics"] = bank_metrics

    # 公司大事 (公告/股东会等): 定增/注资/分红调整等关键事件的来源
    major_events = _parse_major_events(event) if event else []
    if not major_events:
        missing.append("公司大事(公告)")

    # 再融资记录 (资本运作页 "募集资金来源" 表): 判断是否滥发定增/配股
    refinancing_history = _parse_refinancing_records(capital) if capital else []
    if not refinancing_history:
        missing.append("再融资记录(增发/配股)")

    # 高管画像 (公司资料页高管介绍): 董事长姓名/加入年份/薪酬/持股/学历
    executive_profile = _parse_executive_profile(company) if company else None
    if not executive_profile:
        missing.append("高管介绍")
    if company:
        company_plain = re.sub(r"\s+", " ", _strip_tags(company))
        m = re.search(r"员工人数[：:]\s*([\d,]+)", company_plain)
        if m:
            facts["employee_count"] = int(m.group(1).replace(",", ""))

    # 治理警示事件 (减持/处罚/问询等, 来自公司大事全页扫描)
    governance_alerts = (
        _parse_major_events(event, max_events=8, kind_filter=_GOVERNANCE_ALERT_RE)
        if event
        else []
    )

    if holder:
        series = _parse_holdernum(holder)
        if series:
            facts["holder_count_series"] = series[:8]
            latest = series[0]
            facts["holder_count_latest"] = latest["holders"]
            facts["holder_count_period"] = latest["period"]
            if len(series) > 1 and series[1]["holders"]:
                facts["holder_count_qoq_pct"] = round(
                    (latest["holders"] - series[1]["holders"]) / series[1]["holders"] * 100, 2
                )
            # 去年同期: 序列多为季末/半年末，取日期同月同日最近的一条
            same = next((s for s in series[1:] if s["period"][5:] == latest["period"][5:]), None)
            if same and same["holders"]:
                facts["holder_count_yoy_pct"] = round(
                    (latest["holders"] - same["holders"]) / same["holders"] * 100, 2
                )
        else:
            missing.append("股东人数")
    else:
        missing.append("股东人数")

    # 结构化的"变动科目"表比从叙述文本里正则抽取更稳，只在对应字段还空着时兜底
    finance_period, metrics = _parse_finance_metrics(finance) if finance else (None, {})
    facts["finance_period"] = finance_period

    def _fill_metric(field: str, keys: tuple, yoy_field: Optional[str] = None) -> None:
        if facts.get(field) is not None:
            return
        for key in keys:
            entry = metrics.get(key) or {}
            if entry.get("current") is not None:
                facts[field] = entry["current"]
                if entry.get("previous") is not None:
                    facts[field + "_previous"] = entry["previous"]
                if yoy_field and entry.get("yoy_pct") is not None:
                    facts[yoy_field] = entry["yoy_pct"]
                return

    _fill_metric("revenue", ("营业收入(元)",), "revenue_yoy_pct")
    _fill_metric(
        "net_profit",
        ("归属于上市公司股东的净利润(元)", "归属于母公司股东的净利润(元)", "净利润(元)"),
        "net_profit_yoy_pct",
    )
    _fill_metric(
        "operating_cash_flow",
        ("经营活动产生的现金流量净额(元)",),
        "operating_cash_flow_yoy_pct",
    )
    _fill_metric("rd_investment_yuan", ("研发投入(元)", "研发费用(元)"), "rd_investment_yoy_pct")
    _fill_metric(
        "accounts_receivable_yuan",
        ("应收账款(元)", "应收票据及应收账款(元)", "应收款项(元)"),
        "accounts_receivable_yoy_pct",
    )
    _fill_metric(
        "inventory_yuan",
        ("存货(元)",),
        "inventory_yoy_pct",
    )

    if not metrics:
        missing.append("财务指标表")

    # 派生比率: 在 Python 里算好，不让 LLM 做算术
    facts["cash_to_profit_ratio"] = _ratio(facts.get("operating_cash_flow"), facts.get("net_profit"))
    if facts["cash_to_profit_ratio"] is not None:
        facts["cash_to_profit_ratio"] = round(facts["cash_to_profit_ratio"], 3)
    facts["rd_intensity_pct"] = (
        round(facts["rd_investment_yuan"] / facts["revenue"] * 100, 2)
        if facts.get("rd_investment_yuan") and facts.get("revenue")
        else None
    )
    facts["overseas_revenue_pct"] = (
        round(facts["overseas_revenue_yuan"] / facts["revenue"] * 100, 2)
        if facts.get("overseas_revenue_yuan") and facts.get("revenue")
        else None
    )
    # 仅在同一“变动科目”报告期同时取得营收与资产项目时计算。
    # 季报营收为累计流量，因此这里只作为营运资金压力代理，不作为行业横向估值指标。
    facts["receivable_to_revenue_pct"] = (
        round(facts["accounts_receivable_yuan"] / facts["revenue"] * 100, 2)
        if facts.get("accounts_receivable_yuan") is not None and facts.get("revenue")
        else None
    )
    facts["inventory_to_revenue_pct"] = (
        round(facts["inventory_yuan"] / facts["revenue"] * 100, 2)
        if facts.get("inventory_yuan") is not None and facts.get("revenue")
        else None
    )
    ar_yoy = facts.get("accounts_receivable_yoy_pct")
    inv_yoy = facts.get("inventory_yoy_pct")
    rev_yoy = facts.get("revenue_yoy_pct")
    facts["receivable_growth_minus_revenue_pp"] = (
        round(ar_yoy - rev_yoy, 2)
        if ar_yoy is not None and rev_yoy is not None
        else None
    )
    facts["inventory_growth_minus_revenue_pp"] = (
        round(inv_yoy - rev_yoy, 2)
        if inv_yoy is not None and rev_yoy is not None
        else None
    )

    valuation = _parse_profile(profile) if profile else {}
    if not valuation:
        missing.append("估值(PE/PB/每股指标)")

    facts["sw_industry"] = _parse_sw_industry(profile) if profile else None
    if profile and not facts["sw_industry"]:
        utils.logger.warning(
            f"[fundamentals] {code6} 公司概要页已获取但未解析出所属申万行业 "
            f"(疑似同花顺改版, 行业涨跌家数维度会退化为暂缺)"
        )

    source_map = {p: _URLS[p].format(code6=code6) for p in _PAGES if pages.get(p)}
    sources = list(source_map.values())

    return {
        "facts": facts,
        "valuation": valuation,
        "concentration_period": facts.get("concentration_period"),
        "management_narrative": (observe or {}).get("management_narrative", ""),
        "self_disclosed_risks": (observe or {}).get("self_disclosed_risks", ""),
        "top_customers": (provider or {}).get("customers", []),
        "top_suppliers": (provider or {}).get("suppliers", []),
        "major_events": major_events,
        "refinancing_history": refinancing_history,
        "executive_profile": executive_profile,
        "governance_alerts": governance_alerts,
        "missing": missing,
        "sources": sources,
        "source_map": source_map,
    }


def _log_result(code6: str, data: Optional[Dict], pages: Dict[str, Optional[str]]) -> None:
    if data is None:
        utils.logger.error(f"[fundamentals] {code6} 同花顺 F10 全部页面获取失败")
        return
    facts = data["facts"]
    filled = [k for k, v in facts.items() if v not in (None, [], "")]
    filled_val = [k for k, v in (data.get("valuation") or {}).items() if v not in (None, [], "")]
    utils.logger.info(
        f"[fundamentals] {code6} 解析字段 {len(filled) + len(filled_val)} 项 "
        f"(其中估值 {len(filled_val)} 项), 缺失维度: {data['missing'] or '无'}"
    )
    if data["missing"]:
        # 部分维度对特定行业本来就无法取得 (如银行不披露客户/供应商集中度)，
        # 这类"缺失"不该打 WARNING，降级为 info，避免日志噪声掩盖真正的解析失败。
        sw_industry = facts.get("sw_industry") or ""
        expected = [
            m for m in data["missing"]
            if m in _SECTOR_INHERENT_MISSING
            and any(s in sw_industry for s in _SECTOR_INHERENT_MISSING[m])
        ]
        unexpected = [m for m in data["missing"] if m not in expected]
        if unexpected:
            utils.logger.warning(f"[fundamentals] {code6} 未解析出: {unexpected}")
        if expected:
            utils.logger.info(
                f"[fundamentals] {code6} 缺失维度 {expected} 对该行业({sw_industry})通常不披露, 属正常"
            )
    ok = [p for p, h in pages.items() if h]
    if ok and not filled:
        # 页面体积正常却一个字段都没解析出来，是网站改版的典型特征，需要显式告警
        utils.logger.error(
            f"[fundamentals] {code6} 页面已获取但所有字段解析失败 (疑似同花顺改版), 页面: {ok}"
        )


async def get_ths_fundamentals(stock_code: str) -> Optional[Dict]:
    """
    同花顺 F10 结构性事实 (客户/供应商集中度、海外收入与销量占比、经营现金流对净利润
    的覆盖、研发强度、发明专利占比、股东人数序列、公司自述风险)。
    全部页面失败时返回 None；部分失败时对应字段为 None 并记入 missing。
    """
    code6 = _bare_code(stock_code)
    if not code6:
        return None

    cached = _cache.get(code6)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    async with _locks[code6]:
        cached = _cache.get(code6)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

        try:
            pages = await _fetch_pages(code6)
            data = _assemble(code6, pages)
        except Exception as e:
            utils.logger.error(f"[fundamentals] {code6} 抓取异常: {e}")
            return None

        _log_result(code6, data, pages)
        _cache[code6] = (time.time(), data)
        return data
