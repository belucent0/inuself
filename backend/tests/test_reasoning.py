import pytest

from app.core.reasoning import REASONING_DISPLAY_MAP, resolve_reasoning, routing_profile


@pytest.mark.parametrize("value", ["none", "low", "medium", "high"])
def test_explicit_reasoning_wins(value):
    assert resolve_reasoning(value, "reasoning", 9000) == value


def test_auto_reasoning_resolution_order_and_context_boundary():
    assert resolve_reasoning("auto", "reasoning", 0) == "high"
    assert resolve_reasoning("auto", "simple", 3000) == "none"
    assert resolve_reasoning("auto", "simple", 3001) == "medium"
    for mode in ("search", "rag", "hybrid"):
        assert resolve_reasoning("auto", mode, 0) == "medium"
    assert resolve_reasoning("auto", "simple", 0) == "none"


def test_reasoning_display_and_profile_contract():
    assert REASONING_DISPLAY_MAP == {
        "none": "일반",
        "low": "일반",
        "medium": "일반",
        "high": "심층",
    }
    assert routing_profile("chat", "high", True) == {
        "workload": "chat",
        "reasoning": "high",
        "execution_scope": "remote_allowed",
    }
