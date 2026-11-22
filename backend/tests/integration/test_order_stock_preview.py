from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.inventory.models import Inventory, Reservation


@pytest.fixture()
def db(migrated_db):
    return migrated_db


def test_preview_uses_finished_stock_and_shares_components(
    client, db, new_product, make_bom, make_user, headers_for,
):
    first, second = new_product(), new_product()
    cap = new_product(product_type="COMPONENT")
    make_bom(first, [(cap, "2", "0")])
    make_bom(second, [(cap, "1", "0")])
    db.add_all([Inventory(product_id=first.id, available=2),
                Inventory(product_id=cap.id, available=5)])
    db.flush()
    headers = headers_for(make_user("sales"))
    lines = [{"product_id": first.id, "quantity": 2},
             {"product_id": second.id, "quantity": 3},
             {"product_id": first.id, "quantity": 2}]
    response = client.post("/api/orders/stock-preview", json={"lines": lines}, headers=headers)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert [Decimal(line["shortfall"]) for line in preview["finished_goods"]] == [2, 3]
    component, = preview["components"]
    assert component["product_id"] == cap.id
    assert Decimal(component["required"]) == 7
    assert Decimal(component["shortfall"]) == 2
    assert db.get(Inventory, cap.id).available == 5
    assert db.scalar(select(Reservation.id)) is None


def test_missing_bom_only_matters_when_stock_is_short(
    client, db, new_product, make_user, headers_for,
):
    product = new_product()
    db.add(Inventory(product_id=product.id, available=1))
    db.flush()
    headers = headers_for(make_user("sales"))
    line = {"product_id": product.id, "quantity": 1}
    path = "/api/orders/stock-preview"
    covered = client.post(path, json={"lines": [line]}, headers=headers)
    assert covered.status_code == 200
    assert covered.json()["components"] == []
    line["quantity"] = 2
    missing = client.post(path, json={"lines": [line]}, headers=headers)
    assert missing.status_code == 422
    assert "no active BOM" in missing.json()["detail"]
