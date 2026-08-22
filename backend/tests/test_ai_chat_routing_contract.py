import pytest
from pydantic import ValidationError

from app.controllers.ai_chat_controller import (
    AddMessageRequest,
    CreateThreadRequest,
    RegenerateRequest,
    _build_agent_metadata,
)


REQUEST_MODELS = (RegenerateRequest, CreateThreadRequest, AddMessageRequest)


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
@pytest.mark.parametrize("reasoning", ["auto", "none", "low", "medium", "high"])
def test_request_models_accept_reasoning(request_model, reasoning):
    payload = {"reasoning": reasoning, "allow_remote": True}
    if request_model is not RegenerateRequest:
        payload["query"] = "question"
    request = request_model.model_validate(payload)
    assert request.reasoning == reasoning
    assert request.allow_remote is True


@pytest.mark.parametrize("request_model", REQUEST_MODELS)
@pytest.mark.parametrize(
    "extra",
    [{"reasoning": "extreme"}, {"model": "tier-" + "simple"}, {"unknown": True}],
)
def test_request_models_reject_invalid_or_legacy_fields(request_model, extra):
    payload = {"query": "question"} if request_model is not RegenerateRequest else {}
    payload.update(extra)
    with pytest.raises(ValidationError):
        request_model.model_validate(payload)


def test_agent_metadata_overwrites_untrusted_routing_context():
    context = {
        "allow_remote": True,
        "reasoning": "high",
        "model": "codex-high",
        "llm_model": "codex-low",
        "_agent_job": {"user_id": "foreign"},
        "_content_sequence": 999,
        "content_id": "content",
    }

    metadata = _build_agent_metadata(
        context=context,
        reasoning="none",
        allow_remote=False,
    )

    assert metadata["reasoning"] == "none"
    assert metadata["allow_remote"] is False
    assert metadata["content_ids"] == ["content"]
    assert "model" not in metadata
    assert "llm_model" not in metadata
    assert "_agent_job" not in metadata
    assert "_content_sequence" not in metadata
