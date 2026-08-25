# P1 复盘报告

生成时间：2026-08-26 02:07 CST（Asia/Shanghai）

观察窗：Conservative `scan_stats` 自 2026-08-19 起；BTC/ETH 15m 自 2026-08-21 起。
对照成交：2026-08-17～08/18 天气/ETH NO（见 [NET_PNL.md](NET_PNL.md)）。

---

## 1. 按日汇总（Conservative）

| 日期 | 轮次 | SC 机会 | 下单 | near-miss | NAV | SC 拒绝 Top | Arb 拒绝 Top |
|---|---:|---:|---:|---:|---:|---|---|
| 2026-08-19 | 43 | 0 | 0 | 12 | $119.38 | edge_negative=6223, no_real_ask=1954, low_conf=270 | combined=5104, thin_size=1344 |
| 2026-08-20 | 264 | 0 | 0 | 20 | $119.38 | edge_negative=33306, no_real_ask=16074, low_conf=3363 | combined=34736, thin_size=4857 |
| 2026-08-21 | 263 | 0 | 0 | 22 | $119.38 | edge_negative=35697, no_real_ask=14599, low_conf=2231 | combined=34467, thin_size=4974 |
| 2026-08-22 | 265 | 0 | 0 | 24 | $119.38 | edge_negative=36394, no_real_ask=14092, low_conf=2447 | combined=34466, thin_size=5276 |
| 2026-08-23 | 265 | 0 | 0 | 14 | $119.38 | edge_negative=35007, no_real_ask=15101, low_conf=2818 | combined=36438, thin_size=3302 |
| 2026-08-24 | 266 | 0 | 0 | 5 | $119.38 | edge_negative=30632, no_real_ask=19810, low_conf=2726 | combined=35774, thin_size=4114 |
| 2026-08-25 | 265 | 0 | 0 | 3 | $119.38 | edge_negative=28407, no_real_ask=21718, low_conf=2805 | combined=34410, thin_size=5335 |
| 2026-08-26 | 23 | 0 | 0 | 3 | $119.38 | edge_negative=2562, no_real_ask=1838, low_conf=197 | combined=2588, thin_size=861 |

**合计**：1654 轮，Safe Compounder 机会 0，下单 0，near-miss 累计 103。

### 全观察窗拒绝结构（Safe Compounder，累计）

| 拒绝原因 | 次数 | 占 orderbook 检查估算 |
|---|---:|---:|
| `edge_negative` | 208228 | 63% |
| `no_real_ask` | 105186 | 32% |
| `low_conf` | 16857 | 5% |
| `thin_ask` | 257 | 0% |
| `edge_lt_min` | 243 | 0% |
| `disconnect` | 29 | 0% |

### 全观察窗拒绝结构（Completeness Arb，累计）

| 拒绝原因 | 次数 |
|---|---:|
| `combined` | 217983 |
| `thin_size` | 30063 |
| `disconnect` | 54 |

---

## 2. 8/17–8/18 成交 vs 观察窗全空

| 维度 | 8/17–8/18（曾成交） | 8/19–8/26（观察窗） |
|---|---|---|
| 策略 | Safe Compounder NO（天气 + ETH Up/Down） | Conservative（SC + Completeness） |
| 成交 | 3 笔 NO，已实现约 +$1.44 | **0 笔** |
| Completeness | 0 笔 | 0 笔 |
| near-miss | 未系统记录 | **全程 0** |
| 主要拒绝 | — | SC：`edge_negative` ~54%、`no_real_ask` ~40% |

**差异解释（工作假设）**

1. 8/17–8/18 命中**上海天气**等短周期、YES last 低且 NO ask 有可吃 mispricing 的窗口。
2. 8/19 起阈值收紧（`MIN_EDGE=0.02`、去掉 time boost），且全市场 NO ask 相对 YES last **普遍偏贵**。
3. near-miss 为 0 → 不是「差 1～2¢ 就能做」，而是**要么无真实 ask，要么 edge ≤ 0**。

---

## 3. Edge 为负诊断（抽样 + 分布）

### 3.1 实时快照 edge 分布（有真实 NO ask ≥ $0.80 的候选）

| 区间 | 数量 |
|---|---:|
| <0 | 113 |

### 3.2 若仅调整 MIN_EDGE（其它门槛不变）

| MIN_EDGE | 可通过候选数 |
|---|---:|
| $0.010 | 0 |
| $0.015 | 0 |
| $0.020 | 0 |
| $0.025 | 0 |
| $0.030 | 0 |

### 3.3 `edge_negative` 抽样（手算 edge = (1 − YES_last) − NO_ask）

