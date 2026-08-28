from app.db.models import AiMessage


def test_active_assistant_partial_unique_index_matches_runtime_states():
    index = next(
        item
        for item in AiMessage.__table__.indexes
        if item.name == "uq_ai_message_active_assistant_per_thread"
    )
    predicate = str(index.dialect_options["postgresql"]["where"])

    assert index.unique is True
    assert [column.name for column in index.columns] == ["thread_id"]
    for status in ("queued", "analyzing", "searching", "thinking", "generating"):
        assert status in predicate
