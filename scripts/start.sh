mkdir -p app/models

cat > app/models/base.py <<'PY'
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
PY

cat > app/models/network.py <<'PY'
from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    chain_id: Mapped[str | None] = mapped_column(String(120), index=True)
    chain_type: Mapped[str | None] = mapped_column(String(50), default="cosmos")
    base_denom: Mapped[str | None] = mapped_column(String(50))
    display_denom: Mapped[str | None] = mapped_column(String(50))
    exponent: Mapped[int | None] = mapped_column(Integer)
    coingecko_id: Mapped[str | None] = mapped_column(String(120))
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)

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
PY

cat > app/models/endpoint.py <<'PY'
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class NetworkEndpoint(Base):
    __tablename__ = "network_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True)
    endpoint_type: Mapped[str] = mapped_column(String(20))   # rpc, rest, grpc
    label: Mapped[str | None] = mapped_column(String(20))    # rpc1, rpc2, rest1
    url: Mapped[str] = mapped_column(String(500))
    priority: Mapped[int] = mapped_column(Integer, default=1)
    is_public: Mapped[int] = mapped_column(Integer, default=1)
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[DateTime | None] = mapped_column(DateTime)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime)

    network = relationship("Network", back_populates="endpoints")
    checks = relationship(
        "EndpointCheck", back_populates="endpoint", cascade="all, delete-orphan"
    )


class EndpointCheck(Base):
    __tablename__ = "endpoint_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("network_endpoints.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)   # ok, warning, critical
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    remote_height: Mapped[int | None] = mapped_column(Integer)
    chain_id_reported: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    checked_at: Mapped[DateTime] = mapped_column(DateTime, index=True)

    endpoint = relationship("NetworkEndpoint", back_populates="checks")
PY

cat > app/models/validator.py <<'PY'
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Validator(Base):
    __tablename__ = "validators"
    __table_args__ = (
        UniqueConstraint("network_id", "operator_address", name="uq_validator_network_operator"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True)
    moniker: Mapped[str | None] = mapped_column(String(200))
    operator_address: Mapped[str] = mapped_column(String(200), index=True)
    delegator_address: Mapped[str | None] = mapped_column(String(200))
    consensus_address: Mapped[str | None] = mapped_column(String(200))
    is_main: Mapped[int] = mapped_column(Integer, default=1)
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[DateTime | None] = mapped_column(DateTime)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime)

    network = relationship("Network", back_populates="validators")
    current_status = relationship(
        "ValidatorStatusCurrent",
        back_populates="validator",
        uselist=False,
        cascade="all, delete-orphan",
    )
    history = relationship(
        "ValidatorStatusHistory",
        back_populates="validator",
        cascade="all, delete-orphan",
    )


class ValidatorStatusCurrent(Base):
    __tablename__ = "validator_status_current"

    validator_id: Mapped[int] = mapped_column(ForeignKey("validators.id"), primary_key=True)
    status: Mapped[str | None] = mapped_column(String(50), index=True)
    in_active_set: Mapped[int | None] = mapped_column(Integer)
    jailed: Mapped[int | None] = mapped_column(Integer)
    tombstoned: Mapped[int | None] = mapped_column(Integer)
    tokens: Mapped[str | None] = mapped_column(String(100))
    delegator_shares: Mapped[str | None] = mapped_column(String(100))
    commission_rate: Mapped[str | None] = mapped_column(String(50))
    commission_max_rate: Mapped[str | None] = mapped_column(String(50))
    commission_max_change_rate: Mapped[str | None] = mapped_column(String(50))
    min_self_delegation: Mapped[str | None] = mapped_column(String(100))
    self_delegation_amount: Mapped[str | None] = mapped_column(String(100))
    rank: Mapped[int | None] = mapped_column(Integer)
    voting_power: Mapped[str | None] = mapped_column(String(100))
    last_seen_height: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[DateTime | None] = mapped_column(DateTime, index=True)
    raw_json: Mapped[str | None] = mapped_column(String)

    validator = relationship("Validator", back_populates="current_status")


class ValidatorStatusHistory(Base):
    __tablename__ = "validator_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    validator_id: Mapped[int] = mapped_column(ForeignKey("validators.id"), index=True)
    status: Mapped[str | None] = mapped_column(String(50))
    in_active_set: Mapped[int | None] = mapped_column(Integer)
    jailed: Mapped[int | None] = mapped_column(Integer)
    tombstoned: Mapped[int | None] = mapped_column(Integer)
    tokens: Mapped[str | None] = mapped_column(String(100))
    commission_rate: Mapped[str | None] = mapped_column(String(50))
    rank: Mapped[int | None] = mapped_column(Integer)
    voting_power: Mapped[str | None] = mapped_column(String(100))
    last_seen_height: Mapped[int | None] = mapped_column(Integer)
    collected_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    raw_json: Mapped[str | None] = mapped_column(String)

    validator = relationship("Validator", back_populates="history")
PY

cat > app/models/snapshot.py <<'PY'
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SnapshotTarget(Base):
    __tablename__ = "snapshot_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True)
    snapshot_path: Mapped[str] = mapped_column(String(500))
    filename_pattern: Mapped[str | None] = mapped_column(String(200), default="data_latest%")
    compression_type: Mapped[str | None] = mapped_column(String(50))
    min_expected_size_bytes: Mapped[int | None] = mapped_column(Integer)
    max_age_hours: Mapped[int] = mapped_column(Integer, default=24)
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[DateTime | None] = mapped_column(DateTime)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime)

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
PY

cat > app/models/event.py <<'PY'
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
PY

cat > app/models/collector_run.py <<'PY'
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CollectorRun(Base):
    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    collector_name: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime, index=True)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(2000))
PY

cat > app/models/network_status.py <<'PY'
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
PY

cat > app/models/__init__.py <<'PY'
from app.models.base import Base
from app.models.network import Network
from app.models.endpoint import NetworkEndpoint, EndpointCheck
from app.models.validator import Validator, ValidatorStatusCurrent, ValidatorStatusHistory
from app.models.snapshot import SnapshotTarget, SnapshotCheck
from app.models.event import Event
from app.models.collector_run import CollectorRun
from app.models.network_status import NetworkStatusCurrent

__all__ = [
    "Base",
    "Network",
    "NetworkEndpoint",
    "EndpointCheck",
    "Validator",
    "ValidatorStatusCurrent",
    "ValidatorStatusHistory",
    "SnapshotTarget",
    "SnapshotCheck",
    "Event",
    "CollectorRun",
    "NetworkStatusCurrent",
]
PY
