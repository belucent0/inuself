import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import tests

# Backend와 저장소 루트의 동명 tests 패키지에서 E2E 클라이언트도 찾는다.
tests.__path__.append(str(Path(__file__).resolve().parents[2] / "tests"))

from scripts import run_eval as evaluation


def judge_body(content="SCORE: 0.5\nREASON: 적절한 답변", finish_reason="stop", **extra):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "model": "gemma4-a4b", **extra,
    }


def transport_client(monkeypatch, handler):
    client_type = httpx.AsyncClient
    clients = []

    def create():
        client = client_type(transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    monkeypatch.setattr(evaluation.httpx, "AsyncClient", create)
    monkeypatch.setattr(evaluation, "JUDGE_AI_GATEWAY_URL", "http://private-host.invalid")
    monkeypatch.setattr(evaluation, "JUDGE_AI_GATEWAY_API_KEY", "private-key")
    return clients


def test_ci_does_not_override_gateway_model_and_runs_contract_tests():
    root = Path(__file__).resolve().parents[2]
    assert "JUDGE_MODEL" not in vars(evaluation)
    assert "JUDGE_MODEL" not in (root / ".github/workflows/langfuse-eval-gate.yml").read_text(encoding="utf-8")
    assert "backend/tests/test_eval_judge_contract.py" in (root / ".github/workflows/release-readiness-gate.yml").read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
async def test_valid_judge_uses_gateway_contract_and_safe_metadata(monkeypatch, score):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "auto"
        assert payload["routing"] == {
            "workload": "chat", "reasoning": "none", "execution_scope": "local_only",
        }
        assert request.headers["Authorization"] == "Bearer private-key"
        return httpx.Response(200, json=judge_body(f"<think>private thought</think>\nSCORE: {score}\nREASON: 적절함"), headers={
            "X-Inference-Provider": "npu-chat", "X-Inference-Model": "gemma4-it:e4b",
        })

    monkeypatch.setenv("JUDGE_MODEL", "obsolete-model")
    clients = transport_client(monkeypatch, handler)
    assert await evaluation._llm_judge("query", "answer", []) == (
        score, "적절함", {"provider": "npu-chat", "model": "gemma4-it:e4b"},
    )
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "SCORE: 0.5", "REASON: 적절함", "SCORE: 0.5\nREASON:",
    "SCORE: 0.5\nREASON:   ", "SCORE: 0.5\nSCORE: 0.6\nREASON: 적절함",
    "SCORE: 0.5\nREASON: 적절함\nREASON: 중복", "SCORE: 0.5\nREASON: 중복 REASON: 값",
    "prefix\nSCORE: 0.5\nREASON: 적절함", "SCORE: 0.5\nREASON: 적절함\nextra",
    "SCORE: NaN\nREASON: 적절함", "SCORE: Inf\nREASON: 적절함", "SCORE: -Inf\nREASON: 적절함",
    "SCORE: 1.1\nREASON: 적절함", "SCORE: -0.1\nREASON: 적절함", "SCORE: nope\nREASON: 적절함", None,
])
async def test_malformed_judge_never_returns_model_output(monkeypatch, content):
    transport_client(monkeypatch, lambda _: httpx.Response(200, json=judge_body(content)))
    assert await evaluation._llm_judge("query", "answer", []) == (
        -1.0, "format_error", {"provider": "unknown", "model": "gemma4-a4b"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [judge_body(finish_reason="length"), {}, {"choices": []}, {"choices": [None]}])
async def test_truncated_or_invalid_response_is_format_error(monkeypatch, body):
    transport_client(monkeypatch, lambda _: httpx.Response(200, json=body))
    assert await evaluation._llm_judge("query", "answer", []) == (
        -1.0, "format_error", {"provider": "unknown", "model": body.get("model", "unknown")},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type, suffix", [
    ("invalid_request", " invalid_request"), ("private-key http://private-host.invalid", ""),
    ([], ""), (None, ""),
])
async def test_http_diagnostic_only_exposes_status_and_known_type(monkeypatch, error_type, suffix):
    transport_client(monkeypatch, lambda _: httpx.Response(400, json={
        "error": {"type": error_type, "message": "private-key http://private-host.invalid"},
    }))
    assert await evaluation._llm_judge("query", "answer", []) == (-1.0, f"http_error HTTP 400{suffix}", {})


