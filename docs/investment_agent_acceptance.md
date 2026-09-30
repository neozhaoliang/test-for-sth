# Investment Agent 验收指南

这个项目现在有两层验收，不要把它们混在一起。

## 1. 公共数据验收

用于验证不依赖雪球登录和 LLM 的公开数据管线：

```bash
python tools/live_public_acceptance.py
```

默认覆盖：

- 688981 中芯国际：technology
- 601088 中国神华：cyclical
- 600036 招商银行：financial
- 600519 贵州茅台：consumer_brand
- 600900 长江电力：stable_yield

可自定义：

```bash
python tools/live_public_acceptance.py 600309:cyclical 600036:financial
```

它检查：

- 同花顺 F10 基本面是否可取
- 申万行业是否可识别
- 盈利能力趋势
- 历史估值
- 巨潮一手公告
- A 股机构持股
- 分红 / 回购 / 资本配置
- 公司研究画像是否符合预期
- Evidence coverage / stale evidence

公共接口可能临时限流，因此它不放进 PR CI。

---

## 2. 完整报告验收

用于验证真正的投资 Agent 全链路：

```bash
python tools/live_report_acceptance.py
```

默认覆盖六类公司：

- 688981 中芯国际：科技 / 先进制造
- 601088 中国神华：周期 / 资源
- 600036 招商银行：金融
- 600519 贵州茅台：消费品牌
- 600900 长江电力：成熟高股息 / 公用事业
- 600309 万华化学：化工周期

这条路径会真实运行：

1. 公司 / 行业 / 财务 / 估值数据
2. 巨潮一手公告
3. 机构与资金结构
4. 宏观利率 / 汇率 / 周期数据
5. 雪球浏览器会话
6. 雪球多空辩论与情绪
7. 高可信用户上下文
8. 老木匠 / 军师祭咖啡知识库
9. 12 维 LLM 工具分析
10. contradiction reviewer
11. 三层 stance 综合
12. deterministic final-report validation

因此需要：

- 项目依赖已经安装
- LLM 环境变量已经配置
- 浏览器 / 雪球登录态可用
- 公共财经接口网络可访问

### 保存完整 JSON

```bash
python tools/live_report_acceptance.py --out data/acceptance/reports
```

会保存：

```text
data/acceptance/reports/
  688981.json
  601088.json
  ...
  _summary.json
```

### 自定义验收股票

```bash
python tools/live_report_acceptance.py \
  600036:financial \
  601088:cyclical \
  600309:cyclical
```

### 调整最低证据覆盖率

```bash
python tools/live_report_acceptance.py --min-coverage 0.70
```

---

## 3. PR CI 检查什么

`.github/workflows/investment-agent-tests.yml` 只做确定性测试：

- `uv lock --locked`
- Python `compileall`
- WebUI 内嵌 JavaScript `node --check`
- Evidence ledger
- 数据时点 / stale evidence
- 12 维 duplicate/conflict reviewer
- research profile
- report blocks
- final report contract validator
- management / capital allocation
- macro rates freshness
- commodity routing
- A-share ownership / unlock / quarterly changes
- CNINFO primary-source normalization
- policy-context routing
- annual-report R&D table parser

CI 不访问真实雪球、不调用真实 LLM，也不依赖实时公共财经接口。

---

## 4. 什么叫“完整报告通过”

`analysis.report_validation.validate_report()` 会检查：

- `stance`
- `company_quality_stance`
- `current_odds_stance`

三者都只能是：

- `bullish`
- `neutral`
- `bearish`

并且要求：

- 12 个维度分析全部存在
- 12 个维度评分全部存在
- 评分范围在 -10 到 +10
- `thesis_summary` 非空
- `core_counter_evidence` 非空
- `invalidation_condition` 非空
- confidence 不得高于 evidence coverage / profile readiness 在 reviewer penalty 后允许的上限

结构性错误会阻止报告返回，不会静默展示半份报告。

---

## 5. Historical / as-of 模式

现在已经开放严格的 `historical` 模式：

```text
mode = historical
as_of = YYYY-MM-DD
```

它的核心规则不是“数据描述哪个报告期”，而是：

```text
available_at <= as_of
```

即一条信息只有在截止日当天已经公开，才允许进入报告。

### 已严格 point-in-time 的核心来源

