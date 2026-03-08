cat > app/collectors/validator_status_collector.py <<'PY'
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import (
    CollectorRun,
    Network,
    NetworkEndpoint,
    Validator,
    ValidatorStatusCurrent,
    ValidatorStatusHistory,
)

TARGET_MONIKER_RE = re.compile(os.getenv("TARGET_MONIKER_RE", "posthuman"), re.IGNORECASE)
TIMEOUT = float(os.getenv("VALIDATOR_COLLECTOR_TIMEOUT", "12"))
CONCURRENCY = int(os.getenv("VALIDATOR_COLLECTOR_CONCURRENCY", "20"))

REST_VALIDATORS_PATH = "/cosmos/staking/v1beta1/validators"
RPC_STATUS_PATH = "/status"


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def parse_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def validator_status_normalized(status: str | None) -> str | None:
    if not status:
        return None
    s = status.upper()
    if s in ("BOND_STATUS_BONDED", "BONDED", "ACTIVE"):
        return "bonded"
    if s in ("BOND_STATUS_UNBONDING", "UNBONDING"):
        return "unbonding"
    if s in ("BOND_STATUS_UNBONDED", "UNBONDED", "INACTIVE"):
        return "unbonded"
    return status.lower()


async def fetch_rpc_height(client: httpx.AsyncClient, rpc_url: str) -> int | None:
    try:
        r = await client.get(normalize_base_url(rpc_url) + RPC_STATUS_PATH)
        if r.status_code != 200:
            return None
        js = r.json()
        result = js.get("result") or {}
        sync_info = result.get("sync_info") or {}
        return parse_int(sync_info.get("latest_block_height"))
    except Exception:
        return None


async def fetch_all_validators_from_rest(client: httpx.AsyncClient, rest_url: str) -> list[dict[str, Any]]:
    validators: list[dict[str, Any]] = []
    next_key: str | None = None

    while True:
        params: dict[str, Any] = {
            "pagination.limit": 1000,
        }
        if next_key:
            params["pagination.key"] = next_key

        r = await client.get(
            normalize_base_url(rest_url) + REST_VALIDATORS_PATH,
            params=params,
        )
        r.raise_for_status()
        js = r.json()

        batch = js.get("validators") or []
        validators.extend(batch)

        pagination = js.get("pagination") or {}
        next_key = pagination.get("next_key")
        if not next_key:
            break

    return validators


