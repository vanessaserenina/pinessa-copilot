from datetime import datetime
from pathlib import PureWindowsPath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.ai.models import AIExtraction
from app.modules.ai.quantity import source_quantities
from app.modules.ai.source_checks import review_headers
from app.modules.ai.schemas import Customer, ExtractionOut, SourceQuantity, StructuredOrder
from app.modules.audit.models import AuditEvent
from app.modules.audit.service import record_audit_event
from app.modules.orders.schemas import OrderInput, OrderOut, PricedLine
from app.modules.orders.service import confirm_order, read_order, save_order, snapshot
from app.modules.ai.service import extract_order
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.orders.extraction import extract_source_text
from app.modules.orders.intake import receive_source
from app.modules.orders.models import ApprovedSalesPrice, CustomerPriceTier, OrderSource
from app.modules.products.models import Product

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


@router.get("/pending-review")
def pending_review(actor: Manager, db: Database, after_id: Annotated[int, Query(ge=0)] = 0) -> list[int]:
    return list(db.scalars(select(AIExtraction.order_source_id).where(
        AIExtraction.status == "needs_review", AIExtraction.order_source_id > after_id,
    ).order_by(AIExtraction.order_source_id).limit(100)))


@router.get("/failed-extractions")
def failed_extractions(actor: Manager, db: Database, after_id: Annotated[int, Query(ge=0)] = 0) -> list[int]:
    """Return the next 100 source IDs requiring manual handling."""
    manual = select(AIExtraction.order_source_id).where(AIExtraction.status == "needs_manual_handling")
    return list(db.scalars(select(OrderSource.id).where(
        (OrderSource.status == "failed_extraction") | OrderSource.id.in_(manual),
        OrderSource.id > after_id,
    ).order_by(OrderSource.id).limit(100)))


@router.get("/approved-unit-price")
def approved_unit_price(customer_code: str, product_id: int, currency: str,
                        actor: Manager, db: Database) -> str | None:
    prices = db.scalars(select(ApprovedSalesPrice.unit_price)
                        .join(Product, Product.id == ApprovedSalesPrice.product_id)
                        .join(CustomerPriceTier, CustomerPriceTier.tier == ApprovedSalesPrice.tier)
                        .where(CustomerPriceTier.customer_code == customer_code.strip(),
                               Product.id == product_id, Product.is_active,
                               ApprovedSalesPrice.selling_unit == Product.unit_of_measure,
                               ApprovedSalesPrice.currency == currency,
                               ApprovedSalesPrice.valid_from <= func.current_date(),
                               ApprovedSalesPrice.valid_through >= func.current_date()).limit(2)).all()
    return format(prices[0], "f") if len(prices) == 1 else None


@router.post("/{source_id}/extract-text")
def convert_source(source_id: int, actor: Manager, db: Database) -> SourceOut:
    return extract_source_text(db, source_id, actor.id)


def review_output(db: Session, extraction: AIExtraction) -> ExtractionOut:
    output = ExtractionOut.model_validate(extraction, from_attributes=True)
    if output.validated_output and output.line_matches:
        source = db.get(OrderSource, extraction.order_source_id)
        output.review_headers = review_headers(source.source_type, source.raw_text or "", output.validated_output)
        headers = output.review_headers
        supplied = output.validated_output.customer
        codes = db.scalars(select(CustomerPriceTier.customer_code).where(
            func.lower(CustomerPriceTier.customer_name) == (headers.customer_name or "").strip().lower()
        ).limit(2)).all()
        if len(codes) == 1 and headers.customer_name and headers.customer_name.casefold() in (source.raw_text or "").casefold():
            headers.customer_code = codes[0]
        elif not codes and supplied and supplied.code and supplied.code != headers.po_number and supplied.code in (source.raw_text or ""):
            headers.customer_code = supplied.code
        evidence = source_quantities(source.raw_text or "", output.validated_output.lines, source.source_type)
        for match, quantity in zip(output.line_matches, evidence, strict=True):
            match.source_quantity = SourceQuantity(**quantity) if quantity else None
    return output


@router.post("/{source_id}/extract-order")
def propose_order(source_id: int, actor: Manager, db: Database) -> ExtractionOut:
    return review_output(db, extract_order(db, source_id, actor.id))


@router.get("/{source_id}/extraction")
def read_extraction(source_id: int, actor: Manager, db: Database) -> ExtractionOut:
    extraction = db.scalar(select(AIExtraction).where(AIExtraction.order_source_id == source_id))
    if extraction is None:
        raise HTTPException(404, "Order extraction not found.")
    return review_output(db, extraction)


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


class ReviewedLine(PricedLine):
    specification: str | None = None
    customization_notes: str | None = None


class ReviewInput(OrderInput):
    customer: Customer
    lines: list[ReviewedLine] = Field(min_length=1, max_length=100)


@router.post("/{source_id}/confirm")
def confirm_proposal(source_id: int, data: ReviewInput, actor: Manager, db: Database) -> OrderOut:
    extraction = db.scalar(select(AIExtraction).where(
        AIExtraction.order_source_id == source_id).with_for_update())
    if extraction is None or not extraction.schema_valid or extraction.line_matches is None:
        raise HTTPException(409, "Extract and match the proposal before confirming.")
    if extraction.confirmed_order_id is not None:
        decision = db.scalar(select(AuditEvent).where(
            AuditEvent.entity_type == "ai_extraction", AuditEvent.entity_id == extraction.id,
            AuditEvent.action == "ai_extraction.confirmed"))
        if decision is None or decision.detail["reviewed"] != data.model_dump(mode="json"):
            raise HTTPException(409, f"This proposal was already confirmed as order #{extraction.confirmed_order_id}.")
        return snapshot(read_order(db, extraction.confirmed_order_id))
    if len(data.lines) != len(extraction.line_matches):
        raise HTTPException(422, "Review every proposed line before confirming.")
    if not data.customer_po_number:
        source = db.get(OrderSource, source_id)
        headers = review_headers(source.source_type, source.raw_text or "",
                                 StructuredOrder.model_validate(extraction.validated_output))
        if headers.check_po:
            raise HTTPException(422, "Check the PO number against the original source before confirming.")
    order_data = OrderInput.model_validate(data.model_dump(
        exclude={"customer": True, "lines": {"__all__": {"specification", "customization_notes"}}}))
    try:
        reason = f"Sales review of order source #{source_id}"
        draft = save_order(db, order_data, actor.id, commit=False, reason=reason)
        result = confirm_order(db, read_order(db, draft.id), draft.revision, actor.id, commit=False, reason=reason)
        extraction.status = "confirmed"
        extraction.confirmed_order_id = result.id
        record_audit_event(db, action="ai_extraction.confirmed", entity_type="ai_extraction",
                           entity_id=extraction.id, actor_user_id=actor.id,
                           detail={"source_id": source_id, "order_id": result.id,
                                   "reviewed": data.model_dump(mode="json")})
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
