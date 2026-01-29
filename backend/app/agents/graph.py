"""AI Agent 메인 워크플로우 그래프.

LangGraph를 사용하여 AI 에이전트 워크플로우를 정의합니다.
V8.0: 전체 워크플로우 구현 (Intent → Search/RAG/Reasoning → Generator → Reflector)
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from loguru import logger

from .state import GraphState, AIMode
from .nodes import (
    IntentParserNode,
    GeneratorNode,
    SearcherNode,
    RAGRetrieverNode,
    ReasonerNode,
    ReflectorNode,
)


def create_ai_graph(settings: Any, enable_reflection: bool = False) -> StateGraph:
    """AI 에이전트 그래프 생성.

    V8.0 워크플로우:
    - SIMPLE: Intent → Generator → (Reflector) → END
    - SEARCH: Intent → Searcher → Generator → (Reflector) → END
    - RAG: Intent → RAGRetriever → Generator → (Reflector) → END
    - REASONING: Intent → Reasoner → (Reflector) → END
    - HYBRID: Intent → Searcher → RAGRetriever → Generator → (Reflector) → END

    Args:
        settings: 애플리케이션 설정
        enable_reflection: Reflector 노드 활성화 여부

    Returns:
        컴파일된 StateGraph
    """
    # 노드 인스턴스 생성
    intent_parser = IntentParserNode(settings)
    searcher = SearcherNode(settings)
    rag_retriever = RAGRetrieverNode(settings)
    reasoner = ReasonerNode(settings)
    generator = GeneratorNode(settings)
    reflector = ReflectorNode(settings)

    # 그래프 정의
    workflow = StateGraph(GraphState)

    # 노드 추가
    workflow.add_node("intent_parser", intent_parser)
    workflow.add_node("searcher", searcher)
    workflow.add_node("rag_retriever", rag_retriever)
    workflow.add_node("reasoner", reasoner)
    workflow.add_node("generator", generator)
    if enable_reflection:
        workflow.add_node("reflector", reflector)

    # 엔트리 포인트
    workflow.set_entry_point("intent_parser")

    # 조건부 라우팅: Intent Parser → 다음 노드
    def route_by_mode(state: GraphState) -> str:
        """모드에 따른 라우팅."""
        mode = state["mode"]

        if mode == AIMode.SEARCH:
            return "searcher"
        elif mode == AIMode.RAG:
            return "rag_retriever"
        elif mode == AIMode.REASONING:
            return "reasoner"
        elif mode == AIMode.HYBRID:
            # Hybrid: 웹 검색 먼저 수행
            return "searcher"
        else:  # SIMPLE
            return "generator"

    workflow.add_conditional_edges(
        "intent_parser",
        route_by_mode,
        {
            "generator": "generator",
            "searcher": "searcher",
            "rag_retriever": "rag_retriever",
            "reasoner": "reasoner",
        }
    )

    # Searcher → Generator (또는 Hybrid인 경우 RAG로)
    def route_after_search(state: GraphState) -> str:
        """검색 후 라우팅."""
        mode = state["mode"]
        if mode == AIMode.HYBRID:
            return "rag_retriever"
        return "generator"

    workflow.add_conditional_edges(
        "searcher",
        route_after_search,
        {
            "generator": "generator",
            "rag_retriever": "rag_retriever",
        }
    )

    # RAG Retriever → Generator
    workflow.add_edge("rag_retriever", "generator")

    # Reasoner → END (Reasoner가 직접 응답 생성)
    if enable_reflection:
        workflow.add_edge("reasoner", "reflector")
    else:
        workflow.add_edge("reasoner", END)

    # Generator → Reflector 또는 END
    if enable_reflection:
        workflow.add_edge("generator", "reflector")
        workflow.add_edge("reflector", END)
    else:
        workflow.add_edge("generator", END)

    return workflow.compile()


async def run_ai_agent(
    *,
    settings: Any,
    query: str,
    conversation_id: str | None = None,
    mode: str | None = None,
    metadata: dict | None = None,
    enable_reflection: bool = False,
) -> dict:
    """AI 에이전트 실행.

    Args:
        settings: 애플리케이션 설정
        query: 사용자 쿼리
        conversation_id: 대화 ID (선택)
        mode: 강제 모드 지정 (선택, auto면 자동 감지)
        metadata: 추가 메타데이터
        enable_reflection: Reflector 노드 활성화 여부

    Returns:
        실행 결과 딕셔너리
    """
    graph = create_ai_graph(settings, enable_reflection=enable_reflection)

    # 초기 상태 구성
    initial_state: GraphState = {
        "messages": [HumanMessage(content=query)],
        "query": query,
        "mode": AIMode(mode) if mode and mode != "auto" else AIMode.SIMPLE,
        "selected_model": None,  # 동적 라우팅으로 IntentParser에서 설정
        "intent_confidence": 0.0,
        "requires_clarification": False,
        "clarification_question": None,
        "query_analysis": None,
        "search_queries": [query],  # 기본값: 원본 쿼리
        "search_results": [],
        "thinking_steps": [],
        "response": "",
        "sources": [],
        "error": None,
        "conversation_id": conversation_id,
        "metadata": metadata or {},
    }

    # 그래프 실행
    logger.info(f"[AIAgent] Running agent: query='{query[:50]}...', mode={mode}, conversation_id={conversation_id}")

    try:
        result = await graph.ainvoke(initial_state)
        logger.info(f"[AIAgent] Completed: mode={result['mode']}, response_length={len(result.get('response', ''))}")
        return result
    except Exception as e:
        logger.error(f"[AIAgent] Execution failed: {e}")
        return {
            **initial_state,
            "error": str(e),
            "response": "죄송합니다. 처리 중 오류가 발생했습니다.",
        }


async def stream_ai_agent(
    *,
    settings: Any,
    query: str,
    conversation_id: str | None = None,
    mode: str | None = None,
    metadata: dict | None = None,
    enable_reflection: bool = False,
) -> AsyncIterator[dict]:
    """AI 에이전트 스트리밍 실행 (토큰 단위 스트리밍).

    Args:
        settings: 애플리케이션 설정
        query: 사용자 쿼리
        conversation_id: 대화 ID (선택)
        mode: 강제 모드 지정 (선택)
        metadata: 추가 메타데이터
        enable_reflection: Reflector 노드 활성화 여부

    Yields:
        스트리밍 이벤트 딕셔너리
    """
    from .nodes import (
        IntentParserNode,
        SearcherNode,
        RAGRetrieverNode,
        ReasonerNode,
        GeneratorNode,
    )

    logger.info(f"[AIAgent] Streaming agent: query='{query[:50]}...', mode={mode}")

    # 초기 상태 구성
    state: GraphState = {
        "messages": [HumanMessage(content=query)],
        "query": query,
        "mode": AIMode(mode) if mode and mode != "auto" else AIMode.SIMPLE,
        "selected_model": None,  # 동적 라우팅으로 IntentParser에서 설정
        "intent_confidence": 0.0,
        "requires_clarification": False,
        "clarification_question": None,
        "query_analysis": None,
        "search_queries": [query],  # 기본값: 원본 쿼리
        "search_results": [],
        "thinking_steps": [],
        "response": "",
        "sources": [],
        "error": None,
        "conversation_id": conversation_id,
        "metadata": metadata or {},
    }

    try:
        # 1. Intent 분석
        yield {"type": "thinking", "data": {"step": "intent_analysis", "content": "질문 분석 중..."}}

        intent_parser = IntentParserNode(settings)
        intent_result = await intent_parser(state)
        state.update(intent_result)

        detected_mode = state["mode"]
        selected_tier = state.get("selected_model", "tier-simple")  # tier명이 selected_model에 저장됨
        query_analysis = state.get("query_analysis")

        yield {
            "type": "thinking",
            "data": {
                "step": "intent_result",
                "content": f"모드: {detected_mode}, 티어: {selected_tier}",
                "mode": str(detected_mode),
                "selected_tier": selected_tier,
            }
        }

        # 쿼리 재정의 결과가 있으면 전송 (Perplexity 스타일 UI용)
        if query_analysis:
            yield {
                "type": "query_analysis",
                "data": {
                    "original_query": query_analysis.get("original_query", query),
                    "reformulated_query": query_analysis.get("reformulated_query", ""),
                    "search_queries": query_analysis.get("sub_queries", []),
                    "keywords": query_analysis.get("keywords", []),
                    "search_focus": query_analysis.get("search_focus", ""),
                }
            }

        # 2. 모드별 처리
        if detected_mode == AIMode.SEARCH:
            # 웹 검색
            yield {"type": "thinking", "data": {"step": "web_search", "content": "웹 검색 중..."}}
            searcher = SearcherNode(settings)
            search_result = await searcher(state)
            state.update(search_result)

            search_results = state.get("search_results", [])
            if search_results:
                yield {
                    "type": "thinking",
                    "data": {"step": "web_search_complete", "content": f"웹 검색 완료: {len(search_results)}개 결과"}
                }
                yield {"type": "sources", "data": _convert_sources(search_results)}

        elif detected_mode == AIMode.RAG:
            # RAG 검색
            yield {"type": "thinking", "data": {"step": "rag_search", "content": "내부 문서 검색 중..."}}
            rag_retriever = RAGRetrieverNode(settings)
            rag_result = await rag_retriever(state)
            state.update(rag_result)

            search_results = state.get("search_results", [])
            if search_results:
                yield {
                    "type": "thinking",
                    "data": {"step": "rag_search_complete", "content": f"문서 검색 완료: {len(search_results)}개 결과"}
                }
                yield {"type": "sources", "data": _convert_sources(search_results)}

        elif detected_mode == AIMode.HYBRID:
            # 웹 + RAG 검색
            yield {"type": "thinking", "data": {"step": "web_search", "content": "웹 검색 중..."}}
            searcher = SearcherNode(settings)
            search_result = await searcher(state)
            state.update(search_result)

            web_results = state.get("search_results", [])
            if web_results:
                yield {
                    "type": "thinking",
                    "data": {"step": "web_search_complete", "content": f"웹 검색 완료: {len(web_results)}개 결과"}
                }

            yield {"type": "thinking", "data": {"step": "rag_search", "content": "내부 문서 검색 중..."}}
            rag_retriever = RAGRetrieverNode(settings)
            rag_result = await rag_retriever(state)
            state.update(rag_result)

            all_results = state.get("search_results", [])
            if all_results:
                yield {
                    "type": "thinking",
                    "data": {"step": "search_complete", "content": f"통합 검색 완료: {len(all_results)}개 결과"}
                }
                yield {"type": "sources", "data": _convert_sources(all_results)}

        elif detected_mode == AIMode.REASONING:
            # 추론 모드 - Reasoner가 직접 응답 생성
            yield {"type": "thinking", "data": {"step": "reasoning_start", "content": "단계별 분석 중..."}}
            reasoner = ReasonerNode(settings)

            # Reasoner 스트리밍 - content를 token으로 변환하여 점진적 표시
            async for chunk_event in reasoner.stream(state):
                if chunk_event.get("type") == "content":
                    # content 이벤트를 token으로 변환 (점진적 축적)
                    yield {"type": "token", "data": chunk_event.get("data", "")}

            yield {"type": "thinking", "data": {"step": "reasoning_complete", "content": "추론 완료"}}
            yield {"type": "done", "data": None}
            return

        # 3. Generator로 응답 생성 (토큰 스트리밍)
        yield {"type": "thinking", "data": {"step": "generation_start", "content": "답변 생성 중..."}}

        generator = GeneratorNode(settings)
        async for chunk_event in generator.stream(state):
            event_type = chunk_event.get("type", "")
            event_data = chunk_event.get("data")

            if event_type == "content":
                # 토큰 단위 스트리밍
                yield {"type": "token", "data": event_data}
            elif event_type == "sources":
                yield {"type": "sources", "data": _convert_sources(event_data)}
            elif event_type == "done":
                # 최종 응답
                pass

        yield {"type": "done", "data": None}

    except Exception as e:
        logger.error(f"[AIAgent] Stream failed: {e}")
        yield {"type": "error", "data": str(e)}


def _convert_sources(sources: list) -> list[dict]:
    """검색 결과를 dict 형태로 변환."""
    return [
        {
            "title": s.get("title", "") if isinstance(s, dict) else getattr(s, "title", ""),
            "url": s.get("url", "") if isinstance(s, dict) else getattr(s, "url", ""),
            "snippet": s.get("snippet", "") if isinstance(s, dict) else getattr(s, "snippet", ""),
            "source": s.get("source", "web") if isinstance(s, dict) else getattr(s, "source", "web"),
        }
        for s in sources
    ]
