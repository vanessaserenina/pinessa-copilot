from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditEvent
from app.modules.inventory.models import Inventory


@pytest.fixture()
def db(migrated_db):
    return migrated_db


def test_draft_from_confirmed_shortage(client, db, make_user, headers_for, new_product, make_bom):
    finished = new_product()
    short, covered = new_product("COMPONENT", unit="kg"), new_product("COMPONENT")
    make_bom(finished, [(short, "1.25", "0"), (covered, "1", "0")])
    db.add_all([Inventory(product_id=short.id, available=1), Inventory(product_id=covered.id, available=2)])
    sales = headers_for(make_user("sales"))
    buyer = make_user("purchasing")
    buyer_headers = headers_for(buyer)
    db.commit()

    order = client.post("/api/orders", headers=sales, json={
        "customer_code": "TEST-BUY", "currency": "SGD",
        "lines": [{"product_id": finished.id, "quantity": 2, "unit_price": "5"}],
    })
    assert order.status_code == 201, order.text
    order_id = order.json()["id"]
    path = f"/api/purchasing/orders/{order_id}/revisions"
    assert client.put(path + "/1/requisition", headers=buyer_headers).status_code == 409
    confirmed = client.post(f"/api/orders/{order_id}/confirm", headers=sales, json={"revision": 1})
    assert confirmed.status_code == 200, confirmed.text
    assert client.put(path + "/1/requisition", headers=buyer_headers).status_code == 409
    url = path + "/2/requisition"
    assert client.put(url, headers=sales).status_code == 403
    created = client.put(url, headers=buyer_headers)
    assert created.status_code == 200, created.text
    draft = created.json()
    assert draft["source_order_id"] == order_id and draft["source_revision"] == 2
    assert draft["status"] == "draft" and draft["created_by"] == buyer.id
    assert len(draft["items"]) == 1
    line = draft["items"][0]
    assert line["component_product_id"] == short.id and Decimal(line["quantity_needed"]) == Decimal("1.5")
    detail = f"/api/purchasing/requisitions/{draft['id']}"
    assert client.get(detail, headers=sales).status_code == 403
    assert client.get(detail, headers=buyer_headers).json() == draft
    assert client.put(url, headers=buyer_headers).json() == draft
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "requisition.created")).one()
    assert event.actor_user_id == buyer.id and event.entity_id == draft["id"]
