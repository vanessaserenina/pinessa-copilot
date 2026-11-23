"""Detect contradictions between the reply and explicitly labelled order data."""
import csv
import re
from io import StringIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.ai.matching import normalize
from app.modules.ai.quantity import PREFIX, identity, source_quantities
from app.modules.ai.schemas import StructuredOrder
from app.modules.orders.models import OrderSource
from app.modules.products.models import Product, ProductAlias


def csv_customer(text: str) -> str | None:
    rows = csv.reader(StringIO(text), delimiter="\t")
    header = [normalize(cell) for cell in next(rows, [])]
    columns = [i for i, label in enumerate(header) if label in {"customer", "customer name", "customer_name"}]
    if len(columns) != 1:
        return None
    index = columns[0]
    name = None
    for row in rows:
        if len(row) != len(header) or not row[index].strip():
            return None
        value = row[index].strip()
        if name is not None and name != value:
            return None
        name = value
    return name


def preserve_fields(properties: dict, values: dict, except_field: str):
    properties.update({key: {"const": value} for key, value in values.items() if key != except_field})


def correction(db: Session, source: OrderSource, order: StructuredOrder) -> tuple[str, dict] | None:
    text = source.raw_text
    if source.source_type == "csv":
        name = csv_customer(text)
        if name and (order.customer is None or order.customer.name != name):
            hint = "Copy the full customer name exactly from the customer column, including all prefixes."
            schema = StructuredOrder.model_json_schema()
            preserve_fields(schema["properties"], order.model_dump(mode="json"), "customer")
            schema["properties"]["customer"] = {"$ref": "#/$defs/Customer"}
            customer_fields = schema["$defs"]["Customer"]["properties"]
            if order.customer:
                preserve_fields(customer_fields, order.customer.model_dump(), "name")
            customer_fields["name"] = {"const": name}
            return hint, schema
    if source.source_type != "text" or len(order.lines) != 1 or re.search(r"\b(?:sku|code)\b", text, re.I):
        return None
    line = order.lines[0]
    phrase, _ = identity(line.requested_text)
    if not line.stated_sku or not PREFIX.match(line.requested_text):
        return None
    sku = normalize(line.stated_sku)
    if sku != phrase:
        return None
    if not source_quantities(text, [line])[0]:
        return None
    if not db.scalar(select(ProductAlias.id).where(ProductAlias.alias_normalized == phrase,
                                                   ProductAlias.status == "approved").limit(1)):
        return None
    # A literal catalogue SKU can also happen to be an alias.
    if db.scalar(select(Product.id).where(func.lower(Product.sku) == sku).limit(1)):
        return None
    hint = ("The order phrase gives a product description. Copy a stated SKU only when supplied "
            "separately. Otherwise return null.")
    schema = StructuredOrder.model_json_schema()
    preserve_fields(schema["properties"], order.model_dump(mode="json"), "lines")
    schema["properties"]["lines"].update(minItems=1, maxItems=1)
    preserve_fields(schema["$defs"]["RequestedLine"]["properties"], line.model_dump(), "stated_sku")
    return hint, schema
