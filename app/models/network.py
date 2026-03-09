from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    directory: Mapped[str | None] = mapped_column(String(120), index=True)
    chain_id: Mapped[str | None] = mapped_column(String(120), index=True)
    chain_type: Mapped[str | None] = mapped_column(String(50), default="cosmos")
    base_denom: Mapped[str | None] = mapped_column(String(50))
    display_denom: Mapped[str | None] = mapped_column(String(50))
    exponent: Mapped[int | None] = mapped_column(Integer)
    coingecko_id: Mapped[str | None] = mapped_column(String(120))
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)
    rpc: Mapped[str | None] = mapped_column(String, nullable=True)
    rest: Mapped[str | None] = mapped_column(String, nullable=True)
    grpc: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    endpoints = relationship(
        "NetworkEndpoint", back_populates="network", cascade="all, delete-orphan"
    )
    validators = relationship(
        "Validator", back_populates="network", cascade="all, delete-orphan"
    )
    snapshot_targets = relationship(
        "SnapshotTarget", back_populates="network", cascade="all, delete-orphan"
    )
