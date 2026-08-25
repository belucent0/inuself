#!/usr/bin/env python3
"""Exercise the live RoutingProfile capacity contract through AI Gateway."""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx


DEFAULT_OUT = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "benchmarks"
    / "routing-npu-gpu-overflow.md"
)
HOLDER_PROMPT = (
    "Print the integers from 1 through 100000, one integer per line, without "
    "commentary, abbreviation, grouping, or stopping early."
)
CODEX_HOLDER_PROMPT = (
    "This is a streaming capacity test. Start immediately and output the token "
    "HOLD exactly 3000 times, separated only by single spaces. Do not explain, "
    "refuse, summarize, use code, or stop early."
)
PROVIDER_LABELS = {
    "npu-chat": "NPU / FastFlowLM",
    "gpu-llm": "GPU / vLLM",
    "codex": "Remote / Codex",
}


class BenchmarkFailure(RuntimeError):
    """A live observation did not satisfy the routing contract."""


@dataclass
class StreamResult:
    scenario: str
    request: str
    started_at: str
    headers_at: str = ""
    first_chunk_at: str = ""
    done_at: str = ""
    status: int | None = None
    provider: str = ""
    model: str = ""
    reason: str = ""
    headers_ms: float | None = None
    ttfb_ms: float | None = None
    total_ms: float | None = None
    done: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return (
            self.status == 200
            and self.ttfb_ms is not None
            and self.done
            and not self.error
        )


@dataclass
class Report:
    gateway: str
    command: str
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat()
    )
    readiness: dict[str, Any] = field(default_factory=dict)
    results: dict[str, list[StreamResult]] = field(default_factory=dict)
    outcomes: dict[str, tuple[str, str]] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def add(self, result: StreamResult) -> None:
        self.results.setdefault(result.scenario, []).append(result)

    def pass_scenario(self, scenario: str, detail: str) -> None:
        self.outcomes[scenario] = ("PASS", detail)

    def fail_scenario(self, scenario: str, detail: str) -> None:
        self.outcomes[scenario] = ("FAIL", detail)
        self.failures.append(f"{scenario}: {detail}")


