#!/usr/bin/env python3
"""One-shot UCL balance-hedge basket (Champions League slate).

Current legs (live ~25' anti-favorite / glue basket):
  - Dortmund vs Villarreal: Draw      ~$2.5
  - Lille vs Betis:         Draw      ~$1.5
  - Madrid vs Inter:        Inter win ~$1.5
  - Porto vs Man City:      Draw      ~$1.5

Uses CSL explore executor/ledger/alerts so −50% SL is watched by polymarket-csl-explore.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from src.clients import build_polymarket_clients
from src.strategies.csl_explore.alerts import notify_csl_cycle_summary, notify_csl_order
from src.strategies.csl_explore.executor import place_yes_buy
from src.strategies.csl_explore.ledger import ExploreLedger

STRATEGY = "ucl_balance"

# (slug, question_substr, usdc, note)
LEGS = [
    ("ucl-bvb-vil-2026-09-08", "end in a draw", 2.5, "bvb_vil_draw"),
    ("ucl-lil-bet-2026-09-08", "end in a draw", 1.5, "lil_bet_draw"),
    ("ucl-rma-int-2026-09-08", "Will FC Internazionale Milano win", 1.5, "rma_int_away"),
    ("ucl-por-mnc-2026-09-08", "end in a draw", 1.5, "por_mnc_draw"),
]


def _parse_prices(m: dict) -> tuple[float, float]:
    prices = m.get("outcomePrices") or m.get("_outcome_prices")
    if isinstance(prices, str):
        prices = json.loads(prices)
    if isinstance(prices, (list, tuple)) and len(prices) >= 2:
        return float(prices[0]), float(prices[1])
    return 0.0, 0.0


def _parse_tokens(m: dict) -> tuple[str, str]:
    toks = m.get("clobTokenIds") or m.get("_token_ids")
    if isinstance(toks, str):
        toks = json.loads(toks)
    if isinstance(toks, (list, tuple)) and len(toks) >= 2:
        return str(toks[0]), str(toks[1])
    return "", ""


async def _load_leg(client, slug: str, substr: str) -> dict:
    import httpx
    from src.strategies.csl_explore.config import GAMMA_HOST

    async with httpx.AsyncClient() as http:
        r = await http.get(f"{GAMMA_HOST}/events/slug/{slug}", timeout=30.0)
        r.raise_for_status()
        event = r.json()
    title = event.get("title") or slug
    for m in event.get("markets") or []:
        q = m.get("question") or ""
        if substr.lower() not in q.lower():
            continue
        yes, _ = _parse_prices(m)
        yt, nt = _parse_tokens(m)
        return {
            "match_title": title,
            "match_slug": slug,
            "question": q,
            "condition_id": str(m.get("conditionId") or ""),
            "yes_token": yt,
            "no_token": nt,
            "yes_price": yes,
            "neg_risk": bool(m.get("negRisk")),
        }
    raise RuntimeError(f"market not found slug={slug} substr={substr!r}")


async def run(*, live: bool) -> None:
    from src.config.settings import settings

    settings.trading.live_trading_enabled = bool(live)
    settings.trading.paper_trading_mode = not bool(live)

    ledger = ExploreLedger()
    filled = set(str(k) for k in (ledger.load_state().get("filled_keys") or []))
    placed = 0
    deployed = 0.0
    by = {}

    print(f"🏆 UCL balance basket ({'LIVE' if live else 'DRY'})", flush=True)

    async with build_polymarket_clients() as (client, gamma):
        for slug, substr, usdc, note in LEGS:
            leg = await _load_leg(client, slug, substr)
            cid = leg["condition_id"]
            key = f"{STRATEGY}:{cid}"
            if key in filled or f"cond:{cid}" in filled:
                print(f"   skip already filled {note} {cid[:16]}", flush=True)
                continue
            px = float(leg["yes_price"])
            print(
                f"   → [{note}] ${usdc:.2f} YES @ {px:.3f} | {leg['question'][:60]}",
                flush=True,
            )
            if not live:
                ledger.append({
                    "kind": "plan",
                    "strategy": STRATEGY,
                    "match_slug": slug,
                    "match_title": leg["match_title"],
                    "question": leg["question"],
                    "condition_id": cid,
                    "usdc": usdc,
                    "limit_price": px,
                    "meta": {"leg": note},
                })
                continue

            shares, fill_px, raw = await place_yes_buy(
                client,
                gamma,
                condition_id=cid,
                price=px,
                usdc=usdc,
                yes_token=leg["yes_token"],
                no_token=leg["no_token"],
                neg_risk=leg["neg_risk"],
            )
            cost = round(shares * fill_px, 4)
            st = ledger.load_state()
            keys = list(st.get("filled_keys") or [])
            for k in (key, f"cond:{cid}"):
                if k not in keys:
                    keys.append(k)
            st["filled_keys"] = keys[-500:]
            # Do not inflate CSL week_spent — UCL is a separate basket.
            ledger.save_state(st)
            ledger.append({
                "kind": "fill",
                "strategy": STRATEGY,
                "match_slug": slug,
                "match_title": leg["match_title"],
                "question": leg["question"],
                "condition_id": cid,
                "yes_token": leg["yes_token"],
                "shares": shares,
                "fill_price": fill_px,
                "cost_usdc": cost,
                "usdc": usdc,
                "limit_price": px,
                "meta": {"leg": note, "basket": "ucl_live_glue"},
                "raw_status": (raw.get("status") if isinstance(raw, dict) else None),
            })
            ledger.record_open({
                "condition_id": cid,
                "strategy": STRATEGY,
                "match_slug": slug,
                "match_title": leg["match_title"],
                "question": leg["question"],
                "yes_token": leg["yes_token"],
                "shares": shares,
                "entry_price": fill_px,
                "cost_usdc": cost,
            })
            notify_csl_order(
                strategy=STRATEGY,
                match_title=leg["match_title"],
                question=leg["question"],
                reason=f"ucl_balance {note}",
                shares=shares,
                price=fill_px,
                cost_usdc=cost,
                live=True,
                condition_id=cid,
                match_slug=slug,
                meta={"leg": note},
            )
            print(f"   LIVE OK x{shares} @ ${fill_px:.2f} ≈ ${cost:.2f}", flush=True)
            placed += 1
            deployed += cost
            by[STRATEGY] = by.get(STRATEGY, 0) + 1

    if live and placed:
        notify_csl_cycle_summary(
            live=True,
            placed=placed,
            deployed_usdc=deployed,
            week_spent=deployed,
            by_strategy=by,
        )
    print(f"done placed={placed} deployed=${deployed:.2f}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="UCL tonight balance hedge basket")
    ap.add_argument("--live", action="store_true", help="Place real orders")
    args = ap.parse_args()
    asyncio.run(run(live=args.live))


if __name__ == "__main__":
    main()
