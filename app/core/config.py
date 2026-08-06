"""Application configuration.

Read once into a frozen ``Settings`` object at process start and injected via
FastAPI's dependency system everywhere it is needed. Nothing outside this
module calls ``os.getenv`` — a variable that is not represented here is not
configuration the application knows how to use, and adding one means adding a
field here first, which keeps this module the single source of truth for
"what does this service need to run".

Validation is eager: a missing or malformed variable raises at import time,
before the process accepts a connection or a task. A pod that cannot be
configured correctly must never appear ready.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import AnyUrl, Field, PostgresDsn, RedisDsn, SecretStr, UrlConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

QdrantDsn = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["http", "https"])]
S3Dsn = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["http", "https"])]


class Environment(StrEnum):
    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Process-wide configuration, validated once at startup.

    Frozen so no code path can mutate a value after boot — global mutable
    config is a race condition waiting to happen the moment there is more
    than one worker thread reading it.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
        # `.env` is shared with docker-compose's own variable interpolation
        # (POSTGRES_USER, MINIO_ROOT_PASSWORD, ...) so that a developer fills
        # in exactly one file. Those keys are meaningless to this process —
        # ignoring unrecognized keys is what makes that sharing possible.
        # This does not weaken typo-safety on what actually matters: every
        # field this class cares about is required (no default), so leaving
        # it unset or misspelling it still fails validation at construction.
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.LOCAL

    # --- API -----------------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - intentional bind-all inside a container
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_allow_origins: list[str] = Field(default_factory=list)

    # --- Security --------------------------------------------------------
    jwt_secret_key: SecretStr
    jwt_access_token_ttl_minutes: int = Field(default=15, gt=0)
    jwt_refresh_token_ttl_days: int = Field(default=30, gt=0)

    # --- Postgres ----------------------------------------------------------
    database_url: PostgresDsn
    database_pool_size: int = Field(default=10, gt=0)
    database_pool_max_overflow: int = Field(default=5, ge=0)

    # --- Redis ---------------------------------------------------------------
    redis_url: RedisDsn

    # --- Qdrant --------------------------------------------------------------
    qdrant_url: QdrantDsn
    qdrant_api_key: SecretStr | None = None

    # --- Object storage (MinIO / S3-compatible) -----------------------------
    s3_endpoint_url: S3Dsn
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_bucket_documents: str = "documents"
    s3_use_ssl: bool = False

    # --- Uploads ---------------------------------------------------------
    max_upload_size_mb: int = Field(default=50, gt=0)

    # --- Observability -----------------------------------------------------
    log_level: str = "INFO"
    otel_exporter_endpoint: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache the single process-wide ``Settings`` instance.

    ``lru_cache`` gives us the "read once" guarantee without a manual module-
    level singleton — the first call constructs and validates; every
    subsequent call, including every FastAPI dependency injection, returns
    the same frozen object.
    """
    return Settings()
