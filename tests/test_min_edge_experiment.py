"""Tests for P2.3 MIN_EDGE experiment (near-miss gated)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.strategies.min_edge_experiment import (
    evaluate_from_scan_stats,
    experiment_alert,
    run_and_persist,
)
from src.strategies.scan_stats import ScanStatsLog

CN = ZoneInfo("Asia/Shanghai")


class TestMinEdgeExperiment(unittest.TestCase):
    def test_skipped_when_no_near_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan_stats.json"
            log = ScanStatsLog(path)
            log.record_conservative_cycle(
                {"near_miss_count": 0, "near_misses": []},
                {},
                now=datetime(2026, 8, 26, 10, 0, tzinfo=CN),
            )
            result = evaluate_from_scan_stats(log)
            self.assertEqual(result["status"], "skipped")
            self.assertIsNone(experiment_alert(result))

    def test_active_when_near_miss_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan_stats.json"
            log = ScanStatsLog(path)
            log.record_conservative_cycle(
                {
                    "near_miss_count": 2,
                    "near_misses": [
                        {"edge": 0.015, "title": "a"},
                        {"edge": 0.018, "title": "b"},
                    ],
                },
                {},
                now=datetime(2026, 8, 26, 10, 0, tzinfo=CN),
            )
            result = evaluate_from_scan_stats(log)
            self.assertEqual(result["status"], "active")
            self.assertEqual(result["passes_at_threshold"]["0.015"], 2)
            alert = experiment_alert(result)
            self.assertIsNotNone(alert)
            assert alert is not None
            self.assertEqual(alert["severity"], "warning")

    def test_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats = Path(tmp) / "scan_stats.json"
            out = Path(tmp) / "experiment.json"
            log = ScanStatsLog(stats)
            log.record_conservative_cycle({"near_miss_count": 0}, {}, now=datetime(2026, 8, 26, tzinfo=CN))
            from src.strategies import min_edge_experiment as mod

            old = mod.EXPERIMENT_PATH
            try:
                mod.EXPERIMENT_PATH = out
                run_and_persist(log)
                self.assertTrue(out.exists())
            finally:
                mod.EXPERIMENT_PATH = old


if __name__ == "__main__":
    unittest.main()
