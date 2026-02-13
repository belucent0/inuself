"""PostgreSQL 대화 로더.

현재 AI 채팅 저장소(ai_thread / ai_message)에서 대화를 로드한다.
ORM 모델 import를 피하고 SQL text 쿼리로 동작하여
품질 테스트 스크립트 환경에서의 의존성(pgvector 등)을 최소화한다.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..core.interfaces import IConversationLoader, ConversationData


LIST_THREADS_SQL = text(
    """
    SELECT t.id::text AS thread_id
    FROM ai_thread t
    WHERE t.is_archived = false
      AND EXISTS (
          SELECT 1
          FROM ai_message m
          WHERE m.thread_id = t.id
      )
    ORDER BY COALESCE(t.updated_at, t.created_at) DESC
    """
)


LOAD_THREAD_SQL = text(
    """
    SELECT
      t.id::text AS thread_id,
      COALESCE(t.title, '새 대화') AS title,
      COALESCE(t.metadata_, '{}'::jsonb) AS metadata,
      EXTRACT(EPOCH FROM t.created_at) AS created_at_ts,
      EXTRACT(EPOCH FROM COALESCE(t.updated_at, t.created_at)) AS updated_at_ts
    FROM ai_thread t
    WHERE t.id = CAST(:thread_id AS uuid)
      AND t.is_archived = false
    LIMIT 1
    """
)


LOAD_MESSAGES_SQL = text(
    """
    SELECT
      m.role,
      m.content,
      COALESCE(m.metadata_, '{}'::jsonb) AS metadata,
      m.status,
      EXTRACT(EPOCH FROM m.created_at) AS timestamp
    FROM ai_message m
    WHERE m.thread_id = CAST(:thread_id AS uuid)
    ORDER BY m.created_at ASC
    """
)


class PostgresConversationLoader(IConversationLoader):
    """PostgreSQL에서 대화 데이터를 로드한다."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or _resolve_database_url()
        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            future=True,
            pool_pre_ping=True,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def _list_all_conversations_async(self) -> list[str]:
        async with self.session_factory() as session:
            result = await session.execute(LIST_THREADS_SQL)
            rows = result.fetchall()
            return [str(r.thread_id) for r in rows]

    async def _load_conversation_async(self, conversation_id: str) -> ConversationData:
        try:
            UUID(str(conversation_id))
        except ValueError:
            raise ValueError(f"Invalid thread id format: {conversation_id}")

        async with self.session_factory() as session:
            thread_result = await session.execute(
                LOAD_THREAD_SQL,
                {"thread_id": str(conversation_id)},
            )
            thread_row = thread_result.fetchone()
            if thread_row is None:
                raise ValueError(f"Thread {conversation_id} not found in PostgreSQL")

            message_result = await session.execute(
                LOAD_MESSAGES_SQL,
                {"thread_id": str(conversation_id)},
            )
            message_rows = message_result.fetchall()

            messages: list[dict[str, Any]] = []
            for row in message_rows:
                messages.append(
                    {
                        "role": row.role,
                        "content": row.content,
                        "metadata": row.metadata or {},
                        "timestamp": float(row.timestamp or 0.0),
                        "status": row.status,
                    }
                )

            return ConversationData(
                conversation_id=str(thread_row.thread_id),
                title=thread_row.title,
                messages=messages,
                created_at=float(thread_row.created_at_ts or 0.0),
                updated_at=float(thread_row.updated_at_ts or 0.0),
                metadata=thread_row.metadata or {},
            )

    async def _dispose_async(self) -> None:
        await self.engine.dispose()

    def list_all_conversations(self) -> list[str]:
        try:
            return asyncio.run(self._list_all_conversations_async())
        except Exception as e:
            print(f"⚠️  Warning: PostgreSQL error while listing conversations: {str(e)}")
            return []

    def load_conversation(self, conversation_id: str) -> ConversationData:
        try:
            return asyncio.run(self._load_conversation_async(conversation_id))
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(
                f"Failed to load conversation {conversation_id} from PostgreSQL: {str(e)}"
            )

    def load_conversations(self, conversation_ids: list[str]) -> list[ConversationData]:
        conversations: list[ConversationData] = []
        for conv_id in conversation_ids:
            try:
                conv = self.load_conversation(conv_id)
                conversations.append(conv)
            except ValueError as e:
                print(f"⚠️  Warning: {e}")
                continue
        return conversations

    def close(self) -> None:
        try:
            asyncio.run(self._dispose_async())
        except Exception:
            pass


def _resolve_database_url() -> str:
    """DATABASE_URL을 우선순위에 따라 해석한다.

    1) 현재 환경변수 DATABASE_URL
    2) 프로젝트 루트 .env의 DATABASE_URL
    3) 안전한 로컬 기본값
    """
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    parsed_url = _load_database_url_from_dotenv()
    if parsed_url:
        return parsed_url

    return "postgresql+asyncpg://asr:changeme@localhost:5432/asr"


def _load_database_url_from_dotenv() -> str | None:
    """프로젝트 루트 .env에서 DATABASE_URL을 읽는다."""
    # backend/app/quality_testing/loaders/postgres_loader.py -> 프로젝트 루트
    project_root = Path(__file__).resolve().parents[4]
    env_path = project_root / ".env"
    if not env_path.exists():
        return None

    raw_values: dict[str, str] = {}
    try:
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                raw_values[key] = value
    except Exception:
        return None

    database_url = raw_values.get("DATABASE_URL")
    if not database_url:
        return None

    # ${VAR} 치환 (1-pass)
    def replace_var(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return raw_values.get(var_name, os.getenv(var_name, ""))

    resolved = re.sub(r"\$\{([A-Z0-9_]+)\}", replace_var, database_url)
    return resolved or None
