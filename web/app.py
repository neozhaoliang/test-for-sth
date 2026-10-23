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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from analysis.knowledge_base import (
    ensure_loaded as ensure_knowledge_base_loaded,
    invalidate_cache as invalidate_knowledge_base_cache,
)
from analysis.realtime_price import resolve_stock_code
from analysis.stock_suggest import search_stocks, warm_stock_catalog
from analysis.report import generate_report
from analysis.etf_data import resolve_etf, search_etfs, warm_etf_catalog, collect_etf_overview
from analysis.etf_report import generate_etf_report
from analysis.public_report import public_stock_report, public_etf_report
from web.intranet_access import IntranetAccessMiddleware, load_access_settings
from analysis.json_safety import finite_json_numbers, nonfinite_number_paths
from analysis.research_context import (
    ResearchRequest,
    assert_request_supported,
    historical_readiness,
    SOURCE_TEMPORAL_CAPABILITIES,
)
def normalize_user_id(raw: str) -> str:
    # Keep optional crawler/proxy/Redis imports out of stock-analysis GETs.
    # The crawler-specific package has import-time cache initialization.
    from media_platform.xueqiu.help import normalize_user_id as normalize
    return normalize(raw)

from tools.utils import utils

app = FastAPI(title="股票投资助手")
# OFF by default. With INTRANET_ACCESS=1 this protects every UI, asset and
# /api/* request (including crawler, backtest and paid research endpoints).
app.add_middleware(IntranetAccessMiddleware)

_tasks: Dict[str, Dict] = {}

_PROJECT_ROOT = Path(__file__).parent.parent
_CRAWL_LOG_TAIL_LINES = 200

_crawl_tasks: Dict[str, Dict] = {}
_crawl_lock = asyncio.Lock()


@app.on_event("startup")
async def _validate_intranet_guard() -> None:
    # Reject invalid settings before the server accepts LAN connections.
    load_access_settings()


@app.on_event("startup")
async def _preload_knowledge_base() -> None:
    # 后台提炼 (含雪球用户发帖来源, 首次要逐条过 LLM, 会持续一段时间);
    # 不阻塞服务启动, 提炼完成的条目逐批进入缓存
    asyncio.create_task(ensure_knowledge_base_loaded())
    asyncio.create_task(warm_stock_catalog())
    asyncio.create_task(warm_etf_catalog())


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
    asset_type: str = "stock",
) -> None:
    _tasks[task_id]["status"] = "running"
    try:
        if asset_type == "etf":
            spot = await resolve_etf(stock_code)
            if spot is None:
                raise RuntimeError("未能在独立基金名录中确认ETF代码或名称")
            raw_report = await generate_etf_report(spot, research_request)
        else:
            report = await generate_report(stock_code, request=research_request)
            raw_report = report.model_dump()
        bad_paths = nonfinite_number_paths(raw_report)
        if bad_paths:
            # Log only field paths; no confidential input data or numbers.
            utils.logger.warning(
                "[web.app] analysis %s non-finite numeric observations: %s",
                task_id, bad_paths,
            )
        result = (public_etf_report(raw_report) if asset_type == "etf"
                  else public_stock_report(raw_report))
        # Validate BEFORE marking done, so a future unsupported payload type
        # becomes an ordinary failed task instead of repeated GET 500s.
        json.dumps(result, ensure_ascii=False, allow_nan=False)
        _tasks[task_id]["result"] = result
        _tasks[task_id]["status"] = "done"
    except Exception as e:
        utils.logger.error(f"[web.app] 分析任务 {task_id} ({stock_code}) 失败: {e}")
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = str(e)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    raw_input = req.stock_code.strip()
    if not raw_input:
        raise HTTPException(status_code=400, detail="stock_code 不能为空")

    normalized = raw_input.upper().removeprefix("SH").removeprefix("SZ")
    looks_like_etf = ((len(normalized) == 6 and normalized.isdigit()
                       and normalized[0] in "15")
                      or "ETF" in raw_input.upper() or "基金" in raw_input)
    etf = await resolve_etf(raw_input) if looks_like_etf else None
    if etf:
        stock_code = etf["stock_code"]
    elif looks_like_etf:
        alternatives = await search_etfs(raw_input, 60)
        if len(alternatives) > 1:
            raise HTTPException(
                status_code=409,
                detail=f"找到{len(alternatives)}只相关ETF，请从搜索下拉列表选择具体基金代码",
            )
        raise HTTPException(status_code=404, detail=f"未找到可核实的ETF行情: {raw_input}")
    else:
        stock_code = await resolve_stock_code(raw_input)
        if not stock_code:
            etf = await resolve_etf(raw_input)
            stock_code = etf["stock_code"] if etf else None
    if not stock_code:
        raise HTTPException(status_code=404, detail=f"未找到股票或ETF: {raw_input}")

    try:
        research_request = ResearchRequest(
            stock_code=stock_code,
            mode=req.mode,
            as_of=req.as_of,
            save_snapshot=req.save_snapshot,
        )
        if etf and req.mode != "live":
            raise NotImplementedError("ETF历史研究需要历史成分及披露日校验，当前只开放Live模式。")
        if etf and req.save_snapshot:
            raise NotImplementedError("ETF标准快照尚未支持，请取消保存快照后提交。")
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

    # Shared LAN access should not let one busy department launch unlimited
    # paid LLM analyses concurrently. In local/private mode this is unchanged.
    access = load_access_settings()
    if access.enabled:
        active = sum(1 for item in _tasks.values()
                     if item.get("status") in ("pending", "running"))
        if active >= access.max_concurrent:
            raise HTTPException(
                status_code=429,
                detail="分析任务已达到并发上限，请等待其他任务完成后重试。",
            )

    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "asset_type": "etf" if etf else "stock",
        "request": research_request.model_dump(mode="json"),
    }
    asyncio.create_task(_run_analysis(task_id, stock_code, research_request,
                                      "etf" if etf else "stock"))
    return AnalyzeResponse(task_id=task_id)


@app.get("/api/etfs/overview")
async def get_etf_overview(
    code: str = Query(..., min_length=6, max_length=16),
) -> Dict:
    """Price and latest published top-ten; no LLM, company charts or stock F10.

    This endpoint also recovers older task results produced before ETF
    asset_type/etf_data survived the public API projection.
    """
    fund = await resolve_etf(code)
    if fund is None:
        raise HTTPException(status_code=404, detail="未找到对应的场内ETF代码")
    payload = await collect_etf_overview(fund)
    return finite_json_numbers({
        "asset_type": "etf",
        "stock_name": fund["name"],
        "stock_code": fund["stock_code"],
        "etf_data": payload,
    })


