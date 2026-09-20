"""Lottery sleeve: buy cheapest YES longshots on soccer (prefer EU5 names).

Entertainment only — no fair-value filter. Isolated week budget + day cap.
CLOB minimum ticket each fill. Does NOT share RN1/EU5 Guard.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.strategies.capital_policy import CN_TZ, DailyEntryLog
from src.strategies.lottery.config import (
    DEFAULT_LEDGER,
    DEFAULT_PNL_PATH,
    DEFAULT_SCAN_LOG,
    DEFAULT_STATE,
    HARD_MAX_USDC,
    MAX_DAYS_AHEAD,
    MAX_ENTRIES_PER_DAY,
    MAX_SCAN_CYCLES,
    MIN_MINUTES_BEFORE,
    MIN_NOTIONAL,
    MIN_SHARES,
    PRICE_MAX,
    PRICE_MIN,
    WEEK_BUDGET_USDC,
)
from src.strategies.lottery.discover import LotteryTicket, fetch_lottery_tickets
from src.strategies.safe_compounder import nav_cents
from src.strategies.sports.sports_alerts import notify_sports_order
from src.strategies.sports.sports_pnl import SportsPnL

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


def ticket_shares(price: float) -> int:
    """Minimum CLOB ticket: ≥5 shares and ≥$1.01 notional, hard-capped."""
    if price <= 0:
        return 0
    shares = max(MIN_SHARES, int(ceil(MIN_NOTIONAL / price - 1e-12)))
    if shares * price > HARD_MAX_USDC + 1e-9:
        return 0
    return shares


def _best_ask(rows) -> Optional[float]:
    if not rows:
        return None
    try:
        row = rows[0]
        p = float(row.get("price") if isinstance(row, dict) else row[0])
        return p / 100.0 if p > 1.0 else p
    except (TypeError, ValueError, IndexError, KeyError):
        return None


class LotteryState:
    """Week spend tracker (reset on new ISO week starting Monday CN)."""

    def __init__(self, path: Path = DEFAULT_STATE):
        self.path = Path(path)

    def _week_id(self) -> str:
        now = datetime.now(CN_TZ)
        return f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    def _load(self) -> Dict[str, Any]:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
        except (OSError, json.JSONDecodeError):
            pass
        return {"week_id": self._week_id(), "spent_usdc": 0.0}

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def week_spent(self) -> float:
        data = self._load()
        wid = self._week_id()
        if data.get("week_id") != wid:
            data = {"week_id": wid, "spent_usdc": 0.0}
            self._save(data)
        return float(data.get("spent_usdc") or 0.0)

    def add_spent(self, usdc: float) -> float:
        data = self._load()
        wid = self._week_id()
        if data.get("week_id") != wid:
            data = {"week_id": wid, "spent_usdc": 0.0}
        data["spent_usdc"] = float(data.get("spent_usdc") or 0.0) + float(usdc)
        self._save(data)
        return float(data["spent_usdc"])


class LotterySleeve:
    def __init__(
        self,
        client,
        gamma,
        dry_run: bool = True,
        pnl: Optional[SportsPnL] = None,
        entry_log: Optional[DailyEntryLog] = None,
        state: Optional[LotteryState] = None,
        scan_log_path: Path = DEFAULT_SCAN_LOG,
    ):
        self.client = client
        self.gamma = gamma
        self.dry_run = dry_run
        self.pnl = pnl or SportsPnL(DEFAULT_PNL_PATH)
        self._entries = entry_log or DailyEntryLog(
            path=DEFAULT_LEDGER, limit=MAX_ENTRIES_PER_DAY,
        )
        self.state = state or LotteryState()
        self.scan_log_path = Path(scan_log_path)

    async def run(self, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        if dry_run is not None:
            self.dry_run = dry_run

        t0 = time.time()
        spent = self.state.week_spent()
        remaining_budget = WEEK_BUDGET_USDC - spent
        stats: Dict[str, Any] = {
            "mode": "lottery",
            "live": not self.dry_run,
            "week_spent": spent,
            "week_budget": WEEK_BUDGET_USDC,
            "markets": 0,
            "lottery_candidates": 0,
            "books": 0,
            "signals": 0,
            "attempted": 0,
            "placed": 0,
            "errors": 0,
            "rejects": {},
            "signals_detail": [],
        }
        rejects: Counter = Counter()

        print("\n🎰 LOTTERY SLEEVE — small longshots (entertainment)", flush=True)
        print(
            f"   Band {PRICE_MIN}–{PRICE_MAX} | min ticket ≥${MIN_NOTIONAL:.2f} | "
            f"≤{MAX_ENTRIES_PER_DAY}/day | week ${spent:.2f}/${WEEK_BUDGET_USDC:.2f} | "
            f"{'DRY RUN' if self.dry_run else 'LIVE'}",
            flush=True,
        )

        if remaining_budget < MIN_NOTIONAL:
            print(
                f"   🛑 Week budget exhausted (${spent:.2f}/${WEEK_BUDGET_USDC:.2f})",
                flush=True,
            )
            stats["rejects"] = {"week_budget": 1}
            self._finish(stats, rejects, t0)
            return stats

        bal = await self.client.get_balance()
        _, _, nav = nav_cents(bal)
        stats["nav_cents"] = nav

        tickets = await fetch_lottery_tickets(
            self.gamma,
            max_days=MAX_DAYS_AHEAD,
            min_minutes=MIN_MINUTES_BEFORE,
        )
        stats["markets"] = len(tickets)
        stats["lottery_candidates"] = len(tickets)

        held = self._held_keys()
        day_left = self._entries.remaining()

        for t in tickets:
            if day_left <= 0:
                rejects["daily_cap"] += 1
                break
            if remaining_budget < MIN_NOTIONAL:
                rejects["week_budget"] += 1
                break

            key = f"{t.condition_id.lower()}:yes"
            if key in held:
                rejects["already_position"] += 1
                continue

            try:
                if hasattr(self.client, "register_market"):
                    self.client.register_market(
                        t.condition_id, t.yes_token, t.no_token,
                        neg_risk=t.neg_risk, tick_size=t.tick_size,
                    )
                ob_resp = await self.client.get_orderbook(t.condition_id, depth=3)
                ob = ob_resp.get("orderbook", {}) or {}
            except Exception as exc:
                logger.info("lottery book fail %s: %s", t.condition_id[:12], exc)
                rejects["book_error"] += 1
                continue
            stats["books"] += 1

            yes_ask = _best_ask(ob.get("yes_asks") or [])
            if yes_ask is None:
                rejects["missing_ask"] += 1
                continue
            if not (PRICE_MIN <= yes_ask <= PRICE_MAX):
                rejects["price_band_ask"] += 1
                continue

            shares = ticket_shares(yes_ask)
            if shares < 1:
                rejects["size_zero"] += 1
                continue
            cost = shares * yes_ask
            if cost > remaining_budget + 1e-9:
                rejects["week_budget"] += 1
                continue

            stats["signals"] += 1
            stats["signals_detail"].append({
                "label": t.label,
                "kind": t.kind,
                "ask": round(yes_ask, 4),
                "shares": shares,
                "cost": round(cost, 4),
                "question": t.question[:80],
            })
            print(
                f"  🎰 YES [{t.kind}] {t.label} @ {yes_ask:.3f} x{shares} "
                f"(${cost:.2f}) | {t.question[:50]}",
                flush=True,
            )

            stats["attempted"] += 1
            ok = await self._enter(t, yes_ask, shares, cost)
            if ok:
                stats["placed"] += 1
                day_left -= 1
                remaining_budget -= cost
                held.add(key)
            else:
                stats["errors"] += 1

        self._finish(stats, rejects, t0)
        return stats

    def _held_keys(self) -> Set[str]:
        keys: Set[str] = set()
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
        t: LotteryTicket,
        price: float,
        shares: int,
        cost: float,
    ) -> bool:
        price_cents = max(1, min(99, int(round(price * 100))))
        match_date = ""
        if t.end_ts:
            try:
                match_date = datetime.utcfromtimestamp(float(t.end_ts)).date().isoformat()
            except (TypeError, ValueError, OSError):
                match_date = trading_day_safe()

        if self.dry_run:
            # Do not touch live ledgers / week budget — dry must not block LIVE.
            print(f"  [DRY] Would FOK YES x{shares} @ {price:.3f} (${cost:.2f})", flush=True)
            return True

        try:
            resp = await self.client.place_order(
                ticker=t.condition_id,
                client_order_id=str(uuid.uuid4()),
                side="yes",
                action="buy",
                count=shares,
                type_="market",
                yes_price=price_cents,
            )
        except Exception as exc:
            print(f"  ❌ lottery order failed: {str(exc)[:160]}", flush=True)
            return False

        order = (resp or {}).get("order") or {}
        if not order.get("order_id") and not int(order.get("fill_count") or 0):
            print(f"  ❌ lottery no fill: {str(resp)[:160]}", flush=True)
            return False

        self.pnl.record_entry(
            condition_id=t.condition_id,
            title=t.question,
            team=t.label,
            match_date=match_date,
            shares=shares,
            price=price,
            fair_prob=price,
            edge=0.0,
            sport=f"lottery:{t.kind}",
            match=t.question,
            live=True,
            side="yes",
        )
        self._entries.record(t.condition_id, t.question, kind=f"lottery:{t.kind}")
        self.state.add_spent(cost)
        notify_sports_order(
            team=t.label,
            match=t.question,
            shares=shares,
            price=price,
            edge=0.0,
            fair_prob=price,
            live=True,
            condition_id=t.condition_id,
            rn1_reason=f"lottery_{t.kind}",
        )
        print(f"  ✅ lottery filled YES x{shares} @ {price:.3f} (${cost:.2f})", flush=True)
        return True

    def _finish(self, stats: Dict[str, Any], rejects: Counter, t0: float) -> None:
        stats["rejects"] = dict(rejects)
        stats["elapsed_s"] = round(time.time() - t0, 1)
        stats["week_spent"] = self.state.week_spent()
        self._append_scan_log(stats)
        print(
            f"   lottery done: cands={stats['lottery_candidates']} "
            f"signals={stats['signals']} placed={stats['placed']} "
            f"week=${stats['week_spent']:.2f}/{WEEK_BUDGET_USDC:.2f} "
            f"({stats['elapsed_s']}s)",
            flush=True,
        )
        rej_txt = " ".join(f"{k}={v}" for k, v in sorted(rejects.items()) if v)
        if rej_txt:
            print(f"   Rejects: {rej_txt}", flush=True)

    def _append_scan_log(self, stats: Dict[str, Any]) -> None:
        entry = {"ts": _now_iso(), **stats}
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
            logger.warning("lottery scan log write failed: %s", exc)


def trading_day_safe() -> str:
    from src.strategies.capital_policy import trading_day
    return trading_day()
