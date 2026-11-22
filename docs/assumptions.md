# Assumptions and decisions

## Confirmed
- A1  One warehouse, one currency (IDR, whole rupiah); tax rate configurable.
- A2  Pinessa assembles kits from components; some products are sold without assembly.
- A3  Single-level BOMs in the MVP.
- A4  BOMs are synthetic unless real BOMs are explicitly authorised.
- A5  Prices come from a synthetic price list; AI never supplies prices.
- A6  Every AI-extracted order is human-reviewed in the MVP.
- A7  No external sending (supplier POs, customer confirmations) in the MVP.
- A8  Roles: Admin, Sales, Purchasing, Warehouse/Production, Approver.
- A9  Order status flow as in the Stage planning document.
- A10 Deterministic core first, AI intake at Stage 8.
- A11 Mantine, SQLAlchemy + Alembic, JWT auth, Docker Compose.
- D1  One SKU per option combination (including section-level options).
- D2  SKU codes are system-generated, e.g. MND-SABUN-BATANG-BULAT-PKG-LOGO.
- D3  Repeated names stay separate families under their own category.
- D4  "custom" markers set is_custom_allowed; custom orders always need human review.
- Q1-Q7 (Stage 3): BOMs only for 6 kit families, one BOM per SKU, scrap 0/2/5% with round-up on the
  total, catalogue components counted per packed unit (confirm per item), BOM management admin-only,
  sessionStorage for the login token, ILIKE search until Stage 8.

## Open
- Stirrer line "Stirrer logo / non logo" = a plain stirrer offered with or without logo (family "Stirrer", 2 SKUs).
- "Sandal closed towel" (SDL): possibly "closed toe", imported as written.
- Which kit items really have BOMs (decide before Stage 3).