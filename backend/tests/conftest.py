import uuid

from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy.schema import CreateSchema, DropSchema
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every table, including users)
from app.core.database import engine, get_db
from app.main import app
from app.modules.auth.models import Role, User
from app.modules.auth.security import hash_password
from app.modules.products.models import Bom, BomItem, Product, ProductCategory, ProductFamily
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

@pytest.fixture()
def new_product(db):
    """Factory for throw-away synthetic products (rolled back after each test)."""

    def factory(product_type="FINISHED", unit="pcs"):
        tag = uuid.uuid4().hex[:8]
        category = ProductCategory(code=f"ZZB-CAT-{tag}", name="Test category")
        db.add(category)
        db.flush()
        family = ProductFamily(
            category_id=category.id, code=f"ZZB-FAM-{tag}", name="Test family", source="synthetic"
        )
        db.add(family)
        db.flush()
        product = Product(
            family_id=family.id,
            sku=f"ZZB-SKU-{tag}",
            name=f"TEST product {tag}",
            variant_key="default",
            product_type=product_type,
            unit_of_measure=unit,
            source="synthetic",
        )
        db.add(product)
        db.flush()
        return product

    return factory

@pytest.fixture()
def headers_for(client):
    """Returns a function that logs a user in and gives back ready-to-use auth headers."""

    def factory(user, password=DEFAULT_PASSWORD):
        response = client.post(
            "/api/auth/login", data={"username": user.email, "password": password}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return factory


@pytest.fixture()
def make_bom(db):
    """Create a BOM directly in the database. lines = [(component Product, quantity, scrap), ...]."""
    from decimal import Decimal

    def factory(product, lines, version=1, status="active"):
        bom = Bom(product_id=product.id, version_number=version, status=status, is_synthetic=True)
        db.add(bom)
        db.flush()
        for component, quantity, scrap in lines:
            db.add(
                BomItem(
                    bom_id=bom.id,
                    component_product_id=component.id,
                    quantity_per_unit=Decimal(quantity),
                    scrap_percent=Decimal(scrap),
                )
            )
        db.flush()
        return bom

    return factory

@pytest.fixture()
def migrated_db(monkeypatch):
    """An isolated, migrated schema for tests that need committed transactions."""
    schema = f"inventory_test_{uuid.uuid4().hex}"
    with engine.begin() as conn:
        conn.execute(CreateSchema(schema))
    isolated = create_engine(engine.url)
    try:
        monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
        backend = Path(__file__).resolve().parents[1]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "migrations"))
        command.upgrade(config, "head")
        with Session(isolated) as session:
            yield session
    finally:
        isolated.dispose()
        with engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
