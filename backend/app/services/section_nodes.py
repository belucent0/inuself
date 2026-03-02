"""LangGraph 섹션 생성 노드 함수.

새로운 LangGraph 기반 상세 섹션 생성 파이프라인의 노드 함수들을 정의합니다.
기존 코드와 병행하여 사용됩니다.
"""

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import Settings
from ..core.logging import logger
from ..prompts.summary import SECTION_GENERATION_TEMPLATE, SUMMARY_SYSTEM_PROMPT
from .litellm_client import request_litellm_completion_async
from .section_state import SectionGenerationState


def _emit_section_progress(state: SectionGenerationState) -> None:
    """섹션 완료 시 진행률 콜백을 호출합니다."""
    cb = state.get("progress_callback")
    if cb:
        completed = len(state.get("sections", {}))
        total = len(state.get("toc", []))
        if total > 0:
            cb(completed, total)


def extract_fallback_content(topic: str, transcript: str) -> str:
    """JSON 파싱 실패 시 원문에서 주제와 관련된 내용을 추출합니다.

    Args:
        topic: 주제
        transcript: 원본 텍스트

    Returns:
        추출된 내용 (없으면 빈 문자열)
    """
    if not transcript:
        return ""

    # 간단한 키워드 매칭으로 관련 문장 추출
    sentences = (
        transcript.replace(".", ".\n")
        .replace("!", "!\n")
        .replace("?", "?\n")
        .split("\n")
    )

    # 주제에서 키워드 추출 (예: "사건 개요와 발생 배경" → ["사건", "개요", "발생", "배경"])
    topic_keywords = [word for word in topic.split() if len(word) >= 2]

    relevant_sentences = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # 주제 키워드가 문장에 포함되어 있는지 확인
        if any(keyword in sentence for keyword in topic_keywords):
            relevant_sentences.append(sentence)
        # 너무 많이 찾으면 중단
        if len(relevant_sentences) >= 3:
            break

    if relevant_sentences:
        return " ".join(relevant_sentences)

    # 관련 문장을 찾지 못하면 앞부분 반환 (최소한의 내용 제공)
    return transcript[:500] if transcript else ""


def validate_section_length(
    content: str, min_len: int = 50, max_len: int | None = None
) -> Tuple[bool, str]:
    """섹션 내용의 길이를 검증합니다.

    Args:
        content: 검증할 내용
        min_len: 최소 길이 (기본 50)
        max_len: 최대 길이 (기본 None - 제한 없음)

    Returns:
        (is_valid, message) 튜플
        - is_valid: True/False
        - message: 검증 결과 메시지
    """
    if not content:
        return False, "내용이 비어있습니다"

    length = len(content)

    if length < min_len:
        return False, f"내용이 너무 짧습니다: {length}자 (최소 {min_len}자)"

    # 최대 길이 제한은 선택적 (None이면 제한 없음)
    if max_len is not None and length > max_len:
        return False, f"내용이 너무 깁니다: {length}자 (최대 {max_len}자)"

    return True, f"검증 통과: {length}자"


def initialize_node(state: SectionGenerationState) -> SectionGenerationState:
    """초기 상태를 설정하는 노드.

    Args:
        state: 입력 상태

    Returns:
        초기화된 상태
    """
    logger.info("[LangGraph] 섹션 생성 그래프 초기화")

    return {
        **state,
        "sections": {},
        "failed_sections": [],
        "detailed_content_md": None,
        "retry_counts": {topic: 0 for topic in state["toc"]},
        "logs": [],
        "start_time": time.time(),
    }


