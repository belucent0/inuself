"""BlockGenerator partial retry 통합 테스트.

실제 vLLM 호출 없이 mock으로 라운드 진행/실패 회복/circuit breaker 동작을 검증한다.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.block_generator import (
    BlockGenerator,
    BlockState,
    SectionsState,
)
from app.services.summary_templates import BlockStatus, get_template


METADATA_RESPONSE = """```json
{
  "title": "AI와 직장",
  "keywords": "AI|직장 감소",
  "toc": "AI 도입과 직장 감소의 배경|개인의 경력 변화와 감정 반응"
}
```"""

SECTION_RESPONSE_0 = (
    '```json\n{"content": "AI 도입이 빠르게 진행되면서 일자리 감소가 현실화되고 있다. '
    '국내외 여러 사례가 이를 뒷받침한다."}\n```'
)
SECTION_RESPONSE_1 = (
    '```json\n{"content": "개인은 경력 단절을 겪으며 불안과 좌절을 호소하기도 한다. '
    '재교육과 진로 전환이 중요한 대안으로 떠오른다."}\n```'
)


def _patch_path():
    return "app.services.block_generator.request_ai_gateway_completion_async"


@pytest.mark.asyncio
async def test_happy_path_all_blocks_success():
    """모든 block 1라운드 성공 → 정상 종료."""
    responses = iter([METADATA_RESPONSE, SECTION_RESPONSE_0, SECTION_RESPONSE_1])

    async def fake_call(**_kwargs):
        return next(responses)

    with patch(_patch_path(), side_effect=fake_call):
        gen = BlockGenerator()
        # 백오프 시간 회피
        with patch("app.services.block_generator.ROUND_BACKOFF_SECONDS", (0, 0, 0, 0, 0)):
            state = await gen.generate("긴 텍스트 " * 100)

    assert state.blocks["title"].status == BlockStatus.SUCCESS
    assert state.blocks["title"].content == "AI와 직장"
    assert state.blocks["headings"].content == [
        "AI 도입과 직장 감소의 배경",
        "개인의 경력 변화와 감정 반응",
    ]
    assert state.blocks["section_0"].status == BlockStatus.SUCCESS
    assert state.blocks["section_1"].status == BlockStatus.SUCCESS
    assert state.all_required_success(gen.template)


def _topic_marker(topic: str) -> str:
    """SECTION_GENERATION_TEMPLATE에서 topic을 식별할 수 있는 unique 문자열."""
    return f'"{topic}" 주제에 대한 상세'


@pytest.mark.asyncio
async def test_section_failure_then_retry_succeeds():
    """section_1이 1라운드에 실패해도 2라운드에서 성공해야 함."""
    call_count = {"section_1": 0}

    async def fake_call(**kwargs):
        messages = kwargs.get("messages") or []
        user_content = messages[1]["content"] if len(messages) > 1 else ""
        if _topic_marker("AI 도입과 직장 감소의 배경") in user_content:
            return SECTION_RESPONSE_0
        if _topic_marker("개인의 경력 변화와 감정 반응") in user_content:
            call_count["section_1"] += 1
            if call_count["section_1"] == 1:
                raise RuntimeError("transient vLLM error")
            return SECTION_RESPONSE_1
        return METADATA_RESPONSE

    with patch(_patch_path(), side_effect=fake_call):
        with patch("app.services.block_generator.ROUND_BACKOFF_SECONDS", (0, 0, 0, 0, 0)):
            gen = BlockGenerator()
            state = await gen.generate("긴 텍스트 " * 100)

    assert state.blocks["section_0"].status == BlockStatus.SUCCESS
    assert state.blocks["section_1"].status == BlockStatus.SUCCESS
    assert state.blocks["section_1"].attempts == 2
    assert state.all_required_success(gen.template)


@pytest.mark.asyncio
async def test_permanent_section_failure_after_cap():
    """section_1이 모든 라운드에서 실패하면 final state에 failed로 남는다."""

    async def fake_call(**kwargs):
        messages = kwargs.get("messages") or []
        user_content = messages[1]["content"] if len(messages) > 1 else ""
        if _topic_marker("AI 도입과 직장 감소의 배경") in user_content:
            return SECTION_RESPONSE_0
        if _topic_marker("개인의 경력 변화와 감정 반응") in user_content:
            raise RuntimeError("permanent vLLM error")
        return METADATA_RESPONSE

    with patch(_patch_path(), side_effect=fake_call):
        with patch("app.services.block_generator.ROUND_BACKOFF_SECONDS", (0, 0, 0, 0, 0)):
            with patch("app.services.block_generator.MAX_ROUNDS", 3):
                gen = BlockGenerator()
                state = await gen.generate("긴 텍스트 " * 100)

    assert state.blocks["section_0"].status == BlockStatus.SUCCESS
    assert state.blocks["section_1"].status == BlockStatus.FAILED
    assert not state.all_required_success(gen.template)


@pytest.mark.asyncio
async def test_incremental_persistence_callback():
    """block 완료 시점마다 콜백이 호출된다."""
    responses = iter([METADATA_RESPONSE, SECTION_RESPONSE_0, SECTION_RESPONSE_1])

    async def fake_call(**_kwargs):
        return next(responses)

    callback_blocks: list[str] = []

    async def cb(block: BlockState, _state: SectionsState):
        callback_blocks.append(f"{block.key}:{block.status.value}")

    with patch(_patch_path(), side_effect=fake_call):
        with patch("app.services.block_generator.ROUND_BACKOFF_SECONDS", (0, 0, 0, 0, 0)):
            gen = BlockGenerator()
            await gen.generate("긴 텍스트 " * 100, on_block_complete=cb)

    # title/keywords/headings는 묶음 추출이므로 셋 다 같은 라운드에 콜백
    assert "title:success" in callback_blocks
    assert "keywords:success" in callback_blocks
    assert "headings:success" in callback_blocks
    assert "section_0:success" in callback_blocks
    assert "section_1:success" in callback_blocks


@pytest.mark.asyncio
async def test_dynamic_section_expansion():
    """headings 성공 후 section_0/section_1이 expand된다."""
    responses = iter([METADATA_RESPONSE, SECTION_RESPONSE_0, SECTION_RESPONSE_1])

    async def fake_call(**_kwargs):
        return next(responses)

    with patch(_patch_path(), side_effect=fake_call):
        with patch("app.services.block_generator.ROUND_BACKOFF_SECONDS", (0, 0, 0, 0, 0)):
            gen = BlockGenerator()
            state = await gen.generate("긴 텍스트 " * 100)

    section_keys = sorted(k for k in state.blocks if k.startswith("section_"))
    assert section_keys == ["section_0", "section_1"]
    # 라벨은 headings 항목과 일치
    assert state.blocks["section_0"].label == "AI 도입과 직장 감소의 배경"
    assert state.blocks["section_1"].label == "개인의 경력 변화와 감정 반응"
