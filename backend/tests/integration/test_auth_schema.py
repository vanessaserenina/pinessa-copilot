from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.modules.audit.service import record_audit_event
from app.modules.auth.models import User
from app.modules.products.models import ProductAlias, ProductCategory, ProductFamily


def test_email_must_be_lowercase(db, make_user):
    user = make_user("sales")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(text("UPDATE users SET email = upper(email) WHERE id = :id"), {"id": user.id})


def test_duplicate_email_is_rejected(db, make_user):
    user = make_user("sales")
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(
                User(
                    email=user.email,
                    full_name="x",
                    password_hash="x",
                    role_id=user.role_id,
                )
            )
            db.flush()


def test_alias_approver_must_be_a_real_user(db):
    category = ProductCategory(code="ZZ-AUTH-CAT", name="x")
    db.add(category)
    db.flush()
    family = ProductFamily(
        category_id=category.id, code="ZZ-AUTH-FAM", name="x", source="synthetic"
    )
    db.add(family)
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(
                ProductAlias(
                    family_id=family.id,
                    alias_text="x",
                    alias_normalized="x",
                    approved_by_user_id=999_999_999,
                )
            )
            db.flush()


def test_audit_events_cannot_be_updated(db):
    event = record_audit_event(db, action="test.event", entity_type="test")
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(
                text("UPDATE audit_events SET action = 'tampered' WHERE id = :id"), {"id": event.id}
            )


def test_audit_events_cannot_be_deleted(db):
    event = record_audit_event(db, action="test.event", entity_type="test")
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": event.id})


def test_audit_helper_converts_values_to_json(db):
    event = record_audit_event(
        db,
        action="test.event",
        entity_type="test",
        after={"when": datetime(2026, 1, 1, 12, 0), "amount": Decimal("1.5")},
    )
    assert event.after["amount"] == "1.5"