"""현재 날짜/시간 조회 도구.

LLM 프롬프트에 현재 날짜를 주입하여 최신 정보 필요 여부 판단 및
웹 검색 라우팅 품질을 향상시킵니다.

상용 AI 서비스(ChatGPT, Claude.ai, Perplexity)의 표준 패턴:
- 대화 시작 시 현재 날짜를 시스템 프롬프트에 주입
- 이를 통해 LLM이 학습 컷오프 이후의 정보가 필요한지 판단 가능
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def get_current_datetime(timezone: str = "Asia/Seoul") -> str:
    """현재 날짜와 시간을 반환합니다.

    Args:
        timezone: IANA 타임존 문자열 (기본: "Asia/Seoul")

    Returns:
        "2026년 02월 21일 (토요일)" 형식의 문자열
    """
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    weekday_names = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    weekday = weekday_names[now.weekday()]
    return now.strftime(f"%Y년 %m월 %d일 ({weekday})")
