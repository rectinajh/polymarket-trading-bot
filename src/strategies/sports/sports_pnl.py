"""Sports sleeve PnL ledger — open entries, settlement, experiment totals."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.strategies.capital_policy import CN_TZ, trading_day

LEDGER_PATH = Path("data") / "sports_pnl.json"


def _now_cn() -> datetime:
    return datetime.now(CN_TZ)


class SportsPnL:
    """Track sports YES entries until resolution."""

    def __init__(self, path: Path = LEDGER_PATH):
        self.path = path

    def _load(self) -> Dict[str, Any]:
        try:
            if not self.path.exists():
                return {"entries": [], "experiment_start": None}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {"entries": [], "experiment_start": None}
            entries = raw.get("entries")
            return {
                "entries": entries if isinstance(entries, list) else [],
                "experiment_start": raw.get("experiment_start"),
            }
        except (OSError, json.JSONDecodeError):
            return {"entries": [], "experiment_start": None}

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _ensure_experiment_start(self, data: Dict[str, Any]) -> None:
        if not data.get("experiment_start"):
            data["experiment_start"] = trading_day()

    def record_entry(
        self,
        *,
        condition_id: str,
        title: str,
        team: str,
        match_date: str,
        shares: int,
        price: float,
        fair_prob: float,
        edge: float,
        sport: str = "",
        match: str = "",
        live: bool = True,
        side: str = "yes",
    ) -> None:
        data = self._load()
        self._ensure_experiment_start(data)
        cost_cents = int(round(shares * price * 100))
        expected_cents = int(round(shares * edge * 100))
        side_l = str(side or "yes").lower()
        if side_l not in ("yes", "no"):
            side_l = "yes"
        key = f"{condition_id.lower()}:{side_l}"
        entries = [
            e for e in data["entries"]
            if f"{str(e.get('condition_id') or '').lower()}:{str(e.get('side') or 'yes').lower()}" != key
        ]
        entries.append(
            {
                "condition_id": condition_id,
                "side": side_l,
                "title": (title or "")[:120],
                "team": team,
                "match_date": match_date,
                "shares": int(shares),
                "price": round(price, 4),
                "cost_cents": cost_cents,
                "fair_prob": round(fair_prob, 4),
                "edge": round(edge, 4),
                "expected_profit_cents": expected_cents,
                "sport": sport,
                "match": match,
                "status": "open",
                "live": live,
                "settled_pnl_cents": None,
                "mark_cents": cost_cents,
                "redeemable": False,
                "opened_ts": _now_cn().isoformat(),
                "day": trading_day(),
                "exit_reason": None,
            }
        )
        data["entries"] = entries[-300:]
        self._save(data)

    def update_from_positions(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Refresh marks and settle entries when positions close. Returns newly settled."""
        by_key: Dict[str, Dict[str, Any]] = {}
        for p in positions or []:
            if not isinstance(p, dict):
                continue
            cond = str(p.get("condition_id") or p.get("conditionId") or p.get("ticker") or "")
            if not cond:
                continue
            side = str(p.get("side") or "").lower()
            if side not in ("yes", "no"):
                continue
            size = float(p.get("size") or 0)
            if size <= 0:
                continue
            by_key[f"{cond.lower()}:{side}"] = p

        data = self._load()
        updated: List[Dict[str, Any]] = []
        dirty = False
        for entry in data.get("entries") or []:
            if entry.get("status") != "open":
                continue
            cond = str(entry.get("condition_id") or "").lower()
            side = str(entry.get("side") or "yes").lower()
            # Legacy YES-only entries without side: treat as yes
            pos = by_key.get(f"{cond}:{side}")
            if pos:
                cur = float(pos.get("current_price") or pos.get("curPrice") or 0)
                shares = float(pos.get("size") or entry.get("shares") or 0)
                entry["mark_cents"] = int(round(cur * shares * 100))
                entry["redeemable"] = bool(pos.get("redeemable"))
                dirty = True
                continue

            # Still open on chain? skip settle — may be temporary API gap
            # Only auto-settle if redeemable was already true or mark near $1
            cost = int(entry.get("cost_cents") or 0)
            shares = int(entry.get("shares") or 0)
            if entry.get("redeemable") or int(entry.get("mark_cents") or 0) >= shares * 95:
                payout = shares * 100
                entry["settled_pnl_cents"] = payout - cost
                entry["status"] = "won"
                entry["exit_reason"] = entry.get("exit_reason") or "settled"
            else:
                # Do NOT mark lost just because position missing from snapshot
                # (NO sides used to get false "lost"). Leave open.
                continue
            entry["closed_ts"] = _now_cn().isoformat()
            updated.append(dict(entry))
            dirty = True

        if dirty or updated:
            self._save(data)
        return updated

    def reconcile_ghost_opens(
        self,
        positions: List[Dict[str, Any]],
        *,
        max_age_hours: float = 36.0,
    ) -> List[Dict[str, Any]]:
        """Close open ledger rows missing on-chain for longer than ``max_age_hours``.

        Avoids false settles on brief API gaps; ghosts (expired / already gone)
        otherwise block experiment clarity and exit logic.
        """
        present: set = set()
        for p in positions or []:
            if not isinstance(p, dict):
                continue
            cond = str(p.get("condition_id") or p.get("conditionId") or p.get("ticker") or "")
            side = str(p.get("side") or "").lower()
            if not cond or side not in ("yes", "no"):
                continue
            if float(p.get("size") or 0) <= 0:
                continue
            present.add(f"{cond.lower()}:{side}")

        data = self._load()
        closed: List[Dict[str, Any]] = []
        now = _now_cn()
        dirty = False
        for entry in data.get("entries") or []:
            if entry.get("status") != "open":
                continue
            cond = str(entry.get("condition_id") or "").lower()
            side = str(entry.get("side") or "yes").lower()
            key = f"{cond}:{side}"
            if key in present:
                continue
            opened = entry.get("opened_ts") or ""
            try:
                dt = datetime.fromisoformat(str(opened))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CN_TZ)
                age_h = (now - dt.astimezone(CN_TZ)).total_seconds() / 3600.0
            except ValueError:
                age_h = max_age_hours + 1.0
            if age_h < max_age_hours:
                continue
            cost = int(entry.get("cost_cents") or 0)
            shares = int(entry.get("shares") or 0)
            mark = int(entry.get("mark_cents") or 0)
            # Prefer last mark; else treat as total loss (capital already left NAV).
            proceeds = mark if mark > 0 else 0
            exit_px = (proceeds / 100.0 / shares) if shares > 0 else 0.0
            entry["settled_pnl_cents"] = proceeds - cost
            entry["status"] = "won" if proceeds >= cost else "lost"
            entry["exit_reason"] = f"ghost_reconcile age_h={age_h:.0f}"
            entry["exit_price"] = round(exit_px, 4)
            entry["closed_ts"] = now.isoformat()
            entry["mark_cents"] = proceeds
            closed.append(dict(entry))
            dirty = True
        if dirty:
            self._save(data)
        return closed

    def mark_closed(
        self,
        condition_id: str,
        side: str,
        *,
        exit_price: float,
        shares: int,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        """Close an open ledger row after we sell. Returns closed entry or None."""
        data = self._load()
        side_l = str(side or "yes").lower()
        cond = condition_id.lower()
        for entry in data.get("entries") or []:
            if entry.get("status") != "open":
                continue
            if str(entry.get("condition_id") or "").lower() != cond:
                continue
            if str(entry.get("side") or "yes").lower() != side_l:
                continue
            cost = int(entry.get("cost_cents") or 0)
            sh = int(shares or entry.get("shares") or 0)
            proceeds = int(round(exit_price * sh * 100))
            entry["settled_pnl_cents"] = proceeds - cost
            entry["status"] = "won" if proceeds >= cost else "lost"
            entry["exit_reason"] = reason
            entry["exit_price"] = round(exit_price, 4)
            entry["closed_ts"] = _now_cn().isoformat()
            entry["mark_cents"] = proceeds
            self._save(data)
            return dict(entry)
        return None

    def open_copy_keys(self) -> set:
        """Set of ``condition_id:side`` for open rn1 soccer copies."""
        out = set()
        for e in self._load().get("entries") or []:
            if e.get("status") != "open":
                continue
            sport = str(e.get("sport") or "")
            if "rn1" not in sport and "soccer" not in sport:
                continue
            cond = str(e.get("condition_id") or "").lower()
            side = str(e.get("side") or "yes").lower()
            if cond and side in ("yes", "no"):
                out.add(f"{cond}:{side}")
        return out

    def summary_for_day(self, day: Optional[str] = None) -> Dict[str, Any]:
        day = day or trading_day()
        entries = [
            e for e in (self._load().get("entries") or [])
            if (e.get("day") or "") == day
        ]
        open_n = sum(1 for e in entries if e.get("status") == "open")
        won = [e for e in entries if e.get("status") == "won"]
        lost = [e for e in entries if e.get("status") == "lost"]
        realized = sum(int(e.get("settled_pnl_cents") or 0) for e in won + lost)
        expected = sum(int(e.get("expected_profit_cents") or 0) for e in entries)
        deployed = sum(int(e.get("cost_cents") or 0) for e in entries)
        return {
            "day": day,
            "entries": len(entries),
            "open": open_n,
            "won": len(won),
            "lost": len(lost),
            "expected_profit_cents": expected,
            "realized_pnl_cents": realized,
            "deployed_cents": deployed,
        }

    def experiment_summary(self) -> Dict[str, Any]:
        data = self._load()
        entries = data.get("entries") or []
        start = data.get("experiment_start") or trading_day()
        settled = [e for e in entries if e.get("status") in ("won", "lost")]
        open_entries = [e for e in entries if e.get("status") == "open"]
        realized = sum(int(e.get("settled_pnl_cents") or 0) for e in settled)
        deployed = sum(int(e.get("cost_cents") or 0) for e in entries)
        open_cost = sum(int(e.get("cost_cents") or 0) for e in open_entries)
        peak = 0
        cum = 0
        for e in sorted(settled, key=lambda x: x.get("closed_ts") or ""):
            cum += int(e.get("settled_pnl_cents") or 0)
            peak = max(peak, cum)
        drawdown = peak - cum if peak > 0 else max(0, -cum)
        return {
            "experiment_start": start,
            "total_entries": len(entries),
            "settled": len(settled),
            "open": len(open_entries),
            "realized_pnl_cents": realized,
            "deployed_cents": deployed,
            "open_cost_cents": open_cost,
            "peak_pnl_cents": peak,
            "drawdown_cents": drawdown,
        }

    def daily_realized_cents(self, day: Optional[str] = None) -> int:
        day = day or trading_day()
        total = 0
        for e in self._load().get("entries") or []:
            if e.get("status") not in ("won", "lost"):
                continue
            closed = e.get("closed_ts") or e.get("opened_ts") or ""
            try:
                dt = datetime.fromisoformat(str(closed))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CN_TZ)
                if trading_day(dt) != day:
                    continue
            except ValueError:
                if (e.get("day") or "") != day:
                    continue
            total += int(e.get("settled_pnl_cents") or 0)
        return total

    def recent_entries(self, limit: int = 20) -> List[Dict[str, Any]]:
        entries = list(self._load().get("entries") or [])
        return list(reversed(entries[-limit:]))

    def open_condition_ids(self) -> set:
        return {
            str(e.get("condition_id") or "").lower()
            for e in (self._load().get("entries") or [])
            if e.get("status") == "open" and e.get("condition_id")
        }
