from __future__ import annotations

import asyncio
import json
import os
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

TIMEOUT = float(os.getenv("VALIDATOR_COLLECTOR_TIMEOUT", "12"))
CONCURRENCY = int(os.getenv("VALIDATOR_COLLECTOR_CONCURRENCY", "20"))

REST_VALIDATOR_PATH = "/cosmos/staking/v1beta1/validators/{validator_addr}"
RPC_STATUS_PATH = "/status"


def normalize_base_url(url: str) -> str:
    return (url or "").rstrip("/")


def parse_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def validator_status_normalized(status: str | None) -> str | None:
    if not status:
        return None
    s = str(status).upper()
    if s in ("BOND_STATUS_BONDED", "BONDED", "ACTIVE"):
        return "bonded"
    if s in ("BOND_STATUS_UNBONDING", "UNBONDING"):
        return "unbonding"
    if s in ("BOND_STATUS_UNBONDED", "UNBONDED", "INACTIVE"):
        return "unbonded"
    return str(status).lower()


def get_active_set_flag(status_value: str | None) -> int:
    normalized = validator_status_normalized(status_value)
    return 1 if normalized == "bonded" else 0


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for v in values:
        vv = (v or "").strip()
        if not vv or vv in seen:
            continue
        seen.add(vv)
        result.append(vv)
    return result


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


async def fetch_validator_from_rest(
    client: httpx.AsyncClient,
    rest_url: str,
    operator_address: str,
) -> dict[str, Any] | None:
    try:
        path = REST_VALIDATOR_PATH.format(validator_addr=operator_address)
        r = await client.get(normalize_base_url(rest_url) + path)
        if r.status_code != 200:
            return None

        js = r.json()
        validator = js.get("validator")
        if validator:
            return validator
        return None
    except Exception:
        return None


