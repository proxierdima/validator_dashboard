#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete, select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint, PublicRpcEndpoint, TrackedNetwork

REPO_URL = "https://github.com/cosmos/chain-registry.git"
LOCAL_DIR = Path("chain_registry")

TIMEOUT = httpx.Timeout(connect=2.5, read=4.5, write=4.5, pool=4.5)
LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=50)

RPC_PATH = "/status"
REST_PATH = "/cosmos/base/tendermint/v1beta1/node_info"


def update_registry():
    if not LOCAL_DIR.exists():
        subprocess.run(["git", "clone", REPO_URL, str(LOCAL_DIR)], check=True)
    else:
        subprocess.run(["git", "-C", str(LOCAL_DIR), "pull", "--ff-only"], check=True)


def safe_load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


async def is_working_rpc(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.get(url.rstrip("/") + RPC_PATH)
        if r.status_code != 200:
            return False
        js = r.json()
        return bool(js.get("result"))
    except Exception:
        return False


async def is_working_rest(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.get(url.rstrip("/") + REST_PATH)
        if r.status_code != 200:
            return False
        js = r.json()
        return bool(js.get("default_node_info") or js.get("application_version"))
    except Exception:
        return False


async def pick_first_working(rpcs: list[str], rests: list[str]) -> tuple[str | None, str | None]:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, limits=LIMITS) as client:
        selected_rpc = None
        selected_rest = None

        for url in rpcs:
            if await is_working_rpc(client, url):
                selected_rpc = url.rstrip("/")
                break

        for url in rests:
            if await is_working_rest(client, url):
                selected_rest = url.rstrip("/")
                break

        return selected_rpc, selected_rest


def main():
    update_registry()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        tracked_rows = db.execute(
            select(TrackedNetwork, Network)
            .join(Network, Network.id == TrackedNetwork.network_id)
            .where(TrackedNetwork.is_enabled == 1)
            .where(Network.is_enabled == 1)
            .order_by(Network.name.asc())
        ).all()

        tracked_network_ids = [network.id for _, network in tracked_rows]

        if tracked_network_ids:
            db.execute(
                delete(NetworkEndpoint)
                .where(NetworkEndpoint.network_id.in_(tracked_network_ids))
                .where(NetworkEndpoint.is_public == 1)
                .where(NetworkEndpoint.endpoint_type.in_(["rpc", "rest"]))
            )
            db.execute(
                delete(PublicRpcEndpoint)
                .where(PublicRpcEndpoint.network_id.in_(tracked_network_ids))
            )
            db.commit()

        selected_count = 0
        missing_chain_json = []
        no_working_rpc = []
        no_working_rest = []

        for _, network in tracked_rows:
            chain_json_path = LOCAL_DIR / network.name / "chain.json"
            data = safe_load_json(chain_json_path)

            if not data:
                missing_chain_json.append(network.name)
                continue

            rpc_urls = [
                x.get("address", "").rstrip("/")
                for x in (data.get("apis", {}).get("rpc") or [])
                if x.get("address")
            ]
            rest_urls = [
                x.get("address", "").rstrip("/")
                for x in (data.get("apis", {}).get("rest") or [])
                if x.get("address")
            ]

            selected_rpc, selected_rest = asyncio.run(pick_first_working(rpc_urls, rest_urls))

            if selected_rpc:
                db.add(
                    NetworkEndpoint(
                        network_id=network.id,
                        endpoint_type="rpc",
                        label="public_rpc1",
                        url=selected_rpc,
                        priority=1,
                        is_public=1,
                        is_enabled=1,
                        created_at=now,
                        updated_at=now,
                    )
                )

                db.add(
                    PublicRpcEndpoint(
                        network_id=network.id,
                        label="public_rpc1",
                        url=selected_rpc,
                        priority=1,
                        is_enabled=1,
                        source="chain-registry",
                        created_at=now,
                        updated_at=now,
                    )
                )
                selected_count += 1
            else:
                no_working_rpc.append(network.name)

            if selected_rest:
                db.add(
                    NetworkEndpoint(
                        network_id=network.id,
                        endpoint_type="rest",
                        label="public_rest1",
                        url=selected_rest,
                        priority=1,
                        is_public=1,
                        is_enabled=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                no_working_rest.append(network.name)

        db.commit()

        print(f"Tracked public endpoint refresh complete: {selected_count} working public RPC selected")

        if missing_chain_json:
            print("\nMissing chain.json:")
            for x in missing_chain_json:
                print(f"  - {x}")

        if no_working_rpc:
            print("\nNo working public RPC:")
            for x in no_working_rpc:
                print(f"  - {x}")

        if no_working_rest:
            print("\nNo working public REST:")
            for x in no_working_rest:
                print(f"  - {x}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
