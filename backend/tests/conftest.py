import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every table, including users)
from app.core.database import engine, get_db
from app.main import app
from app.modules.auth.models import Role, User
from app.modules.auth.security import hash_password

DEFAULT_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def db():
    """A session whose changes are always rolled back, so tests never leave data behind."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def make_user(db):
    def factory(role_name="sales", *, password=DEFAULT_PASSWORD, is_active=True):
        role = db.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            role = Role(name=role_name, description=f"Test role {role_name}")
            db.add(role)
            db.flush()
        user = User(
            email=f"{role_name}-{uuid.uuid4().hex[:8]}@pinessa-test.example",
            full_name=f"TEST {role_name} (SYNTHETIC)",
            password_hash=hash_password(password),
            role=role,
            is_active=is_active,
            is_synthetic=True,
        )
        db.add(user)
        db.flush()
        return user

    return factory