"""새로운 LangGraph 기반 섹션 생성 Executor.

Send API와 조건부 엣지를 활용한 병렬 섹션 생성을 제공합니다.
기존 PhaseExecutor를 완전히 대체합니다.

변경 이력:
    v2.0: PhaseExecutor.execute() 호환 execute() 메서드 추가
"""

import asyncio
import json
import re
from typing import List, Dict, Tuple, Any, Optional, Callable
from dataclasses import dataclass
import time

from ..core.config import Settings, get_settings
from ..core.logging import logger
from .section_state import create_initial_state, SectionGenerationState
from .section_graph import get_section_graph
from .litellm_client import request_litellm_completion, request_litellm_completion_async
from ..prompts.summary import (
    PHASE1_STRUCTURE_TEMPLATE_V2,
    PHASE2_SUMMARY_TEMPLATE,
    SUMMARY_SYSTEM_PROMPT,
)


@dataclass
class PhaseResult:
    """단계 실행 결과"""

    success: bool
    data: Dict[str, Any]
    error: Optional[str] = None
    attempts: int = 0


class PhaseExecutionError(Exception):
    """Phase 실행 실패 예외"""

    pass


class SectionGraphExecutor:
    """LangGraph 기반 상세 섹션 생성 Executor.

    TOC 주제별로 병렬로 상세 섹션을 생성하며, 길이 검증 실패 시
    자동으로 재시도(최대 3회)를 수행합니다.

    Attributes:
        settings: 설정 객체

    Example:
        >>> executor = SectionGraphExecutor()
        >>> sections, logs = await executor.generate_sections(
        ...     toc=["주제1", "주제2", "주제3"],
        ...     transcript="원본 텍스트...",
        ...     keywords=["키워드1", "키워드2"],
        ...     title="제목"
        ... )
    """

    def __init__(
        self,
        settings: Settings = None,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ):
        """Executor를 초기화합니다.

        Args:
            settings: 설정 객체. None이면 기본 설정 사용.
            on_progress: 진행률 콜백 (progress: 0-100, message: str)
        """
        self.settings = settings or get_settings()
        self.graph = get_section_graph(self.settings)
        self.max_retries = 3
        self.retry_delay = 1.0  # 고정 1초
        self._on_progress = on_progress
        logger.info("[SectionGraphExecutor] 초기화 완료")

    def _emit(self, progress: float, message: str):
        """진행률 콜백이 있으면 호출합니다."""
        if self._on_progress:
            self._on_progress(progress, message)

    async def execute(self, text: str) -> Tuple[str, str]:
        """전체 2단계 요약 실행 (PhaseExecutor 호환).

        Phase 1: 메타데이터 추출 (제목, 키워드, 목차)
        Phase 2: 병렬 섹션 생성 (LangGraph 기반)

        Args:
            text: 요약할 원본 텍스트

        Returns:
            (title, summary_md) 튜플

        Raises:
            PhaseExecutionError: 모든 재시도 실패 시
        """
        logger.info("[SectionGraphExecutor] 2단계 요약 시작")
        self._emit(5, "메타데이터 추출 중...")

        # Phase 1: 메타데이터 추출
        phase1_result = await self._execute_phase_1(text)
        if not phase1_result.success:
            raise PhaseExecutionError(f"Phase 1 failed: {phase1_result.error}")

        metadata = phase1_result.data
        title = metadata.get("title", "요약")
        keywords = metadata.get("keywords", [])
        toc = metadata.get("toc", [])
        logger.info(
            f"[SectionGraphExecutor] Phase 1 완료: "
            f"title='{title[:50]}...', keywords={len(keywords)}, toc={len(toc)}"
        )
        self._emit(20, f"구조 분석 완료, 섹션 생성 시작 ({len(toc)}개)")

        # Phase 2: 병렬 섹션 생성
        def _section_progress(completed: int, total: int):
            progress = 20 + (completed / total) * 70
            self._emit(round(progress, 1), f"섹션 생성 중 ({completed}/{total})")

        if toc:
            sections, detailed_md, logs = await self.generate_sections(
                toc=toc,
                transcript=text,
                keywords=keywords,
                title=title,
                max_retries=self.max_retries,
                progress_callback=_section_progress,
            )
        else:
            sections = {}
            detailed_md = ""
            logger.warning(
                "[SectionGraphExecutor] TOC가 비어있어 섹션 생성을 건너뜁니다"
            )

        self._emit(92, "요약 조합 중...")

        # 핵심 요약 생성 (상세 섹션 내용에서 추출)
        core_summary = self._generate_core_summary(metadata, sections)

        # 최종 마크다운 조합
        summary_md = self._combine_to_markdown(metadata, core_summary, sections)

        logger.info(
            f"[SectionGraphExecutor] 모든 단계 완료: "
            f"summary_length={len(summary_md)}, sections={len(sections)}"
        )

        return title, summary_md

    async def _execute_phase_1(self, text: str) -> PhaseResult:
        """Phase 1: 메타데이터 추출 (tier-simple).

        Args:
            text: 원본 텍스트

        Returns:
            PhaseResult 객체
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    f"[SectionGraphExecutor] Phase 1 시도 {attempt}/{self.max_retries}"
                )

                prompt = PHASE1_STRUCTURE_TEMPLATE_V2.format(transcript=text[:10000])
                messages = [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]

                # Phase 1도 tier-summarize 사용 (법률/전문 용어 처리 향상)
                response = await request_litellm_completion_async(
                    settings=self.settings,
                    model=self.settings.litellm_model_summarize,
                    messages=messages,
                )

                # JSON 파싱
                data = self._parse_json_response(response)

                # 검증
                if self._validate_phase_1(data):
                    # 파이프 구분 문자열을 리스트로 변환
                    keywords_str = data.get("keywords", "")
                    toc_str = data.get("toc", "")

                    return PhaseResult(
                        success=True,
                        data={
                            "title": data.get("title", ""),
                            "keywords": [
                                k.strip() for k in keywords_str.split("|") if k.strip()
                            ],
                            "toc": [t.strip() for t in toc_str.split("|") if t.strip()],
                        },
                        attempts=attempt,
                    )
                else:
                    logger.warning(
                        f"[SectionGraphExecutor] Phase 1 검증 실패 (시도 {attempt})"
                    )

            except Exception as e:
                logger.error(
                    f"[SectionGraphExecutor] Phase 1 오류 (시도 {attempt}): {e}"
                )

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay)

        return PhaseResult(
            success=False,
            data={},
            error="Phase 1 모든 재시도 실패",
            attempts=self.max_retries,
        )

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """LLM 응답에서 JSON을 추출합니다.

        Args:
            response: LLM 응답 문자열

        Returns:
            파싱된 JSON 딕셔너리

        Raises:
            ValueError: JSON 파싱 실패 시
        """
        # 코드 블록 제거
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\s*|\s*```$", "", text)

        # JSON 추출 시도
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 중괄호 패턴 찾기
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except (json.JSONDecodeError, AttributeError):
            pass

        raise ValueError(f"JSON 파싱 실패: {text[:200]}")

    def _validate_phase_1(self, data: Dict[str, Any]) -> bool:
        """Phase 1 결과를 검증합니다.

        Args:
            data: 파싱된 JSON 데이터

        Returns:
            검증 통과 여부
        """
        # 필수 필드 존재
        if not data.get("title"):
            return False

        keywords = data.get("keywords", "")
        toc = data.get("toc", "")

        # 최소 1개 아이템 (파이프로 구분)
        if not keywords or not keywords.strip():
            return False

        if not toc or not toc.strip():
            return False

        return True

    def _generate_core_summary(
        self, metadata: Dict[str, Any], sections: Dict[str, str]
    ) -> str:
        """핵심 요약을 생성합니다.

        Args:
            metadata: Phase 1 메타데이터
            sections: 생성된 섹션 딕셔너리

        Returns:
            핵심 요약 마크다운 문자열
        """
        parts = []
        parts.append("## 핵심 요약")
        parts.append("")

        if sections:
            # 각 섹션에서 첫 문장(또는 첫 80자)을 추출하여 핵심 요약 생성
            for topic, content in list(sections.items())[:5]:  # 상위 5개 섹션
                # 첫 문장 추출 (마침표, 느낌표, 물음표 기준)
                first_sentence = content.split(".")[0].split("!")[0].split("?")[0]
                if len(first_sentence) > 80:
                    first_sentence = first_sentence[:80] + "..."
                parts.append(f"- {first_sentence}")
        else:
            # 섹션이 없으면 목차 항목 사용 (fallback)
            toc = metadata.get("toc", [])
            if toc:
                for item in toc[:5]:
                    parts.append(f"- {item}")
            else:
                parts.append("- 주요 내용 요약")

        parts.append("")
        return "\n".join(parts)

    def _combine_to_markdown(
        self,
        metadata: Dict[str, Any],
        core_summary: str,
        sections: Dict[str, str],
    ) -> str:
        """결과를 마크다운으로 조합합니다.

        Args:
            metadata: Phase 1 메타데이터
            core_summary: 핵심 요약 문자열
            sections: 생성된 섹션 딕셔너리

        Returns:
            완성된 마크다운 문자열
        """
        parts = []

        # 키워드
        keywords = metadata.get("keywords", [])
        if keywords:
            parts.append("## 키워드")
            parts.append(", ".join(keywords))
            parts.append("")

        # 목차
        toc = metadata.get("toc", [])
        if toc:
            parts.append("## 목차")
            for item in toc:
                parts.append(f"- {item}")
            parts.append("")

        # 핵심 요약
        if core_summary:
            parts.append(core_summary)

        # 상세 내용
        if sections:
            parts.append("## 상세 내용")
            for topic, content in sections.items():
                parts.append(f"### {topic}")
                parts.append(content)
                parts.append("")

        return "\n".join(parts).strip()

    async def generate_sections(
        self,
        toc: List[str],
        transcript: str,
        keywords: List[str],
        title: str,
        max_retries: int = 3,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[Dict[str, str], str, List[Dict[str, Any]]]:
        """상세 섹션을 병렬로 생성합니다.

        Args:
            toc: 목차 주제 리스트
            transcript: 원본 텍스트
            keywords: 키워드 리스트
            title: 콘텐츠 제목
            max_retries: 최대 재시도 횟수 (기본 3)
            progress_callback: 섹션 완료 콜백 (completed, total)

        Returns:
            (sections, detailed_content_md, logs) 튜플
            - sections: {주제: 내용} 딕셔너리
            - detailed_content_md: 마크다운 형식의 상세 내용
            - logs: 실행 로그 리스트

        Raises:
            Exception: 그래프 실행 중 오류 발생 시
        """
        logger.info(
            f"[SectionGraphExecutor] 섹션 생성 시작: "
            f"{len(toc)}개 주제, max_retries={max_retries}"
        )

        start_time = time.time()

        # 초기 상태 생성
        initial_state = create_initial_state(
            toc=toc,
            transcript=transcript,
            keywords=keywords,
            title=title,
            max_retries=max_retries,
            progress_callback=progress_callback,
        )

        try:
            # LangGraph 실행
            result = await self.graph.ainvoke(initial_state)

            elapsed = time.time() - start_time

            # 결과 추출
            sections = result.get("sections", {})
            detailed_content_md = result.get("detailed_content_md", "")
            logs = result.get("logs", [])
            failed_sections = result.get("failed_sections", [])

            logger.info(
                f"[SectionGraphExecutor] 섹션 생성 완료: "
                f"{len(sections)}개 성공, {len(failed_sections)}개 실패, "
                f"소요시간: {elapsed:.2f}초"
            )

            return sections, detailed_content_md, logs

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"[SectionGraphExecutor] 섹션 생성 오류: {e}, 소요시간: {elapsed:.2f}초"
            )
            raise

    def generate_summary_md(
        self,
        metadata: Dict[str, Any],
        core_summary: str,
        sections: Dict[str, str],
    ) -> str:
        """최종 마크다운 요약을 생성합니다.

        Args:
            metadata: Phase 1에서 생성된 메타데이터
            core_summary: Phase 2에서 생성된 핵심 요약
            sections: 섹션 생성 결과

        Returns:
            완성된 마크다운 문자열
        """
        parts = []

        # 키워드
        keywords = metadata.get("keywords", [])
        if keywords:
            parts.append("## 키워드")
            parts.append(", ".join(keywords))
            parts.append("")

        # 목차
        toc = metadata.get("toc", [])
        if toc:
            parts.append("## 목차")
            for item in toc:
                parts.append(f"- {item}")
            parts.append("")

        # 핵심 요약
        if core_summary:
            parts.append(core_summary)
            parts.append("")

        # 상세 내용
        if sections:
            parts.append("## 상세 내용")
            for topic, content in sections.items():
                parts.append(f"### {topic}")
                parts.append(content)
                parts.append("")

        return "\n".join(parts).strip()


# content_service.py에서 사용하는 standalone 함수들
def extract_metadata(text: str, settings: Settings) -> Dict[str, Any]:
    """텍스트에서 메타데이터(제목, 키워드, 목차)를 추출합니다.

    Args:
        text: 원본 텍스트
        settings: 설정 객체

    Returns:
        메타데이터 딕셔너리 {"title": ..., "keywords": [...], "toc": [...]}
    """
    import asyncio

    executor = SectionGraphExecutor(settings)
    # 비동기 메서드를 동기적으로 실행
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    result = loop.run_until_complete(executor._execute_phase_1(text))

    if result.success:
        return result.data
    else:
        # 실패 시 기본값 반환
        return {
            "title": "요약",
            "keywords": [],
            "toc": [],
        }


def generate_core_summary(
    text: str,
    metadata: Dict[str, Any],
    settings: Settings,
    sections: Dict[str, str] = None,
) -> str:
    """핵심 요약을 생성합니다.

    Args:
        text: 원본 텍스트
        metadata: Phase 1에서 추출된 메타데이터
        settings: 설정 객체
        sections: 생성된 섹션 딕셔너리 (없으면 목차 사용)

    Returns:
        핵심 요약 마크다운 문자열
    """
    executor = SectionGraphExecutor(settings)
    return executor._generate_core_summary(metadata, sections or {})


# 편의 함수
async def generate_detailed_sections_langgraph(
    toc: List[str],
    transcript: str,
    keywords: List[str],
    title: str,
    settings: Settings = None,
    max_retries: int = 3,
) -> Tuple[Dict[str, str], str, List[Dict[str, Any]]]:
    """LangGraph 기반 상세 섹션 생성을 수행합니다.

    이 함수는 별도의 Executor 인스턴스 생성 없이 바로 사용할 수 있는
    편의 함수입니다.

    Args:
        toc: 목차 주제 리스트
        transcript: 원본 텍스트
        keywords: 키워드 리스트
        title: 콘텐츠 제목
        settings: 설정 객체
        max_retries: 최대 재시도 횟수

    Returns:
        (sections, detailed_content_md, logs) 튜플
    """
    executor = SectionGraphExecutor(settings)
    return await executor.generate_sections(
        toc=toc,
        transcript=transcript,
        keywords=keywords,
        title=title,
        max_retries=max_retries,
    )
