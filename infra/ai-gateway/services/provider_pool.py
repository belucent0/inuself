"""Policy-driven provider health, circuit, capacity, and admission control."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import httpx

logger = logging.getLogger(__name__)

_REASONING = {"none": 0, "low": 1, "medium": 2, "high": 3}
_SETTINGS = (
    "health_interval_seconds",
    "health_timeout_seconds",
    "first_output_timeout_seconds",
    "request_timeout_seconds",
    "failure_threshold",
    "circuit_cooldown_seconds",
    "admission_timeout_seconds",
)
_PROVIDER_KEYS = (
    "kind",
    "base_url_env",
    "base_url_default",
    "model_env",
    "model_default",
    "api_key_env",
    "scope",
    "max_inflight",
    "health_path",
    "workloads",
    "reasoning_min",
    "reasoning_max",
)


class ProviderPoolError(RuntimeError):
    """Base routing-pool error."""


class NoEligibleProvider(ProviderPoolError):
    """No configured provider can serve the requested profile."""


class NoHealthyProvider(ProviderPoolError):
    """All eligible providers are unhealthy or have an open circuit."""


class AdmissionTimeout(ProviderPoolError):
    """Healthy providers stayed at capacity until the admission deadline."""


@dataclass(frozen=True)
class PoolSettings:
    health_interval_seconds: float
    health_timeout_seconds: float
    first_output_timeout_seconds: float
    request_timeout_seconds: float
    failure_threshold: int
    circuit_cooldown_seconds: float
    admission_timeout_seconds: float


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    kind: str
    base_url: str
    model: str
    api_key: str
    scope: str
    max_inflight: int
    health_path: str | None
    workloads: tuple[str, ...]
    reasoning_min: str
    reasoning_max: str

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)


@dataclass
class ProviderState:
    health: str = "unknown"
    inflight: int = 0
    probe_failures: int = 0
    request_failures: int = 0
    circuit_open_until: float = 0.0
    half_open_probe: bool = False


class ProviderLease:
    """One idempotently releasable provider capacity slot."""

    def __init__(
        self,
        pool: "ProviderPool",
        spec: ProviderSpec,
        reason: str,
        half_open: bool = False,
    ) -> None:
        self.spec = spec
        self.reason = reason
        self._pool = pool
        self._half_open = half_open
        self._released = False

    async def release(self) -> None:
        if not self._released:
            self._released = True
            await self._pool._release(self)


class ProviderPool:
    """Single-process provider state owner. Run the gateway with one worker."""

    def __init__(
        self,
        policy_path: str | Path | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        policy = self._load_policy(policy_path)
        self.settings, self.specs, self.routes = self._parse_policy(policy)
        self.states = {
            name: ProviderState(
                health="healthy" if spec.enabled and spec.health_path is None else "unknown"
            )
            for name, spec in self.specs.items()
        }
        self._clock = clock
        self._condition = asyncio.Condition()
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._monitor_task: asyncio.Task | None = None

    @staticmethod
    def _policy_path(policy_path: str | Path | None) -> Path:
        if policy_path:
            candidates = [Path(policy_path)]
        elif os.getenv("ROUTING_POLICY_PATH"):
            candidates = [Path(os.environ["ROUTING_POLICY_PATH"])]
        else:
            candidates = [
                Path("/app/infra/shared/routing_policy.json"),
                Path(__file__).resolve().parents[2] / "shared" / "routing_policy.json",
            ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise RuntimeError(f"routing policy not found: {', '.join(map(str, candidates))}")

    @classmethod
    def _load_policy(cls, policy_path: str | Path | None) -> dict:
        path = cls._policy_path(policy_path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid routing policy {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("routing policy must be a JSON object")
        return value

    @classmethod
    def load_settings(cls, policy_path: str | Path | None = None) -> PoolSettings:
        """Load and validate policy settings without starting provider resources."""
        return cls._parse_policy(cls._load_policy(policy_path))[0]

    @staticmethod
    def _positive(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise RuntimeError(f"{label} must be positive")
        return float(value)

    @classmethod
    def _parse_policy(
        cls, policy: dict
    ) -> tuple[PoolSettings, dict[str, ProviderSpec], dict[str, tuple[str, ...]]]:
        missing = {"settings", "providers", "routes"} - policy.keys()
        if missing:
            raise RuntimeError(f"routing policy missing keys: {sorted(missing)}")
        unknown = policy.keys() - {"settings", "providers", "routes"}
        if unknown:
            raise RuntimeError(f"routing policy has unknown keys: {sorted(unknown)}")

        raw_settings = policy["settings"]
        if not isinstance(raw_settings, dict):
            raise RuntimeError("settings must be an object")
        absent = set(_SETTINGS) - raw_settings.keys()
        if absent:
            raise RuntimeError(f"settings missing keys: {sorted(absent)}")
        unknown = raw_settings.keys() - set(_SETTINGS)
        if unknown:
            raise RuntimeError(f"settings has unknown keys: {sorted(unknown)}")
        parsed = {
            key: cls._positive(raw_settings[key], f"settings.{key}") for key in _SETTINGS
        }
        if not float(parsed["failure_threshold"]).is_integer():
            raise RuntimeError("settings.failure_threshold must be an integer")
        parsed["failure_threshold"] = int(parsed["failure_threshold"])
        settings = PoolSettings(**parsed)

        raw_providers = policy["providers"]
        if not isinstance(raw_providers, dict) or not raw_providers:
            raise RuntimeError("providers must be a non-empty object")
        specs: dict[str, ProviderSpec] = {}
        for name, raw in raw_providers.items():
            if not isinstance(raw, dict):
                raise RuntimeError(f"provider {name} must be an object")
            absent = set(_PROVIDER_KEYS) - raw.keys()
            if absent:
                raise RuntimeError(f"provider {name} missing keys: {sorted(absent)}")
            unknown = raw.keys() - set(_PROVIDER_KEYS)
            if unknown:
                raise RuntimeError(f"provider {name} has unknown keys: {sorted(unknown)}")
            for key in ("kind", "base_url_env", "base_url_default", "model_env", "model_default", "api_key_env"):
                if not isinstance(raw[key], str):
                    raise RuntimeError(f"providers.{name}.{key} must be a string")
            max_inflight = cls._positive(raw["max_inflight"], f"providers.{name}.max_inflight")
            if not max_inflight.is_integer():
                raise RuntimeError(f"providers.{name}.max_inflight must be an integer")
            if raw["scope"] not in ("local", "remote"):
                raise RuntimeError(f"provider {name} has invalid scope")
            if raw["kind"] not in ("npu", "gpu", "remote"):
                raise RuntimeError(f"provider {name} has invalid kind")
            if raw["reasoning_min"] not in _REASONING or raw["reasoning_max"] not in _REASONING:
                raise RuntimeError(f"provider {name} has invalid reasoning range")
            if _REASONING[raw["reasoning_min"]] > _REASONING[raw["reasoning_max"]]:
                raise RuntimeError(f"provider {name} has reversed reasoning range")
            workloads = raw["workloads"]
            if not isinstance(workloads, list) or not workloads or not all(
                isinstance(item, str) and item for item in workloads
            ):
                raise RuntimeError(f"provider {name} workloads must be non-empty strings")
            health_path = raw["health_path"]
            if health_path is not None and not isinstance(health_path, str):
                raise RuntimeError(f"provider {name} health_path must be string or null")
            if isinstance(health_path, str) and not health_path.startswith("/"):
                raise RuntimeError(f"provider {name} health_path must start with /")

            base_url = os.getenv(raw["base_url_env"], raw["base_url_default"]).strip()
            model = os.getenv(raw["model_env"], raw["model_default"]).strip()
            api_key = os.getenv(raw["api_key_env"], "").strip() if raw["api_key_env"] else ""
            if not model:
                raise RuntimeError(f"provider {name} model must not be empty")
            specs[name] = ProviderSpec(
                name=name,
                kind=str(raw["kind"]),
                base_url=base_url,
                model=model,
                api_key=api_key,
                scope=raw["scope"],
                max_inflight=int(max_inflight),
                health_path=health_path,
                workloads=tuple(workloads),
                reasoning_min=raw["reasoning_min"],
                reasoning_max=raw["reasoning_max"],
            )

        raw_routes = policy["routes"]
        if not isinstance(raw_routes, dict) or not raw_routes:
            raise RuntimeError("routes must be a non-empty object")
        routes: dict[str, tuple[str, ...]] = {}
        for workload, names in raw_routes.items():
            if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
                raise RuntimeError(f"route {workload} must be a non-empty list")
            if len(names) != len(set(names)):
                raise RuntimeError(f"route {workload} contains duplicate providers")
            unknown = [name for name in names if name not in specs]
            if unknown:
                raise RuntimeError(f"route {workload} references unknown providers: {unknown}")
            invalid = [name for name in names if workload not in specs[name].workloads]
            if invalid:
                raise RuntimeError(f"route {workload} uses providers without workload support: {invalid}")
            routes[workload] = tuple(names)
        return settings, specs, routes

    async def start(self) -> None:
        probes = [
            self._probe(spec)
            for spec in self.specs.values()
            if spec.enabled and spec.health_path
        ]
        if probes:
            await asyncio.gather(*probes)
        self._monitor_task = asyncio.create_task(self._monitor(), name="provider-health-monitor")

    async def close(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        if self._owns_client:
            await self._client.aclose()

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(self.settings.health_interval_seconds)
            await asyncio.gather(
                *(
                    self._probe(spec)
                    for spec in self.specs.values()
                    if spec.enabled and spec.health_path
                )
            )

    async def _probe(self, spec: ProviderSpec) -> None:
        healthy = False
        try:
            response = await self._client.get(
                f"{spec.base_url.rstrip('/')}{spec.health_path}",
                timeout=self.settings.health_timeout_seconds,
            )
            if response.status_code == 200:
                payload = response.json()
                models = payload.get("data", []) if isinstance(payload, dict) else []
                healthy = isinstance(models, list) and any(
                    item.get("id") == spec.model
                    for item in models
                    if isinstance(item, dict)
                )
        except Exception:
            logger.warning("provider probe failed name=%s", spec.name, exc_info=True)

        async with self._condition:
            state = self.states[spec.name]
            before = state.health
            if healthy:
                state.health = "healthy"
                state.probe_failures = 0
            else:
                state.probe_failures += 1
                if before == "unknown" or state.probe_failures >= self.settings.failure_threshold:
                    state.health = "unhealthy"
            if state.health != before:
                self._condition.notify_all()

    def _eligible_specs(
        self,
        workload: str,
        reasoning: str,
        execution_scope: str,
        exclude: frozenset[str],
    ) -> list[ProviderSpec]:
        if workload not in self.routes or reasoning not in _REASONING:
            return []
        level = _REASONING[reasoning]
        return [
            spec
            for name in self.routes[workload]
            if name not in exclude
            and (spec := self.specs[name]).enabled
            and workload in spec.workloads
            and _REASONING[spec.reasoning_min] <= level <= _REASONING[spec.reasoning_max]
            and (spec.scope == "local" or execution_scope == "remote_allowed")
        ]

    def _circuit_available(self, state: ProviderState, now: float) -> tuple[bool, bool]:
        if not state.circuit_open_until:
            return True, False
        if state.circuit_open_until > now or state.half_open_probe:
            return False, False
        return True, True

    async def acquire(
        self,
        workload: str,
        reasoning: str,
        execution_scope: str,
        exclude: frozenset[str] = frozenset(),
    ) -> ProviderLease:
        specs = self._eligible_specs(workload, reasoning, execution_scope, exclude)
        if not specs:
            raise NoEligibleProvider(f"no provider for {workload}/{reasoning}/{execution_scope}")
        return await self._acquire_from(specs, "preferred")

    async def acquire_explicit(self, provider_name: str) -> ProviderLease:
        spec = self.specs.get(provider_name)
        if spec is None or not spec.enabled:
            raise NoEligibleProvider(f"provider {provider_name} is not enabled")
        return await self._acquire_from([spec], "explicit-codex")

    async def _acquire_from(
        self, specs: Iterable[ProviderSpec], preferred_reason: str
    ) -> ProviderLease:
        specs = list(specs)
        deadline = asyncio.get_running_loop().time() + self.settings.admission_timeout_seconds
        async with self._condition:
            while True:
                now = self._clock()
                healthy_exists = False
                preceding_unhealthy = False
                preceding_busy = False
                circuit_waits: list[float] = []
                for spec in specs:
                    state = self.states[spec.name]
                    circuit_available, half_open = self._circuit_available(state, now)
                    if state.health != "healthy" or not circuit_available:
                        if state.health == "healthy" and state.circuit_open_until > now:
                            circuit_waits.append(state.circuit_open_until - now)
                        preceding_unhealthy = True
                        continue
                    healthy_exists = True
                    if state.inflight >= spec.max_inflight:
                        preceding_busy = True
                        continue

                    reason = preferred_reason
                    if preferred_reason == "preferred":
                        if preceding_unhealthy:
                            reason = "unhealthy-fallback"
                        elif preceding_busy:
                            reason = "capacity-overflow"
                    state.inflight += 1
                    if half_open:
                        state.half_open_probe = True
                    logger.info(
                        "provider selected name=%s reason=%s inflight=%d/%d",
                        spec.name,
                        reason,
                        state.inflight,
                        spec.max_inflight,
                    )
                    return ProviderLease(self, spec, reason, half_open)

                if not healthy_exists:
                    raise NoHealthyProvider("all eligible providers are unhealthy")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise AdmissionTimeout("provider admission timed out")
                wait_seconds = min([remaining, *circuit_waits])
                try:
                    await asyncio.wait_for(self._condition.wait(), wait_seconds)
                except TimeoutError as exc:
                    if wait_seconds < remaining:
                        continue
                    raise AdmissionTimeout("provider admission timed out") from exc

    async def _release(self, lease: ProviderLease) -> None:
        async with self._condition:
            state = self.states[lease.spec.name]
            state.inflight = max(0, state.inflight - 1)
            if lease._half_open:
                state.half_open_probe = False
            logger.info(
                "provider released name=%s inflight=%d/%d",
                lease.spec.name,
                state.inflight,
                lease.spec.max_inflight,
            )
            self._condition.notify_all()

    async def record_success(self, provider_name: str) -> None:
        async with self._condition:
            state = self.states[provider_name]
            state.request_failures = 0
            state.circuit_open_until = 0.0
            state.half_open_probe = False
            if self.specs[provider_name].health_path is None:
                state.health = "healthy"
            self._condition.notify_all()

    async def record_failure(self, provider_name: str) -> None:
        async with self._condition:
            state = self.states[provider_name]
            state.request_failures += 1
            if state.request_failures >= self.settings.failure_threshold:
                state.circuit_open_until = self._clock() + self.settings.circuit_cooldown_seconds
                state.half_open_probe = False
            self._condition.notify_all()

    async def snapshot(self) -> dict[str, dict]:
        async with self._condition:
            now = self._clock()
            return {
                name: {
                    "health": state.health if spec.enabled else "disabled",
                    "inflight": state.inflight,
                    "max_inflight": spec.max_inflight,
                    "model": spec.model,
                    "circuit_open": not self._circuit_available(state, now)[0],
                    "circuit_cooldown_remaining": max(0.0, state.circuit_open_until - now),
                }
                for name, spec in self.specs.items()
                for state in (self.states[name],)
            }

    async def route_readiness(self, *, local_only: bool = True) -> dict[str, str]:
        async with self._condition:
            now = self._clock()
            return {
                workload: (
                    "ready"
                    if any(
                        self.specs[name].enabled
                        and (not local_only or self.specs[name].scope == "local")
                        and self.states[name].health == "healthy"
                        and self._circuit_available(self.states[name], now)[0]
                        for name in names
                    )
                    else "unavailable"
                )
                for workload, names in self.routes.items()
            }