- 历史行情：只取 `as_of` 当日或之前最近交易日
- PE/PB 历史估值：序列截到 `as_of`
- 大盘 / 个股市场位置：序列截到 `as_of`
- 融资盘：只查询截止日前交易日
- 中美利率：观察值截到 `as_of`，freshness 也相对 `as_of` 判断
- 巨潮公告：发布日期 <= `as_of`
- 周期财报：读取当时已经发布的**原始巨潮 PDF 版本**
- 盈利趋势：仅从上述原始财报版本计算
- 股东户数：仅从当时已发布的定期报告原文提取
- A股特殊股东/机构线索：仅从当时已发布的前十大股东表提取
- 分红 / 回购：只记录截止日前已经发布的公告状态，不回填后来实施结果
- KOL 知识：按原文发布时间截断
- 高可信用户：只使用截止日前已发表且在当时已经完成验证的预测结果

### Historical 模式明确安全省略

以下数据如果没有可靠历史归档，**保持缺失**，绝不会用今天的数据代替：

- 今天的雪球个股讨论区、情绪和多空流
- 今天的财经新闻 / 政策快讯
- 依赖今天行业分类才能确定的行业周期路由
- 历史时点无法从原始财报稳定提取的研发团队、客户/供应商集中度等细字段

因此 historical 报告可能比 live 报告覆盖率低，这是设计结果，不是 bug。

### 双保险防穿越

1. Prompt 时间合同：模型被明确限制在 `as_of` 视角，禁止使用模型记忆和后见之明补未来事实。
2. 最终报告校验：任何 Evidence 的 `period / published_at / available_at` 晚于 `as_of`，报告直接失败。

`retrieved_at` 可以晚于 `as_of`：今天下载一份 2024 年当时已经公开的公告是允许的；
真正决定可用性的是该信息当时何时对投资者公开。

### 已执行的真实公网 Historical 验收

当前已在 GitHub Actions 上实际跑过 3 个 historical public-data case：

| 股票 / 截止日 | 最新可见财报 | 关键已核验指标 | 结果 |
|---|---|---|---|
| 招商银行 600036 @ 2024-06-30 | 2024Q1 / 2024-04-30 发布 | 营收864.17亿、归母380.77亿、OCF -12.08亿、ROE 16.08%、A股股东568,738 | PASS |
| 中国神华 601088 @ 2023-06-30 | 2023Q1 / 2023-04-29 发布 | 营收870.42亿、归母186.12亿、OCF 292.03亿、ROE 4.60% | PASS |
| 贵州茅台 600519 @ 2022-12-30 | 2022Q3 / 2022-10-17 发布 | 累计营收871.60亿、累计归母443.998亿、累计OCF 94.05亿、累计ROE 21.91%、股东145,225 | PASS |

三例均满足：

- 必需 source 可用
- `future_leaks=[]`
- 原始财报发布日期 <= cutoff
- 股东户数来自当时已公开定期报告
- 报告期选择正确

这轮真实验收还发现并修复了两个典型 PDF 语义 bug：

1. Q3 表格同时存在“本季度”和“年初至报告期末”，历史财务趋势必须取累计列；
2. 银行表格中 `ROE(%)(1)` 的 `(1)` 是脚注，不是 -1；但 `(1,208)` 仍必须识别为真实负数。

这两个场景现已加入 deterministic tests。

**注意：完整 LLM Historical E2E 仍未执行。**
GitHub Actions 当前没有检测到 `ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY`，
所以严格 E2E job 会明确跳过并上传 skipped marker，不能把 job success 误读为 LLM 报告已跑通。

### 历史端到端验收

```bash
python tools/live_historical_acceptance.py
```

默认测试：

- 招商银行 @ 2024-06-30
- 中国神华 @ 2023-06-30
- 贵州茅台 @ 2022-12-30

验收会检查：

- 无 `available_at > as_of` 的未来证据
- 不启动/注入今天的雪球讨论区数据
- 12 维分析与评分完整
- 最终合同校验通过
- snapshot 已保存且哈希完整

保存后的 frozen snapshot 可以通过 `analysis/snapshot_replay.py` 重放，只重跑
LLM synthesis / reviewer，不重新抓今天的数据。因此可以公平比较不同模型或 Prompt。


---

## 6. Snapshot：冻结输入，公平比较模型/Prompt

Live 报告可以选择保存标准研究快照。

完整报告验收时：

```bash
python tools/live_report_acceptance.py \
  --snapshot-root data/investment_snapshots
```

每个快照目录现在包含：

```text
manifest.json
request.json
research_inputs.json
evidence.json
report.json
```

其中：

- `request.json`：当时的研究请求与 as-of；
- `research_inputs.json`：**最终 LLM 综合之前**的冻结研究输入；
- `evidence.json`：证据账本；
- `report.json`：当时生成的最终报告；
- `manifest.json`：Prompt 版本、Git SHA、模型标识、source vintages 和 SHA-256。

### 校验快照是否被改过

