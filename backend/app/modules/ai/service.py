from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.ai import provider
from app.modules.ai.models import AIExtraction
from app.modules.ai.schemas import StructuredOrder
from app.modules.audit.service import record_audit_event
from app.modules.orders.models import OrderSource


def extract_order(db: Session, source_id: int, actor_id: int) -> AIExtraction:
    # Serialize submissions for an immutable source, including concurrent HTTP retries.
    source = db.scalar(select(OrderSource).where(OrderSource.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(404, "Order source not found.")
    previous = db.scalar(select(AIExtraction).where(AIExtraction.order_source_id == source_id))
    if previous is not None:
        return previous
    if source.status != "text_extracted" or not source.raw_text:
        raise HTTPException(409, "Extract source text before requesting an order proposal.")
    model = settings.llm_model
    if not model:
        raise HTTPException(503, "Configure the local extraction model first.")

    outputs = []
    error = None
    validated = None
    for _ in range(2):
        try:
            choice = provider.request_order(source.raw_text, model, error)
        except provider.ProviderUnavailable as exc:
            raise HTTPException(503, "The local extraction provider is unavailable.") from exc
        outputs.append(choice.message.content)
        if choice.finish_reason != "stop":
            error = "Return a complete JSON response."
            continue
        try:
            order = StructuredOrder.model_validate_json(choice.message.content, strict=True)
        except ValidationError as exc:
            issue = exc.errors(include_input=False, include_url=False)[0]
            # Field names from extra keys can contain arbitrary source text.
            error = f"Output failed schema validation ({issue['type']}). Return all required fields with the specified types."
            continue
        validated = order.model_dump(mode="json")
        error = None
        break

    extraction = AIExtraction(
        order_source_id=source_id, model_name=model, prompt_version=provider.PROMPT_VERSION,
        raw_output=outputs, validated_output=validated, schema_valid=validated is not None,
        status="needs_review" if validated is not None else "needs_manual_handling", error_message=error,
    )
    db.add(extraction)
    db.flush()
    record_audit_event(db, action=f"ai_extraction.{extraction.status}", entity_type="ai_extraction",
                       entity_id=extraction.id, actor_user_id=actor_id,
                       detail={"source_id": source_id, "model": model, "prompt_version": extraction.prompt_version,
                               "attempts": len(outputs)})
    db.commit()
    return extraction
