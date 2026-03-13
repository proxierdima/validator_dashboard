from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SnapshotTarget(Base):
    __tablename__ = "snapshot_targets"
    __table_args__ = (
        UniqueConstraint(
            "network_id",
            "snapshot_path",
            "filename_pattern",
            name="uq_snapshot_target_network_path_pattern",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True)
    snapshot_path: Mapped[str] = mapped_column(String(500))
    filename_pattern: Mapped[str | None] = mapped_column(String(200), default="data_latest%")
    compression_type: Mapped[str | None] = mapped_column(String(50))
    min_expected_size_bytes: Mapped[int | None] = mapped_column(Integer)
    max_age_hours: Mapped[int] = mapped_column(Integer, default=24)
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    network = relationship("Network", back_populates="snapshot_targets")
    checks = relationship(
        "SnapshotCheck", back_populates="snapshot_target", cascade="all, delete-orphan"
    )


class SnapshotCheck(Base):
    __tablename__ = "snapshot_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_target_id: Mapped[int] = mapped_column(
        ForeignKey("snapshot_targets.id"), index=True
    )
    file_name: Mapped[str | None] = mapped_column(String(300))
    file_path: Mapped[str | None] = mapped_column(String(700))
    file_exists: Mapped[int | None] = mapped_column(Integer)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    file_mtime: Mapped[DateTime | None] = mapped_column(DateTime)
    age_seconds: Mapped[int | None] = mapped_column(Integer)
    size_delta_bytes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), index=True)
    error_message: Mapped[str | None] = mapped_column(String(1000))
    checked_at: Mapped[DateTime] = mapped_column(DateTime, index=True)

    snapshot_target = relationship("SnapshotTarget", back_populates="checks")
