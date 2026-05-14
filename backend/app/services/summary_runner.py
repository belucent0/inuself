"""BlockGenerator 호출 공통 helper.

stream_consumer(자동 트리거), content_service(manual retry)에서 동일 호출 패턴을
공유한다. block 완료마다 summary_sections JSONB 점진 저장, 모든 required block이
성공한 경우에만 summary_md를 채워 반환한다.
"""

from __future__ import annotations

from typing import Callable

from ..core.logging import logger
from ..db.session import AsyncSessionLocal
from ..repositories.file_repository import FileRepository
from ..utils.event_publisher import publish_file_progress
from .block_generator import BlockGenerator
from .summary_renderer import extract_title, render_markdown
from .summary_templates import BlockStatus


async def summarize_with_block_generator(
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
