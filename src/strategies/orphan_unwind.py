"""Track and force-unwind single-leg completeness orphans."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ORPHAN_REGISTRY_PATH = Path("data") / "orphan_positions.json"
ORPHAN_FORCE_AFTER_S = 120
UNWIND_DISCOUNT_CENTS = (2, 5, 10, 20, 30)


class OrphanRegistry:
    """Persist orphaned single-leg fills until unwound or force-sold."""

    def __init__(self, path: Path = ORPHAN_REGISTRY_PATH):
        self.path = path

    def _load(self) -> Dict[str, Any]:
        try:
            if not self.path.exists():
                return {"orphans": []}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {"orphans": []}
            orphans = raw.get("orphans")
            return {"orphans": orphans if isinstance(orphans, list) else []}
        except (OSError, json.JSONDecodeError):
            return {"orphans": []}

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> List[Dict[str, Any]]:
        return list(self._load().get("orphans") or [])

    def register(
        self,
        *,
        condition_id: str,
        side: str,
        quantity: int,
        entry_cents: int,
        strategy: str,
        slug: str = "",
        asset: str = "",
        window_end: Optional[int] = None,
    ) -> None:
        if not condition_id or quantity < 1:
            return
        side = side.lower()
        if side not in ("yes", "no"):
            return
        data = self._load()
        orphans: List[Dict[str, Any]] = [
            o for o in (data.get("orphans") or [])
            if (o.get("condition_id") or "") != condition_id
        ]
        orphans.append(
            {
                "condition_id": condition_id,
                "side": side,
                "quantity": int(quantity),
                "entry_cents": int(entry_cents),
                "strategy": strategy,
                "slug": slug,
                "asset": asset,
                "window_end": window_end,
                "opened_at": int(time.time()),
                "attempts": 0,
            }
        )
        data["orphans"] = orphans
        self._save(data)
        logger.warning(
            "ORPHAN registered %s %s x%s %s (%s)",
            side.upper(), condition_id[:18], quantity, slug or strategy, strategy,
        )

    def remove(self, condition_id: str) -> None:
        data = self._load()
        data["orphans"] = [
            o for o in (data.get("orphans") or [])
            if (o.get("condition_id") or "") != condition_id
        ]
        self._save(data)


async def _position_qty(client, condition_id: str, side: str) -> int:
    try:
        resp = await client.get_positions(condition_id=condition_id)
        positions = resp.get("market_positions") or []
    except Exception:
        return 0
    side = side.lower()
    for pos in positions:
        ps = (pos.get("side") or "").lower()
        if ps != side:
            continue
        return max(0, int(round(float(pos.get("size", 0) or 0))))
    return 0


async def _best_bid_cents(client, condition_id: str, side: str) -> Optional[int]:
    try:
        book_resp = await client.get_orderbook(condition_id, depth=3)
        book = (book_resp or {}).get("orderbook") or {}
        bids = book.get(side, []) or []
        if not bids:
            return None
        best = max(float(level[0]) for level in bids)
        if best > 1.0:
            best = best / 100.0
        return max(1, int(round(best * 100)))
    except Exception as exc:
        logger.info("orphan bid lookup failed %s: %s", condition_id[:18], exc)
        return None


async def _sell_leg(
    client,
    *,
    condition_id: str,
    side: str,
    quantity: int,
    price_cents: int,
    dry_run: bool,
) -> bool:
    if quantity < 1:
        return True
    if dry_run:
        print(
            f"  [DRY] orphan sell {side.upper()} x{quantity} @ ${price_cents/100:.2f} "
            f"{condition_id[:18]}…",
            flush=True,
        )
        return True
    kwargs: Dict[str, Any] = {
        "ticker": condition_id,
        "client_order_id": str(uuid.uuid4()),
        "side": side,
        "action": "sell",
        "count": quantity,
        "type_": "market",
    }
    if side == "yes":
        kwargs["yes_price"] = price_cents
    else:
        kwargs["no_price"] = price_cents
    try:
        await client.place_order(**kwargs)
        return True
    except Exception as exc:
        logger.warning("orphan sell failed %s: %s", condition_id[:18], exc)
        return False


async def process_orphans(
    client,
    *,
    dry_run: bool = False,
    min_seconds_left: int = 90,
    registry: Optional[OrphanRegistry] = None,
) -> Dict[str, int]:
    """Retry unwind for registered orphans; force-sell before window end or after timeout."""
    reg = registry or OrphanRegistry()
    stats = {
        "pending": 0,
        "unwound": 0,
        "forced": 0,
        "cleared": 0,
        "failed": 0,
    }
    now = int(time.time())
    orphans = reg.list()
    stats["pending"] = len(orphans)
    if not orphans:
        return stats

    for orphan in orphans:
        cond = orphan.get("condition_id") or ""
        side = (orphan.get("side") or "yes").lower()
        qty = int(orphan.get("quantity") or 0)
        entry_cents = int(orphan.get("entry_cents") or 0)
        window_end = orphan.get("window_end")
        opened_at = int(orphan.get("opened_at") or now)
        tag = orphan.get("slug") or orphan.get("strategy") or cond[:18]

        live_qty = await _position_qty(client, cond, side)
        if live_qty < 1:
            reg.remove(cond)
            stats["cleared"] += 1
            continue

        qty = min(qty, live_qty)
        age_s = now - opened_at
        force = age_s >= ORPHAN_FORCE_AFTER_S
        if isinstance(window_end, int) and window_end > now:
            left = window_end - now
            if 0 < left <= min_seconds_left:
                force = True

        bid = await _best_bid_cents(client, cond, side)
        if bid is None and not force:
            stats["failed"] += 1
            continue

        discounts = list(UNWIND_DISCOUNT_CENTS)
        if force:
            discounts = discounts + [40, 50]

        sold = False
        for disc in discounts:
            base = bid if bid is not None else max(1, entry_cents)
            price = max(1, base - disc)
            if entry_cents > 0:
                price = min(price, max(1, entry_cents - disc))
            print(
                f"  {'⚡ FORCE' if force else '↩️'} orphan unwind {side.upper()} x{qty} "
                f"@ ${price/100:.2f} | {tag}",
                flush=True,
            )
            if await _sell_leg(
                client, condition_id=cond, side=side, quantity=qty,
                price_cents=price, dry_run=dry_run,
            ):
                remaining = await _position_qty(client, cond, side)
                if remaining < 1 or dry_run:
                    reg.remove(cond)
                    if force:
                        stats["forced"] += 1
                    else:
                        stats["unwound"] += 1
                    sold = True
                    break
        if not sold:
            stats["failed"] += 1

    return stats


def register_partial_fail(
    *,
    condition_id: str,
    filled_side: str,
    quantity: int,
    entry_cents: int,
    strategy: str,
    slug: str = "",
    asset: str = "",
    window_end: Optional[int] = None,
    registry: Optional[OrphanRegistry] = None,
) -> None:
    """Register a single filled leg after pair execution failed."""
    (registry or OrphanRegistry()).register(
        condition_id=condition_id,
        side=filled_side,
        quantity=quantity,
        entry_cents=entry_cents,
        strategy=strategy,
        slug=slug,
        asset=asset,
        window_end=window_end,
    )
