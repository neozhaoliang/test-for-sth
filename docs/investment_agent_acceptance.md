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

会返回每一路数据的 temporal capability。

目前已经具备 point-in-time 截断能力的路径包括：

- 历史价格 / quote
- PE/PB 历史估值
- 巨潮公告
- 巨潮周期报告 filing calendar
- 市场/指数价格环境
- 融资余额
- 中美利率
- KOL 知识发布时间过滤
- 历史高可信用户评分的“预测时间 + 验证时间”双截断
- 股东户数：已经按**公告日期**做日期截断，但第三方历史表可能被后来更正，因此 capability 为 `snapshot_only`
- 分红 / 回购：已经按公告日期过滤明显未来事件，但聚合表中的进度/金额可能后来更新，因此 capability 为 `snapshot_only`

也就是说，这两类数据已经具备“减少明显穿越”的适配器，但**还不能仅凭今天重新查询第三方历史表就宣称严格 point-in-time**。严格历史回测要使用当时冻结的 Snapshot，或直接解析当时版本的原始公告。

仍然阻塞完整 historical mode 的数据源会继续显示在
`blocking_sources` 中。

在 blocking sources 没有清零之前，
`mode=historical` 会返回明确错误，不会偷偷回退成 live 数据。
