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
用户观点摘录 (digest): 对每个用户的历史验证记录做离线聚合与提炼，
生成可直接用于报告/问答的总结文件。

- build: 加载全部验证记录, 按 (user_id, stock_code) 分组。两类内容都会进摘录:
  1. verdict=correct 的预测论据 ("被后续股价走势验证正确"的判断);
  2. verdict=view 的观点记录 (宏观/行业/估值/买卖操作及理由等无方向的观点,
     不做走势验证, 但同样是该用户有价值的看法)。
  多条内容时用 LLM 提炼成一段连贯观点摘要 (按内容哈希缓存, 内容不变不重复
  调用 LLM), 并要求标注哪些判断已被走势验证; 只有一条时直接用原文。
- 产物: data/xueqiu/digest/user_digests.jsonl, 每行一个 (user, stock) 条目:
  {user_id, user_nickname, stock_code, stock_name, correct, incorrect, total,
   hit_rate, summary, theses, views, last_verified_at}
- load: 进程内缓存, 供报告生成按股票查询、问答按用户查询。
"""

import asyncio
import hashlib
import json
import os
from typing import Dict, List, Optional

import config
from backtest import score
from backtest.llm_client import call_text
from tools.time_util import get_date_str_from_unix_time
from tools.utils import utils

_DISTILL_CONCURRENCY = 3

# 知识库来源 -> 雪球用户映射: B 站直播文字稿是老木匠的发言，其观点摘录
# 并入该用户名下 (来源会标注在 view 条目里，与雪球验证记录区分)。
_KB_USER_MAP = {
    "bili_laomujiang": ("3058599833", "买股票的老木匠"),
}

_DISTILL_PROMPT = """以下是一位雪球用户"{nickname}"关于"{stock_name}"({stock_code})的历史内容，分两类：
1. 已验证的预测: 发布后被后续股价走势验证为正确
2. 其他观点: 宏观/行业/估值/买卖操作及理由等，未经走势验证

请整合成一段**完整保留推理链条**的观点总结，读者要能被说服：
- 每条内容都要保留三要素: 背景 (什么时期、什么市场环境)、论点 (作者主张什么)、论据 (作者引用的具体数字与逻辑链)；
- 论据必须保留原文中的关键数字 (如成本价、市净率、股息率、产能数据)，禁止压缩成"估值低""基本面好"这类空洞表述；
- 已验证的预测明确标注"该判断已被后续走势验证"；
- 同一主题的多次表态合并陈述，但不要丢失时间演进 (先看好→后转谨慎这类变化要写出来)。
800字以内，要点式中文。直接输出内容本身，不要"以下是"之类的说明文字，也不要自我介绍。

已验证的预测 (按时间排列):
{theses_block}

