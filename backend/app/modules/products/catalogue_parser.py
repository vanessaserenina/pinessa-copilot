"""Parser for the authorised product list (data/reference/product-list.txt).

Pure Python: it needs no database and no .env file, so it is easy to test.

How the text file is read:
  SECTION HEADER (all capitals)      -> top-level category
  "Pilihan <key>: a / b / c"         -> options shared by every item in the section
  "1. Name (opt / opt) (opt / opt)"  -> group (a category); children inherit its options
  unnumbered line under a group      -> product family (child of that group)
  group with no children             -> the group is also its own single family
Every combination of options becomes one product (SKU).
"""
from __future__ import annotations

import itertools
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Short codes used to build category codes and SKUs. Edit here to change them.
DEFAULT_SECTION_PREFIXES: dict[str, str] = {
    "PERLENGKAPAN MANDI": "MND",
    "KIT AMENITI": "KIT",
    "HANDUK & LINEN": "LIN",
    "SANDAL HOTEL": "SDL",
    "PERLENGKAPAN MAKAN": "MKN",
    "AKSESORI KAMAR": "KMR",
    "ALAT TULIS": "ATL",
    "KEMASAN & TAS": "KMS",
}

# Order in which option groups appear in SKU suffixes (unknown keys go last).
OPTION_ORDER = ["size", "packaging", "finishing", "branding"]
# Lines whose meaning has been confirmed by the business owner. The key is the
# original line in lower case; the value is (family name, options for that line).
# The options replace the group's options of the same kind.
LINE_OVERRIDES: dict[str, tuple[str, dict[str, list[str]]]] = {
    "stirrer logo / non logo": ("Stirrer", {"branding": ["logo", "non_logo"]}),
}
VALUE_ALIASES = {"nonpackaging": "non_packaging", "nonlogo": "non_logo"}
PACKAGING_VALUES = {"packaging", "non_packaging"}
BRANDING_VALUES = {
    "logo",
    "non_logo",
    "custom",
    "custom_design",
    "custom_printing",
    "custom_logo",
}
SIZE_PATTERN = re.compile(r"^\d+(gr|g|ml)$")

VALUE_ABBREVIATIONS = {
    "packaging": "PKG",
    "non_packaging": "NPKG",
    "logo": "LOGO",
    "non_logo": "NLOGO",
    "custom_design": "CDSN",
    "custom_printing": "CPRT",
    "custom_logo": "CLOGO",
    "custom_packaging": "CPKG",
    "kotak": "KTK",
    "plastik": "PLS",
    "plastik_doff": "PLSD",
    "border": "BRD",
    "emboss": "EMB",
    "standard": "STD",
}

_PAREN = re.compile(r"\(([^)]*)\)")
_NUMBERED = re.compile(r"^(\d+)\.(?!\d)\s*(\S.*)$")
_SPACED_SLASH = re.compile(r"\s/\s")


# --------------------------------------------------------------------------
# Result objects
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ParseIssue:
    code: str
    line: int | None
    message: str


@dataclass(frozen=True)
class ParsedCategory:
    code: str
    name: str
    parent_code: str | None
    sort_order: int


@dataclass(frozen=True)
class ParsedProduct:
    sku: str
    name: str
    variant_key: str
    attributes: dict[str, str]


@dataclass(frozen=True)
class ParsedFamily:
    code: str
    name: str
    category_code: str
    option_groups: dict[str, list[str]]
    is_custom_allowed: bool
    source_label: str
    source_line: int
    products: tuple[ParsedProduct, ...]


@dataclass
class ParseResult:
    categories: list[ParsedCategory] = field(default_factory=list)
    families: list[ParsedFamily] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)

    @property
    def products(self) -> list[ParsedProduct]:
        return [product for family in self.families for product in family.products]


# --------------------------------------------------------------------------
# Small text helpers
# --------------------------------------------------------------------------
def normalise_spaces(text: str) -> str:
    return " ".join(text.split())


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").upper()


def normalise_value(text: str) -> str:
    value = normalise_spaces(text).lower().replace(" ", "_")
    return VALUE_ALIASES.get(value, value)


