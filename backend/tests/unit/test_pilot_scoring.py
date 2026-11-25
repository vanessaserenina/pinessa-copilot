from collections import Counter

from app.scripts.pilot_scoring import frozen_cases, score_lines


def match(index, sku, quantity):
    return {"line_index": index, "selected_product_id": 1 if sku else None,
            "status": "auto" if sku else "needs_review",
            "candidates": [{"product_id": 1, "sku": sku, "unit": "case"}] if sku else [],
            "source_quantity": {"quantity": quantity, "normalized_unit": "case", "unit": "cases"}
            if quantity else None}


def test_frozen_shadow_pending_counts():
    observed = {
        "S01": [match(0, "CLIP-R", "4")],
        "S02": [match(1, "CLIP-R", None), match(0, "CLIP-B", "2")],
        "S03": [match(0, "CLIP-B", "5")],
        "S04": [match(0, None, "3")],
        "S05": [match(0, None, "7")],
        "S06": [match(0, "CLIP-R", "2.50")],
    }
    totals = Counter()
    for group, name, _, truth in frozen_cases():
        if group == "shadow":
            rows, counts = score_lines(truth, observed[name], [], [])
            totals.update(counts)
            assert all(row["confirmed_line"] is None for row in rows)
            if name == "S06":
                assert rows[0]["source_quantity"]["quantity"] == "2.50"
                assert rows[0]["initial_confirmation_quantity"] is None
    assert totals == {"automatic_selections": 5, "incorrect_automatic_selections": 0,
                      "expected_referrals": 2, "appropriate_referrals": 2,
                      "expected_quantity_holds": 2, "appropriate_quantity_holds": 2,
                      "unexpected_quantity_holds": 0, "missing_review_lines": 0,
                      "extra_review_lines": 0, "expected_lines": 7, "correct_source_quantities": 7,
                      "correct_routes": 7}


def test_misselections_missing_and_extra_lines_are_visible_after_sales_correction():
    truth = [{"auto": "CLIP-R", "quantity": None}, {"auto": None, "quantity": "3"},
             {"auto": None, "quantity": None}]
    observed = [match(0, "CLIP-B", "1"), match(1, "CLIP-R", None), match(3, "CLIP-B", "5")]
    rows, counts = score_lines(truth, observed, [{"quantity": "1"}], [{"sku": "CLIP-R", "quantity": 6}])
    assert counts["incorrect_automatic_selections"] == counts["automatic_selections"] == 3
    assert counts["appropriate_referrals"] == counts["appropriate_quantity_holds"] == 0
    assert counts["expected_referrals"] == counts["expected_quantity_holds"] == 2
    assert counts["missing_review_lines"] == counts["extra_review_lines"] == 1
    assert counts["unexpected_quantity_holds"] == 1
    assert rows[0]["raw_model_line"]["quantity"] == "1"
    assert rows[0]["initial_confirmation_quantity"] == "1"
    assert rows[0]["confirmed_line"] == {"sku": "CLIP-R", "quantity": 6}
