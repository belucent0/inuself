"""Bound and validate inference inputs before opening an upstream request."""

import base64
import binascii
import json
import math

from starlette.requests import ClientDisconnect


MAX_BODY_BYTES = 300 * 1024 * 1024
MAX_AUDIO_BYTES = 200 * 1024 * 1024
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_OUTPUT_TOKENS = 32768
MAX_JSON_DEPTH = 64
_ROLES = {"system", "developer", "user", "assistant", "tool", "function"}


class InputError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


async def read_body(request) -> dict:
    lengths = request.headers.getlist("content-length")
    if lengths:
        try:
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise ValueError
            declared = int(lengths[0])
        except ValueError:
            raise InputError("invalid Content-Length") from None
        if declared > MAX_BODY_BYTES:
            raise InputError("request body exceeds size limit", 413)
    buffer = bytearray()
    try:
        async for chunk in request.stream():
            if len(buffer) + len(chunk) > MAX_BODY_BYTES:
                raise InputError("request body exceeds size limit", 413)
            buffer.extend(chunk)
    except ClientDisconnect:
        raise InputError("request body was not completed") from None
    try:
        body = json.loads(buffer)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise InputError("request body must be valid JSON") from None
    if not isinstance(body, dict):
        raise InputError("request body must be a JSON object")
    stack = [(iter((body,)), 0)]
    while stack:
        iterator, depth = stack[-1]
        try:
            value = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        if isinstance(value, (dict, list)):
            if depth >= MAX_JSON_DEPTH:
                raise InputError("request JSON exceeds nesting limit")
            stack.append((iter(value.values() if isinstance(value, dict) else value), depth + 1))
    return body


def _decode_base64(value: str, limit: int, label: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise InputError(f"{label} base64 is required")
    if len(value) > 4 * ((limit + 2) // 3):
        raise InputError(f"{label} exceeds size limit", 413)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise InputError(f"{label} must be valid base64") from None
    if len(decoded) > limit:
        raise InputError(f"{label} exceeds size limit", 413)
    if not decoded:
        raise InputError(f"{label} base64 is required")
    return decoded


def decode_audio(extra: dict) -> bytes:
    return _decode_base64(extra.get("audio_base64"), MAX_AUDIO_BYTES, "audio")


def _validate_image_url(value: object, remaining: int) -> int:
    if not isinstance(value, dict) or not isinstance(value.get("url"), str):
        raise InputError("image_url must contain a URL string")
    header, _, encoded = value["url"].partition(",")
    if header not in ("data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64"):
        raise InputError("images must use inline JPEG, PNG, or WebP base64")
    decoded = _decode_base64(encoded, remaining, "images")
    valid = (
        header == "data:image/jpeg;base64" and decoded.startswith(b"\xff\xd8\xff")
        or header == "data:image/png;base64" and decoded.startswith(b"\x89PNG\r\n\x1a\n")
        or header == "data:image/webp;base64" and decoded[:4] == b"RIFF" and decoded[8:12] == b"WEBP"
    )
    if not valid:
        raise InputError("image signature does not match its media type")
    # shortcut: byte/signature checks do not bound decoded pixels; add decoder limits if image workloads grow.
    return len(decoded)


def validate_chat(body: dict) -> None:
    extra = body.get("extra_body", {})
    if not isinstance(extra, dict):
        raise InputError("extra_body must be an object")
    if "task_type" in extra and not isinstance(extra["task_type"], str):
        raise InputError("task_type must be a string")
    if "language" in extra and extra["language"] is not None and not isinstance(extra["language"], str):
        raise InputError("language must be a string or null")
    if "accuracy_mode" in extra and not isinstance(extra["accuracy_mode"], str):
        raise InputError("accuracy_mode must be a string")
    for field in ("min_speakers", "max_speakers"):
        value = extra.get(field)
        if value is not None and (type(value) is not int or value <= 0):
            raise InputError("speaker limits must be positive integers")
    if extra.get("min_speakers") is not None and extra.get("max_speakers") is not None and extra["min_speakers"] > extra["max_speakers"]:
        raise InputError("min_speakers must not exceed max_speakers")
    if "stream" in body and type(body["stream"]) is not bool:
        raise InputError("stream must be a boolean")
    if "max_tokens" in body and (type(body["max_tokens"]) is not int or not 1 <= body["max_tokens"] <= MAX_OUTPUT_TOKENS):
        raise InputError("max_tokens must be an integer within the output limit")
    if "temperature" in body:
        try:
            valid = type(body["temperature"]) in (int, float) and math.isfinite(body["temperature"])
        except OverflowError:
            valid = False
        if not valid:
            raise InputError("temperature must be a finite number")

    messages = body.get("messages", [])
    if not isinstance(messages, list):
        raise InputError("messages must be an array")
    if not messages and extra.get("task_type") not in ("asr", "diarization", "ocr"):
        raise InputError("messages must not be empty")
    text_chars = 0
    image_bytes = 0
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str) or message["role"] not in _ROLES:
            raise InputError("messages must contain objects with a valid role")
        content = message.get("content")
        if content is None and message["role"] == "assistant":
            continue
        if isinstance(content, str):
            text_chars += len(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    raise InputError("message content items must be objects")
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    text_chars += len(item["text"])
                elif item.get("type") == "image_url":
                    image_bytes += _validate_image_url(item.get("image_url"), MAX_IMAGE_BYTES - image_bytes)
                else:
                    raise InputError("message content must contain text or inline images")
        else:
            raise InputError("message content must be a string or an array")
        if text_chars > MAX_TEXT_CHARS:
            raise InputError("text exceeds size limit", 413)


def validate_embedding(body: dict) -> None:
    value = body.get("input", "")
    if isinstance(value, str):
        size = len(value)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        size = sum(len(item) for item in value)
    else:
        raise InputError("embedding input must be a string or an array of strings")
    if size > MAX_TEXT_CHARS:
        raise InputError("text exceeds size limit", 413)
    if size == 0:
        raise InputError("embedding input must not be empty")
    if "model" in body and not isinstance(body["model"], str):
        raise InputError("model must be a string")
    if "extra_body" in body and not isinstance(body["extra_body"], dict):
        raise InputError("extra_body must be an object")
