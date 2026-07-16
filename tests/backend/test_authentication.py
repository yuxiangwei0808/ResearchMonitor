from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from research_monitor.api import BrowserAuthState, create_app

from .conftest import enroll, mutate
from .test_api import op


DIRECT_NAVIGATION_HEADERS = {
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
}


def test_browser_bootstrap_is_one_time_and_uses_separate_cookie_security(settings, database):
    app = create_app(
        settings=settings,
        database=database,
        browser_bootstrap_token="known-capability",
    )

    with TestClient(app) as browser:
        root = browser.get("/")
        assert root.status_code == 200
        assert root.headers["cache-control"] == "no-store"
        assert browser.get("/api/v1").status_code == 401
        assert browser.get("/api/v1/projects").status_code == 401

        bootstrap = browser.get("/__bootstrap/known-capability", follow_redirects=False)
        assert bootstrap.status_code == 303
        assert bootstrap.headers["location"] == "/"

        cookie_headers = bootstrap.headers.get_list("set-cookie")
        session_header = next(
            value for value in cookie_headers if value.startswith("research_monitor_session=")
        )
        csrf_header = next(
            value for value in cookie_headers if value.startswith("research_monitor_csrf=")
        )
        assert "httponly" in session_header.lower()
        assert "samesite=strict" in session_header.lower()
        assert "httponly" not in csrf_header.lower()
        assert "samesite=strict" in csrf_header.lower()

        assert browser.get("/__bootstrap/known-capability", follow_redirects=False).status_code == 404

        browser.headers["Origin"] = "http://testserver"
        browser.headers["X-CSRF-Token"] = browser.cookies["research_monitor_csrf"]
        assert browser.get("/api/v1/projects").status_code == 200
        assert browser.post("/api/v1/backup", json={}).status_code == 200


def test_direct_navigation_creates_protected_browser_sessions(
    settings, database,
) -> None:
    app = create_app(
        settings=settings,
        database=database,
        browser_bootstrap_token="direct-launch-capability",
    )

    with TestClient(app) as browser:
        browser.cookies.set(
            "research_monitor_session", "stale-session",
            domain="testserver.local", path="/",
        )
        browser.cookies.set(
            "research_monitor_csrf", "stale-csrf",
            domain="testserver.local", path="/",
        )
        launched = browser.get(
            "/", headers=DIRECT_NAVIGATION_HEADERS, follow_redirects=False,
        )
        assert launched.status_code == 303
        assert launched.headers["location"] == "/"
        assert launched.headers["cache-control"] == "no-store"
        assert launched.headers["x-frame-options"] == "DENY"

        cookie_headers = launched.headers.get_list("set-cookie")
        session_header = next(
            value for value in cookie_headers
            if value.startswith("research_monitor_session=")
        )
        csrf_header = next(
            value for value in cookie_headers
            if value.startswith("research_monitor_csrf=")
        )
        assert "httponly" in session_header.casefold()
        assert "httponly" not in csrf_header.casefold()
        for header in cookie_headers:
            lowered = header.casefold()
            assert "samesite=strict" in lowered
            assert "path=/" in lowered
            assert "domain=" not in lowered
            assert "max-age=" not in lowered
            assert "expires=" not in lowered

        assert browser.cookies["research_monitor_session"] != "stale-session"
        assert browser.cookies["research_monitor_csrf"] != "stale-csrf"
        session_token = browser.cookies["research_monitor_session"]
        dashboard = browser.get("/")
        assert dashboard.status_code == 200
        assert dashboard.headers["cache-control"] == "no-store"
        assert browser.get("/api/v1/projects").status_code == 200

        already_authenticated = browser.get(
            "/", headers=DIRECT_NAVIGATION_HEADERS, follow_redirects=False,
        )
        assert already_authenticated.status_code == 200
        assert browser.cookies["research_monitor_session"] == session_token

        explicit = browser.get(
            "/__bootstrap/direct-launch-capability", follow_redirects=False,
        )
        assert explicit.status_code == 303
        assert browser.get(
            "/__bootstrap/direct-launch-capability", follow_redirects=False,
        ).status_code == 404

    with TestClient(app) as second_browser:
        launched_again = second_browser.get(
            "/", headers=DIRECT_NAVIGATION_HEADERS, follow_redirects=False,
        )
        assert launched_again.status_code == 303
        assert "research_monitor_session" in second_browser.cookies
        assert second_browser.get("/api/v1/projects").status_code == 200


@pytest.mark.parametrize(
    ("header", "replacement"),
    [
        ("Sec-Fetch-Site", None),
        ("Sec-Fetch-Mode", None),
        ("Sec-Fetch-Dest", None),
        ("Sec-Fetch-User", None),
        ("Sec-Fetch-Site", "cross-site"),
        ("Sec-Fetch-Mode", "cors"),
        ("Sec-Fetch-Dest", "iframe"),
        ("Sec-Fetch-User", "?0"),
    ],
)
def test_incomplete_or_non_user_navigation_cannot_create_a_session(
    settings, database, header: str, replacement: str | None,
) -> None:
    app = create_app(
        settings=settings,
        database=database,
        browser_bootstrap_token="guarded-launch-capability",
    )
    headers = dict(DIRECT_NAVIGATION_HEADERS)
    if replacement is None:
        headers.pop(header)
    else:
        headers[header] = replacement

    with TestClient(app) as browser:
        rejected = browser.get("/", headers=headers, follow_redirects=False)
        assert rejected.status_code == 200
        assert "research_monitor_session" not in browser.cookies
        assert browser.get("/api/v1/projects").status_code == 401

        fallback = browser.get(
            "/__bootstrap/guarded-launch-capability", follow_redirects=False,
        )
        assert fallback.status_code == 303


