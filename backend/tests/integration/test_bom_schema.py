from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.modules.products.models import Bom, BomItem


def add_bom(db, product, version=1, status="draft"):
    bom = Bom(product_id=product.id, version_number=version, status=status, is_synthetic=True)
    db.add(bom)
    db.flush()
    return bom


def add_item(db, bom, component, quantity="1", scrap="0"):
    item = BomItem(
        bom_id=bom.id,
        component_product_id=component.id,
        quantity_per_unit=Decimal(quantity),
        scrap_percent=Decimal(scrap),
    )
    db.add(item)
    db.flush()
    return item


def test_only_one_active_bom_per_product(db, new_product):
    product = new_product()
    add_bom(db, product, 1, "active")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            add_bom(db, product, 2, "active")


def test_retired_and_draft_versions_can_sit_beside_an_active_one(db, new_product):
    product = new_product()
    add_bom(db, product, 1, "retired")
    add_bom(db, product, 2, "active")
    add_bom(db, product, 3, "draft")
    add_bom(db, product, 4, "draft")


def test_two_products_can_each_have_an_active_bom(db, new_product):
    add_bom(db, new_product(), 1, "active")
    add_bom(db, new_product(), 1, "active")


def test_duplicate_version_number_is_rejected(db, new_product):
    product = new_product()
    add_bom(db, product, 1, "retired")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            add_bom(db, product, 1, "draft")


def test_invalid_status_and_version_are_rejected(db, new_product):
    product = new_product()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            add_bom(db, product, 1, "maybe")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            add_bom(db, product, 0, "draft")


@pytest.mark.parametrize("quantity,scrap", [("0", "0"), ("-1", "0"), ("1", "-1"), ("1", "101")])
def test_item_quantity_and_scrap_rules(db, new_product, quantity, scrap):
    bom = add_bom(db, new_product())
    component = new_product(product_type="COMPONENT")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            add_item(db, bom, component, quantity, scrap)


def test_same_component_twice_in_one_bom_is_rejected(db, new_product):
    bom = add_bom(db, new_product())
    component = new_product(product_type="COMPONENT")
    add_item(db, bom, component)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            add_item(db, bom, component, "2")


def test_component_used_in_a_bom_cannot_be_deleted(db, new_product):
    bom = add_bom(db, new_product())
    component = new_product(product_type="COMPONENT")
    add_item(db, bom, component)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(text("DELETE FROM products WHERE id = :id"), {"id": component.id})


def test_product_with_a_bom_cannot_be_deleted(db, new_product):
    product = new_product()
    add_bom(db, product)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(text("DELETE FROM products WHERE id = :id"), {"id": product.id})


def test_deleting_a_bom_deletes_its_lines(db, new_product):
    bom = add_bom(db, new_product())
    add_item(db, bom, new_product(product_type="COMPONENT"))
    bom_id = bom.id
    db.delete(bom)
    db.flush()
    remaining = db.scalar(select(func.count()).select_from(BomItem).where(BomItem.bom_id == bom_id))
    assert remaining == 0