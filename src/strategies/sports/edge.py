"""Edge and maker pricing for RN1 sports sleeve."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from src.strategies.sports.config import FAVORITE_MAX, FAVORITE_MIN, MAKER_TICK, MIN_EDGE
from src.strategies.sports.discover import MatchWinnerMarket, in_favorite_band
from src.strategies.sports.match import MatchedReference


@dataclass(frozen=True)
class MakerOpportunity:
    market: MatchWinnerMarket
    reference: MatchedReference
    fair_prob: float
    maker_price: float
    edge: float
    best_bid: Optional[float]
    best_ask: Optional[float]


def snap_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return round(price, 2)
    steps = round(price / tick)
    return round(steps * tick, 4)


def _level_price(row) -> Optional[float]:
    try:
        if isinstance(row, dict):
            p = float(row.get("price"))
        else:
            p = float(row[0])
        return p / 100.0 if p > 1.0 else p
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _best_bid_ask(book: dict) -> Tuple[Optional[float], Optional[float]]:
    ob = book.get("orderbook") if isinstance(book.get("orderbook"), dict) else book
    bids = ob.get("yes") or ob.get("bids") or []
    asks = ob.get("yes_asks") or ob.get("asks") or []
    best_bid = _level_price(bids[0]) if bids else None
    best_ask = _level_price(asks[0]) if asks else None
    return best_bid, best_ask


def compute_maker_price(
    *,
    fair_prob: float,
    tick: float,
    best_bid: Optional[float],
    best_ask: Optional[float],
    min_edge: float = MIN_EDGE,
) -> Optional[float]:
    """Target GTC bid: under fair by min_edge, improve bid, stay below ask."""
    cap = fair_prob - min_edge
    if cap < FAVORITE_MIN:
        return None

    price = cap
    if best_bid is not None:
        price = min(price, best_bid + tick)
    if best_ask is not None:
        price = min(price, best_ask - tick)

    price = snap_to_tick(price, tick)
    if price < FAVORITE_MIN or price > FAVORITE_MAX:
        return None
    if best_ask is not None and price >= best_ask:
        return None
    if fair_prob - price < min_edge - 1e-6:
        return None
    return price


def evaluate_maker_opportunity(
    market: MatchWinnerMarket,
    reference: MatchedReference,
    orderbook: dict,
    *,
    favorite_min: float = FAVORITE_MIN,
    favorite_max: float = FAVORITE_MAX,
    min_edge: float = MIN_EDGE,
) -> Optional[MakerOpportunity]:
    if not in_favorite_band(market.yes_price, lo=favorite_min, hi=favorite_max):
        return None

    fair = reference.fair_prob
    tick = market.tick_size or MAKER_TICK
    best_bid, best_ask = _best_bid_ask(orderbook)
    maker_price = compute_maker_price(
        fair_prob=fair,
        tick=tick,
        best_bid=best_bid,
        best_ask=best_ask,
        min_edge=min_edge,
    )
    if maker_price is None:
        return None

    return MakerOpportunity(
        market=market,
        reference=reference,
        fair_prob=fair,
        maker_price=maker_price,
        edge=fair - maker_price,
        best_bid=best_bid,
        best_ask=best_ask,
    )
