from datetime import datetime
from pathlib import PureWindowsPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.orders.extraction import extract_source_text
from app.modules.orders.intake import receive_source
from app.modules.orders.models import OrderSource

class OrderSourceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if self.body_field is None:
            return handler

        async def limited_upload(request: Request):
            # Limit the entire encoded request, including multipart framing.
            limit = settings.order_source_upload_limit
            length = request.headers.get("content-length")
            if length is not None:
                try:
                    declared_size = int(length)
                except ValueError:
                    raise HTTPException(400, "Invalid Content-Length.") from None
                if declared_size < 0:
                    raise HTTPException(400, "Invalid Content-Length.")
                if declared_size > limit:
                    raise HTTPException(413, "Order source request exceeds the size limit.")

            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise HTTPException(413, "Order source request exceeds the size limit.")
                body.extend(chunk)

            # Only pass the validated body to FastAPI's form parser.
            buffered = True

            async def receive():
                nonlocal buffered
                if not buffered:
                    return await request.receive()
                buffered = False
                data = bytes(body)
                body.clear()
                return {"type": "http.request", "body": data, "more_body": False}

            return await handler(Request(request.scope, receive))

        return limited_upload


router = APIRouter(prefix="/order-sources", tags=["order sources"], route_class=OrderSourceRoute)
Manager = Annotated[User, Depends(require_permission("orders:manage"))]
Database = Annotated[Session, Depends(get_db)]


class SourceOut(BaseModel):
    id: int
    source_type: str
    status: str
    received_at: datetime = Field(validation_alias="created_at")
    submitted_by: int
    original_filename: str | None
    raw_text: str | None


@router.post("", status_code=201)
def upload_source(actor: Manager, db: Database, text: Annotated[str | None, Form()] = None,
                  file: Annotated[UploadFile | None, File()] = None) -> SourceOut:
    if (text is None) == (file is None):
        raise HTTPException(422, "Provide exactly one message or file.")
    if file is None:
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise HTTPException(422, "Provide valid UTF-8 text.") from exc
        return receive_source(db, actor.id, data, text=text)
    filename = PureWindowsPath(file.filename or "").name
    if not filename or len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "Invalid filename.")
    return receive_source(db, actor.id, file.file.read(settings.order_source_upload_limit + 1),
                          filename=filename)


@router.get("/failed-extractions")
def failed_extractions(actor: Manager, db: Database, after_id: Annotated[int, Query(ge=0)] = 0) -> list[int]:
    """Return the next 100 source IDs requiring manual handling."""
    return list(db.scalars(select(OrderSource.id).where(
        OrderSource.status == "failed_extraction", OrderSource.id > after_id,
    ).order_by(OrderSource.id).limit(100)))


@router.post("/{source_id}/extract-text")
def convert_source(source_id: int, actor: Manager, db: Database) -> SourceOut:
    return extract_source_text(db, source_id, actor.id)


@router.get("/{source_id}")
def get_source(source_id: int, actor: Manager, db: Database) -> SourceOut:
    source = db.get(OrderSource, source_id)
    if source is None:
        raise HTTPException(404, "Order source not found.")
    return source


@router.get("/{source_id}/file")
def download_source(source_id: int, actor: Manager, db: Database):
    source = db.get(OrderSource, source_id)
    if source is None or source.storage_key is None:
        raise HTTPException(404, "Source file not found.")
    return FileResponse(settings.order_source_storage / source.storage_key,
                        filename=source.original_filename, media_type="application/octet-stream")
