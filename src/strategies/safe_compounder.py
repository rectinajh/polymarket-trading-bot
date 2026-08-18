"""
Safe Compounder Strategy — NO-side, edge-based, capital-efficient.

STRATEGY:
- NO side ONLY
- Find near-certain outcomes (YES last ≤ $0.20)
- Edge = (1 - YES last) - real NO ask  (no time-to-expiry heuristic boost)
- Real NO ask must be ≥ MIN_NO_ASK ($0.80)
- Trade only when edge ≥ MIN_EDGE — FOK take the cheap ask (capture the
  mispricing we measured). Do not rest 1¢ below the ask.
- Position size: min(25% of top-2 NO asks, NAV-tier cap, half-Kelly)
- At most 6 new entries per calendar day (Asia/Shanghai); correlated titles share one slot
- No daily PnL target — empty scans are expected

Available via: python cli.py run --safe-compounder
"""

import asyncio
import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from src.clients.gamma_client import GammaClient
from src.strategies.capital_policy import (
    DEPTH_TAKE_PCT,
    MAX_ENTRIES_PER_DAY,
    DailyEntryLog,
    clusters_from_positions,
    correlation_key,
    nav_max_position_pct,
    size_shares,
    top_ask_depth,
)
from src.utils.market_quality import (
    FORCE_EXIT_HOURS_BEFORE_EXPIRY,
    should_skip_market_title,
)

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

# Thresholds (dollar 0.00-1.00). MIN_EDGE is last-vs-ask gap, not a
# time-heuristic boost — that boost maxed at 4¢ while the old 5¢ gate
# made live scans return zero opportunities on efficient books.
MIN_VOLUME = 5000
MIN_NO_ASK = 0.80
MIN_EDGE = 0.02
# Hard ceiling; live cap is min(this, nav_max_position_pct(NAV)).
MAX_POSITION_PCT = 0.05
USE_KELLY = True
MIN_CONFIDENCE = 0.5
MIN_ASK_SIZE = 5.0
# Do not open a new position that inventory management would immediately exit.
MIN_HOURS_TO_ENTRY = FORCE_EXIT_HOURS_BEFORE_EXPIRY + 1.0
BOOK_BATCH_SIZE = 8


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


def estimate_true_no_prob(yes_last: float, hours_to_expiry: float = 0.0) -> float:
    """Implied NO probability from YES last. No time-to-expiry boost.

    ``hours_to_expiry`` is kept for call-site compatibility; it is not used.
    Adding 1–4¢ of uncalibrated certainty made MIN_EDGE look earned when the
    book was already efficient.
    """
    del hours_to_expiry
    return max(0.0, min(1.0, 1.0 - yes_last))


