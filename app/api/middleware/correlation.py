"""Correlation-id middleware.

Every request is assigned a correlation id — reused from the caller's
``X-Correlation-ID`` header if present (so a gateway or an upstream service
can propagate its own trace id through us), generated fresh otherwise. It is
bound into the structlog context for the lifetime of the request and echoed
back in the response header, so a user-reported error can be traced from a
support ticket straight to its log lines.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import override

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_correlation_id

CORRELATION_ID_HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    @override
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER, str(uuid.uuid4()))
        bind_correlation_id(correlation_id)

        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
