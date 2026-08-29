# 2026-08-30 复盘：套利低效、体育事故与 RN1 跟单筛选

时区：Asia/Shanghai。钱包语境：账户 funder `0xfbaa…0ad7`；跟单源 RN1 `0x2005…875ea`。  
配套：[CHANGELOG](CHANGELOG.md) · [ROADMAP](ROADMAP.md) · [NET_PNL](NET_PNL.md) · [STRATEGY_MODES](STRATEGY_MODES_AND_LEARNING.md)

---

## 1. 结论（先看这）

| 问题 | 结论 |
|---|---|
| 数学套利「效率低」 | **不是公式算错**；Polymarket 上 Completeness / Safe Compounder 门槛下的机会**密度接近 0** |
| 体育不赚钱 | **跟单是方向性风险**；叠加 **sync 存量 + 过滤事故**，小账户被日损 Guard 熔断 |
| 接下来怎么做 | **砍无效线（建议 15m dry-run）**；体育只跑 **足球 + ATP/WTA、价带 [0.35,0.75]、永不镜像 open book**，用 1–2 周干净样本再定去留 |

---

## 2. 为什么数学套利效率极低

依据：[P1_REVIEW.md](P1_REVIEW.md)、观察窗 `scan_stats`（约 8/19–8/25）。

### 2.1 Completeness（YES+NO ask &lt; 0.98）

- 15m / 主盘 **绝大多数** combined ≥ 0.98；2000+ 轮 **0 成交** 是常态，不是偶发漏单。
- 真缺口被更快做市/扫单吃掉；本 bot **60s～240s 轮询**捡不到可持续残羹。

### 2.2 Safe Compounder（NO edge ≥ 2¢）

- 主拒因是 **`edge_negative`**（NO ask 系统性贵于 `1 − YES_last`），不是「差 1¢ 被门槛挡住」。
- 观察窗 **near-miss ≈ 0** → 降 `MIN_EDGE` **不会 magically 出量**，只会买更贵的票。
- 早期天气/ETH 几笔合计约 **+$2** 证明逻辑可工作，但**可复现密度养不起 ~$100 NAV**。

### 2.3 资金与角色

- NAV ~$100–120：单笔 capped 几刀；偶有机会也被滑点/腿风险吃光。
- 小户角色应是 **严门槛捡漏**，不是和月榜级换手/库存策略比「效率」。

**明确不做：** 自动降 Completeness 0.98 / `MIN_EDGE`；NAV &lt; $500 不上 P4 L2（WS + 双边库存）。

---

## 3. 体育袖套：为何亏、踩了什么坑

### 3.1 结构问题

- RN1 跟单 = **统计跟风**，不是无风险套利；单笔可归零。
- RN1 `closed-positions` API **几乎只有盈利样本**（曾见 8000/8000 正 PnL）→ **不能当胜率/ROI**。
- RN1 **未平仓**是巨大「墓园」：成本数百万美元量级、市值极低、大量 `cur≈0` 死仓。**镜像 open book = 把墓园拷进小账户。**

### 3.2 2026-08-30 事故链（摘要）

1. 校正假 lost、重置 `sports_pnl` 时，**旧 PM2 仍 `SYNC_OPEN=ON`** → 再次批量镜像。
2. slug 裸匹配 `usc` → 误跟 **USC 美式足球 Spread**。
3. 多笔低价/事故仓触发 SL → 日损约 **−$11.87** ≥ 5% NAV 限 → Guard 挡新开。
4. 处置：`cancel_all`、修过滤、**默认 sync=OFF**、进程启动 **密封 lookback（不回填）**、EXIT 走 redeemable/Relayer。

本地归档（**勿提交 git**，见 `.gitignore`）：

- `data/sports_pnl_accident_2026-08-30.json`
- `data/sports_pnl_archive_2026-08-27.json`（校正后约 −$6.49）
- `data/sports_pnl_guard_clear_2026-08-30.json`（清日损后 settled 归档）

### 3.3 清日损说明（运维）

