"""Proxy / deposit wallet redeem via Polymarket Relayer (gasless)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def relayer_configured() -> bool:
    key = (os.getenv("POLYMARKET_RELAYER_API_KEY") or "").strip()
    addr = (os.getenv("POLYMARKET_RELAYER_API_KEY_ADDRESS") or "").strip()
    return bool(key and addr)


def _relayer_api_key():
    from polymarket import RelayerApiKey

    return RelayerApiKey(
        key=os.environ["POLYMARKET_RELAYER_API_KEY"].strip(),
        address=os.environ["POLYMARKET_RELAYER_API_KEY_ADDRESS"].strip(),
    )


async def redeem_via_relayer(
    *,
    private_key: str,
    wallet: str,
    condition_id: str,
    metadata: Optional[str] = None,
) -> Dict[str, Any]:
    """Redeem resolved positions for a deposit/proxy wallet via Relayer API."""
    if not relayer_configured():
        raise RuntimeError(
            "POLYMARKET_RELAYER_API_KEY and POLYMARKET_RELAYER_API_KEY_ADDRESS required"
        )
    if not condition_id:
        raise ValueError("condition_id required")

    from polymarket import AsyncSecureClient

    tag = metadata or f"Polymarket redeem {condition_id[:18]}"
    logger.info(
        "[Polymarket] Relayer redeem start condition=%s wallet=%s",
        condition_id[:18],
        wallet[:18],
    )

    client = await AsyncSecureClient.create(
        private_key=private_key.strip(),
        wallet=wallet.strip(),
        api_key=_relayer_api_key(),
    )
    try:
        handle = await client.redeem_positions(
            condition_id=condition_id,
            metadata=tag,
        )
        outcome = await handle.wait()
        tx_hash = getattr(outcome, "transaction_hash", None) or getattr(
            outcome, "transactionHash", None
        )
        tx_id = getattr(outcome, "transaction_id", None) or getattr(
            outcome, "transactionId", None
        )
        result = {
            "condition_id": condition_id,
            "tx_hash": str(tx_hash) if tx_hash else None,
            "transaction_id": str(tx_id) if tx_id else None,
            "via": "relayer",
        }
        logger.info(
            "[Polymarket] Relayer redeem ok condition=%s tx=%s",
            condition_id[:18],
            result.get("tx_hash"),
        )
        return result
    finally:
        await client.close()


async def redeem_all_redeemable(
    *,
    private_key: str,
    wallet: str,
    positions: list,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Redeem every redeemable position in a positions API list."""
    stats = {"checked": 0, "redeemable": 0, "redeemed": 0, "errors": 0, "results": []}
    seen: set = set()
    for pos in positions:
        stats["checked"] += 1
        if not pos.get("redeemable"):
            continue
        size = float(pos.get("size", 0) or 0)
        if abs(size) < 0.001:
            continue
        cond = pos.get("condition_id") or pos.get("ticker") or ""
        if not cond or cond in seen:
            continue
        seen.add(cond)
        stats["redeemable"] += 1
        title = (pos.get("title") or cond[:24])[:60]
        if dry_run:
            stats["results"].append({"condition_id": cond, "title": title, "dry_run": True})
            continue
        try:
            result = await redeem_via_relayer(
                private_key=private_key,
                wallet=wallet,
                condition_id=cond,
                metadata=f"Polymarket auto-redeem: {title}",
            )
            stats["redeemed"] += 1
            stats["results"].append(result)
        except Exception as exc:
            stats["errors"] += 1
            logger.warning("[Polymarket] Relayer redeem failed %s: %s", cond[:18], exc)
            stats["results"].append(
                {"condition_id": cond, "error": str(exc)[:300]},
            )
    return stats
