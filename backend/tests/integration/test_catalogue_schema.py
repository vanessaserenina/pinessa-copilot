import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.modules.products.models import (
    Product,
    ProductAlias,
    ProductCategory,
    ProductFamily,
)


def make_family(db, code="TEST-FAM"):
    category = ProductCategory(code=f"{code}-CAT", name="Test category")
    db.add(category)
    db.flush()
    family = ProductFamily(
        category_id=category.id, code=code, name="Test family", source="synthetic"
    )
    db.add(family)
    db.flush()
    return family


def make_product(db, family, sku, variant_key="default", **overrides):
    values = dict(
        family_id=family.id,
        sku=sku,
        name="Test product",
        variant_key=variant_key,
        product_type="FINISHED",
        source="synthetic",
    )
    values.update(overrides)
    product = Product(**values)
    db.add(product)
    db.flush()
    return product


def test_duplicate_sku_is_rejected(db):
    family = make_family(db)
    make_product(db, family, "TEST-SKU-1", "a")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            make_product(db, family, "TEST-SKU-1", "b")


def test_duplicate_variant_in_same_family_is_rejected(db):
    family = make_family(db)
    make_product(db, family, "TEST-SKU-1", "same")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            make_product(db, family, "TEST-SKU-2", "same")


def test_invalid_product_type_is_rejected(db):
    family = make_family(db)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            make_product(db, family, "TEST-SKU-1", product_type="WRONG")


def test_alias_needs_exactly_one_target(db):
    family = make_family(db)
    product = make_product(db, family, "TEST-SKU-1")
    with pytest.raises(IntegrityError):  # no target
        with db.begin_nested():
            db.add(ProductAlias(alias_text="x", alias_normalized="x"))
            db.flush()
    with pytest.raises(IntegrityError):  # two targets
        with db.begin_nested():
            db.add(
                ProductAlias(
                    family_id=family.id,
                    product_id=product.id,
                    alias_text="x",
                    alias_normalized="x",
                )
            )
            db.flush()


def test_alias_status_must_be_valid(db):
    family = make_family(db)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(
                ProductAlias(
                    family_id=family.id,
                    alias_text="x",
                    alias_normalized="x",
                    status="maybe",
                )
            )
            db.flush()


def test_same_alias_twice_on_one_family_is_rejected_but_allowed_on_another(db):
    family_a = make_family(db, "TEST-FAM-A")
    family_b = make_family(db, "TEST-FAM-B")
    db.add(ProductAlias(family_id=family_a.id, alias_text="Sampo", alias_normalized="sampo"))
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(ProductAlias(family_id=family_a.id, alias_text="SAMPO", alias_normalized="sampo"))
            db.flush()
    # The same alias on a different family is allowed (the matcher will show both as candidates).
    db.add(ProductAlias(family_id=family_b.id, alias_text="Sampo", alias_normalized="sampo"))
    db.flush()


def test_category_with_families_cannot_be_deleted(db):
    family = make_family(db)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(delete(ProductCategory).where(ProductCategory.id == family.category_id))