"""Assistant endpoints: fail-soft, authenticated, and unable to change anything."""

from __future__ import annotations

import pytest

from rma_portal.application.ai import (
    AdvisorService,
    AiTimeoutError,
    AiUnavailableError,
)
from tests.unit.application.fakes import complete, row
from tests.unit.application.test_ai import FakeAdvisor
from tests.web.test_api import (
    GARAGE,
    ORIGIN,
    PHOTOS,
    add_user,
    api_login,
    make_client,
    run_cycles,
)


def _use(application, advisor) -> None:
    application.advisor_service = AdvisorService(
        application.uow_factory, application.work_service, advisor
    )


@pytest.fixture
def world(application, uow_factory, portal_account_id):
    add_user(application, "alice", "AlicePassword1")
    run_cycles(
        application,
        uow_factory,
        [
            {GARAGE: complete(GARAGE, row("g1")), PHOTOS: complete(PHOTOS, row("p1"))},
            {GARAGE: complete(GARAGE, row("g1")), PHOTOS: complete(PHOTOS, row("p1"), row("p2"))},
        ],
    )
    client = make_client(application)
    api_login(client, "alice", "AlicePassword1")
    items = {i["record_id"]: i for i in client.get("/api/v1/inbox").json()["items"]}
    return client, items


def test_disabled_assistant_answers_with_a_status_not_an_error(application, world):
    client, items = world

    status = client.get("/api/v1/ai/status").json()
    daily = client.post("/api/v1/ai/daily-summary", headers=ORIGIN)
    summary = client.post(f"/api/v1/dossiers/{items['p2']['dossier_id']}/ai/summary", headers=ORIGIN)

    assert status == {"enabled": False, "model": None, "healthy": None}
    assert daily.status_code == 200 and daily.json()["status"] == "DISABLED"
    assert summary.json()["status"] == "DISABLED" and summary.json()["result"] is None


def test_every_assistant_route_requires_authentication_and_same_origin(application, world):
    client, items = world
    paths = [
        ("get", "/api/v1/ai/status"),
        ("post", "/api/v1/ai/daily-summary"),
        ("post", "/api/v1/ai/anomalies"),
        ("post", f"/api/v1/dossiers/{items['p2']['dossier_id']}/ai/summary"),
        ("post", f"/api/v1/occurrences/{items['p2']['occurrence_id']}/ai/explain"),
        ("post", f"/api/v1/memberships/{items['p2']['membership_id']}/ai/priority"),
    ]
    anonymous = make_client(application)

    for method, path in paths:
        assert getattr(anonymous, method)(path, headers=ORIGIN).status_code == 401
        if method == "post":
            assert client.post(path).status_code == 403
            assert client.post(path, headers={"origin": "http://evil.example"}).status_code == 403


def test_a_working_assistant_returns_structured_results_with_facts_and_a_disclaimer(application, world):
    client, items = world
    _use(application, FakeAdvisor())
    p2 = items["p2"]

    daily = client.post("/api/v1/ai/daily-summary", headers=ORIGIN).json()
    explain = client.post(f"/api/v1/occurrences/{p2['occurrence_id']}/ai/explain", headers=ORIGIN).json()
    priority = client.post(f"/api/v1/memberships/{p2['membership_id']}/ai/priority", headers=ORIGIN).json()
    summary = client.post(f"/api/v1/dossiers/{p2['dossier_id']}/ai/summary", headers=ORIGIN).json()
    anomalies = client.post("/api/v1/ai/anomalies", headers=ORIGIN).json()

    assert daily["status"] == "OK" and daily["result"]["headline"] and daily["model"] == "fake-model:1b"
    assert explain["status"] == "OK" and explain["facts"]
    assert set(explain["result"]["facts_used"]) <= set(explain["facts"])
    assert priority["result"]["priority"] == "HAUTE" and "Suggestion" in priority["disclaimer"]
    assert summary["result"]["summary"] and anomalies["status"] == "NO_DATA"
    assert client.get("/api/v1/ai/status").json()["healthy"] is True


@pytest.mark.parametrize("error", [AiUnavailableError("down"), AiTimeoutError("slow"), RuntimeError("bug")])
def test_a_failing_assistant_never_breaks_login_sync_dashboard_or_notifications(
    application, world, error
):
    client, items = world
    _use(application, FakeAdvisor(error))
    p2 = items["p2"]
    before = client.get("/api/v1/dashboard").json()

    responses = [
        client.post("/api/v1/ai/daily-summary", headers=ORIGIN),
        client.post(f"/api/v1/dossiers/{p2['dossier_id']}/ai/summary", headers=ORIGIN),
        client.post(f"/api/v1/occurrences/{p2['occurrence_id']}/ai/explain", headers=ORIGIN),
        client.post(f"/api/v1/memberships/{p2['membership_id']}/ai/priority", headers=ORIGIN),
    ]

    for response in responses:
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in {"UNAVAILABLE", "TIMEOUT"} and body["result"] is None and body["message"]
    # The rest of the portal is untouched.
    assert client.get("/api/v1/dashboard").json()["counters"] == before["counters"]
    assert client.get("/api/v1/inbox").status_code == 200
    fresh = make_client(application)
    api_login(fresh, "alice", "AlicePassword1")  # login unaffected
    assert client.post("/api/v1/sync/run", headers=ORIGIN).status_code == 202


def test_the_assistant_cannot_acknowledge_or_change_work_status_or_notes(application, world, engine):
    client, items = world
    _use(application, FakeAdvisor())
    p2 = items["p2"]
    unread_before = client.get("/api/v1/dashboard").json()["counters"]["actionable_new"]
    dossier_before = client.get(f"/api/v1/dossiers/{p2['dossier_id']}").json()

    for path in (
        "/api/v1/ai/daily-summary",
        "/api/v1/ai/anomalies",
        f"/api/v1/dossiers/{p2['dossier_id']}/ai/summary",
        f"/api/v1/occurrences/{p2['occurrence_id']}/ai/explain",
        f"/api/v1/memberships/{p2['membership_id']}/ai/priority",
    ):
        client.post(path, headers=ORIGIN)

    dossier_after = client.get(f"/api/v1/dossiers/{p2['dossier_id']}").json()
    assert client.get("/api/v1/dashboard").json()["counters"]["actionable_new"] == unread_before == 1
    assert dossier_after["memberships"] == dossier_before["memberships"]  # unread + work status intact
    assert dossier_after["notes"] == dossier_before["notes"] == []


def test_unknown_targets_are_404_not_assistant_errors(application, world):
    client, _ = world
    _use(application, FakeAdvisor())

    assert client.post("/api/v1/dossiers/99999/ai/summary", headers=ORIGIN).status_code == 404
    assert client.post("/api/v1/occurrences/99999/ai/explain", headers=ORIGIN).status_code == 404
    assert client.post("/api/v1/memberships/99999/ai/priority", headers=ORIGIN).status_code == 404
