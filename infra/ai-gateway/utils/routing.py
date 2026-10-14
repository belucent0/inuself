"""Validate the shared RoutingProfile for chat and media callers."""


_ROUTING_KEYS = {"workload", "reasoning", "execution_scope"}
_WORKLOADS = {"chat", "summary"}
_REASONING = {"auto", "none", "low", "medium", "high"}
_SCOPES = {"local_only", "remote_allowed"}


class RoutingValidationError(ValueError):
    pass


def has_routing(body: dict) -> bool:
    extra = body.get("extra_body")
    return "routing" in body or isinstance(extra, dict) and "routing" in extra


def _extract_routing(body: dict) -> dict[str, str]:
    if "routing" in body:
        raw = body["routing"]
    else:
        extra = body.get("extra_body")
        raw = extra.get("routing") if isinstance(extra, dict) and "routing" in extra else {}
    if not isinstance(raw, dict):
        raise RoutingValidationError("routing must be an object")
    unknown = set(raw) - _ROUTING_KEYS
    if unknown:
        raise RoutingValidationError("unknown routing fields")

    profile = {
        "workload": raw.get("workload", "chat"),
        "reasoning": raw.get("reasoning", "medium"),
        "execution_scope": raw.get("execution_scope", "local_only"),
    }
    if not isinstance(profile["workload"], str) or profile["workload"] not in _WORKLOADS:
        raise RoutingValidationError("routing.workload must be chat or summary")
    if not isinstance(profile["reasoning"], str) or profile["reasoning"] not in _REASONING:
        raise RoutingValidationError("routing.reasoning has an invalid value")
    if not isinstance(profile["execution_scope"], str) or profile["execution_scope"] not in _SCOPES:
        raise RoutingValidationError("routing.execution_scope has an invalid value")
    if profile["reasoning"] == "auto":
        profile["reasoning"] = "medium"
    return profile
