from decimal import Decimal

import pytest

from app.modules.products.bom_calculation import (
    BomError,
    BomLine,
    InvalidQuantityError,
    calculate_requirements,
)


def line(sku, per_unit="1", scrap="0", unit="pcs", pid=1):
    return BomLine(
        component_product_id=pid,
        component_sku=sku,
        component_name=f"Name {sku}",
        unit_of_measure=unit,
        quantity_per_unit=Decimal(per_unit),
        scrap_percent=Decimal(scrap),
    )


def required(result):
    return {r.component_sku: r.required for r in result}


def test_worked_example_250_dental_kits():
    """Hand-calculated: 250 x Dental Kit (kotak, logo)."""
    lines = [
        line("TOOTHBRUSH", "1", "0", pid=1),
        line("PASTE", "1", "0", pid=2),
        line("CARTON", "1", "2", pid=3),
        line("LOGO-LABEL", "1", "5", pid=4),
    ]
    assert required(calculate_requirements(250, lines)) == {
        "TOOTHBRUSH": Decimal("250"),
        "PASTE": Decimal("250"),
        "CARTON": Decimal("255"),  # 250 x 1.02 = 255.0 exactly, so NOT 256
        "LOGO-LABEL": Decimal("263"),  # 250 x 1.05 = 262.5, rounded up
    }


def test_scrap_is_applied_to_the_total_and_rounded_once():
    result = calculate_requirements(3, [line("X", "1", "5")])
    assert result[0].exact_required == Decimal("3.15")
    assert result[0].required == Decimal("4")  # per-kit rounding would wrongly give 6


def test_exact_result_is_not_rounded_up():
    assert calculate_requirements(250, [line("X", "1", "2")])[0].required == Decimal("255")


def test_fractional_quantity_per_unit_rounds_up_for_pieces():
    assert calculate_requirements(3, [line("X", "2.5")])[0].required == Decimal("8")  # 7.5


def test_non_piece_units_round_up_to_four_decimals():
    assert calculate_requirements(1, [line("X", "0.33333", unit="g")])[0].required == Decimal("0.3334")
    assert calculate_requirements(3, [line("X", "0.3333", unit="g")])[0].required == Decimal("0.9999")


def test_large_quantity_is_exact():
    result = calculate_requirements(1_000_000, [line("X", "3", "5")])
    assert result[0].required == Decimal("3150000")


def test_int_str_and_decimal_quantities_are_accepted():
    for quantity in (10, "10", Decimal("10")):
        assert required(calculate_requirements(quantity, [line("X")])) == {"X": Decimal("10")}


@pytest.mark.parametrize("bad", [0, -1, "0", Decimal("-0.5")])
def test_quantity_must_be_positive(bad):
    with pytest.raises(InvalidQuantityError):
        calculate_requirements(bad, [line("X")])


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), "abc", ""])
def test_non_finite_or_non_numeric_quantity_is_rejected(bad):
    with pytest.raises(InvalidQuantityError):
        calculate_requirements(bad, [line("X")])


@pytest.mark.parametrize("bad", [1.5, True])
def test_floats_and_booleans_are_rejected(bad):
    with pytest.raises(TypeError):
        calculate_requirements(bad, [line("X")])


def test_empty_bom_is_an_error():
    with pytest.raises(BomError):
        calculate_requirements(10, [])


@pytest.mark.parametrize(
    "per_unit,scrap", [("0", "0"), ("-1", "0"), ("1", "-1"), ("1", "101")]
)
def test_invalid_line_values_are_rejected(per_unit, scrap):
    with pytest.raises(BomError):
        calculate_requirements(10, [line("X", per_unit, scrap)])


def test_order_is_preserved_and_lines_carry_their_inputs():
    lines = [line("B", pid=2), line("A", pid=1, unit="pcs")]
    result = calculate_requirements(5, lines)
    assert [r.component_sku for r in result] == ["B", "A"]
    assert result[0].component_product_id == 2
    assert result[0].quantity_per_unit == Decimal("1")
    assert lines[0].quantity_per_unit == Decimal("1")  # the input was not modified