async def create_section_node(
    state: SectionGenerationState, settings: Settings
) -> SectionGenerationState:
    """현재 주제(current_topic)에 대한 섹션을 생성하는 노드 (비동기).

    Args:
        state: 현재 상태
        settings: 설정 객체 (LLM 모델 접근용)

    Returns:
        current_content가 업데이트된 상태
    """
    topic = state.get("current_topic")
    if not topic:
        logger.warning("[LangGraph] current_topic이 없습니다")
        return state

    logger.info(f"[LangGraph] 섹션 생성 시작: {topic}")

    try:
        # 프롬프트 준비 - 특정 주제에 대해서만 생성
        keywords_pipe = "|".join(state["keywords"])
        toc_pipe = "|".join(state["toc"])
        title = state.get("title", "")

        # 재시도 횟수 확인
        retry_count = state["retry_counts"].get(topic, 0)
        previous_content = state.get("current_content", "")

        # 기본 프롬프트 생성
        prompt = SECTION_GENERATION_TEMPLATE.format(
            topic=topic,
            toc=toc_pipe,
            keywords=keywords_pipe,
            title=title,
            transcript=state["transcript"],
        )

        # 재시도 시 피드백 추가
        if retry_count > 0:
            if not previous_content:
                feedback = '\n\n[피드백] 반드시 JSON 형식으로만 응답하세요. 예시: {"content": "내용을 여기에 작성"}'
            else:
                prev_length = len(previous_content)
                feedback = f"\n\n[피드백] 이전 답변이 {prev_length}자로 너무 짧았습니다. 내용을 더 자세히 작성하여 50자 이상으로 작성해주세요."
            prompt += feedback

        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        # LLM 호출 (비동기)
        response = await request_litellm_completion_async(
            settings=settings,
            model=settings.litellm_model_summarize,
            messages=messages,
        )

        # JSON 파싱
        content = ""
        try:
            # <think>...</think> 블록 제거 (chain-of-thought 모델 대응)
            json_str = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()

            # ```json ... ``` 블록 찾기
            json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", json_str, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 중괄호만 추출 시도
                start = json_str.find("{")
                end = json_str.rfind("}")
                if start != -1 and end != -1:
                    json_str = json_str[start : end + 1]

            data = json.loads(json_str)

            # 새로운 단일 섹션 형식: {"content": "..."}
            content = data.get("content", "").strip()

            # content가 비어있으면 detailed_sections (레거시 형식)에서 추출 시도
            if not content:
                detailed_str = data.get("detailed_sections", "")
                if detailed_str:
                    # "주제:내용|주제:내용" 형식 파싱
                    for section in detailed_str.split("|"):
                        if ":" in section:
                            parts = section.split(":", 1)
                            if len(parts) == 2:
                                section_topic, section_content = parts
                                if section_topic.strip() == topic:
                                    content = section_content.strip()
                                    break

                    # 주제를 찾지 못했다면 첫 번째 섹션 사용
                    if not content:
                        first_section = detailed_str.split("|")[0]
                        if ":" in first_section:
                            content = first_section.split(":", 1)[1].strip()

            # detailed_sections도 비어있으면 core_summary에서 추출 시도
            if not content:
                core_summary = data.get("core_summary", "")
                if core_summary:
                    content = core_summary

        except json.JSONDecodeError as e:
            logger.warning(
                f"[LangGraph] JSON 파싱 실패 (재시도 예정): {topic}, "
                f"retry={retry_count}, response[:200]={response[:200]}"
            )
            content = ""  # validate_and_route가 retry로 라우팅
        except (IndexError, AttributeError) as e:
            logger.error(f"[LangGraph] 데이터 추출 오류: {e}")
            content = ""

        logger.info(f"[LangGraph] 섹션 생성 완료: {topic} ({len(content)}자)")

        # 검증 통과 시 sections에 저장 (conditional edge에서 state 수정은 무시되므로 여기서 처리)
        is_valid, _ = validate_section_length(content)
        sections_update = {topic: content} if is_valid else {}

        return {
            "current_content": content,
            "sections": sections_update,
            "logs": [
                {
                    "timestamp": time.time(),
                    "topic": topic,
                    "action": "generate",
                    "content_length": len(content),
                }
            ],
        }

    except Exception as e:
        logger.error(f"[LangGraph] 섹션 생성 오류: {topic}, {e}")

        return {
            "current_content": "",
            "logs": [
                {
                    "timestamp": time.time(),
                    "topic": topic,
                    "action": "generate_error",
                    "error": str(e),
                }
            ],
        }


