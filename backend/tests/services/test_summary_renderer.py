"""summary_renderer 단위 테스트."""

from app.services.block_generator import BlockState, SectionsState
from app.services.summary_renderer import (
    extract_title,
    render_markdown,
)
from app.services.summary_templates import BlockStatus, get_template


def _build_full_state() -> SectionsState:
    state = SectionsState(
        template_id="default",
        started_at="2026-05-14T00:00:00+00:00",
        updated_at="2026-05-14T00:01:00+00:00",
        round=1,
    )
    state.blocks["title"] = BlockState(
        key="title", label="제목", type="text",
        status=BlockStatus.SUCCESS, content="AI와 직장",
    )
    state.blocks["keywords"] = BlockState(
        key="keywords", label="키워드", type="list",
        status=BlockStatus.SUCCESS, content=["AI", "직장 감소"],
    )
    state.blocks["headings"] = BlockState(
        key="headings", label="목차", type="list",
        status=BlockStatus.SUCCESS,
        content=["AI 도입과 직장 감소의 배경", "개인의 경력 변화와 감정 반응"],
    )
    state.blocks["section_0"] = BlockState(
        key="section_0", label="AI 도입과 직장 감소의 배경", type="long_text",
        status=BlockStatus.SUCCESS,
        content="AI 도입이 빠르게 진행되면서 일자리 감소가 현실화되고 있다.",
    )
    state.blocks["section_1"] = BlockState(
        key="section_1", label="개인의 경력 변화와 감정 반응", type="long_text",
        status=BlockStatus.SUCCESS,
        content="개인은 경력 단절을 겪으며 불안과 좌절을 호소하기도 한다.",
    )
    return state


def test_render_full_default_template():
    template = get_template("default")
    state = _build_full_state()
    md = render_markdown(template, state)

    assert "## 키워드" in md
    assert "AI, 직장 감소" in md
    assert "## 목차" in md
    assert "- AI 도입과 직장 감소의 배경" in md
    assert "- 개인의 경력 변화와 감정 반응" in md
    assert "## 핵심 요약" in md
    assert "## 상세 내용" in md
    assert "### AI 도입과 직장 감소의 배경" in md
    assert "### 개인의 경력 변화와 감정 반응" in md


def test_failed_section_excluded_no_placeholder():
    """실패 섹션은 출력에서 완전히 제외 (placeholder 노출 X)."""
    template = get_template("default")
    state = _build_full_state()
    state.blocks["section_1"].status = BlockStatus.FAILED
    state.blocks["section_1"].content = None

    md = render_markdown(template, state)

    assert "### AI 도입과 직장 감소의 배경" in md
    # 실패 섹션 본문/제목 어디에도 노출되지 않아야 함
    assert "### 개인의 경력 변화와 감정 반응" not in md
    assert "생성에 실패" not in md
    assert "재요약" not in md


def test_title_extraction():
    state = _build_full_state()
    assert extract_title(state) == "AI와 직장"


def test_title_missing_returns_empty():
    state = SectionsState(
        template_id="default",
        started_at="2026-05-14T00:00:00+00:00",
        updated_at="2026-05-14T00:00:00+00:00",
    )
    assert extract_title(state) == ""


def test_pending_blocks_excluded():
    template = get_template("default")
    state = SectionsState(
        template_id="default",
        started_at="2026-05-14T00:00:00+00:00",
        updated_at="2026-05-14T00:00:00+00:00",
    )
    state.blocks["title"] = BlockState(
        key="title", label="제목", type="text", status=BlockStatus.PENDING,
    )
    md = render_markdown(template, state)
    # 모든 block이 pending이면 빈 결과
    assert md == ""
