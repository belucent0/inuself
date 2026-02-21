"""LLM 요약 서비스 - V6.6 간소화 버전.

효율적인 프롬프트로 제목, 핵심 요약, 키워드, 목차를 추출합니다.
- 단일 프롬프트 (JSON 응답)
- 토큰 사용량 최적화
"""

from __future__ import annotations

import json
import re
import time
import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings, Settings
from ..core.logging import logger
from ..db.models import FileStatus, ContentType
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from .litellm_client import request_litellm_completion, LiteLLMClientError
from .transcription_postprocess import segments_to_text_with_metadata
from ..prompts.summary import (
    SUMMARY_SYSTEM_PROMPT,
    STEP1_PROMPT_TEMPLATE,
    MERGE_PROMPT_TEMPLATE,
    PHASE1_STRUCTURE_TEMPLATE,
    PHASE2_CORE_TEMPLATE,
    PHASE3_DETAIL_TEMPLATE,
    PHASE1_STRUCTURE_TEMPLATE_V2,
    PHASE2_SUMMARY_TEMPLATE,
)
from .section_executor import SectionGraphExecutor, PhaseExecutionError


def _split_text_into_chunks(
    text: str, max_chars: int = 25000, overlap_chars: int = 1000
) -> list[str]:
    """텍스트를 청크로 분할."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + max_chars

        if end < len(text):
            # 문장 경계에서 분할
            for sep in [". ", ".\n", "! ", "? ", "\n\n"]:
                last_sep = text.rfind(sep, start + int(max_chars * 0.7), end)
                if last_sep > start:
                    end = last_sep + len(sep)
                    break

        chunks.append(text[start:end])
        start = end - overlap_chars
        if start < 0:
            start = 0

    return chunks


def _parse_json_response(response: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 파싱."""
    # JSON 블록 추출 시도
    json_match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # 직접 JSON 파싱 시도
        json_str = response.strip()
        start_idx = json_str.find("{")
        if start_idx >= 0:
            # 중괄호 균형 맞추기
            brace_count = 0
            end_idx = start_idx
            for i in range(start_idx, len(json_str)):
                if json_str[i] == "{":
                    brace_count += 1
                elif json_str[i] == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i
                        break
            json_str = json_str[start_idx : end_idx + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("JSON parsing failed, extracting fields from text")
        return _extract_fields_from_text(response)


def _extract_fields_from_text(text: str) -> dict[str, Any]:
    """텍스트에서 필드 추출 (JSON 파싱 실패 시 fallback).

    우선순위:
    1. JSON summary 필드 추출 및 마크다운 파싱
    2. 전체 텍스트 마크다운 형식 감지 및 파싱
    3. JSON 필드 regex 추출
    4. Raw 텍스트 사용 (최후의 수단)
    """
    result = {"title": "", "toc": [], "summary": "", "keywords": []}

    cleaned_text = text.strip()

    # 1. JSON summary 필드 추출 시도 (가장 우선)
    summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if summary_match:
        summary_text = summary_match.group(1)
        summary_text = (
            summary_text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        )
        # summary 필드 내용이 마크다운 형식인지 확인
        if "##" in summary_text or "###" in summary_text:
            logger.info("Extracted markdown from JSON summary field")
            parsed = _extract_from_markdown(summary_text)
            result["summary"] = summary_text
            # 마크다운에서 추출한 title/toc/keywords가 없으면 JSON에서 추출
            if not result["title"]:
                title_match = re.search(r'"title"\s*:\s*"([^"]+)"', text)
                if title_match:
                    result["title"] = title_match.group(1)
            if not result["keywords"]:
                keywords_match = re.search(r'"keywords"\s*:\s*\[([^\]]+)\]', text)
                if keywords_match:
                    keywords_str = keywords_match.group(1)
                    result["keywords"] = [
                        k.strip().strip("\"'") for k in keywords_str.split(",")
                    ]
            if not result["toc"]:
                toc_match = re.search(
                    r'"toc"\s*:\s*\[((?:[^\[\]]|\[(?:[^\[\]])*\])*)\]', text
                )
                if toc_match:
                    toc_str = toc_match.group(1)
                    toc_items = re.findall(r'"([^"]+)"', toc_str)
                    result["toc"] = toc_items
            return result

    # 2. 전체 텍스트가 마크다운 형식인지 감지
    if (
        "## 키워드" in cleaned_text
        or "## 핵심 요약" in cleaned_text
        or "## 상세 내용" in cleaned_text
    ):
        logger.info(
            "Detected markdown format in full text, extracting structured content"
        )
        return _extract_from_markdown(cleaned_text)

    # 3. JSON 필드 regex 추출 (기존 로직)
    title_match = re.search(r'"title"\s*:\s*"([^"]+)"', text)
    if title_match:
        result["title"] = title_match.group(1)

    # summary 필드가 없거나 마크다운 형식이 아닌 경우
    if summary_match:
        summary_text = summary_match.group(1)
        summary_text = (
            summary_text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        )
        result["summary"] = summary_text
    else:
        # JSON 형식이 깨졌지만 텍스트가 있는 경우
        # 혹시 ```json ... ``` 블록이 남아있다면 제거
        if cleaned_text.startswith("```"):
            cleaned_text = re.sub(r"^```\w*\s*|\s*```$", "", cleaned_text)

        # 중괄호로 감싸져 있다면 제거 시도
        if cleaned_text.startswith("{") and cleaned_text.endswith("}"):
            cleaned_text = cleaned_text[1:-1].strip()

        if len(cleaned_text) > 50:
            logger.warning("JSON structure broken, trying markdown extraction")
            # 마크다운 형식인지 다시 시도
            return _extract_from_markdown(cleaned_text)
        else:
            raise ValueError(
                f"LLM 응답에서 summary 필드를 추출할 수 없습니다: {text[:200]}..."
            )

    # 키워드 추출
    keywords_match = re.search(r'"keywords"\s*:\s*\[([^\]]+)\]', text)
    if keywords_match:
        keywords_str = keywords_match.group(1)
        result["keywords"] = [k.strip().strip("\"'") for k in keywords_str.split(",")]

    # toc 추출
    toc_match = re.search(r'"toc"\s*:\s*\[((?:[^\[\]]|\[(?:[^\[\]])*\])*)\]', text)
    if toc_match:
        toc_str = toc_match.group(1)
        toc_items = re.findall(r'"([^"]+)"', toc_str)
        result["toc"] = toc_items

    return result


def _extract_from_markdown(text: str) -> dict[str, Any]:
    """마크다운 형식에서 필드 추출."""
    result = {"title": "", "toc": [], "summary": "", "keywords": []}

    lines = text.split("\n")
    current_section = None
    summary_lines = []

    for line in lines:
        line = line.strip()

        # 제목 추출 (가장 먼저 나오는 h1 또는 h2)
        if not result["title"] and (line.startswith("# ") or line.startswith("## ")):
            candidate = re.sub(r"^#+\s*", "", line).strip().strip('"')
            if len(candidate) < 100 and len(candidate) > 5:
                result["title"] = candidate
            continue

        # 키워드 섹션
        if line == "## 키워드":
            current_section = "keywords"
            continue
        elif line == "## 목차":
            current_section = "toc"
            continue
        elif line == "## 핵심 요약":
            current_section = "summary_core"
            continue
        elif line == "## 상세 내용":
            current_section = "summary_detail"
            continue
        elif line.startswith("## "):
            current_section = "summary_detail"

        # 키워드 추출
        if current_section == "keywords" and line:
            keywords = [k.strip() for k in line.split(",")]
            result["keywords"] = keywords

        # 목차 추출
        elif current_section == "toc":
            if line.startswith("- "):
                toc_item = line[2:].strip().strip('"')
                if toc_item:
                    result["toc"].append(toc_item)

        # 핵심 요약 및 상세 내용
        elif current_section in ["summary_core", "summary_detail"]:
            summary_lines.append(line)

    # summary 구성
    if summary_lines:
        result["summary"] = "\n".join(summary_lines)

    # summary 끝부분의 ``` 마커 제거
    if result["summary"]:
        # 마지막 줄이 ```인 경우 제거
        summary_lines = result["summary"].split("\n")
        cleaned_summary_lines = []
        in_code_block = False

        for line in summary_lines:
            stripped = line.strip()
            # ``` 블록 시작/종료 무시
            if stripped == "```" or stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            cleaned_summary_lines.append(line)

        result["summary"] = "\n".join(cleaned_summary_lines).strip()

    # 목차에서 추출하지 못한 경우, 소제목(###)에서 추출
    if not result["toc"]:
        toc_items = []
        for line in lines:
            if line.startswith("### "):
                toc_item = line[4:].strip().strip('"')
                if toc_item:
                    toc_items.append(toc_item)
        result["toc"] = toc_items

    return result


def _format_as_markdown(data: dict[str, Any]) -> str:
    """JSON 데이터를 마크다운으로 변환."""
    parts = []

    # 제목 (마크다운 본문에는 포함하지 않음 - DB에 별도 저장)

    # 1. 키워드 (최상단)
    keywords = data.get("keywords", [])
    if keywords:
        parts.append("## 키워드")
        parts.append(", ".join(keywords))
        parts.append("")

    # 2. 목차
    toc = data.get("toc", [])
    if toc:
        parts.append("## 목차")
        for item in toc:
            parts.append(f"- {item}")
        parts.append("")

    # 3. 핵심 요약 및 상세 내용
    summary = data.get("summary", "")
    if summary:
        # 이미 마크다운(#)으로 시작하면 그대로 사용 (프롬프트에서 ## 핵심 요약 등을 포함하도록 유도함)
        if summary.strip().startswith("#"):
            parts.append(summary)
        else:
            # 마크다운 헤더가 없으면 강제로 붙여줌
            parts.append("## 핵심 요약")
            parts.append(summary)
        parts.append("")

    return "\n".join(parts)


def sanitize_summary_output(summary_md: str, original_text: str) -> str:
    """요약 결과를 정리합니다 (HTML 제거 포함)."""
    if not summary_md:
        return ""

    # HTML 태그 제거
    summary_md = re.sub(r"<p[^>]*>", "", summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r"</p>", "\n\n", summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r"<li[^>]*>", "- ", summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r"</li>", "\n", summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r"</?[uo]l[^>]*>", "", summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r"<br\s*/?>", "\n", summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r"<[^>]+>", "", summary_md)

    # 연속된 개행 정리
    summary_md = re.sub(r"\n{3,}", "\n\n", summary_md)

    return summary_md.strip()


# =========================================================
# [3-Phase] 프롬프트 생성: 3단계 순차 실행
# =========================================================


def build_llm_messages_3phase(
    text: str, chunk_index: int = 0, total_chunks: int = 1
) -> list[list]:
    """LLM 요약용 3단계 프롬프트(messages)를 생성합니다.

    각 단계별 프롬프트를 생성하여 반환합니다.
    Worker가 이 messages를 순차적으로 실행해야 합니다.

    Args:
        text: 요약할 텍스트
        chunk_index: 현재 청크 인덱스 (0-based)
        total_chunks: 전체 청크 수

    Returns:
        Phase별 messages 리스트: [phase1_messages, phase2_messages, phase3_messages]
    """
    normalized = text.strip()
    if not normalized:
        raise ValueError("프롬프트 생성을 위한 텍스트가 비어 있습니다.")

    # 청크 분할
    max_chunk_chars = 25000
    chunks = _split_text_into_chunks(normalized, max_chars=max_chunk_chars)

    # 단일 청크인 경우
    if len(chunks) == 1:
        chunk = chunks[0]

        # Phase 1: 구조 분석 (Structure Analysis)
        phase1_prompt = PHASE1_STRUCTURE_TEMPLATE.format(transcript=chunk[:10000])
        phase1_messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": phase1_prompt},
        ]
        logger.info(
            f"[3-Phase Builder] Phase 1 prompt built ({len(phase1_messages)} messages)"
        )

        # Phase 2: 핵심 요약 (Core Summary)
        phase2_prompt = PHASE2_CORE_TEMPLATE.format(
            transcript=chunk[:15000],
            toc="- 주제가 없습니다.",  # 임시 TOC, Phase 1 결과로 대체될 것
        )
        phase2_messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": phase2_prompt},
        ]
        logger.info(
            f"[3-Phase Builder] Phase 2 prompt built ({len(phase2_messages)} messages)"
        )

        # Phase 3: 상세 내용 (Detailed Content) - tier-summarize 전용
        phase3_prompt = PHASE3_DETAIL_TEMPLATE.format(
            transcript=chunk,
            toc="- 주제가 없습니다.",  # 임시 TOC, Phase 1 결과로 대체될 것
        )
        phase3_messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": phase3_prompt},
        ]
        logger.info(
            f"[3-Phase Builder] Phase 3 prompt built ({len(phase3_messages)} messages)"
        )

        return [phase1_messages, phase2_messages, phase3_messages]

    # 여러 청크인 경우 - 현재는 사용하지 않음
    else:
        if 0 <= chunk_index < len(chunks):
            chunk = chunks[chunk_index]
            prompt = STEP1_PROMPT_TEMPLATE.format(transcript=chunk)
            messages = [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            logger.info(
                f"[Prompt Builder] Chunk {chunk_index + 1}/{len(chunks)} prompt built ({len(prompt)} chars)"
            )
            return messages
        else:
            raise ValueError(
                f"Invalid chunk_index {chunk_index} for {len(chunks)} chunks"
            )


def build_llm_messages(
    text: str, chunk_index: int = 0, total_chunks: int = 1
) -> list[dict]:
    """LLM 요약용 프롬프트(messages)를 생성합니다.

    Worker가 이 messages를 그대로 LLM에 던질 것이므로, 여기서 프롬프트만 완성합니다.
    청크가 있는 경우 청크별로 프롬프트를 생성합니다.

    Args:
        text: 요약할 텍스트
        chunk_index: 현재 청크 인덱스 (0-based)
        total_chunks: 전체 청크 수

    Returns:
        LLM 호출용 messages 리스트
    """
    normalized = text.strip()
    if not normalized:
        raise ValueError("프롬프트 생성을 위한 텍스트가 비어 있습니다.")

    # 청크 분할 (필요한 경우)
    max_chunk_chars = 25000
    chunks = _split_text_into_chunks(normalized, max_chars=max_chunk_chars)

    # 단일 청크인 경우
    if len(chunks) == 1:
        chunk = chunks[0]
        prompt = STEP1_PROMPT_TEMPLATE.format(transcript=chunk)
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        logger.info(f"[Prompt Builder] Single chunk prompt built ({len(prompt)} chars)")
        return messages

    # 여러 청크인 경우
    else:
        # 이 메서드는 청크별로 호출될 수 있습니다.
        # 호출하는 쪽에서 청크를 나누어서 각각 호출하는 것이 명확합니다.
        # 여기서는 청크 하나를 처리한다고 가정합니다.
        # (StreamConsumer에서 청크 분할 후 각각 호출)
        if 0 <= chunk_index < len(chunks):
            chunk = chunks[chunk_index]
            prompt = STEP1_PROMPT_TEMPLATE.format(transcript=chunk)
            messages = [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            logger.info(
                f"[Prompt Builder] Chunk {chunk_index + 1}/{len(chunks)} prompt built ({len(prompt)} chars)"
            )
            return messages
        else:
            raise ValueError(
                f"Invalid chunk_index {chunk_index} for {len(chunks)} chunks"
            )


def parse_llm_response(raw_response: str, is_merged: bool = False) -> tuple[str, str]:
    """Worker가 LLM으로부터 받은 Raw 응답을 파싱합니다.

    Args:
        raw_response: Worker가 받은 LLM 응답 (JSON 또는 텍스트)
        is_merged: 병합 결과인지 여부

    Returns:
        (title, summary_md) 튜플
    """
    result = _parse_json_response(raw_response)

    # 제목과 마크다운 반환
    title = result.get("title", "요약")
    if not title or len(title) < 2:
        title = result.get("toc", ["요약"])[0] if result.get("toc") else "요약"

    summary_md = _format_as_markdown(result)
    summary_md = sanitize_summary_output(summary_md, raw_response)

    logger.info(
        f"[Response Parser] Parsed: title='{title}', summary_length={len(summary_md)}"
    )
    return title, summary_md


def summarize_transcription_3phase(text: str, settings: Settings) -> tuple[str, str]:
    """전사 텍스트를 3단계로 요약합니다.

    Phase 1: 구조 분석 (제목, 키워드, 목차)
    Phase 2: 핵심 요약 (bullet points)
    Phase 3: 상세 내용 (tier-summarize 전용)

    Args:
        text: 요약할 전사 텍스트
        settings: 설정 객체

    Returns:
        (title, summary_md) 튜플
    """
    normalized = text.strip()
    if not normalized:
        raise ValueError("요약할 텍스트가 비어 있습니다.")

    logger.info("[3-Phase] Starting 3-phase summarization")

    # ========================================
    # Phase 1: 구조 분석 (Structure Analysis)
    # ========================================
    logger.info("[3-Phase] Phase 1: Structure Analysis")
    try:
        phase1_prompt = PHASE1_STRUCTURE_TEMPLATE.format(transcript=normalized[:10000])
        phase1_messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": phase1_prompt},
        ]

        phase1_response = request_litellm_completion(
            settings=settings, messages=phase1_messages
        )
        phase1_result = _parse_json_response(phase1_response)

        title = phase1_result.get("title", "")
        keywords = phase1_result.get("keywords", [])
        toc = phase1_result.get("toc", [])

        if not title:
            title = _extract_title_fallback(normalized)
        if not keywords:
            keywords = []
        if not toc:
            toc = []

        logger.info(
            f"[3-Phase] Phase 1 completed: title='{title[:50]}...', keywords={len(keywords)}, toc={len(toc)}"
        )

    except Exception as e:
        logger.error(f"[3-Phase] Phase 1 failed: {e}")
        # Fallback: 제목과 키워드는 간단히 추출, 목차는 비어있음
        title = _extract_title_fallback(normalized)
        keywords = []
        toc = []
        logger.warning("[3-Phase] Using fallback for Phase 1")

    # ========================================
    # Phase 2: 핵심 요약 (Core Summary)
    # ========================================
    logger.info("[3-Phase] Phase 2: Core Summary")
    try:
        if toc:
            toc_text = "\n".join([f"- {item}" for item in toc])
        else:
            toc_text = "주제가 없습니다."

        phase2_prompt = PHASE2_CORE_TEMPLATE.format(
            toc=toc_text, transcript=normalized[:15000]
        )
        phase2_messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": phase2_prompt},
        ]

        phase2_response = request_litellm_completion(
            settings=settings, messages=phase2_messages
        )
        core_summary = phase2_response.strip()

        # ## 핵심 요약 헤더가 있는지 확인 후 없으면 추가
        if not core_summary.startswith("## 핵심 요약"):
            core_summary = f"## 핵심 요약\n{core_summary}"

        logger.info(
            f"[3-Phase] Phase 2 completed: core_summary_length={len(core_summary)}"
        )

    except Exception as e:
        logger.error(f"[3-Phase] Phase 2 failed: {e}")
        core_summary = "## 핵심 요약\n- 핵심 요약 생성에 실패했습니다."

    # ========================================
    # Phase 3: 상세 내용 (Detailed Content) - tier-summarize 전용
    # ========================================
    logger.info("[3-Phase] Phase 3: Detailed Content (tier-summarize)")
    try:
        if toc:
            toc_text = "\n".join([f"- {item}" for item in toc])
        else:
            toc_text = "주제가 없습니다."

        phase3_prompt = PHASE3_DETAIL_TEMPLATE.format(
            toc=toc_text, transcript=normalized
        )
        phase3_messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": phase3_prompt},
        ]

        phase3_response = request_litellm_completion(
            settings=settings, model=settings.litellm_model_summarize, messages=phase3_messages
        )
        detail_content = phase3_response.strip()

        # ## 상세 내용 헤더가 있는지 확인 후 없으면 추가
        if not detail_content.startswith("## 상세 내용"):
            detail_content = f"## 상세 내용\n{detail_content}"

        # 마지막에 ``` 코드 블록 마커 제거
        detail_lines = detail_content.split("\n")
        cleaned_detail_lines = []
        for line in detail_lines:
            stripped = line.strip()
            # ``` 블록 시작/종료 무시
            if stripped == "```" or stripped.startswith("```"):
                continue
            cleaned_detail_lines.append(line)
        detail_content = "\n".join(cleaned_detail_lines).strip()

        logger.info(f"[3-Phase] Phase 3 completed: detail_length={len(detail_content)}")

    except Exception as e:
        logger.error(f"[3-Phase] Phase 3 failed: {e}")
        detail_content = "## 상세 내용\n상세 내용 생성에 실패했습니다."

    # ========================================
    # 최종 결과 조합
    # ========================================
    parts = []

    # 키워드
    if keywords:
        parts.append("## 키워드")
        parts.append(", ".join(keywords))
        parts.append("")

    # 목차
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
    if detail_content:
        parts.append(detail_content)

    summary_md = "\n".join(parts)
    summary_md = sanitize_summary_output(summary_md, normalized)

    logger.info(f"[3-Phase] All phases completed. Total length: {len(summary_md)}")

    return title, summary_md


