"""프롬프트 모듈 - 용도별 프롬프트 관리.

각 용도에 맞는 프롬프트를 별도 파일로 관리합니다:
- summary.py: ASR/OCR 요약용
- search.py: AI 검색용 (Deep Search)
- chat.py: 일반 채팅용
"""

from .summary import (
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_PROMPT_TEMPLATE,
    MERGE_PROMPT_TEMPLATE,
)

from .search import (
    SEARCH_SYSTEM_PROMPT,
    SEARCH_USER_TEMPLATE,
)

from .chat import (
    CHAT_SYSTEM_PROMPT,
)

__all__ = [
    # Summary
    "SUMMARY_SYSTEM_PROMPT",
    "SUMMARY_PROMPT_TEMPLATE",
    "MERGE_PROMPT_TEMPLATE",
    # Search
    "SEARCH_SYSTEM_PROMPT",
    "SEARCH_USER_TEMPLATE",
    # Chat
    "CHAT_SYSTEM_PROMPT",
]
