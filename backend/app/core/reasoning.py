"""RoutingProfile reasoning contract shared by Backend callers."""

from __future__ import annotations

from typing import Literal, TypedDict


ReasoningPreference = Literal["auto", "none", "low", "medium", "high"]
ResolvedReasoning = Literal["none", "low", "medium", "high"]
Workload = Literal["chat", "summary"]
ExecutionScope = Literal["local_only", "remote_allowed"]


class RoutingProfile(TypedDict):
    workload: Workload
    reasoning: ResolvedReasoning
    execution_scope: ExecutionScope


REASONING_DISPLAY_MAP: dict[ResolvedReasoning, str] = {
    "none": "일반",
    "low": "일반",
    "medium": "일반",
    "high": "심층",
}


def resolve_reasoning(
    requested: ReasoningPreference,
    mode: object,
    context_chars: int,
) -> ResolvedReasoning:
    """Resolve a UI preference to the concrete Gateway reasoning level."""
    if requested != "auto":
        return requested

    mode_name = str(getattr(mode, "value", mode) or "").lower()
    if mode_name == "reasoning":
        return "high"
    if context_chars > 3000:
        return "medium"
    if mode_name in {"search", "hybrid", "rag"}:
        return "medium"
    return "none"


def routing_profile(
    workload: Workload,
    reasoning: ResolvedReasoning,
    allow_remote: bool = False,
) -> RoutingProfile:
    """Build the Gateway RoutingProfile payload."""
    return {
        "workload": workload,
        "reasoning": reasoning,
        "execution_scope": "remote_allowed" if allow_remote else "local_only",
    }
