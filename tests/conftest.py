"""Shared test fixtures.

Sets deterministic dummy configuration in the process environment at import
time — before pytest collects any test module — so that importing
``app.main`` (which builds a ``Settings`` instance at module load) never
depends on a developer's local ``.env`` or on CI secrets being present. Unit
tests never talk to real infrastructure; the values below are never used for
a real connection.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["QDRANT_URL"] = "http://localhost:6333"
os.environ["S3_ENDPOINT_URL"] = "http://localhost:9000"
os.environ["S3_ACCESS_KEY"] = "test-access-key"
os.environ["S3_SECRET_KEY"] = "test-secret-key"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Every test starts with a fresh ``Settings`` singleton.

    ``get_settings()`` is process-cached by design (read once, ADR-backed).
    Tests that mutate ``os.environ`` via ``monkeypatch`` need that cache
    cleared before and after, or they observe a stale ``Settings`` built by
    an earlier test.
    """
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
