"""BlockGenerator 호출 공통 helper.

stream_consumer(자동 트리거), content_service(manual retry)에서 동일 호출 패턴을
공유한다. block 완료마다 summary_sections JSONB 점진 저장, 모든 required block이
성공한 경우에만 summary_md를 채워 반환한다.
"""

from __future__ import annotations

from typing import Callable

from ..db.session import AsyncSessionLocal
from ..repositories.file_repository import FileRepository
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
        progress_emit: (pct: float, message: str) 콜백. PR-B에서 SSE 발행 추가 예정.

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
        if progress_emit is not None:
            blocks = list(state.blocks.values())
            total = len(blocks) or 1
            done = sum(1 for b in blocks if b.status == BlockStatus.SUCCESS)
            pct = round((done / total) * 100, 1)
            try:
                progress_emit(pct, f"섹션 {done}/{total} 완료")
            except Exception:
                pass

    state = await generator.generate(text, on_block_complete=_persist)
    template = generator.template
    success = state.all_required_success(template)
    summary_md = render_markdown(template, state) if success else ""
    title = extract_title(state)
    return title, summary_md, success
