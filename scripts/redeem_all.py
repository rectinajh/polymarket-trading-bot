#!/usr/bin/env python3
"""Redeem all redeemable Polymarket positions via Relayer (proxy/deposit wallet)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from src.clients import build_polymarket_clients
from src.clients.relayer_redeem import redeem_all_redeemable, relayer_configured


async def _run(*, dry_run: bool) -> int:
    if not relayer_configured():
        print(
            "[Polymarket] Missing POLYMARKET_RELAYER_API_KEY or "
            "POLYMARKET_RELAYER_API_KEY_ADDRESS in .env",
            file=sys.stderr,
        )
        return 1

    async with build_polymarket_clients() as (client, _gamma):
        wallet = client._get_funding_address()
        resp = await client.get_positions()
        positions = resp.get("market_positions") or []
        stats = await redeem_all_redeemable(
            private_key=client.private_key,
            wallet=wallet,
            positions=positions,
            dry_run=dry_run,
        )

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"[Polymarket] Redeem scan ({mode})")
    print(f"  wallet: {wallet}")
    print(f"  checked: {stats['checked']} redeemable: {stats['redeemable']}")
    if not dry_run:
        print(f"  redeemed: {stats['redeemed']} errors: {stats['errors']}")
    for row in stats.get("results") or []:
        if row.get("dry_run"):
            print(f"  [DRY] would redeem {row.get('title')} ({row.get('condition_id', '')[:18]}…)")
        elif row.get("tx_hash"):
            print(f"  ✅ {row.get('condition_id', '')[:18]}… tx={row.get('tx_hash')}")
        elif row.get("error"):
            print(f"  ❌ {row.get('condition_id', '')[:18]}… {row.get('error')}")

    return 0 if stats.get("errors", 0) == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket Relayer auto-redeem")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List redeemable positions without submitting",
    )
    args = parser.parse_args()
    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
