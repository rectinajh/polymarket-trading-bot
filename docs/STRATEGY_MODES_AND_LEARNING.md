# 各模式对比：风险、利润、数学依据与学习路线

用大白话整理 Polymarket 上常见几种赚钱方式，方便和当前 **Conservative**（~$119 NAV）对照选型。  
**整体开发顺序（P0–P4）**见 [ROADMAP.md](ROADMAP.md)。体育实验成本见 [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md)；PnL 见 [NET_PNL.md](NET_PNL.md)。

---

## 一、六种模式一张表

| 模式 | 在赚什么 | 数学/模型依据 | 风险（1～5） | 小资金利润潜力 | 与现 bot 匹配 |
|---|---|---|---:|---:|---|
| **① Conservative NO** | 贵 NO 上 last 比 ask 便宜 ≥2¢ | `edge = (1−YES_last) − NO_ask`；半 Kelly + NAV cap | **2～3** | **低～中** | ★★★★★ 已在跑 |
| **② Completeness** | YES+NO 问价 &lt;$1 锁 $1 结算 | `profit = 1 − (YES_ask+NO_ask)` | **1～2**（leg 风险） | **低**（简单层常被扫光） | ★★★★★ 已在跑 |
| **③ RN1：体育 Maker + FLB** | 热门被低估 + spread | Favorite–Longshot Bias；`P(真) − P(隐含)`；Pinnacle 参考 | **3～4** | **中～高**（需 $5k+、Maker） | ★☆☆☆☆ |
| **④ 月榜 #1/#2：大单 Event** | 信息/速度押单场 | 主观 `P` vs 市价；无稳定公式 | **5** | 高方差；$100 不够分散 | ★☆☆☆☆ |
| **⑤ 月榜 #3：超高换手薄 edge** | 巨量 × ~1～2% ROI | 做市/扫尾；库存 + 队列 | **3～4** | 小资金几乎分不到 | ★☆☆☆☆ |
| **⑥ 跨市场组合套利** | 多盘逻辑概率和 ≠ 100% | Bregman 投影、Frank-Wolfe、ILP | **2 理论 / 4 执行** | 机构级 | ★☆☆☆☆ |
| **⑦ 0.2–0.4 分歧区**（鉅亨文） | 买便宜 outcome，高盈亏比 | 凸性 payoff | **4～5** | 中（若真有 edge） | ★★☆☆☆ 需全新策略 |
| **⑧ AI 方向性**（已停） | LLM 猜方向 | 主观概率 + Kelly | **5** | 不稳定 | ★☆☆☆☆ 已放弃 |

---

## 二、各模式详解（风险 / 利润 / 数学）

### ① Conservative NO（Safe Compounder）

**怎么赚：** 市场把 NO 卖贵了（相对 YES last），FOK 吃 ask，等结算 $1。

**数学：**

- `edge = (1 − YES_last) − NO_ask`，要求 `edge ≥ 0.02`、`NO_ask ≥ 0.80`
- 仓位：`min(深度 25%, NAV 档位, 半 Kelly, 现金)`
- **不是锁定价差**，是 **有条件的高置信 NO**

**风险：**

- 单笔 capped ~$6（NAV≈$119 时）
- 小概率 YES 成真 → NO 归零，单笔约亏 **~$5.3**
- 空扫天数多 ≠ 策略坏了

**利润：**

- 单笔潜在 ~$0.5～1（如上海 33°C：6 份 @0.89 → +$0.66）
- 累计已实现约 **+$1.44**（见 NET_PNL.md）
- 适合 **保本金、等 mispricing**，不冲榜

---

### ② Completeness（YES + NO）

**怎么赚：** 同时买 YES+NO，成本 &lt;$1，结算必得 $1。

**数学：**

- `1 − (YES_ask + NO_ask) ≥ 0.02`，两腿 FOK
- 经典 **无方向套利**（两腿都成交时）

**风险：**

- **Leg risk**：一腿成交、另一腿失败 → 变方向赌；代码会尝试平第一腿

**利润：**

- 理论清晰；现实简单层 **常 0 笔**（已被更快系统扫光）
- 价值：低成本捡漏模块，与 ① 并行

---

### ③ RN1 型：体育 Maker + Favorite–Longshot Bias

**怎么赚：** 不猜比分；热门标价偏低（如 0.65、统计像 0.80）+ 双边 Maker spread。

**数学：**

- 横截面：`实际胜率 − 入场价`（Foresight 文：0.60–0.70 桶实际 ~80%）
- 可写：`E[PnL] ≈ (p̂ − p_m) × n`，p̂ 来自历史或 **Pinnacle**
- **91% Maker**，88% 持有到结算