@app.get("/api/stocks/suggest")
async def suggest_stocks(
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=8, ge=1, le=60),
) -> Dict:
    """A-share equity and exchange-traded fund suggestions (both code and name)."""
    normalized = q.strip().casefold()
    digits = normalized.removeprefix("sh").removeprefix("sz")
    # Theme and full ETF-name searches take precedence over stock autocomplete.
    etf_first = ("etf" in normalized or "基金" in normalized or
                 "沪深300" in normalized or "黄金股" in normalized or
                 "黄金产业" in normalized or
                 (len(digits) == 6 and digits.isdigit() and digits[0] in "15"))
    fund_task = asyncio.create_task(search_etfs(q, limit))
    stock_task = None if etf_first else asyncio.create_task(search_stocks(q, limit))
    try:
        funds = await asyncio.wait_for(fund_task, timeout=18.0)
    except Exception as exc:
        utils.logger.warning("[web.app] ETF suggestions unavailable: %s", exc)
        funds = []
    equity = {"items": [], "source": "none"}
    if stock_task is not None:
        try:
            equity = await asyncio.wait_for(stock_task, timeout=5.0)
        except Exception as exc:
            utils.logger.warning("[web.app] stock suggestions unavailable: %s", exc)
    stock_items = [{**item, "asset_type": "stock"} for item in equity.get("items", [])]
    # Preserve size order across ETF issuers. Stock ranking is only meaningful
    # for mixed queries where there is no explicit ETF theme.
    combined = funds + stock_items if etf_first else stock_items + funds
    return {
        "items": combined[:limit],
        "source": "stock+etf" if funds else equity.get("source"),
        "etf_count": len(funds),
        "etf_sort": "fund_assets_desc_unknown_last",
    }


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
    result = task["result"]
    if isinstance(result, dict):
        # A second projection protects responses from reports cached before
        # the privacy change, even when the HTML does not display the data.
        if result.get("asset_type") == "etf" or task.get("asset_type") == "etf":
            result = public_etf_report(result)
        else:
            result = public_stock_report(result)
    # Detailed exceptions may contain provider payloads or a pasted source
    # excerpt. Keep diagnostic details in server logs only, not public JSON.
    public_error = None
    if task["status"] == "failed":
        raw_error = str(task.get("error") or "")
        if "行情无有效价格" in raw_error:
            public_error = "当前行情不可用，无法形成可靠价格判断，请稍后重试。"
        elif "模型" in raw_error or "LLM" in raw_error:
            public_error = "本次模型分析未能完成，请重试或更换模型服务。"
        else:
            public_error = "本次股票分析未能完成，请稍后重试。"
    # Also protect already-stored task payloads created by older versions.
    # Never mutate _tasks while serving concurrent polls.
    return finite_json_numbers(
        {"status": task["status"], "result": result, "error": public_error}
    )


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


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    """Tiny service health check. LAN IP allowlist still applies."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    # App code is embedded into Python's _INDEX_HTML at import time. Do not
    # let a browser/proxy retain an outdated page across server redeploys.
    return HTMLResponse(_INDEX_HTML, headers={"Cache-Control": "no-store"})


@app.get("/assets/roe_discount.js")
async def roe_discount_javascript() -> Response:
    """Serve the standalone, versioned-by-repo interactive pricing engine."""
    source = Path(__file__).with_name("roe_discount.js").read_text(encoding="utf-8")
    return Response(source, media_type="application/javascript")


@app.get("/assets/research_desk.css")
async def research_desk_stylesheet() -> Response:
    """Local dashboard theme: self-hosted, with no browser font or CDN requirement."""
    css = Path(__file__).with_name("research_desk.css").read_text(encoding="utf-8")
    return Response(css, media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/assets/stock_autocomplete.js")
async def stock_autocomplete_javascript() -> Response:
    """Lightweight local autocomplete, no external search or CDN dependencies."""
    source = Path(__file__).with_name("stock_autocomplete.js").read_text(encoding="utf-8")
    return Response(source, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>股票与ETF投资助手 · Research Desk</title>
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
  .investor-reference,.style-insight {border:1px solid var(--line);border-radius:14px;background:#f8faff;padding:16px 18px;margin:14px 0;}
  .investor-reference h4,.style-insight h4 {font-size:17px;margin:0 0 8px;color:var(--ink);}
  .investor-band-table {width:100%;border-collapse:collapse;margin:12px 0;font-size:14px;}
  .investor-band-table th,.investor-band-table td {text-align:left;padding:10px 13px;border-bottom:1px solid #dfe6f3;}
  .investor-band-table th {font-size:12px;color:#54627b;}
  .verdict-line { display:flex; flex-wrap:wrap; align-items:center; gap:10px 16px; padding:4px 0 0; }
  .verdict-item { color:#4d607b; font-size:14px; }
  .verdict-item b { color:var(--ink); font-size:15px; }
  .verdict-industry { color:var(--muted); font-size:13px; }
  .verdict-summary { margin:16px 0 0; color:#344762; line-height:1.85; font-size:15px; white-space:pre-wrap; overflow-wrap:anywhere; }

  /* Charts in the verdict are evidence summaries, never new model estimates. */
  .verdict-figures { margin:12px 0 22px; }
  .verdict-summary + .inline-verdict-figures { margin-top:12px; }
  .inline-verdict-figures .verdict-figure { background:#fafdff; }
  .verdict-figures-title { font-weight:750; color:#2c3e58; font-size:15px; margin:0 0 4px; }
  .verdict-figures-note { font-size:12px; color:var(--muted); margin:0 0 12px; }
  .verdict-figure-grid { display:flex; flex-wrap:wrap; justify-content:center; align-items:stretch; gap:10px; }
  .verdict-figure { flex:1 1 320px; max-width:620px; padding:16px 17px 13px;
    background:#fff; border:1px solid var(--line); border-radius:13px; min-width:0; }
  /* One-to-three-value comparisons belong in a centered horizontal strip,
     not in a tall, half-page column chart. Never stretch these to fill a row. */
  .verdict-figure.verdict-figure-compact { flex:0 1 auto; width:max-content; max-width:100%;
    padding:10px 14px; border-radius:11px; }
  .verdict-compact-line { display:flex; flex-wrap:wrap; justify-content:center;
    align-items:center; column-gap:15px; row-gap:8px; }
  .verdict-compact-title { display:flex; align-items:baseline; flex-wrap:wrap;
    gap:4px 7px; font-size:13px; color:var(--ink); }
  .verdict-compact-title b { font-size:13px; white-space:nowrap; }
  .verdict-compact-period { font-size:10px; color:var(--muted); white-space:nowrap; }
  .verdict-compact-metrics { display:flex; flex-wrap:wrap; align-items:center;
    justify-content:center; gap:5px 0; font-variant-numeric:tabular-nums; }
  .verdict-compact-metric { display:inline-flex; align-items:baseline; gap:6px;
    white-space:nowrap; padding:0 12px; border-left:1px solid #dce3ef; }
  .verdict-compact-metric:first-child { border-left:0; padding-left:0; }
  .verdict-compact-metric .metric-name { font-size:12px; color:#53627a; }
  .verdict-compact-metric strong { font-size:15px; color:var(--ink); font-weight:750; }
  .verdict-compact-metric small { color:#7a879a; font-size:11px; }
  .verdict-compact-details { position:relative; font-size:11px; color:var(--muted); }
  .verdict-compact-details summary { cursor:pointer; white-space:nowrap;
    padding:4px 5px; list-style:none; border-radius:5px; user-select:none; }
  .verdict-compact-details summary::-webkit-details-marker { display:none; }
  .verdict-compact-details summary:hover { color:var(--brand-strong); background:#edf2ff; }
  .verdict-compact-details[open] { flex-basis:100%; }
  .verdict-compact-details p { margin:5px 0 0; font-size:11px; line-height:1.55;
    color:#637188; max-width:640px; }
  @media(max-width:730px){ .verdict-figure-grid { gap:8px; }
    .verdict-figure { flex-basis:100%; padding:14px; }
    .verdict-figure.verdict-figure-compact { max-width:100%; padding:10px 12px; }
    .verdict-compact-line { column-gap:8px; }
    .verdict-compact-metric { padding:0 8px; } }
  .verdict-figure h4 { margin:0 0 3px; font-size:14px; font-weight:750; }
  .verdict-figure .figure-period { margin:0 0 12px; font-size:11px; color:var(--muted); line-height:1.55; }
  .verdict-figure .figure-foot { margin:12px 0 0; font-size:11px; line-height:1.65; color:#637188; }
  .verdict-bars { display:flex; flex-direction:column; gap:12px; }
  .verdict-bar-head { display:flex; justify-content:space-between; gap:8px; align-items:baseline;
    font-size:12px; margin-bottom:4px; }
  .verdict-bar-head b { font-size:15px; font-variant-numeric:tabular-nums; }
  .verdict-track { height:12px; background:#e8edf6; border-radius:7px; overflow:hidden; }
  .verdict-track > span { display:block; height:100%; border-radius:7px; min-width:1px; }
  .verdict-yearbars { display:flex; justify-content:space-around; align-items:flex-end; gap:8px;
    height:135px; border-bottom:1px solid #dfe5ef; margin-top:9px; }
  .verdict-yearcol { flex:1; min-width:0; max-width:74px; display:flex; flex-direction:column;
    align-items:center; justify-content:flex-end; height:100%; font-size:11px; color:var(--muted); }
  .verdict-yearcol b { font-size:11px; font-variant-numeric:tabular-nums; color:#334764; }
  .verdict-yearcol .stick { display:block; width:55%; min-width:13px; max-width:35px;
    background:#5266c9; border-radius:4px 4px 0 0; }
  .verdict-yearcol .year { font-size:11px; margin-top:5px; white-space:nowrap; }
  .verdict-rail-marker { display:flex; justify-content:space-between; font-size:10px; color:#8090a9; margin-top:2px; }



  .dashboard-charts h3, .valuation-lab h3 { margin:0 0 5px; font-size:21px; }
  .section-caption { font-size:12px; color:var(--muted); margin:0 0 16px; }
  .metric-strip { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:0; }
  .metric { padding:15px; border:1px solid var(--line); border-radius:13px; background:#f8faff; min-width:0; }
  .metric label { font-size:11px; color:var(--muted); display:block; }
  .metric strong { display:block; margin-top:5px; font-size:23px; font-weight:800; letter-spacing:-.04em; font-variant-numeric:tabular-nums; }
  .metric small { font-size:11px; color:var(--muted); overflow-wrap:anywhere; }
  .chart-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
  /* A single compact macro strip, not two full-height empty chart tiles. */
  .macro-inline {display:flex;align-items:baseline;flex-wrap:wrap;gap:5px 22px;
    padding:12px 15px;margin:14px 0 8px;border:1px solid var(--line);
    border-radius:10px;background:#f8faff;font-size:12px;line-height:1.8;}
  .macro-inline-group {display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 12px;min-width:0;}
  .macro-inline-group b {font-size:13px;color:var(--ink);}
  .macro-inline-group small {color:var(--muted);font-size:11px;}
  .macro-impact {padding:5px 0 1px;font-size:13px;line-height:1.85;color:#30405b;}
  .macro-impact p {margin:4px 0;}
  .macro-impact strong {color:var(--ink);}
  @media(max-width:600px){ .macro-inline {gap:10px;} .macro-inline-group {width:100%;} }

  .chart-tile { min-width:0; padding:18px; border:1px solid var(--line); border-radius:14px; background:#fcfdff; overflow-x:auto; }
  .chart-tile h4 { font-size:14px; margin:0 0 5px; }
  .chart-tile .section-caption { margin-bottom:10px; }
  .chart-tile svg { display:block; max-width:100%; height:auto; }
  /* Two compact, full-width rows instead of large market sentiment/industry tiles. */
  .market-structure-lines { grid-column:1 / -1; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
  .market-structure-row { display:grid; grid-template-columns:160px minmax(130px,1fr) auto;
    align-items:center; gap:14px; min-height:46px; padding:8px 2px; min-width:0; }
  .market-structure-row + .market-structure-row { border-top:1px solid #edf1f7; }
  .market-structure-heading { color:var(--ink); font-size:13px; font-weight:700; white-space:nowrap; cursor:help; }
  .market-structure-track { display:flex; height:12px; width:100%; overflow:hidden; border-radius:999px; background:#ecf0f6; }
  .market-structure-track span { display:block; height:100%; flex:none; }
  .market-structure-values { display:flex; flex-wrap:wrap; justify-content:flex-end; align-items:center;
    gap:5px 14px; white-space:nowrap; font-size:12px; font-variant-numeric:tabular-nums; color:#40516e; }
  .market-structure-value { display:inline-flex; gap:6px; align-items:center; }
  .market-structure-dot { display:inline-block; flex:none; height:8px; width:8px; border-radius:50%; }
  .market-structure-missing { grid-column:2 / -1; color:var(--muted); font-size:12px; }
  @media(max-width:700px) {
    .market-structure-row { grid-template-columns:145px minmax(0,1fr); }
    .market-structure-values { grid-column:2; justify-content:flex-start; }
  }

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
    .market-structure-row { grid-template-columns:1fr; gap:7px; padding:12px 0; }
    .market-structure-values,.market-structure-missing { grid-column:1; }
  }
  @media(prefers-reduced-motion:reduce) { *,*::before,*::after { scroll-behavior:auto !important; transition:none !important; } }

  .etf-main-card,.etf-block { background:#fff; border:1px solid var(--line); border-radius:18px;
    padding:22px 26px; margin:14px 0; box-shadow:var(--shadow); }
  .etf-main-card h2 {margin:0 0 6px;font-size:25px}
  .etf-subtitle,.etf-source {font-size:12px;color:var(--muted);line-height:1.65}
  .etf-metric-row {display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}
  .etf-metric {flex:1 1 135px;background:#f5f7fe;border-radius:11px;padding:10px 14px}
  .etf-metric span {display:block;color:#596982;font-size:12px}
  .etf-metric strong {display:block;font-size:21px;font-variant-numeric:tabular-nums}
  .etf-prose {white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.85;color:#344762}
  .etf-table-wrap {overflow-x:auto}
  .etf-table {width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
  .etf-table th,.etf-table td {text-align:left;padding:9px 10px;border-bottom:1px solid #e7eaf2;white-space:nowrap}
  .etf-table th {font-size:12px;color:#62718b;background:#f7f9fd}
  .etf-position {display:inline-flex;align-items:center;gap:7px}
  .etf-position-meter {display:inline-block;width:58px;height:8px;background:#e7ebf3;border-radius:8px;overflow:hidden}
  .etf-position-meter > i {display:block;height:8px;background:#5864c8}
  .etf-unknown {color:#8792a5}
</style>
<link rel="stylesheet" href="/assets/research_desk.css">
</head>
<body>
<main class="app-shell">
<header class="hero-header">
  <div class="hero-copy">
    <span class="eyebrow">EVIDENCE-FIRST · INVESTMENT RESEARCH</span>
    <h1>股票与ETF投资助手 <span>/ Research Desk</span></h1>
    <p>从真实经营与财务数据出发，研究公司价值、现金流、周期与风险。区分已核实的事实、主观判断和估值假设。</p>
  </div>
  <div class="hero-aside" aria-label="研究原则">
    <span>RESEARCH PRINCIPLE</span>
    <strong>经营价值优先</strong>
    <small>长期视角 · 证据约束 · 情景推演</small>
  </div>
</header>
<section class="control-deck" aria-label="股票与ETF研究入口">
  <div class="deck-heading"><h2>开始研究</h2><p>输入证券代码或简称，生成完整研究报告</p></div>
<div class="search-bar">
  <div class="stock-search-field">
    <input id="stockCode" aria-label="股票代码或名称" role="combobox"
      aria-autocomplete="list" aria-haspopup="listbox" aria-expanded="false"
      aria-controls="stockSuggestions" autocomplete="off"
      placeholder="输入股票或ETF，如 中国海油 / 600938 / 沪深300ETF / 510300" />
    <div id="stockSuggestions" class="stock-suggestions"
      role="listbox" aria-label="股票匹配结果" hidden></div>
  </div>
  <button id="submitBtn" type="button" aria-busy="false">
    <span class="submit-btn-text">分析</span>
    <span class="submit-btn-spinner" aria-hidden="true"></span>
  </button>
</div>
<p id="analysisError" class="analysis-error" role="alert" hidden></p>
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
</section>
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
</main>

<script>
let pollTimer = null;
let submitting = false;
let activeAnalysisTaskId = null;
let pollFailures = 0;

function setAnalysisBusy(busy) {
  submitting = Boolean(busy);
  const button = document.getElementById('submitBtn');
  button.disabled = submitting;
  button.classList.toggle('is-loading', submitting);
  button.setAttribute('aria-busy', String(submitting));
  button.querySelector('.submit-btn-text').textContent = submitting ? '分析中' : '分析';
}

function showAnalysisError(message = '') {
  const error = document.getElementById('analysisError');
  error.textContent = message;
  error.hidden = !message;
}

function finishAnalysis() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  activeAnalysisTaskId = null;
  pollFailures = 0;
  setAnalysisBusy(false);
}

function scheduleAnalysisPoll(taskId) {
  if (!submitting || taskId !== activeAnalysisTaskId) return;
  pollTimer = setTimeout(() => {
    pollTimer = null;
    pollTask(taskId);
  }, 2000);
}

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
    '<button type="button" class="stock-chip" data-code="' + s.code + '">' + s.name + ' (' + s.code + ')</button>'
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
  // Prevent a second click (including watchlist clicks and Enter) from
  // launching another expensive backend job while the first one is running.
  if (submitting) return;
  const stockCode = document.getElementById('stockCode').value.trim();
  if (!stockCode) {
    showAnalysisError('请输入股票名称或代码（也支持ETF）');
    return;
  }
  const mode = document.getElementById('researchMode').value;
  const asOf = document.getElementById('asOfDate').value;
  if (mode === 'historical' && !asOf) {
    showAnalysisError('历史模式必须选择截止日期');
    return;
  }
  showAnalysisError();
  document.getElementById('result').innerHTML = '';
  setAnalysisBusy(true);
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stock_code: stockCode,
        mode,
        as_of: mode === 'historical' ? asOf : null,
        save_snapshot: document.getElementById('saveSnapshot').checked,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error('提交失败：' + (err.detail || 'HTTP ' + res.status));
    }
    const data = await res.json();
    if (!data || !data.task_id) throw new Error('后端没有返回分析任务编号');
    activeAnalysisTaskId = data.task_id;
    pollFailures = 0;
    scheduleAnalysisPoll(activeAnalysisTaskId);
  } catch (err) {
    finishAnalysis();
    showAnalysisError(err.message || '提交失败，请稍后重试');
  }
}

async function pollTask(taskId) {
  if (!submitting || taskId !== activeAnalysisTaskId) return;
  try {
    const res = await fetch('/api/tasks/' + encodeURIComponent(taskId));
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    pollFailures = 0;

    if (data.status === 'done') {
      finishAnalysis();
      if (!data.result) {
        showAnalysisError('分析已结束，但没有返回报告');
        return;
      }
      try {
        renderResult(data.result);
      } catch (err) {
        showAnalysisError('报告显示失败：' + (err.message || '未知错误'));
      }
      return;
    }
    if (data.status === 'failed') {
      finishAnalysis();
      showAnalysisError('分析失败：' + (data.error || '未知错误'));
      return;
    }
    if (data.status !== 'pending' && data.status !== 'running') {
      throw new Error('未知任务状态：' + String(data.status));
    }
    // Status is no longer shown as a separate line: the spinning Analyze
    // button alone signals an in-progress job.
    scheduleAnalysisPoll(taskId);
  } catch (err) {
    if (!submitting || taskId !== activeAnalysisTaskId) return;
    pollFailures += 1;
    // Transient polling failures should not immediately orphan a live task.
    // But a permanently unreachable backend must not spin forever.
    if (pollFailures >= 10) {
      finishAnalysis();
      showAnalysisError('无法获取分析进度，请检查后端连接后刷新页面查看结果');
    } else {
      scheduleAnalysisPoll(taskId);
    }
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

  const series = (x.holder_count_series || []).filter(p=>
    p && numericOrNull(p.holders)>0 && numericOrNull(p.price)>0)
    .sort((a,b)=>String(a.period).localeCompare(String(b.period)));
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
function renderHolderChart(series, priceSource='daily_close') {
  // Absolute values on independent axes. Never mix account counts and yuan
  // on a shared normalized ('首期=100') scale; there is no detail table below.
  const f10Reference=priceSource==='ths_f10';
  const rows=(series||[]).filter(p=>numericOrNull(p.holders)>0 &&
    numericOrNull(p.price)>0 && /^\d{4}-\d{2}-\d{2}$/.test(String(p.period||'')));
  if(rows.length<2)return '';
  const n=rows.length;
  const W=740,H=295,L=62,R=57,T=55,B=47;
  const plotW=W-L-R,plotH=H-T-B,baseline=T+plotH;
  const holders=rows.map(p=>Number(p.holders)/10000);
  const prices=rows.map(p=>Number(p.price));
  const maximum=Math.max(...holders);
  const unit=Math.pow(10,Math.floor(Math.log10(maximum/5)));
  const step=[1,2,2.5,5,10].map(v=>v*unit).find(v=>v>=maximum/5)||unit*10;
  const top=step*Math.ceil(maximum/step);
  const minPrice=Math.min(...prices),maxPrice=Math.max(...prices);
  const padding=Math.max((maxPrice-minPrice)*.15,maxPrice*.035,.05);
  const priceLow=Math.max(0,minPrice-padding);
  const priceHigh=maxPrice+padding;
  const yH=v=>baseline-v/top*plotH;
  const yP=v=>baseline-(v-priceLow)/(priceHigh-priceLow)*plotH;
  const slot=plotW/n,barW=Math.min(slot*.54,42);
  const center=i=>L+slot*(i+.5);
  const fmtH=v=>v.toFixed(2);
  const fmtP=v=>v.toFixed(2);
  let grid='',bars='',linePts='',dots='',dateLabels='';
  const ticks=Math.round(top/step);
  for(let k=0;k<=ticks;k++){
    const v=k*step,y=yH(v);
    grid+='<line x1="'+L+'" y1="'+y.toFixed(1)+'" x2="'+(W-R)+'" y2="'+y.toFixed(1)+
      '" stroke="#e3eaf2" stroke-width="1"/>'+
      '<text x="'+(L-9)+'" y="'+(y+4).toFixed(1)+'" text-anchor="end" font-size="11" fill="#527399">'+
      Number(v.toFixed(3))+'</text>';
  }
  for(let k=0;k<=4;k++){
    const v=priceLow+(priceHigh-priceLow)*k/4;
    const y=baseline-k*plotH/4;
    grid+='<text x="'+(W-R+8)+'" y="'+(y+4).toFixed(1)+'" font-size="11" fill="#bc6708">'+
      fmtP(v)+'</text>';
  }
  rows.forEach((p,i)=>{
    const h=holders[i],price=prices[i],x=center(i),bh=baseline-yH(h);
    const date=String(p.period).slice(0,10);
    const priceDate=String(p.price_date||p.period).slice(0,10);
    const priceLabel=f10Reference?'F10参考股价':'同期收盘价';
    const tip=date+'：股东户数 '+fmtH(h)+' 万户；'+priceLabel+' '+
      fmtP(price)+' 元'+(f10Reference?'':'（价格日期 '+priceDate+'）');
    bars+='<rect class="holder-mark" x="'+(x-barW/2).toFixed(1)+
      '" y="'+yH(h).toFixed(1)+'" width="'+barW.toFixed(1)+'" height="'+
      Math.max(0,bh).toFixed(1)+'" fill="#3987eb" fill-opacity=".87" '+
      'data-tip="'+escapeHtml(tip)+'"/>';
    // Show actual counts directly above bars; tooltip keeps dates + prices.
    bars+='<text x="'+x.toFixed(1)+'" y="'+Math.max(T+12,yH(h)-7).toFixed(1)+
      '" text-anchor="middle" font-size="10" font-weight="600" fill="#2464b5">'+
      fmtH(h)+'</text>';
    const yp=yP(price);
    linePts+=(linePts?' ':'')+x.toFixed(1)+','+yp.toFixed(1);
    dots+='<circle class="holder-mark" cx="'+x.toFixed(1)+'" cy="'+yp.toFixed(1)+
      '" r="4.3" fill="#d97706" stroke="#fff" stroke-width="1.5" '+
      'data-tip="'+escapeHtml(tip)+'"/>';
    if(n<=9 || i===0 || i===n-1 || i%2===0){
      dateLabels+='<text x="'+x.toFixed(1)+'" y="'+(H-19)+
        '" text-anchor="middle" font-size="'+(n<=9?'9':'10')+'" fill="#66778d">'+
        escapeHtml(n<=9?date:date.slice(0,7))+'</text>';
    }
  });
  const legend='<g font-size="12" fill="#40516e">'+
    '<rect x="'+L+'" y="14" width="12" height="12" fill="#3987eb"/>'+
    '<text x="'+(L+18)+'" y="24">股东人数（万户）</text>'+
    '<circle cx="'+(L+202)+'" cy="19.5" r="4.8" fill="#d97706"/>'+
    '<text x="'+(L+214)+'" y="24">股价（元）</text></g>';
  return '<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img" '+
    'aria-label="股东人数（万户，左轴）与同期股价（元，右轴）实际数值走势图">'+
    legend+grid+bars+
    '<polyline points="'+linePts+'" fill="none" stroke="#d97706" stroke-width="2.4" '+
      'stroke-linejoin="round" stroke-linecap="round"/>'+
    dots+dateLabels+'</svg>';
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
function chartTile(title, subtitle, image, interpretation = '') {
  const reading = image && typeof interpretation==='string' && interpretation.trim() ?
    '<p class="chart-reading" aria-label="这张图对公司的投资含义">'+
      escapeHtml(interpretation.trim())+'</p>' : '';
  return '<div class="chart-tile"><h4>' + escapeHtml(title) + '</h4><p class="section-caption">' +
    escapeHtml(subtitle) + '</p>' + (image || '<div class="chart-empty">当前没有足够的可比数据，暂不绘图</div>') +
    reading+'</div>';
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
// Compact, one-line market breadth indicators; no oversized chart cards.
function renderMarketStructureRow(title, description, parts) {
  const total=parts.reduce((acc,item)=>acc+item.value,0);
  const valid=parts.length>0 && parts.every(item=>item.value!==null && Number.isFinite(item.value) && item.value>=0) && total>0;
  const heading='<strong class="market-structure-heading" title="'+escapeHtml(description)+'">'+escapeHtml(title)+'</strong>';
  if(!valid) {
    return '<div class="market-structure-row" role="group">'+heading+
      '<span class="market-structure-missing">当前没有足够的可比数据</span></div>';
  }
  const bars=parts.map(item=>
    '<span style="width:'+(100*item.value/total).toFixed(3)+'%;background:'+item.color+'" title="'+
    escapeHtml(item.description)+'"></span>').join('');
  const legend=parts.map(item=>
    '<span class="market-structure-value"><span class="market-structure-dot" style="background:'+
    item.color+'"></span>'+escapeHtml(item.description)+'</span>').join('');
  return '<div class="market-structure-row" role="group">'+heading+
    '<div class="market-structure-track" role="img" aria-label="'+
    escapeHtml(title+'：'+parts.map(item=>item.description).join('，'))+'">'+bars+'</div>'+
    '<div class="market-structure-values">'+legend+'</div></div>';
}
function renderSentimentMixChart(sentiment) {
  const s=sentiment || {};
  const bullish=numericOrNull(s.bullish), bearish=numericOrNull(s.bearish), neutral=numericOrNull(s.neutral);
  const values=[bullish,bearish,neutral];
  const total=values.every(v=>v!==null && v>=0)?values.reduce((a,b)=>a+b,0):0;
  const pct=v=>total>0?(100*v/total).toFixed(1)+'%':'暂缺';
  return renderMarketStructureRow('市场讨论结构','雪球言论只能作为情绪信号，不能替代财务事实',[
    {value:bullish,color:'#cc5364',description:'看多 '+pct(bullish)},
    {value:bearish,color:'#18977b',description:'看空 '+pct(bearish)},
    {value:neutral,color:'#9ba8bd',description:'中性 '+pct(neutral)}
  ]);
}
function renderIndustryBreadthChart(industry) {
  const data=industry || {};
  const adv=numericOrNull(data.advancing),dec=numericOrNull(data.declining);
  const total=adv!==null && dec!==null && adv>=0 && dec>=0?adv+dec:0;
  const pct=v=>total>0?(100*v/total).toFixed(0)+'%':'暂缺';
  return renderMarketStructureRow('行业上涨与下跌家数','同一交易时点的行业广度，不能独立当作投资结论',[
    {value:adv,color:'#ce5c68',description:'上涨 '+(adv===null?'暂缺':adv)+' 家（'+pct(adv)+'）'},
    {value:dec,color:'#159a79',description:'下跌 '+(dec===null?'暂缺':dec)+' 家（'+pct(dec)+'）'}
  ]);
}

// Explain concurrent shareholder-count and price changes in normal
// investor language. Holder counts describe accounts, NOT net institutional
// flows. The comparison uses genuine matching reporting dates, never interpolation.
function explainHolderPriceMovement(series) {
  if(!Array.isArray(series) || series.length<2)return null;
  const first=series[0],last=series[series.length-1];
  const pct=(start,end)=>(Number(end)/Number(start)-1)*100;
  const pricePct=pct(first.price,last.price);
  const holderPct=pct(first.holders,last.holders);
  if(!Number.isFinite(pricePct) || !Number.isFinite(holderPct))return null;
  const signed=v=>(v>0?'+':'')+v.toFixed(2)+'%';
  const direction=(p,h)=>{
    const pUp=p>=5,pDown=p<=-5,hUp=h>=10,hDown=h<=-10;
    if(pUp&&hUp)return {
      status:'risk', headline:'上涨伴随明显增户，筹码分散风险上升',
      explanation:'股价上涨时，持股账户却明显增多。这是筹码可能趋于分散的警示，后续上涨对买盘承接的要求更高。',
      phase:'上涨增户',
    };
    if(pDown&&hUp)return {
      status:'risk', headline:'股价下跌、股东户数大增，筹码趋于分散',
      explanation:'股价下跌时，持股账户反而明显增多。这不能解释为筹码改善，反而提示账户分散和下跌承接压力。',
      phase:'下跌增户',
    };
    if(pDown&&hDown)return {
      status:'improve', headline:'下跌伴随明显减户，出现筹码集中迹象',
      explanation:'股价回落期间，股东户数同步下降，说明持股账户的集中程度有所提高。这是筹码改善的线索，但不能证明抛压已经结束。',
      phase:'下跌减户',
    };
    if(pUp&&hDown)return {
      status:'improve', headline:'上涨同时减户，筹码集中迹象较明显',
      explanation:'股价上涨而股东户数减少，持股账户趋于集中。这可能有利于筹码结构，但不能据此断定是机构持续吸筹。',
      phase:'上涨减户',
    };
    if(hUp)return {
      status:'watch', headline:'股东户数明显增多，但价格趋势不足以确认筹码恶化',
      explanation:'股东户数增加提示账户趋于分散，不过同期股价变化不够显著，暂不把它直接视为高位派发。',
      phase:'增户',
    };
    if(hDown)return {
      status:'watch', headline:'股东户数明显减少，集中度有所提高',
      explanation:'持股账户减少意味着集中度可能提高，但在价格缺乏明确趋势时，不宜简单认定为吸筹或触底。',
      phase:'减户',
    };
    return {
      status:'neutral', headline:'股东户数变化不大，暂未出现明显筹码信号',
      explanation:'股价虽然可能波动，但股东户数并未出现足够明显的同步变化，当前更适合保持中性判断。',
      phase:'中性',
    };
  };
  const overall=direction(pricePct,holderPct);
  let largest=null;
  for(let i=1;i<series.length;i++){
    const a=series[i-1],b=series[i];
    const p=pct(a.price,b.price),h=pct(a.holders,b.holders);
    if(!Number.isFinite(p)||!Number.isFinite(h))continue;
    const stage=direction(p,h);
    // A notable two-sided stage, not a single noisy quarter.
    if(!(Math.abs(p)>=5 && Math.abs(h)>=10))continue;
    const importance=Math.abs(p)+Math.abs(h);
    if(!largest || importance>largest.importance) {
      largest={start:a.period,end:b.period,pricePct:p,holderPct:h,
        phase:stage.phase,importance};
    }
  }
  const stageDescription=largest && series.length>2
    ? '最明显的一段是 '+largest.start+' 至 '+largest.end+
      '：股价 '+signed(largest.pricePct)+'，股东户数 '+signed(largest.holderPct)+
      '，属于'+largest.phase+'。'
    : '';
  return {...overall,pricePct,holderPct,stageDescription,signed};
}

function renderChipPositionChart(context, report) {
  const rawSeries=(context && Array.isArray(context.price_holder_series))
    ? context.price_holder_series : [];
  let aligned=rawSeries.filter(p=>p &&
      /^\d{4}-\d{2}-\d{2}$/.test(String(p.period||'')) &&
      numericOrNull(p.holders)>0 && numericOrNull(p.price)>0)
    .sort((a,b)=>String(a.period).localeCompare(String(b.period)));
  let f10Reference=false;

  // For older live reports, chip_price_context can be completely absent even
  // when the already-returned THS F10 holder/price comparison has multiple dates.
  // Never use this live-only F10 data in historical point-in-time reports.
  if(aligned.length<2 && report && report.research_mode!=='historical'){
    const facts=((report.fundamentals||{}).facts||{});
    const cutoff=String(report.as_of||'').slice(0,10);
    const f10=(Array.isArray(facts.holder_count_series)?facts.holder_count_series:[])
      .filter(p=>p && /^\d{4}-\d{2}-\d{2}$/.test(String(p.period||'')) &&
        (!cutoff || String(p.period)<=cutoff) &&
        numericOrNull(p.holders)>0 && numericOrNull(p.price)>0)
      .map(p=>({period:p.period,holders:Number(p.holders),price:Number(p.price)}))
      .sort((a,b)=>String(a.period).localeCompare(String(b.period)));
    if(f10.length>=2){
      aligned=f10;
      f10Reference=true;
    }
  }

  if(aligned.length>=2){
    const analysis=explainHolderPriceMovement(aligned);
    const first=aligned[0],last=aligned[aligned.length-1];
    const note=f10Reference
      ? '价格使用同花顺F10参考值（不是独立行情接口核验的收盘价），结果仅作趋势观察。'
      : '股价采用相应股东统计截止日前最近交易日的实际收盘价。';
    return '<div class="chip-reading chip-reading-'+analysis.status+'">'+
      '<h5>'+escapeHtml(analysis.headline)+'</h5>'+
      '<p class="chip-reading-numbers">区间股价 <strong>'+analysis.signed(analysis.pricePct)+'</strong>'+
      '<span class="chip-reading-separator">·</span> 股东户数 <strong>'+
      analysis.signed(analysis.holderPct)+'</strong></p>'+
      '<p>'+escapeHtml(analysis.explanation)+'</p>'+
      (analysis.stageDescription?'<p>'+escapeHtml(analysis.stageDescription)+'</p>':'')+
      '</div>'+
      '<p class="section-caption chip-data-note">区间 '+escapeHtml(first.period)+'—'+
      escapeHtml(last.period)+'。'+note+
      ' 左轴显示股东人数（万户），右轴显示股价（元），均为实际数值，不插值；'+
      '跨分红、送转或拆股时，价格需要复权后才能作严格比较。</p>'+
      renderHolderChart(aligned,f10Reference?'ths_f10':'daily_close')+
      '<p class="section-caption">股东户数是账户数量，不等于散户人数或持股数量；'+
      '集中与分散只是结构线索，不能单凭这张图认定机构派发或吸筹。</p>';
  }
  if(!context){
    const facts=((report||{}).fundamentals||{}).facts||{};
    const holderPoints=Array.isArray(facts.holder_count_series)?facts.holder_count_series.length:0;
    return '<p class="section-caption">本次报告缺少已核验的股价与股东户数配对数据'+
      (holderPoints?'；F10返回 '+holderPoints+' 期股东记录，但未取得至少2期有股价的可比点':'；股东户数历史记录也暂缺')+
      '。当前不编造走势图，请检查历史行情和股东数据接口。</p>';
  }
  if(numericOrNull(context.position_52w_pct)===null)
    return '<p class="section-caption">尚未取得至少两个有披露依据的股东统计期与同期实际收盘价配对；'+
      '不能凭空画出股东人数或股价趋势。</p>';
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
  const summary=report.summary||{};
  const scores=summary.dimension_scores||[];
  const readings=summary.chart_interpretations||{};
  let html='<section class="dashboard-charts" id="report-charts"><h3>数据概览与维度图谱</h3>'+
    '<p class="section-caption">图表仅展示本次有日期的真实数据；下方解读区别已知事实与可能的经济机制。</p>';
  html+=renderInvestmentStyleStrip(report.valuation_model);
  html+='<div class="chart-grid" style="margin-top:16px;">';
  html+=chartTile('盈利质量的变化','ROE、毛利率与净利率；季报ROE为报告期间累计数，不跨期年化',
    renderProfitabilityTrendChart(report.profitability_trend),
    readings.profitability);
  html+=chartTile('研究视角倾向（非综合评分）','各方面的文字分析倾向示意；不得求和、平均或据此推导买卖结论',
    scores.length>=6?renderDimensionBars(scores):'',
    readings.dimension_scores);
  const sentimentHtml=renderSentimentMixChart(report.sentiment);
  const industryHtml=renderIndustryBreadthChart(report.industry_comparison);
  html+='<div class="market-structure-lines" role="group" aria-label="市场情绪与行业广度">'+
    sentimentHtml+industryHtml+
    ((sentimentHtml||industryHtml) && typeof readings.market_breadth==='string' &&
     readings.market_breadth.trim()?
     '<p class="chart-reading" aria-label="市场情绪和行业广度解读">'+
       escapeHtml(readings.market_breadth.trim())+'</p>':'')+'</div>';
  html+=chartTile('历史估值区间','PE(TTM)历史序列，失真区间将按原图分位裁剪',
    renderValuationHistoryChart((report.valuation_history||{}).history_monthly||[]),
    readings.valuation_history);
  html+='</div></section>';
  return html;
}


// Deprecated universal ROE-perpetuity valuation sliders were removed.
// The only stock price calculator now lives in the source-gated industry model panel.

// Render precisely one full research narrative. Never duplicate it as a
// per-dimension thesis overview, and never hide the detailed analysis in <details>.
function renderIssuerCapitalSection(report) {
  // Only render the public-safe, normalized company capital ledger. The full
  // private issuer capital context is intentionally never returned by API.
  const data=report.shareholder_capital_summary || {};
  const sales=data.completed_holder_sales || [];
  const issuances=data.equity_issuances || [];
  const buybacks=data.corporate_buybacks || [];
  const reconciled=data.reconciled_ownership_not_additional_sales || [];
  if(!sales.length&&!issuances.length&&!buybacks.length)return '';
  const fmt=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN'):'暂缺';
  // This ledger is INLINE under the shareholder-return analysis, not a
  // separate chapter far from the discussion that needs its evidence.
  let html='<div class="analysis-evidence capital-flow-section" id="report-capital">'+
    '<h5>回购、股东减持与股权融资公告记录</h5>'+
    '<p class="section-caption">区分公司回购、股东实际减持和公司增发；只有核实执行的交易才写为完成，公告链接可追溯。</p>'+
    '<div style="overflow-x:auto"><table class="investor-band-table">'+
    '<thead><tr><th>公告日</th><th>交易主体与性质</th><th>实际规模</th><th>出处</th></tr></thead><tbody>';
  sales.slice(0,10).forEach(e=>{
    const price=typeof e.average_sale_price_yuan==='number'?'，均价'+fmt(e.average_sale_price_yuan)+'元/股':'，均价暂缺';
    html+='<tr><td>'+escapeHtml(e.disclosed_at||'')+'</td><td>'+
      escapeHtml(e.actor||'股东')+'<div class="section-caption">已完成减持</div></td><td>'+
      fmt(e.shares)+'股'+escapeHtml(price)+'</td><td>'+
      (e.source_url?safeExternalLink(e.source_url,'公司公告'):'暂缺')+'</td></tr>';
  });
  issuances.slice(0,5).forEach(e=>{
    const kind=String(e.document_title||e.issuance_type||'');
    const issueLabel=/定增|非公开发行|向特定对象发行/.test(kind)?
      '公司定向增发':'公司发行新股融资';
    html+='<tr><td>'+escapeHtml(e.disclosed_at||'')+'</td><td>'+issueLabel+'</td><td>'+
      fmt(e.shares)+'股'+(e.issue_price_hkd?'，发行价'+fmt(e.issue_price_hkd)+'港元':'')+
      '</td><td>'+(e.source_url?safeExternalLink(e.source_url,'公司公告'):'暂缺')+'</td></tr>';
  });
  buybacks.slice(0,5).forEach(e=>{
    const desc=e.status==='plan_only'?'回购方案，非已完成':'公司回购';
    const intended=e.planned_use==='equity_incentive_or_employee_share_plan'?'（用于股权激励/员工持股）':'';
    const actual=numericOrNull(e.actual_amount_yuan);
    const shares=numericOrNull(e.shares);
    const amount=e.status==='plan_only'?'待实施，实际回购规模暂缺':
      actual!==null&&actual>0?fmt(actual)+' 元':
      shares!==null&&shares>0?fmt(shares)+' 股':
      '实际回购规模暂缺';
    html+='<tr><td>'+escapeHtml(e.disclosed_at||'')+'</td><td>'+
      escapeHtml(desc+intended)+'</td><td>'+escapeHtml(amount)+'</td><td>'+
      (e.source_url?safeExternalLink(e.source_url,'公司公告'):'暂缺')+'</td></tr>';
  });
  html+='</tbody></table></div>';
  if(reconciled.some(r=>r.fully_reconciled))html+=
    '<p class="section-caption">持股比例变动公告中部分减持数量已包括上面列出的交易，不再重复相加。发行新股、股权激励引起的持股比例稀释也不等于主动卖股。</p>';
  html+='<p class="section-caption">控股股东与高管出售股份获得的资金归卖方，不归上市公司；公司发行新股则可能稀释原股东。'
    +'是否构成高位减持，需要比较交易当时的复权股价和估值位置，不能仅凭绝对成交价下结论。</p></div>';
  return html;
}


// Figures for the investment verdict use ONLY source-backed report fields.
// They work with existing cached reports, and never extract arbitrary values
// from LLM-authored prose or fabricate missing observations.
function verdictNumber(v) {
  const n=numericOrNull(v);
  return n!==null && Number.isFinite(n)?n:null;
}
function verdictFixed(v, dp=2) {
  return Number(v).toFixed(dp);
}
function verdictFigure(title, period, body, foot) {
  return '<section class="verdict-figure"><h4>'+escapeHtml(title)+'</h4>'+
    '<p class="figure-period">'+escapeHtml(period)+'</p>'+
    body+(foot?'<p class="figure-foot">'+escapeHtml(foot)+'</p>':'')+'</section>';
}
// Short evidence belongs on ONE centered row. The complete caveat stays
// available behind an explicit, keyboard-accessible disclosure.
function verdictCompactFigure(title, period, items, note) {
  if(!Array.isArray(items) || !items.length || items.length>3)return '';
  const line=items.map(item=>{
    const extra=item.detail?
      '<small>'+escapeHtml(String(item.detail))+'</small>':'';
    return '<span class="verdict-compact-metric">'+
      '<span class="metric-name">'+escapeHtml(String(item.label||''))+'</span>'+
      '<strong>'+escapeHtml(String(item.value))+'</strong>'+extra+'</span>';
  }).join('');
  // Allow a genuinely title-free metric strip. Keep the report period
  // unobtrusively at the end so figures from different dates aren't mixed.
  const heading=title ?
    '<div class="verdict-compact-title"><b>'+escapeHtml(title)+'</b>'+
      (period?'<span class="verdict-compact-period">'+escapeHtml(period)+'</span>':'')+
    '</div>':'';
  const trailingPeriod=(!title && period)?
    '<span class="verdict-compact-period">'+escapeHtml(period)+'</span>':'';
  return '<section class="verdict-figure verdict-figure-compact">'+
    '<div class="verdict-compact-line">'+heading+
    '<div class="verdict-compact-metrics">'+line+'</div>'+
    trailingPeriod+
    (note?'<details class="verdict-compact-details"><summary aria-label="查看图表说明">口径</summary>'+
      '<p>'+escapeHtml(note)+'</p></details>':'')+
    '</div></section>';
}

function verdictBar(label, value, upper, unit, color) {
  const width=Math.min(100,Math.max(0,upper>0?Math.abs(value)/upper*100:0));
  return '<div class="verdict-bar">'+
    '<div class="verdict-bar-head"><span>'+escapeHtml(label)+'</span><b>'+
      escapeHtml(verdictFixed(value)+unit)+'</b></div>'+
    '<div class="verdict-track"><span style="width:'+width.toFixed(2)+'%;background:'+
      color+'"></span></div></div>';
}
function verdictPercentile(label, pct, metric, years) {
  const width=Math.max(0,Math.min(100,pct));
  return '<div class="verdict-bar">'+
    '<div class="verdict-bar-head"><span>'+escapeHtml(label+'（近'+years+'年）')+
      '</span><b>'+escapeHtml(verdictFixed(pct,1)+'%')+'</b></div>'+
    '<div class="verdict-track"><span style="width:'+width.toFixed(1)+
      '%;background:'+(pct>=80?'#c27d45':'#6076bf')+'"></span></div>'+
    '<div class="verdict-rail-marker"><span>'+
      escapeHtml(metric===null?'历史低位':'现值 '+verdictFixed(metric,2)+' 倍')+
      '</span><span>历史高位</span></div></div>';
}
function verdictPercentileRow(rows) {
  if(!Array.isArray(rows))return null;
  for(const n of [5,10,3]) {
    const r=rows.find(x=>x && Number(x.years)===n &&
      verdictNumber(x.percentile)!==null && verdictNumber(x.percentile)>=0 &&
      verdictNumber(x.percentile)<=100);
    if(r)return r;
  }
  return null;
}
function collectVerdictFigures(report) {
  const facts=((report.fundamentals||{}).facts)||{};
  const hist=report.valuation_history||{};
  const figures=[];

  // The live issuer-filing fallback can be newer than F10. Use its
  // separately dated profit/cash pair together, never H1 cash with Q1 profit.
  const pairProfit=verdictNumber(facts.cash_profit_net_profit);
  const pairCash=verdictNumber(facts.cash_profit_operating_cash_flow);
  const hasOfficialPair=Boolean(facts.cash_profit_period &&
    pairProfit!==null && pairCash!==null);
  const profit=hasOfficialPair?pairProfit:verdictNumber(facts.net_profit);
  const cash=hasOfficialPair?pairCash:verdictNumber(facts.operating_cash_flow);
  if(profit!==null && cash!==null && profit>0) {
    const net=profit/1e8, operating=cash/1e8;
    const ratio=verdictNumber(facts.cash_to_profit_ratio);
    const cashItems=[
      {label:'净利润',value:verdictFixed(net)+'亿元'},
      {label:'经营现金流',value:verdictFixed(operating)+'亿元'},
    ];
    if(ratio!==null)cashItems.push({label:'现金／利润',value:verdictFixed(ratio,2)+'倍'});
    figures.push({id:'cash',match:/现金流|净利润|利润|回款|经营现金/,
      html:verdictCompactFigure('',
        String(facts.cash_profit_period||facts.finance_period||'报告期未注明'),
        cashItems, '')});
  }

  const pe=verdictPercentileRow(hist.pe_percentiles);
  const pb=verdictPercentileRow(hist.pb_percentiles);
  if(pe || pb) {
    const valuations=[];
    if(pe)valuations.push({
      label:'PE',
      value:verdictFixed(pe.percentile,1)+'%',
      detail:'（历史'+pe.years+'年分位）',
    });
    if(pb)valuations.push({
      label:'PB',
      value:verdictFixed(pb.percentile,1)+'%',
      detail:'（历史'+pb.years+'年分位）',
    });
    figures.push({id:'valuation',match:/估值|市盈率|市净率|PE|PB|分位|价格|便宜|贵/,
      html:verdictCompactFigure('', '', valuations, '')});
  }

  const payout=Array.isArray(report.dividend_chart)?
    report.dividend_chart.filter(r=>r && verdictNumber(r.dividend_per_10)!==null &&
      verdictNumber(r.dividend_per_10)>=0 && Number.isFinite(Number(r.year))):[];
  if(payout.length>=2){
    const points=payout.slice().sort((a,b)=>Number(a.year)-Number(b.year)).slice(-6);
    const max=Math.max(.001,...points.map(p=>Number(p.dividend_per_10)));
    let bars='<div class="verdict-yearbars" role="img" aria-label="按公告年度统计的每10股现金分红柱状图">';
    points.forEach(p=>{
      const v=Number(p.dividend_per_10);
      const h=Math.max(1,Math.round(v/max*91));
      bars+='<div class="verdict-yearcol"><b>'+verdictFixed(v,2)+
        '</b><span class="stick" style="height:'+h+'px"></span>'+
        '<span class="year">'+escapeHtml(String(p.year))+'</span></div>';
    });
    bars+='</div>';
    const dividendNotes='仅包含已返回的年度分红数据。回购不计入现金分红，也不将待实施方案当作已到账款。';
    figures.push({id:'dividends',match:/分红|股息|派息|股东回报|股息率/,
      html:points.length<=3 ?
        verdictCompactFigure('每10股现金分红（元）','按公告年度归集',
          points.map(p=>({label:String(p.year),value:verdictFixed(p.dividend_per_10)+'元'})),
          dividendNotes):
        verdictFigure('每10股现金分红（元）','按公告年度归集',bars,dividendNotes)});
  }

  const debtRows=((report.profitability_trend||{}).periods||[])
    .filter(row=>row && row.period &&
      verdictNumber(row.debt_ratio_pct)!==null &&
      verdictNumber(row.debt_ratio_pct)>=0 && verdictNumber(row.debt_ratio_pct)<=100)
    .slice().sort((a,b)=>String(a.period).localeCompare(String(b.period)));
  if(debtRows.length>=2) {
    const first=debtRows[0],last=debtRows[debtRows.length-1];
    const v0=Number(first.debt_ratio_pct),v1=Number(last.debt_ratio_pct);
    figures.push({id:'debt',match:/负债|资产|并购|收购|增发|杠杆/,
      html:verdictCompactFigure('资产负债率前后比较','',
        [
          {label:String(first.period),value:verdictFixed(v0)+'%'},
          {label:String(last.period),value:verdictFixed(v1)+'%'},
        ],
        '资产负债率是资产负债表的时点指标，不能单凭升降判断收购回报。')});
  }

  const client=verdictNumber(facts.top5_customer_pct);
  const overseas=verdictNumber(facts.overseas_revenue_pct);
  if((client!==null&&client>=0&&client<=100)||
     (overseas!==null&&overseas>=0&&overseas<=100)){
    const concentrations=[];
    if(client!==null && client>=0 && client<=100)
      concentrations.push({label:'前五大客户营收占比',value:verdictFixed(client)+'%'});
    if(overseas!==null && overseas>=0 && overseas<=100)
      concentrations.push({label:'海外收入占比',value:verdictFixed(overseas)+'%'});
    figures.push({id:'customers',match:/客户|煤电|航运|海外|收入结构|出口|集中度/,
      html:verdictCompactFigure('',
        String((report.fundamentals||{}).concentration_period||
          facts.finance_period||'披露期间未注明'),concentrations, '')});
  }

  const ref=((report.valuation_model||{}).investor_reference)||{};
  const earningsPower=ref.earnings_power||{};
  const scenarios=(
    earningsPower.status==='historical_sensitivity_only' &&
    Array.isArray(earningsPower.scenarios)
  )?earningsPower.scenarios.filter(row=>row &&
    verdictNumber(row.conditional_price_yuan)!==null &&
    verdictNumber(row.conditional_price_yuan)>0):[];
  if(scenarios.length>=2) {
    const market=verdictNumber(earningsPower.market_price_yuan);
    const upper=Math.max(...scenarios.map(row=>Number(row.conditional_price_yuan)),
      market===null?0:market);
    let body='<div class="verdict-bars">';
    if(market!==null&&market>0)
      body+=verdictBar('本次参考价',market,upper,'元','#6076bf');
    scenarios.slice(0,3).forEach(row=>{
      body+=verdictBar(String(row.name||'条件情景'),
        Number(row.conditional_price_yuan),upper,'元','#c27d45');
    });
    body+='</div>';
    const stressNotes='以历史EPS和PE分位数计算的敏感性对照，非公司合理价值；不同分位的组合不代表真实发生概率。';
    const scenarioItems=[];
    if(market!==null && market>0)
      scenarioItems.push({label:'本次参考价',value:verdictFixed(market)+'元'});
    scenarios.slice(0,3).forEach(row=>{
      scenarioItems.push({label:String(row.name||'条件情景'),
        value:verdictFixed(row.conditional_price_yuan)+'元'});
    });
    figures.push({id:'earnings_power',match:/利润|盈利|估值|价位|参考价|股价/,
      html:scenarioItems.length<=3 ?
        verdictCompactFigure('历史盈利 × PE 条件价格',
          String(earningsPower.pe_as_of||report.as_of||'价格日期未知'),
          scenarioItems,stressNotes):
        verdictFigure('历史盈利 × PE 条件价格',
          String(earningsPower.pe_as_of||report.as_of||'价格日期未知'),body,stressNotes)});
  }
  const price=verdictNumber((report.realtime_quote||{}).latest_price);
  const pbAnchor=verdictNumber((ref.historical_pb_anchor||{}).median_pb_reference_price);
  if(price!==null&&price>0&&pbAnchor!==null&&pbAnchor>0){
    figures.push({id:'price_refs',match:/参考价|估值|买入|合理|安全边际|价格/,
      html:verdictCompactFigure('现价与历史PB参考价',
        String(ref.as_of||report.as_of||'参考日期未知'),
        [
          {label:'当前行情价',value:verdictFixed(price)+'元'},
          {label:'历史PB中位对应价',value:verdictFixed(pbAnchor)+'元'},
        ],
        '历史PB对应价只是假设估值回到历史中位数时的结果，不是内在价值或推荐买入价。')});
  }
  return figures;
}
// Match each factual chart to the paragraph that actually discusses it.
// Figures are grounded in report fields; only their placement uses prose.
const VERDICT_FIGURE_PARAGRAPH_RULES = {
  cash: [
    [/(?:经营(?:活动)?现金流|经营现金|现金流.{0,12}净利润|净利润.{0,15}现金流|利润.{0,12}现金流|利润.{0,12}变成现金|现金回款)/, 10],
    [/(?:现金回笼|现金转换|回款情况|现金流质量)/, 8],
  ],
  valuation: [
    [/(?:市盈率|市净率|PE.{0,12}PB|PB.{0,12}PE|估值.{0,10}分位|估值.{0,10}高位|估值.{0,10}低位|历史估值|估值水平)/i, 10],
    [/(?:估值.{0,10}贵|当前估值|估值.{0,10}便宜|PE\b|PB\b)/i, 7],
  ],
  dividends: [
    [/(?:现金分红|每股分红|年度分红|历年分红|股息率|派息|股息|红利|分红)/, 10],
    [/股东回报/, 6],
  ],
  debt: [
    [/(?:资产负债率|负债率|有息负债|净负债|偿债压力|财务杠杆)/, 10],
    [/(?:债务负担|资产负债表)/, 6],
  ],
  customers: [
    [/(?:前五大客户|客户集中度|客户营收占比|海外收入占比|境外收入占比|海外营收|境外营收)/, 11],
    [/(?:客户结构|收入结构|客户占比|海外客户|主要客户|客户依赖)/, 7],
  ],
  earnings_power: [
    [/(?:历史EPS|历史盈利.{0,15}PE|历史PE.{0,15}盈利|盈利.{0,12}情景|历史.{0,8}条件价|历史参照价格|历史.{0,8}参考价格)/i, 12],
    [/(?:盈利能力压力|不同盈利.{0,8}价格|盈利.{0,8}估值情景|每股盈利.{0,38}(?:倍PE|倍市盈率)|三档.{0,12}(?:条件价|参考价))/i, 9],
  ],
  price_refs: [
    [/(?:历史PB.{0,12}(?:中位|参考|中枢)|PB.{0,10}(?:历史中位|参考价|中枢)|市净率.{0,10}(?:历史中位|中枢)|历史市净率.{0,8}中位)/i, 12],
    [/(?:PB估值参考价|资产估值参考价|PB回到.{0,8}中位)/i, 10],
  ],
};

function scoreVerdictFigureParagraph(figure, paragraph) {
  const rules=VERDICT_FIGURE_PARAGRAPH_RULES[figure.id]||[];
  let best=0;
  for(const [pattern,score] of rules)
    if(pattern.test(paragraph))best=Math.max(best,score);
  return best;
}

function assignVerdictFiguresToParagraphs(paragraphs, report) {
  const slots=paragraphs.map(()=>[]);
  const figures=collectVerdictFigures(report);
  const candidates=[];
  figures.forEach((figure,figureIndex)=>{
    if(figure.id==='valuation')return;  // now in header KPI strip
    paragraphs.forEach((paragraph,paragraphIndex)=>{
      const score=scoreVerdictFigureParagraph(figure,paragraph);
      if(score>=6)candidates.push({figure,figureIndex,paragraphIndex,score});
    });
  });
  // Assign globally: a passing mention in paragraph one must not steal a
  // chart from a later paragraph about that precise financial issue.
  candidates.sort((a,b)=>b.score-a.score||
    a.paragraphIndex-b.paragraphIndex||a.figureIndex-b.figureIndex);
  const used=new Set();
  for(const candidate of candidates){
    if(used.has(candidate.figure.id) ||
       slots[candidate.paragraphIndex].length>=2)continue;
    used.add(candidate.figure.id);
    slots[candidate.paragraphIndex].push(candidate.figure);
  }
  for(const slot of slots){
    slot.sort((a,b)=>figures.findIndex(v=>v.id===a.id)-
      figures.findIndex(v=>v.id===b.id));
  }
  return slots;
}

function renderVerdictFigureGroup(figures) {
  if(!figures.length)return '';
  return '<div class="verdict-figures inline-verdict-figures" role="group" '+
    'aria-label="上段文字对应的财务与估值图表">'+
    '<div class="verdict-figure-grid">'+
    figures.map(figure=>figure.html).join('')+'</div></div>';
}

// Keep each piece of supporting evidence adjacent to the discussion it
// supports. These blocks share the same pre-existing verified report data;
// no extra price retrieval, synthetic numbers or independently inferred events.
function renderShareholderReturnEvidence(report) {
  const data=report||{};
  const dividendRows=Array.isArray(data.dividend_chart)?data.dividend_chart:[];
  const dividendHistory=Array.isArray(data.dividend_history)?data.dividend_history:[];
  const rawBuybacks=Array.isArray(data.buyback_history)?data.buyback_history:[];
  let html='<div class="analysis-inline-evidence shareholder-return-evidence">';
  if(dividendRows.length){
    html+='<div class="analysis-evidence"><h5>历年分红与回购</h5>'+
      renderDividendChart(dividendRows)+'</div>';
  }else if(dividendHistory.length){
    html+='<div class="analysis-evidence"><h5>已披露分红记录</h5><ul>'+
      dividendHistory.slice(0,20).map(row=>
        '<li>'+escapeHtml(row.announce_date||'日期未知')+'：每10股现金分红 '+
        escapeHtml(String(row.dividend_per_10_shares??'暂缺'))+' 元（'+
        escapeHtml(row.progress||'进度待核实')+'）</li>').join('')+
      '</ul><p class="section-caption">分红方案与已经实施的分红不同，不能把待实施预案作为已到账现金。</p></div>';
  }else{
    html+='<p class="section-caption">历史分红记录暂缺；未取得记录不代表公司从未分红。</p>';
  }
  const ledger=renderIssuerCapitalSection(data);
  if(ledger){
    html+=ledger;
  }else{
    if(rawBuybacks.length){
      html+='<div class="analysis-evidence"><h5>回购公告记录</h5><ul>'+
        rawBuybacks.slice(0,12).map(row=>
          '<li>'+escapeHtml(row.announce_date||'日期未知')+
          '：'+escapeHtml(row.progress||'执行情况待核实')+
          '；已回购 '+escapeHtml(String(row.actual_amount??'未核实'))+
          '</li>').join('')+
        '</ul></div>';
    }
    html+='<p class="section-caption">减持与定增：本次没有返回可核实的已执行公告明细；不能据此认定没有发生。</p>';
  }
  return html+'</div>';
}

function renderThesisWithInlineChipEvidence(thesis, report) {
  const source=String(thesis||'').trim();
  if(!source)return {html:'',chipEvidenceInserted:false};
  let paragraphs=source.split(/\\n\\s*\\n/).map(p=>p.trim()).filter(Boolean);
  // Some cached reports use single newlines rather than blank-line paragraph
  // separators. Preserve their intended paragraph structure as well.
  if(paragraphs.length===1 && source.includes('\\n'))
    paragraphs=source.split(/\\n+/).map(p=>p.trim()).filter(Boolean);

  // Keep figures adjacent to the paragraph they support, not all up front.
  const figureSlots=assignVerdictFiguresToParagraphs(paragraphs,report);
  
  // Prefer the paragraph with actual holder-count/account evidence. Only use
  // a general "筹码" paragraph if the thesis has no explicit holder reference.
  let chipIndex=paragraphs.findIndex(p=>/(股东户数|股东人数|股东账户|持股账户|账户数)/.test(p));
  if(chipIndex<0)chipIndex=paragraphs.findIndex(p=>/(筹码结构|筹码分散|筹码集中|筹码)/.test(p));

  let html='';
  paragraphs.forEach((paragraph,index)=>{
    html+='<p class="verdict-summary">'+escapeHtml(paragraph).replace(/\\n/g,'<br>')+'</p>';
    if(index===chipIndex){
      html+='<div class="analysis-inline-evidence chip-analysis-evidence thesis-chip-evidence">'+
        '<h5>股东人数与同期股价走势</h5>'+
        renderChipPositionChart((report||{}).chip_price_context,report)+
        '</div>';
    }
    html+=renderVerdictFigureGroup(figureSlots[index]);
  });
  return {html,chipEvidenceInserted:chipIndex>=0};
}

function renderDimensionAnalysisSection(summary, report, includeChipEvidence = true) {
  const analyses = (summary || {}).dimension_analyses || {};
  const order = [
    ['management','管理层与接班传承'], ['fundamentals','经营基本面'],
    ['rd','研发能力'], ['chip_flow','筹码与资金'],
    ['price_position','股价位置'], ['cycle_position','行业周期'],
    ['policy_geopolitics','政策与国际形势'], ['retail_sentiment','市场情绪'],
    ['shareholder_returns','股东回报'], ['growth_elasticity','成长弹性'],
    ['a_share_structure','A股资金结构'], ['risk_quality','财务质量与尾部风险']
  ];
  const valid = order.filter(([key]) =>
    typeof analyses[key] === 'string' && analyses[key].trim());
  if (!valid.length) {
    // Older cached reports do not contain tool-generated per-dimension detail.
    // Do not fabricate twelve missing analyses or discard all existing charts:
    // the separate evidence-only fallback is rendered below by renderResult.
    return '';
  }
  let html = '<section class="research-panel research-narrative" id="report-dimensions">' +
    '<h3>多维度分析</h3>' +
    '<p class="section-caption">从公司经营、股价、资金和行业环境等方面逐项判断。</p>';
  order.forEach(([key, label], idx) => {
    const detail = String(analyses[key] || '').trim();
    html += '<article class="analysis-entry" id="report-dimension-' + key + '">' +
      '<h4><span class="analysis-index">' + String(idx + 1).padStart(2, '0') +
      '</span>' + escapeHtml(label) + '</h4>' +
      '<div class="analysis-prose">' +
      (detail ? escapeHtml(detail) : '<span class="section-caption">该项分析暂缺</span>') +
      '</div>';
    if(key==='chip_flow' && includeChipEvidence){
      html+='<div class="analysis-inline-evidence chip-analysis-evidence">'+
        '<h5>股东人数与同期股价走势</h5>'+
        renderChipPositionChart((report||{}).chip_price_context, report)+
        '</div>';
    }
    if(key==='shareholder_returns')html+=renderShareholderReturnEvidence(report);
    html+='</article>';
  });
  return html + '</section>';
}

// Older cached reports may lack narrative dimensions altogether. In that
// special case retain previously visible shareholder disclosures together in
// a clearly evidence-only section, without inventing a model-generated review.
function renderLegacyShareholderEvidence(report, includeChipEvidence = true) {
  const src=report||{};
  const ledger=src.shareholder_capital_summary||{};
  const facts=(src.fundamentals||{}).facts||{};
  const hasChip=includeChipEvidence && (!!src.chip_price_context ||
    (Array.isArray(facts.holder_count_series) && facts.holder_count_series.length>=2));
  const hasReturns=(src.dividend_chart||[]).length ||
    (src.dividend_history||[]).length ||
    (src.buyback_history||[]).length ||
    (ledger.completed_holder_sales||[]).length ||
    (ledger.equity_issuances||[]).length ||
    (ledger.corporate_buybacks||[]).length;
  if(!hasChip&&!hasReturns)return '';
  let html='<section class="research-panel research-narrative" id="report-shareholder-evidence">'+
    '<h3>筹码与股东回报资料</h3>'+
    '<p class="section-caption">旧版报告没有对应的分项分析文字；以下只展示已有证据，不补造投资结论。</p>';
  if(hasChip){
    html+='<div class="analysis-inline-evidence chip-analysis-evidence">'+
      '<h5>股东人数与同期股价走势</h5>'+
      renderChipPositionChart(src.chip_price_context,src)+'</div>';
  }
  if(hasReturns)html+=renderShareholderReturnEvidence(src);
  return html+'</section>';
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
function renderInvestorReference(model) {
  const ref=(model||{}).investor_reference || {};
  const dividend=ref.dividend_basis || {};
  const pb=ref.historical_pb_anchor || {};
  const norm=ref.normalized_earnings || {};
  const decomposition=ref.return_decomposition || {};
  const earnings=ref.earnings_power || {};
  const companyCoverage=(model||{}).company_view_coverage || {};
  const scenarios=decomposition.scenarios || [];
  const earningsScenarios=earnings.scenarios || [];
  const fmt=v=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(2):'暂缺';
  if(!dividend.years&&!pb.median_pb&&!norm.annual_roe_years&&!scenarios.length&&!earningsScenarios.length)return '';
  let html='<div class="investor-reference" id="report-investor-reference" style="margin-top:16px;">'+
    '<h4>投资者长期回报情景</h4>'+
    '<p class="section-caption">先看可持续ROE、留存转化和现金股息，再单独讨论估值变化。'+
    '不是按统一的股息率阈值倒推目标价；全部价格仅为有条件的敏感性计算。</p>';
  if(dividend.years&&dividend.years.length) {
    html+='<div class="metric-strip">'+
      metricCard('已派息年份分红中位',''+fmt(dividend.observed_median_cash_dividend_per_share)+'元/股',
                 (dividend.years||[]).join('、')+'年实施记录')+
      metricCard('历史股息与现价比值',fmt(dividend.median_implemented_dividend_yield_pct)+'%',
                 '历史实施记录不等于未来承诺')+
      '</div>';
  }
  if(scenarios.length) {
    html+='<p><b>五年持有情景：</b>增长≈ROE×(1−派息率)×留存转化率；'+
      '长期总回报拆成增长、现金股息和PB变化的近似贡献。终点PB不是预测。</p>'+
      '<div style="overflow-x:auto;"><table class="investor-band-table">'+
      '<thead><tr><th>情景</th><th>可持续ROE</th><th>派息率</th>'+
      '<th>留存转化</th><th>增长贡献</th><th>现金股息</th><th>PB变化/年</th>'+
      '<th>合计/年(近似)</th><th>要求收益率</th><th>条件参考价</th></tr></thead><tbody>'+
      scenarios.map(x=>'<tr><td>'+escapeHtml(x.name||'')+'</td><td>'+
        fmt(x.sustainable_roe_pct)+'%</td><td>'+fmt(x.payout_pct)+
        '%</td><td>'+fmt(x.retention_conversion_pct)+'%</td><td>'+
        fmt(x.growth_contribution_pct)+'%</td><td>'+
        fmt(x.cash_dividend_yield_pct)+'%</td><td>'+
        fmt(x.valuation_change_annualized_pct)+'%</td><td>'+
        fmt(x.illustrative_total_return_pct)+'%</td><td>'+
        fmt(x.required_return_pct)+'%</td><td><b>'+
        fmt(x.conditional_reference_price)+'元</b></td></tr>').join('')+
      '</tbody></table></div>'+
      '<p class="section-caption">终点PB假设依据：'+
      escapeHtml(decomposition.terminal_pb_basis||'待核验')+
      '。期末价值占比越高，情景对远期PB越敏感。</p>';
  } else if((decomposition.missing_requirements||[]).length) {
    html+='<p class="section-caption">现金流折现情景尚未具备全部条件：'+
      escapeHtml(decomposition.missing_requirements.join('；'))+
      '。这不妨碍下述基于公司历史盈利的敏感性研究。</p>';
  }
  if(earningsScenarios.length) {
    html+='<h4>公司历史盈利能力 × 市场定价情景</h4>'+
      '<p class="section-caption">采用完整年度EPS与本公司过去的正PE月度样本，'+
      '分别取25%、50%、75%分位，仅说明不同盈利能力和市场定价同时出现时对应的价格。'+
      '不是公允价值、盈利预测，也不能把PE周期与EPS周期视为独立事件。</p>'+
      '<div style="overflow-x:auto;"><table class="investor-band-table">'+
      '<thead><tr><th>情景</th><th>历史EPS</th><th>历史PE</th>'+
      '<th>条件价格</th><th>与现价差</th></tr></thead><tbody>'+
      earningsScenarios.map(x=>'<tr><td>'+escapeHtml(x.name||'')+
      '</td><td>'+fmt(x.observed_annual_eps_yuan)+'元</td><td>'+
      fmt(x.historical_pe_quantile)+'倍</td><td><b>'+
      fmt(x.conditional_price_yuan)+'元</b></td><td>'+
      fmt(x.relative_to_market_pct)+'%</td></tr>').join('')+'</tbody></table></div>'+
      '<p class="section-caption">使用'+
      escapeHtml((earnings.earnings_years||[]).join('、'))+'年EPS和'+
      escapeHtml(String(earnings.pe_sample_months||0))+
      '个月正PE观测；周期股的低利润高PE可能破坏上述分位组合。'+
      '请用镍钴锂利润预测、现金流、资本开支与债务变化交叉检验。</p>';
  } else if(earnings.status==='insufficient_history') {
    html+='<p class="section-caption">历史盈利敏感性未满足数据条件：'+
      escapeHtml((earnings.cautions||[]).slice(-1)[0]||'缺少完整年度EPS或同期PE样本')+
      '。</p>';
  }
  if(companyCoverage.status==='found_dated_hypotheses') {
    html+='<p class="section-caption">本地知识库检索到'+
      escapeHtml(String(companyCoverage.count||0))+
      '条涉及本公司的历史估值观点（最近一条发表于'+
      escapeHtml(companyCoverage.latest_published_at||'未知日期')+
      '）。这些观点作为需复核的利润与估值假设进入文字研究，并非财报事实。</p>';
  } else if(companyCoverage.status==='not_found_in_local_archive') {
    html+='<p class="section-caption">本地归档未发现可用的本公司估值观点，'+
      '不代表研究者没有发帖；本次分析不能声称已采用其最新具体估值。</p>';
  }
  if(pb.median_pb)html+='<p><b>历史PB：</b>近'+escapeHtml(String(pb.window_years))+
    '年中位'+fmt(Number(pb.median_pb))+'倍，对应'+
    fmt(pb.median_pb_reference_price)+'元；这只是历史定价中枢，周期公司尤其不能直接把它当作合理价值。</p>';
  if(norm.annual_roe_years)html+='<p><b>跨周期盈利：</b>完整年度ROE中位'+
    fmt(norm.median_roe_pct)+'%，按当前账面权益折算的盈利收益率约'+
    fmt(norm.indicative_normalized_earnings_yield_pct)+'%；不能直接当现金股息率。</p>';
  html+='<p class="section-caption">未实施分红、增发稀释、未来资本开支、回购用途、负债及ROE下滑'+
    '可能改变长期收益；不能把这些情景当作已核实的公司价值。</p></div>';
  return html;
}
// Give investors a readable explanation rather than the model's internal
// four-field research checklist. Different hypotheses may describe the same
// underlying mechanism, so keep only one example per theme in the short view.
// Backwards-compatible gate for cached reports: reject commentary about
// technology attracting money / dividends falling when the actual displayed
// period shows dividend up and technology down (and vice versa).
function styleCauseDirectionConflict(text, style) {
  const raw=String(text||'');
  const red=numericOrNull(style && style.dividend_index_return_pct);
  const growth=numericOrNull(style && style.growth_index_mean_return_pct);
  if(red===null || growth===null)return false;
  if(/此前|之前|前期|过去|先前|一度/.test(raw) &&
     /如今|随后|反转|获利回吐|回撤|高低切/.test(raw))return false;
  if(red>0 && growth< -2 && red-growth>2) {
    return /红利(?:股|板块|资产|类产品|类资金|相对)?[^，。；]{0,14}(?:承压|走弱|领跌|相对下跌|被边缘化|遭抛售|弱于科技|跌得更厉害)|(?:资金|机构|投资者|公募|市场)[^，。；]{0,20}(?:更愿意?追(?:逐)?科技|更愿追科技|更偏科技|转向科技|流向科技|涌入科技|追捧科技)|科技(?:股|成长|板块|主题|类指数基金)?[^，。；]{0,19}(?:持续领涨|正在领涨|相对走强|跑赢红利|买盘持续增强|受到被动买盘推动上涨)/.test(raw);
  }
  if(growth>0 && red< -2 && growth-red>2) {
    return /科技(?:股|成长|板块|主题)?[^，。；]{0,16}(?:承压|大幅回撤|持续下跌|遭抛售|跑输红利)|(?:资金|机构|投资者|公募)[^，。；]{0,20}(?:更偏红利|转向红利|流向红利|追捧红利)|红利(?:股|板块|资产)?[^，。；]{0,14}(?:持续领涨|跑赢科技|相对走强)/.test(raw);
  }
  return false;
}

function readableStyleCauses(drivers, style) {
  const themes=new Set(), result=[];
  for(const item of (Array.isArray(drivers)?drivers:[])){
    if(!item || item.evidence_status!=='private_research_hypothesis' ||
       typeof item.mechanism!=='string') continue;
    const explanation=item.mechanism.trim();
    if(!explanation || styleCauseDirectionConflict(explanation,style))continue;
    const text=[explanation,item.conditions||''].join(' ');
    let key='general', label='其他可能因素';
    if(/游资|量化|高频|短线交易|换手|交易拥挤/.test(text)) {
      key='trading'; label='短线交易';
    } else if(/国债|债券收益率|利率变化|险资/.test(text)) {
      key='rates'; label='利率与分红';
    } else if(/股息|派息|分红现金流|红利.+基本面|现金回报/.test(text) &&
              !/ETF发行|ETF供给/.test(text)){
      key='dividend'; label='现金回报';
    } else if(/ETF|公募|基金|调仓|存量资金|增量资金|资金迁移|资金再平衡/.test(text)){
      key='allocation'; label='资金配置';
    } else if(/科技|成长|估值|盈利预期/.test(text)){
      key='valuation'; label='估值变化';
    }
    // Unclassified arguments remain distinct; do not discard different ideas.
    const dedupeKey=key==='general'?'general_'+result.length:key;
    if(themes.has(dedupeKey))continue;
    themes.add(dedupeKey);
    result.push({label,explanation});
    if(result.length>=3)break;
  }
  return result;
}

// Live cross-industry market style: use a same-window sector sample instead of
// always comparing dividend and technology indices. The LLM can select which
// sectors deserve attention, but never supplies displayed return percentages.
function renderSectorRotationStyleStrip(style) {
  const rotation=style.sector_rotation||{};
  if(rotation.status!=='observed')return '';
  const universe=(Array.isArray(rotation.sectors)?rotation.sectors:[]).filter(x=>
    x && typeof x.name==='string' && Number.isFinite(Number(x.d20_pct)) &&
    /^\d{4}-\d{2}-\d{2}$/.test(String(x.latest_date||'')));
  if(universe.length<3)return '';
  const benchmarks=(Array.isArray(rotation.benchmarks)?rotation.benchmarks:[]).filter(x=>
    x && typeof x.name==='string' && Number.isFinite(Number(x.d20_pct)));
  const byName=new Map([...universe,...benchmarks].map(x=>[x.name,x]));
  const reading=style.rotation_reading||{};
  const preferred=(Array.isArray(reading.featured_sectors)?reading.featured_sectors:[])
    .filter(name=>typeof name==='string'&&byName.has(name));
  const selectedNames=[...new Set(preferred.length>=3?preferred:[
    ...universe.slice(0,3).map(x=>x.name),...universe.slice(-3).map(x=>x.name)
  ])].slice(0,9);
  const selected=selectedNames.map(name=>byName.get(name));
  const leader=universe[0], laggard=universe[universe.length-1];
  const fmt=v=>(v>0?'+':'')+v.toFixed(2)+'%';
  const tone=v=>v>0?'style-return-up':v<0?'style-return-down':'style-return-flat';
  let html='<div class="style-insight"><h4>当前市场在交易什么？</h4>'+
    '<p><b>近20日行业分化：'+escapeHtml(leader.name)+'相对靠前，'+
    escapeHtml(laggard.name)+'相对靠后</b> · <span class="section-caption">'+
    escapeHtml(rotation.as_of||'')+'</span></p>';
  html+='<div class="style-return-list" role="group" aria-label="动态行业涨跌幅">'+
    selected.map(x=>'<div class="style-return-row">'+
      '<span class="style-return-name">'+escapeHtml(x.name)+
      (x.category==='style_index'?' <small>（风格指数）</small>':'')+'</span>'+
      '<strong class="style-return-value '+tone(Number(x.d20_pct))+'">'+
      fmt(Number(x.d20_pct))+'</strong></div>').join('')+'</div>';
  if(rotation.rotation_detectable && Array.isArray(rotation.new_leaders) &&
     rotation.new_leaders.length) {
    html+='<p class="section-caption"><b>主线变化：</b>较此前20个交易日，'+
      escapeHtml(rotation.new_leaders.join('、'))+
      '进入当前行业样本涨幅前列。排名变化并不保证后续持续领涨。</p>';
  }
  if(typeof reading.summary==='string' && reading.summary.trim()) {
    html+='<p class="style-performance-reading"><b>市场解读：</b>'+
      escapeHtml(reading.summary)+'</p>';
  }
  const explanations=Array.isArray(reading.hypotheses)?reading.hypotheses:[];
  if(explanations.length) {
    html+='<section class="style-why"><h5>可能的驱动因素</h5>'+
      explanations.slice(0,2).map(x=>'<p class="style-cause-item">'+
        escapeHtml(x)+'</p>').join('')+'</section>';
  }
  html+='<p class="style-cause-caveat">已统计的'+
    escapeHtml(String(rotation.coverage||universe.length))+
    '个行业中，近20个交易日上涨'+escapeHtml(String(rotation.up_count??'—'))+
    '个、下跌'+escapeHtml(String(rotation.down_count??'—'))+
    '个。以上为行业样本价格表现，不代表覆盖全市场或证实板块之间发生资金迁移。'+
    '判断原因仍需结合ETF份额、基金持仓、行业盈利与估值变化。</p>';
  html+=renderStockYtdHighPosition(style);
  html+='<p class="section-caption">板块强弱不直接等于公司盈利质量或股票内在价值。'+
    '行业涨跌采用东方财富行业板块历史收盘数据，不同板块缺失时不填充。</p></div>';
  return html;
}

// Stock *position*, not stock momentum: two actual unadjusted closing
// prices from one Jan-to-as-of series. Do not substitute F10 point samples,
// 52-week extrema, or an intraday quote into this daily-close calculation.
function renderStockYtdHighPosition(style) {
  const high=numericOrNull(style.stock_ytd_close_high);
  const latest=numericOrNull(style.stock_ytd_latest_close);
  const decline=numericOrNull(style.stock_ytd_close_drawdown_pct);
  const highDate=String(style.stock_ytd_close_high_date||'');
  const asOf=String(style.stock_ytd_close_as_of||'');
  if(style.stock_ytd_close_basis!=='unadjusted_daily_close' ||
      high===null || latest===null || decline===null ||
      !(high>0 && latest>0 && latest<=high) ||
      decline>0 || decline < -100 ||
      !/^\d{4}-\d{2}-\d{2}$/.test(highDate) ||
      !/^\d{4}-\d{2}-\d{2}$/.test(asOf) ||
      highDate.slice(0,4)!==asOf.slice(0,4) ||
      highDate>asOf) return '';
  return '<p class="stock-ytd-position"><b>距年内最高收盘价</b> '+
    '<strong>'+Math.abs(decline).toFixed(2)+'%</strong> 回撤'+
    '<span class="stock-ytd-position-meta">（峰值 '+
    high.toFixed(2)+' 元，'+escapeHtml(highDate)+'；'+
    '最近收盘 '+latest.toFixed(2)+' 元，'+escapeHtml(asOf)+'；'+
    '未复权收盘价口径）</span></p>';
}

// With limited coverage we still show every dated, verifiable observation.
function renderLimitedMarketStyleStrip(style) {
  const data=style.sector_rotation||{};
  const valid=x=>x && typeof x.name==='string' && x.name &&
    x.d20_pct!==null && x.d20_pct!==undefined &&
    Number.isFinite(Number(x.d20_pct)) &&
    /^\d{4}-\d{2}-\d{2}$/.test(String(x.latest_date||''));
  const boards=(Array.isArray(data.sectors)?data.sectors:[]).filter(valid);
  const indexes=(Array.isArray(data.benchmarks)?data.benchmarks:[]).filter(valid);
  const merged=new Map();
  boards.forEach(x=>merged.set(x.name,{...x,kind:'行业'}));
  indexes.forEach(x=>merged.set(x.name,{...x,kind:'指数'}));
  const rows=[...merged.values()].sort((a,b)=>Number(b.d20_pct)-Number(a.d20_pct));
  if(!rows.length)return '';
  const featured=rows.length<=7?rows:[...rows.slice(0,3),...rows.slice(-3)];
  const fmt=v=>(v>0?'+':'')+v.toFixed(2)+'%';
  const klass=v=>v>0?'style-return-up':v<0?'style-return-down':'style-return-flat';
  let title='已观测行情';
  if(rows.length>1) {
    title=Number(rows[0].d20_pct)<=0?
      rows[0].name+'相对抗跌，'+rows[rows.length-1].name+'回撤更大':
      rows[0].name+'表现相对较强，'+rows[rows.length-1].name+'相对较弱';
  }
  let html='<div class="style-insight"><h4>当前市场在交易什么？</h4>'+
    '<p><b>'+escapeHtml(title)+'</b> · <span class="section-caption">'+
    escapeHtml(data.as_of||rows[0].latest_date||'')+'</span></p>'+
    '<div class="style-return-list" role="group" aria-label="已取得的近20日行业与指数涨跌幅">'+
    featured.map(x=>'<div class="style-return-row"><span class="style-return-name">'+
      escapeHtml(x.name)+' <small>（'+x.kind+'）</small></span>'+
      '<strong class="style-return-value '+klass(Number(x.d20_pct))+'">'+
      fmt(Number(x.d20_pct))+'</strong></div>').join('')+'</div>';
  const reading=style.rotation_reading||{};
  if(reading.status==='llm_selected' && typeof reading.summary==='string' && reading.summary.trim())
    html+='<p class="style-performance-reading"><b>有限数据解读：</b>'+
      escapeHtml(reading.summary)+'</p>';
  html+='<p class="style-cause-caveat">本次已取得'+boards.length+
    '个行业板块和'+indexes.length+'个指数的同期行情。'+
    '仅能比较显示的样本，不能据此断言整个A股市场的行业主线已切换，'+
    '也不能把相对涨跌直接认作实际资金流向。</p>';
  html+=renderStockYtdHighPosition(style);
  return html+'</div>';
}

function renderInvestmentStyleStrip(model) {
  const style=(model||{}).style_context || {};
  if((style.sector_rotation||{}).status==='observed') {
    return renderSectorRotationStyleStrip(style);
  }
  if(style.sector_rotation && style.sector_rotation.status!=='observed') {
    const partial=renderLimitedMarketStyleStrip(style);
    if(partial)return partial;
  }
  if(style.status!=='observed') {
    if(!style.sector_rotation)return '';
    return '<div class="style-insight"><h4>当前市场在交易什么？</h4>'+
      '<p class="section-caption">行业和指数数据源本次均未返回有日期的可比涨跌幅。'+
      '无法量化这段期间的市场风格，不代表市场没有出现轮动。</p></div>';
  }
  const events=style.dated_market_events || [];
  const redObserved=(style.dividend_index_return_pct===null ||
    style.dividend_index_return_pct===undefined)?null:Number(style.dividend_index_return_pct);
  const growthObserved=(style.growth_index_mean_return_pct===null ||
    style.growth_index_mean_return_pct===undefined)?null:Number(style.growth_index_mean_return_pct);
  let label=style.style_regime==='dividend_leading'?'红利资产相对走强':
    style.style_regime==='growth_leading'?'成长资产相对走强':
    style.style_regime==='mixed'?'风格相对收益不明显':'指数数据不足，关注最新市场事件';
  if(Number.isFinite(redObserved) && Number.isFinite(growthObserved) &&
     style.style_regime==='dividend_leading') {
    if(redObserved<.5 && growthObserved<-2)
      label='科技成长回撤，红利相对抗跌';
    else if(redObserved<0 && growthObserved<redObserved)
      label='市场普跌，红利跌幅相对较小';
    else if(redObserved>0 && growthObserved>0)
      label='红利与成长同涨，红利涨幅更大';
    else if(redObserved>=.5 && growthObserved<0)
      label='红利上涨，科技成长回撤';
  }
  let html='<div class="style-insight"><h4>当前市场在交易什么？</h4><p><b>'+
    escapeHtml(label)+'</b>';
  if(style.dividend_minus_growth_ytd_pp!==null &&
     style.dividend_minus_growth_ytd_pp!==undefined &&
     Number.isFinite(Number(style.dividend_minus_growth_ytd_pp)))
    html+=' · 红利相对成长'+
      (style.style_window==='20 trading days'?'近20个交易日':'年内')+'涨跌差'+
      escapeHtml(String(style.dividend_minus_growth_ytd_pp))+'个百分点';
  html+=' <span class="section-caption">'+escapeHtml(style.style_observed_as_of||'')+'</span></p>';
  // A percentage needs a number, not a bar chart whose long labels overlap
  // inside the article-width report. These may use source-specific windows;
  // never silently treat their difference as the separate style-spread index.
  const red=Number(style.dividend_index_return_pct), growth=Number(style.growth_index_mean_return_pct);
  if(style.dividend_index_return_pct!==null && style.dividend_index_return_pct!==undefined &&
     style.growth_index_mean_return_pct!==null && style.growth_index_mean_return_pct!==undefined &&
     Number.isFinite(red) && Number.isFinite(growth)) {
    const fmtPct=v=>(v>0?'+':'')+v.toFixed(2)+'%';
    const tone=v=>v>0?'style-return-up':v<0?'style-return-down':'style-return-flat';
    const returns=[['上证红利',red],['科创50／创业板平均',growth]];
    html+='<div class="style-return-list" role="group" aria-label="主要指数涨跌幅">'+
      returns.map(([name,value])=>
        '<div class="style-return-row"><span class="style-return-name">'+
        escapeHtml(name)+'</span><strong class="style-return-value '+tone(value)+'">'+
        fmtPct(value)+'</strong></div>'
      ).join('')+'</div>';
    if(Math.abs(red)<0.5 && growth< -2) {
      html+='<p class="style-performance-reading"><b>先看现象：</b>这段相对领先主要来自科技成长的明显回撤，'+
        '红利指数本身接近持平；相对抗跌不等于已经证实资金持续净流入红利。</p>';
    }
    const spread=Number(style.dividend_minus_growth_ytd_pp);
    if(style.dividend_minus_growth_ytd_pp!==null &&
       style.dividend_minus_growth_ytd_pp!==undefined && Number.isFinite(spread) &&
       Math.abs(spread-(red-growth))>0.11) {
      html+='<p class="section-caption style-return-note">上方相对涨跌差与两项指数涨跌幅'+
        '不是相同的比较口径，不能直接把这两个百分数相减得到该涨跌差。</p>';
    }
  }
  const causes=readableStyleCauses(style.causal_drivers,style);
  html+='<section class="style-why" aria-label="市场风格的可能原因">'+
    '<h5>为什么会这样？</h5>';
  if(causes.length) {
    html+='<div class="style-cause-prose">'+causes.map(item=>
      '<p class="style-cause-item"><strong>'+escapeHtml(item.label)+'：</strong>'+
      escapeHtml(item.explanation)+'</p>'
    ).join('')+'</div>'+
      '<p class="style-cause-caveat">这些是可能的解释，并不代表已证实资金从科技板块流向红利板块。'+
      '判断是否真的调仓，还需要查看同期的基金份额和持仓变化。</p>';
  } else if(Array.isArray(style.causal_drivers) && style.causal_drivers.length &&
            style.causal_drivers.some(item=>item &&
              styleCauseDirectionConflict(item.mechanism,style))){
    html+='<p class="style-cause-caveat">当前指数表现已经明确，但已有的历史观点解释的是另一个市场阶段，'+
      '无法说明本轮涨跌。这里不把相反方向的观点硬套到当前行情上。</p>';
  } else if(style.causal_knowledge_status==='no_matching_distilled_knowledge' ||
            style.causal_knowledge_status==='knowledge_available_unresolved'){
    html+='<p class="style-cause-caveat">指数的相对涨跌可以确认，背后的资金流向还缺少可靠证据。'+
      '暂时不能断言资金正在从科技转向红利。</p>';
  } else if(style.causal_knowledge_status==='historical_method_not_available'){
    html+='<p class="style-cause-caveat">当时的数据还不足以解释这轮风格变化，'+
      '这里不使用后来获得的资料倒推原因。</p>';
  } else {
    html+='<p class="style-cause-caveat">暂时缺少足够的原因分析资料。'+
      '可以先参考上方指数表现，不能仅凭涨跌猜测资金流向。</p>';
  }
  html+='</section>';
  events.slice(0,2).forEach(e=>{
    html+='<p><b>市场事件：</b>'+escapeHtml(e.headline||'')+
      '（'+escapeHtml(e.date||'')+'）'+
      (e.source_url?' <span>'+safeExternalLink(e.source_url,'报道来源')+'</span>':'')+
      '。限购可能意味着关注度升温，但不能直接据此判断顶部。</p>';
  });
  html+=renderStockYtdHighPosition(style);
  html+='<p class="section-caption">市场资金偏好与公司的长期盈利价值是两回事；'
     +'判断个股需同时看价格位置、实际派息、经营利润和指数相对走势。</p></div>';
  return html;
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
    const ref=model.investor_reference || {};
    const haveReference=(ref.dividend_basis||{}).years?.length ||
      (ref.return_decomposition||{}).scenarios?.length ||
      (ref.earnings_power||{}).scenarios?.length ||
      (ref.historical_pb_anchor||{}).median_pb;
    html+=renderInvestorReference(model);
    html+='<div class="evidence-block missing">'+
      '<b>'+(haveReference?'完整现金流估值仍待完善':'当前可用估值数据较少')+'</b>'+
      '<p>完整的股权现金流、未来分红覆盖和资本开支预测尚不充分；'
      +'以上参考情景不等同于公司目标价格。</p></div>';
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
  if(calc.status==='calculated')html+=renderInvestorReference(model);
  if(typeof renderRoeDiscountLab==='function')html+=renderRoeDiscountLab(model);
  const independent=model.cross_checks || [];
  if(independent.some(x=>x.status==='calculated')) {
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
  // One compact row for both rates and FX; all metrics are point-in-time
  // observations, never company profit sensitivities inferred from headlines.
  const observed=(macro.observations || []).filter(x=>x.usable && x.value!==null);
  const obs=Object.fromEntries(observed.map(x=>[x.metric,x]));
  const show=(key,digits=2)=>{
    const item=obs[key];
    if(!item||!Number.isFinite(Number(item.value)))return null;
    const v=Number(item.value);
    return {value:v.toFixed(digits),date:item.as_of||'',source:item.source||''};
  };
  const quote=show('usdcny_midpoint_cny_per_usd',4);
  const fxChange=show('usdcny_change_30obs_pct',2);
  const fedLower=show('fed_target_lower_pct',2),fedUpper=show('fed_target_upper_pct',2);
  const fedChange=show('fed_target_change_180d_pp',2);
  const usTen=show('us_10y_yield_pct',2);
  const lprOne=show('china_lpr_1y_pct',2),lprFive=show('china_lpr_5y_pct',2);
  const trend={appreciating:'人民币升值',depreciating:'人民币贬值',
    stable:'基本稳定'}[macro.rmb_direction]||'方向未核实';
  const macroPart=(label,value,when)=>{
    return '<span><b>'+escapeHtml(label)+'</b> '+escapeHtml(value)+
      (when?' <small>('+escapeHtml(when)+')</small>':'')+'</span>';
  };
  const ratePieces=[];
  if(fedLower&&fedUpper)ratePieces.push(macroPart('美联储目标利率',
    fedLower.value+'–'+fedUpper.value+'%',fedUpper.date));
  if(fedChange)ratePieces.push(macroPart('近180日政策利率变化',
    (Number(fedChange.value)>0?'+':'')+fedChange.value+'百分点',fedChange.date));
  if(usTen)ratePieces.push(macroPart('美国10年国债收益率',usTen.value+'%',usTen.date));
  if(lprOne||lprFive)ratePieces.push(macroPart('中国LPR',
    (lprOne?'1年'+lprOne.value+'%':'')+
    (lprOne&&lprFive?' / ':'')+
    (lprFive?'5年'+lprFive.value+'%':''),(lprOne||lprFive).date));
  const fxPieces=[];
  if(quote)fxPieces.push(macroPart('美元兑人民币',quote.value+'元',quote.date));
  if(fxChange)fxPieces.push(macroPart('近30个报价日',
    (Number(fxChange.value)>0?'+':'')+fxChange.value+'%',fxChange.date));
  fxPieces.push(macroPart('人民币汇率趋势',macro.rmb_is_dated?trend:'尚未核实最新方向',''));
  html+='<div class="macro-inline" role="group" aria-label="利率汇率一行摘要">'+
    '<div class="macro-inline-group"><b>利率</b>'+
      (ratePieces.join('')||'<small>本次暂无可核验的最新政策和国债利率观测</small>')+
    '</div><div class="macro-inline-group"><b>汇率</b>'+
      (fxPieces.join('')||'<small>本次暂无可核验的最新美元兑人民币报价</small>')+
    '</div></div>';
  const miner=macro.miner_transmission || {};
  if(miner.status==='evidence_bounded'){
    html+='<div class="macro-impact"><p><strong>金铜与汇率：</strong>'+
      escapeHtml(miner.currency_reading||'')+'</p>'+
      '<p><strong>美元利率与资源周期：</strong>'+
      escapeHtml(miner.rate_reading||'')+'</p>'+
      '<p><strong>矿山成本、债务与套保：</strong>'+
      escapeHtml(miner.financing_reading||'')+'</p>'+
      '<p class="section-caption">'+escapeHtml(miner.evidence_limit||'')+'</p></div>';
  } else {
    html+='<p class="section-caption">利率、汇率是宏观背景而非利润预测；'
      +'具体净影响需按公司产品定价、结算币种、债务和套保核验。</p>';
  }
  const broad=(model.style_context||{}).sector_rotation||{};
  const style=macro.a_share_style || {};
  if(broad.status==='observed') {
    const ordered=(broad.sectors||[]);
    if(ordered.length)html+='<p class="section-caption" style="margin-top:12px;">'+
      'A股行业观察：近20个交易日，'+escapeHtml(ordered[0].name)+
      '相对靠前，'+escapeHtml(ordered[ordered.length-1].name)+
      '相对靠后（'+escapeHtml(broad.as_of||'未知日期')+
      '）。这是行业样本相对表现，不直接改变内在价值。</p>';
  } else if(!model.style_context || !model.style_context.sector_rotation) {
  if(style.status==='observed') {
    const styleLabel={
      dividend_leading:'红利风格相对领先',
      growth_leading:'科技成长相对领先',mixed:'红利与成长差距有限',
    }[style.regime] || '风格无法确定';
    html+='<p class="section-caption" style="margin-top:12px;">A股观察：'+
      escapeHtml(styleLabel)+'；上证红利相对科创50/创业板指年内差'+
      escapeHtml(String(style.dividend_minus_growth_ytd_pp))+'个百分点（'+
      (style.style_window==='20 trading days'?'近20个交易日；':'年内；')+
      escapeHtml(style.as_of || '未知日期')+
      '）。这是风格相对收益，不直接改变内在价值。</p>';
  }
  }
  // Generic macro transmission templates remain in the backend but are not
  // repeatedly presented as a dense six-item list to end users.
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


// Build links only for section IDs that are present in this particular report.
// Conditional modules (capital flows, long-run returns, ROE model, old cached
// reports without 12 dimensions) must never produce dead anchors.
function renderReportToc(reportHtml) {
  const present=id=>reportHtml.includes('id="'+id+'"');
  const chapters=[
    ['report-verdict','投资结论'],
    ['report-charts','数据概览与维度图谱'],
    ['report-shareholder-evidence','筹码与股东回报资料'],
    ['report-dimensions','多维度分析'],
    ['industryValuationLab','行业估值与市场环境'],
    ['report-investor-reference','投资者长期回报情景'],
    ['roe-discount-lab','ROE · 增长 · 贴现率估值'],
  ].filter(([id])=>present(id));
  const dimensions=[
    ['management','管理层与接班传承'],['fundamentals','经营基本面'],
    ['rd','研发能力'],['chip_flow','筹码与资金'],
    ['price_position','股价位置'],['cycle_position','行业周期'],
    ['policy_geopolitics','政策与国际形势'],
    ['retail_sentiment','市场情绪'],['shareholder_returns','股东回报'],
    ['growth_elasticity','成长弹性'],['a_share_structure','A股资金结构'],
    ['risk_quality','财务质量与尾部风险'],
  ].filter(([key])=>present('report-dimension-'+key));
  if(!chapters.length)return '';
  const entry=([id,label])=>
    '<li><a href="#'+id+'"><span>'+escapeHtml(label)+
    '</span><span class="toc-arrow" aria-hidden="true">↗</span></a></li>';
  let html='<nav class="report-toc" id="report-toc" aria-label="研究目录">'+
    '<div class="toc-heading"><h3>研究目录</h3></div>'+
    '<ol class="toc-chapters">'+chapters.map(entry).join('')+'</ol>';
  if(dimensions.length){
    html+='<details class="toc-subsections"><summary>展开 '+
      dimensions.length+' 项分析子目录</summary>'+
      '<ol>'+dimensions.map(([key,label])=>entry(['report-dimension-'+key,label])).join('')+
      '</ol></details>';
  }
  return html+'</nav>';
}

// The six source-backed quote/valuation facts sit directly under the
// company name in the top identity card, before the report contents.
// Historical snapshots use the values supplied for the as-of date.
function verdictMetric(label, value, date, tone, detail, tooltip) {
  const safeTone=['price','valuation','profit','cash','low','mid','high','missing'].includes(tone)?
    tone:'mid';
  return '<div class="verdict-market-metric verdict-market-'+safeTone+'"'+
    (tooltip?' title="'+escapeHtml(String(tooltip))+'"':'')+'>'+
    '<span class="verdict-market-label">'+escapeHtml(String(label))+'</span>'+
    '<span class="verdict-market-value">'+escapeHtml(String(value))+'</span>'+
    (detail?'<span class="verdict-market-context">'+escapeHtml(String(detail))+'</span>':'')+
    (date?'<span class="verdict-market-date">'+escapeHtml(String(date))+'</span>':'')+
    '</div>';
}
function isEtfInstrument(report) {
  // Legacy reports may not have asset_type. An ETF in a stock-shaped
  // response must not display issuer-only PB, F10 ROE or cash-flow KPIs.
  return !!report && (report.asset_type === 'etf' || !!report.etf_data ||
    /ETF/i.test(String(report.stock_name||'')));
}
function renderVerdictMarketStrip(report) {
  const quote=report.realtime_quote||{};
  const valuation=report.valuation||{};
  const facts=(report.fundamentals||{}).facts||{};
  const history=report.valuation_history||{};
  const isHistorical=report.research_mode==='historical';
  const price=numericOrNull(quote.latest_price);
  const pb=numericOrNull(valuation.pb);
  const roe=numericOrNull(valuation.roe_pct);
  const cash=numericOrNull(facts.cash_to_profit_ratio);
  const peRow=verdictPercentileRow(history.pe_percentiles);
  const pbRow=verdictPercentileRow(history.pb_percentiles);
  const quotedAt=quote.quote_time||quote.date||quote.timestamp||report.as_of||'报价日期暂缺';
  const quoteLabel=isHistorical?'截止日收盘价':
    (quote.quote_mode==='verified_previous_close'?'最近核验收盘价':'参考股价');
  if (isEtfInstrument(report)) {
    const fund=report.etf_data?.fund||{};
    const fundPrice=numericOrNull(fund.latest_price);
    const displayPrice=fundPrice!==null?fundPrice:price;
    const label=fundPrice!==null?(fund.quote_label||'ETF现价'):quoteLabel;
    const at=fund.quote_time||fund.data_date||quotedAt;
    return '<div class="verdict-market-strip verdict-market-strip-etf" '+
      'role="group" aria-label="ETF现价">'+
      verdictMetric(label,displayPrice!==null&&displayPrice>0?
        displayPrice.toFixed(3)+' 元':'暂缺',
        at,displayPrice!==null&&displayPrice>0?'price':'missing')+
      '</div>';
  }
  const items=[
    verdictMetric(quoteLabel,price!==null&&price>0?price.toFixed(2)+' 元':'暂缺',
      quotedAt,price!==null&&price>0?'price':'missing'),
    verdictMetric('市净率 PB',pb!==null?pb.toFixed(2)+' 倍':'暂缺',
      valuation.valuation_as_of||'披露日期暂缺',pb!==null?'valuation':'missing'),
    verdictMetric('F10 披露ROE',roe!==null?roe.toFixed(2)+'%':'暂缺',
      valuation.valuation_as_of||'报告期暂缺',roe!==null?(roe>=0?'profit':'high'):'missing'),
    verdictMetric('现金流/归母净利',cash!==null?cash.toFixed(2)+' 倍':'暂缺',
      (facts.cash_profit_period||facts.finance_period||'报告期暂缺')+
        (facts.cash_profit_source?' · 官方财报':''),
      cash!==null?(cash>=1?'profit':cash>=0?'mid':'high'):'missing'),
  ];
  const makePercentile=(name,row)=>{
    const pct=row?numericOrNull(row.percentile):null;
    const valid=pct!==null&&pct>=0&&pct<=100;
    return verdictMetric(name,valid?pct.toFixed(1)+'%':'暂缺',
      history.as_of||'估值日期暂缺',valid?(pct<=20?'low':pct>=80?'high':'mid'):'missing',
      row?'历史'+row.years+'年分位':'历史分位暂缺');
  };
  items.push(makePercentile('PE',peRow),makePercentile('PB',pbRow));
  return '<div class="verdict-market-strip" role="group" aria-label="公司行情与关键指标">'+
    items.join('')+'</div>';
}


function renderEtfResult(report) {
  const data=report.etf_data||{}, fund=data.fund||{}, portfolio=data.portfolio||{};
  const stats=data.portfolio_stats||{}, price=data.etf_price_position||{}, summary=report.summary||{};
  const rows=Array.isArray(portfolio.holdings)?portfolio.holdings:[];
  const safe=value=>escapeHtml(String(value===undefined||value===null?'':value));
  const fmt=(value,suffix='',digits=1)=>{
    const n=numericOrNull(value);return n===null?'暂无数据':n.toFixed(digits)+suffix;
  };
  const heading=(title)=>'<h3>'+safe(title)+'</h3>';
  // An ETF is not a listed operating company. Never mount the six-company-
  // factor grid here, even when some constituent observations are present.
  const observedQuote=numericOrNull(fund.latest_price);
  const lastClose=numericOrNull(price.recent_close??price.price_close);
  // The daily close can be shown even in old saved ETF payloads that did
  // not enrich fund.latest_price. A dated CLOSE must not be called live.
  const hasMarketQuote=observedQuote!==null&&observedQuote>0;
  const latest=hasMarketQuote?observedQuote:lastClose;
  const closeAsOf=price.recent_close_as_of||price.price_as_of||'日期暂缺';
  const latestText=latest!==null&&latest>0?latest.toFixed(3)+' 元':'暂缺';
  const quoteLabel=hasMarketQuote?(fund.quote_label||'ETF现价'):
    (latest!==null&&latest>0?'最近交易日收盘价':'ETF行情暂无数据');
  const quoteAt=hasMarketQuote?(fund.quote_time||fund.data_date||
    '报价时间暂缺'):(closeAsOf+' 收盘（非实时）');
  const quoteDetail=hasMarketQuote?(fund.quote_source||''):
    (price.recent_close_source||'基金历史交易日数据');
  // Exchange trading price vs IOPV is a REFERENCE premium/discount, not
  // market price vs published fund NAV. Omit the number if IOPV is missing.
  const premium=numericOrNull(fund.iopv_premium_pct);
  const iopv=numericOrNull(fund.iopv);
  const hasPremium=hasMarketQuote && iopv!==null && iopv>0 &&
    premium!==null && Number.isFinite(premium);
  const premiumText=hasPremium?
    (premium>0?'+':'')+premium.toFixed(2)+'%':'暂无数据';
  const premiumLabel='溢价率';
  const turnover=numericOrNull(fund.turnover_yuan);
  const turnoverText=turnover===null||turnover<0?'暂无数据':
    (turnover>=1e8?(turnover/1e8).toFixed(2)+' 亿元':
      turnover>=1e4?(turnover/1e4).toFixed(1)+' 万元':
      turnover.toFixed(0)+' 元');
  const turnoverAt=fund.quote_time||fund.data_date||
    fund.quote_fetch_time||'时间未核实';
  let html='<article class="etf-whole-report-card" id="etf-whole-report-card" role="article" aria-label="ETF完整研究报告">'+
    '<section class="report-header-inline report-company-card report-etf-card" '+
    'role="group" aria-label="ETF基金与现价">'+
    '<div class="etf-hero-row">'+
      '<div class="report-company-identity"><h2 id="report-top">'+
        safe(report.stock_name||'ETF名称暂缺')+'</h2>'+
        '<span class="report-stock-code">'+safe(report.stock_code||'')+'</span>'+
      '</div>'+
      '<div class="verdict-market-strip verdict-market-strip-etf" role="group" aria-label="ETF行情和流动性">'+
        verdictMetric(quoteLabel,latestText,'',
          latest!==null&&latest>0?'price':'missing','',
          [quoteAt,quoteDetail].filter(Boolean).join(' · '))+
        verdictMetric(premiumLabel,premiumText,'',
          hasPremium?'valuation':'missing','',
          hasPremium?'相对IOPV的行情参考溢价率':'未取得可比较的实时IOPV')+
        verdictMetric('成交额',turnoverText,'',
          turnover!==null&&turnover>=0?'cash':'missing','',turnoverAt)+
      '</div>'+
    '</div>';
  // Visible KPI cards contain only a title and value. Source dates remain
  // attached to the report data and are available on hover.
  const dist=data.distribution||{};
  const cashYield=numericOrNull(dist.dividend_yield_ttm_pct);
  const distributable=numericOrNull(dist.distributable_profit_yuan);
  const lastDividend=numericOrNull(dist.latest_dividend_per_share);
  const yieldTitle='近12个月实际现金分红 / 行情参考价；'+
    String(dist.dividend_yield_price_date||'价格日期以行情记录为准');
  const profitTitle='截至 '+String(dist.distributable_profit_as_of||'报告期未核实')+
    ' · '+String(dist.distributable_profit_source||'数据来源未核实');
  const paidTtm=numericOrNull(dist.dividend_ttm_per_share);
  // Show fund-level cash-distribution metrics only if the FUND has
  // confirmed cash dividends in the trailing year. Do not infer a payout
  // from its "high dividend" name or its constituent stocks' dividend yields.
  if (paidTtm!==null && paidTtm>0) {
    const dividendMetrics=[];
    if (cashYield!==null && cashYield>0) {
      dividendMetrics.push(
        '<div class="etf-distribution-item" title="'+safe(yieldTitle)+'">'+
        '<span>近12个月分红率</span><strong>'+
        safe(cashYield.toFixed(2)+'%')+'</strong></div>');
    }
    if (distributable!==null && distributable>=0) {
      dividendMetrics.push(
        '<div class="etf-distribution-item" title="'+safe(profitTitle)+'">'+
        '<span>可供分配利润</span><strong>'+
        safe((distributable/1e8).toFixed(2)+' 亿元')+'</strong></div>');
    }
    if (lastDividend!==null && lastDividend>0) {
      dividendMetrics.push(
        '<div class="etf-distribution-item">'+
        '<span>最近每份分红</span><strong>'+
        safe(lastDividend.toFixed(4)+' 元')+'</strong></div>');
    }
    if(dividendMetrics.length) html+='<div class="etf-distribution-row" '+
      'role="group" aria-label="ETF已实施分红">'+
      dividendMetrics.join('')+'</div>';
  }
  // Place the top-ten disclosure inside the same visual card as the ETF identity,
  // independently of all discretionary/LLM-generated analysis sections.
  const disclosure=portfolio.disclosure_as_of||portfolio.as_of||'报告期未核实';
  html+='<div class="etf-top-holdings" id="etf-latest-top-ten">'+
    '<div class="etf-holdings-heading">'+
      heading('最新披露前十大重仓股')+
      '<p class="etf-source etf-holdings-meta">持仓截止日：'+safe(disclosure)+
      ' · '+safe(portfolio.source||'持仓来源暂缺')+
      (portfolio.retrieved_on?' · 查询日期：'+safe(portfolio.retrieved_on):'')+
      '。属于定期报告持仓，并非当日实时指数权重。</p>'+
    '</div>';
  if(rows.length) {
    html+='<div class="etf-table-wrap"><table class="etf-table"><thead><tr>'+
      '<th>排名</th><th>股票</th><th>代码</th><th>占基金净值</th>'+
      '</tr></thead><tbody>';
    rows.slice(0,10).forEach((h,i)=>{
      html+='<tr><td>'+safe(i+1)+'</td><td><b>'+safe(h.name||'未知名称')+
        '</b></td><td>'+safe(h.code||'')+'</td>'+
        '<td>'+safe(fmt(h.weight_pct,'%',2))+'</td></tr>';
    });
    html+='</tbody></table></div>'+
      '<p class="etf-source">前十大合计占基金净值：'+
      safe(fmt(stats.top10_weight_pct,'%',2))+
      (rows.length<10?'；目前只核实'+safe(rows.length)+'只，不补猜其余持仓。':'')+
      '</p>';
  } else {
    html+='<p class="etf-unknown">未取得可核实的最新一期持仓。'+
      '可到基金公司正式定期报告或基金档案核对；不使用静态示例冒充最新前十。</p>';
  }
  html+='</div></section>';
  const crowd=data.trading_crowding||{};
  if(Object.keys(crowd).length){
    const cx=(v,unit='')=>numericOrNull(v)===null?'未核实':
      numericOrNull(v).toFixed(2)+unit;
    html+='<section class="etf-block etf-financing-crowding" id="etf-financing-crowding">'+
      heading('融资交易与市场拥挤度')+
      '<div class="etf-metric-row">'+
        '<div class="etf-metric"><span>ETF自身融资余额</span><strong>'+
          safe(cx(crowd.margin_balance_yi,' 亿元'))+'</strong></div>'+
        '<div class="etf-metric"><span>融资余额近期变化</span><strong>'+
          safe(cx(crowd.margin_change_pct,'%'))+'</strong></div>'+
        '<div class="etf-metric"><span>近5日/此前20日成交额</span><strong>'+
          safe(cx(crowd.recent5_turnover_ratio,' 倍'))+'</strong></div></div>'+
      '<p class="etf-prose">'+safe(crowd.assessment||'暂无可比数据')+'</p>'+
      '<p class="etf-source">融资数据日期：'+safe(crowd.margin_date||'暂缺')+
      ' · 成交数据日期：'+safe(crowd.trade_date||'暂缺')+
      ' · 数据来源：'+safe(crowd.margin_source||'交易所融资融券披露')+
      '、'+safe(crowd.turnover_source||'场内ETF日成交')+
      '。只统计基金证券自身的融资交易，不将成分股融资余额加总。</p></section>';
  }
  const risk=data.holding_risk||{}, strategy=risk.management_style||{};
  html+='<section class="etf-block etf-unified-evidence" id="etf-unified-evidence">'+
    heading('重仓股、宏观与估值风险分析');
  html+='<div class="etf-analysis-subsection etf-manager-risk" id="etf-management-risk">'+
    heading(risk.risk_type||'指数调样与主动管理的高位风险')+
    '<div class="etf-prose">'+safe(risk.risk_summary||
    '需要先核实基金的主动或指数管理方式，以及重仓股是否处于股价和估值高位。')+'</div>';
  if (Object.keys(risk).length) {
    // Older persisted ETF reports lack observed_count; preserve known nonzero
    // coverage there instead of falsely reporting the entire portfolio missing.
    const hasPriceData=Number(risk.price_observed_count||0)>0 ||
      Number(risk.price_observed_weight_pct||0)>0;
    const hasValuationData=Number(risk.valuation_observed_count||0)>0 ||
      Number(risk.valuation_observed_weight_pct||0)>0;
    const totalHoldings=Number(risk.disclosed_holding_count||rows.length||0);
    html+='<div class="etf-metric-row">'+
      '<div class="etf-metric"><span>52周价格位置已核实</span><strong>'+
      safe(hasPriceData?fmt(risk.price_observed_weight_pct,'%',1):
        '未取得有效日线')+'</strong><small>'+
      safe(String(risk.price_observed_count||0)+'/'+String(totalHoldings)+'只')+
      '</small></div>'+
      '<div class="etf-metric"><span>其中52周高位重仓权重</span><strong>'+
      safe(hasPriceData?fmt(risk.high_price_weight_pct,'%',1):
        '尚无法判断')+'</strong></div>'+
      '<div class="etf-metric"><span>高估值分位重仓权重</span><strong>'+
      safe(hasValuationData?fmt(risk.high_valuation_weight_pct,'%',1):
        '尚无法判断')+'</strong><small>'+
      safe(String(risk.valuation_observed_count||0)+'/'+String(totalHoldings)+'只估值可核实')+
      '</small></div></div>';
    if (!hasPriceData && totalHoldings) {
      const failed=rows.filter(h=>h.price_lookup_status).length;
      html+='<p class="etf-source etf-unknown">目前披露的'+safe(totalHoldings)+
        '只重仓股中，未取得可核实的52周股价位置。'+
        (failed?'其中'+safe(failed)+'只港股的多个行情源没有返回有效近期日线。':'')+
        '这是数据覆盖不足，不能判断高位股实际占比。</p>';
    }
    const suspicious=Array.isArray(risk.watchlist)?risk.watchlist:[];
    if (suspicious.length) {
      html+='<p class="etf-source">高位观察：'+suspicious.slice(0,10).map(h=>
        safe(h.name||h.code)+' ('+safe(fmt(h.weight_pct,'%',1))+')：'+
        safe((h.flags||[]).join('、'))).join('；')+'</p>';
    } else {
      html+='<p class="etf-source">尚无可核实的高位重仓证据；缺乏估值数据不代表没有泡沫风险。</p>';
    }
    // Internal verification checklists and methodology limitations must not
    // appear as investor-facing narrative or small print.
  }
  html+='</div>';
  const macro=data.macro_market||{}, observations=macro.observations||{};
  const cycle=data.industry_cycle||{}, breadth=cycle.earnings_breadth||{};
  const macroFields=[
    'gold_cny','gold_usd','us_real_yield_10y','us_treasury_10y',
    'china_treasury_10y','china_lpr1y','china_m2_yoy','china_pmi',
    'china_official_gold_reserves','sector_commodity'
  ];
  html+='<div class="etf-analysis-subsection" id="etf-macro-evidence">'+
    heading('宏观、流动性与产品价格：最新可核实数据');
  const known=macroFields.filter(key=>observations[key]);
  if(known.length) {
    html+='<div class="etf-table-wrap"><table class="etf-table"><thead><tr>'+
      '<th>指标</th><th>最新</th><th>1个月</th><th>3个月</th><th>1年</th><th>日期</th>'+
      '</tr></thead><tbody>';
    known.forEach(key=>{
      const o=observations[key], prices=['gold_cny','gold_usd','sector_commodity'].includes(key);
      const change=period=>numericOrNull(o[period])===null?'—':fmt(o[period],'%',2);
      html+='<tr><td><b>'+safe(o.name||key)+'</b></td>'+
        '<td>'+safe(fmt(o.value,' '+(o.unit||''),2))+'</td>'+
        '<td>'+safe(prices?change('return_1m_pct'):'—')+'</td>'+
        '<td>'+safe(prices?change('return_3m_pct'):'—')+'</td>'+
        '<td>'+safe(prices?change('return_1y_pct'):'—')+'</td>'+
        '<td>'+safe(o.as_of||'日期缺失')+'</td></tr>';
    });
    html+='</tbody></table></div><p class="etf-source">数据来源：'+
      known.map(key=>observations[key]).map(o=>safe(o.name)+': '+safe(o.source||'未标')).join('；')+
      '。数据时点各不相同，缺失期间不做推算。</p>';
  } else html+='<p class="etf-unknown">宏观或相关产业数据暂无有效近期观测，不能把ETF价格走势当作行业盈利驱动的证明。</p>';
  html+='</div>';
  html+='<div class="etf-analysis-subsection" id="etf-cycle-evidence">'+
    heading('行业周期与前十大盈利广度')+
    '<div class="etf-prose"><b>'+safe(cycle.stage||'周期待确认')+'</b> · '+
      safe(cycle.confidence||'证据不足')+'</div>'+
    '<div class="etf-metric-row">'+
      '<div class="etf-metric"><span>已核实净利同比权重</span><strong>'+
        safe(Number(breadth.sample_count||0)>0?fmt(breadth.sample_weight_pct,'%',1):'尚未核实')+
        '</strong><small>'+safe(String(breadth.sample_count||0)+'/'+String(rows.length)+'只')+
        '</small></div>'+
      '<div class="etf-metric"><span>同比增速改善权重</span><strong>'+
        safe(Number(breadth.comparable_count||0)>0?fmt(breadth.accelerating_growth_weight_pct,'%',1):'尚无法判断')+
        '</strong></div>'+
      '<div class="etf-metric"><span>同比增速回落权重</span><strong>'+
        safe(Number(breadth.comparable_count||0)>0?fmt(breadth.decelerating_growth_weight_pct,'%',1):'尚无法判断')+
        '</strong></div>'+
    '</div><div class="etf-prose">'+safe((cycle.evidence||[]).join('；'))+
    '</div></div>';
  html+='<div class="etf-analysis-subsection" id="etf-holdings">'+heading('十大重仓股价格、业绩与估值穿透')+
    '<p class="etf-source">'+safe(portfolio.scope||'')+' · '+safe(portfolio.as_of||'披露期暂缺')+
    ' · '+safe(portfolio.source||'来源暂缺')+'</p>';
  if(rows.length) {
    html+='<div class="etf-table-wrap"><table class="etf-table"><thead><tr>'+
      '<th>重仓股</th><th>基金净值占比</th><th>52周价格位置</th>'+
      '<th>距52周高点</th><th>近一年涨跌</th><th>价格日期</th><th>财报期</th><th>净利同比</th><th>同比增速变动</th><th>ROE</th><th>PE(TTM)</th><th>PB</th><th>PE历史分位</th><th>PB历史分位</th>'+
      '</tr></thead><tbody>';
    rows.forEach(h=>{
      const pos=numericOrNull(h.price_position_pct);
      const meter=pos===null?'<span class="etf-unknown">未核实</span>':
        '<span class="etf-position"><span class="etf-position-meter"><i style="width:'+
        Math.max(0,Math.min(pos,100))+'%"></i></span>'+fmt(pos,'%',0)+'</span>';
      html+='<tr><td><b>'+safe(h.name)+'</b> <small>'+safe(h.code)+'</small></td>'+
        '<td>'+safe(fmt(h.weight_pct,'%',2))+'</td><td>'+meter+'</td>'+
        '<td>'+safe(fmt(h.below_52w_high_pct,'%',1))+'</td>'+
        '<td>'+safe(fmt(h.period_return_pct,'%',1))+'</td>'+
        '<td>'+safe(h.price_as_of||'未核实')+'</td>'+
        '<td>'+safe(h.financial_period||'暂无')+'</td>'+
        '<td>'+safe(fmt(h.net_profit_growth_pct,'%',1))+'</td>'+
        '<td>'+safe(fmt(h.growth_change_pp,'个百分点',1))+'</td>'+
        '<td>'+safe(fmt(h.roe_pct,'%',1))+'</td>'+ 
        '<td>'+safe(fmt(h.pe_ttm,' 倍',1))+'</td>'+ 
        '<td>'+safe(fmt(h.pb,' 倍',1))+'</td>'+ 
        '<td>'+safe(h.pe_history_position?fmt(h.pe_history_position.percentile,'%',1):'暂无数据')+'</td>'+ 
        '<td>'+safe(h.pb_history_position?fmt(h.pb_history_position.percentile,'%',1):'暂无数据')+'</td></tr>';
    });
    html+='</tbody></table></div>';
  } else html+='<p class="etf-unknown">未能取得有季度标记的基金持仓数据，不会猜测前十大重仓股。</p>';
  html+='<p class="etf-source">高位=近252交易日前复权收盘价在区间80%以上，绝不等于估值高。'+
    '已核实价格位置的披露持仓权重：'+safe(fmt(stats.price_observed_weight_pct,'%',1))+
    '；取得盈利指标的披露持仓权重：'+safe(fmt(stats.profit_observed_weight_pct,'%',1))+
    '；取得PE/PB的披露持仓权重：'+safe(fmt(stats.pe_pb_observed_weight_pct,'%',1))+
    '；有历史估值分位的权重：'+safe(fmt(stats.historical_valuation_observed_weight_pct,'%',1))+
    '；其中处于至少一项估值80%高分位的权重：'+safe(fmt(stats.high_valuation_position_weight_pct,'%',1))+
    '。没有数据的权重不视为零风险。</p></div>';
  html+='</section>';
  html+='<section class="etf-block" id="report-verdict">'+heading('投资结论')+
    (summary.stance?'<span class="stance-badge stance-'+safe(summary.stance)+'">'+
      safe(stanceLabel(summary.stance))+'</span>':
      '<span class="etf-unknown">研究判断暂未生成</span>')+
    '<div class="etf-prose">'+safe(summary.thesis_summary||'暂无结论')+'</div></section>';
  // Holdings are already listed with codes and weights in the top-ten
  // table. Do not render a second prose roll-call below it.
  // The fund structure risks already have a factual box above, and price,
  // IOPV and turnover are shown beside the ETF name. Avoid an extra lecture.
  const keys=['macro_liquidity','commodity_market','industry_cycle',
    'high_positions','earnings_cycle','valuation'];
  const labels=report.section_labels||{};
  const sections=summary.sections||{};
  const isDividendStrategy=/高股息|红利|股息/i.test(String(report.stock_name||''));
  // The dividend ETF report has three distinct jobs: pricing/drawdown,
  // earnings/payout sustainability and trading crowding (shown above).
  // Older cached LLM reports may repeat the SAME stocks and risk across
  // high_positions, valuation, industry_cycle, earnings_cycle, and risk_notes.
  const visibleKeys=keys.filter(key=>{
    if(!String(sections[key]||'').trim())return false;
    if(!isDividendStrategy)return true;
    if(key==='valuation' && sections.high_positions)return false;
    if(key==='industry_cycle' && sections.earnings_cycle)return false;
    if(key==='commodity_market' && sections.earnings_cycle)return false;
    return true;
  });
  for(const key of visibleKeys) {
    html+='<section class="etf-block">'+heading(labels[key]||key)+
      '<div class="etf-prose">'+safe(sections[key])+'</div></section>';
  }
  // For dividend ETFs the thesis already contains the primary downside
  // mechanism; another generic "主要风险" chapter repeats pricing and payout
  // data. Preserve the raw risk_notes in the API but omit the duplicate UI.
  if(summary.risk_notes && (!isDividendStrategy ||
    !(sections.high_positions||sections.earnings_cycle))) {
    html+='<section class="etf-block">'+heading('主要风险')+
      '<div class="etf-prose">'+safe(summary.risk_notes)+'</div></section>';
  }
  if(summary.invalidation_condition)html+='<section class="etf-block">'+heading('什么会改变判断')+
    '<div class="etf-prose">'+safe(summary.invalidation_condition)+'</div></section>';
  html+='<section class="etf-block"><p class="etf-source">'+
    (data.limitations||[]).map(safe).join(' · ')+
    '</p></section>';
  // ETF only: one visible outer card for *all* report sections.
  html+='</article>';
  document.getElementById('result').innerHTML=html;
}

let etfOverviewRenderToken = 0;

async function refreshMissingEtfOverview(report, token) {
  // ETF reports created by an older server version can be missing their fund
  // payload due to a stock-only public projection. The ETF overview is
  // independent of the LLM and must not use issuer valuation widgets.
  const code=String(report.stock_code||'').trim();
  if (!/^(SH|SZ)?[0-9]{6}$/i.test(code)) return;
  try {
    const response=await fetch('/api/etfs/overview?code='+encodeURIComponent(code), {
      headers: {Accept: 'application/json'}, cache: 'no-store',
    });
    if (!response.ok) throw new Error('HTTP '+response.status);
    const fresh=await response.json();
    if (token!==etfOverviewRenderToken || fresh.asset_type!=='etf' ||
        !fresh.etf_data) return;
    const original=report.etf_data||{}, incoming=fresh.etf_data||{};
    const combined={...original,...incoming,
      // The lighter overview has no sector/macro data. Keep it from a full
      // ETF report rather than erasing it on a holdings/quote refresh.
      macro_market:original.macro_market||incoming.macro_market,
      industry_cycle:original.industry_cycle||incoming.industry_cycle,
    };
    renderEtfResult({...report, asset_type:'etf',
      stock_name:fresh.stock_name||report.stock_name,
      stock_code:fresh.stock_code||report.stock_code,
      etf_data:combined,
    });
  } catch (error) {
    if (token===etfOverviewRenderToken && !(report.etf_data?.portfolio?.holdings||[]).length) {
      // Even during an outage, render a truthful ETF-specific notice.
      const holder=document.getElementById('etf-latest-top-ten');
      if (holder) {
        const notice=document.createElement('p');
        notice.className='etf-source';
        notice.textContent='最新持仓查询暂不可用，不能以个股图表或其他基金成分替代。';
        holder.appendChild(notice);
      }
    }
  }
}

function renderResult(report) {
  etfOverviewRenderToken += 1;
  if (isEtfInstrument(report)) {
    const hasDedicatedData=!!report.etf_data;
    const etfReport={...report, asset_type:'etf',
      etf_data:report.etf_data||{},
      // A legacy issuer-shaped report contains no reliable ETF research
      // evidence; its generic buy/sell summary must not be relabelled as one.
      summary:hasDedicatedData?report.summary:{
        stance:'', thesis_summary:'这是旧版报告，缺少ETF专属分析数据。请重新分析以获取宏观、行业周期和重仓股研究。',
        sections:{},
      },
    };
    renderEtfResult(etfReport);
    const fund=etfReport.etf_data?.fund||{};
    const holdings=etfReport.etf_data?.portfolio?.holdings||[];
    const price=etfReport.etf_data?.etf_price_position||{};
    if (!holdings.length || !(numericOrNull(fund.latest_price)>0 ||
                              numericOrNull(price.recent_close)>0)) {
      refreshMissingEtfOverview(etfReport, etfOverviewRenderToken);
    }
    return;
  }
  const el = document.getElementById('result');
  const isHistorical = report.research_mode === 'historical';
  // The top card holds the company name, small ticker, and the six key
  // sourced facts. No separate Live/date banner or redundant quote row.
  const displayCode=String(report.stock_code||'').trim();
  let displayName=String(report.stock_name||report.stock_code||'股票名称暂缺').trim();
  const embeddedTicker=displayName.match(/\s*[（(]\s*([A-Za-z]{0,3}\d{6})\s*[)）]\s*$/);
  if(embeddedTicker && displayCode &&
     embeddedTicker[1].replace(/\D/g,'')===displayCode.replace(/\D/g,'')){
    displayName=displayName.slice(0,embeddedTicker.index).trim()||displayCode;
  }
  let html='<section class="report-header-inline report-company-card" role="group" '+
    'aria-label="公司与关键行情数据">'+
    '<div class="report-company-identity"><h2 id="report-top">'+
      escapeHtml(displayName)+'</h2>'+
      (displayCode && displayCode!==displayName?
        '<span class="report-stock-code">'+escapeHtml(displayCode)+'</span>':'')+
    '</div>'+
    renderVerdictMarketStrip(report)+'</section>';
  if (isHistorical) {
    html += '<p class="report-historical-note">结论只允许使用该日及之前已公开的信息；当前知识不会用于补全历史缺口。</p>';
  }

  const summary = report.summary || {};
  const stanceClass = 'stance-' + (summary.stance || 'neutral');
  html += '<section class="result-summary" id="report-verdict"><h3>投资结论</h3>';
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
  // The investment thesis is authored by the model. Screening indicators
  // remain available to the research logic, not as a programmatic verdict.
  // The thesis is a reader-facing, potentially multi-paragraph explanation.
  // Never silently hide a long verdict merely because detailed analyses exist.
  // Old reports and new reports are both displayed in full.
  const thesis=String(summary.thesis_summary || '').trim();
  let thesisChipEvidenceInserted=false;
  if (thesis) {
    const renderedThesis=renderThesisWithInlineChipEvidence(thesis,report);
    html += renderedThesis.html;
    thesisChipEvidenceInserted=renderedThesis.chipEvidenceInserted;
  }
  html += '</section>';

  html += renderDashboardCharts(report);
  const hasDetailedAnalysis=Object.values(summary.dimension_analyses||{}).some(
    value=>typeof value==='string' && value.trim()
  );
  html += hasDetailedAnalysis?
    renderDimensionAnalysisSection(summary, report, !thesisChipEvidenceInserted):
    renderLegacyShareholderEvidence(report, !thesisChipEvidenceInserted);
  html += renderIndustryValuationLab(report);

  // Insert the TOC after report metadata but before the first research section.
  // Scan *rendered* markup so missing/legacy modules are omitted automatically.
  const chapterStart=html.indexOf('<section class="result-summary"');
  if(chapterStart>=0){
    const toc=renderReportToc(html);
    html=html.slice(0,chapterStart)+toc+html.slice(chapterStart);
  }
  el.innerHTML = html;
  activateIndustryValuationLab(report);
  if(typeof activateRoeDiscountLab==='function')activateRoeDiscountLab(report.valuation_model);
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
<script src="/assets/roe_discount.js"></script>
<script src="/assets/stock_autocomplete.js"></script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
