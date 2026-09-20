"""Smoke tests for eu5_plus strategies (no network)."""

from __future__ import annotations

import unittest

from src.strategies.csl_explore.models import BinaryMarket, MatchBundle
from src.strategies.eu5_plus.strategies.completeness import CompletenessStrategy
from src.strategies.eu5_plus.strategies.draw_fv import DrawFvStrategy
from src.strategies.eu5_plus.strategies.line_cross import LineCrossStrategy


def _mkt(cid: str, q: str, px: float) -> BinaryMarket:
    return BinaryMarket(
        condition_id=cid, question=q, yes_price=px, no_price=1 - px,
        yes_token="y", no_token="n",
    )


class TestCompleteness(unittest.TestCase):
    def test_gap(self) -> None:
        m = MatchBundle(
            slug="epl-a-b", title="A vs. B", start_ts=None, volume=1,
            home_win=_mkt("h", "Will A win on 2026-09-21?", 0.30),
            away_win=_mkt("a", "Will B win on 2026-09-21?", 0.30),
            draw=_mkt("d", "Will A vs. B end in a draw?", 0.35),
        )
        # sum=0.95 gap=0.05
        sigs = CompletenessStrategy().generate(m, context={})
        buys = [s for s in sigs if s.action == "buy_yes"]
        self.assertEqual(len(buys), 3)


class TestDrawFvSkip(unittest.TestCase):
    def test_no_ref(self) -> None:
        m = MatchBundle(
            slug="epl-a-b", title="A vs. B", start_ts=None, volume=1,
            draw=_mkt("d", "Will A vs. B end in a draw?", 0.22),
        )
        sigs = DrawFvStrategy().generate(m, context={"ref_by_slug": {}})
        self.assertEqual(sigs[0].action, "skip")


class TestLineCross(unittest.TestCase):
    def test_heavy_fav_needs_spread(self) -> None:
        m = MatchBundle(
            slug="epl-a-b", title="A vs. B", start_ts=None, volume=1,
            home_win=_mkt("h", "Will A win on 2026-09-21?", 0.70),
            away_win=_mkt("a", "Will B win on 2026-09-21?", 0.15),
        )
        m.spreads = [  # type: ignore[attr-defined]
            _mkt("s", "Spread: B (+1.5)", 0.20),
        ]
        sigs = LineCrossStrategy().generate(m, context={})
        buys = [s for s in sigs if s.action == "buy_yes"]
        self.assertTrue(len(buys) >= 1)


if __name__ == "__main__":
    unittest.main()
