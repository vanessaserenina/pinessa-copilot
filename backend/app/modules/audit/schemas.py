from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    actor_user_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    detail: dict[str, Any] | None
    request_id: str | None