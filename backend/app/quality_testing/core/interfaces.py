"""
추상 인터페이스 및 데이터 모델 정의

SOLID 원칙을 준수하여 설계:
- Interface Segregation: 각 컴포넌트별 독립적인 인터페이스
- Dependency Inversion: 구체적인 구현이 아닌 추상화에 의존
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ==================== 데이터 모델 ====================

class ConversationData(BaseModel):
    """대화 데이터 모델"""
    conversation_id: str
    title: str
    messages: List[Dict[str, Any]]
    created_at: float
    updated_at: float
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        arbitrary_types_allowed = True


class EvaluationResult(BaseModel):
    """평가 결과 모델"""
    conversation_id: str
    evaluator_name: str
    score: float = Field(ge=0.0, le=100.0, description="평가 점수 (0-100)")
    metrics: Dict[str, Any] = Field(default_factory=dict)
    passed: bool
    issues: List[str] = Field(default_factory=list)
    timestamp: float

    class Config:
        arbitrary_types_allowed = True


class TestReport(BaseModel):
    """테스트 리포트 모델"""
    test_run_id: str
    timestamp: float
    conversations_tested: int
    evaluations: List[EvaluationResult]
    summary: Dict[str, Any]
    config: Dict[str, Any]

    class Config:
        arbitrary_types_allowed = True


# ==================== 인터페이스 ====================

class IConversationLoader(ABC):
    """
    대화 로더 인터페이스

    다양한 소스(Redis, File, DB)로부터 대화 데이터를 로드하는 추상 인터페이스.
    Open/Closed Principle: 새로운 로더 추가 시 이 인터페이스를 구현하여 확장.
    """

    @abstractmethod
    def load_conversation(self, conversation_id: str) -> ConversationData:
        """
        단일 대화 로드

        Args:
            conversation_id: 대화 ID

        Returns:
            ConversationData 객체

        Raises:
            ValueError: 대화를 찾을 수 없는 경우
        """
        pass

    @abstractmethod
    def load_conversations(self, conversation_ids: List[str]) -> List[ConversationData]:
        """
        여러 대화 로드

        Args:
            conversation_ids: 대화 ID 목록

        Returns:
            ConversationData 객체 리스트
        """
        pass

    @abstractmethod
    def list_all_conversations(self) -> List[str]:
        """
        모든 대화 ID 조회

        Returns:
            대화 ID 리스트 (최근순 정렬)
        """
        pass


class IEvaluator(ABC):
    """
    평가자 인터페이스

    대화 품질을 평가하는 추상 인터페이스.
    Template Method Pattern: evaluate() 메서드에서 공통 로직 처리.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """평가자 이름"""
        pass

    @abstractmethod
    def evaluate(self, conversation: ConversationData) -> EvaluationResult:
        """
        대화 평가

        Args:
            conversation: 평가할 대화

        Returns:
            EvaluationResult 객체
        """
        pass

    @abstractmethod
    def get_threshold(self) -> float:
        """
        통과 기준 점수

        Returns:
            통과 기준 점수 (0.0 - 100.0)
        """
        pass


class IMasker(ABC):
    """
    마스킹 인터페이스

    민감정보를 마스킹/언마스킹하는 추상 인터페이스.
    Strategy Pattern: 다양한 마스킹 전략을 런타임에 교체 가능.
    """

    @abstractmethod
    def mask_conversation(self, conversation: ConversationData) -> ConversationData:
        """
        대화 마스킹 (민감정보 제거/대체)

        Args:
            conversation: 원본 대화

        Returns:
            마스킹된 대화
        """
        pass

    @abstractmethod
    def unmask_if_needed(self, conversation: ConversationData) -> ConversationData:
        """
        마스킹 해제 (필요 시)

        Args:
            conversation: 마스킹된 대화

        Returns:
            원본 대화
        """
        pass


class IReporter(ABC):
    """
    리포터 인터페이스

    테스트 리포트를 다양한 형식으로 생성하는 추상 인터페이스.
    Strategy Pattern: 리포트 형식을 런타임에 선택 가능.
    """

    @abstractmethod
    def generate_report(self, report: TestReport) -> str:
        """
        리포트 생성

        Args:
            report: TestReport 객체

        Returns:
            생성된 리포트 파일 경로
        """
        pass
