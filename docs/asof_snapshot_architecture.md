# as-of / Snapshot 架构

## 目标

历史研究模式必须满足一个硬约束：

> 任何进入报告的证据，都必须证明它在 `request.as_of` 当天或之前已经可获得。

不能只看“报告期”或“数据日期”。真正决定能否用于历史回测的是：

```text
available_at <= as_of
```

例如 2024 年年报的报告期是 2024-12-31，但如果 2025-03-28 才公告，
那么 `as_of=2025-01-15` 时禁止使用。

---

## ResearchRequest

`analysis/research_context.py` 定义：

```python
ResearchRequest(
    stock_code="600036",
    mode="live",            # live | historical
    as_of=None,
    save_snapshot=False,
    snapshot_root="data/investment_snapshots",
)
```

### live

- `as_of` 固定为今天
- 继续使用当前实时/最新数据
- 可选择保存 Snapshot

### historical

- 必须显式传过去日期
- 当前仍会被能力表阻断
- 只有当全部关键数据源都变成 point-in-time safe 后才允许真正生成报告

这是故意的安全设计，避免“部分历史数据 + 部分今天数据”的伪回测。

---

## 当前时间能力矩阵

机器可读版本位于：

```python
analysis.research_context.SOURCE_TEMPORAL_CAPABILITIES
```

当前已完成：

| Source | 状态 | 说明 |
|---|---|---|
| realtime_quote | AS_OF_SAFE | 历史模式使用 `as_of` 当日或之前最后一个交易日收盘价，并用前一交易日计算涨跌幅 |
| market_context | AS_OF_SAFE | 个股和主要指数都先截断到 `as_of`，再计算 YTD/半年/52周/历史区间 |
| valuation_history | AS_OF_SAFE | PE/PB 历史序列在本地按 `date <= as_of` 截断 |
| primary_evidence | AS_OF_SAFE | 巨潮公告查询以 `as_of` 为截止日期，并再次本地过滤发布日期 |
| macro_rates | AS_OF_SAFE | Fed目标利率、美债10Y、LPR先截到 `as_of`，再相对该日判断新鲜度 |
| margin | AS_OF_SAFE | 交易所融资明细从 `as_of` 向前回溯最近交易日 |
| kol_knowledge | AS_OF_SAFE | 雪球/B站原始发布时间统一为 epoch 秒，历史模式排除未来和未知时间条目 |

当前仍阻断 historical：

| Source | 原因 |
|---|---|
| fundamentals | F10 当前会看到最新已发布财报 |
| profitability | 财务指标尚未按发布日期截断 |
| shareholder_count | 有历史记录，但尚未严格处理披露可用日期 |
| dividend_buyback | 事件尚未统一按公告可用日过滤 |
| a_share_structure | 机构季度持仓必须按披露日而不是季度末判断 |
| industry_cycle | 商品/运价/行业数据尚未统一接受 as_of |
| xueqiu_live | 当前浏览器抓的是今天讨论区 |
| candidate_credibility | 当前命中率可能包含 as_of 之后才验证出来的结果 |

可通过 API 查看实时能力：

```text
GET /api/research/time-capabilities
```

---

## Evidence 时间字段

Evidence 现在保留五种时间语义：

```text
as_of
period
published_at
available_at
retrieved_at
```

含义：

- `period`：这条数据描述哪个报告期/观察期
- `published_at`：原始信息正式发布时间
- `available_at`：普通投资者最早可获得时间
- `retrieved_at`：本次 Agent 实际抓取时间
- `as_of`：旧接口兼容字段，逐步迁移到上面更精确的字段

历史模式最终以 `available_at` 为核心判断。

---

## Snapshot

`analysis/snapshot_store.py` 保存不可变研究快照。

目录结构：

```text
data/investment_snapshots/
  600036/
    2026-09-29/
      600036_2026-09-29_<hash>/
        report.json
        evidence.json
        manifest.json
      latest.json
```

`manifest.json` 包含：

- snapshot_id
- stock code / name
- mode
- as_of
- captured_at
- prompt_version
- Git SHA（GitHub Actions 环境可自动写入）
- LLM model（环境变量可识别时）
- report SHA-256
- evidence SHA-256
- 每个证据类别的最新 `available_at/as_of`
- stale evidence 类别

Snapshot 的用途不是“缓存提速”，而是：

1. 固定当时到底看到了哪些信息
2. 防止 API 后续改字段导致回测结果漂移
3. 用同一份证据比较不同模型 / Prompt
4. 审计报告为什么会得出当时结论

---

## 保存 live Snapshot

Python：

```python
from analysis.research_context import ResearchRequest
from analysis.report import generate_report

request = ResearchRequest(
    stock_code="600036",
    save_snapshot=True,
)
report = await generate_report("600036", request=request)
```

API：

```json
POST /api/analyze

{
  "stock_code": "600036",
  "mode": "live",
  "save_snapshot": true
}
```

完整真实股票验收时批量保存：

```bash
python tools/live_report_acceptance.py \
  --snapshot-root data/investment_snapshots
```

---

## historical API 当前行为

如果现在请求：

```json
{
  "stock_code": "600036",
  "mode": "historical",
  "as_of": "2024-06-30"
}
```

API 会返回 HTTP 409，并列出：

- 已安全的数据源
- 阻断的数据源
- 每个数据源为什么尚未 point-in-time safe

不会自动退化成 live，也不会用今天的数据补空缺。

---

## 后续迁移顺序

建议按下面顺序逐步把能力表从 LIVE_ONLY 改成 AS_OF_SAFE：

1. 历史行情 / market context
2. 财报 + profitability（按公告日）
3. 分红 / 回购 / 再融资
4. 股东户数
5. 融资余额
6. 机构持仓 / 公募 / ETF / 解禁
7. 宏观利率 vintage
8. 商品 / 运价 / 行业周期
9. KOL 知识发布日期
10. 雪球讨论与用户发言
11. 历史 candidate credibility

最后三项最难，也是最容易产生未来泄漏的部分。

在全部关键来源完成之前，`historical_readiness().ready` 必须保持 False。
