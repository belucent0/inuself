"""State Machine 모듈.

중앙화된 상태 관리를 제공하며, 유효한 상태 전이만 허용합니다.
"""

from .base import (
    BaseStateMachine,
    StateInfo,
    TransitionContext,
    TransitionResult,
)
from .exceptions import (
    InvalidTransitionError,
    StateMachineError,
)

__all__ = [
    "BaseStateMachine",
    "StateInfo",
    "TransitionContext",
    "TransitionResult",
    "InvalidTransitionError",
    "StateMachineError",
]
