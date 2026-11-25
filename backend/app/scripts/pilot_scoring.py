"""Read-only, line-level scoring of the frozen synthetic order-intake cases."""
import argparse
import json
import re
from collections import Counter
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select

from app.api.order_sources import review_output
from app.modules.ai.models import AIExtraction
from app.modules.audit.models import AuditEvent
from app.modules.orders.models import OrderSource
from app.modules.products.models import Product


DATA = Path(__file__).resolve().parents[3] / "data/synthetic"


def frozen_cases():
    for path in sorted((DATA / "pilot").glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        truth = case["expected"]
        yield "original", path.stem, case, [{"auto": truth["auto_sku"], "quantity": truth["quantity"]}]
    for case in json.loads((DATA / "pilot-extended.json").read_text(encoding="utf-8")):
        lines = [{"auto": sku, "quantity": quantity} for sku, quantity in
                 zip(case["expected_skus"], case["expected_quantities"], strict=True)]
        yield "extended", case["case"], case, lines
    for case in json.loads((DATA / "pilot-shadow/batch.json").read_text(encoding="utf-8")):
        yield "shadow", case["id"], case, case["lines"]


def whole_quantity(value):
    return (value is not None and re.fullmatch(r"[0-9]+(?:\.0+)?", value) is not None
            and 1 <= Decimal(value) <= 10_000_000)


def score_lines(expected, matches, raw_lines, confirmed_lines):
    """All frozen products sell by the case. Source and match indices are zero-based."""
    indexed = {match["line_index"]: match for match in matches}
    if len(indexed) != len(matches):
        raise ValueError("Duplicate proposal line index")
    rows = []
    totals = Counter(automatic_selections=0, incorrect_automatic_selections=0,
                     expected_referrals=0, appropriate_referrals=0, expected_quantity_holds=0,
                     appropriate_quantity_holds=0, unexpected_quantity_holds=0,
                     missing_review_lines=0, extra_review_lines=0,
                     expected_lines=len(expected), correct_source_quantities=0, correct_routes=0)
    line_count = max(len(expected), len(raw_lines), len(confirmed_lines))
    for i in sorted(set(range(line_count)) | indexed.keys()):
        truth = expected[i] if i < len(expected) else None
        match = indexed.get(i)
        selected = match["selected_product_id"] if match else None
        candidates = match["candidates"] if match else []
        candidate = next((c for c in candidates if c["product_id"] == selected), None)
        sku = candidate["sku"] if candidate else None
        evidence = match.get("source_quantity") if match else None
        quantity = evidence["quantity"] if evidence else None
        # A product referral can leave the form blank even with usable quantity evidence.
        catalogue_unit = candidate["unit"].lower() if candidate else "case"
        usable = whole_quantity(quantity) and catalogue_unit in (evidence["normalized_unit"], evidence["unit"].lower())
        expected_hold = truth is not None and not whole_quantity(truth["quantity"])
        observed_hold = match is not None and not usable
        expected_referral = truth is not None and truth["auto"] is None
        referred = match is not None and selected is None and match["status"] == "needs_review"
        wrong_auto = selected is not None and (truth is None or sku is None or sku != truth["auto"])
        correct_route = truth is not None and ((expected_referral and referred)
                                               or (selected is not None and not wrong_auto))
        correct_evidence = (truth is not None and match is not None and quantity == truth["quantity"]
                            and ("unit" not in truth or truth["unit"] == (evidence or {}).get("unit")))
        totals.update(automatic_selections=int(selected is not None),
                      incorrect_automatic_selections=int(wrong_auto),
                      expected_referrals=int(expected_referral),
                      appropriate_referrals=int(expected_referral and referred),
                      expected_quantity_holds=int(expected_hold),
                      appropriate_quantity_holds=int(expected_hold and observed_hold),
                      unexpected_quantity_holds=int(truth is not None and not expected_hold and observed_hold),
                      missing_review_lines=int(truth is not None and match is None),
                      extra_review_lines=int(truth is None and match is not None),
                      correct_source_quantities=int(correct_evidence), correct_routes=int(correct_route))
        rows.append({"line": i + 1, "expected": truth, "review_line_present": match is not None,
                     "raw_model_line": raw_lines[i] if i < len(raw_lines) else None,
                     "automatic_sku": sku, "incorrect_auto": wrong_auto, "product_referral": referred,
                     "route_correct": correct_route,
                     "source_quantity": evidence, "source_quantity_correct": correct_evidence,
                     "quantity_hold": observed_hold if match else None,
                     "initial_confirmation_quantity": quantity if usable and candidate else None,
                     "confirmed_line": confirmed_lines[i] if i < len(confirmed_lines) else None})
    return rows, totals


def build_report(db, *, generated_files=None):
    sources = db.scalars(select(OrderSource).order_by(OrderSource.id)).all()
    by_hash = {(s.source_type, s.content_hash): s for s in sources}
    groups = {}
    known_ids = set()
    for group, name, case, expected in frozen_cases():
        section = groups.setdefault(group, {"sources": [], "absent_cases": [], "counts": Counter()})
        kind = case["format"]
        if kind in ("text", "csv"):
            content = case["source"].encode()
        elif group == "shadow":
            content = (DATA / "pilot-shadow" / f"{name}.pdf").read_bytes()
        elif name in (generated_files or {}):
            content = generated_files[name].read_bytes()
        else:
            content = None
        source = by_hash.get((kind, sha256(content).hexdigest())) if content is not None else None
        if source is None:
            section["absent_cases"].append(name)
            continue
        known_ids.add(source.id)
        extraction = db.scalar(select(AIExtraction).where(AIExtraction.order_source_id == source.id))
        proposal = review_output(db, extraction) if extraction else None
        matches = [m.model_dump() for m in proposal.line_matches or []] if proposal else []
        raw = []
        if extraction and extraction.raw_output:
            try:
                reply = json.loads(extraction.raw_output[-1])
                if isinstance(reply, dict) and isinstance(reply.get("lines"), list):
                    raw = reply["lines"]
            except (ValueError, TypeError):
                pass
        decision = db.scalar(select(AuditEvent).where(
            AuditEvent.action == "ai_extraction.confirmed", AuditEvent.entity_type == "ai_extraction",
            AuditEvent.entity_id == extraction.id)) if extraction else None
        confirmed = []
        if decision:
            for line in decision.detail["reviewed"]["lines"]:
                confirmed.append({**line, "sku": db.get(Product, line["product_id"]).sku})
        rows, counts = score_lines(expected, matches, raw, confirmed)
        section["counts"].update(counts)
        section["sources"].append({"case": name, "source_id": source.id,
                                   "status": proposal.status if proposal else source.status,
                                   "confirmed_order_id": extraction.confirmed_order_id if extraction else None,
                                   "confirmation_audit_id": decision.id if decision else None,
                                   "lines": rows})
    return {"groups": groups, "unscored_source_ids": [s.id for s in sources if s.id not in known_ids]}


if __name__ == "__main__":
    from app.core.database import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanned-pdf", type=Path, help="Original generated extended-case PDF")
    parser.add_argument("--xlsx", type=Path, help="Original generated extended-case workbook")
    files = {name.replace("_", "-"): path for name, path in vars(parser.parse_args()).items() if path}
    with SessionLocal() as db:
        print(json.dumps(build_report(db, generated_files=files), indent=2))
