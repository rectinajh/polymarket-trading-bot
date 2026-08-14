"""Security gate tests: DRY_RUN, host allowlist, paper sell path."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from src.config.settings import (
    resolve_live_trading_enabled,
    validate_endpoint_url,
)
from src.utils.database import Position


def test_dry_run_true_wins_over_live_enabled(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    assert resolve_live_trading_enabled() is False


def test_dry_run_false_enables_live(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    assert resolve_live_trading_enabled() is True


def test_legacy_live_flag_when_dry_run_unset(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    assert resolve_live_trading_enabled() is True


def test_default_is_paper(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    assert resolve_live_trading_enabled() is False


def test_host_allowlist_accepts_official():
    assert validate_endpoint_url(
        "https://clob.polymarket.com", what="test"
    ).endswith("clob.polymarket.com")


def test_host_allowlist_rejects_unknown(monkeypatch):
    monkeypatch.delenv("POLYMARKET_ALLOW_CUSTOM_HOSTS", raising=False)
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_endpoint_url("https://evil.example.com", what="test")


def test_host_allowlist_opt_in(monkeypatch):
    monkeypatch.setenv("POLYMARKET_ALLOW_CUSTOM_HOSTS", "true")
    assert "evil.example.com" in validate_endpoint_url(
        "https://evil.example.com", what="test"
    )


@pytest.mark.asyncio
async def test_paper_sell_does_not_call_place_order():
    from src.config.settings import settings
    from src.jobs.execute import place_sell_limit_order

    prev_live = settings.trading.live_trading_enabled
    settings.trading.live_trading_enabled = False
    try:
        position = Position(
            market_id="PAPER-SELL-1",
            side="YES",
            entry_price=0.4,
            quantity=3,
            timestamp=datetime.now(),
            rationale="paper",
            confidence=0.7,
            live=False,
            id=1,
        )
        client = Mock()
        client.place_order = AsyncMock()
        ok = await place_sell_limit_order(
            position=position,
            limit_price=0.55,
            db_manager=AsyncMock(),
            polymarket_client=client,
            live_mode=False,
        )
        assert ok is True
        client.place_order.assert_not_called()
    finally:
        settings.trading.live_trading_enabled = prev_live


@pytest.mark.asyncio
async def test_paper_execute_keeps_live_flag_false(tmp_path):
    from src.config.settings import settings
    from src.jobs.execute import execute_position
    from src.utils.database import DatabaseManager

    prev_live = settings.trading.live_trading_enabled
    settings.trading.live_trading_enabled = False
    db_path = str(tmp_path / "paper_exec.db")
    db = DatabaseManager(db_path=db_path)
    await db.initialize()
    try:
        position = Position(
            market_id="PAPER-EXEC-1",
            side="YES",
            entry_price=0.5,
            quantity=2,
            timestamp=datetime.now(),
            rationale="paper",
            confidence=0.8,
            live=False,
        )
        position.id = await db.add_position(position)
        client = Mock()
        client.place_order = AsyncMock()
        ok = await execute_position(
            position=position,
            live_mode=False,
            db_manager=db,
            polymarket_client=client,
        )
        assert ok is True
        client.place_order.assert_not_called()
        updated = await db.get_position_by_market_id("PAPER-EXEC-1")
        assert updated is not None
        assert not updated.live
    finally:
        settings.trading.live_trading_enabled = prev_live
