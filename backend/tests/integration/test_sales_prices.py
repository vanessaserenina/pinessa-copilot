from datetime import date, timedelta

import pytest

from app.modules.orders.models import ApprovedSalesPrice, CustomerPriceTier


@pytest.fixture()
def db(migrated_db):
    return migrated_db


def test_only_one_current_price_for_customer_tier_and_selling_unit(client, db, new_product, make_user, headers_for):
    product = new_product(unit="Box")
    headers = headers_for(make_user("sales"))
    db.add(CustomerPriceTier(customer_code="TEST", tier="Contract"))
    today = date.today()

    def add(book, price, **changes):
        fields = dict(product_id=product.id, selling_unit="Box", currency="IDR", tier="Contract",
                      valid_from=today, valid_through=today, book=book, unit_price=price)
        db.add(ApprovedSalesPrice(**(fields | changes)))
        db.flush()

    def quote(**changes):
        params = dict(product_id=product.id, customer_code="TEST", currency="IDR")
        response = client.get("/api/order-sources/approved-unit-price", params=params | changes, headers=headers)
        assert response.status_code == 200
        return response.json()

    add("retail", 100, tier="Retail")
    add("case", 200, selling_unit="Case")
    add("expired", 300, valid_from=today-timedelta(days=2), valid_through=today-timedelta(days=1))
    assert quote() is None
    add("current", 400)
    assert quote() == "400.0000"
    assert quote(customer_code="UNKNOWN") is None
    assert quote(currency="SGD") is None
    assert quote(product_id=new_product(unit="Box").id) is None
    add("overlap", 500)
    assert quote() is None
