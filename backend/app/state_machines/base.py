"""State Machine 기본 클래스.

모든 상태 머신의 기반이 되는 추상 클래스와 데이터 타입을 정의합니다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Generic, TypeVar
from uuid import UUID

from loguru import logger

from .exceptions import InvalidTransitionError, TerminalStateError

StateT = TypeVar("StateT", bound=Enum)


@dataclass
class StateInfo:
    """상태의 메타정보."""

    name: str
    description: str
    is_terminal: bool = False
    timeout_minutes: int | None = None
    retryable: bool = False


@dataclass
class TransitionContext(Generic[StateT]):
    """상태 전이 컨텍스트.

    상태 전이에 필요한 모든 정보를 담습니다.
    """

    entity_id: UUID
    entity_type: str
    current_state: StateT
    target_state: StateT
    triggered_by: str = "system"
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class TransitionResult:
    """상태 전이 결과."""

    success: bool
    old_state: Enum | None
    new_state: Enum | None
    reason: str = ""


# Hook 타입 정의
TransitionHook = Callable[[TransitionContext], None]


class BaseStateMachine(ABC, Generic[StateT]):
    """모든 상태 머신의 기본 클래스.

    상속받아 TRANSITIONS와 STATE_INFO를 정의하면 됩니다.

    Example:
        class ContentStateMachine(BaseStateMachine[FileStatus]):
            TRANSITIONS = {
                FileStatus.QUEUED: [FileStatus.PROCESSING],
                FileStatus.PROCESSING: [FileStatus.COMPLETED, FileStatus.FAILED],
                ...
            }
    """

    # 상태 전이 규칙: {현재상태: [가능한_다음_상태들]}
    TRANSITIONS: dict[StateT, list[StateT]] = {}

    # 상태별 메타정보
    STATE_INFO: dict[StateT, StateInfo] = {}

    # 상태별 타임아웃 (분)
    TIMEOUTS: dict[StateT, int] = {}

    def __init__(self):
        self._pre_hooks: dict[tuple[StateT, StateT], list[TransitionHook]] = {}
        self._post_hooks: dict[tuple[StateT, StateT], list[TransitionHook]] = {}

    def get_valid_transitions(self, state: StateT) -> list[StateT]:
        """현재 상태에서 가능한 전이 상태 반환."""
        return self.TRANSITIONS.get(state, [])

    def is_terminal_state(self, state: StateT) -> bool:
        """터미널 상태인지 확인."""
        # 전이 가능한 상태가 없으면 터미널
        return len(self.get_valid_transitions(state)) == 0

    def get_timeout(self, state: StateT) -> int | None:
        """상태의 타임아웃(분) 반환."""
        return self.TIMEOUTS.get(state)

    def get_state_info(self, state: StateT) -> StateInfo | None:
        """상태의 메타정보 반환."""
        return self.STATE_INFO.get(state)

    def can_transition(
        self, current_state: StateT, target_state: StateT
    ) -> tuple[bool, str]:
        """전이 가능 여부 검사.

        Returns:
            (가능여부, 사유)
        """
        # 터미널 상태 체크
        if self.is_terminal_state(current_state):
            return False, f"Current state '{current_state.value}' is terminal"

        # 전이 규칙 체크
        valid_targets = self.get_valid_transitions(current_state)
        if target_state not in valid_targets:
            valid_str = ", ".join(s.value for s in valid_targets) or "none"
            return (
                False,
                f"Invalid transition: {current_state.value} → {target_state.value}. "
                f"Valid targets: [{valid_str}]",
            )

        return True, ""

    def validate_transition(self, ctx: TransitionContext[StateT]) -> None:
        """전이 유효성 검증. 실패 시 예외 발생."""
        can_transit, reason = self.can_transition(ctx.current_state, ctx.target_state)
        if not can_transit:
            raise InvalidTransitionError(
                current_state=ctx.current_state,
                target_state=ctx.target_state,
                reason=reason,
                entity_id=ctx.entity_id,
            )

    def register_hook(
        self,
        source: StateT,
        target: StateT,
        hook: TransitionHook,
        hook_type: str = "post",
    ) -> None:
        """상태 전이 전/후 훅 등록.

        Args:
            source: 시작 상태
            target: 목표 상태
            hook: 실행할 콜백 함수
            hook_type: "pre" 또는 "post"
        """
        key = (source, target)
        hooks_dict = self._pre_hooks if hook_type == "pre" else self._post_hooks
        if key not in hooks_dict:
            hooks_dict[key] = []
        hooks_dict[key].append(hook)

    async def _execute_hooks(
        self, ctx: TransitionContext[StateT], hook_type: str
    ) -> None:
        """등록된 훅 실행."""
        key = (ctx.current_state, ctx.target_state)
        hooks_dict = self._pre_hooks if hook_type == "pre" else self._post_hooks
        hooks = hooks_dict.get(key, [])

        for hook in hooks:
            try:
                result = hook(ctx)
                # 비동기 훅 지원
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error(
                    f"Hook execution failed: {hook.__name__} "
                    f"({ctx.current_state.value} → {ctx.target_state.value}): {e}"
                )
                # 훅 실패는 전이를 막지 않음 (로깅만)

    def create_result(
        self,
        success: bool,
        old_state: StateT | None = None,
        new_state: StateT | None = None,
        reason: str = "",
    ) -> TransitionResult:
        """TransitionResult 생성 헬퍼."""
        return TransitionResult(
            success=success,
            old_state=old_state,
            new_state=new_state,
            reason=reason,
        )

    def log_transition(self, ctx: TransitionContext[StateT], success: bool) -> None:
        """상태 전이 로깅."""
        if success:
            logger.info(
                f"[StateMachine] {ctx.entity_type}({ctx.entity_id}) "
                f"{ctx.current_state.value} → {ctx.target_state.value} "
                f"[triggered_by={ctx.triggered_by}]"
            )
        else:
            logger.warning(
                f"[StateMachine] {ctx.entity_type}({ctx.entity_id}) "
                f"FAILED: {ctx.current_state.value} → {ctx.target_state.value} "
                f"[triggered_by={ctx.triggered_by}]"
            )
