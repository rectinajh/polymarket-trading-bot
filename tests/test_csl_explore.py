"""Unit tests for CSL explore signal rules (no network)."""

from __future__ import annotations

from src.strategies.csl_explore.models import BinaryMarket, MatchBundle
from src.strategies.csl_explore.strategies.anti_whale import AntiWhaleStrategy
from src.strategies.csl_explore.strategies.completeness import CompletenessStrategy
from src.strategies.csl_explore.strategies.dog_basket import DogBasketStrategy
from src.strategies.csl_explore.strategies.fingerprint import FingerprintStrategy
from src.strategies.csl_explore.strategies.narrative import NarrativeStrategy
from src.strategies.csl_explore.strategies.time_lag import TimeLagStrategy
from src.strategies.csl_explore.ledger import ExploreLedger


def _mkt(qid: str, q: str, yes: float) -> BinaryMarket:
    return BinaryMarket(
        condition_id=qid,
        question=q,
        yes_price=yes,
        no_price=1 - yes,
        yes_token=f"y{qid}",
    )


def _match(**kwargs) -> MatchBundle:
    base = dict(
        slug="chi-test",
        title="Home FC vs. Away FC",
        start_ts=None,
        volume=100.0,
        home_win=_mkt("h", "Will Home FC win on 2026-09-05?", 0.60),
        away_win=_mkt("a", "Will Away FC win on 2026-09-05?", 0.20),
        draw=_mkt("d", "Will Home FC vs. Away FC end in a draw?", 0.25),
    )
    base.update(kwargs)
    return MatchBundle(**base)


def test_fingerprint_finds_1_1():
    m = _match(
        exact_scores=[
            _mkt("e1", "Exact Score: Home FC 1-1 Away FC?", 0.12),
            _mkt("e2", "Exact Score: Home FC 2-1 Away FC?", 0.08),
        ]
    )
    sigs = FingerprintStrategy(score="1-1").generate(m, context={})
    assert len(sigs) == 1
    assert sigs[0].action == "buy_yes"
    assert sigs[0].condition_id == "e1"


def test_completeness_gap_emits_three_legs():
    m = _match(
        home_win=_mkt("h", "Will Home FC win on 2026-09-05?", 0.30),
        draw=_mkt("d", "draw?", 0.30),
        away_win=_mkt("a", "Will Away FC win on 2026-09-05?", 0.30),
    )
    # sum=0.90 gap=0.10
    sigs = CompletenessStrategy(min_gap=0.03).generate(m, context={})
    buys = [s for s in sigs if s.action == "buy_yes"]
    assert len(buys) == 3


def test_completeness_no_gap_skips():
    m = _match()  # 0.60+0.25+0.20=1.05
    sigs = CompletenessStrategy(min_gap=0.03).generate(m, context={})
    assert sigs[0].action == "skip"


def test_narrative_favorite_plus_under():
    m = _match(
        totals=[_mkt("u", "Home vs Away: Under 2.5?", 0.48)],
    )
    sigs = NarrativeStrategy(favorite_min=0.55).generate(m, context={})
    buys = [s for s in sigs if s.action == "buy_yes"]
    assert len(buys) == 2
    assert any(s.meta.get("leg") == "favorite" for s in buys)
    assert any(s.meta.get("leg") == "under" for s in buys)


def test_time_lag_draw_rule():
    m = _match()
    sigs = TimeLagStrategy(draw_minute=70).generate(
        m,
        context={"live": {"chi-test": {"minute": 75, "home_goals": 0, "away_goals": 0}}},
    )
    assert sigs[0].action == "buy_yes"
    assert "draw" in sigs[0].reason


def test_dog_basket_picks_underdog():
    m = _match()
    sigs = DogBasketStrategy().generate(m, context={})
    assert sigs[0].action == "buy_yes"
    assert sigs[0].condition_id == "a"


def test_anti_whale_triggers_on_drop(tmp_path):
    led = ExploreLedger(tmp_path / "led.jsonl", tmp_path / "st.json")
    # seed older mid high
    import time
    st = {"mids": {"h": [{"t": time.time() - 60, "p": 0.70}]}, "spent_usdc": 0.0}
    led.save_state(st)
    m = _match(home_win=_mkt("h", "Will Home FC win on 2026-09-05?", 0.55))
    sigs = AntiWhaleStrategy(drop_pct=0.08, lookback_s=600).generate(
        m, context={"ledger": led}
    )
    assert sigs[0].action == "buy_yes"
    assert "anti_whale" in sigs[0].reason


def test_min_share_count():
    from src.strategies.csl_explore.executor import min_share_count

    assert min_share_count(0.50, usdc=1.01) >= 5
    assert min_share_count(0.50, usdc=1.01) * 0.50 >= 1.01 - 1e-6
    assert min_share_count(0.20, usdc=1.01) >= 6


def test_stop_loss_triggered():
    from src.strategies.csl_explore.stops import stop_loss_triggered

    assert stop_loss_triggered(0.50, 0.25, stop_loss_pct=0.50) is True  # -50%
    assert stop_loss_triggered(0.50, 0.26, stop_loss_pct=0.50) is False
    assert stop_loss_triggered(0.70, 0.35, stop_loss_pct=0.50) is True
    assert stop_loss_triggered(0.70, 0.36, stop_loss_pct=0.50) is False


def test_bootstrap_opens_from_fills(tmp_path):
    from src.strategies.csl_explore.ledger import ExploreLedger

    led_path = tmp_path / "led.jsonl"
    st_path = tmp_path / "st.json"
    led = ExploreLedger(led_path, st_path)
    led.append({
        "kind": "fill",
        "condition_id": "0xabc",
        "strategy": "narrative",
        "shares": 5,
        "fill_price": 0.50,
        "match_title": "A vs B",
        "question": "draw?",
        "yes_token": "tok",
        "cost_usdc": 2.5,
    })
    opens = led.open_positions()
    assert len(opens) == 1
    assert opens[0]["entry_price"] == 0.50
    led.mark_closed("0xabc", exit_price=0.40, reason="stop_loss")
    assert led.open_positions() == []
