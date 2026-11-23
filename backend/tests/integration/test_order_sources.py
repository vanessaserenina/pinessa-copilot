from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from threading import Barrier
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
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
    monkeypatch.setattr(settings, "order_source_upload_limit", 12)
    for payload, status in [
        ({}, 422), ({"data": {"text": "  "}}, 422),
        ({"files": {"file": ("order.pdf", b"")}}, 422),
        ({"files": {"file": ("order.xlsx", b"not Excel")}}, 415),
        ({"files": {"file": ("order.txt", b"test")}}, 415),
        ({"files": {"file": ("order.pdf", b"x" * 13)}}, 413),
        ({"data": {"text": "x" * 13}}, 413),
        ({"data": {"text": "A"}, "files": {"file": ("a.pdf", b"%PDF-")}}, 422),
    ]:
        assert client.post(URL, headers=headers, **payload).status_code == status
    assert client.post(URL, data={"text": "test"}).status_code == 401
    forbidden = headers_for(make_user("warehouse"))
    assert client.post(URL, headers=forbidden, data={"text": "test"}).status_code == 403
    assert client.get(f"{URL}/1/file", headers=forbidden).status_code == 403
    assert db.scalar(select(func.count()).select_from(OrderSource)) == 0
    assert not list(tmp_path.iterdir())


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
