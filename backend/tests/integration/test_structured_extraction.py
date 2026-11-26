import json
from datetime import date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.ai import provider
from app.modules.ai.models import AIExtraction
from app.modules.ai.schemas import StructuredOrder
from app.modules.ai.service import extract_order
from app.modules.ai.source_checks import review_headers
from app.modules.audit.models import AuditEvent
from app.modules.orders.models import Order, OrderSource


TEXT = "Synthetic customer Acorn, PO TEST-42: 2.50 cases of blue clips, SKU CLIP-B. Deliver on 2027-03-12."
EXPECTED = {
    "customer": {"name": "Acorn", "code": None, "contact_name": None, "email": None,
                 "phone": None, "delivery_address": None},
    "customer_po_number": "TEST-42",
    "requested_delivery_date": "2027-03-12",
    "lines": [{"requested_text": "2.50 cases of blue clips, SKU CLIP-B", "stated_sku": "CLIP-B",
               "quantity": "2.50", "unit": "cases", "specification": "blue", "customization_notes": None}],
}


@pytest.fixture()
def db(migrated_db):
    return migrated_db


@pytest.fixture()
def source(client, make_user, headers_for, monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "synthetic-test-model")
    headers = headers_for(make_user("sales"))
    reply = client.post("/api/order-sources", data={"text": TEXT}, headers=headers)
    assert reply.status_code == 201
    url = f"/api/order-sources/{reply.json()['id']}"
    assert client.post(url + "/extract-text", headers=headers).status_code == 200
    return url, headers


@pytest.fixture()
def mock_llm(monkeypatch):
    def setup(*replies):
        calls = []

        def post(url, *, json, timeout, trust_env):
            assert url == f"http://127.0.0.1:{settings.llm_port}/v1/chat/completions"
            assert trust_env is False
            calls.append(json)
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            if isinstance(reply, httpx.Response):
                return reply
            reason, content = reply
            return httpx.Response(200, request=httpx.Request("POST", url), json={
                "choices": [{"finish_reason": reason, "message": {"content": content}}],
            })
        monkeypatch.setattr(provider.httpx, "post", post)
        return calls

    return setup


def test_shortened_pack_description_shows_original_quantity(client, db, new_product, make_user,
                                                            headers_for, mock_llm, monkeypatch):
    original = "2 cases of blue clips pack of 12, SKU B12-B"
    line = {"requested_text": "2 cases of blue clips", "stated_sku": "B12-B", "quantity": "2.0",
            "unit": "cases", "specification": None, "customization_notes": None}
    raw = json.dumps({"customer": dict.fromkeys(EXPECTED["customer"]), "customer_po_number": None,
                      "requested_delivery_date": None, "lines": [line]})
    monkeypatch.setattr(settings, "llm_model", "synthetic-test-model")
    mock_llm(("stop", raw))
    product = new_product(unit="box")
    product.sku = "B12-B"
    db.commit()
    headers = headers_for(make_user("sales"))
    source = client.post("/api/order-sources", data={"text": original}, headers=headers).json()
    url = f"/api/order-sources/{source['id']}"
    assert client.post(url + "/extract-text", headers=headers).status_code == 200
    proposal = client.post(url + "/extract-order", headers=headers).json()
    match, = proposal["line_matches"]
    assert match["selected_product_id"] is None and match["status"] == "needs_review"
    assert match["source_quantity"] == {"quantity": "2", "unit": "cases", "normalized_unit": "case",
                                        "source_clause": original}
    assert proposal["validated_output"]["lines"] == [line]
    assert db.get(AIExtraction, proposal["id"]).raw_output == [raw]


@pytest.mark.parametrize(("kind", "text", "name", "po", "check_po"), [
    ("text", "Customer: TEST Pine Lodge. PO PINE-2411. 7 cases of blue clips.",
     "TEST Pine Lodge", "PINE-2411", False),
    ("csv", "customer\tpo\torder_line\nTEST Maple House\t001-41\tRed clips",
     "TEST Maple House", "001-41", False),
    ("pdf", "Customer: TEST Elm Hotel. PO ELM-241 1.\n5 cases of blue clips.",
     "TEST Elm Hotel", None, True),
])
def test_review_headers_preserve_reply(client, db, source, mock_llm, kind, text, name, po, check_po):
    url, headers = source
    stored = db.get(OrderSource, int(url.rsplit("/", 1)[1]))
    stored.source_type, stored.raw_text = kind, text
    db.commit()
    wrong = {**EXPECTED, "customer": {**EXPECTED["customer"], "name": "Maple House" if kind == "csv" else "TEST"},
             "customer_po_number": "ELM-241" if kind == "pdf" else "PO PINE-2411"}
    raw = json.dumps(wrong)
    mock_llm(("stop", raw), ("stop", raw))
    for response in (client.post(url + "/extract-order", headers=headers),
                     client.get(url + "/extraction", headers=headers)):
        proposal = response.json()
        assert proposal["review_headers"] == {"customer_name": name, "customer_code": None, "po_number": po,
                                               "check_po": check_po}
        assert proposal["validated_output"] == wrong
        assert db.get(AIExtraction, proposal["id"]).raw_output[0] == raw
    if check_po:
        review = {"customer": EXPECTED["customer"], "customer_code": "ELM", "currency": "SGD",
                  "lines": [{"product_id": 1, "quantity": 5, "unit_price": "1"}]}
        blocked = client.post(url + "/confirm", headers=headers, json=review)
        assert blocked.status_code == 422 and "original source" in blocked.json()["detail"]


