import pytest
from sqlalchemy import func, select

from app.modules.products.catalogue_parser import parse_catalogue
from app.modules.products.importer import ImportConflictError, import_catalogue
from app.modules.products.models import Product, ProductCategory, ProductFamily

# A made-up section with its own prefix, so these tests never collide with the real catalogue rows.
TEST_PREFIXES = {"ZZ TEST SECTION": "ZZT"}
TEXT = """\
ZZ TEST SECTION
1. Alpha
Alpha one (logo/non logo)
Alpha two
2. Beta
"""


def parsed(text: str = TEXT):
    return parse_catalogue(text, section_prefixes=TEST_PREFIXES)


def count(db, model, column, pattern="ZZT%"):
    return db.scalar(select(func.count()).select_from(model).where(column.like(pattern)))


def test_first_import_creates_everything(db):
    stats = import_catalogue(db, parsed())
    assert (stats.categories.created, stats.families.created, stats.products.created) == (3, 3, 4)
    assert count(db, Product, Product.sku) == 4
    assert count(db, ProductFamily, ProductFamily.code) == 3


def test_second_import_changes_nothing(db):
    import_catalogue(db, parsed())
    stats = import_catalogue(db, parsed())
    for entity in (stats.categories, stats.families, stats.products):
        assert (entity.created, entity.updated) == (0, 0)
    assert (stats.categories.unchanged, stats.families.unchanged, stats.products.unchanged) == (3, 3, 4)
    assert count(db, Product, Product.sku) == 4


def test_changed_text_is_updated_not_duplicated(db):
    import_catalogue(db, parsed())
    stats = import_catalogue(db, parsed(TEXT.replace("Alpha two", "Alpha TWO")))  # same code, new name
    assert stats.families.created == 0 and stats.families.updated == 1
    assert stats.products.created == 0 and stats.products.updated == 1
    assert count(db, ProductFamily, ProductFamily.code) == 3


def test_existing_synthetic_row_is_never_overwritten(db):
    category = ProductCategory(code="ZZT-CONFLICT", name="Conflict test")
    db.add(category)
    db.flush()
    db.add(ProductFamily(category_id=category.id, code="ZZT-BETA", name="Synthetic", source="synthetic"))
    db.flush()
    with pytest.raises(ImportConflictError):
        import_catalogue(db, parsed())


def test_rows_missing_from_file_are_reported_but_kept(db):
    import_catalogue(db, parsed())
    stats = import_catalogue(db, parsed(TEXT.replace("Alpha two\n", "")))
    assert "ZZT-ALPHA-TWO" in stats.orphan_families
    assert "ZZT-ALPHA-TWO" in stats.orphan_products
    assert db.scalar(select(func.count()).select_from(ProductFamily).where(ProductFamily.code == "ZZT-ALPHA-TWO")) == 1