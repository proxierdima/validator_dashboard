from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TrackedNetwork(Base):
    __tablename__ = "tracked_networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True, unique=True)
    custom_name: Mapped[str | None] = mapped_column(String(150))
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)
    use_for_validator_search: Mapped[int] = mapped_column(Integer, default=1)
    use_for_validator_rpc_checks: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    network = relationship("Network")