def nav_cents(bal: Dict) -> Tuple[int, int, int]:
    """Return (cash_cents, mtm_cents, nav_cents). Never double-count cash."""
    cash = int(bal.get("balance", 0) or 0)
    mtm = int(bal.get("portfolio_value", 0) or 0)
    return cash, mtm, cash + mtm


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
            qty = int(float(qty_data))
            # Convert cents to dollars if needed
            if price > 1.0:
                price = price / 100.0
            all_levels.append((1.0 - price, qty))  # Convert YES to NO price in dollars
        except (ValueError, TypeError):
            continue
    
    for price_data, qty_data in no_side:
        try:
            price = float(price_data)
            qty = int(float(qty_data))
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

    volume = float(
        market.get("volume_fp", 0)
        or market.get("volume", 0)
        or market.get("_volume_num", 0)
        or 0
    )
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
        entry_log: Optional[DailyEntryLog] = None,
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
        self._entries = entry_log or DailyEntryLog()

    async def run(self, dry_run: Optional[bool] = None) -> Dict:
        """
        Full scan: fetch → filter → orderbook check → place maker orders.
        Returns stats dict.
        """
        if dry_run is not None:
            self.dry_run = dry_run

        start = time.time()

        logger.info("=" * 70)
        logger.info("SAFE COMPOUNDER v7 — DEPTH-CAPPED NO-SIDE (FOK)")
        logger.info(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info(
            "Rules: NO only | real ask ≥ $%.2f | last-vs-ask edge ≥ $%.2f | "
            "depth %.0f%% of top-2 asks | ≤%d entries/day | no daily PnL target | FOK take ask",
            self.min_no_ask, self.min_edge, DEPTH_TAKE_PCT * 100, MAX_ENTRIES_PER_DAY,
        )
        logger.info("=" * 70)

        bal = await self.client.get_balance()
        cash, mtm, nav = nav_cents(bal)

        cap_pct = min(self.max_position_pct, nav_max_position_pct(nav))
        print(
            f"\n💰 Cash: ${cash/100:.2f} | MTM: ${mtm/100:.2f} | "
            f"NAV (sizing): ${nav/100:.2f} | "
            f"cap {cap_pct*100:.1f}%/pos | remaining today {self._entries.remaining()}/{MAX_ENTRIES_PER_DAY}\n",
            flush=True,
        )

        print("🧹 Step 0: Inventory + cancel legacy YES orders...", flush=True)
        inv_stats = await self.manage_inventory()
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
        opportunities, rejects = await self._check_orderbook_and_price(candidates)

        # Display top opportunities
        sorted_opps = sorted(
            opportunities, key=lambda x: (-x["edge"], -x["annualized_roi"])
        )
        print(f"\n📋 Top Opportunities:", flush=True)
        for opp in sorted_opps[:20]:
            print(
                f"  NO ask:${opp['lowest_no_ask']:.2f} FOK | "
                f"EV:${opp['true_no_prob']:.2f} edge:${opp['edge']:.2f} | "
                f"YES@${opp['yes_last']:.2f} | {opp['roi_pct']:.1f}% "
                f"({opp['annualized_roi']:.0f}%ann) | "
                f"{opp['days_to_expiry']}d | vol:{opp['volume']} | {opp['ticker']}",
                flush=True,
            )
            print(f"    {opp['title']}", flush=True)

        reject_txt = " ".join(f"{k}={v}" for k, v in sorted(rejects.items()) if v)
        if reject_txt:
            print(f"  Rejects: {reject_txt}", flush=True)
            logger.info("Orderbook rejects: %s", reject_txt)

        # Step 4: Take cheap asks
        print(f"\n🚀 Step 4: FOK taking cheap NO asks...", flush=True)
        stats = await self._place_resting_orders(sorted_opps, nav, cash)

        elapsed = time.time() - start
        if hasattr(self.client, "flush_token_cache"):
            self.client.flush_token_cache()
        bal = await self.client.get_balance()
        cash_end, mtm_end, nav_end = nav_cents(bal)
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
        print(f"  With edge ≥ ${self.min_edge:.2f}:     {len(opportunities)}", flush=True)
        print(f"  Orders placed:        {stats['placed']}", flush=True)
        print(f"  Instantly filled:     {stats['filled']}", flush=True)
        print(f"  Skipped (existing):   {stats['skipped_existing']}", flush=True)
        print(f"  Skipped (cluster):    {stats.get('skipped_cluster', 0)}", flush=True)
        print(f"  Skipped (daily cap):  {stats.get('skipped_daily_cap', 0)}", flush=True)
        print(f"  Errors:               {stats['errors']}", flush=True)
        print(f"  Capital deployed:     ${stats['total_deployed']/100:.2f}", flush=True)
        print(f"  Potential profit:     ${stats['total_potential_profit']/100:.2f}", flush=True)
        print(f"  Inventory exits:      {inv_stats.get('exited', 0)}", flush=True)
        print(f"  Redeemed:             {inv_stats.get('redeemed', 0)}", flush=True)
        print(f"  Redeem needed:        {inv_stats.get('redeem_needed', 0)}", flush=True)
        print(f"  YES orders cancelled: {cancelled}", flush=True)
        print(f"  Cash:                 ${cash_end/100:.2f}", flush=True)
        print(f"  MTM:                  ${mtm_end/100:.2f}", flush=True)
        print(f"  NAV:                  ${nav_end/100:.2f}", flush=True)
        print(f"  Elapsed:              {elapsed:.0f}s", flush=True)
        print(f"{'='*70}\n", flush=True)

        stats["rejects"] = dict(rejects)
        stats["nav_cents"] = nav_end
        stats["inventory_exited"] = inv_stats.get("exited", 0)
        stats["redeemed"] = inv_stats.get("redeemed", 0)
        stats["redeem_needed"] = inv_stats.get("redeem_needed", 0)
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
            skip_mq, _ = should_skip_market_title(m.get("question") or "")
            if skip_mq:
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
        if hasattr(self.client, "flush_token_cache"):
            self.client.flush_token_cache()
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
            if hours_to_expiry <= MIN_HOURS_TO_ENTRY:
                continue

            true_no_prob = estimate_true_no_prob(yes_last, hours_to_expiry)

            candidates.append({
                **m,
                # Stable ticker/title aliases for downstream code expecting them
                "ticker": cond,
                "title": m.get("question", ""),
                "volume": volume,
                "volume_fp": volume,
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

    async def _check_orderbook_and_price(
        self, candidates: List[Dict]
    ) -> Tuple[List[Dict], Counter]:
        """Check real NO asks; only trade last-vs-ask edge on live liquidity."""
        opportunities: List[Dict] = []
        rejects: Counter = Counter()

        async def _one(m: Dict) -> Tuple[Optional[Dict], str]:
            ticker = m["ticker"]
            true_no_prob = m["_true_no_prob"]
            try:
                ob_resp = await self.client.get_orderbook(ticker, depth=10)
                ob = ob_resp.get("orderbook_fp", ob_resp.get("orderbook", {})) or {}
            except Exception as e:
                logger.info("Orderbook fetch failed for %s: %s", ticker[:18], e)
                return None, "disconnect"

            if not ob:
                return None, "empty_book"

            conf_score, _conf_reason = market_confidence_score(ticker, ob, m)
            if conf_score < self.min_confidence:
                return None, "low_conf"

            no_asks = ob.get("no_asks", []) or []
            lowest_no_ask, ask_depth = top_ask_depth(no_asks)
            no_ask_size = 0.0
            if lowest_no_ask is not None:
                try:
                    priced = []
                    for row in no_asks:
                        p = float(row[0])
                        sz = float(row[1])
                        if p > 1.0:
                            p = p / 100.0
                        if p > 0 and sz > 0:
                            priced.append((p, sz))
                    if priced:
                        no_ask_size = min(priced, key=lambda x: x[0])[1]
                except (ValueError, TypeError, IndexError):
                    no_ask_size = ask_depth

            # Only real NO asks count. Derived (1 - YES bid) is not a takeable price.
            if lowest_no_ask is None:
                return None, "no_real_ask"
            if no_ask_size < MIN_ASK_SIZE:
                return None, "thin_ask"
            if lowest_no_ask < self.min_no_ask:
                return None, "ask_below_min"

            edge = true_no_prob - lowest_no_ask
            if edge < self.min_edge:
                return None, "edge_lt_min"

            our_price = lowest_no_ask
            profit_per_contract = 1.0 - our_price
            roi_pct = profit_per_contract / our_price * 100 if our_price else 0.0
            days = m["_days_to_expiry"] if m["_days_to_expiry"] > 0 else 1
            annualized_roi = (profit_per_contract / our_price) * (365 / days) * 100

            outcome_prices = m.get("_outcome_prices") or (0.0, 0.0)
            yes_last_val = float(outcome_prices[0]) if outcome_prices else 0.0

            best_no_bid = 0.0
            no_bids = ob.get("no_dollars", ob.get("no", [])) or []
            if no_bids:
                try:
                    best_no_bid = max(float(b[0]) for b in no_bids)
                    if best_no_bid > 1.0:
                        best_no_bid = best_no_bid / 100.0
                except (ValueError, TypeError):
                    best_no_bid = 0.0

            opp = {
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
                "close_time": (m.get("close_time") or "")[:10],
                "best_no_bid": best_no_bid,
                "capture": "taker",
                "ask_depth": ask_depth,
                "ask_size": no_ask_size,
            }
            return opp, "ok"

        for start in range(0, len(candidates), BOOK_BATCH_SIZE):
            batch = candidates[start:start + BOOK_BATCH_SIZE]
            results = await asyncio.gather(*[_one(m) for m in batch], return_exceptions=True)
            for i, result in enumerate(results):
                done = start + i + 1
                if isinstance(result, Exception):
                    rejects["disconnect"] += 1
                    logger.info("Orderbook batch error: %s", result)
                    continue
                opp, reason = result
                if opp:
                    opportunities.append(opp)
                else:
                    rejects[reason] += 1
                if done % 50 == 0:
                    logger.info(
                        "Orderbook progress: %d/%d checked, %d viable",
                        done, len(candidates), len(opportunities),
                    )

        logger.info(
            "%d opportunities with last-vs-ask edge ≥ $%.2f",
            len(opportunities), self.min_edge,
        )
        return opportunities, rejects

    async def _place_resting_orders(
        self, opportunities: List[Dict], portfolio: int, cash: int
    ) -> Dict:
        """FOK-take cheap NO asks. `portfolio` is NAV in cents."""
        try:
            positions_resp = await self.client.get_positions()
            positions = positions_resp.get("market_positions", []) or []
            pos_tickers = {
                (p.get("ticker") or p.get("condition_id"))
                for p in positions
                if abs(float(p.get("size", 0) or 0)) > 0
            }
        except Exception:
            positions = []
            pos_tickers = set()

        try:
            orders_resp = await self.client.get_orders(status="resting")
            existing_orders = orders_resp.get("orders", [])
            ord_tickers = {o.get("ticker") for o in existing_orders if o.get("ticker")}
        except Exception:
            ord_tickers = set()

        stats = {
            "placed": 0,
            "skipped_existing": 0,
            "skipped_size": 0,
            "skipped_cluster": 0,
            "skipped_daily_cap": 0,
            "filled": 0,
            "errors": 0,
            "total_potential_profit": 0,
            "total_deployed": 0,
        }

        cap_pct = min(self.max_position_pct, nav_max_position_pct(portfolio))
        remaining_today = self._entries.remaining()
        print(
            f"\n{'='*70}\nFOK TAKE NO ASKS — NAV: ${portfolio/100:.2f} | "
            f"Cash: ${cash/100:.2f} | {'DRY RUN' if self.dry_run else 'LIVE'}\n"
            f"Max per position: ${portfolio * cap_pct / 100:.2f} "
            f"({cap_pct*100:.1f}% NAV, {DEPTH_TAKE_PCT*100:.0f}% book depth) | "
            f"today {remaining_today}/{MAX_ENTRIES_PER_DAY}\n"
            f"{'='*70}\n",
            flush=True,
        )

        pos_clusters = clusters_from_positions(positions) | self._entries.clusters()

        remaining_cash = cash
        for opp in opportunities:
            if stats["placed"] >= remaining_today:
                stats["skipped_daily_cap"] += 1
                continue

            ticker = opp["ticker"]
            title = opp.get("title") or ""
            cluster = correlation_key(title)

            if ticker in pos_tickers or ticker in ord_tickers:
                stats["skipped_existing"] += 1
                continue
            if cluster and cluster in pos_clusters:
                stats["skipped_cluster"] += 1
                continue

            contracts = self._calculate_position_size(opp, portfolio, remaining_cash)
            if contracts < 1:
                stats["skipped_size"] += 1
                continue

            price = opp["our_price"]
            cost = contracts * price * 100
            profit = contracts * opp["profit"] * 100
            if cost > remaining_cash:
                stats["skipped_size"] += 1
                continue

            if self.dry_run:
                kelly_info = ""
                if self.use_kelly:
                    true_prob = opp["true_no_prob"]
                    odds = (1.0 - price) / price if price else 0.0
                    kf = kelly_fraction(true_prob, odds)
                    kelly_info = f" kelly:{kf:.1%}"
                print(
                    f"  [DRY] FOK NO x{contracts} @ ${price:.2f} | "
                    f"EV:${opp['true_no_prob']:.2f} edge:${opp['edge']:.2f} | "
                    f"+${profit/100:.2f} ({opp['roi_pct']:.1f}% / {opp['annualized_roi']:.0f}%ann) | "
                    f"{opp['days_to_expiry']}d{kelly_info} | depth:{opp.get('ask_depth', 0):.0f}",
                    flush=True,
                )
                print(f"    {opp['ticker']} — {opp['title']}", flush=True)
                stats["placed"] += 1
                stats["total_potential_profit"] += profit
                stats["total_deployed"] += cost
                remaining_cash -= int(cost)
                pos_tickers.add(ticker)
                pos_clusters.add(cluster)
                self._entries.record(ticker, title, kind="compounder")
                continue

            try:
                price_cents = int(round(price * 100))
                client_order_id = str(uuid.uuid4())
                r = await self.client.place_order(
                    ticker=ticker,
                    client_order_id=client_order_id,
                    side="no",
                    action="buy",
                    count=contracts,
                    type_="market",
                    no_price=price_cents,
                )
                order = r.get("order", {})
                status = order.get("status", "?")
                filled = order.get("fill_count", 0)

                if filled > 0:
                    stats["filled"] += filled
                    print(
                        f"  FILLED NO x{filled}/{contracts} @ ${price:.2f} | "
                        f"edge:${opp['edge']:.2f} +${filled * opp['profit']/100:.2f} | {ticker}",
                        flush=True,
                    )
                else:
                    print(
                        f"  NO x{contracts} @ ${price:.2f} | {status} | "
                        f"edge:${opp['edge']:.2f} {opp['roi_pct']:.1f}% | {ticker}",
                        flush=True,
                    )

                stats["placed"] += 1
                stats["total_potential_profit"] += profit
                stats["total_deployed"] += cost
                remaining_cash -= int(cost)
                pos_tickers.add(ticker)
                pos_clusters.add(cluster)
                self._entries.record(ticker, title, kind="compounder")
                await asyncio.sleep(0.2)

            except Exception as e:
                print(f"  {ticker}: {e}", flush=True)
                stats["errors"] += 1
                await asyncio.sleep(0.3)

        return stats

    def _calculate_position_size(self, opp: Dict, portfolio: int, cash: int) -> int:
        """Depth-first size, then NAV-tier / half-Kelly / cash caps."""
        price = opp["our_price"]
        kf = 0.0
        if self.use_kelly:
            true_prob = opp["true_no_prob"]
            odds = (1.0 - price) / price if price else 0.0
            kf = kelly_fraction(true_prob, odds) * 0.5
        return size_shares(
            price=price,
            nav_cents=portfolio,
            cash_cents=cash,
            ask_depth=float(opp.get("ask_depth") or 0),
            kelly_fraction_value=kf,
            extra_cap_pct=self.max_position_pct,
        )

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

    async def manage_inventory(self) -> Dict:
        """Exit near-expiry inventory; redeem resolved tokens instead of selling a dead book."""
        stats = {
            "checked": 0, "exited": 0, "errors": 0, "no_book": 0,
            "redeemed": 0, "redeem_needed": 0,
        }
        try:
            positions_resp = await self.client.get_positions()
            positions = positions_resp.get("market_positions", []) or []
        except Exception as exc:
            logger.warning("Inventory: get_positions failed: %s", exc)
            print("  Inventory: could not load positions.", flush=True)
            return stats

        now_ts = datetime.now(timezone.utc).timestamp()
        open_pos = [p for p in positions if abs(float(p.get("size", 0) or 0)) > 0]
        if not open_pos:
            print("  No open positions to manage.", flush=True)
            return stats

        for pos in open_pos:
            stats["checked"] += 1
            cond = pos.get("condition_id") or pos.get("ticker") or ""
            side = (pos.get("side") or "").lower()
            size = float(pos.get("size", 0) or 0)
            qty = int(round(size))
            if not cond or qty < 1:
                continue

            if pos.get("redeemable"):
                print(
                    f"  Redeemable {qty} {side.upper() or '?'} {cond[:18]}…",
                    flush=True,
                )
                if self.dry_run:
                    stats["redeemed"] += 1
                    continue
                if hasattr(self.client, "redeem_condition"):
                    try:
                        await self.client.redeem_condition(
                            cond, neg_risk=bool(pos.get("negative_risk", False)),
                        )
                        stats["redeemed"] += 1
                        continue
                    except Exception as exc:
                        logger.warning("Redeem failed %s: %s", cond[:18], exc)
                        stats["redeem_needed"] += 1
                        stats["errors"] += 1
                        continue
                stats["redeem_needed"] += 1
                continue

            if side not in ("yes", "no"):
                continue

            hours_left = await self._hours_to_expiry(pos, cond, now_ts)
            if hours_left is None:
                logger.info("Inventory: no expiry for %s — leave in place", cond[:18])
                continue
            if hours_left > FORCE_EXIT_HOURS_BEFORE_EXPIRY:
                continue

            print(
                f"  Near expiry ({hours_left:.1f}h): selling {qty} {side.upper()} {cond[:18]}…",
                flush=True,
            )
            if self.dry_run:
                stats["exited"] += 1
                continue
            try:
                book_resp = await self.client.get_orderbook(cond, depth=1)
                book = book_resp.get("orderbook", {}) or {}
                bids = book.get(side, []) or []
                if not bids:
                    stats["no_book"] += 1
                    logger.warning(
                        "Inventory: no %s bid for %s — not selling a dead book",
                        side, cond[:18],
                    )
                    continue
                best_bid = max(float(level[0]) for level in bids)
                if best_bid > 1.0:
                    best_bid = best_bid / 100.0
                price_cents = int(round(best_bid * 100))
                await self.client.place_order(
                    ticker=cond,
                    client_order_id=str(uuid.uuid4()),
                    side=side,
                    action="sell",
                    count=qty,
                    type_="market",
                    yes_price=price_cents if side == "yes" else None,
                    no_price=price_cents if side == "no" else None,
                )
                stats["exited"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("Inventory exit failed %s: %s", cond[:18], exc)
        return stats

    async def _hours_to_expiry(self, pos: Dict, cond: str, now_ts: float) -> Optional[float]:
        for key in ("endDate", "end_date", "expiration", "endDateIso"):
            raw = pos.get(key)
            if not raw:
                continue
            try:
                if isinstance(raw, (int, float)) and raw > 1e9:
                    return max(0.0, (float(raw) - now_ts) / 3600)
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
                return max(0.0, (ts - now_ts) / 3600)
            except (TypeError, ValueError, OSError):
                continue
        try:
            meta = await self.gamma.get_market(cond)
            end_ts = float(meta.get("_end_ts") or 0)
            if end_ts:
                return max(0.0, (end_ts - now_ts) / 3600)
        except Exception:
            pass
        return None

    async def check_fills(self) -> None:
        """Check recent fills and resting orders."""
        bal = await self.client.get_balance()
        cash, mtm, nav = nav_cents(bal)
        print(
            f"💰 Cash: ${cash/100:.2f} | MTM: ${mtm/100:.2f} | NAV: ${nav/100:.2f}",
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
