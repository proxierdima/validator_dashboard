#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, TrackedNetwork

NAMES_FILE = Path("config/posthuman_network_names.txt")


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


def main() -> None:
    if not NAMES_FILE.exists():
        raise FileNotFoundError(f"Missing file: {NAMES_FILE}")

    names = load_names()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        networks = db.execute(
            select(Network).where(Network.is_enabled == 1)
        ).scalars().all()

        matched = []
        missing = []

        for wanted in names:
            wanted_norm = normalize(wanted)
            found = None

            for net in networks:
                if normalize(net.name or "") == wanted_norm or normalize(net.display_name or "") == wanted_norm:
                    found = net
                    break

            if not found:
                missing.append(wanted)
                continue

            row = db.execute(
                select(TrackedNetwork).where(TrackedNetwork.network_id == found.id)
            ).scalars().first()

            if row is None:
                row = TrackedNetwork(
                    network_id=found.id,
                    custom_name=wanted,
                    is_enabled=1,
                    use_for_validator_search=1,
                    use_for_validator_rpc_checks=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.custom_name = wanted
                row.is_enabled = 1
                row.use_for_validator_search = 1
                row.use_for_validator_rpc_checks = 1
                row.updated_at = now

            matched.append((wanted, found.chain_id, found.name, found.display_name))

        db.commit()

        print(f"Tracked networks loaded: {len(matched)}")

        if matched:
            print("\nMatched:")
            for wanted, chain_id, name, display_name in matched:
                print(f"  - {wanted} -> {display_name or name} | {chain_id}")

        if missing:
            print("\nNot matched:")
            for name in missing:
                print(f"  - {name}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
