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
