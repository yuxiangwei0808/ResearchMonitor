from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.exc import DatabaseError

from research_monitor.service import DomainError


def test_runtime_database_error_is_sanitized_structured_503(
    client: TestClient,
    monkeypatch,
) -> None:
    raw_sql = "SELECT token FROM credentials WHERE path = '/home/private/monitor.db'"
    raw_path = "/home/private/monitor.db"
    raw_secret = "super-secret-token"

    def fail_with_database_error(*_args, **_kwargs):
        raise DatabaseError(
            raw_sql,
            {"token": raw_secret},
            RuntimeError(f"database disk image is malformed: {raw_path}"),
        )

    monkeypatch.setattr(client.app.state.service, "list_projects", fail_with_database_error)
    response = client.get("/api/v1/projects")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "database_unavailable",
            "message": (
                "The monitor database is unavailable. Stop Research Monitor with "
                "'research-monitor stop', restore a verified backup with "
                "'research-monitor backup restore <backup.db> --confirm', then restart."
            ),
        }
    }
    for private_value in (raw_sql, raw_path, raw_secret, "credentials"):
        assert private_value not in response.text

    def fail_with_domain_error(*_args, **_kwargs):
        raise DomainError(409, "domain_conflict", "Domain errors remain specific")

    monkeypatch.setattr(client.app.state.service, "list_projects", fail_with_domain_error)
    domain_response = client.get("/api/v1/projects")
    assert domain_response.status_code == 409
    assert domain_response.json()["detail"] == {
        "code": "domain_conflict",
        "message": "Domain errors remain specific",
    }

    validation_response = client.post("/api/v1/projects", json={})
    assert validation_response.status_code == 422
    assert validation_response.json()["detail"]["code"] == "invalid_request"
