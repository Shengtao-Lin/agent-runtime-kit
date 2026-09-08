"""Optional FastAPI service exposing the canonical runtime contract."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol, cast
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Lifespan

from agent_runtime.errors import (
    AgentInvocationError,
    IdempotencyConflictError,
    IdempotentRunUnavailableError,
    InvocationTimeoutError,
    ProviderError,
    RunInProgressError,
    RuntimeKitError,
    ThreadNotFoundError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ToolValidationError,
    UnknownAgentError,
)
from agent_runtime.feedback.base import FeedbackStore
from agent_runtime.feedback.models import FeedbackRecord, FeedbackSubmission
from agent_runtime.hooks import GuardrailBlockedError
from agent_runtime.models import RuntimeRequest, RuntimeResponse
from agent_runtime.registry import AgentRegistry
from agent_runtime.runtime import AgentRuntime
from agent_runtime.service.models import (
    AgentListResponse,
    ErrorDetail,
    ErrorEnvelope,
    HealthResponse,
    ReadinessResponse,
)

LOGGER = logging.getLogger("agent_runtime.service")


class ReadinessStore(Protocol):
    """Minimal database checks required by the HTTP readiness endpoint."""

    async def check_ready(self) -> bool: ...

    async def check_migrations(self, expected_revision: str) -> bool: ...


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies before endpoint validation."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_bytes:
                    return _error_response(
                        uuid4(), "request_too_large", "Request body is too large", False, 413
                    )
            except ValueError:
                return _error_response(
                    uuid4(), "invalid_content_length", "Invalid Content-Length header", False, 400
                )
        body = await request.body()
        if len(body) > self._max_bytes:
            return _error_response(
                uuid4(), "request_too_large", "Request body is too large", False, 413
            )
        return await call_next(request)


def create_app(
    *,
    runtime: AgentRuntime,
    agents: AgentRegistry,
    feedback: FeedbackStore,
    readiness: ReadinessStore,
    max_request_body_bytes: int = 1_000_000,
    expected_migration_revision: str = "0001",
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    """Build the optional framework-neutral HTTP application."""
    app = FastAPI(title="Agent Runtime Kit", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware, max_bytes=max_request_body_bytes)

    @app.exception_handler(RuntimeKitError)
    async def runtime_error_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: RuntimeKitError
    ) -> JSONResponse:
        request_id = _request_id(request)
        status_code = _status_code(exc)
        LOGGER.info(
            "runtime_request_failed",
            extra={"request_id": str(request_id), "error_code": exc.code},
        )
        return _error_response(
            request_id,
            exc.code,
            exc.public_message,
            exc.retryable,
            status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        raw_body: object = exc.body
        if isinstance(raw_body, dict):
            candidate = cast(dict[str, object], raw_body).get("request_id")
            try:
                request.state.request_id = UUID(str(candidate))
            except (TypeError, ValueError):
                pass
        return _error_response(
            _request_id(request),
            "validation_error",
            "Request validation failed",
            False,
            422,
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(  # pyright: ignore[reportUnusedFunction]
        request: Request, exc: Exception
    ) -> JSONResponse:
        LOGGER.exception("unhandled_runtime_error", exc_info=exc)
        return _error_response(
            _request_id(request),
            "internal_error",
            "An internal error occurred",
            False,
            500,
        )

    @app.get("/healthz", response_model=HealthResponse)
    async def health() -> HealthResponse:  # pyright: ignore[reportUnusedFunction]
        return HealthResponse()

    @app.get("/readyz", response_model=ReadinessResponse)
    async def ready() -> Response:  # pyright: ignore[reportUnusedFunction]
        database_ready = await readiness.check_ready()
        migrations_ready = database_ready and await readiness.check_migrations(
            expected_migration_revision
        )
        response = ReadinessResponse(status="ready" if migrations_ready else "not_ready")
        return JSONResponse(
            status_code=200 if migrations_ready else 503,
            content=response.model_dump(mode="json"),
        )

    @app.get("/v1/agents", response_model=AgentListResponse)
    async def list_agents() -> AgentListResponse:  # pyright: ignore[reportUnusedFunction]
        return AgentListResponse(agents=list(agents.list_descriptors()))

    @app.post(
        "/v1/agents/{agent_id}/invoke",
        response_model=RuntimeResponse,
        responses={409: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
    )
    async def invoke_agent(  # pyright: ignore[reportUnusedFunction]
        agent_id: str,
        body: RuntimeRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> RuntimeResponse:
        request.state.request_id = body.request_id
        response = await runtime.invoke(agent_id, body, idempotency_key=idempotency_key)
        LOGGER.info(
            "agent_invocation_succeeded",
            extra={
                "request_id": str(response.request_id),
                "run_id": str(response.run_id),
                "thread_id": str(response.thread_id),
                "agent_id": response.agent.agent_id,
            },
        )
        return response

    @app.post(
        "/v1/agents/{agent_id}/invoke/stream",
        response_class=StreamingResponse,
        responses={409: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
    )
    async def stream_agent(  # pyright: ignore[reportUnusedFunction]
        agent_id: str,
        body: RuntimeRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> StreamingResponse:
        request.state.request_id = body.request_id
        agents.get(agent_id)

        async def events() -> AsyncIterator[str]:
            try:
                async for event in runtime.stream(agent_id, body, idempotency_key=idempotency_key):
                    yield _sse(event.type, event.model_dump_json())
            except RuntimeKitError as exc:
                envelope = ErrorEnvelope(
                    request_id=body.request_id,
                    error=ErrorDetail(
                        code=exc.code,
                        message=exc.public_message,
                        retryable=exc.retryable,
                    ),
                )
                yield _sse("error", envelope.model_dump_json())
            except Exception as exc:
                LOGGER.exception("unhandled_stream_error", exc_info=exc)
                envelope = ErrorEnvelope(
                    request_id=body.request_id,
                    error=ErrorDetail(
                        code="internal_error",
                        message="An internal error occurred",
                        retryable=False,
                    ),
                )
                yield _sse("error", envelope.model_dump_json())

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/v1/feedback",
        response_model=FeedbackRecord,
        status_code=201,
        responses={409: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
    )
    async def record_feedback(  # pyright: ignore[reportUnusedFunction]
        body: FeedbackSubmission,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> FeedbackRecord:
        request.state.request_id = body.run_id
        if body.idempotency_key != idempotency_key:
            raise IdempotencyConflictError("Feedback body and header idempotency keys must match")
        record = await feedback.record(body)
        LOGGER.info(
            "agent_feedback_recorded",
            extra={
                "request_id": str(body.run_id),
                "run_id": str(body.run_id),
            },
        )
        return record

    return app


def _request_id(request: Request) -> UUID:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, UUID) else uuid4()


def _error_response(
    request_id: UUID,
    code: str,
    message: str,
    retryable: bool,
    status_code: int,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        request_id=request_id,
        error=ErrorDetail(code=code, message=message, retryable=retryable),
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def _status_code(exc: RuntimeKitError) -> int:
    if isinstance(exc, (UnknownAgentError, ThreadNotFoundError, ToolNotFoundError)):
        return 404
    if isinstance(
        exc, (IdempotencyConflictError, RunInProgressError, IdempotentRunUnavailableError)
    ):
        return 409
    if isinstance(exc, (GuardrailBlockedError, ToolValidationError)):
        return 422
    if isinstance(exc, (InvocationTimeoutError, ToolTimeoutError)):
        return 504
    if isinstance(exc, (ProviderError, ToolExecutionError, AgentInvocationError)):
        return 502
    return 400


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"
