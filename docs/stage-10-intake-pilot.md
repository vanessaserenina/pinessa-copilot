# Stage 10 intake pilot · 23 November 2026

On eight single-line synthetic orders, Pinessa extracted 35 of 40 scored fields correctly and chose the expected catalogue action on seven of eight lines. Both automatic selections picked the correct SKU. The sample contained five cases that required a reviewer to choose the product. All five reached review.

The model supplied a quantity for an order that gave none. Keep this case in the regression set and test whether quantities can be traced to the source before setting a pilot accuracy target.

## Per-case results

| Input | Extraction fields correct | Catalogue action | Order outcome |
|---|---:|---|---|
| Ambiguous colour, no date | 4/5 · quantity mismatch | Review as expected | Held |
| Approved alias | 4/5 · supplied an unstated SKU | Review; expected automatic CLIP-B | Sales confirmed CLIP-B |
| Exact SKU with conflicting colour | 5/5 | Review as expected | Held |
| Exact SKU with custom logo | 5/5 | Review as expected | Held |
| Exact SKU and compatible colour | 5/5 | Automatic CLIP-B as expected | Sales confirmed CLIP-B |
| Exact SKU with missing quantity | 4/5 · supplied an unstated quantity | Automatic CLIP-B as expected | Held |
| CSV with product description only | 4/5 · customer name mismatch | Review as expected | Sales confirmed CLIP-R |
| CSV with unknown stated SKU | 4/5 · customer name mismatch | Review as expected | Held |

Customer name matched in 6/8 cases, PO number in 8/8, delivery date in 8/8, quantity in 6/8, and stated SKU in 7/8. Every proposal contained the expected single line. Of three lines eligible for automatic selection, two were selected. There were zero incorrect automatic selections in two attempts. For all three confirmed orders, the resulting status was ready for delivery, the finished-goods shortfall was zero, and an audit event linked the source and order.

## Method and reproduction

The application under test was main commit 89db4d9. Each case used a newly migrated PostgreSQL schema with two synthetic products, one approved alias, and twenty cases of each product in stock. Six inputs used pasted text and two used uploaded CSVs. The live llama.cpp model alias was pinessa-local with prompt order-intake-v1 at temperature zero.

Inputs and expected outcomes are frozen together in data/synthetic/pilot. The evaluator scores exact strings, calendar dates, and numerically equal decimal quantities. A correctly absent value earns credit. Catalogue scoring compares the SKU selected before human review against the expected automatic SKU or referral. After scoring, the evaluator submits the specified Sales corrections for three scenarios and checks stock, status and audit. Eight scenarios executed successfully. Extraction mismatches are recorded as measurements.

From the backend directory, set LLM_MODEL to the running local model and PINESSA_PILOT to 1, then run the following command with the development test dependencies and PostgreSQL available.

```sh
python -m pytest tests/integration/test_pilot_evaluation.py -qs
```

This first batch measures single-line intake with a small controlled catalogue. Multi-line orders, PDF/OCR, XLSX, duplicate orders, stock shortages and lifecycle scenarios remain for the broader pilot. The next batch should retain missing quantities, unstated SKUs, and CSV customer names as explicit regression cases.

## Quantity follow-up

The pre-fix rerun on the local model reproduced 35/40 raw fields and 7/8 routing decisions. These were the five discrepancies recorded from the schema-valid replies before scoring Sales review.

| Case and field | Model value | Frozen expectation | Source evidence |
|---|---|---|---|
| Missing quantity | `1.0` | null | “Blue clips, SKU CLIP-B.” |
| Ambiguous colour, quantity | null | `8` | The model copied “8 cases of clips” into the description. |
| Description-only CSV, customer | Acorn Hotel | TEST Acorn Hotel | The customer cell includes the prefix TEST. |
| Unknown-SKU CSV, customer | Acorn Hotel | TEST Acorn Hotel | The same prefix was dropped. |
| Approved alias, stated SKU | desk fasteners | null | The product description is an approved alias. The source gives no SKU. The matcher referred the purported unknown SKU to Sales. |

The review API pairs each proposed description with a unique source clause. An explicit quantity in a recognized “number + unit + of + description” phrase retains its source spelling. Unsupported phrasing, competing clauses and missing evidence leave the field for Sales. The UI also leaves the confirmed quantity blank for fractional amounts or incompatible catalogue units. Confirmation requires Sales to submit a positive whole-unit quantity. The confirmation audit stores that submission alongside the source and order IDs. The original text, raw model replies and validated proposal remain available for comparison.

The opt-in evaluator prints the observed raw fields, source quantity shown to Sales, automatic SKU, and final confirmed line for each original case. The five additional scenarios have separate expectations in `data/synthetic/pilot-extended.json`. Each starts with 20 cases per SKU. The duplicate case resubmits identical source content. The shortage case requests 24 cases without a BOM and expects confirmation to fail with stock intact.

## Scoring a saved pilot

From `backend`, run `python -m app.scripts.pilot_scoring` with the database connection and PostgreSQL search path for the pilot instance. The report reads saved proposals, including pending ones, and prints JSON. It groups the original eight, extended five and shadow six separately. The local Sales rehearsal's existing `pilot.py results` command also includes these groups.

The report identifies a frozen source by file type and the SHA-256 of the original submission. For the generated extended-case PDF and workbook, pass the exact submitted files with `--scanned-pdf PATH` and `--xlsx PATH`. Cases absent from the database and sources without a frozen expectation are listed separately. Only matched sources enter the line counts. A matched source with missing review lines still contributes its expected lines, with no credit for those missing decisions.

Each row shows its frozen expectation, raw model line, initial Sales quantity and unit evidence, automatic SKU, and any audited confirmation. An incorrect automatic selection is a selected product on a line expecting a different SKU or a referral. Appropriate referrals are measured out of lines expecting a product referral. Quantity holds are measured separately against lines requiring Sales to resolve the quantity. A pending proposal or a blank quantity box caused solely by an unresolved product does not count as a quantity hold. The report compares source quantity text at its original precision, and compares the unit where the fixture specifies one.

## Similar catalogue variants

Commit `efe7ed3` froze five synthetic SKUs and thirteen line decisions in `data/synthetic/pilot-catalogue.json` before running the matcher from `4c7cfdd`. Six lines should select a SKU and seven should go to Sales. The two blue 12-pack variants have distinct box and case SKUs, so a conflict between the requested selling unit and the stated SKU requires a product referral.

| Measure | Before | After unit and colour fix |
|---|---:|---:|
| Incorrect selections / all automatic selections | 2/7 | 0/6 |
| Appropriate referrals / lines expected to need referral | 5/7 | 7/7 |
| Referrals / lines expected to match automatically | 1/6 | 0/6 |

The matcher selected B12-C for boxes and B12-B for cases despite the selling-unit conflicts. It referred S12-C even though sky blue, pack of 12, cases and the SKU all agreed. The colour check found the shorter option “blue” inside “sky blue”. The five appropriate referrals covered conflicting colour, conflicting pack size, unknown SKU, a shared family alias and an ambiguous description.

After the fix, both selling-unit conflicts went to Sales with the requested and catalogue units in the referral reason. The exact sky-blue SKU S12-C matched automatically. All thirteen decisions agreed with the original frozen expectations.

From `backend`, run `python -m pytest tests/integration/test_catalogue_matching_pilot.py -qs` with PostgreSQL available. The benchmark uses validated request lines and an isolated catalogue. It prints the expected and observed route for each line and the three totals, then checks every route against its frozen expectation.
