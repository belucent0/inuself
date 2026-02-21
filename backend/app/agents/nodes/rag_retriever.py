"""RAG Retriever 노드.

내부 콘텐츠를 검색하여 상태에 추가하는 노드입니다.
"""
from __future__ import annotations

from loguru import logger
import time
from typing import Any

from ..state import GraphState, AIMode, ThinkingStep, SearchResult
from ..tools.rag_search import search_internal_content, RAGSearchError




class RAGRetrieverNode:
    """내부 콘텐츠를 검색하는 노드."""

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings

    async def __call__(self, state: GraphState) -> dict:
        """RAG 검색 실행.

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        query = state["query"]
        mode = state["mode"]
        metadata = state.get("metadata", {})
        thinking_steps = list(state.get("thinking_steps", []))
        existing_results = list(state.get("search_results", []))

        # 사고 과정 기록
        thinking_steps.append(ThinkingStep(
            step="rag_search_start",
            content=f"내부 문서 검색 시작: '{query[:50]}...'",
            timestamp=time.time()
        ))

        # 특정 콘텐츠 ID가 지정된 경우 (content_id/content_ids 키 방어적 처리)
        content_ids = metadata.get("content_ids")
        if not content_ids:
            single_id = metadata.get("content_id")
            if single_id:
                content_ids = [single_id]

        try:
            results = await search_internal_content(
                query,
                settings=self.settings,
                limit=5,
                content_ids=content_ids,
            )

            logger.info(f"[RAGRetriever] Found {len(results)} internal results for: {query[:50]}")

            # 결과를 SearchResult 형식으로 변환
            rag_results = [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("snippet", ""),
                    source="rag",
                )
                for r in results
            ]

            thinking_steps.append(ThinkingStep(
                step="rag_search_complete",
                content=f"내부 문서 검색 완료: {len(rag_results)}개 결과 발견",
                timestamp=time.time()
            ))

            # Hybrid 모드인 경우 기존 웹 검색 결과와 병합
            if mode == AIMode.HYBRID and existing_results:
                combined_results = existing_results + rag_results
                thinking_steps.append(ThinkingStep(
                    step="hybrid_merge",
                    content=f"웹 검색 {len(existing_results)}개 + 내부 문서 {len(rag_results)}개 통합",
                    timestamp=time.time()
                ))
                return {
                    "search_results": combined_results,
                    "thinking_steps": thinking_steps,
                }

            return {
                "search_results": rag_results,
                "thinking_steps": thinking_steps,
            }

        except RAGSearchError as e:
            logger.warning(f"[RAGRetriever] Search failed: {e}")
            thinking_steps.append(ThinkingStep(
                step="rag_search_error",
                content=f"내부 문서 검색 실패: {str(e)}",
                timestamp=time.time()
            ))
            return {
                "search_results": existing_results,  # 기존 결과 유지
                "thinking_steps": thinking_steps,
                "error": f"내부 검색 실패: {e}",
            }

        except Exception as e:
            logger.error(f"[RAGRetriever] Unexpected error: {e}")
            thinking_steps.append(ThinkingStep(
                step="rag_search_error",
                content=f"예상치 못한 오류: {str(e)}",
                timestamp=time.time()
            ))
            return {
                "search_results": existing_results,
                "thinking_steps": thinking_steps,
                "error": f"RAG 오류: {e}",
            }
