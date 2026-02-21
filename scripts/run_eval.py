#!/usr/bin/env python3
"""Langfuse Dataset 기반 채팅 품질 Eval 실행 스크립트.

골든 데이터셋의 각 케이스를 실제 서버에서 실행하고
결과를 Langfuse에 기록하여 배포 간 품질 변화를 추적합니다.

Langfuse UI에서 확인:
  - Datasets > chat-quality-golden-set > Runs 탭
  - 각 Run의 점수 및 이전 Run과 비교 가능

사용법:
    E2E_BASE_URL=http://your-server:8000 \\
    E2E_LOGIN_ID=testuser \\
    E2E_PASSWORD=Test1234! \\
    LANGFUSE_HOST=https://... \\
    LANGFUSE_PUBLIC_KEY=pk-... \\
    LANGFUSE_SECRET_KEY=sk-... \\
        python scripts/run_eval.py

    # 실행 이름 지정 (기본: eval-YYYY-MM-DD-HHMM)
    RUN_NAME=after-prompt-refactor python scripts/run_eval.py

    # LLM-as-Judge 비활성화 (LiteLLM 프록시 없는 환경)
    JUDGE_ENABLED=false python scripts/run_eval.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from langfuse import Langfuse

# E2E 클라이언트 재사용
sys.path.insert(0, str(Path(__file__).parent.parent))
from tests.e2e.chat_client import ChatClient

DATASET_NAME = "chat-quality-golden-set"
FIXTURES_PATH = Path(__file__).parent.parent / "tests" / "e2e" / "fixtures" / "chat_multiturn_cases.json"
E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
E2E_LOGIN_ID = os.environ.get("E2E_LOGIN_ID", "")
E2E_PASSWORD = os.environ.get("E2E_PASSWORD", "")
RUN_NAME = os.environ.get(
    "RUN_NAME",
    f"eval-{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M')}",
)

# LLM-as-Judge 설정 (정보용 — CI 게이트에 영향 없음)
JUDGE_LITELLM_BASE_URL = os.environ.get("JUDGE_LITELLM_BASE_URL", "http://localhost:4000")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "tier-thinking-local")
JUDGE_ENABLED = os.environ.get("JUDGE_ENABLED", "true").lower() == "true"
JUDGE_LITELLM_API_KEY = os.environ.get("JUDGE_LITELLM_API_KEY", os.environ.get("LITELLM_API_KEY", ""))


def _score_result(
    full_content: str,
    mode_used: str,
    elapsed: float,
    expected: dict,
) -> tuple[float, list[str]]:
    """응답을 채점하고 (0~1 점수, 실패 이유 목록)을 반환합니다."""
    failures: list[str] = []
    content_lower = full_content.lower()

    # 응답 없음
    if not full_content.strip():
        return 0.0, ["응답 내용이 비어 있습니다"]

    # 응답 시간
    max_sec = expected.get("max_response_time_seconds", 60)
    if elapsed > max_sec:
        failures.append(f"응답 시간 초과 ({elapsed:.1f}s > {max_sec}s)")

    # 필수 키워드 (OR)
    must_contain = expected.get("content_contains_any", [])
    if must_contain and not any(kw.lower() in content_lower for kw in must_contain):
        failures.append(f"content_contains_any 실패: {must_contain}")

    # 금지 키워드
    for kw in expected.get("content_not_contains", []):
        if kw.lower() in content_lower:
            failures.append(f"금지 키워드 포함: '{kw}'")

    # 맥락 참조
    ctx = expected.get("context_check")
    if ctx:
        refs = ctx.get("must_reference_any", [])
        if refs and not any(r.lower() in content_lower for r in refs):
            failures.append(f"맥락 참조 누락: {refs}")

    # 모드
    allowed_modes = expected.get("mode", [])
    if allowed_modes and mode_used not in allowed_modes:
        failures.append(f"모드 불일치: expected={allowed_modes}, actual={mode_used}")

    score = 1.0 if not failures else 0.0
    return score, failures


async def _llm_judge(
    query: str,
    response: str,
    conversation_history: list[dict],
) -> tuple[float, str]:
    """LLM 심사위원이 응답 품질을 채점합니다.

    Returns:
        (score, reason) — score는 0~1 float, 오류 시 -1.0 반환
    """
    history_text = ""
    if conversation_history:
        lines = [
            f"사용자: {h['query']}\nAI: {h['response'][:200]}"
            for h in conversation_history
        ]
        history_text = "이전 대화:\n" + "\n".join(lines) + "\n\n"

    prompt = (
        f"{history_text}"
        f"현재 질문: {query}\n\n"
        f"AI 응답:\n{response[:800]}\n\n"
        "위 AI 응답을 다음 기준으로 평가하세요:\n"
        "1. 질문에 대한 답변 적절성\n"
        "2. 내용의 정확성과 유용성\n"
        "3. 이전 대화 맥락 활용 (해당 시)\n\n"
        "반드시 아래 형식으로만 답하세요:\n"
        "SCORE: 0.0~1.0\n"
        "REASON: 한 문장 평가"
    )

    try:
        headers = {"Authorization": f"Bearer {JUDGE_LITELLM_API_KEY}"} if JUDGE_LITELLM_API_KEY else {}
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{JUDGE_LITELLM_BASE_URL}/chat/completions",
                headers=headers,
                json={
                    "model": JUDGE_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 100,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

        score = -1.0
        reason = content.strip()
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("SCORE:"):
                try:
                    score = float(line.split(":", 1)[1].strip())
                    score = max(0.0, min(1.0, score))
                except ValueError:
                    pass
            elif line.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
        return score, reason

    except Exception as exc:
        return -1.0, f"judge 오류: {exc}"


async def run_suite(
    langfuse: Langfuse,
    client: ChatClient,
    items: list,
    run_name: str,
    fixture_suite_order: list[str] | None = None,
    fixture_assertions: dict | None = None,
) -> dict:
    """한 데이터셋의 모든 케이스를 순서대로 실행합니다."""
    results = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}

    # suite_id 별로 그룹화 (멀티턴 순서 유지)
    suites: dict[str, list] = {}
    for item in items:
        sid = item.input.get("suite_id", "default")
        suites.setdefault(sid, [])
        suites[sid].append(item)

    # fixture JSON 순서대로 suite 실행 (Langfuse는 최신순 반환이라 역순 방지)
    ordered_suite_ids = fixture_suite_order if fixture_suite_order else list(suites.keys())

    for suite_id in ordered_suite_ids:
        if suite_id not in suites:
            continue
        suite_items = suites[suite_id]
        suite_items.sort(key=lambda x: x.input.get("turn", 1))
        print(f"\n[Suite] {suite_id} ({len(suite_items)}턴)")

        thread_id: str | None = None
        conversation_history: list[dict] = []  # judge용 대화 맥락

        for item in suite_items:
            turn = item.input.get("turn", "?")
            query = item.input.get("query", "")
            print(f"  Turn {turn}: '{query}'", end=" ", flush=True)

            # Langfuse trace 생성 (eval 실행 기록용)
            trace = langfuse.trace(
                name="eval-chat",
                input=item.input,
                tags=["eval", run_name],
                metadata={"run_name": run_name, "suite_id": suite_id},
            )

            try:
                start = time.monotonic()

                if thread_id is None:
                    thread_id, message_id = await client.create_thread(query)
                else:
                    thread_id, message_id = await client.add_message(thread_id, query)

                result = await client.stream_response(thread_id, message_id)
                elapsed = time.monotonic() - start

                # fixture가 source of truth — Langfuse item.expected_output은 구버전일 수 있음
                case_id = item.metadata.get("case_id") if item.metadata else None
                expected = (
                    (fixture_assertions or {}).get(case_id)
                    or item.expected_output
                    or {}
                )
                score, failures = _score_result(
                    result.full_content,
                    result.mode_used,
                    elapsed,
                    expected,
                )

                # Langfuse trace 업데이트
                trace.update(
                    output={
                        "response": result.full_content[:500],
                        "mode": result.mode_used,
                        "elapsed_seconds": round(elapsed, 2),
                    },
                    level="DEFAULT" if score == 1.0 else "WARNING",
                )

                # 점수 기록 (CI 게이트 기준)
                langfuse.score(
                    trace_id=trace.id,
                    name="quality_pass",
                    value=score,
                    comment="; ".join(failures) if failures else "통과",
                )

                # LLM-as-Judge (정보용 — CI 게이트 무관)
                judge_tag = ""
                if JUDGE_ENABLED:
                    j_score, j_reason = await _llm_judge(
                        query, result.full_content, conversation_history
                    )
                    if j_score >= 0:
                        langfuse.score(
                            trace_id=trace.id,
                            name="quality_judge",
                            value=j_score,
                            comment=j_reason,
                        )
                        judge_tag = f" [judge={j_score:.2f}]"
                    else:
                        judge_tag = f" [judge=ERR: {j_reason[:60]}]"

                # 이번 턴 대화 맥락 저장 (다음 턴 judge용)
                conversation_history.append({"query": query, "response": result.full_content})

                # 데이터셋 항목에 연결 (trace 객체 직접 전달)
                item.link(trace, run_name=run_name)

                status = "PASS" if score == 1.0 else "FAIL"
                print(f"→ {status} (mode={result.mode_used}, {elapsed:.1f}s){judge_tag}")
                if failures:
                    for f in failures:
                        print(f"      ✗ {f}")

                results["total"] += 1
                if score == 1.0:
                    results["passed"] += 1
                else:
                    results["failed"] += 1

            except Exception as e:
                trace.update(output={"error": str(e)}, level="ERROR")
                langfuse.score(trace_id=trace.id, name="quality_pass", value=0.0, comment=str(e))
                item.link(trace, run_name=run_name)
                print(f"→ ERROR: {e}")
                results["total"] += 1
                results["failed"] += 1

    langfuse.flush()
    return results


async def main() -> int:
    if not E2E_LOGIN_ID or not E2E_PASSWORD:
        print("ERROR: E2E_LOGIN_ID / E2E_PASSWORD 환경변수를 설정하세요.")
        return 1

    langfuse = Langfuse(
        public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
        host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )

    try:
        dataset = langfuse.get_dataset(DATASET_NAME)
    except Exception:
        print(f"ERROR: 데이터셋 '{DATASET_NAME}'을 찾을 수 없습니다.")
        print("먼저 실행하세요: python scripts/setup_eval_dataset.py")
        return 1

    # fixture 기준 유효한 case_id만 실행 (Langfuse에 잔존하는 구 항목 제외)
    import json as _json
    fixtures = _json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    valid_case_ids = {
        f"{suite['id']}__turn{turn['turn']}"
        for suite in fixtures["test_suites"]
        for turn in suite["turns"]
    }
    # case_id 기준 중복 제거 (최신 항목 우선 — Langfuse는 최신순 정렬)
    seen_case_ids: set[str] = set()
    active_items = []
    for item in dataset.items:
        case_id = item.metadata.get("case_id") if item.metadata else None
        if case_id in valid_case_ids and case_id not in seen_case_ids:
            seen_case_ids.add(case_id)
            active_items.append(item)

    print(f"Eval 시작: run_name='{RUN_NAME}'")
    print(f"대상: {E2E_BASE_URL} | 케이스: {len(active_items)}개 (전체 {len(dataset.items)}개 중 fixture 기준 필터)")
    print("-" * 60)

    client = ChatClient(base_url=E2E_BASE_URL)
    await client.login(E2E_LOGIN_ID, E2E_PASSWORD)

    try:
        fixture_suite_order = [s["id"] for s in fixtures["test_suites"]]
        # fixture를 assertions source of truth로 사용 (Langfuse expected_output 구버전 방지)
        fixture_assertions: dict = {}
        for suite in fixtures["test_suites"]:
            for turn in suite["turns"]:
                cid = f"{suite['id']}__turn{turn['turn']}"
                a = turn["assertions"]
                fixture_assertions[cid] = {
                    "mode": a.get("mode", {}).get("expected", []),
                    "content_contains_any": a.get("content_contains_any", []),
                    "content_not_contains": a.get("content_not_contains", []),
                    "context_check": a.get("context_check"),
                    "max_response_time_seconds": a.get("max_response_time_seconds", 60),
                }
        results = await run_suite(langfuse, client, active_items, RUN_NAME, fixture_suite_order, fixture_assertions)
    finally:
        await client.cleanup_all()
        await client.close()

    print("\n" + "=" * 60)
    print(f"결과: {results['passed']}/{results['total']} 통과")
    print(f"Langfuse: {os.environ.get('LANGFUSE_HOST', 'https://cloud.langfuse.com')}")
    print(f"  Datasets > {DATASET_NAME} > Runs > {RUN_NAME}")

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
