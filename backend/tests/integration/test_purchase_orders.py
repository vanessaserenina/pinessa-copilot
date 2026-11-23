from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.modules.purchasing.models import PurchaseOrder, Supplier, SupplierProduct
from app.modules.purchasing.service import approve_purchase_order
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


@pytest.fixture()
def approval_draft(client, db, scenario, make_user, headers_for):
    creator = make_user("admin")
    creator_headers = headers_for(creator)
    seed_supplier(db)
    db.commit()
    supplier_id = db.scalars(select(Supplier.id)).one()
    response = client.put(scenario[2], json={"supplier_id": supplier_id}, headers=creator_headers)
    assert response.status_code == 200, response.text
    return response.json(), creator_headers


def test_independent_po_approval(client, db, approval_draft, scenario, make_user, headers_for):
    draft, creator_headers = approval_draft
    buyer, sales = scenario[:2]
    submitter = make_user("admin")
    submitter_headers = headers_for(submitter)
    approver = make_user("approver")
    approver_headers = headers_for(approver)
    db.commit()
    path = f"/api/purchasing/purchase-orders/{draft['id']}"
    assert client.get(path, headers=approver_headers).json() == draft
    assert client.post(path + "/approve", headers=approver_headers).status_code == 409
    for headers in (sales, approver_headers):
        assert client.post(path + "/submit", headers=headers).status_code == 403
    submitted = client.post(path + "/submit", headers=submitter_headers)
    assert submitted.status_code == 200, submitted.text
    pending = dict(draft, status="pending_approval", submitted_by=submitter.id)
    assert submitted.json() == pending
    assert client.post(path + "/submit", headers=buyer).status_code == 409
    for headers in (buyer, sales, creator_headers, submitter_headers):
        assert client.post(path + "/approve", headers=headers).status_code == 403
    assert client.get(path, headers=buyer).json() == pending
    response = client.post(path + "/approve", headers=approver_headers)
    assert response.status_code == 200, response.text
    approved = dict(pending, status="approved", approved_by=approver.id)
    assert response.json() == approved
    assert client.post(path + "/approve", headers=approver_headers).status_code == 409
    assert client.post(path + "/submit", headers=buyer).status_code == 409
    assert client.get(path, headers=buyer).json() == approved
    events = db.execute(select(AuditEvent.action, AuditEvent.actor_user_id).where(
        AuditEvent.entity_type == "purchase_order", AuditEvent.entity_id == draft["id"],
    ).order_by(AuditEvent.id)).all()
    assert events == [("purchase_order.created", draft["created_by"]), ("purchase_order.submitted", submitter.id),
                      ("purchase_order.approved", approver.id)]


def test_competing_approvers(client, db, approval_draft, scenario, make_user):
    draft, _ = approval_draft
    path = f"/api/purchasing/purchase-orders/{draft['id']}"
    assert client.post(path + "/submit", headers=scenario[0]).status_code == 200
    actors = [make_user("approver").id for _ in range(2)]
    db.commit()
    engine = db.get_bind()
    ready = Barrier(2, timeout=10)

    def approve(actor_id):
        with Session(engine) as session:
            ready.wait()
            try:
                approve_purchase_order(session, draft["id"], actor_id)
            except HTTPException as exc:
                return exc.status_code
            return 200

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(approve, actors))
    assert sorted(results) == [200, 409]
    winner = actors[results.index(200)]
    assert client.get(path, headers=scenario[0]).json()["approved_by"] == winner
    assert db.scalars(select(AuditEvent.actor_user_id).where(AuditEvent.action == "purchase_order.approved")).one() == winner