async def collect_one_validator(
    validator_id: int,
    network_id: int,
    network_name: str,
    operator_address: str,
    rest_urls: list[str],
    rpc_urls: list[str],
    grpc_urls: list[str],
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
            validator_data: dict[str, Any] | None = None
            used_rest_url: str | None = None

            for rest_url in rest_urls:
                try:
                    validator_data = await fetch_validator_from_rest(
                        client=client,
                        rest_url=rest_url,
                        operator_address=operator_address,
                    )
                    if validator_data:
                        used_rest_url = rest_url
                        break
                except Exception as e:
                    last_error = str(e)

            if not validator_data:
                return {
                    "validator_id": validator_id,
                    "network_id": network_id,
                    "network_name": network_name,
                    "operator_address": operator_address,
                    "ok": False,
                    "error": last_error or "Validator not fetched from public REST",
                    "reference_height": reference_height,
                    "used_rest_url": used_rest_url,
                    "used_rpc_urls": rpc_urls,
                    "used_grpc_urls": grpc_urls,
                }

            desc = validator_data.get("description") or {}
            commission = ((validator_data.get("commission") or {}).get("commission_rates") or {})

            consensus_pubkey = validator_data.get("consensus_pubkey") or {}
            consensus_address = (
                validator_data.get("consensus_address")
                or consensus_pubkey.get("address")
                or consensus_pubkey.get("key")
            )

            return {
                "validator_id": validator_id,
                "network_id": network_id,
                "network_name": network_name,
                "operator_address": operator_address,
                "ok": True,
                "reference_height": reference_height,
                "used_rest_url": used_rest_url,
                "used_rpc_urls": rpc_urls,
                "used_grpc_urls": grpc_urls,
                "validator": {
                    "moniker": desc.get("moniker"),
                    "operator_address": validator_data.get("operator_address") or operator_address,
                    "consensus_address": consensus_address,
                    "status": validator_status_normalized(validator_data.get("status")),
                    "in_active_set": get_active_set_flag(validator_data.get("status")),
                    "jailed": 1 if bool(validator_data.get("jailed")) else 0,
                    "tombstoned": 0,
                    "tokens": validator_data.get("tokens"),
                    "delegator_shares": validator_data.get("delegator_shares"),
                    "commission_rate": commission.get("rate"),
                    "commission_max_rate": commission.get("max_rate"),
                    "commission_max_change_rate": commission.get("max_change_rate"),
                    "min_self_delegation": validator_data.get("min_self_delegation"),
                    "self_delegation_amount": None,
                    "rank": None,
                    "voting_power": validator_data.get("tokens"),
                    "last_seen_height": reference_height,
                    "raw_json": validator_data,
                },
            }


async def run_async(validator_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        collect_one_validator(
            validator_id=item["validator_id"],
            network_id=item["network_id"],
            network_name=item["network_name"],
            operator_address=item["operator_address"],
            rest_urls=item["rest_urls"],
            rpc_urls=item["rpc_urls"],
            grpc_urls=item["grpc_urls"],
            semaphore=semaphore,
        )
        for item in validator_inputs
    ]
    return await asyncio.gather(*tasks)


def build_validator_inputs(db) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Validator, Network)
        .join(Network, Network.id == Validator.network_id)
        .where(Validator.is_enabled == 1)
        .where(Network.is_enabled == 1)
        .order_by(Network.name.asc(), Validator.id.asc())
    ).all()

    result: list[dict[str, Any]] = []

    for validator, network in rows:
        endpoints = db.execute(
            select(NetworkEndpoint)
            .where(NetworkEndpoint.network_id == network.id)
            .where(NetworkEndpoint.is_enabled == 1)
            .where(NetworkEndpoint.is_public == 1)
            .order_by(NetworkEndpoint.priority.asc(), NetworkEndpoint.id.asc())
        ).scalars().all()

        rest_urls: list[str] = []
        rpc_urls: list[str] = []
        grpc_urls: list[str] = []

        # сначала поля из networks, если уже вычислены
        if getattr(network, "rest", None):
            rest_urls.append(network.rest)
        if getattr(network, "rpc", None):
            rpc_urls.append(network.rpc)
        if getattr(network, "grpc", None):
            grpc_urls.append(network.grpc)

        # потом public endpoints
        for ep in endpoints:
            if ep.endpoint_type == "rest":
                rest_urls.append(ep.url)
            elif ep.endpoint_type == "rpc":
                rpc_urls.append(ep.url)
            elif ep.endpoint_type == "grpc":
                grpc_urls.append(ep.url)

        rest_urls = dedupe_keep_order(rest_urls)
        rpc_urls = dedupe_keep_order(rpc_urls)
        grpc_urls = dedupe_keep_order(grpc_urls)

        # validator status без REST не получить нормально
        if not rest_urls:
            continue

        result.append(
            {
                "validator_id": validator.id,
                "network_id": network.id,
                "network_name": network.name,
                "operator_address": validator.operator_address,
                "rest_urls": rest_urls,
                "rpc_urls": rpc_urls,
                "grpc_urls": grpc_urls,
            }
        )

    return result


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
        validator_inputs = build_validator_inputs(db)
        results = asyncio.run(run_async(validator_inputs))
        now = datetime.now(timezone.utc)

        processed = 0
        success = 0
        failed = 0

        for item in results:
            processed += 1

            if not item["ok"]:
                failed += 1
                print(
                    f"[FAIL] {item['network_name']} {item['operator_address']} | "
                    f"{item['error']}"
                )
                continue

            success += 1
            validator_id = item["validator_id"]
            v = item["validator"]

            validator = db.execute(
                select(Validator).where(Validator.id == validator_id)
            ).scalars().first()

            if validator is None:
                failed += 1
                continue

            raw_json_str = json.dumps(v["raw_json"], ensure_ascii=False)

            validator.moniker = v["moniker"] or validator.moniker
            validator.operator_address = v["operator_address"] or validator.operator_address
            validator.consensus_address = v["consensus_address"]
            validator.updated_at = now
            db.flush()

            current = db.execute(
                select(ValidatorStatusCurrent)
                .where(ValidatorStatusCurrent.validator_id == validator.id)
            ).scalars().first()

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

            db.add(
                ValidatorStatusHistory(
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
            )

            print(
                f"[OK] {item['network_name']} {validator.operator_address} | "
                f"status={v['status']} jailed={v['jailed']} "
                f"commission={v['commission_rate']} height={v['last_seen_height']}"
            )

        finished = datetime.now(timezone.utc)
        run.status = "success"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.items_processed = processed
        run.error_message = None

        db.commit()
        print(
            f"validator_status_collector complete: "
            f"processed={processed} success={success} failed={failed}"
        )
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
