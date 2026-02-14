from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None

    return None


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

    return []


def _extract_query_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()

    if isinstance(payload, dict):
        for key in ("query", "prompt", "input"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _extract_metadata_value(metadata: dict[str, Any], key: str) -> Any:
    value = metadata.get(key)
    if value is not None:
        return value

    nested = metadata.get("langfuse_context")
    if isinstance(nested, dict):
        return nested.get(key)

    return None


def _normalize_trace_status(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip().lower()
    if status in {"error", "failed", "failure"}:
        return "error"
    if status in {"running", "processing", "pending"}:
        return "running"
    if status in {"success", "completed", "ok"}:
        return "completed"

    level = str(item.get("level") or "").strip().lower()
    if level in {"error", "fatal"}:
        return "error"

    output = item.get("output")
    if isinstance(output, dict) and output.get("error"):
        return "error"

    return "completed"


def _to_display_name(name: str) -> str:
    normalized = name.strip().lower()
    if normalized == "ai-chat-stream":
        return "실시간 채팅 응답"
    if normalized == "ai-chat":
        return "채팅 응답"
    return name.strip() or "알 수 없는 Trace"


def _extract_text_preview(payload: Any, max_len: int = 280) -> str:
    text = ""

    if isinstance(payload, str):
        text = payload.strip()
    elif isinstance(payload, dict):
        for key in (
            "query",
            "prompt",
            "input",
            "text",
            "content",
            "response",
            "output",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
    elif isinstance(payload, list):
        segments = [
            item.strip() for item in payload if isinstance(item, str) and item.strip()
        ]
        if segments:
            text = " ".join(segments)

    if len(text) > max_len:
        return f"{text[: max_len - 3]}..."
    return text


def _to_iso_datetime(value: Any) -> str | float | None:
    dt = _coerce_datetime(value)
    if dt is None:
        return value if isinstance(value, (str, int, float)) else None
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _to_trace_path(project_id: str, trace_id: str, html_path: str) -> str | None:
    if html_path.startswith("/"):
        return f"/langfuse{html_path}"
    if trace_id and project_id:
        return f"/langfuse/project/{project_id}/traces/{trace_id}"
    return None


def _to_session_path(project_id: str, session_id: str) -> str | None:
    if project_id and session_id:
        return f"/langfuse/project/{project_id}/sessions/{session_id}"
    return None


def _map_trace_item(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    trace_id = str(item.get("id") or item.get("traceId") or "")
    name = str(item.get("name") or "")
    status = _normalize_trace_status(item)

    session_id = str(item.get("sessionId") or item.get("session_id") or "")
    thread_id = str(_extract_metadata_value(metadata, "thread_id") or session_id or "")
    user_id = str(
        _extract_metadata_value(metadata, "user_id")
        or item.get("userId")
        or item.get("user_id")
        or ""
    )

    user_message_id = str(_extract_metadata_value(metadata, "user_message_id") or "")
    assistant_message_id = str(
        _extract_metadata_value(metadata, "assistant_message_id")
        or _extract_metadata_value(metadata, "message_id")
        or ""
    )

    input_payload = item.get("input")
    output_payload = item.get("output")
    input_mode = input_payload.get("mode") if isinstance(input_payload, dict) else None

    mode = str(
        _extract_metadata_value(metadata, "mode")
        or _extract_metadata_value(metadata, "mode_requested")
        or input_mode
        or ""
    )

    turn_index = _coerce_int(_extract_metadata_value(metadata, "turn_index"))
    query_preview = _extract_query_text(input_payload)
    if len(query_preview) > 120:
        query_preview = f"{query_preview[:117]}..."

    project_id = str(item.get("projectId") or "")
    html_path = str(item.get("htmlPath") or "")

    return {
        "trace_id": trace_id,
        "name": name or "unknown",
        "display_name": _to_display_name(name),
        "status": status,
        "latency_ms": _coerce_float(
            item.get("latency")
            or item.get("latencyMs")
            or item.get("duration")
            or item.get("durationMs")
        ),
        "cost_usd": _coerce_float(
            item.get("totalCost")
            or item.get("total_cost")
            or item.get("cost")
            or item.get("calculatedTotalCost")
        ),
        "created_at": _to_iso_datetime(
            item.get("timestamp")
            or item.get("createdAt")
            or item.get("startTime")
            or item.get("start_time")
        ),
        "project_id": project_id or None,
        "query_preview": query_preview or None,
        "mode": mode or None,
        "thread_id": thread_id or None,
        "session_id": session_id or None,
        "user_id": user_id or None,
        "message_id": assistant_message_id or None,
        "user_message_id": user_message_id or None,
        "turn_index": turn_index,
        "trace_path": _to_trace_path(project_id, trace_id, html_path),
        "session_path": _to_session_path(project_id, session_id),
        "input_preview": _extract_text_preview(input_payload, 320) or None,
        "output_preview": _extract_text_preview(output_payload, 320) or None,
    }


def _map_observation_item(item: dict[str, Any]) -> dict[str, Any]:
    level = str(item.get("level") or "").lower()
    status = "error" if level in {"error", "fatal"} else "completed"

    return {
        "observation_id": str(item.get("id") or ""),
        "trace_id": str(item.get("traceId") or item.get("trace_id") or ""),
        "parent_observation_id": str(
            item.get("parentObservationId") or item.get("parent_observation_id") or ""
        )
        or None,
        "name": str(item.get("name") or ""),
        "type": str(item.get("type") or "").lower() or None,
        "level": level or None,
        "status": status,
        "status_message": str(item.get("statusMessage") or "").strip() or None,
        "model": str(item.get("model") or item.get("providedModelName") or "").strip()
        or None,
        "start_time": _to_iso_datetime(item.get("startTime") or item.get("start_time")),
        "end_time": _to_iso_datetime(item.get("endTime") or item.get("end_time")),
        "latency_ms": _coerce_float(
            item.get("latency")
            or item.get("latencyMs")
            or item.get("duration")
            or item.get("durationMs")
        ),
        "cost_usd": _coerce_float(
            item.get("totalCost")
            or item.get("total_cost")
            or item.get("cost")
            or item.get("calculatedTotalCost")
        ),
        "input_preview": _extract_text_preview(item.get("input"), 240) or None,
        "output_preview": _extract_text_preview(item.get("output"), 240) or None,
    }


def _build_public_api_base(host: str) -> str:
    base = host.rstrip("/")
    if base.endswith("/api/public"):
        return base
    if base.endswith("/api"):
        return f"{base}/public"
    return f"{base}/api/public"


@dataclass(slots=True)
class LangfuseCredentials:
    host: str
    public_key: str
    secret_key: str


class LangfuseDashboardService:
    def __init__(self) -> None:
        self._timeout = 12.0

    def _get_credentials(self) -> LangfuseCredentials | None:
        if os.getenv("LANGFUSE_ENABLED", "true").lower() == "false":
            return None

        host = os.getenv("LANGFUSE_HOST", "").strip()
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        if not host or not public_key or not secret_key:
            return None

        return LangfuseCredentials(
            host=host,
            public_key=public_key,
            secret_key=secret_key,
        )

    async def _get_json(
        self,
        credentials: LangfuseCredentials,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        base = _build_public_api_base(credentials.host)
        url = f"{base}/{endpoint.lstrip('/')}"

        auth = (credentials.public_key, credentials.secret_key)
        async with httpx.AsyncClient(timeout=self._timeout, auth=auth) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def fetch_overview(self, hours: int = 24, limit: int = 100) -> dict[str, Any]:
        credentials = self._get_credentials()
        if credentials is None:
            return {
                "enabled": False,
                "configured": False,
                "host": None,
                "summary": {
                    "trace_count": 0,
                    "error_count": 0,
                    "avg_latency_ms": 0.0,
                    "total_cost_usd": 0.0,
                    "avg_score": 0.0,
                    "score_count": 0,
                },
                "trend": [],
                "errors": ["Langfuse 환경 변수가 설정되지 않았습니다."],
            }

        errors: list[str] = []
        traces: list[dict[str, Any]] = []
        scores: list[dict[str, Any]] = []
        safe_limit = max(10, min(limit, 100))

        try:
            payload = await self._get_json(
                credentials, "traces", params={"limit": safe_limit}
            )
            traces = _extract_items(payload)
        except httpx.HTTPStatusError as exc:
            errors.append(f"traces 조회 실패: {exc.response.status_code}")
        except Exception as exc:  # pragma: no cover
            errors.append(f"traces 조회 실패: {str(exc)}")

        try:
            payload = await self._get_json(
                credentials, "v2/scores", params={"limit": safe_limit}
            )
            scores = _extract_items(payload)
        except httpx.HTTPStatusError:
            try:
                payload = await self._get_json(
                    credentials, "scores", params={"limit": safe_limit}
                )
                scores = _extract_items(payload)
            except Exception as exc:  # pragma: no cover
                errors.append(f"scores 조회 실패: {str(exc)}")
        except Exception as exc:  # pragma: no cover
            errors.append(f"scores 조회 실패: {str(exc)}")

        now = datetime.now(tz=UTC)
        range_start = now - timedelta(hours=max(1, min(hours, 168)))

        trace_count = 0
        error_count = 0
        latency_values: list[float] = []
        cost_values: list[float] = []
        buckets: dict[str, dict[str, float]] = {}

        for item in traces:
            created_at = _coerce_datetime(
                item.get("timestamp")
                or item.get("createdAt")
                or item.get("startTime")
                or item.get("start_time")
            )
            if created_at is None or created_at < range_start:
                continue

            trace_count += 1
            status = str(item.get("status", "")).lower()
            if status in {"error", "failed"}:
                error_count += 1

            latency = _coerce_float(
                item.get("latency")
                or item.get("latencyMs")
                or item.get("duration")
                or item.get("durationMs")
            )
            cost = _coerce_float(
                item.get("totalCost")
                or item.get("total_cost")
                or item.get("cost")
                or item.get("calculatedTotalCost")
            )

            if latency > 0:
                latency_values.append(latency)
            if cost > 0:
                cost_values.append(cost)

            bucket_key = created_at.astimezone(UTC).strftime("%m-%d %H:00")
            bucket = buckets.setdefault(
                bucket_key,
                {
                    "request_count": 0.0,
                    "error_count": 0.0,
                    "latency_sum": 0.0,
                    "cost_sum": 0.0,
                },
            )
            bucket["request_count"] += 1
            bucket["error_count"] += 1 if status in {"error", "failed"} else 0
            bucket["latency_sum"] += latency
            bucket["cost_sum"] += cost

        score_values = [
            _coerce_float(
                item.get("value") or item.get("score") or item.get("numericValue"), 0.0
            )
            for item in scores
        ]
        score_values = [value for value in score_values if value > 0]

        trend: list[dict[str, Any]] = []
        for key in sorted(buckets.keys()):
            bucket = buckets[key]
            count = max(1.0, bucket["request_count"])
            trend.append(
                {
                    "bucket": key,
                    "request_count": int(bucket["request_count"]),
                    "error_count": int(bucket["error_count"]),
                    "avg_latency_ms": round(bucket["latency_sum"] / count, 2),
                    "cost_usd": round(bucket["cost_sum"], 6),
                }
            )

        return {
            "enabled": True,
            "configured": True,
            "host": credentials.host,
            "summary": {
                "trace_count": trace_count,
                "error_count": error_count,
                "avg_latency_ms": round(sum(latency_values) / len(latency_values), 2)
                if latency_values
                else 0.0,
                "total_cost_usd": round(sum(cost_values), 6),
                "avg_score": round(sum(score_values) / len(score_values), 4)
                if score_values
                else 0.0,
                "score_count": len(score_values),
            },
            "trend": trend,
            "errors": errors,
        }

    async def fetch_recent_traces(self, limit: int = 20) -> dict[str, Any]:
        credentials = self._get_credentials()
        if credentials is None:
            return {
                "enabled": False,
                "configured": False,
                "traces": [],
                "errors": ["Langfuse 환경 변수가 설정되지 않았습니다."],
            }

        try:
            safe_limit = max(1, min(limit, 100))
            payload = await self._get_json(
                credentials, "traces", params={"limit": safe_limit}
            )
            items = _extract_items(payload)
        except httpx.HTTPStatusError as exc:
            return {
                "enabled": True,
                "configured": True,
                "traces": [],
                "errors": [f"traces 조회 실패: {exc.response.status_code}"],
            }
        except Exception as exc:  # pragma: no cover
            return {
                "enabled": True,
                "configured": True,
                "traces": [],
                "errors": [f"traces 조회 실패: {str(exc)}"],
            }

        traces = [_map_trace_item(item) for item in items]

        return {
            "enabled": True,
            "configured": True,
            "traces": traces,
            "errors": [],
        }

    async def _fetch_session_traces(
        self,
        credentials: LangfuseCredentials,
        session_id: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        safe_limit = max(1, min(limit, 100))
        errors: list[str] = []
        items: list[dict[str, Any]] = []

        try:
            payload = await self._get_json(
                credentials,
                "traces",
                params={"sessionId": session_id, "limit": safe_limit},
            )
            items = _extract_items(payload)
        except httpx.HTTPStatusError as exc:
            errors.append(f"session traces 조회 실패: {exc.response.status_code}")
        except Exception as exc:  # pragma: no cover
            errors.append(f"session traces 조회 실패: {str(exc)}")

        if not items:
            try:
                payload = await self._get_json(
                    credentials,
                    "traces",
                    params={"session_id": session_id, "limit": safe_limit},
                )
                items = _extract_items(payload)
            except Exception:
                # session_id 파라미터는 환경/버전에 따라 미지원일 수 있어 무시
                pass

        traces = [_map_trace_item(item) for item in items]
        traces.sort(
            key=lambda trace: _coerce_datetime(trace.get("created_at"))
            or datetime.fromtimestamp(0, tz=UTC)
        )

        return traces, errors

    async def fetch_session_timeline(
        self,
        session_id: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        credentials = self._get_credentials()
        if credentials is None:
            return {
                "enabled": False,
                "configured": False,
                "session_id": session_id,
                "traces": [],
                "errors": ["Langfuse 환경 변수가 설정되지 않았습니다."],
            }

        if not session_id.strip():
            return {
                "enabled": True,
                "configured": True,
                "session_id": session_id,
                "traces": [],
                "errors": ["session_id 값이 비어 있습니다."],
            }

        traces, errors = await self._fetch_session_traces(
            credentials,
            session_id=session_id.strip(),
            limit=limit,
        )

        return {
            "enabled": True,
            "configured": True,
            "session_id": session_id.strip(),
            "traces": traces,
            "errors": errors,
        }

    async def fetch_trace_detail(self, trace_id: str) -> dict[str, Any]:
        credentials = self._get_credentials()
        if credentials is None:
            return {
                "enabled": False,
                "configured": False,
                "trace": None,
                "observations": [],
                "session": None,
                "errors": ["Langfuse 환경 변수가 설정되지 않았습니다."],
            }

        trace_key = trace_id.strip()
        if not trace_key:
            return {
                "enabled": True,
                "configured": True,
                "trace": None,
                "observations": [],
                "session": None,
                "errors": ["trace_id 값이 비어 있습니다."],
            }

        errors: list[str] = []

        try:
            payload = await self._get_json(credentials, f"traces/{trace_key}")
        except httpx.HTTPStatusError as exc:
            return {
                "enabled": True,
                "configured": True,
                "trace": None,
                "observations": [],
                "session": None,
                "errors": [f"trace 상세 조회 실패: {exc.response.status_code}"],
            }
        except Exception as exc:  # pragma: no cover
            return {
                "enabled": True,
                "configured": True,
                "trace": None,
                "observations": [],
                "session": None,
                "errors": [f"trace 상세 조회 실패: {str(exc)}"],
            }

        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            trace_item = payload["data"]
        elif isinstance(payload, dict):
            trace_item = payload
        else:
            trace_item = {}

        if not trace_item:
            return {
                "enabled": True,
                "configured": True,
                "trace": None,
                "observations": [],
                "session": None,
                "errors": ["trace 상세 조회 결과가 비어 있습니다."],
            }

        trace = _map_trace_item(trace_item)

        observation_items = trace_item.get("observations")
        observations = (
            [item for item in observation_items if isinstance(item, dict)]
            if isinstance(observation_items, list)
            else []
        )

        if not observations:
            try:
                payload = await self._get_json(
                    credentials,
                    "observations",
                    params={"traceId": trace_key, "limit": 200},
                )
                observations = _extract_items(payload)
            except httpx.HTTPStatusError as exc:
                errors.append(f"observations 조회 실패: {exc.response.status_code}")
            except Exception as exc:  # pragma: no cover
                errors.append(f"observations 조회 실패: {str(exc)}")

        mapped_observations = [_map_observation_item(item) for item in observations]
        mapped_observations.sort(
            key=lambda item: _coerce_datetime(item.get("start_time"))
            or datetime.fromtimestamp(0, tz=UTC)
        )

        session_data: dict[str, Any] | None = None
        session_id = trace.get("session_id") or trace.get("thread_id")
        if isinstance(session_id, str) and session_id:
            session_traces, session_errors = await self._fetch_session_traces(
                credentials,
                session_id=session_id,
                limit=50,
            )
            errors.extend(session_errors)
            session_data = {
                "session_id": session_id,
                "traces": session_traces,
                "trace_count": len(session_traces),
            }

        return {
            "enabled": True,
            "configured": True,
            "trace": trace,
            "observations": mapped_observations,
            "session": session_data,
            "errors": errors,
        }
