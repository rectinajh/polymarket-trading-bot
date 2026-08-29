"""RN1 sports copy-trade — soccer + optional tennis, ≤$1 USDC/order."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from src.clients.gamma_client import GammaClient
from src.strategies.capital_policy import DailyEntryLog
from src.strategies.sports.config import (
    COPY_ALLOW_TENNIS,
    COPY_EXIT_FAIL_COOLDOWN_S,
    COPY_FOLLOW_RN1_EXIT,
    COPY_HARD_MAX_USDC,
    COPY_MAX_USDC,
    COPY_MIN_NOTIONAL,
    COPY_MIN_SHARES,
    COPY_PRICE_MAX,
    COPY_PRICE_MIN,
    COPY_SIDES,
    COPY_STOP_LOSS_PCT,
    COPY_SYNC_OPEN_POSITIONS,
    DEFAULT_COPY_SEEN,
    DEFAULT_LEDGER,
    DEFAULT_SCAN_LOG,
    MAX_ENTRIES_PER_DAY,
    MAX_SCAN_CYCLES,
    RN1_LOOKBACK_HOURS,
    RN1_PROXY_WALLET,
    SPORTS_EXPERIMENT_DAYS,
)
from src.strategies.sports.rn1_tracker import (
    RN1WalletTracker,
    copy_share_count,
)
from src.strategies.sports.soccer_filter import is_tennis_market, side_from_outcome_index
from src.strategies.sports.sports_alerts import (
    notify_sports_exit,
    notify_sports_halt,
    notify_sports_order,
    notify_sports_settlement,
)
from src.strategies.sports.sports_guard import check_trading_allowed
from src.strategies.sports.sports_pnl import SportsPnL
from src.strategies.safe_compounder import nav_cents

logger = logging.getLogger(__name__)


class Rn1SportsMaker:
    """Copy RN1 soccer (+ tennis) BUY trades; hard-capped at COPY_MAX_USDC."""

    def __init__(
        self,
        client,
        gamma: Optional[GammaClient] = None,
        rn1_tracker: Optional[RN1WalletTracker] = None,
        dry_run: bool = True,
        entry_log: Optional[DailyEntryLog] = None,
        scan_log_path: Path = DEFAULT_SCAN_LOG,
        pnl: Optional[SportsPnL] = None,
        seen_path: Path = DEFAULT_COPY_SEEN,
        odds=None,
    ):
        self.client = client
        self.gamma = gamma or GammaClient()
        self._owns_gamma = gamma is None
        if hasattr(self.client, "set_gamma_client") and gamma is not None:
            self.client.set_gamma_client(gamma)
        self.rn1 = rn1_tracker or RN1WalletTracker()
        self.dry_run = dry_run
        self._entries = entry_log or DailyEntryLog(
            path=DEFAULT_LEDGER,
            limit=MAX_ENTRIES_PER_DAY,
        )
        self.scan_log_path = Path(scan_log_path)
        self.seen_path = Path(seen_path)
        self._pnl = pnl or SportsPnL()
        self._halt_notified = False
        self._seen: Set[str] = self._load_seen()
        self._bootstrapped = bool(self._seen)
        # hold_key -> unix ts when last EXIT FAIL happened (cooldown)
        self._exit_fail_until: Dict[str, float] = {}
        # Seal lookback once per process so restart never backfills 6h of trades.
        self._lookback_sealed = False

    def _load_seen(self) -> Set[str]:
        try:
            if not self.seen_path.exists():
                return set()
            raw = json.loads(self.seen_path.read_text(encoding="utf-8"))
            keys = raw.get("keys") if isinstance(raw, dict) else raw
            if isinstance(keys, list):
                return {str(k) for k in keys}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return set()

    def _save_seen(self) -> None:
        try:
            self.seen_path.parent.mkdir(parents=True, exist_ok=True)
            keys = list(self._seen)
            if len(keys) > 8000:
                keys = keys[-8000:]
                self._seen = set(keys)
            payload = {
                "updated": datetime.now(timezone.utc).isoformat(),
                "wallet": RN1_PROXY_WALLET,
                "mode": "soccer_tennis" if COPY_ALLOW_TENNIS else "soccer_only",
                "keys": sorted(self._seen),
            }
            tmp = self.seen_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.seen_path)
        except OSError:
            pass

    async def run(self, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        if dry_run is not None:
            self.dry_run = dry_run

        t0 = time.time()
        sports_mode = "soccer+tennis" if COPY_ALLOW_TENNIS else "soccer"
        stats: Dict[str, Any] = {
            "mode": "sports_rn1_copy",
            "live": not self.dry_run,
            "copy_max_usdc": COPY_MAX_USDC,
            "sports_mode": sports_mode,
            "allow_tennis": COPY_ALLOW_TENNIS,
            "price_band": [COPY_PRICE_MIN, COPY_PRICE_MAX],
            "scanned": 0,
            "copyable_trades": 0,
            "open_copyable_positions": 0,
            "new_signals": 0,
            "opportunities": 0,
            "attempted": 0,
            "placed": 0,
            "errors": 0,
            "skipped_daily_cap": 0,
            "rn1_trades_cached": 0,
            "bootstrapped": 0,
            "synced_positions": 0,
            "exits": 0,
            "stop_losses": 0,
            "rn1_follows_exit": 0,
            "guard_halted": 0,
            "settled": 0,
            "deployed_cents": 0,
            "pnl_summary": {},
            "guard_meta": {},
            "signals": [],
            "rejects": {},
        }
        rejects: Counter = Counter()

        print(
            f"\nRN1 COPY — {sports_mode} + follow-exit + SL",
            flush=True,
        )
        print(
            f"   Rules: {sports_mode} | price [{COPY_PRICE_MIN:.2f},{COPY_PRICE_MAX:.2f}] | "
            f"CLOB min {COPY_MIN_SHARES}sh / "
            f"${COPY_MIN_NOTIONAL:.2f} | "
            f"SL {COPY_STOP_LOSS_PCT*100:.0f}% | "
            f"follow RN1 exit={'ON' if COPY_FOLLOW_RN1_EXIT else 'OFF'} | "
            f"sync open={'ON' if COPY_SYNC_OPEN_POSITIONS else 'OFF'} | "
            f"TP=manual | "
            f"≤{MAX_ENTRIES_PER_DAY}/day | "
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

        # RN1 open copyable book (needed for exits + copy)
        try:
            rn1_pos = await self.rn1.fetch_open_copyable_positions()
        except Exception as exc:
            logger.warning("RN1 positions fetch failed: %s", exc)
            stats["errors"] += 1
            rn1_pos = []
        stats["open_copyable_positions"] = len(rn1_pos)
        rn1_held: Set[str] = set()
        for rp in rn1_pos:
            side = side_from_outcome_index(rp.outcome_index)
            if side:
                rn1_held.add(f"{rp.condition_id.lower()}:{side}")
        print(
            f"   RN1 open copyable positions: {len(rn1_pos)} "
            f"({len(rn1_held)} sides)",
            flush=True,
        )

        # Seal lookback ASAP (even if Guard later blocks entries).
        if not self._lookback_sealed:
            try:
                await self.rn1.refresh(force=True)
            except Exception as exc:
                logger.warning("RN1 seal refresh failed: %s", exc)
            for t in self.rn1.trades:
                self._seen.add(t.copy_key())
                if t.is_copyable:
                    self._seen.add(t.position_key())
            for pos in rn1_pos:
                self._seen.add(pos.position_key())
            self._lookback_sealed = True
            self._bootstrapped = True
            self._save_seen()
            print(
                f"   Sealed lookback: {len(self.rn1.trades)} trade keys + "
                f"{len(rn1_pos)} open sides (no backfill).",
                flush=True,
            )

        # --- Exits first (always): stop-loss + follow RN1 flat ---
        await self._manage_exits(
            pos_list=pos_list,
            rn1_held=rn1_held,
            stats=stats,
            rejects=rejects,
        )

        allowed, guard_reason, guard_meta = check_trading_allowed(
            nav_cents=nav, pnl=self._pnl,
        )
        stats["guard_meta"] = guard_meta
        stats["pnl_summary"] = self._pnl.experiment_summary()
        if not allowed:
            stats["guard_halted"] = 1
            stats["guard_reason"] = guard_reason
            print(f"   ⛔ Guard halt (new entries blocked): {guard_reason}", flush=True)
            if not self._halt_notified:
                notify_sports_halt(guard_reason, guard_meta)
                self._halt_notified = True
            self._save_seen()
            stats["rejects"] = dict(rejects)
            stats["elapsed_s"] = round(time.time() - t0, 2)
            self._append_scan(stats, t0)
            return stats
        self._halt_notified = False

        # Refresh holdings after exits
        positions_resp = await self.client.get_positions()
        pos_list = positions_resp.get("market_positions") or []
        if not isinstance(pos_list, list):
            pos_list = []

        # Our existing holdings keyed by condition:side
        our_held: Set[str] = set()
        for p in pos_list:
            if not isinstance(p, dict) or float(p.get("size") or 0) <= 0:
                continue
            cond = str(p.get("condition_id") or p.get("conditionId") or "").lower()
            side = str(p.get("side") or "").lower()
            if cond and side in ("yes", "no"):
                our_held.add(f"{cond}:{side}")

        open_orders: Set[str] = set()
        if not self.dry_run and hasattr(self.client, "get_orders"):
            try:
                resp = await self.client.get_orders(status="live")
                for o in resp.get("orders") or []:
                    cond = str(o.get("condition_id") or o.get("ticker") or "").lower()
                    side = str(o.get("side") or "").lower()
                    if cond and side in ("yes", "no"):
                        open_orders.add(f"{cond}:{side}")
            except Exception as exc:
                logger.warning("Open orders fetch failed: %s", exc)

        # --- 1) Optionally sync RN1 open copyable book (default OFF) ---
        if COPY_SYNC_OPEN_POSITIONS:
            for pos in rn1_pos:
                if remaining <= 0:
                    rejects["daily_cap"] += 1
                    break
                key = pos.position_key()
                if key in self._seen:
                    rejects["already_synced"] += 1
                    continue
                placed, cash, remaining = await self._copy_one(
                    condition_id=pos.condition_id,
                    title=pos.title,
                    outcome_label=pos.outcome,
                    outcome_index=pos.outcome_index,
                    price=pos.copy_price,
                    rn1_size=pos.size,
                    neg_risk=pos.neg_risk,
                    source="position",
                    dedupe_key=key,
                    cash=cash,
                    remaining=remaining,
                    our_held=our_held,
                    open_orders=open_orders,
                    stats=stats,
                    rejects=rejects,
                    slug=pos.slug,
                    event_slug=pos.event_slug,
                )
                if placed:
                    stats["synced_positions"] += 1
        else:
            # Still mark current open sides as seen so we don't double-copy
            # if sync is later enabled for the same book.
            for pos in rn1_pos:
                self._seen.add(pos.position_key())
            print(
                "   Sync open positions: OFF (only new RN1 BUY trades)",
                flush=True,
            )

        # --- 2) Poll new copyable BUY trades ---
        try:
            rn1_count = await self.rn1.refresh(force=True)
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
            self._save_seen()
            self._append_scan(stats, t0)
            return stats

        trades = self.rn1.trades
        stats["scanned"] = len(trades)
        copyable_trades = [t for t in trades if t.is_copyable]
        stats["copyable_trades"] = len(copyable_trades)

        # Lookback already sealed at cycle start; only copy trades newer than seal.
        unseen = [t for t in copyable_trades if t.copy_key() not in self._seen]
        for t in trades:
            if not t.is_copyable:
                self._seen.add(t.copy_key())

        unseen.sort(key=lambda t: t.timestamp)
        stats["new_signals"] = len(unseen)
        print(f"   New copyable BUY signals: {len(unseen)}", flush=True)

        for trade in unseen:
            key = trade.copy_key()
            self._seen.add(key)
            if trade.side not in COPY_SIDES:
                rejects["not_buy"] += 1
                continue
            self._seen.add(trade.position_key())
            if remaining <= 0:
                rejects["daily_cap"] += 1
                continue
            _placed, cash, remaining = await self._copy_one(
                condition_id=trade.condition_id,
                title=trade.title,
                outcome_label=trade.outcome,
                outcome_index=trade.outcome_index,
                price=trade.price,
                rn1_size=trade.size,
                neg_risk=trade.neg_risk,
                source="trade",
                dedupe_key=key,
                cash=cash,
                remaining=remaining,
                our_held=our_held,
                open_orders=open_orders,
                stats=stats,
                rejects=rejects,
                slug=trade.slug,
                event_slug=trade.event_slug,
            )

        # Re-read remaining/cash is awkward after loop; _copy_one updates
        # through nonlocal-style returns — fix by using a small state bag.
        self._save_seen()
        stats["rejects"] = dict(rejects)
        stats["elapsed_s"] = round(time.time() - t0, 2)
        stats["entries_remaining"] = self._entries.remaining()
        self._append_scan(stats, t0)
        self._flush_caches()
        return stats

    async def _copy_one(
        self,
        *,
        condition_id: str,
        title: str,
        outcome_label: str,
        outcome_index: int,
        price: float,
        rn1_size: float,
        neg_risk: bool,
        source: str,
        dedupe_key: str,
        cash: int,
        remaining: int,
        our_held: Set[str],
        open_orders: Set[str],
        stats: Dict[str, Any],
        rejects: Counter,
        slug: str = "",
        event_slug: str = "",
    ) -> Tuple[bool, int, int]:
        """Place one mirror order. Returns (placed, cash, remaining)."""
        side = side_from_outcome_index(outcome_index)
        if side is None:
            rejects["bad_outcome"] += 1
            self._seen.add(dedupe_key)
            return False, cash, remaining

        hold_key = f"{condition_id.lower()}:{side}"
        entry_key = hold_key

        if not (COPY_PRICE_MIN <= price <= COPY_PRICE_MAX):
            rejects["price_band"] += 1
            self._seen.add(dedupe_key)
            return False, cash, remaining
        if hold_key in our_held:
            rejects["already_position"] += 1
            self._seen.add(dedupe_key)
            return False, cash, remaining
        if remaining <= 0:
            rejects["daily_cap"] += 1
            return False, cash, remaining
        if entry_key in self._entries.tickers():
            rejects["already_entered_today"] += 1
            self._seen.add(dedupe_key)
            return False, cash, remaining
        if hold_key in open_orders:
            rejects["open_order"] += 1
            self._seen.add(dedupe_key)
            return False, cash, remaining
        if cash < int(COPY_MIN_NOTIONAL * 100):
            rejects["no_cash"] += 1
            return False, cash, remaining

        shares = copy_share_count(price, COPY_MAX_USDC)
        if shares <= 0:
            rejects["size_zero"] += 1
            # Don't permanently burn — market mins may change.
            return False, cash, remaining

        cost_cents = int(round(shares * price * 100))
        if cost_cents > cash:
            rejects["no_cash"] += 1
            return False, cash, remaining

        stats["opportunities"] += 1
        signal = {
            "source": source,
            "title": title[:80],
            "outcome": outcome_label,
            "side": side,
            "rn1_price": round(price, 4),
            "rn1_size": round(rn1_size, 2),
            "our_shares": shares,
            "cost_usdc": round(cost_cents / 100.0, 4),
            "condition_id": condition_id[:18],
        }
        stats["signals"].append(signal)
        print(
            f"  COPY[{source}] {side.upper()}({outcome_label}) x{shares} @ ${price:.2f} "
            f"= ${cost_cents/100:.2f} (min {COPY_MIN_SHARES}sh/${COPY_MIN_NOTIONAL:.2f}) "
            f"| RN1 {rn1_size:.0f}sh | {title[:50]}",
            flush=True,
        )

        stats["attempted"] += 1

        if self.dry_run:
            self._seen.add(dedupe_key)
            self._seen.add(f"pos:{condition_id.lower()}:{outcome_index}")
            stats["placed"] += 1
            stats["deployed_cents"] += cost_cents
            cash -= cost_cents
            remaining -= 1
            our_held.add(hold_key)
            self._entries.record(entry_key, title, kind="sports_rn1_copy")
            return True, cash, remaining

        try:
            await self._ensure_registered(condition_id, neg_risk=neg_risk)
            price_cents = max(1, min(99, int(round(price * 100))))
            order_kwargs: Dict[str, Any] = {
                "ticker": condition_id,
                "client_order_id": str(uuid.uuid4()),
                "side": side,
                "action": "buy",
                "count": shares,
                "type_": "limit",
            }
            if side == "yes":
                order_kwargs["yes_price"] = price_cents
            else:
                order_kwargs["no_price"] = price_cents
            await self.client.place_order(**order_kwargs)
            self._seen.add(dedupe_key)
            self._seen.add(f"pos:{condition_id.lower()}:{outcome_index}")
            stats["placed"] += 1
            stats["deployed_cents"] += cost_cents
            cash -= cost_cents
            remaining -= 1
            our_held.add(hold_key)
            open_orders.add(hold_key)
            self._entries.record(entry_key, title, kind="sports_rn1_copy")
            team = _team_from_title(title)
            sport_tag = (
                "rn1_tennis_copy"
                if is_tennis_market(title, slug, event_slug)
                else "rn1_soccer_copy"
            )
            self._pnl.record_entry(
                condition_id=condition_id,
                title=title,
                team=team,
                match_date="",
                shares=shares,
                price=price,
                fair_prob=price,
                edge=0.0,
                sport=sport_tag,
                match=f"{outcome_label} | {title[:60]}",
                live=True,
                side=side,
            )
            notify_sports_order(
                team=team or outcome_label,
                match=title[:80],
                shares=shares,
                price=price,
                edge=0.0,
                fair_prob=price,
                live=True,
                condition_id=condition_id,
                rn1_reason=f"{sport_tag} {source} {side}({outcome_label}) @ {price:.2f}",
            )
            return True, cash, remaining
        except Exception as exc:
            stats["errors"] += 1
            rejects["order_error"] += 1
            # Leave unseen so next cycle can retry after sizing/market fixes.
            self._seen.discard(dedupe_key)
            self._seen.discard(f"pos:{condition_id.lower()}:{outcome_index}")
            print(f"  ORDER FAIL {condition_id[:12]}: {exc}", flush=True)
            return False, cash, remaining

    async def _manage_exits(
        self,
        *,
        pos_list: list,
        rn1_held: Set[str],
        stats: Dict[str, Any],
        rejects: Counter,
    ) -> None:
        """Stop-loss + follow RN1 flat. No auto take-profit."""
        copy_keys = self._pnl.open_copy_keys()
        # Also treat any pos: key we previously copied as eligible.
        for k in list(self._seen):
            if k.startswith("pos:") and k.count(":") == 2:
                _, cond, idx = k.split(":", 2)
                side = side_from_outcome_index(idx)
                if side:
                    copy_keys.add(f"{cond}:{side}")

        candidates = []
        for p in pos_list:
            if not isinstance(p, dict):
                continue
            size = float(p.get("size") or 0)
            if size < 0.5:
                continue
            cond = str(p.get("condition_id") or p.get("conditionId") or "").lower()
            side = str(p.get("side") or "").lower()
            if not cond or side not in ("yes", "no"):
                continue
            title = str(p.get("title") or "")
            hold_key = f"{cond}:{side}"
            idx = "0" if side == "yes" else "1"
            is_copy = (
                hold_key in copy_keys
                or f"pos:{cond}:{idx}" in self._seen
            )
            if not is_copy:
                continue
            entry = float(p.get("avg_price") or p.get("avgPrice") or 0)
            cur = float(p.get("current_price") or p.get("curPrice") or 0)
            candidates.append(
                {
                    "condition_id": cond,
                    "side": side,
                    "shares": max(1, int(size)),
                    "entry": entry,
                    "cur": cur,
                    "title": title,
                    "neg_risk": bool(p.get("negative_risk") or p.get("negRisk")),
                    "redeemable": bool(p.get("redeemable")),
                    "hold_key": hold_key,
                }
            )

        if not candidates:
            print("   Exits: no copy positions to manage", flush=True)
            return

        print(
            f"   Exits: watching {len(candidates)} positions | "
            f"SL={COPY_STOP_LOSS_PCT*100:.0f}% | follow_exit={COPY_FOLLOW_RN1_EXIT}",
            flush=True,
        )

        now_ts = time.time()
        for c in candidates:
            hold_key = c["hold_key"]
            # Redeemable always attempted (don't wait for SL / RN1 flat).
            if c.get("redeemable"):
                reason = "redeemable"
            else:
                cool_until = self._exit_fail_until.get(hold_key) or 0
                if cool_until > now_ts:
                    rejects["exit_cooldown"] += 1
                    continue
                reason = ""
                entry = c["entry"]
                cur = c["cur"]
                if entry > 0 and cur > 0 and COPY_STOP_LOSS_PCT > 0:
                    if cur <= entry * (1.0 - COPY_STOP_LOSS_PCT) + 1e-9:
                        reason = (
                            f"stop_loss cur={cur:.3f} ≤ entry={entry:.3f}"
                            f"×{1-COPY_STOP_LOSS_PCT:.2f}"
                        )
                        stats["stop_losses"] += 1
                if not reason and COPY_FOLLOW_RN1_EXIT:
                    if hold_key not in rn1_held:
                        reason = "rn1_exited"
                        stats["rn1_follows_exit"] += 1
                if not reason:
                    continue

            ok = await self._sell_one(
                condition_id=c["condition_id"],
                side=c["side"],
                shares=c["shares"],
                entry_price=c["entry"],
                mark_price=c["cur"] if c["cur"] > 0 else c["entry"],
                title=c["title"],
                neg_risk=c["neg_risk"],
                redeemable=bool(c.get("redeemable")),
                reason=reason,
                stats=stats,
                rejects=rejects,
            )
            if ok:
                stats["exits"] += 1
                self._exit_fail_until.pop(hold_key, None)

    async def _sell_one(
        self,
        *,
        condition_id: str,
        side: str,
        shares: int,
        entry_price: float,
        mark_price: float,
        title: str,
        neg_risk: bool,
        redeemable: bool,
        reason: str,
        stats: Dict[str, Any],
        rejects: Counter,
    ) -> bool:
        hold_key = f"{condition_id.lower()}:{side}"
        exit_px = mark_price if mark_price > 0 else max(0.01, entry_price * 0.5)

        # Resolved markets: redeem instead of spamming sells into a dead book.
        if redeemable or reason == "redeemable":
            print(
                f"  EXIT[redeem] {side.upper()} x{shares} "
                f"(entry ${entry_price:.2f}) | {title[:55]}",
                flush=True,
            )
            if self.dry_run:
                self._pnl.mark_closed(
                    condition_id, side,
                    exit_price=1.0 if exit_px >= 0.95 else 0.0,
                    shares=shares, reason="redeem",
                )
                return True
            if not hasattr(self.client, "redeem_condition"):
                rejects["exit_error"] += 1
                print(f"  EXIT FAIL {condition_id[:12]}: redeem_condition unavailable", flush=True)
                return False
            try:
                await self.client.redeem_condition(condition_id, neg_risk=neg_risk)
                # Winning redeem ≈ $1/share; losing ≈ $0. Use mark when known.
                redeem_px = 1.0 if exit_px >= 0.95 else (exit_px if exit_px > 0 else 0.0)
                self._pnl.mark_closed(
                    condition_id, side,
                    exit_price=redeem_px, shares=shares, reason="redeem",
                )
                notify_sports_exit(
                    title=title, side=side, shares=shares,
                    entry_price=entry_price, exit_price=redeem_px,
                    reason="redeem", live=True, condition_id=condition_id,
                )
                return True
            except Exception as exc:
                stats["errors"] += 1
                rejects["exit_error"] += 1
                self._exit_fail_until[hold_key] = time.time() + COPY_EXIT_FAIL_COOLDOWN_S
                print(f"  EXIT FAIL {condition_id[:12]}: redeem {exc}", flush=True)
                return False

        print(
            f"  EXIT[{reason}] SELL {side.upper()} x{shares} @ ~${exit_px:.2f} "
            f"(entry ${entry_price:.2f}) | {title[:55]}",
            flush=True,
        )
        if self.dry_run:
            self._pnl.mark_closed(
                condition_id, side,
                exit_price=exit_px, shares=shares, reason=reason,
            )
            notify_sports_exit(
                title=title, side=side, shares=shares,
                entry_price=entry_price, exit_price=exit_px,
                reason=reason, live=False, condition_id=condition_id,
            )
            return True

        try:
            await self._ensure_registered(condition_id, neg_risk=neg_risk)
            # Market sell for urgency (FOK shares).
            await self.client.place_order(
                ticker=condition_id,
                client_order_id=str(uuid.uuid4()),
                side=side,
                action="sell",
                count=int(shares),
                type_="market",
            )
            self._pnl.mark_closed(
                condition_id, side,
                exit_price=exit_px, shares=shares, reason=reason,
            )
            notify_sports_exit(
                title=title, side=side, shares=shares,
                entry_price=entry_price, exit_price=exit_px,
                reason=reason, live=True, condition_id=condition_id,
            )
            return True
        except Exception as exc:
            err_l = str(exc).lower()
            # Dead book → try redeem once instead of limit-spam.
            if any(
                s in err_l
                for s in ("no orderbook", "invalid token", "404", "does not exist")
            ):
                if hasattr(self.client, "redeem_condition"):
                    try:
                        print(
                            f"  EXIT fallback redeem {condition_id[:12]}… "
                            f"(no orderbook)",
                            flush=True,
                        )
                        await self.client.redeem_condition(
                            condition_id, neg_risk=neg_risk,
                        )
                        redeem_px = 0.0
                        self._pnl.mark_closed(
                            condition_id, side,
                            exit_price=redeem_px, shares=shares,
                            reason=f"{reason}+redeem",
                        )
                        notify_sports_exit(
                            title=title, side=side, shares=shares,
                            entry_price=entry_price, exit_price=redeem_px,
                            reason=f"{reason}+redeem", live=True,
                            condition_id=condition_id,
                        )
                        return True
                    except Exception as exc_r:
                        stats["errors"] += 1
                        rejects["exit_error"] += 1
                        self._exit_fail_until[hold_key] = (
                            time.time() + COPY_EXIT_FAIL_COOLDOWN_S
                        )
                        print(
                            f"  EXIT FAIL {condition_id[:12]}: {exc} / redeem {exc_r}",
                            flush=True,
                        )
                        return False
            # Fallback: limit sell at mark (or 1¢ floor).
            try:
                price_cents = max(1, min(99, int(round(exit_px * 100))))
                kwargs: Dict[str, Any] = {
                    "ticker": condition_id,
                    "client_order_id": str(uuid.uuid4()),
                    "side": side,
                    "action": "sell",
                    "count": int(shares),
                    "type_": "limit",
                }
                if side == "yes":
                    kwargs["yes_price"] = price_cents
                else:
                    kwargs["no_price"] = price_cents
                await self.client.place_order(**kwargs)
                self._pnl.mark_closed(
                    condition_id, side,
                    exit_price=exit_px, shares=shares, reason=reason,
                )
                notify_sports_exit(
                    title=title, side=side, shares=shares,
                    entry_price=entry_price, exit_price=exit_px,
                    reason=f"{reason}+limit", live=True, condition_id=condition_id,
                )
                return True
            except Exception as exc2:
                stats["errors"] += 1
                rejects["exit_error"] += 1
                self._exit_fail_until[hold_key] = time.time() + COPY_EXIT_FAIL_COOLDOWN_S
                print(f"  EXIT FAIL {condition_id[:12]}: {exc} / {exc2}", flush=True)
                return False

    async def _ensure_registered(self, condition_id: str, *, neg_risk: bool = False) -> None:
        if not hasattr(self.client, "register_market"):
            return
        try:
            yes_id, no_id = await self.gamma.get_token_ids(condition_id)
        except Exception as exc:
            raise RuntimeError(
                f"Gamma token resolve failed for {condition_id[:16]}: {exc}"
            ) from exc
        # Prefer Gamma market meta for neg_risk when available.
        nr = neg_risk
        try:
            m = await self.gamma.get_market(condition_id)
            nr = bool(m.get("negRisk") or m.get("negativeRisk") or neg_risk)
            tick = float(m.get("orderPriceMinTickSize") or m.get("minimum_tick_size") or 0.01)
        except Exception:
            tick = 0.01
        self.client.register_market(
            condition_id, yes_id, no_id, neg_risk=nr, tick_size=tick,
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
                **{k: v for k, v in stats.items() if k != "signals" or len(v) <= 30},
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


def _team_from_title(title: str) -> str:
    t = (title or "").strip()
    if t.lower().startswith("will ") and " win on " in t.lower():
        mid = t[5:]
        idx = mid.lower().find(" win on ")
        if idx > 0:
            return mid[:idx].strip()
    return t[:40]
