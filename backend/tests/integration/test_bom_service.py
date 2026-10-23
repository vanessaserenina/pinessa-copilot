from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.products.bom_calculation import (
    InvalidQuantityError,
    NoActiveBomError,
    ProductNotFoundError,
)
from app.modules.products.bom_service import calculate_product_requirements, get_active_bom
from app.modules.products.models import Bom, BomItem, Product, ProductFamily


def make_bom(db, product, version, status, lines):
    """lines: list of (component Product, quantity per unit, scrap percent)."""
    bom = Bom(product_id=product.id, version_number=version, status=status, is_synthetic=True)
    db.add(bom)
    db.flush()
    for component, quantity, scrap in lines:
        db.add(
            BomItem(
                bom_id=bom.id,
                component_product_id=component.id,
                quantity_per_unit=Decimal(quantity),
                scrap_percent=Decimal(scrap),
            )
        )
    db.flush()
    return bom


def required_by_sku(result):
    return {r.component_sku: r.required for r in result}


def test_worked_example_through_the_database(db, new_product):
    kit = new_product()
    brush, paste, carton, label = (new_product("COMPONENT") for _ in range(4))
    make_bom(db, kit, 1, "active", [(brush, "1", "0"), (paste, "1", "0"), (carton, "1", "2"), (label, "1", "5")])
    db.expire_all()  # force a real reload, so values come back from NUMERIC columns
    result = calculate_product_requirements(db, kit.id, 250)
    assert required_by_sku(result) == {
        brush.sku: Decimal("250"),
        paste.sku: Decimal("250"),
        carton.sku: Decimal("255"),
        label.sku: Decimal("263"),
    }


def test_the_active_version_is_used_not_the_retired_one(db, new_product):
    kit = new_product()
    part = new_product("COMPONENT")
    make_bom(db, kit, 1, "retired", [(part, "1", "0")])
    make_bom(db, kit, 2, "active", [(part, "2", "0")])
    db.expire_all()
    assert required_by_sku(calculate_product_requirements(db, kit.id, 10)) == {part.sku: Decimal("20")}
    assert get_active_bom(db, kit.id).version_number == 2


@pytest.mark.parametrize("status", ["draft", "retired"])
def test_only_a_non_active_bom_counts_as_missing(db, new_product, status):
    kit = new_product()
    make_bom(db, kit, 1, status, [(new_product("COMPONENT"), "1", "0")])
    with pytest.raises(NoActiveBomError):
        calculate_product_requirements(db, kit.id, 10)


def test_product_without_any_bom_is_a_clear_error(db, new_product):
    with pytest.raises(NoActiveBomError):
        calculate_product_requirements(db, new_product().id, 10)


def test_unknown_product_is_a_clear_error(db):
    with pytest.raises(ProductNotFoundError):
        calculate_product_requirements(db, 999_999_999, 10)


def test_invalid_quantity_is_a_clear_error(db, new_product):
    kit = new_product()
    make_bom(db, kit, 1, "active", [(new_product("COMPONENT"), "1", "0")])
    with pytest.raises(InvalidQuantityError):
        calculate_product_requirements(db, kit.id, 0)


def test_seeded_dental_kit_matches_hand_calculation(db):
    product = db.scalar(select(Product).where(Product.sku == "KIT-DENTAL-KIT-KTK-LOGO"))
    if product is None or get_active_bom(db, product.id) is None:
        pytest.skip("run the catalogue import and seed_boms first")
    result = calculate_product_requirements(db, product.id, 250)
    assert required_by_sku(result) == {
        "MND-SIKAT-GIGI-STANDARD": Decimal("250"),
        "MND-PASTA-GIGI-5GR-DELIDENT": Decimal("250"),
        "CMP-CARTON-BOX-SMALL": Decimal("255"),
        "CMP-LOGO-LABEL-STICKER": Decimal("263"),
    }


def test_seeded_toiletries_kit_has_no_bom(db):
    product = db.scalar(
        select(Product).join(ProductFamily).where(ProductFamily.code == "KIT-TOILETRIES-KIT").limit(1)
    )
    if product is None:
        pytest.skip("run the catalogue import first")
    with pytest.raises(NoActiveBomError):
        calculate_product_requirements(db, product.id, 10)