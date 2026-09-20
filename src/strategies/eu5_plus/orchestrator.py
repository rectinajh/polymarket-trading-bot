"""EU5+ multi-strategy orchestrator (live-capable)."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from src.clients.odds_api_client import OddsAPIClient, OddsAPIError
from src.strategies.capital_policy import CN_TZ, DailyEntryLog
from src.strategies.csl_explore.executor import place_yes_buy, place_yes_sell
from src.strategies.csl_explore.ledger import ExploreLedger
from src.strategies.csl_explore.models import ExploreSignal, MatchBundle
from src.strategies.csl_explore.stops import stop_loss_reason, stop_loss_triggered
from src.strategies.eu5_plus.config import (
    DEFAULT_LEDGER,
    DEFAULT_SCAN_LOG,
    DEFAULT_STATE,
    ENABLED_STRATS,
    EXIT_FAIL_COOLDOWN_S,
    HARD_MAX_USDC,
    MAX_ENTRIES_PER_DAY,
    MAX_PER_MATCH_USDC,
    STOP_LOSS_PCT,
    WEEK_BUDGET_USDC,
)
from src.strategies.eu5_plus.discover import fetch_eu5_match_bundles
from src.strategies.eu5_plus.ref import load_reference, match_bundle_to_reference
from src.strategies.eu5_plus.strategies import STRATEGY_REGISTRY
from src.strategies.sports.sports_alerts import notify_sports_order


def _week_id() -> str:
    now = datetime.now(CN_TZ)
    return f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"


class Eu5PlusOrchestrator:
    """Run draw_fv / kickoff_lag / completeness / live_draw / line_cross / narrative."""

    def __init__(
        self,
        *,
        dry_run: bool = True,
        client: Any = None,
        gamma: Any = None,
        odds: Optional[OddsAPIClient] = None,
        enabled: Optional[Sequence[str]] = None,
        ledger: Optional[ExploreLedger] = None,
        entry_log: Optional[DailyEntryLog] = None,
        scan_log: Path = DEFAULT_SCAN_LOG,
    ) -> None:
        self.dry_run = dry_run
        self.client = client
        self.gamma = gamma
        self.odds = odds
        self.enabled = tuple(enabled or ENABLED_STRATS)
        self.ledger = ledger or ExploreLedger(
            ledger_path=DEFAULT_LEDGER, state_path=DEFAULT_STATE,
        )
        self._entries = entry_log or DailyEntryLog(
            path=Path("data") / "daily_entries_eu5_plus.json",
            limit=MAX_ENTRIES_PER_DAY,
        )
        self.scan_log = Path(scan_log)
        self._exit_fail_until: Dict[str, float] = {}
        self._strategies = []
        for sid in self.enabled:
            cls = STRATEGY_REGISTRY.get(sid)
            if cls is None:
                print(f"   [eu5_plus] unknown strategy id={sid}", flush=True)
                continue
            self._strategies.append(cls())

    def _ensure_week(self) -> float:
        st = self.ledger.load_state()
        wid = _week_id()
        if st.get("week_id") != wid:
            st["week_id"] = wid
            st["spent_usdc"] = 0.0
            # keep opens / filled_keys / mids
            self.ledger.save_state(st)
        return float(st.get("spent_usdc") or 0.0)

    def _filled_keys(self) -> Set[str]:
        st = self.ledger.load_state()
        return set(str(k) for k in (st.get("filled_keys") or []))

    def _mark_filled(self, *keys: str, usdc: float) -> None:
        st = self.ledger.load_state()
        st["week_id"] = _week_id()
        filled = list(st.get("filled_keys") or [])
        for key in keys:
            if key and key not in filled:
                filled.append(key)
        st["filled_keys"] = filled[-800:]
        st["spent_usdc"] = float(st.get("spent_usdc") or 0.0) + float(usdc)
        self.ledger.save_state(st)

    async def run(self) -> Dict[str, Any]:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        spent = self._ensure_week()
        print(f"⚽ EU5+ PARALLEL ({mode})", flush=True)
        print(
            f"   Strategies: {', '.join(s.id for s in self._strategies) or '(none)'}",
            flush=True,
        )
        print(
            f"   Week ${spent:.2f}/${WEEK_BUDGET_USDC:.2f} · "
            f"day left {self._entries.remaining()}/{MAX_ENTRIES_PER_DAY} · "
            f"SL={STOP_LOSS_PCT*100:.0f}%",
            flush=True,
        )

        # Reference odds (optional — draw_fv / kickoff_lag degrade gracefully).
        refs = []
        ref_source = "none"
        if self.odds is not None:
            try:
                refs, ref_source = await load_reference(self.odds)
            except OddsAPIError as exc:
                print(f"   Odds API unavailable: {exc}", flush=True)
                ref_source = "error"

        if self.gamma is None:
            raise RuntimeError("eu5_plus requires gamma client")

        matches = await fetch_eu5_match_bundles(self.gamma)
        print(f"   Loaded EU5 matches: {len(matches)} · ref={len(refs)} ({ref_source})", flush=True)

        ref_by_slug: Dict[str, Any] = {}
        for m in matches:
            ev = match_bundle_to_reference(m.title, m.start_ts, refs)
            if ev:
                ref_by_slug[m.slug] = ev

        marks: Dict[str, float] = {}
        for m in matches:
            for leg in list(m.totals) + list(m.exact_scores) + list(
                getattr(m, "spreads", None) or []
            ) + [m.home_win, m.away_win, m.draw]:
                if leg and leg.condition_id and leg.yes_price > 0:
                    marks[leg.condition_id.lower()] = float(leg.yes_price)
        if marks:
            self.ledger.record_mids(marks)

        marks = await self._enrich_marks(marks)
        sl_exits = await self._manage_stop_losses(marks)

        context = {
            "live": {},
            "ledger": self.ledger,
            "refs": refs,
            "ref_by_slug": ref_by_slug,
            "ref_source": ref_source,
        }
        all_signals: List[ExploreSignal] = []
        for match in matches:
            for strat in self._strategies:
                all_signals.extend(strat.generate(match, context=context))

        actionable = [s for s in all_signals if s.action.startswith("buy")]
        filled = self._filled_keys()
        fresh = [
            s for s in actionable
            if f"{s.strategy}:{s.condition_id}" not in filled
            and f"cond:{s.condition_id}" not in filled
        ]
        planned = fresh if self.dry_run else self._allocate(fresh)

        placed = 0
        errors = 0
        deployed = 0.0
        by_strategy: Dict[str, int] = {}
        for sig in planned:
            print(
                f"   → [{sig.strategy}] ${sig.usdc:.2f} "
                f"@{(sig.limit_price or 0):.3f} | {sig.reason[:70]}",
                flush=True,
            )
            ok, cost = await self._execute(sig)
            if ok:
                placed += 1
                deployed += cost
                by_strategy[sig.strategy] = by_strategy.get(sig.strategy, 0) + 1
            elif not self.dry_run:
                errors += 1

        buys = [s for s in all_signals if s.action.startswith("buy")]
        summary = {
            "mode": "eu5_plus",
            "live": not self.dry_run,
            "dry_run": self.dry_run,
            "matches": len(matches),
            "ref_events": len(refs),
            "ref_matched": len(ref_by_slug),
            "ref_source": ref_source,
            "signals_total": len(all_signals),
            "buy_signals": len(buys),
            "buy_planned": len(planned),
            "placed": placed,
            "stop_losses": sl_exits,
            "errors": errors,
            "deployed_usdc": round(deployed, 4),
            "week_spent": self._ensure_week(),
            "week_budget": WEEK_BUDGET_USDC,
            "day_remaining": self._entries.remaining(),
            "open_positions": len(self.ledger.open_positions()),
            "by_strategy": by_strategy,
            "planned": [s.to_dict() for s in planned[:40]],
            "enabled": list(self.enabled),
        }
        self._write_scan(summary)
        print(
            f"   eu5_plus done: matches={len(matches)} buys={len(buys)} "
            f"planned={len(planned)} placed={placed} SL={sl_exits} "
            f"week=${summary['week_spent']:.2f}/{WEEK_BUDGET_USDC:.2f} "
            f"by={by_strategy}",
            flush=True,
        )
        return summary

    def _allocate(self, signals: List[ExploreSignal]) -> List[ExploreSignal]:
        remaining = WEEK_BUDGET_USDC - self._ensure_week()
        day_left = self._entries.remaining()
        if remaining <= 0 or day_left <= 0:
            print("   Budget/day cap — no new orders.", flush=True)
            return []
        ordered = sorted(signals, key=lambda s: (s.priority, s.match_slug, s.strategy))
        per_match: Dict[str, float] = {}
        out: List[ExploreSignal] = []
        for sig in ordered:
            if day_left <= 0:
                break
            if sig.usdc <= 0 or not sig.condition_id:
                continue
            used = per_match.get(sig.match_slug, 0.0)
            room = min(remaining, MAX_PER_MATCH_USDC - used)
            if room < 0.01:
                continue
            take = min(sig.usdc, room)
            if take < 0.01:
                continue
            out.append(ExploreSignal(
                strategy=sig.strategy,
                match_slug=sig.match_slug,
                match_title=sig.match_title,
                action=sig.action,
                reason=sig.reason,
                usdc=round(take, 4),
                condition_id=sig.condition_id,
                question=sig.question,
                yes_token=sig.yes_token,
                limit_price=sig.limit_price,
                priority=sig.priority,
                meta=dict(sig.meta),
            ))
            per_match[sig.match_slug] = used + take
            remaining -= take
            day_left -= 1
            if remaining < 0.01:
                break
        return out

    async def _execute(self, sig: ExploreSignal) -> tuple[bool, float]:
        if self.dry_run:
            self.ledger.append({"kind": "plan", **sig.to_dict()})
            return True, 0.0
        px = sig.limit_price
        if self.client is None or px is None or px <= 0:
            self.ledger.append({"kind": "error", "error": "no_client_or_price", **sig.to_dict()})
            return False, 0.0
        try:
            # Cap notional: prefer fewer shares over blowing HARD_MAX.
            px_f = float(px)
            from math import ceil
            from src.strategies.eu5_plus.config import MIN_SHARES, ORDER_USDC
            want = max(MIN_SHARES, int(ceil(float(sig.usdc) / px_f - 1e-12)))
            while want > MIN_SHARES and want * px_f > HARD_MAX_USDC + 1e-9:
                want -= 1
            if want * px_f > HARD_MAX_USDC + 1e-9:
                self.ledger.append({
                    "kind": "error", "error": "hard_max", **sig.to_dict(),
                })
                print(
                    f"   skip hard_max {want}x@{px_f:.3f}=${want*px_f:.2f}",
                    flush=True,
                )
                return False, 0.0
            capped_usdc = min(float(sig.usdc), want * px_f)
            shares, fill_px, raw = await place_yes_buy(
                self.client, self.gamma,
                condition_id=sig.condition_id,
                price=px_f,
                usdc=max(ORDER_USDC * 0.5, min(capped_usdc, HARD_MAX_USDC)),
                yes_token=sig.yes_token or "",
            )
            cost = round(shares * fill_px, 4)
            if cost > HARD_MAX_USDC + 0.25:
                # Still oversized after executor floor — record but don't inflate week more than hard.
                print(f"   ⚠️ oversized fill ${cost:.2f} (hard ${HARD_MAX_USDC})", flush=True)
            self._mark_filled(
                f"{sig.strategy}:{sig.condition_id}",
                f"cond:{sig.condition_id}",
                usdc=cost,
            )
            self._entries.record(sig.condition_id, sig.question or sig.match_title,
                                 kind=f"eu5_plus:{sig.strategy}")
            self.ledger.append({
                "kind": "fill", "shares": shares, "fill_price": fill_px,
                "cost_usdc": cost, **sig.to_dict(),
            })
            self.ledger.record_open({
                "condition_id": sig.condition_id,
                "strategy": sig.strategy,
                "match_slug": sig.match_slug,
                "match_title": sig.match_title,
                "question": sig.question,
                "yes_token": sig.yes_token,
                "shares": shares,
                "entry_price": fill_px,
                "cost_usdc": cost,
            })
            print(f"   ✅ [{sig.strategy}] x{shares} @ ${fill_px:.2f} ≈ ${cost:.2f}", flush=True)
            notify_sports_order(
                team=sig.strategy,
                match=sig.match_title,
                shares=shares,
                price=fill_px,
                edge=float((sig.meta or {}).get("edge") or 0),
                fair_prob=float((sig.meta or {}).get("fair") or fill_px),
                live=True,
                condition_id=sig.condition_id,
                rn1_reason=f"eu5_plus_{sig.strategy}",
            )
            return True, cost
        except Exception as exc:
            self.ledger.append({"kind": "error", "error": str(exc)[:300], **sig.to_dict()})
            print(f"   ❌ [{sig.strategy}] {exc}", flush=True)
            return False, 0.0

    async def _enrich_marks(self, marks: Dict[str, float]) -> Dict[str, float]:
        out = dict(marks)
        if self.gamma is None:
            return out
        for pos in self.ledger.open_positions():
            cid = str(pos.get("condition_id") or "")
            if not cid or cid.lower() in out:
                continue
            try:
                m = await self.gamma.get_market(cid)
                prices = m.get("_outcome_prices") or ()
                if prices:
                    out[cid.lower()] = float(prices[0])
            except Exception:
                pass
        return out

    async def _manage_stop_losses(self, marks: Dict[str, float]) -> int:
        if STOP_LOSS_PCT <= 0:
            return 0
        opens = self.ledger.open_positions()
        exited = 0
        now = time.time()
        for pos in opens:
            cid = str(pos.get("condition_id") or "")
            if not cid:
                continue
            if (self._exit_fail_until.get(cid.lower()) or 0) > now:
                continue
            entry = float(pos.get("entry_price") or 0)
            mark = float(marks.get(cid.lower()) or 0)
            shares = int(pos.get("shares") or 0)
            if entry <= 0 or mark <= 0 or shares <= 0:
                continue
            if not stop_loss_triggered(entry, mark, stop_loss_pct=STOP_LOSS_PCT):
                continue
            reason = stop_loss_reason(entry, mark, STOP_LOSS_PCT)
            print(
                f"   SL HIT [{pos.get('strategy')}] mark={mark:.3f} entry={entry:.3f}",
                flush=True,
            )
            ok = await self._exit_one(pos, mark_price=mark, reason=reason)
            if ok:
                exited += 1
            else:
                self._exit_fail_until[cid.lower()] = now + EXIT_FAIL_COOLDOWN_S
        return exited

    async def _exit_one(self, pos: Dict[str, Any], *, mark_price: float, reason: str) -> bool:
        cid = str(pos.get("condition_id") or "")
        shares = int(pos.get("shares") or 0)
        if self.dry_run:
            self.ledger.append({
                "kind": "exit", "dry_run": True, "condition_id": cid,
                "strategy": pos.get("strategy"), "shares": shares,
                "entry_price": pos.get("entry_price"), "exit_price": mark_price,
                "reason": reason,
            })
            self.ledger.mark_closed(cid, exit_price=mark_price, reason=reason)
            return True
        if self.client is None:
            return False
        try:
            await place_yes_sell(
                self.client, self.gamma,
                condition_id=cid,
                price=float(mark_price),
                shares=shares,
                yes_token=str(pos.get("yes_token") or ""),
            )
            self.ledger.append({
                "kind": "exit", "condition_id": cid,
                "strategy": pos.get("strategy"), "shares": shares,
                "entry_price": pos.get("entry_price"), "exit_price": mark_price,
                "reason": reason,
            })
            self.ledger.mark_closed(cid, exit_price=mark_price, reason=reason)
            print(f"   SL exit x{shares} @ ${mark_price:.2f}", flush=True)
            return True
        except Exception as exc:
            print(f"   SL FAIL: {exc}", flush=True)
            return False

    def _write_scan(self, summary: Dict[str, Any]) -> None:
        self.scan_log.parent.mkdir(parents=True, exist_ok=True)
        summary = dict(summary)
        summary.setdefault("ts", datetime.now(CN_TZ).isoformat())
        payload: Dict[str, Any] = {"latest": summary}
        if self.scan_log.exists():
            try:
                prev = json.loads(self.scan_log.read_text(encoding="utf-8"))
                cycles = prev.get("cycles") or []
                if isinstance(cycles, list):
                    payload["cycles"] = (cycles + [summary])[-200:]
            except (OSError, json.JSONDecodeError):
                payload["cycles"] = [summary]
        else:
            payload["cycles"] = [summary]
        self.scan_log.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    async def close(self) -> None:
        if self.odds is not None:
            await self.odds.close()