**Pinnacle 是啥？** 平博——低抽水、不限赢家的传统博彩，赔率接近真实概率；RN1 用它当 **尺子**：Pinnacle 说 75%、Polymarket 标 65% → 有 edge 才做。

**是稳定套利吗？不是。**

| | ② Completeness | ③ RN1 |
|---|---|---|
| 类型 | **无方向套利**（锁 $1） | **统计正期望**（像保险公司） |
| 单笔会亏 | 否（两腿都成交） | **会**（冷门赢则热门归零） |
| 风险 | 1～2 | **3～4** |

**鸡蛋类比：** Polymarket 把好鸡蛋（热门）标 $0.65、真值 ~$1；RN1 低价收，80% 拿回 $1，20% 归零——**长期统计赚钱，不是每笔稳赚**。

**风险：**

- **Adverse selection**：进球前 informed 用户吃你的 Maker
- 需 **大量样本** 才收敛；$100 统计无效

**利润：**

- RN1：子集 ROI ~7.3%，半年 **+$510 万** 建立在 **$5,660 万 turnover**
- 小资金复制不了规模与排队

详见 [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md) 第一～七节。

---

### ④ 月榜 #1 / #2：大单 Event

**怎么赚：** 大赛事上大仓位（如单场阿森纳相关 +$180 万级别）。

**数学：** 接近 **单次 bet**，弱形式 `主观 P vs 市价`，**不可稳定建模**。

**风险：** **5** — 一场可大幅回撤。

**利润：** 榜上显眼；**$119 方差致命**，不建议学。

---

### ⑤ 月榜 #3 型：超高换手、薄 ROI

**怎么赚：** 月成交 $7,341 万、利润 +$126 万 → turnover ROI ~**1.7%**。

**数学：** 做市商：`E[spread] − adverse_loss`；要 **毫秒 + 大资金**。

**风险：** 工程、流动性、排队。

**利润：** 小账户 **分不到队列位置**。

---

### ⑥ 跨市场组合套利（ABMedia / Roan 文）

**怎么赚：** 「赢宾州」与「宾州赢 5%+」等逻辑相关盘 **概率不一致**。

**数学：** Bregman 投影到无套利流形；Frank-Wolfe + Gurobi。

**风险：** 理论低风险；**多腿非原子** → 执行风险高。

**利润：** 机构级（文献称一年 ~$4,000 万量级）；**$119 无意义**。

**学习价值高，实盘门槛高** — 小资金只读 **单条件** 部分即可（即你们的 ②）。

---

### ⑦ 0.2–0.4 分歧区（鉅亨 9 万地址文）

**怎么赚：** 在 **分歧最大** 价位买 outcome，赢则倍数高。

**数学：** 凸性 — 下行有限、上行弹性；该区间统计胜率 ~50%、盈亏比优。

**与 ① 的关系：** **相反** — ① 买贵 NO（~0.8+）；⑦ 买便宜票（0.2–0.4）。

**风险：** **4～5**，连亏心理压力大。

**不建议在 Conservative 上硬改**；若要玩需全新策略。

---

### ⑧ AI 方向性（已停用）

**风险：** 已验证 **不适合** 本账户（8/17 回撤、体育/薄盘）。**不要作为主修。**

---

## 三、文章观点怎么对照（避免搞混）

