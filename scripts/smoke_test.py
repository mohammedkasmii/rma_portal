"""Startup smoke test: exercises the FastAPI app without touching a real
browser or the real OmegaFlow portal.

Deliberately does NOT use ``with TestClient(app):`` -- that would run the
FastAPI lifespan, which starts the real 5-minute poller against Camoufox.
Route handlers work fine without the lifespan for this check; the poller
itself is covered separately by ``PollScheduler`` unit tests.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from rma_portal.bootstrap import build_application
from rma_portal.domain.enums import Role
from rma_portal.web.app import create_app


def main() -> int:
    application = build_application()
    application.account_service.create_user(
        username="smoke-admin",
        display_name="Smoke Admin",
        password="CorrectHorse1",
        role=Role.ADMIN,
    )
    app = create_app(application)
    client = TestClient(app)

    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}, r.text

    r = client.get("/login")
    assert r.status_code == 200, r.text

    r = client.post(
        "/login",
        data={"username": "smoke-admin", "password": "CorrectHorse1"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    cookie = r.cookies.get(application.settings.session_cookie_name)
    assert cookie, "no session cookie set"

    client.cookies.set(application.settings.session_cookie_name, cookie)
    r = client.get("/")
    assert r.status_code == 200, r.text

    r = client.get("/admin/users")
    assert r.status_code == 200, r.text

    print("SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
