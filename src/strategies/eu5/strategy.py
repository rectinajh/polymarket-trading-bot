"""EU5 fair-value strategy — Pinnacle-referenced top-5 league value betting.

Core idea: Pinnacle h2h odds (de-vigged) = fair probability. Polymarket
soccer books are thin and retail-priced, so PM ask can sit below fair by
several cents. Buy YES when ask <= fair - MIN_EDGE; buy NO (team fails to
win = draw or loss) when no_ask <= (1 - fair) - MIN_EDGE.

Isolation: own PnL ledger / daily entry log / scan stats. Shares the sports
Guard (daily loss / drawdown) via a separate SportsPnL path.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.clients.odds_api_client import (
    OddsAPIClient,
    OddsAPIError,
    ReferenceEvent,
    ReferenceOutcome,
)
from src.strategies.capital_policy import DailyEntryLog
from src.strategies.eu5.config import (
    DEFAULT_LEDGER,
    DEFAULT_PNL_PATH,
    DEFAULT_SCAN_LOG,
    EU5_SPORT_KEYS,
    HARD_MAX_USDC,
    MAX_DAYS_AHEAD,
    MAX_ENTRIES_PER_DAY,
    MAX_SCAN_CYCLES,
    MIN_EDGE,
    MIN_MINUTES_BEFORE_KICKOFF,
    MIN_SHARES,
    PRICE_MAX,
    PRICE_MIN,
    REF_TTL_HOURS,
    REFERENCE_CACHE,
    STAKE_USDC,
)
from src.strategies.safe_compounder import nav_cents
from src.strategies.sports.discover import (
    MatchWinnerMarket,
    fetch_match_winner_markets,
)
from src.strategies.sports.match import match_market_to_reference
from src.strategies.sports.sports_alerts import notify_sports_halt, notify_sports_order
from src.strategies.sports.sports_guard import check_trading_allowed
from src.strategies.sports.sports_pnl import SportsPnL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- reference cache

def _serialize_events(events: List[ReferenceEvent]) -> List[Dict[str, Any]]:
    out = []
    for ev in events:
        out.append({
            "event_id": ev.event_id,
            "sport_key": ev.sport_key,
            "commence_time": ev.commence_time.isoformat(),
            "home_team": ev.home_team,
            "away_team": ev.away_team,
            "outcomes": {
                name: {
                    "team": o.team,
                    "decimal_odds": o.decimal_odds,
                    "fair_prob": o.fair_prob,
                }
                for name, o in ev.outcomes.items()
            },
        })
    return out


def _deserialize_events(rows: List[Dict[str, Any]]) -> List[ReferenceEvent]:
    out: List[ReferenceEvent] = []
    for r in rows or []:
        try:
            commence = datetime.fromisoformat(str(r["commence_time"]))
            if commence.tzinfo is None:
                commence = commence.replace(tzinfo=timezone.utc)
            outcomes = {
                name: ReferenceOutcome(
                    team=str(o.get("team") or name),
                    decimal_odds=float(o.get("decimal_odds") or 0),
                    fair_prob=float(o.get("fair_prob") or 0),
                )
                for name, o in (r.get("outcomes") or {}).items()
            }
            out.append(ReferenceEvent(
                event_id=str(r.get("event_id") or ""),
                sport_key=str(r.get("sport_key") or ""),
                commence_time=commence,
                home_team=str(r.get("home_team") or ""),
                away_team=str(r.get("away_team") or ""),
                outcomes=outcomes,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def load_reference(
    odds: OddsAPIClient,
    *,
    cache_path: Path = REFERENCE_CACHE,
    ttl_hours: float = REF_TTL_HOURS,
) -> Tuple[List[ReferenceEvent], str]:
    """Return (events, source). Refresh from API when cache is stale."""
    now = time.time()
    try:
        if cache_path.exists():
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            ts = float(raw.get("ts") or 0)
            if now - ts < ttl_hours * 3600:
                return _deserialize_events(raw.get("events") or []), "cache"
    except (OSError, ValueError):
        pass

    events = await odds.fetch_eu_soccer_reference(list(EU5_SPORT_KEYS))
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "ts": now,
            "events": _serialize_events(events),
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache_path)
    except OSError as exc:
        logger.warning("EU5 reference cache write failed: %s", exc)
    return events, "api"


# ---------------------------------------------------------------- sizing / signals

def value_shares(price: float, *, stake: float = STAKE_USDC,
                 min_shares: int = MIN_SHARES,
                 hard_max: float = HARD_MAX_USDC) -> int:
    """Shares for a value entry: CLOB floor, ~stake notional, hard cap."""
    if price <= 0:
        return 0
    shares = max(min_shares, int(ceil(stake / price - 1e-12)))
    if shares * price > hard_max + 1e-9:
        return 0
    return shares


def evaluate_value(
    market: MatchWinnerMarket,
    fair_prob: float,
    *,
    yes_ask: Optional[float],
    no_ask: Optional[float],
    min_edge: float = MIN_EDGE,
    price_min: float = PRICE_MIN,
    price_max: float = PRICE_MAX,
) -> Optional[Dict[str, Any]]:
    """Return a value signal dict, or None.

    YES side: team wins. NO side: team fails to win (draw/loss) — fair NO
    prob is 1 - fair_prob.
    """
    candidates: List[Dict[str, Any]] = []
    if yes_ask is not None and price_min <= yes_ask <= price_max:
        edge = fair_prob - yes_ask
        if edge >= min_edge:
            candidates.append({
                "side": "yes", "price": yes_ask, "fair": fair_prob, "edge": edge,
            })
    if no_ask is not None and price_min <= no_ask <= price_max:
        fair_no = 1.0 - fair_prob
        edge = fair_no - no_ask
        if edge >= min_edge:
            candidates.append({
                "side": "no", "price": no_ask, "fair": fair_no, "edge": edge,
            })
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["edge"])


def _best_ask(rows) -> Optional[float]:
    if not rows:
        return None
    try:
        row = rows[0]
        p = float(row.get("price") if isinstance(row, dict) else row[0])
        return p / 100.0 if p > 1.0 else p
    except (TypeError, ValueError, IndexError, KeyError):
        return None


# ---------------------------------------------------------------- strategy

class Eu5FairValue:
    """Top-5 EU league value sleeve (Pinnacle reference vs PM price)."""

    def __init__(
        self,
        client,
        gamma,
        odds: Optional[OddsAPIClient] = None,
        dry_run: bool = True,
        min_edge: float = MIN_EDGE,
        pnl: Optional[SportsPnL] = None,
        entry_log: Optional[DailyEntryLog] = None,
        scan_log_path: Path = DEFAULT_SCAN_LOG,
    ):
        self.client = client
        self.gamma = gamma
        self.odds = odds or OddsAPIClient()
        self._owns_odds = odds is None
        self.dry_run = dry_run
        self.min_edge = min_edge
        self.pnl = pnl or SportsPnL(DEFAULT_PNL_PATH)
        self._entries = entry_log or DailyEntryLog(
            path=DEFAULT_LEDGER, limit=MAX_ENTRIES_PER_DAY,
        )
        self.scan_log_path = Path(scan_log_path)
        self._halt_notified = False

    async def run(self, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        if dry_run is not None:
            self.dry_run = dry_run

        t0 = time.time()
        stats: Dict[str, Any] = {
            "mode": "eu5_fair_value",
            "live": not self.dry_run,
            "markets": 0,
            "matched": 0,
            "books": 0,
            "signals": 0,
            "attempted": 0,
            "placed": 0,
            "errors": 0,
            "guard_halted": 0,
            "guard_reason": "",
            "ref_source": "",
            "ref_events": 0,
            "rejects": {},
            "signals_detail": [],
        }
        rejects: Counter = Counter()

        print("\n⚽ EU5 FAIR VALUE — Pinnacle ref vs PM (top-5 leagues)", flush=True)
        print(
            f"   Rules: edge ≥ ${self.min_edge:.2f} | band {PRICE_MIN}–{PRICE_MAX} | "
            f"stake ~${STAKE_USDC:.2f} | ≤{MAX_ENTRIES_PER_DAY}/day | "
            f"{'DRY RUN' if self.dry_run else 'LIVE'}",
            flush=True,
        )

        bal = await self.client.get_balance()
        _, _, nav = nav_cents(bal)

        # Guard first (shared sports guardrails, own ledger).
        allowed, guard_reason, _meta = check_trading_allowed(
            nav_cents=nav, pnl=self.pnl,
        )
        if not allowed:
            stats["guard_halted"] = 1
            stats["guard_reason"] = guard_reason
            print(f"   ⛔ Guard halt: {guard_reason}", flush=True)
            if not self._halt_notified:
                notify_sports_halt(f"EU5: {guard_reason}", _meta)
                self._halt_notified = True
            self._finish(stats, rejects, t0, nav)
            return stats
        self._halt_notified = False

        # Reference odds (cached; free-tier friendly).
        try:
            events, source = await load_reference(self.odds)
        except OddsAPIError as exc:
            print(f"   ⚠️ reference unavailable: {exc}", flush=True)
            stats["guard_reason"] = f"no_reference: {str(exc)[:120]}"
            self._finish(stats, rejects, t0, nav)
            return stats
        stats["ref_source"] = source
        stats["ref_events"] = len(events)
        print(f"   Reference: {len(events)} events ({source})", flush=True)
        if not events:
            stats["guard_reason"] = "no_reference_events"
            self._finish(stats, rejects, t0, nav)
            return stats

        # PM discovery.
        markets = await fetch_match_winner_markets(
            self.gamma,
            max_days=MAX_DAYS_AHEAD,
            min_minutes=MIN_MINUTES_BEFORE_KICKOFF,
        )
        stats["markets"] = len(markets)

        held = self._held_keys()
        remaining = self._entries.remaining()

        for m in markets:
            ref = match_market_to_reference(m, events)
            if not ref:
                rejects["no_match"] += 1
                continue
            stats["matched"] += 1

            try:
                ob_resp = await self.client.get_orderbook(m.condition_id, depth=3)
                ob = ob_resp.get("orderbook", {}) or {}
            except Exception as exc:
                logger.info("EU5 book fail %s: %s", m.condition_id[:12], exc)
                rejects["book_error"] += 1
                continue
            stats["books"] += 1

            sig = evaluate_value(
                m, ref.fair_prob,
                yes_ask=_best_ask(ob.get("yes_asks") or []),
                no_ask=_best_ask(ob.get("no_asks") or []),
                min_edge=self.min_edge,
            )
            if not sig:
                rejects["no_edge"] += 1
                continue

            key = f"{m.condition_id.lower()}:{sig['side']}"
            if key in held:
                rejects["already_position"] += 1
                continue

            shares = value_shares(sig["price"])
            if shares < 1:
                rejects["size_zero"] += 1
                continue

            stats["signals"] += 1
            stats["signals_detail"].append({
                "team": m.team, "date": str(m.match_date),
                "side": sig["side"], "price": sig["price"],
                "fair": round(sig["fair"], 4), "edge": round(sig["edge"], 4),
                "shares": shares,
            })
            print(
                f"  VALUE {sig['side'].upper()} {m.team} @ {sig['price']:.2f} "
                f"(fair {sig['fair']:.2f}, edge {sig['edge']:.3f}) x{shares}",
                flush=True,
            )

            if remaining <= 0:
                rejects["daily_cap"] += 1
                continue

            stats["attempted"] += 1
            ok = await self._enter(m, sig, shares)
            if ok:
                stats["placed"] += 1
                remaining -= 1
                held.add(key)
            else:
                stats["errors"] += 1

        self._finish(stats, rejects, t0, nav)
        return stats

    # ------------------------------------------------------------ internals

    def _held_keys(self) -> set:
        keys = set()
        for e in self.pnl.recent_entries(limit=300):
            if e.get("status") != "open":
                continue
            cond = str(e.get("condition_id") or "").lower()
            side = str(e.get("side") or "yes").lower()
            if cond:
                keys.add(f"{cond}:{side}")
        return keys

    async def _enter(
        self,
        m: MatchWinnerMarket,
        sig: Dict[str, Any],
        shares: int,
    ) -> bool:
        side = sig["side"]
        price = float(sig["price"])
        price_cents = max(1, min(99, int(round(price * 100))))

        if self.dry_run:
            print(f"  [DRY] Would FOK {side.upper()} x{shares} @ {price:.2f}", flush=True)
            self.pnl.record_entry(
                condition_id=m.condition_id,
                title=m.question,
                team=m.team,
                match_date=str(m.match_date),
                shares=shares,
                price=price,
                fair_prob=float(sig["fair"]),
                edge=float(sig["edge"]),
                sport="eu5",
                match=m.question,
                live=False,
                side=side,
            )
            self._entries.record(m.condition_id, m.question, kind=f"eu5:{side}")
            return True

        if hasattr(self.client, "register_market"):
            self.client.register_market(
                m.condition_id, m.yes_token, m.no_token,
                neg_risk=m.neg_risk, tick_size=m.tick_size,
            )
        try:
            resp = await self.client.place_order(
                ticker=m.condition_id,
                client_order_id=str(uuid.uuid4()),
                side=side,
                action="buy",
                count=shares,
                type_="market",
                yes_price=price_cents if side == "yes" else None,
                no_price=price_cents if side == "no" else None,
            )
        except Exception as exc:
            print(f"  ❌ EU5 order failed: {str(exc)[:160]}", flush=True)
            return False

        order = (resp or {}).get("order") or {}
        if not order.get("order_id") and not int(order.get("fill_count") or 0):
            print(f"  ❌ EU5 no fill: {str(resp)[:160]}", flush=True)
            return False

        self.pnl.record_entry(
            condition_id=m.condition_id,
            title=m.question,
            team=m.team,
            match_date=str(m.match_date),
            shares=shares,
            price=price,
            fair_prob=float(sig["fair"]),
            edge=float(sig["edge"]),
            sport="eu5",
            match=m.question,
            live=True,
            side=side,
        )
        self._entries.record(m.condition_id, m.question, kind=f"eu5:{side}")
        notify_sports_order(
            team=m.team,
            match=m.question,
            shares=shares,
            price=price,
            edge=float(sig["edge"]),
            fair_prob=float(sig["fair"]),
            live=True,
            condition_id=m.condition_id,
            rn1_reason="eu5_value",
        )
        print(f"  ✅ EU5 filled {side.upper()} x{shares} @ {price:.2f}", flush=True)
        return True

    def _finish(
        self,
        stats: Dict[str, Any],
        rejects: Counter,
        t0: float,
        nav: int,
    ) -> None:
        stats["rejects"] = dict(rejects)
        stats["elapsed_s"] = round(time.time() - t0, 1)
        stats["nav_cents"] = nav
        self._append_scan_log(stats)
        print(
            f"   EU5 done: markets={stats['markets']} matched={stats['matched']} "
            f"signals={stats['signals']} placed={stats['placed']} "
            f"({stats['elapsed_s']}s)",
            flush=True,
        )
        rej_txt = " ".join(f"{k}={v}" for k, v in sorted(rejects.items()) if v)
        if rej_txt:
            print(f"   Rejects: {rej_txt}", flush=True)
        if self._owns_odds:
            # Keep the client alive across loop cycles; closed by caller scope.
            pass

    def _append_scan_log(self, stats: Dict[str, Any]) -> None:
        entry = {"ts": datetime.now().astimezone().isoformat(), **stats}
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
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.scan_log_path)
        except OSError as exc:
            logger.warning("EU5 scan log write failed: %s", exc)
