"""
Polymarket Gamma API client — market discovery and metadata.

Why a separate client? On the legacy exchange, market discovery and order placement go
through the same authenticated REST API. On Polymarket they are split:

  * **Gamma API** (`gamma-api.polymarket.com`): unauthenticated REST returning
    market/event metadata, current best bid/ask, volume, tags, expiry,
    `conditionId`, `clobTokenIds`. This is what the ingestion job uses.
  * **CLOB API** (`clob.polymarket.com`): authenticated, used for orders,
    orderbook, positions, and `/prices-history` (time-series). Wrapped by
    :class:`PolymarketClient`.

`GammaClient` does NOT sign anything — it's a thin async wrapper over `httpx`.
The interface returns Polymarket-shaped dicts plus a `to_legacy_market_shape()`
helper that produces the same keys the legacy ingestion job (and `Market`
dataclass) expect, so downstream code can keep treating markets generically.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.utils.logging_setup import TradingLoggerMixin
from src.config.settings import validate_endpoint_url


DEFAULT_GAMMA_HOST = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_HOST = "https://clob.polymarket.com"  # for /prices-history

# Pagination caps — Gamma returns at most ~500 markets per page reliably.
MAX_LIMIT_PER_PAGE = 500
DEFAULT_LIMIT_PER_PAGE = 200

# Maximum pages to walk in one discovery call. 40 × 200 = 8 000 markets,
# enough for the full active universe (~3 000 today) with margin.
MAX_PAGES = 40
# Gamma `/events?offset=` rejects values above this with HTTP 422
# ("offset too large, use /events/keyset"). Stop offset pagination here.
MAX_EVENTS_OFFSET = 2000

# Canonical Polymarket category slugs → tag IDs.
# /tags endpoint only paginates a subset and does NOT include some canonical
# IDs (sports=1, politics=2). Discovered empirically from /events; verify if
# Polymarket rotates these.
KNOWN_TAG_IDS: Dict[str, int] = {
    "sports":      1,
    "politics":    2,
    "awards":      18,
    "basketball":  28,
    "music":       100,
    "business":    107,
    "elections":   144,
    "middle-east": 154,
    "united-states": 165,
    "israel":      180,
    "primaries":   264,
    "epl":         306,
    "formula1":    435,
    "pop-culture": 596,
    "nba":         745,
    "champions-league": 1234,
    "global-elections": 1597,
    "us-presidential-election": 1101,
    "world":       101970,
    "fifa-world-cup": 102232,
    "nba-champion": 102288,
    "soccer":      100350,
    "geopolitics": 100265,
    "ucl":         100977,
    "f1":          100389,
}


class GammaAPIError(Exception):
    """Generic Gamma / data-API failure."""


class GammaClient(TradingLoggerMixin):
    """Async wrapper around Polymarket's Gamma API.

    Construction is cheap. Use as an async context manager OR call
    :meth:`close` when done so the underlying httpx session is released.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        clob_host: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        self.host = validate_endpoint_url(
            (host or os.getenv("POLYMARKET_GAMMA_HOST", DEFAULT_GAMMA_HOST)).rstrip("/"),
            what="POLYMARKET_GAMMA_HOST",
        )
        self.clob_host = validate_endpoint_url(
            (clob_host or os.getenv("POLYMARKET_HOST", DEFAULT_CLOB_HOST)).rstrip("/"),
            what="POLYMARKET_HOST",
        )
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json"},
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        # condition_id → (yes_token_id, no_token_id) cache
        self._token_cache: Dict[str, Tuple[str, str]] = {}
        # tag slug → tag id cache, seeded from KNOWN_TAG_IDS and grown via
        # discovery. Resolves the gap where /tags doesn't return canonical IDs.
        self._tag_slug_to_id: Dict[str, int] = {
            slug.lower(): tid for slug, tid in KNOWN_TAG_IDS.items()
        }

        self.logger.info("GammaClient initialized", host=self.host, clob_host=self.clob_host)

    async def close(self) -> None:
        await self._client.aclose()
        self.logger.info("GammaClient closed")

    async def __aenter__(self) -> "GammaClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Low-level request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """HTTP request with exponential-backoff retries on 429/5xx.

        Gamma is unauthenticated and rarely rate-limits, but the CLOB
        prices-history endpoint occasionally returns 429 under load.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = await self._client.request(method, url, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    sleep = self.backoff_factor * (2 ** attempt)
                    self.logger.warning(
                        f"Gamma {resp.status_code} from {url}; retrying in {sleep:.2f}s",
                        attempt=attempt + 1,
                    )
                    await asyncio.sleep(sleep)
                    last_exc = GammaAPIError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                # 4xx (other than 429): don't retry, fail fast.
                raise GammaAPIError(
                    f"HTTP {exc.response.status_code} {url}: {exc.response.text[:200]}"
                ) from exc
            except (httpx.RequestError, json.JSONDecodeError) as exc:
                last_exc = exc
                sleep = self.backoff_factor * (2 ** attempt)
                self.logger.warning(
                    f"Gamma request failed: {exc}; retrying in {sleep:.2f}s",
                    attempt=attempt + 1,
                )
                await asyncio.sleep(sleep)

        raise GammaAPIError(
            f"Gamma request {method} {url} failed after {self.max_retries} retries: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        archived: bool = False,
        accepting_orders: bool = True,
        min_volume: Optional[float] = None,
        max_time_to_expiry_days: Optional[float] = None,
        min_time_to_expiry_minutes: Optional[float] = None,
        tag_ids: Optional[List[int]] = None,
        exclude_tag_ids: Optional[List[int]] = None,
        order: str = "volume",
        ascending: bool = False,
        max_results: int = 2000,
        page_size: int = DEFAULT_LIMIT_PER_PAGE,
    ) -> List[Dict[str, Any]]:
        """Fetch active markets matching the given filters.

        Walks Gamma's `/events` endpoint and explodes the nested market list
        from each event. We use /events rather than /markets here because the
        /markets endpoint returns an `events` array WITHOUT tags populated,
        which would make every market `category=unknown`. Walking /events
        gives us category tags in the same round-trip.

        After fetching, applies Python-side filters that Gamma doesn't expose
        server-side (volume threshold, time window, tag exclusion). Returns
        Polymarket-native dicts; call :func:`to_legacy_market_shape` if you need the
        legacy legacy field names.

        Args:
            active, closed, archived, accepting_orders: Server-side flags.
                Default = "real, currently tradeable" markets.
            min_volume: Drop markets with `volumeNum` below this (in USD).
            max_time_to_expiry_days: Drop markets expiring more than N days
                from now. Mirrors `settings.trading.max_time_to_expiry_days`.
            min_time_to_expiry_minutes: Drop markets expiring within N minutes
                (avoids placing orders that can't settle in time).
            tag_ids: If set, only include markets whose parent event has at
                least one of these tag IDs.
            exclude_tag_ids: Drop markets whose parent event has any of these
                tag IDs.
            order: Sort field (server-side). One of: volume, volume_24hr,
                liquidity, end_date, start_date, competitive.
            ascending: Sort direction.
            max_results: Hard cap on returned markets.
            page_size: Per-request page size (Gamma caps around 500).
        """
        page_size = min(page_size, MAX_LIMIT_PER_PAGE)
        out: List[Dict[str, Any]] = []
        seen_conditions: set[str] = set()
        offset = 0
        page = 0
        now_ts = datetime.now(timezone.utc).timestamp()

        max_ts = (
            now_ts + max_time_to_expiry_days * 86400
            if max_time_to_expiry_days is not None
            else None
        )
        min_ts = (
            now_ts + min_time_to_expiry_minutes * 60
            if min_time_to_expiry_minutes is not None
            else None
        )
        tag_ids_set = set(tag_ids or [])
        exclude_tag_ids_set = set(exclude_tag_ids or [])

        while page < MAX_PAGES and len(out) < max_results:
            if offset > MAX_EVENTS_OFFSET:
                self.logger.info(
                    "Gamma /events offset cap reached; stopping pagination",
                    offset=offset,
                    max_offset=MAX_EVENTS_OFFSET,
                    markets=len(out),
                )
                break
            params: Dict[str, Any] = {
                "active":    str(active).lower(),
                "closed":    str(closed).lower(),
                "archived":  str(archived).lower(),
                "limit":     page_size,
                "offset":    offset,
                "order":     order,
                "ascending": str(ascending).lower(),
            }
            try:
                data = await self._request("GET", f"{self.host}/events", params=params)
            except GammaAPIError as exc:
                if "offset too large" in str(exc).lower():
                    self.logger.info(
                        "Gamma pagination ended (offset limit)",
                        offset=offset,
                        markets=len(out),
                    )
                    break
                raise
            if not isinstance(data, list) or not data:
                break

            for event in data:
                # Hoist the event's tags onto each nested market so that
                # `_derive_market_fields` finds them (the /markets endpoint
                # returns `events: [{...}]` without tags; we synthesise that
                # here by attaching the parent event with its tags intact).
                event_with_tags = {
                    "id": event.get("id"),
                    "ticker": event.get("ticker"),
                    "slug": event.get("slug"),
                    "title": event.get("title"),
                    "tags": event.get("tags", []),
                    "category": event.get("category"),
                }
                # Grow the slug→id cache from the tags we see (the /tags
                # endpoint is incomplete — see KNOWN_TAG_IDS for context).
                for t in event.get("tags") or []:
                    slug = (t.get("slug") or "").lower()
                    tid = t.get("id")
                    if slug and tid is not None and slug not in self._tag_slug_to_id:
                        try:
                            self._tag_slug_to_id[slug] = int(tid)
                        except (TypeError, ValueError):
                            pass

                for m in event.get("markets", []) or []:
                    cond = m.get("conditionId") or ""
                    if not cond or cond in seen_conditions:
                        continue
                    if accepting_orders and not m.get("acceptingOrders", True):
                        continue
                    if not m.get("enableOrderBook", True):
                        continue

                    # Inject parent event so `_derive_market_fields` can pull tags
                    m.setdefault("events", [event_with_tags])

                    derived = _derive_market_fields(m)

                    if min_volume is not None and derived["_volume_num"] < min_volume:
                        continue

                    end_ts = derived["_end_ts"]
                    if end_ts:
                        if max_ts is not None and end_ts > max_ts:
                            continue
                        if min_ts is not None and end_ts < min_ts:
                            continue
                    elif max_ts is not None or min_ts is not None:
                        continue

                    evt_tag_ids = set(derived["_event_tag_ids"])
                    if exclude_tag_ids_set and evt_tag_ids & exclude_tag_ids_set:
                        continue
                    if tag_ids_set and not (evt_tag_ids & tag_ids_set):
                        continue

                    yes_id, no_id = derived["_token_ids"]
                    if yes_id and no_id:
                        self._token_cache[derived["_condition_id"]] = (yes_id, no_id)

                    m.update(derived)
                    out.append(m)
                    seen_conditions.add(cond)

                    if len(out) >= max_results:
                        break
                if len(out) >= max_results:
                    break

            offset += page_size
            page += 1
            await asyncio.sleep(0.05)

        self.logger.info(
            f"Gamma fetched {len(out)} markets via /events",
            pages=page,
            min_volume=min_volume,
            max_days=max_time_to_expiry_days,
        )
        return out

    async def get_market(self, condition_id: str) -> Dict[str, Any]:
        """Fetch a single market by `conditionId`. Returns the same shape as
        items in :meth:`get_markets` (with derived fields). Raises if not found.
        """
        # Gamma does not expose /markets/{conditionId} — must filter client-side.
        # In practice we fetch the wider /markets list with a slug match where
        # possible. Here we walk a small page and filter; for production, prefer
        # caching token_ids at ingestion time and avoid re-lookups.
        data = await self._request(
            "GET",
            f"{self.host}/markets",
            params={"limit": 500, "active": "true", "closed": "false"},
        )
        if not isinstance(data, list):
            raise GammaAPIError(f"unexpected /markets response type: {type(data).__name__}")
        for m in data:
            if m.get("conditionId") == condition_id:
                derived = _derive_market_fields(m)
                yes_id, no_id = derived["_token_ids"]
                if yes_id and no_id:
                    self._token_cache[condition_id] = (yes_id, no_id)
                m.update(derived)
                return m
        # Fallback: try also-closed
        data = await self._request(
            "GET",
            f"{self.host}/markets",
            params={"limit": 500, "active": "false", "closed": "true"},
        )
        for m in data or []:
            if m.get("conditionId") == condition_id:
                derived = _derive_market_fields(m)
                m.update(derived)
                return m
        raise GammaAPIError(f"market with conditionId={condition_id} not found")

    async def get_market_by_slug(self, slug: str) -> Dict[str, Any]:
        """Fetch a single market by its URL slug."""
        data = await self._request(
            "GET", f"{self.host}/markets", params={"slug": slug}
        )
        if isinstance(data, list) and data:
            m = data[0]
        elif isinstance(data, dict):
            m = data
        else:
            raise GammaAPIError(f"market with slug={slug} not found")
        derived = _derive_market_fields(m)
        yes_id, no_id = derived["_token_ids"]
        if yes_id and no_id:
            self._token_cache[derived["_condition_id"]] = (yes_id, no_id)
        m.update(derived)
        return m

    async def get_token_ids(self, condition_id: str) -> Tuple[str, str]:
        """Return `(yes_token_id, no_token_id)` for a condition.

        Used by :class:`PolymarketClient` for lazy token resolution. Cached
        after first lookup.
        """
        cached = self._token_cache.get(condition_id)
        if cached is not None:
            return cached
        market = await self.get_market(condition_id)
        ids = market["_token_ids"]
        if not (ids[0] and ids[1]):
            raise GammaAPIError(
                f"market {condition_id} has no clobTokenIds — cannot resolve YES/NO"
            )
        return ids

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def get_events(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        archived: bool = False,
        tag_ids: Optional[List[int]] = None,
        order: str = "volume",
        ascending: bool = False,
        max_results: int = 1000,
        page_size: int = DEFAULT_LIMIT_PER_PAGE,
    ) -> List[Dict[str, Any]]:
        """Fetch events with their nested markets. Useful when a strategy wants
        to reason about market groups (e.g. "Will any candidate hit X")."""
        page_size = min(page_size, MAX_LIMIT_PER_PAGE)
        out: List[Dict[str, Any]] = []
        offset = 0
        page = 0
        tag_ids_set = set(tag_ids or [])

        while page < MAX_PAGES and len(out) < max_results:
            if offset > MAX_EVENTS_OFFSET:
                break
            params: Dict[str, Any] = {
                "active":    str(active).lower(),
                "closed":    str(closed).lower(),
                "archived":  str(archived).lower(),
                "limit":     page_size,
                "offset":    offset,
                "order":     order,
                "ascending": str(ascending).lower(),
            }
            try:
                data = await self._request("GET", f"{self.host}/events", params=params)
            except GammaAPIError as exc:
                if "offset too large" in str(exc).lower():
                    break
                raise
            if not isinstance(data, list) or not data:
                break

            for e in data:
                if tag_ids_set:
                    evt_tags = {int(t.get("id")) for t in e.get("tags", []) if t.get("id") is not None}
                    if not (evt_tags & tag_ids_set):
                        continue
                out.append(e)
                if len(out) >= max_results:
                    break

            offset += page_size
            page += 1
            await asyncio.sleep(0.05)

        return out

    # ------------------------------------------------------------------
    # Tags / categories
    # ------------------------------------------------------------------

    async def get_tags(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Return Polymarket's tag taxonomy (`{id, slug, label}` per tag).
        Use the slugs to map your `preferred_categories` /
        `excluded_categories` settings to numeric tag_ids.
        """
        data = await self._request(
            "GET", f"{self.host}/tags", params={"limit": limit}
        )
        return data if isinstance(data, list) else []

    async def resolve_tag_slugs(self, slugs: List[str]) -> List[int]:
        """Convert a list of slugs to numeric tag IDs (for /markets ?tag_id=).

        Resolution order:
          1. Internal cache (seeded with KNOWN_TAG_IDS, grown by discovery).
          2. /tags endpoint (incomplete — many canonical tags missing).
          3. /events walk (last resort: scan recent events for the slug).

        Slugs not found in any source are silently dropped — caller should
        validate the returned list length matches their expectation.
        """
        slugs_lower = [s.lower() for s in slugs]
        out: List[int] = []
        unresolved: List[str] = []
        for s in slugs_lower:
            tid = self._tag_slug_to_id.get(s)
            if tid is not None:
                out.append(tid)
            else:
                unresolved.append(s)

        if unresolved:
            try:
                tags = await self.get_tags()
                tag_index = {(t.get("slug") or "").lower(): t.get("id") for t in tags}
                still: List[str] = []
                for s in unresolved:
                    tid = tag_index.get(s)
                    if tid is not None:
                        try:
                            tid_int = int(tid)
                            self._tag_slug_to_id[s] = tid_int
                            out.append(tid_int)
                        except (TypeError, ValueError):
                            still.append(s)
                    else:
                        still.append(s)
                unresolved = still
            except Exception as exc:
                self.logger.warning(f"resolve_tag_slugs: /tags lookup failed: {exc}")

        if unresolved:
            # Last-resort discovery walk — limited to one /events page.
            try:
                events = await self.get_events(max_results=200)
                for e in events:
                    for t in e.get("tags", []) or []:
                        slug = (t.get("slug") or "").lower()
                        if slug in unresolved and t.get("id") is not None:
                            try:
                                tid_int = int(t["id"])
                                self._tag_slug_to_id[slug] = tid_int
                                out.append(tid_int)
                                unresolved.remove(slug)
                            except (TypeError, ValueError):
                                continue
            except Exception as exc:
                self.logger.warning(f"resolve_tag_slugs: /events walk failed: {exc}")

        if unresolved:
            self.logger.warning(
                f"Could not resolve tag slugs to IDs: {unresolved}. "
                "Add to KNOWN_TAG_IDS or check Polymarket's tag taxonomy."
            )

        return out

    # ------------------------------------------------------------------
    # Price history (CLOB endpoint, kept here for one-stop discovery)
    # ------------------------------------------------------------------

    async def get_price_history(
        self,
        token_id: str,
        *,
        interval: str = "1d",         # 1m, 1h, 6h, 1d, 1w, max
        fidelity: int = 60,           # minutes between samples
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 500,             # ignored by CLOB but kept for API parity
    ) -> Dict[str, Any]:
        """Time-series price history for a single token_id.

        Returns `{"history": [{"t": unix_ts, "p": price_dollars}, ...]}`,
        matching the CLOB endpoint's native shape. `start_ts`/`end_ts` override
        the `interval` window when both supplied.
        """
        params: Dict[str, Any] = {"market": token_id, "fidelity": fidelity}
        if start_ts is not None and end_ts is not None:
            params["startTs"] = int(start_ts)
            params["endTs"] = int(end_ts)
        else:
            params["interval"] = interval

        data = await self._request("GET", f"{self.clob_host}/prices-history", params=params)
        if not isinstance(data, dict):
            raise GammaAPIError(f"unexpected prices-history shape: {type(data).__name__}")
        history = data.get("history", [])
        if limit:
            history = history[-limit:]
        return {"history": history}


# --------------------------------------------------------------------------
# Field-derivation helpers
# --------------------------------------------------------------------------

def _derive_market_fields(m: Dict[str, Any]) -> Dict[str, Any]:
    """Pull computed convenience fields out of a raw Gamma market dict.

    Adds (with `_` prefix to mark them as derived):
      _condition_id      str
      _token_ids         (yes_token_id, no_token_id)
      _outcome_prices    (yes_price, no_price) — floats in $
      _volume_num        float (USD)
      _end_ts            int (unix seconds)
      _category          str (slug of first event tag, "unknown" if absent)
      _event_id          str | None
      _event_tag_ids     [int]
      _status            'active' | 'closed' | 'archived'
    """
    cond = m.get("conditionId") or ""

    # clobTokenIds is a JSON-encoded string '[<yes_id>, <no_id>]' aligned to
    # the order of `outcomes` (which is always `["Yes", "No"]` for binary).
    raw_ids = m.get("clobTokenIds") or "[]"
    yes_id, no_id = "", ""
    try:
        ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
        if isinstance(ids, list) and len(ids) >= 2:
            yes_id, no_id = str(ids[0]), str(ids[1])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # outcomePrices is similarly a JSON string '["0.345", "0.655"]'
    raw_prices = m.get("outcomePrices") or "[]"
    yes_price, no_price = 0.0, 0.0
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        if isinstance(prices, list) and len(prices) >= 2:
            yes_price, no_price = float(prices[0]), float(prices[1])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Fallbacks if outcomePrices is missing — derive from bestBid/bestAsk
    if not yes_price and m.get("bestAsk") is not None:
        yes_price = float(m.get("bestAsk") or 0)
    if not no_price and yes_price:
        no_price = max(0.0, 1.0 - yes_price)

    volume_num = float(
        m.get("volumeNum") or m.get("volume") or m.get("volumeClob") or 0
    )

    # End-date parsing — prefer ISO timestamp, fall back to date-only.
    end_ts = 0
    end_iso = m.get("endDate") or m.get("endDateIso") or ""
    if end_iso:
        try:
            end_ts = int(
                datetime.fromisoformat(end_iso.replace("Z", "+00:00")).timestamp()
            )
        except ValueError:
            try:
                end_ts = int(
                    datetime.fromisoformat(end_iso + "T00:00:00+00:00").timestamp()
                )
            except ValueError:
                end_ts = 0

    # Category from first event tag (most useful one).
    events = m.get("events") or []
    event_id: Optional[str] = None
    category = "unknown"
    event_tag_ids: List[int] = []
    if events:
        e = events[0]
        event_id = str(e.get("id") or "") or None
        tags = e.get("tags") or []
        if tags:
            category = (tags[0].get("slug") or tags[0].get("label") or "unknown").lower()
        for t in tags:
            tid = t.get("id")
            if tid is not None:
                try:
                    event_tag_ids.append(int(tid))
                except (TypeError, ValueError):
                    continue

    if m.get("archived"):
        status = "archived"
    elif m.get("closed"):
        status = "closed"
    elif m.get("active"):
        status = "active"
    else:
        status = "inactive"

    return {
        "_condition_id":   cond,
        "_token_ids":      (yes_id, no_id),
        "_outcome_prices": (yes_price, no_price),
        "_volume_num":     volume_num,
        "_end_ts":         end_ts,
        "_category":       category,
        "_event_id":       event_id,
        "_event_tag_ids":  event_tag_ids,
        "_status":         status,
    }


def to_legacy_market_shape(m: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a (derived) Gamma market dict into a legacy-compatible dict.

    The legacy ingestion job and `Market` dataclass expect these keys:
        ticker, title, yes_price, no_price, volume, expiration_time,
        category, status, yes_bid_dollars, yes_ask_dollars, ...

    This function returns a NEW dict with both Polymarket-native and
    legacy-shape keys merged, so downstream code can read whichever it knows.
    The Polymarket source dict must already have been passed through
    :func:`_derive_market_fields` (the GammaClient does this automatically).
    """
    yes_price, no_price = m.get("_outcome_prices", (0.0, 0.0))
    end_ts = m.get("_end_ts", 0)
    expiration_iso = m.get("endDate") or m.get("endDateIso") or ""

    return {
        # legacy-shape fields
        "ticker":            m.get("_condition_id") or m.get("conditionId", ""),
        "title":             m.get("question", ""),
        "yes_price":         yes_price,
        "no_price":          no_price,
        "yes_bid_dollars":   float(m.get("bestBid") or 0),
        "yes_ask_dollars":   float(m.get("bestAsk") or yes_price or 0),
        "no_bid_dollars":    max(0.0, 1.0 - float(m.get("bestAsk") or yes_price or 0)),
        "no_ask_dollars":    max(0.0, 1.0 - float(m.get("bestBid") or 0)),
        "yes_bid":           int(round(float(m.get("bestBid") or 0) * 100)),
        "yes_ask":           int(round(float(m.get("bestAsk") or yes_price or 0) * 100)),
        "no_bid":            int(round(max(0.0, 1.0 - float(m.get("bestAsk") or yes_price or 0)) * 100)),
        "no_ask":            int(round(max(0.0, 1.0 - float(m.get("bestBid") or 0)) * 100)),
        "last_price_dollars": yes_price,
        "last_price":        int(round(yes_price * 100)),
        "volume":            int(m.get("_volume_num", 0)),
        "volume_fp":         m.get("_volume_num", 0),
        "expiration_time":   expiration_iso,
        "expiration_ts":     end_ts,
        "category":          m.get("_category", "unknown"),
        "status":            "active" if m.get("_status") == "active" else m.get("_status", "unknown"),
        "rules":             m.get("description", ""),
        # Polymarket-specific (pass-through; available to strategies that want them)
        "condition_id":      m.get("_condition_id") or m.get("conditionId", ""),
        "yes_token_id":      m.get("_token_ids", ("", ""))[0],
        "no_token_id":       m.get("_token_ids", ("", ""))[1],
        "neg_risk":          bool(m.get("negRisk", False)),
        "min_tick_size":     float(m.get("orderPriceMinTickSize", 0.01)),
        "min_order_size":    int(m.get("orderMinSize", 0)),
        "spread":            float(m.get("spread", 0) or 0),
        "slug":              m.get("slug", ""),
    }
