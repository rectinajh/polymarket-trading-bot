"""Discord webhook alerts — shared DELPHI_ALERT_WEBHOOK_URL, Polymarket-branded."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")
SOURCE = "Polymarket"
DEFAULT_STATE_PATH = Path("data") / "ops_discord_state.json"
DEFAULT_COOLDOWN_S = 3600


def resolve_webhook_url() -> Optional[str]:
    """Shared with Delphi; same env var name."""
    url = (os.getenv("DELPHI_ALERT_WEBHOOK_URL") or "").strip()
    return url or None


def _alert_level() -> str:
    """Minimum severity to push: critical (default) or warning."""
    level = (os.getenv("POLYMARKET_DISCORD_ALERT_LEVEL") or "critical").strip().lower()
    return level if level in ("critical", "warning") else "critical"


def _cooldown_s() -> int:
    raw = (os.getenv("POLYMARKET_DISCORD_ALERT_COOLDOWN_S") or "").strip()
    try:
        return max(60, int(raw)) if raw else DEFAULT_COOLDOWN_S
    except ValueError:
        return DEFAULT_COOLDOWN_S


def _load_state(path: Path = DEFAULT_STATE_PATH) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: Dict[str, Any], path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _filter_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    level = _alert_level()
    order = {"critical": 0, "warning": 1}
    min_rank = order.get(level, 0)
    out: List[Dict[str, Any]] = []
    for a in alerts:
        sev = (a.get("severity") or "warning").lower()
        if order.get(sev, 99) <= min_rank:
            out.append(a)
    return out


def _fingerprint(alerts: List[Dict[str, Any]]) -> str:
    parts = sorted(
        f"{a.get('severity')}:{a.get('code')}:{a.get('message')}"
        for a in alerts
    )
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return digest


def format_ops_payload(payload: Dict[str, Any]) -> str:
    """Build Discord message body with Polymarket branding."""
    ts = payload.get("ts") or datetime.now(CN_TZ).isoformat()
    alerts = _filter_alerts(payload.get("alerts") or [])
    lines = [f"**[{SOURCE}]** 运维告警 · {ts[:19]}"]
    nav = payload.get("latest_nav_cents")
    if nav:
        lines.append(f"NAV ${int(nav)/100:.2f} · 429(1h)={payload.get('rate_limit_1h', 0)}")
    if not alerts:
        lines.append("✅ 当前无待推送告警项。")
        return "\n".join(lines)[:1900]

    for a in alerts:
        sev = a.get("severity", "?")
        icon = "🔴" if sev == "critical" else "🟡"
        lines.append(f"{icon} `{a.get('code')}` — {a.get('message')}")
        detail = a.get("detail")
        if detail:
            if isinstance(detail, list):
                for item in detail[:3]:
                    if isinstance(item, dict):
                        lines.append(f"  · {item.get('slug') or item.get('condition_id', '')[:24]}")
                    else:
                        lines.append(f"  · {item}")
            else:
                lines.append(f"  · {detail}")

    return "\n".join(lines)[:1900]


def _should_send(fingerprint: str, *, state_path: Path = DEFAULT_STATE_PATH) -> bool:
    state = _load_state(state_path)
    prev_fp = state.get("last_fingerprint")
    last_sent = state.get("last_sent_ts")
    if fingerprint != prev_fp:
        return True
    if not last_sent:
        return True
    try:
        sent_at = datetime.fromisoformat(str(last_sent))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=CN_TZ)
        if datetime.now(CN_TZ) - sent_at.astimezone(CN_TZ) >= timedelta(seconds=_cooldown_s()):
            return True
    except ValueError:
        return True
    return False


def send_discord_message(
    content: str,
    *,
    webhook_url: Optional[str] = None,
) -> bool:
    url = webhook_url or resolve_webhook_url()
    if not url:
        logger.debug("[%s] Discord webhook skipped — DELPHI_ALERT_WEBHOOK_URL unset", SOURCE)
        return False

    body = {
        "content": content,
        "username": f"{SOURCE} Bot",
    }
    try:
        import httpx

        resp = httpx.post(url, json=body, timeout=15.0)
        resp.raise_for_status()
        logger.info("[%s] Discord alert sent (%d chars)", SOURCE, len(content))
        print(f"[{SOURCE}] Discord alert sent ({len(content)} chars)", flush=True)
        return True
    except Exception as exc:
        logger.warning("[%s] Discord alert failed: %s", SOURCE, exc)
        print(f"[{SOURCE}] Discord alert failed: {exc}", flush=True)
        return False


def notify_ops_alerts(
    payload: Dict[str, Any],
    *,
    state_path: Path = DEFAULT_STATE_PATH,
    force: bool = False,
) -> bool:
    """Send ops payload to Discord if fingerprint changed or cooldown elapsed."""
    if not resolve_webhook_url():
        return False

    alerts = _filter_alerts(payload.get("alerts") or [])
    prev_state = _load_state(state_path)
    prev_had_critical = int(prev_state.get("last_critical_count") or 0) > 0
    cur_critical = sum(1 for a in alerts if a.get("severity") == "critical")

    if not alerts and prev_had_critical:
        content = f"**[{SOURCE}]** ✅ 严重运维告警已清除 · {payload.get('ts', '')[:19]}"
        if send_discord_message(content):
            _save_state(
                {
                    "last_fingerprint": "cleared",
                    "last_sent_ts": datetime.now(CN_TZ).isoformat(),
                    "last_critical_count": 0,
                },
                state_path,
            )
            return True
        return False

    if not alerts:
        return False

    fp = _fingerprint(alerts)
    if not force and not _should_send(fp, state_path=state_path):
        logger.debug("[%s] Discord alert suppressed (duplicate fingerprint)", SOURCE)
        return False

    content = format_ops_payload(payload)
    if not send_discord_message(content):
        return False

    _save_state(
        {
            "last_fingerprint": fp,
            "last_sent_ts": datetime.now(CN_TZ).isoformat(),
            "last_critical_count": cur_critical,
        },
        state_path,
    )
    return True
