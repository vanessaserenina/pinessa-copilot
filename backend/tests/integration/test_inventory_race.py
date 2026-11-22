from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.database import engine, get_db
from app.main import app
from app.modules.inventory.models import Reservation


@pytest.fixture()
def db(monkeypatch):
    # The competitors need separate committed transactions and disposable data.
    schema = f"inventory_test_{uuid4().hex}"
    with engine.begin() as conn:
        conn.execute(CreateSchema(schema))
    isolated = create_engine(engine.url)
    try:
        monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
        backend = Path(__file__).resolve().parents[2]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "migrations"))
        command.upgrade(config, "head")
        with Session(isolated) as session:
            yield session
    finally:
        isolated.dispose()
        with engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))


@pytest.mark.parametrize(("quantity", "unit"), [(3, "pcs"), ("0.3334", "g")])
def test_two_requests_reserving_last_units(db, make_user, new_product, monkeypatch, quantity, unit):
    product_id = new_product(product_type="COMPONENT", unit=unit).id
    expected = Decimal(quantity)
    password = uuid4().hex
    warehouse = make_user("warehouse", password=password)
    db.commit()
    pool = db.get_bind()

    def request_session():
        with Session(pool) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    path = f"/api/inventory/{product_id}"
    with TestClient(app) as client:
        login = client.post("/api/auth/login", data={
            "username": warehouse.email, "password": password,
        })
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        payload = {"quantity": quantity}
        for endpoint in ("receipts", "reservations"):
            invalid = client.post(f"{path}/{endpoint}", json={"quantity": "0.33345"}, headers=headers)
            assert invalid.status_code == 422
        receipt = client.post(f"{path}/receipts", json=payload, headers=headers)
        assert receipt.status_code == 200
        assert Decimal(receipt.json()["available"]) == expected

        ready = Barrier(2, timeout=10)

        @event.listens_for(pool, "before_cursor_execute")
        def synchronize_claims(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE inventory "):
                ready.wait()

        def claim():
            return client.post(f"{path}/reservations", json=payload, headers=headers)

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(claim) for _ in range(2)]
            responses = [future.result() for future in futures]
        assert sorted(response.status_code for response in responses) == [201, 409]
        assert Decimal(client.get(path, headers=headers).json()["available"]) == 0
        winner = next(r.json() for r in responses if r.status_code == 201)
        assert Decimal(winner["quantity"]) == expected
        reservation = db.scalars(select(Reservation)).one()
        assert (reservation.id, reservation.product_id, reservation.quantity) == (
            winner["id"], product_id, expected,
        )


def test_receive_and_reserve_maximum_bom_total(
    client, db, make_user, headers_for, new_product, make_bom,
):
    finished = new_product()
    component = new_product(product_type="COMPONENT", unit="g")
    make_bom(finished, [(component, "99999999.9999", "100")])
    headers = headers_for(make_user("warehouse"))

    calculation = client.post(
        f"/api/products/{finished.id}/bom-calculation",
        json={"quantity": 10_000_000},
        headers=headers,
    )
    assert calculation.status_code == 200
    quantity = calculation.json()["lines"][0]["required"]
    assert Decimal(quantity) == Decimal("1999999999998000.0000")

    path = f"/api/inventory/{component.id}"
    receipt = client.post(f"{path}/receipts", json={"quantity": quantity}, headers=headers)
    assert receipt.status_code == 200
    assert Decimal(receipt.json()["available"]) == Decimal(quantity)
    reserved = client.post(f"{path}/reservations", json={"quantity": quantity}, headers=headers)
    assert reserved.status_code == 201
    assert db.scalars(select(Reservation)).one().quantity == Decimal(quantity)
    assert Decimal(client.get(path, headers=headers).json()["available"]) == 0


def test_piece_quantities_must_be_whole(client, db, make_user, headers_for, new_product):
    product = new_product(unit="pcs")
    headers = headers_for(make_user("warehouse"))
    path = f"/api/inventory/{product.id}"
    receipt = client.post(f"{path}/receipts", json={"quantity": "3.0000"}, headers=headers)
    assert receipt.status_code == 200

    for endpoint in ("receipts", "reservations"):
        response = client.post(f"{path}/{endpoint}", json={"quantity": "0.3334"}, headers=headers)
        assert response.status_code == 422

    assert Decimal(client.get(path, headers=headers).json()["available"]) == 3
    assert db.scalars(select(Reservation)).first() is None
