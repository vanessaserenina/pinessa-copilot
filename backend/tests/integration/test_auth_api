import uuid

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditEvent

PASSWORD = "correct-horse-battery"  # same as DEFAULT_PASSWORD in conftest.py


def login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def auth_headers(client, user):
    response = login(client, user.email)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def events(db, action, **filters):
    query = select(AuditEvent).where(AuditEvent.action == action)
    for column, value in filters.items():
        query = query.where(getattr(AuditEvent, column) == value)
    return list(db.scalars(query))


def test_login_success_returns_token_and_is_audited(client, db, make_user):
    user = make_user("sales")
    response = login(client, user.email)
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]
    assert len(events(db, "auth.login", actor_user_id=user.id)) == 1


def test_wrong_password_is_rejected_and_audited(client, db, make_user):
    user = make_user("sales")
    response = login(client, user.email, "wrong-password-123")
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"
    failed = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "auth.login_failed", AuditEvent.detail["email"].astext == user.email
        )
    ).all()
    assert len(failed) == 1


def test_unknown_email_gets_the_same_error(client):
    response = login(client, f"nobody-{uuid.uuid4().hex[:8]}@pinessa-test.example")
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_inactive_user_cannot_log_in(client, make_user):
    user = make_user("sales", is_active=False)
    assert login(client, user.email).status_code == 401


def test_me_requires_a_valid_token(client, make_user):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    user = make_user("warehouse")
    response = client.get("/api/auth/me", headers=auth_headers(client, user))
    assert response.status_code == 200
    assert response.json()["email"] == user.email
    assert response.json()["role"] == "warehouse"
    assert "password" not in response.text.lower()


def test_token_stops_working_when_user_is_deactivated(client, db, make_user):
    user = make_user("sales")
    headers = auth_headers(client, user)
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    user.is_active = False
    db.flush()
    assert client.get("/api/auth/me", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "role,expected",
    [("admin", 200), ("sales", 403), ("purchasing", 403), ("warehouse", 403), ("approver", 403)],
)
def test_user_list_permission_by_role(client, make_user, role, expected):
    headers = auth_headers(client, make_user(role))
    assert client.get("/api/users", headers=headers).status_code == expected


@pytest.mark.parametrize(
    "role,expected",
    [("admin", 200), ("approver", 200), ("sales", 403), ("purchasing", 403), ("warehouse", 403)],
)
def test_audit_log_permission_by_role(client, make_user, role, expected):
    headers = auth_headers(client, make_user(role))
    assert client.get("/api/audit-events", headers=headers).status_code == expected


def test_protected_endpoints_need_login(client):
    assert client.get("/api/users").status_code == 401
    assert client.get("/api/audit-events").status_code == 401


def test_forbidden_attempt_is_audited(client, db, make_user):
    user = make_user("sales")
    headers = auth_headers(client, user)
    assert client.get("/api/users", headers=headers).status_code == 403
    denied = events(db, "permission.denied", actor_user_id=user.id)
    assert len(denied) == 1
    assert denied[0].detail["permission"] == "users:manage"


def test_admin_creates_user_and_it_is_audited(client, db, make_user):
    admin = make_user("admin")
    make_user("purchasing")  # makes sure the 'purchasing' role exists in this test
    headers = auth_headers(client, admin)
    email = f"new-{uuid.uuid4().hex[:8]}@pinessa-test.example"
    payload = {
        "email": email.upper(),
        "full_name": "TEST New Person (SYNTHETIC)",
        "password": "another-strong-pass",
        "role": "purchasing",
    }
    response = client.post("/api/users", json=payload, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == email
    assert body["role"] == "purchasing"
    assert "password" not in body

    created = events(db, "user.created", entity_id=body["id"])
    assert len(created) == 1
    assert created[0].actor_user_id == admin.id
    assert created[0].after["email"] == email
    assert "password_hash" not in created[0].after

    assert login(client, email, "another-strong-pass").status_code == 200


def test_duplicate_email_is_a_conflict(client, make_user):
    admin = make_user("admin")
    make_user("purchasing")
    headers = auth_headers(client, admin)
    payload = {
        "email": f"dup-{uuid.uuid4().hex[:8]}@pinessa-test.example",
        "full_name": "TEST Dup (SYNTHETIC)",
        "password": "another-strong-pass",
        "role": "purchasing",
    }
    assert client.post("/api/users", json=payload, headers=headers).status_code == 201
    assert client.post("/api/users", json=payload, headers=headers).status_code == 409


def test_create_user_validation(client, make_user):
    headers = auth_headers(client, make_user("admin"))
    base = {
        "email": f"v-{uuid.uuid4().hex[:8]}@pinessa-test.example",
        "full_name": "TEST V (SYNTHETIC)",
        "password": "another-strong-pass",
        "role": "purchasing",
    }
    assert client.post("/api/users", json={**base, "role": "no-such-role"}, headers=headers).status_code == 422
    assert client.post("/api/users", json={**base, "password": "short"}, headers=headers).status_code == 422
    assert client.post("/api/users", json={**base, "email": "not-an-email"}, headers=headers).status_code == 422


def test_non_admin_cannot_create_users(client, make_user):
    headers = auth_headers(client, make_user("sales"))
    payload = {
        "email": f"x-{uuid.uuid4().hex[:8]}@pinessa-test.example",
        "full_name": "TEST X (SYNTHETIC)",
        "password": "another-strong-pass",
        "role": "sales",
    }
    assert client.post("/api/users", json=payload, headers=headers).status_code == 403