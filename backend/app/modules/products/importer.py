"""Write a ParseResult to the catalogue tables.

Safe to run repeatedly: rows are matched by code / SKU, so nothing is duplicated.
- missing rows are created
- existing catalogue rows are updated only if a field changed
- rows that are in the database but no longer in the file are REPORTED, never deleted
- rows that are not catalogue data (e.g. synthetic) are never overwritten

This module does not commit. The caller (the script, or a test) decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.products.catalogue_parser import ParseResult
from app.modules.products.models import Product, ProductCategory, ProductFamily


class ImportConflictError(RuntimeError):
    """The import would overwrite rows that are not catalogue data."""


@dataclass
class EntityStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass
class ImportStats:
    categories: EntityStats = field(default_factory=EntityStats)
    families: EntityStats = field(default_factory=EntityStats)
    products: EntityStats = field(default_factory=EntityStats)
    orphan_families: list[str] = field(default_factory=list)
    orphan_products: list[str] = field(default_factory=list)


def _apply(row: Any, values: dict[str, Any]) -> bool:
    """Set changed fields on a row. Returns True if anything changed."""
    changed = False
    for key, value in values.items():
        if getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    return changed


def _import_categories(session: Session, parsed: ParseResult, stats: ImportStats) -> dict[str, int]:
    codes = [item.code for item in parsed.categories]
    existing = {
        row.code: row
        for row in session.scalars(select(ProductCategory).where(ProductCategory.code.in_(codes)))
    }
    ids: dict[str, int] = {}
    for item in parsed.categories:  # parents always come before their children
        values = {
            "name": item.name,
            "parent_id": ids[item.parent_code] if item.parent_code else None,
            "sort_order": item.sort_order,
        }
        row = existing.get(item.code)
        if row is None:
            row = ProductCategory(code=item.code, **values)
            session.add(row)
            session.flush()
            stats.categories.created += 1
        elif _apply(row, values):
            stats.categories.updated += 1
        else:
            stats.categories.unchanged += 1
        ids[item.code] = row.id
    return ids


def _import_families(
    session: Session, parsed: ParseResult, category_ids: dict[str, int], stats: ImportStats
) -> dict[str, int]:
    codes = [item.code for item in parsed.families]
    existing = {
        row.code: row
        for row in session.scalars(select(ProductFamily).where(ProductFamily.code.in_(codes)))
    }
    ids: dict[str, int] = {}
    for item in parsed.families:
        values = {
            "category_id": category_ids[item.category_code],
            "name": item.name,
            "option_groups": item.option_groups,
            "is_custom_allowed": item.is_custom_allowed,
            "source_label": item.source_label,
            "source_line": item.source_line,
        }
        row = existing.get(item.code)
        if row is None:
            row = ProductFamily(code=item.code, source="catalogue", **values)
            session.add(row)
            session.flush()
            stats.families.created += 1
        else:
            if row.source != "catalogue":
                raise ImportConflictError(
                    f"Family code '{item.code}' already exists as '{row.source}' data; refusing to overwrite it."
                )
            if _apply(row, values):
                stats.families.updated += 1
            else:
                stats.families.unchanged += 1
        ids[item.code] = row.id
    return ids


def _import_products(
    session: Session, parsed: ParseResult, family_ids: dict[str, int], stats: ImportStats
) -> None:
    skus = [product.sku for product in parsed.products]
    existing = {
        row.sku: row for row in session.scalars(select(Product).where(Product.sku.in_(skus)))
    }
    for family in parsed.families:
        for item in family.products:
            values = {
                "family_id": family_ids[family.code],
                "name": item.name,
                "variant_key": item.variant_key,
                "attributes": item.attributes,
            }
            row = existing.get(item.sku)
            if row is None:
                session.add(
                    Product(
                        sku=item.sku,
                        product_type="FINISHED",
                        unit_of_measure="pcs",
                        source="catalogue",
                        **values,
                    )
                )
                stats.products.created += 1
            else:
                if row.source != "catalogue":
                    raise ImportConflictError(
                        f"SKU '{item.sku}' already exists as '{row.source}' data; refusing to overwrite it."
                    )
                if _apply(row, values):
                    stats.products.updated += 1
                else:
                    stats.products.unchanged += 1


def _find_orphans(session: Session, parsed: ParseResult, stats: ImportStats) -> None:
    family_codes = {item.code for item in parsed.families}
    skus = {product.sku for product in parsed.products}
    stats.orphan_families = sorted(
        session.scalars(
            select(ProductFamily.code).where(
                ProductFamily.source == "catalogue", ProductFamily.code.not_in(family_codes)
            )
        )
    )
    stats.orphan_products = sorted(
        session.scalars(
            select(Product.sku).where(Product.source == "catalogue", Product.sku.not_in(skus))
        )
    )


def import_catalogue(session: Session, parsed: ParseResult) -> ImportStats:
    stats = ImportStats()
    category_ids = _import_categories(session, parsed, stats)
    family_ids = _import_families(session, parsed, category_ids, stats)
    _import_products(session, parsed, family_ids, stats)
    session.flush()
    _find_orphans(session, parsed, stats)
    return stats