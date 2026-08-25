"""Tests for orphan registry and window PnL ledger."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.strategies.btc15m_window_pnl import Btc15mWindowPnL
from src.strategies.orphan_unwind import OrphanRegistry


class TestOrphanRegistry(unittest.TestCase):
    def test_register_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "orphans.json"
            reg = OrphanRegistry(path)
            reg.register(
                condition_id="0xabc",
                side="yes",
                quantity=3,
                entry_cents=45,
                strategy="crypto15m",
                slug="btc-updown",
            )
            self.assertEqual(len(reg.list()), 1)
            reg.remove("0xabc")
            self.assertEqual(reg.list(), [])


class TestBtc15mWindowPnL(unittest.TestCase):
    def test_record_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pnl.json"
            ledger = Btc15mWindowPnL(path)
            ledger.record_fill(
                slug="btc-updown-15m",
                asset="btc",
                condition_id="0xabc",
                shares=2,
                combined=0.97,
                profit_per=0.03,
                window_start=1_700_000_000,
            )
            summary = ledger.summary_for_day()
            self.assertEqual(summary["fills"], 1)
            self.assertEqual(summary["open"], 1)
            self.assertGreater(summary["expected_profit_cents"], 0)


if __name__ == "__main__":
    unittest.main()
