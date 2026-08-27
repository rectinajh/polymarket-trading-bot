"""RN1 wallet trade + open-position feed (Polymarket data-api)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from src.strategies.sports.config import (
    COPY_MAX_USDC,
    RN1_LOOKBACK_HOURS,
    RN1_PROXY_WALLET,
)
from src.strategies.sports.soccer_filter import is_soccer_market

DEFAULT_DATA_HOST = "https://data-api.polymarket.com"
MAX_TRADE_PAGES = 5
TRADES_PER_PAGE = 100


@dataclass(frozen=True)
class Rn1Trade:
    condition_id: str
    event_key: str
    side: str
    outcome: str
    outcome_index: int
    price: float
    size: float
    timestamp: float
    title: str
    asset: str = ""
    tx_hash: str = ""
    slug: str = ""
    event_slug: str = ""
    neg_risk: bool = False

    def copy_key(self) -> str:
        if self.tx_hash:
            return f"tx:{self.tx_hash.lower()}"
        return (
            f"{self.condition_id}:{self.side}:{self.outcome_index}:"
            f"{self.price:.4f}:{self.size:.4f}:{int(self.timestamp)}"
        )

    def position_key(self) -> str:
        return f"pos:{self.condition_id}:{self.outcome_index}"

    @property
    def is_soccer(self) -> bool:
        return is_soccer_market(self.title, self.slug, self.event_slug)


@dataclass(frozen=True)
class Rn1Position:
    condition_id: str
    outcome: str
    outcome_index: int
    size: float
    avg_price: float
    cur_price: float
    title: str
    slug: str = ""
    event_slug: str = ""
    asset: str = ""
    neg_risk: bool = False
    end_date: str = ""

    def position_key(self) -> str:
        return f"pos:{self.condition_id}:{self.outcome_index}"

    @property
    def is_soccer(self) -> bool:
        return is_soccer_market(self.title, self.slug, self.event_slug)

    @property
    def copy_price(self) -> float:
        """Prefer live mid/last; fall back to RN1 avg entry."""
        if self.cur_price > 0:
            return self.cur_price
        return self.avg_price


def copy_share_count(price: float, max_usdc: float = COPY_MAX_USDC) -> int:
    """Exact CLOB-minimum size: max(5 shares, ceil($1.01 / price)).

    ``max_usdc`` is unused for sizing (kept for call-site compat); spend is
    whatever the exchange floor requires, capped by ``COPY_HARD_MAX_USDC``.
    """
    from math import ceil

    from src.strategies.sports.config import (
        COPY_HARD_MAX_USDC,
        COPY_MIN_NOTIONAL,
        COPY_MIN_SHARES,
    )

    if price <= 0:
        return 0
    want = max(
        COPY_MIN_SHARES,
        int(ceil(COPY_MIN_NOTIONAL / price - 1e-12)),
    )
    while want * price + 1e-9 < COPY_MIN_NOTIONAL:
        want += 1
    if want * price > COPY_HARD_MAX_USDC + 1e-9:
        return 0
    return want


def _parse_trade(row: Dict[str, Any]) -> Optional[Rn1Trade]:
    cond = str(row.get("conditionId") or row.get("condition_id") or "").lower()
    if not cond:
        return None
    try:
        ts = float(row.get("timestamp") or 0)
        price = float(row.get("price") or 0)
        size = float(row.get("size") or 0)
        outcome_index = int(row.get("outcomeIndex") if row.get("outcomeIndex") is not None else -1)
    except (TypeError, ValueError):
        return None
    if ts <= 0 or price <= 0:
        return None
    if outcome_index not in (0, 1):
        # Infer from Yes/No labels when index missing.
        label = str(row.get("outcome") or "").upper()
        if label in ("YES", "Y"):
            outcome_index = 0
        elif label in ("NO", "N"):
            outcome_index = 1
        else:
            return None

    slug = str(row.get("slug") or "")
    event_slug = str(row.get("eventSlug") or row.get("event_slug") or "")
    event_key = (event_slug or _event_key_from_slug(slug) or "").lower()
    tx = str(
        row.get("transactionHash")
        or row.get("transaction_hash")
        or row.get("txHash")
        or row.get("hash")
        or ""
    ).lower()

    return Rn1Trade(
        condition_id=cond,
        event_key=event_key,
        side=str(row.get("side") or "").upper(),
        outcome=str(row.get("outcome") or "").upper(),
        outcome_index=outcome_index,
        price=price,
        size=size,
        timestamp=ts,
        title=str(row.get("title") or "")[:160],
        asset=str(row.get("asset") or row.get("asset_id") or row.get("tokenId") or ""),
        tx_hash=tx,
        slug=slug,
        event_slug=event_slug,
        neg_risk=bool(row.get("negativeRisk") or row.get("negRisk") or False),
    )


def _parse_position(row: Dict[str, Any]) -> Optional[Rn1Position]:
    cond = str(row.get("conditionId") or row.get("condition_id") or "").lower()
    if not cond:
        return None
    try:
        size = float(row.get("size") or 0)
        avg = float(row.get("avgPrice") or row.get("avg_price") or 0)
        cur = float(row.get("curPrice") or row.get("cur_price") or 0)
        outcome_index = int(
            row.get("outcomeIndex") if row.get("outcomeIndex") is not None else -1
        )
    except (TypeError, ValueError):
        return None
    if size <= 0 or outcome_index not in (0, 1):
        return None
    return Rn1Position(
        condition_id=cond,
        outcome=str(row.get("outcome") or ""),
        outcome_index=outcome_index,
        size=size,
        avg_price=avg,
        cur_price=cur,
        title=str(row.get("title") or "")[:160],
        slug=str(row.get("slug") or ""),
        event_slug=str(row.get("eventSlug") or ""),
        asset=str(row.get("asset") or ""),
        neg_risk=bool(row.get("negativeRisk") or row.get("negRisk") or False),
        end_date=str(row.get("endDate") or row.get("end_date") or ""),
    )


def _event_key_from_slug(slug: str) -> str:
    slug = str(slug or "").lower()
    if not slug:
        return ""
    parts = slug.rsplit("-", 1)
    if len(parts) == 2 and len(parts[0]) > 8:
        return parts[0]
    return slug


class RN1WalletTracker:
    """Fetch RN1 trades + open positions from Polymarket data-api."""

    def __init__(
        self,
        wallet: Optional[str] = None,
        *,
        lookback_hours: float = RN1_LOOKBACK_HOURS,
        data_host: str = DEFAULT_DATA_HOST,
    ) -> None:
        self.wallet = (wallet or RN1_PROXY_WALLET).lower()
        self.lookback_hours = lookback_hours
        self.data_host = data_host.rstrip("/")
        self._trades: List[Rn1Trade] = []
        self._fetched_at: float = 0.0
        self._cache_ttl_s: float = 30.0

    @property
    def trade_count(self) -> int:
        return len(self._trades)

    @property
    def trades(self) -> List[Rn1Trade]:
        return list(self._trades)

    async def refresh(self, *, force: bool = False) -> int:
        now = time.time()
        if (
            not force
            and self._trades
            and (now - self._fetched_at) < self._cache_ttl_s
        ):
            return len(self._trades)

        cutoff = now - self.lookback_hours * 3600.0
        all_rows: List[Dict[str, Any]] = []
        offset = 0

        async with httpx.AsyncClient(timeout=20.0) as http:
            for _ in range(MAX_TRADE_PAGES):
                params = {
                    "user": self.wallet,
                    "limit": TRADES_PER_PAGE,
                    "offset": offset,
                }
                resp = await http.get(f"{self.data_host}/trades", params=params)
                resp.raise_for_status()
                batch = resp.json() or []
                if not isinstance(batch, list) or not batch:
                    break
                all_rows.extend(batch)
                if len(batch) < TRADES_PER_PAGE:
                    break
                offset += TRADES_PER_PAGE

        trades: List[Rn1Trade] = []
        for row in all_rows:
            parsed = _parse_trade(row)
            if parsed and parsed.timestamp >= cutoff:
                trades.append(parsed)

        trades.sort(key=lambda t: t.timestamp, reverse=True)
        self._trades = trades
        self._fetched_at = now
        return len(trades)

    async def fetch_open_soccer_positions(self) -> List[Rn1Position]:
        """Live (non-redeemable) soccer positions still priced in (0, 0.99)."""
        out: List[Rn1Position] = []
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(
                f"{self.data_host}/positions",
                params={
                    "user": self.wallet,
                    "redeemable": "false",
                    "sizeThreshold": 0.01,
                    "limit": 500,
                },
            )
            resp.raise_for_status()
            rows = resp.json() or []
        if not isinstance(rows, list):
            return out
        for row in rows:
            pos = _parse_position(row)
            if not pos or not pos.is_soccer:
                continue
            if pos.cur_price <= 0 or pos.cur_price >= 0.99:
                continue
            out.append(pos)
        out.sort(key=lambda p: -p.size)
        return out
