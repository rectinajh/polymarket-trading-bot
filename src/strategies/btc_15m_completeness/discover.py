"""Discover crypto 15m Up/Down markets by deterministic Polymarket slug."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.clients.gamma_client import GammaAPIError, GammaClient

WINDOW_S = 900  # 15 minutes

# Confirmed live slug pattern: `{asset}-updown-15m-{unix}`
ASSET_SLUG_PREFIX = {
    "btc": "btc-updown-15m-",
    "eth": "eth-updown-15m-",
}

DEFAULT_ASSETS: tuple[str, ...] = ("btc", "eth")


def normalize_assets(assets: Optional[Sequence[str]] = None) -> List[str]:
    raw = list(assets) if assets is not None else list(DEFAULT_ASSETS)
    out: List[str] = []
    for a in raw:
        key = str(a or "").strip().lower()
        if not key:
            continue
        if key not in ASSET_SLUG_PREFIX:
            raise ValueError(
                f"unsupported 15m asset {a!r}; "
                f"allowed: {', '.join(sorted(ASSET_SLUG_PREFIX))}"
            )
        if key not in out:
            out.append(key)
    if not out:
        raise ValueError("at least one asset required")
    return out


def aligned_window_start(ts: Optional[float] = None) -> int:
    """Unix second of the current 15m window start (UTC)."""
    now = int(ts if ts is not None else time.time())
    return now - (now % WINDOW_S)


def window_slugs(
    *,
    asset: str = "btc",
    now_ts: Optional[float] = None,
    behind: int = 1,
    ahead: int = 3,
) -> List[str]:
    """Slugs for nearby windows for one asset."""
    prefix = ASSET_SLUG_PREFIX[normalize_assets([asset])[0]]
    base = aligned_window_start(now_ts)
    return [
        f"{prefix}{base + i * WINDOW_S}"
        for i in range(-behind, ahead + 1)
    ]


def multi_asset_slugs(
    *,
    assets: Optional[Sequence[str]] = None,
    now_ts: Optional[float] = None,
    behind: int = 1,
    ahead: int = 3,
) -> List[str]:
    """All slugs across assets (stable order: asset order × window order)."""
    slugs: List[str] = []
    for asset in normalize_assets(assets):
        slugs.extend(
            window_slugs(
                asset=asset, now_ts=now_ts, behind=behind, ahead=ahead,
            )
        )
    return slugs


def seconds_to_window_end(ts: Optional[float] = None) -> int:
    start = aligned_window_start(ts)
    now = int(ts if ts is not None else time.time())
    return max(0, start + WINDOW_S - now)


def asset_from_slug(slug: str) -> str:
    s = (slug or "").lower()
    for asset, prefix in ASSET_SLUG_PREFIX.items():
        if s.startswith(prefix):
            return asset
    return "unknown"


async def fetch_crypto_15m_markets(
    gamma: GammaClient,
    *,
    assets: Optional[Sequence[str]] = None,
    behind: int = 1,
    ahead: int = 3,
    now_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Resolve slug windows to Gamma markets (skip missing / closed)."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for slug in multi_asset_slugs(
        assets=assets, now_ts=now_ts, behind=behind, ahead=ahead,
    ):
        try:
            m = await gamma.get_market_by_slug(slug)
        except GammaAPIError:
            continue
        except Exception:
            continue
        cond = m.get("_condition_id") or m.get("conditionId") or ""
        if not cond or cond in seen:
            continue
        if m.get("closed") or m.get("_status") == "closed":
            continue
        yes_id, no_id = m.get("_token_ids") or ("", "")
        if not yes_id or not no_id:
            continue
        seen.add(cond)
        m["_slug"] = slug
        m["_asset"] = asset_from_slug(slug)
        m["_window_start"] = _slug_to_start(slug)
        out.append(m)
    return out


# Back-compat alias used by early BTC-only callers / tests.
async def fetch_btc_15m_markets(
    gamma: GammaClient,
    *,
    behind: int = 1,
    ahead: int = 3,
    now_ts: Optional[float] = None,
) -> List[Dict[str, Any]]:
    return await fetch_crypto_15m_markets(
        gamma, assets=("btc",), behind=behind, ahead=ahead, now_ts=now_ts,
    )


def _slug_to_start(slug: str) -> Optional[int]:
    try:
        return int(slug.rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return None