```bash
python tools/snapshot_cli.py verify \
  data/investment_snapshots/600036/2026-09-29/<snapshot_id>
```

如果任何冻结文件被修改，文件哈希或语义哈希会失败。

### 查看 manifest

```bash
python tools/snapshot_cli.py show <snapshot_path>
```

### 查看冻结的 pre-synthesis 输入

```bash
python tools/snapshot_cli.py inputs <snapshot_path>
```

### 用当前模型 / 当前 Prompt 离线重放同一份数据

```bash
python tools/snapshot_cli.py replay <snapshot_path>
```

Replay 的边界非常重要：

- 不重新抓行情；
- 不重新抓巨潮；
- 不重新抓机构持仓；
- 不重新访问雪球；
- 不重新读取新的 KOL 内容；
- 只重新运行 12 维综合、reviewer、评分与最终合同校验。

因此它适合比较：

```text
同一份证据
Prompt v12 vs Prompt v13

同一份证据
Model A vs Model B
```

而不把“数据变了”和“模型变了”混在一起。

**Snapshot replay 不是 historical backtest。**

一个 2026-09-29 保存的 live snapshot 即使以后重放，也仍然代表
“2026-09-29 当时冻结的信息”，不能改名冒充 2024 年快照。

---

## 7. as-of 数据源状态

接口：

```text
GET /api/research/time-capabilities
```

当前 required source 已全部具备严格 point-in-time 路径，`historical_readiness().ready == True`。其中包括：

- 历史价格 / quote
- PE/PB 历史估值
- 巨潮公告与周期报告 filing calendar
- 原始巨潮 PDF 财报与盈利趋势
- 股东户数
- 分红 / 回购事件账本
- A股公开股东结构
- 市场/指数价格环境
- 融资余额
- 中美利率
- KOL 知识发布时间过滤
- 历史高可信用户评分的“预测时间 + 验证时间”双截断

以下 source 仍是 **optional safe omission**，不会阻断 Historical，也绝不会回填今天的数据：

- `industry_cycle`：历史行业分类缺少可靠归档时不路由
- `policy_news`：当前新闻流不是历史归档
- `xueqiu_live`：不读取今天讨论区/情绪/多空流

### Historical public source corpus

公网 point-in-time 验收现在可以同步冻结原始 source bundle：

```bash
python tools/historical_public_acceptance.py \
  600036@2024-06-30 \
  --output artifacts/600036.json \
  --corpus-root artifacts/historical_source_corpus
```

bundle 包含 `sources.json`、`diagnostics.json` 和 `manifest.json`。manifest 记录 source vintages、Git SHA、语义 SHA-256、文件 SHA-256 和 future-date 检查；bundle 不含任何 LLM stance/评分，因此只是可复现的历史数据输入，不是完整研究报告。

GitHub Actions 的 `historical-public-data` matrix 已会把这些 source corpus 与验收 JSON/日志一起上传为 artifact。

### 仍未完成的唯一关键验收

完整 LLM Historical E2E 仍取决于仓库 Actions 是否配置 `ANTHROPIC_AUTH_TOKEN` 或 `ANTHROPIC_API_KEY`。未配置时 job 会明确写入 skipped marker；不能把 workflow success 当成 LLM 报告已经跑通。


---

## 8. 无 LLM 的 Historical research-input E2E

为了把“公网数据层通过”和“完整 LLM 报告通过”之间的空档进一步缩小，现在增加：

```bash
python tools/historical_research_input_acceptance.py \
  600036@2024-06-30 \
  --snapshot-root artifacts/research_input_snapshots \
  --output artifacts/historical-research-inputs.json
```

这条链路会运行与 `generate_report()` 相同的 Historical 数据采集、证据账本、research profile 和最终 report packaging，只把最后的 LLM synthesis 替换为确定性中性占位符用于通过结构校验。占位结论**不会写入 snapshot**。

保存的是 `snapshot_kind=research_inputs_only`：

- `request.json`
- `research_inputs.json`
- `evidence.json`
- `manifest.json`

没有 `report.json`，也没有 stance、维度评分或 LLM 结论。以后配置真实模型后，可以直接对这个 snapshot 运行 `snapshot_cli.py replay`，从同一份冻结历史输入生成结论。

Historical 模式下知识库也已经改成模型无关：只读取内容哈希匹配的既有蒸馏缓存，再按发布时间 `<= as_of` 截断；不会为了生成历史输入而用今天的模型重新蒸馏旧原文，也不会用 LLM 做 pre-synthesis 相关性筛选。

GitHub Actions 现在额外运行一个 `Historical research-input E2E (no LLM)` job（当前先用招商银行 600036 @ 2024-06-30 作为代表案例）并上传可重放 input snapshot。