| # | edge | YES_last | implied_NO | NO_ask | 标题 |
|---:|---:|---:|---:|---:|---|
| 1 | -0.0010 | 0.002 | 0.998 | 0.999 | Will Jorge Rodríguez be the leader of Venezuela en |
| 2 | -0.0010 | 0.002 | 0.998 | 0.999 | Will Vladimir Padrino López be the leader of Venez |
| 3 | -0.0010 | 0.002 | 0.998 | 0.999 | Will Amazon be the largest company in the world by |
| 4 | -0.0010 | 0.002 | 0.998 | 0.999 | US obtains Iranian enriched uranium by August 31? |
| 5 | -0.0005 | 0.002 | 0.999 | 0.999 | Will LeBron James win the 2028 US Presidential Ele |
| 6 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Chelsea Clinton win the 2028 Democratic presi |
| 7 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Andrew Yang win the 2028 Democratic president |
| 8 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Tim Walz win the 2028 US Presidential Electio |
| 9 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Hillary Clinton win the 2028 Democratic presi |
| 10 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Tim Walz win the 2028 Democratic presidential |
| 11 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Kim Kardashian win the 2028 Democratic presid |
| 12 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Phil Murphy win the 2028 Democratic president |
| 13 | -0.0005 | 0.002 | 0.999 | 0.999 | Will MrBeast win the 2028 Democratic presidential  |
| 14 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Kim Kardashian win the 2028 US Presidential E |
| 15 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Vivek Ramaswamy win the 2028 US Presidential  |
| 16 | -0.0005 | 0.002 | 0.999 | 0.999 | Will John Thune win the 2028 Republican presidenti |
| 17 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Stephen Smith win the 2028 US Presidential El |
| 18 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Jared Polis win the 2028 Democratic president |
| 19 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Frank Donovan be the leader of Venezuela end  |
| 20 | -0.0005 | 0.002 | 0.999 | 0.999 | Will Zohran Mamdani win the 2028 US Presidential E |

### 3.4 诊断结论

**结论：市况就是贵（选池并非主因；公允价 YES_last 口径未见系统偏差）。**

依据：

- 观察窗内 near-miss **恒为 0**，说明不存在大量「0 < edge < 2¢」被阈值挡住的机会。
- 累计拒绝里 **`edge_negative` 占 orderbook 检查的大多数**：NO ask 高于 `(1 − YES_last)`，
  即盘口对 NO 的定价已不低于 last 隐含的胜率。
- **`no_real_ask` ~40%**：大量候选只有 derived 价、无真实 NO ask，属于选池/流动性问题，
  但即使过滤掉这些，剩余盘口的 edge 仍以负为主。
- 实时抽样中 edge<0 的案例：NO_ask 系统性地 ≥ implied NO，符合「高效/偏贵」而非公式算错。

**不建议**为 P1 修改公允价公式；P2 可选做「提前过滤 no_real_ask」减少无效 API。

---

## 4. BTC/ETH 15m 机会密度

| 日期 | 轮次 | 检查盘口 | combined≥0.98 拒绝 | 通过 combined 门 | 机会 | 成交对 |
|---|---:|---:|---:|---:|---:|---:|
| 2026-08-25 | 1574 | 13366 | 11983 | 1383 | 0 | 0 |
| 2026-08-26 | 426 | 3624 | 3225 | 399 | 0 | 0 |

**合计**：2000 轮，检查 16990 次，通过 combined<0.98 约 1782 次，机会 0，成交 0 对。

拒绝 Top：combined=15208, missing_ask=1781, expiring=399, profit_lt_min=1。

**结论**：15m 窗几乎始终 YES+NO ≥ $0.98；在 ~$119 NAV 下即使偶发也仅 ~$2.4/笔。
**建议**：P3 改 **dry-run 或停 live**，保留发现逻辑攒数据，不占 API / 不占资金。

---

## 5. 决策与下一阶段

| 选项 | 含义 | 建议 |
|---|---|---|
| **A 维持** | Conservative 阈值不变，接受空仓 | **推荐（主策略）** |
| B 小改 Conservative | 仅 P2.1 选池（少打 no_real_ask） | 可选，工程向 |
| C 15m dry-run | 停 live，只读扫描 | **推荐（15m 袖套）** |

### 最终建议

1. **主 bot（Conservative）选 A**：当前空仓是正确风控结果，不是故障。
2. **BTC/ETH 15m 选 C**：2000 轮 0 成交 + API 429 风险 → 改 dry-run 或停 PM2。
3. **P2 若做**：优先 2.1 选池优化 + positions API 限速；**不做** MIN_EDGE / Completeness 放宽。
4. **资金**：~$119 适合验证逻辑；要提高频率需 **$500+** 或等待天气类窗口再现。
5. **台账**：更新 NET_PNL 中上海 8/18 持仓状态（若已 close-all）。

---

## 6. 运营决策（P1 报告生成后）

> 本节为文档同步，**不改变上文 P1 统计与书面建议**。

| 时间 | 决策 |
|---|---|
| 2026-08-26 白天 | 按 P1 **选项 C**：15m 曾改 **dry-run 60s** |
| 2026-08-26 晚间 | **用户 override**：Conservative + 15m **均恢复 `--live` 真下单** |
| 2026-08-26 深夜 | **P5 完成**：运维告警 + Discord（`DELPHI_ALERT_WEBHOOK_URL`，`[Polymarket]` 前缀）+ M3/M4 |

**当前 PM2：** 见 [ROADMAP.md](ROADMAP.md) · **未完成待办**见 [ROADMAP — 未完成 / 待办](ROADMAP.md#未完成--待办)。

**风险提醒（仍成立）：** 15m 2000 轮 0 成交、combined≥0.98 占绝大多数；live 下偶发成交时留意 orphan 与 data-api 429。

---

*本报告由 `scripts/p1_review.py` 生成；edge 抽样来自同脚本 `--live-edge` 或 `scripts/edge_diagnostic.py`。§6 运营状态需手工与 ROADMAP 同步。*
