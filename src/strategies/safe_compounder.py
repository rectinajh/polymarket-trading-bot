"""
Safe Compounder Strategy — NO-side, edge-based, capital-efficient.

STRATEGY:
- NO side ONLY
- Find near-certain outcomes (EV ~95-99¢)
- Edge = estimated_true_prob - lowest_no_ask > MIN_EDGE
- Lowest NO ask must be > MIN_NO_ASK ($0.80)
- Place resting order at lowest_no_ask - 1¢ (maker trade, near-zero fees)
- Position size: max 10% of portfolio value per position (Kelly optional)

KEY INSIGHT: We estimate true probability dynamically:
- YES last price is the primary signal (lower = more certain NO wins)
- Time to expiry amplifies certainty (if YES is at 3¢ with 2 days left ≈ 99%)
- Compare EV estimate to the actual NO ask price; trade only when edge > MIN_EDGE.

Polymarket port: market discovery now goes through GammaClient. The original
Polymarket `KX*` prefix skiplist is replaced by Polymarket tag exclusion (sports,
awards, music, pop-culture etc.) — same intent, different mechanism. Edge
math is exchange-agnostic and unchanged.

Available via: python cli.py run --safe-compounder
"""

import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import aiosqlite

from src.clients.gamma_client import GammaClient

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

# Polymarket tag slugs we exclude — too unpredictable for near-certain plays.
# Maps to Polymarket's tag taxonomy (resolved to numeric IDs by GammaClient).
SKIP_TAG_SLUGS = [
    "sports", "soccer", "basketball", "nba", "epl", "ucl", "champions-league",
    "fifa-world-cup", "f1", "formula1", "nfl", "mlb", "nhl", "ufc", "pga",
    "tennis", "boxing", "esports",
    "awards", "oscars", "emmys", "grammys", "music", "pop-culture",
    "entertainment", "tv", "movies", "gaming", "games",
]

# Title-phrase blocklist — markets phrased as "mention", "say in speech" etc.
# tend to be social-media/entertainment garbage even outside sports tags.
SKIP_TITLE_PHRASES = [
    "mention", "say in", "speech mention", "address mention",
]

# Thresholds (all in dollar format 0.00-1.00)
MIN_VOLUME = 10
MIN_NO_ASK = 0.80      # Lowest NO ask must be > $0.80
MIN_EDGE = 0.03        # Edge (EV - price) must be > $0.03 (loosened from $0.05, approved 2026-03-29)
MAX_POSITION_PCT = 0.10    # Max 10% of portfolio per position
USE_KELLY = True
MIN_CONFIDENCE = 0.4


# -----------------------------------------------------------------------
# Core math
# -----------------------------------------------------------------------

def should_skip(market: Dict) -> bool:
    """Polymarket port: skip markets whose parent event tags overlap with
    SKIP_TAG_SLUGS. Falls back to title-phrase matching when tags are absent.

    `market` is expected to be a Gamma market dict (post-derivation), with
    `_event_tag_ids` populated by `_derive_market_fields`. The skiplist of
    numeric IDs is computed once at strategy init and passed in via
    `market["_skip_tag_ids"]` (if present) — otherwise we fall back to slug
    membership.
    """
    skip_ids = market.get("_skip_tag_ids") or set()
    if skip_ids:
        evt_tag_ids = set(market.get("_event_tag_ids") or [])
        if evt_tag_ids & skip_ids:
            return True

    # Fallback: also drop markets whose own category slug is in the skiplist.
    cat = (market.get("_category") or "").lower()
    if cat and cat in {s.lower() for s in SKIP_TAG_SLUGS}:
        return True

    return False


