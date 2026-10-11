from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings
from app.modules.auth.permissions import ROLE_PERMISSIONS
from app.modules.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_verifies():
    hashed = hash_password("correct-horse-battery")
    assert hashed != "correct-horse-battery"
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong-password", hashed)


def test_same_password_gives_different_hashes():
    assert hash_password("same-password-123") != hash_password("same-password-123")


def test_garbage_hash_does_not_crash():
    assert verify_password("anything", "not-a-hash") is False


def test_token_round_trip():
    assert decode_access_token(create_access_token(42)) == 42


def test_expired_token_is_rejected():
    payload = {"sub": "42", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    assert decode_access_token(token) is None


def test_token_signed_with_another_key_is_rejected():
    payload = {"sub": "42", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    assert decode_access_token(jwt.encode(payload, "x" * 40, algorithm="HS256")) is None


def test_token_without_expiry_is_rejected():
    token = jwt.encode({"sub": "42"}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    assert decode_access_token(token) is None


def test_garbage_token_is_rejected():
    assert decode_access_token("not.a.token") is None


def test_all_planned_roles_exist():
    assert set(ROLE_PERMISSIONS) == {"admin", "sales", "purchasing", "warehouse", "approver"}


def test_only_admin_manages_users():
    assert {r for r, p in ROLE_PERMISSIONS.items() if "users:manage" in p} == {"admin"}


def test_audit_read_is_admin_and_approver_only():
    assert {r for r, p in ROLE_PERMISSIONS.items() if "audit:read" in p} == {"admin", "approver"}