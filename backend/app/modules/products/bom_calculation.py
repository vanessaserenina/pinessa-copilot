"""BOM requirement calculation: pure Decimal maths, no database, no AI.

    required = order quantity x quantity per unit x (1 + scrap% / 100), rounded UP once

- Scrap is applied to the TOTAL and rounded up once (not per kit).
- Units in WHOLE_NUMBER_UNITS round up to a whole number; other units to 4 decimal places.
- Floats are rejected on purpose: they cannot represent values like 0.1 exactly.
- Bad input raises a clear error. Nothing is guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, Inexact, InvalidOperation, localcontext
from typing import Iterable

WHOLE_NUMBER_UNITS = frozenset({"pcs"})  # configurable
FRACTION_PLACES = Decimal("0.0001")
HUNDRED = Decimal(100)
_PRECISION = 60


class BomError(ValueError):
    """Base class for BOM problems."""


class ProductNotFoundError(BomError):
    pass


class NoActiveBomError(BomError):
    pass


class InvalidQuantityError(BomError):
    pass


@dataclass(frozen=True)
class BomLine:
    component_product_id: int
    component_sku: str
    component_name: str
    unit_of_measure: str
    quantity_per_unit: Decimal
    scrap_percent: Decimal


@dataclass(frozen=True)
class RequirementLine:
    component_product_id: int
    component_sku: str
    component_name: str
    unit_of_measure: str
    quantity_per_unit: Decimal
    scrap_percent: Decimal
    exact_required: Decimal  # before rounding, kept so the result can be explained
    required: Decimal  # rounded up


def _to_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise TypeError(f"{label} must be an int, Decimal or str, never a float or bool")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, str)):
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise InvalidQuantityError(f"{label} is not a number: {value!r}") from None
    else:
        raise TypeError(f"{label} must be an int, Decimal or str")
    if not number.is_finite():
        raise InvalidQuantityError(f"{label} must be a finite number")
    return number


def _round_up(value: Decimal, unit: str) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        if unit in WHOLE_NUMBER_UNITS:
            return value.to_integral_value(rounding=ROUND_CEILING)
        return value.quantize(FRACTION_PLACES, rounding=ROUND_CEILING)


def calculate_requirements(
    order_quantity: int | str | Decimal, lines: Iterable[BomLine]
) -> list[RequirementLine]:
    quantity = _to_decimal(order_quantity, "Order quantity")
    if quantity <= 0:
        raise InvalidQuantityError("Order quantity must be greater than zero.")
    lines = list(lines)
    if not lines:
        raise BomError("The BOM has no component lines.")

    result: list[RequirementLine] = []
    for line in lines:
        per_unit = _to_decimal(line.quantity_per_unit, f"Quantity per unit of {line.component_sku}")
        scrap = _to_decimal(line.scrap_percent, f"Scrap percent of {line.component_sku}")
        if per_unit <= 0:
            raise BomError(f"Quantity per unit of {line.component_sku} must be greater than zero.")
        if scrap < 0 or scrap > 100:
            raise BomError(f"Scrap percent of {line.component_sku} must be between 0 and 100.")
        try:
            with localcontext() as ctx:
                ctx.prec = _PRECISION
                ctx.traps[Inexact] = True  # never silently lose digits
                exact = quantity * per_unit * (HUNDRED + scrap) / HUNDRED
        except Inexact:
            raise InvalidQuantityError("The quantity has too many digits to calculate exactly.") from None
        result.append(
            RequirementLine(
                component_product_id=line.component_product_id,
                component_sku=line.component_sku,
                component_name=line.component_name,
                unit_of_measure=line.unit_of_measure,
                quantity_per_unit=per_unit,
                scrap_percent=scrap,
                exact_required=exact,
                required=_round_up(exact, line.unit_of_measure),
            )
        )
    return result