def choose_target_validator(validators: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    for v in validators:
        desc = v.get("description") or {}
        moniker = desc.get("moniker") or ""
        operator_address = v.get("operator_address") or ""

        if TARGET_MONIKER_RE.search(moniker) or TARGET_MONIKER_RE.search(operator_address):
            candidates.append(v)

    if not candidates:
        return None

    candidates.sort(key=lambda x: parse_int(x.get("tokens")) or 0, reverse=True)
    return candidates[0]


def compute_rank(validators: list[dict[str, Any]], operator_address: str | None) -> int | None:
    if not operator_address:
        return None

    sorted_validators = sorted(
        validators,
        key=lambda x: parse_int(x.get("tokens")) or 0,
        reverse=True,
    )

    for idx, v in enumerate(sorted_validators, start=1):
        if (v.get("operator_address") or "") == operator_address:
            return idx
    return None


def get_active_set_flag(status_value: str | None) -> int:
    normalized = validator_status_normalized(status_value)
    return 1 if normalized == "bonded" else 0


async def collect_one_network(
    network_id: int,
    network_name: str,
    rest_urls: list[str],
    rpc_urls: list[str],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            reference_height = None
            for rpc_url in rpc_urls:
                h = await fetch_rpc_height(client, rpc_url)
                if h is not None:
                    reference_height = max(reference_height or 0, h)

            last_error: str | None = None
            validators: list[dict[str, Any]] = []

            for rest_url in rest_urls:
                try:
                    validators = await fetch_all_validators_from_rest(client, rest_url)
                    if validators:
                        break
                except Exception as e:
                    last_error = str(e)

            if not validators:
                return {
                    "network_id": network_id,
                    "network_name": network_name,
                    "ok": False,
                    "error": last_error or "No validators fetched from REST",
                    "reference_height": reference_height,
                }

            target = choose_target_validator(validators)
            if target is None:
                return {
                    "network_id": network_id,
                    "network_name": network_name,
                    "ok": False,
                    "error": "Target validator not found by moniker regex",
                    "reference_height": reference_height,
                }

            operator_address = target.get("operator_address")
            desc = target.get("description") or {}
            commission = ((target.get("commission") or {}).get("commission_rates") or {})

            result = {
                "network_id": network_id,
                "network_name": network_name,
                "ok": True,
                "reference_height": reference_height,
                "validator": {
                    "moniker": desc.get("moniker"),
                    "operator_address": operator_address,
                    "consensus_address": target.get("consensus_pubkey", {}).get("key"),
                    "status": validator_status_normalized(target.get("status")),
                    "raw_status": target.get("status"),
                    "in_active_set": get_active_set_flag(target.get("status")),
                    "jailed": 1 if bool(target.get("jailed")) else 0,
                    "tombstoned": 0,
                    "tokens": target.get("tokens"),
                    "delegator_shares": target.get("delegator_shares"),
                    "commission_rate": commission.get("rate"),
                    "commission_max_rate": commission.get("max_rate"),
                    "commission_max_change_rate": commission.get("max_change_rate"),
                    "min_self_delegation": target.get("min_self_delegation"),
                    "self_delegation_amount": None,
                    "rank": compute_rank(validators, operator_address),
                    "voting_power": target.get("tokens"),
                    "last_seen_height": reference_height,
                    "raw_json": target,
                },
            }
            return result


async def run_async(network_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        collect_one_network(
            network_id=item["network_id"],
            network_name=item["network_name"],
            rest_urls=item["rest_urls"],
            rpc_urls=item["rpc_urls"],
            semaphore=semaphore,
        )
        for item in network_inputs
    ]
    return await asyncio.gather(*tasks)


def main() -> None:
    db = SessionLocal()
    started = datetime.now(timezone.utc)

    run = CollectorRun(
        collector_name="validator_status_collector",
        status="running",
        started_at=started,
        items_processed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        networks = db.execute(
            select(Network)
            .where(Network.is_enabled == 1)
            .order_by(Network.name.asc())
        ).scalars().all()

        network_inputs: list[dict[str, Any]] = []

        for network in networks:
            endpoints = db.execute(
                select(NetworkEndpoint)
                .where(NetworkEndpoint.network_id == network.id)
                .where(NetworkEndpoint.is_enabled == 1)
            ).scalars().all()

            rest_urls = [e.url for e in endpoints if e.endpoint_type == "rest"]
            rpc_urls = [e.url for e in endpoints if e.endpoint_type == "rpc"]

            if not rest_urls:
                continue

            network_inputs.append(
                {
                    "network_id": network.id,
                    "network_name": network.name,
                    "rest_urls": rest_urls,
                    "rpc_urls": rpc_urls,
                }
            )

        results = asyncio.run(run_async(network_inputs))
        now = datetime.now(timezone.utc)

        processed = 0

        for item in results:
            network_id = item["network_id"]

            if not item["ok"]:
                processed += 1
                continue

            v = item["validator"]

            validator = db.execute(
                select(Validator)
                .where(Validator.network_id == network_id)
                .where(Validator.operator_address == v["operator_address"])
            ).scalar_one_or_none()

            if validator is None:
                validator = Validator(
                    network_id=network_id,
                    moniker=v["moniker"],
                    operator_address=v["operator_address"],
                    consensus_address=v["consensus_address"],
                    is_main=1,
                    is_enabled=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(validator)
                db.flush()
            else:
                validator.moniker = v["moniker"]
                validator.consensus_address = v["consensus_address"]
                validator.updated_at = now
                db.flush()

            current = db.execute(
                select(ValidatorStatusCurrent)
                .where(ValidatorStatusCurrent.validator_id == validator.id)
            ).scalar_one_or_none()

            raw_json_str = json.dumps(v["raw_json"], ensure_ascii=False)

            if current is None:
                current = ValidatorStatusCurrent(
                    validator_id=validator.id,
                    status=v["status"],
                    in_active_set=v["in_active_set"],
                    jailed=v["jailed"],
                    tombstoned=v["tombstoned"],
                    tokens=v["tokens"],
                    delegator_shares=v["delegator_shares"],
                    commission_rate=v["commission_rate"],
                    commission_max_rate=v["commission_max_rate"],
                    commission_max_change_rate=v["commission_max_change_rate"],
                    min_self_delegation=v["min_self_delegation"],
                    self_delegation_amount=v["self_delegation_amount"],
                    rank=v["rank"],
                    voting_power=v["voting_power"],
                    last_seen_height=v["last_seen_height"],
                    last_checked_at=now,
                    raw_json=raw_json_str,
                )
                db.add(current)
            else:
                current.status = v["status"]
                current.in_active_set = v["in_active_set"]
                current.jailed = v["jailed"]
                current.tombstoned = v["tombstoned"]
                current.tokens = v["tokens"]
                current.delegator_shares = v["delegator_shares"]
                current.commission_rate = v["commission_rate"]
                current.commission_max_rate = v["commission_max_rate"]
                current.commission_max_change_rate = v["commission_max_change_rate"]
                current.min_self_delegation = v["min_self_delegation"]
                current.self_delegation_amount = v["self_delegation_amount"]
                current.rank = v["rank"]
                current.voting_power = v["voting_power"]
                current.last_seen_height = v["last_seen_height"]
                current.last_checked_at = now
                current.raw_json = raw_json_str

            history = ValidatorStatusHistory(
                validator_id=validator.id,
                status=v["status"],
                in_active_set=v["in_active_set"],
                jailed=v["jailed"],
                tombstoned=v["tombstoned"],
                tokens=v["tokens"],
                commission_rate=v["commission_rate"],
                rank=v["rank"],
                voting_power=v["voting_power"],
                last_seen_height=v["last_seen_height"],
                collected_at=now,
                raw_json=raw_json_str,
            )
            db.add(history)

            processed += 1

        finished = datetime.now(timezone.utc)
        run.status = "success"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.items_processed = processed

        db.commit()
        print(f"validator_status_collector complete: {processed} networks updated")
    except Exception as e:
        db.rollback()
        finished = datetime.now(timezone.utc)
        run.status = "failed"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.error_message = str(e)
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
PY
