# 改动记录

每次策略、风控、下单口径的代码改动记在这里，方便对照实盘。
PnL 数字仍记在 [NET_PNL.md](NET_PNL.md)。**阶段计划与决策门**见 [ROADMAP.md](ROADMAP.md)。

日期为改动日（Asia/Shanghai）。括号内是 git commit（若有）。

---

---

## 2026-08-26 — Completeness orphan + Discord warning + P2.3 框架

**目的：** 收尾 ROADMAP 待办（不改 live MIN_EDGE）。

- `completeness_arb.py`：接 `orphan_unwind`（sweep + `partial_fail` 登记）
- `.env`：`POLYMARKET_DISCORD_ALERT_LEVEL=warning`（PM2 429/重启等推 Discord）
- P2.3：`min_edge_experiment.py` + `ops_alerts` 每轮写入 `data/min_edge_experiment.json`；**near-miss=0 时 skipped**

---

## 2026-08-26 — Proxy Relayer 自动 redeem

**目的：** deposit/proxy 钱包（`signature_type=3`）结算后自动 gasless redeem。

- 依赖 `polymarket-client`；`src/clients/relayer_redeem.py`
- 环境变量：`POLYMARKET_RELAYER_API_KEY`、`POLYMARKET_RELAYER_API_KEY_ADDRESS`（signer EOA）
- `PolymarketClient.redeem_condition()`：proxy 路径走 Relayer；EOA 仍链上 redeem
- Safe Compounder `manage_inventory` 自动调用（无需改流程）
- CLI：`python scripts/redeem_all.py` / `--dry-run`

---

## 2026-08-26 — Discord 运维告警（共用 Delphi webhook）

**目的：** 与 Delphi 同频道；必须能区分来源。

- 环境变量：`DELPHI_ALERT_WEBHOOK_URL`（与 Delphi 共用）
- `src/utils/discord_alerts.py`：正文 **`[Polymarket]`**，username **`Polymarket Bot`**
- 默认只推 **critical**；指纹去重 + 1h cooldown；严重告警清除时发恢复消息
- CLI：`scripts/ops_alerts.py --discord-test`

---

## 2026-08-26 — P5 告警 + redeem 提醒 + P3 M3/M4

**目的：** ROADMAP 待办：运维可观测性、proxy redeem 可见、15m orphan/PnL。

- 新增 `scripts/ops_alerts.py`（PM2 健康、429、NAV 跳变、redeem/orphan）+ PM2 `polymarket-ops-alerts`
- **Discord**：共用 `DELPHI_ALERT_WEBHOOK_URL`；消息前缀 **`[Polymarket]`**，username `Polymarket Bot`；去重 cooldown
- `src/utils/ops_metrics.py`：429 计数落盘；CLOB/data-api 触发写入
- Dashboard：**运维告警**面板、`redeem_needed` 高亮、**15m 区间 PnL** 面板
- `scan_stats`：`latest_redeem_needed` / `latest_nav_cents`
- Safe Compounder：`REDEEM_NEEDED` 日志强化
- P3 **M3**：`orphan_unwind.py` + btc15m 每轮 sweep / 超时强平
- P3 **M4**：`btc15m_window_pnl.json` 按窗口记账
- NET_PNL：上海 8/18 close-all 已确认（+$0.56）

---

## 2026-08-26 — 两进程恢复全真钱 live（用户 override P1-C）

**目的：** 不再跑 dry-run；Conservative + 15m 均 `--live` 真下单。

- PM2 `polymarket-btc15m`：恢复 **`--live`**，interval **60s**（`ecosystem.config.cjs`）
- PM2 `polymarket-bot`：维持 **`--conservative --live`**，interval **240s**
- P1 书面仍推荐 15m dry-run；本条目为**运营决策**，见 [ROADMAP.md](ROADMAP.md)

---

## 2026-08-26 — P2 小账户 focus 池 + 日限 2

**目的：** P1 决策 B——不松 `MIN_EDGE`/Completeness，提高「有 edge 时被扫到」概率（~$119 NAV）。

- Safe Compounder **focus 池**：仅 **天气** 或 **≤48h 到期**；`off_focus` 预过滤
- 天气市场 `MIN_VOLUME` **3000**；查盘口 **200→80**
- `MAX_ENTRIES_PER_DAY` **6→2**（`capital_policy.py`）
- Completeness 查盘 **150→50**（门槛仍 0.98）
- PM2 主 bot 间隔 **300→240s**
- 测试：`test_safe_compounder` 日限用例改为 2 笔

