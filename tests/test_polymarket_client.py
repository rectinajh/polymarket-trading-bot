"""
Unit tests for src/clients/polymarket_client.py.

Mocks the underlying py-clob-client and web3 so the suite runs without a
private key, RPC endpoint, or network access. The goal is to lock down the
adapter's contract: legacy-shape responses, cents↔dollars conversion, error
classification, and token_id resolution.

Run:
    pytest tests/test_polymarket_client.py -v
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.clients.polymarket_client import (
    PolymarketClient,
    PolymarketAPIError,
    PolymarketAuthError,
    InsufficientFundsError,
    AllowanceError,
    RateLimitError,
    UnknownMarketError,
    TokenIds,
    _classify_order_error,
    _clob_tick_size,
    _normalize_order_response,
    _bids_to_levels,
    _asks_to_levels,
    _best_bid_dollars,
    _best_ask_dollars,
    POLYMARKET_SPENDERS,
    USDC_E_POLYGON,
    ALLOWANCE_OK_THRESHOLD,
)


# Dummy 64-hex private key (tests never sign with it)
DUMMY_PK = "0x" + "1" * 64


def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Construction & lazy init
# --------------------------------------------------------------------------

class TestConstruction(unittest.TestCase):
    """Constructor must not perform any network I/O."""

    def test_constructor_no_network(self):
        # No host calls, no key validation beyond emptiness check
        c = PolymarketClient(private_key=DUMMY_PK)
        self.assertEqual(c.chain_id, 137)
        self.assertEqual(c.signature_type, 0)
        self.assertEqual(c.host, "https://clob.polymarket.com")
        self.assertIsNone(c._client)
        self.assertIsNone(c._w3)
        self.assertFalse(c._api_creds_set)

    def test_missing_key_warns_but_does_not_raise(self):
        # Per design: importing/constructing should never fail. Only
        # authenticated calls fail. Health-check needs this property.
        c = PolymarketClient(private_key="")
        self.assertIsNotNone(c)


# --------------------------------------------------------------------------
# Token-id resolution
# --------------------------------------------------------------------------

class TestTokenIdResolution(unittest.TestCase):
    """register_market populates the cache; _resolve_token_id reads it.
    Falls back to attached gamma_client. Raises UnknownMarketError otherwise."""

    def test_register_and_get(self):
        c = PolymarketClient(private_key=DUMMY_PK)
        c.register_market("0xabc", "yes_id", "no_id")
        self.assertEqual(c.get_token_ids("0xabc"), TokenIds(yes="yes_id", no="no_id"))
        self.assertIsNone(c.get_token_ids("0xnope"))

    def test_resolve_yes_no(self):
        c = PolymarketClient(private_key=DUMMY_PK)
        c.register_market("0xabc", "Y", "N")
        self.assertEqual(_run(c._resolve_token_id("0xabc", "YES")), "Y")
        self.assertEqual(_run(c._resolve_token_id("0xabc", "no")), "N")

    def test_resolve_unknown_raises(self):
        c = PolymarketClient(private_key=DUMMY_PK)
        with self.assertRaises(UnknownMarketError):
            _run(c._resolve_token_id("0xunknown", "YES"))

    def test_resolve_via_gamma_fallback(self):
        # Use an isolated cache path so a stale on-disk cache from another
        # test/run doesn't pre-warm the entry and skip the gamma fallback.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            c = PolymarketClient(private_key=DUMMY_PK, token_cache_path=cache)
            gamma = MagicMock()
            gamma.get_token_ids = AsyncMock(return_value=("Y", "N"))
            c.set_gamma_client(gamma)

            result = _run(c._resolve_token_id("0xnew", "YES"))
            self.assertEqual(result, "Y")
            self.assertEqual(c.get_token_ids("0xnew"), TokenIds(yes="Y", no="N"))
            gamma.get_token_ids.assert_called_once()

    def test_get_market_resolves_via_gamma_without_preregister(self):
        """The live bug: strategies call get_market(condition_id) on a client
        that never went through ingest.register_market. Gamma must fill it in."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            c = PolymarketClient(private_key=DUMMY_PK, token_cache_path=cache)
            gamma = MagicMock()
            gamma.get_token_ids = AsyncMock(return_value=("yes_tok", "no_tok"))
            gamma.get_market = AsyncMock(return_value={
                "question": "Will X happen?",
                "category": "politics",
            })
            c.set_gamma_client(gamma)

            async def fake_book(token_id, depth=100):
                book = MagicMock()
                book.bids = []
                book.asks = []
                return book

            c._fetch_book_one = fake_book
            result = _run(c.get_market("0xdead"))
            self.assertEqual(result["market"]["yes_token_id"], "yes_tok")
            self.assertEqual(result["market"]["no_token_id"], "no_tok")
            gamma.get_token_ids.assert_called_once_with("0xdead")

    def test_invalid_side_raises_value_error(self):
        c = PolymarketClient(private_key=DUMMY_PK)
        c.register_market("0xabc", "Y", "N")
        with self.assertRaises(ValueError):
            _run(c._resolve_token_id("0xabc", "BAD"))