@pytest.mark.asyncio
async def test_connection_diagnostic_does_not_leak_exception(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("private-key http://private-host.invalid", request=request)

    transport_client(monkeypatch, handler)
    assert await evaluation._llm_judge("query", "answer", []) == (-1.0, "request_error ConnectError", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connect", "body", "combined"])
async def test_one_deadline_covers_connection_and_response_body(monkeypatch, phase):
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.04 if phase == "combined" else 1)
            yield json.dumps(judge_body()).encode()

    async def handler(_):
        if phase in {"connect", "combined"}:
            await asyncio.sleep(0.04 if phase == "combined" else 1)
        return httpx.Response(200, stream=SlowBody())

    clients = transport_client(monkeypatch, handler)
    monkeypatch.setattr(evaluation, "JUDGE_TIMEOUT_SECONDS", 0.06 if phase == "combined" else 0.01)
    assert await evaluation._llm_judge("query", "answer", []) == (-1.0, "request_error TimeoutError", {})
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("header_model, body_model, expected", [
    ("https://private-host.invalid", "gemma4-a4b", {"provider": "unknown", "model": "gemma4-a4b"}),
    ("x" * 129, "file:///private", {"provider": "unknown", "model": "unknown"}),
])
async def test_metadata_rejects_urls_and_overlong_identifiers(monkeypatch, header_model, body_model, expected):
    transport_client(monkeypatch, lambda _: httpx.Response(200, json=judge_body(model=body_model), headers={
        "X-Inference-Provider": "http://private-host.invalid", "X-Inference-Model": header_model,
    }))
    assert (await evaluation._llm_judge("query", "answer", []))[2] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_json", [False, True])
async def test_format_error_retains_safe_header_metadata(monkeypatch, invalid_json):
    def handler(_):
        kwargs = {"content": b"not JSON private-key"} if invalid_json else {"json": judge_body("private-key bad format")}
        return httpx.Response(200, **kwargs, headers={
            "X-Inference-Provider": "npu-chat", "X-Inference-Model": "gemma4-it:e4b",
        })

    transport_client(monkeypatch, handler)
    assert await evaluation._llm_judge("query", "answer", []) == (
        -1.0, "format_error", {"provider": "npu-chat", "model": "gemma4-it:e4b"},
    )


@pytest.mark.asyncio
async def test_format_error_replaces_unsafe_identifiers_with_unknown(monkeypatch):
    transport_client(monkeypatch, lambda _: httpx.Response(200, json=judge_body(
        "private-key bad format", model="https://private-host.invalid/private-key",
    ), headers={"X-Inference-Provider": "file:///private-key", "X-Inference-Model": "https://private-host.invalid"}))
    assert await evaluation._llm_judge("query", "answer", []) == (
        -1.0, "format_error", {"provider": "unknown", "model": "unknown"},
    )


class FakeLangfuse:
    def __init__(self, items=(), metadata_error=False):
        self.items = items
        self.scores = []
        self.metadata_error = metadata_error

    def get_dataset(self, _):
        return SimpleNamespace(items=self.items)

    def trace(self, **_):
        def update(**fields):
            if self.metadata_error and "metadata" in fields:
                raise RuntimeError("private-key http://private-host.invalid")

        return SimpleNamespace(id="trace", update=update)

    def score(self, **fields):
        self.scores.append(fields)

    def flush(self):
        pass


class FakeClient:
    def __init__(self, failure=None):
        self.failure = failure
        self.cleaned = self.closed = False

    async def login(self, *_):
        if self.failure == "login":
            raise RuntimeError("login failure")

    async def create_thread(self, *_):
        if self.failure == "warmup":
            raise RuntimeError("warmup failure")
        return "thread", "message"

    async def add_message(self, *_):
        return "thread", "message"

    async def stream_response(self, *_):
        if self.failure == "turn":
            raise RuntimeError("turn failure")
        return SimpleNamespace(full_content="hello", mode_used="simple")

    async def cleanup_all(self):
        self.cleaned = True
        if self.failure == "cleanup":
            raise RuntimeError("cleanup failure")

    async def close(self):
        self.closed = True


def item(turn=1):
    return SimpleNamespace(
        input={"suite_id": "test", "turn": turn, "query": "query"},
        metadata={"case_id": f"test__turn{turn}"}, expected_output={}, link=lambda *_args, **_kwargs: None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcomes, expected", [
    ([(0.5, "ok", {})] * 4, ("ok", 3, 0, 0, 4)),
    ([(-1, "http_error HTTP 400 invalid_request", {})], ("error: http_error HTTP 400 invalid_request", 0, 0, 3, 1)),
    ([(0.5, "ok", {}), (-1, "format_error", {})], ("ok", 0, 1, 2, 2)),
    ([(0.5, "ok", {}), (0, "low quality", {}), (-1, "format_error", {})], ("ok", 1, 1, 1, 3)),
])
async def test_preflight_and_turn_failure_stop_remaining_judge_calls(monkeypatch, outcomes, expected):
    calls = []

    async def judge(*args):
        calls.append(args)
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(evaluation, "JUDGE_ENABLED", True)
    monkeypatch.setattr(evaluation, "_llm_judge", judge)
    result = await evaluation.run_suite(FakeLangfuse(), FakeClient(), [item(i) for i in range(1, 4)], "test")
    assert (result["judge_preflight"], result["judge_success"], result["judge_errors"], result["judge_not_run"], len(calls)) == expected
    assert result["total"] == result["judge_success"] + result["judge_errors"] + result["judge_not_run"] == 3
    assert result["passed"] == 3 and result["failed"] == 0
    assert calls[0] == ("대한민국의 수도는?", "대한민국의 수도는 서울입니다.", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled, items, order, preflight, total", [
    (False, [item()], None, "disabled", 1), (True, [], None, "no_targets", 0),
    (True, [item()], ["missing"], "no_targets", 0),
])
async def test_disabled_or_zero_targets_never_call_preflight(monkeypatch, enabled, items, order, preflight, total):
    async def judge(*_):
        pytest.fail("judge must not be called")

    monkeypatch.setattr(evaluation, "JUDGE_ENABLED", enabled)
    monkeypatch.setattr(evaluation, "_llm_judge", judge)
    result = await evaluation.run_suite(FakeLangfuse(), FakeClient(), items, "test", order)
    assert result["judge_preflight"] == preflight
    assert result["total"] == result["judge_not_run"] == total


