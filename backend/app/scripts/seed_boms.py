"""Create SYNTHETIC components and BOMs for the kit families (DEVELOPMENT ONLY).

Run the catalogue import first. Then:
    python -m app.scripts.seed_boms --dry-run
    python -m app.scripts.seed_boms

Safe to re-run: products that already have a BOM are skipped.
Real catalogue rows are never modified; only synthetic rows and new BOMs are created.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every table, including users)
from app.core.database import SessionLocal
from app.modules.audit.service import record_audit_event
from app.modules.products.models import Bom, BomItem, Product, ProductCategory, ProductFamily

SECTION = ("CMP", "Komponen (SYNTHETIC)")
GROUPS = {
    "CMP-PKG": "Packaging components (SYNTHETIC)",
    "CMP-BRD": "Branding components (SYNTHETIC)",
    "CMP-SEW": "Sewing materials (SYNTHETIC)",
}
# (group code, SKU = family code, name)
COMPONENTS = [
    ("CMP-PKG", "CMP-CARTON-BOX-SMALL", "TEST Carton box small"),
    ("CMP-PKG", "CMP-PLASTIC-POUCH-CLEAR", "TEST Clear plastic pouch"),
    ("CMP-PKG", "CMP-PLASTIC-POUCH-FROSTED", "TEST Frosted plastic pouch"),
    ("CMP-PKG", "CMP-CUSTOM-PRINTED-BOX", "TEST Custom printed box"),
    ("CMP-BRD", "CMP-LOGO-LABEL-STICKER", "TEST Logo label sticker"),
    ("CMP-BRD", "CMP-CUSTOM-PRINT-SLEEVE", "TEST Custom design print sleeve"),
    ("CMP-SEW", "CMP-THREAD-SET", "TEST Thread set"),
    ("CMP-SEW", "CMP-NEEDLE-PACK", "TEST Needle pack"),
    ("CMP-SEW", "CMP-BUTTON-SET", "TEST Button set"),
    ("CMP-SEW", "CMP-SAFETY-PIN-PACK", "TEST Safety pin pack"),
]

# Base components of each kit family (1 per kit, 0% scrap). Kit families with no BOM
# (Toiletries Kit, Sanitary Bag, Laundry Bag) are deliberately left out.
KIT_RECIPES: dict[str, list[str]] = {
    "KIT-DENTAL-KIT": ["MND-SIKAT-GIGI-STANDARD", "MND-PASTA-GIGI-5GR-DELIDENT"],
    "KIT-SHAVING-KIT": ["MND-RAZOR", "MND-SHAVING-CREAM"],
    "KIT-VANITY-KIT": ["MND-COTTON-BUD", "MND-COTTON-PAD-BULAT", "MND-NAIL-FILE"],
    "KIT-SEWING-KIT": ["CMP-THREAD-SET", "CMP-NEEDLE-PACK", "CMP-BUTTON-SET", "CMP-SAFETY-PIN-PACK"],
    "KIT-COMB-KIT": ["MND-SISIR-HANDLE"],
    "KIT-SHOWER-CAP": ["MND-SHOWER-CAP"],
}

# option value -> (component SKU, scrap %)
PACKAGING_COMPONENTS = {
    "kotak": ("CMP-CARTON-BOX-SMALL", Decimal("2")),
    "plastik": ("CMP-PLASTIC-POUCH-CLEAR", Decimal("2")),
    "plastik_doff": ("CMP-PLASTIC-POUCH-FROSTED", Decimal("2")),
    "custom_packaging": ("CMP-CUSTOM-PRINTED-BOX", Decimal("5")),
}
BRANDING_COMPONENTS = {
    "logo": ("CMP-LOGO-LABEL-STICKER", Decimal("5")),
    "custom_design": ("CMP-CUSTOM-PRINT-SLEEVE", Decimal("5")),
    "non_logo": None,
}

# This one SKU gets a retired v1 (without safety pins) and an active v2,
# so the "retired BOM version" scenario exists in the test data.
VERSIONED_FAMILY = "KIT-SEWING-KIT"
VERSIONED_ATTRIBUTES = {"packaging": "kotak", "branding": "logo"}
RETIRED_V1_EXCLUDES = {"CMP-SAFETY-PIN-PACK"}

ONE = Decimal("1")
ZERO = Decimal("0")


class SeedError(RuntimeError):
    pass


def get_or_create_category(session, code, name, parent, sort_order) -> ProductCategory:
    row = session.scalar(select(ProductCategory).where(ProductCategory.code == code))
    if row is None:
        row = ProductCategory(
            code=code, name=name, parent_id=parent.id if parent else None, sort_order=sort_order
        )
        session.add(row)
        session.flush()
    return row


def ensure_components(session: Session) -> tuple[dict[str, Product], int]:
    section = get_or_create_category(session, SECTION[0], SECTION[1], None, 9)
    groups = {
        code: get_or_create_category(session, code, name, section, index)
        for index, (code, name) in enumerate(GROUPS.items(), start=1)
    }
    created = 0
    products: dict[str, Product] = {}
    for group_code, sku, name in COMPONENTS:
        full_name = f"{name} (SYNTHETIC)"
        family = session.scalar(select(ProductFamily).where(ProductFamily.code == sku))
        if family is None:
            family = ProductFamily(
                category_id=groups[group_code].id, code=sku, name=full_name, source="synthetic"
            )
            session.add(family)
            session.flush()
        elif family.source != "synthetic":
            raise SeedError(f"Family '{sku}' exists but is not synthetic data; refusing to touch it.")
        product = session.scalar(select(Product).where(Product.sku == sku))
        if product is None:
            product = Product(
                family_id=family.id,
                sku=sku,
                name=full_name,
                variant_key="default",
                attributes={},
                product_type="COMPONENT",
                unit_of_measure="pcs",
                source="synthetic",
            )
            session.add(product)
            session.flush()
            created += 1
        elif product.source != "synthetic":
            raise SeedError(f"SKU '{sku}' exists but is not synthetic data; refusing to touch it.")
        products[sku] = product
    return products, created


def load_catalogue_components(session: Session, skus: set[str]) -> dict[str, Product]:
    found = {p.sku: p for p in session.scalars(select(Product).where(Product.sku.in_(skus)))}
    missing = sorted(skus - set(found))
    if missing:
        raise SeedError(
            "These catalogue SKUs were not found (run the catalogue import first):\n  "
            + "\n  ".join(missing)
        )
    return found


def build_lines(product: Product, base_skus: list[str]) -> list[tuple[str, Decimal, Decimal]]:
    lines = [(sku, ONE, ZERO) for sku in base_skus]
    packaging = product.attributes.get("packaging")
    branding = product.attributes.get("branding")
    if packaging not in PACKAGING_COMPONENTS:
        raise SeedError(f"{product.sku}: unknown packaging option {packaging!r}")
    if branding not in BRANDING_COMPONENTS:
        raise SeedError(f"{product.sku}: unknown branding option {branding!r}")
    sku, scrap = PACKAGING_COMPONENTS[packaging]
    lines.append((sku, ONE, scrap))
    choice = BRANDING_COMPONENTS[branding]
    if choice is not None:
        lines.append((choice[0], ONE, choice[1]))
    return lines


def create_bom(session, product, version, status, lines, components) -> int:
    bom = Bom(
        product_id=product.id,
        version_number=version,
        status=status,
        effective_from=date.today(),
        is_synthetic=True,
        notes="SYNTHETIC BOM generated by seed_boms",
    )
    session.add(bom)
    session.flush()
    for sku, quantity, scrap in lines:
        session.add(
            BomItem(
                bom_id=bom.id,
                component_product_id=components[sku].id,
                quantity_per_unit=quantity,
                scrap_percent=scrap,
            )
        )
    session.flush()
    record_audit_event(
        session,
        action="bom.created",
        entity_type="bom",
        entity_id=bom.id,
        after={"product_sku": product.sku, "version": version, "status": status, "lines": len(lines)},
        detail={"source": "seed_boms"},
    )
    return len(lines)


def run(session: Session) -> dict[str, int]:
    stats = {"components": 0, "active": 0, "retired": 0, "skipped": 0, "lines": 0}
    synthetic, stats["components"] = ensure_components(session)

    needed = {sku for skus in KIT_RECIPES.values() for sku in skus}
    needed |= {sku for sku, _ in PACKAGING_COMPONENTS.values()}
    needed |= {choice[0] for choice in BRANDING_COMPONENTS.values() if choice}
    catalogue = load_catalogue_components(session, {s for s in needed if not s.startswith("CMP-")})
    components = {**catalogue, **synthetic}

    for family_code, base_skus in KIT_RECIPES.items():
        family = session.scalar(select(ProductFamily).where(ProductFamily.code == family_code))
        if family is None or family.source != "catalogue":
            raise SeedError(f"Kit family '{family_code}' not found (run the catalogue import first).")
        products = session.scalars(
            select(Product).where(Product.family_id == family.id).order_by(Product.sku)
        ).all()
        for product in products:
            has_bom = session.scalar(
                select(func.count()).select_from(Bom).where(Bom.product_id == product.id)
            )
            if has_bom:
                stats["skipped"] += 1
                continue
            if family_code == VERSIONED_FAMILY and product.attributes == VERSIONED_ATTRIBUTES:
                old_skus = [s for s in base_skus if s not in RETIRED_V1_EXCLUDES]
                stats["lines"] += create_bom(
                    session, product, 1, "retired", build_lines(product, old_skus), components
                )
                stats["retired"] += 1
                version = 2
            else:
                version = 1
            stats["lines"] += create_bom(
                session, product, version, "active", build_lines(product, base_skus), components
            )
            stats["active"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed SYNTHETIC components and kit BOMs.")
    parser.add_argument("--dry-run", action="store_true", help="Do everything, then roll back.")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        try:
            stats = run(session)
        except SeedError as exc:
            session.rollback()
            print(f"STOPPED, nothing was saved: {exc}")
            return 1
        except Exception:
            session.rollback()
            raise
        if args.dry_run:
            session.rollback()
        else:
            session.commit()

    print(f"Components : created {stats['components']}")
    print(
        f"BOMs       : created {stats['active'] + stats['retired']} "
        f"({stats['active']} active, {stats['retired']} retired); "
        f"skipped {stats['skipped']} products that already had BOMs"
    )
    print(f"BOM lines  : {stats['lines']}")
    print("Dry run: everything was rolled back." if args.dry_run else "Saved. These are synthetic records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())