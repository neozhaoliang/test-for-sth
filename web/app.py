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


class AnalyzeResponse(BaseModel):
    task_id: str


async def _run_analysis(task_id: str, stock_code: str) -> None:
    _tasks[task_id]["status"] = "running"
    try:
        report = await generate_report(stock_code)
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

    task_id = str(uuid.uuid4())
    _tasks[task_id] = {"status": "pending", "result": None, "error": None}
    asyncio.create_task(_run_analysis(task_id, stock_code))
    return AnalyzeResponse(task_id=task_id)


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
  .search-bar { display: flex; gap: 8px; margin: 20px 0; }
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
</style>
</head>
<body>
<header>
  <h1>股票投资助手</h1>
  <p>输入股票代码，查看历史高可信度用户的观点与最新发言综合分析。</p>
</header>
<div class="search-bar">
  <input id="stockCode" placeholder="股票代码或名称，如 SH603408 / 洛阳钼业" />
  <button id="submitBtn">分析</button>
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
  submitting = true;
  document.getElementById('result').innerHTML = '';
  document.getElementById('status').textContent = '提交中...';
  if (pollTimer) clearInterval(pollTimer);

  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stock_code: stockCode }),
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
    return '<li><b>' + label + ':</b> 分红覆盖 ' + row.dividend_years_count + '/' + row.window_years +
      ' 个日历年，累计每10股现金分红 ' + row.cash_dividend_per_10_total + ' 元；' +
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
    html += '<div class="evidence-block"><b>股东户数 (散户情绪代理):</b> 最新 ' + st.latest_count + ' 户 (截止 ' +
      escapeHtml(st.as_of) + ')，环比变化 ' + st.change_pct + '%，筹码趋于' + (st.trend === 'increasing' ? '分散' : '集中') + '</div>';
  } else {
    html += '<div class="evidence-block missing"><b>股东户数:</b> 暂缺</div>';
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
        }).join('') + '</ul></details>';
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
    html += '<div class="evidence-block"><b>分红与回购 (回购折算元/10股并入; 柱顶标注按现价股息率):</b><br>' +
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
    html += '<div class="evidence-block"><b>大宗商品价差/汇率 (周期性矿业股):</b> 沪铜 ' + cs.sh_copper_price + ' ' + escapeHtml(cs.sh_copper_unit) +
      '，COMEX铜 ' + cs.comex_copper_price + ' ' + escapeHtml(cs.comex_copper_unit) +
      '，人民币汇率趋势: ' + escapeHtml(cs.rmb_trend || '暂缺') +
      (cs.note ? '<br>' + escapeHtml(cs.note) : '') + '</div>';
  } else {
    html += '<div class="evidence-block missing"><b>大宗商品价差/汇率:</b> 暂缺 (非周期性矿业股，或行业归属数据未能取到)</div>';
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
// 柱顶标注按现价折算的股息率; 悬停显示数值, 图下附原始数据表。
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
    const yBuy = yOf(d.buyback_per_10);
    bars += '<rect class="holder-mark" x="' + (xc - barW / 2).toFixed(1) + '" y="' + yDiv.toFixed(1) +
      '" width="' + barW.toFixed(1) + '" height="' + Math.max(0.5, padT + plotH - yDiv).toFixed(1) +
      '" fill="#1a73e8" fill-opacity="0.85" data-tip="' + escapeHtml(d.year) +
      ': 分红 ' + d.dividend_per_10 + ' 元/10股"/>';
    if (d.buyback_per_10 > 0) {
      bars += '<rect class="holder-mark" x="' + (xc - barW / 2).toFixed(1) + '" y="' + yBuy.toFixed(1) +
        '" width="' + barW.toFixed(1) + '" height="' + Math.max(0.5, yDiv - yBuy).toFixed(1) +
        '" fill="#d97706" stroke="#fff" stroke-width="2" data-tip="' + escapeHtml(d.year) +
        ': 回购折算 ' + d.buyback_per_10 + ' 元/10股"/>';
    }
    const yTotal = yOf(d.total_per_10);
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
    '<td style="padding:2px 6px;color:#666;">股息率(按现价)</td>' +
    data.map(d => '<td style="padding:2px 8px;border-left:1px solid #eee;text-align:center;">' +
      (d.yield_pct === null || d.yield_pct === undefined ? '--' : d.yield_pct + '%') + '</td>').join('') +
    '</tr></table>';

  return '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" style="max-width:100%;"' +
    ' role="img" aria-label="年度分红与回购柱状图">' +
    grid + bars + labels + xLabels + legend + '</svg>' + table;
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

function renderResult(report) {
  const el = document.getElementById('result');
  let html = '<h2>' + escapeHtml(report.stock_name || report.stock_code) + ' (' + escapeHtml(report.stock_code) + ')</h2>';

  if (report.realtime_quote) {
    const q = report.realtime_quote;
    html += '<p class="quote">最新价 ' + q.latest_price + ' 涨跌幅 ' + q.change_pct + '% 成交量 ' + q.volume + '</p>';
  }

  const summary = report.summary || {};
  const stanceClass = 'stance-' + (summary.stance || 'neutral');
  html += '<h3>综合分析</h3>';
  html += '<p><span class="stance-badge ' + stanceClass + '">' + stanceLabel(summary.stance) + '</span>';
  if (summary.company_quality_stance || summary.current_odds_stance) {
    html += '<span style="margin-left:12px;">企业长期质量: <b>' +
      stanceLabel(summary.company_quality_stance) + '</b> ｜ 当前股票赔率: <b>' +
      stanceLabel(summary.current_odds_stance) + '</b></span>';
  }
  if (summary.confidence !== undefined && summary.confidence !== null) {
    html += '<span style="margin-left:12px;">置信度 ' + Math.round(summary.confidence * 100) + '%</span>';
  }
  if (summary.lynch_category && lynchCategoryLabel(summary.lynch_category)) {
    html += '<span class="lynch-category">' + escapeHtml(lynchCategoryLabel(summary.lynch_category)) + '</span>';
  }
  if (report.prompt_version) {
    html += ' <span class="prompt-version">(prompt ' + escapeHtml(report.prompt_version) + ')</span>';
  }
  html += '</p>';
  const rq = report.research_quality || {};
  if (rq.total_dimensions) {
    html += '<div class="evidence-block"><b>证据质量:</b> 覆盖 ' +
      Math.round((rq.coverage || 0) * 100) + '% (' +
      (rq.covered_dimensions || 0) + '/' + rq.total_dimensions + ' 个研究方向)，' +
      '非社交事实/推导来源占比 ' + Math.round((rq.high_grade_ratio || 0) * 100) + '%';
    if (rq.missing_dimensions && rq.missing_dimensions.length) {
      html += '<br><b>尚缺:</b> ' + rq.missing_dimensions.map(escapeHtml).join('、');
    }
    if (rq.warnings && rq.warnings.length) {
      html += '<br><b>质量提示:</b><br>' + rq.warnings.map(x => '· ' + escapeHtml(x)).join('<br>');
    }
    html += '</div>';
  }

  const review = report.review || {};
  const dup = review.duplicate_factors || [];
  const conflicts = review.possible_conflicts || [];
  const weak = review.weak_links || [];
  if (dup.length || conflicts.length || weak.length) {
    html += '<details class="evidence-block"><summary style="cursor:pointer;"><b>研究审查</b>：' +
      '重复因子 ' + dup.length + '，潜在冲突 ' + conflicts.length +
      '，弱证据 ' + weak.length +
      (review.confidence_penalty ? '，置信度扣减 ' + Math.round(review.confidence_penalty * 100) + ' 个百分点' : '') +
      '</summary>';
    const groups = [
      ['重复计分提示', dup],
      ['潜在冲突', conflicts],
      ['弱证据链', weak],
    ];
    groups.forEach(([title, rows]) => {
      if (!rows.length) return;
      html += '<div style="margin-top:8px;"><b>' + title + '</b><ul>' +
        rows.map(x => '<li>' + escapeHtml(x.message || '') +
          (x.dimensions && x.dimensions.length ? ' <span class="credibility-note">[' +
            x.dimensions.map(escapeHtml).join(' / ') + ']</span>' : '') +
          '</li>').join('') + '</ul></div>';
    });
    html += '</details>';
  }

  if (summary.thesis_summary) {
    html += '<div class="thesis-block"><b>关键论据:</b> ' + escapeHtml(summary.thesis_summary) + '</div>';
  }
  if (summary.core_counter_evidence) {
    html += '<div class="counter-evidence-block"><b>与结论相悖的最强证据:</b> ' +
      escapeHtml(summary.core_counter_evidence) + '</div>';
  }
  if (summary.invalidation_condition) {
    html += '<div class="invalidation-block"><b>如果这个判断错了，会是因为:</b> ' + escapeHtml(summary.invalidation_condition) + '</div>';
  }
  if (summary.risk_notes) {
    html += '<div class="risk-block"><b>风险提示:</b> ' + escapeHtml(summary.risk_notes) + '</div>';
  }

  if (summary.dimension_analyses && Object.keys(summary.dimension_analyses).length >= 6) {
    const dimLabels = {
      management: '管理层', fundamentals: '基本面', rd: '研发能力', chip_flow: '筹码',
      price_position: '股价位置', cycle_position: '周期', policy_geopolitics: '政策形势',
      retail_sentiment: '散户情绪', shareholder_returns: '股东回报', growth_elasticity: '成长弹性',
      a_share_structure: 'A股资金结构', risk_quality: '财务质量与尾部风险',
    };
    html += '<details class="evidence-block"><summary style="cursor:pointer;">十二维度详细分析 (展开)</summary>';
    Object.keys(summary.dimension_analyses).forEach(k => {
      html += '<div style="margin:8px 0;white-space:pre-wrap;"><b>' +
        escapeHtml(dimLabels[k] || k) + ':</b> ' +
        escapeHtml(summary.dimension_analyses[k]) + '</div>';
    });
    html += '</details>';
  }

  if (summary.dimension_scores && summary.dimension_scores.length >= 6) {
    html += '<div class="evidence-block"><b>十二维度倾向 (-10 利空 ~ +10 利多):</b><br>' +
      renderDimensionBars(summary.dimension_scores) +
      '<details style="margin-top:8px;"><summary style="cursor:pointer;">查看雷达图</summary>' +
      renderDimensionRadar(summary.dimension_scores) + '</details></div>';
  }

  html += renderEvidenceSection(report);

  if (report.evidence && report.evidence.length) {
    html += '<details class="evidence-block"><summary style="cursor:pointer;">证据账本 (' +
      report.evidence.length + ' 条，展开)</summary><table style="width:100%;margin-top:8px;border-collapse:collapse;">' +
      '<tr><th style="text-align:left">证据</th><th>等级</th><th>类型</th><th style="text-align:left">来源</th></tr>';
    report.evidence.forEach(e => {
      html += '<tr><td>' + escapeHtml(e.label || e.category) + '</td><td style="text-align:center">' +
        escapeHtml(e.source_tier || '') + '</td><td style="text-align:center">' +
        escapeHtml(e.kind || '') + '</td><td>' +
        (e.url ? safeExternalLink(e.url, e.source || '来源') : escapeHtml(e.source || '')) +
        '</td></tr>';
    });
    html += '</table></details>';
  }

  html += '<h3>候选用户 (' + report.candidates.length + ')</h3>';
  for (const c of report.candidates) {
    html += '<div class="candidate">';
    html += '<b>' + escapeHtml(c.user_nickname) + '</b> <span class="credibility-note">(' + escapeHtml(c.credibility_note) + ', Wilson分 ' + c.wilson_score.toFixed(3) + ')</span>';
    if (c.historical_thesis && c.historical_thesis.length) {
      html += '<p>历史观点: ' + c.historical_thesis.map(escapeHtml).join(' | ') + '</p>';
    }
    if (c.latest_posts && c.latest_posts.length) {
      html += '<p>最新发言: ' + c.latest_posts.map(escapeHtml).join(' | ') + '</p>';
    } else {
      html += '<p>(未能获取到最新发言)</p>';
    }
    html += '</div>';
  }

  el.innerHTML = html;
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