def estimate_true_no_prob(yes_last: float, hours_to_expiry: float) -> float:
    """
    Estimate the true probability that NO wins.
    Returns estimated true NO probability in dollars (0.00-1.00).
    """
    base_prob = 1.0 - yes_last

    if hours_to_expiry <= 0:
        return base_prob

    if hours_to_expiry <= 24:
        if yes_last <= 0.05:
            return min(0.99, base_prob + 0.04)
        elif yes_last <= 0.10:
            return min(0.98, base_prob + 0.03)
        elif yes_last <= 0.15:
            return min(0.97, base_prob + 0.02)
        else:
            return min(0.96, base_prob + 0.01)
    elif hours_to_expiry <= 72:
        if yes_last <= 0.05:
            return min(0.99, base_prob + 0.03)
        elif yes_last <= 0.10:
            return min(0.97, base_prob + 0.02)
        else:
            return base_prob + 0.01
    elif hours_to_expiry <= 168:
        if yes_last <= 0.05:
            return min(0.98, base_prob + 0.02)
        elif yes_last <= 0.10:
            return min(0.96, base_prob + 0.01)
        else:
            return base_prob
    else:
        if yes_last <= 0.03:
            return min(0.97, base_prob + 0.01)
        return base_prob


def kelly_fraction(prob_win: float, payout_ratio: float) -> float:
    """Kelly fraction for a binary bet."""
    if payout_ratio <= 0 or prob_win <= 0:
        return 0.0
    prob_lose = 1.0 - prob_win
    f = (prob_win * payout_ratio - prob_lose) / payout_ratio
    return max(0.0, f)


def market_confidence_score(ticker: str, orderbook: dict, market: dict) -> Tuple[float, str]:
    """Return (confidence_score 0-1, reason_str) for a market."""
    reasons = []

    # Handle both new and old orderbook formats
    no_side = orderbook.get("no_dollars", orderbook.get("no", []))
    yes_side = orderbook.get("yes_dollars", orderbook.get("yes", []))

    all_levels = []
    for price_data, qty_data in yes_side:
        try:
            # Handle both old [price_cents, qty] and new [price_dollars_string, size_string]
            price = float(price_data)
            qty = int(qty_data)
            # Convert cents to dollars if needed
            if price > 1.0:
                price = price / 100.0
            all_levels.append((1.0 - price, qty))  # Convert YES to NO price in dollars
        except (ValueError, TypeError):
            continue
    
    for price_data, qty_data in no_side:
        try:
            price = float(price_data)
            qty = int(qty_data)
            # Convert cents to dollars if needed
            if price > 1.0:
                price = price / 100.0
            all_levels.append((price, qty))
        except (ValueError, TypeError):
            continue

    if all_levels:
        best_ask = min(p for p, q in all_levels)
        total_vol = sum(q for _, q in all_levels)
        vol_within_3c = sum(q for p, q in all_levels if p <= best_ask + 0.03)  # 3¢ = $0.03
        depth_ratio = vol_within_3c / max(total_vol, 1)
    else:
        depth_ratio = 0.0
        reasons.append("no book")

    best_no_ask = None
    if yes_side:
        try:
            highest_yes_bid = max(float(p) for p, q in yes_side)
            # Convert cents to dollars if needed
            if highest_yes_bid > 1.0:
                highest_yes_bid = highest_yes_bid / 100.0
            best_no_ask = 1.0 - highest_yes_bid
        except (ValueError, TypeError):
            pass
    
    best_no_bid = 0
    if no_side:
        try:
            best_no_bid = max(float(p) for p, q in no_side)
            # Convert cents to dollars if needed
            if best_no_bid > 1.0:
                best_no_bid = best_no_bid / 100.0
        except (ValueError, TypeError):
            pass

    if best_no_ask and best_no_bid > 0:
        spread = best_no_ask - best_no_bid
        spread_pct = spread / max(best_no_ask, 0.01)
        spread_score = max(0, 1.0 - (spread_pct / 0.10))
        if spread_pct > 0.05:
            reasons.append("wide spread")
    else:
        spread_score = 0.3
        if not reasons:
            reasons.append("unclear spread")

    volume = float(market.get("volume_fp", 0) or market.get("volume", 0) or 0)
    days_to_expiry = market.get("_days_to_expiry", 30)
    vol_per_day = volume / max(days_to_expiry, 1)
    volume_score = min(1.0, vol_per_day / 50.0)
    if vol_per_day < 10:
        reasons.append("thin volume")

    # Handle both new and old price formats
    yes_last = float(market.get("last_price_dollars", 0) or market.get("last_price", 0) or 0)
    # Convert old cent format to dollar format if needed
    if yes_last > 1.0:
        yes_last = yes_last / 100.0
    
    if best_no_ask:
        price_gap = abs(best_no_ask - (1.0 - yes_last))
        stability_score = max(0, 1.0 - (price_gap / 0.15))  # 15¢ = $0.15
        if price_gap > 0.08:  # 8¢ = $0.08
            reasons.append("price gap")
    else:
        stability_score = 0.3

    score = (
        depth_ratio * 0.30
        + spread_score * 0.30
        + volume_score * 0.25
        + stability_score * 0.15
    )

    reason_str = ", ".join(reasons) if reasons else "ok"
    return round(score, 3), reason_str