应要求可手动：归档今日 settled + 清空 `daily_entries_sports`，**保留 open**，使 Guard=`ok`。  
**账本清零 ≠ 钱回来了**；真实亏损仍在 NAV。之后新的 SL 仍计入当日日损。

---

## 4. RN1 近一周：跟什么、不跟什么

窗口约 **2026-08-23 → 08-30 UTC**。口径：`activity` BUY/REDEEM 现金流 + open positions；**不用** win-biased 的 closed-positions ROI。

### 4.1 本周现金流（相对强弱，ratio 可含更早建仓本周兑付）

| 品类 | BUY≈ | REDEEM≈ | redeem/buy | 含义 |
|---|---:|---:|---:|---|
| 足球（明确） | 28k | 116k | 4.12 | 赎回强；跟**新 BUY** |
| 足球（疑似/小联） | 284k | 378k | 1.33 | 量最大，噪音大 |
| Win-on 泛体育 | 75k | 184k | 2.46 | 赛果结构清晰 |
| 网球 | 61k | 111k | 1.84 | 笔少、单笔赎回大 |
| 棒球 MLB | 63k | 58k | 0.93 | 边缘一般 |
| 美式/Spread | 31k | 29k | 0.95 | 易误跟；过滤必严 |

### 4.2 未平仓墓园（结构警示）

足球/网球/Spread/电竞大量 **死仓≈0**；开仓价 &lt;0.45 的库存 mark 普遍极差 → **禁止 bulk sync**。

### 4.3 跟单规则（已落到代码默认）

| 等级 | 规则 |
|---|---|
| **优先** | 足球赛果 / Will-win；**ATP/WTA 单打**；仅 TRADE **BUY**；价 **[0.35, 0.75]** |
| **可试** | MLB 赛果（非 7.5+ 总分）；足球 O/U、BTTS 同价带 |
| **禁止** | Spread / CFB / USC；电竞；&lt;0.20 彩票尾；Exact Score 堆单；**镜像 open book**；默认不开 ITF/双打 |

环境变量（见 `env.template`）：

- `SPORTS_RN1_ALLOW_TENNIS=true`
- `SPORTS_RN1_COPY_PRICE_MIN=0.35` / `MAX=0.75`
- `SPORTS_RN1_SYNC_OPEN_POSITIONS=false`
- `SPORTS_RN1_TENNIS_INCLUDE_ITF=false`（默认）

---

## 5. 当前实盘姿态（2026-08-30 清 Guard 后）

| 项 | 状态 |
|---|---|
| PM2 `polymarket-sports-rn1` | live 60s；`soccer+tennis`；价带 [0.35,0.75] |
| Sync open | **OFF** |
| Lookback | 每进程启动密封，不回填 |
| Guard | 清档后 `ok`；日限恢复 25 |
| Conservative / 15m | 仍 live；15m **长期 0 成交**（P1 曾建议 dry-run） |

---

## 6. 建议的下一步（运营优先，少写代码）

1. **15m**：改 dry-run 或停 PM2（省 API / 注意力）。  
2. **体育**：过滤后干净跑 **7–14 天**；看合格信号数、SL 次数、redeem；两周仍净亏或几乎无信号 → **暂停**。  
3. **Conservative**：保留捡天气窗口；不降门槛冲成交。  
4. **不做**：L2 做市（NAV 不够）；再镜像任何人库存；为成交降 Completeness。

决策门（二选一主线，避免三线并行）：

- **A 小本验证**：Conservative + 过滤体育；砍 15m。  
- **B 要规模**：先加 NAV 到数百再谈执行密度 / 做市；或承认小户套利已饱和。

---

## 7. 相关代码入口

| 模块 | 路径 |
|---|---|
| 跟单策略 | `src/strategies/sports/strategy.py` |
| 足球/网球过滤 | `src/strategies/sports/soccer_filter.py`（`is_copyable_market`） |
| RN1 拉单 | `src/strategies/sports/rn1_tracker.py` |
| 日损 / 回撤 Guard | `src/strategies/sports/sports_guard.py` |
| 配置 | `src/strategies/sports/config.py` · `env.template` |

---

*文档由 2026-08-30 会话复盘整理；数字为当时 API 抽样，随市场变化。*
