"""Run the frozen synthetic intake benchmark against an explicitly selected local model."""
import json
import os
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import settings
from app.modules.ai.models import AIExtraction
from app.modules.ai.schemas import StructuredOrder
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


@pytest.fixture()
def products(db, new_product, monkeypatch, tmp_path):
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
    return products


@pytest.mark.parametrize("case_file", CASES, ids=lambda path: path.stem)
def test_pilot_order(case_file, db, client, make_user, headers_for, products):
    case = json.loads(case_file.read_text())
    expected = case["expected"]
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
    replies = db.get(AIExtraction, proposal["id"]).raw_output
    try:
        output = StructuredOrder.model_validate_json(replies[-1], strict=True).model_dump(mode="json") if replies else {}
    except ValidationError:
        output = {}
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
    result = {"case": case_file.stem, "attempts": len(replies), "fields": fields, "lines": len(lines),
              "observed": {"customer": (output.get("customer") or {}).get("name"),
                           "quantity": line.get("quantity"), "stated_sku": line.get("stated_sku"),
                           "requested_text": line.get("requested_text")},
              "expected": {key: expected[key] for key in ("customer", "quantity", "stated_sku")},
              "auto": auto_skus, "right_route": right_route,
              "wrong_auto": len(auto_skus) - int(expected["auto_sku"] in auto_skus)}
    evidence = matches[0]["source_quantity"] if len(matches) == 1 else None
    result["review_quantity"] = evidence["quantity"] if evidence else None
    result["review_quantity_correct"] = len(matches) == 1 and same_quantity(result["review_quantity"], expected["quantity"])
    result["confirmed"] = None
    review = proposal.get("review_headers") or {}
    result["review_customer_correct"] = review.get("customer_name") == expected["customer"]
    result["review_po_correct"] = review.get("po_number") == expected["po"]
    if expected["confirmed_sku"] is None:
        print("PILOT_RESULT " + json.dumps(result), flush=True)
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
    assert order["lines"][0]["quantity"] == int(expected["quantity"])
    assert order["lines"][0]["product_id"] == products[expected["confirmed_sku"]].id
    result["confirmed"] = [expected["confirmed_sku"], order["lines"][0]["quantity"]]
    print("PILOT_RESULT " + json.dumps(result), flush=True)


EXTENDED = json.loads((CASES[0].parent.parent / "pilot-extended.json").read_text())
SHADOW_DIR = CASES[0].parent.parent / "pilot-shadow"
SHADOW = json.loads((SHADOW_DIR / "batch.json").read_text())


@pytest.mark.parametrize("case", SHADOW, ids=lambda case: case["id"])
def test_shadow_headers(case, db, client, make_user, headers_for, products):
    headers = headers_for(make_user("sales"))
    if case["format"] == "pdf":
        payload = {"files": {"file": ("S03.pdf", (SHADOW_DIR / "S03.pdf").read_bytes())}}
    elif case["format"] == "csv":
        payload = {"files": {"file": ("S04.csv", case["source"].encode())}}
    else:
        payload = {"data": {"text": case["source"]}}
    response = client.post("/api/order-sources", headers=headers, **payload)
    assert response.status_code == 201
    url = f"/api/order-sources/{response.json()['id']}"
    source = client.post(url + "/extract-text", headers=headers).json()
    proposal = client.post(url + "/extract-order", headers=headers).json()
    review = proposal["review_headers"]
    row = db.get(AIExtraction, proposal["id"])
    raw = json.loads(row.raw_output[-1])
    skus = {product.id: sku for sku, product in products.items()}
    matches = proposal["line_matches"]
    print("SHADOW_RESULT " + json.dumps({
        "id": case["id"], "customer": raw["customer"]["name"], "po": raw["customer_po_number"],
        "review": review, "text": source["raw_text"],
        "raw_quantity": [line["quantity"] for line in raw["lines"]],
        "quantities": [m["source_quantity"] for m in matches],
        "selected": [skus.get(m["selected_product_id"]) for m in matches],
    }), flush=True)
    assert review["customer_name"] == case["customer"]
    if case["format"] == "pdf" and review["check_po"]:
        assert review["po_number"] is None
    else:
        assert review["po_number"] == case["po"]
    assert [skus.get(m["selected_product_id"]) for m in matches] == [line["auto"] for line in case["lines"]]
    for match, line in zip(matches, case["lines"], strict=True):
        evidence = match["source_quantity"] or {}
        assert (evidence.get("quantity"), evidence.get("unit")) == (line["quantity"], line["unit"])
    assert db.scalar(select(func.count()).select_from(Order)) == 0


def upload_payload(case):
    if case["format"] == "text":
        return {"data": {"text": case["source"]}}
    stream = BytesIO()
    if case["format"] == "xlsx":
        from openpyxl import Workbook
        book = Workbook()
        book.active.append([case["source"]])
        book.save(stream)
        book.close()
    else:
        from PIL import Image, ImageDraw, ImageFont
        page = Image.new("RGB", (1250, 240), "white")
        ImageDraw.Draw(page).multiline_text((30, 30), case["source"], fill="black",
                                            font=ImageFont.load_default(size=32), spacing=18)
        page.save(stream, format="PDF", resolution=144)
    return {"files": {"file": ("order." + case["format"], stream.getvalue())}}


@pytest.mark.parametrize("case", EXTENDED, ids=lambda case: case["case"])
def test_extended_pilot(case, db, client, make_user, headers_for, products):
    headers = headers_for(make_user("sales"))
    payload = upload_payload(case)
    response = client.post("/api/order-sources", headers=headers, **payload)
    assert response.status_code == 201, response.text
    url = f"/api/order-sources/{response.json()['id']}"
    if "repeat_upload" in case:
        repeat = client.post("/api/order-sources", headers=headers, **payload)
        assert repeat.status_code == case["repeat_upload"] and repeat.headers["location"] == url
    source = client.post(url + "/extract-text", headers=headers).json()
    assert source["status"] == "text_extracted", source
    proposal = client.post(url + "/extract-order", headers=headers).json()
    matches = proposal["line_matches"] or []
    skus = {p.id: sku for sku, p in products.items()}
    auto = [skus.get(m["selected_product_id"]) for m in matches]
    evidence = [(m["source_quantity"] or {}).get("quantity") for m in matches]
    raw = [line["quantity"] for line in (proposal["validated_output"] or {}).get("lines", [])]
    print("EXTENDED_RESULT " + json.dumps({"case": case["case"], "auto": auto,
          "review_quantity": evidence, "raw_quantity": raw, "source_text": source["raw_text"]}), flush=True)
    lines = [{"product_id": products[sku].id, "quantity": qty, "unit_price": "1.00"}
             for sku, qty in zip(case["expected_skus"], case["sales_quantities"], strict=True)]
    order = client.post(url + "/confirm", headers=headers, json={
        "customer": {"name": "TEST Larch Hotel", "code": "LARCH", "contact_name": None,
                     "email": None, "phone": None, "delivery_address": None},
        "customer_code": "LARCH", "currency": "SGD", "lines": lines})
    assert order.status_code == case["confirmation"], order.text
    assert [db.get(Inventory, line["product_id"]).available for line in lines] == case["available"]
    if order.status_code == 200:
        assert order.json()["status"] == "ready_for_delivery"
        assert [line["quantity"] for line in order.json()["lines"]] == case["sales_quantities"]
    else:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
    assert auto == case["expected_skus"]
    assert evidence == case["expected_quantities"]
