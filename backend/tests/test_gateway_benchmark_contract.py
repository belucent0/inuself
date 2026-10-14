import asyncio
from types import SimpleNamespace

import pytest

from scripts import bench_routing as bench


@pytest.mark.asyncio
async def test_codex_capacity_scenario_sends_explicit_remote_scope(monkeypatch):
    bodies = []
    released = asyncio.Event()

    async def close():
        released.set()

    async def open_holders(*args):
        bodies.append(args[6])
        return [SimpleNamespace(close=close, result=SimpleNamespace(provider="codex")) for _ in range(2)]

    async def stream(*args):
        bodies.append(args[4])
        await released.wait()
        return bench.StreamResult("S7", "third", "now", status=200, provider="codex", reason="explicit-codex", ttfb_ms=1, done=True)

    async def monitor(_client, _gateway, _provider, stop):
        await stop.wait()
        return 2

    async def close_holders(holders, *_args):
        for holder in holders:
            await holder.close()

    monkeypatch.setattr(bench, "_open_holders", open_holders)
    monkeypatch.setattr(bench, "_stream_request", stream)
    monkeypatch.setattr(bench, "_monitor_max_inflight", monitor)
    monkeypatch.setattr(bench, "_close_holders", close_holders)
    report = bench.Report("http://gateway", "synthetic capacity test")
    assert await bench._scenario_s7(None, report, "http://gateway", 5) == 2
    assert len(bodies) == 2
    for body in bodies:
        assert body["model"] == "codex-low"
        assert body["routing"] == {
            "workload": "chat", "reasoning": "none", "execution_scope": "remote_allowed",
        }


def test_auto_benchmark_scope_stays_local_by_default():
    assert bench._payload("hello")["routing"]["execution_scope"] == "local_only"