def _payload(
    prompt: str,
    *,
    model: str = "auto",
    reasoning: str = "none",
    execution_scope: str = "local_only",
    max_tokens: int = 64,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if model == "auto":
        body["routing"] = {
            "workload": "chat",
            "reasoning": reasoning,
            "execution_scope": execution_scope,
        }
    return body


def _capture_headers(result: StreamResult, response: httpx.Response) -> None:
    result.headers_at = datetime.now(timezone.utc).astimezone().isoformat()
    result.status = response.status_code
    result.provider = response.headers.get("X-Inference-Provider", "")
    result.model = response.headers.get("X-Inference-Model", "")
    result.reason = response.headers.get("X-Routing-Reason", "")


async def _stream_request(
    client: httpx.AsyncClient,
    gateway: str,
    scenario: str,
    request_name: str,
    body: dict[str, Any],
) -> StreamResult:
    result = StreamResult(
        scenario=scenario,
        request=request_name,
        started_at=datetime.now(timezone.utc).astimezone().isoformat(),
    )
    started = perf_counter()
    try:
        async with client.stream(
            "POST", f"{gateway}/v1/chat/completions", json=body
        ) as response:
            result.headers_ms = (perf_counter() - started) * 1000
            _capture_headers(result, response)
            if response.status_code != 200:
                result.error = (await response.aread()).decode(
                    response.encoding or "utf-8", errors="replace"
                )[:1000]
                result.total_ms = (perf_counter() - started) * 1000
                return result

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    result.done = True
                    result.done_at = datetime.now(timezone.utc).astimezone().isoformat()
                    break
                if data and result.ttfb_ms is None:
                    result.ttfb_ms = (perf_counter() - started) * 1000
                    result.first_chunk_at = (
                        datetime.now(timezone.utc).astimezone().isoformat()
                    )

            result.total_ms = (perf_counter() - started) * 1000
            if result.ttfb_ms is None:
                result.error = "stream had no non-empty SSE data chunk"
            elif not result.done:
                result.error = "stream ended without [DONE]"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # live diagnostics belong in the Markdown report
        result.error = f"{type(exc).__name__}: {exc}"
        result.total_ms = (perf_counter() - started) * 1000
    return result


class Holder:
    """An open streaming request that keeps one Provider lease occupied."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        gateway: str,
        scenario: str,
        request_name: str,
        body: dict[str, Any],
    ) -> None:
        self.client = client
        self.gateway = gateway
        self.body = body
        self.result = StreamResult(
            scenario=scenario,
            request=request_name,
            started_at=datetime.now(timezone.utc).astimezone().isoformat(),
        )
        self._context: Any = None
        self._response: httpx.Response | None = None
        self._lines: Any = None
        self._started = perf_counter()
        self._closed = False

    async def open(self, deadline: float) -> StreamResult:
        try:
            async with asyncio.timeout(deadline):
                self._context = self.client.stream(
                    "POST",
                    f"{self.gateway}/v1/chat/completions",
                    json=self.body,
                )
                self._response = await self._context.__aenter__()
                self.result.headers_ms = (perf_counter() - self._started) * 1000
                _capture_headers(self.result, self._response)
                if self._response.status_code != 200:
                    self.result.error = (await self._response.aread()).decode(
                        self._response.encoding or "utf-8", errors="replace"
                    )[:1000]
                    return self.result

                self._lines = self._response.aiter_lines()
                async for line in self._lines:
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        self.result.done = True
                        self.result.done_at = (
                            datetime.now(timezone.utc).astimezone().isoformat()
                        )
                        self.result.error = "holder reached [DONE] before saturation"
                        return self.result
                    if data:
                        self.result.ttfb_ms = (perf_counter() - self._started) * 1000
                        self.result.first_chunk_at = (
                            datetime.now(timezone.utc).astimezone().isoformat()
                        )
                        return self.result
                self.result.error = "holder stream ended before first SSE data chunk"
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as exc:
            self.result.error = f"{type(exc).__name__}: {exc}"
        finally:
            if self.result.error:
                await self.close()
        return self.result

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._context is not None:
                await self._context.__aexit__(None, None, None)
            elif self._response is not None:
                await self._response.aclose()
        finally:
            self.result.total_ms = (perf_counter() - self._started) * 1000


async def _readiness(
    client: httpx.AsyncClient, gateway: str
) -> tuple[int, dict[str, Any]]:
    response = await client.get(f"{gateway}/health/readiness")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BenchmarkFailure(
            f"readiness returned non-JSON HTTP {response.status_code}"
        ) from exc
    return response.status_code, payload


async def _wait_inflight(
    client: httpx.AsyncClient,
    gateway: str,
    provider: str,
    expected: int,
    timeout: float,
) -> dict[str, Any]:
    deadline = perf_counter() + timeout
    last: dict[str, Any] = {}
    while perf_counter() < deadline:
        _, snapshot = await _readiness(client, gateway)
        last = snapshot.get("providers", {}).get(provider, {})
        if last.get("inflight") == expected:
            return last
        await asyncio.sleep(0.1)
    raise BenchmarkFailure(
        f"{provider} inflight did not reach {expected}; last={last or 'missing'}"
    )


async def _close_holders(
    holders: list[Holder],
    client: httpx.AsyncClient,
    gateway: str,
    provider: str,
    timeout: float,
) -> None:
    await asyncio.gather(*(holder.close() for holder in holders))
    await _wait_inflight(client, gateway, provider, 0, timeout)


async def _open_holders(
    client: httpx.AsyncClient,
    report: Report,
    gateway: str,
    scenario: str,
    provider: str,
    count: int,
    body: dict[str, Any],
    holder_deadline: float,
) -> list[Holder]:
    diagnostics: list[str] = []
    for attempt in range(1, 4):
        holders = [
            Holder(
                client,
                gateway,
                scenario,
                f"holder-{index + 1}-attempt-{attempt}",
                body,
            )
            for index in range(count)
        ]
        try:
            opened = await asyncio.gather(
                *(holder.open(holder_deadline) for holder in holders)
            )
        except BaseException:
            await asyncio.shield(
                asyncio.gather(*(holder.close() for holder in holders))
            )
            raise
        for result in opened:
            report.add(result)
        errors = [result.error for result in opened if result.error]
        if not errors:
            try:
                await _wait_inflight(
                    client,
                    gateway,
                    provider,
                    count,
                    min(10.0, holder_deadline),
                )
                return holders
            except BenchmarkFailure as exc:
                diagnostics.append(f"attempt {attempt}: {exc}")
        else:
            diagnostics.append(f"attempt {attempt}: {'; '.join(errors)}")

        await _close_holders(
            holders,
            client,
            gateway,
            provider,
            min(10.0, holder_deadline),
        )

    raise BenchmarkFailure(
        f"could not saturate {provider} after 3 holder attempts: "
        + " | ".join(diagnostics)
    )


def _require_result(
    result: StreamResult,
    *,
    provider: str,
    reason: str | None = None,
) -> None:
    if not result.ok:
        raise BenchmarkFailure(
            f"{result.request} failed: status={result.status}, error={result.error}"
        )
    if result.provider != provider:
        raise BenchmarkFailure(
            f"{result.request} provider={result.provider!r}, expected={provider!r}"
        )
    if reason is not None and result.reason != reason:
        raise BenchmarkFailure(
            f"{result.request} reason={result.reason!r}, expected={reason!r}"
        )


async def _sequential(
    client: httpx.AsyncClient,
    report: Report,
    scenario: str,
    gateway: str,
    reasoning: str,
    provider: str,
) -> None:
    for index in range(10):
        result = await _stream_request(
            client,
            gateway,
            scenario,
            f"request-{index + 1}",
            _payload(
                f"Reply with only the word OK. Request {index + 1}.",
                reasoning=reasoning,
            ),
        )
        report.add(result)
        _require_result(result, provider=provider, reason="preferred")


async def _scenario_s3(
    client: httpx.AsyncClient,
    report: Report,
    gateway: str,
    holder_deadline: float,
) -> None:
    holders = await _open_holders(
        client,
        report,
        gateway,
        "S3",
        "npu-chat",
        1,
        _payload(
            HOLDER_PROMPT,
            reasoning="none",
            max_tokens=4096,
        ),
        holder_deadline,
    )
    try:
        _require_holder_provider(holders, "npu-chat")
        probes = await asyncio.gather(
            *(
                _stream_request(
                    client,
                    gateway,
                    "S3",
                    f"probe-{index + 1}",
                    _payload("Reply with only OK.", reasoning="none"),
                )
                for index in range(3)
            )
        )
        for result in probes:
            report.add(result)
            _require_result(
                result, provider="gpu-llm", reason="capacity-overflow"
            )
    finally:
        await _close_holders(
            holders, client, gateway, "npu-chat", min(10.0, holder_deadline)
        )


async def _scenario_s4(
    client: httpx.AsyncClient,
    report: Report,
    gateway: str,
) -> str:
    gate = asyncio.Event()

    async def run(index: int, reasoning: str) -> StreamResult:
        await gate.wait()
        return await _stream_request(
            client,
            gateway,
            "S4",
            f"{reasoning}-{index + 1}",
            _payload("Reply with only OK.", reasoning=reasoning),
        )

    tasks = [asyncio.create_task(run(index, "none")) for index in range(5)]
    tasks += [asyncio.create_task(run(index, "medium")) for index in range(3)]
    gate.set()
    results = await asyncio.gather(*tasks)
    for result in results:
        report.add(result)
        if not result.ok or (result.status or 0) >= 500:
            raise BenchmarkFailure(
                f"{result.request} failed: status={result.status}, error={result.error}"
            )

    ttfb = [result.ttfb_ms or 0 for result in results]
    total = [result.total_ms or 0 for result in results]
    return (
        f"8 concurrent requests, 5xx=0; TTFB p50={_percentile(ttfb, 50):.1f}ms "
        f"p95={_percentile(ttfb, 95):.1f}ms; total p50={_percentile(total, 50):.1f}ms "
        f"p95={_percentile(total, 95):.1f}ms"
    )


async def _scenario_s6(
    client: httpx.AsyncClient,
    report: Report,
    gateway: str,
    holder_deadline: float,
) -> None:
    holders = await _open_holders(
        client,
        report,
        gateway,
        "S6",
        "gpu-llm",
        4,
        _payload(
            HOLDER_PROMPT,
            reasoning="high",
            execution_scope="local_only",
            max_tokens=4096,
        ),
        holder_deadline,
    )
    try:
        _require_holder_provider(holders, "gpu-llm")
        probe = await _stream_request(
            client,
            gateway,
            "S6",
            "remote-overflow-probe",
            _payload(
                "Reply with only OK.",
                reasoning="high",
                execution_scope="remote_allowed",
            ),
        )
        report.add(probe)
        _require_result(probe, provider="codex", reason="capacity-overflow")
    finally:
        await _close_holders(
            holders, client, gateway, "gpu-llm", min(10.0, holder_deadline)
        )


async def _monitor_max_inflight(
    client: httpx.AsyncClient,
    gateway: str,
    provider: str,
    stop: asyncio.Event,
) -> int:
    maximum = 0
    while not stop.is_set():
        _, snapshot = await _readiness(client, gateway)
        maximum = max(
            maximum,
            int(snapshot.get("providers", {}).get(provider, {}).get("inflight", 0)),
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.05)
        except TimeoutError:
            pass
    return maximum


async def _scenario_s7(
    client: httpx.AsyncClient,
    report: Report,
    gateway: str,
    holder_deadline: float,
) -> int:
    holders = await _open_holders(
        client,
        report,
        gateway,
        "S7",
        "codex",
        2,
        _payload(CODEX_HOLDER_PROMPT, model="codex-low", max_tokens=4096),
        holder_deadline,
    )
    stop = asyncio.Event()
    monitor = asyncio.create_task(
        _monitor_max_inflight(client, gateway, "codex", stop)
    )
    third: asyncio.Task[StreamResult] | None = None
    try:
        _require_holder_provider(holders, "codex")
        third = asyncio.create_task(
            _stream_request(
                client,
                gateway,
                "S7",
                "third-explicit-codex",
                _payload("Reply with only OK.", model="codex-low"),
            )
        )
        done, _ = await asyncio.wait({third}, timeout=1.0)
        if done:
            result = third.result()
            report.add(result)
            raise BenchmarkFailure(
                "third explicit Codex request completed while both slots were occupied"
            )

        released = holders.pop()
        await released.close()
        result = await third
        third = None
        report.add(result)
        _require_result(result, provider="codex", reason="explicit-codex")
    finally:
        if third is not None:
            third.cancel()
            await asyncio.gather(third, return_exceptions=True)
        try:
            await _close_holders(
                holders, client, gateway, "codex", min(10.0, holder_deadline)
            )
        finally:
            stop.set()
            maximum = await monitor

    if maximum > 2:
        raise BenchmarkFailure(f"Codex inflight exceeded 2 (observed {maximum})")
    return maximum


def _require_holder_provider(holders: list[Holder], provider: str) -> None:
    for holder in holders:
        if holder.result.provider != provider:
            raise BenchmarkFailure(
                f"{holder.result.request} provider={holder.result.provider!r}, "
                f"expected={provider!r}"
            )


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = (len(ordered) - 1) * percentile / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


async def _run_scenario(
    report: Report,
    name: str,
    deadline: float,
    operation: Any,
    success_detail: str | Any,
) -> Any:
    try:
        async with asyncio.timeout(deadline):
            value = await operation
        detail = success_detail(value) if callable(success_detail) else success_detail
        report.pass_scenario(name, detail)
        return value
    except Exception as exc:
        report.fail_scenario(name, f"{type(exc).__name__}: {exc}")
        return None


def _validate_environment(status: int, snapshot: dict[str, Any]) -> None:
    if status != 200:
        raise BenchmarkFailure(
            f"readiness HTTP {status}: status={snapshot.get('status')!r}"
        )
    providers = snapshot.get("providers", {})
    expected = {"npu-chat": 1, "gpu-llm": 4, "codex": 2}
    for provider, capacity in expected.items():
        state = providers.get(provider)
        if not state:
            raise BenchmarkFailure(f"readiness is missing provider {provider}")
        if state.get("max_inflight") != capacity:
            raise BenchmarkFailure(
                f"{provider} max_inflight={state.get('max_inflight')}, expected={capacity}"
            )
        if state.get("health") != "healthy":
            raise BenchmarkFailure(
                f"{provider} health={state.get('health')!r}, expected='healthy'"
            )
    routes = snapshot.get("routes", {})
    for route in ("chat", "summary"):
        if routes.get(route) != "ready":
            raise BenchmarkFailure(
                f"route {route}={routes.get(route)!r}, expected='ready'"
            )


async def run(args: argparse.Namespace) -> Report:
    gateway = args.gateway.rstrip("/")
    command = " ".join(shlex.quote(arg) for arg in sys.argv)
    report = Report(gateway=gateway, command=command)
    headers = {}
    api_key = args.api_key or os.getenv("AI_GATEWAY_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = httpx.Timeout(
        connect=args.connect_timeout,
        read=args.read_timeout,
        write=args.read_timeout,
        pool=args.connect_timeout,
    )

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        try:
            status, snapshot = await _readiness(client, gateway)
            report.readiness = snapshot
            _validate_environment(status, snapshot)
        except Exception as exc:
            report.fail_scenario("Environment", f"{type(exc).__name__}: {exc}")
            return report

        await _run_scenario(
            report,
            "S1",
            args.scenario_deadline,
            _sequential(client, report, "S1", gateway, "none", "npu-chat"),
            "10 sequential none requests all used npu-chat",
        )
        await _run_scenario(
            report,
            "S2",
            args.scenario_deadline,
            _sequential(client, report, "S2", gateway, "medium", "gpu-llm"),
            "10 sequential medium requests all used gpu-llm",
        )
        await _run_scenario(
            report,
            "S3",
            args.scenario_deadline,
            _scenario_s3(client, report, gateway, args.holder_deadline),
            "NPU holder=1; three none probes overflowed to GPU; cancel restored NPU=0",
        )
        s4_detail = await _run_scenario(
            report,
            "S4",
            args.scenario_deadline,
            _scenario_s4(client, report, gateway),
            lambda value: value,
        )
        if s4_detail is None and "S4" not in report.outcomes:
            report.fail_scenario("S4", "scenario returned no aggregate")

        async def s5() -> None:
            result = await _stream_request(
                client,
                gateway,
                "S5",
                "high-local",
                _payload(
                    "Reply with only OK.",
                    reasoning="high",
                    execution_scope="local_only",
                ),
            )
            report.add(result)
            _require_result(result, provider="gpu-llm", reason="preferred")

        await _run_scenario(
            report,
            "S5",
            args.scenario_deadline,
            s5(),
            "high/local_only used GPU and never Codex",
        )
        await _run_scenario(
            report,
            "S6",
            args.scenario_deadline,
            _scenario_s6(client, report, gateway, args.holder_deadline),
            "GPU holders=4; high/remote_allowed overflowed to Codex; cancel restored GPU=0",
        )
        await _run_scenario(
            report,
            "S7",
            args.scenario_deadline,
            _scenario_s7(client, report, gateway, args.holder_deadline),
            lambda maximum: (
                f"Codex holders=2; third waited for release; max observed inflight={maximum}; "
                "cancel restored Codex=0"
            ),
        )

    return report


def _md(value: Any) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "\\|")


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def render(report: Report) -> str:
    providers = report.readiness.get("providers", {})
    lines = [
        "# Routing NPU/GPU/Codex capacity benchmark",
        "",
        f"- Date: `{report.started_at}`",
        f"- Gateway: `{report.gateway}`",
        f"- Command: `{report.command}`",
        "- Method: live streaming SSE; TTFB is the first non-empty `data:` chunk.",
        "",
        "## Runtime policy",
        "",
        "| Provider | Hardware | Health | Model | Capacity | Circuit open |",
        "|---|---|---:|---|---:|---:|",
    ]
    for provider in ("npu-chat", "gpu-llm", "codex"):
        state = providers.get(provider, {})
        lines.append(
            "| "
            + " | ".join(
                [
                    provider,
                    PROVIDER_LABELS.get(provider, provider),
                    _md(state.get("health")),
                    _md(state.get("model")),
                    _md(state.get("max_inflight")),
                    _md(state.get("circuit_open")),
                ]
            )
            + " |"
        )

    lines += [
        "",
        "## Scenario results",
        "",
        "| Scenario | Result | Observation |",
        "|---|---:|---|",
    ]
    order = ["Environment", "S1", "S2", "S3", "S4", "S5", "S6", "S7"]
    for scenario in order:
        if scenario not in report.outcomes:
            continue
        outcome, detail = report.outcomes[scenario]
        lines.append(f"| {scenario} | {outcome} | {_md(detail)} |")

    lines += ["", "## Raw streaming observations", ""]
    for scenario in ("S1", "S2", "S3", "S4", "S5", "S6", "S7"):
        results = report.results.get(scenario, [])
        if not results:
            continue
        lines += [
            f"### {scenario}",
            "",
            "| Request | Started | Headers at | First chunk at | DONE at | HTTP | Provider | Model | Reason | Headers ms | TTFB ms | Total ms | Error |",
            "|---|---|---|---|---|---:|---|---|---|---:|---:|---:|---|",
        ]
        for result in results:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(result.request),
                        _md(result.started_at),
                        _md(result.headers_at),
                        _md(result.first_chunk_at),
                        _md(result.done_at),
                        _md(result.status),
                        _md(result.provider),
                        _md(result.model),
                        _md(result.reason),
                        _ms(result.headers_ms),
                        _ms(result.ttfb_ms),
                        _ms(result.total_ms),
                        _md(result.error),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines += [
        "## Verdict",
        "",
        "PASS" if not report.failures else "FAIL",
        "",
    ]
    if report.failures:
        lines += ["Failures:", ""] + [f"- {failure}" for failure in report.failures]
        lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://localhost:4000")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--scenario-deadline", type=float, default=300.0)
    parser.add_argument("--holder-deadline", type=float, default=180.0)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=180.0)
    args = parser.parse_args()
    for name in (
        "scenario_deadline",
        "holder_deadline",
        "connect_timeout",
        "read_timeout",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


def main() -> int:
    args = parse_args()
    report = asyncio.run(run(args))
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(report), encoding="utf-8")
    print(f"Benchmark report: {output}")
    if report.failures:
        for failure in report.failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("PASS: all routing capacity scenarios matched the live contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
