"""The versioned JSON API: authentication, authorization and the employee workflows."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from rma_portal.domain.enums import Role
from rma_portal.infrastructure.portal.browser_control import (
    BrowserControlError,
    BrowserControlResult,
)
from rma_portal.web.api.auth import reset_login_throttle
from tests.support import seed_workflows
from tests.unit.application.fakes import complete, row
from tests.web.conftest import ADMIN_PASSWORD

ORIGIN = {"origin": "http://testserver"}
GARAGE = "agreement_garage"
PHOTOS = "photos_pending"
VALIDATED = "agreement_validated"


@pytest.fixture(autouse=True)
def _fresh_throttle():
    reset_login_throttle()


def api_login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    client.cookies.set("rma_portal_session", response.cookies["rma_portal_session"])


def make_client(application) -> TestClient:
    from rma_portal.web.app import create_app

    return TestClient(create_app(application))


def run_cycles(application, uow_factory, cycles, workflows=(GARAGE, PHOTOS)) -> None:
    """Enable ``workflows`` and run one synchronization per scripted cycle."""
    seed_workflows(uow_factory, only=list(workflows))
    application.reader_factory._cycles = list(cycles)

    async def run():
        for _ in cycles:
            await application.sync_service.execute()

    asyncio.run(run())


def add_user(application, username: str, password: str = "PasswordOne1", role=Role.EMPLOYEE):
    return application.account_service.create_user(
        username=username, display_name=username.title(), password=password, role=role
    )


@pytest.fixture
def alice(application):
    return add_user(application, "alice", "AlicePassword1")


@pytest.fixture
def bob(application):
    return add_user(application, "bob", "BobPassword12")


@pytest.fixture
def client(application):
    return make_client(application)


# --- authentication -------------------------------------------------------------------------


def test_login_sets_an_http_only_same_site_session_cookie(client, alice):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "AlicePassword1"},
        headers=ORIGIN,
    )

    assert response.status_code == 200
    assert response.json() == {"id": alice.id, "username": "alice", "display_name": "Alice", "role": "EMPLOYEE"}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "password" not in response.text.lower()


def test_wrong_password_and_disabled_account_are_rejected(client, alice, application, admin_user):
    bad = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "nope-nope-1"}, headers=ORIGIN
    )
    assert bad.status_code == 401
    assert "set-cookie" not in bad.headers

    application.account_service.set_active(user_id=alice.id, active=False, acting_admin_id=admin_user.id)
    disabled = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "AlicePassword1"}, headers=ORIGIN
    )
    assert disabled.status_code == 401


def test_login_and_logout_are_same_origin_only(client, alice):
    foreign = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "AlicePassword1"},
        headers={"origin": "http://evil.example"},
    )
    missing = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "AlicePassword1"}
    )
    assert foreign.status_code == 403 and missing.status_code == 403


def test_repeated_failures_are_throttled(client, alice):
    for _ in range(8):
        client.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "wrong-pass-1"}, headers=ORIGIN
        )

    blocked = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "AlicePassword1"}, headers=ORIGIN
    )

    assert blocked.status_code == 429


def test_me_and_logout(client, alice):
    assert client.get("/api/v1/auth/me").status_code == 401
    api_login(client, "alice", "AlicePassword1")
    assert client.get("/api/v1/auth/me").json()["username"] == "alice"

    response = client.post("/api/v1/auth/logout", headers=ORIGIN)

    assert response.status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_forged_or_expired_cookie_is_not_authentication(client, alice):
    client.cookies.set("rma_portal_session", "forged.value.here")
    assert client.get("/api/v1/dashboard").status_code == 401


# --- authorization ---------------------------------------------------------------------------

GET_ENDPOINTS = [
    "/api/v1/dashboard",
    "/api/v1/workflows",
    "/api/v1/workflows/photos_pending",
    "/api/v1/workflows/photos_pending/items",
    "/api/v1/inbox",
    "/api/v1/dossiers/search?q=abc",
    "/api/v1/dossiers/1",
    "/api/v1/dossiers/1/notes",
    "/api/v1/session",
    "/api/v1/sync",
    "/api/v1/admin/workflows",
    "/api/v1/admin/users",
]

MUTATIONS = [
    ("post", "/api/v1/occurrences/1/acknowledge", None),
    ("put", "/api/v1/memberships/1/work-status", {"status": "DONE"}),
    ("post", "/api/v1/dossiers/1/notes", {"body": "x"}),
    ("post", "/api/v1/sync/run", None),
    ("post", "/api/v1/admin/session/connect", None),
    ("patch", "/api/v1/admin/workflows/photos_pending", {"enabled": False}),
    ("post", "/api/v1/admin/users", {"username": "zed", "display_name": "Zed", "password": "PasswordOne1"}),
    ("patch", "/api/v1/admin/users/1", {"active": False}),
]


@pytest.mark.parametrize("path", GET_ENDPOINTS)
def test_every_read_endpoint_requires_authentication(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_every_mutation_requires_authentication(client, method, path, body):
    response = getattr(client, method)(path, json=body, headers=ORIGIN)
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_every_mutation_rejects_a_missing_or_foreign_origin(client, alice, method, path, body):
    api_login(client, "alice", "AlicePassword1")

    missing = getattr(client, method)(path, json=body)
    foreign = getattr(client, method)(path, json=body, headers={"origin": "http://evil.example"})
    lookalike = getattr(client, method)(
        path, json=body, headers={"origin": "http://testserver.evil.example"}
    )

    assert missing.status_code == 403
    assert foreign.status_code == 403
    assert lookalike.status_code == 403


def test_employees_cannot_use_admin_endpoints(client, alice):
    api_login(client, "alice", "AlicePassword1")

    assert client.get("/api/v1/admin/workflows").status_code == 403
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.post("/api/v1/admin/session/connect", headers=ORIGIN).status_code == 403
    assert client.patch("/api/v1/admin/workflows/photos_pending", json={"enabled": False}, headers=ORIGIN).status_code == 403
    assert client.post(
        "/api/v1/admin/users",
        json={"username": "zed", "display_name": "Zed", "password": "PasswordOne1"},
        headers=ORIGIN,
    ).status_code == 403


def test_a_deactivated_employee_loses_access_immediately(client, alice, application, admin_user):
    api_login(client, "alice", "AlicePassword1")
    assert client.get("/api/v1/dashboard").status_code == 200

    application.account_service.set_active(user_id=alice.id, active=False, acting_admin_id=admin_user.id)

    assert client.get("/api/v1/dashboard").status_code == 401


def test_health_needs_no_authentication(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}


class _Control:
    def __init__(self, *, started=True, failure=None):
        self.calls = 0
        self._started = started
        self._failure = failure

    def start_login(self):
        self.calls += 1
        if self._failure:
            raise self._failure
        return BrowserControlResult(state="CONNECTING", started=self._started)


def test_admin_session_connect_returns_the_configured_lan_viewer(client, application, admin_user):
    application.browser_control = _Control()
    api_login(client, "admin", ADMIN_PASSWORD)

    response = client.post("/api/v1/admin/session/connect", headers=ORIGIN)

    assert response.status_code == 202
    assert response.json() == {
        "started": True,
        "state": "started",
        "connect_url": "https://vnc.example/",
    }


def test_admin_session_connect_reports_an_active_login_as_a_normal_state(client, application, admin_user):
    control = application.browser_control = _Control(started=False)
    api_login(client, "admin", ADMIN_PASSWORD)

    response = client.post("/api/v1/admin/session/connect", headers=ORIGIN)

    assert response.status_code == 202 and control.calls == 1
    assert response.json()["started"] is False and response.json()["state"] == "already_running"


def test_admin_session_connect_failure_is_generic_and_leaks_nothing(client, application, admin_user):
    application.browser_control = _Control(failure=BrowserControlError("secret-token-value"))
    api_login(client, "admin", ADMIN_PASSWORD)

    response = client.post("/api/v1/admin/session/connect", headers=ORIGIN)

    assert response.status_code == 503 and "secret-token-value" not in response.text


def test_admin_session_connect_is_admin_only_and_same_origin(client, application, alice, admin_user):
    control = application.browser_control = _Control()

    assert client.post("/api/v1/admin/session/connect", headers=ORIGIN).status_code == 401
    api_login(client, "alice", "AlicePassword1")
    assert client.post("/api/v1/admin/session/connect", headers=ORIGIN).status_code == 403
    client.post("/api/v1/auth/logout", headers=ORIGIN)
    api_login(client, "admin", ADMIN_PASSWORD)
    assert client.post("/api/v1/admin/session/connect").status_code == 403  # missing Origin (CSRF)
    assert control.calls == 0


# --- dashboard, workflows, inbox -------------------------------------------------------------


def _two_cycle_world(application, uow_factory, extra_rows=()):
    baseline = {GARAGE: complete(GARAGE, row("g1")), PHOTOS: complete(PHOTOS, row("p1"))}
    arrivals = {
        GARAGE: complete(GARAGE, row("g1")),
        PHOTOS: complete(PHOTOS, row("p1"), row("p2", insured_name="Sara Test"), *extra_rows),
    }
    run_cycles(application, uow_factory, [baseline, arrivals])


def test_dashboard_reports_counts_health_and_the_site_validation_badge(
    client, alice, application, uow_factory, portal_account_id
):
    _two_cycle_world(application, uow_factory)
    api_login(client, "alice", "AlicePassword1")

    data = client.get("/api/v1/dashboard").json()

    assert data["counters"]["actionable_new"] == 1
    assert data["counters"]["problem_workflows"] == 0
    by_key = {w["key"]: w for w in data["workflows"]}
    assert by_key[PHOTOS]["unread_new"] == 1 and by_key[PHOTOS]["active_count"] == 2
    assert by_key[PHOTOS]["needs_site_validation"] is True  # CAPTURE_DERIVED: "À valider sur site"
    assert by_key[PHOTOS]["rules_status"] == "CAPTURE_DERIVED"
    assert by_key[GARAGE]["needs_site_validation"] is False and by_key[GARAGE]["rules_status"] == "CONFIRMED"
    assert by_key[PHOTOS]["last_run"]["created"] == 1
    assert data["last_run"]["status"] == "COMPLETE"
    assert data["session"]["state"] == "READY" and data["session"]["connect_url"] == "https://vnc.example/"
    assert [e["kind"] for e in data["activity"]] == ["WORKFLOW_ITEM_NEW"]
    assert data["activity"][0]["workflow_name"] == "Dossiers en instance Photos"
    assert data["activity"][0]["notification_class"] == "ACTION"


def test_dashboard_shows_informational_activity_distinctly(
    client, alice, application, uow_factory, portal_account_id
):
    run_cycles(
        application,
        uow_factory,
        [
            {PHOTOS: complete(PHOTOS), VALIDATED: complete(VALIDATED)},
            {PHOTOS: complete(PHOTOS, row("p1")), VALIDATED: complete(VALIDATED, row("v1"))},
        ],
        workflows=(PHOTOS, VALIDATED),
    )
    api_login(client, "alice", "AlicePassword1")

    data = client.get("/api/v1/dashboard").json()

    classes = {e["kind"]: e["notification_class"] for e in data["activity"]}
    assert classes == {"WORKFLOW_ITEM_NEW": "ACTION", "WORKFLOW_ITEM_COMPLETED": "INFORMATIONAL"}
    assert data["counters"]["actionable_new"] == 1  # the informational arrival is not counted


def test_workflow_detail_lists_columns_runs_and_a_blank_queue(
    client, alice, application, uow_factory, portal_account_id
):
    run_cycles(application, uow_factory, [{PHOTOS: complete(PHOTOS)}], workflows=(PHOTOS,))
    api_login(client, "alice", "AlicePassword1")

    detail = client.get(f"/api/v1/workflows/{PHOTOS}").json()
    items = client.get(f"/api/v1/workflows/{PHOTOS}/items").json()

    assert detail["workflow"]["baseline_completed_at"] is not None
    assert detail["primary_date_labels"] == ["Date envoi", "Date photos avant", "Date photo en cours"]
    assert any(c["key"] == "photos_instance" for c in detail["columns"])
    assert detail["recent_runs"][0]["baseline"] is True and detail["recent_runs"][0]["rows_seen"] == 0
    assert items["total"] == 0 and items["items"] == []  # a valid empty queue
    assert client.get("/api/v1/workflows/unknown_queue").status_code == 404
    assert client.get("/api/v1/workflows/unknown_queue/items").status_code == 404


def test_inbox_filters_search_sort_and_paginate_on_the_server(
    client, alice, application, uow_factory, portal_account_id
):
    many = [row(f"x{i:02d}", insured_name=f"Client {i:02d}") for i in range(30)]
    run_cycles(
        application,
        uow_factory,
        [{PHOTOS: complete(PHOTOS)}, {PHOTOS: complete(PHOTOS, *many)}],
        workflows=(PHOTOS,),
    )
    api_login(client, "alice", "AlicePassword1")

    first = client.get("/api/v1/inbox", params={"page_size": 10}).json()
    third = client.get("/api/v1/inbox", params={"page_size": 10, "page": 3}).json()
    fourth = client.get("/api/v1/inbox", params={"page_size": 10, "page": 4}).json()
    found = client.get("/api/v1/inbox", params={"search": "client 07"}).json()
    unread = client.get("/api/v1/inbox", params={"unread": "true", "kind": "arrival"}).json()
    by_number = client.get("/api/v1/inbox", params={"sort": "number", "order": "asc", "page_size": 5}).json()

    assert first["total"] == 30 and len(first["items"]) == 10 and first["page_size"] == 10
    assert len(third["items"]) == 10 and fourth["items"] == []
    assert [i["insured_name"] for i in found["items"]] == ["Client 07"]
    assert unread["total"] == 30
    assert [i["dossier_number"] for i in by_number["items"]] == [f"D-x{i:02d}" for i in range(5)]
    assert first["items"][0]["workflow_name"] == "Dossiers en instance Photos"
    assert client.get("/api/v1/inbox", params={"sort": "bogus"}).status_code == 422
    assert client.get("/api/v1/inbox", params={"page_size": 500}).status_code == 422


def test_inbox_excludes_disabled_workflows_and_scopes_by_workflow(
    client, admin_user, alice, application, uow_factory, portal_account_id
):
    _two_cycle_world(application, uow_factory)
    api_login(client, "alice", "AlicePassword1")
    assert client.get("/api/v1/inbox").json()["total"] == 3

    admin_client = make_client(application)
    api_login(admin_client, "admin", ADMIN_PASSWORD)
    admin_client.patch(f"/api/v1/admin/workflows/{PHOTOS}", json={"enabled": False}, headers=ORIGIN)

    assert client.get("/api/v1/inbox").json()["total"] == 1
    scoped = client.get("/api/v1/inbox", params={"workflow": PHOTOS}).json()
    assert scoped["total"] == 2  # an explicit scope still reads the workflow


def test_item_rows_carry_workflow_specific_columns_and_exact_links(
    client, alice, application, uow_factory, portal_account_id
):
    run_cycles(
        application,
        uow_factory,
        [
            {PHOTOS: complete(PHOTOS)},
            {PHOTOS: complete(PHOTOS, row("p1", photos_instance="Instance photos avant", date_envoi="03/09/2026"))},
        ],
        workflows=(PHOTOS,),
    )
    api_login(client, "alice", "AlicePassword1")

    item = client.get(f"/api/v1/workflows/{PHOTOS}/items").json()["items"][0]

    assert item["fields"]["photos_instance"] == "Instance photos avant"
    assert item["primary_date_raw"] == "03/09/2026" and item["primary_date_label"] == "Date envoi"
    assert item["omegaflow_url"] == (
        "https://omegaflow.example/#dossiers-en-instance-photos/view-dossier-details/p1/"
    )
    assert item["unread"] is True and item["unread_kind"] == "WORKFLOW_ITEM_NEW"


# --- occurrence acknowledgement --------------------------------------------------------------


def test_reading_a_dossier_never_acknowledges_but_opening_an_occurrence_does_for_me_only(
    application, uow_factory, portal_account_id, alice, bob
):
    _two_cycle_world(application, uow_factory)
    alice_client, bob_client = make_client(application), make_client(application)
    api_login(alice_client, "alice", "AlicePassword1")
    api_login(bob_client, "bob", "BobPassword12")
    item = next(
        i for i in alice_client.get("/api/v1/inbox").json()["items"] if i["record_id"] == "p2"
    )

    # GET is safe: reading the dossier page leaves the alert unread.
    page = alice_client.get(f"/api/v1/dossiers/{item['dossier_id']}").json()
    assert page["memberships"][0]["occurrences"][0]["unread"] is True

    ack = alice_client.post(f"/api/v1/occurrences/{item['occurrence_id']}/acknowledge", headers=ORIGIN)
    again = alice_client.post(f"/api/v1/occurrences/{item['occurrence_id']}/acknowledge", headers=ORIGIN)

    assert ack.json() == {"occurrence_id": item["occurrence_id"], "acknowledged": 1}
    assert again.json()["acknowledged"] == 0  # idempotent
    alice_page = alice_client.get(f"/api/v1/dossiers/{item['dossier_id']}").json()
    bob_page = bob_client.get(f"/api/v1/dossiers/{item['dossier_id']}").json()
    assert alice_page["memberships"][0]["occurrences"][0]["unread"] is False
    assert bob_page["memberships"][0]["occurrences"][0]["unread"] is True
    assert alice_client.get("/api/v1/dashboard").json()["counters"]["actionable_new"] == 0
    assert bob_client.get("/api/v1/dashboard").json()["counters"]["actionable_new"] == 1
    assert alice_client.post("/api/v1/occurrences/99999/acknowledge", headers=ORIGIN).status_code == 404


def test_a_new_employee_starts_with_no_unread_history(
    application, uow_factory, portal_account_id, alice
):
    _two_cycle_world(application, uow_factory)
    late = add_user(application, "carol", "CarolPassword1")
    client = make_client(application)
    api_login(client, "carol", "CarolPassword1")

    assert late.id
    assert client.get("/api/v1/dashboard").json()["counters"]["actionable_new"] == 0
    assert client.get("/api/v1/inbox", params={"unread": "true"}).json()["total"] == 0


# --- dossier page, work status, notes --------------------------------------------------------


def test_dossier_page_lists_every_membership_events_and_notes(
    client, alice, application, uow_factory, portal_account_id
):
    shared = row("shared", insured_name="Karim Test")
    run_cycles(
        application,
        uow_factory,
        [
            {GARAGE: complete(GARAGE), PHOTOS: complete(PHOTOS)},
            {GARAGE: complete(GARAGE, shared), PHOTOS: complete(PHOTOS, shared)},
        ],
    )
    api_login(client, "alice", "AlicePassword1")
    dossier_id = client.get("/api/v1/inbox").json()["items"][0]["dossier_id"]

    page = client.get(f"/api/v1/dossiers/{dossier_id}").json()

    assert page["dossier"]["insured_name"] == "Karim Test"
    assert {m["workflow_key"] for m in page["memberships"]} == {GARAGE, PHOTOS}
    assert all(m["omegaflow_url"].startswith("https://omegaflow.example/#") for m in page["memberships"])
    assert {e["workflow_key"] for e in page["events"]} == {GARAGE, PHOTOS}
    assert client.get("/api/v1/dossiers/424242").status_code == 404


def test_work_status_belongs_to_the_membership_and_uses_optimistic_locking(
    client, alice, application, uow_factory, portal_account_id
):
    shared = row("shared")
    run_cycles(
        application,
        uow_factory,
        [{GARAGE: complete(GARAGE), PHOTOS: complete(PHOTOS)}, {GARAGE: complete(GARAGE, shared), PHOTOS: complete(PHOTOS, shared)}],
    )
    api_login(client, "alice", "AlicePassword1")
    dossier_id = client.get("/api/v1/inbox").json()["items"][0]["dossier_id"]
    memberships = {
        m["workflow_key"]: m for m in client.get(f"/api/v1/dossiers/{dossier_id}").json()["memberships"]
    }

    done = client.put(
        f"/api/v1/memberships/{memberships[GARAGE]['membership_id']}/work-status",
        json={"status": "DONE", "expected_version": 1},
        headers=ORIGIN,
    )
    stale = client.put(
        f"/api/v1/memberships/{memberships[GARAGE]['membership_id']}/work-status",
        json={"status": "WAITING", "expected_version": 1},
        headers=ORIGIN,
    )

    assert done.status_code == 200 and done.json()["version"] == 2 and done.json()["updated_by"] == "Alice"
    assert stale.status_code == 409 and stale.json()["current"]["status"] == "DONE"
    after = {
        m["workflow_key"]: m for m in client.get(f"/api/v1/dossiers/{dossier_id}").json()["memberships"]
    }
    assert after[GARAGE]["work_status"] == "DONE"
    assert after[PHOTOS]["work_status"] == "TO_DO"  # the other queue is untouched
    assert client.put("/api/v1/memberships/99999/work-status", json={"status": "DONE"}, headers=ORIGIN).status_code == 404
    assert client.put(
        f"/api/v1/memberships/{memberships[GARAGE]['membership_id']}/work-status",
        json={"status": "SOLVED"},
        headers=ORIGIN,
    ).status_code == 422


def test_notes_are_shared_across_employees_with_optional_workflow_context(
    application, uow_factory, portal_account_id, alice, bob
):
    shared = row("shared")
    run_cycles(
        application,
        uow_factory,
        [{GARAGE: complete(GARAGE), PHOTOS: complete(PHOTOS)}, {GARAGE: complete(GARAGE, shared), PHOTOS: complete(PHOTOS, shared)}],
    )
    alice_client, bob_client = make_client(application), make_client(application)
    api_login(alice_client, "alice", "AlicePassword1")
    api_login(bob_client, "bob", "BobPassword12")
    dossier = alice_client.get("/api/v1/inbox").json()["items"][0]
    membership = dossier["membership_id"]

    plain = alice_client.post(
        f"/api/v1/dossiers/{dossier['dossier_id']}/notes", json={"body": "Appeler le garage."}, headers=ORIGIN
    )
    contextual = alice_client.post(
        f"/api/v1/dossiers/{dossier['dossier_id']}/notes",
        json={"body": "Photos manquantes.", "membership_id": membership},
        headers=ORIGIN,
    )

    assert plain.status_code == 201 and plain.json()["workflow_name"] is None
    assert contextual.json()["workflow_name"] == dossier["workflow_name"]
    seen_by_bob = bob_client.get(f"/api/v1/dossiers/{dossier['dossier_id']}/notes").json()
    assert [n["body"] for n in seen_by_bob] == ["Photos manquantes.", "Appeler le garage."]
    assert seen_by_bob[0]["author"] == "Alice"


def test_note_validation_and_foreign_membership(client, alice, application, uow_factory, portal_account_id):
    run_cycles(
        application,
        uow_factory,
        [{GARAGE: complete(GARAGE), PHOTOS: complete(PHOTOS)}, {GARAGE: complete(GARAGE, row("a")), PHOTOS: complete(PHOTOS, row("b"))}],
    )
    api_login(client, "alice", "AlicePassword1")
    items = {i["record_id"]: i for i in client.get("/api/v1/inbox").json()["items"]}
    a, b = items["a"], items["b"]

    assert client.post(f"/api/v1/dossiers/{a['dossier_id']}/notes", json={"body": "   "}, headers=ORIGIN).status_code == 422
    assert client.post(
        f"/api/v1/dossiers/{a['dossier_id']}/notes", json={"body": "x" * 2001}, headers=ORIGIN
    ).status_code == 422
    # A membership that belongs to another dossier is refused.
    assert client.post(
        f"/api/v1/dossiers/{a['dossier_id']}/notes",
        json={"body": "ok", "membership_id": b["membership_id"]},
        headers=ORIGIN,
    ).status_code == 404
    assert client.post("/api/v1/dossiers/99999/notes", json={"body": "ok"}, headers=ORIGIN).status_code == 404


def test_dossier_search_needs_two_characters_and_finds_by_number_name_or_plate(
    client, alice, application, uow_factory, portal_account_id
):
    run_cycles(
        application,
        uow_factory,
        [{PHOTOS: complete(PHOTOS)}, {PHOTOS: complete(PHOTOS, row("p1", insured_name="Karim Benali", registration="123-A-45"))}],
        workflows=(PHOTOS,),
    )
    api_login(client, "alice", "AlicePassword1")

    by_name = client.get("/api/v1/dossiers/search", params={"q": "benali"}).json()
    by_plate = client.get("/api/v1/dossiers/search", params={"q": "123-a"}).json()

    assert [h["insured_name"] for h in by_name] == ["Karim Benali"]
    assert by_name[0]["workflows"] == ["Dossiers en instance Photos"]
    assert len(by_plate) == 1
    assert client.get("/api/v1/dossiers/search", params={"q": "k"}).status_code == 422


# --- session and synchronization health -----------------------------------------------------


def test_session_and_sync_health_never_expose_browser_state_or_credentials(
    client, alice, application, uow_factory, portal_account_id
):
    _two_cycle_world(application, uow_factory)
    api_login(client, "alice", "AlicePassword1")

    session = client.get("/api/v1/session")
    sync = client.get("/api/v1/sync")

    for text in (session.text.lower(), sync.text.lower()):
        for forbidden in ("cookie", "storage", "password", "secret", "token", "profile"):
            assert forbidden not in text
    assert set(session.json()) == {
        "state", "label", "last_poll_at", "last_success_at", "last_error", "syncing", "connect_url",
    }
    health = sync.json()
    assert health["last_run"]["workflows_total"] == 2 and health["last_run"]["workflows_failed"] == 0
    assert len(health["recent_runs"]) == 2 and len(health["workflows"]) >= 2


def test_auth_required_surfaces_as_a_session_state_and_an_administrator_alert(
    client, alice, application, uow_factory, portal_account_id
):
    from tests.unit.application.fakes import auth_required

    run_cycles(application, uow_factory, [{GARAGE: auth_required(GARAGE), PHOTOS: auth_required(PHOTOS)}])
    api_login(client, "alice", "AlicePassword1")

    session = client.get("/api/v1/session").json()
    sync = client.get("/api/v1/sync").json()
    dashboard = client.get("/api/v1/dashboard").json()

    assert session["state"] == "AUTH_REQUIRED" and "Reconnexion" in session["label"]
    assert [a["kind"] for a in sync["alerts"]] == ["SESSION_AUTH_REQUIRED"]
    assert dashboard["counters"]["problem_workflows"] == 2
    assert dashboard["alerts"][0]["kind"] == "SESSION_AUTH_REQUIRED"
    assert dashboard["activity"] == []  # operational alerts stay out of the employee feed


def test_a_failed_workflow_is_visible_without_hiding_the_healthy_ones(
    client, alice, application, uow_factory, portal_account_id
):
    from tests.unit.application.fakes import failed

    run_cycles(
        application,
        uow_factory,
        [{GARAGE: failed(GARAGE, "vue introuvable"), PHOTOS: complete(PHOTOS, row("p1"))}],
    )
    api_login(client, "alice", "AlicePassword1")

    workflows = {w["key"]: w for w in client.get("/api/v1/sync").json()["workflows"]}

    assert workflows[GARAGE]["last_poll_status"] == "FAILED" and workflows[GARAGE]["last_error"]
    assert workflows[PHOTOS]["last_poll_status"] == "COMPLETE" and workflows[PHOTOS]["active_count"] == 1


def test_requesting_a_sync_queues_one_request_for_the_worker(client, alice, application):
    api_login(client, "alice", "AlicePassword1")

    first = client.post("/api/v1/sync/run", headers=ORIGIN)
    second = client.post("/api/v1/sync/run", headers=ORIGIN)

    assert first.status_code == 202 and first.json() == {"queued": True}
    assert second.json() == {"queued": False}
    assert client.get("/api/v1/sync").json()["pending_sync_requests"] == 1
    assert client.get("/api/v1/session").json()["syncing"] is True


# --- administration --------------------------------------------------------------------------


def test_admin_promotes_disables_and_reclassifies_a_workflow(
    application, admin_user, uow_factory, portal_account_id
):
    seed_workflows(uow_factory, only=[PHOTOS])
    client = make_client(application)
    api_login(client, "admin", ADMIN_PASSWORD)

    listing = {w["key"]: w for w in client.get("/api/v1/admin/workflows").json()}
    assert listing[PHOTOS]["needs_site_validation"] is True and listing[GARAGE]["enabled"] is False

    updated = client.patch(
        f"/api/v1/admin/workflows/{PHOTOS}",
        json={"rules_status": "CONFIRMED", "notification_class": "INFORMATIONAL"},
        headers=ORIGIN,
    ).json()
    disabled = client.patch(
        f"/api/v1/admin/workflows/{PHOTOS}", json={"enabled": False}, headers=ORIGIN
    ).json()

    assert updated["rules_status"] == "CONFIRMED" and updated["needs_site_validation"] is False
    assert updated["notification_class"] == "INFORMATIONAL"
    assert disabled["enabled"] is False and disabled["rules_status"] == "CONFIRMED"
    assert client.patch("/api/v1/admin/workflows/nope", json={"enabled": True}, headers=ORIGIN).status_code == 404
    assert client.patch(
        f"/api/v1/admin/workflows/{PHOTOS}", json={"rules_status": "WHATEVER"}, headers=ORIGIN
    ).status_code == 422


def test_admin_manages_users_but_cannot_disable_themselves(application, admin_user):
    client = make_client(application)
    api_login(client, "admin", ADMIN_PASSWORD)

    created = client.post(
        "/api/v1/admin/users",
        json={"username": "Nadia", "display_name": "Nadia B.", "password": "PasswordOne1"},
        headers=ORIGIN,
    )
    duplicate = client.post(
        "/api/v1/admin/users",
        json={"username": "nadia", "display_name": "Autre", "password": "PasswordOne1"},
        headers=ORIGIN,
    )
    weak = client.post(
        "/api/v1/admin/users",
        json={"username": "weakone", "display_name": "W", "password": "short"},
        headers=ORIGIN,
    )
    disable_self = client.patch(f"/api/v1/admin/users/{admin_user.id}", json={"active": False}, headers=ORIGIN)
    disable_other = client.patch(f"/api/v1/admin/users/{created.json()['id']}", json={"active": False}, headers=ORIGIN)

    assert created.status_code == 201 and created.json()["username"] == "nadia" and created.json()["active"] is True
    assert "password" not in created.text.lower().replace("display_name", "")
    assert duplicate.status_code == 409 and weak.status_code == 422
    assert disable_self.status_code == 409
    assert disable_other.json()["active"] is False
    users = {u["username"]: u for u in client.get("/api/v1/admin/users").json()}
    assert users["nadia"]["active"] is False and "password_hash" not in users["nadia"]
