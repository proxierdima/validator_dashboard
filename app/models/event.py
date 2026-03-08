from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id"), index=True)
    validator_id: Mapped[int | None] = mapped_column(ForeignKey("validators.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(300))
    message: Mapped[str | None] = mapped_column(String(2000))
    event_key: Mapped[str | None] = mapped_column(String(300), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    first_seen_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    resolved_at: Mapped[DateTime | None] = mapped_column(DateTime)
    metadata_json: Mapped[str | None] = mapped_column(String)

    network = relationship("Network")
    validator = relationship("Validator")
