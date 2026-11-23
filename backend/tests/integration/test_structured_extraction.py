import json
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
