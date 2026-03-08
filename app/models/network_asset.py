from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class NetworkAsset(Base):
    __tablename__ = "network_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True)

    base_denom: Mapped[str] = mapped_column(String(200), index=True)
    display_denom: Mapped[str | None] = mapped_column(String(100))
    exponent: Mapped[int] = mapped_column(Integer, default=0)
    symbol: Mapped[str | None] = mapped_column(String(50))
    coingecko_id: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[DateTime | None] = mapped_column(DateTime)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime)

    network = relationship("Network")
