"""
Market Ingestion Job — Polymarket edition.

Discovers active Polymarket markets via the Gamma API, normalizes them into the
project's `Market` dataclass shape, persists them to SQLite, and pushes the
eligible subset onto a queue for downstream decision-making.

This replaces the original Polymarket-events ingestion. Differences worth noting:
  * Discovery is unauthenticated (Gamma) — no signing required.
  * `condition_id` (hex) is used in place of Polymarket's `ticker` string. The
    same field name (`Market.market_id`) is reused so downstream code does
    not change.
  * YES/NO token_ids returned by Gamma are registered against the live
    `PolymarketClient` so subsequent order placement can resolve them
    without an extra round-trip.
"""

import asyncio
from datetime import datetime
from typing import Optional, List

from src.clients.gamma_client import GammaClient, to_legacy_market_shape
from src.clients.polymarket_client import PolymarketClient
from src.utils.database import DatabaseManager, Market
from src.config.settings import settings
from src.utils.logging_setup import get_trading_logger
from src.utils.market_prices import is_tradeable_market


async def process_and_queue_markets(
    markets_data: List[dict],
    db_manager: DatabaseManager,
    queue: asyncio.Queue,
    existing_position_market_ids: set,
    polymarket_client: Optional[PolymarketClient],
    logger,
):
    """Transform Gamma market dicts → `Market` rows, upsert, and queue eligible ones.

    `markets_data` items must have already been processed by
    :func:`gamma_client.to_legacy_market_shape` so they expose the legacy-compatible
    field names (`ticker`, `yes_price`, `volume`, `expiration_time`, etc.) on
    top of Polymarket-native fields (`condition_id`, `yes_token_id`, ...).
    """
    markets_to_upsert: List[Market] = []
    for m in markets_data:
        # Polymarket prices already come from Gamma in dollars (0.0–1.0). The
        # to_legacy_market_shape() helper exposes them as `yes_bid_dollars` /
        # `yes_ask_dollars` etc. — the same keys the legacy code reads.
        yes_bid = float(m.get("yes_bid_dollars", 0) or 0)
        yes_ask = float(m.get("yes_ask_dollars", 0) or 0)
        no_bid = float(m.get("no_bid_dollars", 0) or 0)
        no_ask = float(m.get("no_ask_dollars", 0) or 0)
        yes_price = (yes_bid + yes_ask) / 2 if (yes_bid or yes_ask) else float(m.get("yes_price", 0) or 0)
        no_price = (no_bid + no_ask) / 2 if (no_bid or no_ask) else float(m.get("no_price", 0) or 0)

        # `is_tradeable_market` rejects markets where both asks are ≥ $0.99.
        # On Polymarket this catches markets that have effectively resolved
        # (one outcome priced near certainty) and would reject any new order.
        if not is_tradeable_market(m):
            logger.debug(
                f"Skipping non-tradeable market {m.get('ticker')} "
                f"(yes_ask={yes_ask}, no_ask={no_ask})"
            )
            continue

        volume = int(float(m.get("volume_fp", 0) or m.get("volume", 0) or 0))
        ticker = m.get("ticker") or m.get("condition_id") or ""
        if not ticker:
            continue

        has_position = ticker in existing_position_market_ids

        # Parse expiration. Gamma supplies ISO-8601 in `expiration_time`.
        expiration_ts = m.get("expiration_ts") or 0
        if not expiration_ts and m.get("expiration_time"):
            try:
                expiration_ts = int(
                    datetime.fromisoformat(
                        m["expiration_time"].replace("Z", "+00:00")
                    ).timestamp()
                )
            except (ValueError, TypeError):
                expiration_ts = 0

        market = Market(
            market_id=ticker,
            title=m.get("title", ""),
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            expiration_ts=expiration_ts,
            category=m.get("category", "unknown"),
            status=m.get("status", "active"),
            last_updated=datetime.now(),
            has_position=has_position,
        )
        markets_to_upsert.append(market)

        # Pre-register the YES/NO token_ids on the Polymarket client so the
        # decide → execute pipeline can place orders without a Gamma re-fetch.
        # Routing metadata (neg_risk, tick_size) is required for the SDK to
        # pick the correct exchange contract — without it neg_risk markets
        # silently reject orders.
        yes_tok = m.get("yes_token_id")
        no_tok = m.get("no_token_id")
        if polymarket_client is not None and yes_tok and no_tok:
            routing = dict(
                neg_risk=bool(m.get("neg_risk", False)),
                tick_size=float(m.get("min_tick_size", 0.01) or 0.01),
            )
            polymarket_client.register_market(ticker, yes_tok, no_tok, **routing)
            cond = m.get("condition_id") or ""
            if cond and cond != ticker:
                polymarket_client.register_market(cond, yes_tok, no_tok, **routing)

    if not markets_to_upsert:
        logger.info("No new markets to upsert in this batch.")
        return

    await db_manager.upsert_markets(markets_to_upsert)
    logger.info(f"Successfully upserted {len(markets_to_upsert)} markets.")

    # Apply the lightweight eligibility filter — heavier per-market filters
    # (Kelly sizing, edge thresholds, AI confidence) live downstream in
    # `decide.make_decision_for_market`.
    min_volume: float = float(getattr(settings.trading, "min_volume", 100) or 100)
    eligible = [
        m for m in markets_to_upsert
        if m.volume >= min_volume
        and (
            not settings.trading.preferred_categories
            or m.category in settings.trading.preferred_categories
        )
        and m.category not in settings.trading.excluded_categories
    ]
    logger.info(f"Found {len(eligible)} eligible markets to process in this batch.")
    for market in eligible:
        await queue.put(market)