@pytest.mark.parametrize(("kind", "text", "name", "po", "check"), [
    ("text", "Customer: Acorn, PO pending.\nPO will follow.", "Acorn", "TEST-42", False),
    ("text", "Customer: Acorn & Co.\r\nPO: AB.001/4", "Acorn & Co.", "AB.001/4", False),
    ("image", "PO: TEST-42\nPO: TEST-43", "Acorn", None, True),
    ("csv", "customer\tpo\nAcorn\tAA\nBirch\tBB", "Acorn", "TEST-42", False),
])
def test_header_evidence_boundaries(kind, text, name, po, check):
    headers = review_headers(kind, text, StructuredOrder.model_validate(EXPECTED))
    assert (headers.customer_name, headers.po_number, headers.check_po) == (name, po, check)


def test_structured_proposal_from_stored_text(client, db, source, mock_llm):
    url, headers = source
    calls = mock_llm(("stop", json.dumps(EXPECTED)))
    result = client.post(url + "/extract-order", headers=headers)
    assert result.status_code == 200, result.text
    proposal = result.json()
    assert proposal["validated_output"] == EXPECTED
    assert proposal["status"] == "needs_review" and proposal["schema_valid"]
    assert proposal["error_message"] is None
    assert proposal["model_name"] == "synthetic-test-model"
    assert proposal["prompt_version"] == provider.PROMPT_VERSION
    assert client.get(url + "/extraction", headers=headers).json() == proposal
    assert client.post(url + "/extract-order", headers=headers).json() == proposal
    assert len(calls) == 1
    assert calls[0]["messages"][1] == {"role": "user", "content": TEXT}
    assert calls[0]["response_format"]["json_schema"]["schema"] == StructuredOrder.model_json_schema()
    assert db.get(OrderSource, proposal["order_source_id"]).raw_text == TEXT
    assert db.get(AIExtraction, proposal["id"]).raw_output == [json.dumps(EXPECTED)]
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "ai_extraction.needs_review"))
    assert audit.entity_id == proposal["id"] and audit.detail["attempts"] == 1


@pytest.mark.parametrize(("case_name", "corrected", "invented_sku"), [
    ("text-only", True, None), ("unknown-sku", True, None),
    ("approved-alias", True, "desk fasteners"), ("approved-alias", False, "desk fasteners"),
    ("approved-alias", True, "null"), ("approved-alias", False, "null")])
