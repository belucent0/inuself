"""LangGraph 섹션 생성 State 정의.

새로운 LangGraph 기반 상세 섹션 생성 파이프라인의 상태 타입을 정의합니다.
기존 코드와 병행하여 사용됩니다.

참고: 병렬 노드 동시성 문제 해결을 위해 Annotated 타입 사용
https://langchain-ai.github.io/langgraph/how-tos/map-reduce/
"""

from typing import TypedDict, Dict, List, Optional, Any, Annotated, Callable


# 병렬 업데이트용 reducer 함수들
def _replace(existing: Any, new: Any) -> Any:
    """값을 완전히 대체하는 reducer."""
    return new


def _merge_dicts(existing: Dict[str, str], new: Dict[str, str]) -> Dict[str, str]:
    """딕셔너리를 병합하는 reducer (병렬 섹션 생성 결과 누적용)."""
    result = dict(existing) if existing else {}
    if new:
        result.update(new)
    return result


def _append_unique(existing: List[str], new: List[str]) -> List[str]:
    """리스트에 고유하게 추가하는 reducer."""
    if not existing:
        existing = []
    result = list(existing)
    for item in new:
        if item not in result:
            result.append(item)
    return result


class SectionGenerationState(TypedDict):
    """LangGraph 섹션 생성 그래프의 상태 정의.

    Attributes:
        toc: 목차 주제 리스트 (읽기 전용, 병렬 업데이트용 Annotated)
        transcript: 원본 텍스트 (읽기 전용)
        keywords: 키워드 리스트 (읽기 전용)
        title: 콘텐츠 제목 (읽기 전용)
        sections: 생성된 섹션 {주제: 내용} (병렬 업데이트)
        failed_sections: 실패한 주제 목록 (병렬 업데이트)
        retry_counts: 주제별 재시도 횟수 (병렬 업데이트)
        max_retries: 최대 재시도 횟수
        current_topic: 현재 처리 중인 주제 (Send API용)
        current_content: 현재 생성된 내용
        logs: 실행 로그 (병렬 업데이트)
        start_time: 시작 시간 (Unix timestamp)
        detailed_content_md: 최종 마크다운 결과
    """

    # 입력 필드 (병렬 읽기 전용)
    toc: Annotated[List[str], _replace]
    transcript: Annotated[str, _replace]
    keywords: Annotated[List[str], _replace]
    title: Annotated[str, _replace]

    # 출력/누적 필드 (병렬 업데이트 지원)
    sections: Annotated[Dict[str, str], _merge_dicts]
    failed_sections: Annotated[List[str], _append_unique]
    detailed_content_md: Annotated[Optional[str], _replace]

    # 재시도 추적 (병렬 업데이트 지원)
    retry_counts: Annotated[Dict[str, int], _replace]
    max_retries: Annotated[int, _replace]

    # 현재 처리 중인 컨텍스트 (Send API용)
    current_topic: Annotated[Optional[str], _replace]
    current_content: Annotated[Optional[str], _replace]

    # 메타/로깅 (병렬 업데이트 지원)
    logs: Annotated[List[Dict[str, Any]], _append_unique]
    start_time: Annotated[Optional[float], _replace]

    # 진행률 콜백 (completed_sections, total_sections) → SSE 발행
    progress_callback: Annotated[Optional[Callable[[int, int], None]], _replace]


def create_initial_state(
    toc: List[str],
    transcript: str,
    keywords: List[str],
    title: str,
    max_retries: int = 3,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> SectionGenerationState:
    """초기 상태를 생성합니다.

    Args:
        toc: 목차 주제 리스트
        transcript: 원본 텍스트
        keywords: 키워드 리스트
        title: 콘텐츠 제목
        max_retries: 최대 재시도 횟수 (기본 3)

    Returns:
        초기화된 SectionGenerationState
    """
    import time

    return {
        "toc": toc,
        "transcript": transcript,
        "keywords": keywords,
        "title": title,
        "sections": {},
        "failed_sections": [],
        "detailed_content_md": None,
        "retry_counts": {topic: 0 for topic in toc},
        "max_retries": max_retries,
        "current_topic": None,
        "current_content": None,
        "logs": [],
        "start_time": time.time(),
        "progress_callback": progress_callback,
    }
