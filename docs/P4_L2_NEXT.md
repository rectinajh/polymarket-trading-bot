# P4 L2 — 下一阶段（WebSocket + 双边 Maker 库存）

**状态：⏸ 排队，不在当前 sprint。**  
L1 已上线（live、RN1 strict、PnL/停损/Discord/Dashboard）。本文件是 **P4 第二阶段** 的开发规格与门槛。

相关：[ROADMAP.md](ROADMAP.md) · [SPORTS_EXPERIMENT_COSTS.md](SPORTS_EXPERIMENT_COSTS.md) · [STRATEGY_MODES_AND_LEARNING.md](STRATEGY_MODES_AND_LEARNING.md)

---

## 为什么放到下一阶段

| 原因 | 说明 |
|---|---|
| **资金** | 双边库存 + 多盘挂单需 **$5k+** 才有意义；当前 NAV ~$119 仅适合 L1 验证 |
| **复杂度** | WebSocket + 库存引擎 ≈ **1～3 月+**；与 L1「单边 GTC + 300s 轮询」差一个数量级 |
| **L1 先跑数据** | 90 天实验 + `sports_pnl.json` 需先积累 strict/event 下成交与结算样本 |

**决策（2026-08-26）：** L1 **冻结功能范围**；L2 **只写文档、不写代码**，直到触发条件满足。

---

## L2 范围（计划）

### 1. WebSocket 订单簿

| 项 | 目标 |
|---|---|
| 数据源 | Polymarket CLOB WebSocket（替代/补充 300s REST 轮询） |
| 延迟 | 欧足高峰 UTC 17–23 秒级反应（非毫秒 HFT） |
| 模块 | `src/strategies/sports/ws_book.py`（待建） |
| 集成 | 触发 `evaluate_maker_opportunity` / 撤单重挂，而非仅 loop sleep |

**不做：** 整库 Go/Rust 重写；L2 仍 Python，除非 profiling 证明 CPU 瓶颈。

### 2. 双边 Maker 库存

| 项 | 目标 |
|---|---|
| 含义 | 同场 **YES + NO**（及关联盘）同时挂 Maker；管理 **inventory** 敞口 |
| 模块 | `src/strategies/sports/inventory.py`（待建） |
| 能力 | 每 event 最大敞口、单边成交后对冲/补单、adverse selection 限损 |
| 与 L1 区别 | L1 仅 **单边 YES** + RN1 确认；L2 接近 RN1 文章「91% Maker、双边 spread」 |

**参考：** [Foresight RN1](https://foresightnews.pro/article/detail/95050)；`docs/SPORTS_EXPERIMENT_COSTS.md` L2 表。

### 3. 运维配套（L2 一并考虑）

- 美东 VPS（RTT）
- Dashboard：库存敞口 / 挂单墙 / WS 连接状态
- Discord：库存告警、WS 断线

---

## 启动门槛（建议全部满足再开 L2 代码）

| # | 条件 |
|---|---|
| 1 | NAV **≥ $500**（或独立体育子账户 ≥ $500） |
| 2 | L1 live **≥ 30 天** 有 `sports_pnl.json` 结算样本（或人工确认 strict 长期 0 笔 → 改 `event` 后再观察 30 天） |
| 3 | Odds API + RN1 确认流程 **稳定**（无连续 guard halt / 429） |
| 4 | 人工确认：愿意承担 **双边方向性** 风险与更高占用资金 |

---

## 建议实现顺序（L2 sprint）

```text
1. WebSocket 订单簿（只读 + Dashboard 连接状态）
2. 库存模型 + 敞口上限（仍可不 live）
3. 双边报价逻辑（paper / dry-run）
4. live 小流量（单联赛、单时段）
5. 与 L1 并行或替换（PM2 二选一，避免双倍 API）
```

---

## 明确不在 L2 范围

- 月榜 #1 式大单 event 交易
- AI 方向性
- 降 Conservative `MIN_EDGE` / 放宽 Completeness 0.98

---

*最后更新：2026-08-26 — L2 排队；L1 live 运行中。*
