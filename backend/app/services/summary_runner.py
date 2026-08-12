"""BlockGenerator 호출 공통 helper.

stream_consumer(자동 트리거), content_service(manual retry)에서 동일 호출 패턴을
공유한다. block 완료마다 summary_sections JSONB 점진 저장, 모든 required block이
성공한 경우에만 summary_md를 채워 반환한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from typing import Callable

from redis.exceptions import LockError, RedisError

from ..core.logging import logger
from ..core.redis import get_redis_client
from ..db.session import AsyncSessionLocal
from ..repositories.content_repository import ContentRepository
from ..repositories.file_repository import FileRepository
from ..utils.event_publisher import publish_file_progress
from .block_generator import BlockGenerator, SectionsState
from .summary_renderer import extract_title, render_markdown
from .summary_templates import BlockStatus


@asynccontextmanager
async def _summary_write_lock(file_id):
    lock = get_redis_client().lock(
        f"lock:summary:write:{file_id}", timeout=40 * 60, blocking=False
    )
    if not await lock.acquire(blocking=False):
        raise ValueError("Another summarization is already in progress")
    try:
        yield
    finally:
        with suppress(LockError, RedisError):
            await lock.release()


async def summarize_with_block_generator(
    file_id, text, progress_emit: Callable | None = None,
):
    async with _summary_write_lock(file_id):
        return await _summarize_with_block_generator_unlocked(
            file_id, text, progress_emit
        )


async def _summarize_with_block_generator_unlocked(
    file_id, text, progress_emit: Callable | None = None,
):
    """BlockGenerator로 요약 실행 후 (title, summary_md, all_required_success) 반환.

    Args:
        file_id: Content.file_id (UUID)
        text: 요약 입력 텍스트
        progress_emit: (pct: float, message: str) 콜백. caller가 외부에 보고할 때 사용.

    block 완료 시마다 publish_file_progress로 block-level metadata 발행 →
    frontend가 SSE로 점진 렌더링 가능.

    Returns:
        (title, summary_md, success):
          - title: state에서 추출한 제목 (없으면 빈 문자열)
          - summary_md: 모든 required block 성공 시 markdown, 아니면 빈 문자열
          - success: 모든 required block 성공 여부
    """
    generator = BlockGenerator(template_id="default")

    async def _persist(block, state):
        async with AsyncSessionLocal() as cb_session:
            cb_repo = FileRepository(cb_session)
            await cb_repo.update_summary_sections(file_id, state.to_dict())
            await cb_session.commit()

        blocks = list(state.blocks.values())
        total = len(blocks) or 1
        done = sum(1 for b in blocks if b.status == BlockStatus.SUCCESS)
        failed = sum(1 for b in blocks if b.status == BlockStatus.FAILED)
        pct = round((done / total) * 100, 1)

        # SSE: block-level 진행 상태 발행 (frontend가 즉시 부분 노출 가능)
        try:
            publish_file_progress(
                file_id=file_id,
                status="summarizing",
                step="summary_block",
                progress=pct,
                message=f"섹션 {done}/{total} 완료",
                metadata={
                    "event_subtype": "block_completed",
                    "block_key": block.key,
                    "block_label": block.label,
                    "block_status": block.status.value,
                    "blocks_done": done,
                    "blocks_failed": failed,
                    "blocks_total": total,
                    "template_id": state.template_id,
                },
            )
        except Exception as exc:
            logger.warning(
                "[summary_runner] block_completed SSE publish failed: file_id={}, err={}",
                file_id, exc,
            )

        if progress_emit is not None:
            try:
                progress_emit(pct, f"섹션 {done}/{total} 완료")
            except Exception:
                pass

    state = await generator.generate(text, on_block_complete=_persist)
    template = generator.template
    success = state.all_required_success(template)
    summary_md = render_markdown(template, state) if success else ""
    title = extract_title(state)

    # 종료 이벤트 — frontend가 최종 결과로 전환할 수 있도록
    try:
        blocks = list(state.blocks.values())
        total = len(blocks) or 1
        done = sum(1 for b in blocks if b.status == BlockStatus.SUCCESS)
        failed = sum(1 for b in blocks if b.status == BlockStatus.FAILED)
        publish_file_progress(
            file_id=file_id,
            status="summarizing",
            step="summary_finalized",
            progress=100.0 if success else round((done / total) * 100, 1),
            message="요약 생성 완료" if success else f"섹션 {done}/{total} 완료 (일부 실패)",
            metadata={
                "event_subtype": "summary_finalized",
                "success": success,
                "blocks_done": done,
                "blocks_failed": failed,
                "blocks_total": total,
                "template_id": state.template_id,
            },
        )
    except Exception as exc:
        logger.warning(
            "[summary_runner] summary_finalized SSE publish failed: file_id={}, err={}",
            file_id, exc,
        )

    return title, summary_md, success


async def regenerate_block(
    file_id, block_key: str, text: str,
):
    async with _summary_write_lock(file_id):
        return await _regenerate_block_unlocked(file_id, block_key, text)


async def _regenerate_block_unlocked(
    file_id, block_key: str, text: str,
):
    """단일 block(또는 같은 group_extracts 내 형제 block들)을 재생성한다.

    기존 summary_sections를 그대로 둔 채 target block만 pending으로 reset 후
    BlockGenerator(initial_state=...)로 재시도. 다른 block은 SUCCESS 상태가
    유지되어 LLM 호출 없이 그대로 통과한다.

    Args:
        file_id: Content.file_id (UUID)
        block_key: 재생성 대상 block key (예: 'title', 'section_2')
        text: 요약 입력 텍스트 (transcription / OCR 본문)

    Returns:
        (title, summary_md, success): summarize_with_block_generator와 동일 형식.
    """
    # 1. 현재 sections 로드
    async with AsyncSessionLocal() as session:
        content_repo = ContentRepository(session)
        content = await content_repo.get_by_file_id(file_id)
        if not content or not content.summary_sections:
            raise ValueError(f"summary_sections not found for file_id={file_id}")
        sections_data = content.summary_sections

    state = SectionsState.from_dict(sections_data)
    generator = BlockGenerator(template_id=state.template_id)
    template = generator.template

    # 2. 재생성 대상 결정 — group_extracts에 속하면 group 전체 reset
    group = template.group_for(block_key)
    keys_to_reset: tuple[str, ...] = group if group else (block_key,)

    # dynamic block(section_*)도 단일 key로 처리
    for key in keys_to_reset:
        if key not in state.blocks:
            # section_N이 아직 expand 안 됐을 수도 — 안전하게 skip
            continue
        state.blocks[key].status = BlockStatus.PENDING
        state.blocks[key].content = None
        state.blocks[key].last_error = None
        state.blocks[key].completed_at = None

    # 3. 재시작 라운드 카운터 리셋 (백오프 영향 회피)
    state.round = 0

    async def _persist(block, st):
        async with AsyncSessionLocal() as cb_session:
            cb_repo = FileRepository(cb_session)
            await cb_repo.update_summary_sections(file_id, st.to_dict())
            await cb_session.commit()

        blocks = list(st.blocks.values())
        total = len(blocks) or 1
        done = sum(1 for b in blocks if b.status == BlockStatus.SUCCESS)
        failed = sum(1 for b in blocks if b.status == BlockStatus.FAILED)
        pct = round((done / total) * 100, 1)

        try:
            publish_file_progress(
                file_id=file_id,
                status="block_regenerating",
                step="summary_block",
                progress=pct,
                message=f"'{block.label}' 재생성",
                metadata={
                    "event_subtype": "block_regenerated",
                    "block_key": block.key,
                    "block_label": block.label,
                    "block_status": block.status.value,
                    "blocks_done": done,
                    "blocks_failed": failed,
                    "blocks_total": total,
                    "template_id": st.template_id,
                    "regenerate_target": block_key,
                },
            )
        except Exception as exc:
            logger.warning(
                "[summary_runner] block_regenerated SSE publish failed: file_id={}, err={}",
                file_id, exc,
            )

    state = await generator.generate(text, initial_state=state, on_block_complete=_persist)
    success = state.all_required_success(template)
    summary_md = render_markdown(template, state) if success else ""
    title = extract_title(state)

    # 종료 이벤트
    try:
        blocks = list(state.blocks.values())
        total = len(blocks) or 1
        done = sum(1 for b in blocks if b.status == BlockStatus.SUCCESS)
        failed = sum(1 for b in blocks if b.status == BlockStatus.FAILED)
        publish_file_progress(
            file_id=file_id,
            status="block_regenerated",
            step="summary_finalized",
            progress=100.0 if success else round((done / total) * 100, 1),
            message=f"'{block_key}' 재생성 완료" if success else f"'{block_key}' 재생성 실패",
            metadata={
                "event_subtype": "block_regenerate_finalized",
                "success": success,
                "blocks_done": done,
                "blocks_failed": failed,
                "blocks_total": total,
                "template_id": state.template_id,
                "regenerate_target": block_key,
            },
        )
    except Exception as exc:
        logger.warning(
            "[summary_runner] block_regenerate_finalized SSE publish failed: file_id={}, err={}",
            file_id, exc,
        )

    return title, summary_md, success
