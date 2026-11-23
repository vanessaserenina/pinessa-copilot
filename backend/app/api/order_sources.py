from datetime import datetime
from pathlib import PureWindowsPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.orders.intake import receive_source
from app.modules.orders.models import OrderSource

router = APIRouter(prefix="/order-sources", tags=["order sources"])
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
