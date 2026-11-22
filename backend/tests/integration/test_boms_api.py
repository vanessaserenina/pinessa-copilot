from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.modules.audit.models import AuditEvent
from app.modules.products.models import Bom

ROLES = ["admin", "sales", "purchasing", "warehouse", "approver"]


def lines_payload(lines, notes=None):
    return {
        "notes": notes,
        "items": [
            {"component_product_id": c.id, "quantity_per_unit": q, "scrap_percent": s}
            for c, q, s in lines
        ],
    }


def create_draft(client, headers, product, lines, notes=None):
    return client.post(
        f"/api/products/{product.id}/boms", json=lines_payload(lines, notes), headers=headers
    )


def bom_count(db, product, status=None):
    query = select(func.count()).select_from(Bom).where(Bom.product_id == product.id)
    if status:
        query = query.where(Bom.status == status)
    return db.scalar(query)


def bom_events(db, bom_id, action):
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type == "bom",
                AuditEvent.entity_id == bom_id,
                AuditEvent.action == action,
            )
        )
    )


def required_map(body):
    return {line["component_sku"]: Decimal(line["required"]) for line in body["lines"]}


def calculate(client, headers, product, quantity):
    return client.post(
        f"/api/products/{product.id}/bom-calculation", json={"quantity": quantity}, headers=headers
    )


# ---- calculation ----------------------------------------------------------
def test_calculation_worked_example(client, make_user, headers_for, new_product, make_bom):
    headers = headers_for(make_user("sales"))
    kit = new_product()
    brush, paste, carton, label = (new_product("COMPONENT") for _ in range(4))
    bom = make_bom(kit, [(brush, "1", "0"), (paste, "1", "0"), (carton, "1", "2"), (label, "1", "5")])

    response = calculate(client, headers, kit, 250)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["quantity"], body["bom_id"], body["bom_version"]) == (250, bom.id, 1)
    assert required_map(body) == {
        brush.sku: Decimal("250"),
        paste.sku: Decimal("250"),
        carton.sku: Decimal("255"),
        label.sku: Decimal("263"),
    }
    label_line = next(l for l in body["lines"] if l["component_sku"] == label.sku)
    assert Decimal(label_line["exact_required"]) == Decimal("262.5")


@pytest.mark.parametrize("role", ROLES)
def test_every_role_can_calculate(client, make_user, headers_for, new_product, make_bom, role):
    kit = new_product()
    make_bom(kit, [(new_product("COMPONENT"), "1", "0")])
    assert calculate(client, headers_for(make_user(role)), kit, 10).status_code == 200


def test_calculation_needs_login(client, new_product):
    kit = new_product()
    assert client.post(f"/api/products/{kit.id}/bom-calculation", json={"quantity": 1}).status_code == 401


def test_calculation_without_active_bom_is_a_conflict(
    client, make_user, headers_for, new_product, make_bom
):
    headers = headers_for(make_user("sales"))
    plain = new_product()
    assert calculate(client, headers, plain, 10).status_code == 409
    drafty = new_product()
    make_bom(drafty, [(new_product("COMPONENT"), "1", "0")], status="draft")
    assert calculate(client, headers, drafty, 10).status_code == 409


def test_calculation_for_unknown_product_is_404(client, make_user, headers_for):
    headers = headers_for(make_user("sales"))
    response = client.post("/api/products/999999999/bom-calculation", json={"quantity": 5}, headers=headers)
    assert response.status_code == 404


@pytest.mark.parametrize("quantity", [0, -5, 2.5, 2.0, True, "abc", None, 10_000_001])
def test_calculation_rejects_bad_quantities(
    client, make_user, headers_for, new_product, make_bom, quantity
):
    headers = headers_for(make_user("sales"))
    kit = new_product()
    make_bom(kit, [(new_product("COMPONENT"), "1", "0")])
    assert calculate(client, headers, kit, quantity).status_code == 422


