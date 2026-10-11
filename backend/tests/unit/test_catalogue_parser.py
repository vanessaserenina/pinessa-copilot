import textwrap
from pathlib import Path

import pytest

from app.modules.products.catalogue_parser import parse_catalogue, read_text

REAL_FILE = Path(__file__).resolve().parents[3] / "data" / "reference" / "product-list.txt"

SAMPLE = """\
PERLENGKAPAN MANDI
1. Sabun
Sabun batang bulat (packaging / non packaging) (logo/non logo)
Sabun packaging kotak (logo / non logo / custom design)
2. Pasta Gigi
Pasta gigi 5gr delident
Pasta gigi formula (5gr/8gr)
3. Shower Cap

KIT AMENITI
Pilihan packaging: kotak / plastik / plastik doff / custom packaging
Pilihan branding: logo / non logo / custom design
1. Dental Kit
2. Shower cap

HANDUK & LINEN
Pilihan finishing: border / emboss / standard (polos)
Pilihan branding: logo / non logo / custom design
Handuk mandi
2. Handuk tangan

PERLENGKAPAN MAKAN
1. Stirrer (logo / non logo / custom design)
Stirrer kayu
Stirrer logo / non logo
2. Sedotan (packaging/nonpackaging)
Sedotan putih
"""


def parse(body: str, section: str = "PERLENGKAPAN MANDI"):
    return parse_catalogue(f"{section}\n{textwrap.dedent(body)}")


def family(result, code):
    return next(f for f in result.families if f.code == code)


def codes(result):
    return [issue.code for issue in result.issues]


def test_options_make_cartesian_product():
    result = parse("1. Sabun\nSabun batang bulat (packaging / non packaging) (logo/non logo)\n")
    fam = family(result, "MND-SABUN-BATANG-BULAT")
    assert {p.sku for p in fam.products} == {
        "MND-SABUN-BATANG-BULAT-PKG-LOGO",
        "MND-SABUN-BATANG-BULAT-PKG-NLOGO",
        "MND-SABUN-BATANG-BULAT-NPKG-LOGO",
        "MND-SABUN-BATANG-BULAT-NPKG-NLOGO",
    }
    assert fam.option_groups == {
        "packaging": ["packaging", "non_packaging"],
        "branding": ["logo", "non_logo"],
    }
    assert codes(result) == []


def test_group_without_children_becomes_one_family():
    result = parse("1. Shower Cap\n")
    fam = family(result, "MND-SHOWER-CAP")
    assert len(fam.products) == 1
    product = fam.products[0]
    assert (product.sku, product.variant_key, product.attributes) == ("MND-SHOWER-CAP", "default", {})
    assert [c.code for c in result.categories] == ["MND", "MND-SHOWER-CAP"]


def test_size_options():
    result = parse("1. Pasta Gigi\nPasta gigi formula (5gr/8gr)\n")
    fam = family(result, "MND-PASTA-GIGI-FORMULA")
    assert {p.sku for p in fam.products} == {"MND-PASTA-GIGI-FORMULA-5GR", "MND-PASTA-GIGI-FORMULA-8GR"}
    assert fam.option_groups == {"size": ["5gr", "8gr"]}


def test_section_options_apply_to_every_item():
    result = parse_catalogue(
        "KIT AMENITI\n"
        "Pilihan packaging: kotak / plastik / plastik doff / custom packaging\n"
        "Pilihan branding: logo / non logo / custom design\n"
        "1. Dental Kit\n"
    )
    fam = family(result, "KIT-DENTAL-KIT")
    skus = {p.sku for p in fam.products}
    assert len(fam.products) == 12
    assert "KIT-DENTAL-KIT-PLSD-NLOGO" in skus
    assert "KIT-DENTAL-KIT-CPKG-CDSN" in skus
    assert fam.is_custom_allowed is True


def test_parenthesised_annotation_in_option_line_is_dropped():
    result = parse_catalogue(
        "HANDUK & LINEN\nPilihan finishing: border / emboss / standard (polos)\n1. Handuk mandi\n"
    )
    assert family(result, "LIN-HANDUK-MANDI").option_groups["finishing"] == ["border", "emboss", "standard"]


def test_unnumbered_first_item_is_flagged_and_numbering_continues():
    result = parse_catalogue("HANDUK & LINEN\nHanduk mandi\n2. Handuk tangan\n")
    assert codes(result) == ["UNNUMBERED_ITEM"]
    assert {f.code for f in result.families} == {"LIN-HANDUK-MANDI", "LIN-HANDUK-TANGAN"}


def test_duplicate_and_missing_numbers_are_reported():
    result = parse("1. A\n2. B\n2. C\n4. D\n")
    assert codes(result) == ["DUPLICATE_NUMBER", "NUMBER_GAP"]


def test_children_inherit_group_options():
    result = parse("1. Stirrer (logo / non logo / custom design)\nStirrer kayu\nStirrer plastik\n")
    kayu = family(result, "MND-STIRRER-KAYU")
    assert len(kayu.products) == 3
    assert kayu.is_custom_allowed is True
    assert codes(result) == []
    assert next(c for c in result.categories if c.code == "MND-STIRRER").name == "Stirrer"


