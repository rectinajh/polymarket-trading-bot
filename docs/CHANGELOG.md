# 改动记录

每次策略、风控、下单口径的代码改动记在这里，方便对照实盘。
PnL 数字仍记在 [NET_PNL.md](NET_PNL.md)。**阶段计划与决策门**见 [ROADMAP.md](ROADMAP.md)。

日期为改动日（Asia/Shanghai）。括号内是 git commit（若有）。

---

## 2026-09-19 — EU5 五大联赛公允价值袖套（新）

**目的：** 替代「无参照跟单」思路——用 Pinnacle h2h 去水概率做公允价，PM 薄盘偏离 ≥4¢ 才下手。

- 新包 `src/strategies/eu5/`：价值腿（YES/NO 双边）+ 参考缓存（8h TTL，免费档 500 次/月可控）
- CLI：`python cli.py run --eu5 [--live] [--loop] [--interval 300]`
- 独立账本 `data/eu5_pnl.json` / `daily_entries_eu5.json` / `scan_stats_eu5.json`；共享体育 Guard（日亏 2%）
- PM2：`polymarket-eu5`（live，**但 `THE_ODDS_API_KEY` 未配置前空转不下单**）
- 单测 `tests/test_eu5.py`（11 个）

---

## 2026-09-19 — RN1 降速观察（日 cap 3 + 日亏限 2%）

**背景：** RN1 跟单 20 天 159 笔 −$47.16，全细分负期望（O/U −$35.19 最差）。用户选择降速而非停跑。

- `.env`：`SPORTS_RN1_MAX_ENTRIES_PER_DAY` 10→**3**；新增 `SPORTS_MAX_DAILY_LOSS_PCT=0.02`（原 0.05）
- 今日仍熔断（已亏 $8.48 ≥ $1.85），明日起按新限恢复
- 观察 2 周后再复盘：若仍负期望则停

---

## 2026-09-19 — 月度复盘：CSL 止损修复 + MIN_EDGE 降至 1.5¢

**背景（8/21→9/19）：** NAV $119→$92.96。RN1 足球跟单 159 笔胜率 58% 但净亏 −$47.16（用户决定**继续跑**）；BTC/ETH 15m 一个月 0 机会（**保持 live**）；Conservative 0 成交 0 near-miss。

**改动：**
- CSL 止损卖出改 **market/FOK**（吃最优 bid，不再挂限价等成交）— `csl_explore/executor.py` `place_yes_sell(market=True)`
- CSL 止损遇 `not enough balance`（钱包已无该 token）→ 直接关仓记账 + Discord，**不再每 30 分钟无限重试**
- CLOB 下单遇 `invalid tick size` → 解析真实最小 tick、更新缓存、**自动重试一次**（`polymarket_client.place_order`）
- Conservative `MIN_EDGE` 0.02 → **0.015**（`safe_compounder.py`；P2.3 实验口径同步）

**未动：** RN1 继续、15m 继续 live、Conservative focus 池不变。

---

## 2026-09-05 — CSL Explore 袖套（五策略 dry-run）

**目的：** 中超薄盘探索与 RN1 隔离；小仓验证结构假说。

- 计划：[CSL_EXPLORE_PLAN.md](CSL_EXPLORE_PLAN.md)
- 代码：`src/strategies/csl_explore/`
- CLI：`cli.py run --csl-explore`（`--loop --interval 120`）
- 策略：fingerprint · time_lag · completeness · narrative · anti_whale（可选 dog_basket）
- 账本：`data/csl_explore_ledger.jsonl`（不写 `sports_pnl.json`）
- Live 下单 = P2（本版只 plan）

---

## 2026-09-05 — 日限 10 + 信号优先级 + Will-win 价带 + 幽灵仓加速

**目的：** 日限打满时优先网球；压低足球赛果亏面；账本幽灵更快对齐链上。

- `SPORTS_RN1_MAX_ENTRIES_PER_DAY=10`
- 排序：网球 &gt; soccer props &gt; Will-win
- Will-win 价带 **[0.50, 0.70]**（`SPORTS_RN1_MW_PRICE_*`）；其余仍 [0.35, 0.75]
- 幽灵仓：缺失 **12h**；全平仓时 **6h**（`SPORTS_GHOST_FLAT_HOURS`）

---

## 2026-09-02 — 体育 30s + 网球 title 收紧 + 15m dry-run

**目的：** 少漏临场信号；拒 Challenger（仅 slug 带 atp）；15m 停 live 省 API。

- PM2 `polymarket-sports-rn1`：**interval 30s**
- 网球：title 须含 ATP/WTA（slug-only 不算）
- PM2 `polymarket-btc15m`：去掉 `--live`（dry-run）
- 另：Peak-DD 小样本豁免、幽灵仓 reconcile、`sports_daily_review.py`

