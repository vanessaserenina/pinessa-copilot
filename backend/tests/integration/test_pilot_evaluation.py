"""Run the frozen synthetic intake benchmark against an explicitly selected local model."""
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.inventory.models import Inventory
from app.modules.orders.models import Order
from app.modules.products.models import ProductAlias

CASES = sorted((Path(__file__).resolve().parents[3] / "data/synthetic/pilot").glob("*.json"))
pytestmark = pytest.mark.skipif(os.getenv("PINESSA_PILOT") != "1", reason="Opt-in measured local-model benchmark")


@pytest.fixture()
def db(migrated_db):
    return migrated_db


def same_quantity(actual, expected):
    if actual is None or expected is None:
        return actual == expected
    return Decimal(actual) == Decimal(expected)


@pytest.mark.parametrize("case_file", CASES, ids=lambda path: path.stem)
def test_pilot_order(case_file, db, client, make_user, headers_for, new_product, monkeypatch, tmp_path):
    case = json.loads(case_file.read_text())
    expected = case["expected"]
    monkeypatch.setattr(settings, "order_source_storage", tmp_path)
    products = {}
    for sku, colour in [("CLIP-B", "blue"), ("CLIP-R", "red")]:
        product = new_product(unit="case")
        product.sku = sku
        product.name = f"{colour.title()} clips"
        product.attributes = {"colour": colour}
        db.add(Inventory(product_id=product.id, available=20))
        products[sku] = product
    db.add(ProductAlias(product_id=products["CLIP-B"].id, alias_text="desk fasteners",
                        alias_normalized="desk fasteners", status="approved"))
    db.commit()
    headers = headers_for(make_user("sales"))
    if case["format"] == "csv":
        payload = {"files": {"file": (case_file.stem + ".csv", case["source"].encode(), "text/csv")}}
    else:
        payload = {"data": {"text": case["source"]}}
    response = client.post("/api/order-sources", headers=headers, **payload)
    assert response.status_code == 201, response.text
    source_id = response.json()["id"]
    path = f"/api/order-sources/{source_id}"
    assert client.post(path + "/extract-text", headers=headers).status_code == 200
    response = client.post(path + "/extract-order", headers=headers)
    assert response.status_code == 200, response.text
    proposal = response.json()
    output = proposal["validated_output"] or {}
    lines = output.get("lines", [])
    line = lines[0] if len(lines) == 1 else {}
    matches = proposal["line_matches"] or []
    selected = [m["selected_product_id"] for m in matches if m["selected_product_id"] is not None]
    auto_skus = [sku for product_id in selected for sku, p in products.items() if p.id == product_id]
    fields = {
        "customer": (output.get("customer") or {}).get("name") == expected["customer"],
        "po": output.get("customer_po_number") == expected["po"],
        "delivery_date": output.get("requested_delivery_date") == expected["delivery_date"],
        "quantity": same_quantity(line.get("quantity"), expected["quantity"]),
        "stated_sku": line.get("stated_sku") == expected["stated_sku"],
    }
    # Missing or extra lines cannot earn extraction or routing credit for a missing field.
    if not output:
        fields = dict.fromkeys(fields, False)
    if len(lines) != 1:
        fields.update(quantity=False, stated_sku=False)
    right_route = (len(matches) == 1 and
                   auto_skus == ([expected["auto_sku"]] if expected["auto_sku"] else []))
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    result = {"case": case_file.stem, "fields": fields, "lines": len(lines),
              "auto": auto_skus, "right_route": right_route,
              "wrong_auto": len(auto_skus) - int(expected["auto_sku"] in auto_skus)}
    print("PILOT_RESULT " + json.dumps(result), flush=True)
    if expected["confirmed_sku"] is None:
        return
    # Simulate Sales verifying the source and submitting the agreed fields and SKU.
    reviewed = {"customer": {"name": expected["customer"], "code": "TEST-ACORN",
                             "contact_name": None, "email": None, "phone": None, "delivery_address": None},
                "customer_code": "TEST-ACORN", "customer_po_number": expected["po"],
                "requested_delivery_date": expected["delivery_date"], "currency": "SGD",
                "lines": [{"product_id": products[expected["confirmed_sku"]].id,
                           "quantity": int(expected["quantity"]), "unit_price": "1.00"}]}
    reply = client.post(path + "/confirm", headers=headers, json=reviewed)
    assert reply.status_code == 200, reply.text
    order = reply.json()
    assert order["status"] == expected["order_status"]
    assert Decimal(order["stock"]["finished_goods"][0]["shortfall"]) == expected["shortfall"]
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "ai_extraction.confirmed")).one()
    assert event.detail["source_id"] == source_id and event.detail["order_id"] == order["id"]
