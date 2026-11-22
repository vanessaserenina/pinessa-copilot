from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import User
from app.modules.orders.models import Order
from app.modules.orders.schemas import ConfirmInput, EditInput, OrderInput, OrderOut
from app.modules.orders.service import confirm_order, read_order, save_order, snapshot
from app.modules.orders.preview import ManualOrderPreview, StockPreview, preview_stock
from app.modules.products.bom_calculation import BomError, ProductNotFoundError

router = APIRouter(prefix="/orders", tags=["orders"])
OrderManager = Annotated[User, Depends(require_permission("orders:manage"))]


def order_transaction(db: Session = Depends(get_db)):
    try:
        yield db
    except Exception:
        db.rollback()
        raise


OrderDB = Annotated[Session, Depends(order_transaction)]


@router.post("/stock-preview", dependencies=[Depends(require_permission("products:read"))])
def order_stock_preview(order: ManualOrderPreview, db: Session = Depends(get_db)) -> StockPreview:
    try:
        return preview_stock(db, order)
    except ProductNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except BomError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
def recent_orders(db: OrderDB, _: OrderManager):
    rows = db.execute(select(Order.id, Order.customer_code, Order.status)
                      .order_by(Order.updated_at.desc(), Order.id.desc()).limit(100))
    return [dict(row) for row in rows.mappings()]


@router.post("", status_code=201)
def create_order(data: OrderInput, db: OrderDB, actor: OrderManager) -> OrderOut:
    return save_order(db, data, actor.id)


@router.get("/{order_id}")
def get_order(order_id: int, db: OrderDB, _: OrderManager) -> OrderOut:
    return snapshot(read_order(db, order_id))


@router.put("/{order_id}")
def edit_order(order_id: int, data: EditInput, db: OrderDB, actor: OrderManager) -> OrderOut:
    return save_order(db, data, actor.id, read_order(db, order_id, lock=True))


@router.post("/{order_id}/confirm")
def confirm(order_id: int, data: ConfirmInput, db: OrderDB, actor: OrderManager) -> OrderOut:
    return confirm_order(db, read_order(db, order_id, lock=True), data.revision, actor.id)
