"""E2E 테스트 Pydantic 모델.

fixture 스키마 및 테스트 결과 타입 정의.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModeAssertion(BaseModel):
    """허용되는 AI 응답 모드 목록."""

    expected: list[str] = Field(..., description="허용 모드 (OR 로직)")


class ContextCheck(BaseModel):
    """이전 턴 맥락 참조 검증."""

    must_reference_any: list[str] = Field(
        ..., description="이 중 하나 이상이 응답에 포함되어야 함"
    )


class TurnAssertions(BaseModel):
    """단일 턴의 검증 조건."""

    mode: ModeAssertion | None = None
    content_contains_any: list[str] = Field(
        default_factory=list, description="이 중 하나 이상이 포함되어야 함 (OR 로직)"
    )
    content_not_contains: list[str] = Field(
        default_factory=list, description="이 중 어느 것도 포함되어선 안 됨"
    )
    context_check: ContextCheck | None = None
    no_error: bool = True
    max_response_time_seconds: float = 60.0


class TurnDefinition(BaseModel):
    """단일 턴 정의."""

    turn: int
    query: str
    request_mode: str = "auto"
    assertions: TurnAssertions


class TestSuite(BaseModel):
    """멀티턴 테스트 시나리오 묶음."""

    id: str
    name: str
    turns: list[TurnDefinition]


class MultiTurnFixtures(BaseModel):
    """chat_multiturn_cases.json 최상위 스키마."""

    test_suites: list[TestSuite]


class TurnResult(BaseModel):
    """단일 턴 실행 결과."""

    full_content: str
    mode_used: str
    had_error: bool
    elapsed_seconds: float
