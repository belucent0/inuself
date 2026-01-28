"""Reflector 노드.

생성된 응답의 품질을 검증하고 필요시 재생성을 요청하는 노드입니다.
"""
from __future__ import annotations

from loguru import logger
import time
from typing import Any

from ..state import GraphState, ThinkingStep
from ..tools.llm_client import async_llm_completion



# 품질 검증 프롬프트
REFLECTION_PROMPT = """다음 응답의 품질을 평가해주세요.

원본 질문: {query}

생성된 응답:
{response}

다음 기준으로 1-10점으로 평가하고, JSON 형식으로 응답하세요:
- relevance: 질문에 대한 관련성 (1-10)
- completeness: 답변의 완전성 (1-10)
- accuracy: 정보의 정확성 (1-10)
- clarity: 표현의 명확성 (1-10)

또한 개선이 필요한 부분이 있다면 feedback에 작성하세요.

응답 형식 (JSON만):
{{"relevance": 8, "completeness": 7, "accuracy": 9, "clarity": 8, "average": 8.0, "needs_revision": false, "feedback": ""}}"""


class ReflectorNode:
    """응답 품질을 검증하는 노드."""

    def __init__(self, settings: Any, quality_threshold: float = 6.0):
        """초기화.

        Args:
            settings: 애플리케이션 설정
            quality_threshold: 재생성 요청 임계값 (기본: 6.0)
        """
        self.settings = settings
        self.quality_threshold = quality_threshold

    async def __call__(self, state: GraphState) -> dict:
        """품질 검증 실행.

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        query = state["query"]
        response = state.get("response", "")
        thinking_steps = list(state.get("thinking_steps", []))

        # 응답이 없으면 검증 스킵
        if not response:
            logger.warning("[Reflector] No response to validate")
            return {"thinking_steps": thinking_steps}

        # 짧은 응답은 검증 스킵 (간단한 인사 등)
        if len(response) < 50:
            logger.info("[Reflector] Short response, skipping validation")
            return {"thinking_steps": thinking_steps}

        thinking_steps.append(ThinkingStep(
            step="reflection_start",
            content="응답 품질 검증 중...",
            timestamp=time.time()
        ))

        try:
            # 품질 평가 요청
            prompt = REFLECTION_PROMPT.format(query=query, response=response[:2000])
            evaluation = await async_llm_completion(
                settings=self.settings,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
            )

            # 평가 결과 파싱
            quality_score, needs_revision, feedback = self._parse_evaluation(evaluation)

            logger.info(f"[Reflector] Quality score: {quality_score:.1f}, needs_revision: {needs_revision}")

            thinking_steps.append(ThinkingStep(
                step="reflection_complete",
                content=f"품질 점수: {quality_score:.1f}/10" + (f" (피드백: {feedback[:100]})" if feedback else ""),
                timestamp=time.time()
            ))

            return {
                "thinking_steps": thinking_steps,
                "metadata": {
                    **state.get("metadata", {}),
                    "quality_score": quality_score,
                    "needs_revision": needs_revision,
                    "feedback": feedback,
                }
            }

        except Exception as e:
            logger.warning(f"[Reflector] Validation failed: {e}")
            thinking_steps.append(ThinkingStep(
                step="reflection_error",
                content=f"품질 검증 실패 (무시됨): {str(e)[:50]}",
                timestamp=time.time()
            ))
            return {"thinking_steps": thinking_steps}

    def _parse_evaluation(self, evaluation: str) -> tuple[float, bool, str]:
        """평가 결과 파싱.

        Args:
            evaluation: LLM 평가 응답

        Returns:
            (평균 점수, 재생성 필요 여부, 피드백)
        """
        import json
        import re

        try:
            # JSON 추출
            json_match = re.search(r'\{[^}]+\}', evaluation)
            if json_match:
                data = json.loads(json_match.group())

                # 평균 점수 계산
                scores = [
                    data.get("relevance", 7),
                    data.get("completeness", 7),
                    data.get("accuracy", 7),
                    data.get("clarity", 7),
                ]
                average = sum(scores) / len(scores)

                needs_revision = data.get("needs_revision", average < self.quality_threshold)
                feedback = data.get("feedback", "")

                return average, needs_revision, feedback

        except (json.JSONDecodeError, AttributeError):
            pass

        # 파싱 실패 시 기본값
        return 7.0, False, ""
