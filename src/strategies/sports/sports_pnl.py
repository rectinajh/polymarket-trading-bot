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
    ) -> None:
        data = self._load()
        self._ensure_experiment_start(data)
        cost_cents = int(round(shares * price * 100))
        expected_cents = int(round(shares * edge * 100))
        key = condition_id.lower()
        entries = [e for e in data["entries"] if e.get("condition_id", "").lower() != key]
        entries.append(
            {
                "condition_id": condition_id,
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
            }
        )
        data["entries"] = entries[-300:]
        self._save(data)

    def update_from_positions(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Refresh marks and settle entries when positions close. Returns newly settled."""
        by_cond: Dict[str, Dict[str, Any]] = {}
        for p in positions or []:
            if not isinstance(p, dict):
                continue
            cond = str(p.get("condition_id") or p.get("conditionId") or p.get("ticker") or "")
            if not cond:
                continue
            side = str(p.get("side") or "").upper()
            if side and side not in ("YES", "Y"):
                continue
            size = float(p.get("size") or 0)
            if size <= 0:
                continue
            by_cond[cond.lower()] = p

        data = self._load()
        updated: List[Dict[str, Any]] = []
        dirty = False
        for entry in data.get("entries") or []:
            if entry.get("status") != "open":
                continue
            cond = str(entry.get("condition_id") or "").lower()
            pos = by_cond.get(cond)
            if pos:
                cur = float(pos.get("current_price") or pos.get("curPrice") or 0)
                shares = float(pos.get("size") or entry.get("shares") or 0)
                entry["mark_cents"] = int(round(cur * shares * 100))
                entry["redeemable"] = bool(pos.get("redeemable"))
                dirty = True
                continue

            cost = int(entry.get("cost_cents") or 0)
            shares = int(entry.get("shares") or 0)
            if entry.get("redeemable") or int(entry.get("mark_cents") or 0) >= shares * 95:
                payout = shares * 100
                entry["settled_pnl_cents"] = payout - cost
                entry["status"] = "won"
            else:
                entry["settled_pnl_cents"] = -cost
                entry["status"] = "lost"
            entry["closed_ts"] = _now_cn().isoformat()
            updated.append(dict(entry))

        if dirty or updated:
            self._save(data)
        return updated

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
