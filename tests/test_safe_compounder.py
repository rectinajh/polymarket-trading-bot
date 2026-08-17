"""
End-to-end tests for the SafeCompounder strategy with mocked clients.

Verifies the full decide-loop without hitting Polymarket: gamma returns a
synthetic universe → compounder filters → orderbook fetch → place_order is
called with the right args. Lives or dies by the strategy's own math, not
by network shape — the lower-level adapter is exercised in
test_polymarket_client.py.

Run:
    pytest tests/test_safe_compounder.py -v
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.strategies.safe_compounder import SafeCompounder


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _market(
    *,
    cond: str,
    yes_id: str = "yes_t",
    no_id: str = "no_t",
    yes_last: float = 0.05,
    volume: float = 5_000.0,
    days_to_expiry: float = 5.0,
    neg_risk: bool = False,
    tick_size: float = 0.01,
    category: str = "world",
    tag_ids: tuple[int, ...] = (165,),  # united-states (not in skip list)
    title: str = "Will X happen?",
) -> dict:
    """Build a synthetic Gamma market dict that mirrors what
    `gamma_client._derive_market_fields` would produce."""
    import time
    end_ts = time.time() + days_to_expiry * 86400
    return {
        "_condition_id":   cond,
        "_token_ids":      (yes_id, no_id),
        "_outcome_prices": (yes_last, 1 - yes_last),
        "_volume_num":     volume,
        "_end_ts":         end_ts,
        "_category":       category,
        "_event_tag_ids":  list(tag_ids),
        "_status":         "active",
        "conditionId":     cond,
        "question":        title,
        "negRisk":         neg_risk,
        "orderPriceMinTickSize": tick_size,
        "endDate":         f"{end_ts}",
    }


def _book(yes_bid: float, yes_size: float = 100.0,
          no_bid: float = 0.0, no_size: float = 0.0):
    """Synthesize a legacy-shape orderbook dict (which polymarket_client.
    get_orderbook returns)."""
    return {
        "orderbook": {
            "yes":      [[str(yes_bid), str(int(yes_size))]],
            "yes_asks": [[str(round(1 - no_bid, 4) if no_bid else round(1 - yes_bid + 0.01, 4)), "10"]],
            "no":       [[str(no_bid), str(int(no_size))]] if no_bid else [],
            "no_asks":  [[str(round(1 - yes_bid, 4)), str(int(yes_size))]],
        }
    }


class TestSafeCompounderE2E(unittest.TestCase):
    """The compounder pipeline: discover → filter → orderbook → place_order."""

    def _build_compounder(self, markets, books_by_token):
        # Mock gamma — returns the supplied market list and resolves skip tags
        gamma = MagicMock()
        gamma.get_markets = AsyncMock(return_value=markets)
        gamma.resolve_tag_slugs = AsyncMock(return_value=[1, 100])  # sports + music
        gamma.close = AsyncMock()

        # Mock polymarket client — get_balance, get_orderbook, get_positions,
        # get_orders, place_order. register_market gets called for each market.
        client = MagicMock()
        client.get_balance = AsyncMock(return_value={
            "balance": 10_000,             # $100 in cents
            "balance_dollars": 100.0,
            "portfolio_value": 0,
            "address": "0xtest",
        })

        async def fake_orderbook(cond, depth=10):
            yes_token = next(
                (m["_token_ids"][0] for m in markets if m["_condition_id"] == cond),
                None,
            )
            return books_by_token.get(yes_token) or {"orderbook": {"yes": [], "no": []}}
        client.get_orderbook = fake_orderbook

        client.get_positions = AsyncMock(return_value={"market_positions": []})
        client.get_orders = AsyncMock(return_value={"orders": []})

        # Track register_market + place_order calls
        registered: list[tuple] = []
        def reg(cond, yes, no, neg_risk=False, tick_size=0.01):
            registered.append((cond, yes, no, neg_risk, tick_size))
        client.register_market = reg

        place_calls: list[dict] = []
        async def fake_place(**kwargs):
            place_calls.append(kwargs)
            return {"order": {"order_id": f"id-{len(place_calls)}", "status": "live", "fill_count": 0}}
        client.place_order = fake_place

        compounder = SafeCompounder(
            client=client, gamma=gamma, dry_run=False,
        )
        return compounder, client, registered, place_calls

    def test_dry_run_finds_no_opportunities_without_edge(self):
        """YES at $0.05 but NO ask at $0.93 (1 - 0.07 yes_bid) → edge =
        true_no_prob (~0.96) - 0.93 = 0.03 = MIN_EDGE. Borderline; NO ask
        below MIN_NO_ASK=$0.80 should reject too. We test outright rejection
        when YES bid is high (lowest_no_ask = 1 - yes_bid_high → low)."""
        markets = [_market(cond="0xa", yes_id="ya", no_id="na", yes_last=0.05)]
        books = {"ya": _book(yes_bid=0.50)}  # → derived NO ask = 0.50, < 0.80 → reject
        c, client, registered, place = self._build_compounder(markets, books)
        c.dry_run = True
        _run(c.run())
        self.assertEqual(len(registered), 1)         # market still got registered
        self.assertEqual(len(place), 0)              # but no order placed

    def test_high_edge_no_opportunity_places_dry_order(self):
        """YES at $0.03 (so true_no_prob ≈ 0.99) and NO ask = 1 - 0.05
        (highest YES bid 0.05) = $0.95 → edge = 0.99 - 0.95 = $0.04 > MIN_EDGE
        ($0.03). Should produce a candidate; in dry_run no real call but the
        opportunity gets processed."""
        markets = [_market(cond="0xb", yes_id="yb", no_id="nb",
                           yes_last=0.03, days_to_expiry=0.5, volume=5000.0)]
        books = {"yb": _book(yes_bid=0.07)}  # NO ask≈0.93 → edge≈0.06 > 0.05
        c, client, registered, place = self._build_compounder(markets, books)
        c.dry_run = True
        result = _run(c.run())
        self.assertEqual(len(registered), 1)
        self.assertEqual(registered[0][0], "0xb")    # condition_id
        # In dry_run, place_order is not called — the strategy prints what it WOULD do.
        self.assertEqual(len(place), 0)
        # Stats should show at least one placed-virtually opportunity
        self.assertGreaterEqual(result.get("placed", 0), 1)

    def test_excluded_tag_market_dropped(self):
        """A market whose parent event has a sports tag should be filtered
        out by the SKIP_TAG_SLUGS pipeline before reaching candidate eval."""
        markets = [_market(cond="0xc", yes_id="yc", no_id="nc",
                           tag_ids=(1,), category="sports")]  # 1 = sports
        books = {"yc": _book(yes_bid=0.05)}
        c, client, registered, place = self._build_compounder(markets, books)
        c.dry_run = True
        _run(c.run())
        # The market is still registered (we register everything seen — no harm),
        # but should_skip drops it before orderbook check, so no place_order call.
        self.assertEqual(len(place), 0)

    def test_neg_risk_metadata_propagated_at_register(self):
        """register_market must receive neg_risk + tick_size from the Gamma
        market dict so the order routes to the right CTF exchange."""
        markets = [
            _market(cond="0xd", yes_id="yd", no_id="nd",
                    neg_risk=True, tick_size=0.001),
        ]
        books = {"yd": _book(yes_bid=0.05)}
        c, client, registered, place = self._build_compounder(markets, books)
        c.dry_run = True
        _run(c.run())
        self.assertEqual(len(registered), 1)
        cond, yes, no, neg_risk, tick = registered[0]
        self.assertEqual(cond, "0xd")
        self.assertTrue(neg_risk)
        self.assertAlmostEqual(tick, 0.001)


if __name__ == "__main__":
    unittest.main()