其他观点 (按时间排列):
{views_block}"""


def _digest_dir() -> str:
    base = config.SAVE_DATA_PATH if config.SAVE_DATA_PATH else "data"
    return os.path.join(base, "xueqiu", "digest")


def _digest_path() -> str:
    return os.path.join(_digest_dir(), "user_digests.jsonl")


def _distill_cache_path() -> str:
    return os.path.join(_digest_dir(), "distill_cache.jsonl")


def _group_hash(key_parts: str) -> str:
    return hashlib.sha256(key_parts.encode("utf-8")).hexdigest()[:16]


def _read_distill_cache() -> Dict[str, Dict]:
    """content_hash -> {content_hash, summary}"""
    cache: Dict[str, Dict] = {}
    path = _distill_cache_path()
    if not os.path.exists(path):
        return cache
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("content_hash") or rec.get("cache_key")
                if key:
                    cache[key] = rec
    except (OSError, json.JSONDecodeError) as e:
        utils.logger.error(f"[digest] 读取提炼缓存失败: {e}")
    return cache


def _append_distill_cache(rec: Dict) -> None:
    os.makedirs(os.path.dirname(_distill_cache_path()), exist_ok=True)
    with open(_distill_cache_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _kb_views_cache_path() -> str:
    return os.path.join(_digest_dir(), "kb_views_cache.jsonl")


def _read_kb_views_cache() -> Dict[str, List[Dict]]:
    """content_hash -> 该知识库提炼文本检测出的观点列表"""
    cache: Dict[str, List[Dict]] = {}
    path = _kb_views_cache_path()
    if not os.path.exists(path):
        return cache
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("content_hash") and int(rec.get("schema_version") or 1) >= 2:
                    cache[rec["content_hash"]] = rec.get("views") or []
    except (OSError, json.JSONDecodeError) as e:
        utils.logger.error(f"[digest] 读取知识库观点缓存失败: {e}")
    return cache


def _append_kb_views_cache(rec: Dict) -> None:
    os.makedirs(os.path.dirname(_kb_views_cache_path()), exist_ok=True)
    with open(_kb_views_cache_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def _detect_kb_views() -> List[Dict]:
    """
    把知识库 (B 站直播文字稿等) 的提炼条目过一遍股票观点检测 (与雪球无标签
    帖子同一套 classify 逻辑)，产出伪记录合并进摘录分组。检测结果按内容哈希
    缓存，提炼文本不变不重复调用 LLM。
    """
    from analysis.knowledge_base import ensure_loaded as ensure_kb
    from backtest.classify import classify_post

    entries = await ensure_kb()
    entries = [e for e in entries if e.source in _KB_USER_MAP and (e.distilled or "").strip()]
    if not entries:
        return []
    cache = _read_kb_views_cache()
    semaphore = asyncio.Semaphore(_DISTILL_CONCURRENCY)

    async def _one(e) -> List[Dict]:
        text = (e.distilled or "").strip()
        h = _group_hash(text)
        if h in cache:
            return cache[h]
        uid, nick = _KB_USER_MAP[e.source]
        async with semaphore:
            try:
                preds = await classify_post(
                    {
                        "post": {
                            "status_id": f"kb-{h}",
                            "status_type": "original",
                            "created_at": int(e.timestamp or 0),  # 已是毫秒
                            "user_id": uid,
                            "user_nickname": nick,
                        },
                        "text": text,
                        "stocks": [],
                    }
                )
            except Exception as exc:
                utils.logger.warning(f"[digest] 知识库条目观点检测失败 ({e.title[:30]}): {exc}")
                return []
        recs = [
            {
                "stock_code": p["stock_code"],
                "stock_name": p["stock_name"],
                "direction": p["direction"],
                "thesis": p["thesis"],
                "evidence": p.get("evidence", ""),
                "market_context": p.get("market_context", ""),
            }
            for p in preds
        ]
        _append_kb_views_cache({"content_hash": h, "views": recs, "schema_version": 2})
        return recs

    results = await asyncio.gather(*(_one(e) for e in entries))
    pseudo: List[Dict] = []
    for e, recs in zip(entries, results):
        uid, nick = _KB_USER_MAP[e.source]
        for r in recs:
            pseudo.append(
                {
                    "user_id": uid,
                    "user_nickname": nick,
                    "stock_code": r["stock_code"],
                    "stock_name": r["stock_name"],
                    "direction": r.get("direction", ""),
                    "thesis": r["thesis"],
                    "evidence": r.get("evidence", ""),
                    "market_context": r.get("market_context", ""),
                    "predicted_at": int(e.timestamp or 0),  # 已是毫秒
                    "verified_at": 0,
                    "verdict": "view",
                    "view_source": f"bili:{e.source}",
                }
            )
    if pseudo:
        utils.logger.info(f"[digest] 知识库观点检测: {len(pseudo)} 条 (覆盖 {len(entries)} 条提炼文本)")
    return pseudo


def _date_str(unix_sec_or_ms: int) -> str:
    if not unix_sec_or_ms:
        return ""
    try:
        return get_date_str_from_unix_time(unix_sec_or_ms)
    except Exception:
        return ""


async def _distill_group(
    user_nickname: str, stock_code: str, stock_name: str, theses: List[Dict], views: List[Dict]
) -> str:
    """把同一用户对同一只股票的已验证预测与其他观点提炼成一段观点总结 (带哈希缓存)。"""
    def _item_block(items) -> str:
        lines = []
        for t in items:
            head = f"- [{t.get('date', '')}] {t.get('direction', '')}"
            if t.get("source"):
                head += f" (来源:{'B站直播' if t.get('source', '').startswith('bili') else '雪球'})"
            lines.append(head)
            lines.append(f"  论点: {t.get('thesis', '')}")
            if t.get("evidence"):
                lines.append(f"  论据: {t.get('evidence', '')}")
            if t.get("context"):
                lines.append(f"  背景: {t.get('context', '')}")
        return "\n".join(lines) or "(无)"

    parts = _item_block(theses) + "\n" + _item_block(views)
    content_hash = _group_hash(parts)
    cache = _read_distill_cache()
    if content_hash in cache and cache[content_hash].get("summary"):
        return cache[content_hash]["summary"]

    prompt = _DISTILL_PROMPT.format(
        nickname=user_nickname,
        stock_name=stock_name or stock_code,
        stock_code=stock_code,
        theses_block=_item_block(theses),
        views_block=_item_block(views),
    )
    summary = (await call_text(prompt, max_tokens=1600) or "").strip()
    if summary:
        _append_distill_cache({"content_hash": content_hash, "summary": summary})
    else:
        utils.logger.warning(
            f"[digest] {user_nickname}({stock_code}) 观点提炼失败, 回退为原始论据拼接"
        )
        summary = "；".join(
            [t.get("thesis", "") for t in theses] + [v.get("thesis", "") for v in views]
        )
    return summary


async def build_digests(
    records: Optional[List[Dict]] = None, include_kb: bool = True
) -> List[Dict]:
    """
    从验证记录聚合出每个 (用户, 股票) 的观点摘录条目并原子写入 digest 文件。
    records 为 None 时加载全部验证记录；include_kb=True 时把知识库 (B 站直播
    文字稿等) 提炼文本里检测出的观点作为 view 并入对应用户名下。返回写入的
    条目列表。
    """
    if records is None:
        records = score.load_records()
    records = list(records)
    if include_kb:
        records.extend(await _detect_kb_views())

    groups: Dict[str, Dict] = {}
    for r in records:
        key = (str(r.get("user_id") or ""), str(r.get("stock_code") or ""))
        if not key[0] or not key[1]:
            continue
        g = groups.setdefault(key, {"correct": [], "incorrect": 0, "views": [], "meta": r})
        if r.get("verdict") == "correct":
            g["correct"].append(r)
        elif r.get("verdict") == "incorrect":
            g["incorrect"] += 1
        elif r.get("verdict") == "view":
            g["views"].append(r)

    entries: List[Dict] = []
    semaphore = asyncio.Semaphore(_DISTILL_CONCURRENCY)

    async def _one(key) -> Optional[Dict]:
        user_id, stock_code = key
        g = groups[key]
        correct_recs = g["correct"]
        view_recs = g["views"]
        if not correct_recs and not view_recs:
            return None  # 既无命中预测也无观点记录的用户-股票组不进摘录文件
        meta = g["meta"]
        # 已验证论据去重: 同日同方向的近似重复 (同一观点多次发帖) 只留最长一条
        seen_thesis = set()
        best_by_daydir: Dict[str, str] = {}
        for r in sorted(correct_recs, key=lambda x: x.get("predicted_at") or 0):
            t = (r.get("thesis") or "").strip()
            if not t or t in seen_thesis:
                continue
            seen_thesis.add(t)
            key2 = (r.get("predicted_at") or 0) // 86400000, r.get("direction", "")
            prev = best_by_daydir.get(key2)
            if prev is None or len(t) > len(prev):
                best_by_daydir[key2] = t
        theses: List[Dict] = []
        for r in sorted(correct_recs, key=lambda x: x.get("predicted_at") or 0):
            t = (r.get("thesis") or "").strip()
            key2 = (r.get("predicted_at") or 0) // 86400000, r.get("direction", "")
            if best_by_daydir.get(key2) != t:
                continue
            best_by_daydir[key2] = ""  # 该组只取一次
            theses.append(
                {
                    "date": _date_str(r.get("predicted_at") or 0),
                    "direction": r.get("direction", ""),
                    "thesis": t,
                    "evidence": (r.get("evidence") or "").strip(),
                    "context": (r.get("market_context") or "").strip(),
                }
            )
        # 观点型记录去重 (按文本); 来源标注区分雪球发言与 B 站直播稿
        seen_view = set()
        views: List[Dict] = []
        for r in sorted(view_recs, key=lambda x: x.get("predicted_at") or 0):
            t = (r.get("thesis") or "").strip()
            if not t or t in seen_view:
                continue
            seen_view.add(t)
            views.append(
                {
                    "date": _date_str(r.get("predicted_at") or 0),
                    "direction": r.get("direction", ""),
                    "thesis": t,
                    "evidence": (r.get("evidence") or "").strip(),
                    "context": (r.get("market_context") or "").strip(),
                    "source": r.get("view_source") or "xueqiu",
                }
            )
        if not theses and not views:
            return None
        if len(theses) + len(views) == 1:
            item = theses[0] if theses else views[0]
            summary = item["thesis"]
            if item.get("evidence") and item["evidence"] != summary:
                summary += f"。论据: {item['evidence']}"
        else:
            async with semaphore:
                summary = await _distill_group(
                    meta.get("user_nickname", ""), stock_code, meta.get("stock_name", ""),
                    theses, views,
                )
        total = len(correct_recs) + g["incorrect"]
        return {
            "user_id": user_id,
            "user_nickname": meta.get("user_nickname", ""),
            "stock_code": stock_code,
            "stock_name": meta.get("stock_name", ""),
            "correct": len(correct_recs),
            "incorrect": g["incorrect"],
            "total": total,
            "hit_rate": round(len(correct_recs) / total, 4) if total else 0.0,
            "summary": summary,
            "theses": theses,
            "views": views,
            "last_verified_at": max((r.get("verified_at") or 0 for r in correct_recs), default=0),
        }

    results = await asyncio.gather(*(_one(k) for k in groups))
    entries = [e for e in results if e is not None]
    entries.sort(key=lambda e: (e["user_id"], -e["correct"], e["stock_code"]))

    os.makedirs(_digest_dir(), exist_ok=True)
    tmp_path = _digest_path() + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp_path, _digest_path())

    invalidate()
    utils.logger.info(f"[digest] 摘录文件已更新: {len(entries)} 个 (用户, 股票) 条目 -> {_digest_path()}")
    return entries


_cache: Optional[List[Dict]] = None


def invalidate() -> None:
    """摘录文件重建后清空进程内缓存，下次 load 时重新读取。"""
    global _cache
    _cache = None


def load_digests() -> List[Dict]:
    """加载摘录文件 (进程内缓存)。文件不存在时返回空列表。"""
    global _cache
    if _cache is not None:
        return _cache
    _cache = []
    path = _digest_path()
    if not os.path.exists(path):
        return _cache
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                _cache.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as e:
        utils.logger.error(f"[digest] 读取摘录文件失败: {e}")
        _cache = []
    return _cache


def views_for_stock(stock_code: str) -> List[Dict]:
    """某只股票的全部用户观点摘录，按命中率降序 (预测正确即有价值的观点优先)。"""
    views = [e for e in load_digests() if e.get("stock_code") == stock_code]
    views.sort(key=lambda e: (-e.get("hit_rate", 0.0), -e.get("correct", 0)))
    return views


def views_for_user_stock(user_id: str, stock_code: str) -> List[Dict]:
    """某用户对某只股票的观点摘录条目 (通常 0 或 1 条)。"""
    return [
        e
        for e in load_digests()
        if str(e.get("user_id") or "") == str(user_id) and e.get("stock_code") == stock_code
    ]


def views_for_user(user_id: str) -> List[Dict]:
    """某用户对其谈论过的全部股票的观点摘录，按命中数降序。"""
    views = [e for e in load_digests() if str(e.get("user_id") or "") == str(user_id)]
    views.sort(key=lambda e: (-e.get("correct", 0), e.get("stock_code") or ""))
    return views
