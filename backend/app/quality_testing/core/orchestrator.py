"""
TestOrchestrator - 품질 테스트 실행 엔진

Single Responsibility Principle: 테스트 실행 흐름 관리만 담당
Dependency Inversion: 추상 인터페이스에 의존
"""

from typing import List, Optional
import uuid
import time

from .interfaces import (
    IConversationLoader,
    IEvaluator,
    IMasker,
    IReporter,
    ConversationData,
    EvaluationResult,
    TestReport
)
from .config import QualityTestConfig


class TestOrchestrator:
    """
    품질 테스트 실행 엔진

    책임:
    - 대화 로드 조율
    - 평가 파이프라인 실행
    - 리포트 생성 조율

    Dependency Injection을 통해 구체적인 구현체를 주입받음.
    """

    def __init__(
        self,
        config: QualityTestConfig,
        loader: IConversationLoader,
        evaluators: List[IEvaluator],
        masker: Optional[IMasker] = None,
        reporters: Optional[List[IReporter]] = None
    ):
        """
        Args:
            config: 테스트 설정
            loader: 대화 로더 구현체
            evaluators: 평가자 구현체 리스트
            masker: 마스킹 구현체 (선택)
            reporters: 리포터 구현체 리스트 (선택)
        """
        self.config = config
        self.loader = loader
        self.evaluators = evaluators
        self.masker = masker
        self.reporters = reporters or []

        if config.verbose:
            print(f"[INFO] TestOrchestrator 초기화 완료")
            print(f"   - 평가자: {len(evaluators)}개")
            print(f"   - 리포터: {len(self.reporters)}개")
            print(f"   - 마스킹: {'활성화' if masker else '비활성화'}")

    def run_test(
        self,
        conversation_ids: Optional[List[str]] = None
    ) -> TestReport:
        """
        품질 테스트 실행

        Args:
            conversation_ids: 테스트할 대화 ID 목록 (None이면 전체 또는 max_conversations까지)

        Returns:
            TestReport 객체
        """
        if self.config.verbose:
            print(f"\n{'='*60}")
            print(f"[START] 품질 테스트 시작")
            print(f"{'='*60}")

        # 1. 대화 로드
        if self.config.verbose:
            print(f"\n[STEP 1/5] 대화 로드 중...")

        if conversation_ids is None:
            all_ids = self.loader.list_all_conversations()
            if self.config.verbose:
                print(f"   - 전체 대화: {len(all_ids)}개")

            if self.config.max_conversations:
                conversation_ids = all_ids[:self.config.max_conversations]
                if self.config.verbose:
                    print(f"   - 제한 적용: {len(conversation_ids)}개 테스트")
            else:
                conversation_ids = all_ids
        else:
            if self.config.verbose:
                print(f"   - 지정된 대화: {len(conversation_ids)}개")

        conversations = self.loader.load_conversations(conversation_ids)
        if self.config.verbose:
            print(f"   [OK] {len(conversations)}개 대화 로드 완료")

        if not conversations:
            if self.config.verbose:
                print(f"   [WARN]  로드된 대화가 없습니다. 테스트를 종료합니다.")
            return self._create_empty_report()

        # 2. 마스킹 적용 (선택)
        if self.masker:
            if self.config.verbose:
                print(f"\n[STEP 2/5] Step 2/5: 마스킹 적용 중...")

            masked_conversations = []
            for conv in conversations:
                masked_conv = self.masker.mask_conversation(conv)
                masked_conversations.append(masked_conv)

            conversations = masked_conversations
            if self.config.verbose:
                print(f"   [OK] 마스킹 완료")
        else:
            if self.config.verbose:
                print(f"\n[SKIP]  Step 2/5: 마스킹 건너뜀 (비활성화)")

        # 3. 평가 실행
        if self.config.verbose:
            print(f"\n[STEP 3/5] Step 3/5: 평가 실행 중...")

        all_evaluations = []
        for i, conversation in enumerate(conversations, 1):
            if self.config.verbose:
                print(f"\n   대화 {i}/{len(conversations)}: {conversation.conversation_id[:8]}...")

            for evaluator in self.evaluators:
                try:
                    result = evaluator.evaluate(conversation)
                    all_evaluations.append(result)

                    if self.config.verbose:
                        status = "[OK]" if result.passed else "[FAIL]"
                        print(f"     {status} {evaluator.name}: {result.score:.1f}점")

                except Exception as e:
                    if self.config.verbose:
                        print(f"     [WARN]  {evaluator.name}: 평가 실패 - {str(e)}")

        if self.config.verbose:
            print(f"\n   [OK] 전체 평가 {len(all_evaluations)}개 완료")

        # 4. 리포트 생성
        if self.config.verbose:
            print(f"\n[STEP 4/5] Step 4/5: 리포트 생성 중...")

        report = TestReport(
            test_run_id=str(uuid.uuid4()),
            timestamp=time.time(),
            conversations_tested=len(conversations),
            evaluations=all_evaluations,
            summary=self._generate_summary(all_evaluations),
            config=self.config.model_dump()
        )

        # 5. 리포트 저장
        if self.config.verbose:
            print(f"\n[STEP 5/5] Step 5/5: 리포트 저장 중...")

        for reporter in self.reporters:
            try:
                file_path = reporter.generate_report(report)
                if self.config.verbose:
                    print(f"   [OK] {file_path}")
            except Exception as e:
                if self.config.verbose:
                    print(f"   [WARN]  리포트 저장 실패: {str(e)}")

        if self.config.verbose:
            print(f"\n{'='*60}")
            print(f"[OK] 품질 테스트 완료!")
            print(f"{'='*60}\n")

        return report

    def _generate_summary(self, evaluations: List[EvaluationResult]) -> dict:
        """
        평가 결과 요약 생성

        Args:
            evaluations: 평가 결과 리스트

        Returns:
            요약 딕셔너리
        """
        if not evaluations:
            return {
                "total_evaluations": 0,
                "passed": 0,
                "failed": 0,
                "pass_rate": 0.0,
                "by_evaluator": {}
            }

        total = len(evaluations)
        passed = sum(1 for e in evaluations if e.passed)

        # 평가자별 통계
        by_evaluator = {}
        for eval_result in evaluations:
            name = eval_result.evaluator_name
            if name not in by_evaluator:
                by_evaluator[name] = {
                    "total": 0,
                    "passed": 0,
                    "scores": []
                }
            by_evaluator[name]["total"] += 1
            by_evaluator[name]["passed"] += 1 if eval_result.passed else 0
            by_evaluator[name]["scores"].append(eval_result.score)

        # 평균 점수 계산
        for name, stats in by_evaluator.items():
            stats["avg_score"] = sum(stats["scores"]) / len(stats["scores"])
            stats["pass_rate"] = stats["passed"] / stats["total"] * 100
            # scores는 리포트에 포함하지 않음 (용량 절약)
            del stats["scores"]

        return {
            "total_evaluations": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total * 100 if total > 0 else 0,
            "by_evaluator": by_evaluator
        }

    def _create_empty_report(self) -> TestReport:
        """빈 리포트 생성 (대화가 없는 경우)"""
        return TestReport(
            test_run_id=str(uuid.uuid4()),
            timestamp=time.time(),
            conversations_tested=0,
            evaluations=[],
            summary={
                "total_evaluations": 0,
                "passed": 0,
                "failed": 0,
                "pass_rate": 0.0,
                "by_evaluator": {}
            },
            config=self.config.model_dump()
        )
