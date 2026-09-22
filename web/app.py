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
import sys
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from analysis.knowledge_base import ensure_loaded as ensure_knowledge_base_loaded
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
    await ensure_knowledge_base_loaded()


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


class CrawlBiliOpusRequest(BaseModel):
    creator_id: str


class CrawlTaskResponse(BaseModel):
    task_id: str


def _crawl_task_is_running() -> bool:
    return any(t["status"] == "running" for t in _crawl_tasks.values())


async def _run_crawler_subprocess(task_id: str, cmd: list) -> None:
    _crawl_tasks[task_id]["status"] = "running"
    log_lines: list = _crawl_tasks[task_id]["log_lines"]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_PROJECT_ROOT),
        )
        _crawl_tasks[task_id]["process"] = process
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            log_lines.append(line.decode("utf-8", errors="replace").rstrip())
            if len(log_lines) > _CRAWL_LOG_TAIL_LINES:
                del log_lines[: len(log_lines) - _CRAWL_LOG_TAIL_LINES]
        returncode = await process.wait()
        _crawl_tasks[task_id]["status"] = "done" if returncode == 0 else "failed"
    except Exception as e:
        utils.logger.error(f"[web.app] 抓取任务 {task_id} 失败: {e}")
        log_lines.append(f"启动/运行抓取进程失败: {e}")
        _crawl_tasks[task_id]["status"] = "failed"


async def _start_crawl_task(cmd: list) -> str:
    async with _crawl_lock:
        if _crawl_task_is_running():
            raise HTTPException(status_code=400, detail="已有抓取任务在运行，请稍候")
        task_id = str(uuid.uuid4())
        _crawl_tasks[task_id] = {"status": "pending", "log_lines": [], "process": None}
        asyncio.create_task(_run_crawler_subprocess(task_id, cmd))
        return task_id


@app.post("/api/crawl/xueqiu", response_model=CrawlTaskResponse)
async def crawl_xueqiu(req: CrawlXueqiuRequest) -> CrawlTaskResponse:
    user_id = req.user_id.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id 不能为空")

    cmd = [
        sys.executable, "main.py",
        "--platform", "xueqiu",
        "--lt", "qrcode",
        "--type", "creator",
        "--creator_id", user_id,
        "--update", "true" if req.incremental else "false",
    ]
    task_id = await _start_crawl_task(cmd)
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


@app.post("/api/digest/rebuild", response_model=CrawlTaskResponse)
async def rebuild_digest() -> CrawlTaskResponse:
    """从全部历史验证记录全量重建观点摘录文件 (提炼按内容哈希缓存)。"""
    task_id = await _start_crawl_task([sys.executable, "digest_run.py"])
    return CrawlTaskResponse(task_id=task_id)


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

