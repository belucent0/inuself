"""TSMC 관련 대화 상세 분석"""
import sys
import os
import asyncio
import json
from datetime import datetime

# Windows 인코딩 설정
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Backend 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.services.conversation_service import get_conversation_service


async def analyze_tsmc_conversation():
    """TSMC 대화 상세 분석"""
    print("=" * 80)
    print("TSMC 관련 대화 분석")
    print("=" * 80)

    service = get_conversation_service()

    # TSMC 대화 ID (출력에서 확인된 것)
    conv_id = "30a55732-04c1-48e2-9449-56bd00958188"

    conversation = await service.get_conversation(conv_id)

    if not conversation:
        print(f"대화를 찾을 수 없습니다: {conv_id}")
        return

    print(f"\n대화 ID: {conversation.conversation_id}")
    print(f"제목: {conversation.title}")
    print(f"생성: {datetime.fromtimestamp(conversation.created_at).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"총 메시지: {len(conversation.messages)}개\n")

    # TSMC 관련 메시지만 필터링
    tsmc_messages = []
    for i, msg in enumerate(conversation.messages):
        if 'tsmc' in msg.content.lower() or i >= 2:  # tsmc 언급 이후 모든 메시지
            tsmc_messages.append((i+1, msg))

    print("=" * 80)
    print("TSMC 관련 질문 시퀀스 분석")
    print("=" * 80)

    user_questions = []

    for idx, message in tsmc_messages:
        role = message.role
        content = message.content
        timestamp = datetime.fromtimestamp(message.timestamp)
        metadata = message.metadata

        print(f"\n[메시지 {idx}] {role.upper()} - {timestamp.strftime('%H:%M:%S')}")
        print("-" * 80)

        if role == "user":
            user_questions.append(content)
            print(f"질문: {content}")
        elif role == "assistant":
            # 응답 요약
            response_preview = content[:300].replace('\n', ' ')
            print(f"응답 미리보기: {response_preview}...")
            print(f"응답 길이: {len(content)}자")

            # 메타데이터 확인
            if metadata:
                print("\n[메타데이터]")
                for key, value in metadata.items():
                    if key == "search_results" and isinstance(value, list):
                        print(f"  - {key}: {len(value)}개")
                        for i, result in enumerate(value[:3], 1):
                            if isinstance(result, dict):
                                print(f"      {i}. {result.get('title', 'N/A')[:60]}")
                                print(f"         품질: {result.get('quality_score', 'N/A')}")
                    elif key == "search_queries" and isinstance(value, list):
                        print(f"  - {key}: {len(value)}개")
                        for i, q in enumerate(value[:5], 1):
                            print(f"      {i}. {q}")
                    elif key == "intent" and value:
                        print(f"  - intent:")
                        if isinstance(value, dict):
                            print(f"      의도: {value.get('intent', 'N/A')}")
                            print(f"      모드: {value.get('mode', 'N/A')}")
                    elif key == "citations" and isinstance(value, list):
                        print(f"  - citations: {len(value)}개")
                    else:
                        if isinstance(value, (list, dict)):
                            print(f"  - {key}: {type(value).__name__} (길이: {len(value)})")
                        else:
                            print(f"  - {key}: {value}")
            else:
                print("[메타데이터 없음]")

    # 연이은 질문 평가
    print("\n" + "=" * 80)
    print("연이은 질문 품질 평가")
    print("=" * 80)

    print(f"\n총 {len(user_questions)}개의 사용자 질문:")
    for i, q in enumerate(user_questions, 1):
        print(f"  {i}. {q}")

    print("\n### 분석 ###\n")

    if len(user_questions) >= 1:
        print(f"[질문 1] {user_questions[0]}")
        print("평가:")
        print("  - 구체성: 명확한 주제 (TSMC, 2026, AI, 투자)")
        print("  - 시간성: 미래 시점 (2026년) 명시")
        print("  - 도메인: 반도체 산업, 기업 투자 전략")
        print()

    if len(user_questions) >= 2:
        print(f"[질문 2] {user_questions[1]}")
        print("평가:")
        print("  - 컨텍스트 의존성: 이전 질문의 '투자 계획'을 참조")
        print("  - 구체화 요청: '구체적인' 세부사항 요구")
        print("  - 대화 연속성: 좋음 (follow-up 질문)")
        print()

    if len(user_questions) >= 3:
        print(f"[질문 3] {user_questions[2]}")
        print("평가:")
        print("  - 의도 명확화: 이전 답변이 의도와 맞지 않음을 지적")
        print("  - 피드백 제공: AI가 잘못 이해했음을 알림")
        print("  - 기대 재설정: 'TSMC 사례'로 범위 축소")
        print()

    print("\n### 종합 평가 ###\n")
    print("1. Query Contextualization 필요성:")
    print("   - 질문 2: '구체적인 투자 계획' -> 'TSMC 2026 AI 투자 구체적 계획'")
    print("   - 질문 3: 'TSMC의 사례' -> 이전 대화 맥락에서 'TSMC 투자 성공 사례'")
    print()
    print("2. Intent Analysis 품질:")
    print("   - 질문 1: SEARCH 모드 (정보 검색)")
    print("   - 질문 2: SEARCH 모드 (세부 정보 요청)")
    print("   - 질문 3: CLARIFICATION (의도 재설정)")
    print()
    print("3. 검색 쿼리 품질 (예상):")
    print("   - 원본 쿼리가 짧고 맥락 의존적")
    print("   - HyDETransformer가 컨텍스트 보강 필요")
    print("   - 대화 히스토리 활용 필수")
    print()
    print("4. 개선 방향:")
    print("   - ✓ Phase 1B (HyDE): 구현됨")
    print("   - ✓ Phase 2 (Vector Search): 구현됨")
    print("   - ✓ Phase 3 (Quality Ranking): 구현됨")
    print("   - ✓ Phase 4 (Citations): 구현됨")
    print("   - → Query Contextualization이 제대로 작동하는지 확인 필요")
    print("   - → 메타데이터 저장이 이제 수정됨 (새 대화에서 검증 필요)")


if __name__ == "__main__":
    asyncio.run(analyze_tsmc_conversation())