def classify_option_key(values: list[str]) -> str | None:
    """Guess what kind of option a list of values is. None means 'unknown'."""
    value_set = set(values)
    if value_set <= PACKAGING_VALUES:
        return "packaging"
    if value_set <= BRANDING_VALUES:
        return "branding"
    if all(SIZE_PATTERN.match(value) for value in values):
        return "size"
    return None


def abbreviate(value: str) -> str:
    return VALUE_ABBREVIATIONS.get(value) or slugify(value) or "X"


def _sort_option_keys(options: dict[str, list[str]]) -> list[str]:
    def rank(key: str) -> tuple[int, str]:
        return (OPTION_ORDER.index(key) if key in OPTION_ORDER else len(OPTION_ORDER), key)

    return sorted(options, key=rank)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _is_section_header(line: str) -> bool:
    return line.isupper() and not _NUMBERED.match(line)


def read_text(path: Path | str) -> str:
    """Read the file as UTF-8 (also accepts a Windows 'UTF-8 with BOM' file)."""
    return Path(path).read_text(encoding="utf-8-sig")


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------
@dataclass
class _Section:
    prefix: str
    category_code: str
    options: dict[str, list[str]] = field(default_factory=dict)
    last_number: int | None = None
    group_count: int = 0


@dataclass
class _Group:
    name: str
    raw: str
    line_no: int
    category_code: str
    options: dict[str, list[str]]  # section options + the group's own options
    children: list[tuple[str, int]] = field(default_factory=list)  # (text, line number)


