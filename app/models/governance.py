from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
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

    proposal_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str | None] = mapped_column(String(64), nullable=True)

    voting_start_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    voting_end_time: Mapped[str | None] = mapped_column(String(32), nullable=True)

    yes_votes: Mapped[str | None] = mapped_column(String(128), nullable=True)
    no_votes: Mapped[str | None] = mapped_column(String(128), nullable=True)
    abstain_votes: Mapped[str | None] = mapped_column(String(128), nullable=True)
    no_with_veto_votes: Mapped[str | None] = mapped_column(String(128), nullable=True)

    validator_voter_address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    validator_voted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validator_vote_option: Mapped[str | None] = mapped_column(String(128), nullable=True)

    snapshot_at: Mapped[str] = mapped_column(String(32), nullable=False)
    last_updated_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_latest: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    network = relationship("Network")

    __table_args__ = (
        Index("ix_governance_status", "status"),
        Index("ix_governance_end", "voting_end_time"),
        Index("ix_governance_snapshot_at", "snapshot_at"),
        Index("ix_governance_is_latest", "is_latest"),
        Index(
            "ix_governance_network_proposal_latest",
            "network_id",
            "proposal_id",
            "is_latest",
        ),
    )
