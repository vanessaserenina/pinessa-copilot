"""Deterministic catalogue matching for validated proposals."""
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modules.ai.schemas import RequestedLine
from app.modules.products.models import Product, ProductAlias, ProductFamily


METHODS = ("sku", "alias", "text")
UNIT_GROUPS = {
    "case": ("case", "cases"), "pcs": ("pc", "pcs", "piece", "pieces"),
    "ctn": ("ctn", "ctns", "carton", "cartons"), "box": ("box", "boxes"),
}
UNITS = {word: unit for unit, words in UNIT_GROUPS.items() for word in words}


def normalize(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def words(value: str) -> str:
    # Preserve decimal measurements and negative variant prefixes as whole terms.
    value = re.sub(r"(?<=\d)\s+(?=ml\b|gr?\b|kg\b|cm\b|mm\b)", "", normalize(value))
    value = re.sub(r"\bnon[\s_-]+", "non", value)
    return " ".join(re.findall(r"[^\W_]+(?:[.]\d+)?", value))


def description(line: RequestedLine) -> str:
    phrase = normalize(line.requested_text)
    if sku := normalize(line.stated_sku):
        phrase = re.sub(r"(?<!\w)(?:sku\s*[:#]?\s*)?" + re.escape(sku) + r"(?!\w)", "", phrase)
    unit = normalize(line.unit)
    if line.quantity and unit:
        units = UNIT_GROUPS.get(UNITS.get(unit), (unit,))
        pattern = r"^(\d+(?:\.\d+)?)\s*(?:" + "|".join(map(re.escape, units)) + r")\b\s*(?:of\s+)?"
        prefix = re.match(pattern, phrase)
        if prefix and Decimal(prefix[1]) == Decimal(line.quantity):
            phrase = phrase[prefix.end():]
    return words(phrase)


def trigrams(value: str) -> set[str]:
    grams = set()
    for word in value.split():
        padded = "  " + word + " "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def variant_issue(line: RequestedLine, phrase: str, product: Product, alias: str) -> str | None:
    specification = words(line.specification or "")
    requested_details = f" {phrase} {specification} "
    if normalize(line.customization_notes) or re.search(r"\b(custom|customized|personalized)\b", requested_details):
        return "Customization requires review."
    requested_unit = UNITS.get(normalize(line.unit))
    selling_unit = UNITS.get(normalize(product.unit_of_measure))
    if requested_unit and selling_unit and requested_unit != selling_unit:
        return f"Requested unit ({requested_unit}) differs from the catalogue selling unit ({selling_unit})."
    attrs = " ".join(str(value) for value in product.attributes.values())
    supported = set(words(f"{product.name} {attrs} {alias}").split())
    requested = set(requested_details.split())
    # A variant explicitly selected by the catalogue takes precedence over a name or alias.
    for key, choices in product.family.option_groups.items():
        selected = words(str(product.attributes.get(key, "")))
        options = sorted(filter(None, (words(str(choice)) for choice in choices)), key=len, reverse=True)
        if options:
            # Match "sky blue" as one option, while still checking a separate mention of "blue".
            pattern = r"\b(?:" + "|".join(map(re.escape, options)) + r")\b"
            for detail in (phrase, specification):
                if any(option != selected for option in re.findall(pattern, detail)):
                    return f"Requested {key} differs from the catalogue variant."
    # An approved nickname can identify a product but cannot establish a variant.
    descriptors = set(words(f"{product.name} {attrs}").split())
    colours = {"blue", "red", "white", "black", "green", "yellow", "orange",
               "purple", "pink", "brown", "grey", "gray", "biru", "merah", "putih", "hitam"}
    colour = product.attributes.get("colour", product.attributes.get("color"))
    if colour:
        descriptors -= colours
        descriptors.update(words(str(colour)).split())
    variant_terms = {word for word in requested if word in colours or
                     re.fullmatch(r"\d+(?:\.\d+)?(?:ml|gr?|kg|cm|mm)", word) or
                     word in {"logo", "nonlogo", "packaging", "nonpackaging"}}
    unknown = sorted((requested - supported) | (variant_terms - descriptors))
    if unknown:
        return "Unverified requested details: " + ", ".join(unknown)
    return None


def match_lines(db: Session, lines: list[RequestedLine]) -> list[dict]:
    products = db.scalars(select(Product).join(Product.family).options(joinedload(Product.family))
                          .where(Product.is_active, ProductFamily.is_active, Product.product_type == "FINISHED")
                          .order_by(Product.sku, Product.id)).all()
    aliases = defaultdict(list)
    for alias in db.scalars(select(ProductAlias).where(ProductAlias.status == "approved")):
        aliases[(alias.product_id, alias.family_id)].append(words(alias.alias_text))
    catalogue = []
    for product in products:
        names = aliases[(product.id, None)] + aliases[(None, product.family_id)]
        terms = [words(product.name), words(product.family.name), *names]
        catalogue.append((product, names, [trigrams(term) for term in terms if term]))

    results = []
    for index, line in enumerate(lines):
        phrase = description(line)
        sku = normalize(line.stated_sku)
        query = trigrams(phrase + " " + words(line.specification or ""))
        ranked = []
        for product, names, terms in catalogue:
            if sku and sku == normalize(product.sku):
                priority, score = 0, 1.0
            elif phrase and phrase in names:
                priority, score = 1, 1.0
            else:
                priority = 2
                score = max((len(query & term) / len(query | term) for term in terms), default=0)
                if score < 0.15:
                    continue
            ranked.append((priority, -score, product))
        ranked.sort(key=lambda item: (item[0], item[1], item[2].sku, item[2].id))
        reason = "Stated SKU was not found." if sku else "No catalogue candidate."
        method = None
        selected = None
        if ranked:
            priority, _, product = ranked[0]
            method = METHODS[priority]
            if sku and priority != 0:
                reason = "Stated SKU was not found."
            elif priority == 2:
                reason = "Text candidates require review."
            elif len(ranked) > 1 and ranked[1][0] == priority:
                reason = "Multiple catalogue products match."
            else:
                # An explicit SKU wins ranking, but its own approved wording still
                # explains the description. A family nickname cannot justify a SKU.
                product_alias = phrase in aliases.get((product.id, None), ())
                alias = phrase if method == "alias" or product_alias else ""
                reason = variant_issue(line, phrase, product, alias)
                if reason is None:
                    selected = product.id
        candidates = [
            {"product_id": candidate.id, "sku": candidate.sku, "name": candidate.name,
             "unit": candidate.unit_of_measure, "attributes": candidate.attributes,
             "method": METHODS[tier], "score": round(-negative_score, 4)}
            for tier, negative_score, candidate in ranked[:5]
        ]
        unit = normalize(line.unit)
        results.append({
            "line_index": index,
            "requested_text": line.requested_text,
            "normalized_text": words(line.requested_text),
            "normalized_unit": UNITS.get(unit, unit) or None,
            "selected_product_id": selected,
            "method": method,
            "status": "auto" if selected is not None else "needs_review",
            "reason": reason,
            "candidates": candidates,
        })
    return results
