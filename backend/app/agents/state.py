"""AI Agent 상태 정의.

LangGraph 워크플로우에서 사용하는 상태 스키마를 정의합니다.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AIMode(str, Enum):
    """AI 모드 종류."""
    SIMPLE = "simple"       # 단순 대화
    SEARCH = "search"       # 웹 검색
    RAG = "rag"             # 내부 문서 검색
    REASONING = "reasoning" # 복잡한 추론
    HYBRID = "hybrid"       # 웹 + RAG 통합


class SearchResult(TypedDict):
    """검색 결과."""
    title: str
    url: str
    snippet: str
    source: str  # "web" | "rag"


class CitationInfo(TypedDict):
    """Citation (출처 표시) 정보 - Phase 4."""
    id: int
    title: str
    url: str
    snippet: str
    verified: bool


class ThinkingStep(TypedDict):
    """사고 과정 단계."""
    step: str
    content: str
    timestamp: float


class QueryAnalysis(TypedDict, total=False):
    """쿼리 분석 결과 (Perplexity 스타일).

    사용자 질문의 핵심 의도를 파악하고, 필요시 하위 질문으로 분해합니다.
    """
    original_query: str        # 원본 쿼리
    reformulated_query: str    # 핵심 의도로 재구성된 쿼리
    sub_queries: list[str]     # 분해된 하위 질문들
    keywords: list[str]        # 추출된 핵심 키워드
    search_focus: str          # 검색 초점 (예: "최신 뉴스", "기술 비교" 등)


class GraphState(TypedDict):
    """LangGraph 워크플로우 상태.

    Attributes:
        messages: 대화 히스토리 (LangGraph add_messages 리듀서 사용)
        query: 현재 사용자 쿼리
        mode: AI 모드 (simple, search, rag, reasoning, hybrid)
        selected_model: 동적 라우팅으로 선택된 LLM 모델명
        intent_confidence: 의도 분석 신뢰도 (0.0 ~ 1.0)
        requires_clarification: 추가 질문 필요 여부
        clarification_question: 추가 질문 내용
        query_analysis: 쿼리 분석 결과 (재정의된 검색 쿼리 포함)
        search_queries: 검색에 사용할 재정의된 쿼리 목록
        search_results: 검색 결과 목록
        thinking_steps: 사고 과정 기록
        response: 최종 응답
        sources: 출처 목록
        citations: Citation (출처 표시) 목록 - Phase 4
        error: 에러 메시지 (있는 경우)
        conversation_id: 대화 ID (Redis 저장용)
        metadata: 추가 메타데이터

        # V8.4: 검색 재시도 관련 필드
        search_retry_count: int              # 현재 재시도 횟수
        search_quality_score: float          # 검색 결과 품질 점수 (0-100)
        original_search_queries: list[str]   # 원본 검색 쿼리 (재작성 참고용)
        failed_queries: list[str]            # 실패한 쿼리 목록 (중복 방지)
        needs_retry: bool                    # 재시도 필요 여부
        retry_reason: str                    # 재시도 이유
    """
    messages: Annotated[list[BaseMessage], add_messages]
    query: str
    mode: AIMode
    selected_model: str | None
    intent_confidence: float
    requires_clarification: bool
    clarification_question: str | None
    query_analysis: QueryAnalysis | None
    search_queries: list[str]
    search_results: list[SearchResult]
    thinking_steps: list[ThinkingStep]
    response: str
    sources: list[SearchResult]
    citations: list[CitationInfo]  # Phase 4: Citation 추가
    error: str | None
    conversation_id: str | None
    metadata: dict

    # V8.4: 검색 재시도 관련
    search_retry_count: int
    search_quality_score: float
    original_search_queries: list[str]
    failed_queries: list[str]
    needs_retry: bool
    retry_reason: str
