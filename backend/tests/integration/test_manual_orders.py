from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.inventory.models import Inventory, Reservation
from app.modules.orders.models import OrderRevision
from app.modules.orders.service import confirm_order, read_order


@pytest.fixture()
def db(migrated_db):
    return migrated_db


def payload(product):
    return {"customer_code": " TEST-HOTEL ", "customer_po_number": "PO-1", "currency": "SGD",
            "lines": [{"product_id": product.id, "quantity": 4, "unit_price": "1.55"}]}


def test_confirm_edit_and_rollback(client, db, make_user, headers_for, new_product, make_bom):
    product, other = new_product(), new_product()
    component = new_product(product_type="COMPONENT")
    make_bom(product, [(component, "2", "0")])
    db.add_all([Inventory(product_id=product.id, available=2),
                Inventory(product_id=component.id, available=3)])
    headers = headers_for(make_user("sales"))
    db.commit()
    body = payload(product)
    response = client.post("/api/orders", json=body, headers=headers)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "draft" and Decimal(draft["subtotal"]) == Decimal("6.20")
    assert db.scalar(select(Reservation.id)) is None
    path = f"/api/orders/{draft['id']}"
    confirm = client.post(path + "/confirm", json={"revision": 1}, headers=headers)
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "awaiting_materials"
    assert Decimal(confirm.json()["stock"]["components"][0]["shortfall"]) == 1
    assert client.post(path + "/confirm", json={"revision": 2}, headers=headers).status_code == 409
    body.update(revision=2, reason="Customer reduced quantity")
    body["lines"][0]["product_id"] = other.id
    assert client.put(path, json=body, headers=headers).status_code == 422
    assert db.get(Inventory, product.id).available == 0
    assert db.get(Inventory, component.id).available == 0
    assert client.get(path, headers=headers).json() == confirm.json()
    body["lines"][0].update(product_id=product.id, quantity=1)
    edited = client.put(path, json=body, headers=headers)
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "ready_for_delivery"
    assert db.get(Inventory, product.id).available == 1
    assert db.get(Inventory, component.id).available == 3
    assert db.scalars(select(Reservation)).one().quantity == 1
    assert client.put(path, json=body, headers=headers).status_code == 409
    revisions = db.scalars(select(OrderRevision).order_by(OrderRevision.revision)).all()
    assert [rev.snapshot["status"] for rev in revisions] == [
        "draft", "awaiting_materials", "ready_for_delivery"]
    assert revisions[-1].reason == body["reason"]


def test_duplicate_checks_and_permissions(client, db, make_user, headers_for, new_product):
    body = payload(new_product())
    headers = headers_for(make_user("sales"))
    warehouse = headers_for(make_user("warehouse"))
    db.commit()
    path = "/api/orders"
    assert client.post(path, json=body, headers=warehouse).status_code == 403
    first = client.post(path, json=body, headers=headers).json()
    body.update(customer_code="test-hotel", customer_po_number=" po-1 ", acknowledge_duplicate=True)
    assert client.post(path, json=body, headers=headers).status_code == 409
    body.update(customer_po_number=None, acknowledge_duplicate=False)
    assert client.post(path, json=body, headers=headers).status_code == 409
    body["acknowledge_duplicate"] = True
    second = client.post(path, json=body, headers=headers)
    assert second.status_code == 201
    body.update(revision=1, reason="Update PO", customer_po_number="PO-1")
    assert client.put(f"{path}/{second.json()['id']}", json=body, headers=headers).status_code == 409
    body["customer_po_number"] = "PO-2"
    edited = client.put(f"{path}/{second.json()['id']}", json=body, headers=headers)
    assert edited.status_code == 200 and edited.json()["status"] == "draft"
    assert {o["id"] for o in client.get(path, headers=headers).json()} == {first["id"], second.json()["id"]}


def test_competing_confirmations_share_last_units(client, db, make_user, headers_for, new_product, make_bom):
    product = new_product()
    component = new_product(product_type="COMPONENT")
    make_bom(product, [(component, "1", "0")])
    db.add(Inventory(product_id=product.id, available=4))
    user = make_user("sales")
    actor_id = user.id
    headers = headers_for(user)
    db.commit()
    ids = []
    for customer in ["TEST-A", "TEST-B"]:
        response = client.post("/api/orders", json={**payload(product), "customer_code": customer}, headers=headers)
        assert response.status_code == 201
        ids.append(response.json()["id"])

    ready = Barrier(2, timeout=10)
    pool = db.get_bind()

    def confirm(order_id):
        with Session(pool) as session:
            order = read_order(session, order_id, lock=True)
            ready.wait()
            return confirm_order(session, order, 1, actor_id).status

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(confirm, ids)) == ["awaiting_materials", "ready_for_delivery"]
    assert db.scalars(select(Reservation)).one().quantity == 4
    assert db.get(Inventory, product.id).available == 0


def test_order_validation_and_component_coverage(client, db, make_user, headers_for, new_product, make_bom):
    product = new_product()
    part = new_product(product_type="COMPONENT")
    make_bom(product, [(part, "1.5", "0")])
    db.add(Inventory(product_id=part.id, available=6))
    headers = headers_for(make_user("admin"))
    db.commit()
    body = payload(product)
    path = "/api/orders"
    for changes in [{"requested_delivery_date": "2000-01-01"}, {"customer_code": " "},
                    {"lines": [{**body["lines"][0], "quantity": 0}]},
                    {"lines": [{**body["lines"][0], "product_id": part.id}]}]:
        assert client.post(path, json={**body, **changes}, headers=headers).status_code == 422
    draft = client.post(path, json=body, headers=headers).json()
    confirmed = client.post(f"{path}/{draft['id']}/confirm", json={"revision": 1}, headers=headers)
    assert confirmed.json()["status"] == "ready_for_production"
    assert db.scalars(select(Reservation)).one().quantity == 6