<div class="crawl-section">
  <h3>数据抓取 / 观点回测</h3>

  <p class="crawl-hint">抓取/更新雪球用户的全部发帖与回复 (公开数据，无需登录)</p>
  <div class="crawl-row">
    <input id="xueqiuUserId" placeholder="雪球用户 ID 或主页 URL，如 1263638109" />
    <button id="crawlXueqiuBtn">抓取/更新</button>
  </div>

  <p class="crawl-hint">回测某用户的发言: LLM 提取观点与预测 -> 用预测发布后的真实股价验证 -> 验证正确的观点与逻辑写入摘要文件 (耗时长，完成后自动更新摘要)</p>
  <div class="crawl-row">
    <input id="backtestUserId" placeholder="雪球用户 ID 或主页 URL，如 1263638109" />
    <button id="backtestBtn">回测并更新摘要</button>
  </div>

  <p class="crawl-hint">全量重建观点摘要文件 (从全部历史验证记录提炼，用于回测后或数据修复)</p>
  <div class="crawl-row">
    <button id="digestBtn">重建全部摘要</button>
  </div>

  <p class="crawl-hint">抓取/更新 B 站专栏作者的全部图文 (需要登录，首次抓取请留意弹出的浏览器窗口扫码)</p>
  <div class="crawl-row">
    <input id="biliCreatorId" placeholder="B站 UID 或空间 URL" />
    <button id="crawlBiliBtn">抓取/更新</button>
  </div>

  <div class="crawl-status" id="crawlStatus"></div>
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
    html += '<div style="margin-top:6px"><b>股东户数 vs 同期股价 (判断股价暴涨是否伴随筹码派发):</b><ul>' +
      series.slice(0, 8).map(p => '<li>' + escapeHtml(String(p.period)) + ': 股东户数 ' +
        p.holders + ' 户, 股价 ' + p.price + '</li>').join('') + '</ul></div>';
  }

  const risks = (f.self_disclosed_risks || '').trim();
  html += '<div class="risk-block"><b>公司自述的风险 (原文):</b> ' +
    (risks ? escapeHtml(risks) : '暂缺 (该公司本期报告的董事会经营评述中没有独立的风险小节)') + '</div>';
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

  const pt = report.profitability_trend;
  if (pt) {
    html += '<div class="evidence-block"><b>盈利能力与成本弹性:</b><ul>' +
      [pt.gross_margin_trend_note, pt.net_margin_trend_note, pt.roe_trend_note, pt.debt_ratio_trend_note]
        .filter(Boolean).map(n => '<li>' + escapeHtml(n) + '</li>').join('') +
      '</ul></div>';
  } else {
    html += '<div class="evidence-block missing"><b>盈利能力与成本弹性:</b> 暂缺 (本次未能取到财务指标数据)</div>';
  }

  const div = report.dividend_history || [];
  if (div.length) {
    html += '<div class="evidence-block"><b>历史分红:</b><ul>' +
      div.map(d => '<li>' + escapeHtml(d.announce_date) + ': 每10股派息 ' + d.dividend_per_10_shares + ' 元 (' + escapeHtml(d.progress) + ')</li>').join('') +
      '</ul></div>';
  } else {
    html += '<div class="evidence-block missing"><b>历史分红:</b> 暂缺</div>';
  }

  const bb = report.buyback_history || [];
  if (bb.length) {
    html += '<div class="evidence-block"><b>历史回购:</b><ul>' +
      bb.map(b => '<li>' + escapeHtml(b.announce_date) + ': 计划金额区间 [' + b.planned_amount_range[0] + ', ' + b.planned_amount_range[1] +
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

  html += renderFundamentalsBlock(report.fundamentals);
  html += renderXueqiuBlock(report.xueqiu_stock);

  html += '</div>';
  return html;
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
  if (summary.lynch_category && lynchCategoryLabel(summary.lynch_category)) {
    html += '<span class="lynch-category">' + escapeHtml(lynchCategoryLabel(summary.lynch_category)) + '</span>';
  }
  if (report.prompt_version) {
    html += ' <span class="prompt-version">(prompt ' + escapeHtml(report.prompt_version) + ')</span>';
  }
  html += '</p>';
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

  html += renderEvidenceSection(report);

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
}

document.getElementById('submitBtn').addEventListener('click', submitAnalysis);
document.getElementById('crawlXueqiuBtn').addEventListener('click', () => submitCrawl('xueqiu'));
document.getElementById('crawlBiliBtn').addEventListener('click', () => submitCrawl('bili'));
document.getElementById('backtestBtn').addEventListener('click', () => submitCrawl('backtest'));
document.getElementById('digestBtn').addEventListener('click', () => submitCrawl('digest'));
renderWatchlist();

let crawlPollTimer = null;

async function submitCrawl(platform) {
  const statusEl = document.getElementById('crawlStatus');
  const logEl = document.getElementById('crawlLog');
  let url, body;

  if (platform === 'xueqiu') {
    const userId = document.getElementById('xueqiuUserId').value.trim();
    if (!userId) return;
    url = '/api/crawl/xueqiu';
    body = { user_id: userId };
  } else if (platform === 'backtest') {
    const userId = document.getElementById('backtestUserId').value.trim();
    if (!userId) return;
    url = '/api/backtest/xueqiu';
    body = { user_id: userId };
  } else if (platform === 'digest') {
    url = '/api/digest/rebuild';
    body = {};
  } else {
    const creatorId = document.getElementById('biliCreatorId').value.trim();
    if (!creatorId) return;
    url = '/api/crawl/bili_opus';
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

  if (data.status === 'done' || data.status === 'failed') {
    clearInterval(crawlPollTimer);
  }
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
