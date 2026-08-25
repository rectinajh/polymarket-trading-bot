"""Per-window PnL ledger for the crypto 15m completeness sleeve."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.strategies.capital_policy import CN_TZ, trading_day

LEDGER_PATH = Path("data") / "btc15m_window_pnl.json"


def _now_cn() -> datetime:
    return datetime.now(CN_TZ)


class Btc15mWindowPnL:
    """Track open/settled 15m pair trades by window slug."""

    def __init__(self, path: Path = LEDGER_PATH):
        self.path = path

    def _load(self) -> Dict[str, Any]:
        try:
            if not self.path.exists():
                return {"windows": []}
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {"windows": []}
            windows = raw.get("windows")
            return {"windows": windows if isinstance(windows, list) else []}
        except (OSError, json.JSONDecodeError):
            return {"windows": []}

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _key(slug: str, asset: str) -> str:
        return f"{asset}:{slug}"

    def record_fill(
        self,
        *,
        slug: str,
        asset: str,
        condition_id: str,
        shares: int,
        combined: float,
        profit_per: float,
        window_start: Optional[int] = None,
    ) -> None:
        if not slug:
            slug = condition_id[:18]
        key = self._key(slug, asset or "x")
        data = self._load()
        windows: List[Dict[str, Any]] = [
            w for w in (data.get("windows") or []) if w.get("key") != key
        ]
        cost_cents = int(round(combined * shares * 100))
        expected_cents = int(round(profit_per * shares * 100))
        windows.append(
            {
                "key": key,
                "slug": slug,
                "asset": asset,
                "condition_id": condition_id,
                "shares": int(shares),
                "combined": round(combined, 4),
                "cost_cents": cost_cents,
                "expected_profit_cents": expected_cents,
                "settled_pnl_cents": None,
                "status": "open",
                "window_start": window_start,
                "opened_ts": _now_cn().isoformat(),
                "day": trading_day(),
            }
        )
        data["windows"] = windows[-500:]
        self._save(data)

    def mark_orphan_loss(
        self,
        *,
        condition_id: str,
        loss_cents: int,
        slug: str = "",
        asset: str = "",
    ) -> None:
        data = self._load()
        for w in data.get("windows") or []:
            if w.get("condition_id") == condition_id and w.get("status") == "open":
                w["status"] = "orphan"
                w["settled_pnl_cents"] = -abs(int(loss_cents))
                w["closed_ts"] = _now_cn().isoformat()
                break
        else:
            data.setdefault("windows", []).append(
                {
                    "key": self._key(slug or condition_id[:18], asset or "x"),
                    "slug": slug,
                    "asset": asset,
                    "condition_id": condition_id,
                    "status": "orphan",
                    "settled_pnl_cents": -abs(int(loss_cents)),
                    "closed_ts": _now_cn().isoformat(),
                    "day": trading_day(),
                }
            )
        self._save(data)

    def update_from_positions(self, positions: List[Dict[str, Any]]) -> int:
        """Mark windows settled when paired position is gone and market redeemable."""
        redeemable_conds = {
            (p.get("condition_id") or p.get("ticker") or "")
            for p in positions
            if p.get("redeemable") and abs(float(p.get("size", 0) or 0)) > 0
        }
        open_conds = {
            (p.get("condition_id") or p.get("ticker") or "")
            for p in positions
            if abs(float(p.get("size", 0) or 0)) > 0
        }
        data = self._load()
        updated = 0
        for w in data.get("windows") or []:
            if w.get("status") != "open":
                continue
            cond = w.get("condition_id") or ""
            if cond and cond not in open_conds:
                shares = int(w.get("shares") or 0)
                cost = int(w.get("cost_cents") or 0)
                # Pair held to resolution: payout ~$1/share minus cost.
                payout = shares * 100
                w["settled_pnl_cents"] = payout - cost
                w["status"] = "settled"
                w["closed_ts"] = _now_cn().isoformat()
                updated += 1
            elif cond in redeemable_conds:
                shares = int(w.get("shares") or 0)
                cost = int(w.get("cost_cents") or 0)
                w["settled_pnl_cents"] = shares * 100 - cost
                w["status"] = "settled"
                w["closed_ts"] = _now_cn().isoformat()
                updated += 1
        if updated:
            self._save(data)
        return updated

    def summary_for_day(self, day: Optional[str] = None) -> Dict[str, Any]:
        day = day or trading_day()
        windows = [
            w for w in (self._load().get("windows") or [])
            if (w.get("day") or "") == day
        ]
        open_n = sum(1 for w in windows if w.get("status") == "open")
        settled = [w for w in windows if w.get("status") == "settled"]
        orphans = [w for w in windows if w.get("status") == "orphan"]
        realized = sum(int(w.get("settled_pnl_cents") or 0) for w in settled + orphans)
        expected = sum(int(w.get("expected_profit_cents") or 0) for w in windows)
        return {
            "day": day,
            "fills": len(windows),
            "open": open_n,
            "settled": len(settled),
            "orphans": len(orphans),
            "expected_profit_cents": expected,
            "realized_pnl_cents": realized,
        }

    def recent_windows(self, limit: int = 20) -> List[Dict[str, Any]]:
        windows = list(self._load().get("windows") or [])
        return list(reversed(windows[-limit:]))
