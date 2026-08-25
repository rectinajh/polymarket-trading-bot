"""RN1-style sports Maker sleeve — Pinnacle reference + Polymarket favorites."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.clients.gamma_client import GammaClient
from src.clients.odds_api_client import OddsAPIClient
from src.strategies.capital_policy import DailyEntryLog, size_shares
from src.strategies.sports.config import (
    DEFAULT_LEDGER,
    DEFAULT_SCAN_LOG,
    EU_SOCCER_SPORT_KEYS,
    FAVORITE_MAX,
    FAVORITE_MIN,
    MAX_ENTRIES_PER_DAY,
    MAX_SCAN_CYCLES,
    MIN_EDGE,
    MIN_HOURS_BEFORE_KICKOFF,
    RN1_CONFIRM_MODE,
    RN1_PROXY_WALLET,
    SLEEVE_CAP_PCT,
    SPORTS_EXPERIMENT_DAYS,
)
from src.strategies.sports.discover import fetch_match_winner_markets, in_favorite_band
from src.strategies.sports.edge import evaluate_maker_opportunity
from src.strategies.sports.match import match_market_to_reference
from src.strategies.sports.rn1_tracker import RN1WalletTracker, rn1_confirms
from src.strategies.sports.sports_alerts import (
    notify_sports_halt,
    notify_sports_order,
    notify_sports_settlement,
)
from src.strategies.sports.sports_guard import check_trading_allowed
from src.strategies.sports.sports_pnl import SportsPnL
from src.strategies.safe_compounder import nav_cents

logger = logging.getLogger(__name__)


class Rn1SportsMaker:
    """Independent RN1 sports Maker sleeve (dry-run by default)."""

    def __init__(
        self,
        client,
        gamma: Optional[GammaClient] = None,
        odds: Optional[OddsAPIClient] = None,
        rn1_tracker: Optional[RN1WalletTracker] = None,
        dry_run: bool = True,
        entry_log: Optional[DailyEntryLog] = None,
        scan_log_path: Path = DEFAULT_SCAN_LOG,
        pnl: Optional[SportsPnL] = None,
    ):
        self.client = client
        self.gamma = gamma or GammaClient()
        self._owns_gamma = gamma is None
        self.odds = odds or OddsAPIClient()
        self._owns_odds = odds is None
        self.rn1 = rn1_tracker or RN1WalletTracker()
        self._owns_rn1 = rn1_tracker is None
        self.dry_run = dry_run
        self._entries = entry_log or DailyEntryLog(
            path=DEFAULT_LEDGER,
            limit=MAX_ENTRIES_PER_DAY,
        )
        self.scan_log_path = Path(scan_log_path)
        self._pnl = pnl or SportsPnL()
        self._halt_notified = False

    async def run(self, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        if dry_run is not None:
            self.dry_run = dry_run

        t0 = time.time()
        stats: Dict[str, Any] = {
            "mode": "sports_rn1_maker",
            "live": not self.dry_run,
            "rn1_confirm_mode": RN1_CONFIRM_MODE,
            "scanned": 0,
            "favorite_band": 0,
            "matched_reference": 0,
            "opportunities": 0,
            "attempted": 0,
            "placed": 0,
            "errors": 0,
            "skipped_daily_cap": 0,
            "skipped_kickoff": 0,
            "rn1_trades_cached": 0,
            "rn1_confirmed": 0,
            "guard_halted": 0,
            "settled": 0,
            "deployed_cents": 0,
            "pnl_summary": {},
            "guard_meta": {},
            "signals": [],
            "rejects": {},
            "odds_quota": {},
        }
        rejects: Counter = Counter()

        print(
            "\n⚽ RN1 SPORTS MAKER — Pinnacle reference + favorite YES bids",
            flush=True,
        )
        print(
            f"   Rules: fav {FAVORITE_MIN:.2f}–{FAVORITE_MAX:.2f} | "
            f"edge ≥{MIN_EDGE:.2f} | sleeve ≤{SLEEVE_CAP_PCT*100:.0f}% NAV | "
            f"≤{MAX_ENTRIES_PER_DAY}/day | "
            f"kickoff ≥{MIN_HOURS_BEFORE_KICKOFF:.0f}h | "
            f"RN1 confirm={RN1_CONFIRM_MODE} | "
            f"{'DRY RUN' if self.dry_run else 'LIVE'}",
            flush=True,
        )

        remaining = self._entries.remaining()
        if remaining <= 0:
            print(f"   Daily cap reached ({MAX_ENTRIES_PER_DAY}/day).", flush=True)
            stats["skipped_daily_cap"] = 1

        bal = await self.client.get_balance()
        cash, mtm, nav = nav_cents(bal)
        print(f"   NAV ${nav/100:.2f} | cash ${cash/100:.2f}", flush=True)

        positions_resp = await self.client.get_positions()
        pos_list = positions_resp.get("market_positions") or []
        if not isinstance(pos_list, list):
            pos_list = []

        settled_now = self._pnl.update_from_positions(pos_list)
        stats["settled"] = len(settled_now)
        for entry in settled_now:
            notify_sports_settlement(
                team=str(entry.get("team") or ""),
                status=str(entry.get("status") or ""),
                pnl_cents=int(entry.get("settled_pnl_cents") or 0),
                title=str(entry.get("title") or ""),
            )

        allowed, guard_reason, guard_meta = check_trading_allowed(
            nav_cents=nav, pnl=self._pnl,
        )
        stats["guard_meta"] = guard_meta
        stats["pnl_summary"] = self._pnl.experiment_summary()
        if not allowed:
            stats["guard_halted"] = 1
            stats["guard_reason"] = guard_reason
            print(f"   ⛔ Guard halt: {guard_reason}", flush=True)
            if not self._halt_notified:
                notify_sports_halt(guard_reason, guard_meta)
                self._halt_notified = True
        else:
            self._halt_notified = False
            exp = stats["pnl_summary"]
            if exp.get("experiment_start"):
                elapsed = guard_meta.get("days_elapsed", 0)
                print(
                    f"   Experiment day {elapsed}/{SPORTS_EXPERIMENT_DAYS} · "
                    f"realized ${int(exp.get('realized_pnl_cents', 0))/100:+.2f}",
                    flush=True,
                )

        open_orders: set = set()
        if not self.dry_run and hasattr(self.client, "get_orders"):
            try:
                resp = await self.client.get_orders(status="live")
                for o in resp.get("orders") or []:
                    if str(o.get("side") or "").lower() == "yes":
                        cond = str(o.get("condition_id") or o.get("ticker") or "")
                        if cond:
                            open_orders.add(cond.lower())
            except Exception as exc:
                logger.warning("Open orders fetch failed: %s", exc)

        if RN1_CONFIRM_MODE not in ("off", "none", "disabled", "0", "false"):
            try:
                rn1_count = await self.rn1.refresh()
                stats["rn1_trades_cached"] = rn1_count
                print(
                    f"   RN1 wallet {RN1_PROXY_WALLET[:8]}…{RN1_PROXY_WALLET[-4:]} | "
                    f"{rn1_count} trades / {self.rn1.lookback_hours:.0f}h",
                    flush=True,
                )
            except Exception as exc:
                logger.warning("RN1 tracker refresh failed: %s", exc)
                stats["errors"] += 1
                stats["rn1_error"] = str(exc)[:200]
                if RN1_CONFIRM_MODE == "strict":
                    print(f"   ⚠️  RN1 tracker failed — skipping cycle: {exc}", flush=True)
                    self._append_scan(stats, t0)
                    return stats

        try:
            ref_events = await self.odds.fetch_eu_soccer_reference(EU_SOCCER_SPORT_KEYS)
        except Exception as exc:
            logger.exception("Odds API fetch failed")
            stats["errors"] += 1
            stats["odds_error"] = str(exc)[:200]
            self._append_scan(stats, t0)
            return stats

        stats["odds_quota"] = dict(self.odds.last_quota)
        stats["reference_events"] = len(ref_events)
        print(
            f"   Pinnacle events: {len(ref_events)} | "
            f"quota left: {self.odds.last_quota.get('remaining', '?')}",
            flush=True,
        )

        markets = await self._fetch_markets()
        stats["scanned"] = len(markets)

        pos_tickers = {
            str(p.get("condition_id") or p.get("conditionId") or p.get("ticker") or "")
            for p in pos_list
            if isinstance(p, dict) and float(p.get("size") or 0) > 0
        }

        now = datetime.now(timezone.utc)
        for mkt in markets:
            if not allowed:
                rejects["guard_halt"] += 1
                continue
            if not in_favorite_band(mkt.yes_price, lo=FAVORITE_MIN, hi=FAVORITE_MAX):
                rejects["not_favorite"] += 1
                continue
            stats["favorite_band"] += 1

            ref = match_market_to_reference(mkt, ref_events)
            if not ref:
                rejects["no_reference"] += 1
                continue
            stats["matched_reference"] += 1

            hours_left = (ref.event.commence_time - now).total_seconds() / 3600.0
            if hours_left < MIN_HOURS_BEFORE_KICKOFF:
                rejects["kickoff_soon"] += 1
                stats["skipped_kickoff"] += 1
                continue

            if mkt.condition_id in pos_tickers:
                rejects["already_position"] += 1
                continue

            if remaining <= 0:
                rejects["daily_cap"] += 1
                continue

            if mkt.condition_id in self._entries.tickers():
                rejects["already_entered_today"] += 1
                continue

            if mkt.condition_id.lower() in open_orders:
                rejects["open_order"] += 1
                continue

            self._register_market(mkt)

            try:
                book = await self.client.get_orderbook(mkt.condition_id, depth=5)
            except Exception as exc:
                rejects["book_error"] += 1
                stats["errors"] += 1
                logger.warning("Orderbook %s: %s", mkt.condition_id[:12], exc)
                continue

            opp = evaluate_maker_opportunity(mkt, ref, book)
            if not opp:
                rejects["no_edge"] += 1
                continue

            stats["opportunities"] += 1

            confirm = rn1_confirms(opp, self.rn1)
            if not confirm.confirmed:
                rejects["rn1_no_confirm"] += 1
                continue
            stats["rn1_confirmed"] += 1

            signal = {
                "team": mkt.team,
                "date": mkt.match_date.isoformat(),
                "fair": round(opp.fair_prob, 4),
                "maker": round(opp.maker_price, 4),
                "edge": round(opp.edge, 4),
                "poly_yes": round(mkt.yes_price, 4),
                "sport": ref.event.sport_key,
                "match": f"{ref.event.home_team} vs {ref.event.away_team}",
                "rn1_mode": confirm.mode,
                "rn1_reason": confirm.reason,
            }
            if confirm.matching_trade:
                signal["rn1_price"] = round(confirm.matching_trade.price, 4)
            stats["signals"].append(signal)

            shares = size_shares(
                price=opp.maker_price,
                nav_cents=nav,
                cash_cents=cash,
                ask_depth=999.0,
                extra_cap_pct=SLEEVE_CAP_PCT,
            )
            if shares <= 0:
                rejects["size_zero"] += 1
                continue

            cost_cents = int(shares * opp.maker_price * 100)
            print(
                f"  CONFIRMED YES x{shares} @ ${opp.maker_price:.2f} | "
                f"Pinn {opp.fair_prob:.2f} edge {opp.edge:.2f} | "
                f"RN1: {confirm.reason} | "
                f"{mkt.team} ({mkt.match_date})",
                flush=True,
            )
            print(f"    {ref.event.home_team} vs {ref.event.away_team}", flush=True)

            stats["attempted"] += 1
            if self.dry_run:
                stats["placed"] += 1
                stats["deployed_cents"] += cost_cents
                cash -= cost_cents
                remaining -= 1
                pos_tickers.add(mkt.condition_id)
                self._entries.record(mkt.condition_id, mkt.question, kind="sports_rn1")
                continue

            try:
                price_cents = int(round(opp.maker_price * 100))
                await self.client.place_order(
                    ticker=mkt.condition_id,
                    client_order_id=str(uuid.uuid4()),
                    side="yes",
                    action="buy",
                    count=shares,
                    type_="limit",
                    yes_price=price_cents,
                )
                stats["placed"] += 1
                stats["deployed_cents"] += cost_cents
                cash -= cost_cents
                remaining -= 1
                pos_tickers.add(mkt.condition_id)
                open_orders.add(mkt.condition_id.lower())
                self._entries.record(mkt.condition_id, mkt.question, kind="sports_rn1")
                self._pnl.record_entry(
                    condition_id=mkt.condition_id,
                    title=mkt.question,
                    team=mkt.team,
                    match_date=mkt.match_date.isoformat(),
                    shares=shares,
                    price=opp.maker_price,
                    fair_prob=opp.fair_prob,
                    edge=opp.edge,
                    sport=ref.event.sport_key,
                    match=f"{ref.event.home_team} vs {ref.event.away_team}",
                    live=True,
                )
                notify_sports_order(
                    team=mkt.team,
                    match=f"{ref.event.home_team} vs {ref.event.away_team}",
                    shares=shares,
                    price=opp.maker_price,
                    edge=opp.edge,
                    fair_prob=opp.fair_prob,
                    live=True,
                    condition_id=mkt.condition_id,
                    rn1_reason=confirm.reason,
                )
            except Exception as exc:
                stats["errors"] += 1
                rejects["order_error"] += 1
                print(f"  ORDER FAIL {mkt.condition_id[:12]}: {exc}", flush=True)

        stats["rejects"] = dict(rejects)
        stats["elapsed_s"] = round(time.time() - t0, 2)
        stats["entries_remaining"] = max(0, remaining)
        self._append_scan(stats, t0)
        self._flush_caches()
        return stats

    async def _fetch_markets(self):
        return await fetch_match_winner_markets(self.gamma)

    def _register_market(self, mkt) -> None:
        if hasattr(self.client, "register_market"):
            self.client.register_market(
                mkt.condition_id,
                mkt.yes_token,
                mkt.no_token,
                neg_risk=mkt.neg_risk,
                tick_size=mkt.tick_size,
            )

    def _append_scan(self, stats: Dict[str, Any], t0: float) -> None:
        try:
            self.scan_log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"cycles": []}
            if self.scan_log_path.exists():
                raw = json.loads(self.scan_log_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("cycles"), list):
                    payload = raw
            payload["cycles"].append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(time.time() - t0, 2),
                **{k: v for k, v in stats.items() if k != "signals" or len(v) <= 20},
            })
            if len(payload["cycles"]) > MAX_SCAN_CYCLES:
                payload["cycles"] = payload["cycles"][-MAX_SCAN_CYCLES:]
            tmp = self.scan_log_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.scan_log_path)
        except OSError:
            pass

    def _flush_caches(self) -> None:
        if hasattr(self.client, "flush_token_cache"):
            self.client.flush_token_cache()

    async def close(self) -> None:
        if self._owns_gamma:
            await self.gamma.close()
        if self._owns_odds:
            await self.odds.close()
