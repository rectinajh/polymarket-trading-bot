"""Unit tests for RN1 soccer/tennis copy helpers."""

from __future__ import annotations

from src.strategies.sports.rn1_tracker import _parse_trade, copy_share_count
from src.strategies.sports.soccer_filter import (
    is_copyable_market,
    is_soccer_market,
    is_tennis_market,
    side_from_outcome_index,
)
from src.strategies.sports.strategy import _team_from_title


def test_copy_share_count_clob_min_5_and_1_01():
    # Exactly exchange floor: ≥5 shares and ≥$1.01
    sh = copy_share_count(0.69, 1.01)
    assert sh == 5
    assert sh * 0.69 >= 1.01 - 1e-9
    sh2 = copy_share_count(0.14, 1.01)
    assert sh2 >= 5
    assert sh2 * 0.14 >= 1.01 - 1e-9
    assert sh2 == 8  # ceil(1.01/0.14)=8


def test_is_soccer_match_winner():
    assert is_soccer_market("Will SC Freiburg win on 2026-08-27?")
    assert is_soccer_market(
        "FC Barcelona vs. Athletic Club: 1st Half O/U 1.5",
        slug="spa-bar-ath-2026-08-27-1h-ou15",
    )
    assert is_soccer_market(
        "Kuopion PS vs. Shamrock Rovers FC: Both Teams to Score",
    )


def test_is_soccer_excludes_tennis_cs_mlb():
    assert not is_soccer_market(
        "ITF MEN - SINGLES: M25 Poznan (Poland), clay: A vs B",
        slug="itf-savano-catini-2026-08-27",
    )
    assert not is_soccer_market(
        "Counter-Strike: Leo Team vs Butterfly - Map 1 Winner",
        slug="cs2-leo2-btf-2026-08-27-game1",
    )
    assert not is_soccer_market(
        "Boston Red Sox vs. New York Yankees: O/U 7.5",
    )


def test_is_soccer_excludes_college_football_usc():
    assert not is_soccer_market("Spread: USC (-27.5)")
    assert not is_soccer_market(
        "Spread: USC (-38.5)",
        slug="cfb-usc-sjsu-spread",
    )


def test_is_tennis_atp_wta():
    assert is_tennis_market(
        "ATP Cincinnati: Player A vs Player B",
        slug="atp-cin-a-b-2026-08-15",
    )
    assert is_tennis_market(
        "WTA US Open: X vs Y",
        slug="wta-uso-x-y-2026-08-28",
    )
    assert not is_tennis_market(
        "ITF MEN - SINGLES: M25 Poznan",
        slug="itf-savano-catini-2026-08-27",
    )
    assert is_tennis_market(
        "ITF MEN - SINGLES: M25 Poznan",
        slug="itf-savano-catini-2026-08-27",
        include_itf=True,
    )
    assert not is_tennis_market(
        "ATP Doubles: A/B vs C/D",
        slug="atp-doubles-ab-cd",
    )
    assert not is_tennis_market(
        "Counter-Strike: Leo vs Butterfly",
        slug="cs2-leo-btf",
    )
    # Challenger venue title + atp- slug must not copy
    assert not is_tennis_market(
        "Manacor: Inaki Montes vs Mathys Erhard",
        slug="atp-manacor-montes-erhard",
    )


def test_is_copyable_soccer_and_tennis():
    assert is_copyable_market("Will SC Freiburg win on 2026-08-27?")
    assert is_copyable_market(
        "ATP Cincinnati: A vs B",
        slug="atp-cin-a-b",
        allow_tennis=True,
    )
    assert not is_copyable_market(
        "ATP Cincinnati: A vs B",
        slug="atp-cin-a-b",
        allow_tennis=False,
    )
    assert not is_copyable_market(
        "Spread: USC (-27.5)",
        slug="cfb-usc-sjsu-spread",
    )


def test_default_price_band():
    from src.strategies.sports.config import (
        COPY_MW_PRICE_MAX,
        COPY_MW_PRICE_MIN,
        COPY_PRICE_MAX,
        COPY_PRICE_MIN,
        MAX_ENTRIES_PER_DAY,
        SPORTS_GHOST_RECONCILE_HOURS,
    )

    assert COPY_PRICE_MIN == 0.35
    assert COPY_PRICE_MAX == 0.75
    assert COPY_MW_PRICE_MIN == 0.50
    assert COPY_MW_PRICE_MAX == 0.70
    assert MAX_ENTRIES_PER_DAY == 10
    assert SPORTS_GHOST_RECONCILE_HOURS == 12.0


def test_copy_priority_and_mw_band():
    from src.strategies.sports.soccer_filter import (
        copy_price_band_for_market,
        copy_signal_priority,
    )

    assert copy_signal_priority("ATP Cincinnati: A vs B") == 0
    assert copy_signal_priority("Foo vs Bar: O/U 2.5", slug="ita-foo-bar-ou25") == 1
    assert copy_signal_priority("Will SC Freiburg win on 2026-08-27?") == 2
    assert copy_price_band_for_market("Will SC Freiburg win on 2026-08-27?") == (0.50, 0.70)
    assert copy_price_band_for_market("ATP Cincinnati: A vs B") == (0.35, 0.75)


def test_side_from_outcome_index():
    assert side_from_outcome_index(0) == "yes"
    assert side_from_outcome_index(1) == "no"
    assert side_from_outcome_index(2) is None