def validate_and_route(state: SectionGenerationState) -> str:
    """검증 결과에 따른 라우팅 결정을 반환합니다.

    Args:
        state: 현재 상태

    Returns:
        라우팅 결정 ("success", "retry", "fallback")
    """
    topic = state.get("current_topic")
    content = state.get("current_content", "")

    if not topic:
        return "fallback"

    retry_count = state["retry_counts"].get(topic, 0)
    max_retries = state["max_retries"]

    # 길이 검증
    is_valid, message = validate_section_length(content)

    # 로깅
    state["logs"].append(
        {
            "timestamp": time.time(),
            "topic": topic,
            "action": "validate",
            "valid": is_valid,
            "message": message,
            "retry_count": retry_count,
        }
    )

    if is_valid:
        # sections 저장은 create_section_node에서 처리 (conditional edge의 state 수정은 무시됨)
        _emit_section_progress(state)
        logger.info(f"[LangGraph] 검증 통과: {topic}")
        return "success"

    elif retry_count < max_retries:
        # 재시도 필요
        state["retry_counts"][topic] = retry_count + 1
        logger.warning(
            f"[LangGraph] 재시도 {retry_count + 1}/{max_retries}: {topic} - {message}"
        )
        return "retry"

    else:
        # 최대 재시도 초과
        logger.error(f"[LangGraph] 최대 재시도 초과: {topic}")
        return "fallback"


async def fallback_section_node(
    state: SectionGenerationState, settings: Settings
) -> SectionGenerationState:
    """실패한 주제를 유사 주제로 대체 생성하는 노드 (비동기).

    Args:
        state: 현재 상태
        settings: 설정 객체

    Returns:
        변경된 필드만 담은 delta dict
    """
    failed_topic = state.get("current_topic")
    if not failed_topic:
        return {}

    logger.info(f"[LangGraph] 대체 주제 시도: {failed_topic}")

    # 간단한 대체: 원래 주제를 약간 변형
    alternative_topic = f"{failed_topic} (보안 측면)"

    try:
        # 새로운 주제로 생성 시도
        keywords_pipe = "|".join(state["keywords"])

        prompt = f"""다음 텍스트에서 '{alternative_topic}'에 대한 상세 설명을 50~150자로 작성하세요.

키워드: {keywords_pipe}

텍스트:
{state["transcript"][:5000]}

반드시 한 문단으로 작성하세요."""

        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        response = await request_litellm_completion_async(
            settings=settings,
            model=settings.litellm_model_summarize,
            messages=messages,
        )

        content = response.strip()

        # 길이 검증
        is_valid, _ = validate_section_length(content)

        log_entry = {
            "timestamp": time.time(),
            "topic": failed_topic,
            "action": "fallback",
            "alternative_topic": alternative_topic,
            "success": is_valid,
        }

        if is_valid:
            # 진행률 콜백: 새 섹션 포함 카운트로 호출
            _emit_section_progress({**state, "sections": {**state.get("sections", {}), failed_topic: content}})
            logger.info(
                f"[LangGraph] 대체 주제 성공: {failed_topic} ({len(content)}자)"
            )
            return {
                "sections": {failed_topic: content},
                "logs": [log_entry],
            }
        else:
            # 그래도 실패하면 기본 메시지
            _emit_section_progress({**state, "sections": {**state.get("sections", {}), failed_topic: "해당 내용 생성 실패"}})
            logger.warning(f"[LangGraph] 대체 주제도 실패: {failed_topic}")
            return {
                "sections": {failed_topic: "해당 내용 생성 실패"},
                "failed_sections": [failed_topic],
                "logs": [log_entry],
            }

    except Exception as e:
        logger.error(f"[LangGraph] 대체 생성 오류: {failed_topic}, {e}")
        return {
            "sections": {failed_topic: "해당 내용 생성 실패"},
            "failed_sections": [failed_topic],
        }


def aggregate_sections_node(state: SectionGenerationState) -> SectionGenerationState:
    """모든 섹션 결과를 집계하여 마크다운을 생성하는 노드.

    Args:
        state: 현재 상태

    Returns:
        최종 마크다운이 추가된 상태
    """
    logger.info("[LangGraph] 섹션 결과 집계 시작")

    sections_md = []

    for topic in state["toc"]:
        content = state["sections"].get(topic, "해당 내용 생성 실패")
        sections_md.append(f"### {topic}\n{content}\n")

    detailed_content_md = "## 상세 내용\n\n" + "\n".join(sections_md)

    elapsed = time.time() - state["start_time"]

    logger.info(
        f"[LangGraph] 집계 완료: {len(state['sections'])}개 섹션, "
        f"{len(state['failed_sections'])}개 실패, "
        f"소요시간: {elapsed:.2f}초"
    )

    return {
        "detailed_content_md": detailed_content_md,
        "logs": [
            {
                "timestamp": time.time(),
                "action": "aggregate",
                "total_sections": len(state["sections"]),
                "failed_sections": len(state["failed_sections"]),
                "elapsed_seconds": elapsed,
            }
        ],
    }