@pytest.mark.asyncio
@pytest.mark.parametrize("preflight_fails", [False, True])
async def test_format_failure_log_retains_safe_metadata_and_rule_counts(monkeypatch, capsys, preflight_fails):
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        content = "private-key bad format" if preflight_fails or calls > 1 else "SCORE: 0.5\nREASON: 적절함"
        return httpx.Response(200, json=judge_body(content), headers={
            "X-Inference-Provider": "npu-chat", "X-Inference-Model": "gemma4-it:e4b",
        })

    transport_client(monkeypatch, handler)
    monkeypatch.setattr(evaluation, "JUDGE_ENABLED", True)
    result = await evaluation.run_suite(FakeLangfuse(), FakeClient(), [item(1), item(2)], "test")
    assert calls == (1 if preflight_fails else 2)
    assert result["judge_errors"] == (0 if preflight_fails else 1)
    assert result["passed"] == result["total"] == 2 and result["failed"] == 0
    assert result["judge_not_run"] + result["judge_errors"] == 2
    output = capsys.readouterr().out
    failure_line = next(line for line in output.splitlines() if "format_error" in line)
    assert "npu-chat" in failure_line and "gemma4-it:e4b" in failure_line
    assert "private-key" not in output and "private-host" not in output


@pytest.mark.asyncio
async def test_rule_failure_counts_judge_not_run(monkeypatch):
    async def judge(*_):
        return 0.5, "ok", {}

    monkeypatch.setattr(evaluation, "JUDGE_ENABLED", True)
    monkeypatch.setattr(evaluation, "_llm_judge", judge)
    result = await evaluation.run_suite(FakeLangfuse(), FakeClient("turn"), [item()], "test")
    assert result["failed"] == result["judge_not_run"] == result["total"] == 1


def setup_main(monkeypatch, tmp_path, client, langfuse):
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(json.dumps({"test_suites": [{"id": "test", "turns": [{"turn": 1, "assertions": {}}]}]}), encoding="utf-8")
    monkeypatch.setattr(evaluation, "FIXTURES_PATH", fixtures)
    monkeypatch.setattr(evaluation, "E2E_LOGIN_ID", "user")
    monkeypatch.setattr(evaluation, "E2E_PASSWORD", "password")
    monkeypatch.setattr(evaluation, "Langfuse", lambda **_: langfuse)
    monkeypatch.setattr(evaluation, "ChatClient", lambda **_: client)


@pytest.mark.asyncio
@pytest.mark.parametrize("judge_result", [(0.0, "low quality", {}), (-1.0, "format_error", {})])
@pytest.mark.parametrize("metadata_error", [False, True])
async def test_judge_score_or_failure_never_changes_ci_exit(monkeypatch, tmp_path, judge_result, metadata_error, capsys):
    async def judge(*_):
        return judge_result

    client = FakeClient()
    langfuse = FakeLangfuse([item()], metadata_error=metadata_error)
    setup_main(monkeypatch, tmp_path, client, langfuse)
    monkeypatch.setattr(evaluation, "JUDGE_ENABLED", True)
    monkeypatch.setattr(evaluation, "_llm_judge", judge)
    assert await evaluation.main() == 0
    assert client.cleaned and client.closed
    if judge_result[0] == 0 and not metadata_error:
        assert any(score["name"] == "quality_judge" and score["value"] == 0 for score in langfuse.scores)
    output = capsys.readouterr().out
    assert "private-key" not in output and "private-host" not in output
    assert "[Judge summary]" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["login", "warmup", "cleanup"])
async def test_main_closes_client_on_login_warmup_or_cleanup_failure(monkeypatch, tmp_path, failure):
    client = FakeClient(failure)
    setup_main(monkeypatch, tmp_path, client, FakeLangfuse([]))
    monkeypatch.setattr(evaluation, "JUDGE_ENABLED", False)
    if failure == "warmup":
        assert await evaluation.main() == 2
    else:
        with pytest.raises(RuntimeError):
            await evaluation.main()
    assert client.cleaned and client.closed
