"""Unit tests for lottery sleeve sizing (no network)."""

from __future__ import annotations

import unittest

from src.strategies.lottery.config import (
    MAX_ENTRIES_PER_DAY,
    PRICE_MAX,
    WEEK_BUDGET_USDC,
)
from src.strategies.lottery.discover import _eu5_blob, _parse_ticket
from src.strategies.lottery.strategy import ticket_shares


class TestLotteryConfig(unittest.TestCase):
    def test_budget_defaults(self) -> None:
        self.assertEqual(WEEK_BUDGET_USDC, 10.0)
        self.assertEqual(MAX_ENTRIES_PER_DAY, 5)
        self.assertEqual(PRICE_MAX, 0.15)


class TestTicketShares(unittest.TestCase):
    def test_at_ten_cents(self) -> None:
        self.assertEqual(ticket_shares(0.10), 11)

    def test_at_fifteen(self) -> None:
        self.assertEqual(ticket_shares(0.15), 7)

    def test_too_expensive_for_hard_cap(self) -> None:
        self.assertEqual(ticket_shares(0.50), 0)

    def test_zero(self) -> None:
        self.assertEqual(ticket_shares(0.0), 0)


class TestParseTicket(unittest.TestCase):
    def test_eu5_hint(self) -> None:
        self.assertTrue(_eu5_blob("Will Arsenal win on 2026-09-21?"))
        self.assertFalse(_eu5_blob("Will Madura United win on 2026-09-21?"))

    def test_parse_win(self) -> None:
        m = {
            "question": "Will Arsenal win on 2026-09-21?",
            "_outcome_prices": [0.12, 0.88],
            "_token_ids": ("y", "n"),
            "_condition_id": "0xabc",
        }
        t = _parse_ticket(m)
        self.assertIsNotNone(t)
        self.assertEqual(t.kind, "win")
        self.assertEqual(t.label, "Arsenal")


if __name__ == "__main__":
    unittest.main()
