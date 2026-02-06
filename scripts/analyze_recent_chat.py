"""가장 최근 AI 채팅 세션 분석 스크립트

사용자가 의도한대로 AI가 검색하여 답변했는지 확인:
- Intent 분석 품질
- 웹 검색 쿼리 품질
- 검색 결과 관련성
- Citation 사용 여부
"""
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


async def analyze_recent_conversations():
    """최근 대화 세션 분석"""
    print("=" * 80)
    print("최근 AI 채팅 세션 분석")
    print("=" * 80)

    # 1. Conversation Service 가져오기
    service = get_conversation_service()

    # 2. 최근 대화 목록 조회
    print("\n[1] 최근 대화 목록 조회 중...")
    conversations = await service.list_conversations(limit=10)

    if not conversations:
        print("저장된 대화가 없습니다.")
        return

    print(f"총 {len(conversations)}개의 대화 발견\n")

    # 3. 각 대화 요약 출력
    for i, conv_summary in enumerate(conversations, 1):
        created = datetime.fromtimestamp(conv_summary['created_at'])
        updated = datetime.fromtimestamp(conv_summary['updated_at'])

        print(f"[{i}] {conv_summary['conversation_id'][:8]}...")
        print(f"    제목: {conv_summary['title']}")
        print(f"    메시지: {conv_summary['message_count']}개")
        print(f"    생성: {created.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"    업데이트: {updated.strftime('%Y-%m-%d %H:%M:%S')}")
        print()

    # 4. 가장 최근 대화 상세 분석
    print("\n" + "=" * 80)
    print("[2] 가장 최근 대화 상세 분석")
    print("=" * 80)

    latest_conv_id = conversations[0]['conversation_id']
    conversation = await service.get_conversation(latest_conv_id)

    if not conversation:
        print(f"대화를 불러올 수 없습니다: {latest_conv_id}")
        return

    print(f"\n대화 ID: {conversation.conversation_id}")
    print(f"제목: {conversation.title}")
    print(f"총 메시지: {len(conversation.messages)}개\n")

    # 5. 각 메시지 분석
    for idx, message in enumerate(conversation.messages, 1):
        role = message.role
        content = message.content
        timestamp = datetime.fromtimestamp(message.timestamp)
        metadata = message.metadata

        print("-" * 80)
        print(f"[메시지 {idx}] {role.upper()} ({timestamp.strftime('%H:%M:%S')})")
        print("-" * 80)

        # 사용자 메시지
        if role == "user":
            print(f"질문: {content[:200]}")
            if len(content) > 200:
                print("    (... 생략)")

        # AI 응답 - 메타데이터 분석
        elif role == "assistant":
            print(f"응답: {content[:200]}")
            if len(content) > 200:
                print("    (... 생략)")

            # 메타데이터 분석
            if metadata:
                print("\n### 메타데이터 분석 ###")

                # Intent Parser 결과
                if 'intent' in metadata:
                    intent = metadata['intent']
                    print(f"\n[Intent]")
                    print(f"  - 의도: {intent.get('intent', 'N/A')}")
                    print(f"  - 모드: {intent.get('mode', 'N/A')}")

                # 검색 쿼리
                if 'search_queries' in metadata:
                    queries = metadata['search_queries']
                    print(f"\n[검색 쿼리] (총 {len(queries)}개)")
                    for i, q in enumerate(queries[:5], 1):  # 최대 5개만 표시
                        print(f"  {i}. {q}")

                # 검색 결과
                if 'search_results' in metadata:
                    results = metadata['search_results']
                    print(f"\n[검색 결과] (총 {len(results)}개)")
                    for i, r in enumerate(results[:5], 1):  # 최대 5개만 표시
                        title = r.get('title', 'N/A')
                        url = r.get('url', 'N/A')
                        quality = r.get('quality_score', 0)
                        print(f"  {i}. [{quality:.1f}점] {title}")
                        print(f"     {url}")

                # Citation 정보
                if 'citations' in metadata:
                    citations = metadata['citations']
                    print(f"\n[Citations] (총 {len(citations)}개)")
                    for cite in citations:
                        cid = cite.get('id', '?')
                        title = cite.get('title', 'N/A')
                        verified = cite.get('verified', False)
                        status = 'OK' if verified else 'Invalid'
                        print(f"  [{cid}] {title} ({status})")

                # 추가 정보
                if 'error' in metadata:
                    print(f"\n[오류] {metadata['error']}")

                if 'reasoning' in metadata:
                    print(f"\n[추론 과정]")
                    print(f"  {metadata['reasoning'][:300]}")

        print()

    # 6. 검색 품질 평가
    print("\n" + "=" * 80)
    print("[3] 검색 품질 종합 평가")
    print("=" * 80)

    ai_messages = [m for m in conversation.messages if m.role == "assistant"]

    if not ai_messages:
        print("AI 응답이 없습니다.")
        return

    # 마지막 AI 응답 평가
    last_ai = ai_messages[-1]
    metadata = last_ai.metadata

    print("\n### 평가 항목 ###\n")

    # 1) Intent 분석
    if 'intent' in metadata:
        print("[OK] Intent 분석 완료")
        print(f"     - 의도: {metadata['intent'].get('intent', 'N/A')}")
    else:
        print("[!] Intent 분석 정보 없음")

    # 2) 검색 쿼리
    if 'search_queries' in metadata and metadata['search_queries']:
        queries = metadata['search_queries']
        print(f"[OK] 검색 쿼리 생성: {len(queries)}개")
        for i, q in enumerate(queries[:3], 1):
            print(f"     {i}. {q}")
    else:
        print("[!] 검색 쿼리가 생성되지 않음")

    # 3) 검색 결과
    if 'search_results' in metadata and metadata['search_results']:
        results = metadata['search_results']
        print(f"[OK] 검색 결과: {len(results)}개")

        # 품질 점수 분석
        scores = [r.get('quality_score', 0) for r in results if 'quality_score' in r]
        if scores:
            avg_quality = sum(scores) / len(scores)
            print(f"     - 평균 품질 점수: {avg_quality:.1f}/100")
            print(f"     - 최고 점수: {max(scores):.1f}")
            print(f"     - 최저 점수: {min(scores):.1f}")
    else:
        print("[!] 검색 결과가 없음")

    # 4) Citation
    if 'citations' in metadata and metadata['citations']:
        citations = metadata['citations']
        verified = [c for c in citations if c.get('verified', False)]
        print(f"[OK] Citation: {len(citations)}개 (유효: {len(verified)}개)")
    else:
        print("[!] Citation 정보 없음")

    # 5) 응답 품질
    content = last_ai.content
    print(f"\n[응답 분석]")
    print(f"  - 길이: {len(content)}자")

    # Citation 사용 여부
    import re
    citation_pattern = r'\[(\d+)\]'
    citations_in_text = re.findall(citation_pattern, content)
    if citations_in_text:
        print(f"  - 본문 내 Citation: {len(set(citations_in_text))}개 사용")
    else:
        print(f"  - 본문 내 Citation: 없음")

    print("\n" + "=" * 80)
    print("분석 완료")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(analyze_recent_conversations())
