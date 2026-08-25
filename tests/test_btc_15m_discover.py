"""Unit tests for crypto 15m slug discovery (no network)."""

from __future__ import annotations

import unittest

from src.strategies.btc_15m_completeness.discover import (
    aligned_window_start,
    asset_from_slug,
    multi_asset_slugs,
    normalize_assets,
    seconds_to_window_end,
    window_slugs,
)


class TestBtc15mDiscover(unittest.TestCase):
    def test_aligned_window(self) -> None:
        ts = 1787293650
        start = aligned_window_start(ts)
        self.assertEqual(start % 900, 0)
        self.assertLessEqual(start, int(ts))
        self.assertLess(int(ts) - start, 900)

    def test_window_slugs_shape(self) -> None:
        ts = 1_700_000_000
        slugs = window_slugs(now_ts=ts, behind=1, ahead=2)
        self.assertEqual(len(slugs), 4)  # -1,0,1,2
        for s in slugs:
            self.assertTrue(s.startswith("btc-updown-15m-"))
            self.assertTrue(s.rsplit("-", 1)[-1].isdigit())

    def test_eth_slugs(self) -> None:
        slugs = window_slugs(asset="eth", now_ts=1_700_000_000, behind=0, ahead=0)
        self.assertEqual(len(slugs), 1)
        self.assertTrue(slugs[0].startswith("eth-updown-15m-"))

    def test_multi_asset_default_btc_eth(self) -> None:
        self.assertEqual(normalize_assets(), ["btc", "eth"])
        slugs = multi_asset_slugs(now_ts=1_700_000_000, behind=0, ahead=0)
        self.assertEqual(len(slugs), 2)
        self.assertTrue(slugs[0].startswith("btc-updown-15m-"))
        self.assertTrue(slugs[1].startswith("eth-updown-15m-"))
        self.assertEqual(asset_from_slug(slugs[1]), "eth")

    def test_seconds_to_end(self) -> None:
        start = aligned_window_start(1_700_000_100)
        left = seconds_to_window_end(1_700_000_100)
        self.assertEqual(left, start + 900 - 1_700_000_100)


if __name__ == "__main__":
    unittest.main()
