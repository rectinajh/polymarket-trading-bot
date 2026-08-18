"""
Completeness arbitrage — buy YES + NO when combined asks < $1.

Locks settlement value of $1 per share pair. Only trades when both asks
exist with size, profit after a fee buffer is positive, and both FOK buys
can be attempted in the same cycle. If the second leg fails, immediately
tries to unwind the first leg.

This is opportunistic and often finds zero trades; that is expected.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.clients.gamma_client import GammaClient
from src.strategies.safe_compounder import nav_cents
from src.utils.market_quality import should_skip_market_title

logger = logging.getLogger(__name__)

# Combined ask must be strictly below this (leaves buffer for fees/slippage).
MAX_COMBINED_ASK = 0.98
MIN_PROFIT_PER_SHARE = 0.02  # 1 - combined >= this
MIN_VOLUME = 5000.0
MAX_SHARES_PER_TRADE = 20
MAX_NOTIONAL_PCT = 0.02  # 2% of NAV
MAX_MARKETS_TO_CHECK = 150
MIN_ASK_SIZE = 5.0
BOOK_BATCH_SIZE = 8


def _best_ask(levels: List) -> Tuple[Optional[float], float]:
    """Return (best_ask_price, size) from [[price, size], ...] asks."""
    best_p: Optional[float] = None
    best_sz = 0.0
    for row in levels or []:
        try:
            p = float(row[0])
            sz = float(row[1])
            if p > 1.0:
                p = p / 100.0
            if p <= 0 or sz <= 0:
                continue
            if best_p is None or p < best_p:
                best_p = p
                best_sz = sz
        except (TypeError, ValueError, IndexError):
            continue
    return best_p, best_sz


def evaluate_completeness(
    yes_ask: float,
    no_ask: float,
    yes_size: float,
    no_size: float,
    *,
    max_combined: float = MAX_COMBINED_ASK,
    min_profit: float = MIN_PROFIT_PER_SHARE,
    min_size: float = MIN_ASK_SIZE,
) -> Tuple[bool, str, float]:
    """Pure check. Returns (ok, reason, profit_per_share)."""
    if yes_ask is None or no_ask is None:
        return False, "missing_ask", 0.0
    if yes_size < min_size or no_size < min_size:
        return False, "thin_size", 0.0
    combined = yes_ask + no_ask
    profit = 1.0 - combined
    if combined >= max_combined:
        return False, f"combined={combined:.3f}>={max_combined}", profit
    if profit < min_profit:
        return False, f"profit={profit:.3f}<{min_profit}", profit
    return True, "ok", profit


class CompletenessArb:
    """Scan Gamma markets and attempt two-leg FOK completeness buys."""

    def __init__(
        self,
        client,
        gamma: Optional[GammaClient] = None,
        dry_run: bool = True,
        max_combined: float = MAX_COMBINED_ASK,
        min_profit: float = MIN_PROFIT_PER_SHARE,
        min_volume: float = MIN_VOLUME,
    ):
        self.client = client
        self.gamma = gamma or GammaClient()
        self._owns_gamma = gamma is None
        self.dry_run = dry_run
        self.max_combined = max_combined
        self.min_profit = min_profit
        self.min_volume = min_volume

    async def run(self, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        if dry_run is not None:
            self.dry_run = dry_run

        start = time.time()
        stats = {
            "scanned": 0,
            "checked_books": 0,
            "opportunities": 0,
            "attempted": 0,
            "filled_pairs": 0,
            "unwound": 0,
            "errors": 0,
            "deployed_cents": 0,
            "expected_profit_cents": 0,
        }

        print("\n📐 COMPLETENESS ARB — YES+NO asks < $1", flush=True)
        print(
            f"   Rules: combined < ${self.max_combined:.2f} | "
            f"min profit ${self.min_profit:.2f}/share | vol ≥ {self.min_volume:.0f}",
            flush=True,
        )

        bal = await self.client.get_balance()
        cash, mtm, nav = nav_cents(bal)
        max_notional_cents = max(100, int(nav * MAX_NOTIONAL_PCT))

        markets = await self.gamma.get_markets(
            active=True,
            closed=False,
            archived=False,
            accepting_orders=True,
            order="volume24hr",
            ascending=False,
            max_results=800,
        )
        stats["scanned"] = len(markets)

        candidates: List[Dict] = []
        for m in markets:
            title = m.get("question") or ""
            skip, reason = should_skip_market_title(title)
            if skip:
                continue
            vol = float(m.get("_volume_num") or m.get("volume") or 0)
            if vol < self.min_volume:
                continue
            cond = m.get("_condition_id") or m.get("conditionId") or ""
            yes_tok, no_tok = m.get("_token_ids", ("", ""))
            if not cond or not yes_tok or not no_tok:
                continue
            if hasattr(self.client, "register_market"):
                self.client.register_market(
                    cond,
                    yes_tok,
                    no_tok,
                    neg_risk=bool(m.get("negRisk", False)),
                    tick_size=float(m.get("orderPriceMinTickSize", 0.01) or 0.01),
                )
            candidates.append({
                "ticker": cond,
                "title": title[:80],
                "volume": vol,
            })

        candidates = candidates[:MAX_MARKETS_TO_CHECK]
        print(f"   Checking books on {len(candidates)} liquid markets...", flush=True)

        opps: List[Dict] = []
        rejects: Counter = Counter()

        async def _one(m: Dict) -> Tuple[Optional[Dict], str]:
            try:
                ob_resp = await self.client.get_orderbook(m["ticker"], depth=5)
                ob = ob_resp.get("orderbook", {}) or {}
            except Exception as exc:
                logger.info("book fail %s: %s", m["ticker"][:18], exc)
                return None, "disconnect"

            yes_ask, yes_sz = _best_ask(ob.get("yes_asks") or [])
            no_ask, no_sz = _best_ask(ob.get("no_asks") or [])
            ok, reason, profit = evaluate_completeness(
                yes_ask or 0.0,
                no_ask or 0.0,
                yes_sz,
                no_sz,
                max_combined=self.max_combined,
                min_profit=self.min_profit,
            )
            if not ok or yes_ask is None or no_ask is None:
                return None, reason.split("=")[0] if reason else "fail"

            shares = int(min(yes_sz, no_sz, MAX_SHARES_PER_TRADE))
            cost_cents = int(round((yes_ask + no_ask) * shares * 100))
            if cost_cents > max_notional_cents or cost_cents > cash:
                shares = max(
                    0,
                    min(
                        shares,
                        int(max_notional_cents / max(1, int(round((yes_ask + no_ask) * 100)))),
                        int(cash / max(1, int(round((yes_ask + no_ask) * 100)))),
                    ),
                )
            if shares < 1:
                return None, "size"

            return {
                **m,
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "yes_size": yes_sz,
                "no_size": no_sz,
                "shares": shares,
                "profit_per": profit,
                "combined": yes_ask + no_ask,
            }, "ok"

        for start in range(0, len(candidates), BOOK_BATCH_SIZE):
            batch = candidates[start:start + BOOK_BATCH_SIZE]
            results = await asyncio.gather(*[_one(m) for m in batch], return_exceptions=True)
            for i, result in enumerate(results):
                done = start + i + 1
                if isinstance(result, Exception):
                    rejects["disconnect"] += 1
                    continue
                opp, reason = result
                if reason != "disconnect":
                    stats["checked_books"] += 1
                if opp:
                    opps.append(opp)
                else:
                    rejects[reason] += 1
                if done % 40 == 0:
                    logger.info("Completeness book progress %d/%d", done, len(candidates))

        stats["opportunities"] = len(opps)
        opps.sort(key=lambda x: -x["profit_per"])

        for opp in opps[:10]:
            print(
                f"  ARB {opp['combined']:.3f} (+${opp['profit_per']:.3f}/sh) "
                f"x{opp['shares']} | Y@{opp['yes_ask']:.2f} N@{opp['no_ask']:.2f} | "
                f"{opp['ticker'][:18]}… {opp['title'][:50]}",
                flush=True,
            )

        for opp in opps:
            try:
                result = await self._execute_pair(opp)
                stats["attempted"] += 1
                if result == "filled":
                    stats["filled_pairs"] += 1
                    cost = int(round(opp["combined"] * opp["shares"] * 100))
                    stats["deployed_cents"] += cost
                    stats["expected_profit_cents"] += int(
                        round(opp["profit_per"] * opp["shares"] * 100)
                    )
                    cash -= cost
                elif result == "unwound":
                    stats["unwound"] += 1
                # Only one live attempt per cycle to limit leg risk
                if not self.dry_run and result in ("filled", "unwound", "partial_fail"):
                    break
            except Exception as exc:
                logger.error("Completeness execute failed: %s", exc)
                stats["errors"] += 1

        if self._owns_gamma:
            try:
                await self.gamma.close()
            except Exception:
                pass

        elapsed = time.time() - start
        if hasattr(self.client, "flush_token_cache"):
            self.client.flush_token_cache()
        reject_txt = " ".join(f"{k}={v}" for k, v in sorted(rejects.items()) if v)
        print(
            f"   Completeness done: opps={stats['opportunities']} "
            f"filled_pairs={stats['filled_pairs']} unwound={stats['unwound']} "
            f"errors={stats['errors']} ({elapsed:.0f}s)",
            flush=True,
        )
        if reject_txt:
            print(f"   Rejects: {reject_txt}", flush=True)
            logger.info("Completeness rejects: %s", reject_txt)
        return stats

    async def _execute_pair(self, opp: Dict) -> str:
        ticker = opp["ticker"]
        shares = int(opp["shares"])
        yes_cents = int(round(opp["yes_ask"] * 100))
        no_cents = int(round(opp["no_ask"] * 100))

        if self.dry_run:
            print(
                f"  [DRY] Would FOK buy YES+NO x{shares} @ "
                f"{opp['yes_ask']:.2f}+{opp['no_ask']:.2f}={opp['combined']:.3f} "
                f"on {ticker}",
                flush=True,
            )
            return "dry"

        # Re-check book immediately before sending
        ob_resp = await self.client.get_orderbook(ticker, depth=5)
        ob = ob_resp.get("orderbook", {})
        yes_ask, yes_sz = _best_ask(ob.get("yes_asks") or [])
        no_ask, no_sz = _best_ask(ob.get("no_asks") or [])
        ok, reason, _ = evaluate_completeness(
            yes_ask or 0.0,
            no_ask or 0.0,
            yes_sz,
            no_sz,
            max_combined=self.max_combined,
            min_profit=self.min_profit,
        )
        if not ok or yes_ask is None or no_ask is None:
            print(f"  ⏭️ Arb stale {ticker}: {reason}", flush=True)
            return "stale"

        yes_cents = int(round(yes_ask * 100))
        no_cents = int(round(no_ask * 100))
        shares = int(min(shares, yes_sz, no_sz, MAX_SHARES_PER_TRADE))
        if shares < 1:
            return "stale"

        print(
            f"  ⚡ FOK YES x{shares} @{yes_ask:.2f} then NO @{no_ask:.2f} on {ticker}",
            flush=True,
        )

        yes_resp = await self.client.place_order(
            ticker=ticker,
            client_order_id=str(uuid.uuid4()),
            side="yes",
            action="buy",
            count=shares,
            type_="market",
            yes_price=yes_cents,
        )
        yes_order = (yes_resp or {}).get("order") or {}
        yes_filled = int(yes_order.get("fill_count") or 0)
        if yes_filled <= 0 and str(yes_order.get("status", "")).lower() not in (
            "matched", "filled",
        ):
            # Some adapters only return order id — treat presence as tentative success
            if not yes_order.get("order_id"):
                print(f"  ❌ YES leg failed: {yes_resp}", flush=True)
                return "partial_fail"

        try:
            no_resp = await self.client.place_order(
                ticker=ticker,
                client_order_id=str(uuid.uuid4()),
                side="no",
                action="buy",
                count=shares,
                type_="market",
                no_price=no_cents,
            )
            no_order = (no_resp or {}).get("order") or {}
            if no_order.get("order_id") or int(no_order.get("fill_count") or 0) > 0:
                print(f"  ✅ Completeness pair filled x{shares} on {ticker}", flush=True)
                return "filled"
            raise RuntimeError(f"NO leg rejected: {no_resp}")
        except Exception as exc:
            print(f"  ⚠️ NO leg failed ({exc}); unwinding YES...", flush=True)
            try:
                await self.client.place_order(
                    ticker=ticker,
                    client_order_id=str(uuid.uuid4()),
                    side="yes",
                    action="sell",
                    count=shares,
                    type_="market",
                    yes_price=max(1, yes_cents - 2),
                )
                print(f"  ↩️ Unwound YES leg on {ticker}", flush=True)
                return "unwound"
            except Exception as unwind_exc:
                print(f"  ❌ Unwind failed on {ticker}: {unwind_exc}", flush=True)
                return "partial_fail"
