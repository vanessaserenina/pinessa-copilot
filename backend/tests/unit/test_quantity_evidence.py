import pytest

from app.modules.ai.quantity import source_quantities
from app.modules.ai.schemas import RequestedLine


def requested(text, sku="CLIP-B7"):
    return RequestedLine(requested_text=text, stated_sku=sku, quantity="7", unit="cases",
                         specification=None, customization_notes=None)


@pytest.mark.parametrize("source", [
    "PO 7. Delivery 2027-07-07. Blue clips, SKU CLIP-B7.",
    "Blue clips, SKU CLIP-B7. Pack size: 7 cases of blue clips.",
    "Blue clips, SKU CLIP-B7 (7 pcs per pack).",
    "Blue clips, SKU CLIP-B7.\n7 cases of red clips, SKU CLIP-R.",
    "Blue clips, SKU CLIP-B7.\n7 cases of blue clips, SKU CLIP-X.",
])
def test_unrelated_numbers_do_not_supply_order_quantity(source):
    assert source_quantities(source, [requested("7 cases of blue clips, SKU CLIP-B7")]) == [None]


def test_each_line_keeps_its_own_quantity_and_precision():
    source = "2.50 cases of red clips, SKU CLIP-R.\nBlue clips, SKU CLIP-B7."
    lines = [requested("Blue clips"), requested("Red clips", sku="CLIP-R")]
    assert source_quantities(source, lines) == [
        None, {"quantity": "2.50", "unit": "cases", "normalized_unit": "case",
               "source_clause": "2.50 cases of red clips, SKU CLIP-R"}]


@pytest.mark.parametrize("source", [
    "Blue clips pack of 12, SKU CLIP-B7.",
    "2 cases of blue clips pack of 12, SKU OTHER.",
    "2 cases of blue clips pack of 12.",
    "2 cases of blue clips pack of 12, SKU CLIP-B7.\n8 cases of blue clips pack of 24, SKU CLIP-B7.",
])
def test_omitted_pack_requires_unique_sku_and_explicit_order_quantity(source):
    assert source_quantities(source, [requested("Blue clips")]) == [None]


def test_full_and_shortened_lines_cannot_share_the_same_clause():
    source = "2 cases of blue clips pack of 12, SKU CLIP-B7."
    assert source_quantities(source, [requested("Blue clips"), requested("Blue clips pack of 12")]) == [None, None]


def test_one_source_clause_cannot_supply_two_proposal_lines():
    assert source_quantities("7 cases of blue clips.", [requested("Blue clips")] * 2) == [None, None]


def test_pack_size_is_not_an_order_even_when_it_contains_a_quantity_phrase():
    assert source_quantities("Pack size:\n7 cases of blue clips.", [requested("Blue clips")]) == [None]
    assert source_quantities("7 cases of blue clips per pack.",
                             [requested("7 cases of blue clips per pack")]) == [None]


def test_pack_size_cell_is_not_an_order_line():
    text = "order_line\tpack_size\nBlue clips\t7 cases of blue clips"
    assert source_quantities(text, [requested("Blue clips")], "csv") == [None]
