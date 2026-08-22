from types import SimpleNamespace

from app.agents.graph import _recent_completed_history


def test_history_stops_at_the_queued_user_message():
    messages = [
        SimpleNamespace(message_id="old-user", content="old question", status="completed"),
        SimpleNamespace(message_id="old-ai", content="old answer", status="completed"),
        SimpleNamespace(message_id="current-user", content="current question", status="completed"),
        SimpleNamespace(message_id="later-ai", content="wrong answer", status="completed"),
    ]

    history = _recent_completed_history(messages, "current-user")

    assert [message.message_id for message in history] == ["old-user", "old-ai"]
