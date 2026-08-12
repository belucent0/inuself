"""generic block schema 기반 요약 생성 엔진.

기존 SectionGraphExecutor가 단일 라운드로 모든 섹션을 시도한 뒤 실패 항목을
영구히 누락시키던 구조를 대체한다. 핵심 차이:

- block 단위 추상화: title/keywords/headings/section_N 모두 block으로 통일
- partial retry: 실패한 block만 다음 라운드에서 재시도
- 라운드별 백오프 (5s → 30s → 2m → 5m → 10m)
- circuit breaker 연동: vLLM 영구 다운 시 라운드 즉시 종료
- incremental persistence: block 완료 시점에 즉시 콜백 (DB 저장 가능)
- 도메인 템플릿 확장 가능: BlockTemplate으로 schema 정의

PR-A 범위에서는 default 템플릿만 활성화되어 기존 출력과 동일한 markdown을
재현한다. core_summary는 LLM 호출이 아닌 renderer 측 가공 결과이므로 본
generator에서는 다루지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ..core.config import Settings, get_settings
from ..prompts.summary import (
    PHASE1_STRUCTURE_TEMPLATE_V2,
    SECTION_GENERATION_TEMPLATE,
    SUMMARY_SYSTEM_PROMPT,
)
from .ai_gateway_client import (
    AIGatewayClientError,
    request_ai_gateway_completion_async,
)
from .circuit_breaker import CircuitBreakerOpenError
from .summary_templates import BlockStatus, BlockTemplate, DEFAULT_TEMPLATE, get_template

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 정책 상수
# ---------------------------------------------------------------------------
MAX_ROUNDS = 10
MAX_TOTAL_SECONDS = 30 * 60  # 30분
ROUND_BACKOFF_SECONDS = (5, 30, 120, 300, 600)  # 이후 600 고정
SECTION_MIN_LENGTH = 50
# vLLM 부하 회피: 본문 섹션 병렬 호출 동시성 상한
# (vLLM Qwen3-VL-4B의 안정 동시 처리량 ≈ 2~3개. 그 이상은 큐 폭주로 timeout 유발)
SECTION_CONCURRENCY = 2


# ---------------------------------------------------------------------------
# 런타임 상태
# ---------------------------------------------------------------------------
@dataclass
class BlockState:
    """단일 block의 런타임 상태. DB JSONB로 직렬화된다."""

    key: str
    label: str
    type: str
    status: BlockStatus = BlockStatus.PENDING
    content: Any = None
    attempts: int = 0
    last_error: str | None = None
    depends_on: tuple[str, ...] = ()
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "status": self.status.value,
            "content": self.content,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "depends_on": list(self.depends_on),
            "completed_at": self.completed_at,
        }


@dataclass
class SectionsState:
    """summary_sections JSONB의 in-memory 표현. DB와 1:1."""

    template_id: str
    started_at: str
    updated_at: str
    round: int = 0
    blocks: dict[str, BlockState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "round": self.round,
            "blocks": [b.to_dict() for b in self.blocks.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionsState":
        """DB JSONB → in-memory SectionsState 복원. 부분 재생성 시 활용."""
        state = cls(
            template_id=data["template_id"],
            started_at=data["started_at"],
            updated_at=data["updated_at"],
            round=data.get("round", 0),
        )
        for b_data in data.get("blocks", []):
            block = BlockState(
                key=b_data["key"],
                label=b_data["label"],
                type=b_data["type"],
                status=BlockStatus(b_data["status"]),
                content=b_data.get("content"),
                attempts=b_data.get("attempts", 0),
                last_error=b_data.get("last_error"),
                depends_on=tuple(b_data.get("depends_on", [])),
                completed_at=b_data.get("completed_at"),
            )
            state.blocks[block.key] = block
        return state

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def all_required_success(self, template: BlockTemplate) -> bool:
        for b_def in template.blocks:
            if b_def.dynamic:
                # dynamic은 모두 success여야 함 (expand된 모든 인스턴스)
                expanded = [
                    b for b in self.blocks.values()
                    if b.key.startswith(b_def.key.rstrip("*"))
                    and b.key != b_def.key
                ]
                if not expanded:
                    return False  # 아직 expand 안 됨
                if any(b.status != BlockStatus.SUCCESS for b in expanded):
                    return False
            else:
                b = self.blocks.get(b_def.key)
                if b is None or b.status != BlockStatus.SUCCESS:
                    if b_def.required:
                        return False
        return True

    def has_unresolved(self, template: BlockTemplate) -> bool:
        """failed 상태(재시도 대상) 또는 pending 상태가 남아있는지."""
        for b in self.blocks.values():
            if b.status in (BlockStatus.FAILED, BlockStatus.PENDING):
                return True
        # 아직 expand 안 된 dynamic block이 있는지 확인
        for b_def in template.blocks:
            if b_def.dynamic:
                headings = self.blocks.get(b_def.expand_from or "")
                if (
                    headings
                    and headings.status == BlockStatus.SUCCESS
                    and not any(
                        b.key.startswith(b_def.key.rstrip("*")) and b.key != b_def.key
                        for b in self.blocks.values()
                    )
                ):
                    return True
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 엔진
# ---------------------------------------------------------------------------
BlockCallback = Callable[[BlockState, SectionsState], Awaitable[None]]


class BlockGenerator:
    """generic block schema 기반 요약 생성 엔진."""

    def __init__(
        self,
        settings: Settings | None = None,
        template_id: str = "default",
    ) -> None:
        self.settings = settings or get_settings()
        self.template = get_template(template_id)

    async def generate(
        self,
        transcript: str,
        *,
        initial_state: SectionsState | None = None,
        on_block_complete: BlockCallback | None = None,
    ) -> SectionsState:
        """요약 생성 메인 진입점.

        Args:
            transcript: 원본 텍스트
            initial_state: 부분 진행 상태에서 재개할 경우 (PR-C 부분 재생성)
            on_block_complete: block 완료 시점 콜백 (incremental persistence)

        Returns:
            최종 SectionsState
        """
        state = initial_state or self._init_state()
        started = time.monotonic()

        for round_idx in range(1, MAX_ROUNDS + 1):
            state.round = round_idx

            if time.monotonic() - started > MAX_TOTAL_SECONDS:
                logger.warning(
                    f"[BlockGenerator] 시간 cap({MAX_TOTAL_SECONDS}s) 도달 → 종료"
                )
                break

            if round_idx > 1:
                backoff = self._backoff_for(round_idx)
                logger.info(f"[BlockGenerator] round {round_idx} 백오프 {backoff}s")
                await asyncio.sleep(backoff)

            try:
                progressed = await self._run_round(
                    state, transcript, on_block_complete
                )
            except CircuitBreakerOpenError as exc:
                logger.warning(f"[BlockGenerator] circuit breaker open: {exc}")
                # 라운드 중단. 다음 라운드 백오프 후 재진입.
                continue

            if not progressed:
                # 더 시도할 block이 없거나 모두 처리됨
                logger.info(
                    "[BlockGenerator] round %d: 진행할 block 없음, 종료",
                    round_idx,
                )
                break

            if state.all_required_success(self.template):
                logger.info("[BlockGenerator] 모든 required block 성공 → 종료")
                break

        return state

    # -------------------------------------------------------------------
    # 라운드 실행
    # -------------------------------------------------------------------
    async def _run_round(
        self,
        state: SectionsState,
        transcript: str,
        cb: BlockCallback | None,
    ) -> bool:
        progressed = False

        # 1. 묶음 추출 (title/keywords/headings)
        progressed |= await self._run_metadata_group(state, transcript, cb)

        # 2. headings 성공 시 section_* expand
        self._expand_dynamic_blocks(state)

        # 3. 본문 섹션 병렬 시도 (depends_on 만족하는 pending/failed만)
        progressed |= await self._run_sections(state, transcript, cb)

        state.touch()
        return progressed

    async def _run_metadata_group(
        self,
        state: SectionsState,
        transcript: str,
        cb: BlockCallback | None,
    ) -> bool:
        """title/keywords/headings 묶음 추출. 한 LLM 호출."""
        group_keys = ("title", "keywords", "headings")
        targets = [
            state.blocks[k]
            for k in group_keys
            if state.blocks[k].status != BlockStatus.SUCCESS
        ]
        if not targets:
            return False

        for b in targets:
            b.attempts += 1

        try:
            prompt = PHASE1_STRUCTURE_TEMPLATE_V2.format(transcript=transcript[:10000])
            response = await request_ai_gateway_completion_async(
                settings=self.settings,
                model=self.settings.ai_gateway_model_summarize,
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            parsed = self._parse_metadata_response(response)
        except CircuitBreakerOpenError:
            raise
        except (AIGatewayClientError, ValueError, Exception) as exc:
            err = f"metadata extraction failed: {exc}"
            for b in targets:
                b.status = BlockStatus.FAILED
                b.last_error = err
            logger.warning(f"[BlockGenerator] {err}")
            for b in targets:
                if cb is not None:
                    await cb(b, state)
            return True  # 진행은 발생함 (실패 기록)

        # 셋 다 검증되어야 통과 (현재 코드 정책 유지)
        if (
            parsed.get("title")
            and parsed.get("keywords")
            and parsed.get("headings")
        ):
            now = _now_iso()
            for b, value in (
                (state.blocks["title"], parsed["title"]),
                (state.blocks["keywords"], parsed["keywords"]),
                (state.blocks["headings"], parsed["headings"]),
            ):
                b.status = BlockStatus.SUCCESS
                b.content = value
                b.last_error = None
                b.completed_at = now
                if cb is not None:
                    await cb(b, state)
        else:
            for b in targets:
                b.status = BlockStatus.FAILED
                b.last_error = "metadata response missing title/keywords/headings"
                if cb is not None:
                    await cb(b, state)

        return True

    async def _run_sections(
        self,
        state: SectionsState,
        transcript: str,
        cb: BlockCallback | None,
    ) -> bool:
        # depends_on이 모두 success인 section_* 중 미완료
        targets = [
            b
            for b in state.blocks.values()
            if b.key.startswith("section_")
            and b.status != BlockStatus.SUCCESS
            and all(
                state.blocks.get(d)
                and state.blocks[d].status == BlockStatus.SUCCESS
                for d in b.depends_on
            )
        ]
        if not targets:
            return False

        title_block = state.blocks.get("title")
        keywords_block = state.blocks.get("keywords")
        headings_block = state.blocks.get("headings")
        title = title_block.content if title_block else ""
        keywords = keywords_block.content if keywords_block else []
        headings = headings_block.content if headings_block else []

        async def _one(block: BlockState) -> None:
            block.attempts += 1
            try:
                prompt = SECTION_GENERATION_TEMPLATE.format(
                    topic=block.label,
                    toc="|".join(headings),
                    keywords="|".join(keywords),
                    title=title,
                    transcript=transcript,
                )
                response = await request_ai_gateway_completion_async(
                    settings=self.settings,
                    model=self.settings.ai_gateway_model_summarize,
                    messages=[
                        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                content = self._parse_section_response(response)
                if len(content) < SECTION_MIN_LENGTH:
                    block.status = BlockStatus.FAILED
                    block.last_error = f"content too short ({len(content)} chars)"
                else:
                    block.status = BlockStatus.SUCCESS
                    block.content = content
                    block.completed_at = _now_iso()
                    block.last_error = None
            except CircuitBreakerOpenError:
                raise
            except (AIGatewayClientError, Exception) as exc:
                block.status = BlockStatus.FAILED
                block.last_error = str(exc)
                logger.warning(
                    f"[BlockGenerator] section '{block.label}' failed: {exc}"
                )

            if cb is not None:
                await cb(block, state)

        # 동시성 제한 (vLLM 큐 폭주 회피)
        semaphore = asyncio.Semaphore(SECTION_CONCURRENCY)

        async def _gated(block: BlockState) -> None:
            async with semaphore:
                await _one(block)

        # circuit breaker 차단 시 한 섹션이라도 raise하면 전체 라운드 중단
        await asyncio.gather(*(_gated(b) for b in targets))
        return True

    # -------------------------------------------------------------------
    # 응답 파싱
    # -------------------------------------------------------------------
    def _parse_metadata_response(self, response: str) -> dict[str, Any]:
        """title/keywords|/headings 묶음 응답 파싱."""
        text = response.strip()
        json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"metadata JSON parse failed: {exc}") from exc

        title = (data.get("title") or "").strip()
        keywords_raw = data.get("keywords", "")
        # 기존 프롬프트는 toc라는 키를 쓰지만 schema 상으로는 headings로 매핑
        headings_raw = data.get("toc", "") or data.get("headings", "")

        if isinstance(keywords_raw, str):
            keywords = [k.strip() for k in keywords_raw.split("|") if k.strip()]
        else:
            keywords = list(keywords_raw)

        if isinstance(headings_raw, str):
            headings = [t.strip() for t in headings_raw.split("|") if t.strip()]
        else:
            headings = list(headings_raw)

        return {"title": title, "keywords": keywords, "headings": headings}

    def _parse_section_response(self, response: str) -> str:
        """단일 섹션 본문 응답 파싱."""
        text = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return ""

        content = (data.get("content") or "").strip()
        return content

    # -------------------------------------------------------------------
    # 헬퍼
    # -------------------------------------------------------------------
    def _init_state(self) -> SectionsState:
        now = _now_iso()
        state = SectionsState(
            template_id=self.template.id,
            started_at=now,
            updated_at=now,
        )
        for b_def in self.template.blocks:
            if b_def.dynamic:
                # expand는 headings 성공 후 동적으로 추가
                continue
            state.blocks[b_def.key] = BlockState(
                key=b_def.key,
                label=b_def.label,
                type=b_def.type,
                depends_on=b_def.depends_on,
            )
        return state

    def _expand_dynamic_blocks(self, state: SectionsState) -> None:
        for b_def in self.template.blocks:
            if not b_def.dynamic:
                continue
            source_block = state.blocks.get(b_def.expand_from or "")
            if not source_block or source_block.status != BlockStatus.SUCCESS:
                continue

            prefix = b_def.key.rstrip("*")  # 'section_'
            already_expanded = any(
                b.key.startswith(prefix) and b.key != b_def.key
                for b in state.blocks.values()
            )
            if already_expanded:
                continue

            for idx, item in enumerate(source_block.content or []):
                new_key = f"{prefix}{idx}"
                state.blocks[new_key] = BlockState(
                    key=new_key,
                    label=item,
                    type=b_def.type,
                    depends_on=b_def.depends_on,
                )

    def _backoff_for(self, round_idx: int) -> int:
        i = min(round_idx - 2, len(ROUND_BACKOFF_SECONDS) - 1)
        return ROUND_BACKOFF_SECONDS[max(i, 0)]