def test_direct_navigation_authentication_preserves_a_safe_deep_spa_route(
    settings, database,
) -> None:
    app = create_app(
        settings=settings,
        database=database,
        browser_bootstrap_token="root-only-capability",
    )

    with TestClient(app) as browser:
        target = "/projects/example/graph?status=ready&label=demo%20task"
        deep_link = browser.get(
            target,
            headers=DIRECT_NAVIGATION_HEADERS,
            follow_redirects=False,
        )
        assert deep_link.status_code == 303
        assert deep_link.headers["location"] == target
        assert deep_link.headers["cache-control"] == "no-store"
        assert "research_monitor_session" in browser.cookies
        assert browser.get(target).status_code == 200
        assert browser.get("/api/v1/projects").status_code == 200

    with TestClient(app) as cross_site:
        rejected = cross_site.get(
            target,
            headers={**DIRECT_NAVIGATION_HEADERS, "Sec-Fetch-Site": "cross-site"},
            follow_redirects=False,
        )
        assert rejected.status_code == 200
        assert "research_monitor_session" not in cross_site.cookies
        assert cross_site.get(
            "/api/v1/projects", headers=DIRECT_NAVIGATION_HEADERS,
        ).status_code == 401
        assert "research_monitor_session" not in cross_site.cookies


def test_explicit_capability_and_direct_navigation_are_independent(
    settings, database,
) -> None:
    app = create_app(
        settings=settings,
        database=database,
        browser_bootstrap_token="explicit-first-capability",
    )

    with TestClient(app) as explicit_browser:
        assert explicit_browser.get(
            "/__bootstrap/explicit-first-capability", follow_redirects=False,
        ).status_code == 303

    with TestClient(app) as direct_browser:
        response = direct_browser.get(
            "/", headers=DIRECT_NAVIGATION_HEADERS, follow_redirects=False,
        )
        assert response.status_code == 303
        assert "research_monitor_session" in direct_browser.cookies
        assert direct_browser.get("/api/v1/projects").status_code == 200


def test_all_api_routes_require_auth_and_user_agent_cannot_bypass(
    client: TestClient, project_root: Path
) -> None:
    (project_root / "result.txt").write_text("result", encoding="utf-8")
    project = enroll(client, project_root)
    root_id = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()["artifact_roots"][0]["id"]
    artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {
        "id": artifact_id,
        "kind": "local",
        "artifact_root_id": root_id,
        "locator": "result.txt",
        "label": "Result",
    })])

    with TestClient(client.app) as unauthenticated:
        assert unauthenticated.get("/api/v1/projects").status_code == 401
        assert unauthenticated.post("/api/v1/projects", json={}).status_code == 401
        assert unauthenticated.get(
            f"/api/v1/artifacts/{artifact_id}/preview"
        ).status_code == 401
        assert unauthenticated.post("/api/v1/backup", json={}).status_code == 401
        assert unauthenticated.get(
            "/api/v1/projects",
            headers={"User-Agent": "research-monitor-cli/999"},
        ).status_code == 401

        bearer = {"Authorization": "Bearer test-cli-token"}
        assert unauthenticated.get("/api/v1/projects", headers=bearer).status_code == 200
        assert unauthenticated.post(
            "/api/v1/backup", json={}, headers=bearer,
        ).status_code == 200


def test_browser_cannot_choose_backup_target_and_cli_overwrite_requires_force(
    client: TestClient, tmp_path: Path
) -> None:
    target = tmp_path / "chosen.db"
    response = client.post("/api/v1/backup", json={"output": str(target)})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "browser_backup_target_forbidden"
    assert not target.exists()

    bearer = {"Authorization": "Bearer test-cli-token"}
    with TestClient(client.app) as cli:
        created = cli.post(
            "/api/v1/backup", json={"output": str(target)}, headers=bearer,
        )
        assert created.status_code == 200, created.text
        assert target.exists()

        duplicate = cli.post(
            "/api/v1/backup", json={"output": str(target)}, headers=bearer,
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "backup_target_exists"

        replaced = cli.post(
            "/api/v1/backup",
            json={"output": str(target), "force": True},
            headers=bearer,
        )
        assert replaced.status_code == 200, replaced.text


def test_only_cli_can_mint_short_lived_one_use_browser_bootstrap(
    client: TestClient,
) -> None:
    browser_attempt = client.post("/api/v1/browser/bootstrap", json={})
    assert browser_attempt.status_code == 403
    assert browser_attempt.json()["detail"]["code"] == "cli_auth_required"

    with TestClient(client.app) as recovery:
        minted = recovery.post(
            "/api/v1/browser/bootstrap",
            json={},
            headers={"Authorization": "Bearer test-cli-token"},
        )
        assert minted.status_code == 200, minted.text
        payload = minted.json()
        assert payload["expires_in_seconds"] == 60
        assert payload["browser_url"].startswith("http://testserver/__bootstrap/")

        opened = recovery.get(payload["browser_url"], follow_redirects=False)
        assert opened.status_code == 303
        assert opened.headers["location"] == "/"
        assert recovery.get(
            payload["browser_url"], follow_redirects=False
        ).status_code == 404


def test_minted_browser_bootstrap_expires_after_sixty_seconds() -> None:
    now = [100.0]
    state = BrowserAuthState("serve-token", clock=lambda: now[0])
    token = state.mint_bootstrap(60)
    now[0] = 160.0

    assert state.consume_bootstrap(token) is None
    # The initial serve URL intentionally remains valid until its first use.
    assert state.consume_bootstrap("serve-token") is not None