# --------------------------------------------------------------------------
# Balance (USDC.e)
# --------------------------------------------------------------------------

class TestGetBalance(unittest.TestCase):
    """USDC.e is 6-decimal — verify the conversion from raw to dollars+cents."""

    def test_balance_conversion(self):
        c = PolymarketClient(private_key=DUMMY_PK)
        # Mock the web3 contract round-trip: 1234.56 USDC.e = 1234560000 raw
        with patch.object(c, "_ensure_w3") as mock_w3, \
             patch.object(c, "_get_funding_address", return_value="0xabc"):
            mock_contract = MagicMock()
            mock_contract.functions.balanceOf.return_value.call.return_value = 1234560000
            w3 = MagicMock()
            w3.eth.contract.return_value = mock_contract
            w3.to_checksum_address.side_effect = lambda x: x
            mock_w3.return_value = w3

            result = _run(c.get_balance())

        self.assertAlmostEqual(result["balance_dollars"], 1234.56, places=2)
        self.assertEqual(result["balance"], 123456)  # cents
        self.assertEqual(result["address"], "0xabc")
        self.assertEqual(result["portfolio_value"], 0)

    def test_balance_failure_raises_polymarket_error(self):
        c = PolymarketClient(private_key=DUMMY_PK)
        with patch.object(c, "_ensure_w3") as mock_w3, \
             patch.object(c, "_get_funding_address", return_value="0xabc"):
            w3 = MagicMock()
            w3.eth.contract.side_effect = RuntimeError("RPC down")
            w3.to_checksum_address.side_effect = lambda x: x
            mock_w3.return_value = w3
            with self.assertRaises(PolymarketAPIError):
                _run(c.get_balance())


# --------------------------------------------------------------------------
# Order placement
# --------------------------------------------------------------------------

