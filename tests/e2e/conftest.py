"""E2E 테스트 pytest fixtures.

환경변수:
    E2E_BASE_URL   백엔드 URL (기본: http://localhost:8000)
    E2E_LOGIN_ID   테스트 계정 아이디
    E2E_PASSWORD   테스트 계정 비밀번호
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import pytest_asyncio

from .chat_client import ChatClient
from .models import MultiTurnFixtures

E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
E2E_LOGIN_ID = os.environ.get("E2E_LOGIN_ID", "")
E2E_PASSWORD = os.environ.get("E2E_PASSWORD", "")

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "chat_multiturn_cases.json"


@pytest_asyncio.fixture(scope="session")
async def chat_client():
    """세션 전체에서 재사용하는 인증된 ChatClient.

    - 세션 시작 시 로그인
    - 세션 종료 시 생성한 모든 스레드 삭제
    """
    if not E2E_LOGIN_ID or not E2E_PASSWORD:
        pytest.skip(
            "E2E_LOGIN_ID / E2E_PASSWORD 환경변수가 설정되지 않았습니다."
        )

    client = ChatClient(base_url=E2E_BASE_URL)
    await client.login(E2E_LOGIN_ID, E2E_PASSWORD)

    yield client

    await client.cleanup_all()
    await client.close()


@pytest.fixture(scope="session")
def multiturn_fixtures() -> MultiTurnFixtures:
    """chat_multiturn_cases.json 을 로드하여 Pydantic 모델로 반환합니다."""
    raw = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    return MultiTurnFixtures.model_validate(raw)
