"""ASGI application factory.

Wiring only: this module reads settings, configures logging, registers
middleware/exception handlers/routers, and returns the app. It contains no
business logic — if a decision needs explaining beyond "which middleware runs
in which order", it belongs in the module it wires up, not here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware.correlation import CorrelationIdMiddleware
from app.api.v1.routers import health
from app.core.config import get_settings
from app.core.errors import domain_error_handler, unhandled_exception_handler
from app.core.logging import configure_logging, get_logger
from app.domain.errors import DomainError

settings = get_settings()
configure_logging(log_level=settings.log_level, json_logs=True)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("startup", environment=settings.environment.value)
    yield
    logger.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Enterprise RAG Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(CorrelationIdMiddleware)

    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health.router, prefix="/v1")

    return app


app = create_app()
