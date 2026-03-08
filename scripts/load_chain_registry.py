#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint

CHAIN_REGISTRY_DIR = Path("./chain-registry")
GIT_URL = "https://github.com/cosmos/chain-registry.git"


def ensure_repo() -> None:
    if CHAIN_REGISTRY_DIR.exists():
        subprocess.run(
            ["git", "-C", str(CHAIN_REGISTRY_DIR), "pull", "--ff-only"],
            check=True,
        )
    else:
        subprocess.run(
            ["git", "clone", "--depth", "1", GIT_URL, str(CHAIN_REGISTRY_DIR)],
            check=True,
        )


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def first_nonempty(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def parse_endpoints(chain_json: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    apis = chain_json.get("apis") or {}

    for endpoint_type in ("rpc", "rest", "grpc"):
        items = apis.get(endpoint_type) or []
        for idx, item in enumerate(items, start=1):
            address = item.get("address")
            if not address:
                continue
            result.append(
                {
                    "endpoint_type": endpoint_type,
                    "label": f"{endpoint_type}{idx}",
                    "url": address,
                }
            )
    return result


def main() -> None:
    ensure_repo()

    db = SessionLocal()
    try:
        chain_files = sorted(CHAIN_REGISTRY_DIR.glob("*/chain.json"))

        for chain_file in chain_files:
            if chain_file.parts[-2].startswith("."):
                continue

            data = load_json(chain_file)
            if not data:
                continue

            name = first_nonempty(
                data.get("name"),
                data.get("chain_name"),
                chain_file.parent.name,
            )
            if not name:
                continue

            display_name = first_nonempty(
                data.get("pretty_name"),
                data.get("display_name"),
                name,
            )

            chain_id = data.get("chain_id")
            chain_type = "cosmos"

            fees = data.get("fees") or {}
            fee_tokens = fees.get("fee_tokens") or []
            base_denom = None
            display_denom = None
            exponent = None

            if fee_tokens:
                fee0 = fee_tokens[0]
                base_denom = fee0.get("denom")
                display_denom = first_nonempty(
                    fee0.get("display_denom"),
                    fee0.get("symbol"),
                    fee0.get("denom"),
                )
                exponent = fee0.get("fixed_min_gas_price")  # placeholder source exists sometimes
                exponent = None  # intentionally keep clean until assetlist enrichment

            network = db.execute(
                select(Network).where(Network.name == name)
            ).scalar_one_or_none()

            if network is None:
                network = Network(
                    name=name,
                    display_name=display_name,
                    chain_id=chain_id,
                    chain_type=chain_type,
                    base_denom=base_denom,
                    display_denom=display_denom,
                    exponent=exponent,
                    is_enabled=1,
                )
                db.add(network)
                db.flush()
            else:
                network.display_name = display_name
                network.chain_id = chain_id
                network.chain_type = chain_type
                network.base_denom = base_denom
                network.display_denom = display_denom
                network.is_enabled = 1
                db.flush()

            db.execute(
                delete(NetworkEndpoint).where(NetworkEndpoint.network_id == network.id)
            )

            for ep in parse_endpoints(data):
                db.add(
                    NetworkEndpoint(
                        network_id=network.id,
                        endpoint_type=ep["endpoint_type"],
                        label=ep["label"],
                        url=ep["url"],
                        priority=1,
                        is_public=1,
                        is_enabled=1,
                    )
                )

        db.commit()
        print("Chain registry import complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()
