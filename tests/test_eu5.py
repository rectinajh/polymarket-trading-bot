"""Unit tests for EU5 fair-value sleeve (no network)."""

from __future__ import annotations

import unittest
from datetime import date

from src.strategies.eu5.config import EU5_SPORT_KEYS
from src.strategies.eu5.strategy import (
    _deserialize_events,
    _serialize_events,
    evaluate_value,
    value_shares,
)
from src.strategies.sports.discover import MatchWinnerMarket


def _market(yes_price: float = 0.50) -> MatchWinnerMarket:
    return MatchWinnerMarket(
        condition_id="0xabc",
        question="Will Arsenal win on 2026-09-20?",
        team="Arsenal",
        match_date=date(2026, 9, 20),
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        end_ts=None,
        volume=10000.0,
        neg_risk=False,
        tick_size=0.01,
        yes_token="y",
        no_token="n",
        raw={},
    )


class TestEu5Config(unittest.TestCase):
    def test_five_leagues_only(self) -> None:
        self.assertEqual(len(EU5_SPORT_KEYS), 5)
        self.assertIn("soccer_epl", EU5_SPORT_KEYS)
        self.assertNotIn("soccer_uefa_champs_league_qualification", EU5_SPORT_KEYS)


class TestValueShares(unittest.TestCase):
    def test_min_shares_floor(self) -> None:
        # $2 stake at 0.50 → 4 shares → floor to 5
        self.assertEqual(value_shares(0.50), 5)

    def test_stake_scaling(self) -> None:
        # $2 at 0.30 → ceil(6.67) = 7 shares ($2.10)
        self.assertEqual(value_shares(0.30), 7)

    def test_hard_cap(self) -> None:
        # 5 shares at 0.80 = $4.00 ok; at 0.99 = $4.95 ok; force breach:
        self.assertEqual(value_shares(0.80), 5)
        self.assertEqual(value_shares(0.99, stake=10.0), 0)  # 11×0.99 > $5

    def test_zero_price(self) -> None:
        self.assertEqual(value_shares(0.0), 0)


class TestEvaluateValue(unittest.TestCase):
    def test_yes_value(self) -> None:
        # fair 0.55, ask 0.50 → edge 0.05 ≥ 0.04 → YES signal
        sig = evaluate_value(_market(), 0.55, yes_ask=0.50, no_ask=0.52)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "yes")
        self.assertAlmostEqual(sig["edge"], 0.05, places=4)

    def test_no_value(self) -> None:
        # fair 0.40 → fair NO 0.60; no_ask 0.55 → edge 0.05 → NO signal
        sig = evaluate_value(_market(), 0.40, yes_ask=0.41, no_ask=0.55)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "no")

    def test_no_edge(self) -> None:
        sig = evaluate_value(_market(), 0.52, yes_ask=0.51, no_ask=0.49)
        self.assertIsNone(sig)

    def test_band_excludes_extremes(self) -> None:
        # ask 0.20 below band → no signal even with huge edge
        sig = evaluate_value(_market(), 0.40, yes_ask=0.20, no_ask=0.85)
        self.assertIsNone(sig)

    def test_picks_bigger_edge(self) -> None:
        # YES edge 0.05, NO edge 0.08 → picks NO
        sig = evaluate_value(_market(), 0.50, yes_ask=0.45, no_ask=0.42)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "no")
        self.assertAlmostEqual(sig["edge"], 0.08, places=4)


class TestReferenceCacheSerde(unittest.TestCase):
    def test_roundtrip(self) -> None:
        from datetime import datetime, timezone

        from src.clients.odds_api_client import ReferenceEvent, ReferenceOutcome

        ev = ReferenceEvent(
            event_id="e1",
            sport_key="soccer_epl",
            commence_time=datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc),
            home_team="Arsenal",
            away_team="Chelsea",
            outcomes={
                "Arsenal": ReferenceOutcome("Arsenal", 2.0, 0.45),
                "Chelsea": ReferenceOutcome("Chelsea", 4.0, 0.25),
                "Draw": ReferenceOutcome("Draw", 3.5, 0.30),
            },
        )
        rows = _serialize_events([ev])
        back = _deserialize_events(rows)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0].home_team, "Arsenal")
        self.assertAlmostEqual(back[0].outcomes["Draw"].fair_prob, 0.30)


if __name__ == "__main__":
    unittest.main()
