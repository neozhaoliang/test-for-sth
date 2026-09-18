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
import uuid
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from analysis.candidates import list_supported_stocks
from analysis.report import generate_report
from tools.utils import utils

app = FastAPI(title="股票投资助手")

_tasks: Dict[str, Dict] = {}


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
    stock_code = req.stock_code.strip()
    if not stock_code:
        raise HTTPException(status_code=400, detail="stock_code 不能为空")

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


@app.get("/api/stocks")
async def get_supported_stocks() -> Dict:
    return {"stocks": list_supported_stocks()}


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
  .risk-block { margin: 12px 0; padding: 10px 12px; background: #fff6e5; border-left: 4px solid #e0a020; border-radius: 4px; }
  .credibility-note { color: #555; }
  .prompt-version { color: #aaa; font-size: 12px; }
  .disclaimer { margin-top: 32px; padding-top: 12px; border-top: 1px solid #eee; color: #999; font-size: 12px; }
  .supported-stocks { margin: 12px 0; }
  .supported-stocks p { margin: 0 0 6px; color: #666; font-size: 13px; }
  .stock-chip { display: inline-block; padding: 4px 10px; margin: 0 6px 6px 0; border: 1px solid #ccc; border-radius: 14px; font-size: 13px; cursor: pointer; background: #f7f7f7; }
  .stock-chip:hover { background: #eaf1fd; border-color: #1a73e8; }
  .stock-chip .record-count { color: #999; margin-left: 4px; }
</style>
</head>
<body>
<header>
  <h1>股票投资助手</h1>
  <p>输入股票代码，查看历史高可信度用户的观点与最新发言综合分析。</p>
</header>
<div class="search-bar">
  <input id="stockCode" placeholder="SH603408" />
  <button id="submitBtn">分析</button>
</div>
<div class="supported-stocks">
  <p>当前有历史验证数据支持的股票 (数据为后台离线抓取分析，非全市场覆盖，点击可快速填入):</p>
  <div id="stockChips">加载中...</div>
</div>
<div id="status"></div>
<div id="result"></div>
<p class="disclaimer">以上内容基于历史数据与 AI 生成，仅供参考，不构成投资建议。</p>

<script>
let pollTimer = null;

async function loadSupportedStocks() {
  const el = document.getElementById('stockChips');
  try {
    const res = await fetch('/api/stocks');
    if (!res.ok) throw new Error('请求失败');
    const { stocks } = await res.json();
    if (!stocks || !stocks.length) {
      el.textContent = '暂无支持的股票';
      return;
    }
    el.innerHTML = stocks.map(s =>
      '<span class="stock-chip" data-code="' + escapeHtml(s.stock_code) + '">' +
      escapeHtml(s.stock_name || s.stock_code) + ' (' + escapeHtml(s.stock_code) + ')' +
      '<span class="record-count">' + s.record_count + ' 条记录</span></span>'
    ).join('');
    el.querySelectorAll('.stock-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.getElementById('stockCode').value = chip.getAttribute('data-code');
        submitAnalysis();
      });
    });
  } catch (e) {
    el.textContent = '加载失败';
  }
}

async function submitAnalysis() {
  const stockCode = document.getElementById('stockCode').value.trim();
  if (!stockCode) return;
  document.getElementById('result').innerHTML = '';
  document.getElementById('status').textContent = '提交中...';
  if (pollTimer) clearInterval(pollTimer);

  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stock_code: stockCode }),
  });
  if (!res.ok) {
    document.getElementById('status').textContent = '提交失败: ' + res.status;
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

  if (data.status === 'done') {
    clearInterval(pollTimer);
    renderResult(data.result);
  } else if (data.status === 'failed') {
    clearInterval(pollTimer);
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
  if (report.prompt_version) {
    html += ' <span class="prompt-version">(prompt ' + escapeHtml(report.prompt_version) + ')</span>';
  }
  html += '</p>';
  if (summary.thesis_summary) {
    html += '<div class="thesis-block"><b>关键论据:</b> ' + escapeHtml(summary.thesis_summary) + '</div>';
  }
  if (summary.risk_notes) {
    html += '<div class="risk-block"><b>风险提示:</b> ' + escapeHtml(summary.risk_notes) + '</div>';
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
}

document.getElementById('submitBtn').addEventListener('click', submitAnalysis);
loadSupportedStocks();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
