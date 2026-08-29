# Conservative 净利润台账

记账口径：`NAV = 现金 + MTM`。不要把现金再加一遍。
已实现盈亏在市场结算/赎回后才记；持仓只记 MTM。

来源：`logs/pm2-bot-out.log`、轮转日志、`scan_stats` NAV 快照。时区除非注明均为 UTC。

## 账户快照

| 时间 (UTC) | 现金 | MTM | NAV | 备注 |
|---|---:|---:|---:|---|
| 2026-08-17 07:59 | 117.26 | 0.00 | 117.26 | 上海 33°C 下单前 |
| 2026-08-17 07:59 | 111.89 | 5.81 | 117.70 | 下单后 |
| 2026-08-17 23:08 | 117.83 | 0.00 | 117.83 | 上海 17 已结算，ETH 下单前 |
| 2026-08-18 00:01 | 118.82 | 0.00 | 118.82 | ETH 已结算 |
| 2026-08-18 09:10 | 118.82 | 0.00 | 118.82 | 上海 32°C 下单前 |
| 2026-08-18 13:19 | 113.46 | 5.91 | 119.37 | 上海 18 开仓后 |
| 2026-08-25～26 | ~119.38 | 0.00 | **~119.38** | close-all 后全现金；P0 观察窗内 scan_stats `nav_cents=11938` |
| 2026-08-26 | 119.38 | 0.00 | **119.38** | **已确认**：无持仓、无 redeemable；上海 8/18 经 close-all 平仓入账 |

**当前 PM2（2026-08-26）：** `polymarket-bot` **live** 240s；`polymarket-btc15m` **live** 60s；`polymarket-sports-rn1` **live** 300s（RN1 strict）；`polymarket-ops-alerts` 120s + Discord；Dashboard :8501。

**ROADMAP 未完成项（摘要）：** P2.3 **执行降门槛**（需 near-miss>0 + 人工）；P4 **L2 排队**（WebSocket + 双边 Maker 库存 → [P4_L2_NEXT.md](P4_L2_NEXT.md)）。详见 [ROADMAP.md](ROADMAP.md#未完成--待办)。

累计 Conservative **已实现（约计）：+$2.00**（上海 17 +$0.66，ETH +$0.78，上海 18 close-all +$0.56）。

**体育 RN1（2026-08-30）：** 事故 + SL 造成真实 NAV 回撤（账本曾记日损约 −$11.87，后为恢复跟入做 Guard 清档；**清档不等于回本**）。过滤后实验：足球+网球、价带 [0.35,0.75]、sync=OFF。复盘见 [RN1_WEEK_REVIEW_2026-08-30.md](RN1_WEEK_REVIEW_2026-08-30.md)。

上海 18（`0x34f4df9b…`）：8/18 开仓后 MTM +$5.91；**8/25 前 close-all 已平**；2026-08-26 API 确认 **0 持仓 / 0 redeemable**，NAV $119.38 全现金。按 close-all 路径已实现约 **+$0.56**（相对 8/18 开仓前 $118.82）。

**P0 观察窗（8/19–8/25）：** Conservative + Completeness **0 笔新成交**；NAV 维持 ~$119.38。

## 成交

| 时间 (UTC) | 市场 | 侧 | 数量 | 价格 | 成本 | 结算/状态 | 已实现 |
|---|---|---|---:|---:|---:|---|---:|
| 2026-08-17 07:59 | 上海 8/17 最高温 33°C `0x3aed6cc2…` | NO | 6 | 0.89 | 5.34 | 已结算 | +0.66 |
| 2026-08-17 23:08 | ETH Up/Down 8/17 12–4PM ET `0x802e4399…` | NO | 6 | 0.87 | 5.22 | 已结算 | +0.78 |
| 2026-08-18 09:10 | 上海 8/18 最高温 32°C `0x34f4df9b…` | NO | 6 | 0.89 | 5.34 | **close-all 已平**（非 redeem） | **+0.56** |

Completeness（YES+NO ask &lt; $0.98）：**0 笔**（含 P0 观察窗）。  
BTC/ETH 15m sleeve：**0 笔**（8/21 起 live；8/26 曾短暂 dry-run 后恢复 live；累计仍 0 成交）。  
RN1 体育 L1 sleeve：**live**（8/26 起；strict 确认下成交样本待积累；台账 `data/sports_pnl.json`）。

## 公式（用来核对「是不是套利」）

口径以 `2e16627` / `2ddaede` 为准；P1/P2 见 [CHANGELOG.md](CHANGELOG.md)、[ROADMAP.md](ROADMAP.md)、[P1_REVIEW.md](P1_REVIEW.md)。

- **Completeness（套利）**：`1 - (YES_ask + NO_ask) ≥ 0.02` 且 combined **&lt; 0.98**；两腿 FOK。
- **Safe Compounder（方向性 NO）**：`edge = (1 - YES_last) - NO_ask`，要求 `edge ≥ 0.02`、`NO_ask ≥ 0.80`、剩余时间 &gt; 3h；**focus 池**（2026-08-26 起）：天气或 ≤48h 到期。
- **单笔份额**：`min(前两档 ask 深度的 25%, NAV 档位上限, 半 Kelly, 现金)`。
- **NAV 档位**：&lt;$500 → 5%（当前 ~$6/笔）；&lt;$5k → 2%；&lt;$20k → 1%；否则 0.5%。
- **每日新开仓**：最多 **2 笔**（两策略共用，2026-08-26 起；原 6 笔）；相关标题共用一个名额。无每日利润目标。

## 更新规则

每个自然日（UTC+8 收盘后）补一行快照：现金、MTM、NAV、当日成交、已实现。
僵尸仓（盘口 404）不记市值，赎回到账后再记已实现。
阶段结论与下一步见 [ROADMAP.md](ROADMAP.md)。
