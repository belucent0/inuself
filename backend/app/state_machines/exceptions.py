"""State Machine 예외 클래스."""

from enum import Enum
from typing import Any


class StateMachineError(Exception):
    """State Machine 기본 예외."""

    pass


class InvalidTransitionError(StateMachineError):
    """유효하지 않은 상태 전이 예외."""

    def __init__(
        self,
        current_state: Enum,
        target_state: Enum,
        reason: str = "",
        entity_id: Any = None,
    ):
        self.current_state = current_state
        self.target_state = target_state
        self.entity_id = entity_id
        self.reason = reason

        message = f"Invalid transition: {current_state.value} → {target_state.value}"
        if reason:
            message += f" ({reason})"
        if entity_id:
            message += f" [entity_id={entity_id}]"

        super().__init__(message)


class TerminalStateError(StateMachineError):
    """터미널 상태에서 전이 시도 예외."""

    def __init__(self, current_state: Enum, entity_id: Any = None):
        self.current_state = current_state
        self.entity_id = entity_id

        message = f"Cannot transition from terminal state: {current_state.value}"
        if entity_id:
            message += f" [entity_id={entity_id}]"

        super().__init__(message)
