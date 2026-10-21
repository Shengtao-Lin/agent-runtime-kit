"""Bounded provider HTTP/SSE decoding; no retry or credential logging policy."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from agent_runtime.errors import ProviderError

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 256 * 1024


def request_failure(message: str, exc: Exception) -> ProviderError:
    """Classify transport/status failures without exposing response bodies or URLs."""
    retryable = isinstance(exc, httpx.RequestError)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retryable = status in (408, 409, 429) or 500 <= status < 600
    return ProviderError(message, retryable=retryable)


async def response_json(
    client: httpx.AsyncClient, path: str, *, payload: dict[str, object], headers: dict[str, str]
) -> object:
    """Read bounded decoded JSON and always close the response, including on failure."""
    async with client.stream("POST", path, json=payload, headers=headers) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ProviderError("Provider response exceeded the size limit", retryable=False)
            body.extend(chunk)
        return json.loads(body)


async def sse_data(response: httpx.Response) -> AsyncIterator[str]:
    """Decode bounded SSE data fields, including split UTF-8 and multiline events.

    A final event without its blank-line terminator is incomplete and is not dispatched.
    Bounds apply to decoded bytes, not just the possibly compressed Content-Length header.
    """
    buffer = bytearray()
    fields: list[str] = []
    event_bytes = 0
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ProviderError("Provider stream exceeded the size limit", retryable=False)
        buffer.extend(chunk)
        while b"\n" in buffer:
            raw_line, _, rest = buffer.partition(b"\n")
            buffer = bytearray(rest)
            event_bytes += len(raw_line) + 1
            if event_bytes > MAX_EVENT_BYTES:
                raise ProviderError(
                    "Provider stream event exceeded the size limit", retryable=False
                )
            line = raw_line.rstrip(b"\r").decode("utf-8")
            if not line:
                if fields:
                    yield "\n".join(fields)
                fields = []
                event_bytes = 0
            elif line.startswith("data:"):
                value = line[5:]
                fields.append(value[1:] if value.startswith(" ") else value)
        if event_bytes + len(buffer) > MAX_EVENT_BYTES:
            raise ProviderError("Provider stream event exceeded the size limit", retryable=False)
