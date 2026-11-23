"""Run python -m app.scripts.seed_supplier after seed_boms for local purchasing demos."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import SessionLocal
from app.modules.products.models import BomItem
from app.modules.purchasing.models import Supplier, SupplierProduct


def seed_supplier(db: Session) -> None:
    code = "TEST-COMPONENTS"
    db.execute(insert(Supplier).values(code=code, name="TEST Component Supply (SYNTHETIC)",
                                       is_synthetic=True).on_conflict_do_nothing(index_elements=["code"]))
    supplier = db.scalars(select(Supplier).where(Supplier.code == code)).one()
    if not supplier.is_synthetic:
        raise ValueError(f"Supplier code {code} already belongs to a non-synthetic supplier.")
    for product_id in db.scalars(select(BomItem.component_product_id).distinct()):
        db.execute(insert(SupplierProduct).values(supplier_id=supplier.id, component_product_id=product_id,
                                                  unit_cost=1000).on_conflict_do_nothing())


if __name__ == "__main__":
    with SessionLocal.begin() as session:
        seed_supplier(session)
