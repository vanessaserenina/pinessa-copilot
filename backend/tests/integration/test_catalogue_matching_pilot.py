"""Measure the production matcher on frozen validated lines, independently of extraction."""
import json
from pathlib import Path

from app.modules.ai.matching import match_lines, normalize
from app.modules.ai.schemas import RequestedLine
from app.modules.products.models import Product, ProductAlias, ProductCategory, ProductFamily
from app.scripts.pilot_scoring import score_lines


def test_similar_catalogue_routes(migrated_db):
    db = migrated_db
    path = Path(__file__).resolve().parents[3] / "data/synthetic/pilot-catalogue.json"
    batch = json.loads(path.read_text(encoding="utf-8"))
    category = ProductCategory(code="SYNTH-CLIPS", name="Synthetic clips")
    db.add(category)
    db.flush()
    family = ProductFamily(category_id=category.id, code="DESK", name=batch["family"],
                           option_groups=batch["option_groups"], source="synthetic")
    db.add(family)
    db.flush()
    products = {}
    for variant in batch["products"]:
        product = Product(family_id=family.id, sku=variant["sku"], variant_key=variant["sku"],
                          name=f"{variant['colour']} clips {variant['pack']}",
                          attributes={"colour": variant["colour"], "pack": variant["pack"]},
                          unit_of_measure=variant["unit"], source="synthetic")
        products[product.sku] = product
        db.add(product)
    db.flush()
    for alias, sku in batch["product_aliases"].items():
        db.add(ProductAlias(product_id=products[sku].id, alias_text=alias,
                            alias_normalized=normalize(alias), status="approved"))
    alias = batch["family_alias"]
    db.add(ProductAlias(family_id=family.id, alias_text=alias, alias_normalized=normalize(alias),
                        status="approved"))
    db.flush()

    cases = batch["lines"]
    lines = [RequestedLine(requested_text=c["text"], stated_sku=c["sku"], quantity="2", unit=c["unit"],
                           specification=None, customization_notes=None) for c in cases]
    matches = match_lines(db, lines)
    assert [m["line_index"] for m in matches] == list(range(len(cases)))
    expected = [{"auto": c["expected"], "quantity": "2"} for c in cases]
    rows, counts = score_lines(expected, matches, [], [])
    missed = 0
    for case, row, match in zip(cases, rows, matches, strict=True):
        missed += int(case["expected"] is not None and row["product_referral"])
        print("CATALOGUE_LINE", json.dumps({"id": case["id"], "expected": case["expected"],
                                           "selected": row["automatic_sku"], "reason": match["reason"]}))
    print("CATALOGUE_TOTAL", json.dumps({
        "incorrect_selections": counts["incorrect_automatic_selections"],
        "automatic_selections": counts["automatic_selections"],
        "appropriate_referrals": counts["appropriate_referrals"], "expected_referrals": counts["expected_referrals"],
        "missed_automatic_matches": missed, "expected_automatic_matches": len(cases) - counts["expected_referrals"],
    }))
    assert all(row["route_correct"] for row in rows), [
        case["id"] for case, row in zip(cases, rows, strict=True) if not row["route_correct"]]
