# 整体开发计划（路线图）

以当前实盘为准：**Conservative 在跑**、NAV≈$119、**观察到 2026-08-25（下周二）**、观察期内**不松策略阈值**。  
PnL 见 [NET_PNL.md](NET_PNL.md)；模式对比见 [STRATEGY_MODES_AND_LEARNING.md](STRATEGY_MODES_AND_LEARNING.md)；改动记录见 [CHANGELOG.md](CHANGELOG.md)。

---

## 总原则

1. **一条主线跑稳，再开支线** — 不同时大改 Conservative、上 BTC 15m、上 RN1。
2. **先有数据，再改代码** — 用 `data/scan_stats.json` + Dashboard「Conservative 扫描观察」攒满观察窗。
3. **资金量决定策略上限** — ~$119 适合验证逻辑，不适合按「$10–250/15m 区间」类 KPI 验收。
4. **技术栈** — 继续 Python + 现有 CLOB 客户端；除非 15m 实盘证明延迟是唯一瓶颈，再评估 Go/Rust。

---

## 阶段总览

| 阶段 | 时间（建议） | 主题 | 状态 |
|---|---|---|---|
| **P0** | 现在 → **2026-08-25** | 观察 + 基线 | **进行中**；策略阈值冻结 |
| **P1** | 8/25–8/28 | 复盘 + 定价/选池诊断 | 只读分析 → 决定小改或不改 |
| **P2** | 8/28–9/10 | Conservative 增强（可选） | 选池 / 公允价 / 仪表盘 |
| **P3** | 9 月中（可选） | BTC 15m Completeness 实验 | 独立进程 + 小仓 |
| **P4** | 更晚（可选） | RN1 体育 Maker | 新模块；资金到位再开 |
| **P5** | 贯穿 | 工程债 / 运维 | 赎回、告警、文档 |

---

## P0 — 观察窗（到 2026-08-25）

**目标：** 攒满可解释的空仓/成交证据，不凭感觉改策略。

| 做什么 | 不做 |
|---|---|
| 每天看 Dashboard：过 edge、edge 不足（near-miss）、edge 为负、Arb YES+NO≥1、成交、NAV | 改 `MIN_EDGE` / Completeness 阈值 |
| Bot / Dashboard 保持现状 | 上 RN1、上 15m 主策略 |
| 仅修故障（进程挂、赎回异常） | 为「多成交」松风控 |

**Near-miss 说明：** `0 < edge < 2¢` 的候选；Dashboard 有数据时才显示「🎯 Near-miss」块，否则看拒绝表里的「edge 不足（0&lt;edge&lt;2¢）」。

**通过标准（周二复盘）：**

- 至少 **5～7 个交易日** 的 `scan_stats` 记录
- 能回答：near-miss 接近 0 还是变多？成交 0 还是偶发？
- NAV / 赎回无未解释亏损

---

## P1 — 周二复盘 + 诊断（约 2～3 天）

**目标：** 决定「空仓正确」还是「模型/选池有问题」。

### 工作包

1. **复盘报告（半天）**  
   - 按日汇总：扫描、机会、成交、拒绝 Top  
   - 对照 8/17–8/18 曾成交 vs 近几日全空  

2. **Edge 为负诊断（1～2 天，优先）**  
   - 抽样 10～20 个 `edge_negative`：YES_last、NO_ask、手算 edge  
   - 结论三选一：**公允价定义有偏差** / **市况就是贵** / **选池太脏**  

3. **机会密度抽查（半天，只读）**  
   - BTC 15m：一天内「双边 ask 合计 &lt; 0.98」出现几次  
   - 决定 P3 是否值得开  

**通过标准：** 书面结论 + 下一阶段选 **A 维持 / B 小改 Conservative / C 开 15m 只读或干跑**。

---

## P2 — Conservative 增强（约 1～2 周，仅当 P1 需要）

按优先级，**不要一次全做**：

| 顺序 | 项 | 说明 |
|---|---|---|
| 2.1 | **选池优化** | 无真实 NO ask / low_conf 提前过滤，少打无效盘口 |
| 2.2 | **公允价** | 仅当 P1 证明 YES_last 口径有系统偏差时再改 |
| 2.3 | **参数实验** | **仅当 near-miss 明显增多**：`MIN_EDGE` 单变量干跑 1～2 天 |
| 2.4 | **仪表盘** | near-miss 为 0 时也显示计数；7 日图标注「有数据日起」 |
| 2.5 | **Completeness** | 一般不松到 0.99；除非 P1 有明确机会数据 |

