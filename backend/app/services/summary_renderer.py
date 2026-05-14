"""block 데이터를 markdown 문자열로 렌더링.

default 템플릿 기준 출력은 PR-A 이전과 동일:
- ## 키워드
- ## 목차
- ## 핵심 요약  (sections에서 자동 가공)
- ## 상세 내용 + ### {label} 반복

성공하지 못한 block은 조립에서 제외한다 (placeholder/안내 문구 일절 미삽입).
"""

from __future__ import annotations

from .block_generator import BlockState, SectionsState
from .summary_templates import BlockStatus, BlockTemplate


CORE_SUMMARY_MAX_ITEMS = 5


def render_markdown(template: BlockTemplate, state: SectionsState) -> str:
    """SectionsState를 사용자에게 노출되는 markdown 문자열로 변환."""

    parts: list[str] = []

    # 1. 키워드
    kw_block = _success(state, "keywords")
    if kw_block:
        parts.append("## 키워드")
        parts.append(", ".join(kw_block.content or []))
        parts.append("")

    # 2. 목차
    headings_block = _success(state, "headings")
    if headings_block:
        parts.append("## 목차")
        for item in headings_block.content or []:
            parts.append(f"- {item}")
        parts.append("")

    # 3. 본문 섹션 (먼저 수집)
    section_blocks = _ordered_section_blocks(state)

    # 4. 핵심 요약 (본문 첫 문장 자동 가공, LLM 호출 없음)
    if section_blocks:
        core_lines = _build_core_summary(section_blocks)
        if core_lines:
            parts.append("## 핵심 요약")
            parts.extend(core_lines)
            parts.append("")

    # 5. 상세 내용
    if section_blocks:
        parts.append("## 상세 내용")
        for block in section_blocks:
            parts.append(f"### {block.label}")
            parts.append(block.content)
            parts.append("")

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
def _success(state: SectionsState, key: str) -> BlockState | None:
    block = state.blocks.get(key)
    if block and block.status == BlockStatus.SUCCESS:
        return block
    return None


def _ordered_section_blocks(state: SectionsState) -> list[BlockState]:
    """section_N block을 인덱스 순서대로 정렬하여 반환. success만 포함."""
    items: list[tuple[int, BlockState]] = []
    for block in state.blocks.values():
        if not block.key.startswith("section_"):
            continue
        if block.status != BlockStatus.SUCCESS:
            continue
        try:
            idx = int(block.key.removeprefix("section_"))
        except ValueError:
            continue
        items.append((idx, block))
    items.sort(key=lambda x: x[0])
    return [b for _, b in items]


def _build_core_summary(sections: list[BlockState]) -> list[str]:
    """본문 첫 문장을 그대로 핵심 요약 bullet으로 사용 (자르지 않음).

    사용자가 핵심 요약에서 의도된 흐름을 끊지 않고 읽을 수 있도록
    "..." 트리밍을 제거한다.
    """
    lines: list[str] = []
    for block in sections[:CORE_SUMMARY_MAX_ITEMS]:
        content = (block.content or "").strip()
        if not content:
            continue
        # 첫 문장 추출 (. ! ? 기준). 구분자 미발견 시 본문 전체.
        first = content
        for sep in (".", "!", "?"):
            cut = content.find(sep)
            if cut >= 0:
                first = content[: cut + 1]  # 종결 부호 포함하여 자연스럽게
                break
        first = first.strip()
        if first:
            lines.append(f"- {first}")
    return lines


def extract_title(state: SectionsState) -> str:
    """state에서 title을 추출. 없으면 빈 문자열."""
    block = _success(state, "title")
    if block and block.content:
        return str(block.content).strip()
    return ""
