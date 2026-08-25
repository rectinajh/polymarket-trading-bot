"""Tests for sports PnL ledger and experiment guard."""

from __future__ import annotations

from pathlib import Path

from src.strategies.sports.sports_guard import check_trading_allowed
from src.strategies.sports.sports_pnl import SportsPnL


def test_record_and_settle_win(tmp_path: Path):
    path = tmp_path / "sports_pnl.json"
    pnl = SportsPnL(path)
    pnl.record_entry(
        condition_id="0xabc",
        title="Will Team win?",
        team="Team",
        match_date="2026-08-26",
        shares=10,
        price=0.60,
        fair_prob=0.70,
        edge=0.10,
    )
    settled = pnl.update_from_positions([
        {
            "condition_id": "0xabc",
            "side": "YES",
            "size": 10,
            "current_price": 0.99,
            "redeemable": True,
        }
    ])
    assert settled == []
    settled = pnl.update_from_positions([])
    assert len(settled) == 1
    assert settled[0]["status"] == "won"
    assert settled[0]["settled_pnl_cents"] == 400


from src.strategies.capital_policy import trading_day


def test_guard_blocks_daily_loss(tmp_path: Path):
    path = tmp_path / "sports_pnl.json"
    pnl = SportsPnL(path)
    today = trading_day()
    data = pnl._load()
    data["experiment_start"] = today
    data["entries"] = [{
        "condition_id": "0x1",
        "team": "X",
        "status": "lost",
        "settled_pnl_cents": -800,
        "closed_ts": f"{today}T10:00:00+08:00",
        "day": today,
    }]
    pnl._save(data)

    allowed, reason, _meta = check_trading_allowed(nav_cents=11900, pnl=pnl)
    assert allowed is False
    assert "daily loss" in reason
