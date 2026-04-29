"""Tests for O-SCR-01: ScoringEngine.from_user() reads UserNode.scoring_weights.

Verifies that the factory method picks up per-user learned weights instead of
always using hardcoded defaults.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graphclaw.models.nodes import ScoringWeights, UserNode
from graphclaw.scoring.engine import ScoringEngine


def _utc():
    return datetime.now(timezone.utc)


def _user(weights: ScoringWeights | None = None) -> UserNode:
    user = UserNode(
        id="USER-scr01-test",
        name="SCR01 Tester",
        email="scr01@example.com",
        created_at=_utc(),
        updated_at=_utc(),
    )
    if weights is not None:
        user.scoring_weights = weights
    return user


# ---------------------------------------------------------------------------
# from_user() factory method
# ---------------------------------------------------------------------------


async def test_from_user_uses_custom_weights():
    """from_user() sets engine weights from UserNode.scoring_weights."""
    user = _user(
        ScoringWeights(
            W1_timeline=0.40,
            W2_dependencies=0.15,
            W3_critical_path=0.15,
            W4_blocker=0.12,
            W5_override=0.08,
            W6_resource_risk=0.06,
            W7_constraint=0.04,
        )
    )
    engine = ScoringEngine.from_user(user)
    assert engine.w1 == pytest.approx(0.40)
    assert engine.w2 == pytest.approx(0.15)
    assert engine.w3 == pytest.approx(0.15)
    assert engine.w4 == pytest.approx(0.12)
    assert engine.w5 == pytest.approx(0.08)
    assert engine.w6 == pytest.approx(0.06)
    assert engine.w7 == pytest.approx(0.04)


async def test_from_user_default_weights_when_scoring_weights_all_zero():
    """If all learned weights are 0.0 (new user), fall back to PRD defaults."""
    user = _user(
        ScoringWeights(
            W1_timeline=0.0,
            W2_dependencies=0.0,
            W3_critical_path=0.0,
            W4_blocker=0.0,
            W5_override=0.0,
            W6_resource_risk=0.0,
            W7_constraint=0.0,
        )
    )
    engine = ScoringEngine.from_user(user)
    assert engine.w1 == pytest.approx(0.25)  # PRD default
    assert engine.w2 == pytest.approx(0.20)
    assert engine.w3 == pytest.approx(0.20)
    assert engine.w4 == pytest.approx(0.15)
    assert engine.w5 == pytest.approx(0.10)
    assert engine.w6 == pytest.approx(0.05)
    assert engine.w7 == pytest.approx(0.05)


async def test_from_user_default_scoring_weights_model():
    """User with default ScoringWeights gets PRD default engine weights."""
    user = _user()  # ScoringWeights() has all defaults = 0.25, 0.20, etc.
    engine = ScoringEngine.from_user(user)
    assert engine.w1 == pytest.approx(0.25)
    assert engine.w2 == pytest.approx(0.20)
    assert engine.w3 == pytest.approx(0.20)


async def test_from_user_partial_custom_weights():
    """Partial customisation: non-zero weights used, zero weight falls back."""
    user = _user(
        ScoringWeights(
            W1_timeline=0.50,  # custom
            W2_dependencies=0.0,  # unlearned → default 0.20
            W3_critical_path=0.18,
            W4_blocker=0.0,  # unlearned → default 0.15
            W5_override=0.08,
            W6_resource_risk=0.04,
            W7_constraint=0.0,  # unlearned → default 0.05
        )
    )
    engine = ScoringEngine.from_user(user)
    assert engine.w1 == pytest.approx(0.50)
    assert engine.w2 == pytest.approx(0.20)  # fell back to default
    assert engine.w3 == pytest.approx(0.18)
    assert engine.w4 == pytest.approx(0.15)  # fell back to default
    assert engine.w5 == pytest.approx(0.08)
    assert engine.w7 == pytest.approx(0.05)  # fell back to default


async def test_from_user_without_scoring_weights_attribute():
    """If user object lacks scoring_weights, fall back to PRD defaults."""

    class MinimalUser:
        pass  # no scoring_weights attribute

    engine = ScoringEngine.from_user(MinimalUser())
    assert engine.w1 == pytest.approx(0.25)
    assert engine.w7 == pytest.approx(0.05)


async def test_from_user_returns_scoring_engine_instance():
    """from_user() must return a ScoringEngine, not a subclass or None."""
    user = _user()
    engine = ScoringEngine.from_user(user)
    assert type(engine) is ScoringEngine


async def test_direct_constructor_weights_unchanged():
    """Original constructor still works with explicit weight args."""
    engine = ScoringEngine(w1=0.35, w2=0.18)
    assert engine.w1 == pytest.approx(0.35)
    assert engine.w2 == pytest.approx(0.18)
    assert engine.w3 == pytest.approx(0.20)  # default