def summarize_transcription_old(text: str) -> tuple[str, str]:
    """전사 텍스트를 요약합니다 (Legacy - 내부용).

    Args:
        text: 요약할 전사 텍스트

    Returns:
        (title, summary_md) 튜플
    """
    settings = get_settings()
    normalized = text.strip()

    if not normalized:
        raise ValueError("요약할 텍스트가 비어 있습니다.")

    # 청크 분할
    max_chunk_chars = 25000  # ~10000 토큰
    chunks = _split_text_into_chunks(normalized, max_chars=max_chunk_chars)
    logger.info(f"[Summarizer] Text split into {len(chunks)} chunks")

    # 각 청크 요약
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        logger.info(f"[Summarizer] Processing chunk {i}/{len(chunks)}...")
        try:
            prompt = STEP1_PROMPT_TEMPLATE.format(transcript=chunk)
            messages = [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            response = request_litellm_completion(settings=settings, messages=messages)
            result = _parse_json_response(response)
            summaries.append(result)
            logger.info(
                f"Chunk {i}/{len(chunks)} summarized: title='{result.get('title', '')[:30]}...'"
            )
        except Exception as e:
            logger.error(f"Chunk {i} failed: {e}")
            continue

    if not summaries:
        raise RuntimeError("모든 청크 요약에 실패했습니다.")

    # 통합
    if len(summaries) == 1:
        final = summaries[0]
    else:
        final = _merge_summaries(summaries, settings)

    # 제목과 마크다운 반환
    title = final.get("title", "요약")
    if not title or len(title) < 2:
        title = _extract_title_fallback(normalized)

    summary_md = _format_as_markdown(final)
    summary_md = sanitize_summary_output(summary_md, normalized)

    logger.info(
        f"[Summarizer] Completed: title='{title}', summary_length={len(summary_md)}"
    )
    return title, summary_md


def _merge_summaries(summaries: list[dict[str, Any]], settings) -> dict[str, Any]:
    """여러 청크의 요약을 통합."""
    # 부분 요약 텍스트 생성
    parts = []
    for i, s in enumerate(summaries, 1):
        parts.append(
            f"=== 부분 {i} ===\n제목: {s.get('title', '')}\n요약:\n{s.get('summary', '')}"
        )

    summaries_text = "\n\n".join(parts)
    prompt = MERGE_PROMPT_TEMPLATE.format(summaries=summaries_text)

    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        response = request_litellm_completion(settings=settings, messages=messages)
        result = _parse_json_response(response)

        # 키워드 통합
        all_keywords = set()
        for s in summaries:
            all_keywords.update(s.get("keywords", []))
        if not result.get("keywords"):
            result["keywords"] = list(all_keywords)[:10]

        logger.info(f"Merged {len(summaries)} summaries into final result")
        return result

    except Exception as e:
        logger.warning(f"Merge failed, using first chunk: {e}")
        return summaries[0]


def _extract_title_fallback(text: str) -> str:
    """제목 추출 실패 시 대체 방법."""
    first_sentence = text.split(".")[0].strip()
    if first_sentence and len(first_sentence) > 5:
        if len(first_sentence) > 50:
            return first_sentence[:47] + "..."
        return first_sentence
    return "요약"


class LlmSummaryService:
    """LLM 요약 실행 및 DB 반영 서비스."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.transcription_repo = TranscriptionRepository(session)
        self.document_repo = DocumentRepository(session)
        self.settings = get_settings()

    async def summarize(self, file_id: UUID) -> None:
        """LLM 요약 수행."""
        file_obj = await self.file_repo.get_file(file_id)
        if not file_obj:
            logger.warning(
                "[LLM] File not found, skipping summarization: file_id=%s", file_id
            )
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarization_skipped", "reason": "file_not_found"},
                message="LLM summarization skipped: file not found",
            )
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARY_FAILED)
            await self.session.commit()
            return

        await self._summarize_file(file_obj)

    async def _summarize_file(self, file_obj) -> None:
        """File을 사용한 요약."""
        file_id = file_obj.id
        content = file_obj.content

        if content and content.status == FileStatus.COMPLETED and content.summary_md:
            logger.info("File %s already completed with summary, skipping", file_id)
            return

        text_to_summarize = ""
        if file_obj.content_type == ContentType.AUDIO:
            transcription = await self.transcription_repo.get_by_file_id(file_id)
            if transcription:
                transcript_data = transcription.transcription or {}
                # 세그먼트가 있으면 화자/시간 정보 포함 형식 사용
                segments = transcript_data.get("segments", [])
                if segments:
                    text_to_summarize = segments_to_text_with_metadata(segments)
                    logger.info(
                        "Using segment format with speaker/time metadata for file_id=%s",
                        file_id,
                    )
                else:
                    text_to_summarize = str(transcript_data.get("text") or "").strip()
        elif file_obj.content_type in [ContentType.DOCUMENT, ContentType.PORTRAY]:
            document = await self.document_repo.get_by_file_id(file_id)
            if document:
                text_to_summarize = document.ocr_text.strip()

        if not text_to_summarize:
            logger.warning("Text to summarize is empty for file_id=%s", file_id)
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarization_skipped", "reason": "empty_text"},
                message="LLM summarization skipped: no text to summarize",
            )
            await self.file_repo.update_file_status(file_id, FileStatus.COMPLETED)
            await self.session.commit()
            return

        # 3단계 요약 실행 (새로운 파이프라인)
        logger.info("Starting 3-phase LLM summarization for file_id={}", file_id)

        start = time.perf_counter()
        try:
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
            await self.file_repo.add_llm_log(
                file_id,
                log={
                    "event": "summarizing_started",
                    "previous_status": content.status.value if content else "UNKNOWN",
                },
                message="LLM summarization started",
            )
            await self.session.commit()

            # 2단계 요약 실행 (LangGraph 기반 SectionGraphExecutor)
            executor = SectionGraphExecutor(self.settings)
            title, summary_md = await executor.execute(text_to_summarize)

        except PhaseExecutionError as exc:
            logger.exception("LLM summarization failed for file_id={}", file_id)
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarizing_failed", "error": str(exc)},
                message=f"LLM summarization failed: {exc}",
            )
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARY_FAILED)
            await self.session.commit()
            raise
        except Exception as exc:
            logger.exception(
                "Unexpected error during summarization for file_id={}", file_id
            )
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarizing_error", "error": str(exc)},
                message=f"Unexpected error: {exc}",
            )
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARY_FAILED)
            await self.session.commit()
            raise

        elapsed = time.perf_counter() - start

        await self.file_repo.update_title(file_id, title)
        await self.file_repo.update_summary_markdown(file_id, summary_md)

        # [Phase 2] 임베딩 자동 생성
        logger.info(f"[Embedding] Generating embedding for file_id={file_id}")
        try:
            from ..utils.embedding import create_embedding

            embedding = await create_embedding(summary_md)
            if embedding and len(embedding) == 768:
                await self.file_repo.update_embedding(file_id, embedding)
                logger.info(f"[Embedding] Successfully stored embedding for file_id={file_id}")
            else:
                logger.warning(
                    f"[Embedding] Invalid embedding dimension for file_id={file_id}: "
                    f"expected 768, got {len(embedding) if embedding else 0}"
                )
        except Exception as e:
            logger.error(f"[Embedding] Failed to generate embedding for file_id={file_id}: {e}")
            # 임베딩 실패는 치명적이지 않으므로 계속 진행

        await self.file_repo.update_file_status(file_id, FileStatus.COMPLETED)
        await self.file_repo.add_llm_log(
            file_id,
            log={"event": "summarizing_completed", "duration_sec": elapsed},
            message="LLM summarization completed",
        )
        await self.session.commit()
        logger.info("LLM summary stored for file_id={} ({:.2f}s)", file_id, elapsed)