# -----------------------------------------------------------------------
# SafeCompounder class
# -----------------------------------------------------------------------

class SafeCompounder:
    """
    Edge-based NO-side strategy. Polymarket edition.

    Usage:
        async with build_polymarket_clients() as (client, gamma):
            compounder = SafeCompounder(client=client, gamma=gamma)
            await compounder.run(dry_run=False)

    `gamma` is required — it owns market discovery (CLOB cannot discover
    markets on Polymarket). If you instantiate `client` via
    `build_polymarket_clients()` the gamma client is the same one attached
    to it, so token-id resolution stays cached across this strategy and
    any sibling jobs in the same process.
    """

    def __init__(
        self,
        client,  # PolymarketClient instance
        gamma: Optional[GammaClient] = None,
        db_path: str = "trading_system.db",
        dry_run: bool = True,
        min_no_ask: float = MIN_NO_ASK,
        min_edge: float = MIN_EDGE,
        max_position_pct: float = MAX_POSITION_PCT,
        use_kelly: bool = USE_KELLY,
        min_confidence: float = MIN_CONFIDENCE,
    ):
        self.client = client
        self.gamma = gamma or GammaClient()
        self._owns_gamma = gamma is None  # close it ourselves if we made it
        self.db_path = db_path
        self.dry_run = dry_run
        self.min_no_ask = min_no_ask
        self.min_edge = min_edge
        self.max_position_pct = max_position_pct
        self.use_kelly = use_kelly
        self.min_confidence = min_confidence
        # Resolved at run-time on first use; depends on Gamma being reachable.
        self._skip_tag_ids: Optional[set] = None

    async def run(self, dry_run: Optional[bool] = None) -> Dict:
        """
        Full scan: fetch → filter → orderbook check → place maker orders.
        Returns stats dict.
        """
        if dry_run is not None:
            self.dry_run = dry_run

        start = time.time()

        logger.info("=" * 70)
        logger.info("SAFE COMPOUNDER v5 — EDGE-BASED NO-SIDE")
        logger.info(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info(
            "Rules: NO only | ask > $%.2f | edge > $%.2f | max %.0f%%/position | maker orders",
            self.min_no_ask, self.min_edge, self.max_position_pct * 100,
        )
        logger.info("=" * 70)

        # Get portfolio state. PolymarketClient.get_balance() reports USDC.e
        # cash; mark-to-market on open positions is not yet computed (returns 0
        # for portfolio_value). Fall back to cash balance for sizing so the
        # strategy still works on a fresh wallet.
        bal = await self.client.get_balance()
        cash = bal.get("balance", 0)
        portfolio = bal.get("portfolio_value", 0) or cash

        print(f"\n💰 Cash: ${cash/100:.2f} | Portfolio (sizing basis): ${portfolio/100:.2f} | "
              f"Total: ${(cash+portfolio)/100:.2f}\n", flush=True)

        # Step 0: Cancel legacy YES orders (no-op on a fresh Polymarket wallet
        # but kept for parity with the Polymarket version when migrating).
        print("🧹 Step 0: Cancel legacy YES orders...", flush=True)
        cancelled = await self._cancel_yes_orders()

        # Step 1: Fetch all markets
        print("\n📡 Step 1: Fetching all active markets...", flush=True)
        markets = await self._fetch_all_markets()
        print(f"  Fetched {len(markets)} markets", flush=True)

        # Step 2: Filter NO candidates
        print("\n🔍 Step 2: Finding NO-side candidates (YES ≤ $0.20)...", flush=True)
        candidates = self._find_no_candidates(markets)

        # Step 3: Orderbook + edge check
        print(f"\n📊 Step 3: Checking orderbooks for edge ≥ ${self.min_edge:.2f}...", flush=True)
        opportunities = await self._check_orderbook_and_price(candidates)

        # Display top opportunities
        sorted_opps = sorted(
            opportunities, key=lambda x: (-x["edge"], -x["annualized_roi"])
        )
        print(f"\n📋 Top Opportunities:", flush=True)
        for opp in sorted_opps[:20]:
            print(
                f"  NO ask:${opp['lowest_no_ask']:.2f} → our:${opp['our_price']:.2f} | "
                f"EV:${opp['true_no_prob']:.2f} edge:${opp['edge']:.2f} | "
                f"YES@${opp['yes_last']:.2f} | {opp['roi_pct']:.1f}% "
                f"({opp['annualized_roi']:.0f}%ann) | "
                f"{opp['days_to_expiry']}d | vol:{opp['volume']} | {opp['ticker']}",
                flush=True,
            )
            print(f"    {opp['title']}", flush=True)

        # Step 4: Place orders
        print(f"\n🚀 Step 4: Placing maker orders (ask - $0.01)...", flush=True)
        stats = await self._place_resting_orders(sorted_opps, portfolio, cash)

        elapsed = time.time() - start
        bal = await self.client.get_balance()
        # Lifecycle: close the gamma session if we created it ourselves.
        if self._owns_gamma:
            try:
                await self.gamma.close()
            except Exception:
                pass

        print(f"\n{'='*70}", flush=True)
        print(f"📊 SAFE COMPOUNDER REPORT", flush=True)
        print(f"{'='*70}", flush=True)
        print(f"  Markets scanned:      {len(markets)}", flush=True)
        print(f"  NO candidates:        {len(candidates)}", flush=True)
        print(f"  With edge > ${self.min_edge:.2f}:      {len(opportunities)}", flush=True)
        print(f"  Orders placed:        {stats['placed']}", flush=True)
        print(f"  Instantly filled:     {stats['filled']}", flush=True)
        print(f"  Skipped (existing):   {stats['skipped_existing']}", flush=True)
        print(f"  Errors:               {stats['errors']}", flush=True)
        print(f"  Capital deployed:     ${stats['total_deployed']/100:.2f}", flush=True)
        print(f"  Potential profit:     ${stats['total_potential_profit']/100:.2f}", flush=True)
        print(f"  YES orders cancelled: {cancelled}", flush=True)
        print(f"  Cash:                 ${bal.get('balance', 0)/100:.2f}", flush=True)
        print(f"  Portfolio:            ${bal.get('portfolio_value', 0)/100:.2f}", flush=True)
        print(f"  Elapsed:              {elapsed:.0f}s", flush=True)
        print(f"{'='*70}\n", flush=True)

        return stats

    async def _fetch_all_markets(self) -> List[Dict]:
        """Fetch the full active universe from Polymarket Gamma.

        Resolves SKIP_TAG_SLUGS to numeric IDs once and passes them as
        server-side `exclude_tag_ids` so we never even download the markets
        we're going to drop. Each returned market dict has been augmented by
        :func:`gamma_client._derive_market_fields` with `_condition_id`,
        `_token_ids`, `_outcome_prices`, `_volume_num`, `_end_ts`,
        `_category`, and `_event_tag_ids` — used downstream for filtering
        and order placement.
        """
        if self._skip_tag_ids is None:
            try:
                ids = await self.gamma.resolve_tag_slugs(SKIP_TAG_SLUGS)
                self._skip_tag_ids = set(ids)
                logger.info(
                    "SafeCompounder: resolved %d/%d skip-tag slugs",
                    len(self._skip_tag_ids), len(SKIP_TAG_SLUGS),
                )
            except Exception as exc:
                logger.warning("Skip-tag resolution failed (%s); will fall back to slug-string match", exc)
                self._skip_tag_ids = set()

        markets = await self.gamma.get_markets(
            active=True,
            closed=False,
            archived=False,
            accepting_orders=True,
            exclude_tag_ids=list(self._skip_tag_ids) or None,
            order="volume24hr",
            ascending=False,
            max_results=2000,
        )

        # Last-ditch local filter for tags we couldn't resolve to IDs and for
        # the title-phrase blocklist. Every kept market also gets its YES/NO
        # token_ids + routing metadata pushed onto the Polymarket client cache
        # so subsequent get_orderbook / place_order calls work without a Gamma
        # re-fetch (and without losing the neg_risk flag).
        skip_ids = self._skip_tag_ids or set()
        filtered: List[Dict] = []
        for m in markets:
            m["_skip_tag_ids"] = skip_ids
            if should_skip(m):
                continue
            title_lower = (m.get("question") or "").lower()
            if any(p in title_lower for p in SKIP_TITLE_PHRASES):
                continue

            cond = m.get("_condition_id") or m.get("conditionId") or ""
            yes_tok, no_tok = m.get("_token_ids", ("", ""))
            if cond and yes_tok and no_tok and hasattr(self.client, "register_market"):
                self.client.register_market(
                    cond,
                    yes_tok,
                    no_tok,
                    neg_risk=bool(m.get("negRisk", False)),
                    tick_size=float(m.get("orderPriceMinTickSize", 0.01) or 0.01),
                )

            filtered.append(m)

        logger.info("Fetched %d markets after server+client filter (from %d raw)",
                    len(filtered), len(markets))
        return filtered

    def _find_no_candidates(self, markets: List[Dict]) -> List[Dict]:
        """Filter markets to NO-side candidates.

        Polymarket markets carry their condition_id in `_condition_id` (or
        `conditionId` raw). Volume comes from `_volume_num`. End-time is
        already an epoch second in `_end_ts`. YES last price is the first
        element of `_outcome_prices` (always [yes, no] for binary markets).
        """
        candidates = []
        now_ts = datetime.now(timezone.utc).timestamp()

        for m in markets:
            cond = m.get("_condition_id") or m.get("conditionId") or m.get("ticker") or ""
            if not cond:
                continue
            # Tag/title skips already handled in _fetch_all_markets, but be
            # defensive here in case a caller hands us un-pre-filtered markets.
            if should_skip(m):
                continue
            title_lower = (m.get("question") or "").lower()
            if any(phrase in title_lower for phrase in SKIP_TITLE_PHRASES):
                continue

            volume = float(
                m.get("_volume_num") or m.get("volumeNum") or m.get("volume") or 0
            )
            if int(volume) < MIN_VOLUME:
                continue

            outcome_prices = m.get("_outcome_prices") or (0.0, 0.0)
            yes_last = float(outcome_prices[0]) if outcome_prices else 0.0
            if yes_last > 0.20:  # Only consider markets with YES ≤ $0.20
                continue

            end_ts = m.get("_end_ts") or 0
            hours_to_expiry = max(0.0, (end_ts - now_ts) / 3600) if end_ts else 720.0
            if hours_to_expiry <= 0:
                continue

            true_no_prob = estimate_true_no_prob(yes_last, hours_to_expiry)

            candidates.append({
                **m,
                # Stable ticker/title aliases for downstream code expecting them
                "ticker": cond,
                "title": m.get("question", ""),
                "_true_no_prob": true_no_prob,
                "_hours_to_expiry": round(hours_to_expiry, 1),
                "_days_to_expiry": round(hours_to_expiry / 24, 1),
            })

        logger.info("Found %d NO-side candidates (YES last <= $0.20)", len(candidates))
        
        # Sort by estimated edge potential: lowest YES price + highest volume + soonest expiry
        # Then cap to top 500 to keep orderbook checks under ~1 minute
        MAX_ORDERBOOK_CHECKS = 200
        if len(candidates) > MAX_ORDERBOOK_CHECKS:
            candidates.sort(key=lambda c: (
                -c["_true_no_prob"],  # Highest estimated NO probability first
                -float(c.get("volume_fp", 0) or c.get("volume", 0) or 0),  # Highest volume
                c["_hours_to_expiry"],  # Soonest expiry
            ))
            logger.info("Capping to top %d candidates (from %d) for orderbook checks",
                        MAX_ORDERBOOK_CHECKS, len(candidates))
            candidates = candidates[:MAX_ORDERBOOK_CHECKS]
        
        return candidates

    async def _check_orderbook_and_price(self, candidates: List[Dict]) -> List[Dict]:
        """Check orderbooks and find trades with sufficient edge."""
        opportunities = []

        for i, m in enumerate(candidates):
            ticker = m["ticker"]
            true_no_prob = m["_true_no_prob"]

            try:
                ob_resp = await self.client.get_orderbook(ticker, depth=10)
                # Handle both new and old orderbook formats
                ob = ob_resp.get("orderbook_fp", ob_resp.get("orderbook", {}))
                # No extra sleep — client already has 0.5s rate limiter
                if (i + 1) % 50 == 0:
                    logger.info("Orderbook progress: %d/%d checked", i + 1, len(candidates))
            except Exception as e:
                logger.debug("Orderbook fetch failed for %s: %s", ticker, e)
                continue

            conf_score, conf_reason = market_confidence_score(ticker, ob, m)
            if conf_score < self.min_confidence:
                logger.debug(
                    "Low confidence (%.2f) %s — %s", conf_score, ticker, conf_reason
                )
                continue

            # Handle both new and old orderbook formats
            yes_bids = ob.get("yes_dollars", ob.get("yes", []))
            no_bids = ob.get("no_dollars", ob.get("no", []))

            lowest_no_ask = None
            if yes_bids:
                try:
                    highest_yes_bid = max(float(b[0]) for b in yes_bids)
                    # Convert cents to dollars if needed
                    if highest_yes_bid > 1.0:
                        highest_yes_bid = highest_yes_bid / 100.0
                    lowest_no_ask = 1.0 - highest_yes_bid
                except (ValueError, TypeError):
                    pass

            best_no_bid = 0
            if no_bids:
                try:
                    best_no_bid = max(float(b[0]) for b in no_bids)
                    # Convert cents to dollars if needed
                    if best_no_bid > 1.0:
                        best_no_bid = best_no_bid / 100.0
                except (ValueError, TypeError):
                    pass

            if lowest_no_ask is None and best_no_bid > 0:
                lowest_no_ask = best_no_bid + 0.02  # 2¢ = $0.02

            if lowest_no_ask is None:
                continue

            if lowest_no_ask < self.min_no_ask:
                continue

            edge = true_no_prob - lowest_no_ask
            if edge < self.min_edge:
                continue

            our_price = lowest_no_ask - 0.01  # 1¢ = $0.01
            if our_price < self.min_no_ask:
                continue

            profit_per_contract = 1.0 - our_price
            roi_pct = profit_per_contract / our_price * 100
            days = m["_days_to_expiry"] if m["_days_to_expiry"] > 0 else 1
            annualized_roi = (profit_per_contract / our_price) * (365 / days) * 100

            yes_last_val = float(m.get("last_price_dollars", 0) or m.get("last_price", 0) or 0)
            # Convert cents to dollars if needed
            if yes_last_val > 1.0:
                yes_last_val = yes_last_val / 100.0
            
            opportunities.append({
                "ticker": ticker,
                "title": m.get("title", "")[:70],
                "side": "no",
                "yes_last": yes_last_val,
                "true_no_prob": true_no_prob,
                "lowest_no_ask": lowest_no_ask,
                "our_price": our_price,
                "edge": edge,
                "profit": profit_per_contract,
                "roi_pct": roi_pct,
                "annualized_roi": annualized_roi,
                "volume": int(float(m.get("volume_fp", 0) or m.get("volume", 0) or 0)),
                "days_to_expiry": m["_days_to_expiry"],
                "close_time": m.get("close_time", "")[:10],
                "best_no_bid": best_no_bid,
            })

            if (i + 1) % 25 == 0:
                logger.info(
                    "Checked %d/%d orderbooks, %d viable",
                    i + 1, len(candidates), len(opportunities),
                )

        logger.info(
            "%d opportunities with edge > $%.2f", len(opportunities), self.min_edge
        )
        return opportunities

    async def _place_resting_orders(
        self, opportunities: List[Dict], portfolio: int, cash: int
    ) -> Dict:
        """Place NO-side resting orders at lowest_ask - 1¢."""
        # Get existing positions and orders
        try:
            positions_resp = await self.client.get_positions()
            positions = positions_resp.get("market_positions", [])
            pos_tickers = {
                p["ticker"] for p in positions if abs(p.get("position", 0)) > 0
            }
        except Exception:
            pos_tickers = set()

        try:
            orders_resp = await self.client.get_orders(status="resting")
            existing_orders = orders_resp.get("orders", [])
            ord_tickers = {o["ticker"] for o in existing_orders}
        except Exception:
            ord_tickers = set()

        stats = {
            "placed": 0,
            "skipped_existing": 0,
            "skipped_size": 0,
            "filled": 0,
            "errors": 0,
            "total_potential_profit": 0,
            "total_deployed": 0,
        }

        print(
            f"\n{'='*70}\nPLACING MAKER ORDERS — Portfolio: ${portfolio/100:.2f} | "
            f"Cash: ${cash/100:.2f} | {'DRY RUN' if self.dry_run else 'LIVE'}\n"
            f"Max per position: ${portfolio * self.max_position_pct / 100:.2f} ({self.max_position_pct*100:.0f}%)\n"
            f"{'='*70}\n",
            flush=True,
        )

        for opp in opportunities:
            ticker = opp["ticker"]

            if ticker in pos_tickers or ticker in ord_tickers:
                stats["skipped_existing"] += 1
                continue

            contracts = self._calculate_position_size(opp, portfolio, cash)
            if contracts < 1:
                stats["skipped_size"] += 1
                continue

            price = opp["our_price"]
            cost = contracts * price * 100  # Convert dollars to cents for cost calculation
            profit = contracts * opp["profit"] * 100  # Convert dollars to cents for profit calculation

            if self.dry_run:
                kelly_info = ""
                if self.use_kelly:
                    true_prob = opp["true_no_prob"]  # Already in 0-1 format
                    odds = (1.0 - price) / price  # Dollar format
                    kf = kelly_fraction(true_prob, odds)
                    kelly_info = f" kelly:{kf:.1%}"
                print(
                    f"  🏷️ [DRY] NO x{contracts} @ ${price:.2f} | "
                    f"ask:${opp['lowest_no_ask']:.2f} EV:${opp['true_no_prob']:.2f} "
                    f"edge:${opp['edge']:.2f} | "
                    f"+${profit/100:.2f} ({opp['roi_pct']:.1f}% / {opp['annualized_roi']:.0f}%ann) | "
                    f"{opp['days_to_expiry']}d{kelly_info}",
                    flush=True,
                )
                print(f"    {opp['ticker']} — {opp['title']}", flush=True)
                stats["placed"] += 1
                stats["total_potential_profit"] += profit
                stats["total_deployed"] += cost
                continue

            try:
                # Convert dollar price to cents for API call
                price_cents = int(price * 100)
                client_order_id = str(uuid.uuid4())
                r = await self.client.place_order(
                    ticker=ticker,
                    client_order_id=client_order_id,
                    side="no",
                    action="buy",
                    count=contracts,
                    no_price=price_cents,
                )
                order = r.get("order", {})
                status = order.get("status", "?")
                filled = order.get("fill_count", 0)

                if filled > 0:
                    stats["filled"] += filled
                    print(
                        f"  🎯 FILLED NO x{filled}/{contracts} @ ${price:.2f} | "
                        f"edge:${opp['edge']:.2f} +${filled * opp['profit']/100:.2f} | {ticker}",
                        flush=True,
                    )
                else:
                    print(
                        f"  ✅ NO x{contracts} @ ${price:.2f} | {status} | "
                        f"edge:${opp['edge']:.2f} {opp['roi_pct']:.1f}% | {ticker}",
                        flush=True,
                    )

                stats["placed"] += 1
                stats["total_potential_profit"] += profit
                stats["total_deployed"] += cost
                ord_tickers.add(ticker)
                await asyncio.sleep(0.2)

            except Exception as e:
                print(f"  ❌ {ticker}: {e}", flush=True)
                stats["errors"] += 1
                await asyncio.sleep(0.3)

        return stats

    def _calculate_position_size(self, opp: Dict, portfolio: int, cash: int) -> int:
        """Size each position using Kelly or fixed fraction."""
        max_position_value = int(portfolio * self.max_position_pct)
        price = opp["our_price"]  # Already in dollar format

        if self.use_kelly:
            true_prob = opp["true_no_prob"]  # Already in 0-1 format
            odds = (1.0 - price) / price  # Dollar format
            kf = kelly_fraction(true_prob, odds)
            half_kelly_f = kf * 0.5
            kelly_position = int(portfolio * half_kelly_f)
            position_value = min(kelly_position, max_position_value)
        else:
            position_value = max_position_value

        # Convert price to cents for position calculation
        price_cents = int(price * 100)
        contracts = max(1, position_value // price_cents)
        contracts = min(contracts, 200)
        return contracts

    async def _cancel_yes_orders(self) -> int:
        """Cancel any resting YES-side orders (legacy)."""
        try:
            orders_resp = await self.client.get_orders(status="resting")
            orders = orders_resp.get("orders", [])
            yes_orders = [o for o in orders if o.get("side") == "yes"]
            cancelled = 0
            for o in yes_orders:
                try:
                    await self.client.cancel_order(o["order_id"])
                    yes_price = o.get('yes_price', 0)
                    if isinstance(yes_price, (int, float)) and yes_price > 0:
                        # Convert cents to dollars if needed for display
                        if yes_price > 1.0:
                            price_display = f"${yes_price/100:.2f}"
                        else:
                            price_display = f"${yes_price:.2f}"
                    else:
                        price_display = "?"
                    print(
                        f"  🗑️ Cancelled YES: {o['ticker']} @ {price_display}",
                        flush=True,
                    )
                    cancelled += 1
                    await asyncio.sleep(0.15)
                except Exception as e:
                    logger.warning("Cancel failed %s: %s", o["ticker"], e)
            if not yes_orders:
                print("  No legacy YES orders.", flush=True)
            return cancelled
        except Exception as e:
            logger.error("Error cancelling YES orders: %s", e)
            return 0

    async def check_fills(self) -> None:
        """Check recent fills and resting orders."""
        bal = await self.client.get_balance()
        portfolio = bal.get("portfolio_value", 0)
        cash = bal.get("balance", 0)
        print(
            f"💰 Cash: ${cash/100:.2f} | Portfolio: ${portfolio/100:.2f} | "
            f"Total: ${(cash+portfolio)/100:.2f}",
            flush=True,
        )

        try:
            orders_resp = await self.client.get_orders(status="resting")
            resting = orders_resp.get("orders", [])
            no_resting = [o for o in resting if o.get("side") == "no"]
            yes_resting = [o for o in resting if o.get("side") == "yes"]
            print(
                f"📋 Resting: {len(no_resting)} NO, {len(yes_resting)} YES",
                flush=True,
            )
        except Exception:
            pass

        try:
            fills_resp = await self.client.get_fills(limit=20)
            fill_list = fills_resp.get("fills", [])
            print(f"\n📊 Last 20 fills:", flush=True)
            for f in fill_list:
                ticker = f.get("ticker", "")
                side = f.get("side", "")
                count = f.get("count", 0)
                price = f.get("yes_price", f.get("no_price", 0))
                created = f.get("created_time", "")[:16]
                # Convert cents to dollars if needed for display
                if isinstance(price, (int, float)) and price > 1.0:
                    price_display = f"${price/100:.2f}"
                else:
                    price_display = f"${price:.2f}" if isinstance(price, (int, float)) else f"{price}¢"
                print(f"  {created} | {side} x{count} @ {price_display} | {ticker}", flush=True)
        except Exception:
            pass
