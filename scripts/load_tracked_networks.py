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
    return (
        (s or "")
        .strip()
        .lower()
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
    )


def load_names() -> list[str]:
    raw = NAMES_FILE.read_text(encoding="utf-8").splitlines()
    result = []
    seen = set()

    for line in raw:
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        key = normalize(name)
        if key in seen:
            continue
        seen.add(key)
        result.append(name)

    return result


def is_testnet_network(net: Network) -> bool:
    chain_type = normalize(getattr(net, "chain_type", "") or "")
    chain_id = normalize(getattr(net, "chain_id", "") or "")
    directory = normalize(getattr(net, "directory", "") or "")
    name = normalize(getattr(net, "name", "") or "")
    display_name = normalize(getattr(net, "display_name", "") or "")

    return (
        "testnet" in chain_type
        or "testnet" in chain_id
        or "testnet" in directory
        or "testnet" in name
        or "testnet" in display_name
    )


def build_network_keys(net: Network) -> set[str]:
    keys: set[str] = set()

    for value in (
        getattr(net, "name", None),
        getattr(net, "display_name", None),
        getattr(net, "directory", None),
        getattr(net, "chain_id", None),
    ):
        n = normalize(value)
        if n:
            keys.add(n)

    if is_testnet_network(net):
        extra = set()
        for k in keys:
            if not k.endswith("testnet"):
                extra.add(f"{k}testnet")
        keys |= extra

    return keys


def choose_best_match(wanted: str, networks: list[Network]) -> Network | None:
    wanted_norm = normalize(wanted)
    wanted_is_testnet = wanted_norm.endswith("testnet")
    wanted_base = wanted_norm[:-7] if wanted_is_testnet else wanted_norm

    exact_matches: list[Network] = []
    base_matches: list[Network] = []

    for net in networks:
        keys = build_network_keys(net)

        if wanted_norm in keys:
            exact_matches.append(net)
            continue

        if wanted_is_testnet and wanted_base in keys:
            base_matches.append(net)

    candidates = exact_matches if exact_matches else base_matches
    if not candidates:
        return None

    if wanted_is_testnet:
        testnet_candidates = [n for n in candidates if is_testnet_network(n)]
        if testnet_candidates:
            return sorted(
                testnet_candidates,
                key=lambda n: (
                    normalize(getattr(n, "directory", "") or ""),
                    normalize(getattr(n, "chain_id", "") or ""),
                ),
            )[0]
    else:
        mainnet_candidates = [n for n in candidates if not is_testnet_network(n)]
        if mainnet_candidates:
            return sorted(
                mainnet_candidates,
                key=lambda n: (
                    normalize(getattr(n, "directory", "") or ""),
                    normalize(getattr(n, "chain_id", "") or ""),
                ),
            )[0]

    return sorted(
        candidates,
        key=lambda n: (
            0 if is_testnet_network(n) == wanted_is_testnet else 1,
            normalize(getattr(n, "directory", "") or ""),
            normalize(getattr(n, "chain_id", "") or ""),
        ),
    )[0]


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
            found = choose_best_match(wanted, networks)

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

            matched.append(
                (
                    wanted,
                    found.chain_id,
                    found.name,
                    found.display_name,
                    found.directory,
                    found.chain_type,
                )
            )

        db.commit()

        print(f"Tracked networks loaded: {len(matched)}")

        if matched:
            print("\nMatched:")
            for wanted, chain_id, name, display_name, directory, chain_type in matched:
                print(
                    f"  - {wanted} -> {display_name or name} | "
                    f"{chain_id} | dir={directory} | type={chain_type}"
                )

        if missing:
            print("\nNot matched:")
            for name in missing:
                print(f"  - {name}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