---

## 2026-09-02 — Peak-DD 小样本误杀修复 + 幽灵仓 reconcile

**目的：** 8/30 后 Guard 因峰值回撤误挡 3 天；账本幽灵 open 失真。

- Peak-DD 仅当 `peak ≥ $10` 且 `settled ≥ 15`（`SPORTS_PEAK_DD_MIN_*`）
- 链上缺失 &gt;36h 的 open → `reconcile_ghost_opens`
- Guard halt 时仍统计 unseen copyable 信号（不写入 seen）

---

## 2026-08-30 — 日限 5 + 体育每日复盘脚本

**目的：** 方向性样本期控仓；每天一行看清信号/成交/拒因/PnL。

- 默认 / `.env`：`SPORTS_RN1_MAX_ENTRIES_PER_DAY=5`
- 脚本：`scripts/sports_daily_review.py`（可 `--append` → `data/sports_daily_review.jsonl`）
- 用法：`.venv/bin/python scripts/sports_daily_review.py`

---

## 2026-08-30 — 复盘文档 + 足球/网球价带跟单

**目的：** 固化「套利为何低效 / RN1 跟什么 / 事故与 Guard」结论；代码与默认参数对齐。

- 新文档：[RN1_WEEK_REVIEW_2026-08-30.md](RN1_WEEK_REVIEW_2026-08-30.md)；ROADMAP P6 / NET_PNL 同步
- 跟单：`is_copyable` = 足球 ∪ ATP/WTA；价带默认 **[0.35, 0.75]**；**sync open=OFF**；lookback 密封
- 过滤：禁 Spread/CFB/`usc` 误匹配；EXIT redeemable → Relayer
- 运维：可归档 settled 清日损 Guard（本地 `sports_pnl_guard_clear_*.json`，不入库）

---

## 2026-08-30 — 清日损 Guard，恢复体育跟入

**目的：** 事故日损（≈−$11.87）挡新开；归档结算笔并重置日限。

- 归档：`data/sports_pnl_guard_clear_2026-08-30.json`（含 settled + 当日 daily_entries；gitignore）
- 活账本仅留 open；`daily_entries_sports` 清空 → 日限恢复
- Guard 校验：`ok`（daily_realized=0）

---

## 2026-08-30 — 跟单扩网球 + 收紧入场价带

**目的：** 按 RN1 一周现金流，优先跟足球赛果 + ATP/WTA；避开彩票尾与锁盘。

- 白名单：`is_copyable` = 足球 ∪ ATP/WTA 单打（`SPORTS_RN1_ALLOW_TENNIS=true`；ITF/双打默认关）
- 默认价带 **[0.35, 0.75]**（`SPORTS_RN1_COPY_PRICE_MIN/MAX`）
- 仍禁止 Spread / CFB / 镜像存量仓

---

## 2026-08-30 — 校正体育账本 + 重置 Guard；停镜像存量仓

**目的：** 假 lost（−$37.76）误触发熔断；恢复跟单并避免再踩坑。

- 归档校正账本：`data/sports_pnl_archive_2026-08-27.json`（核实 SL/跟平/redeem ≈ **−$6.49**）
- `sports_pnl` 新实验日起 **2026-08-30**；默认 **`SPORTS_RN1_SYNC_OPEN_POSITIONS=false`**
- EXIT：`redeemable` / 无 orderbook → Relayer redeem；EXIT FAIL 冷却 30min
- **事故：** 写空账本时旧进程仍开着 sync，又镜像一批；`usc` slug 误跟美式足球 USC。已 `cancel_all`、修过滤、进程启动密封 lookback（不回填）
- 事故仓归档：`data/sports_pnl_accident_2026-08-30.json`

---

- 每轮先跑 exits：RN1 不再持有同侧 → 市价/限价卖出我们的跟单仓
- 单仓止损默认 **−20%**（`SPORTS_RN1_STOP_LOSS_PCT`）；**无自动止盈**
- `sports_pnl` 按 `condition:side` 记账，修复 NO 仓被误标 lost
- Discord：`notify_sports_exit`

---

## 2026-08-27 — RN1 纯足球跟单（镜像持仓 + 持续监控）

**目的：** 只跟足球；同步 RN1 当前足球仓方向；忽略网球/电竞等。

- `soccer_filter.py`：足球判定；排除 ITF/CS/MLB 等
- 每轮：拉 RN1 `positions?redeemable=false` 同步未跟过的足球仓
- 新成交：只跟 soccer BUY（同 `outcomeIndex` → YES/NO）
- 仓位：满足 CLOB **≥5 股 / ≥$1**；目标 $1，硬顶 $5（`COPY_HARD_MAX_USDC`）
- PM2 60s live；首轮已同步 **16** 笔足球方向

---

