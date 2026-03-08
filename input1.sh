cat > scripts/load_public_rpcs.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint, PublicRpcEndpoint

NAMES_FILE = Path("config/posthuman_network_names.txt")
TIMEOUT = 6.0
GLOBAL_CONCURRENCY = 80
PER_NETWORK_CONCURRENCY = 8
MAX_WORKING_PER_NETWORK = 3


def normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def load_names() -> list[str]:
    raw = NAMES_FILE.read_text(encoding="utf-8").splitlines()
    result = []
    seen = set()

    for line in raw:
        name = line.strip()
        if not name:
            continue
        key = normalize(name)
        if key in seen:
            continue
        seen.add(key)
        result.append(name)

    return result


async def check_rpc(client: httpx.AsyncClient, url: str) -> tuple[bool, int | None]:
    started = time.perf_counter()
    try:
        r = await client.get(url.rstrip("/") + "/status")
        latency_ms = int((time.perf_counter() - started) * 1000)

        if r.status_code != 200:
            return False, latency_ms

        js = r.json()
        if not js.get("result"):
            return False, latency_ms

        return True, latency_ms
    except Exception:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return False, latency_ms


def find_networks_exact_list(db, wanted_names: list[str]) -> tuple[list[Network], list[str]]:
    all_networks = db.execute(
        select(Network).where(Network.is_enabled == 1)
    ).scalars().all()

    by_norm = {}
    for net in all_networks:
        for candidate in (net.name, net.display_name):
            if candidate:
                by_norm.setdefault(normalize(candidate), []).append(net)

    matched: list[Network] = []
    missing: list[str] = []
    used_ids = set()

    for wanted in wanted_names:
        hits = by_norm.get(normalize(wanted), [])
        if not hits:
            missing.append(wanted)
            continue

        chosen = hits[0]
        if chosen.id not in used_ids:
            matched.append(chosen)
            used_ids.add(chosen.id)

    return matched, missing


async def pick_top_working_rpcs(urls: list[str], global_sem: asyncio.Semaphore) -> list[tuple[str, int | None]]:
    if not urls:
        return []

    working: list[tuple[str, int | None]] = []
    queue = list(dict.fromkeys(urls))  # dedup with order

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        async def worker(url: str) -> tuple[str, bool, int | None]:
            async with global_sem:
                ok, latency_ms = await check_rpc(client, url)
                return url, ok, latency_ms

        idx = 0
        running: set[asyncio.Task] = set()

        while (idx < len(queue) or running) and len(working) < MAX_WORKING_PER_NETWORK:
            while idx < len(queue) and len(running) < PER_NETWORK_CONCURRENCY:
                running.add(asyncio.create_task(worker(queue[idx])))
                idx += 1

            if not running:
                break

            done, pending = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            running = pending

            for task in done:
                url, ok, latency_ms = await task
                if ok:
                    working.append((url, latency_ms))
                    if len(working) >= MAX_WORKING_PER_NETWORK:
                        for p in pending:
                            p.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        return working[:MAX_WORKING_PER_NETWORK]

    working.sort(key=lambda x: (x[1] is None, x[1] if x[1] is not None else 10**9, x[0]))
    return working[:MAX_WORKING_PER_NETWORK]


def main() -> None:
    if not NAMES_FILE.exists():
        raise FileNotFoundError(f"Missing {NAMES_FILE}")

    wanted_names = load_names()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        matched_networks, missing_names = find_networks_exact_list(db, wanted_names)
        matched_ids = [n.id for n in matched_networks]

        print(f"Matched networks from list: {len(matched_networks)}")

        if missing_names:
            print("\nNames not matched in DB:")
            for name in missing_names:
                print(f"  - {name}")

        if not matched_networks:
            print("No matched networks. Exiting.")
            return

        source_rows = db.execute(
            select(
                NetworkEndpoint.network_id,
                NetworkEndpoint.url,
                NetworkEndpoint.priority,
            )
            .where(NetworkEndpoint.network_id.in_(matched_ids))
            .where(NetworkEndpoint.endpoint_type == "rpc")
            .where(NetworkEndpoint.is_enabled == 1)
            .where(NetworkEndpoint.is_public == 1)
            .order_by(NetworkEndpoint.network_id.asc(), NetworkEndpoint.priority.asc(), NetworkEndpoint.url.asc())
        ).all()

        urls_by_network: dict[int, list[str]] = {}
        for network in matched_networks:
            urls_by_network[network.id] = []

        for network_id, url, _priority in source_rows:
            urls_by_network.setdefault(network_id, []).append(url)

        global_sem = asyncio.Semaphore(GLOBAL_CONCURRENCY)

        async def process_all() -> dict[int, list[tuple[str, int | None]]]:
            tasks = {
                network.id: asyncio.create_task(
                    pick_top_working_rpcs(urls_by_network.get(network.id, []), global_sem)
                )
                for network in matched_networks
            }
            result: dict[int, list[tuple[str, int | None]]] = {}
            for network_id, task in tasks.items():
                result[network_id] = await task
            return result

        selected_by_network = asyncio.run(process_all())

        existing_rows = db.execute(
            select(PublicRpcEndpoint)
            .where(PublicRpcEndpoint.network_id.in_(matched_ids))
        ).scalars().all()

        existing_map: dict[tuple[int, str], PublicRpcEndpoint] = {
            (row.network_id, row.url): row for row in existing_rows
        }

        existing_by_network: dict[int, list[PublicRpcEndpoint]] = {}
        for row in existing_rows:
            existing_by_network.setdefault(row.network_id, []).append(row)

        added = 0
        updated = 0
        disabled = 0

        for network in matched_networks:
            selected = selected_by_network.get(network.id, [])
            selected_urls = {url for url, _latency in selected}

            for idx, (url, _latency) in enumerate(selected, start=1):
                key = (network.id, url)
                exists = existing_map.get(key)

                if exists is None:
                    db.add(
                        PublicRpcEndpoint(
                            network_id=network.id,
                            label=f"public_rpc{idx}",
                            url=url,
                            priority=idx,
                            is_enabled=1,
                            source="chain-registry",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    added += 1
                else:
                    exists.label = f"public_rpc{idx}"
                    exists.priority = idx
                    exists.is_enabled = 1
                    exists.updated_at = now
                    updated += 1

            for row in existing_by_network.get(network.id, []):
                if row.url not in selected_urls and row.is_enabled == 1:
                    row.is_enabled = 0
                    row.updated_at = now
                    disabled += 1

        db.commit()

        print(f"\nAdded: {added}")
        print(f"Updated: {updated}")
        print(f"Disabled old rows: {disabled}")

        print("\nSelected public RPCs:")
        for network in sorted(matched_networks, key=lambda x: (x.display_name or x.name or "").lower()):
            selected = selected_by_network.get(network.id, [])
            print(f"\n{network.display_name or network.name} | {network.chain_id}")
            if not selected:
                print("  - no working public RPC found")
                continue
            for idx, (url, latency_ms) in enumerate(selected, start=1):
                print(f"  {idx}. {url}  [{latency_ms} ms]" if latency_ms is not None else f"  {idx}. {url}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
PY