class _Parser:
    def __init__(self, section_prefixes: dict[str, str]):
        self.prefixes = {normalise_spaces(k).upper(): v for k, v in section_prefixes.items()}
        self.result = ParseResult()
        self.section: _Section | None = None
        self.group: _Group | None = None
        self.section_count = 0
        self.used_category_codes: set[str] = set()
        self.used_family_codes: set[str] = set()
        self.used_skus: set[str] = set()

    # ---- helpers ---------------------------------------------------------
    def issue(self, code: str, line: int | None, message: str) -> None:
        self.result.issues.append(ParseIssue(code, line, message))

    def _unique(self, base: str, used: set[str], kind: str, line_no: int, max_len: int) -> str:
        code, counter = base, 2
        while code in used:
            code = f"{base}-{counter}"
            counter += 1
        if code != base:
            self.issue(
                "DUPLICATE_CODE",
                line_no,
                f"The {kind} code '{base}' was already used; '{code}' was generated instead.",
            )
        if len(code) > max_len:
            raise ValueError(f"Generated {kind} code is too long ({len(code)} > {max_len}): {code}")
        used.add(code)
        return code

    def _slug(self, name: str, line_no: int) -> str:
        slug = slugify(name)
        if not slug:
            self.issue("EMPTY_SLUG", line_no, f"Could not build a code from '{name}'; used 'ITEM'.")
            return "ITEM"
        return slug

    def _split_name(self, text: str, line_no: int) -> tuple[str, list[str]]:
        parens = _PAREN.findall(text)
        name = normalise_spaces(_PAREN.sub(" ", text))
        if "(" in name or ")" in name:
            self.issue("UNBALANCED_PARENTHESES", line_no, f"Unbalanced parentheses in '{text}'.")
        if not name:
            self.issue("EMPTY_NAME", line_no, f"No name found in '{text}'.")
            name = normalise_spaces(text)
        return name, parens

    def _read_parentheses(self, parens: list[str], line_no: int) -> dict[str, list[str]]:
        options: dict[str, list[str]] = {}
        for content in parens:
            values = _dedupe([normalise_value(p) for p in content.split("/") if p.strip()])
            if len(values) >= 2:
                key = classify_option_key(values)
                if key is None:
                    key = "option"
                    self.issue(
                        "UNKNOWN_OPTION_GROUP",
                        line_no,
                        f"Could not tell what kind of option '({content})' is; stored as 'option'.",
                    )
            elif len(values) == 1 and "custom" in values[0]:
                key = "branding"
            elif len(values) == 1:
                self.issue(
                    "NOTE_IN_PARENTHESES",
                    line_no,
                    f"'({content})' is a single value, so it was treated as a description, "
                    "not an option (the original line is kept in source_label).",
                )
                continue
            else:
                self.issue("EMPTY_PARENTHESES", line_no, "Empty parentheses were ignored.")
                continue
            if key in options:
                self.issue(
                    "DUPLICATE_OPTION_GROUP",
                    line_no,
                    f"Two '{key}' option lists on one line; the last one was used.",
                )
            options[key] = values
        return options

    # ---- main loop -------------------------------------------------------
    def run(self, text: str) -> ParseResult:
        for line_no, raw in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
            line = normalise_spaces(raw)
            if line:
                self._handle_line(line, line_no)
        self._flush_group()
        self.result.issues.sort(key=lambda item: item.line or 0)
        return self.result

    def _handle_line(self, line: str, line_no: int) -> None:
        if _is_section_header(line):
            self._start_section(line, line_no)
            return
        if self.section is None:
            self.issue("ORPHAN_LINE", line_no, f"Line before any section header was skipped: '{line}'.")
            return
        if line.lower().startswith("pilihan "):
            self._read_section_options(line, line_no)
            return
        match = _NUMBERED.match(line)
        if match:
            self._start_group(int(match.group(1)), match.group(2), line, line_no)
        elif self.group is None:
            self._start_group(None, line, line, line_no)
        else:
            self.group.children.append((line, line_no))

    # ---- sections --------------------------------------------------------
    def _start_section(self, name: str, line_no: int) -> None:
        self._flush_group()
        self.section_count += 1
        prefix = self.prefixes.get(name.upper())
        if prefix is None:
            prefix = slugify(name).replace("-", "")[:3] or "SEC"
            self.issue(
                "UNKNOWN_SECTION_PREFIX",
                line_no,
                f"No prefix configured for section '{name}'; using '{prefix}'. "
                "Add it to DEFAULT_SECTION_PREFIXES.",
            )
        code = self._unique(prefix, self.used_category_codes, "category", line_no, 80)
        self.result.categories.append(ParsedCategory(code, name, None, self.section_count))
        self.section = _Section(prefix=prefix, category_code=code)

    def _read_section_options(self, line: str, line_no: int) -> None:
        assert self.section is not None
        body = line[len("pilihan"):].strip()
        if ":" not in body:
            self.issue("BAD_OPTION_LINE", line_no, f"Expected 'Pilihan <name>: a / b / c' but found '{line}'.")
            return
        key_text, values_text = body.split(":", 1)
        key = normalise_value(key_text)
        values = _dedupe(
            [v for v in (normalise_value(_PAREN.sub("", part)) for part in values_text.split("/")) if v]
        )
        if not key or not values:
            self.issue("BAD_OPTION_LINE", line_no, f"No usable options found in '{line}'.")
            return
        if self.section.group_count > 0:
            self.issue(
                "PILIHAN_AFTER_ITEMS",
                line_no,
                "This option line comes after items; it only applies to the items that follow it.",
            )
        if key in self.section.options:
            self.issue("DUPLICATE_OPTION_GROUP", line_no, f"Option list '{key}' was given twice; the last one was used.")
        self.section.options[key] = values

    # ---- groups ----------------------------------------------------------
    def _check_number(self, number: int | None, name: str, line_no: int) -> None:
        assert self.section is not None
        last = self.section.last_number
        if number is None:
            number = (last or 0) + 1
            self.issue("UNNUMBERED_ITEM", line_no, f"'{name}' has no number; treated as item {number}.")
        elif last is None:
            if number != 1:
                self.issue("NUMBER_GAP", line_no, f"Numbering in this section starts at {number}, not 1.")
        elif number == last:
            self.issue("DUPLICATE_NUMBER", line_no, f"Number {number} is used twice (second use: '{name}').")
            return
        elif number < last:
            self.issue("NUMBER_OUT_OF_ORDER", line_no, f"Number {number} comes after {last} ('{name}').")
        elif number > last + 1:
            missing = str(last + 1) if number == last + 2 else f"{last + 1}-{number - 1}"
            self.issue("NUMBER_GAP", line_no, f"Number {missing} is missing before '{name}'.")
        self.section.last_number = number

    def _start_group(self, number: int | None, text: str, raw: str, line_no: int) -> None:
        self._flush_group()
        section = self.section
        assert section is not None
        name, parens = self._split_name(text, line_no)
        own_options = self._read_parentheses(parens, line_no)
        self._check_number(number, name, line_no)
        section.group_count += 1
        code = self._unique(
            f"{section.prefix}-{self._slug(name, line_no)}",
            self.used_category_codes,
            "category",
            line_no,
            80,
        )
        self.result.categories.append(ParsedCategory(code, name, section.category_code, section.group_count))
        self.group = _Group(
            name=name,
            raw=raw,
            line_no=line_no,
            category_code=code,
            options={**section.options, **own_options},
        )

    def _flush_group(self) -> None:
        group, self.group = self.group, None
        if group is None:
            return
        section = self.section
        assert section is not None
        if not group.children:
            self._build_family(section, group.category_code, group.name, group.options, group.raw, group.line_no)
            return
        for text, line_no in group.children:
            override = LINE_OVERRIDES.get(text.lower())
            if override is not None:
                name, own_options = override
                options = {**group.options, **{k: list(v) for k, v in own_options.items()}}
            else:
                name, parens = self._split_name(text, line_no)
                options = {**group.options, **self._read_parentheses(parens, line_no)}
            self._build_family(section, group.category_code, name, options, f"{group.raw} > {text}", line_no)

    # ---- families and products ------------------------------------------
    def _build_family(
        self,
        section: _Section,
        category_code: str,
        name: str,
        options: dict[str, list[str]],
        label: str,
        line_no: int,
    ) -> None:
        if _SPACED_SLASH.search(name):
            self.issue(
                "SLASH_IN_NAME",
                line_no,
                f"'{name}' contains ' / ' outside parentheses. It was kept as part of the name; "
                "please check what it should mean.",
            )
        option_groups = {key: list(options[key]) for key in _sort_option_keys(options)}
        code = self._unique(
            f"{section.prefix}-{self._slug(name, line_no)}",
            self.used_family_codes,
            "family",
            line_no,
            120,
        )
        is_custom = "custom" in name.lower() or any(
            value.startswith("custom") for values in option_groups.values() for value in values
        )
        products = self._build_products(code, name, option_groups, line_no)
        self.result.families.append(
            ParsedFamily(
                code=code,
                name=name,
                category_code=category_code,
                option_groups=option_groups,
                is_custom_allowed=is_custom,
                source_label=label,
                source_line=line_no,
                products=tuple(products),
            )
        )

    def _build_products(
        self, family_code: str, name: str, option_groups: dict[str, list[str]], line_no: int
    ) -> list[ParsedProduct]:
        if not option_groups:
            sku = self._unique(family_code, self.used_skus, "SKU", line_no, 120)
            return [ParsedProduct(sku=sku, name=name, variant_key="default", attributes={})]
        keys = list(option_groups)
        products: list[ParsedProduct] = []
        for combo in itertools.product(*(option_groups[key] for key in keys)):
            attributes = dict(zip(keys, combo))
            suffix = "-".join(abbreviate(value) for value in combo)
            sku = self._unique(f"{family_code}-{suffix}", self.used_skus, "SKU", line_no, 120)
            label = ", ".join(value.replace("_", " ") for value in combo)
            variant_key = "|".join(f"{key}={attributes[key]}" for key in sorted(attributes))
            products.append(
                ParsedProduct(sku=sku, name=f"{name} ({label})", variant_key=variant_key, attributes=attributes)
            )
        return products


def parse_catalogue(text: str, section_prefixes: dict[str, str] | None = None) -> ParseResult:
    """Turn the text of the product list into categories, families and products."""
    return _Parser(section_prefixes or DEFAULT_SECTION_PREFIXES).run(text)