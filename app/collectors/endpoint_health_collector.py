from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import CollectorRun, EndpointCheck, NetworkEndpoint

RPC_PATH = "/status"
REST_PATH = "/cosmos/base/tendermint/v1beta1/node_info"

TIMEOUT = httpx.Timeout(connect=2.5, read=4.0, write=4.0, pool=4.0)
LIMITS = httpx.Limits(max_connections=200, max_keepalive_connections=100)
CONCURRENCY = 120


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


async def probe_rpc(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    url = normalize_base_url(base_url) + RPC_PATH
    started = time.perf_counter()

    try:
        r = await client.get(url)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if r.status_code != 200:
            return {
                "status": "critical",
                "http_status": r.status_code,
                "latency_ms": latency_ms,
                "remote_height": None,
                "chain_id_reported": None,
                "error_message": f"HTTP {r.status_code}",
            }

        js = r.json()
        result = js.get("result") or {}
        sync_info = result.get("sync_info") or {}
        node_info = result.get("node_info") or {}

        latest_block_height = sync_info.get("latest_block_height")
        try:
            latest_block_height = int(latest_block_height) if latest_block_height is not None else None
        except Exception:
            latest_block_height = None

        return {
            "status": "ok",
            "http_status": r.status_code,
            "latency_ms": latency_ms,
            "remote_height": latest_block_height,
            "chain_id_reported": node_info.get("network"),
            "error_message": None,
        }
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "critical",
            "http_status": None,
            "latency_ms": latency_ms,
            "remote_height": None,
            "chain_id_reported": None,
            "error_message": str(e)[:1000],
        }


async def probe_rest(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    url = normalize_base_url(base_url) + REST_PATH
    started = time.perf_counter()

    try:
        r = await client.get(url)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if r.status_code != 200:
            return {
                "status": "critical",
                "http_status": r.status_code,
                "latency_ms": latency_ms,
                "remote_height": None,
                "chain_id_reported": None,
                "error_message": f"HTTP {r.status_code}",
            }

        js = r.json()
        default_info = js.get("default_node_info") or {}

        return {
            "status": "ok",
            "http_status": r.status_code,
            "latency_ms": latency_ms,
            "remote_height": None,
            "chain_id_reported": default_info.get("network"),
            "error_message": None,
        }
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "critical",
            "http_status": None,
            "latency_ms": latency_ms,
            "remote_height": None,
            "chain_id_reported": None,
            "error_message": str(e)[:1000],
        }


async def probe_one(
    client: httpx.AsyncClient,
    endpoint_id: int,
    endpoint_type: str,
    url: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        if endpoint_type == "rpc":
            result = await probe_rpc(client, url)
        elif endpoint_type == "rest":
            result = await probe_rest(client, url)
        else:
            result = {
                "status": "warning",
                "http_status": None,
                "latency_ms": None,
                "remote_height": None,
                "chain_id_reported": None,
                "error_message": f"Unsupported endpoint_type={endpoint_type}",
            }

        result["endpoint_id"] = endpoint_id
        return result


async def run_async(endpoints: list[tuple[int, str, str]]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        limits=LIMITS,
        headers={"User-Agent": "validator-dashboard/1.0"},
    ) as client:
        tasks = [
            probe_one(client, endpoint_id, endpoint_type, url, semaphore)
            for endpoint_id, endpoint_type, url in endpoints
        ]
        return await asyncio.gather(*tasks)


def main() -> None:
    db = SessionLocal()
    started = datetime.now(timezone.utc)

    run = CollectorRun(
        collector_name="endpoint_health_collector",
        status="running",
        started_at=started,
        items_processed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        endpoints = db.execute(
            select(NetworkEndpoint.id, NetworkEndpoint.endpoint_type, NetworkEndpoint.url)
            .where(NetworkEndpoint.is_enabled == 1)
            .where(NetworkEndpoint.endpoint_type.in_(["rpc", "rest"]))
        ).all()

        results = asyncio.run(run_async(list(endpoints)))
        now = datetime.now(timezone.utc)

        rows = [
            EndpointCheck(
                endpoint_id=item["endpoint_id"],
                status=item["status"],
                http_status=item["http_status"],
                latency_ms=item["latency_ms"],
                remote_height=item["remote_height"],
                chain_id_reported=item["chain_id_reported"],
                error_message=item["error_message"],
                checked_at=now,
            )
            for item in results
        ]

        db.add_all(rows)

        finished = datetime.now(timezone.utc)
        run.status = "success"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.items_processed = len(results)

        db.commit()
        print(f"Endpoint collector complete: {len(results)} checks")
    except Exception as e:
        db.rollback()
        finished = datetime.now(timezone.utc)
        run.status = "failed"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.error_message = str(e)[:2000]
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