**不做：** 为冲成交关掉日限 / 深度 / 聚类。

**通过标准：** 过 edge 或成交有可解释改善，或确认「维持空仓」策略正确。

---

## P3 — BTC 15m Completeness 实验（可选，2～4 周）

对应「两边 &lt; $1 + orphan 管理」类需求（如 gabagool 风格），**独立于主 Conservative**。

| 里程碑 | 内容 | 约工期 |
|---|---|---|
| M1 | 市场发现：下一场 BTC 15m condition、开收时间 | 2～3 天 |
| M2 | 双边定价：ask 合计 &lt; 阈值才下；FOK/GTC 策略选定 | 2～4 天 |
| M3 | **Orphan 管理**：单腿成交 → 立即对冲/市价平；超时强平 | 3～5 天 |
| M4 | 结算前清仓 / 赎回；区间 PnL 台账（每 15m 一行） | 2～3 天 |
| M5 | 独立 PM2 + 小仓（建议 ≤ 总资金 1/5）干跑 → 实盘 | 持续 |

### 与外包帖子的关系

- **策略类型可做**：与现有 `completeness_arb.py` 同族，需专用 15m 调度与腿风险引擎。
- **验收标准不可承诺**：「每 15m 均赚 $10–250、连跑 3 天」在 ~$119 NAV 下不现实（288 区间 × $10 即 ~$2,880，需大量本金与机会密度）。
- **自有验收指标**：有机会次数、腿失败率、区间盈亏分布、orphan 处理成功率。

**硬约束：** 主 bot Conservative **继续跑**；资金 / 进程隔离。

**通过标准：** 干跑 ≥2 天无逻辑事故；小仓 ≥3 天腿失败率可控；再谈加仓。

---

## P4 — RN1 体育 Maker（可选，更晚，3～6 周+）

仅在 Conservative 观察结论清楚，且愿意**另开资金/账号**时启动。  
成本与条件见 [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md)。

**开发顺序：**

1. 赔率 API + 赛事名匹配  
2. Maker 双边挂单引擎  
3. 赛事日历 / 开球时段  
4. WebSocket + adverse selection  
5. 独立 PnL / PM2  

**不做：** 用 $100 验证能否「复制榜一 RN1」。

---

## P5 — 工程与运维（贯穿）

| 项 | 说明 |
|---|---|
| 赎回 / 僵尸仓 | 已有基础；观察窗内盯告警 |
| 成交拉取 | CLOB 超时 → data-api fallback（已上） |
| 扫描统计 | `scan_stats.json` + Dashboard Overview |
| 文档 | CHANGELOG / NET_PNL 按周补；阶段结束更新本路线图 |
| 发布 | 改策略或新进程 → 同步 `/www` + `pm2 restart` + push |

---

## 资源与决策门

```text
资金 ~$119
  → P0/P1/P2 足够
  → P3 只能「验证」，不能「达标外包 KPI」
  → P4 建议另筹 $500～5k+

同时最多 1 个「改策略」主题 + 1 个小修

决策门：
  2026-08-25  → 开不开 P2 / P3
  P3 干跑过关 → 开不开小仓
  资金到位    → 开不开 P4
```

---

## 默认路径（少纠结版）

```text
现在 ──P0──► 8/25 复盘
                │
                ├─ near-miss≈0 且 edge 负占优 ──► 维持 Conservative，可选 P2.1 选池
                │
                ├─ 诊断出公允价问题 ──► P2.2
                │
                └─ 15m 机会密度够 且想做 ──► P3 干跑（独立 sleeve）
                         │
                         └─ 以后资金/兴趣 ──► P4 RN1
```

---

## 近期日历

| 日期 | 动作 |
|---|---|
| 今～8/24 | 只观察，记拒绝结构 |
| **8/25（二）** | 复盘会：定 A/B/C |
| 8/26–8/28 | P1 诊断（或开 P2.1）；仍不开 RN1 |
| 8/28 后 | 按门控进 P2 / P3 |

---

## 相关代码与文档

| 资源 | 路径 |
|---|---|
| Conservative 入口 | `cli.py --conservative --loop` |
| Safe Compounder | `src/strategies/safe_compounder.py` |
| Completeness Arb | `src/strategies/completeness_arb.py` |
| 扫描统计 | `src/strategies/scan_stats.py` → `data/scan_stats.json` |
| Dashboard | `scripts/trading_dashboard.py` |
| Edge 诊断脚本 | `scripts/edge_diagnostic.py` |

---

*最后更新：2026-08-20 — 观察窗至 2026-08-25。*
