"""2단계 LLM 요약 Executor with 검증 & 재시도.

Phase 1: 메타데이터 추출 (tier-simple) → JSON
Phase 2: 요약 생성 (tier-recap) → 파이프 구분 텍스트
"""

import asyncio
import json
import time
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass

from ..core.config import Settings
from ..core.logging import logger
from .litellm_client import request_litellm_completion


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


class PhaseExecutor:
    """2단계 요약 실행기 with 검증 & 재시도"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.max_retries = 3
        self.retry_delay = 1.0  # 고정 1초

    async def execute(self, text: str) -> Tuple[str, str]:
        """전체 2단계 실행

        Returns:
            (title, summary_md) 튜플

        Raises:
            PhaseExecutionError: 모든 재시도 실패 시
        """
        logger.info("[2-Phase] Starting 2-phase summarization")

        # Phase 1: 메타데이터 추출
        phase1_result = await self._execute_phase_1(text)
        if not phase1_result.success:
            raise PhaseExecutionError(f"Phase 1 failed: {phase1_result.error}")

        metadata = phase1_result.data
        logger.info(
            f"[2-Phase] Phase 1 success: title='{metadata.get('title', '')[:50]}...', "
            f"keywords={len(metadata.get('keywords', []))}, "
            f"toc={len(metadata.get('toc', []))}"
        )

        # Phase 2: 요약 생성
        phase2_result = await self._execute_phase_2(text, metadata)
        if not phase2_result.success:
            # Phase 1 결과라도 반환할지 결정 (요구사항: 에러 반환)
            raise PhaseExecutionError(f"Phase 2 failed: {phase2_result.error}")

        summary_data = phase2_result.data
        logger.info(
            f"[2-Phase] Phase 2 success: core={len(summary_data.get('core_summary', []))}, "
            f"details={len(summary_data.get('detailed_content', {}))}"
        )

        # 결과 조합
        summary_md = self._combine_to_markdown(metadata, summary_data)
        title = metadata.get("title", "요약")

        logger.info(
            f"[2-Phase] All phases completed. Summary length: {len(summary_md)}"
        )
        return title, summary_md

    async def _execute_phase_1(self, text: str) -> PhaseResult:
        """Phase 1: 메타데이터 추출 (tier-simple)"""
        from ..prompts.summary import (
            PHASE1_STRUCTURE_TEMPLATE_V2,
            SUMMARY_SYSTEM_PROMPT,
        )

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"[2-Phase] Phase 1 attempt {attempt}/{self.max_retries}")

                prompt = PHASE1_STRUCTURE_TEMPLATE_V2.format(transcript=text[:10000])
                messages = [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]

                response = request_litellm_completion(
                    settings=self.settings,
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
                        f"[2-Phase] Phase 1 validation failed (attempt {attempt})"
                    )

            except Exception as e:
                logger.error(f"[2-Phase] Phase 1 error (attempt {attempt}): {e}")

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay)

        return PhaseResult(
            success=False,
            data={},
            error="All Phase 1 retries failed",
            attempts=self.max_retries,
        )

    async def _execute_phase_2(self, text: str, metadata: Dict) -> PhaseResult:
        """Phase 2: 요약 생성 (tier-recap)"""
        from ..prompts.summary import PHASE2_SUMMARY_TEMPLATE, SUMMARY_SYSTEM_PROMPT

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"[2-Phase] Phase 2 attempt {attempt}/{self.max_retries}")

                # 메타데이터를 파이프 구분 문자열로 변환
                keywords_pipe = "|".join(metadata.get("keywords", []))
                toc_pipe = "|".join(metadata.get("toc", []))

                prompt = PHASE2_SUMMARY_TEMPLATE.format(
                    transcript=text, keywords=keywords_pipe, toc=toc_pipe
                )
                messages = [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]

                response = request_litellm_completion(
                    settings=self.settings,
                    model=self.settings.ai_gateway_model_summarize,
                    messages=messages,
                )

                # JSON 파싱
                data = self._parse_json_response(response)

                # 검증
                if self._validate_phase_2(data):
                    # 파이프 구분 문자열 파싱
                    core_str = data.get("core_summary", "")
                    detail_str = data.get("detailed_sections", "")

                    core_list = [c.strip() for c in core_str.split("|") if c.strip()]

                    # detailed_sections 파싱: "주제1:내용1|주제2:내용2"
                    detail_dict = {}
                    for section in detail_str.split("|"):
                        if ":" in section:
                            parts = section.split(":", 1)  # 첫 번째 :만 분리
                            if len(parts) == 2:
                                title, content = parts
                                detail_dict[title.strip()] = content.strip()

                    return PhaseResult(
                        success=True,
                        data={
                            "core_summary": core_list,
                            "detailed_content": detail_dict,
                        },
                        attempts=attempt,
                    )
                else:
                    logger.warning(
                        f"[2-Phase] Phase 2 validation failed (attempt {attempt})"
                    )

            except Exception as e:
                logger.error(f"[2-Phase] Phase 2 error (attempt {attempt}): {e}")

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay)

        return PhaseResult(
            success=False,
            data={},
            error="All Phase 2 retries failed",
            attempts=self.max_retries,
        )

    def _parse_json_response(self, response: str) -> Dict:
        """LLM 응답에서 JSON 추출 및 파싱"""
        import re

        # 코드 블록 제거
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\s*|\s*```$", "", text)

        # JSON 추출 시도
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 중괘호 패턴 찾기
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except (json.JSONDecodeError, AttributeError):
            pass

        raise ValueError(f"Failed to parse JSON from response: {text[:200]}")

    def _validate_phase_1(self, data: Dict) -> bool:
        """Phase 1 결과 검증"""
        # 필수 필드 존재
        if not data.get("title"):
            return False

        keywords = data.get("keywords", "")
        toc = data.get("toc", "")

        # 최소 1개 아이템 (파이프로 구분)
        if not keywords or "|" not in keywords:
            # 단일 아이템도 허용
            if not keywords.strip():
                return False

        if not toc or "|" not in toc:
            # 단일 아이템도 허용
            if not toc.strip():
                return False

        return True

    def _validate_phase_2(self, data: Dict) -> bool:
        """Phase 2 결과 검증"""
        core_summary = data.get("core_summary", "")
        detailed_sections = data.get("detailed_sections", "")

        # 최소 1개 핵심 요약
        if not core_summary or not core_summary.strip():
            return False

        # 최소 1개 상세 섹션
        if not detailed_sections or not detailed_sections.strip():
            return False

        # 상세 섹션에 : 또는 | 포함 확인
        if ":" not in detailed_sections and "|" not in detailed_sections:
            return False

        return True

    def _combine_to_markdown(self, metadata: Dict, summary: Dict) -> str:
        """결과를 마크다운으로 조합"""
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
        core_summary = summary.get("core_summary", [])
        if core_summary:
            parts.append("## 핵심 요약")
            for item in core_summary:
                parts.append(f"- {item}")
            parts.append("")

        # 상세 내용
        detailed = summary.get("detailed_content", {})
        if detailed:
            parts.append("## 상세 내용")
            for title, content in detailed.items():
                parts.append(f"### {title}")
                parts.append(content)
                parts.append("")

        return "\n".join(parts).strip()
