"""The one place that writes audit events.

The helper adds the event to the caller's session and flushes, but does NOT commit.
That way the audit row and the change it describes succeed or fail together.
Never put secrets (passwords, hashes, tokens) into before / after / detail.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent


def _json_safe(value: Any) -> Any:
    """Convert Decimal, datetime, etc. to plain JSON values."""
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def record_audit_event(
    db: Session,
    *,
    action: str,
    entity_type: str,
    actor_user_id: int | None = None,
    entity_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        action=action,
        entity_type=entity_type,
        actor_user_id=actor_user_id,
        entity_id=entity_id,
        before=_json_safe(before),
        after=_json_safe(after),
        detail=_json_safe(detail),
        request_id=request_id,
    )
    db.add(event)
    db.flush()
    return event