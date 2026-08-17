"""Unit tests for completeness arbitrage math (no network)."""

from __future__ import annotations

import unittest

from src.strategies.completeness_arb import evaluate_completeness


class TestCompletenessMath(unittest.TestCase):
    def test_profit_ok(self):
        ok, reason, profit = evaluate_completeness(0.45, 0.50, 20, 20)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertAlmostEqual(profit, 0.05, places=4)

    def test_combined_too_high(self):
        ok, reason, _ = evaluate_completeness(0.50, 0.50, 20, 20)
        self.assertFalse(ok)
        self.assertIn("combined", reason)

    def test_thin_size(self):
        ok, reason, _ = evaluate_completeness(0.40, 0.50, 1, 20)
        self.assertFalse(ok)
        self.assertEqual(reason, "thin_size")

    def test_min_profit_gate(self):
        # combined 0.975 → profit 0.025 < 0.02? actually 0.025 > 0.02 and
        # combined < 0.98 → ok. Use 0.979 → profit 0.021 ok.
        # Use 0.985 combined blocked by max_combined first.
        ok, _, profit = evaluate_completeness(0.48, 0.495, 10, 10)
        self.assertTrue(ok)
        self.assertAlmostEqual(profit, 0.025, places=4)


if __name__ == "__main__":
    unittest.main()