def test_observed_pilot_errors_get_a_source_based_retry(case_name, corrected, invented_sku, client, db, make_user,
                                                        headers_for, mock_llm, new_product, monkeypatch, tmp_path):
    from app.modules.products.models import ProductAlias

    case = json.loads((Path(__file__).resolve().parents[3] /
                       f"data/synthetic/pilot/{case_name}.json").read_text())
    expected = case["expected"]
    monkeypatch.setattr(settings, "llm_model", "synthetic-test-model")
    monkeypatch.setattr(settings, "order_source_storage", tmp_path)
    headers = headers_for(make_user("sales"))
    if case["format"] == "csv":
        payload = {"files": {"file": ("test.csv", case["source"].encode(), "text/csv")}}
        wording = ("6 cases of red clips." if case_name == "text-only" else "5 cases of blue clips")
    else:
        payload = {"data": {"text": case["source"]}}
        wording = "3 cases of desk fasteners"
        product = new_product(unit="case")
        product.sku = "CLIP-B"
        db.add(ProductAlias(product_id=product.id, alias_text="desk fasteners",
                            alias_normalized="desk fasteners", status="approved"))
        db.commit()
    uploaded = client.post("/api/order-sources", headers=headers, **payload)
    url = f"/api/order-sources/{uploaded.json()['id']}"
    assert client.post(url + "/extract-text", headers=headers).status_code == 200
    line = {**EXPECTED["lines"][0], "requested_text": wording, "quantity": expected["quantity"],
            "stated_sku": expected["stated_sku"], "specification": None}
    correct = {**EXPECTED, "customer": {**EXPECTED["customer"], "name": expected["customer"]},
               "lines": [line]}
    observed = ({**correct, "customer": {**correct["customer"], "name": "Acorn Hotel"}}
                if case["format"] == "csv" else {**correct, "lines": [{**line, "stated_sku": invented_sku}]})
    if invented_sku == "null":
        observed["lines"][0].update(specification="null", customization_notes="null")
    final = correct if corrected else observed
    replies = [json.dumps(observed), json.dumps(final)]
    calls = mock_llm(*[("stop", reply) for reply in replies])
    result = client.post(url + "/extract-order", headers=headers).json()
    assert len(calls) == 2
    schema = calls[1]["response_format"]["json_schema"]["schema"]
    if case["format"] == "csv":
        assert schema["properties"]["lines"]["const"] == [line]
        assert schema["$defs"]["Customer"]["properties"]["name"]["const"] == expected["customer"]
    else:
        fields = schema["$defs"]["RequestedLine"]["properties"]
        assert fields["quantity"]["const"] == expected["quantity"]
        assert "const" not in fields["stated_sku"]
        if invented_sku == "null":
            assert "const" not in fields["specification"]
            assert "const" not in fields["customization_notes"]
    assert db.get(AIExtraction, result["id"]).raw_output == replies
    assert result["validated_output"] == final
    assert result["status"] == "needs_review"
    if case_name == "approved-alias" and corrected:
        assert result["line_matches"][0]["selected_product_id"] == product.id
    else:
        assert result["line_matches"][0]["selected_product_id"] is None


@pytest.mark.parametrize("sku", ["null", "CLIP-X"])
def test_stated_unknown_sku_overrides_approved_alias(sku, client, db, source, mock_llm, new_product):
    from app.modules.products.models import ProductAlias

    product = new_product(unit="case")
    db.add(ProductAlias(product_id=product.id, alias_text="desk fasteners",
                        alias_normalized="desk fasteners", status="approved"))
    url, headers = source
    stored = db.get(OrderSource, int(url.rsplit("/", 1)[1]))
    stored.raw_text = f"3 cases of desk fasteners, SKU {sku}."
    db.commit()
    line = {**EXPECTED["lines"][0], "requested_text": "3 cases of desk fasteners",
            "quantity": "3", "stated_sku": sku, "specification": None}
    raw = json.dumps({**EXPECTED, "lines": [line]})
    calls = mock_llm(("stop", raw))
    proposal = client.post(url + "/extract-order", headers=headers).json()
    assert len(calls) == 1
    assert proposal["line_matches"][0]["selected_product_id"] is None
    assert proposal["validated_output"]["lines"][0]["stated_sku"] == sku


@pytest.mark.parametrize("bad", [
    ("stop", "{bad json"),
    ("stop", json.dumps({**EXPECTED, "unexpected": True})),
    ("stop", json.dumps({**EXPECTED, "requested_delivery_date": "2026-02-30"})),
    ("stop", json.dumps({**EXPECTED, "lines": [{**EXPECTED["lines"][0], "quantity": True}]})),
    ("length", json.dumps(EXPECTED)),
])
def test_invalid_or_incomplete_reply_gets_one_retry(client, db, source, mock_llm, bad):
    url, headers = source
    calls = mock_llm(bad, ("stop", json.dumps(EXPECTED)))
    reply = client.post(url + "/extract-order", headers=headers).json()
    assert reply["validated_output"] == EXPECTED and reply["schema_valid"]
    assert len(calls) == 2
    assert calls[1]["messages"][0]["content"] != calls[0]["messages"][0]["content"]
    assert db.get(AIExtraction, reply["id"]).raw_output == [bad[1], json.dumps(EXPECTED)]


def test_failed_validation_routes_to_manual_queue(client, source, mock_llm):
    url, headers = source
    calls = mock_llm(("stop", "{}"), ("stop", '{"lines": []}'))
    reply = client.post(url + "/extract-order", headers=headers).json()
    assert reply["status"] == "needs_manual_handling" and not reply["schema_valid"]
    assert reply["validated_output"] is None and reply["error_message"]
    assert client.get("/api/order-sources/failed-extractions", headers=headers).json() == [reply["order_source_id"]]
    assert client.post(url + "/extract-order", headers=headers).json() == reply
    assert len(calls) == 2