---

## 2026-08-26 — P1 复盘 + 工程优化

**目的：** 8/25 观察窗关闭；书面结论 + 减 API 噪音。

- 新增 [P1_REVIEW.md](P1_REVIEW.md)、`scripts/p1_review.py --live-edge`
- Conservative **单轮共享 Gamma**（`cli._conservative_cycle`）
- Gamma 预过滤：`gamma_no_high`、`lottery_tail`；`MAX_YES_LAST_FOR_EDGE=0.18`
- Completeness **last-sum≥0.98** 预过滤（不打 orderbook）
- Positions API **45s TTL 缓存**（减 data-api 429）
- 日志：timeout / ConnectionTerminated / 429 降为 WARNING
- Dashboard：near-miss 为 0 也显示；7 日图标注 scan 起始日；预过滤统计

---

## 2026-08-26 — P3 15m 曾停 live，改 dry-run 60s（同日已恢复 live）

**目的：** P1 结论 C——2000 轮 0 成交；15s live 加剧 429。

- PM2 `polymarket-btc15m`：曾去掉 `--live`，**interval 60s**
- **同日**用户要求全真钱 → 已恢复 `--live`（见上条「两进程恢复 live」）
- 主 Conservative **不受影响**，全程 live

---

## 2026-08-21 — P3 15m sleeve 开小仓 live

**目的：** 用户要求直接 live；袖套上限 ≤2% NAV/笔、≤12/天。

- PM2 `polymarket-btc15m` 加 `--live`（BTC+ETH）
- **8/26 曾短暂 dry-run**；**同日恢复 live**（见 2026-08-26 条目）

---

## 2026-08-21 — P3 干跑并入 ETH

- `discover.py` 多资产；`strategy.py` 默认 BTC+ETH；`by_asset` 统计

---

## 2026-08-21 — BTC 15m Completeness sleeve（P3 提前）

- 新增 `src/strategies/btc_15m_completeness/`
- CLI：`--btc-15m-completeness`；独立 `daily_entries_btc15m.json` / `scan_stats_btc15m.json`

---

## 2026-08-18 — 大资金仓位政策与赎回 (`2e16627`)

- 新增 `capital_policy.py`；深度 25%、NAV 档位上限、半 Kelly
- 每日开仓上限（**后于 2026-08-26 改为 2 笔**，见上）
- 赎回 / `redeemable` / 近到期 force exit

---

## 2026-08-18 — Conservative 口径与吃单修正 (`2ddaede`)

- `edge = (1 - YES_last) - NO_ask`，`≥ 0.02`；FOK 吃 ask；去掉 time boost
- NAV = 现金 + MTM；Completeness 按 NAV  sizing

---

## 2026-08-17 — 实盘切到纯数学策略 (`9e98bd9`)

- Safe Compounder + Completeness Arb；Dashboard 日/周/月权益

---

## 2026-08-17 — CLOB 噪音与撤单 (`f234d57`)

- 404 / 断连 / api key 降 warning；`cancel_order` → `OrderPayload`

---

## 2026-08-17 — 回撤后收紧风控 (`6a6ab04`)

- 体育过滤、僵尸仓归档等（旧 AI 路径；Conservative 已替代）

---

## 更早条目

见 git history（2026-08-14～15 Dashboard / CLOB V2 等）。

---

## 刻意没做 / 已知限制

- **未**降低 `MIN_EDGE`，**未**放宽 Completeness 到 0.99（P1 数据不支持）。
- **未**重开 IMMEDIATE / AI 方向性实盘。
- **已完成**：Proxy Relayer **自动** redeem（`relayer_redeem.py` + Relayer API key）。
- **未完成**：Conservative `completeness_arb` orphan sweep（btc15m 已接 `orphan_unwind`）。
- P2.2 公允价 / P2.3：**框架已接**（near-miss>0 才 active）；**未**自动降低 live `MIN_EDGE`。
- P4 RN1 体育 Maker：**排队**（建议 NAV ≥ $500 再开）。
- 完整待办列表见 [ROADMAP.md — 未完成 / 待办](ROADMAP.md#未完成--待办)。
