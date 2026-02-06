"""SK하이닉스 대화 상세 분석 스크립트

검색 품질과 재시도 여부를 확인합니다.
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


async def analyze_sk_hynix_conversation():
    """SK하이닉스 대화 상세 분석"""
    print("=" * 80)
    print("SK하이닉스 대화 검색 품질 분석")
    print("=" * 80)

    service = get_conversation_service()

    # SK하이닉스 대화 찾기
    conversations = await service.list_conversations(limit=20)

    sk_conv = None
    for conv in conversations:
        title = conv.get('title', '').lower()
        if 'sk' in title or 'hynix' in title or '하이닉스' in title:
            sk_conv = conv
            break

    if not sk_conv:
        print("SK하이닉스 관련 대화를 찾을 수 없습니다.")
        return

    conv_id = sk_conv['conversation_id']
    print(f"\n대화 ID: {conv_id}")
    print(f"제목: {sk_conv['title']}")
    print(f"메시지 수: {sk_conv['message_count']}개")
    print(f"생성: {datetime.fromtimestamp(sk_conv['created_at']).strftime('%Y-%m-%d %H:%M:%S')}")

    # 상세 내용 가져오기
    conversation = await service.get_conversation(conv_id)

    if not conversation:
        print("대화를 불러올 수 없습니다.")
        return

    print("\n" + "=" * 80)
    print("메시지 분석")
    print("=" * 80)

    for idx, message in enumerate(conversation.messages, 1):
        role = message.role
        content = message.content
        metadata = message.metadata
        timestamp = datetime.fromtimestamp(message.timestamp)

        print(f"\n[메시지 {idx}] {role.upper()} - {timestamp.strftime('%H:%M:%S')}")
        print("-" * 80)

        if role == "user":
            print(f"질문: {content}")

        elif role == "assistant":
            print(f"응답 길이: {len(content)}자")
            print(f"응답 미리보기: {content[:200].replace(chr(10), ' ')}...")

            if metadata:
                print("\n### 메타데이터 ###")

                # V8.4 재시도 정보
                retry_count = metadata.get("search_retry_count", 0)
                quality_score = metadata.get("search_quality_score", 0)
                retry_reason = metadata.get("retry_reason", "")
                needs_retry = metadata.get("needs_retry", False)

                if retry_count > 0 or quality_score > 0:
                    print("\n[V8.4 재시도 정보]")
                    print(f"  재시도 횟수: {retry_count}")
                    print(f"  품질 점수: {quality_score:.1f}/100")
                    print(f"  재시도 필요: {needs_retry}")
                    print(f"  재시도 이유: {retry_reason if retry_reason else '없음'}")

                # Intent
                if 'intent' in metadata:
                    intent = metadata['intent']
                    print(f"\n[Intent 분석]")
                    if isinstance(intent, dict):
                        print(f"  의도: {intent.get('intent', 'N/A')}")
                        print(f"  모드: {intent.get('mode', 'N/A')}")

                # 검색 쿼리
                search_queries = metadata.get('search_queries', [])
                if search_queries:
                    print(f"\n[생성된 검색 쿼리] (총 {len(search_queries)}개)")
                    for i, q in enumerate(search_queries, 1):
                        print(f"  {i}. {q}")

                # 실패한 쿼리 (V8.4)
                failed_queries = metadata.get('failed_queries', [])
                if failed_queries:
                    print(f"\n[실패한 쿼리] (총 {len(failed_queries)}개)")
                    for i, fq in enumerate(failed_queries, 1):
                        print(f"  {i}. {fq}")

                # 원본 쿼리 (V8.4)
                original_queries = metadata.get('original_search_queries', [])
                if original_queries:
                    print(f"\n[원본 검색 쿼리]")
                    for i, oq in enumerate(original_queries, 1):
                        print(f"  {i}. {oq}")

                # 검색 결과
                search_results = metadata.get('search_results', [])
                if search_results:
                    print(f"\n[검색 결과] (총 {len(search_results)}개)")
                    for i, r in enumerate(search_results[:5], 1):
                        if isinstance(r, dict):
                            title = r.get('title', 'N/A')[:60]
                            quality = r.get('quality_score', 0)
                            url = r.get('url', 'N/A')[:50]
                            print(f"  {i}. [{quality:.1f}점] {title}")
                            print(f"     {url}")
                else:
                    print(f"\n[검색 결과] 없음")

                # Citations
                citations = metadata.get('citations', [])
                if citations:
                    print(f"\n[Citations] (총 {len(citations)}개)")
                    for cite in citations[:5]:
                        if isinstance(cite, dict):
                            cid = cite.get('id', '?')
                            title = cite.get('title', 'N/A')[:50]
                            verified = cite.get('verified', False)
                            print(f"  [{cid}] {title} ({'OK' if verified else 'Invalid'})")

                # Thinking steps
                thinking_steps = metadata.get('thinking_steps', [])
                if thinking_steps:
                    retry_steps = [
                        s for s in thinking_steps
                        if isinstance(s, dict) and ('rewrite' in s.get('step', '') or 'evaluation' in s.get('step', '') or 'fallback' in s.get('step', ''))
                    ]
                    if retry_steps:
                        print(f"\n[재시도 관련 사고 과정]")
                        for step in retry_steps:
                            content = step.get('content', '')
                            print(f"  - {content}")

    # 종합 평가
    print("\n" + "=" * 80)
    print("검색 품질 종합 평가")
    print("=" * 80)

    last_message = conversation.messages[-1] if conversation.messages else None
    if last_message and last_message.role == "assistant":
        metadata = last_message.metadata

        print("\n### 평가 항목 ###\n")

        # V8.4 재시도 기능 사용 여부
        retry_count = metadata.get("search_retry_count", 0)
        quality_score = metadata.get("search_quality_score", 0)

        if retry_count == 0 and quality_score == 0:
            print("❌ V8.4 재시도 기능이 사용되지 않음 (구버전 또는 비활성화)")
            print("   → enable_retry=False로 실행되었거나 V8.3 이하 버전")
        elif retry_count == 0 and quality_score > 0:
            print(f"✓ 첫 시도 성공 (품질: {quality_score:.1f}점)")
            print("   → 재시도 불필요")
        elif retry_count > 0 and quality_score >= 50:
            print(f"✓ {retry_count}회 재시도 후 성공 (품질: {quality_score:.1f}점)")
            print("   → 재시도 메커니즘이 효과적으로 작동")
        elif retry_count >= 3:
            print(f"⚠️ 최대 재시도 도달 (품질: {quality_score:.1f}점)")
            print("   → 폴백 처리 사용됨")

        # 검색 쿼리 품질
        search_queries = metadata.get('search_queries', [])
        print(f"\n[검색 쿼리 품질]")
        if search_queries:
            print(f"  생성된 쿼리: {len(search_queries)}개")
            for i, q in enumerate(search_queries[:3], 1):
                print(f"    {i}. {q}")
        else:
            print("  ❌ 검색 쿼리가 생성되지 않음")

        # 검색 결과 품질
        search_results = metadata.get('search_results', [])
        print(f"\n[검색 결과 품질]")
        if search_results:
            print(f"  결과 개수: {len(search_results)}개")
            scores = [r.get('quality_score', 0) for r in search_results if isinstance(r, dict) and 'quality_score' in r]
            if scores:
                avg = sum(scores) / len(scores)
                print(f"  평균 품질: {avg:.1f}점")
                print(f"  최고 품질: {max(scores):.1f}점")
                print(f"  최저 품질: {min(scores):.1f}점")
        else:
            print("  ❌ 검색 결과 없음")

        # 사용자 만족도 추정
        print(f"\n[추정 사용자 만족도]")
        if not search_results or len(search_results) < 3:
            print("  ⭐☆☆☆☆ 매우 낮음 (검색 결과 부족)")
        elif quality_score < 40:
            print("  ⭐⭐☆☆☆ 낮음 (품질 부족)")
        elif quality_score < 60:
            print("  ⭐⭐⭐☆☆ 보통")
        elif quality_score < 80:
            print("  ⭐⭐⭐⭐☆ 좋음")
        else:
            print("  ⭐⭐⭐⭐⭐ 매우 좋음")

    print("\n" + "=" * 80)
    print("분석 완료")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(analyze_sk_hynix_conversation())
