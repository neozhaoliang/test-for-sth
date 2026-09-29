# -*- coding: utf-8 -*-
"""
Rendering of structured evidence into prompt-ready human-readable blocks.

No network calls live here.  Keeping these pure-ish formatters outside report.py makes
prompt composition testable and prevents evidence blocks from accidentally bleeding into
unrelated sections.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from analysis.evidence import ResearchQuality
from analysis.research_profile import ResearchProfile, profile_prompt_block
from analysis.report_contract import _PROMPT_TEMPLATE
from model.m_analysis import CandidateOpinion, KnowledgeExcerpt


def _build_major_events_block(major_events: Optional[List[Dict]]) -> str:
    if not major_events:
        return f"近期重大事项 (公告/股东会等): {_MISSING_TAIL}"
    lines = ["近期重大事项 (同花顺 F10 公司大事，倒序排列):"]
    for e in major_events[:8]:
        lines.append(f"  {e.get('date', '')} [{e.get('kind', '')}] {e.get('title', '')}")
    return "\n".join(lines)


_KB_FUND_FLOW_RE = re.compile(
    r"筹码|资金|风格|调仓|公募|蓝筹|红利|抱团|机构|散户|派发|切换|融资|杠杆|政策"
)


def _build_kb_fund_flow_block(knowledge_excerpts: List[KnowledgeExcerpt]) -> str:
    """
    从知识库 (老木匠/军师祭咖啡等的专栏与发帖摘要) 中抽出资金面/筹码/风格/
    政策相关的真实记录, 单独成块——解释股价与户数联动、板块涨跌时模型必须
    先引用这里, 而不是套"户数增加=派发"模板。
    """
    hits = [e for e in knowledge_excerpts if _KB_FUND_FLOW_RE.search(e.distilled)]
    if not hits:
        return "(知识库中无相关的资金/风格/筹码记录)"
    lines = [
        "知识库中该时期的资金与风格真实记录 (专栏/发帖摘要原文, 含来源与日期; "
        "解释股价与户数联动、板块涨跌、风格切换时必须先引用这里的记录, 模板推断仅在其缺位时使用):"
    ]
    for e in hits[:10]:
        lines.append(f"  · [{e.source} {e.title[:50]}] {e.distilled[:400]}")
    return "\n".join(lines)


def _build_margin_block(
    margin_signal: Optional[Dict], valuation: Optional[Dict], quote: Optional[Dict]
) -> str:
    if not margin_signal:
        return f"融资盘与流通盘: {_MISSING_TAIL}"
    lines: List[str] = []
    float_shares = (valuation or {}).get("float_shares")
    latest_price = (quote or {}).get("latest_price")
    if float_shares:
        lines.append(f"  流通股本 {round(float_shares / 1e8, 2)} 亿股")
        if latest_price:
            float_mv_yi = float_shares * latest_price / 1e8
            lines.append(f"  按最新价计流通市值 {round(float_mv_yi, 0)} 亿")
    lines.append(
        f"  融资余额 {margin_signal.get('latest_balance_yi')} 亿 "
        f"({margin_signal.get('latest_date')}), "
        f"近 {margin_signal.get('days')} 个交易日从 {margin_signal.get('first_balance_yi')} 亿 "
        f"变为 {margin_signal.get('latest_balance_yi')} 亿 "
        f"(变化 {margin_signal.get('change_pct'):+}%)"
    )
    if float_shares and latest_price:
        ratio = margin_signal.get("latest_balance_yi", 0) / (float_shares * latest_price / 1e8) * 100
        lines.append(f"  融资余额/流通市值 {round(ratio, 2)}%")
    return "融资盘与流通盘:\n" + "\n".join(lines)


def _build_rd_block(
    fundamentals: Optional[Dict],
    executive_profile: Optional[Dict],
    rd_team: Optional[Dict],
) -> str:
    facts = (fundamentals or {}).get("facts") or {}
    lines = ["研发能力 (科技企业重点维度, 技术护城河判断必须以这些数字为证据):"]
    rd = facts.get("rd_investment_yuan")
    lines.append(
        f"  研发投入 {rd / 1e8:.2f} 亿" if rd else "  研发投入 暂缺"
    )
    lines.append(
        f"  研发强度(研发投入/营业收入) {facts.get('rd_intensity_pct')}%"
        if facts.get("rd_intensity_pct") is not None else "  研发强度 暂缺"
    )
    lines.append(
        f"  授权专利 {facts.get('patents_granted')} 件, 其中发明专利 "
        f"{facts.get('patents_invention')} 件 (发明专利占比 {facts.get('invention_ratio_pct')}%)"
        if facts.get("patents_granted") is not None else "  专利数据 暂缺"
    )
    lines.append(
        f"  员工人数 {facts.get('employee_count')}"
        if facts.get("employee_count") else "  员工人数 暂缺"
    )

    if rd_team and rd_team.get("parse_status") == "ok":
        lines.append(
            f"  研发人员 {rd_team.get('rd_headcount')} 人，占员工总数 "
            f"{rd_team.get('rd_staff_ratio_pct')}% "
            f"(来源: {rd_team.get('title') or '最新年报'}, 巨潮资讯)"
        )
        education = rd_team.get("education") or {}
        if education:
            labels = {
                "doctor": "博士",
                "master": "硕士",
                "bachelor": "本科",
                "college": "专科",
                "high_school_or_below": "高中及以下",
            }
            lines.append(
                "  研发人员学历结构: "
                + "，".join(
                    f"{labels.get(k, k)} {v} 人"
                    for k, v in education.items()
                    if v is not None
                )
            )
        else:
            lines.append("  研发人员学历结构: 年报相关页未解析出")
        age = rd_team.get("age") or {}
        if age:
            labels = {
                "under_30": "30岁以下",
                "30_to_40": "30-40岁",
                "40_to_50": "40-50岁",
                "50_to_60": "50-60岁",
                "60_or_above": "60岁及以上",
            }
            lines.append(
                "  研发人员年龄结构: "
                + "，".join(
                    f"{labels.get(k, k)} {v} 人"
                    for k, v in age.items()
                    if v is not None
                )
            )
        if rd_team.get("pdf_url"):
            lines.append(f"  年报原文: {rd_team['pdf_url']}")
    elif rd_team:
        lines.append(
            f"  研发队伍组成: 已定位 {rd_team.get('title') or '最新年报'}，"
            f"但解析状态为 {rd_team.get('parse_status')}，不得猜测人员结构"
        )
    else:
        lines.append("  研发队伍组成: 本次未取得最新年报研发人员表, 暂缺")

    if executive_profile:
        lines.append(
            f"  董事长学历 {executive_profile.get('chairman_education')}"
            if executive_profile.get("chairman_education") else "  董事长学历 暂缺"
        )
    return "\n".join(lines)


def _build_management_capital_block(data: Optional[Dict]) -> str:
    if not data:
        return "暂缺 (本次未能构建长期资本分配账本)"
    lines: List[str] = []
    alignment = data.get("alignment") or {}
    parts = []
    if alignment.get("chairman"):
        parts.append(f"董事长 {alignment['chairman']}")
    if alignment.get("joined_year"):
        tenure = alignment.get("tenure_years")
        parts.append(
            f"{alignment['joined_year']}年加入公司"
            + (f"，至今约 {tenure} 年" if tenure is not None else "")
        )
    if alignment.get("chairman_salary_wan") is not None:
        parts.append(f"年薪 {alignment['chairman_salary_wan']} 万元")
    if alignment.get("chairman_shares"):
        parts.append(f"持股 {alignment['chairman_shares']}")
    lines.append("管理层利益绑定: " + ("；".join(parts) if parts else "暂缺"))

    execution = data.get("execution") or {}
    if execution:
        seg = [
            f"观察期 {execution.get('period_start')}~{execution.get('period_end')}",
        ]
        if execution.get("roe_latest_pct") is not None:
            seg.append(
                f"ROE从 {execution.get('roe_start_pct')}% 变为 {execution.get('roe_latest_pct')}% "
                f"({execution.get('roe_change_pp'):+}pct；区间 "
                f"{execution.get('roe_min_pct')}%~{execution.get('roe_max_pct')}%)"
            )
        if execution.get("net_profit_growth_observations"):
            seg.append(
                f"净利润增速为正 {execution.get('net_profit_growth_positive_periods')}/"
                f"{execution.get('net_profit_growth_observations')} 个观察期"
            )
        if execution.get("net_margin_change_pp") is not None:
            seg.append(
                f"净利率较观察期起点变化 {execution.get('net_margin_change_pp'):+} 个百分点"
            )
        lines.append("经营执行记录: " + "；".join(seg))
    else:
        lines.append("经营执行记录: 暂缺")

    for key, label in (("five_year", "近5年"), ("ten_year", "近10年")):
        row = data.get(key) or {}
        if not row:
            continue
        lines.append(
            f"{label}资本分配: 分红覆盖 {row.get('dividend_years_count')}/{row.get('window_years')} 个日历年，"
            f"累计每10股现金分红 {row.get('cash_dividend_per_10_total')} 元；"
            f"回购记录 {row.get('buyback_records')} 次，已回购金额合计 "
            f"{_yi(row.get('buyback_actual_amount_yuan'))}；"
            f"F10再融资记录 {row.get('refinancing_records')} 次，"
            f"巨潮再融资公告 {row.get('primary_refinancing_announcements')} 条；"
            f"巨潮减持公告 {row.get('insider_reduction_announcements')} 条；"
            f"处罚/警示/问询等治理负面公告 {row.get('governance_negative_announcements')} 条"
        )

    reductions = data.get("recent_insider_reduction_events") or []
    if reductions:
        lines.append("近期减持公告:")
        for item in reductions[:5]:
            lines.append(
                f"  · {item.get('published_at') or ''} {item.get('title') or ''}"
                + (f" | {item.get('url')}" if item.get("url") else "")
            )

    negatives = data.get("recent_governance_negative_events") or []
    if negatives:
        lines.append("近期治理负面公告:")
        for item in negatives[:5]:
            lines.append(
                f"  · {item.get('published_at') or ''} {item.get('title') or ''}"
                + (f" | {item.get('url')}" if item.get("url") else "")
            )

    for note in data.get("notes") or []:
        lines.append(f"注: {note}")
    lines.append(
        "口径说明: 这是一份事实账本，不等同于对个人品德的判断；"
        "管理层可信度只能从长期行为记录、利益绑定和经营兑现中谨慎推断。"
    )
    return "\n".join(lines)


def _build_governance_block(
    refinancing_history: Optional[List[Dict]],
    executive_profile: Optional[Dict],
    governance_alerts: Optional[List[Dict]],
) -> str:
    lines = ["治理与股东回报记录 (管理层评价只能引用这里的客观事实):"]
    if refinancing_history:
        lines.append(
            "  再融资历史 (判断是否滥发定增/配股): "
            + "；".join(
                f"{r.get('announce_date', '')} {r.get('kind', '')} 募资 {r.get('amount', '')}"
                for r in refinancing_history[:5]
            )
        )
    else:
        lines.append(f"  再融资历史 (增发/配股): {_MISSING_TAIL}")
    if governance_alerts:
        lines.append(
            "  治理警示事件 (减持/处罚/问询等): "
            + "；".join(
                f"{e.get('date', '')} [{e.get('kind', '')}] {e.get('title', '')[:60]}"
                for e in governance_alerts[:5]
            )
        )
    else:
        lines.append("  治理警示事件: 近期公司大事中无减持/处罚/问询类记录")
    if executive_profile:
        parts = []
        if executive_profile.get("chairman"):
            parts.append(f"董事长 {executive_profile['chairman']}")
        if executive_profile.get("joined_year"):
            parts.append(f"{executive_profile['joined_year']}年加入公司")
        if executive_profile.get("chairman_salary_wan") is not None:
            parts.append(f"薪酬 {executive_profile['chairman_salary_wan']}万/年")
        if executive_profile.get("chairman_shares"):
            parts.append(f"持股 {executive_profile['chairman_shares']}")
        lines.append("  高管画像 (客观事实): " + ", ".join(parts))
    else:
        lines.append(f"  高管画像 (董事长/任职/薪酬/持股): {_MISSING_TAIL}")
    return "\n".join(lines)


def _build_sentiment_block(sentiment: Optional[Dict]) -> str:
    if not sentiment:
        return f"雪球讨论区情绪: {_MISSING_TAIL}"
    lines = [
        f"雪球讨论区情绪 (收集 {sentiment.get('posts_collected')} 条表态, "
        f"来自 {sentiment.get('users')} 位用户):",
        f"  看多 {sentiment.get('bullish')} 条 (其中有论据 {sentiment.get('reasoned_bullish')} 条), "
        f"看空 {sentiment.get('bearish')} 条 (其中有论据 {sentiment.get('reasoned_bearish')} 条), "
        f"中性 {sentiment.get('neutral')} 条, 无关 {sentiment.get('irrelevant')} 条; "
        f"看多占方向性表态 {sentiment.get('bullish_ratio')}",
    ]
    if sentiment.get("note"):
        lines.append(f"  {sentiment['note']}")
    return "\n".join(lines)


def _build_dividend_chart(
    dividend_history: Optional[List[Dict]],
    buyback_history: Optional[List[Dict]],
    valuation: Optional[Dict],
    quote: Optional[Dict],
) -> Optional[List[Dict]]:
    """
    按年度聚合分红与回购 (回购按总股本折算成"元/10股", 与分红同口径相加),
    并计算按现价折算的股息率, 供柱状图展示。
    """
    total_shares = (valuation or {}).get("total_shares")
    latest_price = (quote or {}).get("latest_price")
    by_year: Dict[str, Dict] = {}
    for d in dividend_history or []:
        y = str(d.get("announce_date", ""))[:4]
        if not y.isdigit():
            continue
        g = by_year.setdefault(y, {"year": y, "div_per_10": 0.0, "buyback_per_10": 0.0})
        try:
            g["div_per_10"] += float(d.get("dividend_per_10_shares") or 0)
        except (TypeError, ValueError):
            pass
    for b in buyback_history or []:
        y = str(b.get("announce_date", ""))[:4]
        if not y.isdigit():
            continue
        g = by_year.setdefault(y, {"year": y, "div_per_10": 0.0, "buyback_per_10": 0.0})
        try:
            amount = float(b.get("actual_amount") or 0)
        except (TypeError, ValueError):
            continue
        if total_shares:
            g["buyback_per_10"] += amount / total_shares * 10
    if not by_year:
        return None
    out = []
    selected_years = sorted(by_year)[-10:]
    for y in selected_years:
        g = by_year[y]
        total = g["div_per_10"] + g["buyback_per_10"]
        yield_pct = None
        if latest_price and total:
            yield_pct = round(total / 10 / latest_price * 100, 2)
        out.append(
            {
                "year": y,
                "dividend_per_10": round(g["div_per_10"], 2),
                "buyback_per_10": round(g["buyback_per_10"], 2),
                "total_per_10": round(total, 2),
                "yield_pct": yield_pct,
            }
        )
    return out


def _build_debate_block(debate: Optional[Dict]) -> str:
    if not debate:
        return f"雪球多空辩论: {_MISSING_TAIL}"
    bull = debate.get("bull") or {}
    bear = debate.get("bear") or {}
    lines = [
        f"雪球讨论区多空辩论 (收集 {debate.get('posts_collected')} 条表态, "
        f"分类 {debate.get('classified')} 条):",
        f"  多方: {bull.get('count')} 条, 其中有时间范围的股价预测验证 "
        f"{bull.get('verified')} 条 (正确 {bull.get('correct')}, 错误 {bull.get('incorrect')}), "
        f"未验证 {bull.get('unverified')} 条",
        f"  空方: {bear.get('count')} 条, 其中有时间范围的股价预测验证 "
        f"{bear.get('verified')} 条 (正确 {bear.get('correct')}, 错误 {bear.get('incorrect')}), "
        f"未验证 {bear.get('unverified')} 条",
        "  多方核心论点:",
        *[f"    · {ln}" for ln in (debate.get('bull_core') or '').split('\n') if ln.strip()][:6],
        "  空方核心论点:",
        *[f"    · {ln}" for ln in (debate.get('bear_core') or '').split('\n') if ln.strip()][:6],
    ]
    if debate.get("verdict"):
        lines.append(f"  哪方更合理: {debate.get('verdict')} — {debate.get('reason', '')}")
    return "\n".join(lines)


def _build_industry_block(industry_comparison: Optional[Dict]) -> str:
    if not industry_comparison:
        return "暂缺 (行业归属数据本次未能取到)"
    name = industry_comparison.get("industry_name")
    sw_name = industry_comparison.get("sw_industry")
    head = f"所属行业: {name}" if name else f"所属申万行业: {sw_name or '未知'}"
    if "advancing" not in industry_comparison:
        # 只给行业名不给统计时必须说清"统计暂缺"，否则模型容易把"知道行业"当成
        # "知道行业整体在涨"的依据。
        return f"{head} (该行业涨跌家数与涨跌幅统计暂缺, 不得据此判断行业普涨/普跌)"
    return (
        f"{head}, "
        f"上涨家数 {industry_comparison.get('advancing')}, "
        f"下跌家数 {industry_comparison.get('declining')}, "
        f"行业涨跌幅 {industry_comparison.get('industry_change_pct')}%"
    )


def _build_market_block(market_context: Optional[Dict]) -> str:
    if not market_context:
        return f"大盘与风格: {_MISSING_TAIL}"

    lines = ["大盘与风格 (指数与个股均为不复权收盘价口径):"]
    for idx in market_context.get("indices") or []:
        seg = []
        for key, label in (("h1_pct", "上半年"), ("h2_pct", "下半年"), ("ytd_pct", "年内")):
            v = idx.get(key)
            seg.append(f"{label} {v:+}%" if v is not None else f"{label} 暂缺")
        lines.append(
            f"  · {idx.get('name')} 最新 {idx.get('latest')} ({idx.get('latest_date')}): "
            + "，".join(seg)
        )

    stock = market_context.get("stock")
    if stock:
        seg = []
        for key, label in (("h1_pct", "上半年"), ("h2_pct", "下半年"), ("ytd_pct", "年内")):
            v = stock.get(key)
            seg.append(f"{label} {v:+}%" if v is not None else f"{label} 暂缺")
        lines.append(
            f"  个股 最新 {stock.get('latest')} ({stock.get('latest_date')}): " + "，".join(seg)
        )
        lines.append(
            f"  个股 52 周区间 {stock.get('w52_low')} ({stock.get('w52_low_date')}) ~ "
            f"{stock.get('w52_high')} ({stock.get('w52_high_date')})"
        )
        lines.append(
            f"  个股 {stock.get('hist_start')} 以来区间 {stock.get('hist_low')} "
            f"({stock.get('hist_low_date')}) ~ {stock.get('hist_high')} ({stock.get('hist_high_date')})"
        )
    else:
        lines.append("  个股行情序列: 暂缺 (不得据此判断该股相对强弱)")
    return "\n".join(lines)


def _build_freight_block(freight_signal: Optional[Dict]) -> str:
    if not freight_signal:
        return f"运价景气度: {_MISSING_TAIL}"

    f = freight_signal
    lines = [f"运价景气度 ({f.get('instrument')}):"]
    ytd = f.get("ytd_pct")
    lines.append(
        f"  最新 {f.get('latest')} 点 ({f.get('latest_date')})"
        + (f"，年内 {ytd:+}%" if ytd is not None else "，年内涨跌 暂缺")
        + f"，当前处于历史 {f.get('hist_pct_rank')}% 分位"
    )
    lines.append("  走势骨架:")
    lines.extend(f"    {m}" for m in (f.get("milestones") or []))
    lines.append(f"  注: {f.get('note')}")
    return "\n".join(lines)


def _build_shareholder_block(
    shareholder_trend: Optional[Dict],
    dividend_history: List[Dict],
    buyback_history: List[Dict],
) -> str:
    lines = []
    if shareholder_trend:
        lines.append(
            f"股东户数: 最新 {shareholder_trend.get('latest_count')} 户 "
            f"(截止 {shareholder_trend.get('as_of')})，环比变化 {shareholder_trend.get('change_pct')}%，"
            f"筹码趋于{'分散' if shareholder_trend.get('trend') == 'increasing' else '集中'}"
        )
    else:
        lines.append("股东户数变化: 暂缺")

    if dividend_history:
        lines.append("历史分红记录:")
        for d in dividend_history[:12]:
            lines.append(
                f"  · {d.get('announce_date')}: 每10股派息 {d.get('dividend_per_10_shares')} 元 ({d.get('progress')})"
            )
    else:
        lines.append("历史分红记录: 暂缺")

    if buyback_history:
        lines.append("历史回购记录:")
        for b in buyback_history[:10]:
            lines.append(
                f"  · {b.get('announce_date')}: 计划金额区间 {b.get('planned_amount_range')}，"
                f"已回购 {b.get('actual_amount')} ({b.get('progress')})"
            )
    else:
        lines.append("历史回购记录: 暂缺")

    return "\n".join(lines)


def _build_commodity_block(commodity_signal: Optional[Dict]) -> str:
    if not commodity_signal:
        return (
            "暂缺 (当前公司未匹配到可靠的产品级商品锚，或对应期货数据获取失败；"
            "不得用其他商品价格替代)"
        )

    lines = [
        f"周期商品路由: {commodity_signal.get('route') or '未知'}",
        f"数据截止: {commodity_signal.get('as_of') or '未知'}",
    ]
    anchors = commodity_signal.get("anchors") or []
    for item in anchors:
        seg = (
            f"  · {item.get('name')} ({item.get('symbol')}): "
            f"{item.get('latest')} {item.get('unit')}"
        )
        if item.get("change_20d_pct") is not None:
            seg += f"，20交易日 {item.get('change_20d_pct'):+}%"
        if item.get("change_60d_pct") is not None:
            seg += f"，60交易日 {item.get('change_60d_pct'):+}%"
        if item.get("position_1y_pct") is not None:
            seg += f"，1年区间位置 {item.get('position_1y_pct')}%"
        lines.append(seg)

    copper = commodity_signal.get("copper_cross_market") or {}
    if copper:
        lines.append(
            f"铜产业额外内外盘背景: 沪铜 {copper.get('sh_copper_price')} "
            f"{copper.get('sh_copper_unit')}；COMEX铜 {copper.get('comex_copper_price')} "
            f"{copper.get('comex_copper_unit')}；人民币趋势 {copper.get('rmb_trend') or '暂缺'}"
        )
        if copper.get("note"):
            lines.append(f"注: {copper.get('note')}")

    if commodity_signal.get("note"):
        lines.append(f"注: {commodity_signal.get('note')}")
    return "\n".join(lines)


def _build_policy_events_block(data: Optional[Dict]) -> str:
    if not data:
        return (
            "暂缺 (近期财经媒体事件线索没有匹配到公司暴露路径；"
            "不得据此反向断言“没有政策/地缘风险”)"
        )
    lines = [
        "以下仅为近期财经媒体/快讯事件线索，不是一手事实；"
        "只能用于提出需要核对的传导路径，不能单独成为看多/看空证据。"
    ]
    for item in (data.get("events") or [])[:14]:
        date_text = str(item.get("published_at") or "日期未知")
        media = str(item.get("media") or "媒体来源未知")
        title = str(item.get("title") or "").strip()
        topics = "、".join(item.get("matched_topics") or [])
        terms = "、".join(item.get("matched_terms") or [])
        line = f"  · {date_text} [{media}] {title}"
        if topics:
            line += f" | 匹配主题: {topics}"
        if terms:
            line += f" | 关键词: {terms}"
        if item.get("url"):
            line += f" | {item.get('url')}"
        lines.append(line)
    if data.get("note"):
        lines.append(f"注: {data.get('note')}")
    return "\n".join(lines)


def _build_macro_rates_block(data: Optional[Dict]) -> str:
    if not data:
        return "暂缺 (本次未取得中美利率数据，不得讨论加息/降息影响)"

    lines: List[str] = []
    us = data.get("us") or {}
    if us:
        ff_fresh = (us.get("fed_target_freshness") or {}).get("fresh", False)
        lo = us.get("fed_target_lower_pct")
        hi = us.get("fed_target_upper_pct")
        if ff_fresh and lo is not None and hi is not None:
            lines.append(
                f"美联储联邦基金目标区间 {lo}%~{hi}% "
                f"(截至 {us.get('fed_target_upper_as_of')}; "
                f"上限较约180日前变化 {us.get('fed_target_upper_change_180d_pp')}pct)"
            )
        else:
            lines.append("美联储目标利率: 数据缺失或过期，不得作为当前利率引用")

        u10_fresh = (us.get("us10y_freshness") or {}).get("fresh", False)
        if u10_fresh and us.get("us10y_yield_pct") is not None:
            lines.append(
                f"美国10年期国债收益率 {us.get('us10y_yield_pct')}% "
                f"(截至 {us.get('us10y_as_of')}; 30日变化 "
                f"{us.get('us10y_change_30d_pp')}pct，90日变化 "
                f"{us.get('us10y_change_90d_pp')}pct)"
            )
        else:
            lines.append("美国10年期国债收益率: 数据缺失或过期")
    else:
        lines.append("美国利率数据: 暂缺")

    china = data.get("china") or {}
    if china:
        fresh = (china.get("freshness") or {}).get("fresh", False)
        if fresh:
            lines.append(
                f"中国LPR: 1年期 {china.get('lpr_1y_pct')}%，5年期 {china.get('lpr_5y_pct')}% "
                f"(截至 {china.get('as_of')}; 近6个观测值变化分别 "
                f"{china.get('lpr_1y_change_6obs_pp')}pct / "
                f"{china.get('lpr_5y_change_6obs_pp')}pct)"
            )
            lines.append(f"注: {china.get('note')}")
        else:
            lines.append("中国LPR: 数据缺失或过期，不得作为当前利率引用")
    else:
        lines.append("中国LPR: 暂缺")

    for warning in data.get("warnings") or []:
        lines.append(f"警告: {warning}")
    return "\n".join(lines)


def _build_fx_block(rmb_signal: Optional[Dict], facts: Optional[Dict]) -> str:
    """
    汇率敞口 = 汇率方向 × 海外收入占比。两者缺一都无法判断顺风逆风：
    只知道人民币升值但不知道有没有海外收入，或只知道海外收入高但不知道汇率方向，
    都不足以给出方向性结论——所以缺任一项时必须写明"暂缺"，不要让模型自己补。
    """
    lines = []
    trend_note = (rmb_signal or {}).get("rmb_trend_note")
    lines.append(
        f"人民币汇率: {trend_note}" if trend_note
        else f"人民币汇率: {_MISSING_TAIL}"
    )
    overseas_pct = (facts or {}).get("overseas_revenue_pct")
    if overseas_pct is None:
        lines.append(f"海外收入占比: {_MISSING_TAIL}")
    else:
        lines.append(
            f"海外收入占比: {overseas_pct}% (汇率敞口的主要来源；占比越高，"
            f"汇率变动对收入/毛利的杠杆越大)"
        )
    if trend_note and overseas_pct is not None:
        lines.append(
            "注: 上述两项均已给出，必须据此给出汇率对该公司收入的影响方向 (顺风/逆风)，"
            "并说明大致传导路径 (折算收入 / 报价竞争力 / 汇兑损益)。"
        )
    return "\n".join(lines)


def _build_profitability_block(profitability_trend: Optional[Dict]) -> str:
    if not profitability_trend:
        return "暂缺 (本次未能取到财务指标数据)"
    lines = [
        profitability_trend.get("gross_margin_trend_note", ""),
        profitability_trend.get("net_margin_trend_note", ""),
        profitability_trend.get("roe_trend_note", ""),
        profitability_trend.get("debt_ratio_trend_note", ""),
    ]
    periods = profitability_trend.get("periods") or []
    if periods:
        lines.append("各报告期明细:")
        for p in periods:
            lines.append(
                f"  · {p.get('period')}: 毛利率/主营业务利润率 {p.get('gross_margin_pct')}%, "
                f"净利率 {p.get('net_margin_pct')}%, ROE {p.get('roe_pct')}%, "
                f"资产负债率 {p.get('debt_ratio_pct')}%, 净利润增长率 {p.get('net_profit_growth_pct')}%"
            )
    return "\n".join(l for l in lines if l)


_MISSING_TAIL = "暂缺 (本次未能取到，该维度不得做任何断言，包括反向断言)"


def _yi(yuan: Optional[float]) -> str:
    """元 -> 亿元。缺失一律写"暂缺"，绝不写 0 或 0.0。"""
    if yuan is None:
        return "暂缺"
    return f"{yuan / 1e8:.2f}亿"


def _pct_str(value: Optional[float]) -> str:
    return "暂缺" if value is None else f"{value}%"


def _build_valuation_block(valuation: Optional[Dict], quote: Optional[Dict]) -> str:
    if not valuation:
        return f"估值快照: {_MISSING_TAIL}"

    lines = [f"估值快照 (数据日期 {valuation.get('valuation_as_of') or '未知'}; 价格随行情变动，比率可能有小幅滞后):"]
    lines.append(
        f"  市盈率(动态) {valuation.get('pe_dynamic')}, 市盈率(静态) {valuation.get('pe_static')}, "
        f"市净率 {valuation.get('pb')}"
    )
    lines.append(
        f"  每股收益 {valuation.get('eps')} 元, 每股净资产 {valuation.get('nav_per_share')} 元, "
        f"每股经营现金流 {valuation.get('ocf_per_share')} 元"
    )
    lines.append(
        f"  净资产收益率 {_pct_str(valuation.get('roe_pct'))}, 毛利率 {_pct_str(valuation.get('gross_margin_pct'))}"
    )
    lines.append(f"  股权质押占A股总股本 {_pct_str(valuation.get('pledge_ratio_pct'))}")

    pe_dyn = valuation.get("pe_dynamic")
    pe_sta = valuation.get("pe_static")
    if pe_dyn is not None and pe_sta is not None and pe_dyn > 0 and pe_sta > pe_dyn:
        lines.append(
            f"  注: 静态市盈率({pe_sta}) 高于动态市盈率({pe_dyn})，说明当前估值已把未来利润增长"
            f"计入价格——增速一旦回落会同时杀业绩和杀估值。"
        )

    float_shares = valuation.get("float_shares")
    price = (quote or {}).get("latest_price")
    if float_shares and price:
        lines.append(
            f"  流通市值 约 {float_shares * float(price) / 1e8:.2f}亿 "
            f"(流通A股 {float_shares / 1e8:.2f}亿股 × 最新价 {price})"
        )
    else:
        lines.append("  流通市值: 暂缺")
    return "\n".join(lines)


def _build_fundamentals_block(fundamentals: Optional[Dict]) -> str:
    if not fundamentals:
        return f"同花顺 F10 结构性事实: {_MISSING_TAIL}"

    facts = fundamentals.get("facts") or {}
    lines = ["同花顺 F10 结构性事实 (来自公司定期报告原文，比率已由程序算好，直接引用即可):"]
    period_line = f"  财务数据期间 {facts.get('finance_period') or '未知'}"
    if fundamentals.get("concentration_period"):
        period_line += (
            f"；客户/供应商集中度期间 {fundamentals['concentration_period']}"
            "(集中度是年报强制披露项，可能滞后最多约 9 个月，引用时要点明期间)"
        )
    lines.append(period_line)

    revenue_yoy = facts.get("revenue_yoy_pct")
    lines.append(
        f"  营业收入 {_yi(facts.get('revenue'))}"
        + (f" (同比 {revenue_yoy:+}%)" if revenue_yoy is not None else "")
    )
    net_profit_yoy = facts.get("net_profit_yoy_pct")
    basis = facts.get("net_profit_basis")
    lines.append(
        f"  净利润 {_yi(facts.get('net_profit'))}"
        + (f" (口径: {basis})" if basis else "")
        + (f" (同比 {net_profit_yoy:+}%)" if net_profit_yoy is not None else "")
    )
    ocf_yoy = facts.get("operating_cash_flow_yoy_pct")
    lines.append(
        f"  经营活动现金流净额 {_yi(facts.get('operating_cash_flow'))}"
        + (f" (同比 {ocf_yoy:+}%)" if ocf_yoy is not None else "")
    )
    lines.append(
        f"  经营现金流/净利润 {facts.get('cash_to_profit_ratio') if facts.get('cash_to_profit_ratio') is not None else '暂缺'}"
        " (显著小于 1 说明账面利润没有同步变成现金)"
    )
    ar = facts.get("accounts_receivable_yuan")
    inv = facts.get("inventory_yuan")
    ar_yoy = facts.get("accounts_receivable_yoy_pct")
    inv_yoy = facts.get("inventory_yoy_pct")
    ar_gap = facts.get("receivable_growth_minus_revenue_pp")
    inv_gap = facts.get("inventory_growth_minus_revenue_pp")
    lines.append(
        f"  应收账款 {_yi(ar)}"
        + (f" (同比 {ar_yoy:+}%)" if ar_yoy is not None else "")
        + (
            f"，应收增速较营收高 {ar_gap:+} 个百分点"
            if ar_gap is not None else ""
        )
        + (
            f"，应收/营收 {facts.get('receivable_to_revenue_pct')}%"
            if facts.get("receivable_to_revenue_pct") is not None else ""
        )
    )
    lines.append(
        f"  存货 {_yi(inv)}"
        + (f" (同比 {inv_yoy:+}%)" if inv_yoy is not None else "")
        + (
            f"，存货增速较营收高 {inv_gap:+} 个百分点"
            if inv_gap is not None else ""
        )
        + (
            f"，存货/营收 {facts.get('inventory_to_revenue_pct')}%"
            if facts.get("inventory_to_revenue_pct") is not None else ""
        )
        + " (季报营收为累计流量，该比率只作营运资金压力代理)"
    )
    lines.append(
        f"  研发投入 {_yi(facts.get('rd_investment_yuan'))}, 研发强度(研发投入/营业收入) "
        f"{_pct_str(facts.get('rd_intensity_pct'))}"
    )
    lines.append(
        f"  前五大客户占营业收入 {_pct_str(facts.get('top5_customer_pct'))}, "
        f"前五大供应商占总采购额 {_pct_str(facts.get('top5_supplier_pct'))}"
    )
    lines.append(
        f"  海外业务收入 {_yi(facts.get('overseas_revenue_yuan'))}, "
        f"占营业收入 {_pct_str(facts.get('overseas_revenue_pct'))}"
    )
    lines.append(f"  境外销量占比 {_pct_str(facts.get('overseas_sales_pct'))}")
    lines.append(
        f"  授权专利 {facts.get('patents_granted') if facts.get('patents_granted') is not None else '暂缺'} 件, "
        f"其中发明专利 {facts.get('patents_invention') if facts.get('patents_invention') is not None else '暂缺'} 件, "
        f"发明专利占比 {_pct_str(facts.get('invention_ratio_pct'))}"
    )
    lines.append(
        f"  股东户数 {facts.get('holder_count_latest') if facts.get('holder_count_latest') is not None else '暂缺'} 户"
        f" (截止 {facts.get('holder_count_period') or '未知'}), "
        f"环比 {_pct_str(facts.get('holder_count_qoq_pct'))}, 同比 {_pct_str(facts.get('holder_count_yoy_pct'))}"
    )

    # 户数序列带同期股价，是"股价暴涨是否伴随筹码派发"唯一可核验的对照数据，
    # 不渲染出来模型就只能靠猜。
    series = facts.get("holder_count_series") or []
    if len(series) >= 2:
        lines.append("  股东户数 vs 同期股价序列 (用于判断股价涨幅与筹码分散是否同步):")
        for point in series[:8]:
            lines.append(
                f"    · {point.get('period')}: 股东户数 {point.get('holders')} 户, 股价 {point.get('price')}"
            )

    customers = fundamentals.get("top_customers") or []
    if customers:
        lines.append("  主要客户明细:")
        for c in customers[:5]:
            lines.append(f"    · {c.get('name')}: 销售额 {_yi(c.get('amount_yuan'))}, 占比 {_pct_str(c.get('pct'))}")
    suppliers = fundamentals.get("top_suppliers") or []
    if suppliers:
        lines.append("  主要供应商明细:")
        for s in suppliers[:5]:
            lines.append(f"    · {s.get('name')}: 采购额 {_yi(s.get('amount_yuan'))}, 占比 {_pct_str(s.get('pct'))}")

    bank = facts.get("bank_industry_metrics")
    if bank:
        lines.append(
            f"  银行资产质量与资本 (行业口径, {bank.get('period')}, 供对比本行水平用): "
            f"商业银行不良贷款余额 {bank.get('industry_npl_balance_trillion')} 万亿元, "
            f"不良贷款率 {bank.get('industry_npl_ratio_pct')}%, "
            f"拨备覆盖率 {bank.get('industry_provision_coverage_pct')}%, "
            f"资本充足率 {bank.get('industry_capital_adequacy_pct')}%"
        )

    risks = (fundamentals.get("self_disclosed_risks") or "").strip()
    lines.append("")
    lines.append("公司自述的风险 (原文摘录: 公司在自己的定期报告里承认的风险，引用时请注明来自公司披露):")
    lines.append(risks if risks else "暂缺 (该公司本期报告的董事会经营评述中没有独立的风险小节)")

    narrative = (fundamentals.get("management_narrative") or "").strip()
    lines.append("")
    lines.append(
        "公司自己的经营表述 (公司管理层视角，属于利益相关方自述，不是客观事实；"
        "只能用于了解公司自己想强调什么，不得单独作为看多理由):"
    )
    lines.append(narrative if narrative else "暂缺")

    if fundamentals.get("missing"):
        lines.append("")
        lines.append(f"本块缺失维度: {', '.join(fundamentals['missing'])} (这些维度不得做任何断言)")

    return "\n".join(lines)


def _build_xueqiu_block(xueqiu_stock: Optional[Dict]) -> str:
    if not xueqiu_stock:
        return f"雪球个股维度 (机构持仓/讨论热度): {_MISSING_TAIL}"

    lines = ["雪球个股维度 (仅聚合数字，不含用户观点原文):"]
    holding = xueqiu_stock.get("org_holding")
    if holding:
        lines.append("  机构/主要股东持仓 (雪球口径):")
        for h in holding[:10]:
            numbers = ", ".join(f"{k}={v}" for k, v in h.items() if k != "name")
            lines.append(f"    · {h.get('name')}: {numbers}")
    else:
        lines.append("  机构持仓: 暂缺")

    quote_detail = xueqiu_stock.get("quote_detail") or {}
    if quote_detail:
        lines.append("  雪球行情扩展字段: " + ", ".join(f"{k}={v}" for k, v in quote_detail.items()))

    discussion = xueqiu_stock.get("discussion") or {}
    if discussion.get("post_count") is not None:
        lines.append(
            f"  讨论热度: 相关帖子约 {discussion['post_count']} 条 "
            f"(仅作情绪/关注度参考，不构成证据；热度高本身既可能意味着分歧也可能意味着拥挤)"
        )
    else:
        lines.append("  讨论热度: 暂缺")

    return "\n".join(lines)


def _build_candidates_block(candidates: List[CandidateOpinion]) -> str:
    lines = []
    for c in candidates:
        lines.append(f"- {c.user_nickname} ({c.credibility_note})")
        if c.historical_thesis:
            lines.append("  历史观点摘录:")
            for t in c.historical_thesis:
                lines.append(f"    · {t[:200]}")
        if c.latest_posts:
            lines.append("  最新发言摘录:")
            for t in c.latest_posts:
                lines.append(f"    · {t[:200]}")
        else:
            lines.append("  (未能获取到最新发言)")
    if not lines:
        lines.append("(暂无)")
    return "\n".join(lines)


def _build_knowledge_block(knowledge_excerpts: List[KnowledgeExcerpt]) -> str:
    lines = []
    for k in knowledge_excerpts:
        link = f" | {k.source_url}" if k.source_url else ""
        lines.append(f"- 《{k.title}》 ({k.source}){link}")
        lines.append(f"    {k.distilled}")
    if not lines:
        lines.append("(暂无背景资料)")
    return "\n".join(lines)


def _build_valuation_history_block(data: Optional[Dict]) -> str:
    if not data:
        return "暂缺 (本次未取得历史估值序列，不能判断PE/PB历史分位)"
    lines = [
        f"数据截止 {data.get('as_of') or '未知'}，当前 PE(TTM) {data.get('current_pe_ttm')}，"
        f"PB {data.get('current_pb')}，历史样本 {data.get('observations')} 个交易日"
    ]
    pe = data.get("pe_percentiles") or []
    pb = data.get("pb_percentiles") or []
    if pe:
        lines.append(
            "PE(TTM) 分位: " + "；".join(
                f"{x.get('years')}年 {x.get('percentile')}% "
                f"(中位 {x.get('median')}, 区间 {x.get('min')}~{x.get('max')})"
                for x in pe
            )
        )
    else:
        lines.append("PE(TTM) 分位: 暂缺/不适用")
    if pb:
        lines.append(
            "PB 分位: " + "；".join(
                f"{x.get('years')}年 {x.get('percentile')}% "
                f"(中位 {x.get('median')}, 区间 {x.get('min')}~{x.get('max')})"
                for x in pb
            )
        )
    else:
        lines.append("PB 分位: 暂缺")
    for note in data.get("notes") or []:
        lines.append(f"注: {note}")
    return "\n".join(lines)


def _build_primary_evidence_block(primary_evidence: List[Dict]) -> str:
    if not primary_evidence:
        return "暂缺 (本次未能取得巨潮公告元数据；治理/资本运作相关结论应降低置信度)"
    lines = []
    for item in primary_evidence[:24]:
        date = item.get("published_at") or "日期未知"
        category = item.get("category") or "公告"
        title = item.get("title") or ""
        url = item.get("url") or ""
        lines.append(f"  · {date} [{category}] {title}" + (f" | {url}" if url else ""))
    return "\n".join(lines)


def _build_a_share_structure_block(data: Optional[Dict]) -> str:
    if not data:
        return "暂缺 (本次未取得公开机构持股/十大流通股东数据，不能推断国家队、公募、险资或外资动向)"
    lines = [f"报告期: {data.get('report_period') or '未知'}"]
    institutions = data.get("institution_summary") or []
    if institutions:
        lines.append("机构持股汇总:")
        for row in institutions:
            ratio = row.get("latest_float_ratio_pct")
            provider_change = row.get("float_ratio_change_pct")
            seg = f"  · {row.get('type')}: {row.get('institutions')} 家"
            if ratio is not None:
                seg += f"，合计占流通股 {ratio}%"
            if provider_change is not None:
                seg += f"，数据源报告增幅合计 {provider_change:+}%"
            lines.append(seg)
    else:
        lines.append("机构持股汇总: 暂缺")

    qoq = data.get("institution_qoq") or []
    if qoq:
        prev = data.get("previous_report_period") or "前一披露期"
        lines.append(f"机构披露快照变化 ({prev} → {data.get('report_period') or '本期'}):")
        for row in qoq:
            seg = f"  · {row.get('type')}: "
            parts = []
            if row.get("float_ratio_change_pp") is not None:
                parts.append(f"占流通股变化 {row.get('float_ratio_change_pp'):+}%")
            if row.get("shares_change_pct") is not None:
                parts.append(f"持股数变化 {row.get('shares_change_pct'):+}%")
            if row.get("institution_count_change") is not None:
                parts.append(f"机构数变化 {row.get('institution_count_change'):+d}")
            lines.append(seg + ("，".join(parts) if parts else "可比数据不足"))

    fund_qoq = data.get("fund_qoq") or {}
    if fund_qoq:
        inc = fund_qoq.get("increased") or []
        dec = fund_qoq.get("decreased") or []
        new = fund_qoq.get("newly_seen") or []
        exited = fund_qoq.get("exited_top_list") or []
        if inc:
            lines.append(
                "公募增持较多: " + "；".join(
                    f"{x.get('name')} "
                    + (
                        f"{x.get('float_ratio_change_pp'):+}%流通股"
                        if x.get("float_ratio_change_pp") is not None
                        else f"持股数变化 {x.get('shares_change_pct'):+}%"
                    )
                    for x in inc[:5]
                )
            )
        if dec:
            lines.append(
                "公募减持较多: " + "；".join(
                    f"{x.get('name')} "
                    + (
                        f"{x.get('float_ratio_change_pp'):+}%流通股"
                        if x.get("float_ratio_change_pp") is not None
                        else f"持股数变化 {x.get('shares_change_pct'):+}%"
                    )
                    for x in dec[:5]
                )
            )
        if new:
            lines.append("本期新见公募: " + "；".join(str(x.get("name")) for x in new[:5]))
        if exited:
            lines.append("本期明细未再见公募: " + "；".join(str(x.get("name")) for x in exited[:5]))

    funds = data.get("fund_details") or []
    if funds:
        lines.append("主要公募基金持仓 (按最新占流通股比例排序):")
        for f in funds[:8]:
            change = f.get("float_ratio_change_pct")
            seg = f"  · {f.get('name')}: 占流通股 {f.get('latest_float_ratio_pct')}%"
            if change is not None:
                seg += f"，数据源报告增幅 {change:+}%"
            lines.append(seg)

    etfs = data.get("etf_details") or []
    if etfs:
        lines.append("可识别ETF持仓:")
        for f in etfs[:8]:
            change = f.get("float_ratio_change_pct")
            seg = f"  · {f.get('name')}: 占流通股 {f.get('latest_float_ratio_pct')}%"
            if change is not None:
                seg += f"，数据源报告增幅 {change:+}%"
            lines.append(seg)
    else:
        lines.append("可识别ETF持仓: 本期机构明细中未识别到ETF名称")

    unlocks = data.get("unlock_supply") or {}
    upcoming = unlocks.get("upcoming_12m") or []
    if upcoming:
        lines.append(
            "未来12个月限售解禁（仅代表潜在供给，不等同于实际卖出）:"
        )
        for x in upcoming[:8]:
            lines.append(
                f"  · {x.get('date')}: 解禁数量 {x.get('unlock_shares')} 股，"
                f"占流通市值比例 {x.get('float_market_ratio_pct')}%，"
                f"类型 {x.get('type') or '未知'}"
            )
    else:
        lines.append("未来12个月限售解禁: 未见记录或数据暂缺")
    recent_unlock = unlocks.get("recent_6m") or []
    if recent_unlock:
        lines.append("近6个月已发生解禁:")
        for x in recent_unlock[:5]:
            lines.append(
                f"  · {x.get('date')}: 实际解禁 {x.get('actual_unlock_shares')} 股，"
                f"占流通市值比例 {x.get('float_market_ratio_pct')}%"
            )

    special = data.get("special_holders") or {}
    labels = {
        "national_team": "汇金/证金/国新/诚通等国家资本",
        "social_security": "社保基金",
        "insurance": "保险资金",
        "foreign": "香港中央结算/QFII等境外资金",
        "public_fund": "公募基金",
    }
    lines.append("前十大流通股东中特殊资金:")
    for key, label in labels.items():
        holders = special.get(key) or []
        if not holders:
            lines.append(f"  · {label}: 本期前十大未见")
            continue
        text = "；".join(
            f"{h.get('name')} {h.get('float_ratio_pct')}%"
            if h.get("float_ratio_pct") is not None else str(h.get("name"))
            for h in holders[:5]
        )
        lines.append(f"  · {label}: {text}")
    for note in data.get("notes") or []:
        lines.append(f"注: {note}")
    return "\n".join(lines)


def _build_research_quality_block(quality: Optional[ResearchQuality]) -> str:
    if quality is None:
        return "证据时点审计: 暂缺"
    lines = [
        f"证据时点审计: 当前有效覆盖 {quality.covered_dimensions}/{quality.total_dimensions} "
        f"({quality.coverage * 100:.0f}%)"
    ]
    if quality.stale_evidence:
        lines.append("以下时点型证据已过新鲜度阈值，只能作历史背景，禁止当作当前状态:")
        for item in quality.stale_evidence:
            lines.append(
                f"  · {item.get('label')}: 截止 {item.get('as_of')}，"
                f"距今 {item.get('age_days')} 天，阈值 {item.get('max_age_days')} 天"
            )
    else:
        lines.append("未发现已过新鲜度阈值的时点型证据。")
    if quality.missing_dimensions:
        lines.append("当前证据缺口: " + "、".join(quality.missing_dimensions))
    return "\n".join(lines)


def _build_time_contract_block(inputs: Any) -> str:
    mode = str(getattr(inputs, "research_mode", "live") or "live")
    as_of = str(getattr(inputs, "as_of", "") or "")
    if mode == "historical":
        return (
            f"模式=historical，研究截止日={as_of or '未知'}。\n"
            "你必须把自己限制在这个截止日：只能使用本次输入数据块中明确给出的证据。"
            "严禁使用模型记忆、常识库或后见之明补入截止日之后发生的股价、财报、公告、"
            "分红实施结果、机构持仓、政策、战争、产品进展或任何事件。\n"
            "若某个维度因历史源缺失而标注暂缺，就保持暂缺；不得用今天知道的结果倒推。"
            "正文禁止使用“后来证明”“随后发生”“最终实现”等超出截止日视角的措辞。"
        )
    return (
        f"模式=live，研究基准日={as_of or '当前日期'}。"
        "仍然只能引用本次输入的数据块，不得凭模型记忆补数字或未提供的公司事实。"
    )


def _build_prompt(inputs: Any, candidates: List[CandidateOpinion]) -> str:
    quote = inputs.quote
    if quote:
        quote_line = (
            f"最新价 {quote.get('latest_price')}, "
            f"涨跌幅 {quote.get('change_pct')}%, 成交量 {quote.get('volume')}"
        )
    else:
        quote_line = "暂无实时数据"

    return _PROMPT_TEMPLATE.format(
        time_contract_block=_build_time_contract_block(inputs),
        stock_code=inputs.stock_code,
        stock_name=inputs.stock_name or inputs.stock_code,
        quote_line=quote_line,
        research_profile_block=profile_prompt_block(
            inputs.research_profile or ResearchProfile()
        ),
        research_quality_block=_build_research_quality_block(inputs.research_quality),
        valuation_block=_build_valuation_block(inputs.valuation, quote),
        valuation_history_block=_build_valuation_history_block(inputs.valuation_history),
        fundamentals_block=_build_fundamentals_block(inputs.fundamentals),
        rd_block=_build_rd_block(inputs.fundamentals, inputs.executive_profile, inputs.rd_team),
        primary_evidence_block=_build_primary_evidence_block(inputs.primary_evidence),
        major_events_block=_build_major_events_block(inputs.major_events),
        management_capital_block=_build_management_capital_block(inputs.management_capital),
        governance_block=_build_governance_block(
            inputs.refinancing_history, inputs.executive_profile, inputs.governance_alerts
        ),
        fx_block=_build_fx_block(inputs.rmb_signal, (inputs.fundamentals or {}).get("facts")),
        macro_rates_block=_build_macro_rates_block(inputs.macro_rates),
        policy_events_block=_build_policy_events_block(inputs.policy_events),
        xueqiu_block=_build_xueqiu_block(inputs.xueqiu_stock),
        debate_block=_build_debate_block(inputs.debate),
        sentiment_block=_build_sentiment_block(inputs.sentiment),
        candidates_block=_build_candidates_block(candidates),
        knowledge_block=_build_knowledge_block(inputs.knowledge_excerpts),
        industry_block=_build_industry_block(inputs.industry_comparison),
        market_block=_build_market_block(inputs.market_context),
        shareholder_block=_build_shareholder_block(
            inputs.shareholder_trend, inputs.dividend_history, inputs.buyback_history
        ),
        margin_block=_build_margin_block(inputs.margin_signal, inputs.valuation, inputs.quote),
        a_share_structure_block=_build_a_share_structure_block(inputs.a_share_structure),
        kb_fund_flow_block=_build_kb_fund_flow_block(inputs.knowledge_excerpts),
        profitability_block=_build_profitability_block(inputs.profitability_trend),
        commodity_block=_build_commodity_block(inputs.commodity_signal),
        freight_block=_build_freight_block(inputs.freight_signal),
    )


