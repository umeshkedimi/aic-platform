"""Liveness must never depend on external state; readiness must."""

from fastapi.testclient import TestClient


def test_liveness_always_succeeds(client: TestClient) -> None:
    response = client.get("/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_reports_unavailable_dependencies(client: TestClient) -> None:
    """With no real Postgres/Redis/Qdrant/MinIO listening on the configured
    test URLs, readiness must report unreachable and return 503 — not raise,
    not hang, not report healthy.
    """
    response = client.get("/v1/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"postgres", "redis", "qdrant", "object_storage"}
    assert all(value == "unreachable" for value in body.values())


def test_correlation_id_is_echoed(client: TestClient) -> None:
    response = client.get("/v1/health/live", headers={"X-Correlation-ID": "test-fixed-id"})

    assert response.headers["X-Correlation-ID"] == "test-fixed-id"


def test_correlation_id_is_generated_when_absent(client: TestClient) -> None:
    response = client.get("/v1/health/live")

    assert "X-Correlation-ID" in response.headers
