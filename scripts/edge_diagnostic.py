#!/usr/bin/env python3
"""One-shot edge diagnostic: how many NO candidates pass at various MIN_EDGE levels."""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.clients import build_polymarket_clients
from src.strategies.safe_compounder import (
    MIN_ASK_SIZE,
    MIN_NO_ASK,
    SafeCompounder,
    market_confidence_score,
)
from src.strategies.capital_policy import top_ask_depth


async def _scan_edges(compounder: SafeCompounder):
    markets = await compounder._fetch_all_markets()
    candidates = compounder._find_no_candidates(markets)

    near_misses = []
    rejects = Counter()

    for m in candidates:
        ticker = m["ticker"]
        true_no_prob = m["_true_no_prob"]
        try:
            ob_resp = await compounder.client.get_orderbook(ticker, depth=10)
            ob = ob_resp.get("orderbook_fp", ob_resp.get("orderbook", {})) or {}
        except Exception:
            rejects["disconnect"] += 1
            continue
        if not ob:
            rejects["empty_book"] += 1
            continue

        conf_score, _ = market_confidence_score(ticker, ob, m)
        if conf_score < compounder.min_confidence:
            rejects["low_conf"] += 1
            continue

        no_asks = ob.get("no_asks", []) or []
        lowest_no_ask, ask_depth = top_ask_depth(no_asks)
        no_ask_size = 0.0
        if lowest_no_ask is not None:
            try:
                priced = []
                for row in no_asks:
                    p, sz = float(row[0]), float(row[1])
                    if p > 1.0:
                        p /= 100.0
                    if p > 0 and sz > 0:
                        priced.append((p, sz))
                if priced:
                    no_ask_size = min(priced, key=lambda x: x[0])[1]
            except (ValueError, TypeError, IndexError):
                no_ask_size = ask_depth

        if lowest_no_ask is None:
            rejects["no_real_ask"] += 1
            continue
        if no_ask_size < MIN_ASK_SIZE:
            rejects["thin_ask"] += 1
            continue
        if lowest_no_ask < MIN_NO_ASK:
            rejects["ask_below_min"] += 1
            continue

        edge = true_no_prob - lowest_no_ask
        outcome_prices = m.get("_outcome_prices") or (0.0, 0.0)
        yes_last = float(outcome_prices[0]) if outcome_prices else 0.0

        if edge < 0.02:
            rejects["edge_lt_min"] += 1
        near_misses.append({
            "edge": edge,
            "no_ask": lowest_no_ask,
            "yes_last": yes_last,
            "true_no_prob": true_no_prob,
            "title": (m.get("title") or "")[:70],
            "ticker": ticker,
            "volume": int(float(m.get("volume_fp", 0) or m.get("volume", 0) or 0)),
            "days": m.get("_days_to_expiry", 0),
            "ask_depth": ask_depth,
        })

    return len(markets), len(candidates), near_misses, rejects


def _count_at(near_misses, threshold: float) -> int:
    return sum(1 for x in near_misses if x["edge"] >= threshold)


async def main() -> None:
    async with build_polymarket_clients() as (client, gamma):
        sc = SafeCompounder(client=client, gamma=gamma, dry_run=True, min_edge=0.02)
        n_markets, n_cands, near, rejects = await _scan_edges(sc)

    thresholds = [0.01, 0.015, 0.02, 0.025, 0.03, 0.05]
    print("=" * 72)
    print("EDGE DIAGNOSTIC (read-only, no orders)")
    print("=" * 72)
    print(f"Markets scanned:     {n_markets}")
    print(f"NO candidates:       {n_cands} (top 200 by volume/expiry)")
    print(f"With real NO ask:    {len(near)}")
    print(f"Rejects:             {' '.join(f'{k}={v}' for k, v in sorted(rejects.items()))}")
    print()
    print("Pass count by MIN_EDGE (other gates unchanged):")
    for t in thresholds:
        n = _count_at(near, t)
        print(f"  edge >= ${t:.3f}:  {n:3d} candidates")
    print()

    # Edge distribution for near-miss bucket
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
    print("Edge distribution (markets with real NO ask >= $0.80):")
    for label in ["<0", "0-0.5c", "0.5-1c", "1-1.5c", "1.5-2c", "2-3c", ">=3c"]:
        if buckets[label]:
            print(f"  {label:8s}: {buckets[label]}")
    print()

    top = sorted(near, key=lambda x: -x["edge"])[:15]
    print("Top 15 by edge (would trade if MIN_EDGE lowered):")
    for x in top:
        flag = "✓@2c" if x["edge"] >= 0.02 else ("~@1c" if x["edge"] >= 0.01 else "  ")
        print(
            f"  [{flag}] edge ${x['edge']:.3f} | NO ask ${x['no_ask']:.2f} | "
            f"YES ${x['yes_last']:.2f} | {x['days']}d vol:{x['volume']}"
        )
        print(f"         {x['title']}")


if __name__ == "__main__":
    asyncio.run(main())
