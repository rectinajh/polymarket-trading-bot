"""Tests for scan_stats persistence and aggregation."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.strategies.scan_stats import ScanStatsLog

CN = ZoneInfo("Asia/Shanghai")


class TestScanStatsLog(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "scan_stats.json"
        self.log = ScanStatsLog(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_and_latest(self) -> None:
        ts = datetime(2026, 8, 19, 10, 0, tzinfo=CN)
        self.log.record_conservative_cycle(
            {"markets_scanned": 100, "candidates": 5, "opportunities": 1, "filled": 0, "placed": 1},
            {"scanned": 800, "opportunities": 0, "filled_pairs": 0},
            now=ts,
        )
        latest = self.log.latest_cycle()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest["day"], "2026-08-19")
        self.assertEqual(latest["safe_compounder"]["markets_scanned"], 100)

    def test_daily_summary_aggregates_fills(self) -> None:
        ts1 = datetime(2026, 8, 19, 10, 0, tzinfo=CN)
        ts2 = datetime(2026, 8, 19, 11, 0, tzinfo=CN)
        self.log.record_conservative_cycle(
            {"filled": 1, "placed": 1, "rejects": {"edge_lt_min": 3}},
            {"filled_pairs": 0},
            now=ts1,
        )
        self.log.record_conservative_cycle(
            {"filled": 0, "placed": 0, "rejects": {"edge_lt_min": 2}},
            {"filled_pairs": 1},
            now=ts2,
        )
        summary = self.log.daily_summary("2026-08-19")
        self.assertEqual(summary["cycles"], 2)
        self.assertEqual(summary["safe_compounder"]["total_filled"], 1)
        self.assertEqual(summary["completeness_arb"]["total_filled_pairs"], 1)
        self.assertEqual(summary["total_filled"], 2)
        self.assertEqual(summary["safe_compounder"]["rejects"]["edge_lt_min"], 5)

    def test_trims_old_cycles(self) -> None:
        from src.strategies import scan_stats as mod

        original = mod.MAX_CYCLES
        try:
            mod.MAX_CYCLES = 3
            for i in range(5):
                self.log.record_conservative_cycle({"filled": i}, {}, now=datetime(2026, 8, 19, i, 0, tzinfo=CN))
            data = self.log._load()
            self.assertEqual(len(data["cycles"]), 3)
            self.assertEqual(data["cycles"][0]["safe_compounder"]["filled"], 2)
        finally:
            mod.MAX_CYCLES = original


if __name__ == "__main__":
    unittest.main()
