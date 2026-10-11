"""Import the authorised product list into the catalogue tables.

Run from the backend folder (virtual environment active):
    python -m app.scripts.import_catalogue --dry-run
    python -m app.scripts.import_catalogue --dry-run --csv ../data/reference/generated-catalogue.csv
    python -m app.scripts.import_catalogue
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from app.modules.products.catalogue_parser import ParseResult, parse_catalogue, read_text

ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_FILE = ROOT_DIR / "data" / "reference" / "product-list.txt"


def format_report(result: ParseResult) -> str:
    categories = {c.code: c for c in result.categories}
    sections = [c for c in result.categories if c.parent_code is None]
    totals = {s.code: [0, 0, 0] for s in sections}  # groups, families, SKUs
    for category in result.categories:
        if category.parent_code is not None:
            totals[category.parent_code][0] += 1
    for family in result.families:
        section_code = categories[family.category_code].parent_code
        totals[section_code][1] += 1
        totals[section_code][2] += len(family.products)

    lines = [
        "CATALOGUE PARSE REPORT",
        "",
        f"Categories : {len(result.categories)} ({len(sections)} sections + {len(result.categories) - len(sections)} groups)",
        f"Families   : {len(result.families)}",
        f"SKUs       : {len(result.products)}",
        "",
        f"{'Section':<22}{'Prefix':<8}{'Groups':>7}{'Families':>10}{'SKUs':>7}",
    ]
    for section in sections:
        groups, families, skus = totals[section.code]
        lines.append(f"{section.name:<22}{section.code:<8}{groups:>7}{families:>10}{skus:>7}")
    lines.append("")
    if result.issues:
        lines.append(f"Warnings ({len(result.issues)}) - nothing was silently corrected:")
        for item in result.issues:
            lines.append(f"  [{item.code}] line {item.line}: {item.message}")
    else:
        lines.append("No warnings.")
    return "\n".join(lines)


def write_csv(result: ParseResult, path: Path) -> None:
    categories = {c.code: c for c in result.categories}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:  # utf-8-sig so Excel reads it correctly
        writer = csv.writer(handle)
        writer.writerow(
            ["section", "group", "family_code", "family_name", "sku", "product_name",
             "variant_key", "attributes", "custom_allowed", "source_line"]
        )
        for family in result.families:
            group = categories[family.category_code]
            section = categories[group.parent_code]
            for product in family.products:
                writer.writerow(
                    [section.name, group.name, family.code, family.name, product.sku, product.name,
                     product.variant_key, json.dumps(product.attributes), "yes" if family.is_custom_allowed else "no",
                     family.source_line]
                )


def format_stats(stats) -> str:
    def row(label, entity):
        return f"  {label:<11}: created {entity.created}, updated {entity.updated}, unchanged {entity.unchanged}"

    lines = [row("Categories", stats.categories), row("Families", stats.families), row("SKUs", stats.products)]
    if stats.orphan_families or stats.orphan_products:
        lines.append(
            f"  In the database but NOT in the file (kept, not deleted): "
            f"{len(stats.orphan_families)} families, {len(stats.orphan_products)} SKUs"
        )
        for code in stats.orphan_families[:10]:
            lines.append(f"    family {code}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the authorised product list into the catalogue tables.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="Path to the product list text file.")
    parser.add_argument("--csv", type=Path, help="Also write the generated catalogue to this CSV file.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print the report only. The database is not touched.")
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"File not found: {args.file}")
        return 1
    try:
        text = read_text(args.file)
    except UnicodeDecodeError:
        print("The file is not UTF-8. Re-save it as UTF-8 in your editor and try again.")
        return 1

    result = parse_catalogue(text)
    print(format_report(result))
    if args.csv:
        write_csv(result, args.csv)
        print(f"\nCSV written to {args.csv}")
    if args.dry_run:
        print("\nDry run: nothing was written to the database.")
        return 0

    # Imported here so that --dry-run works even if the database is not running.
    from app.core.database import SessionLocal
    from app.modules.products.importer import ImportConflictError, import_catalogue

    with SessionLocal() as session:
        try:
            stats = import_catalogue(session, result)
            session.commit()
        except ImportConflictError as exc:
            session.rollback()
            print(f"\nIMPORT STOPPED, nothing was saved: {exc}")
            return 2
        except Exception:
            session.rollback()
            raise
    print("\nImport finished and saved:")
    print(format_stats(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())