async def run_ingestion(
    db_manager: DatabaseManager,
    queue: asyncio.Queue,
    market_ticker: Optional[str] = None,
    polymarket_client: Optional[PolymarketClient] = None,
):
    """Main entry point for the market ingestion job.

    Args:
        db_manager: Open DatabaseManager.
        queue: asyncio.Queue ingested markets are pushed onto.
        market_ticker: If set, fetch only this `condition_id` (hex string).
        polymarket_client: Optional. If supplied, the discovered YES/NO
            token_ids are registered on it so downstream order placement
            can resolve them without a Gamma round-trip.
    """
    logger = get_trading_logger("market_ingestion")
    logger.info("Starting market ingestion job (Polymarket Gamma).", market_ticker=market_ticker)

    gamma = GammaClient()
    try:
        existing_position_market_ids = await db_manager.get_markets_with_positions()

        # Translate optional category preferences (slugs) into numeric tag IDs
        # for server-side filtering. Slugs that don't resolve are dropped with
        # a warning by GammaClient.
        tag_ids: Optional[List[int]] = None
        exclude_tag_ids: Optional[List[int]] = None
        if settings.trading.preferred_categories:
            tag_ids = await gamma.resolve_tag_slugs(settings.trading.preferred_categories)
        if settings.trading.excluded_categories or settings.trading.exclude_low_liquidity_categories:
            slugs = list(settings.trading.excluded_categories) + list(
                settings.trading.exclude_low_liquidity_categories
            )
            exclude_tag_ids = await gamma.resolve_tag_slugs(slugs)

        if market_ticker:
            logger.info(f"Fetching single market: condition_id={market_ticker}")
            try:
                m = await gamma.get_market(market_ticker)
            except Exception as exc:
                logger.warning(f"Could not fetch market {market_ticker}: {exc}")
                return
            await process_and_queue_markets(
                [to_legacy_market_shape(m)],
                db_manager,
                queue,
                existing_position_market_ids,
                polymarket_client,
                logger,
            )
            return

        # Pull the active universe — capped at 2000 markets to keep ingestion
        # under ~30 seconds. Polymarket has ~3000 active markets at peak;
        # high-volume names appear in the first few hundred.
        markets = await gamma.get_markets(
            active=True,
            closed=False,
            archived=False,
            accepting_orders=True,
            min_volume=float(getattr(settings.trading, "min_volume", 100) or 100),
            max_time_to_expiry_days=getattr(settings.trading, "max_time_to_expiry_days", None),
            tag_ids=tag_ids or None,
            exclude_tag_ids=exclude_tag_ids or None,
            order="volume",
            ascending=False,
            max_results=2000,
        )
        logger.info(f"Gamma returned {len(markets)} markets after server+client filters.")

        # Normalize once and forward in batches of ~200 to keep the queue
        # latency bounded for downstream consumers.
        BATCH = 200
        for i in range(0, len(markets), BATCH):
            chunk = [to_legacy_market_shape(m) for m in markets[i:i + BATCH]]
            await process_and_queue_markets(
                chunk,
                db_manager,
                queue,
                existing_position_market_ids,
                polymarket_client,
                logger,
            )

        logger.info(f"Ingestion finished — {len(markets)} markets processed.")

    except Exception as e:
        logger.error("An error occurred during market ingestion.", error=str(e), exc_info=True)
    finally:
        await gamma.close()
        logger.info("Market ingestion job finished.")
