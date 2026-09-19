# CSL 探索袖套开发计划（与 RN1 隔离）

**目标：** 用小仓探索中超在 Polymarket 上的定价结构，不追求稳定 alpha。  
**原则：** 默认 dry-run；独立账本；单场 / 总风险硬顶；策略可开关。

## 策略矩阵（全部启用）

| ID | 名称 | 假说 | 默认标的 | 单场名义 |
|----|------|------|----------|----------|
| `fingerprint` | 比分指纹 | 联赛常重复同一比分纹理 | Exact Score **1-1** Yes | $1.01 |
| `time_lag` | 时间错位 | 中文信息 / 赛况快于 PM | 赛中：70'+0-0→平；早红牌→对手 | $1.01 |
| `completeness` | 完备缺口 | 薄盘三路偶发 &lt;1 | 主+平+客同时买齐 | 按缺口缩放，≤$1.01×3 |
| `narrative` | 叙事对冲 | 结构（闷平/进球）&gt;队名 | 热门：热门胜+Under；均衡：平+Under | $0.50+$0.50 |
| `anti_whale` | 反向砸盘 | 薄盘大单过度反应 | 砸价后买反方向 | $1.01 |

另：反热门弱胜篮可作为对照实验开关 `dog_basket`（默认 off，代码已预留）。

## 分期

### P0 — 骨架（本提交）
- [x] 配置 / 模型 / 发现（event slug 列表）
- [x] 五策略纯信号生成 + 编排器 dry-run
- [x] JSONL 账本 `data/csl_explore_ledger.jsonl`
- [x] CLI：`cli.py run --csl-explore`
- [x] 单测：信号规则

### P1 — 可观测（下一迭代）
- [ ] Dashboard 面板 / 日审脚本
- [ ] 赛中比分源（可选 API 或手工 webhook）
- [ ] 成交量冲击检测（CLOB trades 窗口）

### P2 — 小额 live（人工确认后）
- [x] `cli.py run --csl-explore --live` 接 CLOB GTC 最小 YES 买
- [x] 去重 `filled_keys` + 周预算 / 单场帽
- [x] PM2 `polymarket-csl-explore`（120s）
- [x] Discord webhook（策略标签）
- [x] 止损：标价相对入场 **−50%** 限价卖；**止盈手动**
- [ ] 赛后结算进独立 PnL
- [ ] Dashboard 面板

## 风险壳

| 项 | 默认 |
|----|------|
| 总预算 / 周 | `$8.08`（8×$1.01）可配 |
| 单场 | `$1.01` |
| 策略总开关 | `CSL_EXPLORE_ENABLED_STRATS` |
| 默认模式 | **dry-run** |

## 验收

1. dry-run 能对本周 8 场产出可复现信号 JSON。  
2. 各策略互不影响 RN1 `sports_pnl.json`。  
3. 关闭某一策略后编排器跳过且测试绿。

## 运行

```bash
.venv/bin/python cli.py run --csl-explore
.venv/bin/python cli.py run --csl-explore --loop --interval 120
# live 仅在显式 --live 且理解风险后
```
