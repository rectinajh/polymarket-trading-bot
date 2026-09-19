"""Place minimum-size YES buys for CSL explore signals."""

from __future__ import annotations

import uuid
from math import ceil
from typing import Any, Optional, Tuple

from src.strategies.csl_explore.config import MIN_SHARES, ORDER_USDC


def min_share_count(price: float, *, usdc: float = ORDER_USDC, min_shares: int = MIN_SHARES) -> int:
    """CLOB floor: max(min_shares, ceil(usdc / price))."""
    if price <= 0:
        return 0
    want = max(min_shares, int(ceil(usdc / price - 1e-12)))
    while want * price + 1e-9 < usdc:
        want += 1
    # Safety: never spend > $5 on a "min" explore order
    if want * price > 5.0 + 1e-9:
        return 0
    return want


async def ensure_registered(
    client: Any,
    gamma: Any,
    condition_id: str,
    *,
    yes_token: str = "",
    no_token: str = "",
    neg_risk: bool = False,
) -> None:
    if not hasattr(client, "register_market"):
        return
    yt, nt = yes_token, no_token
    tick = 0.01
    nr = neg_risk
    if (not yt or not nt) and gamma is not None:
        yt, nt = await gamma.get_token_ids(condition_id)
    if gamma is not None:
        try:
            m = await gamma.get_market(condition_id)
            nr = bool(m.get("negRisk") or m.get("negativeRisk") or neg_risk)
            tick = float(
                m.get("orderPriceMinTickSize") or m.get("minimum_tick_size") or 0.01
            )
        except Exception:
            pass
    if not yt or not nt:
        raise RuntimeError(f"missing token ids for {condition_id[:16]}")
    client.register_market(condition_id, yt, nt, neg_risk=nr, tick_size=tick)


async def place_yes_buy(
    client: Any,
    gamma: Any,
    *,
    condition_id: str,
    price: float,
    usdc: float = ORDER_USDC,
    yes_token: str = "",
    no_token: str = "",
    neg_risk: bool = False,
    aggressive_ticks: int = 1,
) -> Tuple[int, float, Any]:
    """Limit BUY YES at price + aggressive_ticks (cents). Returns (shares, limit_px, raw)."""
    shares = min_share_count(price, usdc=usdc)
    if shares <= 0:
        raise RuntimeError(f"size_zero at price={price}")
    await ensure_registered(
        client, gamma, condition_id,
        yes_token=yes_token, no_token=no_token, neg_risk=neg_risk,
    )
    # Slightly aggressive for thin CSL books (still limit/GTC).
    px = min(0.99, max(0.01, price + 0.01 * max(0, aggressive_ticks)))
    price_cents = max(1, min(99, int(round(px * 100))))
    raw = await client.place_order(
        ticker=condition_id,
        client_order_id=str(uuid.uuid4()),
        side="yes",
        action="buy",
        count=shares,
        type_="limit",
        yes_price=price_cents,
    )
    return shares, price_cents / 100.0, raw


async def place_yes_sell(
    client: Any,
    gamma: Any,
    *,
    condition_id: str,
    price: float,
    shares: int,
    yes_token: str = "",
    no_token: str = "",
    neg_risk: bool = False,
    aggressive_ticks: int = 1,
    market: bool = False,
) -> Tuple[int, float, Any]:
    """SELL YES. Limit at price − aggressive_ticks, or marketable FOK when
    ``market=True`` (stop-loss: take the best bid now instead of resting a
    limit order that never fills in a thin book)."""
    if shares <= 0:
        raise RuntimeError("sell size_zero")
    await ensure_registered(
        client, gamma, condition_id,
        yes_token=yes_token, no_token=no_token, neg_risk=neg_risk,
    )
    # Slightly aggressive sell (below mark) for thin books.
    px = min(0.99, max(0.01, price - 0.01 * max(0, aggressive_ticks)))
    price_cents = max(1, min(99, int(round(px * 100))))
    raw = await client.place_order(
        ticker=condition_id,
        client_order_id=str(uuid.uuid4()),
        side="yes",
        action="sell",
        count=int(shares),
        type_="market" if market else "limit",
        yes_price=price_cents,
    )
    return int(shares), price_cents / 100.0, raw
