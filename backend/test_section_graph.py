"""LangGraph Section Executor 테스트 스크립트.

새로운 LangGraph 기반 섹션 생성이 잘 작동하는지 테스트합니다.
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from app.services.section_executor import (
    SectionGraphExecutor,
    generate_detailed_sections_langgraph,
)


async def test_section_generation():
    """섹션 생성 테스트"""
    print("=" * 60)
    print("LangGraph Section Generation Test")
    print("=" * 60)

    # 테스트 데이터
    toc = [
        "사건 개요와 발생 배경",
        "취약점 발견과 영향 범위",
        "배포 과정에서의 문제점",
        "책임자 및 해결 노력",
        "향후 대응 방안",
    ]

    transcript = (
        """
    XG 라이브러리 백도어 사건은 2022년 안드레스 프레우든이 작성한 커밋으로 시작되었다.
    이 라이브러리는 많은 유틸리티에 기본 패키지로 포함되어 널리 사용되고 있었다.
    SSH 연동 등의 특징으로 인해 탐지가 어려웠으며, 5.6.0 버전 출시 시 발견되었다.
    이 사건은 오픈소스 생태계 내 취약한 메인테이너 구조를 드러냈다.
    """
        * 20
    )  # 길이 확보

    keywords = ["XG 라이브러리", "백도어", "오픈소스 보안", "공급망 공격", "취약점"]
    title = "XG 라이브러리 백도어 사건 분석"

    print(f"\n목차 주제: {len(toc)}개")
    for i, topic in enumerate(toc, 1):
        print(f"  {i}. {topic}")

    print(f"\n원본 텍스트 길이: {len(transcript)}자")
    print(f"키워드: {', '.join(keywords)}")

    try:
        # 새로운 LangGraph Executor로 섹션 생성
        print("\n" + "=" * 60)
        print("LangGraph Executor 실행 중...")
        print("=" * 60)

        executor = SectionGraphExecutor()
        sections, detailed_md, logs = await executor.generate_sections(
            toc=toc,
            transcript=transcript,
            keywords=keywords,
            title=title,
            max_retries=3,
        )

        print(f"\n✅ 성공! 생성된 섹션: {len(sections)}개")
        print(f"실패한 섹션: {len([t for t in toc if t not in sections])}개")

        print("\n" + "=" * 60)
        print("생성된 섹션 내용:")
        print("=" * 60)

        for i, (topic, content) in enumerate(sections.items(), 1):
            print(f"\n{i}. {topic}")
            print(f"   길이: {len(content)}자")
            print(f"   내용: {content[:100]}...")

        print("\n" + "=" * 60)
        print("실행 로그:")
        print("=" * 60)
        for log in logs[-10:]:  # 마지막 10개 로그
            print(f"  - {log}")

        # 상세 내용 마크다운 출력
        print("\n" + "=" * 60)
        print("최종 마크다운 (상세 내용):")
        print("=" * 60)
        print(detailed_md[:500] + "...")

        return True

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(test_section_generation())
    sys.exit(0 if result else 1)