def test_context_window_failure_queues_source_once(client, db, source, mock_llm):
    url, headers = source
    calls = mock_llm(httpx.Response(400, json={"error": {"type": "exceed_context_size_error"}}))
    response = client.post(url + "/extract-order", headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "needs_manual_handling" and not result["schema_valid"]
    assert result["validated_output"] is None and "context window" in result["error_message"]
    assert client.get("/api/order-sources/failed-extractions", headers=headers).json() == [result["order_source_id"]]
    assert client.post(url + "/extract-order", headers=headers).json() == result
    assert len(calls) == 1
    assert db.get(AIExtraction, result["id"]).raw_output == []
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "ai_extraction.needs_manual_handling"))
    assert audit.detail["attempts"] == 1


@pytest.mark.parametrize("outage", [
    httpx.ConnectError("offline"),
    httpx.Response(503, request=httpx.Request("POST", "http://localhost")),
])
def test_unavailable_provider_allows_later_retry(client, db, source, mock_llm, monkeypatch, outage):
    url, headers = source
    with monkeypatch.context() as config:
        config.setattr(settings, "llm_model", None)
        assert client.post(url + "/extract-order", headers=headers).status_code == 503
    mock_llm(outage)
    assert client.post(url + "/extract-order", headers=headers).status_code == 503
    assert db.scalar(select(func.count()).select_from(AIExtraction)) == 0
    mock_llm(("stop", json.dumps(EXPECTED)))
    assert client.post(url + "/extract-order", headers=headers).json()["schema_valid"]


def test_permissions_and_source_readiness(client, make_user, headers_for, mock_llm):
    calls = mock_llm()
    headers = headers_for(make_user("sales"))
    source_id = client.post("/api/order-sources", data={"text": TEXT}, headers=headers).json()["id"]
    url = f"/api/order-sources/{source_id}"
    warehouse = headers_for(make_user("warehouse"))
    assert client.post(url + "/extract-order").status_code == 401
    assert client.post(url + "/extract-order", headers=warehouse).status_code == 403
    assert client.get(url + "/extraction", headers=warehouse).status_code == 403
    assert client.post(url + "/extract-order", headers=headers).status_code == 409
    assert client.get(url + "/extraction", headers=headers).status_code == 404
    assert client.post("/api/order-sources/999999/extract-order", headers=headers).status_code == 404
    assert calls == []


def test_concurrent_submissions_share_one_proposal(db, source, mock_llm):
    url, _ = source
    source_id = int(url.rsplit("/", 1)[1])
    actor_id = db.get(OrderSource, source_id).submitted_by
    engine = db.get_bind()
    calls = mock_llm(("stop", json.dumps(EXPECTED)))
    ready = Barrier(2)

    def submit():
        with Session(engine) as session:
            ready.wait(timeout=10)
            return extract_order(session, source_id, actor_id).id

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(submit)
        second = workers.submit(submit)
        assert first.result() == second.result()
    assert len(calls) == 1

CLIP_FIXTURE = json.loads((Path(__file__).resolve().parents[3] /
                          "data/synthetic/clip-b-expected.json").read_text())


@pytest.mark.parametrize("scenario", CLIP_FIXTURE["scenarios"])
def test_clip_variant_from_original_wording(client, db, source, mock_llm, new_product, scenario):
    catalogue_sku = scenario["catalogue_sku"]
    colour = scenario["colour"]
    accepted = scenario["expected_status"] == "auto"
    product = new_product(unit="case")
    product.sku = catalogue_sku
    product.name = f"{colour.title()} clips"
    product.attributes = {"colour": colour}
    db.commit()
    line = CLIP_FIXTURE["requested_line"]
    mock_llm(("stop", json.dumps({**EXPECTED, "lines": [line]})))
    url, headers = source
    response = client.post(url + "/extract-order", headers=headers)
    assert response.status_code == 200, response.text
    match, = response.json()["line_matches"]
    assert match["requested_text"] == line["requested_text"]
    assert match["method"] == scenario["expected_method"]
    assert match["normalized_unit"] == "case"
    assert match["candidates"][0]["product_id"] == product.id
    assert match["status"] == ("auto" if accepted else "needs_review")
    assert match["selected_product_id"] == (product.id if accepted else None)
    assert client.get(url + "/extraction", headers=headers).json()["line_matches"] == [match]


