"""The Odds API client — Pinnacle (EU) reference odds for RN1 sports sleeve."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from src.utils.logging_setup import TradingLoggerMixin

DEFAULT_ODDS_HOST = "https://api.the-odds-api.com"
DEFAULT_BOOKMAKER = "pinnacle"
DEFAULT_REGION = "eu"


@dataclass(frozen=True)
class ReferenceOutcome:
    """Fair win probability for one side of an h2h market."""

    team: str
    decimal_odds: float
    fair_prob: float


@dataclass(frozen=True)
class ReferenceEvent:
    """One scheduled match with Pinnacle h2h odds."""

    event_id: str
    sport_key: str
    commence_time: datetime
    home_team: str
    away_team: str
    outcomes: Dict[str, ReferenceOutcome]  # normalized team key -> outcome


class OddsAPIError(Exception):
    """The Odds API request failed."""


class OddsAPIClient(TradingLoggerMixin):
    """Thin async wrapper around The Odds API v4."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        host: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = (api_key or os.getenv("THE_ODDS_API_KEY") or "").strip()
        self.host = (host or os.getenv("THE_ODDS_API_HOST") or DEFAULT_ODDS_HOST).rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self.last_quota: Dict[str, str] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OddsAPIClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def list_sports(self, *, all_sports: bool = False) -> List[Dict[str, Any]]:
        params = {"apiKey": self.api_key}
        if all_sports:
            params["all"] = "true"
        return await self._get("/v4/sports", params)

    async def fetch_h2h_events(
        self,
        sport_key: str,
        *,
        bookmaker: str = DEFAULT_BOOKMAKER,
        region: str = DEFAULT_REGION,
    ) -> List[ReferenceEvent]:
        """Return upcoming/in-play h2h events for one sport key."""
        params = {
            "apiKey": self.api_key,
            "regions": region,
            "markets": "h2h",
            "oddsFormat": "decimal",
            "bookmakers": bookmaker,
        }
        raw = await self._get(f"/v4/sports/{sport_key}/odds", params)
        if not isinstance(raw, list):
            raise OddsAPIError(f"Unexpected odds payload for {sport_key}: {type(raw)}")

        out: List[ReferenceEvent] = []
        for row in raw:
            parsed = _parse_reference_event(row, sport_key=sport_key, bookmaker=bookmaker)
            if parsed:
                out.append(parsed)
        return out

    async def fetch_eu_soccer_reference(
        self,
        sport_keys: Optional[List[str]] = None,
        *,
        bookmaker: str = DEFAULT_BOOKMAKER,
    ) -> List[ReferenceEvent]:
        """Fetch Pinnacle h2h odds across configured European soccer leagues."""
        from src.strategies.sports.config import EU_SOCCER_SPORT_KEYS

        keys = sport_keys or EU_SOCCER_SPORT_KEYS
        events: List[ReferenceEvent] = []
        for key in keys:
            try:
                batch = await self.fetch_h2h_events(key, bookmaker=bookmaker)
                events.extend(batch)
            except OddsAPIError as exc:
                self.logger.warning("Odds API skip sport=%s: %s", key, exc)
        return events

    async def _get(self, path: str, params: Dict[str, Any]) -> Any:
        if not self.api_key:
            raise OddsAPIError(
                "THE_ODDS_API_KEY is not set. Add it to .env (see env.template)."
            )
        url = f"{self.host}{path}"
        resp = await self._client.get(url, params=params)
        self.last_quota = {
            "remaining": resp.headers.get("x-requests-remaining", ""),
            "used": resp.headers.get("x-requests-used", ""),
            "last": resp.headers.get("x-requests-last", ""),
        }
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise OddsAPIError(f"Odds API {resp.status_code} {path}: {detail}")
        return resp.json()


def _parse_reference_event(
    row: Dict[str, Any],
    *,
    sport_key: str,
    bookmaker: str,
) -> Optional[ReferenceEvent]:
    commence_raw = row.get("commence_time")
    if not commence_raw:
        return None
    try:
        commence = datetime.fromisoformat(str(commence_raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if commence.tzinfo is None:
        commence = commence.replace(tzinfo=timezone.utc)

    home = str(row.get("home_team") or "").strip()
    away = str(row.get("away_team") or "").strip()
    if not home or not away:
        return None

    bm = None
    for book in row.get("bookmakers") or []:
        if str(book.get("key") or "").lower() == bookmaker.lower():
            bm = book
            break
    if not bm:
        return None

    market = None
    for mk in bm.get("markets") or []:
        if str(mk.get("key") or "").lower() == "h2h":
            market = mk
            break
    if not market:
        return None

    raw_probs: Dict[str, float] = {}
    decimal_by_name: Dict[str, float] = {}
    for outcome in market.get("outcomes") or []:
        name = str(outcome.get("name") or "").strip()
        try:
            price = float(outcome.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 1.0 or not name:
            continue
        raw_probs[name] = 1.0 / price
        decimal_by_name[name] = price

    if not raw_probs:
        return None

    total = sum(raw_probs.values())
    if total <= 0:
        return None

    outcomes: Dict[str, ReferenceOutcome] = {}
    for name, raw in raw_probs.items():
        fair = raw / total
        outcomes[name] = ReferenceOutcome(
            team=name,
            decimal_odds=decimal_by_name[name],
            fair_prob=fair,
        )

    return ReferenceEvent(
        event_id=str(row.get("id") or ""),
        sport_key=sport_key,
        commence_time=commence,
        home_team=home,
        away_team=away,
        outcomes=outcomes,
    )
