from __future__ import annotations

import json
from datetime import datetime, timezone

import requests
from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models import Network, TrackedNetwork, Validator

TIMEOUT = 12

# Cosmos SDK gov v1
GOV_PROPOSALS_PATH = "/cosmos/gov/v1/proposals"
GOV_TALLY_PATH = "/cosmos/gov/v1/proposals/{proposal_id}/tally"
GOV_VOTE_PATH = "/cosmos/gov/v1/proposals/{proposal_id}/votes/{voter}"

ACTIVE_STATUS = "PROPOSAL_STATUS_VOTING_PERIOD"


def now_fmt() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M %Y-%m-%d")


def format_dt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%H:%M %Y-%m-%d")
    except Exception:
        return value


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
            normalize_url(rest_url) + GOV_PROPOSALS_PATH,
            params={"proposal_status": ACTIVE_STATUS},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("proposals") or []
    except Exception:
        return []


def fetch_proposal_tally(rest_url: str, proposal_id: int) -> dict:
    """
    Берем текущее tally по proposal.
    """
    try:
        r = requests.get(
            normalize_url(rest_url) + GOV_TALLY_PATH.format(proposal_id=proposal_id),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        tally = data.get("tally") or data.get("final_tally_result") or {}
        return {
            "yes_votes": tally.get("yes_count"),
            "no_votes": tally.get("no_count"),
            "abstain_votes": tally.get("abstain_count"),
            "no_with_veto_votes": tally.get("no_with_veto_count"),
        }
    except Exception:
        return {
            "yes_votes": None,
            "no_votes": None,
            "abstain_votes": None,
            "no_with_veto_votes": None,
        }


def normalize_vote_option(option: str | None) -> str | None:
    if not option:
        return None
    option = str(option).strip()
    prefix = "VOTE_OPTION_"
    if option.startswith(prefix):
        option = option[len(prefix):]
    return option


def fetch_validator_vote(
    rest_url: str,
    proposal_id: int,
    voter_address: str | None,
) -> tuple[int, str | None]:
    """
    Возвращает:
      validator_voted: 0/1
      validator_vote_option: YES / NO / ABSTAIN / NO_WITH_VETO
                             или weighted: YES:0.700000,NO:0.300000

    Для gov/votes/{voter} нужно использовать account/delegator address.
    """
    if not voter_address:
        return 0, None

    try:
        r = requests.get(
            normalize_url(rest_url)
            + GOV_VOTE_PATH.format(proposal_id=proposal_id, voter=voter_address),
            timeout=TIMEOUT,
        )

        if r.status_code == 404:
            return 0, None

        r.raise_for_status()
        data = r.json()
        vote = data.get("vote") or {}

        options = vote.get("options") or []
        if options:
            parts = []
            for item in options:
                opt = normalize_vote_option(item.get("option"))
                weight = item.get("weight")
                if opt and weight:
                    parts.append(f"{opt}:{weight}")
                elif opt:
                    parts.append(opt)
            if parts:
                return 1, ",".join(parts)

        single_option = normalize_vote_option(vote.get("option"))
        if single_option:
            return 1, single_option

        return 1, "UNKNOWN"

    except Exception:
        return 0, None


def build_network_list(db):
    """
    Берем только сети, где:
    - tracked network enabled
    - use_for_validator_search enabled
    - network enabled
    - есть основной активный validator

    Для проверки голоса используем:
    1) delegator_address
    2) fallback -> operator_address
    """
    rows = db.execute(
        select(Network, Validator)
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
    ).all()

    result = []
    seen = set()

    for network, validator in rows:
        if network.id in seen:
            continue
        seen.add(network.id)

        if not getattr(network, "rest", None):
            continue

        voter_address = (
            getattr(validator, "delegator_address", None)
            or getattr(validator, "operator_address", None)
        )

        result.append(
            {
                "network": network,
                "validator": validator,
                "voter_address": voter_address,
            }
        )

    return result


def extract_title(proposal: dict) -> str:
    """
    В gov v1 title может лежать в metadata или messages.
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


def extract_tally_from_proposal(proposal: dict) -> dict:
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
        items = build_network_list(db)
        print(f"Networks to check: {len(items)}")

        total_inserted = 0

        for item in items:
            network = item["network"]
            validator = item["validator"]
            voter_address = item["voter_address"]

            proposals = fetch_active_proposals(network.rest)

            # Историю НЕ удаляем.
            # Просто снимаем флаг latest у старых снимков этой сети.
            db.execute(
                text(
                    """
                    UPDATE governance_proposals
                    SET is_latest = 0
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

                voting_start_time = format_dt(proposal.get("voting_start_time"))
                voting_end_time = format_dt(proposal.get("voting_end_time"))

                tally = fetch_proposal_tally(network.rest, proposal_id)
                if not any(tally.values()):
                    tally = extract_tally_from_proposal(proposal)

                validator_voted, validator_vote_option = fetch_validator_vote(
                    network.rest,
                    proposal_id,
                    voter_address,
                )

                snapshot_at = now_fmt()

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
                            validator_voter_address,
                            validator_voted,
                            validator_vote_option,
                            snapshot_at,
                            last_updated_at,
                            is_latest
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
                            :validator_voter_address,
                            :validator_voted,
                            :validator_vote_option,
                            :snapshot_at,
                            :last_updated_at,
                            1
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
                        "validator_voter_address": voter_address,
                        "validator_voted": validator_voted,
                        "validator_vote_option": validator_vote_option,
                        "snapshot_at": snapshot_at,
                        "last_updated_at": snapshot_at,
                    },
                )

                inserted_for_network += 1
                total_inserted += 1

            print(
                f"[OK] {network.name}: "
                f"active_proposals={inserted_for_network} "
                f"| voter={voter_address or 'None'} "
                f"| validator={getattr(validator, 'operator_address', None)}"
            )

        db.commit()
        print(f"Governance collector complete: inserted={total_inserted}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