## 2026-08-27 — P4 改为 RN1 纯跟单（弃用 Odds API）

**目的：** Odds API 额度贵且已耗尽；改为直接跟 RN1 钱包成交，单笔硬顶 $1 USDC。

- `strategy.py`：mode=`sports_rn1_copy`；轮询 RN1 BUY → GTC；**无 Pinnacle**
- `rn1_tracker.py`：解析 `tx_hash` / `asset`；`copy_share_count`
- 去重：`data/rn1_copy_seen.json`（首轮 bootstrap 不回填）
- 环境：`SPORTS_RN1_COPY_MAX_USDC=1`、日限默认 10；PM2 间隔 **60s**
- CLI / Dashboard / ROADMAP 同步；不再依赖 `THE_ODDS_API_KEY`

---

## 2026-08-26 — P6 运营观察期规划（ROADMAP）

**目的：** 代码 sprint 收尾后进入「先运营、再决策、后开发」阶段；待决项写入路线图。

- [ROADMAP.md](ROADMAP.md) 新增 **P6** 与「下一步规划」：每周检查清单、体育 RN1 模式待决、可选工程与时间线
- 待决：体育 `strict` / `event` / 暂停；15m live vs dry-run

---

## 2026-08-26 — P4 L2 排队（WebSocket + 双边 Maker 库存）

**目的：** L1 范围冻结；WebSocket 订单簿与双边 Maker 库存明确为 **下一阶段**，不写代码直至门槛满足。

- 新增 [P4_L2_NEXT.md](P4_L2_NEXT.md)：L2 规格、启动门槛（NAV ≥ $500、L1 ≥ 30d）、实现顺序
- [ROADMAP.md](ROADMAP.md) / [NET_PNL.md](NET_PNL.md) / [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md) / [STRATEGY_MODES_AND_LEARNING.md](STRATEGY_MODES_AND_LEARNING.md) 同步

---

## 2026-08-26 — 体育 PnL / 停损 / Discord 完善

**目的：** 补齐体育 L1 运维：结算台账、90 天实验停损、Discord、Dashboard PnL。

- `sports_pnl.json`：开仓、mark、won/lost 结算
- `sports_guard.py`：日亏 5% NAV、实验回撤 30%、90 天到期
- `sports_alerts.py`：Discord 推送挂单/结算/暂停（`POLYMARKET_SPORTS_DISCORD`）
- 策略：停损拦截、避免重复 GTC、live 成交写台账
- Dashboard：今日/累计 PnL、实验进度、PnL 台账表

---

## 2026-08-26 — 体育 live + Dashboard 面板

**目的：** P4 从 dry-run 切 live；Overview 可视体育扫描与 RN1 确认。

- PM2 `polymarket-sports-rn1` 加 `--live`（仍 ≤1% NAV、RN1 strict）
- Dashboard `render_sports_rn1_panel`：扫描/RN1 确认/日限/信号表/拒绝原因
- `scan_stats` 写入 `live` + `rn1_confirm_mode` 字段

---

## 2026-08-26 — P4 RN1 聪明钱确认（Layer 2）

**目的：** Pinnacle 信号通过后，再与 RN1 成交对齐才下单。

- `src/strategies/sports/rn1_tracker.py`：轮询 data-api 缓存 RN1 24h 成交
- `RN1_CONFIRM_MODE=strict`（默认）：同 condition、YES BUY、价格 ≤ limit+2tick
- 可选 `event`（同场比赛有 RN1 活动） / `off`（关闭）
- 环境变量：`RN1_PROXY_WALLET`、`RN1_LOOKBACK_HOURS`、`RN1_PRICE_TOLERANCE_TICKS`

---

## 2026-08-26 — P4 RN1 体育 Maker L1 MVP

**目的：** 正式开发 RN1 袖套（Pinnacle 参考 + 热门 YES Maker），与 Conservative 资金隔离。

- `THE_ODDS_API_KEY` → `src/clients/odds_api_client.py`（Pinnacle h2h，EU 联赛）
- `src/strategies/sports/`：发现「Will X win on date?」、队名匹配、edge、GTC YES bid
- CLI：`python cli.py run --sports-rn1 [--loop] [--live]`
- PM2：`polymarket-sports-rn1`（300s，**默认 dry-run**）
- 台账：`data/daily_entries_sports.json`；扫描：`data/scan_stats_sports.json`
- 默认：`≤1% NAV/笔`，`≤2 笔/天`，edge ≥5pt，开球前 ≥2h

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
- P4 RN1 体育 Maker：**L1 live**；**L2（WebSocket + 双边库存）排队** → [P4_L2_NEXT.md](P4_L2_NEXT.md)。
- 完整待办列表见 [ROADMAP.md — 未完成 / 待办](ROADMAP.md#未完成--待办)。
