"""Unit tests for RN1 soccer copy helpers."""

from __future__ import annotations

from src.strategies.sports.rn1_tracker import _parse_trade, copy_share_count
from src.strategies.sports.soccer_filter import is_soccer_market, side_from_outcome_index
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
    assert t.position_key() == "pos:0xcond:1"


def test_stop_loss_threshold():
    entry, cur, pct = 0.50, 0.39, 0.20
    assert cur <= entry * (1.0 - pct)
    assert not (0.45 <= entry * (1.0 - pct))