| 来源 | 核心 | 和 ① / ③ 的关系 |
|---|---|---|
| [鉅亨 9 万地址](https://news.cnyes.com/news/id/6309390) | 全样本买 &gt;0.8 长期差；中频中位 PnL≈0 | 提醒别盲买贵票；① 有 **edge 门槛**；③ 是 **体育子集 + Maker** |
| [ABMedia 套利](https://x.com/ABMedia_Crypto/status/2031614131820966290) | 跨市场 + 毫秒执行 | ⑥ 机构路；② 只覆盖最简单盘 |
| [Foresight RN1](https://foresightnews.pro/article/detail/95050) | 足球 FLB + Maker | 体育实验最接近的参照 |
| [月榜](https://polymarket.com/zh/leaderboard/overall/monthly/profit) | 大池子体育/event | 品类与 ③ 重叠，打法含 ④⑤ |

**共性：** 都在赚 **「定价错了」**。  
**差异：** 池子、执行（Maker/Taker）、资金规模、数学工具。

---

## 四、推荐学习路线（按优先级）

针对：**有小 bot、NAV ~$100–500、Conservative 在跑、暂不上体育实盘**。

### 第一优先级（现在就该学 — 与现有 bot 直接相关）

| 学什么 | 为什么 | 怎么学 |
|---|---|---|
| **定价 vs 预测** | 赚价错，不是猜得准 | RN1 文第一节 + 你们 `edge` 公式（NET_PNL.md） |
| **无套利 / Completeness** | ② 数学最干净；理解 leg risk | `completeness_arb.py` + ABMedia **单条件** 部分 |
| **Kelly / 仓位** | 半 Kelly + NAV tier 已在用 | `f* = edge/odds`；为何 **半 Kelly** 防爆仓 |
| **执行风险** | FOK、第二腿、proxy 赎回 | CHANGELOG + 上海仓案例 |
| **统计与样本** | 3 笔不能下结论 | 鉅亨中位 PnL≈0；`scripts/edge_diagnostic.py` |

**暂不必学：** Bregman/Gurobi（⑥）、Go/Rust 低延迟 — **NAV &lt; $5k 前 ROI 极低**。

### 第二优先级（扩大视野 — paper / 阅读，不实盘）

| 学什么 | 为什么 |
|---|---|
| **Favorite–Longshot Bias** | 理解体育池为何有钱 |
| **Maker vs Taker** | 榜上一半利润来自 Maker 队列 |
| **外部参考线（Pinnacle）** | 无尺子则体育退化为赌博 |
| **链上 trades vs Activity API** | 复盘别误判 maker/taker |

### 第三优先级（NAV ≥ $2k 且愿做体育实验）

- 赛程、WebSocket 盘口、Maker 库存、adverse selection  
- 美东 VPS、Odds API  
- 见 [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md) L1～L2  

### 明确不要主修

| 方向 | 原因 |
|---|---|
| 月榜 #1 式大单 event（④） | 不可复制、小资金方差致命 |
| AI 方向性（⑧） | 已验证不适合 |
| 全面改 0.2–0.4 分歧（⑦） | 与 Conservative 相反，需新系统 |
| 跟单排行榜 | 延迟 + 幸存者偏差 |

---

## 五、个人路线建议（一张图）

```text
现在（NAV ~$100–500）
  ├─ 主修：① Conservative 数学 + ② Completeness + Kelly/风控 + 执行/样本
  ├─ 辅修：RN1/FLB 概念（只读，不实盘）
  └─ 动作：Conservative 继续跑；台账；空扫不焦虑

6～12 个月后（若 NAV ≥ $500～1000 且仍想对比）
  ├─ 体育 L0 paper + Pinnacle 价差日志
  └─ 仍不学 Go/Rust、Gurobi，除非 profiling 证明需要

除非全职 + $10k+
  └─ 再考虑 L2 Maker 或 ⑥ 跨市场（职业量化路径）
```

---

## 六、综合结论

| 维度 | 建议 |
|---|---|
| **小资金利润期望** | ①② 稳但薄；③⑤⑥ 榜上有名但门槛高；④⑦ 勿用 $119 玩 |
| **风险最低且已在验证** | ② 理论最低（常 0 笔）；① 单笔小但有 tail risk |
| **最该学的** | 定价错误 + 无套利 + Kelly + 执行/样本 |
| **储备阅读** | FLB / Maker / **Pinnacle 当尺子**（为将来决策，非现在实盘） |
| **现在实盘** | **只跑 Conservative**；体育等 NAV 与样本量到位再议 |

**易混概念速查：**

| 问题 | 答案 |
|---|---|
| Pinnacle 是啥 | 全球最准体育赔率之一，当 **基准线** |
| RN1 是稳定套利吗 | **否**；是统计正期望，单笔可亏 |
| 风险最小的是谁 | **② Completeness**（常 0 笔）；不是 RN1 |
| ① 和 ③ 像吗 | 都可能有 tail loss；① 是天气/短周期 + edge 门槛，③ 是足球 + Maker + 大量样本 |

**一句话：** 榜上学的是 **大池子 + 大工程**；你们学 **小池子 + 严 edge + 能执行的数学** — 先把 ①② 吃透，比追 RN1 更匹配当前账户。

---

## 参考链接

- [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md)
- [NET_PNL.md](NET_PNL.md)
- [CHANGELOG.md](CHANGELOG.md)
- [Polymarket 月榜](https://polymarket.com/zh/leaderboard/overall/monthly/profit)
- [Foresight RN1](https://foresightnews.pro/article/detail/95050)
- [鉅亨 9 万地址](https://news.cnyes.com/news/id/6309390)
- [ABMedia 套利](https://x.com/ABMedia_Crypto/status/2031614131820966290)
