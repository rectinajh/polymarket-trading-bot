"""On-disk counters for ops alerting (429, etc.)."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_PATH = Path("data") / "ops_metrics.json"
MAX_EVENTS = 500


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


def _load(path: Path = DEFAULT_PATH) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {"events": []}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"events": []}
        events = raw.get("events")
        if not isinstance(events, list):
            return {"events": []}
        return {"events": events}
    except (OSError, json.JSONDecodeError):
        return {"events": []}


def _save(data: Dict[str, Any], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def record_event(kind: str, *, source: str = "", detail: str = "", path: Path = DEFAULT_PATH) -> None:
    """Append a timestamped ops event (rate limits, auth errors, etc.)."""
    data = _load(path)
    events: List[Dict[str, Any]] = list(data.get("events") or [])
    events.append(
        {
            "ts": _now_iso(),
            "kind": kind,
            "source": source,
            "detail": (detail or "")[:500],
        }
    )
    if len(events) > MAX_EVENTS:
        events = events[-MAX_EVENTS:]
    data["events"] = events
    _save(data, path)


def record_rate_limit(source: str, detail: str = "", path: Path = DEFAULT_PATH) -> None:
    record_event("rate_limit", source=source, detail=detail, path=path)


def events_since(
    hours: float = 1.0,
    *,
    kind: Optional[str] = None,
    path: Path = DEFAULT_PATH,
) -> List[Dict[str, Any]]:
    cutoff = datetime.now(CN_TZ) - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for ev in _load(path).get("events") or []:
        if kind and ev.get("kind") != kind:
            continue
        ts_raw = ev.get("ts") or ""
        try:
            ts = datetime.fromisoformat(str(ts_raw))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=CN_TZ)
            if ts >= cutoff:
                out.append(ev)
        except ValueError:
            continue
    return out


def count_since(hours: float = 1.0, *, kind: str, path: Path = DEFAULT_PATH) -> int:
    return len(events_since(hours, kind=kind, path=path))
