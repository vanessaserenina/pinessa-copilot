from decimal import Decimal

import pytest

ROLES = ["admin", "sales", "purchasing", "warehouse", "approver"]


def tag_of(product):
    return product.sku.removeprefix("ZZB-SKU-")


def search(client, headers, **params):
    response = client.get("/api/products", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_products_need_login(client):
    assert client.get("/api/products").status_code == 401
    assert client.get("/api/products/1").status_code == 401


@pytest.mark.parametrize("role", ROLES)
def test_every_role_can_read_products(client, make_user, headers_for, role):
    headers = headers_for(make_user(role))
    assert client.get("/api/products?limit=1", headers=headers).status_code == 200


def test_search_by_sku_and_name_is_case_insensitive(client, make_user, headers_for, new_product):
    headers = headers_for(make_user("sales"))
    product = new_product()
    tag = tag_of(product)
    data = search(client, headers, q=tag)
    assert data["total"] == 1
    assert data["items"][0]["sku"] == product.sku
    assert search(client, headers, q=tag.upper())["total"] == 1
    assert search(client, headers, q=f"test PRODUCT {tag}")["total"] == 1


def test_every_search_word_must_match(client, make_user, headers_for, new_product):
    headers = headers_for(make_user("sales"))
    tag = tag_of(new_product())
    assert search(client, headers, q=f"{tag} thisworddoesnotexist")["total"] == 0


def test_like_wildcards_are_treated_as_plain_characters(client, make_user, headers_for, new_product):
    headers = headers_for(make_user("sales"))
    tag = tag_of(new_product())
    assert search(client, headers, q="%")["total"] == 0
    assert search(client, headers, q=f"{tag[:3]}_{tag[4:]}")["total"] == 0
    assert search(client, headers, q=f"{tag[:3]}%")["total"] == 0


def test_filters_for_type_source_and_bom(client, make_user, headers_for, new_product, make_bom):
    headers = headers_for(make_user("sales"))
    kit = new_product()
    part = new_product("COMPONENT")
    drafty = new_product()
    make_bom(kit, [(part, "1", "0")], version=1, status="active")
    make_bom(drafty, [(part, "1", "0")], version=1, status="draft")  # a draft is not an active BOM

    assert search(client, headers, q="ZZB-SKU-")["total"] == 3
    assert {i["sku"] for i in search(client, headers, q="ZZB-SKU-", product_type="COMPONENT")["items"]} == {part.sku}
    assert {i["sku"] for i in search(client, headers, q="ZZB-SKU-", has_bom="true")["items"]} == {kit.sku}
    assert {i["sku"] for i in search(client, headers, q="ZZB-SKU-", has_bom="false")["items"]} == {
        part.sku,
        drafty.sku,
    }
    assert search(client, headers, q="ZZB-SKU-", source="catalogue")["total"] == 0
    assert search(client, headers, q="ZZB-SKU-", source="synthetic")["total"] == 3


def test_inactive_products_are_hidden_unless_requested(client, db, make_user, headers_for, new_product):
    headers = headers_for(make_user("sales"))
    product = new_product()
    product.is_active = False
    db.flush()
    tag = tag_of(product)
    assert search(client, headers, q=tag)["total"] == 0
    assert search(client, headers, q=tag, include_inactive="true")["total"] == 1


def test_section_and_group_filters(client, make_user, headers_for, new_product):
    headers = headers_for(make_user("sales"))
    product = new_product()
    code = f"ZZB-CAT-{tag_of(product)}"
    item = search(client, headers, group=code)["items"][0]
    assert item["sku"] == product.sku
    assert item["category_code"] == code
    assert item["section_code"] == code  # a category without a parent is its own section
    assert search(client, headers, section=code)["total"] == 1
    assert search(client, headers, section="NO-SUCH-SECTION")["total"] == 0


def test_seeded_kit_section_counts(client, make_user, headers_for):
    headers = headers_for(make_user("sales"))
    total = search(client, headers, section="KIT", source="catalogue")["total"]
    if total == 0:
        pytest.skip("run the catalogue import first")
    assert total == 108
    with_bom = search(client, headers, section="KIT", has_bom="true")["total"]
    if with_bom == 0:
        pytest.skip("run seed_boms first")
    assert with_bom == 72


def test_paging_is_sorted_and_stable(client, make_user, headers_for, new_product):
    headers = headers_for(make_user("sales"))
    for _ in range(3):
        new_product()
    first = search(client, headers, q="ZZB-SKU-", limit=2)
    assert first["total"] == 3 and len(first["items"]) == 2
    second = search(client, headers, q="ZZB-SKU-", limit=2, offset=2)
    assert len(second["items"]) == 1
    skus = [i["sku"] for i in first["items"] + second["items"]]
    assert skus == sorted(skus) and len(set(skus)) == 3


def test_bad_parameters_are_rejected(client, make_user, headers_for):
    headers = headers_for(make_user("sales"))
    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"product_type": "WRONG"}, {"source": "x"}):
        assert client.get("/api/products", params=params, headers=headers).status_code == 422, params


def test_product_detail_shows_versions_and_active_bom(
    client, make_user, headers_for, new_product, make_bom
):
    headers = headers_for(make_user("warehouse"))
    kit = new_product()
    part = new_product("COMPONENT")
    make_bom(kit, [(part, "2", "5")], version=1, status="retired")
    make_bom(kit, [(part, "3", "0")], version=2, status="active")

    data = client.get(f"/api/products/{kit.id}", headers=headers).json()
    assert [b["version_number"] for b in data["boms"]] == [2, 1]
    assert [b["status"] for b in data["boms"]] == ["active", "retired"]
    assert data["has_active_bom"] is True
    assert data["active_bom"]["version_number"] == 2
    line = data["active_bom"]["items"][0]
    assert line["component_sku"] == part.sku
    assert Decimal(line["quantity_per_unit"]) == Decimal("3")

    component = client.get(f"/api/products/{part.id}", headers=headers).json()
    assert component["used_in_active_boms"] == 1  # the retired v1 does not count
    assert component["active_bom"] is None


def test_unknown_product_is_404(client, make_user, headers_for):
    headers = headers_for(make_user("sales"))
    assert client.get("/api/products/999999999", headers=headers).status_code == 404