"""
BaseEvaluator 추상 클래스

Template Method Pattern을 사용하여 평가 로직의 공통 부분을 추상화
"""

from abc import ABC, abstractmethod
import time

from ..core.interfaces import IEvaluator, ConversationData, EvaluationResult


class BaseEvaluator(IEvaluator):
    """
    평가자 추상 기본 클래스

    Template Method Pattern:
    - evaluate() 메서드에서 공통 흐름 정의
    - _calculate_score()는 서브클래스에서 구현
    - _find_issues()는 서브클래스에서 선택적으로 오버라이드
    """

    def __init__(self, threshold: float = 50.0):
        """
        Args:
            threshold: 통과 기준 점수 (0.0 - 100.0)
        """
        if not 0 <= threshold <= 100:
            raise ValueError(f"Threshold must be between 0 and 100, got {threshold}")

        self._threshold = threshold

    @property
    def name(self) -> str:
        """평가자 이름 (클래스명에서 'Evaluator' 제거)"""
        return self.__class__.__name__.replace("Evaluator", "")

    @abstractmethod
    def _calculate_score(self, conversation: ConversationData) -> tuple[float, dict]:
        """
        점수 계산 로직 (서브클래스에서 구현)

        Args:
            conversation: 평가할 대화

        Returns:
            (score, metrics) 튜플
            - score: 0.0 - 100.0
            - metrics: 상세 메트릭 딕셔너리
        """
        pass

    def evaluate(self, conversation: ConversationData) -> EvaluationResult:
        """
        대화 평가 (템플릿 메서드)

        Args:
            conversation: 평가할 대화

        Returns:
            EvaluationResult 객체
        """
        # 1. 점수 계산
        score, metrics = self._calculate_score(conversation)

        # 2. 점수 범위 검증
        score = max(0.0, min(100.0, score))

        # 3. 통과 여부 판정
        passed = score >= self._threshold

        # 4. 이슈 추출 (실패한 경우만)
        issues = self._find_issues(conversation, metrics) if not passed else []

        # 5. 결과 생성
        return EvaluationResult(
            conversation_id=conversation.conversation_id,
            evaluator_name=self.name,
            score=score,
            metrics=metrics,
            passed=passed,
            issues=issues,
            timestamp=time.time()
        )

    def get_threshold(self) -> float:
        """통과 기준 점수"""
        return self._threshold

    def _find_issues(self, conversation: ConversationData, metrics: dict) -> list[str]:
        """
        이슈 목록 추출 (서브클래스에서 오버라이드 가능)

        Args:
            conversation: 평가한 대화
            metrics: 계산된 메트릭

        Returns:
            이슈 설명 문자열 리스트
        """
        return []
