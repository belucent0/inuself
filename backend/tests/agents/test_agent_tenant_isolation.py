from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.agents import graph as agent_graph
from app.agents.tools import content_context
from app.agents.tools.rag_search import _keyword_search
from app.controllers.ai_chat_controller import (
    AddMessageRequest,
    CreateThreadRequest,
    RegenerateRequest,
    add_message,
    create_thread,
    regenerate_response,
)
from app.repositories.content_repository import ContentRepository


class _Result:
    def __init__(self, values=()):
        self._values = list(values)

    def scalars(self):
        return self

    def all(self):
        return self._values

    def fetchall(self):
        return self._values


class _Session:
    def __init__(self, values=()):
        self.values = values
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append((statement, params))
        return _Result(self.values)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Graph:
    async def ainvoke(self, state, config=None):
        return state


def _foreign_context():
    return {"content_ids": [str(uuid4()), str(uuid4())]}


@pytest.mark.asyncio
async def test_create_rejects_foreign_content_before_persisting():
    service = SimpleNamespace(create_thread=AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await create_thread(
            CreateThreadRequest(query="question", context=_foreign_context()),
            svc=service,
            user_id=uuid4(),
            session=_Session(),
        )

    assert exc_info.value.status_code == 404
    service.create_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_message_rejects_foreign_content_before_persisting():
    service = SimpleNamespace(
        get_thread=AsyncMock(return_value=SimpleNamespace()),
        add_message=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await add_message(
            str(uuid4()),
            AddMessageRequest(query="question", context=_foreign_context()),
            svc=service,
            user_id=uuid4(),
            session=_Session(),
        )

    assert exc_info.value.status_code == 404
    service.add_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_regenerate_rejects_foreign_content_before_removing_response():
    service = SimpleNamespace(
        get_thread=AsyncMock(return_value=SimpleNamespace()),
        remove_last_assistant_message=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await regenerate_response(
            str(uuid4()),
            RegenerateRequest(context=_foreign_context()),
            svc=service,
            user_id=uuid4(),
            session=_Session(),
        )

    assert exc_info.value.status_code == 404
    service.remove_last_assistant_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyword_rag_query_is_scoped_to_current_user():
    session = _Session()
    user_id = uuid4()

    await _keyword_search(session, "needle", user_id, limit=5, content_ids=None)

    statement = str(session.statements[0][0])
    assert "content.user_id" in statement
    assert user_id in session.statements[0][0].compile().params.values()


@pytest.mark.asyncio
async def test_vector_rag_query_is_scoped_to_current_user():
    session = _Session()
    user_id = uuid4()

    await ContentRepository(session).vector_search_contents(
        [0.1, 0.2],
        user_id=user_id,
    )

    statement, params = session.statements[0]
    assert "content.user_id = :user_id" in str(statement)
    assert params["user_id"] == user_id


@pytest.mark.asyncio
async def test_summary_and_transcription_context_query_is_scoped_to_current_user(
    monkeypatch,
):
    session = _Session()
    user_id = uuid4()
    monkeypatch.setattr(content_context, "AsyncSessionLocal", lambda: session)

    assert await content_context.load_content_context(
        [uuid4()],
        user_id=user_id,
        source_options={"include_summary": True, "include_transcription": True},
    ) == ""

    statement = session.statements[0][0]
    assert "content.user_id" in str(statement)
    assert user_id in statement.compile().params.values()


@pytest.mark.asyncio
async def test_agent_history_lookup_is_scoped_to_current_user(monkeypatch):
    from app.db import session as db_session
    from app.services import thread_service

    user_id = str(uuid4())
    thread_id = str(uuid4())
    service = SimpleNamespace(get_thread=AsyncMock(return_value=None))
    monkeypatch.setattr(
        agent_graph,
        "create_ai_graph_with_retry",
        lambda *_args, **_kwargs: _Graph(),
    )
    monkeypatch.setattr(agent_graph, "get_langfuse_handler", lambda **_kwargs: None)
    monkeypatch.setattr(db_session, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(thread_service, "get_thread_service", lambda _session: service)

    await agent_graph.run_ai_agent(
        settings=SimpleNamespace(),
        query="question",
        thread_id=thread_id,
        user_id=user_id,
    )

    service.get_thread.assert_awaited_once_with(thread_id, user_id=user_id)


@pytest.mark.asyncio
async def test_create_hides_internal_failure_detail():
    service = SimpleNamespace(
        create_thread=AsyncMock(
            side_effect=RuntimeError("postgres://secret@internal/provider")
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_thread(
            CreateThreadRequest(query="question"),
            svc=service,
            user_id=uuid4(),
            session=_Session(),
        )

    assert exc_info.value.detail == "Thread creation failed"
    assert "secret" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_add_message_hides_internal_failure_detail():
    service = SimpleNamespace(
        get_thread=AsyncMock(return_value=SimpleNamespace()),
        add_message=AsyncMock(
            side_effect=RuntimeError("redis://secret@internal/provider")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await add_message(
            str(uuid4()),
            AddMessageRequest(query="question"),
            svc=service,
            user_id=uuid4(),
            session=_Session(),
        )

    assert exc_info.value.detail == "Message creation failed"
    assert "secret" not in exc_info.value.detail