class TestPlaceOrder(unittest.TestCase):
    """Ensures cents → dollars conversion, side resolution, and price-range
    sanity guards. Mocks the SDK so no actual signing/sending happens."""

    def setUp(self):
        # place_order refuses unless live trading is enabled
        from src.config.settings import settings
        self._prev_live = settings.trading.live_trading_enabled
        self._prev_paper = settings.trading.paper_trading_mode
        settings.trading.live_trading_enabled = True
        settings.trading.paper_trading_mode = False

    def tearDown(self):
        from src.config.settings import settings
        settings.trading.live_trading_enabled = self._prev_live
        settings.trading.paper_trading_mode = self._prev_paper

    def _setup_client(self):
        c = PolymarketClient(private_key=DUMMY_PK)
        c.register_market("0xabc", "yes_token", "no_token")
        # Stub api creds so _ensure_api_creds is a no-op
        c._api_creds_set = True
        return c

    def test_place_order_blocked_when_not_live(self):
        from src.config.settings import settings
        settings.trading.live_trading_enabled = False
        c = self._setup_client()
        with self.assertRaises(PolymarketAPIError) as ctx:
            _run(c.place_order(
                ticker="0xabc", client_order_id="cid-1", side="yes", action="buy",
                count=10, type_="limit", yes_price=35,
            ))
        self.assertIn("live trading is disabled", str(ctx.exception).lower())

    def test_limit_yes_buy_converts_cents_to_dollars(self):
        c = self._setup_client()
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY

        captured = {}
        def fake_create_order(args, options):
            captured["args"] = args
            captured["options"] = options
            return "signed-order-stub"
        def fake_post_order(signed, order_type):
            captured["order_type"] = order_type
            return {"success": True, "orderID": "order-123", "status": "live"}

        mock_clob = MagicMock()
        mock_clob.create_order = fake_create_order
        mock_clob.post_order = fake_post_order
        c._client = mock_clob

        result = _run(c.place_order(
            ticker="0xabc", client_order_id="cid-1", side="yes", action="buy",
            count=10, type_="limit", yes_price=35,  # 35 cents
        ))

        args = captured["args"]
        self.assertIsInstance(args, OrderArgs)
        self.assertEqual(args.token_id, "yes_token")
        self.assertAlmostEqual(args.price, 0.35, places=4)
        self.assertEqual(args.size, 10)
        self.assertEqual(args.side, BUY)
        self.assertEqual(captured["order_type"], OrderType.GTC)
        # Routing options carry tick_size / neg_risk (defaults from register_market)
        self.assertIsInstance(captured["options"], PartialCreateOrderOptions)
        self.assertEqual(captured["options"].neg_risk, False)
        self.assertEqual(captured["options"].tick_size, "0.01")
        # Response shape
        self.assertEqual(result["order"]["order_id"], "order-123")
        self.assertEqual(result["order"]["side"], "YES")
        self.assertEqual(result["order"]["action"], "buy")
        self.assertEqual(result["order"]["token_id"], "yes_token")

    def test_market_no_buy_uses_amount_in_dollars(self):
        c = self._setup_client()
        from py_clob_client.clob_types import MarketOrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY

        captured = {}
        def fake_create_market(args, options):
            captured["args"] = args
            captured["options"] = options
            return "signed-market-stub"
        mock_clob = MagicMock()
        mock_clob.create_market_order = fake_create_market
        mock_clob.post_order = MagicMock(return_value={"success": True, "orderID": "m1"})
        c._client = mock_clob

        # 5 contracts × $0.40 = $2.00 USDC spend
        _run(c.place_order(
            ticker="0xabc", client_order_id="cid-2", side="no", action="buy",
            count=5, type_="market", no_price=40,
        ))

        args = captured["args"]
        self.assertIsInstance(args, MarketOrderArgs)
        self.assertEqual(args.token_id, "no_token")
        self.assertAlmostEqual(args.amount, 2.00, places=4)
        self.assertEqual(args.side, BUY)
        self.assertIsInstance(captured["options"], PartialCreateOrderOptions)
        # post_order called with FOK for market
        mock_clob.post_order.assert_called_once()
        _signed, order_type = mock_clob.post_order.call_args[0]
        self.assertEqual(order_type, OrderType.FOK)

    def test_neg_risk_routing_propagates(self):
        """Markets registered with neg_risk=True must pass that flag in
        PartialCreateOrderOptions to the SDK — otherwise the CLOB rejects
        the order with `not allowed for this market`."""
        c = PolymarketClient(private_key=DUMMY_PK)
        c.register_market("0xnr", "y", "n", neg_risk=True, tick_size=0.001)
        c._api_creds_set = True

        captured = {}
        mock_clob = MagicMock()
        def fake_create_order(args, options):
            captured["options"] = options
            return "signed"
        mock_clob.create_order = fake_create_order
        mock_clob.post_order = MagicMock(return_value={"success": True, "orderID": "x"})
        c._client = mock_clob

        # Use a tick-aligned price for tick_size=0.001
        _run(c.place_order(
            ticker="0xnr", client_order_id="cid", side="yes", action="buy",
            count=1, type_="limit", yes_price=35,
        ))
        self.assertTrue(captured["options"].neg_risk)
        self.assertEqual(captured["options"].tick_size, "0.001")

    def test_token_cache_persistence(self):
        """Persisting the token_id cache to disk lets a fresh process start
        avoid the Gamma round-trip on the first order. Round-trip the cache
        through a temp file and verify it loads back identically."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            c1 = PolymarketClient(private_key=DUMMY_PK, token_cache_path=cache_path)
            c1.register_market("0xa", "y", "n", neg_risk=True, tick_size=0.01)
            c1.register_market("0xb", "y2", "n2", neg_risk=False, tick_size=0.001)

            self.assertTrue(cache_path.exists())

            # Fresh client reading the same path should see both entries
            c2 = PolymarketClient(private_key=DUMMY_PK, token_cache_path=cache_path)
            self.assertEqual(c2.get_token_ids("0xa"), TokenIds(yes="y", no="n", neg_risk=True, tick_size=0.01))
            self.assertEqual(c2.get_token_ids("0xb"), TokenIds(yes="y2", no="n2", neg_risk=False, tick_size=0.001))

    def test_limit_without_price_raises(self):
        c = self._setup_client()
        c._client = MagicMock()  # avoid _ensure_clob lazy init
        with self.assertRaises(PolymarketAPIError) as ctx:
            _run(c.place_order(
                ticker="0xabc", client_order_id="x", side="yes", action="buy",
                count=1, type_="limit",  # no yes_price
            ))
        self.assertIn("require yes_price or no_price", str(ctx.exception))

    def test_price_outside_valid_range_rejected(self):
        c = self._setup_client()
        c._client = MagicMock()
        # 99 cents = $0.99 (boundary) is OK; 100 cents = $1.00 is rejected
        with self.assertRaises(PolymarketAPIError) as ctx:
            _run(c.place_order(
                ticker="0xabc", client_order_id="x", side="yes", action="buy",
                count=1, type_="limit", yes_price=100,
            ))
        self.assertIn("outside the valid range", str(ctx.exception))

    def test_invalid_action_raises(self):
        c = self._setup_client()
        c._client = MagicMock()
        with self.assertRaises(ValueError):
            _run(c.place_order(
                ticker="0xabc", client_order_id="x", side="yes",
                action="hodl", count=1, type_="market",
            ))

    def test_unknown_market_raises(self):
        c = PolymarketClient(private_key=DUMMY_PK)  # no register_market
        c._api_creds_set = True
        c._client = MagicMock()
        with self.assertRaises(UnknownMarketError):
            _run(c.place_order(
                ticker="0xunknown", client_order_id="x", side="yes",
                action="buy", count=1, type_="market", yes_price=50,
            ))


class TestClobTickSize(unittest.TestCase):
    """Float ticks must become the string literals the SDK ROUNDING_CONFIG uses."""

    def test_common_ticks(self):
        self.assertEqual(_clob_tick_size(0.01), "0.01")
        self.assertEqual(_clob_tick_size(0.001), "0.001")
        self.assertEqual(_clob_tick_size("0.1"), "0.1")
        self.assertEqual(_clob_tick_size(0), "0.01")
        self.assertEqual(_clob_tick_size(None), "0.01")


# --------------------------------------------------------------------------
# Error classification
# --------------------------------------------------------------------------

class TestErrorClassification(unittest.TestCase):
    """_classify_order_error must map raw SDK strings to typed exceptions
    so callers can react to specific failure classes."""

    def test_insufficient_funds(self):
        exc = _classify_order_error(RuntimeError("Insufficient funds for trade"))
        self.assertIsInstance(exc, InsufficientFundsError)

    def test_allowance(self):
        exc = _classify_order_error(RuntimeError("ERC20 allowance not set"))
        self.assertIsInstance(exc, AllowanceError)
        self.assertIn("scripts/set_allowances.py", str(exc))

    def test_rate_limit(self):
        exc = _classify_order_error(RuntimeError("429 rate limit exceeded"))
        self.assertIsInstance(exc, RateLimitError)

    def test_signature_failure(self):
        exc = _classify_order_error(RuntimeError("Signature verification failed"))
        self.assertIsInstance(exc, PolymarketAuthError)

    def test_generic_fallback(self):
        exc = _classify_order_error(RuntimeError("Some unrelated error"))
        self.assertIsInstance(exc, PolymarketAPIError)
        self.assertNotIsInstance(exc, (InsufficientFundsError, AllowanceError, RateLimitError, PolymarketAuthError))


# --------------------------------------------------------------------------
# Orderbook normalization
# --------------------------------------------------------------------------

class TestOrderbookHelpers(unittest.TestCase):
    """The polymarket_client maps SDK OrderBookSummary into a legacy-style
    list of `[price_str, size_str]` levels — verify both attribute and dict
    style level objects are accepted, and ordering is correct."""

    def test_bids_sorted_highest_first(self):
        book = MagicMock()
        book.bids = [
            MagicMock(price="0.40", size="100"),
            MagicMock(price="0.42", size="50"),
            MagicMock(price="0.38", size="200"),
        ]
        levels = _bids_to_levels(book)
        # Highest bid first
        self.assertEqual([float(lv[0]) for lv in levels], [0.42, 0.40, 0.38])

    def test_asks_sorted_lowest_first(self):
        book = MagicMock()
        book.asks = [
            MagicMock(price="0.55", size="100"),
            MagicMock(price="0.50", size="50"),
            MagicMock(price="0.60", size="200"),
        ]
        levels = _asks_to_levels(book)
        self.assertEqual([float(lv[0]) for lv in levels], [0.50, 0.55, 0.60])

    def test_best_bid_ask(self):
        book = MagicMock()
        book.bids = [MagicMock(price="0.42", size="50")]
        book.asks = [MagicMock(price="0.50", size="50")]
        self.assertEqual(_best_bid_dollars(book), 0.42)
        self.assertEqual(_best_ask_dollars(book), 0.50)

    def test_dict_style_level_supported(self):
        # Some SDK callbacks return plain dicts instead of OrderSummary objs
        class DictBook:
            bids = [{"price": "0.30", "size": "100"}]
            asks = [{"price": "0.32", "size": "100"}]
        self.assertEqual(_best_bid_dollars(DictBook), 0.30)
        self.assertEqual(_best_ask_dollars(DictBook), 0.32)

    def test_empty_book(self):
        class Empty:
            bids = []
            asks = []
        self.assertEqual(_best_bid_dollars(Empty), 0.0)
        self.assertEqual(_best_ask_dollars(Empty), 0.0)


# --------------------------------------------------------------------------
# Allowance threshold
# --------------------------------------------------------------------------

class TestAllowances(unittest.TestCase):
    """check_allowances reads each spender's USDC allowance and returns a
    boolean per spender. Threshold is 10M USDC (any approve(MAX) clears it)."""

    def test_check_allowances(self):
        c = PolymarketClient(private_key=DUMMY_PK)
        # Two spenders set, one not
        async def fake_get_allowance(spender):
            return ALLOWANCE_OK_THRESHOLD if "0x4b" in spender or "0xC5" in spender else 0
        with patch.object(c, "get_allowance", side_effect=fake_get_allowance):
            result = _run(c.check_allowances())
        self.assertEqual(result["ctf_exchange"], True)
        self.assertEqual(result["neg_risk_exchange"], True)
        self.assertEqual(result["neg_risk_adapter"], False)


# --------------------------------------------------------------------------
# Response normalization
# --------------------------------------------------------------------------

class TestNormalizeOrderResponse(unittest.TestCase):
    def test_dict_response(self):
        out = _normalize_order_response(
            {"orderID": "abc-123", "success": True, "status": "live"},
            condition_id="0xc", token_id="t1", side="yes", action="buy",
            count=10, client_order_id="cid",
        )
        self.assertEqual(out["order"]["order_id"], "abc-123")
        self.assertEqual(out["order"]["status"], "live")
        self.assertEqual(out["order"]["side"], "YES")
        self.assertEqual(out["order"]["count"], 10)
        self.assertTrue(out["success"])

    def test_non_dict_response(self):
        # Some SDKs return a SignedOrder or string — must still wrap cleanly
        out = _normalize_order_response(
            "raw-string-response",
            condition_id="0xc", token_id="t1", side="no", action="sell",
            count=5, client_order_id="cid",
        )
        self.assertEqual(out["order"]["order_id"], "cid")
        self.assertEqual(out["order"]["status"], "submitted")
        self.assertTrue(out["success"])


# --------------------------------------------------------------------------
# Get-orders status alias mapping
# --------------------------------------------------------------------------

class TestStatusAlias(unittest.TestCase):
    """The 'resting' legacy-vocabulary status must map to Polymarket
    {LIVE, MATCHED, open, OPEN}."""

    def test_resting_mapping(self):
        aliases = PolymarketClient._STATUS_ALIASES
        # 'resting' = anything actually resting on the book OR partially matched
        self.assertIn("LIVE", aliases["resting"])
        self.assertIn("MATCHED", aliases["resting"])
        # 'open' is broader than 'live' — includes both case variants of LIVE/OPEN
        self.assertIn("OPEN", aliases["open"])
        self.assertIn("LIVE", aliases["open"])
        # 'live' is strict — only the literal LIVE status
        self.assertIn("LIVE", aliases["live"])


if __name__ == "__main__":
    unittest.main()
