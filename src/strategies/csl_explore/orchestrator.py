"""CSL explore orchestrator — dry-run by default; live places min YES buys + SL."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from src.strategies.csl_explore.alerts import (
    notify_csl_cycle_summary,
    notify_csl_error,
    notify_csl_exit,
    notify_csl_order,
)
from src.strategies.csl_explore.config import (
    DEFAULT_SCAN_LOG,
    ENABLED_STRATS,
    EVENT_SLUGS,
    EXIT_FAIL_COOLDOWN_S,
    MAX_PER_MATCH_USDC,
    STOP_LOSS_PCT,
    WEEK_BUDGET_USDC,
)
from src.strategies.csl_explore.discover import fetch_match_bundles
from src.strategies.csl_explore.executor import place_yes_buy, place_yes_sell
from src.strategies.csl_explore.ledger import ExploreLedger
from src.strategies.csl_explore.models import ExploreSignal, MatchBundle
from src.strategies.csl_explore.stops import stop_loss_reason, stop_loss_triggered
from src.strategies.csl_explore.strategies.anti_whale import AntiWhaleStrategy
from src.strategies.csl_explore.strategies.completeness import CompletenessStrategy
from src.strategies.csl_explore.strategies.dog_basket import DogBasketStrategy
from src.strategies.csl_explore.strategies.fingerprint import FingerprintStrategy
from src.strategies.csl_explore.strategies.narrative import NarrativeStrategy
from src.strategies.csl_explore.strategies.time_lag import TimeLagStrategy

STRATEGY_REGISTRY = {
    FingerprintStrategy.id: FingerprintStrategy,
    TimeLagStrategy.id: TimeLagStrategy,
    CompletenessStrategy.id: CompletenessStrategy,
    NarrativeStrategy.id: NarrativeStrategy,
    AntiWhaleStrategy.id: AntiWhaleStrategy,
    DogBasketStrategy.id: DogBasketStrategy,
}


def _mark_map(matches: List[MatchBundle]) -> Dict[str, float]:
    """condition_id(lower) -> yes mid from loaded match markets."""
    out: Dict[str, float] = {}
    for m in matches:
        for leg in list(m.totals) + list(m.exact_scores) + [
            m.home_win, m.away_win, m.draw,
        ]:
            if leg and leg.condition_id and leg.yes_price > 0:
                out[leg.condition_id.lower()] = float(leg.yes_price)
    return out


class CslExploreOrchestrator:
    """Run enabled explore strategies across configured CSL event slugs."""

    def __init__(
        self,
        *,
        dry_run: bool = True,
        client: Any = None,
        gamma: Any = None,
        enabled: Optional[Sequence[str]] = None,
        slugs: Optional[Sequence[str]] = None,
        live_context: Optional[Dict[str, Any]] = None,
        ledger: Optional[ExploreLedger] = None,
        scan_log: Path = DEFAULT_SCAN_LOG,
    ) -> None:
        self.dry_run = dry_run
        self.client = client
        self.gamma = gamma
        self.enabled = tuple(enabled or ENABLED_STRATS)
        self.slugs = tuple(slugs or EVENT_SLUGS)
        self.live_context = live_context or {}
        self.ledger = ledger or ExploreLedger()
        self.scan_log = Path(scan_log)
        self._exit_fail_until: Dict[str, float] = {}
        self._strategies = []
        for sid in self.enabled:
            cls = STRATEGY_REGISTRY.get(sid)
            if cls is None:
                print(f"   [csl_explore] unknown strategy id={sid}", flush=True)
                continue
            self._strategies.append(cls())

    def _filled_keys(self) -> Set[str]:
        st = self.ledger.load_state()
        keys = st.get("filled_keys") or []
        return set(str(k) for k in keys)

    def _mark_filled(self, *keys: str, usdc: float) -> None:
        st = self.ledger.load_state()
        filled = list(st.get("filled_keys") or [])
        for key in keys:
            if key and key not in filled:
                filled.append(key)
        st["filled_keys"] = filled[-500:]
        st["spent_usdc"] = float(st.get("spent_usdc") or 0.0) + float(usdc)
        self.ledger.save_state(st)

    async def run(self) -> Dict[str, Any]:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        print(f"🧪 CSL EXPLORE ({mode})", flush=True)
        print(
            f"   Strategies: {', '.join(s.id for s in self._strategies) or '(none)'}",
            flush=True,
        )
        print(
            f"   Events: {len(self.slugs)} slugs · week_budget=${WEEK_BUDGET_USDC:.2f} "
            f"· spent=${self.ledger.week_spent():.2f} · SL={STOP_LOSS_PCT*100:.0f}% (TP manual)",
            flush=True,
        )

        matches = await fetch_match_bundles(self.slugs)
        print(f"   Loaded matches: {len(matches)}", flush=True)
        marks = _mark_map(matches)

        mids: Dict[str, float] = {}
        for m in matches:
            for leg in (m.home_win, m.away_win, m.draw):
                if leg and leg.condition_id:
                    mids[leg.condition_id] = leg.yes_price
        if mids:
            self.ledger.record_mids(mids)

        # Stop-loss pass before new entries (enrich marks for non-CSL opens e.g. UCL).
        marks = await self._enrich_marks_for_opens(marks)
        sl_exits = await self._manage_stop_losses(marks)

        context = {"live": self.live_context, "ledger": self.ledger}
        all_signals: List[ExploreSignal] = []
        for match in matches:
            print(
                f"   • {match.title} | "
                f"H/D/A={match.home_px}/{match.draw_px}/{match.away_px} "
                f"sum={match.three_way_sum()}",
                flush=True,
            )
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
            ok, spent = await self._execute(sig)
            if ok:
                placed += 1
                deployed += spent
                if not self.dry_run and spent > 0:
                    by_strategy[sig.strategy] = by_strategy.get(sig.strategy, 0) + 1
            elif not self.dry_run:
                errors += 1

        if not self.dry_run and placed > 0:
            notify_csl_cycle_summary(
                live=True,
                placed=placed,
                deployed_usdc=deployed,
                week_spent=self.ledger.week_spent(),
                by_strategy=by_strategy,
            )
        watches = sum(1 for s in all_signals if s.action == "watch")
        skips = sum(1 for s in all_signals if s.action == "skip")

        summary = {
            "dry_run": self.dry_run,
            "matches": len(matches),
            "signals_total": len(all_signals),
            "buy_planned": len(planned),
            "placed": placed,
            "stop_losses": sl_exits,
            "errors": errors,
            "deployed_usdc": round(deployed, 4),
            "watches": watches,
            "skips": skips,
            "planned_usdc": round(sum(s.usdc for s in planned), 4),
            "week_spent": self.ledger.week_spent(),
            "open_positions": len(self.ledger.open_positions()),
            "match_summaries": [m.to_summary() for m in matches],
            "planned": [s.to_dict() for s in planned],
        }
        self._write_scan(summary)
        print(
            f"   Result planned={len(planned)} placed={placed} SL={sl_exits} "
            f"open={summary['open_positions']} deployed=${deployed:.2f} "
            f"week_spent=${summary['week_spent']:.2f} "
            f"(watch={watches} skip={skips} err={errors})",
            flush=True,
        )
        return summary

    async def _enrich_marks_for_opens(self, marks: Dict[str, float]) -> Dict[str, float]:
        """Fill missing mids for open positions via Gamma (UCL / off-slug holds)."""
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
                if prices and float(prices[0]) > 0:
                    out[cid.lower()] = float(prices[0])
                    continue
                raw = m.get("outcomePrices")
                if isinstance(raw, str):
                    raw = json.loads(raw)
                if isinstance(raw, (list, tuple)) and raw:
                    out[cid.lower()] = float(raw[0])
            except Exception as exc:
                print(f"   mark enrich fail {cid[:12]}: {exc}", flush=True)
        return out

    async def _manage_stop_losses(self, marks: Dict[str, float]) -> int:
        """Limit-sell YES when mark ≤ entry × (1 - STOP_LOSS_PCT). No auto TP."""
        if STOP_LOSS_PCT <= 0:
            return 0
        opens = self.ledger.open_positions()
        if not opens:
            print("   SL: no open explore positions", flush=True)
            return 0

        print(
            f"   SL: watching {len(opens)} opens · trigger −{STOP_LOSS_PCT*100:.0f}% (TP manual)",
            flush=True,
        )
        exited = 0
        now = time.time()
        for pos in opens:
            cid = str(pos.get("condition_id") or "")
            if not cid:
                continue
            cool = self._exit_fail_until.get(cid.lower()) or 0
            if cool > now:
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
                f"   SL HIT [{pos.get('strategy')}] mark={mark:.3f} entry={entry:.3f} "
                f"| {(pos.get('match_title') or '')[:40]}",
                flush=True,
            )
            ok = await self._exit_one(pos, mark_price=mark, reason=reason)
            if ok:
                exited += 1
            else:
                self._exit_fail_until[cid.lower()] = now + EXIT_FAIL_COOLDOWN_S
        return exited

    async def _exit_one(
        self,
        pos: Dict[str, Any],
        *,
        mark_price: float,
        reason: str,
    ) -> bool:
        cid = str(pos.get("condition_id") or "")
        shares = int(pos.get("shares") or 0)
        entry = float(pos.get("entry_price") or 0)
        strategy = str(pos.get("strategy") or "")
        title = str(pos.get("match_title") or "")
        question = str(pos.get("question") or "")
        yes_token = str(pos.get("yes_token") or "")

        if self.dry_run:
            self.ledger.append({
                "kind": "exit",
                "dry_run": True,
                "condition_id": cid,
                "strategy": strategy,
                "shares": shares,
                "entry_price": entry,
                "exit_price": mark_price,
                "reason": reason,
                "match_title": title,
                "question": question,
            })
            self.ledger.mark_closed(cid, exit_price=mark_price, reason=reason)
            notify_csl_exit(
                strategy=strategy,
                match_title=title,
                question=question,
                reason=reason + " (dry)",
                shares=shares,
                entry_price=entry,
                exit_price=mark_price,
                live=False,
                condition_id=cid,
                match_slug=str(pos.get("match_slug") or ""),
            )
            print(f"   SL DRY exit x{shares} @ ${mark_price:.2f}", flush=True)
            return True

        if self.client is None:
            notify_csl_error(
                strategy=strategy, match_title=title,
                reason=reason, error="no_client", condition_id=cid,
            )
            return False

        try:
            sold, exit_px, raw = await place_yes_sell(
                self.client,
                self.gamma,
                condition_id=cid,
                price=float(mark_price),
                shares=shares,
                yes_token=yes_token,
                market=True,  # stop-loss: FOK take best bid, don't rest a limit
            )
            self.ledger.mark_closed(cid, exit_price=exit_px, reason=reason)
            self.ledger.append({
                "kind": "exit",
                "condition_id": cid,
                "strategy": strategy,
                "shares": sold,
                "entry_price": entry,
                "exit_price": exit_px,
                "reason": reason,
                "match_title": title,
                "question": question,
                "raw_status": (raw.get("status") if isinstance(raw, dict) else None),
            })
            notify_csl_exit(
                strategy=strategy,
                match_title=title,
                question=question,
                reason=reason,
                shares=sold,
                entry_price=entry,
                exit_price=exit_px,
                live=True,
                condition_id=cid,
                match_slug=str(pos.get("match_slug") or ""),
            )
            print(f"   SL LIVE exit x{sold} @ ${exit_px:.2f}", flush=True)
            return True
        except Exception as exc:
            err = str(exc)[:300]
            # Wallet holds 0 of the token → position already gone (resolved /
            # sold elsewhere / fill never delivered). Close it out instead of
            # retrying every EXIT_FAIL_COOLDOWN_S forever.
            if "not enough balance" in err.lower():
                self.ledger.mark_closed(
                    cid, exit_price=0.0, reason=reason + " | no_balance_close",
                )
                self.ledger.append({
                    "kind": "exit_error",
                    "condition_id": cid,
                    "strategy": strategy,
                    "error": err,
                    "reason": reason + " | closed_no_balance",
                })
                notify_csl_error(
                    strategy=strategy,
                    match_title=title,
                    reason=reason + " (closed: wallet balance 0)",
                    error=err,
                    condition_id=cid,
                )
                print(f"   SL close w/o balance: {cid[:12]}", flush=True)
                return True
            self.ledger.append({
                "kind": "exit_error",
                "condition_id": cid,
                "strategy": strategy,
                "error": err,
                "reason": reason,
            })
            notify_csl_error(
                strategy=strategy,
                match_title=title,
                reason=reason,
                error=str(exc)[:300],
                condition_id=cid,
            )
            print(f"   SL FAIL: {exc}", flush=True)
            return False

    def _allocate(self, signals: List[ExploreSignal]) -> List[ExploreSignal]:
        """Priority + week budget + per-match cap (live)."""
        remaining = WEEK_BUDGET_USDC - self.ledger.week_spent()
        if remaining <= 0:
            print("   Week budget exhausted — no new orders.", flush=True)
            return []

        ordered = sorted(signals, key=lambda s: (s.priority, s.match_slug, s.strategy))
        per_match: Dict[str, float] = {}
        out: List[ExploreSignal] = []
        for sig in ordered:
            if sig.usdc <= 0 or not sig.condition_id:
                continue
            used = per_match.get(sig.match_slug, 0.0)
            room_match = MAX_PER_MATCH_USDC - used
            room = min(remaining, room_match)
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
            if remaining < 0.01:
                break
        return out

    async def _execute(self, sig: ExploreSignal) -> tuple[bool, float]:
        px = sig.limit_price
        px_s = f"{px:.3f}" if px is not None else "?"
        print(
            f"   → [{sig.strategy}] ${sig.usdc:.2f} {sig.action} "
            f"@{px_s} | {sig.reason} | {(sig.question or '')[:48]}",
            flush=True,
        )
        if self.dry_run:
            self.ledger.append({"kind": "plan", **sig.to_dict()})
            return True, 0.0

        if self.client is None:
            print("   LIVE FAIL: no client", flush=True)
            self.ledger.append({"kind": "error", "error": "no_client", **sig.to_dict()})
            notify_csl_error(
                strategy=sig.strategy,
                match_title=sig.match_title,
                reason=sig.reason,
                error="no_client",
                condition_id=sig.condition_id,
            )
            return False, 0.0
        if px is None or px <= 0:
            print("   LIVE FAIL: no price", flush=True)
            self.ledger.append({"kind": "error", "error": "no_price", **sig.to_dict()})
            notify_csl_error(
                strategy=sig.strategy,
                match_title=sig.match_title,
                reason=sig.reason,
                error="no_price",
                condition_id=sig.condition_id,
            )
            return False, 0.0

        try:
            shares, fill_px, raw = await place_yes_buy(
                self.client,
                self.gamma,
                condition_id=sig.condition_id,
                price=float(px),
                usdc=float(sig.usdc),
                yes_token=sig.yes_token or "",
            )
            cost = round(shares * fill_px, 4)
            self._mark_filled(
                f"{sig.strategy}:{sig.condition_id}",
                f"cond:{sig.condition_id}",
                usdc=cost,
            )
            self.ledger.append({
                "kind": "fill",
                "shares": shares,
                "fill_price": fill_px,
                "cost_usdc": cost,
                "raw_status": (raw.get("status") if isinstance(raw, dict) else None),
                **sig.to_dict(),
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
                "opened_ts": None,
            })
            print(
                f"   LIVE OK x{shares} @ ${fill_px:.2f} ≈ ${cost:.2f}",
                flush=True,
            )
            notify_csl_order(
                strategy=sig.strategy,
                match_title=sig.match_title,
                question=sig.question,
                reason=sig.reason,
                shares=shares,
                price=fill_px,
                cost_usdc=cost,
                live=True,
                condition_id=sig.condition_id,
                match_slug=sig.match_slug,
                meta=sig.meta,
            )
            return True, cost
        except Exception as exc:
            self.ledger.append({
                "kind": "error",
                "error": str(exc)[:300],
                **sig.to_dict(),
            })
            print(f"   LIVE FAIL: {exc}", flush=True)
            notify_csl_error(
                strategy=sig.strategy,
                match_title=sig.match_title,
                reason=sig.reason,
                error=str(exc)[:300],
                condition_id=sig.condition_id,
            )
            return False, 0.0

    def _write_scan(self, summary: Dict[str, Any]) -> None:
        self.scan_log.parent.mkdir(parents=True, exist_ok=True)
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
        return None
