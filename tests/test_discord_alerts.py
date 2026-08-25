"""Tests for Polymarket-branded Discord alerts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.utils.discord_alerts import (
    _fingerprint,
    format_ops_payload,
    notify_ops_alerts,
    resolve_webhook_url,
)


class TestDiscordAlerts(unittest.TestCase):
    def test_format_polymarket_prefix(self) -> None:
        text = format_ops_payload(
            {
                "ts": "2026-08-26T02:00:00+08:00",
                "latest_nav_cents": 11938,
                "rate_limit_1h": 2,
                "alerts": [
                    {
                        "severity": "critical",
                        "code": "redeem_needed",
                        "message": "Redeem needed: 1",
                    }
                ],
            }
        )
        self.assertIn("[Polymarket]", text)
        self.assertIn("redeem_needed", text)

    def test_fingerprint_stable(self) -> None:
        alerts = [{"severity": "warning", "code": "x", "message": "m"}]
        self.assertEqual(_fingerprint(alerts), _fingerprint(list(alerts)))

    @patch.dict("os.environ", {"DELPHI_ALERT_WEBHOOK_URL": "https://discord.test/hook"})
    def test_resolve_webhook(self) -> None:
        self.assertEqual(resolve_webhook_url(), "https://discord.test/hook")

    @patch("src.utils.discord_alerts.send_discord_message", return_value=True)
    def test_notify_dedupes_same_fingerprint(self, mock_send: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            payload = {
                "ts": "2026-08-26T02:00:00+08:00",
                "alerts": [
                    {"severity": "critical", "code": "orphan_open", "message": "1 orphan"},
                ],
            }
            with patch.dict("os.environ", {"DELPHI_ALERT_WEBHOOK_URL": "https://discord.test/hook"}):
                self.assertTrue(notify_ops_alerts(payload, state_path=state))
                self.assertFalse(notify_ops_alerts(payload, state_path=state))
            self.assertEqual(mock_send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