def test_review_corrections_confirmation_and_retry(client, db, source, mock_llm, new_product):
    from app.modules.inventory.models import Inventory

    product = new_product()
    product.name = "Blue clips"
    db.commit()
    mock_llm(("stop", json.dumps(EXPECTED)))
    url, headers = source
    proposal = client.post(url + "/extract-order", headers=headers).json()
    assert proposal["line_matches"][0]["selected_product_id"] is None
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    review = {"customer": {**EXPECTED["customer"], "code": "ACORN"}, "customer_code": "ACORN",
              "customer_po_number": "TEST-CORRECTED", "requested_delivery_date": (date.today() + timedelta(days=30)).isoformat(),
              "currency": "SGD", "lines": [{"product_id": product.id, "quantity": 25,
              "unit_price": "1.20", "specification": "blue", "customization_notes": None}]}
    # No BOM or stock. A failed confirmation must also roll back draft creation.
    failure = client.post(url + "/confirm", headers=headers, json=review)
    assert failure.status_code == 422, failure.text
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    db.add(Inventory(product_id=product.id, available=50))
    db.commit()
    response = client.post(url + "/confirm", headers=headers, json=review)
    assert response.status_code == 200, response.text
    order = response.json()
    assert order["customer_po_number"] == "TEST-CORRECTED"
    assert order["status"] == "ready_for_delivery" and order["lines"][0]["quantity"] == 25
    assert client.post(url + "/confirm", headers=headers, json=review).json()["id"] == order["id"]
    assert db.get(Inventory, product.id).available == 25
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    audit = db.scalars(select(AuditEvent).where(AuditEvent.action == "ai_extraction.confirmed")).one()
    assert audit.detail["order_id"] == order["id"] and audit.entity_id == proposal["id"]
    assert audit.detail["reviewed"]["lines"][0]["specification"] == "blue"
    assert client.get(url + "/extraction", headers=headers).json()["confirmed_order_id"] == order["id"]


