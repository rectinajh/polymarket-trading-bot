"""BTC 15m Completeness strategy — scan + optional two-leg FOK."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.clients.gamma_client import GammaClient
from src.strategies.btc_15m_completeness.discover import (
    fetch_btc_15m_markets,
    seconds_to_window_end,
)
from src.strategies.capital_policy import (
    DailyEntryLog,
    nav_max_position_pct,
    size_shares,
)
from src.strategies.completeness_arb import (
    _best_ask,
    evaluate_completeness,
)
from src.strategies.safe_compounder import nav_cents

logger = logging.getLogger(__name__)

DEFAULT_LEDGER = Path("data") / "daily_entries_btc15m.json"
DEFAULT_SCAN_LOG = Path("data") / "scan_stats_btc15m.json"
MAX_SCAN_CYCLES = 2000

# Sleeve: tighter than main bot; 15m books are thin and competitive.
MAX_COMBINED_ASK = 0.98
MIN_PROFIT_PER_SHARE = 0.02
MIN_ASK_SIZE = 3.0
SLEEVE_CAP_PCT = 0.02  # max 2% NAV per pair
MAX_ENTRIES_PER_DAY = 12
# Skip windows that are about to resolve (orphan / settlement race).
MIN_SECONDS_LEFT = 90


class Btc15mCompleteness:
    """Independent Completeness sleeve for BTC 15m Up/Down markets."""

    def __init__(
        self,
        client,
        gamma: Optional[GammaClient] = None,
        dry_run: bool = True,
        max_combined: float = MAX_COMBINED_ASK,
        min_profit: float = MIN_PROFIT_PER_SHARE,
        entry_log: Optional[DailyEntryLog] = None,
        scan_log_path: Path = DEFAULT_SCAN_LOG,
    ):
        self.client = client
        self.gamma = gamma or GammaClient()
        self._owns_gamma = gamma is None
        self.dry_run = dry_run
        self.max_combined = max_combined
        self.min_profit = min_profit
        self._entries = entry_log or DailyEntryLog(
            path=DEFAULT_LEDGER, limit=MAX_ENTRIES_PER_DAY,
        )
        self.scan_log_path = Path(scan_log_path)

    async def run(self, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        if dry_run is not None:
            self.dry_run = dry_run

        t0 = time.time()
        stats: Dict[str, Any] = {
            "mode": "btc_15m_completeness",
            "scanned": 0,
            "checked_books": 0,
            "opportunities": 0,
            "attempted": 0,
            "filled_pairs": 0,
            "unwound": 0,
            "errors": 0,
            "skipped_expiring": 0,
            "deployed_cents": 0,
            "expected_profit_cents": 0,
            "near_misses": [],
            "rejects": {},
        }
        rejects: Counter = Counter()

        print("\n⏱️  BTC 15m COMPLETENESS — Up+Down asks < $1", flush=True)
        print(
            f"   Rules: combined < ${self.max_combined:.2f} | "
            f"min profit ${self.min_profit:.2f}/sh | "
            f"sleeve ≤{SLEEVE_CAP_PCT*100:.0f}% NAV | "
            f"≤{MAX_ENTRIES_PER_DAY}/day | "
            f"{'DRY RUN' if self.dry_run else 'LIVE'}",
            flush=True,
        )
        print(
            f"   Window ends in ~{seconds_to_window_end()}s",
            flush=True,
        )

        bal = await self.client.get_balance()
        cash, mtm, nav = nav_cents(bal)

        markets = await fetch_btc_15m_markets(self.gamma)
        stats["scanned"] = len(markets)
        print(f"   Discovered {len(markets)} BTC 15m window(s)", flush=True)

        opps: List[Dict] = []
        near: List[Dict] = []

        for m in markets:
            cond = m.get("_condition_id") or m.get("conditionId") or ""
            yes_tok, no_tok = m.get("_token_ids") or ("", "")
            title = m.get("question") or m.get("title") or ""
            slug = m.get("_slug") or ""

            # Skip if too close to resolution for this window.
            wstart = m.get("_window_start")
            if isinstance(wstart, int):
                left = wstart + 900 - int(time.time())
                if 0 < left < MIN_SECONDS_LEFT:
                    stats["skipped_expiring"] += 1
                    rejects["expiring"] += 1
                    continue

            if hasattr(self.client, "register_market"):
                self.client.register_market(
                    cond,
                    yes_tok,
                    no_tok,
                    neg_risk=bool(m.get("negRisk", False)),
                    tick_size=float(m.get("orderPriceMinTickSize", 0.01) or 0.01),
                )

            try:
                ob_resp = await self.client.get_orderbook(cond, depth=5)
                ob = ob_resp.get("orderbook", {}) or {}
            except Exception as exc:
                logger.info("BTC15m book fail %s: %s", slug, exc)
                rejects["disconnect"] += 1
                continue

            stats["checked_books"] += 1
            yes_ask, yes_sz = _best_ask(ob.get("yes_asks") or [])
            no_ask, no_sz = _best_ask(ob.get("no_asks") or [])

            ok, reason, profit = evaluate_completeness(
                yes_ask or 0.0,
                no_ask or 0.0,
                yes_sz,
                no_sz,
                max_combined=self.max_combined,
                min_profit=self.min_profit,
                min_size=MIN_ASK_SIZE,
            )

            combined = (yes_ask or 0) + (no_ask or 0)
            if not ok or yes_ask is None or no_ask is None:
                # Near-miss: positive profit but below threshold / thin
                if yes_ask is not None and no_ask is not None:
                    raw_profit = 1.0 - combined
                    if 0 < raw_profit < self.min_profit:
                        near.append({
                            "slug": slug,
                            "title": title[:70],
                            "combined": round(combined, 4),
                            "profit": round(raw_profit, 4),
                        })
                        rejects["profit_lt_min"] += 1
                    else:
                        key = reason.split("=")[0] if reason else "fail"
                        if "combined" in key or combined >= self.max_combined:
                            rejects["combined"] += 1
                        else:
                            rejects[key] += 1
                else:
                    rejects["missing_ask"] += 1
                continue

            shares = size_shares(
                price=combined,
                nav_cents=nav,
                cash_cents=cash,
                ask_depth=min(yes_sz, no_sz),
                extra_cap_pct=min(SLEEVE_CAP_PCT, nav_max_position_pct(nav)),
            )
            if shares < 1:
                rejects["size"] += 1
                continue

            opps.append({
                "ticker": cond,
                "slug": slug,
                "title": title[:80],
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "yes_size": yes_sz,
                "no_size": no_sz,
                "shares": shares,
                "profit_per": profit,
                "combined": combined,
            })

        stats["opportunities"] = len(opps)
        stats["near_misses"] = near[:10]
        opps.sort(key=lambda x: -x["profit_per"])

        for opp in opps[:5]:
            print(
                f"  OPP {opp['combined']:.3f} (+${opp['profit_per']:.3f}/sh) "
                f"x{opp['shares']} | {opp.get('slug')}",
                flush=True,
            )

        remaining = self._entries.remaining()
        for opp in opps:
            if remaining <= 0:
                rejects["daily_cap"] += 1
                break
            try:
                result = await self._execute_pair(opp)
                stats["attempted"] += 1
                if result in ("filled", "dry"):
                    self._entries.record(
                        opp["ticker"], opp.get("title") or "", kind="btc15m",
                    )
                    remaining -= 1
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
                if not self.dry_run and result in ("filled", "unwound", "partial_fail"):
                    break
            except Exception as exc:
                logger.error("BTC15m execute failed: %s", exc)
                stats["errors"] += 1

        if self._owns_gamma:
            try:
                await self.gamma.close()
            except Exception:
                pass
        if hasattr(self.client, "flush_token_cache"):
            self.client.flush_token_cache()

        elapsed = time.time() - t0
        stats["rejects"] = dict(rejects)
        stats["elapsed_s"] = round(elapsed, 1)
        stats["nav_cents"] = nav
        self._append_scan_log(stats)

        print(
            f"   BTC15m done: windows={stats['scanned']} books={stats['checked_books']} "
            f"opps={stats['opportunities']} filled={stats['filled_pairs']} "
            f"unwound={stats['unwound']} ({elapsed:.1f}s)",
            flush=True,
        )
        rej_txt = " ".join(f"{k}={v}" for k, v in sorted(rejects.items()) if v)
        if rej_txt:
            print(f"   Rejects: {rej_txt}", flush=True)
        if near:
            print(
                f"   Near-miss (0<profit<{self.min_profit}): {len(near)} "
                f"best={near[0].get('combined')}",
                flush=True,
            )
        return stats

    async def _execute_pair(self, opp: Dict) -> str:
        ticker = opp["ticker"]
        shares = int(opp["shares"])
        yes_cents = int(round(opp["yes_ask"] * 100))
        no_cents = int(round(opp["no_ask"] * 100))

        if self.dry_run:
            print(
                f"  [DRY] Would FOK Up+Down x{shares} @ "
                f"{opp['yes_ask']:.2f}+{opp['no_ask']:.2f}={opp['combined']:.3f} "
                f"| {opp.get('slug')}",
                flush=True,
            )
            return "dry"

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
            min_size=MIN_ASK_SIZE,
        )
        if not ok or yes_ask is None or no_ask is None:
            print(f"  ⏭️ BTC15m stale {opp.get('slug')}: {reason}", flush=True)
            return "stale"

        yes_cents = int(round(yes_ask * 100))
        no_cents = int(round(no_ask * 100))
        shares = int(min(shares, yes_sz, no_sz))
        if shares < 1:
            return "stale"

        print(
            f"  ⚡ FOK Up x{shares} @{yes_ask:.2f} then Down @{no_ask:.2f} "
            f"| {opp.get('slug')}",
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
            if not yes_order.get("order_id"):
                print(f"  ❌ Up leg failed: {yes_resp}", flush=True)
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
                print(f"  ✅ BTC15m pair filled x{shares} | {opp.get('slug')}", flush=True)
                return "filled"
            raise RuntimeError(f"Down leg rejected: {no_resp}")
        except Exception as exc:
            print(f"  ⚠️ Down leg failed ({exc}); unwinding Up...", flush=True)
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
                print(f"  ↩️ Unwound Up leg | {opp.get('slug')}", flush=True)
                return "unwound"
            except Exception as unwind_exc:
                print(f"  ❌ Unwind failed | {opp.get('slug')}: {unwind_exc}", flush=True)
                return "partial_fail"

    def _append_scan_log(self, stats: Dict[str, Any]) -> None:
        entry = {
            "ts": datetime.now().astimezone().isoformat(),
            **{k: v for k, v in stats.items() if k != "near_misses"},
            "near_miss_count": len(stats.get("near_misses") or []),
            "near_misses": stats.get("near_misses") or [],
        }
        try:
            self.scan_log_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"cycles": []}
            if self.scan_log_path.exists():
                try:
                    raw = json.loads(self.scan_log_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and isinstance(raw.get("cycles"), list):
                        data = raw
                except (OSError, json.JSONDecodeError):
                    pass
            data["cycles"].append(entry)
            if len(data["cycles"]) > MAX_SCAN_CYCLES:
                data["cycles"] = data["cycles"][-MAX_SCAN_CYCLES:]
            tmp = self.scan_log_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.scan_log_path)
        except OSError as exc:
            logger.warning("BTC15m scan log write failed: %s", exc)
