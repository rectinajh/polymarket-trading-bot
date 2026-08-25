"""Unit tests for RN1 sports sleeve helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.clients.odds_api_client import _parse_reference_event
from src.strategies.sports.discover import parse_match_winner_market
from src.strategies.sports.edge import compute_maker_price, evaluate_maker_opportunity
from src.strategies.sports.match import match_market_to_reference, normalize_team


def test_normalize_team_strips_fc_suffix():
    assert normalize_team("Wolverhampton Wanderers FC") == "wolverhampton wanderers"
    assert normalize_team("Manchester City") == "manchester city"


def test_parse_match_winner_market():
    m = {
        "question": "Will Liverpool FC win on 2026-08-29?",
        "conditionId": "0xabc",
        "_outcome_prices": (0.65, 0.35),
        "_token_ids": ("yes_tok", "no_tok"),
        "_volume_num": 1000.0,
        "negRisk": False,
        "orderPriceMinTickSize": 0.01,
    }
    parsed = parse_match_winner_market(m)
    assert parsed is not None
    assert parsed.team == "Liverpool FC"
    assert parsed.match_date == date(2026, 8, 29)
    assert parsed.yes_price == 0.65


def test_parse_reference_event_vig_adjusted():
    row = {
        "id": "evt1",
        "commence_time": "2026-08-29T15:00:00Z",
        "home_team": "Liverpool",
        "away_team": "Nottingham Forest",
        "bookmakers": [{
            "key": "pinnacle",
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": "Liverpool", "price": 1.53},
                    {"name": "Nottingham Forest", "price": 5.89},
                    {"name": "Draw", "price": 4.69},
                ],
            }],
        }],
    }
    event = _parse_reference_event(row, sport_key="soccer_epl", bookmaker="pinnacle")
    assert event is not None
    liverpool = event.outcomes["Liverpool"]
    assert 0.6 < liverpool.fair_prob < 0.7


def test_match_market_to_reference_by_date_and_team():
    row = {
        "id": "evt1",
        "commence_time": "2026-08-29T15:00:00Z",
        "home_team": "Liverpool",
        "away_team": "Nottingham Forest",
        "bookmakers": [{
            "key": "pinnacle",
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": "Liverpool", "price": 1.53},
                    {"name": "Nottingham Forest", "price": 5.89},
                    {"name": "Draw", "price": 4.69},
                ],
            }],
        }],
    }
    event = _parse_reference_event(row, sport_key="soccer_epl", bookmaker="pinnacle")
    market = parse_match_winner_market({
        "question": "Will Liverpool win on 2026-08-29?",
        "conditionId": "0xabc",
        "_outcome_prices": (0.60, 0.40),
        "_token_ids": ("y", "n"),
    })
    ref = match_market_to_reference(market, [event])
    assert ref is not None
    assert ref.team_name == "Liverpool"
    assert ref.fair_prob > 0.6


def test_compute_maker_price_respects_edge_and_band():
    price = compute_maker_price(
        fair_prob=0.70,
        tick=0.01,
        best_bid=0.58,
        best_ask=0.62,
        min_edge=0.05,
    )
    assert price is not None
    assert 0.50 <= price <= 0.65
    assert 0.70 - price >= 0.05 - 1e-6


def test_evaluate_maker_opportunity_rejects_outside_favorite_band():
    market = parse_match_winner_market({
        "question": "Will Liverpool win on 2026-08-29?",
        "conditionId": "0xabc",
        "_outcome_prices": (0.90, 0.10),
        "_token_ids": ("y", "n"),
    })
    ref = match_market_to_reference(
        market,
        [_make_liverpool_event()],
    )
    opp = evaluate_maker_opportunity(
        market,
        ref,
        {"orderbook": {"yes": [[0.89, 100]], "yes_asks": [[0.91, 100]]}},
    )
    assert opp is None


def _make_liverpool_event():
    from src.clients.odds_api_client import ReferenceEvent, ReferenceOutcome

    return ReferenceEvent(
        event_id="1",
        sport_key="soccer_epl",
        commence_time=datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc),
        home_team="Liverpool",
        away_team="Nottingham Forest",
        outcomes={
            "Liverpool": ReferenceOutcome("Liverpool", 1.53, 0.654),
        },
    )


def test_rn1_confirms_strict_matching_trade():
    from src.strategies.sports.edge import MakerOpportunity
    from src.strategies.sports.match import MatchedReference
    from src.strategies.sports.rn1_tracker import RN1WalletTracker, Rn1Trade, rn1_confirms

    market = parse_match_winner_market({
        "question": "Will LASK Linz win on 2026-08-25?",
        "conditionId": "0xcond123",
        "_outcome_prices": (0.51, 0.49),
        "_token_ids": ("y", "n"),
        "slug": "uel-lask-2026-08-25-lask",
        "events": [{"slug": "uel-lask-2026-08-25"}],
    })
    ref = MatchedReference(
        event=_make_liverpool_event(),
        team_name="LASK Linz",
        fair_prob=0.58,
        match_score=0.9,
    )
    opp = MakerOpportunity(
        market=market,
        reference=ref,
        fair_prob=0.58,
        maker_price=0.51,
        edge=0.07,
        best_bid=0.50,
        best_ask=0.53,
    )
    tracker = RN1WalletTracker(wallet="0xtest")
    tracker._trades = [
        Rn1Trade(
            condition_id="0xcond123",
            event_key="uel-lask-2026-08-25",
            side="BUY",
            outcome="YES",
            price=0.51,
            size=230,
            timestamp=datetime.now(timezone.utc).timestamp(),
            title="Will LASK Linz win on 2026-08-25?",
        )
    ]
    result = rn1_confirms(opp, tracker, mode="strict")
    assert result.confirmed is True
    assert result.matching_trade is not None


def test_rn1_confirms_strict_rejects_no_trade():
    from src.strategies.sports.edge import MakerOpportunity
    from src.strategies.sports.match import MatchedReference
    from src.strategies.sports.rn1_tracker import RN1WalletTracker, rn1_confirms

    market = parse_match_winner_market({
        "question": "Will Real Madrid CF win on 2026-08-26?",
        "conditionId": "0xmadrid",
        "_outcome_prices": (0.68, 0.32),
        "_token_ids": ("y", "n"),
    })
    ref = MatchedReference(
        event=_make_liverpool_event(),
        team_name="Real Madrid",
        fair_prob=0.73,
        match_score=0.9,
    )
    opp = MakerOpportunity(
        market=market,
        reference=ref,
        fair_prob=0.73,
        maker_price=0.68,
        edge=0.05,
        best_bid=0.67,
        best_ask=0.70,
    )
    tracker = RN1WalletTracker(wallet="0xtest")
    tracker._trades = []
    result = rn1_confirms(opp, tracker, mode="strict")
    assert result.confirmed is False


def test_rn1_confirms_off_always_passes():
    from src.strategies.sports.edge import MakerOpportunity
    from src.strategies.sports.match import MatchedReference
    from src.strategies.sports.rn1_tracker import RN1WalletTracker, rn1_confirms

    market = parse_match_winner_market({
        "question": "Will Real Madrid CF win on 2026-08-26?",
        "conditionId": "0xmadrid",
        "_outcome_prices": (0.68, 0.32),
        "_token_ids": ("y", "n"),
    })
    ref = MatchedReference(
        event=_make_liverpool_event(),
        team_name="Real Madrid",
        fair_prob=0.73,
        match_score=0.9,
    )
    opp = MakerOpportunity(
        market=market,
        reference=ref,
        fair_prob=0.73,
        maker_price=0.68,
        edge=0.05,
        best_bid=0.67,
        best_ask=0.70,
    )
    result = rn1_confirms(opp, RN1WalletTracker(wallet="0xtest"), mode="off")
    assert result.confirmed is True
