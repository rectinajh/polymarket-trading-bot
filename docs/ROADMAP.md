# 整体开发计划（路线图）

以当前实盘为准：**Conservative + 15m 均为 live**（Conservative focus 池 + 日限 2；15m ≤12/天）、NAV≈**$119**、**P0/P1 已完成**（2026-08-25 复盘）。  
PnL 见 [NET_PNL.md](NET_PNL.md)；P1 书面结论见 [P1_REVIEW.md](P1_REVIEW.md)；改动见 [CHANGELOG.md](CHANGELOG.md)。

---

## 总原则

1. **一条主线跑稳，再开支线** — Conservative 为主；15m / RN1 均为独立袖套（小仓）。
2. **先有数据，再改代码** — P0 观察窗已关闭；后续改动需对照 `scan_stats` / Dashboard。
3. **资金量决定策略上限** — ~$119 适合验证逻辑，不适合「每 15m $10–250」类 KPI。
4. **质量门槛不动** — `MIN_EDGE=0.02`、`Completeness 0.98` 维持；优化选池与执行，不松风控换成交。

---

## 阶段总览

| 阶段 | 时间（建议） | 主题 | 状态 |
|---|---|---|---|
| **P0** | 8/19 → **8/25** | 观察 + 基线 | **✅ 已完成** |
| **P1** | **8/25–8/26** | 复盘 + 定价/选池诊断 | **✅ 已完成** → [P1_REVIEW.md](P1_REVIEW.md) |
| **P2** | **8/26 起** | Conservative 小改（选 B） | **✅ 核心项已完成**；2.2/2.3 按 P1 跳过 |
| **P3** | 8/21 起 | BTC+ETH 15m Completeness | **✅ live 60s**（P1 曾建议 dry-run；**8/26 用户改回 live**） |
| **P4** | **8/26 起** | RN1 体育 Maker | **🟡 L1 live**；**L2 排队** → [P4_L2_NEXT.md](P4_L2_NEXT.md) |
| **P5** | 贯穿 | 工程债 / 运维 | **✅ 核心已完成**（告警/Discord/redeem/M3/M4）；见 [未完成清单](#未完成--待办) |
| **P6** | **8/26 起** | 运营观察 + 待决决策 | **🟡 进行中** → [下一步规划](#下一步规划p6-运营观察期) |

---

## P0 — 观察窗 ✅ 已完成（8/25 关闭）

**目标：** 攒满可解释的空仓/成交证据。

| 通过标准 | 结果 |
|---|---|
| 5～7 个交易日 `scan_stats` | ✅ 1653+ Conservative 轮次 |
| near-miss / 成交 / 拒绝可解释 | ✅ near-miss 全程 **0**；成交 **0**；主拒 `edge_negative` + `no_real_ask` |
| NAV / 赎回无未解释亏损 | ✅ NAV≈$119.38 全现金；无未解释回撤 |

---

## P1 — 复盘 + 诊断 ✅ 已完成

**结论（摘要）：** 市况偏贵；公允价公式无系统偏差；**不降** `MIN_EDGE` / Completeness 门槛。

**决策：**

| 选项 | 决策 |
|---|---|
| **A** 维持 Conservative 门槛 | ✅ 采用 |
| **B** 小改 Conservative（选池/仪表盘） | ✅ 已实施（见 P2） |
| **C** 15m dry-run 或停 live | P1 书面 **推荐 C**；**8/26 曾执行 dry-run，同日用户 override → live 60s** |

完整报告：[P1_REVIEW.md](P1_REVIEW.md) · 脚本：`scripts/p1_review.py --live-edge`

---

## P2 — Conservative 增强 ✅ 已完成（适用项）

| 顺序 | 项 | 状态 |
|---|---|---|
| 2.1 | **选池优化** | ✅ Gamma 预过滤；**天气 + ≤48h** focus 池；查盘 **80**；天气 vol **3000** |
| 2.2 | **公允价** | ⏭ **跳过**（P1：无偏差） |
| 2.3 | **MIN_EDGE 实验** | ✅ **框架已接**（`near-miss>0` 时自动分析；**不改** live 门槛） |
| 2.4 | **仪表盘** | ✅ near-miss 为 0 也显示；7 日图标注数据起始日；预过滤统计 |
| 2.5 | **Completeness** | ✅ **未放宽** 0.98；查盘 **50**（减 API） |

**同步工程项（P5 交叉）：**

- Conservative 单轮 **共享 Gamma** 拉取
- `MAX_ENTRIES_PER_DAY` **6 → 2**
- Positions API **45s 缓存**（减 429）
- CLOB 日志降噪（timeout / 429 等）
- 主 bot 扫描间隔 **240s**

**仍不做：** 为冲成交关日限 / 深度 / 聚类 / 降 `MIN_EDGE`。

---

## P3 — Crypto 15m Completeness ✅ live 运行中

独立 sleeve；与 Conservative **资金/日限额隔离**（15m 单独 **≤12 笔/天**，Conservative **≤2 笔/天**）。

### 代码入口

| 资源 | 路径 |
|---|---|
| 策略包 | `src/strategies/btc_15m_completeness/` |
| CLI | `cli.py run --btc-15m-completeness --live --loop --interval 60` |
| PM2 | `polymarket-btc15m` — **`--live`，60s** |
| 扫描日志 | `data/scan_stats_btc15m.json` |

### 里程碑

| 里程碑 | 状态 |
|---|---|
| M1 发现 slug（BTC+ETH） | ✅ |
| M2 干跑统计 | ✅ |
| M2b ETH 并入 | ✅ |
| M2c 小仓 live（8/21–8/25） | ✅ 曾开；8/26 曾短暂 dry-run；**同日恢复 live** |
| M3 Orphan unwind | ✅ **超时强平**（`orphan_unwind.py`，120s / 窗口结束前） |
| M4 结算前清仓 / 区间 PnL | ✅ **区间 PnL 台账** + Dashboard（清仓与 M3 共用 orphan sweep） |
| M5 观察至 8/25 复盘 | ✅ **0 成交**；P1 建议 dry-run；**运营：live 继续** |

**P1 数据：** 2000 轮、0 成交；combined≥0.98 占绝大多数。

---

## P4 — RN1 体育 Maker 🟡 L1 live（L2 排队）

独立 PM2：`polymarket-sports-rn1`（**`--live`**，300s）。  
逻辑：Polymarket 足球「Will X win on date?」热门盘（0.50–0.85） vs **Pinnacle h2h**（The Odds API）→ GTC YES bid + **RN1 strict** 确认。  
资金：`≤1% NAV/笔`，`≤2 笔/天`；台账 `daily_entries_sports.json` · `sports_pnl.json` · 90d 停损。  
详见 [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md)。

| 项 | 状态 |
|---|---|
| Odds API + Pinnacle 参考线 | ✅ `odds_api_client.py` |
| RN1 聪明钱确认 | ✅ `rn1_tracker.py`，默认 `strict` |
| 体育 PnL + 90d 停损 | ✅ `sports_pnl.py` + `sports_guard.py` |
| Discord 体育推送 | ✅ `sports_alerts.py` |
| 防重复挂单 | ✅ open YES orders 检测 |
| 市场发现 + 队名匹配 | ✅ `src/strategies/sports/` |
| CLI `--sports-rn1` + PM2 live | ✅ 300s |
| Dashboard 体育面板 | ✅ Overview `render_sports_rn1_panel` |

**L1 范围冻结。** 不在本阶段开发：**WebSocket 订单簿**、**双边 Maker 库存** → 见 **[P4 L2 下一阶段](P4_L2_NEXT.md)**。

---

## P4 L2 — 下一阶段 ⏸ 排队

| 项 | 状态 | 说明 |
|---|---|---|
| **WebSocket 订单簿** | ⏸ 排队 | CLOB WS 替代 300s REST；欧足时段秒级 |
| **双边 Maker 库存** | ⏸ 排队 | 同场 YES/NO 报价 + inventory 敞口管理 |
| **启动建议** | — | NAV ≥ $500；L1 ≥ 30d 样本；见 [P4_L2_NEXT.md](P4_L2_NEXT.md) |

---

## P5 — 工程与运维 ✅ 核心已完成

| 项 | 状态 |
|---|---|
| 赎回 / 僵尸仓 | ✅ Safe Compounder inventory + redeem 基础 |
| CLOB → data-api fallback | ✅ |
| `scan_stats` + Dashboard | ✅ 含 P2.4、运维面板、15m PnL |
| P1 报告 / 文档同步 | ✅ 本文件 + CHANGELOG + NET_PNL |
| 系统化告警（PM2/429/NAV） | ✅ `ops_alerts.py` + PM2 120s |
| Discord 推送 | ✅ `warning` 级已开（`POLYMARKET_DISCORD_ALERT_LEVEL=warning`） |
| Redeem 提醒 | ✅ Dashboard + 日志 + scan_stats + Discord |
| **Proxy Relayer 自动 redeem** | ✅ `relayer_redeem.py` + `manage_inventory` + `scripts/redeem_all.py` |
| P3 M3 orphan 超时强平 | ✅ `orphan_unwind.py` + **btc15m + completeness_arb** |
| P3 M4 15m 区间 PnL | ✅ `btc15m_window_pnl.json` + Dashboard |

**仍缺（见下节）：** P2.3 执行降门槛（near-miss>0）；P4 L2（WebSocket + 双边库存，见 [P4_L2_NEXT.md](P4_L2_NEXT.md)）。

---

## 未完成 / 待办

按优先级与触发条件分组（2026-08-26 快照）。

### 工程 / 运维

| 项 | 状态 |
|---|---|
| **Git 提交/推送** | ✅ P4 体育 MVP |

### 等数据再考虑（P1 门槛）

| 项 | 触发条件 | 现状 |
|---|---|---|
| **P2.3 降 MIN_EDGE 执行** | 实验 `recommendation` + 人工确认 | 框架已跑；**near-miss=0 时 skipped**；live 仍 **0.02** |
| **P2.2 公允价调整** | P1 诊断发现系统偏差 | **跳过** |
| **降 `MIN_EDGE` / 放宽 0.98** | 有 near-miss 或 P1 支持 | **明确不做**（自动） |

### P4 L2 — 下一阶段（排队）

| 项 | 启动门槛 | 现状 |
|---|---|---|
| **WebSocket 订单簿** | NAV ≥ $500；L1 稳定 ≥ 30d | ⏸ [P4_L2_NEXT.md](P4_L2_NEXT.md) |
| **双边 Maker 库存** | 同上 + 接受更高占用资金 | ⏸ 同上 |

### 等资金再观察（L1 已 live）

| 项 | 说明 |
|---|---|
| **P4 L1 样本** | strict 下可能长期 0 笔；看 `scan_stats_sports.json` / `sports_pnl.json` |

### 运营观察（非开发）

| 项 | 说明 |
|---|---|
| Conservative **0 成交** | 市况偏贵；focus 池 + 日限 2 已上线，等天气类窗口 |
| 15m **0 成交** | P1：2000 轮；live 继续但机会极稀 |
| 体育 **RN1 strict** | 可能长期 0 笔；L2 不提前开 |
| PM2 **重启次数** | `ops_alerts` 会 warning；需时查 `pm2 logs` |

---

## 当前 PM2 配置（`/www`）

| 进程 | 模式 | 间隔 |
|---|---|---|
| `polymarket-bot` | `--conservative --live` | **240s** |
| `polymarket-dashboard` | Streamlit :8501 | — |
| `polymarket-btc15m` | **`--btc-15m-completeness --live`** | **60s** |
| `polymarket-ops-alerts` | `ops_alerts.py --loop 120s` | **120s** |
| `polymarket-sports-rn1` | **`--sports-rn1 --live`** | **300s** |

---

## 决策门（已执行）

```text
2026-08-25  P0 关闭 → P1 复盘
            → A 维持 2¢/0.98
            → B 小改选池（已完成）
            → C 15m 书面推荐 dry-run

2026-08-26  P2 + P5 核心完成；P4 L1 live + PnL/停损/Discord
            P4 L2（WebSocket + 双边库存）→ 排队，见 P4_L2_NEXT.md
            → P2.3 降门槛（仅 near-miss>0 + 人工）

2026-08-26  进入 P6 运营观察期（见下节「下一步规划」）
            → 待决：体育 RN1 strict / event / 暂停
            → 待议：15m 是否改 dry-run（P1 选项 C）
```

---

## 下一步规划（P6 运营观察期）

**原则：** 先运营、再决策、后开发。P0～P5 核心代码已齐；接下来 **2～4 周以跑数据为主**，不急于加功能。  
**资金现实：** NAV ~$119 适合验证逻辑，不适合 P4 L2 或冲成交式降门槛。

### 现状快照（2026-08-26）

| 袖套 | PM2 | 数据结论 |
|---|---|---|
| **Conservative** | live 240s | P0 观察窗 **0 笔** — 市况偏贵，等天气窗口，**正常** |
| **15m Completeness** | live 60s | **2000 轮 0 成交**；combined≥0.98 占绝大多数 |
| **体育 RN1 L1** | live 300s strict | 有 Pinnacle edge，**`rn1_no_confirm` 挡单**；90 天实验刚启，**0 笔结算** |
| **P2.3 降门槛** | ops 每轮写入 | `near_miss=0` → **skipped**，**不改** live 门槛 |
| **P4 L2** | — | NAV < $500 → **不写代码**（见 [P4_L2_NEXT.md](P4_L2_NEXT.md)） |

**运维告警：** `polymarket-bot` / `dashboard` 重启次数偏高 → 见下方「第三优先级」。

---

### 第一优先级：运营观察（几乎不写代码）

**目标：** 三条 live 袖套各自产出可复盘数据。

**每周固定看：**

| 袖套 | 文件 / 面板 |
|---|---|
| Conservative | `data/scan_stats.json` · Dashboard Overview |
| 15m | `data/scan_stats_btc15m.json` · `btc15m_window_pnl.json` |
| 体育 | `data/scan_stats_sports.json` · `data/sports_pnl.json` |
| 全局 | `data/ops_alerts.json`（429、PM2 重启、NAV） |

| 动作 | 决策 |
|---|---|
| **Conservative** | 维持 P1 **选项 A** — 不改 `MIN_EDGE` / 0.98；空仓 = 风控在工作 |
| **体育 L1** | 跑满 **90 天实验**（起点 2026-08-26）；留意 Odds API 配额 |
| **P2.3** | 仅当 `near_miss_count > 0` 才讨论降门槛；须 **人工确认** |

---

### 第二优先级：待决运营决策（建议本周定）

体育 **strict** 下有 edge、无成交 — **预期行为**。三选一：

| 选项 | 做法 | 适合若… |
|---|---|---|
| **A 保持 strict** | 不改 `.env` | 验证「Pinnacle + RN1 对齐」最严 hypothesis；接受长期 0 笔 |
| **B 改 `event` 模式** | `RN1_CONFIRM_MODE=event` + PM2 重启 | L1 阶段**先积累样本**；接受更多噪声 |
| **C 暂停体育 live** | `pm2 stop polymarket-sports-rn1` | 省 Odds API / 注意力留给 Conservative |

**文档建议（非强制）：** 若 90 天内要判定体育路线是否值得 → **B** 跑 2～4 周并保留 strict 拒绝统计对照；若复制 RN1 最严逻辑 → **A**。

| 项 | 状态 |
|---|---|
| 体育 RN1 模式 | ⏳ **待决**（A / B / C） |
| 15m live vs dry-run | ⏳ **待议**（P1 书面推荐 C；当前仍为 live） |

---

### 第三优先级：可选工程（有开发时间再做）

按 ROI 排序；**均无触发则不启动**。

| # | 项 | 价值 | 触发条件 |
|---|---|---|---|
| 1 | **15m 改 dry-run** | 省 API、减 orphan 风险 | 继续 0 成交 + 用户接受 P1 选项 C |
| 2 | **查 PM2 重启根因** | 稳定性 | `ops_alerts` 持续 warning |
| 3 | **P2.1 选池再优化** | 少打 `no_real_ask` | Conservative API 压力高 |
| 4 | Dashboard 体育周报 | 自动汇总 strict 拒绝率 / Odds 配额 | 纯运营便利 |

**明确不做（除非数据变）：**

- 自动降 `MIN_EDGE`（P2.3 仍 skipped）
- P4 L2 WebSocket / 双边 Maker 库存
- 放宽 Completeness **0.98**
- 月榜 #1 式大单 event、AI 方向性（见 [STRATEGY_MODES_AND_LEARNING.md](STRATEGY_MODES_AND_LEARNING.md)）

---

### 时间线

```text
现在 ～ 2 周
  ├─ 运营：三袖套 live 观察 + 每周看 Dashboard / ops_alerts
  ├─ 决策：体育 strict vs event vs 暂停（本周定）
  └─ 可选：15m 是否改 dry-run

2 ～ 4 周
  ├─ 体育：若有成交 → 看 sports_pnl 首笔结算
  ├─ Conservative：等天气窗口是否再现 edge
  └─ near_miss > 0 → 才讨论 P2.3（仍须人工）

30 天+
  ├─ 复盘体育 L1：ROI、回撤、笔数、RN1 确认命中率
  └─ NAV ≥ $500 且 L1 稳定 → 评估 P4 L2（见 P4_L2_NEXT.md）

90 天（sports_guard 实验到期）
  └─ 宣布体育路线：继续 / 暂停 / 改模式
```

**若只能做一件事：** 定体育 **RN1 确认模式**（strict / event / 暂停）— 决定 L1 实验能否产出可判定样本。

---

## 默认路径（当前）

```text
Conservative live（focus 天气+48h，日限 2，门槛不变）
        │
        ├─ 有天气 mispricing → 偶发 1～2 笔
        └─ 无 edge → 空仓（正常）

15m live 60s（≤2% NAV/笔，≤12/天；P1 数据仍显示机会极稀）

RN1 L1 live（PM2 sports-rn1，strict，Dashboard）
        │
        └─ L2 排队：WebSocket 盘口 + 双边 Maker 库存（见 P4_L2_NEXT.md）
```

---

## 相关文档

| 资源 | 路径 |
|---|---|
| P1 复盘 | [P1_REVIEW.md](P1_REVIEW.md) |
| 净利润台账 | [NET_PNL.md](NET_PNL.md) |
| 改动记录 | [CHANGELOG.md](CHANGELOG.md) |
| Edge 诊断 | `scripts/edge_diagnostic.py` · `scripts/p1_review.py` |
| P4 L2 规划 | [P4_L2_NEXT.md](P4_L2_NEXT.md) |

---

*最后更新：2026-08-26 — P6 运营观察期规划；P4 L2 排队 → [P4_L2_NEXT.md](P4_L2_NEXT.md)。*
