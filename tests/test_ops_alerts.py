"""Tests for ops metrics and alert helpers."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.strategies.scan_stats import ScanStatsLog
from src.utils.ops_metrics import count_since, record_rate_limit

CN = ZoneInfo("Asia/Shanghai")


class TestOpsMetrics(unittest.TestCase):
    def test_record_and_count_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ops_metrics.json"
            record_rate_limit("test", detail="429", path=path)
            record_rate_limit("test", detail="429 again", path=path)
            self.assertEqual(count_since(1.0, kind="rate_limit", path=path), 2)


class TestNavAnomaly(unittest.TestCase):
    def test_nav_jump_without_fills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats_path = Path(tmp) / "scan_stats.json"
            log = ScanStatsLog(stats_path)
            ts1 = datetime(2026, 8, 26, 10, 0, tzinfo=CN)
            ts2 = datetime(2026, 8, 26, 10, 4, tzinfo=CN)
            log.record_conservative_cycle(
                {"nav_cents": 11938, "filled": 0},
                {"filled_pairs": 0},
                now=ts1,
            )
            log.record_conservative_cycle(
                {"nav_cents": 12200, "filled": 0},
                {"filled_pairs": 0},
                now=ts2,
            )
            from scripts import ops_alerts

            with patch.object(ops_alerts, "DEFAULT_STATS_PATH", stats_path):
                alerts = ops_alerts._check_nav_anomaly()
            self.assertTrue(any(a.get("code") == "nav_jump" for a in alerts))


if __name__ == "__main__":
    unittest.main()
