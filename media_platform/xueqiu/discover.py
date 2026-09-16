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
用户发现模块 — 检索粉丝数 >= 阈值的用户。

来源:
  1. 已爬取帖子数据 (creator_*_contents_*.jsonl):
     每条帖子内嵌 author 的 user 对象, 转发帖还内嵌 retweeted_status.user
     对象, 均带 followers_count 字段 —— 可直接按粉丝数过滤。
  2. 用户主页 "用户推荐" 列表 (爬取过程中顺带收集):
     只有用户 ID 和昵称, 无粉丝数; 发现阶段为候选用户抓取一页
     timeline 拿到 user 对象后补全粉丝数。
"""

import glob
import json
import os
from typing import Dict, List, Optional, Set

import config
from tools import utils


def _discover_dir() -> str:
    base = config.SAVE_DATA_PATH or "data"
    return os.path.join(base, "xueqiu", "discover")


def discover_file_path() -> str:
    return os.path.join(_discover_dir(), "discovered_users.json")


def _data_jsonl_dir() -> str:
    return os.path.join(config.SAVE_DATA_PATH or "data", "xueqiu", "jsonl")


def _user_dict(user_obj: Dict, source: str) -> Optional[Dict]:
    """从 user 对象提取发现所需的字段。"""
    if not user_obj:
        return None
    uid = str(user_obj.get("id") or "")
    if not uid:
        return None
    return {
        "user_id": uid,
        "screen_name": user_obj.get("screen_name", "") or "",
        "followers_count": int(user_obj.get("followers_count") or 0),
        "status_count": int(user_obj.get("status_count") or 0),
        "description": (user_obj.get("description", "") or "")[:100],
        "profile": user_obj.get("profile", "") or f"/{uid}",
        "source": source,
    }


def discover_users_from_data(min_followers: int = 1000) -> List[Dict]:
    """
    扫描所有已爬取的帖子数据, 提取粉丝数 >= min_followers 的用户。

    Returns:
        去重后的用户列表 (按粉丝数降序)
    """
    users: Dict[str, Dict] = {}
    pattern = os.path.join(_data_jsonl_dir(), "creator_*_contents_*.jsonl")
    files = glob.glob(pattern)
    utils.logger.info(f"[Discover] 扫描 {len(files)} 个帖子数据文件 ...")
    for path in files:
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
                    # 帖子作者 (新数据带 user_followers_count 字段)
                    if item.get("user_followers_count"):
                        d = _user_dict(
                            {
                                "id": item.get("user_id", ""),
                                "screen_name": item.get("user_nickname", ""),
                                "followers_count": item.get("user_followers_count"),
                                "status_count": 0,
                                "description": "",
                                "profile": "",
                            },
                            "post_author",
                        )
                        if d:
                            users[d["user_id"]] = d
                    # 转发原帖作者 (retweeted_status 内嵌完整 user 对象)
                    retweeted = None
                    try:
                        raw = item.get("retweeted_status") or ""
                        if raw:
                            retweeted = json.loads(raw)
                    except Exception:
                        retweeted = None
                    if retweeted and retweeted.get("user"):
                        d = _user_dict(retweeted["user"], "retweet")
                        if d:
                            users[d["user_id"]] = d
        except Exception as e:
            utils.logger.warning(f"[Discover] 扫描 {path} 失败: {e}")

    result = [u for u in users.values() if u["followers_count"] >= min_followers]
    result.sort(key=lambda u: -u["followers_count"])
    utils.logger.info(f"[Discover] 数据中命中粉丝数>={min_followers} 的用户: {len(result)} 个")
    return result


def load_candidates() -> List[Dict]:
    """加载主页 "用户推荐" 收集的候选用户 (无粉丝数)。"""
    try:
        with open(discover_file_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("candidates", [])
    except Exception:
        return []


def save_candidates(candidates: List[Dict]) -> None:
    """保存候选用户 (增量合并)。"""
    existing = {c.get("user_id"): c for c in load_candidates()}
    for c in candidates:
        if c.get("user_id"):
            existing[c["user_id"]] = c
    _save_discover_data(existing.values())


def _save_discover_data(candidates) -> None:
    import pathlib
    path = discover_file_path()
    pathlib.Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
    data = {"candidates": list(candidates)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_discovered() -> List[Dict]:
    """加载已发现/已抓取过的发现用户列表。"""
    try:
        with open(discover_file_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("discovered", [])
    except Exception:
        return []


def save_discovered(users: List[Dict]) -> None:
    """保存发现用户列表 (增量合并, 保留已抓取状态)。"""
    import pathlib
    path = discover_file_path()
    pathlib.Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    merged = {u.get("user_id"): u for u in data.get("discovered", [])}
    for u in users:
        if u.get("user_id"):
            merged[u["user_id"]] = {**merged.get(u["user_id"], {}), **u}
    data["discovered"] = sorted(
        merged.values(), key=lambda u: -u.get("followers_count", 0)
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def already_crawled_user_ids() -> Set[str]:
    """返回已有断点记录 (已抓取过) 的用户 ID 集合。"""
    base = config.SAVE_DATA_PATH or "data"
    resume_dir = os.path.join(base, "xueqiu", "resume")
    ids = set()
    for path in glob.glob(os.path.join(resume_dir, "resume_*.json")):
        name = os.path.basename(path)
        uid = name[len("resume_"):-len(".json")]
        if uid:
            ids.add(uid)
    return ids
