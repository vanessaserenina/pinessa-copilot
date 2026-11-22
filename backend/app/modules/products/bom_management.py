"""BOM management rules: create a draft, activate it, delete a draft.

Each function checks every rule BEFORE changing anything, so a rejected request changes nothing.
The product row is locked (SELECT ... FOR UPDATE) so two admins cannot create the same version
number or activate two versions of one product at the same time.
Functions flush but never commit; the caller commits.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modules.audit.service import record_audit_event
from app.modules.products.models import Bom, BomItem, Product

# Real BOMs may be confidential (assumption A4). Until they are authorised, BOMs created
# through the app are flagged as synthetic test data.
NEW_BOMS_ARE_SYNTHETIC = True


class BomRuleViolation(Exception):
    """A business rule stopped the request. kind is 'not_found', 'conflict' or 'invalid'."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def load_bom(db: Session, bom_id: int) -> Bom | None:
    """Load a BOM with its product and lines, always re-reading fresh values from the database."""
    return db.scalar(
        select(Bom)
        .options(selectinload(Bom.product), selectinload(Bom.items).selectinload(BomItem.component))
        .where(Bom.id == bom_id)
        .execution_options(populate_existing=True)
    )


def _lock_product(db: Session, product_id: int) -> Product:
    product = db.scalar(
        select(Product)
        .where(Product.id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if product is None:
        raise BomRuleViolation("not_found", f"Product {product_id} does not exist.")
    return product


def _snapshot(bom: Bom) -> dict[str, Any]:
    """Safe description of a BOM for the audit log."""
    return {
        "product_sku": bom.product.sku,
        "version": bom.version_number,
        "status": bom.status,
        "lines": [
            {
                "component_sku": item.component.sku,
                "quantity_per_unit": str(item.quantity_per_unit),
                "scrap_percent": str(item.scrap_percent),
            }
            for item in bom.items
        ],
    }


def create_draft_bom(
    db: Session,
    *,
    product_id: int,
    items: list[tuple[int, Decimal, Decimal]],
    notes: str | None,
    actor_user_id: int,
    request_id: str | None,
) -> Bom:
    product = _lock_product(db, product_id)
    if not product.is_active:
        raise BomRuleViolation("invalid", f"Product '{product.sku}' is not active.")
    if product.product_type != "FINISHED":
        raise BomRuleViolation(
            "invalid", f"'{product.sku}' is a component; only finished products can have a BOM."
        )
    if not items:
        raise BomRuleViolation("invalid", "A BOM needs at least one component line.")

    ids = [item[0] for item in items]
    if len(set(ids)) != len(ids):
        raise BomRuleViolation("invalid", "The same component appears more than once.")
    if product.id in ids:
        raise BomRuleViolation("invalid", "A product cannot be a component of itself.")

    found = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))}
    missing = sorted(set(ids) - set(found))
    if missing:
        raise BomRuleViolation("invalid", f"Unknown component product id(s): {missing}")
    inactive = sorted(found[i].sku for i in ids if not found[i].is_active)
    if inactive:
        raise BomRuleViolation("invalid", f"These components are not active: {', '.join(inactive)}")

    next_version = (
        db.scalar(select(func.max(Bom.version_number)).where(Bom.product_id == product.id)) or 0
    ) + 1
    clean_notes = notes.strip() if notes and notes.strip() else None
    bom = Bom(
        product_id=product.id,
        version_number=next_version,
        status="draft",
        is_synthetic=NEW_BOMS_ARE_SYNTHETIC,
        notes=clean_notes,
        created_by_user_id=actor_user_id,
        items=[
            BomItem(component_product_id=cid, quantity_per_unit=quantity, scrap_percent=scrap)
            for cid, quantity, scrap in items
        ],
    )
    db.add(bom)
    db.flush()
    record_audit_event(
        db,
        action="bom.created",
        entity_type="bom",
        entity_id=bom.id,
        actor_user_id=actor_user_id,
        after=_snapshot(bom),
        request_id=request_id,
    )
    return bom


def activate_bom(
    db: Session, bom_id: int, *, actor_user_id: int, request_id: str | None
) -> Bom:
    bom = db.get(Bom, bom_id)
    if bom is None:
        raise BomRuleViolation("not_found", f"BOM {bom_id} does not exist.")
    product = _lock_product(db, bom.product_id)
    bom = load_bom(db, bom_id)  # re-read under the lock, so the status is current
    if bom is None:
        raise BomRuleViolation("not_found", f"BOM {bom_id} does not exist.")
    if bom.status != "draft":
        raise BomRuleViolation(
            "conflict",
            f"Only a draft BOM can be activated (this one is '{bom.status}'). "
            "To restore an old recipe, create a new draft with the same lines.",
        )
    if not product.is_active or product.product_type != "FINISHED":
        raise BomRuleViolation("conflict", "Only active finished products can have an active BOM.")
    if not bom.items:
        raise BomRuleViolation("conflict", "The draft has no component lines.")
    inactive = sorted(item.component.sku for item in bom.items if not item.component.is_active)
    if inactive:
        raise BomRuleViolation(
            "conflict", f"These components are no longer active: {', '.join(inactive)}"
        )

    current = db.scalar(
        select(Bom).where(Bom.product_id == bom.product_id, Bom.status == "active")
    )
    if current is not None:
        current.status = "retired"
        db.flush()  # free the "one active BOM" slot BEFORE activating the new one
        record_audit_event(
            db,
            action="bom.retired",
            entity_type="bom",
            entity_id=current.id,
            actor_user_id=actor_user_id,
            before={"status": "active"},
            after={"status": "retired"},
            detail={"replaced_by_bom_id": bom.id, "product_sku": bom.product.sku},
            request_id=request_id,
        )

    bom.status = "active"
    if bom.effective_from is None:
        bom.effective_from = date.today()
    db.flush()
    record_audit_event(
        db,
        action="bom.activated",
        entity_type="bom",
        entity_id=bom.id,
        actor_user_id=actor_user_id,
        before={"status": "draft"},
        after={
            "status": "active",
            "version": bom.version_number,
            "effective_from": bom.effective_from.isoformat(),
        },
        detail={"product_sku": bom.product.sku, "replaced_bom_id": current.id if current else None},
        request_id=request_id,
    )
    return bom


def delete_draft_bom(
    db: Session, bom_id: int, *, actor_user_id: int, request_id: str | None
) -> None:
    bom = db.get(Bom, bom_id)
    if bom is None:
        raise BomRuleViolation("not_found", f"BOM {bom_id} does not exist.")
    _lock_product(db, bom.product_id)
    bom = load_bom(db, bom_id)
    if bom is None:
        raise BomRuleViolation("not_found", f"BOM {bom_id} does not exist.")
    if bom.status != "draft":
        raise BomRuleViolation(
            "conflict", f"Only a draft BOM can be deleted (this one is '{bom.status}')."
        )
    snapshot = _snapshot(bom)
    deleted_id = bom.id
    db.delete(bom)
    db.flush()
    record_audit_event(
        db,
        action="bom.deleted",
        entity_type="bom",
        entity_id=deleted_id,
        actor_user_id=actor_user_id,
        before=snapshot,
        request_id=request_id,
    )