def test_simultaneous_conflicting_quantities(client, db, source, mock_llm, new_product,
                                            make_user, headers_for, monkeypatch):
    from sqlalchemy import event
    from app.core.database import get_db
    from app.main import app
    from app.modules.inventory.models import Inventory, Reservation

    url, first_headers = source
    source_id = int(url.rsplit("/", 1)[1])
    first_id = db.get(OrderSource, source_id).submitted_by
    second = make_user("sales")
    second_headers = headers_for(second)
    product = new_product(unit="case")
    product.sku = "CLIP-B"
    db.add(Inventory(product_id=product.id, available=50))
    db.commit()
    mock_llm(("stop", json.dumps(EXPECTED)))
    proposal = client.post(url + "/extract-order", headers=first_headers)
    assert proposal.status_code == 200
    proposal_id = proposal.json()["id"]
    product_id = product.id
    engine = db.get_bind()
    ready = Barrier(2, timeout=30)

    def request_session():
        with Session(engine) as session:
            yield session

    def meet_at_proposal_lock(conn, cursor, statement, parameters, context, executemany):
        if "ai_extractions" in statement and "FOR UPDATE" in statement:
            ready.wait()

    def confirm(user_id, headers, quantity):
        review = {"customer": EXPECTED["customer"], "customer_code": "ACORN", "currency": "SGD",
                  "lines": [{"product_id": product_id, "quantity": quantity, "unit_price": "1.20"}]}
        response = client.post(url + "/confirm", headers=headers, json=review)
        return response.status_code, user_id, quantity, response.json()

    assert first_id != second.id
    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    event.listen(engine, "before_cursor_execute", meet_at_proposal_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(confirm, first_id, first_headers, 25)
            other = pool.submit(confirm, second.id, second_headers, 12)
            winner, loser = sorted([first.result(), other.result()])
    finally:
        event.remove(engine, "before_cursor_execute", meet_at_proposal_lock)

    assert (winner[0], loser[0]) == (200, 409)
    _, actor_id, quantity, response = winner
    order_id = response["id"]
    assert response["status"] == "ready_for_delivery"
    assert response["lines"][0]["quantity"] == quantity
    assert loser[3] == {"detail": f"This proposal was already confirmed as order #{order_id}."}
    with Session(engine) as check:
        order = check.scalars(select(Order)).one()
        line, = order.items
        assert (order.id, line.product_id, line.quantity) == (order_id, product_id, quantity)
        assert check.get(AIExtraction, proposal_id).confirmed_order_id == order_id
        reservation = check.scalars(select(Reservation)).one()
        assert (reservation.order_id, reservation.product_id, reservation.quantity, reservation.user_id) == (
            order_id, product_id, quantity, actor_id)
        available = check.get(Inventory, product_id).available
        assert available == 50 - quantity
        audit = check.scalars(select(AuditEvent).where(AuditEvent.action == "ai_extraction.confirmed")).one()
        assert (audit.entity_id, audit.actor_user_id) == (proposal_id, actor_id)
        assert (audit.detail["source_id"], audit.detail["order_id"]) == (source_id, order_id)
        reviewed_line, = audit.detail["reviewed"]["lines"]
        assert (reviewed_line["product_id"], reviewed_line["quantity"]) == (product_id, quantity)
        print(f"Order {order_id}: user {actor_id} confirmed {quantity} (200), user {loser[1]} "
              f"submitted {loser[2]} (409). Reservation {reservation.id}, stock 50 -> {available}, "
              f"confirmation audit {audit.id}.")


def test_competing_orders_retry_after_stock_receipt(client, db, mock_llm, new_product,
                                                   make_user, headers_for, monkeypatch):
    from decimal import Decimal
    from sqlalchemy import event
    from app.core.database import get_db
    from app.main import app
    from app.modules.inventory.models import Inventory, Reservation

    monkeypatch.setattr(settings, "llm_model", "synthetic-test-model")
    product = new_product(unit="case")
    product.sku = "CLIP-B"
    product_id = product.id
    warehouse = headers_for(make_user("warehouse"))
    receipt_url = f"/api/inventory/{product_id}/receipts"
    receipt = client.post(receipt_url, headers=warehouse, json={"quantity": 10})
    assert receipt.status_code == 200 and Decimal(receipt.json()["available"]) == 10

    sales = {}
    for code in ("ACORN", "BIRCH"):
        user = make_user("sales")
        headers = headers_for(user)
        po = f"{code}-100"
        line = "8 cases of blue clips, SKU CLIP-B"
        customer = {**EXPECTED["customer"], "name": code}
        extracted = {**EXPECTED, "customer": customer, "customer_po_number": po,
                     "requested_delivery_date": None, "lines": [
                         {**EXPECTED["lines"][0], "requested_text": line, "quantity": "8"}]}
        mock_llm(("stop", json.dumps(extracted)))
        source = client.post("/api/order-sources", headers=headers,
                             data={"text": f"Customer: {code}\nPO: {po}\n{line}"})
        assert source.status_code == 201
        source_id = source.json()["id"]
        url = f"/api/order-sources/{source_id}"
        assert client.post(url + "/extract-text", headers=headers).status_code == 200
        proposal = client.post(url + "/extract-order", headers=headers)
        assert proposal.status_code == 200
        review = {"customer": customer, "customer_code": code, "customer_po_number": po,
                  "currency": "SGD", "lines": [{"product_id": product_id, "quantity": 8,
                                                "unit_price": "1.20"}]}
        sales[po] = (user.id, source_id, proposal.json()["id"], headers, review)

    engine = db.get_bind()

    def request_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    ready = Barrier(2, timeout=30)
    stock_lock_sessions = []

    def meet_at_stock_lock(conn, cursor, statement, parameters, context, executemany):
        if "FROM inventory" in statement and "FOR UPDATE" in statement:
            stock_lock_sessions.append(conn)
            ready.wait()

    def confirm(po):
        _, source_id, _, headers, review = sales[po]
        return client.post(f"/api/order-sources/{source_id}/confirm", headers=headers, json=review)

    event.listen(engine, "before_cursor_execute", meet_at_stock_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {po: pool.submit(confirm, po) for po in sales}
            responses = {po: future.result() for po, future in futures.items()}
    finally:
        event.remove(engine, "before_cursor_execute", meet_at_stock_lock)

    assert len(set(stock_lock_sessions)) == 2
    winner, held = sorted(sales, key=lambda po: responses[po].status_code)
    assert responses[winner].status_code == 200, responses[winner].text
    assert responses[held].status_code == 422, responses[held].text
    assert responses[held].json()["detail"] == (
        "8 cases requested for CLIP-B. 2 available. 6 more needed. "
        "Retry after Warehouse receives the missing stock.")

    def verify_state(confirmed, available):
        with Session(engine) as check:
            assert check.get(Inventory, product_id).available == available
            orders = check.scalars(select(Order)).all()
            assert len(orders) == len(confirmed)
            reservations = check.scalars(select(Reservation)).all()
            audits = check.scalars(select(AuditEvent).where(
                AuditEvent.action == "ai_extraction.confirmed")).all()
            assert len(reservations) == len(audits) == len(confirmed)
            for po, response in confirmed.items():
                actor, source_id, proposal_id, _, _ = sales[po]
                body = response.json()
                order_id = body["id"]
                order, = [row for row in orders if row.id == order_id]
                assert body["status"] == order.status == "ready_for_delivery"
                assert body["customer_po_number"] == order.customer_po_number == po
                item, = order.items
                response_item, = body["lines"]
                assert item.product_id == response_item["product_id"] == product_id
                assert item.quantity == response_item["quantity"] == 8
                proposal = check.get(AIExtraction, proposal_id)
                assert (proposal.status, proposal.confirmed_order_id) == ("confirmed", order_id)
                reservation, = [r for r in reservations if r.order_id == order_id]
                assert (reservation.product_id, reservation.quantity, reservation.user_id) == (product_id, 8, actor)
                audit, = [a for a in audits if a.entity_id == proposal_id]
                assert (audit.actor_user_id, audit.detail["source_id"], audit.detail["order_id"]) == (
                    actor, source_id, order_id)
                reviewed, = audit.detail["reviewed"]["lines"]
                assert (reviewed["product_id"], reviewed["quantity"]) == (product_id, 8)
            for po in sales.keys() - confirmed.keys():
                proposal = check.get(AIExtraction, sales[po][2])
                assert (proposal.status, proposal.confirmed_order_id) == ("needs_review", None)

    confirmed = {winner: responses[winner]}
    verify_state(confirmed, 2)
    receipt = client.post(receipt_url, headers=warehouse, json={"quantity": 6})
    assert receipt.status_code == 200 and Decimal(receipt.json()["available"]) == 8
    retry = confirm(held)
    assert retry.status_code == 200, retry.text
    confirmed[held] = retry
    verify_state(confirmed, 0)
    print(f"{winner}: 200 order {responses[winner].json()['id']}; {held}: 422, "
          f"then 200 order {retry.json()['id']}. Stock 10 -> 2 -> 8 -> 0.")


def test_invented_quantity_requires_sales_entry(client, db, mock_llm, new_product, make_user,
                                                headers_for, monkeypatch):
    from app.modules.inventory.models import Inventory

    monkeypatch.setattr(settings, "llm_model", "synthetic-test-model")
    product = new_product(unit="case")
    product.sku, product.name = "CLIP-B", "Blue clips"
    db.add(Inventory(product_id=product.id, available=10))
    db.commit()
    headers = headers_for(make_user("sales"))
    text = "PO TEST-7. Delivery 2027-07-07. Blue clips, SKU CLIP-B."
    source = client.post("/api/order-sources", headers=headers, data={"text": text}).json()
    url = f"/api/order-sources/{source['id']}"
    assert client.post(url + "/extract-text", headers=headers).json()["raw_text"] == text
    line = {**EXPECTED["lines"][0], "requested_text": "Blue clips, SKU CLIP-B", "quantity": "7"}
    raw = json.dumps({**EXPECTED, "lines": [line]})
    mock_llm(("stop", raw))
    proposal = client.post(url + "/extract-order", headers=headers).json()
    match, = proposal["line_matches"]
    assert match["selected_product_id"] == product.id
    assert match["source_quantity"] is None
    assert db.get(AIExtraction, proposal["id"]).raw_output == [raw]
    reviewed = {"product_id": product.id, "unit_price": "1"}
    payload = {"customer": EXPECTED["customer"], "customer_code": "ACORN", "currency": "SGD",
               "lines": [reviewed]}
    for invalid in ({}, {"quantity": None}, {"quantity": ""}, {"quantity": 0},
                    {"quantity": -1}, {"quantity": 2.5}):
        response = client.post(url + "/confirm", headers=headers,
                               json={**payload, "lines": [{**reviewed, **invalid}]})
        assert response.status_code == 422
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    assert db.get(Inventory, product.id).available == 10
    reviewed["quantity"] = 3
    reply = client.post(url + "/confirm", headers=headers, json=payload)
    assert reply.status_code == 200, reply.text
    assert reply.json()["lines"][0]["quantity"] == 3
    assert db.get(Inventory, product.id).available == 7
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "ai_extraction.confirmed")).one()
    assert event.detail["reviewed"]["lines"][0]["quantity"] == 3


