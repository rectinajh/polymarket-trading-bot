"""Append-only JSONL ledger + small state blob for CSL explore."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.strategies.csl_explore.config import DEFAULT_LEDGER, DEFAULT_STATE
from src.strategies.capital_policy import CN_TZ


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


class ExploreLedger:
    def __init__(
        self,
        ledger_path: Path = DEFAULT_LEDGER,
        state_path: Path = DEFAULT_STATE,
    ) -> None:
        self.ledger_path = Path(ledger_path)
        self.state_path = Path(state_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, row: Dict[str, Any]) -> None:
        row = dict(row)
        row.setdefault("ts", _now_iso())
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"mids": {}, "spent_usdc": 0.0}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"mids": {}, "spent_usdc": 0.0}

    def save_state(self, state: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def week_spent(self) -> float:
        return float(self.load_state().get("spent_usdc") or 0.0)

    def add_spent(self, usdc: float) -> None:
        st = self.load_state()
        st["spent_usdc"] = float(st.get("spent_usdc") or 0.0) + float(usdc)
        self.save_state(st)

    def record_mids(self, mids: Dict[str, float]) -> None:
        """mids: condition_id -> yes mid, with timestamp."""
        st = self.load_state()
        store = st.setdefault("mids", {})
        now = datetime.now(timezone.utc).timestamp()
        for cid, px in mids.items():
            hist: List[Dict[str, float]] = store.setdefault(cid, [])
            hist.append({"t": now, "p": float(px)})
            # keep last 30 samples
            store[cid] = hist[-30:]
        self.save_state(st)

    def mid_drop(
        self,
        condition_id: str,
        current: float,
        *,
        lookback_s: float,
    ) -> Optional[float]:
        """Return fractional drop vs oldest sample in lookback, if any."""
        st = self.load_state()
        hist = (st.get("mids") or {}).get(condition_id) or []
        if not hist:
            return None
        now = datetime.now(timezone.utc).timestamp()
        old = [h for h in hist if now - float(h.get("t") or 0) <= lookback_s]
        if not old:
            return None
        base = float(old[0].get("p") or 0)
        if base <= 0:
            return None
        return (base - current) / base

    def open_positions(self) -> List[Dict[str, Any]]:
        """Return open explore positions from state (bootstrap from ledger if empty)."""
        st = self.load_state()
        opens = st.get("opens")
        if isinstance(opens, list) and opens:
            return [o for o in opens if isinstance(o, dict) and o.get("status") == "open"]
        boot = self._bootstrap_opens_from_ledger()
        if boot:
            st["opens"] = boot
            self.save_state(st)
        return [o for o in boot if o.get("status") == "open"]

    def _bootstrap_opens_from_ledger(self) -> List[Dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        closed: set = set()
        fills: List[Dict[str, Any]] = []
        try:
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                kind = row.get("kind")
                cid = str(row.get("condition_id") or "")
                if kind == "exit" and cid:
                    closed.add(cid.lower())
                elif kind == "fill" and cid:
                    fills.append(row)
        except (OSError, json.JSONDecodeError):
            return []
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for f in fills:
            cid = str(f.get("condition_id") or "").lower()
            if not cid or cid in closed or cid in seen:
                continue
            seen.add(cid)
            out.append({
                "status": "open",
                "condition_id": f.get("condition_id"),
                "strategy": f.get("strategy") or "",
                "match_slug": f.get("match_slug") or "",
                "match_title": f.get("match_title") or "",
                "question": f.get("question") or "",
                "yes_token": f.get("yes_token") or "",
                "shares": int(f.get("shares") or 0),
                "entry_price": float(f.get("fill_price") or f.get("limit_price") or 0),
                "cost_usdc": float(f.get("cost_usdc") or 0),
                "opened_ts": f.get("ts") or "",
            })
        return out

    def record_open(self, pos: Dict[str, Any]) -> None:
        st = self.load_state()
        opens = list(st.get("opens") or [])
        cid = str(pos.get("condition_id") or "").lower()
        opens = [
            o for o in opens
            if not (isinstance(o, dict) and str(o.get("condition_id") or "").lower() == cid)
        ]
        row = dict(pos)
        row["status"] = "open"
        opens.append(row)
        st["opens"] = opens
        self.save_state(st)

    def mark_closed(
        self,
        condition_id: str,
        *,
        exit_price: float,
        reason: str,
    ) -> Optional[Dict[str, Any]]:
        st = self.load_state()
        opens = list(st.get("opens") or [])
        cid = condition_id.lower()
        closed_row = None
        kept: List[Dict[str, Any]] = []
        for o in opens:
            if not isinstance(o, dict):
                continue
            if str(o.get("condition_id") or "").lower() == cid and o.get("status") == "open":
                closed_row = dict(o)
                closed_row["status"] = "closed"
                closed_row["exit_price"] = float(exit_price)
                closed_row["exit_reason"] = reason
                closed_row["closed_ts"] = _now_iso()
            else:
                kept.append(o)
        if closed_row:
            kept.append(closed_row)
            st["opens"] = kept
            self.save_state(st)
        return closed_row

    def pnl_summary(self) -> Dict[str, Any]:
        """Realized PnL from ledger exits + open cost / mark MTM.

        Realized = Σ (exit_price − entry_price) × shares for each ``exit`` row.
        Unrealized uses latest mid in state when > 0; otherwise cost only.
        """
        fills_by_cid: Dict[str, Dict[str, Any]] = {}
        exits: List[Dict[str, Any]] = []
        if self.ledger_path.exists():
            try:
                for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    kind = row.get("kind") or row.get("action")
                    cid = str(row.get("condition_id") or "").lower()
                    if not cid:
                        continue
                    if kind in ("fill", "buy_yes"):
                        fills_by_cid[cid] = row
                    elif kind == "exit":
                        exits.append(row)
            except (OSError, json.JSONDecodeError):
                pass

        realized = 0.0
        won = lost = 0
        exit_rows: List[Dict[str, Any]] = []
        for x in exits:
            cid = str(x.get("condition_id") or "").lower()
            fill = fills_by_cid.get(cid) or {}
            entry = float(
                x.get("entry_price")
                or fill.get("entry_price")
                or fill.get("fill_price")
                or fill.get("limit_price")
                or 0
            )
            exit_px = float(x.get("exit_price") or 0)
            shares = int(x.get("shares") or fill.get("shares") or 0)
            pnl = (exit_px - entry) * shares if shares > 0 else 0.0
            realized += pnl
            if pnl > 1e-9:
                won += 1
            elif pnl < -1e-9:
                lost += 1
            exit_rows.append({
                "strategy": x.get("strategy"),
                "match_title": x.get("match_title") or x.get("question") or "",
                "shares": shares,
                "entry_price": entry,
                "exit_price": exit_px,
                "pnl_usdc": round(pnl, 4),
                "reason": x.get("reason") or "",
                "ts": x.get("ts"),
            })

        opens = self.open_positions()
        open_cost = sum(float(o.get("cost_usdc") or 0) for o in opens)
        mids = (self.load_state().get("mids") or {})
        unrealized = 0.0
        marked = 0
        for o in opens:
            cid = str(o.get("condition_id") or "").lower()
            shares = int(o.get("shares") or 0)
            entry = float(o.get("entry_price") or 0)
            hist = mids.get(cid) or mids.get(o.get("condition_id") or "") or []
            mark = 0.0
            if isinstance(hist, list) and hist:
                try:
                    mark = float(hist[-1].get("p") or 0)
                except (TypeError, ValueError, AttributeError):
                    mark = 0.0
            if mark > 0 and shares > 0 and entry > 0:
                unrealized += (mark - entry) * shares
                marked += 1

        return {
            "realized_usdc": round(realized, 2),
            "unrealized_usdc": round(unrealized, 2),
            "open_cost_usdc": round(open_cost, 2),
            "won": won,
            "lost": lost,
            "exits_n": len(exits),
            "opens_n": len(opens),
            "marked_n": marked,
            "exit_rows": exit_rows[-20:],
            "net_closed_usdc": round(realized, 2),
        }
