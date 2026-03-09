#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkAsset, TrackedNetwork

REPO_URL = "https://github.com/cosmos/chain-registry.git"
LOCAL_DIR = Path("chain-registry")


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

        network_ids = [network.id for _, network in tracked_rows]
        if network_ids:
            db.execute(delete(NetworkAsset).where(NetworkAsset.network_id.in_(network_ids)))
            db.commit()

        added = 0
        missing_assetlists = []

        for _, network in tracked_rows:
            assetlist_path = LOCAL_DIR / network.name / "assetlist.json"
            data = safe_load_json(assetlist_path)

            if not data:
                missing_assetlists.append(network.name)
                continue

            for asset in data.get("assets", []):
                base = asset.get("base")
                display = asset.get("display")
                symbol = asset.get("symbol")
                cg_id = asset.get("coingecko_id")

                exponent = 0
                for unit in asset.get("denom_units", []):
                    if display and unit.get("denom") == display:
                        exponent = int(unit.get("exponent", 0))
                        break

                if not base:
                    continue

                db.add(
                    NetworkAsset(
                        network_id=network.id,
                        base_denom=base,
                        display_denom=display or base,
                        exponent=exponent,
                        symbol=symbol,
                        coingecko_id=cg_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
                added += 1

        db.commit()

        print(f"Network assets loaded: {added}")

        if missing_assetlists:
            print("\nMissing assetlist.json for:")
            for x in missing_assetlists:
                print(f"  - {x}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