@pytest.mark.skipif(not settings.llm_model, reason="Requires a configured local model")
def test_live_synthetic_review(client, db, make_user, headers_for, new_product):
    from app.modules.inventory.models import Inventory

    product = new_product(unit="case")
    product.sku = "CLIP-B"
    product.name = "Blue clips"
    product.attributes = {"colour": "blue"}
    db.add(Inventory(product_id=product.id, available=10))
    db.commit()
    headers = headers_for(make_user("sales"))
    uploaded = client.post("/api/order-sources", data={"text": TEXT}, headers=headers)
    assert uploaded.status_code == 201
    url = f"/api/order-sources/{uploaded.json()['id']}"
    assert client.post(url + "/extract-text", headers=headers).status_code == 200
    response = client.post(url + "/extract-order", headers=headers)
    assert response.status_code == 200, response.text
    proposal = response.json()
    extracted = proposal["validated_output"]
    assert extracted["customer_po_number"] == "TEST-42"
    assert extracted["requested_delivery_date"] == "2027-03-12"
    assert extracted["lines"][0]["stated_sku"] == product.sku
    assert proposal["line_matches"][0]["selected_product_id"] == product.id, (
        proposal["line_matches"][0]["reason"], extracted["lines"][0])
    review = {"customer": {**extracted["customer"], "code": "ACORN"}, "customer_code": "ACORN",
              "customer_po_number": "TEST-42-CORRECTED", "requested_delivery_date": (date.today() + timedelta(days=30)).isoformat(),
              "currency": "SGD", "lines": [{"product_id": product.id, "quantity": 3, "unit_price": "1.20"}]}
    result = client.post(url + "/confirm", headers=headers, json=review)
    assert result.status_code == 200, result.text
    order = client.get(f"/api/orders/{result.json()['id']}", headers=headers).json()
    assert order["status"] == "ready_for_delivery"
    assert order["lines"][0]["quantity"] == 3 and order["customer_po_number"] == "TEST-42-CORRECTED"
    audit = db.scalars(select(AuditEvent).where(AuditEvent.action == "ai_extraction.confirmed")).one()
    assert audit.detail["order_id"] == order["id"] and audit.entity_id == proposal["id"]