# ---- creating drafts ------------------------------------------------------
def test_admin_creates_a_draft_bom(client, db, make_user, headers_for, new_product):
    admin = make_user("admin")
    headers = headers_for(admin)
    kit = new_product()
    a, b = new_product("COMPONENT"), new_product("COMPONENT")

    response = create_draft(client, headers, kit, [(a, "2", "0"), (b, "1.5", "5")], notes="  first draft ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["version_number"], body["status"], body["product_sku"]) == (1, "draft", kit.sku)
    assert body["notes"] == "first draft"
    assert body["is_synthetic"] is True
    assert [i["component_sku"] for i in body["items"]] == [a.sku, b.sku]
    assert Decimal(body["items"][1]["quantity_per_unit"]) == Decimal("1.5")
    assert Decimal(body["items"][1]["scrap_percent"]) == Decimal("5")

    events = bom_events(db, body["id"], "bom.created")
    assert len(events) == 1 and events[0].actor_user_id == admin.id
    assert len(events[0].after["lines"]) == 2
    assert client.get(f"/api/boms/{body['id']}", headers=headers).json() == body


def test_only_admin_can_create_boms(client, db, make_user, headers_for, new_product):
    kit = new_product()
    part = new_product("COMPONENT")
    for role in ("sales", "purchasing", "warehouse", "approver"):
        response = create_draft(client, headers_for(make_user(role)), kit, [(part, "1", "0")])
        assert response.status_code == 403, role
    assert bom_count(db, kit) == 0


def test_version_numbers_count_up_and_drafts_are_not_active(
    client, make_user, headers_for, new_product
):
    headers = headers_for(make_user("admin"))
    kit = new_product()
    part = new_product("COMPONENT")
    first = create_draft(client, headers, kit, [(part, "1", "0")]).json()
    second = create_draft(client, headers, kit, [(part, "2", "0")]).json()
    assert (first["version_number"], second["version_number"]) == (1, 2)
    assert calculate(client, headers, kit, 5).status_code == 409  # drafts are never used


def test_invalid_boms_are_rejected_by_the_rules(client, db, make_user, headers_for, new_product):
    headers = headers_for(make_user("admin"))
    kit = new_product()
    part = new_product("COMPONENT")
    inactive = new_product("COMPONENT")
    inactive.is_active = False
    db.flush()

    cases = {
        "same component twice": (kit, [(part, "1", "0"), (part, "2", "0")]),
        "product as its own component": (kit, [(kit, "1", "0")]),
        "inactive component": (kit, [(inactive, "1", "0")]),
        "BOM on a component-type product": (new_product("COMPONENT"), [(part, "1", "0")]),
    }
    for name, (product, lines) in cases.items():
        assert create_draft(client, headers, product, lines).status_code == 422, name
    assert bom_count(db, kit) == 0

    unknown = {"items": [{"component_product_id": 999999999, "quantity_per_unit": "1"}]}
    assert client.post(f"/api/products/{kit.id}/boms", json=unknown, headers=headers).status_code == 422


def test_invalid_bom_input_is_rejected_by_validation(client, make_user, headers_for, new_product):
    headers = headers_for(make_user("admin"))
    kit = new_product()
    part = new_product("COMPONENT")
    url = f"/api/products/{kit.id}/boms"
    line = {"component_product_id": part.id, "quantity_per_unit": "1", "scrap_percent": "0"}
    bad_bodies = [
        {"items": []},
        {"items": [{**line, "quantity_per_unit": "0"}]},
        {"items": [{**line, "quantity_per_unit": "-1"}]},
        {"items": [{**line, "scrap_percent": "101"}]},
        {"items": [{**line, "scrap_percent": "-1"}]},
        {"items": [{**line, "surprise_field": 1}]},
        {"items": [line], "surprise_field": 1},
        {"items": [{"quantity_per_unit": "1"}]},
    ]
    for body in bad_bodies:
        assert client.post(url, json=body, headers=headers).status_code == 422, body


def test_creating_a_bom_for_an_unknown_product_is_404(client, make_user, headers_for, new_product):
    headers = headers_for(make_user("admin"))
    part = new_product("COMPONENT")
    response = client.post(
        "/api/products/999999999/boms", json=lines_payload([(part, "1", "0")]), headers=headers
    )
    assert response.status_code == 404


# ---- activating -----------------------------------------------------------
def test_activating_the_first_version(client, db, make_user, headers_for, new_product):
    admin = make_user("admin")
    headers = headers_for(admin)
    kit = new_product()
    part = new_product("COMPONENT")
    bom_id = create_draft(client, headers, kit, [(part, "2", "0")]).json()["id"]

    response = client.post(f"/api/boms/{bom_id}/activate", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active" and body["effective_from"] is not None
    assert len(bom_events(db, bom_id, "bom.activated")) == 1
    assert bom_events(db, bom_id, "bom.retired") == []
    assert required_map(calculate(client, headers, kit, 10).json()) == {part.sku: Decimal("20")}


def test_activating_a_new_version_retires_the_old_one(client, db, make_user, headers_for, new_product):
    admin = make_user("admin")
    headers = headers_for(admin)
    kit = new_product()
    part = new_product("COMPONENT")
    v1 = create_draft(client, headers, kit, [(part, "1", "0")]).json()["id"]
    v2 = create_draft(client, headers, kit, [(part, "3", "0")]).json()["id"]
    assert client.post(f"/api/boms/{v1}/activate", headers=headers).status_code == 200
    assert client.post(f"/api/boms/{v2}/activate", headers=headers).status_code == 200

    assert client.get(f"/api/boms/{v1}", headers=headers).json()["status"] == "retired"
    assert client.get(f"/api/boms/{v2}", headers=headers).json()["status"] == "active"
    assert bom_count(db, kit, "active") == 1
    retired = bom_events(db, v1, "bom.retired")
    assert len(retired) == 1 and retired[0].detail["replaced_by_bom_id"] == v2
    assert retired[0].actor_user_id == admin.id
    body = calculate(client, headers, kit, 10).json()
    assert body["bom_version"] == 2
    assert required_map(body) == {part.sku: Decimal("30")}


def test_only_a_draft_can_be_activated(client, make_user, headers_for, new_product):
    headers = headers_for(make_user("admin"))
    kit = new_product()
    part = new_product("COMPONENT")
    v1 = create_draft(client, headers, kit, [(part, "1", "0")]).json()["id"]
    v2 = create_draft(client, headers, kit, [(part, "2", "0")]).json()["id"]
    assert client.post(f"/api/boms/{v1}/activate", headers=headers).status_code == 200
    assert client.post(f"/api/boms/{v1}/activate", headers=headers).status_code == 409  # already active
    assert client.post(f"/api/boms/{v2}/activate", headers=headers).status_code == 200
    assert client.post(f"/api/boms/{v1}/activate", headers=headers).status_code == 409  # retired now
    assert client.post("/api/boms/999999999/activate", headers=headers).status_code == 404


def test_cannot_activate_a_bom_with_an_inactive_component(
    client, db, make_user, headers_for, new_product
):
    headers = headers_for(make_user("admin"))
    kit = new_product()
    part = new_product("COMPONENT")
    bom_id = create_draft(client, headers, kit, [(part, "1", "0")]).json()["id"]
    part.is_active = False
    db.flush()
    response = client.post(f"/api/boms/{bom_id}/activate", headers=headers)
    assert response.status_code == 409
    assert part.sku in response.json()["detail"]
    assert bom_count(db, kit, "active") == 0


@pytest.mark.parametrize("role", [None, "sales", "purchasing", "warehouse", "approver"])
def test_bom_changes_require_admin(client, make_user, headers_for, new_product, role):
    kit = new_product()
    part = new_product("COMPONENT")
    admin_headers = headers_for(make_user("admin"))
    bom_id = create_draft(client, admin_headers, kit, [(part, "1", "0")]).json()["id"]
    headers = headers_for(make_user(role)) if role else {}
    expected = 403 if role else 401
    assert create_draft(client, headers, kit, [(part, "2", "0")]).status_code == expected
    assert client.post(f"/api/boms/{bom_id}/activate", headers=headers).status_code == expected
    assert client.delete(f"/api/boms/{bom_id}", headers=headers).status_code == expected
    assert client.get(f"/api/boms/{bom_id}", headers=admin_headers).json()["status"] == "draft"


def test_non_calculable_drafts_cannot_replace_an_active_version(
    client, db, make_user, headers_for, new_product, make_bom
):
    headers = headers_for(make_user("admin"))
    kit = new_product()
    part = new_product("COMPONENT")
    active = make_bom(kit, [(part, "1", "0")])
    empty = make_bom(kit, [], version=2, status="draft")
    draft = make_bom(kit, [(part, "2", "0")], version=3, status="draft")

    assert client.post(f"/api/boms/{empty.id}/activate", headers=headers).status_code == 409
    kit.is_active = False
    db.flush()
    assert client.post(f"/api/boms/{draft.id}/activate", headers=headers).status_code == 409
    assert client.get(f"/api/boms/{active.id}", headers=headers).json()["status"] == "active"


# ---- deleting drafts ------------------------------------------------------
def test_deleting_a_draft(client, db, make_user, headers_for, new_product):
    admin = make_user("admin")
    headers = headers_for(admin)
    kit = new_product()
    part = new_product("COMPONENT")
    bom_id = create_draft(client, headers, kit, [(part, "1", "0")]).json()["id"]

    assert client.delete(f"/api/boms/{bom_id}", headers=headers).status_code == 204
    assert client.get(f"/api/boms/{bom_id}", headers=headers).status_code == 404
    events = bom_events(db, bom_id, "bom.deleted")
    assert len(events) == 1 and events[0].actor_user_id == admin.id
    assert events[0].before["lines"][0]["component_sku"] == part.sku


def test_active_and_retired_boms_cannot_be_deleted(client, db, make_user, headers_for, new_product):
    headers = headers_for(make_user("admin"))
    kit = new_product()
    part = new_product("COMPONENT")
    v1 = create_draft(client, headers, kit, [(part, "1", "0")]).json()["id"]
    v2 = create_draft(client, headers, kit, [(part, "2", "0")]).json()["id"]
    client.post(f"/api/boms/{v1}/activate", headers=headers)
    assert client.delete(f"/api/boms/{v1}", headers=headers).status_code == 409  # active
    client.post(f"/api/boms/{v2}/activate", headers=headers)
    assert client.delete(f"/api/boms/{v1}", headers=headers).status_code == 409  # retired
    assert bom_count(db, kit) == 2


def test_delete_permissions_and_unknown_bom(client, make_user, headers_for, new_product):
    kit = new_product()
    part = new_product("COMPONENT")
    bom_id = create_draft(client, headers_for(make_user("admin")), kit, [(part, "1", "0")]).json()["id"]
    assert client.delete(f"/api/boms/{bom_id}", headers=headers_for(make_user("sales"))).status_code == 403
    assert client.delete("/api/boms/999999999", headers=headers_for(make_user("admin"))).status_code == 404


# ---- reading --------------------------------------------------------------
@pytest.mark.parametrize("role", ROLES)
def test_every_role_can_read_a_bom(client, make_user, headers_for, new_product, make_bom, role):
    kit = new_product()
    bom = make_bom(kit, [(new_product("COMPONENT"), "1", "0")])
    headers = headers_for(make_user(role))
    response = client.get(f"/api/boms/{bom.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["product_sku"] == kit.sku


def test_reading_boms_needs_login_and_unknown_is_404(client, make_user, headers_for):
    assert client.get("/api/boms/1").status_code == 401
    assert client.get("/api/boms/999999999", headers=headers_for(make_user("sales"))).status_code == 404
