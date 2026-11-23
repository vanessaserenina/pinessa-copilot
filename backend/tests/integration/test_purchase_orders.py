from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.modules.inventory.models import Inventory
from app.modules.purchasing.models import GoodsReceiptLine, PurchaseOrder, Supplier, SupplierProduct
from app.modules.purchasing.service import approve_purchase_order, receive_purchase_order
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


def test_manually_mark_approved_po_sent(client, db, approval_draft, scenario, make_user, headers_for):
    draft, admin_headers = approval_draft
    buyer, sales = scenario[:2]
    approver = headers_for(make_user("approver"))
    db.commit()
    path = f"/api/purchasing/purchase-orders/{draft['id']}"
    mark_sent = path + "/mark-sent"
    assert draft["sent_by"] is None and draft["sent_at"] is None
    assert client.post(mark_sent, headers=buyer).status_code == 409
    submitted = client.post(path + "/submit", headers=buyer)
    assert submitted.status_code == 200
    assert client.post(mark_sent, headers=admin_headers).status_code == 409
    approved = client.post(path + "/approve", headers=approver)
    assert approved.status_code == 200
    for headers in (sales, approver):
        assert client.post(mark_sent, headers=headers).status_code == 403
    assert client.get(path, headers=buyer).json() == approved.json()

    response = client.post(mark_sent, headers=buyer)
    assert response.status_code == 200, response.text
    sent = response.json()
    buyer_id = submitted.json()["submitted_by"]
    assert sent == dict(approved.json(), status="sent", sent_by=buyer_id, sent_at=sent["sent_at"])
    assert datetime.fromisoformat(sent["sent_at"]).utcoffset() is not None
    assert client.post(mark_sent, headers=admin_headers).status_code == 409
    assert client.get(path, headers=approver).json() == sent
    event = db.scalars(select(AuditEvent).where(
        AuditEvent.entity_type == "purchase_order", AuditEvent.entity_id == draft["id"],
        AuditEvent.action == "purchase_order.sent",
    )).one()
    assert event.actor_user_id == buyer_id and event.before == {"status": "approved"}
    assert event.after["status"] == "sent"
    assert datetime.fromisoformat(event.after["sent_at"]) == datetime.fromisoformat(sent["sent_at"])


@pytest.fixture()
def sent_po(client, db, approval_draft, scenario, make_user, headers_for):
    draft, _ = approval_draft
    buyer = scenario[0]
    receiver = make_user("warehouse")
    warehouse = headers_for(receiver)
    approver = headers_for(make_user("approver"))
    db.commit()
    path = f"/api/purchasing/purchase-orders/{draft['id']}"
    for action, headers in (("submit", buyer), ("approve", approver), ("mark-sent", buyer)):
        receipt = {"items": [{"product_id": scenario[3].id, "quantity": 1}]}
        assert client.put(f"{path}/receipts/{uuid4()}", json=receipt, headers=warehouse).status_code == 409
        assert client.post(f"{path}/{action}", headers=headers).status_code == 200
    return path, warehouse, receiver.id


def test_partial_and_full_goods_receipts(client, db, sent_po, scenario):
    path, warehouse, receiver_id = sent_po
    buyer, _, _, part, other, _, _ = scenario
    db.add(Inventory(product_id=part.id, available=4))
    db.commit()
    url = f"{path}/receipts/{uuid4()}"
    first = {"items": [{"product_id": part.id, "quantity": "0.25"}]}
    assert client.put(url, json=first, headers=buyer).status_code == 403
    for line, status in [({"product_id": other.id, "quantity": 3}, 409),
                         ({"product_id": other.id, "quantity": "0.5"}, 422),
                         ({"product_id": other.id, "quantity": 0}, 422),
                         ({"product_id": 2**62, "quantity": 1}, 422),
                         (first["items"][0], 422)]:
        invalid = {"items": [*first["items"], line]}
        assert client.put(url, json=invalid, headers=warehouse).status_code == status
    assert db.get(Inventory, part.id).available == 4
    assert db.scalar(select(GoodsReceiptLine.receipt_id)) is None

    partial = client.put(url, json=first, headers=warehouse)
    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "partially_received"
    assert {row["product_id"]: Decimal(row["quantity_received"]) for row in partial.json()["items"]} == {
        part.id: Decimal("0.25"), other.id: 0}
    assert client.put(url, json=first, headers=warehouse).json() == partial.json()
    assert client.put(url, json={"items": [{"product_id": part.id, "quantity": 1}]}, headers=warehouse).status_code == 409
    assert db.get(Inventory, part.id).available == Decimal("4.25")
    final_url = f"{path}/receipts/{uuid4()}"
    delivery = {"items": [{"product_id": other.id, "quantity": 2}, {"product_id": part.id, "quantity": "1.25"}]}
    full = client.put(final_url, json=delivery, headers=warehouse)
    assert full.status_code == 200, full.text
    assert full.json()["status"] == "received"
    assert all(Decimal(row["quantity_received"]) == Decimal(row["quantity"]) for row in full.json()["items"])
    assert client.put(final_url, json=delivery, headers=warehouse).json() == full.json()
    assert client.put(f"{path}/receipts/{uuid4()}", json=first, headers=warehouse).status_code == 409
    assert db.get(Inventory, part.id).available == Decimal("5.5")
    assert db.get(Inventory, other.id).available == 2
    lines = db.scalars(select(GoodsReceiptLine)).all()
    assert len(lines) == 3 and all(line.received_by == receiver_id for line in lines)
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "purchase_order.goods_received")
                        .order_by(AuditEvent.id)).all()
    assert [event.after["status"] for event in events] == ["partially_received", "received"]


def test_competing_full_deliveries(db, sent_po, approval_draft, scenario):
    po_id = approval_draft[0]["id"]
    receiver_id = sent_po[2]
    quantities = {scenario[3].id: Decimal("1.5"), scenario[4].id: Decimal(2)}
    db.commit()
    engine = db.get_bind()
    ready = Barrier(2, timeout=10)

    def receive(receipt_id):
        with Session(engine) as session:
            ready.wait()
            try:
                return receive_purchase_order(session, po_id, receipt_id, quantities, receiver_id).status
            except HTTPException as exc:
                return str(exc.status_code)

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(receive, [uuid4(), uuid4()])) == ["409", "received"]
    assert {row.product_id: row.available for row in db.scalars(select(Inventory))} == quantities
