import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.audit.service import record_audit_event
from app.modules.orders.models import OrderSource


def extract_source_text(db: Session, source_id: int, actor_id: int) -> OrderSource:
    source = db.scalar(select(OrderSource).where(OrderSource.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(404, "Order source not found.")
    if source.status == "text_extracted":
        return source

    if source.source_type != "text":
        # Isolate native document parsers and bound their runtime per source.
        worker = Path(__file__).with_name("source_text_worker.py")
        path = settings.order_source_storage / source.storage_key
        try:
            run = subprocess.run(
                [sys.executable, "-I", str(worker), source.source_type, str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90, check=True,
            )
            source.raw_text = run.stdout.decode("utf-8")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            source.raw_text = None

    source.status = "text_extracted" if source.raw_text else "failed_extraction"
    record_audit_event(db, action=f"order_source.{source.status}", entity_type="order_source",
                       entity_id=source.id, actor_user_id=actor_id)
    db.commit()
    return source
