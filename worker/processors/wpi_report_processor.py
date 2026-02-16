"""WPI AI 리포트 처리 프로세서."""

import asyncio
import json
import re
from functools import partial
from typing import Any
from uuid import uuid4

from worker.config import get_settings
from worker.logging_config import logger
from worker.utils.event_loop import cleanup_worker_event_loop, setup_worker_event_loop
from worker.utils.result_publisher import (
    publish_wpi_report_completed,
    publish_wpi_report_failed,
    publish_wpi_report_started,
)
from worker.utils.storage import upload_json

settings = get_settings()


def _wpi_model_name() -> str:
    return settings.wpi_report_litellm_model or settings.litellm_model_summarize


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rank_top_types(scores: dict[str, Any], top_k: int = 3) -> list[tuple[str, float]]:
    ranked = [
        (str(type_name), _safe_float(score_value))
        for type_name, score_value in scores.items()
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:top_k]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _build_rule_based_wpi_report(
    context: dict[str, Any], *, llm_error: str | None = None
) -> str:
    i_test = _as_dict(context.get("i_test"))
    me_test = _as_dict(context.get("me_test"))
    routing = _as_dict(context.get("routing"))
    pair_gap_profile = _as_dict(context.get("pair_gap_profile"))
    pair_gap_block = _as_dict(context.get("pair_gap_block"))
    auto_profile = _as_dict(context.get("auto_profile"))
    secondary_profiles = _as_list(context.get("secondary_profiles"))
    selected_block_ids = _as_list(context.get("selected_block_ids"))

    i_dom = str(i_test.get("dominant_type") or "Unknown")
    me_dom = str(me_test.get("dominant_type") or "Unknown")
    i_scores_raw = _as_dict(i_test.get("scores"))
    me_scores_raw = _as_dict(me_test.get("scores"))

    i_top_types = _rank_top_types(i_scores_raw)
    me_top_types = _rank_top_types(me_scores_raw)

    pair_gap = _safe_float(pair_gap_profile.get("gap"))
    pair_gap_ratio = _safe_float(pair_gap_profile.get("gap_ratio"))
    pair_gap_bucket = str(
        pair_gap_profile.get("bucket") or routing.get("pair_gap_bucket") or "balanced"
    )
    pair_gap_direction = str(pair_gap_profile.get("direction") or "balanced")

    routing_key = str(routing.get("key") or f"rk_v1:{i_dom}:{me_dom}:{pair_gap_bucket}")
    routing_rule_id = str(
        routing.get("rule_id")
        or f"R1-{i_dom[:3].lower()}-{me_dom[:3].lower()}-{pair_gap_bucket}"
    )

    summary_focus = str(
        pair_gap_block.get("summary_focus")
        or "자기 인식과 사회적 표현의 간격을 안정적으로 조율할 필요가 있습니다"
    )
    caution_focus = str(
        pair_gap_block.get("caution_focus")
        or "해석을 단정하기보다 현재 맥락에서 반복되는 패턴을 확인하는 것이 중요합니다"
    )
    action_focus = [
        str(item)
        for item in pair_gap_block.get("action_focus", [])
        if isinstance(item, str) and item.strip()
    ]
    if len(action_focus) < 3:
        action_focus.extend(
            [
                "하루 1회 핵심 우선순위를 문장으로 정리합니다",
                "중요 요청에 대해 범위와 기한을 명시적으로 확인합니다",
                "하루 마감 시 자기 인식과 실제 행동의 차이를 3줄로 기록합니다",
            ]
        )
    action_focus = action_focus[:3]

    strengths_text = str(
        auto_profile.get("strengths") or "상황 판단력, 실행력, 관계 조율력"
    )
    weaknesses_text = str(
        auto_profile.get("weaknesses")
        or "과도한 자기비판, 설명 누락, 경계 설정의 흔들림"
    )

    i_top_text = ", ".join(f"{name}({score:.1f})" for name, score in i_top_types)
    me_top_text = ", ".join(f"{name}({score:.1f})" for name, score in me_top_types)
    selected_blocks_text = ", ".join(str(block_id) for block_id in selected_block_ids)

    secondary_lines: list[str] = []
    for item in secondary_profiles[:2]:
        if not isinstance(item, dict):
            continue
        secondary_i = item.get("i_type")
        secondary_me = item.get("me_type")
        if secondary_i and secondary_me:
            secondary_lines.append(f"{secondary_i}-{secondary_me}")
    secondary_text = (
        ", ".join(secondary_lines) if secondary_lines else "보조 조합 데이터 없음"
    )

    direction_label = {
        "i_test_dominant": "I 우세",
        "me_test_dominant": "Me 우세",
        "balanced": "균형",
    }.get(pair_gap_direction, pair_gap_direction)

    sections: list[str] = [
        "## 1. 종합 해석",
        (
            f"이번 리포트는 `{routing_key}`(`{routing_rule_id}`) 라우팅을 기준으로 구성되었습니다. "
            f"I 우세형은 **{i_dom}**, Me 우세형은 **{me_dom}**이며, 상위 점수 분포는 "
            f"I-Test 기준 `{i_top_text}`, Me-Test 기준 `{me_top_text}`입니다. "
            f"대응 페어 gap은 **{pair_gap:.1f}**이고 gap_ratio는 **{pair_gap_ratio:.2f}**로, "
            f"현재 버킷은 **{pair_gap_bucket}**({direction_label})입니다."
        ),
        (
            f"해석 초점은 `{summary_focus}`입니다. 이번 결과는 우세형 자체보다 "
            f"`{caution_focus}`를 어떻게 실무/관계 맥락에서 조율하느냐가 핵심이며, "
            f"근거 블록은 `{selected_blocks_text}`를 우선 사용했습니다."
        ),
        "",
        "## 2. 핵심 강점",
        f"- 우세형 `{i_dom}` 점수가 가장 높아 자기 기준 설정과 문제 구조화에서 강점을 보입니다 (I top: {i_top_text}).",
        f"- 사회적 표현축에서는 `{me_dom}`이 우세하여 협업 상황에서 기대 조율과 관계 맥락 파악이 상대적으로 빠릅니다 (Me top: {me_top_text}).",
        f"- 프로파일 강점 근거는 `{strengths_text}`이며, 현재 routing `{routing_rule_id}`에서 실행력과 조율력을 함께 살릴 수 있습니다.",
        f"- pair_gap bucket `{pair_gap_bucket}`에서 {summary_focus}에 해당하므로, 상황 적응과 자기 기준 정렬을 동시에 유지할 여지가 큽니다.",
        "",
        "## 3. 주의 포인트",
        f"- 대응 페어 gap {pair_gap:.1f}(ratio {pair_gap_ratio:.2f})가 반복될 때, 의도와 전달 방식의 간격이 누적될 수 있습니다.",
        f"- 주요 주의 근거는 `{caution_focus}`이며, 조율 과정에서 자기 기준이 희석되지 않도록 점검이 필요합니다.",
        f"- 취약 패턴 단서는 `{weaknesses_text}`로 요약되며, 특히 일정 압박 상황에서 설명 생략/과잉수용 형태로 나타날 수 있습니다.",
        f"- selected_block_ids `{selected_blocks_text}` 기반으로 보면, 현재는 강점 확장보다 경계 조건 명시가 선행되어야 충돌 비용을 줄일 수 있습니다.",
        "",
        "## 4. 상황별 제안",
        (
            f"아래 제안은 pair_gap 축 `{pair_gap_bucket}`과 현재 점수 분포(I: {i_top_text} / Me: {me_top_text})를 기준으로 구성했습니다. "
            "각 행동은 1주 단위로 적용한 뒤, 점수-축-프로파일 근거와 실제 체감의 일치 여부를 함께 기록해 조정하는 방식이 좋습니다."
        ),
        "### 개인 실행",
        f"- {action_focus[0]} (근거: pair_gap {pair_gap:.1f}, gap_ratio {pair_gap_ratio:.2f}, bucket {pair_gap_bucket})",
        f"- {action_focus[1]} (근거: I 우세형 {i_dom} 점수와 Me 우세형 {me_dom} 점수의 축 정렬 상태)",
        f"- {action_focus[2]} (근거: selected_block_ids 기반 프로파일 해석을 일상 행동으로 전환)",
        "### 협업/소통",
        "- 중요한 의사결정 전, 배경과 의도를 2문장으로 먼저 공유하고 pair_gap 축 해석(왜 그렇게 판단했는지)을 함께 전달합니다.",
        "- 요청 수락/거절 시 범위·기한·우선순위를 명확히 말하고, 현재 점수 근거(강점/주의 포인트)를 짧게 덧붙여 기대 불일치를 줄입니다.",
        "- 회고 시 사실-해석-다음 행동 순서로 정리하면서, 프로파일 근거 문장과 실제 반응 데이터를 1:1로 대응시켜 재사용합니다.",
        "",
        "## 5. 비우세형(보조 프로파일) 해석",
        (
            f"보조 조합 후보는 `{secondary_text}`입니다. 우세형만 보면 실행 방향이 명확하지만, "
            f"보조 조합을 함께 보면 특정 상황에서 신중함/관계 고려/절차 지향이 교차적으로 나타날 수 있습니다. "
            f"따라서 실제 행동 해석은 단일 유형 고정이 아니라 상황별 발현 패턴으로 읽는 것이 적합합니다. "
            f"특히 보조 프로파일은 우세형 점수만으로 설명되지 않는 선택 맥락을 보완해 주므로, 충돌 장면/협업 장면처럼 축 긴장이 커지는 구간에서 더 유용합니다."
        ),
        (
            "이번 결과는 1회 응답 기반이므로, 반복 측정에서 동일 패턴이 재현되는지 확인하면 개인화 정확도를 더 높일 수 있습니다. "
            "다음 측정에서는 동일한 점수·축·프로파일 용어를 유지해 비교하면 변화 원인을 더 명확히 해석할 수 있습니다."
        ),
        "",
        "## 6. 코멘트",
        "이 결과는 임상적 진단이 아니라 현재 시점의 자기 인식/사회적 표현 패턴을 정리한 참고 자료이며, 점수와 축 근거를 함께 읽는 것이 중요합니다.",
        "점수와 gap은 맥락(역할, 관계, 업무 강도)에 따라 달라질 수 있으므로 단정 대신 변화 추세를 관찰해 주세요. 특히 pair_gap 축 변화는 협업 품질 변화와 함께 보시길 권장합니다.",
        "실행 제안은 작은 행동 단위로 1~2주 적용한 뒤, 유지/수정/중단 기준을 스스로 점검하는 방식이 가장 효과적이며, 프로파일 문장과 실제 행동 로그를 함께 남기면 개선 속도가 빨라집니다.",
    ]

    return "\n".join(sections).strip()


