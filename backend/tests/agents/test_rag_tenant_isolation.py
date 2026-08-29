from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.agents.nodes.rag_retriever import RAGRetrieverNode
from app.agents.state import AIMode
from app.agents.tools import content_context, rag_search
from app.repositories.content_repository import ContentRepository


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


def _assert_user_filter(statement, user_id):
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "content.user_id" in str(compiled)
    assert user_id in compiled.params.values()


@pytest.mark.asyncio
async def test_direct_context_is_scoped_to_owner_and_returns_owned_content(monkeypatch):
    user_id = uuid4()
    own_file_id = uuid4()
    own_content = SimpleNamespace(
        title="Owned title",
        summary_md="Owned summary",
        transcription_result=None,
    )
    own_file = SimpleNamespace(
        id=own_file_id,
        filename="owned.txt",
        content=own_content,
    )
    scalar_result = SimpleNamespace(all=lambda: [own_file])
    result = SimpleNamespace(scalars=lambda: scalar_result)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(
        content_context,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )

    context = await content_context.load_content_context(
        [own_file_id, uuid4()],
        user_id=user_id,
    )

    _assert_user_filter(session.execute.await_args.args[0], user_id)
    assert "Owned summary" in context


@pytest.mark.asyncio
async def test_keyword_search_is_scoped_to_owner_and_returns_owned_content():
    user_id = uuid4()
    own_file = SimpleNamespace(id=uuid4())
    row = SimpleNamespace(File=own_file, score=1.0)
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [row]))
    )

    results = await rag_search._keyword_search(
        session,
        "owned",
        user_id=user_id,
        limit=5,
        content_ids=None,
    )

    _assert_user_filter(session.execute.await_args.args[0], user_id)
    assert results == [(own_file, 1.0)]


@pytest.mark.asyncio
async def test_vector_search_is_scoped_to_owner_and_returns_owned_content():
    user_id = uuid4()
    own_content = SimpleNamespace(id=uuid4())
    db_result = SimpleNamespace(fetchall=lambda: [(own_content.id, 0.9)])
    session = SimpleNamespace(
        execute=AsyncMock(return_value=db_result),
        get=AsyncMock(return_value=own_content),
    )

    results = await ContentRepository(session).vector_search_contents(
        [0.1, 0.2],
        user_id=user_id,
    )

    statement, params = session.execute.await_args.args
    assert "content.user_id = :user_id" in str(statement)
    assert params["user_id"] == user_id
    assert results == [(own_content, 0.9)]


@pytest.mark.asyncio
async def test_search_scope_all_drops_only_content_ids_not_owner(monkeypatch):
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.agents.nodes.rag_retriever.search_internal_content",
        search,
    )
    user_id = str(uuid4())
    state = {
        "query": "find mine",
        "mode": AIMode.RAG,
        "metadata": {"search_scope": "all", "content_ids": [str(uuid4())]},
        "user_id": user_id,
        "thinking_steps": [],
        "search_results": [],
    }

    await RAGRetrieverNode(SimpleNamespace())(state)

    assert search.await_args.kwargs["content_ids"] is None
    assert search.await_args.kwargs["user_id"] == UUID(user_id)
