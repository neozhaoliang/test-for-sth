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
- 所有 required source 必须是 point-in-time safe
- 没有可靠历史归档的 optional source 必须安全省略，不能回填今天数据
- 当前 required source 已全部通过 readiness，严格 historical 报告已经开放

安全设计仍然不变：宁可缺失，也不允许“部分历史数据 + 部分今天数据”的伪回测。

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
| candidate_credibility | AS_OF_SAFE | Wilson score 只使用 as-of 前发布且 as-of 前已经验证的预测 |

当前 historical 的安全省略项：

| Source | 状态 | 说明 |
|---|---|---|
| industry_cycle | LIVE_ONLY / optional | 商品与运价本身可按 as-of 截断，但历史行业分类缺少可靠归档；无法确认时不路由 |
| policy_news | LIVE_ONLY / optional | 当前财经新闻流不是历史归档，historical 模式直接省略 |
| xueqiu_live | LIVE_ONLY / optional | 不读取今天的讨论区、情绪或多空流，缺失会降低覆盖率但不会回填 |

以下 required source 已经是严格 `AS_OF_SAFE`：财报/盈利能力、股东户数、分红回购、A股公开持股结构、行情、估值、融资盘、宏观利率、巨潮公告、KOL 与候选用户历史验证。

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
        request.json
        research_inputs.json
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

现在请求：

```json
{
  "stock_code": "600036",
  "mode": "historical",
  "as_of": "2024-06-30"
}
```

会进入严格 Historical 主链。系统先执行 readiness 检查：required source 必须全部 point-in-time safe；optional live-only source 则保持缺失。

任何 Evidence 的 `period / published_at / available_at` 晚于 `as_of` 都会触发最终 hard-fail。系统不会自动退化成 live，也不会用今天的数据补空缺。

---

## 下一阶段

required source 的 point-in-time 改造已经完成，`historical_readiness().ready == True`。下一阶段重点不再是“解锁 Historical”，而是提高可复现性与覆盖率：

1. 扩大不同年份、行业和报告期的 Historical 公网验收矩阵
2. 持续修复原始财报 PDF 的版式/语义解析差异
3. 建立历史 source corpus，冻结公网原始 payload、source vintage 与哈希
4. 在有 LLM 密钥后保存完整 `research_inputs.json` snapshot，并用 replay 比较不同 Prompt/模型
5. 再考虑真正的历史新闻归档、历史雪球讨论快照、ETF 历史成份/份额等 optional source

其中第 3 项已经由 `analysis/historical_corpus.py` 与
`tools/historical_public_acceptance.py --corpus-root ...` 开始落地。

---

## Frozen-input Prompt / Model A-B 对比

有了 `research_inputs_only` snapshot 后，不需要重新抓任何公网数据，就可以在不同
Prompt 或不同模型环境下分别 replay：

```bash
python tools/snapshot_cli.py replay <snapshot_path> \
  --out artifacts/replay-a.json

# 切换 Prompt / 模型配置后，对同一 snapshot 再跑一次
python tools/snapshot_cli.py replay <snapshot_path> \
  --out artifacts/replay-b.json
```

然后做确定性差异比较：

```bash
python tools/snapshot_cli.py compare \
  artifacts/replay-a.json \
  artifacts/replay-b.json \
  --out artifacts/replay-diff.json
```

比较器不会调用 LLM，也不会访问任何数据源。它会先验证两份结果来自同一个冻结
snapshot / stock / as-of，再比较：

- 三层 stance 是否变化
- confidence 的变化
- 各维度评分 `B - A` 的差值
- 发生变化的维度数
- 最大绝对维度分差

这使 Prompt / Model A-B 的因果边界更清晰：**输入数据完全相同，只比较综合阶段本身。**

## 外部模型 / Provider-agnostic replay

冻结输入回放现在不要求模型 SDK 必须存在于本仓库。外部模型只需要输出一个标准 JSON：

```json
{
  "provider": "openai-chatgpt",
  "model": "GPT-5.6 Sol",
  "prompt_version": "my-external-prompt-v1",
  "summary": {
    "lynch_category": "stalwart",
    "stance": "neutral",
    "company_quality_stance": "bullish",
    "current_odds_stance": "neutral",
    "confidence": 0.6,
    "thesis_summary": "...",
    "core_counter_evidence": "...",
    "invalidation_condition": "...",
    "risk_notes": "...",
    "dimension_scores": [],
    "dimension_analyses": {}
  }
}
```

然后对冻结 snapshot 执行：

```bash
PYTHONPATH=. python tools/snapshot_cli.py replay-external \
  <snapshot_path> \
  --synthesis external-synthesis.json \
  --out artifacts/replay-external.json
```

边界刻意保持清晰：

- 外部模型只负责 `StructuredSummary`，不接管数据采集；
- `dimension_analyses` 会重新经过项目自己的 deterministic reviewer；
- confidence 仍由 evidence coverage、research profile readiness 和 reviewer penalty 统一封顶；
- snapshot SHA-256 完整性和 historical future-leak 校验仍由项目执行；
- 最终 `validate_report()` 失败时 replay 直接失败；
- provider / model / 外部 prompt version 会写入 `validation.replay.external_synthesis` 作为审计元数据。

因此 Anthropic、OpenAI、Gemini、DeepSeek、本地模型或人工生成的兼容 JSON 都可以复用同一条 Historical / Snapshot / Reviewer / Validator 主链，而不需要修改底层研究数据架构。

---

## Historical public source corpus

完整 LLM Historical E2E 依赖模型密钥，但公网 point-in-time 数据本身可以先独立冻结：

```bash
python tools/historical_public_acceptance.py \
  600036@2024-06-30 \
  --output artifacts/600036.json \
  --corpus-root artifacts/historical_source_corpus
```

每个 bundle 包含：

- `sources.json`：本次实际获取的 quote / valuation / market / macro / 巨潮财报 / 股东结构等原始归一化 payload
- `diagnostics.json`：各 source 耗时、错误、验收 warning/failure
- `manifest.json`：as-of、Git SHA、source vintages、语义 SHA-256、文件 SHA-256、future-date 检查

它不包含 stance、评分或任何 LLM 结论，因此不能冒充完整报告 snapshot。它的用途是固定“这次公网验收到底看到了什么”，为 parser 回归和后续 snapshot corpus 提供可复现输入。

下载 bundle 后可以直接检查：

```bash
python tools/historical_corpus_cli.py verify <bundle_path>
python tools/historical_corpus_cli.py show <bundle_path>
python tools/historical_corpus_cli.py sources <bundle_path>
python tools/historical_corpus_cli.py diagnostics <bundle_path>
```
