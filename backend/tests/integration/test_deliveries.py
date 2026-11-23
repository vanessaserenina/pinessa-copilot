from decimal import Decimal
from hashlib import sha256

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.inventory.models import Inventory, Reservation


@pytest.fixture
def db(migrated_db):
    return migrated_db


@pytest.fixture
def shipment(client, db, make_user, headers_for, new_product, make_bom):
    warehouse = headers_for(make_user("warehouse"))
    sales = headers_for(make_user("sales"))
    kit, part = new_product(), new_product("COMPONENT")
    make_bom(kit, [(part, "2", "0")])
    db.add(Inventory(product_id=part.id, available=6))
    db.commit()
    data = {"customer_code": "TEST-SHIP", "currency": "SGD",
            "lines": [{"product_id": kit.id, "quantity": 3, "unit_price": "1"}]}
    order_id = client.post("/api/orders", json=data, headers=sales).json()["id"]
    order = f"/api/orders/{order_id}"
    assert client.post(order + "/confirm", json={"revision": 1}, headers=sales).status_code == 200
    collection = f"/api/deliveries/orders/{order_id}"
    plan = {"revision": 2, "delivery_number": "TEST-D1", "address_snapshot": "TEST warehouse",
            "planned_date": "2030-01-01", "items": [{"line_no": 1, "quantity": 2}]}
    assert client.post(collection, json=plan, headers=warehouse).status_code == 409
    job, = client.put(f"/api/production/orders/{order_id}/revisions/2/jobs", headers=warehouse).json()
    progress = client.put(f"/api/production/jobs/{job['id']}/progress", headers=warehouse,
                          json={"quantity_completed": 1})
    assert progress.status_code == 200, progress.text
    assert client.get(order, headers=sales).json()["status"] == "in_production"
    assert client.put(f"/api/production/jobs/{job['id']}/complete", headers=warehouse).json()["status"] == "completed"
    assert client.post(collection, json=plan, headers=sales).status_code == 403
    created = client.post(collection, json=plan, headers=warehouse)
    assert created.status_code == 201, created.text
    return order_id, kit.id, warehouse, sales, data, plan, f"/api/deliveries/{created.json()['id']}"


def test_completed_job_through_partial_delivery(client, db, shipment, tmp_path, monkeypatch):
    oid, kit, warehouse, sales, data, plan, first = shipment
    monkeypatch.setattr(settings, "delivery_storage", tmp_path)
    collection = f"/api/deliveries/orders/{oid}"
    order = f"/api/orders/{oid}"

    def reserved():
        return db.scalar(select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
            Reservation.order_id == oid, Reservation.product_id == kit))

    assert reserved() == 3
    assert client.post(collection, headers=warehouse, json={**plan, "delivery_number": "TEST-D2"}).status_code == 409
    def transition(path, status):
        return client.put(path + "/status/" + status, headers=warehouse)

    assert transition(first, "dispatched").status_code == 409
    assert transition(first, "ready").json()["status"] == "ready"
    assert client.put(first + "/status/dispatched", headers=sales).status_code == 403
    dispatched = transition(first, "dispatched").json()
    assert dispatched["dispatched_at"] and dispatched["items"][0]["quantity"] == 2
    assert transition(first, "dispatched").json() == dispatched
    assert reserved() == 1
    assert client.put(order, headers=sales, json={**data, "revision": 2, "reason": "Edit"}).status_code == 409
    assert transition(first, "delivered").json()["delivered_at"]
    assert client.get(order, headers=sales).json()["status"] == "partially_dispatched"

    payload = {**plan, "delivery_number": "TEST-D2", "items": [{"line_no": 1, "quantity": 1}]}
    second_id = client.post(collection, json=payload, headers=warehouse).json()["id"]
    second = f"/api/deliveries/{second_id}"
    assert transition(second, "ready").status_code == 200
    assert transition(second, "dispatched").status_code == 200
    assert client.get(order, headers=sales).json()["status"] == "dispatched"
    assert transition(second, "delivered").status_code == 200
    assert client.get(order, headers=sales).json()["status"] == "delivered"
    assert reserved() == 0 and db.get(Inventory, kit).available == Decimal(0)
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "delivery.dispatched")) == 2

    note = b"TEST signed delivery note"
    files = {"file": ("note.txt", note, "text/plain")}
    form = {"document_type": "delivery_note"}
    uploads = first + "/documents/uploads"
    assert client.post(uploads, headers=sales, data=form, files=files).status_code == 403
    uploaded = client.post(uploads, headers=warehouse, data=form, files=files)
    assert uploaded.status_code == 201, uploaded.text
    doc = uploaded.json()
    assert doc["sha256"] == sha256(note).hexdigest() and doc["file_name"] == "note.txt"
    download = f"/documents/{doc['id']}/download"
    assert client.get(first + download, headers=warehouse).content == note
    assert client.get(first + download, headers=sales).status_code == 403
    assert client.get(second + download, headers=warehouse).status_code == 404
    invoice = {"document_type": "invoice", "file_name": "invoice.pdf", "url": "https://example.org/test-invoice.pdf"}
    links = first + "/documents/links"
    assert client.post(links, headers=warehouse, json={**invoice, "url": "file:///local"}).status_code == 422
    assert client.post(links, headers=warehouse, json=invoice).status_code == 201
    docs = client.get(first, headers=warehouse).json()["documents"]
    assert docs[0] == doc and docs[1]["url"] == invoice["url"] and docs[1]["document_type"] == "invoice"
    assert len(client.get(collection, headers=warehouse).json()) == 2
    monkeypatch.setattr(settings, "delivery_upload_limit", 4)
    assert client.post(uploads, headers=warehouse, data=form, files=files).status_code == 413
    assert len(list(tmp_path.iterdir())) == 1


def test_revision_supersedes_unshipped_delivery(client, shipment):
    oid, _, warehouse, sales, data, _, delivery = shipment
    edited = client.put(f"/api/orders/{oid}", headers=sales, json={**data, "revision": 2, "reason": "Replan"})
    assert edited.status_code == 200, edited.text
    snapshot = client.get(delivery, headers=warehouse).json()
    assert snapshot["status"] == "superseded" and snapshot["items"][0]["quantity"] == 2
    assert client.put(delivery + "/status/ready", headers=warehouse).status_code == 409


def test_warehouse_dashboard_totals(client, db, shipment):
    from app.modules.deliveries.models import Delivery
    from app.modules.production.models import ProductionJob

    warehouse = shipment[2]
    groups = client.get("/api/dashboard", headers=warehouse).json()["groups"]
    completed = db.scalar(select(func.count()).select_from(ProductionJob)
                          .where(ProductionJob.completed_at.is_not(None)))
    pending = db.scalar(select(func.count()).select_from(Delivery).where(Delivery.status == "pending"))
    assert groups["Production jobs"] == {"completed": completed} and completed == 1
    assert groups["Deliveries"] == {"pending": pending} and pending == 1
