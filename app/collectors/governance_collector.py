from __future__ import annotations

import json
from datetime import datetime, timezone

import requests
from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models import Network, TrackedNetwork, Validator

TIMEOUT = 12

# Cosmos SDK gov v1
GOV_PATH = "/cosmos/gov/v1/proposals"
ACTIVE_STATUS = "PROPOSAL_STATUS_VOTING_PERIOD"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/")


def fetch_active_proposals(rest_url: str) -> list[dict]:
    """
    Возвращает только proposals в voting period.
    """
    try:
        r = requests.get(
            normalize_url(rest_url) + GOV_PATH,
            params={"proposal_status": ACTIVE_STATUS},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("proposals") or []
    except Exception:
        return []


def build_network_list(db):
    """
    Берем только сети, где:
    - tracked network enabled
    - use_for_validator_search enabled
    - network enabled
    - есть основной активный validator
    """
    rows = db.execute(
        select(Network)
        .join(TrackedNetwork, TrackedNetwork.network_id == Network.id)
        .join(
            Validator,
            (Validator.network_id == Network.id)
            & (Validator.is_main == 1)
            & (Validator.is_enabled == 1),
        )
        .where(TrackedNetwork.is_enabled == 1)
        .where(TrackedNetwork.use_for_validator_search == 1)
        .where(Network.is_enabled == 1)
        .order_by(Network.name.asc())
    ).scalars().all()

    result = []
    seen = set()

    for network in rows:
        if network.id in seen:
            continue
        seen.add(network.id)

        if not getattr(network, "rest", None):
            continue

        result.append(network)

    return result


def extract_title(proposal: dict) -> str:
    """
    В gov v1 title может лежать в metadata или messages.
    Пытаемся вытащить максимально читабельно.
    """
    metadata = proposal.get("metadata")
    if metadata:
        try:
            md = json.loads(metadata)
            if isinstance(md, dict):
                title = md.get("title")
                if title:
                    return str(title)[:300]
        except Exception:
            pass

        if isinstance(metadata, str) and metadata.strip():
            # если metadata не JSON, но строка полезная
            return metadata.strip()[:300]

    title = proposal.get("title")
    if title:
        return str(title)[:300]

    messages = proposal.get("messages") or []
    if messages:
        msg0 = messages[0]
        msg_type = msg0.get("@type") or "proposal"
        return msg_type.split(".")[-1][:300]

    return f"Proposal #{proposal.get('id', '?')}"


def extract_description(proposal: dict) -> str | None:
    metadata = proposal.get("metadata")
    if metadata:
        try:
            md = json.loads(metadata)
            if isinstance(md, dict):
                desc = md.get("summary") or md.get("description")
                if desc:
                    return str(desc)[:4000]
        except Exception:
            pass

        if isinstance(metadata, str) and metadata.strip():
            return metadata.strip()[:4000]

    summary = proposal.get("summary")
    if summary:
        return str(summary)[:4000]

    return None


def extract_final_tally(proposal: dict) -> dict:
    tally = proposal.get("final_tally_result") or {}
    return {
        "yes_votes": tally.get("yes_count"),
        "no_votes": tally.get("no_count"),
        "abstain_votes": tally.get("abstain_count"),
        "no_with_veto_votes": tally.get("no_with_veto_count"),
    }


def main() -> None:
    db = SessionLocal()

    try:
        networks = build_network_list(db)
        print(f"Networks to check: {len(networks)}")

        total_inserted = 0

        for network in networks:
            proposals = fetch_active_proposals(network.rest)

            # Удаляем старые активные proposals этой сети и вставляем текущий снимок заново
            db.execute(
                text(
                    """
                    DELETE FROM governance_proposals
                    WHERE network_id = :network_id
                    """
                ),
                {"network_id": network.id},
            )

            inserted_for_network = 0

            for proposal in proposals:
                proposal_id_raw = proposal.get("id")
                if proposal_id_raw is None:
                    continue

                try:
                    proposal_id = int(proposal_id_raw)
                except Exception:
                    continue

                title = extract_title(proposal)
                description = extract_description(proposal)
                status = proposal.get("status")
                voting_start_time = proposal.get("voting_start_time")
                voting_end_time = proposal.get("voting_end_time")
                tally = extract_final_tally(proposal)
                now = utc_now_iso()

                db.execute(
                    text(
                        """
                        INSERT INTO governance_proposals (
                            network_id,
                            proposal_id,
                            title,
                            description,
                            status,
                            voting_start_time,
                            voting_end_time,
                            yes_votes,
                            no_votes,
                            abstain_votes,
                            no_with_veto_votes,
                            last_updated_at
                        )
                        VALUES (
                            :network_id,
                            :proposal_id,
                            :title,
                            :description,
                            :status,
                            :voting_start_time,
                            :voting_end_time,
                            :yes_votes,
                            :no_votes,
                            :abstain_votes,
                            :no_with_veto_votes,
                            :last_updated_at
                        )
                        """
                    ),
                    {
                        "network_id": network.id,
                        "proposal_id": proposal_id,
                        "title": title,
                        "description": description,
                        "status": status,
                        "voting_start_time": voting_start_time,
                        "voting_end_time": voting_end_time,
                        "yes_votes": tally["yes_votes"],
                        "no_votes": tally["no_votes"],
                        "abstain_votes": tally["abstain_votes"],
                        "no_with_veto_votes": tally["no_with_veto_votes"],
                        "last_updated_at": now,
                    },
                )

                inserted_for_network += 1
                total_inserted += 1

            print(
                f"[OK] {network.name}: "
                f"active_proposals={inserted_for_network}"
            )

        db.commit()
        print(f"Governance collector complete: inserted={total_inserted}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
