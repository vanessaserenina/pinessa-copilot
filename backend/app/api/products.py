from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.bom_errors import to_http
from app.core.database import get_db
from app.modules.auth.dependencies import get_request_id, require_permission
from app.modules.auth.models import User
from app.modules.products.bom_calculation import BomError, NoActiveBomError, ProductNotFoundError
from app.modules.products.bom_management import BomRuleViolation, create_draft_bom, load_bom
from app.modules.products.bom_service import calculate_with_bom, get_active_bom
from app.modules.products.models import Bom, BomItem, Product, ProductCategory, ProductFamily
from app.modules.products.schemas import (
    BomCreate,
    BomOut,
    BomSummary,
    CalculationLine,
    CalculationOut,
    CalculationRequest,
    ProductDetail,
    ProductListItem,
    ProductPage,
)

router = APIRouter(prefix="/products", tags=["products"])

Group = aliased(ProductCategory, name="grp")
Section = aliased(ProductCategory, name="sec")
SECTION_CODE = func.coalesce(Section.code, Group.code)
SECTION_NAME = func.coalesce(Section.name, Group.name)
HAS_ACTIVE_BOM = exists().where(Bom.product_id == Product.id, Bom.status == "active")


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _joined(stmt):
    return (
        stmt.select_from(Product)
        .join(ProductFamily, ProductFamily.id == Product.family_id)
        .join(Group, Group.id == ProductFamily.category_id)
        .outerjoin(Section, Section.id == Group.parent_id)
    )


def _row_columns():
    return select(
        Product,
        ProductFamily,
        Group,
        SECTION_CODE.label("section_code"),
        SECTION_NAME.label("section_name"),
        HAS_ACTIVE_BOM.label("has_active_bom"),
    )


def _to_item(row) -> ProductListItem:
    product, family, group, section_code, section_name, has_bom = row
    return ProductListItem(
        id=product.id,
        sku=product.sku,
        name=product.name,
        product_type=product.product_type,
        unit_of_measure=product.unit_of_measure,
        source=product.source,
        is_active=product.is_active,
        attributes=product.attributes or {},
        family_code=family.code,
        family_name=family.name,
        category_code=group.code,
        category_name=group.name,
        section_code=section_code,
        section_name=section_name,
        is_custom_allowed=family.is_custom_allowed,
        has_active_bom=bool(has_bom),
    )


@router.get("", response_model=ProductPage)
def list_products(
    q: str | None = Query(None, max_length=100, description="Words to find in SKU, name or family"),
    product_type: Literal["FINISHED", "COMPONENT"] | None = None,
    source: Literal["catalogue", "synthetic"] | None = None,
    section: str | None = Query(None, max_length=80, description="Section code, e.g. KIT"),
    group: str | None = Query(None, max_length=80, description="Group code, e.g. KIT-DENTAL-KIT"),
    has_bom: bool | None = None,
    include_inactive: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("products:read")),
) -> ProductPage:
    def filtered(stmt):
        for term in (q or "").split()[:8]:  # every word must match
            pattern = f"%{_escape_like(term)}%"
            stmt = stmt.where(
                or_(
                    Product.sku.ilike(pattern, escape="\\"),
                    Product.name.ilike(pattern, escape="\\"),
                    ProductFamily.name.ilike(pattern, escape="\\"),
                )
            )
        if product_type:
            stmt = stmt.where(Product.product_type == product_type)
        if source:
            stmt = stmt.where(Product.source == source)
        if section:
            stmt = stmt.where(SECTION_CODE == section)
        if group:
            stmt = stmt.where(Group.code == group)
        if has_bom is True:
            stmt = stmt.where(HAS_ACTIVE_BOM)
        elif has_bom is False:
            stmt = stmt.where(~HAS_ACTIVE_BOM)
        if not include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        return stmt

    total = db.scalar(filtered(_joined(select(func.count(Product.id))))) or 0
    rows = db.execute(
        filtered(_joined(_row_columns())).order_by(Product.sku, Product.id).limit(limit).offset(offset)
    ).all()
    return ProductPage(items=[_to_item(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{product_id}", response_model=ProductDetail)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("products:read")),
) -> ProductDetail:
    row = db.execute(_joined(_row_columns()).where(Product.id == product_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    item = _to_item(row)

    version_rows = db.execute(
        select(Bom, func.count(BomItem.id))
        .outerjoin(BomItem, BomItem.bom_id == Bom.id)
        .where(Bom.product_id == product_id)
        .group_by(Bom.id)
        .order_by(Bom.version_number.desc())
    ).all()
    boms = [
        BomSummary(
            id=bom.id,
            version_number=bom.version_number,
            status=bom.status,
            effective_from=bom.effective_from,
            is_synthetic=bom.is_synthetic,
            line_count=lines,
            created_at=bom.created_at,
        )
        for bom, lines in version_rows
    ]
    active = get_active_bom(db, product_id)
    used_in = db.scalar(
        select(func.count(func.distinct(Bom.product_id)))
        .select_from(BomItem)
        .join(Bom, Bom.id == BomItem.bom_id)
        .where(BomItem.component_product_id == product_id, Bom.status == "active")
    )
    return ProductDetail(
        **item.model_dump(),
        option_groups=row[1].option_groups or {},
        boms=boms,
        active_bom=BomOut.from_bom(active) if active else None,
        used_in_active_boms=used_in or 0,
    )


@router.post("/{product_id}/bom-calculation", response_model=CalculationOut)
def calculate_bom(
    product_id: int,
    payload: CalculationRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_permission("products:read")),
) -> CalculationOut:
    try:
        bom, requirements = calculate_with_bom(db, product_id, payload.quantity)
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NoActiveBomError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BomError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return CalculationOut(
        product_id=product_id,
        product_sku=bom.product.sku,
        product_name=bom.product.name,
        bom_id=bom.id,
        bom_version=bom.version_number,
        quantity=payload.quantity,
        lines=[CalculationLine(**asdict(line)) for line in requirements],
    )


@router.post("/{product_id}/boms", response_model=BomOut, status_code=201)
def create_bom_draft(
    product_id: int,
    payload: BomCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("boms:manage")),
) -> BomOut:
    try:
        bom = create_draft_bom(
            db,
            product_id=product_id,
            items=[(i.component_product_id, i.quantity_per_unit, i.scrap_percent) for i in payload.items],
            notes=payload.notes,
            actor_user_id=actor.id,
            request_id=get_request_id(request),
        )
    except BomRuleViolation as exc:
        raise to_http(exc)
    saved = load_bom(db, bom.id)
    assert saved is not None  # the draft was just flushed in this transaction
    result = BomOut.from_bom(saved)
    db.commit()
    return result
