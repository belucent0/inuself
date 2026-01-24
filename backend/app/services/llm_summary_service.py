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

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings, Settings
from ..core.logging import logger
from ..db.models import FileStatus, ContentType
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from .litellm_client import request_litellm_completion, LiteLLMClientError
from ..prompts.summary import (
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_PROMPT_TEMPLATE,
    MERGE_PROMPT_TEMPLATE,
)


def _split_text_into_chunks(text: str, max_chars: int = 25000, overlap_chars: int = 1000) -> list[str]:
    """텍스트를 청크로 분할."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + max_chars

        if end < len(text):
            # 문장 경계에서 분할
            for sep in ['. ', '.\n', '! ', '? ', '\n\n']:
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
    json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # 직접 JSON 파싱 시도
        json_str = response.strip()
        start_idx = json_str.find('{')
        if start_idx >= 0:
            # 중괄호 균형 맞추기
            brace_count = 0
            end_idx = start_idx
            for i in range(start_idx, len(json_str)):
                if json_str[i] == '{':
                    brace_count += 1
                elif json_str[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i
                        break
            json_str = json_str[start_idx:end_idx + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("JSON parsing failed, extracting fields from text")
        return _extract_fields_from_text(response)


def _extract_fields_from_text(text: str) -> dict[str, Any]:
    """텍스트에서 필드 추출 (JSON 파싱 실패 시 fallback).

    주의: raw text를 그대로 summary로 사용하지 않음.
    각 필드를 regex로 추출하고, summary 필드가 없으면 에러 발생.
    """
    result = {
        "title": "",
        "toc": [],
        "summary": "",
        "keywords": []
    }

    # 제목 추출
    title_match = re.search(r'"title"\s*:\s*"([^"]+)"', text)
    if title_match:
        result["title"] = title_match.group(1)

    # summary 필드 추출 (핵심!)
    summary_match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if summary_match:
        summary_text = summary_match.group(1)
        summary_text = summary_text.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
        result["summary"] = summary_text
    else:
        # summary 필드가 없는 경우, 텍스트가 JSON이 아닌 순수 응답인지 확인
        if not text.strip().startswith('{') and len(text.strip()) > 50:
            result["summary"] = text.strip()
        else:
            raise ValueError(f"LLM 응답에서 summary 필드를 추출할 수 없습니다: {text[:200]}...")

    # 키워드 추출
    keywords_match = re.search(r'"keywords"\s*:\s*\[([^\]]+)\]', text)
    if keywords_match:
        keywords_str = keywords_match.group(1)
        result["keywords"] = [k.strip().strip('"\'') for k in keywords_str.split(',')]

    # toc 추출
    toc_match = re.search(r'"toc"\s*:\s*\[((?:[^\[\]]|\[(?:[^\[\]])*\])*)\]', text)
    if toc_match:
        toc_str = toc_match.group(1)
        toc_items = re.findall(r'"([^"]+)"', toc_str)
        result["toc"] = toc_items

    return result


def _format_as_markdown(data: dict[str, Any]) -> str:
    """JSON 데이터를 마크다운으로 변환."""
    parts = []

    # 제목 (마크다운 본문에는 포함하지 않음 - DB에 별도 저장)

    # 목차
    toc = data.get('toc', [])
    if toc:
        parts.append("## 목차")
        for item in toc:
            parts.append(f"- {item}")
        parts.append("")

    # 핵심 요약
    summary = data.get('summary', '')
    if summary:
        if summary.startswith('#'):
            parts.append(summary)
        else:
            parts.append("## 핵심 요약")
            parts.append(summary)
        parts.append("")

    # 키워드
    keywords = data.get('keywords', [])
    if keywords:
        parts.append("## 키워드")
        parts.append(", ".join(keywords))
        parts.append("")

    return "\n".join(parts)


def sanitize_summary_output(summary_md: str, original_text: str) -> str:
    """요약 결과를 정리합니다 (HTML 제거 포함)."""
    if not summary_md:
        return ""

    # HTML 태그 제거
    summary_md = re.sub(r'<p[^>]*>', '', summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r'</p>', '\n\n', summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r'<li[^>]*>', '- ', summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r'</li>', '\n', summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r'</?[uo]l[^>]*>', '', summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r'<br\s*/?>', '\n', summary_md, flags=re.IGNORECASE)
    summary_md = re.sub(r'<[^>]+>', '', summary_md)

    # 연속된 개행 정리
    summary_md = re.sub(r'\n{3,}', '\n\n', summary_md)

    return summary_md.strip()


def summarize_transcription(text: str) -> tuple[str, str]:
    """전사 텍스트를 요약합니다.

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
            prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript=chunk)
            messages = [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            response = request_litellm_completion(settings=settings, messages=messages)
            result = _parse_json_response(response)
            summaries.append(result)
            logger.info(f"Chunk {i}/{len(chunks)} summarized: title='{result.get('title', '')[:30]}...'")
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
    title = final.get('title', '요약')
    if not title or len(title) < 2:
        title = _extract_title_fallback(normalized)

    summary_md = _format_as_markdown(final)
    summary_md = sanitize_summary_output(summary_md, normalized)

    logger.info(f"[Summarizer] Completed: title='{title}', summary_length={len(summary_md)}")
    return title, summary_md


def _merge_summaries(summaries: list[dict[str, Any]], settings) -> dict[str, Any]:
    """여러 청크의 요약을 통합."""
    # 부분 요약 텍스트 생성
    parts = []
    for i, s in enumerate(summaries, 1):
        parts.append(f"=== 부분 {i} ===\n제목: {s.get('title', '')}\n요약:\n{s.get('summary', '')}")

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
            all_keywords.update(s.get('keywords', []))
        if not result.get('keywords'):
            result['keywords'] = list(all_keywords)[:10]

        logger.info(f"Merged {len(summaries)} summaries into final result")
        return result

    except Exception as e:
        logger.warning(f"Merge failed, using first chunk: {e}")
        return summaries[0]


def _extract_title_fallback(text: str) -> str:
    """제목 추출 실패 시 대체 방법."""
    first_sentence = text.split('.')[0].strip()
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

    async def summarize(self, file_id: int) -> None:
        """LLM 요약 수행."""
        file_obj = await self.file_repo.get_file(file_id)
        if not file_obj:
            logger.warning("[LLM] File not found, skipping summarization: file_id=%s", file_id)
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

        if file_obj.status == FileStatus.COMPLETED and file_obj.summary_md:
             logger.info("File %s already completed with summary, skipping", file_id)
             return

        text_to_summarize = ""
        if file_obj.content_type == ContentType.AUDIO:
            transcription = await self.transcription_repo.get_by_file_id(file_id)
            if transcription:
                transcript_data = transcription.transcription or {}
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

        logger.info("Starting LLM summarization for file_id={} (provider={})", file_id, self.settings.llm_provider)

        start = time.perf_counter()
        try:
            await self.file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
            await self.file_repo.add_llm_log(
                file_id,
                log={"event": "summarizing_started", "previous_status": file_obj.status.value},
                message="LLM summarization started",
            )
            await self.session.commit()

            # 동기 함수인 summarize_transcription을 Executor에서 실행
            loop = asyncio.get_running_loop()
            title, summary_md = await loop.run_in_executor(
                None,
                summarize_transcription,
                text_to_summarize
            )

        except Exception as exc:
            logger.exception("LLM summarization failed for file_id={}", file_id)
            raise

        elapsed = time.perf_counter() - start

        await self.file_repo.update_title(file_id, title)
        await self.file_repo.update_summary_markdown(file_id, summary_md)
        await self.file_repo.update_file_status(file_id, FileStatus.COMPLETED)
        await self.file_repo.add_llm_log(
            file_id,
            log={"event": "summarizing_completed", "duration_sec": elapsed},
            message="LLM summarization completed",
        )
        await self.session.commit()
        logger.info("LLM summary stored for file_id={} ({:.2f}s)", file_id, elapsed)
