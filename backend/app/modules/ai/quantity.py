"""Bind explicit order quantities to individual source clauses for Sales review."""
import csv
import re
from collections import defaultdict
from io import StringIO

from app.modules.ai.matching import UNITS, normalize
from app.modules.ai.schemas import RequestedLine


# Recognize an order phrase, not an arbitrary numeric token in a description.
PREFIX = re.compile(r"^(?P<number>[0-9]+(?:\.[0-9]+)?)\s+(?P<unit>" +
                    "|".join(UNITS) + r")\s+of\s+", re.I)
SKU_SUFFIX = re.compile(r",?\s+sku\s+([\w-]+)$", re.I)
PACK_SIZE = re.compile(r"\b(?:pack(?:age)?\s+size|per\s+(?:pack|box|case|carton))\b", re.I)


def identity(text):
    description = PREFIX.sub("", text.strip().rstrip("."))
    sku = SKU_SUFFIX.search(description)
    return normalize(SKU_SUFFIX.sub("", description)), normalize(sku[1]) if sku else None


def order_cells(text: str, source_type: str):
    if source_type not in {"csv", "xlsx"}:
        yield text
        return
    # The document worker emits tab-delimited rows. Only labelled order cells
    # or a single-cell order message can establish the meaning of a number.
    order_columns = None
    for row in csv.reader(StringIO(text), delimiter="\t"):
        if len(row) == 1 and row[0].startswith("[") and row[0].endswith("]"):
            order_columns = None
        elif order_columns is None and len(row) > 1:
            order_columns = [i for i, value in enumerate(row)
                             if normalize(value) in {"order_line", "order line", "order message"}]
        elif order_columns is None and len(row) == 1:
            yield row[0]
        elif order_columns:
            yield from (row[i] for i in order_columns if i < len(row))


def source_quantities(text: str, lines: list[RequestedLine], source_type: str = "text"):
    clauses = defaultdict(list)
    pack_heading = False
    cells = order_cells(text, source_type)
    for clause in (part for cell in cells for part in re.split(r"[\r\n;]+|[.!?](?=\s|$)", cell)):
        clause = clause.strip()
        if not clause:
            continue
        # A PO header can introduce the actual order after a colon.
        clause = re.sub(r"^.*\bPO\s+[\w-]+:\s*", "", clause, flags=re.I)
        name, sku = identity(clause)
        quantity = None if pack_heading or PACK_SIZE.search(clause) else PREFIX.match(clause)
        clauses[name].append((sku, quantity))
        pack_heading = bool(PACK_SIZE.fullmatch(clause.rstrip(": ")))

    result = [None] * len(lines)
    claims = defaultdict(list)
    for index, line in enumerate(lines):
        name, suffix = identity(line.requested_text)
        sku = suffix or normalize(line.stated_sku)
        candidates = [(i, match) for i, (code, match) in enumerate(clauses.get(name, []))
                      if not code or not sku or code == sku]
        if len(candidates) == 1:
            i, match = candidates[0]
            if match:
                claims[(name, i)].append((index, match))
    for users in claims.values():
        if len(users) == 1:
            index, match = users[0]
            result[index] = {"quantity": match["number"], "unit": match["unit"],
                             "normalized_unit": UNITS[match["unit"].lower()]}
    return result
