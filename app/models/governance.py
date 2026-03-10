from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class GovernanceProposal(Base):
    __tablename__ = "governance_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    network_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("networks.id"),
        nullable=False,
        index=True,
    )
    proposal_id: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str | None] = mapped_column(String(64), nullable=True)

    voting_start_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    voting_end_time: Mapped[str | None] = mapped_column(String(64), nullable=True)

    yes_votes: Mapped[str | None] = mapped_column(String(128), nullable=True)
    no_votes: Mapped[str | None] = mapped_column(String(128), nullable=True)
    abstain_votes: Mapped[str | None] = mapped_column(String(128), nullable=True)
    no_with_veto_votes: Mapped[str | None] = mapped_column(String(128), nullable=True)

    last_updated_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    network = relationship("Network")

    __table_args__ = (
        UniqueConstraint("network_id", "proposal_id", name="uq_governance_network_proposal"),
        Index("ix_governance_status", "status"),
        Index("ix_governance_end", "voting_end_time"),
    )
