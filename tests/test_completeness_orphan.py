"""Completeness arb orphan hook tests."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.strategies.completeness_arb import CompletenessArb
from src.strategies.orphan_unwind import OrphanRegistry


class TestCompletenessOrphanHook(unittest.IsolatedAsyncioTestCase):
    @patch("src.strategies.completeness_arb.process_orphans", new_callable=AsyncMock)
    async def test_run_sweeps_orphans_at_start(self, mock_sweep: AsyncMock) -> None:
        mock_sweep.return_value = {"pending": 0, "unwound": 0, "forced": 0}
        client = MagicMock()
        client.get_balance = AsyncMock(return_value={"balance": 10000, "portfolio_value": 0})
        client.flush_token_cache = MagicMock()
        gamma = MagicMock()
        gamma.close = AsyncMock()
        arb = CompletenessArb(client=client, gamma=gamma, dry_run=True)
        arb._orphans = OrphanRegistry()
        stats = await arb.run(markets=[])
        mock_sweep.assert_awaited_once()
        self.assertIn("orphan_pending", stats)

    def test_partial_fail_registers_orphan(self) -> None:
        import tempfile
        from pathlib import Path
        from src.strategies.orphan_unwind import register_partial_fail

        with tempfile.TemporaryDirectory() as tmp:
            reg = OrphanRegistry(Path(tmp) / "orphans.json")
            register_partial_fail(
                condition_id="0xabc",
                filled_side="yes",
                quantity=5,
                entry_cents=45,
                strategy="completeness_arb",
                slug="test market",
                registry=reg,
            )
            self.assertEqual(len(reg.list()), 1)
            self.assertEqual(reg.list()[0]["strategy"], "completeness_arb")


if __name__ == "__main__":
    unittest.main()
