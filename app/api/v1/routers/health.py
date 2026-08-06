"""Liveness and readiness.

These answer different questions and must never be conflated:

- ``/health/live``  — "is this process alive?" No external dependency checks.
  If it did depend on Postgres, a transient database blip would make
  Kubernetes kill and restart a perfectly healthy API process — which does
  nothing to fix the database and adds a needless restart storm on top.
  Checked rarely; failure restarts the pod.

- ``/health/ready`` — "should traffic be routed here right now?" Checks that
  every dependency this process actually needs is reachable. Checked
  frequently by the load balancer / kube-proxy; failure only removes the pod
  from rotation, it does not restart it.

At this milestone there is no ORM, cache client, or vector-store adapter yet
(those land in M1, M6, M7) — these are the minimal, direct connectivity pings
readiness needs. Real adapters, once they exist, reuse the same connection
pools rather than opening a new one per health check.
"""

import asyncio
from dataclasses import dataclass
from typing import Literal

import asyncpg
import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends, Response, status

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)

DependencyStatus = Literal["ok", "unreachable"]


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    postgres: DependencyStatus
    redis: DependencyStatus
    qdrant: DependencyStatus
    object_storage: DependencyStatus

    @property
    def is_ready(self) -> bool:
        return all(
            status_ == "ok"
            for status_ in (self.postgres, self.redis, self.qdrant, self.object_storage)
        )


async def _check_postgres(settings: Settings) -> DependencyStatus:
    try:
        conn = await asyncpg.connect(str(settings.database_url), timeout=2)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return "ok"
    except (OSError, asyncpg.PostgresError):
        logger.warning("readiness_check_failed", dependency="postgres")
        return "unreachable"


async def _check_redis(settings: Settings) -> DependencyStatus:
    client: redis.Redis = redis.from_url(str(settings.redis_url), socket_timeout=2)
    try:
        await client.ping()
        return "ok"
    except (OSError, redis.RedisError):
        logger.warning("readiness_check_failed", dependency="redis")
        return "unreachable"
    finally:
        await client.aclose()


async def _check_http_endpoint(url: str) -> DependencyStatus:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(url)
        response.raise_for_status()
        return "ok"
    except (httpx.HTTPError, OSError):
        return "unreachable"


async def _check_qdrant(settings: Settings) -> DependencyStatus:
    base = str(settings.qdrant_url).rstrip("/")
    status_ = await _check_http_endpoint(f"{base}/healthz")
    if status_ == "unreachable":
        logger.warning("readiness_check_failed", dependency="qdrant")
    return status_


async def _check_object_storage(settings: Settings) -> DependencyStatus:
    # pydantic's AnyUrl normalizes a bare host to a trailing-slash form
    # (http://minio:9000/), so an un-stripped f-string join here produces a
    # double slash. MinIO's router treats that as a distinct, invalid path
    # and returns 400 rather than serving /minio/health/live.
    base = str(settings.s3_endpoint_url).rstrip("/")
    status_ = await _check_http_endpoint(f"{base}/minio/health/live")
    if status_ == "unreachable":
        logger.warning("readiness_check_failed", dependency="object_storage")
    return status_


@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def readiness(
    response: Response, settings: Settings = Depends(get_settings)
) -> ReadinessReport:
    # Independent checks, run concurrently — a probe hit every few seconds by
    # Kubernetes must not accumulate four sequential timeouts into an 8s tail.
    postgres, redis_, qdrant, object_storage = await asyncio.gather(
        _check_postgres(settings),
        _check_redis(settings),
        _check_qdrant(settings),
        _check_object_storage(settings),
    )
    report = ReadinessReport(
        postgres=postgres, redis=redis_, qdrant=qdrant, object_storage=object_storage
    )
    response.status_code = (
        status.HTTP_200_OK if report.is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return report
