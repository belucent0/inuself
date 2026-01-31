"""LangGraph 섹션 생성 그래프 구성.

Send API를 활용한 동적 팬아웃과 조건부 엣지를 사용하여
상세 섹션을 병렬로 생성합니다.
기존 코드와 병행하여 사용됩니다.
"""

from typing import List
from functools import partial

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from ..core.config import Settings, get_settings
from ..core.logging import logger
from .section_state import SectionGenerationState
from .section_nodes import (
    initialize_node,
    create_section_node,
    validate_and_route,
    fallback_section_node,
    aggregate_sections_node,
)


def create_section_graph(settings: Settings = None):
    """LangGraph 섹션 생성 그래프를 생성합니다.

    Args:
        settings: 설정 객체. None이면 기본 설정 사용.

    Returns:
        컴파일된 LangGraph
    """
    if settings is None:
        settings = get_settings()

    logger.info("[LangGraph] 섹션 생성 그래프 구성 시작")

    # 그래프 빌더
    builder = StateGraph(SectionGenerationState)

    # 노드 등록 (partial로 settings 주입)
    builder.add_node("initialize", initialize_node)
    builder.add_node(
        "generate_section", partial(create_section_node, settings=settings)
    )
    builder.add_node(
        "fallback_section", partial(fallback_section_node, settings=settings)
    )
    builder.add_node("aggregate", aggregate_sections_node)

    # 엣지 연결
    builder.set_entry_point("initialize")

    # 초기화 후 Send API로 동적 팬아웃
    def fan_out_sections(state: SectionGenerationState) -> List[Send]:
        """TOC 주제별로 섹션 생성 노드를 동적으로 생성합니다."""
        sends = []

        for topic in state["toc"]:
            # 이미 생성된 주제는 스킵
            if topic in state["sections"]:
                continue

            sends.append(
                Send(
                    node="generate_section",
                    arg={
                        **state,
                        "current_topic": topic,
                        "current_content": None,
                    },
                )
            )

        if sends:
            logger.info(f"[LangGraph] {len(sends)}개 주제에 대해 동적 팬아웃")

        return sends

    # 초기화 → Send API (동적 팬아웃)
    builder.add_conditional_edges(
        "initialize",
        fan_out_sections,
        path_map={"generate_section": "generate_section"},
    )

    # 섹션 생성 → 검증 및 라우팅
    builder.add_conditional_edges(
        "generate_section",
        validate_and_route,
        path_map={
            "success": "aggregate",  # 성공 시 집계
            "retry": "generate_section",  # 재시도 (같은 노드로)
            "fallback": "fallback_section",  # 대체 시도
        },
    )

    # 대체 생성 → 집계
    builder.add_edge("fallback_section", "aggregate")

    # 집계 → 종료
    builder.add_edge("aggregate", END)

    # 그래프 컴파일
    graph = builder.compile()

    logger.info("[LangGraph] 섹션 생성 그래프 구성 완료")

    return graph


# 전역 그래프 인스턴스 (싱글톤)
_graph_instance = None


def get_section_graph(settings: Settings = None):
    """섹션 생성 그래프의 싱글톤 인스턴스를 반환합니다."""
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = create_section_graph(settings)
    return _graph_instance


def reset_section_graph():
    """그래프 인스턴스를 리셋합니다 (테스트용)."""
    global _graph_instance
    _graph_instance = None
    logger.info("[LangGraph] 섹션 생성 그래프 리셋")
