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
股票投资助手 web 服务: 提交股票代码 -> 异步任务 (后台抓取+LLM) -> 轮询结果。

用法:
    uvicorn web.app:app --reload
    浏览器打开 http://127.0.0.1:8000
"""

import asyncio
import glob
from datetime import date
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from analysis.knowledge_base import (
    ensure_loaded as ensure_knowledge_base_loaded,
    invalidate_cache as invalidate_knowledge_base_cache,
)
from analysis.realtime_price import resolve_stock_code
from analysis.report import generate_report
from analysis.research_context import (
    ResearchRequest,
    assert_request_supported,
    historical_readiness,
    SOURCE_TEMPORAL_CAPABILITIES,
)
from media_platform.xueqiu.help import normalize_user_id
from tools.utils import utils

app = FastAPI(title="股票投资助手")

_tasks: Dict[str, Dict] = {}

_PROJECT_ROOT = Path(__file__).parent.parent
_CRAWL_LOG_TAIL_LINES = 200

_crawl_tasks: Dict[str, Dict] = {}
_crawl_lock = asyncio.Lock()


@app.on_event("startup")
async def _preload_knowledge_base() -> None:
    # 后台提炼 (含雪球用户发帖来源, 首次要逐条过 LLM, 会持续一段时间);
    # 不阻塞服务启动, 提炼完成的条目逐批进入缓存
    asyncio.create_task(ensure_knowledge_base_loaded())


class AnalyzeRequest(BaseModel):
    stock_code: str
    mode: str = "live"
    as_of: Optional[date] = None
    save_snapshot: bool = False


class AnalyzeResponse(BaseModel):
    task_id: str


async def _run_analysis(
    task_id: str,
    stock_code: str,
    research_request: ResearchRequest,
) -> None:
    _tasks[task_id]["status"] = "running"
    try:
        report = await generate_report(stock_code, request=research_request)
        _tasks[task_id]["status"] = "done"
        _tasks[task_id]["result"] = report.model_dump()
    except Exception as e:
        utils.logger.error(f"[web.app] 分析任务 {task_id} ({stock_code}) 失败: {e}")
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = str(e)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    raw_input = req.stock_code.strip()
    if not raw_input:
        raise HTTPException(status_code=400, detail="stock_code 不能为空")

    stock_code = await resolve_stock_code(raw_input)
    if not stock_code:
        raise HTTPException(status_code=404, detail=f"未找到股票: {raw_input}")

    try:
        research_request = ResearchRequest(
            stock_code=stock_code,
            mode=req.mode,
            as_of=req.as_of,
            save_snapshot=req.save_snapshot,
        )
        assert_request_supported(research_request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except NotImplementedError as e:
        readiness = historical_readiness()
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(e),
                "historical_ready": readiness.ready,
                "safe_sources": readiness.safe_sources,
                "blocking_sources": readiness.blocking_sources,
                "reasons": readiness.reasons,
            },
        ) from e

    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "request": research_request.model_dump(mode="json"),
    }
    asyncio.create_task(_run_analysis(task_id, stock_code, research_request))
    return AnalyzeResponse(task_id=task_id)


@app.get("/api/research/time-capabilities")
async def get_research_time_capabilities() -> Dict:
    readiness = historical_readiness()
    return {
        "historical_ready": readiness.ready,
        "safe_sources": readiness.safe_sources,
        "blocking_sources": readiness.blocking_sources,
        "reasons": readiness.reasons,
        "sources": {
            name: item.model_dump(mode="json")
            for name, item in SOURCE_TEMPORAL_CAPABILITIES.items()
        },
    }


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> Dict:
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task_id 不存在")
    return {"status": task["status"], "result": task["result"], "error": task["error"]}


class CrawlXueqiuRequest(BaseModel):
    user_id: str
    incremental: bool = True
    auto_backtest: bool = True


class CrawlBiliOpusRequest(BaseModel):
    creator_id: str


class CrawlTaskResponse(BaseModel):
    task_id: str


def _crawl_task_is_running() -> bool:
    return any(t["status"] == "running" for t in _crawl_tasks.values())


def _decode_subprocess_line(raw: bytes) -> str:
    """子进程 stdout 在 Windows 管道下默认按 locale (GBK) 编码输出，
    先按 UTF-8 试、失败回退 GB18030，避免日志出现乱码。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


async def _run_cmd(cmd: list, log_lines: list) -> int:
    """跑一个子进程命令, 输出追加进 log_lines, 返回退出码 (-1 表示启动失败)。"""
    try:
        # 强制子进程输出 UTF-8 (Windows 管道默认 GBK, 父进程解码容易乱码)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_PROJECT_ROOT),
            env=env,
        )
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            log_lines.append(_decode_subprocess_line(line).rstrip())
            if len(log_lines) > _CRAWL_LOG_TAIL_LINES:
                del log_lines[: len(log_lines) - _CRAWL_LOG_TAIL_LINES]
        return await process.wait()
    except Exception as e:
        utils.logger.error(f"[web.app] 子进程运行失败: {e}")
        log_lines.append(f"启动/运行子进程失败: {e}")
        return -1


async def _run_crawler_subprocess(task_id: str, cmd: list) -> None:
    _crawl_tasks[task_id]["status"] = "running"
    log_lines: list = _crawl_tasks[task_id]["log_lines"]
    _crawl_tasks[task_id]["process"] = cmd

    returncode = await _run_cmd(cmd, log_lines)
    chain = _crawl_tasks[task_id].get("chain")
    if returncode == 0 and chain:
        # 兼容旧的一条后续命令，也支持多条命令串行执行 (如 B站专栏 -> 视频字幕)。
        commands = chain if chain and isinstance(chain[0], (list, tuple)) else [chain]
        for idx, next_cmd in enumerate(commands, start=1):
            log_lines.append("=" * 50)
            log_lines.append(f"[web.app] 自动执行后续任务 {idx}/{len(commands)}")
            log_lines.append("=" * 50)
            returncode = await _run_cmd(list(next_cmd), log_lines)
            if returncode != 0:
                break
    if returncode == 0:
        # 原始知识语料可能变化；清掉进程内列表缓存。逐条蒸馏仍按内容哈希复用，
        # 所以下次分析只会为新增/变化内容调用 LLM。
        invalidate_knowledge_base_cache()
    _crawl_tasks[task_id]["status"] = "done" if returncode == 0 else "failed"


async def _start_crawl_task(cmd: list, chain: Optional[list] = None) -> str:
    async with _crawl_lock:
        if _crawl_task_is_running():
            raise HTTPException(status_code=400, detail="已有抓取任务在运行，请稍候")
        task_id = str(uuid.uuid4())
        _crawl_tasks[task_id] = {"status": "pending", "log_lines": [], "process": None, "chain": chain}
        asyncio.create_task(_run_crawler_subprocess(task_id, cmd))
        return task_id


@app.post("/api/crawl/xueqiu", response_model=CrawlTaskResponse)
async def crawl_xueqiu(req: CrawlXueqiuRequest) -> CrawlTaskResponse:
    user_id = req.user_id.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id 不能为空")

    uid = normalize_user_id(user_id)
    cmd = [
        sys.executable, "main.py",
        "--platform", "xueqiu",
        "--lt", "qrcode",
        "--type", "creator",
        "--creator_id", uid,
        "--update", "true" if req.incremental else "false",
    ]
    # 抓取成功后自动回测 (LLM 提取观点与预测 -> 验证 -> 摘要文件自动重建)
    chain = None
    if req.auto_backtest:
        chain = [sys.executable, "backtest_run.py", "--creator_id", uid]
    task_id = await _start_crawl_task(cmd, chain=chain)
    return CrawlTaskResponse(task_id=task_id)


@app.post("/api/crawl/bili_opus", response_model=CrawlTaskResponse)
async def crawl_bili_opus(req: CrawlBiliOpusRequest) -> CrawlTaskResponse:
    creator_id = req.creator_id.strip()
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id 不能为空")

    cmd = [
        sys.executable, "main.py",
        "--platform", "bili",
        "--lt", "qrcode",
        "--type", "opus",
        "--creator_id", creator_id,
    ]
    task_id = await _start_crawl_task(cmd)
    return CrawlTaskResponse(task_id=task_id)


@app.post("/api/crawl/bili_knowledge", response_model=CrawlTaskResponse)
async def crawl_bili_knowledge(req: CrawlBiliOpusRequest) -> CrawlTaskResponse:
    """
    一次更新 B站 KOL 的两类知识源：
    1) opus 专栏/图文全文；
    2) creator 视频元数据 + 可用的人工/AI字幕。
    两步串行，复用浏览器持久登录态；任何一步失败都保留日志并将任务标记失败。
    """
    creator_id = req.creator_id.strip()
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id 不能为空")

    opus_cmd = [
        sys.executable, "main.py",
        "--platform", "bili",
        "--lt", "qrcode",
        "--type", "opus",
        "--creator_id", creator_id,
    ]
    video_cmd = [
        sys.executable, "main.py",
        "--platform", "bili",
        "--lt", "qrcode",
        "--type", "creator",
        "--creator_id", creator_id,
        "--get_comment", "false",
    ]
    task_id = await _start_crawl_task(opus_cmd, chain=[video_cmd])
    return CrawlTaskResponse(task_id=task_id)


@app.get("/api/crawl/tasks/{task_id}")
async def get_crawl_task(task_id: str) -> Dict:
    task = _crawl_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task_id 不存在")
    return {"status": task["status"], "log_tail": "\n".join(task["log_lines"])}


class BacktestXueqiuRequest(BaseModel):
    user_id: str
    since: Optional[str] = None
    limit: Optional[int] = None


@app.post("/api/backtest/xueqiu", response_model=CrawlTaskResponse)
async def backtest_xueqiu(req: BacktestXueqiuRequest) -> CrawlTaskResponse:
    """
    回测某用户的发言: backtest_run.py 依次做
    LLM 提取观点与预测 -> 用预测发布后的真实股价验证 -> 验证正确的观点
    写入摘录文件 (回测结束自动重建 digest)。与抓取任务共用一把锁，
    同一时间只跑一个，避免同时冲击雪球。
    """
    user_id = normalize_user_id(req.user_id)
    if not user_id or not user_id.isdigit():
        raise HTTPException(status_code=400, detail=f"无法从输入解析出用户 ID: {req.user_id}")

    cmd = [sys.executable, "backtest_run.py", "--creator_id", user_id]
    if req.since:
        cmd += ["--since", req.since]
    if req.limit:
        cmd += ["--limit", str(req.limit)]
    task_id = await _start_crawl_task(cmd)
    return CrawlTaskResponse(task_id=task_id)


class DigestRebuildRequest(BaseModel):
    user_id: Optional[str] = None


@app.post("/api/digest/rebuild", response_model=CrawlTaskResponse)
async def rebuild_digest(req: DigestRebuildRequest = DigestRebuildRequest()) -> CrawlTaskResponse:
    """
    重建观点摘录文件。user_id 给定时只重建该用户的条目 (其他用户保持不变);
    不给定时全量重建。提炼按内容哈希缓存。
    """
    cmd = [sys.executable, "digest_run.py"]
    if req.user_id and req.user_id.strip():
        uid = normalize_user_id(req.user_id)
        if not uid or not uid.isdigit():
            raise HTTPException(status_code=400, detail=f"无法从输入解析出用户 ID: {req.user_id}")
        cmd += ["--user_id", uid]
    task_id = await _start_crawl_task(cmd)
    return CrawlTaskResponse(task_id=task_id)


