"""Ingestion must register YES/NO token ids on the Polymarket client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs.ingest import process_and_queue_markets


pytestmark = pytest.mark.asyncio


async def test_ingest_registers_token_ids_on_polymarket_client():
    db = MagicMock()
    db.upsert_markets = AsyncMock()
    queue = asyncio.Queue()
    client = MagicMock()
    logger = MagicMock()

    markets = [{
        "ticker": "0xabc",
        "condition_id": "0xabc",
        "title": "Will it rain?",
        "yes_token_id": "yes-token",
        "no_token_id": "no-token",
        "yes_bid_dollars": 0.40,
        "yes_ask_dollars": 0.42,
        "no_bid_dollars": 0.58,
        "no_ask_dollars": 0.60,
        "yes_price": 0.41,
        "no_price": 0.59,
        "volume": 50000,
        "volume_fp": 50000,
        "expiration_ts": 0,
        "category": "politics",
        "status": "active",
        "neg_risk": True,
        "min_tick_size": 0.001,
    }]

    await process_and_queue_markets(
        markets, db, queue, set(), client, logger,
    )

    client.register_market.assert_called_with(
        "0xabc",
        "yes-token",
        "no-token",
        neg_risk=True,
        tick_size=0.001,
    )
    client.flush_token_cache.assert_called_once()
    db.upsert_markets.assert_awaited()
