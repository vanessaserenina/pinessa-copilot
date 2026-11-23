import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from threading import Barrier
from zipfile import ZipFile

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartParser

from app.core.config import settings
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.orders.intake import receive_source
from app.modules.orders.models import OrderSource

URL = "/api/order-sources"


@pytest.fixture()
def db(migrated_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "order_source_storage", tmp_path)
    return migrated_db


def test_pasted_original_and_duplicate(client, db, make_user, headers_for):
    headers = headers_for(make_user())
    original = "  Synthetic order – 2 units\r\n"
    response = client.post(URL, data={"text": original}, headers=headers)
    assert response.status_code == 201
    source = response.json()
    assert source["raw_text"] == original
    assert source["status"] == "received"
    assert db.get(OrderSource, source["id"]).content_hash == sha256(original.encode()).hexdigest()
    duplicate = client.post(URL, data={"text": original}, headers=headers)
    assert duplicate.status_code == 409
    assert client.get(duplicate.headers["Location"], headers=headers).json() == source
    assert client.post(URL, data={"text": original.strip()}, headers=headers).status_code == 201


def workbook():
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return stream.getvalue()


@pytest.mark.parametrize(("filename", "data", "kind"), [
    ("order.pdf", b"%PDF-1.4\nsynthetic sample", "pdf"),
    ("order.xlsx", workbook(), "xlsx"),
    ("order.png", b"\x89PNG\r\n\x1a\nsynthetic", "image"),
    ("order.jpeg", b"\xff\xd8\xffsynthetic", "image"),
])
def test_upload_original_and_renamed_duplicate(client, db, tmp_path, make_user, headers_for,
                                               filename, data, kind):
    user = make_user()
    headers = headers_for(user)
    created = client.post(URL, files={"file": ("../" + filename, data)}, headers=headers)
    assert created.status_code == 201
    source = created.json()
    assert source["source_type"] == kind
    assert source["original_filename"] == filename
    assert source["submitted_by"] == user.id
    assert client.get(f"{URL}/{source['id']}/file", headers=headers).content == data
    duplicate = client.post(URL, files={"file": ("copy-" + filename, data)}, headers=headers)
    assert duplicate.status_code == 409
    assert duplicate.headers["Location"] == f"{URL}/{source['id']}"
    assert len(list(tmp_path.iterdir())) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent)
                     .where(AuditEvent.action == "order_source.received")) == 1


def test_invalid_uploads_and_access(client, db, tmp_path, make_user, headers_for, monkeypatch):
    headers = headers_for(make_user())
    monkeypatch.setattr(settings, "order_source_upload_limit", 512)
    for payload, status in [
        ({}, 422), ({"data": {"text": "  "}}, 422),
        ({"files": {"file": ("order.pdf", b"")}}, 422),
        ({"files": {"file": ("order.xlsx", b"not Excel")}}, 415),
        ({"files": {"file": ("order.txt", b"test")}}, 415),
        ({"files": {"file": ("order.pdf", b"x" * 513)}}, 413),
        ({"data": {"text": "x" * 513}}, 413),
        ({"data": {"text": "A"}, "files": {"file": ("a.pdf", b"%PDF-")}}, 422),
    ]:
        assert client.post(URL, headers=headers, **payload).status_code == status
    assert client.post(URL, data={"text": "test"}).status_code == 401
    forbidden = headers_for(make_user("warehouse"))
    assert client.post(URL, headers=forbidden, data={"text": "test"}).status_code == 403
    assert client.get(f"{URL}/1/file", headers=forbidden).status_code == 403
    assert db.scalar(select(func.count()).select_from(OrderSource)) == 0
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(("declared_length", "chunks_expected"), [
    ("65", 0), (None, 2), ("32", 2),
])
def test_oversized_request_stops_before_multipart_parser(monkeypatch, declared_length, chunks_expected):
    monkeypatch.setattr(settings, "order_source_upload_limit", 64)
    monkeypatch.setattr(MultiPartParser, "parse", lambda self: pytest.fail("multipart parser ran"))
    consumed = []

    async def body():
        for chunk in (b"a" * 64, b"b", b"remaining"):
            consumed.append(chunk)
            yield chunk

    async def submit():
        headers = {"Content-Type": "multipart/form-data; boundary=order"}
        if declared_length is not None:
            headers["Content-Length"] = declared_length
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            return await client.post(URL, headers=headers, content=body())

    assert asyncio.run(submit()).status_code == 413
    assert len(consumed) == chunks_expected


def test_multipart_at_request_limit_is_preserved(client, make_user, headers_for, monkeypatch):
    headers = headers_for(make_user())
    data = b"%PDF-synthetic order"
    upload = httpx.Request("POST", URL, files={"file": ("order.pdf", data)})
    body = upload.read()
    monkeypatch.setattr(settings, "order_source_upload_limit", len(body))
    response = client.post(URL, content=body, headers={
        **headers, "Content-Type": upload.headers["Content-Type"],
    })
    assert response.status_code == 201
    assert client.get(f"{URL}/{response.json()['id']}/file", headers=headers).content == data


