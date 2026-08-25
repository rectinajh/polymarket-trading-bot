"""Tests for Relayer-based proxy wallet redeem."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.clients.relayer_redeem import relayer_configured, redeem_via_relayer


class TestRelayerRedeem(unittest.IsolatedAsyncioTestCase):
    @patch.dict(
        "os.environ",
        {
            "POLYMARKET_RELAYER_API_KEY": "test-key",
            "POLYMARKET_RELAYER_API_KEY_ADDRESS": "0xE3FBCE7898d7b92591730cDD63A859F90F89D110",
        },
    )
    def test_relayer_configured(self) -> None:
        self.assertTrue(relayer_configured())

    def test_relayer_not_configured(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(relayer_configured())

    @patch.dict(
        "os.environ",
        {
            "POLYMARKET_RELAYER_API_KEY": "test-key",
            "POLYMARKET_RELAYER_API_KEY_ADDRESS": "0xE3FBCE7898d7b92591730cDD63A859F90F89D110",
        },
    )
    @patch("polymarket.AsyncSecureClient")
    async def test_redeem_via_relayer(self, mock_client_cls: MagicMock) -> None:
        outcome = MagicMock(transaction_hash="0xabc", transaction_id="tx-1")
        handle = MagicMock()
        handle.wait = AsyncMock(return_value=outcome)
        client = MagicMock()
        client.redeem_positions = AsyncMock(return_value=handle)
        client.close = AsyncMock()
        mock_client_cls.create = AsyncMock(return_value=client)

        result = await redeem_via_relayer(
            private_key="0x" + "11" * 32,
            wallet="0xfbaa20141fe693925dd13533d3ab0b3dec330ad7",
            condition_id="0x" + "aa" * 32,
        )
        self.assertEqual(result["via"], "relayer")
        self.assertEqual(result["tx_hash"], "0xabc")
        client.close.assert_awaited_once()


class TestPolymarketClientProxyRedeem(unittest.IsolatedAsyncioTestCase):
    @patch.dict(
        "os.environ",
        {
            "POLYMARKET_RELAYER_API_KEY": "test-key",
            "POLYMARKET_RELAYER_API_KEY_ADDRESS": "0xE3FBCE7898d7b92591730cDD63A859F90F89D110",
            "POLYMARKET_PRIVATE_KEY": "0x" + "22" * 32,
            "POLYMARKET_FUNDER": "0xfbaa20141fe693925dd13533d3ab0b3dec330ad7",
        },
    )
    @patch("src.clients.relayer_redeem.redeem_via_relayer", new_callable=AsyncMock)
    async def test_redeem_condition_uses_relayer_for_proxy(
        self, mock_redeem: AsyncMock,
    ) -> None:
        from src.clients.polymarket_client import PolymarketClient

        mock_redeem.return_value = {"via": "relayer", "tx_hash": "0x1", "condition_id": "0xabc"}
        client = PolymarketClient(signature_type=3)
        client._get_funding_address = MagicMock(return_value="0xfbaa20141fe693925dd13533d3ab0b3dec330ad7")
        result = await client.redeem_condition("0x" + "ab" * 32)
        self.assertEqual(result["via"], "relayer")
        mock_redeem.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
