cat > scripts/load_posthuman_endpoints.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint, Validator

SOURCE_FILE = Path("config/posthuman_endpoints.txt")

CHAIN_RE = re.compile(r"^\s*chain_id:\s*(.+?)\s*$")
VALOPER_RE = re.compile(r"^\s*valoper_address:\s*(.+?)\s*$")
URL_RE = re.compile(r"^\s*-\s*url:\s*(.+?)\s*$")


def clean_value(s: str) -> str:
    return s.strip().strip('"').strip("'")


def parse_source(text: str) -> list[dict]:
    items = []
    current = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        m = CHAIN_RE.match(line)
        if m:
            if current:
                items.append(current)
            current = {
                "chain_id": clean_value(m.group(1)),
                "valoper_address": None,
                "urls": [],
            }
            continue

        m = VALOPER_RE.match(line)
        if m and current:
            current["valoper_address"] = clean_value(m.group(1))
            continue

        m = URL_RE.match(line)
        if m and current:
            current["urls"].append(clean_value(m.group(1)))
            continue

    if current:
        items.append(current)

    return items


def is_ip_host(host: str | None) -> bool:
    if not host:
        return False
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host))


def classify_url(url: str) -> str:
    if url.startswith("tcp://127.0.0.1"):
        return "validator"

    try:
        p = urlparse(url)
        host = p.hostname
    except Exception:
        return "public"

    if host in {"127.0.0.1", "localhost"}:
        return "validator"

    if is_ip_host(host):
        return "validator"

    return "public"


def main() -> None:
    if not SOURCE_FILE.exists():
        raise FileNotFoundError(f"Source file not found: {SOURCE_FILE}")

    raw = SOURCE_FILE.read_text(encoding="utf-8")
    items = parse_source(raw)

    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        added_validator_eps = 0
        skipped_networks = []

        for item in items:
            chain_id = item["chain_id"]
            valoper = item["valoper_address"]
            urls = item["urls"]

            network = db.execute(
                select(Network).where(Network.chain_id == chain_id)
            ).scalar_one_or_none()

            if network is None:
                skipped_networks.append((chain_id, valoper))
                continue

            validator = db.execute(
                select(Validator)
                .where(Validator.network_id == network.id)
                .where(Validator.operator_address == valoper)
            ).scalar_one_or_none()

            if validator is None:
                validator = Validator(
                    network_id=network.id,
                    moniker="PostHuman",
                    operator_address=valoper,
                    consensus_address=None,
                    is_main=1,
                    is_enabled=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(validator)
                db.flush()
            else:
                validator.is_main = 1
                validator.is_enabled = 1
                validator.moniker = validator.moniker or "PostHuman"
                validator.updated_at = now
                db.flush()

            validator_urls = [u for u in urls if classify_url(u) == "validator"]

            for idx, url in enumerate(validator_urls, start=1):
                exists = db.execute(
                    select(NetworkEndpoint)
                    .where(NetworkEndpoint.network_id == network.id)
                    .where(NetworkEndpoint.url == url)
                ).scalar_one_or_none()

                if exists is None:
                    db.add(
                        NetworkEndpoint(
                            network_id=network.id,
                            endpoint_type="rpc",
                            label=f"validator_rpc{idx}",
                            url=url,
                            priority=idx,
                            is_public=0,
                            is_enabled=1,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    added_validator_eps += 1
                else:
                    exists.endpoint_type = "rpc"
                    exists.label = f"validator_rpc{idx}"
                    exists.priority = idx
                    exists.is_public = 0
                    exists.is_enabled = 1
                    exists.updated_at = now

        db.commit()

        print(f"Added/updated validator RPC endpoints: {added_validator_eps}")

        if skipped_networks:
            print("\nSkipped networks not found in DB by chain_id:")
            for chain_id, valoper in skipped_networks:
                print(f"  - {chain_id} | {valoper}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
PY