def test_simultaneous_identical_files(db, tmp_path, make_user):
    actor_id = make_user().id
    db.commit()
    engine = db.get_bind()
    ready = Barrier(2)

    def submit():
        with Session(engine) as session:
            ready.wait(timeout=10)
            try:
                receive_source(session, actor_id, b"%PDF-synthetic", filename="order.pdf")
            except HTTPException as exc:
                return exc.status_code
            return 201

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(lambda _: submit(), range(2))) == [201, 409]
    assert db.scalar(select(func.count()).select_from(OrderSource)) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_failed_audit_removes_original_and_allows_retry(db, tmp_path, make_user, monkeypatch):
    actor_id = make_user().id
    db.commit()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    with monkeypatch.context() as patch:
        patch.setattr("app.modules.orders.intake.record_audit_event", fail_audit)
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            receive_source(db, actor_id, b"%PDF-synthetic", filename="order.pdf")
    assert db.scalar(select(func.count()).select_from(OrderSource)) == 0
    assert not list(tmp_path.iterdir())
    source = receive_source(db, actor_id, b"%PDF-synthetic", filename="retry.pdf")
    assert (tmp_path / source.storage_key).read_bytes() == b"%PDF-synthetic"


def test_extract_pasted_source_once(client, db, make_user, headers_for):
    headers = headers_for(make_user())
    text = "  Order sample\r\n12 units. "
    source = client.post(URL, data={"text": text}, headers=headers).json()
    endpoint = f"{URL}/{source['id']}/extract-text"
    assert client.post(endpoint).status_code == 401
    assert client.post(endpoint, headers=headers_for(make_user("warehouse"))).status_code == 403
    for _ in range(2):
        response = client.post(endpoint, headers=headers)
        assert response.status_code == 200
        assert response.json()["raw_text"] == text
        assert response.json()["status"] == "text_extracted"
    assert db.scalar(select(func.count()).select_from(AuditEvent)
                     .where(AuditEvent.action == "order_source.text_extracted")) == 1


def spreadsheet_order():
    from openpyxl import Workbook

    book = Workbook()
    book.active.title = "Order"
    book.active.append(["SKU", "Qty"])
    book.active.append(["SYN-001", 0])
    book.create_sheet("Empty")
    book.create_sheet("Notes").append(["Gift box"])
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


@pytest.mark.parametrize(("filename", "contents", "expected"), [
    ("order.csv", b'\xef\xbb\xbfSKU,Qty\r\n"Box, blue",0\r\n', "SKU\tQty\nBox, blue\t0\n"),
    ("order.xlsx", spreadsheet_order(), "[Order]\nSKU\tQty\nSYN-001\t0\n[Notes]\nGift box\n"),
])
def test_extract_spreadsheet_rows(client, make_user, headers_for, filename, contents, expected):
    headers = headers_for(make_user())
    upload = client.post(URL, files={"file": (filename, contents)}, headers=headers)
    assert upload.status_code == 201
    endpoint = f"{URL}/{upload.json()['id']}"
    assert client.post(endpoint + "/extract-text", headers=headers).json()["raw_text"] == expected
    assert client.get(endpoint, headers=headers).json()["status"] == "text_extracted"
    assert client.get(endpoint + "/file", headers=headers).content == contents


def test_failed_source_queue_and_retry(client, db, make_user, headers_for, tmp_path):
    headers = headers_for(make_user())
    data = b"SKU,Qty\nSYN,42\n"
    source_id = client.post(URL, files={"file": ("order.csv", data)}, headers=headers).json()["id"]
    endpoint = f"{URL}/{source_id}/extract-text"
    path = tmp_path / db.get(OrderSource, source_id).storage_key
    path.unlink()
    failure = client.post(endpoint, headers=headers).json()
    assert failure["status"] == "failed_extraction" and failure["raw_text"] is None
    assert client.get(URL + "/failed-extractions", headers=headers).json() == [source_id]
    path.write_bytes(data)
    assert client.post(endpoint, headers=headers).json()["status"] == "text_extracted"
    assert client.get(URL + "/failed-extractions", headers=headers).json() == []


def test_unreadable_pdf_keeps_original_for_review(client, make_user, headers_for):
    headers = headers_for(make_user())
    contents = b"%PDF-truncated"
    source_id = client.post(URL, files={"file": ("order.pdf", contents)}, headers=headers).json()["id"]
    endpoint = f"{URL}/{source_id}"
    assert client.post(endpoint + "/extract-text", headers=headers).json()["status"] == "failed_extraction"
    assert client.get(endpoint + "/file", headers=headers).content == contents


@pytest.mark.parametrize("format_name", ["PNG", "JPEG"])
def test_extract_stored_image_with_ocr(client, make_user, headers_for, format_name):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (780, 160), "white")
    ImageDraw.Draw(image).text((35, 40), "ORDER 12345", fill="black",
                               font=ImageFont.load_default(size=58))
    stream = BytesIO()
    image.save(stream, format=format_name)
    headers = headers_for(make_user())
    uploaded = client.post(URL, files={"file": (f"order.{format_name.lower()}", stream.getvalue())},
                           headers=headers)
    assert uploaded.status_code == 201
    endpoint = f"{URL}/{uploaded.json()['id']}"
    converted = client.post(endpoint + "/extract-text", headers=headers)
    assert converted.status_code == 200
    assert converted.json()["status"] == "text_extracted"
    assert "ORDER12345" in converted.json()["raw_text"].replace(" ", "")
    assert client.get(endpoint + "/file", headers=headers).content == stream.getvalue()
