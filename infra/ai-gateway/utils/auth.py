"""Authenticate trusted Gateway callers with the existing shared key."""

import hmac

from fastapi import HTTPException, Request

from config import GATEWAY_API_KEY


async def require_gateway_key(request: Request) -> None:
    configured_key = GATEWAY_API_KEY.strip()
    if not configured_key:
        raise HTTPException(503, "Gateway authentication is not configured")

    values = request.headers.getlist("authorization")
    scheme, _, token = values[0].partition(" ") if len(values) == 1 else ("", "", "")
    if (
        scheme.lower() != "bearer"
        or not token
        or not token.isascii()
        or any(character.isspace() for character in token)
        or not hmac.compare_digest(token.encode(), configured_key.encode())
    ):
        raise HTTPException(
            401,
            "Invalid Gateway credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
