from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.audit.service import record_audit_event
from app.modules.orders.models import OrderSource


def file_type(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    signatures = {
        ".pdf": ("pdf", b"%PDF-"),
        ".png": ("image", b"\x89PNG\r\n\x1a\n"),
        ".jpg": ("image", b"\xff\xd8\xff"),
        ".jpeg": ("image", b"\xff\xd8\xff"),
    }
    if suffix in signatures:
        kind, signature = signatures[suffix]
        if data.startswith(signature):
            return kind
    if suffix == ".xlsx":
        try:
            with ZipFile(BytesIO(data)) as archive:
                if {"[Content_Types].xml", "xl/workbook.xml"}.issubset(archive.namelist()):
                    return "xlsx"
        except BadZipFile:
            pass
    raise HTTPException(415, "Expected a PDF, XLSX, PNG, or JPEG file matching its extension.")


def receive_source(db: Session, actor_id: int, data: bytes, *, filename: str | None = None,
                   text: str | None = None) -> OrderSource:
    if len(data) > settings.order_source_upload_limit:
        raise HTTPException(413, "Order source exceeds the size limit.")
    if not data or (text is not None and (not text.strip() or "\x00" in text)):
        raise HTTPException(422, "Provide a nonempty file or message without null characters.")

    kind = "text" if text is not None else file_type(filename, data)
    digest = sha256(data).hexdigest()
    key = uuid4().hex if filename is not None else None
    insert_source = insert(OrderSource).values(
        source_type=kind, content_hash=digest, submitted_by=actor_id,
        raw_text=text, original_filename=filename, storage_key=key,
    ).on_conflict_do_nothing(index_elements=[OrderSource.content_hash]).returning(OrderSource)
    path = None
    try:
        source = db.scalar(insert_source)
        if source is None:
            existing = db.scalar(select(OrderSource.id).where(OrderSource.content_hash == digest))
            raise HTTPException(409, f"Identical content already received as source #{existing}.",
                                headers={"Location": f"/api/order-sources/{existing}"})
        if key:
            settings.order_source_storage.mkdir(parents=True, exist_ok=True)
            target = settings.order_source_storage / key
            with target.open("xb") as stream:
                path = target
                stream.write(data)
        record_audit_event(db, action="order_source.received", entity_type="order_source",
                           actor_user_id=actor_id, entity_id=source.id)
        db.commit()
    except Exception:
        db.rollback()
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    return source
