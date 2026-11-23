from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditEvent
from app.modules.purchasing.models import PurchaseOrder, Supplier, SupplierProduct
from app.scripts.seed_supplier import seed_supplier


@pytest.fixture()
def db(migrated_db):
    return migrated_db


@pytest.fixture()
def scenario(client, db, new_product, make_bom, make_user, headers_for):
    finished = new_product()
    part, other = new_product("COMPONENT", unit="kg"), new_product("COMPONENT")
    make_bom(finished, [(part, "1.5", "0"), (other, "2", "0")])
    buyer = headers_for(make_user("purchasing"))
    sales = headers_for(make_user("sales"))
    db.commit()
    body = {"customer_code": "TEST-PO", "currency": "IDR",
            "lines": [{"product_id": finished.id, "quantity": 1, "unit_price": "1"}]}
    order = client.post("/api/orders", headers=sales, json=body)
    assert order.status_code == 201, order.text
    order_path = f"/api/orders/{order.json()['id']}"
    confirmed = client.post(order_path + "/confirm", headers=sales, json={"revision": 1})
    assert confirmed.status_code == 200, confirmed.text
    requisition = client.put(f"/api/purchasing/orders/{order.json()['id']}/revisions/2/requisition", headers=buyer)
    assert requisition.status_code == 200, requisition.text
    url = f"/api/purchasing/requisitions/{requisition.json()['id']}/purchase-order"
    return buyer, sales, url, part, other, order_path, body


def test_draft_purchase_order(client, db, scenario):
    buyer, sales, url, part, other, _, _ = scenario
    supplier = Supplier(code="TEST-1", name="Test supplier", is_synthetic=True)
    db.add(supplier)
    db.flush()
    price = SupplierProduct(supplier_id=supplier.id, component_product_id=part.id, unit_cost=3)
    db.add(price)
    db.commit()
    payload = {"supplier_id": supplier.id}
    assert client.get("/api/purchasing/suppliers", headers=buyer).json()[0]["id"] == supplier.id
    assert client.put(url, json=payload, headers=sales).status_code == 403
    assert client.put(url, json={**payload, "total": 1}, headers=buyer).status_code == 422
    assert client.put(url, json=payload, headers=buyer).status_code == 409
    assert db.scalar(select(PurchaseOrder.id)) is None
    db.add(SupplierProduct(supplier_id=supplier.id, component_product_id=other.id, unit_cost=10))
    db.commit()
    response = client.put(url, json=payload, headers=buyer)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["status"] == "draft" and draft["currency"] == "IDR"
    assert draft["po_number"] == f"PO-{draft['id']:06d}"
    assert Decimal(draft["total"]) == 25
    assert {line["product_id"]: (Decimal(line["quantity"]), Decimal(line["unit_cost"]))
            for line in draft["items"]} == {part.id: (Decimal("1.5"), 3), other.id: (2, 10)}
    price.unit_cost = 99
    db.commit()
    assert client.put(url, json=payload, headers=buyer).json() == draft
    assert client.put(url, json={"supplier_id": supplier.id + 1}, headers=buyer).status_code == 409
    detail = f"/api/purchasing/purchase-orders/{draft['id']}"
    assert client.get(detail, headers=buyer).json() == draft
    assert client.get(detail, headers=sales).status_code == 403
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "purchase_order.created")).one()
    assert event.entity_id == draft["id"] and event.actor_user_id == draft["created_by"]


def test_stale_requisition(client, db, scenario):
    buyer, sales, url, _, _, order_path, body = scenario
    seed_supplier(db)
    seed_supplier(db)
    db.commit()
    supplier = db.scalars(select(Supplier)).one()
    assert len(db.scalars(select(SupplierProduct)).all()) == 2
    body.update(revision=2, reason="Customer doubled quantity")
    body["lines"][0]["quantity"] = 2
    edited = client.put(order_path, headers=sales, json=body)
    assert edited.status_code == 200, edited.text
    assert client.put(url, headers=buyer, json={"supplier_id": supplier.id}).status_code == 409
    assert db.scalar(select(PurchaseOrder.id)) is None