def test_parse_trade_soccer_with_index():
    row = {
        "conditionId": "0xCOND",
        "side": "BUY",
        "outcome": "No",
        "outcomeIndex": 1,
        "price": 0.67,
        "size": 100,
        "timestamp": 1700000000,
        "title": "Will Modern SC win on 2026-08-27?",
        "slug": "egy-mod-gha-2026-08-27-mod",
        "eventSlug": "egy-mod-gha-2026-08-27",
        "transactionHash": "0xabc",
    }
    t = _parse_trade(row)
    assert t is not None
    assert t.outcome_index == 1
    assert t.is_soccer is True
    assert t.is_copyable is True
    assert t.position_key() == "pos:0xcond:1"


def test_stop_loss_threshold():
    entry, cur, pct = 0.50, 0.39, 0.20
    assert cur <= entry * (1.0 - pct)
    assert not (0.45 <= entry * (1.0 - pct))


def test_sync_open_positions_default_off():
    from src.strategies.sports.config import COPY_SYNC_OPEN_POSITIONS

    assert COPY_SYNC_OPEN_POSITIONS is False


def test_fresh_guard_allows_after_reset(tmp_path):
    from pathlib import Path
    from src.strategies.sports.sports_guard import check_trading_allowed
    from src.strategies.sports.sports_pnl import SportsPnL
    from src.strategies.capital_policy import trading_day

    path = tmp_path / "sports_pnl.json"
    pnl = SportsPnL(path)
    data = {"experiment_start": trading_day(), "entries": []}
    pnl._save(data)
    allowed, reason, _ = check_trading_allowed(nav_cents=11097, pnl=pnl)
    assert allowed is True
    assert reason == "ok"


def test_peak_dd_skipped_until_sample_ready(tmp_path):
    """Tiny peak + few settles must not trip peak-DD (8/30 false halt)."""
    from src.strategies.sports.sports_guard import check_trading_allowed
    from src.strategies.sports.sports_pnl import SportsPnL
    from src.strategies.capital_policy import trading_day

    path = tmp_path / "sports_pnl.json"
    pnl = SportsPnL(path)
    pnl._save({
        "experiment_start": trading_day(),
        "entries": [
            {
                "condition_id": "0xa",
                "side": "yes",
                "status": "won",
                "settled_pnl_cents": 245,
                "cost_cents": 250,
                "closed_ts": "2026-08-30T06:00:00+08:00",
                "day": trading_day(),
            },
            {
                "condition_id": "0xb",
                "side": "yes",
                "status": "lost",
                "settled_pnl_cents": -50,
                "cost_cents": 100,
                "closed_ts": "2026-08-30T07:00:00+08:00",
                "day": trading_day(),
            },
            {
                "condition_id": "0xc",
                "side": "yes",
                "status": "lost",
                "settled_pnl_cents": -102,
                "cost_cents": 300,
                "closed_ts": "2026-08-30T08:00:00+08:00",
                "day": trading_day(),
            },
        ],
    })
    allowed, reason, meta = check_trading_allowed(nav_cents=10329, pnl=pnl)
    assert meta.get("peak_dd_armed") is False
    assert allowed is True
    assert reason == "ok"


def test_reconcile_ghost_opens(tmp_path):
    from datetime import datetime, timedelta
    from src.strategies.capital_policy import CN_TZ
    from src.strategies.sports.sports_pnl import SportsPnL

    path = tmp_path / "sports_pnl.json"
    pnl = SportsPnL(path)
    old = (datetime.now(CN_TZ) - timedelta(hours=48)).isoformat()
    fresh = (datetime.now(CN_TZ) - timedelta(hours=1)).isoformat()
    pnl._save({
        "experiment_start": "2026-08-30",
        "entries": [
            {
                "condition_id": "0xghost",
                "side": "yes",
                "status": "open",
                "shares": 5,
                "cost_cents": 210,
                "mark_cents": 0,
                "opened_ts": old,
                "sport": "rn1_soccer_copy",
            },
            {
                "condition_id": "0xfresh",
                "side": "yes",
                "status": "open",
                "shares": 5,
                "cost_cents": 200,
                "mark_cents": 100,
                "opened_ts": fresh,
                "sport": "rn1_soccer_copy",
            },
        ],
    })
    closed = pnl.reconcile_ghost_opens([], max_age_hours=36, flat_age_hours=36)
    assert len(closed) == 1
    assert closed[0]["condition_id"] == "0xghost"
    assert closed[0]["status"] == "lost"
    still = [e for e in pnl._load()["entries"] if e["status"] == "open"]
    assert len(still) == 1
    assert still[0]["condition_id"] == "0xfresh"


def test_reconcile_flat_book_faster(tmp_path):
    from datetime import datetime, timedelta
    from src.strategies.capital_policy import CN_TZ
    from src.strategies.sports.sports_pnl import SportsPnL

    path = tmp_path / "sports_pnl.json"
    pnl = SportsPnL(path)
    mid = (datetime.now(CN_TZ) - timedelta(hours=8)).isoformat()
    pnl._save({
        "experiment_start": "2026-08-30",
        "entries": [{
            "condition_id": "0xflat",
            "side": "yes",
            "status": "open",
            "shares": 5,
            "cost_cents": 200,
            "mark_cents": 0,
            "opened_ts": mid,
            "sport": "rn1_soccer_copy",
        }],
    })
    # 8h old, flat book → closes at flat_age_hours=6
    closed = pnl.reconcile_ghost_opens([], max_age_hours=12, flat_age_hours=6)
    assert len(closed) == 1
    assert "flat" in closed[0]["exit_reason"]
