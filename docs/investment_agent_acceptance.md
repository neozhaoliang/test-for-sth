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
