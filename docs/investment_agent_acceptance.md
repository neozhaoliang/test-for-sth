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

## 5. 数据时点原则

当前 Agent 是 **live/current report**。

Evidence ledger 已经记录：

- 数据来源
- source tier
- fact / derived / opinion
- as-of
- freshness / stale 状态

但这不等于历史回测已经支持 `as_of`。

真正历史报告必须保证：

- 行情不穿越
- 财报不穿越
- 公告不穿越
- 机构持仓不穿越
- 雪球观点不穿越
- KOL 知识不使用未来内容
- 高可信用户评分不能用未来验证结果

在这些数据源都支持日期截断之前，禁止把 live report 接口伪装成历史回测接口。


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
- 股东户数：按**公告日期**截断，不按统计期提前使用
- 分红 / 回购：按公告可得日期截断

仍然阻塞完整 historical mode 的数据源会继续显示在
`blocking_sources` 中。

在 blocking sources 没有清零之前，
`mode=historical` 会返回明确错误，不会偷偷回退成 live 数据。
