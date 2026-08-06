"""Settings must fail loudly, at construction, when required configuration is
missing or malformed — never lazily, at the first request that happens to
touch the missing value.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings, get_settings


def test_missing_required_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="jwt_secret_key"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_malformed_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-postgres-url")

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_valid_environment_constructs() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.environment is Environment.LOCAL
    assert settings.jwt_secret_key.get_secret_value() == "test-secret-key-not-for-production"


def test_settings_is_frozen() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


def test_get_settings_is_a_cached_singleton() -> None:
    assert get_settings() is get_settings()


def test_unrelated_env_vars_do_not_break_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """.env is shared with docker-compose's own interpolation vars
    (POSTGRES_USER, MINIO_ROOT_PASSWORD, ...), which this class does not and
    should not declare as fields. Their presence must never crash Settings —
    that is precisely the bug this test pins down: it broke `make check` the
    first time a real `.env` existed on disk, because extra="forbid" treated
    every compose-only key as an unrecognised field.
    """
    monkeypatch.setenv("POSTGRES_USER", "rag")
    monkeypatch.setenv("POSTGRES_PASSWORD", "irrelevant-to-this-process")
    monkeypatch.setenv("MINIO_ROOT_USER", "rag-admin")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert not hasattr(settings, "postgres_user")


def test_typo_in_a_required_field_name_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The typo class that actually matters — forgetting/misspelling a
    *required* field — is still caught: the correctly-named field is simply
    never set, and has no default to fall back on.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URLL", "postgresql://test:test@localhost:5432/test")

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)  # type: ignore[call-arg]
