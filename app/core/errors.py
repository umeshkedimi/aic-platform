"""Domain error -> HTTP response mapping.

The one place allowed to know that ``NotFoundError`` means ``404``. Registered
once on the app in ``app.main``; routers never catch domain errors themselves.
"""

from typing import Final

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.core.logging import get_correlation_id, get_logger
from app.domain.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
    ValidationError,
)

logger = get_logger(__name__)

_STATUS_BY_ERROR: Final[dict[type[DomainError], int]] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    ValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    UnauthenticatedError: status.HTTP_401_UNAUTHORIZED,
}


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, DomainError):
        raise TypeError(f"domain_error_handler received non-domain error: {type(exc)}")

    http_status = _STATUS_BY_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)

    if http_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error("unhandled_domain_error", error=str(exc), path=request.url.path)

    return JSONResponse(
        status_code=http_status,
        content={
            "error": type(exc).__name__,
            "detail": str(exc),
            "correlation_id": get_correlation_id(),
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: never leak an internal exception message to a client.

    Full detail goes to structured logs, keyed by correlation id, so it is
    diagnosable without ever appearing in an HTTP response body.
    """
    logger.error(
        "unhandled_exception",
        error=str(exc),
        error_type=type(exc).__name__,
        path=request.url.path,
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "detail": "An unexpected error occurred.",
            "correlation_id": get_correlation_id(),
        },
    )