@pytest.mark.parametrize(("approval", "family_alias", "accepted"), [
    ("approved", False, True), ("approved", True, True), ("suggested", False, False),
])
def test_only_approved_alias_can_select(client, db, source, mock_llm, new_product,
                                        approval, family_alias, accepted):
    from app.modules.products.models import ProductAlias

    product = new_product(unit="case")
    target = {"family_id": product.family_id} if family_alias else {"product_id": product.id}
    db.add(ProductAlias(**target, alias_text="Office clips", alias_normalized="office clips", status=approval))
    db.commit()
    line = {**EXPECTED["lines"][0], "requested_text": "2.50 Cases of OFFICE clips",
            "stated_sku": None, "specification": None}
    mock_llm(("stop", json.dumps({**EXPECTED, "lines": [line]})))
    url, headers = source
    match, = client.post(url + "/extract-order", headers=headers).json()["line_matches"]
    assert match["selected_product_id"] == (product.id if accepted else None)
    if accepted:
        assert match["method"] == "alias"


@pytest.mark.parametrize("count", [0, 101])
def test_unconfirmable_proposal_routes_to_manual_handling(client, source, mock_llm, count):
    url, headers = source
    output = {**EXPECTED, "lines": EXPECTED["lines"] * count}
    calls = mock_llm(("stop", json.dumps(output)))
    reply = client.post(url + "/extract-order", headers=headers).json()
    assert reply["validated_output"] == output
    assert reply["status"] == "needs_manual_handling" and reply["line_matches"] is None
    assert "1 to 100" in reply["error_message"]
    assert client.get("/api/order-sources/pending-review", headers=headers).json() == []
    assert client.get("/api/order-sources/failed-extractions", headers=headers).json() == [reply["order_source_id"]]
    assert client.post(url + "/extract-order", headers=headers).json() == reply and len(calls) == 1

@pytest.mark.parametrize("ambiguous", [False, True])
def test_shadow_order_customer_and_quantity(client, db, headers_for, make_user, mock_llm, monkeypatch, ambiguous):
    from app.modules.orders.models import CustomerPriceTier

    description = "8 boxes of paket penutup kepala kotak individual / polos box 100 set"
    sku = "KIT-SHOWER-CAP-KTK-NLOGO-BX100"
    name = "Hotel Gantari Utama"
    po = "SHADOW-001"
    original = "\r\n".join(["Hello Pinessa,", "", f"Customer: {name}", f"PO number: {po}",
                            "Requested delivery date: 27 November 2026", "", f"Please send {description}.",
                            f"SKU: {sku}", "", "Thank you."])
    for code in (["CUS-0270", "CUS-0271"] if ambiguous else ["CUS-0270"]):
        db.add(CustomerPriceTier(customer_code=code, customer_name=name, tier="Hotel Standard"))
    db.commit()
    monkeypatch.setattr(settings, "llm_model", "test")
    raw = json.dumps(dict(EXPECTED, customer=dict(EXPECTED["customer"], name=name, code=po),
                          customer_po_number=po, requested_delivery_date="2026-11-27",
                          lines=[dict(EXPECTED["lines"][0], requested_text=description, stated_sku=sku,
                                      quantity="8", unit="box")]))
    mock_llm(("stop", raw))
    headers = headers_for(make_user("sales"))
    saved = client.post("/api/order-sources", data={"text": original}, headers=headers).json()
    path = f"/api/order-sources/{saved['id']}"
    assert client.post(path + "/extract-text", headers=headers).status_code == 200
    proposal = client.post(path + "/extract-order", headers=headers).json()
    assert proposal["review_headers"]["customer_code"] == (None if ambiguous else "CUS-0270")
    assert proposal["review_headers"]["po_number"] == po
    assert proposal["line_matches"][0]["source_quantity"]["quantity"] == "8"
    extraction = db.get(AIExtraction, proposal["id"])
    assert extraction.raw_output == [raw]
    assert db.get(OrderSource, saved["id"]).raw_text == original
