from hashlib import sha256
from pathlib import PureWindowsPath
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.orders import OrderDB
from app.core.config import settings
from app.modules.audit.service import record_audit_event
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.deliveries.models import Delivery, DeliveryDocument
from app.modules.deliveries.schemas import DeliveryOut, DocumentLink, DocumentOut, DocumentType, NextStatus, PlanDelivery
from app.modules.deliveries.service import plan_delivery, read_delivery, set_delivery_status
from app.modules.orders.service import read_order

router = APIRouter(prefix="/deliveries", tags=["deliveries"])
Manager = Annotated[User, Depends(require_permission("deliveries:manage"))]


@router.post("/orders/{order_id}", status_code=201)
def create(order_id: int, data: PlanDelivery, db: OrderDB, actor: Manager) -> DeliveryOut:
    return plan_delivery(db, order_id, data, actor.id)


@router.get("/orders/{order_id}")
def for_order(order_id: int, db: OrderDB, _: Manager) -> list[DeliveryOut]:
    read_order(db, order_id)
    return list(db.scalars(select(Delivery).where(Delivery.order_id == order_id).order_by(Delivery.id)))


@router.get("/{delivery_id}")
def detail(delivery_id: int, db: OrderDB, _: Manager) -> DeliveryOut:
    return read_delivery(db, delivery_id)


@router.put("/{delivery_id}/status/{status}")
def transition(delivery_id: int, status: NextStatus, db: OrderDB, actor: Manager) -> DeliveryOut:
    return set_delivery_status(db, delivery_id, status, actor.id)


def save_document(db, doc: DeliveryDocument):
    db.add(doc)
    db.flush()
    record_audit_event(db, actor_user_id=doc.uploaded_by, action="delivery.document_added",
                       entity_type="delivery", entity_id=doc.delivery_id,
                       detail={"document_id": doc.id, "document_type": doc.document_type})
    db.commit()
    return doc


@router.post("/{delivery_id}/documents/links", status_code=201)
def link(delivery_id: int, data: DocumentLink, db: OrderDB, actor: Manager) -> DocumentOut:
    read_delivery(db, delivery_id)
    if data.url.username or data.url.password:
        raise HTTPException(422, "Document links must omit embedded credentials.")
    return save_document(db, DeliveryDocument(delivery_id=delivery_id, uploaded_by=actor.id,
                                              **data.model_dump(mode="json")))


@router.post("/{delivery_id}/documents/uploads", status_code=201)
def upload(delivery_id: int, document_type: Annotated[DocumentType, Form()],
           file: Annotated[UploadFile, File()], db: OrderDB, actor: Manager) -> DocumentOut:
    read_delivery(db, delivery_id)
    name = PureWindowsPath(file.filename or "").name
    if not name or len(name) > 255 or any(ord(c) < 32 for c in name):
        raise HTTPException(422, "Provide a valid document filename of up to 255 characters.")
    storage = settings.delivery_storage
    storage.mkdir(parents=True, exist_ok=True)
    key = uuid4().hex
    path = storage / key
    digest = sha256()
    size = 0
    output = path.open("xb")
    try:
        with output:
            while chunk := file.file.read(64 * 1024):
                size += len(chunk)
                if size > settings.delivery_upload_limit:
                    raise HTTPException(413, "Document exceeds the upload size limit.")
                digest.update(chunk)
                output.write(chunk)
            if not size:
                raise HTTPException(422, "Document is empty.")
        return save_document(db, DeliveryDocument(delivery_id=delivery_id, uploaded_by=actor.id,
                             document_type=document_type, file_name=name, storage_key=key,
                             sha256=digest.hexdigest(), mime_type=(file.content_type or "application/octet-stream")[:255]))
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.get("/{delivery_id}/documents/{document_id}/download")
def download(delivery_id: int, document_id: int, db: OrderDB, _: Manager):
    doc = db.get(DeliveryDocument, document_id)
    if doc is None or delivery_id != doc.delivery_id or not doc.storage_key:
        raise HTTPException(404, "Uploaded delivery document not found.")
    path = settings.delivery_storage / doc.storage_key
    if not path.is_file():
        raise HTTPException(404, "Stored delivery document not found.")
    return FileResponse(path, filename=doc.file_name, media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff"})
