"""Monitor RN1 (smart money) trades via Polymarket data-api for order confirmation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.strategies.sports.config import (
    RN1_CONFIRM_MODE,
    RN1_LOOKBACK_HOURS,
    RN1_PRICE_TOLERANCE_TICKS,
    RN1_PROXY_WALLET,
)
from src.strategies.sports.discover import MatchWinnerMarket
from src.strategies.sports.edge import MakerOpportunity

DEFAULT_DATA_HOST = "https://data-api.polymarket.com"
MAX_TRADE_PAGES = 5
TRADES_PER_PAGE = 100


@dataclass(frozen=True)
class Rn1Trade:
    condition_id: str
    event_key: str
    side: str
    outcome: str
    price: float
    size: float
    timestamp: float
    title: str


@dataclass(frozen=True)
class Rn1ConfirmResult:
    confirmed: bool
    mode: str
    reason: str
    matching_trade: Optional[Rn1Trade] = None


def extract_event_key(market: MatchWinnerMarket) -> str:
    """Derive a stable event key for cross-market RN1 activity matching."""
    raw = market.raw or {}
    for ev in raw.get("events") or []:
        if not isinstance(ev, dict):
            continue
        slug = ev.get("slug") or ev.get("ticker")
        if slug:
            return str(slug).lower()

    slug = str(raw.get("slug") or raw.get("eventSlug") or "").lower()
    if slug:
        # Market slugs often append a team suffix: event-2026-08-25-team
        parts = slug.rsplit("-", 1)
        if len(parts) == 2 and len(parts[0]) > 8:
            return parts[0]
        return slug

    return f"win-on-{market.match_date.isoformat()}"


def _parse_trade(row: Dict[str, Any]) -> Optional[Rn1Trade]:
    cond = str(row.get("conditionId") or row.get("condition_id") or "").lower()
    if not cond:
        return None
    try:
        ts = float(row.get("timestamp") or 0)
        price = float(row.get("price") or 0)
        size = float(row.get("size") or 0)
    except (TypeError, ValueError):
        return None
    if ts <= 0 or price <= 0:
        return None

    event_key = str(
        row.get("eventSlug")
        or row.get("event_slug")
        or _event_key_from_slug(row.get("slug") or "")
        or ""
    ).lower()
    if not event_key:
        title = str(row.get("title") or "")
        if " win on " in title.lower():
            event_key = title.lower().split(" win on ")[-1].split("?")[0].strip()
            event_key = f"win-on-{event_key}"

    return Rn1Trade(
        condition_id=cond,
        event_key=event_key,
        side=str(row.get("side") or "").upper(),
        outcome=str(row.get("outcome") or "").upper(),
        price=price,
        size=size,
        timestamp=ts,
        title=str(row.get("title") or "")[:120],
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
    """Fetch and cache recent RN1 trades from Polymarket data-api."""

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
        self._cache_ttl_s: float = 120.0

    @property
    def trade_count(self) -> int:
        return len(self._trades)

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

        self._trades = trades
        self._fetched_at = now
        return len(trades)

    def by_condition(self) -> Dict[str, List[Rn1Trade]]:
        out: Dict[str, List[Rn1Trade]] = {}
        for t in self._trades:
            out.setdefault(t.condition_id, []).append(t)
        return out

    def by_event(self) -> Dict[str, List[Rn1Trade]]:
        out: Dict[str, List[Rn1Trade]] = {}
        for t in self._trades:
            if t.event_key:
                out.setdefault(t.event_key, []).append(t)
        return out


def rn1_confirms(
    opp: MakerOpportunity,
    tracker: RN1WalletTracker,
    *,
    mode: Optional[str] = None,
    price_tolerance_ticks: int = RN1_PRICE_TOLERANCE_TICKS,
    lookback_hours: Optional[float] = None,
) -> Rn1ConfirmResult:
    """Layer-2 filter: require RN1 smart-money alignment before placing."""
    mode_l = (mode or RN1_CONFIRM_MODE).lower().strip()
    if mode_l in ("off", "none", "disabled", "0", "false"):
        return Rn1ConfirmResult(True, mode_l, "confirmation disabled")

    lookback = lookback_hours if lookback_hours is not None else tracker.lookback_hours
    now = time.time()
    cutoff = now - lookback * 3600.0
    tick = opp.market.tick_size or 0.01
    max_yes_price = opp.maker_price + price_tolerance_ticks * tick

    if mode_l == "event":
        event_key = extract_event_key(opp.market)
        for t in tracker.by_event().get(event_key, []):
            if t.timestamp < cutoff:
                continue
            if t.side != "BUY":
                continue
            return Rn1ConfirmResult(
                True,
                mode_l,
                f"RN1 active on event {event_key}",
                matching_trade=t,
            )
        return Rn1ConfirmResult(
            False,
            mode_l,
            f"no RN1 BUY on event {event_key} in last {lookback:.0f}h",
        )

    # strict (default): same condition, YES BUY, price within tolerance
    cond = opp.market.condition_id.lower()
    for t in tracker.by_condition().get(cond, []):
        if t.timestamp < cutoff:
            continue
        if t.side != "BUY":
            continue
        if t.outcome not in ("YES", "Y"):
            continue
        if t.price > max_yes_price + 1e-6:
            continue
        return Rn1ConfirmResult(
            True,
            mode_l,
            f"RN1 YES BUY @ {t.price:.2f} ≤ our {opp.maker_price:.2f}+{price_tolerance_ticks}tick",
            matching_trade=t,
        )

    return Rn1ConfirmResult(
        False,
        mode_l,
        f"no RN1 YES BUY on {cond[:12]}… within {lookback:.0f}h @ ≤{max_yes_price:.2f}",
    )
