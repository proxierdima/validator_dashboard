from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import CollectorRun, PublicRpcCheck, PublicRpcEndpoint

TIMEOUT = 8.0
CONCURRENCY = 50


async def probe_rpc(client: httpx.AsyncClient, base_url: str) -> dict:
    url = base_url.rstrip("/") + "/status"
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
            "error_message": str(e),
        }


async def probe_one(endpoint_id: int, url: str, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            result = await probe_rpc(client, url)
            result["endpoint_id"] = endpoint_id
            return result


async def run_async(endpoints: list[tuple[int, str]]) -> list[dict]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [probe_one(endpoint_id, url, semaphore) for endpoint_id, url in endpoints]
    return await asyncio.gather(*tasks)


def main() -> None:
    db = SessionLocal()
    started = datetime.now(timezone.utc)

    run = CollectorRun(
        collector_name="public_rpc_collector",
        status="running",
        started_at=started,
        items_processed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        endpoints = db.execute(
            select(PublicRpcEndpoint.id, PublicRpcEndpoint.url)
            .where(PublicRpcEndpoint.is_enabled == 1)
        ).all()

        results = asyncio.run(run_async(list(endpoints)))
        now = datetime.now(timezone.utc)

        for item in results:
            db.add(
                PublicRpcCheck(
                    endpoint_id=item["endpoint_id"],
                    status=item["status"],
                    http_status=item["http_status"],
                    latency_ms=item["latency_ms"],
                    remote_height=item["remote_height"],
                    chain_id_reported=item["chain_id_reported"],
                    error_message=item["error_message"],
                    checked_at=now,
                )
            )

        finished = datetime.now(timezone.utc)
        run.status = "success"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.items_processed = len(results)

        db.commit()
        print(f"public_rpc_collector complete: {len(results)} checks")
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
