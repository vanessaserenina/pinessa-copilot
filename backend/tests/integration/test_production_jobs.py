from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.core.database import get_db
from app.main import app
from app.modules.inventory.models import Inventory, Reservation


@pytest.fixture()
def db(migrated_db):
    return migrated_db


def test_job_plan_follows_order_revision(client, db, make_user, headers_for, new_product, make_bom):
    sales = headers_for(make_user("sales"))
    warehouse = headers_for(make_user("warehouse"))
    product, covered = new_product(), new_product()
    part = new_product("COMPONENT")
    bom = make_bom(product, [(part, "1.5", "10")])
    db.add_all([Inventory(product_id=product.id, available=2),
                Inventory(product_id=covered.id, available=1)])
    db.commit()
    data = {"customer_code": "TEST-JOB", "currency": "SGD", "lines": [
        {"product_id": product.id, "quantity": 5, "unit_price": "1"},
        {"product_id": covered.id, "quantity": 1, "unit_price": "1"},
    ]}
    created = client.post("/api/orders", headers=sales, json=data)
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    order = f"/api/orders/{order_id}"
    revisions = f"/api/production/orders/{order_id}/revisions"
    assert client.put(revisions + "/1/jobs", headers=warehouse).status_code == 409
    confirmed = client.post(order + "/confirm", headers=sales, json={"revision": 1})
    assert confirmed.json()["status"] == "awaiting_materials"
    path = revisions + "/2/jobs"
    assert client.put(path, headers=sales).status_code == 403
    assert client.get(path, headers=sales).status_code == 403
    planned = client.put(path, headers=warehouse)
    assert planned.status_code == 200, planned.text
    job, = planned.json()
    assert job["product_id"] == product.id and job["bom_id"] == bom.id
    assert job["status"] == "planned" and Decimal(job["quantity_planned"]) == 3
    line, = job["components"]
    assert line["component_product_id"] == part.id and Decimal(line["quantity_required"]) == 5

    bom.status = "retired"
    db.flush()
    replacement = make_bom(product, [(part, "2", "0")], version=2)
    db.commit()
    assert client.put(path, headers=warehouse).json() == [job]
    assert len(db.scalars(select(AuditEvent).where(AuditEvent.action == "production_job.planned")).all()) == 1

    edited = client.put(order, headers=sales, json={**data, "revision": 2, "reason": "Update assembly plan"})
    assert edited.status_code == 200, edited.text
    assert client.put(path, headers=warehouse).status_code == 409
    stale = f"/api/production/jobs/{job['id']}"
    assert client.put(stale + "/complete", headers=warehouse).status_code == 409
    assert client.put(stale + "/progress", headers=warehouse, json={"quantity_completed": 1}).status_code == 409
    assert client.get(path, headers=warehouse).json() == [{**job, "status": "superseded"}]
    revised = revisions + "/3/jobs"
    replacement.status = "retired"
    db.flush()
    make_bom(product, [(part, "3", "0")], version=3)
    db.commit()
    assert client.put(revised, headers=warehouse).status_code == 409
    assert client.get(revised, headers=warehouse).json() == []


def test_completion_posts_stock_once(client, db, make_user, headers_for, new_product, make_bom, monkeypatch):
    sales = headers_for(make_user("sales"))
    warehouse = headers_for(make_user("warehouse"))
    kit, other = new_product(), new_product()
    shared, extra = new_product("COMPONENT"), new_product("COMPONENT")
    make_bom(kit, [(shared, "2", "0"), (extra, "1", "0")])
    make_bom(other, [(shared, "2", "0")])
    db.add_all([Inventory(product_id=shared.id, available=4), Inventory(product_id=kit.id, available=1)])
    db.commit()
    created = client.post("/api/orders", headers=sales, json={
        "customer_code": "TEST-ASSEMBLY", "currency": "SGD",
        "lines": [{"product_id": kit.id, "quantity": 3, "unit_price": "1"},
                  {"product_id": other.id, "quantity": 1, "unit_price": "1"}]})
    order_id = created.json()["id"]
    order = f"/api/orders/{order_id}"
    assert client.post(order + "/confirm", headers=sales, json={"revision": 1}).status_code == 200
    jobs = client.put(f"/api/production/orders/{order_id}/revisions/2/jobs", headers=warehouse).json()
    paths = {job["product_id"]: f"/api/production/jobs/{job['id']}" for job in jobs}
    progress = paths[kit.id] + "/progress"
    complete = paths[kit.id] + "/complete"

    def reserved(product):
        return db.scalar(select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
            Reservation.product_id == product.id, Reservation.order_id == order_id))

    assert client.put(complete, headers=sales).status_code == 403
    assert client.put(progress, headers=sales, json={"quantity_completed": 1}).status_code == 403
    assert client.put(progress, headers=warehouse, json={"quantity_completed": "0.5"}).status_code == 422
    assert client.put(progress, headers=warehouse, json={"quantity_completed": 2}).status_code == 422
    recorded = client.put(progress, headers=warehouse, json={"quantity_completed": 1}).json()
    assert recorded["status"] == "in_progress" and Decimal(recorded["quantity_completed"]) == 1
    assert client.put(progress, headers=warehouse, json={"quantity_completed": 1}).json() == recorded
    assert client.put(progress, headers=warehouse, json={"quantity_completed": 0}).status_code == 409
    assert client.put(complete, headers=warehouse).status_code == 409
    assert reserved(shared) == 4 and reserved(kit) == 1
    assert client.post(f"/api/inventory/{extra.id}/receipts", headers=warehouse,
                       json={"quantity": 2}).status_code == 200

    # Independent request sessions reach the same order lock before either can post stock.
    pool = db.get_bind()
    def request_session():
        with Session(pool) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    ready = Barrier(2, timeout=10)
    def at_order_lock(conn, cursor, statement, parameters, context, executemany):
        if "FROM orders" in statement and "FOR UPDATE" in statement:
            ready.wait()

    event.listen(pool, "before_cursor_execute", at_order_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(client.put, complete, headers=warehouse) for _ in range(2)]
            responses = [future.result() for future in futures]
    finally:
        event.remove(pool, "before_cursor_execute", at_order_lock)
    assert [r.status_code for r in responses] == [200, 200]
    posted = responses[0].json()
    assert posted == responses[1].json() == client.put(complete, headers=warehouse).json()
    assert posted["status"] == "completed" and Decimal(posted["quantity_completed"]) == 2
    assert posted["started_at"] == recorded["started_at"] and posted["completed_at"]
    assert client.put(progress, headers=warehouse, json={"quantity_completed": 1}).status_code == 409
    db.expire_all()
    assert reserved(kit) == 3 and db.get(Inventory, kit.id).available == 0
    assert reserved(shared) == 0 and db.get(Inventory, shared.id).available == 0
    assert db.get(Inventory, extra.id).available == 0
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "production_job.completed")).all()
    assert len(events) == 1
    assert client.get(order, headers=sales).json()["status"] != "ready_for_delivery"
    assert client.post(f"/api/inventory/{shared.id}/receipts", headers=warehouse,
                       json={"quantity": 3}).status_code == 200
    assert client.put(paths[other.id] + "/complete", headers=warehouse).status_code == 200
    db.expire_all()
    assert db.get(Inventory, shared.id).available == 1 and reserved(other) == 1
    assert client.get(order, headers=sales).json()["status"] == "ready_for_delivery"
