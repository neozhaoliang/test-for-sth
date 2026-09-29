# Investment Agent v2 architecture

> 本文描述 `feature/investment-agent-evidence-v2` 的研究主链。目标是让股票研究
> **可追溯、可审计、能表达不确定性**，而不是把更多数据无差别塞进一个超长 prompt。

## 1. 数据流

```text
用户输入股票
  ↓
report.py                     # orchestration only
  ├─ 一手/公开数据采集
  ├─ 雪球会话与候选观点
  └─ KOL 知识检索
  ↓
evidence.py                   # normalize evidence + source tier + freshness
  ↓
research_profile.py           # company archetype + priority evidence
  ↓
report_blocks.py              # evidence → readable prompt blocks
  ↓
report_synthesis.py           # 12 dimensions → review → final synthesis
  ├─ report_contract.py       # prompt/tool/output contract
  └─ reviewer.py              # duplicate factors / conflicts / weak links
  ↓
AnalysisReport
  ↓
web/app.py                    # charts + evidence ledger + readable report
```

## 2. 模块职责

### `analysis/report.py`

只负责：

- 标的解析后的数据采集与并发编排；
- 知识库相关性筛选；
- 建立 `AnalysisInputs`；
- 生成 evidence ledger / research profile；
- 调用 synthesis；
- 组装最终 `AnalysisReport`。

**不要再把 Prompt 大段文字、图表、网页解析规则或新数据源实现塞进这里。**

### `analysis/evidence.py`

统一研究证据契约：

- `fact`：原始披露/可直接核验的事实；
- `derived`：可从原始数据复算得到的派生指标；
- `opinion`：雪球/B站/KOL/研报式观点。

来源等级：

- S：交易所、公司公告、巨潮官方年报等；
- A：权威官方/高可复核汇总；
- B：可复算第三方数据/F10/行情平台；
- C：社交媒体和观点。

同时负责：

- 12 个研究方向覆盖率；
- 时点型证据 freshness；
- 过旧证据不再计入“当前覆盖”。

### `analysis/research_profile.py`

确定性公司研究画像，不负责看多/看空：

- technology：科技/先进制造；
- cyclical：周期/资源；
- financial：金融；
- consumer_brand：消费品牌；
- stable_yield：成熟高股息/公用事业；
- general：综合型。

所有股票仍跑 12 个研究方向，但不同画像有不同的：

- priority dimensions；
- priority evidence；
- analysis rules；
- readiness。

最终 confidence 不得高于重点证据准备度。

### `analysis/report_blocks.py`

只做格式化，不做网络请求，不下投资结论。

典型函数：

- 历史估值块；
- 研发团队块；
- A股资金结构块；
- 财务/营运资金块；
- 巨潮公告块；
- 管理层/资本配置块。

这层需要行为回归测试，防止某个数据块串进无关章节。

### `analysis/report_contract.py`

唯一的 LLM “研究合同”位置：

- prompt version；
- 硬规则；
- 12 个 analysis tools；
- submit_report schema；
- dimension score schema；
- review rewrite contract。

修改研究规则时优先改这里，不要在采集层散落 Prompt 文本。

### `analysis/report_synthesis.py`

负责：

1. 强制执行 12 个研究方向；
2. 工具调用失败时的 JSON fallback；
3. deterministic reviewer；
4. 对冲突/弱证据做二次综合；
5. 生成维度倾向分。

该模块**不能抓新数据**。二次综合也禁止新增事实。

### `analysis/reviewer.py`

解决“大模型很容易重复计分”的问题。

例如：

- 融资余额上升；
- 股东户数增加；
- A股资金结构拥挤；

三处可能描述同一轮散户/杠杆资金涌入，综合结论不能把它当三份独立证据。

Reviewer 输出：

- duplicate_factors；
- possible_conflicts；
- weak_links；
- confidence_penalty。

## 3. 当前 12 个研究方向

1. 管理层与治理
2. 经营基本面与护城河
3. 研发、人才与技术护城河
4. 筹码与散户结构
5. 价格与估值位置
6. 行业周期位置
7. 政策、利率、汇率与地缘
8. 雪球散户情绪
9. 股东回报与资本抽取
10. 增长空间与股票弹性
11. A股资金结构与市场风格
12. 财务质量与尾部风险

