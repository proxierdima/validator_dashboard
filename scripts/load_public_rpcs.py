#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint, PublicRpcEndpoint


def main() -> None:
    now = datetime.now(timezone.utc)
    db = SessionLocal()

    try:
        networks = db.execute(
            select(Network)
            .where(Network.is_enabled == 1)
            .order_by(Network.name.asc())
        ).scalars().all()

        added = 0
        updated = 0

        for network in networks:
            first_rpc = db.execute(
                select(NetworkEndpoint)
                .where(NetworkEndpoint.network_id == network.id)
                .where(NetworkEndpoint.endpoint_type == "rpc")
                .where(NetworkEndpoint.is_enabled == 1)
                .where(NetworkEndpoint.is_public == 1)
                .order_by(NetworkEndpoint.priority.asc(), NetworkEndpoint.id.asc())
            ).scalars().first()

            if first_rpc is None:
                continue

            row = db.execute(
                select(PublicRpcEndpoint)
                .where(PublicRpcEndpoint.network_id == network.id)
                .order_by(PublicRpcEndpoint.id.asc())
            ).scalars().first()

            if row is None:
                db.add(
                    PublicRpcEndpoint(
                        network_id=network.id,
                        label="public_rpc1",
                        url=first_rpc.url,
                        priority=1,
                        is_enabled=1,
                        source="chain-registry",
                        created_at=now,
                        updated_at=now,
                    )
                )
                added += 1
            else:
                row.label = "public_rpc1"
                row.url = first_rpc.url
                row.priority = 1
                row.is_enabled = 1
                row.source = "chain-registry"
                row.updated_at = now
                updated += 1

        db.commit()

        print(f"Public RPC rows added: {added}")
        print(f"Public RPC rows updated: {updated}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
