from sqlalchemy import BigInteger, ForeignKey, Identity, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, TimestampMixin


class AIExtraction(TimestampMixin, Base):
    __tablename__ = "ai_extractions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_source_id: Mapped[int] = mapped_column(ForeignKey("order_sources.id"), unique=True)
    model_name: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(80))
    raw_output: Mapped[list[str]] = mapped_column(JSON)
    validated_output: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    schema_valid: Mapped[bool]
    status: Mapped[str] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(String(300))
    line_matches: Mapped[list[dict] | None] = mapped_column(JSON(none_as_null=True))
    confirmed_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), unique=True)
