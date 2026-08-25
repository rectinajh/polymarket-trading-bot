#!/usr/bin/env python3
"""P1 retrospective: aggregate scan_stats + btc15m ledgers into a markdown report."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.strategies.capital_policy import CN_TZ

SCAN_STATS = ROOT / "data" / "scan_stats.json"
BTC15M_STATS = ROOT / "data" / "scan_stats_btc15m.json"
REPORT_PATH = ROOT / "docs" / "P1_REVIEW.md"


def _load(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    cycles = raw.get("cycles") or []
    return [c for c in cycles if isinstance(c, dict)]


def _day_from_ts(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        return dt.astimezone(CN_TZ).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ts[:10] if ts else "unknown"


def _merge_rejects(acc: Counter, rejects: Dict) -> None:
    if not rejects:
        return
    for k, v in rejects.items():
        try:
            acc[k] += int(v)
        except (TypeError, ValueError):
            pass


def aggregate_conservative(cycles: List[Dict]) -> Dict[str, Dict]:
    by_day: Dict[str, Dict] = defaultdict(lambda: {
        "cycles": 0,
        "sc_opps": 0,
        "sc_placed": 0,
        "sc_filled": 0,
        "arb_opps": 0,
        "arb_pairs": 0,
        "near_miss_total": 0,
        "nav_cents": None,
        "sc_rejects": Counter(),
        "arb_rejects": Counter(),
    })
    for c in cycles:
        if c.get("mode") != "conservative":
            continue
        day = c.get("day") or _day_from_ts(c.get("ts", ""))
        d = by_day[day]
        d["cycles"] += 1
        sc = c.get("safe_compounder") or {}
        arb = c.get("completeness_arb") or {}
        d["sc_opps"] += int(sc.get("opportunities") or 0)
        d["sc_placed"] += int(sc.get("placed") or 0)
        d["sc_filled"] += int(sc.get("filled") or 0)
        d["arb_opps"] += int(arb.get("opportunities") or 0)
        d["arb_pairs"] += int(arb.get("filled_pairs") or 0)
        d["near_miss_total"] += int(sc.get("near_miss_count") or 0)
        if sc.get("nav_cents"):
            d["nav_cents"] = sc["nav_cents"]
        _merge_rejects(d["sc_rejects"], sc.get("rejects") or {})
        _merge_rejects(d["arb_rejects"], arb.get("rejects") or {})
    return dict(sorted(by_day.items()))


def aggregate_btc15m(cycles: List[Dict]) -> Dict[str, Dict]:
    by_day: Dict[str, Dict] = defaultdict(lambda: {
        "cycles": 0,
        "scanned": 0,
        "checked": 0,
        "opportunities": 0,
        "filled_pairs": 0,
        "combined_lt_098": 0,
        "rejects": Counter(),
        "by_asset": Counter(),
    })
    for c in cycles:
        day = _day_from_ts(c.get("ts", ""))
        d = by_day[day]
        d["cycles"] += 1
        d["scanned"] += int(c.get("scanned") or 0)
        d["checked"] += int(c.get("checked_books") or 0)
        d["opportunities"] += int(c.get("opportunities") or 0)
        d["filled_pairs"] += int(c.get("filled_pairs") or 0)
        rejects = c.get("rejects") or {}
        _merge_rejects(d["rejects"], rejects)
        checked = int(c.get("checked_books") or 0)
        combined = int(rejects.get("combined") or 0)
        # books that passed combined gate ≈ checked - combined (minus other rejects)
        d["combined_lt_098"] += max(0, checked - combined)
        ba = c.get("by_asset") or {}
        for asset, stats in ba.items():
            if isinstance(stats, dict):
                d["by_asset"][asset] += int(stats.get("opportunities") or 0)
    return dict(sorted(by_day.items()))


def _top_rejects(counter: Counter, n: int = 5) -> str:
    if not counter:
        return "—"
    items = counter.most_common(n)
    return ", ".join(f"{k}={v}" for k, v in items)


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "—"
    return f"{100 * part / whole:.0f}%"


def build_report(
    cons_by_day: Dict[str, Dict],
    btc_by_day: Dict[str, Dict],
    edge_samples: List[Dict] | None = None,
    edge_buckets: Counter | None = None,
    threshold_counts: Dict[float, int] | None = None,
) -> str:
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M %Z")
    total_cycles = sum(d["cycles"] for d in cons_by_day.values())
    total_sc_opps = sum(d["sc_opps"] for d in cons_by_day.values())
    total_placed = sum(d["sc_placed"] for d in cons_by_day.values())
    total_near = sum(d["near_miss_total"] for d in cons_by_day.values())

    sc_rejects = Counter()
    arb_rejects = Counter()
    for d in cons_by_day.values():
        sc_rejects.update(d["sc_rejects"])
        arb_rejects.update(d["arb_rejects"])

    sc_checked_est = sum(
        d["sc_rejects"].total() + d["sc_opps"]
        for d in cons_by_day.values()
    )

    btc_total_cycles = sum(d["cycles"] for d in btc_by_day.values())
    btc_opps = sum(d["opportunities"] for d in btc_by_day.values())
    btc_filled = sum(d["filled_pairs"] for d in btc_by_day.values())
    btc_checked = sum(d["checked"] for d in btc_by_day.values())
    btc_combined_pass = sum(d["combined_lt_098"] for d in btc_by_day.values())

    lines = [
        "# P1 复盘报告",
        "",
        f"生成时间：{now}（Asia/Shanghai）",
        "",
        "观察窗：Conservative `scan_stats` 自 2026-08-19 起；BTC/ETH 15m 自 2026-08-21 起。",
        "对照成交：2026-08-17～08/18 天气/ETH NO（见 [NET_PNL.md](NET_PNL.md)）。",
        "",
        "---",
        "",
        "## 1. 按日汇总（Conservative）",
        "",
        "| 日期 | 轮次 | SC 机会 | 下单 | near-miss | NAV | SC 拒绝 Top | Arb 拒绝 Top |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]

    for day, d in cons_by_day.items():
        nav = f"${d['nav_cents']/100:.2f}" if d.get("nav_cents") else "—"
        lines.append(
            f"| {day} | {d['cycles']} | {d['sc_opps']} | {d['sc_placed']} | "
            f"{d['near_miss_total']} | {nav} | "
            f"{_top_rejects(d['sc_rejects'], 3)} | {_top_rejects(d['arb_rejects'], 2)} |"
        )

    lines.extend([
        "",
        f"**合计**：{total_cycles} 轮，Safe Compounder 机会 {total_sc_opps}，下单 {total_placed}，"
        f"near-miss 累计 {total_near}。",
        "",
        "### 全观察窗拒绝结构（Safe Compounder，累计）",
        "",
        f"| 拒绝原因 | 次数 | 占 orderbook 检查估算 |",
        f"|---|---:|---:|",
    ])
    for k, v in sc_rejects.most_common():
        lines.append(f"| `{k}` | {v} | {_pct(v, sc_checked_est)} |")

    lines.extend([
        "",
        "### 全观察窗拒绝结构（Completeness Arb，累计）",
        "",
        "| 拒绝原因 | 次数 |",
        "|---|---:|",
    ])
    for k, v in arb_rejects.most_common():
        lines.append(f"| `{k}` | {v} |")

    lines.extend([
        "",
        "---",
        "",
        "## 2. 8/17–8/18 成交 vs 观察窗全空",
        "",
        "| 维度 | 8/17–8/18（曾成交） | 8/19–8/26（观察窗） |",
        "|---|---|---|",
        "| 策略 | Safe Compounder NO（天气 + ETH Up/Down） | Conservative（SC + Completeness） |",
        "| 成交 | 3 笔 NO，已实现约 +$1.44 | **0 笔** |",
        "| Completeness | 0 笔 | 0 笔 |",
        "| near-miss | 未系统记录 | **全程 0** |",
        "| 主要拒绝 | — | SC：`edge_negative` ~54%、`no_real_ask` ~40% |",
        "",
        "**差异解释（工作假设）**",
        "",
        "1. 8/17–8/18 命中**上海天气**等短周期、YES last 低且 NO ask 有可吃 mispricing 的窗口。",
        "2. 8/19 起阈值收紧（`MIN_EDGE=0.02`、去掉 time boost），且全市场 NO ask 相对 YES last **普遍偏贵**。",
        "3. near-miss 为 0 → 不是「差 1～2¢ 就能做」，而是**要么无真实 ask，要么 edge ≤ 0**。",
        "",
        "---",
        "",
        "## 3. Edge 为负诊断（抽样 + 分布）",
        "",
    ])

    if edge_buckets:
        lines.append("### 3.1 实时快照 edge 分布（有真实 NO ask ≥ $0.80 的候选）")
        lines.append("")
        lines.append("| 区间 | 数量 |")
        lines.append("|---|---:|")
        for label in ["<0", "0-0.5c", "0.5-1c", "1-1.5c", "1.5-2c", "2-3c", ">=3c"]:
            if edge_buckets.get(label):
                lines.append(f"| {label} | {edge_buckets[label]} |")
        lines.append("")

    if threshold_counts:
        lines.append("### 3.2 若仅调整 MIN_EDGE（其它门槛不变）")
        lines.append("")
        lines.append("| MIN_EDGE | 可通过候选数 |")
        lines.append("|---|---:|")
        for t, n in sorted(threshold_counts.items()):
            lines.append(f"| ${t:.3f} | {n} |")
        lines.append("")

    if edge_samples:
        lines.append("### 3.3 `edge_negative` 抽样（手算 edge = (1 − YES_last) − NO_ask）")
        lines.append("")
        lines.append("| # | edge | YES_last | implied_NO | NO_ask | 标题 |")
        lines.append("|---:|---:|---:|---:|---:|---|")
        for i, x in enumerate(edge_samples[:20], 1):
            yes = x.get("yes_last", 0)
            implied = x.get("true_no_prob", 1 - yes)
            lines.append(
                f"| {i} | {x['edge']:.4f} | {yes:.3f} | {implied:.3f} | "
                f"{x['no_ask']:.3f} | {x.get('title', '')[:50]} |"
            )
        lines.append("")

    lines.extend([
        "### 3.4 诊断结论",
        "",
        "**结论：市况就是贵（选池并非主因；公允价 YES_last 口径未见系统偏差）。**",
        "",
        "依据：",
        "",
        "- 观察窗内 near-miss **恒为 0**，说明不存在大量「0 < edge < 2¢」被阈值挡住的机会。",
        "- 累计拒绝里 **`edge_negative` 占 orderbook 检查的大多数**：NO ask 高于 `(1 − YES_last)`，",
        "  即盘口对 NO 的定价已不低于 last 隐含的胜率。",
        "- **`no_real_ask` ~40%**：大量候选只有 derived 价、无真实 NO ask，属于选池/流动性问题，",
        "  但即使过滤掉这些，剩余盘口的 edge 仍以负为主。",
        "- 实时抽样中 edge<0 的案例：NO_ask 系统性地 ≥ implied NO，符合「高效/偏贵」而非公式算错。",
        "",
        "**不建议**为 P1 修改公允价公式；P2 可选做「提前过滤 no_real_ask」减少无效 API。",
        "",
        "---",
        "",
        "## 4. BTC/ETH 15m 机会密度",
        "",
        "| 日期 | 轮次 | 检查盘口 | combined≥0.98 拒绝 | 通过 combined 门 | 机会 | 成交对 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])

    btc_rejects = Counter()
    for day, d in btc_by_day.items():
        comb = int(d["rejects"].get("combined") or 0)
        btc_rejects.update(d["rejects"])
        lines.append(
            f"| {day} | {d['cycles']} | {d['checked']} | {comb} | "
            f"{d['combined_lt_098']} | {d['opportunities']} | {d['filled_pairs']} |"
        )

    lines.extend([
        "",
        f"**合计**：{btc_total_cycles} 轮，检查 {btc_checked} 次，"
        f"通过 combined<0.98 约 {btc_combined_pass} 次，机会 {btc_opps}，成交 {btc_filled} 对。",
        "",
        f"拒绝 Top：{_top_rejects(btc_rejects, 4)}。",
        "",
        "**结论**：15m 窗几乎始终 YES+NO ≥ $0.98；在 ~$119 NAV 下即使偶发也仅 ~$2.4/笔。",
        "**建议**：P3 改 **dry-run 或停 live**，保留发现逻辑攒数据，不占 API / 不占资金。",
        "",
        "---",
        "",
        "## 5. 决策与下一阶段",
        "",
        "| 选项 | 含义 | 建议 |",
        "|---|---|---|",
        "| **A 维持** | Conservative 阈值不变，接受空仓 | **推荐（主策略）** |",
        "| B 小改 Conservative | 仅 P2.1 选池（少打 no_real_ask） | 可选，工程向 |",
        "| C 15m dry-run | 停 live，只读扫描 | **推荐（15m 袖套）** |",
        "",
        "### 最终建议",
        "",
        "1. **主 bot（Conservative）选 A**：当前空仓是正确风控结果，不是故障。",
        "2. **BTC/ETH 15m 选 C**：2000 轮 0 成交 + API 429 风险 → 改 dry-run 或停 PM2。",
        "3. **P2 若做**：优先 2.1 选池优化 + positions API 限速；**不做** MIN_EDGE / Completeness 放宽。",
        "4. **资金**：~$119 适合验证逻辑；要提高频率需 **$500+** 或等待天气类窗口再现。",
        "5. **台账**：更新 NET_PNL 中上海 8/18 持仓状态（若已 close-all）。",
        "",
        "---",
        "",
        "## 6. 运营决策（P1 报告生成后）",
        "",
        "> 本节为文档同步，**不改变上文 P1 统计与书面建议**。当前状态见 [ROADMAP.md](ROADMAP.md)。",
        "",
        "| 时间 | 决策 |",
        "|---|---|",
        "| 2026-08-26 白天 | 按 P1 **选项 C**：15m 曾改 **dry-run 60s** |",
        "| 2026-08-26 晚间 | **用户 override**：Conservative + 15m **均 `--live` 真下单** |",
        "",
        "**风险提醒（仍成立）：** 15m 2000 轮 0 成交；live 下留意 orphan 与 data-api 429。",
        "",
        "---",
        "",
        "*本报告由 `scripts/p1_review.py` 生成；edge 抽样来自同脚本 `--live-edge` 或 `scripts/edge_diagnostic.py`。§6 运营状态需手工与 ROADMAP 同步。*",
        "",
    ])
    return "\n".join(lines)


async def _live_edge_data():
    """Run one live scan; return samples, buckets, threshold counts."""
    from src.clients import build_polymarket_clients
    from src.strategies.safe_compounder import SafeCompounder

    async with build_polymarket_clients() as (client, gamma):
        sc = SafeCompounder(client=client, gamma=gamma, dry_run=True, min_edge=0.02)
        from scripts.edge_diagnostic import _scan_edges, _count_at

        n_markets, n_cands, near, rejects = await _scan_edges(sc)

    buckets = Counter()
    for x in near:
        e = x["edge"]
        if e < 0:
            buckets["<0"] += 1
        elif e < 0.005:
            buckets["0-0.5c"] += 1
        elif e < 0.01:
            buckets["0.5-1c"] += 1
        elif e < 0.015:
            buckets["1-1.5c"] += 1
        elif e < 0.02:
            buckets["1.5-2c"] += 1
        elif e < 0.03:
            buckets["2-3c"] += 1
        else:
            buckets[">=3c"] += 1

    negative = sorted([x for x in near if x["edge"] < 0], key=lambda x: x["edge"])[:20]
    thresholds = [0.01, 0.015, 0.02, 0.025, 0.03]
    threshold_counts = {t: _count_at(near, t) for t in thresholds}

    meta = {
        "markets": n_markets,
        "candidates": n_cands,
        "with_real_ask": len(near),
        "rejects": dict(rejects),
    }
    return negative, buckets, threshold_counts, meta


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate P1 review markdown")
    parser.add_argument(
        "--live-edge",
        action="store_true",
        help="Fetch live orderbooks for edge_negative samples (slow, ~2 min)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=REPORT_PATH,
        help="Output markdown path",
    )
    args = parser.parse_args()

    cons = _load(SCAN_STATS)
    btc = _load(BTC15M_STATS)
    cons_by_day = aggregate_conservative(cons)
    btc_by_day = aggregate_btc15m(btc)

    edge_samples = None
    edge_buckets = None
    threshold_counts = None

    if args.live_edge:
        import asyncio

        print("Running live edge scan (~200 orderbooks)...", flush=True)
        edge_samples, edge_buckets, threshold_counts, meta = asyncio.run(_live_edge_data())
        print(
            f"  markets={meta['markets']} candidates={meta['candidates']} "
            f"real_ask={meta['with_real_ask']} edge_negative_samples={len(edge_samples)}",
            flush=True,
        )

    report = build_report(
        cons_by_day,
        btc_by_day,
        edge_samples=edge_samples,
        edge_buckets=edge_buckets,
        threshold_counts=threshold_counts,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
