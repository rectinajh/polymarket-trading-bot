# 改动记录

每次策略、风控、下单口径的代码改动记在这里，方便对照实盘。
PnL 数字仍记在 [NET_PNL.md](NET_PNL.md)。**阶段计划与决策门**见 [ROADMAP.md](ROADMAP.md)。

日期为提交日（仓库时区）。括号内是 git commit。

---

## 2026-08-21 — BTC 15m Completeness sleeve 干跑（P3 提前）

**目的：** 用户要求提前开 15m/RN1；先做 BTC 15m Completeness 独立 sleeve，RN1 排队。

- 新增 `src/strategies/btc_15m_completeness/`：slug 发现 + Completeness 扫描 + orphan unwind
- CLI：`python cli.py run --btc-15m-completeness [--loop] [--interval 15] [--live]`
- 独立 `data/daily_entries_btc15m.json` / `data/scan_stats_btc15m.json`
- PM2：`polymarket-btc15m`（默认干跑，无 `--live`）
- Conservative 主 bot **不动**

---

## 2026-08-18 — 大资金仓位政策与赎回 (`2e16627`)

**目的**：资金变大后不再按「每天赚固定几美元」交易；空仓是合法结果。同时处理已结算仓位，避免对着死盘口硬卖。

### 仓位

- 新增 `src/strategies/capital_policy.py`，Compounder 与 Completeness 共用。
- 单笔份额 = `min(前两档 ask 深度的 25%, NAV 档位上限, 半 Kelly, 现金)`。
- NAV 档位上限：
  - &lt; $500 → 5%
  - &lt; $5,000 → 2%
  - &lt; $20,000 → 1%
  - 其余 → 0.5%
- 取消 Completeness 硬编码 20 股上限，改按盘口深度下单。
- **无每日利润目标**。

### 每日开仓上限

- 每个自然日（Asia/Shanghai）最多 **6 笔新开仓**。
- Compounder 与 Completeness 共用 `data/daily_entries.json`。
- 同一天气城市、同一币种 Up/Down 视为相关簇，共用一个名额。

### 赎回

- 持仓带 `redeemable` / `title` / `negative_risk`。
- 已结算仓：EOA（`signature_type=0`）走链上 `redeemPositions`。
- Proxy / 入金钱包（`signature_type≠0`）不能由机器人直接赎回，日志记 `Redeem needed`，需在 Polymarket 界面或 Relayer 领取。
- 临近到期且盘口 404 时不再当可卖仓强平。

---

## 2026-08-18 — Conservative 口径与吃单修正 (`2ddaede`)

**目的**：修掉「看起来像套利、实际在付溢价」的下单方式，以及把现金算两遍的 NAV。

- **NAV = 现金 + MTM**，禁止把现金再加一遍。
- Compounder `edge = (1 - YES_last) - 真实 NO ask`，要求 `edge ≥ 0.02`、`NO_ask ≥ 0.80`、距到期 &gt; 3h。
- **去掉**按剩余时间给 edge 加 1–4¢ 的启发式加成。
- 入场改为 **FOK 吃真实 NO ask**，不再挂在 ask 下方 1¢（那会买不到、或买到更差的价）。
- 持仓生命周期：临近到期才处理，不再把缺盘口当止损触发。
- Token cache 每个扫描周期只落盘一次，避免循环里反复写文件。
- Gamma 父事件 `events` 强制覆盖带 tags 的版本（原先 `setdefault` 会丢掉分类，体育单可能漏过滤）。
- CLI 循环复用 CLOB/Gamma 客户端，减少重复握手。
- Completeness 按 NAV 下单，并统计拒绝原因直方图。
- 订单簿拉取加 `Semaphore(8)` 并发上限。

---

## 2026-08-17 — 实盘切到纯数学策略 (`9e98bd9`)

**目的**：停掉 AI 方向性交易，改跑 Safe Compounder + Completeness。

- 默认实盘：`cli.py run --safe-compounder --live`（PM2 同此）。
- 新增 `src/strategies/completeness_arb.py`：YES+NO ask 合计 &lt; $0.98 且两腿都能 FOK 才下；第二腿失败立即平第一腿。
- 小账户收紧 Compounder 门槛。
- Dashboard 增加日/周/月权益视图；AI 备注仅展示、不下单。

---

## 2026-08-17 — CLOB 噪音与撤单 (`f234d57`)

- 预期内的断线 / 404 / 创建 API key 失败降为 warning，不再刷 error。
- `cancel_order` 改走 `OrderPayload`，兼容 py-clob-client-v2 上清理遗留 YES 单。

---

## 2026-08-17 — 回撤后收紧风控 (`6a6ab04`)

**背景**：AI / IMMEDIATE 路径在体育命题与薄盘口上把资金锁死。

- 跳过体育命题类市场。
- 提高成交量与置信度门槛。
- 无盘口僵尸仓归档，不再当可交易持仓。
- 临近到期强制退出；卖单失败加重试。

> 之后已切到 Conservative；本条主要约束旧 AI 路径。未再打开 IMMEDIATE / AI 换换手。

---

## 2026-08-15 — 假止损与 NO 出场 (`6fa7932`)

- 标记价为 0 或盘口 404 时 **不触发**止损。
- YES/NO 都按多头 outcome token 算止盈止损（NO 不再反向算错出场价）。
- 加固 LLM `market_id` 日志、quick-flip 失败清理、现金状态报错。

---

## 2026-08-15 / 08-14 — Dashboard (`20e7482`, `af63629`)

- 持仓明细与 open-position 映射修正。
- 权益曲线；实盘订单与 TP/SL 视图。

---

## 2026-08-14 — CLOB V2 实盘与扫描 (`8ec0fa8` 及更早)

- 切到 `py-clob-client-v2`；入金钱包 / deposit-wallet 流程。
- 订单簿按 dict 解析；优先有 mid、有流动性的盘口。
- 默认 `POLY_1271` 生产链。
- 尊重 geoblock；无活盘口的市场跳过。
- Gamma token-id 解析接到实盘下单路径。
- 做市策略停止对薄盘口狂刷。
- 支持 Kimi 作为 LLM 提供方（现已不用于 Conservative 实盘）。

---

## 刻意没做 / 已知限制

- **未**降低 `MIN_EDGE`，也 **未**重开 IMMEDIATE / AI 换换手。
- Proxy / 入金钱包的 Relayer 自动赎回尚未实现；结算后需在界面点「领取 / Redeem」。
- Completeness 经常 0 笔、Compounder 常因 `edge_lt_min` 空扫，属于预期。
