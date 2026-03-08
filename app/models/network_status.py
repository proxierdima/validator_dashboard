from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class NetworkStatusCurrent(Base):
    __tablename__ = "network_status_current"

    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), primary_key=True)
    validator_status: Mapped[str | None] = mapped_column(String(20), index=True)
    endpoint_status: Mapped[str | None] = mapped_column(String(20), index=True)
    sync_status: Mapped[str | None] = mapped_column(String(20), index=True)
    snapshot_status: Mapped[str | None] = mapped_column(String(20), index=True)
    governance_status: Mapped[str | None] = mapped_column(String(20), index=True)
    reward_status: Mapped[str | None] = mapped_column(String(20), index=True)
    overall_status: Mapped[str | None] = mapped_column(String(20), index=True)
    local_height: Mapped[int | None] = mapped_column(Integer)
    reference_height: Mapped[int | None] = mapped_column(Integer)
    sync_diff: Mapped[int | None] = mapped_column(Integer)
    active_alerts_count: Mapped[int] = mapped_column(Integer, default=0)
    last_updated_at: Mapped[DateTime | None] = mapped_column(DateTime, index=True)

    network = relationship("Network")
