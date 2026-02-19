"""멀티턴 대화 E2E 테스트.

시나리오:
  Turn 1: "안녕하세요" → 인사 응답 (mode=simple, 검색 없음)
  Turn 2: "대한민국의 수도는?" → "서울" 포함 응답
  Turn 3: "그 도시의 인구는?" → 서울 맥락 참조 응답

실행:
  E2E_BASE_URL=http://localhost:8000 \\
  E2E_LOGIN_ID=e2etest \\
  E2E_PASSWORD="Test1234!" \\
    python -m pytest tests/e2e/ -v
"""

from __future__ import annotations

import pytest

from .chat_client import ChatClient
from .models import MultiTurnFixtures, TurnAssertions, TurnResult


class TestMultiTurnChat:
    """멀티턴 대화 E2E 테스트 클래스."""

    # ------------------------------------------------------------------
    # 헬퍼
    # ------------------------------------------------------------------

    async def _execute_turn(
        self,
        client: ChatClient,
        thread_id: str | None,
        query: str,
    ) -> tuple[str, TurnResult]:
        """한 턴을 실행하고 (thread_id, TurnResult)를 반환합니다."""
        if thread_id is None:
            thread_id, message_id = await client.create_thread(query)
        else:
            thread_id, message_id = await client.add_message(thread_id, query)

        result = await client.stream_response(thread_id, message_id)
        return thread_id, result

    def _assert_turn(
        self,
        turn_num: int,
        query: str,
        assertions: TurnAssertions,
        result: TurnResult,
    ) -> list[str]:
        """단일 턴 검증 후 실패 메시지 목록을 반환합니다."""
        failures: list[str] = []
        prefix = f"[Turn {turn_num}: '{query[:30]}']"

        # 에러 없음 검증
        if assertions.no_error and result.had_error:
            failures.append(f"{prefix} 응답에 error 이벤트가 발생했습니다.")

        # 응답 시간 검증
        if result.elapsed_seconds > assertions.max_response_time_seconds:
            failures.append(
                f"{prefix} 응답 시간 초과 "
                f"({result.elapsed_seconds:.1f}s > "
                f"{assertions.max_response_time_seconds}s)"
            )

        # 응답 내용 없음 검증
        if not result.full_content.strip():
            failures.append(f"{prefix} 응답 내용이 비어 있습니다.")
            # content가 없으면 이후 content 검증은 건너뜀
            return failures

        content_lower = result.full_content.lower()

        # content_contains_any: OR 로직 (하나 이상 포함)
        if assertions.content_contains_any:
            if not any(
                kw.lower() in content_lower
                for kw in assertions.content_contains_any
            ):
                failures.append(
                    f"{prefix} content_contains_any 실패 — "
                    f"다음 중 하나가 포함되어야 합니다: "
                    f"{assertions.content_contains_any}\n"
                    f"  실제 응답 (앞 200자): {result.full_content[:200]!r}"
                )

        # content_not_contains: 금지 키워드
        for kw in assertions.content_not_contains:
            if kw.lower() in content_lower:
                failures.append(
                    f"{prefix} content_not_contains 실패 — "
                    f"'{kw}'가 응답에 포함되어선 안 됩니다.\n"
                    f"  실제 응답 (앞 200자): {result.full_content[:200]!r}"
                )

        # context_check: 이전 턴 맥락 참조
        if assertions.context_check:
            refs = assertions.context_check.must_reference_any
            if not any(ref.lower() in content_lower for ref in refs):
                failures.append(
                    f"{prefix} context_check 실패 — "
                    f"이전 맥락 참조 키워드 {refs} 중 하나가 없습니다.\n"
                    f"  실제 응답 (앞 200자): {result.full_content[:200]!r}"
                )

        # mode 검증
        if assertions.mode:
            if result.mode_used not in assertions.mode.expected:
                failures.append(
                    f"{prefix} mode 검증 실패 — "
                    f"expected={assertions.mode.expected}, "
                    f"actual={result.mode_used!r}"
                )

        return failures

    # ------------------------------------------------------------------
    # 테스트
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_greeting_context_flow(
        self,
        chat_client: ChatClient,
        multiturn_fixtures: MultiTurnFixtures,
    ) -> None:
        """인사 → 사실 질문 → 맥락 참조 3-turn 시나리오."""
        suite = next(
            (s for s in multiturn_fixtures.test_suites if s.id == "greeting-context-flow"),
            None,
        )
        assert suite is not None, (
            "'greeting-context-flow' 테스트 스위트가 fixture에 없습니다."
        )

        thread_id: str | None = None
        all_failures: list[str] = []

        for turn_def in suite.turns:
            thread_id, result = await self._execute_turn(
                chat_client, thread_id, turn_def.query
            )
            failures = self._assert_turn(
                turn_def.turn,
                turn_def.query,
                turn_def.assertions,
                result,
            )
            all_failures.extend(failures)

            # 디버깅용 출력 (pytest -s 또는 -v 시 표시)
            print(
                f"\n[Turn {turn_def.turn}] query={turn_def.query!r} "
                f"mode={result.mode_used} "
                f"elapsed={result.elapsed_seconds:.1f}s "
                f"content_len={len(result.full_content)}"
            )

        if all_failures:
            pytest.fail(
                f"{len(all_failures)} 개 검증 실패:\n"
                + "\n".join(f"  {i + 1}. {f}" for i, f in enumerate(all_failures))
            )
