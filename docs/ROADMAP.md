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
| **P4** | **8/26 起** | RN1 体育 Maker | **🟡 L1 MVP 已开发**（默认 dry-run；NAV ~$119 小仓） |
| **P5** | 贯穿 | 工程债 / 运维 | **✅ 核心已完成**（告警/Discord/redeem/M3/M4）；见 [未完成清单](#未完成--待办) |

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

## P4 — RN1 体育 Maker 🟡 L1 MVP（dry-run）

独立 PM2：`polymarket-sports-rn1`（300s，**无 `--live`**）。  
逻辑：Polymarket 足球「Will X win on date?」热门盘（0.50–0.85） vs **Pinnacle h2h**（The Odds API）→ GTC YES Maker  bid。  
资金：`≤1% NAV/笔`，`≤2 笔/天`，独立台账 `daily_entries_sports.json`。  
详见 [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md)。

| 项 | 状态 |
|---|---|
| Odds API + Pinnacle 参考线 | ✅ `odds_api_client.py` |
| RN1 聪明钱确认（Layer 2） | ✅ `rn1_tracker.py`，默认 `strict` |
| 市场发现 + 队名匹配 | ✅ `src/strategies/sports/` |
| CLI `--sports-rn1` + PM2 | ✅ 默认 dry-run |
| Live 实盘 | ⏸ 需人工 `--live` + NAV/样本评估 |

**仍缺：** WebSocket Maker、双边库存、Dashboard 体育面板、60 天对照实验。

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

**仍缺（见下节）：** P4 live 评估、P2.3 执行降门槛（near-miss>0）。

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

### 等资金再开（~$119 不适合）

| 项 | 建议门槛 | 现状 |
|---|---|---|
| **P4 RN1 体育 Maker** | NAV **≥ $500** 建议 live；L1 MVP 已 dry-run | **🟡 MVP 已开发**，见 [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md) |

### 运营观察（非开发）

| 项 | 说明 |
|---|---|
| Conservative **0 成交** | 市况偏贵；focus 池 + 日限 2 已上线，等天气类窗口 |
| 15m **0 成交** | P1：2000 轮；live 继续但机会极稀 |
| PM2 **重启次数** | `ops_alerts` 会 warning；需时查 `pm2 logs` |

---

## 当前 PM2 配置（`/www`）

| 进程 | 模式 | 间隔 |
|---|---|---|
| `polymarket-bot` | `--conservative --live` | **240s** |
| `polymarket-dashboard` | Streamlit :8501 | — |
| `polymarket-btc15m` | **`--btc-15m-completeness --live`** | **60s** |
| `polymarket-ops-alerts` | `ops_alerts.py --loop 120s` | **120s** |
| `polymarket-sports-rn1` | **`--sports-rn1` dry-run** | **300s** |

---

## 决策门（已执行）

```text
2026-08-25  P0 关闭 → P1 复盘
            → A 维持 2¢/0.98
            → B 小改选池（已完成）
            → C 15m 书面推荐 dry-run

2026-08-26  P2 + P5 核心完成（告警/Discord/redeem/M3/M4）
            15m + Conservative 均 live
            → 未完成见上文「未完成 / 待办」：
               · P4 RN1（L1 dry-run 已开；live 待 $500+）
               · P2.3 降门槛（仅 near-miss>0 + 人工）
               · Completeness orphan ✅ · Discord warning ✅ · P2.3 框架 ✅
```

---

## 默认路径（当前）

```text
Conservative live（focus 天气+48h，日限 2，门槛不变）
        │
        ├─ 有天气 mispricing → 偶发 1～2 笔
        └─ 无 edge → 空仓（正常）

15m live 60s（≤2% NAV/笔，≤12/天；P1 数据仍显示机会极稀）

RN1：L1 dry-run（PM2 sports-rn1）；live 待评估
```

---

## 相关文档

| 资源 | 路径 |
|---|---|
| P1 复盘 | [P1_REVIEW.md](P1_REVIEW.md) |
| 净利润台账 | [NET_PNL.md](NET_PNL.md) |
| 改动记录 | [CHANGELOG.md](CHANGELOG.md) |
| Edge 诊断 | `scripts/edge_diagnostic.py` · `scripts/p1_review.py` |

---

*最后更新：2026-08-26 — **Relayer 自动 redeem** 上线；P0～P5 核心完成。*
