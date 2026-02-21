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
from ..core.langfuse import get_langfuse_handler
from ..core.llm_tier import TIER_DISPLAY_MAP, LLMTier
from .nodes import (
    IntentParserNode,
    GeneratorNode,
    SearcherNode,
    RAGRetrieverNode,
    ReasonerNode,
    ReflectorNode,
    SearchEvaluatorNode,
    QueryRewriterNode,
    FallbackHandlerNode,
)


# 상태 표기용 모드/티어 한글 표시명 매핑
MODE_DISPLAY_MAP = {
    AIMode.SEARCH: "웹 검색",
    AIMode.REASONING: "추론",
    AIMode.RAG: "문서 검색",
    AIMode.HYBRID: "하이브리드",
    AIMode.SIMPLE: "일반",
}


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
        },
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
        },
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


def create_ai_graph_with_retry(
    settings: Any,
    enable_reflection: bool = False,
    max_retries: int = 3,
) -> StateGraph:
    """검색 재시도 기능이 있는 AI 에이전트 그래프 생성.

    V8.4 워크플로우 (SEARCH/HYBRID 모드):
    - Intent → Searcher → Evaluator
      → (충분) → Generator
      → (부족) → QueryRewriter → Searcher (루프)
      → (한계) → FallbackHandler → Generator

    Args:
        settings: 애플리케이션 설정
        enable_reflection: Reflector 노드 활성화 여부
        max_retries: 최대 재시도 횟수

    Returns:
        컴파일된 StateGraph
    """
    # 노드 인스턴스 생성
    intent_parser = IntentParserNode(settings)
    searcher = SearcherNode(settings)
    search_evaluator = SearchEvaluatorNode(settings)
    query_rewriter = QueryRewriterNode(settings)
    fallback_handler = FallbackHandlerNode(settings)
    rag_retriever = RAGRetrieverNode(settings)
    reasoner = ReasonerNode(settings)
    generator = GeneratorNode(settings)
    reflector = ReflectorNode(settings)

    # 그래프 정의
    workflow = StateGraph(GraphState)

    # 노드 추가
    workflow.add_node("intent_parser", intent_parser)
    workflow.add_node("searcher", searcher)
    workflow.add_node("search_evaluator", search_evaluator)
    workflow.add_node("query_rewriter", query_rewriter)
    workflow.add_node("fallback_handler", fallback_handler)
    workflow.add_node("rag_retriever", rag_retriever)
    workflow.add_node("reasoner", reasoner)
    workflow.add_node("generator", generator)
    if enable_reflection:
        workflow.add_node("reflector", reflector)

    # 엔트리 포인트
    workflow.set_entry_point("intent_parser")

    # IntentParser → 다음 노드
    def route_by_mode(state: GraphState) -> str:
        """모드에 따른 라우팅."""
        mode = state["mode"]

        if mode == AIMode.SEARCH or mode == AIMode.HYBRID:
            return "searcher"
        elif mode == AIMode.RAG:
            return "rag_retriever"
        elif mode == AIMode.REASONING:
            return "reasoner"
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
        },
    )

    # Searcher → SearchEvaluator (항상)
    workflow.add_edge("searcher", "search_evaluator")

    # SearchEvaluator → 조건부 라우팅
    def route_after_evaluation(state: GraphState) -> str:
        """평가 후 라우팅."""
        needs_retry = state.get("needs_retry", False)
        retry_count = state.get("search_retry_count", 0)
        mode = state["mode"]

        # 충분한 결과 → 다음 단계
        if not needs_retry:
            if mode == AIMode.HYBRID:
                return "rag_retriever"  # Hybrid면 RAG도 수행
            return "generator"

        # 재시도 가능 → QueryRewriter
        if retry_count < max_retries:
            return "query_rewriter"

        # HYBRID는 웹 검색 품질이 낮아도 내부 문서 결합 시도
        if mode == AIMode.HYBRID:
            return "rag_retriever"

        # 모든 시도 실패 → FallbackHandler
        return "fallback_handler"

    workflow.add_conditional_edges(
        "search_evaluator",
        route_after_evaluation,
        {
            "generator": "generator",
            "rag_retriever": "rag_retriever",
            "query_rewriter": "query_rewriter",
            "fallback_handler": "fallback_handler",
        },
    )

    # QueryRewriter → Searcher (루프!)
    workflow.add_edge("query_rewriter", "searcher")

    # FallbackHandler → 조건부 라우팅
    def route_after_fallback(state: GraphState) -> str:
        """폴백 후 라우팅."""
        mode = state.get("mode")
        if mode == AIMode.RAG:
            return "rag_retriever"  # RAG로 전환된 경우
        return "generator"

    workflow.add_conditional_edges(
        "fallback_handler",
        route_after_fallback,
        {
            "generator": "generator",
            "rag_retriever": "rag_retriever",
        },
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
    thread_id: str | None = None,
    mode: str | None = None,
    metadata: dict | None = None,
    enable_reflection: bool = False,
    enable_retry: bool = True,
    max_retries: int = 3,
    user_id: str | None = None,
) -> dict:
    """AI 에이전트 실행.

    Args:
        settings: 애플리케이션 설정
        query: 사용자 쿼리
        thread_id: 대화 ID (선택)
        mode: 강제 모드 지정 (선택, auto면 자동 감지)
        metadata: 추가 메타데이터
        enable_reflection: Reflector 노드 활성화 여부
        enable_retry: V8.4 검색 재시도 활성화 여부
        max_retries: 최대 재시도 횟수
        user_id: 사용자 ID (Langfuse 추적용)

    Returns:
        실행 결과 딕셔너리
    """
    from langchain_core.messages import AIMessage
    from ..services.thread_service import get_thread_service

    # V8.4: 재시도 기능 선택
    if enable_retry:
        graph = create_ai_graph_with_retry(
            settings,
            enable_reflection=enable_reflection,
            max_retries=max_retries,
        )
    else:
        graph = create_ai_graph(settings, enable_reflection=enable_reflection)

    # 대화 히스토리 불러오기 (V8.3: Query Contextualization 지원)
    messages = [HumanMessage(content=query)]
    if thread_id:
        try:
            from ..db.session import async_session_factory
            async with async_session_factory() as session:
                thread_svc = get_thread_service(session)
                thread = await thread_svc.get_thread(thread_id)
                if thread and thread.messages:
                    # completed 상태이고 content가 있는 메시지만 (queued/generating 제외)
                    completed_messages = [
                        m for m in thread.messages
                        if m.content and getattr(m, "status", "completed") == "completed"
                    ]
                    # 최근 10개 메시지만 (5턴) - 너무 긴 히스토리는 성능 저하
                    recent_messages = completed_messages[-10:]
                    messages = []
                    for msg in recent_messages:
                        if msg.role == "user":
                            messages.append(HumanMessage(content=msg.content))
                        elif msg.role == "assistant":
                            messages.append(AIMessage(content=msg.content))
                    # 현재 쿼리 추가
                    messages.append(HumanMessage(content=query))
                    logger.info(
                        f"[AIAgent] Loaded {len(messages) - 1} previous messages for thread {thread_id}"
                    )
        except Exception as e:
            logger.warning(f"[AIAgent] Failed to load thread history: {e}")
            messages = [HumanMessage(content=query)]

    # 콘텐츠 컨텍스트 로딩
    content_context = ""
    if metadata:
        ctx_ids = metadata.get("content_ids") or (
            [metadata["content_id"]] if metadata.get("content_id") else []
        )
        if ctx_ids:
            from .tools.content_context import load_content_context
            content_context = await load_content_context(
                ctx_ids,
                source_options=metadata.get("source_options"),
            )

    # 초기 상태 구성
    initial_state: GraphState = {
        "messages": messages,
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
        "thread_id": thread_id,
        "metadata": metadata or {},
        # V8.4: 검색 재시도 관련
        "search_retry_count": 0,
        "search_quality_score": 0.0,
        "original_search_queries": [],
        "failed_queries": [],
        "needs_retry": False,
        "retry_reason": "",
        # 콘텐츠 컨텍스트
        "content_context": content_context,
    }

    # 그래프 실행
    logger.info(
        f"[AIAgent] Running agent: query='{query[:50]}...', mode={mode}, thread_id={thread_id}"
    )

    # LLM Observability 콜백 핸들러 설정 (Langfuse)
    callbacks = []

    trace_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    if thread_id and "thread_id" not in trace_metadata:
        trace_metadata["thread_id"] = thread_id
    if user_id and "user_id" not in trace_metadata:
        trace_metadata["user_id"] = user_id
    if mode and "mode_requested" not in trace_metadata:
        trace_metadata["mode_requested"] = mode

    # Langfuse 핸들러
    langfuse_handler = get_langfuse_handler(
        user_id=user_id,
        session_id=thread_id,
        trace_name="ai-chat",
        tags=["ai-chat-mode", f"mode:{mode or 'auto'}"],
        metadata=trace_metadata,
    )
    if langfuse_handler:
        callbacks.append(langfuse_handler)

    config = {"callbacks": callbacks} if callbacks else {}

    try:
        result = await graph.ainvoke(initial_state, config=config)
        logger.info(
            f"[AIAgent] Completed: mode={result['mode']}, response_length={len(result.get('response', ''))}"
        )
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
    thread_id: str | None = None,
    mode: str | None = None,
    metadata: dict | None = None,
    enable_reflection: bool = False,
    enable_retry: bool = True,
    max_retries: int = 3,
    user_id: str | None = None,
) -> AsyncIterator[dict]:
    """AI 에이전트 스트리밍 실행 (토큰 단위 스트리밍).

    Args:
        settings: 애플리케이션 설정
        query: 사용자 쿼리
        thread_id: 대화 ID (선택)
        mode: 강제 모드 지정 (선택)
        metadata: 추가 메타데이터
        enable_reflection: Reflector 노드 활성화 여부
        enable_retry: 검색 재시도 활성화 여부 (V8.4)
        max_retries: 최대 재시도 횟수 (V8.4)
        user_id: 사용자 ID (Langfuse 트레이싱용)

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

    # OpenTelemetry Span Link 패턴 적용
    # - 현재 FastAPI 요청의 trace context를 parent로 추출
    # - 새 독립 trace 생성하되 Span Link로 연결
    # - LLM 처리는 독립적인 trace로 추적 (long-running)
    from ..core.telemetry import get_tracer
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Link, get_current_span, SpanKind
    from opentelemetry import context as otel_context

    tracer = get_tracer("ai-agent")

    # 현재 FastAPI 요청의 span context 추출 (parent)
    parent_span = get_current_span()
    parent_span_ctx = parent_span.get_span_context() if parent_span else None

    # Span Link 생성 (parent가 유효하면)
    links = []
    if parent_span_ctx and parent_span_ctx.is_valid:
        links = [Link(parent_span_ctx)]
        logger.debug(
            f"[AIAgent] Linked to parent trace: {format(parent_span_ctx.trace_id, '032x')}"
        )

    # Langfuse trace 생성 (v2 API)
    langfuse_trace = None
    langfuse_client = None
    try:
        from ..core.langfuse import get_langfuse_client, is_langfuse_enabled

        trace_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if thread_id and "thread_id" not in trace_metadata:
            trace_metadata["thread_id"] = thread_id
        if user_id and "user_id" not in trace_metadata:
            trace_metadata["user_id"] = user_id
        if mode and "mode_requested" not in trace_metadata:
            trace_metadata["mode_requested"] = mode

        if is_langfuse_enabled():
            langfuse_client = get_langfuse_client()
            if langfuse_client:
                langfuse_trace = langfuse_client.trace(
                    name="ai-chat-stream",
                    user_id=user_id,
                    session_id=thread_id,
                    input={"query": query, "mode": mode},
                    tags=["ai-chat-mode", "streaming", f"mode:{mode or 'auto'}"],
                    metadata=trace_metadata,
                )
                logger.debug(
                    f"[Langfuse] Trace created: {langfuse_trace.id if langfuse_trace else 'none'}"
                )
    except Exception as e:
        logger.warning(f"[Langfuse] Failed to create trace: {e}")

    # 독립적인 새 trace 시작 (Span Link로 연결)
    # Async generator에서는 context manager 사용이 복잡하므로 수동 관리
    otel_span = tracer.start_span(
        "ai-chat-stream",
        kind=SpanKind.INTERNAL,
        links=links,  # Parent trace와 링크!
        attributes={
            "ai.query": query[:200],
            "ai.mode": mode or "auto",
            "ai.thread_id": thread_id or "",
            "ai.user_id": user_id or "",
            "ai.operation": "stream",
        },
    )

    # Parent trace ID 저장 (검색 용이)
    if parent_span_ctx and parent_span_ctx.is_valid:
        otel_span.set_attribute(
            "link.trace_id", format(parent_span_ctx.trace_id, "032x")
        )
        otel_span.set_attribute("link.span_id", format(parent_span_ctx.span_id, "016x"))

    # Span을 current context로 활성화 (child span이 제대로 연결되도록)
    ctx = otel_trace.set_span_in_context(otel_span)
    token = otel_context.attach(ctx)

    # 대화 히스토리 불러오기 (V8.3: Query Contextualization 지원)
    from langchain_core.messages import AIMessage
    from ..services.thread_service import get_thread_service

    messages = [HumanMessage(content=query)]
    if thread_id:
        try:
            from ..db.session import async_session_factory
            async with async_session_factory() as session:
                thread_svc = get_thread_service(session)
                thread = await thread_svc.get_thread(thread_id)
                if thread and thread.messages:
                    # completed 상태이고 content가 있는 메시지만 (queued/generating 제외)
                    completed_messages = [
                        m for m in thread.messages
                        if m.content and getattr(m, "status", "completed") == "completed"
                    ]
                    # 최근 10개 메시지만 (5턴)
                    recent_messages = completed_messages[-10:]
                    messages = []
                    for msg in recent_messages:
                        if msg.role == "user":
                            messages.append(HumanMessage(content=msg.content))
                        elif msg.role == "assistant":
                            messages.append(AIMessage(content=msg.content))
                    # 현재 쿼리 추가
                    messages.append(HumanMessage(content=query))
                    logger.info(
                        f"[AIAgent] Stream: Loaded {len(messages) - 1} previous messages"
                    )
        except Exception as e:
            logger.warning(f"[AIAgent] Stream: Failed to load thread history: {e}")
            messages = [HumanMessage(content=query)]

    # 콘텐츠 컨텍스트 로딩
    content_context = ""
    if metadata:
        ctx_ids = metadata.get("content_ids") or (
            [metadata["content_id"]] if metadata.get("content_id") else []
        )
        if ctx_ids:
            from .tools.content_context import load_content_context
            content_context = await load_content_context(
                ctx_ids,
                source_options=metadata.get("source_options"),
            )

    # 초기 상태 구성
    state: GraphState = {
        "messages": messages,
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
        "thread_id": thread_id,
        "metadata": metadata or {},
        # V8.4: 검색 재시도 관련
        "search_retry_count": 0,
        "search_quality_score": 0.0,
        "original_search_queries": [],
        "failed_queries": [],
        "needs_retry": False,
        "retry_reason": "",
        # 콘텐츠 컨텍스트
        "content_context": content_context,
    }

    try:
        # 1. Intent 분석
        yield {
            "type": "thinking",
            "data": {"step": "intent_analysis", "content": "질문 분석 중..."},
        }

        # Langfuse span
        intent_span = None
        if langfuse_trace:
            intent_span = langfuse_trace.span(
                name="intent_parser", input={"query": query}
            )

        # OpenTelemetry child span
        with tracer.start_as_current_span("intent_parser") as intent_otel:
            intent_otel.set_attribute("ai.query", query[:200])

            intent_parser = IntentParserNode(settings)
            intent_result = await intent_parser(state)
            state.update(intent_result)

            detected_mode = state["mode"]
            selected_tier = state.get(
                "selected_model", LLMTier.SIMPLE
            )  # tier명이 selected_model에 저장됨
            query_analysis = state.get("query_analysis")

            intent_otel.set_attribute("ai.detected_mode", str(detected_mode))
            intent_otel.set_attribute("ai.selected_tier", selected_tier)

        if intent_span:
            intent_span.end(
                output={
                    "mode": str(detected_mode),
                    "selected_tier": selected_tier,
                    "query_analysis": query_analysis,
                }
            )

        # 모드/티어 한글 표시명 가져오기
        mode_display = MODE_DISPLAY_MAP.get(detected_mode, str(detected_mode))
        tier_display = TIER_DISPLAY_MAP.get(selected_tier, selected_tier)

        yield {
            "type": "thinking",
            "data": {
                "step": "intent_result",
                "content": f"모드 선택 완료 ({mode_display} / {tier_display})",
                "mode": detected_mode.value,
                "selected_tier": selected_tier,
            },
        }

        # 쿼리 재정의 결과가 있으면 전송 (Perplexity 스타일 UI용)
        if query_analysis:
            reformulated_query = query_analysis.get("reformulated_query", "")
            keywords = query_analysis.get("keywords", [])
            search_focus = query_analysis.get("search_focus", "")
            reframed_queries = query_analysis.get("sub_queries", [])

            topic_parts = []
            if reformulated_query:
                topic_parts.append(f"핵심 질문: {reformulated_query}")
            if keywords:
                topic_parts.append(f"핵심 키워드: {', '.join(keywords[:4])}")
            if search_focus:
                topic_parts.append(f"검색 방향: {search_focus}")

            if topic_parts:
                yield {
                    "type": "thinking",
                    "data": {
                        "step": "topic_understanding",
                        "content": "\n".join(
                            ["사용자 질문을 이렇게 이해했어요.", *topic_parts]
                        ),
                    },
                }

            if reframed_queries:
                if len(reframed_queries) == 1:
                    reframing_content = (
                        "요청을 단일 검색 과제로 재정의했습니다.\n"
                        f"1. {reframed_queries[0]}"
                    )
                else:
                    numbered_queries = "\n".join(
                        [f"{idx + 1}. {q}" for idx, q in enumerate(reframed_queries)]
                    )
                    reframing_content = (
                        f"요청을 {len(reframed_queries)}개 검색 과제로 재정의했습니다.\n"
                        f"{numbered_queries}"
                    )

                yield {
                    "type": "thinking",
                    "data": {
                        "step": "request_reframing",
                        "content": reframing_content,
                    },
                }

            yield {
                "type": "query_analysis",
                "data": {
                    "original_query": query_analysis.get("original_query", query),
                    "reformulated_query": reformulated_query,
                    "search_queries": query_analysis.get("sub_queries", []),
                    "keywords": keywords,
                    "search_focus": search_focus,
                    "search_recency": query_analysis.get("search_recency", ""),
                    "search_language": query_analysis.get("search_language", ""),
                    "domain_allowlist": query_analysis.get("domain_allowlist", []),
                },
            }

            # 쿼리 생성 후 상태 표시
            search_queries = query_analysis.get("sub_queries", [])
            if search_queries:
                queries_text = "\n".join(
                    [f"{idx + 1}. {q}" for idx, q in enumerate(search_queries)]
                )
                yield {
                    "type": "thinking",
                    "data": {
                        "step": "query_generated",
                        "content": queries_text,
                    },
                }

        # IntentParser가 생성한 search_queries 전송 (메타데이터 저장용)
        search_queries_from_intent = state.get("search_queries", [])
        if search_queries_from_intent:
            yield {"type": "search_queries", "data": search_queries_from_intent}

        # 2. 모드별 처리
        if detected_mode == AIMode.SEARCH:
            # V8.4: 재시도 활성화 시 루프 구조
            if enable_retry:
                from .nodes import (
                    SearchEvaluatorNode,
                    QueryRewriterNode,
                    FallbackHandlerNode,
                )

                # 원본 쿼리 저장
                state["original_search_queries"] = state.get("search_queries", [query])

                # 재시도 루프
                while state["search_retry_count"] < max_retries:
                    # 웹 검색
                    retry_count = state["search_retry_count"]
                    if retry_count == 0:
                        yield {
                            "type": "thinking",
                            "data": {"step": "web_search", "content": "웹 검색 중..."},
                        }
                    else:
                        yield {
                            "type": "thinking",
                            "data": {
                                "step": f"web_search_retry_{retry_count}",
                                "content": f"재검색 중... ({retry_count}/{max_retries})",
                            },
                        }

                    search_span = None
                    if langfuse_trace:
                        search_span = langfuse_trace.span(
                            name=f"web_search_attempt_{retry_count}",
                            input={"queries": state.get("search_queries", [])},
                        )

                    searcher = SearcherNode(settings)
                    search_result = await searcher(state)
                    state.update(search_result)

                    search_results = state.get("search_results", [])
                    if search_span:
                        search_span.end(output={"results_count": len(search_results)})

                    # 품질 평가
                    evaluator = SearchEvaluatorNode(settings)
                    eval_result = await evaluator(state)
                    state.update(eval_result)

                    quality_score = state.get("search_quality_score", 0.0)
                    needs_retry = state.get("needs_retry", False)
                    retry_reason = state.get("retry_reason", "")

                    # 재시도 정보 전송
                    if retry_count > 0 or needs_retry:
                        yield {
                            "type": "search_retry",
                            "data": {
                                "retry_count": retry_count,
                                "quality_score": quality_score,
                                "reason": retry_reason,
                                "results_count": len(search_results),
                            },
                        }

                    # 성공하면 루프 종료
                    if not needs_retry:
                        if search_results:
                            yield {
                                "type": "sources",
                                "data": _convert_sources(search_results),
                            }
                            # 검색 결과 전송 (메타데이터 저장용)
                            yield {"type": "search_results", "data": search_results}
                            # 출처 분석 상태 표시
                            yield {
                                "type": "thinking",
                                "data": {
                                    "step": "source_analysis",
                                    "content": f"답변 준비 중 (출처 {len(search_results)}개 분석)",
                                },
                            }
                        break

                    # 최대 재시도 도달 시 폴백
                    if retry_count >= max_retries - 1:
                        fallback = FallbackHandlerNode(settings)
                        fallback_result = await fallback(state)
                        state.update(fallback_result)
                        break

                    # 쿼리 재작성
                    rewriter = QueryRewriterNode(settings)
                    rewrite_result = await rewriter(state)
                    state.update(rewrite_result)

                    # 재시도 카운트 증가
                    state["search_retry_count"] = retry_count + 1
            else:
                # 재시도 비활성화 - 기존 로직
                # "웹 검색 중..." 메시지는 쿼리 생성 후 이미 표시되므로 제거

                search_span = None
                if langfuse_trace:
                    search_span = langfuse_trace.span(
                        name="web_search",
                        input={"queries": state.get("search_queries", [])},
                    )

                searcher = SearcherNode(settings)
                search_result = await searcher(state)
                state.update(search_result)

                search_results = state.get("search_results", [])
                if search_span:
                    search_span.end(output={"results_count": len(search_results)})

                if search_results:
                    yield {"type": "sources", "data": _convert_sources(search_results)}
                    # 검색 결과 전송 (메타데이터 저장용)
                    yield {"type": "search_results", "data": search_results}
                    # 출처 분석 상태 표시
                    yield {
                        "type": "thinking",
                        "data": {
                            "step": "source_analysis",
                            "content": f"답변 준비 중 (출처 {len(search_results)}개 분석)",
                        },
                    }

        elif detected_mode == AIMode.RAG:
            # RAG 검색
            yield {
                "type": "thinking",
                "data": {"step": "rag_search", "content": "내부 문서 검색 중..."},
            }

            rag_span = None
            if langfuse_trace:
                rag_span = langfuse_trace.span(
                    name="rag_retrieval", input={"query": query}
                )

            rag_retriever = RAGRetrieverNode(settings)
            rag_result = await rag_retriever(state)
            state.update(rag_result)

            search_results = state.get("search_results", [])
            if rag_span:
                rag_span.end(output={"results_count": len(search_results)})

            if search_results:
                yield {
                    "type": "thinking",
                    "data": {
                        "step": "rag_search_complete",
                        "content": f"문서 검색 완료: {len(search_results)}개 결과",
                    },
                }
                yield {"type": "sources", "data": _convert_sources(search_results)}
                # 검색 결과 전송 (메타데이터 저장용)
                yield {"type": "search_results", "data": search_results}

        elif detected_mode == AIMode.HYBRID:
            hybrid_span = None
            if langfuse_trace:
                hybrid_span = langfuse_trace.span(
                    name="hybrid_search", input={"query": query}
                )

            web_results = state.get("search_results", [])

            # 웹 검색 + 품질 평가 + 재시도 (SEARCH 모드와 동일 정책)
            if enable_retry:
                from .nodes import SearchEvaluatorNode, QueryRewriterNode

                state["original_search_queries"] = state.get("search_queries", [query])

                while state["search_retry_count"] < max_retries:
                    retry_count = state["search_retry_count"]
                    if retry_count == 0:
                        yield {
                            "type": "thinking",
                            "data": {"step": "web_search", "content": "웹 검색 중..."},
                        }
                    else:
                        yield {
                            "type": "thinking",
                            "data": {
                                "step": f"web_search_retry_{retry_count}",
                                "content": f"재검색 중... ({retry_count}/{max_retries})",
                            },
                        }

                    searcher = SearcherNode(settings)
                    search_result = await searcher(state)
                    state.update(search_result)

                    web_results = state.get("search_results", [])

                    evaluator = SearchEvaluatorNode(settings)
                    eval_result = await evaluator(state)
                    state.update(eval_result)

                    quality_score = state.get("search_quality_score", 0.0)
                    needs_retry = state.get("needs_retry", False)
                    retry_reason = state.get("retry_reason", "")

                    if retry_count > 0 or needs_retry:
                        yield {
                            "type": "search_retry",
                            "data": {
                                "retry_count": retry_count,
                                "quality_score": quality_score,
                                "reason": retry_reason,
                                "results_count": len(web_results),
                            },
                        }

                    if not needs_retry:
                        if web_results:
                            yield {
                                "type": "thinking",
                                "data": {
                                    "step": "web_search_complete",
                                    "content": f"웹 검색 완료: {len(web_results)}개 결과",
                                },
                            }
                        break

                    # HYBRID는 웹 검색 재시도 한계 도달 시에도 RAG 결합을 진행
                    if retry_count >= max_retries - 1:
                        yield {
                            "type": "thinking",
                            "data": {
                                "step": "web_search_degraded",
                                "content": "웹 검색 품질이 낮아 내부 문서 결합으로 보완합니다.",
                            },
                        }
                        break

                    rewriter = QueryRewriterNode(settings)
                    rewrite_result = await rewriter(state)
                    state.update(rewrite_result)

                    state["search_retry_count"] = retry_count + 1
            else:
                yield {
                    "type": "thinking",
                    "data": {"step": "web_search", "content": "웹 검색 중..."},
                }

                searcher = SearcherNode(settings)
                search_result = await searcher(state)
                state.update(search_result)

                web_results = state.get("search_results", [])
                if web_results:
                    yield {
                        "type": "thinking",
                        "data": {
                            "step": "web_search_complete",
                            "content": f"웹 검색 완료: {len(web_results)}개 결과",
                        },
                    }

            yield {
                "type": "thinking",
                "data": {"step": "rag_search", "content": "내부 문서 검색 중..."},
            }
            rag_retriever = RAGRetrieverNode(settings)
            rag_result = await rag_retriever(state)
            state.update(rag_result)

            all_results = state.get("search_results", [])
            if hybrid_span:
                hybrid_span.end(
                    output={
                        "web_results": len(web_results),
                        "total_results": len(all_results),
                        "retry_count": state.get("search_retry_count", 0),
                    }
                )

            if all_results:
                yield {
                    "type": "thinking",
                    "data": {
                        "step": "search_complete",
                        "content": f"통합 검색 완료: {len(all_results)}개 결과",
                    },
                }
                yield {"type": "sources", "data": _convert_sources(all_results)}
                # 검색 결과 전송 (메타데이터 저장용)
                yield {"type": "search_results", "data": all_results}

        elif detected_mode == AIMode.REASONING:
            # 추론 모드 - Reasoner가 직접 응답 생성
            yield {
                "type": "thinking",
                "data": {"step": "reasoning_start", "content": "단계별 분석 중..."},
            }

            reasoning_span = None
            if langfuse_trace:
                reasoning_span = langfuse_trace.span(
                    name="reasoner", input={"query": query}
                )

            reasoner = ReasonerNode(settings)
            reasoning_response = ""

            # Reasoner 스트리밍 - content를 token으로 변환하여 점진적 표시
            async for chunk_event in reasoner.stream(state):
                if chunk_event.get("type") == "content":
                    # content 이벤트를 token으로 변환
                    token_data = chunk_event.get("data", "")
                    reasoning_response += token_data
                    yield {"type": "token", "data": token_data}

            if reasoning_span:
                reasoning_span.end(output={"response": reasoning_response})

            # Langfuse trace 완료
            if langfuse_trace:
                langfuse_trace.update(
                    output={
                        "response": reasoning_response,
                        "mode": str(detected_mode),
                        "selected_tier": selected_tier,
                    }
                )
                if langfuse_client:
                    langfuse_client.flush()

            yield {
                "type": "thinking",
                "data": {"step": "reasoning_complete", "content": "추론 완료"},
            }
            yield {"type": "done", "data": None}
            return

        # 3. Generator로 응답 생성 (토큰 스트리밍)
        yield {
            "type": "thinking",
            "data": {"step": "generation_start", "content": "답변 생성 중..."},
        }

        generation_span = None
        if langfuse_trace:
            generation_span = langfuse_trace.span(
                name="generator",
                input={"query": query, "context": state.get("search_results", [])},
            )

        full_response = ""
        generator = GeneratorNode(settings)
        async for chunk_event in generator.stream(state):
            event_type = chunk_event.get("type", "")
            event_data = chunk_event.get("data")

            if event_type == "thinking":
                # 사고 과정 전달
                yield chunk_event
            elif event_type == "content":
                # 토큰 단위 스트리밍
                full_response += event_data
                yield {"type": "token", "data": event_data}
            elif event_type == "sources":
                yield {"type": "sources", "data": _convert_sources(event_data)}
            elif event_type == "citations":
                # Citation 정보 전달
                yield chunk_event
            elif event_type == "done":
                # 최종 응답
                pass

        if generation_span:
            generation_span.end(output={"response": full_response})

        # Langfuse trace 완료
        if langfuse_trace:
            langfuse_trace.update(
                output={
                    "response": full_response,
                    "mode": str(detected_mode),
                    "selected_tier": selected_tier,
                }
            )
            if langfuse_client:
                langfuse_client.flush()

        # OpenTelemetry span 완료
        otel_span.set_attribute("ai.detected_mode", str(detected_mode))
        otel_span.set_attribute("ai.selected_tier", selected_tier)
        otel_span.set_attribute("ai.response_length", len(full_response))
        otel_context.detach(token)
        otel_span.end()

        yield {"type": "done", "data": None}

    except Exception as e:
        logger.error(f"[AIAgent] Stream failed: {e}")

        # Langfuse trace 에러 기록
        if langfuse_trace:
            langfuse_trace.update(output={"error": str(e)}, level="ERROR")
            if langfuse_client:
                langfuse_client.flush()

        # OpenTelemetry span 에러 기록
        from opentelemetry.trace import Status, StatusCode

        otel_span.set_status(Status(StatusCode.ERROR, str(e)))
        otel_span.record_exception(e)
        otel_context.detach(token)
        otel_span.end()

        yield {"type": "error", "data": str(e)}


def _convert_sources(sources: list) -> list[dict]:
    """검색 결과를 dict 형태로 변환."""
    return [
        {
            "title": s.get("title", "")
            if isinstance(s, dict)
            else getattr(s, "title", ""),
            "url": s.get("url", "") if isinstance(s, dict) else getattr(s, "url", ""),
            "snippet": s.get("snippet", "")
            if isinstance(s, dict)
            else getattr(s, "snippet", ""),
            "source": s.get("source", "web")
            if isinstance(s, dict)
            else getattr(s, "source", "web"),
        }
        for s in sources
    ]
