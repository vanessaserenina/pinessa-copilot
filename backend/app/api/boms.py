from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.bom_errors import to_http
from app.core.database import get_db
from app.modules.auth.dependencies import get_request_id, require_permission
from app.modules.auth.models import User
from app.modules.products.bom_management import (
    BomRuleViolation,
    activate_bom,
    delete_draft_bom,
    load_bom,
)
from app.modules.products.schemas import BomOut

router = APIRouter(prefix="/boms", tags=["boms"])


@router.get("/{bom_id}", response_model=BomOut)
def get_bom(
    bom_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("products:read")),
) -> BomOut:
    bom = load_bom(db, bom_id)
    if bom is None:
        raise HTTPException(status_code=404, detail="BOM not found.")
    return BomOut.from_bom(bom)


@router.post("/{bom_id}/activate", response_model=BomOut)
def activate(
    bom_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("boms:manage")),
) -> BomOut:
    try:
        bom = activate_bom(db, bom_id, actor_user_id=actor.id, request_id=get_request_id(request))
    except BomRuleViolation as exc:
        raise to_http(exc)
    result = BomOut.from_bom(bom)
    db.commit()
    return result


@router.delete("/{bom_id}", status_code=204)
def delete_draft(
    bom_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("boms:manage")),
) -> Response:
    try:
        delete_draft_bom(db, bom_id, actor_user_id=actor.id, request_id=get_request_id(request))
    except BomRuleViolation as exc:
        raise to_http(exc)
    db.commit()
    return Response(status_code=204)