最终报告再额外区分：

- 企业长期质量；
- 当前股票赔率；
- 综合看多/中性/看空。

## 4. 中国市场专门数据

### 资金结构

`a_share_structure.py` 当前覆盖：

- 公募基金；
- 社保；
- QFII；
- 保险；
- 前十大流通股东；
- 汇金/证金/国新/诚通等公开国家资本；
- 香港中央结算等境外资金；
- ETF 名称识别；
- 两个披露季度快照的机构变化；
- 未来 12 个月解禁。

严格措辞：

- “本期新见” != “首次买入”；
- “本期未再见” != “全部卖出”；
- “前十大未见国家资本” != “国家队没有持仓”；
- “解禁” != “一定卖出”。

### KOL 市场经验

`knowledge_base.py` 对老木匠 / 军师祭咖啡内容抽取：

- 原则；
- 机制；
- 适用条件；
- 失效条件。

KOL 内容永远是 opinion，不覆盖一手事实。

## 5. 关键质量规则

### 好公司 != 当前好赔率

允许：

- 企业质量看多 + 当前赔率中性；
- 企业质量中性 + 当前赔率看多；
- 两者冲突时综合中性。

### 周期股

禁止：

- 周期顶峰因为 PE 低就直接说低估；
- 周期底部因为 PE 高就直接说昂贵。

必须结合产品价格/运价/库存/景气与中周期盈利能力。

### 科技股

研发投入不是护城河本身。

需要尽量组合：

- 研发率；
- 研发人数及占比；
- 学历结构（只描述结构，不直接评价能力）；
- 发明专利；
- 产品/客户验证；
- 盈利兑现；
- 当前估值预期。

### 管理层

禁止主观打“人品分”。

改看：

- 任期、持股、薪酬；
- 经营兑现；
- 5/10 年分红；
- 回购；
- 再融资；
- 减持；
- 处罚、问询；
- 关联/治理事件。

### 财务质量

制造业/消费/医药等重点看：

- 经营现金流/利润；
- 应收增速 vs 营收；
- 存货增速 vs 营收；
- 客户/供应商集中度；
- 资产负债率；
- 再融资和质押。

## 6. 图表原则

图表必须解释投资问题，而不是装饰。

当前主要图表：

- 12 维倾向横条图；
- 历史 PE 曲线；
- 股东户数 vs 股价；
- 机构披露季度变化；
- 营收 vs 应收/存货增速；
- 分红 + 回购。

雷达图保留为辅助视图，不作为主图。

## 7. 测试策略

普通 PR CI：

- `uv lock --locked`；
- Python compileall；
- WebUI inline JavaScript `node --check`；
- Evidence / freshness；
- Reviewer；
- Research profile；
- Report blocks；
- 管理层资本配置；
- A股资金结构；
- 巨潮公告；
- 宏观利率；
- 研发团队 PDF 解析。

公开数据 live smoke：

```bash
python tools/live_public_acceptance.py
```

默认代表公司：

- 中芯国际：technology；
- 中国神华：cyclical；
- 招商银行：financial；
- 贵州茅台：consumer_brand；
- 长江电力：stable_yield。

也可以从 GitHub Actions 手工运行：

`Investment Agent live public-data acceptance`

该 workflow 不依赖雪球登录、不调用 LLM，只验证公开数据链。

## 8. 新增数据源的正确方式

新增一个数据源时，应依次回答：

1. 它是 fact / derived / opinion？
2. 来源等级是什么？
3. 数据时点是什么？
4. freshness 多久？
5. 支撑哪个研究方向？
6. 会不会和现有因子重复计分？
7. 是否需要单独图表？
8. 缺失时应该降 confidence，还是仅作为可选信息？

正确扩展路径：

```text
new_source.py
  → AnalysisInputs
  → evidence.py
  → research_profile (如属于重点证据)
  → report_blocks.py
  → report_contract.py（仅当研究规则需要变化）
  → web/app.py
  → tests
```

不要直接把新 API 返回值拼进 `report.py` 的长字符串。