@app.get("/api/crawled/users")
async def crawled_users() -> List[Dict]:
    """列出已抓取过的雪球用户 (昵称/ID/发帖数/粉丝数), 按发帖数降序。"""
    base = _PROJECT_ROOT / "data" / "xueqiu" / "jsonl"
    users: Dict[str, Dict] = {}

    def _slot(uid: str) -> Dict:
        return users.setdefault(
            uid, {"user_id": uid, "user_nickname": "", "followers_count": 0, "post_count": 0}
        )

    for p in sorted(glob.glob(str(base / "creator_*_creators_*.jsonl"))):
        uid = os.path.basename(p).split("_")[1]
        u = _slot(uid)
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("user_nickname"):
                        u["user_nickname"] = rec["user_nickname"]
                    if rec.get("followers_count"):
                        u["followers_count"] = int(rec["followers_count"])
        except OSError:
            continue
    for p in sorted(glob.glob(str(base / "creator_*_contents_*.jsonl"))):
        uid = os.path.basename(p).split("_")[1]
        u = _slot(uid)
        try:
            with open(p, encoding="utf-8") as f:
                u["post_count"] += sum(1 for l in f if l.strip())
        except OSError:
            continue
    return sorted(users.values(), key=lambda u: -u["post_count"])


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>股票投资助手</title>
<style>
  body { font-family: -apple-system, "Microsoft YaHei", sans-serif; max-width: 800px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; }
  header { margin-bottom: 24px; }
  header h1 { margin: 0 0 4px; font-size: 24px; }
  header p { margin: 0; color: #666; font-size: 14px; }
  .search-bar { display: flex; gap: 8px; margin: 20px 0 8px; }
  .research-options { display:flex; gap:14px; align-items:center; flex-wrap:wrap; margin:0 0 14px; color:#666; font-size:12px; }
  .research-options label { display:flex; align-items:center; gap:5px; }
  .research-mode-controls { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .research-mode-controls select, .research-mode-controls input[type="date"] {
    padding:6px 8px; border:1px solid #ccc; border-radius:4px; background:#fff;
  }
  .historical-banner {
    margin:10px 0; padding:10px 12px; border-left:4px solid #b26a00;
    background:#fff6e5; border-radius:4px; font-size:13px;
  }
  .live-banner {
    margin:10px 0; padding:8px 12px; border-left:4px solid #1a73e8;
    background:#eef6ff; border-radius:4px; font-size:13px;
  }
  .temporal-status { padding:6px 9px; border-radius:4px; background:#f5f7fa; border:1px solid #e0e4e8; }
  input { padding: 8px; font-size: 14px; width: 200px; border: 1px solid #ccc; border-radius: 4px; }
  button { padding: 8px 20px; font-size: 14px; cursor: pointer; background: #1a73e8; color: #fff; border: none; border-radius: 4px; }
  button:hover { background: #1558b0; }
  #status { margin: 16px 0; color: #666; font-size: 13px; }
  .candidate { border: 1px solid #ddd; border-radius: 6px; padding: 12px; margin: 8px 0; }
  .quote { font-weight: bold; }
  pre { white-space: pre-wrap; background: #f7f7f7; padding: 12px; border-radius: 6px; }
  .stance-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 13px; font-weight: bold; color: #fff; }
  .stance-bullish { background: #d33; }
  .stance-bearish { background: #2a9d3f; }
  .stance-neutral { background: #888; }
  .thesis-block { margin: 12px 0; }
  .counter-evidence-block { margin: 12px 0; padding: 10px 12px; background: #fff0f0; border-left: 4px solid #d33; border-radius: 4px; }
  .lynch-category { display: inline-block; padding: 3px 9px; margin-left: 6px; border-radius: 10px; font-size: 12px; background: #eef2f7; color: #444; border: 1px solid #dbe2ea; }
  .invalidation-block { margin: 12px 0; padding: 10px 12px; background: #eef6ff; border-left: 4px solid #1a73e8; border-radius: 4px; }
  .risk-block { margin: 12px 0; padding: 10px 12px; background: #fff6e5; border-left: 4px solid #e0a020; border-radius: 4px; }
  /* LLM 生成的段落带 \n 换行，HTML 默认把换行折叠成空格，必须显式保留，
     否则分条列举的结论会挤成一整行 */
  .thesis-block, .counter-evidence-block, .invalidation-block, .risk-block { white-space: pre-wrap; }
  .evidence-section { margin: 20px 0; }
  .evidence-block { margin: 8px 0; padding: 10px 12px; background: #f7f7f7; border-left: 4px solid #999; border-radius: 4px; font-size: 13px; }
  .evidence-block.missing { color: #999; }
  .evidence-block ul { margin: 4px 0 0; padding-left: 20px; }
  .credibility-note { color: #555; }
  .prompt-version { color: #aaa; font-size: 12px; }
  .disclaimer { margin-top: 32px; padding-top: 12px; border-top: 1px solid #eee; color: #999; font-size: 12px; }
  .watchlist { margin: 12px 0; }
  .watchlist p { margin: 0 0 6px; color: #666; font-size: 13px; }
  .stock-chip { display: inline-block; padding: 4px 10px; margin: 0 6px 6px 0; border: 1px solid #ccc; border-radius: 14px; font-size: 13px; cursor: pointer; background: #f7f7f7; }
  .stock-chip:hover { background: #eaf1fd; border-color: #1a73e8; }
  .crawl-section { margin: 24px 0; padding: 16px; border: 1px solid #e0e0e0; border-radius: 6px; background: #fafafa; }
  .crawl-section h3 { margin: 0 0 10px; font-size: 15px; }
  .crawl-row { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
  .crawl-row input { flex: 1; max-width: 320px; }
  .crawl-hint { color: #999; font-size: 12px; margin: 2px 0 8px; }
  .crawl-log { max-height: 160px; overflow-y: auto; font-size: 12px; }
  .crawl-status { font-size: 13px; margin: 4px 0; color: #555; }

  /* Research dashboard visual system — accessible without external CSS libraries */
  :root { color-scheme: light; --paper:#f3f6fc; --panel:#ffffff; --ink:#15243c; --muted:#66758d;
    --brand:#4c5ce5; --brand-strong:#3443bf; --line:#e4eaf4; --rise:#d44d57; --fall:#159676;
    --radius:18px; --shadow:0 14px 38px rgba(24,43,85,.065); }
  *, *::before, *::after { box-sizing:border-box; }
  html { scroll-behavior:smooth; }
  body { max-width:1180px; margin:0 auto; padding:26px 24px 56px; background:var(--paper); color:var(--ink);
    font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; line-height:1.65; }
  header { position:relative; overflow:hidden; border-radius:26px; padding:36px 40px 32px; margin:0 0 18px;
    color:#fff; background:linear-gradient(118deg,#141e40 0%,#273d7d 54%,#6053bb 100%); }
  header::after { content:""; position:absolute; width:420px; height:420px; right:-90px; top:-260px;
    border:1px solid rgba(255,255,255,.19); border-radius:50%; box-shadow:0 0 0 76px rgba(255,255,255,.04),
    0 0 0 150px rgba(255,255,255,.035); pointer-events:none; }
  header .eyebrow { display:block; color:#a9c5ff; font-size:11px; font-weight:800; text-transform:uppercase;
    letter-spacing:.16em; margin-bottom:8px; }
  header h1 { font-size:clamp(28px,4vw,38px); letter-spacing:-.045em; margin:0 0 8px; font-weight:800; }
  header p { color:#d9e2ff; font-size:14px; max-width:620px; }
  .search-bar, .research-options, .watchlist, .crawl-section, #result > .evidence-section, #result > .research-panel,
  #result > .result-summary, #result > .dashboard-charts, #result > .valuation-lab { background:var(--panel);
    border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); }
  .search-bar { padding:16px; margin:0 0 10px; gap:12px; align-items:stretch; }
  .search-bar input { flex:1; width:auto; min-width:0; height:46px; }
  button { background:var(--brand); border-radius:11px; font-weight:700; padding:10px 20px; transition:filter .18s,transform .18s; }
  button:hover { background:var(--brand-strong); filter:brightness(1.05); transform:translateY(-1px); }
  button:disabled { opacity:.55; cursor:not-allowed; transform:none; }
  input,select { background:#fff; border:1px solid #cfd9e8; color:var(--ink); border-radius:10px; }
  input:focus-visible,select:focus-visible,button:focus-visible,summary:focus-visible,
  .stock-chip:focus-visible { outline:3px solid #93a7ff; outline-offset:2px; }
  input[type="checkbox"] { width:auto; accent-color:var(--brand); }
  input[type="range"] { width:100%; padding:0; border:none; accent-color:var(--brand); cursor:pointer; }
  .research-options { padding:12px 16px; margin:0 0 12px; font-size:12px; }
  .research-mode-controls select,.research-mode-controls input[type="date"] { border-radius:8px; }
  .temporal-status { color:#53617b; background:#f1f5fb; border-color:#e7edf7; border-radius:30px; }
  .watchlist { padding:14px 16px 8px; margin:0 0 16px; }
  .watchlist p { font-weight:700; color:#52617b; }
  .stock-chip { border:1px solid #dce4f5; color:#385078; background:#f8faff; margin-bottom:8px;
    transition:background .2s,border-color .2s; }
  .stock-chip:hover { color:var(--brand-strong); background:#ecf0ff; border-color:#8495ef; }
  #status { padding:4px 2px; margin:9px 0; color:var(--muted); }
  #result { display:flex; flex-direction:column; gap:16px; min-width:0; }
  #result > h2 { margin:10px 4px 0; font-size:29px; letter-spacing:-.04em; }
  #result > h3 { margin:8px 4px 0; font-size:21px; }
  #result > p { margin:0 4px; }
  #result .result-summary,#result .research-panel,#result .dashboard-charts,#result .valuation-lab { padding:24px; }
  #result .evidence-section { padding:22px 24px; margin:0; }
  #result .evidence-section > h3 { margin-top:0; font-size:20px; }
  #result .evidence-block { background:#f8faff; border:1px solid #e9eef7; border-left:3px solid #c9d5f3;
    border-radius:12px; padding:14px 17px; margin:12px 0; overflow-x:auto; }
  #result details.evidence-block { cursor:default; }
  #result details summary { cursor:pointer; }
  #result .candidate { border:1px solid var(--line); background:#fff; border-radius:14px; box-shadow:var(--shadow); padding:18px; margin:0; }
  .thesis-block,.counter-evidence-block,.risk-block,.invalidation-block { padding:14px 17px; border-radius:12px; margin:12px 0; }
  .thesis-block { background:#f1f5ff; border-left:3px solid #5266cf; }
  .counter-evidence-block { background:#fff3f2; border-left:3px solid #d76363; }
  .invalidation-block { background:#f0f8fe; border-left:3px solid #3b92c0; }
  .risk-block { background:#fff8ec; border-left:3px solid #e1ad52; }
  .stance-badge { font-weight:750; padding:5px 12px; }
  .stance-bullish { background:var(--rise); }.stance-bearish { background:var(--fall); }
  .live-banner,.historical-banner { margin:0; border-radius:10px; }
  .quote { display:inline-flex; gap:12px; align-items:center; }
  .result-summary h3 { margin:0 0 10px; font-size:20px; }
  .verdict-line { display:flex; flex-wrap:wrap; align-items:center; gap:10px 16px; padding:4px 0 0; }
  .verdict-item { color:#4d607b; font-size:14px; }
  .verdict-item b { color:var(--ink); font-size:15px; }
  .verdict-industry { color:var(--muted); font-size:13px; }
  .verdict-summary { margin:16px 0 0; color:#344762; line-height:1.85; font-size:15px; white-space:pre-wrap; overflow-wrap:anywhere; }

  .dashboard-charts h3, .valuation-lab h3 { margin:0 0 5px; font-size:21px; }
  .section-caption { font-size:12px; color:var(--muted); margin:0 0 16px; }
  .metric-strip { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:0; }
  .metric { padding:15px; border:1px solid var(--line); border-radius:13px; background:#f8faff; min-width:0; }
  .metric label { font-size:11px; color:var(--muted); display:block; }
  .metric strong { display:block; margin-top:5px; font-size:23px; font-weight:800; letter-spacing:-.04em; font-variant-numeric:tabular-nums; }
  .metric small { font-size:11px; color:var(--muted); overflow-wrap:anywhere; }
  .chart-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
  .chart-tile { min-width:0; padding:18px; border:1px solid var(--line); border-radius:14px; background:#fcfdff; overflow-x:auto; }
  .chart-tile h4 { font-size:14px; margin:0 0 5px; }
  .chart-tile .section-caption { margin-bottom:10px; }
  .chart-tile svg { display:block; max-width:100%; height:auto; }
  .chart-empty { padding:32px 10px; text-align:center; background:#f7f9ff; border:1px dashed #d6e1f1;
    border-radius:10px; font-size:12px; color:var(--muted); }
  .valuation-lab { border-color:#cbd5ff !important; }
  .valuation-lab .model-tag { display:inline-block; padding:3px 9px; font-size:11px; font-weight:700;
    color:#475ad1; border:1px solid #d8deff; background:#eef1ff; border-radius:30px; }
  .research-narrative h3 { margin:0 0 8px; }
  .analysis-entry { padding:20px 0; border-top:1px solid var(--line); }
  .analysis-entry:first-of-type { border-top:0; }
  .analysis-entry h4 { display:flex; align-items:center; gap:12px; font-size:17px; margin:0 0 12px; color:var(--ink); }
  .analysis-index { flex:0 0 33px; height:33px; display:inline-flex; align-items:center; justify-content:center;
    border-radius:10px; color:#4b5dce; background:#edf0ff; font-weight:800; font-size:12px; font-variant-numeric:tabular-nums; }
  .analysis-prose { white-space:pre-wrap; overflow-wrap:anywhere; font-size:14px; line-height:1.85; color:#32445f; }
  .lab-layout { display:grid; grid-template-columns:1.15fr 1fr; gap:20px; align-items:start; }
  .slider-row { padding:11px 0; border-bottom:1px solid var(--line); }
  .slider-row:last-child { border-bottom:0; }
  .slider-top { display:flex; align-items:baseline; justify-content:space-between; gap:10px; }
  .slider-top label { color:#3b4e6c; font-weight:700; font-size:13px; }
  .slider-top output { color:var(--brand-strong); font-weight:800; font-variant-numeric:tabular-nums; }
  .slider-source { margin:4px 0 6px; font-size:11px; color:var(--muted); }
  .lab-result { border-radius:16px; background:linear-gradient(145deg,#172344,#303d80); padding:22px;
    color:#fff; position:sticky; top:16px; }
  .lab-result h4 { color:#cfdbff; margin:0 0 4px; font-size:12px; }
  .lab-price { font-size:clamp(27px,3vw,40px); line-height:1.2; font-weight:850; letter-spacing:-.04em;
    font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
  .lab-stat-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:15px 0; }
  .lab-stat-grid div { padding:10px; border:1px solid rgba(255,255,255,.2); border-radius:10px; }
  .lab-stat-grid span { display:block; color:#d6def8; font-size:11px; }
  .lab-stat-grid b { display:block; font-size:18px; font-variant-numeric:tabular-nums; }
  .lab-foot { font-size:11px; color:#d5defb; line-height:1.65; }
  .lab-actions { display:flex; align-items:center; gap:10px; margin-top:14px; flex-wrap:wrap; }
  .lab-actions button { background:#edf0ff; color:#3443ad; }
  .lab-scenario-chart { margin-top:16px; }
  .crawl-section { margin:22px 0; padding:20px 24px; }
  .crawl-hint,.credibility-note,.prompt-version { color:var(--muted); }
  .crawl-log,pre { background:#f4f7fe; color:#30405b; }
  .disclaimer { color:var(--muted); border-color:var(--line); }
  @media(max-width:800px) {
    body { padding:12px; } header { border-radius:18px; padding:26px 24px; }
    .metric-strip,.chart-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .lab-layout { grid-template-columns:1fr; }.lab-result { position:static; }
    #result .result-summary,#result .research-panel,#result .dashboard-charts,#result .valuation-lab,
    #result .evidence-section { padding:16px; }
  }
  @media(max-width:500px) {
    .search-bar { flex-wrap:wrap; }.search-bar button { width:100%; }
    .metric-strip,.chart-grid { grid-template-columns:1fr; }
    .research-options { align-items:stretch; }
    .metric strong { font-size:21px; }
  }
  @media(prefers-reduced-motion:reduce) { *,*::before,*::after { scroll-behavior:auto !important; transition:none !important; } }
</style>
</head>
<body>
<header>
  <span class="eyebrow">EVIDENCE-FIRST · INVESTMENT RESEARCH</span>
  <h1>股票投资助手 <span style="font-weight:400;color:#c2d1ff;">/ Research Desk</span></h1>
  <p>用长期经营数据与可追溯证据研究公司价值。行业、筹码、现金流、估值和反方论据，尽量交给图表与计算来说话。</p>
</header>
<div class="search-bar">
  <input id="stockCode" placeholder="股票代码或名称，如 SH603408 / 洛阳钼业" />
  <button id="submitBtn">分析</button>
</div>
<div class="research-options">
  <div class="research-mode-controls">
    <label>研究模式
      <select id="researchMode">
        <option value="live">Live：按今天可用信息分析</option>
        <option value="historical">Historical：站在过去某天分析</option>
      </select>
    </label>
    <label id="asOfLabel" style="display:none;">截止日期
      <input type="date" id="asOfDate" />
    </label>
    <label><input type="checkbox" id="saveSnapshot" /> 保存标准研究快照</label>
  </div>
  <div id="historicalHint" class="crawl-hint" style="display:none;">
    历史模式只使用截止日当时已可获得的信息；缺失数据保持缺失，不会用今天的数据回填。
  </div>
  <span id="temporalStatus" class="temporal-status">检查 historical/as-of 能力...</span>
</div>
<div class="watchlist">
  <p>常用股票 (点击可快速填入分析):</p>
  <div id="stockChips"></div>
</div>
<div id="status"></div>
<div id="result"></div>
<div id="radarTip" style="display:none;position:fixed;z-index:10;max-width:260px;padding:6px 10px;background:#333;color:#fff;font-size:12px;border-radius:4px;pointer-events:none;"></div>

<div class="crawl-section">
  <h3>数据抓取 / 观点回测</h3>

  <p class="crawl-hint">选择已抓取用户 (下拉自动补全, 显示昵称/ID/帖子数) 或直接输入新用户 ID/主页 URL; 抓取需要登录, 弹出的浏览器里登录后自动继续。"更新并回测" = 抓取 + LLM 回测 + 摘要重建, 一条龙</p>
  <div class="crawl-row">
    <input id="xueqiuUserId" placeholder="雪球用户 ID 或主页 URL" list="crawledUserList" style="flex:1;max-width:320px;" />
    <datalist id="crawledUserList"></datalist>
    <button id="crawlXueqiuBtn">更新并回测</button>
    <label style="font-size:13px;color:#666;display:flex;align-items:center;gap:4px;">
      <input type="checkbox" id="xueqiuIncremental" checked /> 仅增量更新
    </label>
  </div>

  <details class="crawl-hint">
    <summary style="cursor:pointer;color:#666;">高级操作 (一般不常用)</summary>
    <div class="crawl-row">
      <button id="backtestBtn">仅重新回测</button>
      <button id="digestUserBtn">仅重建该用户摘要</button>
      <button id="crawlOnlyBtn">仅抓取</button>
      <button id="digestBtn">重建全部摘要</button>
    </div>
  </details>

  <p class="crawl-hint">抓取/更新 B 站 KOL 知识：专栏全文 + 创作者视频字幕（人工字幕优先，AI字幕会标记；无字幕不会拿简介替代正文）。需要登录。</p>
  <div class="crawl-row">
    <input id="biliCreatorId" placeholder="B站 UID 或空间 URL" />
    <button id="crawlBiliBtn">更新专栏+视频字幕</button>
  </div>

  <div class="crawl-status" id="crawlStatus"></div>
  <button id="copyLogBtn" style="display:none;">复制全部日志</button>
  <pre class="crawl-log" id="crawlLog" style="display:none;"></pre>
</div>

<p class="disclaimer">以上内容基于历史数据与 AI 生成，仅供参考，不构成投资建议。</p>

<script>
let pollTimer = null;
let submitting = false;

// 固定自选股。代码已对全市场代码表核实；"长鑫存储"的上市主体名为"长鑫科技"(688825)，
// 用上市名是因为输入框打"长鑫存储"后端解析不出来 (按名称解析只做单向包含匹配)。
const WATCHLIST = [
  { code: 'SH600519', name: '贵州茅台' },
  { code: 'SH601088', name: '中国神华' },
  { code: 'SH600938', name: '中国海油' },
  { code: 'SH601398', name: '工商银行' },
  { code: 'SZ300308', name: '中际旭创' },
  { code: 'SH601899', name: '紫金矿业' },
  { code: 'SZ000333', name: '美的集团' },
  { code: 'SH601919', name: '中远海控' },
  { code: 'SH688256', name: '寒武纪' },
  { code: 'SH688825', name: '长鑫科技' },
];

function renderWatchlist() {
  const el = document.getElementById('stockChips');
  el.innerHTML = WATCHLIST.map(s =>
    '<span class="stock-chip" data-code="' + s.code + '">' + s.name + ' (' + s.code + ')</span>'
  ).join('');
  el.querySelectorAll('.stock-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.getElementById('stockCode').value = chip.getAttribute('data-code');
      submitAnalysis();
    });
  });
}

function syncResearchModeControls() {
  const mode = document.getElementById('researchMode').value;
  const historical = mode === 'historical';
  document.getElementById('asOfLabel').style.display = historical ? 'inline-flex' : 'none';
  document.getElementById('historicalHint').style.display = historical ? 'block' : 'none';
  if (historical) {
    // Historical runs are expensive and should be reproducible by default.
    document.getElementById('saveSnapshot').checked = true;
  }
}

async function loadTemporalCapabilities() {
  const el = document.getElementById('temporalStatus');
  try {
    const res = await fetch('/api/research/time-capabilities');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const safe = data.safe_sources || [];
    const blocking = data.blocking_sources || [];
    if (data.historical_ready) {
      el.textContent = 'Historical/as-of 已就绪：' + safe.length + ' 路数据源通过 point-in-time 校验';
      el.title = '历史模式已具备全部必要 point-in-time 数据源';
    } else {
      el.textContent = 'Historical/as-of 尚未开放：' + safe.length + ' 路已安全，' +
        blocking.length + ' 路仍阻塞';
      el.title = blocking.map(x => x + ': ' + ((data.reasons || {})[x] || '')).join('\\n');
    }
  } catch (e) {
    el.textContent = 'Historical/as-of 能力状态读取失败';
    el.title = String(e);
  }
}

async function submitAnalysis() {
  const stockCode = document.getElementById('stockCode').value.trim();
  if (!stockCode) return;
  // 重入保护: 后端每个 POST 都会 create_task 跑完整报告 (含 3 分钟 CDP 等待 + 一次
  // LLM 调用)。连点两下会同时跑两份，而前端只轮询后一个 task_id，前一个就成了没人
  // 认领却仍在消耗资源的孤儿任务——表现是日志里同一只股票出现两组会话启动。
  if (submitting) {
    document.getElementById('status').textContent = '已有分析任务在跑，等它结束再提交 (一次分析要几分钟)';
    return;
  }
  const mode = document.getElementById('researchMode').value;
  const asOf = document.getElementById('asOfDate').value;
  if (mode === 'historical' && !asOf) {
    document.getElementById('status').textContent = '历史模式必须选择截止日期';
    return;
  }
  submitting = true;
  document.getElementById('result').innerHTML = '';
  document.getElementById('status').textContent = '提交中...';
  if (pollTimer) clearInterval(pollTimer);

  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      stock_code: stockCode,
      mode: document.getElementById('researchMode').value,
      as_of: document.getElementById('researchMode').value === 'historical'
        ? (document.getElementById('asOfDate').value || null)
        : null,
      save_snapshot: document.getElementById('saveSnapshot').checked,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    document.getElementById('status').textContent = '提交失败: ' + (err.detail || res.status);
    submitting = false;
    return;
  }
  const { task_id } = await res.json();
  document.getElementById('status').textContent = '任务已提交, 状态: pending';
  pollTimer = setInterval(() => pollTask(task_id), 2000);
}

async function pollTask(taskId) {
  const res = await fetch('/api/tasks/' + taskId);
  if (!res.ok) return;
  const data = await res.json();
  document.getElementById('status').textContent = '状态: ' + data.status;

  // 任务跑到终态才解锁，而不是 POST 返回就解锁——一次分析要几分钟，
  // POST 返回时后端还在跑，此时放开会让用户再点出一个并行任务。
  if (data.status === 'done') {
    clearInterval(pollTimer);
    submitting = false;
    renderResult(data.result);
  } else if (data.status === 'failed') {
    clearInterval(pollTimer);
    submitting = false;
    document.getElementById('status').textContent = '任务失败: ' + (data.error || '未知错误');
  }
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s || '';
  return div.innerHTML;
}

function safeExternalLink(url, label) {
  if (!url || !/^https?:\/\//i.test(url)) return escapeHtml(label || '');
  return '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' +
    escapeHtml(label || url) + '</a>';
}

function stanceLabel(stance) {
  if (stance === 'bullish') return '看多';
  if (stance === 'bearish') return '看空';
  if (stance === 'neutral') return '中性';
  return '未知';
}

function lynchCategoryLabel(cat) {
  const labels = {
    fast_grower: '高成长股',
    stalwart: '大盘稳健股',
    cyclical: '周期股',
    turnaround: '困境反转股',
    asset_play: '资产价值低估',
    slow_grower: '低增长股',
    unclear: '类型不明确',
  };
  return labels[cat] || '';
}

function yi(yuan) {
  if (yuan === null || yuan === undefined) return '暂缺';
  return (yuan / 1e8).toFixed(2) + '亿';
}

function pctStr(v) {
  return (v === null || v === undefined) ? '暂缺' : v + '%';
}

function renderWorkingCapitalGrowthChart(facts) {
  const candidates = [
    ['营收', facts.revenue_yoy_pct],
    ['应收', facts.accounts_receivable_yoy_pct],
    ['存货', facts.inventory_yoy_pct],
  ].filter(([, v]) => v !== null && v !== undefined && Number.isFinite(Number(v)));
  if (candidates.length < 2) return '';

  const values = candidates.map(([, v]) => Number(v));
  const maxAbs = Math.max(5, ...values.map(Math.abs));
  const W = 620, H = 205, padL = 48, padR = 20, padT = 24, padB = 42;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const zeroY = padT + plotH / 2;
  const scale = (plotH / 2 - 10) / maxAbs;
  const barW = Math.min(90, plotW / (candidates.length * 1.8));
  const gap = plotW / candidates.length;

  let svg = '<svg viewBox="0 0 ' + W + ' ' + H +
    '" width="100%" role="img" aria-label="营收应收存货同比增速对比">';
  svg += '<line x1="' + padL + '" y1="' + zeroY + '" x2="' + (W - padR) +
    '" y2="' + zeroY + '" stroke="#aaa" stroke-width="1"/>';
  svg += '<text x="' + (padL - 6) + '" y="' + (zeroY + 4) +
    '" text-anchor="end" font-size="10" fill="#666">0%</text>';

  candidates.forEach(([name, raw], i) => {
    const value = Number(raw);
    const h = Math.abs(value) * scale;
    const x = padL + gap * (i + 0.5) - barW / 2;
    const y = value >= 0 ? zeroY - h : zeroY;
    svg += '<rect class="holder-mark" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
      '" width="' + barW.toFixed(1) + '" height="' + Math.max(1, h).toFixed(1) +
      '" rx="3" fill="currentColor" fill-opacity="0.55" data-tip="' +
      escapeHtml(name + '同比 ' + (value >= 0 ? '+' : '') + value + '%') + '"/>';
    svg += '<text x="' + (x + barW / 2).toFixed(1) + '" y="' +
      (value >= 0 ? y - 5 : y + h + 13).toFixed(1) +
      '" text-anchor="middle" font-size="11" fill="#444">' +
      (value >= 0 ? '+' : '') + value + '%</text>';
    svg += '<text x="' + (x + barW / 2).toFixed(1) + '" y="' + (H - 14) +
      '" text-anchor="middle" font-size="12" fill="#333">' + escapeHtml(name) + '</text>';
  });
  svg += '</svg>';
  return svg;
}

function renderFundamentalsBlock(f) {
  if (!f || !f.facts) {
    return '<div class="evidence-block missing"><b>结构性事实:</b> 暂缺 (本次未能取到同花顺 F10 数据)</div>';
  }
  const x = f.facts;
  const rows = [
    ['财务数据期间', x.finance_period],
    ['客户/供应商集中度期间', f.concentration_period],
    ['营业收入', yi(x.revenue) + (x.revenue_yoy_pct === null || x.revenue_yoy_pct === undefined ? '' : ' (同比 ' + x.revenue_yoy_pct + '%)')],
    ['净利润', yi(x.net_profit) + (x.net_profit_basis ? ' (口径: ' + x.net_profit_basis + ')' : '')],
    ['经营活动现金流净额', yi(x.operating_cash_flow)],
    ['经营现金流/净利润', x.cash_to_profit_ratio === null || x.cash_to_profit_ratio === undefined ? '暂缺' : x.cash_to_profit_ratio],
    ['应收账款',
      yi(x.accounts_receivable_yuan) +
      (x.accounts_receivable_yoy_pct === null || x.accounts_receivable_yoy_pct === undefined
        ? '' : '，同比 ' + (x.accounts_receivable_yoy_pct >= 0 ? '+' : '') + x.accounts_receivable_yoy_pct + '%') +
      (x.receivable_growth_minus_revenue_pp === null || x.receivable_growth_minus_revenue_pp === undefined
        ? '' : '，较营收增速 ' + (x.receivable_growth_minus_revenue_pp >= 0 ? '+' : '') + x.receivable_growth_minus_revenue_pp + 'pct')
    ],
    ['存货',
      yi(x.inventory_yuan) +
      (x.inventory_yoy_pct === null || x.inventory_yoy_pct === undefined
        ? '' : '，同比 ' + (x.inventory_yoy_pct >= 0 ? '+' : '') + x.inventory_yoy_pct + '%') +
      (x.inventory_growth_minus_revenue_pp === null || x.inventory_growth_minus_revenue_pp === undefined
        ? '' : '，较营收增速 ' + (x.inventory_growth_minus_revenue_pp >= 0 ? '+' : '') + x.inventory_growth_minus_revenue_pp + 'pct')
    ],
    ['研发投入', yi(x.rd_investment_yuan) + '，研发强度 ' + pctStr(x.rd_intensity_pct)],
    ['前五大客户占营收', pctStr(x.top5_customer_pct)],
    ['前五大供应商占采购额', pctStr(x.top5_supplier_pct)],
    ['海外业务收入', yi(x.overseas_revenue_yuan) + '，占营收 ' + pctStr(x.overseas_revenue_pct)],
    ['境外销量占比', pctStr(x.overseas_sales_pct)],
    ['发明专利占比', pctStr(x.invention_ratio_pct) + (x.patents_granted ? ' (' + (x.patents_invention || 0) + '/' + x.patents_granted + ')' : '')],
    ['股东户数', (x.holder_count_latest === null || x.holder_count_latest === undefined ? '暂缺' : x.holder_count_latest + ' 户')
      + ' (截止 ' + (x.holder_count_period || '未知') + ')，环比 ' + pctStr(x.holder_count_qoq_pct) + '，同比 ' + pctStr(x.holder_count_yoy_pct)],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== '暂缺');

  let html = '<div class="evidence-block"><b>结构性事实 (同花顺 F10，公司定期报告原文):</b><ul>' +
    rows.map(([k, v]) => '<li>' + k + ': ' + escapeHtml(String(v)) + '</li>').join('') + '</ul>';

  const wcChart = renderWorkingCapitalGrowthChart(x);
  if (wcChart) {
    html += '<div style="margin-top:8px"><b>营运资金压力：营收 vs 应收/存货同比增速</b><br>' +
      wcChart +
      '<div class="credibility-note">应收或存货增速长期显著高于营收时，需要进一步解释回款质量、渠道压货或库存积压；单一期不能独立定性。</div></div>';
  }

  const series = x.holder_count_series || [];
  if (series.length >= 2) {
    html += '<div style="margin-top:6px"><b>股东户数 vs 同期股价 (判断股价暴涨是否伴随筹码派发):</b><br>' +
      renderHolderChart(series.slice(0, 8)) + '</div>';
  }

  const risks = (f.self_disclosed_risks || '').trim();
  html += '<div class="risk-block"><b>公司自述的风险 (原文):</b> ' +
    (risks ? escapeHtml(risks) : '暂缺 (该公司本期报告的董事会经营评述中没有独立的风险小节)') + '</div>';
  html += '</div>';
  return html;
}

function renderRdTeamBlock(rd) {
  if (!rd) {
    return '<div class="evidence-block missing"><b>研发团队组成:</b> 暂缺 (本次未取得巨潮最新年报研发人员表)</div>';
  }
  if (rd.parse_status !== 'ok') {
    return '<div class="evidence-block missing"><b>研发团队组成:</b> 已定位 ' +
      escapeHtml(rd.title || '最新年报') + '，但解析状态为 ' +
      escapeHtml(rd.parse_status || 'unknown') + '；不据此猜测人才结构</div>';
  }

  let html = '<div class="evidence-block"><b>研发团队组成（巨潮最新年报）:</b><ul>';
  if (rd.rd_headcount !== null && rd.rd_headcount !== undefined) {
    html += '<li>研发人员: ' + rd.rd_headcount + ' 人' +
      (rd.rd_staff_ratio_pct !== null && rd.rd_staff_ratio_pct !== undefined
        ? '，占员工总数 ' + rd.rd_staff_ratio_pct + '%' : '') + '</li>';
  }
  const edu = rd.education || {};
  const eduLabels = {
    doctor: '博士', master: '硕士', bachelor: '本科',
    college: '专科', high_school_or_below: '高中及以下',
  };
  const eduParts = Object.keys(eduLabels)
    .filter(k => edu[k] !== null && edu[k] !== undefined)
    .map(k => eduLabels[k] + ' ' + edu[k] + ' 人');
  if (eduParts.length) {
    html += '<li>学历结构: ' + eduParts.join('，') + '</li>';
  }
  const age = rd.age || {};
  const ageLabels = {
    under_30: '30岁以下', '30_to_40': '30-40岁', '40_to_50': '40-50岁',
    '50_to_60': '50-60岁', '60_or_above': '60岁及以上',
  };
  const ageParts = Object.keys(ageLabels)
    .filter(k => age[k] !== null && age[k] !== undefined)
    .map(k => ageLabels[k] + ' ' + age[k] + ' 人');
  if (ageParts.length) {
    html += '<li>年龄结构: ' + ageParts.join('，') + '</li>';
  }
  if (rd.hit_pages && rd.hit_pages.length) {
    html += '<li>年报命中页: ' + rd.hit_pages.join('、') + '</li>';
  }
  html += '</ul>';
  if (rd.pdf_url) {
    html += '<div>' + safeExternalLink(rd.pdf_url, rd.title || '打开巨潮年报原文') + '</div>';
  }
  html += '<div class="credibility-note">学历/年龄结构只描述研发队伍构成，不能单独推出技术实力；需与研发强度、专利和产品兑现结合。</div>';
  html += '</div>';
  return html;
}

function renderManagementCapitalBlock(mc) {
  if (!mc) {
    return '<div class="evidence-block missing"><b>管理层与长期资本分配:</b> 暂缺</div>';
  }
  let html = '<div class="evidence-block"><b>管理层与长期资本分配:</b>';

  const a = mc.alignment || {};
  const alignment = [];
  if (a.chairman) alignment.push('董事长 ' + escapeHtml(a.chairman));
  if (a.joined_year) {
    alignment.push(a.joined_year + '年加入' +
      (a.tenure_years !== null && a.tenure_years !== undefined ? '，约 ' + a.tenure_years + ' 年' : ''));
  }
  if (a.chairman_salary_wan !== null && a.chairman_salary_wan !== undefined) {
    alignment.push('年薪 ' + a.chairman_salary_wan + ' 万元');
  }
  if (a.chairman_shares) alignment.push('持股 ' + escapeHtml(a.chairman_shares));
  html += '<div><b>利益绑定:</b> ' + (alignment.length ? alignment.join('；') : '暂缺') + '</div>';

  const ex = mc.execution || {};
  if (Object.keys(ex).length) {
    const parts = [];
    if (ex.period_start || ex.period_end) parts.push('观察期 ' + escapeHtml(ex.period_start || '') + '~' + escapeHtml(ex.period_end || ''));
    if (ex.roe_latest_pct !== null && ex.roe_latest_pct !== undefined) {
      parts.push('ROE ' + ex.roe_start_pct + '% → ' + ex.roe_latest_pct + '% (' +
        (ex.roe_change_pp >= 0 ? '+' : '') + ex.roe_change_pp + 'pct；区间 ' +
        ex.roe_min_pct + '%~' + ex.roe_max_pct + '%)');
    }
    if (ex.net_profit_growth_observations) {
      parts.push('净利润增速为正 ' + ex.net_profit_growth_positive_periods + '/' + ex.net_profit_growth_observations + ' 个观察期');
    }
    if (ex.net_margin_change_pp !== null && ex.net_margin_change_pp !== undefined) {
      parts.push('净利率较起点 ' + (ex.net_margin_change_pp >= 0 ? '+' : '') + ex.net_margin_change_pp + 'pct');
    }
    html += '<div><b>经营执行:</b> ' + parts.join('；') + '</div>';
  }

  const renderWindow = (row, label) => {
    if (!row) return '';
    const buybackYi = ((row.buyback_actual_amount_yuan || 0) / 1e8).toFixed(2);
    const cashTotal = row.cash_dividend_per_10_total;
    const cashText = cashTotal === null || cashTotal === undefined
      ? '现金分红累计金额暂缺'
      : '已知记录累计每10股现金分红 ' + cashTotal + ' 元' +
        (row.cash_dividend_amount_complete === false ? '（非完整累计）' : '');
    return '<li><b>' + label + ':</b> 分红覆盖 ' + row.dividend_years_count + '/' + row.window_years +
      ' 个日历年，' + cashText + '；' +
      '回购 ' + row.buyback_records + ' 次，已回购约 ' + buybackYi + ' 亿元；' +
      'F10再融资 ' + row.refinancing_records + ' 次，巨潮再融资公告 ' +
      row.primary_refinancing_announcements + ' 条；减持公告 ' + row.insider_reduction_announcements +
      ' 条；处罚/警示/问询等公告 ' + row.governance_negative_announcements + ' 条</li>';
  };
  html += '<ul>' + renderWindow(mc.five_year, '近5年') + renderWindow(mc.ten_year, '近10年') + '</ul>';

  const bad = mc.recent_governance_negative_events || [];
  if (bad.length) {
    html += '<details><summary style="cursor:pointer;">查看近期治理负面公告</summary><ul>' +
      bad.slice(0, 5).map(x => '<li>' + escapeHtml(x.published_at || '') + ' ' +
        safeExternalLink(x.url, x.title || '公告') + '</li>').join('') + '</ul></details>';
  }
  const reductions = mc.recent_insider_reduction_events || [];
  if (reductions.length) {
    html += '<details><summary style="cursor:pointer;">查看近期减持公告</summary><ul>' +
      reductions.slice(0, 5).map(x => '<li>' + escapeHtml(x.published_at || '') + ' ' +
        safeExternalLink(x.url, x.title || '公告') + '</li>').join('') + '</ul></details>';
  }
  html += '<div class="credibility-note">这里只展示长期行为记录，不把单个事实直接等同于“人品”；管理层判断需同时看利益绑定、经营兑现和资本分配。</div>';
  html += '</div>';
  return html;
}

function renderMacroRatesBlock(m) {
  if (!m) {
    return '<div class="evidence-block missing"><b>中美利率环境:</b> 暂缺；不据此讨论加息/降息影响</div>';
  }
  let html = '<div class="evidence-block"><b>中美利率环境:</b><ul>';
  const us = m.us || {};
  const ffFresh = !!((us.fed_target_freshness || {}).fresh);
  if (ffFresh && us.fed_target_lower_pct !== null && us.fed_target_lower_pct !== undefined &&
      us.fed_target_upper_pct !== null && us.fed_target_upper_pct !== undefined) {
    html += '<li>Fed目标区间: ' + us.fed_target_lower_pct + '%~' + us.fed_target_upper_pct +
      '%（' + escapeHtml(us.fed_target_upper_as_of || '') + '），上限较约180日前 ' +
      (us.fed_target_upper_change_180d_pp >= 0 ? '+' : '') + us.fed_target_upper_change_180d_pp + 'pct</li>';
  } else {
    html += '<li>Fed目标区间: 数据缺失或过期</li>';
  }
  const u10Fresh = !!((us.us10y_freshness || {}).fresh);
  if (u10Fresh && us.us10y_yield_pct !== null && us.us10y_yield_pct !== undefined) {
    html += '<li>美国10Y国债: ' + us.us10y_yield_pct + '%（' + escapeHtml(us.us10y_as_of || '') +
      '），30日变化 ' + (us.us10y_change_30d_pp >= 0 ? '+' : '') + us.us10y_change_30d_pp +
      'pct，90日变化 ' + (us.us10y_change_90d_pp >= 0 ? '+' : '') + us.us10y_change_90d_pp + 'pct</li>';
  } else {
    html += '<li>美国10Y国债: 数据缺失或过期</li>';
  }

  const cn = m.china || {};
  const cnFresh = !!((cn.freshness || {}).fresh);
  if (cnFresh) {
    html += '<li>中国LPR: 1年期 ' + cn.lpr_1y_pct + '%，5年期 ' + cn.lpr_5y_pct +
      '%（' + escapeHtml(cn.as_of || '') + '）</li>';
  } else {
    html += '<li>中国LPR: 数据缺失或过期</li>';
  }
  html += '</ul>';
  if (us.source_urls) {
    const links = [];
    if (us.source_urls.target_upper) links.push(safeExternalLink(us.source_urls.target_upper, 'Fed目标上限/FRED'));
    if (us.source_urls.us10y) links.push(safeExternalLink(us.source_urls.us10y, '美国10Y/FRED'));
    if (links.length) html += '<div>' + links.join(' · ') + '</div>';
  }
  if (cn.source_url) html += '<div>' + safeExternalLink(cn.source_url, '中国LPR来源') + '</div>';
  (m.warnings || []).forEach(x => {
    html += '<div class="credibility-note">⚠ ' + escapeHtml(x) + '</div>';
  });
  html += '<div class="credibility-note">LPR是贷款市场报价利率，不等同于央行政策利率；过期序列不会作为当前宏观证据。</div>';
  html += '</div>';
  return html;
}

function renderPolicyEventsBlock(p) {
  if (!p || !(p.events || []).length) {
    return '<div class="evidence-block missing"><b>近期政策/地缘事件线索:</b> 暂缺；不能反向推断没有相关风险</div>';
  }
  let html = '<div class="evidence-block"><b>近期政策/地缘事件线索:</b>' +
    '<div class="credibility-note">以下来自财经媒体/快讯，只是待核对的事件线索；必须结合公司实际暴露与一手/市场数据，不能把标题直接当成利好或利空。</div>' +
    '<ul>';
  (p.events || []).slice(0, 12).forEach(x => {
    const label = (x.published_at || '日期未知') + ' [' + (x.media || '媒体来源未知') + '] ' +
      (x.title || '');
    html += '<li>' +
      (x.url ? safeExternalLink(x.url, label) : escapeHtml(label));
    if (x.matched_terms && x.matched_terms.length) {
      html += '<br><span class="credibility-note">匹配暴露/主题: ' +
        x.matched_terms.map(escapeHtml).join('、') + '</span>';
    }
    html += '</li>';
  });
  html += '</ul></div>';
  return html;
}

function renderXueqiuBlock(x) {
  if (!x) {
    return '<div class="evidence-block missing"><b>雪球个股维度:</b> 暂缺 (本次未能取到机构持仓/讨论热度)</div>';
  }
  let html = '<div class="evidence-block"><b>雪球个股维度:</b><ul>';
  const holding = x.org_holding || [];
  if (holding.length) {
    html += '<li>机构/主要股东持仓:' + holding.slice(0, 10).map(h =>
      '<br>&nbsp;&nbsp;· ' + escapeHtml(h.name || '') + ': ' +
      Object.keys(h).filter(k => k !== 'name').map(k => escapeHtml(k) + '=' + escapeHtml(String(h[k]))).join(', ')
    ).join('') + '</li>';
  } else {
    html += '<li>机构持仓: 暂缺</li>';
  }
  const d = x.discussion || {};
  html += '<li>讨论热度: ' + (d.post_count === null || d.post_count === undefined
    ? '暂缺' : '相关帖子约 ' + d.post_count + ' 条 (仅情绪/关注度参考)') + '</li>';
  html += '</ul></div>';
  return html;
}

function renderEvidenceSection(report) {
  let html = '<div class="evidence-section"><h3>多维度证据</h3>';

  const ic = report.industry_comparison;
  if (ic && ic.advancing !== undefined && ic.advancing !== null) {
    html += '<div class="evidence-block"><b>行业涨跌:</b> 所属行业 ' + escapeHtml(ic.industry_name) +
      '，上涨家数 ' + ic.advancing + '，下跌家数 ' + ic.declining +
      '，行业涨跌幅 ' + ic.industry_change_pct + '%</div>';
  } else if (ic) {
    // 只认出行业归属、没匹配到同花顺板块统计时仍显示行业名，但不显示涨跌家数
    html += '<div class="evidence-block missing"><b>行业涨跌:</b> 所属申万行业 ' +
      escapeHtml(ic.sw_industry) + '，涨跌家数与涨跌幅暂缺</div>';
  } else {
    html += '<div class="evidence-block missing"><b>行业涨跌:</b> 暂缺 (行业归属数据本次未能取到)</div>';
  }

  const st = report.shareholder_trend;
  if (st) {
    const movement = st.trend === 'increasing' ? '户数增加' : st.trend === 'decreasing' ? '户数减少' : '户数变化不明确';
    html += '<div class="evidence-block"><b>股东户数（须结合股价位置）:</b> ' +
      escapeHtml(String(st.latest_count ?? '暂缺')) + ' 户 (截止 ' +
      escapeHtml(st.as_of || '未知') + ')，环比变化 ' +
      escapeHtml(String(st.change_pct ?? '暂缺')) + '%，'+movement+
      '。股东户数多本身不是利空。</div>';
  } else {
    html += '<div class="evidence-block missing"><b>股东户数:</b> 暂缺</div>';
  }
  const chip=report.chip_price_context || {};
  if(chip.assessment) {
    html += '<div class="evidence-block"><b>股价与筹码联合判断:</b> '+
      escapeHtml(chip.assessment) +
      (chip.position_52w_pct === null || chip.position_52w_pct === undefined?'':'；52周位置 '+Number(chip.position_52w_pct).toFixed(1)+'%')+
      '；股东户数报告期 '+escapeHtml(chip.holder_period || '未知')+
      '，股价时点 '+escapeHtml(chip.price_date || '未知')+'</div>';
  }

  const mg = report.margin_signal;
  if (mg) {
    const fs = (report.valuation || {}).float_shares;
    const lp = report.realtime_quote ? report.realtime_quote.latest_price : null;
    let mgText = '融资余额 ' + mg.latest_balance_yi + ' 亿 (' + mg.latest_date + ')，近 ' + mg.days +
      ' 个交易日 ' + mg.first_balance_yi + ' → ' + mg.latest_balance_yi + ' 亿 (' +
      (mg.change_pct >= 0 ? '+' : '') + mg.change_pct + '%)';
    if (fs && lp) {
      mgText += '，占流通市值 ' + (mg.latest_balance_yi / (fs * lp / 1e8) * 100).toFixed(2) +
        '% (流通股本 ' + (fs / 1e8).toFixed(2) + ' 亿股)';
    }
    html += '<div class="evidence-block"><b>融资盘与流通盘:</b> ' + escapeHtml(mgText) + '</div>';
  } else {
    html += '<div class="evidence-block missing"><b>融资盘与流通盘:</b> 暂缺</div>';
  }

  const ash = report.a_share_structure;
  if (ash) {
    let ashHtml = '<div class="evidence-block"><b>A股公开资金结构 (' +
      escapeHtml(ash.report_period || '报告期未知') + '):</b>';
    const inst = ash.institution_summary || [];
    if (inst.length) {
      ashHtml += '<ul>' + inst.map(x => {
        let s = escapeHtml(x.type || '') + ': ' + (x.institutions || 0) + ' 家';
        if (x.latest_float_ratio_pct !== null && x.latest_float_ratio_pct !== undefined) {
          s += '，合计占流通股 ' + x.latest_float_ratio_pct + '%';
        }
        if (x.float_ratio_change_pct !== null && x.float_ratio_change_pct !== undefined) {
          s += '，较前期 ' + (x.float_ratio_change_pct >= 0 ? '+' : '') + x.float_ratio_change_pct + '%';
        }
        return '<li>' + s + '</li>';
      }).join('') + '</ul>';
    }
    const qoq = ash.institution_qoq || [];
    if (qoq.length) {
      ashHtml += '<details open><summary style="cursor:pointer;"><b>机构季度变化 ' +
        escapeHtml((ash.previous_report_period || '前期') + ' → ' + (ash.report_period || '本期')) +
        '</b></summary><ul>' +
        qoq.map(x => {
          const parts = [];
          if (x.float_ratio_change_pp !== null && x.float_ratio_change_pp !== undefined) {
            parts.push('占流通股 ' + (x.float_ratio_change_pp >= 0 ? '+' : '') + x.float_ratio_change_pp + 'pct');
          }
          if (x.shares_change_pct !== null && x.shares_change_pct !== undefined) {
            parts.push('持股数 ' + (x.shares_change_pct >= 0 ? '+' : '') + x.shares_change_pct + '%');
          }
          if (x.institution_count_change !== null && x.institution_count_change !== undefined) {
            parts.push('机构数 ' + (x.institution_count_change >= 0 ? '+' : '') + x.institution_count_change);
          }
          return '<li>' + escapeHtml(x.type || '') + ': ' + escapeHtml(parts.join('，') || '可比数据不足') + '</li>';
        }).join('') + '</ul>' +
        renderInstitutionChangeChart(qoq) +
        '</details>';
    }

    const fundQoq = ash.fund_qoq || {};
    const fundInc = fundQoq.increased || [];
    const fundDec = fundQoq.decreased || [];
    const fundNew = fundQoq.newly_seen || [];
    const fundExit = fundQoq.exited_top_list || [];
    if (fundInc.length || fundDec.length || fundNew.length || fundExit.length) {
      const renderFundMove = x => {
        let move = '';
        if (x.float_ratio_change_pp !== null && x.float_ratio_change_pp !== undefined) {
          move = (x.float_ratio_change_pp >= 0 ? '+' : '') + x.float_ratio_change_pp + 'pct流通股';
        } else if (x.shares_change_pct !== null && x.shares_change_pct !== undefined) {
          move = '持股数 ' + (x.shares_change_pct >= 0 ? '+' : '') + x.shares_change_pct + '%';
        }
        return escapeHtml(x.name || '') + (move ? ' (' + escapeHtml(move) + ')' : '');
      };
      ashHtml += '<details><summary style="cursor:pointer;"><b>公募基金季度变化</b></summary>';
      if (fundInc.length) {
        ashHtml += '<div>增持较多：' + fundInc.slice(0, 6).map(renderFundMove).join('；') + '</div>';
      }
      if (fundDec.length) {
        ashHtml += '<div>减持较多：' + fundDec.slice(0, 6).map(renderFundMove).join('；') + '</div>';
      }
      if (fundNew.length) {
        ashHtml += '<div>本期新见：' + fundNew.slice(0, 6).map(x => escapeHtml(x.name || '')).join('；') + '</div>';
      }
      if (fundExit.length) {
        ashHtml += '<div>本期明细未再见：' + fundExit.slice(0, 6).map(x => escapeHtml(x.name || '')).join('；') + '</div>';
      }
      ashHtml += '<div class="credibility-note">“新见/未再见”只表示本次机构明细中的披露变化，不等于首次买入或全部卖出。</div></details>';
    }

    const special = ash.special_holders || {};
    const labels = {
      national_team: '汇金/证金/国新/诚通等国家资本',
      social_security: '社保基金',
      insurance: '保险资金',
      foreign: '香港中央结算/QFII等境外资金',
      public_fund: '公募基金',
    };
    ashHtml += '<div><b>前十大流通股东中特殊资金:</b><ul>';
    Object.keys(labels).forEach(k => {
      const holders = special[k] || [];
      ashHtml += '<li>' + labels[k] + ': ' +
        (holders.length
          ? holders.slice(0, 5).map(h => escapeHtml(h.name || '') +
              (h.float_ratio_pct !== null && h.float_ratio_pct !== undefined
                ? ' (' + h.float_ratio_pct + '%)' : '')).join('；')
          : '本期前十大未见') +
        '</li>';
    });
    ashHtml += '</ul></div>';

    const funds = ash.fund_details || [];
    if (funds.length) {
      ashHtml += '<details><summary style="cursor:pointer;">主要公募基金持仓</summary><ul>' +
        funds.slice(0, 8).map(f => {
          let s = escapeHtml(f.name || '') +
            (f.latest_float_ratio_pct !== null && f.latest_float_ratio_pct !== undefined
              ? '：占流通股 ' + f.latest_float_ratio_pct + '%' : '');
          if (f.float_ratio_change_pct !== null && f.float_ratio_change_pct !== undefined) {
            s += '，较前期 ' + (f.float_ratio_change_pct >= 0 ? '+' : '') + f.float_ratio_change_pct + '%';
          }
          return '<li>' + s + '</li>';
        }).join('') + '</ul></details>';
    }

    const etfs = ash.etf_details || [];
    ashHtml += '<details><summary style="cursor:pointer;">可识别ETF持仓 (' + etfs.length + ')</summary>' +
      (etfs.length
        ? '<ul>' + etfs.slice(0, 8).map(f => {
            let s = escapeHtml(f.name || '') +
              (f.latest_float_ratio_pct !== null && f.latest_float_ratio_pct !== undefined
                ? '：占流通股 ' + f.latest_float_ratio_pct + '%' : '');
            if (f.float_ratio_change_pct !== null && f.float_ratio_change_pct !== undefined) {
              s += '，较前期 ' + (f.float_ratio_change_pct >= 0 ? '+' : '') + f.float_ratio_change_pct + '%';
            }
            return '<li>' + s + '</li>';
          }).join('') + '</ul>'
        : '<div class="credibility-note">本期机构明细中未识别到ETF名称。</div>') +
      '</details>';

    const unlock = ash.unlock_supply || {};
    const upcoming = unlock.upcoming_12m || [];
    if (upcoming.length) {
      ashHtml += '<details open><summary style="cursor:pointer;"><b>未来12个月限售解禁 (' + upcoming.length + '批)</b></summary><ul>' +
        upcoming.slice(0, 8).map(x =>
          '<li>' + escapeHtml(x.date || '') + '：解禁 ' +
          (x.unlock_shares === null || x.unlock_shares === undefined ? '暂缺' : (x.unlock_shares / 1e8).toFixed(2) + ' 亿股') +
          (x.float_market_ratio_pct === null || x.float_market_ratio_pct === undefined
            ? '' : '，约占解禁前流通市值 ' + x.float_market_ratio_pct + '%') +
          (x.type ? '，' + escapeHtml(x.type) : '') +
          '</li>'
        ).join('') + '</ul></details>';
    }

    (ash.notes || []).forEach(n => {
      ashHtml += '<div class="credibility-note">· ' + escapeHtml(n) + '</div>';
    });
    ashHtml += '</div>';
    html += ashHtml;
  } else {
    html += '<div class="evidence-block missing"><b>A股公开资金结构:</b> 暂缺；不能据此推断国家队、公募、险资或外资动向</div>';
  }

  const pt = report.profitability_trend;
  if (pt) {
    html += '<div class="evidence-block"><b>盈利能力与成本弹性:</b><ul>' +
      [pt.gross_margin_trend_note, pt.net_margin_trend_note, pt.roe_trend_note, pt.debt_ratio_trend_note]
        .filter(Boolean).map(n => '<li>' + escapeHtml(n) + '</li>').join('') +
      '</ul></div>';
  } else {
    html += '<div class="evidence-block missing"><b>盈利能力与成本弹性:</b> 暂缺 (本次未能取到财务指标数据)</div>';
  }

  const dc = report.dividend_chart;
  if (dc && dc.length) {
    html += '<div class="evidence-block"><b>分红与回购 (回购折算元/10股并入; 柱顶为等价返还比例，非股息率):</b><br>' +
      renderDividendChart(dc) + '</div>';
  } else {
    const div = report.dividend_history || [];
    if (div.length) {
      html += '<div class="evidence-block"><b>历史分红:</b><ul>' +
        div.slice(0, 12).map(d => '<li>' + escapeHtml(d.announce_date) + ': 每10股派息 ' + d.dividend_per_10_shares + ' 元 (' + escapeHtml(d.progress) + ')</li>').join('') +
        '</ul></div>';
    } else {
      html += '<div class="evidence-block missing"><b>历史分红:</b> 暂缺</div>';
    }
  }

  const bb = report.buyback_history || [];
  if (bb.length) {
    html += '<div class="evidence-block"><b>历史回购:</b><ul>' +
      bb.slice(0, 10).map(b => '<li>' + escapeHtml(b.announce_date) + ': 计划金额区间 [' + b.planned_amount_range[0] + ', ' + b.planned_amount_range[1] +
        ']，已回购 ' + b.actual_amount + ' (' + escapeHtml(b.progress) + ')</li>').join('') +
      '</ul></div>';
  } else {
    html += '<div class="evidence-block missing"><b>历史回购:</b> 暂缺</div>';
  }

  const cs = report.commodity_signal;
  if (cs) {
    html += '<div class="evidence-block"><b>周期商品锚 (' + escapeHtml(cs.route || '未分类') +
      (cs.as_of ? '，截至 ' + escapeHtml(cs.as_of) : '') + '):</b><ul>';
    (cs.anchors || []).forEach(a => {
      let s = escapeHtml(a.name || a.symbol || '') + ': ' +
        (a.latest === null || a.latest === undefined ? '暂缺' : a.latest + ' ' + escapeHtml(a.unit || ''));
      if (a.change_20d_pct !== null && a.change_20d_pct !== undefined) {
        s += '，20日 ' + (a.change_20d_pct >= 0 ? '+' : '') + a.change_20d_pct + '%';
      }
      if (a.change_60d_pct !== null && a.change_60d_pct !== undefined) {
        s += '，60日 ' + (a.change_60d_pct >= 0 ? '+' : '') + a.change_60d_pct + '%';
      }
      if (a.position_1y_pct !== null && a.position_1y_pct !== undefined) {
        s += '，1年位置 ' + a.position_1y_pct + '%';
      }
      html += '<li>' + s + '</li>';
    });
    const copper = cs.copper_cross_market || {};
    if (Object.keys(copper).length) {
      html += '<li>铜产业内外盘背景：沪铜 ' +
        (copper.sh_copper_price === null || copper.sh_copper_price === undefined ? '暂缺' : copper.sh_copper_price + ' ' + escapeHtml(copper.sh_copper_unit || '')) +
        '；COMEX铜 ' +
        (copper.comex_copper_price === null || copper.comex_copper_price === undefined ? '暂缺' : copper.comex_copper_price + ' ' + escapeHtml(copper.comex_copper_unit || '')) +
        '</li>';
    }
    html += '</ul>' +
      '<div class="credibility-note">' +
      escapeHtml(cs.note || '期货连续合约仅作周期方向代理，不等同公司实际结算价。') +
      '</div></div>';
  } else {
    html += '<div class="evidence-block missing"><b>周期商品锚:</b> 暂缺；未匹配到可靠产品代理时不拿其他商品价格替代</div>';
  }

  const mc = report.market_context;
  if (mc) {
    let rows = (mc.indices || []).map(i => {
      const seg = [['上半年', i.h1_pct], ['下半年', i.h2_pct], ['年内', i.ytd_pct]]
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => k + ' ' + (v >= 0 ? '+' : '') + v + '%').join('，');
      return '<li>' + escapeHtml(i.name) + ' 最新 ' + i.latest + ' (' + escapeHtml(i.latest_date) + '): ' + seg + '</li>';
    }).join('');
    if (mc.stock) {
      const s = mc.stock;
      const seg = [['上半年', s.h1_pct], ['下半年', s.h2_pct], ['年内', s.ytd_pct]]
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) => k + ' ' + (v >= 0 ? '+' : '') + v + '%').join('，');
      rows += '<li>个股 最新 ' + s.latest + ' (' + escapeHtml(s.latest_date) + '): ' + seg + '</li>' +
        '<li>个股 52周区间 ' + s.w52_low + ' (' + escapeHtml(s.w52_low_date) + ') ~ ' + s.w52_high + ' (' + escapeHtml(s.w52_high_date) + ')</li>' +
        '<li>个股 ' + escapeHtml(s.hist_start) + ' 以来区间 ' + s.hist_low + ' (' + escapeHtml(s.hist_low_date) + ') ~ ' + s.hist_high + ' (' + escapeHtml(s.hist_high_date) + ')</li>';
    }
    html += '<div class="evidence-block"><b>大盘与风格 (A股市场生态):</b><ul>' + rows + '</ul></div>';
  } else {
    html += '<div class="evidence-block missing"><b>大盘与风格:</b> 暂缺 (本次未能取到指数/个股行情序列)</div>';
  }

  const fs = report.freight_signal;
  if (fs) {
    const ytd = (fs.ytd_pct === null || fs.ytd_pct === undefined) ? '' : '，年内 ' + (fs.ytd_pct >= 0 ? '+' : '') + fs.ytd_pct + '%';
    html += '<div class="evidence-block"><b>运价景气度 (' + escapeHtml(fs.instrument) + '):</b><ul>' +
      '<li>最新 ' + fs.latest + ' 点 (' + escapeHtml(fs.latest_date) + ')' + ytd +
      '，当前处于历史 ' + fs.hist_pct_rank + '% 分位</li>' +
      (fs.milestones || []).map(m => '<li>' + escapeHtml(m.replace(/^· /, '')) + '</li>').join('') +
      '<li>' + escapeHtml(fs.note) + '</li></ul></div>';
  } else {
    html += '<div class="evidence-block missing"><b>运价景气度:</b> 暂缺 (非航运/港口类公司，或运价数据未能取到)</div>';
  }

  const fx = report.rmb_signal;
  if (fx && fx.rmb_trend_note) {
    const overseas = ((report.fundamentals || {}).facts || {}).overseas_revenue_pct;
    html += '<div class="evidence-block"><b>汇率敞口:</b> ' + escapeHtml(fx.rmb_trend_note) +
      (overseas === null || overseas === undefined
        ? '；海外收入占比 暂缺'
        : '；海外收入占比 ' + overseas + '%') + '</div>';
  } else {
    html += '<div class="evidence-block missing"><b>汇率敞口:</b> 暂缺 (本次未能取到人民币汇率趋势)</div>';
  }

  const v = report.valuation;
  if (v) {
    const valCells = [
      ['市盈率(动态)', v.pe_dynamic], ['市盈率(静态)', v.pe_static], ['市净率', v.pb],
      ['每股收益', v.eps], ['每股净资产', v.nav_per_share], ['净资产收益率', v.roe_pct],
      ['毛利率', v.gross_margin_pct], ['股权质押占A股', v.pledge_ratio_pct],
    ].filter(([, x]) => x !== null && x !== undefined)
     .map(([k, x]) => '<li>' + k + ': ' + x + '</li>').join('');
    html += '<div class="evidence-block"><b>估值 (数据日期 ' + escapeHtml(v.valuation_as_of || '未知') + '):</b>' +
      (valCells ? '<ul>' + valCells + '</ul>' : ' 暂缺') + '</div>';
  } else {
    html += '<div class="evidence-block missing"><b>估值:</b> 暂缺 (本次未能取到估值数据)</div>';
  }

  const vh = report.valuation_history;
  if (vh) {
    const peText = (vh.pe_percentiles || []).map(x =>
      x.years + '年 ' + x.percentile + '%分位').join('，');
    const pbText = (vh.pb_percentiles || []).map(x =>
      x.years + '年 ' + x.percentile + '%分位').join('，');
    html += '<div class="evidence-block"><b>历史估值位置:</b> 当前 PE(TTM) ' +
      (vh.current_pe_ttm === null || vh.current_pe_ttm === undefined ? '暂缺' : vh.current_pe_ttm) +
      '，PB ' + (vh.current_pb === null || vh.current_pb === undefined ? '暂缺' : vh.current_pb) +
      (peText ? '<br>PE: ' + escapeHtml(peText) : '') +
      (pbText ? '<br>PB: ' + escapeHtml(pbText) : '');
    if (vh.history_monthly && vh.history_monthly.length >= 6) {
      html += '<br>' + renderValuationHistoryChart(vh.history_monthly);
    }
    (vh.notes || []).forEach(n => {
      html += '<div class="credibility-note">· ' + escapeHtml(n) + '</div>';
    });
    html += '</div>';
  } else {
    html += '<div class="evidence-block missing"><b>历史估值位置:</b> 暂缺</div>';
  }

  html += renderFundamentalsBlock(report.fundamentals);
  html += renderRdTeamBlock(report.rd_team);
  html += renderManagementCapitalBlock(report.management_capital);
  html += renderMacroRatesBlock(report.macro_rates);
  html += renderPolicyEventsBlock(report.policy_events);
  html += renderXueqiuBlock(report.xueqiu_stock);

  const db = report.debate;
  if (db) {
    let dbHtml = '<div class="evidence-block"><b>雪球讨论区多空辩论:</b> 收集 ' + db.posts_collected +
      ' 条表态 (分类 ' + db.classified + ' 条)<ul>' +
      '<li>多方 ' + db.bull.count + ' 条: 有时间范围的股价预测验证 ' + db.bull.verified +
      ' 条 (正确 ' + db.bull.correct + ', 错误 ' + db.bull.incorrect + '), 未验证 ' + db.bull.unverified + ' 条</li>' +
      '<li>空方 ' + db.bear.count + ' 条: 有时间范围的股价预测验证 ' + db.bear.verified +
      ' 条 (正确 ' + db.bear.correct + ', 错误 ' + db.bear.incorrect + '), 未验证 ' + db.bear.unverified + ' 条</li></ul>';
    if (db.bull_core) {
      dbHtml += '<div><b>多方核心论点:</b><br>' +
        db.bull_core.split('\\n').filter(s => s.trim()).map(escapeHtml).join('<br>') + '</div>';
    }
    if (db.bear_core) {
      dbHtml += '<div><b>空方核心论点:</b><br>' +
        db.bear_core.split('\\n').filter(s => s.trim()).map(escapeHtml).join('<br>') + '</div>';
    }
    if (db.verdict) {
      dbHtml += '<div><b>哪方更合理: ' + escapeHtml(db.verdict) + '</b> — ' + escapeHtml(db.reason) + '</div>';
    }
    dbHtml += '</div>';
    html += dbHtml;
  }

  const se = report.sentiment;
  if (se) {
    html += '<div class="evidence-block"><b>雪球讨论区情绪 (反向指标):</b> 收集 ' + se.posts_collected +
      ' 条表态 (' + se.users + ' 位用户)，看多 ' + se.bullish + ' (有论据 ' + se.reasoned_bullish + ')，' +
      '看空 ' + se.bearish + ' (有论据 ' + se.reasoned_bearish + ')，中性 ' + se.neutral +
      '，无关 ' + se.irrelevant + '；看多占方向性表态 ' + (se.bullish_ratio * 100).toFixed(1) + '%';
    if (se.note) {
      html += '<br><b style="color:#d33;">' + escapeHtml(se.note) + '</b>';
    }
    html += '</div>';
  }

  html += '</div>';
  return html;
}

// 年度分红+回购柱状图 (回购按总股本折算成 元/10股 并入分红, 同一条 y 轴)。
// 柱顶标注等价回报比例（含回购，非股息率）; 悬停显示数值, 图下附数据表。
function renderDividendChart(data) {
  const n = data.length;
  const W = 640, H = 250, padL = 52, padR = 16, padT = 40, padB = 36;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxV = (Math.max(...data.map(d => d.total_per_10)) * 1.15) || 1;
  const yOf = v => padT + plotH - (v / maxV) * plotH;
  const slot = plotW / n;
  const barW = Math.min(slot * 0.55, 46);

  let bars = '', labels = '';
  data.forEach((d, i) => {
    const xc = padL + slot * i + slot / 2;
    const yDiv = yOf(d.dividend_per_10);
    const yTotal = yOf(d.total_per_10);
    bars += '<rect class="holder-mark" x="' + (xc - barW / 2).toFixed(1) + '" y="' + yDiv.toFixed(1) +
      '" width="' + barW.toFixed(1) + '" height="' + Math.max(0.5, padT + plotH - yDiv).toFixed(1) +
      '" fill="#1a73e8" fill-opacity="0.85" data-tip="' + escapeHtml(d.year) +
      ': 分红 ' + d.dividend_per_10 + ' 元/10股"/>';
    if (d.buyback_per_10 > 0) {
      // Stacking must use cumulative total, not yOf(buyback) in isolation.
      bars += '<rect class="holder-mark" x="' + (xc - barW / 2).toFixed(1) + '" y="' + yTotal.toFixed(1) +
        '" width="' + barW.toFixed(1) + '" height="' + Math.max(0.5, yDiv - yTotal).toFixed(1) +
        '" fill="#d97706" stroke="#fff" stroke-width="2" data-tip="' + escapeHtml(d.year) +
        ': 回购折算 ' + d.buyback_per_10 + ' 元/10股"/>';
    }
    labels += '<text x="' + xc.toFixed(1) + '" y="' + (yTotal - 7).toFixed(1) +
      '" text-anchor="middle" font-size="11" fill="#666">' +
      (d.yield_pct === null || d.yield_pct === undefined ? '--' : d.yield_pct + '%') + '</text>';
  });

  let xLabels = '';
  data.forEach((d, i) => {
    if (n > 8 && i % 2 === 1) return;
    const xc = padL + slot * i + slot / 2;
    xLabels += '<text x="' + xc.toFixed(1) + '" y="' + (H - 12).toFixed(1) +
      '" text-anchor="middle" font-size="11" fill="#444">' + escapeHtml(d.year) + '</text>';
  });

  let grid = '';
  const steps = 4;
  for (let s = 0; s <= steps; s++) {
    const v = maxV * s / steps;
    const y = yOf(v);
    grid += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) +
      '" stroke="' + (s === 0 ? '#d9d9d9' : '#eeeeee') + '" stroke-width="1"/>';
    grid += '<text x="' + (padL - 8) + '" y="' + (y + 4).toFixed(1) +
      '" text-anchor="end" font-size="11" fill="#999">' + v.toFixed(1) + '</text>';
  }

  const legend = '<g font-size="12" fill="#444">' +
    '<rect x="' + padL + '" y="10" width="12" height="12" fill="#1a73e8" fill-opacity="0.85"/>' +
    '<text x="' + (padL + 18) + '" y="20">分红</text>' +
    '<rect x="' + (padL + 72) + '" y="10" width="12" height="12" fill="#d97706"/>' +
    '<text x="' + (padL + 90) + '" y="20">回购 (折算元/10股)</text></g>';

  const table = '<table style="border-collapse:collapse;font-size:12px;margin-top:6px;"><tr>' +
    '<td style="padding:2px 6px;color:#666;">年度</td>' +
    data.map(d => '<td style="padding:2px 8px;border-left:1px solid #eee;color:#666;text-align:center;">' +
      escapeHtml(d.year) + '</td>').join('') + '</tr><tr>' +
    '<td style="padding:2px 6px;color:#666;">分红(元/10股)</td>' +
    data.map(d => '<td style="padding:2px 8px;border-left:1px solid #eee;text-align:center;">' +
      d.dividend_per_10 + '</td>').join('') + '</tr><tr>' +
    '<td style="padding:2px 6px;color:#666;">回购折算(元/10股)</td>' +
    data.map(d => '<td style="padding:2px 8px;border-left:1px solid #eee;text-align:center;">' +
      d.buyback_per_10 + '</td>').join('') + '</tr><tr>' +
    '<td style="padding:2px 6px;color:#666;">合计(元/10股)</td>' +
    data.map(d => '<td style="padding:2px 8px;border-left:1px solid #eee;text-align:center;">' +
      d.total_per_10 + '</td>').join('') + '</tr><tr>' +
    '<td style="padding:2px 6px;color:#666;">等价回报比例(含回购，非股息率)</td>' +
    data.map(d => '<td style="padding:2px 8px;border-left:1px solid #eee;text-align:center;">' +
      (d.yield_pct === null || d.yield_pct === undefined ? '--' : d.yield_pct + '%') + '</td>').join('') +
    '</tr></table>';

  return '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" style="max-width:100%;"' +
    ' role="img" aria-label="年度分红与回购柱状图">' +
    grid + bars + labels + xLabels + legend + '</svg>' +
    '<p class="section-caption">回购不是现金分红；合计比例仅为按现价折算的资本返还指标，不等于真实现金股息率。</p>' + table;
}

// 股东户数(柱,蓝) + 同期股价(线,橙) 走势图。两条序列都以首期=100 指数化、
// 共用一条 y 轴 (避免双轴混标); 悬停显示原始数值, 图下附原始数据表。
function renderHolderChart(series) {
  const n = series.length;
  const W = 720, H = 250, padL = 52, padR = 16, padT = 30, padB = 36;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const first = series[0];
  const idxHolders = series.map(p => (p.holders / first.holders) * 100);
  const idxPrice = series.map(p => (p.price / first.price) * 100);
  let min = Math.min(...idxHolders, ...idxPrice);
  let max = Math.max(...idxHolders, ...idxPrice);
  const spread = (max - min) * 0.15 || 5;
  min -= spread; max += spread;
  const yOf = v => padT + plotH - ((v - min) / (max - min)) * plotH;
  const slot = plotW / n;
  const barW = Math.min(slot * 0.5, 36);

  let bars = '', linePts = '', dots = '';
  series.forEach((p, i) => {
    const xc = padL + slot * i + slot / 2;
    const yTop = yOf(idxHolders[i]);
    bars += '<rect class="holder-mark" x="' + (xc - barW / 2).toFixed(1) + '" y="' + yTop.toFixed(1) +
      '" width="' + barW.toFixed(1) + '" height="' + (padT + plotH - yTop).toFixed(1) +
      '" fill="#1a73e8" fill-opacity="0.85" data-tip="' +
      escapeHtml(p.period) + ': 股东户数 ' + p.holders + ' 户 (指数 ' + idxHolders[i].toFixed(1) + ')"/>';
    const py = yOf(idxPrice[i]);
    linePts += (linePts ? ' ' : '') + xc.toFixed(1) + ',' + py.toFixed(1);
    dots += '<circle class="holder-mark" cx="' + xc.toFixed(1) + '" cy="' + py.toFixed(1) +
      '" r="4.5" fill="#d97706" stroke="#fff" stroke-width="1.5" data-tip="' +
      escapeHtml(p.period) + ': 股价 ' + p.price + ' 元 (指数 ' + idxPrice[i].toFixed(1) + ')"/>';
  });

  let xLabels = '';
  series.forEach((p, i) => {
    if (n > 6 && i % 2 === 1) return;  // 标签过密时抽稀
    const xc = padL + slot * i + slot / 2;
    xLabels += '<text x="' + xc.toFixed(1) + '" y="' + (H - 12).toFixed(1) +
      '" text-anchor="middle" font-size="11" fill="#444">' +
      escapeHtml(String(p.period).slice(0, 10)) + '</text>';
  });

  let grid = '';
  const steps = 4;
  for (let s = 0; s <= steps; s++) {
    const v = min + (max - min) * s / steps;
    const y = yOf(v);
    grid += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) +
      '" stroke="' + (s === 0 || s === steps ? '#d9d9d9' : '#eeeeee') + '" stroke-width="1"/>';
    grid += '<text x="' + (padL - 8) + '" y="' + (y + 4).toFixed(1) +
      '" text-anchor="end" font-size="11" fill="#999">' + Math.round(v) + '</text>';
  }

  const legend = '<g font-size="12" fill="#444">' +
    '<rect x="' + padL + '" y="10" width="12" height="12" fill="#1a73e8" fill-opacity="0.85"/>' +
    '<text x="' + (padL + 18) + '" y="20">股东户数 (首期=100)</text>' +
    '<circle cx="' + (padL + 168) + '" cy="16" r="4.5" fill="#d97706"/>' +
    '<text x="' + (padL + 180) + '" y="20">股价 (首期=100)</text></g>';

  const table = '<table style="border-collapse:collapse;font-size:12px;margin-top:6px;"><tr>' +
    '<td style="padding:2px 6px;color:#666;">期间</td>' +
    series.map(p => '<td style="padding:2px 8px;border-left:1px solid #eee;color:#666;text-align:center;">' +
      escapeHtml(String(p.period).slice(0, 10)) + '</td>').join('') + '</tr><tr>' +
    '<td style="padding:2px 6px;color:#666;">股东户数</td>' +
    series.map(p => '<td style="padding:2px 8px;border-left:1px solid #eee;text-align:center;">' +
      p.holders + '</td>').join('') + '</tr><tr>' +
    '<td style="padding:2px 6px;color:#666;">股价(元)</td>' +
    series.map(p => '<td style="padding:2px 8px;border-left:1px solid #eee;text-align:center;">' +
      p.price + '</td>').join('') + '</tr></table>';

  return '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" style="max-width:100%;"' +
    ' role="img" aria-label="股东户数与同期股价走势 (首期=100 指数化)">' +
    grid + bars +
    '<polyline points="' + linePts + '" fill="none" stroke="#d97706" stroke-width="2"/>' +
    dots + xLabels + legend + '</svg>' + table;
}

// 十二维度雷达图 (内联 SVG): 顶点=维度, 值=-10(利空)~+10(利多)。
// 极性除颜色外还有位置(相对0环)与数字双重编码; 正红负绿沿用本界面方向色。
function renderDimensionRadar(scores) {
  const n = scores.length;
  const cx = 220, cy = 190, R = 140;
  let grid = '', axes = '', dots = '', labels = '';

  for (const frac of [0.25, 0.5, 0.75, 1]) {
    const r = R * frac;
    grid += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' +
      (frac === 0.5 ? '#b5b5b5' : '#e6e6e6') + '" stroke-width="' + (frac === 0.5 ? 1.5 : 1) + '"/>';
  }
  const pts = scores.map((s, i) => {
    const ang = -Math.PI / 2 + i * 2 * Math.PI / n;
    const x = cx + R * Math.cos(ang), y = cy + R * Math.sin(ang);
    axes += '<line x1="' + cx + '" y1="' + cy + '" x2="' + x.toFixed(1) + '" y2="' + y.toFixed(1) +
      '" stroke="#e6e6e6" stroke-width="1"/>';
    const r = R * (s.score + 10) / 20;
    return [cx + r * Math.cos(ang), cy + r * Math.sin(ang)];
  });
  const poly = pts.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');

  scores.forEach((s, i) => {
    const ang = -Math.PI / 2 + i * 2 * Math.PI / n;
    const x = pts[i][0], y = pts[i][1];
    const color = s.score > 0 ? '#dd3333' : (s.score < 0 ? '#2a9d3f' : '#888888');
    dots += '<circle class="radar-dot" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) +
      '" r="5" fill="' + color + '" stroke="#fff" stroke-width="1.5" data-note="' +
      escapeHtml(s.note || '') + '"/>';
    const lx = cx + (R + 26) * Math.cos(ang);
    const ly = cy + (R + 26) * Math.sin(ang);
    labels += '<text x="' + lx.toFixed(1) + '" y="' + (ly + 3).toFixed(1) +
      '" text-anchor="middle" font-size="12" fill="#444">' + escapeHtml(s.dimension) + '</text>';
    labels += '<text x="' + lx.toFixed(1) + '" y="' + (ly + 17).toFixed(1) +
      '" text-anchor="middle" font-size="11" font-weight="bold" fill="' + color + '">' +
      (s.score > 0 ? '+' : '') + s.score + '</text>';
  });

  return '<svg viewBox="0 0 440 395" width="440" height="395" role="img" ' +
    'aria-label="十二维度评分雷达图">' +
    grid + axes +
    '<polygon points="' + poly + '" fill="#1a73e8" fill-opacity="0.20" stroke="#1a73e8" stroke-width="2"/>' +
    dots + labels + '</svg>';
}

function renderInstitutionChangeChart(rows) {
  const data = (rows || []).filter(x =>
    x.float_ratio_change_pp !== null && x.float_ratio_change_pp !== undefined
  );
  const fallback = !data.length
    ? (rows || []).filter(x => x.shares_change_pct !== null && x.shares_change_pct !== undefined)
    : [];
  const source = data.length ? data : fallback;
  if (!source.length) return '';

  const useRatio = data.length > 0;
  const values = source.map(x => Number(
    useRatio ? x.float_ratio_change_pp : x.shares_change_pct
  )).filter(Number.isFinite);
  if (!values.length) return '';

  const maxAbs = Math.max(0.1, ...values.map(Math.abs));
  const W = 680, rowH = 34, top = 28, bottom = 24;
  const H = top + bottom + source.length * rowH;
  const labelW = 130, axisX = 390, halfW = 230;

  let svg = '<svg viewBox="0 0 ' + W + ' ' + H +
    '" width="100%" role="img" aria-label="机构季度持仓变化">';
  svg += '<text x="' + axisX + '" y="14" text-anchor="middle" font-size="11" fill="#666">' +
    (useRatio ? '占流通股比例变化（百分点）' : '持股数变化（%）') + '</text>';
  svg += '<line x1="' + axisX + '" y1="' + (top - 6) + '" x2="' + axisX +
    '" y2="' + (H - bottom + 2) + '" stroke="#aaa" stroke-width="1"/>';

  source.forEach((x, i) => {
    const value = Number(useRatio ? x.float_ratio_change_pp : x.shares_change_pct);
    if (!Number.isFinite(value)) return;
    const y = top + i * rowH + 7;
    const width = Math.abs(value) / maxAbs * halfW;
    const left = value >= 0 ? axisX : axisX - width;
    svg += '<text x="' + (labelW - 8) + '" y="' + (y + 12) +
      '" text-anchor="end" font-size="12" fill="#333">' +
      escapeHtml(x.type || '') + '</text>';
    svg += '<rect class="holder-mark" x="' + left.toFixed(1) + '" y="' + y +
      '" width="' + Math.max(1, width).toFixed(1) + '" height="16" rx="2" ' +
      'fill="currentColor" fill-opacity="0.55" data-tip="' +
      escapeHtml((x.type || '') + ' ' + (value >= 0 ? '+' : '') + value +
        (useRatio ? ' pct' : '%')) + '"/>';
    svg += '<text x="' + (value >= 0 ? axisX + width + 6 : axisX - width - 6).toFixed(1) +
      '" y="' + (y + 12) + '" text-anchor="' + (value >= 0 ? 'start' : 'end') +
      '" font-size="11" fill="#444">' + (value >= 0 ? '+' : '') + value + '</text>';
  });
  svg += '</svg>';
  return '<div style="margin-top:8px;">' + svg + '</div>';
}

function renderDimensionBars(scores) {
  const rows = scores || [];
  const W = 720, rowH = 34, top = 22, bottom = 24;
  const H = top + bottom + rows.length * rowH;
  const labelW = 150, axisX = 430, halfW = 245;
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" aria-label="十二维度倾向条形图">';
  svg += '<line x1="' + axisX + '" y1="' + (top - 8) + '" x2="' + axisX + '" y2="' + (H - bottom + 2) +
    '" stroke="#aaa" stroke-width="1"/>';
  svg += '<text x="' + (axisX - halfW) + '" y="13" font-size="11" fill="#666">-10 利空</text>' +
    '<text x="' + (axisX + halfW - 36) + '" y="13" font-size="11" fill="#666">+10 利多</text>';
  rows.forEach((s, i) => {
    const y = top + i * rowH + 8;
    const score = Math.max(-10, Math.min(10, Number(s.score) || 0));
    const width = Math.abs(score) / 10 * halfW;
    const x = score >= 0 ? axisX : axisX - width;
    const fill = score >= 0 ? '#d93025' : '#188038';
    svg += '<text x="' + (labelW - 8) + '" y="' + (y + 12) + '" text-anchor="end" font-size="12" fill="#333">' +
      escapeHtml(s.dimension || '') + '</text>';
    svg += '<rect class="holder-mark" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
      '" width="' + Math.max(1, width).toFixed(1) + '" height="16" rx="2" fill="' + fill +
      '" fill-opacity="0.78" data-tip="' + escapeHtml(s.note || '') + '"/>';
    svg += '<text x="' + (score >= 0 ? axisX + width + 6 : axisX - width - 6).toFixed(1) +
      '" y="' + (y + 12) + '" text-anchor="' + (score >= 0 ? 'start' : 'end') +
      '" font-size="11" font-weight="bold" fill="' + fill + '">' +
      (score > 0 ? '+' : '') + score + '</text>';
  });
  svg += '</svg>';
  return svg;
}

function renderValuationHistoryChart(data) {
  const rows = (data || []).filter(x => x.pe_ttm !== null && x.pe_ttm !== undefined && x.pe_ttm > 0);
  if (rows.length < 3) return '';
  const vals = rows.map(x => Number(x.pe_ttm)).filter(Number.isFinite).sort((a, b) => a - b);
  if (vals.length < 3) return '';
  const q = p => vals[Math.min(vals.length - 1, Math.max(0, Math.floor((vals.length - 1) * p)))];
  let lo = q(0.05), hi = q(0.95);
  if (!(hi > lo)) { lo = vals[0]; hi = vals[vals.length - 1] || lo + 1; }
  if (!(hi > lo)) hi = lo + 1;
  const W = 680, H = 220, padL = 52, padR = 18, padT = 18, padB = 34;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const xOf = i => padL + (rows.length === 1 ? 0 : i / (rows.length - 1)) * plotW;
  const yOf = v => {
    const clipped = Math.max(lo, Math.min(hi, v));
    return padT + plotH - (clipped - lo) / (hi - lo) * plotH;
  };
  const points = rows.map((r, i) => xOf(i).toFixed(1) + ',' + yOf(Number(r.pe_ttm)).toFixed(1)).join(' ');
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" aria-label="PE历史走势">';
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4;
    const y = yOf(v);
    svg += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) +
      '" y2="' + y.toFixed(1) + '" stroke="#eee"/>' +
      '<text x="' + (padL - 6) + '" y="' + (y + 4).toFixed(1) +
      '" text-anchor="end" font-size="10" fill="#666">' + v.toFixed(1) + '</text>';
  }
  svg += '<polyline points="' + points + '" fill="none" stroke="#1a73e8" stroke-width="2"/>';
  const last = rows[rows.length - 1];
  svg += '<circle class="holder-mark" cx="' + xOf(rows.length - 1).toFixed(1) +
    '" cy="' + yOf(Number(last.pe_ttm)).toFixed(1) + '" r="4" fill="#1a73e8" data-tip="' +
    escapeHtml(last.date + ' PE(TTM) ' + last.pe_ttm) + '"/>';
  svg += '<text x="' + padL + '" y="' + (H - 10) + '" font-size="10" fill="#666">' +
    escapeHtml(rows[0].date) + '</text>' +
    '<text x="' + (W - padR) + '" y="' + (H - 10) + '" text-anchor="end" font-size="10" fill="#666">' +
    escapeHtml(last.date) + '</text>' +
    '<text x="' + (W / 2) + '" y="12" text-anchor="middle" font-size="11" fill="#666">' +
    'PE(TTM) 月度历史（纵轴按5%~95%分位裁剪显示）</text>';
  svg += '</svg>';
  return svg;
}

function attachRadarTooltips() {
  const tip = document.getElementById('radarTip');
  if (!tip) return;
  document.querySelectorAll('.radar-dot, .holder-mark').forEach(mark => {
    mark.addEventListener('mousemove', (e) => {
      const text = mark.getAttribute('data-note') || mark.getAttribute('data-tip') || '';
      if (!text) return;
      tip.style.display = 'block';
      tip.style.left = (e.clientX + 14) + 'px';
      tip.style.top = (e.clientY + 14) + 'px';
      tip.textContent = text;
    });
    mark.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  });
}


function numericOrNull(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}
function metricCard(label, value, source) {
  return '<div class="metric"><label>' + escapeHtml(label) + '</label><strong>' +
    escapeHtml(String(value)) + '</strong><small>' + escapeHtml(source) + '</small></div>';
}
function chartTile(title, subtitle, image) {
  return '<div class="chart-tile"><h4>' + escapeHtml(title) + '</h4><p class="section-caption">' +
    escapeHtml(subtitle) + '</p>' + (image || '<div class="chart-empty">当前没有足够的可比数据，暂不绘图</div>') + '</div>';
}

// Three reported-ratio trend lines. Values are shown as reported: do not
// annualize interim YTD ROE and do not interpolate missing observations.
function renderProfitabilityTrendChart(trend) {
  const rows = ((trend || {}).periods || []).filter(x => x.period).slice().sort((a,b)=>String(a.period).localeCompare(String(b.period))).slice(-9);
  if (rows.length < 2) return '';
  const metrics = [
    {key:'roe_pct',name:'ROE',color:'#5362d5'},
    {key:'gross_margin_pct',name:'毛利率',color:'#0a9c9a'},
    {key:'net_margin_pct',name:'净利率',color:'#dc8654'},
  ];
  const observed = [];
  rows.forEach(row => metrics.forEach(m => {
    const value = numericOrNull(row[m.key]); if (value !== null) observed.push(value);
  }));
  if (observed.length < 3) return '';
  const W=640,H=252,L=50,R=16,T=34,B=51,plotW=W-L-R,plotH=H-T-B;
  let low=Math.min(...observed,0), high=Math.max(...observed,0);
  const span=Math.max(4,high-low); low-=span*.10; high+=span*.10;
  const x=i=>L + i*plotW/(rows.length-1);
  const y=v=>T+(high-v)*plotH/(high-low);
  let svg='<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img" aria-label="报告期ROE毛利率净利率趋势">';
  for(let j=0;j<=4;j++) {
    const val=low+(high-low)*j/4, yp=y(val);
    svg+='<line x1="'+L+'" y1="'+yp.toFixed(1)+'" x2="'+(W-R)+'" y2="'+yp.toFixed(1)+'" stroke="#e8edf6"/>';
    svg+='<text x="'+(L-7)+'" y="'+(yp+4).toFixed(1)+'" text-anchor="end" font-size="11" fill="#6c7c97">'+val.toFixed(1)+'%</text>';
  }
  metrics.forEach((m,j) => {
    const legendX=L+j*150;
    svg+='<line x1="'+legendX+'" y1="12" x2="'+(legendX+18)+'" y2="12" stroke="'+m.color+'" stroke-width="3"/>';
    svg+='<text x="'+(legendX+23)+'" y="16" font-size="12" fill="#40516e">'+m.name+'</text>';
    let segment=[];
    const flush=()=>{ if(segment.length>=2) svg+='<polyline points="'+segment.join(' ')+'" fill="none" stroke="'+m.color+'" stroke-width="2.6" stroke-linejoin="round"/>'; segment=[]; };
    rows.forEach((row,i)=>{
      const v=numericOrNull(row[m.key]);
      if(v===null){flush();return;}
      segment.push(x(i).toFixed(1)+','+y(v).toFixed(1));
      svg+='<circle class="holder-mark" cx="'+x(i).toFixed(1)+'" cy="'+y(v).toFixed(1)+'" r="3.4" fill="'+m.color+'" data-tip="'+
        escapeHtml(String(row.period)+' '+m.name+' '+v+'%')+'"/>';
    });
    flush();
  });
  rows.forEach((row,i)=>{
    if(i!==0 && i!==rows.length-1 && i%2===1) return;
    svg+='<text x="'+x(i).toFixed(1)+'" y="'+(H-23)+'" text-anchor="middle" font-size="10" fill="#6c7c97">'+
      escapeHtml(String(row.period).slice(2,7))+'</text>';
  });
  svg+='</svg>';
  return svg;
}
function renderSentimentMixChart(sentiment) {
  if(!sentiment) return '';
  const values=[
    ['看多',numericOrNull(sentiment.bullish),'#cc5364'],
    ['看空',numericOrNull(sentiment.bearish),'#18977b'],
    ['中性',numericOrNull(sentiment.neutral),'#9ba8bd']
  ];
  if(values.some(x=>x[1]===null)||values.reduce((a,x)=>a+x[1],0)<=0) return '';
  const total=values.reduce((a,x)=>a+x[1],0);
  let pos=0, svg='<svg viewBox="0 0 590 108" width="100%" role="img" aria-label="雪球观点方向比例">';
  values.forEach((item,i)=>{
    const width=item[1]/total*550;
    svg+='<rect x="'+(20+pos).toFixed(1)+'" y="17" width="'+width.toFixed(1)+'" height="32" fill="'+item[2]+'"><title>'+
      escapeHtml(item[0]+': '+item[1])+'</title></rect>';
    svg+='<circle cx="'+(25+i*184)+'" cy="79" r="5" fill="'+item[2]+'"/>';
    svg+='<text x="'+(36+i*184)+'" y="83" font-size="12" fill="#40516e">'+item[0]+' '+(item[1]/total*100).toFixed(1)+'%</text>';
    pos+=width;
  });
  return svg+'</svg>';
}

function renderIndustryBreadthChart(industry) {
  if (!industry) return '';
  const adv=numericOrNull(industry.advancing),dec=numericOrNull(industry.declining);
  if (adv===null || dec===null || adv<0 || dec<0 || adv+dec<=0) return '';
  const total=adv+dec,aw=adv/total*540,dw=dec/total*540;
  return '<svg viewBox="0 0 590 108" width="100%" role="img" aria-label="行业上涨和下跌家数">'+
    '<rect x="20" y="14" width="'+aw.toFixed(1)+'" height="34" rx="5" fill="#ce5c68"/>'+
    '<rect x="'+(20+aw).toFixed(1)+'" y="14" width="'+dw.toFixed(1)+'" height="34" rx="5" fill="#159a79"/>'+
    '<text x="20" y="80" font-size="12" fill="#ad3e53">上涨 '+adv+' 家（'+(100*adv/total).toFixed(0)+'%）</text>'+
    '<text x="290" y="80" font-size="12" fill="#197c68">下跌 '+dec+' 家（'+(100*dec/total).toFixed(0)+'%）</text>'+
    '</svg>';
}

function renderChipPositionChart(context) {
  if(!context || numericOrNull(context.position_52w_pct)===null) return '';
  const p=Math.max(0,Math.min(100,Number(context.position_52w_pct)));
  const change=numericOrNull(context.holder_change_pct);
  const status=context.status === 'contextualized';
  const desc=status ? (change>0?'增加 '+change+'%':change<0?'减少 '+Math.abs(change)+'%':'基本持平') : '数据缺失或过期';
  const risk=context.holder_context === 'high_price_more_holders_watch';
  const label=risk?'高位分散：待核验':context.holder_context==='low_price_more_holders_neutral'?'低位增户：不自动扣分':'暂无明确筹码方向';
  let svg='<svg viewBox="0 0 590 132" width="100%" role="img" aria-label="52周价格位置及股东户数变化">';
  svg+='<defs><linearGradient id="chip-location-gradient"><stop offset="0" stop-color="#18a086"/>'+
    '<stop offset=".5" stop-color="#c6d1e4"/><stop offset="1" stop-color="#dd7776"/></linearGradient></defs>';
  svg+='<rect x="24" y="30" width="540" height="18" rx="9" fill="url(#chip-location-gradient)"/>';
  const px=24+540*p/100;
  svg+='<path d="M'+px.toFixed(1)+' 25 l-7 -10 h14 Z" fill="#223354"/>';
  svg+='<text x="24" y="67" font-size="11" fill="#60728c">52周低位</text>'+
    '<text x="564" y="67" text-anchor="end" font-size="11" fill="#60728c">52周高位</text>';
  svg+='<text x="24" y="92" font-size="15" font-weight="bold" fill="'+(risk?'#bd5264':'#365078')+'">'+
    escapeHtml('位置 '+p.toFixed(1)+'% · '+label)+'</text>';
  svg+='<text x="24" y="115" font-size="12" fill="#60728c">'+
    escapeHtml('股东户数 '+desc+'；报告期 '+(context.holder_period || '未知'))+'</text></svg>';
  return svg;
}

function renderDashboardCharts(report) {
  const quote=report.realtime_quote || {};
  const val=report.valuation || {};
  const facts=(report.fundamentals || {}).facts || {};
  const scores=(report.summary || {}).dimension_scores || [];
  const price=numericOrNull(quote.latest_price);
  const pb=numericOrNull(val.pb);
  const roe=numericOrNull(val.roe_pct);
  const cashRatio=numericOrNull(facts.cash_to_profit_ratio);
  let html='<section class="dashboard-charts"><h3>数据概览与维度图谱</h3>'+
    '<p class="section-caption">图表仅展示当前报告真实返回的数据，不对缺失年份插值，也不自动将半年ROE年化。</p>';
  html+='<div class="metric-strip">'+
    metricCard('参考股价',price===null?'暂缺':price.toFixed(2)+' 元',quote.quote_time || report.as_of || '报价时点未知')+
    metricCard('市净率 PB',pb===null?'暂缺':pb.toFixed(2)+' 倍',val.valuation_as_of || 'F10时点未知')+
    metricCard('F10 披露ROE',roe===null?'暂缺':roe.toFixed(2)+'%',val.valuation_as_of || '需注意报告期间')+
    metricCard('现金流/利润',cashRatio===null?'暂缺':cashRatio.toFixed(2)+' 倍',facts.finance_period || '报告期未知')+
    '</div>';
  html+='<div class="chart-grid" style="margin-top:16px;">';
  html+=chartTile('盈利质量的变化','ROE、毛利率与净利率；季报ROE为报告期间累计数，不跨期年化',
    renderProfitabilityTrendChart(report.profitability_trend));
  html+=chartTile('十二方面的多空力度','分数从 -10 到 +10，数值越高表示观点越积极',
    scores.length>=6?renderDimensionBars(scores):'');
  html+=chartTile('历年分红与回购','金额和回购分开理解，原图含历史金额及备注',
    (report.dividend_chart || []).length?renderDividendChart(report.dividend_chart):'');
  html+=chartTile('市场讨论结构','雪球言论只能作为情绪信号，不能替代财务事实',
    renderSentimentMixChart(report.sentiment));
  html+=chartTile('行业上涨与下跌家数','同一交易时点的行业广度，不能独立当作投资结论',
    renderIndustryBreadthChart(report.industry_comparison));
  html+=chartTile('股价位置 × 筹码分散','先看52周位置，再看股东户数增减；低位散户多不单独扣分',
    renderChipPositionChart(report.chip_price_context));
  html+=chartTile('历史估值区间','PE(TTM)历史序列，失真区间将按原图分位裁剪',
    renderValuationHistoryChart((report.valuation_history || {}).history_monthly || []));
  html+='</div></section>';
  return html;
}


// Deprecated universal ROE-perpetuity valuation sliders were removed.
// The only stock price calculator now lives in the source-gated industry model panel.

// Render precisely one full research narrative. Never duplicate it as a
// per-dimension thesis overview, and never hide the detailed analysis in <details>.
function renderDimensionAnalysisSection(summary) {
  const analyses = (summary || {}).dimension_analyses || {};
  const order = [
    ['management','管理层'], ['fundamentals','经营基本面'],
    ['rd','研发能力'], ['chip_flow','筹码与资金'],
    ['price_position','股价位置'], ['cycle_position','行业周期'],
    ['policy_geopolitics','政策与国际形势'], ['retail_sentiment','市场情绪'],
    ['shareholder_returns','股东回报'], ['growth_elasticity','成长弹性'],
    ['a_share_structure','A股资金结构'], ['risk_quality','财务质量与尾部风险']
  ];
  const valid = order.filter(([key]) =>
    typeof analyses[key] === 'string' && analyses[key].trim());
  if (!valid.length) {
    const fallback = String((summary || {}).thesis_summary || '').trim();
    return fallback
      ? '<section class="research-panel"><h3>研究分析</h3><p style="white-space:pre-wrap;">' +
        escapeHtml(fallback) + '</p></section>'
      : '<section class="research-panel"><h3>研究分析</h3><p class="section-caption">本次暂无可展示的详细分析。</p></section>';
  }
  let html = '<section class="research-panel research-narrative">' +
    '<h3>多维度分析</h3>' +
    '<p class="section-caption">从公司经营、股价、资金和行业环境等方面逐项判断。</p>';
  order.forEach(([key, label], idx) => {
    const detail = String(analyses[key] || '').trim();
    html += '<article class="analysis-entry">' +
      '<h4><span class="analysis-index">' + String(idx + 1).padStart(2, '0') +
      '</span>' + escapeHtml(label) + '</h4>' +
      '<div class="analysis-prose">' +
      (detail ? escapeHtml(detail) : '<span class="section-caption">该项分析暂缺</span>') +
      '</div></article>';
  });
  return html + '</section>';
}

// The industry engine, not the old perpetual-ROE dividend toy, owns the
// investable-valuation panel. Missing verified projections => NO target price.
const INDUSTRY_MODEL_LABELS = {
  owner_fcfe:'股权自由现金流 FCFE',
  midcycle_fcfe:'跨周期中枢 FCFE',
  regulated_dividend_discount:'受监管现金分红折现',
  bank_residual_income:'银行残余收益 RIM',
  financial_residual_income:'非银金融残余收益 RIM',
  adjusted_nav:'调整后净资产 NAV',
  insurance_embedded_value:'保险精算内含价值（待审核）',
};
function industryModelName(id) {return INDUSTRY_MODEL_LABELS[id] || id || '尚未识别';}
function industryInputLabel(id) {
  const labels={
    annual_cash_flow_per_share:'未来逐年股权自由现金流（元/股）',
    terminal_cash_flow_per_share:'下一年可持续 FCFE（元/股）',
    annual_dividends_per_share:'未来逐年可分配现金股息（元/股）',
    terminal_dividend_per_share:'下一年可持续股息（元/股）',
    opening_book_per_share:'可用普通股每股净资产',
    annual_roe_pct:'未来逐年ROE情景',
    annual_payout_pct:'未来逐年现金分红比例',
    required_return_pct:'投资者要求收益率',
    terminal_growth_pct:'长期增长上限',
    fair_assets_per_share:'资产公允价值/股',
    total_obligations_per_share:'全部债务及其他义务/股',
    realization_tax_and_cost_per_share:'变现税费/股',
    cycle_span_years:'覆盖完整周期的年数',
    maintenance_capex_basis:'维持性资本开支来源',
    dividend_coverage_by_fcfe:'现金股息FCFE覆盖率',
    capital_adequacy_verified:'监管资本充足率已核验',
  };
  return labels[id] || id;
}
function industryModelCompute(route, src, hurdlePct, growthPct, stressPct, policyPct) {
  // Mirrors analysis/valuation_models.py; stress is explicitly a hypothetical
  // earnings/cash-flow or ROE shock, not a presumed currency or Treasury beta.
  const type=route.primary, inp=src || {};
  const k=(hurdlePct+policyPct)/100;
  if(!Number.isFinite(k)||k<.04||k>.35) return null;
  let value=0, components={},terminalShare=null;
  if(['owner_fcfe','midcycle_fcfe','regulated_dividend_discount'].includes(type)) {
    const flows=inp.annual_cash_flow_per_share || inp.annual_dividends_per_share;
    const terminal=Number(inp.terminal_cash_flow_per_share ?? inp.terminal_dividend_per_share);
    if(!Array.isArray(flows)||flows.length<3||flows.length>15||!Number.isFinite(terminal)||terminal<=0) return null;
    const g=growthPct/100;
    if(g>.04||k-g<.04-1e-12) return null;
    const multiplier=1+stressPct/100;
    if(multiplier<=0) return null;
    const annual=flows.map(Number);
    if(annual.some(x=>!Number.isFinite(x))) return null;
    const pv=annual.reduce((sum,cf,i)=>sum+cf*multiplier/Math.pow(1+k,i+1),0);
    const tail=terminal*multiplier/(k-g)/Math.pow(1+k,annual.length);
    value=pv+tail;
    terminalShare=value>0?100*tail/value:null;
    components={pvExplicit:pv,pvTerminal:tail};
  } else if(['bank_residual_income','financial_residual_income'].includes(type)) {
    let book=Number(inp.opening_book_per_share);
    const roes=inp.annual_roe_pct, payouts=inp.annual_payout_pct;
    if(!Number.isFinite(book)||book<=0||!Array.isArray(roes)||!Array.isArray(payouts)||
       roes.length!==payouts.length||roes.length<3||roes.length>15) return null;
    let pv=0;
    for(let i=0;i<roes.length;i++) {
      const roe=(Number(roes[i])+stressPct)/100, p=Number(payouts[i])/100;
      if(!Number.isFinite(roe)||!Number.isFinite(p)||p<0||p>1)return null;
      pv+=(roe-k)*book/Math.pow(1+k,i+1);
      book+=book*roe*(1-p);
      if(book<=0)return null;
    }
    value=Number(inp.opening_book_per_share)+pv;
    components={pvResidual:pv,endingBook:book};
  } else if(type==='adjusted_nav') {
    // For NAV, stress means haircut on asset fair value; style premium is
    // not a mathematical component of liquidation NAV.
    if(policyPct!==0)return null;
    value=Number(inp.fair_assets_per_share)*(1+stressPct/100)-
      Number(inp.total_obligations_per_share)-Number(inp.realization_tax_and_cost_per_share);
  } else return null;
  if(!Number.isFinite(value)||value<=0)return null;
  return {price:value,terminalShare,components};
}
function renderIndustryValuationLab(report) {
  const model=report.valuation_model || {};
  const route=model.route || {};
  const calc=model.valuation || {};
  const macro=model.macro_context || {};
  const primary=route.primary || '';
  let html='<section class="valuation-lab" id="industryValuationLab">'+
    '<span class="model-tag">按行业估值 · 市场环境</span>'+
    '<h3 style="margin-top:10px;">'+escapeHtml(industryModelName(primary))+' · 估值</h3>'+
    '<p class="section-caption">不同公司采用不同估值方式；汇率、利率和A股风格会影响股价，但不能直接当作公司盈利。</p>';
  if(!route.primary) {
    html+='<div class="chart-empty">旧版报告尚未包含行业估值结果。请用更新后的Agent重新分析。</div>';
  } else if(calc.status!=='calculated'||!model.interactive_inputs) {
    const missing=(model.evidence_gate || {}).missing || calc.missing || [];
    const invalid=(model.evidence_gate || {}).invalid || [];
    html+='<div class="evidence-block missing"><b>当前不输出目标价</b>'+
      '<p>缺少计算合理价格所需的未来现金流、资本或资产数据，暂时无法给出可靠估值。</p>'+
      '<p><b>需要补充：</b>'+escapeHtml(missing.length?missing.map(industryInputLabel).join('、'):'公司相关数据')+'</p>'+
      (invalid.length?'<p><b>输入错误：</b>'+escapeHtml(invalid.join('；'))+'</p>':'')+
      '<p class="section-caption">必要的数据尚不充分，不用单一年份ROE或通用倍数强行推导目标价。</p></div>';
  } else {
    const initial=Number(model.interactive_inputs.required_return_pct || 10);
    const initialG=Number(model.interactive_inputs.terminal_growth_pct || 0);
    const hasDiscount=primary!=='adjusted_nav';
    const hasGrowth=['owner_fcfe','midcycle_fcfe','regulated_dividend_discount'].includes(primary);
    const stressLabel=['bank_residual_income','financial_residual_income'].includes(primary)?
      '各年ROE压力调整（百分点）' : primary==='adjusted_nav'?
      '资产公允价值压力情景' : '现金流压力情景（全期比例）';
    const slider=(key,label,min,max,step,val,unit)=>'<div class="slider-row"><div class="slider-top">'+
      '<label for="sector-'+key+'">'+escapeHtml(label)+'</label><output id="sector-'+key+'-value">'+
      Number(val).toFixed(2)+unit+'</output></div>'+
      '<input type="range" id="sector-'+key+'" min="'+min+'" max="'+max+'" step="'+step+
      '" value="'+val+'"></div>';
    html+='<div class="lab-layout"><div>'+
      (hasDiscount?slider('discount','人民币股权要求收益率',4,35,.25,initial,'%'):'')+
      (hasGrowth?slider('growth','下一年可持续现金流增长率',-3,4,.25,initialG,'%'):'')+
      slider('stress',stressLabel,primary==='adjusted_nav'?-50:primary.includes('residual_income')?-5:-40,
        primary==='adjusted_nav'?20:primary.includes('residual_income')?5:40,
        primary.includes('residual_income')?.25:5,0,primary.includes('residual_income')?'pp':'%')+
      (hasDiscount?slider('premium','A股风格/流动性风险溢价压力',-2,4,.25,0,'pp'):'')+
      slider('safety','额外安全边际折价',0,50,5,20,'%')+
      '<div class="lab-actions"><button type="button" id="sector-reset">恢复已核验的初始假设</button></div>'+
      '<p class="section-caption">所有压力变化都是人为情景：不代表预测到美债/汇率实际影响。行业模型需要经过审核的现金流、资产质量或监管资本数据。</p>'+
      '</div><div class="lab-result" aria-live="polite"><h4>模型条件下的股权价值 · 非买卖指令</h4>'+
      '<div class="lab-price" id="sector-price">—</div>'+
      '<div class="lab-stat-grid"><div><span>安全边际后的情景价</span><b id="sector-buy">—</b></div>'+
      '<div><span>终值依赖度</span><b id="sector-terminal">—</b></div></div>'+
      '<div class="lab-foot" id="sector-note">计算中</div>'+
      '<div id="sector-sensitivity"></div></div></div>';
  }
  const independent=model.cross_checks || [];
  if(independent.length) {
    html+='<div class="evidence-block" style="margin-top:16px;"><b>独立模型交叉检验</b><ul>'+
      independent.map(x=>'<li>'+escapeHtml(industryModelName(x.model))+'：'+
        (x.status==='calculated'?
          escapeHtml(String((x.valuation || {}).intrinsic_per_share))+'元/股（独立情景）'+
          (x.disagreement_pct_of_primary===undefined?'':'；与主模型差'+
            escapeHtml(String(x.disagreement_pct_of_primary))+'%'):
          '尚无法定价（'+escapeHtml(x.reason || x.status || '数据缺失')+'）')+
        (x.warning?'；'+escapeHtml(x.warning):'')+'</li>').join('')+
      '</ul><p class="section-caption">不同模型结果不能机械取均值；每套模型需要各自独立的来源和适用性检验。</p></div>';
  }
  const stresses=model.macro_stress_scenarios || [];
  if(stresses.length) {
    html+='<div class="evidence-block" style="margin-top:14px;"><b>带公司敏感度来源的宏观压力测试</b><ul>'+
      stresses.map(x=>'<li>'+(
        x.status==='calculated'?
          '情景股权价值 '+escapeHtml(String((x.result || {}).valuation?.intrinsic_per_share ||
            (x.result || {}).intrinsic_per_share || '暂缺'))+'元/股'+
          '；来源 '+escapeHtml(x.source_url || '未注明')+
          '；系数日期 '+escapeHtml(x.source_as_of || '未知'):
          '未能量化：'+escapeHtml(x.reason || x.status)
      )+'</li>').join('')+
      '</ul></div>';
  }
  const observed=(macro.observations || []).filter(x=>x.usable && x.value!==null);
  html+='<div class="chart-grid" style="margin-top:16px;">'+
    chartTile('宏观观察值（含数据时点）','仅显示有明确日期、未过期的指标',
      observed.length?'<div class="evidence-block">'+observed.map(x=>
        '<div><b>'+escapeHtml(x.metric)+':</b> '+escapeHtml(String(x.value))+
        ' <small>('+escapeHtml(x.as_of)+')</small></div>').join('')+'</div>':'')+
    chartTile('外围与国内市场传导','不自动根据风格叙事调整合理股价',
      '<div class="evidence-block">'+
      '美元兑人民币：'+escapeHtml(macro.rmb_direction || '资料不足')+
      '；海外营收占比：'+escapeHtml(macro.overseas_revenue_pct===null||macro.overseas_revenue_pct===undefined?
        '资料不足':String(macro.overseas_revenue_pct)+'%')+
      '<p>未审计套保/美元债及汇率敏感度前，禁止给出固定估值调整。</p></div>')+'</div>';
  const style=macro.a_share_style || {};
  if(style.status==='observed') {
    const styleLabel={
      dividend_leading:'红利风格相对领先',
      growth_leading:'科技成长相对领先',mixed:'红利与成长差距有限',
    }[style.regime] || '风格无法确定';
    html+='<p class="section-caption" style="margin-top:12px;">A股观察：'+
      escapeHtml(styleLabel)+'；上证红利相对科创50/创业板指年内差'+
      escapeHtml(String(style.dividend_minus_growth_ytd_pp))+'个百分点（'+
      escapeHtml(style.as_of || '未知日期')+
      '）。这是风格相对收益，不直接改变内在价值。</p>';
  }
  const routes=(macro.transmission_paths || []);
  html+='<details class="evidence-block" style="margin-top:16px;"><summary>查阅中国政策、A股风格与外围市场风险传导依据</summary>'+
    '<ul>'+routes.map(x=>'<li><b>'+escapeHtml(x.factor)+':</b> '+
      escapeHtml(x.route)+'；'+escapeHtml(x.evidence)+'</li>').join('')+'</ul></details>';
  return html+'</section>';
}
function activateIndustryValuationLab(report) {
  const model=report.valuation_model || {};
  const route=model.route || {}, inputs=model.interactive_inputs;
  if(!inputs || (model.valuation || {}).status!=='calculated')return;
  const primary=route.primary;
  const names=primary==='adjusted_nav'?['stress','safety']:
    ['discount',...(['owner_fcfe','midcycle_fcfe','regulated_dividend_discount'].includes(primary)?['growth']:[]),
      'stress','premium','safety'];
  const get=id=>document.getElementById(id);
  const sliders=Object.fromEntries(names.map(key=>[key,get('sector-'+key)]));
  const initial=Object.fromEntries(names.map(key=>[key,Number(sliders[key].value)]));
  function recalculate() {
    const props=Object.fromEntries(names.map(key=>[key,Number(sliders[key].value)]));
    names.forEach(key=>{
      get('sector-'+key+'-value').textContent=props[key].toFixed(2)+
        ((key==='premium'||(key==='stress'&&primary.includes('residual_income')))?'pp':'%');
    });
    const k=props.discount===undefined?10:props.discount;
    const growth=props.growth===undefined?0:props.growth;
    const penalty=props.premium||0;
    const result=industryModelCompute(route,inputs,k,growth,props.stress,penalty);
    get('sector-price').textContent=result?result.price.toFixed(2)+'元/股':'情景不适用';
    get('sector-buy').textContent=result?(result.price*(1-props.safety/100)).toFixed(2)+'元':'暂缺';
    get('sector-terminal').textContent=result&&result.terminalShare!==null?
      result.terminalShare.toFixed(1)+'%':'不适用';
    const msgs=['参数为情景分析，不是市场真实预期。'];
    if(result && result.terminalShare!==null && result.terminalShare>70)
      msgs.push('终值超过70%，远期预测主导价值。');
    if(!result)msgs.push('折现率/终值距离或资本约束不满足；拒绝生成情景价。');
    if(penalty!==0)msgs.push('A股风格风险溢价为手动输入，并非已校准的市场β。');
    get('sector-note').textContent=msgs.join(' ');
    if(!result){get('sector-sensitivity').innerHTML='';return;}
    const variations=primary==='adjusted_nav'?[-20,-10,0,10,20]:
      [-2,-1,0,1,2];
    let bars='<svg viewBox="0 0 420 176" width="100%" role="img" aria-label="估值压力敏感性图">';
    const series=variations.map(shift=>{
      const v=primary==='adjusted_nav'?
        industryModelCompute(route,inputs,k,growth,shift,0):
        industryModelCompute(route,inputs,k+shift,growth,props.stress,penalty);
      return v?.price??null;
    });
    const scale=Math.max(1,...series.filter(x=>x!==null));
    series.forEach((val,i)=>{
      const x=18+i*82,h=val===null?0:Math.max(2,90*val/scale);
      bars+='<rect x="'+x+'" y="'+(129-h).toFixed(1)+'" width="60" height="'+h.toFixed(1)+
        '" rx="4" fill="'+(i===2?'#91c8ff':'#7787ce')+'"/>'+
        '<text x="'+(x+30)+'" y="'+(121-h).toFixed(1)+'" text-anchor="middle" font-size="10" fill="white">'+
        (val===null?'不适用':val.toFixed(1))+'</text>'+
        '<text x="'+(x+30)+'" y="150" text-anchor="middle" font-size="10" fill="#dde6ff">'+
        variations[i]+(primary==='adjusted_nav'?'%':'pp')+'</text>';
    });
    get('sector-sensitivity').innerHTML=bars+'</svg>';
  }
  names.forEach(key=>sliders[key].addEventListener('input',recalculate));
  get('sector-reset').addEventListener('click',()=>{
    names.forEach(key=>sliders[key].value=initial[key]);
    recalculate();
  });
  recalculate();
}

function renderResult(report) {
  const el = document.getElementById('result');
  let html = '<h2>' + escapeHtml(report.stock_name || report.stock_code) + ' (' + escapeHtml(report.stock_code) + ')</h2>';
  const isHistorical = report.research_mode === 'historical';
  if (isHistorical) {
    html += '<div class="historical-banner"><b>历史研究</b> ｜ 截止日 ' +
      escapeHtml(report.as_of || '未知') +
      ' ｜ 结论只允许使用该日及之前已公开的信息；当前知识不会用于补全历史缺口。</div>';
  } else {
    html += '<div class="live-banner"><b>Live 研究</b> ｜ 基准日 ' +
      escapeHtml(report.as_of || '今天') + '</div>';
  }

  if (report.realtime_quote) {
    const q = report.realtime_quote;
    html += '<p class="quote">' + (isHistorical ? '截止日收盘/最近交易价 ' : '最新价 ') +
      q.latest_price + ' 涨跌幅 ' + q.change_pct + '% 成交量 ' + q.volume + '</p>';
  }

  const summary = report.summary || {};
  const stanceClass = 'stance-' + (summary.stance || 'neutral');
  html += '<section class="result-summary"><h3>投资结论</h3>';
  html += '<div class="verdict-line"><span class="stance-badge ' + stanceClass + '">' +
    stanceLabel(summary.stance) + '</span>';
  if (summary.company_quality_stance) {
    html += '<span class="verdict-item">公司质量 <b>' +
      stanceLabel(summary.company_quality_stance) + '</b></span>';
  }
  if (summary.current_odds_stance) {
    html += '<span class="verdict-item">当前价格 <b>' +
      stanceLabel(summary.current_odds_stance) + '</b></span>';
  }
  if (summary.lynch_category && lynchCategoryLabel(summary.lynch_category)) {
    html += '<span class="lynch-category">' +
      escapeHtml(lynchCategoryLabel(summary.lynch_category)) + '</span>';
  }
  const profile = report.research_profile || {};
  if (profile.industry) {
    html += '<span class="verdict-industry">' + escapeHtml(profile.industry) + '</span>';
  }
  html += '</div>';
  const conciseThesis=String(summary.thesis_summary || '').trim();
  const hasDetailedAnalysis=Object.keys(summary.dimension_analyses || {}).length >= 6;
  // Historical cached reports sometimes put the entire 12-dimension essay in
  // thesis_summary. Do not repeat it before the actual 12 perspectives.
  if (conciseThesis && (!hasDetailedAnalysis || conciseThesis.length <= 400)) {
    html += '<p class="verdict-summary">' + escapeHtml(conciseThesis) + '</p>';
  }
  html += '</section>';

  html += renderDashboardCharts(report);
  html += renderDimensionAnalysisSection(summary);
  html += renderIndustryValuationLab(report);

  el.innerHTML = html;
  activateIndustryValuationLab(report);
  attachRadarTooltips();
}

document.getElementById('submitBtn').addEventListener('click', submitAnalysis);
document.getElementById('crawlXueqiuBtn').addEventListener('click', () => submitCrawl('xueqiu'));
document.getElementById('crawlBiliBtn').addEventListener('click', () => submitCrawl('bili'));
document.getElementById('backtestBtn').addEventListener('click', () => submitCrawl('backtest'));
document.getElementById('digestBtn').addEventListener('click', () => submitCrawl('digest'));
document.getElementById('digestUserBtn').addEventListener('click', () => submitCrawl('digest_user'));
document.getElementById('crawlOnlyBtn').addEventListener('click', () => submitCrawl('crawl_only'));
renderWatchlist();
document.getElementById('researchMode').addEventListener('change', syncResearchModeControls);
syncResearchModeControls();
loadTemporalCapabilities();
loadCrawledUsers();

// 聚焦时全选: 输入框里残留上次的选择文本时, datalist 会按它过滤导致
// 下拉只剩一个用户; 全选后直接输入即可看到全部选项。
document.getElementById('xueqiuUserId').addEventListener('focus', function () {
  this.select();
});

let crawlPollTimer = null;

// 下拉自动补全: 选项值是 "昵称 (ID)", 提交时提取括号里的数字 ID;
// 用户直接输入 ID/URL 时原样使用。
function resolveUserId(value) {
  const m = value.match(/\((\d+)\)\s*$/);
  return m ? m[1] : value;
}

async function loadCrawledUsers() {
  try {
    const res = await fetch('/api/crawled/users');
    if (!res.ok) return;
    const users = await res.json();
    const dl = document.getElementById('crawledUserList');
    dl.innerHTML = users.map(u => {
      const name = u.user_nickname || u.user_id;
      return '<option value="' + escapeHtml(name) + ' (' + escapeHtml(u.user_id) + ')"' +
        ' label="' + escapeHtml(name) + ' — 帖子 ' + u.post_count + ' 条, 粉丝 ' + u.followers_count + '">' +
        '</option>';
    }).join('');
  } catch (e) {
    // 用户列表加载失败不影响页面其余功能
  }
}

async function submitCrawl(platform) {
  const statusEl = document.getElementById('crawlStatus');
  const logEl = document.getElementById('crawlLog');
  let url, body;

  if (platform === 'xueqiu') {
    const userId = resolveUserId(document.getElementById('xueqiuUserId').value.trim());
    if (!userId) return;
    url = '/api/crawl/xueqiu';
    body = {
      user_id: userId,
      incremental: document.getElementById('xueqiuIncremental').checked,
      auto_backtest: true,
    };
  } else if (platform === 'crawl_only') {
    const userId = resolveUserId(document.getElementById('xueqiuUserId').value.trim());
    if (!userId) return;
    url = '/api/crawl/xueqiu';
    body = {
      user_id: userId,
      incremental: document.getElementById('xueqiuIncremental').checked,
      auto_backtest: false,
    };
  } else if (platform === 'backtest') {
    const userId = resolveUserId(document.getElementById('xueqiuUserId').value.trim());
    if (!userId) return;
    url = '/api/backtest/xueqiu';
    body = { user_id: userId };
  } else if (platform === 'digest') {
    url = '/api/digest/rebuild';
    body = {};
  } else if (platform === 'digest_user') {
    const userId = resolveUserId(document.getElementById('xueqiuUserId').value.trim());
    if (!userId) return;
    url = '/api/digest/rebuild';
    body = { user_id: userId };
  } else {
    const creatorId = document.getElementById('biliCreatorId').value.trim();
    if (!creatorId) return;
    url = '/api/crawl/bili_knowledge';
    body = { creator_id: creatorId };
  }

  if (crawlPollTimer) clearInterval(crawlPollTimer);
  statusEl.textContent = '提交中...';
  logEl.style.display = 'none';

  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    statusEl.textContent = '提交失败: ' + (err.detail || res.status);
    return;
  }
  const { task_id } = await res.json();
  statusEl.textContent = '任务已提交, 状态: pending';
  logEl.style.display = 'block';
  // 清空输入框: 残留的选择文本会让 datalist 只过滤出该用户
  document.getElementById('xueqiuUserId').value = '';
  crawlPollTimer = setInterval(() => pollCrawlTask(task_id), 2000);
}

async function pollCrawlTask(taskId) {
  const res = await fetch('/api/crawl/tasks/' + taskId);
  if (!res.ok) return;
  const data = await res.json();
  const statusEl = document.getElementById('crawlStatus');
  const logEl = document.getElementById('crawlLog');

  statusEl.textContent = '状态: ' + data.status;
  logEl.textContent = data.log_tail || '';
  logEl.scrollTop = logEl.scrollHeight;
  document.getElementById('copyLogBtn').style.display = (data.log_tail || '') ? 'inline-block' : 'none';

  if (data.status === 'done' || data.status === 'failed') {
    clearInterval(crawlPollTimer);
    loadCrawledUsers();  // 抓取/回测可能新增用户, 刷新下拉列表
  }
}

document.getElementById('copyLogBtn').addEventListener('click', async () => {
  const logEl = document.getElementById('crawlLog');
  const btn = document.getElementById('copyLogBtn');
  const text = logEl.textContent || '';
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    // clipboard API 失败时回退: 选中日志文本 + execCommand
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(logEl);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('copy');
    sel.removeAllRanges();
  }
  btn.textContent = '已复制';
  setTimeout(() => { btn.textContent = '复制全部日志'; }, 1500);
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