def _extract_json_object(
    content: str, *, start_index: int = 0
) -> dict[str, Any] | None:
    brace_start = content.find("{", start_index)
    if brace_start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(brace_start, len(content)):
        char = content[index]

        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == "{":
            depth += 1
            continue

        if char == "}":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                raw_json = content[brace_start : index + 1]
                try:
                    parsed = json.loads(raw_json)
                except json.JSONDecodeError:
                    return None
                if isinstance(parsed, dict):
                    return parsed
                return None

    return None


def _extract_context_from_messages(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """백엔드 user prompt에서 context_json을 추출한다."""

    for message in messages:
        if message.get("role") != "user":
            continue

        content = str(message.get("content") or "")
        if not content:
            continue

        section_start = content.find("## 입력 데이터")
        if section_start >= 0:
            parsed_from_section = _extract_json_object(
                content,
                start_index=section_start,
            )
            if parsed_from_section is not None:
                return parsed_from_section

        block_match = re.search(
            r"## 입력 데이터\s*(.*?)\s*## 작성 형식",
            content,
            re.DOTALL,
        )
        if block_match:
            raw_json = block_match.group(1).strip()
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        parsed_from_any = _extract_json_object(content)
        if parsed_from_any is not None:
            return parsed_from_any

    return None


def process_wpi_report_job(
    *,
    scan_result_id: str,
    messages: list[dict[str, Any]],
) -> None:
    """WPI AI 리포트 생성 작업 진입점."""

    logger.info("[WPI Report] Job started: scan_result_id=%s", scan_result_id)
    loop = setup_worker_event_loop()

    try:
        loop.run_until_complete(
            _process_job_async(scan_result_id=scan_result_id, messages=messages)
        )
        logger.info("[WPI Report] Job completed: scan_result_id=%s", scan_result_id)
    except Exception as exc:
        logger.error(
            "[WPI Report] Job failed: scan_result_id=%s, error=%s",
            scan_result_id,
            exc,
        )
        raise
    finally:
        cleanup_worker_event_loop(loop)


async def _process_job_async(
    *,
    scan_result_id: str,
    messages: list[dict[str, Any]],
) -> None:
    if not messages:
        raise ValueError("WPI report messages are empty")

    from worker.processors.wpi_report_graph_executor import WpiReportGraphExecutor
    from worker.pipelines.llm.litellm_client import request_litellm_completion

    try:
        publish_wpi_report_started(scan_result_id)

        context = _extract_context_from_messages(messages)
        generation_mode = "single_prompt"
        response = ""

        if context:
            try:
                graph_executor = WpiReportGraphExecutor(settings=settings)
                response = await asyncio.wait_for(
                    graph_executor.execute(context),
                    timeout=settings.wpi_report_graph_timeout_seconds,
                )
                generation_mode = "langgraph"
                logger.info(
                    "[WPI Report] LangGraph generation completed: scan_result_id=%s",
                    scan_result_id,
                )
            except Exception as graph_exc:
                logger.warning(
                    "[WPI Report] LangGraph generation failed, fallback to single prompt: "
                    "scan_result_id=%s, error=%s",
                    scan_result_id,
                    graph_exc,
                )

        if not response:
            try:
                llm_call = partial(
                    request_litellm_completion,
                    settings=settings,
                    messages=messages,
                    model=_wpi_model_name(),
                    request_timeout_seconds=settings.wpi_report_llm_request_timeout_seconds,
                    max_retry_time=settings.wpi_report_llm_busy_max_seconds,
                    retry_interval=settings.wpi_report_llm_retry_interval_seconds,
                )
                response = await asyncio.wait_for(
                    asyncio.to_thread(llm_call),
                    timeout=settings.wpi_report_single_prompt_timeout_seconds,
                )
                logger.info(
                    "[WPI Report] Single prompt generation completed: scan_result_id=%s",
                    scan_result_id,
                )
            except Exception as llm_exc:
                if context:
                    response = _build_rule_based_wpi_report(
                        context,
                        llm_error=str(llm_exc),
                    )
                    generation_mode = "rule_based_fallback"
                    logger.warning(
                        "[WPI Report] LLM generation failed, fallback report used: "
                        "scan_result_id=%s, error=%s",
                        scan_result_id,
                        llm_exc,
                    )
                else:
                    raise
    except Exception as exc:
        publish_wpi_report_failed(scan_result_id, error=str(exc))
        raise

    result_data = {
        "scan_result_id": scan_result_id,
        "raw_response": response,
        "generation_mode": generation_mode,
    }
    result_s3_key = f"results/wpi_report/{scan_result_id}/{uuid4().hex}.json"
    upload_json(result_data, key=result_s3_key)

    publish_wpi_report_completed(scan_result_id, result_s3_key=result_s3_key)