def test_confirmed_line_override_for_stirrer_logo_non_logo():
    result = parse("1. Stirrer (logo / non logo / custom design)\nStirrer logo / non logo\n")
    fam = family(result, "MND-STIRRER")
    assert fam.name == "Stirrer"
    assert fam.option_groups == {"branding": ["logo", "non_logo"]}
    assert {p.sku for p in fam.products} == {"MND-STIRRER-LOGO", "MND-STIRRER-NLOGO"}
    assert fam.is_custom_allowed is False
    assert "Stirrer logo / non logo" in fam.source_label
    assert codes(result) == []


def test_unconfirmed_slash_in_name_still_warns():
    result = parse("1. Sabun\nSabun mandi / cuci\n")
    assert codes(result) == ["SLASH_IN_NAME"]


def test_child_options_override_group_options():
    result = parse(
        "1. Hanger (logo/non logo)\n"
        "Hanger kayu (logo / non logo / custom design)\n"
        "Hanger plastik\n"
    )
    assert len(family(result, "MND-HANGER-KAYU").products) == 3
    assert len(family(result, "MND-HANGER-PLASTIK").products) == 2


def test_single_custom_marker_becomes_branding_option():
    result = parse("1. Buku Catatan (custom)\n")
    fam = family(result, "MND-BUKU-CATATAN")
    assert fam.option_groups == {"branding": ["custom"]}
    assert fam.is_custom_allowed is True
    assert [p.sku for p in fam.products] == ["MND-BUKU-CATATAN-CUSTOM"]


def test_descriptive_parentheses_are_not_options():
    result = parse("1. Toiletries Kit (bag & toiletries)\n")
    fam = family(result, "MND-TOILETRIES-KIT")
    assert fam.name == "Toiletries Kit"
    assert fam.option_groups == {}
    assert codes(result) == ["NOTE_IN_PARENTHESES"]
    assert "bag & toiletries" in fam.source_label


def test_nonpackaging_spelling_is_normalised():
    result = parse("1. Sedotan (packaging/nonpackaging)\nSedotan putih\n")
    assert family(result, "MND-SEDOTAN-PUTIH").option_groups == {"packaging": ["packaging", "non_packaging"]}


def test_custom_in_name_marks_family_custom():
    result = parse("1. Sisir/Comb\nSisir custom logo\nSisir handle\n")
    assert family(result, "MND-SISIR-CUSTOM-LOGO").is_custom_allowed is True
    assert family(result, "MND-SISIR-HANDLE").is_custom_allowed is False
    assert next(c for c in result.categories if c.code == "MND-SISIR-COMB").name == "Sisir/Comb"


def test_lines_before_first_section_are_reported():
    result = parse_catalogue("stray line\nPERLENGKAPAN MANDI\n1. Razor\n")
    assert codes(result) == ["ORPHAN_LINE"]
    assert [f.code for f in result.families] == ["MND-RAZOR"]


def test_unknown_section_gets_fallback_prefix():
    result = parse_catalogue("KATEGORI BARU\n1. Barang\n")
    assert "UNKNOWN_SECTION_PREFIX" in codes(result)
    assert result.categories[0].code == "KAT"
    assert result.families[0].code == "KAT-BARANG"


def test_option_line_after_items_is_flagged():
    result = parse_catalogue("KIT AMENITI\n1. A\nPilihan branding: logo / non logo\n")
    assert "PILIHAN_AFTER_ITEMS" in codes(result)
    assert family(result, "KIT-A").option_groups == {}


def test_duplicate_names_get_unique_codes():
    result = parse("1. Laundry Bag\n2. Laundry Bag\n")
    assert [f.code for f in result.families] == ["MND-LAUNDRY-BAG", "MND-LAUNDRY-BAG-2"]
    assert "DUPLICATE_CODE" in codes(result)


def test_same_name_in_different_sections_stays_separate():
    result = parse_catalogue("PERLENGKAPAN MANDI\n8. Shower Cap\nKIT AMENITI\n5. Shower cap\n")
    assert {f.code for f in result.families} == {"MND-SHOWER-CAP", "KIT-SHOWER-CAP"}


def test_windows_line_endings_and_bom(tmp_path):
    path = tmp_path / "list.txt"
    path.write_bytes(b"\xef\xbb\xbfPERLENGKAPAN MANDI\r\n1. Razor\r\n")
    result = parse_catalogue(read_text(path))
    assert [c.code for c in result.categories] == ["MND", "MND-RAZOR"]


def test_parsing_is_repeatable_and_codes_are_unique():
    first = parse_catalogue(SAMPLE)
    assert first == parse_catalogue(SAMPLE)
    skus = [p.sku for p in first.products]
    assert len(skus) == len(set(skus))
    assert len({f.code for f in first.families}) == len(first.families)


@pytest.mark.skipif(not REAL_FILE.exists(), reason="authorised product list not present")
def test_real_file_matches_hand_count():
    """Expected numbers come from counting the file by hand, not from running the parser."""
    result = parse_catalogue(read_text(REAL_FILE))
    assert len([c for c in result.categories if c.parent_code is None]) == 8
    assert len(result.categories) == 77
    assert len(result.families) == 106
    assert len(result.products) == 396
    assert sorted({i.code for i in result.issues}) == [
        "DUPLICATE_NUMBER",
        "NOTE_IN_PARENTHESES",
        "NUMBER_GAP",
        "UNNUMBERED_ITEM",
    ]
    assert len(result.issues) == 4