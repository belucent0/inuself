"""Transcript 청크 단위 한국어 번역 실행기.

PR-A의 BlockGenerator partial retry 정책을 차용한다.
- segments을 N개씩 묶어 청크 1개 = LLM 호출 1회
- 청크 완료 시 transcription JSONB 점진 저장 + PR-B SSE 발행
- 실패한 청크만 다음 라운드에서 재시도

번역 결과는 transcription["segments"][i]["translation_ko"] 필드에 저장된다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ..core.config import get_settings
from ..db.session import AsyncSessionLocal
from ..prompts.translation import (
    TRANSLATION_CHUNK_TEMPLATE,
    TRANSLATION_SYSTEM_PROMPT,
)
from ..repositories.transcription_repository import TranscriptionRepository
from ..utils.event_publisher import publish_file_progress
from .ai_gateway_client import (
    AIGatewayClientError,
    request_ai_gateway_completion_async,
)
from .circuit_breaker import CircuitBreakerOpenError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 정책
# ---------------------------------------------------------------------------
CHUNK_SIZE = 1  # 한 청크에 묶을 segment 수.
# 1.2B sLLM(EXAONE)은 일괄 N개 번역에서 응답 누락/병합으로 count mismatch가 잦다.
# 단일 segment 단위는 누락 위험이 사실상 0이고, 짧은 응답으로 청크당 시간도 짧아
# 총 시간은 청크 4 + 재시도 폭주 시나리오보다 오히려 빠르다.
MAX_ROUNDS = 5
ROUND_BACKOFF_SECONDS = (5, 30, 120, 300, 600)
# vLLM 큐 폭주 → AI Gateway timeout 회피를 위해 직렬 처리. 다른 사용자/요약 작업과
# GPU 경합이 일어나면 동시 호출이 누적되어 응답 시간이 폭증하기 때문.
CHUNK_CONCURRENCY = 1
MAX_TOTAL_SECONDS = 30 * 60


@dataclass
class ChunkState:
    chunk_idx: int
    segment_indices: list[int]  # segments 배열 안의 위치 (id가 아닌 위치)
    status: str = "pending"  # pending | success | failed
    attempts: int = 0
    last_error: str | None = None
    translations: list[str] = field(default_factory=list)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _persist_progress_only(file_id: UUID, transcription_data: dict) -> None:
    """transcription JSONB 전체 patch — translation_progress 변경분 반영."""
    async with AsyncSessionLocal() as cb_session:
        cb_repo = TranscriptionRepository(cb_session)
        await cb_repo.update_transcription_jsonb(file_id, transcription_data)
        await cb_session.commit()


def _update_progress_dict(
    transcription_data: dict,
    chunk_states: list[ChunkState],
    target_lang: str,
    active: bool,
) -> None:
    """transcription_data["translation_progress"] in-place 갱신."""
    done = sum(1 for s in chunk_states if s.status == "success")
    failed = sum(1 for s in chunk_states if s.status == "failed")
    total = len(chunk_states)
    prev = transcription_data.get("translation_progress") or {}
    transcription_data["translation_progress"] = {
        "active": active,
        "target_lang": target_lang,
        "chunks_done": done,
        "chunks_failed": failed,
        "chunks_total": total,
        "started_at": prev.get("started_at") or _now_iso(),
        "updated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------
async def translate_transcription(file_id: UUID, target_lang: str = "ko") -> dict[str, Any]:
    """Transcription의 segments를 청크 단위로 번역.

    Args:
        file_id: Content.file_id (UUID)
        target_lang: 현재 "ko"만 지원. 추후 확장.

    Returns:
        {"success": bool, "translated_chunks": int, "total_chunks": int, "failed_chunks": int}

    Side effects:
        - transcription.transcription JSONB의 각 segment에 translation_ko 추가
        - PR-B SSE: chunk_completed / translation_finalized 이벤트 발행
    """
    if target_lang != "ko":
        raise ValueError(f"Unsupported target_lang: {target_lang}")

    # 1. transcription 로드
    async with AsyncSessionLocal() as session:
        repo = TranscriptionRepository(session)
        transcription_obj = await repo.get_by_file_id(file_id)
        if not transcription_obj:
            raise ValueError(f"Transcription not found for file_id={file_id}")
        transcription_data: dict[str, Any] = copy.deepcopy(transcription_obj.transcription or {})

    segments = transcription_data.get("segments") or []
    if not segments:
        raise ValueError("No segments to translate")

    # 2. 청크 분할 + 이미 번역된 segment 스킵
    chunk_states: list[ChunkState] = []
    for chunk_idx, start in enumerate(range(0, len(segments), CHUNK_SIZE)):
        indices = list(range(start, min(start + CHUNK_SIZE, len(segments))))
        # 청크의 모든 segment가 이미 translation_ko 있으면 skip
        if all(segments[i].get("translation_ko") for i in indices):
            chunk_states.append(
                ChunkState(chunk_idx=chunk_idx, segment_indices=indices, status="success")
            )
        else:
            chunk_states.append(ChunkState(chunk_idx=chunk_idx, segment_indices=indices))

    total_chunks = len(chunk_states)
    if all(s.status == "success" for s in chunk_states):
        logger.info(f"[Translation] file_id={file_id} all chunks already translated, skip")
        return {
            "success": True,
            "translated_chunks": total_chunks,
            "total_chunks": total_chunks,
            "failed_chunks": 0,
        }

    # 진행 상태 dict 초기화 — 새로고침 시 frontend 복원 가능
    _update_progress_dict(transcription_data, chunk_states, target_lang, active=True)
    await _persist_progress_only(file_id, transcription_data)

    settings = get_settings()
    started = time.monotonic()

    # 3. 라운드 루프
    for round_idx in range(1, MAX_ROUNDS + 1):
        if time.monotonic() - started > MAX_TOTAL_SECONDS:
            logger.warning(f"[Translation] time cap reached, stop at round {round_idx}")
            break

        targets = [s for s in chunk_states if s.status != "success"]
        if not targets:
            break

        if round_idx > 1:
            backoff = ROUND_BACKOFF_SECONDS[
                min(round_idx - 2, len(ROUND_BACKOFF_SECONDS) - 1)
            ]
            logger.info(f"[Translation] round {round_idx} backoff {backoff}s")
            await asyncio.sleep(backoff)

        sem = asyncio.Semaphore(CHUNK_CONCURRENCY)

        async def _run_chunk(state: ChunkState) -> None:
            async with sem:
                await _translate_one_chunk(
                    state=state,
                    segments=segments,
                    settings=settings,
                    file_id=file_id,
                    target_lang=target_lang,
                    chunk_states=chunk_states,
                    transcription_data=transcription_data,
                )

        try:
            await asyncio.gather(*(_run_chunk(s) for s in targets))
        except CircuitBreakerOpenError as exc:
            logger.warning(f"[Translation] circuit breaker open: {exc}")
            # 다음 라운드 백오프 후 재진입
            continue

    # 4. 종료 이벤트
    success_count = sum(1 for s in chunk_states if s.status == "success")
    failed_count = sum(1 for s in chunk_states if s.status == "failed")
    success = failed_count == 0 and success_count == total_chunks

    # active=false로 마감 — 새로고침해도 진행 카드 안 보이게
    _update_progress_dict(transcription_data, chunk_states, target_lang, active=False)
    transcription_data["translation_progress"]["success"] = success
    await _persist_progress_only(file_id, transcription_data)

    try:
        publish_file_progress(
            file_id=file_id,
            status="translated" if success else "translation_partial_failure",
            step="translation_finalized",
            progress=100.0 if success else round((success_count / total_chunks) * 100, 1),
            message=(
                "번역 완료"
                if success
                else f"{success_count}/{total_chunks} 청크 완료 (실패 {failed_count})"
            ),
            metadata={
                "event_subtype": "translation_finalized",
                "target_lang": target_lang,
                "success": success,
                "chunks_done": success_count,
                "chunks_total": total_chunks,
                "chunks_failed": failed_count,
            },
        )
    except Exception as exc:
        logger.warning(f"[Translation] finalized SSE publish failed: {exc}")

    return {
        "success": success,
        "translated_chunks": success_count,
        "total_chunks": total_chunks,
        "failed_chunks": failed_count,
    }


# ---------------------------------------------------------------------------
async def _translate_one_chunk(
    *,
    state: ChunkState,
    segments: list[dict],
    settings,
    file_id: UUID,
    target_lang: str,
    chunk_states: list[ChunkState],
    transcription_data: dict,
) -> None:
    """청크 1개 번역 → DB 저장 → SSE 발행."""
    state.attempts += 1
    chunk_segments = [segments[i] for i in state.segment_indices]
    numbered = "\n".join(
        f"{n+1}. {seg.get('text', '').strip()}"
        for n, seg in enumerate(chunk_segments)
    )
    prompt = TRANSLATION_CHUNK_TEMPLATE.format(
        count=len(chunk_segments),
        numbered_segments=numbered,
    )

    try:
        # PR-Translate.3: 번역은 ai-translate 컨테이너(EXAONE 4.0-1.2B) 직접 호출.
        # ai-llm(Qwen3-VL-4B)의 GPU 큐 경합 분리 + 한국어 dual-training 모델로 quality ↑.
        response = await request_ai_gateway_completion_async(
            settings=settings,
            base_url=settings.ai_translate_url,
            api_key="none",  # 내부 컨테이너 인증 없음
            model=settings.ai_translate_model,
            messages=[
                {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        translations = _parse_translations(response, expected=len(chunk_segments))
    except CircuitBreakerOpenError:
        raise
    except (AIGatewayClientError, ValueError, Exception) as exc:
        state.status = "failed"
        state.last_error = str(exc)
        logger.warning(
            f"[Translation] chunk {state.chunk_idx} failed (attempt {state.attempts}): {exc}"
        )
        # 실패도 progress에 반영 (재시도 카운트)
        _update_progress_dict(transcription_data, chunk_states, target_lang, active=True)
        try:
            await _persist_progress_only(file_id, transcription_data)
        except Exception:
            pass
        await _publish_chunk_event(
            file_id, state, chunk_states, target_lang, success=False
        )
        return

    # 결과 segments에 patch
    for seg_idx, translation in zip(state.segment_indices, translations):
        segments[seg_idx]["translation_ko"] = translation
    state.translations = translations
    state.status = "success"
    state.last_error = None

    # translation_progress 갱신 (새로고침 복원 + frontend 카드 정확도)
    _update_progress_dict(transcription_data, chunk_states, target_lang, active=True)

    # DB 저장 (callback별 새 session — race 회피)
    transcription_data["segments"] = segments
    try:
        async with AsyncSessionLocal() as cb_session:
            cb_repo = TranscriptionRepository(cb_session)
            await cb_repo.update_transcription_jsonb(file_id, transcription_data)
            await cb_session.commit()
    except Exception as exc:
        # DB 저장 실패는 chunk 실패로 처리
        state.status = "failed"
        state.last_error = f"DB persist failed: {exc}"
        logger.exception(f"[Translation] chunk {state.chunk_idx} DB save failed")
        await _publish_chunk_event(
            file_id, state, chunk_states, target_lang, success=False
        )
        return

    await _publish_chunk_event(
        file_id, state, chunk_states, target_lang, success=True
    )


# ---------------------------------------------------------------------------
async def _publish_chunk_event(
    file_id: UUID,
    state: ChunkState,
    chunk_states: list[ChunkState],
    target_lang: str,
    success: bool,
) -> None:
    done = sum(1 for s in chunk_states if s.status == "success")
    failed = sum(1 for s in chunk_states if s.status == "failed")
    total = len(chunk_states) or 1
    pct = round((done / total) * 100, 1)
    try:
        publish_file_progress(
            file_id=file_id,
            status="translating",
            step="translation_chunk",
            progress=pct,
            message=f"번역 {done}/{total} 청크",
            metadata={
                "event_subtype": "chunk_completed",
                "target_lang": target_lang,
                "chunk_idx": state.chunk_idx,
                "chunk_status": state.status,
                "chunks_done": done,
                "chunks_failed": failed,
                "chunks_total": total,
            },
        )
    except Exception as exc:
        logger.warning(f"[Translation] chunk SSE publish failed: {exc}")


# ---------------------------------------------------------------------------
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_translations(response: str, expected: int) -> list[str]:
    """LLM 응답에서 translations 배열 추출. 길이가 expected와 일치해야 한다."""
    text = (response or "").strip()
    match = _JSON_BLOCK_RE.search(text)
    payload = match.group(1) if match else text
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"translation response not valid JSON: {exc}") from exc
    translations = data.get("translations")
    if not isinstance(translations, list):
        raise ValueError("translations field missing or not a list")
    if len(translations) != expected:
        raise ValueError(
            f"translations count mismatch: got {len(translations)}, expected {expected}"
        )
    cleaned = [str(t).strip() for t in translations]
    if any(not t for t in cleaned):
        raise ValueError("empty translation entry detected")
    return cleaned
